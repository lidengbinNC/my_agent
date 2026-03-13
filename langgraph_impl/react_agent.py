"""LangGraph 版 ReAct Agent — 与自研 react_engine.py 的对比实现。

面试考点 & 对比分析:
  ┌─────────────────────┬──────────────────────────────┬──────────────────────────────┐
  │ 维度                │ 自研 react_engine.py          │ LangGraph react_agent.py     │
  ├─────────────────────┼──────────────────────────────┼──────────────────────────────┤
  │ 代码量              │ ~260 行                       │ ~120 行（本文件）             │
  │ 工具绑定            │ 手动拼接 OpenAI tool 格式     │ llm.bind_tools() 一行搞定    │
  │ 状态管理            │ 手动维护 messages list        │ TypedDict State 自动追加     │
  │ 循环控制            │ while loop + max_iterations  │ 条件边 should_continue()     │
  │ 工具执行            │ 自研 ToolExecutor             │ ToolNode 自动分派            │
  │ 流式输出            │ AsyncGenerator yield step    │ graph.astream() 事件流       │
  │ 可调试性            │ 完全透明，可断点              │ 需要 LangSmith 可视化        │
  │ 定制灵活性          │ 任意修改循环逻辑              │ 受限于 StateGraph 范式       │
  │ Checkpoint          │ 需自行实现                    │ 内置 MemorySaver             │
  └─────────────────────┴──────────────────────────────┴──────────────────────────────┘

LangGraph 核心概念:
  - StateGraph: 以状态为核心的有向图，节点修改状态，边决定流向
  - TypedDict State: 定义图的状态结构，Annotated[list, add_messages] 自动追加消息
  - ToolNode: 内置工具执行节点，自动解析 AIMessage 中的 tool_calls 并执行
  - 条件边 (Conditional Edge): 根据当前状态动态决定下一个节点
  - should_continue: 判断是继续调用工具还是结束
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, AsyncGenerator

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool as lc_tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from my_agent.config.settings import settings
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# ── 1. 定义图状态 ─────────────────────────────────────────────────
# Annotated[list, add_messages]: LangGraph 内置 reducer，自动将新消息追加到列表
# 面试考点：Reducer 函数决定如何合并新旧状态值
#
# 改造说明：
#   原设计只有 messages 字段，存在两个问题：
#   1. max_iterations 参数传入 build_react_graph 但从未被使用，无法防止无限循环
#   2. 缺少 error 字段，节点异常时无法在 State 中传递错误信息
#   新增 iteration_count（配合条件边实现迭代上限）和 error 字段解决上述问题

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    iteration_count: int   # 当前已执行的工具调用轮次，配合 max_iterations 防止无限循环
    error: str             # 节点异常信息，非空时条件边直接路由到 END


# ── 2. 将自研工具系统桥接为 LangChain Tool ────────────────────────
# 面试考点：适配器模式 — 将内部 BaseTool 适配为 LangChain @tool 格式

def _build_lc_tools() -> list:
    """将自研 ToolRegistry 中的工具转换为 LangChain Tool 列表。"""
    import my_agent.domain.tool.builtin  # noqa: F401 触发工具注册
    from my_agent.domain.tool.registry import get_registry

    registry = get_registry()
    lc_tools = []

    for internal_tool in registry.all():
        # 闭包捕获 internal_tool，避免循环变量问题
        def _make_tool(t):
            @lc_tool(t.name, description=t.description)
            async def _wrapped(**kwargs: Any) -> str:
                result = await t._execute(**kwargs)
                return result.to_observation()
            return _wrapped

        lc_tools.append(_make_tool(internal_tool))

    return lc_tools


# ── 3. 构建 ReAct Graph ───────────────────────────────────────────

def build_react_graph(
    model: str | None = None,
    temperature: float = 0.0,
    max_iterations: int = 10,
) -> StateGraph:
    """构建 LangGraph ReAct Agent 图。

    图结构:
      START → agent_node → (should_continue?) → tool_node → agent_node → ...
                                              ↘ END

    面试考点:
      - add_node: 注册节点（函数或 Runnable）
      - add_conditional_edges: 根据返回值动态路由
      - compile(): 将图编译为可执行的 Runnable
    """
    cfg = settings.default_llm
    llm = ChatOpenAI(
        model=model or cfg.model,
        temperature=temperature,
        openai_api_key=cfg.api_key,
        openai_api_base=cfg.base_url,
        max_retries=2,
    )

    lc_tools = _build_lc_tools()
    llm_with_tools = llm.bind_tools(lc_tools)  # 绑定工具，LLM 输出会包含 tool_calls

    # ── 节点函数 ──────────────────────────────────────────────────
    async def agent_node(state: AgentState) -> dict:
        """LLM 推理节点：接收当前消息历史，输出 AIMessage（可能含 tool_calls）。"""
        try:
            response = await llm_with_tools.ainvoke(state["messages"])
            return {"messages": [response], "error": ""}
        except Exception as e:
            logger.error("lg_agent_node_failed", error=str(e))
            return {"error": f"LLM 调用失败: {e}"}

    # ToolNode: 自动解析 AIMessage.tool_calls，并行执行所有工具调用
    tool_node = ToolNode(lc_tools)

    # ── 条件边函数 ────────────────────────────────────────────────
    def should_continue(state: AgentState) -> str:
        """判断是否继续调用工具。

        面试考点：条件边是 LangGraph 实现 ReAct 循环的核心机制
          - error 非空 → 直接结束，避免带错误状态继续循环
          - iteration_count 达到上限 → 结束，防止无限循环（原设计的 bug）
          - 最后一条消息是 AIMessage 且有 tool_calls → 继续执行工具
          - 最后一条消息是 AIMessage 且无 tool_calls → 结束（Final Answer）
        """
        if state.get("error"):
            return END

        if state.get("iteration_count", 0) >= max_iterations:
            logger.warning("lg_max_iterations_reached", count=max_iterations)
            return END

        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and last_msg.tool_calls:
            return "tools"
        return END

    async def tool_node_with_counter(state: AgentState) -> dict:
        """包装 ToolNode，每次工具调用后递增 iteration_count。"""
        result = await tool_node.ainvoke(state)
        return {**result, "iteration_count": state.get("iteration_count", 0) + 1}

    # ── 构建图 ────────────────────────────────────────────────────
    graph = StateGraph(AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node_with_counter)

    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", END: END},
    )
    graph.add_edge("tools", "agent")  # 工具执行完毕，回到 agent 继续推理

    return graph


def compile_react_graph(checkpointer: Any = None, **kwargs) -> Any:
    """编译 ReAct Graph，返回可执行的 CompiledGraph。

    Args:
        checkpointer: 可选，传入 SqliteSaver/MemorySaver 实例以启用 Checkpoint 持久化。
                      传入后同一 thread_id 的多次调用共享对话历史。
                      不传则无状态（每次调用独立）。
    """
    graph = build_react_graph(**kwargs)
    return graph.compile(checkpointer=checkpointer)


# ── 4. 便捷运行接口 ───────────────────────────────────────────────

async def run_react_agent(
    question: str,
    system_prompt: str = "你是一个智能助手，可以使用工具来回答问题。请用中文回答。",
    **kwargs,
) -> str:
    """运行 LangGraph ReAct Agent，返回最终答案。

    对比自研接口:
      自研: async for step in react_engine.run(question): ...
      LangGraph: result = await run_react_agent(question)
    """
    app = compile_react_graph(**kwargs)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ]
    result = await app.ainvoke({"messages": messages, "iteration_count": 0, "error": ""})
    if result.get("error"):
        return f"[错误] {result['error']}"
    last = result["messages"][-1]
    return last.content if hasattr(last, "content") else str(last)


async def stream_react_agent(
    question: str,
    system_prompt: str = "你是一个智能助手，可以使用工具来回答问题。请用中文回答。",
    **kwargs,
) -> AsyncGenerator[dict, None]:
    """流式运行 LangGraph ReAct Agent，逐步 yield 事件。

    面试考点：LangGraph astream() 返回每个节点的输出事件
      事件格式: {"node_name": {"messages": [...]}}
    """
    app = compile_react_graph(**kwargs)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ]
    async for event in app.astream({"messages": messages, "iteration_count": 0, "error": ""}):
        for node_name, node_output in event.items():
            msgs = node_output.get("messages", [])
            for msg in msgs:
                yield {
                    "node": node_name,
                    "type": type(msg).__name__,
                    "content": getattr(msg, "content", ""),
                    "tool_calls": getattr(msg, "tool_calls", []),
                }


# ── 5. 代码量对比统计 ─────────────────────────────────────────────

def code_comparison() -> dict[str, Any]:
    """返回自研 vs LangGraph 的代码量对比数据。"""
    import os

    self_impl_path = os.path.join(
        os.path.dirname(__file__), "..", "my_agent", "core", "engine", "react_engine.py"
    )
    lg_impl_path = __file__

    def count_lines(path: str) -> int:
        try:
            with open(path, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip() and not line.strip().startswith("#"))
        except Exception:
            return -1

    return {
        "self_impl": {
            "file": "my_agent/core/engine/react_engine.py",
            "non_blank_non_comment_lines": count_lines(self_impl_path),
        },
        "langgraph_impl": {
            "file": "langgraph_impl/react_agent.py",
            "non_blank_non_comment_lines": count_lines(lg_impl_path),
        },
        "comparison": {
            "代码量": "自研 ~260 行 vs LangGraph ~120 行（含注释和对比说明）",
            "工具绑定": "自研手动拼接 OpenAI tool 格式 vs LangGraph llm.bind_tools() 一行",
            "状态管理": "自研手动维护 messages list vs LangGraph TypedDict + add_messages reducer",
            "循环控制": "自研 while loop + max_iterations vs LangGraph 条件边 should_continue()",
            "工具执行": "自研 ToolExecutor（超时/安全检查）vs LangGraph ToolNode（自动分派）",
            "可调试性": "自研完全透明可断点 vs LangGraph 需要 LangSmith 可视化",
            "定制灵活性": "自研任意修改循环逻辑 vs LangGraph 受限于 StateGraph 范式",
            "Checkpoint": "自研需自行实现 vs LangGraph 内置 MemorySaver/SqliteSaver",
        },
    }


if __name__ == "__main__":
    async def _demo():
        print("=== LangGraph ReAct Agent Demo ===")
        answer = await run_react_agent("计算 (123 + 456) * 789 - 321 的结果")
        print(f"Answer: {answer}")
        print("\n=== 代码量对比 ===")
        import json
        print(json.dumps(code_comparison(), ensure_ascii=False, indent=2))

    asyncio.run(_demo())

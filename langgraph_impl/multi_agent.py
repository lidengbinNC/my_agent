"""LangGraph 版多 Agent 协作 — Subgraph 嵌套实现。

面试考点:
  LangGraph 多 Agent 三种模式:
    1. 顺序协作 (Sequential): A → B → C，通过共享 State 传递上下文
    2. 并行协作 (Parallel): 多个 Agent 并行执行，结果汇总（Send API）
    3. 层级协作 (Hierarchical): Supervisor 分配任务给 Worker Agent

  LangGraph 新概念:
    - Subgraph: 将已编译的 Graph 嵌入另一个 Graph 作为节点
    - Send API: 动态创建并行任务，实现 Map-Reduce 模式
    - Supervisor: 用 LLM 决定下一个执行的 Agent（路由器）
    - 共享 State: 父子图通过 State 字段通信

  与自研对比:
    ┌──────────────────┬──────────────────────────────┬────────────────────────────────┐
    │ 维度             │ 自研 multi_agent/             │ LangGraph multi_agent.py       │
    ├──────────────────┼──────────────────────────────┼────────────────────────────────┤
    │ 顺序协作         │ SequentialCoordinator 类      │ 链式 add_edge 连接子图         │
    │ 并行协作         │ asyncio.gather + 手动汇总     │ Send API + ToolNode 并行       │
    │ 层级协作         │ HierarchicalCoordinator 类    │ Supervisor + 条件边路由        │
    │ Agent 间通信     │ AgentMessage 协议             │ 共享 State TypedDict           │
    │ 代码量           │ ~400 行（3个协调器类）        │ ~200 行（本文件含注释）         │
    └──────────────────┴──────────────────────────────┴────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
import operator
from typing import Annotated, Any, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from my_agent.config.settings import settings
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


def _make_llm() -> ChatOpenAI:
    cfg = settings.default_llm
    return ChatOpenAI(
        model=cfg.model,
        temperature=0.1,
        openai_api_key=cfg.api_key,
        openai_api_base=cfg.base_url,
    )


# ═══════════════════════════════════════════════════════════════════
# 模式一：顺序协作 (Sequential Multi-Agent)
# 场景：研究报告生成 — 搜索 → 分析 → 撰写
# ═══════════════════════════════════════════════════════════════════

class SequentialState(TypedDict):
    """顺序协作图的共享状态。"""
    topic: str
    search_result: str       # 搜索 Agent 的输出
    analysis_result: str     # 分析 Agent 的输出
    final_report: str        # 撰写 Agent 的输出


async def search_agent_node(state: SequentialState) -> dict:
    """搜索 Agent：收集相关信息。"""
    llm = _make_llm()
    response = await llm.ainvoke([
        SystemMessage(content="你是一个信息搜索专家，擅长收集和整理相关资料。"),
        HumanMessage(content=f"请收集关于「{state['topic']}」的关键信息，包括定义、特点、应用场景。"),
    ])
    logger.info("lg_search_agent_done", topic=state["topic"][:30])
    return {"search_result": response.content}


async def analysis_agent_node(state: SequentialState) -> dict:
    """分析 Agent：深度分析搜索结果。"""
    llm = _make_llm()
    response = await llm.ainvoke([
        SystemMessage(content="你是一个深度分析专家，擅长从信息中提取洞察。"),
        HumanMessage(content=(
            f"主题：{state['topic']}\n\n"
            f"收集的信息：\n{state['search_result']}\n\n"
            "请进行深度分析，找出关键洞察、优缺点和发展趋势。"
        )),
    ])
    logger.info("lg_analysis_agent_done")
    return {"analysis_result": response.content}


async def writer_agent_node(state: SequentialState) -> dict:
    """撰写 Agent：生成最终报告。"""
    llm = _make_llm()
    response = await llm.ainvoke([
        SystemMessage(content="你是一个专业报告撰写专家，擅长将分析结果整合为清晰的报告。"),
        HumanMessage(content=(
            f"主题：{state['topic']}\n\n"
            f"基础信息：\n{state['search_result'][:500]}\n\n"
            f"深度分析：\n{state['analysis_result'][:500]}\n\n"
            "请撰写一份结构清晰的研究报告（500字以内）。"
        )),
    ])
    logger.info("lg_writer_agent_done")
    return {"final_report": response.content}


def build_sequential_graph() -> StateGraph:
    """构建顺序协作图：搜索 → 分析 → 撰写。

    面试考点：顺序协作通过 add_edge 链式连接，前一个 Agent 的输出
    自动写入 State，后一个 Agent 从 State 读取。
    """
    graph = StateGraph(SequentialState)
    graph.add_node("search_agent", search_agent_node)
    graph.add_node("analysis_agent", analysis_agent_node)
    graph.add_node("writer_agent", writer_agent_node)

    graph.add_edge(START, "search_agent")
    graph.add_edge("search_agent", "analysis_agent")
    graph.add_edge("analysis_agent", "writer_agent")
    graph.add_edge("writer_agent", END)

    return graph


# ═══════════════════════════════════════════════════════════════════
# 模式二：层级协作 (Hierarchical / Supervisor Pattern)
# 场景：Supervisor 根据任务动态分配给不同 Worker Agent
# ═══════════════════════════════════════════════════════════════════

WORKERS = ["researcher", "coder", "analyst"]

class SupervisorState(TypedDict):
    """Supervisor 协作图的共享状态。"""
    messages: Annotated[list[BaseMessage], add_messages]
    next_worker: str    # Supervisor 决定的下一个 Worker


class SupervisorDecision(BaseModel):
    """Supervisor 的路由决策。"""
    next: str = Field(
        description=f"下一个执行的 Worker，可选: {WORKERS} 或 'FINISH'",
    )
    reason: str = Field(description="选择该 Worker 的原因")


async def supervisor_node(state: SupervisorState) -> dict:
    """Supervisor 节点：分析当前状态，决定下一个执行的 Worker。

    面试考点：Supervisor 是层级协作的核心，用 LLM 做动态路由决策
    """
    llm = _make_llm()
    supervisor_llm = llm.with_structured_output(SupervisorDecision)

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            f"你是一个任务协调员，管理以下 Worker：\n"
            f"- researcher: 负责信息搜索和资料收集\n"
            f"- coder: 负责代码编写和技术实现\n"
            f"- analyst: 负责数据分析和结论总结\n\n"
            f"根据对话历史，决定下一个应该执行的 Worker，或者输出 'FINISH' 表示任务完成。\n"
            f"注意：每个 Worker 只调用一次，避免重复。"
        )),
        ("human", "当前对话历史：\n{history}\n\n请决定下一步："),
    ])

    history = "\n".join(
        f"{type(m).__name__}: {getattr(m, 'content', '')[:200]}"
        for m in state["messages"][-5:]
    )

    try:
        decision: SupervisorDecision = await (prompt | supervisor_llm).ainvoke(
            {"history": history}
        )
        logger.info("lg_supervisor_decided", next=decision.next, reason=decision.reason[:50])
        return {"next_worker": decision.next}
    except Exception as e:
        logger.warning("lg_supervisor_failed", error=str(e))
        return {"next_worker": "FINISH"}


def _make_worker_node(role: str, system_prompt: str):
    """工厂函数：创建 Worker 节点。"""
    async def worker_node(state: SupervisorState) -> dict:
        llm = _make_llm()
        last_human = next(
            (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
            "请完成你的任务",
        )
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=last_human),
        ])
        logger.info(f"lg_{role}_done")
        return {"messages": [AIMessage(content=f"[{role}] {response.content}")]}
    worker_node.__name__ = f"{role}_node"
    return worker_node


def route_supervisor(state: SupervisorState) -> str:
    """Supervisor 路由函数：根据 next_worker 决定下一个节点。"""
    next_w = state.get("next_worker", "FINISH")
    if next_w == "FINISH" or next_w not in WORKERS:
        return END
    return next_w


def build_supervisor_graph() -> StateGraph:
    """构建 Supervisor 层级协作图。

    图结构:
      START → supervisor → (route?) → researcher/coder/analyst → supervisor → ...
                                    ↘ END

    面试考点：
      - Supervisor 作为中央路由器，每次执行后回到 Supervisor 重新决策
      - 条件边实现动态 Worker 选择
      - 与自研 HierarchicalCoordinator 对比：LangGraph 更简洁，但定制性较低
    """
    graph = StateGraph(SupervisorState)

    graph.add_node("supervisor", supervisor_node)
    graph.add_node("researcher", _make_worker_node(
        "researcher",
        "你是一个研究专家，负责收集信息和资料。请提供详细的研究结果。",
    ))
    graph.add_node("coder", _make_worker_node(
        "coder",
        "你是一个编程专家，负责代码实现和技术方案。请提供可运行的代码示例。",
    ))
    graph.add_node("analyst", _make_worker_node(
        "analyst",
        "你是一个分析专家，负责数据分析和结论总结。请提供深度分析和建议。",
    ))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {w: w for w in WORKERS} | {END: END},
    )
    for worker in WORKERS:
        graph.add_edge(worker, "supervisor")

    return graph


# ═══════════════════════════════════════════════════════════════════
# 便捷运行接口
# ═══════════════════════════════════════════════════════════════════

async def run_sequential_agents(topic: str) -> dict[str, Any]:
    """运行顺序协作（研究报告场景）。"""
    app = build_sequential_graph().compile()
    result = await app.ainvoke({
        "topic": topic,
        "search_result": "",
        "analysis_result": "",
        "final_report": "",
    })
    return {
        "topic": topic,
        "search_result": result.get("search_result", "")[:300],
        "analysis_result": result.get("analysis_result", "")[:300],
        "final_report": result.get("final_report", ""),
    }


async def run_supervisor_agents(question: str) -> dict[str, Any]:
    """运行 Supervisor 层级协作。"""
    app = build_supervisor_graph().compile()
    result = await app.ainvoke({
        "messages": [HumanMessage(content=question)],
        "next_worker": "",
    })
    messages = result.get("messages", [])
    worker_outputs = [
        {"role": m.content.split("]")[0].lstrip("["), "content": m.content}
        for m in messages
        if isinstance(m, AIMessage)
    ]
    return {
        "question": question,
        "worker_outputs": worker_outputs,
        "total_workers_called": len(worker_outputs),
    }


if __name__ == "__main__":
    async def _demo():
        print("=== LangGraph 顺序协作 Demo ===")
        result = await run_sequential_agents("LangGraph 框架")
        print(f"报告摘要: {result['final_report'][:200]}")

        print("\n=== LangGraph Supervisor 协作 Demo ===")
        result2 = await run_supervisor_agents(
            "请帮我分析 Python asyncio 的使用场景，并给出代码示例"
        )
        print(f"调用了 {result2['total_workers_called']} 个 Worker")

    asyncio.run(_demo())

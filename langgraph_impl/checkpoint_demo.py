"""LangGraph Checkpoint — 状态持久化 + Human-in-the-Loop。

面试考点:
  LangGraph Checkpoint 机制:
    - MemorySaver: 内存 Checkpoint，进程重启后丢失（开发调试用）
    - AsyncSqliteSaver: SQLite 异步持久化，支持跨进程恢复（生产可用）
    - thread_id: 会话标识，同一 thread_id 的多次调用共享状态
    - checkpoint_id: 每次图执行后的状态快照 ID，支持回溯

  Human-in-the-Loop 实现方式:
    - interrupt_before: 在指定节点执行前暂停，等待人工确认
    - interrupt_after: 在指定节点执行后暂停，等待人工审核
    - graph.update_state(): 人工修改状态后继续执行
    - 恢复执行: graph.invoke(None, config) 从断点继续

  SQLite 表结构（可直接用 DB Browser 查看 lg_checkpoints.db）:
    - checkpoints      — 每次节点执行后的完整 State 快照（JSON）
    - checkpoint_blobs — 大型字段分块存储
    - checkpoint_writes — interrupt_before 暂停时的待写入记录

  与自研对比:
    ┌──────────────────┬──────────────────────────────┬────────────────────────────────┐
    │ 维度             │ 自研 workflow/engine.py       │ LangGraph checkpoint_demo.py   │
    ├──────────────────┼──────────────────────────────┼────────────────────────────────┤
    │ 状态持久化       │ JSON 序列化到内存字典         │ AsyncSqliteSaver 自动写 SQLite │
    │ Human-in-Loop    │ asyncio.Event 暂停/恢复       │ interrupt_before 声明式暂停    │
    │ 状态回溯         │ 需自行实现版本管理            │ checkpoint_id 内置时间旅行     │
    │ 多会话隔离       │ workflow_run_id 手动管理      │ thread_id 自动隔离             │
    │ 代码复杂度       │ 较高（需手动实现所有机制）    │ 较低（框架内置）               │
    └──────────────────┴──────────────────────────────┴────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
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


# ── 1. 带 Checkpoint 的对话 Agent ─────────────────────────────────

class ConversationState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


async def chat_node(state: ConversationState) -> dict:
    """对话节点。"""
    llm = _make_llm()
    response = await llm.ainvoke([
        SystemMessage(content="你是一个智能助手，记住对话历史，提供连贯的回答。"),
        *state["messages"],
    ])
    return {"messages": [response]}


def build_conversation_graph_with_checkpoint(checkpointer: Any = None) -> Any:
    """构建带 Checkpoint 的对话图。

    面试考点:
      - checkpointer 参数支持外部注入（依赖注入模式），便于单例复用
      - 若不传 checkpointer，则使用 MemorySaver（仅用于本地调试）
      - compile 时注入 checkpointer，每次节点执行后自动保存完整 State 快照
      - 同一 thread_id 的多次调用自动加载历史状态，实现跨请求对话记忆

    Args:
        checkpointer: 外部传入的 checkpointer 实例（AsyncSqliteSaver 或 MemorySaver）。
                      生产环境由 checkpoint_store.init_checkpointer() 创建并注入。
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
        logger.warning("lg_using_memory_saver", reason="no checkpointer injected, state will not persist across restarts")

    graph = StateGraph(ConversationState)
    graph.add_node("chat", chat_node)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)

    return graph.compile(checkpointer=checkpointer)


# ── 2. Human-in-the-Loop (interrupt_before) ──────────────────────

class ReviewState(TypedDict):
    """Human-in-the-Loop 审核图的状态。

    字段说明：
      - messages: 对话历史（add_messages 自动追加，不覆盖）
      - draft: LLM 生成的内容草稿
      - approved: 最终发布审批结果（publish_node 写入 True）
      - needs_revision: 是否需要人工修订（draft_node 写 True，revise_node 写 False）
                        与 approved 语义分离：approved 表示最终结果，needs_revision 控制路由
      - human_feedback: 人工填写的修订意见（通过 update_state 注入）
    """
    messages: Annotated[list[BaseMessage], add_messages]
    draft: str
    approved: bool
    needs_revision: bool
    human_feedback: str


async def draft_node(state: ReviewState) -> dict:
    """起草节点：根据用户请求生成内容草稿，并标记需要人工审核。"""
    llm = _make_llm()
    last_human = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "请生成内容",
    )
    response = await llm.ainvoke([
        SystemMessage(content="你是一个内容创作者，请生成高质量的内容草稿。"),
        HumanMessage(content=last_human),
    ])
    logger.info("lg_draft_created")
    # needs_revision=True → route_after_review 路由到 revise → 触发 interrupt_before 暂停
    return {"draft": response.content, "needs_revision": True}


async def revise_node(state: ReviewState) -> dict:
    """修订节点：根据人工反馈修改草稿。

    面试考点：此节点配置了 interrupt_before，图在进入此节点前会暂停。
    人工通过 update_state 注入 human_feedback 后，调用 invoke(None) 继续执行。
    """
    llm = _make_llm()
    feedback = state.get("human_feedback", "")
    response = await llm.ainvoke([
        SystemMessage(content="你是一个内容编辑，根据反馈修改草稿。"),
        HumanMessage(content=f"原稿:\n{state['draft']}\n\n修改意见:\n{feedback}\n\n请修改:"),
    ])
    logger.info("lg_draft_revised")
    return {
        "draft": response.content,
        "needs_revision": False,
        "messages": [AIMessage(content=f"已根据反馈修改：{response.content[:100]}...")],
    }


async def publish_node(state: ReviewState) -> dict:
    """发布节点：最终发布内容。"""
    logger.info("lg_content_published")
    return {
        "messages": [AIMessage(content=f"内容已发布！\n\n{state['draft']}")],
        "approved": True,
    }


def route_after_review(state: ReviewState) -> str:
    """审核后路由：needs_revision=True → revise（触发暂停），False → 直接发布。"""
    if state.get("needs_revision", True):
        return "revise"
    return "publish"


def build_human_review_graph(checkpointer: Any = None) -> Any:
    """构建带 Human-in-the-Loop 的内容审核图。

    面试考点:
      - interrupt_before=["revise"]: 在 revise 节点执行前暂停
      - 暂停后通过 app.aupdate_state() 注入人工反馈
      - 调用 app.ainvoke(None, config) 从断点继续执行
      - checkpointer 外部注入，与 conversation_graph 共享同一个 SqliteSaver 实例
        → 两种图的 checkpoint 都写入同一个 SQLite 文件，便于统一查看

    Args:
        checkpointer: 外部传入的 checkpointer 实例。未传则使用 MemorySaver（仅调试用）。
    """
    if checkpointer is None:
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()
        logger.warning("lg_review_using_memory_saver", reason="no checkpointer injected")

    # 注意：节点名不能与 ReviewState 的字段名重名（LangGraph 限制）
    # ReviewState 有 draft / approved / needs_revision / human_feedback 字段
    # 因此节点名加 "_node" 后缀以避免冲突
    graph = StateGraph(ReviewState)
    graph.add_node("draft_node", draft_node)
    graph.add_node("revise_node", revise_node)
    graph.add_node("publish_node", publish_node)

    graph.add_edge(START, "draft_node")
    graph.add_conditional_edges(
        "draft_node",
        route_after_review,
        {"revise": "revise_node", "publish": "publish_node"},
    )
    graph.add_edge("revise_node", "publish_node")
    graph.add_edge("publish_node", END)

    # interrupt_before=["revise_node"]: 在 revise_node 执行前暂停，等待人工注入反馈
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["revise_node"],
    )


# ── 3. 状态时间旅行（Time Travel）────────────────────────────────

async def get_thread_history(app: Any, thread_id: str) -> list[dict]:
    """获取指定 thread_id 的所有历史 Checkpoint 快照。

    面试考点:
      - aget_state_history(): 返回该 thread 所有历史状态，按时间倒序
      - 每个 snapshot 包含: checkpoint_id、State 值、next 节点、创建时间
      - 可通过指定 checkpoint_id 回溯到任意历史状态重新执行（时间旅行）
      - 对比自研：自研需要手动实现状态版本管理，LangGraph 内置

    Returns:
        list of checkpoint snapshots，每项包含 checkpoint_id / state / next / ts
    """
    thread_config = {"configurable": {"thread_id": thread_id}}
    history = []
    async for snapshot in app.aget_state_history(thread_config):
        cfg = snapshot.config.get("configurable", {})
        # 序列化 State：messages 转为可 JSON 化的格式
        state_values = {}
        for k, v in snapshot.values.items():
            if k == "messages":
                state_values[k] = [
                    {
                        "type": type(m).__name__,
                        "content": getattr(m, "content", str(m))[:200],
                    }
                    for m in v
                ]
            else:
                state_values[k] = v
        history.append({
            "checkpoint_id": cfg.get("checkpoint_id", ""),
            "checkpoint_ns": cfg.get("checkpoint_ns", ""),
            "state": state_values,
            "next": list(snapshot.next),
            "created_at": getattr(snapshot, "created_at", None),
            "metadata": snapshot.metadata,
        })
    return history


if __name__ == "__main__":
    async def _local_demo():
        """本地调试用，直接运行此文件时执行（使用 MemorySaver，不写 SQLite）。"""
        from langgraph.checkpoint.memory import MemorySaver

        print("=== 多轮对话 Checkpoint Demo（MemorySaver）===")
        mem = MemorySaver()
        conv_app = build_conversation_graph_with_checkpoint(checkpointer=mem)
        thread_config = {"configurable": {"thread_id": "local-demo-001"}}

        for question in ["我叫小明，是 Python 开发者", "我刚才说我叫什么？"]:
            result = await conv_app.ainvoke(
                {"messages": [HumanMessage(content=question)]},
                config=thread_config,
            )
            answer = result["messages"][-1].content
            print(f"Q: {question}\nA: {answer[:100]}\n")

        print("=== Human-in-the-Loop Demo（MemorySaver）===")
        mem2 = MemorySaver()
        review_app = build_human_review_graph(checkpointer=mem2)
        t_cfg = {"configurable": {"thread_id": "local-review-001"}}

        init_state: ReviewState = {
            "messages": [HumanMessage(content="请写一篇关于 LangGraph 的技术介绍（100字）")],
            "draft": "", "approved": False, "needs_revision": False, "human_feedback": "",
        }
        result = await review_app.ainvoke(init_state, config=t_cfg)
        print(f"草稿（暂停前）: {result.get('draft', '')[:100]}")

        await review_app.aupdate_state(
            config=t_cfg,
            values={"human_feedback": "请增加 Checkpoint 特性说明", "needs_revision": True},
        )
        final = await review_app.ainvoke(None, config=t_cfg)
        print(f"修订后: {final.get('draft', '')[:100]}")

    asyncio.run(_local_demo())

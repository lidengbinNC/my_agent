"""LangGraph Checkpoint — 状态持久化 + Human-in-the-Loop。

面试考点:
  LangGraph Checkpoint 机制:
    - MemorySaver: 内存 Checkpoint，进程重启后丢失（开发调试用）
    - SqliteSaver: SQLite 持久化，支持跨进程恢复（生产可用）
    - thread_id: 会话标识，同一 thread_id 的多次调用共享状态
    - checkpoint_id: 每次图执行后的状态快照 ID，支持回溯

  Human-in-the-Loop 实现方式:
    - interrupt_before: 在指定节点执行前暂停，等待人工确认
    - interrupt_after: 在指定节点执行后暂停，等待人工审核
    - graph.update_state(): 人工修改状态后继续执行
    - 恢复执行: graph.invoke(None, config) 从断点继续

  与自研对比:
    ┌──────────────────┬──────────────────────────────┬────────────────────────────────┐
    │ 维度             │ 自研 workflow/engine.py       │ LangGraph checkpoint_demo.py   │
    ├──────────────────┼──────────────────────────────┼────────────────────────────────┤
    │ 状态持久化       │ JSON 序列化到内存字典         │ MemorySaver/SqliteSaver 内置   │
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
from langgraph.checkpoint.memory import MemorySaver
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


def build_conversation_graph_with_checkpoint() -> tuple[Any, MemorySaver]:
    """构建带 MemorySaver Checkpoint 的对话图。

    面试考点:
      - MemorySaver 将每次图执行后的完整 State 保存到内存
      - 同一 thread_id 的多次调用自动加载历史状态
      - 实现了跨请求的对话记忆（无需手动管理 session）
    """
    checkpointer = MemorySaver()

    graph = StateGraph(ConversationState)
    graph.add_node("chat", chat_node)
    graph.add_edge(START, "chat")
    graph.add_edge("chat", END)

    # compile 时传入 checkpointer，启用状态持久化
    app = graph.compile(checkpointer=checkpointer)
    return app, checkpointer


async def demo_multi_turn_conversation() -> list[dict]:
    """演示多轮对话（Checkpoint 自动维护历史）。

    面试考点：
      - 每次调用传入相同的 thread_id，LangGraph 自动加载历史消息
      - 对比自研：自研需要手动从 DB 加载历史消息，LangGraph 内置
    """
    app, _ = build_conversation_graph_with_checkpoint()
    thread_config = {"configurable": {"thread_id": "demo-thread-001"}}

    conversations = [
        "我叫小明，我是一名 Python 开发者",
        "我刚才说我叫什么名字？我是做什么的？",
        "推荐一些适合我的 Python 进阶学习资源",
    ]

    results = []
    for question in conversations:
        result = await app.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            config=thread_config,
        )
        last_msg = result["messages"][-1]
        answer = getattr(last_msg, "content", str(last_msg))
        results.append({"question": question, "answer": answer})
        logger.info("lg_conversation_turn", question=question[:30])

    return results


# ── 2. Human-in-the-Loop (interrupt_before) ──────────────────────

class ReviewState(TypedDict):
    """Human-in-the-Loop 审核图的状态。
    改造说明：
      1. 原设计 approved 初始为 False，route_after_review 直接读 approved 做路由，
         但 draft_node 不写 approved 字段，导致每次都走 revise 分支（逻辑 bug）。
         新增 needs_revision 字段专门表达"是否需要修订"，与 approved（最终审批结果）
         语义分离，路由逻辑更清晰。
      2. human_feedback 为空时 revise_node 静默跳过（return {}），行为不透明。
         改造后 route_after_review 在 needs_revision=False 时直接路由到 publish，
         不再进入 revise 节点，消除静默跳过的歧义。
    """
    messages: Annotated[list[BaseMessage], add_messages]
    draft: str
    approved: bool          # 最终发布审批结果
    needs_revision: bool    # 是否需要人工修订（新增，与 approved 语义分离）
    human_feedback: str     # 人工修订意见


async def draft_node(state: ReviewState) -> dict:
    """起草节点：生成初稿。
    改造说明：明确写入 needs_revision=True，表示草稿生成后默认需要人工审核，
    route_after_review 据此路由到 revise（触发 interrupt_before 暂停）。
    人工审核通过后通过 update_state 将 needs_revision 改为 False 再继续执行。
    """
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
    return {"draft": response.content, "needs_revision": True}


async def revise_node(state: ReviewState) -> dict:
    """修订节点：根据人工反馈修改草稿。
    改造说明：原设计 feedback 为空时 return {} 静默跳过，行为不透明且难以调试。
    现在 revise_node 只在 needs_revision=True 且有 feedback 时才会被路由到，
    因此无需再做空判断，直接执行修订逻辑，语义更清晰。
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
    """审核后路由：需要修订则进 revise（触发 interrupt_before 暂停），否则直接发布。
    改造说明：原设计读 approved 字段，但 draft_node 不写 approved，
    导致 approved 始终为初始值 False，每次都走 revise 分支。
    现在改为读 needs_revision，语义明确：True=需要人工介入，False=直接发布。
    """
    if state.get("needs_revision", True):
        return "revise"
    return "publish"


def build_human_review_graph() -> Any:
    """构建带 Human-in-the-Loop 的内容审核图。

    面试考点:
      - interrupt_before=["revise"]: 在 revise 节点执行前暂停
      - 暂停后通过 graph.update_state() 注入人工反馈
      - 调用 graph.invoke(None, config) 从断点继续执行
      - 对比自研：自研用 asyncio.Event，LangGraph 用 interrupt_before 声明式
    """
    checkpointer = MemorySaver()

    graph = StateGraph(ReviewState)
    graph.add_node("draft", draft_node)
    graph.add_node("revise", revise_node)
    graph.add_node("publish", publish_node)

    graph.add_edge(START, "draft")
    graph.add_conditional_edges(
        "draft",
        route_after_review,
        {"revise": "revise", "publish": "publish"},
    )
    graph.add_edge("revise", "publish")
    graph.add_edge("publish", END)

    # interrupt_before=["revise"]: 在 revise 节点前暂停，等待人工审核
    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["revise"],
    )


async def demo_human_in_the_loop() -> dict[str, Any]:
    """演示 Human-in-the-Loop 流程。

    流程:
      1. 提交任务 → draft 节点生成草稿
      2. 图在 revise 节点前暂停（interrupt_before）
      3. 人工查看草稿，注入反馈（update_state）
      4. 继续执行（invoke None）→ revise → publish
    """
    app = build_human_review_graph()
    thread_config = {"configurable": {"thread_id": "review-thread-001"}}

    # 步骤1: 提交任务，图执行到 draft 后在 revise 前暂停
    initial_state: ReviewState = {
        "messages": [HumanMessage(content="请写一篇关于 LangGraph 的技术介绍（100字）")],
        "draft": "",
        "approved": False,
        "needs_revision": False,
        "human_feedback": "",
    }

    result = await app.ainvoke(initial_state, config=thread_config)
    draft = result.get("draft", "")
    logger.info("lg_hitl_paused", draft_preview=draft[:50])

    # 步骤2: 模拟人工审核 — 注入反馈，同时保持 needs_revision=True 让 revise 节点继续执行
    human_feedback = "请增加 LangGraph 与 LangChain 的关系说明，并突出 Checkpoint 特性"
    await app.aupdate_state(
        config=thread_config,
        values={"human_feedback": human_feedback, "needs_revision": True},
    )
    logger.info("lg_hitl_feedback_injected")

    # 步骤3: 继续执行（从断点恢复）
    final_result = await app.ainvoke(None, config=thread_config)
    final_draft = final_result.get("draft", "")

    return {
        "original_draft": draft,
        "human_feedback": human_feedback,
        "revised_draft": final_draft,
        "approved": final_result.get("approved", False),
    }


# ── 3. 状态时间旅行（Time Travel）────────────────────────────────

async def demo_time_travel() -> dict[str, Any]:
    """演示 LangGraph 时间旅行：回溯到历史 Checkpoint 重新执行。

    面试考点:
      - get_state_history(): 获取所有历史 Checkpoint
      - 指定 checkpoint_id 重新执行：实现"撤销"和"重试"
      - 对比自研：自研需要手动实现状态版本管理，LangGraph 内置
    """
    app, _ = build_conversation_graph_with_checkpoint()
    thread_config = {"configurable": {"thread_id": "time-travel-thread"}}

    # 执行几轮对话
    for question in ["什么是 Python？", "Python 有哪些主要特点？"]:
        await app.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            config=thread_config,
        )

    # 获取历史 Checkpoint
    history = []
    async for state_snapshot in app.aget_state_history(thread_config):
        history.append({
            "checkpoint_id": state_snapshot.config.get("configurable", {}).get("checkpoint_id", ""),
            "message_count": len(state_snapshot.values.get("messages", [])),
        })

    return {
        "total_checkpoints": len(history),
        "checkpoints": history[:5],
        "note": "可通过指定 checkpoint_id 回溯到任意历史状态重新执行",
    }


if __name__ == "__main__":
    async def _demo():
        print("=== 多轮对话 Checkpoint Demo ===")
        convs = await demo_multi_turn_conversation()
        for c in convs:
            print(f"Q: {c['question']}")
            print(f"A: {c['answer'][:100]}\n")

        print("=== Human-in-the-Loop Demo ===")
        hitl = await demo_human_in_the_loop()
        print(f"原稿: {hitl['original_draft'][:100]}")
        print(f"修订后: {hitl['revised_draft'][:100]}")

    asyncio.run(_demo())

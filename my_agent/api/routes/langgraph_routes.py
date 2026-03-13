"""LangGraph API 路由 — 完整串联 Checkpoint / HitL / 多轮对话 / 时间旅行。

端点总览:
  POST /langgraph/chat                  — ReAct/Plan-Execute/Sequential/Supervisor 对话（支持 SSE 流式 + thread_id 跨请求记忆）
  POST /langgraph/conversation          — 带 Checkpoint 的多轮对话（SqliteSaver 持久化，跨请求保留历史）
  GET  /langgraph/conversation/{tid}    — 查询指定 thread 的完整 Checkpoint 历史（可在 SQLite 验证）

  POST /langgraph/hitl/start            — 提交 HitL 任务，LLM 生成草稿后在 revise 节点前暂停，返回 thread_id + 草稿
  GET  /langgraph/hitl/{thread_id}      — 查询 HitL 任务当前状态（暂停中 / 已完成）
  POST /langgraph/hitl/{thread_id}/resume — 注入人工反馈，从断点继续执行 → revise → publish

  GET  /langgraph/checkpoints/{thread_id} — 查询任意 thread 的 Checkpoint 历史（通用，conversation + hitl 都可用）
  GET  /langgraph/comparison            — 自研 vs LangGraph 代码量对比
  GET  /langgraph/graph/react           — ReAct 图结构（Mermaid 格式）

面试考点:
  - SqliteSaver 单例：checkpointer 在 lifespan 初始化，所有请求共享，跨请求状态持久化
  - thread_id 隔离：不同用户/会话使用不同 thread_id，互不干扰
  - interrupt_before 暂停：图在 revise 节点前暂停，HTTP 请求返回；下次请求从断点恢复
  - invoke(None, config)：传 None 表示"不新增输入，从上次断点继续"
  - aget_state_history()：获取所有历史快照，可在 SQLite 中验证数据
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from my_agent.utils.logger import get_logger

router = APIRouter(prefix="/langgraph", tags=["langgraph"])
logger = get_logger(__name__)


# ── Pydantic 请求模型 ──────────────────────────────────────────────

class LGChatRequest(BaseModel):
    question: str
    mode: str = Field(default="react", description="react / plan_execute / sequential / supervisor")
    stream: bool = False
    thread_id: str = Field(default="", description="会话 ID，相同 thread_id 共享对话历史（仅 react 模式有效）")


class ConversationRequest(BaseModel):
    message: str = Field(description="用户消息")
    thread_id: str = Field(description="会话 ID，同一 thread_id 跨请求保留历史")


class HitLStartRequest(BaseModel):
    content_request: str = Field(description="内容生成请求，例如：请写一篇关于 LangGraph 的技术介绍")
    thread_id: str = Field(default="", description="可选，不填则自动生成 UUID")


class HitLResumeRequest(BaseModel):
    feedback: str = Field(description="人工修订意见，注入后图从断点继续执行")
    approve_without_revision: bool = Field(default=False, description="True=直接发布不修订，False=按 feedback 修订后发布")


# ── 1. ReAct/Plan-Execute/Multi-Agent 对话 ────────────────────────

@router.post("/chat", summary="LangGraph Agent 对话（支持 thread_id 跨请求记忆）", response_model=None)
async def langgraph_chat(body: LGChatRequest) -> JSONResponse | StreamingResponse:
    """使用 LangGraph 实现运行 Agent，支持流式输出。

    面试考点：
      - react 模式支持 thread_id：传入相同 thread_id 可跨请求保留对话历史（SqliteSaver 持久化）
      - 其他模式（plan_execute/sequential/supervisor）暂不支持跨请求记忆
    """
    if body.stream:
        return StreamingResponse(
            _stream_langgraph(body.question, body.mode, body.thread_id),
            media_type="text/event-stream",
        )

    try:
        if body.mode == "react":
            from langgraph_impl.react_agent import compile_react_graph
            from langchain_core.messages import HumanMessage, SystemMessage
            from langgraph_impl.checkpoint_store import get_checkpointer

            # react 模式：若提供 thread_id，使用 SqliteSaver 持久化跨请求历史
            if body.thread_id:
                checkpointer = get_checkpointer()
                app = compile_react_graph(checkpointer=checkpointer)
                config = {"configurable": {"thread_id": body.thread_id}}
                messages = [
                    SystemMessage(content="你是一个智能助手，可以使用工具来回答问题。请用中文回答。"),
                    HumanMessage(content=body.question),
                ]
                result = await app.ainvoke(
                    {"messages": messages, "iteration_count": 0, "error": ""},
                    config=config,
                )
                last = result["messages"][-1]
                answer = getattr(last, "content", str(last))
            else:
                from langgraph_impl.react_agent import run_react_agent
                answer = await run_react_agent(body.question)

            return JSONResponse(content={"mode": "react", "answer": answer, "thread_id": body.thread_id})

        elif body.mode == "plan_execute":
            from langgraph_impl.plan_execute import run_plan_execute_agent
            result = await run_plan_execute_agent(body.question)
            return JSONResponse(content={"mode": "plan_execute", **result})

        elif body.mode == "sequential":
            from langgraph_impl.multi_agent import run_sequential_agents
            result = await run_sequential_agents(body.question)
            return JSONResponse(content={"mode": "sequential", **result})

        elif body.mode == "supervisor":
            from langgraph_impl.multi_agent import run_supervisor_agents
            result = await run_supervisor_agents(body.question)
            return JSONResponse(content={"mode": "supervisor", **result})

        else:
            return JSONResponse(
                status_code=400,
                content={"error": f"未知模式: {body.mode}，可选: react/plan_execute/sequential/supervisor"},
            )
    except Exception as e:
        logger.error("langgraph_chat_error", mode=body.mode, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── 2. 带 Checkpoint 的多轮对话 ───────────────────────────────────

@router.post("/conversation", summary="带 Checkpoint 的多轮对话（SqliteSaver 持久化）")
async def langgraph_conversation(body: ConversationRequest) -> JSONResponse:
    """多轮对话，SqliteSaver 自动持久化 State 到 lg_checkpoints.db。

    面试考点：
      - 同一 thread_id 的多次调用自动加载历史消息，无需手动管理 session
      - 每次调用后可在 SQLite 的 checkpoints 表查看新增的 State 快照
      - 对比自研：自研需要手动从 DB 加载历史消息，LangGraph 内置
    """
    try:
        from langchain_core.messages import HumanMessage
        from langgraph_impl.checkpoint_store import get_conversation_app

        app = get_conversation_app()
        config = {"configurable": {"thread_id": body.thread_id}}

        result = await app.ainvoke(
            {"messages": [HumanMessage(content=body.message)]},
            config=config,
        )
        last_msg = result["messages"][-1]
        answer = getattr(last_msg, "content", str(last_msg))

        # 获取当前 checkpoint 信息
        current_state = await app.aget_state(config)
        checkpoint_id = current_state.config.get("configurable", {}).get("checkpoint_id", "")
        message_count = len(current_state.values.get("messages", []))

        logger.info("lg_conversation_turn", thread_id=body.thread_id, checkpoint_id=checkpoint_id[:8])

        return JSONResponse(content={
            "thread_id": body.thread_id,
            "answer": answer,
            "checkpoint_id": checkpoint_id,
            "message_count": message_count,
            "tip": f"可通过 GET /api/v1/langgraph/checkpoints/{body.thread_id} 查看所有历史快照",
        })
    except Exception as e:
        logger.error("lg_conversation_error", thread_id=body.thread_id, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/conversation/{thread_id}", summary="查询对话 thread 的 Checkpoint 历史")
async def get_conversation_checkpoints(thread_id: str) -> JSONResponse:
    """查询指定对话 thread 的所有 Checkpoint 快照。

    返回数据可与 SQLite 中 checkpoints 表的记录对照验证。
    """
    try:
        from langgraph_impl.checkpoint_store import get_conversation_app
        from langgraph_impl.checkpoint_demo import get_thread_history

        app = get_conversation_app()
        history = await get_thread_history(app, thread_id)
        return JSONResponse(content={
            "thread_id": thread_id,
            "total_checkpoints": len(history),
            "checkpoints": history,
        })
    except Exception as e:
        logger.error("lg_get_conv_checkpoints_error", thread_id=thread_id, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── 3. Human-in-the-Loop ─────────────────────────────────────────

@router.post("/hitl/start", summary="提交 HitL 任务（LLM 生成草稿后暂停等待人工审核）")
async def hitl_start(body: HitLStartRequest) -> JSONResponse:
    """提交内容生成任务，图执行到 draft 节点后在 revise 节点前暂停（interrupt_before）。

    面试考点：
      - interrupt_before=["revise"] 让图在 revise 节点前暂停
      - 此时 HTTP 请求返回，草稿内容和 thread_id 返回给调用方
      - SqliteSaver 将暂停时的 State 写入 checkpoint_writes 表
      - 调用方保存 thread_id，后续通过 /hitl/{thread_id}/resume 继续

    Returns:
        thread_id: 用于后续 resume 的会话标识
        draft: LLM 生成的初稿内容
        status: "waiting_for_review" 表示已暂停等待人工审核
        checkpoint_id: 当前 Checkpoint ID，可在 SQLite 中查验
    """
    import uuid
    try:
        from langchain_core.messages import HumanMessage
        from langgraph_impl.checkpoint_store import get_review_app
        from langgraph_impl.checkpoint_demo import ReviewState

        thread_id = body.thread_id or str(uuid.uuid4())
        app = get_review_app()
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: ReviewState = {
            "messages": [HumanMessage(content=body.content_request)],
            "draft": "",
            "approved": False,
            "needs_revision": False,
            "human_feedback": "",
        }

        # 执行到 draft 节点完成，在 revise 节点前暂停
        result = await app.ainvoke(initial_state, config=config)
        draft = result.get("draft", "")

        # 获取暂停时的 checkpoint 信息
        current_state = await app.aget_state(config)
        checkpoint_id = current_state.config.get("configurable", {}).get("checkpoint_id", "")
        next_nodes = list(current_state.next)

        logger.info("lg_hitl_paused", thread_id=thread_id, next_nodes=next_nodes)

        return JSONResponse(content={
            "thread_id": thread_id,
            "status": "waiting_for_review",
            "draft": draft,
            "next_nodes": next_nodes,
            "checkpoint_id": checkpoint_id,
            "tip": f"审核后调用 POST /api/v1/langgraph/hitl/{thread_id}/resume 继续执行",
        })
    except Exception as e:
        logger.error("lg_hitl_start_error", error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/hitl/{thread_id}", summary="查询 HitL 任务当前状态")
async def hitl_status(thread_id: str) -> JSONResponse:
    """查询 HitL 任务当前状态：暂停中 / 已完成。

    面试考点：
      - aget_state() 获取最新 Checkpoint 的 State
      - next 字段：非空表示图还在运行中（暂停在某节点前），空列表表示图已结束
      - 可通过此接口轮询任务状态（对比自研：自研用 asyncio.Event + 状态字段）
    """
    try:
        from langgraph_impl.checkpoint_store import get_review_app

        app = get_review_app()
        config = {"configurable": {"thread_id": thread_id}}
        state = await app.aget_state(config)

        if not state or not state.values:
            return JSONResponse(
                status_code=404,
                content={"error": f"thread_id={thread_id} 不存在，请先调用 /hitl/start"},
            )

        values = state.values
        next_nodes = list(state.next)
        checkpoint_id = state.config.get("configurable", {}).get("checkpoint_id", "")

        status = "waiting_for_review" if next_nodes else "completed"

        return JSONResponse(content={
            "thread_id": thread_id,
            "status": status,
            "next_nodes": next_nodes,
            "checkpoint_id": checkpoint_id,
            "draft": values.get("draft", ""),
            "approved": values.get("approved", False),
            "needs_revision": values.get("needs_revision", False),
            "human_feedback": values.get("human_feedback", ""),
        })
    except Exception as e:
        logger.error("lg_hitl_status_error", thread_id=thread_id, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/hitl/{thread_id}/resume", summary="注入人工反馈，从断点继续执行")
async def hitl_resume(thread_id: str, body: HitLResumeRequest) -> JSONResponse:
    """注入人工反馈后从断点继续执行，完成 revise → publish 流程。

    面试考点（核心）：
      1. aupdate_state(): 向当前 Checkpoint 注入新的 State 字段（human_feedback）
         → SqliteSaver 将更新后的 State 写入新的 checkpoint 快照
      2. ainvoke(None, config): 传 None 表示"不新增输入，从上次断点继续"
         → LangGraph 加载最新 Checkpoint，从 revise 节点继续执行
      3. 整个流程产生多个 Checkpoint 快照，可在 SQLite 的 checkpoints 表中观察 State 变化

    Args:
        thread_id: 由 /hitl/start 返回的会话标识
        body.feedback: 人工修订意见
        body.approve_without_revision: True=直接发布（跳过修订），False=按 feedback 修订
    """
    try:
        from langgraph_impl.checkpoint_store import get_review_app

        app = get_review_app()
        config = {"configurable": {"thread_id": thread_id}}

        # 验证任务存在且处于暂停状态
        current_state = await app.aget_state(config)
        if not current_state or not current_state.values:
            return JSONResponse(
                status_code=404,
                content={"error": f"thread_id={thread_id} 不存在"},
            )
        if not list(current_state.next):
            return JSONResponse(
                status_code=400,
                content={"error": f"thread_id={thread_id} 已完成，无需 resume"},
            )

        if body.approve_without_revision:
            # 直接发布：将 needs_revision 改为 False，路由跳过 revise 直接到 publish
            await app.aupdate_state(
                config=config,
                values={"needs_revision": False, "human_feedback": ""},
            )
            logger.info("lg_hitl_approved_without_revision", thread_id=thread_id)
        else:
            # 注入反馈：保持 needs_revision=True，revise 节点会根据 feedback 修改草稿
            await app.aupdate_state(
                config=config,
                values={"human_feedback": body.feedback, "needs_revision": True},
            )
            logger.info("lg_hitl_feedback_injected", thread_id=thread_id, feedback_len=len(body.feedback))

        # 从断点继续执行（invoke(None) = 不新增输入，加载最新 Checkpoint 继续）
        final_result = await app.ainvoke(None, config=config)

        # 获取最终 checkpoint 信息
        final_state = await app.aget_state(config)
        final_checkpoint_id = final_state.config.get("configurable", {}).get("checkpoint_id", "")

        logger.info("lg_hitl_completed", thread_id=thread_id)

        return JSONResponse(content={
            "thread_id": thread_id,
            "status": "completed",
            "approved": final_result.get("approved", False),
            "final_draft": final_result.get("draft", ""),
            "human_feedback": body.feedback,
            "final_checkpoint_id": final_checkpoint_id,
            "tip": f"可通过 GET /api/v1/langgraph/checkpoints/{thread_id} 查看完整 State 变化历史",
        })
    except Exception as e:
        logger.error("lg_hitl_resume_error", thread_id=thread_id, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── 4. 通用 Checkpoint 历史查询 ───────────────────────────────────

@router.get("/checkpoints/{thread_id}", summary="查询任意 thread 的 Checkpoint 历史（可验证 SQLite 数据）")
async def get_checkpoints(
    thread_id: str,
    graph_type: str = "conversation",
) -> JSONResponse:
    """查询指定 thread 的所有 Checkpoint 快照，可与 SQLite 数据对照验证。

    面试考点：
      - 每个 Checkpoint 对应一次节点执行后的完整 State 快照
      - 返回数据与 lg_checkpoints.db 的 checkpoints 表记录一一对应
      - 通过 checkpoint_id 可以"时间旅行"回到任意历史状态重新执行
      - metadata.writes 字段记录了该 checkpoint 由哪个节点写入

    Args:
        thread_id: 会话标识
        graph_type: "conversation"（对话图）或 "hitl"（审核图）
    """
    try:
        from langgraph_impl.checkpoint_store import get_conversation_app, get_review_app
        from langgraph_impl.checkpoint_demo import get_thread_history

        if graph_type == "hitl":
            app = get_review_app()
        else:
            app = get_conversation_app()

        history = await get_thread_history(app, thread_id)

        return JSONResponse(content={
            "thread_id": thread_id,
            "graph_type": graph_type,
            "total_checkpoints": len(history),
            "checkpoints": history,
            "sqlite_tip": "可用 DB Browser for SQLite 打开 lg_checkpoints.db，在 checkpoints 表中查看原始数据",
        })
    except Exception as e:
        logger.error("lg_get_checkpoints_error", thread_id=thread_id, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── 5. 工具端点 ───────────────────────────────────────────────────

@router.get("/comparison", summary="自研 vs LangGraph 代码量对比")
async def get_comparison() -> JSONResponse:
    """返回自研引擎与 LangGraph 实现的代码量对比数据。"""
    from langgraph_impl.react_agent import code_comparison
    return JSONResponse(content=code_comparison())


@router.get("/graph/react", summary="获取 ReAct Graph 结构（Mermaid 格式）")
async def get_react_graph_structure() -> JSONResponse:
    """返回 LangGraph ReAct 图的结构描述。"""
    try:
        from langgraph_impl.react_agent import compile_react_graph
        app = compile_react_graph()
        mermaid = app.get_graph().draw_mermaid()
        return JSONResponse(content={"mermaid": mermaid})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ── 6. SSE 流式输出（内部函数）───────────────────────────────────

async def _stream_langgraph(question: str, mode: str, thread_id: str = ""):
    """LangGraph 流式输出生成器 — 将 LangGraph 事件转换为前端可识别的 SSE 格式。"""
    try:
        if mode == "react":
            from langgraph_impl.react_agent import compile_react_graph
            from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

            # react 模式：有 thread_id 则使用 SqliteSaver 持久化
            if thread_id:
                from langgraph_impl.checkpoint_store import get_checkpointer
                app = compile_react_graph(checkpointer=get_checkpointer())
                config: dict[str, Any] = {"configurable": {"thread_id": thread_id}}
            else:
                app = compile_react_graph()
                config = {}

            messages = [
                SystemMessage(content="你是一个智能助手，可以使用工具来回答问题。请用中文回答。"),
                HumanMessage(content=question),
            ]

            iteration = 0
            stream_kwargs: dict[str, Any] = {"messages": messages, "iteration_count": 0, "error": ""}
            async for event in app.astream(stream_kwargs, config=config or None):
                for node_name, node_output in event.items():
                    msgs = node_output.get("messages", [])
                    for msg in msgs:
                        if isinstance(msg, AIMessage):
                            if msg.tool_calls:
                                iteration += 1
                                for tc in msg.tool_calls:
                                    payload = {
                                        "type": "tool_call",
                                        "tool": tc.get("name", ""),
                                        "args": tc.get("args", {}),
                                        "thought": msg.content or "",
                                        "iteration": iteration,
                                    }
                                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                            elif msg.content:
                                if node_name == "agent":
                                    yield f"data: {json.dumps({'type': 'thinking', 'message': str(msg.content)[:120]}, ensure_ascii=False)}\n\n"
                                    yield f"data: {json.dumps({'type': 'content', 'delta': msg.content}, ensure_ascii=False)}\n\n"
                        elif isinstance(msg, ToolMessage):
                            payload = {
                                "type": "tool_result",
                                "result": str(msg.content),
                                "tool_call_id": getattr(msg, "tool_call_id", ""),
                            }
                            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        elif mode == "plan_execute":
            from langgraph_impl.plan_execute import run_plan_execute_agent
            yield f"data: {json.dumps({'type': 'thinking', 'message': '正在规划执行步骤...'}, ensure_ascii=False)}\n\n"
            result = await run_plan_execute_agent(question)
            answer = result.get("final_answer") or result.get("answer") or str(result)
            yield f"data: {json.dumps({'type': 'content', 'delta': answer}, ensure_ascii=False)}\n\n"

        elif mode == "sequential":
            from langgraph_impl.multi_agent import run_sequential_agents
            yield f"data: {json.dumps({'type': 'thinking', 'message': 'Sequential 多 Agent 协作中...'}, ensure_ascii=False)}\n\n"
            result = await run_sequential_agents(question)
            answer = result.get("final_report") or result.get("answer") or str(result)
            yield f"data: {json.dumps({'type': 'content', 'delta': answer}, ensure_ascii=False)}\n\n"

        elif mode == "supervisor":
            from langgraph_impl.multi_agent import run_supervisor_agents
            yield f"data: {json.dumps({'type': 'thinking', 'message': 'Supervisor 多 Agent 协作中...'}, ensure_ascii=False)}\n\n"
            result = await run_supervisor_agents(question)
            answer = result.get("final_report") or result.get("answer") or str(result)
            yield f"data: {json.dumps({'type': 'content', 'delta': answer}, ensure_ascii=False)}\n\n"

        else:
            yield f"data: {json.dumps({'error': f'未知模式: {mode}'}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.error("stream_langgraph_error", mode=mode, error=str(e))
        yield f"data: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"

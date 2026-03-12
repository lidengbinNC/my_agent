"""Agent 对话路由 — ReAct 引擎驱动，SSE 实时推送思考过程，支持会话持久化。

面试考点:
  - SSE 结构化事件: thinking / action / observation / final_answer / error
  - 记忆注入: 从数据库加载历史消息，注入 ReAct 引擎
  - 持久化: 对话结束后将 user/assistant 消息写入数据库
  - 工具调用记录: 将 Action/Observation 写入 tool_calls 表（审计）
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from my_agent.api.schemas.chat import (
    ChatRequest,
    ChatResponse,
    SSEEvent,
    SSEEventType,
)
from my_agent.config.settings import settings
from my_agent.core.dependencies import create_memory, get_guardrails, get_react_engine
from my_agent.core.engine.react_engine import ReActEngine, ReActStepType
from my_agent.domain.guardrails.base import GuardAction
from my_agent.infrastructure.db.database import get_db
from my_agent.infrastructure.db.repository import MessageRepository, SessionRepository
from my_agent.utils.cost_tracker import get_cost_tracker

router = APIRouter(tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(
    req: ChatRequest,
    engine: ReActEngine = Depends(get_react_engine),
    db: AsyncSession = Depends(get_db),
):
    # ── 输入护栏检查 ──────────────────────────────────────────────
    # output_chain 用于最终回复 PII 脱敏等，流式/非流式路径内使用
    input_chain, output_chain, _ = get_guardrails()
    if input_chain:
        _, guard_result = await input_chain.check(req.message)
        if guard_result and guard_result.action == GuardAction.BLOCK:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"输入被安全护栏拦截: {guard_result.reason}")

    # 获取或创建会话
    session_id = req.session_id
    s_repo = SessionRepository(db)
    m_repo = MessageRepository(db)

    if session_id:
        session = await s_repo.get_by_id(session_id)
        if not session:
            session = await s_repo.create()
            session_id = session.id
    else:
        session = await s_repo.create()
        session_id = session.id

    # 用 history_budget 估算最多需要加载多少条消息，避免无谓地从数据库拉取大量数据
    # 粗略估算：每条消息平均 200 tokens，多拉 50% 作为余量供记忆策略裁剪
    budget = settings.context_budget
    estimated_limit = max(10, int(budget.history_budget / 200 * 1.5))

    # 从数据库加载历史消息（原始条数）
    raw_history = await m_repo.to_domain_messages(session_id, limit=estimated_limit)

    # 计算当前对话轮数（1轮 = 1条user + 1条assistant，取 user 条数即可）
    turn_count = sum(1 for msg in raw_history if msg.role.value == "user")

    # 根据会话的 memory_type + 实际轮数，自动选择或使用指定的记忆策略
    memory = create_memory(session.memory_type, turn_count=turn_count)

    # 将原始历史填充到记忆策略中（window 自动截断，summary 自动压缩）
    for msg in raw_history:
        if msg.role.value == "user":
            await memory.add_user_message(msg.content or "", session_id=session_id)
        elif msg.role.value == "assistant":
            await memory.add_assistant_message(msg.content or "", session_id=session_id)

    # 通过记忆策略获取处理后的历史
    # react_engine 内部还会用 trim_history_to_budget 做最终精确裁剪
    history = await memory.get_history(session_id=session_id)

    # 持久化用户消息
    await m_repo.add(session_id, "user", req.message)

    if req.stream:
        return StreamingResponse(
            _stream_react(engine, req.message, session_id, history, db, output_chain),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式：收集所有步骤
    final_answer = ""
    async for step in engine.run(req.message, history=history):
        if step.type == ReActStepType.FINAL_ANSWER:
            final_answer = step.answer
        elif step.type == ReActStepType.ERROR:
            final_answer = f"[错误] {step.error}"

    # 输出护栏：PII 脱敏等（MODIFY 后放行）
    if output_chain and final_answer:
        final_answer, _ = await output_chain.check(final_answer)

    # 持久化 AI 回复（已脱敏后的内容写入 DB，避免泄露）
    await m_repo.add(session_id, "assistant", final_answer)

    return ChatResponse(
        session_id=session_id,
        content=final_answer,
        usage={},
    )


async def _stream_react(
    engine: ReActEngine,
    query: str,
    session_id: str,
    history: list,
    db: AsyncSession,
    output_chain=None,
) -> AsyncGenerator[str, None]:
    """将 ReAct 步骤转换为 SSE 事件流，并持久化对话记录。"""
    m_repo = MessageRepository(db)
    final_answer = ""
    last_assistant_msg_id: int | None = None

    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={"session_id": session_id, "message": "Agent 启动，开始推理..."},
    ).to_sse()

    try:
        async for step in engine.run(query, history=history):
            if step.type == ReActStepType.THINKING:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={
                        "iteration": step.iteration,
                        "message": f"第 {step.iteration} 步：思考中...",
                    },
                ).to_sse()

            elif step.type == ReActStepType.ACTION:
                yield SSEEvent(
                    event=SSEEventType.TOOL_CALL,
                    data={
                        "iteration": step.iteration,
                        "thought": step.thought,
                        "tool": step.action,
                        "args": step.action_input,
                    },
                ).to_sse()
                # 持久化 assistant 消息（含工具调用思考）
                msg = await m_repo.add(session_id, "assistant", step.thought or f"[调用工具: {step.action}]")
                last_assistant_msg_id = msg.id

            elif step.type == ReActStepType.OBSERVATION:
                yield SSEEvent(
                    event=SSEEventType.TOOL_RESULT,
                    data={
                        "iteration": step.iteration,
                        "tool": step.action,
                        "result": step.observation,
                    },
                ).to_sse()
                # 持久化工具调用记录
                if last_assistant_msg_id:
                    from my_agent.infrastructure.db.repository import ToolCallRepository
                    import json as _json
                    tc_repo = ToolCallRepository(db)
                    await tc_repo.record(
                        message_id=last_assistant_msg_id,
                        tool_name=step.action,
                        arguments=_json.dumps({}, ensure_ascii=False),
                        result=step.observation,
                    )

            elif step.type == ReActStepType.FINAL_ANSWER:
                if step.thought:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"iteration": step.iteration, "message": step.thought},
                    ).to_sse()
                answer = step.answer
                # 输出护栏：整段脱敏后再推送与持久化（需完整文本才能正确匹配 PII 正则）
                if output_chain and answer:
                    answer, _ = await output_chain.check(answer)
                final_answer = answer
                chunk_size = 10
                for i in range(0, len(answer), chunk_size):
                    yield SSEEvent(
                        event=SSEEventType.CONTENT,
                        data={"delta": answer[i: i + chunk_size]},
                    ).to_sse()
                yield SSEEvent(
                    event=SSEEventType.DONE,
                    data={"session_id": session_id, "content": answer},
                ).to_sse()
                break

            elif step.type == ReActStepType.ERROR:
                yield SSEEvent(
                    event=SSEEventType.ERROR,
                    data={"error": step.error, "iteration": step.iteration},
                ).to_sse()
                final_answer = f"[错误] {step.error}"
                break

    except Exception as e:
        yield SSEEvent(
            event=SSEEventType.ERROR,
            data={"error": str(e)},
        ).to_sse()
        final_answer = f"[异常] {e}"

    # 持久化最终 AI 回复
    # 注意：不在这里手动 commit，由 get_db() 依赖统一在请求结束后 commit
    if final_answer:
        await m_repo.add(session_id, "assistant", final_answer)

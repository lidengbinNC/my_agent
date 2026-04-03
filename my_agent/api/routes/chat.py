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
    ResumeRunRequest,
    SSEEvent,
    SSEEventType,
)
from my_agent.config.settings import settings
from my_agent.core.dependencies import create_memory, get_guardrails, get_react_engine
from my_agent.core.engine.react_engine import ReActEngine, ReActRunControl, ReActStep, ReActStepType
from my_agent.domain.agent import AgentSkill, get_skill_registry
from my_agent.domain.guardrails.base import GuardAction
from my_agent.infrastructure.db.database import get_db
from my_agent.infrastructure.db.repository import (
    ApprovalRecordRepository,
    MessageRepository,
    SessionRepository,
)

router = APIRouter(tags=["chat"])


def _build_run_control(req: ChatRequest) -> ReActRunControl:
    return ReActRunControl(
        pause_before_tools=req.pause_before_tools,
        pause_before_answer=req.pause_before_answer,
        approval_before_tools=req.approval_before_tools,
        approval_before_answer=req.approval_before_answer,
    )


@router.get("/skills")
async def list_skills():
    registry = get_skill_registry()
    return {
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "allowed_tools": skill.allowed_tools,
                "trigger_terms": skill.trigger_terms,
            }
            for skill in registry.all()
        ]
    }


@router.post("/chat/completions")
async def chat_completions(
    req: ChatRequest,
    engine: ReActEngine = Depends(get_react_engine),
    db: AsyncSession = Depends(get_db),
):
    from fastapi import HTTPException

    # ── 输入护栏检查 ──────────────────────────────────────────────
    # output_chain 用于最终回复 PII 脱敏等，流式/非流式路径内使用
    input_chain, output_chain, _ = get_guardrails()
    if input_chain:
        _, guard_result = await input_chain.check(req.message)
        if guard_result and guard_result.action == GuardAction.BLOCK:
            raise HTTPException(status_code=400, detail=f"输入被安全护栏拦截: {guard_result.reason}")

    skill_registry = get_skill_registry()
    active_skill: AgentSkill | None
    if req.skill:
        active_skill = skill_registry.get(req.skill)
        if active_skill is None:
            raise HTTPException(
                status_code=400,
                detail=f"Skill '{req.skill}' 不存在，可用 Skills: {skill_registry.names()}",
            )
    else:
        active_skill = skill_registry.match(req.message)

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
    run_id = req.run_id or f"{session_id}:{uuid.uuid4()}"
    run_control = _build_run_control(req)

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
            _stream_react(
                engine,
                req.message,
                session_id,
                run_id,
                history,
                db,
                output_chain,
                active_skill,
                run_control,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式：收集所有步骤
    final_answer = ""
    paused_step: ReActStep | None = None
    async for step in engine.run(
        req.message,
        history=history,
        skill=active_skill,
        thread_id=run_id,
        control=run_control,
    ):
        if step.type == ReActStepType.FINAL_ANSWER:
            final_answer = step.answer
        elif step.type == ReActStepType.PAUSED:
            paused_step = step
            break
        elif step.type == ReActStepType.ERROR:
            final_answer = f"[错误] {step.error}"

    if paused_step is not None:
        state = await engine.get_run_state(run_id)
        return ChatResponse(
            session_id=session_id,
            run_id=run_id,
            status="paused",
            content="",
            checkpoint_id=paused_step.checkpoint_id,
            pause_reason=paused_step.pause_reason,
            requires_approval=paused_step.requires_approval,
            pending_node=str(paused_step.data.get("node", "") or ""),
            next_nodes=state.get("next_nodes", []),
            data=paused_step.data,
            usage={},
            skill=active_skill.name if active_skill else None,
        )

    # 输出护栏：PII 脱敏等（MODIFY 后放行）
    if output_chain and final_answer:
        final_answer, _ = await output_chain.check(final_answer)

    # 持久化 AI 回复（已脱敏后的内容写入 DB，避免泄露）
    await m_repo.add(session_id, "assistant", final_answer)

    return ChatResponse(
        session_id=session_id,
        run_id=run_id,
        status="completed",
        content=final_answer,
        usage={},
        skill=active_skill.name if active_skill else None,
    )


@router.get("/chat/runs/{run_id}")
async def get_chat_run_state(
    run_id: str,
    engine: ReActEngine = Depends(get_react_engine),
):
    return await engine.get_run_state(run_id)


@router.get("/chat/runs/{run_id}/history")
async def get_chat_run_history(
    run_id: str,
    engine: ReActEngine = Depends(get_react_engine),
):
    return {
        "run_id": run_id,
        "history": await engine.get_run_history(run_id),
    }


@router.post("/chat/runs/{run_id}/resume")
async def resume_chat_run(
    run_id: str,
    body: ResumeRunRequest,
    engine: ReActEngine = Depends(get_react_engine),
    db: AsyncSession = Depends(get_db),
):
    input_chain, output_chain, _ = get_guardrails()
    _ = input_chain  # 保持接口对齐；resume 不重新做输入检查
    previous_state = await engine.get_run_state(run_id)
    stage = previous_state.get("current_node", "")
    checkpoint_id = previous_state.get("checkpoint_id", "")
    session_id = run_id.split(":", 1)[0] if ":" in run_id else ""
    if previous_state.get("status") == "paused" and body.action.lower() in {"resume", "approve", "reject", "cancel"}:
        approval_repo = ApprovalRecordRepository(db)
        await approval_repo.add(
            run_id=run_id,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            stage=stage or "unknown",
            decision=body.action.lower(),
            feedback=body.feedback,
        )

    if body.stream:
        return StreamingResponse(
            _stream_react_resume(
                engine,
                run_id,
                body,
                db,
                output_chain,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    final_answer = ""
    paused_step: ReActStep | None = None
    async for step in engine.resume_run(run_id, action=body.action, feedback=body.feedback):
        if step.type == ReActStepType.FINAL_ANSWER:
            final_answer = step.answer
        elif step.type == ReActStepType.PAUSED:
            paused_step = step
            break
        elif step.type == ReActStepType.ERROR:
            final_answer = f"[错误] {step.error}"

    state = await engine.get_run_state(run_id)
    if paused_step is not None:
        return ChatResponse(
            session_id=session_id,
            run_id=run_id,
            status="paused",
            content="",
            checkpoint_id=paused_step.checkpoint_id,
            pause_reason=paused_step.pause_reason,
            requires_approval=paused_step.requires_approval,
            pending_node=str(paused_step.data.get("node", "") or ""),
            next_nodes=state.get("next_nodes", []),
            data=paused_step.data,
            usage={},
        )

    if output_chain and final_answer:
        final_answer, _ = await output_chain.check(final_answer)

    if final_answer and session_id:
        m_repo = MessageRepository(db)
        await m_repo.add(session_id, "assistant", final_answer)

    return ChatResponse(
        session_id=session_id,
        run_id=run_id,
        status=state.get("status", "completed"),
        content=final_answer,
        checkpoint_id=state.get("checkpoint_id", ""),
        next_nodes=state.get("next_nodes", []),
        data={},
        usage={},
    )


async def _stream_react(
    engine: ReActEngine,
    query: str,
    session_id: str,
    run_id: str,
    history: list,
    db: AsyncSession,
    output_chain=None,
    skill: AgentSkill | None = None,
    control: ReActRunControl | None = None,
) -> AsyncGenerator[str, None]:
    """将 ReAct 步骤转换为 SSE 事件流，并持久化对话记录。"""
    m_repo = MessageRepository(db)
    final_answer = ""
    last_assistant_msg_id: int | None = None
    last_tool_args: dict = {}

    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={
            "session_id": session_id,
            "run_id": run_id,
            "message": "Agent 启动，开始推理...",
            "skill": skill.name if skill else None,
        },
    ).to_sse()

    try:
        async for step in engine.run(
            query,
            history=history,
            skill=skill,
            thread_id=run_id,
            control=control,
        ):
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
                last_tool_args = step.action_input or {}

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
                        arguments=_json.dumps(last_tool_args, ensure_ascii=False),
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
                    data={
                        "session_id": session_id,
                        "run_id": run_id,
                        "content": answer,
                        "skill": skill.name if skill else None,
                    },
                ).to_sse()
                break

            elif step.type == ReActStepType.PAUSED:
                yield SSEEvent(
                    event=SSEEventType.PAUSED,
                    data={
                        "session_id": session_id,
                        "run_id": run_id,
                        "checkpoint_id": step.checkpoint_id,
                        "pause_reason": step.pause_reason,
                        "requires_approval": step.requires_approval,
                        "node": step.data.get("node", ""),
                        "action": step.action,
                        "args": step.action_input,
                        "answer_preview": step.answer,
                        "data": step.data,
                    },
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


async def _stream_react_resume(
    engine: ReActEngine,
    run_id: str,
    body: ResumeRunRequest,
    db: AsyncSession,
    output_chain=None,
) -> AsyncGenerator[str, None]:
    session_id = run_id.split(":", 1)[0] if ":" in run_id else ""
    m_repo = MessageRepository(db)
    final_answer = ""
    last_assistant_msg_id: int | None = None
    last_tool_args: dict = {}

    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={
            "session_id": session_id,
            "run_id": run_id,
            "message": "Run 恢复执行中...",
            "action": body.action,
        },
    ).to_sse()

    async for step in engine.resume_run(run_id, action=body.action, feedback=body.feedback):
        if step.type == ReActStepType.THINKING:
            yield SSEEvent(
                event=SSEEventType.THINKING,
                data={"iteration": step.iteration, "message": f"第 {step.iteration} 步：思考中..."},
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
            msg = await m_repo.add(session_id, "assistant", step.thought or f"[调用工具: {step.action}]")
            last_assistant_msg_id = msg.id
            last_tool_args = step.action_input or {}
        elif step.type == ReActStepType.OBSERVATION:
            yield SSEEvent(
                event=SSEEventType.TOOL_RESULT,
                data={
                    "iteration": step.iteration,
                    "tool": step.action,
                    "result": step.observation,
                },
            ).to_sse()
            if last_assistant_msg_id:
                from my_agent.infrastructure.db.repository import ToolCallRepository
                import json as _json

                tc_repo = ToolCallRepository(db)
                await tc_repo.record(
                    message_id=last_assistant_msg_id,
                    tool_name=step.action,
                    arguments=_json.dumps(last_tool_args, ensure_ascii=False),
                    result=step.observation,
                )
        elif step.type == ReActStepType.FINAL_ANSWER:
            answer = step.answer
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
                data={"session_id": session_id, "run_id": run_id, "content": answer},
            ).to_sse()
            break
        elif step.type == ReActStepType.PAUSED:
            yield SSEEvent(
                event=SSEEventType.PAUSED,
                data={
                    "session_id": session_id,
                    "run_id": run_id,
                    "checkpoint_id": step.checkpoint_id,
                    "pause_reason": step.pause_reason,
                    "requires_approval": step.requires_approval,
                    "node": step.data.get("node", ""),
                    "action": step.action,
                    "args": step.action_input,
                    "answer_preview": step.answer,
                    "data": step.data,
                },
            ).to_sse()
            break
        elif step.type == ReActStepType.ERROR:
            yield SSEEvent(
                event=SSEEventType.ERROR,
                data={"error": step.error, "iteration": step.iteration},
            ).to_sse()
            final_answer = f"[错误] {step.error}"
            break

    if final_answer and session_id:
        await m_repo.add(session_id, "assistant", final_answer)

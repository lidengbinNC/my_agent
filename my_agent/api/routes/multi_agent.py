"""多 Agent 协作 API。

默认走生产版 LangGraph 运行时，支持：
  - 结构化 handoff 上下文共享
  - session_id / run_id / thread_id
  - pause / resume / history
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from langgraph_impl.multi_agent_runtime import (
    get_multi_agent_run_history,
    get_multi_agent_run_state,
    resume_multi_agent_run,
    serialize_agent_spec,
    stream_multi_agent_run,
)
from my_agent.api.schemas.chat import SSEEvent, SSEEventType
from my_agent.api.schemas.multi_agent import (
    AgentEventInfo,
    MultiAgentRunRequest,
    MultiAgentRunResponse,
    ResumeMultiAgentRunRequest,
)
from my_agent.config.settings import settings
from my_agent.core.dependencies import create_memory, get_guardrails
from my_agent.core.multi_agent.scenarios import (
    build_customer_complaint_review_agents,
    build_customer_complex_case_agents,
    build_data_analysis_agents,
    build_research_report_agents,
)
from my_agent.domain.guardrails.base import GuardAction
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentRole
from my_agent.infrastructure.db.database import get_db
from my_agent.infrastructure.db.repository import (
    MessageRepository,
    MultiAgentEventRepository,
    MultiAgentRunRepository,
    SessionRepository,
)
router = APIRouter(prefix="/multi-agent", tags=["multi-agent"])

_PRESET_SCENARIOS = {
    "research_report": {
        "name": "研究报告生成",
        "description": "Researcher → Writer → Reviewer，顺序协作生成高质量研究报告",
        "mode": "sequential",
        "agents": ["researcher", "writer", "reviewer"],
    },
    "data_analysis": {
        "name": "数据分析",
        "description": "Manager 协调 DataAgent + AnalystAgent + ReporterAgent，层级协作生成数据报告",
        "mode": "hierarchical",
        "agents": ["manager", "data_agent", "analyst_agent", "reporter_agent"],
    },
    "customer_complaint_review": {
        "name": "投诉复核",
        "description": "Supervisor 反复调度事实调查、政策核验和处置建议，输出投诉复核结论",
        "mode": "supervisor",
        "agents": ["manager", "fact_agent", "policy_agent", "resolution_agent"],
    },
    "customer_complex_case": {
        "name": "复杂售后案件",
        "description": "调查员、政策核验员、工单草稿专家顺序协作处理复杂售后案件",
        "mode": "sequential",
        "agents": ["investigator", "policy_checker", "ticket_drafter"],
    },
}
_AGENT_COLORS = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899"]


@router.get("/scenarios")
async def list_scenarios() -> dict:
    return {"scenarios": _PRESET_SCENARIOS}


@router.post("/run")
async def run_multi_agent(
    req: MultiAgentRunRequest,
    db: AsyncSession = Depends(get_db),
):
    from langgraph_impl.checkpoint_store import get_multi_agent_app

    input_chain, output_chain, _ = get_guardrails()
    if input_chain:
        _, guard_result = await input_chain.check(req.goal)
        if guard_result and guard_result.action == GuardAction.BLOCK:
            raise HTTPException(status_code=400, detail=f"输入被安全护栏拦截: {guard_result.reason}")

    s_repo = SessionRepository(db)
    m_repo = MessageRepository(db)
    run_repo = MultiAgentRunRepository(db)
    event_repo = MultiAgentEventRepository(db)

    session_id = await _ensure_session_id(req.session_id, s_repo)
    run_id = req.run_id or req.thread_id or f"{session_id}:{uuid.uuid4()}"
    thread_id = req.thread_id or run_id
    await m_repo.add(session_id, "user", req.goal)

    context_summary = await _build_context_summary(session_id, s_repo, m_repo, extra_context=req.context)
    specs = _build_agent_specs(req)
    manager_name = _resolve_manager_name(specs)
    scenario_mode = req.mode.lower() if req.scenario == "custom" else _PRESET_SCENARIOS.get(req.scenario, {}).get("mode", req.mode.lower())
    agent_specs_payload = [serialize_agent_spec(spec) for spec in specs]
    app = get_multi_agent_app()

    await run_repo.create_or_update(
        run_id=run_id,
        thread_id=thread_id,
        session_id=session_id,
        scenario=req.scenario,
        mode=scenario_mode,
        goal=req.goal,
        status="running",
    )

    if req.stream:
        return StreamingResponse(
            _stream_multi_agent_runtime(
                app=app,
                req=req,
                db=db,
                session_id=session_id,
                run_id=run_id,
                thread_id=thread_id,
                mode=scenario_mode,
                manager_name=manager_name,
                context_summary=context_summary,
                agent_specs_payload=agent_specs_payload,
                output_chain=output_chain,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    events: list[AgentEventInfo] = []
    final_answer = ""
    paused_payload: dict | None = None
    agent_colors: dict[str, str] = {}

    async for evt in stream_multi_agent_run(
        app,
        session_id=session_id,
        run_id=run_id,
        thread_id=thread_id,
        scenario=req.scenario,
        mode=scenario_mode,
        goal=req.goal,
        global_context_summary=context_summary,
        agent_specs=agent_specs_payload,
        manager_name=manager_name,
        pause_before_handoff=req.pause_before_handoff,
        approval_before_handoff=req.approval_before_handoff,
        pause_before_answer=req.pause_before_answer,
        approval_before_answer=req.approval_before_answer,
    ):
        event_type = str(evt.get("type", "") or "")
        if event_type in {"agent_done", "message"}:
            agent_name = str(evt.get("agent_name", "") or "")
            if agent_name and agent_name not in agent_colors:
                agent_colors[agent_name] = _AGENT_COLORS[len(agent_colors) % len(_AGENT_COLORS)]
        if event_type == "final_answer":
            final_answer = str(evt.get("answer", "") or "")
        if event_type == "paused":
            paused_payload = evt
        events.append(
            AgentEventInfo(
                event_type=event_type,
                agent_name=str(evt.get("agent_name", "") or ""),
                message=str(evt.get("message", "") or ""),
                result=str(evt.get("result", evt.get("answer", "")) or ""),
                data=evt.get("data", {}) or {},
            )
        )

    state = await get_multi_agent_run_state(app, run_id)
    if paused_payload is not None:
        await run_repo.create_or_update(
            run_id=run_id,
            thread_id=thread_id,
            session_id=session_id,
            scenario=req.scenario,
            mode=scenario_mode,
            goal=req.goal,
            status="paused",
        )
        await event_repo.add(
            run_id=run_id,
            agent_name=str(paused_payload.get("agent_name", "") or ""),
            event_type="paused",
            summary=str(paused_payload.get("message", "") or ""),
            payload=paused_payload.get("data", {}) or {},
        )
        return MultiAgentRunResponse(
            session_id=session_id,
            run_id=run_id,
            thread_id=thread_id,
            scenario=req.scenario,
            mode=scenario_mode,
            status="paused",
            checkpoint_id=state.get("checkpoint_id", ""),
            pause_reason=state.get("pause_reason", ""),
            requires_approval=state.get("requires_approval", False),
            next_nodes=state.get("next_nodes", []),
            final_answer="",
            agent_events=events,
            data={
                "agent_colors": agent_colors,
                "shared_facts": state.get("shared_facts", []),
                "handoffs": state.get("handoffs", []),
            },
        )

    if output_chain and final_answer:
        final_answer, _ = await output_chain.check(final_answer)
    if final_answer:
        await m_repo.add(session_id, "assistant", final_answer)
    await run_repo.create_or_update(
        run_id=run_id,
        thread_id=thread_id,
        session_id=session_id,
        scenario=req.scenario,
        mode=scenario_mode,
        goal=req.goal,
        status=state.get("status", "completed"),
        final_answer=final_answer,
        error=state.get("error", ""),
    )
    if final_answer:
        await event_repo.add(
            run_id=run_id,
            agent_name="",
            event_type="done",
            summary="多 Agent 运行完成",
            payload={"shared_facts": state.get("shared_facts", []), "handoffs": state.get("handoffs", [])},
        )
    return MultiAgentRunResponse(
        session_id=session_id,
        run_id=run_id,
        thread_id=thread_id,
        scenario=req.scenario,
        mode=scenario_mode,
        status=state.get("status", "completed"),
        checkpoint_id=state.get("checkpoint_id", ""),
        next_nodes=state.get("next_nodes", []),
        final_answer=final_answer,
        agent_events=events,
        data={
            "agent_colors": agent_colors,
            "shared_facts": state.get("shared_facts", []),
            "handoffs": state.get("handoffs", []),
        },
    )


@router.get("/runs/{run_id}")
async def get_multi_agent_state(run_id: str):
    from langgraph_impl.checkpoint_store import get_multi_agent_app

    return await get_multi_agent_run_state(get_multi_agent_app(), run_id)


@router.get("/runs/{run_id}/history")
async def get_multi_agent_history(run_id: str):
    from langgraph_impl.checkpoint_store import get_multi_agent_app

    return {
        "run_id": run_id,
        "history": await get_multi_agent_run_history(get_multi_agent_app(), run_id),
    }


@router.post("/runs/{run_id}/resume")
async def resume_multi_agent(
    run_id: str,
    body: ResumeMultiAgentRunRequest,
    db: AsyncSession = Depends(get_db),
):
    from langgraph_impl.checkpoint_store import get_multi_agent_app

    app = get_multi_agent_app()
    input_chain, output_chain, _ = get_guardrails()
    _ = input_chain

    run_repo = MultiAgentRunRepository(db)
    event_repo = MultiAgentEventRepository(db)
    m_repo = MessageRepository(db)
    previous_state = await get_multi_agent_run_state(app, run_id)
    session_id = str(previous_state.get("session_id", "") or "")
    thread_id = str(previous_state.get("thread_id", "") or run_id)
    if previous_state.get("status") == "paused":
        await event_repo.add(
            run_id=run_id,
            agent_name=str(previous_state.get("current_agent", "") or ""),
            event_type=f"resume:{body.action.lower()}",
            summary="恢复多 Agent 运行",
            payload={"feedback": body.feedback, "current_node": previous_state.get("current_node", "")},
        )

    if body.stream:
        return StreamingResponse(
            _stream_multi_agent_resume(
                app=app,
                run_id=run_id,
                thread_id=thread_id,
                db=db,
                body=body,
                output_chain=output_chain,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    events: list[AgentEventInfo] = []
    final_answer = ""
    paused_payload: dict | None = None
    async for evt in resume_multi_agent_run(app, run_id=run_id, action=body.action, feedback=body.feedback):
        event_type = str(evt.get("type", "") or "")
        if event_type == "final_answer":
            final_answer = str(evt.get("answer", "") or "")
        if event_type == "paused":
            paused_payload = evt
        events.append(
            AgentEventInfo(
                event_type=event_type,
                agent_name=str(evt.get("agent_name", "") or ""),
                message=str(evt.get("message", "") or ""),
                result=str(evt.get("result", evt.get("answer", "")) or ""),
                data=evt.get("data", {}) or {},
            )
        )

    state = await get_multi_agent_run_state(app, run_id)
    if paused_payload is not None:
        await run_repo.create_or_update(
            run_id=run_id,
            thread_id=thread_id,
            session_id=session_id,
            scenario=str(previous_state.get("scenario", "custom") or "custom"),
            mode=str(previous_state.get("mode", "sequential") or "sequential"),
            goal=str(previous_state.get("goal", "") or ""),
            status="paused",
        )
        return MultiAgentRunResponse(
            session_id=session_id,
            run_id=run_id,
            thread_id=thread_id,
            scenario=str(previous_state.get("scenario", "custom") or "custom"),
            mode=str(previous_state.get("mode", "sequential") or "sequential"),
            status="paused",
            checkpoint_id=state.get("checkpoint_id", ""),
            pause_reason=state.get("pause_reason", ""),
            requires_approval=state.get("requires_approval", False),
            next_nodes=state.get("next_nodes", []),
            final_answer="",
            agent_events=events,
            data={"shared_facts": state.get("shared_facts", []), "handoffs": state.get("handoffs", [])},
        )

    if output_chain and final_answer:
        final_answer, _ = await output_chain.check(final_answer)
    if final_answer and session_id:
        await m_repo.add(session_id, "assistant", final_answer)
    await run_repo.create_or_update(
        run_id=run_id,
        thread_id=thread_id,
        session_id=session_id,
        scenario=str(previous_state.get("scenario", "custom") or "custom"),
        mode=str(previous_state.get("mode", "sequential") or "sequential"),
        goal=str(previous_state.get("goal", "") or ""),
        status=state.get("status", "completed"),
        final_answer=final_answer,
        error=state.get("error", ""),
    )
    return MultiAgentRunResponse(
        session_id=session_id,
        run_id=run_id,
        thread_id=thread_id,
        scenario=str(previous_state.get("scenario", "custom") or "custom"),
        mode=str(previous_state.get("mode", "sequential") or "sequential"),
        status=state.get("status", "completed"),
        checkpoint_id=state.get("checkpoint_id", ""),
        next_nodes=state.get("next_nodes", []),
        final_answer=final_answer,
        agent_events=events,
        data={"shared_facts": state.get("shared_facts", []), "handoffs": state.get("handoffs", [])},
    )


async def _stream_multi_agent_runtime(
    *,
    app,
    req: MultiAgentRunRequest,
    db: AsyncSession,
    session_id: str,
    run_id: str,
    thread_id: str,
    mode: str,
    manager_name: str,
    context_summary: str,
    agent_specs_payload: list[dict],
    output_chain=None,
) -> AsyncGenerator[str, None]:
    run_repo = MultiAgentRunRepository(db)
    event_repo = MultiAgentEventRepository(db)
    m_repo = MessageRepository(db)
    agent_color_map: dict[str, str] = {}
    final_answer = ""

    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={
            "scenario": req.scenario,
            "mode": mode,
            "session_id": session_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "message": "多 Agent 协作启动...",
        },
    ).to_sse()

    async for evt in stream_multi_agent_run(
        app,
        session_id=session_id,
        run_id=run_id,
        thread_id=thread_id,
        scenario=req.scenario,
        mode=mode,
        goal=req.goal,
        global_context_summary=context_summary,
        agent_specs=agent_specs_payload,
        manager_name=manager_name,
        pause_before_handoff=req.pause_before_handoff,
        approval_before_handoff=req.approval_before_handoff,
        pause_before_answer=req.pause_before_answer,
        approval_before_answer=req.approval_before_answer,
    ):
        event_type = str(evt.get("type", "") or "")
        agent_name = str(evt.get("agent_name", "") or "")
        if agent_name and agent_name not in agent_color_map:
            agent_color_map[agent_name] = _AGENT_COLORS[len(agent_color_map) % len(_AGENT_COLORS)]
        color = agent_color_map.get(agent_name, "#6B7280")

        if event_type == "thinking":
            yield SSEEvent(
                event=SSEEventType.THINKING,
                data={
                    "agent": agent_name,
                    "color": color,
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "message": str(evt.get("message", "") or "协调中..."),
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
        elif event_type in {"agent_done", "message"}:
            await event_repo.add(
                run_id=run_id,
                agent_name=agent_name,
                event_type=event_type,
                summary=str(evt.get("message", "") or ""),
                payload=evt.get("data", {}) or {},
            )
            yield SSEEvent(
                event=SSEEventType.TOOL_RESULT,
                data={
                    "agent": agent_name,
                    "color": color,
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "result": str(evt.get("result", "") or ""),
                    "message": str(evt.get("message", "") or ""),
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
        elif event_type == "paused":
            await run_repo.create_or_update(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                scenario=req.scenario,
                mode=mode,
                goal=req.goal,
                status="paused",
            )
            await event_repo.add(
                run_id=run_id,
                agent_name=agent_name,
                event_type="paused",
                summary=str(evt.get("message", "") or ""),
                payload=evt.get("data", {}) or {},
            )
            yield SSEEvent(
                event=SSEEventType.PAUSED,
                data={
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "checkpoint_id": str(evt.get("checkpoint_id", "") or ""),
                    "pause_reason": str(evt.get("pause_reason", "") or ""),
                    "requires_approval": bool(evt.get("requires_approval", False)),
                    "agent": agent_name,
                    "message": str(evt.get("message", "") or ""),
                    "answer_preview": str(evt.get("answer_preview", "") or ""),
                    "resume_url": f"/api/v1/multi-agent/runs/{run_id}/resume",
                    "resume_mode": "multi_agent",
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
            return
        elif event_type == "final_answer":
            final_answer = str(evt.get("answer", "") or "")
            if output_chain and final_answer:
                final_answer, _ = await output_chain.check(final_answer)
            if final_answer:
                await m_repo.add(session_id, "assistant", final_answer)
            await run_repo.create_or_update(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                scenario=req.scenario,
                mode=mode,
                goal=req.goal,
                status="completed",
                final_answer=final_answer,
            )
            await event_repo.add(
                run_id=run_id,
                agent_name="",
                event_type="done",
                summary="多 Agent 运行完成",
                payload=evt.get("data", {}) or {},
            )
            for i in range(0, len(final_answer), 15):
                yield SSEEvent(
                    event=SSEEventType.CONTENT,
                    data={"delta": final_answer[i: i + 15]},
                ).to_sse()
            yield SSEEvent(
                event=SSEEventType.DONE,
                data={
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "answer": final_answer,
                    "agent_colors": agent_color_map,
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
            return
        elif event_type == "error":
            await run_repo.create_or_update(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                scenario=req.scenario,
                mode=mode,
                goal=req.goal,
                status="error",
                error=str(evt.get("error", "") or ""),
            )
            yield SSEEvent(
                event=SSEEventType.ERROR,
                data={"error": str(evt.get("error", "") or "未知错误"), "run_id": run_id, "thread_id": thread_id},
            ).to_sse()
            return


async def _stream_multi_agent_resume(
    *,
    app,
    run_id: str,
    thread_id: str,
    db: AsyncSession,
    body: ResumeMultiAgentRunRequest,
    output_chain=None,
) -> AsyncGenerator[str, None]:
    run_repo = MultiAgentRunRepository(db)
    event_repo = MultiAgentEventRepository(db)
    m_repo = MessageRepository(db)
    state_before = await get_multi_agent_run_state(app, run_id)
    session_id = str(state_before.get("session_id", "") or "")
    scenario = str(state_before.get("scenario", "custom") or "custom")
    mode = str(state_before.get("mode", "sequential") or "sequential")
    goal = str(state_before.get("goal", "") or "")
    agent_color_map: dict[str, str] = {}

    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={
            "session_id": session_id,
            "run_id": run_id,
            "thread_id": thread_id,
            "message": "多 Agent Run 恢复执行中...",
            "action": body.action,
        },
    ).to_sse()

    async for evt in resume_multi_agent_run(app, run_id=run_id, action=body.action, feedback=body.feedback):
        event_type = str(evt.get("type", "") or "")
        agent_name = str(evt.get("agent_name", "") or "")
        if agent_name and agent_name not in agent_color_map:
            agent_color_map[agent_name] = _AGENT_COLORS[len(agent_color_map) % len(_AGENT_COLORS)]
        color = agent_color_map.get(agent_name, "#6B7280")

        if event_type == "thinking":
            yield SSEEvent(
                event=SSEEventType.THINKING,
                data={"agent": agent_name, "color": color, "message": str(evt.get("message", "") or "恢复中...")},
            ).to_sse()
        elif event_type in {"agent_done", "message"}:
            await event_repo.add(
                run_id=run_id,
                agent_name=agent_name,
                event_type=event_type,
                summary=str(evt.get("message", "") or ""),
                payload=evt.get("data", {}) or {},
            )
            yield SSEEvent(
                event=SSEEventType.TOOL_RESULT,
                data={
                    "agent": agent_name,
                    "color": color,
                    "result": str(evt.get("result", "") or ""),
                    "message": str(evt.get("message", "") or ""),
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
        elif event_type == "paused":
            await run_repo.create_or_update(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                scenario=scenario,
                mode=mode,
                goal=goal,
                status="paused",
            )
            yield SSEEvent(
                event=SSEEventType.PAUSED,
                data={
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "checkpoint_id": str(evt.get("checkpoint_id", "") or ""),
                    "pause_reason": str(evt.get("pause_reason", "") or ""),
                    "requires_approval": bool(evt.get("requires_approval", False)),
                    "agent": agent_name,
                    "message": str(evt.get("message", "") or ""),
                    "answer_preview": str(evt.get("answer_preview", "") or ""),
                    "resume_url": f"/api/v1/multi-agent/runs/{run_id}/resume",
                    "resume_mode": "multi_agent",
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
            return
        elif event_type == "final_answer":
            answer = str(evt.get("answer", "") or "")
            if output_chain and answer:
                answer, _ = await output_chain.check(answer)
            if answer and session_id:
                await m_repo.add(session_id, "assistant", answer)
            await run_repo.create_or_update(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                scenario=scenario,
                mode=mode,
                goal=goal,
                status="completed",
                final_answer=answer,
            )
            for i in range(0, len(answer), 15):
                yield SSEEvent(
                    event=SSEEventType.CONTENT,
                    data={"delta": answer[i: i + 15]},
                ).to_sse()
            yield SSEEvent(
                event=SSEEventType.DONE,
                data={
                    "session_id": session_id,
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "answer": answer,
                    "agent_colors": agent_color_map,
                    **(evt.get("data", {}) or {}),
                },
            ).to_sse()
            return
        elif event_type == "error":
            await run_repo.create_or_update(
                run_id=run_id,
                thread_id=thread_id,
                session_id=session_id,
                scenario=scenario,
                mode=mode,
                goal=goal,
                status="error",
                error=str(evt.get("error", "") or ""),
            )
            yield SSEEvent(event=SSEEventType.ERROR, data={"error": str(evt.get("error", "") or "未知错误")}).to_sse()
            return


async def _ensure_session_id(session_id: str | None, s_repo: SessionRepository) -> str:
    if session_id:
        session = await s_repo.get_by_id(session_id)
        if session:
            return session.id
    session = await s_repo.create()
    return session.id


async def _build_context_summary(
    session_id: str,
    s_repo: SessionRepository,
    m_repo: MessageRepository,
    *,
    extra_context: str = "",
) -> str:
    session = await s_repo.get_by_id(session_id)
    if not session:
        return extra_context
    budget = settings.context_budget
    estimated_limit = max(10, int(budget.history_budget / 200 * 1.5))
    raw_history = await m_repo.to_domain_messages(session_id, limit=estimated_limit)
    turn_count = sum(1 for msg in raw_history if msg.role.value == "user")
    memory = create_memory(session.memory_type, turn_count=turn_count)
    for msg in raw_history:
        if msg.role.value == "user":
            await memory.add_user_message(msg.content or "", session_id=session_id)
        elif msg.role.value == "assistant":
            await memory.add_assistant_message(msg.content or "", session_id=session_id)
    history = await memory.get_history(session_id=session_id)
    history_text = "\n".join(
        f"{msg.role.value}: {(msg.content or '')[:400]}"
        for msg in history[-12:]
        if msg.content
    )
    return "\n\n".join(part for part in [extra_context.strip(), history_text.strip()] if part)


def _build_agent_specs(req: MultiAgentRunRequest) -> list[AgentSpec]:
    if req.scenario == "research_report":
        return build_research_report_agents()
    if req.scenario == "data_analysis":
        return build_data_analysis_agents()
    if req.scenario == "customer_complaint_review":
        return build_customer_complaint_review_agents()
    if req.scenario == "customer_complex_case":
        return build_customer_complex_case_agents()
    specs: list[AgentSpec] = []
    for item in req.agents:
        try:
            role = AgentRole(item.role)
        except ValueError:
            role = AgentRole.WORKER
        specs.append(
            AgentSpec(
                name=item.name,
                role=role,
                system_prompt=item.system_prompt,
                description=item.description,
                tools=item.tools,
                max_iterations=item.max_iterations,
            )
        )
    if specs:
        return specs
    return [
        AgentSpec(name="agent_a", role=AgentRole.WORKER, description="Worker A"),
        AgentSpec(name="agent_b", role=AgentRole.WORKER, description="Worker B"),
    ]


def _resolve_manager_name(specs: list[AgentSpec]) -> str:
    for spec in specs:
        if spec.role == AgentRole.MANAGER or spec.name == "manager":
            return spec.name
    return ""

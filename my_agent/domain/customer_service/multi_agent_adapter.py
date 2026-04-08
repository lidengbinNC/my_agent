"""客服 Copilot 到 multi-agent runtime 的适配层。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from langgraph_impl.multi_agent_runtime import (
    get_multi_agent_run_state,
    serialize_agent_spec,
    stream_multi_agent_run,
)
from my_agent.api.routes.multi_agent import run_multi_agent
from my_agent.api.schemas.chat import ChatResponse
from my_agent.api.schemas.multi_agent import AgentEventInfo, MultiAgentRunRequest, MultiAgentRunResponse
from my_agent.core.multi_agent.scenarios import (
    build_customer_complaint_review_agents,
    build_customer_complex_case_agents,
)
from my_agent.domain.customer_service.routing import (
    CustomerServiceRouteDecision,
    CustomerServiceRouteInput,
)
from my_agent.domain.customer_service.service import build_customer_service_message
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentRole


async def run_customer_service_multi_agent(
    *,
    body,
    route_input: CustomerServiceRouteInput,
    decision: CustomerServiceRouteDecision,
    db: AsyncSession,
) -> ChatResponse | StreamingResponse:
    req = build_customer_service_multi_agent_request(
        session_id=body.session_id,
        message=body.message,
        mode=route_input.mode,
        allow_write_actions=route_input.allow_write_actions,
        customer_context=route_input.customer_context,
        stream=bool(body.stream),
        approval_before_answer=bool(body.approval_before_answer),
        decision=decision,
    )
    response = await run_multi_agent(req, db=db)
    if isinstance(response, StreamingResponse):
        return response
    return adapt_multi_agent_response(response, decision=decision)


def build_customer_service_multi_agent_request(
    *,
    session_id: str | None,
    message: str,
    mode: str,
    allow_write_actions: bool,
    customer_context: dict[str, Any],
    stream: bool,
    approval_before_answer: bool,
    decision: CustomerServiceRouteDecision,
) -> MultiAgentRunRequest:
    context_text = build_customer_service_multi_agent_context(
        message=message,
        mode=mode,
        allow_write_actions=allow_write_actions,
        customer_context=customer_context,
        decision=decision,
    )
    return MultiAgentRunRequest(
        goal=message,
        session_id=session_id,
        context=context_text,
        scenario=decision.scenario,
        mode=decision.multi_agent_mode,
        stream=stream,
        approval_before_answer=approval_before_answer or decision.requires_final_approval,
    )


def adapt_multi_agent_response(
    response: MultiAgentRunResponse,
    *,
    decision: CustomerServiceRouteDecision,
) -> ChatResponse:
    data = {
        **decision.to_metadata(),
        "multi_agent_run_id": response.run_id,
        "multi_agent_thread_id": response.thread_id,
        "agent_events": [event.model_dump(mode="json") for event in response.agent_events],
        **response.data,
    }
    return ChatResponse(
        session_id=response.session_id,
        run_id=response.run_id,
        status=response.status,
        content=response.final_answer,
        checkpoint_id=response.checkpoint_id,
        pause_reason=response.pause_reason,
        requires_approval=response.requires_approval,
        pending_node=response.next_nodes[0] if response.next_nodes else "",
        next_nodes=response.next_nodes,
        data=data,
        usage={},
    )


async def run_customer_service_multi_agent_task(
    *,
    task_id: str,
    route_input: CustomerServiceRouteInput,
    decision: CustomerServiceRouteDecision,
    approval_before_answer: bool = False,
    progress_cb: Callable[[int, str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    from langgraph_impl.checkpoint_store import get_multi_agent_app

    app = get_multi_agent_app()
    specs = _build_specs(decision.scenario)
    manager_name = _resolve_manager_name(specs)
    serialized_specs = [serialize_agent_spec(spec) for spec in specs]
    run_id = f"task:{task_id}"
    thread_id = run_id
    session_id = f"task-session:{task_id}"
    context_text = build_customer_service_multi_agent_context(
        message=route_input.message,
        mode=route_input.mode,
        allow_write_actions=route_input.allow_write_actions,
        customer_context=route_input.customer_context,
        decision=decision,
    )

    events: list[dict[str, Any]] = []
    final_answer = ""
    paused_payload: dict[str, Any] | None = None
    thinking_count = 0

    async for evt in stream_multi_agent_run(
        app,
        session_id=session_id,
        run_id=run_id,
        thread_id=thread_id,
        scenario=decision.scenario,
        mode=decision.multi_agent_mode,
        goal=route_input.message,
        global_context_summary=context_text,
        agent_specs=serialized_specs,
        manager_name=manager_name,
        pause_before_handoff=False,
        approval_before_handoff=False,
        pause_before_answer=False,
        approval_before_answer=approval_before_answer or decision.requires_final_approval,
    ):
        event_type = str(evt.get("type", "") or "")
        if event_type == "thinking":
            thinking_count += 1
            if progress_cb is not None:
                agent_name = str(evt.get("agent_name", "") or "agent")
                await progress_cb(min(20 + thinking_count * 10, 85), f"{agent_name} 处理中...")
        elif event_type == "final_answer":
            final_answer = str(evt.get("answer", "") or "")
        elif event_type == "paused":
            paused_payload = evt
        elif event_type == "error":
            raise RuntimeError(str(evt.get("error", "") or "多 Agent 运行失败"))

        events.append(
            {
                "event_type": event_type,
                "agent_name": str(evt.get("agent_name", "") or ""),
                "message": str(evt.get("message", "") or ""),
                "result": str(evt.get("result", evt.get("answer", "")) or ""),
                "data": evt.get("data", {}) or {},
            }
        )

    state = await get_multi_agent_run_state(app, run_id)
    paused: dict[str, Any] | None = None
    if paused_payload is not None:
        paused = {
            "checkpoint_id": state.get("checkpoint_id", ""),
            "pause_reason": state.get("pause_reason", ""),
            "requires_approval": state.get("requires_approval", False),
            "next_nodes": state.get("next_nodes", []),
            "data": paused_payload.get("data", {}) or {},
            "answer_preview": str(paused_payload.get("answer_preview", "") or ""),
        }

    return {
        "engine_type": "multi_agent",
        "mode": route_input.mode,
        "scenario": decision.scenario,
        "multi_agent_mode": decision.multi_agent_mode,
        "route_reasons": list(decision.reasons),
        "complexity_score": decision.complexity_score,
        "answer": final_answer,
        "paused": paused,
        "agent_events": events,
        "shared_facts": state.get("shared_facts", []),
        "handoffs": state.get("handoffs", []),
        "customer_context": route_input.customer_context,
    }


def build_customer_service_multi_agent_context(
    *,
    message: str,
    mode: str,
    allow_write_actions: bool,
    customer_context: dict[str, Any],
    decision: CustomerServiceRouteDecision,
) -> str:
    base_prompt = build_customer_service_message(
        message,
        context=customer_context,
        mode=mode,
        allow_write_actions=allow_write_actions,
    )
    route_lines = [
        "## 编排提示",
        f"- 协作场景: {decision.scenario}",
        f"- 协作模式: {decision.multi_agent_mode}",
        f"- 分流原因: {', '.join(decision.reasons) or 'auto'}",
    ]
    return "\n".join([base_prompt, "", *route_lines])


def _build_specs(scenario: str) -> list[AgentSpec]:
    if scenario == "customer_complaint_review":
        return build_customer_complaint_review_agents()
    if scenario == "customer_complex_case":
        return build_customer_complex_case_agents()
    raise ValueError(f"不支持的客服 multi-agent 场景: {scenario}")


def _resolve_manager_name(specs: list[AgentSpec]) -> str:
    for spec in specs:
        if spec.role == AgentRole.MANAGER or spec.name == "manager":
            return spec.name
    return ""

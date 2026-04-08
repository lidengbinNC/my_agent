"""生产版多 Agent LangGraph 运行时。

核心原则：
  - 全局共享的是结构化 state，而不是完整推理轨迹
  - 每个 Agent 保留私有 workspace，只通过 handoff 暴露必要结论
  - 使用 thread_id + checkpoint 支持跨请求续接、暂停、恢复和历史回放
"""

from __future__ import annotations

import asyncio
from typing import Any

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from my_agent.core.dependencies import get_llm_client
from my_agent.core.engine.react_engine import ReActEngine, ReActStepType
from my_agent.domain.agent.skill import AgentSkill
from my_agent.domain.llm.message import Message, SystemMessage, UserMessage
from my_agent.domain.llm.structured_output import StructuredOutputError, get_structured_output
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.handoff import AgentHandoff, AgentWorkspaceSnapshot
from my_agent.domain.multi_agent.message import AgentRole
from my_agent.domain.tool.registry import get_registry
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class AssignmentItem(BaseModel):
    worker: str
    task: str


class AssignmentPlan(BaseModel):
    assignments: list[AssignmentItem] = Field(default_factory=list)


class SupervisorDecision(BaseModel):
    next_agent: str = Field(default="")
    task: str = Field(default="")
    reason: str = Field(default="")


class ReviewFeedbackItem(BaseModel):
    worker: str
    issue: str = ""
    instruction: str


class ReviewDecision(BaseModel):
    approved: bool = True
    feedback: list[ReviewFeedbackItem] = Field(default_factory=list)
    final_answer: str = ""


class MultiAgentGraphState(TypedDict):
    session_id: str
    run_id: str
    thread_id: str
    scenario: str
    mode: str
    goal: str
    global_context_summary: str
    agent_specs: list[dict[str, Any]]
    manager_name: str
    agent_order: list[str]
    assignments: dict[str, str]
    pending_agents: list[str]
    completed_agents: list[str]
    current_agent: str
    current_task: str
    supervisor_reason: str
    phase: str
    review_round: int
    shared_facts: list[str]
    handoffs: list[dict[str, Any]]
    pending_handoff: dict[str, Any]
    pending_batch_handoffs: list[dict[str, Any]]
    agent_outputs: dict[str, str]
    agent_workspaces: dict[str, dict[str, Any]]
    manager_review_result: dict[str, Any]
    pending_answer: str
    final_answer: str
    error: str
    pause_before_handoff: bool
    approval_before_handoff: bool
    pause_before_answer: bool
    approval_before_answer: bool
    handoff_gate_decision: str
    handoff_gate_feedback: str
    final_gate_decision: str
    final_gate_feedback: str


_MANAGER_ASSIGN_PROMPT = """你是一个多 Agent 协调器。
可用 Agent:
{workers_desc}

用户目标:
{goal}

全局背景:
{context_summary}

请为每个必要的 Agent 分配具体任务，输出严格 JSON:
{{
  "assignments": [
    {{"worker": "agent_name", "task": "具体任务描述"}},
    ...
  ]
}}

要求：
1. 只使用给定 Agent 名称
2. task 要明确、可执行
3. 不需要使用全部 Agent
4. 只输出 JSON
"""

_MANAGER_REVIEW_PROMPT = """你是多 Agent 任务的最终审核者。

用户目标:
{goal}

共享背景:
{context_summary}

各 Agent 的 handoff:
{handoffs_text}

请审核是否需要返工。输出严格 JSON:
{{
  "approved": true,
  "feedback": [
    {{"worker": "agent_name", "issue": "问题描述", "instruction": "返工指令"}}
  ],
  "final_answer": "若可直接交付，则给出最终答案；否则留空"
}}

要求：
1. 只有明确缺陷时才要求返工
2. 最多给出必要的少量反馈
3. 只输出 JSON
"""

_SUPERVISOR_DECISION_PROMPT = """你是 LangGraph Supervisor 架构中的主管 Agent。

用户目标:
{goal}

共享背景:
{context_summary}

已完成的 Agent:
{completed_agents}

待执行的 Agent:
{pending_agents}

当前共享事实:
{shared_facts}

历史交接:
{handoffs_text}

可用 Worker 说明:
{workers_desc}

请决定下一位要执行的 Agent，或输出 FINISH 表示可以直接进入最终汇总。
输出严格 JSON:
{{
  "next_agent": "agent_name 或 FINISH",
  "task": "给该 Agent 的具体任务；若 next_agent=FINISH 可留空",
  "reason": "为什么选择该 Agent 或为什么可以结束"
}}

要求：
1. next_agent 必须是待执行 Agent 中的一个，或 FINISH
2. task 必须明确、可执行、聚焦该 Agent 的职责
3. 当证据已充分时才输出 FINISH
4. 只输出 JSON
"""

_HANDOFF_PROMPT = """你是多 Agent 系统中的交接整理器。

请根据以下输入，生成供其他 Agent 复用的结构化 handoff。

Agent 名称: {agent_name}
任务:
{task}

原始输出:
{output}

要求：
1. summary 控制在 120 字内
2. facts 只保留可复用事实，避免冗长复述
3. artifacts 写明阶段性产物或交付件
4. risks 只写真实风险，不要编造
5. next_recommendations 给下游 Agent 可执行建议
6. final_output 保留原始输出正文
"""


def serialize_agent_spec(spec: AgentSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "role": spec.role.value,
        "system_prompt": spec.system_prompt,
        "description": spec.description,
        "tools": list(spec.tools),
        "max_iterations": spec.max_iterations,
    }


def deserialize_agent_spec(payload: dict[str, Any]) -> AgentSpec:
    role_value = str(payload.get("role", AgentRole.WORKER.value) or AgentRole.WORKER.value)
    try:
        role = AgentRole(role_value)
    except ValueError:
        role = AgentRole.WORKER
    return AgentSpec(
        name=str(payload.get("name", "agent") or "agent"),
        role=role,
        system_prompt=str(payload.get("system_prompt", "") or ""),
        description=str(payload.get("description", "") or ""),
        tools=[str(item) for item in payload.get("tools", []) if str(item).strip()],
        max_iterations=int(payload.get("max_iterations", 6) or 6),
    )


def build_multi_agent_graph(*, checkpointer: Any | None = None) -> Any:
    graph = StateGraph(MultiAgentGraphState)
    graph.add_node("planner", planner_node)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("run_worker", run_worker_node)
    graph.add_node("run_parallel_workers", run_parallel_workers_node)
    graph.add_node("handoff_gate", handoff_gate_node)
    graph.add_node("apply_handoffs", apply_handoffs_node)
    graph.add_node("manager_review", manager_review_node)
    graph.add_node("finalize_prepare", finalize_prepare_node)
    graph.add_node("finalize_gate", finalize_gate_node)
    graph.add_node("finalize_commit", finalize_commit_node)

    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "supervisor": "supervisor",
            "run_worker": "run_worker",
            "run_parallel_workers": "run_parallel_workers",
            "finalize_prepare": "finalize_prepare",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "supervisor",
        route_after_supervisor,
        {
            "run_worker": "run_worker",
            "finalize_prepare": "finalize_prepare",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "run_worker",
        route_after_worker_run,
        {
            "handoff_gate": "handoff_gate",
            "apply_handoffs": "apply_handoffs",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "run_parallel_workers",
        route_after_parallel_run,
        {
            "handoff_gate": "handoff_gate",
            "apply_handoffs": "apply_handoffs",
            END: END,
        },
    )
    graph.add_edge("handoff_gate", "apply_handoffs")
    graph.add_conditional_edges(
        "apply_handoffs",
        route_after_apply_handoffs,
        {
            "run_worker": "run_worker",
            "run_parallel_workers": "run_parallel_workers",
            "manager_review": "manager_review",
            "finalize_prepare": "finalize_prepare",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "manager_review",
        route_after_manager_review,
        {
            "run_parallel_workers": "run_parallel_workers",
            "finalize_prepare": "finalize_prepare",
            END: END,
        },
    )
    graph.add_conditional_edges(
        "finalize_prepare",
        route_after_finalize_prepare,
        {
            "finalize_gate": "finalize_gate",
            "finalize_commit": "finalize_commit",
            END: END,
        },
    )
    graph.add_edge("finalize_gate", "finalize_commit")
    graph.add_edge("finalize_commit", END)
    return graph.compile(checkpointer=checkpointer, interrupt_before=["handoff_gate", "finalize_gate"])


async def stream_multi_agent_run(
    app: Any,
    *,
    session_id: str,
    run_id: str,
    thread_id: str,
    scenario: str,
    mode: str,
    goal: str,
    global_context_summary: str,
    agent_specs: list[dict[str, Any]],
    manager_name: str = "",
    pause_before_handoff: bool = False,
    approval_before_handoff: bool = False,
    pause_before_answer: bool = False,
    approval_before_answer: bool = False,
) -> Any:
    config = {"configurable": {"thread_id": thread_id}}
    existing_state = await app.aget_state(config)
    if existing_state and existing_state.values:
        initial_input = {
            "goal": goal,
            "global_context_summary": global_context_summary,
        }
    else:
        initial_input = build_initial_state(
            session_id=session_id,
            run_id=run_id,
            thread_id=thread_id,
            scenario=scenario,
            mode=mode,
            goal=goal,
            global_context_summary=global_context_summary,
            agent_specs=agent_specs,
            manager_name=manager_name,
            pause_before_handoff=pause_before_handoff,
            approval_before_handoff=approval_before_handoff,
            pause_before_answer=pause_before_answer,
            approval_before_answer=approval_before_answer,
        )
    async for event in _stream_graph(app, config=config, initial_input=initial_input):
        yield event


async def resume_multi_agent_run(
    app: Any,
    *,
    run_id: str,
    action: str = "resume",
    feedback: str = "",
) -> Any:
    config = {"configurable": {"thread_id": run_id}}
    state = await app.aget_state(config)
    if not state or not state.values:
        yield {"type": "error", "error": f"run_id={run_id} 不存在"}
        return
    next_nodes = list(state.next)
    if not next_nodes:
        values = state.values
        if values.get("final_answer"):
            yield {"type": "final_answer", "answer": str(values.get("final_answer", "") or "")}
        else:
            yield {"type": "error", "error": f"run_id={run_id} 已完成，无需 resume"}
        return
    updates = build_resume_updates(next_nodes[0], action=action, feedback=feedback)
    if updates is None:
        yield {"type": "error", "error": f"当前断点 {next_nodes[0]} 不支持 resume"}
        return
    await app.aupdate_state(config=config, values=updates)
    async for event in _stream_graph(app, config=config, initial_input=None):
        yield event


async def get_multi_agent_run_state(app: Any, run_id: str) -> dict[str, Any]:
    config = {"configurable": {"thread_id": run_id}}
    state = await app.aget_state(config)
    if not state or not state.values:
        return {"run_id": run_id, "status": "not_found"}
    values = state.values
    next_nodes = list(state.next)
    checkpoint_id = state.config.get("configurable", {}).get("checkpoint_id", "")
    pause_reason = derive_pause_reason(values, next_nodes[0]) if next_nodes else ""
    return {
        "run_id": run_id,
        "thread_id": str(values.get("thread_id", "") or run_id),
        "session_id": str(values.get("session_id", "") or ""),
        "status": derive_status(values, next_nodes),
        "scenario": str(values.get("scenario", "") or ""),
        "mode": str(values.get("mode", "") or ""),
        "checkpoint_id": checkpoint_id,
        "next_nodes": next_nodes,
        "current_node": next_nodes[0] if next_nodes else "",
        "current_agent": str(values.get("current_agent", "") or ""),
        "phase": str(values.get("phase", "") or ""),
        "pause_reason": pause_reason,
        "requires_approval": requires_approval(values, next_nodes[0]) if next_nodes else False,
        "goal": str(values.get("goal", "") or ""),
        "shared_facts": values.get("shared_facts", []) or [],
        "handoffs": values.get("handoffs", []) or [],
        "pending_answer": str(values.get("pending_answer", "") or ""),
        "final_answer": str(values.get("final_answer", "") or ""),
        "error": str(values.get("error", "") or ""),
    }


async def get_multi_agent_run_history(app: Any, run_id: str) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    config = {"configurable": {"thread_id": run_id}}
    async for snapshot in app.aget_state_history(config):
        values = snapshot.values
        history.append(
            {
                "checkpoint_id": snapshot.config.get("configurable", {}).get("checkpoint_id", ""),
                "created_at": getattr(snapshot, "created_at", None),
                "next": list(snapshot.next),
                "status": derive_status(values, list(snapshot.next)),
                "state": {
                    "phase": str(values.get("phase", "") or ""),
                    "current_agent": str(values.get("current_agent", "") or ""),
                    "completed_agents": list(values.get("completed_agents", []) or []),
                    "shared_facts": list(values.get("shared_facts", []) or []),
                    "handoff_count": len(values.get("handoffs", []) or []),
                    "final_answer": str(values.get("final_answer", "") or ""),
                    "error": str(values.get("error", "") or ""),
                },
                "metadata": snapshot.metadata,
            }
        )
    return history


def build_initial_state(
    *,
    session_id: str,
    run_id: str,
    thread_id: str,
    scenario: str,
    mode: str,
    goal: str,
    global_context_summary: str,
    agent_specs: list[dict[str, Any]],
    manager_name: str,
    pause_before_handoff: bool,
    approval_before_handoff: bool,
    pause_before_answer: bool,
    approval_before_answer: bool,
) -> MultiAgentGraphState:
    return {
        "session_id": session_id,
        "run_id": run_id,
        "thread_id": thread_id,
        "scenario": scenario,
        "mode": mode,
        "goal": goal,
        "global_context_summary": global_context_summary,
        "agent_specs": agent_specs,
        "manager_name": manager_name,
        "agent_order": [],
        "assignments": {},
        "pending_agents": [],
        "completed_agents": [],
        "current_agent": "",
        "current_task": "",
        "supervisor_reason": "",
        "phase": "planning",
        "review_round": 0,
        "shared_facts": [],
        "handoffs": [],
        "pending_handoff": {},
        "pending_batch_handoffs": [],
        "agent_outputs": {},
        "agent_workspaces": {},
        "manager_review_result": {},
        "pending_answer": "",
        "final_answer": "",
        "error": "",
        "pause_before_handoff": pause_before_handoff,
        "approval_before_handoff": approval_before_handoff,
        "pause_before_answer": pause_before_answer,
        "approval_before_answer": approval_before_answer,
        "handoff_gate_decision": "auto",
        "handoff_gate_feedback": "",
        "final_gate_decision": "auto",
        "final_gate_feedback": "",
    }


async def planner_node(state: MultiAgentGraphState) -> dict[str, Any]:
    specs = _specs_from_state(state)
    if not specs:
        return {"error": "未提供可用 Agent 规格"}
    mode = str(state.get("mode", "sequential") or "sequential").lower()
    non_manager_specs = [spec for spec in specs if spec.name != state.get("manager_name")]
    if mode == "hierarchical":
        assignments = await _manager_assign(state, specs)
        if not assignments:
            assignments = {
                spec.name: _build_parallel_task(state, spec)
                for spec in non_manager_specs
            }
        return {
            "assignments": assignments,
            "pending_agents": list(assignments.keys()),
            "phase": "workers",
            "manager_name": _resolve_manager_name(specs),
        }
    if mode == "supervisor":
        order = [spec.name for spec in non_manager_specs]
        return {
            "agent_order": order,
            "pending_agents": order,
            "phase": "supervising",
            "manager_name": _resolve_manager_name(specs),
        }
    if mode == "parallel":
        assignments = {spec.name: _build_parallel_task(state, spec) for spec in specs}
        return {
            "assignments": assignments,
            "pending_agents": list(assignments.keys()),
            "phase": "workers",
        }
    order = [spec.name for spec in specs]
    return {
        "agent_order": order,
        "pending_agents": order,
        "phase": "workers",
    }


async def supervisor_node(state: MultiAgentGraphState) -> dict[str, Any]:
    specs = _specs_from_state(state)
    manager_name = _resolve_manager_name(specs)
    pending_agents = list(state.get("pending_agents", []) or [])
    if not pending_agents:
        return {
            "current_agent": "",
            "current_task": "",
            "supervisor_reason": "所有待执行 Agent 已完成，进入最终汇总。",
            "phase": "supervisor_done",
        }

    decision = await _supervisor_decide(state, manager_name)
    next_agent = decision.next_agent.strip()
    if not next_agent or next_agent.upper() == "FINISH":
        return {
            "current_agent": "",
            "current_task": "",
            "pending_agents": [],
            "supervisor_reason": decision.reason or "Supervisor 判断现有结果已足够交付。",
            "phase": "supervisor_done",
        }

    if next_agent not in pending_agents:
        next_agent = pending_agents[0]

    spec = _spec_by_name(state, next_agent)
    if spec is None:
        return {"error": f"Supervisor 选择的 Agent '{next_agent}' 不存在"}
    task = decision.task.strip() or _build_supervisor_task(state, spec)
    assignments = dict(state.get("assignments", {}) or {})
    assignments[next_agent] = task
    return {
        "current_agent": next_agent,
        "current_task": task,
        "assignments": assignments,
        "supervisor_reason": decision.reason or f"Supervisor 选择 {next_agent} 执行下一步。",
        "phase": "workers",
    }


async def run_worker_node(state: MultiAgentGraphState) -> dict[str, Any]:
    pending_agents = list(state.get("pending_agents", []) or [])
    if not pending_agents:
        return {}
    current_agent = str(state.get("current_agent", "") or "")
    if current_agent not in pending_agents:
        current_agent = pending_agents[0]
    spec = _spec_by_name(state, current_agent)
    if spec is None:
        return {"error": f"Agent '{current_agent}' 不存在"}
    task = (
        str(state.get("current_task", "") or "")
        or state.get("assignments", {}).get(current_agent)
        or _build_sequential_task(state, spec)
    )
    result = await _execute_agent(spec, task, state)
    pending_workspace = dict(state.get("agent_workspaces", {}) or {})
    pending_workspace[current_agent] = result["workspace"]
    gate_decision = (
        "pending"
        if state.get("pause_before_handoff") or state.get("approval_before_handoff")
        else "auto"
    )
    return {
        "current_agent": current_agent,
        "current_task": task,
        "pending_handoff": result["handoff"],
        "agent_workspaces": pending_workspace,
        "handoff_gate_decision": gate_decision,
        "handoff_gate_feedback": "",
    }


async def run_parallel_workers_node(state: MultiAgentGraphState) -> dict[str, Any]:
    pending_agents = list(state.get("pending_agents", []) or [])
    if not pending_agents:
        return {}
    tasks: list[Any] = []
    specs: list[AgentSpec] = []
    for name in pending_agents:
        spec = _spec_by_name(state, name)
        if spec is None:
            return {"error": f"Agent '{name}' 不存在"}
        specs.append(spec)
        task = state.get("assignments", {}).get(name) or _build_parallel_task(state, spec)
        tasks.append(_execute_agent(spec, task, state))
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    batch_handoffs: list[dict[str, Any]] = []
    updated_workspaces = dict(state.get("agent_workspaces", {}) or {})
    for spec, item in zip(specs, raw_results):
        if isinstance(item, Exception):
            handoff = AgentHandoff(
                agent_name=spec.name,
                task_id=f"{state['run_id']}:{spec.name}",
                task=state.get("assignments", {}).get(spec.name, ""),
                summary=f"{spec.name} 执行失败",
                facts=[],
                artifacts=[],
                risks=[str(item)],
                next_recommendations=[],
                final_output=f"[异常] {item}",
            )
            workspace = AgentWorkspaceSnapshot(
                agent_name=spec.name,
                task=handoff.task,
                context_digest=_build_context_digest(state),
                final_output=handoff.final_output,
                handoff_summary=handoff.summary,
            )
        else:
            handoff = AgentHandoff.model_validate(item["handoff"])
            workspace = AgentWorkspaceSnapshot.model_validate(item["workspace"])
        batch_handoffs.append(handoff.model_dump(mode="json"))
        updated_workspaces[spec.name] = workspace.model_dump(mode="json")
    gate_decision = (
        "pending"
        if state.get("pause_before_handoff") or state.get("approval_before_handoff")
        else "auto"
    )
    phase = str(state.get("phase", "workers") or "workers")
    if phase == "workers":
        phase = "workers_done"
    elif phase == "revision":
        phase = "revision_done"
    return {
        "pending_batch_handoffs": batch_handoffs,
        "agent_workspaces": updated_workspaces,
        "handoff_gate_decision": gate_decision,
        "handoff_gate_feedback": "",
        "phase": phase,
    }


async def handoff_gate_node(state: MultiAgentGraphState) -> dict[str, Any]:
    return {
        "phase": str(state.get("phase", "") or ""),
    }


async def apply_handoffs_node(state: MultiAgentGraphState) -> dict[str, Any]:
    if state.get("handoff_gate_decision") == "rejected":
        feedback = str(state.get("handoff_gate_feedback", "") or "")
        suffix = f" 反馈: {feedback}" if feedback else ""
        return {"error": f"handoff 未获批准。{suffix}".strip()}

    pending_single = dict(state.get("pending_handoff", {}) or {})
    pending_batch = list(state.get("pending_batch_handoffs", []) or [])
    pending_items = []
    if pending_single:
        pending_items.append(pending_single)
    pending_items.extend(item for item in pending_batch if item)
    if not pending_items:
        return {}

    handoffs = list(state.get("handoffs", []) or [])
    shared_facts = list(state.get("shared_facts", []) or [])
    completed_agents = list(state.get("completed_agents", []) or [])
    outputs = dict(state.get("agent_outputs", {}) or {})
    pending_agents = list(state.get("pending_agents", []) or [])

    for payload in pending_items:
        handoff = AgentHandoff.model_validate(payload)
        handoffs.append(handoff.model_dump(mode="json"))
        for fact in handoff.facts:
            if fact and fact not in shared_facts:
                shared_facts.append(fact)
        outputs[handoff.agent_name] = handoff.final_output
        if handoff.agent_name in pending_agents:
            pending_agents.remove(handoff.agent_name)
        if handoff.agent_name not in completed_agents:
            completed_agents.append(handoff.agent_name)

    return {
        "handoffs": handoffs,
        "shared_facts": shared_facts,
        "agent_outputs": outputs,
        "completed_agents": completed_agents,
        "pending_agents": pending_agents,
        "pending_handoff": {},
        "pending_batch_handoffs": [],
        "current_agent": "",
        "current_task": "",
        "handoff_gate_decision": "auto",
        "handoff_gate_feedback": "",
    }


async def manager_review_node(state: MultiAgentGraphState) -> dict[str, Any]:
    specs = _specs_from_state(state)
    manager_name = _resolve_manager_name(specs)
    if not manager_name:
        return {}
    review = await _manager_review(state, manager_name)
    if review.approved or not review.feedback or int(state.get("review_round", 0) or 0) >= 1:
        final_answer = review.final_answer or await _synthesize_final_answer(state)
        return {
            "manager_review_result": review.model_dump(mode="json"),
            "pending_answer": final_answer,
            "phase": "finalize",
        }

    assignments: dict[str, str] = {}
    for fb in review.feedback:
        previous_output = str(state.get("agent_outputs", {}).get(fb.worker, "") or "")
        assignments[fb.worker] = (
            f"原始目标: {state['goal']}\n\n"
            f"该 Agent 上一版输出:\n{previous_output}\n\n"
            f"审核发现的问题: {fb.issue}\n"
            f"返工指令: {fb.instruction}\n\n"
            "请基于以上反馈给出修订后的结果，并聚焦问题本身。"
        )

    return {
        "manager_review_result": review.model_dump(mode="json"),
        "assignments": assignments,
        "pending_agents": list(assignments.keys()),
        "review_round": int(state.get("review_round", 0) or 0) + 1,
        "phase": "revision",
    }


async def finalize_prepare_node(state: MultiAgentGraphState) -> dict[str, Any]:
    pending_answer = str(state.get("pending_answer", "") or "")
    if not pending_answer:
        pending_answer = await _synthesize_final_answer(state)
    gate_decision = (
        "pending"
        if state.get("pause_before_answer") or state.get("approval_before_answer")
        else "auto"
    )
    return {
        "pending_answer": pending_answer,
        "final_gate_decision": gate_decision,
        "final_gate_feedback": "",
        "phase": "finalize",
    }


async def finalize_gate_node(state: MultiAgentGraphState) -> dict[str, Any]:
    return {
        "phase": "finalize",
    }


async def finalize_commit_node(state: MultiAgentGraphState) -> dict[str, Any]:
    if state.get("final_gate_decision") == "rejected":
        feedback = str(state.get("final_gate_feedback", "") or "")
        suffix = f" 反馈: {feedback}" if feedback else ""
        return {"error": f"最终答案未获批准。{suffix}".strip()}
    pending_answer = str(state.get("pending_answer", "") or "")
    if not pending_answer:
        return {"error": "最终答案为空，无法提交"}
    return {
        "final_answer": pending_answer,
        "pending_answer": "",
        "phase": "completed",
    }


def route_after_planner(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    mode = str(state.get("mode", "sequential") or "sequential").lower()
    if not list(state.get("pending_agents", []) or []):
        return "finalize_prepare"
    if mode == "supervisor":
        return "supervisor"
    if mode in {"parallel", "hierarchical"}:
        return "run_parallel_workers"
    return "run_worker"


def route_after_supervisor(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    if state.get("current_agent"):
        return "run_worker"
    return "finalize_prepare"


def route_after_worker_run(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    if dict(state.get("pending_handoff", {}) or {}):
        if state.get("handoff_gate_decision") == "pending":
            return "handoff_gate"
        return "apply_handoffs"
    return END


def route_after_parallel_run(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    if list(state.get("pending_batch_handoffs", []) or []):
        if state.get("handoff_gate_decision") == "pending":
            return "handoff_gate"
        return "apply_handoffs"
    return END


def route_after_apply_handoffs(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    mode = str(state.get("mode", "sequential") or "sequential").lower()
    pending_agents = list(state.get("pending_agents", []) or [])
    phase = str(state.get("phase", "") or "")
    if mode == "sequential":
        if pending_agents:
            return "run_worker"
        return "finalize_prepare"
    if mode == "parallel":
        return "finalize_prepare"
    if mode == "hierarchical":
        if pending_agents:
            return "run_parallel_workers"
        if phase in {"workers_done", "revision_done", "workers", "revision"}:
            return "manager_review"
        return "finalize_prepare"
    if mode == "supervisor":
        if pending_agents:
            return "supervisor"
        return "finalize_prepare"
    return "finalize_prepare"


def route_after_manager_review(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    if str(state.get("phase", "") or "") == "revision" and list(state.get("pending_agents", []) or []):
        return "run_parallel_workers"
    return "finalize_prepare"


def route_after_finalize_prepare(state: MultiAgentGraphState) -> str:
    if state.get("error"):
        return END
    if not state.get("pending_answer"):
        return END
    if state.get("final_gate_decision") == "pending":
        return "finalize_gate"
    return "finalize_commit"


def derive_status(values: dict[str, Any], next_nodes: list[str]) -> str:
    if values.get("error"):
        return "error"
    if next_nodes:
        if derive_pause_reason(values, next_nodes[0]):
            return "paused"
        return "running"
    if values.get("final_answer"):
        return "completed"
    return "idle"


def derive_pause_reason(values: dict[str, Any], next_node: str) -> str:
    if next_node == "handoff_gate" and values.get("handoff_gate_decision") == "pending":
        if values.get("approval_before_handoff"):
            return "approval_before_handoff"
        if values.get("pause_before_handoff"):
            return "pause_before_handoff"
    if next_node == "finalize_gate" and values.get("final_gate_decision") == "pending":
        if values.get("approval_before_answer"):
            return "approval_before_answer"
        if values.get("pause_before_answer"):
            return "pause_before_answer"
    return ""


def requires_approval(values: dict[str, Any], next_node: str) -> bool:
    if next_node == "handoff_gate":
        return bool(values.get("approval_before_handoff", False))
    if next_node == "finalize_gate":
        return bool(values.get("approval_before_answer", False))
    return False


def build_resume_updates(next_node: str, *, action: str, feedback: str) -> dict[str, Any] | None:
    normalized = action.lower().strip()
    approved = normalized in {"resume", "approve", "approved", "continue"}
    rejected = normalized in {"reject", "rejected", "cancel", "abort"}
    if not approved and not rejected:
        approved = True
    if next_node == "handoff_gate":
        return {
            "handoff_gate_decision": "approved" if approved else "rejected",
            "handoff_gate_feedback": feedback,
        }
    if next_node == "finalize_gate":
        return {
            "final_gate_decision": "approved" if approved else "rejected",
            "final_gate_feedback": feedback,
        }
    return None


async def _stream_graph(
    app: Any,
    *,
    config: dict[str, Any],
    initial_input: dict[str, Any] | None,
) -> Any:
    current_input = initial_input
    while True:
        async for event in app.astream(current_input, config=config, stream_mode="updates"):
            for node_name, node_output in event.items():
                if not isinstance(node_output, dict):
                    continue
                for translated in _translate_event(node_name, node_output):
                    yield translated
        state = await app.aget_state(config)
        if not state or not state.values:
            return
        next_nodes = list(state.next)
        if not next_nodes:
            values = state.values
            if values.get("final_answer"):
                yield {
                    "type": "final_answer",
                    "answer": str(values.get("final_answer", "") or ""),
                    "data": {
                        "shared_facts": values.get("shared_facts", []) or [],
                        "handoffs": values.get("handoffs", []) or [],
                    },
                }
            return
        pause_event = _build_pause_event(state)
        if pause_event is not None:
            yield pause_event
            return
        current_input = None


def _translate_event(node_name: str, node_output: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if node_name == "planner":
        events.append(
            {
                "type": "thinking",
                "message": "多 Agent 计划已生成，开始进入协作流程。",
                "data": {
                    "pending_agents": node_output.get("pending_agents", []),
                    "mode": node_output.get("mode", ""),
                },
            }
        )
    elif node_name == "supervisor":
        current_agent = str(node_output.get("current_agent", "") or "")
        reason = str(node_output.get("supervisor_reason", "") or "")
        if current_agent:
            events.append(
                {
                    "type": "thinking",
                    "message": f"Supervisor 选择 {current_agent} 执行下一步。",
                    "data": {
                        "agent_name": current_agent,
                        "task": str(node_output.get("current_task", "") or ""),
                        "reason": reason,
                    },
                }
            )
        elif reason:
            events.append(
                {
                    "type": "thinking",
                    "message": reason,
                    "data": {"reason": reason},
                }
            )
    elif node_name == "run_worker" and node_output.get("pending_handoff"):
        handoff = AgentHandoff.model_validate(node_output["pending_handoff"])
        events.append(
            {
                "type": "agent_done",
                "agent_name": handoff.agent_name,
                "result": handoff.final_output,
                "message": handoff.summary,
                "data": {"handoff": handoff.model_dump(mode="json")},
            }
        )
    elif node_name == "run_parallel_workers" and node_output.get("pending_batch_handoffs"):
        for payload in node_output.get("pending_batch_handoffs", []):
            handoff = AgentHandoff.model_validate(payload)
            events.append(
                {
                    "type": "agent_done",
                    "agent_name": handoff.agent_name,
                    "result": handoff.final_output,
                    "message": handoff.summary,
                    "data": {"handoff": handoff.model_dump(mode="json")},
                }
            )
    elif node_name == "apply_handoffs" and node_output.get("handoffs"):
        latest = node_output.get("handoffs", [])[-1]
        if latest:
            handoff = AgentHandoff.model_validate(latest)
            events.append(
                {
                    "type": "message",
                    "agent_name": handoff.agent_name,
                    "message": f"[{handoff.agent_name}] handoff 已合入共享上下文",
                    "result": handoff.summary,
                    "data": {"facts": handoff.facts},
                }
            )
    elif node_name == "manager_review" and node_output.get("manager_review_result"):
        review = node_output["manager_review_result"]
        approved = bool(review.get("approved", True))
        events.append(
            {
                "type": "thinking",
                "message": "Manager 审核完成，准备进入最终汇总。" if approved else "Manager 要求部分 Agent 返工。",
                "data": review,
            }
        )
    elif node_name == "finalize_prepare" and node_output.get("pending_answer"):
        events.append(
            {
                "type": "thinking",
                "message": "正在汇总共享事实和各 Agent handoff...",
            }
        )
    elif node_output.get("error"):
        events.append({"type": "error", "error": str(node_output["error"])})
    return events


def _build_pause_event(state: Any) -> dict[str, Any] | None:
    next_nodes = list(state.next)
    if not next_nodes:
        return None
    node = next_nodes[0]
    values = state.values
    checkpoint_id = state.config.get("configurable", {}).get("checkpoint_id", "")
    if node == "handoff_gate" and values.get("handoff_gate_decision") == "pending":
        pending_single = dict(values.get("pending_handoff", {}) or {})
        pending_batch = list(values.get("pending_batch_handoffs", []) or [])
        preview = pending_single or (pending_batch[0] if pending_batch else {})
        return {
            "type": "paused",
            "checkpoint_id": checkpoint_id,
            "pause_reason": derive_pause_reason(values, node),
            "requires_approval": requires_approval(values, node),
            "agent_name": str(preview.get("agent_name", values.get("current_agent", "")) or ""),
            "message": "等待 handoff 审批后继续合入共享上下文。",
            "data": {
                "node": node,
                "next_nodes": next_nodes,
                "pending_handoff": preview,
            },
        }
    if node == "finalize_gate" and values.get("final_gate_decision") == "pending":
        return {
            "type": "paused",
            "checkpoint_id": checkpoint_id,
            "pause_reason": derive_pause_reason(values, node),
            "requires_approval": requires_approval(values, node),
            "message": "等待最终答案审批后继续交付。",
            "answer_preview": str(values.get("pending_answer", "") or ""),
            "data": {
                "node": node,
                "next_nodes": next_nodes,
            },
        }
    return None


def _specs_from_state(state: MultiAgentGraphState) -> list[AgentSpec]:
    return [deserialize_agent_spec(item) for item in state.get("agent_specs", []) or []]


def _spec_by_name(state: MultiAgentGraphState, name: str) -> AgentSpec | None:
    for spec in _specs_from_state(state):
        if spec.name == name:
            return spec
    return None


def _resolve_manager_name(specs: list[AgentSpec]) -> str:
    for spec in specs:
        if spec.role == AgentRole.MANAGER or spec.name == "manager":
            return spec.name
    return ""


def _build_parallel_task(state: MultiAgentGraphState, spec: AgentSpec) -> str:
    return (
        f"用户目标: {state['goal']}\n\n"
        f"共享背景:\n{state.get('global_context_summary', '')}\n\n"
        f"你的角色: {spec.role_description()}\n"
        "请只完成你负责的部分，并产出可供下游复用的结论。"
    )


def _build_sequential_task(state: MultiAgentGraphState, spec: AgentSpec) -> str:
    latest_handoffs = state.get("handoffs", []) or []
    latest_text = ""
    if latest_handoffs:
        latest = AgentHandoff.model_validate(latest_handoffs[-1])
        latest_text = (
            f"上一个 Agent（{latest.agent_name}）的交接总结:\n{latest.summary}\n\n"
            f"上一个 Agent 的事实:\n" + "\n".join(f"- {fact}" for fact in latest.facts[:6])
        )
    return (
        f"用户目标: {state['goal']}\n\n"
        f"共享背景:\n{state.get('global_context_summary', '')}\n\n"
        f"{latest_text}\n\n"
        f"你的角色: {spec.role_description()}\n"
        "请基于已有上下文继续推进任务，并交付可供下游复用的结果。"
    ).strip()


def _build_context_digest(state: MultiAgentGraphState) -> str:
    parts: list[str] = []
    summary = str(state.get("global_context_summary", "") or "")
    if summary:
        parts.append(summary[:500])
    facts = list(state.get("shared_facts", []) or [])
    if facts:
        parts.append("\n".join(f"- {fact}" for fact in facts[:8]))
    return "\n\n".join(parts)


def _build_supervisor_task(state: MultiAgentGraphState, spec: AgentSpec) -> str:
    shared_facts = list(state.get("shared_facts", []) or [])
    fact_text = "\n".join(f"- {fact}" for fact in shared_facts[:8]) or "- 暂无共享事实"
    return (
        f"用户目标: {state['goal']}\n\n"
        f"共享背景:\n{state.get('global_context_summary', '')}\n\n"
        f"当前共享事实:\n{fact_text}\n\n"
        f"你的角色: {spec.role_description()}\n"
        "请聚焦你负责的部分，输出供 Supervisor 复核的结构化结果。"
    )


def _build_agent_history(state: MultiAgentGraphState, spec: AgentSpec, task: str) -> list[Message]:
    history: list[Message] = []
    if state.get("global_context_summary"):
        history.append(UserMessage(f"[会话背景]\n{state['global_context_summary']}"))
    shared_facts = list(state.get("shared_facts", []) or [])
    if shared_facts:
        facts_text = "\n".join(f"- {fact}" for fact in shared_facts[:12])
        history.append(UserMessage(f"[共享事实]\n{facts_text}"))
    handoffs = list(state.get("handoffs", []) or [])
    if handoffs:
        lines: list[str] = []
        for payload in handoffs[-3:]:
            handoff = AgentHandoff.model_validate(payload)
            lines.append(f"[{handoff.agent_name}] {handoff.summary}")
        history.append(UserMessage("[历史交接]\n" + "\n".join(lines)))
    workspaces = dict(state.get("agent_workspaces", {}) or {})
    if spec.name in workspaces:
        snapshot = AgentWorkspaceSnapshot.model_validate(workspaces[spec.name])
        if snapshot.final_output:
            history.append(UserMessage(f"[你上一次的结果]\n{snapshot.final_output[:1200]}"))
    history.append(UserMessage(f"[当前子任务]\n{task}"))
    return history


async def _supervisor_decide(state: MultiAgentGraphState, manager_name: str) -> SupervisorDecision:
    specs = _specs_from_state(state)
    available = [spec for spec in specs if spec.name in set(state.get("pending_agents", []) or [])]
    if not available:
        return SupervisorDecision(next_agent="FINISH", task="", reason="没有待执行的 Agent。")

    workers_desc = "\n".join(f"- {spec.name}: {spec.role_description()}" for spec in available)
    shared_facts = "\n".join(f"- {fact}" for fact in (state.get("shared_facts", []) or [])[:10]) or "- 暂无"
    handoffs_text = "\n\n".join(
        f"【{AgentHandoff.model_validate(payload).agent_name}】 {AgentHandoff.model_validate(payload).summary}"
        for payload in (state.get("handoffs", []) or [])[-4:]
    ) or "（暂无）"
    llm = get_llm_client()
    try:
        decision = await get_structured_output(
            llm,
            [
                SystemMessage(
                    f"你是 {manager_name or 'supervisor'}，负责按照 LangGraph Supervisor 模式协调 Worker。"
                ),
                UserMessage(
                    _SUPERVISOR_DECISION_PROMPT.format(
                        goal=state["goal"],
                        context_summary=state.get("global_context_summary", ""),
                        completed_agents=", ".join(state.get("completed_agents", []) or []) or "无",
                        pending_agents=", ".join(state.get("pending_agents", []) or []) or "无",
                        shared_facts=shared_facts,
                        handoffs_text=handoffs_text,
                        workers_desc=workers_desc,
                    )
                ),
            ],
            SupervisorDecision,
            max_retries=2,
            initial_temperature=0.2,
        )
        valid_names = {spec.name for spec in available}
        if decision.next_agent.strip().upper() == "FINISH":
            return decision
        if decision.next_agent not in valid_names:
            fallback_spec = available[0]
            return SupervisorDecision(
                next_agent=fallback_spec.name,
                task=decision.task or _build_supervisor_task(state, fallback_spec),
                reason=f"Supervisor 输出无效，回退到 {fallback_spec.name}。",
            )
        return decision
    except (StructuredOutputError, Exception) as exc:
        logger.warning("multi_agent_supervisor_decide_fallback", error=str(exc))
        fallback_spec = available[0]
        return SupervisorDecision(
            next_agent=fallback_spec.name,
            task=_build_supervisor_task(state, fallback_spec),
            reason=f"Supervisor 决策失败，回退到 {fallback_spec.name}。",
        )


async def _execute_agent(spec: AgentSpec, task: str, state: MultiAgentGraphState) -> dict[str, Any]:
    llm = get_llm_client()
    engine = ReActEngine(
        llm=llm,
        tool_registry=get_registry(),
        max_iterations=spec.max_iterations,
    )
    skill = AgentSkill(
        name=f"multi_agent.{spec.name}",
        description=spec.role_description(),
        system_instructions=spec.system_prompt,
        allowed_tools=list(spec.tools),
    )
    final_output = f"[Agent {spec.name} 未返回答案]"
    async for step in engine.run(task, history=_build_agent_history(state, spec, task), skill=skill):
        if step.type == ReActStepType.FINAL_ANSWER:
            final_output = step.answer
        elif step.type == ReActStepType.ERROR:
            final_output = f"[错误] {step.error}"
            break
    handoff = await _build_handoff(spec, task, final_output)
    workspace = AgentWorkspaceSnapshot(
        agent_name=spec.name,
        task=task,
        context_digest=_build_context_digest(state),
        final_output=final_output,
        handoff_summary=handoff.summary,
    )
    return {
        "handoff": handoff.model_dump(mode="json"),
        "workspace": workspace.model_dump(mode="json"),
    }


async def _build_handoff(spec: AgentSpec, task: str, output: str) -> AgentHandoff:
    llm = get_llm_client()
    try:
        result = await get_structured_output(
            llm,
            [
                SystemMessage("你负责为多 Agent 系统抽取结构化交接结果。"),
                UserMessage(
                    _HANDOFF_PROMPT.format(
                        agent_name=spec.name,
                        task=task,
                        output=output,
                    )
                ),
            ],
            AgentHandoff,
            max_retries=2,
            initial_temperature=0.2,
        )
        return result.model_copy(
            update={
                "agent_name": spec.name,
                "task_id": result.task_id or f"{spec.name}_handoff",
                "task": result.task or task,
                "final_output": result.final_output or output,
            }
        )
    except (StructuredOutputError, Exception) as exc:
        logger.warning("multi_agent_handoff_fallback", agent=spec.name, error=str(exc))
        return AgentHandoff(
            agent_name=spec.name,
            task_id=f"{spec.name}_handoff",
            task=task,
            summary=output[:120],
            facts=[line.strip("- ").strip() for line in output.splitlines()[:5] if line.strip()],
            artifacts=[],
            risks=[],
            next_recommendations=[],
            final_output=output,
        )


async def _manager_assign(state: MultiAgentGraphState, specs: list[AgentSpec]) -> dict[str, str]:
    manager_name = _resolve_manager_name(specs)
    workers = [spec for spec in specs if spec.name != manager_name]
    if not workers:
        return {}
    workers_desc = "\n".join(f"- {spec.name}: {spec.role_description()}" for spec in workers)
    llm = get_llm_client()
    try:
        plan = await get_structured_output(
            llm,
            [
                SystemMessage("你是项目经理，擅长做多 Agent 任务分配。"),
                UserMessage(
                    _MANAGER_ASSIGN_PROMPT.format(
                        workers_desc=workers_desc,
                        goal=state["goal"],
                        context_summary=state.get("global_context_summary", ""),
                    )
                ),
            ],
            AssignmentPlan,
            max_retries=2,
            initial_temperature=0.2,
        )
        available = {spec.name for spec in workers}
        return {
            item.worker: item.task
            for item in plan.assignments
            if item.worker in available and item.task.strip()
        }
    except (StructuredOutputError, Exception) as exc:
        logger.warning("multi_agent_manager_assign_fallback", error=str(exc))
        return {}


async def _manager_review(state: MultiAgentGraphState, manager_name: str) -> ReviewDecision:
    handoff_lines: list[str] = []
    for payload in state.get("handoffs", []) or []:
        handoff = AgentHandoff.model_validate(payload)
        fact_lines = "\n".join(f"- {fact}" for fact in handoff.facts[:5])
        handoff_lines.append(
            f"【{handoff.agent_name}】\n"
            f"summary: {handoff.summary}\n"
            f"facts:\n{fact_lines or '- 无'}\n"
            f"output:\n{handoff.final_output[:1200]}"
        )
    llm = get_llm_client()
    try:
        return await get_structured_output(
            llm,
            [
                SystemMessage(f"你是 {manager_name}，负责审核多 Agent 最终交付质量。"),
                UserMessage(
                    _MANAGER_REVIEW_PROMPT.format(
                        goal=state["goal"],
                        context_summary=state.get("global_context_summary", ""),
                        handoffs_text="\n\n".join(handoff_lines) or "（暂无）",
                    )
                ),
            ],
            ReviewDecision,
            max_retries=2,
            initial_temperature=0.2,
        )
    except (StructuredOutputError, Exception) as exc:
        logger.warning("multi_agent_manager_review_fallback", error=str(exc))
        return ReviewDecision(
            approved=True,
            feedback=[],
            final_answer="",
        )


async def _synthesize_final_answer(state: MultiAgentGraphState) -> str:
    llm = get_llm_client()
    handoff_text = []
    for payload in state.get("handoffs", []) or []:
        handoff = AgentHandoff.model_validate(payload)
        handoff_text.append(f"【{handoff.agent_name}】\n{handoff.final_output}")
    shared_facts_text = "\n".join(f"- {fact}" for fact in state.get("shared_facts", [])[:20])
    handoff_summary = "\n\n".join(handoff_text) or "（暂无）"
    resp = await llm.chat(
        [
            SystemMessage("你是一个擅长综合多 Agent 结论的助手。请用中文给出最终可交付答案。"),
            UserMessage(
                f"用户目标: {state['goal']}\n\n"
                f"共享背景:\n{state.get('global_context_summary', '')}\n\n"
                f"共享事实:\n{shared_facts_text}\n\n"
                f"各 Agent 结果:\n{handoff_summary}"
            ),
        ],
        temperature=0.3,
    )
    return resp.content or "（未生成最终答案）"

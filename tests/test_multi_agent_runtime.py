from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph_impl.multi_agent_runtime import (
    build_multi_agent_graph,
    build_initial_state,
    build_resume_updates,
    derive_pause_reason,
    deserialize_agent_spec,
    route_after_apply_handoffs,
    route_after_planner,
    serialize_agent_spec,
)
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentRole


def test_agent_spec_roundtrip_preserves_runtime_fields():
    spec = AgentSpec(
        name="researcher",
        role=AgentRole.RESEARCHER,
        system_prompt="你是研究员",
        description="负责收集资料",
        tools=["web_search", "http_request"],
        max_iterations=7,
    )

    payload = serialize_agent_spec(spec)
    restored = deserialize_agent_spec(payload)

    assert restored.name == "researcher"
    assert restored.role == AgentRole.RESEARCHER
    assert restored.system_prompt == "你是研究员"
    assert restored.description == "负责收集资料"
    assert restored.tools == ["web_search", "http_request"]
    assert restored.max_iterations == 7


def test_build_initial_state_records_gate_flags_and_ids():
    state = build_initial_state(
        session_id="session-1",
        run_id="run-1",
        thread_id="thread-1",
        scenario="custom",
        mode="sequential",
        goal="生成报告",
        global_context_summary="已有历史",
        agent_specs=[],
        manager_name="manager",
        pause_before_handoff=True,
        approval_before_handoff=False,
        pause_before_answer=False,
        approval_before_answer=True,
    )

    assert state["session_id"] == "session-1"
    assert state["run_id"] == "run-1"
    assert state["thread_id"] == "thread-1"
    assert state["pause_before_handoff"] is True
    assert state["approval_before_answer"] is True
    assert state["phase"] == "planning"
    assert state["handoff_gate_decision"] == "auto"
    assert state["final_gate_decision"] == "auto"


def test_resume_updates_and_pause_reason_follow_gate_node():
    values = {
        "handoff_gate_decision": "pending",
        "approval_before_handoff": True,
        "pause_before_handoff": False,
        "final_gate_decision": "pending",
        "approval_before_answer": False,
        "pause_before_answer": True,
    }

    assert derive_pause_reason(values, "handoff_gate") == "approval_before_handoff"
    assert derive_pause_reason(values, "finalize_gate") == "pause_before_answer"
    assert build_resume_updates("handoff_gate", action="approve", feedback="ok") == {
        "handoff_gate_decision": "approved",
        "handoff_gate_feedback": "ok",
    }
    assert build_resume_updates("finalize_gate", action="reject", feedback="retry") == {
        "final_gate_decision": "rejected",
        "final_gate_feedback": "retry",
    }


def test_build_multi_agent_graph_compiles_without_state_key_conflicts():
    app = build_multi_agent_graph()

    assert app is not None


def test_supervisor_mode_routes_through_supervisor_node():
    planner_state = {
        "error": "",
        "mode": "supervisor",
        "pending_agents": ["fact_agent", "policy_agent"],
    }
    apply_state = {
        "error": "",
        "mode": "supervisor",
        "pending_agents": ["policy_agent"],
        "phase": "workers",
    }

    assert route_after_planner(planner_state) == "supervisor"
    assert route_after_apply_handoffs(apply_state) == "supervisor"

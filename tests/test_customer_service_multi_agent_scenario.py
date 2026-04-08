from my_agent.core.multi_agent.scenarios.customer_service import (
    build_customer_complaint_review_agents,
    build_customer_complex_case_agents,
)
from my_agent.domain.multi_agent.message import AgentRole


def test_build_customer_complaint_review_agents_has_expected_roles_and_tools():
    agents = build_customer_complaint_review_agents()
    names = [agent.name for agent in agents]

    assert names == ["manager", "fact_agent", "policy_agent", "resolution_agent"]
    assert agents[0].role == AgentRole.MANAGER
    assert agents[1].tools == [
        "customer_profile_tool",
        "order_query_tool",
        "logistics_query_tool",
        "refund_status_tool",
        "session_history_tool",
    ]
    assert agents[2].tools == ["knowledge_search_tool"]
    assert agents[3].tools == []


def test_build_customer_complex_case_agents_returns_sequential_worker_chain():
    agents = build_customer_complex_case_agents()

    assert [agent.name for agent in agents] == ["investigator", "policy_checker", "ticket_drafter"]
    assert all(agent.role == AgentRole.WORKER for agent in agents)
    assert "session_history_tool" in agents[0].tools
    assert agents[1].tools == ["knowledge_search_tool"]
    assert agents[2].tools == []

from my_agent.domain.customer_service.routing import (
    build_customer_service_route_input,
    decide_customer_service_execution,
)


def test_complaint_review_forces_multi_agent():
    route_input = build_customer_service_route_input(
        message="客户要求赔偿并升级投诉。",
        mode="complaint_review",
        customer_context={"customer_id": "CUST-001", "order_id": "ORD-1001"},
    )

    decision = decide_customer_service_execution(route_input)

    assert decision.engine_type == "multi_agent"
    assert decision.scenario == "customer_complaint_review"
    assert decision.multi_agent_mode == "supervisor"
    assert decision.requires_final_approval is True


def test_simple_logistics_request_stays_single_agent():
    route_input = build_customer_service_route_input(
        message="帮我查询一下这个订单的物流状态。",
        mode="copilot",
        customer_context={"order_id": "ORD-1001"},
    )

    decision = decide_customer_service_execution(route_input)

    assert decision.engine_type == "single_agent"
    assert decision.scenario == ""


def test_complex_case_routes_to_multi_agent_when_score_high_enough():
    route_input = build_customer_service_route_input(
        message="客户多次联系，订单延迟且要求退款，请先判断责任再给升级建单建议。",
        mode="after_sales",
        allow_write_actions=True,
        customer_context={
            "customer_id": "CUST-001",
            "order_id": "ORD-1001",
            "ticket_id": "TICKET-9",
            "session_id": "SESSION-1",
            "knowledge_domain": "policy",
            "tags": ["repeat_contact"],
        },
    )

    decision = decide_customer_service_execution(route_input)

    assert decision.engine_type == "multi_agent"
    assert decision.scenario == "customer_complex_case"
    assert decision.multi_agent_mode == "sequential"
    assert decision.complexity_score >= 4


def test_execution_strategy_can_force_single_or_multi_agent():
    single_input = build_customer_service_route_input(
        message="客户要求赔偿并升级投诉。",
        mode="complaint_review",
        execution_strategy="single_agent",
    )
    multi_input = build_customer_service_route_input(
        message="帮我看下这个复杂售后案件。",
        mode="copilot",
        execution_strategy="multi_agent",
        multi_agent_scenario="customer_complex_case",
    )

    single_decision = decide_customer_service_execution(single_input)
    multi_decision = decide_customer_service_execution(multi_input)

    assert single_decision.engine_type == "single_agent"
    assert multi_decision.engine_type == "multi_agent"
    assert multi_decision.scenario == "customer_complex_case"

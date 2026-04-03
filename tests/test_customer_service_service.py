from my_agent.domain.customer_service.service import (
    build_customer_service_message,
    default_approval_before_tools,
    resolve_skill_name,
)


def test_build_customer_service_message_contains_key_context():
    text = build_customer_service_message(
        "客户说包裹一直没到。",
        context={
            "customer_id": "CUST-001",
            "order_id": "ORD-1001",
            "knowledge_domain": "policy",
            "tags": ["delay", "vip"],
        },
        mode="after_sales",
        allow_write_actions=False,
    )

    assert "客户说包裹一直没到" in text
    assert "customer_id: CUST-001" in text
    assert "order_id: ORD-1001" in text
    assert "knowledge_domain: policy" in text
    assert "delay, vip" in text


def test_default_approval_before_tools_only_for_high_risk_modes():
    assert default_approval_before_tools("ticket_draft", False) is True
    assert default_approval_before_tools("complaint_review", False) is True
    assert default_approval_before_tools("copilot", False) is False
    assert default_approval_before_tools("after_sales", False) is False
    assert default_approval_before_tools("copilot", True) is True


def test_resolve_skill_name_prefers_explicit_value():
    assert resolve_skill_name("copilot", "ticket-assistant") == "ticket-assistant"
    assert resolve_skill_name("after_sales") == "after-sales-triage"

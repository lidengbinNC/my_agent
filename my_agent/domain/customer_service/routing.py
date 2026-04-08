"""客服 Copilot 的 single/multi-agent 分流规则。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

EngineType = Literal["single_agent", "multi_agent"]

_HIGH_RISK_TAGS = {
    "chargeback",
    "complaint",
    "escalated",
    "legal_risk",
    "media",
    "public_opinion",
    "vip_complaint",
}
_HIGH_RISK_COMPLAINT_KEYWORDS = (
    "投诉",
    "维权",
    "赔偿",
    "曝光",
    "媒体",
    "律师",
    "监管",
    "申诉",
    "舆情",
    "chargeback",
    "complaint",
)
_ESCALATION_KEYWORDS = (
    "升级",
    "转主管",
    "建单",
    "工单",
    "escalate",
    "supervisor",
)
_COMPENSATION_KEYWORDS = (
    "赔付",
    "赔偿",
    "补偿",
    "退款",
    "refund",
    "compensation",
)
_LOGISTICS_KEYWORDS = ("物流", "快递", "包裹", "没到", "延迟", "tracking", "shipment")
_REFUND_KEYWORDS = ("退款", "退货", "退款进度", "refund", "return")
_KNOWLEDGE_KEYWORDS = ("政策", "规则", "sop", "faq", "知识库", "流程")
_SIMPLE_REQUEST_KEYWORDS = (
    "进度",
    "状态",
    "什么时候",
    "在哪",
    "怎么查",
    "查询",
    "物流",
    "退款状态",
    "faq",
)


@dataclass(frozen=True)
class CustomerServiceRouteInput:
    message: str
    mode: str = "copilot"
    allow_write_actions: bool = False
    customer_context: dict[str, Any] = field(default_factory=dict)
    execution_strategy: str = "auto"
    multi_agent_scenario: str = ""


@dataclass(frozen=True)
class CustomerServiceRouteDecision:
    engine_type: EngineType
    scenario: str = ""
    multi_agent_mode: str = "sequential"
    complexity_score: int = 0
    requires_final_approval: bool = False
    reasons: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "engine_type": self.engine_type,
            "route_reasons": list(self.reasons),
            "complexity_score": self.complexity_score,
            "multi_agent_scenario": self.scenario,
            "multi_agent_mode": self.multi_agent_mode,
            "requires_final_approval": self.requires_final_approval,
        }


def build_customer_service_route_input(
    *,
    message: str,
    mode: str = "copilot",
    allow_write_actions: bool = False,
    customer_context: dict[str, Any] | None = None,
    execution_strategy: str = "auto",
    multi_agent_scenario: str = "",
) -> CustomerServiceRouteInput:
    return CustomerServiceRouteInput(
        message=message,
        mode=mode,
        allow_write_actions=allow_write_actions,
        customer_context=customer_context or {},
        execution_strategy=execution_strategy,
        multi_agent_scenario=multi_agent_scenario,
    )


def decide_customer_service_execution(route_input: CustomerServiceRouteInput) -> CustomerServiceRouteDecision:
    strategy = route_input.execution_strategy.strip().lower()
    if strategy == "single_agent":
        return _single_agent("forced_single_agent")
    if strategy == "multi_agent":
        return _multi_agent_forced(route_input)

    if route_input.mode == "complaint_review":
        return _multi_agent(
            scenario="customer_complaint_review",
            mode="supervisor",
            score=max(_score_complex_case(route_input), 4),
            requires_final_approval=True,
            reasons=("mode=complaint_review",),
        )

    if is_high_risk_complaint(route_input):
        return _multi_agent(
            scenario="customer_complaint_review",
            mode="supervisor",
            score=max(_score_complex_case(route_input), 4),
            requires_final_approval=True,
            reasons=("high_risk_complaint",),
        )

    if is_simple_request(route_input):
        return _single_agent("simple_request")

    score, score_reasons = _score_complex_case(route_input, include_reasons=True)
    if score >= 4:
        return _multi_agent(
            scenario="customer_complex_case",
            mode="sequential",
            score=score,
            requires_final_approval=False,
            reasons=tuple(score_reasons),
        )
    return _single_agent(f"score={score}")


def is_high_risk_complaint(route_input: CustomerServiceRouteInput) -> bool:
    tags = _normalized_tags(route_input.customer_context)
    if _HIGH_RISK_TAGS.intersection(tags):
        return True
    metadata_text = _metadata_text(route_input.customer_context)
    message = route_input.message.lower()
    return any(keyword.lower() in message or keyword.lower() in metadata_text for keyword in _HIGH_RISK_COMPLAINT_KEYWORDS)


def count_case_domains(route_input: CustomerServiceRouteInput) -> int:
    ctx = route_input.customer_context
    message = route_input.message.lower()
    metadata_text = _metadata_text(ctx)
    tags = _normalized_tags(ctx)
    domains: set[str] = set()

    if ctx.get("customer_id") or ctx.get("customer_tier"):
        domains.add("customer")
    if ctx.get("order_id") or "订单" in message or "order" in message:
        domains.add("order")
    if any(keyword.lower() in message or keyword.lower() in metadata_text for keyword in _LOGISTICS_KEYWORDS):
        domains.add("logistics")
    if any(keyword.lower() in message or keyword.lower() in metadata_text for keyword in _REFUND_KEYWORDS):
        domains.add("refund")
    if ctx.get("session_id") or "历史" in message or "repeat_contact" in tags:
        domains.add("session")
    if ctx.get("knowledge_domain") or ctx.get("knowledge_base") or any(
        keyword.lower() in message or keyword.lower() in metadata_text for keyword in _KNOWLEDGE_KEYWORDS
    ):
        domains.add("knowledge")
    if ctx.get("ticket_id") or any(keyword.lower() in message for keyword in _ESCALATION_KEYWORDS):
        domains.add("ticket")
    return len(domains)


def is_simple_request(route_input: CustomerServiceRouteInput) -> bool:
    if route_input.allow_write_actions:
        return False
    if route_input.mode in {"ticket_draft", "complaint_review"}:
        return False
    if is_high_risk_complaint(route_input):
        return False
    if has_resolution_decision_intent(route_input.message):
        return False
    if has_repeat_or_escalation_signal(route_input):
        return False
    if count_case_domains(route_input) > 2:
        return False
    message = route_input.message.lower()
    return any(keyword.lower() in message for keyword in _SIMPLE_REQUEST_KEYWORDS)


def has_resolution_decision_intent(message: str) -> bool:
    normalized = message.lower()
    return any(
        keyword in normalized
        for keyword in (
            "判断",
            "定责",
            "建议",
            "怎么处理",
            "如何处理",
            "处理方案",
            "是否升级",
            "是否建单",
            "怎么赔",
            "next step",
        )
    )


def has_repeat_or_escalation_signal(route_input: CustomerServiceRouteInput) -> bool:
    tags = _normalized_tags(route_input.customer_context)
    metadata_text = _metadata_text(route_input.customer_context)
    message = route_input.message.lower()
    if {"repeat_contact", "cross_channel", "unresolved", "escalated"} & tags:
        return True
    return any(
        keyword in message or keyword in metadata_text
        for keyword in (
            "再次",
            "一直没解决",
            "重复联系",
            "转主管",
            "升级",
            "cross_channel",
            "unresolved",
        )
    )


def has_compensation_refund_complaint_combo(route_input: CustomerServiceRouteInput) -> bool:
    message = route_input.message.lower()
    hit_count = 0
    if any(keyword.lower() in message for keyword in _COMPENSATION_KEYWORDS):
        hit_count += 1
    if any(keyword.lower() in message for keyword in _HIGH_RISK_COMPLAINT_KEYWORDS):
        hit_count += 1
    if any(keyword.lower() in message for keyword in _ESCALATION_KEYWORDS):
        hit_count += 1
    return hit_count >= 2


def _score_complex_case(
    route_input: CustomerServiceRouteInput,
    *,
    include_reasons: bool = False,
) -> int | tuple[int, list[str]]:
    ctx = route_input.customer_context
    reasons: list[str] = []
    score = 0

    if route_input.allow_write_actions:
        score += 1
        reasons.append("allow_write_actions")
    if ctx.get("ticket_id") or any(keyword in route_input.message.lower() for keyword in _ESCALATION_KEYWORDS):
        score += 1
        reasons.append("ticket_or_escalation")
    domain_count = count_case_domains(route_input)
    if domain_count >= 3:
        score += 2
        reasons.append(f"domains={domain_count}")
    if has_resolution_decision_intent(route_input.message):
        score += 1
        reasons.append("decision_intent")
    if has_repeat_or_escalation_signal(route_input):
        score += 1
        reasons.append("repeat_or_escalation")
    if has_compensation_refund_complaint_combo(route_input):
        score += 1
        reasons.append("compensation_refund_complaint")

    if include_reasons:
        return score, reasons
    return score


def _multi_agent_forced(route_input: CustomerServiceRouteInput) -> CustomerServiceRouteDecision:
    scenario = route_input.multi_agent_scenario.strip() or (
        "customer_complaint_review" if route_input.mode == "complaint_review" else "customer_complex_case"
    )
    mode = "supervisor" if scenario == "customer_complaint_review" else "sequential"
    return _multi_agent(
        scenario=scenario,
        mode=mode,
        score=max(_score_complex_case(route_input), 1),
        requires_final_approval=scenario == "customer_complaint_review",
        reasons=("forced_multi_agent",),
    )


def _single_agent(reason: str) -> CustomerServiceRouteDecision:
    return CustomerServiceRouteDecision(engine_type="single_agent", reasons=(reason,))


def _multi_agent(
    *,
    scenario: str,
    mode: str,
    score: int,
    requires_final_approval: bool,
    reasons: tuple[str, ...],
) -> CustomerServiceRouteDecision:
    return CustomerServiceRouteDecision(
        engine_type="multi_agent",
        scenario=scenario,
        multi_agent_mode=mode,
        complexity_score=score,
        requires_final_approval=requires_final_approval,
        reasons=reasons,
    )


def _normalized_tags(customer_context: dict[str, Any]) -> set[str]:
    tags = customer_context.get("tags") or []
    return {str(tag).strip().lower() for tag in tags if str(tag).strip()}


def _metadata_text(customer_context: dict[str, Any]) -> str:
    metadata = customer_context.get("metadata") or {}
    return " ".join(f"{key}:{value}".lower() for key, value in metadata.items())

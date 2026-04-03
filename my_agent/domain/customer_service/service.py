"""客服 Copilot 上下文拼装与模式解析。"""

from __future__ import annotations

from typing import Any

from my_agent.domain.customer_service.contracts import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES

MODE_TO_SKILL = {
    "read_only": "customer-service-copilot",
    "copilot": "customer-service-copilot",
    "ticket_draft": "ticket-assistant",
    "complaint_review": "complaint-review",
    "after_sales": "after-sales-triage",
    "pre_sales": "pre-sales-consulting",
}


def resolve_skill_name(mode: str, explicit_skill: str | None = None) -> str | None:
    if explicit_skill:
        return explicit_skill
    return MODE_TO_SKILL.get((mode or "copilot").strip().lower())


def build_customer_service_message(
    message: str,
    *,
    context: dict[str, Any] | None = None,
    mode: str = "copilot",
    allow_write_actions: bool = False,
) -> str:
    ctx = context or {}
    lines = [
        "你当前处于海外客服融合系统的 AI 编排层。",
        "请基于客服场景输出可执行、可审计、可给坐席使用的建议。",
        f"当前执行模式: {mode}",
        f"允许写操作: {'是' if allow_write_actions else '否'}",
        "如果证据不足，优先调用知识/订单/物流/会话历史等只读工具补足信息。",
        "如果写操作未被允许，不要创建或修改工单，只生成建议稿。",
        "",
        "## 客服上下文",
    ]
    for key in [
        "customer_id",
        "customer_tier",
        "channel",
        "locale",
        "order_id",
        "ticket_id",
        "knowledge_domain",
        "knowledge_base",
        "session_id",
    ]:
        value = ctx.get(key)
        if value:
            lines.append(f"- {key}: {value}")
    tags = ctx.get("tags") or []
    if tags:
        lines.append(f"- tags: {', '.join(str(tag) for tag in tags)}")
    metadata = ctx.get("metadata") or {}
    if metadata:
        lines.append(f"- metadata: {metadata}")
    lines.extend(
        [
            "",
            "## 结果要求",
            "1. 先给出判断与建议。",
            "2. 如果引用知识，请说明证据来源。",
            "3. 如果建议建单，请给出标题、分类、优先级、摘要、需补充信息。",
            "4. 对投诉、退款、补偿等高风险动作，明确标记需要人工审批。",
            "",
            "## 用户问题",
            message,
        ]
    )
    return "\n".join(lines)


def default_approval_before_tools(mode: str, allow_write_actions: bool) -> bool:
    normalized = (mode or "copilot").strip().lower()
    return allow_write_actions or normalized in {"ticket_draft", "complaint_review"}


def allowed_tools_for_mode(mode: str, allow_write_actions: bool) -> list[str]:
    if allow_write_actions:
        return [*READ_ONLY_TOOL_NAMES, *WRITE_TOOL_NAMES]
    return list(READ_ONLY_TOOL_NAMES)

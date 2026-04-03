"""客服融合场景工具集。"""

from __future__ import annotations

import json
from typing import Any

import httpx
from sqlalchemy import select

from my_agent.config.settings import settings
from my_agent.domain.tool.base import ToolResult
from my_agent.domain.tool.registry import tool
from my_agent.infrastructure.db.database import AsyncSessionLocal
from my_agent.infrastructure.db.models import MessageModel

_MOCK_CUSTOMERS = {
    "CUST-001": {
        "customer_id": "CUST-001",
        "name": "Alice",
        "tier": "vip",
        "locale": "en-US",
        "risk_level": "medium",
        "recent_orders": ["ORD-1001"],
        "summary": "高价值用户，最近一次咨询与物流延迟相关。",
    }
}

_MOCK_ORDERS = {
    "ORD-1001": {
        "order_id": "ORD-1001",
        "status": "delayed",
        "amount": 249.0,
        "currency": "USD",
        "items": ["Bluetooth headset"],
        "shipping_status": "customs_hold",
    }
}

_MOCK_LOGISTICS = {
    "ORD-1001": {
        "tracking_no": "TRK-9981",
        "carrier": "DHL",
        "status": "in_transit",
        "latest_event": "Package delayed at customs checkpoint",
    }
}

_MOCK_REFUNDS = {
    "ORD-1001": {
        "refund_id": "REF-3001",
        "status": "not_requested",
        "eligible": True,
        "policy_hint": "签收前可申请取消；延误需人工确认补偿策略。",
    }
}

_MOCK_TICKETS: dict[str, dict[str, Any]] = {}


def _render_payload(title: str, payload: Any) -> str:
    return f"{title}\n{json.dumps(payload, ensure_ascii=False, indent=2)}"


async def _request_backend(
    path: str,
    *,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    base_url = settings.customer_service_api_base_url.strip().rstrip("/")
    if settings.customer_service_mock_mode or not base_url:
        return None

    headers = {}
    if settings.customer_service_api_key:
        headers["X-Api-Key"] = settings.customer_service_api_key

    async with httpx.AsyncClient(timeout=settings.customer_service_timeout_seconds) as client:
        response = await client.request(
            method=method,
            url=f"{base_url}{path}",
            headers=headers,
            json=json_body,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@tool(description="查询客户画像、等级、风险和近期订单信息")
async def customer_profile_tool(customer_id: str) -> ToolResult:
    backend = await _request_backend(f"/customers/{customer_id}")
    payload = backend or _MOCK_CUSTOMERS.get(customer_id) or {
        "customer_id": customer_id,
        "name": "Unknown",
        "tier": "standard",
        "risk_level": "unknown",
        "summary": "未命中真实客户中心，返回本地兜底画像。",
    }
    return ToolResult.ok(_render_payload("客户画像查询结果", payload), source="customer_profile")


@tool(description="查询订单状态、金额、商品和履约信息")
async def order_query_tool(order_id: str, include_items: bool = True) -> ToolResult:
    backend = await _request_backend(f"/orders/{order_id}", params={"include_items": include_items})
    payload = backend or _MOCK_ORDERS.get(order_id) or {
        "order_id": order_id,
        "status": "unknown",
        "summary": "未命中真实订单中心，返回本地兜底结果。",
    }
    if not include_items and isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if k != "items"}
    return ToolResult.ok(_render_payload("订单查询结果", payload), source="order_query")


@tool(description="查询物流轨迹、承运商和最新事件")
async def logistics_query_tool(order_id: str = "", tracking_no: str = "") -> ToolResult:
    key = order_id or tracking_no
    backend = await _request_backend(
        "/logistics/query",
        method="POST",
        json_body={"order_id": order_id, "tracking_no": tracking_no},
    )
    payload = backend or _MOCK_LOGISTICS.get(key) or {
        "order_id": order_id,
        "tracking_no": tracking_no,
        "status": "unknown",
        "summary": "未命中真实物流系统，返回本地兜底结果。",
    }
    return ToolResult.ok(_render_payload("物流查询结果", payload), source="logistics_query")


@tool(description="查询退款状态、资格和政策提示")
async def refund_status_tool(order_id: str = "", refund_id: str = "") -> ToolResult:
    key = order_id or refund_id
    backend = await _request_backend(
        "/refunds/query",
        method="POST",
        json_body={"order_id": order_id, "refund_id": refund_id},
    )
    payload = backend or _MOCK_REFUNDS.get(key) or {
        "order_id": order_id,
        "refund_id": refund_id,
        "status": "unknown",
        "eligible": False,
        "summary": "未命中真实退款系统，返回本地兜底结果。",
    }
    return ToolResult.ok(_render_payload("退款查询结果", payload), source="refund_status")


@tool(description="读取客服会话历史，生成会话摘要、关键事实和最近消息")
async def session_history_tool(session_id: str, limit: int = 12) -> ToolResult:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(MessageModel)
            .where(MessageModel.session_id == session_id)
            .order_by(MessageModel.created_at.desc())
            .limit(limit)
        )
        rows = list(result.scalars().all())

    if not rows:
        payload = {
            "session_id": session_id,
            "message_count": 0,
            "summary": "未找到本地会话历史，可继续通过外部客服系统补充。",
        }
    else:
        ordered = list(reversed(rows))
        payload = {
            "session_id": session_id,
            "message_count": len(ordered),
            "recent_messages": [
                {"role": item.role, "content": (item.content or "")[:300]}
                for item in ordered
            ],
            "summary": "已返回最近会话内容，适合进一步做摘要、建单草稿和回复建议。",
        }
    return ToolResult.ok(_render_payload("会话历史查询结果", payload), source="session_history")


@tool(description="从 MyRAG 检索客服 FAQ、SOP、政策和工单知识")
async def knowledge_search_tool(
    question: str,
    top_k: int = 5,
    knowledge_domain: str = "",
    knowledge_base: str = "",
    agent_role: str = "agent",
) -> ToolResult:
    base_url = settings.customer_service_myrag_base_url.strip().rstrip("/")
    if not base_url:
        return ToolResult.fail("未配置 customer_service_myrag_base_url")

    payload = {
        "query": question,
        "top_k": top_k,
        "knowledge_base": knowledge_base or settings.customer_service_default_knowledge_base,
        "domain": knowledge_domain or settings.customer_service_default_knowledge_domain,
        "agent_role": agent_role,
    }
    headers = {}
    if settings.customer_service_myrag_api_key:
        headers["X-Api-Key"] = settings.customer_service_myrag_api_key

    async with httpx.AsyncClient(timeout=settings.customer_service_timeout_seconds) as client:
        response = await client.post(f"{base_url}/api/v1/search", json=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    results = data.get("results", [])
    compact = [
        {
            "content": item.get("content", ""),
            "score": item.get("score", 0.0),
            "source": item.get("source", ""),
            "metadata": item.get("metadata", {}),
        }
        for item in results
    ]
    return ToolResult.ok(
        _render_payload(
            "知识检索结果",
            {
                "knowledge_base_id": data.get("knowledge_base_id", ""),
                "knowledge_base_name": data.get("knowledge_base_name", ""),
                "domain": data.get("domain", knowledge_domain),
                "results": compact,
            },
        ),
        source="knowledge_search",
        result_count=len(compact),
    )


@tool(description="创建售后工单或投诉工单，默认生成草稿，写入前需要审批")
async def ticket_create_tool(
    session_id: str,
    customer_id: str,
    title: str,
    category: str,
    summary: str,
    priority: str = "normal",
    dry_run: bool = True,
) -> ToolResult:
    payload = {
        "session_id": session_id,
        "customer_id": customer_id,
        "title": title,
        "category": category,
        "summary": summary,
        "priority": priority,
    }
    if dry_run or not settings.customer_service_write_enabled:
        payload["status"] = "draft"
        payload["approval_required"] = True
        return ToolResult.ok(_render_payload("工单创建草稿", payload), source="ticket_create_draft")

    backend = await _request_backend("/tickets", method="POST", json_body=payload)
    ticket = backend or {"ticket_id": f"TICKET-{len(_MOCK_TICKETS) + 1:04d}", **payload, "status": "created"}
    _MOCK_TICKETS[str(ticket["ticket_id"])] = ticket
    return ToolResult.ok(_render_payload("工单创建结果", ticket), source="ticket_create")


@tool(description="更新工单状态、评论和标签，写入前需要审批")
async def ticket_update_tool(
    ticket_id: str,
    action: str,
    comment: str = "",
    dry_run: bool = True,
) -> ToolResult:
    payload = {
        "ticket_id": ticket_id,
        "action": action,
        "comment": comment,
    }
    if dry_run or not settings.customer_service_write_enabled:
        payload["status"] = "draft"
        payload["approval_required"] = True
        return ToolResult.ok(_render_payload("工单更新草稿", payload), source="ticket_update_draft")

    backend = await _request_backend(f"/tickets/{ticket_id}", method="POST", json_body=payload)
    ticket = backend or {"ticket_id": ticket_id, "status": "updated", **payload}
    _MOCK_TICKETS[ticket_id] = ticket
    return ToolResult.ok(_render_payload("工单更新结果", ticket), source="ticket_update")

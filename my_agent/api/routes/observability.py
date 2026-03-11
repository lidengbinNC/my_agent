"""可观测性 API — Token 成本统计 + 护栏状态 + LangFuse 配置检查。

端点:
  GET /api/v1/observability/cost           全局成本汇总
  GET /api/v1/observability/cost/session/{id}  按会话汇总
  GET /api/v1/observability/cost/recent    最近 N 条调用记录
  GET /api/v1/observability/budget         预算使用情况
  GET /api/v1/observability/langfuse       LangFuse 连接状态
  POST /api/v1/observability/guardrails/test  测试护栏（输入/输出检查）
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from my_agent.domain.guardrails.chain import (
    build_default_input_chain,
    build_default_output_chain,
)
from my_agent.utils.cost_tracker import get_cost_tracker
from my_agent.utils.langfuse_client import get_langfuse_client

router = APIRouter(prefix="/observability", tags=["observability"])


# ── 成本统计 ──────────────────────────────────────────────────────

@router.get("/cost")
async def get_global_cost() -> dict:
    """全局 Token 成本汇总。"""
    tracker = get_cost_tracker()
    return {
        "global": tracker.global_summary(),
        "message": "按模型估算，仅供参考，以平台账单为准",
    }


@router.get("/cost/session/{session_id}")
async def get_session_cost(session_id: str) -> dict:
    """按会话 Token 成本汇总。"""
    return get_cost_tracker().session_summary(session_id)


@router.get("/cost/recent")
async def get_recent_calls(n: int = 20) -> dict:
    """最近 N 条 LLM 调用记录。"""
    return {"records": get_cost_tracker().recent_records(n=min(n, 100))}


@router.get("/budget")
async def get_budget_status() -> dict:
    """预算使用情况。"""
    from my_agent.core.dependencies import get_token_budget
    budget = get_token_budget()
    return budget.summary()


# ── LangFuse ──────────────────────────────────────────────────────

@router.get("/langfuse")
async def get_langfuse_status() -> dict:
    """LangFuse 连接状态。"""
    client = get_langfuse_client()
    return {
        "enabled": client.enabled,
        "message": (
            "LangFuse 已连接，LLM 调用将自动上报 Trace"
            if client.enabled
            else "LangFuse 未配置，请设置 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY"
        ),
    }


# ── 护栏测试 ─────────────────────────────────────────────────────

class GuardTestRequest(BaseModel):
    content: str
    guard_type: str = "input"   # input / output


@router.post("/guardrails/test")
async def test_guardrail(body: GuardTestRequest) -> dict:
    """测试护栏规则（调试用）。"""
    if body.guard_type == "output":
        chain = build_default_output_chain()
    else:
        chain = build_default_input_chain()

    final_content, result = await chain.check(body.content)

    return {
        "action": result.action.value if result else "pass",
        "reason": result.reason if result else "",
        "original": body.content,
        "final": final_content,
        "modified": final_content != body.content,
    }

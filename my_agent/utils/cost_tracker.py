"""Token 成本追踪 + 预算管理。

面试考点:
  - 成本追踪：按模型单价（$/1K token）计算每次调用费用，
    支持按 session / agent / 全局三个维度统计
  - 预算管理：
      单次请求预算（max_tokens_per_request）
      日预算（daily_budget_usd）
      超限策略：WARN（警告继续）/ BLOCK（拒绝执行）
  - 模型定价（示例，实际以官方为准）：
      qwen-turbo:  input $0.0008/1K, output $0.002/1K
      qwen-plus:   input $0.004/1K,  output $0.012/1K
      gpt-4o:      input $0.005/1K,  output $0.015/1K
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# 模型定价表（USD / 1K tokens）
MODEL_PRICING: dict[str, dict[str, float]] = {
    # 通义千问
    "qwen-turbo":     {"input": 0.0008, "output": 0.002},
    "qwen-plus":      {"input": 0.004,  "output": 0.012},
    "qwen-max":       {"input": 0.04,   "output": 0.12},
    # OpenAI
    "gpt-3.5-turbo":  {"input": 0.0005, "output": 0.0015},
    "gpt-4o":         {"input": 0.005,  "output": 0.015},
    "gpt-4o-mini":    {"input": 0.00015,"output": 0.0006},
    # 默认（未知模型）
    "default":        {"input": 0.001,  "output": 0.003},
}


def calc_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """计算单次调用费用（USD）。"""
    pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])
    return (prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]) / 1000.0


@dataclass
class UsageRecord:
    """单次 LLM 调用记录。"""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    session_id: str = ""
    agent_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class UsageSummary:
    """使用量汇总。"""

    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    call_count: int = 0

    def add(self, record: UsageRecord) -> None:
        self.total_prompt_tokens += record.prompt_tokens
        self.total_completion_tokens += record.completion_tokens
        self.total_tokens += record.prompt_tokens + record.completion_tokens
        self.total_cost_usd += record.cost_usd
        self.call_count += 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "call_count": self.call_count,
        }


class TokenBudget:
    """Token 预算管理器。

    支持：
      - 单次请求 token 上限
      - 日总费用预算（USD）
      - 超限时 WARN 或 BLOCK
    """

    def __init__(
        self,
        max_tokens_per_request: int = 4096,
        daily_budget_usd: float = 1.0,
        on_exceed: str = "warn",   # "warn" | "block"
    ) -> None:
        self.max_tokens_per_request = max_tokens_per_request
        self.daily_budget_usd = daily_budget_usd
        self.on_exceed = on_exceed
        self._daily_cost: float = 0.0
        self._day_start: float = time.time()

    def check_request(self, estimated_tokens: int) -> tuple[bool, str]:
        """检查单次请求是否超出 token 上限。

        Returns:
            (allowed, reason)
        """
        if estimated_tokens > self.max_tokens_per_request:
            reason = (
                f"请求预估 Token 数 {estimated_tokens} "
                f"超出单次上限 {self.max_tokens_per_request}"
            )
            if self.on_exceed == "block":
                return False, reason
            logger.warning("token_budget_warn", reason=reason)
        return True, ""

    def record_usage(self, cost_usd: float) -> tuple[bool, str]:
        """记录使用费用，检查日预算。

        Returns:
            (within_budget, reason)
        """
        self._reset_daily_if_needed()
        self._daily_cost += cost_usd

        if self._daily_cost > self.daily_budget_usd:
            reason = (
                f"日累计费用 ${self._daily_cost:.4f} "
                f"超出日预算 ${self.daily_budget_usd}"
            )
            if self.on_exceed == "block":
                logger.error("token_budget_exceeded", reason=reason)
                return False, reason
            logger.warning("token_budget_warn", reason=reason)

        return True, ""

    def _reset_daily_if_needed(self) -> None:
        if time.time() - self._day_start >= 86400:
            self._daily_cost = 0.0
            self._day_start = time.time()

    def summary(self) -> dict[str, Any]:
        return {
            "daily_cost_usd": round(self._daily_cost, 6),
            "daily_budget_usd": self.daily_budget_usd,
            "budget_remaining_usd": round(max(0.0, self.daily_budget_usd - self._daily_cost), 6),
            "max_tokens_per_request": self.max_tokens_per_request,
        }


class CostTracker:
    """全局成本追踪器（单例）。

    维度：全局 / 按 session / 按 agent
    """

    def __init__(self) -> None:
        self._global = UsageSummary()
        self._by_session: dict[str, UsageSummary] = defaultdict(UsageSummary)
        self._by_agent: dict[str, UsageSummary] = defaultdict(UsageSummary)
        self._records: list[UsageRecord] = []

    def record(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        session_id: str = "",
        agent_id: str = "",
    ) -> UsageRecord:
        cost = calc_cost(model, prompt_tokens, completion_tokens)
        rec = UsageRecord(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            session_id=session_id,
            agent_id=agent_id,
        )
        self._global.add(rec)
        if session_id:
            self._by_session[session_id].add(rec)
        if agent_id:
            self._by_agent[agent_id].add(rec)
        self._records.append(rec)

        logger.debug(
            "token_usage",
            model=model,
            prompt=prompt_tokens,
            completion=completion_tokens,
            cost_usd=round(cost, 6),
            session=session_id,
        )
        return rec

    def global_summary(self) -> dict[str, Any]:
        return self._global.to_dict()

    def session_summary(self, session_id: str) -> dict[str, Any]:
        return self._by_session[session_id].to_dict()

    def agent_summary(self, agent_id: str) -> dict[str, Any]:
        return self._by_agent[agent_id].to_dict()

    def recent_records(self, n: int = 20) -> list[dict[str, Any]]:
        return [
            {
                "model": r.model,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cost_usd": round(r.cost_usd, 6),
                "session_id": r.session_id,
                "agent_id": r.agent_id,
            }
            for r in self._records[-n:]
        ]


# 全局单例
_tracker = CostTracker()


def get_cost_tracker() -> CostTracker:
    return _tracker

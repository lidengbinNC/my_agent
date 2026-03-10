"""Replanner — 执行过程中动态调整计划。

面试考点:
  - Replanner 触发条件: 步骤失败数超阈值 / 执行结果与预期偏离
  - 动态调整 vs 静态计划的权衡: 灵活性 vs LLM 调用成本
  - 输入: 原始目标 + 已完成步骤摘要 + 失败步骤信息
  - 输出: 重新规划剩余步骤（保留已完成步骤，只替换未完成部分）
"""

from __future__ import annotations

import json

from my_agent.domain.agent.base import ExecutionPlan, PlanStep
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import SystemMessage, UserMessage
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

_REPLANNER_SYSTEM = """你是一个任务规划专家。当前执行计划的某些步骤失败了，
你需要根据已完成的进度和失败原因，重新规划剩余步骤。

输出格式（严格 JSON）:
{
  "revised_steps": [
    {
      "step_id": <新步骤ID，从当前最大ID+1开始>,
      "description": "修订后的步骤描述",
      "tool_hint": "建议工具",
      "depends_on": []
    }
  ],
  "reasoning": "为什么这样调整"
}

只输出 JSON，不要其他文字。"""


class Replanner:
    """动态重规划器：在执行失败时调整剩余计划。"""

    def __init__(self, llm: BaseLLMClient, failure_threshold: int = 1) -> None:
        """
        Args:
            llm: LLM 客户端
            failure_threshold: 允许的最大失败步骤数，超过则触发重规划
        """
        self._llm = llm
        self.failure_threshold = failure_threshold

    def should_replan(self, plan: ExecutionPlan) -> bool:
        """判断是否需要重新规划。"""
        return len(plan.failed_steps()) >= self.failure_threshold

    async def replan(self, plan: ExecutionPlan) -> ExecutionPlan:
        """基于当前执行状态重新规划剩余步骤。

        Returns:
            更新后的 ExecutionPlan（保留已完成步骤，替换剩余步骤）
        """
        completed = plan.completed_steps()
        failed = plan.failed_steps()
        pending = plan.pending_steps()

        completed_summary = "\n".join(
            f"  Step {s.step_id}: {s.description} → {s.result[:100]}" for s in completed
        )
        failed_summary = "\n".join(
            f"  Step {s.step_id}: {s.description} → 失败原因: {s.result[:100]}" for s in failed
        )
        pending_summary = "\n".join(
            f"  Step {s.step_id}: {s.description}" for s in pending
        )

        user_content = (
            f"原始目标: {plan.goal}\n\n"
            f"已完成步骤:\n{completed_summary or '无'}\n\n"
            f"失败步骤:\n{failed_summary}\n\n"
            f"尚未执行的步骤:\n{pending_summary or '无'}\n\n"
            "请重新规划，给出修订后的剩余步骤。"
        )

        logger.info("replanner_triggered", failed_count=len(failed), goal=plan.goal[:60])

        response = await self._llm.chat(
            [SystemMessage(_REPLANNER_SYSTEM), UserMessage(user_content)],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        raw = response.content or "{}"
        return self._apply_revision(plan, raw)

    def _apply_revision(self, plan: ExecutionPlan, raw: str) -> ExecutionPlan:
        """将修订结果合并回原计划。"""
        try:
            data = json.loads(raw)
        except Exception:
            logger.warning("replanner_parse_failed", raw=raw[:200])
            return plan

        new_steps_data = data.get("revised_steps", [])
        max_id = max((s.step_id for s in plan.steps), default=0)

        new_steps: list[PlanStep] = []
        for i, s in enumerate(new_steps_data):
            new_steps.append(
                PlanStep(
                    step_id=max_id + i + 1,
                    description=s.get("description", ""),
                    tool_hint=s.get("tool_hint", ""),
                    depends_on=[int(d) for d in s.get("depends_on", [])],
                    status="pending",
                )
            )

        # 保留已完成 / 失败的步骤，追加新步骤
        kept = [s for s in plan.steps if s.status in ("done", "failed")]
        plan.steps = kept + new_steps
        plan.total_steps = len(plan.steps)

        logger.info(
            "replanner_done",
            new_steps=len(new_steps),
            reasoning=data.get("reasoning", "")[:100],
        )
        return plan

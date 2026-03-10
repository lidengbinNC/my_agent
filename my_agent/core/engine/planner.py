"""Planner — LLM 生成结构化执行计划。

面试考点:
  - Plan-and-Execute vs ReAct 的区别:
      ReAct = 逐步推理，每步决策  ← 适合简单任务、工具调用少
      Plan-and-Execute = 先全局规划再执行 ← 适合复杂多步骤任务
  - 使用 Structured Output（JSON Mode）确保计划格式化输出
  - 计划步骤间可声明依赖（depends_on），为并行执行打基础
"""

from __future__ import annotations

import json

from my_agent.domain.agent.base import ExecutionPlan, PlanStep
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import SystemMessage, UserMessage
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

_PLANNER_SYSTEM = """你是一个任务规划专家。给定一个复杂任务目标，你需要将其拆解为若干可执行的子步骤。

输出格式必须是严格的 JSON，结构如下:
{{
  "goal": "任务总目标",
  "steps": [
    {{
      "step_id": 1,
      "description": "步骤描述（自然语言，说明要做什么）",
      "tool_hint": "建议使用的工具名称，如 calculator / web_search / code_executor，无工具填空字符串",
      "depends_on": []
    }},
    {{
      "step_id": 2,
      "description": "...",
      "tool_hint": "",
      "depends_on": [1]
    }}
  ]
}}

规则:
1. 步骤数量控制在 {max_steps} 步以内
2. 每步描述清晰、可独立执行
3. depends_on 表示当前步骤依赖哪些步骤的结果（填 step_id 列表）
4. 如果步骤间无依赖，设为空列表（方便并行执行）
5. 只输出 JSON，不要任何其他文字"""


class Planner:
    """任务规划器：调用 LLM 生成结构化执行计划。"""

    def __init__(self, llm: BaseLLMClient, max_steps: int = 8) -> None:
        self._llm = llm
        self._max_steps = max_steps

    async def plan(self, goal: str, context: str = "") -> ExecutionPlan:
        """为目标生成执行计划。

       Args:
            goal: 任务目标
            context: 额外背景信息（如对话历史摘要）

        Returns:
            ExecutionPlan 对象
        """
        try:
            system_prompt = _PLANNER_SYSTEM.format(max_steps=self._max_steps)
            logger.info("prompt_formatted", max_steps=self._max_steps, prompt_length=len(system_prompt))
        except Exception as e:
            logger.error("prompt_format_failed", error=str(e), max_steps=self._max_steps)
            raise
        user_content = f"任务目标：{goal}"
        if context:
            user_content += f"\n\n背景信息:\n{context}"

        messages = [
            SystemMessage(system_prompt),
            UserMessage(user_content),
        ]

        logger.info("planner_generating", goal=goal[:80])

        response = await self._llm.chat(
            messages,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        raw = response.content or "{}"
        plan = self._parse_plan(raw, goal)
        logger.info("planner_done", steps=plan.total_steps, goal=goal[:80])
        return plan

    def _parse_plan(self, raw: str, goal: str) -> ExecutionPlan:
        """解析 LLM 输出为 ExecutionPlan。"""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
            else:
                # 兜底：单步计划
                return ExecutionPlan(
                    goal=goal,
                    steps=[PlanStep(step_id=1, description=goal)],
                )

        steps = []
        for s in data.get("steps", []):
            steps.append(
                PlanStep(
                    step_id=int(s.get("step_id", len(steps) + 1)),
                    description=s.get("description", ""),
                    tool_hint=s.get("tool_hint", ""),
                    depends_on=[int(d) for d in s.get("depends_on", [])],
                )
            )

        return ExecutionPlan(
            goal=data.get("goal", goal),
            steps=steps,
        )

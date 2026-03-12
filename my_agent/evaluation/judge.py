"""LLM-as-Judge — 用 LLM 评估 Agent 回答质量。

面试考点:
  - LLM-as-Judge 的优势：
      比规则匹配更灵活（能理解语义等价）
      比人工评估更快（自动化）
      比 BLEU/ROUGE 更准确（理解上下文）
  - LLM-as-Judge 的局限：
      评分存在随机性（需要低 temperature）
      可能存在偏见（偏好更长/更自信的回答）
      成本：每次评估都消耗 Token
  - 评分维度：准确性(4分) + 完整性(3分) + 简洁性(2分) + 工具使用合理性(1分) = 10分
  - 结构化输出：JSON Mode 保证分数可解析
"""

from __future__ import annotations

import json

from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import SystemMessage, UserMessage
from my_agent.evaluation.models import EvalMetrics, EvalTask
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

_JUDGE_SYSTEM = """你是一个严格、公正的 AI Agent 评估专家。
你的任务是评估 Agent 对用户问题的回答质量。

评分标准（总分 10 分）：
- 准确性（0-4分）：回答是否正确、与参考答案是否一致
- 完整性（0-3分）：是否完整回答了问题，没有遗漏关键信息
- 简洁性（0-2分）：回答是否简洁，没有无关内容
- 工具使用（0-1分）：是否合理使用了工具（若有工具调用）

输出严格 JSON 格式：
{
  "score": <0-10 的整数或一位小数>,
  "accuracy": <0-4>,
  "completeness": <0-3>,
  "conciseness": <0-2>,
  "tool_usage": <0-1>,
  "reason": "简短评分理由（50字以内）",
  "task_completed": true/false
}

只输出 JSON，不要其他文字。"""

_JUDGE_USER_TEMPLATE = """用户问题：{question}

参考答案：{expected_answer}

Agent 实际回答：{actual_answer}

Agent 调用的工具：{tools_called}

请评分："""


class LLMJudge:
    """LLM-as-Judge 评估器。"""

    def __init__(self, llm: BaseLLMClient, temperature: float = 0.1) -> None:
        self._llm = llm
        self._temperature = temperature

    async def judge(
        self,
        task: EvalTask,
        actual_answer: str,
        tools_called: list[str] | None = None,
    ) -> EvalMetrics:
        """评估单次 Agent 执行结果，返回填充了 judge 字段的 EvalMetrics。"""
        tools_str = ", ".join(tools_called or []) or "无"
        user_content = _JUDGE_USER_TEMPLATE.format(
            question=task.question,
            expected_answer=task.expected_answer or "（无参考答案，请根据问题合理性评分）",
            actual_answer=actual_answer[:800],
            tools_called=tools_str,
        )

        metrics = EvalMetrics()
        try:
            response = await self._llm.chat(
                messages=[SystemMessage(_JUDGE_SYSTEM), UserMessage(user_content)],
                temperature=self._temperature,
                response_format={"type": "json_object"},
            )
            data = json.loads(response.content or "{}")

            metrics.judge_score = float(data.get("score", 0))
            metrics.task_completed = bool(data.get("task_completed", False))
            metrics.judge_reason = data.get("reason", "")

            logger.info(
                "llm_judge_scored",
                task_id=task.task_id,
                score=metrics.judge_score,
                completed=metrics.task_completed,
            )
        except Exception as e:
            logger.warning("llm_judge_failed", task_id=task.task_id, error=str(e))
            # 降级：关键词匹配
            metrics = self._fallback_judge(task, actual_answer)

        # 工具准确率
        if task.expected_tools:
            called_set = set(tools_called or [])
            expected_set = set(task.expected_tools)
            if expected_set:
                hit = len(called_set & expected_set)
                metrics.tool_accuracy = round(hit / len(expected_set), 3)
        else:
            metrics.tool_accuracy = 1.0  # 无工具要求，默认满分

        return metrics

    @staticmethod
    def _fallback_judge(task: EvalTask, actual_answer: str) -> EvalMetrics:
        """降级评估：关键词匹配（LLM 调用失败时使用）。"""
        metrics = EvalMetrics()
        if not task.expected_answer:
            metrics.judge_score = 5.0
            metrics.task_completed = bool(actual_answer and len(actual_answer) > 10)
            return metrics

        # 简单关键词重叠率
        expected_words = set(task.expected_answer.lower().split())
        actual_words = set(actual_answer.lower().split())
        if expected_words:
            overlap = len(expected_words & actual_words) / len(expected_words)
            metrics.judge_score = round(overlap * 10, 1)
            metrics.task_completed = overlap > 0.3
        return metrics

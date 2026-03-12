"""Agent 评估数据模型。

面试考点:
  - Agent 评估四大维度：
      1. 任务完成率（Task Completion Rate）：最终答案是否满足要求
      2. 工具准确率（Tool Accuracy）：调用了正确的工具且参数合理
      3. 步骤效率（Step Efficiency）：用最少迭代步骤完成任务
      4. Token 效率（Token Efficiency）：用最少 Token 完成任务
  - LLM-as-Judge：用 LLM 对 Agent 输出打分（0-10），比规则匹配更灵活
  - 对比评估：同一数据集上 ReAct vs Plan-and-Execute 的横向对比
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TaskDifficulty(str, Enum):
    EASY = "easy"       # 单步工具调用
    MEDIUM = "medium"   # 2-3 步推理
    HARD = "hard"       # 多步规划 + 工具组合


class AgentTypeLabel(str, Enum):
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"


@dataclass
class EvalTask:
    """单个评估任务定义。"""

    task_id: str
    question: str                           # 用户问题
    expected_answer: str = ""               # 参考答案（用于 LLM-as-Judge 对比）
    expected_tools: list[str] = field(default_factory=list)  # 期望调用的工具
    difficulty: TaskDifficulty = TaskDifficulty.MEDIUM
    category: str = "general"              # 任务类别：math / search / code / reasoning
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalMetrics:
    """单次评估的量化指标。"""

    # 核心指标
    task_completed: bool = False            # 任务是否完成
    judge_score: float = 0.0               # LLM-as-Judge 评分 (0-10)
    judge_reason: str = ""                 # 评分理由

    # 效率指标
    total_iterations: int = 0              # ReAct 迭代次数
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0

    # 工具使用
    tools_called: list[str] = field(default_factory=list)   # 实际调用的工具
    tool_accuracy: float = 0.0             # 工具准确率 (0-1)

    # 衍生指标
    @property
    def token_efficiency(self) -> float:
        """Token 效率：judge_score / total_tokens * 1000（越高越好）。"""
        if self.total_tokens == 0:
            return 0.0
        return round(self.judge_score / self.total_tokens * 1000, 4)

    @property
    def step_efficiency(self) -> float:
        """步骤效率：judge_score / iterations（越高越好）。"""
        if self.total_iterations == 0:
            return 0.0
        return round(self.judge_score / self.total_iterations, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_completed": self.task_completed,
            "judge_score": self.judge_score,
            "judge_reason": self.judge_reason,
            "total_iterations": self.total_iterations,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "tools_called": self.tools_called,
            "tool_accuracy": round(self.tool_accuracy, 3),
            "token_efficiency": self.token_efficiency,
            "step_efficiency": self.step_efficiency,
        }


@dataclass
class EvalResult:
    """单个任务的完整评估结果。"""

    task: EvalTask
    agent_type: AgentTypeLabel
    actual_answer: str = ""
    metrics: EvalMetrics = field(default_factory=EvalMetrics)
    error: str = ""
    evaluated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task.task_id,
            "question": self.task.question,
            "difficulty": self.task.difficulty.value,
            "category": self.task.category,
            "agent_type": self.agent_type.value,
            "actual_answer": self.actual_answer[:300],
            "expected_answer": self.task.expected_answer[:300],
            "error": self.error,
            "metrics": self.metrics.to_dict(),
            "evaluated_at": self.evaluated_at.isoformat(),
        }


@dataclass
class EvalReport:
    """批量评估报告（支持多 Agent 对比）。"""

    report_id: str
    agent_type: AgentTypeLabel
    results: list[EvalResult] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)

    # 汇总统计（调用 compute() 后填充）
    total_tasks: int = 0
    completed_tasks: int = 0
    avg_judge_score: float = 0.0
    avg_iterations: float = 0.0
    avg_total_tokens: float = 0.0
    avg_elapsed_seconds: float = 0.0
    avg_tool_accuracy: float = 0.0
    completion_rate: float = 0.0

    def compute(self) -> "EvalReport":
        """计算汇总统计。"""
        self.total_tasks = len(self.results)
        if self.total_tasks == 0:
            return self

        valid = [r for r in self.results if not r.error]
        self.completed_tasks = sum(1 for r in valid if r.metrics.task_completed)
        self.completion_rate = round(self.completed_tasks / self.total_tasks, 3)
        self.avg_judge_score = round(
            sum(r.metrics.judge_score for r in valid) / max(len(valid), 1), 2
        )
        self.avg_iterations = round(
            sum(r.metrics.total_iterations for r in valid) / max(len(valid), 1), 1
        )
        self.avg_total_tokens = round(
            sum(r.metrics.total_tokens for r in valid) / max(len(valid), 1), 0
        )
        self.avg_elapsed_seconds = round(
            sum(r.metrics.elapsed_seconds for r in valid) / max(len(valid), 1), 2
        )
        self.avg_tool_accuracy = round(
            sum(r.metrics.tool_accuracy for r in valid) / max(len(valid), 1), 3
        )
        return self

    def to_dict(self) -> dict[str, Any]:
        self.compute()
        return {
            "report_id": self.report_id,
            "agent_type": self.agent_type.value,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "completion_rate": self.completion_rate,
            "avg_judge_score": self.avg_judge_score,
            "avg_iterations": self.avg_iterations,
            "avg_total_tokens": self.avg_total_tokens,
            "avg_elapsed_seconds": self.avg_elapsed_seconds,
            "avg_tool_accuracy": self.avg_tool_accuracy,
            "created_at": self.created_at.isoformat(),
            "results": [r.to_dict() for r in self.results],
        }

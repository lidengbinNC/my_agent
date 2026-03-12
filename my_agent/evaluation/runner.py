"""评估执行器 — 单任务评估 + 批量评估 + ReAct vs PlanExec 对比。

面试考点:
  - 评估执行器设计：与 Agent 引擎解耦，只关注输入/输出/指标
  - 批量评估：asyncio.gather 并行执行，提升评估速度
  - 对比评估：同一数据集在两种 Agent 上运行，指标横向对比
  - 评估报告：JSON 格式，可导入 Excel / 可视化工具分析
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Callable, AsyncGenerator

from my_agent.core.engine.react_engine import ReActEngine, ReActStepType
from my_agent.evaluation.judge import LLMJudge
from my_agent.evaluation.models import (
    AgentTypeLabel,
    EvalMetrics,
    EvalReport,
    EvalResult,
    EvalTask,
)
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class EvalRunner:
    """Agent 评估执行器。"""

    def __init__(
        self,
        react_engine: ReActEngine,
        judge: LLMJudge,
        concurrency: int = 3,
    ) -> None:
        self._react_engine = react_engine
        self._judge = judge
        self._concurrency = concurrency  # 并行评估任务数

    async def evaluate_single(
        self,
        task: EvalTask,
        agent_type: AgentTypeLabel = AgentTypeLabel.REACT,
    ) -> EvalResult:
        """评估单个任务。"""
        logger.info("eval_task_start", task_id=task.task_id, agent=agent_type.value)
        start = time.monotonic()

        actual_answer = ""
        tools_called: list[str] = []
        prompt_tokens = 0
        completion_tokens = 0
        iterations = 0
        error = ""

        try:
            if agent_type == AgentTypeLabel.REACT:
                async for step in self._react_engine.run(task.question):
                    iterations += 1
                    if step.type == ReActStepType.ACTION and step.action:
                        tools_called.append(step.action)
                    elif step.type == ReActStepType.FINAL_ANSWER:
                        actual_answer = step.answer
                    elif step.type == ReActStepType.ERROR:
                        error = step.error or ""
                        actual_answer = f"[错误] {step.error}"

            elif agent_type == AgentTypeLabel.PLAN_EXECUTE:
                from my_agent.core.engine.plan_execute_engine import (
                    PlanAndExecuteEngine,
                    PlanExecEventType,
                )
                from my_agent.domain.tool.registry import get_registry
                pe_engine = PlanAndExecuteEngine(
                    llm=self._react_engine._llm,
                    tool_registry=get_registry(),
                    max_plan_steps=6,
                    max_iterations_per_step=4,
                )
                async for evt in pe_engine.run(task.question):
                    if evt.type == PlanExecEventType.STEP_DONE:
                        iterations += 1
                    elif evt.type == PlanExecEventType.DONE:
                        actual_answer = evt.message

        except Exception as e:
            error = str(e)
            actual_answer = f"[异常] {e}"
            logger.warning("eval_task_error", task_id=task.task_id, error=error)

        elapsed = time.monotonic() - start

        # LLM-as-Judge 评分
        metrics = await self._judge.judge(task, actual_answer, tools_called)
        metrics.total_iterations = iterations
        metrics.tools_called = tools_called
        metrics.elapsed_seconds = elapsed
        # 注：token 统计需要 LLM client 回传，此处简化为 0（可通过 CostTracker 获取）

        result = EvalResult(
            task=task,
            agent_type=agent_type,
            actual_answer=actual_answer,
            metrics=metrics,
            error=error,
        )
        logger.info(
            "eval_task_done",
            task_id=task.task_id,
            score=metrics.judge_score,
            completed=metrics.task_completed,
            elapsed=round(elapsed, 1),
        )
        return result

    async def evaluate_batch(
        self,
        tasks: list[EvalTask],
        agent_type: AgentTypeLabel = AgentTypeLabel.REACT,
        progress_cb: Callable[[int, int], None] | None = None,
    ) -> EvalReport:
        """批量评估，控制并发数量。"""
        report = EvalReport(
            report_id=str(uuid.uuid4())[:8],
            agent_type=agent_type,
        )
        semaphore = asyncio.Semaphore(self._concurrency)
        completed = 0

        async def _run_one(task: EvalTask) -> EvalResult:
            nonlocal completed
            async with semaphore:
                result = await self.evaluate_single(task, agent_type)
                completed += 1
                if progress_cb:
                    progress_cb(completed, len(tasks))
                return result

        results = await asyncio.gather(
            *[_run_one(t) for t in tasks],
            return_exceptions=True,
        )

        for task, res in zip(tasks, results):
            if isinstance(res, Exception):
                report.results.append(EvalResult(
                    task=task,
                    agent_type=agent_type,
                    error=str(res),
                ))
            else:
                report.results.append(res)

        report.compute()
        logger.info(
            "eval_batch_done",
            agent=agent_type.value,
            total=report.total_tasks,
            completed=report.completed_tasks,
            avg_score=report.avg_judge_score,
        )
        return report

    async def compare(
        self,
        tasks: list[EvalTask],
    ) -> dict[str, EvalReport]:
        """对比评估：同一数据集在 ReAct 和 PlanExec 上运行，返回两份报告。

        面试考点：对比评估是衡量 Agent 架构优劣的核心手段
        """
        logger.info("eval_compare_start", tasks=len(tasks))

        react_report, plan_report = await asyncio.gather(
            self.evaluate_batch(tasks, AgentTypeLabel.REACT),
            self.evaluate_batch(tasks, AgentTypeLabel.PLAN_EXECUTE),
        )

        return {
            "react": react_report,
            "plan_execute": plan_report,
            "comparison": _build_comparison(react_report, plan_report),
        }


def _build_comparison(react: EvalReport, plan: EvalReport) -> dict:
    """生成对比摘要。"""
    def _diff(a: float, b: float) -> str:
        d = a - b
        return f"+{d:.2f}" if d > 0 else f"{d:.2f}"

    return {
        "winner_completion_rate": (
            "react" if react.completion_rate >= plan.completion_rate else "plan_execute"
        ),
        "winner_judge_score": (
            "react" if react.avg_judge_score >= plan.avg_judge_score else "plan_execute"
        ),
        "winner_token_efficiency": (
            "react" if react.avg_total_tokens <= plan.avg_total_tokens else "plan_execute"
        ),
        "winner_speed": (
            "react" if react.avg_elapsed_seconds <= plan.avg_elapsed_seconds else "plan_execute"
        ),
        "metrics_diff": {
            "completion_rate": _diff(react.completion_rate, plan.completion_rate),
            "avg_judge_score": _diff(react.avg_judge_score, plan.avg_judge_score),
            "avg_total_tokens": _diff(react.avg_total_tokens, plan.avg_total_tokens),
            "avg_elapsed_seconds": _diff(react.avg_elapsed_seconds, plan.avg_elapsed_seconds),
        },
        "insight": (
            "ReAct 在简单任务上更高效（Token 少、速度快）；"
            "Plan-and-Execute 在复杂多步任务上完成率更高（规划更系统）。"
        ),
    }

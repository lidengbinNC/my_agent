"""Executor — 逐步执行计划，每步调用 ReAct 子引擎完成。

面试考点:
  - Plan-and-Execute 中 Executor 的职责: 接收单个 PlanStep，调用 ReAct 完成
  - 步骤结果累积: 每步完成后将结果注入下一步的上下文
  - 失败处理策略: 步骤失败时标记 failed，不影响其他步骤，Replanner 决定是否补救
  - yield 事件: 与 ReActEngine 一样，Executor 也是 AsyncGenerator，支持 SSE 流式推送
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from my_agent.core.engine.react_engine import ReActEngine, ReActStepType
from my_agent.domain.agent.base import ExecutionPlan, PlanStep
from my_agent.domain.agent.fsm import AgentEvent, AgentFSM
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class ExecutorEventType(str, Enum):
    PLAN_READY = "plan_ready"       # 计划生成完毕
    STEP_START = "step_start"       # 开始执行某步骤
    STEP_DONE = "step_done"         # 步骤执行完成
    STEP_FAILED = "step_failed"     # 步骤执行失败
    REPLANNING = "replanning"       # 触发重新规划
    SYNTHESIZING = "synthesizing"   # 汇总结果
    DONE = "done"                   # 全部完成
    ERROR = "error"                 # 致命错误


@dataclass
class ExecutorEvent:
    """Executor 推送的单个事件。"""

    type: ExecutorEventType
    step: PlanStep | None = None
    plan: ExecutionPlan | None = None
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class StepExecutor:
    """单步执行器：将一个 PlanStep 委托给 ReAct 引擎完成。"""

    def __init__(self, react_engine: ReActEngine) -> None:
        self._engine = react_engine

    async def execute(
        self,
        step: PlanStep,
        context: str = "",
    ) -> str:
        """执行单个步骤，返回步骤结果摘要。"""
        # 构建步骤级查询：结合步骤描述 + 已有上下文
        query = step.description
        if context:
            query = f"背景（前面步骤的结果）:\n{context}\n\n当前任务: {step.description}"
        if step.tool_hint:
            query += f"\n\n提示：优先使用工具 [{step.tool_hint}] 完成此任务"

        step.status = "running"
        final_answer = ""

        try:
            async for react_step in self._engine.run(query):
                if react_step.type == ReActStepType.FINAL_ANSWER:
                    final_answer = react_step.answer
                elif react_step.type == ReActStepType.ERROR:
                    raise RuntimeError(react_step.error)
        except Exception as e:
            step.status = "failed"
            step.result = f"[失败] {e}"
            logger.warning("step_execution_failed", step_id=step.step_id, error=str(e))
            return step.result

        step.status = "done"
        step.result = final_answer
        return final_answer


class PlanExecutor:
    """计划执行器：按顺序逐步执行 ExecutionPlan。"""

    def __init__(self, step_executor: StepExecutor) -> None:
        self._step_executor = step_executor

    async def execute(
        self,
        plan: ExecutionPlan,
        fsm: AgentFSM,
    ) -> AsyncGenerator[ExecutorEvent, None]:
        """逐步执行计划，yield ExecutorEvent 供 SSE 推送。"""
        yield ExecutorEvent(
            type=ExecutorEventType.PLAN_READY,
            plan=plan,
            message=plan.summary(),
        )

        # 累积上下文：每步结果拼接给下一步参考
        accumulated_context = ""

        for step in plan.steps:
            if step.status != "pending":
                continue

            fsm.trigger(AgentEvent.ACT)

            yield ExecutorEvent(
                type=ExecutorEventType.STEP_START,
                step=step,
                message=f"开始执行 Step {step.step_id}: {step.description}",
            )

            start = time.monotonic()
            result = await self._step_executor.execute(step, context=accumulated_context)
            elapsed = time.monotonic() - start

            if step.status == "done":
                fsm.trigger(AgentEvent.OBSERVE)
                accumulated_context += f"\nStep {step.step_id} 结果: {result}"
                yield ExecutorEvent(
                    type=ExecutorEventType.STEP_DONE,
                    step=step,
                    message=f"Step {step.step_id} 完成（{elapsed:.1f}s）",
                    data={"result": result, "elapsed": elapsed},
                )
            else:
                yield ExecutorEvent(
                    type=ExecutorEventType.STEP_FAILED,
                    step=step,
                    message=f"Step {step.step_id} 失败: {result}",
                    data={"error": result},
                )

        # accumulated_context 通过 step.result 字段供调用方访问

"""Plan-and-Execute 引擎 — 组合 Planner + Executor + Replanner + FSM。

面试考点:
  - Plan-and-Execute 流程: Plan → Execute(步骤逐个) → [Replan if fail] → Synthesize
  - FSM 贯穿全程: IDLE→THINKING(规划)→ACTING(执行步)→THINKING(评估)→SYNTHESIZING→FINISHED
  - AsyncGenerator 统一推送事件，与 ReAct 引擎接口对齐
  - Synthesizer: 将所有步骤结果汇总生成自然语言最终答案
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from my_agent.core.engine.executor import ExecutorEventType, PlanExecutor, StepExecutor
from my_agent.core.engine.planner import Planner
from my_agent.core.engine.replanner import Replanner
from my_agent.domain.agent.base import AgentRunResult, AgentType, ExecutionPlan
from my_agent.domain.agent.fsm import AgentEvent, AgentFSM
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import SystemMessage, UserMessage
from my_agent.domain.tool.registry import ToolRegistry
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class PlanExecEventType(str, Enum):
    PLANNING = "planning"           # 正在生成计划
    PLAN_READY = "plan_ready"       # 计划已生成
    STEP_START = "step_start"       # 开始执行步骤
    STEP_DONE = "step_done"         # 步骤完成
    STEP_FAILED = "step_failed"     # 步骤失败
    REPLANNING = "replanning"       # 重新规划
    SYNTHESIZING = "synthesizing"   # 汇总中
    DONE = "done"                   # 完成
    ERROR = "error"                 # 错误


@dataclass
class PlanExecEvent:
    """Plan-and-Execute 引擎推送的结构化事件。"""

    type: PlanExecEventType
    message: str = ""
    plan: ExecutionPlan | None = None
    step_id: int | None = None
    step_desc: str = ""
    result: str = ""
    data: dict[str, Any] = field(default_factory=dict)


_SYNTHESIZE_PROMPT = """你是一个智能助手。用户提出了一个问题，系统通过多步骤执行得到了各步结果。
请根据以下执行结果，生成一个完整、流畅、准确的最终回答。

用户问题: {goal}

各步骤执行结果:
{steps_summary}

请用中文给出完整回答，不要列举步骤编号，直接给出答案:"""


class PlanAndExecuteEngine:
    """Plan-and-Execute Agent 引擎。

    流程:
      1. Planner: 将用户目标分解为 N 个步骤
      2. Executor: 逐步调用 ReAct 完成每步
      3. Replanner: 若有步骤失败，动态调整剩余计划（可选）
      4. Synthesizer: LLM 汇总所有步骤结果生成最终答案
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        tool_registry: ToolRegistry,
        *,
        max_plan_steps: int = 8,
        max_iterations_per_step: int = 5,
        tool_timeout: float = 30.0,
        enable_replanning: bool = True,
    ) -> None:
        from my_agent.core.engine.react_engine import ReActEngine

        self._llm = llm
        self._planner = Planner(llm, max_steps=max_plan_steps)
        self._replanner = Replanner(llm)
        self._react_engine = ReActEngine(
            llm=llm,
            tool_registry=tool_registry,
            max_iterations=max_iterations_per_step,
            tool_timeout=tool_timeout,
        )
        self._step_executor = StepExecutor(self._react_engine)
        self._plan_executor = PlanExecutor(self._step_executor)
        self._enable_replanning = enable_replanning

    async def run(
        self,
        goal: str,
        context: str = "",
    ) -> AsyncGenerator[PlanExecEvent, None]:
        """执行 Plan-and-Execute 循环，逐步 yield PlanExecEvent。"""
        fsm = AgentFSM(agent_id="plan_exec")
        start_time = time.monotonic()

        fsm.trigger(AgentEvent.START)

        # ── 1. 规划阶段 ──────────────────────────────────────────
        yield PlanExecEvent(type=PlanExecEventType.PLANNING, message="正在生成执行计划...")

        try:
            plan = await self._planner.plan(goal, context=context)
        except Exception as e:
            fsm.trigger(AgentEvent.ERROR)
            yield PlanExecEvent(type=PlanExecEventType.ERROR, message=f"规划失败: {e}")
            return

        yield PlanExecEvent(
            type=PlanExecEventType.PLAN_READY,
            plan=plan,
            message=plan.summary(),
        )

        # ── 2. 执行阶段（含重规划）────────────────────────────────
        max_replan = 2
        replan_count = 0

        while not plan.is_complete() and replan_count <= max_replan:
            async for evt in self._plan_executor.execute(plan, fsm):
                if evt.type == ExecutorEventType.PLAN_READY:
                    pass  # 已在上面推送
                elif evt.type == ExecutorEventType.STEP_START:
                    yield PlanExecEvent(
                        type=PlanExecEventType.STEP_START,
                        step_id=evt.step.step_id if evt.step else None,
                        step_desc=evt.step.description if evt.step else "",
                        message=evt.message,
                    )
                elif evt.type == ExecutorEventType.STEP_DONE:
                    yield PlanExecEvent(
                        type=PlanExecEventType.STEP_DONE,
                        step_id=evt.step.step_id if evt.step else None,
                        step_desc=evt.step.description if evt.step else "",
                        result=evt.data.get("result", ""),
                        message=evt.message,
                    )
                elif evt.type == ExecutorEventType.STEP_FAILED:
                    yield PlanExecEvent(
                        type=PlanExecEventType.STEP_FAILED,
                        step_id=evt.step.step_id if evt.step else None,
                        step_desc=evt.step.description if evt.step else "",
                        message=evt.message,
                    )

            # 检查是否需要重规划
            if (
                self._enable_replanning
                and self._replanner.should_replan(plan)
                and replan_count < max_replan
            ):
                replan_count += 1
                yield PlanExecEvent(
                    type=PlanExecEventType.REPLANNING,
                    message=f"检测到步骤失败，开始第 {replan_count} 次重规划...",
                )
                try:
                    plan = await self._replanner.replan(plan)
                except Exception as e:
                    logger.warning("replan_failed", error=str(e))
                    break
            else:
                break

        # ── 3. 合成阶段 ──────────────────────────────────────────
        fsm.trigger(AgentEvent.SYNTHESIZE)
        yield PlanExecEvent(
            type=PlanExecEventType.SYNTHESIZING,
            message="正在汇总所有步骤结果，生成最终回答...",
            plan=plan,
        )

        final_answer = await self._synthesize(goal, plan)

        fsm.trigger(AgentEvent.FINISH)
        elapsed = time.monotonic() - start_time

        yield PlanExecEvent(
            type=PlanExecEventType.DONE,
            message=final_answer,
            plan=plan,
            data={"elapsed": elapsed, "fsm_history": [str(t) for t in fsm.transition_history()]},
        )

    async def _synthesize(self, goal: str, plan: ExecutionPlan) -> str:
        """调用 LLM 将步骤结果汇总为最终答案。"""
        done_steps = plan.completed_steps()
        if not done_steps:
            return "所有步骤均未成功完成，无法生成最终答案。"

        steps_summary = "\n".join(
            f"步骤 {s.step_id}（{s.description}）:\n  {s.result}" for s in done_steps
        )
        prompt = _SYNTHESIZE_PROMPT.format(goal=goal, steps_summary=steps_summary)

        try:
            response = await self._llm.chat(
                [SystemMessage("你是一个智能助手，擅长总结和分析。"), UserMessage(prompt)],
                temperature=0.5,
            )
            return response.content or "（合成失败）"
        except Exception as e:
            logger.warning("synthesize_failed", error=str(e))
            return steps_summary

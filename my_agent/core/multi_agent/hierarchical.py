"""HierarchicalCoordinator — 层级协作（Manager-Workers 模式）。

面试考点:
  - 层级模式：Manager Agent 智能分配任务，Workers 执行，Manager 再审核
  - Manager 使用 LLM 动态决策：根据目标 + 可用 Worker 列表 → 生成分配方案
  - 二轮审核：Manager 拿到所有 Worker 结果后可要求某个 Worker 修改（反馈循环）
  - 对比 Sequential: Sequential 是固定顺序，Hierarchical 是动态分配
  - 适用场景：复杂任务、需要专业分工、质量要求高（内容审核 + 修改）
"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from my_agent.core.multi_agent.base import (
    BaseCoordinator,
    CoordinatorEvent,
    CoordinatorEventType,
)
from my_agent.domain.llm.message import SystemMessage, UserMessage
from my_agent.domain.multi_agent.message import AgentMessage, MessageType
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

_MANAGER_ASSIGN_PROMPT = """你是一个任务管理者（Manager）。
你有以下可用的 Worker Agent:
{workers_desc}

用户目标: {goal}

请为每个 Worker 分配具体的子任务。输出严格 JSON:
{{
  "assignments": [
    {{"worker": "worker名称", "task": "具体子任务描述"}},
    ...
  ]
}}

只分配必要的 Worker，可以不使用全部 Worker。只输出 JSON。"""

_MANAGER_REVIEW_PROMPT = """你是一个任务审核者（Manager）。
用户原始目标: {goal}

各 Worker 的执行结果:
{results_text}

请审核以上结果，判断是否需要某个 Worker 修改。输出严格 JSON:
{{
  "approved": true/false,
  "feedback": [
    {{"worker": "worker名称", "issue": "问题描述", "instruction": "修改指令"}}
  ],
  "final_answer": "如果 approved=true，在此给出综合最终答案；否则留空"
}}

只输出 JSON。"""


class HierarchicalCoordinator(BaseCoordinator):
    """层级协作协调器：Manager 分配 → Workers 并行执行 → Manager 审核 → [修改] → 汇总。"""

    def __init__(self, *args, manager_name: str = "manager", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._manager_name = manager_name

    async def run(
        self,
        goal: str,
        context: str = "",
    ) -> AsyncGenerator[CoordinatorEvent, None]:
        # 分离 manager 和 workers
        workers = {n: s for n, s in self._agents.items() if n != self._manager_name}
        if not workers:
            yield CoordinatorEvent(type=CoordinatorEventType.ERROR, message="无可用 Worker Agent")
            return

        # ── 阶段 1: Manager 分配任务 ──────────────────────────────
        yield CoordinatorEvent(
            type=CoordinatorEventType.AGENT_START,
            agent_name=self._manager_name,
            message=f"[Manager] 正在为 {len(workers)} 个 Worker 分配任务...",
        )

        assignments = await self._manager_assign(goal, workers)

        if not assignments:
            # 兜底：平均分配
            assignments = {name: f"目标: {goal}\n你的角色: {spec.role_description()}" for name, spec in workers.items()}

        yield CoordinatorEvent(
            type=CoordinatorEventType.AGENT_DONE,
            agent_name=self._manager_name,
            message=f"[Manager] 任务分配完成，分配了 {len(assignments)} 个子任务",
            data={"assignments": assignments},
        )

        # ── 阶段 2: Workers 并行执行 ──────────────────────────────
        import asyncio
        tasks_coros = {
            name: self._run_agent(name, task, context=context)
            for name, task in assignments.items()
            if name in workers
        }

        for name in tasks_coros:
            msg = AgentMessage(
                type=MessageType.TASK, sender=self._manager_name,
                receiver=name, content=assignments[name], task_id=goal[:30],
            )
            self._log_message(msg)
            yield CoordinatorEvent(
                type=CoordinatorEventType.AGENT_START,
                agent_name=name,
                message=f"[{name}] 开始执行",
            )

        raw_results = await asyncio.gather(*tasks_coros.values(), return_exceptions=True)
        results: dict[str, str] = {}
        for name, res in zip(tasks_coros.keys(), raw_results):
            results[name] = f"[错误] {res}" if isinstance(res, Exception) else str(res)
            reply = AgentMessage(
                type=MessageType.RESULT, sender=name,
                receiver=self._manager_name, content=results[name], task_id=goal[:30],
            )
            self._log_message(reply)
            yield CoordinatorEvent(
                type=CoordinatorEventType.AGENT_DONE,
                agent_name=name,
                result=results[name],
                message=f"[{name}] 完成",
            )

        # ── 阶段 3: Manager 审核 ──────────────────────────────────
        yield CoordinatorEvent(
            type=CoordinatorEventType.AGENT_START,
            agent_name=self._manager_name,
            message="[Manager] 正在审核所有 Worker 的结果...",
        )

        review = await self._manager_review(goal, results)
        approved = review.get("approved", True)
        final_answer = review.get("final_answer", "")

        if not approved and review.get("feedback"):
            # ── 阶段 4: 修改轮（最多 1 次）────────────────────────
            for fb in review["feedback"]:
                worker = fb.get("worker", "")
                instruction = fb.get("instruction", "")
                if worker not in workers or not instruction:
                    continue

                revised_task = (
                    f"原始目标: {goal}\n原始结果:\n{results.get(worker, '')}\n\n"
                    f"Manager 的修改要求: {instruction}"
                )
                msg = AgentMessage(
                    type=MessageType.FEEDBACK, sender=self._manager_name,
                    receiver=worker, content=instruction, task_id=goal[:30],
                )
                self._log_message(msg)

                yield CoordinatorEvent(
                    type=CoordinatorEventType.AGENT_START,
                    agent_name=worker,
                    message=f"[{worker}] 收到 Manager 反馈，修改中...",
                )
                revised = await self._run_agent(worker, revised_task, context=context)
                results[worker] = revised
                yield CoordinatorEvent(
                    type=CoordinatorEventType.AGENT_DONE,
                    agent_name=worker,
                    result=revised,
                    message=f"[{worker}] 修改完成",
                )

            # 重新汇总
            final_answer = await self._llm_synthesize(goal, results)

        if not final_answer:
            final_answer = await self._llm_synthesize(goal, results)

        yield CoordinatorEvent(
            type=CoordinatorEventType.DONE,
            message=final_answer,
            data={"results": results, "approved": approved},
        )

    async def _manager_assign(self, goal: str, workers: dict) -> dict[str, str]:
        """Manager 调用 LLM 生成任务分配方案。"""
        workers_desc = "\n".join(
            f"  - {name}: {spec.role_description()}" for name, spec in workers.items()
        )
        prompt = _MANAGER_ASSIGN_PROMPT.format(goal=goal, workers_desc=workers_desc)
        try:
            resp = await self._llm.chat(
                [SystemMessage("你是一个项目经理，擅长任务分解和团队协作。"), UserMessage(prompt)],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.content or "{}")
            return {a["worker"]: a["task"] for a in data.get("assignments", []) if "worker" in a}
        except Exception as e:
            logger.warning("manager_assign_failed", error=str(e))
            return {}

    async def _manager_review(self, goal: str, results: dict[str, str]) -> dict:
        """Manager 审核所有结果，决定是否需要修改。"""
        results_text = "\n\n".join(f"【{n}】:\n{r}" for n, r in results.items())
        prompt = _MANAGER_REVIEW_PROMPT.format(goal=goal, results_text=results_text)
        try:
            resp = await self._llm.chat(
                [SystemMessage("你是一个严格的质量审核者。"), UserMessage(prompt)],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.content or '{"approved": true, "feedback": [], "final_answer": ""}')
        except Exception as e:
            logger.warning("manager_review_failed", error=str(e))
            return {"approved": True, "feedback": [], "final_answer": ""}

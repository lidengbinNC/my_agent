"""ParallelCoordinator — 并行协作（Map-Reduce 模式）。

面试考点:
  - Map 阶段：将目标分解为 N 个子任务，asyncio.gather 并行发给各 Agent
  - Reduce 阶段：LLM 汇总各 Agent 结果生成最终答案
  - asyncio.gather vs asyncio.create_task 的区别:
      gather: 等待所有完成（适合需要全部结果再汇总）
      create_task: 独立调度（适合后台任务，不阻塞主流程）
  - 异常隔离：return_exceptions=True 防止单个 Agent 失败导致整批失败
  - 适用场景：子任务独立无依赖（多角度研究、并行搜索、批量处理）
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

from my_agent.core.multi_agent.base import (
    BaseCoordinator,
    CoordinatorEvent,
    CoordinatorEventType,
)
from my_agent.domain.multi_agent.message import AgentMessage, MessageType
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class ParallelCoordinator(BaseCoordinator):
    """并行协作协调器：Map-Reduce 模式，所有 Agent 并行执行，LLM 汇总。"""

    async def run(
        self,
        goal: str,
        context: str = "",
    ) -> AsyncGenerator[CoordinatorEvent, None]:
        agent_names = list(self._agents.keys())

        # ── Map 阶段：为每个 Agent 分配独立子任务 ──────────────────
        yield CoordinatorEvent(
            type=CoordinatorEventType.AGENT_START,
            message=f"并行启动 {len(agent_names)} 个 Agent: {', '.join(agent_names)}",
        )

        for name in agent_names:
            spec = self._agents[name]
            task_msg = AgentMessage(
                type=MessageType.TASK,
                sender="coordinator",
                receiver=name,
                content=f"目标: {goal}\n你的角色: {spec.role_description()}\n请完成你负责的部分。",
                task_id=goal[:30],
            )
            self._log_message(task_msg)

        # 并行执行所有 Agent（return_exceptions=True 保证单个失败不影响整体）
        tasks = [
            self._run_agent(
                name,
                task=f"目标: {goal}\n你的角色: {self._agents[name].role_description()}\n请完成你负责的部分。",
                context=context,
            )
            for name in agent_names
        ]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        # ── 整理结果 ──────────────────────────────────────────────
        results: dict[str, str] = {}
        for name, res in zip(agent_names, raw_results):
            if isinstance(res, Exception):
                results[name] = f"[错误] {res}"
                yield CoordinatorEvent(
                    type=CoordinatorEventType.AGENT_ERROR,
                    agent_name=name,
                    message=f"[{name}] 执行失败: {res}",
                )
            else:
                results[name] = str(res)
                reply = AgentMessage(
                    type=MessageType.RESULT,
                    sender=name,
                    receiver="coordinator",
                    content=str(res),
                    task_id=goal[:30],
                )
                self._log_message(reply)
                yield CoordinatorEvent(
                    type=CoordinatorEventType.AGENT_DONE,
                    agent_name=name,
                    result=str(res),
                    message=f"[{name}] 完成",
                )

        # ── Reduce 阶段：LLM 汇总 ────────────────────────────────
        yield CoordinatorEvent(
            type=CoordinatorEventType.SYNTHESIZING,
            message="正在汇总所有 Agent 的结果...",
        )
        final = await self._llm_synthesize(goal, results)

        yield CoordinatorEvent(
            type=CoordinatorEventType.DONE,
            message=final,
            data={"results": results, "agents": agent_names},
        )

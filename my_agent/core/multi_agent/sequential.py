"""SequentialCoordinator — 顺序协作（Pipeline 模式）。

面试考点:
  - Pipeline 模式：Agent A 的输出作为 Agent B 的输入，形成处理链
  - 每步上下文累积：前序 Agent 的结果拼接入下一 Agent 的 context
  - 适用场景：有明确先后依赖的工作流（研究 → 写作 → 审核）
  - 对比 Parallel：无法并行，但每步可利用前步结果，质量更高
"""

from __future__ import annotations

from typing import AsyncGenerator

from my_agent.core.multi_agent.base import (
    BaseCoordinator,
    CoordinatorEvent,
    CoordinatorEventType,
)
from my_agent.domain.multi_agent.message import AgentMessage, MessageType
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class SequentialCoordinator(BaseCoordinator):
    """顺序协作协调器：按 agents 列表顺序依次执行，前步结果传入后步。"""

    async def run(
        self,
        goal: str,
        context: str = "",
    ) -> AsyncGenerator[CoordinatorEvent, None]:
        agent_names = list(self._agents.keys())
        accumulated = context  # 累积上下文，逐步传递
        results: dict[str, str] = {}

        for i, name in enumerate(agent_names):
            spec = self._agents[name]

            # 构造任务：原始目标 + 前序结果
            if i == 0:
                task = goal
            else:
                prev_name = agent_names[i - 1]
                task = (
                    f"原始目标: {goal}\n\n"
                    f"前一步（{prev_name}）的输出:\n{results[prev_name]}\n\n"
                    f"你的任务（{spec.role_description()}）: 基于以上内容继续完成工作"
                )

            # 发送任务消息
            msg = AgentMessage(
                type=MessageType.TASK,
                sender="coordinator",
                receiver=name,
                content=task,
                task_id=goal[:30],
            )
            self._log_message(msg)

            yield CoordinatorEvent(
                type=CoordinatorEventType.AGENT_START,
                agent_name=name,
                message=f"[{name}] 开始执行（第 {i+1}/{len(agent_names)} 步）",
            )

            result = await self._run_agent(name, task, context=accumulated)
            results[name] = result
            accumulated += f"\n\n[{name} 完成]: {result}"

            # 回复消息
            reply = msg.reply(result)
            self._log_message(reply)

            yield CoordinatorEvent(
                type=CoordinatorEventType.AGENT_DONE,
                agent_name=name,
                result=result,
                message=f"[{name}] 完成",
            )

        # 最后一个 Agent 的输出即为最终答案（已经过完整 Pipeline）
        final = results[agent_names[-1]] if agent_names else "（无 Agent 执行）"

        yield CoordinatorEvent(
            type=CoordinatorEventType.DONE,
            message=final,
            data={"results": results, "pipeline": agent_names},
        )

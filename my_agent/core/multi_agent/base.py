"""BaseCoordinator — 多 Agent 协调器抽象基类（中介者模式）。

面试考点:
  - 中介者模式（Mediator Pattern）：各 Agent 不直接通信，
    统一通过 Coordinator 路由，解耦 Agent 间依赖
  - BaseCoordinator 定义统一接口：run() 返回 AsyncGenerator[CoordinatorEvent]
    与 ReActEngine / PlanAndExecuteEngine 风格对齐，支持 SSE 推送
  - 每个子类实现不同的协作策略，无需修改 Agent 本身（开闭原则）
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from my_agent.core.engine.react_engine import ReActEngine
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import SystemMessage, UserMessage
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentMessage, MessageType
from my_agent.domain.tool.registry import ToolRegistry
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class CoordinatorEventType(str, Enum):
    AGENT_START = "agent_start"         # 某个 Agent 开始执行
    AGENT_DONE = "agent_done"           # 某个 Agent 完成
    AGENT_ERROR = "agent_error"         # 某个 Agent 报错
    MESSAGE = "message"                 # Agent 间消息传递
    SYNTHESIZING = "synthesizing"       # 汇总中
    DONE = "done"                       # 全部完成
    ERROR = "error"                     # 致命错误


@dataclass
class CoordinatorEvent:
    """Coordinator 推送的结构化事件，驱动 SSE 流。"""

    type: CoordinatorEventType
    agent_name: str = ""
    message: str = ""
    result: str = ""
    data: dict[str, Any] = field(default_factory=dict)


class BaseCoordinator(ABC):
    """多 Agent 协调器抽象基类。

    中介者模式：
      - Coordinator 是中心枢纽，拥有所有 AgentSpec
      - Agent 之间不互相感知，只和 Coordinator 交换 AgentMessage
      - 具体协作策略由子类实现（Sequential / Parallel / Hierarchical）
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        tool_registry: ToolRegistry,
        agents: list[AgentSpec],
    ) -> None:
        self._llm = llm
        self._registry = tool_registry
        self._agents: dict[str, AgentSpec] = {a.name: a for a in agents}
        self._engines: dict[str, ReActEngine] = {}
        self._message_log: list[AgentMessage] = []

    def _get_engine(self, agent_name: str) -> ReActEngine:
        """懒加载：首次使用时为该 Agent 创建 ReActEngine。"""
        if agent_name not in self._engines:
            spec = self._agents[agent_name]
            # 若有工具白名单，过滤注册表（子集注册）
            self._engines[agent_name] = ReActEngine(
                llm=self._llm,
                tool_registry=self._registry,
                max_iterations=spec.max_iterations,
            )
        return self._engines[agent_name]

    async def _run_agent(
        self,
        agent_name: str,
        task: str,
        context: str = "",
    ) -> str:
        """运行单个 Agent，返回其最终答案。

        如果 AgentSpec 定义了 system_prompt，会将其作为对话历史的第一条 system 消息。
        """
        spec = self._agents.get(agent_name)
        if spec is None:
            return f"[错误] Agent '{agent_name}' 不存在"

        engine = self._get_engine(agent_name)

        # 将 system_prompt + context 组合为对话历史前缀
        history = []
        if spec.system_prompt:
            history.append(SystemMessage(spec.system_prompt))
        if context:
            history.append(UserMessage(f"[背景信息]\n{context}"))

        from my_agent.core.engine.react_engine import ReActStepType
        final_answer = f"[Agent {agent_name} 未返回答案]"
        try:
            async for step in engine.run(task, history=history):
                if step.type == ReActStepType.FINAL_ANSWER:
                    final_answer = step.answer
                elif step.type == ReActStepType.ERROR:
                    final_answer = f"[错误] {step.error}"
        except Exception as e:
            final_answer = f"[异常] {e}"

        return final_answer

    def _log_message(self, msg: AgentMessage) -> None:
        self._message_log.append(msg)
        logger.debug(
            "agent_message",
            sender=msg.sender,
            receiver=msg.receiver,
            msg_type=msg.type.value,
            content=msg.content[:80],
        )

    @abstractmethod
    async def run(
        self,
        goal: str,
        context: str = "",
    ) -> AsyncGenerator[CoordinatorEvent, None]:
        """执行多 Agent 协作，逐步 yield CoordinatorEvent。"""

    async def _llm_synthesize(self, goal: str, results: dict[str, str]) -> str:
        """调用 LLM 将多个 Agent 结果合并为最终答案。"""
        results_text = "\n\n".join(
            f"【{name}】的输出:\n{result}" for name, result in results.items()
        )
        prompt = (
            f"用户目标: {goal}\n\n"
            f"各 Agent 的执行结果如下:\n{results_text}\n\n"
            "请综合以上内容，生成一份完整、连贯的最终回答（中文）:"
        )
        try:
            resp = await self._llm.chat(
                [SystemMessage("你是一个擅长总结和整合信息的助手。"), UserMessage(prompt)],
                temperature=0.5,
            )
            return resp.content or "（合成失败）"
        except Exception as e:
            logger.warning("llm_synthesize_failed", error=str(e))
            return results_text

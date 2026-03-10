"""Agent 工厂 — 根据配置创建不同类型的 Agent 引擎（工厂模式）。

面试考点:
  - 工厂模式（Factory Pattern）: 将对象创建逻辑集中，调用方只需传入 AgentConfig
  - 策略模式（Strategy Pattern）: ReActEngine / PlanAndExecuteEngine 实现相同的"运行"接口
  - 可扩展性: 新增 Agent 类型只需在工厂中注册，无需修改调用方
"""

from __future__ import annotations

from my_agent.core.engine.plan_execute_engine import PlanAndExecuteEngine
from my_agent.core.engine.react_engine import ReActEngine
from my_agent.domain.agent.base import AgentConfig, AgentType
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.tool.registry import ToolRegistry
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# 联合类型：工厂可返回任意 Agent 引擎
AnyAgent = ReActEngine | PlanAndExecuteEngine


class AgentFactory:
    """Agent 工厂，根据 AgentConfig 创建对应引擎。"""

    def __init__(self, llm: BaseLLMClient, tool_registry: ToolRegistry) -> None:
        self._llm = llm
        self._registry = tool_registry

    def create(self, config: AgentConfig) -> AnyAgent:
        """创建 Agent 引擎实例。

        Args:
            config: AgentConfig，指定 agent_type 及各项参数

        Returns:
            ReActEngine 或 PlanAndExecuteEngine

        Raises:
            ValueError: 未知 agent_type
        """
        agent_type = config.agent_type

        if agent_type == AgentType.REACT:
            engine = ReActEngine(
                llm=self._llm,
                tool_registry=self._registry,
                max_iterations=config.max_iterations,
                tool_timeout=config.tool_timeout,
            )
            logger.info("agent_created", type="react", name=config.name)
            return engine

        if agent_type == AgentType.PLAN_AND_EXECUTE:
            engine = PlanAndExecuteEngine(
                llm=self._llm,
                tool_registry=self._registry,
                max_plan_steps=config.max_plan_steps,
                max_iterations_per_step=config.max_iterations,
                tool_timeout=config.tool_timeout,
                enable_replanning=config.enable_replanning,
            )
            logger.info("agent_created", type="plan_execute", name=config.name)
            return engine

        raise ValueError(f"未知 Agent 类型: {agent_type}")

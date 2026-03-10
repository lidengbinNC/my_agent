"""AgentSpec — 多 Agent 场景中的单个 Agent 配置规格。

AgentSpec 描述一个 Agent 的角色、职责、系统提示，
由 Coordinator 统一管理和实例化，无需调用方知道底层引擎类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from my_agent.domain.multi_agent.message import AgentRole


@dataclass
class AgentSpec:
    """单个协作 Agent 的规格定义。"""

    name: str                                   # Agent 唯一名称（如 "researcher"）
    role: AgentRole = AgentRole.WORKER          # 预定义角色
    system_prompt: str = ""                     # 该 Agent 专用系统提示
    description: str = ""                       # 角色描述（用于 Manager 任务分配）
    tools: list[str] = field(default_factory=list)   # 允许使用的工具名称列表（空=全部）
    max_iterations: int = 6                     # ReAct 最大迭代次数

    def role_description(self) -> str:
        """返回用于 Manager Prompt 的角色描述。"""
        return self.description or f"{self.role.value} agent: {self.name}"

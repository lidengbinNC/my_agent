"""Agent 公共数据模型 — 配置、类型、运行结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AgentType(str, Enum):
    REACT = "react"                     # ReAct Agent（P2 已实现）
    PLAN_AND_EXECUTE = "plan_execute"   # Plan-and-Execute Agent（P4 新增）


@dataclass
class AgentConfig:
    """Agent 配置，供 AgentFactory 使用。"""

    agent_type: AgentType = AgentType.REACT
    name: str = "MyAgent"
    description: str = ""
    max_iterations: int = 10
    tool_timeout: float = 30.0
    # Plan-and-Execute 专用
    max_plan_steps: int = 8
    enable_replanning: bool = True


@dataclass
class PlanStep:
    """执行计划中的单个步骤。"""

    step_id: int
    description: str                    # 步骤自然语言描述
    tool_hint: str = ""                 # 建议使用的工具（可为空）
    depends_on: list[int] = field(default_factory=list)  # 依赖步骤 ID
    status: str = "pending"             # pending / running / done / failed
    result: str = ""                    # 步骤执行结果摘要


@dataclass
class ExecutionPlan:
    """LLM 生成的完整执行计划。"""

    goal: str
    steps: list[PlanStep]
    total_steps: int = 0

    def __post_init__(self) -> None:
        self.total_steps = len(self.steps)

    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "pending"]

    def completed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "done"]

    def failed_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "failed"]

    def is_complete(self) -> bool:
        return all(s.status in ("done", "failed") for s in self.steps)

    def summary(self) -> str:
        lines = [f"目标: {self.goal}", f"共 {self.total_steps} 步:"]
        for s in self.steps:
            icon = {"pending": "⏳", "running": "🔄", "done": "✅", "failed": "❌"}.get(s.status, "?")
            lines.append(f"  {icon} Step {s.step_id}: {s.description}")
        return "\n".join(lines)


@dataclass
class AgentRunResult:
    """Agent 运行的最终结果（统一 ReAct 和 PlanExec 的返回格式）。"""

    answer: str
    agent_type: AgentType
    plan: ExecutionPlan | None = None   # PlanExec 专用
    total_iterations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

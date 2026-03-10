"""Agent 管理 API Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from my_agent.domain.agent.base import AgentType


class AgentCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    agent_type: AgentType = AgentType.REACT
    description: str = ""
    max_iterations: int = Field(default=10, ge=1, le=30)
    tool_timeout: float = Field(default=30.0, ge=1.0)
    max_plan_steps: int = Field(default=8, ge=1, le=20)
    enable_replanning: bool = True


class AgentInfo(BaseModel):
    id: str
    name: str
    agent_type: AgentType
    description: str
    max_iterations: int
    max_plan_steps: int
    enable_replanning: bool


class AgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="任务目标")
    session_id: str | None = None
    stream: bool = True


class PlanStepInfo(BaseModel):
    step_id: int
    description: str
    tool_hint: str
    status: str
    result: str


class AgentRunResponse(BaseModel):
    agent_id: str
    agent_type: AgentType
    answer: str
    plan: list[PlanStepInfo] | None = None
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)

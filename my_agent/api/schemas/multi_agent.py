"""多 Agent 协作 API Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MultiAgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="协作目标")
    scenario: str = Field(
        default="custom",
        description="预置场景: research_report / data_analysis / custom",
    )
    # custom 场景下的 Agent 列表
    agents: list[dict[str, Any]] = Field(
        default_factory=list,
        description="自定义 Agent 规格列表（scenario=custom 时生效）",
    )
    mode: str = Field(
        default="sequential",
        description="协作模式: sequential / parallel / hierarchical",
    )
    stream: bool = True


class AgentEventInfo(BaseModel):
    event_type: str
    agent_name: str = ""
    message: str = ""
    result: str = ""


class MultiAgentRunResponse(BaseModel):
    scenario: str
    mode: str
    final_answer: str
    agent_events: list[AgentEventInfo] = Field(default_factory=list)

"""多 Agent 协作 API Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AgentSpecInput(BaseModel):
    """多 Agent 运行时可接受的 Agent 规格。"""

    name: str = Field(..., min_length=1)
    role: str = Field(default="worker")
    system_prompt: str = Field(default="")
    description: str = Field(default="")
    tools: list[str] = Field(default_factory=list)
    max_iterations: int = Field(default=6, ge=1, le=20)


class MultiAgentRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, description="协作目标")
    session_id: str | None = Field(default=None, description="会话 ID，为空则自动创建")
    run_id: str | None = Field(default=None, description="运行 ID，为空则自动生成")
    thread_id: str | None = Field(default=None, description="LangGraph thread_id；为空则默认等于 run_id")
    context: str = Field(default="", description="额外注入的业务上下文")
    scenario: str = Field(
        default="custom",
        description="预置场景: research_report / data_analysis / custom",
    )
    agents: list[AgentSpecInput] = Field(
        default_factory=list,
        description="自定义 Agent 规格列表（scenario=custom 时生效）",
    )
    mode: str = Field(
        default="sequential",
        description="协作模式: sequential / parallel / hierarchical / supervisor",
    )
    stream: bool = True
    pause_before_handoff: bool = Field(default=False, description="每个 handoff 合入共享上下文前暂停")
    approval_before_handoff: bool = Field(default=False, description="每个 handoff 合入共享上下文前需要审批")
    pause_before_answer: bool = Field(default=False, description="最终答案交付前暂停")
    approval_before_answer: bool = Field(default=False, description="最终答案交付前需要审批")


class ResumeMultiAgentRunRequest(BaseModel):
    action: str = Field(default="resume", description="resume/approve/reject/cancel")
    feedback: str = Field(default="", description="审批反馈或恢复备注")
    stream: bool = Field(default=False, description="是否流式返回恢复后的执行过程")


class AgentEventInfo(BaseModel):
    event_type: str
    agent_name: str = ""
    message: str = ""
    result: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class MultiAgentRunResponse(BaseModel):
    session_id: str
    run_id: str
    thread_id: str
    scenario: str
    mode: str
    status: str = "completed"
    final_answer: str = ""
    checkpoint_id: str = ""
    pause_reason: str = ""
    requires_approval: bool = False
    next_nodes: list[str] = Field(default_factory=list)
    agent_events: list[AgentEventInfo] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

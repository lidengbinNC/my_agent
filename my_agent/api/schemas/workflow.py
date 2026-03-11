"""工作流 API Schema。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from my_agent.domain.workflow.models import EdgeCondition, NodeType


class NodeDefRequest(BaseModel):
    node_id: str
    name: str
    node_type: NodeType
    config: dict[str, Any] = Field(default_factory=dict)
    description: str = ""
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0.0, "y": 0.0})


class EdgeDefRequest(BaseModel):
    source: str
    target: str
    condition: EdgeCondition = EdgeCondition.DEFAULT
    condition_expr: str = ""
    label: str = ""


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., max_length=100)
    description: str = ""
    nodes: list[NodeDefRequest] = Field(default_factory=list)
    edges: list[EdgeDefRequest] = Field(default_factory=list)


class WorkflowInfo(BaseModel):
    workflow_id: str
    name: str
    description: str
    node_count: int
    edge_count: int


class WorkflowRunRequest(BaseModel):
    goal: str = Field(..., min_length=1)
    resume_run_id: str | None = None    # 断点恢复：传入已有 run_id
    stream: bool = True


class NodeRunInfo(BaseModel):
    node_id: str
    status: str
    output: str = ""
    error: str = ""
    human_token: str = ""


class WorkflowRunInfo(BaseModel):
    run_id: str
    workflow_id: str
    status: str
    goal: str
    node_runs: dict[str, NodeRunInfo] = Field(default_factory=dict)


class HumanApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""

"""工作流 DAG 领域模型。

面试考点:
  - DAG（有向无环图）：节点 = 任务，边 = 依赖关系，无环保证执行有序
  - 四种节点类型：
      AgentNode   — 调用 ReAct / PlanExec Agent 完成智能任务
      ToolNode    — 直接调用单个工具（轻量，无需 LLM）
      ConditionNode — 基于上一节点输出做条件路由（if/else 分支）
      HumanNode   — Human-in-the-Loop，暂停等待人工审批
  - 边的条件（EdgeCondition）：default（无条件）/ on_success / on_failure / expr（表达式）
  - WorkflowRun / NodeRun 记录执行状态，支持断点恢复
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class NodeType(str, Enum):
    AGENT = "agent"         # 智能 Agent 节点
    TOOL = "tool"           # 工具调用节点
    CONDITION = "condition" # 条件分支节点
    HUMAN = "human"         # 人工审批节点
    START = "start"         # 开始节点（占位）
    END = "end"             # 结束节点（占位）


class NodeStatus(str, Enum):
    PENDING = "pending"     # 等待执行
    RUNNING = "running"     # 执行中
    SUCCESS = "success"     # 执行成功
    FAILED = "failed"       # 执行失败
    SKIPPED = "skipped"     # 被条件路由跳过
    WAITING = "waiting"     # Human 节点等待审批
    APPROVED = "approved"   # Human 节点已审批通过
    REJECTED = "rejected"   # Human 节点审批拒绝


class EdgeCondition(str, Enum):
    DEFAULT = "default"         # 无条件，前节点成功就执行
    ON_SUCCESS = "on_success"   # 前节点成功时走此边
    ON_FAILURE = "on_failure"   # 前节点失败时走此边
    EXPR = "expr"               # 自定义表达式（在 condition_expr 中定义）


@dataclass
class NodeDef:
    """节点定义（工作流设计阶段）。"""

    node_id: str                        # 节点唯一 ID
    name: str                           # 显示名称
    node_type: NodeType                 # 节点类型
    config: dict[str, Any] = field(default_factory=dict)
    # AgentNode: {"agent_type": "react", "prompt": "..."}
    # ToolNode:  {"tool_name": "calculator", "tool_args": {...}}
    # ConditionNode: {"condition_expr": "output contains '成功'"}
    # HumanNode: {"prompt": "请审批以下内容", "timeout_seconds": 3600}
    description: str = ""
    position: dict[str, float] = field(default_factory=lambda: {"x": 0.0, "y": 0.0})


@dataclass
class EdgeDef:
    """边定义（节点间的依赖关系）。"""

    edge_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    source: str = ""                    # 源节点 node_id
    target: str = ""                    # 目标节点 node_id
    condition: EdgeCondition = EdgeCondition.DEFAULT
    condition_expr: str = ""            # EXPR 类型时的表达式
    label: str = ""                     # 边标签（如 "是" / "否"）


@dataclass
class WorkflowDef:
    """工作流定义（DAG 图结构）。"""

    workflow_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "新工作流"
    description: str = ""
    nodes: list[NodeDef] = field(default_factory=list)
    edges: list[EdgeDef] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def node_map(self) -> dict[str, NodeDef]:
        return {n.node_id: n for n in self.nodes}

    def successors(self, node_id: str) -> list[tuple[EdgeDef, NodeDef]]:
        """返回某节点的所有后继节点（含边定义）。"""
        nm = self.node_map()
        return [
            (e, nm[e.target])
            for e in self.edges
            if e.source == node_id and e.target in nm
        ]

    def predecessors(self, node_id: str) -> list[str]:
        """返回某节点的所有前驱节点 ID。"""
        return [e.source for e in self.edges if e.target == node_id]


@dataclass
class NodeRun:
    """节点的单次运行记录。"""

    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    input_data: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    human_token: str = ""       # HumanNode 用于审批的 token


@dataclass
class WorkflowRun:
    """工作流的单次运行状态（支持断点恢复）。"""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    goal: str = ""                                  # 触发工作流的原始目标
    status: NodeStatus = NodeStatus.PENDING
    node_runs: dict[str, NodeRun] = field(default_factory=dict)  # node_id -> NodeRun
    context: dict[str, Any] = field(default_factory=dict)        # 节点间共享的上下文数据
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def get_node_run(self, node_id: str) -> NodeRun:
        if node_id not in self.node_runs:
            self.node_runs[node_id] = NodeRun(node_id=node_id)
        return self.node_runs[node_id]

    def is_complete(self) -> bool:
        return self.status in (NodeStatus.SUCCESS, NodeStatus.FAILED, NodeStatus.REJECTED)

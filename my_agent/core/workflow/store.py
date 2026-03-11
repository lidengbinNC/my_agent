"""工作流状态持久化 — 内存 + JSON 序列化（支持断点恢复）。

面试考点:
  - 断点恢复原理：WorkflowRun 中每个 NodeRun 记录 status，
    引擎启动前检查已成功节点直接跳过（状态恢复即重跑安全）
  - 生产级方案：将 WorkflowDef / WorkflowRun 序列化为 JSON 存入数据库
  - 本实现：内存 dict + dataclass_to_dict 序列化，演示核心思路
  - 幂等性：节点执行器应设计为幂等（重复执行结果一致），
    是断点恢复的前提条件
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from my_agent.domain.workflow.models import (
    EdgeCondition,
    EdgeDef,
    NodeDef,
    NodeRun,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRun,
)


def _serialize(obj: Any) -> Any:
    """递归序列化 dataclass 和枚举。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):   # Enum
        return obj.value
    return obj


def workflow_def_to_dict(wd: WorkflowDef) -> dict:
    return _serialize(wd)


def workflow_run_to_dict(wr: WorkflowRun) -> dict:
    return _serialize(wr)


def workflow_run_from_dict(data: dict) -> WorkflowRun:
    """从 dict 还原 WorkflowRun（断点恢复入口）。"""
    node_runs: dict[str, NodeRun] = {}
    for nid, nr_data in data.get("node_runs", {}).items():
        node_runs[nid] = NodeRun(
            node_id=nr_data["node_id"],
            status=NodeStatus(nr_data.get("status", "pending")),
            output=nr_data.get("output", ""),
            error=nr_data.get("error", ""),
            human_token=nr_data.get("human_token", ""),
        )

    return WorkflowRun(
        run_id=data["run_id"],
        workflow_id=data.get("workflow_id", ""),
        goal=data.get("goal", ""),
        status=NodeStatus(data.get("status", "pending")),
        node_runs=node_runs,
        context=data.get("context", {}),
    )


class WorkflowStore:
    """内存工作流存储（生产环境替换为数据库实现）。

    面试时可说明：生产中将 to_json() 持久化到数据库，
    恢复时 from_json() 反序列化，引擎自动跳过已完成节点。
    """

    def __init__(self) -> None:
        self._defs: dict[str, WorkflowDef] = {}
        self._runs: dict[str, WorkflowRun] = {}

    # ── WorkflowDef CRUD ─────────────────────────────────────────

    def save_def(self, wd: WorkflowDef) -> None:
        self._defs[wd.workflow_id] = wd

    def get_def(self, workflow_id: str) -> WorkflowDef | None:
        return self._defs.get(workflow_id)

    def list_defs(self) -> list[WorkflowDef]:
        return list(self._defs.values())

    def delete_def(self, workflow_id: str) -> bool:
        return self._defs.pop(workflow_id, None) is not None

    # ── WorkflowRun CRUD ─────────────────────────────────────────

    def save_run(self, wr: WorkflowRun) -> None:
        self._runs[wr.run_id] = wr

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def list_runs(self, workflow_id: str | None = None) -> list[WorkflowRun]:
        runs = list(self._runs.values())
        if workflow_id:
            runs = [r for r in runs if r.workflow_id == workflow_id]
        return runs

    # ── 序列化（演示断点恢复）────────────────────────────────────

    def export_run_json(self, run_id: str) -> str | None:
        wr = self._runs.get(run_id)
        if wr is None:
            return None
        return json.dumps(workflow_run_to_dict(wr), ensure_ascii=False, indent=2)

    def import_run_json(self, json_str: str) -> WorkflowRun:
        data = json.loads(json_str)
        wr = workflow_run_from_dict(data)
        self._runs[wr.run_id] = wr
        return wr


# 全局单例
_store = WorkflowStore()


def get_workflow_store() -> WorkflowStore:
    return _store

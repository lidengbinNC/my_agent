"""异步任务队列数据模型。

面试考点:
  - 任务状态机：PENDING → RUNNING → COMPLETED / FAILED / CANCELLED
  - 任务类型：Agent 对话任务 / 批量评估任务
  - 持久化策略：内存字典（开发） + SQLite/Redis（生产）
  - 幂等性：task_id 唯一，防止重复提交
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field
import uuid


class TaskStatus(str, Enum):
    PENDING = "pending"       # 等待执行
    RUNNING = "running"       # 执行中
    COMPLETED = "completed"   # 执行完成
    FAILED = "failed"         # 执行失败
    CANCELLED = "cancelled"   # 已取消


class TaskType(str, Enum):
    AGENT_CHAT = "agent_chat"         # 单次 Agent 对话（长时间运行）
    EVAL_SINGLE = "eval_single"       # 单任务评估
    EVAL_BATCH = "eval_batch"         # 批量评估
    EVAL_COMPARE = "eval_compare"     # 对比评估


class TaskCreate(BaseModel):
    """创建任务请求。"""
    task_type: TaskType
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)  # 1=最高优先级
    timeout_seconds: int = Field(default=300, ge=10, le=3600)


class TaskRecord(BaseModel):
    """任务记录（持久化单元）。"""
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 5
    timeout_seconds: int = 300

    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None

    # 进度信息（用于 WebSocket 推送）
    progress: int = 0          # 0-100
    progress_message: str = ""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def elapsed_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at or datetime.utcnow()
        return round((end - self.started_at).total_seconds(), 2)

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "status": self.status.value,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "elapsed_seconds": self.elapsed_seconds,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }

    def to_full_dict(self) -> dict[str, Any]:
        d = self.to_status_dict()
        d["result"] = self.result
        return d

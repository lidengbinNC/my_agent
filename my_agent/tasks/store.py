"""任务状态持久化存储。

面试考点:
  - 内存存储（开发阶段）：dict + asyncio.Lock 保证并发安全
  - 生产扩展：可替换为 Redis（HSET 存储 + SUBSCRIBE 推送）或 SQLite
  - Repository 模式：存储层与业务逻辑解耦
"""

from __future__ import annotations

import asyncio
from typing import Optional

from my_agent.tasks.models import TaskRecord, TaskStatus
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class TaskStore:
    """任务状态存储（内存实现，线程安全）。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, task: TaskRecord) -> None:
        async with self._lock:
            self._tasks[task.task_id] = task

    async def get(self, task_id: str) -> Optional[TaskRecord]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(
        self,
        status: TaskStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaskRecord]:
        async with self._lock:
            tasks = list(self._tasks.values())

        if status:
            tasks = [t for t in tasks if t.status == status]

        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[offset: offset + limit]

    async def cancel(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.CANCELLED
                return True
            return False

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            tasks = list(self._tasks.values())
        result: dict[str, int] = {}
        for status in TaskStatus:
            result[status.value] = sum(1 for t in tasks if t.status == status)
        result["total"] = len(tasks)
        return result


# 全局单例
_task_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store

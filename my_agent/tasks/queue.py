"""异步任务队列 — asyncio.Queue + 工作者池。

面试考点:
  - asyncio.PriorityQueue：按优先级调度任务（数值越小优先级越高）
  - Worker Pool：多个协程并发消费队列，控制并发数
  - 生产者-消费者模式：API 提交任务（生产者），Worker 执行（消费者）
  - 背压控制：maxsize 限制队列长度，防止内存溢出
  - 优雅关闭：sentinel 哨兵值通知 Worker 退出
  - 超时控制：asyncio.wait_for 防止单个任务阻塞 Worker
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Coroutine

from my_agent.tasks.models import TaskRecord, TaskStatus
from my_agent.tasks.store import TaskStore, get_task_store
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# 哨兵值，通知 Worker 退出
_SENTINEL = object()


class TaskQueue:
    """基于 asyncio.PriorityQueue 的异步任务队列。

    队列元素格式：(priority, task_id)
    优先级数值越小越先执行（PriorityQueue 最小堆）。
    """

    def __init__(
        self,
        store: TaskStore | None = None,
        num_workers: int = 3,
        maxsize: int = 100,
    ) -> None:
        self._store = store or get_task_store()
        self._num_workers = num_workers
        # PriorityQueue: 元素 (priority, sequence, task_id)
        # sequence 保证相同优先级时按提交顺序执行（FIFO）
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=maxsize)
        self._sequence = 0
        self._handlers: dict[str, Callable[..., Coroutine]] = {}
        self._workers: list[asyncio.Task] = []
        self._running = False
        # WebSocket 推送回调 {task_id: [callback]}
        self._ws_callbacks: dict[str, list[Callable]] = {}

    def register_handler(
        self,
        task_type: str,
        handler: Callable[[TaskRecord, "TaskQueue"], Coroutine],
    ) -> None:
        """注册任务类型处理器。"""
        self._handlers[task_type] = handler

    async def submit(self, task: TaskRecord) -> str:
        """提交任务到队列，返回 task_id。"""
        await self._store.save(task)
        self._sequence += 1
        await self._queue.put((task.priority, self._sequence, task.task_id))
        logger.info("task_submitted", task_id=task.task_id, type=task.task_type.value)
        return task.task_id

    async def start(self) -> None:
        """启动 Worker 池。"""
        if self._running:
            return
        self._running = True
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"task-worker-{i}")
            for i in range(self._num_workers)
        ]
        logger.info("task_queue_started", workers=self._num_workers)

    async def stop(self) -> None:
        """优雅关闭：发送哨兵值，等待 Worker 退出。"""
        if not self._running:
            return
        self._running = False
        for _ in self._workers:
            await self._queue.put((0, 0, _SENTINEL))
        await asyncio.gather(*self._workers, return_exceptions=True)
        logger.info("task_queue_stopped")

    async def _worker(self, worker_id: int) -> None:
        """Worker 协程：持续从队列取任务并执行。"""
        logger.info("worker_started", worker_id=worker_id)
        while True:
            try:
                _, _, task_id = await self._queue.get()
            except Exception:
                break

            if task_id is _SENTINEL:
                self._queue.task_done()
                break

            await self._execute(task_id, worker_id)
            self._queue.task_done()

        logger.info("worker_stopped", worker_id=worker_id)

    async def _execute(self, task_id: str, worker_id: int) -> None:
        """执行单个任务。"""
        task = await self._store.get(task_id)
        if task is None or task.status == TaskStatus.CANCELLED:
            return

        # 标记为运行中
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow()
        await self._store.save(task)
        await self._notify_ws(task)

        handler = self._handlers.get(task.task_type.value)
        if handler is None:
            task.status = TaskStatus.FAILED
            task.error = f"No handler for task type: {task.task_type.value}"
            task.finished_at = datetime.utcnow()
            await self._store.save(task)
            return

        try:
            await asyncio.wait_for(
                handler(task, self),
                timeout=task.timeout_seconds,
            )
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.COMPLETED
        except asyncio.TimeoutError:
            task.status = TaskStatus.FAILED
            task.error = f"Task timed out after {task.timeout_seconds}s"
            logger.warning("task_timeout", task_id=task_id)
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error("task_failed", task_id=task_id, error=str(e))
        finally:
            task.finished_at = datetime.utcnow()
            if task.progress < 100 and task.status == TaskStatus.COMPLETED:
                task.progress = 100
            await self._store.save(task)
            await self._notify_ws(task)
            logger.info(
                "task_executed",
                task_id=task_id,
                status=task.status.value,
                worker=worker_id,
                elapsed=task.elapsed_seconds,
            )

    async def update_progress(
        self,
        task: TaskRecord,
        progress: int,
        message: str = "",
    ) -> None:
        """更新任务进度并推送 WebSocket 通知。"""
        task.progress = min(progress, 99)  # 完成前最多 99%
        task.progress_message = message
        await self._store.save(task)
        await self._notify_ws(task)

    def subscribe_ws(self, task_id: str, callback: Callable) -> None:
        """注册 WebSocket 推送回调。"""
        self._ws_callbacks.setdefault(task_id, []).append(callback)

    def unsubscribe_ws(self, task_id: str, callback: Callable) -> None:
        callbacks = self._ws_callbacks.get(task_id, [])
        if callback in callbacks:
            callbacks.remove(callback)

    async def _notify_ws(self, task: TaskRecord) -> None:
        """通知所有订阅该任务的 WebSocket 连接。"""
        callbacks = self._ws_callbacks.get(task.task_id, [])
        for cb in list(callbacks):
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(task.to_status_dict())
                else:
                    cb(task.to_status_dict())
            except Exception as e:
                logger.warning("ws_notify_error", task_id=task.task_id, error=str(e))


# 全局单例
_task_queue: TaskQueue | None = None


def get_task_queue() -> TaskQueue:
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
    return _task_queue

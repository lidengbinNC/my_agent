"""任务 API — POST 提交 + GET 轮询 + WebSocket 推送。

面试考点:
  - 异步任务模式：提交即返回 task_id，客户端轮询或 WebSocket 订阅
  - WebSocket：FastAPI 原生支持，双向通信，实时推送任务进度
  - 轮询 vs WebSocket：轮询简单但有延迟，WebSocket 实时但需维护连接
  - 任务幂等性：同一 task_id 只执行一次
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from my_agent.tasks.models import TaskCreate, TaskRecord, TaskStatus
from my_agent.tasks.queue import get_task_queue
from my_agent.tasks.store import get_task_store
from my_agent.utils.logger import get_logger

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = get_logger(__name__)


@router.post("", summary="提交异步任务")
async def submit_task(body: TaskCreate) -> JSONResponse:
    """提交任务到队列，立即返回 task_id。

    客户端可通过 GET /tasks/{task_id} 轮询状态，
    或通过 WS /tasks/{task_id}/ws 订阅实时进度。
    """
    task = TaskRecord(
        task_type=body.task_type,
        payload=body.payload,
        priority=body.priority,
        timeout_seconds=body.timeout_seconds,
    )
    queue = get_task_queue()
    task_id = await queue.submit(task)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "status": TaskStatus.PENDING.value,
            "message": "任务已提交，请通过 task_id 查询状态",
        },
    )


@router.get("", summary="列出任务")
async def list_tasks(
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> JSONResponse:
    store = get_task_store()
    status_filter = TaskStatus(status) if status else None
    tasks = await store.list_tasks(status=status_filter, limit=limit, offset=offset)
    return JSONResponse(content={
        "tasks": [t.to_status_dict() for t in tasks],
        "total": len(tasks),
    })


@router.get("/stats", summary="任务统计")
async def task_stats() -> JSONResponse:
    store = get_task_store()
    stats = await store.stats()
    return JSONResponse(content=stats)


@router.get("/{task_id}", summary="查询任务状态（轮询）")
async def get_task(task_id: str) -> JSONResponse:
    """轮询任务状态。

    - status=pending/running：任务未完成，继续轮询
    - status=completed：任务完成，result 字段包含结果
    - status=failed：任务失败，error 字段包含错误信息
    """
    store = get_task_store()
    task = await store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")

    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        return JSONResponse(content=task.to_full_dict())
    return JSONResponse(content=task.to_status_dict())


@router.delete("/{task_id}", summary="取消任务")
async def cancel_task(task_id: str) -> JSONResponse:
    store = get_task_store()
    cancelled = await store.cancel(task_id)
    if not cancelled:
        task = await store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        raise HTTPException(
            status_code=400,
            detail=f"任务状态为 {task.status.value}，只有 pending 状态可以取消",
        )
    return JSONResponse(content={"message": "任务已取消", "task_id": task_id})


@router.websocket("/{task_id}/ws")
async def task_progress_ws(websocket: WebSocket, task_id: str) -> None:
    """WebSocket 实时推送任务进度。

    面试考点:
      - WebSocket 生命周期：accept → 收发消息 → close
      - 任务完成后自动关闭连接
      - 心跳机制：客户端发送 ping，服务端回复 pong
    """
    await websocket.accept()
    store = get_task_store()

    # 检查任务是否存在
    task = await store.get(task_id)
    if task is None:
        await websocket.send_text(json.dumps({"error": f"任务不存在: {task_id}"}))
        await websocket.close()
        return

    # 如果任务已完成，直接推送结果
    if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
        await websocket.send_text(json.dumps(task.to_full_dict()))
        await websocket.close()
        return

    # 注册推送回调
    queue = get_task_queue()
    done_event = asyncio.Event()

    async def push_update(data: dict) -> None:
        try:
            await websocket.send_text(json.dumps(data, default=str))
            if data.get("status") in (
                TaskStatus.COMPLETED.value,
                TaskStatus.FAILED.value,
                TaskStatus.CANCELLED.value,
            ):
                done_event.set()
        except Exception:
            done_event.set()

    queue.subscribe_ws(task_id, push_update)

    # 推送当前状态
    await websocket.send_text(json.dumps(task.to_status_dict(), default=str))

    try:
        # 同时监听：任务完成 或 客户端断开
        async def listen_client() -> None:
            while True:
                try:
                    msg = await websocket.receive_text()
                    if msg == "ping":
                        await websocket.send_text(json.dumps({"type": "pong"}))
                except WebSocketDisconnect:
                    done_event.set()
                    break

        await asyncio.gather(
            done_event.wait(),
            listen_client(),
            return_exceptions=True,
        )
    finally:
        queue.unsubscribe_ws(task_id, push_update)
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("ws_closed", task_id=task_id)

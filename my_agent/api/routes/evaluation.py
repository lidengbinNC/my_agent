"""评估 API — 触发评估任务、查看数据集、获取报告。

面试考点:
  - 评估 API 设计：同步（小数据集直接返回）vs 异步（大数据集提交任务队列）
  - 数据集查询：支持按难度/类别过滤
  - 评估报告：JSON 格式，包含所有指标和对比分析
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from my_agent.evaluation.dataset import get_benchmark_dataset
from my_agent.evaluation.models import AgentTypeLabel, TaskDifficulty
from my_agent.tasks.models import TaskCreate, TaskRecord, TaskType
from my_agent.tasks.queue import get_task_queue
from my_agent.utils.logger import get_logger

router = APIRouter(prefix="/eval", tags=["evaluation"])
logger = get_logger(__name__)


# ── 数据集查询 ────────────────────────────────────────────────────

@router.get("/dataset", summary="查看评估数据集")
async def list_dataset(
    difficulty: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> JSONResponse:
    diff = TaskDifficulty(difficulty) if difficulty else None
    tasks = get_benchmark_dataset(difficulty=diff, category=category, limit=limit)
    return JSONResponse(content={
        "total": len(tasks),
        "tasks": [
            {
                "task_id": t.task_id,
                "question": t.question,
                "difficulty": t.difficulty.value,
                "category": t.category,
                "expected_tools": t.expected_tools,
                "tags": t.tags,
            }
            for t in tasks
        ],
    })


# ── 快速同步评估（单任务，用于调试）────────────────────────────────

class QuickEvalRequest(BaseModel):
    eval_task_id: str
    agent_type: str = "react"


@router.post("/quick", summary="快速评估单个任务（同步，适合调试）")
async def quick_eval(body: QuickEvalRequest) -> JSONResponse:
    """直接运行评估，同步返回结果（不经过任务队列）。

    注意：可能耗时较长，生产环境建议使用异步接口 POST /eval/batch。
    """
    from my_agent.core.dependencies import get_react_engine, get_llm_client
    from my_agent.evaluation.judge import LLMJudge
    from my_agent.evaluation.runner import EvalRunner

    tasks = get_benchmark_dataset()
    eval_task = next((t for t in tasks if t.task_id == body.eval_task_id), None)
    if eval_task is None:
        raise HTTPException(status_code=404, detail=f"评估任务不存在: {body.eval_task_id}")

    llm = get_llm_client()
    judge = LLMJudge(llm)
    react_engine = get_react_engine()
    runner = EvalRunner(react_engine, judge)

    agent_type = AgentTypeLabel(body.agent_type)
    result = await runner.evaluate_single(eval_task, agent_type)
    return JSONResponse(content=result.to_dict())


# ── 异步批量评估 ──────────────────────────────────────────────────

class BatchEvalRequest(BaseModel):
    difficulty: str | None = None
    category: str | None = None
    limit: int = Field(default=10, ge=1, le=22)
    agent_type: str = "react"
    timeout_seconds: int = Field(default=600, ge=60, le=3600)


@router.post("/batch", summary="提交批量评估任务（异步）")
async def submit_batch_eval(body: BatchEvalRequest) -> JSONResponse:
    """提交批量评估到任务队列，返回 task_id。

    通过 GET /tasks/{task_id} 轮询状态，或 WS /tasks/{task_id}/ws 订阅进度。
    """
    task = TaskRecord(
        task_type=TaskType.EVAL_BATCH,
        payload={
            "difficulty": body.difficulty,
            "category": body.category,
            "limit": body.limit,
            "agent_type": body.agent_type,
        },
        priority=7,
        timeout_seconds=body.timeout_seconds,
    )
    queue = get_task_queue()
    task_id = await queue.submit(task)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "message": f"批量评估已提交，共约 {body.limit} 个任务",
        },
    )


# ── 对比评估 ──────────────────────────────────────────────────────

class CompareEvalRequest(BaseModel):
    difficulty: str = "easy"
    limit: int = Field(default=5, ge=1, le=10)
    timeout_seconds: int = Field(default=900, ge=60, le=3600)


@router.post("/compare", summary="提交 ReAct vs Plan-and-Execute 对比评估（异步）")
async def submit_compare_eval(body: CompareEvalRequest) -> JSONResponse:
    """对比评估：同一数据集分别运行 ReAct 和 Plan-and-Execute，生成对比报告。"""
    task = TaskRecord(
        task_type=TaskType.EVAL_COMPARE,
        payload={
            "difficulty": body.difficulty,
            "limit": body.limit,
        },
        priority=8,
        timeout_seconds=body.timeout_seconds,
    )
    queue = get_task_queue()
    task_id = await queue.submit(task)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "message": "对比评估已提交（ReAct vs Plan-and-Execute）",
        },
    )

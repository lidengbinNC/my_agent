"""客服融合场景路由。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from my_agent.api.routes.chat import chat_completions
from my_agent.api.schemas.chat import ChatRequest
from my_agent.api.schemas.customer_service import (
    CustomerServiceCopilotRequest,
    CustomerServiceFeedbackRequest,
    CustomerServiceTaskRequest,
)
from my_agent.core.dependencies import get_react_engine
from my_agent.core.engine.react_engine import ReActEngine
from my_agent.domain.customer_service import (
    build_customer_service_message,
    default_approval_before_tools,
    get_customer_service_baseline,
    resolve_skill_name,
)
from my_agent.infrastructure.db.database import get_db
from my_agent.infrastructure.db.models import ApprovalRecordModel, MessageModel, ToolCallModel
from my_agent.infrastructure.db.repository import CustomerServiceFeedbackRepository
from my_agent.tasks.models import TaskRecord, TaskType
from my_agent.tasks.queue import get_task_queue

router = APIRouter(prefix="/customer-service", tags=["customer-service"])


@router.get("/baseline")
async def get_baseline():
    return get_customer_service_baseline()


@router.post("/copilot")
async def customer_service_copilot(
    body: CustomerServiceCopilotRequest,
    engine: ReActEngine = Depends(get_react_engine),
    db: AsyncSession = Depends(get_db),
):
    context = body.customer_context.model_dump()
    if body.session_id and not context.get("session_id"):
        context["session_id"] = body.session_id
    enriched_message = build_customer_service_message(
        body.message,
        context=context,
        mode=body.mode,
        allow_write_actions=body.allow_write_actions,
    )
    chat_request = ChatRequest(
        message=enriched_message,
        session_id=body.session_id,
        stream=body.stream,
        skill=resolve_skill_name(body.mode, body.skill),
        approval_before_tools=default_approval_before_tools(body.mode, body.allow_write_actions),
        approval_before_answer=body.approval_before_answer,
    )
    return await chat_completions(chat_request, engine=engine, db=db)


@router.post("/tasks")
async def submit_customer_service_task(body: CustomerServiceTaskRequest) -> JSONResponse:
    queue = get_task_queue()
    task = TaskRecord(
        task_type=TaskType(body.task_type),
        payload={
            "message": body.message,
            "mode": body.mode,
            "allow_write_actions": body.allow_write_actions,
            "customer_context": body.customer_context.model_dump(),
        },
    )
    task_id = await queue.submit(task)
    return JSONResponse(
        status_code=202,
        content={
            "task_id": task_id,
            "status": "pending",
            "task_type": body.task_type,
        },
    )


@router.post("/feedback")
async def record_customer_service_feedback(
    body: CustomerServiceFeedbackRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = CustomerServiceFeedbackRepository(db)
    item = await repo.add(
        session_id=body.session_id,
        run_id=body.run_id,
        customer_id=body.customer_id,
        knowledge_domain=body.knowledge_domain,
        adopted=body.adopted,
        rating=body.rating,
        feedback_type=body.feedback_type,
        feedback_text=body.feedback_text,
        metadata_json=json.dumps(body.metadata, ensure_ascii=False),
    )
    return {"feedback_id": item.id, "status": "recorded"}


@router.get("/feedback")
async def list_customer_service_feedback(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    repo = CustomerServiceFeedbackRepository(db)
    items = await repo.list_recent(limit=limit)
    return {
        "items": [
            {
                "id": item.id,
                "session_id": item.session_id,
                "run_id": item.run_id,
                "customer_id": item.customer_id,
                "knowledge_domain": item.knowledge_domain,
                "adopted": item.adopted,
                "rating": item.rating,
                "feedback_type": item.feedback_type,
                "feedback_text": item.feedback_text or "",
                "metadata": json.loads(item.metadata_json) if item.metadata_json else {},
                "created_at": item.created_at,
            }
            for item in items
        ],
        "total": len(items),
    }


@router.get("/audit/{session_id}")
async def get_customer_service_audit(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    msg_result = await db.execute(
        select(MessageModel)
        .where(MessageModel.session_id == session_id)
        .order_by(MessageModel.created_at.asc())
    )
    messages = list(msg_result.scalars().all())
    tool_result = await db.execute(
        select(ToolCallModel)
        .join(MessageModel, MessageModel.id == ToolCallModel.message_id)
        .where(MessageModel.session_id == session_id)
        .order_by(ToolCallModel.created_at.asc())
    )
    approvals = await db.execute(
        select(ApprovalRecordModel)
        .where(ApprovalRecordModel.session_id == session_id)
        .order_by(ApprovalRecordModel.created_at.asc())
    )
    return {
        "session_id": session_id,
        "messages": [
            {
                "id": item.id,
                "role": item.role,
                "content": item.content or "",
                "created_at": item.created_at,
            }
            for item in messages
        ],
        "tool_calls": [
            {
                "id": item.id,
                "tool_name": item.tool_name,
                "arguments": item.arguments,
                "result": item.result or "",
                "is_error": item.is_error,
                "created_at": item.created_at,
            }
            for item in tool_result.scalars().all()
        ],
        "approvals": [
            {
                "id": item.id,
                "run_id": item.run_id,
                "stage": item.stage,
                "decision": item.decision,
                "feedback": item.feedback or "",
                "created_at": item.created_at,
            }
            for item in approvals.scalars().all()
        ],
    }

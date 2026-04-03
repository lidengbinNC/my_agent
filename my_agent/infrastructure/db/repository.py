"""Repository 模式 — 封装数据访问逻辑，隔离业务层与 ORM。

面试考点:
  - Repository 模式：将数据访问逻辑集中管理，业务层只依赖接口
  - 异步查询：select + scalars().all() / scalar_one_or_none()
  - 软删除：查询时过滤 is_deleted=True，保留历史数据
  - 关联加载：selectinload 避免 N+1 查询问题
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from my_agent.domain.llm.message import Message, MessageRole
from my_agent.infrastructure.db.models import (
    ApprovalRecordModel,
    CustomerServiceFeedbackModel,
    MessageModel,
    SessionModel,
    ToolCallModel,
)
from my_agent.utils.token_counter import count_tokens


class SessionRepository:
    """会话数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, title: str = "新对话", memory_type: str = "window") -> SessionModel:
        session = SessionModel(
            id=str(uuid.uuid4()),
            title=title,
            memory_type=memory_type,
        )
        self._db.add(session)
        await self._db.flush()
        return session

    async def get_by_id(self, session_id: str) -> SessionModel | None:
        result = await self._db.execute(
            select(SessionModel)
            .where(SessionModel.id == session_id, SessionModel.is_deleted == False)  # noqa: E712
        )
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[SessionModel]:
        result = await self._db.execute(
            select(SessionModel)
            .where(SessionModel.is_deleted == False)  # noqa: E712
            .order_by(SessionModel.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_title(self, session_id: str, title: str) -> None:
        await self._db.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(title=title, updated_at=datetime.utcnow())
        )

    async def soft_delete(self, session_id: str) -> None:
        await self._db.execute(
            update(SessionModel)
            .where(SessionModel.id == session_id)
            .values(is_deleted=True, updated_at=datetime.utcnow())
        )


class MessageRepository:
    """消息数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        session_id: str,
        role: str,
        content: str | None,
    ) -> MessageModel:
        token_cnt = count_tokens(content) if content else 0
        msg = MessageModel(
            session_id=session_id,
            role=role,
            content=content,
            token_count=token_cnt,
        )
        self._db.add(msg)
        await self._db.flush()
        return msg

    async def get_history(
        self, session_id: str, limit: int = 100
    ) -> list[MessageModel]:
        result = await self._db.execute(
            select(MessageModel)
            .where(MessageModel.session_id == session_id)
            .options(selectinload(MessageModel.tool_calls))
            .order_by(MessageModel.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def to_domain_messages(self, session_id: str, limit: int = 50) -> list[Message]:
        """将 ORM 消息转换为领域 Message 对象（用于注入 Prompt）。"""
        rows = await self.get_history(session_id, limit=limit)
        messages: list[Message] = []
        for row in rows:
            try:
                role = MessageRole(row.role)
            except ValueError:
                continue
            messages.append(Message(role=role, content=row.content))
        return messages


class ToolCallRepository:
    """工具调用记录数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def record(
        self,
        message_id: int,
        tool_name: str,
        arguments: str,
        result: str | None = None,
        is_error: bool = False,
        duration_ms: int = 0,
    ) -> ToolCallModel:
        tc = ToolCallModel(
            message_id=message_id,
            tool_name=tool_name,
            arguments=arguments,
            result=result,
            is_error=is_error,
            duration_ms=duration_ms,
        )
        self._db.add(tc)
        await self._db.flush()
        return tc


class ApprovalRecordRepository:
    """审批记录数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        run_id: str,
        session_id: str,
        checkpoint_id: str,
        stage: str,
        decision: str,
        feedback: str = "",
    ) -> ApprovalRecordModel:
        record = ApprovalRecordModel(
            run_id=run_id,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            stage=stage,
            decision=decision,
            feedback=feedback,
        )
        self._db.add(record)
        await self._db.flush()
        return record

    async def list_by_run_id(self, run_id: str) -> list[ApprovalRecordModel]:
        result = await self._db.execute(
            select(ApprovalRecordModel)
            .where(ApprovalRecordModel.run_id == run_id)
            .order_by(ApprovalRecordModel.created_at.asc())
        )
        return list(result.scalars().all())


class CustomerServiceFeedbackRepository:
    """客服反馈数据访问对象。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def add(
        self,
        *,
        session_id: str,
        run_id: str,
        customer_id: str,
        knowledge_domain: str,
        adopted: bool,
        rating: int,
        feedback_type: str,
        feedback_text: str = "",
        metadata_json: str = "",
    ) -> CustomerServiceFeedbackModel:
        item = CustomerServiceFeedbackModel(
            session_id=session_id,
            run_id=run_id,
            customer_id=customer_id,
            knowledge_domain=knowledge_domain,
            adopted=adopted,
            rating=rating,
            feedback_type=feedback_type,
            feedback_text=feedback_text,
            metadata_json=metadata_json,
        )
        self._db.add(item)
        await self._db.flush()
        return item

    async def list_recent(self, limit: int = 50) -> list[CustomerServiceFeedbackModel]:
        result = await self._db.execute(
            select(CustomerServiceFeedbackModel)
            .order_by(CustomerServiceFeedbackModel.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

"""SQLAlchemy ORM 模型定义。

面试考点:
  - 异步 ORM：SQLAlchemy 2.x async_session + AsyncEngine
  - 关系设计：Session 1-N Message，Message 1-N ToolCall
  - 软删除：is_deleted 字段，避免物理删除导致历史数据丢失
  - 时间戳：created_at / updated_at 自动维护
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class SessionModel(Base):
    """对话会话表。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    memory_type: Mapped[str] = mapped_column(String(20), default="window")  # buffer/window/summary
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[MessageModel]] = relationship(
        "MessageModel", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Session id={self.id!r} title={self.title!r}>"


class MessageModel(Base):
    """消息记录表。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))  # user / assistant / system / tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped[SessionModel] = relationship("SessionModel", back_populates="messages")
    tool_calls: Mapped[list[ToolCallModel]] = relationship(
        "ToolCallModel", back_populates="message", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role!r} session={self.session_id!r}>"


class ToolCallModel(Base):
    """工具调用记录表（用于审计和回放）。"""

    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    arguments: Mapped[str] = mapped_column(Text, default="{}")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, default=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    message: Mapped[MessageModel] = relationship("MessageModel", back_populates="tool_calls")

    def __repr__(self) -> str:
        return f"<ToolCall id={self.id} tool={self.tool_name!r}>"


class ApprovalRecordModel(Base):
    """审批记录表（用于高风险工具和最终答复审计）。"""

    __tablename__ = "approval_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(120), index=True)
    session_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    checkpoint_id: Mapped[str] = mapped_column(String(120), default="")
    stage: Mapped[str] = mapped_column(String(50), default="tool_gate")  # tool_gate / final_gate
    decision: Mapped[str] = mapped_column(String(20), default="approved")
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<ApprovalRecord run_id={self.run_id!r} stage={self.stage!r} decision={self.decision!r}>"


class CustomerServiceFeedbackModel(Base):
    """客服 Copilot 反馈表。"""

    __tablename__ = "customer_service_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    run_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    customer_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    knowledge_domain: Mapped[str] = mapped_column(String(64), default="faq")
    adopted: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int] = mapped_column(Integer, default=0)
    feedback_type: Mapped[str] = mapped_column(String(50), default="reply_suggestion")
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<CustomerServiceFeedback id={self.id} session={self.session_id!r} type={self.feedback_type!r}>"


class MultiAgentRunModel(Base):
    """多 Agent 运行主表。"""

    __tablename__ = "multi_agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    thread_id: Mapped[str] = mapped_column(String(120), default="", index=True)
    session_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    scenario: Mapped[str] = mapped_column(String(50), default="custom")
    mode: Mapped[str] = mapped_column(String(30), default="sequential")
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="running")
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    events: Mapped[list["MultiAgentEventModel"]] = relationship(
        "MultiAgentEventModel",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<MultiAgentRun run_id={self.run_id!r} status={self.status!r}>"


class MultiAgentEventModel(Base):
    """多 Agent 关键事件审计表。"""

    __tablename__ = "multi_agent_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("multi_agent_runs.run_id", ondelete="CASCADE"),
        index=True,
    )
    agent_name: Mapped[str] = mapped_column(String(80), default="", index=True)
    event_type: Mapped[str] = mapped_column(String(50), default="message", index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped[MultiAgentRunModel] = relationship("MultiAgentRunModel", back_populates="events")

    def __repr__(self) -> str:
        return f"<MultiAgentEvent run_id={self.run_id!r} type={self.event_type!r}>"

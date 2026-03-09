"""会话管理 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    title: str = Field(default="新对话", max_length=200)
    memory_type: str = Field(default="window", pattern="^(buffer|window|summary)$")


class SessionInfo(BaseModel):
    id: str
    title: str
    memory_type: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MessageInfo(BaseModel):
    id: int
    role: str
    content: str | None
    token_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SessionHistory(BaseModel):
    session: SessionInfo
    messages: list[MessageInfo]
    total_tokens: int

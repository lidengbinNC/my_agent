"""对话相关 Pydantic Schema — 请求 / 响应 / SSE 事件模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求。"""

    message: str = Field(..., min_length=1, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID，为空则新建")
    stream: bool = Field(default=True, description="是否流式输出")


class SSEEventType(str, Enum):
    """SSE 事件类型 — Agent 思考过程的结构化事件。"""

    THINKING = "thinking"  # Agent 正在思考
    CONTENT = "content"  # 输出内容块
    TOOL_CALL = "tool_call"  # 发起工具调用
    TOOL_RESULT = "tool_result"  # 工具返回结果
    DONE = "done"  # 完成
    ERROR = "error"  # 错误


class SSEEvent(BaseModel):
    """SSE 推送的单个事件。"""

    event: SSEEventType
    data: dict[str, Any] = Field(default_factory=dict)

    def to_sse(self) -> str:
        import json

        return f"event: {self.event.value}\ndata: {json.dumps(self.data, ensure_ascii=False)}\n\n"


class ChatResponse(BaseModel):
    """非流式对话响应。"""

    session_id: str
    content: str
    usage: dict[str, int] = Field(default_factory=dict)
    model: str = ""

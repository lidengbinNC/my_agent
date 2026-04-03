"""对话相关 Pydantic Schema — 请求 / 响应 / SSE 事件模型。"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求。"""

    message: str = Field(..., min_length=1, description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID，为空则新建")
    run_id: str | None = Field(default=None, description="本次执行 ID，为空则自动生成")
    stream: bool = Field(default=True, description="是否流式输出")
    skill: str | None = Field(default=None, description="指定 Skill 名称；为空则自动匹配")
    pause_before_tools: bool = Field(default=False, description="每次工具执行前暂停")
    pause_before_answer: bool = Field(default=False, description="最终答案输出前暂停")
    approval_before_tools: bool = Field(default=False, description="每次工具执行前需要审批")
    approval_before_answer: bool = Field(default=False, description="最终答案输出前需要审批")


class ResumeRunRequest(BaseModel):
    """恢复已暂停的运行。"""

    action: str = Field(default="resume", description="resume/approve/reject/cancel")
    feedback: str = Field(default="", description="审批反馈或恢复备注")
    stream: bool = Field(default=False, description="是否流式返回恢复后的执行过程")


class SSEEventType(str, Enum):
    """SSE 事件类型 — Agent 思考过程的结构化事件。"""

    THINKING = "thinking"  # Agent 正在思考
    CONTENT = "content"  # 输出内容块
    TOOL_CALL = "tool_call"  # 发起工具调用
    TOOL_RESULT = "tool_result"  # 工具返回结果
    PAUSED = "paused"  # 执行暂停，等待恢复/审批
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
    run_id: str = ""
    status: str = "completed"
    content: str
    checkpoint_id: str = ""
    pause_reason: str = ""
    requires_approval: bool = False
    pending_node: str = ""
    next_nodes: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, int] = Field(default_factory=dict)
    model: str = ""
    skill: str | None = None

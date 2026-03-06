"""消息模型 — 覆盖 OpenAI Chat Completion 消息协议的四种角色。

面试考点:
  - System / User / Assistant / Tool 四种角色的语义
  - Tool Call 消息的双阶段结构: Assistant(tool_calls) → Tool(result)
  - 消息序列化到 OpenAI API 格式
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCallInfo(BaseModel):
    """一次工具调用的信息，对应 OpenAI tool_calls 数组中的一项。"""

    id: str
    name: str
    arguments: str  # JSON 字符串


class Message(BaseModel):
    """统一消息模型。"""

    role: MessageRole
    content: str | None = None
    name: str | None = None

    # Assistant 消息可能包含 tool_calls
    tool_calls: list[ToolCallInfo] | None = None

    # Tool 消息必须关联 tool_call_id
    tool_call_id: str | None = None

    def to_openai_dict(self) -> dict[str, Any]:
        """序列化为 OpenAI API 消息格式。"""
        d: dict[str, Any] = {"role": self.role.value}

        if self.content is not None:
            d["content"] = self.content

        if self.name is not None:
            d["name"] = self.name

        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in self.tool_calls
            ]

        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id

        return d


# ----- 便捷构造函数 -----


def SystemMessage(content: str) -> Message:
    return Message(role=MessageRole.SYSTEM, content=content)


def UserMessage(content: str) -> Message:
    return Message(role=MessageRole.USER, content=content)


def AssistantMessage(
    content: str | None = None,
    tool_calls: list[ToolCallInfo] | None = None,
) -> Message:
    return Message(role=MessageRole.ASSISTANT, content=content, tool_calls=tool_calls)


def ToolMessage(content: str, tool_call_id: str) -> Message:
    return Message(role=MessageRole.TOOL, content=content, tool_call_id=tool_call_id)

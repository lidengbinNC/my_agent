"""LLM 客户端基类 — 策略模式，支持多实现切换。

面试考点:
  - 抽象基类 + 策略模式（方便切换不同 LLM 提供商）
  - 流式 vs 非流式 两种调用方式
  - Tool Call 的解析（从 LLM 响应中提取工具调用信息）
  - AsyncIterator 用于流式输出
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from my_agent.domain.llm.message import Message, ToolCallInfo


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    """非流式 LLM 调用的完整响应。"""

    content: str | None = None
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    model: str = ""
    finish_reason: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


@dataclass
class StreamChunk:
    """流式输出的单个块。"""

    delta_content: str = ""
    # 流式中工具调用是增量拼接的，完整结果在流结束后汇总
    tool_call_chunks: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: TokenUsage | None = None


class BaseLLMClient(ABC):
    """LLM 客户端抽象基类。"""

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """非流式调用。"""
        ...

    @abstractmethod
    async def stream_chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """流式调用，逐块返回。"""
        ...

    @abstractmethod
    async def close(self) -> None:
        ...

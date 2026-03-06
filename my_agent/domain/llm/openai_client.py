"""OpenAI 兼容 LLM 客户端 — 支持 Tool Call 解析和流式输出。

面试考点:
  - httpx 异步 HTTP 客户端（连接池复用、超时控制）
  - OpenAI Chat Completion API 协议（messages / tools / stream）
  - Tool Call 从流式 chunk 中增量拼接的实现
  - 指数退避重试策略
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from my_agent.domain.llm.base import BaseLLMClient, LLMResponse, StreamChunk, TokenUsage
from my_agent.domain.llm.message import Message, ToolCallInfo
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class OpenAIClient(BaseLLMClient):
    """兼容所有 OpenAI 接口标准的 LLM 客户端（通义千问 / DeepSeek / OpenAI 等）。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int = 60,
        max_retries: int = 3,
    ) -> None:
        self.model = model
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    # ==================== 非流式调用 ====================

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
        payload = self._build_payload(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, stream=False,
            response_format=response_format, **kwargs,
        )

        data = await self._request_with_retry(payload)
        return self._parse_response(data)

    # ==================== 流式调用 ====================

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(
            messages, tools=tools, temperature=temperature,
            max_tokens=max_tokens, stream=True,
            stream_options={"include_usage": True},
            **kwargs,
        )

        async with self._client.stream(
            "POST", "/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk_data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                chunk = self._parse_stream_chunk(chunk_data)
                if chunk:
                    yield chunk

    # ==================== 内部方法 ====================

    def _build_payload(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stream: bool = False,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai_dict() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        payload.update(kwargs)
        return payload

    async def _request_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        """指数退避重试。"""
        import asyncio

        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.TransportError) as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning(
                    "llm_request_retry",
                    attempt=attempt + 1,
                    wait=wait,
                    error=str(e),
                )
                await asyncio.sleep(wait)
        raise RuntimeError(f"LLM 请求失败，已重试 {self._max_retries} 次: {last_err}")

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        choice = data["choices"][0]
        msg = choice["message"]

        tool_calls: list[ToolCallInfo] = []
        if raw_tc := msg.get("tool_calls"):
            for tc in raw_tc:
                tool_calls.append(ToolCallInfo(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                ))

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )

        return LLMResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            usage=usage,
            model=data.get("model", self.model),
            finish_reason=choice.get("finish_reason", ""),
        )

    @staticmethod
    def _parse_stream_chunk(data: dict[str, Any]) -> StreamChunk | None:
        choices = data.get("choices", [])
        if not choices:
            usage_data = data.get("usage")
            if usage_data:
                return StreamChunk(usage=TokenUsage(
                    prompt_tokens=usage_data.get("prompt_tokens", 0),
                    completion_tokens=usage_data.get("completion_tokens", 0),
                ))
            return None

        delta = choices[0].get("delta", {})
        return StreamChunk(
            delta_content=delta.get("content", "") or "",
            tool_call_chunks=delta.get("tool_calls", []),
            finish_reason=choices[0].get("finish_reason"),
        )

    async def close(self) -> None:
        await self._client.aclose()

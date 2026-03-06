"""Agent 对话路由 — 支持 SSE 流式输出思考过程。

面试考点:
  - SSE (Server-Sent Events) 的实现原理
  - 结构化事件: thinking / content / tool_call / tool_result / done / error
  - AsyncGenerator 驱动的流式响应
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from my_agent.api.schemas.chat import (
    ChatRequest,
    ChatResponse,
    SSEEvent,
    SSEEventType,
)
from my_agent.config.settings import settings
from my_agent.core.dependencies import get_llm_client
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import Message, SystemMessage, UserMessage
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])

_DEFAULT_SYSTEM_PROMPT = (
    "你是 MyAgent，一个智能助手。你能够分析问题、调用工具、完成用户的各种任务。"
    "回答时请简洁准确，使用中文。"
)


@router.post("/chat/completions")
async def chat_completions(
    req: ChatRequest,
    llm: BaseLLMClient = Depends(get_llm_client),
):
    session_id = req.session_id or str(uuid.uuid4())
    system_prompt = settings.system_prompt or _DEFAULT_SYSTEM_PROMPT
    messages: list[Message] = [
        SystemMessage(system_prompt),
        UserMessage(req.message),
    ]

    if req.stream:
        return StreamingResponse(
            _stream_response(llm, messages, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式
    response = await llm.chat(messages)
    return ChatResponse(
        session_id=session_id,
        content=response.content or "",
        usage={
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        model=response.model,
    )


async def _stream_response(
    llm: BaseLLMClient,
    messages: list[Message],
    session_id: str,
) -> AsyncGenerator[str, None]:
    """SSE 流式生成器。"""
    # 发送 thinking 事件
    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={"session_id": session_id, "message": "正在思考..."},
    ).to_sse()

    try:
        full_content = ""
        async for chunk in llm.stream_chat(messages):
            if chunk.delta_content:
                full_content += chunk.delta_content
                yield SSEEvent(
                    event=SSEEventType.CONTENT,
                    data={"delta": chunk.delta_content},
                ).to_sse()

            if chunk.finish_reason == "stop":
                usage_data = {}
                if chunk.usage:
                    usage_data = {
                        "prompt_tokens": chunk.usage.prompt_tokens,
                        "completion_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    }
                yield SSEEvent(
                    event=SSEEventType.DONE,
                    data={
                        "session_id": session_id,
                        "content": full_content,
                        "usage": usage_data,
                    },
                ).to_sse()
                return

        # 流正常结束但没有 finish_reason=stop
        yield SSEEvent(
            event=SSEEventType.DONE,
            data={"session_id": session_id, "content": full_content},
        ).to_sse()

    except Exception as e:
        logger.error("stream_error", error=str(e))
        yield SSEEvent(
            event=SSEEventType.ERROR,
            data={"error": str(e)},
        ).to_sse()

"""Agent 对话路由 — ReAct 引擎驱动，SSE 实时推送思考过程。

面试考点:
  - SSE 结构化事件: thinking / action / observation / final_answer / error
  - ReAct 引擎 AsyncGenerator 驱动流式推送
  - 非流式模式: 收集所有步骤后一次性返回
"""

from __future__ import annotations

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
from my_agent.core.dependencies import get_react_engine
from my_agent.core.engine.react_engine import ReActEngine, ReActStepType

router = APIRouter(tags=["chat"])


@router.post("/chat/completions")
async def chat_completions(
    req: ChatRequest,
    engine: ReActEngine = Depends(get_react_engine),
):
    session_id = req.session_id or str(uuid.uuid4())

    if req.stream:
        return StreamingResponse(
            _stream_react(engine, req.message, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # 非流式：收集所有步骤
    final_answer = ""
    steps_data = []
    async for step in engine.run(req.message):
        steps_data.append({"type": step.type.value, "iteration": step.iteration})
        if step.type == ReActStepType.FINAL_ANSWER:
            final_answer = step.answer
        elif step.type == ReActStepType.ERROR:
            final_answer = f"[错误] {step.error}"

    return ChatResponse(
        session_id=session_id,
        content=final_answer,
        usage={},
    )


async def _stream_react(
    engine: ReActEngine,
    query: str,
    session_id: str,
) -> AsyncGenerator[str, None]:
    """将 ReAct 步骤转换为 SSE 事件流。"""
    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={"session_id": session_id, "message": "Agent 启动，开始推理..."},
    ).to_sse()

    try:
        async for step in engine.run(query):
            if step.type == ReActStepType.THINKING:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={
                        "iteration": step.iteration,
                        "message": f"第 {step.iteration} 步：思考中...",
                    },
                ).to_sse()

            elif step.type == ReActStepType.ACTION:
                yield SSEEvent(
                    event=SSEEventType.TOOL_CALL,
                    data={
                        "iteration": step.iteration,
                        "thought": step.thought,
                        "tool": step.action,
                        "args": step.action_input,
                    },
                ).to_sse()

            elif step.type == ReActStepType.OBSERVATION:
                yield SSEEvent(
                    event=SSEEventType.TOOL_RESULT,
                    data={
                        "iteration": step.iteration,
                        "tool": step.action,
                        "result": step.observation,
                    },
                ).to_sse()

            elif step.type == ReActStepType.FINAL_ANSWER:
                # 先推送 thought
                if step.thought:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"iteration": step.iteration, "message": step.thought},
                    ).to_sse()
                # 逐字推送最终答案（模拟流式）
                answer = step.answer
                chunk_size = 10
                for i in range(0, len(answer), chunk_size):
                    yield SSEEvent(
                        event=SSEEventType.CONTENT,
                        data={"delta": answer[i: i + chunk_size]},
                    ).to_sse()
                yield SSEEvent(
                    event=SSEEventType.DONE,
                    data={"session_id": session_id, "content": answer},
                ).to_sse()
                return

            elif step.type == ReActStepType.ERROR:
                yield SSEEvent(
                    event=SSEEventType.ERROR,
                    data={"error": step.error, "iteration": step.iteration},
                ).to_sse()
                return

    except Exception as e:
        yield SSEEvent(
            event=SSEEventType.ERROR,
            data={"error": str(e)},
        ).to_sse()

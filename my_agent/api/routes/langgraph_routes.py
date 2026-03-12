"""LangGraph API 路由 — 暴露 LangGraph 实现供前端调用和对比。

面试考点:
  - 同一接口背后可以切换自研/LangGraph 实现（策略模式）
  - 流式输出：LangGraph astream() 事件流转为 SSE
"""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from my_agent.utils.logger import get_logger

router = APIRouter(prefix="/langgraph", tags=["langgraph"])
logger = get_logger(__name__)


class LGChatRequest(BaseModel):
    question: str
    mode: str = Field(default="react", description="react / plan_execute / sequential / supervisor")
    stream: bool = False


@router.post("/chat", summary="LangGraph Agent 对话", response_model=None)
async def langgraph_chat(body: LGChatRequest) -> JSONResponse | StreamingResponse:
    """使用 LangGraph 实现运行 Agent，支持流式输出。"""
    if body.stream:
        return StreamingResponse(
            _stream_langgraph(body.question, body.mode),
            media_type="text/event-stream",
        )

    try:
        if body.mode == "react":
            from langgraph_impl.react_agent import run_react_agent
            answer = await run_react_agent(body.question)
            return JSONResponse(content={"mode": "react", "answer": answer})

        elif body.mode == "plan_execute":
            from langgraph_impl.plan_execute import run_plan_execute_agent
            result = await run_plan_execute_agent(body.question)
            return JSONResponse(content={"mode": "plan_execute", **result})

        elif body.mode == "sequential":
            from langgraph_impl.multi_agent import run_sequential_agents
            result = await run_sequential_agents(body.question)
            return JSONResponse(content={"mode": "sequential", **result})

        elif body.mode == "supervisor":
            from langgraph_impl.multi_agent import run_supervisor_agents
            result = await run_supervisor_agents(body.question)
            return JSONResponse(content={"mode": "supervisor", **result})

        else:
            return JSONResponse(
                status_code=400,
                content={"error": f"未知模式: {body.mode}，可选: react/plan_execute/sequential/supervisor"},
            )
    except Exception as e:
        logger.error("langgraph_chat_error", mode=body.mode, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


async def _stream_langgraph(question: str, mode: str):
    """LangGraph 流式输出生成器。"""
    try:
        if mode == "react":
            from langgraph_impl.react_agent import stream_react_agent
            async for event in stream_react_agent(question):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        else:
            yield f"data: {json.dumps({'error': f'流式模式暂不支持 {mode}'})}\n\n"
    except Exception as e:
        yield f"data: {json.dumps({'error': str(e)})}\n\n"
    yield "data: [DONE]\n\n"


@router.get("/comparison", summary="自研 vs LangGraph 代码量对比")
async def get_comparison() -> JSONResponse:
    """返回自研引擎与 LangGraph 实现的代码量对比数据。"""
    from langgraph_impl.react_agent import code_comparison
    return JSONResponse(content=code_comparison())


@router.get("/graph/react", summary="获取 ReAct Graph 结构（Mermaid 格式）")
async def get_react_graph_structure() -> JSONResponse:
    """返回 LangGraph ReAct 图的结构描述。"""
    try:
        from langgraph_impl.react_agent import compile_react_graph
        app = compile_react_graph()
        mermaid = app.get_graph().draw_mermaid()
        return JSONResponse(content={"mermaid": mermaid})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/checkpoint/demo", summary="运行 Checkpoint 多轮对话演示")
async def checkpoint_demo() -> JSONResponse:
    """演示 LangGraph Checkpoint 多轮对话记忆。"""
    try:
        from langgraph_impl.checkpoint_demo import demo_multi_turn_conversation
        results = await demo_multi_turn_conversation()
        return JSONResponse(content={"conversations": results})
    except Exception as e:
        logger.error("checkpoint_demo_error", error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})

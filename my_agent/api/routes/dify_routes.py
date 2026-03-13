"""Dify 集成 API 路由。

面试考点:
  - 代理模式：MyAgent 作为 Dify 的代理，统一对外暴露 AI 能力
  - 健康检查：检测 Dify 是否可用，实现优雅降级
  - 双向集成：MyAgent 调用 Dify + Dify 调用 MyAgent 工具
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from my_agent.utils.logger import get_logger

router = APIRouter(prefix="/dify", tags=["dify"])
logger = get_logger(__name__)


class DifyChatRequest(BaseModel):
    query: str
    conversation_id: str = ""
    app_type: str = Field(default="chat", description="chat / workflow")
    stream: bool = False


@router.post("/chat", summary="通过 MyAgent 调用 Dify 应用", response_model=None)
async def dify_chat(body: DifyChatRequest) -> JSONResponse | StreamingResponse:
    """将请求转发给 Dify 应用，支持流式和同步两种模式。"""
    from dify_integration.client import create_dify_client

    client = create_dify_client()

    if not await client.health_check():
        await client.close()
        return JSONResponse(
            status_code=503,
            content={
                "error": "Dify 服务不可用",
                "hint": "请确认 Dify 已启动：docker-compose --profile dify up -d",
            },
        )

    try:
        if body.stream:
            async def _stream():
                import json
                async for chunk in client.chat_stream(
                    query=body.query,
                    conversation_id=body.conversation_id,
                ):
                    yield f"data: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                await client.close()

            return StreamingResponse(_stream(), media_type="text/event-stream")

        if body.app_type == "workflow":
            response = await client.run_workflow(inputs={"query": body.query})
            await client.close()
            return JSONResponse(content={
                "workflow_run_id": response.workflow_run_id,
                "status": response.status,
                "outputs": response.outputs,
                "elapsed_time": response.elapsed_time,
            })
        else:
            response = await client.chat(
                query=body.query,
                conversation_id=body.conversation_id,
            )
            await client.close()
            return JSONResponse(content={
                "answer": response.answer,
                "conversation_id": response.conversation_id,
                "usage": response.usage,
            })
    except Exception as e:
        await client.close()
        logger.error("dify_route_error", error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get("/health", summary="检查 Dify 服务状态")
async def dify_health() -> JSONResponse:
    from dify_integration.client import create_dify_client
    client = create_dify_client()
    available = await client.health_check()
    await client.close()
    return JSONResponse(content={
        "dify_available": available,
        "message": "Dify 服务正常" if available else "Dify 服务不可用（未启动或未配置）",
    })


@router.get("/datasets", summary="列出 Dify 知识库")
async def list_datasets() -> JSONResponse:
    from dify_integration.client import create_dify_client
    client = create_dify_client()
    try:
        datasets = await client.list_datasets()
        return JSONResponse(content={"datasets": datasets, "total": len(datasets)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        await client.close()

"""MCP API 路由 — SSE 传输端点 + 管理接口。

面试考点:
  - SSE 端点：GET /mcp/sse 建立长连接，服务端推送响应
  - 消息端点：POST /mcp/messages 接收客户端请求
  - 管理接口：查看工具列表、连接外部 MCP Server
  - Cursor 配置：通过 stdio 传输直接启动本服务
"""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from mcp_impl.server import get_mcp_server
from mcp_impl.transport import get_sse_transport
from my_agent.utils.logger import get_logger

router = APIRouter(prefix="/mcp", tags=["mcp"])
logger = get_logger(__name__)


# ── SSE 传输端点 ──────────────────────────────────────────────────

@router.get("/sse", summary="建立 MCP SSE 连接（供 MCP 客户端使用）")
async def mcp_sse(
    session_id: str | None = Query(default=None),
) -> StreamingResponse:
    """建立 SSE 长连接，MCP 客户端通过此端点接收服务端响应。

    面试考点：
      - SSE 是 HTTP 长连接，服务端主动推送
      - 每个连接有唯一 session_id
      - 客户端通过 POST /mcp/messages 发送请求，通过 SSE 接收响应
    """
    sid = session_id or str(uuid.uuid4())[:8]
    transport = get_sse_transport()
    await transport.create_session(sid)

    return StreamingResponse(
        transport.sse_generator(sid),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/messages", summary="发送 MCP JSON-RPC 消息")
async def mcp_messages(
    body: dict,
    session_id: str | None = Query(default=None),
) -> JSONResponse:
    """接收 MCP 客户端发送的 JSON-RPC 请求，将响应推入 SSE 队列。

    面试考点：
      - 请求通过 POST 发送，响应通过 SSE 异步推送
      - session_id 关联请求和 SSE 连接
      - 无 session_id 时直接同步处理（兼容简单客户端）
    """
    transport = get_sse_transport()
    server = get_mcp_server()

    if session_id and session_id in transport._sessions:
        # 异步模式：将响应推入 SSE 队列
        await transport.handle_message(session_id, body)
        return JSONResponse(content={"status": "accepted"}, status_code=202)
    else:
        # 同步模式：直接返回响应（兼容无 SSE 的客户端）
        response = await server.handle_request(body)
        if response is None:
            return JSONResponse(content={"status": "notification_received"})
        return JSONResponse(content=response)


# ── 管理接口 ──────────────────────────────────────────────────────

@router.get("/tools", summary="列出 MCP Server 暴露的工具")
async def list_mcp_tools() -> JSONResponse:
    """列出 MCP Server 暴露的所有工具（对应 tools/list 方法）。"""
    server = get_mcp_server()
    result = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    })
    tools = result.get("result", {}).get("tools", []) if result else []
    return JSONResponse(content={"tools": tools, "total": len(tools)})


@router.get("/resources", summary="列出 MCP Server 暴露的资源")
async def list_mcp_resources() -> JSONResponse:
    """列出 MCP Server 暴露的所有资源（对应 resources/list 方法）。"""
    server = get_mcp_server()
    result = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "resources/list",
        "params": {},
    })
    resources = result.get("result", {}).get("resources", []) if result else []
    return JSONResponse(content={"resources": resources, "total": len(resources)})


class ToolCallRequest(BaseModel):
    name: str
    arguments: dict = {}


@router.post("/tools/call", summary="直接调用 MCP 工具（测试用）")
async def call_mcp_tool(body: ToolCallRequest) -> JSONResponse:
    """直接调用 MCP 工具，用于测试和调试。"""
    server = get_mcp_server()
    result = await server.handle_request({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": body.name, "arguments": body.arguments},
    })
    if result is None:
        raise HTTPException(status_code=500, detail="No response from MCP server")
    return JSONResponse(content=result)


# ── MCP Client 管理 ───────────────────────────────────────────────

class ConnectRequest(BaseModel):
    name: str
    server_url: str


@router.post("/client/connect", summary="连接外部 MCP Server")
async def connect_mcp_server(body: ConnectRequest) -> JSONResponse:
    """连接到外部 MCP Server，自动发现并注册其工具。

    面试考点：
      - 运行时动态连接外部 MCP Server
      - 工具自动注册到内部 ToolRegistry
      - Agent 无需重启即可使用新工具
    """
    from mcp_impl.client import get_mcp_manager
    manager = get_mcp_manager()
    try:
        client = await manager.connect(body.name, body.server_url)
        tools = client.get_tools()
        # 注册到内部工具系统
        count = manager.register_all_tools()
        return JSONResponse(content={
            "message": f"已连接到 {body.name}，发现 {len(tools)} 个工具",
            "tools": [t.name for t in tools],
            "registered": count,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接失败: {e}")


@router.get("/client/connections", summary="列出已连接的 MCP Server")
async def list_mcp_connections() -> JSONResponse:
    from mcp_impl.client import get_mcp_manager
    manager = get_mcp_manager()
    return JSONResponse(content={"connections": manager.list_connections()})


@router.get("/cursor-config", summary="获取 Cursor MCP 配置示例")
async def get_cursor_config() -> JSONResponse:
    """返回 Cursor MCP 配置示例，可直接复制到 ~/.cursor/mcp.json。

    面试考点：
      - stdio 传输：Cursor 直接启动 Python 进程作为 MCP Server
      - SSE 传输：Cursor 连接已运行的 HTTP 服务
    """
    import os
    cwd = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    return JSONResponse(content={
        "stdio_config": {
            "mcpServers": {
                "myagent": {
                    "command": "python",
                    "args": ["-m", "mcp_impl.transport"],
                    "cwd": cwd,
                    "env": {
                        "LLM_API_KEY": "your-api-key",
                        "LLM_BASE_URL": "https://api.openai.com/v1",
                        "LLM_MODEL": "gpt-4o-mini",
                    },
                }
            }
        },
        "sse_config": {
            "mcpServers": {
                "myagent-remote": {
                    "url": "http://localhost:8001/api/v1/mcp/sse",
                    "transport": "sse",
                }
            }
        },
        "note": "将 stdio_config 复制到 ~/.cursor/mcp.json 即可在 Cursor 中使用 MyAgent 工具",
    })

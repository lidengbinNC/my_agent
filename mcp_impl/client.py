"""MCP Client — 动态连接外部 MCP Server，自动发现工具并代理调用。

面试考点:
  MCP Client 核心能力:
    1. 连接管理：连接到外部 MCP Server（SSE 传输）
    2. 工具发现：调用 tools/list 获取工具列表
    3. 工具适配：将 MCP Tool 转换为内部 BaseTool（适配器模式）
    4. 工具调用：Agent 调用工具 → MCP Client → 外部 MCP Server

  MCP Client 的价值:
    - 不改代码就能扩展工具：接入任何社区 MCP Server
    - 工具生态复用：GitHub MCP / Slack MCP / Database MCP 等
    - 标准化接口：统一的工具调用方式，无需为每个服务写适配器

  连接方式:
    - SSE 传输：通过 HTTP 连接远程 MCP Server
      GET /mcp/sse → 建立 SSE 连接
      POST /mcp/messages → 发送请求

  与自研工具系统的集成:
    - McpProxyTool：将外部 MCP Tool 包装为内部 BaseTool
    - 注册到 ToolRegistry 后，ReAct Agent 可以透明调用
    - Agent 不需要知道工具是本地实现还是 MCP 代理
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx

from mcp_impl.protocol import (
    JsonRpcRequest,
    McpMethod,
    McpToolDefinition,
)
from my_agent.domain.tool.base import BaseTool, ToolResult
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class McpClient:
    """MCP Client — 连接外部 MCP Server，代理工具调用。

    面试考点：
      - 使用 SSE 传输连接远程 MCP Server
      - 自动发现工具列表（tools/list）
      - 将外部工具包装为内部 BaseTool（适配器模式）
    """

    def __init__(
        self,
        server_url: str,
        timeout: float = 30.0,
    ) -> None:
        self._server_url = server_url.rstrip("/")
        self._timeout = timeout
        self._http = httpx.AsyncClient(timeout=timeout)
        self._session_id: str | None = None
        self._request_counter = 0
        self._pending: dict[int | str, asyncio.Future] = {}
        self._tools: dict[str, McpToolDefinition] = {}
        self._initialized = False
        self._sse_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """连接到 MCP Server，完成握手和工具发现。

        面试考点：
          1. 建立 SSE 连接获取 session_id
          2. 发送 initialize 请求协商协议版本
          3. 发送 notifications/initialized 通知
          4. 调用 tools/list 获取工具列表
        """
        # 步骤1: 建立 SSE 连接
        self._session_id = str(uuid.uuid4())[:8]
        self._sse_task = asyncio.create_task(
            self._listen_sse(),
            name=f"mcp-sse-{self._session_id}",
        )
        await asyncio.sleep(0.1)  # 等待 SSE 连接建立

        # 步骤2: initialize 握手
        init_result = await self._send_request(McpMethod.INITIALIZE, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}, "resources": {}},
            "clientInfo": {"name": "MyAgent MCP Client", "version": "1.0.0"},
        })
        logger.info(
            "mcp_client_initialized",
            server=init_result.get("serverInfo", {}).get("name", ""),
            version=init_result.get("protocolVersion", ""),
        )

        # 步骤3: 发送 initialized 通知
        await self._send_notification(McpMethod.INITIALIZED, {})

        # 步骤4: 发现工具
        await self.refresh_tools()
        self._initialized = True

    async def refresh_tools(self) -> list[McpToolDefinition]:
        """刷新工具列表（动态发现）。"""
        result = await self._send_request(McpMethod.TOOLS_LIST, {})
        tools_data = result.get("tools", [])
        self._tools = {
            t["name"]: McpToolDefinition.from_dict(t)
            for t in tools_data
        }
        logger.info("mcp_tools_discovered", count=len(self._tools))
        return list(self._tools.values())

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用外部 MCP Server 上的工具。

        面试考点：
          - 工具调用通过 POST /mcp/messages 发送 JSON-RPC 请求
          - 响应通过 SSE 连接异步接收
          - 使用 asyncio.Future 实现请求-响应匹配
        """
        result = await self._send_request(McpMethod.TOOLS_CALL, {
            "name": name,
            "arguments": arguments,
        })
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) or str(result)

    async def close(self) -> None:
        """关闭连接。"""
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
        await self._http.aclose()

    def get_tools(self) -> list[McpToolDefinition]:
        return list(self._tools.values())

    def as_proxy_tools(self) -> list["McpProxyTool"]:
        """将所有外部工具转换为内部 BaseTool 代理。"""
        return [McpProxyTool(tool_def, self) for tool_def in self._tools.values()]

    # ── 内部通信 ──────────────────────────────────────────────────

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """发送 JSON-RPC 请求并等待响应。

        面试考点：
          - 使用 asyncio.Future 实现异步请求-响应匹配
          - request_id 用于将响应与请求关联
          - 超时处理：防止服务端无响应时永久阻塞
        """
        self._request_counter += 1
        req_id = self._request_counter

        req = JsonRpcRequest(method=method, params=params, id=req_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._post_message(req.to_dict())
            result = await asyncio.wait_for(future, timeout=self._timeout)
            return result
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP request timed out: {method}")
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def _send_notification(
        self,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """发送 JSON-RPC 通知（无需响应）。"""
        req = JsonRpcRequest(method=method, params=params)
        await self._post_message(req.to_dict())

    async def _post_message(self, data: dict[str, Any]) -> None:
        """通过 POST /mcp/messages 发送消息。"""
        url = f"{self._server_url}/mcp/messages"
        params = {}
        if self._session_id:
            params["session_id"] = self._session_id
        await self._http.post(url, json=data, params=params)

    async def _listen_sse(self) -> None:
        """监听 SSE 连接，接收服务端推送的响应。

        面试考点：
          - SSE 是长连接，服务端主动推送
          - 每条 SSE 消息解析后，根据 id 找到对应的 Future 并 set_result
        """
        url = f"{self._server_url}/mcp/sse"
        params = {}
        if self._session_id:
            params["session_id"] = self._session_id

        try:
            async with self._http.stream("GET", url, params=params) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if not data_str:
                        continue
                    try:
                        data = json.loads(data_str)
                        req_id = data.get("id")
                        if req_id is not None and req_id in self._pending:
                            future = self._pending.pop(req_id)
                            if not future.done():
                                if "error" in data:
                                    future.set_exception(
                                        RuntimeError(data["error"].get("message", "RPC error"))
                                    )
                                else:
                                    future.set_result(data.get("result", {}))
                    except json.JSONDecodeError:
                        continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("mcp_sse_listen_error", error=str(e))


# ── MCP 代理工具（适配器模式）────────────────────────────────────

class McpProxyTool(BaseTool):
    """将外部 MCP Tool 包装为内部 BaseTool。

    面试考点：
      - 适配器模式：将 MCP 接口适配为内部 BaseTool 接口
      - 透明代理：Agent 不需要知道工具是本地还是 MCP
      - 动态注册：运行时从 MCP Server 发现工具并注册
    """

    def __init__(
        self,
        tool_def: McpToolDefinition,
        client: McpClient,
    ) -> None:
        self.name = tool_def.name
        self.description = f"[MCP] {tool_def.description}"
        self.parameters_schema = tool_def.input_schema
        self._client = client

    async def _execute(self, **kwargs: Any) -> ToolResult:
        try:
            output = await self._client.call_tool(self.name, kwargs)
            return ToolResult.ok(output, source="mcp")
        except Exception as e:
            return ToolResult.fail(f"MCP tool call failed: {e}")


# ── MCP Client 管理器 ─────────────────────────────────────────────

class McpClientManager:
    """管理多个 MCP Server 连接。

    面试考点：
      - 支持同时连接多个 MCP Server
      - 工具名称冲突处理：添加 server 前缀
      - 连接池管理：复用连接，避免重复握手
    """

    def __init__(self) -> None:
        self._clients: dict[str, McpClient] = {}

    async def connect(self, name: str, server_url: str) -> McpClient:
        """连接到指定 MCP Server。"""
        if name in self._clients:
            return self._clients[name]

        client = McpClient(server_url)
        await client.connect()
        self._clients[name] = client
        logger.info("mcp_server_connected", name=name, url=server_url)
        return client

    async def disconnect(self, name: str) -> None:
        """断开指定 MCP Server 连接。"""
        client = self._clients.pop(name, None)
        if client:
            await client.close()

    async def disconnect_all(self) -> None:
        """断开所有连接。"""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()

    def register_all_tools(self, prefix_by_server: bool = True) -> int:
        """将所有已连接 MCP Server 的工具注册到内部 ToolRegistry。"""
        from my_agent.domain.tool.registry import get_registry
        registry = get_registry()
        count = 0
        for server_name, client in self._clients.items():
            for proxy_tool in client.as_proxy_tools():
                if prefix_by_server:
                    proxy_tool.name = f"{server_name}__{proxy_tool.name}"
                registry.register(proxy_tool)
                count += 1
        logger.info("mcp_proxy_tools_registered", count=count)
        return count

    def list_connections(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "tools": [t.name for t in client.get_tools()],
                "tool_count": len(client.get_tools()),
            }
            for name, client in self._clients.items()
        ]


# 全局单例
_mcp_manager: McpClientManager | None = None


def get_mcp_manager() -> McpClientManager:
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = McpClientManager()
    return _mcp_manager

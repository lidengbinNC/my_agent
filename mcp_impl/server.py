"""MCP Server — 将 MyAgent 工具暴露为 MCP 标准接口。

面试考点:
  MCP Server 职责:
    - 实现 initialize 握手（协商协议版本 + 能力）
    - 实现 tools/list（返回所有可用工具的 Schema）
    - 实现 tools/call（执行工具并返回结果）
    - 实现 resources/list + resources/read（暴露知识库等数据源）

  MCP Server 架构:
    McpServer（核心逻辑）
      ├── ToolRegistry 适配层（自研工具 → MCP Tool 格式）
      ├── ResourceRegistry（知识库/文件 → MCP Resource）
      └── 请求分发器（method → handler）

  与自研工具系统的关系:
    - MCP Server 是自研工具系统的"标准化门面"
    - 内部仍然使用自研 ToolRegistry 执行工具
    - 对外暴露 MCP 标准接口，任何 MCP 客户端均可调用
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp_impl.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    McpCapabilities,
    McpMethod,
    McpResource,
    McpResourceContent,
    McpServerInfo,
    McpToolCallResult,
    McpToolDefinition,
    RpcErrorCode,
)
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"


class McpServer:
    """MCP Server 核心实现。

    面试考点：
      - 请求分发：method → handler 映射（类似 HTTP 路由）
      - 工具注册：从自研 ToolRegistry 动态加载工具
      - 资源注册：支持自定义资源（知识库、文件系统等）
    """

    def __init__(
        self,
        name: str = "MyAgent MCP Server",
        version: str = "1.0.0",
    ) -> None:
        self._info = McpServerInfo(name=name, version=version)
        self._capabilities = McpCapabilities(tools=True, resources=True)
        self._initialized = False

        # 工具注册表（从自研系统加载）
        self._tools: dict[str, McpToolDefinition] = {}
        # 资源注册表
        self._resources: dict[str, McpResource] = {}
        # 请求处理器映射
        self._handlers: dict[str, Any] = {
            McpMethod.INITIALIZE: self._handle_initialize,
            McpMethod.INITIALIZED: self._handle_initialized,
            McpMethod.TOOLS_LIST: self._handle_tools_list,
            McpMethod.TOOLS_CALL: self._handle_tools_call,
            McpMethod.RESOURCES_LIST: self._handle_resources_list,
            McpMethod.RESOURCES_READ: self._handle_resources_read,
            McpMethod.PING: self._handle_ping,
        }

    # ── 工具 & 资源注册 ───────────────────────────────────────────

    def load_from_registry(self) -> None:
        """从自研 ToolRegistry 加载所有工具（适配器模式）。

        面试考点：适配器将自研 BaseTool 转换为 MCP McpToolDefinition
        """
        import my_agent.domain.tool.builtin  # noqa: F401
        from my_agent.domain.tool.registry import get_registry

        registry = get_registry()
        for tool in registry.all():
            mcp_tool = McpToolDefinition(
                name=tool.name,
                description=tool.description,
                input_schema=tool.parameters_schema,
            )
            self._tools[tool.name] = mcp_tool
            logger.debug("mcp_tool_loaded", name=tool.name)

        logger.info("mcp_tools_loaded", count=len(self._tools))

    def register_resource(self, resource: McpResource) -> None:
        """注册 MCP 资源（知识库、文件等）。"""
        self._resources[resource.uri] = resource
        logger.debug("mcp_resource_registered", uri=resource.uri)

    def register_default_resources(self) -> None:
        """注册默认资源（MyAgent 知识库、系统信息等）。"""
        self.register_resource(McpResource(
            uri="knowledge://myagent/tools",
            name="MyAgent 工具文档",
            description="MyAgent 所有内置工具的使用说明",
            mime_type="text/markdown",
        ))
        self.register_resource(McpResource(
            uri="knowledge://myagent/prompts",
            name="MyAgent Prompt 模板",
            description="MyAgent 内置的 Prompt 模板（ReAct 等）",
            mime_type="text/plain",
        ))

    # ── 请求处理 ──────────────────────────────────────────────────

    async def handle_request(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """处理单个 JSON-RPC 请求，返回响应字典（通知消息返回 None）。"""
        try:
            req = JsonRpcRequest.from_dict(raw)
        except (KeyError, TypeError) as e:
            return JsonRpcResponse.err(
                None, RpcErrorCode.INVALID_REQUEST, f"Invalid request: {e}"
            ).to_dict()

        # 通知消息（无 id）不需要响应
        is_notification = req.id is None

        handler = self._handlers.get(req.method)
        if handler is None:
            if is_notification:
                return None
            return JsonRpcResponse.err(
                req.id, RpcErrorCode.METHOD_NOT_FOUND, f"Method not found: {req.method}"
            ).to_dict()

        try:
            result = await handler(req)
            if is_notification:
                return None
            return JsonRpcResponse.ok(req.id, result).to_dict()
        except McpError as e:
            return JsonRpcResponse.err(req.id, e.code, e.message).to_dict()
        except Exception as e:
            logger.error("mcp_handler_error", method=req.method, error=str(e))
            return JsonRpcResponse.err(
                req.id, RpcErrorCode.INTERNAL_ERROR, str(e)
            ).to_dict()

    # ── 具体处理器 ────────────────────────────────────────────────

    async def _handle_initialize(self, req: JsonRpcRequest) -> dict[str, Any]:
        """处理 initialize 请求 — 协商协议版本和能力。

        面试考点：
          - 客户端发送自己支持的协议版本
          - 服务端返回自己的版本、能力声明、服务器信息
          - 双方协商出共同支持的最低版本
        """
        client_version = req.params.get("protocolVersion", MCP_PROTOCOL_VERSION)
        logger.info("mcp_initialize", client_version=client_version)
        self._initialized = True
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": self._capabilities.to_dict(),
            "serverInfo": self._info.to_dict(),
        }

    async def _handle_initialized(self, req: JsonRpcRequest) -> None:
        """处理 notifications/initialized — 客户端通知初始化完成。"""
        logger.info("mcp_client_initialized")

    async def _handle_tools_list(self, req: JsonRpcRequest) -> dict[str, Any]:
        """处理 tools/list — 返回所有可用工具。

        面试考点：
          - 工具列表是动态的，可以在运行时注册/注销工具
          - cursor 参数支持分页（本实现不分页）
        """
        return {
            "tools": [t.to_dict() for t in self._tools.values()],
        }

    async def _handle_tools_call(self, req: JsonRpcRequest) -> dict[str, Any]:
        """处理 tools/call — 执行指定工具。

        面试考点：
          - 工具调用通过内部 ToolRegistry 执行（不重复实现）
          - 结果转换为 MCP content 格式（支持 text/image/resource）
          - 错误处理：工具不存在 → isError=true，执行失败 → isError=true
        """
        tool_name = req.params.get("name", "")
        arguments = req.params.get("arguments", {})

        if not tool_name:
            raise McpError(RpcErrorCode.INVALID_PARAMS, "Missing tool name")

        if tool_name not in self._tools:
            raise McpError(RpcErrorCode.METHOD_NOT_FOUND, f"Tool not found: {tool_name}")

        # 通过自研 ToolRegistry 执行工具
        from my_agent.domain.tool.registry import get_registry
        registry = get_registry()
        tool = registry.get(tool_name)

        if tool is None:
            return McpToolCallResult.error(f"Tool not available: {tool_name}").to_dict()

        try:
            logger.info("mcp_tool_call", tool=tool_name, args=str(arguments)[:100])
            result = await asyncio.wait_for(
                tool._execute(**arguments),
                timeout=30.0,
            )
            if result.success:
                return McpToolCallResult.text(result.output).to_dict()
            else:
                return McpToolCallResult.error(result.error).to_dict()
        except asyncio.TimeoutError:
            return McpToolCallResult.error(f"Tool {tool_name} timed out").to_dict()
        except Exception as e:
            logger.error("mcp_tool_call_error", tool=tool_name, error=str(e))
            return McpToolCallResult.error(str(e)).to_dict()

    async def _handle_resources_list(self, req: JsonRpcRequest) -> dict[str, Any]:
        """处理 resources/list — 返回所有资源。"""
        return {
            "resources": [r.to_dict() for r in self._resources.values()],
        }

    async def _handle_resources_read(self, req: JsonRpcRequest) -> dict[str, Any]:
        """处理 resources/read — 读取指定资源内容。

        面试考点：
          - URI 格式：scheme://authority/path
          - 不同 URI 对应不同的内容获取逻辑
        """
        uri = req.params.get("uri", "")
        if not uri:
            raise McpError(RpcErrorCode.INVALID_PARAMS, "Missing resource URI")

        if uri not in self._resources:
            raise McpError(RpcErrorCode.METHOD_NOT_FOUND, f"Resource not found: {uri}")

        content = await self._read_resource(uri)
        return {"contents": [content.to_dict()]}

    async def _read_resource(self, uri: str) -> McpResourceContent:
        """根据 URI 读取资源内容。"""
        if uri == "knowledge://myagent/tools":
            from my_agent.domain.tool.registry import get_registry
            registry = get_registry()
            lines = ["# MyAgent 工具文档\n"]
            for t in registry.all():
                lines.append(f"## {t.name}\n{t.description}\n")
                lines.append(f"参数: {t.parameters_schema}\n")
            return McpResourceContent(uri=uri, mime_type="text/markdown", text="\n".join(lines))

        if uri == "knowledge://myagent/prompts":
            from my_agent.domain.prompt.registry import PromptRegistry
            reg = PromptRegistry()
            names = reg.list_prompts()
            text = f"可用 Prompt 模板: {', '.join(names)}"
            return McpResourceContent(uri=uri, mime_type="text/plain", text=text)

        return McpResourceContent(uri=uri, text="Resource content not available")

    async def _handle_ping(self, req: JsonRpcRequest) -> dict[str, Any]:
        """处理 ping — 心跳检测。"""
        return {}


class McpError(Exception):
    """MCP 协议错误。"""
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


# 全局单例
_mcp_server: McpServer | None = None


def get_mcp_server() -> McpServer:
    global _mcp_server
    if _mcp_server is None:
        _mcp_server = McpServer()
        _mcp_server.load_from_registry()
        _mcp_server.register_default_resources()
    return _mcp_server

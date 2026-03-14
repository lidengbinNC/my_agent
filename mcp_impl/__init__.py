"""MCP (Model Context Protocol) 实现模块。

目录结构:
  protocol.py    — JSON-RPC 2.0 消息定义 + MCP 数据结构
  server.py      — MCP Server（工具暴露 + 资源暴露）
  transport.py   — 传输层（stdio + SSE）
  client.py      — MCP Client（动态发现 + 代理调用）

面试话术:
  "我实现了 MCP Server，把 Agent 的工具能力标准化暴露，
   Cursor 和 Claude 都能直接调用；同时实现了 MCP Client，
   让 Agent 能动态接入社区的任何 MCP Server，不改代码就能扩展工具。"
"""

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
)
from mcp_impl.server import McpServer, get_mcp_server
from mcp_impl.client import McpClient, McpClientManager, McpProxyTool, get_mcp_manager

__all__ = [
    "JsonRpcRequest", "JsonRpcResponse", "McpCapabilities", "McpMethod",
    "McpResource", "McpResourceContent", "McpServerInfo",
    "McpToolCallResult", "McpToolDefinition",
    "McpServer", "get_mcp_server",
    "McpClient", "McpClientManager", "McpProxyTool", "get_mcp_manager",
]

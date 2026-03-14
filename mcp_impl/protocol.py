"""MCP 协议消息定义 — 基于 JSON-RPC 2.0。

面试考点:
  MCP（Model Context Protocol）协议架构:
    - 由 Anthropic 于 2024 年底发布的开放标准
    - 解决问题：AI 工具调用碎片化，每个平台有自己的工具调用格式
    - 核心思想：统一工具调用协议，让任何 AI 客户端（Cursor/Claude/自研 Agent）
      都能调用任何 MCP Server 暴露的工具

  JSON-RPC 2.0 基础:
    - 请求格式: {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
    - 响应格式: {"jsonrpc":"2.0","id":1,"result":{...}}
    - 错误格式: {"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"..."}}
    - 通知格式: {"jsonrpc":"2.0","method":"notifications/initialized"} (无 id)

  MCP 方法列表:
    初始化:
      initialize              → 协商协议版本和能力
      notifications/initialized → 客户端通知初始化完成

    工具:
      tools/list              → 列出所有可用工具
      tools/call              → 调用指定工具

    资源:
      resources/list          → 列出所有资源
      resources/read          → 读取指定资源内容
      resources/subscribe     → 订阅资源变更通知

    Prompt:
      prompts/list            → 列出所有 Prompt 模板
      prompts/get             → 获取指定 Prompt

  与 OpenAI Function Calling 对比:
    - Function Calling: 专有协议，只适用于 OpenAI API
    - MCP: 开放标准，任何 AI 客户端/服务器均可实现
    - 自研工具系统: Python 内部调用，无网络开销，但不跨进程
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# ── JSON-RPC 2.0 基础消息 ─────────────────────────────────────────

@dataclass
class JsonRpcRequest:
    """JSON-RPC 2.0 请求消息。"""
    method: str
    params: dict[str, Any] = field(default_factory=dict)
    id: int | str | None = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params:
            d["params"] = self.params
        if self.id is not None:
            d["id"] = self.id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JsonRpcRequest":
        return cls(
            method=data["method"],
            params=data.get("params", {}),
            id=data.get("id"),
            jsonrpc=data.get("jsonrpc", "2.0"),
        )


@dataclass
class JsonRpcResponse:
    """JSON-RPC 2.0 响应消息。"""
    id: int | str | None
    result: Any = None
    error: "JsonRpcError | None" = None
    jsonrpc: str = "2.0"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            d["error"] = self.error.to_dict()
        else:
            d["result"] = self.result
        return d

    @classmethod
    def ok(cls, req_id: Any, result: Any) -> "JsonRpcResponse":
        return cls(id=req_id, result=result)

    @classmethod
    def err(cls, req_id: Any, code: int, message: str, data: Any = None) -> "JsonRpcResponse":
        return cls(id=req_id, error=JsonRpcError(code=code, message=message, data=data))


@dataclass
class JsonRpcError:
    """JSON-RPC 2.0 错误对象。"""
    code: int
    message: str
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = self.data
        return d


# JSON-RPC 2.0 标准错误码
class RpcErrorCode:
    PARSE_ERROR = -32700       # JSON 解析失败
    INVALID_REQUEST = -32600   # 请求格式无效
    METHOD_NOT_FOUND = -32601  # 方法不存在
    INVALID_PARAMS = -32602    # 参数无效
    INTERNAL_ERROR = -32603    # 内部错误


# ── MCP 协议数据结构 ──────────────────────────────────────────────

@dataclass
class McpToolDefinition:
    """MCP 工具定义（对应 tools/list 返回的工具描述）。

    面试考点：与 OpenAI Function Calling 的 tool 格式对比
      OpenAI: {"type":"function","function":{"name":...,"description":...,"parameters":...}}
      MCP:    {"name":...,"description":...,"inputSchema":{...}}
      区别：MCP 使用 inputSchema（JSON Schema），OpenAI 使用 parameters
    """
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "McpToolDefinition":
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            input_schema=data.get("inputSchema", {}),
        )


@dataclass
class McpToolCallResult:
    """MCP 工具调用结果（对应 tools/call 返回）。

    面试考点：MCP 结果支持多种内容类型（text/image/resource）
    """
    content: list[dict[str, Any]] = field(default_factory=list)
    is_error: bool = False

    @classmethod
    def text(cls, text: str) -> "McpToolCallResult":
        return cls(content=[{"type": "text", "text": text}])

    @classmethod
    def error(cls, message: str) -> "McpToolCallResult":
        return cls(
            content=[{"type": "text", "text": f"Error: {message}"}],
            is_error=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"content": self.content, "isError": self.is_error}


@dataclass
class McpResource:
    """MCP 资源定义（对应 resources/list 返回）。

    面试考点：MCP Resources 是只读数据源（文档/数据库/文件）
      与 Tools 的区别：Resources 是数据，Tools 是行为
    """
    uri: str                   # 资源唯一标识，如 "knowledge://default/docs"
    name: str
    description: str = ""
    mime_type: str = "text/plain"

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass
class McpResourceContent:
    """MCP 资源内容（对应 resources/read 返回）。"""
    uri: str
    mime_type: str = "text/plain"
    text: str = ""
    blob: str = ""             # base64 编码的二进制内容

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"uri": self.uri, "mimeType": self.mime_type}
        if self.text:
            d["text"] = self.text
        if self.blob:
            d["blob"] = self.blob
        return d


@dataclass
class McpCapabilities:
    """MCP 服务端能力声明（initialize 响应中返回）。

    面试考点：能力协商是 MCP 的重要特性，客户端根据能力决定使用哪些功能
    """
    tools: bool = True
    resources: bool = True
    prompts: bool = False
    logging: bool = False

    def to_dict(self) -> dict[str, Any]:
        caps: dict[str, Any] = {}
        if self.tools:
            caps["tools"] = {}
        if self.resources:
            caps["resources"] = {"subscribe": False, "listChanged": False}
        if self.prompts:
            caps["prompts"] = {}
        if self.logging:
            caps["logging"] = {}
        return caps


@dataclass
class McpServerInfo:
    """MCP 服务器信息（initialize 响应中返回）。"""
    name: str = "MyAgent MCP Server"
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version}


# ── MCP 方法常量 ──────────────────────────────────────────────────

class McpMethod:
    INITIALIZE = "initialize"
    INITIALIZED = "notifications/initialized"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"
    PING = "ping"

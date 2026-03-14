"""MCP Transport 传输层 — stdio + SSE 两种传输方式。

面试考点:
  MCP 支持两种传输方式:

  1. stdio 传输（本地进程通信）:
     - 原理：父进程（Cursor/Claude Desktop）通过 stdin/stdout 与 MCP Server 通信
     - 格式：每条消息是一行 JSON，以 \\n 分隔
     - 适用：本地工具服务器（Cursor 插件、Claude Desktop 工具）
     - 启动方式：在 ~/.cursor/mcp.json 中配置 command + args

  2. SSE 传输（HTTP 远程通信）:
     - 原理：客户端通过 HTTP SSE 接收服务端推送，通过 POST 发送请求
     - 格式：SSE 事件流（Content-Type: text/event-stream）
     - 适用：远程工具服务器、Web 客户端
     - 端点：GET /sse（建立 SSE 连接）+ POST /messages（发送请求）

  传输层与协议层的分离（面试考点）:
    - McpServer 只处理协议逻辑（JSON-RPC 解析/响应）
    - Transport 只处理 I/O（读写 stdin/stdout 或 HTTP）
    - 两者通过 handle_request() 接口解耦
    - 这是"传输无关"设计，可以轻松添加新传输方式（WebSocket 等）

  Cursor MCP 配置示例（~/.cursor/mcp.json）:
    {
      "mcpServers": {
        "myagent": {
          "command": "python",
          "args": ["-m", "mcp_impl.stdio_server"],
          "cwd": "/path/to/my_agent"
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, AsyncGenerator

from mcp_impl.server import get_mcp_server
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


# ── stdio 传输 ────────────────────────────────────────────────────

class StdioTransport:
    """stdio 传输层 — 通过 stdin/stdout 与 MCP 客户端通信。

    面试考点：
      - 每条消息是一行完整的 JSON（Content-Length 帧协议可选）
      - 使用 asyncio 异步读写，不阻塞事件循环
      - 错误处理：JSON 解析失败时发送 parse error 响应
    """

    def __init__(self) -> None:
        self._server = get_mcp_server()

    async def run(self) -> None:
        """启动 stdio 传输循环（阻塞直到 stdin 关闭）。"""
        logger.info("mcp_stdio_transport_started")

        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            try:
                line = await reader.readline()
                if not line:
                    break  # stdin 关闭，退出

                line = line.strip()
                if not line:
                    continue

                # 解析 JSON-RPC 请求
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as e:
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {e}"},
                    }
                    self._write(response)
                    continue

                # 处理请求
                response = await self._server.handle_request(raw)
                if response is not None:
                    self._write(response)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("stdio_transport_error", error=str(e))

        logger.info("mcp_stdio_transport_stopped")

    @staticmethod
    def _write(data: dict[str, Any]) -> None:
        """向 stdout 写入 JSON-RPC 响应（以 \\n 结尾）。"""
        line = json.dumps(data, ensure_ascii=False) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()


# ── SSE 传输（FastAPI 集成）────────────────────────────────────────

class SseTransport:
    """SSE 传输层 — 通过 HTTP SSE 与 MCP 客户端通信。

    面试考点：
      - SSE 是单向推送（服务端 → 客户端），请求通过 POST 发送
      - 每个客户端连接有唯一 session_id
      - 消息格式：data: {json}\\n\\n
      - 连接管理：客户端断开时清理 session

    端点设计:
      GET  /mcp/sse           → 建立 SSE 连接，返回 session_id
      POST /mcp/messages      → 发送 JSON-RPC 请求（带 session_id）
    """

    def __init__(self) -> None:
        self._server = get_mcp_server()
        # session_id → asyncio.Queue（用于向 SSE 连接推送响应）
        self._sessions: dict[str, asyncio.Queue] = {}

    async def create_session(self, session_id: str) -> None:
        """创建新的 SSE 会话。"""
        self._sessions[session_id] = asyncio.Queue()
        logger.info("mcp_sse_session_created", session_id=session_id)

    async def close_session(self, session_id: str) -> None:
        """关闭 SSE 会话。"""
        self._sessions.pop(session_id, None)
        logger.info("mcp_sse_session_closed", session_id=session_id)

    async def handle_message(
        self,
        session_id: str,
        raw: dict[str, Any],
    ) -> None:
        """处理客户端通过 POST 发送的消息，将响应推入 SSE 队列。"""
        if session_id not in self._sessions:
            logger.warning("mcp_unknown_session", session_id=session_id)
            return

        response = await self._server.handle_request(raw)
        if response is not None:
            await self._sessions[session_id].put(response)

    async def sse_generator(
        self,
        session_id: str,
    ) -> AsyncGenerator[str, None]:
        """SSE 事件生成器 — 持续从队列取消息并推送给客户端。

        面试考点：
          - asyncio.Queue 实现生产者-消费者模式
          - SSE 格式：data: {json}\\n\\n
          - 心跳：每30秒发送注释行 ": heartbeat\\n\\n"
        """
        if session_id not in self._sessions:
            await self.create_session(session_id)

        # 发送 endpoint 信息（MCP SSE 协议要求）
        endpoint_event = {
            "event": "endpoint",
            "data": f"/mcp/messages?session_id={session_id}",
        }
        yield f"event: endpoint\ndata: /mcp/messages?session_id={session_id}\n\n"

        queue = self._sessions[session_id]
        while True:
            try:
                # 等待消息，超时后发送心跳
                msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                data = json.dumps(msg, ensure_ascii=False)
                yield f"data: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("sse_generator_error", error=str(e))
                break

        await self.close_session(session_id)


# 全局 SSE 传输单例
_sse_transport: SseTransport | None = None


def get_sse_transport() -> SseTransport:
    global _sse_transport
    if _sse_transport is None:
        _sse_transport = SseTransport()
    return _sse_transport


# ── stdio 入口（供 Cursor/Claude Desktop 直接启动）────────────────

async def run_stdio_server() -> None:
    """stdio MCP Server 入口。

    Cursor MCP 配置 (~/.cursor/mcp.json):
    {
      "mcpServers": {
        "myagent": {
          "command": "python",
          "args": ["-m", "mcp_impl.transport"],
          "cwd": "d:/work-tianrun/project/my/my_agent",
          "env": {
            "LLM_API_KEY": "your-api-key",
            "LLM_BASE_URL": "https://api.openai.com/v1"
          }
        }
      }
    }
    """
    transport = StdioTransport()
    await transport.run()


if __name__ == "__main__":
    asyncio.run(run_stdio_server())

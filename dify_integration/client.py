"""Dify API 客户端 — 从外部系统调用 Dify 应用。

面试考点:
  Dify 提供三类 API:
    1. Chat API:     调用 Dify 上的 Chatbot/Agent 应用
    2. Workflow API: 调用 Dify 上的 Workflow 应用（非对话式）
    3. Knowledge API: 管理知识库（上传文档、检索）

  客户端设计要点:
    - 统一封装 HTTP 调用，隐藏 API 细节
    - 支持流式输出（SSE）和同步输出两种模式
    - 异步 httpx 客户端，复用连接池
    - 优雅降级：Dify 不可用时返回错误而不崩溃

  Dify API 认证:
    - 每个应用有独立的 API Key（在 Dify 控制台获取）
    - 请求头: Authorization: Bearer {api_key}

  与自研 API 对比:
    - Dify: 通过控制台配置 Prompt/工具/模型，API 调用简单
    - 自研: 代码控制一切，灵活但需要更多开发工作
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import httpx

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DifyConfig:
    """Dify 连接配置。"""
    base_url: str = "http://localhost:8080"   # Dify 服务地址
    api_key: str = ""                          # 应用 API Key（从 Dify 控制台获取）
    timeout: float = 60.0
    max_retries: int = 2


@dataclass
class DifyChatResponse:
    """Dify Chat API 响应。"""
    answer: str
    conversation_id: str = ""
    message_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DifyWorkflowResponse:
    """Dify Workflow API 响应。"""
    workflow_run_id: str
    status: str                          # running / succeeded / failed / stopped
    outputs: dict[str, Any] = field(default_factory=dict)
    elapsed_time: float = 0.0
    total_tokens: int = 0
    error: str = ""


class DifyClient:
    """Dify API 客户端。

    用法:
        client = DifyClient(DifyConfig(
            base_url="http://localhost:8080",
            api_key="app-xxxx",
        ))
        response = await client.chat("你好，帮我分析一下这段代码")
    """

    def __init__(self, config: DifyConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=config.timeout,
        )

    async def close(self) -> None:
        await self._http.aclose()

    # ── Chat API ─────────────────────────────────────────────────

    async def chat(
        self,
        query: str,
        conversation_id: str = "",
        user: str = "myagent-user",
        inputs: dict[str, Any] | None = None,
    ) -> DifyChatResponse:
        """调用 Dify Chat/Agent 应用（同步模式）。

        面试考点：Dify Chat API 支持多轮对话，通过 conversation_id 关联上下文
        """
        payload: dict[str, Any] = {
            "query": query,
            "user": user,
            "response_mode": "blocking",
            "inputs": inputs or {},
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        try:
            resp = await self._http.post("/v1/chat-messages", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return DifyChatResponse(
                answer=data.get("answer", ""),
                conversation_id=data.get("conversation_id", ""),
                message_id=data.get("message_id", ""),
                usage=data.get("metadata", {}).get("usage", {}),
                metadata=data.get("metadata", {}),
            )
        except httpx.HTTPStatusError as e:
            logger.error("dify_chat_error", status=e.response.status_code, error=str(e))
            raise
        except Exception as e:
            logger.error("dify_chat_failed", error=str(e))
            raise

    async def chat_stream(
        self,
        query: str,
        conversation_id: str = "",
        user: str = "myagent-user",
        inputs: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """调用 Dify Chat/Agent 应用（流式 SSE 模式）。

        面试考点：
          - Dify 流式输出使用 SSE（Server-Sent Events）
          - 每个 SSE 事件是一个 JSON，包含 event 类型和数据
          - 事件类型：message（文本片段）/ message_end（结束）/ error
        """
        payload: dict[str, Any] = {
            "query": query,
            "user": user,
            "response_mode": "streaming",
            "inputs": inputs or {},
        }
        if conversation_id:
            payload["conversation_id"] = conversation_id

        async with self._http.stream("POST", "/v1/chat-messages", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    event = data.get("event", "")
                    if event == "message":
                        yield data.get("answer", "")
                    elif event == "error":
                        raise RuntimeError(data.get("message", "Dify stream error"))
                except json.JSONDecodeError:
                    continue

    # ── Workflow API ──────────────────────────────────────────────

    async def run_workflow(
        self,
        inputs: dict[str, Any],
        user: str = "myagent-user",
    ) -> DifyWorkflowResponse:
        """调用 Dify Workflow 应用（同步模式）。

        面试考点：
          - Workflow API 适合非对话式任务（数据处理、报告生成等）
          - 输入/输出通过 inputs/outputs 字典传递
          - 与 Chat API 的区别：无对话历史，每次独立执行
        """
        payload = {
            "inputs": inputs,
            "user": user,
            "response_mode": "blocking",
        }
        try:
            resp = await self._http.post("/v1/workflows/run", json=payload)
            resp.raise_for_status()
            data = resp.json()
            return DifyWorkflowResponse(
                workflow_run_id=data.get("workflow_run_id", ""),
                status=data.get("data", {}).get("status", ""),
                outputs=data.get("data", {}).get("outputs", {}),
                elapsed_time=data.get("data", {}).get("elapsed_time", 0.0),
                total_tokens=data.get("data", {}).get("total_tokens", 0),
                error=data.get("data", {}).get("error", ""),
            )
        except httpx.HTTPStatusError as e:
            logger.error("dify_workflow_error", status=e.response.status_code)
            raise

    async def run_workflow_stream(
        self,
        inputs: dict[str, Any],
        user: str = "myagent-user",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """调用 Dify Workflow 应用（流式模式）。"""
        payload = {
            "inputs": inputs,
            "user": user,
            "response_mode": "streaming",
        }
        async with self._http.stream("POST", "/v1/workflows/run", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    continue

    # ── Knowledge Base API ────────────────────────────────────────

    async def search_knowledge(
        self,
        dataset_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """在 Dify 知识库中检索相关文档。

        面试考点：Dify 知识库 API 可以从外部系统触发检索，
        实现 RAG 能力复用（不需要重复建索引）
        """
        payload = {
            "query": query,
            "retrieval_model": {
                "search_method": "semantic_search",
                "top_k": top_k,
            },
        }
        try:
            resp = await self._http.post(
                f"/v1/datasets/{dataset_id}/retrieve",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("records", [])
        except Exception as e:
            logger.warning("dify_knowledge_search_failed", error=str(e))
            return []

    async def list_datasets(self) -> list[dict[str, Any]]:
        """列出所有知识库。"""
        try:
            resp = await self._http.get("/v1/datasets")
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as e:
            logger.warning("dify_list_datasets_failed", error=str(e))
            return []

    async def upload_document(
        self,
        dataset_id: str,
        text: str,
        name: str = "document",
    ) -> dict[str, Any]:
        """向知识库上传文档（文本方式）。"""
        payload = {
            "name": name,
            "text": text,
            "indexing_technique": "high_quality",
            "process_rule": {"mode": "automatic"},
        }
        try:
            resp = await self._http.post(
                f"/v1/datasets/{dataset_id}/document/create_by_text",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("dify_upload_document_failed", error=str(e))
            raise

    # ── 健康检查 ──────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """检查 Dify 服务是否可用。"""
        try:
            resp = await self._http.get("/health", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False


# ── 工厂函数 ──────────────────────────────────────────────────────

def create_dify_client(
    base_url: str | None = None,
    api_key: str | None = None,
) -> DifyClient:
    """从环境变量或参数创建 Dify 客户端。"""
    import os
    config = DifyConfig(
        base_url=base_url or os.getenv("DIFY_BASE_URL", "http://localhost:8080"),
        api_key=api_key or os.getenv("DIFY_API_KEY", ""),
    )
    return DifyClient(config)

"""MyAgent 工具插件服务 — 将自研工具暴露给 Dify 调用。

面试考点:
  Dify 自定义工具开发流程:
    1. 实现 HTTP API（本文件，FastAPI 实现）
    2. 编写 OpenAPI Schema（描述工具参数和返回值）
    3. 在 Dify 控制台"工具"→"自定义工具"中导入 Schema
    4. Dify Agent 在推理时自动调用该 HTTP API

  设计要点:
    - 每个工具对应一个 POST 端点
    - 请求/响应格式遵循 Dify 自定义工具规范
    - 鉴权：通过 X-API-Key 请求头验证
    - 错误处理：返回标准错误格式，Dify 能正确处理

  与 MCP 的区别:
    - Dify 自定义工具：HTTP REST，Dify 专用
    - MCP：JSON-RPC over stdio/SSE，通用协议（Cursor/Claude 等均支持）
    - 自研工具系统：Python 内部调用，无网络开销

启动方式（独立插件服务）:
  uvicorn dify_integration.plugins.myagent_tools:plugin_app --port 8002
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

plugin_app = FastAPI(
    title="MyAgent Tools Plugin",
    description="MyAgent 工具插件服务，供 Dify 自定义工具调用",
    version="1.0.0",
    docs_url="/openapi-ui",
    openapi_url="/openapi.json",
)

_PLUGIN_API_KEY = os.getenv("PLUGIN_API_KEY", "myagent-plugin-key")


def _verify_key(x_api_key: str | None) -> None:
    """验证插件 API Key。"""
    if x_api_key != _PLUGIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")


# ── RAG 检索工具 ──────────────────────────────────────────────────

class RagSearchRequest(BaseModel):
    query: str = Field(description="检索查询语句")
    top_k: int = Field(default=5, ge=1, le=20, description="返回结果数量")
    knowledge_base: str = Field(default="default", description="知识库名称")


class RagSearchResult(BaseModel):
    content: str
    score: float
    source: str = ""


@plugin_app.post(
    "/tools/rag_search",
    summary="RAG 知识库检索",
    description="在 MyRAG 知识库中检索相关文档片段",
)
async def rag_search(
    body: RagSearchRequest,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """RAG 检索工具。

    面试考点：
      - 将 MyRAG 项目的检索能力通过 HTTP API 暴露给 Dify
      - Dify Agent 可以在推理时调用此工具获取知识库内容
      - 实现了两个 AI 项目（MyAgent + MyRAG）的能力复用
    """
    _verify_key(x_api_key)

    myrag_url = os.getenv("MYRAG_BASE_URL", "http://localhost:8000")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{myrag_url}/api/v1/search",
                json={
                    "query": body.query,
                    "top_k": body.top_k,
                    "knowledge_base": body.knowledge_base,
                },
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
    except Exception as e:
        # MyRAG 不可用时，返回空结果（降级处理）
        results = []
        error_msg = str(e)
        return JSONResponse(content={
            "results": [],
            "total": 0,
            "error": f"RAG 服务暂不可用: {error_msg}",
        })

    return JSONResponse(content={
        "results": [
            {
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
                "source": r.get("metadata", {}).get("source", ""),
            }
            for r in results
        ],
        "total": len(results),
        "query": body.query,
    })


# ── Text-to-SQL 工具 ──────────────────────────────────────────────

class SqlQueryRequest(BaseModel):
    natural_language: str = Field(description="自然语言查询描述")
    database_schema: str = Field(
        default="",
        description="数据库 Schema 描述（表名、字段名），不提供则使用默认 Schema",
    )
    execute: bool = Field(default=False, description="是否直接执行 SQL（默认只生成不执行）")


@plugin_app.post(
    "/tools/sql_query",
    summary="Text-to-SQL 查询",
    description="将自然语言转换为 SQL 查询语句",
)
async def sql_query(
    body: SqlQueryRequest,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    """Text-to-SQL 工具。

    面试考点：
      - Text-to-SQL 是企业 AI 应用的重要场景（数据分析、BI 查询）
      - 安全考虑：默认只生成 SQL，不直接执行（防止 SQL 注入）
      - 实际执行需要额外的权限验证和 SQL 审计
    """
    _verify_key(x_api_key)

    from my_agent.config.settings import settings
    from my_agent.domain.llm.message import SystemMessage, UserMessage
    from my_agent.domain.llm.openai_client import OpenAIClient

    default_schema = """
    表: users (id, name, email, created_at, role)
    表: sessions (id, user_id, created_at, message_count)
    表: messages (id, session_id, role, content, tokens, created_at)
    表: tool_calls (id, message_id, tool_name, arguments, result, created_at)
    """

    schema = body.database_schema or default_schema

    cfg = settings.default_llm
    llm = OpenAIClient(
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        model=cfg.model,
    )

    try:
        response = await llm.chat(
            messages=[
                SystemMessage(
                    f"你是一个 SQL 专家。根据以下数据库 Schema 将自然语言转换为 SQL 查询。\n\n"
                    f"Schema:\n{schema}\n\n"
                    f"只输出 SQL 语句，不要解释。"
                ),
                UserMessage(body.natural_language),
            ],
            temperature=0.1,
        )
        sql = response.content.strip()
        # 清理 markdown 代码块
        if sql.startswith("```"):
            sql = "\n".join(sql.split("\n")[1:-1])

        result: dict[str, Any] = {"sql": sql, "natural_language": body.natural_language}

        if body.execute:
            result["warning"] = "SQL 执行功能需要配置数据库连接，当前仅返回生成的 SQL"

        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"SQL 生成失败: {e}"},
        )


# ── 计算器工具 ────────────────────────────────────────────────────

class CalculatorRequest(BaseModel):
    expression: str = Field(description="数学表达式，如 (123 + 456) * 789")


@plugin_app.post(
    "/tools/calculator",
    summary="数学计算器",
    description="安全计算数学表达式（使用 ast 模块，防止代码注入）",
)
async def calculator(
    body: CalculatorRequest,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    _verify_key(x_api_key)

    from my_agent.domain.tool.builtin.calculator import calculator as calc_tool
    from my_agent.domain.tool.registry import get_registry
    import my_agent.domain.tool.builtin  # noqa: F401

    registry = get_registry()
    tool = registry.get("calculator")
    if tool is None:
        return JSONResponse(status_code=500, content={"error": "calculator tool not found"})

    result = await tool._execute(expression=body.expression)
    return JSONResponse(content={
        "expression": body.expression,
        "result": result.output,
        "success": result.success,
    })


# ── 代码执行工具 ──────────────────────────────────────────────────

class CodeExecRequest(BaseModel):
    code: str = Field(description="要执行的 Python 代码")
    timeout: int = Field(default=10, ge=1, le=30, description="执行超时秒数")


@plugin_app.post(
    "/tools/code_exec",
    summary="Python 代码执行",
    description="在沙箱环境中执行 Python 代码（subprocess 隔离）",
)
async def code_exec(
    body: CodeExecRequest,
    x_api_key: str | None = Header(default=None),
) -> JSONResponse:
    _verify_key(x_api_key)

    import my_agent.domain.tool.builtin  # noqa: F401
    from my_agent.domain.tool.registry import get_registry

    registry = get_registry()
    tool = registry.get("code_executor")
    if tool is None:
        return JSONResponse(status_code=500, content={"error": "code_executor tool not found"})

    result = await tool._execute(code=body.code, timeout=body.timeout)
    return JSONResponse(content={
        "code": body.code,
        "output": result.output,
        "success": result.success,
        "error": result.error,
    })


# ── OpenAPI Schema 端点（供 Dify 导入）────────────────────────────

@plugin_app.get(
    "/dify-schema",
    summary="获取 Dify 自定义工具 Schema",
    description="返回符合 Dify 自定义工具规范的 OpenAPI Schema",
    include_in_schema=False,
)
async def get_dify_schema() -> JSONResponse:
    """返回 Dify 可直接导入的工具 Schema。

    在 Dify 控制台：工具 → 自定义工具 → 导入此 URL 的 Schema
    """
    plugin_base_url = os.getenv("PLUGIN_BASE_URL", "http://host.docker.internal:8002")
    schema = {
        "openapi": "3.0.0",
        "info": {
            "title": "MyAgent Tools",
            "description": "MyAgent 工具集，供 Dify Agent 调用",
            "version": "1.0.0",
        },
        "servers": [{"url": plugin_base_url}],
        "paths": {
            "/tools/rag_search": {
                "post": {
                    "operationId": "rag_search",
                    "summary": "RAG 知识库检索",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "query": {"type": "string", "description": "检索查询"},
                                        "top_k": {"type": "integer", "default": 5},
                                    },
                                    "required": ["query"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "检索结果"}},
                }
            },
            "/tools/calculator": {
                "post": {
                    "operationId": "calculator",
                    "summary": "数学计算",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "expression": {"type": "string", "description": "数学表达式"},
                                    },
                                    "required": ["expression"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "计算结果"}},
                }
            },
            "/tools/sql_query": {
                "post": {
                    "operationId": "sql_query",
                    "summary": "Text-to-SQL",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "natural_language": {"type": "string"},
                                        "database_schema": {"type": "string"},
                                    },
                                    "required": ["natural_language"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "SQL 语句"}},
                }
            },
        },
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-Api-Key",
                }
            }
        },
        "security": [{"ApiKeyAuth": []}],
    }
    return JSONResponse(content=schema)

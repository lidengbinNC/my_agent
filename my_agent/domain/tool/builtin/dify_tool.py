"""Dify 工具 — 将 Dify 应用作为 MyAgent 的一个工具调用。

面试考点:
  双向集成模式:
    1. MyAgent → Dify（本文件）：MyAgent 调用 Dify 应用获取结果
       场景：Dify 上已有配置好的 Workflow，MyAgent 直接复用
    2. Dify → MyAgent（plugins/myagent_tools.py）：Dify Agent 调用 MyAgent 工具
       场景：MyAgent 的工具能力通过 HTTP 暴露给 Dify

  设计思路:
    - 将 Dify 的 Chat/Workflow 应用封装为 BaseTool
    - MyAgent 的 ReAct 引擎可以像调用普通工具一样调用 Dify 应用
    - 实现了"AI 调用 AI"的能力组合

  适用场景:
    - Dify 上已有复杂的 Workflow（如客服流程、数据分析），
      MyAgent 可以直接调用而不需要重新实现
    - 快速集成第三方 AI 能力（Dify 生态中的其他应用）
"""

from __future__ import annotations

import os
from typing import Any

from my_agent.domain.tool.base import BaseTool, ToolResult
from my_agent.domain.tool.registry import tool
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class DifyChatTool(BaseTool):
    """调用 Dify Chat/Agent 应用的工具。"""

    name = "dify_chat"
    description = "调用 Dify 智能助手应用，适合需要复杂推理或特定领域知识的问题"
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "发送给 Dify 应用的问题或指令",
            },
            "app_type": {
                "type": "string",
                "description": "Dify 应用类型：chat（对话）或 workflow（工作流）",
                "enum": ["chat", "workflow"],
                "default": "chat",
            },
        },
        "required": ["query"],
    }

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url or os.getenv("DIFY_BASE_URL", "http://localhost:8080")
        self._api_key = api_key or os.getenv("DIFY_API_KEY", "")

    async def _execute(self, query: str, app_type: str = "chat") -> ToolResult:
        """调用 Dify 应用。"""
        if not self._api_key:
            return ToolResult.fail("DIFY_API_KEY 未配置，请在 .env 中设置")

        from dify_integration.client import DifyClient, DifyConfig

        client = DifyClient(DifyConfig(
            base_url=self._base_url,
            api_key=self._api_key,
        ))

        try:
            if app_type == "workflow":
                response = await client.run_workflow(inputs={"query": query})
                output = str(response.outputs.get("answer", response.outputs))
            else:
                response = await client.chat(query=query)
                output = response.answer

            logger.info("dify_tool_called", app_type=app_type, query=query[:50])
            return ToolResult.ok(output, source="dify", app_type=app_type)
        except Exception as e:
            logger.warning("dify_tool_failed", error=str(e))
            return ToolResult.fail(f"Dify 调用失败: {e}")
        finally:
            await client.close()


class DifyWorkflowTool(BaseTool):
    """调用 Dify Workflow 应用的工具（专用于数据分析场景）。"""

    name = "dify_data_analysis"
    description = "调用 Dify 数据分析 Workflow，输入数据描述，返回分析报告"
    parameters_schema = {
        "type": "object",
        "properties": {
            "data_description": {
                "type": "string",
                "description": "数据分析需求描述，如：分析最近30天的用户活跃度趋势",
            },
            "output_format": {
                "type": "string",
                "description": "输出格式：markdown 或 json",
                "enum": ["markdown", "json"],
                "default": "markdown",
            },
        },
        "required": ["data_description"],
    }

    def __init__(
        self,
        base_url: str | None = None,
        workflow_api_key: str | None = None,
    ) -> None:
        self._base_url = base_url or os.getenv("DIFY_BASE_URL", "http://localhost:8080")
        self._api_key = workflow_api_key or os.getenv("DIFY_WORKFLOW_API_KEY", "")

    async def _execute(
        self,
        data_description: str,
        output_format: str = "markdown",
    ) -> ToolResult:
        if not self._api_key:
            return ToolResult.fail("DIFY_WORKFLOW_API_KEY 未配置")

        from dify_integration.client import DifyClient, DifyConfig

        client = DifyClient(DifyConfig(
            base_url=self._base_url,
            api_key=self._api_key,
        ))

        try:
            response = await client.run_workflow(inputs={
                "data_description": data_description,
                "output_format": output_format,
            })

            if response.error:
                return ToolResult.fail(f"Workflow 执行失败: {response.error}")

            report = response.outputs.get("report", str(response.outputs))
            return ToolResult.ok(
                report,
                workflow_run_id=response.workflow_run_id,
                elapsed_time=response.elapsed_time,
            )
        except Exception as e:
            return ToolResult.fail(f"Dify Workflow 调用失败: {e}")
        finally:
            await client.close()


def register_dify_tools() -> None:
    """注册 Dify 工具到全局注册中心（仅在配置了 DIFY_API_KEY 时注册）。"""
    from my_agent.domain.tool.registry import get_registry

    if not os.getenv("DIFY_API_KEY"):
        logger.info("dify_tools_skipped", reason="DIFY_API_KEY not set")
        return

    registry = get_registry()
    registry.register(DifyChatTool())
    logger.info("dify_chat_tool_registered")

    if os.getenv("DIFY_WORKFLOW_API_KEY"):
        registry.register(DifyWorkflowTool())
        logger.info("dify_workflow_tool_registered")

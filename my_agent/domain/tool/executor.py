"""工具执行器 — 超时控制、错误捕获、结果格式化。

面试考点:
  - asyncio.wait_for 实现超时控制
  - 异常隔离：工具错误不影响 Agent 主流程
  - 结果截断：防止超长 Observation 撑爆 Context Window
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from my_agent.domain.tool.base import BaseTool, ToolResult
from my_agent.domain.tool.registry import ToolRegistry
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

MAX_OBSERVATION_LEN = 2000  # Observation 最大字符数，超出截断


class ToolExecutor:
    """负责安全地执行工具调用。"""

    def __init__(
        self,
        registry: ToolRegistry,
        timeout: float = 30.0,
    ) -> None:
        self._registry = registry
        self._timeout = timeout

    async def execute(self, tool_name: str, arguments: str | dict[str, Any]) -> ToolResult:
        """执行指定工具，返回 ToolResult。

        :param tool_name: 工具名称
        :param arguments: JSON 字符串或已解析的 dict
        """
        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult.fail(
                f"工具 '{tool_name}' 不存在，可用工具: {self._registry.names()}"
            )

        # 解析参数
        kwargs = self._parse_arguments(arguments)
        if kwargs is None:
            return ToolResult.fail(f"工具参数解析失败: {arguments!r}")

        logger.info("tool_executing", tool=tool_name, args=kwargs)

        try:
            result = await asyncio.wait_for(
                tool._execute(**kwargs),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            result = ToolResult.fail(f"工具执行超时（>{self._timeout}s）")
        except Exception as e:
            logger.error("tool_execution_error", tool=tool_name, error=str(e))
            result = ToolResult.fail(str(e))

        # 截断过长 Observation
        if len(result.output) > MAX_OBSERVATION_LEN:
            result.output = result.output[:MAX_OBSERVATION_LEN] + "\n...[结果已截断]"

        logger.info(
            "tool_executed",
            tool=tool_name,
            success=result.success,
            output_len=len(result.output),
        )
        return result

    @staticmethod
    def _parse_arguments(arguments: str | dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None

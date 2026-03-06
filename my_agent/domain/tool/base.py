"""工具基类 — 定义所有工具的统一接口。

面试考点:
  - 抽象基类 + 模板方法模式
  - JSON Schema 描述工具参数（与 OpenAI Function Calling 格式对齐）
  - ToolResult 统一封装成功/失败两种状态
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    """工具执行结果，统一封装成功和失败两种状态。"""

    success: bool
    output: str                          # 文本化结果，注入回 LLM 的 Observation
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str, **metadata: Any) -> "ToolResult":
        return cls(success=True, output=output, metadata=metadata)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(success=False, output=f"工具执行失败: {error}", error=error)

    def to_observation(self) -> str:
        """格式化为注入 LLM 的 Observation 文本。"""
        if self.success:
            return self.output
        return f"[ERROR] {self.error}"


class BaseTool(ABC):
    """所有工具的抽象基类。

    子类必须实现:
      - name: 工具名称（唯一标识）
      - description: 工具描述（LLM 据此决定是否调用）
      - parameters_schema: 参数 JSON Schema
      - _execute: 实际执行逻辑
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]

    @abstractmethod
    async def _execute(self, **kwargs: Any) -> ToolResult:
        """子类实现具体执行逻辑。"""
        ...

    def to_openai_tool(self) -> dict[str, Any]:
        """转换为 OpenAI Function Calling 工具定义格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }

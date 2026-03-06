"""工具注册中心 — 装饰器注册 + inspect 自动生成 JSON Schema。

面试考点:
  - 装饰器模式：@tool() 一行注册工具，无需手写 Schema
  - inspect 模块：提取函数签名 → 自动生成 JSON Schema
  - 单例注册中心：全局唯一，支持动态注册和查询
  - 工厂模式：根据名称动态创建工具实例
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from my_agent.domain.tool.base import BaseTool, ToolResult
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Python 类型 → JSON Schema 类型映射
_PY_TO_JSON_TYPE: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _build_schema_from_func(func: Callable) -> dict[str, Any]:
    """从函数签名自动生成 JSON Schema（排除 self）。"""
    sig = inspect.signature(func)
    hints = {}
    try:
        hints = func.__annotations__
    except AttributeError:
        pass

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "kwargs"):
            continue

        annotation = hints.get(param_name, str)
        # 处理 Optional[X] → X
        origin = getattr(annotation, "__origin__", None)
        if origin is type(None):
            continue
        if hasattr(annotation, "__args__"):
            # Optional[X] = Union[X, None]
            args = [a for a in annotation.__args__ if a is not type(None)]
            annotation = args[0] if args else str

        json_type = _PY_TO_JSON_TYPE.get(annotation, "string")
        prop: dict[str, Any] = {"type": json_type}

        # 从 docstring 提取参数描述（格式: ":param name: description"）
        doc = func.__doc__ or ""
        for line in doc.splitlines():
            line = line.strip()
            if line.startswith(f":param {param_name}:"):
                prop["description"] = line.split(":", 2)[-1].strip()
                break

        properties[param_name] = prop

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


class _FunctionTool(BaseTool):
    """将普通 async 函数包装为 BaseTool 实例。"""

    def __init__(
        self,
        func: Callable,
        name: str,
        description: str,
        schema: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_schema = schema or _build_schema_from_func(func)
        self._func = func

    async def _execute(self, **kwargs: Any) -> ToolResult:
        return await self._func(**kwargs)


class ToolRegistry:
    """全局工具注册中心（单例）。"""

    _instance: "ToolRegistry | None" = None

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools: dict[str, BaseTool] = {}
        return cls._instance

    def register(self, tool_instance: BaseTool) -> None:
        self._tools[tool_instance.name] = tool_instance
        logger.debug("tool_registered", name=tool_instance.name)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def to_openai_tools(self) -> list[dict[str, Any]]:
        """返回所有工具的 OpenAI Function Calling 格式定义。"""
        return [t.to_openai_tool() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


# 全局单例
_registry = ToolRegistry()


def tool(
    name: str | None = None,
    description: str | None = None,
    schema: dict[str, Any] | None = None,
) -> Callable:
    """工具注册装饰器。

    用法:
        @tool(description="计算数学表达式")
        async def calculator(expression: str) -> ToolResult:
            ...
    """
    def decorator(func: Callable) -> Callable:
        tool_name = name or func.__name__
        tool_desc = description or (func.__doc__ or "").strip().splitlines()[0]
        instance = _FunctionTool(func, tool_name, tool_desc, schema)
        _registry.register(instance)
        # 保留原函数可直接调用
        func._tool = instance  # type: ignore[attr-defined]
        return func

    return decorator


def get_registry() -> ToolRegistry:
    return _registry

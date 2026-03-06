"""计算器工具 — 安全求值数学表达式。

面试考点:
  - 沙箱安全: 只允许数学运算，禁止任意代码执行
  - 使用 ast.literal_eval + 白名单操作符替代 eval
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from my_agent.domain.tool.base import ToolResult
from my_agent.domain.tool.registry import tool

_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCTIONS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "pi": math.pi, "e": math.e,
}


def _safe_eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        return _SAFE_OPERATORS[op_type](_safe_eval(node.left), _safe_eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise ValueError(f"不支持的运算符: {op_type.__name__}")
        return _SAFE_OPERATORS[op_type](_safe_eval(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("不支持的函数调用形式")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"不支持的函数: {func_name}")
        args = [_safe_eval(a) for a in node.args]
        return _SAFE_FUNCTIONS[func_name](*args)
    if isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCTIONS:
            return _SAFE_FUNCTIONS[node.id]
        raise ValueError(f"不支持的变量: {node.id}")
    raise ValueError(f"不支持的表达式类型: {type(node).__name__}")


@tool(description="计算数学表达式，支持四则运算、幂运算、三角函数等")
async def calculator(expression: str) -> ToolResult:
    """安全计算数学表达式。

    :param expression: 数学表达式，如 "2 + 3 * 4" 或 "sqrt(16)"
    """
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _safe_eval(tree)
        # 格式化结果
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return ToolResult.ok(f"{expression} = {result}")
    except ZeroDivisionError:
        return ToolResult.fail("除零错误")
    except Exception as e:
        return ToolResult.fail(f"计算失败: {e}")

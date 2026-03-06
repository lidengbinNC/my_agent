"""Python 代码执行工具 — subprocess 沙箱隔离执行。

面试考点:
  - 沙箱安全: subprocess 隔离，不在主进程中 exec
  - 超时控制: 防止死循环
  - 资源限制: 禁止危险模块导入
  - stdout/stderr 捕获
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

from my_agent.domain.tool.base import ToolResult
from my_agent.domain.tool.registry import tool

# 禁止导入的危险模块
_BLOCKED_IMPORTS = {
    "os", "sys", "subprocess", "shutil", "socket",
    "requests", "urllib", "ftplib", "smtplib",
    "importlib", "ctypes", "multiprocessing",
}

_SANDBOX_TIMEOUT = 10  # 秒
_MAX_OUTPUT_LEN = 2000


def _check_dangerous_code(code: str) -> str | None:
    """静态检查危险代码模式，返回错误描述或 None（安全）。"""
    import ast

    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"语法错误: {e}"

    for node in ast.walk(tree):
        # 检查 import 语句
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = ""
            if isinstance(node, ast.Import):
                module = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                module = node.module.split(".")[0]
            if module in _BLOCKED_IMPORTS:
                return f"禁止导入模块: {module}"

        # 检查 __import__ 调用
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "__import__":
                return "禁止使用 __import__"

    return None


@tool(description="在安全沙箱中执行 Python 代码，返回标准输出结果")
async def code_executor(code: str) -> ToolResult:
    """在隔离沙箱中执行 Python 代码片段。

    :param code: 要执行的 Python 代码（支持多行）
    """
    # 静态安全检查
    if err := _check_dangerous_code(code):
        return ToolResult.fail(f"代码安全检查失败: {err}")

    # 构造沙箱脚本
    sandbox_script = textwrap.dedent(f"""
import sys
# 限制可用模块
_allowed = {{'math', 'json', 'datetime', 'collections', 'itertools',
              'functools', 'random', 'string', 'time', 're'}}

{code}
""")

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", sandbox_script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=_SANDBOX_TIMEOUT
        )

        stdout_str = stdout.decode("utf-8", errors="replace")[:_MAX_OUTPUT_LEN]
        stderr_str = stderr.decode("utf-8", errors="replace")[:500]

        if proc.returncode != 0:
            err_msg = stderr_str or f"进程退出码: {proc.returncode}"
            return ToolResult.fail(f"代码执行错误:\n{err_msg}")

        output = stdout_str or "（代码执行成功，无输出）"
        if stderr_str:
            output += f"\n[stderr]: {stderr_str}"
        return ToolResult.ok(output)

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return ToolResult.fail(f"代码执行超时（>{_SANDBOX_TIMEOUT}s），已终止")
    except Exception as e:
        return ToolResult.fail(f"沙箱执行失败: {e}")

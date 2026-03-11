"""ToolGuard — 工具调用权限 + 参数校验 + 频率限制。

面试考点:
  - 工具权限（白名单）：不同用户/会话只允许调用特定工具
  - 参数范围校验：防止恶意参数（如 code_executor 执行 rm -rf）
  - 频率限制（Rate Limiting）：滑动窗口算法，防止工具被滥用
  - 滑动窗口 vs 固定窗口：
      固定窗口：实现简单，但窗口边界突发问题（窗口重置瞬间可发 2x 请求）
      滑动窗口：更精确，本实现用 deque 记录最近请求时间戳
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Any

from my_agent.domain.guardrails.base import BaseGuard, GuardResult
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# 工具危险参数关键词（ToolNode 参数校验）
_DANGEROUS_CODE_PATTERNS = [
    "import os", "import sys", "import subprocess",
    "__import__", "eval(", "exec(",
    "os.system", "os.popen", "subprocess.run",
    "rm -rf", "del /f", "format c:",
    "shutil.rmtree",
]

_DANGEROUS_URL_PATTERNS = [
    "169.254.",       # AWS metadata
    "192.168.",       # 内网
    "10.",            # 内网
    "127.",           # loopback
    "localhost",
    "file://",        # 本地文件
]


class ToolGuard(BaseGuard):
    """工具调用安全检查：权限 + 参数 + 频率。"""

    def __init__(
        self,
        allowed_tools: list[str] | None = None,   # None = 全部允许
        rate_limit: int = 20,                       # 每分钟最多调用次数
        rate_window_seconds: float = 60.0,
    ) -> None:
        self._allowed_tools = set(allowed_tools) if allowed_tools else None
        self._rate_limit = rate_limit
        self._rate_window = rate_window_seconds
        # session_id/tool_name -> deque of timestamps（滑动窗口）
        self._call_times: dict[str, deque] = defaultdict(deque)

    @property
    def name(self) -> str:
        return "ToolGuard"

    async def check(self, content: str, context: dict[str, Any] | None = None) -> GuardResult:
        ctx = context or {}
        tool_name = ctx.get("tool_name", "")
        session_id = ctx.get("session_id", "default")

        # 1. 权限检查
        if self._allowed_tools is not None and tool_name not in self._allowed_tools:
            reason = f"工具 '{tool_name}' 未在允许列表中"
            logger.warning("tool_guard_blocked", reason=reason, tool=tool_name)
            return GuardResult.blocked(reason)

        # 2. 参数安全检查
        param_result = self._check_params(tool_name, content)
        if param_result is not None:
            return param_result

        # 3. 频率限制（滑动窗口）
        rate_result = self._check_rate_limit(session_id, tool_name)
        if rate_result is not None:
            return rate_result

        return GuardResult.passed()

    def _check_params(self, tool_name: str, args_json: str) -> GuardResult | None:
        """检查工具参数是否包含危险内容。"""
        args_lower = args_json.lower()

        if tool_name == "code_executor":
            for pattern in _DANGEROUS_CODE_PATTERNS:
                if pattern.lower() in args_lower:
                    reason = f"code_executor 参数包含危险代码: '{pattern}'"
                    logger.warning("tool_guard_blocked", reason=reason)
                    return GuardResult.blocked(reason)

        if tool_name == "http_request":
            try:
                args = json.loads(args_json)
                url = args.get("url", "")
            except Exception:
                url = args_json
            for pattern in _DANGEROUS_URL_PATTERNS:
                if pattern in url:
                    reason = f"http_request 目标 URL 包含内网/危险地址: '{pattern}'"
                    logger.warning("tool_guard_blocked", reason=reason, url=url[:80])
                    return GuardResult.blocked(reason)

        return None

    def _check_rate_limit(self, session_id: str, tool_name: str) -> GuardResult | None:
        """滑动窗口频率限制。"""
        key = f"{session_id}:{tool_name}"
        now = time.monotonic()
        window = self._call_times[key]

        # 移除窗口外的旧记录
        while window and window[0] < now - self._rate_window:
            window.popleft()

        if len(window) >= self._rate_limit:
            reason = (
                f"工具 '{tool_name}' 调用频率超限 "
                f"（{self._rate_limit} 次/{self._rate_window}s）"
            )
            logger.warning("tool_guard_rate_limited", tool=tool_name, session=session_id)
            return GuardResult.blocked(reason)

        window.append(now)
        return None

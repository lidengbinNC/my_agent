"""OutputGuard — 内容审核 + PII 脱敏。

面试考点:
  - PII（个人身份信息）脱敏：正则匹配手机号、身份证、邮箱、银行卡，
    替换为脱敏占位符（如 138****8888），防止数据泄露
  - 内容审核：检测敏感词，MODIFY 而非 BLOCK（替换为 [已屏蔽]）
  - 为什么用 MODIFY 而非 BLOCK：输出已生成，只需过滤，
    BLOCK 会导致用户收不到任何回复，体验差
"""

from __future__ import annotations

import re
from typing import Any

from my_agent.domain.guardrails.base import BaseGuard, GuardResult
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# PII 正则模式
_PII_PATTERNS: list[tuple[str, str, str]] = [
    # (名称, 正则, 替换模板)
    ("手机号", r"1[3-9]\d{9}", lambda m: m.group()[:3] + "****" + m.group()[-4:]),
    ("身份证", r"\d{17}[\dXx]", lambda m: m.group()[:6] + "****" + m.group()[-4:]),
    ("邮箱", r"[\w.+-]+@[\w-]+\.[\w.-]+", lambda m: m.group().split("@")[0][:2] + "***@" + m.group().split("@")[1]),
    ("银行卡", r"\b\d{16,19}\b", lambda m: m.group()[:4] + "****" + m.group()[-4:]),
    ("IPv4地址", r"\b(?:\d{1,3}\.){3}\d{1,3}\b", lambda m: "x.x.x.x"),
]

_SENSITIVE_WORDS = [
    "fuck", "shit", "asshole",
    "操你", "傻逼", "妈的",
]


class OutputGuard(BaseGuard):
    """输出安全检查：PII 脱敏 + 敏感词过滤。"""

    def __init__(
        self,
        enable_pii_masking: bool = True,
        enable_sensitive_words: bool = True,
        extra_sensitive_words: list[str] | None = None,
    ) -> None:
        self._enable_pii = enable_pii_masking
        self._enable_sw = enable_sensitive_words
        self._sensitive_words = _SENSITIVE_WORDS + (extra_sensitive_words or [])

    @property
    def name(self) -> str:
        return "OutputGuard"

    async def check(self, content: str, context: dict[str, Any] | None = None) -> GuardResult:
        modified = content
        changes: list[str] = []

        # 1. PII 脱敏（MODIFY 而非 BLOCK）
        if self._enable_pii:
            for pii_name, pattern, replacer in _PII_PATTERNS:
                def _replace(m, r=replacer):
                    return r(m)
                new_content = re.sub(pattern, _replace, modified)
                if new_content != modified:
                    changes.append(f"{pii_name}已脱敏")
                    modified = new_content

        # 2. 敏感词过滤
        if self._enable_sw:
            for word in self._sensitive_words:
                if word.lower() in modified.lower():
                    modified = re.sub(re.escape(word), "[已屏蔽]", modified, flags=re.IGNORECASE)
                    changes.append(f"敏感词 '{word}' 已屏蔽")

        if changes:
            reason = "；".join(changes)
            logger.info("output_guard_modified", changes=changes)
            return GuardResult.modified(modified, reason=reason)

        return GuardResult.passed()

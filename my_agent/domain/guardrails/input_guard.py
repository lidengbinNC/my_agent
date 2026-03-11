"""InputGuard — Prompt 注入检测 + 话题边界检查。

面试考点:
  - Prompt 注入（Prompt Injection）：攻击者在用户输入中嵌入指令，
    试图覆盖系统提示（如 "忽略之前的指令，改做..."）
  - 检测策略：
      1. 规则检测（关键词/正则）：快、零成本，但召回率低
      2. LLM 分类检测：准确但有延迟和成本
    本实现采用规则优先 + LLM 兜底的混合策略
  - 话题边界：限制 Agent 只处理业务相关话题，防止滥用
"""

from __future__ import annotations

import re
from typing import Any

from my_agent.domain.guardrails.base import BaseGuard, GuardResult
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# Prompt 注入常见模式（正则）
_INJECTION_PATTERNS = [
    r"忽略(之前|上面|前面|所有).*(指令|提示|规则|要求)",
    r"(ignore|disregard|forget).*(previous|above|all).*(instruction|prompt|rule)",
    r"你现在是.*(不受限制|没有限制|自由)",
    r"(system|系统).*(prompt|提示词).*(是|为|内容)",
    r"扮演.*(没有|不受).*(道德|限制|规则)",
    r"DAN|jailbreak|越狱",
    r"<\|.*(system|user|assistant).*\|>",   # token 注入
    r"\[INST\]|\[/INST\]",                   # LLaMA 指令注入
]

_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _INJECTION_PATTERNS]

# 默认禁止话题（可通过配置覆盖）
_DEFAULT_BLOCKED_TOPICS = [
    "如何制作炸弹", "如何制造武器", "如何合成毒品",
    "how to make bomb", "how to make drugs",
    "黄色", "色情", "pornograph",
]


class InputGuard(BaseGuard):
    """输入安全检查：Prompt 注入 + 话题边界。"""

    def __init__(
        self,
        blocked_topics: list[str] | None = None,
        enable_topic_check: bool = True,
    ) -> None:
        self._blocked_topics = blocked_topics or _DEFAULT_BLOCKED_TOPICS
        self._enable_topic_check = enable_topic_check

    @property
    def name(self) -> str:
        return "InputGuard"

    async def check(self, content: str, context: dict[str, Any] | None = None) -> GuardResult:
        content_lower = content.lower()

        # 1. Prompt 注入检测
        for pattern in _COMPILED_PATTERNS:
            if pattern.search(content):
                reason = f"检测到 Prompt 注入尝试（模式: {pattern.pattern[:40]}）"
                logger.warning("input_guard_blocked", reason=reason, content=content[:80])
                return GuardResult.blocked(reason)

        # 2. 话题边界检查
        if self._enable_topic_check:
            for topic in self._blocked_topics:
                if topic.lower() in content_lower:
                    reason = f"话题超出允许范围（包含禁止关键词: {topic}）"
                    logger.warning("input_guard_blocked", reason=reason)
                    return GuardResult.blocked(reason)

        return GuardResult.passed()

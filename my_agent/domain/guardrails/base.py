"""Guardrails 抽象基类 + 数据模型。

面试考点:
  - 责任链模式（Chain of Responsibility）：多个 Guard 串联，
    每个 Guard 处理自己关注的风险，互不感知
  - GuardAction: PASS（通过）/ BLOCK（拦截）/ MODIFY（修改内容后放行）
  - 每个 Guard 只返回 GuardResult，由 GuardChain 决定是否继续传递
  - 与业务逻辑解耦：Guard 只关注安全，不参与 Agent 推理
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GuardAction(str, Enum):
    PASS = "pass"       # 通过，继续执行
    BLOCK = "block"     # 拦截，直接返回错误给用户
    MODIFY = "modify"   # 修改内容后放行（如 PII 脱敏）


@dataclass
class GuardResult:
    """单个 Guard 的检查结果。"""

    action: GuardAction = GuardAction.PASS
    reason: str = ""                    # 拦截/修改原因
    modified_content: str | None = None # MODIFY 时替换的内容
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def passed(cls) -> "GuardResult":
        return cls(action=GuardAction.PASS)

    @classmethod
    def blocked(cls, reason: str) -> "GuardResult":
        return cls(action=GuardAction.BLOCK, reason=reason)

    @classmethod
    def modified(cls, content: str, reason: str = "") -> "GuardResult":
        return cls(action=GuardAction.MODIFY, modified_content=content, reason=reason)

    @property
    def is_blocked(self) -> bool:
        return self.action == GuardAction.BLOCK

    @property
    def is_passed(self) -> bool:
        return self.action == GuardAction.PASS


class BaseGuard(ABC):
    """Guard 抽象基类（责任链节点）。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """Guard 名称，用于日志和错误信息。"""

    @abstractmethod
    async def check(self, content: str, context: dict[str, Any] | None = None) -> GuardResult:
        """执行检查，返回 GuardResult。

        Args:
            content: 待检查内容（输入文本 / 输出文本 / 工具参数 JSON）
            context: 额外上下文（如用户 ID、会话 ID、工具名称等）
        """

"""GuardChain — 责任链：串联多个 Guard，逐一检查。

面试考点:
  - 责任链模式（Chain of Responsibility）：
      每个 Guard 只关注自己的职责，互不依赖
      链上任意一个 Guard BLOCK 即终止，不再继续检查
      MODIFY 时将修改后的内容传给下一个 Guard
  - 与 if-elif 的区别：链条可运行时动态增删 Guard，无需修改 GuardChain 代码
  - 执行顺序：InputGuard 在前（快速拦截），OutputGuard 在后（细化过滤）
"""

from __future__ import annotations

from typing import Any

from my_agent.domain.guardrails.base import BaseGuard, GuardAction, GuardResult
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class GuardChain:
    """Guard 责任链，按注册顺序依次执行。"""

    def __init__(self, guards: list[BaseGuard] | None = None) -> None:
        self._guards: list[BaseGuard] = guards or []

    def add(self, guard: BaseGuard) -> "GuardChain":
        """添加 Guard 到链尾（支持链式调用）。"""
        self._guards.append(guard)
        return self

    async def check(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> tuple[str, GuardResult | None]:
        """依次执行链上所有 Guard。

        Returns:
            (最终内容, 最后一个非 PASS 的 GuardResult 或 None)
            若某个 Guard BLOCK，立即返回，不再执行后续 Guard
        """
        current = content
        last_result: GuardResult | None = None

        for guard in self._guards:
            result = await guard.check(current, context)

            if result.action == GuardAction.BLOCK:
                logger.warning(
                    "guard_chain_blocked",
                    guard=guard.name,
                    reason=result.reason,
                    content=current[:80],
                )
                return current, result

            if result.action == GuardAction.MODIFY and result.modified_content is not None:
                logger.info(
                    "guard_chain_modified",
                    guard=guard.name,
                    reason=result.reason,
                )
                current = result.modified_content
                last_result = result

        return current, last_result


def build_default_input_chain() -> GuardChain:
    """构建默认的输入检查链。"""
    from my_agent.domain.guardrails.input_guard import InputGuard
    return GuardChain([InputGuard()])


def build_default_output_chain() -> GuardChain:
    """构建默认的输出检查链。"""
    from my_agent.domain.guardrails.output_guard import OutputGuard
    return GuardChain([OutputGuard()])


def build_default_tool_chain(allowed_tools: list[str] | None = None) -> GuardChain:
    """构建默认的工具调用检查链。"""
    from my_agent.domain.guardrails.tool_guard import ToolGuard
    return GuardChain([ToolGuard(allowed_tools=allowed_tools)])

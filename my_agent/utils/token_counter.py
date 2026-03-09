"""Token 计数工具 — 基于 tiktoken，精确统计消息 Token 用量。

面试考点:
  - tiktoken 的 cl100k_base 编码（GPT-4 / GPT-3.5 / 通义千问 兼容）
  - 每条消息的 Token 开销 = content tokens + 固定 overhead (4 tokens/message)
  - 上下文窗口管理：在发送前预估 Token，决定是否需要压缩历史
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from my_agent.domain.llm.message import Message

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


@lru_cache(maxsize=4)
def _get_encoding(model: str = "cl100k_base"):
    """获取 tiktoken 编码器（带缓存）。"""
    if not _TIKTOKEN_AVAILABLE:
        return None
    try:
        return tiktoken.get_encoding(model)
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "cl100k_base") -> int:
    """统计字符串的 Token 数量。"""
    enc = _get_encoding(model)
    if enc is None:
        # tiktoken 不可用时，用字符数 / 4 粗略估算
        return max(1, len(text) // 4)
    return len(enc.encode(text))


def count_messages_tokens(messages: list["Message"], model: str = "cl100k_base") -> int:
    """统计消息列表的总 Token 数。

    OpenAI 计算规则:
      每条消息 = content tokens + 4 (固定 overhead: role + separators)
      整体 + 3 (reply primer)
    """
    total = 3  # reply primer
    for msg in messages:
        total += 4  # per-message overhead
        if msg.content:
            total += count_tokens(msg.content, model)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                total += count_tokens(tc.name, model)
                total += count_tokens(tc.arguments, model)
    return total


def estimate_remaining_tokens(
    messages: list["Message"],
    max_context: int = 8192,
    reserved_for_output: int = 1024,
) -> int:
    """估算剩余可用 Token 数（用于判断是否需要压缩历史）。"""
    used = count_messages_tokens(messages)
    return max(0, max_context - reserved_for_output - used)

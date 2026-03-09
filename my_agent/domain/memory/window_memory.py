"""WindowMemory — 滑动窗口记忆，只保留最近 K 轮对话。

面试考点:
  - 滑动窗口策略：以轮次（turn）为单位截断，而非以 Token 为单位
  - 一轮 = 1 条 user + 1 条 assistant（成对出现）
  - 优点：Token 消耗固定上限；缺点：丢失早期上下文
  - 适用场景：长对话、实时客服、Token 预算敏感场景
"""

from __future__ import annotations

from collections import defaultdict, deque

from my_agent.domain.llm.message import Message, MessageRole
from my_agent.domain.memory.base import BaseMemory
from my_agent.utils.token_counter import count_messages_tokens


class WindowMemory(BaseMemory):
    """滑动窗口记忆，保留最近 K 轮（每轮 = user + assistant 各一条）。"""

    def __init__(self, window_size: int = 10) -> None:
        """
        Args:
            window_size: 保留最近多少轮对话（每轮含 user + assistant 两条消息）
        """
        self.window_size = window_size
        # session_id -> deque of Message
        self._store: dict[str, deque[Message]] = defaultdict(
            lambda: deque(maxlen=window_size * 2)
        )

    async def add_user_message(self, content: str, session_id: str | None = None) -> None:
        key = session_id or "_default"
        self._store[key].append(Message(role=MessageRole.USER, content=content))

    async def add_assistant_message(self, content: str, session_id: str | None = None) -> None:
        key = session_id or "_default"
        self._store[key].append(Message(role=MessageRole.ASSISTANT, content=content))

    async def get_history(self, session_id: str | None = None) -> list[Message]:
        key = session_id or "_default"
        return list(self._store[key])

    async def clear(self, session_id: str | None = None) -> None:
        key = session_id or "_default"
        self._store[key].clear()

    def token_count(self, session_id: str | None = None) -> int:
        key = session_id or "_default"
        return count_messages_tokens(list(self._store[key]))

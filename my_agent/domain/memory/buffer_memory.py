"""BufferMemory — 保留完整对话历史。

面试考点:
  - 最简单的记忆策略，适合短对话
  - 缺点：随对话轮数增加，Token 消耗线性增长，最终超出上下文窗口
  - 适用场景：单次任务、短会话、调试
"""

from __future__ import annotations

from collections import defaultdict

from my_agent.domain.llm.message import Message, MessageRole
from my_agent.domain.memory.base import BaseMemory
from my_agent.utils.token_counter import count_messages_tokens


class BufferMemory(BaseMemory):
    """完整对话历史记忆。"""

    def __init__(self) -> None:
        # session_id -> list[Message]
        self._store: dict[str, list[Message]] = defaultdict(list)

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
        return count_messages_tokens(self._store[key])

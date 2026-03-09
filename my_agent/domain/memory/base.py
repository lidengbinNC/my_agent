"""记忆系统抽象基类。

面试考点:
  - 为何需要多种记忆策略：不同场景对上下文长度 vs 精确度的权衡
  - BaseMemory 定义统一接口，上层 Agent 无需关心具体实现（策略模式）
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from my_agent.domain.llm.message import Message


class BaseMemory(ABC):
    """记忆系统抽象基类。"""

    @abstractmethod
    async def add_user_message(self, content: str, session_id: str | None = None) -> None:
        """追加用户消息。"""

    @abstractmethod
    async def add_assistant_message(self, content: str, session_id: str | None = None) -> None:
        """追加 AI 回复。"""

    @abstractmethod
    async def get_history(self, session_id: str | None = None) -> list[Message]:
        """获取用于注入 Prompt 的历史消息列表。"""

    @abstractmethod
    async def clear(self, session_id: str | None = None) -> None:
        """清空记忆。"""

    @abstractmethod
    def token_count(self, session_id: str | None = None) -> int:
        """当前记忆占用的 Token 数（用于上下文窗口管理）。"""

"""SummaryMemory — LLM 摘要压缩 + 近期消息。

面试考点:
  - 混合策略：远期历史 → LLM 摘要（1 条 system 消息），近期保留原文
  - 触发条件：当总 Token 超过阈值时，对"旧消息"进行摘要压缩
  - 摘要本身也消耗 Token，需要权衡摘要频率
  - 适用场景：长期对话助手、多轮任务规划
"""

from __future__ import annotations

from collections import defaultdict

from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import Message, MessageRole
from my_agent.domain.memory.base import BaseMemory
from my_agent.utils.logger import get_logger
from my_agent.utils.token_counter import count_messages_tokens

logger = get_logger(__name__)

_SUMMARY_PROMPT = """请将以下对话历史压缩为简洁的摘要，保留关键信息、用户意图和重要结论。
摘要应使用中文，不超过 300 字。

对话历史:
{history}

摘要:"""


class SummaryMemory(BaseMemory):
    """摘要压缩记忆：旧消息用 LLM 摘要，近期消息保留原文。"""

    def __init__(
        self,
        llm_client: BaseLLMClient,
        *,
        max_tokens: int = 2000,
        recent_keep: int = 6,
    ) -> None:
        """
        Args:
            llm_client: 用于生成摘要的 LLM 客户端
            max_tokens: 触发摘要压缩的 Token 阈值
            recent_keep: 压缩时保留最近多少条消息不压缩
        """
        self._llm = llm_client
        self.max_tokens = max_tokens
        self.recent_keep = recent_keep
        # session_id -> {"summary": str | None, "recent": list[Message]}
        self._store: dict[str, dict] = defaultdict(lambda: {"summary": None, "recent": []})

    async def add_user_message(self, content: str, session_id: str | None = None) -> None:
        key = session_id or "_default"
        self._store[key]["recent"].append(Message(role=MessageRole.USER, content=content))
        await self._maybe_compress(key)

    async def add_assistant_message(self, content: str, session_id: str | None = None) -> None:
        key = session_id or "_default"
        self._store[key]["recent"].append(Message(role=MessageRole.ASSISTANT, content=content))
        await self._maybe_compress(key)

    async def get_history(self, session_id: str | None = None) -> list[Message]:
        key = session_id or "_default"
        state = self._store[key]
        messages: list[Message] = []
        if state["summary"]:
            messages.append(
                Message(
                    role=MessageRole.SYSTEM,
                    content=f"[对话历史摘要]\n{state['summary']}",
                )
            )
        messages.extend(state["recent"])
        return messages

    async def clear(self, session_id: str | None = None) -> None:
        key = session_id or "_default"
        self._store[key] = {"summary": None, "recent": []}

    def token_count(self, session_id: str | None = None) -> int:
        key = session_id or "_default"
        state = self._store[key]
        total = 0
        if state["summary"]:
            from my_agent.utils.token_counter import count_tokens
            total += count_tokens(state["summary"])
        total += count_messages_tokens(state["recent"])
        return total

    async def _maybe_compress(self, key: str) -> None:
        """当 Token 超过阈值时，压缩旧消息为摘要。"""
        state = self._store[key]
        recent = state["recent"]
        if count_messages_tokens(recent) <= self.max_tokens:
            return

        # 保留最近 recent_keep 条，其余压缩
        to_compress = recent[: -self.recent_keep] if len(recent) > self.recent_keep else recent[:-2]
        keep = recent[len(to_compress) :]

        if not to_compress:
            return

        history_text = "\n".join(
            f"{m.role.value.upper()}: {m.content}" for m in to_compress if m.content
        )
        # 如果已有摘要，将其一并压缩
        if state["summary"]:
            history_text = f"[已有摘要]\n{state['summary']}\n\n[新增对话]\n{history_text}"

        try:
            prompt = _SUMMARY_PROMPT.format(history=history_text)
            resp = await self._llm.chat(
                messages=[Message(role=MessageRole.USER, content=prompt)],
                temperature=0.3,
            )
            state["summary"] = resp.content
            state["recent"] = keep
            logger.info("memory_compressed", session=key, kept=len(keep))
        except Exception as e:
            logger.warning("summary_compression_failed", error=str(e))

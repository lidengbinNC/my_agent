"""Token 计数工具 — 基于 tiktoken，精确统计消息 Token 用量。

面试考点:
  - tiktoken 的 cl100k_base 编码（GPT-4 / GPT-3.5 / 通义千问 兼容）
  - 每条消息的 Token 开销 = content tokens + 固定 overhead (4 tokens/message)
  - 分层预算控制：将上下文窗口按用途切分，在填充阶段就按预算截断，而非填完再报错
"""

from __future__ import annotations

from dataclasses import dataclass
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


# ---------------------------------------------------------------------------
# 分层预算控制
# ---------------------------------------------------------------------------

@dataclass
class ContextBudget:
    """上下文窗口的分层 Token 预算。

    将整个上下文窗口按用途切分，确保每一层都有确定的 Token 上限，
    在填充阶段就按预算截断，而不是填完所有内容再统一报错。

    各层含义：
      max_context       : 模型支持的最大上下文窗口（如 8192）
      system_budget     : System Prompt + 工具描述，固定占用
      few_shot_budget   : Few-shot 示例，固定占用
      output_budget     : 为模型输出预留，不能被输入占用
      history_budget    : 剩余空间全部分配给对话历史（动态计算）
      iteration_budget  : 每次 ReAct 迭代（Thought+Action+Observation）预估占用
    """

    max_context: int
    system_budget: int
    few_shot_budget: int
    output_budget: int
    iteration_budget: int

    @property
    def history_budget(self) -> int:
        """历史消息可用的 Token 上限（动态计算）。"""
        return max(
            0,
            self.max_context
            - self.system_budget
            - self.few_shot_budget
            - self.output_budget
            - self.iteration_budget,
        )

    @property
    def total_input_budget(self) -> int:
        """所有输入层的 Token 上限（不含输出预留）。"""
        return self.max_context - self.output_budget

    def remaining_after(self, used_tokens: int) -> int:
        """给定已用 Token 数，返回剩余可用量（相对于总输入预算）。"""
        return max(0, self.total_input_budget - used_tokens)

    def summary(self) -> dict[str, int]:
        """返回各层预算的可读摘要，用于日志记录。"""
        return {
            "max_context": self.max_context,
            "system_budget": self.system_budget,
            "few_shot_budget": self.few_shot_budget,
            "history_budget": self.history_budget,
            "iteration_budget": self.iteration_budget,
            "output_budget": self.output_budget,
        }


def build_context_budget(
    max_context: int = 8192,
    system_budget: int = 1500,
    few_shot_budget: int = 500,
    output_budget: int = 1024,
    iteration_budget: int = 800,
) -> ContextBudget:
    """构造 ContextBudget，并做基本合理性校验。"""
    fixed = system_budget + few_shot_budget + output_budget + iteration_budget
    if fixed >= max_context:
        raise ValueError(
            f"各固定层预算之和 ({fixed}) 已超过 max_context ({max_context})，"
            "请调整预算配置。"
        )
    return ContextBudget(
        max_context=max_context,
        system_budget=system_budget,
        few_shot_budget=few_shot_budget,
        output_budget=output_budget,
        iteration_budget=iteration_budget,
    )


def trim_history_to_budget(
    history: list["Message"],
    budget: int,
) -> list["Message"]:
    """将历史消息列表从最旧到最新地裁剪，直到总 Token 不超过 budget。

    裁剪策略：
      - 优先保留最近的消息（从尾部开始保留）
      - 以完整的一条消息为单位裁剪，不截断单条消息内容
    """
    if not history:
        return []

    # 从最新消息往前累加，直到超出预算为止
    kept: list["Message"] = []
    used = 3  # reply primer
    for msg in reversed(history):
        msg_tokens = 4  # per-message overhead
        if msg.content:
            msg_tokens += count_tokens(msg.content)
        if used + msg_tokens > budget:
            break
        kept.append(msg)
        used += msg_tokens

    kept.reverse()
    return kept

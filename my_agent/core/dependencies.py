"""依赖注入 — 管理全局单例组件的创建和生命周期。

面试考点:
  - 单例模式: LLM 客户端全局复用，避免重复创建连接池
  - FastAPI Depends: 声明式依赖注入
  - 生命周期管理: startup 创建 / shutdown 销毁
"""

from __future__ import annotations

from my_agent.config.settings import settings
from my_agent.core.engine.react_engine import ReActEngine
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.model_router import ComplexityLevel, ModelRouter, ModelTier
from my_agent.domain.llm.openai_client import OpenAIClient
from my_agent.domain.memory import BaseMemory, BufferMemory, SummaryMemory, WindowMemory
from my_agent.domain.tool.registry import get_registry
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# 全局单例
_llm_client: BaseLLMClient | None = None
_react_engine: ReActEngine | None = None


def _create_llm_client() -> BaseLLMClient:
    """根据配置创建 LLM 客户端。

    如果配置了多模型，返回 ModelRouter；否则返回单个 OpenAIClient。
    """
    default_cfg = settings.default_llm
    if not default_cfg.is_configured():
        raise RuntimeError(
            "LLM 未配置，请在 .env 中设置 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL"
        )

    default_client = OpenAIClient(
        api_key=default_cfg.api_key,
        base_url=default_cfg.base_url,
        model=default_cfg.model,
        timeout=settings.request_timeout,
        max_retries=settings.max_retries,
    )

    tiers: list[ModelTier] = [
        ModelTier(name="default", client=default_client, complexity=ComplexityLevel.MODERATE),
    ]

    lite_cfg = settings.lite_llm
    if lite_cfg.is_configured() and lite_cfg.model != default_cfg.model:
        lite_client = OpenAIClient(
            api_key=lite_cfg.api_key,
            base_url=lite_cfg.base_url,
            model=lite_cfg.model,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )
        tiers.append(ModelTier(name="lite", client=lite_client, complexity=ComplexityLevel.SIMPLE))

    strong_cfg = settings.strong_llm
    if strong_cfg.is_configured() and strong_cfg.model != default_cfg.model:
        strong_client = OpenAIClient(
            api_key=strong_cfg.api_key,
            base_url=strong_cfg.base_url,
            model=strong_cfg.model,
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
        )
        tiers.append(
            ModelTier(name="strong", client=strong_client, complexity=ComplexityLevel.COMPLEX)
        )

    if len(tiers) > 1:
        logger.info(
            "model_router_initialized",
            tiers=[t.name for t in tiers],
        )
        return ModelRouter(
            tiers=tiers,
            fallback_order=["strong", "default", "lite"],
        )

    logger.info("single_model_initialized", model=default_cfg.model)
    return default_client


def get_llm_client() -> BaseLLMClient:
    """FastAPI 依赖注入入口。"""
    global _llm_client
    if _llm_client is None:
        _llm_client = _create_llm_client()
    return _llm_client


def get_react_engine() -> ReActEngine:
    """获取 ReAct 引擎单例。"""
    global _react_engine
    if _react_engine is None:
        # 确保内置工具已注册
        import my_agent.domain.tool.builtin  # noqa: F401

        budget = settings.context_budget
        _react_engine = ReActEngine(
            llm=get_llm_client(),
            tool_registry=get_registry(),
            max_iterations=10,
            tool_timeout=30.0,
            budget=budget,
        )
        logger.info(
            "react_engine_initialized",
            tools=get_registry().names(),
            **budget.summary(),
        )
    return _react_engine


def create_memory(memory_type: str | None = None, turn_count: int = 0) -> BaseMemory:
    """根据 memory_type 和当前对话轮数创建对应的记忆策略实例。

    Args:
        memory_type: "auto" / "buffer" / "window" / "summary"
                     为 None 时读取全局配置 settings.memory_type
        turn_count:  当前会话已有的对话轮数（1轮 = 1条user + 1条assistant）
                     仅在 auto 模式下生效，用于自动选择策略

    auto 模式升级规则：
        轮数 < memory_buffer_turns  → BufferMemory  完整历史，精确，适合短对话
        轮数 < memory_window_turns  → WindowMemory  滑动窗口，省 Token，适合中等对话
        轮数 >= memory_window_turns → SummaryMemory 摘要压缩，长期记忆，适合长对话
    """
    mt = (memory_type or settings.memory_type).lower()

    if mt == "auto":
        if turn_count < settings.memory_buffer_turns:
            resolved = "buffer"
        elif turn_count < settings.memory_window_turns:
            resolved = "window"
        else:
            resolved = "summary"
        logger.info(
            "memory_auto_selected",
            turn_count=turn_count,
            resolved=resolved,
            buffer_threshold=settings.memory_buffer_turns,
            window_threshold=settings.memory_window_turns,
        )
        mt = resolved

    if mt == "buffer":
        return BufferMemory()
    if mt == "summary":
        return SummaryMemory(
            llm_client=get_llm_client(),
            max_tokens=settings.memory_max_tokens,
            recent_keep=settings.memory_recent_keep,
        )
    # 默认 window
    return WindowMemory(window_size=settings.memory_window_size)


async def shutdown_clients() -> None:
    global _llm_client, _react_engine
    _react_engine = None
    if _llm_client is not None:
        await _llm_client.close()
        _llm_client = None

"""多模型路由 Model Router — 根据任务复杂度选择最优模型 + Fallback 降级链。

面试考点:
  - 策略模式: 多种路由策略可切换（规则 / 分类 / 成本优先）
  - Fallback 降级链: 主模型失败 → 备选模型 → 兜底模型
  - 企业成本优化: 简单问题用便宜模型，复杂推理用贵模型
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator

from my_agent.domain.llm.base import BaseLLMClient, LLMResponse, StreamChunk
from my_agent.domain.llm.message import Message
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class ComplexityLevel(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass
class ModelTier:
    """一个模型层级的配置。"""

    name: str  # 层级名称，如 "lite" / "default" / "strong"
    client: BaseLLMClient
    complexity: ComplexityLevel


class ModelRouter(BaseLLMClient):
    """根据任务复杂度自动选择模型，支持 Fallback 降级。

    路由逻辑:
      1. 评估 query 复杂度 → SIMPLE / MODERATE / COMPLEX
      2. 选择对应层级的模型
      3. 调用失败 → 沿 Fallback 链降级
    """

    def __init__(
        self,
        tiers: list[ModelTier],
        fallback_order: list[str] | None = None,
    ) -> None:
        self._tiers = {t.name: t for t in tiers}
        self._complexity_map: dict[ComplexityLevel, str] = {
            t.complexity: t.name for t in tiers
        }
        self._fallback_order = fallback_order or [t.name for t in tiers]

    def assess_complexity(self, messages: list[Message]) -> ComplexityLevel:
        """规则化评估 query 复杂度。"""
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.role.value == "user" and msg.content:
                last_user_msg = msg.content
                break

        if not last_user_msg:
            return ComplexityLevel.MODERATE

        length = len(last_user_msg)
        complex_keywords = [
            "分析", "对比", "设计", "实现", "编写代码", "多步骤",
            "架构", "优化", "调试", "debug", "explain", "analyze",
        ]
        simple_keywords = [
            "你好", "谢谢", "是什么", "翻译", "hello", "hi",
            "帮我", "查一下",
        ]

        has_complex = any(kw in last_user_msg for kw in complex_keywords)
        has_simple = any(kw in last_user_msg for kw in simple_keywords)

        if has_complex or length > 200:
            return ComplexityLevel.COMPLEX
        if has_simple and length < 50:
            return ComplexityLevel.SIMPLE
        return ComplexityLevel.MODERATE

    def _select_client(self, complexity: ComplexityLevel) -> list[BaseLLMClient]:
        """返回按优先级排序的客户端列表（首选 + Fallback 链）。"""
        primary_name = self._complexity_map.get(complexity)
        ordered: list[BaseLLMClient] = []
        seen: set[str] = set()

        if primary_name and primary_name in self._tiers:
            ordered.append(self._tiers[primary_name].client)
            seen.add(primary_name)

        for name in self._fallback_order:
            if name not in seen and name in self._tiers:
                ordered.append(self._tiers[name].client)
                seen.add(name)

        return ordered

    async def chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        complexity = self.assess_complexity(messages)
        clients = self._select_client(complexity)
        logger.info("model_router_decision", complexity=complexity.value, candidates=len(clients))

        last_err: Exception | None = None
        for client in clients:
            try:
                return await client.chat(
                    messages, tools=tools, temperature=temperature,
                    max_tokens=max_tokens, response_format=response_format,
                    **kwargs,
                )
            except Exception as e:
                logger.warning("model_router_fallback", error=str(e))
                last_err = e
                await asyncio.sleep(0.5)

        raise RuntimeError(f"所有模型均调用失败: {last_err}")

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        complexity = self.assess_complexity(messages)
        clients = self._select_client(complexity)
        logger.info("model_router_stream_decision", complexity=complexity.value)

        last_err: Exception | None = None
        for client in clients:
            try:
                async for chunk in client.stream_chat(
                    messages, tools=tools, temperature=temperature,
                    max_tokens=max_tokens, **kwargs,
                ):
                    yield chunk
                return
            except Exception as e:
                logger.warning("model_router_stream_fallback", error=str(e))
                last_err = e

        raise RuntimeError(f"所有模型流式调用均失败: {last_err}")

    async def close(self) -> None:
        for tier in self._tiers.values():
            await tier.client.close()

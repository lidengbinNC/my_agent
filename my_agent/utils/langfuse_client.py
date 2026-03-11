"""LangFuse 集成 — LLM 调用链路追踪 + Prompt 版本管理。

面试考点:
  - LangFuse 是什么：开源 LLM 可观测性平台，提供：
      Trace（请求级）→ Span（步骤级）→ Generation（LLM 调用级）三层追踪
      Prompt 管理（版本化 + A/B 测试 + 效果对比）
      成本统计、质量评分、在线评估
  - 为什么用 LangFuse 而非自建：
      开源可自部署（数据不出域）
      OpenAI / LangChain / 自定义 SDK 均支持
      Prompt 在线管理避免频繁部署
  - 本实现：LangFuse Python SDK 集成 + 降级处理（未配置时静默跳过）

Docker 自部署 LangFuse:
  docker run -d -p 3000:3000 \\
    -e DATABASE_URL=postgresql://... \\
    -e NEXTAUTH_SECRET=... \\
    langfuse/langfuse:latest

  访问 http://localhost:3000，创建项目后获取 public_key / secret_key
"""

from __future__ import annotations

import functools
import time
from contextlib import contextmanager
from typing import Any, Generator

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

try:
    import langfuse as _lf_module
    _LANGFUSE_AVAILABLE = True
except ImportError:
    _LANGFUSE_AVAILABLE = False


class _NoopSpan:
    """LangFuse 不可用时的空操作 Span，避免到处 if 判断。"""
    def end(self, **kwargs): pass
    def update(self, **kwargs): pass
    def score(self, **kwargs): pass
    def generation(self, **kwargs): return self
    def span(self, **kwargs): return self


class _NoopTrace(_NoopSpan):
    def span(self, **kwargs): return _NoopSpan()
    def generation(self, **kwargs): return _NoopSpan()


class LangFuseClient:
    """LangFuse 客户端封装（带降级）。

    配置项（.env）：
      LANGFUSE_PUBLIC_KEY=pk-...
      LANGFUSE_SECRET_KEY=sk-...
      LANGFUSE_HOST=http://localhost:3000  # 自部署地址
    """

    def __init__(
        self,
        public_key: str = "",
        secret_key: str = "",
        host: str = "https://cloud.langfuse.com",
    ) -> None:
        self._enabled = False
        self._client = None

        if not _LANGFUSE_AVAILABLE:
            logger.info("langfuse_unavailable", reason="langfuse SDK 未安装，追踪功能已禁用")
            return

        if not public_key or not secret_key:
            logger.info("langfuse_disabled", reason="未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY")
            return

        try:
            self._client = _lf_module.Langfuse(
                public_key=public_key,
                secret_key=secret_key,
                host=host,
            )
            self._enabled = True
            logger.info("langfuse_initialized", host=host)
        except Exception as e:
            logger.warning("langfuse_init_failed", error=str(e))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def trace(
        self,
        name: str,
        user_id: str = "",
        session_id: str = "",
        metadata: dict[str, Any] | None = None,
    ):
        """创建一个 Trace（请求级追踪入口）。"""
        if not self._enabled or self._client is None:
            return _NoopTrace()
        try:
            return self._client.trace(
                name=name,
                user_id=user_id or None,
                session_id=session_id or None,
                metadata=metadata or {},
            )
        except Exception as e:
            logger.warning("langfuse_trace_failed", error=str(e))
            return _NoopTrace()

    def record_generation(
        self,
        trace,
        name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        input_messages: list[dict] | None = None,
        output: str = "",
        latency_ms: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """在 Trace 下记录一次 LLM Generation（Span 的子类型）。"""
        if not self._enabled:
            return
        try:
            trace.generation(
                name=name,
                model=model,
                usage={
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                input=input_messages or [],
                output=output,
                metadata={
                    **(metadata or {}),
                    "latency_ms": latency_ms,
                },
            )
        except Exception as e:
            logger.warning("langfuse_generation_failed", error=str(e))

    def get_prompt(self, prompt_name: str, version: int | None = None) -> str | None:
        """从 LangFuse 获取 Prompt（在线管理）。

        面试考点：Prompt 在线管理 = 修改 Prompt 无需重新部署服务
        """
        if not self._enabled or self._client is None:
            return None
        try:
            prompt_obj = self._client.get_prompt(prompt_name, version=version)
            return prompt_obj.prompt
        except Exception as e:
            logger.debug("langfuse_get_prompt_failed", name=prompt_name, error=str(e))
            return None

    def flush(self) -> None:
        """强制刷新缓冲区，确保数据上报（应用关闭时调用）。"""
        if self._enabled and self._client:
            try:
                self._client.flush()
            except Exception:
                pass


# 全局单例（懒加载）
_client: LangFuseClient | None = None


def get_langfuse_client() -> LangFuseClient:
    global _client
    if _client is None:
        from my_agent.config.settings import settings
        _client = LangFuseClient(
            public_key=getattr(settings, "langfuse_public_key", ""),
            secret_key=getattr(settings, "langfuse_secret_key", ""),
            host=getattr(settings, "langfuse_host", "https://cloud.langfuse.com"),
        )
    return _client

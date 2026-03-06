"""Pydantic Settings — 全局配置，支持 .env 文件和环境变量覆盖。"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """单个 LLM 端点配置。"""

    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 应用 ---
    app_name: str = "MyAgent"
    app_host: str = "0.0.0.0"
    app_port: int = 8001
    app_debug: bool = True
    log_level: str = "INFO"

    # --- 主 LLM（默认模型）---
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-plus"

    # --- 轻量 LLM（Model Router: 简单任务）---
    llm_lite_api_key: str = ""
    llm_lite_base_url: str = ""
    llm_lite_model: str = "qwen-turbo"

    # --- 强力 LLM（Model Router: 复杂任务）---
    llm_strong_api_key: str = ""
    llm_strong_base_url: str = ""
    llm_strong_model: str = ""

    # --- Token / 请求限制 ---
    max_tokens_per_request: int = Field(default=4096, ge=1)
    max_retries: int = Field(default=3, ge=0)
    request_timeout: int = Field(default=60, ge=1)

    # ----- 衍生配置 -----

    @property
    def default_llm(self) -> LLMConfig:
        return LLMConfig(
            api_key=self.llm_api_key,
            base_url=self.llm_base_url,
            model=self.llm_model,
        )

    @property
    def lite_llm(self) -> LLMConfig:
        api_key = self.llm_lite_api_key or self.llm_api_key
        base_url = self.llm_lite_base_url or self.llm_base_url
        return LLMConfig(api_key=api_key, base_url=base_url, model=self.llm_lite_model)

    @property
    def strong_llm(self) -> LLMConfig:
        api_key = self.llm_strong_api_key or self.llm_api_key
        base_url = self.llm_strong_base_url or self.llm_base_url
        model = self.llm_strong_model or self.llm_model
        return LLMConfig(api_key=api_key, base_url=base_url, model=model)


settings = Settings()

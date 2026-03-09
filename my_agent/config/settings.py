"""Pydantic Settings — 全局配置，支持 .env 文件和环境变量覆盖。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（my_agent/config/settings.py -> 上两级即为项目根）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"


class LLMConfig(BaseSettings):
    """单个 LLM 端点配置。"""

    api_key: str = ""
    base_url: str = ""
    model: str = ""

    def is_configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- 应用（有合理默认值，允许不配置）---
    app_name: str = "MyAgent"
    app_host: str = "0.0.0.0"
    app_port: int = 8001
    app_debug: bool = False
    log_level: str = "INFO"

    # --- 主 LLM（必填，无默认值）---
    llm_api_key: str
    llm_base_url: str
    llm_model: str

    # --- 轻量 LLM（可选，未配置时复用主模型）---
    llm_lite_api_key: str = ""
    llm_lite_base_url: str = ""
    llm_lite_model: str = ""

    # --- 强力 LLM（可选，未配置时复用主模型）---
    llm_strong_api_key: str = ""
    llm_strong_base_url: str = ""
    llm_strong_model: str = ""

    # --- Agent 行为（必填，无默认值）---
    system_prompt: str

    # --- Token / 请求限制（必填，无默认值）---
    max_tokens_per_request: int = Field(ge=1)
    max_retries: int = Field(ge=0)
    request_timeout: int = Field(ge=1)

    # --- 数据库（默认 SQLite，可切换 PostgreSQL）---
    database_url: str = "sqlite+aiosqlite:///./my_agent.db"

    # --- 记忆系统 ---
    memory_type: str = "window"          # buffer / window / summary
    memory_window_size: int = 10         # WindowMemory 保留轮数
    memory_max_tokens: int = 2000        # SummaryMemory 触发阈值
    memory_recent_keep: int = 6          # SummaryMemory 保留近期条数

    @field_validator("llm_api_key", "llm_base_url", "llm_model", "system_prompt")
    @classmethod
    def must_not_be_empty(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} 不能为空，请在 .env 中配置")
        return v

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
        model = self.llm_lite_model or self.llm_model
        return LLMConfig(api_key=api_key, base_url=base_url, model=model)

    @property
    def strong_llm(self) -> LLMConfig:
        api_key = self.llm_strong_api_key or self.llm_api_key
        base_url = self.llm_strong_base_url or self.llm_base_url
        model = self.llm_strong_model or self.llm_model
        return LLMConfig(api_key=api_key, base_url=base_url, model=model)


settings = Settings()

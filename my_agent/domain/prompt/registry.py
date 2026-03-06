"""Prompt 版本管理注册中心。

面试考点:
  - Prompt 版本化：每个 Prompt 有 name + version，支持回滚
  - 动态加载：代码中不硬编码 Prompt 内容，通过 registry.get() 获取
  - A/B 测试基础：canary 状态支持灰度流量切换
  - 模板渲染：支持 {variable} 占位符替换
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class PromptStatus(str, Enum):
    DRAFT = "draft"       # 草稿，仅开发测试
    CANARY = "canary"     # 灰度，部分流量
    STABLE = "stable"     # 稳定，全量生产
    ARCHIVED = "archived" # 归档，不再使用


@dataclass
class PromptVersion:
    name: str
    version: str
    template: str
    description: str = ""
    status: PromptStatus = PromptStatus.DRAFT
    metadata: dict[str, Any] = field(default_factory=dict)

    def render(self, **kwargs: Any) -> str:
        """渲染 Prompt 模板，替换 {variable} 占位符。"""
        try:
            return self.template.format(**kwargs)
        except KeyError as e:
            logger.warning("prompt_render_missing_var", prompt=self.name, var=str(e))
            return self.template


class PromptRegistry:
    """Prompt 版本管理注册中心（单例）。"""

    _instance: "PromptRegistry | None" = None

    def __new__(cls) -> "PromptRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            # name → {version → PromptVersion}
            cls._instance._store: dict[str, dict[str, PromptVersion]] = {}
        return cls._instance

    def register(self, prompt: PromptVersion) -> None:
        if prompt.name not in self._store:
            self._store[prompt.name] = {}
        self._store[prompt.name][prompt.version] = prompt
        logger.debug(
            "prompt_registered",
            name=prompt.name,
            version=prompt.version,
            status=prompt.status.value,
        )

    def get(self, name: str, version: str = "latest") -> PromptVersion | None:
        """获取 Prompt。version='latest' 返回最新 stable，否则按版本号查找。"""
        versions = self._store.get(name)
        if not versions:
            return None
        if version == "latest":
            # 优先返回 stable，其次 canary，最后 draft
            for status in (PromptStatus.STABLE, PromptStatus.CANARY, PromptStatus.DRAFT):
                for pv in reversed(list(versions.values())):
                    if pv.status == status:
                        return pv
            return list(versions.values())[-1]
        return versions.get(version)

    def render(self, name: str, version: str = "latest", **kwargs: Any) -> str:
        """获取并渲染 Prompt，找不到时抛出 KeyError。"""
        pv = self.get(name, version)
        if pv is None:
            raise KeyError(f"Prompt '{name}' (version={version}) 未注册")
        return pv.render(**kwargs)

    def list_prompts(self) -> list[dict[str, Any]]:
        result = []
        for name, versions in self._store.items():
            for pv in versions.values():
                result.append({
                    "name": pv.name,
                    "version": pv.version,
                    "status": pv.status.value,
                    "description": pv.description,
                })
        return result

    def update_status(self, name: str, version: str, status: PromptStatus) -> bool:
        pv = self.get(name, version)
        if pv is None:
            return False
        pv.status = status
        logger.info("prompt_status_updated", name=name, version=version, status=status.value)
        return True


_registry = PromptRegistry()


def get_prompt_registry() -> PromptRegistry:
    return _registry

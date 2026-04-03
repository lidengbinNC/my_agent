"""内置工具自动注册 — import 此包即完成所有内置工具的注册。"""

from my_agent.domain.tool.builtin import (
    calculator,
    code_executor,
    customer_service_tools,
    http_request,
    web_search,
)

__all__ = [
    "calculator",
    "code_executor",
    "customer_service_tools",
    "http_request",
    "web_search",
]

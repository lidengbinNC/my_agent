"""HTTP 请求工具 — 发送 GET/POST 请求并返回响应内容。

面试考点:
  - 安全限制: 禁止访问内网 IP（防止 SSRF 攻击）
  - 响应内容截取，避免 Observation 过长
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

import httpx

from my_agent.domain.tool.base import ToolResult
from my_agent.domain.tool.registry import tool

_BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}
_MAX_RESPONSE_LEN = 3000


def _is_private_ip(host: str) -> bool:
    """检测是否为内网 IP（防止 SSRF）。"""
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        return False


def _validate_url(url: str) -> str | None:
    """验证 URL 安全性，返回错误信息或 None（安全）。"""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "只支持 http/https 协议"
        host = parsed.hostname or ""
        if host in _BLOCKED_HOSTS or _is_private_ip(host):
            return f"禁止访问内网地址: {host}"
    except Exception as e:
        return f"URL 解析失败: {e}"
    return None


@tool(description="发送 HTTP GET 请求并返回响应内容")
async def http_get(url: str) -> ToolResult:
    """发送 HTTP GET 请求。

    :param url: 请求的完整 URL（仅支持公网地址）
    """
    if err := _validate_url(url):
        return ToolResult.fail(err)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(
                url, headers={"User-Agent": "MyAgent/0.1"}
            )
            content = resp.text[:_MAX_RESPONSE_LEN]
            if len(resp.text) > _MAX_RESPONSE_LEN:
                content += "\n...[响应已截断]"
            return ToolResult.ok(
                f"HTTP {resp.status_code} {url}\n\n{content}",
                status_code=resp.status_code,
            )
    except httpx.TimeoutException:
        return ToolResult.fail(f"请求超时: {url}")
    except Exception as e:
        return ToolResult.fail(str(e))


@tool(description="发送 HTTP POST 请求（JSON 格式）并返回响应内容")
async def http_post(url: str, body: str) -> ToolResult:
    """发送 HTTP POST 请求（JSON body）。

    :param url: 请求的完整 URL（仅支持公网地址）
    :param body: JSON 格式的请求体字符串
    """
    import json

    if err := _validate_url(url):
        return ToolResult.fail(err)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        return ToolResult.fail(f"body 不是合法 JSON: {e}")
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"User-Agent": "MyAgent/0.1"},
            )
            content = resp.text[:_MAX_RESPONSE_LEN]
            return ToolResult.ok(
                f"HTTP {resp.status_code} {url}\n\n{content}",
                status_code=resp.status_code,
            )
    except httpx.TimeoutException:
        return ToolResult.fail(f"请求超时: {url}")
    except Exception as e:
        return ToolResult.fail(str(e))

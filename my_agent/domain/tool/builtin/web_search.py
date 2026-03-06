"""网络搜索工具 — 调用 DuckDuckGo 免费搜索 API（无需 Key）。

面试考点:
  - 免费搜索 API 集成（DuckDuckGo Instant Answer API）
  - httpx 异步 HTTP 请求
  - 结果摘要截取，避免 Observation 过长
"""

from __future__ import annotations

import httpx

from my_agent.domain.tool.base import ToolResult
from my_agent.domain.tool.registry import tool


@tool(description="搜索互联网获取实时信息，返回搜索摘要")
async def web_search(query: str, max_results: int = 3) -> ToolResult:
    """搜索互联网获取实时信息。

    :param query: 搜索关键词或问题
    :param max_results: 返回结果数量（默认 3）
    """
    try:
        # DuckDuckGo Instant Answer API（免费，无需 Key）
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": query,
                    "format": "json",
                    "no_html": "1",
                    "skip_disambig": "1",
                },
                headers={"User-Agent": "MyAgent/0.1"},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[str] = []

        # Abstract（摘要）
        if abstract := data.get("AbstractText"):
            source = data.get("AbstractSource", "")
            results.append(f"摘要（{source}）: {abstract}")

        # Related Topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and (text := topic.get("Text")):
                results.append(f"• {text}")

        if not results:
            # 没有即时答案，返回提示
            return ToolResult.ok(
                f"未找到关于「{query}」的即时答案。建议使用更具体的关键词重新搜索。"
            )

        output = f"搜索「{query}」的结果:\n\n" + "\n".join(results)
        return ToolResult.ok(output)

    except httpx.TimeoutException:
        return ToolResult.fail("搜索请求超时，请稍后重试")
    except Exception as e:
        return ToolResult.fail(f"搜索失败: {e}")

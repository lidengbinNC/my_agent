"""网络搜索工具 — 使用 ddgs 库实现真实搜索（免费，无需 Key）。

面试考点:
  - ddgs 库（原 duckduckgo-search）：直接调用 DuckDuckGo 搜索，返回真实结果列表
  - 同步库 + asyncio.to_thread：将阻塞 I/O 放到线程池，不阻塞事件循环
  - 结果摘要截取，避免 Observation 过长
"""

from __future__ import annotations

import asyncio

from my_agent.domain.tool.base import ToolResult
from my_agent.domain.tool.registry import tool


@tool(description="搜索互联网获取实时信息，返回真实搜索结果列表")
async def web_search(query: str, max_results: int = 5) -> ToolResult:
    """搜索互联网获取实时信息。

    :param query: 搜索关键词或问题
    :param max_results: 返回结果数量（默认 5）
    """
    try:
        from ddgs import DDGS

        def _search() -> list[dict]:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=max_results))

        # ddgs 是同步库，放入线程池避免阻塞事件循环
        results = await asyncio.to_thread(_search)

        if not results:
            return ToolResult.ok(
                f"未找到关于「{query}」的搜索结果，建议换用更具体的关键词重试。"
            )

        lines: list[str] = [f"搜索「{query}」的结果:\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            body  = r.get("body", "")[:300]   # 每条摘要最多 300 字符
            href  = r.get("href", "")
            lines.append(f"{i}. **{title}**\n   {body}\n   来源: {href}")

        return ToolResult.ok("\n\n".join(lines))

    except ImportError:
        return ToolResult.fail(
            "ddgs 库未安装，请执行: pip install ddgs"
        )
    except Exception as e:
        return ToolResult.fail(f"搜索失败: {e}")

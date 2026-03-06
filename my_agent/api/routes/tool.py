"""工具管理路由 — 列出可用工具 + 测试单个工具。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from my_agent.core.dependencies import get_react_engine
from my_agent.core.engine.react_engine import ReActEngine

router = APIRouter(tags=["tools"])


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}


@router.get("/tools")
async def list_tools(engine: ReActEngine = Depends(get_react_engine)):
    """获取所有已注册工具的列表和 Schema。"""
    tools = engine._registry.all()
    return {
        "count": len(tools),
        "tools": [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters_schema,
            }
            for t in tools
        ],
    }


@router.post("/tools/{name}/test")
async def test_tool(
    name: str,
    req: ToolCallRequest,
    engine: ReActEngine = Depends(get_react_engine),
):
    """测试调用指定工具。"""
    if engine._registry.get(name) is None:
        raise HTTPException(status_code=404, detail=f"工具 '{name}' 不存在")

    result = await engine._executor.execute(name, req.arguments)
    return {
        "tool": name,
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }

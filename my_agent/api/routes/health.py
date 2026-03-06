"""健康检查路由。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["system"])


@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "healthy",
        "service": "my_agent",
        "version": "0.1.0",
    }

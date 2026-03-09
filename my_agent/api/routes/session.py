"""会话管理 API — 创建 / 列表 / 历史 / 删除。

面试考点:
  - RESTful 设计：资源路径 /sessions, /sessions/{id}/history
  - 软删除：DELETE 不物理删除，只标记 is_deleted
  - 依赖注入：get_db() 提供异步数据库会话
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from my_agent.api.schemas.session import (
    MessageInfo,
    SessionCreate,
    SessionHistory,
    SessionInfo,
)
from my_agent.infrastructure.db.database import get_db
from my_agent.infrastructure.db.repository import MessageRepository, SessionRepository

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionInfo, status_code=201)
async def create_session(
    body: SessionCreate,
    db: AsyncSession = Depends(get_db),
) -> SessionInfo:
    """创建新会话。"""
    repo = SessionRepository(db)
    session = await repo.create(title=body.title, memory_type=body.memory_type)
    return SessionInfo.model_validate(session)


@router.get("", response_model=list[SessionInfo])
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[SessionInfo]:
    """列出所有会话（按更新时间倒序）。"""
    repo = SessionRepository(db)
    sessions = await repo.list_all(limit=limit, offset=offset)
    return [SessionInfo.model_validate(s) for s in sessions]


@router.get("/{session_id}/history", response_model=SessionHistory)
async def get_session_history(
    session_id: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
) -> SessionHistory:
    """获取会话的完整消息历史。"""
    s_repo = SessionRepository(db)
    m_repo = MessageRepository(db)

    session = await s_repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = await m_repo.get_history(session_id, limit=limit)
    total_tokens = sum(m.token_count for m in messages)

    return SessionHistory(
        session=SessionInfo.model_validate(session),
        messages=[MessageInfo.model_validate(m) for m in messages],
        total_tokens=total_tokens,
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    """软删除会话（保留数据，仅标记删除）。"""
    repo = SessionRepository(db)
    session = await repo.get_by_id(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    await repo.soft_delete(session_id)

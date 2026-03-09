"""数据库连接管理 — 异步 SQLAlchemy Engine + Session 工厂。

面试考点:
  - create_async_engine：异步引擎，配合 asyncio 使用
  - async_sessionmaker：会话工厂，每次请求创建独立 Session（避免并发冲突）
  - check_same_thread=False：SQLite 在多线程/异步环境的必要配置
  - pool_pre_ping：连接池健康检查，防止使用已断开的连接
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from my_agent.config.settings import settings
from my_agent.infrastructure.db.models import Base

engine = create_async_engine(
    settings.database_url,
    echo=settings.app_debug,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    pool_pre_ping=True,
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """创建所有表（首次启动时调用）。"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:  # type: ignore[return]
    """FastAPI Depends 依赖函数，提供数据库会话。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

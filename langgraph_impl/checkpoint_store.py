"""LangGraph Checkpoint 单例管理。

职责：
  - 持有全局唯一的 SqliteSaver 实例（写入 lg_checkpoints.db）
  - 持有全局唯一的已编译图实例（conversation_graph / review_graph）
  - 提供初始化 / 关闭接口，供 FastAPI lifespan 调用

面试考点：
  - SqliteSaver vs MemorySaver：SqliteSaver 将每个 checkpoint 写入 SQLite，
    进程重启后仍可通过 thread_id + checkpoint_id 恢复状态；MemorySaver 仅内存，重启丢失。
  - 单例模式：checkpointer 必须是应用级单例，多个请求共享同一个 checkpointer 实例，
    才能实现跨请求的状态持久化。
  - SQLite 表结构（由 SqliteSaver 自动创建）：
      checkpoints      — 每次图执行后的完整 State 快照（JSON 序列化）
      checkpoint_blobs — 大型 State 字段的分块存储
      checkpoint_writes — 节点写操作记录（用于 interrupt_before 恢复）
    可直接用 sqlite3 / DB Browser 查看这三张表，观察 State 变化。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# SQLite 文件路径（项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_DB_PATH = str(_PROJECT_ROOT / "lg_checkpoints.db")

# 模块级单例
_checkpointer: Any = None          # SqliteSaver 实例
_conversation_app: Any = None      # 带 checkpoint 的对话图（编译后）
_review_app: Any = None            # 带 HitL 的审核图（编译后）


def get_checkpointer() -> Any:
    """获取全局 SqliteSaver 实例，未初始化则抛出 RuntimeError。"""
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer 未初始化，请先调用 init_checkpointer()。"
            "通常在 FastAPI lifespan 的 startup 阶段调用。"
        )
    return _checkpointer


def get_conversation_app() -> Any:
    """获取全局对话图实例（带 SqliteSaver checkpoint）。"""
    if _conversation_app is None:
        raise RuntimeError("Conversation graph 未初始化，请先调用 init_checkpointer()。")
    return _conversation_app


def get_review_app() -> Any:
    """获取全局 HitL 审核图实例（带 SqliteSaver checkpoint + interrupt_before）。"""
    if _review_app is None:
        raise RuntimeError("Review graph 未初始化，请先调用 init_checkpointer()。")
    return _review_app


async def init_checkpointer() -> None:
    """初始化 AsyncSqliteSaver 和所有图实例。在 FastAPI lifespan startup 阶段调用。

    面试考点：
      - AsyncSqliteSaver 使用 aiosqlite 异步驱动，需要先建立 aiosqlite.Connection
      - 两张图共享同一个 checkpointer 实例，通过 thread_id 隔离不同会话
      - setup() 是异步方法，负责创建 SQLite 表（checkpoints / checkpoint_blobs / checkpoint_writes）
    """
    global _checkpointer, _conversation_app, _review_app

    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph_impl.checkpoint_demo import (
        build_conversation_graph_with_checkpoint,
        build_human_review_graph,
    )

    # 手动创建 aiosqlite 连接，保持连接在整个应用生命周期内存活
    conn = await aiosqlite.connect(CHECKPOINT_DB_PATH)
    saver = AsyncSqliteSaver(conn)
    # setup() 创建 SQLite 表结构（幂等，已存在则跳过）
    await saver.setup()
    _checkpointer = saver

    # 用同一个 checkpointer 编译两张图
    _conversation_app = build_conversation_graph_with_checkpoint(checkpointer=saver)
    _review_app = build_human_review_graph(checkpointer=saver)

    logger.info(
        "lg_checkpointer_initialized",
        db_path=CHECKPOINT_DB_PATH,
        backend="AsyncSqliteSaver",
    )


async def shutdown_checkpointer() -> None:
    """关闭 AsyncSqliteSaver 连接。在 FastAPI lifespan shutdown 阶段调用。"""
    global _checkpointer, _conversation_app, _review_app
    if _checkpointer is not None:
        try:
            # 关闭底层 aiosqlite 连接
            await _checkpointer.conn.close()
            logger.info("lg_checkpointer_closed")
        except Exception as e:
            logger.warning("lg_checkpointer_close_error", error=str(e))
    _checkpointer = None
    _conversation_app = None
    _review_app = None

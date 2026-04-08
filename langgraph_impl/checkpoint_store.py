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

from pathlib import Path
from typing import Any

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

# SQLite 文件路径（项目根目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHECKPOINT_DB_PATH = str(_PROJECT_ROOT / "lg_checkpoints.db")

# 模块级单例
_checkpointer: Any = None          # SqliteSaver 实例
_saver_cm: Any = None              # AsyncSqliteSaver 上下文管理器（用于 shutdown 时关闭连接）
_conversation_app: Any = None      # 带 checkpoint 的简单对话图（编译后）
_review_app: Any = None            # 带 HitL 的审核图（编译后）
_react_app: Any = None             # 带 checkpoint 的 ReAct Agent 图（编译后）
_multi_agent_app: Any = None       # 带 checkpoint 的多 Agent 图（编译后）


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


def get_react_app() -> Any:
    """获取全局 ReAct Agent 图实例（带 SqliteSaver checkpoint）。

    面试考点：
      - 图必须是应用级单例，不能每次请求都 compile_react_graph()
      - 每次 compile 会创建新图实例，虽然 checkpointer 是单例，
        但新图实例不知道"上一次请求用的是哪个图实例"，无法正确加载历史
      - 单例图 + 单例 checkpointer + 相同 thread_id = 跨请求状态持久化
    """
    if _react_app is None:
        raise RuntimeError("ReAct graph 未初始化，请先调用 init_checkpointer()。")
    return _react_app


def get_multi_agent_app() -> Any:
    """获取全局多 Agent 图实例（带 SqliteSaver checkpoint）。"""
    if _multi_agent_app is None:
        raise RuntimeError("Multi-agent graph 未初始化，请先调用 init_checkpointer()。")
    return _multi_agent_app




async def init_checkpointer() -> None:
    """初始化 AsyncSqliteSaver 和所有图实例。在 FastAPI lifespan startup 阶段调用。

    面试考点：
      - AsyncSqliteSaver.from_conn_string() 是官方推荐的初始化方式，内部管理连接
      - 所有图共享同一个 checkpointer 实例，通过 thread_id 隔离不同会话
      - setup() 是异步方法，负责创建 SQLite 表（checkpoints / writes）
      - 图实例必须是单例：同一个图实例 + 同一个 checkpointer + 相同 thread_id
        才能正确加载历史 checkpoint，每次请求重新 compile 会丢失 checkpoint 关联
    """
    global _checkpointer, _conversation_app, _review_app, _react_app, _multi_agent_app, _saver_cm

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph_impl.checkpoint_demo import (
        build_conversation_graph_with_checkpoint,
        build_human_review_graph,
    )
    from langgraph_impl.multi_agent_runtime import build_multi_agent_graph
    from langgraph_impl.react_agent import compile_react_graph

    # 使用官方推荐的 from_conn_string() 上下文管理器方式初始化
    # __aenter__ 内部会建立 aiosqlite 连接并调用 setup()
    _saver_cm = AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB_PATH)
    saver = await _saver_cm.__aenter__()
    _checkpointer = saver

    # 所有图共享同一个 checkpointer，通过 thread_id 隔离会话
    _conversation_app = build_conversation_graph_with_checkpoint(checkpointer=saver)
    _review_app = build_human_review_graph(checkpointer=saver)
    _react_app = compile_react_graph(checkpointer=saver)
    _multi_agent_app = build_multi_agent_graph(checkpointer=saver)

    logger.info(
        "lg_checkpointer_initialized",
        db_path=CHECKPOINT_DB_PATH,
        backend="AsyncSqliteSaver",
        graphs=["conversation", "review", "react", "multi_agent"],
    )


async def shutdown_checkpointer() -> None:
    """关闭 AsyncSqliteSaver 连接。在 FastAPI lifespan shutdown 阶段调用。"""
    global _checkpointer, _conversation_app, _review_app, _react_app, _multi_agent_app, _saver_cm
    if _saver_cm is not None:
        try:
            await _saver_cm.__aexit__(None, None, None)
            logger.info("lg_checkpointer_closed")
        except Exception as e:
            logger.warning("lg_checkpointer_close_error", error=str(e))
    _checkpointer = None
    _saver_cm = None
    _conversation_app = None
    _review_app = None
    _react_app = None
    _multi_agent_app = None

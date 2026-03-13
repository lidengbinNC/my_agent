"""端到端测试：验证 Checkpoint 数据写入 SQLite。

运行方式：python test_checkpoint_e2e.py
"""
import asyncio
import sqlite3
import sys

# Windows GBK 终端兼容：将 stdout 替换为 UTF-8 模式
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from langchain_core.messages import HumanMessage

from langgraph_impl.checkpoint_store import (
    init_checkpointer,
    shutdown_checkpointer,
    get_conversation_app,
    get_review_app,
    CHECKPOINT_DB_PATH,
)
from langgraph_impl.checkpoint_demo import ReviewState, get_thread_history


def count_checkpoints(thread_id: str) -> int:
    conn = sqlite3.connect(CHECKPOINT_DB_PATH)
    rows = conn.execute(
        "SELECT COUNT(*) FROM checkpoints WHERE thread_id=?", (thread_id,)
    ).fetchone()
    conn.close()
    return rows[0]


def show_checkpoints(thread_id: str):
    conn = sqlite3.connect(CHECKPOINT_DB_PATH)
    rows = conn.execute(
        "SELECT checkpoint_id, parent_checkpoint_id, metadata FROM checkpoints WHERE thread_id=? ORDER BY rowid",
        (thread_id,),
    ).fetchall()
    conn.close()
    print(f"\n  [SQLite] thread_id={thread_id} 共 {len(rows)} 条 checkpoint:")
    for i, (cid, parent, meta) in enumerate(rows):
        print(f"    [{i+1}] checkpoint_id={cid[:16]}... parent={str(parent)[:16] if parent else 'None'}")
        print(f"         metadata={meta[:120]}")


async def test_conversation():
    print("\n=== 测试1: 多轮对话 Checkpoint ===")
    app = get_conversation_app()
    thread_id = "e2e-conv-001"

    for i, msg in enumerate(["我叫小明，是 Python 开发者", "我刚才说我叫什么名字？"], 1):
        result = await app.ainvoke(
            {"messages": [HumanMessage(content=msg)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        answer = result["messages"][-1].content
        print(f"  轮{i} Q: {msg}")
        print(f"  轮{i} A: {answer[:80]}...")
        n = count_checkpoints(thread_id)
        print(f"  → SQLite checkpoints 表已有 {n} 条记录")

    show_checkpoints(thread_id)
    print("  [PASS] 多轮对话测试通过")


async def test_hitl():
    print("\n=== 测试2: Human-in-the-Loop ===")
    app = get_review_app()
    thread_id = "e2e-hitl-001"

    # 步骤1: 提交任务，暂停在 revise 前
    init_state: ReviewState = {
        "messages": [HumanMessage(content="请写一篇关于 LangGraph Checkpoint 的技术介绍（50字）")],
        "draft": "", "approved": False, "needs_revision": False, "human_feedback": "",
    }
    result = await app.ainvoke(init_state, config={"configurable": {"thread_id": thread_id}})
    draft = result.get("draft", "")
    state = await app.aget_state({"configurable": {"thread_id": thread_id}})
    next_nodes = list(state.next)
    n1 = count_checkpoints(thread_id)
    print(f"  步骤1: 草稿生成，暂停在 {next_nodes}")
    print(f"  草稿: {draft[:80]}...")
    print(f"  → SQLite 已有 {n1} 条 checkpoint")

    # 步骤2: 注入人工反馈
    await app.aupdate_state(
        config={"configurable": {"thread_id": thread_id}},
        values={"human_feedback": "请增加 SqliteSaver 与 MemorySaver 的对比说明", "needs_revision": True},
    )
    n2 = count_checkpoints(thread_id)
    print(f"\n  步骤2: 注入反馈后 → SQLite 已有 {n2} 条 checkpoint（+{n2-n1} 条）")

    # 步骤3: 从断点继续
    final = await app.ainvoke(None, config={"configurable": {"thread_id": thread_id}})
    n3 = count_checkpoints(thread_id)
    print(f"\n  步骤3: 继续执行完成 → SQLite 已有 {n3} 条 checkpoint（+{n3-n2} 条）")
    print(f"  最终草稿: {final.get('draft', '')[:80]}...")
    print(f"  approved: {final.get('approved', False)}")

    show_checkpoints(thread_id)
    print("  [PASS] Human-in-the-Loop 测试通过")


async def test_history_api():
    print("\n=== 测试3: get_thread_history API ===")
    app = get_conversation_app()
    thread_id = "e2e-conv-001"  # 复用测试1的 thread
    history = await get_thread_history(app, thread_id)
    print(f"  共 {len(history)} 个 Checkpoint 快照:")
    for i, h in enumerate(history):
        msgs = h["state"].get("messages", [])
        print(f"  [{i+1}] checkpoint_id={h['checkpoint_id'][:16]}... messages={len(msgs)} next={h['next']}")
    print("  [PASS] 历史查询测试通过")


async def main():
    print(f"SQLite 路径: {CHECKPOINT_DB_PATH}")
    await init_checkpointer()
    try:
        await test_conversation()
        await test_hitl()
        await test_history_api()
        print("\n[ALL PASS] 所有测试通过！可用 DB Browser for SQLite 打开 lg_checkpoints.db 查看数据")
    finally:
        await shutdown_checkpointer()


if __name__ == "__main__":
    asyncio.run(main())

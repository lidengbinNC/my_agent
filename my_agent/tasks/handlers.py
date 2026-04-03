"""任务处理器 — 每种 TaskType 对应一个 handler。

面试考点:
  - 策略模式：不同任务类型注册不同 handler，运行时动态分派
  - 进度上报：通过 queue.update_progress 实时更新，WebSocket 推送给前端
  - 解耦设计：handler 只依赖 TaskRecord 和 TaskQueue，不依赖 HTTP 层
"""

from __future__ import annotations

from my_agent.evaluation.dataset import get_benchmark_dataset
from my_agent.evaluation.models import AgentTypeLabel, TaskDifficulty
from my_agent.evaluation.runner import EvalRunner
from my_agent.domain.customer_service import (
    build_customer_service_message,
    resolve_skill_name,
)
from my_agent.domain.agent import get_skill_registry
from my_agent.tasks.models import TaskRecord, TaskType
from my_agent.tasks.queue import TaskQueue
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


async def handle_eval_single(task: TaskRecord, queue: TaskQueue) -> None:
    """处理单任务评估。"""
    from my_agent.core.dependencies import get_react_engine, get_llm_client
    from my_agent.evaluation.judge import LLMJudge
    from my_agent.evaluation.dataset import get_benchmark_dataset

    payload = task.payload
    task_id_to_eval = payload.get("eval_task_id")
    agent_type_str = payload.get("agent_type", "react")

    await queue.update_progress(task, 10, "初始化评估器...")

    tasks = get_benchmark_dataset()
    eval_task = next((t for t in tasks if t.task_id == task_id_to_eval), None)
    if eval_task is None:
        raise ValueError(f"评估任务不存在: {task_id_to_eval}")

    llm = get_llm_client()
    judge = LLMJudge(llm)
    react_engine = get_react_engine()
    runner = EvalRunner(react_engine, judge)

    await queue.update_progress(task, 30, f"开始评估任务 {task_id_to_eval}...")

    agent_type = AgentTypeLabel(agent_type_str)
    result = await runner.evaluate_single(eval_task, agent_type)

    await queue.update_progress(task, 90, "LLM-as-Judge 评分完成")

    task.result = result.to_dict()
    logger.info("eval_single_done", task_id=task.task_id, score=result.metrics.judge_score)


async def handle_eval_batch(task: TaskRecord, queue: TaskQueue) -> None:
    """处理批量评估。"""
    from my_agent.core.dependencies import get_react_engine, get_llm_client
    from my_agent.evaluation.judge import LLMJudge

    payload = task.payload
    difficulty_str = payload.get("difficulty")
    category = payload.get("category")
    limit = payload.get("limit", 10)
    agent_type_str = payload.get("agent_type", "react")

    await queue.update_progress(task, 5, "加载数据集...")

    difficulty = TaskDifficulty(difficulty_str) if difficulty_str else None
    tasks = get_benchmark_dataset(difficulty=difficulty, category=category, limit=limit)

    if not tasks:
        raise ValueError("没有符合条件的评估任务")

    await queue.update_progress(task, 10, f"共 {len(tasks)} 个任务，开始评估...")

    llm = get_llm_client()
    judge = LLMJudge(llm)
    react_engine = get_react_engine()
    runner = EvalRunner(react_engine, judge, concurrency=2)

    completed_count = 0

    def progress_cb(done: int, total: int) -> None:
        nonlocal completed_count
        completed_count = done
        pct = 10 + int(done / total * 80)
        import asyncio
        asyncio.create_task(
            queue.update_progress(task, pct, f"已完成 {done}/{total} 个任务")
        )

    agent_type = AgentTypeLabel(agent_type_str)
    report = await runner.evaluate_batch(tasks, agent_type, progress_cb)

    await queue.update_progress(task, 95, "生成评估报告...")
    task.result = report.to_dict()


async def handle_eval_compare(task: TaskRecord, queue: TaskQueue) -> None:
    """处理对比评估（ReAct vs Plan-and-Execute）。"""
    from my_agent.core.dependencies import get_react_engine, get_llm_client
    from my_agent.evaluation.judge import LLMJudge

    payload = task.payload
    difficulty_str = payload.get("difficulty", "easy")
    limit = payload.get("limit", 5)

    await queue.update_progress(task, 5, "加载数据集...")

    difficulty = TaskDifficulty(difficulty_str)
    tasks = get_benchmark_dataset(difficulty=difficulty, limit=limit)

    if not tasks:
        raise ValueError("没有符合条件的评估任务")

    await queue.update_progress(task, 10, f"共 {len(tasks)} 个任务，并行运行 ReAct + PlanExec...")

    llm = get_llm_client()
    judge = LLMJudge(llm)
    react_engine = get_react_engine()
    runner = EvalRunner(react_engine, judge, concurrency=2)

    comparison = await runner.compare(tasks)

    await queue.update_progress(task, 95, "生成对比报告...")
    task.result = {
        "react": comparison["react"].to_dict(),
        "plan_execute": comparison["plan_execute"].to_dict(),
        "comparison": comparison["comparison"],
    }


async def handle_agent_chat(task: TaskRecord, queue: TaskQueue) -> None:
    """处理长时间 Agent 对话任务（异步执行，支持轮询结果）。"""
    from my_agent.core.dependencies import get_react_engine
    from my_agent.core.engine.react_engine import ReActStepType

    payload = task.payload
    question = payload.get("question", "")
    if not question:
        raise ValueError("question 不能为空")

    await queue.update_progress(task, 5, "Agent 开始思考...")

    react_engine = get_react_engine()
    steps = []
    final_answer = ""
    iteration = 0

    async for step in react_engine.run(question):
        iteration += 1
        steps.append({
            "type": step.type.value,
            "iteration": iteration,
        })
        if step.type == ReActStepType.THINKING:
                await queue.update_progress(
                    task, min(10 + iteration * 10, 80), f"思考中（第 {iteration} 步）..."
                )
        elif step.type == ReActStepType.FINAL_ANSWER:
            final_answer = step.answer
        elif step.type == ReActStepType.ERROR:
            raise RuntimeError(step.error)

    task.result = {
        "question": question,
        "answer": final_answer,
        "total_iterations": iteration,
        "steps_summary": steps,
    }


async def handle_customer_service_task(task: TaskRecord, queue: TaskQueue) -> None:
    """处理客服 Copilot / 工单草稿异步任务。"""
    from my_agent.core.dependencies import get_react_engine
    from my_agent.core.engine.react_engine import ReActRunControl, ReActStepType

    payload = task.payload
    message = payload.get("message", "")
    mode = payload.get("mode", "copilot")
    allow_write_actions = bool(payload.get("allow_write_actions", False))
    customer_context = payload.get("customer_context", {}) or {}
    if not message:
        raise ValueError("message 不能为空")

    await queue.update_progress(task, 5, "准备客服上下文...")
    prompt = build_customer_service_message(
        message,
        context=customer_context,
        mode=mode,
        allow_write_actions=allow_write_actions,
    )
    skill_name = resolve_skill_name(mode)
    skill = get_skill_registry().get(skill_name) if skill_name else None
    control = ReActRunControl(
        approval_before_tools=allow_write_actions or mode in {"ticket_draft", "complaint_review"},
    )

    await queue.update_progress(task, 15, "开始客服 Copilot 推理...")
    react_engine = get_react_engine()
    final_answer = ""
    paused = None
    steps = []
    async for step in react_engine.run(prompt, skill=skill, control=control):
        steps.append({"type": step.type.value, "iteration": step.iteration})
        if step.type == ReActStepType.THINKING:
            await queue.update_progress(task, min(15 + step.iteration * 10, 85), f"第 {step.iteration} 步推理中...")
        elif step.type == ReActStepType.PAUSED:
            paused = {
                "checkpoint_id": step.checkpoint_id,
                "pause_reason": step.pause_reason,
                "requires_approval": step.requires_approval,
                "action": step.action,
                "action_input": step.action_input,
                "answer_preview": step.answer,
                "data": step.data,
            }
            break
        elif step.type == ReActStepType.FINAL_ANSWER:
            final_answer = step.answer
        elif step.type == ReActStepType.ERROR:
            raise RuntimeError(step.error)

    task.result = {
        "mode": mode,
        "skill": skill_name,
        "answer": final_answer,
        "paused": paused,
        "steps_summary": steps,
        "customer_context": customer_context,
    }


def register_all_handlers(queue: TaskQueue) -> None:
    """注册所有任务处理器。"""
    queue.register_handler(TaskType.EVAL_SINGLE.value, handle_eval_single)
    queue.register_handler(TaskType.EVAL_BATCH.value, handle_eval_batch)
    queue.register_handler(TaskType.EVAL_COMPARE.value, handle_eval_compare)
    queue.register_handler(TaskType.AGENT_CHAT.value, handle_agent_chat)
    queue.register_handler(TaskType.CUSTOMER_SERVICE_COPILOT.value, handle_customer_service_task)
    queue.register_handler(TaskType.AFTER_SALES_TICKET_DRAFT.value, handle_customer_service_task)
    logger.info("task_handlers_registered")

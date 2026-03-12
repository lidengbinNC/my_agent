"""LangGraph 版 Plan-and-Execute Agent — 与自研 plan_execute_engine.py 的对比实现。

面试考点:
  LangGraph Plan-and-Execute 架构:
    1. Planner Graph: 接收用户目标 → LLM 生成步骤列表
    2. Executor Graph: 逐步执行（每步调用 ReAct 子图）
    3. Replanner: 若步骤失败，LLM 重新规划剩余步骤
    4. 两个 Graph 通过共享 State 通信（Subgraph 模式）

  与自研对比:
    ┌──────────────────┬─────────────────────────────┬────────────────────────────────┐
    │ 维度             │ 自研 plan_execute_engine.py  │ LangGraph plan_execute.py      │
    ├──────────────────┼─────────────────────────────┼────────────────────────────────┤
    │ 代码量           │ ~200 行（引擎）+ 辅助类      │ ~180 行（本文件含注释）         │
    │ 规划器           │ Planner 类 + Pydantic 解析   │ with_structured_output() 直接  │
    │ 子图嵌套         │ 手动调用 ReActEngine         │ 编译后的 react_graph 作为节点  │
    │ 状态传递         │ 手动传 context dict          │ 共享 State TypedDict 自动传递  │
    │ 重规划           │ Replanner 类                 │ replanner_node 函数节点        │
    └──────────────────┴─────────────────────────────┴────────────────────────────────┘

  LangGraph 新概念:
    - with_structured_output(): 自动解析 LLM 输出为 Pydantic 模型
    - Subgraph: 将已编译的 Graph 作为另一个 Graph 的节点
    - State 共享: 父图和子图通过 State 字段传递数据
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from my_agent.config.settings import settings
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


# ── 1. 结构化输出模型 ─────────────────────────────────────────────
# 面试考点: with_structured_output() 利用 Function Calling 强制 LLM 输出 JSON
# 对比自研: 自研需要手动解析 JSON + 重试，LangGraph 封装了这个过程

class PlanStep(BaseModel):
    """单个执行步骤。"""
    step_id: int = Field(description="步骤编号，从1开始")
    description: str = Field(description="步骤描述，说明要做什么")
    tool_hint: str = Field(default="", description="可能用到的工具名称（可选）")


class ExecutionPlan(BaseModel):
    """执行计划。"""
    goal: str = Field(description="用户目标")
    steps: list[PlanStep] = Field(description="执行步骤列表，最多6步")
    reasoning: str = Field(default="", description="规划思路")


class ReplanDecision(BaseModel):
    """重规划决策。"""
    need_replan: bool = Field(description="是否需要重规划")
    remaining_steps: list[PlanStep] = Field(
        default_factory=list,
        description="调整后的剩余步骤（need_replan=True 时有效）",
    )
    reason: str = Field(default="", description="重规划原因")


# ── 2. 图状态定义 ─────────────────────────────────────────────────

class PlanExecState(TypedDict):
    """Plan-and-Execute 图的共享状态。"""
    # 输入
    goal: str

    # 规划阶段
    plan: ExecutionPlan | None
    current_step_idx: int

    # 执行阶段
    step_results: list[dict]       # 每步的执行结果
    current_step_result: str

    # 重规划
    replan_count: int

    # 输出
    final_answer: str
    error: str


# ── 3. 节点函数 ───────────────────────────────────────────────────

def _make_llm(model: str | None = None) -> ChatOpenAI:
    cfg = settings.default_llm
    return ChatOpenAI(
        model=model or cfg.model,
        temperature=0.1,
        openai_api_key=cfg.api_key,
        openai_api_base=cfg.base_url,
    )


async def planner_node(state: PlanExecState) -> dict:
    """规划节点：将用户目标分解为执行步骤。

    面试考点: with_structured_output() 强制 LLM 输出符合 ExecutionPlan 的 JSON
    """
    llm = _make_llm()
    planner = llm.with_structured_output(ExecutionPlan)

    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "你是一个任务规划专家。将用户目标分解为清晰的执行步骤（最多6步）。\n"
            "每步应该是具体可执行的，并说明可能用到的工具。"
        )),
        ("human", "目标：{goal}"),
    ])

    chain = prompt | planner
    try:
        plan: ExecutionPlan = await chain.ainvoke({"goal": state["goal"]})
        logger.info("lg_plan_created", steps=len(plan.steps), goal=state["goal"][:50])
        return {"plan": plan, "current_step_idx": 0, "replan_count": 0}
    except Exception as e:
        logger.error("lg_plan_failed", error=str(e))
        return {"error": f"规划失败: {e}"}


async def executor_node(state: PlanExecState) -> dict:
    """执行节点：执行当前步骤（调用 ReAct 子图）。

    面试考点: 将已编译的 react_graph 作为子图调用（Subgraph 嵌套模式）
    """
    from langgraph_impl.react_agent import compile_react_graph

    plan = state.get("plan")
    if not plan:
        return {"error": "没有执行计划"}

    idx = state.get("current_step_idx", 0)
    if idx >= len(plan.steps):
        return {}

    step = plan.steps[idx]
    step_results = list(state.get("step_results", []))

    # 构建步骤上下文
    context = ""
    if step_results:
        context = "\n".join(
            f"步骤{r['step_id']}结果: {r['result'][:200]}"
            for r in step_results[-3:]
        )

    step_question = step.description
    if context:
        step_question = f"已完成的步骤:\n{context}\n\n当前任务: {step.description}"

    logger.info("lg_step_executing", step_id=step.step_id, desc=step.description[:50])

    try:
        react_app = compile_react_graph()
        messages = [
            SystemMessage(content="你是一个智能助手，使用工具完成具体任务。请用中文回答。"),
            HumanMessage(content=step_question),
        ]
        result = await react_app.ainvoke({"messages": messages})
        last_msg = result["messages"][-1]
        step_result = getattr(last_msg, "content", str(last_msg))

        step_results.append({
            "step_id": step.step_id,
            "description": step.description,
            "result": step_result,
            "success": True,
        })
        logger.info("lg_step_done", step_id=step.step_id)
        return {
            "step_results": step_results,
            "current_step_result": step_result,
            "current_step_idx": idx + 1,
        }
    except Exception as e:
        step_results.append({
            "step_id": step.step_id,
            "description": step.description,
            "result": f"执行失败: {e}",
            "success": False,
        })
        return {
            "step_results": step_results,
            "current_step_result": f"步骤执行失败: {e}",
            "current_step_idx": idx + 1,
        }


async def replanner_node(state: PlanExecState) -> dict:
    """重规划节点：检查是否需要调整剩余步骤。

    面试考点: 动态重规划是 Plan-and-Execute 的核心优势
    """
    plan = state.get("plan")
    step_results = state.get("step_results", [])
    replan_count = state.get("replan_count", 0)

    if replan_count >= 2 or not plan:
        return {}

    # 检查最近一步是否失败
    if not step_results or step_results[-1].get("success", True):
        return {}

    llm = _make_llm()
    replanner = llm.with_structured_output(ReplanDecision)

    completed = "\n".join(
        f"步骤{r['step_id']}({'成功' if r['success'] else '失败'}): {r['result'][:100]}"
        for r in step_results
    )
    idx = state.get("current_step_idx", 0)
    remaining = plan.steps[idx:]
    remaining_desc = "\n".join(f"步骤{s.step_id}: {s.description}" for s in remaining)

    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个任务重规划专家。根据已完成步骤的结果，判断是否需要调整剩余步骤。"),
        ("human", (
            "目标: {goal}\n\n"
            "已完成步骤:\n{completed}\n\n"
            "原定剩余步骤:\n{remaining}\n\n"
            "请判断是否需要重规划剩余步骤。"
        )),
    ])

    try:
        chain = prompt | replanner
        decision: ReplanDecision = await chain.ainvoke({
            "goal": state["goal"],
            "completed": completed,
            "remaining": remaining_desc,
        })

        if decision.need_replan and decision.remaining_steps:
            new_plan = ExecutionPlan(
                goal=plan.goal,
                steps=plan.steps[:idx] + decision.remaining_steps,
                reasoning=f"第{replan_count + 1}次重规划: {decision.reason}",
            )
            logger.info("lg_replanned", reason=decision.reason[:50])
            return {"plan": new_plan, "replan_count": replan_count + 1}
    except Exception as e:
        logger.warning("lg_replan_failed", error=str(e))

    return {}


async def synthesizer_node(state: PlanExecState) -> dict:
    """汇总节点：将所有步骤结果合成最终答案。"""
    llm = _make_llm()
    step_results = state.get("step_results", [])

    if not step_results:
        return {"final_answer": "未能获取执行结果"}

    steps_summary = "\n".join(
        f"步骤{r['step_id']}: {r['description']}\n结果: {r['result'][:300]}"
        for r in step_results
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", "你是一个结果汇总专家。根据各步骤的执行结果，给出完整、简洁的最终答案。"),
        ("human", "用户目标: {goal}\n\n各步骤执行结果:\n{steps_summary}\n\n请给出最终答案:"),
    ])

    try:
        chain = prompt | llm
        response = await chain.ainvoke({
            "goal": state["goal"],
            "steps_summary": steps_summary,
        })
        return {"final_answer": response.content}
    except Exception as e:
        return {"final_answer": f"汇总失败: {e}"}


# ── 4. 路由函数 ───────────────────────────────────────────────────

def route_after_executor(state: PlanExecState) -> str:
    """执行完一步后的路由：继续执行下一步 / 重规划 / 汇总。

    面试考点: 条件边实现动态路由，是 LangGraph 的核心能力
    """
    if state.get("error"):
        return "synthesizer"

    plan = state.get("plan")
    if not plan:
        return "synthesizer"

    idx = state.get("current_step_idx", 0)
    if idx >= len(plan.steps):
        return "synthesizer"

    # 检查是否需要重规划
    step_results = state.get("step_results", [])
    replan_count = state.get("replan_count", 0)
    if step_results and not step_results[-1].get("success", True) and replan_count < 2:
        return "replanner"

    return "executor"


# ── 5. 构建 Plan-and-Execute Graph ────────────────────────────────

def build_plan_execute_graph() -> StateGraph:
    """构建 Plan-and-Execute 图。

    图结构:
      START → planner → executor → (route?) → replanner → executor → ...
                                            ↘ synthesizer → END
    """
    graph = StateGraph(PlanExecState)

    graph.add_node("planner", planner_node)
    graph.add_node("executor", executor_node)
    graph.add_node("replanner", replanner_node)
    graph.add_node("synthesizer", synthesizer_node)

    graph.add_edge(START, "planner")
    graph.add_edge("planner", "executor")
    graph.add_conditional_edges(
        "executor",
        route_after_executor,
        {
            "executor": "executor",
            "replanner": "replanner",
            "synthesizer": "synthesizer",
        },
    )
    graph.add_edge("replanner", "executor")
    graph.add_edge("synthesizer", END)

    return graph


def compile_plan_execute_graph(**kwargs) -> Any:
    return build_plan_execute_graph().compile(**kwargs)


async def run_plan_execute_agent(goal: str) -> dict[str, Any]:
    """运行 LangGraph Plan-and-Execute Agent。"""
    app = compile_plan_execute_graph()
    initial_state: PlanExecState = {
        "goal": goal,
        "plan": None,
        "current_step_idx": 0,
        "step_results": [],
        "current_step_result": "",
        "replan_count": 0,
        "final_answer": "",
        "error": "",
    }
    result = await app.ainvoke(initial_state)
    return {
        "goal": goal,
        "final_answer": result.get("final_answer", ""),
        "total_steps": len(result.get("step_results", [])),
        "replan_count": result.get("replan_count", 0),
        "step_results": result.get("step_results", []),
    }


if __name__ == "__main__":
    async def _demo():
        print("=== LangGraph Plan-and-Execute Demo ===")
        result = await run_plan_execute_agent(
            "用 Python 实现快速排序，分析其时间复杂度，并与冒泡排序对比"
        )
        print(f"Final Answer: {result['final_answer']}")
        print(f"Total Steps: {result['total_steps']}")

    asyncio.run(_demo())

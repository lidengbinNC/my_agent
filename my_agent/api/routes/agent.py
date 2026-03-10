"""Agent 管理 API — 创建 / 列表 / 删除 / 运行 Agent。

面试考点:
  - 多 Agent 管理: 同一服务中可注册多个不同类型的 Agent
  - 运行接口统一: ReAct 和 PlanExec 通过 SSE 流式推送，前端无需关心内部差异
  - 工厂模式在 API 层的应用: AgentFactory 按需创建引擎实例
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from my_agent.api.schemas.agent import (
    AgentCreateRequest,
    AgentInfo,
    AgentRunRequest,
    AgentRunResponse,
    PlanStepInfo,
)
from my_agent.api.schemas.chat import SSEEvent, SSEEventType
from my_agent.core.agent_factory import AgentFactory
from my_agent.core.dependencies import get_agent_factory
from my_agent.domain.agent.base import AgentConfig, AgentType
from my_agent.core.engine.plan_execute_engine import PlanExecEventType

router = APIRouter(prefix="/agents", tags=["agents"])

# 内存注册表：agent_id -> AgentConfig（生产环境应持久化到数据库）
_agent_registry: dict[str, AgentConfig] = {}

# 服务启动时预创建的默认 Plan-and-Execute Agent ID
_default_plan_agent_id: str | None = None


def init_default_agent() -> str:
    """服务启动时调用，预创建一个默认的 Plan-and-Execute Agent。

    Returns:
        预创建 Agent 的 ID（全局唯一）
    """
    global _default_plan_agent_id
    agent_id = str(uuid.uuid4())
    _agent_registry[agent_id] = AgentConfig(
        agent_type=AgentType.PLAN_AND_EXECUTE,
        name="深度思考 Agent",
        description="服务启动时预创建，用于复杂多步骤任务的规划与执行",
        max_iterations=10,
        tool_timeout=30.0,
        max_plan_steps=5,
        enable_replanning=True,
    )
    _default_plan_agent_id = agent_id
    return agent_id


def _get_agent(agent_id: str) -> AgentConfig:
    cfg = _agent_registry.get(agent_id)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' 不存在")
    return cfg


# ── CRUD ──────────────────────────────────────────────────────────

@router.post("", response_model=AgentInfo, status_code=201)
async def create_agent(body: AgentCreateRequest) -> AgentInfo:
    """创建并注册一个新 Agent。"""
    agent_id = str(uuid.uuid4())
    cfg = AgentConfig(
        agent_type=body.agent_type,
        name=body.name,
        description=body.description,
        max_iterations=body.max_iterations,
        tool_timeout=body.tool_timeout,
        max_plan_steps=body.max_plan_steps,
        enable_replanning=body.enable_replanning,
    )
    _agent_registry[agent_id] = cfg
    return AgentInfo(
        id=agent_id,
        name=cfg.name,
        agent_type=cfg.agent_type,
        description=cfg.description,
        max_iterations=cfg.max_iterations,
        max_plan_steps=cfg.max_plan_steps,
        enable_replanning=cfg.enable_replanning,
    )


@router.get("", response_model=list[AgentInfo])
async def list_agents() -> list[AgentInfo]:
    """列出所有已注册的 Agent。"""
    return [
        AgentInfo(
            id=aid,
            name=cfg.name,
            agent_type=cfg.agent_type,
            description=cfg.description,
            max_iterations=cfg.max_iterations,
            max_plan_steps=cfg.max_plan_steps,
            enable_replanning=cfg.enable_replanning,
        )
        for aid, cfg in _agent_registry.items()
    ]


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    """删除 Agent。"""
    _get_agent(agent_id)
    del _agent_registry[agent_id]


@router.get("/default", response_model=AgentInfo)
async def get_default_agent() -> AgentInfo:
    """返回服务启动时预创建的默认 Plan-and-Execute Agent 信息。

    前端通过此接口获取 agent_id，用于深度思考模式。
    """
    if _default_plan_agent_id is None:
        raise HTTPException(status_code=503, detail="默认 Agent 尚未初始化，请稍后重试")
    cfg = _get_agent(_default_plan_agent_id)
    return AgentInfo(
        id=_default_plan_agent_id,
        name=cfg.name,
        agent_type=cfg.agent_type,
        description=cfg.description,
        max_iterations=cfg.max_iterations,
        max_plan_steps=cfg.max_plan_steps,
        enable_replanning=cfg.enable_replanning,
    )


# ── 运行 ──────────────────────────────────────────────────────────
#TODO 这里开启了 推理模式，会非常慢，不知道有没有优化的方案，这里可以后期思考下
@router.post("/{agent_id}/run")
async def run_agent(
    agent_id: str,
    body: AgentRunRequest,
    factory: AgentFactory = Depends(get_agent_factory),
):
    """运行指定 Agent（支持 SSE 流式输出）。"""
    cfg = _get_agent(agent_id)
    engine = factory.create(cfg)

    if body.stream:
        return StreamingResponse(
            _stream_agent(agent_id, cfg, engine, body.goal),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 非流式
    answer = ""
    plan_steps = None

    if cfg.agent_type == AgentType.REACT:
        from my_agent.core.engine.react_engine import ReActStepType
        async for step in engine.run(body.goal):  # type: ignore[union-attr]
            if step.type == ReActStepType.FINAL_ANSWER:
                answer = step.answer
            elif step.type == ReActStepType.ERROR:
                answer = f"[错误] {step.error}"
    else:
        async for evt in engine.run(body.goal):  # type: ignore[union-attr]
            if evt.type == PlanExecEventType.DONE:
                answer = evt.message
                if evt.plan:
                    plan_steps = [
                        PlanStepInfo(
                            step_id=s.step_id,
                            description=s.description,
                            tool_hint=s.tool_hint,
                            status=s.status,
                            result=s.result,
                        )
                        for s in evt.plan.steps
                    ]

    return AgentRunResponse(
        agent_id=agent_id,
        agent_type=cfg.agent_type,
        answer=answer,
        plan=plan_steps,
    )


async def _stream_agent(
    agent_id: str,
    cfg: AgentConfig,
    engine,
    goal: str,
) -> AsyncGenerator[str, None]:
    """将 Agent 事件转换为 SSE 流。"""
    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={"agent_id": agent_id, "agent_type": cfg.agent_type, "message": "Agent 启动..."},
    ).to_sse()

    try:
        if cfg.agent_type == AgentType.REACT:
            from my_agent.core.engine.react_engine import ReActStepType
            async for step in engine.run(goal):
                if step.type == ReActStepType.THINKING:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"iteration": step.iteration, "message": f"第 {step.iteration} 步：思考中..."},
                    ).to_sse()
                elif step.type == ReActStepType.ACTION:
                    yield SSEEvent(
                        event=SSEEventType.TOOL_CALL,
                        data={"tool": step.action, "args": step.action_input, "thought": step.thought},
                    ).to_sse()
                elif step.type == ReActStepType.OBSERVATION:
                    yield SSEEvent(
                        event=SSEEventType.TOOL_RESULT,
                        data={"tool": step.action, "result": step.observation},
                    ).to_sse()
                elif step.type == ReActStepType.FINAL_ANSWER:
                    yield SSEEvent(
                        event=SSEEventType.CONTENT,
                        data={"delta": step.answer},
                    ).to_sse()
                    yield SSEEvent(
                        event=SSEEventType.DONE,
                        data={"answer": step.answer},
                    ).to_sse()
                    return
                elif step.type == ReActStepType.ERROR:
                    yield SSEEvent(event=SSEEventType.ERROR, data={"error": step.error}).to_sse()
                    return

        else:  # PLAN_AND_EXECUTE
            async for evt in engine.run(goal):
                if evt.type == PlanExecEventType.PLANNING:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"message": evt.message},
                    ).to_sse()
                elif evt.type == PlanExecEventType.PLAN_READY:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={
                            "message": "计划已生成",
                            "plan": [
                                {"step_id": s.step_id, "description": s.description}
                                for s in (evt.plan.steps if evt.plan else [])
                            ],
                        },
                    ).to_sse()
                elif evt.type == PlanExecEventType.STEP_START:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"message": evt.message, "step_id": evt.step_id},
                    ).to_sse()
                elif evt.type == PlanExecEventType.STEP_DONE:
                    yield SSEEvent(
                        event=SSEEventType.TOOL_RESULT,
                        data={"step_id": evt.step_id, "desc": evt.step_desc, "result": evt.result},
                    ).to_sse()
                elif evt.type == PlanExecEventType.STEP_FAILED:
                    yield SSEEvent(
                        event=SSEEventType.TOOL_RESULT,
                        data={"step_id": evt.step_id, "desc": evt.step_desc, "error": evt.message},
                    ).to_sse()
                elif evt.type == PlanExecEventType.REPLANNING:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"message": evt.message},
                    ).to_sse()
                elif evt.type == PlanExecEventType.SYNTHESIZING:
                    yield SSEEvent(
                        event=SSEEventType.THINKING,
                        data={"message": evt.message},
                    ).to_sse()
                elif evt.type == PlanExecEventType.DONE:
                    answer = evt.message
                    chunk_size = 15
                    for i in range(0, len(answer), chunk_size):
                        yield SSEEvent(
                            event=SSEEventType.CONTENT,
                            data={"delta": answer[i: i + chunk_size]},
                        ).to_sse()
                    yield SSEEvent(
                        event=SSEEventType.DONE,
                        data={"answer": answer},
                    ).to_sse()
                    return
                elif evt.type == PlanExecEventType.ERROR:
                    yield SSEEvent(event=SSEEventType.ERROR, data={"error": evt.message}).to_sse()
                    return

    except Exception as e:
        yield SSEEvent(event=SSEEventType.ERROR, data={"error": str(e)}).to_sse()

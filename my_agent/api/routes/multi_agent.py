"""多 Agent 协作 API — 顺序/并行/层级协作，支持预置场景和自定义。

端点:
  POST /api/v1/multi-agent/run   运行多 Agent 协作（SSE 流式输出）
  GET  /api/v1/multi-agent/scenarios  列出可用预置场景
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from my_agent.api.schemas.chat import SSEEvent, SSEEventType
from my_agent.api.schemas.multi_agent import (
    AgentEventInfo,
    MultiAgentRunRequest,
    MultiAgentRunResponse,
)
from my_agent.core.agent_factory import AgentFactory
from my_agent.core.dependencies import get_agent_factory
from my_agent.core.multi_agent.base import CoordinatorEventType
from my_agent.core.multi_agent.scenarios import (
    create_data_analysis_pipeline,
    create_research_report_pipeline,
)
from my_agent.domain.agent.base import AgentConfig, AgentType
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentRole

router = APIRouter(prefix="/multi-agent", tags=["multi-agent"])

_PRESET_SCENARIOS = {
    "research_report": {
        "name": "研究报告生成",
        "description": "Researcher → Writer → Reviewer，顺序协作生成高质量研究报告",
        "mode": "sequential",
        "agents": ["researcher", "writer", "reviewer"],
    },
    "data_analysis": {
        "name": "数据分析",
        "description": "Manager 协调 DataAgent + AnalystAgent + ReporterAgent，层级协作生成数据报告",
        "mode": "hierarchical",
        "agents": ["manager", "data_agent", "analyst_agent", "reporter_agent"],
    },
}


@router.get("/scenarios")
async def list_scenarios() -> dict:
    """列出所有预置多 Agent 场景。"""
    return {"scenarios": _PRESET_SCENARIOS}


@router.post("/run")
async def run_multi_agent(
    req: MultiAgentRunRequest,
    factory: AgentFactory = Depends(get_agent_factory),
):
    """运行多 Agent 协作任务（支持 SSE 流式输出）。"""
    coordinator = _build_coordinator(req, factory)

    if req.stream:
        return StreamingResponse(
            _stream_coordinator(coordinator, req),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 非流式
    events: list[AgentEventInfo] = []
    final_answer = ""
    async for evt in coordinator.run(req.goal):
        events.append(AgentEventInfo(
            event_type=evt.type.value,
            agent_name=evt.agent_name,
            message=evt.message,
            result=evt.result,
        ))
        if evt.type == CoordinatorEventType.DONE:
            final_answer = evt.message

    return MultiAgentRunResponse(
        scenario=req.scenario,
        mode=req.mode,
        final_answer=final_answer,
        agent_events=events,
    )


def _build_coordinator(req: MultiAgentRunRequest, factory: AgentFactory):
    """根据请求构建对应的协调器。"""
    llm = factory._llm
    registry = factory._registry

    if req.scenario == "research_report":
        return create_research_report_pipeline(llm, registry)

    if req.scenario == "data_analysis":
        return create_data_analysis_pipeline(llm, registry)

    # custom 模式：从请求体构建 AgentSpec 列表
    from my_agent.core.multi_agent.sequential import SequentialCoordinator
    from my_agent.core.multi_agent.parallel import ParallelCoordinator
    from my_agent.core.multi_agent.hierarchical import HierarchicalCoordinator

    specs: list[AgentSpec] = []
    for a in req.agents:
        specs.append(AgentSpec(
            name=a.get("name", "agent"),
            role=AgentRole(a.get("role", "worker")),
            system_prompt=a.get("system_prompt", ""),
            description=a.get("description", ""),
            max_iterations=int(a.get("max_iterations", 6)),
        ))

    if not specs:
        # 兜底：两个通用 Worker
        specs = [
            AgentSpec(name="agent_a", role=AgentRole.WORKER, description="Worker A"),
            AgentSpec(name="agent_b", role=AgentRole.WORKER, description="Worker B"),
        ]

    mode = req.mode.lower()
    if mode == "parallel":
        return ParallelCoordinator(llm=llm, tool_registry=registry, agents=specs)
    if mode == "hierarchical":
        return HierarchicalCoordinator(llm=llm, tool_registry=registry, agents=specs)
    return SequentialCoordinator(llm=llm, tool_registry=registry, agents=specs)


async def _stream_coordinator(coordinator, req: MultiAgentRunRequest) -> AsyncGenerator[str, None]:
    """将 CoordinatorEvent 转换为 SSE 流。"""
    # 颜色映射（面试点：不同 Agent 用不同颜色区分）
    agent_colors = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899"]
    agent_color_map: dict[str, str] = {}
    color_idx = 0

    yield SSEEvent(
        event=SSEEventType.THINKING,
        data={"scenario": req.scenario, "mode": req.mode, "message": "多 Agent 协作启动..."},
    ).to_sse()

    try:
        async for evt in coordinator.run(req.goal):
            # 分配颜色
            if evt.agent_name and evt.agent_name not in agent_color_map:
                agent_color_map[evt.agent_name] = agent_colors[color_idx % len(agent_colors)]
                color_idx += 1

            color = agent_color_map.get(evt.agent_name, "#6B7280")

            if evt.type == CoordinatorEventType.AGENT_START:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={
                        "agent": evt.agent_name,
                        "color": color,
                        "message": evt.message,
                    },
                ).to_sse()

            elif evt.type in (CoordinatorEventType.AGENT_DONE, CoordinatorEventType.MESSAGE):
                yield SSEEvent(
                    event=SSEEventType.TOOL_RESULT,
                    data={
                        "agent": evt.agent_name,
                        "color": color,
                        "result": evt.result,
                        "message": evt.message,
                    },
                ).to_sse()

            elif evt.type == CoordinatorEventType.AGENT_ERROR:
                yield SSEEvent(
                    event=SSEEventType.ERROR,
                    data={"agent": evt.agent_name, "error": evt.message},
                ).to_sse()

            elif evt.type == CoordinatorEventType.SYNTHESIZING:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={"message": evt.message},
                ).to_sse()

            elif evt.type == CoordinatorEventType.DONE:
                answer = evt.message
                chunk_size = 15
                for i in range(0, len(answer), chunk_size):
                    yield SSEEvent(
                        event=SSEEventType.CONTENT,
                        data={"delta": answer[i: i + chunk_size]},
                    ).to_sse()
                yield SSEEvent(
                    event=SSEEventType.DONE,
                    data={
                        "answer": answer,
                        "agent_colors": agent_color_map,
                        **evt.data,
                    },
                ).to_sse()
                return

            elif evt.type == CoordinatorEventType.ERROR:
                yield SSEEvent(event=SSEEventType.ERROR, data={"error": evt.message}).to_sse()
                return

    except Exception as e:
        yield SSEEvent(event=SSEEventType.ERROR, data={"error": str(e)}).to_sse()

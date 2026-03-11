"""工作流 API — CRUD + 执行 + Human-in-the-Loop 审批。

端点:
  POST   /workflows                      创建工作流定义
  GET    /workflows                      列出所有工作流
  GET    /workflows/{id}                 获取工作流详情
  DELETE /workflows/{id}                 删除工作流
  POST   /workflows/{id}/run             执行工作流（SSE 流式）
  GET    /workflows/{id}/runs            列出该工作流的运行记录
  GET    /workflows/runs/{run_id}        获取运行详情（断点恢复用）
  POST   /workflows/human/{token}/approve  审批通过
  POST   /workflows/human/{token}/reject   审批拒绝
  GET    /workflows/human/pending         查看待审批列表
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from my_agent.api.schemas.chat import SSEEvent, SSEEventType
from my_agent.api.schemas.workflow import (
    EdgeDefResponse,
    HumanApprovalRequest,
    NodeDefRequest,
    NodeDefResponse,
    NodeRunInfo,
    WorkflowCreateRequest,
    WorkflowDetail,
    WorkflowInfo,
    WorkflowRunInfo,
    WorkflowRunRequest,
)
from my_agent.core.dependencies import get_agent_factory
from my_agent.core.workflow.engine import WorkflowEngine, WorkflowEventType
from my_agent.core.workflow.node_executors import HumanNodeExecutor, NodeExecutorRegistry
from my_agent.core.workflow.store import get_workflow_store
from my_agent.domain.workflow.models import (
    EdgeDef,
    NodeDef,
    WorkflowDef,
    WorkflowRun,
)

router = APIRouter(prefix="/workflows", tags=["workflows"])


def _build_engine(factory) -> WorkflowEngine:
    from my_agent.core.engine.react_engine import ReActEngine
    from my_agent.domain.tool.executor import ToolExecutor
    import my_agent.domain.tool.builtin  # noqa: F401

    llm = factory._llm
    registry = factory._registry

    react_engine = ReActEngine(llm=llm, tool_registry=registry, max_iterations=6)
    tool_exec = ToolExecutor(registry=registry)
    exec_registry = NodeExecutorRegistry(
        react_engine=react_engine,
        tool_executor=tool_exec,
        llm=llm,
    )
    return WorkflowEngine(exec_registry)


# ── 工作流 CRUD ───────────────────────────────────────────────────

@router.post("", response_model=WorkflowInfo, status_code=201)
async def create_workflow(body: WorkflowCreateRequest) -> WorkflowInfo:
    """创建并保存工作流定义。"""
    nodes = [
        NodeDef(
            node_id=n.node_id,
            name=n.name,
            node_type=n.node_type,
            config=n.config,
            description=n.description,
            position=n.position,
        )
        for n in body.nodes
    ]
    edges = [
        EdgeDef(
            edge_id=str(uuid.uuid4())[:8],
            source=e.source,
            target=e.target,
            condition=e.condition,
            condition_expr=e.condition_expr,
            label=e.label,
        )
        for e in body.edges
    ]
    wd = WorkflowDef(
        workflow_id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        nodes=nodes,
        edges=edges,
    )
    # DAG 校验
    from my_agent.core.workflow.dag import CyclicDependencyError, DAGSorter
    try:
        DAGSorter(wd).validate()
    except CyclicDependencyError as e:
        raise HTTPException(status_code=422, detail=str(e))

    get_workflow_store().save_def(wd)
    return WorkflowInfo(
        workflow_id=wd.workflow_id,
        name=wd.name,
        description=wd.description,
        node_count=len(wd.nodes),
        edge_count=len(wd.edges),
    )


@router.get("", response_model=list[WorkflowInfo])
async def list_workflows() -> list[WorkflowInfo]:
    defs = get_workflow_store().list_defs()
    return [
        WorkflowInfo(
            workflow_id=d.workflow_id,
            name=d.name,
            description=d.description,
            node_count=len(d.nodes),
            edge_count=len(d.edges),
        )
        for d in defs
    ]


@router.get("/{workflow_id}/detail", response_model=WorkflowDetail)
async def get_workflow_detail(workflow_id: str) -> WorkflowDetail:
    """获取工作流完整定义（含节点、边、position），用于前端画布恢复。"""
    wd = get_workflow_store().get_def(workflow_id)
    if not wd:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return WorkflowDetail(
        workflow_id=wd.workflow_id,
        name=wd.name,
        description=wd.description,
        nodes=[
            NodeDefResponse(
                node_id=n.node_id,
                name=n.name,
                node_type=n.node_type.value,
                config=n.config,
                description=n.description,
                position=n.position,
            )
            for n in wd.nodes
        ],
        edges=[
            EdgeDefResponse(
                edge_id=e.edge_id,
                source=e.source,
                target=e.target,
                condition=e.condition.value,
                condition_expr=e.condition_expr,
                label=e.label,
            )
            for e in wd.edges
        ],
    )


@router.get("/{workflow_id}", response_model=WorkflowInfo)
async def get_workflow(workflow_id: str) -> WorkflowInfo:
    wd = get_workflow_store().get_def(workflow_id)
    if not wd:
        raise HTTPException(status_code=404, detail="工作流不存在")
    return WorkflowInfo(
        workflow_id=wd.workflow_id,
        name=wd.name,
        description=wd.description,
        node_count=len(wd.nodes),
        edge_count=len(wd.edges),
    )


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str) -> None:
    if not get_workflow_store().delete_def(workflow_id):
        raise HTTPException(status_code=404, detail="工作流不存在")


# ── 运行 ──────────────────────────────────────────────────────────

@router.post("/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    body: WorkflowRunRequest,
    factory=Depends(get_agent_factory),
):
    """执行工作流（支持断点恢复和 SSE 流式）。"""
    store = get_workflow_store()
    wd = store.get_def(workflow_id)
    if not wd:
        raise HTTPException(status_code=404, detail="工作流不存在")

    # 断点恢复
    if body.resume_run_id:
        wr = store.get_run(body.resume_run_id)
        if not wr:
            raise HTTPException(status_code=404, detail="运行记录不存在")
    else:
        wr = WorkflowRun(workflow_id=workflow_id, goal=body.goal)
        store.save_run(wr)

    engine = _build_engine(factory)

    if body.stream:
        return StreamingResponse(
            _stream_workflow(engine, wd, wr, store),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 非流式
    async for _ in _stream_workflow(engine, wd, wr, store):
        pass
    return _run_to_info(wr)


@router.get("/{workflow_id}/runs", response_model=list[WorkflowRunInfo])
async def list_runs(workflow_id: str) -> list[WorkflowRunInfo]:
    runs = get_workflow_store().list_runs(workflow_id=workflow_id)
    return [_run_to_info(r) for r in runs]


@router.get("/runs/{run_id}", response_model=WorkflowRunInfo)
async def get_run(run_id: str) -> WorkflowRunInfo:
    wr = get_workflow_store().get_run(run_id)
    if not wr:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return _run_to_info(wr)


# ── Human-in-the-Loop ────────────────────────────────────────────

@router.get("/human/pending")
async def list_pending_approvals() -> dict:
    """列出所有待人工审批的 token。"""
    return {"tokens": HumanNodeExecutor.pending_tokens()}


@router.post("/human/{token}/approve", status_code=200)
async def approve_human_node(token: str, body: HumanApprovalRequest) -> dict:
    """审批通过指定 HumanNode。"""
    ok = HumanNodeExecutor.approve(token, comment=body.comment)
    if not ok:
        raise HTTPException(status_code=404, detail="token 不存在或已过期")
    return {"status": "approved", "token": token}


@router.post("/human/{token}/reject", status_code=200)
async def reject_human_node(token: str, body: HumanApprovalRequest) -> dict:
    """拒绝指定 HumanNode。"""
    ok = HumanNodeExecutor.reject(token, comment=body.comment)
    if not ok:
        raise HTTPException(status_code=404, detail="token 不存在或已过期")
    return {"status": "rejected", "token": token}


# ── 内部辅助 ─────────────────────────────────────────────────────

async def _stream_workflow(engine, wd, wr, store) -> AsyncGenerator[str, None]:
    """将工作流事件转换为 SSE 流，并实时持久化。"""
    try:
        async for evt in engine.run(wd, wr):
            store.save_run(wr)  # 每步持久化（支持断点恢复）

            if evt.type == WorkflowEventType.STARTED:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={"message": evt.message, "run_id": wr.run_id},
                ).to_sse()

            elif evt.type == WorkflowEventType.NODE_START:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={
                        "node_id": evt.node_id,
                        "node_name": evt.node_name,
                        "node_type": evt.node_type,
                        "message": evt.message,
                    },
                ).to_sse()

            elif evt.type == WorkflowEventType.NODE_DONE:
                yield SSEEvent(
                    event=SSEEventType.TOOL_RESULT,
                    data={
                        "node_id": evt.node_id,
                        "node_name": evt.node_name,
                        "output": evt.output[:300],
                        "message": evt.message,
                        "human_token": evt.human_token,
                    },
                ).to_sse()

            elif evt.type == WorkflowEventType.NODE_WAITING:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={
                        "node_id": evt.node_id,
                        "node_name": evt.node_name,
                        "message": evt.message,
                        "human_token": evt.human_token,
                    },
                ).to_sse()

            elif evt.type in (WorkflowEventType.NODE_FAILED, WorkflowEventType.ERROR):
                yield SSEEvent(
                    event=SSEEventType.ERROR,
                    data={"node_id": evt.node_id, "error": evt.message},
                ).to_sse()

            elif evt.type == WorkflowEventType.NODE_SKIPPED:
                yield SSEEvent(
                    event=SSEEventType.THINKING,
                    data={"message": evt.message},
                ).to_sse()

            elif evt.type == WorkflowEventType.WORKFLOW_DONE:
                # 推送最终输出
                outputs = evt.data.get("outputs", {})
                final_text = "\n\n".join(
                    f"**{nid}**: {out}" for nid, out in outputs.items() if out
                )
                chunk_size = 20
                for i in range(0, len(final_text), chunk_size):
                    yield SSEEvent(
                        event=SSEEventType.CONTENT,
                        data={"delta": final_text[i: i + chunk_size]},
                    ).to_sse()
                yield SSEEvent(
                    event=SSEEventType.DONE,
                    data={"run_id": wr.run_id, "message": evt.message, **evt.data},
                ).to_sse()
                return

            elif evt.type == WorkflowEventType.WORKFLOW_FAILED:
                yield SSEEvent(
                    event=SSEEventType.ERROR,
                    data={"error": evt.message, "run_id": wr.run_id},
                ).to_sse()
                return

    except Exception as e:
        store.save_run(wr)
        yield SSEEvent(event=SSEEventType.ERROR, data={"error": str(e)}).to_sse()


def _run_to_info(wr: WorkflowRun) -> WorkflowRunInfo:
    return WorkflowRunInfo(
        run_id=wr.run_id,
        workflow_id=wr.workflow_id,
        status=wr.status.value,
        goal=wr.goal,
        node_runs={
            nid: NodeRunInfo(
                node_id=nr.node_id,
                status=nr.status.value,
                output=nr.output[:200],
                error=nr.error,
                human_token=nr.human_token,
            )
            for nid, nr in wr.node_runs.items()
        },
    )

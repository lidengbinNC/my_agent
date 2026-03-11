"""WorkflowEngine — DAG 并行调度引擎。

面试考点:
  - DAG 并行调度: 每轮找出入度为 0 的可执行节点，asyncio.gather 并行执行
  - 条件边路由: ConditionNode 输出 true/false，决定哪些后继节点被激活
  - 断点恢复: 运行前检查 WorkflowRun，跳过已完成节点（status=SUCCESS）
  - 事件流: yield WorkflowEvent，驱动 SSE 实时推送执行进度
  - 错误隔离: 单节点失败只标记该节点，不影响无依赖的并行节点
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, AsyncGenerator

from my_agent.core.workflow.dag import CyclicDependencyError, DAGSorter
from my_agent.core.workflow.node_executors import HumanNodeExecutor, NodeExecutorRegistry
from my_agent.domain.workflow.models import (
    EdgeCondition,
    NodeDef,
    NodeRun,
    NodeStatus,
    NodeType,
    WorkflowDef,
    WorkflowRun,
)
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class WorkflowEventType(str, Enum):
    STARTED = "workflow_started"
    NODE_START = "node_start"
    NODE_DONE = "node_done"
    NODE_FAILED = "node_failed"
    NODE_WAITING = "node_waiting"       # HumanNode 等待审批
    NODE_SKIPPED = "node_skipped"
    WORKFLOW_DONE = "workflow_done"
    WORKFLOW_FAILED = "workflow_failed"
    ERROR = "error"


@dataclass
class WorkflowEvent:
    """WorkflowEngine 推送的结构化事件，驱动 SSE 流。"""

    type: WorkflowEventType
    node_id: str = ""
    node_name: str = ""
    node_type: str = ""
    message: str = ""
    output: str = ""
    human_token: str = ""               # HumanNode 审批 token
    data: dict[str, Any] = field(default_factory=dict)


class WorkflowEngine:
    """DAG 工作流执行引擎。

    特性:
      - 并行调度: 同一层无依赖的节点 asyncio.gather 并行
      - 条件路由: ConditionNode 输出决定后继节点是否执行
      - 断点恢复: 已成功节点自动跳过
      - Human-in-the-Loop: HumanNode 暂停等待审批
    """

    def __init__(self, executor_registry: NodeExecutorRegistry) -> None:
        self._registry = executor_registry

    async def run(
        self,
        workflow: WorkflowDef,
        workflow_run: WorkflowRun,
    ) -> AsyncGenerator[WorkflowEvent, None]:
        """执行工作流 DAG，逐步 yield WorkflowEvent。

        支持断点恢复：传入已有 WorkflowRun（含部分完成的 node_runs）即可续跑。
        """
        start_time = time.monotonic()

        # 拓扑校验
        try:
            sorter = DAGSorter(workflow)
            sorter.validate()
        except CyclicDependencyError as e:
            yield WorkflowEvent(type=WorkflowEventType.ERROR, message=str(e))
            return

        workflow_run.status = NodeStatus.RUNNING
        node_map = workflow.node_map()
        all_node_ids = set(node_map.keys())

        yield WorkflowEvent(
            type=WorkflowEventType.STARTED,
            message=f"工作流 '{workflow.name}' 启动，共 {len(all_node_ids)} 个节点",
        )

        # 跳过节点集合（被路由排除的节点）
        skipped: set[str] = set()
        # 已完成节点（SUCCESS / APPROVED / FAILED 均视为已处理）
        completed: set[str] = set(
            nid for nid, nr in workflow_run.node_runs.items()
            if nr.status in (NodeStatus.SUCCESS, NodeStatus.APPROVED, NodeStatus.FAILED)
        )

        # 主调度循环
        while True:
            # 找出当前可执行节点：前驱全部完成且自身未执行且未跳过
            ready = [
                nid for nid in sorter.get_ready_nodes(completed | skipped, all_node_ids)
                if nid not in completed and nid not in skipped
            ]

            if not ready:
                break  # 无节点可执行，结束

            # 并行执行本轮所有就绪节点
            tasks = [
                self._execute_node(node_map[nid], workflow_run)
                for nid in ready
                if nid in node_map
            ]

            events_lists = await asyncio.gather(*tasks, return_exceptions=True)

            # 收集事件并路由条件边
            for nid, events_or_exc in zip(ready, events_lists):
                if isinstance(events_or_exc, Exception):
                    node_run = workflow_run.get_node_run(nid)
                    node_run.status = NodeStatus.FAILED
                    node_run.error = str(events_or_exc)
                    completed.add(nid)
                    yield WorkflowEvent(
                        type=WorkflowEventType.NODE_FAILED,
                        node_id=nid,
                        node_name=node_map[nid].name,
                        message=str(events_or_exc),
                    )
                    # 标记以 on_failure=False 的后继为跳过
                    for edge, successor in workflow.successors(nid):
                        if edge.condition != EdgeCondition.ON_FAILURE:
                            skipped.add(successor.node_id)
                else:
                    for evt in events_or_exc:
                        yield evt

                    node_run = workflow_run.get_node_run(nid)
                    completed.add(nid)

                    # ConditionNode：根据输出路由后继
                    node_def = node_map[nid]
                    if node_def.node_type == NodeType.CONDITION:
                        condition_result = node_run.output.strip().lower()
                        is_true = condition_result == "true" or "true" in condition_result
                        for edge, successor in workflow.successors(nid):
                            if edge.label in ("true", "是", "yes"):
                                if not is_true:
                                    skipped.add(successor.node_id)
                            elif edge.label in ("false", "否", "no"):
                                if is_true:
                                    skipped.add(successor.node_id)

                    # HumanNode 被拒绝：终止工作流
                    if node_run.status == NodeStatus.REJECTED:
                        workflow_run.status = NodeStatus.REJECTED
                        yield WorkflowEvent(
                            type=WorkflowEventType.WORKFLOW_FAILED,
                            message=f"HumanNode '{node_map[nid].name}' 审批拒绝，工作流终止",
                        )
                        return

        # 判断整体是否成功
        failed = [nid for nid in all_node_ids if workflow_run.get_node_run(nid).status == NodeStatus.FAILED]
        elapsed = time.monotonic() - start_time
        workflow_run.updated_at = datetime.utcnow()

        if failed:
            workflow_run.status = NodeStatus.FAILED
            yield WorkflowEvent(
                type=WorkflowEventType.WORKFLOW_FAILED,
                message=f"工作流完成，但有 {len(failed)} 个节点失败: {failed}",
                data={"elapsed": elapsed},
            )
        else:
            workflow_run.status = NodeStatus.SUCCESS
            # 收集所有节点输出
            outputs = {
                nid: workflow_run.get_node_run(nid).output
                for nid in all_node_ids
                if nid not in skipped
            }
            yield WorkflowEvent(
                type=WorkflowEventType.WORKFLOW_DONE,
                message=f"工作流 '{workflow.name}' 成功完成（{elapsed:.1f}s）",
                data={"outputs": outputs, "elapsed": elapsed},
            )

    async def _execute_node(
        self,
        node: NodeDef,
        workflow_run: WorkflowRun,
    ) -> list[WorkflowEvent]:
        """执行单个节点，返回产生的事件列表（供 gather 收集）。"""
        events: list[WorkflowEvent] = []
        node_run = workflow_run.get_node_run(node.node_id)

        # 断点恢复：已成功则跳过
        if node_run.status == NodeStatus.SUCCESS:
            events.append(WorkflowEvent(
                type=WorkflowEventType.NODE_SKIPPED,
                node_id=node.node_id,
                node_name=node.name,
                message=f"[跳过] {node.name}（断点恢复：已完成）",
            ))
            return events

        # START / END 节点直接通过
        if node.node_type in (NodeType.START, NodeType.END):
            node_run.status = NodeStatus.SUCCESS
            return events

        executor = self._registry.get(node.node_type)
        if executor is None:
            node_run.status = NodeStatus.FAILED
            node_run.error = f"未知节点类型: {node.node_type}"
            events.append(WorkflowEvent(
                type=WorkflowEventType.NODE_FAILED,
                node_id=node.node_id,
                node_name=node.name,
                message=node_run.error,
            ))
            return events

        node_run.status = NodeStatus.RUNNING
        node_run.started_at = datetime.utcnow()

        # HumanNode：先推送 WAITING 事件
        if node.node_type == NodeType.HUMAN:
            events.append(WorkflowEvent(
                type=WorkflowEventType.NODE_START,
                node_id=node.node_id,
                node_name=node.name,
                node_type=node.node_type.value,
                message=f"[{node.name}] 等待人工审批...",
            ))

        else:
            events.append(WorkflowEvent(
                type=WorkflowEventType.NODE_START,
                node_id=node.node_id,
                node_name=node.name,
                node_type=node.node_type.value,
                message=f"[{node.name}] 开始执行",
            ))

        try:
            output = await executor.execute(node, node_run, workflow_run)
            node_run.output = output
            node_run.finished_at = datetime.utcnow()
            if node_run.status not in (NodeStatus.APPROVED, NodeStatus.REJECTED):
                node_run.status = NodeStatus.SUCCESS

            # 将输出写入共享 context（供后续节点使用）
            workflow_run.context[node.node_id] = output

            if node.node_type == NodeType.HUMAN:
                events.append(WorkflowEvent(
                    type=WorkflowEventType.NODE_DONE,
                    node_id=node.node_id,
                    node_name=node.name,
                    output=output,
                    human_token=node_run.human_token,
                    message=f"[{node.name}] 审批通过",
                ))
            else:
                events.append(WorkflowEvent(
                    type=WorkflowEventType.NODE_DONE,
                    node_id=node.node_id,
                    node_name=node.name,
                    output=output,
                    message=f"[{node.name}] 完成",
                ))

        except PermissionError as e:
            node_run.finished_at = datetime.utcnow()
            events.append(WorkflowEvent(
                type=WorkflowEventType.NODE_FAILED,
                node_id=node.node_id,
                node_name=node.name,
                message=str(e),
            ))
            raise

        except Exception as e:
            node_run.status = NodeStatus.FAILED
            node_run.error = str(e)
            node_run.finished_at = datetime.utcnow()
            events.append(WorkflowEvent(
                type=WorkflowEventType.NODE_FAILED,
                node_id=node.node_id,
                node_name=node.name,
                message=f"[{node.name}] 失败: {e}",
            ))
            raise

        return events

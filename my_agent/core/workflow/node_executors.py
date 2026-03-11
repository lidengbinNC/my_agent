"""四种节点执行器 — 每种节点类型对应独立执行逻辑。

面试考点:
  - 策略模式（Strategy）：BaseNodeExecutor 定义统一接口 execute()，
    各子类封装不同执行策略，WorkflowEngine 无需 if/else 分支
  - AgentNodeExecutor：调用 ReActEngine，将 context 注入历史
  - ToolNodeExecutor：直接调用 ToolExecutor，无需 LLM 推理
  - ConditionNodeExecutor：基于前节点输出做简单文本匹配或 LLM 判断，
    返回 "true"/"false" 路由下游
  - HumanNodeExecutor：写入 human_token，yield WAITING 事件，
    engine 持有 asyncio.Event，审批 API 触发 event.set()
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from my_agent.domain.workflow.models import NodeDef, NodeRun, NodeStatus, NodeType, WorkflowRun
from my_agent.utils.logger import get_logger

if TYPE_CHECKING:
    from my_agent.domain.llm.base import BaseLLMClient
    from my_agent.domain.tool.executor import ToolExecutor
    from my_agent.domain.tool.registry import ToolRegistry
    from my_agent.core.engine.react_engine import ReActEngine

logger = get_logger(__name__)


class BaseNodeExecutor(ABC):
    """节点执行器抽象基类（策略模式）。"""

    @abstractmethod
    async def execute(
        self,
        node: NodeDef,
        node_run: NodeRun,
        workflow_run: WorkflowRun,
    ) -> str:
        """执行节点，返回输出字符串（写入 node_run.output）。"""


class AgentNodeExecutor(BaseNodeExecutor):
    """AgentNode 执行器：调用 ReActEngine 完成智能任务。"""

    def __init__(self, react_engine: "ReActEngine") -> None:
        self._engine = react_engine

    async def execute(self, node: NodeDef, node_run: NodeRun, workflow_run: WorkflowRun) -> str:
        from my_agent.core.engine.react_engine import ReActStepType
        from my_agent.domain.llm.message import UserMessage

        task = node.config.get("prompt", "") or node.name
        # 将工作流上下文注入为历史
        ctx = workflow_run.context
        context_str = "\n".join(
            f"{k}: {v}" for k, v in ctx.items()
            if isinstance(v, str) and k != node.node_id
        )
        history = []
        if context_str:
            history.append(UserMessage(f"[工作流上下文]\n{context_str}"))

        answer = f"[Agent {node.name} 未返回答案]"
        try:
            async for step in self._engine.run(task, history=history):
                if step.type == ReActStepType.FINAL_ANSWER:
                    answer = step.answer
                elif step.type == ReActStepType.ERROR:
                    raise RuntimeError(step.error)
        except Exception as e:
            raise

        return answer


class ToolNodeExecutor(BaseNodeExecutor):
    """ToolNode 执行器：直接调用单个工具，无需 LLM 推理。"""

    def __init__(self, tool_executor: "ToolExecutor") -> None:
        self._executor = tool_executor

    async def execute(self, node: NodeDef, node_run: NodeRun, workflow_run: WorkflowRun) -> str:
        tool_name = node.config.get("tool_name", "")
        tool_args = node.config.get("tool_args", {})

        # 支持从 context 动态填充参数（模板变量替换）
        resolved_args: dict[str, Any] = {}
        ctx = workflow_run.context
        for k, v in tool_args.items():
            if isinstance(v, str) and v.startswith("{{") and v.endswith("}}"):
                var_name = v[2:-2].strip()
                resolved_args[k] = ctx.get(var_name, v)
            else:
                resolved_args[k] = v

        if not tool_name:
            raise ValueError(f"ToolNode '{node.name}' 未配置 tool_name")

        result = await self._executor.execute(tool_name, resolved_args)
        return result.to_observation()


class ConditionNodeExecutor(BaseNodeExecutor):
    """ConditionNode 执行器：基于前节点输出决定路由方向。

    输出 "true" 或 "false"，由 WorkflowEngine 决定走哪条边。
    判断方式（优先级）：
      1. contains_any：检查输出是否包含关键词
      2. LLM 判断（condition_prompt）
      3. 默认返回 "true"
    """

    def __init__(self, llm: "BaseLLMClient") -> None:
        self._llm = llm

    async def execute(self, node: NodeDef, node_run: NodeRun, workflow_run: WorkflowRun) -> str:
        from my_agent.domain.llm.message import SystemMessage, UserMessage

        config = node.config
        # 获取上一节点的输出（通过 input_from 指定）
        input_from = config.get("input_from", "")
        prev_output = workflow_run.context.get(input_from, "")

        # 方式 1: 关键词检查
        contains_any = config.get("contains_any", [])
        if contains_any:
            if any(kw in prev_output for kw in contains_any):
                return "true"
            return "false"

        # 方式 2: LLM 判断
        condition_prompt = config.get("condition_prompt", "")
        if condition_prompt:
            prompt = (
                f"根据以下内容判断条件是否满足，只回答 true 或 false。\n"
                f"条件: {condition_prompt}\n"
                f"内容: {prev_output[:500]}"
            )
            try:
                resp = await self._llm.chat(
                    [SystemMessage("你是一个条件判断器，只输出 true 或 false。"), UserMessage(prompt)],
                    temperature=0.0,
                )
                answer = (resp.content or "").strip().lower()
                return "true" if "true" in answer else "false"
            except Exception:
                return "true"  # 判断失败时走默认路径

        return "true"


class HumanNodeExecutor(BaseNodeExecutor):
    """HumanNode 执行器：暂停工作流，等待人工审批。

    面试考点:
      - asyncio.Event 实现协程级暂停：engine 挂起在 event.wait()，
        审批 API 调用 event.set() 解除阻塞
      - human_token：URL-safe token，发给审批人，审批时携带
      - timeout_seconds：超时后自动拒绝（防止工作流永久挂起）
    """

    # 全局等待表：token -> asyncio.Event + result
    _pending: dict[str, asyncio.Event] = {}
    _results: dict[str, dict] = {}

    async def execute(self, node: NodeDef, node_run: NodeRun, workflow_run: WorkflowRun) -> str:
        config = node.config
        timeout = float(config.get("timeout_seconds", 3600))

        # 生成唯一审批 token
        token = str(uuid.uuid4())
        node_run.human_token = token
        node_run.status = NodeStatus.WAITING

        event = asyncio.Event()
        self.__class__._pending[token] = event
        self.__class__._results[token] = {}

        logger.info(
            "human_node_waiting",
            node=node.name,
            token=token,
            run_id=workflow_run.run_id,
            prompt=config.get("prompt", "请审批"),
        )

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.__class__._pending.pop(token, None)
            self.__class__._results.pop(token, None)
            raise TimeoutError(f"HumanNode '{node.name}' 审批超时（{timeout}s）")

        result = self.__class__._results.pop(token, {})
        approved = result.get("approved", False)
        comment = result.get("comment", "")
        self.__class__._pending.pop(token, None)

        if approved:
            node_run.status = NodeStatus.APPROVED
            return f"approved: {comment}" if comment else "approved"
        else:
            node_run.status = NodeStatus.REJECTED
            raise PermissionError(f"HumanNode '{node.name}' 审批拒绝: {comment}")

    @classmethod
    def approve(cls, token: str, comment: str = "") -> bool:
        """外部调用：审批通过。"""
        if token not in cls._pending:
            return False
        cls._results[token] = {"approved": True, "comment": comment}
        cls._pending[token].set()
        return True

    @classmethod
    def reject(cls, token: str, comment: str = "") -> bool:
        """外部调用：审批拒绝。"""
        if token not in cls._pending:
            return False
        cls._results[token] = {"approved": False, "comment": comment}
        cls._pending[token].set()
        return True

    @classmethod
    def pending_tokens(cls) -> list[str]:
        """返回所有等待审批的 token 列表。"""
        return list(cls._pending.keys())


class NodeExecutorRegistry:
    """节点执行器注册表，按 NodeType 分发到对应执行器。"""

    def __init__(
        self,
        react_engine: "ReActEngine",
        tool_executor: "ToolExecutor",
        llm: "BaseLLMClient",
    ) -> None:
        self._executors: dict[NodeType, BaseNodeExecutor] = {
            NodeType.AGENT: AgentNodeExecutor(react_engine),
            NodeType.TOOL: ToolNodeExecutor(tool_executor),
            NodeType.CONDITION: ConditionNodeExecutor(llm),
            NodeType.HUMAN: HumanNodeExecutor(),
        }

    def get(self, node_type: NodeType) -> BaseNodeExecutor | None:
        return self._executors.get(node_type)

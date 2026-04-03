"""ReAct 推理引擎 — Thought → Action → Observation 循环。

面试考点:
  - ReAct 论文核心: Reasoning（思考）+ Acting（行动）交替进行
  - 停止条件: max_iterations / final_answer / 超时 / 成本上限
  - LLM 输出解析: 原生 Tool Calling 优先 + JSON/文本兜底
  - AsyncGenerator: 逐步 yield ReActStep，驱动 SSE 实时推送
  - 工具调用闭环: LLM 决策 → 工具执行 → Observation 注入 → 继续推理
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from my_agent.config.settings import settings
from my_agent.domain.agent.skill import AgentSkill
from my_agent.domain.guardrails.chain import GuardChain, build_default_output_chain
from my_agent.domain.llm.base import BaseLLMClient, LLMResponse
from my_agent.domain.llm.message import (
    Message,
    UserMessage,
)
from my_agent.domain.prompt.react_prompt import _register_react_prompts  # 确保注册
from my_agent.domain.prompt.registry import get_prompt_registry
from my_agent.domain.tool.base import BaseTool
from my_agent.domain.tool.executor import ToolExecutor
from my_agent.domain.tool.registry import ToolRegistry
from my_agent.utils.logger import get_logger
from my_agent.utils.token_counter import (
    ContextBudget,
    build_context_budget,
    count_messages_tokens,
)

logger = get_logger(__name__)

_register_react_prompts()  # 保证 Prompt 已注册


class ReActStepType(str, Enum):
    THINKING = "thinking"         # Agent 正在思考
    ACTION = "action"             # Agent 决定调用工具
    OBSERVATION = "observation"   # 工具返回结果
    FINAL_ANSWER = "final_answer" # 最终答案
    PAUSED = "paused"             # 在断点处暂停，等待恢复/审批
    ERROR = "error"               # 发生错误


@dataclass
class ReActStep:
    """ReAct 循环中的单个步骤，用于 SSE 推送。"""

    type: ReActStepType
    thought: str = ""
    action: str = ""
    action_input: dict[str, Any] = field(default_factory=dict)
    observation: str = ""
    answer: str = ""
    error: str = ""
    iteration: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    checkpoint_id: str = ""
    pause_reason: str = ""
    requires_approval: bool = False
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReActRunControl:
    """主链路 ReAct 的暂停/审批控制项。"""

    pause_before_tools: bool = False
    pause_before_answer: bool = False
    approval_before_tools: bool = False
    approval_before_answer: bool = False

    @property
    def tool_gate_enabled(self) -> bool:
        return self.pause_before_tools or self.approval_before_tools

    @property
    def final_gate_enabled(self) -> bool:
        return self.pause_before_answer or self.approval_before_answer


@dataclass
class ReActResult:
    """ReAct 引擎最终结果。"""

    answer: str
    steps: list[ReActStep]
    total_iterations: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    elapsed_seconds: float = 0.0


class ReActEngine:
    """ReAct 推理引擎。

    用法:
        async for step in engine.run(query, messages):
            # 每个 step 可以通过 SSE 推送给前端
    """

    def __init__(
        self,
        llm: BaseLLMClient,
        tool_registry: ToolRegistry,
        max_iterations: int = 5,
        tool_timeout: float = 30.0,
        budget: ContextBudget | None = None,
        observation_guard: GuardChain | None = None,
    ) -> None:
        self._llm = llm
        self._registry = tool_registry
        self._executor = ToolExecutor(tool_registry, timeout=tool_timeout)
        self._max_iterations = max_iterations
        self._prompt_registry = get_prompt_registry()
        self._budget = budget or build_context_budget()
        self._observation_guard = observation_guard
        self._graph_runtime: Any | None = None
        if self._observation_guard is None and settings.guardrails_enabled:
            self._observation_guard = build_default_output_chain()

    async def run(
        self,
        query: str,
        history: list[Message] | None = None,
        skill: AgentSkill | None = None,
        thread_id: str | None = None,
        control: ReActRunControl | None = None,
    ) -> AsyncGenerator[ReActStep, None]:
        """执行 ReAct 循环，逐步 yield ReActStep。"""
        runtime = self._get_graph_runtime()
        async for event in runtime.run(
            query,
            history=history,
            skill=skill,
            thread_id=thread_id,
            control=control,
        ):
            event_type = event["type"]
            if event_type == ReActStepType.THINKING.value:
                yield ReActStep(
                    type=ReActStepType.THINKING,
                    iteration=int(event.get("iteration", 0) or 0),
                )
            elif event_type == ReActStepType.ACTION.value:
                yield ReActStep(
                    type=ReActStepType.ACTION,
                    thought=str(event.get("thought", "") or ""),
                    action=str(event.get("action", "") or ""),
                    action_input=event.get("action_input", {}) or {},
                    iteration=int(event.get("iteration", 0) or 0),
                    prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(event.get("completion_tokens", 0) or 0),
                )
            elif event_type == ReActStepType.OBSERVATION.value:
                yield ReActStep(
                    type=ReActStepType.OBSERVATION,
                    observation=str(event.get("observation", "") or ""),
                    action=str(event.get("action", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                )
            elif event_type == ReActStepType.FINAL_ANSWER.value:
                yield ReActStep(
                    type=ReActStepType.FINAL_ANSWER,
                    thought=str(event.get("thought", "") or ""),
                    answer=str(event.get("answer", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                    prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(event.get("completion_tokens", 0) or 0),
                )
                return
            elif event_type == ReActStepType.PAUSED.value:
                yield ReActStep(
                    type=ReActStepType.PAUSED,
                    thought=str(event.get("thought", "") or ""),
                    action=str(event.get("action", "") or ""),
                    action_input=event.get("action_input", {}) or {},
                    answer=str(event.get("answer_preview", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                    checkpoint_id=str(event.get("checkpoint_id", "") or ""),
                    pause_reason=str(event.get("pause_reason", "") or ""),
                    requires_approval=bool(event.get("requires_approval", False)),
                    data=event.get("data", {}) or {},
                )
                return
            elif event_type == ReActStepType.ERROR.value:
                yield ReActStep(
                    type=ReActStepType.ERROR,
                    error=str(event.get("error", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                )
                return

    async def resume_run(
        self,
        run_id: str,
        *,
        action: str = "resume",
        feedback: str = "",
    ) -> AsyncGenerator[ReActStep, None]:
        runtime = self._get_graph_runtime()
        async for event in runtime.resume_run(run_id, action=action, feedback=feedback):
            event_type = event["type"]
            if event_type == ReActStepType.THINKING.value:
                yield ReActStep(type=ReActStepType.THINKING, iteration=int(event.get("iteration", 0) or 0))
            elif event_type == ReActStepType.ACTION.value:
                yield ReActStep(
                    type=ReActStepType.ACTION,
                    thought=str(event.get("thought", "") or ""),
                    action=str(event.get("action", "") or ""),
                    action_input=event.get("action_input", {}) or {},
                    iteration=int(event.get("iteration", 0) or 0),
                    prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(event.get("completion_tokens", 0) or 0),
                )
            elif event_type == ReActStepType.OBSERVATION.value:
                yield ReActStep(
                    type=ReActStepType.OBSERVATION,
                    observation=str(event.get("observation", "") or ""),
                    action=str(event.get("action", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                )
            elif event_type == ReActStepType.FINAL_ANSWER.value:
                yield ReActStep(
                    type=ReActStepType.FINAL_ANSWER,
                    thought=str(event.get("thought", "") or ""),
                    answer=str(event.get("answer", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                    prompt_tokens=int(event.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(event.get("completion_tokens", 0) or 0),
                )
                return
            elif event_type == ReActStepType.PAUSED.value:
                yield ReActStep(
                    type=ReActStepType.PAUSED,
                    thought=str(event.get("thought", "") or ""),
                    action=str(event.get("action", "") or ""),
                    action_input=event.get("action_input", {}) or {},
                    answer=str(event.get("answer_preview", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                    checkpoint_id=str(event.get("checkpoint_id", "") or ""),
                    pause_reason=str(event.get("pause_reason", "") or ""),
                    requires_approval=bool(event.get("requires_approval", False)),
                    data=event.get("data", {}) or {},
                )
                return
            elif event_type == ReActStepType.ERROR.value:
                yield ReActStep(
                    type=ReActStepType.ERROR,
                    error=str(event.get("error", "") or ""),
                    iteration=int(event.get("iteration", 0) or 0),
                )
                return

    async def get_run_state(self, run_id: str) -> dict[str, Any]:
        runtime = self._get_graph_runtime()
        return await runtime.get_run_state(run_id)

    async def get_run_history(self, run_id: str) -> list[dict[str, Any]]:
        runtime = self._get_graph_runtime()
        return await runtime.get_run_history(run_id)

    # ==================== 内部方法 ====================

    def _get_graph_runtime(self) -> Any:
        if self._graph_runtime is None:
            from my_agent.core.engine.react_graph_runtime import ReActGraphRuntime

            self._graph_runtime = ReActGraphRuntime(self)
        return self._graph_runtime

    def _resolve_available_tools(self, skill: AgentSkill | None) -> list[BaseTool]:
        """根据 Skill 过滤当前轮允许使用的工具。"""
        tools = self._registry.all()
        if not skill or not skill.has_tool_restrictions:
            return tools
        allowed = set(skill.allowed_tools)
        filtered = [tool for tool in tools if tool.name in allowed]
        missing = sorted(allowed - {tool.name for tool in filtered})
        if missing:
            logger.warning("skill_allowed_tools_missing", skill=skill.name, missing=missing)
        return filtered

    def _build_tools_description(self, tools: list[BaseTool]) -> str:
        """构建工具描述文本，注入到 System Prompt。"""
        if not tools:
            return "（当前无可用工具）"
        lines: list[str] = []
        for t in tools:
            params = t.parameters_schema.get("properties", {})
            param_desc = ", ".join(
                f"{k}: {v.get('description', v.get('type', 'any'))}"
                for k, v in params.items()
            )
            lines.append(f"- **{t.name}**: {t.description}\n  参数: {param_desc or '无'}")
        return "\n".join(lines)

    def _extract_response_decision(
        self,
        response: LLMResponse,
        iteration: int,
    ) -> dict[str, Any] | None:
        """从 LLM 响应中提取本轮决策。

        优先读取原生 tool_calls；若没有，再兼容旧版 JSON；最后兜底纯文本 final answer。
        """
        raw_output = (response.content or "").strip()

        if response.tool_calls:
            if len(response.tool_calls) > 1:
                logger.warning(
                    "react_multiple_tool_calls_detected",
                    iteration=iteration,
                    count=len(response.tool_calls),
                )
            tool_call = response.tool_calls[0]
            return {
                "thought": raw_output,
                "action": tool_call.name,
                "action_input": self._safe_parse_action_input(tool_call.arguments),
                "raw_action_input": tool_call.arguments,
                "tool_call_id": tool_call.id,
                "answer": "",
            }

        parsed = self._parse_llm_output(raw_output) if raw_output else None
        if parsed is not None:
            thought = str(parsed.get("thought", "") or "")
            action = str(parsed.get("action", "") or "")
            action_input = parsed.get("action_input", {})
            parsed_input = action_input if isinstance(action_input, dict) else {}
            raw_action_input: str | dict[str, Any]
            if isinstance(action_input, dict):
                raw_action_input = action_input
            else:
                raw_action_input = json.dumps(action_input, ensure_ascii=False)
            answer = ""
            if action == "final_answer":
                answer = (
                    action_input.get("answer", "")
                    if isinstance(action_input, dict)
                    else str(action_input)
                )
            return {
                "thought": thought,
                "action": action,
                "action_input": parsed_input,
                "raw_action_input": raw_action_input,
                "tool_call_id": None,
                "answer": answer,
            }

        if raw_output:
            return {
                "thought": "",
                "action": "final_answer",
                "action_input": {"answer": raw_output},
                "raw_action_input": {"answer": raw_output},
                "tool_call_id": None,
                "answer": raw_output,
            }
        return None

    async def _sanitize_observation(self, observation: str, tool_name: str) -> str:
        """对 Observation 做统一过滤，避免敏感结果直接注入下一轮上下文。"""
        if not self._observation_guard or not observation:
            return observation
        sanitized, result = await self._observation_guard.check(
            observation,
            context={"phase": "observation", "tool": tool_name},
        )
        if result and result.is_blocked:
            return (
                "[FILTERED] 工具输出因安全策略被拦截，未向模型暴露原始结果。"
                f"原因: {result.reason or '未提供'}"
            )
        return sanitized

    async def _summarize_with_current_context(
        self,
        messages: list[Message],
        *,
        iteration: int,
        reason: str,
    ) -> ReActStep:
        """在无法继续迭代时，强制 LLM 基于现有 observation 做最终总结。"""
        summary_prompt = self._prompt_registry.render(
            "react_force_final_answer",
            reason=reason,
        )
        summary_messages = self._build_summary_messages(messages, summary_prompt)
        try:
            response = await self._llm.chat(
                summary_messages,
                temperature=0.1,
                max_tokens=self._budget.output_budget,
            )
        except Exception as e:
            return ReActStep(
                type=ReActStepType.ERROR,
                error=f"{reason} 最终总结失败: {e}",
                iteration=iteration,
            )

        thought, answer = self._extract_final_answer(response.content or "")
        if not answer:
            return ReActStep(
                type=ReActStepType.ERROR,
                error=f"{reason} 最终总结为空，未能生成答案。",
                iteration=iteration,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
            )
        return ReActStep(
            type=ReActStepType.FINAL_ANSWER,
            thought=thought or reason,
            answer=answer,
            iteration=iteration,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
        )

    def _build_summary_messages(self, messages: list[Message], prompt: str) -> list[Message]:
        """构建总结轮输入；若超预算，逐步裁剪最早的非 system 消息。"""
        summary_messages = [*messages, UserMessage(prompt)]
        while len(summary_messages) > 2 and count_messages_tokens(summary_messages) > self._budget.total_input_budget:
            summary_messages.pop(1)
        return summary_messages

    @staticmethod
    def _extract_final_answer(raw: str) -> tuple[str, str]:
        raw = raw.strip()
        if not raw:
            return "", ""
        parsed = ReActEngine._parse_llm_output(raw)
        if parsed and parsed.get("action") == "final_answer":
            thought = str(parsed.get("thought", "") or "")
            action_input = parsed.get("action_input", {})
            answer = (
                action_input.get("answer", "")
                if isinstance(action_input, dict)
                else str(action_input)
            )
            return thought, answer
        return "", raw

    @staticmethod
    def _safe_parse_action_input(arguments: str) -> dict[str, Any]:
        try:
            parsed = json.loads(arguments)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _serialize_tool_arguments(arguments: str | dict[str, Any]) -> str:
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False)
        return arguments

    @staticmethod
    def _parse_llm_output(raw: str) -> dict[str, Any] | None:
        """解析 LLM 输出，支持 JSON 模式和正则兜底。"""
        raw = raw.strip()

        # 方式 1: 直接 JSON 解析
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # 方式 2: 提取 ```json ... ``` 代码块
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 方式 3: 提取第一个 { ... } 对象
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return None
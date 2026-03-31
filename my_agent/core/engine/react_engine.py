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
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator

from my_agent.config.settings import settings
from my_agent.domain.agent.skill import AgentSkill
from my_agent.domain.guardrails.chain import GuardChain, build_default_output_chain
from my_agent.domain.llm.base import BaseLLMClient, LLMResponse
from my_agent.domain.llm.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolMessage,
    ToolCallInfo,
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
    count_tokens,
)

logger = get_logger(__name__)

_register_react_prompts()  # 保证 Prompt 已注册


class ReActStepType(str, Enum):
    THINKING = "thinking"         # Agent 正在思考
    ACTION = "action"             # Agent 决定调用工具
    OBSERVATION = "observation"   # 工具返回结果
    FINAL_ANSWER = "final_answer" # 最终答案
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
        if self._observation_guard is None and settings.guardrails_enabled:
            self._observation_guard = build_default_output_chain()

    async def run(
        self,
        query: str,
        history: list[Message] | None = None,
        skill: AgentSkill | None = None,
    ) -> AsyncGenerator[ReActStep, None]:
        """执行 ReAct 循环，逐步 yield ReActStep。"""
        start_time = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0

        # 构建工具描述
        available_tools = self._resolve_available_tools(skill)
        tools_description = self._build_tools_description(available_tools)
        llm_tools = [tool.to_openai_tool() for tool in available_tools]
        if skill:
            logger.info(
                "react_skill_activated",
                skill=skill.name,
                allowed_tools=[tool.name for tool in available_tools],
            )

        # 构建系统 Prompt
        system_content = self._prompt_registry.render(
            "react_system",
            tools_description=tools_description,
            max_iterations=self._max_iterations,
        )
        if skill and skill.system_instructions:
            system_content = (
                f"{system_content}\n\n"
                f"## 当前激活技能: {skill.name}\n"
                f"{skill.system_instructions}"
            )

        budget = self._budget

        # ── 第1层：System Prompt（含工具描述）──────────────────────────────
        system_tokens = count_tokens(system_content)
        if system_tokens > budget.system_budget:
            logger.warning(
                "system_prompt_over_budget",
                system_tokens=system_tokens,
                system_budget=budget.system_budget,
            )
        messages: list[Message] = [SystemMessage(system_content)]

        # ── 第2层：对话历史（按 history_budget 裁剪）──────────────────────
        if history:
            from my_agent.utils.token_counter import trim_history_to_budget
            trimmed = trim_history_to_budget(history, budget.history_budget)
            dropped = len(history) - len(trimmed)
            if dropped > 0:
                logger.info(
                    "history_trimmed",
                    original=len(history),
                    kept=len(trimmed),
                    dropped=dropped,
                    history_budget=budget.history_budget,
                )
            messages.extend(trimmed)

        # ── 第3层：Few-shot + 用户问题 ────────────────────────────────────
        few_shot = self._prompt_registry.render("react_few_shot")
        if skill and skill.few_shot:
            few_shot = f"{few_shot}\n\n{skill.few_shot}"
        user_turn = f"{few_shot}\n\n用户问题: {query}"
        user_tokens = count_tokens(user_turn)
        if user_tokens > budget.few_shot_budget:
            logger.warning(
                "user_turn_over_budget",
                user_tokens=user_tokens,
                few_shot_budget=budget.few_shot_budget,
            )
        messages.append(UserMessage(user_turn))

        # ── 预算汇总日志 ──────────────────────────────────────────────────
        total_input_tokens = count_messages_tokens(messages)
        logger.info(
            "context_budget_check",
            **budget.summary(),
            total_input_tokens=total_input_tokens,
            remaining=budget.remaining_after(total_input_tokens),
        )

        # 最终兜底：如果固定层本身就超出总预算（配置错误），直接报错
        if total_input_tokens > budget.total_input_budget:
            yield ReActStep(
                type=ReActStepType.ERROR,
                error=(
                    f"上下文超出总输入预算！"
                    f"当前 {total_input_tokens} tokens，"
                    f"总输入预算 {budget.total_input_budget} tokens。"
                    f"请检查 ctx_system_budget / ctx_few_shot_budget 配置。"
                ),
                iteration=0,
            )
            return

        for iteration in range(1, self._max_iterations + 1):
            logger.info("react_iteration", iteration=iteration, query=query[:50])

            # ---- LLM 推理 ----
            yield ReActStep(type=ReActStepType.THINKING, iteration=iteration)

            try:
                response = await self._llm.chat(
                    messages,
                    tools=llm_tools or None,
                    temperature=0.2,  # 低温度提升 JSON 输出稳定性
                )
            except Exception as e:
                yield ReActStep(type=ReActStepType.ERROR, error=str(e), iteration=iteration)
                return

            step_prompt_tokens = response.usage.prompt_tokens
            step_completion_tokens = response.usage.completion_tokens
            prompt_tokens += step_prompt_tokens
            completion_tokens += step_completion_tokens

            raw_output = response.content or ""
            logger.debug("react_llm_output", iteration=iteration, raw=raw_output[:300])

            # ---- 解析 LLM 输出 / Tool Call ----
            decision = self._extract_response_decision(response, iteration)
            if decision is None:
                yield ReActStep(
                    type=ReActStepType.ERROR,
                    error=f"LLM 输出解析失败: {raw_output[:200]}",
                    iteration=iteration,
                )
                return

            thought = decision["thought"]
            action = decision["action"]
            action_input = decision["action_input"]
            raw_action_input = decision["raw_action_input"]
            tool_call_id = decision["tool_call_id"] or f"call_{iteration}"

            # ---- 最终答案 ----
            if action == "final_answer":
                answer = decision["answer"]
                yield ReActStep(
                    type=ReActStepType.FINAL_ANSWER,
                    thought=thought,
                    answer=answer,
                    iteration=iteration,
                    prompt_tokens=step_prompt_tokens,
                    completion_tokens=step_completion_tokens,
                )
                return

            # ---- 工具调用 ----
            yield ReActStep(
                type=ReActStepType.ACTION,
                thought=thought,
                action=action,
                action_input=action_input if isinstance(action_input, dict) else {},
                iteration=iteration,
                prompt_tokens=step_prompt_tokens,
                completion_tokens=step_completion_tokens,
            )

            # 将 Assistant 消息加入历史（含 tool_calls）
            messages.append(AssistantMessage(
                content=response.content,
                tool_calls=[ToolCallInfo(
                    id=tool_call_id,
                    name=action,
                    arguments=self._serialize_tool_arguments(raw_action_input),
                )],
            ))

            # ---- 执行工具 ----
            if skill and skill.has_tool_restrictions and action not in {tool.name for tool in available_tools}:
                observation = (
                    f"[ERROR] 工具 '{action}' 不在当前技能 '{skill.name}' 的允许列表中。"
                    f"允许工具: {[tool.name for tool in available_tools]}"
                )
            else:
                tool_result = await self._executor.execute(
                    action,
                    raw_action_input,
                )
                observation = tool_result.to_observation()
            observation = await self._sanitize_observation(observation, action)

            yield ReActStep(
                type=ReActStepType.OBSERVATION,
                observation=observation,
                action=action,
                iteration=iteration,
            )

            # 将 Observation 注入消息历史
            messages.append(ToolMessage(content=observation, tool_call_id=tool_call_id))

            # ── 迭代内预算检查：每次注入 Observation 后检查剩余空间 ──────
            current_tokens = count_messages_tokens(messages)
            remaining = budget.remaining_after(current_tokens)
            logger.debug(
                "iteration_budget_check",
                iteration=iteration,
                current_tokens=current_tokens,
                remaining=remaining,
                iteration_budget=budget.iteration_budget,
            )
            # 剩余空间不足以支撑下一次迭代，提前结束并要求 LLM 给出当前结论
            if remaining < budget.iteration_budget:
                logger.warning(
                    "budget_exhausted_early_stop",
                    iteration=iteration,
                    current_tokens=current_tokens,
                    remaining=remaining,
                )
                yield await self._summarize_with_current_context(
                    messages,
                    iteration=iteration,
                    reason=(
                        f"上下文预算不足，已在第 {iteration} 次迭代后停止继续调用工具。"
                        f"当前已用 {current_tokens} tokens，"
                        f"剩余 {remaining} tokens 不足以支撑下一轮完整推理。"
                    ),
                )
                return

        # 超出最大迭代次数后，做最后总结
        yield await self._summarize_with_current_context(
            messages,
            iteration=self._max_iterations,
            reason=f"已达到最大迭代次数 {self._max_iterations}，请基于已有 observation 收敛并给出最终答案。",
        )

    # ==================== 内部方法 ====================

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
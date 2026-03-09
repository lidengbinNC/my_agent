"""ReAct 推理引擎 — Thought → Action → Observation 循环。

面试考点:
  - ReAct 论文核心: Reasoning（思考）+ Acting（行动）交替进行
  - 停止条件: max_iterations / final_answer / 超时 / 成本上限
  - LLM 输出解析: JSON 模式 + 正则兜底
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

from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCallInfo,
    UserMessage,
)
from my_agent.domain.prompt.react_prompt import _register_react_prompts  # 确保注册
from my_agent.domain.prompt.registry import get_prompt_registry
from my_agent.domain.tool.executor import ToolExecutor
from my_agent.domain.tool.registry import ToolRegistry
from my_agent.utils.logger import get_logger

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
        max_iterations: int = 10,
        tool_timeout: float = 30.0,
    ) -> None:
        self._llm = llm
        self._registry = tool_registry
        self._executor = ToolExecutor(tool_registry, timeout=tool_timeout)
        self._max_iterations = max_iterations
        self._prompt_registry = get_prompt_registry()

    async def run(
        self,
        query: str,
        history: list[Message] | None = None,
    ) -> AsyncGenerator[ReActStep, None]:
        """执行 ReAct 循环，逐步 yield ReActStep。"""
        start_time = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0

        # 构建工具描述
        tools_description = self._build_tools_description()

        # 构建系统 Prompt
        #TODO:这里需要了解下，是什么时候，构建的系统提示词的流程
        system_content = self._prompt_registry.render(
            "react_system",
            tools_description=tools_description,
            max_iterations=self._max_iterations,
        )

        # 构建消息列表
        messages: list[Message] = [SystemMessage(system_content)]
        if history:
            messages.extend(history)

        # Few-shot 示例 + 用户问题
        few_shot = self._prompt_registry.render("react_few_shot")
        messages.append(UserMessage(f"{few_shot}\n\n用户问题: {query}"))

        for iteration in range(1, self._max_iterations + 1):
            logger.info("react_iteration", iteration=iteration, query=query[:50])

            # ---- LLM 推理 ----
            yield ReActStep(type=ReActStepType.THINKING, iteration=iteration)

            try:
                response = await self._llm.chat(
                    messages,
                    temperature=0.2,  # 低温度提升 JSON 输出稳定性
                    response_format={"type": "json_object"},
                )
            except Exception as e:
                yield ReActStep(type=ReActStepType.ERROR, error=str(e), iteration=iteration)
                return

            prompt_tokens += response.usage.prompt_tokens
            completion_tokens += response.usage.completion_tokens

            raw_output = response.content or ""
            logger.debug("react_llm_output", iteration=iteration, raw=raw_output[:300])

            # ---- 解析 LLM 输出 ----
            parsed = self._parse_llm_output(raw_output)
            if parsed is None:
                yield ReActStep(
                    type=ReActStepType.ERROR,
                    error=f"LLM 输出解析失败: {raw_output[:200]}",
                    iteration=iteration,
                )
                return

            thought = parsed.get("thought", "")
            action = parsed.get("action", "")
            action_input = parsed.get("action_input", {})

            # ---- 最终答案 ----
            if action == "final_answer":
                answer = action_input.get("answer", "") if isinstance(action_input, dict) else str(action_input)
                yield ReActStep(
                    type=ReActStepType.FINAL_ANSWER,
                    thought=thought,
                    answer=answer,
                    iteration=iteration,
                )
                return

            # ---- 工具调用 ----
            yield ReActStep(
                type=ReActStepType.ACTION,
                thought=thought,
                action=action,
                action_input=action_input if isinstance(action_input, dict) else {},
                iteration=iteration,
            )

            # 将 Assistant 消息加入历史（含 tool_calls）
            tool_call_id = f"call_{iteration}"
            messages.append(AssistantMessage(
                content=raw_output,
                tool_calls=[ToolCallInfo(
                    id=tool_call_id,
                    name=action,
                    arguments=json.dumps(action_input, ensure_ascii=False),
                )],
            ))

            # ---- 执行工具 ----
            tool_result = await self._executor.execute(
                action,
                action_input if isinstance(action_input, dict) else {},
            )
            observation = tool_result.to_observation()

            yield ReActStep(
                type=ReActStepType.OBSERVATION,
                observation=observation,
                action=action,
                iteration=iteration,
            )

            # 将 Observation 注入消息历史
            from my_agent.domain.llm.message import ToolMessage
            messages.append(ToolMessage(content=observation, tool_call_id=tool_call_id))

        # 超出最大迭代次数
        yield ReActStep(
            type=ReActStepType.ERROR,
            error=f"已达到最大迭代次数 {self._max_iterations}，任务未完成",
            iteration=self._max_iterations,
        )

    # ==================== 内部方法 ====================

    def _build_tools_description(self) -> str:
        """构建工具描述文本，注入到 System Prompt。"""
        tools = self._registry.all()
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

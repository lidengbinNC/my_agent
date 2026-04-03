"""LangGraph-backed runtime for the self-built ReAct engine."""

from __future__ import annotations

import operator
from typing import TYPE_CHECKING, Annotated, Any, AsyncGenerator

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from my_agent.domain.llm.message import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCallInfo,
    ToolMessage,
    UserMessage,
)
from my_agent.utils.logger import get_logger
from my_agent.utils.token_counter import count_messages_tokens, count_tokens

if TYPE_CHECKING:
    from my_agent.core.engine.react_engine import ReActEngine, ReActRunControl
    from my_agent.domain.agent.skill import AgentSkill

logger = get_logger(__name__)


class ReActGraphState(TypedDict):
    messages: Annotated[list[dict[str, Any]], operator.add]
    allowed_tools: list[str]
    iteration_count: int
    current_iteration: int
    error: str
    final_answer: str
    pending_answer: str
    finalize_reason: str
    last_thought: str
    last_action: str
    last_action_input: dict[str, Any]
    last_raw_action_input: str | dict[str, Any]
    last_observation: str
    last_tool_call_id: str
    prompt_tokens: int
    completion_tokens: int
    last_prompt_tokens: int
    last_completion_tokens: int
    pause_before_tools: bool
    pause_before_finalize: bool
    approval_before_tools: bool
    approval_before_finalize: bool
    tool_gate_decision: str
    final_gate_decision: str
    tool_gate_feedback: str
    final_gate_feedback: str


class ReActGraphRuntime:
    """Execute the existing ReAct loop as a LangGraph state machine."""

    def __init__(self, engine: ReActEngine) -> None:
        self._engine = engine
        self._plain_app: Any | None = None
        self._managed_app: Any | None = None
        self._checkpoint_key: int | None = None

    async def run(
        self,
        query: str,
        history: list[Message] | None = None,
        skill: AgentSkill | None = None,
        thread_id: str | None = None,
        control: ReActRunControl | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        initial_state = await self._build_initial_state(
            query,
            history=history,
            skill=skill,
            control=control,
        )
        if isinstance(initial_state, dict) and initial_state.get("type") == "error":
            yield initial_state
            return

        app, config, managed = self._resolve_app(thread_id)
        async for event in self._stream_segments(
            app,
            config=config,
            initial_input=initial_state,
            managed=managed,
        ):
            yield event

    async def resume_run(
        self,
        run_id: str,
        *,
        action: str = "resume",
        feedback: str = "",
    ) -> AsyncGenerator[dict[str, Any], None]:
        app = self._get_managed_app()
        config = {"configurable": {"thread_id": run_id}}
        state = await app.aget_state(config)
        if not state or not state.values:
            yield {"type": "error", "error": f"run_id={run_id} 不存在", "iteration": 0}
            return

        next_nodes = list(state.next)
        if not next_nodes:
            values = state.values
            if values.get("final_answer"):
                yield {
                    "type": "final_answer",
                    "thought": str(values.get("last_thought", "") or ""),
                    "answer": str(values.get("final_answer", "") or ""),
                    "iteration": int(values.get("iteration_count", 0) or 0),
                }
            else:
                yield {"type": "error", "error": f"run_id={run_id} 已完成，无需 resume", "iteration": 0}
            return

        next_node = next_nodes[0]
        updates = self._build_resume_updates(next_node, action=action, feedback=feedback)
        if updates is None:
            yield {"type": "error", "error": f"当前断点 {next_node} 不支持 resume", "iteration": 0}
            return

        await app.aupdate_state(config=config, values=updates)
        async for event in self._stream_segments(app, config=config, initial_input=None, managed=True):
            yield event

    async def get_run_state(self, run_id: str) -> dict[str, Any]:
        app = self._get_managed_app()
        config = {"configurable": {"thread_id": run_id}}
        state = await app.aget_state(config)
        if not state or not state.values:
            return {"run_id": run_id, "status": "not_found"}

        values = state.values
        next_nodes = list(state.next)
        checkpoint_id = state.config.get("configurable", {}).get("checkpoint_id", "")
        status = self._derive_status(values, next_nodes)
        return {
            "run_id": run_id,
            "status": status,
            "checkpoint_id": checkpoint_id,
            "next_nodes": next_nodes,
            "current_node": next_nodes[0] if next_nodes else "",
            "iteration": int(values.get("iteration_count", 0) or 0),
            "last_thought": str(values.get("last_thought", "") or ""),
            "last_action": str(values.get("last_action", "") or ""),
            "last_action_input": values.get("last_action_input", {}) or {},
            "last_observation": str(values.get("last_observation", "") or ""),
            "pending_answer": str(values.get("pending_answer", "") or ""),
            "final_answer": str(values.get("final_answer", "") or ""),
            "error": str(values.get("error", "") or ""),
            "pause_reason": self._pause_reason(values, next_nodes[0]) if next_nodes else "",
            "requires_approval": self._requires_approval(values, next_nodes[0]) if next_nodes else False,
            "can_resume": bool(next_nodes),
            "can_approve": bool(next_nodes and self._requires_approval(values, next_nodes[0])),
            "can_reject": bool(next_nodes and self._requires_approval(values, next_nodes[0])),
        }

    async def get_run_history(self, run_id: str) -> list[dict[str, Any]]:
        app = self._get_managed_app()
        history: list[dict[str, Any]] = []
        config = {"configurable": {"thread_id": run_id}}
        async for snapshot in app.aget_state_history(config):
            cfg = snapshot.config.get("configurable", {})
            values = snapshot.values
            history.append(
                {
                    "checkpoint_id": cfg.get("checkpoint_id", ""),
                    "checkpoint_ns": cfg.get("checkpoint_ns", ""),
                    "created_at": getattr(snapshot, "created_at", None),
                    "next": list(snapshot.next),
                    "status": self._derive_status(values, list(snapshot.next)),
                    "state": {
                        "iteration_count": int(values.get("iteration_count", 0) or 0),
                        "last_action": str(values.get("last_action", "") or ""),
                        "last_observation": str(values.get("last_observation", "") or ""),
                        "pending_answer": str(values.get("pending_answer", "") or ""),
                        "final_answer": str(values.get("final_answer", "") or ""),
                        "error": str(values.get("error", "") or ""),
                    },
                    "metadata": snapshot.metadata,
                }
            )
        return history

    async def _stream_segments(
        self,
        app: Any,
        *,
        config: dict[str, Any] | None,
        initial_input: ReActGraphState | None,
        managed: bool,
    ) -> AsyncGenerator[dict[str, Any], None]:
        current_input = initial_input
        while True:
            async for event in app.astream(current_input, config=config, stream_mode="updates"):
                for node_name, node_output in event.items():
                    if not isinstance(node_output, dict):
                        continue
                    async for translated in self._translate_event(node_name, node_output):
                        yield translated

            if not managed or config is None:
                return

            state = await app.aget_state(config)
            if not state or not state.values:
                return

            next_nodes = list(state.next)
            if not next_nodes:
                return

            pause_event = self._build_pause_event(state)
            if pause_event is not None:
                yield pause_event
                return

            current_input = None

    async def _translate_event(
        self,
        node_name: str,
        node_output: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any], None]:
        if node_name == "agent":
            yield {
                "type": "thinking",
                "iteration": int(node_output.get("current_iteration", 0) or 0),
            }
            if node_output.get("error"):
                yield {
                    "type": "error",
                    "error": str(node_output["error"]),
                    "iteration": int(node_output.get("current_iteration", 0) or 0),
                }
            elif node_output.get("last_action"):
                yield {
                    "type": "action",
                    "thought": str(node_output.get("last_thought", "") or ""),
                    "action": str(node_output.get("last_action", "") or ""),
                    "action_input": node_output.get("last_action_input", {}) or {},
                    "iteration": int(node_output.get("current_iteration", 0) or 0),
                    "prompt_tokens": int(node_output.get("last_prompt_tokens", 0) or 0),
                    "completion_tokens": int(node_output.get("last_completion_tokens", 0) or 0),
                }
        elif node_name == "tools":
            if node_output.get("error"):
                yield {
                    "type": "error",
                    "error": str(node_output["error"]),
                    "iteration": int(node_output.get("current_iteration", 0) or 0),
                }
            elif node_output.get("last_observation"):
                yield {
                    "type": "observation",
                    "action": str(node_output.get("last_action", "") or ""),
                    "observation": str(node_output.get("last_observation", "") or ""),
                    "iteration": int(node_output.get("current_iteration", 0) or 0),
                }
        elif node_name == "finalize":
            if node_output.get("error"):
                yield {
                    "type": "error",
                    "error": str(node_output["error"]),
                    "iteration": int(node_output.get("iteration_count", 0) or 0),
                }
            elif node_output.get("final_answer"):
                yield {
                    "type": "final_answer",
                    "thought": str(node_output.get("last_thought", "") or ""),
                    "answer": str(node_output["final_answer"]),
                    "iteration": int(node_output.get("iteration_count", 0) or 0),
                    "prompt_tokens": int(node_output.get("last_prompt_tokens", 0) or 0),
                    "completion_tokens": int(node_output.get("last_completion_tokens", 0) or 0),
                }

    async def _build_initial_state(
        self,
        query: str,
        *,
        history: list[Message] | None,
        skill: AgentSkill | None,
        control: ReActRunControl | None,
    ) -> ReActGraphState | dict[str, Any]:
        if control is None:
            from my_agent.core.engine.react_engine import ReActRunControl as _ReActRunControl

            control = _ReActRunControl()
        available_tools = self._engine._resolve_available_tools(skill)
        tools_description = self._engine._build_tools_description(available_tools)
        if skill:
            logger.info(
                "react_skill_activated",
                skill=skill.name,
                allowed_tools=[tool.name for tool in available_tools],
            )

        system_content = self._engine._prompt_registry.render(
            "react_system",
            tools_description=tools_description,
            max_iterations=self._engine._max_iterations,
        )
        if skill and skill.system_instructions:
            system_content = (
                f"{system_content}\n\n"
                f"## 当前激活技能: {skill.name}\n"
                f"{skill.system_instructions}"
            )

        budget = self._engine._budget
        system_tokens = count_tokens(system_content)
        if system_tokens > budget.system_budget:
            logger.warning(
                "system_prompt_over_budget",
                system_tokens=system_tokens,
                system_budget=budget.system_budget,
            )

        messages: list[Message] = [SystemMessage(system_content)]
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

        few_shot = self._engine._prompt_registry.render("react_few_shot")
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

        total_input_tokens = count_messages_tokens(messages)
        logger.info(
            "context_budget_check",
            **budget.summary(),
            total_input_tokens=total_input_tokens,
            remaining=budget.remaining_after(total_input_tokens),
        )
        if total_input_tokens > budget.total_input_budget:
            return {
                "type": "error",
                "error": (
                    f"上下文超出总输入预算！当前 {total_input_tokens} tokens，"
                    f"总输入预算 {budget.total_input_budget} tokens。"
                    "请检查 ctx_system_budget / ctx_few_shot_budget 配置。"
                ),
                "iteration": 0,
            }

        return {
            "messages": [self._serialize_message(message) for message in messages],
            "allowed_tools": [tool.name for tool in available_tools],
            "iteration_count": 0,
            "current_iteration": 0,
            "error": "",
            "final_answer": "",
            "pending_answer": "",
            "finalize_reason": "",
            "last_thought": "",
            "last_action": "",
            "last_action_input": {},
            "last_raw_action_input": {},
            "last_observation": "",
            "last_tool_call_id": "",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "last_prompt_tokens": 0,
            "last_completion_tokens": 0,
            "pause_before_tools": control.pause_before_tools,
            "pause_before_finalize": control.pause_before_answer,
            "approval_before_tools": control.approval_before_tools,
            "approval_before_finalize": control.approval_before_answer,
            "tool_gate_decision": "pending" if control.tool_gate_enabled else "auto",
            "final_gate_decision": "pending" if control.final_gate_enabled else "auto",
            "tool_gate_feedback": "",
            "final_gate_feedback": "",
        }

    def _resolve_app(self, thread_id: str | None) -> tuple[Any, dict[str, Any] | None, bool]:
        if not thread_id:
            if self._plain_app is None:
                self._plain_app = self._build_graph()
            return self._plain_app, None, False

        checkpointer = self._try_get_checkpointer()
        if checkpointer is None:
            if self._plain_app is None:
                self._plain_app = self._build_graph()
            return self._plain_app, None, False

        return self._get_managed_app(checkpointer), {"configurable": {"thread_id": thread_id}}, True

    def _get_managed_app(self, checkpointer: Any | None = None) -> Any:
        checkpointer = checkpointer or self._try_get_checkpointer()
        if checkpointer is None:
            raise RuntimeError("LangGraph checkpointer 未初始化，无法查询或恢复主链路运行状态。")
        checkpointer_key = id(checkpointer)
        if self._managed_app is None or self._checkpoint_key != checkpointer_key:
            self._managed_app = self._build_graph(
                checkpointer=checkpointer,
                interrupt_before=["tools", "finalize"],
            )
            self._checkpoint_key = checkpointer_key
        return self._managed_app

    def _build_graph(
        self,
        *,
        checkpointer: Any | None = None,
        interrupt_before: list[str] | None = None,
    ) -> Any:
        graph = StateGraph(ReActGraphState)
        graph.add_node("agent", self._agent_node)
        graph.add_node("tools", self._tools_node)
        graph.add_node("summarize", self._summarize_node)
        graph.add_node("finalize", self._finalize_node)

        graph.add_edge(START, "agent")
        graph.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "finalize": "finalize", END: END},
        )
        graph.add_conditional_edges(
            "tools",
            self._route_after_tools,
            {"agent": "agent", "summarize": "summarize", END: END},
        )
        graph.add_edge("summarize", "finalize")
        graph.add_edge("finalize", END)
        return graph.compile(checkpointer=checkpointer, interrupt_before=interrupt_before or [])

    async def _agent_node(self, state: ReActGraphState) -> dict[str, Any]:
        iteration = int(state.get("iteration_count", 0) or 0) + 1
        messages = self._deserialize_messages(state.get("messages", []))
        available_tools = self._resolve_allowed_tools(state.get("allowed_tools", []))
        llm_tools = [tool.to_openai_tool() for tool in available_tools]

        logger.info("react_iteration", iteration=iteration)
        try:
            response = await self._engine._llm.chat(
                messages,
                tools=llm_tools or None,
                temperature=0.2,
            )
        except Exception as exc:
            return {"current_iteration": iteration, "error": str(exc)}

        step_prompt_tokens = response.usage.prompt_tokens
        step_completion_tokens = response.usage.completion_tokens
        prompt_tokens = int(state.get("prompt_tokens", 0) or 0) + step_prompt_tokens
        completion_tokens = int(state.get("completion_tokens", 0) or 0) + step_completion_tokens
        decision = self._engine._extract_response_decision(response, iteration)
        if decision is None:
            raw_output = (response.content or "")[:200]
            return {
                "current_iteration": iteration,
                "error": f"LLM 输出解析失败: {raw_output}",
                "last_prompt_tokens": step_prompt_tokens,
                "last_completion_tokens": step_completion_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            }

        thought = str(decision["thought"] or "")
        action = str(decision["action"] or "")
        action_input = decision["action_input"] if isinstance(decision["action_input"], dict) else {}
        raw_action_input = decision["raw_action_input"]
        tool_call_id = str(decision["tool_call_id"] or f"call_{iteration}")

        if action == "final_answer":
            return {
                "current_iteration": iteration,
                "pending_answer": str(decision["answer"] or ""),
                "last_thought": thought,
                "last_action": "",
                "last_action_input": {},
                "last_raw_action_input": {},
                "last_tool_call_id": "",
                "last_prompt_tokens": step_prompt_tokens,
                "last_completion_tokens": step_completion_tokens,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "final_gate_decision": (
                    "pending"
                    if state.get("pause_before_finalize") or state.get("approval_before_finalize")
                    else "auto"
                ),
                "final_gate_feedback": "",
                "error": "",
            }

        assistant_message = AssistantMessage(
            content=response.content,
            tool_calls=[
                ToolCallInfo(
                    id=tool_call_id,
                    name=action,
                    arguments=self._engine._serialize_tool_arguments(raw_action_input),
                )
            ],
        )
        return {
            "messages": [self._serialize_message(assistant_message)],
            "current_iteration": iteration,
            "last_thought": thought,
            "last_action": action,
            "last_action_input": action_input,
            "last_raw_action_input": raw_action_input,
            "last_tool_call_id": tool_call_id,
            "last_prompt_tokens": step_prompt_tokens,
            "last_completion_tokens": step_completion_tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "pending_answer": "",
            "error": "",
        }

    async def _tools_node(self, state: ReActGraphState) -> dict[str, Any]:
        action = str(state.get("last_action", "") or "")
        current_iteration = int(state.get("current_iteration", 0) or 0)
        if not action:
            return {"current_iteration": current_iteration, "error": "缺少待执行的工具动作"}

        if state.get("tool_gate_decision") == "rejected":
            feedback = str(state.get("tool_gate_feedback", "") or "")
            return {
                "current_iteration": current_iteration,
                "iteration_count": current_iteration,
                "error": f"工具调用未获审批通过。{feedback}".strip("。"),
            }

        allowed_tools = set(state.get("allowed_tools", []))
        raw_action_input = state.get("last_raw_action_input", state.get("last_action_input", {}))
        if allowed_tools and action not in allowed_tools:
            observation = (
                f"[ERROR] 工具 '{action}' 不在当前技能的允许列表中。"
                f"允许工具: {sorted(allowed_tools)}"
            )
        else:
            tool_result = await self._engine._executor.execute(action, raw_action_input)
            observation = tool_result.to_observation()

        observation = await self._engine._sanitize_observation(observation, action)
        tool_call_id = str(state.get("last_tool_call_id", "") or f"call_{current_iteration}")
        tool_message = ToolMessage(content=observation, tool_call_id=tool_call_id)

        current_messages = self._deserialize_messages(state.get("messages", []))
        current_tokens = count_messages_tokens([*current_messages, tool_message])
        remaining = self._engine._budget.remaining_after(current_tokens)
        logger.debug(
            "iteration_budget_check",
            iteration=current_iteration,
            current_tokens=current_tokens,
            remaining=remaining,
            iteration_budget=self._engine._budget.iteration_budget,
        )

        finalize_reason = ""
        if remaining < self._engine._budget.iteration_budget:
            logger.warning(
                "budget_exhausted_early_stop",
                iteration=current_iteration,
                current_tokens=current_tokens,
                remaining=remaining,
            )
            finalize_reason = (
                f"上下文预算不足，已在第 {current_iteration} 次迭代后停止继续调用工具。"
                f"当前已用 {current_tokens} tokens，"
                f"剩余 {remaining} tokens 不足以支撑下一轮完整推理。"
            )
        elif current_iteration >= self._engine._max_iterations:
            finalize_reason = (
                f"已达到最大迭代次数 {self._engine._max_iterations}，"
                "请基于已有 observation 收敛并给出最终答案。"
            )

        return {
            "messages": [self._serialize_message(tool_message)],
            "last_action": action,
            "last_observation": observation,
            "iteration_count": current_iteration,
            "current_iteration": current_iteration,
            "finalize_reason": finalize_reason,
            "tool_gate_decision": (
                "pending"
                if state.get("pause_before_tools") or state.get("approval_before_tools")
                else "auto"
            ),
            "tool_gate_feedback": "",
        }

    async def _summarize_node(self, state: ReActGraphState) -> dict[str, Any]:
        reason = str(state.get("finalize_reason", "") or "")
        if not reason:
            reason = (
                f"已达到最大迭代次数 {self._engine._max_iterations}，"
                "请基于已有 observation 收敛并给出最终答案。"
            )

        summary_step = await self._engine._summarize_with_current_context(
            self._deserialize_messages(state.get("messages", [])),
            iteration=int(state.get("iteration_count", 0) or 0),
            reason=reason,
        )
        if summary_step.type.value == "error":
            return {
                "error": summary_step.error,
                "iteration_count": int(state.get("iteration_count", 0) or 0),
                "last_prompt_tokens": summary_step.prompt_tokens,
                "last_completion_tokens": summary_step.completion_tokens,
            }

        return {
            "pending_answer": summary_step.answer,
            "last_thought": summary_step.thought,
            "iteration_count": int(state.get("iteration_count", 0) or 0),
            "last_prompt_tokens": summary_step.prompt_tokens,
            "last_completion_tokens": summary_step.completion_tokens,
            "prompt_tokens": int(state.get("prompt_tokens", 0) or 0) + summary_step.prompt_tokens,
            "completion_tokens": int(state.get("completion_tokens", 0) or 0) + summary_step.completion_tokens,
            "final_gate_decision": (
                "pending"
                if state.get("pause_before_finalize") or state.get("approval_before_finalize")
                else "auto"
            ),
            "final_gate_feedback": "",
        }

    async def _finalize_node(self, state: ReActGraphState) -> dict[str, Any]:
        if state.get("final_gate_decision") == "rejected":
            feedback = str(state.get("final_gate_feedback", "") or "")
            suffix = f" 反馈: {feedback}" if feedback else ""
            return {
                "error": f"最终答案未获审批通过。{suffix}".strip(),
                "iteration_count": int(state.get("iteration_count", 0) or 0),
            }

        pending_answer = str(state.get("pending_answer", "") or "")
        if not pending_answer:
            return {
                "error": "最终答案为空，无法完成 finalize。",
                "iteration_count": int(state.get("iteration_count", 0) or 0),
            }

        return {
            "final_answer": pending_answer,
            "pending_answer": "",
            "iteration_count": int(state.get("iteration_count", 0) or 0),
            "last_prompt_tokens": int(state.get("last_prompt_tokens", 0) or 0),
            "last_completion_tokens": int(state.get("last_completion_tokens", 0) or 0),
        }

    def _route_after_agent(self, state: ReActGraphState) -> str:
        if state.get("error"):
            return END
        if state.get("pending_answer"):
            return "finalize"
        if state.get("last_action"):
            return "tools"
        return END

    def _route_after_tools(self, state: ReActGraphState) -> str:
        if state.get("error"):
            return END
        if state.get("finalize_reason"):
            return "summarize"
        return "agent"

    def _resolve_allowed_tools(self, allowed_tool_names: list[str]) -> list[Any]:
        allowed = set(allowed_tool_names)
        return [tool for tool in self._engine._registry.all() if tool.name in allowed]

    def _build_pause_event(self, state: Any) -> dict[str, Any] | None:
        next_nodes = list(state.next)
        if not next_nodes:
            return None
        node = next_nodes[0]
        values = state.values
        checkpoint_id = state.config.get("configurable", {}).get("checkpoint_id", "")

        if node == "tools" and values.get("tool_gate_decision") == "pending":
            return {
                "type": "paused",
                "iteration": int(values.get("current_iteration", 0) or 0),
                "checkpoint_id": checkpoint_id,
                "pause_reason": self._pause_reason(values, node),
                "requires_approval": self._requires_approval(values, node),
                "thought": str(values.get("last_thought", "") or ""),
                "action": str(values.get("last_action", "") or ""),
                "action_input": values.get("last_action_input", {}) or {},
                "data": {
                    "node": node,
                    "next_nodes": next_nodes,
                },
            }

        if node == "finalize" and values.get("final_gate_decision") == "pending":
            return {
                "type": "paused",
                "iteration": int(values.get("iteration_count", 0) or 0),
                "checkpoint_id": checkpoint_id,
                "pause_reason": self._pause_reason(values, node),
                "requires_approval": self._requires_approval(values, node),
                "thought": str(values.get("last_thought", "") or ""),
                "answer_preview": str(values.get("pending_answer", "") or ""),
                "data": {
                    "node": node,
                    "next_nodes": next_nodes,
                },
            }

        return None

    def _build_resume_updates(
        self,
        next_node: str,
        *,
        action: str,
        feedback: str,
    ) -> dict[str, Any] | None:
        normalized = action.lower().strip()
        approved = normalized in {"resume", "approve", "approved", "continue"}
        rejected = normalized in {"reject", "rejected", "cancel", "abort"}
        if not approved and not rejected:
            approved = True

        if next_node == "tools":
            return {
                "tool_gate_decision": "approved" if approved else "rejected",
                "tool_gate_feedback": feedback,
            }
        if next_node == "finalize":
            return {
                "final_gate_decision": "approved" if approved else "rejected",
                "final_gate_feedback": feedback,
            }
        return None

    def _derive_status(self, values: dict[str, Any], next_nodes: list[str]) -> str:
        if values.get("error"):
            return "error"
        if next_nodes:
            pause_event = self._pause_reason(values, next_nodes[0])
            if pause_event:
                return "paused"
            return "running"
        if values.get("final_answer"):
            return "completed"
        return "idle"

    def _pause_reason(self, values: dict[str, Any], next_node: str) -> str:
        if next_node == "tools" and values.get("tool_gate_decision") == "pending":
            if values.get("approval_before_tools"):
                return "approval_before_tools"
            if values.get("pause_before_tools"):
                return "pause_before_tools"
        if next_node == "finalize" and values.get("final_gate_decision") == "pending":
            if values.get("approval_before_finalize"):
                return "approval_before_answer"
            if values.get("pause_before_finalize"):
                return "pause_before_answer"
        return ""

    def _requires_approval(self, values: dict[str, Any], next_node: str) -> bool:
        if next_node == "tools":
            return bool(values.get("approval_before_tools", False))
        if next_node == "finalize":
            return bool(values.get("approval_before_finalize", False))
        return False

    def _serialize_message(self, message: Message) -> dict[str, Any]:
        return message.model_dump(mode="json")

    def _deserialize_messages(self, payload: list[dict[str, Any]]) -> list[Message]:
        return [Message.model_validate(item) for item in payload]

    def _try_get_checkpointer(self) -> Any | None:
        try:
            from langgraph_impl.checkpoint_store import get_checkpointer

            return get_checkpointer()
        except Exception:
            return None

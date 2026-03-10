"""有限状态机 (FSM) — Agent 生命周期管理。

面试考点:
  - 状态机模式：将 Agent 行为拆解为明确的状态和转换，杜绝非法状态跃迁
  - 状态: IDLE → THINKING → ACTING → SYNTHESIZING → FINISHED / ERROR
  - 每次状态转换触发回调（Observer 模式），用于日志、监控、UI 更新
  - 非法转换抛出 FSMError，强制调用方处理异常状态

状态转换图:
  IDLE ──[start]──► THINKING ──[act]──► ACTING ──[observe]──► THINKING
                       │                                         │
                       └──[synthesize]──► SYNTHESIZING ◄────────┘
                                              │
                                         [finish]
                                              │
                                          FINISHED
  任意状态 ──[error]──► ERROR
  ERROR / FINISHED ──[reset]──► IDLE
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class AgentState(str, Enum):
    IDLE = "idle"               # 等待任务
    THINKING = "thinking"       # LLM 推理 / 规划中
    ACTING = "acting"           # 执行工具调用
    SYNTHESIZING = "synthesizing"  # 汇总结果，生成最终答案
    FINISHED = "finished"       # 任务完成
    ERROR = "error"             # 发生错误


class AgentEvent(str, Enum):
    START = "start"             # 开始新任务
    ACT = "act"                 # 决定调用工具
    OBSERVE = "observe"         # 工具结果返回，继续推理
    SYNTHESIZE = "synthesize"   # 开始合成最终答案
    FINISH = "finish"           # 任务完成
    ERROR = "error"             # 发生错误
    RESET = "reset"             # 重置到 IDLE


# 合法状态转换表: {(current_state, event): next_state}
_TRANSITIONS: dict[tuple[AgentState, AgentEvent], AgentState] = {
    (AgentState.IDLE, AgentEvent.START): AgentState.THINKING,
    (AgentState.THINKING, AgentEvent.ACT): AgentState.ACTING,
    (AgentState.THINKING, AgentEvent.SYNTHESIZE): AgentState.SYNTHESIZING,
    (AgentState.ACTING, AgentEvent.OBSERVE): AgentState.THINKING,
    (AgentState.ACTING, AgentEvent.SYNTHESIZE): AgentState.SYNTHESIZING,
    (AgentState.SYNTHESIZING, AgentEvent.FINISH): AgentState.FINISHED,
    # 任意非 FINISHED 状态可触发 ERROR
    (AgentState.IDLE, AgentEvent.ERROR): AgentState.ERROR,
    (AgentState.THINKING, AgentEvent.ERROR): AgentState.ERROR,
    (AgentState.ACTING, AgentEvent.ERROR): AgentState.ERROR,
    (AgentState.SYNTHESIZING, AgentEvent.ERROR): AgentState.ERROR,
    # ERROR / FINISHED 可重置
    (AgentState.ERROR, AgentEvent.RESET): AgentState.IDLE,
    (AgentState.FINISHED, AgentEvent.RESET): AgentState.IDLE,
}


class FSMError(RuntimeError):
    """非法状态转换错误。"""


StateChangeCallback = Callable[[AgentState, AgentEvent, AgentState], None]


class AgentFSM:
    """Agent 有限状态机。"""

    def __init__(self, agent_id: str = "agent") -> None:
        self._agent_id = agent_id
        self._state: AgentState = AgentState.IDLE
        self._callbacks: list[StateChangeCallback] = []
        self._history: list[tuple[AgentState, AgentEvent, AgentState]] = []

    @property
    def state(self) -> AgentState:
        return self._state

    def on_transition(self, callback: StateChangeCallback) -> None:
        """注册状态转换回调（Observer 模式）。"""
        self._callbacks.append(callback)

    def trigger(self, event: AgentEvent) -> AgentState:
        """触发事件，执行状态转换。

        Returns:
            转换后的新状态

        Raises:
            FSMError: 当前状态不允许该事件
        """
        key = (self._state, event)
        next_state = _TRANSITIONS.get(key)
        if next_state is None:
            raise FSMError(
                f"[FSM] 非法转换: {self._state.value} --[{event.value}]--> ?"
            )

        prev = self._state
        self._state = next_state
        self._history.append((prev, event, next_state))

        logger.debug(
            "fsm_transition",
            agent=self._agent_id,
            from_state=prev.value,
            trigger=event.value,
            to_state=next_state.value,
        )
        for cb in self._callbacks:
            try:
                cb(prev, event, next_state)
            except Exception as e:
                logger.warning("fsm_callback_error", error=str(e))

        return next_state

    def reset(self) -> None:
        """重置到 IDLE 状态。"""
        if self._state in (AgentState.FINISHED, AgentState.ERROR):
            self.trigger(AgentEvent.RESET)
        else:
            self._state = AgentState.IDLE

    def transition_history(self) -> list[tuple[AgentState, AgentEvent, AgentState]]:
        """返回完整的状态转换历史。"""
        return list(self._history)

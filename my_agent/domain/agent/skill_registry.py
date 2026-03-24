"""Agent Skill 注册中心。"""

from __future__ import annotations

from my_agent.domain.agent.skill import AgentSkill
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)


class SkillRegistry:
    """全局 Skill 注册中心。"""

    _instance: "SkillRegistry | None" = None

    def __new__(cls) -> "SkillRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._skills = {}
        return cls._instance

    def register(self, skill: AgentSkill) -> None:
        self._skills[skill.name] = skill
        logger.debug("skill_registered", name=skill.name)

    def get(self, name: str) -> AgentSkill | None:
        return self._skills.get(name)

    def all(self) -> list[AgentSkill]:
        return list(self._skills.values())

    def names(self) -> list[str]:
        return list(self._skills.keys())

    def match(self, query: str) -> AgentSkill | None:
        """按关键词和优先级自动匹配最佳 Skill。"""
        best: AgentSkill | None = None
        best_score = 0

        for skill in self._skills.values():
            score = skill.match_score(query)
            if score <= 0:
                continue
            if score > best_score or (score == best_score and best and skill.priority > best.priority):
                best = skill
                best_score = score

        if best is not None:
            logger.info("skill_auto_matched", skill=best.name, score=best_score)
        return best


_registry = SkillRegistry()


def get_skill_registry() -> SkillRegistry:
    return _registry


def _register_builtin_skills() -> None:
    registry = get_skill_registry()
    if registry.names():
        return

    registry.register(AgentSkill(
        name="observation-audit",
        description="分析 ReAct Observation 的生成、检查、截断、注入与风险。",
        system_instructions=(
            "你当前处于 Observation 审计技能模式。\n"
            "优先分析 Action -> ToolResult -> Observation -> ToolMessage 注入链路。\n"
            "回答时要指出已有检查点、缺失的检查点和潜在风险。"
        ),
        few_shot=(
            "技能提示：当用户询问 Observation 时，重点检查工具存在性、参数解析、"
            "异常处理、结果截断、上下文预算检查和缺失的安全审查。"
        ),
        trigger_terms=["observation", "观察结果", "tool result", "工具结果", "obs"],
        allowed_tools=["web_search", "http_request"],
        priority=100,
    ))

    registry.register(AgentSkill(
        name="react-debug",
        description="分析 ReAct 推理链路、Action/Observation 循环和停止条件。",
        system_instructions=(
            "你当前处于 ReAct 调试技能模式。\n"
            "解释问题时优先结合迭代循环、消息历史注入、工具调用闭环和停止条件。"
        ),
        few_shot=(
            "技能提示：当用户排查 ReAct 问题时，优先按入口、循环、工具执行、"
            "Observation 注入、SSE 推送的顺序展开。"
        ),
        trigger_terms=["react", "推理引擎", "thought", "action", "observation", "循环"],
        allowed_tools=["web_search", "http_request"],
        priority=80,
    ))

    registry.register(AgentSkill(
        name="budget-audit",
        description="分析上下文窗口、System/History/Iteration 预算与提前终止。",
        system_instructions=(
            "你当前处于 Token 预算审计技能模式。\n"
            "优先解释 system/history/iteration/output 各层预算如何协作，"
            "并指出上下文溢出或提前终止的触发点。"
        ),
        few_shot=(
            "技能提示：当用户提到 token、budget、上下文溢出、裁剪、trim 时，"
            "重点分析分层预算和 early stop。"
        ),
        trigger_terms=["token", "budget", "上下文", "溢出", "trim", "裁剪"],
        allowed_tools=["web_search"],
        priority=90,
    ))


_register_builtin_skills()

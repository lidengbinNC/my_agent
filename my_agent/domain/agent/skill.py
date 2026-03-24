"""Agent Skill 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentSkill:
    """运行时 Agent Skill。

    Skill 不是独立引擎，而是对当前任务注入额外的领域指令、
    工具约束和 few-shot 示例，增强 ReAct 的专长。
    """

    name: str
    description: str
    system_instructions: str = ""
    few_shot: str = ""
    trigger_terms: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    priority: int = 0

    def match_score(self, query: str) -> int:
        """按关键词计算匹配分数，分数越高越优先。"""
        normalized = query.lower()
        score = 0
        for term in self.trigger_terms:
            if term.lower() in normalized:
                score += len(term)
        return score

    @property
    def has_tool_restrictions(self) -> bool:
        return bool(self.allowed_tools)

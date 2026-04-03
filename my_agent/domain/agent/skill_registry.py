"""Agent Skill 注册中心。"""

from __future__ import annotations

from my_agent.domain.agent.skill import AgentSkill
from my_agent.domain.customer_service import READ_ONLY_TOOL_NAMES, WRITE_TOOL_NAMES
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

    registry.register(AgentSkill(
        name="customer-service-copilot",
        description="海外客服坐席辅助：知识检索、订单物流查询、会话总结与推荐回复。",
        system_instructions=(
            "你当前处于客服 Copilot 模式。\n"
            "优先通过知识、订单、物流、会话历史工具补齐证据。\n"
            "回答要适合坐席使用，输出建议回复、证据来源、风险提示和下一步动作。"
        ),
        few_shot=(
            "技能提示：优先输出 1）问题判断 2）推荐回复 3）证据来源 4）下一步动作。\n"
            "除非明确允许，否则不要发起任何写操作。"
        ),
        trigger_terms=["客服", "客户", "copilot", "推荐回复", "物流", "退款", "售后"],
        allowed_tools=READ_ONLY_TOOL_NAMES,
        priority=120,
    ))

    registry.register(AgentSkill(
        name="pre-sales-consulting",
        description="售前咨询：商品、FAQ、政策、推荐回答。",
        system_instructions=(
            "你当前处于售前咨询模式。\n"
            "优先使用知识检索和客户画像工具，给出简洁、可直接发送的回复建议。"
        ),
        few_shot="技能提示：售前问题优先知识引用，不随意承诺补偿或退款。",
        trigger_terms=["售前", "购买前", "商品咨询", "价格", "优惠", "活动"],
        allowed_tools=READ_ONLY_TOOL_NAMES,
        priority=110,
    ))

    registry.register(AgentSkill(
        name="after-sales-triage",
        description="售后分流：订单、物流、退款、工单前置判断。",
        system_instructions=(
            "你当前处于售后分流模式。\n"
            "需要先判断是物流、退款、质量、投诉还是其他售后问题，再给出分流建议。"
        ),
        few_shot="技能提示：先查订单/物流/退款，再决定是否建议建工单。",
        trigger_terms=["售后", "退款", "退货", "物流", "没收到", "坏了", "补发"],
        allowed_tools=READ_ONLY_TOOL_NAMES,
        priority=115,
    ))

    registry.register(AgentSkill(
        name="ticket-assistant",
        description="工单助手：整理问题摘要、分类、优先级和建单草稿。",
        system_instructions=(
            "你当前处于工单助手模式。\n"
            "请优先整理标题、分类、优先级、摘要、证据和需补充字段。\n"
            "如果允许写操作，可调用工单工具，但必须标记审批需求。"
        ),
        few_shot="技能提示：输出建单草稿时，字段要完整，可直接给人工审核。",
        trigger_terms=["工单", "建单", "ticket", "升级工单", "售后单"],
        allowed_tools=[*READ_ONLY_TOOL_NAMES, *WRITE_TOOL_NAMES],
        priority=118,
    ))

    registry.register(AgentSkill(
        name="complaint-review",
        description="投诉识别与升级建议：识别高风险投诉并生成审批建议。",
        system_instructions=(
            "你当前处于投诉复核模式。\n"
            "重点识别情绪升级、赔付诉求、舆情风险和跨部门升级信号。"
        ),
        few_shot="技能提示：投诉场景必须输出风险等级、建议动作和人工审批说明。",
        trigger_terms=["投诉", "申诉", "赔偿", "差评", "维权", "舆情"],
        allowed_tools=[*READ_ONLY_TOOL_NAMES, *WRITE_TOOL_NAMES],
        priority=125,
    ))


_register_builtin_skills()

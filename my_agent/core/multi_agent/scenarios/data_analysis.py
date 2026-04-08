"""预置场景：数据分析（DataAgent + AnalystAgent + ReporterAgent）。

面试考点:
  - Hierarchical 模式应用：Manager 根据数据类型动态分配任务
  - 三种角色分工：数据获取 → 统计分析 → 报告生成
  - 与研究报告场景的对比：
      研究报告 = Sequential（顺序依赖强）
      数据分析 = Hierarchical（Manager 可灵活决定先做哪步）

流程:
  Manager 分配:
    DataAgent    → 获取/清洗数据，返回结构化数据摘要
    AnalystAgent → 统计分析、趋势识别、异常检测
    ReporterAgent → 生成数据分析报告（含关键指标和建议）
  Manager 审核 → [修改] → 最终报告
"""

from __future__ import annotations

from my_agent.core.multi_agent.hierarchical import HierarchicalCoordinator
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentRole
from my_agent.domain.tool.registry import ToolRegistry


def build_data_analysis_agents() -> list[AgentSpec]:
    """构建数据分析场景的 Agent 规格列表。"""
    return [
        AgentSpec(
            name="manager",
            role=AgentRole.MANAGER,
            description="项目经理，负责任务分配和结果审核",
            system_prompt=(
                "你是一名数据项目经理，擅长拆解分析任务并协调团队。"
            ),
            tools=[],
            max_iterations=4,
        ),
        AgentSpec(
            name="data_agent",
            role=AgentRole.DATA_ANALYST,
            description="数据工程师，负责数据获取、清洗和描述性统计",
            system_prompt=(
                "你是一名数据工程师。你的任务是：\n"
                "1. 理解数据需求，描述数据结构和字段含义\n"
                "2. 如有必要，使用 code_executor 编写数据处理代码\n"
                "3. 输出：数据摘要（行数、字段、缺失值、基础统计量）\n"
                "4. 发现数据质量问题时明确指出"
            ),
            tools=["code_executor", "calculator"],
            max_iterations=6,
        ),
        AgentSpec(
            name="analyst_agent",
            role=AgentRole.DATA_ANALYST,
            description="数据分析师，负责统计分析、趋势识别和异常检测",
            system_prompt=(
                "你是一名资深数据分析师。你的任务是：\n"
                "1. 基于数据摘要进行深度分析\n"
                "2. 识别关键趋势、模式和异常\n"
                "3. 使用 code_executor 进行统计计算（如需要）\n"
                "4. 输出：关键指标、趋势分析、异常说明、业务洞察"
            ),
            tools=["code_executor", "calculator"],
            max_iterations=6,
        ),
        AgentSpec(
            name="reporter_agent",
            role=AgentRole.REPORTER,
            description="报告生成专家，负责撰写数据分析报告",
            system_prompt=(
                "你是一名商业分析报告专家。你的任务是：\n"
                "1. 将数据和分析结果转化为清晰的业务报告\n"
                "2. 报告结构：执行摘要 → 数据概览 → 关键发现 → 业务影响 → 行动建议\n"
                "3. 使用图表描述（文字版）辅助说明\n"
                "4. 语言简洁专业，适合非技术人员阅读"
            ),
            tools=[],
            max_iterations=4,
        ),
    ]


def create_data_analysis_pipeline(
    llm: BaseLLMClient,
    tool_registry: ToolRegistry,
) -> HierarchicalCoordinator:
    """创建数据分析协作流水线（Hierarchical）。

    Returns:
        配置好的 HierarchicalCoordinator
    """
    agents = build_data_analysis_agents()

    return HierarchicalCoordinator(
        llm=llm,
        tool_registry=tool_registry,
        agents=agents,
        manager_name="manager",
    )

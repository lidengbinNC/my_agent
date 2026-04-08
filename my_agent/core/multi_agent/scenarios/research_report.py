"""预置场景：研究报告生成（Researcher → Writer → Reviewer）。

面试考点:
  - Sequential 模式最典型应用：三个角色有明确的先后依赖
  - 每个 Agent 拥有专属 system_prompt，专注自己的领域（专家化）
  - Reviewer 持有前两步结果，能给出有针对性的改进建议

流程:
  1. Researcher: 搜集信息、分析数据，输出结构化研究素材
  2. Writer: 基于素材撰写报告草稿（Markdown 格式）
  3. Reviewer: 审核草稿，提出修改建议并输出最终版本
"""

from __future__ import annotations

from my_agent.core.multi_agent.sequential import SequentialCoordinator
from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.multi_agent.agent_spec import AgentSpec
from my_agent.domain.multi_agent.message import AgentRole
from my_agent.domain.tool.registry import ToolRegistry


def build_research_report_agents() -> list[AgentSpec]:
    """构建研究报告场景的 Agent 规格列表。"""
    return [
        AgentSpec(
            name="researcher",
            role=AgentRole.RESEARCHER,
            description="负责信息搜集、数据分析，输出结构化研究素材",
            system_prompt=(
                "你是一名专业研究员。你的任务是：\n"
                "1. 搜集与主题相关的关键信息和数据\n"
                "2. 分析信息的可靠性和相关性\n"
                "3. 整理为结构化素材（背景、现状、数据、趋势）\n"
                "输出格式：清晰的条目列表，包含来源说明。"
            ),
            tools=["web_search", "http_request"],
            max_iterations=6,
        ),
        AgentSpec(
            name="writer",
            role=AgentRole.WRITER,
            description="基于研究素材撰写报告草稿",
            system_prompt=(
                "你是一名专业技术写作员。你的任务是：\n"
                "1. 基于提供的研究素材，撰写完整的报告草稿\n"
                "2. 报告结构：摘要 → 背景 → 主要发现 → 结论 → 建议\n"
                "3. 使用 Markdown 格式，语言专业流畅\n"
                "4. 确保内容有逻辑性，每个论点有数据支撑"
            ),
            tools=[],
            max_iterations=4,
        ),
        AgentSpec(
            name="reviewer",
            role=AgentRole.REVIEWER,
            description="审核报告草稿，提出修改建议并输出最终版本",
            system_prompt=(
                "你是一名严格的内容审核编辑。你的任务是：\n"
                "1. 审核报告的准确性、逻辑性和完整性\n"
                "2. 检查是否有矛盾或不支持的论点\n"
                "3. 改进表达，使报告更专业、清晰\n"
                "4. 直接输出修改后的最终完整报告（Markdown 格式）\n"
                "注意：输出最终报告，不要只列改进意见。"
            ),
            tools=[],
            max_iterations=4,
        ),
    ]


def create_research_report_pipeline(
    llm: BaseLLMClient,
    tool_registry: ToolRegistry,
) -> SequentialCoordinator:
    """创建研究报告生成 Pipeline（Sequential）。

    Args:
        llm: LLM 客户端
        tool_registry: 工具注册表（Researcher 会使用 web_search）

    Returns:
        配置好的 SequentialCoordinator
    """
    agents = build_research_report_agents()

    return SequentialCoordinator(llm=llm, tool_registry=tool_registry, agents=agents)

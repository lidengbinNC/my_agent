"""多 Agent 结构化交接模型。

设计目标：
  - Agent 内部执行过程保持私有 workspace，不直接暴露给其他 Agent
  - 对外只共享结构化 handoff，避免完整推理链污染全局上下文
  - handoff 可安全写入 LangGraph State / DB / 审计日志
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AgentHandoff(BaseModel):
    """单个 Agent 对外发布的结构化交接结果。"""

    agent_name: str = Field(description="产出该 handoff 的 Agent 名称")
    task_id: str = Field(default="", description="本次子任务标识")
    task: str = Field(default="", description="Agent 实际收到的任务描述")
    summary: str = Field(default="", description="供其他 Agent 使用的精简总结")
    facts: list[str] = Field(default_factory=list, description="可跨 Agent 复用的事实")
    artifacts: list[str] = Field(default_factory=list, description="产物或中间成果摘要")
    risks: list[str] = Field(default_factory=list, description="当前结果中的风险或不确定性")
    next_recommendations: list[str] = Field(
        default_factory=list,
        description="推荐给下游 Agent 的后续动作",
    )
    final_output: str = Field(default="", description="该 Agent 的原始最终输出")


class AgentWorkspaceSnapshot(BaseModel):
    """Agent 私有工作区的可持久化快照。

    该对象只用于断点恢复和审计，不会被直接注入其他 Agent 的 prompt。
    """

    agent_name: str
    task: str = ""
    context_digest: str = ""
    final_output: str = ""
    handoff_summary: str = ""

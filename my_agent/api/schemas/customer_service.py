"""客服融合场景 Schema。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CustomerContext(BaseModel):
    customer_id: str = Field(default="", description="客户 ID")
    customer_tier: str = Field(default="", description="客户等级")
    channel: str = Field(default="", description="渠道，如 whatsapp/facebook/line")
    locale: str = Field(default="zh-CN", description="语言/地区")
    order_id: str = Field(default="", description="关联订单 ID")
    ticket_id: str = Field(default="", description="关联工单 ID")
    knowledge_domain: str = Field(default="faq", description="知识域")
    knowledge_base: str = Field(default="", description="指定知识库")
    session_id: str = Field(default="", description="外部客服会话 ID")
    tags: list[str] = Field(default_factory=list, description="业务标签")
    metadata: dict[str, Any] = Field(default_factory=dict, description="补充上下文")


class CustomerServiceCopilotRequest(BaseModel):
    message: str = Field(..., min_length=1, description="客户问题或坐席指令")
    session_id: str | None = Field(default=None, description="MyAgent 会话 ID")
    stream: bool = Field(default=False, description="是否流式返回")
    mode: Literal["read_only", "copilot", "ticket_draft", "complaint_review", "after_sales", "pre_sales"] = Field(
        default="copilot",
        description="客服执行模式",
    )
    skill: str | None = Field(default=None, description="显式指定 Skill")
    allow_write_actions: bool = Field(default=False, description="是否允许进入写操作审批流")
    approval_before_answer: bool = Field(default=False, description="最终答复前是否审批")
    customer_context: CustomerContext = Field(default_factory=CustomerContext)


class CustomerServiceFeedbackRequest(BaseModel):
    session_id: str = Field(default="", description="会话 ID")
    run_id: str = Field(default="", description="运行 ID")
    customer_id: str = Field(default="", description="客户 ID")
    knowledge_domain: str = Field(default="faq", description="知识域")
    adopted: bool = Field(default=False, description="坐席是否采纳")
    rating: int = Field(default=0, ge=0, le=5, description="评分")
    feedback_type: str = Field(default="reply_suggestion", description="反馈类型")
    feedback_text: str = Field(default="", description="反馈文本")
    metadata: dict[str, Any] = Field(default_factory=dict, description="补充元数据")


class CustomerServiceTaskRequest(BaseModel):
    task_type: Literal["customer_service_copilot", "after_sales_ticket_draft"] = Field(
        default="customer_service_copilot",
        description="异步任务类型",
    )
    message: str = Field(..., min_length=1, description="用户消息")
    mode: str = Field(default="copilot", description="执行模式")
    allow_write_actions: bool = Field(default=False, description="是否允许进入写操作审批流")
    customer_context: CustomerContext = Field(default_factory=CustomerContext)

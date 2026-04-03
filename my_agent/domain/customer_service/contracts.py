"""客服融合基线契约与写接口白名单。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class SystemBoundary:
    system: str
    role: str
    responsibilities: list[str]
    owns_write_path: bool = False


@dataclass(frozen=True)
class MainDataMapping:
    entity: str
    source_of_truth: str
    agent_field: str
    notes: str = ""


@dataclass(frozen=True)
class ToolPolicy:
    tool_name: str
    category: str
    access_mode: str
    owner_system: str
    requires_approval: bool
    description: str


@dataclass(frozen=True)
class CustomerServiceBaseline:
    boundaries: list[SystemBoundary] = field(default_factory=list)
    main_data_mapping: list[MainDataMapping] = field(default_factory=list)
    write_whitelist: list[ToolPolicy] = field(default_factory=list)
    knowledge_domains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "boundaries": [asdict(item) for item in self.boundaries],
            "main_data_mapping": [asdict(item) for item in self.main_data_mapping],
            "write_whitelist": [asdict(item) for item in self.write_whitelist],
            "knowledge_domains": list(self.knowledge_domains),
        }


BASELINE = CustomerServiceBaseline(
    boundaries=[
        SystemBoundary(
            system="existing_customer_service",
            role="交易主系统",
            responsibilities=[
                "多渠道接入",
                "会话状态流转",
                "转人工",
                "工单主状态",
                "客服消息出入站",
            ],
            owns_write_path=True,
        ),
        SystemBoundary(
            system="my_agent",
            role="AI 编排层",
            responsibilities=[
                "客服意图识别",
                "知识检索编排",
                "坐席 Copilot",
                "售后任务编排",
                "人工审批与审计",
            ],
        ),
        SystemBoundary(
            system="my_rag",
            role="知识检索层",
            responsibilities=[
                "知识库管理",
                "证据检索与重排",
                "按领域分库",
            ],
        ),
        SystemBoundary(
            system="external_business_systems",
            role="业务数据权威源",
            responsibilities=[
                "订单",
                "物流",
                "退款",
                "会员",
                "CRM",
            ],
            owns_write_path=True,
        ),
        SystemBoundary(
            system="dify",
            role="运营配置层",
            responsibilities=[
                "低代码工作流试验",
                "运营话术实验",
                "非核心流程原型",
            ],
        ),
    ],
    main_data_mapping=[
        MainDataMapping("customer", "crm_or_member_center", "customer_id", "客户主档，AI 只读优先"),
        MainDataMapping("session", "existing_customer_service", "session_id", "客服会话主键"),
        MainDataMapping("channel", "existing_customer_service", "channel", "如 whatsapp/facebook/line"),
        MainDataMapping("ticket", "ticket_system", "ticket_id", "工单主状态与写入仍以工单系统为准"),
        MainDataMapping("order", "oms_or_order_center", "order_id", "售后、物流、退款的业务锚点"),
        MainDataMapping("refund", "refund_system", "refund_id", "高风险写操作需审批"),
    ],
    write_whitelist=[
        ToolPolicy(
            tool_name="ticket_create_tool",
            category="ticket",
            access_mode="write",
            owner_system="ticket_system",
            requires_approval=True,
            description="创建售后工单或投诉工单",
        ),
        ToolPolicy(
            tool_name="ticket_update_tool",
            category="ticket",
            access_mode="write",
            owner_system="ticket_system",
            requires_approval=True,
            description="更新工单状态、评论、标签、指派信息",
        ),
    ],
    knowledge_domains=["faq", "sop", "policy", "ticket", "quality", "product"],
)


READ_ONLY_TOOL_NAMES = [
    "customer_profile_tool",
    "order_query_tool",
    "logistics_query_tool",
    "refund_status_tool",
    "session_history_tool",
    "knowledge_search_tool",
]

WRITE_TOOL_NAMES = [item.tool_name for item in BASELINE.write_whitelist]


def get_customer_service_baseline() -> dict:
    return BASELINE.to_dict()


def requires_approval(tool_name: str) -> bool:
    for policy in BASELINE.write_whitelist:
        if policy.tool_name == tool_name:
            return policy.requires_approval
    return False

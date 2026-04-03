# 客服融合系统基线

## 系统边界

- `existing_customer_service`: 继续作为交易主系统，负责多渠道接入、会话主状态、转人工、工单主状态和消息出入站。
- `my_agent`: 作为 AI 编排层，负责客服意图识别、知识检索编排、坐席 Copilot、售后任务编排、人工审批和审计。
- `my_rag`: 作为知识检索层，负责 FAQ、SOP、政策、工单知识和质检规则的分库检索。
- `external_business_systems`: 作为订单、物流、退款、会员、CRM 等业务数据的权威源。
- `dify`: 作为运营配置和快速试验层，承载低代码工作流和话术实验。

## 主数据映射

| 实体 | 权威系统 | AI 使用字段 | 说明 |
| --- | --- | --- | --- |
| customer | CRM / 会员中心 | `customer_id` | 客户主档以业务系统为准 |
| session | 客服主系统 | `session_id` | 客服会话主键 |
| channel | 客服主系统 | `channel` | WhatsApp / Facebook / Line 等 |
| ticket | 工单系统 | `ticket_id` | 工单主状态以工单系统为准 |
| order | 订单中心 | `order_id` | 售后与物流的业务锚点 |
| refund | 退款系统 | `refund_id` | 高风险动作，必须审批 |

## 写接口白名单

- `ticket_create_tool`: 创建售后或投诉工单，默认只生成草稿，正式写入前必须审批。
- `ticket_update_tool`: 更新工单状态、评论、标签或指派信息，正式写入前必须审批。

## 第一批知识域

- `faq`
- `sop`
- `policy`
- `ticket`
- `quality`
- `product`

"""Agent 间消息传递协议。

面试考点:
  - 多 Agent 通信必须有统一协议，否则 Agent 间解耦不彻底
  - AgentMessage 携带来源/目标/类型/内容/元数据，解耦发送方与接收方
  - MessageType 区分任务分配、结果上报、状态通知，便于中介者路由
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    TASK = "task"           # 任务分配: Coordinator → Agent
    RESULT = "result"       # 结果上报: Agent → Coordinator
    FEEDBACK = "feedback"   # 反馈修改: Reviewer → Writer 等
    STATUS = "status"       # 状态通知: Agent → Coordinator
    ERROR = "error"         # 错误上报


class AgentRole(str, Enum):
    """预定义的 Agent 角色，用于可读性和场景快速创建。"""
    MANAGER = "manager"
    RESEARCHER = "researcher"
    WRITER = "writer"
    REVIEWER = "reviewer"
    DATA_ANALYST = "data_analyst"
    VISUALIZER = "visualizer"
    REPORTER = "reporter"
    WORKER = "worker"
    CUSTOM = "custom"


@dataclass
class AgentMessage:
    """Agent 间传递的消息单元。

    设计原则：不可变字段用 dataclass，保证消息在传递过程中不被意外修改。
    """

    msg_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    type: MessageType = MessageType.TASK
    sender: str = ""            # 发送方 Agent 名称
    receiver: str = ""          # 接收方 Agent 名称（空表示广播）
    content: str = ""           # 消息正文
    task_id: str = ""           # 关联的任务 ID
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def reply(self, content: str, msg_type: MessageType = MessageType.RESULT) -> "AgentMessage":
        """创建一条回复消息（自动填充 sender/receiver）。"""
        return AgentMessage(
            type=msg_type,
            sender=self.receiver,
            receiver=self.sender,
            content=content,
            task_id=self.task_id,
        )

    def to_context(self) -> str:
        """将消息格式化为 LLM Prompt 可读的字符串。"""
        return f"[{self.sender} → {self.receiver}] {self.content}"

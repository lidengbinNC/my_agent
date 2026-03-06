"""Structured Output — 确保 LLM 输出符合 Pydantic Schema，自动重试。

面试考点:
  - JSON Mode vs Structured Outputs 的区别
  - Pydantic model_validate_json 运行时校验
  - 校验失败后将错误信息反馈给 LLM 的"self-healing"重试策略
  - 递减温度提升重试成功率
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from my_agent.domain.llm.base import BaseLLMClient
from my_agent.domain.llm.message import Message, UserMessage
from my_agent.utils.logger import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(Exception):
    """结构化输出校验多次重试后仍失败。"""


async def get_structured_output(
    client: BaseLLMClient,
    messages: list[Message],
    output_model: type[T],
    *,
    max_retries: int = 3,
    initial_temperature: float = 0.7,
) -> T:
    """调用 LLM 并确保输出符合 output_model 的 Schema。

    策略:
      1. 开启 JSON Mode 要求 LLM 输出合法 JSON
      2. 用 Pydantic 校验 JSON 是否符合 Schema
      3. 校验失败 → 将错误信息反馈给 LLM → 降低温度 → 重试
    """
    work_messages = list(messages)
    schema_hint = _build_schema_hint(output_model)
    work_messages.append(UserMessage(schema_hint))

    for attempt in range(max_retries):
        temperature = max(0.1, initial_temperature - attempt * 0.2)

        response = await client.chat(
            work_messages,
            temperature=temperature,
            response_format={"type": "json_object"},
        )

        raw_content = response.content or ""
        logger.debug("structured_output_attempt", attempt=attempt + 1, raw=raw_content[:200])

        try:
            result = output_model.model_validate_json(raw_content)
            logger.info("structured_output_success", attempt=attempt + 1, model=output_model.__name__)
            return result
        except ValidationError as e:
            error_msg = _format_validation_error(e)
            logger.warning(
                "structured_output_validation_failed",
                attempt=attempt + 1,
                error=error_msg,
            )
            work_messages.append(
                UserMessage(
                    f"你的输出格式不正确，解析错误:\n{error_msg}\n"
                    f"请严格按照以下 JSON Schema 重新输出，不要包含额外文字:\n{schema_hint}"
                )
            )

    raise StructuredOutputError(
        f"结构化输出失败: 已重试 {max_retries} 次，模型输出无法通过 {output_model.__name__} 校验"
    )


def _build_schema_hint(model: type[BaseModel]) -> str:
    """从 Pydantic Model 生成 Schema 提示注入到消息中。"""
    schema = model.model_json_schema()
    import json

    schema_str = json.dumps(schema, ensure_ascii=False, indent=2)
    return (
        f"请以 JSON 格式输出，严格符合以下 JSON Schema:\n```json\n{schema_str}\n```\n"
        "只输出 JSON，不要包含其他文字。"
    )


def _format_validation_error(e: ValidationError) -> str:
    """将 Pydantic 校验错误格式化为可读字符串。"""
    lines: list[str] = []
    for err in e.errors():
        loc = " → ".join(str(x) for x in err["loc"])
        lines.append(f"  字段 [{loc}]: {err['msg']} (类型: {err['type']})")
    return "\n".join(lines)

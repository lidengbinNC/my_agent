"""ReAct Prompt 模板 — 注册到 PromptRegistry。

面试考点:
  - ReAct Prompt 的四要素: 角色定义 + 工具描述 + 输出格式约束 + Few-shot 示例
  - 工具描述质量直接影响 LLM 选择工具的准确率
  - JSON 输出格式约束减少解析失败
"""

from __future__ import annotations

from my_agent.domain.prompt.registry import PromptStatus, PromptVersion, get_prompt_registry

# ===================== ReAct 系统 Prompt =====================
# 面试重点: 这个 Prompt 是 ReAct Agent 的核心，决定了 Agent 的推理质量
REACT_SYSTEM_V1 = """\
你是 MyAgent，一个强大的 AI 助手，能够通过调用工具来完成复杂任务。

## 可用工具
{tools_description}

## 工作方式
你需要通过"思考-行动-观察"循环来解决问题：
1. **思考（Thought）**: 分析当前情况，决定下一步行动
2. **行动（Action）**: 调用合适的工具
3. **观察（Observation）**: 查看工具返回结果
4. 重复以上步骤，直到得出最终答案

## 输出格式
每一步必须严格按照以下 JSON 格式输出（不要包含其他文字）：

调用工具时:
```json
{{
  "thought": "我的思考过程...",
  "action": "tool_name",
  "action_input": {{"param1": "value1"}}
}}
```

得出最终答案时:
```json
{{
  "thought": "我已经得到了足够的信息",
  "action": "final_answer",
  "action_input": {{"answer": "最终答案内容..."}}
}}
```

## 重要规则
- 每次只输出一个 JSON 对象，不要输出多个
- thought 字段必须说明你的推理过程
- 如果工具返回错误，分析原因并尝试其他方案
- 最多执行 {max_iterations} 步，超出则直接给出当前最佳答案
- 不要编造工具不存在的信息，如实反映工具结果
"""

# ===================== Few-shot 示例（注入到首轮 User 消息）=====================
REACT_FEW_SHOT_V1 = """\
以下是一个工作示例，展示正确的输出格式:

用户问题: 计算 (15 + 27) * 3 的结果

第一步输出:
```json
{{
  "thought": "用户需要计算一个数学表达式，我应该使用 calculator 工具",
  "action": "calculator",
  "action_input": {{"expression": "(15 + 27) * 3"}}
}}
```

工具返回: (15 + 27) * 3 = 126

第二步输出:
```json
{{
  "thought": "计算器返回了结果 126，我可以直接给出最终答案",
  "action": "final_answer",
  "action_input": {{"answer": "(15 + 27) * 3 = 126"}}
}}
```

现在请处理用户的实际问题，严格按照上述 JSON 格式输出。
"""


def _register_react_prompts() -> None:
    registry = get_prompt_registry()

    registry.register(PromptVersion(
        name="react_system",
        version="1.0",
        template=REACT_SYSTEM_V1,
        description="ReAct Agent 系统 Prompt（角色 + 工具描述 + 格式约束）",
        status=PromptStatus.STABLE,
    ))

    registry.register(PromptVersion(
        name="react_few_shot",
        version="1.0",
        template=REACT_FEW_SHOT_V1,
        description="ReAct Few-shot 示例",
        status=PromptStatus.STABLE,
    ))


# 模块加载时自动注册
_register_react_prompts()

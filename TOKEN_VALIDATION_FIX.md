# Token 校验功能实现说明

## 问题分析

### 原有问题

虽然项目实现了完整的 Token 计数工具（`token_counter.py`）和三种记忆策略，但存在以下问题：

1. **`token_count()` 方法未被实际调用**
   - `BufferMemory` 和 `WindowMemory` 实现了 `token_count()` 方法，但从未被使用
   - 只有 `SummaryMemory` 内部使用了 Token 校验来触发摘要压缩

2. **`ReActEngine` 缺少发送前校验**
   - 直接将消息列表发送给 LLM，没有检查是否超出上下文窗口
   - 可能导致 API 调用失败：`context_length_exceeded` 错误

3. **潜在风险**
   - `BufferMemory` 会无限增长，最终超出 LLM 上下文窗口（如 8K/16K）
   - `WindowMemory` 虽然有 `maxlen` 限制，但没有基于实际 Token 数量控制
   - 用户可能在不知情的情况下浪费 API 配额或遇到错误

---

## 解决方案

### 1. 在 `ReActEngine.run()` 中添加 Token 校验

#### 修改文件：`my_agent/core/engine/react_engine.py`

**添加导入**：
```python
from my_agent.utils.token_counter import count_messages_tokens, estimate_remaining_tokens
```

**添加构造函数参数**：
```python
def __init__(
    self,
    llm: BaseLLMClient,
    tool_registry: ToolRegistry,
    max_iterations: int = 10,
    tool_timeout: float = 30.0,
    max_context_tokens: int = 8192,      # ← 新增：最大上下文窗口
    reserved_for_output: int = 1024,     # ← 新增：为输出预留的 Token 数
) -> None:
    # ...
    self._max_context_tokens = max_context_tokens
    self._reserved_for_output = reserved_for_output
```

**在 `run()` 方法中添加校验逻辑**：
```python
# 构建消息列表
messages: list[Message] = [SystemMessage(system_content)]
if history:
    messages.extend(history)

# Few-shot 示例 + 用户问题
few_shot = self._prompt_registry.render("react_few_shot")
messages.append(UserMessage(f"{few_shot}\n\n用户问题: {query}"))

# ✅ Token 校验：检查是否超出上下文窗口
total_tokens = count_messages_tokens(messages)
remaining_tokens = estimate_remaining_tokens(
    messages, 
    max_context=self._max_context_tokens,
    reserved_for_output=self._reserved_for_output
)

logger.info(
    "token_check",
    total_tokens=total_tokens,
    remaining_tokens=remaining_tokens,
    max_context=self._max_context_tokens,
)

# ⚠️ 如果超限，立即返回错误
if remaining_tokens < 0:
    yield ReActStep(
        type=ReActStepType.ERROR,
        error=f"上下文 Token 超限！当前 {total_tokens} tokens，最大允许 {self._max_context_tokens - self._reserved_for_output} tokens。请清理历史记录或使用 SummaryMemory。",
        iteration=0,
    )
    return
```

---

### 2. 添加可配置的上下文窗口参数

#### 修改文件：`my_agent/config/settings.py`

```python
# --- 上下文窗口管理 ---
max_context_tokens: int = 8192       # 模型最大上下文窗口（qwen-plus: 8K, gpt-4: 8K/32K）
reserved_output_tokens: int = 1024   # 为输出预留的 Token 数
```

#### 修改文件：`.env.example`

```bash
# === 上下文窗口管理 ===
MAX_CONTEXT_TOKENS=8192     # 模型最大上下文窗口（qwen-plus: 8K, gpt-4: 8K/32K）
RESERVED_OUTPUT_TOKENS=1024 # 为输出预留的 Token 数
```

---

### 3. 更新依赖注入配置

#### 修改文件：`my_agent/core/dependencies.py`

```python
def get_react_engine() -> ReActEngine:
    """获取 ReAct 引擎单例。"""
    global _react_engine
    if _react_engine is None:
        import my_agent.domain.tool.builtin  # noqa: F401

        _react_engine = ReActEngine(
            llm=get_llm_client(),
            tool_registry=get_registry(),
            max_iterations=10,
            tool_timeout=30.0,
            max_context_tokens=settings.max_context_tokens,      # ← 从配置读取
            reserved_for_output=settings.reserved_output_tokens, # ← 从配置读取
        )
        logger.info(
            "react_engine_initialized",
            tools=get_registry().names(),
            max_context_tokens=settings.max_context_tokens,
        )
    return _react_engine
```

---

## 工作流程

### 修复前（无校验）：
```
用户: [发送很长的对话历史]
ReActEngine: 直接构建消息列表 → 发送给 LLM
LLM API: ❌ 返回错误 "context_length_exceeded"
用户: 看到错误，不知道如何解决
```

### 修复后（有校验）：
```
用户: [发送很长的对话历史]
ReActEngine: 
  1. 构建消息列表
  2. 计算 Token 数量: 9500 tokens
  3. 检查上下文窗口: 最大 8192 - 1024 = 7168 tokens
  4. 发现超限 (9500 > 7168)
  5. ✅ 立即返回友好错误提示
用户: 看到提示 "上下文 Token 超限！请清理历史记录或使用 SummaryMemory"
用户: 点击"新对话"按钮或切换到 SummaryMemory
```

---

## Token 计算逻辑

### 使用的工具函数

#### 1. `count_tokens(text: str) -> int`
精确计算单个字符串的 Token 数量（基于 tiktoken）

#### 2. `count_messages_tokens(messages: list[Message]) -> int`
计算消息列表的总 Token 数，遵循 OpenAI 计费规则：
```python
total = 3  # reply primer
for msg in messages:
    total += 4  # per-message overhead (role + separators)
    total += count_tokens(msg.content)
    # 如果有 tool_calls，也计入
return total
```

#### 3. `estimate_remaining_tokens(messages, max_context, reserved_for_output) -> int`
估算剩余可用 Token 数：
```python
used = count_messages_tokens(messages)
remaining = max_context - reserved_for_output - used
return remaining
```

---

## 配置说明

### 不同模型的上下文窗口

| 模型 | 上下文窗口 | 推荐配置 |
|------|-----------|---------|
| **qwen-plus** | 8K (8192) | `MAX_CONTEXT_TOKENS=8192` |
| **qwen-turbo** | 8K (8192) | `MAX_CONTEXT_TOKENS=8192` |
| **qwen3.5-plus** | 32K (32768) | `MAX_CONTEXT_TOKENS=32768` |
| **gpt-3.5-turbo** | 4K/16K | `MAX_CONTEXT_TOKENS=4096` 或 `16384` |
| **gpt-4** | 8K/32K | `MAX_CONTEXT_TOKENS=8192` 或 `32768` |
| **gpt-4-turbo** | 128K | `MAX_CONTEXT_TOKENS=128000` |

### 预留输出 Token 的作用

`RESERVED_OUTPUT_TOKENS` 用于为 LLM 的输出预留空间：
- **太小**：LLM 输出可能被截断
- **太大**：浪费上下文空间
- **推荐值**：
  - 简单问答：512 tokens
  - 一般对话：1024 tokens
  - 长文本生成：2048 tokens

---

## 日志输出

启用 Token 校验后，每次请求都会记录：

```json
{
  "level": "info",
  "event": "token_check",
  "total_tokens": 1234,
  "remaining_tokens": 5934,
  "max_context": 8192
}
```

可以通过日志监控 Token 使用情况，及时发现问题。

---

## 与记忆策略的配合

### BufferMemory
- **特点**：保留完整历史，Token 无限增长
- **校验作用**：在超限前及时提醒用户切换策略或清理历史
- **适用场景**：短对话（< 10 轮）

### WindowMemory
- **特点**：保留最近 K 轮，自动淘汰旧消息
- **校验作用**：双重保险，防止单轮消息过长
- **适用场景**：长对话（10-50 轮）

### SummaryMemory
- **特点**：自动压缩旧消息为摘要
- **校验作用**：兜底保护，防止摘要失败后超限
- **适用场景**：超长对话（> 50 轮）

---

## 测试验证

### 测试步骤

1. **启动服务**：
   ```bash
   python -m my_agent.main
   ```

2. **测试正常对话**（Token 未超限）：
   - 发送短消息："你好"
   - **预期**：正常返回，日志显示 Token 使用情况

3. **测试 Token 超限**（模拟长历史）：
   - 方法 1：在 `.env` 中临时设置 `MAX_CONTEXT_TOKENS=500`（故意设小）
   - 方法 2：发送超长消息（复制大段文本）
   - **预期**：立即返回错误提示，不调用 LLM API

4. **检查日志**：
   ```bash
   # 查看 Token 校验日志
   grep "token_check" logs/app.log
   ```

### 预期输出

**正常情况**：
```json
{
  "event": "token_check",
  "total_tokens": 456,
  "remaining_tokens": 6712,
  "max_context": 8192
}
```

**超限情况**：
```json
{
  "event": "token_check",
  "total_tokens": 9500,
  "remaining_tokens": -2332,
  "max_context": 8192
}
```

前端收到错误：
```
上下文 Token 超限！当前 9500 tokens，最大允许 7168 tokens。
请清理历史记录或使用 SummaryMemory。
```

---

## 修改文件清单

1. ✅ `my_agent/core/engine/react_engine.py`
   - 添加 Token 校验逻辑
   - 添加 `max_context_tokens` 和 `reserved_for_output` 参数

2. ✅ `my_agent/config/settings.py`
   - 添加 `max_context_tokens` 和 `reserved_output_tokens` 配置项

3. ✅ `my_agent/core/dependencies.py`
   - 从配置读取参数并传递给 `ReActEngine`

4. ✅ `.env.example`
   - 添加上下文窗口配置示例

5. ✅ `TOKEN_VALIDATION_FIX.md`
   - 本说明文档

---

## 后续优化建议

### 1. 动态调整历史长度
当检测到 Token 接近上限时，自动截断历史消息：
```python
if remaining_tokens < 500:  # 接近上限
    # 只保留最近 5 轮对话
    messages = messages[:1] + messages[-10:]  # system + 最近 10 条
```

### 2. 智能推荐记忆策略
根据对话轮数和 Token 使用情况，自动推荐最佳记忆策略：
```python
if total_tokens > 5000:
    logger.warning("建议切换到 SummaryMemory 以节省 Token")
```

### 3. Token 使用统计
在会话管理界面显示每个会话的 Token 消耗：
```python
GET /api/v1/sessions/{id}/stats
{
  "total_tokens": 12345,
  "estimated_cost": 0.15,  # 美元
  "message_count": 42
}
```

### 4. 成本预警
设置 Token 预算，超出时发出警告：
```python
if total_cost > budget_limit:
    send_notification("Token 预算即将用完")
```

---

## 总结

通过这次改进，实现了：

- ✅ **发送前 Token 校验**：防止超限请求浪费 API 配额
- ✅ **友好错误提示**：用户清楚知道问题原因和解决方法
- ✅ **可配置参数**：支持不同模型的上下文窗口
- ✅ **日志监控**：实时追踪 Token 使用情况
- ✅ **与记忆策略配合**：多层防护，确保系统稳定

现在 `token_count` 工具不再是"摆设"，而是真正发挥作用的核心功能！🎉

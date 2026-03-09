# Session 会话持久化修复说明

## 问题描述

在之前的版本中，前端每次发送消息都会创建一个新的 session（会话），导致无法保持对话的上下文连续性。用户每次提问都像是在开始一个全新的对话，AI 无法记住之前的对话内容。

## 问题原因

**前端代码缺陷**：
- 前端 JavaScript 在调用 `/api/v1/chat/completions` 接口时，只发送了 `message` 和 `stream` 参数
- **没有携带 `session_id` 参数**
- 导致后端每次都判断 `session_id` 为 `None`，从而创建新会话

**后端逻辑**（正常）：
```python
if session_id:
    session = await s_repo.get_by_id(session_id)
    if not session:
        session = await s_repo.create()
        session_id = session.id
else:
    session = await s_repo.create()  # ← 每次都走这里
    session_id = session.id
```

## 修复方案

### 1. 添加会话 ID 状态管理

在 `chat.js` 中添加全局变量来保存当前会话 ID：

```javascript
let currentSessionId = null; // 当前会话 ID
```

### 2. 修改非流式请求

在发送请求时携带 `session_id`，并在收到响应后保存返回的 `session_id`：

```javascript
async function nonStreamChat(message) {
    // ...
    const resp = await fetch('/api/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            message, 
            stream: false,
            session_id: currentSessionId  // ← 携带会话 ID
        }),
    });
    
    const data = await resp.json();
    
    // 保存会话 ID
    if (data.session_id) {
        currentSessionId = data.session_id;  // ← 保存返回的会话 ID
    }
    // ...
}
```

### 3. 修改流式请求

同样在流式请求中携带和保存 `session_id`：

```javascript
async function streamChat(message) {
    // ...
    const resp = await fetch('/api/v1/chat/completions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            message, 
            stream: true,
            session_id: currentSessionId  // ← 携带会话 ID
        }),
    });
    
    // 在处理 'done' 事件时保存会话 ID
    case 'done':
        if (data.session_id) {
            currentSessionId = data.session_id;  // ← 保存返回的会话 ID
        }
        // ...
}
```

### 4. 添加"新建会话"功能

在页面头部添加"新对话"按钮，允许用户主动开始新会话：

**HTML 修改** (`index.html`)：
```html
<button id="new-session-btn" 
        class="text-xs text-gray-600 hover:text-primary-600 hover:bg-primary-50 px-3 py-1.5 rounded-lg transition-colors border border-gray-200 hover:border-primary-300"
        title="开始新对话">
    ➕ 新对话
</button>
```

**JavaScript 功能** (`chat.js`)：
```javascript
newSessionBtn.addEventListener('click', () => {
    if (isStreaming) return;
    
    // 清空当前会话 ID
    currentSessionId = null;
    
    // 清空消息列表
    messagesEl.innerHTML = `...欢迎页面...`;
    
    // 清空 token 统计
    tokenUsageEl.textContent = '';
    
    // 聚焦输入框
    userInput.focus();
});
```

## 工作流程

### 修复前（每次创建新会话）：
```
用户: 你好
前端 → 后端: { message: "你好", stream: true }
后端: session_id = None → 创建新会话 A
后端 → 前端: { session_id: "A", content: "你好！" }

用户: 我刚才说了什么？
前端 → 后端: { message: "我刚才说了什么？", stream: true }  ← 没有 session_id
后端: session_id = None → 创建新会话 B  ← 又是新会话！
后端 → 前端: { session_id: "B", content: "抱歉，我没有记忆..." }
```

### 修复后（保持会话连续性）：
```
用户: 你好
前端 → 后端: { message: "你好", stream: true, session_id: null }
后端: session_id = None → 创建新会话 A
后端 → 前端: { session_id: "A", content: "你好！" }
前端: 保存 currentSessionId = "A"  ← 关键！

用户: 我刚才说了什么？
前端 → 后端: { message: "我刚才说了什么？", stream: true, session_id: "A" }  ← 携带会话 ID
后端: session_id = "A" → 使用现有会话 A，加载历史消息
后端 → 前端: { session_id: "A", content: "你刚才说了'你好'" }  ← 能记住了！
```

## 测试验证

### 测试步骤：

1. **启动服务**：
   ```bash
   python -m my_agent.main
   ```

2. **打开浏览器**访问 `http://localhost:8001`

3. **测试对话连续性**：
   - 发送第一条消息："你好，我叫张三"
   - 发送第二条消息："我叫什么名字？"
   - **预期结果**：AI 应该能回答"你叫张三"

4. **测试新建会话**：
   - 点击右上角的"➕ 新对话"按钮
   - 发送消息："我叫什么名字？"
   - **预期结果**：AI 应该回答"我不知道"或类似内容（因为是新会话）

5. **检查数据库**：
   ```bash
   # 查看 sessions 表
   sqlite3 my_agent.db "SELECT id, title, created_at FROM sessions;"
   
   # 查看 messages 表
   sqlite3 my_agent.db "SELECT session_id, role, content FROM messages ORDER BY created_at;"
   ```

## 修改文件清单

1. ✅ `my_agent/static/js/chat.js` - 前端 JavaScript 逻辑
   - 添加 `currentSessionId` 变量
   - 修改 `nonStreamChat()` 函数
   - 修改 `streamChat()` 函数
   - 添加新建会话按钮事件处理

2. ✅ `my_agent/templates/index.html` - 前端 HTML 模板
   - 添加"新对话"按钮

## 技术要点

### 会话生命周期管理
- **首次对话**：`currentSessionId = null` → 后端创建新会话 → 前端保存 session_id
- **后续对话**：前端携带 `currentSessionId` → 后端使用现有会话 → 加载历史消息
- **新建会话**：用户点击"新对话" → `currentSessionId = null` → 重新开始

### 数据持久化
- 每条用户消息都会写入 `messages` 表，关联到 `session_id`
- 每条 AI 回复也会写入 `messages` 表
- 后端从数据库加载历史消息注入到 ReAct 引擎的上下文中

### 前后端协议
```typescript
// 请求
interface ChatRequest {
  message: string;
  stream: boolean;
  session_id: string | null;  // null 表示创建新会话
}

// 响应
interface ChatResponse {
  session_id: string;  // 后端返回会话 ID
  content: string;
  usage?: TokenUsage;
}
```

## 注意事项

1. **浏览器刷新**：刷新页面后 `currentSessionId` 会丢失，下次对话会创建新会话
   - 如需持久化，可以使用 `localStorage` 保存
   
2. **多标签页**：不同浏览器标签页的 `currentSessionId` 是独立的
   - 每个标签页都有自己的会话

3. **会话过期**：目前没有会话过期机制
   - 可以考虑添加 TTL（Time To Live）

4. **并发安全**：SQLAlchemy 的 AsyncSession 保证了数据库操作的安全性

## 后续优化建议

1. **会话列表**：添加左侧边栏显示历史会话列表
2. **会话标题**：自动根据首条消息生成会话标题
3. **本地存储**：使用 `localStorage` 保存当前会话 ID，刷新页面后恢复
4. **会话管理**：支持删除、重命名会话
5. **会话搜索**：支持搜索历史会话内容

## 总结

通过这次修复，实现了：
- ✅ 对话上下文连续性（AI 能记住之前的对话）
- ✅ 会话持久化到数据库
- ✅ 用户可以主动开始新对话
- ✅ 前后端会话 ID 同步机制

现在用户可以像使用 ChatGPT 一样，进行连续的多轮对话了！

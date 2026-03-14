# Function Calling vs MCP vs 自研工具系统 深度对比

> 面试话术：
> "我实现了 MCP Server，把 Agent 的工具能力标准化暴露，Cursor 和 Claude 都能直接调用；
> 同时实现了 MCP Client，让 Agent 能动态接入社区的任何 MCP Server，不改代码就能扩展工具。
> 三种方式各有适用场景，关键是理解它们的设计目标和 trade-off。"

---

## 一、三种工具调用方式对比

```
┌─────────────────────────────────────────────────────────────────────┐
│                    工具调用三种方式                                    │
│                                                                       │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐ │
│  │ Function Calling │  │      MCP        │  │   自研工具系统       │ │
│  │  (OpenAI 专有)   │  │  (开放标准)     │  │  (Python 内部)      │ │
│  └────────┬────────┘  └────────┬────────┘  └──────────┬──────────┘ │
│           │                    │                        │             │
│    LLM API 层面         跨进程/跨网络             进程内直接调用      │
│    JSON Schema 描述     JSON-RPC 2.0              Python 接口        │
│    OpenAI 生态          任何 AI 客户端             自研 Agent         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、详细对比表

| 维度 | Function Calling | MCP | 自研工具系统 |
|------|-----------------|-----|------------|
| **标准化程度** | OpenAI 专有格式 | 开放标准（Anthropic 发布） | 自定义接口 |
| **跨平台** | 仅 OpenAI 兼容 API | 任何 MCP 客户端 | 仅自研 Agent |
| **传输方式** | HTTP（LLM API 内） | stdio / SSE / HTTP | Python 函数调用 |
| **工具发现** | 每次请求传入 Schema | tools/list 动态发现 | ToolRegistry 注册 |
| **性能开销** | 低（API 内） | 中（进程间/网络） | 极低（函数调用） |
| **安全控制** | LLM 侧参数验证 | 服务端实现 | 完全自定义 |
| **工具生态** | OpenAI 插件 | 社区 MCP Server | 自研 |
| **调试难度** | 中（需看 API 日志） | 中（JSON-RPC 可追踪） | 低（Python 断点） |
| **适用场景** | 使用 OpenAI API 时 | 跨平台工具共享 | 自研 Agent 内部 |

---

## 三、MCP 协议架构详解

### 3.1 消息流

```
MCP 客户端（Cursor/Claude/自研Agent）
    │
    │  1. initialize（协商版本+能力）
    │  2. notifications/initialized
    │  3. tools/list（发现工具）
    │  4. tools/call（调用工具）
    ▼
MCP Server（本项目实现）
    │
    │  内部调用
    ▼
自研 ToolRegistry（calculator/web_search/code_executor...）
```

### 3.2 传输层对比

| 传输方式 | 适用场景 | 通信方式 | 延迟 |
|---------|---------|---------|------|
| **stdio** | 本地工具（Cursor/Claude Desktop） | stdin/stdout | 极低 |
| **SSE** | 远程工具（Web 客户端） | HTTP 长连接 | 低 |
| **WebSocket** | 双向实时通信（规划中） | WebSocket | 低 |

### 3.3 能力协商（面试重点）

```json
// 客户端发送 initialize
{
  "protocolVersion": "2024-11-05",
  "capabilities": {"tools": {}, "resources": {}},
  "clientInfo": {"name": "Cursor", "version": "1.0"}
}

// 服务端响应
{
  "protocolVersion": "2024-11-05",
  "capabilities": {
    "tools": {},
    "resources": {"subscribe": false, "listChanged": false}
  },
  "serverInfo": {"name": "MyAgent MCP Server", "version": "1.0.0"}
}
```

---

## 四、MCP vs Function Calling 消息格式对比

### 工具定义

```json
// OpenAI Function Calling 格式
{
  "type": "function",
  "function": {
    "name": "calculator",
    "description": "计算数学表达式",
    "parameters": {
      "type": "object",
      "properties": {
        "expression": {"type": "string"}
      },
      "required": ["expression"]
    }
  }
}

// MCP 格式（tools/list 返回）
{
  "name": "calculator",
  "description": "计算数学表达式",
  "inputSchema": {
    "type": "object",
    "properties": {
      "expression": {"type": "string"}
    },
    "required": ["expression"]
  }
}
```

**区别**：MCP 用 `inputSchema`，OpenAI 用 `parameters`；MCP 无 `type: function` 包装层。

### 工具调用

```json
// OpenAI Function Calling（LLM 返回的 tool_call）
{
  "id": "call_abc123",
  "type": "function",
  "function": {
    "name": "calculator",
    "arguments": "{\"expression\": \"(123+456)*789\"}"
  }
}

// MCP tools/call 请求
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "calculator",
    "arguments": {"expression": "(123+456)*789"}
  }
}
```

**区别**：OpenAI arguments 是 JSON 字符串，MCP arguments 是对象；MCP 有标准的 JSON-RPC 包装。

---

## 五、MCP 生态（面试加分项）

### 社区 MCP Server 示例

| MCP Server | 功能 | 连接方式 |
|-----------|------|---------|
| `@modelcontextprotocol/server-filesystem` | 文件系统读写 | stdio |
| `@modelcontextprotocol/server-github` | GitHub API | stdio |
| `@modelcontextprotocol/server-postgres` | PostgreSQL 查询 | stdio |
| `@modelcontextprotocol/server-slack` | Slack 消息 | stdio |
| `mcp-server-fetch` | HTTP 请求 | stdio |

### 接入社区 MCP Server（通过 McpClient）

```python
# 动态接入 GitHub MCP Server
manager = get_mcp_manager()
await manager.connect("github", "http://localhost:3001/sse")
manager.register_all_tools()
# 现在 ReAct Agent 可以直接使用 github__search_repos 等工具
```

---

## 六、面试常见问题

**Q: MCP 和 Function Calling 有什么本质区别？**

> A: "Function Calling 是 LLM API 层面的工具调用协议，工具定义随每次 API 请求传入，
> 只适用于 OpenAI 兼容的 API。MCP 是独立的工具服务协议，工具运行在独立进程中，
> 通过 JSON-RPC 通信，任何 AI 客户端（Cursor/Claude/自研 Agent）都能调用。
> 本质区别：Function Calling 是 LLM 内置能力，MCP 是外部工具标准化接口。"

**Q: 为什么 MCP 要用 stdio 传输？**

> A: "stdio 传输是为了本地工具设计的。Cursor 或 Claude Desktop 直接启动一个 Python
> 进程作为 MCP Server，通过 stdin/stdout 通信，无需网络端口，安全且简单。
> 对比 SSE 传输：SSE 适合远程工具，需要 HTTP 服务器，但可以跨机器访问。"

**Q: MCP Resources 和 Tools 有什么区别？**

> A: "Tools 是行为（有副作用），Resources 是数据（只读）。
> 例如：web_search 是 Tool（发起网络请求），知识库文档是 Resource（只读内容）。
> 客户端可以订阅 Resource 变更通知（resources/subscribe），但不能订阅 Tool 变更。"

**Q: 如何在自研 Agent 中使用社区 MCP Server？**

> A: "通过 McpClient 连接外部 MCP Server，调用 tools/list 发现工具，
> 然后用 McpProxyTool 适配器将 MCP Tool 包装为内部 BaseTool，
> 注册到 ToolRegistry 后，ReAct Agent 可以透明调用，
> 完全不需要知道工具是本地实现还是 MCP 代理。"

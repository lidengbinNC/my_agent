# MyAgent - 智能多 Agent 任务执行平台

> 从零实现的 AI Agent 系统，覆盖 ReAct 推理、工具调用、多 Agent 协作、工作流编排等核心面试考点。与 MyRAG 形成互补，完整覆盖 AI 工程师面试知识体系。

## 项目亮点

- **底层手写 + 框架应用**：核心引擎从零实现掌握原理，同时集成 LangGraph / Dify 展示工程落地能力
- **面试导向**：每个模块标注面试考点，覆盖 26 大知识领域
- **8 种设计模式**：策略、观察者、状态机、责任链、工厂、命令、中介者、模板方法
- **三种 Agent 架构**：ReAct Agent + Plan-and-Execute Agent + Multi-Agent（三种协作模式）
- **MCP 协议实现**：实现 MCP Server + Client，掌握 AI 工具互联的标准协议（2025 最热方向）
- **框架对比**：自研实现 vs LangGraph vs Dify，能说清 trade-off 展示架构判断力
- **企业级特性**：Guardrails 安全护栏、Human-in-the-Loop、成本控制、失败重试

## 与 MyRAG 的知识互补

| 知识领域 | MyRAG 覆盖 | MyAgent 覆盖 |
|---------|-----------|-------------|
| 检索增强生成 | ✅ 核心 | ○ 作为工具集成 |
| Prompt Engineering | ○ 基础模板 | ✅ CoT / Few-shot / ReAct Prompt |
| Function Calling | ○ 未涉及 | ✅ 核心（工具调用协议） |
| Agent 推理 | ○ 未涉及 | ✅ 核心（ReAct / Plan-and-Execute） |
| 多 Agent 协作 | ○ 未涉及 | ✅ 核心（Sequential / Parallel / Hierarchical） |
| 状态机 / 工作流 | ○ Pipeline | ✅ DAG 工作流引擎 + 有限状态机 |
| 记忆系统 | ○ 语义缓存 | ✅ 短期 + 长期 + 摘要记忆 |
| 安全与对齐 | ○ 未涉及 | ✅ Guardrails 输入/输出校验 |
| 流式输出 | ✅ SSE/WS | ✅ Agent 思考过程实时流式 |
| 可观测性 | ✅ Prometheus | ✅ Token 成本追踪 + Agent Trace |
| **LangChain / LangGraph** | ○ 未涉及 | ✅ 用 LangGraph 重构工作流，对比自研实现 |
| **Dify 平台** | ○ 未涉及 | ✅ Dify 二次开发 + 自定义工具插件 |
| **MCP 协议** | ○ 未涉及 | ✅ MCP Server + Client，AI 工具生态互联标准 |
| **Structured Output** | ○ 未涉及 | ✅ LLM 结构化输出 + Pydantic 校验 + 自动重试 |
| **Agent 评估体系** | ○ 未涉及（RAG有RAGAS） | ✅ 任务完成率 / 工具准确率 / 步骤效率 |
| **LLM 可观测性（LangFuse）** | ○ Prometheus 系统指标 | ✅ LLM 调用链追踪 + Prompt 管理 + 在线评估 |
| **多模型路由** | ○ 单模型 | ✅ 智能路由 + Fallback 降级 + 成本优化 |
| **Prompt 版本管理** | ○ 静态模板 | ✅ 版本控制 + A/B 测试 + 动态加载 |
| **异步任务队列** | ○ 未涉及 | ✅ 长任务异步执行 + 状态轮询 + WebSocket 推送 |

## 技术栈

| 层级 | 技术 | 面试考点 |
|------|------|---------|
| Web 框架 | FastAPI | SSE 流式推送 Agent 思考过程 |
| Agent 推理 | 自研 ReAct Engine | Thought → Action → Observation 循环 |
| Function Calling | OpenAI Tool Call 协议 | JSON Schema 描述、参数提取、结果注入 |
| 工作流引擎 | 自研 DAG Engine | 拓扑排序、并行执行、条件分支、状态持久化 |
| 状态管理 | 有限状态机 (FSM) | Agent 生命周期、任务状态流转 |
| 记忆系统 | 对话缓冲 + 摘要 + 向量 | 滑动窗口、LLM 摘要压缩、长期记忆检索 |
| 工具系统 | 自研 Tool Registry | 动态注册、Schema 验证、沙箱执行 |
| 安全护栏 | Guardrails | 输入意图分类、输出内容审核、工具调用权限 |
| 数据库 | SQLAlchemy 2.0 + SQLite | Agent 会话持久化、任务执行记录 |
| LLM | OpenAI 兼容 API | 多模型适配、Tool Call 解析、流式 + 非流式 |
| 前端 | Jinja2 + HTMX + TailwindCSS | Agent 思考过程可视化、工作流画布 |
| LangGraph | LangGraph 0.x | StateGraph、节点编排、条件边、检查点 |
| Dify | Dify 平台 | Workflow 编排、自定义工具插件、API 集成 |
| MCP | Model Context Protocol | MCP Server/Client、Transport（stdio/SSE）、工具生态互联 |
| Structured Output | Pydantic + JSON Mode | LLM 输出结构化校验、自动重试、Schema 约束 |
| 可观测性 | LangFuse（自部署） | LLM Trace 可视化、Prompt 管理、在线评估、成本分析 |
| 模型路由 | 自研 Model Router | 复杂度评估 → 模型选择、Fallback 链、成本优化 |
| 任务队列 | asyncio.Queue + Redis(可选) | 长任务异步执行、状态轮询、结果回调 |
| 部署 | Docker Compose | 多服务编排 |

## 项目结构

```
my_agent/
├── api/                          # API 层
│   ├── routes/
│   │   ├── health.py             # 健康检查
│   │   ├── chat.py               # Agent 对话（SSE 流式思考过程）
│   │   ├── agent.py              # Agent CRUD 管理
│   │   ├── workflow.py           # 工作流管理与执行
│   │   ├── tool.py               # 工具列表与测试
│   │   ├── session.py            # 会话管理（历史、记忆）
│   │   └── mcp.py                # MCP Server 管理（连接 / 工具发现）
│   ├── schemas/                  # Pydantic 请求/响应模型
│   │   ├── chat.py               # 对话相关 Schema
│   │   ├── agent.py              # Agent 配置 Schema
│   │   ├── workflow.py           # 工作流定义 Schema
│   │   └── tool.py               # 工具相关 Schema
│   └── middleware/
│       ├── tracing.py            # 请求追踪中间件
│       └── cost_tracking.py      # Token 成本追踪中间件
│
├── config/                       # 配置管理
│   └── settings.py               # Pydantic Settings（LLM / 工具 / 安全配置）
│
├── core/                         # 核心编排层（★ 面试重点）
│   ├── engine/
│   │   ├── base.py               # Agent 引擎基类（模板方法模式）
│   │   ├── react_engine.py       # ★ ReAct 引擎（Thought-Action-Observation 循环）
│   │   ├── plan_execute_engine.py# ★ Plan-and-Execute 引擎（先规划后执行）
│   │   └── fsm.py                # ★ 有限状态机（Agent 状态流转）
│   │
│   ├── multi_agent/
│   │   ├── base.py               # 多 Agent 协调器基类
│   │   ├── sequential.py         # ★ 顺序协作（Pipeline 模式）
│   │   ├── parallel.py           # ★ 并行协作（Map-Reduce 模式）
│   │   └── hierarchical.py       # ★ 层级协作（Manager-Worker 模式）
│   │
│   ├── workflow/
│   │   ├── dag.py                # ★ DAG 工作流引擎（拓扑排序 + 并行执行）
│   │   ├── node.py               # 工作流节点定义（Agent / Tool / Condition / Human）
│   │   └── state.py              # 工作流状态管理与持久化
│   │
│   └── dependencies.py           # 依赖注入 / 组件单例管理
│
├── domain/                       # 领域层
│   ├── llm/
│   │   ├── base.py               # LLM 客户端基类
│   │   ├── openai_client.py      # OpenAI 兼容客户端（支持 Tool Call）
│   │   ├── message.py            # 消息模型（System / User / Assistant / Tool）
│   │   ├── structured_output.py  # ★ 结构化输出（JSON Mode + Pydantic 校验 + 重试）
│   │   └── model_router.py       # ★ 多模型路由（复杂度评估 → 模型选择 → Fallback）
│   │
│   ├── tool/                     # ★ 工具系统（面试必考）
│   │   ├── registry.py           # 工具注册中心（装饰器 + 自动 Schema 生成）
│   │   ├── base.py               # 工具基类（名称、描述、参数 Schema、执行）
│   │   ├── executor.py           # 工具执行器（超时、重试、沙箱）
│   │   └── builtin/              # 内置工具集
│   │       ├── web_search.py     # 网络搜索工具
│   │       ├── calculator.py     # 数学计算工具
│   │       ├── code_executor.py  # Python 代码执行工具（沙箱）
│   │       ├── file_reader.py    # 文件读取工具
│   │       ├── http_request.py   # HTTP 请求工具
│   │       ├── database_query.py # 数据库查询工具（Text-to-SQL）
│   │       └── rag_tool.py       # RAG 检索工具（集成 MyRAG）
│   │
│   ├── memory/                   # ★ 记忆系统（面试常考）
│   │   ├── base.py               # 记忆接口
│   │   ├── buffer_memory.py      # 缓冲记忆（完整对话历史）
│   │   ├── window_memory.py      # 滑动窗口记忆（最近 K 轮）
│   │   ├── summary_memory.py     # 摘要记忆（LLM 压缩历史）
│   │   └── long_term_memory.py   # 长期记忆（向量存储 + 相似检索）
│   │
│   ├── prompt/                   # ★ Prompt 工程 + 版本管理
│   │   ├── registry.py           # Prompt 注册中心（版本管理 + 动态加载）
│   │   ├── react_prompt.py       # ReAct Prompt 模板
│   │   ├── plan_prompt.py        # Planning Prompt 模板
│   │   ├── summary_prompt.py     # 记忆摘要 Prompt
│   │   └── guardrail_prompt.py   # 安全检查 Prompt
│   │
│   ├── guardrails/               # ★ 安全护栏（企业级必备）
│   │   ├── base.py               # 护栏基类（责任链模式）
│   │   ├── input_guard.py        # 输入护栏（意图分类、注入检测）
│   │   ├── output_guard.py       # 输出护栏（内容审核、格式校验）
│   │   └── tool_guard.py         # 工具调用护栏（权限检查、参数校验）
│   │
│   └── mcp/                      # ★ MCP 协议实现（2025 最热方向）
│       ├── server.py             # MCP Server（将 Agent 工具暴露为 MCP 服务）
│       ├── client.py             # MCP Client（接入外部 MCP Server 的工具）
│       ├── transport/
│       │   ├── stdio.py          # stdio 传输层（本地进程通信）
│       │   └── sse.py            # SSE 传输层（远程 HTTP 通信）
│       ├── protocol.py           # MCP 协议消息定义（JSON-RPC 2.0）
│       └── adapter.py            # MCP ↔ 内部工具系统适配器
│
├── infrastructure/               # 基础设施层
│   ├── database/
│   │   ├── models.py             # ORM 模型（Session / Message / Task / ToolCall）
│   │   ├── session_manager.py    # 数据库会话管理
│   │   └── repository.py         # Repository 模式数据访问
│   │
│   └── vector_store/             # 向量存储（长期记忆用）
│       └── faiss_store.py        # FAISS 向量存储
│
├── evaluation/                   # ★ Agent 评估框架
│   ├── metrics.py                # 评估指标（任务完成率 / 工具准确率 / 步骤效率）
│   ├── evaluator.py              # 评估执行器（单任务评估 + 批量评估）
│   ├── benchmark.py              # 基准测试数据集管理
│   └── reporter.py               # 评估报告生成
│
├── utils/                        # 工具模块
│   ├── logger.py                 # 结构化日志
│   ├── tracing.py                # Agent 执行链路追踪（Span 级）
│   ├── langfuse_client.py        # ★ LangFuse 集成（LLM 调用链上报）
│   ├── cost_tracker.py           # Token 用量 & 成本追踪
│   ├── token_counter.py          # Token 计数（tiktoken）
│   ├── retry.py                  # 重试策略（指数退避 + 抖动）
│   ├── task_queue.py             # ★ 异步任务队列（长任务提交 + 状态轮询）
│   └── sandbox.py                # 代码沙箱（subprocess 隔离执行）
│
├── templates/                    # Jinja2 前端模板
│   ├── index.html                # 主页（Agent 对话界面）
│   ├── workflow.html             # 工作流可视化画布
│   └── components/
│       ├── thinking_process.html # Agent 思考过程展示组件
│       └── tool_call_card.html   # 工具调用卡片组件
│
├── static/                       # 前端静态资源
│   ├── css/
│   │   └── main.css              # TailwindCSS 样式
│   └── js/
│       ├── chat.js               # 对话交互（SSE 接收思考过程）
│       └── workflow.js           # 工作流画布交互
│
├── langgraph_impl/               # ★ LangGraph 重构实现（框架对比）
│   ├── react_agent.py            # LangGraph 版 ReAct Agent
│   ├── plan_execute_agent.py     # LangGraph 版 Plan-and-Execute
│   ├── multi_agent.py            # LangGraph 版多 Agent 协作
│   ├── workflow.py               # LangGraph StateGraph 工作流
│   └── comparison.md             # ★ 自研 vs LangGraph 对比分析文档
│
├── dify_integration/             # ★ Dify 平台集成
│   ├── custom_tools/             # Dify 自定义工具插件
│   │   ├── rag_search_tool.py    # RAG 检索工具（对接 MyRAG）
│   │   ├── database_query_tool.py# 数据库查询工具
│   │   └── tool_schema.yaml      # 工具 Schema 定义
│   ├── workflows/                # Dify Workflow DSL 导出
│   │   ├── customer_service.yml  # 客服场景工作流
│   │   └── data_analysis.yml     # 数据分析场景工作流
│   ├── api_client.py             # Dify API 客户端封装
│   └── comparison.md             # ★ 自研 vs Dify 对比分析文档
│
├── main.py                       # 应用入口
├── pyproject.toml                # 项目配置
├── Dockerfile                    # Docker 构建
├── docker-compose.yml            # 编排配置
├── .env.example                  # 环境变量模板
└── tests/                        # 测试
    ├── unit/                     # 单元测试
    └── integration/              # 集成测试
```

## 核心面试知识点

### 1. Agent 架构演进（★★★ 必考）

```
LLM 直接调用:     User → LLM → Response（无推理能力）
Function Calling:  User → LLM → Tool Call → Tool Result → LLM → Response
ReAct Agent:       User → [Thought → Action → Observation]×N → Final Answer
Plan-and-Execute:  User → Planner(拆分子任务) → Executor(逐步执行) → Synthesizer(汇总)
Multi-Agent:       User → Coordinator → [Agent1, Agent2, ...] → Coordinator → Response
```

**面试高频问题：**
- ReAct 和 Function Calling 的区别？（ReAct 是推理框架，FC 是 LLM 能力）
- 什么场景用 ReAct vs Plan-and-Execute？（简单任务 vs 复杂多步任务）
- Agent 的停止条件有哪些？（最大步数、LLM 判断完成、超时、成本上限）

### 2. ReAct（Reasoning + Acting）推理循环（★★★ 核心实现）

```
┌─────────────────────────────────────────────────────┐
│                    ReAct Loop                        │
│                                                      │
│  User Query ──→ System Prompt + History + Tools      │
│       │                                              │
│       ▼                                              │
│  ┌─────────┐    ┌─────────┐    ┌──────────────┐    │
│  │ Thought  │───→│ Action  │───→│ Observation  │    │
│  │(LLM推理) │    │(调用工具)│    │(工具返回结果) │    │
│  └─────────┘    └─────────┘    └──────┬───────┘    │
│       ▲                               │             │
│       └───────────────────────────────┘             │
│                  (循环直到得出答案)                    │
│                       │                              │
│                       ▼                              │
│                 Final Answer                         │
└─────────────────────────────────────────────────────┘
```

**面试高频问题：**
- ReAct Prompt 如何设计？（角色 + 工具描述 + 输出格式 + Few-shot 示例）
- 如何防止 Agent 陷入死循环？（max_iterations + 重复检测 + 成本上限）
- Thought 和 Action 如何从 LLM 输出中解析？（JSON 模式 vs 正则提取）

### 3. Function Calling / Tool Use（★★★ 必考）

```
┌──────────┐    Tool Call Request        ┌──────────┐
│          │  ──────────────────────────→ │          │
│   LLM    │  {name, arguments(JSON)}     │  Tool    │
│          │  ←────────────────────────── │ Executor │
│          │    Tool Call Result           │          │
└──────────┘    {result / error}          └──────────┘

工具定义格式（OpenAI 标准）:
{
  "type": "function",
  "function": {
    "name": "web_search",
    "description": "搜索互联网获取实时信息",
    "parameters": {                        ← JSON Schema
      "type": "object",
      "properties": {
        "query": {"type": "string", "description": "搜索关键词"}
      },
      "required": ["query"]
    }
  }
}
```

**面试高频问题：**
- Function Calling 的工具描述对效果影响有多大？（非常大，是 Prompt Engineering 的一部分）
- 如何实现工具自动注册？（装饰器 + inspect 模块提取函数签名 → 自动生成 JSON Schema）
- 工具调用失败怎么处理？（重试 + 错误信息反馈给 LLM 让它修正参数）
- 如何防止工具调用注入攻击？（参数 Schema 校验 + 白名单 + 沙箱执行）

### 4. 记忆系统（★★ 常考）

```
┌──────────────────── 记忆系统架构 ────────────────────┐
│                                                       │
│  短期记忆                                             │
│  ├── BufferMemory:  [msg1, msg2, ..., msgN]  完整历史  │
│  ├── WindowMemory:  [..., msgN-K, ..., msgN]  最近K轮  │
│  └── SummaryMemory: [summary, msgN-1, msgN]  摘要+近期 │
│                                                       │
│  长期记忆                                             │
│  └── VectorMemory:  query → FAISS 相似搜索 → 相关记忆  │
│                                                       │
│  组合策略                                             │
│  └── summary(早期) + window(近期) + vector(相关)       │
└───────────────────────────────────────────────────────┘
```

**面试高频问题：**
- 对话超过 LLM 上下文窗口怎么办？（滑动窗口 / 摘要压缩 / 混合策略）
- 摘要记忆的摘要时机？（固定轮数 / Token 阈值 / 每轮增量）
- 长期记忆如何实现？（关键信息提取 → Embedding → 向量存储 → 相似检索注入）

### 5. Plan-and-Execute（★★ 复杂任务分解）

```
User: "帮我分析这份销售数据，生成可视化报告"

Planner（规划阶段）:
  Step 1: 使用 file_reader 工具读取数据文件
  Step 2: 使用 code_executor 分析数据（统计、趋势）
  Step 3: 使用 code_executor 生成可视化图表
  Step 4: 汇总分析结果，生成报告

Executor（执行阶段）:
  Step 1: ✅ 成功读取 sales_2024.csv（1000 行 x 8 列）
  Step 2: ✅ 分析完成（总销售额: ¥5.2M，同比增长 23%）
  Step 3: ✅ 生成 3 张图表（柱状图、折线图、饼图）
  Step 4: 🔄 执行中...

Replanner（动态调整）:
  若 Step 2 发现数据异常 → 插入新步骤: 数据清洗
```

**面试高频问题：**
- Plan-and-Execute 和 ReAct 的区别？（全局规划 vs 逐步推理）
- 计划执行中某步失败怎么办？（Replanning 机制，动态调整计划）
- 如何评估计划的质量？（步骤合理性、完整性、可执行性）

### 6. 多 Agent 协作模式（★★★ 高级考点）

```
(A) 顺序协作 - Sequential / Pipeline
    User → Agent1(研究) → Agent2(撰写) → Agent3(审核) → Result

(B) 并行协作 - Parallel / Map-Reduce
    User → Coordinator ─┬→ Agent1(数据分析) ──┐
                        ├→ Agent2(市场调研) ──┤→ Coordinator → Result
                        └→ Agent3(竞品分析) ──┘

(C) 层级协作 - Hierarchical / Manager-Worker
    User → Manager Agent ──→ 分配任务 ──→ Worker Agent 1
              ▲               │          → Worker Agent 2
              │               │          → Worker Agent 3
              └── 审核结果 ◄───┘
```

**面试高频问题：**
- 三种协作模式的适用场景？（流水线任务 / 独立并行任务 / 需要决策协调的任务）
- Agent 之间如何通信？（共享消息队列 / 直接消息传递 / 共享状态）
- 如何防止多 Agent 系统的级联失败？（超时 + 降级 + 隔离）

### 7. 工作流引擎 - DAG（★★ 企业级核心）

```
                    ┌─────────┐
                    │  Start  │
                    └────┬────┘
                         │
                    ┌────▼────┐
                    │ 意图识别 │
                    └────┬────┘
                    ┌────┴────┐
              ┌─────▼───┐ ┌──▼──────┐
              │ 数据查询  │ │ 文件处理 │    ← 并行执行
              └─────┬───┘ └──┬──────┘
                    └────┬────┘
                    ┌────▼────┐
                 ┌──┤ 条件判断 ├──┐
                 │  └─────────┘  │
           ┌─────▼───┐    ┌─────▼───┐
           │ 自动审批  │    │ 人工审批  │   ← 条件分支
           └─────┬───┘    └─────┬───┘
                 └────┬────────┘
                 ┌────▼────┐
                 │   End   │
                 └─────────┘

实现要点: 拓扑排序确定执行顺序 → 入度为 0 的节点并行执行 → 状态持久化支持断点恢复
```

**面试高频问题：**
- DAG 如何检测循环依赖？（拓扑排序，若排序结果节点数 < 总节点数则有环）
- 如何实现并行节点执行？（asyncio.gather / ThreadPoolExecutor）
- 工作流中断后如何恢复？（状态持久化 + 从最近成功节点重新执行）

### 8. 安全护栏 Guardrails（★★ 企业级必备）

```
User Input                                              Final Output
    │                                                        ▲
    ▼                                                        │
┌─────────────┐                                    ┌─────────────┐
│ Input Guard  │                                    │ Output Guard │
│ · 意图分类    │                                    │ · 内容审核    │
│ · Prompt注入  │─── 拦截/放行 ──→  Agent 执行  ──→  │ · 格式校验    │
│ · 话题边界    │                      │             │ · 敏感信息    │
└─────────────┘                      │             └─────────────┘
                                     ▼
                              ┌─────────────┐
                              │ Tool Guard   │
                              │ · 权限检查    │
                              │ · 参数校验    │
                              │ · 调用频率限制 │
                              └─────────────┘
```

**面试高频问题：**
- 如何检测 Prompt 注入？（意图分类模型 / 规则匹配 / LLM 检测）
- Agent 的工具调用如何做权限控制？（RBAC 角色权限 + 工具白名单 + 参数范围限制）
- 如何防止 Agent 输出敏感信息？（输出过滤 + PII 检测 + 正则脱敏）

### 9. 有限状态机 FSM（★ Agent 生命周期管理）

```
                ┌──────────┐
                │  IDLE    │ ← 初始状态
                └────┬─────┘
                     │ receive_query
                ┌────▼─────┐
          ┌─────│ THINKING │←──────────────┐
          │     └────┬─────┘               │
          │          │ decide_action        │ need_more_info
          │     ┌────▼─────┐               │
          │     │ ACTING   │───────────────┘
          │     └────┬─────┘
          │          │ action_complete
          │     ┌────▼──────────┐
          │     │ SYNTHESIZING  │
          │     └────┬──────────┘
          │          │ done
          │     ┌────▼─────┐
          │     │ FINISHED │
          │     └──────────┘
          │
          │ error (any state)
     ┌────▼─────┐
     │  ERROR   │
     └──────────┘
```

### 10. LangChain / LangGraph 框架（★★★ 面试必问）

```
LangChain 核心抽象:
  ├── LLM / ChatModel      → 模型调用层
  ├── PromptTemplate        → Prompt 管理
  ├── Chain (LCEL)          → 调用链编排（已演进为 LangChain Expression Language）
  ├── Agent                 → 推理 + 工具调用
  ├── Tool                  → 工具封装
  ├── Memory                → 记忆管理
  └── Retriever             → 检索器（对接 RAG）

LangGraph 核心概念（Agent 编排的未来方向）:
  ├── StateGraph            → 状态图定义（节点 + 边）
  ├── Node                  → 处理节点（函数 / Agent）
  ├── Conditional Edge      → 条件路由边
  ├── Checkpoint            → 状态持久化（支持断点恢复、Human-in-the-Loop）
  └── Subgraph              → 子图嵌套（多 Agent 协作）
```

```python
# LangGraph ReAct Agent 示例（面试手写级别）
from langgraph.graph import StateGraph, END

graph = StateGraph(AgentState)
graph.add_node("reason", reason_node)       # LLM 推理
graph.add_node("act", tool_node)            # 工具执行
graph.add_conditional_edges("reason", should_continue,
    {"continue": "act", "end": END})        # 条件路由
graph.add_edge("act", "reason")             # 观察结果 → 继续推理
```

**面试高频问题：**
- LangChain 和 LangGraph 的关系？（LangGraph 是 LangChain 团队出的 Agent 编排框架，用状态图替代了 AgentExecutor）
- LangGraph 的 StateGraph 和你自研的 DAG 有什么区别？（StateGraph 支持循环，DAG 不支持；LangGraph 内置 Checkpoint）
- LangChain 的 LCEL 是什么？（LangChain Expression Language，用 `|` 管道符串联组件）
- 为什么很多人说 LangChain 过度封装？（抽象层次过多、调试困难、升级频繁不稳定）
- 什么时候该用框架，什么时候该自研？（快速验证用框架；需要深度定制、性能敏感、减少依赖时自研）

### 11. Dify 平台（★★ 企业级应用）

```
Dify 在企业中的定位:

┌─────────────────────────────────────────────────────┐
│                  Dify 平台能力                        │
│                                                      │
│  1. Workflow 可视化编排                               │
│     · LLM 节点 / 知识检索节点 / 工具节点 / 条件分支    │
│     · 拖拽式编排，非技术人员也能构建 AI 应用            │
│                                                      │
│  2. Agent 模式                                       │
│     · 内置 ReAct / Function Calling                  │
│     · 可挂载自定义工具                                │
│                                                      │
│  3. 知识库管理                                        │
│     · 文档上传 → 分块 → Embedding → 检索              │
│     · 支持多种向量数据库后端                           │
│                                                      │
│  4. API 开放平台                                      │
│     · 每个应用自动生成 REST API                       │
│     · 适合作为 AI 中间层被业务系统调用                  │
│                                                      │
│  企业使用方式:                                        │
│  ├── 方式 A: 直接使用 Dify 搭建应用（运营 / 产品人员）  │
│  ├── 方式 B: 通过 Dify API 集成到现有系统（后端开发）   │
│  └── 方式 C: 二次开发（自定义工具插件 / 模型接入）      │
└─────────────────────────────────────────────────────┘
```

**面试高频问题：**
- Dify 和 LangChain 的区别？（Dify 是平台级产品，有 UI / API / 用户管理；LangChain 是开发框架）
- 企业为什么选择 Dify？（降低 AI 应用门槛、快速交付、非技术人员也能参与）
- Dify 的 Workflow 和你自研的 DAG 有什么区别？（Dify 是可视化配置驱动；自研是代码驱动，更灵活）
- 如何给 Dify 开发自定义工具？（实现 Tool 接口 + 定义 Schema + 注册到 Dify）
- Dify 的局限性？（复杂逻辑表达力有限、深度定制困难、私有化部署成本）

### 12. MCP - Model Context Protocol（★★★ 2025 最热方向，面试新高频）

```
什么是 MCP？
  Anthropic 主导的开放协议，定义了 AI 应用连接外部工具和数据源的标准方式。
  类比：USB-C 是硬件接口标准，MCP 是 AI 工具接口标准。

为什么重要？
  · Cursor、Claude Desktop、Windsurf、Cline 等主流 AI 产品已全面支持
  · 正在替代各家私有的 Function Calling 封装，成为工具互联的通用协议
  · 一次实现 MCP Server → 所有支持 MCP 的 AI 应用都能使用你的工具
```

```
MCP 协议架构:

┌──────────────┐         JSON-RPC 2.0         ┌──────────────┐
│  MCP Client  │ ◄──────────────────────────► │  MCP Server  │
│ (AI 应用侧)   │      Transport Layer         │ (工具/数据侧) │
│              │      ┌──────────────┐         │              │
│ · Cursor     │      │ stdio (本地)  │         │ · 你的工具    │
│ · Claude     │      │ SSE   (远程)  │         │ · 数据库      │
│ · 自研Agent  │      │ WebSocket    │         │ · API 服务    │
└──────────────┘      └──────────────┘         └──────────────┘

MCP 三大原语（Primitives）:

┌─────────────┬──────────────────────────────────────────────┐
│ Tools       │ 模型可调用的函数（等同于 Function Calling 的工具）│
│             │ 例：search_web(query) / query_database(sql)   │
├─────────────┼──────────────────────────────────────────────┤
│ Resources   │ 模型可读取的数据源（文件、数据库记录、API 数据）  │
│             │ 例：file://docs/readme.md / db://users/123    │
├─────────────┼──────────────────────────────────────────────┤
│ Prompts     │ 预定义的 Prompt 模板（可参数化）                │
│             │ 例：summarize(text) / translate(text, lang)   │
└─────────────┴──────────────────────────────────────────────┘
```

```python
# MCP Server 实现示例（面试手写级别）
from mcp.server import Server
from mcp.types import Tool

server = Server("my-agent-tools")

@server.tool()
async def web_search(query: str) -> str:
    """搜索互联网获取实时信息"""
    result = await do_search(query)
    return result

@server.tool()
async def rag_search(question: str, knowledge_base: str) -> str:
    """从知识库中检索相关文档并回答问题"""
    result = await call_my_rag_api(question, knowledge_base)
    return result

# 启动 Server（stdio 传输，供 Cursor/Claude 等客户端连接）
server.run(transport="stdio")
```

```
MCP 在本项目中的两个方向:

方向 A: 实现 MCP Server（把你的能力暴露出去）
  · 将 MyAgent 的工具系统包装为 MCP Server
  · 将 MyRAG 的知识库检索包装为 MCP Server
  · → 任何支持 MCP 的 AI 应用（Cursor / Claude）都能调用你的工具和知识库

方向 B: 实现 MCP Client（把外部能力接入进来）
  · MyAgent 作为 MCP Client，动态连接外部 MCP Server
  · → 不改代码就能扩展工具能力（如接入社区的 GitHub MCP、Slack MCP、数据库 MCP）

工具接入方式对比:

  之前: 每个工具硬编码 ──→ web_search.py / calculator.py / ...（N 个文件）
  现在: MCP Client 动态发现 ──→ 连接 MCP Server → 自动获取工具列表和 Schema
```

**面试高频问题：**
- MCP 和 Function Calling 的区别？（FC 是 LLM 层的能力，MCP 是应用层的互联协议；FC 定义 LLM 如何输出工具调用，MCP 定义工具如何被发现和执行）
- MCP 的传输层有哪些？各自适用场景？（stdio 用于本地进程通信，延迟低；SSE 用于远程服务，支持跨网络）
- MCP 的三大原语分别是什么？（Tools = 可调用函数，Resources = 可读取数据，Prompts = 可复用模板）
- 为什么 MCP 用 JSON-RPC 2.0 而不是 REST？（双向通信、支持通知、协议简洁、天然适合请求-响应模式）
- 如何实现 MCP 工具的动态发现？（Client 发送 tools/list 请求 → Server 返回工具列表和 JSON Schema）
- MCP Server 如何做鉴权？（Transport 层实现，如 OAuth2 / API Key / mTLS）

### 13. 自研 vs 框架 vs 平台 vs 协议 对比分析（★★★ 架构判断力，面试加分项）

```
┌─────────┬───────────────┬───────────────┬─────────────┬──────────────┐
│  维度    │    自研实现     │   LangGraph   │    Dify     │   MCP 协议    │
├─────────┼───────────────┼───────────────┼─────────────┼──────────────┤
│ 定位     │ 底层引擎       │ 编排框架       │ 应用平台     │ 互联协议      │
│ 开发效率 │ 慢（全部手写）  │ 中（框架辅助）  │ 快（拖拽配置）│ 中（协议实现）  │
│ 灵活性   │ 极高           │ 高             │ 中           │ 高（标准化）   │
│ 工具生态 │ 封闭（自己写）  │ 半开放         │ 平台内置     │ 开放（社区共享）│
│ 适用场景 │ 核心产品       │ 中等复杂度     │ 快速验证     │ 工具标准化互联  │
└─────────┴───────────────┴───────────────┴─────────────┴──────────────┘

面试中的最佳回答策略:
"我先从零实现了核心引擎，理解了底层原理（ReAct 循环、工具调用、状态管理）；
 然后用 LangGraph 重构了工作流模块，体验了框架的便利和局限；
 接着用 Dify 搭建了业务场景应用，理解了平台化产品的价值；
 最后实现了 MCP Server/Client，让我的 Agent 工具能被 Cursor、Claude 等生态复用。
 四种方式定位不同，不是互斥而是互补，我会根据场景选择最合适的方案。"
```

### 14. Token 成本控制（★ 企业级关注重点）

```
┌────────────────── 成本控制策略 ──────────────────┐
│                                                    │
│  1. Token 预算管理                                  │
│     · 单次对话 Token 上限                           │
│     · 单个 Agent 单步 Token 上限                    │
│     · 全局 Token 日/月预算                          │
│                                                    │
│  2. 成本优化                                       │
│     · 长对话自动摘要压缩（减少 Context Token）       │
│     · 工具结果截断（限制 Observation 长度）          │
│     · 短路优化（简单问题跳过复杂推理）               │
│                                                    │
│  3. 追踪与告警                                     │
│     · 每步 Token 用量记录                           │
│     · 实时成本计算（按模型定价）                     │
│     · 超预算自动终止 + 告警                         │
└────────────────────────────────────────────────────┘
```

### 15. Structured Output - 结构化输出（★★★ 生产环境基石）

```
问题：LLM 输出不可控
  Agent: "我来调用搜索工具" → 这不是合法的 JSON → 工具调用解析失败 → 整个流程崩溃

解决方案层次:

┌────────────────── Structured Output 策略 ──────────────────┐
│                                                              │
│  Level 1: JSON Mode（LLM 原生）                              │
│    · response_format: {"type": "json_object"}                │
│    · 强制 LLM 输出合法 JSON（但不保证 Schema 正确）           │
│                                                              │
│  Level 2: Pydantic 校验                                      │
│    · LLM 输出 JSON → Pydantic Model 解析校验                 │
│    · 类型错误 / 字段缺失 → 自动检测                          │
│                                                              │
│  Level 3: 自动重试 + 错误反馈                                │
│    · 校验失败 → 将错误信息连同原始输出反馈给 LLM              │
│    · LLM 根据错误修正 → 再次输出 → 再次校验                  │
│    · 最多重试 N 次                                           │
│                                                              │
│  Level 4: JSON Schema 约束（OpenAI Structured Outputs）      │
│    · 将 Pydantic Model → JSON Schema → 传给 LLM              │
│    · LLM 保证输出严格符合 Schema（最可靠）                    │
└──────────────────────────────────────────────────────────────┘
```

```python
# Structured Output 示例（面试手写级别）
from pydantic import BaseModel

class ToolCallOutput(BaseModel):
    thought: str
    tool_name: str
    tool_args: dict

async def get_structured_output(llm, messages, output_model, max_retries=3):
    for attempt in range(max_retries):
        response = await llm.chat(messages, response_format={"type": "json_object"})
        try:
            return output_model.model_validate_json(response)
        except ValidationError as e:
            messages.append({"role": "user",
                "content": f"输出格式错误: {e}。请严格按要求重新输出。"})
    raise StructuredOutputError("重试次数用尽")
```

**面试高频问题：**
- LLM 输出不符合 JSON 格式怎么办？（JSON Mode + 正则提取兜底 + 重试反馈）
- Pydantic 和 JSON Schema 在 LLM 输出校验中的作用？（运行时类型校验 + 约束传递给 LLM）
- Structured Output 的重试策略？（错误信息反馈 LLM + 递减温度 + 最大重试次数）
- OpenAI 的 Structured Outputs 和 JSON Mode 有什么区别？（Structured Outputs 用 Schema 约束解码过程，100% 合规；JSON Mode 只保证合法 JSON）

### 16. Agent 评估体系（★★★ 面试必问 - MyRAG 有评估，Agent 不能没有）

```
Agent 评估维度（类比 MyRAG 的 RAGAS）:

┌─────────────────── Agent 评估指标体系 ───────────────────┐
│                                                            │
│  1. 任务完成率 (Task Completion Rate)                      │
│     · 给定任务是否最终完成？（Pass / Fail）                 │
│     · 完成的质量如何？（LLM-as-Judge 评分 1-5）            │
│                                                            │
│  2. 工具选择准确率 (Tool Selection Accuracy)               │
│     · 是否选择了正确的工具？                               │
│     · 是否存在不必要的工具调用？                           │
│                                                            │
│  3. 步骤效率 (Step Efficiency)                             │
│     · 完成任务的实际步数 vs 最优步数                       │
│     · 是否有重复或无效的推理循环？                         │
│                                                            │
│  4. Token 效率 (Token Efficiency)                          │
│     · 完成任务消耗的 Token 数                              │
│     · 相同任务不同策略的 Token 消耗对比                     │
│                                                            │
│  5. 回答质量 (Answer Quality)                              │
│     · LLM-as-Judge: 让另一个 LLM 评价回答质量              │
│     · 忠实度: 回答是否基于工具返回的真实数据                 │
│                                                            │
│  评估方式:                                                  │
│  ├── 单元评估: 单个任务 → Agent 执行 → 评分                │
│  ├── 批量评估: 评估数据集（50+ 任务） → 统计指标            │
│  └── 对比评估: ReAct vs Plan-and-Execute 在同一数据集上对比  │
└────────────────────────────────────────────────────────────┘
```

**面试高频问题：**
- 如何评估一个 Agent 的好坏？（任务完成率 + 工具准确率 + 步骤效率 + Token 消耗）
- LLM-as-Judge 是什么？（用一个 LLM 评估另一个 LLM 的输出质量）
- Agent 评估和 RAG 评估有什么区别？（RAG 评估关注检索质量和回答忠实度；Agent 评估还要关注推理步骤和工具使用）
- 如何构建 Agent 评估数据集？（人工标注任务 + 预期步骤 + 参考答案）

### 17. LLM 可观测性 - LangFuse（★★★ 企业线上必备）

```
Prometheus vs LangFuse - 两层可观测性:

┌──────────────────────────────────────────────────────────┐
│  Prometheus（系统层）         │  LangFuse（LLM 应用层）     │
│  · QPS / 延迟 / 错误率       │  · 每次 LLM 调用的完整 Trace │
│  · CPU / 内存 / 磁盘         │  · Input Prompt + Output    │
│  · HTTP 状态码分布            │  · Token 消耗 + 成本        │
│  · 适合 DevOps 监控           │  · Prompt 版本管理          │
│                               │  · 在线评估（质量评分）      │
│                               │  · 适合 AI 工程师调试优化    │
└──────────────────────────────────────────────────────────┘

LangFuse 在 Agent 系统中的价值:

  Agent 对话请求
       │
       ▼
  ┌─── Trace ──────────────────────────────────────┐
  │  Span: ReAct Loop                               │
  │  ├── Generation: LLM Call #1 (Thinking)         │
  │  │   · model: qwen-plus                         │
  │  │   · input_tokens: 1200, output_tokens: 150   │
  │  │   · latency: 2.3s, cost: ¥0.003              │
  │  ├── Span: Tool Call (web_search)               │
  │  │   · input: {"query": "..."}                  │
  │  │   · output: "..."                            │
  │  │   · latency: 1.1s                            │
  │  ├── Generation: LLM Call #2 (Reasoning)        │
  │  │   · input_tokens: 2100, output_tokens: 300   │
  │  └── Generation: LLM Call #3 (Final Answer)     │
  │      · total_cost: ¥0.012                       │
  └─────────────────────────────────────────────────┘
```

**面试高频问题：**
- 为什么需要 LangFuse，Prometheus 不够吗？（Prometheus 看不到 Prompt 内容和 LLM 调用细节）
- LangFuse 和 LangSmith 的区别？（LangFuse 开源可自部署，LangSmith 是 LangChain 的商业 SaaS）
- 如何用 LangFuse 做 Prompt 管理？（Prompt 版本化存储 → 代码动态拉取 → A/B 测试 → 效果对比）
- 线上如何快速定位 Agent 问题？（LangFuse Trace → 找到具体哪步 LLM 调用出错 → 查看 Input/Output）

### 18. 多模型路由 Model Router（★★ 企业成本优化核心）

```
┌────────────────── 多模型路由架构 ──────────────────┐
│                                                      │
│  User Query → 复杂度评估器 → 路由决策                │
│                                                      │
│  ┌──────────────┐                                    │
│  │ 简单问题       │ → qwen-turbo   (¥0.001/次)       │
│  │ "今天星期几"    │   快、便宜、够用                  │
│  ├──────────────┤                                    │
│  │ 中等问题       │ → qwen-plus    (¥0.004/次)       │
│  │ "分析这段代码"  │   均衡                            │
│  ├──────────────┤                                    │
│  │ 复杂推理       │ → gpt-4o       (¥0.03/次)        │
│  │ "多步骤数据分析"│   强推理能力                      │
│  └──────────────┘                                    │
│                                                      │
│  Fallback 降级链:                                    │
│  gpt-4o(超时) → qwen-plus(重试) → qwen-turbo(兜底)  │
│                                                      │
│  复杂度评估方法:                                     │
│  ├── 规则匹配: 关键词/问题长度/工具数量              │
│  ├── 分类模型: 轻量 LLM 先做分类                     │
│  └── 历史统计: 相似问题历史用量参考                    │
└──────────────────────────────────────────────────────┘
```

**面试高频问题：**
- 多模型路由有哪些策略？（规则路由 / LLM 分类路由 / 成本优先路由）
- Fallback 降级怎么做？（主模型超时 → 备选模型 → 缓存兜底，类似服务降级）
- 模型路由如何评估效果？（对比路由前后的成本 + 任务完成率）

### 19. Prompt 版本管理（★★ 企业 Prompt 治理）

```
┌────────────────── Prompt 生命周期 ──────────────────┐
│                                                       │
│  开发 → 测试 → 灰度 → 线上 → 迭代                    │
│                                                       │
│  Prompt Registry:                                     │
│  ┌─────────┬─────────┬────────┬───────────────┐      │
│  │ name     │ version │ status │ metrics        │      │
│  ├─────────┼─────────┼────────┼───────────────┤      │
│  │ react_v1 │ 1.0     │ stable │ 完成率: 85%    │      │
│  │ react_v2 │ 2.0     │ canary │ 完成率: 91%    │      │
│  │ react_v3 │ 3.0     │ draft  │ 测试中         │      │
│  └─────────┴─────────┴────────┴───────────────┘      │
│                                                       │
│  A/B 测试: 90% 流量 → v1，10% 流量 → v2              │
│  效果对比 → v2 更好 → 全量切换                        │
│                                                       │
│  动态加载: 代码中不硬编码 Prompt                      │
│  prompt = await registry.get("react", version="latest")│
└───────────────────────────────────────────────────────┘
```

**面试高频问题：**
- 为什么 Prompt 需要版本管理？（Prompt 是 AI 系统的核心逻辑，改动频繁，需要可追溯可回滚）
- Prompt A/B 测试怎么做？（流量分组 + 在线评估 + 指标对比）
- Prompt 在代码中如何管理？（不硬编码，走注册中心动态加载，类似配置中心）

### 20. 异步任务队列（★★ 生产架构必备）

```
问题：Agent 执行可能要 30 秒 ~ 5 分钟，HTTP 请求会超时

┌────────────────── 异步任务架构 ──────────────────────┐
│                                                        │
│  同步模式（当前）:                                      │
│  Client ──HTTP──→ Agent 执行(30s+) ──→ Response       │
│                   ↑ 超时风险                            │
│                                                        │
│  异步模式（企业级）:                                    │
│  Client ──POST──→ 提交任务 → 返回 task_id（即时响应）   │
│  Client ──GET───→ 轮询状态 → {status: "running"}       │
│  Client ──GET───→ 轮询状态 → {status: "completed", result}│
│                                                        │
│  或 WebSocket 推送:                                     │
│  Client ──WS──→ 订阅 task_id → 实时接收中间结果         │
│                                                        │
│  实现:                                                  │
│  ┌─────┐    ┌───────────┐    ┌────────┐               │
│  │ API  │───→│ TaskQueue │───→│ Worker │               │
│  │      │    │ (asyncio  │    │(Agent  │               │
│  │      │◄───│  Queue /  │◄───│Execute)│               │
│  │      │    │  Redis)   │    │        │               │
│  └─────┘    └───────────┘    └────────┘               │
│                                                        │
│  任务状态: PENDING → RUNNING → COMPLETED / FAILED      │
└────────────────────────────────────────────────────────┘
```

**面试高频问题：**
- Agent 执行时间很长，API 怎么设计？（异步任务模式：提交 → 轮询/推送 → 获取结果）
- 任务队列用什么实现？（简单场景 asyncio.Queue；高可用场景 Redis + Celery / ARQ）
- 如何实现任务的断点续传？（任务状态持久化 + 工作流 Checkpoint）

## API 接口

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | /api/v1/health | 健康检查 |
| POST | /api/v1/chat/completions | Agent 对话（SSE 流式思考过程） |
| GET | /api/v1/agents | 获取 Agent 列表 |
| POST | /api/v1/agents | 创建自定义 Agent（指定工具、Prompt、模式） |
| GET | /api/v1/tools | 获取可用工具列表 |
| POST | /api/v1/tools/{name}/test | 测试工具调用 |
| GET | /api/v1/sessions | 获取会话列表 |
| GET | /api/v1/sessions/{id}/messages | 获取会话历史 |
| POST | /api/v1/workflows | 创建工作流定义 |
| POST | /api/v1/workflows/{id}/run | 执行工作流 |
| GET | /api/v1/workflows/{id}/status | 获取工作流执行状态 |
| POST | /api/v1/workflows/{id}/approve | Human-in-the-Loop 审批 |
| GET | /api/v1/mcp/servers | 获取已连接的 MCP Server 列表 |
| POST | /api/v1/mcp/servers | 添加外部 MCP Server 连接 |
| GET | /api/v1/mcp/tools | 获取所有 MCP 工具（本地 + 远程） |
| POST | /api/v1/tasks | 提交异步 Agent 任务（立即返回 task_id） |
| GET | /api/v1/tasks/{id} | 轮询任务状态和结果 |
| POST | /api/v1/evaluations | 运行 Agent 评估 |
| GET | /api/v1/evaluations/{id}/report | 获取评估报告 |
| GET | /api/v1/prompts | Prompt 版本列表 |
| PUT | /api/v1/prompts/{name} | 更新 Prompt 版本 |
| - | stdio / SSE | MCP Server 端点（供 Cursor / Claude 连接） |

## 前端功能

### 1. Agent 对话界面
- 实时展示 Agent 思考过程（Thought / Action / Observation 折叠展示）
- 工具调用卡片（输入参数 + 执行结果 + 耗时）
- Token 用量实时统计
- 多 Agent 标识（不同 Agent 头像和颜色区分）

### 2. 工作流可视化
- DAG 图形化展示（节点 + 边）
- 节点实时状态更新（待执行 / 执行中 / 成功 / 失败）
- Human-in-the-Loop 审批弹窗

---

## 开发路线（分阶段执行）

### Phase 1: 项目骨架 + LLM 基础 （预计 2 天）

**目标**：搭建项目结构，实现 LLM 客户端和基础对话能力

**实现内容**：
- [ ] 项目结构搭建（目录、配置、入口）
- [ ] Pydantic Settings 配置管理
- [ ] OpenAI 兼容 LLM 客户端（支持 Tool Call 协议解析）
- [ ] 消息模型定义（System / User / Assistant / Tool Message）
- [ ] **Structured Output**（JSON Mode + Pydantic 校验 + 自动重试）
- [ ] **多模型路由 Model Router**（复杂度评估 + 模型选择 + Fallback 降级链）
- [ ] FastAPI 基础路由（health / chat）
- [ ] SSE 流式输出 Agent 思考过程
- [ ] 基础前端对话界面（Jinja2 + TailwindCSS）
- [ ] 结构化日志

**面试考点**：OpenAI API 协议、Function Calling 消息格式、SSE 实现、Structured Output、Model Router

---

### Phase 2: 工具系统 + ReAct Agent（预计 3 天）

**目标**：实现工具注册框架和 ReAct 推理引擎

**实现内容**：
- [ ] 工具基类（名称、描述、参数 JSON Schema、execute 方法）
- [ ] 工具注册中心（装饰器注册 + 自动 Schema 生成）
- [ ] 工具执行器（超时控制、错误捕获、结果格式化）
- [ ] 内置工具：web_search、calculator、code_executor、http_request
- [ ] ReAct 引擎核心循环（Thought → Action → Observation → ... → Final Answer）
- [ ] ReAct Prompt 模板（角色 + 工具描述 + 输出格式约束 + Few-shot）
- [ ] **Prompt 版本管理**（Prompt Registry + 版本控制 + 动态加载）
- [ ] 停止条件（max_iterations / LLM final_answer / 超时）
- [ ] 前端：思考过程展示（折叠式 Thought/Action/Observation 卡片）

**面试考点**：ReAct 论文原理、装饰器原理、JSON Schema、Prompt Engineering、Prompt 版本管理

---

### Phase 3: 记忆系统 + 会话持久化（预计 2 天）

**目标**：实现多层记忆系统和会话数据持久化

**实现内容**：
- [ ] 记忆接口定义
- [ ] BufferMemory（完整对话历史）
- [ ] WindowMemory（滑动窗口，最近 K 轮）
- [ ] SummaryMemory（LLM 摘要压缩 + 近期消息）
- [ ] SQLAlchemy ORM 模型（Session / Message / ToolCall）
- [ ] Repository 模式数据访问
- [ ] 会话管理 API（创建 / 列表 / 历史 / 删除）
- [ ] Token 计数集成（tiktoken）

**面试考点**：上下文窗口管理、对话历史压缩策略、ORM 异步操作

---

### Phase 4: Plan-and-Execute Agent + 有限状态机（预计 2 天）

**目标**：实现 Plan-and-Execute 推理模式和 Agent 状态管理

**实现内容**：
- [ ] Planner（LLM 生成结构化执行计划）
- [ ] Executor（逐步执行计划，每步可调用 ReAct）
- [ ] Replanner（执行中动态调整计划）
- [ ] 有限状态机 FSM（IDLE → THINKING → ACTING → SYNTHESIZING → FINISHED）
- [ ] 状态转换事件触发
- [ ] Agent 工厂（根据配置创建不同类型 Agent）
- [ ] Agent CRUD API
- [ ] 前端：计划步骤展示（步骤列表 + 实时进度）

**面试考点**：任务分解策略、状态机设计模式、策略模式

---

### Phase 5: 多 Agent 协作（预计 3 天）

**目标**：实现三种多 Agent 协作模式

**实现内容**：
- [ ] Agent 协调器基类（中介者模式）
- [ ] Sequential 顺序协作（Pipeline：A → B → C）
- [ ] Parallel 并行协作（Map-Reduce：并行执行 → 汇总）
- [ ] Hierarchical 层级协作（Manager 分配 → Workers 执行 → Manager 审核）
- [ ] Agent 间消息传递协议
- [ ] 预置多 Agent 场景：
  - 研究报告生成（Researcher → Writer → Reviewer）
  - 数据分析（数据 Agent + 可视化 Agent + 报告 Agent）
- [ ] 前端：多 Agent 对话展示（不同 Agent 颜色头像区分）

**面试考点**：多 Agent 通信机制、并发控制、中介者模式

---

### Phase 6: 工作流引擎 DAG（预计 3 天）

**目标**：实现可视化的 DAG 工作流引擎

**实现内容**：
- [ ] DAG 定义模型（Node + Edge + Condition）
- [ ] 拓扑排序 + 循环依赖检测
- [ ] 并行节点执行（asyncio.gather）
- [ ] 节点类型：AgentNode / ToolNode / ConditionNode / HumanNode
- [ ] 条件分支路由
- [ ] Human-in-the-Loop（暂停等待人工审批）
- [ ] 工作流状态持久化（支持断点恢复）
- [ ] 工作流 CRUD API + 执行 API
- [ ] 前端：工作流 DAG 可视化（节点拖拽 + 状态实时更新）

**面试考点**：拓扑排序算法、DAG 并行调度、状态持久化

---

### Phase 7: 安全护栏 + 成本控制 + 可观测性（预计 3 天）

**目标**：实现企业级安全防护、成本管理和 LLM 可观测性

**实现内容**：
- [ ] Input Guard（Prompt 注入检测 + 话题边界检查）
- [ ] Output Guard（内容审核 + PII 脱敏）
- [ ] Tool Guard（工具调用权限 + 参数范围校验 + 频率限制）
- [ ] 护栏责任链（多个 Guard 串联执行）
- [ ] Token 成本追踪（按模型计价 + 按对话/Agent/全局统计）
- [ ] Token 预算管理（单次上限 + 日预算 + 超限自动终止）
- [ ] **LangFuse 集成**（Docker 自部署 + LLM 调用链上报 + Trace 可视化）
- [ ] **LangFuse Prompt 管理**（在线管理 Prompt 版本 + A/B 测试 + 效果对比）
- [ ] Agent 执行链路追踪（Span 级 Trace → 上报 LangFuse）
- [ ] 前端：Token 用量实时展示

**面试考点**：Prompt 注入防御、责任链模式、LangFuse 可观测性、Prompt A/B 测试

---

### Phase 8: Agent 评估 + 异步任务队列（预计 2 天）

**目标**：建立 Agent 质量评估体系，实现生产级异步任务架构

**实现内容**：
- [ ] Agent 评估指标定义（任务完成率 / 工具准确率 / 步骤效率 / Token 效率）
- [ ] 评估执行器（单任务评估 + 批量评估）
- [ ] LLM-as-Judge（用 LLM 评估 Agent 回答质量）
- [ ] 基准测试数据集（20+ 评估任务，覆盖不同复杂度）
- [ ] 对比评估（ReAct vs Plan-and-Execute 在同一数据集上的表现对比）
- [ ] 评估报告生成（JSON + 可视化）
- [ ] **异步任务队列**（asyncio.Queue 实现）
- [ ] 任务 API（POST 提交 → GET 轮询状态 → 获取结果）
- [ ] 任务状态持久化（PENDING → RUNNING → COMPLETED / FAILED）
- [ ] WebSocket 任务进度推送

**面试考点**：Agent 评估方法论、LLM-as-Judge、异步任务模式、任务状态管理

---

### Phase 9: LangGraph 重构对比（预计 3 天）

**目标**：用 LangGraph 重新实现核心模块，形成"自研 vs 框架"的深度对比

**实现内容**：
- [ ] LangGraph 环境搭建（langgraph + langchain-core + langchain-openai）
- [ ] LangGraph 版 ReAct Agent（StateGraph + 条件边 + 工具节点）
- [ ] LangGraph 版 Plan-and-Execute（Planner Graph + Executor Graph）
- [ ] LangGraph 版多 Agent 协作（Subgraph 嵌套）
- [ ] LangGraph Checkpoint（状态持久化 + Human-in-the-Loop）
- [ ] 撰写对比分析文档：自研 vs LangGraph
  - 代码量对比（自研 ~500 行 vs LangGraph ~80 行）
  - 灵活性对比（自研可深度定制 vs LangGraph 受限于 StateGraph 范式）
  - 调试体验对比（自研全透明 vs LangGraph 需要 LangSmith）
  - 性能对比（自研无依赖开销 vs LangGraph 有框架开销）

**面试考点**：LangGraph StateGraph、Checkpoint 机制、框架 vs 自研 trade-off

**面试话术**：
> "我先从零实现理解了 Agent 底层原理，再用 LangGraph 重构后，发现框架在工作流编排和状态持久化上确实更便捷，但在工具系统定制和错误处理细粒度控制上，自研更灵活。"

---

### Phase 10: Dify 集成与二次开发（预计 2 天）

**目标**：掌握 Dify 平台使用和二次开发能力，展示企业级 AI 应用交付能力

**实现内容**：
- [ ] Docker 部署 Dify 平台（docker-compose）
- [ ] 在 Dify 上搭建 Agent 应用（配置工具 + Prompt + 模型）
- [ ] 在 Dify 上搭建 Workflow 应用（客服场景 / 数据分析场景）
- [ ] 开发 Dify 自定义工具插件
  - RAG 检索工具（对接 MyRAG API）
  - 数据库查询工具（Text-to-SQL）
- [ ] Dify API 客户端封装（从外部系统调用 Dify 应用）
- [ ] 撰写对比分析文档：自研 vs Dify
  - 开发效率：Dify 拖拽 10 分钟 vs 自研 2 天
  - 灵活性：Dify 受限于 UI 节点类型 vs 自研可任意编码
  - 适用场景：Dify 适合快速验证和内部工具 vs 自研适合核心产品

**面试考点**：Dify 平台架构、自定义工具开发、平台化 vs 自研的选型

**面试话术**：
> "企业里不是所有场景都需要从零开发，简单场景用 Dify 快速交付，复杂场景用自研引擎深度定制，我两种方式都有实践经验。"

---

### Phase 11: MCP 协议实现（预计 3 天）

**目标**：实现 MCP Server 和 Client，让 Agent 工具融入 AI 开放生态

**实现内容**：
- [ ] MCP 协议消息定义（基于 JSON-RPC 2.0）
- [ ] MCP Server 实现
  - 将 MyAgent 内置工具（web_search / calculator / code_executor 等）暴露为 MCP Tools
  - 将 MyRAG 知识库检索暴露为 MCP Resources + Tools
  - 支持 tools/list、tools/call、resources/list、resources/read
- [ ] Transport 传输层
  - stdio 传输（本地进程通信，供 Cursor / Claude Desktop 连接）
  - SSE 传输（远程 HTTP 通信，供 Web 客户端连接）
- [ ] MCP Client 实现
  - 动态连接外部 MCP Server
  - 自动发现工具（tools/list）→ 转换为内部 Tool 对象
  - 工具调用代理（Agent 调用工具 → MCP Client → 外部 MCP Server）
- [ ] MCP ↔ 内部工具系统适配器（双向转换：MCP Tool ↔ 内部 BaseTool）
- [ ] 集成测试：用 Cursor / Claude Desktop 连接你的 MCP Server 调用工具
- [ ] 撰写对比文档：Function Calling vs MCP vs 自研工具系统

**面试考点**：MCP 协议架构、JSON-RPC 2.0、传输层设计、工具动态发现

**面试话术**：
> "我实现了 MCP Server，把 Agent 的工具能力标准化暴露，Cursor 和 Claude 都能直接调用；同时实现了 MCP Client，让 Agent 能动态接入社区的任何 MCP Server，不改代码就能扩展工具。"

---

### Phase 12: 长期记忆 + RAG 集成 + Docker 部署（预计 2 天）

**目标**：完善高级特性，全栈容器化部署

**实现内容**：
- [ ] LongTermMemory（向量存储 + 关键信息提取 + 相似检索）
- [ ] RAG Tool（调用 MyRAG 的知识库检索能力）
- [ ] 代码沙箱（subprocess 隔离 + 超时 + 资源限制）
- [ ] Dockerfile（多阶段构建）
- [ ] docker-compose.yml（MyAgent + MyRAG + Dify + LangFuse 多服务联动）
- [ ] .env.example 完善
- [ ] 最终前端优化

**面试考点**：Agent + RAG 集成、代码安全执行、Docker 多服务编排

---

## 面试知识点清单（共 26 大类）

| # | 知识领域 | 对应模块 | Phase |
|---|---------|---------|-------|
| 1 | LLM API 调用与 Tool Call 协议 | domain/llm | P1 |
| 2 | SSE 流式推送 | api/routes/chat | P1 |
| 3 | **Structured Output（结构化输出）** | **domain/llm/structured_output** | **P1** |
| 4 | **多模型路由 Model Router** | **domain/llm/model_router** | **P1** |
| 5 | Prompt Engineering（CoT / Few-shot / ReAct） | domain/prompt | P2 |
| 6 | Function Calling / Tool Use | domain/tool | P2 |
| 7 | ReAct 推理框架 | core/engine/react | P2 |
| 8 | 装饰器 + 反射（自动 Schema 生成） | domain/tool/registry | P2 |
| 9 | **Prompt 版本管理** | **domain/prompt/registry** | **P2** |
| 10 | 记忆系统（Buffer / Window / Summary） | domain/memory | P3 |
| 11 | Token 管理与上下文窗口 | utils/token_counter | P3 |
| 12 | Plan-and-Execute + Replanning | core/engine/plan_execute | P4 |
| 13 | 有限状态机 | core/engine/fsm | P4 |
| 14 | 多 Agent 协作模式 | core/multi_agent | P5 |
| 15 | 并发编程（asyncio） | core/multi_agent/parallel | P5 |
| 16 | DAG 工作流引擎 | core/workflow | P6 |
| 17 | Guardrails 安全护栏 | domain/guardrails | P7 |
| 18 | 成本控制与可观测性 | utils/cost_tracker | P7 |
| 19 | **LLM 可观测性（LangFuse）** | **utils/langfuse_client** | **P7** |
| 20 | **Agent 评估体系** | **evaluation/** | **P8** |
| 21 | **异步任务队列** | **utils/task_queue** | **P8** |
| 22 | LangGraph 状态图编排 | langgraph_impl | P9 |
| 23 | Dify 平台应用 & 二次开发 | dify_integration | P10 |
| 24 | MCP 协议（Server + Client） | domain/mcp | P11 |
| 25 | MCP Transport 与工具动态发现 | domain/mcp/transport | P11 |
| 26 | 自研 vs 框架 vs 平台 vs 协议 选型 | comparison.md | P9-P11 |

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入 LLM API Key
```

支持的 LLM 服务（需支持 Function Calling）：

| 服务 | LLM_BASE_URL | LLM_MODEL |
|------|-------------|-----------|
| 通义千问 | https://dashscope.aliyuncs.com/compatible-mode/v1 | qwen-plus |
| DeepSeek | https://api.deepseek.com/v1 | deepseek-chat |
| OpenAI | https://api.openai.com/v1 | gpt-4o-mini |

### 3. 启动服务

```bash
uvicorn my_agent.main:app --reload --host 0.0.0.0 --port 8001
```

### 4. 访问

- 前端：http://localhost:8001
- API 文档：http://localhost:8001/api/docs

### Docker 部署（多服务联动）

```bash
docker-compose up -d
```

- MyAgent：http://localhost:8001
- MyRAG：http://localhost:8000
- Dify：http://localhost:3000
- LangFuse：http://localhost:4000

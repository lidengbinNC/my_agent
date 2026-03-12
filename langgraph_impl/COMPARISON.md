# 自研 Agent 引擎 vs LangGraph 深度对比分析

> 面试话术：
> "我先从零实现理解了 Agent 底层原理，再用 LangGraph 重构后，发现框架在工作流编排和
> 状态持久化上确实更便捷，但在工具系统定制和错误处理细粒度控制上，自研更灵活。
> 选型时要根据项目阶段和团队能力做 trade-off。"

---

## 一、代码量对比

| 模块 | 自研实现 | LangGraph 实现 | 节省比例 |
|------|---------|---------------|---------|
| ReAct Agent | `core/engine/react_engine.py` ~260 行 | `langgraph_impl/react_agent.py` ~120 行 | **54%** |
| Plan-and-Execute | `core/engine/plan_execute_engine.py` ~200 行 + 辅助类 | `langgraph_impl/plan_execute.py` ~180 行 | **~30%** |
| 多 Agent 协作 | `core/multi_agent/` ~400 行（3个协调器） | `langgraph_impl/multi_agent.py` ~200 行 | **50%** |
| Human-in-the-Loop | `core/workflow/node_executors.py` ~80 行 | `langgraph_impl/checkpoint_demo.py` ~40 行 | **50%** |
| 状态持久化 | `infrastructure/db/` ~200 行（ORM+Repository） | MemorySaver/SqliteSaver 内置 | **~100%** |

---

## 二、核心特性对比

### 2.1 工具系统

| 维度 | 自研 | LangGraph |
|------|------|-----------|
| 工具定义 | `@tool()` 装饰器 + 自动 JSON Schema | `@lc_tool()` 装饰器 |
| 工具绑定 | 手动拼接 OpenAI tool 格式 | `llm.bind_tools()` 一行 |
| 工具执行 | `ToolExecutor`（超时/安全检查/重试） | `ToolNode`（自动分派，无内置安全检查） |
| 安全防护 | 内置 SSRF 防护、代码沙箱、参数验证 | 需自行实现 |
| **结论** | **安全性更高，定制灵活** | **开发更快，但需补充安全层** |

### 2.2 状态管理

| 维度 | 自研 | LangGraph |
|------|------|-----------|
| 状态定义 | 手动维护 `messages: list` | `TypedDict` + `Annotated[list, add_messages]` |
| 状态更新 | 手动 append/replace | Reducer 函数自动合并 |
| 状态持久化 | SQLAlchemy ORM + Repository | `MemorySaver` / `SqliteSaver` 内置 |
| 多会话隔离 | `session_id` 手动管理 | `thread_id` 自动隔离 |
| 状态回溯 | 需自行实现版本管理 | `checkpoint_id` 内置时间旅行 |
| **结论** | **更灵活，可定制存储后端** | **开箱即用，时间旅行是独特优势** |

### 2.3 流程控制

| 维度 | 自研 | LangGraph |
|------|------|-----------|
| 循环控制 | `while loop + max_iterations` | 条件边 `should_continue()` |
| 分支路由 | `if/elif` 手动判断 | `add_conditional_edges()` 声明式 |
| 并行执行 | `asyncio.gather()` | `Send API` |
| 流程可视化 | 无（需自行实现） | `graph.get_graph().draw_mermaid()` |
| **结论** | **逻辑透明，易于调试** | **声明式更清晰，有可视化** |

### 2.4 Human-in-the-Loop

| 维度 | 自研 | LangGraph |
|------|------|-----------|
| 暂停机制 | `asyncio.Event` + `human_token` | `interrupt_before/after` 声明式 |
| 恢复机制 | `POST /workflow/{id}/approve` | `graph.invoke(None, config)` |
| 状态修改 | 通过 API 传入 `approved` 字段 | `graph.update_state()` |
| **结论** | **更适合 Web API 场景** | **更简洁，适合脚本/批处理场景** |

### 2.5 可观测性

| 维度 | 自研 | LangGraph |
|------|------|-----------|
| 日志 | `structlog` 结构化日志 | 无内置 |
| 追踪 | LangFuse 集成 | LangSmith 原生集成 |
| 成本追踪 | `CostTracker` 自研 | LangSmith 自动统计 |
| 调试 | 完全透明，可断点调试 | 需要 LangSmith UI |
| **结论** | **自研可选择追踪工具** | **LangSmith 体验更好但有锁定风险** |

---

## 三、适用场景选型

### 选择自研引擎的场景

```
✅ 需要深度定制工具安全检查（金融/医疗等合规场景）
✅ 需要精细控制 Token 成本（成本敏感型产品）
✅ 需要与现有系统深度集成（自定义存储/认证/监控）
✅ 团队需要完全理解 Agent 底层原理（技术积累）
✅ 不希望引入框架依赖（减少升级风险）
```

### 选择 LangGraph 的场景

```
✅ 快速原型验证（节省 50%+ 代码量）
✅ 需要复杂工作流编排（条件分支/并行/循环）
✅ 需要开箱即用的状态持久化
✅ 团队熟悉 LangChain 生态
✅ 需要时间旅行/状态回溯功能
```

---

## 四、性能对比

| 指标 | 自研 | LangGraph |
|------|------|-----------|
| 启动开销 | 极低（无框架初始化） | 较低（StateGraph 编译） |
| 运行时开销 | 极低 | 低（有状态序列化开销） |
| 内存占用 | 低 | 中（Checkpoint 存储） |
| 依赖包数量 | ~10 个 | ~30 个（langchain 生态） |

---

## 五、面试常见问题

**Q: 为什么不直接用 LangGraph，要自己实现？**

> A: "自研的目的是深入理解 Agent 底层原理——ReAct 循环、工具调用协议、状态管理、
> 安全防护等。只有理解了这些，才能在使用 LangGraph 时知道框架在帮你做什么，
> 出问题时才能快速定位。就像学习操作系统原理，不是为了自己写 OS，
> 而是为了更好地使用 OS。"

**Q: LangGraph 和 LangChain 是什么关系？**

> A: "LangChain 是工具链（LLM/Prompt/Chain/Memory），LangGraph 是在 LangChain 之上
> 构建的图编排框架，专门解决 Agent 的状态管理和循环控制问题。
> LangGraph 依赖 langchain-core，但不依赖完整的 langchain 包。"

**Q: LangGraph 的 Checkpoint 和数据库持久化有什么区别？**

> A: "Checkpoint 是图执行状态的快照，包含所有消息历史和中间变量，
> 主要用于会话恢复和 Human-in-the-Loop。数据库持久化是业务数据的存储，
> 两者互补：Checkpoint 管 Agent 执行状态，数据库管业务数据。"

**Q: 什么时候用 interrupt_before，什么时候用 interrupt_after？**

> A: "interrupt_before 在节点执行前暂停，适合需要人工审批才能继续的场景
>（如代码执行前确认）。interrupt_after 在节点执行后暂停，适合需要人工
> 审核输出结果的场景（如内容发布前审核）。"

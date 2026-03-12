"""LangGraph 实现模块 — 与自研引擎的对比实现。

目录结构:
  react_agent.py       — LangGraph 版 ReAct Agent（对比自研 react_engine.py ~260 行）
  plan_execute.py      — LangGraph 版 Plan-and-Execute（对比自研 plan_execute_engine.py）
  multi_agent.py       — LangGraph 版多 Agent 协作（Subgraph 嵌套）
  checkpoint_demo.py   — LangGraph Checkpoint 状态持久化 + Human-in-the-Loop

面试话术:
  "我先从零实现理解了 Agent 底层原理，再用 LangGraph 重构后，
   发现框架在工作流编排和状态持久化上确实更便捷，但在工具系统
   定制和错误处理细粒度控制上，自研更灵活。"
"""

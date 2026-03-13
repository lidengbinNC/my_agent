"""Dify 集成模块。

目录结构:
  client.py          — Dify API 客户端（封装 Chat/Workflow/Knowledge API）
  plugins/           — Dify 自定义工具插件（暴露 MyAgent 工具给 Dify）
    myagent_tools.py — FastAPI 插件服务（Dify 通过 HTTP 调用）
    rag_tool.py      — RAG 检索工具插件
    sql_tool.py      — Text-to-SQL 工具插件
  app_templates/     — Dify 应用配置 DSL（可导入 Dify 控制台）
    agent_app.yml    — Agent 应用配置模板
    workflow_app.yml — Workflow 应用配置模板
  COMPARISON.md      — 自研 vs Dify 深度对比分析

面试话术:
  "企业里不是所有场景都需要从零开发，简单场景用 Dify 快速交付，
   复杂场景用自研引擎深度定制，我两种方式都有实践经验。"
"""

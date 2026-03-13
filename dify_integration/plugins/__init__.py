"""Dify 自定义工具插件服务。

Dify 自定义工具原理:
  1. 开发者提供一个 HTTP API（本文件实现）
  2. 在 Dify 控制台配置该 API 的 OpenAPI Schema
  3. Dify 的 Agent 在需要时调用该 HTTP API
  4. 结果返回给 Dify Agent，继续推理

本插件暴露的工具:
  - /tools/rag_search:  RAG 知识库检索（对接 MyRAG）
  - /tools/sql_query:   Text-to-SQL 查询
  - /tools/calculator:  数学计算（复用自研工具）
  - /tools/code_exec:   代码执行（复用自研工具）
"""

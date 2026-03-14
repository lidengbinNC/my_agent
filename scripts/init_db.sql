-- 初始化数据库脚本
-- 在 postgres 容器首次启动时自动执行
-- 注意：POSTGRES_DB 已创建 myagent 数据库，此处补充创建其他服务所需数据库

-- Dify 平台数据库
SELECT 'CREATE DATABASE dify'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'dify')\gexec

-- LangFuse 可观测性数据库
SELECT 'CREATE DATABASE langfuse'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'langfuse')\gexec

# MyAgent 多阶段构建 Dockerfile
#
# 面试考点:
#   - 多阶段构建：builder 阶段安装依赖，runtime 阶段只复制必要文件
#     → 最终镜像不包含构建工具，体积更小（~200MB vs ~800MB）
#   - 非 root 用户：安全最佳实践，防止容器逃逸
#   - .dockerignore：排除不必要文件，加速构建
#   - PYTHONDONTWRITEBYTECODE：不生成 .pyc，减少镜像体积
#   - PYTHONUNBUFFERED：日志实时输出，不缓冲

# ── 阶段1: 依赖安装 ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 切换为阿里云 apt 镜像源（加速国内网络）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件（利用 Docker 层缓存：依赖不变则不重新安装）
COPY pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    --trusted-host mirrors.aliyun.com \
    fastapi uvicorn[standard] \
    openai httpx \
    pydantic pydantic-settings \
    sqlalchemy aiosqlite asyncpg \
    structlog \
    tiktoken \
    langgraph \
    langgraph-checkpoint \
    "langgraph-checkpoint-sqlite<3.0.0" \
    langchain-core langchain-openai \
    python-dotenv jinja2 \
    prometheus-client

# ── 阶段2: 运行时镜像 ────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# 环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PORT=8001

WORKDIR /app

# 切换为阿里云 apt 镜像源（加速国内网络）
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 阶段复制已安装的 Python 包
COPY --from=builder /install /usr/local

# 创建非 root 用户（安全最佳实践）
RUN groupadd -r myagent && useradd -r -g myagent myagent

# 复制应用代码
COPY my_agent/ ./my_agent/
COPY langgraph_impl/ ./langgraph_impl/
COPY dify_integration/ ./dify_integration/

# 创建日志目录并设置权限
RUN mkdir -p /app/logs && chown -R myagent:myagent /app

# 切换到非 root 用户
USER myagent

EXPOSE ${PORT}

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/v1/health || exit 1

# 启动命令
CMD ["uvicorn", "my_agent.main:app", "--host", "0.0.0.0", "--port", "8001"]
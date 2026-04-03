"""MyAgent 应用入口 — FastAPI 组装。

启动方式:
  python -m my_agent.main          # 直接运行（推荐开发时使用）
  uvicorn my_agent.main:app --reload --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from my_agent.api.middleware.tracing import TracingMiddleware
from my_agent.api.routes import (
    agent,
    chat,
    customer_service,
    health,
    multi_agent,
    observability,
    session,
    tool,
    workflow,
)
from my_agent.api.routes import tasks as tasks_router
from my_agent.api.routes import evaluation as eval_router
from my_agent.api.routes import langgraph_routes
from my_agent.api.routes import dify_routes
from my_agent.api.routes import mcp_routes
from my_agent.api.routes.agent import init_default_agent
from my_agent.config.settings import settings
from my_agent.core.dependencies import shutdown_clients
from my_agent.infrastructure.db.database import init_db
from my_agent.tasks.handlers import register_all_handlers
from my_agent.tasks.queue import get_task_queue
from my_agent.utils.logger import get_logger, setup_logging

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(log_level=settings.log_level, json_format=not settings.app_debug)
    logger = get_logger("startup")
    logger.info(
        "app_starting",
        app=settings.app_name,
        host=settings.app_host,
        port=settings.app_port,
        model=settings.llm_model,
    )
    await init_db()
    logger.info("database_initialized")
    default_agent_id = init_default_agent()
    logger.info("default_plan_agent_created", agent_id=default_agent_id)
    # 启动异步任务队列
    queue = get_task_queue()
    register_all_handlers(queue)
    await queue.start()
    logger.info("task_queue_started")
    # 初始化 LangGraph SqliteSaver（单例 checkpointer + 图实例）
    # 写入 lg_checkpoints.db，可用 DB Browser for SQLite 查看 State 变化
    from langgraph_impl.checkpoint_store import init_checkpointer, CHECKPOINT_DB_PATH
    await init_checkpointer()
    logger.info("lg_checkpointer_ready", db=CHECKPOINT_DB_PATH)
    yield
    logger = get_logger("shutdown")
    logger.info("app_shutting_down")
    await queue.stop()
    await shutdown_clients()
    from langgraph_impl.checkpoint_store import shutdown_checkpointer
    await shutdown_checkpointer()
    from my_agent.utils.langfuse_client import get_langfuse_client
    get_langfuse_client().flush()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="智能多 Agent 任务执行平台",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ----- 中间件 -----
app.add_middleware(TracingMiddleware)

# ----- 路由 -----
app.include_router(health.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(customer_service.router, prefix="/api/v1")
app.include_router(tool.router, prefix="/api/v1")
app.include_router(session.router, prefix="/api/v1")
app.include_router(agent.router, prefix="/api/v1")
app.include_router(multi_agent.router, prefix="/api/v1")
app.include_router(workflow.router, prefix="/api/v1")
app.include_router(observability.router, prefix="/api/v1")
app.include_router(tasks_router.router, prefix="/api/v1")
app.include_router(eval_router.router, prefix="/api/v1")
app.include_router(langgraph_routes.router, prefix="/api/v1")
app.include_router(dify_routes.router, prefix="/api/v1")
app.include_router(mcp_routes.router, prefix="/api/v1")

# ----- 静态文件 & 模板 -----
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", include_in_schema=False)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/workflow", include_in_schema=False)
async def workflow_page(request: Request):
    return templates.TemplateResponse("workflow.html", {"request": request})


@app.get("/evaluation", include_in_schema=False)
async def evaluation_page(request: Request):
    return templates.TemplateResponse("evaluation.html", {"request": request})


@app.get("/tasks", include_in_schema=False)
async def tasks_page(request: Request):
    return templates.TemplateResponse("tasks.html", {"request": request})


@app.get("/mcp", include_in_schema=False)
async def mcp_page(request: Request):
    return templates.TemplateResponse("mcp.html", {"request": request})


def main() -> None:
    import uvicorn

    uvicorn.run(
        "my_agent.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

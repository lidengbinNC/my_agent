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
from my_agent.api.routes import chat, health
from my_agent.config.settings import settings
from my_agent.core.dependencies import shutdown_clients
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
    yield
    logger = get_logger("shutdown")
    logger.info("app_shutting_down")
    await shutdown_clients()


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

# ----- 静态文件 & 模板 -----
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", include_in_schema=False)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


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

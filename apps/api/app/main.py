from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from starlette.responses import Response

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import (  # noqa: F401
    AgentRun,
    CallEvent,
    Conversation,
    IntegrationEvent,
    KnowledgeChunk,
    Lead,
    LlmCall,
    Message,
    Product,
    WorkflowRun,
)
from app.routers import chat, integrations, ops
from app.seed import bootstrap

settings = get_settings()
REQUESTS = Counter("opsforge_api_requests_total", "API requests", ["path"])
WEB = Path(__file__).parent / "web"


def init_db() -> None:
    Path("data").mkdir(exist_ok=True)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        bootstrap(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="OpsForge",
    description="Автоматизация продаж и внутренних процессов OpsForge Demo",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(chat.router)
app.include_router(ops.router)
app.include_router(integrations.router)


@app.middleware("http")
async def metrics_mw(request, call_next):
    REQUESTS.labels(path=request.url.path).inc()
    return await call_next(request)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "env": settings.app_env, "service": "opsforge"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if WEB.exists():
    app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

"""
Main FastAPI application entry point.
"""
import sys
import asyncio
import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Windows: forcer le SelectorEventLoop (compatible avec uvicorn + websockets)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from backend.routers import (
    agents_router,
    connections_router,
    chat_router,
    llm_config_router,
    export_router,
    config_router,
    toolkits_router,
    report_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Python Agent Platform",
    description="Multi-agent orchestration platform with ClickHouse/Oracle data analyst agents",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Handler global d'erreurs 500 — affiche la vraie cause dans les logs ───────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        "Unhandled exception on %s %s\n%s",
        request.method,
        request.url,
        traceback.format_exc(),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {type(exc).__name__}: {exc}"},
    )

# ── API Routers ───────────────────────────────────────────────────────────────
app.include_router(llm_config_router)
app.include_router(agents_router)
app.include_router(connections_router)
app.include_router(chat_router)
app.include_router(export_router)
app.include_router(config_router)
app.include_router(toolkits_router)
app.include_router(report_router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Serve React frontend (production build) ───────────────────────────────────
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    # html=True : sert index.html pour toutes les routes inconnues (SPA fallback)
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

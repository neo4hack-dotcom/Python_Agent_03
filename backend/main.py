"""
Main FastAPI application entry point.
"""
import sys
import asyncio
import logging
from fastapi import FastAPI

# Windows: forcer le SelectorEventLoop (compatible avec uvicorn + websockets)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from backend.routers import (
    agents_router,
    connections_router,
    chat_router,
    llm_config_router,
    export_router,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

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
    allow_origins=["*"],  # Tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Routers ───────────────────────────────────────────────────────────────
app.include_router(llm_config_router)
app.include_router(agents_router)
app.include_router(connections_router)
app.include_router(chat_router)
app.include_router(export_router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "version": "1.0.0"}


# ── Serve React frontend (production build) ───────────────────────────────────
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        index = frontend_dist / "index.html"
        return FileResponse(str(index))

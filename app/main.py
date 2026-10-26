"""
Pream — FastAPI Application Entry Point.
"""
import logging
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# Configure root logger with standard timestamp formatting
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pream")

# Ensure parent directory (project root) is on sys.path if invoked directly
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.routers import (
    m3u_output,
    notifications,
    proxy,
    rules,
    sources,
    system,
    tmdb,
    ui,
    xtream_api,
)
from app.scheduler.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    # ── Startup ──
    # Ensure data directory exists for SQLite
    os.makedirs("data", exist_ok=True)

    # Initialize database tables
    init_db()

    # Start background scheduler
    start_scheduler()

    logger.info(f"Started {settings.SERVER_NAME} on {settings.HOST}:{settings.PORT}")

    yield

    # ── Shutdown ──
    stop_scheduler()
    logger.info(f"{settings.SERVER_NAME} shutting down")


app = FastAPI(
    title=settings.SERVER_NAME,
    description="Self-hosted IPTV playlist manager and Xtream Codes API gateway",
    version="0.1.0",
    lifespan=lifespan,
)

# ── Static Files ──
static_dir = os.path.join(os.path.dirname(__file__), "ui", "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# ── Routers ──
app.include_router(system.router)
app.include_router(sources.router)
app.include_router(rules.router)
app.include_router(xtream_api.router)
app.include_router(proxy.router)
app.include_router(m3u_output.router)
app.include_router(tmdb.router)
app.include_router(notifications.router)
app.include_router(ui.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=True)

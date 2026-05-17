"""Entry point: starts the FastAPI app, scheduler, and DB.

Run with:
    python main.py                # foreground, both API + scheduler
    SCAN_ONLY=1 python main.py    # run one scan and exit (useful for cron / debug)
"""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from loguru import logger

from api.routes import router
from config import PROJECT_ROOT, get_secrets
from database.db import init_db
from scheduler.jobs import create_scheduler, run_scan_cycle


def _setup_logging() -> None:
    log_level = get_secrets().log_level.upper()
    logger.remove()
    logger.add(sys.stderr, level=log_level)
    logger.add(
        PROJECT_ROOT / "logs" / "deal_hunter.log",
        rotation="10 MB",
        retention="14 days",
        level=log_level,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _setup_logging()
    init_db()
    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Deal Hunter started. Dashboard: http://127.0.0.1:8000/")
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        logger.info("Deal Hunter stopped.")


def create_app() -> FastAPI:
    app = FastAPI(title="Deal Hunter", lifespan=lifespan)
    app.include_router(router)
    return app


app = create_app()


async def _run_single_scan() -> None:
    _setup_logging()
    init_db()
    await run_scan_cycle()


def main() -> None:
    if os.getenv("SCAN_ONLY"):
        asyncio.run(_run_single_scan())
        return
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()

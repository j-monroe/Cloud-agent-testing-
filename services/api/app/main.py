"""
EDGAR Filing Intelligence API
FastAPI application providing query access to ingested filings.
"""
from __future__ import annotations
import os
import re
import time
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import yaml
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, "/app")

from shared.logger import get_logger
from app.database import engine, Base
from app.routes.filings import router as filings_router
from app.routes.companies import router as companies_router

logger = get_logger("api")

CONFIG_PATH = os.getenv("CONFIG_PATH", "/app/config/config.yaml")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        raw = f.read()

    def replacer(m: re.Match) -> str:
        key, default = m.group(1), m.group(2)
        return os.getenv(key, default or "")

    raw = re.sub(r"\$\{([A-Z_]+)(?::-(.*?))?\}", replacer, raw)
    return yaml.safe_load(raw)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    logger.info("api_starting")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("api_ready")
    yield
    await engine.dispose()
    logger.info("api_stopped")


config = load_config()
api_cfg = config.get("api", {})

app = FastAPI(
    title=api_cfg.get("title", "EDGAR Filing Intelligence API"),
    version=api_cfg.get("version", "1.0.0"),
    description=api_cfg.get("description", ""),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=api_cfg.get("cors_origins", ["*"]),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """Log all API requests for audit trail."""
    start = time.time()
    response: Response = await call_next(request)
    duration_ms = (time.time() - start) * 1000
    logger.info(
        "api_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
        client_ip=request.client.host if request.client else "unknown",
    )
    return response


app.include_router(filings_router)
app.include_router(companies_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "edgar-api"}


@app.get("/search")
async def search(
    q: str,
    page: int = 1,
    page_size: int = 50,
):
    """Full-text search placeholder (wire to Elasticsearch for production)."""
    return {
        "query": q,
        "message": "Full-text search requires Elasticsearch/OpenSearch integration",
        "items": [],
        "total": 0,
    }

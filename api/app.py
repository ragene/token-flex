"""
FastAPI application factory for token-flow.

Usage:
    from api.app import create_app
    app = create_app(db_path="token_flow.db")
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI

from api.routers import health, ingest, chunks, summaries, memory

logger = logging.getLogger(__name__)


def create_app(db_path: str) -> FastAPI:
    """
    Create and configure the token-flow FastAPI application.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Configured FastAPI instance.
    """
    load_dotenv()

    app = FastAPI(
        title="token-flow",
        description=(
            "Engine-based memory chunking, scoring, summarization, and S3 export service. "
            "Pipeline: ingest → chunk (4K tokens) → score (fact/preference/intent) → "
            "summarize top-40% → push to S3."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Store db_path on app.state for use in request handlers
    app.state.db_path = db_path

    # Register routers
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(chunks.router)
    app.include_router(summaries.router)
    app.include_router(memory.router)

    logger.info("token-flow app created (db=%s)", db_path)
    return app

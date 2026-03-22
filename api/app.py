"""
FastAPI application factory for token-flow.

Usage:
    from api.app import create_app
    app = create_app(database_url="postgresql://user:pass@host:5432/dbname")
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import health, ingest, chunks, summaries, memory, token_data, auth_routes

logger = logging.getLogger(__name__)


def create_app(database_url: str) -> FastAPI:
    """
    Create and configure the token-flow FastAPI application.

    Args:
        database_url: PostgreSQL connection URL.

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

    # CORS — allow the token-flow-ui and any local dev origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store database_url on app.state for use in request handlers
    app.state.database_url = database_url
    # Legacy alias — some routers still read db_path; point it at the URL string
    # so health endpoint can display something meaningful
    app.state.db_path = database_url

    # Register routers
    app.include_router(auth_routes.router)  # /auth/config and /auth/exchange (no auth required)
    app.include_router(health.router)
    app.include_router(ingest.router)
    app.include_router(chunks.router)
    app.include_router(summaries.router)
    app.include_router(memory.router)
    app.include_router(token_data.router)

    # Init DB schema on app startup + start background session push
    @app.on_event("startup")
    async def _init_db():
        import asyncio
        from db.schema import init_db
        from db.pg_compat import connect as pg_connect
        conn = pg_connect(database_url)
        try:
            init_db(conn)
        finally:
            conn.close()
        db_label = "SQLite" if database_url.startswith("sqlite") else "PostgreSQL"
        logger.info(f"token-flow DB schema initialized ({db_label})")

        # Background task: push snapshot (including live session data) every 30s
        async def _session_push_loop():
            from api.push_client import push_snapshot
            await asyncio.sleep(5)  # brief startup delay
            while True:
                try:
                    push_snapshot(database_url)
                except Exception as exc:
                    logger.debug("session push loop error (non-fatal): %s", exc)
                await asyncio.sleep(30)

        asyncio.create_task(_session_push_loop())

    logger.info("token-flow app created (db=PostgreSQL)")
    return app

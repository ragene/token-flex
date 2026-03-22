"""
FastAPI application factory for token-flow.

Usage:
    from api.app import create_app
    app = create_app(database_url="postgresql://user:pass@host:5432/dbname")
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import health, ingest, chunks, summaries, memory, token_data, auth_routes
from api.routers import users as users_router

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
    app.include_router(users_router.router)  # /users/*
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

        # Seed admin user if tf_users table is empty
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@token-flow.thefreightdawg.com")
        conn2 = pg_connect(database_url)
        try:
            with conn2._conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tf_users")
                count = cur.fetchone()[0]
                if count == 0:
                    cur.execute(
                        """INSERT INTO tf_users (email, name, role, is_active)
                           VALUES (%s, 'Admin', 'admin', TRUE)
                           ON CONFLICT (email) DO NOTHING""",
                        (admin_email,)
                    )
                    conn2.commit()
                    logger.info(f"Seeded admin user: {admin_email}")
        except Exception as e:
            logger.warning(f"Admin seed skipped (non-fatal): {e}")
        finally:
            conn2.close()

        # Background push loop: only run if local session files exist.
        # In ECS there are no local session files — running the push loop there
        # would POST a zero-data snapshot to /token-data/push every 30s, overwriting
        # the good snapshot pushed by the real local service and blanking the UI.
        # The local service (main.py) has its own push loop via a background thread.
        sessions_dir_env = os.environ.get("SESSIONS_DIR", "")
        from pathlib import Path
        _sessions_dir = Path(sessions_dir_env) if sessions_dir_env else Path.home() / ".openclaw/agents/main/sessions"
        _has_local_sessions = _sessions_dir.exists() and any(_sessions_dir.glob("*.jsonl"))

        if _has_local_sessions:
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
            logger.info("Background session push loop started (local sessions found)")
        else:
            logger.info("Background session push loop skipped (no local sessions — running in ECS/remote mode)")

    logger.info("token-flow app created (db=PostgreSQL)")
    return app

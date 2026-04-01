"""
PostgreSQL schema for token-flow.

init_db() is idempotent — safe to call on every startup.
Tables mirror the original SQLite schema with PostgreSQL-appropriate types:
  - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
  - REAL → FLOAT
  - TEXT DEFAULT (datetime('now')) → TIMESTAMPTZ DEFAULT NOW()
  - SQLite partial index (WHERE clause) → supported natively in PG
"""
from __future__ import annotations

_SCHEMA_SQL = """
    -- AI / LLM call token usage log
    CREATE TABLE IF NOT EXISTS token_usage (
        id                SERIAL PRIMARY KEY,
        user_email        TEXT,
        operation         TEXT NOT NULL,
        model             TEXT,
        prompt_tokens     INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens      INTEGER DEFAULT 0,
        cost_usd          FLOAT,
        source_label      TEXT,
        created_at        TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tok_op      ON token_usage(operation);
    CREATE INDEX IF NOT EXISTS idx_tok_email   ON token_usage(user_email);
    CREATE INDEX IF NOT EXISTS idx_tok_created ON token_usage(created_at DESC);

    CREATE TABLE IF NOT EXISTS memory_entries (
        id          SERIAL PRIMARY KEY,
        source_file TEXT NOT NULL,
        category    TEXT,
        content     TEXT NOT NULL,
        summary     TEXT,
        keywords    TEXT,
        relevance   FLOAT DEFAULT 1.0,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        last_used   TIMESTAMPTZ
    );
    CREATE INDEX IF NOT EXISTS idx_relevance ON memory_entries(relevance DESC);
    CREATE INDEX IF NOT EXISTS idx_category  ON memory_entries(category);

    CREATE TABLE IF NOT EXISTS chunk_cache (
        id               SERIAL PRIMARY KEY,
        source_id        INTEGER REFERENCES memory_entries(id) ON DELETE CASCADE,
        source_label     TEXT,
        chunk_index      INTEGER NOT NULL,
        content          TEXT NOT NULL,
        token_count      INTEGER,
        fact_score       FLOAT DEFAULT 0.0,
        preference_score FLOAT DEFAULT 0.0,
        intent_score     FLOAT DEFAULT 0.0,
        composite_score  FLOAT DEFAULT 0.0,
        summary          TEXT,
        is_summarized    INTEGER DEFAULT 0,
        pushed_to_s3_at  TIMESTAMPTZ,
        s3_key           TEXT,
        created_at       TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_composite ON chunk_cache(composite_score DESC);
    CREATE INDEX IF NOT EXISTS idx_source    ON chunk_cache(source_id);
    CREATE INDEX IF NOT EXISTS idx_unpushed  ON chunk_cache(pushed_to_s3_at)
        WHERE pushed_to_s3_at IS NULL;

    -- Pipeline activity log
    CREATE TABLE IF NOT EXISTS pipeline_events (
        id          SERIAL PRIMARY KEY,
        event_type  TEXT NOT NULL,
        detail      TEXT,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_ev_type    ON pipeline_events(event_type);
    CREATE INDEX IF NOT EXISTS idx_ev_created ON pipeline_events(created_at DESC);

    -- FreightDawg token usage mirror (synced from FreightDawg API)
    CREATE TABLE IF NOT EXISTS fd_token_usage (
        id                   INTEGER PRIMARY KEY,
        user_id              INTEGER,
        user_email           TEXT,
        operation            TEXT,
        chunk_id             TEXT,
        model                TEXT,
        prompt_tokens        INTEGER DEFAULT 0,
        completion_tokens    INTEGER DEFAULT 0,
        total_tokens         INTEGER DEFAULT 0,
        cost_usd             FLOAT,
        metadata_json        TEXT,
        created_at           TIMESTAMPTZ,
        synced_at            TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_fd_tok_op      ON fd_token_usage(operation);
    CREATE INDEX IF NOT EXISTS idx_fd_tok_email   ON fd_token_usage(user_email);
    CREATE INDEX IF NOT EXISTS idx_fd_tok_created ON fd_token_usage(created_at DESC);

    -- Persisted push snapshot cache — survives ECS restarts
    -- Only ever has one row (id=1), upserted on every push.
    CREATE TABLE IF NOT EXISTS push_cache (
        id         INTEGER PRIMARY KEY,
        payload    TEXT NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    -- Live token stats — upserted by the local push client on every push.
    -- Single row per owner_email. Dashboard reads directly from this table.
    CREATE TABLE IF NOT EXISTS token_stats (
        owner_email           TEXT PRIMARY KEY,
        total_tokens_approx   INTEGER NOT NULL DEFAULT 0,
        session_tokens        INTEGER NOT NULL DEFAULT 0,
        active_session_tokens INTEGER NOT NULL DEFAULT 0,
        idle_session_tokens   INTEGER NOT NULL DEFAULT 0,
        memory_tokens         INTEGER NOT NULL DEFAULT 0,
        session_files         INTEGER NOT NULL DEFAULT 0,
        cached_chunks         INTEGER NOT NULL DEFAULT 0,
        cached_chunk_tokens   INTEGER NOT NULL DEFAULT 0,
        status                TEXT NOT NULL DEFAULT 'ok',
        message               TEXT NOT NULL DEFAULT '',
        warn_threshold        INTEGER NOT NULL DEFAULT 30000,
        distill_threshold     INTEGER NOT NULL DEFAULT 30000,
        updated_at            TIMESTAMPTZ DEFAULT NOW()
    );

    -- SSO users with RBAC (roles: admin, viewer)
    CREATE TABLE IF NOT EXISTS tf_users (
        id          SERIAL PRIMARY KEY,
        email       TEXT NOT NULL UNIQUE,
        name        TEXT,
        auth0_sub   TEXT UNIQUE,
        role        TEXT NOT NULL DEFAULT 'viewer',
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        last_login  TIMESTAMPTZ,
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tf_users_email ON tf_users(email);
    CREATE INDEX IF NOT EXISTS idx_tf_users_sub   ON tf_users(auth0_sub);

    -- Active local service session — tracks who is running the local service
    CREATE TABLE IF NOT EXISTS local_sessions (
        id          SERIAL PRIMARY KEY,
        email       TEXT NOT NULL,
        name        TEXT,
        picture     TEXT,
        auth0_sub   TEXT,
        host        TEXT,
        session_id  TEXT,
        last_seen   TIMESTAMPTZ DEFAULT NOW(),
        created_at  TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_local_sessions_email    ON local_sessions(email);
    CREATE INDEX IF NOT EXISTS idx_local_sessions_last_seen ON local_sessions(last_seen DESC);
"""


_SCHEMA_SQL_SQLITE = """
    CREATE TABLE IF NOT EXISTS token_usage (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email        TEXT,
        operation         TEXT NOT NULL,
        model             TEXT,
        prompt_tokens     INTEGER DEFAULT 0,
        completion_tokens INTEGER DEFAULT 0,
        total_tokens      INTEGER DEFAULT 0,
        cost_usd          REAL,
        source_label      TEXT,
        created_at        TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_tok_op      ON token_usage(operation);
    CREATE INDEX IF NOT EXISTS idx_tok_email   ON token_usage(user_email);
    CREATE INDEX IF NOT EXISTS idx_tok_created ON token_usage(created_at);

    CREATE TABLE IF NOT EXISTS memory_entries (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        source_file TEXT NOT NULL,
        category    TEXT,
        content     TEXT NOT NULL,
        summary     TEXT,
        keywords    TEXT,
        relevance   REAL DEFAULT 1.0,
        created_at  TEXT DEFAULT (datetime('now')),
        last_used   TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_relevance ON memory_entries(relevance);
    CREATE INDEX IF NOT EXISTS idx_category  ON memory_entries(category);

    CREATE TABLE IF NOT EXISTS chunk_cache (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id        INTEGER REFERENCES memory_entries(id) ON DELETE CASCADE,
        source_label     TEXT,
        chunk_index      INTEGER NOT NULL,
        content          TEXT NOT NULL,
        token_count      INTEGER,
        fact_score       REAL DEFAULT 0.0,
        preference_score REAL DEFAULT 0.0,
        intent_score     REAL DEFAULT 0.0,
        composite_score  REAL DEFAULT 0.0,
        summary          TEXT,
        is_summarized    INTEGER DEFAULT 0,
        pushed_to_s3_at  TEXT,
        s3_key           TEXT,
        created_at       TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_composite ON chunk_cache(composite_score);
    CREATE INDEX IF NOT EXISTS idx_source    ON chunk_cache(source_id);

    CREATE TABLE IF NOT EXISTS pipeline_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type  TEXT NOT NULL,
        detail      TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS fd_token_usage (
        id                   INTEGER PRIMARY KEY,
        user_id              INTEGER,
        user_email           TEXT,
        operation            TEXT,
        chunk_id             TEXT,
        model                TEXT,
        prompt_tokens        INTEGER DEFAULT 0,
        completion_tokens    INTEGER DEFAULT 0,
        total_tokens         INTEGER DEFAULT 0,
        cost_usd             REAL,
        metadata_json        TEXT,
        created_at           TEXT,
        synced_at            TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS push_cache (
        id         INTEGER PRIMARY KEY,
        payload    TEXT NOT NULL,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS token_stats (
        owner_email           TEXT PRIMARY KEY,
        total_tokens_approx   INTEGER NOT NULL DEFAULT 0,
        session_tokens        INTEGER NOT NULL DEFAULT 0,
        active_session_tokens INTEGER NOT NULL DEFAULT 0,
        idle_session_tokens   INTEGER NOT NULL DEFAULT 0,
        memory_tokens         INTEGER NOT NULL DEFAULT 0,
        session_files         INTEGER NOT NULL DEFAULT 0,
        cached_chunks         INTEGER NOT NULL DEFAULT 0,
        cached_chunk_tokens   INTEGER NOT NULL DEFAULT 0,
        status                TEXT NOT NULL DEFAULT 'ok',
        message               TEXT NOT NULL DEFAULT '',
        warn_threshold        INTEGER NOT NULL DEFAULT 30000,
        distill_threshold     INTEGER NOT NULL DEFAULT 30000,
        updated_at            TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS tf_users (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        email       TEXT NOT NULL UNIQUE,
        name        TEXT,
        auth0_sub   TEXT UNIQUE,
        role        TEXT NOT NULL DEFAULT 'viewer',
        is_active   INTEGER NOT NULL DEFAULT 1,
        last_login  TEXT,
        created_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_tf_users_email ON tf_users(email);
    CREATE INDEX IF NOT EXISTS idx_tf_users_sub   ON tf_users(auth0_sub);

    -- Active local service session — tracks who is running the local service
    CREATE TABLE IF NOT EXISTS local_sessions (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        email       TEXT NOT NULL,
        name        TEXT,
        picture     TEXT,
        auth0_sub   TEXT,
        host        TEXT,
        session_id  TEXT,
        last_seen   TEXT DEFAULT (datetime('now')),
        created_at  TEXT DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_local_sessions_email     ON local_sessions(email);
    CREATE INDEX IF NOT EXISTS idx_local_sessions_last_seen ON local_sessions(last_seen DESC);
"""


def init_db(conn) -> None:
    """
    Create all tables and indexes if they don't exist.
    Auto-detects SQLite vs PostgreSQL from the connection type.
    Idempotent — safe to call on every startup.

    For PostgreSQL: each statement is executed in its own transaction so that
    a pre-existing table (IF NOT EXISTS) does not abort the remaining statements.
    """
    import sqlite3 as _sqlite3
    inner = getattr(conn, '_conn', conn)

    if isinstance(inner, _sqlite3.Connection):
        conn.executescript(_SCHEMA_SQL_SQLITE)
        conn.commit()
        return

    # PostgreSQL: run each statement independently so errors don't cascade
    from db.pg_compat import _adapt_sql
    adapted = _adapt_sql(_SCHEMA_SQL)
    pg_conn = inner
    for stmt in adapted.split(';'):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            cur = pg_conn.cursor()
            cur.execute(stmt)
            pg_conn.commit()
            cur.close()
        except Exception as e:
            pg_conn.rollback()
            import logging
            logging.getLogger(__name__).warning("init_db stmt failed: %s | %.120s", e, stmt)

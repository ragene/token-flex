"""
SQLite schema for token-flow.
Two tables:
  - memory_entries: source/summary records (same as original memory_distill)
  - chunk_cache: 4K-token chunks with scoring, summary, and S3 push metadata
"""
import sqlite3


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        -- AI / LLM call token usage log
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
        CREATE INDEX IF NOT EXISTS idx_tok_created ON token_usage(created_at DESC);

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
        CREATE INDEX IF NOT EXISTS idx_relevance ON memory_entries(relevance DESC);
        CREATE INDEX IF NOT EXISTS idx_category ON memory_entries(category);

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
        CREATE INDEX IF NOT EXISTS idx_composite ON chunk_cache(composite_score DESC);
        CREATE INDEX IF NOT EXISTS idx_source    ON chunk_cache(source_id);
        CREATE INDEX IF NOT EXISTS idx_unpushed  ON chunk_cache(pushed_to_s3_at)
            WHERE pushed_to_s3_at IS NULL;

        -- Pipeline activity log — one row per chunk/distill/clear/rebuild event
        CREATE TABLE IF NOT EXISTS pipeline_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type  TEXT NOT NULL,   -- 'chunk' | 'distill' | 'clear' | 'rebuild' | 'ingest'
            detail      TEXT,            -- JSON blob: counts, paths, stats
            created_at  TEXT DEFAULT (datetime('now'))
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
            cost_usd             REAL,
            metadata_json        TEXT,
            created_at           TEXT,
            synced_at            TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_fd_tok_op     ON fd_token_usage(operation);
        CREATE INDEX IF NOT EXISTS idx_fd_tok_email  ON fd_token_usage(user_email);
        CREATE INDEX IF NOT EXISTS idx_fd_tok_created ON fd_token_usage(created_at DESC);
    """)
    conn.commit()

"""
SQLite schema for token-flow.
Two tables:
  - memory_entries: source/summary records (same as original memory_distill)
  - chunk_cache: 4K-token chunks with scoring, summary, and S3 push metadata
"""
import sqlite3


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
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
    """)
    conn.commit()

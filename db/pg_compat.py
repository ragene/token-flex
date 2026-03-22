"""
db/pg_compat.py — psycopg2 wrapper that mimics the sqlite3 Connection/Cursor API
used throughout token-flow, so no engine or router code needs to change.

Key compatibility provided:
  - conn.execute(sql, params)  → replaces ? with %s, returns a cursor-like object
  - conn.executescript(sql)    → splits on ; and runs each statement
  - conn.commit()              → passthrough
  - conn.close()               → passthrough
  - conn.row_factory = sqlite3.Row  → ignored (we always return dict-like rows)
  - cursor.lastrowid           → populated via RETURNING id on INSERT
  - cursor.fetchone()          → list of DictRow
  - cursor.fetchall()          → list of DictRow
  - cursor.rowcount            → passthrough

Usage (drop-in for sqlite3.connect):
    from db.pg_compat import connect
    conn = connect(database_url)
"""
from __future__ import annotations

import re
import logging
from typing import Any, Optional, Sequence

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

# Regex: replace SQLite ? placeholders with %s (but not inside strings — good enough for our queries)
_PLACEHOLDER_RE = re.compile(r'\?')

# Regex: strip SQLite-only syntax for CREATE TABLE / INDEX
_AUTOINCREMENT_RE = re.compile(r'\bAUTOINCREMENT\b', re.IGNORECASE)
_INTEGER_PK_RE   = re.compile(r'\bINTEGER\s+PRIMARY\s+KEY\b', re.IGNORECASE)
_REAL_TYPE_RE    = re.compile(r'\bREAL\b', re.IGNORECASE)
# datetime('now') → NOW()
_DATETIME_NOW_RE = re.compile(r"datetime\('now'\)", re.IGNORECASE)
# TEXT / INTEGER DEFAULT ... → keep type but fix defaults
_TEXT_TYPE_RE    = re.compile(r'\bTEXT\b', re.IGNORECASE)


def _adapt_sql(sql: str) -> str:
    """Convert SQLite SQL dialect to PostgreSQL."""
    sql = _PLACEHOLDER_RE.sub('%s', sql)
    sql = _AUTOINCREMENT_RE.sub('', sql)
    sql = _INTEGER_PK_RE.sub('SERIAL PRIMARY KEY', sql)
    sql = _REAL_TYPE_RE.sub('FLOAT', sql)
    sql = _DATETIME_NOW_RE.sub('NOW()', sql)
    # SQLite uses TEXT for everything; map to TEXT (fine in PG)
    # Fix DEFAULT (NOW()) → DEFAULT NOW()
    sql = re.sub(r"DEFAULT\s+\(NOW\(\)\)", "DEFAULT NOW()", sql, flags=re.IGNORECASE)
    return sql


class _Cursor:
    """Thin wrapper around psycopg2 DictCursor that exposes lastrowid."""

    def __init__(self, pg_cursor):
        self._cur = pg_cursor
        self.lastrowid: Optional[int] = None

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _DictRow(row)

    def fetchall(self):
        return [_DictRow(r) for r in self._cur.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def __iter__(self):
        for row in self._cur:
            yield _DictRow(row)


class _DictRow:
    """
    Wraps a psycopg2 DictRow so callers can access columns by index OR by name,
    matching sqlite3.Row behaviour used in our code.
    """
    __slots__ = ('_row',)

    def __init__(self, row):
        self._row = row

    def __getitem__(self, key):
        return self._row[key]

    def __iter__(self):
        return iter(self._row.values() if hasattr(self._row, 'values') else self._row)

    def keys(self):
        return self._row.keys()

    def __len__(self):
        return len(self._row)

    def __repr__(self):
        return f"_DictRow({dict(self._row)})"


class Connection:
    """
    psycopg2 connection wrapper with sqlite3-compatible API.
    """

    def __init__(self, pg_conn):
        self._conn = pg_conn
        # sqlite3.Row sentinel — ignored, we always use DictCursor
        self.row_factory = None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> _Cursor:
        adapted = _adapt_sql(sql)
        cur = self._conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        wrapper = _Cursor(cur)

        # Detect INSERT … RETURNING id to populate lastrowid
        is_insert = adapted.strip().upper().startswith('INSERT')
        if is_insert and 'RETURNING' not in adapted.upper():
            # Append RETURNING id so we can grab lastrowid
            adapted = adapted.rstrip().rstrip(';') + ' RETURNING id'
            try:
                cur.execute(adapted, params or ())
                row = cur.fetchone()
                if row:
                    wrapper.lastrowid = row[0]
            except Exception:
                # If RETURNING fails (e.g. table has no id col), fall back
                cur.execute(_adapt_sql(sql), params or ())
        else:
            cur.execute(adapted, params or ())

        return wrapper

    def executescript(self, sql: str) -> None:
        """Execute a multi-statement SQL script (SQLite-style)."""
        adapted = _adapt_sql(sql)
        cur = self._conn.cursor()
        # Split on semicolons, skip empty statements
        for stmt in adapted.split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    cur.execute(stmt)
                except Exception as e:
                    # Log but continue — some statements may already exist (IF NOT EXISTS)
                    log.debug("executescript stmt error (non-fatal): %s | stmt: %.100s", e, stmt)
        cur.close()

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


_NOW_RE = re.compile(r'\bNOW\(\)', re.IGNORECASE)


def _adapt_sql_sqlite(sql: str) -> str:
    """Convert PostgreSQL SQL dialect to SQLite."""
    sql = re.sub(r'%s', '?', sql)
    sql = _NOW_RE.sub("datetime('now')", sql)
    return sql


class _SqliteCursor:
    """Wraps sqlite3 cursor to provide %s→? translation and lastrowid."""

    def __init__(self, cur):
        self._cur = cur
        self.lastrowid = None
        self.rowcount = 0

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


class _SqliteConnection:
    """Wraps sqlite3.Connection to provide %s and NOW() translation for SQLite."""

    def __init__(self, conn):
        self._conn = conn
        import sqlite3 as _sq
        self._conn.row_factory = _sq.Row

    def execute(self, sql: str, params=None) -> _SqliteCursor:
        adapted = _adapt_sql_sqlite(sql)
        cur = self._conn.cursor()
        if params:
            cur.execute(adapted, params)
        else:
            cur.execute(adapted)
        wrapper = _SqliteCursor(cur)
        wrapper.lastrowid = cur.lastrowid
        wrapper.rowcount = cur.rowcount
        return wrapper

    def executescript(self, sql: str) -> None:
        self._conn.executescript(sql)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def connect(database_url: str):
    """
    Open a DB connection compatible with the sqlite3 API used in token-flow.
    Supports both sqlite:/// (local dev) and postgresql:// (production).

    Returns a Connection wrapper with sqlite3-compatible API.
    """
    if database_url.startswith("sqlite"):
        import sqlite3 as _sq
        path = database_url.replace("sqlite:///", "")
        return _SqliteConnection(_sq.connect(path))
    pg_conn = psycopg2.connect(database_url)
    pg_conn.autocommit = False
    return Connection(pg_conn)

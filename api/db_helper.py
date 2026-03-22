"""
api/db_helper.py — shared helper to get a DB connection from a FastAPI Request.

Replaces the sqlite3.connect(request.app.state.db_path) pattern used in all routers.
Returns a pg_compat.Connection that exposes the same sqlite3-compatible API.
"""
from __future__ import annotations

from fastapi import Request

from db.schema import init_db


def get_conn(request: Request):
    """
    Open and return a DB connection using the DATABASE_URL stored on app.state.
    Supports both sqlite:/// (local dev) and postgresql:// (production).
    Always returns a pg_compat-wrapped connection with %s placeholder support.
    Caller is responsible for closing. Use in a try/finally block.
    """
    from db.pg_compat import connect as pg_connect
    database_url: str = request.app.state.database_url
    conn = pg_connect(database_url)
    init_db(conn)
    return conn


def get_db_url(request: Request) -> str:
    return request.app.state.database_url

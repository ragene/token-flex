"""
push_client.py — Lightweight helper for the token-flow local service to push
token data, chunk, and summary snapshots to the token-flow-ui.

Usage
-----
    from api.push_client import push_snapshot

    # Fire-and-forget (best-effort, never raises):
    push_snapshot(db_path, ui_url="http://localhost:8081")

    # Or push a pre-built payload:
    push_snapshot(db_path, ui_url="http://localhost:8081", payload={...})

Configuration
-------------
The UI base URL is read from the environment variable TOKEN_FLOW_UI_URL
(default: http://localhost:8081).  Set it in .env or the OS environment.

The helper calls POST /token-data/push on the UI service.  It is safe to
call even when no UI is running — failures are logged at DEBUG level and
swallowed so they never crash the caller.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_UI_URL = "https://token-flow-api.thefreightdawg.com"


def _build_snapshot(db_path: str) -> dict:
    """Read current state from DB and return a snapshot dict."""
    import sqlite3 as _sqlite3
    from db.schema import init_db

    c = _sqlite3.connect(db_path)
    c.row_factory = _sqlite3.Row
    init_db(c)
    try:
        rows = c.execute("""
            SELECT operation, model,
                   COUNT(*) as total_calls,
                   COALESCE(SUM(prompt_tokens),0)     as prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) as completion_tokens,
                   COALESCE(SUM(total_tokens),0)      as total_tokens,
                   COALESCE(SUM(cost_usd),0.0)        as cost_usd
            FROM token_usage
            GROUP BY operation, model
            ORDER BY total_tokens DESC
        """).fetchall()
        summary_rows = [dict(r) for r in rows]
        grand_tokens = sum(r["total_tokens"] for r in summary_rows)
        grand_calls  = sum(r["total_calls"]  for r in summary_rows)
        grand_cost   = round(sum(r["cost_usd"] for r in summary_rows), 6)

        chunk_rows = c.execute("""
            SELECT id, source_label, chunk_index, token_count,
                   composite_score, fact_score, preference_score, intent_score,
                   summary, is_summarized, created_at
            FROM chunk_cache
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()
        chunks = [dict(r) for r in chunk_rows]

        event_rows = c.execute("""
            SELECT id, user_email, operation, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   cost_usd, source_label, created_at
            FROM token_usage
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()
        events = [dict(r) for r in event_rows]
    finally:
        c.close()

    return {
        "ts": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "rows": summary_rows,
            "grand_total_tokens": grand_tokens,
            "grand_total_calls":  grand_calls,
            "grand_cost_usd":     grand_cost,
        },
        "chunks": chunks,
        "events": events,
    }


def push_snapshot(
    db_path: str,
    ui_url: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """
    POST a snapshot to the token-flow-ui's /token-data/push endpoint.

    Args:
        db_path:  Path to the SQLite DB (used to build the snapshot if payload is None).
        ui_url:   Base URL of the token-flow-ui service.
                  Defaults to TOKEN_FLOW_UI_URL env var or http://localhost:8081.
        payload:  Pre-built snapshot dict.  If None, a fresh snapshot is built from DB.
    """
    base = (ui_url or os.environ.get("TOKEN_FLOW_UI_URL", _DEFAULT_UI_URL)).rstrip("/")
    endpoint = f"{base}/token-data/push"

    try:
        import urllib.request
        import json

        data = payload if payload is not None else _build_snapshot(db_path)
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            log.debug("push_snapshot → %s  status=%s", endpoint, resp.status)
    except Exception as exc:
        log.debug("push_snapshot failed (non-fatal): %s", exc)

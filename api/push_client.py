"""
push_client.py — Lightweight helper for the token-flow local service to push
token data, chunk, summary, memory entry, and pipeline event snapshots to
the token-flow-ui via POST /token-data/push.

Usage
-----
    from api.push_client import push_snapshot, log_pipeline_event

    # Log a pipeline event then push (best-effort, never raises):
    log_pipeline_event(db_path, "chunk",   {"chunks_created": 12, "source": "foo.md"})
    log_pipeline_event(db_path, "distill", {"summarized": 5, "pushed_s3": 2})
    log_pipeline_event(db_path, "clear",   {"files_cleared": 2})
    log_pipeline_event(db_path, "rebuild", {"entries": 20, "output": "2026-03-21.md"})
    push_snapshot(db_path)

Configuration
-------------
TOKEN_FLOW_UI_URL env var (default: https://token-flow-api.thefreightdawg.com).
Snapshot is posted to POST /token-data/push on that base URL.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_UI_URL = "https://token-flow-api.thefreightdawg.com"


# ── Snapshot builder ──────────────────────────────────────────────────────────

def _build_snapshot(db_path: str) -> dict:
    """Read current state from DB and return a full snapshot dict."""
    from db.schema import init_db

    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    init_db(c)
    try:
        # Token usage summary
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

        # Latest 100 chunks
        chunk_rows = c.execute("""
            SELECT id, source_label, chunk_index, token_count,
                   composite_score, fact_score, preference_score, intent_score,
                   summary, is_summarized, created_at
            FROM chunk_cache
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()
        chunks = [dict(r) for r in chunk_rows]

        # Latest 100 token events
        event_rows = c.execute("""
            SELECT id, user_email, operation, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   cost_usd, source_label, created_at
            FROM token_usage
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()
        events = [dict(r) for r in event_rows]

        # Latest 50 memory entries
        try:
            memory_rows = c.execute("""
                SELECT id, source_file, category, summary, keywords, relevance, created_at
                FROM memory_entries
                ORDER BY relevance DESC, created_at DESC
                LIMIT 50
            """).fetchall()
            memory_entries = [dict(r) for r in memory_rows]
        except Exception:
            memory_entries = []

        # Latest 50 pipeline events
        try:
            pipeline_rows = c.execute("""
                SELECT id, event_type, detail, created_at
                FROM pipeline_events
                ORDER BY created_at DESC
                LIMIT 50
            """).fetchall()
            pipeline_events = []
            for r in pipeline_rows:
                row = dict(r)
                try:
                    row["detail"] = json.loads(row["detail"]) if row["detail"] else {}
                except Exception:
                    row["detail"] = {}
                pipeline_events.append(row)
        except Exception:
            pipeline_events = []

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
        "chunks":          chunks,
        "events":          events,
        "memory_entries":  memory_entries,
        "pipeline_events": pipeline_events,
    }


# ── Pipeline event logger ─────────────────────────────────────────────────────

def log_pipeline_event(
    db_path: str,
    event_type: str,
    detail: Optional[dict] = None,
) -> None:
    """
    Insert a pipeline_events row. Best-effort — never raises.

    event_type: 'chunk' | 'distill' | 'clear' | 'rebuild' | 'ingest'
    detail:     arbitrary JSON-serialisable dict with counts / paths / stats
    """
    try:
        from db.schema import init_db
        conn = sqlite3.connect(db_path)
        init_db(conn)
        conn.execute(
            "INSERT INTO pipeline_events (event_type, detail) VALUES (?, ?)",
            (event_type, json.dumps(detail or {})),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.debug("log_pipeline_event failed (non-fatal): %s", exc)


# ── Push helper ───────────────────────────────────────────────────────────────

def push_snapshot(
    db_path: str,
    ui_url: Optional[str] = None,
    payload: Optional[dict] = None,
) -> None:
    """
    POST a snapshot to the token-flow-ui's /token-data/push endpoint.

    Args:
        db_path:  Path to the SQLite DB (used to build the snapshot if payload is None).
        ui_url:   Base URL override. Defaults to TOKEN_FLOW_UI_URL env var or
                  https://token-flow-api.thefreightdawg.com.
        payload:  Pre-built snapshot dict. If None, a fresh snapshot is built from DB.
    """
    import urllib.request

    base = (ui_url or os.environ.get("TOKEN_FLOW_UI_URL", _DEFAULT_UI_URL)).rstrip("/")
    endpoint = f"{base}/token-data/push"

    try:
        data = payload if payload is not None else _build_snapshot(db_path)
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            log.debug("push_snapshot → %s  status=%s", endpoint, resp.status)
    except Exception as exc:
        log.debug("push_snapshot failed (non-fatal): %s", exc)

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
TOKEN_FLOW_UI_URL env var (default: https://token-flow.thefreightdawg.com).
Snapshot is posted to POST /token-data/push on that base URL.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_UI_URL = "https://token-flow.thefreightdawg.com"


def _normalize_db_url(db_path: str) -> str:
    """Ensure db_path is a proper URL for pg_compat.connect().
    Raw file paths (e.g. /tmp/foo.db) are wrapped as sqlite:///path.
    postgresql:// and sqlite:/// URLs are passed through unchanged.
    """
    if db_path.startswith(("postgresql://", "postgres://", "sqlite:///")):
        return db_path
    return f"sqlite:///{db_path}"


# ── Session / token data builders (local files) ───────────────────────────────

def _build_session_data() -> dict:
    """Read the active OpenClaw session from local disk."""
    import json as _j
    from pathlib import Path

    sessions_dir = Path(
        os.environ.get("SESSIONS_DIR") or
        os.path.expanduser("~/.openclaw/agents/main/sessions")
    )
    result = {
        "session_id": None, "session_file": None,
        "token_count_approx": 0, "message_count": 0,
        "channel": None, "started_at": None, "last_updated_at": None,
    }
    try:
        sessions_json = sessions_dir / "sessions.json"
        if not sessions_json.exists():
            return result
        meta = _j.loads(sessions_json.read_text(errors="ignore"))
        # Prefer agent:main:main, fall back to first entry
        sm = meta.get("agent:main:main") or next(iter(meta.values()), {})
        sid = sm.get("sessionId")
        channel = sm.get("lastChannel") or sm.get("deliveryContext", {}).get("channel")
        updated_ms = sm.get("updatedAt")
        from datetime import timezone
        last_updated = (
            datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc).isoformat()
            if updated_ms else None
        )
        # Use exact token count from metadata if available
        total_tokens = sm.get("totalTokens")
        result.update({
            "session_id":         sid,
            "channel":            channel,
            "last_updated_at":    last_updated,
            "token_count_approx": total_tokens or 0,
            "model":              sm.get("model"),
        })
        if sid:
            sf = sessions_dir / f"{sid}.jsonl"
            result["session_file"] = str(sf)
            if sf.exists():
                raw = sf.read_text(errors="ignore")
                if not total_tokens:
                    result["token_count_approx"] = len(raw) // 4
                mc = 0
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    try:
                        obj = _j.loads(line)
                    except Exception:
                        continue
                    t = obj.get("type", "")
                    if t == "session" and result["started_at"] is None:
                        result["started_at"] = obj.get("timestamp")
                    if t in ("human", "assistant", "say", "message"):
                        mc += 1
                result["message_count"] = mc
    except Exception as exc:
        log.debug("_build_session_data failed (non-fatal): %s", exc)
    return result


def _build_token_data() -> dict:
    """Build token count / status data from local session + memory files."""
    from pathlib import Path

    sessions_dir = Path(
        os.environ.get("SESSIONS_DIR") or
        os.path.expanduser("~/.openclaw/agents/main/sessions")
    )
    memory_dir = Path(os.environ.get(
        "MEMORY_DIR",
        os.path.expanduser("~/.openclaw/workspace/memory"),
    ))
    warn      = int(os.environ.get("SMART_MEMORY_WARN_TOKENS",   "30000"))
    distill   = int(os.environ.get("SMART_MEMORY_DISTILL_TOKENS", "30000"))

    def _approx(p: Path) -> int:
        try:
            return len(p.read_text(errors="ignore")) // 4
        except Exception:
            return 0

    session_files = list(sessions_dir.glob("*.jsonl")) if sessions_dir.exists() else []
    # Identify the active session ID from sessions.json so we can track it separately
    _active_sid = None
    try:
        import json as _j
        _sj = sessions_dir / "sessions.json"
        if _sj.exists():
            _meta = _j.loads(_sj.read_text(errors="ignore"))
            _sm = _meta.get("agent:main:main") or next(iter(_meta.values()), {})
            _active_sid = _sm.get("sessionId")
    except Exception:
        pass

    _active_tokens = 0
    _idle_tokens = 0
    for f in session_files:
        t = _approx(f)
        if _active_sid and f.stem == _active_sid:
            _active_tokens = t
        else:
            _idle_tokens += t
    session_tokens = _active_tokens + _idle_tokens

    today = datetime.utcnow().date().isoformat()
    memory_tokens = _approx(memory_dir / f"{today}.md")
    total = session_tokens + memory_tokens

    # Status is based on idle (clearable) sessions + memory only.
    # The active session grows continuously and shouldn't trigger a distill alarm by itself.
    clearable = _idle_tokens + memory_tokens
    if clearable >= distill:
        status, msg = "critical", f"⚠️ Clearable context large (~{clearable:,} tokens). Distill NOW."
    elif clearable >= warn:
        status, msg = "warning", f"🟡 Clearable context growing (~{clearable:,} tokens). Consider distilling."
    else:
        status, msg = "ok", f"✅ Context healthy (~{clearable:,} clearable tokens). Active session: ~{_active_tokens:,} tokens."

    return {
        "total_tokens_approx":   total,
        "session_tokens":        session_tokens,
        "active_session_tokens": _active_tokens,
        "idle_session_tokens":   _idle_tokens,
        "memory_tokens":         memory_tokens,
        "session_files":         len(session_files),
        "status":                status,
        "message":               msg,
        "warn_threshold":        warn,
        "distill_threshold":     distill,
    }


# ── Snapshot builder ──────────────────────────────────────────────────────────

def _build_snapshot(db_path: str) -> dict:
    """Read current state from DB and return a full snapshot dict."""
    from db.schema import init_db

    from db.pg_compat import connect as pg_connect
    c = pg_connect(_normalize_db_url(db_path))
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
        "session":         _build_session_data(),
        "tokens":          _build_token_data(),
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
        from db.pg_compat import connect as pg_connect
        conn = pg_connect(_normalize_db_url(db_path))
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

def _get_push_token() -> Optional[str]:
    """
    Resolve a bearer token for the /token-data/push call.

    Priority order:
      1. TOKEN_FLOW_JWT env var — pre-minted by manage.sh start-poller so
         the poller never needs interactive device-flow.
      2. Cached Auth0 device-flow token (interactive sessions / local dev).
      3. None — push goes unauthenticated; will 401 if AUTH0_DOMAIN is set.
    """
    # 1. Pre-minted JWT passed in by manage.sh (headless / ECS path)
    jwt_env = os.environ.get("TOKEN_FLOW_JWT", "").strip()
    if jwt_env:
        return jwt_env

    # 2. Cached device-flow token (interactive local dev)
    try:
        from api.device_auth import _load_cache
        cached = _load_cache()
        if cached:
            return cached
    except Exception:
        pass

    return None


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
        headers = {"Content-Type": "application/json"}
        token = _get_push_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        else:
            log.warning("push_snapshot: no auth token available — push may be rejected (401)")
        req = urllib.request.Request(
            endpoint,
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            log.debug("push_snapshot → %s  status=%s", endpoint, resp.status)
    except Exception as exc:
        log.debug("push_snapshot failed (non-fatal): %s", exc)

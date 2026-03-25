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

# Suppress repeated "no auth token" warnings — warn once, stay quiet until a
# token is resolved or the push succeeds.
_warned_no_token: bool = False


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
    """
    Build token count / status data — single source of truth.

    Strategy (in priority order):
      1. Fetch from local /tokens API (same logic as the dashboard's GET /tokens).
         This is the authoritative value since it reads actual .jsonl file sizes.
      2. Fall back to file-based estimation if the local API is unreachable.
    """
    import urllib.request as _ur

    port = int(os.environ.get("TOKEN_FLOW_PORT", "8001"))
    try:
        req = _ur.Request(f"http://localhost:{port}/tokens")
        tok = _get_push_token()
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        with _ur.urlopen(req, timeout=3) as r:
            data = json.loads(r.read())
            # Normalise field names so downstream consumers see a consistent shape.
            return {
                "total_tokens_approx":   data.get("total_tokens_approx", 0),
                "session_tokens":        data.get("session_tokens", 0),
                "active_session_tokens": data.get("claude_tokens", 0),
                "idle_session_tokens":   data.get("session_tokens", 0) - data.get("claude_tokens", 0),
                "memory_tokens":         data.get("memory_tokens", 0),
                "session_files":         data.get("session_files", 0),
                "status":                data.get("status", "ok"),
                "message":               data.get("message", ""),
                "warn_threshold":        data.get("warn_threshold", 30000),
                "distill_threshold":     data.get("distill_threshold", 30000),
                "cached_chunks":         data.get("cached_chunks", 0),
                "cached_chunk_tokens":   data.get("cached_chunk_tokens", 0),
            }
    except Exception:
        pass  # fall through to file-based fallback

    # ── File-based fallback (local API unreachable) ───────────────────────────
    from pathlib import Path

    sessions_dir = Path(
        os.environ.get("SESSIONS_DIR") or
        os.path.expanduser("~/.openclaw/agents/main/sessions")
    )
    memory_dir = Path(os.environ.get(
        "MEMORY_DIR",
        os.path.expanduser("~/.openclaw/workspace/memory"),
    ))
    warn    = int(os.environ.get("SMART_MEMORY_WARN_TOKENS",    "30000"))
    distill = int(os.environ.get("SMART_MEMORY_DISTILL_TOKENS", "30000"))

    def _approx(p: Path) -> int:
        try:
            return len(p.read_text(errors="ignore")) // 4
        except Exception:
            return 0

    session_files  = list(sessions_dir.glob("*.jsonl")) if sessions_dir.exists() else []
    session_tokens = sum(_approx(f) for f in session_files)

    # Sum all memory files (not just today's)
    memory_tokens = sum(_approx(f) for f in memory_dir.glob("*.md")) if memory_dir.exists() else 0

    total     = session_tokens + memory_tokens
    clearable = memory_tokens  # active session isn't clearable without distill

    if clearable >= distill:
        status, msg = "critical", f"⚠️ Clearable context large (~{clearable:,} tokens). Distill NOW."
    elif clearable >= warn:
        status, msg = "warning",  f"🟡 Clearable context growing (~{clearable:,} tokens). Consider distilling."
    else:
        status, msg = "ok",       f"✅ Context healthy (~{clearable:,} clearable tokens). Session: ~{session_tokens:,} tokens."

    return {
        "total_tokens_approx":   total,
        "session_tokens":        session_tokens,
        "active_session_tokens": session_tokens,
        "idle_session_tokens":   0,
        "memory_tokens":         memory_tokens,
        "session_files":         len(session_files),
        "status":                status,
        "message":               msg,
        "warn_threshold":        warn,
        "distill_threshold":     distill,
    }


# ── Snapshot builder ──────────────────────────────────────────────────────────

def _get_cleared_at(ui_url: Optional[str] = None) -> Optional[str]:
    """
    Fetch the cleared_at timestamp for the owner from the remote push_cache.
    Returns an ISO timestamp string, or None if never cleared.
    """
    try:
        base = (ui_url or os.environ.get("TOKEN_FLOW_UI_URL", _DEFAULT_UI_URL)).rstrip("/")
        import urllib.request as _ur
        req = _ur.Request(f"{base}/token-data/cleared-at")
        tok = _get_push_token()
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
        with _ur.urlopen(req, timeout=4) as r:
            data = json.loads(r.read())
            return data.get("cleared_at")
    except Exception:
        pass
    return None


def _extract_session_usage(owner_email: Optional[str] = None, after: Optional[str] = None) -> list:
    """
    Parse token usage records from the active OpenClaw session JSONL file.
    Returns a list of event dicts compatible with the token_usage schema.
    """
    from pathlib import Path as _Path
    sessions_dir = _Path(
        os.environ.get("SESSIONS_DIR") or
        os.path.expanduser("~/.openclaw/agents/main/sessions")
    )
    events = []
    try:
        sj = sessions_dir / "sessions.json"
        if not sj.exists():
            return events
        meta = json.loads(sj.read_text(errors="ignore"))
        # Process all sessions, not just main — include idle sessions too
        for key, sm in meta.items():
            sid = sm.get("sessionId")
            if not sid:
                continue
            sf = sessions_dir / f"{sid}.jsonl"
            if not sf.exists():
                continue
            current_model = None
            with open(sf, errors="ignore") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    # Track current model
                    if obj.get("type") == "model_change":
                        current_model = obj.get("modelId")
                    # Extract usage from assistant messages
                    if obj.get("type") == "message":
                        msg = obj.get("message") or {}
                        usage = msg.get("usage")
                        if usage and msg.get("role") == "assistant":
                            ts = obj.get("timestamp", "")
                            if after and ts and ts <= after:
                                continue  # skip events before the clear timestamp
                            cost = (usage.get("cost") or {}).get("total")
                            events.append({
                                "user_email":         owner_email or "",
                                "operation":          "chat",
                                "model":              current_model,
                                "prompt_tokens":      (usage.get("input") or 0) +
                                                      (usage.get("cacheRead") or 0) +
                                                      (usage.get("cacheWrite") or 0),
                                "completion_tokens":  usage.get("output") or 0,
                                "total_tokens":       usage.get("totalTokens") or 0,
                                "cost_usd":           cost,
                                "source_label":       f"session:{sid[:8]}",
                                "created_at":         obj.get("timestamp"),
                            })
    except Exception as exc:
        log.debug("_extract_session_usage failed (non-fatal): %s", exc)
    return events


def _build_snapshot(db_path: str) -> dict:
    """Read current state from DB and return a full snapshot dict."""
    from db.schema import init_db

    from db.pg_compat import connect as pg_connect
    c = pg_connect(_normalize_db_url(db_path))
    init_db(c)
    try:
        # Token usage summary — chat/session ops only (excludes engine ops like
        # summarize/ingest_summarize/ingest_git which belong in the Activity view).
        _CHAT_OPS = ("'chat'",)
        _CHAT_FILTER = f"WHERE operation IN ({','.join(_CHAT_OPS)})"
        rows = c.execute(f"""
            SELECT operation, model,
                   COUNT(*) as total_calls,
                   COALESCE(SUM(prompt_tokens),0)     as prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) as completion_tokens,
                   COALESCE(SUM(total_tokens),0)      as total_tokens,
                   COALESCE(SUM(cost_usd),0.0)        as cost_usd
            FROM token_usage
            {_CHAT_FILTER}
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

        # Latest 100 chat/session token events only — excludes engine ops.
        event_rows = c.execute(f"""
            SELECT id, COALESCE(user_email, '') as user_email, operation, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   cost_usd, source_label, created_at
            FROM token_usage
            {_CHAT_FILTER}
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()
        events = [dict(r) for r in event_rows]

        # If DB has no token_usage rows (local-only deployment), extract usage
        # directly from the OpenClaw session JSONL files so the dashboard shows
        # real AI cost/token data even without a Postgres token_usage table.
        if not events:
            owner_email = _get_owner_email()
            # Respect cleared_at — only show events after the last clear timestamp
            _cleared_map = {}
            try:
                _pc = c.execute("SELECT payload FROM push_cache WHERE id = 1").fetchone()
                if _pc:
                    _cleared_map = json.loads(_pc[0]).get("cleared_at") or {}
            except Exception:
                pass
            _after = _cleared_map.get(owner_email or "") or _cleared_map.get("__all__")
            session_events = _extract_session_usage(owner_email=owner_email, after=_after)
            if session_events:
                events = session_events[-100:]  # latest 100
                # Recompute summary from session events
                from collections import defaultdict as _dd
                agg: dict = _dd(lambda: {"total_calls": 0, "prompt_tokens": 0,
                                          "completion_tokens": 0, "total_tokens": 0,
                                          "cost_usd": 0.0})
                for e in session_events:
                    key = (e.get("operation", ""), e.get("model", ""))
                    agg[key]["total_calls"]       += 1
                    agg[key]["prompt_tokens"]     += e.get("prompt_tokens") or 0
                    agg[key]["completion_tokens"] += e.get("completion_tokens") or 0
                    agg[key]["total_tokens"]      += e.get("total_tokens") or 0
                    agg[key]["cost_usd"]          += e.get("cost_usd") or 0.0
                summary_rows = [{"operation": k[0], "model": k[1], **v} for k, v in agg.items()]
                grand_tokens = sum(r["total_tokens"] for r in summary_rows)
                grand_calls  = sum(r["total_calls"]  for r in summary_rows)
                grand_cost   = round(sum(r["cost_usd"] for r in summary_rows), 6)

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
        "owner_email": _get_owner_email(),   # tags snapshot with local user identity
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

def _get_owner_email() -> Optional[str]:
    """
    Resolve the email of the authenticated local user (machine owner).
    Used to tag push snapshots so the server can scope tokens/session display.

    Priority:
      1. OWNER_EMAIL env var (set by manage.sh / ECS task def)
      2. Decoded from TOKEN_FLOW_JWT env var
      3. Cached tf_auth.json user.email (device-flow login)
    """
    # 1. Explicit override
    explicit = os.environ.get("OWNER_EMAIL", "").strip()
    if explicit:
        return explicit

    # 2. Decode from TOKEN_FLOW_JWT
    jwt_env = os.environ.get("TOKEN_FLOW_JWT", "").strip()
    if jwt_env:
        try:
            import base64 as _b64
            parts = jwt_env.split(".")
            if len(parts) >= 2:
                padded = parts[1] + "=" * (-len(parts[1]) % 4)
                claim = json.loads(_b64.urlsafe_b64decode(padded))
                if claim.get("email"):
                    return claim["email"]
        except Exception:
            pass

    # 3. Cached device-flow user info
    try:
        from api.device_auth import CACHE_PATH
        data = json.loads(CACHE_PATH.read_text())
        email = (data.get("user") or {}).get("email", "").strip()
        if email:
            return email
    except Exception:
        pass

    return None


def _get_push_token() -> Optional[str]:
    """
    Resolve the internal JWT for authenticated push/record calls.

    Priority order:
      1. TOKEN_FLOW_JWT env var — pre-minted JWT (headless / ECS path).
      2. Cached tf_auth.json token (device-flow login / interactive sessions).
      3. None — push goes unauthenticated; will 401 if AUTH0_DOMAIN is set.
    """
    # 1. Pre-minted JWT from env (headless / ECS / manage.sh start-poller)
    jwt_env = os.environ.get("TOKEN_FLOW_JWT", "").strip()
    if jwt_env:
        return jwt_env

    # 2. Cached device-flow token (interactive local dev / manage.sh start)
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

    global _warned_no_token
    try:
        data = payload if payload is not None else _build_snapshot(db_path)
        body = json.dumps(data).encode()
        headers = {"Content-Type": "application/json"}
        token = _get_push_token()
        if token:
            if _warned_no_token:
                log.info("push_snapshot: auth token now available — push resuming")
                _warned_no_token = False
            headers["Authorization"] = f"Bearer {token}"
        else:
            if not _warned_no_token:
                log.warning("push_snapshot: no auth token available — push will be retried silently until one is found")
                _warned_no_token = True
            # Skip the push entirely rather than sending an unauthenticated request
            # that will 401 and waste bandwidth every 30s.
            return
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

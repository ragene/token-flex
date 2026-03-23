"""
Token-data router — exposes local token_usage records to token-flow-ui.

Routes
------
  WS     /token-data/ws      — WebSocket: pushes snapshot on connect, then on every new record
  GET    /token-data/summary — aggregated usage by (operation, model)
  GET    /token-data/events  — raw event rows with optional filters
  GET    /token-data/export  — CSV download of all rows
  POST   /token-data/record  — write a single usage row + broadcast snapshot to WS clients
  POST   /token-data/push    — receive a snapshot pushed from the local token-flow client
                               and broadcast it to all connected WS clients
  POST   /token-data/distill — publish distill+clear trigger to SQS queue
  DELETE /token-data/clear   — wipe all token_usage rows
"""
from __future__ import annotations

import csv
import io
import json as _json
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from api.auth import verify_token, decode_token, get_current_user_email, AUTH0_DOMAIN
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.schema import init_db
from db.pg_compat import connect as pg_connect
from api.db_helper import get_db_url
from api.ws_manager import ws_manager

router = APIRouter(tags=["token-data"])


def _json_default(obj):
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# ── Helpers ──────────────────────────────────────────────────────────────────

log = logging.getLogger(__name__)


def _conn(request: Request):
    database_url: str = request.app.state.database_url
    c = pg_connect(database_url)
    init_db(c)
    return c


def _load_push_cache(database_url: str) -> dict | None:
    """Load the last persisted push snapshot from push_cache. Returns None if empty."""
    try:
        c = pg_connect(database_url)
        init_db(c)
        try:
            row = c.execute("SELECT payload FROM push_cache WHERE id = 1").fetchone()
            if row:
                return _json.loads(row[0])
        finally:
            c.close()
    except Exception as exc:
        log.debug("_load_push_cache failed (non-fatal): %s", exc)
    return None


def _build_tokens_and_session(database_url: str, user_email: Optional[str] = None) -> tuple[dict, dict]:
    """
    Compute the `tokens` and `session` dicts that the Dashboard WS consumer
    expects (snap.tokens / snap.session).  Mirrors the logic in the
    GET /tokens and GET /session/current HTTP endpoints so the WS snapshot
    is self-contained even without a push from the local service.
    """
    import json as _j
    from pathlib import Path

    WARN_THRESHOLD    = int(os.environ.get("SMART_MEMORY_WARN_TOKENS",   "30000"))
    DISTILL_THRESHOLD = int(os.environ.get("SMART_MEMORY_DISTILL_TOKENS", "30000"))
    SESSIONS_DIR = Path(os.environ.get(
        "SESSIONS_DIR", Path.home() / ".openclaw/agents/main/sessions"
    ))
    MEMORY_DIR = Path(os.environ.get(
        "MEMORY_DIR", str(Path.home() / ".openclaw" / "workspace" / "memory")
    ))

    def _approx(p: Path) -> int:
        try:
            return len(p.read_text(errors="ignore")) // 4
        except Exception:
            return 0

    # ── tokens ───────────────────────────────────────────────────────────────
    session_files  = list(SESSIONS_DIR.glob("*.jsonl")) if SESSIONS_DIR.exists() else []
    session_tokens = sum(_approx(f) for f in session_files)

    from datetime import date as _date
    today = _date.today().isoformat()
    mem_file = MEMORY_DIR / f"{today}.md"
    memory_tokens = _approx(mem_file) if mem_file.exists() else 0
    total = session_tokens + memory_tokens

    if total >= DISTILL_THRESHOLD:
        status = "critical"
        msg = f"⚠️ Context is very large (~{total:,} tokens). Distill NOW."
    elif total >= WARN_THRESHOLD:
        status = "warning"
        msg = f"🟡 Context growing (~{total:,} tokens). Consider distilling soon."
    else:
        status = "ok"
        msg = f"✅ Context healthy (~{total:,} tokens)."

    # Chunk cache counts (not filtered by user — chunk_cache has no user_email column)
    cached_chunks = 0
    cached_chunk_tokens = 0
    try:
        c = pg_connect(database_url)
        init_db(c)
        crow = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(token_count),0) FROM chunk_cache"
        ).fetchone()
        c.close()
        cached_chunks = int(crow[0] or 0)
        cached_chunk_tokens = int(crow[1] or 0)
    except Exception:
        pass

    tokens = {
        "total_tokens_approx":   total,
        "session_tokens":        session_tokens,
        "memory_tokens":         memory_tokens,
        "session_files":         len(session_files),
        "status":                status,
        "message":               msg,
        "warn_threshold":        WARN_THRESHOLD,
        "distill_threshold":     DISTILL_THRESHOLD,
        "cached_chunks":         cached_chunks,
        "cached_chunk_tokens":   cached_chunk_tokens,
    }

    # ── session ───────────────────────────────────────────────────────────────
    session: dict = {
        "session_id": None, "session_file": None,
        "token_count_approx": 0, "message_count": 0,
        "channel": None, "started_at": None, "last_updated_at": None,
        "user_email": None, "user_name": None,
        "user_picture": None, "user_last_seen": None,
    }
    try:
        sessions_json = SESSIONS_DIR / "sessions.json"
        if sessions_json.exists():
            meta = _j.loads(sessions_json.read_text(errors="ignore"))
            session_meta = next(iter(meta.values()), {}) if meta else {}
            sid = session_meta.get("sessionId")
            channel = (
                session_meta.get("lastChannel")
                or session_meta.get("deliveryContext", {}).get("channel")
            )
            updated_ms = session_meta.get("updatedAt")
            last_updated_at = None
            if updated_ms:
                from datetime import datetime as _dt, timezone as _tz
                last_updated_at = _dt.fromtimestamp(
                    updated_ms / 1000, tz=_tz.utc
                ).isoformat()

            session.update({"channel": channel, "last_updated_at": last_updated_at})

            if sid:
                sf = SESSIONS_DIR / f"{sid}.jsonl"
                session["session_id"] = sid
                session["session_file"] = str(sf)
                if sf.exists():
                    raw = sf.read_text(errors="ignore")
                    session["token_count_approx"] = len(raw) // 4
                    mc = 0
                    started_at = None
                    for line in raw.splitlines():
                        if not line.strip():
                            continue
                        try:
                            obj = _j.loads(line)
                        except Exception:
                            continue
                        t = obj.get("type", "")
                        if t == "session" and started_at is None:
                            started_at = obj.get("timestamp")
                        if t in ("human", "assistant", "say", "message"):
                            mc += 1
                    session["message_count"] = mc
                    session["started_at"] = started_at

        # Enrich with local_sessions identity — scoped to the requesting user.
        # When user_email is set we only look up THAT user. We deliberately do NOT
        # fall back to "most recent" — that would leak a different user's identity
        # into an authenticated user's session card.
        try:
            c2 = pg_connect(database_url)
            init_db(c2)
            if user_email:
                # Return only THIS user's local session identity
                row = c2.execute(
                    "SELECT email, name, picture, last_seen FROM local_sessions WHERE email = %s LIMIT 1",
                    (user_email,)
                ).fetchone()
            else:
                # No user filter (admin / dev mode) — return most recent
                row = c2.execute(
                    "SELECT email, name, picture, last_seen FROM local_sessions ORDER BY last_seen DESC LIMIT 1"
                ).fetchone()
            c2.close()
            if row:
                session.update({
                    "user_email":     row[0],
                    "user_name":      row[1],
                    "user_picture":   row[2],
                    "user_last_seen": str(row[3]) if row[3] else None,
                })
        except Exception:
            pass
    except Exception:
        pass

    return tokens, session


def _build_snapshot(database_url: str, user_email: Optional[str] = None) -> dict:
    """
    Read current state from DB and return a snapshot dict.
    If user_email is provided, token_usage data is filtered to that user only.

    Token/session/memory/pipeline data preference order:
      1. push_cache (pushed by local service — has real local file data)
      2. _build_tokens_and_session() fallback (reads local files, works when running locally)
    DB tables (token_usage, chunk_cache) are always read fresh from Postgres.
    """
    c = pg_connect(database_url)
    init_db(c)

    # Build WHERE clause for user filtering
    _email_filter = "WHERE user_email = ?" if user_email else ""
    _email_params = (user_email,) if user_email else ()

    try:
        # Per-operation/model summary (filtered by user)
        rows = c.execute(f"""
            SELECT operation, model,
                   COUNT(*) as total_calls,
                   COALESCE(SUM(prompt_tokens),0)     as prompt_tokens,
                   COALESCE(SUM(completion_tokens),0) as completion_tokens,
                   COALESCE(SUM(total_tokens),0)      as total_tokens,
                   COALESCE(SUM(cost_usd),0.0)        as cost_usd
            FROM token_usage
            {_email_filter}
            GROUP BY operation, model
            ORDER BY total_tokens DESC
        """, _email_params).fetchall()
        summary_rows = [dict(r) for r in rows]
        grand_tokens = sum(r["total_tokens"] for r in summary_rows)
        grand_calls  = sum(r["total_calls"]  for r in summary_rows)
        grand_cost   = round(sum(r["cost_usd"] for r in summary_rows), 6)

        # Latest 100 chunks (no user column — shared across users)
        chunk_rows = c.execute("""
            SELECT id, source_label, chunk_index, token_count,
                   composite_score, fact_score, preference_score, intent_score,
                   summary, is_summarized, created_at
            FROM chunk_cache
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()
        chunks = [dict(r) for r in chunk_rows]

        # Latest 100 token events (filtered by user)
        event_rows = c.execute(f"""
            SELECT id, user_email, operation, model,
                   prompt_tokens, completion_tokens, total_tokens,
                   cost_usd, source_label, created_at
            FROM token_usage
            {_email_filter}
            ORDER BY created_at DESC
            LIMIT 100
        """, _email_params).fetchall()
        events = [dict(r) for r in event_rows]

        # Latest 50 memory entries (not user-scoped)
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

        # Latest 50 pipeline events (not user-scoped)
        try:
            import json as _j
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
                    row["detail"] = _j.loads(row["detail"]) if row["detail"] else {}
                except Exception:
                    row["detail"] = {}
                pipeline_events.append(row)
        except Exception:
            pipeline_events = []

    finally:
        c.close()

    # Pull tokens/session/memory/pipeline from push_cache first (has real local data).
    # Fall back to building from local files (works when running the service locally).
    # Initialise tokens/session to safe defaults before the if/else so they are always
    # defined even if push_cache is empty and _build_tokens_and_session isn't called.
    tokens: dict = {}
    session: dict = {"session_id": None, "token_count_approx": 0, "message_count": 0}
    pushed = _load_push_cache(database_url)
    if pushed:
        # Never copy push_cache session wholesale — it contains the local machine
        # owner's identity. Rebuild a clean empty session shell for every user;
        # the real session data comes from the user-scoped _build_tokens_and_session
        # call in the else branch (when there is no push_cache).
        session = {"session_id": None, "token_count_approx": 0, "message_count": 0}

        # push_cache tokens = machine-level health data (session file sizes, memory
        # token counts). This is NOT per-user billing data — that's token_usage, which
        # is already scoped separately above. Show tokens to all authenticated users.
        tokens = pushed.get("tokens") or {}
        # token_usage lives in local SQLite (not Postgres), so the DB queries above
        # return empty rows.  When the DB has no data, pull summary/events from the
        # push_cache snapshot sent by the local service — but only when no user filter
        # is active (i.e. admin/unauthenticated view).  For authenticated users we
        # never fall back to the unscoped push_cache summary/events because that would
        # leak other users' data into a user-scoped session.
        # token_usage lives in local SQLite (not Postgres), so DB queries above
        # return empty when using a remote Postgres deployment.  Fall back to
        # push_cache data, but filter it to the requesting user when user_email
        # is set so one user never sees another's data.
        if not events and pushed.get("events"):
            cached_events = pushed["events"]
            if user_email:
                # Only include rows explicitly tagged to this user.
                # Rows with a blank/null user_email are genuinely unattributed
                # (old records before user tracking was added) — include them
                # only if they are the ONLY user connected, i.e. single-user mode.
                # In multi-user deployments, unattributed rows are ambiguous and
                # must not be assigned to a random authenticated user.
                events = [
                    e for e in cached_events
                    if e.get("user_email") == user_email
                ]
            else:
                events = cached_events

        if not summary_rows and pushed.get("summary"):
            if user_email:
                # Recompute summary totals from the already-filtered events list
                # so the summary card reflects only this user's usage.
                from collections import defaultdict
                agg: dict = defaultdict(lambda: {"total_calls": 0, "prompt_tokens": 0,
                                                  "completion_tokens": 0, "total_tokens": 0,
                                                  "cost_usd": 0.0})
                for e in events:
                    key = (e.get("operation", ""), e.get("model", ""))
                    agg[key]["total_calls"]        += 1
                    agg[key]["prompt_tokens"]      += e.get("prompt_tokens") or 0
                    agg[key]["completion_tokens"]  += e.get("completion_tokens") or 0
                    agg[key]["total_tokens"]       += e.get("total_tokens") or 0
                    agg[key]["cost_usd"]           += e.get("cost_usd") or 0.0
                summary_rows = [
                    {"operation": k[0], "model": k[1], **v} for k, v in agg.items()
                ]
                grand_tokens = sum(r["total_tokens"] for r in summary_rows)
                grand_calls  = sum(r["total_calls"]  for r in summary_rows)
                grand_cost   = round(sum(r["cost_usd"] for r in summary_rows), 6)
            else:
                # No user filter — serve the full unscoped push_cache summary (admin view)
                pushed_summary = pushed["summary"]
                summary_rows   = pushed_summary.get("rows", summary_rows)
                grand_tokens   = pushed_summary.get("grand_total_tokens", grand_tokens)
                grand_calls    = pushed_summary.get("grand_total_calls",  grand_calls)
                grand_cost     = pushed_summary.get("grand_cost_usd",     grand_cost)
        # Only fall back to DB memory/pipeline rows if push didn't include them
        if not memory_entries:
            memory_entries  = pushed.get("memory_entries")  or memory_entries
        if not pipeline_events:
            pipeline_events = pushed.get("pipeline_events") or pipeline_events
        # chunk_cache in remote Postgres is always empty — the local pipeline writes
        # to SQLite only and pushes chunks as part of the snapshot payload.
        # Use push_cache chunks as the source of truth when the DB has none.
        if not chunks and pushed.get("chunks"):
            chunks = pushed["chunks"]
    else:
        tokens, session = _build_tokens_and_session(database_url, user_email=user_email)

    # Attach live chunk cache count to tokens dict (always from DB — most current)
    if tokens:
        tokens["cached_chunks"]       = len(chunks)
        tokens["cached_chunk_tokens"] = sum(c.get("token_count") or 0 for c in chunks)

    return {
        "ts": datetime.utcnow().isoformat() + "Z",
        "tokens":  tokens,
        "session": session,
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


# ── Pydantic models ───────────────────────────────────────────────────────────

class TokenUsageIn(BaseModel):
    user_email: Optional[str] = None
    operation: str
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    source_label: Optional[str] = None


class TokenUsageOut(BaseModel):
    id: int
    user_email: Optional[str]
    operation: str
    model: Optional[str]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: Optional[float]
    source_label: Optional[str]
    created_at: Optional[str]


class TokenSummaryRow(BaseModel):
    operation: str
    model: Optional[str]
    total_calls: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class TokenSummaryResponse(BaseModel):
    rows: List[TokenSummaryRow]
    grand_total_tokens: int
    grand_total_calls: int
    grand_cost_usd: float


class PushSnapshotIn(BaseModel):
    """
    Payload posted by the local token-flow client to push a full snapshot
    (token data + chunks + summaries) to all connected UI clients.
    """
    ts: Optional[str] = None
    summary: Optional[dict] = None
    chunks: Optional[list] = None
    events: Optional[list] = None
    # Allow arbitrary extra fields for forward-compatibility
    class Config:
        extra = "allow"


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@router.websocket("/token-data/ws")
async def token_data_ws(websocket: WebSocket, token: Optional[str] = None) -> None:
    """
    WebSocket endpoint for the token-flow-ui.

    - Authenticates via ?token=<jwt> query param (browsers can't set WS headers).
    - When authenticated, all snapshot data is filtered to the requesting user's email.
    - On connect: immediately sends the current user-scoped snapshot (fresh, not cached).
    - Every PUSH_INTERVAL seconds: server proactively pushes a fresh user-scoped snapshot.
    - Clients can also send any text (e.g. "ping") to request an immediate refresh.
    """
    import asyncio

    PUSH_INTERVAL = 10  # seconds between server-initiated pushes

    # Authenticate — require token when AUTH0_DOMAIN is configured
    user_email: Optional[str] = None
    if AUTH0_DOMAIN:
        if not token:
            await websocket.close(code=4401, reason="Authorization required")
            return
        try:
            payload = decode_token(token)
            user_email = payload.get("email")
        except Exception:
            await websocket.close(code=4401, reason="Invalid token")
            return
        # Defensive: if auth is required but email is missing from JWT, reject rather
        # than falling through with user_email=None which would expose all users' data.
        if not user_email:
            await websocket.close(code=4401, reason="Token missing email claim")
            return

    database_url: str = websocket.app.state.database_url
    await ws_manager.connect(websocket, user_email=user_email)
    try:
        # Send initial snapshot on connect (uses push_cache if available).
        initial = _build_snapshot(database_url, user_email=user_email)
        await websocket.send_text(_json.dumps(initial, default=_json_default))

        # If push_cache was empty (first connect after cold start), kick the local
        # push loop to fire immediately so the client gets real data fast.
        if _load_push_cache(database_url) is None:
            try:
                import threading
                from api.push_client import push_snapshot as _push_now
                threading.Thread(
                    target=_push_now, args=(database_url,), daemon=True
                ).start()
            except Exception:
                pass

        # Wait for client pings or disconnect.
        # We do NOT run a server-initiated periodic push loop here — the local service
        # already pushes a fresh snapshot every 30s via POST /token-data/push, which
        # broadcasts to all connected WS clients via ws_manager.broadcast().
        # A server-side poll loop would race against that push and overwrite good
        # push_cache data with stale ECS-local file reads.
        while True:
            try:
                msg = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                # Client sent a ping — reply with a fresh snapshot
                snapshot = _build_snapshot(database_url, user_email=user_email)
                await websocket.send_text(_json.dumps(snapshot, default=_json_default))
            except asyncio.TimeoutError:
                # Send a keepalive ping to prevent proxy/LB idle timeout
                try:
                    await websocket.send_text(_json.dumps({"ts": datetime.utcnow().isoformat() + "Z", "keepalive": True}, default=_json_default))
                except Exception:
                    break
            except WebSocketDisconnect:
                break
    finally:
        ws_manager.disconnect(websocket)


# ── Push endpoint (called by local token-flow client) ─────────────────────────

@router.post("/token-data/push", status_code=200, dependencies=[Depends(verify_token)])
async def push_snapshot(body: PushSnapshotIn, request: Request) -> dict:
    """
    Receive a snapshot from the local token-flow client, persist it to
    push_cache (survives restarts), and broadcast to all connected WS clients.
    """
    payload = body.model_dump(exclude_none=False)
    if not payload.get("ts"):
        payload["ts"] = datetime.utcnow().isoformat() + "Z"

    # Persist to DB so new WS connections get real data after ECS restarts.
    # UPDATE first (no gap), INSERT if no row exists yet.
    try:
        database_url: str = request.app.state.database_url
        conn = _conn(request)
        try:
            payload_json = _json.dumps(payload)
            conn.execute(
                """INSERT INTO push_cache (id, payload, updated_at) VALUES (1, ?, NOW())
                   ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()""",
                (payload_json,),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("push_cache persist FAILED: %s", exc, exc_info=True)

    # Notify each client with their own user-scoped snapshot rather than
    # broadcasting the shared push payload — prevents data cross-contamination
    # between users when multiple people are connected simultaneously.
    database_url: str = request.app.state.database_url
    await ws_manager.notify(lambda email: _build_snapshot(database_url, user_email=email), require_email=bool(AUTH0_DOMAIN))
    return {"ok": True, "clients_notified": ws_manager.connection_count}


# ── Record endpoint ───────────────────────────────────────────────────────────

@router.post("/token-data/record", response_model=TokenUsageOut, status_code=201)
async def record_usage(body: TokenUsageIn, request: Request, token_payload: Optional[dict] = Depends(verify_token)) -> TokenUsageOut:
    """Write a single AI call token-usage row and broadcast a fresh snapshot to WS clients."""
    total = body.total_tokens if body.total_tokens is not None else (body.prompt_tokens + body.completion_tokens)
    # Auto-populate user_email from JWT when not provided in body — prevents NULL
    # user_email which causes the user-scoped summary/events endpoints to return nothing.
    user_email = body.user_email
    if user_email is None:
        user_email = get_current_user_email(token_payload)
    database_url: str = get_db_url(request)
    conn = _conn(request)
    try:
        cur = conn.execute(
            """INSERT INTO token_usage
               (user_email, operation, model, prompt_tokens, completion_tokens,
                total_tokens, cost_usd, source_label)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_email, body.operation, body.model,
             body.prompt_tokens, body.completion_tokens,
             total, body.cost_usd, body.source_label),
        )
        conn.commit()
        row_id = cur.lastrowid
        row = conn.execute("SELECT * FROM token_usage WHERE id=?", (row_id,)).fetchone()
        out = _row_out(row)
    finally:
        conn.close()

    # Notify each connected WS client with their own scoped snapshot
    if ws_manager.connection_count > 0:
        await ws_manager.notify(lambda email: _build_snapshot(database_url, user_email=email), require_email=bool(AUTH0_DOMAIN))

    return out


# ── Summary endpoint ──────────────────────────────────────────────────────────

@router.get("/token-data/summary", response_model=TokenSummaryResponse)
async def token_summary(request: Request, token_payload: Optional[dict] = Depends(verify_token)) -> TokenSummaryResponse:
    """Aggregated totals grouped by operation + model, scoped to the requesting user."""
    user_email = get_current_user_email(token_payload)
    email_filter = "WHERE user_email = ?" if user_email else ""
    email_params = (user_email,) if user_email else ()
    conn = _conn(request)
    try:
        rows = conn.execute(f"""
            SELECT
                operation,
                model,
                COUNT(*) as total_calls,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cost_usd), 0.0) as cost_usd
            FROM token_usage
            {email_filter}
            GROUP BY operation, model
            ORDER BY total_tokens DESC
        """, email_params).fetchall()
        summary_rows = [
            TokenSummaryRow(
                operation=r["operation"],
                model=r["model"],
                total_calls=r["total_calls"],
                prompt_tokens=r["prompt_tokens"],
                completion_tokens=r["completion_tokens"],
                total_tokens=r["total_tokens"],
                cost_usd=round(r["cost_usd"], 6),
            )
            for r in rows
        ]
        grand_tokens = sum(r.total_tokens for r in summary_rows)
        grand_calls  = sum(r.total_calls  for r in summary_rows)
        grand_cost   = round(sum(r.cost_usd for r in summary_rows), 6)
        return TokenSummaryResponse(
            rows=summary_rows,
            grand_total_tokens=grand_tokens,
            grand_total_calls=grand_calls,
            grand_cost_usd=grand_cost,
        )
    finally:
        conn.close()


# ── Events endpoint ───────────────────────────────────────────────────────────

@router.get("/token-data/events", response_model=List[TokenUsageOut])
async def list_events(
    request: Request,
    token_payload: Optional[dict] = Depends(verify_token),
    operation: Optional[str] = Query(None),
    model:     Optional[str] = Query(None),
    limit:     int           = Query(200, ge=1, le=1000),
    offset:    int           = Query(0,   ge=0),
) -> List[TokenUsageOut]:
    """Raw event rows, newest first, scoped to the requesting user."""
    user_email = get_current_user_email(token_payload)
    conn = _conn(request)
    try:
        conditions, params = [], []
        if user_email:
            conditions.append("user_email = ?"); params.append(user_email)
        if operation:
            conditions.append("operation = ?"); params.append(operation)
        if model:
            conditions.append("model = ?"); params.append(model)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params += [limit, offset]
        rows = conn.execute(
            f"SELECT * FROM token_usage {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_row_out(r) for r in rows]
    finally:
        conn.close()


# ── Export endpoint ───────────────────────────────────────────────────────────

@router.get("/token-data/export")
async def export_csv(request: Request, token_payload: Optional[dict] = Depends(verify_token)) -> StreamingResponse:
    """Download token_usage rows as CSV, scoped to the requesting user."""
    user_email = get_current_user_email(token_payload)
    email_filter = "WHERE user_email = ?" if user_email else ""
    email_params = (user_email,) if user_email else ()
    conn = _conn(request)
    try:
        rows = conn.execute(
            f"SELECT * FROM token_usage {email_filter} ORDER BY created_at DESC",
            email_params,
        ).fetchall()
    finally:
        conn.close()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id","user_email","operation","model",
                "prompt_tokens","completion_tokens","total_tokens",
                "cost_usd","source_label","created_at"])
    for r in rows:
        w.writerow([
            r["id"], r["user_email"], r["operation"], r["model"],
            r["prompt_tokens"], r["completion_tokens"], r["total_tokens"],
            r["cost_usd"], r["source_label"], r["created_at"],
        ])
    buf.seek(0)
    filename = f"token-usage-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Distill trigger endpoint ──────────────────────────────────────────────────

SQS_QUEUE_URL = os.environ.get(
    "MEMORY_DISTILL_QUEUE_URL",
    "https://sqs.us-west-2.amazonaws.com/531948420901/freightdawg-memory-distill",
)
AWS_REGION = os.environ.get("AWS_REGION", "us-west-2")


@router.post("/token-data/distill", status_code=202)
async def trigger_distill(request: Request, token_payload: Optional[dict] = Depends(verify_token)) -> dict:
    """
    Publish a distill_and_clear message to the SQS queue.
    The local smart-memory service polls this queue and runs memory_distill.py full + clears token_usage.
    user_email is extracted from the JWT so the clear only affects the triggering user's rows.

    SECURITY: scoped (non-owner) users can only clear their own token_usage rows.
    The SQS message action is set to "clear_tokens_only" so the poller skips the
    local memory distillation step (which would rewrite the machine owner's files).
    Only the machine owner (no user_email, i.e. admin / unauthenticated mode) may
    trigger a full distill_and_clear that also rewrites memory on disk.
    """
    user_email = get_current_user_email(token_payload)

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    # triggered_by falls back to the JWT email so attribution is always accurate
    triggered_by = (body.get("triggered_by") or user_email or "unknown").strip()

    # Scoped users can only clear their own token_usage rows — not the owner's memory.
    action = "distill_and_clear" if not user_email else "clear_tokens_only"

    try:
        import boto3
        sqs = boto3.client("sqs", region_name=AWS_REGION)
        message = {
            "action": action,
            "requested_at": datetime.utcnow().isoformat(),
            "triggered_by": triggered_by,
            "user_email": user_email,   # scopes the token_usage DELETE to this user
        }
        resp = sqs.send_message(
            QueueUrl=SQS_QUEUE_URL,
            MessageBody=_json.dumps(message),
        )
        log.info("Distill trigger sent to SQS by %s action=%s (MessageId=%s)", triggered_by, action, resp.get("MessageId"))
        return {
            "status": "queued",
            "action": action,
            "message_id": resp.get("MessageId"),
            "requested_at": message["requested_at"],
            "triggered_by": triggered_by,
            "user_email": user_email,
        }
    except Exception as exc:
        log.error("SQS send_message failed: %s", exc)
        from fastapi import HTTPException
        raise HTTPException(status_code=502, detail=f"SQS error: {exc}")


@router.delete("/token-data/clear", status_code=200)
async def clear_token_usage(request: Request, token_payload: Optional[dict] = Depends(verify_token)) -> dict:
    """
    Delete token_usage rows for the requesting user only.
    Called by the local service after distillation completes, or manually from the dashboard.

    Scoping logic (first match wins):
      1. ``scoped_user_email`` in the JSON request body — used by the SQS poller
         which authenticates with a service JWT but wants to clear a specific user's rows.
      2. ``user_email`` from the JWT payload — used by browser clients.
      3. None → clears all rows (dev mode / no auth).

    A service JWT (shared TOKEN_FLOW_AUTH_TOKEN) returns token_payload=None from
    verify_token, so without body param #1 it would clear ALL rows.  Always pass
    ``scoped_user_email`` from the poller to avoid that.
    """
    # Try to pull scoped_user_email from body (poller path)
    scoped_user_email: Optional[str] = None
    try:
        body = await request.json()
        scoped_user_email = (body.get("scoped_user_email") or "").strip() or None
    except Exception:
        pass

    user_email = scoped_user_email or get_current_user_email(token_payload)
    database_url: str = request.app.state.database_url
    conn = _conn(request)

    email_filter = "WHERE user_email = ?" if user_email else ""
    email_params = (user_email,) if user_email else ()

    try:
        count = conn.execute(f"SELECT COUNT(*) FROM token_usage {email_filter}", email_params).fetchone()[0]
        conn.execute(f"DELETE FROM token_usage {email_filter}", email_params)
        conn.commit()
        log.info("Cleared %d token_usage rows", count)
    finally:
        conn.close()

    # Patch push_cache so the next WS snapshot reflects the clear immediately,
    # without waiting for the next local push cycle.
    # IMPORTANT: only remove the cleared user's rows from push_cache events/summary.
    # Zeroing the entire push_cache would wipe OTHER users' data from their views.
    try:
        cached = _load_push_cache(database_url)
        if cached:
            cached_events = cached.get("events") or []
            if user_email:
                # Remove only this user's events; leave other users' rows intact.
                cached["events"] = [
                    e for e in cached_events
                    if e.get("user_email") != user_email
                ]
            else:
                # Admin/no-auth clear — wipe everything.
                cached["events"] = []

            # Recompute summary from the surviving events so grand totals stay correct.
            surviving = cached["events"]
            from collections import defaultdict
            agg: dict = defaultdict(lambda: {"total_calls": 0, "prompt_tokens": 0,
                                              "completion_tokens": 0, "total_tokens": 0,
                                              "cost_usd": 0.0})
            for e in surviving:
                key = (e.get("operation", ""), e.get("model", ""))
                agg[key]["total_calls"]       += 1
                agg[key]["prompt_tokens"]     += e.get("prompt_tokens") or 0
                agg[key]["completion_tokens"] += e.get("completion_tokens") or 0
                agg[key]["total_tokens"]      += e.get("total_tokens") or 0
                agg[key]["cost_usd"]          += e.get("cost_usd") or 0.0
            summary_rows_patched = [
                {"operation": k[0], "model": k[1], **v} for k, v in agg.items()
            ]
            cached["summary"] = {
                "rows": summary_rows_patched,
                "grand_total_tokens": sum(r["total_tokens"] for r in summary_rows_patched),
                "grand_total_calls":  sum(r["total_calls"]  for r in summary_rows_patched),
                "grand_cost_usd":     round(sum(r["cost_usd"] for r in summary_rows_patched), 6),
            }
            cached["ts"] = datetime.utcnow().isoformat() + "Z"

            conn2 = _conn(request)
            try:
                payload_json = _json.dumps(cached)
                conn2.execute(
                    """INSERT INTO push_cache (id, payload, updated_at) VALUES (1, ?, NOW())
                       ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()""",
                    (payload_json,),
                )
                conn2.commit()
            finally:
                conn2.close()
    except Exception as exc:
        log.debug("push_cache patch after clear failed (non-fatal): %s", exc)

    # Notify each connected WS client with their own scoped snapshot
    await ws_manager.notify(lambda email: _build_snapshot(database_url, user_email=email), require_email=bool(AUTH0_DOMAIN))

    return {"status": "cleared", "rows_deleted": count}


# ── Internal helper ───────────────────────────────────────────────────────────

def _row_out(r) -> TokenUsageOut:
    created = r["created_at"]
    if created is not None and not isinstance(created, str):
        created = created.isoformat()
    return TokenUsageOut(
        id=r["id"],
        user_email=r["user_email"],
        operation=r["operation"],
        model=r["model"],
        prompt_tokens=r["prompt_tokens"] or 0,
        completion_tokens=r["completion_tokens"] or 0,
        total_tokens=r["total_tokens"] or 0,
        cost_usd=r["cost_usd"],
        source_label=r["source_label"],
        created_at=created,
    )

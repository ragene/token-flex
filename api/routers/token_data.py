"""
Token-data router — exposes local token_usage records to token-flow-ui.

Routes
------
  WS   /token-data/ws      — WebSocket: pushes snapshot on connect, then on every new record
  GET  /token-data/summary — aggregated usage by (operation, model)
  GET  /token-data/events  — raw event rows with optional filters
  GET  /token-data/export  — CSV download of all rows
  POST /token-data/record  — write a single usage row + broadcast snapshot to WS clients
  POST /token-data/push    — receive a snapshot pushed from the local token-flow client
                             and broadcast it to all connected WS clients
"""
from __future__ import annotations

import csv
import io
import json as _json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
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

def _conn(request: Request):
    database_url: str = request.app.state.database_url
    c = pg_connect(database_url)
    init_db(c)
    return c


def _build_snapshot(database_url: str) -> dict:
    """Read current state from DB and return a snapshot dict."""
    c = pg_connect(database_url)
    init_db(c)
    try:
        # Per-operation/model summary
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
async def token_data_ws(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for the token-flow-ui.

    - On connect: immediately sends the current DB snapshot.
    - On new record (POST /token-data/record): broadcasts fresh snapshot to all clients.
    - On push (POST /token-data/push): broadcasts the pushed snapshot to all clients.

    Clients can also send any text message (e.g. a ping) and will receive
    an up-to-date snapshot in reply.
    """
    database_url: str = websocket.app.state.database_url
    await ws_manager.connect(websocket)
    try:
        # Send initial snapshot immediately on connect
        snapshot = _build_snapshot(database_url)
        await websocket.send_text(_json.dumps(snapshot, default=_json_default))

        # Keep the connection alive; respond to any client message with a fresh snapshot
        while True:
            try:
                _ = await websocket.receive_text()
                snapshot = _build_snapshot(db_path)
                await websocket.send_text(_json.dumps(snapshot, default=_json_default))
            except WebSocketDisconnect:
                break
    finally:
        ws_manager.disconnect(websocket)


# ── Push endpoint (called by local token-flow client) ─────────────────────────

@router.post("/token-data/push", status_code=200)
async def push_snapshot(body: PushSnapshotIn) -> dict:
    """
    Receive a snapshot from the local token-flow client and broadcast it
    to all connected WebSocket (UI) clients.

    If no WS clients are connected the push is a no-op (data is still
    readable via /token-data/summary and /token-data/events).
    """
    payload = body.model_dump(exclude_none=False)
    if not payload.get("ts"):
        payload["ts"] = datetime.utcnow().isoformat() + "Z"

    await ws_manager.broadcast(payload)
    return {"ok": True, "clients_notified": ws_manager.connection_count}


# ── Record endpoint ───────────────────────────────────────────────────────────

@router.post("/token-data/record", response_model=TokenUsageOut, status_code=201)
async def record_usage(body: TokenUsageIn, request: Request) -> TokenUsageOut:
    """Write a single AI call token-usage row and broadcast a fresh snapshot to WS clients."""
    total = body.total_tokens if body.total_tokens is not None else (body.prompt_tokens + body.completion_tokens)
    database_url: str = get_db_url(request)
    conn = _conn(request)
    try:
        cur = conn.execute(
            """INSERT INTO token_usage
               (user_email, operation, model, prompt_tokens, completion_tokens,
                total_tokens, cost_usd, source_label)
               VALUES (?,?,?,?,?,?,?,?)""",
            (body.user_email, body.operation, body.model,
             body.prompt_tokens, body.completion_tokens,
             total, body.cost_usd, body.source_label),
        )
        conn.commit()
        row_id = cur.lastrowid
        row = conn.execute("SELECT * FROM token_usage WHERE id=?", (row_id,)).fetchone()
        out = _row_out(row)
    finally:
        conn.close()

    # Broadcast updated snapshot to all connected WS clients
    if ws_manager.connection_count > 0:
        snapshot = _build_snapshot(database_url)
        await ws_manager.broadcast(snapshot)

    return out


# ── Summary endpoint ──────────────────────────────────────────────────────────

@router.get("/token-data/summary", response_model=TokenSummaryResponse)
async def token_summary(request: Request) -> TokenSummaryResponse:
    """Aggregated totals grouped by operation + model."""
    conn = _conn(request)
    try:
        rows = conn.execute("""
            SELECT
                operation,
                model,
                COUNT(*) as total_calls,
                COALESCE(SUM(prompt_tokens), 0) as prompt_tokens,
                COALESCE(SUM(completion_tokens), 0) as completion_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(cost_usd), 0.0) as cost_usd
            FROM token_usage
            GROUP BY operation, model
            ORDER BY total_tokens DESC
        """).fetchall()
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
    operation: Optional[str] = Query(None),
    model:     Optional[str] = Query(None),
    limit:     int           = Query(200, ge=1, le=1000),
    offset:    int           = Query(0,   ge=0),
) -> List[TokenUsageOut]:
    """Raw event rows, newest first."""
    conn = _conn(request)
    try:
        conditions, params = [], []
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
async def export_csv(request: Request) -> StreamingResponse:
    """Download all token_usage rows as CSV."""
    conn = _conn(request)
    try:
        rows = conn.execute(
            "SELECT * FROM token_usage ORDER BY created_at DESC"
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


# ── Internal helper ───────────────────────────────────────────────────────────

def _row_out(r) -> TokenUsageOut:
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
        created_at=r["created_at"],
    )

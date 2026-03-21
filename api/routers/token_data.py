"""
Token-data router — exposes local token_usage records to token-flow-ui.

Routes
------
  GET  /token-data/summary   — aggregated usage by (operation, model)
  GET  /token-data/events    — raw event rows with optional filters
  GET  /token-data/export    — CSV download of all rows
  POST /token-data/record    — write a single usage row (called by any local AI service)
"""
from __future__ import annotations

import csv
import io
import sqlite3
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from db.schema import init_db

router = APIRouter(tags=["token-data"])

# ── Helpers ──────────────────────────────────────────────────────────────────

def _conn(request: Request) -> sqlite3.Connection:
    db_path: str = request.app.state.db_path
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    init_db(c)
    return c


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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/token-data/record", response_model=TokenUsageOut, status_code=201)
async def record_usage(body: TokenUsageIn, request: Request) -> TokenUsageOut:
    """Write a single AI call token-usage row."""
    total = body.total_tokens if body.total_tokens is not None else (body.prompt_tokens + body.completion_tokens)
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
        return _row_out(row)
    finally:
        conn.close()


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

def _row_out(r: sqlite3.Row) -> TokenUsageOut:
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

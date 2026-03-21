"""
POST /summarize   — run the summarization pipeline (optionally push to S3)
GET  /summaries   — list summarized chunks
"""
from __future__ import annotations

import os
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from engine.summarizer import summarize_top_chunks
from engine.s3_uploader import push_summaries_to_s3

router = APIRouter(tags=["summaries"])


# ── Request / Response models ────────────────────────────────────────────────

class SummarizeRequest(BaseModel):
    top_pct: float = 0.4
    push_to_s3: bool = False
    context_hint: str = ""


class SummarizeResponse(BaseModel):
    summarized: int
    pushed: int


class SummaryOut(BaseModel):
    id: int
    source_label: Optional[str]
    chunk_index: int
    summary: str
    composite_score: float
    pushed_to_s3_at: Optional[str]
    s3_key: Optional[str]
    created_at: Optional[str]


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/summarize", response_model=SummarizeResponse)
async def run_summarize(body: SummarizeRequest, request: Request) -> SummarizeResponse:
    """
    Summarize the top *top_pct* fraction of unsummarized chunks.
    Optionally push completed summaries to S3.
    """
    if not (0.0 < body.top_pct <= 1.0):
        raise HTTPException(status_code=422, detail="top_pct must be between 0.0 (exclusive) and 1.0.")

    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    try:
        summarized = summarize_top_chunks(
            conn,
            top_pct=body.top_pct,
            context_hint=body.context_hint,
        )

        pushed = 0
        if body.push_to_s3:
            bucket = os.environ.get("S3_BUCKET", "")
            if not bucket:
                raise HTTPException(
                    status_code=500,
                    detail="S3_BUCKET environment variable is not set.",
                )
            pushed = push_summaries_to_s3(conn, bucket=bucket)
    finally:
        conn.close()

    return SummarizeResponse(summarized=summarized, pushed=pushed)


@router.get("/summaries", response_model=List[SummaryOut])
async def list_summaries(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    source: Optional[str] = Query(default=None, description="Filter by source_label (prefix match)"),
) -> List[SummaryOut]:
    """Return summarized chunks ordered by composite_score descending."""
    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    try:
        conditions = ["is_summarized = 1", "summary IS NOT NULL"]
        params: list = []

        if source:
            conditions.append("source_label LIKE ?")
            params.append(f"{source}%")

        where = "WHERE " + " AND ".join(conditions)
        sql = f"""
            SELECT id, source_label, chunk_index, summary, composite_score,
                   pushed_to_s3_at, s3_key, created_at
            FROM chunk_cache
            {where}
            ORDER BY composite_score DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [
        SummaryOut(
            id=r[0],
            source_label=r[1],
            chunk_index=r[2],
            summary=r[3],
            composite_score=r[4] or 0.0,
            pushed_to_s3_at=r[5],
            s3_key=r[6],
            created_at=r[7],
        )
        for r in rows
    ]

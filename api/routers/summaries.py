"""
POST /summarize   — run the summarization pipeline (optionally push to S3)
GET  /summaries   — list summarized chunks
"""
from __future__ import annotations

import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from api.auth import verify_token
from pydantic import BaseModel

from engine.summarizer import summarize_top_chunks
from engine.s3_uploader import push_summaries_to_s3
from api.push_client import push_snapshot
from api.db_helper import get_conn, get_db_url

router = APIRouter(tags=["summaries"], dependencies=[Depends(verify_token)])


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


@router.post("/summarize", response_model=SummarizeResponse)
async def run_summarize(body: SummarizeRequest, request: Request) -> SummarizeResponse:
    if not (0.0 < body.top_pct <= 1.0):
        raise HTTPException(status_code=422, detail="top_pct must be between 0.0 (exclusive) and 1.0.")

    database_url: str = get_db_url(request)
    conn = get_conn(request)
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

    if summarized > 0:
        push_snapshot(database_url)

    return SummarizeResponse(summarized=summarized, pushed=pushed)


@router.get("/summaries", response_model=List[SummaryOut])
async def list_summaries(
    request: Request,
    limit: int = Query(default=50, ge=1, le=500),
    source: Optional[str] = Query(default=None),
) -> List[SummaryOut]:
    conn = get_conn(request)
    try:
        conditions = ["is_summarized = 1", "summary IS NOT NULL"]
        params: list = []

        if source:
            conditions.append("source_label LIKE %s")
            params.append(f"{source}%")

        where = "WHERE " + " AND ".join(conditions)
        sql = f"""
            SELECT id, source_label, chunk_index, summary, composite_score,
                   pushed_to_s3_at, s3_key, created_at
            FROM chunk_cache
            {where}
            ORDER BY composite_score DESC
            LIMIT %s
        """
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [
        SummaryOut(
            id=r["id"],
            source_label=r["source_label"],
            chunk_index=r["chunk_index"],
            summary=r["summary"],
            composite_score=r["composite_score"] or 0.0,
            pushed_to_s3_at=str(r["pushed_to_s3_at"]) if r["pushed_to_s3_at"] else None,
            s3_key=r["s3_key"],
            created_at=str(r["created_at"]) if r["created_at"] else None,
        )
        for r in rows
    ]

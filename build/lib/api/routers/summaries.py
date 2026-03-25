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

    if rows:
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

    # chunk_cache in remote Postgres is always empty — the local pipeline writes to
    # SQLite only and syncs via push_snapshot.  Fall back to push_cache chunks so
    # the Summaries page shows data after a distill+clear cycle.
    try:
        from db.pg_compat import connect as pg_connect
        from api.db_helper import get_db_url
        import json as _json
        database_url: str = get_db_url(request)
        c2 = pg_connect(database_url)
        try:
            row = c2.execute("SELECT payload FROM push_cache WHERE id = 1").fetchone()
        finally:
            c2.close()
        if row:
            pushed = _json.loads(row[0])
            cached_chunks = pushed.get("chunks") or []
            # Filter to summarized chunks only; apply source filter if given
            filtered = [
                c for c in cached_chunks
                if c.get("is_summarized") and c.get("summary")
                and (not source or str(c.get("source_label", "")).startswith(source))
            ]
            filtered.sort(key=lambda c: c.get("composite_score") or 0.0, reverse=True)
            filtered = filtered[:limit]
            return [
                SummaryOut(
                    id=c.get("id", 0),
                    source_label=c.get("source_label"),
                    chunk_index=c.get("chunk_index", 0),
                    summary=c.get("summary", ""),
                    composite_score=float(c.get("composite_score") or 0.0),
                    pushed_to_s3_at=str(c["pushed_to_s3_at"]) if c.get("pushed_to_s3_at") else None,
                    s3_key=c.get("s3_key"),
                    created_at=str(c["created_at"]) if c.get("created_at") else None,
                )
                for c in filtered
            ]
    except Exception:
        pass

    return []

"""
GET /chunks        — list chunks with optional filters
GET /chunks/{id}   — single chunk by primary key
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(tags=["chunks"])


class TokenStats(BaseModel):
    total_chunks: int
    total_tokens: int
    summarized_chunks: int
    unsummarized_chunks: int
    pushed_to_s3: int
    pending_push: int
    avg_tokens_per_chunk: float
    by_source: list


class ChunkOut(BaseModel):
    id: int
    source_id: Optional[int]
    source_label: Optional[str]
    chunk_index: int
    content: str
    token_count: Optional[int]
    fact_score: float
    preference_score: float
    intent_score: float
    composite_score: float
    summary: Optional[str]
    is_summarized: int
    pushed_to_s3_at: Optional[str]
    s3_key: Optional[str]
    created_at: Optional[str]


_SELECT_ALL = """
    SELECT
        id, source_id, source_label, chunk_index, content, token_count,
        fact_score, preference_score, intent_score, composite_score,
        summary, is_summarized, pushed_to_s3_at, s3_key, created_at
    FROM chunk_cache
"""


def _row_to_chunk(row: tuple) -> ChunkOut:
    (
        row_id, source_id, source_label, chunk_index, content, token_count,
        fact_score, preference_score, intent_score, composite_score,
        summary, is_summarized, pushed_to_s3_at, s3_key, created_at,
    ) = row
    return ChunkOut(
        id=row_id,
        source_id=source_id,
        source_label=source_label,
        chunk_index=chunk_index,
        content=content,
        token_count=token_count,
        fact_score=fact_score or 0.0,
        preference_score=preference_score or 0.0,
        intent_score=intent_score or 0.0,
        composite_score=composite_score or 0.0,
        summary=summary,
        is_summarized=is_summarized or 0,
        pushed_to_s3_at=pushed_to_s3_at,
        s3_key=s3_key,
        created_at=created_at,
    )


@router.get("/tokens", response_model=TokenStats)
async def token_stats(request: Request) -> TokenStats:
    """Return current token count and chunk breakdown across the cache."""
    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("""
            SELECT
                COUNT(*),
                COALESCE(SUM(token_count), 0),
                COALESCE(SUM(CASE WHEN is_summarized=1 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN is_summarized=0 THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN pushed_to_s3_at IS NOT NULL THEN 1 ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN is_summarized=1 AND pushed_to_s3_at IS NULL THEN 1 ELSE 0 END), 0)
            FROM chunk_cache
        """).fetchone()

        by_source = conn.execute("""
            SELECT source_label, COUNT(*), COALESCE(SUM(token_count), 0)
            FROM chunk_cache
            GROUP BY source_label
            ORDER BY SUM(token_count) DESC
            LIMIT 20
        """).fetchall()
    finally:
        conn.close()

    total_chunks = row[0] or 0
    total_tokens = row[1] or 0
    avg = round(total_tokens / total_chunks, 1) if total_chunks else 0.0

    return TokenStats(
        total_chunks=total_chunks,
        total_tokens=total_tokens,
        summarized_chunks=row[2] or 0,
        unsummarized_chunks=row[3] or 0,
        pushed_to_s3=row[4] or 0,
        pending_push=row[5] or 0,
        avg_tokens_per_chunk=avg,
        by_source=[{"source": r[0], "chunks": r[1], "tokens": r[2]} for r in by_source],
    )


@router.get("/chunks", response_model=List[ChunkOut])
async def list_chunks(
    request: Request,
    min_score: float = Query(default=0.0, ge=0.0, le=1.0, description="Minimum composite_score"),
    limit: int = Query(default=50, ge=1, le=500),
    source: Optional[str] = Query(default=None, description="Filter by source_label (prefix match)"),
) -> List[ChunkOut]:
    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    try:
        conditions = ["composite_score >= ?"]
        params: list = [min_score]

        if source:
            conditions.append("source_label LIKE ?")
            params.append(f"{source}%")

        where = "WHERE " + " AND ".join(conditions)
        sql = f"{_SELECT_ALL} {where} ORDER BY composite_score DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [_row_to_chunk(r) for r in rows]


@router.get("/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: int, request: Request) -> ChunkOut:
    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            f"{_SELECT_ALL} WHERE id = ?", (chunk_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found.")

    return _row_to_chunk(row)

"""
POST /ingest

Accepts raw text or a file path, chunks it, scores each chunk,
and persists to chunk_cache.

source_type options:
  "raw"         — (default) chunk the provided text directly
  "memory_file" — ingest via ingest_memory_file
  "session"     — ingest via ingest_session_file
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from api.auth import verify_token
from pydantic import BaseModel

from db.schema import init_db
from engine.chunker import chunk_text
from engine.scorer import score_chunks
from api.push_client import push_snapshot
from api.db_helper import get_conn, get_db_url

router = APIRouter(tags=["ingest"], dependencies=[Depends(verify_token)])


class IngestRequest(BaseModel):
    source: str
    text: Optional[str] = None
    context_hint: str = ""
    source_type: Literal["raw", "memory_file", "session"] = "raw"


class IngestResponse(BaseModel):
    chunks_created: int
    avg_composite_score: float


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest, request: Request) -> IngestResponse:
    database_url: str = get_db_url(request)

    if body.source_type in ("memory_file", "session"):
        from engine.ingestor import ingest_memory_file, ingest_session_file

        filepath = Path(body.source)
        if not filepath.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Source file not found: {body.source}",
            )

        conn = get_conn(request)
        try:
            if body.source_type == "memory_file":
                _entries, chunks_created = ingest_memory_file(
                    conn, filepath, context_hint=body.context_hint
                )
            else:
                _entries, chunks_created = ingest_session_file(
                    conn, filepath, context_hint=body.context_hint
                )
        finally:
            conn.close()

        push_snapshot(database_url)
        return IngestResponse(chunks_created=chunks_created, avg_composite_score=0.0)

    # --- raw type ---
    if body.text is not None:
        raw_text = body.text
    else:
        source_path = Path(body.source)
        if not source_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Source file not found: {body.source}",
            )
        try:
            raw_text = source_path.read_text(errors="replace")
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Could not read file: {exc}",
            ) from exc

    if not raw_text.strip():
        raise HTTPException(status_code=422, detail="Text content is empty.")

    chunks = chunk_text(raw_text)
    if not chunks:
        return IngestResponse(chunks_created=0, avg_composite_score=0.0)

    scored = score_chunks(chunks, context_hint=body.context_hint)

    conn = get_conn(request)
    try:
        for chunk in scored:
            conn.execute(
                """
                INSERT INTO chunk_cache
                    (source_label, chunk_index, content, token_count,
                     fact_score, preference_score, intent_score, composite_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    body.source,
                    chunk["chunk_index"],
                    chunk["content"],
                    chunk.get("token_count", 0),
                    chunk.get("fact_score", 0.0),
                    chunk.get("preference_score", 0.0),
                    chunk.get("intent_score", 0.0),
                    chunk.get("composite_score", 0.0),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    avg = (
        sum(c.get("composite_score", 0.0) for c in scored) / len(scored)
        if scored else 0.0
    )

    push_snapshot(database_url)

    return IngestResponse(
        chunks_created=len(scored),
        avg_composite_score=round(avg, 4),
    )

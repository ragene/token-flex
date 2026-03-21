"""
POST /ingest

Accepts raw text or a file path, chunks it, scores each chunk,
and persists to chunk_cache.

source_type options:
  "raw"         — (default) chunk the provided text directly
  "memory_file" — ingest via ingest_memory_file (markdown sections → memory_entries + chunks)
  "session"     — ingest via ingest_session_file (.jsonl session → memory_entries + chunks)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db.schema import init_db
from engine.chunker import chunk_text
from engine.scorer import score_chunks
from api.push_client import push_snapshot

router = APIRouter(tags=["ingest"])


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
    """
    Ingest text into the chunk pipeline.

    - source_type="raw" (default): chunk the text directly (text field or file at source).
    - source_type="memory_file": ingest markdown file via memory pipeline (memory_entries + chunks).
    - source_type="session": ingest .jsonl session file via memory pipeline (memory_entries + chunks).
    """
    db_path: str = request.app.state.db_path

    # --- memory_file and session types go through the memory ingestor ---
    if body.source_type in ("memory_file", "session"):
        from engine.ingestor import ingest_memory_file, ingest_session_file

        filepath = Path(body.source)
        if not filepath.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Source file not found: {body.source}",
            )

        conn = sqlite3.connect(db_path)
        init_db(conn)
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

        # Push updated snapshot to the token-flow-ui (best-effort)
        push_snapshot(db_path)

        return IngestResponse(chunks_created=chunks_created, avg_composite_score=0.0)

    # --- raw type: classic chunk-score-persist path ---
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

    # Chunk
    chunks = chunk_text(raw_text)
    if not chunks:
        return IngestResponse(chunks_created=0, avg_composite_score=0.0)

    # Score
    scored = score_chunks(chunks, context_hint=body.context_hint)

    # Persist
    conn = sqlite3.connect(db_path)
    try:
        for chunk in scored:
            conn.execute(
                """
                INSERT INTO chunk_cache
                    (source_label, chunk_index, content, token_count,
                     fact_score, preference_score, intent_score, composite_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
        if scored
        else 0.0
    )

    # Push updated snapshot to the token-flow-ui (best-effort)
    push_snapshot(db_path)

    return IngestResponse(
        chunks_created=len(scored),
        avg_composite_score=round(avg, 4),
    )

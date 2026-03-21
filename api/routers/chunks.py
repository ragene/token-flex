"""
GET /chunks          — list chunks with optional filters
GET /chunks/{id}     — single chunk by primary key
GET /tokens          — token stats across all sessions
GET /session/current — metadata + token count for the active local session
"""
from __future__ import annotations

import json as _json
import sqlite3
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(tags=["chunks"])


class TokenStats(BaseModel):
    total_tokens_approx: int
    session_tokens: int
    claude_tokens: int
    memory_tokens: int
    session_files: int
    claude_session_files: int
    status: str           # ok | warning | critical
    message: str
    warn_threshold: int
    distill_threshold: int
    # chunk cache stats
    cached_chunks: int
    cached_chunk_tokens: int


class CurrentSessionOut(BaseModel):
    session_id: Optional[str]
    session_file: Optional[str]
    token_count_approx: int
    message_count: int
    channel: Optional[str]
    started_at: Optional[str]
    last_updated_at: Optional[str]


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
    """
    Estimate total context tokens across active session files + today's memory file.
    Uses the same char/4 method as smart-memory. Also includes chunk cache stats.
    """
    import os
    from pathlib import Path
    from datetime import date as _date

    WARN_THRESHOLD   = int(os.environ.get("SMART_MEMORY_WARN_TOKENS",   "30000"))
    DISTILL_THRESHOLD = int(os.environ.get("SMART_MEMORY_DISTILL_TOKENS", "30000"))

    SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR",
                        Path.home() / ".openclaw/agents/main/sessions"))
    MEMORY_DIR   = Path(os.environ.get("MEMORY_DIR",
                        "/home/ec2-user/.openclaw/workspace/memory"))

    def _approx_tokens(p: Path) -> int:
        try:
            return len(p.read_text(errors="ignore")) // 4
        except Exception:
            return 0

    def _claude_cli_sessions() -> list:
        home = Path.home()
        found = list(home.glob(".claude/projects/*/conversation.jsonl"))
        found += list(home.glob(".claude/conversations/*.jsonl"))
        extra = os.environ.get("CLAUDE_SESSIONS_DIR", "")
        if extra:
            root = Path(extra)
            found += list(root.glob("projects/*/conversation.jsonl"))
            found += list(root.glob("conversations/*.jsonl"))
        return list(set(found))

    # Session files
    session_files = list(SESSIONS_DIR.glob("*.jsonl")) if SESSIONS_DIR.exists() else []
    session_tokens = sum(_approx_tokens(f) for f in session_files)

    # Claude CLI sessions
    claude_files = _claude_cli_sessions()
    claude_tokens = sum(_approx_tokens(f) for f in claude_files)

    # Today's memory file
    today = _date.today().isoformat()
    mem_file = MEMORY_DIR / f"{today}.md"
    memory_tokens = _approx_tokens(mem_file) if mem_file.exists() else 0

    total = session_tokens + claude_tokens + memory_tokens

    if total >= DISTILL_THRESHOLD:
        status = "critical"
        msg = (f"⚠️ Context is very large (~{total:,} tokens). "
               "Distill NOW to avoid context degradation. Run: POST /memory/full")
    elif total >= WARN_THRESHOLD:
        status = "warning"
        msg = (f"🟡 Context growing (~{total:,} tokens). "
               "Consider distilling soon. Run: POST /memory/full")
    else:
        status = "ok"
        msg = f"✅ Context healthy (~{total:,} tokens)."

    # Chunk cache totals
    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    try:
        crow = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(token_count),0) FROM chunk_cache"
        ).fetchone()
    finally:
        conn.close()

    return TokenStats(
        total_tokens_approx=total,
        session_tokens=session_tokens,
        claude_tokens=claude_tokens,
        memory_tokens=memory_tokens,
        session_files=len(session_files),
        claude_session_files=len(claude_files),
        status=status,
        message=msg,
        warn_threshold=WARN_THRESHOLD,
        distill_threshold=DISTILL_THRESHOLD,
        cached_chunks=crow[0] or 0,
        cached_chunk_tokens=crow[1] or 0,
    )


@router.get("/session/current", response_model=CurrentSessionOut)
async def current_session(request: Request) -> CurrentSessionOut:
    """
    Return metadata and approximate token count for the active local OpenClaw session.
    Reads sessions.json to find the active session file, then counts tokens and messages.
    """
    import os

    SESSIONS_DIR = Path(os.environ.get(
        "SESSIONS_DIR",
        Path.home() / ".openclaw/agents/main/sessions",
    ))

    sessions_json = SESSIONS_DIR / "sessions.json"
    if not sessions_json.exists():
        return CurrentSessionOut(
            session_id=None,
            session_file=None,
            token_count_approx=0,
            message_count=0,
            channel=None,
            started_at=None,
            last_updated_at=None,
        )

    try:
        meta = _json.loads(sessions_json.read_text(errors="ignore"))
    except Exception:
        meta = {}

    # sessions.json is a dict keyed by agent label — grab the first (main) entry
    session_meta = next(iter(meta.values()), {}) if meta else {}
    session_id = session_meta.get("sessionId")
    channel = session_meta.get("lastChannel") or session_meta.get("deliveryContext", {}).get("channel")

    updated_ms = session_meta.get("updatedAt")
    last_updated_at: Optional[str] = None
    if updated_ms:
        from datetime import datetime, timezone
        last_updated_at = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc).isoformat()

    if not session_id:
        return CurrentSessionOut(
            session_id=None,
            session_file=None,
            token_count_approx=0,
            message_count=0,
            channel=channel,
            started_at=None,
            last_updated_at=last_updated_at,
        )

    session_file = SESSIONS_DIR / f"{session_id}.jsonl"
    if not session_file.exists():
        return CurrentSessionOut(
            session_id=session_id,
            session_file=str(session_file),
            token_count_approx=0,
            message_count=0,
            channel=channel,
            started_at=None,
            last_updated_at=last_updated_at,
        )

    raw = session_file.read_text(errors="ignore")
    token_count_approx = len(raw) // 4

    # Count messages (lines with type=human or type=assistant)
    message_count = 0
    started_at: Optional[str] = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            obj = _json.loads(line)
        except Exception:
            continue
        t = obj.get("type", "")
        if t == "session" and started_at is None:
            started_at = obj.get("timestamp")
        if t in ("human", "assistant", "say", "message"):
            message_count += 1

    return CurrentSessionOut(
        session_id=session_id,
        session_file=str(session_file),
        token_count_approx=token_count_approx,
        message_count=message_count,
        channel=channel,
        started_at=started_at,
        last_updated_at=last_updated_at,
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

"""
GET /chunks          — list chunks with optional filters
GET /chunks/{id}     — single chunk by primary key
GET /tokens          — token stats across all sessions
GET /session/current — metadata + token count for the active local session
GET /session/stream  — SSE stream: pushes session + token data on an interval
"""
from __future__ import annotations

import asyncio
import json as _json
import os
from pathlib import Path
from typing import AsyncGenerator, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from api.auth import verify_token
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.db_helper import get_conn, get_db_url

router = APIRouter(tags=["chunks"], dependencies=[Depends(verify_token)])


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


def _row_to_chunk(row) -> ChunkOut:
    return ChunkOut(
        id=row["id"],
        source_id=row["source_id"],
        source_label=row["source_label"],
        chunk_index=row["chunk_index"],
        content=row["content"],
        token_count=row["token_count"],
        fact_score=row["fact_score"] or 0.0,
        preference_score=row["preference_score"] or 0.0,
        intent_score=row["intent_score"] or 0.0,
        composite_score=row["composite_score"] or 0.0,
        summary=row["summary"],
        is_summarized=row["is_summarized"] or 0,
        pushed_to_s3_at=str(row["pushed_to_s3_at"]) if row["pushed_to_s3_at"] else None,
        s3_key=row["s3_key"],
        created_at=str(row["created_at"]) if row["created_at"] else None,
    )


@router.get("/tokens", response_model=TokenStats)
async def token_stats(request: Request) -> TokenStats:
    WARN_THRESHOLD    = int(os.environ.get("SMART_MEMORY_WARN_TOKENS",   "30000"))
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

    session_files  = list(SESSIONS_DIR.glob("*.jsonl")) if SESSIONS_DIR.exists() else []
    session_tokens = sum(_approx_tokens(f) for f in session_files)
    claude_files   = _claude_cli_sessions()
    claude_tokens  = sum(_approx_tokens(f) for f in claude_files)

    from datetime import date as _date
    today    = _date.today().isoformat()
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

    conn = get_conn(request)
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
        cached_chunk_tokens=int(crow[1] or 0),
    )


@router.get("/session/current", response_model=CurrentSessionOut)
async def current_session(request: Request) -> CurrentSessionOut:
    SESSIONS_DIR = Path(os.environ.get(
        "SESSIONS_DIR",
        Path.home() / ".openclaw/agents/main/sessions",
    ))

    sessions_json = SESSIONS_DIR / "sessions.json"
    if not sessions_json.exists():
        return CurrentSessionOut(
            session_id=None, session_file=None,
            token_count_approx=0, message_count=0,
            channel=None, started_at=None, last_updated_at=None,
        )

    try:
        meta = _json.loads(sessions_json.read_text(errors="ignore"))
    except Exception:
        meta = {}

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
            session_id=None, session_file=None,
            token_count_approx=0, message_count=0,
            channel=channel, started_at=None, last_updated_at=last_updated_at,
        )

    session_file = SESSIONS_DIR / f"{session_id}.jsonl"
    if not session_file.exists():
        return CurrentSessionOut(
            session_id=session_id, session_file=str(session_file),
            token_count_approx=0, message_count=0,
            channel=channel, started_at=None, last_updated_at=last_updated_at,
        )

    raw = session_file.read_text(errors="ignore")
    token_count_approx = len(raw) // 4

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
        session_id=session_id, session_file=str(session_file),
        token_count_approx=token_count_approx, message_count=message_count,
        channel=channel, started_at=started_at, last_updated_at=last_updated_at,
    )


@router.get("/session/stream")
async def session_stream(request: Request, interval: int = 10) -> StreamingResponse:
    SESSIONS_DIR = Path(os.environ.get(
        "SESSIONS_DIR",
        Path.home() / ".openclaw/agents/main/sessions",
    ))
    WARN_THRESHOLD    = int(os.environ.get("SMART_MEMORY_WARN_TOKENS",   "30000"))
    DISTILL_THRESHOLD = int(os.environ.get("SMART_MEMORY_DISTILL_TOKENS", "30000"))
    MEMORY_DIR        = Path(os.environ.get("MEMORY_DIR",
                             "/home/ec2-user/.openclaw/workspace/memory"))

    def _approx_tokens(p: Path) -> int:
        try:
            return len(p.read_text(errors="ignore")) // 4
        except Exception:
            return 0

    def _build_snapshot() -> dict:
        from datetime import datetime, timezone

        sessions_json = SESSIONS_DIR / "sessions.json"
        session_data: dict = {
            "session_id": None, "session_file": None,
            "token_count_approx": 0, "message_count": 0,
            "channel": None, "started_at": None, "last_updated_at": None,
        }
        if sessions_json.exists():
            try:
                meta = _json.loads(sessions_json.read_text(errors="ignore"))
                sm = next(iter(meta.values()), {}) if meta else {}
                sid = sm.get("sessionId")
                channel = sm.get("lastChannel") or sm.get("deliveryContext", {}).get("channel")
                updated_ms = sm.get("updatedAt")
                last_updated_at = (
                    datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc).isoformat()
                    if updated_ms else None
                )
                session_data.update({"session_id": sid, "channel": channel, "last_updated_at": last_updated_at})
                if sid:
                    sf = SESSIONS_DIR / f"{sid}.jsonl"
                    session_data["session_file"] = str(sf)
                    if sf.exists():
                        raw = sf.read_text(errors="ignore")
                        session_data["token_count_approx"] = len(raw) // 4
                        mc = 0
                        for line in raw.splitlines():
                            if not line.strip():
                                continue
                            try:
                                obj = _json.loads(line)
                            except Exception:
                                continue
                            t = obj.get("type", "")
                            if t == "session" and session_data["started_at"] is None:
                                session_data["started_at"] = obj.get("timestamp")
                            if t in ("human", "assistant", "say", "message"):
                                mc += 1
                        session_data["message_count"] = mc
            except Exception:
                pass

        session_files = list(SESSIONS_DIR.glob("*.jsonl")) if SESSIONS_DIR.exists() else []
        session_tokens = sum(_approx_tokens(f) for f in session_files)
        today = datetime.utcnow().date().isoformat()
        mem_file = MEMORY_DIR / f"{today}.md"
        memory_tokens = _approx_tokens(mem_file) if mem_file.exists() else 0
        total = session_tokens + memory_tokens

        if total >= DISTILL_THRESHOLD:
            status, msg = "critical", f"⚠️ Context very large (~{total:,} tokens). Distill NOW."
        elif total >= WARN_THRESHOLD:
            status, msg = "warning", f"🟡 Context growing (~{total:,} tokens). Consider distilling."
        else:
            status, msg = "ok", f"✅ Context healthy (~{total:,} tokens)."

        token_data = {
            "total_tokens_approx": total,
            "session_tokens": session_tokens,
            "memory_tokens": memory_tokens,
            "session_files": len(session_files),
            "status": status,
            "message": msg,
            "warn_threshold": WARN_THRESHOLD,
            "distill_threshold": DISTILL_THRESHOLD,
        }

        return {
            "ts": datetime.utcnow().isoformat() + "Z",
            "session": session_data,
            "tokens": token_data,
        }

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            snapshot = _build_snapshot()
            yield f"data: {_json.dumps(snapshot)}\n\n"
        except Exception as exc:
            yield f"data: {_json.dumps({'error': str(exc)})}\n\n"

        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(max(1, min(interval, 300)))
            if await request.is_disconnected():
                break
            try:
                snapshot = _build_snapshot()
                yield f"data: {_json.dumps(snapshot)}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chunks", response_model=List[ChunkOut])
async def list_chunks(
    request: Request,
    min_score: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=50, ge=1, le=500),
    source: Optional[str] = Query(default=None),
    sort: Optional[str] = Query(default="score", description="Sort order: 'score' (default) or 'recent'"),
) -> List[ChunkOut]:
    conn = get_conn(request)
    try:
        conditions = ["composite_score >= %s"]
        params: list = [min_score]

        if source:
            conditions.append("source_label LIKE %s")
            params.append(f"{source}%")

        where = "WHERE " + " AND ".join(conditions)
        order = "id DESC" if sort == "recent" else "composite_score DESC"
        sql = f"{_SELECT_ALL} {where} ORDER BY {order} LIMIT %s"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    return [_row_to_chunk(r) for r in rows]


@router.get("/chunks/{chunk_id}", response_model=ChunkOut)
async def get_chunk(chunk_id: int, request: Request) -> ChunkOut:
    conn = get_conn(request)
    try:
        row = conn.execute(
            f"{_SELECT_ALL} WHERE id = %s", (chunk_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Chunk {chunk_id} not found.")

    return _row_to_chunk(row)

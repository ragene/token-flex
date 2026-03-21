"""
api/routers/memory.py — Smart-memory operations exposed as REST endpoints.

Routes (all prefixed /memory/ in this router):
  POST /memory/ingest/file
  POST /memory/ingest/git
  POST /memory/ingest/session
  POST /memory/ingest/auto
  GET  /memory/query
  POST /memory/rebuild
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from db.schema import init_db
from engine.ingestor import (
    find_claude_cli_sessions,
    ingest_git_history,
    ingest_memory_file,
    ingest_session_file,
    query_context,
    rebuild_memory,
)

router = APIRouter(tags=["memory"])

_DEFAULT_WORKSPACE = "/home/ec2-user/.openclaw/workspace"
_DEFAULT_MEMORY_DIR = "/home/ec2-user/.openclaw/workspace/memory"


def _get_conn(request: Request) -> sqlite3.Connection:
    db_path: str = request.app.state.db_path
    conn = sqlite3.connect(db_path)
    init_db(conn)
    return conn


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class IngestFileRequest(BaseModel):
    path: str
    context_hint: str = ""
    clear: bool = False


class IngestGitRequest(BaseModel):
    workspace: str = _DEFAULT_WORKSPACE
    since: str = "24 hours ago"
    context_hint: str = ""


class IngestSessionRequest(BaseModel):
    path: str
    context_hint: str = ""


class IngestAutoRequest(BaseModel):
    context_hint: str = ""
    since: str = "24 hours ago"
    clear_after: bool = False


class IngestResult(BaseModel):
    ingested: int
    chunks_created: int


class IngestAutoResult(BaseModel):
    md_files: int
    git: int
    sessions: int
    total_chunks: int


class MemoryEntry(BaseModel):
    category: str
    summary: str
    relevance: float
    keywords: List[str]


class RebuildRequest(BaseModel):
    output_path: str
    top_n: int = 20


class RebuildResult(BaseModel):
    entries_written: int
    output_path: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/memory/ingest/file", response_model=IngestResult)
async def ingest_file(body: IngestFileRequest, request: Request) -> IngestResult:
    """Ingest a markdown memory file, summarize sections, and chunk them."""
    filepath = Path(body.path)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    conn = _get_conn(request)
    try:
        if body.clear:
            # Clear existing entries for this source before re-ingesting
            conn.execute(
                "DELETE FROM memory_entries WHERE source_file = ?",
                (filepath.name,),
            )
            conn.commit()

        ingested, chunks = ingest_memory_file(conn, filepath, context_hint=body.context_hint)
    finally:
        conn.close()

    return IngestResult(ingested=ingested, chunks_created=chunks)


@router.post("/memory/ingest/git", response_model=IngestResult)
async def ingest_git(body: IngestGitRequest, request: Request) -> IngestResult:
    """Ingest git commit history from the workspace and chunk it."""
    workspace = Path(body.workspace)
    if not workspace.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found: {body.workspace}")

    conn = _get_conn(request)
    try:
        ingested, chunks = ingest_git_history(
            conn,
            workspace=workspace,
            context_hint=body.context_hint,
            since=body.since,
        )
    finally:
        conn.close()

    return IngestResult(ingested=ingested, chunks_created=chunks)


@router.post("/memory/ingest/session", response_model=IngestResult)
async def ingest_session(body: IngestSessionRequest, request: Request) -> IngestResult:
    """Ingest a session transcript (.jsonl) and chunk it."""
    filepath = Path(body.path)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Session file not found: {body.path}")

    conn = _get_conn(request)
    try:
        ingested, chunks = ingest_session_file(conn, filepath, context_hint=body.context_hint)
    finally:
        conn.close()

    return IngestResult(ingested=ingested, chunks_created=chunks)


@router.post("/memory/ingest/auto", response_model=IngestAutoResult)
async def ingest_auto(body: IngestAutoRequest, request: Request) -> IngestAutoResult:
    """
    Auto-discover and ingest:
      - All *.md files in MEMORY_DIR env (default: workspace/memory/)
      - Git history from WORKSPACE env
      - All auto-discovered Claude CLI sessions
    """
    memory_dir = Path(os.environ.get("MEMORY_DIR", _DEFAULT_MEMORY_DIR))
    workspace = Path(os.environ.get("WORKSPACE", _DEFAULT_WORKSPACE))

    conn = _get_conn(request)
    try:
        total_md = 0
        total_git = 0
        total_sessions = 0
        total_chunks = 0

        # Ingest markdown memory files
        if memory_dir.exists():
            md_files = list(memory_dir.glob("*.md"))
            for f in md_files:
                ingested, chunks = ingest_memory_file(conn, f, context_hint=body.context_hint)
                total_md += ingested
                total_chunks += chunks
                if body.clear_after and ingested > 0:
                    from datetime import datetime
                    f.write_text(
                        f"# Memory — {f.stem}\n\n"
                        f"*Cleared after distillation on "
                        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.*\n"
                        f"*Context stored in context.db — rebuilt below.*\n"
                    )

        # Ingest git history
        git_ingested, git_chunks = ingest_git_history(
            conn,
            workspace=workspace,
            context_hint=body.context_hint,
            since=body.since,
        )
        total_git += git_ingested
        total_chunks += git_chunks

        # Ingest Claude CLI sessions (auto-discovered)
        session_files = find_claude_cli_sessions()
        for sf in session_files:
            ingested, chunks = ingest_session_file(conn, sf, context_hint=body.context_hint)
            total_sessions += ingested
            total_chunks += chunks

    finally:
        conn.close()

    return IngestAutoResult(
        md_files=total_md,
        git=total_git,
        sessions=total_sessions,
        total_chunks=total_chunks,
    )


@router.get("/memory/query", response_model=List[MemoryEntry])
async def query_memory(
    request: Request,
    top_n: int = 20,
    min_relevance: float = 0.5,
) -> List[MemoryEntry]:
    """Return top-N most relevant memory entries above the relevance threshold."""
    import json as _json

    conn = _get_conn(request)
    try:
        rows = query_context(conn, top_n=top_n, min_relevance=min_relevance)
    finally:
        conn.close()

    result = []
    for category, summary, _content, relevance, keywords_raw in rows:
        try:
            kw = _json.loads(keywords_raw) if keywords_raw else []
        except Exception:
            kw = []
        # Filter out MD5 hashes from displayed keywords
        kw = [k for k in kw if len(k) != 32]
        result.append(
            MemoryEntry(
                category=category or "general",
                summary=summary or "",
                relevance=round(relevance, 4),
                keywords=kw,
            )
        )
    return result


@router.post("/memory/rebuild", response_model=RebuildResult)
async def rebuild(body: RebuildRequest, request: Request) -> RebuildResult:
    """Rebuild a distilled markdown file from top-scoring memory entries."""
    output_path = Path(body.output_path)

    conn = _get_conn(request)
    try:
        entries_written = rebuild_memory(conn, output_path=output_path, top_n=body.top_n)
    finally:
        conn.close()

    return RebuildResult(
        entries_written=entries_written,
        output_path=str(output_path.resolve()),
    )

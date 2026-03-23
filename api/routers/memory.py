"""
api/routers/memory.py — Smart-memory operations exposed as REST endpoints.

Routes:
  POST /memory/ingest/file
  POST /memory/ingest/git
  POST /memory/ingest/session
  POST /memory/ingest/auto
  POST /memory/full          ← full cycle: ingest → safety gate → clear → rebuild
  GET  /memory/query
  POST /memory/rebuild
"""
from __future__ import annotations

import json as _json
import os
from api.db_helper import get_conn as _get_conn_helper, get_db_url
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from api.auth import verify_token
from pydantic import BaseModel

from db.schema import init_db
from api.push_client import push_snapshot, log_pipeline_event
from engine.ingestor import (
    find_claude_cli_sessions,
    ingest_git_history,
    ingest_memory_file,
    ingest_session_file,
    query_context,
    rebuild_memory,
    _run_chunk_pipeline_raw,
)

router = APIRouter(tags=["memory"], dependencies=[Depends(verify_token)])

_DEFAULT_WORKSPACE = "/home/ec2-user/.openclaw/workspace"
_DEFAULT_MEMORY_DIR = "/home/ec2-user/.openclaw/workspace/memory"


def _get_conn(request: Request):
    return _get_conn_helper(request)


def _db_entry_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]


def _clear_md_file(f: Path) -> None:
    """Overwrite a markdown memory file with a cleared placeholder."""
    f.write_text(
        f"# Memory — {f.stem}\n\n"
        f"*Cleared after distillation on "
        f"{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.*\n"
        f"*Context stored in token-flow DB — rebuilt below.*\n"
    )


def _clear_session_file(f: Path) -> None:
    """Zero out a .jsonl session file after it's been ingested."""
    try:
        f.write_text("")
    except Exception:
        pass


def _find_openclaw_sessions() -> list[Path]:
    """Find OpenClaw .jsonl session files from SESSIONS_DIR env."""
    sessions_dir = os.environ.get(
        "SESSIONS_DIR",
        "/home/ec2-user/.openclaw/agents/main/sessions"
    )
    if not sessions_dir:
        return []
    root = Path(sessions_dir)
    if not root.exists():
        return []
    return list(root.glob("*.jsonl"))


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


class FullCycleRequest(BaseModel):
    context_hint: str = ""
    since: str = "24 hours ago"
    rebuild_output: Optional[str] = None   # path to write rebuilt markdown; defaults to today's memory file
    top_n: int = 20
    dry_run: bool = False                  # if True, ingest but skip clearing and rebuild


class IngestResult(BaseModel):
    ingested: int
    chunks_created: int


class IngestAutoResult(BaseModel):
    md_files: int
    git: int
    sessions: int
    total_chunks: int


class FullCycleResult(BaseModel):
    md_files_ingested: int
    git_ingested: int
    sessions_ingested: int
    raw_file_chunks: int
    total_chunks: int
    db_entries: int
    safety_gate_passed: bool
    md_files_cleared: int
    session_files_cleared: int
    chunks_pruned: int
    chunks_remaining: int
    rebuilt_to: Optional[str]


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
    filepath = Path(body.path)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {body.path}")

    conn = _get_conn(request)
    db_path: str = get_db_url(request)
    try:
        if body.clear:
            conn.execute("DELETE FROM memory_entries WHERE source_file = ?", (filepath.name,))
            conn.commit()
        ingested, chunks = ingest_memory_file(conn, filepath, context_hint=body.context_hint)
    finally:
        conn.close()

    log_pipeline_event(db_path, "ingest", {"source": body.path, "ingested": ingested, "chunks_created": chunks})
    push_snapshot(db_path)
    return IngestResult(ingested=ingested, chunks_created=chunks)


@router.post("/memory/ingest/git", response_model=IngestResult)
async def ingest_git(body: IngestGitRequest, request: Request) -> IngestResult:
    workspace = Path(body.workspace)
    if not workspace.exists():
        raise HTTPException(status_code=404, detail=f"Workspace not found: {body.workspace}")

    db_path: str = get_db_url(request)
    conn = _get_conn(request)
    try:
        ingested, chunks = ingest_git_history(
            conn, workspace=workspace, context_hint=body.context_hint, since=body.since,
        )
    finally:
        conn.close()

    log_pipeline_event(db_path, "ingest", {"source": "git", "since": body.since, "ingested": ingested, "chunks_created": chunks})
    push_snapshot(db_path)
    return IngestResult(ingested=ingested, chunks_created=chunks)


@router.post("/memory/ingest/session", response_model=IngestResult)
async def ingest_session(body: IngestSessionRequest, request: Request) -> IngestResult:
    filepath = Path(body.path)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail=f"Session file not found: {body.path}")

    db_path: str = get_db_url(request)
    conn = _get_conn(request)
    try:
        ingested, chunks = ingest_session_file(conn, filepath, context_hint=body.context_hint)
    finally:
        conn.close()

    log_pipeline_event(db_path, "ingest", {"source": body.path, "ingested": ingested, "chunks_created": chunks})
    push_snapshot(db_path)
    return IngestResult(ingested=ingested, chunks_created=chunks)


@router.post("/memory/ingest/auto", response_model=IngestAutoResult)
async def ingest_auto(body: IngestAutoRequest, request: Request) -> IngestAutoResult:
    """Auto-ingest all md files, git history, and Claude CLI sessions."""
    memory_dir = Path(os.environ.get("MEMORY_DIR", _DEFAULT_MEMORY_DIR))
    workspace = Path(os.environ.get("WORKSPACE", _DEFAULT_WORKSPACE))

    db_path: str = get_db_url(request)
    conn = _get_conn(request)
    try:
        total_md = 0
        total_git = 0
        total_sessions = 0
        total_chunks = 0

        if memory_dir.exists():
            for f in memory_dir.glob("*.md"):
                ingested, chunks = ingest_memory_file(conn, f, context_hint=body.context_hint)
                total_md += ingested
                total_chunks += chunks
                if body.clear_after and ingested > 0:
                    _clear_md_file(f)

        git_ingested, git_chunks = ingest_git_history(
            conn, workspace=workspace, context_hint=body.context_hint, since=body.since,
        )
        total_git += git_ingested
        total_chunks += git_chunks

        for sf in find_claude_cli_sessions():
            ingested, chunks = ingest_session_file(conn, sf, context_hint=body.context_hint)
            total_sessions += ingested
            total_chunks += chunks

    finally:
        conn.close()

    result = IngestAutoResult(
        md_files=total_md, git=total_git, sessions=total_sessions, total_chunks=total_chunks,
    )
    log_pipeline_event(db_path, "ingest", {
        "mode": "auto", "md_files": total_md, "git": total_git,
        "sessions": total_sessions, "total_chunks": total_chunks,
    })
    push_snapshot(db_path)
    return result


@router.post("/memory/full", response_model=FullCycleResult)
async def full_cycle(body: FullCycleRequest, request: Request) -> FullCycleResult:
    """
    Full smart-memory cycle:
      1. Ingest all .md files, git history, OpenClaw sessions, Claude CLI sessions
      2. Safety gate — abort clear if DB has 0 entries
      3. Clear md files + session files (unless dry_run)
      4. Rebuild distilled markdown context file
    """
    memory_dir = Path(os.environ.get("MEMORY_DIR", _DEFAULT_MEMORY_DIR))
    workspace = Path(os.environ.get("WORKSPACE", _DEFAULT_WORKSPACE))

    db_path: str = get_db_url(request)
    conn = _get_conn(request)
    try:
        total_md = 0
        total_git = 0
        total_sessions = 0
        total_chunks = 0

        # --- Step 1: Ingest ---
        md_files_ingested: list[Path] = []
        if memory_dir.exists():
            for f in memory_dir.glob("*.md"):
                ingested, chunks = ingest_memory_file(conn, f, context_hint=body.context_hint)
                if ingested > 0:
                    md_files_ingested.append(f)
                total_md += ingested
                total_chunks += chunks

        git_ingested, git_chunks = ingest_git_history(
            conn, workspace=workspace, context_hint=body.context_hint, since=body.since,
        )
        total_git += git_ingested
        total_chunks += git_chunks

        # OpenClaw sessions
        openclaw_sessions = _find_openclaw_sessions()
        session_files_ingested: list[Path] = []
        for sf in openclaw_sessions:
            ingested, chunks = ingest_session_file(conn, sf, context_hint=body.context_hint)
            if ingested > 0:
                session_files_ingested.append(sf)
            total_sessions += ingested
            total_chunks += chunks

        # Claude CLI sessions
        for sf in find_claude_cli_sessions():
            ingested, chunks = ingest_session_file(conn, sf, context_hint=body.context_hint)
            if ingested > 0:
                session_files_ingested.append(sf)
            total_sessions += ingested
            total_chunks += chunks

        # --- Step 2: Safety gate ---
        db_count = _db_entry_count(conn)
        safety_passed = db_count > 0

        md_cleared = 0
        sessions_cleared = 0
        raw_file_chunks = 0
        pruned = 0
        chunks_remaining = 0
        rebuilt_to: Optional[str] = None

        if not body.dry_run:
            if not safety_passed:
                # Abort — do not clear anything
                log_pipeline_event(db_path, "ingest", {
                    "mode": "full_cycle", "safety_gate": False,
                    "md_files": total_md, "git": total_git,
                    "sessions": total_sessions, "chunks": total_chunks,
                })
                push_snapshot(db_path)
                return FullCycleResult(
                    md_files_ingested=total_md,
                    git_ingested=total_git,
                    sessions_ingested=total_sessions,
                    raw_file_chunks=0,
                    total_chunks=total_chunks,
                    db_entries=db_count,
                    safety_gate_passed=False,
                    md_files_cleared=0,
                    session_files_cleared=0,
                    chunks_pruned=0,
                    chunks_remaining=total_chunks,
                    rebuilt_to=None,
                )

            # --- Step 3: Chunk raw files before clearing ---
            # Chunk the live session .jsonl files and today's memory .md directly
            # so their full content is scored and stored before being wiped.
            openclaw_raw = _find_openclaw_sessions()
            for sf in openclaw_raw:
                try:
                    text = sf.read_text(errors="ignore")
                    if text.strip():
                        raw_file_chunks += _run_chunk_pipeline_raw(
                            conn, sf.name, text, context_hint=body.context_hint
                        )
                except Exception:
                    pass

            today_md = memory_dir / f"{datetime.utcnow().strftime('%Y-%m-%d')}.md"
            if today_md.exists():
                try:
                    text = today_md.read_text(errors="ignore")
                    if text.strip():
                        raw_file_chunks += _run_chunk_pipeline_raw(
                            conn, today_md.name, text, context_hint=body.context_hint
                        )
                except Exception:
                    pass

            total_chunks += raw_file_chunks

            # Log chunk event after all chunking is done
            log_pipeline_event(db_path, "chunk", {
                "raw_file_chunks": raw_file_chunks,
                "total_new_chunks": total_chunks,
            })
            push_snapshot(db_path)

            # --- Step 4: Clear ---
            for f in md_files_ingested:
                _clear_md_file(f)
                md_cleared += 1

            for sf in session_files_ingested:
                _clear_session_file(sf)
                sessions_cleared += 1

            log_pipeline_event(db_path, "clear", {
                "md_files_cleared": md_cleared,
                "session_files_cleared": sessions_cleared,
            })
            push_snapshot(db_path)

            # --- Step 4: Prune processed chunks ---
            pruned = conn.execute(
                "DELETE FROM chunk_cache WHERE is_summarized = 1 AND pushed_to_s3_at IS NOT NULL"
            ).rowcount
            conn.commit()
            chunks_remaining = conn.execute("SELECT COUNT(*) FROM chunk_cache").fetchone()[0]

            log_pipeline_event(db_path, "distill", {
                "chunks_pruned": pruned,
                "chunks_remaining": chunks_remaining,
            })
            push_snapshot(db_path)

            # --- Step 5: Rebuild ---
            today = datetime.utcnow().strftime("%Y-%m-%d")
            default_output = str(memory_dir / f"{today}.md")
            output_path = Path(body.rebuild_output or default_output)
            rebuild_memory(conn, output_path=output_path, top_n=body.top_n)
            rebuilt_to = str(output_path.resolve())

            log_pipeline_event(db_path, "rebuild", {
                "output": rebuilt_to,
                "top_n": body.top_n,
                "db_entries": db_count,
            })
            push_snapshot(db_path)

    finally:
        conn.close()

    return FullCycleResult(
        md_files_ingested=total_md,
        git_ingested=total_git,
        sessions_ingested=total_sessions,
        raw_file_chunks=raw_file_chunks,
        total_chunks=total_chunks,
        db_entries=db_count,
        safety_gate_passed=safety_passed,
        md_files_cleared=md_cleared,
        session_files_cleared=sessions_cleared,
        chunks_pruned=pruned if not body.dry_run and safety_passed else 0,
        chunks_remaining=chunks_remaining if not body.dry_run and safety_passed else total_chunks,
        rebuilt_to=rebuilt_to,
    )


@router.get("/memory/query", response_model=List[MemoryEntry])
async def query_memory(
    request: Request,
    top_n: int = 20,
    min_relevance: float = 0.5,
) -> List[MemoryEntry]:
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
        kw = [k for k in kw if len(k) != 32]
        result.append(MemoryEntry(
            category=category or "general",
            summary=summary or "",
            relevance=round(relevance, 4),
            keywords=kw,
        ))
    return result


@router.post("/memory/rebuild", response_model=RebuildResult)
async def rebuild(body: RebuildRequest, request: Request) -> RebuildResult:
    db_path: str = get_db_url(request)
    output_path = Path(body.output_path)
    conn = _get_conn(request)
    try:
        entries_written = rebuild_memory(conn, output_path=output_path, top_n=body.top_n)
    finally:
        conn.close()

    log_pipeline_event(db_path, "rebuild", {"output": str(output_path.resolve()), "top_n": body.top_n, "entries_written": entries_written})
    push_snapshot(db_path)
    return RebuildResult(entries_written=entries_written, output_path=str(output_path.resolve()))

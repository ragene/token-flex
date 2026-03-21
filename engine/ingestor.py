"""
engine/ingestor.py — Smart-memory ingestion pipeline for token-flow.

Ports all memory_distill.py functionality into the engine layer.
Each ingest_* function automatically runs the chunk pipeline on inserted
memory_entries rows: chunk → score → persist to chunk_cache.

All functions accept a sqlite3.Connection as the first argument so callers
control the connection lifecycle.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared Claude summarization helper
# ---------------------------------------------------------------------------

def summarize_with_claude(text: str, context_hint: str = "") -> dict:
    """Summarize text using Claude Haiku. Returns {summary, keywords, category, relevance}."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = f"""Analyze this memory/context entry from a coding assistant session.

Context hint: {context_hint or 'FreightDawg freight dispatch app development'}

Entry:
{text[:4000]}

Return a JSON object with:
- summary: 1-2 sentence distilled summary (max 200 chars)
- keywords: array of 3-6 key terms
- category: one of [infrastructure, frontend, backend, auth, deployment, feature, fix, config]
- relevance: float 0.0-1.0 (how important/reusable this context is for future sessions)

Return ONLY valid JSON, no markdown."""

        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r'^```json?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        return json.loads(raw)
    except Exception as e:
        logger.warning("Claude summarization failed: %s", e)
        return {
            "summary": text[:200],
            "keywords": [],
            "category": "general",
            "relevance": 0.5,
        }


# ---------------------------------------------------------------------------
# Chunk pipeline helper
# ---------------------------------------------------------------------------

def _run_chunk_pipeline(conn: sqlite3.Connection, memory_entry_id: int, content: str) -> int:
    """
    Run chunk → score → persist for the given memory entry content.
    Inserts rows into chunk_cache with source_id = memory_entry_id.
    Returns the number of chunks created.
    """
    from engine.chunker import chunk_text
    from engine.scorer import score_chunks

    chunks = chunk_text(content)
    if not chunks:
        return 0

    scored = score_chunks(chunks)

    for chunk in scored:
        conn.execute(
            """
            INSERT INTO chunk_cache
                (source_id, source_label, chunk_index, content, token_count,
                 fact_score, preference_score, intent_score, composite_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_entry_id,
                f"memory_entry:{memory_entry_id}",
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
    return len(scored)


# ---------------------------------------------------------------------------
# Private message extraction helpers
# ---------------------------------------------------------------------------

def _extract_messages_openclaw(lines: list) -> list:
    """Extract messages from OpenClaw .jsonl format (type=message entries).

    Supports both v2 (role/content at top level) and v3 (role/content nested
    under a 'message' key) formats.
    """
    messages = []
    for line in lines:
        try:
            obj = json.loads(line)
            if obj.get("type") != "message":
                continue
            msg = obj.get("message", obj)
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                text = " ".join(
                    p.get("text", "") for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            elif isinstance(content, str):
                text = content
            else:
                continue
            if text.strip():
                messages.append((role.upper(), text.strip()[:500]))
        except Exception:
            continue
    return messages


def _extract_messages_claude_cli(lines: list) -> list:
    """
    Extract messages from claude CLI .jsonl format.
    Handles both Claude Code (projects/*/conversation.jsonl) and older
    conversations/*.jsonl formats.
    """
    messages = []
    for line in lines:
        try:
            obj = json.loads(line)
            role = obj.get("role", "")
            if role in ("user", "assistant", "human"):
                content = obj.get("content", obj.get("text", ""))
                if isinstance(content, list):
                    text = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                elif isinstance(content, str):
                    text = content
                else:
                    continue
                if text.strip():
                    display_role = "USER" if role in ("user", "human") else "ASSISTANT"
                    messages.append((display_role, text.strip()[:500]))
            elif obj.get("type") in ("tool_use", "tool_result"):
                tool = obj.get("name", obj.get("tool_name", "tool"))
                inp = obj.get("input", obj.get("content", ""))
                if isinstance(inp, dict):
                    inp = json.dumps(inp)[:200]
                if inp:
                    messages.append(("TOOL", f"{tool}: {str(inp)[:300]}"))
        except Exception:
            continue
    return messages


# ---------------------------------------------------------------------------
# Session discovery
# ---------------------------------------------------------------------------

def find_claude_cli_sessions() -> list[Path]:
    """
    Auto-discover claude CLI session files from well-known paths:
    - ~/.claude/projects/*/conversation.jsonl  (Claude Code / new claude CLI)
    - ~/.claude/conversations/*.jsonl           (older claude CLI format)
    Also reads CLAUDE_SESSIONS_DIR env var as an additional source root.
    Returns a deduplicated list of Path objects.
    """
    found: list[Path] = []
    home = Path.home()

    found.extend(home.glob(".claude/projects/*/conversation.jsonl"))
    found.extend(home.glob(".claude/conversations/*.jsonl"))

    sessions_dir_env = os.environ.get("SESSIONS_DIR", "")
    if sessions_dir_env:
        root = Path(sessions_dir_env)
        if root.exists():
            found.extend(root.glob("*.jsonl"))

    claude_root_env = os.environ.get("CLAUDE_SESSIONS_DIR", "")
    if claude_root_env:
        root = Path(claude_root_env)
        found.extend(root.glob("projects/*/conversation.jsonl"))
        found.extend(root.glob("conversations/*.jsonl"))

    seen: set[Path] = set()
    unique: list[Path] = []
    for f in found:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


# ---------------------------------------------------------------------------
# Ingest functions
# ---------------------------------------------------------------------------

def ingest_memory_file(conn: sqlite3.Connection, filepath: Path, context_hint: str = "") -> tuple[int, int]:
    """
    Parse a memory markdown file into sections and store each in memory_entries.
    After insertion, automatically runs the chunk pipeline.

    Returns (entries_ingested, chunks_created).
    """
    if not filepath.exists():
        logger.warning("File not found: %s", filepath)
        return 0, 0

    content = filepath.read_text()
    sections = re.split(r'\n(?=## )', content)
    entries_count = 0
    chunks_count = 0

    for section in sections:
        section = section.strip()
        if len(section) < 20:
            continue

        content_hash = hashlib.md5(section.encode()).hexdigest()
        existing = conn.execute(
            "SELECT id FROM memory_entries WHERE keywords LIKE ?",
            (f'%{content_hash}%',)
        ).fetchone()
        if existing:
            logger.debug("Skipping already-ingested section (%s)", content_hash[:8])
            continue

        logger.info("Summarizing section (%d chars)...", len(section))
        result = summarize_with_claude(section, context_hint=context_hint)

        cur = conn.execute(
            """
            INSERT INTO memory_entries (source_file, category, content, summary, keywords, relevance)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(filepath.name),
                result.get("category", "general"),
                section,
                result.get("summary", section[:200]),
                json.dumps(result.get("keywords", []) + [content_hash]),
                float(result.get("relevance", 0.5)),
            ),
        )
        conn.commit()
        entry_id = cur.lastrowid
        entries_count += 1

        chunks_count += _run_chunk_pipeline(conn, entry_id, section)

    logger.info("Ingested %d new entries from %s", entries_count, filepath.name)
    return entries_count, chunks_count


def ingest_git_history(
    conn: sqlite3.Connection,
    workspace: Path,
    context_hint: str = "",
    since: str = "24 hours ago",
) -> tuple[int, int]:
    """
    Summarize git commit history from workspace and store in memory_entries.
    Deduplicates by hashing the raw log output.
    After insertion, automatically runs the chunk pipeline.

    Returns (entries_ingested, chunks_created).
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(workspace), "log",
             f"--since={since}",
             "--pretty=format:commit %h  %ai  %s",
             "--stat",
             "--no-merges"],
            capture_output=True, text=True, timeout=15,
        )
        log_output = result.stdout.strip()
    except Exception as e:
        logger.warning("git log failed: %s", e)
        return 0, 0

    if not log_output:
        logger.info("No commits found in workspace since '%s'", since)
        return 0, 0

    content_hash = hashlib.md5(log_output.encode()).hexdigest()
    existing = conn.execute(
        "SELECT id FROM memory_entries WHERE keywords LIKE ?",
        (f'%{content_hash}%',)
    ).fetchone()
    if existing:
        logger.info("Git history already ingested (hash %s)", content_hash[:8])
        return 0, 0

    try:
        oneliner = subprocess.run(
            ["git", "-C", str(workspace), "log",
             f"--since={since}",
             "--oneline", "--no-merges"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        oneliner = ""

    num_commits = len([l for l in oneliner.splitlines() if l.strip()])
    full_text = (
        f"Git commit history for workspace ({since}):\n"
        f"Total commits: {num_commits}\n\n"
        f"## Commit list\n{oneliner}\n\n"
        f"## Detailed log\n{log_output[:6000]}"
    )

    hint = context_hint or "FreightDawg SoCal freight dispatch app development on AWS ECS"
    prompt_extra = (
        "This is a git commit log. Weight it highly — it is a concrete record of what changed today. "
        "Summarize the key themes/areas of work (features, fixes, infra, etc.). "
        "Set relevance >= 0.88 since commit history is high-value coding context."
    )

    logger.info("Summarizing git history (%d commits since '%s')...", num_commits, since)

    try:
        import anthropic
        client = anthropic.Anthropic()
        prompt = f"""Analyze this git commit history from a coding assistant workspace.

Context: {hint}
{prompt_extra}

{full_text[:5000]}

Return a JSON object with:
- summary: 1-2 sentence distilled summary of today's work themes (max 250 chars)
- keywords: array of 4-8 key terms (feature names, files touched, fix areas)
- category: one of [infrastructure, frontend, backend, auth, deployment, feature, fix, config]
- relevance: float 0.0-1.0 (should be >= 0.88 for commit history)

Return ONLY valid JSON, no markdown."""

        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=350,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r'^```json?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        result_json = json.loads(raw)
    except Exception as e:
        logger.warning("Claude summarization failed: %s", e)
        result_json = {
            "summary": f"Git history: {num_commits} commits today — {oneliner[:200]}",
            "keywords": ["git-history", "daily-commits"],
            "category": "feature",
            "relevance": 0.88,
        }

    relevance = max(float(result_json.get("relevance", 0.88)), 0.88)

    cur = conn.execute(
        """
        INSERT INTO memory_entries (source_file, category, content, summary, keywords, relevance)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            f"git-history:{since}",
            result_json.get("category", "feature"),
            full_text[:8000],
            result_json.get("summary", full_text[:250]),
            json.dumps(result_json.get("keywords", []) + [content_hash, "git-history"]),
            relevance,
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid
    logger.info("Ingested git history: %d commits, relevance=%.2f", num_commits, relevance)

    chunks_count = _run_chunk_pipeline(conn, entry_id, full_text[:8000])
    return 1, chunks_count


def ingest_session_file(
    conn: sqlite3.Connection,
    filepath: Path,
    context_hint: str = "",
) -> tuple[int, int]:
    """
    Parse a session transcript (.jsonl) and ingest as a summarized memory_entry.
    Supports OpenClaw and claude CLI session formats.
    After insertion, automatically runs the chunk pipeline.

    Returns (entries_ingested, chunks_created).
    """
    if not filepath.exists():
        logger.warning("Session file not found: %s", filepath)
        return 0, 0

    content_hash = hashlib.md5(filepath.read_bytes()).hexdigest()
    existing = conn.execute(
        "SELECT id FROM memory_entries WHERE keywords LIKE ?",
        (f'%{content_hash}%',)
    ).fetchone()
    if existing:
        logger.info("Already ingested: %s", filepath.name)
        return 0, 0

    try:
        raw_lines = [l.strip() for l in filepath.read_text(errors="ignore").splitlines() if l.strip()]
    except Exception as e:
        logger.warning("Error reading %s: %s", filepath.name, e)
        return 0, 0

    is_openclaw = False
    try:
        first = json.loads(raw_lines[0]) if raw_lines else {}
        is_openclaw = first.get("type") == "session"
    except Exception:
        pass

    if is_openclaw:
        pairs = _extract_messages_openclaw(raw_lines)
        source_label = "OpenClaw"
    else:
        pairs = _extract_messages_claude_cli(raw_lines)
        source_label = "claude-cli"

    if not pairs:
        logger.info("No messages found in %s (tried both OpenClaw + claude-cli formats)", filepath.name)
        return 0, 0

    messages = [f"{r}: {t}" for r, t in pairs]
    excerpt = "\n".join(messages[:6] + (["[...]"] if len(messages) > 10 else []) + messages[-4:])
    full_text = (
        f"Session transcript ({source_label}): {filepath.name}\n"
        f"Total turns: {len(messages)}\n\n"
        f"{excerpt}"
    )

    hint = context_hint or f"{source_label} agent session history — FreightDawg SoCal freight dispatch"
    logger.info("Summarizing %s session (%d turns)...", source_label, len(messages))
    result = summarize_with_claude(full_text, context_hint=hint)

    cur = conn.execute(
        """
        INSERT INTO memory_entries (source_file, category, content, summary, keywords, relevance)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            filepath.name,
            result.get("category", "feature"),
            full_text[:8000],
            result.get("summary", full_text[:200]),
            json.dumps(result.get("keywords", []) + [content_hash, source_label]),
            float(result.get("relevance", 0.7)),
        ),
    )
    conn.commit()
    entry_id = cur.lastrowid
    logger.info(
        "Ingested %s session: %s (%d turns, relevance=%.2f)",
        source_label, filepath.name, len(messages), result.get("relevance", 0.7),
    )

    chunks_count = _run_chunk_pipeline(conn, entry_id, full_text[:8000])
    return 1, chunks_count


# ---------------------------------------------------------------------------
# Query and rebuild
# ---------------------------------------------------------------------------

def query_context(conn: sqlite3.Connection, top_n: int = 20, min_relevance: float = 0.5) -> list:
    """Query top-N most relevant memory entries."""
    rows = conn.execute(
        """
        SELECT category, summary, content, relevance, keywords
        FROM memory_entries
        WHERE relevance >= ?
        ORDER BY relevance DESC, last_used DESC NULLS LAST
        LIMIT ?
        """,
        (min_relevance, top_n),
    ).fetchall()
    return rows


def rebuild_memory(conn: sqlite3.Connection, output_path: Path, top_n: int = 20) -> int:
    """
    Rebuild memory file from top-scoring DB entries.
    Writes distilled markdown to output_path.
    Returns the number of entries written.
    """
    rows = query_context(conn, top_n=top_n)
    if not rows:
        logger.info("No entries found in DB — nothing to rebuild.")
        return 0

    by_category: dict[str, list] = {}
    for category, summary, content, relevance, keywords in rows:
        by_category.setdefault(category, []).append({
            "summary": summary,
            "content": content,
            "relevance": relevance,
            "keywords": json.loads(keywords) if keywords else [],
        })

    lines = [f"# Memory — Rebuilt {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} (from context.db)\n"]
    lines.append(f"*Top {len(rows)} entries by relevance score*\n")

    for cat, entries in sorted(by_category.items()):
        lines.append(f"\n## {cat.upper()}\n")
        for e in sorted(entries, key=lambda x: x["relevance"], reverse=True):
            score = f"[score: {e['relevance']:.2f}]"
            kw = ", ".join([k for k in e["keywords"] if len(k) < 30 and len(k) != 32][:5])
            lines.append(f"**{score}** {e['summary']}")
            if kw:
                lines.append(f"*Keywords: {kw}*")
            lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
    logger.info("Rebuilt memory written to %s (%d entries)", output_path, len(rows))

    conn.execute("UPDATE memory_entries SET last_used = datetime('now') WHERE relevance >= 0.5")
    conn.commit()
    return len(rows)

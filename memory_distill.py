#!/usr/bin/env python3
"""
Legacy CLI interface. The FastAPI service in api/ now provides all functionality via HTTP.
Use `python main.py` to start the service.
"""
import sqlite3
import os
import json
import re
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(os.environ.get("MEMORY_DB", "/home/ec2-user/.openclaw/workspace/memory/context.db"))
MEMORY_DIR = Path(os.environ.get("MEMORY_DIR", "/home/ec2-user/.openclaw/workspace/memory"))
WORKSPACE = Path(os.environ.get("WORKSPACE", "/home/ec2-user/.openclaw/workspace"))


def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT NOT NULL,
            category    TEXT,
            content     TEXT NOT NULL,
            summary     TEXT,
            keywords    TEXT,
            relevance   REAL DEFAULT 1.0,
            created_at  TEXT DEFAULT (datetime('now')),
            last_used   TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_relevance ON memory_entries(relevance DESC);
        CREATE INDEX IF NOT EXISTS idx_category ON memory_entries(category);
    """)
    conn.commit()


def summarize_with_claude(text: str, context_hint: str = "") -> dict:
    """Summarize text using Claude. Returns {summary, keywords, category, relevance}."""
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
        # Strip markdown code blocks if present
        raw = re.sub(r'^```json?\s*', '', raw)
        raw = re.sub(r'\s*```$', '', raw)
        # Extract only the first JSON object — Claude sometimes appends extra text/blocks
        # which causes json.loads to raise "Extra data: line N column 1 (char X)"
        brace_depth = 0
        end_idx = None
        for i, ch in enumerate(raw):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    end_idx = i + 1
                    break
        if end_idx is not None:
            raw = raw[:end_idx]
        return json.loads(raw)
    except Exception as e:
        print(f"Claude summarization failed: {e}")
        return {
            "summary": text[:200],
            "keywords": [],
            "category": "general",
            "relevance": 0.5
        }


def ingest_memory_file(conn, filepath: Path, context_hint: str = "") -> int:
    """Parse memory file into sections and store each in DB."""
    if not filepath.exists():
        print(f"File not found: {filepath}")
        return 0

    content = filepath.read_text()
    # Split on ## headers
    sections = re.split(r'\n(?=## )', content)
    count = 0

    for section in sections:
        section = section.strip()
        if len(section) < 20:
            continue

        import hashlib
        content_hash = hashlib.md5(section.encode()).hexdigest()
        existing = conn.execute(
            "SELECT id FROM memory_entries WHERE keywords LIKE ?",
            (f'%{content_hash}%',)
        ).fetchone()
        if existing:
            print(f"  Skipping already-ingested section ({content_hash[:8]})")
            continue

        print(f"  Summarizing section ({len(section)} chars)...")
        result = summarize_with_claude(section, context_hint=context_hint)

        conn.execute("""
            INSERT INTO memory_entries (source_file, category, content, summary, keywords, relevance)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            str(filepath.name),
            result.get("category", "general"),
            section,
            result.get("summary", section[:200]),
            json.dumps(result.get("keywords", []) + [content_hash]),
            float(result.get("relevance", 0.5))
        ))
        count += 1

    conn.commit()
    print(f"  Ingested {count} new entries from {filepath.name}")
    return count


def query_context(conn, top_n: int = 20, min_relevance: float = 0.5) -> list:
    """Query top-N most relevant memory entries."""
    rows = conn.execute("""
        SELECT category, summary, content, relevance, keywords
        FROM memory_entries
        WHERE relevance >= ?
        ORDER BY relevance DESC, last_used DESC NULLS LAST
        LIMIT ?
    """, (min_relevance, top_n)).fetchall()
    return rows


def rebuild_memory(conn, output_path: Path, top_n: int = 20):
    """Rebuild memory file from top-scoring DB entries."""
    rows = query_context(conn, top_n=top_n)
    if not rows:
        print("No entries found in DB — nothing to rebuild.")
        return

    by_category = {}
    for category, summary, content, relevance, keywords in rows:
        by_category.setdefault(category, []).append({
            "summary": summary,
            "content": content,
            "relevance": relevance,
            "keywords": json.loads(keywords) if keywords else []
        })

    lines = [f"# Memory — Rebuilt {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} (from context.db)\n"]
    lines.append(f"*Top {len(rows)} entries by relevance score*\n")

    for cat, entries in sorted(by_category.items()):
        lines.append(f"\n## {cat.upper()}\n")
        for e in sorted(entries, key=lambda x: x['relevance'], reverse=True):
            score = f"[score: {e['relevance']:.2f}]"
            # Filter out MD5 hashes (32-char hex) from displayed keywords
            kw = ', '.join([k for k in e['keywords'] if len(k) < 30 and len(k) != 32][:5])
            lines.append(f"**{score}** {e['summary']}")
            if kw:
                lines.append(f"*Keywords: {kw}*")
            lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text('\n'.join(lines))
    print(f"Rebuilt memory written to {output_path} ({len(rows)} entries)")

    conn.execute("UPDATE memory_entries SET last_used = datetime('now') WHERE relevance >= 0.5")
    conn.commit()



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
            # v3: content nested under obj["message"]
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
    Claude Code CLI stores sessions at ~/.claude/projects/<hash>/conversation.jsonl
    Each line is a JSON object with role + content fields (similar to Anthropic API format).
    Also handles the older ~/.claude/conversations/<id>.jsonl format.
    """
    messages = []
    for line in lines:
        try:
            obj = json.loads(line)
            # Direct message format (Anthropic API-style)
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
            # Claude Code tool-use / result entries
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


def ingest_git_history(conn, workspace: Path, context_hint: str = "", since: str = "24 hours ago") -> int:
    """
    Summarize today's git commit history from workspace and store in DB.
    Deduplicates by hashing the raw log output so re-runs are safe.
    Returns 1 if a new entry was added, 0 if already ingested or no commits found.
    """
    import subprocess
    import hashlib

    try:
        # Full log with file-change stats for richer context
        result = subprocess.run(
            ["git", "-C", str(workspace), "log",
             f"--since={since}",
             "--pretty=format:commit %h  %ai  %s",
             "--stat",
             "--no-merges"],
            capture_output=True, text=True, timeout=15
        )
        log_output = result.stdout.strip()
    except Exception as e:
        print(f"  git log failed: {e}")
        return 0

    if not log_output:
        print(f"  No commits found in workspace since '{since}'")
        return 0

    content_hash = hashlib.md5(log_output.encode()).hexdigest()
    existing = conn.execute(
        "SELECT id FROM memory_entries WHERE keywords LIKE ?",
        (f'%{content_hash}%',)
    ).fetchone()
    if existing:
        print(f"  Git history already ingested (hash {content_hash[:8]})")
        return 0

    # Also grab a compact one-liner list for the summary prompt
    try:
        oneliner = subprocess.run(
            ["git", "-C", str(workspace), "log",
             f"--since={since}",
             "--oneline", "--no-merges"],
            capture_output=True, text=True, timeout=10
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

    print(f"  Summarizing git history ({num_commits} commits since '{since}')...")

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
        # Extract only the first JSON object to avoid "Extra data" errors
        brace_depth = 0
        end_idx = None
        for i, ch in enumerate(raw):
            if ch == '{':
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    end_idx = i + 1
                    break
        if end_idx is not None:
            raw = raw[:end_idx]
        result_json = json.loads(raw)
    except Exception as e:
        print(f"  Claude summarization failed: {e}")
        result_json = {
            "summary": f"Git history: {num_commits} commits today — {oneliner[:200]}",
            "keywords": ["git-history", "daily-commits"],
            "category": "feature",
            "relevance": 0.88
        }

    # Enforce minimum relevance for commit history
    relevance = max(float(result_json.get("relevance", 0.88)), 0.88)

    conn.execute("""
        INSERT INTO memory_entries (source_file, category, content, summary, keywords, relevance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        f"git-history:{since}",
        result_json.get("category", "feature"),
        full_text[:8000],
        result_json.get("summary", full_text[:250]),
        json.dumps(result_json.get("keywords", []) + [content_hash, "git-history"]),
        relevance,
    ))
    conn.commit()
    print(f"  ✅ Ingested git history: {num_commits} commits, relevance={relevance:.2f}")
    return 1


def ingest_session_file(conn, filepath: Path, context_hint: str = "") -> int:
    """
    Parse a session transcript (.jsonl) and ingest as a summarized DB entry.
    Supports:
      - OpenClaw agent sessions (type=message entries)
      - Claude CLI sessions (role=user/assistant, ~/.claude/projects/ or ~/.claude/conversations/)
    """
    if not filepath.exists():
        print(f"Session file not found: {filepath}")
        return 0

    import hashlib
    content_hash = hashlib.md5(filepath.read_bytes()).hexdigest()
    existing = conn.execute(
        "SELECT id FROM memory_entries WHERE keywords LIKE ?",
        (f'%{content_hash}%',)
    ).fetchone()
    if existing:
        print(f"  Already ingested: {filepath.name}")
        return 0

    try:
        raw_lines = [l.strip() for l in filepath.read_text(errors="ignore").splitlines() if l.strip()]
    except Exception as e:
        print(f"  Error reading {filepath.name}: {e}")
        return 0

    # Auto-detect format: OpenClaw has type=session as first line
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
        print(f"  No messages found in {filepath.name} (tried both OpenClaw + claude-cli formats)")
        return 0

    messages = [f"{r}: {t}" for r, t in pairs]

    # Representative excerpt: first 6 turns + last 4 turns
    excerpt = "\n".join(messages[:6] + (["[...]"] if len(messages) > 10 else []) + messages[-4:])
    full_text = (
        f"Session transcript ({source_label}): {filepath.name}\n"
        f"Total turns: {len(messages)}\n\n"
        f"{excerpt}"
    )

    hint = context_hint or f"{source_label} agent session history — FreightDawg SoCal freight dispatch"
    print(f"  Summarizing {source_label} session ({len(messages)} turns)...")
    result = summarize_with_claude(full_text, context_hint=hint)

    conn.execute("""
        INSERT INTO memory_entries (source_file, category, content, summary, keywords, relevance)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        filepath.name,
        result.get("category", "feature"),
        full_text[:8000],
        result.get("summary", full_text[:200]),
        json.dumps(result.get("keywords", []) + [content_hash, source_label]),
        float(result.get("relevance", 0.7)),
    ))
    conn.commit()
    print(f"  ✅ Ingested {source_label} session: {filepath.name} ({len(messages)} turns, relevance={result.get('relevance', 0.7):.2f})")
    return 1


def _find_claude_cli_sessions() -> list:
    """
    Auto-discover claude CLI session files from well-known paths:
    - ~/.claude/projects/*/conversation.jsonl  (Claude Code / new claude CLI)
    - ~/.claude/conversations/*.jsonl           (older claude CLI format)
    Also reads CLAUDE_SESSIONS_DIR env var as an additional source root.
    Returns a deduplicated list of Path objects.
    """
    found = []
    home = Path.home()

    # New claude CLI / Claude Code format
    found.extend(home.glob(".claude/projects/*/conversation.jsonl"))
    # Older claude CLI format
    found.extend(home.glob(".claude/conversations/*.jsonl"))

    # Optional env-var override (root dir)
    claude_root_env = os.environ.get("CLAUDE_SESSIONS_DIR", "")
    if claude_root_env:
        root = Path(claude_root_env)
        found.extend(root.glob("projects/*/conversation.jsonl"))
        found.extend(root.glob("conversations/*.jsonl"))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for f in found:
        if f not in seen:
            seen.add(f)
            unique.append(f)
    return unique


def run_distill_and_clear(args_ns, triggered_by: str = "unknown", auth_token: str = None, user_email: str = None):
    """
    Run a full ingest+rebuild cycle then clear session files and token_usage.
    Called by the SQS poller on each trigger message.

    Args:
        triggered_by: display label of the user who initiated the distill (from SQS payload).
        auth_token:   pre-acquired JWT to use for the /token-data/clear call.
                      If None, falls back to get_auth_headers() then direct DB clear.
        user_email:   email of the triggering user — scopes the token_usage DELETE so
                      only that user's rows are cleared. If None, falls back to all rows
                      (legacy behaviour for unauthenticated / admin triggers).
    """
    import subprocess, sys, shutil
    from datetime import datetime as _dt

    print(f"  Triggered by: {triggered_by} (user_email={user_email or 'all'})")

    # ── 1. Distill memory ────────────────────────────────────────────────────
    cmd = [sys.executable, __file__, "full",
           "--output", args_ns.output,
           "--context-hint", args_ns.context_hint,
           "--top", str(args_ns.top)]
    if args_ns.git_since:
        cmd += ["--git-since", args_ns.git_since]
    print(f"  Running distill: {' '.join(cmd)}")
    # Forward critical env vars to the subprocess so it can find session files,
    # memory dir, DB, etc. — without this SESSIONS_DIR is empty and session
    # transcripts are silently skipped.
    _sub_env = os.environ.copy()
    _sessions_dir_val = os.environ.get(
        "SESSIONS_DIR",
        str(Path.home() / ".openclaw/agents/main/sessions")
    )
    _sub_env.setdefault("SESSIONS_DIR", _sessions_dir_val)
    _sub_env.setdefault("MEMORY_DIR",   os.environ.get("MEMORY_DIR",   str(Path.home() / ".openclaw/workspace/memory")))
    _sub_env.setdefault("WORKSPACE",    os.environ.get("WORKSPACE",    str(Path.home() / ".openclaw/workspace")))
    _sub_env.setdefault("TOKEN_FLOW_DB", os.environ.get("TOKEN_FLOW_DB", str(Path.home() / ".openclaw/data/token_flow.db")))
    result = subprocess.run(cmd, env=_sub_env)
    if result.returncode != 0:
        print(f"  ⚠️  Distill subprocess exited with code {result.returncode}")

    # ── 2. Clear session .jsonl files (archive then truncate) ────────────────
    sessions_dir = Path(os.environ.get(
        "SESSIONS_DIR",
        Path.home() / ".openclaw/agents/main/sessions"
    ))
    archive_dir = sessions_dir / "archived"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = _dt.utcnow().strftime("%Y%m%dT%H%M%S")

    cleared = 0
    if sessions_dir.exists():
        for f in sessions_dir.glob("*.jsonl"):
            try:
                dest = archive_dir / f"{f.stem}_{stamp}.jsonl"
                shutil.copy2(f, dest)
                f.write_text("")   # truncate in place so the file still exists
                cleared += 1
                print(f"  Cleared session: {f.name} → archived/{dest.name}")
            except Exception as e:
                print(f"  ⚠️  Could not clear {f.name}: {e}")
    print(f"  ✅ {cleared} session file(s) cleared")

    # ── 3. Clear memory .md files for today ──────────────────────────────────
    from datetime import date as _date
    mem_dir = Path(os.environ.get("MEMORY_DIR",
                                  "/home/ec2-user/.openclaw/workspace/memory"))
    today_md = mem_dir / f"{_date.today().isoformat()}.md"
    if today_md.exists():
        today_md.write_text(
            f"# Memory — {today_md.stem}\n\n"
            f"*Cleared after distillation on {_dt.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.*\n"
        )
        print(f"  ✅ Cleared memory file: {today_md.name}")

    # ── 4. Clear token_usage rows ─────────────────────────────────────────────
    # Strategy:
    #   a) ALWAYS clear the local SQLite TOKEN_FLOW_DB first — that's where the
    #      local service accumulates token_usage rows. This must happen regardless
    #      of whether the remote API clear succeeds.
    #   b) Also hit the remote ECS API (TOKEN_FLOW_UI_URL) to clear the Postgres
    #      push_cache so the UI reflects zero immediately.
    #   c) If no JWT and SKIP_STARTUP_AUTH=true, skip device-flow.

    # ── 4a. Always clear local SQLite ────────────────────────────────────────
    _local_db = os.environ.get("TOKEN_FLOW_DB", "/home/ec2-user/.openclaw/data/token_flow.db")
    _local_db_url = f"sqlite:///{_local_db}" if not _local_db.startswith("sqlite") else _local_db
    # Build user-scoped SQL fragments used in all clear paths below
    _email_filter = "WHERE user_email = ?" if user_email else ""
    _email_params = (user_email,) if user_email else ()

    print(f"  🗄️  Clearing local token_usage (SQLite: {_local_db}, scope={user_email or 'all'})...")
    try:
        import sqlite3 as _sqlite3
        _lc = _sqlite3.connect(_local_db_url.replace("sqlite:///", ""))
        _lcount = _lc.execute(f"SELECT COUNT(*) FROM token_usage {_email_filter}", _email_params).fetchone()[0]
        _lc.execute(f"DELETE FROM token_usage {_email_filter}", _email_params)
        _lc.commit()
        _lc.close()
        print(f"  ✅ Deleted {_lcount} local token_usage rows from SQLite")
    except Exception as e:
        print(f"  ⚠️  Local SQLite clear failed: {e}")

    # ── 4b. Also clear remote ECS Postgres via API (best-effort) ─────────────
    # The API /token-data/clear endpoint is now JWT-scoped, so passing the
    # auth_token is sufficient — the server will scope the DELETE to that user.
    _remote_url = os.environ.get("TOKEN_FLOW_UI_URL", "").rstrip("/")
    api_url = _remote_url or os.environ.get("TOKEN_FLOW_API_URL", "http://localhost:8001")
    _cleared_via_api = False

    if auth_token:
        try:
            import urllib.request as _ureq
            import json as _json_inner
            print("  🔐 Clearing remote token_usage via API (pre-acquired token)...")
            # Pass scoped_user_email in the body so the server clears only the
            # triggering user's rows even when auth_token belongs to a service account.
            _clear_body = _json_inner.dumps(
                {"scoped_user_email": user_email} if user_email else {}
            ).encode()
            req = _ureq.Request(
                f"{api_url}/token-data/clear",
                data=_clear_body,
                method="DELETE",
                headers={
                    "Authorization": f"Bearer {auth_token}",
                    "Content-Type": "application/json",
                },
            )
            with _ureq.urlopen(req, timeout=10) as r:
                body = r.read().decode()
                print(f"  ✅ Remote token_usage cleared via API: {body}")
            _cleared_via_api = True
        except Exception as e:
            print(f"  ⚠️  Remote API clear failed ({e}) — local SQLite already cleared above")

    if not _cleared_via_api:
        _skip_auth = os.environ.get("SKIP_STARTUP_AUTH", "").lower() in ("1", "true", "yes")
        db_url = os.environ.get("DATABASE_URL", "")

        if db_url and db_url.startswith("postgresql"):
            # Direct Postgres clear — scoped to user_email when provided
            print("  🗄️  Clearing remote token_usage directly via DATABASE_URL...")
            try:
                import psycopg2
                conn_str = db_url.replace("postgresql+psycopg2://", "postgresql://")
                conn_db = psycopg2.connect(conn_str)
                cur = conn_db.cursor()
                cur.execute(f"SELECT COUNT(*) FROM token_usage {_email_filter}", _email_params)
                count = cur.fetchone()[0]
                cur.execute(f"DELETE FROM token_usage {_email_filter}", _email_params)
                conn_db.commit()
                cur.close()
                conn_db.close()
                print(f"  ✅ Deleted {count} remote token_usage rows from Postgres (scope={user_email or 'all'})")
            except Exception as e:
                print(f"  ⚠️  Direct Postgres clear failed: {e}")
        elif not _skip_auth:
            # Last resort: device flow auth + API call (server will scope via JWT)
            try:
                import urllib.request
                from api.device_auth import get_auth_headers
                print("  🔐 Authenticating with token-flow (SSO fallback)...")
                auth_headers = get_auth_headers()
                req = urllib.request.Request(
                    f"{api_url}/token-data/clear",
                    method="DELETE",
                    headers=auth_headers,
                )
                with urllib.request.urlopen(req, timeout=10) as r:
                    body = r.read().decode()
                    print(f"  ✅ token_usage cleared via API (SSO): {body}")
            except Exception as e:
                print(f"  ⚠️  Could not clear remote token_usage via API: {e}")
        else:
            print("  ℹ️  No remote DATABASE_URL / JWT — only local SQLite was cleared")

    # ── 5. Log distill event to pipeline_events ───────────────────────────────
    try:
        from api.push_client import log_pipeline_event
        # Always use TOKEN_FLOW_DB — that's where token_usage / pipeline_events live.
        # DB_PATH points to memory/context.db (summarization) which has neither table.
        db_path = os.environ.get("TOKEN_FLOW_DB", "/home/ec2-user/.openclaw/data/token_flow.db")
        log_pipeline_event(db_path, "distill", {
            "triggered_by": triggered_by,
            "cleared_sessions": cleared,
            "timestamp": _dt.utcnow().isoformat(),
        })
        print(f"  ✅ Distill event logged (triggered_by={triggered_by})")
    except Exception as e:
        print(f"  ⚠️  Could not log pipeline event: {e}")

    # ── 6. Push fresh snapshot so UI reflects the clear immediately ───────────
    # token_usage rows live in Postgres (DATABASE_URL), not the local SQLite
    # TOKEN_FLOW_DB. Prefer DATABASE_URL so _build_snapshot() reads the right DB.
    # Since we just cleared token_usage via the API, we also push a minimal
    # zero-summary payload directly so the UI blanks immediately without waiting
    # for the next 30-second push cycle.
    try:
        from api.push_client import push_snapshot, _build_token_data, _build_session_data, _build_snapshot as _push_build_snapshot
        _db_url = (
            os.environ.get("DATABASE_URL", "").strip()
            or f"sqlite:///{os.environ.get('TOKEN_FLOW_DB', '/home/ec2-user/.openclaw/data/token_flow.db')}"
        )
        # Build a full snapshot from the local DB so chunks/memory_entries/pipeline_events
        # are preserved.  Only zero out token_usage summary + events (just cleared above).
        # This ensures the Summaries page continues to show Haiku-summarized chunk data
        # after a distill+clear cycle.
        try:
            _full_snap = _push_build_snapshot(_db_url)
        except Exception:
            _full_snap = {}
        _post_distill_payload = {
            "ts": _dt.utcnow().isoformat() + "Z",
            "summary": {
                "rows": [],
                "grand_total_tokens": 0,
                "grand_total_calls": 0,
                "grand_cost_usd": 0.0,
            },
            "events": [],
            # Preserve chunks, memory_entries, pipeline_events from local DB
            "chunks":          _full_snap.get("chunks", []),
            "memory_entries":  _full_snap.get("memory_entries", []),
            "pipeline_events": _full_snap.get("pipeline_events", []),
            "tokens": _build_token_data(),
            "session": _build_session_data(),
        }
        push_snapshot(_db_url, payload=_post_distill_payload)
        print(f"  ✅ Pushed fresh snapshot to UI after distill+clear ({len(_post_distill_payload['chunks'])} chunks preserved)")
    except Exception as e:
        print(f"  ⚠️  Could not push post-distill snapshot: {e}")


def poll_sqs(args_ns):
    """
    Long-poll SQS for distill+clear trigger messages. Runs forever (daemon mode).

    Required env vars:
      MEMORY_DISTILL_QUEUE_URL  — SQS queue URL
      TOKEN_FLOW_API_URL        — token-flow service base URL (default: http://localhost:8001)
      AWS_REGION                — defaults to us-west-2

    SSO auth is required before polling starts. If no valid cached token exists,
    the Auth0 Device Flow is triggered — a URL is printed for the user to visit.
    """
    import boto3

    # ── SSO Auth + local session registration ────────────────────────────────
    # When TOKEN_FLOW_JWT is set (injected by manage.sh), skip the interactive
    # Auth0 device-flow — the pre-minted JWT handles all authenticated calls.
    # In interactive local dev mode (no TOKEN_FLOW_JWT), run the device-flow so
    # the dashboard can show who is running the local service.
    _skip_auth = bool(os.environ.get("TOKEN_FLOW_JWT", "").strip())
    user_email = "sqs-poller@token-flow.internal" if _skip_auth else "unknown"

    if _skip_auth:
        print("[SQS poller] 🔐 Skipping SSO device-flow (TOKEN_FLOW_JWT provided)")
    else:
        print("[SQS poller] Authenticating with Auth0 SSO...")
        try:
            import urllib.request as _urllib_req
            from api.device_auth import get_token, _load_cache
            import json as _json2, time as _time

            # Get token (device flow if no cache) — userinfo is cached alongside it
            get_token()
            from api.device_auth import get_cached_user
            user_info = get_cached_user()

            user_email = user_info.get("email", "unknown")
            print(f"[SQS poller] ✅ Authenticated as {user_email}")

            # Register with the API
            api_url = os.environ.get("TOKEN_FLOW_API_URL", "http://localhost:8001")
            import socket as _socket
            payload = _json2.dumps({
                "email":    user_email,
                "name":     user_info.get("name"),
                "picture":  user_info.get("picture"),
                "auth0_sub": user_info.get("sub"),
                "host":     _socket.gethostname(),
            }).encode()
            id_req = _urllib_req.Request(
                f"{api_url}/session/identify",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _urllib_req.urlopen(id_req, timeout=5) as r:
                print(f"[SQS poller] ✅ Registered local session: {r.read().decode()}")

        except Exception as e:
            print(f"[SQS poller] ⚠️  Auth/identify failed (continuing): {e}")
    # ─────────────────────────────────────────────────────────────────────────

    queue_url = os.environ.get(
        "MEMORY_DISTILL_QUEUE_URL",
        "https://sqs.us-west-2.amazonaws.com/531948420901/freightdawg-memory-distill",
    )
    region = os.environ.get("AWS_REGION", "us-west-2")
    sqs = boto3.client("sqs", region_name=region)

    print(f"[SQS poller] Listening on {queue_url} (region={region})")

    # Resolve the startup token once — reuse it for all distill calls.
    # TOKEN_FLOW_JWT is minted by manage.sh start-poller from SECRET_KEY so
    # the poller never needs an interactive device-flow / browser prompt.
    startup_token: Optional[str] = os.environ.get("TOKEN_FLOW_JWT", "").strip() or None

    if startup_token:
        print(f"[SQS poller] ✅ Using TOKEN_FLOW_JWT for authenticated push/clear calls")
    else:
        # Fallback: cached device-flow token (interactive local dev)
        try:
            from api.device_auth import _load_cache
            startup_token = _load_cache()
            if startup_token:
                print(f"[SQS poller] ✅ Using cached device-flow token for distill calls")
        except Exception:
            pass

    if not startup_token:
        print(f"[SQS poller] ⚠️  No token available — distill clear will fall back to direct DB path")

    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=1,
                WaitTimeSeconds=20,
                VisibilityTimeout=300,
            )
            messages = resp.get("Messages", [])
            if not messages:
                continue

            msg = messages[0]
            receipt = msg["ReceiptHandle"]

            try:
                body = json.loads(msg["Body"])
                action       = body.get("action", "")
                triggered_by = body.get("triggered_by", "unknown")
                msg_user_email = body.get("user_email")   # scopes the token_usage DELETE
                print(f"[SQS poller] Received: action={action} triggered_by={triggered_by} user_email={msg_user_email or 'all'} at {body.get('requested_at','?')}")
            except Exception as e:
                print(f"[SQS poller] Bad message body: {e} — deleting")
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)
                continue

            if action == "distill_and_clear":
                # Full distill + token clear — server already verified the requester
                # is the machine owner before sending this action.
                print(f"[SQS poller] Starting distill+clear (triggered by {triggered_by})...")
                try:
                    run_distill_and_clear(args_ns, triggered_by=triggered_by,
                                          auth_token=startup_token,
                                          user_email=msg_user_email or None)
                    print("[SQS poller] ✅ distill+clear complete")
                except Exception as e:
                    print(f"[SQS poller] ⚠️  distill+clear failed: {e}")

            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)

        except KeyboardInterrupt:
            print("\n[SQS poller] Stopped.")
            break
        except Exception as e:
            print(f"[SQS poller] Error: {e} — retrying in 5s")
            import time; time.sleep(5)


def main():
    parser = argparse.ArgumentParser(description="Memory distillation skill")
    parser.add_argument("action", choices=["ingest", "rebuild", "query", "full", "poll-sqs"],
                        help="ingest: load files into DB | rebuild: write distilled context | "
                             "full: ingest+clear+rebuild | query: print top entries | "
                             "poll-sqs: daemon — long-poll SQS for distill+clear triggers")
    parser.add_argument("--file", help="Specific memory file to ingest (default: all *.md in MEMORY_DIR)")
    parser.add_argument("--output", default=str(WORKSPACE / "memory" / "2026-03-20.md"),
                        help="Output memory file path for rebuild")
    parser.add_argument("--top", type=int, default=20, help="Top N entries to include in rebuild")
    parser.add_argument("--min-relevance", type=float, default=0.5, help="Minimum relevance threshold")
    parser.add_argument("--clear", action="store_true", help="Clear source file(s) after ingest")
    parser.add_argument("--context-hint", default="FreightDawg SoCal freight dispatch app on AWS ECS",
                        help="Context hint passed to Claude for better categorization")
    parser.add_argument("--git-since", default=None,
                        help="Git history window (default: GIT_SINCE env or '24 hours ago')")
    parser.add_argument("--claude-sessions-dir", default=None,
                        help="Additional directory to scan for claude CLI .jsonl session files")
    args = parser.parse_args()

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if args.action in ("ingest", "full"):
        # Ingest markdown memory files
        md_files = [Path(args.file)] if args.file else list(MEMORY_DIR.glob("*.md"))
        for f in md_files:
            if f.suffix != ".md":
                continue
            print(f"Ingesting memory file: {f.name}...")
            ingest_memory_file(conn, f, context_hint=args.context_hint)
            if args.clear or args.action == "full":
                f.write_text(
                    f"# Memory — {f.stem}\n\n"
                    f"*Cleared after distillation on {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.*\n"
                    f"*Context stored in context.db — rebuilt below.*\n"
                )
                print(f"  Cleared {f.name}")

        # Ingest today's git commit history from workspace
        git_since = args.git_since or os.environ.get("GIT_SINCE", "24 hours ago")
        print(f"\nIngesting git history from {WORKSPACE} (since '{git_since}')...")
        ingest_git_history(conn, WORKSPACE, context_hint=args.context_hint, since=git_since)

        # Ingest OpenClaw session transcripts (.jsonl) if sessions_dir is set
        sessions_dir = Path(os.environ.get("SESSIONS_DIR", ""))
        if sessions_dir.exists():
            jsonl_files = list(sessions_dir.glob("*.jsonl"))
            print(f"\nIngesting {len(jsonl_files)} session transcript(s) from {sessions_dir}...")
            for f in jsonl_files:
                ingest_session_file(conn, f, context_hint=args.context_hint)

        # Ingest claude CLI session transcripts (auto-discovered)
        claude_cli_files = _find_claude_cli_sessions()
        if args.claude_sessions_dir:
            extra_dir = Path(args.claude_sessions_dir)
            if extra_dir.exists():
                claude_cli_files += list(extra_dir.glob("*.jsonl"))
        if claude_cli_files:
            print(f"\nIngesting {len(claude_cli_files)} claude CLI session(s)...")
            for f in claude_cli_files:
                ingest_session_file(conn, f, context_hint=args.context_hint)

    if args.action in ("rebuild", "full"):
        rebuild_memory(conn, Path(args.output), top_n=args.top)

    if args.action == "query":
        rows = query_context(conn, top_n=args.top, min_relevance=args.min_relevance)
        if not rows:
            print("No entries found.")
        for cat, summary, _, relevance, _ in rows:
            print(f"[{relevance:.2f}] {cat}: {summary}")

    if args.action == "poll-sqs":
        conn.close()
        poll_sqs(args)
        return

    conn.close()


if __name__ == "__main__":
    main()

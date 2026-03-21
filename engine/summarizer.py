"""
Top-chunk summarizer.

Reads unsummarized chunks from chunk_cache, ranks by composite_score,
takes the top *top_pct* fraction, and summarizes each with Claude Haiku.
Saves the summary back to DB and marks is_summarized=1.
"""
from __future__ import annotations

import logging
import math
import re
import sqlite3

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5"

_SUMMARIZE_PROMPT = """\
Summarize the following text chunk, focusing on:
  1. Concrete facts and decisions
  2. User preferences or choices
  3. Intent or plans

Context hint: {context_hint}

Text:
{content}

Write a concise summary in at most 3 sentences. Be specific — preserve names, numbers, and key decisions.
Return ONLY the summary text, no preamble."""


def summarize_top_chunks(
    conn: sqlite3.Connection,
    top_pct: float = 0.4,
    context_hint: str = "",
) -> int:
    """
    Summarize the top *top_pct* fraction of unsummarized chunks by composite_score.

    Args:
        conn:         Open SQLite connection (token-flow DB).
        top_pct:      Fraction of unsummarized chunks to summarize (0.0–1.0).
        context_hint: Optional context passed to Claude for better summaries.

    Returns:
        Number of chunks summarized in this call.
    """
    top_pct = max(0.0, min(1.0, top_pct))

    rows = conn.execute(
        """
        SELECT id, content, composite_score
        FROM chunk_cache
        WHERE is_summarized = 0
        ORDER BY composite_score DESC
        """
    ).fetchall()

    if not rows:
        logger.info("No unsummarized chunks found.")
        return 0

    total = len(rows)
    take = max(1, math.ceil(total * top_pct))
    top_rows = rows[:take]

    logger.info("Summarizing top %d / %d unsummarized chunks (%.0f%%)", take, total, top_pct * 100)

    client = anthropic.Anthropic()
    summarized = 0

    for row_id, content, composite_score in top_rows:
        prompt = _SUMMARIZE_PROMPT.format(
            context_hint=context_hint or "memory/session content",
            content=content[:4000],
        )
        try:
            msg = client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )
            summary = msg.content[0].text.strip()
            # Strip stray markdown fences just in case
            summary = re.sub(r"^```[a-z]*\s*", "", summary)
            summary = re.sub(r"\s*```$", "", summary)
        except Exception as e:
            logger.error("Summarization failed for chunk id=%d: %s", row_id, e)
            summary = content[:300]  # fallback: first 300 chars

        conn.execute(
            """
            UPDATE chunk_cache
            SET summary = ?, is_summarized = 1
            WHERE id = ?
            """,
            (summary, row_id),
        )
        summarized += 1

    conn.commit()
    logger.info("Summarized %d chunks.", summarized)
    return summarized

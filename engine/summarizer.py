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
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5"

_COST_TABLE: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":        (0.80,  4.00),
    "claude-haiku-3-5":        (0.80,  4.00),
    "claude-haiku-3":          (0.25,  1.25),
    "claude-3-haiku-20240307": (0.25,  1.25),
}


def _record_token_usage(
    conn,
    operation: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    source_label: Optional[str] = None,
) -> None:
    """Write a token_usage row to the local DB. Best-effort — never raises."""
    try:
        rates = _COST_TABLE.get(model)
        cost = None
        if rates:
            cost = round(
                (prompt_tokens / 1_000_000) * rates[0]
                + (completion_tokens / 1_000_000) * rates[1],
                6,
            )
        conn.execute(
            """INSERT INTO token_usage
               (operation, model, prompt_tokens, completion_tokens, total_tokens, cost_usd, source_label)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (operation, model, prompt_tokens, completion_tokens,
             prompt_tokens + completion_tokens, cost, source_label),
        )
        conn.commit()
    except Exception as exc:
        logger.debug("_record_token_usage failed (non-fatal): %s", exc)


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
    conn,
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

    def _summarize_one(row):
        row_id, content, _score = row
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
            summary = re.sub(r"^```[a-z]*\s*", "", summary)
            summary = re.sub(r"\s*```$", "", summary)
            return row_id, summary, msg.usage.input_tokens, msg.usage.output_tokens, None
        except Exception as e:
            logger.error("Summarization failed for chunk id=%d: %s", row_id, e)
            return row_id, content[:300], 0, 0, str(e)

    max_workers = min(10, len(top_rows))
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_summarize_one, row): row for row in top_rows}
        for future in as_completed(futures):
            results.append(future.result())

    summarized = 0
    for row_id, summary, prompt_tokens, completion_tokens, err in results:
        conn.execute(
            "UPDATE chunk_cache SET summary = ?, is_summarized = 1 WHERE id = ?",
            (summary, row_id),
        )
        if not err:
            _record_token_usage(
                conn=conn,
                operation="summarize",
                model=_HAIKU_MODEL,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                source_label=f"chunk:{row_id}",
            )
        summarized += 1

    conn.commit()
    logger.info("Summarized %d chunks.", summarized)
    return summarized

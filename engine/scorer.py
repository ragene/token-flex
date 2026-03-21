"""
Per-chunk scorer using Claude Haiku.

Scores each chunk on three dimensions (0.0–1.0):
  - fact_score       : density of concrete facts and decisions
  - preference_score : degree to which user preferences / choices are reflected
  - intent_score     : clarity of what was intended or planned

composite_score = fact*0.4 + preference*0.3 + intent*0.3

Batches up to 5 chunks per API call for efficiency.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

_HAIKU_MODEL = "claude-haiku-4-5"
_BATCH_SIZE = 5

_SCORE_PROMPT_TEMPLATE = """\
You are a memory-relevance scoring engine. Analyze the following text chunks and score each on three dimensions (0.0 to 1.0).

Context hint: {context_hint}

Scoring dimensions:
  - fact_score       (0.0-1.0): How many concrete facts, decisions, numbers, names, or outcomes does this chunk contain?
  - preference_score (0.0-1.0): How clearly does this chunk reflect user preferences, choices, likes/dislikes?
  - intent_score     (0.0-1.0): How clearly does this chunk show what was intended, planned, or requested?

Chunks to score:
{chunks_block}

Return a JSON ARRAY (and nothing else) with one object per chunk:
[
  {{
    "chunk_index": <int>,
    "fact_score": <float 0.0-1.0>,
    "preference_score": <float 0.0-1.0>,
    "intent_score": <float 0.0-1.0>,
    "reasoning": "<one short sentence>"
  }},
  ...
]

Return ONLY valid JSON. No markdown, no extra text."""


def _build_chunks_block(chunks: list[dict]) -> str:
    parts = []
    for c in chunks:
        idx = c["chunk_index"]
        content_preview = c["content"][:600].replace("\n", " ")
        parts.append(f"--- CHUNK {idx} ---\n{content_preview}")
    return "\n\n".join(parts)


def _parse_scores(raw: str, chunks: list[dict]) -> list[dict]:
    """
    Parse the JSON array returned by Claude.
    Falls back gracefully: if a chunk's score is missing, default to 0.5.
    """
    # Strip markdown code fences if present
    raw = re.sub(r"^```json?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw)

    try:
        scored = json.loads(raw)
        if not isinstance(scored, list):
            raise ValueError("Expected JSON array")
    except Exception as e:
        logger.warning("Score parse failed (%s). Assigning defaults.", e)
        # Return all chunks with neutral scores
        result = []
        for c in chunks:
            r = dict(c)
            r.update(
                fact_score=0.5,
                preference_score=0.5,
                intent_score=0.5,
                composite_score=0.5,
                reasoning="parse error — default scores applied",
            )
            result.append(r)
        return result

    # Index by chunk_index for fast lookup
    by_idx: dict[int, dict] = {s["chunk_index"]: s for s in scored if isinstance(s, dict)}

    result = []
    for c in chunks:
        r = dict(c)
        s = by_idx.get(c["chunk_index"], {})
        fact = float(s.get("fact_score", 0.5))
        pref = float(s.get("preference_score", 0.5))
        intent = float(s.get("intent_score", 0.5))
        r["fact_score"] = round(max(0.0, min(1.0, fact)), 4)
        r["preference_score"] = round(max(0.0, min(1.0, pref)), 4)
        r["intent_score"] = round(max(0.0, min(1.0, intent)), 4)
        r["composite_score"] = round(
            r["fact_score"] * 0.4 + r["preference_score"] * 0.3 + r["intent_score"] * 0.3, 4
        )
        r["reasoning"] = s.get("reasoning", "")
        result.append(r)

    return result


def score_chunks(chunks: list[dict], context_hint: str = "") -> list[dict]:
    """
    Score a list of chunk dicts (as returned by chunker.chunk_text).

    Each input dict must have at minimum: {chunk_index, content}.
    Returns a new list of dicts with additional fields:
        fact_score, preference_score, intent_score, composite_score, reasoning

    Batches up to _BATCH_SIZE chunks per Claude API call.
    """
    if not chunks:
        return []

    client = anthropic.Anthropic()
    enriched: list[dict] = []

    for batch_start in range(0, len(chunks), _BATCH_SIZE):
        batch = chunks[batch_start : batch_start + _BATCH_SIZE]
        prompt = _SCORE_PROMPT_TEMPLATE.format(
            context_hint=context_hint or "general memory/session content",
            chunks_block=_build_chunks_block(batch),
        )
        try:
            msg = client.messages.create(
                model=_HAIKU_MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            scored_batch = _parse_scores(raw, batch)
        except Exception as e:
            logger.error("Scoring API call failed for batch starting at %d: %s", batch_start, e)
            # Fall back to default scores for this entire batch
            scored_batch = []
            for c in batch:
                r = dict(c)
                r.update(
                    fact_score=0.5,
                    preference_score=0.5,
                    intent_score=0.5,
                    composite_score=0.5,
                    reasoning=f"API error: {e}",
                )
                scored_batch.append(r)

        enriched.extend(scored_batch)

    return enriched

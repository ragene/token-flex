"""
Text chunker — splits input text into ~4096-token chunks.

Strategy:
  1. Try to load tiktoken (cl100k_base). Fall back to len(text)//4 char estimate.
  2. Split on paragraph boundaries (double newline) first.
     If a paragraph itself exceeds max_tokens, further split on sentence boundaries.
  3. Accumulate segments until the next one would push the chunk over max_tokens;
     then flush and start a new chunk.

Returns:
  list of dicts:  {chunk_index: int, content: str, token_count: int}
"""
from __future__ import annotations

import re
from typing import Callable


def _build_token_counter() -> Callable[[str], int]:
    """Return a function that counts tokens in a string."""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return lambda text: len(enc.encode(text))
    except Exception:
        # Fallback: rough character-based estimate (4 chars ≈ 1 token)
        return lambda text: max(1, len(text) // 4)


_count_tokens = _build_token_counter()


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences using a simple regex heuristic."""
    # Split after sentence-ending punctuation followed by whitespace
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p for p in parts if p.strip()]


def _split_into_segments(text: str) -> list[str]:
    """
    Split text into the smallest natural units (paragraphs, then sentences).
    Each segment is non-empty.
    """
    paragraphs = re.split(r'\n{2,}', text)
    segments: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        segments.append(para)
    return segments


def chunk_text(text: str, max_tokens: int = 4096) -> list[dict]:
    """
    Split *text* into chunks of at most *max_tokens* tokens each.

    Returns a list of dicts:
        [{"chunk_index": 0, "content": "...", "token_count": 1234}, ...]
    """
    if not text or not text.strip():
        return []

    segments = _split_into_segments(text)

    chunks: list[dict] = []
    current_parts: list[str] = []
    current_tokens: int = 0
    chunk_index: int = 0

    def flush() -> None:
        nonlocal chunk_index, current_parts, current_tokens
        if current_parts:
            content = "\n\n".join(current_parts)
            chunks.append(
                {
                    "chunk_index": chunk_index,
                    "content": content,
                    "token_count": _count_tokens(content),
                }
            )
            chunk_index += 1
            current_parts = []
            current_tokens = 0

    for seg in segments:
        seg_tokens = _count_tokens(seg)

        # If a single segment is larger than max_tokens, break it into sentences
        if seg_tokens > max_tokens:
            sentences = _split_into_sentences(seg)
            for sent in sentences:
                sent_tokens = _count_tokens(sent)
                if sent_tokens > max_tokens:
                    # Extreme edge-case: single sentence too long — hard-split by characters
                    step = max_tokens * 4  # approx chars per max_tokens
                    for start in range(0, len(sent), step):
                        part = sent[start : start + step]
                        part_tokens = _count_tokens(part)
                        if current_tokens + part_tokens > max_tokens:
                            flush()
                        current_parts.append(part)
                        current_tokens += part_tokens
                else:
                    if current_tokens + sent_tokens > max_tokens:
                        flush()
                    current_parts.append(sent)
                    current_tokens += sent_tokens
        else:
            if current_tokens + seg_tokens > max_tokens:
                flush()
            current_parts.append(seg)
            current_tokens += seg_tokens

    flush()  # flush any remaining content

    return chunks

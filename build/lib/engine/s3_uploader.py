"""
S3 uploader for token-flow chunk summaries.

Pushes summarized-but-not-yet-pushed chunk_cache rows to S3 as JSON objects.

Key pattern:
    token-flow/summaries/{YYYY}/{MM}/{DD}/{source_label}_{chunk_index}.json

Each JSON payload:
    {
        "source_label":  str,
        "chunk_index":   int,
        "content":       str,
        "summary":       str,
        "scores": {
            "fact":       float,
            "preference": float,
            "intent":     float,
            "composite":  float
        },
        "token_count":   int,
        "created_at":    str
    }

Marks pushed rows with pushed_to_s3_at (UTC datetime string) and s3_key.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


def _safe_label(label: str) -> str:
    """Convert source_label into a safe S3 key component."""
    # Replace path separators and whitespace with underscores
    label = re.sub(r"[\s/\\:]+", "_", label or "unknown")
    # Keep only alphanumeric, underscore, hyphen, dot
    label = re.sub(r"[^A-Za-z0-9._\-]", "", label)
    return label[:80] or "unknown"


def push_summaries_to_s3(conn, bucket: str) -> int:
    """
    Push all summarized-but-not-yet-pushed chunk_cache rows to S3.

    Args:
        conn:   Open SQLite connection.
        bucket: S3 bucket name.

    Returns:
        Number of rows successfully pushed.
    """
    if not bucket:
        raise ValueError("S3 bucket name must not be empty.")

    rows = conn.execute(
        """
        SELECT
            id,
            source_label,
            chunk_index,
            content,
            summary,
            fact_score,
            preference_score,
            intent_score,
            composite_score,
            token_count,
            created_at
        FROM chunk_cache
        WHERE is_summarized = 1
          AND pushed_to_s3_at IS NULL
        ORDER BY id
        """
    ).fetchall()

    if not rows:
        logger.info("No unpushed summaries found.")
        return 0

    s3 = boto3.client("s3")
    now = datetime.now(timezone.utc)
    date_prefix = now.strftime("%Y/%m/%d")
    pushed = 0

    for (
        row_id,
        source_label,
        chunk_index,
        content,
        summary,
        fact_score,
        preference_score,
        intent_score,
        composite_score,
        token_count,
        created_at,
    ) in rows:
        safe_label = _safe_label(source_label or "unknown")
        s3_key = (
            f"token-flow/summaries/{date_prefix}/{safe_label}_{chunk_index}.json"
        )

        payload = {
            "source_label": source_label,
            "chunk_index": chunk_index,
            "content": content,
            "summary": summary,
            "scores": {
                "fact": fact_score,
                "preference": preference_score,
                "intent": intent_score,
                "composite": composite_score,
            },
            "token_count": token_count,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at) if created_at else None,
        }

        try:
            s3.put_object(
                Bucket=bucket,
                Key=s3_key,
                Body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                ContentType="application/json",
            )
        except (BotoCoreError, ClientError) as e:
            logger.error("S3 upload failed for chunk id=%d key=%s: %s", row_id, s3_key, e)
            continue

        conn.execute(
            """
            UPDATE chunk_cache
            SET pushed_to_s3_at = ?, s3_key = ?
            WHERE id = ?
            """,
            (now.strftime("%Y-%m-%d %H:%M:%S"), s3_key, row_id),
        )
        pushed += 1

    conn.commit()
    logger.info("Pushed %d summaries to s3://%s", pushed, bucket)
    return pushed

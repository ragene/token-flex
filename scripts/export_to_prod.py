#!/usr/bin/env python3
"""
Export local SQLite chunk_cache + memory_entries to the production API via /ingest.

Usage:
    python3 scripts/export_to_prod.py [--prod-url https://token-flow.thefreightdawg.com]
"""
import argparse
import sqlite3
import sys
import time
import requests
from pathlib import Path

DEFAULT_DB = Path("/home/ec2-user/.openclaw/data/token_flow.db")
DEFAULT_PROD = "https://token-flow.thefreightdawg.com"


def export(db_path: Path, prod_url: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Get all unique sources that have chunks
    sources = conn.execute(
        "SELECT DISTINCT source_label FROM chunk_cache WHERE source_label IS NOT NULL ORDER BY source_label"
    ).fetchall()

    print(f"Found {len(sources)} sources to export from {db_path}")
    print(f"Target: {prod_url}\n")

    total_chunks = 0
    errors = 0

    for row in sources:
        source_label = row["source_label"]

        # Get all chunks for this source concatenated as one document
        chunks = conn.execute(
            "SELECT content FROM chunk_cache WHERE source_label = ? ORDER BY chunk_index",
            (source_label,)
        ).fetchall()

        if not chunks:
            continue

        combined = "\n\n---\n\n".join(c["content"] for c in chunks)

        print(f"  [{source_label}] {len(chunks)} chunks → ", end="", flush=True)

        try:
            r = requests.post(
                f"{prod_url}/ingest",
                json={
                    "source": source_label,
                    "text": combined,
                    "source_type": "raw",
                    "context_hint": f"exported from local SQLite: {source_label}"
                },
                timeout=60
            )
            data = r.json()
            if r.status_code == 200:
                print(f"✅ {data.get('chunks_created', '?')} chunks created")
                total_chunks += data.get("chunks_created", 0)
            else:
                print(f"❌ {r.status_code}: {data}")
                errors += 1
        except Exception as e:
            print(f"❌ error: {e}")
            errors += 1

        time.sleep(0.2)  # small delay to avoid overwhelming the API

    conn.close()
    print(f"\nDone. {total_chunks} total chunks created on prod. {errors} errors.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--prod-url", default=DEFAULT_PROD)
    args = parser.parse_args()

    export(Path(args.db), args.prod_url.rstrip("/"))

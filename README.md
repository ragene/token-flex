# token-flow

A standalone FastAPI service that ingests memory and session text, chunks it into ~4096-token segments, scores each chunk with Claude Haiku, summarizes the highest-scoring chunks, and pushes results to S3.

---

## Pipeline

```
POST /ingest
     │
     ▼
┌─────────────────────────────────┐
│  chunker.chunk_text()           │
│  Split on paragraphs / sentences│
│  ~4096 tokens per chunk         │
└─────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────────┐
│  scorer.score_chunks()                              │
│  Claude Haiku scores each chunk (batches of 5):    │
│   • fact_score       (0.0–1.0)                     │
│   • preference_score (0.0–1.0)                     │
│   • intent_score     (0.0–1.0)                     │
│   • composite = fact×0.4 + pref×0.3 + intent×0.3  │
└─────────────────────────────────────────────────────┘
     │  persisted to chunk_cache (SQLite)
     ▼
POST /summarize
     │
     ▼
┌────────────────────────────────────────────────────┐
│  summarizer.summarize_top_chunks()                 │
│  Rank by composite_score, take top 40%            │
│  Claude Haiku → ≤3-sentence summary per chunk     │
└────────────────────────────────────────────────────┘
     │  (optional) push_to_s3=true
     ▼
┌───────────────────────────────────────────────────────────────┐
│  s3_uploader.push_summaries_to_s3()                          │
│  Key: token-flow/summaries/YYYY/MM/DD/{label}_{index}.json   │
│  Marks rows pushed_to_s3_at + s3_key                        │
└───────────────────────────────────────────────────────────────┘
```

---

## Environment Variables

| Variable              | Required | Default         | Description                                  |
|-----------------------|----------|-----------------|----------------------------------------------|
| `TOKEN_FLOW_DB`       | No       | `token_flow.db` | Path to SQLite database file                 |
| `ANTHROPIC_API_KEY`   | **Yes**  | —               | Anthropic API key (for Claude Haiku calls)   |
| `S3_BUCKET`           | No*      | —               | S3 bucket name (*required if push_to_s3=true)|
| `AWS_DEFAULT_REGION`  | No       | `us-east-1`     | AWS region for S3                            |
| `PORT`                | No       | `8001`          | HTTP port to listen on                       |

AWS credentials are resolved via the standard boto3 chain (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / instance profile / `~/.aws/credentials`).

---

## API Reference

### `GET /health`
Liveness check.

**Response:**
```json
{"status": "ok", "db": "token_flow.db", "version": "0.1.0"}
```

---

### `POST /ingest`
Chunk + score text and persist to DB.

**Request body:**
```json
{
  "source": "/path/to/file.md",   // used as source_label; file is read if text omitted
  "text": "optional raw text",    // if provided, source file is NOT read
  "context_hint": "FreightDawg app memory"
}
```

**Response:**
```json
{"chunks_created": 12, "avg_composite_score": 0.6234}
```

---

### `GET /chunks`
List chunks with optional filters.

| Query param | Type    | Default | Description                          |
|-------------|---------|---------|--------------------------------------|
| `min_score` | float   | `0.0`   | Minimum `composite_score`            |
| `limit`     | int     | `50`    | Max rows returned (max 500)          |
| `source`    | string  | —       | Prefix filter on `source_label`      |

---

### `GET /chunks/{id}`
Retrieve a single chunk by its DB primary key.

---

### `POST /summarize`
Run the summarization pipeline.

**Request body:**
```json
{
  "top_pct": 0.4,
  "push_to_s3": false,
  "context_hint": "FreightDawg app memory"
}
```

**Response:**
```json
{"summarized": 5, "pushed": 5}
```

---

### `GET /summaries`
List summarized chunks ordered by `composite_score` descending.

| Query param | Type   | Default | Description                     |
|-------------|--------|---------|---------------------------------|
| `limit`     | int    | `50`    | Max rows returned (max 500)     |
| `source`    | string | —       | Prefix filter on `source_label` |

---

## Local Development

```bash
# 1. Clone and enter the repo
cd token-flow

# 2. Create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY at minimum

# 5. Start the server
python main.py
# → Listening on http://0.0.0.0:8001
# → Interactive docs at http://localhost:8001/docs
```

---

## Docker

```bash
# Build
docker build -t token-flow:latest .

# Run
docker run -d \
  --name token-flow \
  -p 8001:8001 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e S3_BUCKET=my-token-flow-bucket \
  -e AWS_DEFAULT_REGION=us-west-2 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -v $(pwd)/data:/app/data \
  -e TOKEN_FLOW_DB=/app/data/token_flow.db \
  token-flow:latest
```

---

## SQLite Schema

**`memory_entries`** — base memory records (compatible with `memory_distill.py`)

**`chunk_cache`** — 4K-token chunks with scoring and S3 push state

Key columns in `chunk_cache`:

| Column            | Description                                      |
|-------------------|--------------------------------------------------|
| `source_label`    | Identifier for the source (file path, label)     |
| `chunk_index`     | 0-based index within the source                  |
| `token_count`     | Estimated tokens in this chunk                   |
| `fact_score`      | Claude Haiku fact density score (0.0–1.0)        |
| `preference_score`| User preference density score (0.0–1.0)          |
| `intent_score`    | Intent/plan clarity score (0.0–1.0)              |
| `composite_score` | Weighted average (fact×0.4 + pref×0.3 + intent×0.3) |
| `summary`         | Claude Haiku 3-sentence summary (if summarized)  |
| `is_summarized`   | 1 once summarized, 0 otherwise                   |
| `pushed_to_s3_at` | UTC timestamp when pushed to S3 (NULL if not)    |
| `s3_key`          | Full S3 object key                               |

---

## Origin

This service extends [`memory_distill.py`](./memory_distill.py) — a standalone memory distillation script — into a full engine-based HTTP API with chunking, scoring, and cloud export.

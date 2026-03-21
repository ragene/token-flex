#!/bin/bash
# token-flow pipeline cron job
# Runs every 6 hours: ingest new memory/git/sessions, summarize top 40%, push to S3

LOG=/tmp/token-flow-cron.log
BASE_URL="http://localhost:8001"
SINCE="7 hours ago"

echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] token-flow pipeline starting..." >> $LOG

# Step 1: auto-ingest
INGEST=$(curl -s -X POST $BASE_URL/memory/ingest/auto \
  -H "Content-Type: application/json" \
  -d "{\"context_hint\":\"FreightDawg SoCal freight dispatch app on AWS ECS\",\"since\":\"$SINCE\",\"clear_after\":false}" 2>/dev/null)

echo "  ingest: $INGEST" >> $LOG

TOTAL_CHUNKS=$(echo $INGEST | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('total_chunks',0))" 2>/dev/null || echo 0)

if [ "$TOTAL_CHUNKS" -gt 0 ] 2>/dev/null; then
  # Step 2: summarize + push to S3
  SUMMARIZE=$(curl -s -X POST $BASE_URL/summarize \
    -H "Content-Type: application/json" \
    -d '{"top_pct":0.4,"push_to_s3":true,"context_hint":"FreightDawg SoCal freight dispatch app on AWS ECS"}' 2>/dev/null)
  echo "  summarize: $SUMMARIZE" >> $LOG
  SUMMARIZED=$(echo $SUMMARIZE | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('summarized',0))" 2>/dev/null || echo 0)
  PUSHED=$(echo $SUMMARIZE | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pushed',0))" 2>/dev/null || echo 0)
  echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] Done — chunks: $TOTAL_CHUNKS ingested, $SUMMARIZED summarized, $PUSHED pushed to S3" >> $LOG
else
  echo "[$(date -u '+%Y-%m-%d %H:%M UTC')] No new chunks — skipping summarize/push" >> $LOG
fi

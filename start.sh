#!/usr/bin/env bash
# Starts the token-flow API + SQS distill poller as sibling processes.
# The API is the primary process; the poller exits if the API exits.
set -euo pipefail

# ── SSO Auth Gate ─────────────────────────────────────────────────────────────
# Authenticate the user via Auth0 Device Flow before starting any service.
# If a valid cached token exists it completes silently; otherwise a URL is
# printed for the user to visit in their browser.
echo "🔐 Authenticating with Auth0 SSO..."
python3 - <<'PYEOF'
import sys, os
sys.path.insert(0, "/app")
try:
    from api.device_auth import get_token
    get_token()
    print("✅ Authenticated — starting services")
except Exception as e:
    print(f"❌ Authentication failed: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF
# ─────────────────────────────────────────────────────────────────────────────

echo "🚀 Starting token-flow API on port ${PORT:-8001}..."
python3 /app/main.py &
API_PID=$!

echo "🔄 Starting SQS distill poller..."
python3 /app/memory_distill.py poll-sqs \
  --output "${MEMORY_DIR:-/app/memory}/distilled.md" \
  --context-hint "${CONTEXT_HINT:-FreightDawg SoCal freight dispatch app on AWS ECS}" &
POLLER_PID=$!

echo "   API PID   : $API_PID"
echo "   Poller PID: $POLLER_PID"

# If either process exits, kill the other and exit
wait_any() {
  while kill -0 $API_PID 2>/dev/null && kill -0 $POLLER_PID 2>/dev/null; do
    sleep 5
  done
  if ! kill -0 $API_PID 2>/dev/null; then
    echo "⚠️  API process exited — stopping poller"
    kill $POLLER_PID 2>/dev/null || true
  else
    echo "⚠️  Poller process exited — API still running"
    # Poller death is non-fatal; restart it
    while true; do
      echo "🔄 Restarting SQS poller..."
      python3 /app/memory_distill.py poll-sqs \
        --output "${MEMORY_DIR:-/app/memory}/distilled.md" \
        --context-hint "${CONTEXT_HINT:-FreightDawg SoCal freight dispatch app on AWS ECS}" &
      POLLER_PID=$!
      echo "   New poller PID: $POLLER_PID"
      wait $POLLER_PID || true
      echo "⚠️  Poller exited again — will retry in 10s"
      sleep 10
      kill -0 $API_PID 2>/dev/null || break
    done
    wait $API_PID
  fi
}

wait_any

#!/usr/bin/env bash
# token-flow service manager
# Usage: manage.sh [start|stop|restart|status|install-deps]
# Supports: Linux, macOS
set -euo pipefail

PORT="${TOKEN_FLOW_PORT:-8001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SCRIPT="${SCRIPT_DIR}/main.py"

# ── OS detection ─────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
  Linux*)  PLATFORM="linux" ;;
  Darwin*) PLATFORM="mac"   ;;
  *)       PLATFORM="unknown" ;;
esac

# ── Platform-specific paths ───────────────────────────────────────────────────
if [[ "$PLATFORM" == "mac" ]]; then
  TMP_DIR="${TMPDIR:-/tmp}"
  DEFAULT_DB="$HOME/.openclaw/data/token_flow.db"
  DEFAULT_WORKSPACE="$HOME/.openclaw/workspace"
  DEFAULT_MEMORY_DIR="$HOME/.openclaw/workspace/memory"
  DEFAULT_SESSIONS_DIR="$HOME/.openclaw/agents/main/sessions"
  DEFAULT_AUTH_PROFILES="$HOME/.openclaw/agents/main/agent/auth-profiles.json"
  DEFAULT_TF_AUTH="$HOME/.openclaw/tf_auth.json"
else
  TMP_DIR="/tmp"
  DEFAULT_DB="/home/ec2-user/.openclaw/data/token_flow.db"
  DEFAULT_WORKSPACE="/home/ec2-user/.openclaw/workspace"
  DEFAULT_MEMORY_DIR="/home/ec2-user/.openclaw/workspace/memory"
  DEFAULT_SESSIONS_DIR="/home/ec2-user/.openclaw/agents/main/sessions"
  DEFAULT_AUTH_PROFILES="/home/ec2-user/.openclaw/agents/main/agent/auth-profiles.json"
  DEFAULT_TF_AUTH="/home/ec2-user/.openclaw/tf_auth.json"
fi

PID_FILE="${TMP_DIR}/token-flow.pid"
LOG_FILE="${TMP_DIR}/token-flow.log"

cmd="${1:-status}"

_is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

# Returns PID using the port (cross-platform), or empty string
_pid_on_port() {
  local pid=""
  if [[ "$PLATFORM" == "mac" ]]; then
    pid=$(lsof -ti tcp:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)
  else
    # Linux: try ss first, fall back to /proc/net/tcp
    if command -v ss &>/dev/null; then
      pid=$(ss -tlnp "sport = :${PORT}" 2>/dev/null | awk 'NR>1 {match($6,/pid=([0-9]+)/,a); if(a[1]) print a[1]}' | head -1 || true)
    fi
    if [[ -z "$pid" ]]; then
      local _hex_port; _hex_port=$(printf '%04X' "$PORT")
      local _inode; _inode=$(awk "toupper(\$2) ~ /0\\.0\\.0\\.0:${_hex_port}/ && \$4 == \"0A\" {print \$10; exit}" /proc/net/tcp 2>/dev/null || true)
      if [[ -n "$_inode" ]]; then
        pid=$(grep -rl "socket:\[${_inode}\]" /proc/*/fd 2>/dev/null | head -1 | grep -o '/proc/[0-9]*' | grep -o '[0-9]*' || true)
      fi
    fi
  fi
  echo "$pid"
}

# Resolve ANTHROPIC_API_KEY from env or OpenClaw auth-profiles
_resolve_api_key() {
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    local _key
    _key=$(python3 -c "
import json, pathlib
for p in ['${DEFAULT_AUTH_PROFILES}']:
    try:
        data = json.loads(pathlib.Path(p).expanduser().read_text())
        for name, prof in data.get('profiles', {}).items():
            if 'anthropic' in name.lower():
                k = prof.get('key', '')
                if k.startswith('sk-ant'):
                    print(k); raise SystemExit(0)
    except SystemExit: raise
    except: pass
" 2>/dev/null || true)
    if [[ -n "$_key" ]]; then
      export ANTHROPIC_API_KEY="$_key"
      echo "   API key: resolved from OpenClaw auth-profiles"
    else
      echo "   ⚠️  ANTHROPIC_API_KEY not found — Claude summarization will use fallback mode"
      echo "   Set it with: export ANTHROPIC_API_KEY=sk-ant-... before running start"
    fi
  else
    echo "   API key: found in environment"
  fi
}

case "$cmd" in
  install-deps)
    pip3 install -q "fastapi>=0.111" "uvicorn[standard]>=0.29" "anthropic>=0.25" \
      "boto3>=1.34" "tiktoken>=0.7" "python-dotenv>=1.0"
    echo "✅ Dependencies installed."
    ;;

  start)
    if _is_running; then
      echo "⚠️  token-flow already running (PID $(cat "$PID_FILE"))"
      exit 0
    fi

    # Kill any stale/untracked process on the port
    _stale_pid="$(_pid_on_port)"
    if [[ -n "$_stale_pid" ]]; then
      echo "⚠️  Port ${PORT} already in use by PID ${_stale_pid} (stale/untracked) — killing it..."
      kill -9 "$_stale_pid" 2>/dev/null || true
      sleep 1
      echo "   Cleared."
    fi

    _resolve_api_key

    # Load .env if present
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
      set -o allexport
      source "${SCRIPT_DIR}/.env"
      set +o allexport
    fi

    _TOKEN_FLOW_DB="${TOKEN_FLOW_DB:-${DEFAULT_DB}}"
    _WORKSPACE="${WORKSPACE:-${DEFAULT_WORKSPACE}}"
    _MEMORY_DIR="${MEMORY_DIR:-${DEFAULT_MEMORY_DIR}}"
    _SESSIONS_DIR="${SESSIONS_DIR:-${DEFAULT_SESSIONS_DIR}}"
    _S3_BUCKET="${S3_BUCKET:-smart-memory}"

    _env=(
      "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}"
      "TOKEN_FLOW_PORT=${PORT}"
      "TOKEN_FLOW_DB=${_TOKEN_FLOW_DB}"
      "WORKSPACE=${_WORKSPACE}"
      "MEMORY_DIR=${_MEMORY_DIR}"
      "SESSIONS_DIR=${_SESSIONS_DIR}"
      "S3_BUCKET=${_S3_BUCKET}"
      "AUTH0_DOMAIN=${AUTH0_DOMAIN:-}"
      "AUTH0_CLIENT_ID=${AUTH0_CLIENT_ID:-}"
      "SECRET_KEY=${SECRET_KEY:-}"
      "TOKEN_FLOW_UI_URL=${TOKEN_FLOW_UI_URL:-}"
    )

    nohup env "${_env[@]}" PYTHONUNBUFFERED=1 python3 -u "$SERVER_SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    echo "   Waiting for service to become healthy..."
    _deadline=$(( $(date +%s) + 120 ))
    _ready=0
    while [[ $(date +%s) -lt $_deadline ]]; do
      if ! _is_running; then
        echo "❌ Process exited before becoming healthy. Check $LOG_FILE"
        exit 1
      fi
      if curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1; then
        _ready=1
        break
      fi
      sleep 2
    done

    if [[ $_ready -eq 1 ]]; then
      echo "✅ token-flow started (PID $(cat "$PID_FILE")) on port ${PORT}"
      echo "   Logs: $LOG_FILE"
    else
      echo "❌ Service did not become healthy within 120s. Check $LOG_FILE"
      exit 1
    fi
    ;;

  stop)
    if _is_running; then
      kill "$(cat "$PID_FILE")" && rm -f "$PID_FILE"
      echo "✅ token-flow stopped."
    else
      echo "ℹ️  token-flow not running."
    fi
    ;;

  restart)
    bash "${BASH_SOURCE[0]}" stop || true
    sleep 1
    bash "${BASH_SOURCE[0]}" start
    ;;

  status)
    if _is_running; then
      echo "✅ token-flow running (PID $(cat "$PID_FILE")) on http://localhost:${PORT}"
      curl -sf "http://localhost:${PORT}/health" | python3 -m json.tool 2>/dev/null || true
    else
      echo "❌ token-flow not running."
    fi
    ;;

  start-poller)
    POLLER_PID_FILE="${TMP_DIR}/token-flow-poller.pid"
    POLLER_LOG_FILE="${TMP_DIR}/token-flow-poller.log"

    if [[ -f "$POLLER_PID_FILE" ]] && kill -0 "$(cat "$POLLER_PID_FILE")" 2>/dev/null; then
      echo "⚠️  SQS poller already running (PID $(cat "$POLLER_PID_FILE"))"
      exit 0
    fi

    _resolve_api_key

    # Load .env if present
    if [[ -f "${SCRIPT_DIR}/.env" ]]; then
      set -o allexport
      source "${SCRIPT_DIR}/.env"
      set +o allexport
    fi

    _WORKSPACE="${WORKSPACE:-${DEFAULT_WORKSPACE}}"
    _MEMORY_DIR="${MEMORY_DIR:-${DEFAULT_MEMORY_DIR}}"
    _QUEUE_URL="${MEMORY_DISTILL_QUEUE_URL:-https://sqs.us-west-2.amazonaws.com/531948420901/freightdawg-memory-distill}"
    _API_URL="${TOKEN_FLOW_API_URL:-http://localhost:${PORT}}"
    _TOKEN_FLOW_DB="${TOKEN_FLOW_DB:-${DEFAULT_DB}}"
    _DATABASE_URL="${DATABASE_URL:-sqlite:///${_TOKEN_FLOW_DB}}"

    _TOKEN_FLOW_JWT=$(python3 -c "
import json, pathlib, time
p = pathlib.Path('${DEFAULT_TF_AUTH}').expanduser()
try:
    d = json.loads(p.read_text())
    if time.time() < d.get('expires_at', 0) - 60:
        print(d['token'])
except Exception:
    pass
" 2>/dev/null || true)

    if [[ -n "$_TOKEN_FLOW_JWT" ]]; then
      echo "   Auth  : using cached token from ${DEFAULT_TF_AUTH}"
    else
      echo "   ⚠️  No valid cached token — run the token-flow service first to authenticate"
    fi

    _TOKEN_FLOW_UI_URL="${TOKEN_FLOW_UI_URL:-}"

    nohup env \
      ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
      WORKSPACE="${_WORKSPACE}" \
      MEMORY_DIR="${_MEMORY_DIR}" \
      MEMORY_DISTILL_QUEUE_URL="${_QUEUE_URL}" \
      TOKEN_FLOW_API_URL="${_API_URL}" \
      DATABASE_URL="${_DATABASE_URL}" \
      TOKEN_FLOW_UI_URL="${_TOKEN_FLOW_UI_URL}" \
      TOKEN_FLOW_JWT="${_TOKEN_FLOW_JWT}" \
      PYTHONUNBUFFERED=1 \
      python3 -u "${SCRIPT_DIR}/memory_distill.py" poll-sqs \
        --output "${_MEMORY_DIR}/distilled.md" \
        --context-hint "FreightDawg SoCal freight dispatch app on AWS ECS" \
      >> "$POLLER_LOG_FILE" 2>&1 &
    echo $! > "$POLLER_PID_FILE"
    sleep 1
    if kill -0 "$(cat "$POLLER_PID_FILE")" 2>/dev/null; then
      echo "✅ SQS poller started (PID $(cat "$POLLER_PID_FILE"))"
      echo "   Queue : ${_QUEUE_URL}"
      echo "   Logs  : ${POLLER_LOG_FILE}"
    else
      echo "❌ SQS poller failed to start. Check ${POLLER_LOG_FILE}"
      exit 1
    fi
    ;;

  stop-poller)
    POLLER_PID_FILE="${TMP_DIR}/token-flow-poller.pid"
    if [[ -f "$POLLER_PID_FILE" ]] && kill -0 "$(cat "$POLLER_PID_FILE")" 2>/dev/null; then
      kill "$(cat "$POLLER_PID_FILE")" && rm -f "$POLLER_PID_FILE"
      echo "✅ SQS poller stopped."
    else
      echo "ℹ️  SQS poller not running."
    fi
    ;;

  status-poller)
    POLLER_PID_FILE="${TMP_DIR}/token-flow-poller.pid"
    if [[ -f "$POLLER_PID_FILE" ]] && kill -0 "$(cat "$POLLER_PID_FILE")" 2>/dev/null; then
      echo "✅ SQS poller running (PID $(cat "$POLLER_PID_FILE"))"
    else
      echo "❌ SQS poller not running."
    fi
    ;;

  *)
    echo "Usage: $0 [start|stop|restart|status|install-deps|start-poller|stop-poller|status-poller]"
    exit 1
    ;;
esac

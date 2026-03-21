#!/usr/bin/env bash
# token-flow service manager
# Usage: manage.sh [start|stop|restart|status|install-deps]
set -euo pipefail

PORT="${TOKEN_FLOW_PORT:-8001}"
PID_FILE="/tmp/token-flow.pid"
LOG_FILE="/tmp/token-flow.log"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SCRIPT="${SCRIPT_DIR}/main.py"

cmd="${1:-status}"

_is_running() {
  [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
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

    # Resolve ANTHROPIC_API_KEY — env first, then OpenClaw auth-profiles.json
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
      _key=$(python3 -c "
import json, pathlib
for p in [
    '~/.openclaw/agents/main/agent/auth-profiles.json',
]:
    try:
        data = json.loads(pathlib.Path(p).expanduser().read_text())
        profiles = data.get('profiles', {})
        for name, prof in profiles.items():
            if 'anthropic' in name.lower():
                k = prof.get('key', '')
                if k.startswith('sk-ant'):
                    print(k)
                    raise SystemExit(0)
    except SystemExit:
        raise
    except:
        pass
" 2>/dev/null)
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

    # Build env for the subprocess — only pass non-empty overrides so defaults survive
    _env=(
      "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}"
      "TOKEN_FLOW_PORT=${PORT}"
    )
    # Defaults for path vars — can be overridden by caller env
    _TOKEN_FLOW_DB="${TOKEN_FLOW_DB:-/home/ec2-user/.openclaw/data/token_flow.db}"
    _WORKSPACE="${WORKSPACE:-/home/ec2-user/.openclaw/workspace}"
    _MEMORY_DIR="${MEMORY_DIR:-/home/ec2-user/.openclaw/workspace/memory}"
    _SESSIONS_DIR="${SESSIONS_DIR:-/home/ec2-user/.openclaw/agents/main/sessions}"
    _S3_BUCKET="${S3_BUCKET:-smart-memory}"

    _env+=(
      "TOKEN_FLOW_DB=${_TOKEN_FLOW_DB}"
      "WORKSPACE=${_WORKSPACE}"
      "MEMORY_DIR=${_MEMORY_DIR}"
      "SESSIONS_DIR=${_SESSIONS_DIR}"
      "S3_BUCKET=${_S3_BUCKET}"
    )

    nohup env "${_env[@]}" python3 "$SERVER_SCRIPT" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if _is_running; then
      echo "✅ token-flow started (PID $(cat "$PID_FILE")) on port ${PORT}"
      echo "   Logs: $LOG_FILE"
    else
      echo "❌ Failed to start. Check $LOG_FILE"
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

  *)
    echo "Usage: $0 [start|stop|restart|status|install-deps]"
    exit 1
    ;;
esac

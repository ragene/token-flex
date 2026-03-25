#!/usr/bin/env bash
# token-flow service manager
# Usage: manage.sh [start|stop|restart|status|install-deps]
# Supports: Linux (systemd user service), macOS (nohup fallback)
set -euo pipefail

# ── OS detection (early — needed by delegation block below) ──────────────────
OS="$(uname -s)"
case "$OS" in
  Linux*)  PLATFORM="linux" ;;
  Darwin*) PLATFORM="mac"   ;;
  *)       PLATFORM="unknown" ;;
esac

PORT="${TOKEN_FLOW_PORT:-8001}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SCRIPT="${SCRIPT_DIR}/main.py"

# ── Service-manager delegation ────────────────────────────────────────────────
# On Linux  → systemd user service (token-flow.service)
# On macOS  → launchd user agent  (com.freightdawg.token-flow)
# Falls through to legacy nohup path if neither is available / installed.

LAUNCHD_LABEL="com.freightdawg.token-flow"
LAUNCHD_PLIST="${HOME}/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"

_systemd_available() {
  [[ "$PLATFORM" == "linux" ]] && command -v systemctl &>/dev/null && \
    systemctl --user list-units &>/dev/null 2>&1
}

_launchd_installed() {
  [[ "$PLATFORM" == "mac" ]] && [[ -f "$LAUNCHD_PLIST" ]]
}

_systemd_delegate() {
  local subcmd="$1"
  case "$subcmd" in
    start)
      systemctl --user start token-flow
      sleep 2
      systemctl --user status token-flow --no-pager
      ;;
    stop)
      systemctl --user stop token-flow
      echo "✅ token-flow stopped."
      ;;
    restart)
      systemctl --user restart token-flow
      sleep 2
      systemctl --user status token-flow --no-pager
      ;;
    status)
      systemctl --user status token-flow --no-pager
      ;;
  esac
}

_launchd_delegate() {
  local subcmd="$1"
  case "$subcmd" in
    start)
      launchctl load -w "$LAUNCHD_PLIST" 2>/dev/null || launchctl start "$LAUNCHD_LABEL" 2>/dev/null || true
      sleep 2
      launchctl list | grep "$LAUNCHD_LABEL" || echo "  (service not listed — may have failed to start)"
      curl -sf "http://localhost:${PORT}/health" && echo "✅ token-flow running on http://localhost:${PORT}" || echo "⚠️  /health not responding yet"
      ;;
    stop)
      launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
      echo "✅ token-flow stopped."
      ;;
    restart)
      launchctl stop "$LAUNCHD_LABEL" 2>/dev/null || true
      sleep 2
      launchctl start "$LAUNCHD_LABEL" 2>/dev/null || true
      sleep 2
      curl -sf "http://localhost:${PORT}/health" && echo "✅ token-flow running on http://localhost:${PORT}" || echo "⚠️  /health not responding yet"
      ;;
    status)
      local entry
      entry=$(launchctl list | grep "$LAUNCHD_LABEL" || true)
      if [[ -n "$entry" ]]; then
        echo "✅ token-flow running ($LAUNCHD_LABEL)"
        echo "   $entry"
        curl -sf "http://localhost:${PORT}/health" | python3 -m json.tool 2>/dev/null || true
      else
        echo "❌ token-flow not running."
      fi
      ;;
  esac
}

_early_cmd="${1:-status}"

if _systemd_available && [[ "$_early_cmd" =~ ^(start|stop|restart|status)$ ]]; then
  _systemd_delegate "$_early_cmd"
  exit $?
fi

if _launchd_installed && [[ "$_early_cmd" =~ ^(start|stop|restart|status)$ ]]; then
  _launchd_delegate "$_early_cmd"
  exit $?
fi
# ─────────────────────────────────────────────────────────────────────────────

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

# Load cached internal JWT from tf_auth.json (used for authenticated push/record calls)
_load_tf_jwt() {
  python3 -c "
import json, pathlib, time
p = pathlib.Path('${DEFAULT_TF_AUTH}').expanduser()
try:
    d = json.loads(p.read_text())
    if time.time() < d.get('expires_at', 0) - 60:
        print(d['token'])
except Exception:
    pass
" 2>/dev/null || true
}

# Resolve owner email from tf_auth.json cache (used to tag push snapshots)
_resolve_owner_email() {
  python3 -c "
import json, pathlib, time
p = pathlib.Path('${DEFAULT_TF_AUTH}').expanduser()
try:
    d = json.loads(p.read_text())
    if time.time() < d.get('expires_at', 0) - 60:
        email = (d.get('user') or {}).get('email', '').strip()
        if email: print(email)
except Exception:
    pass
" 2>/dev/null || true
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
    _OWNER_EMAIL="${OWNER_EMAIL:-$(_resolve_owner_email)}"

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
      "TOKEN_FLOW_JWT=$(_load_tf_jwt)"
      "OWNER_EMAIL=${_OWNER_EMAIL}"
      "SKIP_STARTUP_AUTH=false"
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
    _OWNER_EMAIL="${OWNER_EMAIL:-$(_resolve_owner_email)}"

    nohup env \
      ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
      WORKSPACE="${_WORKSPACE}" \
      MEMORY_DIR="${_MEMORY_DIR}" \
      MEMORY_DISTILL_QUEUE_URL="${_QUEUE_URL}" \
      TOKEN_FLOW_API_URL="${_API_URL}" \
      DATABASE_URL="${_DATABASE_URL}" \
      TOKEN_FLOW_UI_URL="${_TOKEN_FLOW_UI_URL}" \
      TOKEN_FLOW_JWT="${_TOKEN_FLOW_JWT}" \
      OWNER_EMAIL="${_OWNER_EMAIL}" \
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

  install-service)
    if [[ "$PLATFORM" == "linux" ]]; then
      # ── Linux: systemd user service ──────────────────────────────────────
      UNIT_DIR="${HOME}/.config/systemd/user"
      UNIT_FILE="${UNIT_DIR}/token-flow.service"
      mkdir -p "$UNIT_DIR"

      # Load .env to embed values into the unit file
      if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        set -o allexport; source "${SCRIPT_DIR}/.env"; set +o allexport
      fi

      _resolve_api_key

      cat > "$UNIT_FILE" <<UNIT
[Unit]
Description=Token Flow API
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${SCRIPT_DIR}/.env
Environment=TOKEN_FLOW_PORT=${PORT}
Environment=TOKEN_FLOW_DB=${TOKEN_FLOW_DB:-${DEFAULT_DB}}
Environment=WORKSPACE=${WORKSPACE:-${DEFAULT_WORKSPACE}}
Environment=MEMORY_DIR=${MEMORY_DIR:-${DEFAULT_MEMORY_DIR}}
Environment=SESSIONS_DIR=${SESSIONS_DIR:-${DEFAULT_SESSIONS_DIR}}
Environment=S3_BUCKET=${S3_BUCKET:-smart-memory}
Environment=OWNER_EMAIL=${OWNER_EMAIL:-admin@thefreightdawg.com}
Environment=SKIP_STARTUP_AUTH=false
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 -u ${SERVER_SCRIPT}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT

      systemctl --user daemon-reload
      systemctl --user enable token-flow
      echo "✅ systemd user service installed and enabled."
      echo "   Unit : $UNIT_FILE"
      echo "   Start: manage.sh start"

    elif [[ "$PLATFORM" == "mac" ]]; then
      # ── macOS: launchd user agent ────────────────────────────────────────
      mkdir -p "${HOME}/Library/LaunchAgents"
      mkdir -p "${TMP_DIR}"

      # Load .env
      if [[ -f "${SCRIPT_DIR}/.env" ]]; then
        set -o allexport; source "${SCRIPT_DIR}/.env"; set +o allexport
      fi
      _resolve_api_key

      PYTHON_BIN="$(command -v python3)"
      LOG_OUT="${TMP_DIR}/token-flow.log"
      LOG_ERR="${TMP_DIR}/token-flow-err.log"

      cat > "$LAUNCHD_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LAUNCHD_LABEL}</string>

  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>-u</string>
    <string>${SERVER_SCRIPT}</string>
  </array>

  <key>WorkingDirectory</key>
  <string>${SCRIPT_DIR}</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>TOKEN_FLOW_PORT</key>       <string>${PORT}</string>
    <key>TOKEN_FLOW_DB</key>         <string>${TOKEN_FLOW_DB:-${DEFAULT_DB}}</string>
    <key>WORKSPACE</key>             <string>${WORKSPACE:-${DEFAULT_WORKSPACE}}</string>
    <key>MEMORY_DIR</key>            <string>${MEMORY_DIR:-${DEFAULT_MEMORY_DIR}}</string>
    <key>SESSIONS_DIR</key>          <string>${SESSIONS_DIR:-${DEFAULT_SESSIONS_DIR}}</string>
    <key>S3_BUCKET</key>             <string>${S3_BUCKET:-smart-memory}</string>
    <key>AUTH0_DOMAIN</key>          <string>${AUTH0_DOMAIN:-}</string>
    <key>AUTH0_CLIENT_ID</key>       <string>${AUTH0_CLIENT_ID:-}</string>
    <key>SECRET_KEY</key>            <string>${SECRET_KEY:-}</string>
    <key>TOKEN_FLOW_UI_URL</key>     <string>${TOKEN_FLOW_UI_URL:-}</string>
    <key>ANTHROPIC_API_KEY</key>     <string>${ANTHROPIC_API_KEY:-}</string>
    <key>OWNER_EMAIL</key>           <string>${OWNER_EMAIL:-}</string>
    <key>SKIP_STARTUP_AUTH</key>     <string>false</string>
    <key>PYTHONUNBUFFERED</key>      <string>1</string>
  </dict>

  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>
  <key>ThrottleInterval</key> <integer>5</integer>

  <key>StandardOutPath</key>  <string>${LOG_OUT}</string>
  <key>StandardErrorPath</key><string>${LOG_ERR}</string>
</dict>
</plist>
PLIST

      launchctl load -w "$LAUNCHD_PLIST"
      echo "✅ launchd agent installed and loaded."
      echo "   Plist: $LAUNCHD_PLIST"
      echo "   Logs : $LOG_OUT / $LOG_ERR"
      echo "   Start: manage.sh start"

    else
      echo "❌ install-service is not supported on platform: $PLATFORM"
      exit 1
    fi
    ;;

  uninstall-service)
    if [[ "$PLATFORM" == "linux" ]]; then
      systemctl --user stop token-flow 2>/dev/null || true
      systemctl --user disable token-flow 2>/dev/null || true
      UNIT_FILE="${HOME}/.config/systemd/user/token-flow.service"
      rm -f "$UNIT_FILE"
      systemctl --user daemon-reload
      echo "✅ systemd user service removed."

    elif [[ "$PLATFORM" == "mac" ]]; then
      launchctl unload -w "$LAUNCHD_PLIST" 2>/dev/null || true
      rm -f "$LAUNCHD_PLIST"
      echo "✅ launchd agent removed."

    else
      echo "❌ uninstall-service is not supported on platform: $PLATFORM"
      exit 1
    fi
    ;;

  *)
    echo "Usage: $0 [start|stop|restart|status|install-deps|install-service|uninstall-service|start-poller|stop-poller|status-poller]"
    exit 1
    ;;
esac

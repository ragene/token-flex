# token-flow service manager — Windows (PowerShell)
# Usage: .\manage.ps1 [start|stop|restart|status|install-deps|start-poller|stop-poller|status-poller]
param(
    [Parameter(Position=0)]
    [string]$Command = "status"
)

$ErrorActionPreference = "Stop"

# ── Config ────────────────────────────────────────────────────────────────────
$PORT        = if ($env:TOKEN_FLOW_PORT) { $env:TOKEN_FLOW_PORT } else { "8001" }
$SCRIPT_DIR  = Split-Path -Parent $MyInvocation.MyCommand.Path
$SERVER_SCRIPT = Join-Path $SCRIPT_DIR "main.py"

$TMP_DIR        = $env:TEMP
$PID_FILE       = Join-Path $TMP_DIR "token-flow.pid"
$LOG_FILE       = Join-Path $TMP_DIR "token-flow.log"
$POLLER_PID     = Join-Path $TMP_DIR "token-flow-poller.pid"
$POLLER_LOG     = Join-Path $TMP_DIR "token-flow-poller.log"

$HOME_DIR       = $env:USERPROFILE
$DEFAULT_DB        = Join-Path $HOME_DIR ".openclaw\data\token_flow.db"
$DEFAULT_WORKSPACE = Join-Path $HOME_DIR ".openclaw\workspace"
$DEFAULT_MEMORY    = Join-Path $HOME_DIR ".openclaw\workspace\memory"
$DEFAULT_SESSIONS  = Join-Path $HOME_DIR ".openclaw\agents\main\sessions"
$DEFAULT_AUTH_JSON = Join-Path $HOME_DIR ".openclaw\agents\main\agent\auth-profiles.json"
$DEFAULT_TF_AUTH   = Join-Path $HOME_DIR ".openclaw\tf_auth.json"

# ── Helpers ───────────────────────────────────────────────────────────────────
function Is-Running {
    if (-not (Test-Path $PID_FILE)) { return $false }
    $pid = Get-Content $PID_FILE -ErrorAction SilentlyContinue
    if (-not $pid) { return $false }
    return $null -ne (Get-Process -Id $pid -ErrorAction SilentlyContinue)
}

function Pid-On-Port {
    try {
        $conn = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($conn) { return $conn.OwningProcess }
    } catch {}
    return $null
}

function Load-DotEnv {
    $envFile = Join-Path $SCRIPT_DIR ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
                $k = $Matches[1].Trim()
                $v = $Matches[2].Trim().Trim('"').Trim("'")
                if (-not [System.Environment]::GetEnvironmentVariable($k)) {
                    [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
                }
            }
        }
    }
}

function Resolve-ApiKey {
    if ($env:ANTHROPIC_API_KEY) {
        Write-Host "   API key: found in environment"
        return
    }
    $key = python3 -c @"
import json, pathlib
for p in ['$($DEFAULT_AUTH_JSON.Replace('\','\\'))']:
    try:
        data = json.loads(pathlib.Path(p).read_text())
        for name, prof in data.get('profiles', {}).items():
            if 'anthropic' in name.lower():
                k = prof.get('key', '')
                if k.startswith('sk-ant'):
                    print(k); raise SystemExit(0)
    except SystemExit: raise
    except: pass
"@ 2>$null
    if ($key) {
        $env:ANTHROPIC_API_KEY = $key.Trim()
        Write-Host "   API key: resolved from OpenClaw auth-profiles"
    } else {
        Write-Host "   WARNING: ANTHROPIC_API_KEY not found — Claude summarization will use fallback mode"
        Write-Host "   Set it with: `$env:ANTHROPIC_API_KEY='sk-ant-...' before running start"
    }
}

function Resolve-TfJwt {
    $jwt = python3 -c @"
import json, pathlib, time
p = pathlib.Path('$($DEFAULT_TF_AUTH.Replace('\','\\'))')
try:
    d = json.loads(p.read_text())
    if time.time() < d.get('expires_at', 0) - 60:
        print(d['token'])
except Exception:
    pass
"@ 2>$null
    return $jwt
}

# ── Commands ──────────────────────────────────────────────────────────────────
switch ($Command) {

    "install-deps" {
        pip3 install -q "fastapi>=0.111" "uvicorn[standard]>=0.29" "anthropic>=0.25" "boto3>=1.34" "tiktoken>=0.7" "python-dotenv>=1.0"
        Write-Host "✅ Dependencies installed."
    }

    "start" {
        if (Is-Running) {
            Write-Host "⚠️  token-flow already running (PID $(Get-Content $PID_FILE))"
            exit 0
        }

        # Kill stale process on port
        $stalePid = Pid-On-Port
        if ($stalePid) {
            Write-Host "⚠️  Port $PORT already in use by PID $stalePid — killing it..."
            Stop-Process -Id $stalePid -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
            Write-Host "   Cleared."
        }

        Resolve-ApiKey
        Load-DotEnv

        $db       = if ($env:TOKEN_FLOW_DB)   { $env:TOKEN_FLOW_DB }   else { $DEFAULT_DB }
        $ws       = if ($env:WORKSPACE)        { $env:WORKSPACE }       else { $DEFAULT_WORKSPACE }
        $mem      = if ($env:MEMORY_DIR)       { $env:MEMORY_DIR }      else { $DEFAULT_MEMORY }
        $sessions = if ($env:SESSIONS_DIR)     { $env:SESSIONS_DIR }    else { $DEFAULT_SESSIONS }
        $s3bucket = if ($env:S3_BUCKET)        { $env:S3_BUCKET }       else { "smart-memory" }

        $procEnv = @{
            ANTHROPIC_API_KEY  = $env:ANTHROPIC_API_KEY
            TOKEN_FLOW_PORT    = $PORT
            TOKEN_FLOW_DB      = $db
            WORKSPACE          = $ws
            MEMORY_DIR         = $mem
            SESSIONS_DIR       = $sessions
            S3_BUCKET          = $s3bucket
            AUTH0_DOMAIN       = "$($env:AUTH0_DOMAIN)"
            AUTH0_CLIENT_ID    = "$($env:AUTH0_CLIENT_ID)"
            SECRET_KEY         = "$($env:SECRET_KEY)"
            TOKEN_FLOW_UI_URL  = "$($env:TOKEN_FLOW_UI_URL)"
            PYTHONUNBUFFERED   = "1"
        }

        # Set env vars for child process
        foreach ($kv in $procEnv.GetEnumerator()) {
            [System.Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, "Process")
        }

        $proc = Start-Process python3 -ArgumentList "-u `"$SERVER_SCRIPT`"" `
            -RedirectStandardOutput $LOG_FILE -RedirectStandardError $LOG_FILE `
            -NoNewWindow -PassThru
        $proc.Id | Set-Content $PID_FILE

        Write-Host "   Waiting for service to become healthy..."
        $deadline = (Get-Date).AddSeconds(120)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
                Write-Host "❌ Process exited before becoming healthy. Check $LOG_FILE"
                exit 1
            }
            try {
                $r = Invoke-WebRequest "http://localhost:$PORT/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                if ($r.StatusCode -eq 200) { $ready = $true; break }
            } catch {}
            Start-Sleep -Seconds 2
        }

        if ($ready) {
            Write-Host "✅ token-flow started (PID $($proc.Id)) on port $PORT"
            Write-Host "   Logs: $LOG_FILE"
        } else {
            Write-Host "❌ Service did not become healthy within 120s. Check $LOG_FILE"
            exit 1
        }
    }

    "stop" {
        if (Is-Running) {
            $pid = Get-Content $PID_FILE
            Stop-Process -Id $pid -Force
            Remove-Item $PID_FILE -ErrorAction SilentlyContinue
            Write-Host "✅ token-flow stopped."
        } else {
            Write-Host "ℹ️  token-flow not running."
        }
    }

    "restart" {
        & $MyInvocation.MyCommand.Path stop
        Start-Sleep -Seconds 1
        & $MyInvocation.MyCommand.Path start
    }

    "status" {
        if (Is-Running) {
            Write-Host "✅ token-flow running (PID $(Get-Content $PID_FILE)) on http://localhost:$PORT"
            try {
                $r = Invoke-WebRequest "http://localhost:$PORT/health" -UseBasicParsing -ErrorAction Stop
                $r.Content | python3 -m json.tool 2>$null
            } catch {}
        } else {
            Write-Host "❌ token-flow not running."
        }
    }

    "start-poller" {
        if ((Test-Path $POLLER_PID) -and (Get-Process -Id (Get-Content $POLLER_PID) -ErrorAction SilentlyContinue)) {
            Write-Host "⚠️  SQS poller already running (PID $(Get-Content $POLLER_PID))"
            exit 0
        }

        Resolve-ApiKey
        Load-DotEnv

        $ws       = if ($env:WORKSPACE)    { $env:WORKSPACE }    else { $DEFAULT_WORKSPACE }
        $mem      = if ($env:MEMORY_DIR)   { $env:MEMORY_DIR }   else { $DEFAULT_MEMORY }
        $queueUrl = if ($env:MEMORY_DISTILL_QUEUE_URL) { $env:MEMORY_DISTILL_QUEUE_URL } else { "https://sqs.us-west-2.amazonaws.com/531948420901/freightdawg-memory-distill" }
        $apiUrl   = if ($env:TOKEN_FLOW_API_URL) { $env:TOKEN_FLOW_API_URL } else { "http://localhost:$PORT" }
        $db       = if ($env:TOKEN_FLOW_DB) { $env:TOKEN_FLOW_DB } else { $DEFAULT_DB }
        $dbUrl    = if ($env:DATABASE_URL)  { $env:DATABASE_URL }  else { "sqlite:///$db" }
        $tfUiUrl  = if ($env:TOKEN_FLOW_UI_URL) { $env:TOKEN_FLOW_UI_URL } else { "" }

        $jwt = Resolve-TfJwt
        if ($jwt) {
            Write-Host "   Auth  : using cached token from $DEFAULT_TF_AUTH"
        } else {
            Write-Host "   WARNING: No valid cached token — run the token-flow service first to authenticate"
        }

        $env:ANTHROPIC_API_KEY         = $env:ANTHROPIC_API_KEY
        $env:WORKSPACE                 = $ws
        $env:MEMORY_DIR                = $mem
        $env:MEMORY_DISTILL_QUEUE_URL  = $queueUrl
        $env:TOKEN_FLOW_API_URL        = $apiUrl
        $env:DATABASE_URL              = $dbUrl
        $env:TOKEN_FLOW_UI_URL         = $tfUiUrl
        $env:TOKEN_FLOW_JWT            = $jwt
        $env:PYTHONUNBUFFERED          = "1"

        $pollerScript = Join-Path $SCRIPT_DIR "memory_distill.py"
        $args = "-u `"$pollerScript`" poll-sqs --output `"$mem\distilled.md`" --context-hint `"FreightDawg SoCal freight dispatch app on AWS ECS`""
        $proc = Start-Process python3 -ArgumentList $args `
            -RedirectStandardOutput $POLLER_LOG -RedirectStandardError $POLLER_LOG `
            -NoNewWindow -PassThru
        $proc.Id | Set-Content $POLLER_PID
        Start-Sleep -Seconds 1

        if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
            Write-Host "✅ SQS poller started (PID $($proc.Id))"
            Write-Host "   Queue : $queueUrl"
            Write-Host "   Logs  : $POLLER_LOG"
        } else {
            Write-Host "❌ SQS poller failed to start. Check $POLLER_LOG"
            exit 1
        }
    }

    "stop-poller" {
        if ((Test-Path $POLLER_PID) -and (Get-Process -Id (Get-Content $POLLER_PID) -ErrorAction SilentlyContinue)) {
            Stop-Process -Id (Get-Content $POLLER_PID) -Force
            Remove-Item $POLLER_PID -ErrorAction SilentlyContinue
            Write-Host "✅ SQS poller stopped."
        } else {
            Write-Host "ℹ️  SQS poller not running."
        }
    }

    "status-poller" {
        if ((Test-Path $POLLER_PID) -and (Get-Process -Id (Get-Content $POLLER_PID) -ErrorAction SilentlyContinue)) {
            Write-Host "✅ SQS poller running (PID $(Get-Content $POLLER_PID))"
        } else {
            Write-Host "❌ SQS poller not running."
        }
    }

    default {
        Write-Host "Usage: .\manage.ps1 [start|stop|restart|status|install-deps|start-poller|stop-poller|status-poller]"
        exit 1
    }
}

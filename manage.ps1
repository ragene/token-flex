# token-flow service manager — Windows (PowerShell)
# Usage: .\manage.ps1 [start|stop|restart|status|install-deps|install-service|uninstall-service|start-poller|stop-poller|status-poller]
#
# Windows Service support (recommended):
#   Install NSSM first: winget install nssm  (or choco install nssm)
#   Then: .\manage.ps1 install-service
#   After that, start/stop/restart/status all delegate to the Windows service.
#
# The Windows service runs detached from any terminal/TUI and auto-restarts on failure.
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

# ── Windows Service config ────────────────────────────────────────────────────
$WIN_SERVICE_NAME = "TokenFlow"
$WIN_SERVICE_DISPLAY = "Token Flow API"
$WIN_SERVICE_DESC = "Token Flow local API server for FreightDawg smart memory and token tracking"

# ── Windows Service delegation ───────────────────────────────────────────────
# When the Windows service is installed, start/stop/restart/status delegate to
# sc.exe so the process is fully detached from any terminal/TUI session and
# auto-restarts on failure (configured via NSSM at install time).
function WinService-Installed {
    return $null -ne (Get-Service -Name $WIN_SERVICE_NAME -ErrorAction SilentlyContinue)
}

if (WinService-Installed -and $Command -in @("start","stop","restart","status")) {
    switch ($Command) {
        "start" {
            Start-Service -Name $WIN_SERVICE_NAME
            Start-Sleep -Seconds 2
            $svc = Get-Service -Name $WIN_SERVICE_NAME
            Write-Host "✅ token-flow service: $($svc.Status)"
            try { (Invoke-WebRequest "http://localhost:$PORT/health" -UseBasicParsing -TimeoutSec 5).Content | python -m json.tool 2>$null } catch {}
        }
        "stop" {
            Stop-Service -Name $WIN_SERVICE_NAME -Force
            Write-Host "✅ token-flow stopped."
        }
        "restart" {
            Restart-Service -Name $WIN_SERVICE_NAME -Force
            Start-Sleep -Seconds 2
            $svc = Get-Service -Name $WIN_SERVICE_NAME
            Write-Host "✅ token-flow service: $($svc.Status)"
            try { (Invoke-WebRequest "http://localhost:$PORT/health" -UseBasicParsing -TimeoutSec 5).Content | python -m json.tool 2>$null } catch {}
        }
        "status" {
            $svc = Get-Service -Name $WIN_SERVICE_NAME
            if ($svc.Status -eq "Running") {
                Write-Host "✅ token-flow running (Windows service: $WIN_SERVICE_NAME) on http://localhost:$PORT"
                try { (Invoke-WebRequest "http://localhost:$PORT/health" -UseBasicParsing -TimeoutSec 5).Content | python -m json.tool 2>$null } catch {}
            } else {
                Write-Host "❌ token-flow service status: $($svc.Status)"
            }
        }
    }
    exit 0
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Helpers ───────────────────────────────────────────────────────────────────
function Is-Running {
    if (-not (Test-Path $PID_FILE)) { return $false }
    $storedPid = Get-Content $PID_FILE -ErrorAction SilentlyContinue
    if (-not $storedPid) { return $false }
    return $null -ne (Get-Process -Id $storedPid -ErrorAction SilentlyContinue)
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
    $authJson = $DEFAULT_AUTH_JSON.Replace('\','/')
    $pyScript = @'
import json, pathlib, sys
for p in [sys.argv[1]]:
    try:
        data = json.loads(pathlib.Path(p).read_text())
        for name, prof in data.get("profiles", {}).items():
            if "anthropic" in name.lower():
                k = prof.get("key", "")
                if k.startswith("sk-ant"):
                    print(k); sys.exit(0)
    except SystemExit: raise
    except: pass
'@
    $key = python -c $pyScript $authJson 2>$null
    if ($key) {
        $env:ANTHROPIC_API_KEY = $key.Trim()
        Write-Host "   API key: resolved from OpenClaw auth-profiles"
    } else {
        Write-Host "   WARNING: ANTHROPIC_API_KEY not found -- Claude summarization will use fallback mode"
        Write-Host "   Set it with: `$env:ANTHROPIC_API_KEY='sk-ant-...' before running start"
    }
}

function Resolve-TfJwt {
    $authPath = $DEFAULT_TF_AUTH.Replace('\','/')
    $pyCode = 'import json, pathlib, time; p = pathlib.Path(r"' + $authPath + '"); d = json.loads(p.read_text()); print(d["token"]) if time.time() < d.get("expires_at", 0) - 60 else None'
    $jwt = python -c $pyCode 2>$null
    return $jwt
}

function Resolve-OwnerEmail {
    # Allow explicit override via env var (e.g. set in ECS task def or CI)
    if ($env:OWNER_EMAIL) { return $env:OWNER_EMAIL }
    $authPath = $DEFAULT_TF_AUTH.Replace('\','/')
    $pyCode = 'import json, pathlib, time; p = pathlib.Path(r"' + $authPath + '"); d = json.loads(p.read_text()); e = (d.get(\"user\") or {}).get(\"email\",\"\").strip(); print(e) if e and time.time() < d.get(\"expires_at\",0)-60 else None'
    $email = python -c $pyCode 2>$null
    return $email
}

# ── Commands ──────────────────────────────────────────────────────────────────
switch ($Command) {

    "install-deps" {
        pip install -q "fastapi>=0.111" "uvicorn[standard]>=0.29" "anthropic>=0.25" "boto3>=1.34" "tiktoken>=0.7" "python-dotenv>=1.0"
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

        $db         = if ($env:TOKEN_FLOW_DB)   { $env:TOKEN_FLOW_DB }   else { $DEFAULT_DB }
        $ws         = if ($env:WORKSPACE)        { $env:WORKSPACE }       else { $DEFAULT_WORKSPACE }
        $mem        = if ($env:MEMORY_DIR)       { $env:MEMORY_DIR }      else { $DEFAULT_MEMORY }
        $sessions   = if ($env:SESSIONS_DIR)     { $env:SESSIONS_DIR }    else { $DEFAULT_SESSIONS }
        $s3bucket   = if ($env:S3_BUCKET)        { $env:S3_BUCKET }       else { "smart-memory" }
        $ownerEmail = Resolve-OwnerEmail
        $tfJwt      = Resolve-TfJwt

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
            TOKEN_FLOW_JWT     = "$tfJwt"
            OWNER_EMAIL        = "$ownerEmail"
            PYTHONUNBUFFERED   = "1"
            PYTHONIOENCODING   = "utf-8"
        }

        # Set env vars for child process
        foreach ($kv in $procEnv.GetEnumerator()) {
            [System.Environment]::SetEnvironmentVariable($kv.Key, $kv.Value, "Process")
        }

        # Find python executable path
        $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $pythonExe) {
            Write-Host "ERROR: python not found in PATH"
            exit 1
        }

        Write-Host "   Python : $pythonExe"
        Write-Host "   Script : $SERVER_SCRIPT"
        Write-Host "   Logs   : $LOG_FILE"

        # Build a .bat launcher that sets env and runs python with combined output
        $launcherBat = Join-Path $TMP_DIR "token-flow-launcher.bat"
        $batLines = @("@echo off", "cd /d `"$SCRIPT_DIR`"")
        foreach ($kv in $procEnv.GetEnumerator()) {
            $batLines += "set `"$($kv.Key)=$($kv.Value)`""
        }
        $batLines += "`"$pythonExe`" -u `"$SERVER_SCRIPT`" > `"$LOG_FILE`" 2>&1"
        $batLines -join "`r`n" | Set-Content $launcherBat -Encoding ASCII

        $proc = Start-Process cmd.exe -ArgumentList "/c `"$launcherBat`"" `
            -WindowStyle Hidden -PassThru

        if (-not $proc -or $proc.Id -eq 0) {
            Write-Host "ERROR: Failed to start process"
            exit 1
        }

        $proc.Id | Set-Content $PID_FILE
        Write-Host "   PID    : $($proc.Id)"
        Write-Host "   Waiting for service to become healthy..."

        $deadline = (Get-Date).AddSeconds(120)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            if ($proc.HasExited) {
                Write-Host "ERROR: Process exited before becoming healthy."
                Write-Host "--- log tail ---"
                Start-Sleep -Seconds 1
                Get-Content $LOG_FILE -ErrorAction SilentlyContinue | Select-Object -Last 25
                exit 1
            }
            try {
                $r = Invoke-WebRequest "http://localhost:$PORT/health" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
                if ($r.StatusCode -eq 200) { $ready = $true; break }
            } catch {}
            Start-Sleep -Seconds 2
        }

        if ($ready) {
            Write-Host "token-flow started (PID $($proc.Id)) on http://localhost:$PORT"
            Write-Host "   View logs: Get-Content '$LOG_FILE' -Tail 30 -Wait"
        } else {
            Write-Host "ERROR: Service did not become healthy within 120s."
            Write-Host "--- log tail ---"
            Get-Content $LOG_FILE -ErrorAction SilentlyContinue | Select-Object -Last 25
            exit 1
        }
    }

    "stop" {
        if (Is-Running) {
            $storedPid = Get-Content $PID_FILE
            Stop-Process -Id $storedPid -Force
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
                $r.Content | python -m json.tool 2>$null
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

        $ownerEmail = Resolve-OwnerEmail

        $env:ANTHROPIC_API_KEY         = $env:ANTHROPIC_API_KEY
        $env:WORKSPACE                 = $ws
        $env:MEMORY_DIR                = $mem
        $env:MEMORY_DISTILL_QUEUE_URL  = $queueUrl
        $env:TOKEN_FLOW_API_URL        = $apiUrl
        $env:DATABASE_URL              = $dbUrl
        $env:TOKEN_FLOW_UI_URL         = $tfUiUrl
        $env:TOKEN_FLOW_JWT            = $jwt
        $env:OWNER_EMAIL               = $ownerEmail
        $env:PYTHONUNBUFFERED          = "1"

        $pollerScript = Join-Path $SCRIPT_DIR "memory_distill.py"
        $args = "-u `"$pollerScript`" poll-sqs --output `"$mem\distilled.md`" --context-hint `"FreightDawg SoCal freight dispatch app on AWS ECS`""
        $POLLER_ERR_LOG = $POLLER_LOG -replace '\.log$', '-err.log'
        $proc = Start-Process python -ArgumentList $args `
            -RedirectStandardOutput $POLLER_LOG -RedirectStandardError $POLLER_ERR_LOG `
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

    "install-service" {
        # Requires NSSM: winget install nssm  OR  choco install nssm
        $nssm = Get-Command nssm -ErrorAction SilentlyContinue
        if (-not $nssm) {
            Write-Host "❌ NSSM not found. Install it first:"
            Write-Host "   winget install nssm"
            Write-Host "   — or —"
            Write-Host "   choco install nssm"
            exit 1
        }

        if (WinService-Installed) {
            Write-Host "⚠️  Service '$WIN_SERVICE_NAME' already exists. Run uninstall-service first to reinstall."
            exit 0
        }

        $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $pythonExe) {
            Write-Host "❌ python not found in PATH"
            exit 1
        }

        Load-DotEnv
        Resolve-ApiKey

        $db         = if ($env:TOKEN_FLOW_DB)   { $env:TOKEN_FLOW_DB }   else { $DEFAULT_DB }
        $ws         = if ($env:WORKSPACE)        { $env:WORKSPACE }       else { $DEFAULT_WORKSPACE }
        $mem        = if ($env:MEMORY_DIR)       { $env:MEMORY_DIR }      else { $DEFAULT_MEMORY }
        $sessions   = if ($env:SESSIONS_DIR)     { $env:SESSIONS_DIR }    else { $DEFAULT_SESSIONS }
        $s3bucket   = if ($env:S3_BUCKET)        { $env:S3_BUCKET }       else { "smart-memory" }
        $ownerEmail = Resolve-OwnerEmail

        $logDir = Join-Path $HOME_DIR ".openclaw\logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $logOut = Join-Path $logDir "token-flow.log"
        $logErr = Join-Path $logDir "token-flow-err.log"

        Write-Host "Installing Windows service '$WIN_SERVICE_NAME' via NSSM..."

        & nssm install $WIN_SERVICE_NAME $pythonExe "-u `"$SERVER_SCRIPT`""
        & nssm set $WIN_SERVICE_NAME AppDirectory $SCRIPT_DIR
        & nssm set $WIN_SERVICE_NAME DisplayName $WIN_SERVICE_DISPLAY
        & nssm set $WIN_SERVICE_NAME Description $WIN_SERVICE_DESC
        & nssm set $WIN_SERVICE_NAME Start SERVICE_AUTO_START
        & nssm set $WIN_SERVICE_NAME AppStdout $logOut
        & nssm set $WIN_SERVICE_NAME AppStderr $logErr
        & nssm set $WIN_SERVICE_NAME AppRotateFiles 1
        & nssm set $WIN_SERVICE_NAME AppRotateBytes 10485760  # 10 MB

        # Set environment variables
        $envBlock = @(
            "TOKEN_FLOW_PORT=$PORT",
            "TOKEN_FLOW_DB=$db",
            "WORKSPACE=$ws",
            "MEMORY_DIR=$mem",
            "SESSIONS_DIR=$sessions",
            "S3_BUCKET=$s3bucket",
            "AUTH0_DOMAIN=$($env:AUTH0_DOMAIN)",
            "AUTH0_CLIENT_ID=$($env:AUTH0_CLIENT_ID)",
            "SECRET_KEY=$($env:SECRET_KEY)",
            "TOKEN_FLOW_UI_URL=$($env:TOKEN_FLOW_UI_URL)",
            "ANTHROPIC_API_KEY=$($env:ANTHROPIC_API_KEY)",
            "OWNER_EMAIL=$ownerEmail",
            "SKIP_STARTUP_AUTH=false",
            "PYTHONUNBUFFERED=1",
            "PYTHONIOENCODING=utf-8"
        )
        & nssm set $WIN_SERVICE_NAME AppEnvironmentExtra ($envBlock -join "`n")

        # Restart on failure: 3 attempts with 5s delay
        & nssm set $WIN_SERVICE_NAME AppExit Default Restart
        & nssm set $WIN_SERVICE_NAME AppRestartDelay 5000

        Start-Service -Name $WIN_SERVICE_NAME
        Start-Sleep -Seconds 3
        $svc = Get-Service -Name $WIN_SERVICE_NAME
        Write-Host "✅ Service installed and started: $($svc.Status)"
        Write-Host "   Name    : $WIN_SERVICE_NAME"
        Write-Host "   Logs    : $logOut"
        Write-Host "   Manage  : .\manage.ps1 start|stop|restart|status"
    }

    "uninstall-service" {
        $nssm = Get-Command nssm -ErrorAction SilentlyContinue
        if (-not $nssm) {
            Write-Host "❌ NSSM not found in PATH"
            exit 1
        }
        if (-not (WinService-Installed)) {
            Write-Host "ℹ️  Service '$WIN_SERVICE_NAME' is not installed."
            exit 0
        }
        & nssm stop $WIN_SERVICE_NAME 2>$null
        & nssm remove $WIN_SERVICE_NAME confirm
        Write-Host "✅ Windows service '$WIN_SERVICE_NAME' removed."
    }

    default {
        Write-Host "Usage: .\manage.ps1 [start|stop|restart|status|install-deps|install-service|uninstall-service|start-poller|stop-poller|status-poller]"
        exit 1
    }
}

"""
tf-server CLI entry point.

Commands
--------
  tf-server start            Start the token-flow API service (default)
  tf-server start --port N   Override port (default: 8001)
  tf-server stop             Stop a running service (by PID file)
  tf-server restart          stop + start
  tf-server status           Print running status + health check
  tf-server distill          Run one full ingest→distill→clear→rebuild cycle and exit
  tf-server poller           Start the SQS memory-distill poller (background worker)

Auth0 device flow runs on every start/restart. A cached token is used if
still valid; otherwise a fresh device flow is initiated.

Environment variables honoured
-------------------------------
  TOKEN_FLOW_PORT      Override listen port (default 8001)
  DATABASE_URL         PostgreSQL URL; defaults to SQLite ~/.openclaw/data/token_flow.db
  TOKEN_FLOW_DB        SQLite path when DATABASE_URL is not set
  WORKSPACE            Workspace root (default ~/.openclaw/workspace)
  MEMORY_DIR           Memory directory (default WORKSPACE/memory)
  SESSIONS_DIR         OpenClaw sessions directory
  S3_BUCKET            S3 bucket for summary export
  TOKEN_FLOW_UI_URL    Remote UI URL — enables the 30-second push loop
  TOKEN_FLOW_JWT       Pre-minted JWT to seed the auth cache (ECS/headless deployments)
  TOKEN_FLOW_REPO      Path to the token-flow repo (for .env discovery)
  TOKEN_FLOW_ENV_FILE  Explicit path to a .env file to load
  AUTH0_DOMAIN         Auth0 domain (default tokenflow.us.auth0.com)
  AUTH0_CLIENT_ID      Auth0 client ID
  SECRET_KEY           JWT signing secret
  ANTHROPIC_API_KEY    Anthropic API key for summarization
  OWNER_EMAIL          Email to tag push snapshots with
"""
from __future__ import annotations

import argparse
import io
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

# Fix Windows console encoding for emoji/unicode output
if sys.platform == "win32" and not os.environ.get("PYTHONIOENCODING"):
    os.environ["PYTHONIOENCODING"] = "utf-8"
    # Re-wrap stdout/stderr if they exist and use a lossy codec
    for _stream_name in ("stdout", "stderr"):
        _s = getattr(sys, _stream_name, None)
        if _s and hasattr(_s, "buffer") and hasattr(_s, "encoding") and _s.encoding and _s.encoding.lower().replace("-", "") != "utf8":
            try:
                setattr(sys, _stream_name, io.TextIOWrapper(_s.detach(), encoding="utf-8", errors="replace", line_buffering=True))
            except Exception:
                pass

# Platform-appropriate temp paths
if sys.platform == "win32":
    _tmp = Path(os.environ.get("TEMP", os.environ.get("TMP", "C:\\Temp")))
else:
    _tmp = Path("/tmp")
PID_FILE = _tmp / "token-flow.pid"
LOG_FILE = _tmp / "token-flow.log"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        if sys.platform == "win32":
            # os.kill(pid, 0) on Windows kills the process; use tasklist instead
            import subprocess as _sp
            result = _sp.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ValueError):
        return False


def _wait_healthy(port: int, timeout: int = 120) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
            return True
        except Exception:
            time.sleep(2)
    return False


def _resolve_db_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    db_path = Path(os.environ.get(
        "TOKEN_FLOW_DB",
        Path.home() / ".openclaw" / "data" / "token_flow.db",
    ))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"


def _resolve_paths():
    workspace   = Path(os.environ.get("WORKSPACE",   Path.home() / ".openclaw" / "workspace"))
    memory_dir  = Path(os.environ.get("MEMORY_DIR",  workspace / "memory"))
    memory_dir.mkdir(parents=True, exist_ok=True)
    return workspace, memory_dir


# ── SSO helpers (mirrors main.py logic) ──────────────────────────────────────

def _run_sso(port: int) -> tuple[list, list]:
    """
    Phase 1 SSO: device flow (pre-server).
    Returns (auth0_token_holder, sso_user_holder) — mutable 1-element lists
    so the background thread in phase 2 can read the results.
    """
    auth0_token: list = [""]
    sso_user:    list = [{}]

    try:
        from api.device_auth import _load_cache, get_cached_user, _device_flow

        cached = _load_cache()
        if cached:
            print("🔐 SSO: using cached token")
            sso_user[0] = get_cached_user()
            # Cache may have been written without user info (e.g. seeded via
            # TOKEN_FLOW_JWT). Try to decode email directly from the JWT payload.
            if not sso_user[0].get("email"):
                try:
                    import base64 as _b64
                    parts = cached.split(".")
                    if len(parts) >= 2:
                        padded = parts[1] + "=" * (-len(parts[1]) % 4)
                        claim = json.loads(_b64.urlsafe_b64decode(padded))
                        if claim.get("email"):
                            sso_user[0] = {"email": claim["email"], "name": claim.get("name", "")}
                except Exception:
                    pass
            print(f"✅ Authenticated as {sso_user[0].get('email', 'unknown')}")
        else:
            print("🔐 Authenticating with Auth0 SSO...")
            auth0_token[0] = _device_flow()
            print("✅ Auth0 login complete — will exchange token after server starts")
    except Exception as e:
        print(f"⚠️  SSO auth failed (continuing): {e}")

    return auth0_token, sso_user


def _launch_session_thread(port: int, auth0_token: list, sso_user: list):
    """Phase 2: exchange token + register identity (runs after server is up)."""
    def _work():
        import urllib.error, urllib.request as _req

        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                _req.urlopen(f"http://localhost:{port}/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        else:
            print("⚠️  Server not ready — skipping session registration")
            return

        # Exchange Auth0 token for internal JWT if we did a fresh device flow
        if auth0_token[0] and not sso_user[0]:
            try:
                from api.device_auth import _exchange, AUTH0_DOMAIN
                internal_token, expires_in = _exchange(auth0_token[0])
                from api.device_auth import _save_cache
                _save_cache(internal_token, expires_in=expires_in)
                print(f"✅ Authenticated as {sso_user[0].get('email', 'unknown')}")

                # Fetch user info from Auth0
                try:
                    req = _req.Request(
                        f"https://{AUTH0_DOMAIN}/userinfo",
                        headers={"Authorization": f"Bearer {auth0_token[0]}"},
                    )
                    with _req.urlopen(req, timeout=5) as r:
                        sso_user[0] = json.loads(r.read())
                except Exception:
                    pass
            except Exception as e:
                print(f"⚠️  Token exchange failed: {e}")
                return

        if not sso_user[0].get("email"):
            return

        try:
            payload = json.dumps({
                "email":     sso_user[0].get("email"),
                "name":      sso_user[0].get("name"),
                "picture":   sso_user[0].get("picture"),
                "auth0_sub": sso_user[0].get("sub"),
                "host":      socket.gethostname(),
            }).encode()
            req = _req.Request(
                f"http://localhost:{port}/session/identify",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=5) as r:
                print(f"✅ Local session registered: {r.read().decode()}")
        except Exception as e:
            print(f"⚠️  Could not register local session: {e}")

    threading.Thread(target=_work, daemon=True).start()


def _launch_push_loop(port: int, db_url: str):
    """
    Spawn api.remote_push as a subprocess — fully decoupled from the uvicorn
    GIL and SQLite connections in the main process.
    """
    remote_ui = os.environ.get("TOKEN_FLOW_UI_URL", "").strip().rstrip("/")
    if not remote_ui or "localhost" in remote_ui or "127.0.0.1" in remote_ui:
        print("ℹ️  Remote push loop skipped (TOKEN_FLOW_UI_URL not set or is localhost)")
        return

    import subprocess as _sp
    from pathlib import Path as _Path

    remote_push_module = str(_Path(__file__).parent.parent / "api" / "remote_push.py")

    def _run():
        try:
            proc = _sp.Popen(
                [sys.executable, remote_push_module,
                 str(port), remote_ui, "10"],
                stdout=None, stderr=None,  # inherited by journald via systemd
            )
            ret = proc.wait()
            print(f"⚠️  Push worker exited with code {ret}", flush=True)
        except Exception as e:
            print(f"⚠️  Push worker failed to start: {e}", flush=True)

    threading.Thread(target=_run, daemon=True).start()



def cmd_start(args: argparse.Namespace) -> int:
    if _is_running():
        print(f"⚠️  token-flow already running (PID {PID_FILE.read_text().strip()})")
        return 0

    port     = int(os.environ.get("TOKEN_FLOW_PORT", args.port))
    db_url   = _resolve_db_url()
    workspace, memory_dir = _resolve_paths()

    print(f"🔧 token-flow service starting on http://localhost:{port}")
    print(f"   Workspace : {workspace}")
    print(f"   Memory dir: {memory_dir}")
    print(f"   DB        : {'SQLite (local)' if db_url.startswith('sqlite') else 'PostgreSQL'}")

    # Phase 1: SSO (blocking, before server starts)
    auth0_token, sso_user = _run_sso(port)

    # Init DB schema
    from db.schema import init_db
    from db.pg_compat import connect as pg_connect
    if db_url.startswith("sqlite"):
        import sqlite3
        conn = sqlite3.connect(db_url.replace("sqlite:///", ""))
    else:
        conn = pg_connect(db_url)
    init_db(conn)
    conn.close()

    # Phase 2: post-startup thread (exchange token + register session)
    _launch_session_thread(port, auth0_token, sso_user)
    _launch_push_loop(port, db_url)

    # Write PID file for stop/status
    PID_FILE.write_text(str(os.getpid()))

    # Start uvicorn (blocking)
    import uvicorn
    from api.app import create_app
    app = create_app(database_url=db_url)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

    PID_FILE.unlink(missing_ok=True)
    return 0


def cmd_install_service(_args: argparse.Namespace) -> int:
    from token_flow._service import install_service
    install_service()
    return 0


def cmd_uninstall_service(_args: argparse.Namespace) -> int:
    from token_flow._service import uninstall_service
    uninstall_service()
    return 0


def cmd_stop(_args: argparse.Namespace) -> int:
    if not _is_running():
        print("ℹ️  token-flow not running.")
        return 0
    pid = int(PID_FILE.read_text().strip())
    if sys.platform == "win32":
        import subprocess as _sp
        _sp.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        os.kill(pid, 15)   # SIGTERM
    PID_FILE.unlink(missing_ok=True)
    print("✅ token-flow stopped.")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    cmd_stop(args)
    time.sleep(1)
    return cmd_start(args)


def cmd_status(args: argparse.Namespace) -> int:
    port = int(os.environ.get("TOKEN_FLOW_PORT", args.port))
    if _is_running():
        pid = PID_FILE.read_text().strip()
        print(f"✅ token-flow running (PID {pid}) on http://localhost:{port}")
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/health", timeout=3) as r:
                data = json.loads(r.read())
                print(f"   Health: {json.dumps(data, indent=2)}")
        except Exception as e:
            print(f"   ⚠️  Health check failed: {e}")
    else:
        print("❌ token-flow not running.")
    return 0


def cmd_distill(args: argparse.Namespace) -> int:
    """Run one full distill cycle inline (no server needed if already running)."""
    port   = int(os.environ.get("TOKEN_FLOW_PORT", args.port))
    db_url = _resolve_db_url()
    _resolve_paths()

    # Prefer hitting the live API if the service is running
    if _is_running() or _wait_healthy(port, timeout=3):
        try:
            from token_flow._client import memory_full, summarize
            print("🔄  Running memory/full via live API…")
            result = memory_full(
                context_hint=args.hint,
                since=args.since,
                top_n=args.top_n,
                dry_run=args.dry_run,
                base_url=f"http://localhost:{port}",
            )
            _print_distill_result(result)

            if not args.dry_run and result.get("total_chunks", 0) > 0:
                print("\n🧠  Running summarize…")
                s = summarize(
                    push_to_s3=args.s3,
                    context_hint=args.hint,
                    base_url=f"http://localhost:{port}",
                )
                print(f"   Summarized: {s.get('summarized', 0)}  Pushed: {s.get('pushed', 0)}")
        except Exception as e:
            print(f"❌  {e}", file=sys.stderr)
            return 1
    else:
        print("❌  token-flow service is not running. Start it first:", file=sys.stderr)
        print("     tf-server start", file=sys.stderr)
        return 1
    return 0


def _print_distill_result(r: dict):
    passed = r.get("safety_gate_passed", False)
    if not passed:
        print("⚠️   Safety gate failed — DB was empty, nothing cleared.")
        return
    print(f"\n✅  Distill complete")
    print(f"   MD files ingested   : {r.get('md_files_ingested', 0)}")
    print(f"   Git commits ingested: {r.get('git_ingested', 0)}")
    print(f"   Sessions ingested   : {r.get('sessions_ingested', 0)}")
    print(f"   Total chunks        : {r.get('total_chunks', 0)}")
    print(f"   MD files cleared    : {r.get('md_files_cleared', 0)}")
    print(f"   Session files clrd  : {r.get('session_files_cleared', 0)}")
    if r.get("rebuilt_to"):
        print(f"   Rebuilt to          : {r['rebuilt_to']}")


def cmd_poller(args: argparse.Namespace) -> int:
    """Run the SQS memory-distill poller inline."""
    import subprocess
    poller_script = Path(__file__).parent.parent / "memory_distill.py"
    if not poller_script.exists():
        # Fall back — find via importlib
        import importlib.util
        spec = importlib.util.find_spec("token_flow")
        if spec and spec.origin:
            poller_script = Path(spec.origin).parent.parent / "memory_distill.py"

    if not poller_script.exists():
        print("❌  memory_distill.py not found.", file=sys.stderr)
        return 1

    workspace, memory_dir = _resolve_paths()
    env = {**os.environ,
           "MEMORY_DIR": str(memory_dir),
           "WORKSPACE":  str(workspace)}
    result = subprocess.run(
        [sys.executable, str(poller_script), "poll-sqs",
         "--output", str(memory_dir / "distilled.md"),
         "--context-hint", args.hint],
        env=env,
    )
    return result.returncode


# ── Thin client (used by cmd_distill when service is live) ────────────────────

def _ensure_client():
    """Lazily import the client module from tfcli if available, else use inline impl."""
    try:
        from token_flow import _client  # noqa: F401
        return True
    except ImportError:
        return False


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    from token_flow import __version__
    p = argparse.ArgumentParser(
        prog="tf-server",
        description="token-flow service manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--version", action="version", version=f"token-flow-service {__version__}")
    p.add_argument("--port", type=int, default=8001, metavar="PORT",
                   help="Listen port (default: 8001; overridden by TOKEN_FLOW_PORT env)")

    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")
    sub.required = False   # bare `tf-server` → start

    # start
    s = sub.add_parser("start", help="Start the token-flow service")
    s.add_argument("--port", type=int, default=8001, metavar="PORT")

    # stop / restart / status
    sub.add_parser("stop", help="Stop the running service")
    re = sub.add_parser("restart", help="Restart the service")
    re.add_argument("--port", type=int, default=8001, metavar="PORT")
    r = sub.add_parser("status", help="Print running status and health")
    r.add_argument("--port", type=int, default=8001, metavar="PORT")

    # distill
    d = sub.add_parser("distill", help="Run one full distill cycle (requires service running)")
    d.add_argument("--dry-run",  action="store_true", dest="dry_run")
    d.add_argument("--since",    default="7 hours ago", metavar="TIMESPEC")
    d.add_argument("--top-n",    type=int, default=20, dest="top_n")
    d.add_argument("--hint",     default="", metavar="TEXT")
    d.add_argument("--s3",       action="store_true", help="Push summaries to S3 after")
    d.add_argument("--port",     type=int, default=8001, metavar="PORT")

    # poller
    po = sub.add_parser("poller", help="Run the SQS memory-distill poller")
    po.add_argument("--hint", default="", metavar="TEXT")

    # service management
    sub.add_parser("install-service",   help="Install and start the OS service (systemd/launchd/Task Scheduler)")
    sub.add_parser("uninstall-service", help="Stop and remove the OS service")

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Load .env — check explicit override, then cwd, then repo root, then package parent
    from dotenv import load_dotenv
    _explicit = os.environ.get("TOKEN_FLOW_ENV_FILE")
    for candidate in [
        Path(_explicit) if _explicit else None,
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",
        # Repo .env: resolved via TOKEN_FLOW_REPO or a well-known default
        Path(os.environ.get("TOKEN_FLOW_REPO", str(Path.home() / ".openclaw" / "workspace" / "token-flow"))) / ".env",
    ]:
        if candidate and candidate.exists():
            load_dotenv(candidate)
            break

    parser = build_parser()
    args   = parser.parse_args()

    if args.cmd is None:
        args.cmd = "start"

    dispatch = {
        "start":             cmd_start,
        "stop":              cmd_stop,
        "restart":           cmd_restart,
        "status":            cmd_status,
        "distill":           cmd_distill,
        "poller":            cmd_poller,
        "install-service":   cmd_install_service,
        "uninstall-service": cmd_uninstall_service,
    }
    handler = dispatch.get(args.cmd)
    if not handler:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args) or 0)


if __name__ == "__main__":
    main()

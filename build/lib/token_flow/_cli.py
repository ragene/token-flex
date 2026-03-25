"""
tf-server CLI entry point.

Commands
--------
  tf-server start            Start the token-flow API service (default)
  tf-server start --port N   Override port (default: 8001)
  tf-server start --no-auth  Skip Auth0 device flow on startup
  tf-server stop             Stop a running service (by PID file)
  tf-server restart          stop + start
  tf-server status           Print running status + health check
  tf-server distill          Run one full ingest→distill→clear→rebuild cycle and exit
  tf-server poller           Start the SQS memory-distill poller (background worker)

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
  SKIP_STARTUP_AUTH    Set to "true" to skip Auth0 device flow (not recommended)
  TOKEN_FLOW_JWT       Pre-minted JWT to use when SKIP_STARTUP_AUTH=true
  AUTH0_DOMAIN         Auth0 domain (default tokenflow.us.auth0.com)
  AUTH0_CLIENT_ID      Auth0 client ID
  SECRET_KEY           JWT signing secret
  ANTHROPIC_API_KEY    Anthropic API key for summarization
  OWNER_EMAIL          Email to tag push snapshots with
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

PID_FILE = Path("/tmp/token-flow.pid")
LOG_FILE = Path("/tmp/token-flow.log")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
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

def _run_sso(port: int, skip: bool) -> tuple[list, list]:
    """
    Phase 1 SSO: device flow (pre-server).
    Returns (auth0_token_holder, sso_user_holder) — mutable 1-element lists
    so the background thread in phase 2 can read the results.
    """
    auth0_token: list = [""]
    sso_user:    list = [{}]

    if skip:
        print("🔐 SSO: skipping startup auth (SKIP_STARTUP_AUTH=true)")
        preset_jwt = os.environ.get("TOKEN_FLOW_JWT", "").strip()
        if preset_jwt:
            try:
                from api.device_auth import _save_cache
                _save_cache(preset_jwt, expires_in=365 * 24 * 3600)
                print("🔐 SSO: TOKEN_FLOW_JWT cached from env")
            except Exception as e:
                print(f"⚠️  Could not cache TOKEN_FLOW_JWT: {e}")
        return auth0_token, sso_user

    try:
        from api.device_auth import _load_cache, get_cached_user, _device_flow

        cached = _load_cache()
        if cached:
            print("🔐 SSO: using cached token")
            sso_user[0] = get_cached_user()
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
    """Periodic push loop to remote TOKEN_FLOW_UI_URL (if configured)."""
    remote_ui = os.environ.get("TOKEN_FLOW_UI_URL", "").strip().rstrip("/")
    if not remote_ui or "localhost" in remote_ui or "127.0.0.1" in remote_ui:
        print("ℹ️  Remote push loop skipped (TOKEN_FLOW_UI_URL not set or is localhost)")
        return

    def _loop():
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(f"http://localhost:{port}/health", timeout=2)
                break
            except Exception:
                time.sleep(2)
        print(f"🚀 Remote push loop started → {remote_ui} (immediate + every 30s)")
        while True:
            try:
                from api.push_client import push_snapshot
                push_snapshot(db_url, ui_url=remote_ui)
            except Exception as e:
                print(f"⚠️  Remote push failed (non-fatal): {e}")
            time.sleep(30)

    threading.Thread(target=_loop, daemon=True).start()


# ── Sub-commands ──────────────────────────────────────────────────────────────

def cmd_start(args: argparse.Namespace) -> int:
    if _is_running():
        print(f"⚠️  token-flow already running (PID {PID_FILE.read_text().strip()})")
        return 0

    port     = int(os.environ.get("TOKEN_FLOW_PORT", args.port))
    db_url   = _resolve_db_url()
    workspace, memory_dir = _resolve_paths()
    skip_auth = args.no_auth or os.environ.get("SKIP_STARTUP_AUTH", "").lower() in ("1", "true", "yes")

    print(f"🔧 token-flow service starting on http://localhost:{port}")
    print(f"   Workspace : {workspace}")
    print(f"   Memory dir: {memory_dir}")
    print(f"   DB        : {'SQLite (local)' if db_url.startswith('sqlite') else 'PostgreSQL'}")

    # Phase 1: SSO (blocking, before server starts)
    auth0_token, sso_user = _run_sso(port, skip=skip_auth)

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


def cmd_stop(_args: argparse.Namespace) -> int:
    if not _is_running():
        print("ℹ️  token-flow not running.")
        return 0
    pid = int(PID_FILE.read_text().strip())
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
    s.add_argument("--no-auth", action="store_true",
                   help="Skip Auth0 device flow (SKIP_STARTUP_AUTH=true)")
    s.add_argument("--port", type=int, default=8001, metavar="PORT")

    # stop / restart / status
    sub.add_parser("stop",    help="Stop the running service")
    sub.add_parser("restart", help="Restart the service")
    r = sub.add_parser("status",  help="Print running status and health")
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

    return p


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Load .env from current dir or token-flow repo root before parsing args
    from dotenv import load_dotenv
    for candidate in [
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",
    ]:
        if candidate.exists():
            load_dotenv(candidate)
            break

    parser = build_parser()
    args   = parser.parse_args()

    if args.cmd is None:
        args.cmd = "start"
        # inject defaults that start requires
        if not hasattr(args, "no_auth"):
            args.no_auth = False

    dispatch = {
        "start":   cmd_start,
        "stop":    cmd_stop,
        "restart": cmd_restart,
        "status":  cmd_status,
        "distill": cmd_distill,
        "poller":  cmd_poller,
    }
    handler = dispatch.get(args.cmd)
    if not handler:
        parser.print_help()
        sys.exit(1)

    sys.exit(handler(args) or 0)


if __name__ == "__main__":
    main()

import uvicorn
import os
from pathlib import Path
from dotenv import load_dotenv
from api.app import create_app
from db.schema import init_db

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL", "")
WORKSPACE    = Path(os.environ.get("WORKSPACE",   "/home/ec2-user/.openclaw/workspace"))
MEMORY_DIR   = Path(os.environ.get("MEMORY_DIR",  "/home/ec2-user/.openclaw/workspace/memory"))
PORT         = int(os.environ.get("PORT", 8001))

# Fall back to SQLite for local dev when DATABASE_URL is not set
if not DATABASE_URL:
    _sqlite_path = Path(os.environ.get("TOKEN_FLOW_DB", "/home/ec2-user/.openclaw/data/token_flow.db"))
    _sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    DATABASE_URL = f"sqlite:///{_sqlite_path}"

# Ensure memory dir exists
MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _init_conn(url: str):
    if url.startswith("sqlite"):
        import sqlite3
        path = url.replace("sqlite:///", "")
        return sqlite3.connect(path)
    else:
        from db.pg_compat import connect as pg_connect
        return pg_connect(url)


if __name__ == "__main__":
    # Init DB schema on startup
    conn = _init_conn(DATABASE_URL)
    init_db(conn)
    conn.close()

    db_label = "SQLite (local)" if DATABASE_URL.startswith("sqlite") else "PostgreSQL"
    print(f"🔧 token-flow service starting on http://localhost:{PORT}")
    print(f"   Workspace : {WORKSPACE}")
    print(f"   Memory dir: {MEMORY_DIR}")
    print(f"   DB        : {db_label}")

    # ── SSO Auth + local session registration ────────────────────────────────
    # Authenticate the user via Auth0 Device Flow before the service starts.
    # If a valid cached token exists, completes silently. Otherwise prints
    # a verification URL for the user to visit in their browser.
    # On success, registers the user identity with the API (local_sessions table)
    # so the dashboard can show who is running the local service.
    try:
        import socket as _socket
        import json as _json2
        import urllib.request as _urllib_req
        from api.device_auth import get_token, get_cached_user

        print("🔐 Authenticating with Auth0 SSO...")
        get_token()
        user_info = get_cached_user()
        user_email = user_info.get("email", "unknown")
        print(f"✅ Authenticated as {user_email}")

        # Register identity with the local API once it's up — do it after startup
        # by storing user info for the post-startup hook below
        _sso_user = user_info
    except Exception as _e:
        print(f"⚠️  SSO auth failed (continuing): {_e}")
        _sso_user = {}
    # ─────────────────────────────────────────────────────────────────────────

    # Register identity with the local API a few seconds after startup
    def _register_local_session():
        import time, socket
        time.sleep(3)  # wait for uvicorn to be ready
        try:
            payload = _json2.dumps({
                "email":     _sso_user.get("email"),
                "name":      _sso_user.get("name"),
                "picture":   _sso_user.get("picture"),
                "auth0_sub": _sso_user.get("sub"),
                "host":      socket.gethostname(),
            }).encode()
            req = _urllib_req.Request(
                f"http://localhost:{PORT}/session/identify",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _urllib_req.urlopen(req, timeout=5) as r:
                print(f"✅ Local session registered: {r.read().decode()}")
        except Exception as e:
            print(f"⚠️  Could not register local session: {e}")

    if _sso_user.get("email"):
        import threading
        threading.Thread(target=_register_local_session, daemon=True).start()

    app = create_app(database_url=DATABASE_URL)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

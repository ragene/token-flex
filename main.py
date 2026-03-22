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
    print(f"🔧 token-flow: connecting to DB...")
    conn = _init_conn(DATABASE_URL)
    init_db(conn)
    conn.close()
    print(f"🔧 token-flow: DB schema initialized")

    db_label = "SQLite (local)" if DATABASE_URL.startswith("sqlite") else "PostgreSQL"
    print(f"🔧 token-flow service starting on http://localhost:{PORT}")
    print(f"   Workspace : {WORKSPACE}")
    print(f"   Memory dir: {MEMORY_DIR}")
    print(f"   DB        : {db_label}")

    # ── SSO Auth + local session registration ────────────────────────────────
    # Phase 1 (pre-startup): Run the Auth0 Device Flow to get an Auth0 access
    # token. We deliberately stop BEFORE calling _exchange(), because that hits
    # localhost:{PORT}/auth/exchange — a server that doesn't exist yet.
    # Phase 2 (post-startup): A background thread waits for uvicorn to be ready,
    # then exchanges the Auth0 token for an internal JWT, caches it, and
    # registers the user identity with /session/identify.
    import json as _json2
    import urllib.request as _urllib_req

    _auth0_token: list = [""]   # mutable container so inner function can update it
    _sso_user: list = [{}]      # _sso_user[0] holds the dict

    _skip_auth = os.environ.get("SKIP_STARTUP_AUTH", "").lower() in ("1", "true", "yes")

    if _skip_auth:
        print("🔐 SSO: skipping startup auth (SKIP_STARTUP_AUTH=true)")
        # If TOKEN_FLOW_JWT is pre-set in env/.env, cache it so push_client picks it up
        _preset_jwt = os.environ.get("TOKEN_FLOW_JWT", "").strip()
        if _preset_jwt:
            try:
                from api.device_auth import _save_cache
                _save_cache(_preset_jwt, expires_in=365 * 24 * 3600)
                print("🔐 SSO: TOKEN_FLOW_JWT cached from env")
            except Exception as _e:
                print(f"⚠️  Could not cache TOKEN_FLOW_JWT: {_e}")
    else:
        try:
            from api.device_auth import _load_cache, get_cached_user, _device_flow

            cached = _load_cache()
            if cached:
                # Token already cached — grab user info directly, no network needed
                print("🔐 SSO: using cached token")
                _sso_user[0] = get_cached_user()
                print(f"✅ Authenticated as {_sso_user[0].get('email', 'unknown')}")
            else:
                # Run Auth0 Device Flow (prints URL, waits for browser login)
                print("🔐 Authenticating with Auth0 SSO...")
                _auth0_token[0] = _device_flow()
                print("✅ Auth0 login complete — will exchange token after server starts")
        except Exception as _e:
            print(f"⚠️  SSO auth failed (continuing): {_e}")
    # ─────────────────────────────────────────────────────────────────────────

    # Phase 2: run after uvicorn is ready
    def _register_local_session():
        import time, socket, urllib.error
        from api.device_auth import _exchange, _save_cache, get_cached_user

        # Wait for uvicorn to be ready
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with _urllib_req.urlopen(f"http://localhost:{PORT}/health", timeout=2):
                    break
            except Exception:
                time.sleep(1)
        else:
            print("⚠️  Server did not become ready in time — skipping session registration")
            return

        # Exchange Auth0 token for internal JWT (only if we did a fresh device flow)
        if _auth0_token[0] and not _sso_user[0]:
            try:
                internal_token, expires_in = _exchange(_auth0_token[0])

                # Fetch user info from Auth0
                user: dict = {}
                try:
                    from api.device_auth import AUTH0_DOMAIN
                    req = _urllib_req.Request(
                        f"https://{AUTH0_DOMAIN}/userinfo",
                        headers={"Authorization": f"Bearer {_auth0_token[0]}"},
                    )
                    with _urllib_req.urlopen(req, timeout=10) as r:
                        user = _json2.loads(r.read().decode())
                except Exception:
                    pass

                _save_cache(internal_token, expires_in, user=user)
                _sso_user[0] = user
                print(f"✅ Authenticated as {_sso_user[0].get('email', 'unknown')}")
            except Exception as e:
                print(f"⚠️  Token exchange failed: {e}")
                return

        if not _sso_user[0].get("email"):
            return

        # Register identity with the API
        try:
            payload = _json2.dumps({
                "email":     _sso_user[0].get("email"),
                "name":      _sso_user[0].get("name"),
                "picture":   _sso_user[0].get("picture"),
                "auth0_sub": _sso_user[0].get("sub"),
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

    import threading
    threading.Thread(target=_register_local_session, daemon=True).start()

    app = create_app(database_url=DATABASE_URL)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

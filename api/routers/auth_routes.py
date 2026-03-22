"""Auth0 exchange and config endpoints for token-flow."""
import os
import time
import urllib.request
import urllib.parse
import urllib.error
import json
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from api.auth import create_access_token
from api.db_helper import get_conn

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

AUTH0_DOMAIN    = os.environ.get("AUTH0_DOMAIN", "")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "aUzCxuMq5qToHnSZHCIIGiWgIIb3A32I")
TOKEN_FLOW_API  = os.environ.get("TOKEN_FLOW_API_URL", "http://localhost:8001")

# In-memory device-flow state keyed by device_code (short-lived)
_device_flow_cache: dict[str, dict] = {}


def _post_form(url: str, data: dict, timeout: int = 15) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


@router.get("/auth/config")
def get_auth_config():
    return {
        "domain": os.environ.get("AUTH0_DOMAIN", ""),
        "clientId": os.environ.get("AUTH0_CLIENT_ID", ""),
        "audience": os.environ.get("AUTH0_AUDIENCE", ""),
        "configured": bool(os.environ.get("AUTH0_DOMAIN") and os.environ.get("AUTH0_CLIENT_ID")),
    }


@router.post("/auth/exchange")
def exchange_auth0_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
):
    domain = os.environ.get("AUTH0_DOMAIN", "")
    if not domain:
        raise HTTPException(status_code=503, detail="Auth0 not configured")
    if not credentials:
        raise HTTPException(status_code=401, detail="No token provided")

    # Call Auth0 userinfo to validate the token and get user info
    try:
        resp = httpx.get(
            f"https://{domain}/userinfo",
            headers={"Authorization": f"Bearer {credentials.credentials}"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail=f"Auth0 userinfo failed: {resp.text}")
        userinfo = resp.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach Auth0: {str(e)}")

    email = userinfo.get("email", "")
    name = userinfo.get("name") or userinfo.get("nickname") or email
    auth0_sub = userinfo.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Auth0 userinfo")

    # Get DB connection via the standard app pattern
    conn = get_conn(request)
    try:
        # Check if user exists
        cur = conn.execute(
            "SELECT id, role, is_active FROM tf_users WHERE email = %s",
            (email,)
        )
        row = cur.fetchone()

        if row is None:
            # Check if this is the very first user — bootstrap as admin if so
            count_cur = conn.execute("SELECT COUNT(*) FROM tf_users")
            count_row = count_cur.fetchone()
            total_users = count_row[0] if count_row else 0

            if total_users == 0:
                # First user ever — make them admin and activate immediately
                cur = conn.execute(
                    """INSERT INTO tf_users (email, name, auth0_sub, role, is_active, last_login)
                       VALUES (%s, %s, %s, 'admin', TRUE, NOW())
                       RETURNING id, role, is_active""",
                    (email, name, auth0_sub)
                )
            else:
                # Normal new SSO user — pending approval
                cur = conn.execute(
                    """INSERT INTO tf_users (email, name, auth0_sub, role, is_active, last_login)
                       VALUES (%s, %s, %s, 'viewer', FALSE, NOW())
                       RETURNING id, role, is_active""",
                    (email, name, auth0_sub)
                )
            row = cur.fetchone()
        else:
            conn.execute(
                "UPDATE tf_users SET auth0_sub=%s, last_login=NOW(), name=%s WHERE email=%s",
                (auth0_sub, name, email)
            )
        conn.commit()

        user_id = row[0]
        role = row[1]
        is_active = row[2]
    finally:
        conn.close()

    if not is_active:
        raise HTTPException(status_code=403, detail="Your account is pending admin approval.")

    internal = create_access_token({
        "sub": str(user_id),
        "email": email,
        "role": role,
        "name": name,
    })
    return {"access_token": internal, "token_type": "bearer"}


# ── Device Flow — start ───────────────────────────────────────────────────────

@router.post("/auth/device/start")
def device_flow_start():
    """
    Start an Auth0 Device Authorization Flow.

    Returns the verification URL (with embedded user_code) and a device_code
    the client should pass to POST /auth/device/poll to check completion.

    Response:
        {
            "device_code": "...",
            "user_code": "ABCD-1234",
            "verification_url": "https://tokenflow.us.auth0.com/activate?user_code=ABCD-1234",
            "expires_in": 300,
            "interval": 5
        }
    """
    domain = AUTH0_DOMAIN
    if not domain:
        raise HTTPException(status_code=503, detail="Auth0 not configured")

    try:
        resp = _post_form(
            f"https://{domain}/oauth/device/code",
            {"client_id": AUTH0_CLIENT_ID, "scope": "openid profile email"},
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Auth0 device/code request failed: {e}")

    device_code      = resp.get("device_code", "")
    user_code        = resp.get("user_code", "")
    verification_url = resp.get("verification_uri_complete") or resp.get("verification_uri", "")
    interval         = resp.get("interval", 5)
    expires_in       = resp.get("expires_in", 300)

    # Cache the device_code state for /auth/device/poll
    _device_flow_cache[device_code] = {
        "interval": interval,
        "expires_at": time.time() + expires_in,
        "last_poll": 0.0,
    }

    return {
        "device_code":       device_code,
        "user_code":         user_code,
        "verification_url":  verification_url,
        "expires_in":        expires_in,
        "interval":          interval,
    }


# ── Device Flow — poll ────────────────────────────────────────────────────────

class _DevicePollRequest(HTTPException):
    pass


@router.post("/auth/device/poll")
async def device_flow_poll(request: Request):
    """
    Poll Auth0 for device flow completion.

    Request body: { "device_code": "..." }

    Returns one of:
        { "status": "pending" }
        { "status": "slow_down" }
        { "status": "expired" }
        { "status": "authorized", "access_token": "<internal-jwt>", "email": "...", "name": "...", "role": "..." }

    On "authorized" the client should store access_token and use it as a
    Bearer token for all subsequent API calls (including POST /token-data/distill).
    """
    domain = AUTH0_DOMAIN
    if not domain:
        raise HTTPException(status_code=503, detail="Auth0 not configured")

    body = await request.json()
    device_code = body.get("device_code", "")
    if not device_code:
        raise HTTPException(status_code=400, detail="device_code required")

    state = _device_flow_cache.get(device_code)
    if not state:
        raise HTTPException(status_code=404, detail="Unknown device_code — call /auth/device/start first")

    if time.time() > state["expires_at"]:
        _device_flow_cache.pop(device_code, None)
        return {"status": "expired"}

    # Respect Auth0's polling interval
    min_interval = state.get("interval", 5)
    elapsed = time.time() - state["last_poll"]
    if elapsed < min_interval:
        return {"status": "pending"}

    state["last_poll"] = time.time()

    # Poll Auth0
    try:
        token_resp = _post_form(
            f"https://{domain}/oauth/token",
            {
                "client_id":   AUTH0_CLIENT_ID,
                "device_code": device_code,
                "grant_type":  "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            err = json.loads(raw)
        except Exception:
            err = {}
        error = err.get("error", "")
        if error == "authorization_pending":
            return {"status": "pending"}
        if error == "slow_down":
            state["interval"] = min_interval + 2
            return {"status": "slow_down"}
        if error == "expired_token":
            _device_flow_cache.pop(device_code, None)
            return {"status": "expired"}
        raise HTTPException(status_code=502, detail=f"Auth0 token poll error: {error}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Auth0 token poll failed: {e}")

    auth0_access_token = token_resp.get("access_token")
    if not auth0_access_token:
        error = token_resp.get("error", "unknown")
        if error == "authorization_pending":
            return {"status": "pending"}
        raise HTTPException(status_code=502, detail=f"Auth0 error: {error}")

    # Exchange Auth0 token → internal JWT (reuse /auth/exchange logic)
    try:
        userinfo_resp = httpx.get(
            f"https://{domain}/userinfo",
            headers={"Authorization": f"Bearer {auth0_access_token}"},
            timeout=10,
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Auth0 userinfo failed after device flow")
        userinfo = userinfo_resp.json()
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Cannot reach Auth0 userinfo: {e}")

    email    = userinfo.get("email", "")
    name     = userinfo.get("name") or userinfo.get("nickname") or email
    auth0_sub = userinfo.get("sub", "")

    if not email:
        raise HTTPException(status_code=400, detail="No email in Auth0 userinfo")

    conn = get_conn(request)
    try:
        cur = conn.execute("SELECT id, role, is_active FROM tf_users WHERE email = %s", (email,))
        row = cur.fetchone()
        if row is None:
            count_row = conn.execute("SELECT COUNT(*) FROM tf_users").fetchone()
            is_first  = (count_row[0] if count_row else 0) == 0
            role_val  = "admin" if is_first else "viewer"
            is_active = True if is_first else False
            cur = conn.execute(
                """INSERT INTO tf_users (email, name, auth0_sub, role, is_active, last_login)
                   VALUES (%s, %s, %s, %s, %s, NOW())
                   RETURNING id, role, is_active""",
                (email, name, auth0_sub, role_val, is_active),
            )
            row = cur.fetchone()
        else:
            conn.execute(
                "UPDATE tf_users SET auth0_sub=%s, last_login=NOW(), name=%s WHERE email=%s",
                (auth0_sub, name, email),
            )
        conn.commit()
        user_id   = row[0]
        role      = row[1]
        is_active = row[2]
    finally:
        conn.close()

    if not is_active:
        raise HTTPException(status_code=403, detail="Your account is pending admin approval.")

    internal = create_access_token({
        "sub":   str(user_id),
        "email": email,
        "role":  role,
        "name":  name,
    })

    _device_flow_cache.pop(device_code, None)

    return {
        "status":       "authorized",
        "access_token": internal,
        "token_type":   "bearer",
        "email":        email,
        "name":         name,
        "role":         role,
    }


# ── /session/identify — no auth required (called by local service at startup) ─

from pydantic import BaseModel as _BaseModel

class _IdentifyRequest(_BaseModel):
    email: str
    name: Optional[str] = None
    picture: Optional[str] = None
    auth0_sub: Optional[str] = None
    host: Optional[str] = None
    session_id: Optional[str] = None

@router.post("/session/identify", status_code=200)
async def identify_local_session(body: _IdentifyRequest, request: Request) -> dict:
    """
    Called by the local token-flow service at startup to register the authenticated user.
    No auth required — the local service has already authenticated via Auth0 device flow.
    """
    import socket
    host = body.host or socket.gethostname()
    try:
        from api.db_helper import get_conn
        conn = get_conn(request)
        existing = conn.execute(
            "SELECT id FROM local_sessions WHERE email = %s", (body.email,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE local_sessions
                   SET name=%s, picture=%s, auth0_sub=%s, host=%s, session_id=%s, last_seen=NOW()
                   WHERE email=%s""",
                (body.name, body.picture, body.auth0_sub, host, body.session_id, body.email),
            )
        else:
            conn.execute(
                """INSERT INTO local_sessions (email, name, picture, auth0_sub, host, session_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (body.email, body.name, body.picture, body.auth0_sub, host, body.session_id),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")
    return {"status": "ok", "email": body.email, "name": body.name, "host": host}

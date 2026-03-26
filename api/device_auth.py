"""
device_auth.py — Auth0 Device Authorization Flow for token-flow CLI / local service.

Usage:
    from api.device_auth import get_token

    token = get_token()   # returns internal JWT string, prompts SSO if needed
    headers = {"Authorization": f"Bearer {token}"}

Flow:
    1. Check ~/.openclaw/tf_auth.json for a cached token (verify expiry)
    2. If missing/expired: start Auth0 device flow, print URL + code, poll until complete
    3. Exchange the Auth0 access token for an internal JWT via POST /auth/exchange
    4. Cache the internal JWT with expiry
    5. Return the internal JWT
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

AUTH0_DOMAIN   = os.environ.get("AUTH0_DOMAIN",   "tokenflow.us.auth0.com")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "aUzCxuMq5qToHnSZHCIIGiWgIIb3A32I")
TOKEN_FLOW_API = os.environ.get("TOKEN_FLOW_API_URL", "http://localhost:8001")
CACHE_PATH     = Path(os.environ.get("TF_AUTH_CACHE", Path.home() / ".openclaw" / "tf_auth.json"))


def _load_cache() -> Optional[str]:
    """Return cached internal JWT if not expired, else None."""
    try:
        data = json.loads(CACHE_PATH.read_text())
        expires_at = data.get("expires_at", 0)
        if time.time() < expires_at - 60:   # 60s buffer
            return data["token"]
    except Exception:
        pass
    return None


def _save_cache(token: str, expires_in: int = 28800, user: Optional[dict] = None) -> None:
    """Cache the internal JWT with an expiry timestamp and optional user info."""
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps({
        "token": token,
        "expires_at": time.time() + expires_in,
        "cached_at": datetime.utcnow().isoformat(),
        "user": user or {},
    }))


def _post(url: str, data: dict, timeout: int = 15) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _post_json(url: str, data: dict, headers: Optional[dict] = None, timeout: int = 15) -> dict:
    body = json.dumps(data).encode()
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _try_open_browser(url: str) -> bool:
    """
    Attempt to open *url* in a GUI browser.  Returns True if a browser was
    successfully launched, False if we're in a headless/SSH environment and
    the user should open the URL themselves.

    Strategy (in order):
      1. DISPLAY / WAYLAND_DISPLAY / macOS → use webbrowser.open()
      2. WSL with Windows browser available → run cmd.exe /c start <url>
      3. Nothing found → return False (caller prints the URL)

    We deliberately do NOT launch headless Chrome with remote-debugging —
    that confuses users and is unnecessary since the URL + code is printed.
    """
    import os as _os
    import subprocess as _sp
    import sys as _sys

    # ── macOS ─────────────────────────────────────────────────────────────────
    if _sys.platform == "darwin":
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    # ── Linux with a live display (GUI or X-forwarding) ──────────────────────
    display   = _os.environ.get("DISPLAY", "")
    wayland   = _os.environ.get("WAYLAND_DISPLAY", "")
    if display or wayland:
        try:
            import webbrowser
            webbrowser.open(url)
            return True
        except Exception:
            return False

    # ── WSL: delegate to Windows browser ─────────────────────────────────────
    try:
        _uname_release = _os.uname().release.lower()
    except AttributeError:
        _uname_release = ""
    wsl_interop = _os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop") or \
                  "microsoft" in _uname_release
    if wsl_interop:
        try:
            _sp.Popen(
                ["cmd.exe", "/c", "start", "", url],
                stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
            )
            return True
        except Exception:
            pass

    # ── SSH / pure headless — nothing to open ────────────────────────────────
    return False


def device_flow() -> str:
    """Public alias for _device_flow — run Auth0 Device Flow without the internal exchange."""
    return _device_flow()


def _device_flow() -> str:
    """
    Run Auth0 Device Authorization Flow.
    Prints the verification URL + code, polls until the user authorizes.
    Returns the Auth0 access token.
    """
    # Step 1: request device code
    resp = _post(
        f"https://{AUTH0_DOMAIN}/oauth/device/code",
        {
            "client_id": AUTH0_CLIENT_ID,
            "scope": "openid profile email",
        }
    )

    device_code     = resp["device_code"]
    user_code       = resp["user_code"]
    verification_url = resp.get("verification_uri_complete") or resp.get("verification_uri", "")
    interval        = resp.get("interval", 5)
    expires_in      = resp.get("expires_in", 300)

    print("\n" + "="*60)
    print("🔐  Token Flow — Login Required")
    print("="*60)

    # Try to auto-open a browser; fall back to displaying the URL
    opened = _try_open_browser(verification_url)
    if opened:
        print(f"\n  ✅ Browser opened for authentication.")
    else:
        print(f"\n  👉 Open this URL to log in:")
        print(f"\n     {verification_url}")
        print(f"\n  Or enter code:  {user_code}")
        print(f"  At:             https://{AUTH0_DOMAIN}/activate")

    print("\n  Waiting for you to authenticate...")
    print("="*60 + "\n")

    # Step 2: poll for token
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        try:
            token_resp = _post(
                f"https://{AUTH0_DOMAIN}/oauth/token",
                {
                    "client_id": AUTH0_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                }
            )
            if "access_token" in token_resp:
                print("  ✅ Authenticated!\n")
                return token_resp["access_token"]
            error = token_resp.get("error", "")
            if error == "authorization_pending":
                continue
            elif error == "slow_down":
                interval += 2
                continue
            else:
                raise RuntimeError(f"Auth0 device flow error: {error} — {token_resp.get('error_description','')}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            err_data = json.loads(body) if body else {}
            error = err_data.get("error", "")
            if error in ("authorization_pending", "slow_down"):
                if error == "slow_down":
                    interval += 2
                continue
            raise RuntimeError(f"Auth0 token poll failed: {error} — {err_data.get('error_description','')}")

    raise RuntimeError("Device flow timed out — please try again")


def _exchange(auth0_token: str) -> tuple[str, int]:
    """
    Exchange an Auth0 access token for an internal token-flow JWT.
    Returns (internal_jwt, expires_in_seconds).
    """
    req = urllib.request.Request(
        f"{TOKEN_FLOW_API}/auth/exchange",
        data=b"{}",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth0_token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
    return data["access_token"], 28800   # 8h to match server-side expiry


def get_token(force_refresh: bool = False) -> str:
    """
    Get a valid internal JWT for the token-flow API.

    - Returns cached token if fresh.
    - Otherwise triggers Auth0 Device Flow (browser login), exchanges for internal JWT, caches it.
    - Raises RuntimeError if auth fails.
    """
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached

    auth0_token = _device_flow()

    # Fetch userinfo from Auth0 and cache alongside the token
    user: dict = {}
    try:
        req = urllib.request.Request(
            f"https://{AUTH0_DOMAIN}/userinfo",
            headers={"Authorization": f"Bearer {auth0_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            user = json.loads(r.read().decode())
    except Exception:
        pass

    internal_token, expires_in = _exchange(auth0_token)
    _save_cache(internal_token, expires_in, user=user)
    return internal_token


def get_cached_user() -> dict:
    """Return the cached Auth0 user info dict, or {} if not available."""
    try:
        data = json.loads(CACHE_PATH.read_text())
        return data.get("user", {})
    except Exception:
        return {}


def clear_cache() -> None:
    """Remove cached token (force re-login on next call)."""
    try:
        CACHE_PATH.unlink()
    except FileNotFoundError:
        pass


def get_auth_headers(force_refresh: bool = False) -> dict:
    """Convenience: return headers dict with Authorization bearer token."""
    return {"Authorization": f"Bearer {get_token(force_refresh=force_refresh)}"}

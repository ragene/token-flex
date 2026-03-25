"""
Thin HTTP client for the token-flow local API.
Mirrors tfcli.client but lives inside the service package so tf-server distill
works without tfcli installed.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

DEFAULT_BASE_URL = os.environ.get("TOKEN_FLOW_URL", "http://localhost:8001")
TF_AUTH_PATH     = Path(os.environ.get("TF_AUTH_PATH",
                        str(Path.home() / ".openclaw" / "tf_auth.json")))


def _load_jwt() -> Optional[str]:
    try:
        data = json.loads(TF_AUTH_PATH.read_text())
        if time.time() < data.get("expires_at", 0) - 60:
            return data["token"]
    except Exception:
        pass
    return None


def _auth_headers() -> dict:
    jwt = _load_jwt()
    if not jwt:
        raise RuntimeError(
            "No valid auth token in ~/.openclaw/tf_auth.json. "
            "Start tf-server so it can authenticate first."
        )
    return {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}


def _request(method: str, path: str, body: Optional[dict] = None,
             base_url: str = DEFAULT_BASE_URL, timeout: int = 300) -> Any:
    url  = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req  = urllib.request.Request(url, data=data, headers=_auth_headers(), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


def memory_full(context_hint="", since="7 hours ago", top_n=20,
                dry_run=False, base_url=DEFAULT_BASE_URL) -> dict:
    return _request("POST", "/memory/full", {
        "context_hint": context_hint, "since": since,
        "top_n": top_n, "dry_run": dry_run,
    }, base_url=base_url)


def summarize(top_pct=0.4, push_to_s3=False, context_hint="",
              base_url=DEFAULT_BASE_URL) -> dict:
    return _request("POST", "/summarize", {
        "top_pct": top_pct, "push_to_s3": push_to_s3,
        "context_hint": context_hint,
    }, base_url=base_url)

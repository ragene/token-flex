"""
api.remote_push — lightweight remote dashboard push client.

Queries the local /tokens API (already computed by uvicorn, no file I/O,
no SQLite) and POSTs a snapshot to the remote TOKEN_FLOW_UI_URL.

Avoids all GIL/SQLite deadlock issues that plague _build_snapshot when
called from a thread or subprocess alongside the main uvicorn process.

Usage:
    from api.remote_push import RemotePusher
    pusher = RemotePusher()
    pusher.push()          # single push
    pusher.run_forever()   # blocking loop (for subprocess use)
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_UI_URL = "https://token-flow.thefreightdawg.com"


class RemotePusher:
    """
    Fetches live token stats from the local service and pushes them
    to the remote dashboard.

    All I/O is HTTP-only — no SQLite, no file reads, no GIL contention.
    """

    def __init__(
        self,
        local_port: int | None = None,
        remote_ui: str | None = None,
        interval: int = 10,
    ):
        self.local_port = local_port or int(os.environ.get("TOKEN_FLOW_PORT", "8001"))
        self.remote_ui  = (remote_ui or os.environ.get("TOKEN_FLOW_UI_URL", _DEFAULT_UI_URL)).rstrip("/")
        self.interval   = interval
        self._push_count = 0

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _get_token(self) -> Optional[str]:
        """Load cached auth token (tf_auth.json → TOKEN_FLOW_JWT env var)."""
        jwt_env = os.environ.get("TOKEN_FLOW_JWT", "").strip()
        if jwt_env:
            return jwt_env
        try:
            cache = Path.home() / ".openclaw" / "tf_auth.json"
            d = json.loads(cache.read_text())
            if time.time() < d.get("expires_at", 0) - 60:
                return d["token"]
        except Exception:
            pass
        return None

    def _get_owner_email(self) -> Optional[str]:
        explicit = os.environ.get("OWNER_EMAIL", "").strip()
        if explicit:
            return explicit
        try:
            cache = Path.home() / ".openclaw" / "tf_auth.json"
            d = json.loads(cache.read_text())
            if time.time() < d.get("expires_at", 0) - 60:
                return (d.get("user") or {}).get("email")
        except Exception:
            pass
        return None

    # ── Local data fetch ──────────────────────────────────────────────────────

    def _fetch_tokens(self) -> dict:
        """GET /tokens from the local service (fast, no file I/O)."""
        import urllib.request as _ur
        url = f"http://localhost:{self.local_port}/tokens"
        req = _ur.Request(url)
        token = self._get_token()
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with _ur.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def _build_payload(self) -> dict:
        """Build the snapshot payload from live /tokens data."""
        tokens_raw = self._fetch_tokens()

        # Normalise to the shape the dashboard expects
        session_tokens = tokens_raw.get("session_tokens", 0)
        claude_tokens  = tokens_raw.get("claude_tokens", 0)

        tokens = {
            "total_tokens_approx":   tokens_raw.get("total_tokens_approx", 0),
            "session_tokens":        session_tokens,
            "active_session_tokens": session_tokens,   # dashboard "Local Session"
            "idle_session_tokens":   claude_tokens,
            "memory_tokens":         tokens_raw.get("memory_tokens", 0),
            "session_files":         tokens_raw.get("session_files", 0),
            "claude_session_files":  tokens_raw.get("claude_session_files", 0),
            "status":                tokens_raw.get("status", "ok"),
            "message":               tokens_raw.get("message", ""),
            "warn_threshold":        tokens_raw.get("warn_threshold", 30000),
            "distill_threshold":     tokens_raw.get("distill_threshold", 30000),
            "cached_chunks":         tokens_raw.get("cached_chunks", 0),
            "cached_chunk_tokens":   tokens_raw.get("cached_chunk_tokens", 0),
        }

        return {
            "ts":          datetime.now(timezone.utc).isoformat(),
            "owner_email": self._get_owner_email(),
            "tokens":      tokens,
            # Empty collections — chunk/event data is only in the full snapshot
            # from push_client._build_snapshot; this lightweight pusher focuses
            # on keeping token counts fresh.
            "summary":        {"rows": [], "grand_total_tokens": 0,
                               "grand_total_calls": 0, "grand_cost_usd": 0.0},
            "chunks":         [],
            "events":         [],
            "memory_entries": [],
            "pipeline_events": [],
            "session":        {},
        }

    # ── Remote push ───────────────────────────────────────────────────────────

    def push(self) -> bool:
        """
        Fetch token stats and push to the remote dashboard.
        Returns True on success, False on failure.
        """
        import httpx

        token = self._get_token()
        if not token:
            log.warning("remote_push: no auth token — skipping")
            return False

        try:
            payload = self._build_payload()
        except Exception as e:
            log.warning("remote_push: failed to build payload: %s", e)
            return False

        endpoint = f"{self.remote_ui}/token-data/push"
        try:
            with httpx.Client(timeout=8, http2=False) as c:
                r = c.post(
                    endpoint,
                    content=json.dumps(payload).encode(),
                    headers={
                        "Content-Type":  "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
            self._push_count += 1
            t = payload["tokens"]
            log.info(
                "push #%d → %s | total=%s active=%s chunks=%s status=%s",
                self._push_count, r.status_code,
                f"{t['total_tokens_approx']:,}",
                f"{t['active_session_tokens']:,}",
                t["cached_chunks"],
                t["status"],
            )
            return r.is_success
        except Exception as e:
            log.warning("remote_push: POST failed: %s", e)
            return False

    # ── Loop ─────────────────────────────────────────────────────────────────

    def wait_for_local_service(self, timeout: int = 60) -> bool:
        """Block until the local service is healthy (or timeout)."""
        import urllib.request as _ur
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                _ur.urlopen(
                    f"http://localhost:{self.local_port}/health", timeout=2
                )
                return True
            except Exception:
                time.sleep(2)
        return False

    def run_forever(self) -> None:
        """Block forever, pushing every self.interval seconds."""
        if not self.wait_for_local_service():
            print(
                f"⚠️  remote_push: local service not ready after 60s — exiting",
                flush=True,
            )
            return

        print(
            f"🚀 Remote push worker → {self.remote_ui} (every {self.interval}s)",
            flush=True,
        )
        while True:
            try:
                ok = self.push()
                if not ok and self._push_count == 0:
                    print("⚠️  First push failed — will retry", flush=True)
            except Exception as e:
                print(f"⚠️  Push error: {e}", flush=True)
            time.sleep(self.interval)


# ── CLI entry point (used by _push_worker.py / subprocess) ───────────────────

def main() -> None:
    import sys
    import logging as _log

    _log.basicConfig(
        level=_log.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    port      = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("TOKEN_FLOW_PORT", "8001"))
    remote_ui = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TOKEN_FLOW_UI_URL", _DEFAULT_UI_URL)
    interval  = int(sys.argv[3]) if len(sys.argv) > 3 else 10

    RemotePusher(local_port=port, remote_ui=remote_ui, interval=interval).run_forever()


if __name__ == "__main__":
    main()

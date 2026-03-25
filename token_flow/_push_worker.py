"""
token_flow._push_worker — standalone push loop process.

Spawned as a subprocess by _cli.py so it runs outside the GIL/event-loop of
the main uvicorn process.  Pushes a snapshot to the remote dashboard every 10s.

Usage (internal):
    python -m token_flow._push_worker <port> <db_url> <remote_ui>
"""
from __future__ import annotations
import json
import sys
import time
import traceback

def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: _push_worker <port> <db_url> <remote_ui>", flush=True)
        sys.exit(1)

    port       = int(sys.argv[1])
    db_url     = sys.argv[2]
    remote_ui  = sys.argv[3].rstrip("/")

    # Ensure package root is on path
    import os
    from pathlib import Path
    pkg_root = str(Path(__file__).parent.parent)
    if pkg_root not in sys.path:
        sys.path.insert(0, pkg_root)

    # Wait for the server to be ready (up to 60s)
    import urllib.request as _ur
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            _ur.urlopen(f"http://localhost:{port}/health", timeout=2)
            break
        except Exception:
            time.sleep(2)

    from api.push_client import _build_snapshot, _get_push_token
    import httpx

    print(f"🚀 Remote push worker started → {remote_ui} (every 10s)", flush=True)
    count = 0
    while True:
        try:
            snap  = _build_snapshot(db_url)
            token = _get_push_token()
            if not token:
                print("⚠️  Push skipped — no auth token", flush=True)
                time.sleep(10)
                continue
            with httpx.Client(timeout=8, http2=False) as c:
                r = c.post(
                    f"{remote_ui}/token-data/push",
                    content=json.dumps(snap).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
            count += 1
            t = snap.get("tokens", {})
            if count == 1 or count % 30 == 0:
                print(
                    f"✅ Push #{count} → {r.status_code} | "
                    f"total={t.get('total_tokens_approx', 0):,} "
                    f"active={t.get('active_session_tokens', 0):,} "
                    f"chunks={t.get('cached_chunks', 0)} "
                    f"status={t.get('status', '?')}",
                    flush=True,
                )
        except Exception as e:
            print(f"⚠️  Push failed: {e}", flush=True)
            traceback.print_exc()
        time.sleep(10)


if __name__ == "__main__":
    main()

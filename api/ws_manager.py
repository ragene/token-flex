"""
WebSocket connection manager for token-flow.

A single shared instance (`ws_manager`) is imported by any router that needs
to broadcast real-time snapshots to connected UI clients.

Usage
-----
    from api.ws_manager import ws_manager

    # In a WebSocket endpoint:
    await ws_manager.connect(websocket)

    # To push a snapshot to all connected clients:
    await ws_manager.broadcast(snapshot_dict)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from fastapi import WebSocket

log = logging.getLogger(__name__)


class WSManager:
    """Tracks connected WebSocket clients and provides a broadcast helper."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self.last_snapshot: dict | None = None  # cache of last pushed snapshot

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.debug("WS client connected  (total=%d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.debug("WS client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, data: dict) -> None:
        """Send a JSON-serialised snapshot to every connected client.

        Caches the snapshot so new connections receive it on connect.
        Dead connections are silently pruned.
        """
        self.last_snapshot = data
        if not self._clients:
            return
        msg = json.dumps(data, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o))
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    async def send_initial(self, ws: WebSocket, fallback: dict) -> None:
        """Send the last cached snapshot to a newly connected client.
        Falls back to the provided dict (DB snapshot) if no push has arrived yet.
        """
        snapshot = self.last_snapshot if self.last_snapshot is not None else fallback
        msg = json.dumps(snapshot, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o))
        await ws.send_text(msg)

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# Module-level singleton — import this everywhere.
ws_manager = WSManager()

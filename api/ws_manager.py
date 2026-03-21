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
from fastapi import WebSocket

log = logging.getLogger(__name__)


class WSManager:
    """Tracks connected WebSocket clients and provides a broadcast helper."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.debug("WS client connected  (total=%d)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        log.debug("WS client disconnected (total=%d)", len(self._clients))

    async def broadcast(self, data: dict) -> None:
        """Send a JSON-serialised snapshot to every connected client.

        Dead connections are silently pruned.
        """
        if not self._clients:
            return
        msg = json.dumps(data)
        dead: set[WebSocket] = set()
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        self._clients -= dead

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# Module-level singleton — import this everywhere.
ws_manager = WSManager()

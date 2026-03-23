"""
WebSocket connection manager for token-flow.

A single shared instance (`ws_manager`) is imported by any router that needs
to broadcast real-time snapshots to connected UI clients.

Usage
-----
    from api.ws_manager import ws_manager

    # In a WebSocket endpoint:
    await ws_manager.connect(websocket, user_email="user@example.com")

    # To push a snapshot builder to all connected clients (per-user):
    await ws_manager.notify(snapshot_fn)
    # snapshot_fn(user_email: str | None) -> dict

    # Legacy: broadcast the same dict to all clients (avoid for user data)
    await ws_manager.broadcast(snapshot_dict)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Awaitable, Callable, Optional
from fastapi import WebSocket

log = logging.getLogger(__name__)

_JsonDefault = Callable[[object], str]

def _default(o: object) -> str:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


class WSManager:
    """Tracks connected WebSocket clients keyed by user_email."""

    def __init__(self) -> None:
        # Map websocket -> user_email (None = unauthenticated / admin)
        self._clients: dict[WebSocket, Optional[str]] = {}

    async def connect(self, ws: WebSocket, user_email: Optional[str] = None) -> None:
        await ws.accept()
        self._clients[ws] = user_email
        log.debug("WS client connected  user=%s (total=%d)", user_email, len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)
        log.debug("WS client disconnected (total=%d)", len(self._clients))

    async def notify(
        self,
        snapshot_fn: Callable[[Optional[str]], dict],
    ) -> None:
        """Call snapshot_fn(user_email) for each connected client and send
        the result only to that client.  Dead connections are pruned.

        This ensures every user sees only their own data on live pushes.
        """
        if not self._clients:
            return
        dead: list[WebSocket] = []
        for ws, email in list(self._clients.items()):
            try:
                data = snapshot_fn(email)
                await ws.send_text(json.dumps(data, default=_default))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    async def broadcast(self, data: dict) -> None:
        """Send the *same* JSON payload to every connected client.

        Kept for backwards-compat with non-user-scoped events (e.g. system
        health pings).  Do NOT use this for user-specific token data — use
        notify() instead.
        """
        if not self._clients:
            return
        msg = json.dumps(data, default=_default)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.pop(ws, None)

    @property
    def connection_count(self) -> int:
        return len(self._clients)


# Module-level singleton — import this everywhere.
ws_manager = WSManager()

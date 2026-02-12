"""WebSocket streaming — real-time event and agent status broadcasts.

Endpoint: ws://localhost:8000/v1/stream?token={api_key}
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from shared.enums import MAX_WEBSOCKET_CONNECTIONS, Severity

logger = logging.getLogger(__name__)


# Severity ordering for min_severity filter
_SEVERITY_ORDER = {
    Severity.DEBUG: 0,
    Severity.INFO: 1,
    Severity.WARN: 2,
    Severity.ERROR: 3,
}


class Subscription:
    """Per-connection subscription state."""

    def __init__(self):
        self.channels: set[str] = set()
        self.filters: dict[str, Any] = {}

    @property
    def project_id(self) -> str | None:
        return self.filters.get("project_id")

    @property
    def environment(self) -> str | None:
        return self.filters.get("environment")

    @property
    def group(self) -> str | None:
        return self.filters.get("group")

    @property
    def agent_id(self) -> str | None:
        return self.filters.get("agent_id")

    @property
    def event_types(self) -> set[str] | None:
        et = self.filters.get("event_types")
        if et and isinstance(et, list):
            return set(et)
        return None

    @property
    def min_severity(self) -> str | None:
        return self.filters.get("min_severity")

    def matches_event(self, event: dict) -> bool:
        """Check if an event matches this subscription's filters."""
        if "events" not in self.channels:
            return False
        if self.project_id and event.get("project_id") != self.project_id:
            return False
        if self.environment and event.get("environment") != self.environment:
            return False
        if self.group and event.get("group") != self.group:
            return False
        if self.agent_id and event.get("agent_id") != self.agent_id:
            return False
        if self.event_types and event.get("event_type") not in self.event_types:
            return False
        if self.min_severity:
            event_sev = event.get("severity", "info")
            if _SEVERITY_ORDER.get(event_sev, 1) < _SEVERITY_ORDER.get(
                self.min_severity, 0
            ):
                return False
        return True

    def matches_agent(self) -> bool:
        """Check if this subscription wants agent updates."""
        return "agents" in self.channels


class ConnectionInfo:
    """Metadata for a WebSocket connection."""

    def __init__(self, ws: WebSocket, tenant_id: str, key_id: str):
        self.ws = ws
        self.tenant_id = tenant_id
        self.key_id = key_id
        self.subscription = Subscription()
        self.missed_pongs = 0


class WebSocketManager:
    """Manages WebSocket connections, subscriptions, and broadcasts."""

    def __init__(self):
        # tenant_id -> list of connections
        self._connections: dict[str, list[ConnectionInfo]] = defaultdict(list)
        # key_id -> connection count (for limits)
        self._key_counts: dict[str, int] = defaultdict(int)
        # agent_id -> previously known stuck state (to fire once per episode)
        self._stuck_fired: dict[str, bool] = {}

    @property
    def connection_count(self) -> int:
        return sum(len(conns) for conns in self._connections.values())

    def connections_for_tenant(self, tenant_id: str) -> list[ConnectionInfo]:
        return self._connections.get(tenant_id, [])

    async def accept(
        self, ws: WebSocket, tenant_id: str, key_id: str
    ) -> ConnectionInfo | None:
        """Accept a new WebSocket connection if under limits."""
        if self._key_counts[key_id] >= MAX_WEBSOCKET_CONNECTIONS:
            await ws.close(code=4002, reason="Too many connections for this API key")
            return None
        await ws.accept()
        conn = ConnectionInfo(ws, tenant_id, key_id)
        self._connections[tenant_id].append(conn)
        self._key_counts[key_id] += 1
        return conn

    def disconnect(self, conn: ConnectionInfo) -> None:
        """Remove a connection from the registry."""
        tenant_conns = self._connections.get(conn.tenant_id, [])
        if conn in tenant_conns:
            tenant_conns.remove(conn)
        self._key_counts[conn.key_id] = max(
            0, self._key_counts[conn.key_id] - 1
        )

    async def handle_message(
        self, conn: ConnectionInfo, data: dict
    ) -> None:
        """Process a client message (subscribe, unsubscribe, ping)."""
        action = data.get("action", "")

        if action == "subscribe":
            channels = data.get("channels", [])
            valid_channels = {"events", "agents"}
            conn.subscription.channels = {
                c for c in channels if c in valid_channels
            }
            conn.subscription.filters = data.get("filters", {}) or {}
            await self._send(conn, {
                "type": "subscribed",
                "channels": list(conn.subscription.channels),
                "filters": conn.subscription.filters,
            })

        elif action == "unsubscribe":
            channels = set(data.get("channels", []))
            conn.subscription.channels -= channels
            await self._send(conn, {
                "type": "unsubscribed",
                "channels": list(channels),
            })

        elif action == "ping":
            await self._send(conn, {
                "type": "pong",
                "server_time": datetime.now(timezone.utc).isoformat(),
            })

    async def broadcast_events(
        self, tenant_id: str, events: list[dict]
    ) -> None:
        """Push new events to matching subscribers."""
        for conn in self._connections.get(tenant_id, []):
            for event in events:
                if conn.subscription.matches_event(event):
                    await self._send(conn, {
                        "type": "event.new",
                        "data": event,
                    })

    async def broadcast_agent_status_change(
        self,
        tenant_id: str,
        agent_id: str,
        previous_status: str,
        new_status: str,
        current_task_id: str | None = None,
        current_project_id: str | None = None,
        heartbeat_age_seconds: int | None = None,
    ) -> None:
        """Push agent status change to subscribers on the 'agents' channel."""
        msg = {
            "type": "agent.status_changed",
            "data": {
                "agent_id": agent_id,
                "previous_status": previous_status,
                "new_status": new_status,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "current_task_id": current_task_id,
                "current_project_id": current_project_id,
                "heartbeat_age_seconds": heartbeat_age_seconds,
            },
        }
        for conn in self._connections.get(tenant_id, []):
            if conn.subscription.matches_agent():
                await self._send(conn, msg)

    async def broadcast_agent_stuck(
        self,
        tenant_id: str,
        agent_id: str,
        last_heartbeat: str | None,
        stuck_threshold_seconds: int,
        current_task_id: str | None = None,
        current_project_id: str | None = None,
    ) -> None:
        """Fire once per stuck episode — not repeatedly."""
        cache_key = f"{tenant_id}:{agent_id}"
        if self._stuck_fired.get(cache_key):
            return  # Already fired for this episode
        self._stuck_fired[cache_key] = True

        msg = {
            "type": "agent.stuck",
            "data": {
                "agent_id": agent_id,
                "last_heartbeat": last_heartbeat,
                "stuck_threshold_seconds": stuck_threshold_seconds,
                "current_task_id": current_task_id,
                "current_project_id": current_project_id,
            },
        }
        for conn in self._connections.get(tenant_id, []):
            if conn.subscription.matches_agent():
                await self._send(conn, msg)

    def clear_stuck(self, tenant_id: str, agent_id: str) -> None:
        """Clear stuck flag when agent recovers."""
        cache_key = f"{tenant_id}:{agent_id}"
        self._stuck_fired.pop(cache_key, None)

    async def ping_all(self) -> None:
        """Send ping to all connections. Close stale ones."""
        for tenant_id, conns in list(self._connections.items()):
            for conn in list(conns):
                try:
                    await conn.ws.send_json({"type": "ping"})
                    conn.missed_pongs += 1
                    if conn.missed_pongs >= 3:
                        await conn.ws.close(
                            code=4003, reason="Ping timeout"
                        )
                        self.disconnect(conn)
                except Exception:
                    self.disconnect(conn)

    async def _send(self, conn: ConnectionInfo, data: dict) -> None:
        """Send a JSON message to a connection, handling errors."""
        try:
            await conn.ws.send_json(data)
        except Exception:
            self.disconnect(conn)


# Singleton manager — used by app.py and ingestion
ws_manager = WebSocketManager()

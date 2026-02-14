"""WebSocket bridge — AWS API Gateway WebSocket integration.

In production, the dashboard connects to AWS API Gateway (WebSocket API).
API Gateway forwards connect/disconnect/message as HTTP POST requests to
the backend, with connectionId in the request header.

The backend pushes messages to clients via the API Gateway Management API
(boto3 apigatewaymanagementapi).

Local development continues to use direct WebSocket via websocket.py.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import boto3

from backend.websocket import Subscription

logger = logging.getLogger(__name__)


class BridgeConnection:
    """A connection tracked by connectionId (not a live WebSocket)."""

    def __init__(self, connection_id: str, tenant_id: str, key_id: str):
        self.connection_id = connection_id
        self.tenant_id = tenant_id
        self.key_id = key_id
        self.subscription = Subscription()


class WebSocketBridge:
    """Manages AWS API Gateway WebSocket connections.

    Mirrors the broadcast API of WebSocketManager so call sites can use
    either interchangeably via _get_broadcaster().
    """

    def __init__(self, gateway_endpoint: str, region: str):
        self._connections: dict[str, BridgeConnection] = {}
        self._tenant_index: dict[str, list[str]] = defaultdict(list)
        self._stuck_fired: dict[str, bool] = {}
        self._apigw_client = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=gateway_endpoint,
            region_name=region,
        )

    # ── Connection management ─────────────────────────

    def is_registered(self, connection_id: str) -> bool:
        return connection_id in self._connections

    def register(self, connection_id: str, tenant_id: str, key_id: str) -> None:
        """Register a new connection (or update if re-registering)."""
        if connection_id in self._connections:
            # Already registered — update tenant index if needed
            return
        conn = BridgeConnection(connection_id, tenant_id, key_id)
        self._connections[connection_id] = conn
        self._tenant_index[tenant_id].append(connection_id)
        logger.debug("Bridge: registered %s for tenant %s", connection_id, tenant_id)

    def unregister(self, connection_id: str) -> None:
        """Remove a connection from the registry."""
        conn = self._connections.pop(connection_id, None)
        if conn:
            tenant_list = self._tenant_index.get(conn.tenant_id, [])
            if connection_id in tenant_list:
                tenant_list.remove(connection_id)
            logger.debug("Bridge: unregistered %s", connection_id)

    def subscribe(
        self, connection_id: str, channels: list[str], filters: dict[str, Any]
    ) -> None:
        conn = self._connections.get(connection_id)
        if not conn:
            logger.warning("Bridge: subscribe for unknown connection %s", connection_id)
            return
        valid_channels = {"events", "agents"}
        conn.subscription.channels = {c for c in channels if c in valid_channels}
        conn.subscription.filters = filters or {}

    def unsubscribe(self, connection_id: str, channels: list[str]) -> None:
        conn = self._connections.get(connection_id)
        if not conn:
            return
        conn.subscription.channels -= set(channels)

    # ── Broadcast methods (mirror WebSocketManager API) ───

    async def broadcast_events(
        self, tenant_id: str, events: list[dict]
    ) -> None:
        """Push new events to matching subscribers via API Gateway."""
        for conn_id in list(self._tenant_index.get(tenant_id, [])):
            conn = self._connections.get(conn_id)
            if not conn:
                continue
            for event in events:
                if conn.subscription.matches_event(event):
                    self._push(conn_id, {"type": "event.new", "data": event})

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
        for conn_id in list(self._tenant_index.get(tenant_id, [])):
            conn = self._connections.get(conn_id)
            if conn and conn.subscription.matches_agent():
                self._push(conn_id, msg)

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
            return
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
        for conn_id in list(self._tenant_index.get(tenant_id, [])):
            conn = self._connections.get(conn_id)
            if conn and conn.subscription.matches_agent():
                self._push(conn_id, msg)

    def clear_stuck(self, tenant_id: str, agent_id: str) -> None:
        """Clear stuck flag when agent recovers."""
        cache_key = f"{tenant_id}:{agent_id}"
        self._stuck_fired.pop(cache_key, None)

    # ── Push via API Gateway Management API ───────────

    def _push(self, connection_id: str, data: dict) -> None:
        """Send message to a specific connection via API Gateway."""
        try:
            self._apigw_client.post_to_connection(
                ConnectionId=connection_id,
                Data=json.dumps(data).encode("utf-8"),
            )
        except self._apigw_client.exceptions.GoneException:
            # Client disconnected but $disconnect didn't fire
            self.unregister(connection_id)
        except Exception:
            logger.warning("Failed to push to connection %s", connection_id)

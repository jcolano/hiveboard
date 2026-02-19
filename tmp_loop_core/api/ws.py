"""
WebSocket routes for loopCore.

These endpoints are called by AWS API Gateway WebSocket API (HTTP integration).
They are NOT WebSocket endpoints themselves — they are plain HTTP POST handlers
that receive connection lifecycle events from API Gateway.

Uses the same AWS API Gateway WebSocket as loopColony but with loopCore-specific
authentication and event handling.
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSocket"])

# Data directory for WebSocket connections
DATA_DIR = Path(__file__).parent.parent.parent.parent / "data" / "loopCore" / "WS"


def _ensure_data_dir():
    """Ensure the WS data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connections_file = DATA_DIR / "connections.json"
    if not connections_file.exists():
        connections_file.write_text(json.dumps({"connections": []}, indent=2))
    return connections_file


def _load_connections() -> list:
    """Load all WebSocket connections."""
    connections_file = _ensure_data_dir()
    try:
        data = json.loads(connections_file.read_text())
        return data.get("connections", [])
    except Exception as e:
        logger.error(f"Failed to load WS connections: {e}")
        return []


def _save_connections(connections: list):
    """Save WebSocket connections."""
    connections_file = _ensure_data_dir()
    try:
        connections_file.write_text(json.dumps({"connections": connections}, indent=2))
    except Exception as e:
        logger.error(f"Failed to save WS connections: {e}")


def _find_connection(user_id: str = None, connection_id: str = None) -> Optional[dict]:
    """Find a connection by user_id or connection_id."""
    connections = _load_connections()
    for conn in connections:
        if user_id and conn.get("user_id") == user_id:
            return conn
        if connection_id and conn.get("connection_id") == connection_id:
            return conn
    return None


def _add_connection(user_id: str, connection_id: str):
    """Add or replace a connection for a user."""
    connections = _load_connections()
    # Remove existing connections for this user
    connections = [c for c in connections if c.get("user_id") != user_id]
    # Add new connection
    connections.append({
        "user_id": user_id,
        "connection_id": connection_id,
        "connected_at": datetime.utcnow().isoformat() + "Z"
    })
    _save_connections(connections)


def _remove_connection(connection_id: str):
    """Remove a connection by connection_id."""
    connections = _load_connections()
    connections = [c for c in connections if c.get("connection_id") != connection_id]
    _save_connections(connections)


def _authenticate_from_token(token: str) -> Optional[dict]:
    """
    Authenticate a user from a raw token.
    Returns user info dict or None.
    """
    if not token:
        return None

    try:
        from .auth import get_user_store
        user_store = get_user_store()
        user = user_store.get_by_token(token)
        if user:
            return {
                "user_id": user.user_id,
                "email": user.email,
                "company_id": user.company_id
            }
    except Exception as e:
        logger.error(f"WS auth error: {e}")

    return None


@router.post("/connect")
async def ws_connect(request: Request):
    """
    Called by API Gateway when a client connects to the WebSocket.

    API Gateway sends:
    - connectionId header (configured via request parameter mapping)
    - token query parameter (sent by browser: wss://...?token=xxx)
    """
    # Get connection ID from header
    connection_id = request.headers.get("connectionid")
    if not connection_id:
        logger.warning("ws_connect: missing connectionId header")
        raise HTTPException(status_code=400, detail="Missing connectionId header")

    # Get auth token from query string
    token = request.query_params.get("token")
    if not token:
        logger.warning(f"ws_connect: missing token, connection_id={connection_id}")
        raise HTTPException(status_code=401, detail="Missing token")

    # Authenticate
    user = _authenticate_from_token(token)
    if not user:
        logger.warning(f"ws_connect: auth failed, connection_id={connection_id}")
        raise HTTPException(status_code=403, detail="Invalid token")

    # Store connection
    _add_connection(user["user_id"], connection_id)

    logger.info(f"ws_connected: user_id={user['user_id']}, connection_id={connection_id}")

    return {"statusCode": 200}


@router.post("/disconnect")
async def ws_disconnect(request: Request):
    """
    Called by API Gateway when a client disconnects from the WebSocket.
    """
    connection_id = request.headers.get("connectionid")
    if not connection_id:
        return {"statusCode": 200}

    # Find connection to log user_id
    conn = _find_connection(connection_id=connection_id)
    if conn:
        logger.info(f"ws_disconnected: user_id={conn['user_id']}, connection_id={connection_id}")
        _remove_connection(connection_id)

    return {"statusCode": 200}


@router.post("/message")
async def ws_message(request: Request):
    """
    Called by API Gateway for any message sent by the browser ($default route).
    Handles client pings — re-registers the connection if unknown.
    """
    connection_id = request.headers.get("connectionid")
    if not connection_id:
        return {"statusCode": 200}

    # Check if this connection is known
    existing = _find_connection(connection_id=connection_id)
    if existing:
        # Known connection — just acknowledge
        return {"statusCode": 200}

    # Unknown connection — try to authenticate and re-register
    try:
        body = await request.json()
        token = body.get("token")
    except Exception:
        token = None

    if not token:
        logger.warning(f"ws_message: unknown connection, no token, connection_id={connection_id}")
        return {"statusCode": 200}

    user = _authenticate_from_token(token)
    if not user:
        logger.warning(f"ws_message: auth failed, connection_id={connection_id}")
        return {"statusCode": 200}

    # Re-register connection
    _add_connection(user["user_id"], connection_id)
    logger.info(f"ws_reconnected: user_id={user['user_id']}, connection_id={connection_id}")

    return {"statusCode": 200}

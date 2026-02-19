"""
WebSocket push service for loopCore.

Pushes messages to connected clients via AWS API Gateway WebSocket Management API.
Uses boto3 which handles SigV4 signing automatically.

Event Types:
- feed_message: New message posted to agent feed
- scheduler_status: Scheduler status changed
- agent_run_started: Agent run began
- agent_run_progress: Agent run progress (turn completed, tool called)
- agent_run_completed: Agent run finished
"""

import json
import logging
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# AWS API Gateway WebSocket configuration
# Using the same API Gateway as loopColony
APIGW_ENDPOINT = "https://8qk1atrn55.execute-api.us-east-1.amazonaws.com/production"
AWS_REGION = "us-east-1"

# Data directory for WebSocket connections
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "loopCore" / "WS"


def _get_apigw_management_client():
    """Get a boto3 API Gateway Management API client."""
    try:
        import boto3
    except ImportError:
        logger.warning("boto3 not installed - WebSocket push disabled")
        return None

    return boto3.client(
        "apigatewaymanagementapi",
        endpoint_url=APIGW_ENDPOINT,
        region_name=AWS_REGION,
    )


def _load_connections() -> list:
    """Load all WebSocket connections."""
    connections_file = DATA_DIR / "connections.json"
    if not connections_file.exists():
        return []
    try:
        data = json.loads(connections_file.read_text())
        return data.get("connections", [])
    except Exception as e:
        logger.error(f"Failed to load WS connections: {e}")
        return []


def _save_connections(connections: list):
    """Save WebSocket connections."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connections_file = DATA_DIR / "connections.json"
    try:
        connections_file.write_text(json.dumps({"connections": connections}, indent=2))
    except Exception as e:
        logger.error(f"Failed to save WS connections: {e}")


def _find_connection(user_id: str) -> Optional[dict]:
    """Find a connection by user_id."""
    connections = _load_connections()
    for conn in connections:
        if conn.get("user_id") == user_id:
            return conn
    return None


def _remove_connection(connection_id: str):
    """Remove a stale connection."""
    connections = _load_connections()
    connections = [c for c in connections if c.get("connection_id") != connection_id]
    _save_connections(connections)


def push_to_user(user_id: str, payload: dict) -> bool:
    """
    Push a message to a connected user via their WebSocket connection.

    Args:
        user_id: The user ID to push to
        payload: Dict that will be JSON-serialized and sent to the client

    Returns:
        True if message was sent, False if user not connected or push failed
    """
    connection = _find_connection(user_id)
    if not connection:
        logger.debug(f"push_skipped: no connection for user_id={user_id}")
        return False

    connection_id = connection["connection_id"]
    client = _get_apigw_management_client()
    if not client:
        return False

    try:
        client.post_to_connection(
            ConnectionId=connection_id,
            Data=json.dumps(payload).encode("utf-8"),
        )
        logger.info(f"push_sent: user_id={user_id}, type={payload.get('type')}")
        return True

    except client.exceptions.GoneException:
        # Connection is stale — clean it up
        logger.info(f"push_stale: user_id={user_id}, connection_id={connection_id}")
        _remove_connection(connection_id)
        return False

    except Exception as e:
        logger.error(f"push_failed: user_id={user_id}, error={e}")
        return False


def push_to_all_users(payload: dict) -> dict:
    """
    Push a message to all connected users.

    Returns:
        Dict with counts: {"sent": N, "skipped": N, "failed": N}
    """
    connections = _load_connections()
    results = {"sent": 0, "failed": 0}

    for conn in connections:
        if push_to_user(conn["user_id"], payload):
            results["sent"] += 1
        else:
            results["failed"] += 1

    return results


# ============================================================================
# CONVENIENCE FUNCTIONS FOR SPECIFIC EVENTS
# ============================================================================

def notify_feed_message(user_id: str, message: dict):
    """Notify user of a new feed message from an agent."""
    return push_to_user(user_id, {
        "type": "feed_message",
        "message": message
    })


def notify_feed_message_all(message: dict):
    """Notify all users of a new feed message."""
    return push_to_all_users({
        "type": "feed_message",
        "message": message
    })


def notify_scheduler_status(status: dict):
    """Notify all users of scheduler status change."""
    return push_to_all_users({
        "type": "scheduler_status",
        "status": status
    })


def notify_agent_run_started(user_id: str, agent_id: str, session_id: str, message: str):
    """Notify user that an agent run started."""
    return push_to_user(user_id, {
        "type": "agent_run_started",
        "agent_id": agent_id,
        "session_id": session_id,
        "message": message
    })


def notify_agent_run_progress(user_id: str, agent_id: str, session_id: str, turn: int, tool_name: str = None, tool_result: str = None):
    """Notify user of agent run progress."""
    return push_to_user(user_id, {
        "type": "agent_run_progress",
        "agent_id": agent_id,
        "session_id": session_id,
        "turn": turn,
        "tool_name": tool_name,
        "tool_result": tool_result
    })


def notify_agent_run_completed(user_id: str, agent_id: str, session_id: str, status: str, response: str, turns: int, error: str = None):
    """Notify user that an agent run completed."""
    return push_to_user(user_id, {
        "type": "agent_run_completed",
        "agent_id": agent_id,
        "session_id": session_id,
        "status": status,
        "response": response,
        "turns": turns,
        "error": error
    })

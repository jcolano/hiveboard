"""
FEED_TOOLS
==========

Agent-to-human communication via the operator feed.

The feed is how agents proactively communicate with their human operators.
Messages appear in the "Feed" tab of the loopCore admin UI (port 8431) and
can be pushed in real-time via WebSocket.

Message Types & Use Cases
--------------------------
- ``info``: General status updates, discoveries, summaries
- ``success``: Task completion confirmations, positive results
- ``warning``: Issues that need attention, degraded performance
- ``error``: Failures, exceptions, things that broke
- ``request``: Agent needs human input, approval, or a decision

Priority Levels
----------------
- ``low``, ``normal``, ``high``, ``urgent``

Storage
-------
All messages stored in ``data/loopCore/FEED/messages.json`` (single JSON file).
Maximum 1000 messages retained — oldest deleted when limit exceeded.

UI Integration
--------------
The admin UI at port 8431 has a Feed tab with:
- Unread badge on the tab (updated via WebSocket push)
- Filter by type (info/success/warning/error/request) and read status
- Mark as read / delete actions
- Color-coded left borders (blue=info, green=success, yellow=warning, red=error,
  purple=request)

API Endpoints (in api/app.py)
------------------------------
- ``GET /api/feed`` — Retrieve messages with filters (limit, offset, agent_id,
  unread_only, message_type)
- ``GET /api/feed/unread-count`` — Unread count (optionally by agent)
- ``PUT /api/feed/{message_id}/read`` — Mark one message as read
- ``POST /api/feed/mark-all-read`` — Mark all as read
- ``DELETE /api/feed/{message_id}`` — Delete a message

Helper functions (for API use, not agent tools):
  ``get_feed_messages()``, ``mark_message_read()``, ``mark_all_read()``,
  ``delete_message()``, ``get_unread_count()``

Usage::

    # Agent posts via tool:
    feed_tool = FeedPostTool(agent_id="cheerleader_007")
    feed_tool.execute(title="Daily Report", body="All tasks complete.",
                      message_type="success", priority="normal")
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import uuid

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult

logger = logging.getLogger(__name__)

# Feed data directory
FEED_DIR = Path(__file__).parent.parent.parent.parent / "data" / "loopCore" / "FEED"


def _ensure_feed_file():
    """Ensure the feed messages file exists."""
    FEED_DIR.mkdir(parents=True, exist_ok=True)
    messages_file = FEED_DIR / "messages.json"
    if not messages_file.exists():
        messages_file.write_text(json.dumps({"messages": []}, indent=2))
    return messages_file


def _load_messages():
    """Load all messages from the feed."""
    messages_file = _ensure_feed_file()
    try:
        data = json.loads(messages_file.read_text())
        return data.get("messages", [])
    except Exception as e:
        logger.error(f"Failed to load feed messages: {e}")
        return []


def _save_messages(messages: list):
    """Save messages to the feed file."""
    messages_file = _ensure_feed_file()
    try:
        messages_file.write_text(json.dumps({"messages": messages}, indent=2))
    except Exception as e:
        logger.error(f"Failed to save feed messages: {e}")


class FeedPostTool(BaseTool):
    """
    Post a message to the operator feed.

    Use this when you need to communicate important information to your
    human operator that they should see even when not actively chatting.
    """

    def __init__(self, agent_id: str):
        """
        Initialize the feed post tool.

        Args:
            agent_id: ID of the agent using this tool
        """
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="feed_post",
            description=(
                "Post a message to your human operator's feed. Use this when you need to "
                "communicate important information that should be visible even when the "
                "operator isn't actively chatting with you.\n\n"
                "Common use cases:\n"
                "- Share invitation codes, tokens, or credentials\n"
                "- Report task completion or status updates\n"
                "- Request human input or approval\n"
                "- Alert about errors or important issues\n"
                "- Share discoveries or results\n\n"
                "The message will appear in the operator's Feed panel in the admin UI."
            ),
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Short title for the message (max 200 chars)",
                    required=True
                ),
                ToolParameter(
                    name="body",
                    type="string",
                    description="Full message content. Supports markdown formatting.",
                    required=True
                ),
                ToolParameter(
                    name="message_type",
                    type="string",
                    description=(
                        "Type of message: 'info' (general), 'success' (positive outcome), "
                        "'warning' (attention needed), 'error' (problem), 'request' (needs human input)"
                    ),
                    required=False
                ),
                ToolParameter(
                    name="priority",
                    type="string",
                    description="Priority level: 'low', 'normal', 'high', 'urgent'. Use 'urgent' sparingly.",
                    required=False
                )
            ]
        )

    def execute(self, **kwargs) -> ToolResult:
        """Post a message to the operator feed."""
        title = kwargs.get("title", "")
        body = kwargs.get("body", "")
        message_type = kwargs.get("message_type", "info")
        priority = kwargs.get("priority", "normal")

        try:
            # Validate inputs
            if not title:
                return ToolResult(
                    success=False,
                    output="",
                    error="Title is required"
                )

            if len(title) > 200:
                title = title[:200]

            if not body:
                return ToolResult(
                    success=False,
                    output="",
                    error="Body is required"
                )

            valid_types = ["info", "success", "warning", "error", "request"]
            if message_type not in valid_types:
                message_type = "info"

            valid_priorities = ["low", "normal", "high", "urgent"]
            if priority not in valid_priorities:
                priority = "normal"

            # Create message
            message_id = f"msg_{uuid.uuid4().hex[:12]}"
            message = {
                "id": message_id,
                "agent_id": self.agent_id,
                "title": title,
                "body": body,
                "type": message_type,
                "priority": priority,
                "read": False,
                "created_at": datetime.utcnow().isoformat() + "Z"
            }

            # Load existing messages and prepend new one
            messages = _load_messages()
            messages.insert(0, message)

            # Keep only last 1000 messages
            messages = messages[:1000]

            _save_messages(messages)

            logger.info(f"Agent {self.agent_id} posted feed message: {title}")

            # Push WebSocket notification to all connected users
            try:
                from loop_core.services.ws_push import notify_feed_message_all
                notify_feed_message_all(message)
            except Exception as ws_err:
                logger.debug(f"WebSocket push failed (non-critical): {ws_err}")

            return ToolResult(
                success=True,
                output=f"Message posted to operator feed successfully. Message ID: {message_id}",
                error=None
            )

        except Exception as e:
            logger.error(f"Failed to post feed message: {e}")
            return ToolResult(
                success=False,
                output="",
                error=str(e)
            )


# Helper functions for API use

def get_feed_messages(
    limit: int = 50,
    offset: int = 0,
    agent_id: Optional[str] = None,
    unread_only: bool = False,
    message_type: Optional[str] = None
) -> dict:
    """
    Get messages from the feed.

    Args:
        limit: Maximum number of messages to return
        offset: Number of messages to skip
        agent_id: Filter by agent ID
        unread_only: Only return unread messages
        message_type: Filter by message type

    Returns:
        dict with messages list and total count
    """
    messages = _load_messages()

    # Apply filters
    if agent_id:
        messages = [m for m in messages if m.get("agent_id") == agent_id]

    if unread_only:
        messages = [m for m in messages if not m.get("read", False)]

    if message_type:
        messages = [m for m in messages if m.get("type") == message_type]

    total = len(messages)

    # Apply pagination
    messages = messages[offset:offset + limit]

    return {
        "messages": messages,
        "total": total,
        "limit": limit,
        "offset": offset
    }


def mark_message_read(message_id: str) -> dict:
    """Mark a message as read."""
    messages = _load_messages()

    for msg in messages:
        if msg.get("id") == message_id:
            msg["read"] = True
            msg["read_at"] = datetime.utcnow().isoformat() + "Z"
            _save_messages(messages)
            return {"success": True}

    return {"success": False, "error": "Message not found"}


def mark_all_read(agent_id: Optional[str] = None) -> dict:
    """Mark all messages as read, optionally filtered by agent."""
    messages = _load_messages()
    count = 0

    for msg in messages:
        if not msg.get("read", False):
            if agent_id is None or msg.get("agent_id") == agent_id:
                msg["read"] = True
                msg["read_at"] = datetime.utcnow().isoformat() + "Z"
                count += 1

    _save_messages(messages)
    return {"success": True, "marked_count": count}


def delete_message(message_id: str) -> dict:
    """Delete a message from the feed."""
    messages = _load_messages()
    original_count = len(messages)

    messages = [m for m in messages if m.get("id") != message_id]

    if len(messages) < original_count:
        _save_messages(messages)
        return {"success": True}

    return {"success": False, "error": "Message not found"}


def get_unread_count(agent_id: Optional[str] = None) -> int:
    """Get count of unread messages."""
    messages = _load_messages()

    if agent_id:
        messages = [m for m in messages if m.get("agent_id") == agent_id]

    return sum(1 for m in messages if not m.get("read", False))

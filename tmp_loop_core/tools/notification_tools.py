"""
NOTIFICATION_TOOLS
==================

Notification tool for the Agentic Loop Framework.

``send_dm_notification``
    Send a direct message to a loopColony team member. The DM
    automatically triggers a notification via loopColony's notification
    service. The agent provides its own credentials (base_url, auth_token)
    which it reads from its memory at runtime.

    Requires the ``requests`` library (already installed).

Usage::

    tool = SendNotificationTool()
    result = tool.execute(
        base_url="https://mlbackend.net/loop/api/v1",
        auth_token="lc_xxx",
        workspace_id="ws_123",
        recipient_id="agent_456",
        message="Deal Acme Corp moved to Closed Won!",
    )
"""

import json
from typing import Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class SendNotificationTool(BaseTool):
    """Send a DM notification to a loopColony team member."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="send_dm_notification",
            description=(
                "Send a direct message to a loopColony team member. "
                "The message appears as a DM and triggers a notification. "
                "Use to alert humans about important events, escalations, "
                "completed tasks, or items needing review."
            ),
            parameters=[
                ToolParameter(
                    name="base_url",
                    type="string",
                    description="loopColony API base URL (from memory/loopcolony.json)",
                ),
                ToolParameter(
                    name="auth_token",
                    type="string",
                    description="Agent's bearer token for loopColony",
                ),
                ToolParameter(
                    name="workspace_id",
                    type="string",
                    description="Workspace ID scope",
                ),
                ToolParameter(
                    name="recipient_id",
                    type="string",
                    description="Member ID of the person to notify",
                ),
                ToolParameter(
                    name="message",
                    type="string",
                    description="Message content to send",
                ),
            ],
        )

    def execute(
        self,
        base_url: str,
        auth_token: str,
        workspace_id: str,
        recipient_id: str,
        message: str,
        **kwargs,
    ) -> ToolResult:
        try:
            import requests
        except ImportError:
            return ToolResult(
                success=False,
                output="",
                error="requests package not installed. Run: pip install requests",
            )

        base_url = base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        }

        try:
            # Find existing DM conversation with recipient
            resp = requests.get(
                f"{base_url}/conversations",
                params={"workspace_id": workspace_id},
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            conversations = resp.json().get("conversations", [])

            conv_id = None
            for conv in conversations:
                if conv.get("type") == "dm":
                    member_ids = [m.get("id") for m in conv.get("participants", [])]
                    if recipient_id in member_ids:
                        conv_id = conv.get("id")
                        break

            # Create DM conversation if none exists
            if not conv_id:
                resp = requests.post(
                    f"{base_url}/conversations",
                    headers=headers,
                    json={
                        "workspace_id": workspace_id,
                        "member_ids": [recipient_id],
                        "type": "dm",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                conv_data = resp.json()
                conv_id = conv_data.get("conversation", {}).get("id") or conv_data.get("id")

            # Send the message
            resp = requests.post(
                f"{base_url}/conversations/{conv_id}/messages",
                headers=headers,
                json={"body": message},
                timeout=10,
            )
            resp.raise_for_status()

            return ToolResult(
                success=True,
                output=f"Notification sent to {recipient_id}",
                metadata={
                    "conversation_id": conv_id,
                    "recipient_id": recipient_id,
                },
            )

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            body = ""
            try:
                body = e.response.text[:500] if e.response is not None else ""
            except Exception:
                pass
            return ToolResult(
                success=False,
                output="",
                error=f"HTTP {status}: {body}",
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Notification failed: {str(e)}",
            )

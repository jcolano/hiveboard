"""
EVENT_TOOLS
===========

Tool that lets agents queue follow-up work they detect during execution.

When an agent discovers actionable work (e.g., an @mention to research, a
request to follow up on), it calls ``queue_followup_event`` to queue that work as
a persistent event. The event runs in an isolated session so it doesn't
pollute the current conversation.

Approval Logic
--------------
- Events created during **human chat** (source="human") get status
  ``pending_approval`` — the human sees approval buttons in the admin UI.
- Events created from **any other source** (heartbeat, task, webhook) get
  status ``active`` — they go straight into the agent's queue.

The tool itself only collects events in memory. After ``loop.execute()``
finishes, ``agent.run()`` harvests them via ``collect_events()`` and the
runtime processes them.
"""

import uuid
from typing import Dict, List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class CreateEventTool(BaseTool):
    """Lets the agent queue follow-up work it detects."""

    def __init__(self, agent_id: str):
        self.agent_id = agent_id
        self._created_events: List[dict] = []
        self._current_source: str = "unknown"

    def set_execution_context(self, source: str):
        """Called by agent.run() before each execution."""
        self._current_source = source
        self._created_events.clear()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="queue_followup_event",
            description=(
                "Queue a follow-up task or action you've detected. "
                "Use this when you find work that should be done separately "
                "(e.g., research a detected @mention, respond to a request, "
                "follow up on a conversation). Include full context so the "
                "event can execute independently in its own session."
            ),
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Short title for the event (what needs to be done)",
                    required=True,
                ),
                ToolParameter(
                    name="message",
                    type="string",
                    description=(
                        "Full instructions for the follow-up work. Be specific "
                        "and include all context needed for independent execution."
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="context",
                    type="object",
                    description=(
                        "Context snapshot: credentials, IDs, URLs needed for "
                        "execution (e.g., post_id, reply_to, base_url, auth_token)"
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="skill_id",
                    type="string",
                    description="Skill to load when executing this event",
                    required=False,
                ),
                ToolParameter(
                    name="priority",
                    type="string",
                    description="Priority: high, normal, or low",
                    required=False,
                    default="normal",
                    enum=["high", "normal", "low"],
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        title = kwargs.get("title", "")
        message = kwargs.get("message", "")
        context = kwargs.get("context")
        skill_id = kwargs.get("skill_id")
        priority = kwargs.get("priority", "normal")

        if not title:
            return ToolResult(success=False, output="", error="Title is required")
        if not message:
            return ToolResult(success=False, output="", error="Message is required")

        if priority not in ("high", "normal", "low"):
            priority = "normal"

        status = "pending_approval" if self._current_source == "human" else "active"

        event = {
            "event_id": f"evt_{uuid.uuid4().hex[:12]}",
            "title": title,
            "message": message,
            "context": context,
            "skill_id": skill_id,
            "priority": priority,
            "status": status,
            "created_by": "agent",
        }
        self._created_events.append(event)

        status_msg = "queued for approval" if status == "pending_approval" else "queued"
        return ToolResult(
            success=True,
            output=f"Event '{title}' {status_msg}.",
        )

    def collect_events(self) -> List[dict]:
        """Harvest created events and clear the buffer."""
        events = list(self._created_events)
        self._created_events.clear()
        return events

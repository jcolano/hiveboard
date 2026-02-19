"""
DELEGATION_TOOLS
================

Tools that let an agent delegate a task to another agent or a human.

Follows the collect/harvest pattern from ``event_tools.py``:
the tool itself does NOT write to disk or queue events. It prepares
suspension data and a delegation event that ``agent.run()`` harvests
after the loop exits.

Depth Rule
----------
If this agent is already running a delegated task (``_is_delegate=True``),
the tool returns an error -- max delegation depth is 1.

Classes
-------
- ``DelegationBase`` -- shared state and getters for both delegation tools
- ``DelegateToAgentTool`` -- delegate to another AI agent (existing)
- ``DelegateToHumanTool`` -- delegate to a human team member (new)
"""

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from ..forms import generate_form_schema

logger = logging.getLogger(__name__)


class DelegationBase(BaseTool):
    """Shared state and helpers for delegation tools."""

    def __init__(self, agent_id: str):
        self._agent_id = agent_id
        self._suspension_data: Optional[dict] = None
        self._delegation_event: Optional[dict] = None
        self._is_delegate: bool = False

    def get_suspension_data(self) -> Optional[dict]:
        """Return suspension data prepared by execute(), or None."""
        return self._suspension_data

    def get_delegation_event(self) -> Optional[dict]:
        """Return delegation event prepared by execute(), or None."""
        return self._delegation_event

    def reset(self) -> None:
        """Clear collected data (called between runs)."""
        self._suspension_data = None
        self._delegation_event = None

    def _check_depth(self) -> Optional[ToolResult]:
        """Return error ToolResult if depth limit reached, else None."""
        if self._is_delegate:
            return ToolResult(
                success=False, output="",
                error="Cannot delegate: max depth reached. You are already "
                      "running a delegated task and cannot delegate further.",
            )
        return None


class DelegateToAgentTool(DelegationBase):
    """Lets an agent delegate a task to another agent."""

    def __init__(self, agent_id: str, config_manager):
        super().__init__(agent_id)
        self._config_manager = config_manager

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="delegate_to_agent",
            description=(
                "Assign a task to another agent. The task is queued and "
                "your current plan is suspended until the delegate responds. "
                "Use when a task requires specialized expertise from a "
                "different agent. The delegate cannot delegate further."
            ),
            parameters=[
                ToolParameter(
                    name="agent_id",
                    type="string",
                    description=(
                        "ID of the agent to delegate to (from Your Team section)"
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="task",
                    type="string",
                    description=(
                        "Clear, self-contained description of what the agent "
                        "should do. Include all necessary context -- the "
                        "delegate has no access to your plan or prior conversation."
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="context",
                    type="string",
                    description=(
                        "Relevant data the delegate needs (e.g., deal details, "
                        "email draft, ticket content). Appended as a Context section."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="deadline_minutes",
                    type="integer",
                    description=(
                        "How long to wait before considering the delegation "
                        "timed out. Default: 30 minutes."
                    ),
                    required=False,
                    default=30,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        agent_id = kwargs.get("agent_id", "")
        task = kwargs.get("task", "")
        context = kwargs.get("context", "")
        deadline_minutes = int(kwargs.get("deadline_minutes", 30) or 30)

        # Depth check
        depth_err = self._check_depth()
        if depth_err:
            return depth_err

        # Validate inputs
        if not agent_id:
            return ToolResult(success=False, output="", error="agent_id is required")
        if not task:
            return ToolResult(success=False, output="", error="task is required")

        # Cannot delegate to self
        if agent_id == self._agent_id:
            return ToolResult(
                success=False, output="",
                error="Cannot delegate to yourself.",
            )

        # Validate target agent exists and is not deleted
        try:
            target_config = self._config_manager.load_agent(agent_id)
            if getattr(target_config, "is_deleted", False):
                return ToolResult(
                    success=False, output="",
                    error=f"Agent '{agent_id}' is deleted.",
                )
        except Exception:
            return ToolResult(
                success=False, output="",
                error=f"Agent '{agent_id}' not found.",
            )

        # Generate delegation ID
        delegation_id = f"d_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(minutes=deadline_minutes)

        # Build suspension data (NOT written to disk -- agent.run() does that)
        self._suspension_data = {
            "delegation_id": delegation_id,
            "type": "agent",
            "suspended_at": now.isoformat(),
            "deadline": deadline.isoformat(),
            "deadline_minutes": deadline_minutes,
            "waiting_on": agent_id,
            "waiting_on_type": "agent",
            "delegation_task": task[:500],
        }

        # Build delegation event for cross-agent routing
        message_parts = [
            f"DELEGATED TASK from {self._agent_id}:",
            "",
            task,
        ]
        if context:
            message_parts.extend(["", "## Context", context])

        self._delegation_event = {
            "event_id": f"evt_{delegation_id}",
            "target_agent": agent_id,
            "title": f"Delegation from {self._agent_id}",
            "message": "\n".join(message_parts),
            "priority": "high",
            "status": "active",
            "created_by": "delegation",
            "context": {
                "delegation_id": delegation_id,
                "callback_agent": self._agent_id,
                "delegation_depth": 1,
            },
        }

        return ToolResult(
            success=True,
            output=(
                f"Task delegated to '{agent_id}'. Plan suspended. "
                f"Will resume when '{agent_id}' responds (deadline: {deadline_minutes}min)."
            ),
            metadata={"action": "suspend_plan"},
        )


class DelegateToHumanTool(DelegationBase):
    """Lets an agent delegate a task to a human team member.

    The human receives a form (via loopColony, email, or standalone HTML)
    and the agent's plan is suspended until they submit a response or the
    deadline expires.
    """

    def __init__(self, agent_id: str, project_root: Optional[Path] = None):
        super().__init__(agent_id)
        self._project_root = project_root

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="delegate_to_human",
            description=(
                "Delegate a task to a human team member. Your plan is suspended "
                "until the human submits their response. Use when the task requires "
                "physical action, legal authority, subjective judgment, or "
                "external relationships that only a human can handle."
            ),
            parameters=[
                ToolParameter(
                    name="human_id",
                    type="string",
                    description=(
                        "ID of the human to delegate to (from Your Team section)"
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="task",
                    type="string",
                    description=(
                        "Clear description of what the human needs to do. "
                        "Include all necessary context, instructions, and "
                        "what you need back from them."
                    ),
                    required=True,
                ),
                ToolParameter(
                    name="result_format",
                    type="array",
                    description=(
                        "Array of field definitions for the response form. "
                        'Each item: {"field": "name", "type": "text|number|date|textarea|select|checkbox", '
                        '"description": "...", "required": true/false}. '
                        "If omitted, a single free-text Result field is used."
                    ),
                    required=False,
                ),
                ToolParameter(
                    name="deadline_hours",
                    type="number",
                    description=(
                        "Hours to wait before timeout. Default: 24. "
                        "Set based on task complexity and human availability."
                    ),
                    required=False,
                    default=24,
                ),
                ToolParameter(
                    name="channel",
                    type="string",
                    description=(
                        'Notification channel: "loopcolony" (default), '
                        '"email", "standalone", or "webhook".'
                    ),
                    required=False,
                    default="loopcolony",
                ),
                ToolParameter(
                    name="notes",
                    type="string",
                    description=(
                        "Additional context or instructions for the human."
                    ),
                    required=False,
                ),
            ],
        )

    def _load_team_json(self) -> Optional[dict]:
        """Load team.json from the project root."""
        if not self._project_root:
            return None
        # Try loopCore path first, then generic
        for subpath in ("data/loopCore/CONFIG/team.json", "data/CONFIG/team.json"):
            team_path = self._project_root / subpath
            if team_path.exists():
                try:
                    return json.loads(team_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
        return None

    def _find_human(self, human_id: str) -> Optional[dict]:
        """Find a human member in team.json."""
        team_data = self._load_team_json()
        if not team_data:
            return None
        for member in team_data.get("members", []):
            if member.get("id") == human_id and member.get("type") == "human":
                return member
        return None

    def execute(self, **kwargs) -> ToolResult:
        human_id = kwargs.get("human_id", "")
        task = kwargs.get("task", "")
        result_format = kwargs.get("result_format")
        deadline_hours = float(kwargs.get("deadline_hours", 24) or 24)
        channel = kwargs.get("channel", "loopcolony") or "loopcolony"
        notes = kwargs.get("notes", "")

        # Depth check
        depth_err = self._check_depth()
        if depth_err:
            return depth_err

        # Validate inputs
        if not human_id:
            return ToolResult(success=False, output="", error="human_id is required")
        if not task:
            return ToolResult(success=False, output="", error="task is required")

        # Validate human exists in team.json
        human = self._find_human(human_id)
        if not human:
            return ToolResult(
                success=False, output="",
                error=f"Human '{human_id}' not found in team.json or is not type 'human'.",
            )

        # Generate delegation ID
        delegation_id = f"d_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(hours=deadline_hours)

        # Build form schema
        form_schema = generate_form_schema(
            fields_def=result_format,
            title=task[:100],
            description=task,
        )
        form_schema["submit_url"] = f"/delegation/{delegation_id}/complete"

        # Build suspension data
        self._suspension_data = {
            "delegation_id": delegation_id,
            "type": "human",
            "suspended_at": now.isoformat(),
            "deadline": deadline.isoformat(),
            "deadline_hours": deadline_hours,
            "waiting_on": human_id,
            "waiting_on_type": "human",
            "delegation_task": task[:500],
            "form_schema": form_schema,
            "channel": channel,
            "notes": notes,
            "human_name": human.get("name", human_id),
            "reminder_config": {
                "interval_hours": min(deadline_hours / 4, 6),
                "max_reminders": 3,
                "reminders_sent": 0,
                "last_reminder_at": None,
            },
        }

        # Build delegation event (no target_agent -- human delegation has no
        # cross-agent routing, but we store it for consistency)
        self._delegation_event = {
            "event_id": f"evt_{delegation_id}",
            "target_human": human_id,
            "title": f"Delegation to {human.get('name', human_id)}",
            "message": task,
            "priority": "normal",
            "status": "active",
            "created_by": "delegation",
            "context": {
                "delegation_id": delegation_id,
                "callback_agent": self._agent_id,
                "type": "human",
            },
        }

        human_name = human.get("name", human_id)
        return ToolResult(
            success=True,
            output=(
                f"Task delegated to '{human_name}' (human). Plan suspended. "
                f"A form will be sent via {channel}. "
                f"Will resume when '{human_name}' responds (deadline: {deadline_hours}h)."
            ),
            metadata={"action": "suspend_plan"},
        )

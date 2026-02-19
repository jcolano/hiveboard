"""
TASK_TOOLS
==========

Tools for managing scheduled tasks in the Agentic Loop Framework (7 tools).

These tools allow agents to create, manage, and monitor their own scheduled tasks.
**Ownership is enforced** — an agent can only manage tasks where agent_id matches.

Task Storage
------------
Each task lives in ``data/AGENTS/{agent_id}/tasks/{task_id}/`` with:
- ``task.json``: Configuration, schedule, metadata, status
- ``task.md``: Instructions (executed as the agent's prompt when the task runs)
- ``runs/``: Execution history (one folder per run with result.json + transcript)
- ``state.json``: Persistent state between runs (managed by state_tools.py)

Schedule Types
--------------
- ``interval``: Run every N seconds (e.g. every 3600s = hourly)
- ``cron``: Cron expression (e.g. "0 9 * * *" = daily at 9am)
- ``once``: Run at a specific datetime, then disable
- ``event_only``: No automatic schedule, only manual trigger

Task + Skill Integration
-------------------------
Tasks can reference a ``skill_id`` to use a specific skill when executing.
If skill_id is null, the SkillMatcher auto-selects the best skill at runtime
based on the task.md content and available skill triggers.

Task context supports keyword injection (resolved by KeywordResolver):
  ``$CREDENTIALS:loopcolony$`` → actual auth credentials
  ``$POSTS_RECENT[10]$`` → recent loopColony posts
  ``$STATE:last_cursor$`` → value from previous run's state.json

Requires
--------
All task tools require the scheduler to be set via
``agent_manager.set_scheduler(scheduler_instance)`` before they can be registered.

Tools
-----
- ``schedule_create``  — Create a new scheduled task
- ``schedule_list``    — List agent's own tasks (optionally include disabled)
- ``schedule_get``     — Get full task details
- ``schedule_update``  — Update task config or instructions
- ``schedule_delete``  — Permanently delete a task
- ``schedule_trigger`` — Manually trigger immediately (bypasses schedule)
- ``schedule_run_list``    — Get run history (default: last 10 runs)
"""

from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class TaskCreateTool(BaseTool):
    """
    Create a new scheduled task.

    The task will be owned by the agent and stored in their tasks directory.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        """
        Initialize the task create tool.

        Args:
            scheduler: TaskScheduler instance
            agent_id: ID of the agent using this tool (for ownership)
        """
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_create",
            description=(
                "Create a new scheduled task. The task will run automatically based on "
                "the schedule type. Supported schedule types: 'interval' (repeating), "
                "'cron' (cron expression), 'once' (one-time), 'event_only' (manual only)."
            ),
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="Unique identifier for the task (lowercase, no spaces, use underscores)",
                    required=True
                ),
                ToolParameter(
                    name="name",
                    type="string",
                    description="Human-readable name for the task",
                    required=True
                ),
                ToolParameter(
                    name="instructions",
                    type="string",
                    description="Task instructions (what the agent should do when task runs). Markdown format.",
                    required=True
                ),
                ToolParameter(
                    name="schedule_type",
                    type="string",
                    description="Type of schedule: 'interval', 'cron', 'once', or 'event_only'",
                    required=True
                ),
                ToolParameter(
                    name="interval_seconds",
                    type="integer",
                    description="For 'interval' type: seconds between runs (e.g., 1800 for 30 minutes)",
                    required=False
                ),
                ToolParameter(
                    name="cron_expression",
                    type="string",
                    description="For 'cron' type: cron expression (e.g., '0 9 * * 1' for Monday 9am)",
                    required=False
                ),
                ToolParameter(
                    name="run_at",
                    type="string",
                    description="For 'once' type: ISO 8601 datetime when to run (e.g., '2026-02-06T10:00:00Z')",
                    required=False
                ),
                ToolParameter(
                    name="skill_id",
                    type="string",
                    description="Optional skill ID to use when executing (None = auto-match)",
                    required=False
                ),
                ToolParameter(
                    name="enabled",
                    type="boolean",
                    description="Whether task is enabled (default: true)",
                    required=False
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Optional longer description of the task's purpose",
                    required=False
                ),
                ToolParameter(
                    name="context",
                    type="object",
                    description="Optional context passed to task execution (e.g., {\"workspace_id\": \"ws_xxx\", \"target_topic\": \"general\"})",
                    required=False
                )
            ]
        )

    def execute(
        self,
        task_id: str,
        name: str,
        instructions: str,
        schedule_type: str,
        interval_seconds: int = None,
        cron_expression: str = None,
        run_at: str = None,
        skill_id: str = None,
        enabled: bool = True,
        description: str = "",
        context: dict = None
    ) -> ToolResult:
        """Create a new scheduled task."""

        # Validate schedule_type
        valid_types = ["interval", "cron", "once", "event_only"]
        if schedule_type not in valid_types:
            return ToolResult(
                success=False,
                output="",
                error=f"Invalid schedule_type '{schedule_type}'. Must be one of: {valid_types}"
            )

        # Validate required schedule parameters
        if schedule_type == "interval" and not interval_seconds:
            return ToolResult(
                success=False,
                output="",
                error="interval_seconds is required for 'interval' schedule type"
            )

        if schedule_type == "cron" and not cron_expression:
            return ToolResult(
                success=False,
                output="",
                error="cron_expression is required for 'cron' schedule type"
            )

        if schedule_type == "once" and not run_at:
            return ToolResult(
                success=False,
                output="",
                error="run_at is required for 'once' schedule type"
            )

        # Validate task_id format
        if not task_id or not task_id.replace("_", "").replace("-", "").isalnum():
            return ToolResult(
                success=False,
                output="",
                error="task_id must be alphanumeric with underscores/hyphens only"
            )

        # Build schedule config
        schedule = {"type": schedule_type}
        if interval_seconds:
            schedule["interval_seconds"] = interval_seconds
        if cron_expression:
            schedule["expression"] = cron_expression
        if run_at:
            schedule["run_at"] = run_at

        try:
            task = self.scheduler.create_task(
                task_id=task_id,
                name=name,
                task_md_content=instructions,
                schedule=schedule,
                agent_id=self.agent_id,
                skill_id=skill_id,
                enabled=enabled,
                description=description,
                context=context or {},
                created_by=f"agent:{self.agent_id}"
            )

            return ToolResult(
                success=True,
                output=(
                    f"Task '{name}' created successfully.\n"
                    f"  ID: {task.task_id}\n"
                    f"  Schedule: {schedule_type}\n"
                    f"  Next run: {task.next_run.isoformat() if task.next_run else 'Not scheduled'}\n"
                    f"  Enabled: {task.enabled}"
                ),
                metadata={
                    "task_id": task.task_id,
                    "name": task.name,
                    "schedule_type": schedule_type,
                    "next_run": task.next_run.isoformat() if task.next_run else None,
                    "enabled": task.enabled
                }
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to create task: {str(e)}"
            )


class TaskListTool(BaseTool):
    """
    List tasks owned by the agent.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_list",
            description="List all scheduled tasks owned by this agent.",
            parameters=[
                ToolParameter(
                    name="include_disabled",
                    type="boolean",
                    description="Include disabled tasks (default: true)",
                    required=False
                )
            ]
        )

    def execute(self, include_disabled: bool = True) -> ToolResult:
        """List tasks owned by this agent."""
        try:
            # Get tasks filtered by agent_id
            tasks = self.scheduler.list_tasks(agent_id=self.agent_id)

            # Optionally filter out disabled
            if not include_disabled:
                tasks = [t for t in tasks if t.get("enabled", True)]

            if not tasks:
                return ToolResult(
                    success=True,
                    output="No tasks found.",
                    metadata={"tasks": [], "count": 0}
                )

            # Format output
            lines = [f"Found {len(tasks)} task(s):\n"]
            for t in tasks:
                status = "✓" if t.get("enabled") else "○"
                next_run = t.get("next_run", "Not scheduled")
                lines.append(
                    f"  {status} {t['task_id']}: {t['name']}\n"
                    f"      Schedule: {t.get('schedule_type', 'unknown')}, "
                    f"Next: {next_run}, Runs: {t.get('run_count', 0)}"
                )

            return ToolResult(
                success=True,
                output="\n".join(lines),
                metadata={"tasks": tasks, "count": len(tasks)}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to list tasks: {str(e)}"
            )


class TaskGetTool(BaseTool):
    """
    Get detailed information about a specific task.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_get",
            description="Get detailed information about a specific task, including its instructions.",
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="ID of the task to retrieve",
                    required=True
                )
            ]
        )

    def execute(self, task_id: str) -> ToolResult:
        """Get task details."""
        try:
            task = self.scheduler.get_task(task_id)

            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task not found: {task_id}"
                )

            # Verify ownership
            task_agent_id = task.get("execution", {}).get("agent_id", "")
            if task_agent_id != self.agent_id:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: task belongs to agent '{task_agent_id}'"
                )

            # Format output
            schedule = task.get("schedule", {})
            status = task.get("status", {})
            execution = task.get("execution", {})

            output = (
                f"Task: {task.get('name', task_id)}\n"
                f"ID: {task_id}\n"
                f"Description: {task.get('description', 'N/A')}\n"
                f"\n"
                f"Schedule:\n"
                f"  Type: {schedule.get('type', 'unknown')}\n"
            )

            if schedule.get("interval_seconds"):
                output += f"  Interval: {schedule['interval_seconds']} seconds\n"
            if schedule.get("expression"):
                output += f"  Cron: {schedule['expression']}\n"
            if schedule.get("run_at"):
                output += f"  Run at: {schedule['run_at']}\n"

            output += (
                f"\n"
                f"Status:\n"
                f"  Enabled: {status.get('enabled', True)}\n"
                f"  Last run: {status.get('last_run', 'Never')}\n"
                f"  Next run: {status.get('next_run', 'Not scheduled')}\n"
                f"  Run count: {status.get('run_count', 0)}\n"
            )

            if execution.get("skill_id"):
                output += f"\nSkill: {execution['skill_id']}\n"

            # Include instructions
            instructions = task.get("task_md_content", "")
            if instructions:
                output += f"\nInstructions:\n{instructions}\n"

            return ToolResult(
                success=True,
                output=output,
                metadata={"task": task}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to get task: {str(e)}"
            )


class TaskUpdateTool(BaseTool):
    """
    Update an existing task's configuration or instructions.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_update",
            description=(
                "Update a task's configuration or instructions. "
                "Only specify the fields you want to change."
            ),
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="ID of the task to update",
                    required=True
                ),
                ToolParameter(
                    name="name",
                    type="string",
                    description="New name for the task",
                    required=False
                ),
                ToolParameter(
                    name="instructions",
                    type="string",
                    description="New instructions (task.md content)",
                    required=False
                ),
                ToolParameter(
                    name="enabled",
                    type="boolean",
                    description="Enable or disable the task",
                    required=False
                ),
                ToolParameter(
                    name="interval_seconds",
                    type="integer",
                    description="New interval in seconds (for interval schedules)",
                    required=False
                ),
                ToolParameter(
                    name="cron_expression",
                    type="string",
                    description="New cron expression (for cron schedules)",
                    required=False
                ),
                ToolParameter(
                    name="skill_id",
                    type="string",
                    description="New skill ID to use (or empty string to clear)",
                    required=False
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="New description",
                    required=False
                )
            ]
        )

    def execute(
        self,
        task_id: str,
        name: str = None,
        instructions: str = None,
        enabled: bool = None,
        interval_seconds: int = None,
        cron_expression: str = None,
        skill_id: str = None,
        description: str = None
    ) -> ToolResult:
        """Update a task."""
        try:
            # First verify ownership
            task = self.scheduler.get_task(task_id)
            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task not found: {task_id}"
                )

            task_agent_id = task.get("execution", {}).get("agent_id", "")
            if task_agent_id != self.agent_id:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: task belongs to agent '{task_agent_id}'"
                )

            # Build updates dict
            updates = {}

            if name is not None:
                updates["name"] = name

            if description is not None:
                updates["description"] = description

            if enabled is not None:
                updates.setdefault("status", {})["enabled"] = enabled

            if interval_seconds is not None:
                updates.setdefault("schedule", {})["interval_seconds"] = interval_seconds

            if cron_expression is not None:
                updates.setdefault("schedule", {})["expression"] = cron_expression

            if skill_id is not None:
                updates.setdefault("execution", {})["skill_id"] = skill_id if skill_id else None

            # Apply config updates
            changes_made = []
            if updates:
                success = self.scheduler.update_task(task_id, updates)
                if not success:
                    return ToolResult(
                        success=False,
                        output="",
                        error="Failed to update task configuration"
                    )
                changes_made.append("configuration")

            # Update instructions separately if provided
            if instructions is not None:
                success = self.scheduler.update_task_md(task_id, instructions)
                if not success:
                    return ToolResult(
                        success=False,
                        output="",
                        error="Failed to update task instructions"
                    )
                changes_made.append("instructions")

            if not changes_made:
                return ToolResult(
                    success=True,
                    output="No changes specified.",
                    metadata={"task_id": task_id, "changes": []}
                )

            return ToolResult(
                success=True,
                output=f"Task '{task_id}' updated successfully. Changed: {', '.join(changes_made)}",
                metadata={"task_id": task_id, "changes": changes_made}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to update task: {str(e)}"
            )


class TaskDeleteTool(BaseTool):
    """
    Delete a scheduled task.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_delete",
            description="Delete a scheduled task permanently. This cannot be undone.",
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="ID of the task to delete",
                    required=True
                )
            ]
        )

    def execute(self, task_id: str) -> ToolResult:
        """Delete a task."""
        try:
            # First verify ownership
            task = self.scheduler.get_task(task_id)
            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task not found: {task_id}"
                )

            task_agent_id = task.get("execution", {}).get("agent_id", "")
            if task_agent_id != self.agent_id:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: task belongs to agent '{task_agent_id}'"
                )

            task_name = task.get("name", task_id)
            success = self.scheduler.delete_task(task_id)

            if success:
                return ToolResult(
                    success=True,
                    output=f"Task '{task_name}' ({task_id}) deleted successfully.",
                    metadata={"task_id": task_id, "deleted": True}
                )
            else:
                return ToolResult(
                    success=False,
                    output="",
                    error="Failed to delete task"
                )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to delete task: {str(e)}"
            )


class TaskTriggerTool(BaseTool):
    """
    Manually trigger a task to run immediately.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_trigger",
            description=(
                "Manually trigger a task to run immediately, regardless of its schedule. "
                "The task must have allow_manual=true (default)."
            ),
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="ID of the task to trigger",
                    required=True
                )
            ]
        )

    def execute(self, task_id: str) -> ToolResult:
        """Trigger a task immediately."""
        try:
            # First verify ownership
            task = self.scheduler.get_task(task_id)
            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task not found: {task_id}"
                )

            task_agent_id = task.get("execution", {}).get("agent_id", "")
            if task_agent_id != self.agent_id:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: task belongs to agent '{task_agent_id}'"
                )

            task_name = task.get("name", task_id)
            result = self.scheduler.trigger_task(task_id)

            if result.get("status") == "error":
                return ToolResult(
                    success=False,
                    output="",
                    error=result.get("error", "Unknown error")
                )

            return ToolResult(
                success=True,
                output=(
                    f"Task '{task_name}' triggered successfully.\n"
                    f"Status: {result.get('status', 'unknown')}\n"
                    f"Turns: {result.get('turns', 0)}"
                ),
                metadata={
                    "task_id": task_id,
                    "result": result
                }
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to trigger task: {str(e)}"
            )


class TaskRunsTool(BaseTool):
    """
    Get the run history for a task.
    """

    def __init__(self, scheduler: Any, agent_id: str):
        self.scheduler = scheduler
        self.agent_id = agent_id

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_run_list",
            description="Get the run history for a task, showing recent executions and their results.",
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="ID of the task",
                    required=True
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Maximum number of runs to return (default: 10)",
                    required=False
                )
            ]
        )

    def execute(self, task_id: str, limit: int = 10) -> ToolResult:
        """Get task run history."""
        try:
            # First verify ownership
            task = self.scheduler.get_task(task_id)
            if not task:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Task not found: {task_id}"
                )

            task_agent_id = task.get("execution", {}).get("agent_id", "")
            if task_agent_id != self.agent_id:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Access denied: task belongs to agent '{task_agent_id}'"
                )

            runs = self.scheduler.get_task_runs(task_id, limit=limit)

            if not runs:
                return ToolResult(
                    success=True,
                    output=f"No runs found for task '{task_id}'.",
                    metadata={"runs": [], "count": 0}
                )

            # Format output
            lines = [f"Last {len(runs)} run(s) for '{task_id}':\n"]
            for run in runs:
                status = run.get("result", {}).get("status", "unknown")
                started = run.get("started_at", "unknown")
                duration = run.get("duration_ms", 0)
                trigger = run.get("trigger", {}).get("type", "unknown")

                status_icon = "✓" if status == "completed" else "✗"
                lines.append(
                    f"  {status_icon} {started}\n"
                    f"      Status: {status}, Duration: {duration}ms, Trigger: {trigger}"
                )

                if status == "error":
                    error = run.get("result", {}).get("error", "")
                    if error:
                        lines.append(f"      Error: {error[:100]}...")

            return ToolResult(
                success=True,
                output="\n".join(lines),
                metadata={"runs": runs, "count": len(runs)}
            )
        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to get task runs: {str(e)}"
            )

"""
TASK_MANAGEMENT_TOOL
====================

Tool for agents to create and manage scheduled tasks.

Allows agents to:
- List all tasks
- Create new tasks with schedules
- Update task configuration and content
- Enable/disable tasks
- Manually trigger tasks
- View task run history
"""

import json
from typing import Optional, List

from ..tools.base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class TaskManagementTool(BaseTool):
    """
    Tool for agents to manage scheduled tasks.

    Actions:
    - list: List all tasks
    - get: Get task details
    - create: Create a new task
    - update: Update task settings
    - update_content: Update task.md content
    - delete: Delete a task
    - enable: Enable a task
    - disable: Disable a task
    - trigger: Manually run a task
    - runs: Get task run history
    """

    def __init__(self, scheduler: 'TaskScheduler'):
        """
        Initialize the tool.

        Args:
            scheduler: TaskScheduler instance
        """
        self.scheduler = scheduler

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="task_manager",
            description="""Manage scheduled tasks. Actions:
- list: List all tasks
- get: Get task details
- create: Create a new task
- update: Update task settings
- update_content: Update task.md content
- delete: Delete a task
- enable: Enable a task
- disable: Disable a task
- trigger: Manually run a task
- runs: Get task run history""",
            parameters=[
                ToolParameter(
                    name="action",
                    type="string",
                    description="Action to perform",
                    required=True,
                    enum=["list", "get", "create", "update", "update_content",
                          "delete", "enable", "disable", "trigger", "runs"]
                ),
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="Task ID (required for most actions)",
                    required=False
                ),
                ToolParameter(
                    name="name",
                    type="string",
                    description="Task name (for create)",
                    required=False
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="task.md content (for create/update_content)",
                    required=False
                ),
                ToolParameter(
                    name="schedule",
                    type="object",
                    description="Schedule config: {type, interval_seconds, expression, etc.}",
                    required=False
                ),
                ToolParameter(
                    name="agent_id",
                    type="string",
                    description="Agent ID to execute the task (for create)",
                    required=False
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Task description (for create)",
                    required=False
                ),
                ToolParameter(
                    name="updates",
                    type="object",
                    description="Fields to update (for update action)",
                    required=False
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Max items to return (for runs)",
                    required=False
                ),
                ToolParameter(
                    name="on_event",
                    type="array",
                    description="Events that trigger this task (for create)",
                    required=False
                )
            ]
        )

    def execute(self, action: str, **kwargs) -> ToolResult:
        """Execute task management action."""
        try:
            if action == "list":
                return self._list_tasks()

            elif action == "get":
                return self._get_task(kwargs.get("task_id"))

            elif action == "create":
                return self._create_task(
                    task_id=kwargs.get("task_id"),
                    name=kwargs.get("name"),
                    content=kwargs.get("content", ""),
                    schedule=kwargs.get("schedule"),
                    agent_id=kwargs.get("agent_id", "default"),
                    description=kwargs.get("description", ""),
                    on_event=kwargs.get("on_event", [])
                )

            elif action == "update":
                return self._update_task(
                    task_id=kwargs.get("task_id"),
                    updates=kwargs.get("updates", {})
                )

            elif action == "update_content":
                return self._update_content(
                    task_id=kwargs.get("task_id"),
                    content=kwargs.get("content", "")
                )

            elif action == "delete":
                return self._delete_task(kwargs.get("task_id"))

            elif action == "enable":
                return self._enable_task(kwargs.get("task_id"))

            elif action == "disable":
                return self._disable_task(kwargs.get("task_id"))

            elif action == "trigger":
                return self._trigger_task(kwargs.get("task_id"))

            elif action == "runs":
                return self._get_runs(
                    task_id=kwargs.get("task_id"),
                    limit=kwargs.get("limit", 10)
                )

            else:
                return ToolResult(
                    success=False,
                    output="",
                    error=f"Unknown action: {action}"
                )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=str(e)
            )

    def _list_tasks(self) -> ToolResult:
        """List all tasks."""
        tasks = self.scheduler.list_tasks()
        return ToolResult(
            success=True,
            output=json.dumps(tasks, indent=2)
        )

    def _get_task(self, task_id: Optional[str]) -> ToolResult:
        """Get task details."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        task = self.scheduler.get_task(task_id)
        if not task:
            return ToolResult(success=False, output="", error="Task not found")

        return ToolResult(
            success=True,
            output=json.dumps(task, indent=2)
        )

    def _create_task(
        self,
        task_id: Optional[str],
        name: Optional[str],
        content: str,
        schedule: Optional[dict],
        agent_id: str,
        description: str,
        on_event: List[str]
    ) -> ToolResult:
        """Create a new task."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        if not schedule:
            schedule = {"type": "interval", "interval_seconds": 3600}

        # Validate schedule type
        if schedule.get("type") not in ["interval", "cron", "once", "event_only"]:
            return ToolResult(
                success=False,
                output="",
                error="Invalid schedule type. Use: interval, cron, once, or event_only"
            )

        task = self.scheduler.create_task(
            task_id=task_id,
            name=name or task_id,
            task_md_content=content,
            schedule=schedule,
            agent_id=agent_id,
            description=description,
            on_event=on_event
        )

        if task:
            return ToolResult(
                success=True,
                output=f"Created task: {task.task_id}\nNext run: {task.next_run.isoformat() if task.next_run else 'N/A'}",
                metadata={"task_id": task.task_id}
            )
        else:
            return ToolResult(
                success=False,
                output="",
                error="Failed to create task"
            )

    def _update_task(self, task_id: Optional[str], updates: dict) -> ToolResult:
        """Update task configuration."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        success = self.scheduler.update_task(task_id, updates)
        return ToolResult(
            success=success,
            output="Task updated" if success else "",
            error=None if success else "Update failed - task not found"
        )

    def _update_content(self, task_id: Optional[str], content: str) -> ToolResult:
        """Update task.md content."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        success = self.scheduler.update_task_md(task_id, content)
        return ToolResult(
            success=success,
            output="task.md updated" if success else "",
            error=None if success else "Update failed - task not found"
        )

    def _delete_task(self, task_id: Optional[str]) -> ToolResult:
        """Delete a task."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        success = self.scheduler.delete_task(task_id)
        return ToolResult(
            success=success,
            output="Task deleted" if success else "",
            error=None if success else "Delete failed - task not found"
        )

    def _enable_task(self, task_id: Optional[str]) -> ToolResult:
        """Enable a task."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        success = self.scheduler.enable_task(task_id)
        return ToolResult(
            success=success,
            output="Task enabled" if success else "",
            error=None if success else "Enable failed - task not found"
        )

    def _disable_task(self, task_id: Optional[str]) -> ToolResult:
        """Disable a task."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        success = self.scheduler.disable_task(task_id)
        return ToolResult(
            success=success,
            output="Task disabled" if success else "",
            error=None if success else "Disable failed - task not found"
        )

    def _trigger_task(self, task_id: Optional[str]) -> ToolResult:
        """Manually trigger a task."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        result = self.scheduler.trigger_task(task_id)

        if result.get("status") == "error":
            return ToolResult(
                success=False,
                output="",
                error=result.get("error", "Trigger failed")
            )

        return ToolResult(
            success=True,
            output=json.dumps(result, indent=2)
        )

    def _get_runs(self, task_id: Optional[str], limit: int) -> ToolResult:
        """Get task run history."""
        if not task_id:
            return ToolResult(success=False, output="", error="task_id required")

        runs = self.scheduler.get_task_runs(task_id, limit=limit)
        return ToolResult(
            success=True,
            output=json.dumps(runs, indent=2)
        )

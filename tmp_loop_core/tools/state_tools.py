"""
STATE_TOOLS
===========

Tools for managing task state persistence between runs (2 tools).

Tasks often need to remember things across executions — a cursor position, a
run counter, the last post ID processed, etc. These tools read/write a
``state.json`` file in the task's directory.

Storage
-------
State is stored at ``data/AGENTS/{agent_id}/tasks/{task_id}/state.json``.
The ``agent_dir`` is passed at init time; ``task_id`` is passed at execute time
(the agent knows its task_id from the skill prompt context).

How State Connects to Keywords
-------------------------------
State values are also accessible via the keyword system. In a task's context:
  ``$STATE:last_cursor$`` → reads state.json["last_cursor"]
This means state saved in one run is automatically available in the next run's
prompt context without the agent needing to call schedule_state_get explicitly.

Tools
-----
- ``schedule_state_set``: Write state dict to state.json. By default merges with
  existing state (set ``replace=true`` to overwrite entirely). Auto-adds
  ``_updated_at`` timestamp.
- ``schedule_state_get``: Read state from state.json. Can retrieve a specific key
  or the entire state dict. Returns empty dict if no state exists.

Usage::

    save_tool = SaveTaskStateTool(agent_dir="./data/AGENTS/main")
    save_tool.execute(task_id="daily_check", state={"last_cursor": "abc123"})

    get_tool = GetTaskStateTool(agent_dir="./data/AGENTS/main")
    get_tool.execute(task_id="daily_check", key="last_cursor")  # → "abc123"
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult


class SaveTaskStateTool(BaseTool):
    """
    Save state to the task's state.json file.

    State persists between task runs and is available in the next run's context.
    """

    def __init__(self, agent_dir: str):
        """
        Initialize the save state tool.

        Args:
            agent_dir: Path to the agent directory (e.g. data/AGENTS/editor).
                      State is stored at agent_dir/tasks/{task_id}/state.json.
        """
        self.agent_dir = Path(agent_dir)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_state_set",
            description=(
                "Save state that persists between task runs. "
                "Use this to store information needed in the next execution, "
                "such as cursors, timestamps, or tracking data. "
                "State is merged with existing state (new keys added, existing keys updated)."
            ),
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="The task ID to save state for (from your task context).",
                    required=True
                ),
                ToolParameter(
                    name="state",
                    type="object",
                    description=(
                        "State to save. This will be merged with existing state. "
                        "Example: {\"last_run_at\": \"2026-02-05T20:30:00Z\", \"cursor\": \"abc123\"}"
                    ),
                    required=True
                ),
                ToolParameter(
                    name="replace",
                    type="boolean",
                    description="If true, replace all state instead of merging. Default: false",
                    required=False,
                    default=False
                )
            ]
        )

    def execute(self, task_id: str, state: Dict[str, Any], replace: bool = False) -> ToolResult:
        """
        Save state to state.json.

        Args:
            task_id: The task ID to save state for
            state: Dictionary of state to save
            replace: If True, replace entire state. If False, merge with existing.

        Returns:
            ToolResult indicating success or failure
        """
        try:
            state_path = self.agent_dir / "tasks" / task_id / "state.json"
            # Task dir may not exist yet on first run
            state_path.parent.mkdir(parents=True, exist_ok=True)

            # Load existing state if merging
            existing_state = {}
            if not replace and state_path.exists():
                try:
                    existing_state = json.loads(state_path.read_text(encoding='utf-8'))
                except:  # Corrupt JSON — start fresh rather than blocking the agent
                    existing_state = {}

            # Merge or replace
            if replace:
                new_state = state
            else:
                new_state = {**existing_state, **state}

            # Add metadata
            new_state["_updated_at"] = datetime.now(timezone.utc).isoformat()

            # Write state
            state_path.write_text(
                json.dumps(new_state, indent=2, default=str),
                encoding='utf-8'
            )

            return ToolResult(
                success=True,
                output=f"State saved successfully. Keys: {list(new_state.keys())}",
                error=None
            )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to save state: {str(e)}"
            )


class GetTaskStateTool(BaseTool):
    """
    Read state from the task's state.json file.

    Useful if the agent needs to check current state during execution.
    """

    def __init__(self, agent_dir: str):
        """
        Initialize the get state tool.

        Args:
            agent_dir: Path to the agent directory (e.g. data/AGENTS/editor).
                      State is read from agent_dir/tasks/{task_id}/state.json.
        """
        self.agent_dir = Path(agent_dir)

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="schedule_state_get",
            description=(
                "Read the current task state. "
                "Returns the state saved from previous runs."
            ),
            parameters=[
                ToolParameter(
                    name="task_id",
                    type="string",
                    description="The task ID to read state for (from your task context).",
                    required=True
                ),
                ToolParameter(
                    name="key",
                    type="string",
                    description="Specific key to retrieve. If not provided, returns entire state.",
                    required=False
                )
            ]
        )

    def execute(self, task_id: str, key: str = None) -> ToolResult:
        """
        Read state from state.json.

        Args:
            task_id: The task ID to read state for
            key: Optional specific key to retrieve

        Returns:
            ToolResult with state data or error
        """
        try:
            state_path = self.agent_dir / "tasks" / task_id / "state.json"

            # First run — no prior state is normal, not an error
            if not state_path.exists():
                return ToolResult(
                    success=True,
                    output=json.dumps({}),
                    error=None
                )

            state = json.loads(state_path.read_text(encoding='utf-8'))

            if key:
                value = state.get(key)
                return ToolResult(
                    success=True,
                    output=json.dumps(value, default=str) if value is not None else "null",
                    error=None
                )
            else:
                return ToolResult(
                    success=True,
                    output=json.dumps(state, indent=2, default=str),
                    error=None
                )

        except Exception as e:
            return ToolResult(
                success=False,
                output="",
                error=f"Failed to read state: {str(e)}"
            )

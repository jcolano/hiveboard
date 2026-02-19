"""
TODO_TOOLS
==========

Persistent TO_DO queue for agents (4 tools).

Every agent has a personal ``todo.json`` in its data directory. The agent
adds items when it discovers work to do, and marks them done when complete.

The agentic loop injects pending items into the agent's context before each
execution so the agent never loses track of outstanding work.

Storage
-------
``data/AGENTS/{agent_id}/todo.json`` — list of items:
  [{"id": "td_001", "task": "...", "status": "pending", "created_at": "...", "completed_at": null}, ...]

Tools
-----
- ``todo_add``      — Add a new item to the TO_DO list
- ``todo_list``     — List all items (optionally filter by status)
- ``todo_complete`` — Mark an item as done
- ``todo_remove``   — Remove an item from the list
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .base import BaseTool, ToolDefinition, ToolParameter, ToolResult
from ..observability import get_hiveloop_agent


def _todo_path(agent_dir: str) -> Path:
    return Path(agent_dir) / "todo.json"


def _load_todos(agent_dir: str) -> list:
    path = _todo_path(agent_dir)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_todos(agent_dir: str, todos: list) -> None:
    path = _todo_path(agent_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(todos, indent=2), encoding="utf-8")


def _next_id(todos: list) -> str:
    max_n = 0
    for t in todos:
        tid = t.get("id", "")
        if tid.startswith("td_"):
            try:
                max_n = max(max_n, int(tid[3:]))
            except ValueError:
                pass
    return f"td_{max_n + 1:03d}"


def get_pending_todos_prompt(agent_dir: str) -> str:
    """Build a prompt section listing pending TO_DO items for context injection."""
    todos = _load_todos(agent_dir)
    pending = [t for t in todos if t.get("status") == "pending"]
    if not pending:
        return ""

    lines = ["[PENDING TO-DO ITEMS -- review and act on these]"]
    for t in pending:
        priority = t.get("priority", "normal")
        marker = "!!! " if priority == "high" else ""
        lines.append(f"  - [{t['id']}] {marker}{t['task']}")
        if t.get("context"):
            lines.append(f"    context: {t['context']}")
    lines.append(f"Total: {len(pending)} pending item(s).")
    lines.append(
        "IMPORTANT: For each item you work on, VERIFY the result of every tool call. "
        "If a tool call fails (error, missing permissions, missing account, etc.), "
        "do NOT silently move on. Instead: (1) understand WHY it failed, "
        "(2) try to fix the root cause, (3) if you cannot fix it, ESCALATE to the "
        "human operator using feed_post or send_dm_notification explaining what failed "
        "and what is needed. Mark items done with todo_complete only after verifying success."
    )
    return "\n".join(lines)


class TodoAddTool(BaseTool):
    """Add a new item to the agent's personal TO_DO list."""

    def __init__(self, agent_dir: str):
        self._agent_dir = agent_dir

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="todo_add",
            description=(
                "Add a task to your personal TO_DO list. Use this whenever you "
                "discover work that needs doing — even if you plan to do it right "
                "now. This ensures nothing gets lost across sessions."
            ),
            parameters=[
                ToolParameter(
                    name="task",
                    type="string",
                    description="Short description of what needs to be done",
                ),
                ToolParameter(
                    name="priority",
                    type="string",
                    description="Priority level",
                    required=False,
                    enum=["high", "normal", "low"],
                    default="normal",
                ),
                ToolParameter(
                    name="context",
                    type="string",
                    description="Additional context (e.g., who requested it, relevant IDs)",
                    required=False,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        task_text = kwargs.get("task", "").strip()
        if not task_text:
            return ToolResult(success=False, output="", error="task is required")

        priority = kwargs.get("priority", "normal")
        context = kwargs.get("context", "")

        todos = _load_todos(self._agent_dir)
        item = {
            "id": _next_id(todos),
            "task": task_text,
            "status": "pending",
            "priority": priority,
            "context": context,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
        }
        todos.append(item)
        _save_todos(self._agent_dir, todos)

        # HiveLoop: report TODO created
        _hl_agent = get_hiveloop_agent()
        if _hl_agent:
            try:
                _hl_agent.todo(
                    todo_id=item["id"],
                    action="created",
                    summary=task_text,
                    priority=priority,
                    source="tool",
                    context=context[:200] if context else "",
                )
            except Exception:
                pass

        pending_count = sum(1 for t in todos if t["status"] == "pending")
        return ToolResult(
            success=True,
            output=f"Added [{item['id']}] to TO_DO list. {pending_count} item(s) pending.",
        )


class TodoListTool(BaseTool):
    """List items from the agent's TO_DO list."""

    def __init__(self, agent_dir: str):
        self._agent_dir = agent_dir

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="todo_list",
            description="List items from your personal TO_DO list. Shows pending items by default.",
            parameters=[
                ToolParameter(
                    name="status",
                    type="string",
                    description="Filter by status (default: pending)",
                    required=False,
                    enum=["pending", "completed", "all"],
                    default="pending",
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        status_filter = kwargs.get("status", "pending")
        todos = _load_todos(self._agent_dir)

        if status_filter != "all":
            filtered = [t for t in todos if t.get("status") == status_filter]
        else:
            filtered = todos

        if not filtered:
            return ToolResult(success=True, output=f"No {status_filter} items in TO_DO list.")

        lines = []
        for t in filtered:
            mark = "[x]" if t["status"] == "completed" else "[ ]"
            pri = f" ({t['priority']})" if t.get("priority", "normal") != "normal" else ""
            lines.append(f"{mark} [{t['id']}]{pri} {t['task']}")
            if t.get("context"):
                lines.append(f"    context: {t['context']}")

        pending_count = sum(1 for t in todos if t["status"] == "pending")
        completed_count = sum(1 for t in todos if t["status"] == "completed")
        lines.append(f"\nSummary: {pending_count} pending, {completed_count} completed")
        return ToolResult(success=True, output="\n".join(lines))


class TodoCompleteTool(BaseTool):
    """Mark a TO_DO item as completed."""

    def __init__(self, agent_dir: str):
        self._agent_dir = agent_dir

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="todo_complete",
            description=(
                "Mark a TO_DO item as completed. Use this AFTER you have verified "
                "the task was actually done successfully (e.g., API returned success, "
                "data was created). Do NOT mark complete if the action failed."
            ),
            parameters=[
                ToolParameter(
                    name="todo_id",
                    type="string",
                    description="The ID of the item to complete (e.g., td_001)",
                ),
                ToolParameter(
                    name="result",
                    type="string",
                    description="Brief note on the outcome (e.g., 'Sent 5 emails successfully')",
                    required=False,
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        todo_id = kwargs.get("todo_id", "").strip()
        if not todo_id:
            return ToolResult(success=False, output="", error="todo_id is required")

        result_note = kwargs.get("result", "")
        todos = _load_todos(self._agent_dir)

        for t in todos:
            if t["id"] == todo_id:
                if t["status"] == "completed":
                    return ToolResult(success=True, output=f"[{todo_id}] is already completed.")
                t["status"] = "completed"
                t["completed_at"] = datetime.now(timezone.utc).isoformat()
                if result_note:
                    t["result"] = result_note
                _save_todos(self._agent_dir, todos)

                # HiveLoop: report TODO completed
                _hl_agent = get_hiveloop_agent()
                if _hl_agent:
                    try:
                        _hl_agent.todo(
                            todo_id=todo_id,
                            action="completed",
                            summary=result_note or t["task"],
                        )
                    except Exception:
                        pass

                pending_count = sum(1 for t2 in todos if t2["status"] == "pending")
                return ToolResult(
                    success=True,
                    output=f"Completed [{todo_id}]. {pending_count} item(s) still pending.",
                )

        return ToolResult(success=False, output="", error=f"Item {todo_id} not found")


class TodoRemoveTool(BaseTool):
    """Remove an item from the TO_DO list entirely."""

    def __init__(self, agent_dir: str):
        self._agent_dir = agent_dir

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="todo_remove",
            description="Remove a TO_DO item entirely (e.g., no longer relevant).",
            parameters=[
                ToolParameter(
                    name="todo_id",
                    type="string",
                    description="The ID of the item to remove (e.g., td_001)",
                ),
            ],
        )

    def execute(self, **kwargs) -> ToolResult:
        todo_id = kwargs.get("todo_id", "").strip()
        if not todo_id:
            return ToolResult(success=False, output="", error="todo_id is required")

        todos = _load_todos(self._agent_dir)
        before = len(todos)
        todos = [t for t in todos if t["id"] != todo_id]
        if len(todos) == before:
            return ToolResult(success=False, output="", error=f"Item {todo_id} not found")

        _save_todos(self._agent_dir, todos)

        # HiveLoop: report TODO dismissed
        _hl_agent = get_hiveloop_agent()
        if _hl_agent:
            try:
                _hl_agent.todo(
                    todo_id=todo_id,
                    action="dismissed",
                    summary="Removed by agent",
                )
            except Exception:
                pass

        pending_count = sum(1 for t in todos if t["status"] == "pending")
        return ToolResult(
            success=True,
            output=f"Removed [{todo_id}]. {pending_count} item(s) still pending.",
        )

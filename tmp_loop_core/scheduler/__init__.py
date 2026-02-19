"""
SCHEDULER MODULE
=================

Task scheduling for the Agentic Loop Framework.

Allows agents to create scheduled tasks that execute at specified times/intervals.
Each task has a task.md file containing instructions for execution.

Features:
- Interval schedules (every N seconds)
- Cron schedules (standard cron expressions)
- One-shot schedules (run once at specific time)
- Event-triggered tasks
- Run history tracking
- Skill linking (explicit or auto-matched)
- Agent-controlled task management via tool
"""

from .scheduler import (
    TaskScheduler,
    TaskSchedule,
    ScheduledTask,
    TaskRunResult,
    create_task_executor,
)

from .keyword_resolver import (
    KeywordResolver,
    KeywordResolutionError,
    format_resolved_context,
)

from .tool import TaskManagementTool

__all__ = [
    'TaskScheduler',
    'TaskSchedule',
    'ScheduledTask',
    'TaskRunResult',
    'TaskManagementTool',
    'create_task_executor',
    'KeywordResolver',
    'KeywordResolutionError',
    'format_resolved_context',
]

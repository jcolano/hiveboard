"""
OBSERVABILITY
=============

HiveLoop integration plumbing for loopCore.

Provides contextvars-based access to the current HiveLoop task object
anywhere in the call stack, without threading it through every function
signature.

Usage::

    from loop_core.observability import get_current_task

    task = get_current_task()
    if task:
        task.llm_call("phase1", model="claude-sonnet-4", tokens_in=500, tokens_out=100)
"""

import contextvars
from typing import Any, Dict, Optional

# Current HiveLoop task for this execution context.
# Set by agent_manager.run_agent(), read anywhere deeper in the stack.
_current_task: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "hiveloop_task", default=None
)

# Current HiveLoop agent handle for this execution context.
# Set by agent_manager.run_agent(), used for agent-level methods
# (track_context, report_issue, queue_snapshot, etc.)
_current_hiveloop_agent: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "hiveloop_agent", default=None
)


def set_current_task(task: Any) -> None:
    """Set the HiveLoop task for the current execution context."""
    _current_task.set(task)


def get_current_task() -> Optional[Any]:
    """Get the current HiveLoop task, or None if not in a tracked context."""
    return _current_task.get()


def clear_current_task() -> None:
    """Clear the current HiveLoop task."""
    _current_task.set(None)


def set_hiveloop_agent(agent: Any) -> None:
    """Set the HiveLoop agent handle for the current execution context."""
    _current_hiveloop_agent.set(agent)


def get_hiveloop_agent() -> Optional[Any]:
    """Get the current HiveLoop agent handle, or None if not initialized."""
    return _current_hiveloop_agent.get()


def clear_hiveloop_agent() -> None:
    """Clear the current HiveLoop agent handle."""
    _current_hiveloop_agent.set(None)


# ============================================================================
# COST ESTIMATION
# ============================================================================

# USD per 1M tokens (as of Feb 2026 — update when pricing changes)
COST_PER_MILLION: Dict[str, Dict[str, float]] = {
    # Anthropic
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
}


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> Optional[float]:
    """Estimate USD cost for an LLM call. Returns None if model not in table."""
    rates = COST_PER_MILLION.get(model)
    if not rates:
        return None
    return (tokens_in * rates["input"] / 1_000_000) + (tokens_out * rates["output"] / 1_000_000)

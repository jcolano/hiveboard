"""
DELEGATION
==========

Pure persistence helpers for agent-to-agent and agent-to-human delegation.

When agent A delegates a task to agent B (or human H), A's plan is
suspended and a ``suspended_delegation.json`` file is written to A's
agent directory. When B/H finishes (or times out), the file is read to
build a resume message, then deleted.

No classes -- just four functions.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_FILENAME = "suspended_delegation.json"


def save_suspended_delegation(agent_dir: str, data: dict) -> None:
    """Write suspended delegation state to disk."""
    path = Path(agent_dir) / _FILENAME
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Saved suspended delegation '%s' to %s", data.get("delegation_id", "?"), path)


def load_suspended_delegation(agent_dir: str) -> Optional[dict]:
    """Read suspended delegation state, or None if missing/corrupt."""
    path = Path(agent_dir) / _FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load suspended delegation from %s: %s", path, e)
        return None


def delete_suspended_delegation(agent_dir: str) -> None:
    """Remove the suspended delegation file if it exists."""
    path = Path(agent_dir) / _FILENAME
    try:
        path.unlink(missing_ok=True)
        logger.info("Deleted suspended delegation file: %s", path)
    except OSError as e:
        logger.warning("Failed to delete %s: %s", path, e)


def build_resume_message(delegation_result: dict, suspended: dict) -> str:
    """Build the context message injected when the delegating agent resumes.

    Args:
        delegation_result: Dict with keys: status, result, error, from_agent
        suspended: The suspended delegation data (may be empty for auto-callback)

    Returns:
        Formatted message string for the resuming agent's LLM.
    """
    status = delegation_result.get("status", "unknown")
    from_agent = delegation_result.get("from_agent", "unknown")
    result_text = delegation_result.get("result", "")
    error_text = delegation_result.get("error", "")

    lines = ["Resume suspended plan."]

    if status == "completed":
        lines.append(f"Delegation to '{from_agent}' COMPLETED.")
        if result_text:
            lines.append(f"Result: {result_text[:3000]}")
    elif status == "timeout":
        deadline_hours = suspended.get("deadline_hours")
        deadline_minutes = suspended.get("deadline_minutes")
        if deadline_hours:
            lines.append(f"Delegation to '{from_agent}' TIMED OUT.")
            lines.append(f"No response received after {deadline_hours} hours.")
        else:
            lines.append(f"Delegation to '{from_agent}' TIMED OUT.")
            lines.append(f"No response received after {deadline_minutes or '?'} minutes.")
    else:
        lines.append(f"Delegation to '{from_agent}' FAILED (status={status}).")
        if error_text:
            lines.append(f"Error: {error_text[:1000]}")
        if result_text:
            lines.append(f"Partial output: {result_text[:2000]}")

    # Include remaining plan steps if available
    if suspended:
        plan = suspended.get("plan")
        if plan and plan.get("steps"):
            current_idx = plan.get("current_step_index", 0)
            remaining = [
                s.get("description", "?")
                for s in plan["steps"][current_idx:]
                if s.get("status") != "completed"
            ]
            if remaining:
                lines.append(f"Remaining steps: {remaining}")

    return "\n".join(lines)

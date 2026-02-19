"""
OUTPUT_MANAGER
==============

Run output storage for the Agentic Loop Framework.

Saves a result.json and transcript.md for every agent execution, organized
by agent and date. This is the **audit trail** — a record of every LLM call,
tool invocation, and response.

Directory Structure
-------------------
::

    data/AGENTS/{agent_id}/runs/
    └── {YYYY-MM-DD}/
        ├── run_001/
        │   ├── result.json       # Structured: status, tokens, tools, conversation
        │   └── transcript.md     # Human-readable formatted version
        ├── run_002/
        └── ...

What result.json Contains
--------------------------
- ``run_id``, ``agent_id``, ``session_id``, ``timestamp``
- ``status``: completed, error, timeout, max_turns
- ``message``: The user/task input that triggered the run
- ``response``: The agent's final text response
- ``turns``: Number of LLM iterations
- ``tools_called``: List of tools used (e.g. ["http_request", "file_read"])
- ``total_tokens``: LLM token count
- ``duration_ms``: Wall-clock execution time
- ``conversation``: Full message array (role, content, tool_calls)

Typical size: 3-8 KB per run.

Dependency Analysis (what breaks if runs are deleted)
------------------------------------------------------
- **Nothing breaks functionally.** No agent logic depends on runs existing.
- Admin UI loses visible history (``GET /runs``, ``GET /agents/{id}/runs``).
- ``schedule_run_list`` tool returns empty — but that's the task scheduler's own run
  history (stored in ``tasks/{task_id}/runs/``), not this directory.
- Debugging and performance analysis lose their data.

Cleanup Status
--------------
- **Agent runs (this file): Auto-cleanup after each save.** Keeps the last 50
  runs per agent (``OutputManager.MAX_RUNS_PER_AGENT``). Oldest runs deleted
  first. Empty date folders are removed.
- Task runs (scheduler.py ``_save_run_history``): Auto-purges to last 50.
"""

import json
import shutil
import logging
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class RunOutput:
    """
    Output from a single agent run.

    Immutable after creation — runs are never updated. One RunOutput is created
    per agent execution and saved as result.json + transcript.md.

    No agent logic depends on RunOutput existing. It's purely for audit,
    debugging, and the admin UI's run history view.
    """
    run_id: str
    agent_id: str
    session_id: str
    timestamp: str
    status: str
    message: str
    response: Optional[str]
    turns: int
    tools_called: List[str]
    total_tokens: int
    duration_ms: int
    error: Optional[str] = None
    conversation: List[Dict[str, Any]] = field(default_factory=list)
    execution_trace: List[Dict] = field(default_factory=list)
    plan: Optional[Dict] = None
    reflections: List[Dict] = field(default_factory=list)
    turn_details: List[Dict] = field(default_factory=list)
    step_stats: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        d = {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "message": self.message,
            "response": self.response,
            "turns": self.turns,
            "tools_called": self.tools_called,
            "total_tokens": self.total_tokens,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "conversation": self.conversation,
        }
        if self.execution_trace:
            d["execution_trace"] = self.execution_trace
        if self.plan:
            d["plan"] = self.plan
        if self.reflections:
            d["reflections"] = self.reflections
        if self.turn_details:
            d["turn_details"] = self.turn_details
        if self.step_stats:
            d["step_stats"] = self.step_stats
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RunOutput':
        """Create from dictionary."""
        return cls(
            run_id=data["run_id"],
            agent_id=data.get("agent_id", "unknown"),
            session_id=data.get("session_id", ""),
            timestamp=data.get("timestamp", ""),
            status=data.get("status", "unknown"),
            message=data.get("message", ""),
            response=data.get("response"),
            turns=data.get("turns", 0),
            tools_called=data.get("tools_called", []),
            total_tokens=data.get("total_tokens", 0),
            duration_ms=data.get("duration_ms", 0),
            error=data.get("error"),
            conversation=data.get("conversation", []),
            execution_trace=data.get("execution_trace", []),
            plan=data.get("plan"),
            reflections=data.get("reflections", []),
            turn_details=data.get("turn_details", []),
            step_stats=data.get("step_stats", []),
        )

    def to_transcript(self) -> str:
        """Generate a markdown transcript of the run."""
        lines = [
            f"# Agent Run Transcript",
            "",
            f"**Run ID:** {self.run_id}",
            f"**Agent:** {self.agent_id}",
            f"**Session:** {self.session_id or 'N/A'}",
            f"**Timestamp:** {self.timestamp}",
            f"**Status:** {self.status}",
            f"**Duration:** {self.duration_ms}ms",
            f"**Tokens Used:** {self.total_tokens}",
            "",
            "---",
            "",
            "## User Message",
            "",
            self.message,
            "",
        ]

        if self.conversation:
            lines.extend([
                "---",
                "",
                "## Conversation",
                ""
            ])
            for msg in self.conversation:
                role = msg.get("role", "unknown").title()
                content = msg.get("content", "")
                lines.append(f"### {role}")
                lines.append("")
                lines.append(content)
                lines.append("")

                # Include tool calls if present
                if "tool_calls" in msg:
                    lines.append("**Tool Calls:**")
                    for tc in msg["tool_calls"]:
                        lines.append(f"- `{tc.get('name', 'unknown')}`")
                    lines.append("")

        if self.response:
            lines.extend([
                "---",
                "",
                "## Final Response",
                "",
                self.response,
                ""
            ])

        if self.tools_called:
            lines.extend([
                "---",
                "",
                "## Tools Used",
                ""
            ])
            for tool in self.tools_called:
                lines.append(f"- {tool}")
            lines.append("")

        if self.error:
            lines.extend([
                "---",
                "",
                "## Error",
                "",
                f"```",
                self.error,
                f"```",
                ""
            ])

        return "\n".join(lines)


# ============================================================================
# OUTPUT MANAGER
# ============================================================================

class OutputManager:
    """Manage agent run outputs."""

    MAX_RUNS_PER_AGENT = 50

    def __init__(self, output_dir: str = None, agent_dir: Path = None, agent_id: str = None):
        """
        Initialize output manager.

        New per-agent structure (preferred):
            agent_dir: Path to the agent's directory (e.g., data/AGENTS/main/)
                       Runs stored in: agent_dir/runs/{date}/run_###/

        Legacy support:
            output_dir: Base directory for outputs
                       Runs stored in: output_dir/{agent_id}/{date}/run_###/

        Args:
            output_dir: Legacy base directory for outputs
            agent_dir: New per-agent directory path (takes precedence)
            agent_id: Agent ID (used for legacy mode tracking)
        """
        self.agent_id = agent_id
        self._run_counters: Dict[str, int] = {}  # date -> counter

        # New per-agent directory structure takes precedence
        if agent_dir is not None:
            self.agent_dir = Path(agent_dir)
            self.runs_dir = self.agent_dir / "runs"
            self.output_dir = None  # Not used in new mode
            self._use_legacy = False
        elif output_dir is not None:
            # Legacy mode
            self.output_dir = Path(output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.runs_dir = None  # Not used in legacy mode
            self.agent_dir = None
            self._use_legacy = True
        else:
            raise ValueError("Either agent_dir or output_dir must be provided")

        # Create runs directory if using new structure
        if self.runs_dir:
            self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _get_date_dir(self, date: str = None) -> Path:
        """Get the directory for outputs on a specific date."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if self._use_legacy:
            # Legacy: output_dir/{agent_id}/{date}
            if not self.agent_id:
                raise ValueError("agent_id required for legacy output mode")
            date_dir = self.output_dir / self.agent_id / date
        else:
            # New: runs_dir/{date}
            date_dir = self.runs_dir / date

        date_dir.mkdir(parents=True, exist_ok=True)
        return date_dir

    def _get_agent_dir(self, agent_id: str, date: str = None) -> Path:
        """
        Get the directory for an agent's outputs on a specific date.

        Legacy method for backward compatibility.
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if self._use_legacy:
            agent_dir = self.output_dir / agent_id / date
        else:
            # In new structure, all runs are for this agent
            agent_dir = self.runs_dir / date

        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir

    def _get_next_run_number(self, agent_id: str = None, date: str = None) -> int:
        """Get the next run number for a date."""
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        key = f"{agent_id or self.agent_id}:{date}"
        if key in self._run_counters:
            self._run_counters[key] += 1
            return self._run_counters[key]

        # Count existing runs
        if self._use_legacy:
            if not agent_id:
                agent_id = self.agent_id
            run_dir = self.output_dir / agent_id / date if agent_id else self.output_dir / date
        else:
            run_dir = self.runs_dir / date

        if run_dir.exists():
            existing_runs = list(run_dir.glob("run_*"))
            next_num = len(existing_runs) + 1
        else:
            next_num = 1

        self._run_counters[key] = next_num
        return next_num

    def save_run(
        self,
        agent_id: str,
        session_id: str,
        message: str,
        response: Optional[str],
        status: str,
        turns: int,
        tools_called: List[str],
        total_tokens: int,
        duration_ms: int,
        error: Optional[str] = None,
        conversation: List[Dict] = None,
        loop_result_data: Optional[Dict] = None
    ) -> RunOutput:
        """
        Save an agent run output (result.json + transcript.md).

        Called by agent.py at the end of every execution. Creates an immutable
        record — runs are never updated after creation.

        Files are written to: ``{runs_dir}/{YYYY-MM-DD}/run_{NNN}/``
        Run numbering is sequential per day (run_001, run_002, etc.).

        After saving, runs beyond MAX_RUNS_PER_AGENT (50) are automatically
        deleted, oldest first.

        Args:
            agent_id: Agent ID
            session_id: Session ID
            message: User message
            response: Agent response
            status: Run status (completed, error, timeout, max_turns)
            turns: Number of LLM iterations
            tools_called: List of tools used
            total_tokens: Total tokens used
            duration_ms: Duration in milliseconds
            error: Error message if any
            conversation: Full conversation history

        Returns:
            RunOutput object
        """
        date = datetime.now().strftime("%Y-%m-%d")
        run_num = self._get_next_run_number(agent_id, date)
        run_id = f"run_{run_num:03d}"
        timestamp = datetime.now().isoformat()

        # Create run output
        lrd = loop_result_data or {}
        run_output = RunOutput(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            timestamp=timestamp,
            status=status,
            message=message,
            response=response,
            turns=turns,
            tools_called=tools_called,
            total_tokens=total_tokens,
            duration_ms=duration_ms,
            error=error,
            conversation=conversation or [],
            execution_trace=lrd.get("execution_trace", []),
            plan=lrd.get("plan"),
            reflections=lrd.get("reflections", []),
            turn_details=lrd.get("turn_details", []),
            step_stats=lrd.get("step_stats", []),
        )

        # Create run directory
        if self._use_legacy:
            run_dir = self._get_agent_dir(agent_id, date) / run_id
        else:
            run_dir = self._get_date_dir(date) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Save result.json
        result_path = run_dir / "result.json"
        result_path.write_text(
            json.dumps(run_output.to_dict(), indent=2),
            encoding='utf-8'
        )

        # Save transcript.md
        transcript_path = run_dir / "transcript.md"
        transcript_path.write_text(
            run_output.to_transcript(),
            encoding='utf-8'
        )

        # Save journal.jsonl (flight recorder — one JSON object per line)
        journal_entries = (lrd or {}).get("journal", [])
        if journal_entries:
            journal_path = run_dir / "journal.jsonl"
            lines = [json.dumps(entry, default=str) for entry in journal_entries]
            journal_path.write_text(
                "\n".join(lines) + "\n",
                encoding='utf-8'
            )

        # Cleanup old runs (keep last MAX_RUNS_PER_AGENT)
        self._cleanup_old_runs()

        return run_output

    def _cleanup_old_runs(self) -> int:
        """Delete oldest runs beyond MAX_RUNS_PER_AGENT.

        Collects all run directories across all date folders, sorts by date
        then run number, and removes the oldest ones exceeding the limit.
        Empty date folders are removed afterward.

        Returns:
            Number of runs deleted.
        """
        if self._use_legacy or not self.runs_dir or not self.runs_dir.exists():
            return 0

        # Collect all (date_str, run_num, run_dir_path) tuples
        all_runs = []
        for date_dir in self.runs_dir.iterdir():
            if not date_dir.is_dir():
                continue
            for run_dir in date_dir.iterdir():
                if run_dir.is_dir() and run_dir.name.startswith("run_"):
                    try:
                        run_num = int(run_dir.name.split("_")[1])
                    except (IndexError, ValueError):
                        run_num = 0
                    all_runs.append((date_dir.name, run_num, run_dir))

        if len(all_runs) <= self.MAX_RUNS_PER_AGENT:
            return 0

        # Sort oldest first: by date ascending, then run number ascending
        all_runs.sort(key=lambda x: (x[0], x[1]))

        to_delete = len(all_runs) - self.MAX_RUNS_PER_AGENT
        deleted = 0
        for _, _, run_dir in all_runs[:to_delete]:
            try:
                shutil.rmtree(run_dir)
                deleted += 1
            except OSError as e:
                logger.warning("Failed to delete old run %s: %s", run_dir, e)

        # Remove empty date directories
        for date_dir in self.runs_dir.iterdir():
            if date_dir.is_dir() and not any(date_dir.iterdir()):
                try:
                    date_dir.rmdir()
                except OSError:
                    pass

        if deleted:
            logger.info("Cleaned up %d old runs (kept %d)", deleted, self.MAX_RUNS_PER_AGENT)

        return deleted

    def load_run(self, agent_id: str, date: str, run_id: str) -> Optional[RunOutput]:
        """
        Load a run output.

        Args:
            agent_id: Agent ID (used in legacy mode, ignored in new mode)
            date: Date (YYYY-MM-DD)
            run_id: Run ID (e.g., "run_001")

        Returns:
            RunOutput or None
        """
        if self._use_legacy:
            result_path = self.output_dir / agent_id / date / run_id / "result.json"
        else:
            result_path = self.runs_dir / date / run_id / "result.json"

        if not result_path.exists():
            return None

        try:
            data = json.loads(result_path.read_text(encoding='utf-8'))
            return RunOutput.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def list_runs(
        self,
        agent_id: str = None,
        date: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List runs with optional filtering.

        Args:
            agent_id: Filter by agent ID (legacy mode only)
            date: Filter by date (YYYY-MM-DD)
            limit: Maximum runs to return

        Returns:
            List of run summaries
        """
        runs = []

        if self._use_legacy:
            # Legacy mode: scan output_dir/{agent_id}/{date}/run_*/
            if agent_id:
                agent_dirs = [self.output_dir / agent_id]
            else:
                agent_dirs = [d for d in self.output_dir.iterdir() if d.is_dir()]

            for agent_dir in agent_dirs:
                if not agent_dir.exists():
                    continue

                if date:
                    date_dirs = [agent_dir / date]
                else:
                    date_dirs = sorted(
                        [d for d in agent_dir.iterdir() if d.is_dir()],
                        reverse=True
                    )

                for date_dir in date_dirs:
                    if not date_dir.exists():
                        continue

                    run_dirs = sorted(
                        [d for d in date_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
                        reverse=True
                    )

                    for run_dir in run_dirs:
                        result_path = run_dir / "result.json"
                        if result_path.exists():
                            try:
                                data = json.loads(result_path.read_text(encoding='utf-8'))
                                runs.append({
                                    "run_id": data.get("run_id"),
                                    "agent_id": data.get("agent_id"),
                                    "session_id": data.get("session_id"),
                                    "timestamp": data.get("timestamp"),
                                    "status": data.get("status"),
                                    "turns": data.get("turns"),
                                    "duration_ms": data.get("duration_ms"),
                                    "date": date_dir.name,
                                    "path": str(run_dir)
                                })
                            except (json.JSONDecodeError, KeyError):
                                continue

                        if len(runs) >= limit:
                            break
                    if len(runs) >= limit:
                        break
                if len(runs) >= limit:
                    break
        else:
            # New mode: scan runs_dir/{date}/run_*/
            if date:
                date_dirs = [self.runs_dir / date] if (self.runs_dir / date).exists() else []
            else:
                date_dirs = sorted(
                    [d for d in self.runs_dir.iterdir() if d.is_dir()],
                    reverse=True
                ) if self.runs_dir.exists() else []

            for date_dir in date_dirs:
                if not date_dir.exists():
                    continue

                run_dirs = sorted(
                    [d for d in date_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
                    reverse=True
                )

                for run_dir in run_dirs:
                    result_path = run_dir / "result.json"
                    if result_path.exists():
                        try:
                            data = json.loads(result_path.read_text(encoding='utf-8'))
                            # Filter by agent_id if provided
                            if agent_id and data.get("agent_id") != agent_id:
                                continue
                            runs.append({
                                "run_id": data.get("run_id"),
                                "agent_id": data.get("agent_id") or self.agent_id,
                                "session_id": data.get("session_id"),
                                "timestamp": data.get("timestamp"),
                                "status": data.get("status"),
                                "turns": data.get("turns"),
                                "duration_ms": data.get("duration_ms"),
                                "date": date_dir.name,
                                "path": str(run_dir)
                            })
                        except (json.JSONDecodeError, KeyError):
                            continue

                    if len(runs) >= limit:
                        break
                if len(runs) >= limit:
                    break

        return runs[:limit]

    def get_transcript(self, agent_id: str, date: str, run_id: str) -> Optional[str]:
        """
        Get the transcript for a run.

        Args:
            agent_id: Agent ID (used in legacy mode, ignored in new mode)
            date: Date (YYYY-MM-DD)
            run_id: Run ID

        Returns:
            Transcript markdown or None
        """
        if self._use_legacy:
            transcript_path = self.output_dir / agent_id / date / run_id / "transcript.md"
        else:
            transcript_path = self.runs_dir / date / run_id / "transcript.md"

        if transcript_path.exists():
            return transcript_path.read_text(encoding='utf-8')
        return None

    def delete_run(self, agent_id: str, date: str, run_id: str) -> bool:
        """
        Delete a run and its outputs.

        Args:
            agent_id: Agent ID (used in legacy mode, ignored in new mode)
            date: Date (YYYY-MM-DD)
            run_id: Run ID

        Returns:
            True if deleted, False if not found
        """
        if self._use_legacy:
            run_dir = self.output_dir / agent_id / date / run_id
        else:
            run_dir = self.runs_dir / date / run_id

        if run_dir.exists():
            shutil.rmtree(run_dir)
            return True
        return False

    def get_agent_stats(self, agent_id: str = None) -> Dict[str, Any]:
        """
        Get statistics for runs.

        Args:
            agent_id: Agent ID (required for legacy mode, optional for new mode)

        Returns:
            Statistics dictionary
        """
        effective_agent_id = agent_id or self.agent_id

        if self._use_legacy:
            if not effective_agent_id:
                return {
                    "agent_id": None,
                    "total_runs": 0,
                    "dates": [],
                    "status_counts": {}
                }
            base_dir = self.output_dir / effective_agent_id
        else:
            base_dir = self.runs_dir

        if not base_dir or not base_dir.exists():
            return {
                "agent_id": effective_agent_id,
                "total_runs": 0,
                "dates": [],
                "status_counts": {}
            }

        total_runs = 0
        dates = []
        status_counts: Dict[str, int] = {}

        for date_dir in base_dir.iterdir():
            if date_dir.is_dir():
                dates.append(date_dir.name)
                for run_dir in date_dir.iterdir():
                    if run_dir.is_dir() and run_dir.name.startswith("run_"):
                        total_runs += 1
                        result_path = run_dir / "result.json"
                        if result_path.exists():
                            try:
                                data = json.loads(result_path.read_text(encoding='utf-8'))
                                status = data.get("status", "unknown")
                                status_counts[status] = status_counts.get(status, 0) + 1
                            except (json.JSONDecodeError, KeyError):
                                pass

        return {
            "agent_id": effective_agent_id,
            "total_runs": total_runs,
            "dates": sorted(dates, reverse=True),
            "status_counts": status_counts
        }

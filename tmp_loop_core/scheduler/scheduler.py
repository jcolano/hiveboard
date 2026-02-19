"""
TASK_SCHEDULER
==============

Core task scheduler engine for the Agentic Loop Framework.

The scheduler:
- Loads tasks from per-agent directories: data/AGENTS/{agent_id}/tasks/
- Calculates next run times
- Fires tasks when due
- Handles event-based triggers
- Manages task lifecycle
- Supports skill linking (explicit or auto-matched)

Usage:
    from loop_core.scheduler import TaskScheduler, create_task_executor

    executor = create_task_executor(agent_manager)
    scheduler = TaskScheduler(
        agents_dir="./data/AGENTS",  # Scans all agents/{id}/tasks/
        executor=executor
    )
    scheduler.start()

Skill Integration:
    Tasks can optionally specify a skill_id in task.json:
    - If skill_id is set: That skill is used for execution
    - If skill_id is null: SkillMatcher auto-selects best skill at runtime
    - Skill content is injected into the execution prompt
"""

import threading
import time
import json
import shutil
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

from .keyword_resolver import KeywordResolver, KeywordResolutionError, format_resolved_context


# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class TaskSchedule:
    """Parsed schedule information."""
    type: str  # "interval", "cron", "once", "event_only"
    next_run: Optional[datetime] = None
    interval_seconds: Optional[int] = None
    cron_expression: Optional[str] = None
    timezone: Optional[str] = "UTC"
    events: List[str] = field(default_factory=list)
    run_at: Optional[datetime] = None  # For one-shot schedules


@dataclass
class ScheduledTask:
    """A task registered with the scheduler."""
    task_id: str
    name: str
    folder_path: Path
    schedule: TaskSchedule
    agent_id: str
    enabled: bool
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    run_count: int = 0
    skill_id: Optional[str] = None  # Explicit skill link (None = auto-match at runtime)
    context: dict = field(default_factory=dict)  # Task-specific context (workspace_id, etc.)


@dataclass
class TaskRunResult:
    """Result of a task execution."""
    run_id: str
    task_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    trigger_type: str
    status: str  # "completed", "error", "timeout"
    response: Optional[str]
    turns: int = 0
    tools_used: List[str] = field(default_factory=list)
    tokens_used: int = 0
    error: Optional[str] = None


# ============================================================================
# TASK SCHEDULER
# ============================================================================

class TaskScheduler:
    """
    Background scheduler that monitors and executes tasks.

    Responsibilities:
    - Load tasks from per-agent AGENTS/{id}/tasks/ directories
    - Calculate next run times
    - Fire tasks when due
    - Handle event-based triggers
    - Manage task lifecycle
    """

    def __init__(
        self,
        tasks_dir: str = None,
        agents_dir: str = None,
        executor: Callable[[str, str, str], dict] = None,  # (task_id, task_md_content, agent_id) -> result
        check_interval: float = 1.0
    ):
        """
        Initialize the scheduler.

        New per-agent structure (preferred):
            agents_dir: Path to AGENTS directory, scans all agents/{id}/tasks/

        Legacy support:
            tasks_dir: Directory containing task folders (single directory)

        Args:
            tasks_dir: Legacy directory containing task folders
            agents_dir: New per-agent base directory (data/AGENTS)
            executor: Function to execute tasks (task_id, content, agent_id) -> result
            check_interval: How often to check for due tasks (seconds)
        """
        self.agents_dir = Path(agents_dir) if agents_dir else None
        self.tasks_dir = Path(tasks_dir) if tasks_dir else None
        self.executor = executor
        self.check_interval = check_interval

        # Use new structure if agents_dir provided
        self._use_per_agent = self.agents_dir is not None

        self._tasks: Dict[str, ScheduledTask] = {}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._event_queue: List[dict] = []

        # Heartbeat tracking - store in agents_dir or tasks_dir
        self._started_at: Optional[datetime] = None
        self._last_heartbeat: Optional[datetime] = None
        self._heartbeat_interval: float = 5.0  # seconds

        if self._use_per_agent:
            self._heartbeat_file = self.agents_dir / ".scheduler_heartbeat"
            self._stop_signal_file = self.agents_dir / ".scheduler_stop"
            self.agents_dir.mkdir(parents=True, exist_ok=True)
        elif self.tasks_dir:
            self._heartbeat_file = self.tasks_dir / ".scheduler_heartbeat"
            self._stop_signal_file = self.tasks_dir / ".scheduler_stop"
            self.tasks_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._heartbeat_file = None
            self._stop_signal_file = None

    # ========================================================================
    # LIFECYCLE
    # ========================================================================

    def start(self) -> None:
        """Start the scheduler background thread."""
        if self._running:
            return

        self._running = True
        self._started_at = datetime.now(timezone.utc)
        self._last_heartbeat = self._started_at

        # Clear any existing stop signal
        self._clear_stop_signal()

        self._write_heartbeat_file()
        self._load_all_tasks()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the scheduler (local instance)."""
        self._running = False
        self._remove_heartbeat_file()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None

    def request_stop(self) -> bool:
        """
        Request stop for any scheduler (local or external).

        Writes a stop signal file that running schedulers will detect.
        Returns True if signal was written successfully.
        """
        # Stop local scheduler if running
        if self._running:
            self.stop()
            return True

        # Write stop signal for external scheduler
        if self._stop_signal_file:
            try:
                self._stop_signal_file.write_text(
                    json.dumps({
                        "requested_at": datetime.now(timezone.utc).isoformat(),
                        "requested_by": "api"
                    }),
                    encoding='utf-8'
                )
                return True
            except Exception as e:
                print(f"Failed to write stop signal: {e}")
                return False
        return False

    def _check_stop_signal(self) -> bool:
        """Check if a stop signal has been received."""
        if self._stop_signal_file and self._stop_signal_file.exists():
            return True
        return False

    def _clear_stop_signal(self) -> None:
        """Clear the stop signal file."""
        try:
            if self._stop_signal_file and self._stop_signal_file.exists():
                self._stop_signal_file.unlink()
        except Exception:
            pass

    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._running

    def get_status(self) -> dict:
        """
        Get comprehensive scheduler status.

        Checks both local scheduler state and external heartbeat file
        to detect schedulers running in other processes.

        Returns:
            Dictionary with scheduler health information
        """
        now = datetime.now(timezone.utc)

        # Determine the correct directory for heartbeat (per-agent or legacy)
        heartbeat_dir = self.agents_dir if self._use_per_agent else self.tasks_dir
        heartbeat_dir_str = str(heartbeat_dir) if heartbeat_dir else None

        # Count tasks (always available)
        with self._lock:
            total_tasks = len(self._tasks)
            enabled_tasks = sum(1 for t in self._tasks.values() if t.enabled)
            pending_events = len(self._event_queue)

        # Check if LOCAL scheduler is running
        if self._running:
            # Local scheduler is running - use local state
            uptime_seconds = None
            if self._started_at:
                uptime_seconds = (now - self._started_at).total_seconds()

            heartbeat_ok = False
            heartbeat_age_seconds = None
            if self._last_heartbeat:
                heartbeat_age_seconds = (now - self._last_heartbeat).total_seconds()
                heartbeat_ok = heartbeat_age_seconds < (self.check_interval * 2 + 1)

            if not heartbeat_ok:
                status = "stalled"
            else:
                status = "running"

            return {
                "status": status,
                "running": True,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "uptime_seconds": uptime_seconds,
                "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
                "heartbeat_age_seconds": heartbeat_age_seconds,
                "heartbeat_ok": heartbeat_ok,
                "check_interval": self.check_interval,
                "total_tasks": total_tasks,
                "enabled_tasks": enabled_tasks,
                "pending_events": pending_events,
                "tasks_dir": heartbeat_dir_str,
                "external": False
            }

        # Local scheduler not running - check for EXTERNAL scheduler via heartbeat file
        external_hb = self.read_external_heartbeat(heartbeat_dir_str) if heartbeat_dir_str else None
        if external_hb:
            # External scheduler is running
            started_at = external_hb.get("started_at")
            uptime_seconds = None
            if started_at:
                try:
                    started_dt = datetime.fromisoformat(started_at)
                    uptime_seconds = (now - started_dt).total_seconds()
                except Exception:
                    pass

            return {
                "status": "running",
                "running": True,
                "started_at": started_at,
                "uptime_seconds": uptime_seconds,
                "last_heartbeat": external_hb.get("last_heartbeat"),
                "heartbeat_age_seconds": external_hb.get("heartbeat_age_seconds"),
                "heartbeat_ok": external_hb.get("heartbeat_ok", True),
                "check_interval": external_hb.get("check_interval", self.check_interval),
                "total_tasks": total_tasks,
                "enabled_tasks": enabled_tasks,
                "pending_events": pending_events,
                "tasks_dir": heartbeat_dir_str,
                "external": True,
                "external_pid": external_hb.get("pid")
            }

        # No scheduler running (local or external)
        return {
            "status": "stopped",
            "running": False,
            "started_at": None,
            "uptime_seconds": None,
            "last_heartbeat": None,
            "heartbeat_age_seconds": None,
            "heartbeat_ok": False,
            "check_interval": self.check_interval,
            "total_tasks": total_tasks,
            "enabled_tasks": enabled_tasks,
            "pending_events": pending_events,
            "tasks_dir": heartbeat_dir_str,
            "external": False
        }

    def _run_loop(self) -> None:
        """
        Main scheduler loop.

        IMPORTANT: ``_check_and_execute()`` runs tasks **synchronously** in this
        thread. While a task is executing (which involves full agentic-loop LLM
        calls and can take minutes), the heartbeat is NOT updated. This causes
        ``get_status()`` / ``read_external_heartbeat()`` to report the scheduler
        as "stopped" even though it is alive and working. This is expected — the
        heartbeat is only a *liveness* signal between iterations, not during them.
        """
        # Track when we last reloaded tasks from disk
        last_task_reload = datetime.now(timezone.utc)
        task_reload_interval = 10.0  # Reload tasks every 10 seconds to pick up API-created tasks

        while self._running:
            try:
                # Check for external stop signal
                if self._check_stop_signal():
                    print("[Scheduler] Stop signal received, shutting down...")
                    self._running = False
                    self._clear_stop_signal()
                    self._remove_heartbeat_file()
                    break

                # Update heartbeat
                self._last_heartbeat = datetime.now(timezone.utc)
                self._write_heartbeat_file()

                # Periodically reload tasks from disk to pick up API-created tasks
                now = datetime.now(timezone.utc)
                if (now - last_task_reload).total_seconds() >= task_reload_interval:
                    self._load_all_tasks()
                    last_task_reload = now

                self._check_and_execute()  # Blocking — see docstring above
                self._process_events()
            except Exception as e:
                print(f"Scheduler error: {e}")

            time.sleep(self.check_interval)

    def _write_heartbeat_file(self) -> None:
        """Write heartbeat status to file for cross-process detection."""
        try:
            data = {
                "status": "running",
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "pid": __import__('os').getpid(),
                "check_interval": self.check_interval
            }
            self._heartbeat_file.write_text(json.dumps(data), encoding='utf-8')
        except Exception:
            pass  # Don't fail if we can't write heartbeat

    def _remove_heartbeat_file(self) -> None:
        """Remove heartbeat file when stopping."""
        try:
            if self._heartbeat_file.exists():
                self._heartbeat_file.unlink()
        except Exception:
            pass

    @classmethod
    def read_external_heartbeat(cls, tasks_dir: str) -> Optional[dict]:
        """
        Read heartbeat from an externally running scheduler.

        Returns:
            Heartbeat data dict if valid and recent, None otherwise
        """
        heartbeat_file = Path(tasks_dir) / ".scheduler_heartbeat"
        if not heartbeat_file.exists():
            return None

        try:
            data = json.loads(heartbeat_file.read_text(encoding='utf-8'))
            last_hb = datetime.fromisoformat(data.get("last_heartbeat", ""))

            # Check if heartbeat is recent (within 10 seconds)
            age = (datetime.now(timezone.utc) - last_hb).total_seconds()
            if age < 10:
                data["heartbeat_age_seconds"] = age
                data["heartbeat_ok"] = True
                return data
            else:
                # Stale heartbeat - scheduler may have crashed
                return None
        except Exception:
            return None

    # ========================================================================
    # TASK LOADING
    # ========================================================================

    def _load_all_tasks(self) -> None:
        """Load all tasks from per-agent or legacy task directories."""
        if self._use_per_agent:
            # Scan all agents/{id}/tasks/ directories
            for agent_dir in self.agents_dir.iterdir():
                if agent_dir.is_dir() and not agent_dir.name.startswith('.'):
                    tasks_dir = agent_dir / "tasks"
                    if tasks_dir.exists():
                        for task_folder in tasks_dir.iterdir():
                            if task_folder.is_dir() and not task_folder.name.startswith('.'):
                                self._load_task(task_folder, agent_id=agent_dir.name)
        elif self.tasks_dir and self.tasks_dir.exists():
            # Legacy: load from single tasks directory
            for task_folder in self.tasks_dir.iterdir():
                if task_folder.is_dir() and not task_folder.name.startswith('.'):
                    self._load_task(task_folder)

    def _load_task(self, folder: Path, agent_id: str = None) -> Optional[ScheduledTask]:
        """
        Load a single task from its folder.

        Args:
            folder: Path to task folder
            agent_id: Agent ID (for per-agent structure, overrides config)
        """
        task_json = folder / "task.json"
        task_md = folder / "task.md"

        if not task_json.exists():
            return None

        try:
            config = json.loads(task_json.read_text(encoding='utf-8'))
            schedule = self._parse_schedule(config.get("schedule", {}))
            status = config.get("status", {})

            # Use provided agent_id (from directory) or fall back to config
            effective_agent_id = agent_id or config.get("execution", {}).get("agent_id", "default")

            disk_last_run = self._parse_datetime(status.get("last_run"))

            # BUG FIX: Preserve in-memory next_run when reloading unchanged tasks.
            # Without this, the periodic reload (_load_all_tasks every 10s) recalculates
            # next_run = now + interval_seconds on every cycle. For tasks that haven't
            # run yet (last_run=null), this pushes next_run forward indefinitely, so
            # the task is NEVER due. We only recalculate when the task is new or its
            # last_run changed on disk (meaning an execution completed).
            with self._lock:
                existing = self._tasks.get(config["task_id"])

            if existing and existing.next_run and disk_last_run == existing.last_run:
                next_run = existing.next_run
            else:
                next_run = self._calculate_next_run(schedule, status)

            task = ScheduledTask(
                task_id=config["task_id"],
                name=config.get("name", config["task_id"]),
                folder_path=folder,
                schedule=schedule,
                agent_id=effective_agent_id,
                enabled=status.get("enabled", True),
                last_run=disk_last_run,
                next_run=next_run,
                run_count=status.get("run_count", 0),
                skill_id=config.get("execution", {}).get("skill_id"),  # Optional skill link
                context=config.get("context", {})  # Task-specific context
            )

            with self._lock:
                self._tasks[task.task_id] = task

            return task

        except Exception as e:
            print(f"Failed to load task from {folder}: {e}")
            return None

    def reload_task(self, task_id: str) -> Optional[ScheduledTask]:
        """Reload a task from disk."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return None

        return self._load_task(task.folder_path)

    def _parse_schedule(self, schedule_config: dict) -> TaskSchedule:
        """Parse schedule configuration."""
        schedule_type = schedule_config.get("type", "interval")

        return TaskSchedule(
            type=schedule_type,
            next_run=None,
            interval_seconds=schedule_config.get("interval_seconds"),
            cron_expression=schedule_config.get("expression"),
            timezone=schedule_config.get("timezone", "UTC"),
            events=schedule_config.get("events", []),
            run_at=self._parse_datetime(schedule_config.get("run_at"))
        )

    def _calculate_next_run(
        self,
        schedule: TaskSchedule,
        status: dict
    ) -> Optional[datetime]:
        """Calculate when the task should next run."""
        now = datetime.now(timezone.utc)

        if schedule.type == "event_only":
            return None

        if schedule.type == "once":
            if schedule.run_at and schedule.run_at > now:
                return schedule.run_at
            # Check if already run
            if status.get("last_run"):
                return None  # Already executed
            run_at = self._parse_datetime(status.get("run_at"))
            if run_at and run_at > now:
                return run_at
            return None

        if schedule.type == "interval":
            last_run = self._parse_datetime(status.get("last_run"))
            if last_run and schedule.interval_seconds:
                next_time = last_run + timedelta(seconds=schedule.interval_seconds)
                # If next time is in the past, calculate from now
                while next_time <= now:
                    next_time += timedelta(seconds=schedule.interval_seconds)
                return next_time
            # First run: use anchor or now
            anchor = self._parse_datetime(status.get("anchor_time"))
            if anchor:
                next_time = anchor
                while next_time <= now:
                    next_time += timedelta(seconds=schedule.interval_seconds or 3600)
                return next_time
            return now + timedelta(seconds=schedule.interval_seconds or 3600)

        if schedule.type == "cron" and schedule.cron_expression:
            try:
                from croniter import croniter
                cron = croniter(schedule.cron_expression, now)
                return cron.get_next(datetime)
            except ImportError:
                print("Warning: croniter not installed, cron schedules disabled")
                return None
            except Exception as e:
                print(f"Cron parse error: {e}")
                return None

        return None

    def _parse_datetime(self, value: Optional[str]) -> Optional[datetime]:
        """Parse ISO datetime string."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return None

    # ========================================================================
    # TASK EXECUTION
    # ========================================================================

    def _check_and_execute(self) -> None:
        """
        Check for due tasks and execute them **sequentially**.

        All due tasks are collected first, then executed one by one in the
        caller's thread. A task that takes 60 seconds delays every task behind
        it by 60 seconds. During execution the heartbeat is not updated, so
        ``get_status()`` may report "stopped" — see ``_run_loop`` docstring.
        """
        now = datetime.now(timezone.utc)

        with self._lock:
            due_tasks = [
                task for task in self._tasks.values()
                if task.enabled and task.next_run and task.next_run <= now
            ]

        for task in due_tasks:
            self._execute_task(task, trigger_type="scheduled")

    def _execute_task(self, task: ScheduledTask, trigger_type: str) -> dict:
        """
        Execute a task through the agentic loop.

        WARNING — Early-return paths: If ``task.md`` is missing or empty, this
        method returns immediately WITHOUT calling ``_update_task_after_run`` or
        ``_save_run_history``. That means ``run_count`` stays at 0, ``next_run``
        is never advanced, and the task remains "due" on every cycle (but keeps
        being skipped). If you see a task with run_count=0 and next_run in the
        past, check whether its task.md is present and non-empty.
        """
        task_md_path = task.folder_path / "task.md"

        if not task_md_path.exists():
            return {"status": "error", "error": "task.md not found"}

        # Read task instructions
        task_content = task_md_path.read_text(encoding='utf-8')

        # Skip if empty
        if not task_content.strip():
            return {"status": "skipped", "reason": "empty task.md"}

        started_at = datetime.now(timezone.utc)

        # Execute via the agentic loop (with skill_id and context support)
        try:
            # Try new signature with skill_id and context
            import inspect
            sig = inspect.signature(self.executor)
            param_count = len(sig.parameters)
            if param_count >= 5:
                result = self.executor(task.task_id, task_content, task.agent_id, task.skill_id, task.context)
            elif param_count >= 4:
                # Legacy: skill_id but no context
                result = self.executor(task.task_id, task_content, task.agent_id, task.skill_id)
            else:
                # Legacy executor without skill support
                result = self.executor(task.task_id, task_content, task.agent_id)
        except Exception as e:
            result = {"status": "error", "error": str(e)}

        completed_at = datetime.now(timezone.utc)

        # Update task status
        self._update_task_after_run(task, result, trigger_type)

        # Save run history
        self._save_run_history(task, result, trigger_type, started_at, completed_at)

        return result

    def _update_task_after_run(
        self,
        task: ScheduledTask,
        result: dict,
        trigger_type: str
    ) -> None:
        """Update task metadata after execution."""
        now = datetime.now(timezone.utc)

        # Update in-memory state
        task.last_run = now
        task.run_count += 1
        task.next_run = self._calculate_next_run(
            task.schedule,
            {"last_run": now.isoformat()}
        )

        # For one-shot tasks, disable after run
        if task.schedule.type == "once":
            task.enabled = False

        # Update task.json
        task_json_path = task.folder_path / "task.json"
        if task_json_path.exists():
            try:
                config = json.loads(task_json_path.read_text(encoding='utf-8'))
                config.setdefault("status", {})
                config["status"]["last_run"] = now.isoformat()
                config["status"]["next_run"] = task.next_run.isoformat() if task.next_run else None
                config["status"]["run_count"] = task.run_count
                config["status"]["last_status"] = result.get("status", "unknown")
                config["status"]["enabled"] = task.enabled
                config["updated_at"] = now.isoformat()
                task_json_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
            except Exception as e:
                print(f"Failed to update task.json: {e}")

    def _save_run_history(
        self,
        task: ScheduledTask,
        result: dict,
        trigger_type: str,
        started_at: datetime,
        completed_at: datetime
    ) -> None:
        """
        Save task run to history.

        IMPORTANT: This is separate from agent runs (output/manager.py).
        - **Task runs** (here): Stored in ``tasks/{task_id}/runs/*.json``.
          Auto-cleaned to last 50 via ``_cleanup_old_runs()``.
          Read by the ``schedule_run_list`` tool so agents can check their own history.
        - **Agent runs** (OutputManager): Stored in ``runs/{date}/run_{N}/``.
          NO auto-cleanup — grows indefinitely.

        Both are created per execution, but they serve different purposes:
        task runs track schedule/trigger metadata, agent runs track the full
        conversation and token usage.
        """
        runs_dir = task.folder_path / "runs"
        runs_dir.mkdir(exist_ok=True)

        # Include microseconds to ensure unique filenames
        timestamp_str = started_at.strftime('%Y%m%d_%H%M%S') + f"_{started_at.microsecond:06d}"
        run_id = f"run_{timestamp_str}"
        run_file = runs_dir / f"{timestamp_str}.json"

        run_data = {
            "run_id": run_id,
            "task_id": task.task_id,
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "trigger": {"type": trigger_type},
            "execution": {
                "agent_id": task.agent_id,
                "session_id": f"task_{task.task_id}_{started_at.strftime('%Y%m%d_%H%M%S')}",
                "skill_id": result.get("skill_id"),
                "skill_matched": result.get("skill_matched", False),
                "turns": result.get("turns", 0),
                "tools_used": result.get("tools_used", []),
                "tokens_used": result.get("tokens_used", 0)
            },
            "result": {
                "status": result.get("status", "unknown"),
                "response": result.get("response"),
                "error": result.get("error")
            }
        }

        run_file.write_text(json.dumps(run_data, indent=2), encoding='utf-8')

        # Cleanup old runs
        self._cleanup_old_runs(runs_dir, max_keep=50)

    def _cleanup_old_runs(self, runs_dir: Path, max_keep: int) -> None:
        """
        Remove old task run history files, keeping only the most recent.

        This is the ONLY auto-cleanup that exists for run data. It applies to
        task runs (tasks/{task_id}/runs/) only — NOT to agent runs
        (runs/{date}/run_{N}/), which have no cleanup and grow indefinitely.
        """
        run_files = sorted(runs_dir.glob("*.json"))
        if len(run_files) > max_keep:
            for old_file in run_files[:-max_keep]:
                old_file.unlink()

    # ========================================================================
    # EVENT HANDLING
    # ========================================================================

    def emit_event(self, event_name: str, payload: dict = None) -> None:
        """Emit an event that may trigger tasks."""
        with self._lock:
            self._event_queue.append({
                "event": event_name,
                "payload": payload or {},
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

    def _process_events(self) -> None:
        """Process queued events."""
        with self._lock:
            events = self._event_queue.copy()
            self._event_queue.clear()

        for event in events:
            event_name = event["event"]

            # Find tasks that listen to this event
            with self._lock:
                matching_tasks = [
                    task for task in self._tasks.values()
                    if task.enabled and event_name in task.schedule.events
                ]

            for task in matching_tasks:
                self._execute_task(task, trigger_type=f"event:{event_name}")

    # ========================================================================
    # TASK MANAGEMENT
    # ========================================================================

    def create_task(
        self,
        task_id: str,
        name: str,
        task_md_content: str,
        schedule: dict,
        agent_id: str = "main",
        skill_id: str = None,
        **kwargs
    ) -> ScheduledTask:
        """
        Create a new task.

        Args:
            task_id: Unique task identifier
            name: Human-readable name
            task_md_content: Content for task.md
            schedule: Schedule configuration
            agent_id: Agent to execute the task (required for per-agent structure)
            skill_id: Optional skill ID to use (None = auto-match at runtime)
            **kwargs: Additional configuration

        Returns:
            Created ScheduledTask
        """
        # Determine task folder location
        if self._use_per_agent:
            # Per-agent structure: agents/{agent_id}/tasks/{task_id}/
            agent_tasks_dir = self.agents_dir / agent_id / "tasks"
            agent_tasks_dir.mkdir(parents=True, exist_ok=True)
            task_folder = agent_tasks_dir / task_id
        else:
            # Legacy: tasks_dir/{task_id}/
            if not self.tasks_dir:
                raise ValueError("No tasks_dir configured")
            task_folder = self.tasks_dir / task_id

        task_folder.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)

        # Create task.json
        config = {
            "task_id": task_id,
            "name": name,
            "description": kwargs.get("description", ""),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "created_by": kwargs.get("created_by", "system"),
            "schedule": schedule,
            "execution": {
                "agent_id": agent_id,
                "skill_id": skill_id,  # None = auto-match at runtime
                "session_mode": kwargs.get("session_mode", "isolated"),
                "timeout_seconds": kwargs.get("timeout_seconds", 300),
                "max_turns": kwargs.get("max_turns", 15)
            },
            "context": kwargs.get("context", {}),  # Task-specific context (workspace_id, etc.)
            "status": {
                "enabled": kwargs.get("enabled", True),
                "last_run": None,
                "next_run": None,
                "run_count": 0
            },
            "triggers": {
                "on_event": kwargs.get("on_event", []),
                "allow_manual": kwargs.get("allow_manual", True)
            },
            "output": {
                "save_runs": True,
                "max_runs_kept": kwargs.get("max_runs_kept", 50)
            }
        }

        (task_folder / "task.json").write_text(json.dumps(config, indent=2), encoding='utf-8')
        (task_folder / "task.md").write_text(task_md_content, encoding='utf-8')
        (task_folder / "runs").mkdir(exist_ok=True)

        # Load and register
        return self._load_task(task_folder, agent_id=agent_id)

    def update_task(self, task_id: str, updates: dict) -> bool:
        """Update task configuration."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return False

        task_json_path = task.folder_path / "task.json"
        if not task_json_path.exists():
            return False

        try:
            config = json.loads(task_json_path.read_text(encoding='utf-8'))

            # Deep merge updates
            self._deep_update(config, updates)
            config["updated_at"] = datetime.now(timezone.utc).isoformat()

            task_json_path.write_text(json.dumps(config, indent=2), encoding='utf-8')

            # Reload task
            self._load_task(task.folder_path)
            return True
        except Exception as e:
            print(f"Failed to update task: {e}")
            return False

    def _deep_update(self, base: dict, updates: dict) -> None:
        """Deep merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_update(base[key], value)
            else:
                base[key] = value

    def update_task_md(self, task_id: str, content: str) -> bool:
        """Update task.md content."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return False

        task_md_path = task.folder_path / "task.md"
        try:
            task_md_path.write_text(content, encoding='utf-8')

            # Update timestamp
            task_json_path = task.folder_path / "task.json"
            if task_json_path.exists():
                config = json.loads(task_json_path.read_text(encoding='utf-8'))
                config["updated_at"] = datetime.now(timezone.utc).isoformat()
                task_json_path.write_text(json.dumps(config, indent=2), encoding='utf-8')

            return True
        except Exception as e:
            print(f"Failed to update task.md: {e}")
            return False

    def delete_task(self, task_id: str) -> bool:
        """Delete a task."""
        with self._lock:
            task = self._tasks.pop(task_id, None)

        if not task:
            return False

        try:
            shutil.rmtree(task.folder_path)
            return True
        except Exception as e:
            print(f"Failed to delete task: {e}")
            return False

    def enable_task(self, task_id: str) -> bool:
        """Enable a task."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return False

        task.enabled = True

        # Recalculate next run
        task_json_path = task.folder_path / "task.json"
        if task_json_path.exists():
            config = json.loads(task_json_path.read_text(encoding='utf-8'))
            task.next_run = self._calculate_next_run(
                task.schedule,
                config.get("status", {})
            )

        return self.update_task(task_id, {"status": {"enabled": True}})

    def disable_task(self, task_id: str) -> bool:
        """Disable a task."""
        with self._lock:
            task = self._tasks.get(task_id)

        if task:
            task.enabled = False

        return self.update_task(task_id, {"status": {"enabled": False}})

    def trigger_task(self, task_id: str) -> dict:
        """Manually trigger a task."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return {"status": "error", "error": "Task not found"}

        # Check if manual trigger is allowed
        task_json_path = task.folder_path / "task.json"
        if task_json_path.exists():
            config = json.loads(task_json_path.read_text(encoding='utf-8'))
            if not config.get("triggers", {}).get("allow_manual", True):
                return {"status": "error", "error": "Manual trigger not allowed"}

        return self._execute_task(task, trigger_type="manual")

    # ========================================================================
    # QUERY METHODS
    # ========================================================================

    def list_tasks(self, agent_id: str = None) -> List[dict]:
        """
        List all tasks, optionally filtered by agent.

        Reads status fields (last_run, run_count) from disk to reflect
        executions by external scheduler processes.

        Args:
            agent_id: Optional agent ID filter

        Returns:
            List of task dictionaries
        """
        with self._lock:
            tasks = list(self._tasks.values())
            if agent_id:
                tasks = [t for t in tasks if t.agent_id == agent_id]

        result = []
        for t in tasks:
            # Read fresh status from disk to reflect external scheduler executions
            last_run = t.last_run.isoformat() if t.last_run else None
            run_count = t.run_count
            enabled = t.enabled
            disk_config = {}
            try:
                disk_config = json.loads((t.folder_path / "task.json").read_text(encoding='utf-8'))
                disk_status = disk_config.get("status", {})
                last_run = disk_status.get("last_run", last_run)
                run_count = disk_status.get("run_count", run_count)
                enabled = disk_status.get("enabled", enabled)
            except Exception:
                pass  # Fall back to in-memory state

            # Read extra fields from disk config (created_by, description, cron_expression)
            created_by = disk_config.get("created_by", "system")
            description = disk_config.get("description", "")
            sched_cfg = disk_config.get("schedule", {})
            cron_expression = sched_cfg.get("expression")

            result.append({
                "task_id": t.task_id,
                "name": t.name,
                "agent_id": t.agent_id,
                "enabled": enabled,
                "schedule_type": t.schedule.type,
                "interval": t.schedule.interval_seconds,
                "interval_seconds": t.schedule.interval_seconds,
                "cron_expression": cron_expression,
                "skill_id": t.skill_id,
                "last_run": last_run,
                "next_run": t.next_run.isoformat() if t.next_run else None,
                "run_count": run_count,
                "created_by": created_by,
                "description": description
            })
        return result

    def get_task(self, task_id: str) -> Optional[dict]:
        """Get task details."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return None

        task_json_path = task.folder_path / "task.json"
        if task_json_path.exists():
            config = json.loads(task_json_path.read_text(encoding='utf-8'))
            # Add task.md content
            task_md_path = task.folder_path / "task.md"
            if task_md_path.exists():
                config["task_md_content"] = task_md_path.read_text(encoding='utf-8')
            return config
        return None

    def get_task_runs(self, task_id: str, limit: int = 10) -> List[dict]:
        """Get task run history."""
        with self._lock:
            task = self._tasks.get(task_id)

        if not task:
            return []

        runs_dir = task.folder_path / "runs"
        if not runs_dir.exists():
            return []

        run_files = sorted(runs_dir.glob("*.json"), reverse=True)[:limit]
        runs = []
        for f in run_files:
            try:
                runs.append(json.loads(f.read_text(encoding='utf-8')))
            except Exception:
                pass
        return runs

    def get_due_tasks(self) -> List[dict]:
        """Get tasks that are due to run."""
        now = datetime.now(timezone.utc)
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "name": t.name,
                    "next_run": t.next_run.isoformat() if t.next_run else None,
                    "overdue_seconds": int((now - t.next_run).total_seconds()) if t.next_run and t.next_run <= now else 0
                }
                for t in self._tasks.values()
                if t.enabled and t.next_run and t.next_run <= now
            ]


# ============================================================================
# TASK EXECUTOR FACTORY
# ============================================================================

def create_task_executor(
    agent_manager: Any,
    skill_matcher: Any = None,
    skill_loader: Any = None
) -> Callable[[str, str, str, Optional[str]], dict]:
    """
    Create an executor function for the scheduler.

    This bridges the scheduler to the agentic loop with skill support.

    In practice, both callers (``cli_scheduler_start`` and ``app.get_scheduler``)
    pass only ``agent_manager`` — ``skill_matcher`` and ``skill_loader`` are
    always None. When None, skill auto-matching is disabled, and skill loading
    falls through to ``_get_skill_content()`` which reads directly from disk
    using paths from ``agent_manager.config_manager.global_config.paths``.

    Args:
        agent_manager: AgentManager instance
        skill_matcher: Optional SkillMatcher for auto-matching (None = disabled)
        skill_loader: Optional SkillLoader for loading skill content (None = disk fallback)

    Returns:
        Executor function (task_id, content, agent_id, skill_id, context) -> result
    """
    _skill_matcher = skill_matcher
    _skill_loader = skill_loader

    def _get_skill_content(skill_id: str, agent_id: str) -> Optional[str]:
        """Load skill content for injection into prompt."""
        if not skill_id:
            return None

        # Try skill_loader first
        if _skill_loader:
            skill = _skill_loader.get_skill(skill_id)
            if skill:
                return _skill_loader.build_skill_content_prompt(skill_id)

        # Try loading directly from disk using config paths
        try:
            from pathlib import Path

            # Use agent_manager's config paths (resolves data/loopCore/AGENTS etc.)
            agents_dir = Path(agent_manager.config_manager.global_config.paths.agents_dir)
            skills_dir = Path(agent_manager.config_manager.global_config.paths.skills_dir)

            # Check agent's private skills first
            agent_skill_dir = agents_dir / agent_id / "skills" / skill_id
            global_skill_dir = skills_dir / skill_id

            skill_dir = None
            if agent_skill_dir.exists():
                skill_dir = agent_skill_dir
            elif global_skill_dir.exists():
                skill_dir = global_skill_dir

            if skill_dir:
                skill_md = skill_dir / "skill.md"
                skill_json = skill_dir / "skill.json"

                if skill_md.exists():
                    content = skill_md.read_text(encoding='utf-8')
                    name = skill_id
                    description = ""

                    if skill_json.exists():
                        import json
                        metadata = json.loads(skill_json.read_text(encoding='utf-8'))
                        name = metadata.get("name", skill_id)
                        description = metadata.get("description", "")

                    return f"""## Skill: {name}

**Description:** {description}

### Instructions

{content}
"""
        except Exception as e:
            print(f"[WARN] Failed to load skill {skill_id}: {e}")

        return None

    def execute_task(
        task_id: str,
        task_md_content: str,
        agent_id: str,
        skill_id: str = None,
        context: dict = None
    ) -> dict:
        """
        Execute a task through the agentic loop.

        Args:
            task_id: Task identifier
            task_md_content: Content from task.md
            agent_id: Agent to execute the task
            skill_id: Explicit skill ID (None = auto-match)
            context: Task-specific context (workspace_id, etc.)
        """
        context = context or {}

        # Resolve keywords in context
        resolved_context = context
        if context:
            try:
                # Get paths from agent manager
                agents_dir = Path(agent_manager.config_manager.global_config.paths.agents_dir)
                config_dir = Path(agent_manager.config_manager.global_config.paths.config_dir)

                resolver = KeywordResolver(agents_dir, config_dir)
                resolved_context = resolver.resolve_context(context, agent_id, task_id)
                keys_list = ", ".join(context.keys())
                skill_info = f", skill={skill_id}" if skill_id else ""
                print(f"[INFO] Task '{task_id}' (agent={agent_id}{skill_info}): "
                      f"Resolved {len(context)} context keys [{keys_list}]")
            except KeywordResolutionError as e:
                print(f"[ERROR] Task '{task_id}' (agent={agent_id}): Keyword resolution failed: {e}")
                return {
                    "status": "error",
                    "response": f"Context resolution failed: {e}",
                    "error": str(e)
                }
            except Exception as e:
                print(f"[WARN] Task '{task_id}' (agent={agent_id}): "
                      f"Keyword resolution error (continuing with raw context): {e}")
                resolved_context = context

        # Determine which skill to use
        effective_skill_id = skill_id
        matched_skill = False

        # Auto-match if no explicit skill
        if not effective_skill_id and _skill_matcher:
            try:
                match_result = _skill_matcher.match_with_details(
                    task_description=task_md_content,
                    agent_id=agent_id
                )
                if match_result.skill_id:
                    effective_skill_id = match_result.skill_id
                    matched_skill = True
                    print(f"[INFO] Auto-matched skill '{effective_skill_id}' "
                          f"({match_result.confidence}): {match_result.reason}")
            except Exception as e:
                print(f"[WARN] Skill matching failed: {e}")

        # Load skill content if we have a skill
        skill_content = None
        if effective_skill_id:
            skill_content = _get_skill_content(effective_skill_id, agent_id)
            if skill_content:
                print(f"[INFO] Loaded skill '{effective_skill_id}' for task {task_id} ({len(skill_content)} chars)")
            else:
                print(f"[WARN] Skill '{effective_skill_id}' not found for agent '{agent_id}' — task runs without skill context")

        # Build context section using formatter
        context_section = ""
        if resolved_context:
            context_section = "\n" + format_resolved_context(resolved_context)

        # Build the task prompt
        if skill_content:
            prompt = f"""You are executing a scheduled task.

SKILL CONTEXT:
{skill_content}
{context_section}
TASK INSTRUCTIONS:
{task_md_content}

---
Task ID: {task_id}
Skill: {effective_skill_id} {'(auto-matched)' if matched_skill else '(explicit)'}

Execute the task using the skill provided above. Follow the skill instructions to accomplish the task.
"""
        else:
            prompt = f"""You are executing a scheduled task.
{context_section}
TASK INSTRUCTIONS:
{task_md_content}

---
Task ID: {task_id}
Execute the instructions above. Report your actions and results.
"""

        # Run through agent
        try:
            session_id = f"task_{task_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

            result = agent_manager.run_agent(
                agent_id=agent_id,
                message=prompt,
                session_id=session_id,
                save_output=True
            )

            return {
                "status": result.status,
                "response": result.final_response,
                "turns": result.turns,
                "tools_used": result.tools_called,
                "tokens_used": result.total_tokens,
                "session_id": session_id,
                "skill_id": effective_skill_id,
                "skill_matched": matched_skill
            }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "skill_id": effective_skill_id,
                "skill_matched": matched_skill
            }

    return execute_task

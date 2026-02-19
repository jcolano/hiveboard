"""
AGENT RUNTIME
==============

Lifecycle manager for autonomous agents with heartbeat timers and a priority
event queue. Gives agents a Start/Stop state so they can periodically check
for @mentions, execute scheduled tasks, and respond to external triggers.

Architecture
------------
A single daemon thread (``_run_loop``) iterates over all active agents once
per second. For each agent it:

1. Checks quantized heartbeat timers → may enqueue a LOW-priority event.
2. Checks scheduled tasks → may enqueue NORMAL-priority events.
3. If the agent is idle and the queue is non-empty, pops the highest-priority
   event and submits it to a ``ThreadPoolExecutor``.
4. If the agent's current run is done, harvests the result and clears state.

LLM calls are always submitted to the thread pool so they never block the
main loop. Only one LLM call runs per agent at a time.

Design Decisions
-----------------
- Independent from TaskScheduler — absorbs its execution role while
  TaskScheduler stays alive as a task CRUD adapter.
- Priority queue is a plain sorted ``list`` (max 20 items). We need
  inspection for queue-depth reporting and heartbeat-drop logic.
- Active state tracked in ``.runtime_state.json``. On server restart,
  agents active within the last 10 minutes are auto-restored via
  ``restore_previously_active()``.
- Thread pool hardcoded to 4 workers (enough for 3–5 agents).
"""

import bisect
import json
import logging
import os
import re
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ============================================================================
# CONSTANTS
# ============================================================================

MAX_QUEUE_DEPTH = 20
MAX_THREAD_WORKERS = 4
TASK_RELOAD_INTERVAL_S = 10.0
RETRY_DELAY_MINUTES = 2
MAX_DEFERRED_EVENTS = 10

TRANSIENT_PATTERNS = [
    "429",
    "503",
    "529",
    "overloaded",
    "rate limit",
    "rate_limit",
    "too many requests",
    "service unavailable",
    "connection timeout",
    "connection refused",
    "read timed out",
    "readtimeout",
    "temporarily unavailable",
]


def is_transient_error(status: str, error: str) -> bool:
    """Check if an error is transient and worth retrying."""
    if status in ("completed", "max_turns", "timeout"):
        return False
    if not error:
        return False
    error_lower = error.lower()
    return any(pattern in error_lower for pattern in TRANSIENT_PATTERNS)


# ============================================================================
# DATA STRUCTURES
# ============================================================================

class Priority(IntEnum):
    HIGH = 1      # Human messages — user is waiting
    NORMAL = 2    # Webhooks, scheduled tasks
    LOW = 3       # Heartbeat ticks


@dataclass(order=False)
class AgentEvent:
    """A queued event for an agent to process."""
    priority: Priority
    timestamp: datetime
    message: str
    session_key: Optional[str] = None   # None = main session
    source: str = "unknown"             # "human", "heartbeat", "task:{id}", "agent:{id}", etc.
    routing: Any = None                 # OutputRouteConfig for response delivery
    # --- Agent-created event fields ---
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    title: Optional[str] = None
    context: Optional[Dict] = None      # Credentials, IDs, URLs for execution
    skill_id: Optional[str] = None      # Which skill to load for execution
    status: str = "active"              # pending_approval | active | running | completed | dropped
    created_by: str = "system"          # system | agent | human
    retry_count: int = 0                # 0 = first attempt, 1 = retry

    def __lt__(self, other: "AgentEvent") -> bool:
        """Sort by priority first, then timestamp (FIFO within same priority)."""
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp


@dataclass
class AgentState:
    """Per-agent runtime state (transient — never persisted)."""
    agent_id: str
    active: bool = False
    queue: List[AgentEvent] = field(default_factory=list)
    pending_events: List[AgentEvent] = field(default_factory=list)  # Awaiting human approval
    heartbeat_enabled: bool = True
    heartbeat_interval_minutes: int = 15
    heartbeat_skills: List[dict] = field(default_factory=list)  # Cached list of skills with heartbeat.md
    last_heartbeat_time: Optional[datetime] = None
    current_run: Optional[Future] = None
    current_event: Optional[AgentEvent] = None
    heartbeat_md: str = ""
    started_at: Optional[datetime] = None
    scheduled_tasks: Dict[str, dict] = field(default_factory=dict)
    last_task_reload: Optional[datetime] = None

    # Metrics (transient, reset on start)
    heartbeats_fired: int = 0
    heartbeats_skipped: int = 0
    events_processed: int = 0
    events_failed: int = 0
    events_retried: int = 0
    webhooks_received: int = 0
    total_run_duration_ms: int = 0

    # Event history (last N completed events)
    event_history: List[dict] = field(default_factory=list)

    # Deferred events (retries waiting for backoff delay)
    deferred_events: List[dict] = field(default_factory=list)


# ============================================================================
# AGENT RUNTIME
# ============================================================================

class AgentRuntime:
    """
    Lifecycle manager for autonomous agents.

    Manages start/stop state, heartbeat timers, scheduled task execution,
    and a priority event queue for each agent.
    """

    def __init__(
        self,
        agent_manager: Any,
        agents_dir: str,
        executor_factory: Callable = None,
    ):
        """
        Args:
            agent_manager: AgentManager instance (for run_agent, registry access).
            agents_dir: Path to data/AGENTS directory.
            executor_factory: Factory function that returns the task executor closure.
                              Signature: executor_factory(agent_manager) -> executor_fn.
        """
        self._manager = agent_manager
        self._agents_dir = Path(agents_dir)
        self._executor_factory = executor_factory
        self._task_executor: Optional[Callable] = None

        # Thread pool for LLM calls
        self._pool = ThreadPoolExecutor(max_workers=MAX_THREAD_WORKERS)

        # Per-agent state
        self._agents: Dict[str, AgentState] = {}
        self._lock = threading.Lock()

        # Main loop control
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # Human delegation monitor
        self._human_monitor = None

        # Output router (set via set_router)
        self._router: Any = None

        # Heartbeat file for admin panel liveness detection
        self._heartbeat_file = self._agents_dir / ".runtime_heartbeat"
        self._state_file = self._agents_dir / ".runtime_state.json"

    # ========================================================================
    # LIFECYCLE
    # ========================================================================

    def start(self) -> None:
        """Start the runtime main loop in a daemon thread."""
        if self._running:
            return

        # Build the task executor if factory was provided
        if self._executor_factory and self._task_executor is None:
            self._task_executor = self._executor_factory(self._manager)

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="agent-runtime")
        self._thread.start()

        # Start human delegation monitor
        try:
            from .human_monitor import HumanDelegationMonitor
            self._human_monitor = HumanDelegationMonitor(
                agents_dir=self._agents_dir, runtime=self,
            )
            self._human_monitor.start()
        except Exception as e:
            logger.warning("Human delegation monitor init error (non-fatal): %s", e)

        logger.info("AgentRuntime started")

    def stop(self) -> None:
        """Stop the runtime. In-flight LLM calls finish their current turn then exit."""
        if not self._running:
            return

        # Save queues for agents with persist_queue_on_stop before stopping
        try:
            from .config.loader import get_config_manager
            config_mgr = get_config_manager()
            with self._lock:
                for agent_id, state in self._agents.items():
                    if state.active and state.queue:
                        try:
                            config = config_mgr.load_agent(agent_id)
                            if getattr(config, 'persist_queue_on_stop', False):
                                self._save_agent_queue(agent_id, state)
                        except Exception:
                            pass
        except Exception:
            pass

        # Stop human delegation monitor
        if self._human_monitor:
            try:
                self._human_monitor.stop()
            except Exception:
                pass

        # Signal all workers to stop via cancel_check (they see _running=False)
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        # Workers self-terminate after current turn via cancel_check.
        # wait=True so pool threads finish cleanly; cancel_futures drops queued work.
        self._pool.shutdown(wait=True, cancel_futures=True)
        self._remove_heartbeat()
        self._save_runtime_state()
        logger.info("AgentRuntime stopped")

    def restore_previously_active(self, max_age_minutes: int = 10) -> List[str]:
        """
        Auto-start agents that were active before a server crash/restart.

        Reads .runtime_state.json and starts any agent whose started_at is
        within the last `max_age_minutes` minutes.

        Returns:
            List of agent IDs that were restored.
        """
        if not self._state_file.exists():
            return []

        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return []

        active_agents = data.get("active_agents", [])
        if not active_agents:
            return []

        now = datetime.now(timezone.utc)
        restored = []

        for entry in active_agents:
            agent_id = entry.get("agent_id")
            if not agent_id:
                continue

            # Check if the state file was updated recently enough
            updated_at = data.get("updated_at")
            if updated_at:
                try:
                    updated = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    age_minutes = (now - updated).total_seconds() / 60
                    if age_minutes > max_age_minutes:
                        logger.info("Skipping restore of '%s': state file too old (%.0fm)", agent_id, age_minutes)
                        continue
                except (ValueError, TypeError):
                    pass

            try:
                result = self.start_agent(agent_id)
                if result.get("status") in ("ok", "already_active"):
                    restored.append(agent_id)
                    logger.info("Auto-restored agent '%s' from previous session", agent_id)
            except Exception as e:
                logger.warning("Failed to auto-restore agent '%s': %s", agent_id, e)

        return restored

    def set_router(self, router) -> None:
        """Set the output router for response delivery."""
        self._router = router

    # ========================================================================
    # AGENT START / STOP
    # ========================================================================

    def start_agent(self, agent_id: str) -> dict:
        """
        Start an agent: load registry, build timers, set active.

        Returns:
            Status dict with timer count.
        """
        offset_minutes = 0  # Set inside lock, used for logging after
        with self._lock:
            if agent_id in self._agents and self._agents[agent_id].active:
                return {"status": "already_active", "agent_id": agent_id}

            state = AgentState(agent_id=agent_id)

            # Clear cached registry and skill loader so we always read fresh from disk
            if agent_id in self._manager._agent_skill_registries:
                del self._manager._agent_skill_registries[agent_id]
            if agent_id in self._manager._agent_skill_loaders:
                del self._manager._agent_skill_loaders[agent_id]

            # Load heartbeat config from agent config
            try:
                agent_config = self._manager.get_agent(agent_id).config
                state.heartbeat_enabled = agent_config.heartbeat_enabled
                state.heartbeat_interval_minutes = agent_config.heartbeat_interval_minutes
            except Exception:
                pass  # Defaults already set on AgentState

            # Discover heartbeat skills (one-time scan, cached on state)
            state.heartbeat_skills = self._discover_heartbeat_skills(agent_id)

            # Load HEARTBEAT.md
            heartbeat_path = self._agents_dir / agent_id / "HEARTBEAT.md"
            if heartbeat_path.exists():
                try:
                    state.heartbeat_md = heartbeat_path.read_text(encoding="utf-8")
                except Exception:
                    pass

            # Load scheduled tasks
            self._load_agent_tasks(state)

            state.active = True
            state.started_at = datetime.now(timezone.utc)

            # Stagger heartbeats via hash-based deterministic offset.
            # Each agent gets a fixed offset within the interval so that
            # multiple agents don't all fire at the same wall-clock instant
            # (thundering herd). The offset survives restarts because it's
            # derived from the agent_id, not from random state.
            from datetime import timedelta
            offset_minutes = hash(agent_id) % max(state.heartbeat_interval_minutes, 1)
            state.last_heartbeat_time = (
                datetime.now(timezone.utc)
                - timedelta(minutes=(state.heartbeat_interval_minutes - offset_minutes))
            )

            state.last_task_reload = datetime.now(timezone.utc)
            self._agents[agent_id] = state

            # Restore saved queue (from previous graceful shutdown)
            restored = self._restore_agent_queue(agent_id, state)

        self._save_runtime_state()
        if state.heartbeat_enabled:
            hb_label = f"heartbeat every {state.heartbeat_interval_minutes}m, first in {state.heartbeat_interval_minutes - offset_minutes}m"
        else:
            hb_label = "heartbeat disabled"
        logger.info(f"Agent '{agent_id}' started ({hb_label}, {restored} restored events)")

        # HiveLoop: report scheduled work
        try:
            _agent_obj = self._manager.get_agent(agent_id)
            _hl_agent = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
            if _hl_agent:
                sched_items = []
                # Agent-level heartbeat
                if state.heartbeat_enabled:
                    sched_items.append({
                        "id": "heartbeat",
                        "name": "Heartbeat",
                        "interval": f"{state.heartbeat_interval_minutes}m",
                        "enabled": True,
                        "last_status": None,
                    })
                # Scheduled tasks
                for task_id, task_data in state.scheduled_tasks.items():
                    interval = task_data.get("schedule", {}).get("interval", "")
                    sched_items.append({
                        "id": f"task_{task_id}",
                        "name": task_data.get("name", task_id),
                        "interval": interval or "event_only",
                        "enabled": task_data.get("enabled", True),
                        "last_status": None,
                    })
                if sched_items:
                    _hl_agent.scheduled(items=sched_items)
        except Exception:
            pass

        # Gap #9: Report agent started
        try:
            _agent_obj = self._manager.get_agent(agent_id)
            _hl = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
            if _hl:
                _hl.event("agent_started", payload={
                    "heartbeat_enabled": state.heartbeat_enabled,
                    "heartbeat_interval_minutes": state.heartbeat_interval_minutes,
                    "restored_events": restored,
                    "scheduled_tasks": len(state.scheduled_tasks),
                })
        except Exception:
            pass

        result = {
            "status": "ok",
            "agent_id": agent_id,
            "heartbeat_enabled": state.heartbeat_enabled,
            "heartbeat_interval_minutes": state.heartbeat_interval_minutes,
        }
        if restored:
            result["restored_events"] = restored
        return result

    def stop_agent(self, agent_id: str) -> dict:
        """
        Stop an agent: set inactive, clear queue and timers.
        Lets current run finish gracefully.
        Persists queue to disk if persist_queue_on_stop is enabled.
        """
        saved = 0
        with self._lock:
            state = self._agents.get(agent_id)
            if not state or not state.active:
                return {"status": "not_active", "agent_id": agent_id}

            # Persist queue before clearing if configured
            if state.queue:
                try:
                    from .config.loader import get_config_manager
                    config = get_config_manager().load_agent(agent_id)
                    if getattr(config, 'persist_queue_on_stop', False):
                        saved = self._save_agent_queue(agent_id, state)
                except Exception:
                    pass

            dropped = len(state.queue) - saved
            state.active = False
            state.queue.clear()

        self._save_runtime_state()
        logger.info(
            f"Agent '{agent_id}' stopped "
            f"(saved {saved}, dropped {dropped} queued events)"
        )

        # Gap #9: Report agent stopped
        try:
            _agent_obj = self._manager.get_agent(agent_id)
            _hl = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
            if _hl:
                _hl.event("agent_stopped", payload={
                    "saved_events": saved,
                    "dropped_events": dropped,
                })
        except Exception:
            pass

        return {
            "status": "ok",
            "agent_id": agent_id,
            "saved_events": saved,
            "dropped_events": dropped,
        }

    # ========================================================================
    # DELEGATION CALLBACKS
    # ========================================================================

    def _fire_delegation_callback(
        self,
        delegation_id: str,
        callback_agent: str,
        status: str,
        result: str,
        from_entity: str,
        error: str = "",
    ) -> bool:
        """Insert a resume event into the callback agent's queue.

        Used by both agent completion (auto-callback in _run_loop) and
        human form submission (complete_human_delegation).

        Args:
            delegation_id: The delegation ID
            callback_agent: Agent ID to resume
            status: "completed", "timeout", or "error"
            result: Result text from the delegate
            from_entity: Who completed it (agent ID or "human:human_id")
            error: Error detail if status is not "completed"

        Returns:
            True if the event was queued, False if callback agent not active.
        """
        with self._lock:
            callback_state = self._agents.get(callback_agent)
            if not callback_state or not callback_state.active:
                logger.warning(
                    "Delegation callback: agent '%s' not active, dropping result '%s'",
                    callback_agent, delegation_id,
                )
                return False

            from .delegation import build_resume_message
            resume_msg = build_resume_message(
                {
                    "status": status,
                    "result": result,
                    "error": error,
                    "from_agent": from_entity,
                },
                {},  # Suspended plan loaded by agent on resume
            )

            result_event = AgentEvent(
                priority=Priority.HIGH,
                timestamp=datetime.now(timezone.utc),
                message=resume_msg,
                source=f"delegation:{from_entity}",
                event_id=f"deleg_result_{delegation_id}",
                title=f"Delegation result from {from_entity}",
                context={
                    "delegation_result": True,
                    "delegation_id": delegation_id,
                    "from_agent": from_entity,
                    "status": status,
                    "result": result,
                    "error": error,
                },
                session_key=f"delegation_{delegation_id}",
                created_by="delegation",
            )
            bisect.insort(callback_state.queue, result_event)

        logger.info(
            "Delegation callback: '%s' result queued on agent '%s'",
            delegation_id, callback_agent,
        )
        return True

    def _find_delegation(self, delegation_id: str):
        """Find which agent has a suspended delegation with the given ID.

        Returns:
            Tuple of (agent_id, suspension_data) or (None, None).
        """
        from .delegation import load_suspended_delegation

        for agent_dir in self._agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            data = load_suspended_delegation(str(agent_dir))
            if data and data.get("delegation_id") == delegation_id:
                return agent_dir.name, data
        return None, None

    def complete_human_delegation(self, delegation_id: str, result: dict) -> bool:
        """Called by the API endpoint when a human submits a delegation form.

        Finds the agent that delegated, fires the callback to resume it,
        and cleans up the suspension file.

        Args:
            delegation_id: The delegation ID
            result: Dict of form field values submitted by the human

        Returns:
            True if callback was fired, False if delegation not found or
            callback agent not active.
        """
        agent_id, suspended = self._find_delegation(delegation_id)
        if not agent_id:
            logger.warning("complete_human_delegation: delegation '%s' not found", delegation_id)
            return False

        human_id = suspended.get("waiting_on", "unknown")

        # Format result as readable text for the agent
        result_text = json.dumps(result, indent=2, default=str) if isinstance(result, dict) else str(result)

        success = self._fire_delegation_callback(
            delegation_id=delegation_id,
            callback_agent=agent_id,
            status="completed",
            result=result_text,
            from_entity=f"human:{human_id}",
        )

        if success:
            from .delegation import delete_suspended_delegation
            agent_dir = str(self._agents_dir / agent_id)
            delete_suspended_delegation(agent_dir)

        return success

    # ========================================================================
    # STATUS
    # ========================================================================

    def get_agent_status(self, agent_id: str) -> dict:
        """Get runtime status for a single agent."""
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return {"active": False, "queue_depth": 0}

            # Calculate next heartbeat time
            next_heartbeat_at = None
            if state.active and state.heartbeat_enabled and state.last_heartbeat_time:
                from datetime import timedelta
                next_heartbeat_at = (
                    state.last_heartbeat_time + timedelta(minutes=state.heartbeat_interval_minutes)
                ).isoformat()

            # Build current_event object with full metadata
            current_event_obj = None
            if state.current_event:
                ce = state.current_event
                current_event_obj = {
                    "event_id": ce.event_id,
                    "source": ce.source,
                    "priority": ce.priority.name,
                    "title": ce.title,
                    "skill_id": ce.skill_id,
                    "message": ce.message,
                    "message_preview": ce.message[:150],
                    "session_key": ce.session_key,
                    "created_by": ce.created_by,
                    "timestamp": ce.timestamp.isoformat(),
                    "has_routing": ce.routing is not None,
                }

            return {
                "active": state.active,
                "queue_depth": len(state.queue),
                "deferred_retry_count": len(state.deferred_events),
                "pending_approval_count": len(state.pending_events),
                "started_at": state.started_at.isoformat() if state.started_at else None,
                "current_event_source": state.current_event.source if state.current_event else None,
                "current_event_started_at": (
                    state.current_event.timestamp.isoformat()
                    if state.current_event else None
                ),
                "current_event": current_event_obj,
                "heartbeat": {
                    "enabled": state.heartbeat_enabled,
                    "interval_minutes": state.heartbeat_interval_minutes,
                    "next_at": next_heartbeat_at,
                    "skills": [s["name"] for s in state.heartbeat_skills],
                },
                "metrics": {
                    "heartbeats_fired": state.heartbeats_fired,
                    "heartbeats_skipped": state.heartbeats_skipped,
                    "events_processed": state.events_processed,
                    "events_failed": state.events_failed,
                    "events_retried": state.events_retried,
                    "webhooks_received": state.webhooks_received,
                    "total_run_duration_ms": state.total_run_duration_ms,
                },
            }

    def get_status(self) -> dict:
        """Get overall runtime status."""
        with self._lock:
            active_agents = [
                aid for aid, s in self._agents.items() if s.active
            ]
            total_queued = sum(len(s.queue) for s in self._agents.values())
            running_calls = sum(
                1 for s in self._agents.values()
                if s.current_run is not None and not s.current_run.done()
            )

        return {
            "running": self._running,
            "active_agents": active_agents,
            "total_queued": total_queued,
            "running_llm_calls": running_calls,
        }

    # ========================================================================
    # PUBLIC EVENT API
    # ========================================================================

    def push_event(self, agent_id: str, event: AgentEvent) -> dict:
        """
        Push an event into an agent's queue.

        Enforces MAX_QUEUE_DEPTH by dropping oldest LOW-priority items first.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if not state or not state.active:
                return {"status": "agent_not_active", "agent_id": agent_id}

            # Enforce max queue depth
            while len(state.queue) >= MAX_QUEUE_DEPTH:
                # Drop oldest LOW priority first
                low_idx = None
                for i in range(len(state.queue) - 1, -1, -1):
                    if state.queue[i].priority == Priority.LOW:
                        low_idx = i
                        break
                if low_idx is not None:
                    state.queue.pop(low_idx)
                else:
                    # No LOW items, drop the last (lowest-priority, oldest) item
                    state.queue.pop()

            bisect.insort(state.queue, event)
            if event.source.startswith("webhook:"):
                state.webhooks_received += 1

            # Gap #9: Report queue event to HiveLoop
            try:
                _agent_obj = self._manager.get_agent(agent_id)
                _hl = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
                if _hl:
                    _hl.event("event_queued", payload={
                        "source": event.source,
                        "priority": event.priority.name,
                        "queue_depth": len(state.queue),
                        "event_id": event.event_id,
                    })
            except Exception:
                pass

            return {"status": "queued", "queue_depth": len(state.queue)}

    # ========================================================================
    # MAIN LOOP
    # ========================================================================

    def _run_loop(self) -> None:
        """Main loop — runs in daemon thread, polls every 1 second."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                self._write_heartbeat()

                with self._lock:
                    agent_ids = [
                        aid for aid, s in self._agents.items() if s.active
                    ]

                for agent_id in agent_ids:
                    with self._lock:
                        state = self._agents.get(agent_id)
                        if not state or not state.active:
                            continue

                    # 1. Check heartbeat timers
                    self._check_heartbeat_timer(state, now)

                    # 2. Check scheduled tasks
                    self._check_scheduled_tasks(state, now)

                    # 2.5 Check deferred events (retries with backoff)
                    with self._lock:
                        ready = [d for d in state.deferred_events if d["ready_at"] <= now]
                        for d in ready:
                            state.deferred_events.remove(d)
                            bisect.insort(state.queue, d["event"])
                            logger.info(
                                "Deferred retry event '%s' now ready, added to queue",
                                d["event"].event_id,
                            )

                    # 3. If idle and queue non-empty → submit next event
                    with self._lock:
                        if state.current_run is None and state.queue:
                            event = state.queue.pop(0)
                            state.current_event = event
                            logger.info(
                                f"Dispatching event to '{agent_id}': "
                                f"source={event.source}, priority={event.priority.name}, "
                                f"msg={event.message[:80]}..."
                            )
                            state.current_run = self._pool.submit(
                                self._execute_event, agent_id, event
                            )

                    # 4. If current run is done → harvest result
                    route_work = None
                    with self._lock:
                        if state.current_run is not None and state.current_run.done():
                            finished_event = state.current_event
                            harvest_time = datetime.now(timezone.utc)
                            status_str = "completed"
                            agent_status = "completed"
                            response_text = None
                            error_detail = None
                            run_turns = 0
                            run_tokens = 0
                            try:
                                run_result = state.current_run.result()
                                # _execute_event returns a dict with status info
                                if isinstance(run_result, dict):
                                    response_text = run_result.get("response")
                                    agent_status = run_result.get("status", "completed")
                                    error_detail = run_result.get("error")
                                    run_turns = run_result.get("turns", 0)
                                    run_tokens = run_result.get("tokens", 0)
                                    # Map agent status to event status
                                    if agent_status in ("completed",):
                                        status_str = "completed"
                                        state.events_processed += 1
                                    else:
                                        status_str = "failed"
                                        state.events_failed += 1
                                        logger.warning(
                                            "Agent '%s' run failed: status=%s error=%s turns=%d",
                                            agent_id, agent_status, error_detail, run_turns,
                                        )
                                else:
                                    # Legacy: plain string return (from task events)
                                    response_text = run_result
                                    state.events_processed += 1

                                if finished_event:
                                    elapsed_ms = int(
                                        (harvest_time - finished_event.timestamp).total_seconds() * 1000
                                    )
                                    state.total_run_duration_ms += max(elapsed_ms, 0)
                                    if finished_event.routing and status_str == "completed":
                                        route_work = (agent_id, response_text, finished_event.routing)
                            except Exception as e:
                                logger.error(f"Error in agent '{agent_id}': {e}", exc_info=True)
                                state.events_failed += 1
                                status_str = "failed"
                                agent_status = "error"
                                error_detail = str(e)
                                elapsed_ms = 0

                            # Record in event history
                            if finished_event:
                                elapsed_ms = int(
                                    (harvest_time - finished_event.timestamp).total_seconds() * 1000
                                ) if finished_event else 0
                                history_entry = {
                                    "event_id": finished_event.event_id,
                                    "source": finished_event.source,
                                    "priority": finished_event.priority.name,
                                    "status": status_str,
                                    "queued_at": finished_event.timestamp.isoformat(),
                                    "completed_at": harvest_time.isoformat(),
                                    "duration_ms": max(elapsed_ms, 0),
                                    "message": finished_event.message,
                                    "response": response_text or "",
                                    "error": error_detail,
                                    "turns": run_turns,
                                    "tokens": run_tokens,
                                    "title": finished_event.title,
                                    "skill_id": finished_event.skill_id,
                                    "session_key": finished_event.session_key,
                                    "created_by": finished_event.created_by,
                                    "has_routing": finished_event.routing is not None,
                                    "retry_count": finished_event.retry_count,
                                }
                                state.event_history.append(history_entry)
                                # Keep only last 50 entries
                                if len(state.event_history) > 50:
                                    state.event_history = state.event_history[-50:]

                                # Re-queue transient failures for one retry
                                if (
                                    status_str == "failed"
                                    and finished_event.retry_count < 1
                                    and is_transient_error(agent_status, error_detail or "")
                                    and len(state.deferred_events) < MAX_DEFERRED_EVENTS
                                ):
                                    retry_event = AgentEvent(
                                        priority=finished_event.priority,
                                        timestamp=datetime.now(timezone.utc),
                                        message=finished_event.message,
                                        session_key=finished_event.session_key,
                                        source=finished_event.source,
                                        routing=finished_event.routing,
                                        event_id=f"{finished_event.event_id}_retry",
                                        title=finished_event.title,
                                        context=finished_event.context,
                                        skill_id=finished_event.skill_id,
                                        created_by=finished_event.created_by,
                                        retry_count=finished_event.retry_count + 1,
                                    )
                                    state.deferred_events.append({
                                        "event": retry_event,
                                        "ready_at": datetime.now(timezone.utc) + timedelta(minutes=RETRY_DELAY_MINUTES),
                                    })
                                    state.events_retried += 1
                                    logger.info(
                                        "Re-queued event '%s' for retry in %d minutes (attempt %d)",
                                        finished_event.event_id, RETRY_DELAY_MINUTES, retry_event.retry_count,
                                    )

                                # Auto-callback for completed delegation runs
                                if finished_event and finished_event.context:
                                    deleg_ctx = finished_event.context
                                    delegation_id = deleg_ctx.get("delegation_id")
                                    callback_agent = deleg_ctx.get("callback_agent")
                                    if delegation_id and callback_agent:
                                        self._fire_delegation_callback(
                                            delegation_id=delegation_id,
                                            callback_agent=callback_agent,
                                            status=agent_status,
                                            result=response_text or "",
                                            error=error_detail,
                                            from_entity=agent_id,
                                        )

                            state.current_run = None
                            state.current_event = None

                    # Route OUTSIDE the lock (plugins do HTTP calls)
                    if route_work and self._router:
                        try:
                            self._router.route(*route_work)
                        except Exception as e:
                            logger.error(f"Routing error for '{agent_id}': {e}")

            except Exception as e:
                logger.error(f"Runtime loop error: {e}")

            time.sleep(1)

    # ========================================================================
    # PRE-CHECK OPTIMIZATION
    # ========================================================================

    def _evaluate_skip_condition(self, condition: str, response_data: Any) -> bool:
        """
        Safely evaluate a skip_if condition against a JSON response.

        Supports: response.field == value, connected by 'and' / 'or'.
        Returns True if the condition is met (i.e. the heartbeat should be SKIPPED).
        On parse error → returns False (don't skip, be safe).
        """
        try:
            # Split on ' or ' first, then ' and ' within each
            or_clauses = [c.strip() for c in condition.split(" or ")]
            for or_clause in or_clauses:
                and_parts = [p.strip() for p in or_clause.split(" and ")]
                all_true = True
                for part in and_parts:
                    match = re.match(
                        r"^response\.([a-zA-Z0-9_.]+)\s*==\s*(.+)$", part.strip()
                    )
                    if not match:
                        return False  # Can't parse → don't skip
                    field_path, expected_str = match.group(1), match.group(2).strip()

                    # Navigate dotted path
                    value = response_data
                    for key in field_path.split("."):
                        if isinstance(value, dict):
                            value = value.get(key)
                        else:
                            return False  # Can't navigate → don't skip

                    # Parse expected value
                    if expected_str in ("true", "True"):
                        expected = True
                    elif expected_str in ("false", "False"):
                        expected = False
                    elif expected_str in ("null", "None"):
                        expected = None
                    elif expected_str.startswith('"') and expected_str.endswith('"'):
                        expected = expected_str[1:-1]
                    else:
                        try:
                            expected = int(expected_str)
                        except ValueError:
                            try:
                                expected = float(expected_str)
                            except ValueError:
                                expected = expected_str

                    if value != expected:
                        all_true = False
                        break
                if all_true:
                    return True
            return False
        except Exception:
            return False  # On any error, don't skip

    def _run_pre_checks(
        self, agent_id: str, due_skills: List[dict]
    ) -> List[dict]:
        """
        Run pre-check HTTP polls for due skills. Returns only skills that
        should NOT be skipped (i.e. passed the pre-check or have no pre-check).

        Each item in due_skills is a dict with keys: name, description,
        heartbeat_md_path, skill_dir, pre_check (optional).
        """
        # Load agent credentials once
        creds_path = self._agents_dir / agent_id / "credentials.json"
        all_creds = {}
        if creds_path.exists():
            try:
                all_creds = json.loads(creds_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        # Flatten all credential sections for interpolation
        flat_creds = {}
        for section_creds in all_creds.values():
            if isinstance(section_creds, dict):
                flat_creds.update(section_creds)

        passed = []
        for skill_info in due_skills:
            pre_check = skill_info.get("pre_check")
            if not pre_check:
                passed.append(skill_info)
                continue

            url = pre_check.get("url", "")
            headers = dict(pre_check.get("headers", {}))
            skip_if = pre_check.get("skip_if", "")

            if not url or not skip_if:
                passed.append(skill_info)
                continue

            # Interpolate {key} placeholders from credentials
            try:
                url = url.format(**flat_creds)
                headers = {k: v.format(**flat_creds) for k, v in headers.items()}
            except KeyError as e:
                logger.warning(f"Pre-check credential missing for '{skill_info['name']}': {e}")
                passed.append(skill_info)
                continue

            # HTTP GET with short timeout
            try:
                resp = requests.get(url, headers=headers, timeout=5)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.debug(f"Pre-check HTTP failed for '{skill_info['name']}': {e}")
                passed.append(skill_info)  # On failure, don't skip
                continue

            # Evaluate skip condition
            if self._evaluate_skip_condition(skip_if, data):
                logger.debug(f"Pre-check skip: '{skill_info['name']}' (condition met)")
            else:
                passed.append(skill_info)

        return passed

    # ========================================================================
    # HEARTBEAT TIMER
    # ========================================================================

    def _discover_heartbeat_skills(self, agent_id: str) -> List[dict]:
        """
        Scan all agent skills for heartbeat.md file existence.

        Returns a list of dicts with keys:
            name, description, heartbeat_md_path, skill_dir, pre_check
        """
        registry = self._manager._get_agent_skill_registry(agent_id)
        if not registry:
            return []

        result = []
        for entry in registry.entries:
            if not entry.resolved_path:
                continue
            skill_dir = Path(entry.resolved_path).parent
            heartbeat_md = skill_dir / "heartbeat.md"
            if heartbeat_md.exists():
                # Carry pre_check from registry entry if present
                pre_check = None
                if entry.heartbeat:
                    pre_check = entry.heartbeat.get("pre_check")
                result.append({
                    "name": entry.name,
                    "description": entry.description,
                    "heartbeat_md_path": str(heartbeat_md),
                    "skill_dir": str(skill_dir),
                    "pre_check": pre_check,
                })
        return result

    def refresh_heartbeat_skills(self, agent_id: str) -> dict:
        """Lightweight refresh of heartbeat_skills on a running agent.

        Called after skill add/remove/restore so the runtime picks up
        new heartbeat.md files without a full stop/start cycle.
        Also clears the cached skill registry and loader so the next
        LLM execution builds a fresh system prompt.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if not state or not state.active:
                return {"status": "not_active", "agent_id": agent_id}

        # Clear cached registry + loader so next execution loads fresh skills
        if agent_id in self._manager._agent_skill_registries:
            del self._manager._agent_skill_registries[agent_id]
        if agent_id in self._manager._agent_skill_loaders:
            del self._manager._agent_skill_loaders[agent_id]

        old_skills = [s["name"] for s in state.heartbeat_skills]
        state.heartbeat_skills = self._discover_heartbeat_skills(agent_id)
        new_skills = [s["name"] for s in state.heartbeat_skills]

        logger.info(
            f"Refreshed heartbeat skills for '{agent_id}': "
            f"{old_skills} -> {new_skills}"
        )
        return {
            "status": "ok",
            "agent_id": agent_id,
            "old_skills": old_skills,
            "new_skills": new_skills,
        }

    def _check_heartbeat_timer(self, state: AgentState, now: datetime) -> None:
        """Check if heartbeat interval has elapsed, maybe enqueue.

        Uses state.heartbeat_skills (cached list) — no filesystem scanning.
        Fires even when no skills have heartbeats (alive signal).
        """
        if not state.heartbeat_enabled or not state.last_heartbeat_time:
            return

        elapsed_minutes = (now - state.last_heartbeat_time).total_seconds() / 60.0
        if elapsed_minutes < state.heartbeat_interval_minutes:
            return

        # Heartbeat is due — update time
        state.last_heartbeat_time = now

        # Drop heartbeat if agent is busy (queue non-empty or running)
        with self._lock:
            if state.queue or state.current_run is not None:
                logger.info(f"Heartbeat skipped for '{state.agent_id}' (busy: queue={len(state.queue)}, running={state.current_run is not None})")
                state.heartbeats_skipped += 1
                try:
                    _agent_obj = self._manager.get_agent(state.agent_id)
                    _hl = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
                    if _hl:
                        _hl.event("heartbeat_skipped", payload={
                            "reason": "busy",
                            "queue_depth": len(state.queue),
                            "running": state.current_run is not None,
                        })
                except Exception:
                    pass
                return

        # Use cached heartbeat_skills list (populated at start, updated on skill add/remove)
        due_skills = list(state.heartbeat_skills)

        # Run pre-checks to filter out skills with nothing to do
        if due_skills:
            original_count = len(due_skills)
            due_skills = self._run_pre_checks(state.agent_id, due_skills)
            skipped = original_count - len(due_skills)
            if skipped:
                state.heartbeats_skipped += skipped
                logger.info(f"Pre-check skipped {skipped} heartbeat(s) for '{state.agent_id}'")
                try:
                    _agent_obj = self._manager.get_agent(state.agent_id)
                    _hl = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
                    if _hl:
                        _hl.event("heartbeat_precheck_skipped", payload={
                            "skipped_count": skipped,
                            "remaining": len(due_skills),
                        })
                except Exception:
                    pass

        # Enqueue ONE event per surviving heartbeat skill (each gets its own agentic loop)
        for skill_info in due_skills:
            event = AgentEvent(
                priority=Priority.LOW,
                timestamp=now,
                message=f"Heartbeat: execute your {skill_info['name']} duties.",
                session_key=None,
                source="heartbeat",
                skill_id=skill_info["name"],
            )
            with self._lock:
                bisect.insort(state.queue, event)

        # ALWAYS enqueue the agent's own HEARTBEAT.md as a separate core_request.
        # This is the agent's "reason for being" -- treated identically to any skill.
        if state.heartbeat_md:
            event = AgentEvent(
                priority=Priority.LOW,
                timestamp=now,
                message=state.heartbeat_md,
                session_key=None,
                source="heartbeat",
                # No skill_id -- this is the agent's own purpose, not a skill
            )
            with self._lock:
                bisect.insort(state.queue, event)

        enqueued_count = len(due_skills) + (1 if state.heartbeat_md else 0)
        state.heartbeats_fired += 1
        skill_names = [s["name"] for s in due_skills]
        logger.info(
            f"Heartbeat FIRED for '{state.agent_id}': "
            f"{enqueued_count} events (skills={skill_names or ['(none)']}"
            f"{', +HEARTBEAT.md' if state.heartbeat_md else ''})"
        )

        # Check for delegation timeout
        agent_dir = str(self._agents_dir / state.agent_id)
        try:
            from .delegation import load_suspended_delegation, delete_suspended_delegation, build_resume_message
            suspended = load_suspended_delegation(agent_dir)
            if suspended:
                deadline_str = suspended.get("deadline", "")
                if deadline_str:
                    deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                    if now >= deadline:
                        delegation_id = suspended["delegation_id"]
                        resume_msg = build_resume_message(
                            {
                                "status": "timeout",
                                "result": "",
                                "error": f"No response after deadline",
                                "from_agent": suspended.get("waiting_on_agent", "unknown"),
                            },
                            suspended,
                        )
                        timeout_event = AgentEvent(
                            priority=Priority.HIGH,
                            timestamp=now,
                            message=resume_msg,
                            source="delegation:timeout",
                            event_id=f"deleg_timeout_{delegation_id}",
                            title=f"Delegation timeout: {suspended.get('waiting_on_agent', '?')}",
                            context={
                                "delegation_result": True,
                                "delegation_id": delegation_id,
                                "status": "timeout",
                                "from_agent": suspended.get("waiting_on_agent", ""),
                            },
                            session_key=f"delegation_{delegation_id}",
                            created_by="delegation",
                        )
                        with self._lock:
                            bisect.insort(state.queue, timeout_event)
                        delete_suspended_delegation(agent_dir)
                        logger.info(
                            "Delegation '%s' timed out for agent '%s'",
                            delegation_id, state.agent_id,
                        )
        except Exception as e:
            logger.debug("Delegation timeout check error for '%s': %s", state.agent_id, e)

    def update_heartbeat_interval(self, agent_id: str, interval_minutes: int) -> dict:
        """
        Update the agent's heartbeat interval at runtime and persist to config.
        """
        if interval_minutes < 1:
            return {"status": "error", "error": "interval_minutes must be >= 1"}

        with self._lock:
            state = self._agents.get(agent_id)
            if not state or not state.active:
                return {"status": "error", "error": "Agent not active"}

            old_interval = state.heartbeat_interval_minutes
            state.heartbeat_interval_minutes = interval_minutes

        # Persist to agent config on disk
        try:
            from .config.loader import get_config_manager
            config_mgr = get_config_manager()
            config = config_mgr.load_agent(agent_id)
            config.heartbeat_interval_minutes = interval_minutes
            config_mgr.save_agent(config)
        except Exception as e:
            logger.warning(f"Failed to persist heartbeat interval to disk: {e}")

        return {
            "status": "ok",
            "old_interval": old_interval,
            "new_interval": interval_minutes,
        }

    def trigger_heartbeat(self, agent_id: str) -> dict:
        """Manually trigger an immediate heartbeat for an agent.

        Uses cached state.heartbeat_skills. Enqueues one event per skill + HEARTBEAT.md.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if not state or not state.active:
                return {"status": "error", "error": "Agent not active"}

        now = datetime.now(timezone.utc)
        due_skills = list(state.heartbeat_skills)

        for skill_info in due_skills:
            event = AgentEvent(
                priority=Priority.LOW,
                timestamp=now,
                message=f"Heartbeat: execute your {skill_info['name']} duties.",
                session_key=None,
                source="heartbeat:manual",
                skill_id=skill_info["name"],
            )
            with self._lock:
                bisect.insort(state.queue, event)

        if state.heartbeat_md:
            event = AgentEvent(
                priority=Priority.LOW,
                timestamp=now,
                message=state.heartbeat_md,
                session_key=None,
                source="heartbeat:manual",
            )
            with self._lock:
                bisect.insort(state.queue, event)

        enqueued = len(due_skills) + (1 if state.heartbeat_md else 0)
        state.heartbeats_fired += 1
        return {
            "status": "queued",
            "skills": [s["name"] for s in due_skills],
            "events_enqueued": enqueued,
        }

    # ========================================================================
    # SCHEDULED TASKS
    # ========================================================================

    def _check_scheduled_tasks(self, state: AgentState, now: datetime) -> None:
        """Reload tasks periodically and enqueue due ones."""
        # Reload tasks from disk periodically
        if state.last_task_reload is None:
            state.last_task_reload = now
        elapsed = (now - state.last_task_reload).total_seconds()
        if elapsed >= TASK_RELOAD_INTERVAL_S:
            self._load_agent_tasks(state)
            state.last_task_reload = now

        # Check each task
        for task_id, task_data in list(state.scheduled_tasks.items()):
            if not task_data.get("enabled", True):
                continue

            next_run = self._calculate_task_next_run(task_data)
            if next_run is None or next_run > now:
                continue

            # Skip if already queued or running for this task
            source = f"task:{task_id}"
            with self._lock:
                already_queued = any(e.source == source for e in state.queue)
                already_running = (
                    state.current_event is not None
                    and state.current_event.source == source
                )
            if already_queued or already_running:
                continue

            event = AgentEvent(
                priority=Priority.NORMAL,
                timestamp=now,
                message="",  # Will be built during execution
                session_key=f"task_{task_id}",
                source=source,
            )

            with self._lock:
                bisect.insort(state.queue, event)

    def _load_agent_tasks(self, state: AgentState) -> None:
        """Load scheduled tasks from an agent's tasks/ directory."""
        tasks_dir = self._agents_dir / state.agent_id / "tasks"
        if not tasks_dir.exists():
            return

        loaded = {}
        for task_dir in tasks_dir.iterdir():
            if not task_dir.is_dir():
                continue
            task_json = task_dir / "task.json"
            if not task_json.exists():
                continue
            try:
                data = json.loads(task_json.read_text(encoding="utf-8"))
                data["_folder_path"] = str(task_dir)
                loaded[data.get("task_id", task_dir.name)] = data
            except Exception:
                pass

        state.scheduled_tasks = loaded

    def _calculate_task_next_run(self, task_data: dict) -> Optional[datetime]:
        """Calculate when a task should next run (interval-based only)."""
        schedule = task_data.get("schedule", {})
        schedule_type = schedule.get("type", "event_only")

        if schedule_type == "event_only":
            return None

        last_run_str = task_data.get("last_run")
        if not last_run_str:
            # Never run → due now
            return datetime.now(timezone.utc)

        try:
            last_run = datetime.fromisoformat(last_run_str)
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

        if schedule_type == "interval":
            interval_s = schedule.get("interval_seconds", 3600)
            from datetime import timedelta
            return last_run + timedelta(seconds=interval_s)

        if schedule_type == "once":
            # One-shot tasks: if already run, never again
            return None

        if schedule_type == "cron":
            expression = schedule.get("expression", "")
            if not expression:
                return None
            try:
                from croniter import croniter
                cron = croniter(expression, last_run)
                return cron.get_next(datetime)
            except ImportError:
                logger.warning("croniter not installed -- cron schedule type unavailable (pip install croniter)")
                return None
            except Exception as e:
                logger.warning(f"Invalid cron expression '{expression}': {e}")
                return None

        return None

    # ========================================================================
    # EXECUTION
    # ========================================================================

    def _execute_event(self, agent_id: str, event: AgentEvent) -> dict:
        """Execute an event in the thread pool.

        Returns a dict with keys:
            response: str or None (LLM response text)
            status: "completed" | "error" | original AgentResult status
            error: str or None (error message if failed)
            turns: int (number of turns executed)
            tokens: int (total tokens used)
        """
        try:
            cancel_check = lambda: not self._running
            if event.source.startswith("task:"):
                resp = self._execute_task_event(agent_id, event, cancel_check)
                return {"response": resp, "status": "completed", "error": None,
                        "turns": 0, "tokens": 0}
            else:
                # Build event context for the identity block
                with self._lock:
                    state = self._agents.get(agent_id)

                event_context = {
                    "source": event.source,
                    "priority": event.priority.name,
                    "session_key": event.session_key,
                    "event_id": getattr(event, "event_id", None),
                    "skill_id": event.skill_id,  # Pass skill_id through to agent.run()
                    "triggered_skills": [event.skill_id] if event.skill_id else [],
                    "agent_status": "started" if (state and state.active) else "stopped",
                }

                # All events go through run_agent() — skill_id in event_context
                # is extracted by run_agent() and passed to agent.run(skill_id=...)
                result = self._manager.run_agent(
                    agent_id=agent_id,
                    message=event.message,
                    session_id=event.session_key,
                    save_output=True,
                    event_context=event_context,
                    cancel_check=cancel_check,
                )

                # Process agent-created events
                if result.pending_events:
                    self._process_pending_events(agent_id, result.pending_events)

                return {
                    "response": result.final_response,
                    "status": result.status,
                    "error": result.error,
                    "turns": result.turns,
                    "tokens": result.total_tokens,
                }
        except Exception as e:
            logger.error(f"Event execution error for '{agent_id}': {e}", exc_info=True)
            return {"response": None, "status": "error",
                    "error": str(e), "turns": 0, "tokens": 0}

    def _process_pending_events(
        self, agent_id: str, pending_events: List[dict]
    ) -> None:
        """Process events created by the agent during execution.

        Supports cross-agent routing: if an event has ``target_agent``,
        it is queued on that agent instead of the originator.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if not state or not state.active:
                return

        for evt_data in pending_events:
            target_agent = evt_data.get("target_agent", agent_id)

            priority_str = evt_data.get("priority", "normal").upper()
            try:
                priority = Priority[priority_str]
            except KeyError:
                priority = Priority.NORMAL

            # Build context header to prepend to message
            message = evt_data["message"]
            ctx = evt_data.get("context")
            if ctx:
                ctx_header = "\n".join(f"- {k}: {v}" for k, v in ctx.items())
                message = f"[Event Context]\n{ctx_header}\n\n{message}"

            agent_event = AgentEvent(
                priority=priority,
                timestamp=datetime.now(timezone.utc),
                message=message,
                event_id=evt_data["event_id"],
                title=evt_data.get("title"),
                context=ctx,
                skill_id=evt_data.get("skill_id"),
                status=evt_data["status"],
                created_by=evt_data.get("created_by", "agent"),
                source=f"agent:{agent_id}",
                session_key=f"event_{evt_data['event_id']}",  # Isolated session
            )

            with self._lock:
                target_state = self._agents.get(target_agent)
                if not target_state or not target_state.active:
                    logger.warning(
                        "Target agent '%s' not active, dropping event '%s'",
                        target_agent, evt_data.get("title", "untitled"),
                    )
                    continue

                if evt_data["status"] == "pending_approval":
                    target_state.pending_events.append(agent_event)
                    logger.info(
                        "Agent '%s' created pending event on '%s': %s",
                        agent_id, target_agent, evt_data.get("title", "untitled"),
                    )

                    # HiveLoop: request approval
                    try:
                        _agent_obj = self._manager.get_agent(target_agent)
                        _hl_agent = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
                        if _hl_agent:
                            _hl_agent.request_approval(
                                f"Approval needed: {evt_data.get('title', 'untitled')}",
                                approver="human",
                            )
                    except Exception:
                        pass

                else:
                    bisect.insort(target_state.queue, agent_event)
                    logger.info(
                        "Agent '%s' queued event on '%s': %s",
                        agent_id, target_agent, evt_data.get("title", "untitled"),
                    )

    def approve_event(self, agent_id: str, event_id: str) -> dict:
        """Move a pending event to the active queue."""
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return {"status": "error", "error": "Agent not found"}

            # Find the event in pending_events
            target = None
            for i, evt in enumerate(state.pending_events):
                if evt.event_id == event_id:
                    target = state.pending_events.pop(i)
                    break

            if not target:
                return {"status": "error", "error": "Event not found in pending list"}

            target.status = "active"
            bisect.insort(state.queue, target)

        logger.info(f"Event '{event_id}' approved for agent '{agent_id}'")

        # HiveLoop: approval received
        try:
            _agent_obj = self._manager.get_agent(agent_id)
            _hl_agent = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
            if _hl_agent:
                _hl_agent.approval_received(
                    f"Event '{event_id}' approved by operator",
                    approved_by="human",
                    decision="approved",
                )
        except Exception:
            pass

        return {"status": "ok", "event_id": event_id}

    def drop_event(self, agent_id: str, event_id: str) -> dict:
        """Drop a pending event."""
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return {"status": "error", "error": "Agent not found"}

            for i, evt in enumerate(state.pending_events):
                if evt.event_id == event_id:
                    state.pending_events.pop(i)
                    logger.info(f"Event '{event_id}' dropped for agent '{agent_id}'")

                    # HiveLoop: approval rejected (dropped)
                    try:
                        _agent_obj = self._manager.get_agent(agent_id)
                        _hl_agent = getattr(_agent_obj, "_hiveloop", None) if _agent_obj else None
                        if _hl_agent:
                            _hl_agent.approval_received(
                                f"Event '{event_id}' dropped by operator",
                                approved_by="human",
                                decision="rejected",
                            )
                    except Exception:
                        pass

                    return {"status": "ok", "event_id": event_id}

        return {"status": "error", "error": "Event not found in pending list"}

    def get_pending_events(self, agent_id: str) -> List[dict]:
        """Get pending events awaiting approval for an agent."""
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return []
            return [
                {
                    "event_id": e.event_id,
                    "title": e.title,
                    "message": e.message,
                    "message_preview": e.message[:150],
                    "priority": e.priority.name,
                    "skill_id": e.skill_id,
                    "created_by": e.created_by,
                    "status": e.status,
                    "session_key": e.session_key,
                    "context": e.context,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in state.pending_events
            ]

    def _execute_task_event(self, agent_id: str, event: AgentEvent, cancel_check=None) -> Optional[str]:
        """Execute a scheduled-task event via the task executor."""
        task_id = event.source.replace("task:", "")

        with self._lock:
            state = self._agents.get(agent_id)
            task_data = state.scheduled_tasks.get(task_id) if state else None

        if not task_data:
            logger.warning(f"Task '{task_id}' not found for agent '{agent_id}'")
            return None

        folder_path = Path(task_data.get("_folder_path", ""))
        task_md_path = folder_path / "task.md"

        if not task_md_path.exists():
            logger.warning(f"task.md not found for task '{task_id}'")
            return None

        task_content = task_md_path.read_text(encoding="utf-8")
        if not task_content.strip():
            return None

        started_at = datetime.now(timezone.utc)

        # Use the task executor if available, otherwise run directly
        if self._task_executor:
            skill_id = task_data.get("skill_id")
            context = task_data.get("context")
            try:
                result = self._task_executor(
                    task_id, task_content, agent_id, skill_id, context
                )
            except Exception as e:
                result = {"status": "error", "error": str(e)}
        else:
            # Fallback: run directly through agent manager
            session_id = f"task_{task_id}_{started_at.strftime('%Y%m%d_%H%M%S')}"
            agent_result = self._manager.run_agent(
                agent_id=agent_id,
                message=f"You are executing a scheduled task.\n\nTASK INSTRUCTIONS:\n{task_content}\n\n---\nTask ID: {task_id}\nExecute the instructions above.",
                session_id=session_id,
                save_output=True,
                cancel_check=cancel_check,
            )
            result = {
                "status": agent_result.status,
                "response": agent_result.final_response,
            }

        completed_at = datetime.now(timezone.utc)

        # Update task after run
        self._update_task_after_run(task_data, result, started_at, completed_at)

        return result.get("response")

    def _update_task_after_run(
        self,
        task_data: dict,
        result: dict,
        started_at: datetime,
        completed_at: datetime,
    ) -> None:
        """Update task.json and save run history on disk."""
        folder_path = Path(task_data.get("_folder_path", ""))
        task_json_path = folder_path / "task.json"

        # Update in-memory
        task_data["last_run"] = completed_at.isoformat()
        task_data["run_count"] = task_data.get("run_count", 0) + 1

        # Disable one-shot tasks
        schedule = task_data.get("schedule", {})
        if schedule.get("type") == "once":
            task_data["enabled"] = False

        # Write updated task.json
        try:
            # Read current to preserve fields we don't track
            if task_json_path.exists():
                on_disk = json.loads(task_json_path.read_text(encoding="utf-8"))
            else:
                on_disk = {}
            on_disk["last_run"] = task_data["last_run"]
            on_disk["run_count"] = task_data["run_count"]
            if "enabled" in task_data:
                on_disk["enabled"] = task_data["enabled"]
            task_json_path.write_text(
                json.dumps(on_disk, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"Failed to update task.json: {e}")

        # Save run history
        runs_dir = folder_path / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_file = runs_dir / f"{started_at.strftime('%Y%m%d_%H%M%S')}.json"
        run_record = {
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_ms": int((completed_at - started_at).total_seconds() * 1000),
            "trigger": {"type": "runtime_scheduled"},
            "result": result,
        }
        try:
            run_file.write_text(json.dumps(run_record, indent=2), encoding="utf-8")
            # Cleanup: keep only last 50 run files
            all_runs = sorted(runs_dir.glob("*.json"))
            if len(all_runs) > 50:
                for old in all_runs[:-50]:
                    old.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Failed to save run history: {e}")

    # ========================================================================
    # HELPERS
    # ========================================================================

    def _write_heartbeat(self) -> None:
        """Write runtime heartbeat file for liveness detection."""
        try:
            data = {
                "status": "running",
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            }
            self._heartbeat_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def _remove_heartbeat(self) -> None:
        """Remove heartbeat file on stop."""
        try:
            if self._heartbeat_file.exists():
                self._heartbeat_file.unlink()
        except Exception:
            pass

    def _save_runtime_state(self) -> None:
        """Write .runtime_state.json with active agent list for admin panel."""
        try:
            with self._lock:
                active = [
                    {
                        "agent_id": aid,
                        "started_at": s.started_at.isoformat() if s.started_at else None,
                        "heartbeat_enabled": s.heartbeat_enabled,
                        "heartbeat_interval_minutes": s.heartbeat_interval_minutes,
                        "queue_depth": len(s.queue),
                    }
                    for aid, s in self._agents.items()
                    if s.active
                ]
            data = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "active_agents": active,
            }
            self._state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ========================================================================
    # QUEUE PERSISTENCE
    # ========================================================================

    def _save_agent_queue(self, agent_id: str, state: AgentState) -> int:
        """
        Serialize the agent's queue to disk. Returns count saved.
        Skips the currently-running event (it will finish on its own).
        """
        if not state.queue:
            return 0

        queue_file = self._agents_dir / agent_id / ".saved_queue.json"
        events = []
        for e in state.queue:
            event_dict = {
                "priority": e.priority.value,
                "timestamp": e.timestamp.isoformat(),
                "message": e.message,
                "session_key": e.session_key,
                "source": e.source,
                "event_id": e.event_id,
                "title": e.title,
                "context": e.context,
                "skill_id": e.skill_id,
                "status": e.status,
                "created_by": e.created_by,
            }
            # Only serialize routing if it's a simple dict-like object
            if e.routing and hasattr(e.routing, '__dict__'):
                try:
                    event_dict["routing"] = {
                        k: v for k, v in e.routing.__dict__.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    }
                except Exception:
                    pass
            events.append(event_dict)

        try:
            queue_file.write_text(json.dumps(events, indent=2), encoding="utf-8")
            logger.info(f"Saved {len(events)} queued events for '{agent_id}'")
            return len(events)
        except Exception as e:
            logger.error(f"Failed to save queue for '{agent_id}': {e}")
            return 0

    def _restore_agent_queue(self, agent_id: str, state: AgentState) -> int:
        """
        Restore saved queue from disk into the agent's state.
        Deletes the file after loading. Returns count restored.
        """
        queue_file = self._agents_dir / agent_id / ".saved_queue.json"
        if not queue_file.exists():
            return 0

        try:
            events_data = json.loads(queue_file.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Failed to read saved queue for '{agent_id}': {e}")
            return 0

        count = 0
        for ed in events_data:
            try:
                event = AgentEvent(
                    priority=Priority(ed["priority"]),
                    timestamp=datetime.fromisoformat(ed["timestamp"]),
                    message=ed["message"],
                    session_key=ed.get("session_key"),
                    source=ed.get("source", "restored"),
                    event_id=ed.get("event_id", f"evt_{uuid.uuid4().hex[:12]}"),
                    title=ed.get("title"),
                    context=ed.get("context"),
                    skill_id=ed.get("skill_id"),
                    status=ed.get("status", "active"),
                    created_by=ed.get("created_by", "system"),
                )
                bisect.insort(state.queue, event)
                count += 1
            except Exception:
                pass

        # Delete the file after restoring
        try:
            queue_file.unlink()
        except Exception:
            pass

        if count:
            logger.info(f"Restored {count} queued events for '{agent_id}'")
        return count

    # ========================================================================
    # QUEUE OBSERVABILITY
    # ========================================================================

    def get_agent_queue(self, agent_id: str) -> list:
        """Return serialized queue contents for an agent."""
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return []
            return [
                {
                    "event_id": e.event_id,
                    "priority": e.priority.name,
                    "timestamp": e.timestamp.isoformat(),
                    "source": e.source,
                    "session_key": e.session_key,
                    "message": e.message,
                    "message_preview": e.message[:150],
                    "title": e.title,
                    "skill_id": e.skill_id,
                    "status": e.status,
                    "created_by": e.created_by,
                    "has_routing": e.routing is not None,
                }
                for e in state.queue
            ]

    def get_agent_event_history(self, agent_id: str, limit: int = 20) -> list:
        """Return the last N completed events for an agent (most recent first).

        Each entry includes message_preview and response_preview convenience
        fields so the list endpoint doesn't send full text on every refresh.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return []
            entries = list(reversed(state.event_history[-limit:]))
            for e in entries:
                e["message_preview"] = (e.get("message") or "")[:150]
                e["response_preview"] = (e.get("response") or "")[:250]
            return entries

    def get_event_detail(self, agent_id: str, event_id: str) -> Optional[dict]:
        """Find a single event by ID across all states.

        Searches current_event, queue, pending_events, and event_history.
        Returns full data with a ``location`` field.
        """
        with self._lock:
            state = self._agents.get(agent_id)
            if not state:
                return None

            # Check current_event (processing)
            if state.current_event and state.current_event.event_id == event_id:
                ce = state.current_event
                return {
                    "location": "processing",
                    "event_id": ce.event_id,
                    "source": ce.source,
                    "priority": ce.priority.name,
                    "title": ce.title,
                    "skill_id": ce.skill_id,
                    "message": ce.message,
                    "session_key": ce.session_key,
                    "created_by": ce.created_by,
                    "status": "running",
                    "timestamp": ce.timestamp.isoformat(),
                    "context": ce.context,
                    "has_routing": ce.routing is not None,
                }

            # Check queue
            for e in state.queue:
                if e.event_id == event_id:
                    return {
                        "location": "queue",
                        "event_id": e.event_id,
                        "source": e.source,
                        "priority": e.priority.name,
                        "title": e.title,
                        "skill_id": e.skill_id,
                        "message": e.message,
                        "session_key": e.session_key,
                        "created_by": e.created_by,
                        "status": e.status,
                        "timestamp": e.timestamp.isoformat(),
                        "context": e.context,
                        "has_routing": e.routing is not None,
                    }

            # Check pending_events
            for e in state.pending_events:
                if e.event_id == event_id:
                    return {
                        "location": "pending",
                        "event_id": e.event_id,
                        "source": e.source,
                        "priority": e.priority.name,
                        "title": e.title,
                        "skill_id": e.skill_id,
                        "message": e.message,
                        "session_key": e.session_key,
                        "created_by": e.created_by,
                        "status": e.status,
                        "timestamp": e.timestamp.isoformat(),
                        "context": e.context,
                        "has_routing": e.routing is not None,
                    }

            # Check event_history
            for h in state.event_history:
                if h.get("event_id") == event_id:
                    result = dict(h)
                    result["location"] = "history"
                    return result

        return None

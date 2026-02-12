"""Agent and Task classes.

Spec Sections 9–12: agent registration, heartbeat, task context manager,
action tracking decorator, manual events, convenience methods.
"""

import contextvars
import functools
import inspect
import logging
import platform
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("hiveloop.agent")

# Context var for action nesting (spec Section 11.2)
_current_action_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_action_id", default=None
)


class HiveLoopError(Exception):
    """Raised on SDK misuse (e.g. task.event() outside task context)."""
    pass


class HiveLoopConfigError(HiveLoopError):
    """Raised on invalid configuration."""
    pass


class Task:
    """Task context — wraps a logical unit of agent work.

    Can be used as a context manager or via start_task()/complete()/fail().
    """

    def __init__(self, agent: "Agent", task_id: str, type: str | None = None,
                 task_run_id: str | None = None, correlation_id: str | None = None):
        self.agent = agent
        self.task_id = task_id
        self.type = type
        self.task_run_id = task_run_id or str(uuid.uuid4())
        self.correlation_id = correlation_id
        self._start_time: float | None = None
        self._completed = False
        self._completion_payload = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.fail(exception=exc_val)
        else:
            self.complete()
        return False  # never swallow exceptions

    def _start(self):
        """Emit task_started, set as active task on thread."""
        self._start_time = time.monotonic()
        self.agent._active_task.task = self
        self.agent._emit_event(
            event_type="task_started",
            task_id=self.task_id,
            task_type=self.type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            payload={"summary": f"Task {self.task_id} started"},
        )

    def complete(self, status: str = "success", payload: dict | None = None):
        """Emit task_completed. Clears task context."""
        if self._completed:
            return
        self._completed = True
        dur_ms = self._duration_ms()
        p = payload or self._completion_payload or {}
        p.setdefault("summary", f"Task {self.task_id} completed")
        self.agent._emit_event(
            event_type="task_completed",
            task_id=self.task_id,
            task_type=self.type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            status=status,
            duration_ms=dur_ms,
            payload=p,
        )
        self.agent._active_task.task = None

    def fail(self, exception: BaseException | None = None, payload: dict | None = None):
        """Emit task_failed. Clears task context."""
        if self._completed:
            return
        self._completed = True
        dur_ms = self._duration_ms()
        p = payload or {}
        if exception:
            p["exception_type"] = type(exception).__name__
            p["exception_message"] = str(exception)
        p.setdefault("summary", f"Task {self.task_id} failed")
        self.agent._emit_event(
            event_type="task_failed",
            task_id=self.task_id,
            task_type=self.type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            status="failure",
            duration_ms=dur_ms,
            payload=p,
        )
        self.agent._active_task.task = None

    def event(self, event_type: str, payload: dict | None = None, severity: str | None = None,
              parent_event_id: str | None = None):
        """Emit a task-scoped event."""
        self.agent._emit_event(
            event_type=event_type,
            task_id=self.task_id,
            task_type=self.type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            severity=severity,
            parent_event_id=parent_event_id,
            payload=payload,
        )

    def escalate(self, reason: str, assigned_to: str | None = None):
        """Convenience: emit escalated event."""
        self.event("escalated", payload={
            "summary": reason,
            "data": {"assigned_to": assigned_to},
        })

    def request_approval(self, approver: str, reason: str | None = None):
        """Convenience: emit approval_requested event."""
        self.event("approval_requested", payload={
            "summary": reason or f"Approval requested from {approver}",
            "data": {"approver": approver},
        })

    def approval_received(self, approved_by: str, decision: str = "approved"):
        """Convenience: emit approval_received event."""
        self.event("approval_received", payload={
            "summary": f"Approval {decision} by {approved_by}",
            "data": {"approved_by": approved_by, "decision": decision},
        })

    def retry(self, attempt: int, reason: str | None = None, backoff_seconds: float | None = None):
        """Convenience: emit retry_started event."""
        self.event("retry_started", payload={
            "summary": reason or f"Retry attempt {attempt}",
            "data": {"attempt": attempt, "backoff_seconds": backoff_seconds},
        })

    def set_payload(self, payload: dict):
        """Set payload to include in the task's completion event."""
        self._completion_payload = payload

    def _duration_ms(self) -> int | None:
        if self._start_time is None:
            return None
        return int((time.monotonic() - self._start_time) * 1000)


class _ActionContext:
    """Context manager for inline action tracking (agent.track_context)."""

    def __init__(self, agent: "Agent", action_name: str):
        self.agent = agent
        self.action_name = action_name
        self.action_id = str(uuid.uuid4())
        self._start_time: float | None = None
        self._token = None
        self._payload = None

    def __enter__(self):
        self._start_time = time.monotonic()
        parent_action_id = _current_action_id.get()
        self._token = _current_action_id.set(self.action_id)

        task = getattr(self.agent._active_task, "task", None)
        self.agent._emit_event(
            event_type="action_started",
            task_id=task.task_id if task else None,
            task_type=task.type if task else None,
            task_run_id=task.task_run_id if task else None,
            action_id=self.action_id,
            parent_action_id=parent_action_id,
            payload={"action_name": self.action_name},
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        dur_ms = int((time.monotonic() - self._start_time) * 1000) if self._start_time else None
        task = getattr(self.agent._active_task, "task", None)

        if exc_type is not None:
            payload = {"action_name": self.action_name,
                       "exception_type": exc_type.__name__,
                       "exception_message": str(exc_val)}
            if self._payload:
                payload.update(self._payload)
            self.agent._emit_event(
                event_type="action_failed",
                task_id=task.task_id if task else None,
                task_type=task.type if task else None,
                task_run_id=task.task_run_id if task else None,
                action_id=self.action_id,
                parent_action_id=_current_action_id.get(),
                status="failure",
                duration_ms=dur_ms,
                payload=payload,
            )
        else:
            payload = {"action_name": self.action_name}
            if self._payload:
                payload.update(self._payload)
            self.agent._emit_event(
                event_type="action_completed",
                task_id=task.task_id if task else None,
                task_type=task.type if task else None,
                task_run_id=task.task_run_id if task else None,
                action_id=self.action_id,
                parent_action_id=_current_action_id.get(),
                status="success",
                duration_ms=dur_ms,
                payload=payload,
            )

        _current_action_id.reset(self._token)
        return False  # never swallow

    def set_payload(self, payload: dict):
        self._payload = payload


class Agent:
    """Registered agent — emits events, manages tasks, tracks actions.

    Spec Sections 9–11.
    """

    def __init__(self, client, agent_id: str, type: str = "general",
                 version: str | None = None, framework: str = "custom",
                 heartbeat_interval: int = 30, stuck_threshold: int = 300):
        self._client = client
        self.agent_id = agent_id
        self.type = type
        self.version = version
        self.framework = framework
        self.heartbeat_interval = heartbeat_interval
        self.stuck_threshold = stuck_threshold

        # Thread-local task context (spec Section 10.2)
        self._active_task = threading.local()

        # Envelope sent with every batch
        self._envelope = {
            "agent_id": agent_id,
            "agent_type": type,
            "agent_version": version,
            "framework": framework,
            "runtime": f"python-{platform.python_version()}",
            "sdk_version": "hiveloop-0.1.0",
            "environment": client._environment,
            "group": client._group,
        }

        # Emit agent_registered event
        self._emit_event(
            event_type="agent_registered",
            payload={
                "summary": f"Agent {agent_id} registered",
                "data": {
                    "type": type,
                    "version": version,
                    "framework": framework,
                    "stuck_threshold": stuck_threshold,
                },
            },
        )

        # Start heartbeat thread (spec Section 9.2)
        self._hb_stop = threading.Event()
        if heartbeat_interval > 0:
            self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._hb_thread.start()

    def task(self, task_id: str, type: str | None = None,
             task_run_id: str | None = None, correlation_id: str | None = None) -> Task:
        """Start a task (context manager). Spec Section 10.1."""
        t = Task(self, task_id, type=type, task_run_id=task_run_id, correlation_id=correlation_id)
        t._start()
        return t

    def start_task(self, task_id: str, type: str | None = None,
                   task_run_id: str | None = None, correlation_id: str | None = None) -> Task:
        """Start a task without context manager. Spec Section 10.3."""
        t = Task(self, task_id, type=type, task_run_id=task_run_id, correlation_id=correlation_id)
        t._start()
        return t

    def track(self, action_name: str):
        """Decorator for function tracking. Spec Section 11.1.

        Works with both sync and async functions.
        """
        def decorator(fn):
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def async_wrapper(*args, **kwargs):
                    action_id = str(uuid.uuid4())
                    parent_action_id = _current_action_id.get()
                    token = _current_action_id.set(action_id)
                    task = getattr(self._active_task, "task", None)

                    qualified_name = f"{fn.__module__}.{fn.__qualname__}"

                    self._emit_event(
                        event_type="action_started",
                        task_id=task.task_id if task else None,
                        task_type=task.type if task else None,
                        task_run_id=task.task_run_id if task else None,
                        action_id=action_id,
                        parent_action_id=parent_action_id,
                        payload={"action_name": action_name, "function": qualified_name},
                    )

                    start = time.monotonic()
                    try:
                        result = await fn(*args, **kwargs)
                        dur_ms = int((time.monotonic() - start) * 1000)
                        self._emit_event(
                            event_type="action_completed",
                            task_id=task.task_id if task else None,
                            task_type=task.type if task else None,
                            task_run_id=task.task_run_id if task else None,
                            action_id=action_id,
                            parent_action_id=parent_action_id,
                            status="success",
                            duration_ms=dur_ms,
                            payload={"action_name": action_name, "function": qualified_name},
                        )
                        return result
                    except Exception as e:
                        dur_ms = int((time.monotonic() - start) * 1000)
                        self._emit_event(
                            event_type="action_failed",
                            task_id=task.task_id if task else None,
                            task_type=task.type if task else None,
                            task_run_id=task.task_run_id if task else None,
                            action_id=action_id,
                            parent_action_id=parent_action_id,
                            status="failure",
                            duration_ms=dur_ms,
                            payload={
                                "action_name": action_name,
                                "function": qualified_name,
                                "exception_type": type(e).__name__,
                                "exception_message": str(e),
                            },
                        )
                        raise
                    finally:
                        _current_action_id.reset(token)

                return async_wrapper
            else:
                @functools.wraps(fn)
                def sync_wrapper(*args, **kwargs):
                    action_id = str(uuid.uuid4())
                    parent_action_id = _current_action_id.get()
                    token = _current_action_id.set(action_id)
                    task = getattr(self._active_task, "task", None)

                    qualified_name = f"{fn.__module__}.{fn.__qualname__}"

                    self._emit_event(
                        event_type="action_started",
                        task_id=task.task_id if task else None,
                        task_type=task.type if task else None,
                        task_run_id=task.task_run_id if task else None,
                        action_id=action_id,
                        parent_action_id=parent_action_id,
                        payload={"action_name": action_name, "function": qualified_name},
                    )

                    start = time.monotonic()
                    try:
                        result = fn(*args, **kwargs)
                        dur_ms = int((time.monotonic() - start) * 1000)
                        self._emit_event(
                            event_type="action_completed",
                            task_id=task.task_id if task else None,
                            task_type=task.type if task else None,
                            task_run_id=task.task_run_id if task else None,
                            action_id=action_id,
                            parent_action_id=parent_action_id,
                            status="success",
                            duration_ms=dur_ms,
                            payload={"action_name": action_name, "function": qualified_name},
                        )
                        return result
                    except Exception as e:
                        dur_ms = int((time.monotonic() - start) * 1000)
                        self._emit_event(
                            event_type="action_failed",
                            task_id=task.task_id if task else None,
                            task_type=task.type if task else None,
                            task_run_id=task.task_run_id if task else None,
                            action_id=action_id,
                            parent_action_id=parent_action_id,
                            status="failure",
                            duration_ms=dur_ms,
                            payload={
                                "action_name": action_name,
                                "function": qualified_name,
                                "exception_type": type(e).__name__,
                                "exception_message": str(e),
                            },
                        )
                        raise
                    finally:
                        _current_action_id.reset(token)

                return sync_wrapper
        return decorator

    def track_context(self, action_name: str) -> _ActionContext:
        """Context manager for inline action tracking. Spec Section 11.4."""
        return _ActionContext(self, action_name)

    def event(self, event_type: str, payload: dict | None = None, severity: str | None = None,
              parent_event_id: str | None = None):
        """Emit an agent-level event (no task context). Spec Section 12.2."""
        self._emit_event(
            event_type=event_type,
            severity=severity,
            parent_event_id=parent_event_id,
            payload=payload,
        )

    def stop_heartbeat(self):
        """Stop the heartbeat thread."""
        self._hb_stop.set()

    def _heartbeat_loop(self):
        """Background heartbeat. Spec Section 9.2."""
        while not self._hb_stop.wait(timeout=self.heartbeat_interval):
            self._emit_event(event_type="heartbeat")

    def _emit_event(self, event_type: str, **kwargs):
        """Build event dict and enqueue via transport."""
        now = datetime.now(timezone.utc).isoformat()
        event = {
            "event_id": str(uuid.uuid4()),
            "timestamp": now,
            "event_type": event_type,
            "task_id": kwargs.get("task_id"),
            "task_type": kwargs.get("task_type"),
            "task_run_id": kwargs.get("task_run_id"),
            "correlation_id": kwargs.get("correlation_id"),
            "action_id": kwargs.get("action_id"),
            "parent_action_id": kwargs.get("parent_action_id"),
            "parent_event_id": kwargs.get("parent_event_id"),
            "severity": kwargs.get("severity"),
            "status": kwargs.get("status"),
            "duration_ms": kwargs.get("duration_ms"),
            "payload": kwargs.get("payload"),
        }
        # Strip None values to keep payloads clean
        event = {k: v for k, v in event.items() if v is not None}
        # But always include these keys
        for key in ("event_id", "timestamp", "event_type"):
            if key not in event:
                event[key] = None

        self._client._transport.enqueue(event, self._envelope)

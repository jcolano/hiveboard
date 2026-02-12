"""HiveLoop Agent, Task, and action tracking.

Contains:
- Agent: registration, heartbeat, event emission, convenience methods
- Task: context manager lifecycle, manual lifecycle, scoped events
- @agent.track decorator and track_context for action nesting
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import logging
import platform
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from shared.enums import EventType, PayloadKind, Severity, SEVERITY_DEFAULTS

if TYPE_CHECKING:
    from ._transport import Transport

logger = logging.getLogger("hiveloop.agent")

# ContextVar for action nesting (works across threads and async)
_current_action_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_action_id", default=None
)

# SDK version constant
SDK_VERSION = "hiveloop-0.1.0"


def _utcnow_iso() -> str:
    """UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _new_id() -> str:
    """Generate a new UUID4 string."""
    return str(uuid.uuid4())


def _strip_none(d: dict[str, Any]) -> dict[str, Any]:
    """Remove None values from a dict, but always keep event_id, timestamp, event_type."""
    keep_always = {"event_id", "timestamp", "event_type"}
    return {k: v for k, v in d.items() if v is not None or k in keep_always}


class HiveLoopError(Exception):
    """Raised for SDK misuse (e.g. calling task-scoped methods outside a task)."""


class Task:
    """A task execution context.

    Can be used as a context manager or manually via start_task/complete/fail.
    """

    def __init__(
        self,
        agent: Agent,
        task_id: str,
        project_id: str | None = None,
        task_type: str | None = None,
        task_run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        self._agent = agent
        self.task_id = task_id
        self.project_id = project_id
        self.task_type = task_type
        self.task_run_id = task_run_id or _new_id()
        self.correlation_id = correlation_id
        self._start_time: float | None = None
        self._completed = False
        self._payload: dict[str, Any] | None = None
        # Plan state tracking (C1.4)
        self._plan_total_steps: int | None = None
        self._plan_revision: int = 0

    # -- Context manager --

    def __enter__(self) -> Task:
        self._start()
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> bool:
        if exc_val is not None:
            self._fail_internal(exc_val)
        else:
            self._complete_internal()
        # Never swallow exceptions
        return False

    # -- Manual lifecycle --

    def _start(self) -> None:
        """Emit task_started and set as active task."""
        self._start_time = time.monotonic()
        # Set as active task on thread-local
        self._agent._set_active_task(self)
        self._agent._emit_event(
            event_type=EventType.TASK_STARTED,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            payload={"summary": f"Task {self.task_id} started"},
        )

    def complete(self, status: str = "success", payload: dict[str, Any] | None = None) -> None:
        """Manually complete the task."""
        if payload:
            self._payload = payload
        self._complete_internal(status=status)

    def fail(self, exception: BaseException | None = None, payload: dict[str, Any] | None = None) -> None:
        """Manually fail the task."""
        if payload:
            self._payload = payload
        self._fail_internal(exception)

    def set_payload(self, payload: dict[str, Any]) -> None:
        """Set payload data for the completion event."""
        self._payload = payload

    def _complete_internal(self, status: str = "success") -> None:
        if self._completed:
            return
        self._completed = True
        duration_ms = self._duration_ms()
        payload: dict[str, Any] = {"summary": f"Task {self.task_id} completed"}
        if self._payload:
            payload.update(self._payload)
        self._agent._emit_event(
            event_type=EventType.TASK_COMPLETED,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            status=status,
            duration_ms=duration_ms,
            payload=payload,
        )
        self._agent._clear_active_task()

    def _fail_internal(self, exception: BaseException | None = None) -> None:
        if self._completed:
            return
        self._completed = True
        duration_ms = self._duration_ms()
        payload: dict[str, Any] = {"summary": f"Task {self.task_id} failed"}
        if exception:
            payload["exception_type"] = type(exception).__name__
            payload["exception_message"] = str(exception)
        if self._payload:
            payload.update(self._payload)
        self._agent._emit_event(
            event_type=EventType.TASK_FAILED,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            status="failure",
            duration_ms=duration_ms,
            payload=payload,
        )
        self._agent._clear_active_task()

    def _duration_ms(self) -> int | None:
        if self._start_time is None:
            return None
        return int((time.monotonic() - self._start_time) * 1000)

    # -- Task-scoped events --

    def event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        severity: str | None = None,
        parent_event_id: str | None = None,
    ) -> None:
        """Emit a task-scoped event."""
        self._agent._emit_event(
            event_type=event_type,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            severity=severity,
            parent_event_id=parent_event_id,
            payload=payload,
        )

    # -- Convenience methods (C1.4) --

    def llm_call(
        self,
        name: str,
        model: str,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost: float | None = None,
        duration_ms: int | None = None,
        prompt_preview: str | None = None,
        response_preview: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record an LLM call within this task."""
        data: dict[str, Any] = {"name": name, "model": model}
        if tokens_in is not None:
            data["tokens_in"] = tokens_in
        if tokens_out is not None:
            data["tokens_out"] = tokens_out
        if cost is not None:
            data["cost"] = cost
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if prompt_preview is not None:
            data["prompt_preview"] = prompt_preview
        if response_preview is not None:
            data["response_preview"] = response_preview
        if metadata is not None:
            data["metadata"] = metadata

        summary = _build_llm_summary(name, model, tokens_in, tokens_out, cost)
        payload: dict[str, Any] = {
            "kind": PayloadKind.LLM_CALL,
            "summary": summary,
            "data": data,
            "tags": ["llm"],
        }
        # Inherit action context if inside a tracked function
        action_id = _current_action_id.get()
        self._agent._emit_event(
            event_type=EventType.CUSTOM,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            action_id=action_id,
            payload=payload,
        )

    def plan(
        self,
        goal: str,
        steps: list[str],
        *,
        revision: int = 0,
    ) -> None:
        """Record a plan created for this task."""
        self._plan_total_steps = len(steps)
        self._plan_revision = revision
        step_data = [{"index": i, "description": s} for i, s in enumerate(steps)]
        payload: dict[str, Any] = {
            "kind": PayloadKind.PLAN_CREATED,
            "summary": goal,
            "data": {"steps": step_data, "revision": revision},
            "tags": ["plan", "created"],
        }
        self._agent._emit_event(
            event_type=EventType.CUSTOM,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            payload=payload,
        )

    def plan_step(
        self,
        step_index: int,
        action: str,
        summary: str,
        *,
        total_steps: int | None = None,
        turns: int | None = None,
        tokens: int | None = None,
        plan_revision: int | None = None,
    ) -> None:
        """Record a plan step update for this task."""
        ts = total_steps if total_steps is not None else self._plan_total_steps
        rev = plan_revision if plan_revision is not None else self._plan_revision
        data: dict[str, Any] = {
            "step_index": step_index,
            "total_steps": ts,
            "action": action,
        }
        if turns is not None:
            data["turns"] = turns
        if tokens is not None:
            data["tokens"] = tokens
        if rev is not None:
            data["plan_revision"] = rev

        auto_summary = f"Step {step_index} {action}: {summary}"
        tags = ["plan", f"step_{action}"]
        payload: dict[str, Any] = {
            "kind": PayloadKind.PLAN_STEP,
            "summary": auto_summary,
            "data": data,
            "tags": tags,
        }
        self._agent._emit_event(
            event_type=EventType.CUSTOM,
            task_id=self.task_id,
            project_id=self.project_id,
            task_type=self.task_type,
            task_run_id=self.task_run_id,
            correlation_id=self.correlation_id,
            payload=payload,
        )


class _ActionContext:
    """Context manager for inline action tracking via agent.track_context()."""

    def __init__(self, agent: Agent, action_name: str) -> None:
        self._agent = agent
        self._action_name = action_name
        self._action_id = _new_id()
        self._parent_action_id: str | None = None
        self._token: contextvars.Token | None = None
        self._start_time: float = 0.0
        self._payload: dict[str, Any] | None = None

    def set_payload(self, payload: dict[str, Any]) -> None:
        """Set additional payload data for the action events."""
        self._payload = payload

    def __enter__(self) -> _ActionContext:
        self._parent_action_id = _current_action_id.get()
        self._token = _current_action_id.set(self._action_id)
        self._start_time = time.monotonic()

        task = self._agent._get_active_task()
        event_payload: dict[str, Any] = {
            "action_name": self._action_name,
        }
        self._agent._emit_event(
            event_type=EventType.ACTION_STARTED,
            action_id=self._action_id,
            parent_action_id=self._parent_action_id,
            task_id=task.task_id if task else None,
            project_id=task.project_id if task else None,
            task_type=task.task_type if task else None,
            task_run_id=task.task_run_id if task else None,
            correlation_id=task.correlation_id if task else None,
            payload=event_payload,
        )
        return self

    def __exit__(self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any) -> bool:
        duration_ms = int((time.monotonic() - self._start_time) * 1000)
        task = self._agent._get_active_task()

        if exc_val is not None:
            event_payload: dict[str, Any] = {
                "action_name": self._action_name,
                "exception_type": type(exc_val).__name__,
                "exception_message": str(exc_val),
            }
            if self._payload:
                event_payload.update(self._payload)
            self._agent._emit_event(
                event_type=EventType.ACTION_FAILED,
                action_id=self._action_id,
                parent_action_id=self._parent_action_id,
                task_id=task.task_id if task else None,
                project_id=task.project_id if task else None,
                task_type=task.task_type if task else None,
                task_run_id=task.task_run_id if task else None,
                correlation_id=task.correlation_id if task else None,
                status="failure",
                duration_ms=duration_ms,
                payload=event_payload,
            )
        else:
            event_payload = {"action_name": self._action_name}
            if self._payload:
                event_payload.update(self._payload)
            self._agent._emit_event(
                event_type=EventType.ACTION_COMPLETED,
                action_id=self._action_id,
                parent_action_id=self._parent_action_id,
                task_id=task.task_id if task else None,
                project_id=task.project_id if task else None,
                task_type=task.task_type if task else None,
                task_run_id=task.task_run_id if task else None,
                correlation_id=task.correlation_id if task else None,
                status="success",
                duration_ms=duration_ms,
                payload=event_payload,
            )

        # Restore previous action context
        if self._token is not None:
            _current_action_id.reset(self._token)
        return False  # Never swallow exceptions


class Agent:
    """An instrumented agent that emits telemetry events."""

    def __init__(
        self,
        agent_id: str,
        transport: Transport,
        *,
        agent_type: str = "general",
        version: str | None = None,
        framework: str = "custom",
        heartbeat_interval: float = 30.0,
        stuck_threshold: int = 300,
        heartbeat_payload: Callable[[], dict[str, Any] | None] | None = None,
        queue_provider: Callable[[], dict[str, Any] | None] | None = None,
        environment: str = "production",
        group: str = "default",
    ) -> None:
        self.agent_id = agent_id
        self._transport = transport
        self.agent_type = agent_type
        self.version = version
        self.framework = framework
        self._heartbeat_interval = heartbeat_interval
        self._stuck_threshold = stuck_threshold
        self._heartbeat_payload_cb = heartbeat_payload
        self._queue_provider_cb = queue_provider
        self._environment = environment
        self._group = group

        # Thread-local for active task
        self._task_local = threading.local()

        # Heartbeat thread
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    def _get_envelope(self) -> dict[str, Any]:
        """Build the batch envelope for this agent."""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "agent_version": self.version,
            "framework": self.framework,
            "runtime": f"python-{platform.python_version()}",
            "sdk_version": SDK_VERSION,
            "environment": self._environment,
            "group": self._group,
        }

    # -- Registration --

    def _register(self) -> None:
        """Emit agent_registered event and start heartbeat."""
        self._emit_event(
            event_type=EventType.AGENT_REGISTERED,
            payload={
                "summary": f"Agent {self.agent_id} registered",
                "data": {
                    "type": self.agent_type,
                    "version": self.version,
                    "framework": self.framework,
                    "stuck_threshold": self._stuck_threshold,
                },
            },
        )
        self._start_heartbeat()

    # -- Heartbeat --

    def _start_heartbeat(self) -> None:
        """Start the background heartbeat thread."""
        if self._heartbeat_interval <= 0:
            return
        self._hb_thread = threading.Thread(
            target=self._heartbeat_loop,
            name=f"hiveloop-hb-{self.agent_id}",
            daemon=True,
        )
        self._hb_thread.start()

    def _heartbeat_loop(self) -> None:
        """Heartbeat thread loop."""
        while not self._hb_stop.wait(timeout=self._heartbeat_interval):
            self._emit_heartbeat()

    def _emit_heartbeat(self) -> None:
        """Emit a heartbeat event, optionally with payload callback."""
        payload: dict[str, Any] | None = None

        # Heartbeat payload callback
        if self._heartbeat_payload_cb:
            try:
                payload = self._heartbeat_payload_cb()
            except Exception:
                logger.warning(
                    "heartbeat_payload callback failed for agent %s",
                    self.agent_id,
                    exc_info=True,
                )
                payload = None

        self._emit_event(
            event_type=EventType.HEARTBEAT,
            payload=payload,
        )

        # Queue provider callback — emits separate queue_snapshot event
        if self._queue_provider_cb:
            try:
                queue_data = self._queue_provider_cb()
                if queue_data is not None:
                    self._emit_queue_snapshot_from_callback(queue_data)
            except Exception:
                logger.warning(
                    "queue_provider callback failed for agent %s",
                    self.agent_id,
                    exc_info=True,
                )

    def _emit_queue_snapshot_from_callback(self, data: dict[str, Any]) -> None:
        """Emit a queue_snapshot event from the queue_provider callback."""
        depth = data.get("depth", 0)
        age = data.get("oldest_age_seconds")
        summary = f"Queue: {depth} items"
        if age is not None:
            summary += f", oldest {age}s"
        payload: dict[str, Any] = {
            "kind": PayloadKind.QUEUE_SNAPSHOT,
            "summary": summary,
            "data": data,
            "tags": ["queue"],
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat thread."""
        self._hb_stop.set()
        if self._hb_thread and self._hb_thread.is_alive():
            self._hb_thread.join(timeout=2.0)

    # -- Task lifecycle --

    def task(
        self,
        task_id: str,
        project: str | None = None,
        type: str | None = None,
        task_run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Task:
        """Create a task context manager."""
        return Task(
            agent=self,
            task_id=task_id,
            project_id=project,
            task_type=type,
            task_run_id=task_run_id,
            correlation_id=correlation_id,
        )

    def start_task(
        self,
        task_id: str,
        project: str | None = None,
        type: str | None = None,
        task_run_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Task:
        """Start a task without context manager. Caller must call task.complete() or task.fail()."""
        t = Task(
            agent=self,
            task_id=task_id,
            project_id=project,
            task_type=type,
            task_run_id=task_run_id,
            correlation_id=correlation_id,
        )
        t._start()
        return t

    def _set_active_task(self, task: Task) -> None:
        self._task_local.task = task

    def _clear_active_task(self) -> None:
        self._task_local.task = None

    def _get_active_task(self) -> Task | None:
        return getattr(self._task_local, "task", None)

    # -- Agent-level events --

    def event(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        severity: str | None = None,
        parent_event_id: str | None = None,
    ) -> None:
        """Emit an agent-level event (no task context)."""
        self._emit_event(
            event_type=event_type,
            severity=severity,
            parent_event_id=parent_event_id,
            payload=payload,
        )

    # -- Action tracking (C1.3) --

    def track(self, action_name: str) -> Callable:
        """Decorator for tracking function execution as actions.

        Works with both sync and async functions. Supports nesting.
        """

        def decorator(fn: Callable) -> Callable:
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    return await self._track_async(action_name, fn, args, kwargs)
                return async_wrapper
            else:
                @functools.wraps(fn)
                def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                    return self._track_sync(action_name, fn, args, kwargs)
                return sync_wrapper

        return decorator

    def track_context(self, action_name: str) -> _ActionContext:
        """Context manager for inline action tracking."""
        return _ActionContext(self, action_name)

    def _track_sync(
        self,
        action_name: str,
        fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        """Execute a sync function with action tracking."""
        action_id = _new_id()
        parent_action_id = _current_action_id.get()
        token = _current_action_id.set(action_id)

        task = self._get_active_task()
        func_name = f"{fn.__module__}.{fn.__qualname__}"

        self._emit_event(
            event_type=EventType.ACTION_STARTED,
            action_id=action_id,
            parent_action_id=parent_action_id,
            task_id=task.task_id if task else None,
            project_id=task.project_id if task else None,
            task_type=task.task_type if task else None,
            task_run_id=task.task_run_id if task else None,
            correlation_id=task.correlation_id if task else None,
            payload={"action_name": action_name, "function": func_name},
        )

        start = time.monotonic()
        try:
            result = fn(*args, **kwargs)
            duration_ms = int((time.monotonic() - start) * 1000)
            self._emit_event(
                event_type=EventType.ACTION_COMPLETED,
                action_id=action_id,
                parent_action_id=parent_action_id,
                task_id=task.task_id if task else None,
                project_id=task.project_id if task else None,
                task_type=task.task_type if task else None,
                task_run_id=task.task_run_id if task else None,
                correlation_id=task.correlation_id if task else None,
                status="success",
                duration_ms=duration_ms,
                payload={"action_name": action_name, "function": func_name},
            )
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._emit_event(
                event_type=EventType.ACTION_FAILED,
                action_id=action_id,
                parent_action_id=parent_action_id,
                task_id=task.task_id if task else None,
                project_id=task.project_id if task else None,
                task_type=task.task_type if task else None,
                task_run_id=task.task_run_id if task else None,
                correlation_id=task.correlation_id if task else None,
                status="failure",
                duration_ms=duration_ms,
                payload={
                    "action_name": action_name,
                    "function": func_name,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            raise
        finally:
            _current_action_id.reset(token)

    async def _track_async(
        self,
        action_name: str,
        fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        """Execute an async function with action tracking."""
        action_id = _new_id()
        parent_action_id = _current_action_id.get()
        token = _current_action_id.set(action_id)

        task = self._get_active_task()
        func_name = f"{fn.__module__}.{fn.__qualname__}"

        self._emit_event(
            event_type=EventType.ACTION_STARTED,
            action_id=action_id,
            parent_action_id=parent_action_id,
            task_id=task.task_id if task else None,
            project_id=task.project_id if task else None,
            task_type=task.task_type if task else None,
            task_run_id=task.task_run_id if task else None,
            correlation_id=task.correlation_id if task else None,
            payload={"action_name": action_name, "function": func_name},
        )

        start = time.monotonic()
        try:
            result = await fn(*args, **kwargs)
            duration_ms = int((time.monotonic() - start) * 1000)
            self._emit_event(
                event_type=EventType.ACTION_COMPLETED,
                action_id=action_id,
                parent_action_id=parent_action_id,
                task_id=task.task_id if task else None,
                project_id=task.project_id if task else None,
                task_type=task.task_type if task else None,
                task_run_id=task.task_run_id if task else None,
                correlation_id=task.correlation_id if task else None,
                status="success",
                duration_ms=duration_ms,
                payload={"action_name": action_name, "function": func_name},
            )
            return result
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            self._emit_event(
                event_type=EventType.ACTION_FAILED,
                action_id=action_id,
                parent_action_id=parent_action_id,
                task_id=task.task_id if task else None,
                project_id=task.project_id if task else None,
                task_type=task.task_type if task else None,
                task_run_id=task.task_run_id if task else None,
                correlation_id=task.correlation_id if task else None,
                status="failure",
                duration_ms=duration_ms,
                payload={
                    "action_name": action_name,
                    "function": func_name,
                    "exception_type": type(exc).__name__,
                    "exception_message": str(exc),
                },
            )
            raise
        finally:
            _current_action_id.reset(token)

    # -- Agent-level convenience methods (C1.4) --

    def llm_call(
        self,
        name: str,
        model: str,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost: float | None = None,
        duration_ms: int | None = None,
        prompt_preview: str | None = None,
        response_preview: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record an agent-level LLM call (no task context required)."""
        data: dict[str, Any] = {"name": name, "model": model}
        if tokens_in is not None:
            data["tokens_in"] = tokens_in
        if tokens_out is not None:
            data["tokens_out"] = tokens_out
        if cost is not None:
            data["cost"] = cost
        if duration_ms is not None:
            data["duration_ms"] = duration_ms
        if prompt_preview is not None:
            data["prompt_preview"] = prompt_preview
        if response_preview is not None:
            data["response_preview"] = response_preview
        if metadata is not None:
            data["metadata"] = metadata

        summary = _build_llm_summary(name, model, tokens_in, tokens_out, cost)
        payload: dict[str, Any] = {
            "kind": PayloadKind.LLM_CALL,
            "summary": summary,
            "data": data,
            "tags": ["llm"],
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    def queue_snapshot(
        self,
        depth: int,
        *,
        oldest_age_seconds: int | None = None,
        items: list[dict[str, Any]] | None = None,
        processing: dict[str, Any] | None = None,
    ) -> None:
        """Report the current state of the agent's work queue."""
        data: dict[str, Any] = {"depth": depth}
        if oldest_age_seconds is not None:
            data["oldest_age_seconds"] = oldest_age_seconds
        if items is not None:
            data["items"] = items
        if processing is not None:
            data["processing"] = processing

        summary = f"Queue: {depth} items"
        if oldest_age_seconds is not None:
            summary += f", oldest {oldest_age_seconds}s"

        payload: dict[str, Any] = {
            "kind": PayloadKind.QUEUE_SNAPSHOT,
            "summary": summary,
            "data": data,
            "tags": ["queue"],
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    def todo(
        self,
        todo_id: str,
        action: str,
        summary: str,
        *,
        priority: str | None = None,
        source: str | None = None,
        context: str | None = None,
        due_by: str | None = None,
    ) -> None:
        """Report a TODO lifecycle event."""
        data: dict[str, Any] = {
            "todo_id": todo_id,
            "action": action,
        }
        if priority is not None:
            data["priority"] = priority
        if source is not None:
            data["source"] = source
        if context is not None:
            data["context"] = context
        if due_by is not None:
            data["due_by"] = due_by

        tags = ["todo", action]
        payload: dict[str, Any] = {
            "kind": PayloadKind.TODO,
            "summary": summary,
            "data": data,
            "tags": tags,
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    def scheduled(self, items: list[dict[str, Any]]) -> None:
        """Report scheduled work items."""
        count = len(items)
        # Find earliest next_run for summary
        next_time: str | None = None
        for item in items:
            nr = item.get("next_run")
            if nr and (next_time is None or nr < next_time):
                next_time = nr
        summary = f"{count} scheduled items"
        if next_time:
            # Extract time portion
            time_part = next_time.split("T")[1] if "T" in next_time else next_time
            summary += f", next at {time_part}"

        payload: dict[str, Any] = {
            "kind": PayloadKind.SCHEDULED,
            "summary": summary,
            "data": {"items": items},
            "tags": ["scheduled"],
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    def report_issue(
        self,
        summary: str,
        severity: str,
        *,
        issue_id: str | None = None,
        category: str | None = None,
        context: dict[str, Any] | None = None,
        occurrence_count: int | None = None,
    ) -> None:
        """Report an agent issue."""
        data: dict[str, Any] = {
            "severity": severity,
            "action": "reported",
        }
        if issue_id is not None:
            data["issue_id"] = issue_id
        if category is not None:
            data["category"] = category
        if context is not None:
            data["context"] = context
        if occurrence_count is not None:
            data["occurrence_count"] = occurrence_count

        tags = ["issue"]
        if category:
            tags.append(category)
        payload: dict[str, Any] = {
            "kind": PayloadKind.ISSUE,
            "summary": summary,
            "data": data,
            "tags": tags,
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    def resolve_issue(
        self,
        summary: str,
        *,
        issue_id: str | None = None,
    ) -> None:
        """Resolve a previously reported issue."""
        data: dict[str, Any] = {
            "severity": "low",  # resolved issues are low severity
            "action": "resolved",
        }
        if issue_id is not None:
            data["issue_id"] = issue_id

        payload: dict[str, Any] = {
            "kind": PayloadKind.ISSUE,
            "summary": summary,
            "data": data,
            "tags": ["issue", "resolved"],
        }
        self._emit_event(event_type=EventType.CUSTOM, payload=payload)

    # -- Event construction (C1.2.8) --

    def _emit_event(self, **kwargs: Any) -> None:
        """Build an event dict and enqueue it via transport.

        Auto-generates event_id, timestamp. Strips None values.
        Applies severity auto-defaults. Never raises.
        """
        try:
            event: dict[str, Any] = {
                "event_id": _new_id(),
                "timestamp": _utcnow_iso(),
            }
            event.update(kwargs)

            # Apply severity auto-default if not set
            if event.get("severity") is None:
                et = event.get("event_type", "")
                event["severity"] = SEVERITY_DEFAULTS.get(et, Severity.INFO)

            # Strip None values (but keep required fields)
            event = _strip_none(event)

            self._transport.enqueue(event, self._get_envelope())
        except Exception:
            logger.debug("Failed to emit event", exc_info=True)


# -- Helpers --

def _build_llm_summary(
    name: str,
    model: str,
    tokens_in: int | None,
    tokens_out: int | None,
    cost: float | None,
) -> str:
    """Build auto-generated LLM call summary."""
    parts = [f"{name} \u2192 {model}"]
    detail_parts: list[str] = []
    if tokens_in is not None and tokens_out is not None:
        detail_parts.append(f"{tokens_in} in / {tokens_out} out")
    if cost is not None:
        detail_parts.append(f"${cost}")
    if detail_parts:
        parts.append(f"({', '.join(detail_parts)})")
    return " ".join(parts)

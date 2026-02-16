"""HiveLoop × Claude Agent SDK integration.

Auto-instruments Claude Agent SDK agents with full HiveBoard observability
through a single ``hiveloop_hooks()`` call.  No changes to agent logic required.

Usage::

    from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks

    hooks = hiveloop_hooks(api_key="hb_live_xxx", project="my-project")

    async for message in query(
        prompt="Fix the bug in auth.py",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Bash"],
            hooks=hooks,
        ),
    ):
        print(message)

The integration maps Agent SDK hooks to HiveLoop sensors:

- SessionStart  → agent_registered + task_started + heartbeat
- PreToolUse    → action_started  (+ subagent / approval branches)
- PostToolUse   → action_completed / action_failed
- Stop          → task_completed with result capture
- SessionEnd    → fallback completion + flush + orphan cleanup

Each ``hiveloop_hooks()`` call returns an isolated ``HooksResult`` instance
with its own state — safe for concurrent sessions.
"""

from __future__ import annotations

import contextvars
import logging
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

import hiveloop

logger = logging.getLogger("hiveloop.integrations.claude_agent_sdk")


# ---------------------------------------------------------------------------
# Custom exceptions (never surface to host agent)
# ---------------------------------------------------------------------------

class ToolExecutionError(Exception):
    """Raised synthetically to trigger action_failed via __exit__."""


class OrphanedToolError(Exception):
    """Raised synthetically for tools that never got a PostToolUse."""


# ---------------------------------------------------------------------------
# HookMatcher — minimal compatible type for Agent SDK hooks dict
# ---------------------------------------------------------------------------

@dataclass
class HookMatcher:
    """Minimal hook matcher compatible with the Agent SDK's hook format.

    The Claude Agent SDK iterates hooks dicts where each event name maps to
    a list of matchers.  Each matcher has a ``matcher`` regex pattern and a
    ``hooks`` list of async callables.
    """

    matcher: str
    hooks: list[Callable[..., Any]]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class ClaudeAgentSDKConfig:
    """Configuration for the Claude Agent SDK integration."""

    # HiveLoop connection
    api_key: str
    endpoint: str | None = None
    environment: str = "production"
    group: str = "default"

    # Agent identity
    agent_name: str = "claude-agent"
    agent_type: str = "general"
    agent_version: str | None = None
    project: str | None = None

    # Behavioral
    heartbeat_interval: float = 30.0
    stuck_threshold: int = 300
    track_subagents: bool = True
    capture_tool_results: bool = True
    result_preview_length: int = 500

    # Advanced
    flush_interval: float = 5.0
    batch_size: int = 100
    debug: bool = False


# ---------------------------------------------------------------------------
# Per-invocation session state (captured by closures, not module-level)
# ---------------------------------------------------------------------------

@dataclass
class _SessionState:
    """Mutable state for a single ``hiveloop_hooks()`` invocation.

    Each call to ``hiveloop_hooks()`` creates one of these, captured by
    the closure-based hooks.  Multiple concurrent sessions get independent
    state — no module-level globals to overwrite.
    """

    config: ClaudeAgentSDKConfig
    hb: Any  # hiveloop.HiveBoard instance

    # Thread-safe dicts bridging PreToolUse → PostToolUse via tool_use_id
    active_tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    active_tools_lock: threading.Lock = field(default_factory=threading.Lock)

    subagent_tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    subagent_lock: threading.Lock = field(default_factory=threading.Lock)


# ---------------------------------------------------------------------------
# Module-level context vars for task/agent propagation across async boundaries
# These MUST stay module-level — they're the public API surface for
# get_current_task() / get_current_agent(), and ContextVar isolation is
# per-async-context, not per-module-global.
# ---------------------------------------------------------------------------

_current_task: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "hiveloop_casdk_task", default=None,
)
_current_agent: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "hiveloop_casdk_agent", default=None,
)


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

def get_current_task() -> Any | None:
    """Return the current HiveLoop Task, or ``None`` if outside a session."""
    return _current_task.get()


def get_current_agent() -> Any | None:
    """Return the current HiveLoop Agent, or ``None`` if outside a session."""
    return _current_agent.get()


# ---------------------------------------------------------------------------
# Helper — readable action name from tool name + input
# ---------------------------------------------------------------------------

def _tool_to_action_name(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Convert Agent SDK tool name + input to a descriptive action name.

    Examples::

        Read(auth.py)     → "Read:auth.py"
        Bash(pytest)      → "Bash:pytest"
        Grep(login)       → "Grep:login"
        Edit(auth.py)     → "Edit:auth.py"
        Glob(**/*.py)     → "Glob:**/*.py"
        WebSearch(query)  → "WebSearch:query"
        WebFetch(url)     → "WebFetch:example.com"
    """
    detail = ""
    if tool_name in ("Read", "Edit", "Write"):
        detail = tool_input.get("file_path", "")
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        detail = cmd.split()[0] if cmd else ""
    elif tool_name in ("Grep", "Glob"):
        detail = tool_input.get("pattern", "")
    elif tool_name == "WebSearch":
        detail = tool_input.get("query", "")[:50]
    elif tool_name == "WebFetch":
        url = tool_input.get("url", "")
        try:
            from urllib.parse import urlparse
            detail = urlparse(url).netloc
        except Exception:
            detail = url[:50]

    if detail:
        if len(detail) > 60:
            detail = detail[:57] + "..."
        return f"{tool_name}:{detail}"
    return tool_name


# ---------------------------------------------------------------------------
# _safe_hook — critical safety wrapper (state-aware version)
# ---------------------------------------------------------------------------

def _safe_hook(
    fn: Callable[..., Any],
    state: _SessionState,
) -> Callable[..., Any]:
    """Wrap an async hook so it *never* raises into the Agent SDK.

    The integration must never crash the host agent — same invariant as the
    core SDK transport layer.  Receives state so it can check ``debug``.
    """

    async def wrapper(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            return await fn(input_data, tool_use_id, context)
        except Exception as exc:
            if state.config.debug:
                logger.error("HiveLoop hook error in %s: %s", fn.__name__, exc)
            return {}

    # Preserve the original name for debugging
    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper


# ---------------------------------------------------------------------------
# Phase 2 — LLM cost tracking (pure functions — no state needed)
# ---------------------------------------------------------------------------

# Approximate pricing — update as models change
_MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-haiku-4-20250514": {"input": 0.80 / 1_000_000, "output": 4.0 / 1_000_000},
    "claude-opus-4-20250514": {"input": 15.0 / 1_000_000, "output": 75.0 / 1_000_000},
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate LLM cost from model name and token counts."""
    pricing = _MODEL_PRICING.get(model, {"input": 3.0 / 1e6, "output": 15.0 / 1e6})
    return (tokens_in * pricing["input"]) + (tokens_out * pricing["output"])


def _try_capture_llm_usage(message: Any) -> None:
    """Inspect a message from the Agent SDK stream for LLM usage data.

    If usage metadata is found, emit an LLM call event on the current task.
    """
    task = _current_task.get()
    if not task:
        return

    usage = getattr(message, "usage", None)
    if not usage:
        return

    model = getattr(message, "model", "claude-unknown")
    tokens_in = getattr(usage, "input_tokens", 0)
    tokens_out = getattr(usage, "output_tokens", 0)

    cost = _estimate_cost(model, tokens_in, tokens_out)

    task.llm_call(
        "agent-loop",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )


async def instrumented_query(query_fn: Callable[..., Any], *args: Any, **kwargs: Any):
    """Async generator wrapper that intercepts LLM usage from the message stream.

    Usage::

        async for message in instrumented_query(
            query,
            prompt="Fix the bug",
            options=ClaudeAgentOptions(
                hooks=hiveloop_hooks(api_key="hb_live_xxx"),
            ),
        ):
            print(message)
    """
    async for message in query_fn(*args, **kwargs):
        _try_capture_llm_usage(message)
        yield message


# ---------------------------------------------------------------------------
# merge_hooks — compose multiple hook dicts
# ---------------------------------------------------------------------------

def merge_hooks(*hook_dicts: dict[str, list[HookMatcher]]) -> dict[str, list[HookMatcher]]:
    """Merge multiple hook dicts.  Later dicts append to earlier ones.

    Usage::

        my_hooks = {"PostToolUse": [HookMatcher(matcher="Edit", hooks=[my_audit])]}
        hl_hooks = hiveloop_hooks(api_key="hb_live_xxx")
        combined = merge_hooks(my_hooks, hl_hooks)
    """
    merged: dict[str, list[HookMatcher]] = {}
    for hooks in hook_dicts:
        for event_name, matchers in hooks.items():
            if event_name not in merged:
                merged[event_name] = []
            merged[event_name].extend(matchers)
    return merged


# ---------------------------------------------------------------------------
# HooksResult — dict subclass returned by hiveloop_hooks()
# ---------------------------------------------------------------------------

class HooksResult(dict):
    """Dict subclass returned by ``hiveloop_hooks()``.

    Behaves like a normal dict (compatible with Agent SDK ``hooks=`` param)
    but also exposes per-invocation state and named hook accessors for
    testing and advanced usage.
    """

    def __init__(self, mapping: dict[str, list[HookMatcher]], state: _SessionState):
        super().__init__(mapping)
        self.state: _SessionState = state

        # Extract the actual hook callable from each HookMatcher for convenience
        self.on_session_start: Callable[..., Any] = mapping["SessionStart"][0].hooks[0]
        self.on_session_end: Callable[..., Any] = mapping["SessionEnd"][0].hooks[0]
        self.on_pre_tool_use: Callable[..., Any] = mapping["PreToolUse"][0].hooks[0]
        self.on_post_tool_use: Callable[..., Any] = mapping["PostToolUse"][0].hooks[0]
        self.on_stop: Callable[..., Any] = mapping["Stop"][0].hooks[0]

    async def cleanup(self) -> None:
        """Force-close any orphaned tool contexts and subagent tasks."""
        await self._cleanup_fn()

    # Set by the factory after creation
    _cleanup_fn: Callable[..., Any] = staticmethod(lambda: None)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Factory function — the public API
# ---------------------------------------------------------------------------

def hiveloop_hooks(
    api_key: str,
    *,
    endpoint: str | None = None,
    project: str | None = None,
    agent_name: str = "claude-agent",
    agent_type: str = "general",
    agent_version: str | None = None,
    environment: str = "production",
    group: str = "default",
    track_subagents: bool = True,
    capture_tool_results: bool = True,
    result_preview_length: int = 500,
    heartbeat_interval: float = 30.0,
    stuck_threshold: int = 300,
    debug: bool = False,
) -> HooksResult:
    """Create Claude Agent SDK hooks for HiveBoard observability.

    Returns a ``HooksResult`` — a dict compatible with ``ClaudeAgentOptions.hooks``
    that also carries per-session state and named hook accessors.

    Each invocation returns an independent set of hooks with isolated state,
    safe for concurrent sessions.

    What you get on HiveBoard:
        - Agent card with live heartbeat in Fleet View
        - Task timelines with tool action nodes
        - Subagent pipeline tracking (if using Task tool)
        - Approval flow tracking (if using AskUserQuestion)
        - Real-time activity stream
        - Full task list with duration and status

    Usage::

        hooks = hiveloop_hooks(api_key="hb_live_xxx", project="my-project")

        async for message in query(
            prompt="...",
            options=ClaudeAgentOptions(hooks=hooks),
        ):
            ...

        # For testing / advanced usage:
        hooks.state.active_tools   # per-session tool tracking dict
        hooks.on_session_start     # direct hook callable
        await hooks.cleanup()      # force-close orphans
    """
    config = ClaudeAgentSDKConfig(
        api_key=api_key,
        endpoint=endpoint,
        project=project,
        agent_name=agent_name,
        agent_type=agent_type,
        agent_version=agent_version,
        environment=environment,
        group=group,
        track_subagents=track_subagents,
        capture_tool_results=capture_tool_results,
        result_preview_length=result_preview_length,
        heartbeat_interval=heartbeat_interval,
        stuck_threshold=stuck_threshold,
        debug=debug,
    )

    hb = hiveloop.init(
        api_key=api_key,
        endpoint=endpoint,
        environment=environment,
        group=group,
        flush_interval=config.flush_interval,
        batch_size=config.batch_size,
        debug=debug,
    )

    state = _SessionState(config=config, hb=hb)

    # --- Closure-based hooks: each captures `state` ---

    def _make_heartbeat_payload() -> Callable[[], dict[str, Any] | None]:
        """Return a callback that reports active tool count in heartbeats."""
        def _payload() -> dict[str, Any] | None:
            with state.active_tools_lock:
                active = len(state.active_tools)
            with state.subagent_lock:
                subs = len(state.subagent_tasks)
            return {
                "summary": f"active_tools={active}, subagents={subs}",
                "data": {"active_tools": active, "active_subagents": subs},
            }
        return _payload

    async def _on_session_start(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """SessionStart hook — register agent + start task."""
        if not state.hb:
            return {}

        session_id = input_data.get("session_id", f"session-{uuid.uuid4().hex[:8]}")

        agent_handle = state.hb.agent(
            agent_id=state.config.agent_name,
            type=state.config.agent_type,
            version=state.config.agent_version,
            framework="claude-agent-sdk",
            heartbeat_interval=state.config.heartbeat_interval,
            stuck_threshold=state.config.stuck_threshold,
            heartbeat_payload=_make_heartbeat_payload(),
        )
        _current_agent.set(agent_handle)

        task = agent_handle.start_task(
            task_id=session_id,
            project=state.config.project,
            type="agent-sdk-session",
        )
        _current_task.set(task)

        return {}

    async def _on_pre_tool_use(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """PreToolUse hook — track actions, detect subagents, detect approvals."""
        agent = _current_agent.get()
        task = _current_task.get()
        if not agent:
            return {}

        tool_name = input_data.get("tool_name", "unknown")
        tool_input = input_data.get("tool_input", {})

        # ── BRANCH: Subagent spawn via Task tool ──
        if tool_name == "Task" and state.config.track_subagents:
            subagent_name = tool_input.get("description", tool_input.get("agent", "unknown-subagent"))

            if state.hb:
                sub_agent = state.hb.agent(
                    agent_id=f"{state.config.agent_name}:sub:{subagent_name}",
                    type="claude-agent-sdk-subagent",
                    framework="claude-agent-sdk",
                    heartbeat_interval=state.config.heartbeat_interval,
                )

                sub_task = sub_agent.start_task(
                    task_id=f"sub-{tool_use_id}",
                    project=state.config.project,
                    type="subagent-execution",
                )

                with state.subagent_lock:
                    state.subagent_tasks[tool_use_id] = {
                        "agent": sub_agent,
                        "task": sub_task,
                        "start_time": time.perf_counter(),
                    }

                if task:
                    task.event("custom", payload={
                        "kind": "delegation",
                        "summary": f"Delegated to subagent: {subagent_name}",
                        "data": {
                            "subagent_name": subagent_name,
                            "subagent_id": f"{state.config.agent_name}:sub:{subagent_name}",
                            "sub_task_id": f"sub-{tool_use_id}",
                            "prompt_preview": str(tool_input.get("prompt", ""))[:500],
                        },
                    })

            return {}

        # ── BRANCH: AskUserQuestion → approval flow ──
        if tool_name == "AskUserQuestion" and task:
            questions = tool_input.get("questions", [])
            question_text = questions[0].get("question", "Awaiting user input") if questions else "Awaiting user input"
            task.request_approval(
                summary=f"Agent asking user: {question_text[:200]}",
                approver="user",
            )

        # ── STANDARD: Track as action ──
        action_name = _tool_to_action_name(tool_name, tool_input)
        ctx = agent.track_context(action_name)
        ctx.__enter__()

        with state.active_tools_lock:
            state.active_tools[tool_use_id] = {
                "track_ctx": ctx,
                "start_time": time.perf_counter(),
                "tool_name": tool_name,
            }

        return {}

    async def _on_post_tool_use(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """PostToolUse hook — complete actions, close subagent tasks, record approvals."""
        tool_name = input_data.get("tool_name", "")
        tool_result = input_data.get("tool_result", "")
        error = input_data.get("error")
        task = _current_task.get()

        # ── BRANCH: Subagent return ──
        if tool_name == "Task" and state.config.track_subagents:
            with state.subagent_lock:
                sub_info = state.subagent_tasks.pop(tool_use_id, None)

            if sub_info:
                sub_task = sub_info["task"]
                elapsed = (time.perf_counter() - sub_info["start_time"]) * 1000

                if error:
                    sub_task.fail(RuntimeError(str(error)))
                else:
                    sub_task.set_payload({
                        "result_preview": str(tool_result)[:(state.config.result_preview_length)],
                        "duration_ms": round(elapsed),
                    })
                    sub_task.complete()

            return {}

        # ── BRANCH: AskUserQuestion → approval received ──
        if tool_name == "AskUserQuestion" and task:
            response = str(tool_result)[:200] if tool_result else "No response"
            task.approval_received(
                summary=f"User responded: {response}",
                approved_by="user",
                decision="approved",
            )

        # ── STANDARD: Close action tracking ──
        with state.active_tools_lock:
            tool_info = state.active_tools.pop(tool_use_id, None)

        if tool_info:
            ctx = tool_info["track_ctx"]

            # Attach result preview to payload
            if state.config.capture_tool_results:
                result_str = str(tool_result)[:state.config.result_preview_length]
                payload: dict[str, Any] = {
                    "tool_name": tool_info["tool_name"],
                    "result_preview": result_str,
                }
                if error:
                    payload["error"] = str(error)[:500]
                ctx.set_payload(payload)

            if error:
                # Exit with exception info → triggers action_failed
                try:
                    raise ToolExecutionError(str(error))
                except ToolExecutionError:
                    ctx.__exit__(*sys.exc_info())
            else:
                ctx.__exit__(None, None, None)

        return {}

    async def _on_stop(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Stop hook — capture result and close task."""
        task = _current_task.get()
        if not task:
            return {}

        result = input_data.get("result", "")

        task.event("custom", payload={
            "kind": "agent_result",
            "summary": f"Agent completed: {str(result)[:100]}",
            "data": {"result_preview": str(result)[:1000]},
        })

        task.complete()
        _current_task.set(None)

        return {}

    async def _cleanup() -> None:
        """Force-close any orphaned tool contexts and subagent tasks."""
        with state.active_tools_lock:
            for _tool_use_id, info in list(state.active_tools.items()):
                try:
                    ctx = info["track_ctx"]
                    ctx.set_payload({"error": "orphaned — session ended before tool returned"})
                    try:
                        raise OrphanedToolError("Session ended before tool returned")
                    except OrphanedToolError:
                        ctx.__exit__(*sys.exc_info())
                except Exception:
                    pass
            state.active_tools.clear()

        with state.subagent_lock:
            for _tool_use_id, info in list(state.subagent_tasks.items()):
                try:
                    info["task"].fail(RuntimeError("Parent session ended"))
                except Exception:
                    pass
            state.subagent_tasks.clear()

    async def _on_session_end(
        input_data: dict[str, Any],
        tool_use_id: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """SessionEnd hook — safety net: close task + flush + cleanup orphans."""
        # Clean up orphaned tool contexts first
        await _cleanup()

        task = _current_task.get()
        if task:
            error = input_data.get("error")
            if error:
                task.fail(RuntimeError(str(error)))
            else:
                task.complete()
            _current_task.set(None)

        # Force flush to ensure delivery
        if state.hb:
            state.hb.flush()

        return {}

    # --- Build the hooks dict with _safe_hook wrappers ---

    hooks_dict = {
        "SessionStart": [
            HookMatcher(matcher=".*", hooks=[_safe_hook(_on_session_start, state)]),
        ],
        "SessionEnd": [
            HookMatcher(matcher=".*", hooks=[_safe_hook(_on_session_end, state)]),
        ],
        "PreToolUse": [
            HookMatcher(matcher=".*", hooks=[_safe_hook(_on_pre_tool_use, state)]),
        ],
        "PostToolUse": [
            HookMatcher(matcher=".*", hooks=[_safe_hook(_on_post_tool_use, state)]),
        ],
        "Stop": [
            HookMatcher(matcher=".*", hooks=[_safe_hook(_on_stop, state)]),
        ],
    }

    result = HooksResult(hooks_dict, state)
    result._cleanup_fn = _cleanup
    return result

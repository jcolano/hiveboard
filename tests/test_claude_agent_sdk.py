"""Tests for hiveloop.integrations.claude_agent_sdk.

Covers: factory function, all 5 hook callbacks, _safe_hook error suppression,
_tool_to_action_name helper, subagent detection, approval flow, orphaned tool
cleanup, merge_hooks utility, Phase 2 LLM cost interception.

All hook tests are async because the hooks use contextvars internally and
changes must propagate within the same async task context.

After the closure-based refactor, hooks and mutable state live on the
``HooksResult`` returned by ``hiveloop_hooks()`` — no module-level globals.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import pytest

import hiveloop
from hiveloop.integrations.claude_agent_sdk import (
    ClaudeAgentSDKConfig,
    HookMatcher,
    HooksResult,
    ToolExecutionError,
    OrphanedToolError,
    _SessionState,
    _current_task,
    _current_agent,
    _tool_to_action_name,
    _estimate_cost,
    _try_capture_llm_usage,
    hiveloop_hooks,
    merge_hooks,
    get_current_task,
    get_current_agent,
    instrumented_query,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flush_and_wait(hb: hiveloop.HiveBoard, delay: float = 0.3) -> None:
    """Flush events and wait for transport delivery."""
    hb.flush()
    time.sleep(delay)


@pytest.fixture(autouse=True)
def _clean_casdk_state():
    """Ensure module-level context vars are clean before and after each test."""
    _current_task.set(None)
    _current_agent.set(None)
    yield
    _current_task.set(None)
    _current_agent.set(None)


# ---------------------------------------------------------------------------
# TestToolToActionName (sync — no contextvars involved)
# ---------------------------------------------------------------------------

class TestToolToActionName:
    """_tool_to_action_name produces descriptive labels."""

    def test_read(self):
        assert _tool_to_action_name("Read", {"file_path": "/src/auth.py"}) == "Read:/src/auth.py"

    def test_edit(self):
        assert _tool_to_action_name("Edit", {"file_path": "/src/auth.py"}) == "Edit:/src/auth.py"

    def test_write(self):
        assert _tool_to_action_name("Write", {"file_path": "/src/new.py"}) == "Write:/src/new.py"

    def test_bash(self):
        assert _tool_to_action_name("Bash", {"command": "pytest tests/"}) == "Bash:pytest"

    def test_bash_empty(self):
        assert _tool_to_action_name("Bash", {"command": ""}) == "Bash"

    def test_grep(self):
        assert _tool_to_action_name("Grep", {"pattern": "login"}) == "Grep:login"

    def test_glob(self):
        assert _tool_to_action_name("Glob", {"pattern": "**/*.py"}) == "Glob:**/*.py"

    def test_websearch(self):
        assert _tool_to_action_name("WebSearch", {"query": "python async"}) == "WebSearch:python async"

    def test_webfetch(self):
        result = _tool_to_action_name("WebFetch", {"url": "https://example.com/page"})
        assert result == "WebFetch:example.com"

    def test_unknown_tool(self):
        assert _tool_to_action_name("CustomTool", {}) == "CustomTool"

    def test_truncates_long_paths(self):
        long_path = "/very/long/path/" + "x" * 100 + "/file.py"
        result = _tool_to_action_name("Read", {"file_path": long_path})
        assert len(result) <= len("Read:") + 60
        assert result.endswith("...")

    def test_no_input(self):
        assert _tool_to_action_name("Read", {}) == "Read"


# ---------------------------------------------------------------------------
# TestEstimateCost (sync)
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_known_model(self):
        cost = _estimate_cost("claude-sonnet-4-20250514", 1000, 500)
        expected = (1000 * 3.0 / 1e6) + (500 * 15.0 / 1e6)
        assert abs(cost - expected) < 1e-10

    def test_unknown_model_uses_default(self):
        cost = _estimate_cost("unknown-model", 1000, 500)
        expected = (1000 * 3.0 / 1e6) + (500 * 15.0 / 1e6)
        assert abs(cost - expected) < 1e-10


# ---------------------------------------------------------------------------
# TestMergeHooks (sync)
# ---------------------------------------------------------------------------

class TestMergeHooks:
    def test_merge_disjoint(self):
        a = {"SessionStart": [HookMatcher(".*", [lambda: None])]}
        b = {"SessionEnd": [HookMatcher(".*", [lambda: None])]}
        merged = merge_hooks(a, b)
        assert "SessionStart" in merged
        assert "SessionEnd" in merged

    def test_merge_overlapping(self):
        hook1 = HookMatcher(".*", [lambda: None])
        hook2 = HookMatcher("Edit", [lambda: None])
        a = {"PostToolUse": [hook1]}
        b = {"PostToolUse": [hook2]}
        merged = merge_hooks(a, b)
        assert len(merged["PostToolUse"]) == 2
        assert merged["PostToolUse"][0] is hook1
        assert merged["PostToolUse"][1] is hook2

    def test_merge_empty(self):
        result = merge_hooks()
        assert result == {}

    def test_merge_three(self):
        a = {"A": [HookMatcher(".*", [])]}
        b = {"B": [HookMatcher(".*", [])]}
        c = {"A": [HookMatcher("x", [])]}
        merged = merge_hooks(a, b, c)
        assert len(merged["A"]) == 2
        assert len(merged["B"]) == 1


# ---------------------------------------------------------------------------
# TestFactory (sync — hiveloop_hooks is sync, checks return structure)
# ---------------------------------------------------------------------------

class TestFactory:
    """hiveloop_hooks() factory returns correct structure."""

    def test_returns_all_hook_types(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_factory",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        assert "SessionStart" in hooks
        assert "SessionEnd" in hooks
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks
        assert "Stop" in hooks

    def test_each_hook_has_matcher(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_factory",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        for name, matchers in hooks.items():
            assert len(matchers) == 1
            assert matchers[0].matcher == ".*"
            assert callable(matchers[0].hooks[0])

    def test_sets_config(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_factory",
            endpoint=mock_server.url,
            project="test-proj",
            agent_name="test-agent",
            agent_type="coder",
            heartbeat_interval=0,
        )
        assert hooks.state.config is not None
        assert hooks.state.config.project == "test-proj"
        assert hooks.state.config.agent_name == "test-agent"
        assert hooks.state.config.agent_type == "coder"

    def test_sets_hb_client(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_factory",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        assert hooks.state.hb is not None

    def test_returns_hooks_result(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_factory",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        assert isinstance(hooks, HooksResult)
        assert callable(hooks.on_session_start)
        assert callable(hooks.on_session_end)
        assert callable(hooks.on_pre_tool_use)
        assert callable(hooks.on_post_tool_use)
        assert callable(hooks.on_stop)


# ---------------------------------------------------------------------------
# TestSessionStart (async — hooks use contextvars)
# ---------------------------------------------------------------------------

class TestSessionStart:
    """on_session_start registers agent and starts task."""

    async def test_registers_agent_and_starts_task(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_session",
            endpoint=mock_server.url,
            agent_name="sess-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start(
            {"session_id": "sess-123"},
            "tu_start",
            {},
        )
        assert get_current_agent() is not None
        assert get_current_task() is not None

        _flush_and_wait(hooks.state.hb)

        events = mock_server.all_events()
        types = [e["event_type"] for e in events]
        assert "agent_registered" in types
        assert "task_started" in types

        started = next(e for e in events if e["event_type"] == "task_started")
        assert started["task_id"] == "sess-123"

    async def test_generates_session_id_if_missing(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_session",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        await hooks.on_session_start({}, "tu_start", {})
        task = get_current_task()
        assert task is not None
        assert task.task_id.startswith("session-")

    async def test_noop_without_client(self, mock_server):
        # Create hooks but null out the hb client to simulate no client
        hooks = hiveloop_hooks(
            api_key="hb_test_noop",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        hooks.state.hb = None
        result = await hooks.on_session_start({"session_id": "x"}, "tu", {})
        assert result == {}
        assert get_current_agent() is None


# ---------------------------------------------------------------------------
# TestPreToolUse (async)
# ---------------------------------------------------------------------------

class TestPreToolUse:
    """on_pre_tool_use tracks actions."""

    async def _setup(self, mock_server) -> HooksResult:
        hooks = hiveloop_hooks(
            api_key="hb_test_tool",
            endpoint=mock_server.url,
            agent_name="tool-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-tool-1"}, "tu_s", {})
        return hooks

    async def test_tracks_standard_tool(self, mock_server):
        hooks = await self._setup(mock_server)
        mock_server.clear()

        await hooks.on_pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "/src/main.py"}},
            "tu_001",
            {},
        )

        with hooks.state.active_tools_lock:
            assert "tu_001" in hooks.state.active_tools
            assert hooks.state.active_tools["tu_001"]["tool_name"] == "Read"

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        action_events = [e for e in events if e["event_type"] == "action_started"]
        assert len(action_events) == 1
        assert "Read:/src/main.py" in action_events[0]["payload"]["action_name"]

    async def test_detects_subagent_task_tool(self, mock_server):
        hooks = await self._setup(mock_server)
        mock_server.clear()

        await hooks.on_pre_tool_use(
            {
                "tool_name": "Task",
                "tool_input": {
                    "description": "code-reviewer",
                    "prompt": "Review the code",
                },
            },
            "tu_task_001",
            {},
        )

        # Should NOT be in active_tools (subagent branch returns early)
        with hooks.state.active_tools_lock:
            assert "tu_task_001" not in hooks.state.active_tools

        # Should be in subagent_tasks
        with hooks.state.subagent_lock:
            assert "tu_task_001" in hooks.state.subagent_tasks

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()

        # Should have registered a subagent
        reg_events = [e for e in events if e["event_type"] == "agent_registered"]
        assert any("sub:code-reviewer" in str(e) for e in reg_events)

        # Should have a delegation custom event
        custom_events = [e for e in events if e["event_type"] == "custom"]
        assert any(e.get("payload", {}).get("kind") == "delegation" for e in custom_events)

    async def test_subagent_disabled(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_nosub",
            endpoint=mock_server.url,
            agent_name="nosub-agent",
            track_subagents=False,
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-nosub"}, "tu_s", {})
        mock_server.clear()

        await hooks.on_pre_tool_use(
            {"tool_name": "Task", "tool_input": {"description": "sub"}},
            "tu_task_nosub",
            {},
        )

        # Should be tracked as a regular action, not a subagent
        with hooks.state.active_tools_lock:
            assert "tu_task_nosub" in hooks.state.active_tools
        with hooks.state.subagent_lock:
            assert "tu_task_nosub" not in hooks.state.subagent_tasks

    async def test_approval_request(self, mock_server):
        hooks = await self._setup(mock_server)
        mock_server.clear()

        await hooks.on_pre_tool_use(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {
                    "questions": [{"question": "Deploy to prod?"}],
                },
            },
            "tu_ask_001",
            {},
        )

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        approval_events = [e for e in events if e["event_type"] == "approval_requested"]
        assert len(approval_events) == 1
        assert "Deploy to prod?" in approval_events[0]["payload"]["summary"]

        # AskUserQuestion should ALSO be tracked as an action
        with hooks.state.active_tools_lock:
            assert "tu_ask_001" in hooks.state.active_tools

    async def test_noop_without_agent(self, mock_server):
        # Create hooks but don't start session — _current_agent is None
        hooks = hiveloop_hooks(
            api_key="hb_test_noop_pre",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        result = await hooks.on_pre_tool_use(
            {"tool_name": "Read", "tool_input": {}},
            "tu_noop",
            {},
        )
        assert result == {}
        with hooks.state.active_tools_lock:
            assert "tu_noop" not in hooks.state.active_tools


# ---------------------------------------------------------------------------
# TestPostToolUse (async)
# ---------------------------------------------------------------------------

class TestPostToolUse:
    """on_post_tool_use closes actions."""

    async def _setup_with_action(self, mock_server, tool_use_id="tu_post_001") -> HooksResult:
        hooks = hiveloop_hooks(
            api_key="hb_test_post",
            endpoint=mock_server.url,
            agent_name="post-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-post-1"}, "tu_s", {})
        await hooks.on_pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "/x.py"}},
            tool_use_id,
            {},
        )
        mock_server.clear()
        return hooks

    async def test_completes_action(self, mock_server):
        hooks = await self._setup_with_action(mock_server)

        await hooks.on_post_tool_use(
            {
                "tool_name": "Read",
                "tool_result": "file contents here",
            },
            "tu_post_001",
            {},
        )

        with hooks.state.active_tools_lock:
            assert "tu_post_001" not in hooks.state.active_tools

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        completed = [e for e in events if e["event_type"] == "action_completed"]
        assert len(completed) == 1
        assert "result_preview" in completed[0]["payload"]

    async def test_fails_action_on_error(self, mock_server):
        hooks = await self._setup_with_action(mock_server, tool_use_id="tu_post_err")

        await hooks.on_post_tool_use(
            {
                "tool_name": "Read",
                "tool_result": "",
                "error": "File not found",
            },
            "tu_post_err",
            {},
        )

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        failed = [e for e in events if e["event_type"] == "action_failed"]
        assert len(failed) == 1
        assert "ToolExecutionError" in failed[0]["payload"]["exception_type"]

    async def test_closes_subagent_task(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_sub_close",
            endpoint=mock_server.url,
            agent_name="sub-close-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-sub-close"}, "tu_s", {})
        await hooks.on_pre_tool_use(
            {
                "tool_name": "Task",
                "tool_input": {"description": "helper-sub"},
            },
            "tu_sub_close",
            {},
        )
        mock_server.clear()

        await hooks.on_post_tool_use(
            {
                "tool_name": "Task",
                "tool_result": "subagent result here",
            },
            "tu_sub_close",
            {},
        )

        with hooks.state.subagent_lock:
            assert "tu_sub_close" not in hooks.state.subagent_tasks

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        completed = [e for e in events if e["event_type"] == "task_completed"]
        assert len(completed) >= 1

    async def test_approval_received(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_approval",
            endpoint=mock_server.url,
            agent_name="approval-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-approval"}, "tu_s", {})
        await hooks.on_pre_tool_use(
            {
                "tool_name": "AskUserQuestion",
                "tool_input": {"questions": [{"question": "Continue?"}]},
            },
            "tu_approval",
            {},
        )
        mock_server.clear()

        await hooks.on_post_tool_use(
            {
                "tool_name": "AskUserQuestion",
                "tool_result": "Yes, continue",
            },
            "tu_approval",
            {},
        )

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        received = [e for e in events if e["event_type"] == "approval_received"]
        assert len(received) == 1
        assert "Yes, continue" in received[0]["payload"]["summary"]

    async def test_noop_for_unknown_tool_use_id(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_noop",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        result = await hooks.on_post_tool_use(
            {"tool_name": "Read", "tool_result": "x"},
            "tu_unknown",
            {},
        )
        assert result == {}


# ---------------------------------------------------------------------------
# TestStop (async)
# ---------------------------------------------------------------------------

class TestStop:
    """on_stop closes the task with result capture."""

    async def test_completes_task_with_result(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_stop",
            endpoint=mock_server.url,
            agent_name="stop-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-stop-1"}, "tu_s", {})
        mock_server.clear()

        await hooks.on_stop(
            {"result": "Bug fixed in auth.py — added null check"},
            "tu_stop",
            {},
        )

        assert get_current_task() is None

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()

        custom = [e for e in events if e["event_type"] == "custom"]
        assert any(e["payload"].get("kind") == "agent_result" for e in custom)

        completed = [e for e in events if e["event_type"] == "task_completed"]
        assert len(completed) == 1

    async def test_noop_without_task(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_stop_noop",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        result = await hooks.on_stop({"result": "x"}, "tu", {})
        assert result == {}


# ---------------------------------------------------------------------------
# TestSessionEnd (async)
# ---------------------------------------------------------------------------

class TestSessionEnd:
    """on_session_end is the safety net."""

    async def test_closes_task_if_stop_didnt_fire(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_end",
            endpoint=mock_server.url,
            agent_name="end-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-end-1"}, "tu_s", {})
        mock_server.clear()

        await hooks.on_session_end({}, "tu_end", {})

        assert get_current_task() is None

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        completed = [e for e in events if e["event_type"] == "task_completed"]
        assert len(completed) == 1

    async def test_fails_task_on_error(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_end_err",
            endpoint=mock_server.url,
            agent_name="end-err-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-end-err"}, "tu_s", {})
        mock_server.clear()

        await hooks.on_session_end({"error": "Context limit reached"}, "tu_end", {})

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        failed = [e for e in events if e["event_type"] == "task_failed"]
        assert len(failed) == 1

    async def test_noop_after_stop(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_end_after",
            endpoint=mock_server.url,
            agent_name="end-after-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-after"}, "tu_s", {})
        await hooks.on_stop({"result": "done"}, "tu_stop", {})

        # Flush Stop's events before clearing, so they don't leak
        _flush_and_wait(hooks.state.hb)
        mock_server.clear()

        await hooks.on_session_end({}, "tu_end", {})

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        # No additional task_completed (task was already completed by Stop)
        completed = [e for e in events if e["event_type"] == "task_completed"]
        assert len(completed) == 0


# ---------------------------------------------------------------------------
# TestCleanup (async)
# ---------------------------------------------------------------------------

class TestCleanup:
    """cleanup() force-closes orphaned tool contexts and subagent tasks."""

    async def test_cleans_orphaned_tools(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_cleanup",
            endpoint=mock_server.url,
            agent_name="cleanup-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-cleanup"}, "tu_s", {})

        await hooks.on_pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "/a.py"}},
            "tu_orphan_1",
            {},
        )
        await hooks.on_pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            "tu_orphan_2",
            {},
        )

        with hooks.state.active_tools_lock:
            assert len(hooks.state.active_tools) == 2

        mock_server.clear()

        await hooks.cleanup()

        with hooks.state.active_tools_lock:
            assert len(hooks.state.active_tools) == 0

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        failed = [e for e in events if e["event_type"] == "action_failed"]
        assert len(failed) == 2
        for f in failed:
            assert "OrphanedToolError" in f["payload"]["exception_type"]

    async def test_cleans_orphaned_subagents(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_cleanup_sub",
            endpoint=mock_server.url,
            agent_name="cleanup-sub-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-cleanup-sub"}, "tu_s", {})
        await hooks.on_pre_tool_use(
            {"tool_name": "Task", "tool_input": {"description": "orphan-sub"}},
            "tu_orphan_sub",
            {},
        )

        with hooks.state.subagent_lock:
            assert len(hooks.state.subagent_tasks) == 1

        await hooks.cleanup()

        with hooks.state.subagent_lock:
            assert len(hooks.state.subagent_tasks) == 0


# ---------------------------------------------------------------------------
# TestSafeHook (async)
# ---------------------------------------------------------------------------

class TestSafeHook:
    """_safe_hook suppresses exceptions and returns {}."""

    async def test_suppresses_exception(self, mock_server):
        from hiveloop.integrations.claude_agent_sdk import _safe_hook, _SessionState

        config = ClaudeAgentSDKConfig(api_key="test", debug=True)
        state = _SessionState(config=config, hb=None)

        async def _bad_hook(input_data, tool_use_id, context):
            raise RuntimeError("intentional crash")

        safe = _safe_hook(_bad_hook, state)
        result = await safe({}, "tu", {})
        assert result == {}

    async def test_returns_normal_result(self, mock_server):
        from hiveloop.integrations.claude_agent_sdk import _safe_hook, _SessionState

        config = ClaudeAgentSDKConfig(api_key="test", debug=False)
        state = _SessionState(config=config, hb=None)

        async def _good_hook(input_data, tool_use_id, context):
            return {"decision": "allow"}

        safe = _safe_hook(_good_hook, state)
        result = await safe({}, "tu", {})
        assert result == {"decision": "allow"}


# ---------------------------------------------------------------------------
# TestFullLifecycle (async)
# ---------------------------------------------------------------------------

class TestFullLifecycle:
    """End-to-end: SessionStart → tools → Stop → SessionEnd."""

    async def test_full_session(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_full",
            endpoint=mock_server.url,
            agent_name="full-agent",
            project="test-proj",
            heartbeat_interval=0,
        )

        # 1. SessionStart
        await hooks.on_session_start({"session_id": "full-session-1"}, "tu_s", {})

        # 2. Read file
        await hooks.on_pre_tool_use(
            {"tool_name": "Read", "tool_input": {"file_path": "/auth.py"}},
            "tu_1",
            {},
        )
        await hooks.on_post_tool_use(
            {"tool_name": "Read", "tool_result": "def login(): ..."},
            "tu_1",
            {},
        )

        # 3. Edit file
        await hooks.on_pre_tool_use(
            {"tool_name": "Edit", "tool_input": {"file_path": "/auth.py"}},
            "tu_2",
            {},
        )
        await hooks.on_post_tool_use(
            {"tool_name": "Edit", "tool_result": "edited successfully"},
            "tu_2",
            {},
        )

        # 4. Bash test
        await hooks.on_pre_tool_use(
            {"tool_name": "Bash", "tool_input": {"command": "pytest tests/"}},
            "tu_3",
            {},
        )
        await hooks.on_post_tool_use(
            {"tool_name": "Bash", "tool_result": "3 passed"},
            "tu_3",
            {},
        )

        # 5. Stop
        await hooks.on_stop({"result": "Fixed the auth bug"}, "tu_stop", {})

        # 6. SessionEnd
        await hooks.on_session_end({}, "tu_end", {})

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        types = [e["event_type"] for e in events]

        assert "agent_registered" in types
        assert "task_started" in types
        assert types.count("action_started") == 3
        assert types.count("action_completed") == 3
        assert "custom" in types  # agent_result
        assert "task_completed" in types
        assert "task_failed" not in types

        with hooks.state.active_tools_lock:
            assert len(hooks.state.active_tools) == 0


# ---------------------------------------------------------------------------
# TestLLMCostInterception (Phase 2)
# ---------------------------------------------------------------------------

class TestLLMCostInterception:
    """Phase 2 message stream interception."""

    async def test_captures_usage_metadata(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_llm",
            endpoint=mock_server.url,
            agent_name="llm-agent",
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-llm"}, "tu_s", {})
        mock_server.clear()

        @dataclass
        class FakeUsage:
            input_tokens: int = 500
            output_tokens: int = 100

        @dataclass
        class FakeMessage:
            model: str = "claude-sonnet-4-20250514"
            usage: FakeUsage = None

            def __post_init__(self):
                if self.usage is None:
                    self.usage = FakeUsage()

        _try_capture_llm_usage(FakeMessage())

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        custom = [e for e in events if e["event_type"] == "custom"]
        llm_events = [e for e in custom if e.get("payload", {}).get("kind") == "llm_call"]
        assert len(llm_events) == 1
        data = llm_events[0]["payload"]["data"]
        assert data["model"] == "claude-sonnet-4-20250514"
        assert data["tokens_in"] == 500
        assert data["tokens_out"] == 100
        assert data["cost"] > 0

    async def test_noop_without_usage(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_llm_noop",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-llm-noop"}, "tu_s", {})
        mock_server.clear()

        @dataclass
        class FakeMessageNoUsage:
            text: str = "hello"

        _try_capture_llm_usage(FakeMessageNoUsage())

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        custom = [e for e in events if e["event_type"] == "custom"]
        assert len(custom) == 0

    async def test_instrumented_query(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_iq",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "task-iq"}, "tu_s", {})
        mock_server.clear()

        @dataclass
        class FakeUsage:
            input_tokens: int = 200
            output_tokens: int = 50

        @dataclass
        class FakeMessage:
            model: str = "claude-haiku-4-20250514"
            usage: FakeUsage = None
            text: str = "response"

            def __post_init__(self):
                if self.usage is None:
                    self.usage = FakeUsage()

        async def fake_query():
            yield FakeMessage()
            yield FakeMessage(text="done")

        collected = []
        async for msg in instrumented_query(fake_query):
            collected.append(msg)

        assert len(collected) == 2
        assert collected[0].text == "response"
        assert collected[1].text == "done"

        _flush_and_wait(hooks.state.hb)
        events = mock_server.all_events()
        custom = [e for e in events if e["event_type"] == "custom"]
        llm_events = [e for e in custom if e.get("payload", {}).get("kind") == "llm_call"]
        assert len(llm_events) == 2


# ---------------------------------------------------------------------------
# TestGetAccessors (async)
# ---------------------------------------------------------------------------

class TestGetAccessors:
    """get_current_task / get_current_agent public API."""

    def test_none_by_default(self):
        assert get_current_task() is None
        assert get_current_agent() is None

    async def test_set_after_session_start(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_get",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "get-test"}, "tu_s", {})
        assert get_current_task() is not None
        assert get_current_agent() is not None

    async def test_cleared_after_stop(self, mock_server):
        hooks = hiveloop_hooks(
            api_key="hb_test_get_clear",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        await hooks.on_session_start({"session_id": "get-clear"}, "tu_s", {})
        await hooks.on_stop({"result": "done"}, "tu_stop", {})
        assert get_current_task() is None
        # Agent persists (for potential resumed sessions)
        assert get_current_agent() is not None


# ---------------------------------------------------------------------------
# TestConcurrentSessions — new test for isolation guarantee
# ---------------------------------------------------------------------------

class TestConcurrentSessions:
    """Verify that two hiveloop_hooks() invocations have independent state."""

    def test_independent_state(self, mock_server):
        hooks_a = hiveloop_hooks(
            api_key="hb_test_a",
            endpoint=mock_server.url,
            agent_name="agent-a",
            heartbeat_interval=0,
        )
        hooks_b = hiveloop_hooks(
            api_key="hb_test_b",
            endpoint=mock_server.url,
            agent_name="agent-b",
            heartbeat_interval=0,
        )

        # State objects should be different instances
        assert hooks_a.state is not hooks_b.state
        assert hooks_a.state.active_tools is not hooks_b.state.active_tools
        assert hooks_a.state.subagent_tasks is not hooks_b.state.subagent_tasks
        assert hooks_a.state.config.agent_name == "agent-a"
        assert hooks_b.state.config.agent_name == "agent-b"

    def test_independent_active_tools(self, mock_server):
        hooks_a = hiveloop_hooks(
            api_key="hb_test_a",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )
        hooks_b = hiveloop_hooks(
            api_key="hb_test_b",
            endpoint=mock_server.url,
            heartbeat_interval=0,
        )

        # Mutating one state doesn't affect the other
        hooks_a.state.active_tools["tool-1"] = {"test": True}
        assert "tool-1" not in hooks_b.state.active_tools

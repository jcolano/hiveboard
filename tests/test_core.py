"""Tests for HiveLoop core primitives.

Covers: init singleton, agent registration, heartbeat, task lifecycle
(started/completed/failed), context manager exception handling,
thread-local task isolation.
"""

from __future__ import annotations

import threading
import time

import pytest

import hiveloop
from hiveloop import HiveLoopError


class TestInit:
    """Module singleton behavior."""

    def test_init_returns_hiveboard(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        assert isinstance(hb, hiveloop.HiveBoard)

    def test_init_validates_api_key(self):
        with pytest.raises(HiveLoopError, match="must start with 'hb_'"):
            hiveloop.init(api_key="bad_key_123")

    def test_init_singleton(self, mock_server):
        hb1 = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        hb2 = hiveloop.init(
            api_key="hb_test_different",
            endpoint=mock_server.url,
        )
        assert hb1 is hb2

    def test_reset_allows_reinit(self, mock_server):
        hb1 = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        hiveloop.reset()
        hb2 = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        assert hb1 is not hb2


class TestAgentRegistration:
    """Agent creation and registration events."""

    def test_agent_emits_registered_event(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
            batch_size=100,
        )
        agent = hb.agent(
            "test-agent",
            type="sales",
            version="1.0.0",
            heartbeat_interval=0,  # Disable heartbeat
        )
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        assert len(events) == 1
        assert events[0]["event_type"] == "agent_registered"
        assert "test-agent" in events[0]["payload"]["summary"]

    def test_agent_idempotent(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent1 = hb.agent("same-agent", heartbeat_interval=0)
        agent2 = hb.agent("same-agent", heartbeat_interval=0)
        assert agent1 is agent2

    def test_get_agent(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        hb.agent("my-agent", heartbeat_interval=0)
        assert hb.get_agent("my-agent") is not None
        assert hb.get_agent("nonexistent") is None


class TestHeartbeat:
    """Heartbeat emission."""

    def test_heartbeat_emitted(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )
        agent = hb.agent("hb-agent", heartbeat_interval=0.2)

        time.sleep(0.5)  # Wait for at least 1 heartbeat
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        types = [e["event_type"] for e in events]
        assert "heartbeat" in types

    def test_no_heartbeat_when_disabled(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=0.1,
        )
        agent = hb.agent("no-hb-agent", heartbeat_interval=0)

        time.sleep(0.5)
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        types = [e["event_type"] for e in events]
        hb_events = [t for t in types if t == "heartbeat"]
        assert len(hb_events) == 0


class TestTaskLifecycle:
    """Task context manager and manual lifecycle."""

    def test_task_context_manager_success(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("task-agent", heartbeat_interval=0)

        with agent.task("task-001", project="my-project", type="test_task") as task:
            pass  # Task completes successfully

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        types = [e["event_type"] for e in events]
        assert "agent_registered" in types
        assert "task_started" in types
        assert "task_completed" in types

        # Verify task fields
        started = next(e for e in events if e["event_type"] == "task_started")
        assert started["task_id"] == "task-001"
        assert started["project_id"] == "my-project"

        completed = next(e for e in events if e["event_type"] == "task_completed")
        assert completed["status"] == "success"
        assert "duration_ms" in completed

    def test_task_context_manager_failure(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("task-agent", heartbeat_interval=0)

        with pytest.raises(ValueError, match="test error"):
            with agent.task("task-002") as task:
                raise ValueError("test error")

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        types = [e["event_type"] for e in events]
        assert "task_failed" in types

        failed = next(e for e in events if e["event_type"] == "task_failed")
        assert failed["status"] == "failure"
        assert failed["payload"]["exception_type"] == "ValueError"
        assert "test error" in failed["payload"]["exception_message"]

    def test_task_manual_lifecycle(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("manual-agent", heartbeat_interval=0)

        task = agent.start_task("task-003", project="proj")
        task.complete()

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        types = [e["event_type"] for e in events]
        assert "task_started" in types
        assert "task_completed" in types

    def test_task_manual_fail(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("manual-agent", heartbeat_interval=0)

        task = agent.start_task("task-004")
        task.fail(RuntimeError("something broke"))

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        failed = next(e for e in events if e["event_type"] == "task_failed")
        assert failed["payload"]["exception_type"] == "RuntimeError"

    def test_task_scoped_event(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("event-agent", heartbeat_interval=0)

        with agent.task("task-005", project="proj") as task:
            task.event("custom", payload={"summary": "Something happened"})

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        custom = [e for e in events if e["event_type"] == "custom"]
        assert len(custom) == 1
        assert custom[0]["task_id"] == "task-005"
        assert custom[0]["project_id"] == "proj"

    def test_agent_level_event(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("event-agent", heartbeat_interval=0)

        agent.event("custom", payload={"summary": "Agent-level event"})

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        custom = [e for e in events if e["event_type"] == "custom"]
        assert len(custom) == 1
        assert custom[0].get("task_id") is None


class TestThreadLocalIsolation:
    """Tasks are isolated per-thread."""

    def test_concurrent_tasks_isolated(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("thread-agent", heartbeat_interval=0)
        results = {}

        def run_task(task_id: str):
            with agent.task(task_id, project="proj") as task:
                time.sleep(0.05)
                task.event("custom", payload={"summary": f"From {task_id}"})
            results[task_id] = True

        threads = [
            threading.Thread(target=run_task, args=(f"task-t{i}",))
            for i in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        custom = [e for e in events if e["event_type"] == "custom"]
        # Each thread should have emitted its event with the correct task_id
        task_ids = {e["task_id"] for e in custom}
        assert task_ids == {"task-t0", "task-t1", "task-t2"}

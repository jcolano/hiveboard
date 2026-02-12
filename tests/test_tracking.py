"""Tests for @agent.track decorator and action nesting.

Covers: sync and async decorator, nesting (3 levels),
parent_action_id chains, exception propagation, context manager alternative.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import hiveloop


class TestSyncTracking:
    """@agent.track with synchronous functions."""

    def test_track_emits_started_completed(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("track-agent", heartbeat_interval=0)

        @agent.track("do_work")
        def do_work():
            return 42

        with agent.task("task-track") as task:
            result = do_work()

        assert result == 42
        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        action_started = [e for e in events if e["event_type"] == "action_started"]
        action_completed = [e for e in events if e["event_type"] == "action_completed"]
        assert len(action_started) == 1
        assert len(action_completed) == 1
        assert action_started[0]["payload"]["action_name"] == "do_work"
        assert action_completed[0]["status"] == "success"
        assert "duration_ms" in action_completed[0]

    def test_track_exception_propagates(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("track-agent", heartbeat_interval=0)

        @agent.track("fail_work")
        def fail_work():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            with agent.task("task-fail-track") as task:
                fail_work()

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        action_failed = [e for e in events if e["event_type"] == "action_failed"]
        assert len(action_failed) == 1
        assert action_failed[0]["payload"]["exception_type"] == "RuntimeError"
        assert "boom" in action_failed[0]["payload"]["exception_message"]

    def test_nesting_three_levels(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("nest-agent", heartbeat_interval=0)

        @agent.track("level3")
        def level3():
            return "done"

        @agent.track("level2")
        def level2():
            return level3()

        @agent.track("level1")
        def level1():
            return level2()

        with agent.task("task-nest") as task:
            level1()

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        started = [e for e in events if e["event_type"] == "action_started"]
        completed = [e for e in events if e["event_type"] == "action_completed"]

        assert len(started) == 3
        assert len(completed) == 3

        # Verify parent_action_id chain
        action_map = {}
        for e in started:
            action_map[e["payload"]["action_name"]] = e

        # level1 has no parent action
        assert action_map["level1"].get("parent_action_id") is None

        # level2 parent is level1
        assert action_map["level2"]["parent_action_id"] == action_map["level1"]["action_id"]

        # level3 parent is level2
        assert action_map["level3"]["parent_action_id"] == action_map["level2"]["action_id"]


class TestAsyncTracking:
    """@agent.track with async functions."""

    def test_async_track(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("async-agent", heartbeat_interval=0)

        @agent.track("async_work")
        async def async_work():
            await asyncio.sleep(0.01)
            return "async result"

        async def run():
            with agent.task("task-async") as task:
                result = await async_work()
                return result

        result = asyncio.run(run())
        assert result == "async result"

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        action_started = [e for e in events if e["event_type"] == "action_started"]
        action_completed = [e for e in events if e["event_type"] == "action_completed"]
        assert len(action_started) == 1
        assert len(action_completed) == 1
        assert action_started[0]["payload"]["action_name"] == "async_work"

    def test_async_exception_propagates(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("async-agent", heartbeat_interval=0)

        @agent.track("async_fail")
        async def async_fail():
            raise ValueError("async boom")

        async def run():
            with agent.task("task-async-fail") as task:
                await async_fail()

        with pytest.raises(ValueError, match="async boom"):
            asyncio.run(run())

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        failed = [e for e in events if e["event_type"] == "action_failed"]
        assert len(failed) == 1

    def test_mixed_sync_async_nesting(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("mixed-agent", heartbeat_interval=0)

        @agent.track("inner_sync")
        def inner_sync():
            return "sync"

        @agent.track("outer_async")
        async def outer_async():
            return inner_sync()

        async def run():
            with agent.task("task-mixed") as task:
                return await outer_async()

        result = asyncio.run(run())
        assert result == "sync"

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        started = [e for e in events if e["event_type"] == "action_started"]
        assert len(started) == 2

        # Verify nesting
        action_map = {}
        for e in started:
            action_map[e["payload"]["action_name"]] = e

        assert action_map["inner_sync"]["parent_action_id"] == action_map["outer_async"]["action_id"]


class TestTrackContext:
    """agent.track_context() context manager."""

    def test_track_context_success(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("ctx-agent", heartbeat_interval=0)

        with agent.task("task-ctx") as task:
            with agent.track_context("manual_step") as action:
                action.set_payload({"key": "value"})

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        completed = [e for e in events if e["event_type"] == "action_completed"]
        assert len(completed) == 1
        assert completed[0]["payload"]["action_name"] == "manual_step"
        assert completed[0]["payload"]["key"] == "value"

    def test_track_context_exception(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("ctx-agent", heartbeat_interval=0)

        with pytest.raises(TypeError):
            with agent.task("task-ctx-err") as task:
                with agent.track_context("fail_step") as action:
                    raise TypeError("ctx error")

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        failed = [e for e in events if e["event_type"] == "action_failed"]
        assert len(failed) == 1
        assert failed[0]["payload"]["exception_type"] == "TypeError"


class TestAutoPopulatedPayload:
    """Action events include function metadata."""

    def test_function_fully_qualified(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("payload-agent", heartbeat_interval=0)

        @agent.track("my_func")
        def my_func():
            pass

        with agent.task("task-fqn") as task:
            my_func()

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        started = next(e for e in events if e["event_type"] == "action_started")
        assert "function" in started["payload"]
        assert "my_func" in started["payload"]["function"]

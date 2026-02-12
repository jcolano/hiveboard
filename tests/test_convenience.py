"""Tests for HiveLoop convenience methods.

Covers: llm_call (task + agent), plan, plan_step, queue_snapshot,
todo, scheduled, report_issue, resolve_issue.
"""

from __future__ import annotations

import time

import hiveloop


def _find_events(events: list, kind: str) -> list:
    """Filter custom events by payload.kind."""
    return [
        e
        for e in events
        if e.get("event_type") == "custom"
        and e.get("payload", {}).get("kind") == kind
    ]


class TestLlmCall:
    """task.llm_call() and agent.llm_call()."""

    def test_task_llm_call(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("llm-agent", heartbeat_interval=0)

        with agent.task("task-llm", project="proj") as task:
            task.llm_call(
                "reasoning",
                "claude-sonnet-4-20250514",
                tokens_in=1200,
                tokens_out=350,
                cost=0.008,
                duration_ms=2100,
            )

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        llm_events = _find_events(events, "llm_call")
        assert len(llm_events) == 1
        e = llm_events[0]
        assert e["task_id"] == "task-llm"
        assert e["project_id"] == "proj"
        assert e["payload"]["data"]["model"] == "claude-sonnet-4-20250514"
        assert e["payload"]["data"]["tokens_in"] == 1200
        assert e["payload"]["data"]["cost"] == 0.008
        assert "1200 in / 350 out" in e["payload"]["summary"]
        assert "$0.008" in e["payload"]["summary"]

    def test_agent_llm_call(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("llm-agent", heartbeat_interval=0)

        agent.llm_call(
            "triage",
            "gpt-4o",
            tokens_in=800,
            tokens_out=200,
            cost=0.004,
        )

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        llm_events = _find_events(events, "llm_call")
        assert len(llm_events) == 1
        e = llm_events[0]
        assert e.get("task_id") is None
        assert e["payload"]["data"]["name"] == "triage"

    def test_llm_call_summary_without_tokens(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("llm-agent", heartbeat_interval=0)

        agent.llm_call("quick_call", "gpt-4")

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        llm_events = _find_events(events, "llm_call")
        assert len(llm_events) == 1
        # Summary without tokens is just "name → model"
        assert llm_events[0]["payload"]["summary"] == "quick_call \u2192 gpt-4"


class TestPlan:
    """task.plan() and task.plan_step()."""

    def test_plan_created(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("plan-agent", heartbeat_interval=0)

        with agent.task("task-plan", project="proj") as task:
            task.plan("Process lead", ["Score", "Enrich", "Route"])

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        plans = _find_events(events, "plan_created")
        assert len(plans) == 1
        p = plans[0]
        assert p["payload"]["summary"] == "Process lead"
        assert p["payload"]["data"]["goal"] == "Process lead"
        assert len(p["payload"]["data"]["steps"]) == 3
        assert p["payload"]["data"]["revision"] == 0

    def test_plan_step(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("plan-agent", heartbeat_interval=0)

        with agent.task("task-plan-step") as task:
            task.plan("Goal", ["Step A", "Step B"])
            task.plan_step(0, "completed", "Step A done", turns=2, tokens=3200)
            task.plan_step(1, "started", "Step B starting")

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        steps = _find_events(events, "plan_step")
        assert len(steps) == 2

        step0 = steps[0]
        assert step0["payload"]["data"]["step_index"] == 0
        assert step0["payload"]["data"]["action"] == "completed"
        assert step0["payload"]["data"]["total_steps"] == 2  # Inherited from plan
        assert step0["payload"]["data"]["turns"] == 2
        assert "Step 0 completed: Step A done" in step0["payload"]["summary"]


class TestQueueSnapshot:
    """agent.queue_snapshot()."""

    def test_queue_snapshot(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("queue-agent", heartbeat_interval=0)

        agent.queue_snapshot(
            depth=3,
            oldest_age_seconds=120,
            items=[
                {"id": "q1", "priority": "high", "summary": "Item 1"},
                {"id": "q2", "priority": "normal", "summary": "Item 2"},
                {"id": "q3", "priority": "low", "summary": "Item 3"},
            ],
            processing={"id": "q0", "summary": "Processing", "elapsed_ms": 5000},
        )

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        qs = _find_events(events, "queue_snapshot")
        assert len(qs) == 1
        assert qs[0]["payload"]["data"]["depth"] == 3
        assert qs[0]["payload"]["summary"] == "Queue: 3 items, oldest 120s"
        assert len(qs[0]["payload"]["data"]["items"]) == 3


class TestTodo:
    """agent.todo()."""

    def test_todo_created(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("todo-agent", heartbeat_interval=0)

        agent.todo(
            todo_id="todo-001",
            action="created",
            summary="Follow up on failed enrichment",
            priority="high",
            source="failed_action",
        )

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        todos = _find_events(events, "todo")
        assert len(todos) == 1
        assert todos[0]["payload"]["data"]["todo_id"] == "todo-001"
        assert todos[0]["payload"]["data"]["action"] == "created"
        assert todos[0]["payload"]["summary"] == "Follow up on failed enrichment"

    def test_todo_completed(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("todo-agent", heartbeat_interval=0)

        agent.todo(
            todo_id="todo-001",
            action="completed",
            summary="Enrichment succeeded",
        )

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        todos = _find_events(events, "todo")
        assert len(todos) == 1
        assert todos[0]["payload"]["data"]["action"] == "completed"


class TestScheduled:
    """agent.scheduled()."""

    def test_scheduled_items(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("sched-agent", heartbeat_interval=0)

        agent.scheduled(items=[
            {"id": "s1", "name": "CRM Sync", "next_run": "2026-02-11T15:00:00Z", "interval": "1h", "enabled": True},
            {"id": "s2", "name": "Digest", "next_run": "2026-02-12T08:00:00Z", "interval": "daily", "enabled": True},
        ])

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        scheds = _find_events(events, "scheduled")
        assert len(scheds) == 1
        assert len(scheds[0]["payload"]["data"]["items"]) == 2
        assert "2 scheduled items" in scheds[0]["payload"]["summary"]
        assert "15:00:00Z" in scheds[0]["payload"]["summary"]


class TestIssues:
    """agent.report_issue() and agent.resolve_issue()."""

    def test_report_issue(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("issue-agent", heartbeat_interval=0)

        agent.report_issue(
            summary="CRM API 403",
            severity="high",
            category="permissions",
            context={"tool": "crm_search", "error_code": 403},
            occurrence_count=3,
        )

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        issues = _find_events(events, "issue")
        assert len(issues) == 1
        assert issues[0]["payload"]["data"]["severity"] == "high"
        assert issues[0]["payload"]["data"]["action"] == "reported"
        assert issues[0]["payload"]["data"]["category"] == "permissions"
        assert issues[0]["payload"]["summary"] == "CRM API 403"

    def test_resolve_issue(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("issue-agent", heartbeat_interval=0)

        agent.resolve_issue(summary="CRM API 403", issue_id="issue-crm")

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        issues = _find_events(events, "issue")
        assert len(issues) == 1
        assert issues[0]["payload"]["data"]["action"] == "resolved"
        assert issues[0]["payload"]["data"]["issue_id"] == "issue-crm"


class TestSampleFixtureShape:
    """Verify our events match the shape in shared/fixtures/sample_batch.json."""

    def test_event_has_required_fields(self, mock_server):
        hb = hiveloop.init(
            api_key="hb_test_abc123",
            endpoint=mock_server.url,
            flush_interval=60,
        )
        agent = hb.agent("shape-agent", heartbeat_interval=0)

        with agent.task("task-shape", project="proj", type="test") as task:
            task.llm_call("call1", "model1", tokens_in=100, tokens_out=50, cost=0.01)

        hb.flush()
        time.sleep(0.3)

        events = mock_server.all_events()
        for e in events:
            assert "event_id" in e
            assert "timestamp" in e
            assert "event_type" in e
            assert "severity" in e

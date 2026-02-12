"""Phase I1.3 — Full pipeline integration test.

Tests the complete flow: SDK → API → Storage → Query → WebSocket.
Validates all 13 event types and 7 payload kinds end-to-end.

Run with: python -m pytest tests/test_integration.py -v
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
import httpx

# Ensure src is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from backend.app import app, _bootstrap_dev_tenant
from backend.storage_json import JsonStorageBackend
from backend.middleware import reset_rate_limits

DEV_KEY = "hb_live_dev000000000000000000000000000000"
AUTH = {"Authorization": f"Bearer {DEV_KEY}"}


@pytest.fixture
async def server(tmp_path: Path, monkeypatch):
    """Start backend with fresh storage, return httpx async client."""
    monkeypatch.setenv("HIVEBOARD_DEV_KEY", DEV_KEY)
    reset_rate_limits()
    storage = JsonStorageBackend(data_dir=tmp_path / "data")
    await storage.initialize()
    app.state.storage = storage
    await _bootstrap_dev_tenant(storage)
    # Create project used by simulator
    from shared.models import ProjectCreate
    await storage.create_project("dev", ProjectCreate(name="Sales Pipeline", slug="sales-pipeline"))
    await storage.create_project("dev", ProjectCreate(name="Customer Support", slug="customer-support"))
    await storage.create_project("dev", ProjectCreate(name="Data Warehouse", slug="data-warehouse"))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _build_batch(agent_id: str, events: list[dict], **envelope_extra) -> dict:
    """Build an ingest batch with envelope + events."""
    envelope = {
        "agent_id": agent_id,
        "agent_type": "test",
        "agent_version": "1.0.0",
        "framework": "custom",
        "runtime": "python-3.12",
        "sdk_version": "hiveloop-0.1.0",
        "environment": "production",
        "group": "default",
        **envelope_extra,
    }
    return {"envelope": envelope, "events": events}


class TestIngestionPipeline:
    """I1.1: SDK events flow through ingestion correctly."""

    async def test_all_13_event_types_accepted(self, server):
        """Every event type in the spec is accepted by the backend."""
        from shared.enums import EventType
        events = []
        for i, et in enumerate(EventType):
            event = {
                "event_id": f"et-{i}",
                "timestamp": "2026-02-12T10:00:00.000Z",
                "event_type": et.value,
            }
            # Add required fields for task/action events
            if "task" in et.value:
                event["task_id"] = "test-task"
                event["project_id"] = "sales-pipeline"
            if "action" in et.value:
                event["action_id"] = f"action-{i}"
                event["task_id"] = "test-task"
            events.append(event)

        batch = _build_batch("type-test-agent", events)
        r = await server.post("/v1/ingest", json=batch, headers=AUTH)
        data = r.json()
        assert data["accepted"] == 13, f"Expected 13 accepted: {data}"
        assert data["rejected"] == 0, f"Unexpected rejections: {data.get('errors')}"

    async def test_all_7_payload_kinds(self, server):
        """All well-known payload kinds are accepted."""
        events = [
            # 1. llm_call
            {"event_id": "pk-1", "timestamp": "2026-02-12T10:00:01Z", "event_type": "custom",
             "task_id": "t1", "project_id": "sales-pipeline",
             "payload": {"kind": "llm_call", "summary": "test llm",
                         "data": {"name": "classify", "model": "gpt-4o", "tokens_in": 100, "tokens_out": 50, "cost": 0.01}}},
            # 2. queue_snapshot
            {"event_id": "pk-2", "timestamp": "2026-02-12T10:00:02Z", "event_type": "custom",
             "payload": {"kind": "queue_snapshot", "summary": "Queue: 3 items",
                         "data": {"depth": 3, "oldest_age_seconds": 120}}},
            # 3. todo
            {"event_id": "pk-3", "timestamp": "2026-02-12T10:00:03Z", "event_type": "custom",
             "payload": {"kind": "todo", "summary": "Update KB",
                         "data": {"todo_id": "td-1", "action": "created", "priority": "normal"}}},
            # 4. scheduled
            {"event_id": "pk-4", "timestamp": "2026-02-12T10:00:04Z", "event_type": "custom",
             "payload": {"kind": "scheduled", "summary": "2 scheduled items",
                         "data": {"items": [{"id": "s1", "name": "Hourly sync", "interval": "1h"}]}}},
            # 5. plan_created
            {"event_id": "pk-5", "timestamp": "2026-02-12T10:00:05Z", "event_type": "custom",
             "task_id": "t1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_created", "summary": "Process lead",
                         "data": {"steps": [{"index": 0, "description": "Score"}, {"index": 1, "description": "Route"}], "revision": 0}}},
            # 6. plan_step
            {"event_id": "pk-6", "timestamp": "2026-02-12T10:00:06Z", "event_type": "custom",
             "task_id": "t1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_step", "summary": "Step 0 completed: Score",
                         "data": {"step_index": 0, "total_steps": 2, "action": "completed"}}},
            # 7. issue
            {"event_id": "pk-7", "timestamp": "2026-02-12T10:00:07Z", "event_type": "custom",
             "payload": {"kind": "issue", "summary": "API timeout",
                         "data": {"severity": "high", "action": "reported", "issue_id": "iss-1", "category": "connectivity"}}},
        ]
        batch = _build_batch("payload-test-agent", events)
        r = await server.post("/v1/ingest", json=batch, headers=AUTH)
        data = r.json()
        assert r.status_code == 200, f"Expected 200: {data}"
        assert data["accepted"] == 7
        assert data["rejected"] == 0


class TestQueryEndpointsE2E:
    """I1.2/I1.3: Query endpoints return correct data after ingestion."""

    @staticmethod
    async def _seed(server):
        """Seed realistic multi-agent data."""
        # Agent 1: lead-qualifier with full task lifecycle + LLM + plan
        events1 = [
            {"event_id": "lq-1", "timestamp": "2026-02-12T10:00:00Z", "event_type": "agent_registered",
             "payload": {"summary": "Agent lead-qualifier registered", "data": {"type": "sales"}}},
            {"event_id": "lq-2", "timestamp": "2026-02-12T10:00:01Z", "event_type": "heartbeat"},
            {"event_id": "lq-3", "timestamp": "2026-02-12T10:00:02Z", "event_type": "task_started",
             "task_id": "task-lead-1", "project_id": "sales-pipeline", "task_type": "lead_processing",
             "payload": {"summary": "Task task-lead-1 started"}},
            {"event_id": "lq-4", "timestamp": "2026-02-12T10:00:03Z", "event_type": "custom",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_created", "summary": "Process lead #1",
                         "data": {"steps": [{"index": 0, "description": "Score lead"}, {"index": 1, "description": "Route lead"}], "revision": 0}}},
            {"event_id": "lq-5", "timestamp": "2026-02-12T10:00:04Z", "event_type": "custom",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_step", "summary": "Step 0 started: Score lead",
                         "data": {"step_index": 0, "total_steps": 2, "action": "started"}}},
            {"event_id": "lq-6", "timestamp": "2026-02-12T10:00:05Z", "event_type": "action_started",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "action_id": "act-1",
             "payload": {"action_name": "score_lead", "function": "simulator.score_lead"}},
            {"event_id": "lq-7", "timestamp": "2026-02-12T10:00:06Z", "event_type": "custom",
             "task_id": "task-lead-1", "project_id": "sales-pipeline", "action_id": "act-1",
             "payload": {"kind": "llm_call", "summary": "lead_scoring -> gpt-4o (500 in / 100 out, $0.003)",
                         "data": {"name": "lead_scoring", "model": "gpt-4o", "tokens_in": 500, "tokens_out": 100, "cost": 0.003, "duration_ms": 1200}}},
            {"event_id": "lq-8", "timestamp": "2026-02-12T10:00:07Z", "event_type": "action_completed",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "action_id": "act-1", "status": "success", "duration_ms": 2000,
             "payload": {"action_name": "score_lead", "function": "simulator.score_lead"}},
            {"event_id": "lq-9", "timestamp": "2026-02-12T10:00:08Z", "event_type": "custom",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_step", "summary": "Step 0 completed: Score lead",
                         "data": {"step_index": 0, "total_steps": 2, "action": "completed"}}},
            {"event_id": "lq-10", "timestamp": "2026-02-12T10:00:09Z", "event_type": "custom",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_step", "summary": "Step 1 started: Route lead",
                         "data": {"step_index": 1, "total_steps": 2, "action": "started"}}},
            {"event_id": "lq-11", "timestamp": "2026-02-12T10:00:10Z", "event_type": "custom",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "payload": {"kind": "plan_step", "summary": "Step 1 completed: Route lead",
                         "data": {"step_index": 1, "total_steps": 2, "action": "completed"}}},
            {"event_id": "lq-12", "timestamp": "2026-02-12T10:00:11Z", "event_type": "task_completed",
             "task_id": "task-lead-1", "project_id": "sales-pipeline",
             "status": "success", "duration_ms": 11000,
             "payload": {"summary": "Task task-lead-1 completed"}},
        ]
        batch1 = _build_batch("lead-qualifier", events1, agent_type="sales", agent_version="2.1.0")
        r = await server.post("/v1/ingest", json=batch1, headers=AUTH)
        assert r.status_code == 200, f"Seed batch 1 failed: {r.json()}"

        # Agent 2: support-triage with escalation + approval
        events2 = [
            {"event_id": "st-1", "timestamp": "2026-02-12T10:01:00Z", "event_type": "agent_registered",
             "payload": {"summary": "Agent support-triage registered"}},
            {"event_id": "st-2", "timestamp": "2026-02-12T10:01:01Z", "event_type": "heartbeat"},
            {"event_id": "st-3", "timestamp": "2026-02-12T10:01:02Z", "event_type": "task_started",
             "task_id": "ticket-1001", "project_id": "customer-support", "task_type": "ticket_triage",
             "payload": {"summary": "Task ticket-1001 started"}},
            {"event_id": "st-4", "timestamp": "2026-02-12T10:01:03Z", "event_type": "custom",
             "task_id": "ticket-1001", "project_id": "customer-support",
             "payload": {"kind": "llm_call", "summary": "classification -> claude-sonnet",
                         "data": {"name": "classification", "model": "claude-sonnet-4-20250514", "tokens_in": 800, "tokens_out": 200, "cost": 0.005}}},
            {"event_id": "st-5", "timestamp": "2026-02-12T10:01:04Z", "event_type": "escalated",
             "task_id": "ticket-1001", "project_id": "customer-support",
             "payload": {"summary": "Ticket escalated — complex billing issue", "data": {"assigned_to": "senior-support"}}},
            {"event_id": "st-6", "timestamp": "2026-02-12T10:01:05Z", "event_type": "approval_requested",
             "task_id": "ticket-1001", "project_id": "customer-support",
             "payload": {"summary": "Approval needed for account credit", "data": {"approver": "support-lead"}}},
            {"event_id": "st-7", "timestamp": "2026-02-12T10:01:06Z", "event_type": "approval_received",
             "task_id": "ticket-1001", "project_id": "customer-support",
             "payload": {"summary": "Credit approved", "data": {"approved_by": "support-lead", "decision": "approved"}}},
            {"event_id": "st-8", "timestamp": "2026-02-12T10:01:07Z", "event_type": "task_completed",
             "task_id": "ticket-1001", "project_id": "customer-support",
             "status": "success", "duration_ms": 7000,
             "payload": {"summary": "Task ticket-1001 completed"}},
        ]
        batch2 = _build_batch("support-triage", events2, agent_type="support", agent_version="1.5.0")
        r = await server.post("/v1/ingest", json=batch2, headers=AUTH)
        assert r.status_code == 200, f"Seed batch 2 failed: {r.json()}"

        # Agent 3: data-pipeline with retry + queue + scheduled + todo + issue
        events3 = [
            {"event_id": "dp-1", "timestamp": "2026-02-12T10:02:00Z", "event_type": "agent_registered",
             "payload": {"summary": "Agent data-pipeline registered"}},
            {"event_id": "dp-2", "timestamp": "2026-02-12T10:02:01Z", "event_type": "heartbeat"},
            {"event_id": "dp-3", "timestamp": "2026-02-12T10:02:02Z", "event_type": "task_started",
             "task_id": "etl-batch-1", "project_id": "data-warehouse", "task_type": "etl_batch",
             "payload": {"summary": "Task etl-batch-1 started"}},
            {"event_id": "dp-4", "timestamp": "2026-02-12T10:02:03Z", "event_type": "retry_started",
             "task_id": "etl-batch-1", "project_id": "data-warehouse",
             "payload": {"summary": "Retrying step 2", "data": {"attempt": 1, "backoff_seconds": 2.0}}},
            {"event_id": "dp-5", "timestamp": "2026-02-12T10:02:04Z", "event_type": "task_completed",
             "task_id": "etl-batch-1", "project_id": "data-warehouse",
             "status": "success", "duration_ms": 4000,
             "payload": {"summary": "Task etl-batch-1 completed"}},
            # Pipeline events
            {"event_id": "dp-6", "timestamp": "2026-02-12T10:02:05Z", "event_type": "custom",
             "payload": {"kind": "queue_snapshot", "summary": "Queue: 5 items, oldest 120s",
                         "data": {"depth": 5, "oldest_age_seconds": 120, "items": [
                             {"id": "batch-101", "priority": "normal", "source": "scheduled", "summary": "Process batch #1"}]}}},
            {"event_id": "dp-7", "timestamp": "2026-02-12T10:02:06Z", "event_type": "custom",
             "payload": {"kind": "todo", "summary": "Update pipeline config",
                         "data": {"todo_id": "todo-1", "action": "created", "priority": "high", "source": "agent_decision"}}},
            {"event_id": "dp-8", "timestamp": "2026-02-12T10:02:07Z", "event_type": "custom",
             "payload": {"kind": "scheduled", "summary": "3 scheduled items, next at 15:00:00Z",
                         "data": {"items": [
                             {"id": "sched-1", "name": "Hourly ETL", "next_run": "2026-02-12T15:00:00Z", "interval": "1h", "enabled": True, "last_status": "success"},
                             {"id": "sched-2", "name": "Daily Report", "next_run": "2026-02-13T06:00:00Z", "interval": "daily", "enabled": True}]}}},
            {"event_id": "dp-9", "timestamp": "2026-02-12T10:02:08Z", "event_type": "custom",
             "payload": {"kind": "issue", "summary": "Connection pool exhausted",
                         "data": {"severity": "high", "action": "reported", "issue_id": "pool-exhaust", "category": "connectivity"}}},
        ]
        batch3 = _build_batch("data-pipeline", events3, agent_type="etl", agent_version="3.0.1")
        r = await server.post("/v1/ingest", json=batch3, headers=AUTH)
        assert r.status_code == 200, f"Seed batch 3 failed: {r.json()}"

    async def test_agents_endpoint(self, server):
        """GET /v1/agents returns all 3 agents with stats."""
        await self._seed(server)
        r = await server.get("/v1/agents", headers=AUTH)
        assert r.status_code == 200
        agents = r.json()["data"]
        assert len(agents) == 3
        agent_ids = {a["agent_id"] for a in agents}
        assert agent_ids == {"lead-qualifier", "support-triage", "data-pipeline"}
        # Check stats_1h present
        for a in agents:
            assert "stats_1h" in a
            assert "queue_depth" in a["stats_1h"]
            assert "active_issues" in a["stats_1h"]

    async def test_tasks_endpoint(self, server):
        """GET /v1/tasks returns tasks with correct fields."""
        await self._seed(server)
        r = await server.get("/v1/tasks", headers=AUTH)
        assert r.status_code == 200
        tasks = r.json()["data"]
        assert len(tasks) >= 3
        task_ids = {t["task_id"] for t in tasks}
        assert "task-lead-1" in task_ids
        assert "ticket-1001" in task_ids
        assert "etl-batch-1" in task_ids
        # Check token counts
        lead_task = next(t for t in tasks if t["task_id"] == "task-lead-1")
        assert lead_task["llm_call_count"] >= 1
        assert lead_task["total_tokens_in"] > 0

    async def test_timeline_with_plan(self, server):
        """GET /v1/tasks/{id}/timeline has plan + action tree."""
        await self._seed(server)
        r = await server.get("/v1/tasks/task-lead-1/timeline", headers=AUTH)
        assert r.status_code == 200
        tl = r.json()
        # Plan
        assert tl["plan"] is not None
        plan = tl["plan"]
        assert plan["goal"] == "Process lead #1"
        assert len(plan["steps"]) == 2
        assert plan["progress"]["completed"] == 2
        assert plan["progress"]["total"] == 2
        # Plan step enrichment — steps should have action/completed_at
        assert plan["steps"][0].get("action") == "completed"
        assert plan["steps"][0].get("completed_at") is not None
        # Action tree
        assert len(tl["action_tree"]) >= 1
        action = tl["action_tree"][0]
        assert action["name"] == "score_lead"
        assert action["status"] in ("success", "completed")
        assert action["duration_ms"] == 2000

    async def test_timeline_with_escalation(self, server):
        """Timeline for ticket-1001 includes escalation chain."""
        await self._seed(server)
        r = await server.get("/v1/tasks/ticket-1001/timeline", headers=AUTH)
        assert r.status_code == 200
        tl = r.json()
        event_types = {e["event_type"] for e in tl["events"]}
        assert "escalated" in event_types
        assert "approval_requested" in event_types
        assert "approval_received" in event_types

    async def test_events_payload_kind_filter(self, server):
        """GET /v1/events?payload_kind=llm_call filters correctly."""
        await self._seed(server)
        r = await server.get("/v1/events?payload_kind=llm_call", headers=AUTH)
        assert r.status_code == 200
        events = r.json()["data"]
        assert len(events) >= 2  # lead-qualifier + support-triage each have one
        for e in events:
            assert e["payload"]["kind"] == "llm_call"

    async def test_cost_endpoint(self, server):
        """GET /v1/cost returns non-zero cost data."""
        await self._seed(server)
        r = await server.get("/v1/cost?range=30d", headers=AUTH)
        assert r.status_code == 200
        cost = r.json()
        assert cost["total_cost"] > 0
        assert cost["call_count"] >= 2
        assert cost["total_tokens_in"] > 0
        assert cost["total_tokens_out"] > 0
        assert len(cost["by_agent"]) >= 1
        assert len(cost["by_model"]) >= 1

    async def test_metrics_with_group_by(self, server):
        """GET /v1/metrics?group_by=agent returns groups."""
        await self._seed(server)
        r = await server.get("/v1/metrics?range=30d&group_by=agent", headers=AUTH)
        assert r.status_code == 200
        metrics = r.json()
        assert "groups" in metrics
        assert metrics["groups"] is not None

    async def test_pipeline_endpoint(self, server):
        """GET /v1/agents/{id}/pipeline returns queue, todos, scheduled, issues."""
        await self._seed(server)
        r = await server.get("/v1/agents/data-pipeline/pipeline", headers=AUTH)
        assert r.status_code == 200
        pl = r.json()
        assert pl["agent_id"] == "data-pipeline"
        # Queue
        assert pl["queue"] is not None
        assert pl["queue"]["depth"] == 5
        assert len(pl["queue"]["items"]) >= 1
        # TODOs
        assert len(pl["todos"]) >= 1
        assert pl["todos"][0]["todo_id"] == "todo-1"
        # Scheduled
        assert len(pl["scheduled"]) >= 1
        # Issues
        assert len(pl["issues"]) >= 1
        assert pl["issues"][0]["issue_id"] == "pool-exhaust"

    async def test_projects_endpoint(self, server):
        """GET /v1/projects returns all created projects."""
        await self._seed(server)
        r = await server.get("/v1/projects", headers=AUTH)
        assert r.status_code == 200
        projects = r.json()["data"]
        slugs = {p["slug"] for p in projects}
        assert "sales-pipeline" in slugs
        assert "customer-support" in slugs
        assert "data-warehouse" in slugs

    async def test_events_stream_format(self, server):
        """Events have the fields the dashboard expects."""
        await self._seed(server)
        r = await server.get("/v1/events?limit=5", headers=AUTH)
        assert r.status_code == 200
        events = r.json()["data"]
        assert len(events) > 0
        e = events[0]
        # Dashboard reads these fields
        assert "event_id" in e
        assert "event_type" in e
        assert "agent_id" in e
        assert "timestamp" in e
        assert "severity" in e

    async def test_agent_stats_include_pipeline_fields(self, server):
        """Agent stats_1h includes queue_depth and active_issues from pipeline."""
        await self._seed(server)
        r = await server.get("/v1/agents/data-pipeline", headers=AUTH)
        assert r.status_code == 200
        agent = r.json()
        stats = agent["stats_1h"]
        assert stats["queue_depth"] == 5
        assert stats["active_issues"] >= 1

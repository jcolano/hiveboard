"""Storage tests — runs against StorageBackend protocol.

Same test suite will validate MS SQL Server later by swapping the fixture.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.storage_json import JsonStorageBackend, derive_agent_status
from shared.enums import AgentStatus, EventType, TaskStatus
from shared.models import (
    AlertRuleCreate,
    AlertHistoryRecord,
    Event,
    IngestEvent,
    Page,
    ProjectCreate,
    ProjectUpdate,
)


# ═══════════════════════════════════════════════════════════════════════════
#  TENANT & AUTH TESTS (B1.3.2)
# ═══════════════════════════════════════════════════════════════════════════


class TestTenantAndAuth:
    async def test_create_tenant(self, storage: JsonStorageBackend):
        t = await storage.create_tenant("t1", "Acme Corp", "acme")
        assert t.tenant_id == "t1"
        assert t.name == "Acme Corp"
        assert t.plan == "free"

    async def test_get_tenant(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme Corp", "acme")
        t = await storage.get_tenant("t1")
        assert t is not None
        assert t.slug == "acme"

    async def test_get_tenant_not_found(self, storage: JsonStorageBackend):
        t = await storage.get_tenant("nonexistent")
        assert t is None

    async def test_default_project_auto_created(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme Corp", "acme")
        projects = await storage.list_projects("t1")
        assert len(projects) == 1
        assert projects[0].slug == "default"

    async def test_create_api_key_and_authenticate(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        raw_key = "hb_live_abc123xyz"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        await storage.create_api_key(
            key_id="k1", tenant_id="t1", key_hash=key_hash,
            key_prefix="hb_live_", key_type="live", label="Test Key",
        )
        info = await storage.authenticate(key_hash)
        assert info is not None
        assert info.tenant_id == "t1"
        assert info.key_type == "live"

    async def test_authenticate_invalid_hash(self, storage: JsonStorageBackend):
        info = await storage.authenticate("bogus_hash")
        assert info is None

    async def test_revoke_key(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        raw_key = "hb_live_abc123xyz"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        await storage.create_api_key(
            key_id="k1", tenant_id="t1", key_hash=key_hash,
            key_prefix="hb_live_", key_type="live",
        )
        revoked = await storage.revoke_api_key("t1", "k1")
        assert revoked is True
        info = await storage.authenticate(key_hash)
        assert info is None

    async def test_list_api_keys(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        for i, kt in enumerate(["live", "test", "read"]):
            h = hashlib.sha256(f"key_{kt}".encode()).hexdigest()
            await storage.create_api_key(
                key_id=f"k{i}", tenant_id="t1", key_hash=h,
                key_prefix=f"hb_{kt}_", key_type=kt,
            )
        keys = await storage.list_api_keys("t1")
        assert len(keys) == 3

    async def test_touch_api_key(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        h = hashlib.sha256(b"mykey").hexdigest()
        await storage.create_api_key(
            key_id="k1", tenant_id="t1", key_hash=h,
            key_prefix="hb_live_", key_type="live",
        )
        await storage.touch_api_key("k1")
        keys = await storage.list_api_keys("t1")
        assert keys[0].last_used_at is not None


# ═══════════════════════════════════════════════════════════════════════════
#  PROJECT TESTS (B1.3.3)
# ═══════════════════════════════════════════════════════════════════════════


class TestProjects:
    async def test_create_project(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        p = await storage.create_project(
            "t1", ProjectCreate(name="Sales", slug="sales")
        )
        assert p.name == "Sales"
        assert p.project_id  # UUID generated

    async def test_list_projects_excludes_archived(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        await storage.create_project(
            "t1", ProjectCreate(name="Active", slug="active")
        )
        p2 = await storage.create_project(
            "t1", ProjectCreate(name="Old", slug="old")
        )
        await storage.archive_project("t1", p2.project_id)

        active = await storage.list_projects("t1")
        # default + active = 2 (Old is archived)
        assert len(active) == 2

        all_projects = await storage.list_projects("t1", include_archived=True)
        assert len(all_projects) == 3

    async def test_update_project(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        p = await storage.create_project(
            "t1", ProjectCreate(name="Sales", slug="sales")
        )
        updated = await storage.update_project(
            "t1", p.project_id, ProjectUpdate(name="Sales v2")
        )
        assert updated is not None
        assert updated.name == "Sales v2"

    async def test_update_nonexistent_project(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        result = await storage.update_project(
            "t1", "fake-id", ProjectUpdate(name="Nope")
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
#  INGESTION TESTS (B1.3.4)
# ═══════════════════════════════════════════════════════════════════════════


def _expand_fixture_events(batch: dict, tenant_id: str) -> list[Event]:
    """Expand fixture batch into canonical Event objects (simplified)."""
    envelope = batch["envelope"]
    now = datetime.now(timezone.utc).isoformat()
    events = []
    for raw in batch["events"]:
        events.append(Event(
            event_id=raw["event_id"],
            tenant_id=tenant_id,
            agent_id=raw.get("agent_id") or envelope["agent_id"],
            agent_type=raw.get("agent_type") or envelope.get("agent_type"),
            project_id=raw.get("project_id"),
            timestamp=raw["timestamp"],
            received_at=now,
            environment=envelope.get("environment", "production"),
            group=envelope.get("group", "default"),
            task_id=raw.get("task_id"),
            task_type=raw.get("task_type"),
            task_run_id=raw.get("task_run_id"),
            correlation_id=raw.get("correlation_id"),
            action_id=raw.get("action_id"),
            parent_action_id=raw.get("parent_action_id"),
            event_type=raw["event_type"],
            severity=raw.get("severity", "info"),
            status=raw.get("status"),
            duration_ms=raw.get("duration_ms"),
            parent_event_id=raw.get("parent_event_id"),
            payload=raw.get("payload"),
        ))
    return events


class TestIngestion:
    async def test_insert_sample_batch(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        count = await storage.insert_events(events)
        assert count == 22  # All 22 events from fixture

    async def test_deduplication(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        first = await storage.insert_events(events)
        second = await storage.insert_events(events)
        assert first == 22
        assert second == 0  # All duplicates

    async def test_agent_upsert_on_ingestion(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)

        # Simulate what the ingestion endpoint does: upsert agent
        last_evt = events[-1]
        hb_events = [e for e in events if e.event_type == "heartbeat"]
        last_hb = max(hb_events, key=lambda e: e.timestamp) if hb_events else None

        await storage.upsert_agent(
            "t1", "lead-qualifier",
            agent_type="sales",
            agent_version="1.2.0",
            framework="custom",
            runtime="python-3.12.1",
            last_seen=datetime.now(timezone.utc),
            last_heartbeat=datetime.fromisoformat(
                last_hb.timestamp.replace("Z", "+00:00")
            ) if last_hb else None,
            last_event_type=last_evt.event_type,
        )

        agent = await storage.get_agent("t1", "lead-qualifier")
        assert agent is not None
        assert agent.agent_type == "sales"

    async def test_project_agent_junction(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        p = await storage.create_project(
            "t1", ProjectCreate(name="Sales", slug="sales")
        )
        await storage.upsert_agent(
            "t1", "agent-1",
            last_seen=datetime.now(timezone.utc),
        )
        await storage.upsert_project_agent("t1", p.project_id, "agent-1")
        # Idempotent
        await storage.upsert_project_agent("t1", p.project_id, "agent-1")

        agents = await storage.list_agents("t1", project_id=p.project_id)
        assert len(agents) == 1
        assert agents[0].agent_id == "agent-1"


# ═══════════════════════════════════════════════════════════════════════════
#  QUERY TESTS (B1.3.5)
# ═══════════════════════════════════════════════════════════════════════════


class TestQueries:
    async def _seed(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)
        return events

    async def test_get_events_basic(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 22

    async def test_get_events_excludes_heartbeats(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_events("t1", exclude_heartbeats=True)
        heartbeats = [e for e in page.data if e.event_type == "heartbeat"]
        assert len(heartbeats) == 0

    async def test_get_events_filter_by_agent(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_events(
            "t1", agent_id="lead-qualifier", exclude_heartbeats=False
        )
        assert all(e.agent_id == "lead-qualifier" for e in page.data)

    async def test_get_events_filter_by_event_type(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_events(
            "t1", event_type="task_started", exclude_heartbeats=False
        )
        assert all(e.event_type == "task_started" for e in page.data)

    async def test_get_events_filter_comma_separated_types(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_events(
            "t1", event_type="task_started,task_completed", exclude_heartbeats=False
        )
        assert all(
            e.event_type in ("task_started", "task_completed")
            for e in page.data
        )

    async def test_get_events_reverse_chronological(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_events("t1", exclude_heartbeats=False)
        timestamps = [e.timestamp for e in page.data]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_get_events_pagination(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page1 = await storage.get_events(
            "t1", limit=5, exclude_heartbeats=False
        )
        assert len(page1.data) == 5
        assert page1.pagination.has_more is True

        page2 = await storage.get_events(
            "t1", limit=5, cursor=page1.pagination.cursor,
            exclude_heartbeats=False,
        )
        assert len(page2.data) == 5
        # No overlap
        ids1 = {e.event_id for e in page1.data}
        ids2 = {e.event_id for e in page2.data}
        assert ids1.isdisjoint(ids2)

    async def test_get_task_events(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        events = await storage.get_task_events("t1", "task_lead-4821")
        assert len(events) > 0
        # Chronological
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)
        # All for same task
        assert all(e.task_id == "task_lead-4821" for e in events)

    async def test_list_tasks(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.list_tasks("t1")
        assert len(page.data) > 0
        # task_lead-4821 should be completed
        t4821 = next(
            (t for t in page.data if t.task_id == "task_lead-4821"), None
        )
        assert t4821 is not None
        assert t4821.derived_status == TaskStatus.COMPLETED

        # task_lead-4822 should be failed
        t4822 = next(
            (t for t in page.data if t.task_id == "task_lead-4822"), None
        )
        assert t4822 is not None
        assert t4822.derived_status == TaskStatus.FAILED

    async def test_list_tasks_filter_by_status(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.list_tasks("t1", status="completed")
        assert all(t.derived_status == "completed" for t in page.data)

    async def test_get_events_filter_by_time_range(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        since = datetime(2026, 2, 10, 14, 2, 0, tzinfo=timezone.utc)
        page = await storage.get_events(
            "t1", since=since, exclude_heartbeats=False
        )
        for e in page.data:
            ts = datetime.fromisoformat(e.timestamp.replace("Z", "+00:00"))
            assert ts >= since


# ═══════════════════════════════════════════════════════════════════════════
#  METRICS & COST TESTS (B1.3.6)
# ═══════════════════════════════════════════════════════════════════════════


class TestMetricsAndCost:
    async def _seed(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)

    async def test_get_cost_summary(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        cost = await storage.get_cost_summary("t1", range="30d")
        # 2 llm_call events in fixture: 0.0078 + 0.0045 = 0.0123
        assert cost.call_count == 2
        assert abs(cost.total_cost - 0.0123) < 0.001

    async def test_get_cost_calls(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_cost_calls("t1")
        # Should have 2 llm_call records within default 50 limit
        assert len(page.data) == 2
        # Most recent first
        assert page.data[0].timestamp >= page.data[1].timestamp

    async def test_get_cost_calls_filter_by_model(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        page = await storage.get_cost_calls("t1", model="gpt-4o")
        assert len(page.data) == 1
        assert page.data[0].model == "gpt-4o"

    async def test_cost_by_agent(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        cost = await storage.get_cost_summary("t1", range="30d")
        assert len(cost.by_agent) == 1
        assert cost.by_agent[0]["agent_id"] == "lead-qualifier"

    async def test_cost_by_model(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        cost = await storage.get_cost_summary("t1", range="30d")
        models = {m["model"] for m in cost.by_model}
        assert "claude-sonnet-4-20250514" in models
        assert "gpt-4o" in models


# ═══════════════════════════════════════════════════════════════════════════
#  PIPELINE TESTS (B1.3.7)
# ═══════════════════════════════════════════════════════════════════════════


class TestPipeline:
    async def _seed(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)

    async def test_pipeline_has_all_sections(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)
        pipeline = await storage.get_pipeline("t1", "lead-qualifier")
        assert pipeline.agent_id == "lead-qualifier"
        assert pipeline.queue is not None
        assert pipeline.queue["depth"] == 3
        assert len(pipeline.todos) > 0
        assert len(pipeline.scheduled) > 0
        assert len(pipeline.issues) > 0

    async def test_pipeline_todo_lifecycle(self, storage: JsonStorageBackend, sample_batch: dict):
        await self._seed(storage, sample_batch)

        # Add a completion event for the todo
        complete_evt = Event(
            event_id="evt-todo-complete",
            tenant_id="t1",
            agent_id="lead-qualifier",
            timestamp="2026-02-10T14:10:00.000Z",
            received_at="2026-02-10T14:10:00.000Z",
            event_type="custom",
            payload={
                "kind": "todo",
                "data": {
                    "todo_id": "todo-001",
                    "action": "completed",
                },
            },
        )
        await storage.insert_events([complete_evt])

        pipeline = await storage.get_pipeline("t1", "lead-qualifier")
        # todo-001 should no longer be in active list
        active_ids = [t["todo_id"] for t in pipeline.todos]
        assert "todo-001" not in active_ids

    async def test_pipeline_empty_agent(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        pipeline = await storage.get_pipeline("t1", "nonexistent")
        assert pipeline.queue is None
        assert pipeline.todos == []
        assert pipeline.scheduled == []
        assert pipeline.issues == []


# ═══════════════════════════════════════════════════════════════════════════
#  ALERT TESTS (B1.3.8)
# ═══════════════════════════════════════════════════════════════════════════


class TestAlerts:
    async def test_alert_rule_crud(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        rule = await storage.create_alert_rule(
            "t1",
            AlertRuleCreate(
                name="Agent stuck",
                condition_type="agent_stuck",
                condition_config={"stuck_threshold_seconds": 300},
            ),
        )
        assert rule.name == "Agent stuck"
        assert rule.is_enabled is True

        # List
        rules = await storage.list_alert_rules("t1")
        assert len(rules) == 1

        # Update
        from shared.models import AlertRuleUpdate
        updated = await storage.update_alert_rule(
            "t1", rule.rule_id,
            AlertRuleUpdate(name="Agent stuck v2", cooldown_seconds=600),
        )
        assert updated is not None
        assert updated.name == "Agent stuck v2"
        assert updated.cooldown_seconds == 600

        # Delete
        deleted = await storage.delete_alert_rule("t1", rule.rule_id)
        assert deleted is True
        rules = await storage.list_alert_rules("t1")
        assert len(rules) == 0

    async def test_alert_history(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        now = datetime.now(timezone.utc)
        alert = AlertHistoryRecord(
            alert_id="a1",
            tenant_id="t1",
            rule_id="r1",
            fired_at=now,
            condition_snapshot={"agent_id": "agent-1", "threshold": 300},
            related_agent_id="agent-1",
        )
        await storage.insert_alert("t1", alert)

        page = await storage.list_alert_history("t1")
        assert len(page.data) == 1
        assert page.data[0].alert_id == "a1"

    async def test_get_last_alert_for_rule(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        now = datetime.now(timezone.utc)
        for i in range(3):
            await storage.insert_alert("t1", AlertHistoryRecord(
                alert_id=f"a{i}",
                tenant_id="t1",
                rule_id="r1",
                fired_at=now + timedelta(minutes=i),
            ))
        last = await storage.get_last_alert_for_rule("t1", "r1")
        assert last is not None
        assert last.alert_id == "a2"  # Most recent

    async def test_list_alert_rules_filter_enabled(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        await storage.create_alert_rule(
            "t1", AlertRuleCreate(name="Rule 1", condition_type="agent_stuck"),
        )
        r2 = await storage.create_alert_rule(
            "t1", AlertRuleCreate(name="Rule 2", condition_type="error_rate"),
        )
        from shared.models import AlertRuleUpdate
        await storage.update_alert_rule(
            "t1", r2.rule_id, AlertRuleUpdate(is_enabled=False),
        )
        enabled = await storage.list_alert_rules("t1", is_enabled=True)
        assert len(enabled) == 1
        assert enabled[0].name == "Rule 1"


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT STATUS DERIVATION TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestAgentStatusDerivation:
    def _make_agent(self, **kwargs) -> "AgentRecord":
        from shared.models import AgentRecord
        now = datetime.now(timezone.utc)
        defaults = dict(
            agent_id="a1", tenant_id="t1", first_seen=now, last_seen=now,
            last_heartbeat=now, stuck_threshold_seconds=300,
        )
        defaults.update(kwargs)
        return AgentRecord(**defaults)

    def test_stuck_no_heartbeat_no_recent_activity(self):
        """Agent with no heartbeat AND old last_seen is stuck."""
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        agent = self._make_agent(last_heartbeat=None, last_seen=old)
        assert derive_agent_status(agent) == AgentStatus.STUCK

    def test_no_heartbeat_but_recently_seen_not_stuck(self):
        """Agent with no heartbeat but recently seen should NOT be stuck (Issue #10)."""
        agent = self._make_agent(last_heartbeat=None)
        assert derive_agent_status(agent) != AgentStatus.STUCK

    def test_stuck_old_heartbeat(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        agent = self._make_agent(last_heartbeat=old)
        assert derive_agent_status(agent) == AgentStatus.STUCK

    def test_error_task_failed(self):
        agent = self._make_agent(last_event_type="task_failed")
        assert derive_agent_status(agent) == AgentStatus.ERROR

    def test_error_action_failed(self):
        agent = self._make_agent(last_event_type="action_failed")
        assert derive_agent_status(agent) == AgentStatus.ERROR

    def test_waiting_approval(self):
        agent = self._make_agent(last_event_type="approval_requested")
        assert derive_agent_status(agent) == AgentStatus.WAITING_APPROVAL

    def test_processing(self):
        agent = self._make_agent(last_event_type="task_started")
        assert derive_agent_status(agent) == AgentStatus.PROCESSING

    def test_idle_default(self):
        agent = self._make_agent(last_event_type="task_completed")
        assert derive_agent_status(agent) == AgentStatus.IDLE

    def test_stuck_takes_priority_over_error(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=600)
        agent = self._make_agent(
            last_heartbeat=old, last_event_type="task_failed"
        )
        assert derive_agent_status(agent) == AgentStatus.STUCK


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE 2 FEATURE TESTS
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeAgentStats1h:
    async def _seed(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)

    async def test_stats_1h_returns_defaults_for_unknown_agent(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        stats = await storage.compute_agent_stats_1h("t1", "nonexistent")
        assert stats.tasks_completed == 0
        assert stats.tasks_failed == 0
        assert stats.success_rate is None
        assert stats.total_cost is None

    async def test_stats_1h_with_recent_events(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        now = datetime.now(timezone.utc)
        events = [
            Event(
                event_id="tc1", tenant_id="t1", agent_id="a1",
                timestamp=now.isoformat(), received_at=now.isoformat(),
                event_type="task_completed", duration_ms=1000,
            ),
            Event(
                event_id="tf1", tenant_id="t1", agent_id="a1",
                timestamp=now.isoformat(), received_at=now.isoformat(),
                event_type="task_failed",
            ),
            Event(
                event_id="llm1", tenant_id="t1", agent_id="a1",
                timestamp=now.isoformat(), received_at=now.isoformat(),
                event_type="custom",
                payload={"kind": "llm_call", "data": {"name": "test", "model": "gpt-4", "cost": 0.05}},
            ),
        ]
        await storage.insert_events(events)
        stats = await storage.compute_agent_stats_1h("t1", "a1")
        assert stats.tasks_completed == 1
        assert stats.tasks_failed == 1
        assert stats.success_rate == 50.0
        assert stats.avg_duration_ms == 1000
        assert abs(stats.total_cost - 0.05) < 0.001


class TestPayloadKindFilter:
    async def test_filter_events_by_payload_kind(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)
        page = await storage.get_events("t1", payload_kind="llm_call", exclude_heartbeats=False)
        assert len(page.data) > 0
        for e in page.data:
            assert e.payload is not None
            assert e.payload.get("kind") == "llm_call"

    async def test_filter_events_payload_kind_no_match(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        page = await storage.get_events("t1", payload_kind="nonexistent", exclude_heartbeats=False)
        assert len(page.data) == 0


class TestTasksSinceUntil:
    async def test_tasks_since_filter(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            Event(
                event_id="e1", tenant_id="t1", agent_id="a1", task_id="old-task",
                timestamp="2026-01-01T00:00:00Z", received_at="2026-01-01T00:00:00Z",
                event_type="task_started",
            ),
            Event(
                event_id="e2", tenant_id="t1", agent_id="a1", task_id="new-task",
                timestamp="2026-02-10T12:00:00Z", received_at="2026-02-10T12:00:00Z",
                event_type="task_started",
            ),
        ]
        await storage.insert_events(events)
        since = datetime(2026, 2, 1, tzinfo=timezone.utc)
        page = await storage.list_tasks("t1", since=since)
        task_ids = [t.task_id for t in page.data]
        assert "new-task" in task_ids
        assert "old-task" not in task_ids

    async def test_tasks_until_filter(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            Event(
                event_id="e1", tenant_id="t1", agent_id="a1", task_id="old-task",
                timestamp="2026-01-01T00:00:00Z", received_at="2026-01-01T00:00:00Z",
                event_type="task_started",
            ),
            Event(
                event_id="e2", tenant_id="t1", agent_id="a1", task_id="new-task",
                timestamp="2026-02-10T12:00:00Z", received_at="2026-02-10T12:00:00Z",
                event_type="task_started",
            ),
        ]
        await storage.insert_events(events)
        until = datetime(2026, 2, 1, tzinfo=timezone.utc)
        page = await storage.list_tasks("t1", until=until)
        task_ids = [t.task_id for t in page.data]
        assert "old-task" in task_ids
        assert "new-task" not in task_ids


class TestTokenCounts:
    async def test_task_token_counts(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            Event(
                event_id="ts1", tenant_id="t1", agent_id="a1", task_id="t-tokens",
                timestamp="2026-02-10T12:00:00Z", received_at="2026-02-10T12:00:00Z",
                event_type="task_started",
            ),
            Event(
                event_id="llm1", tenant_id="t1", agent_id="a1", task_id="t-tokens",
                timestamp="2026-02-10T12:01:00Z", received_at="2026-02-10T12:01:00Z",
                event_type="custom",
                payload={"kind": "llm_call", "data": {"name": "c1", "model": "gpt-4", "cost": 0.01, "tokens_in": 100, "tokens_out": 50}},
            ),
            Event(
                event_id="llm2", tenant_id="t1", agent_id="a1", task_id="t-tokens",
                timestamp="2026-02-10T12:02:00Z", received_at="2026-02-10T12:02:00Z",
                event_type="custom",
                payload={"kind": "llm_call", "data": {"name": "c2", "model": "gpt-4", "cost": 0.02, "tokens_in": 200, "tokens_out": 100}},
            ),
        ]
        await storage.insert_events(events)
        page = await storage.list_tasks("t1")
        task = next(t for t in page.data if t.task_id == "t-tokens")
        assert task.llm_call_count == 2
        assert task.total_tokens_in == 300
        assert task.total_tokens_out == 150


class TestCostTokenTotals:
    async def test_cost_summary_has_token_totals(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)
        cost = await storage.get_cost_summary("t1", range="30d")
        # Token counts from fixture llm_call events
        assert cost.total_tokens_in >= 0
        assert cost.total_tokens_out >= 0
        # by_agent should also have token fields
        if cost.by_agent:
            assert "tokens_in" in cost.by_agent[0]
            assert "tokens_out" in cost.by_agent[0]


class TestCostTimeBuckets:
    async def test_cost_timeseries_uses_cost_time_bucket(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        now = datetime.now(timezone.utc)
        events = [
            Event(
                event_id="llm1", tenant_id="t1", agent_id="a1",
                timestamp=now.isoformat(), received_at=now.isoformat(),
                event_type="custom",
                payload={"kind": "llm_call", "data": {"name": "c1", "model": "gpt-4", "cost": 0.05, "tokens_in": 100, "tokens_out": 50}},
            ),
        ]
        await storage.insert_events(events)
        buckets = await storage.get_cost_timeseries("t1", range="1h")
        assert len(buckets) > 0
        # Verify CostTimeBucket fields
        from shared.models import CostTimeBucket
        assert isinstance(buckets[0], CostTimeBucket)
        # At least one bucket should have data
        total = sum(b.call_count for b in buckets)
        assert total == 1


class TestMetricsGroupBy:
    async def test_metrics_group_by_agent(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)
        resp = await storage.get_metrics("t1", range="30d", group_by="agent")
        assert resp.groups is not None
        assert len(resp.groups) > 0
        assert "agent" in resp.groups[0]

    async def test_metrics_without_group_by(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)
        resp = await storage.get_metrics("t1", range="30d")
        assert resp.groups is None


class TestPreviousStatusTracking:
    async def test_upsert_tracks_previous_status(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        now = datetime.now(timezone.utc)
        # First upsert — no previous status
        rec1 = await storage.upsert_agent(
            "t1", "a1", last_seen=now, last_heartbeat=now,
            last_event_type="task_started",
        )
        assert rec1.previous_status is None  # New agent

        # Second upsert — should have previous status
        rec2 = await storage.upsert_agent(
            "t1", "a1", last_seen=now, last_heartbeat=now,
            last_event_type="task_completed",
        )
        assert rec2.previous_status == "processing"


class TestPipelineSnapshotAt:
    async def test_queue_has_snapshot_at(self, storage: JsonStorageBackend, sample_batch: dict):
        await storage.create_tenant("t1", "Acme", "acme")
        events = _expand_fixture_events(sample_batch, "t1")
        await storage.insert_events(events)
        pipeline = await storage.get_pipeline("t1", "lead-qualifier")
        assert pipeline.queue is not None
        assert "snapshot_at" in pipeline.queue

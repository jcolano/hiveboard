"""Event retention & pruning tests — covers spec Sections 3-7.

Tests TTL pruning (plan-based retention), cold event pruning (heartbeat
and action_started), unified single-pass prune, and edge cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.storage_json import JsonStorageBackend
from shared.enums import COLD_EVENT_RETENTION, EventType, PLAN_LIMITS
from shared.models import Event


# ═══════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _make_event(
    event_id: str,
    tenant_id: str = "t1",
    agent_id: str = "a1",
    event_type: str = "task_completed",
    timestamp: datetime | None = None,
    **kwargs,
) -> Event:
    """Create a minimal Event for testing."""
    ts = timestamp or datetime.now(timezone.utc)
    return Event(
        event_id=event_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        event_type=event_type,
        timestamp=ts.isoformat(),
        received_at=ts.isoformat(),
        **kwargs,
    )


def _ts_ago(**kwargs) -> datetime:
    """Return a UTC datetime offset from now."""
    return datetime.now(timezone.utc) - timedelta(**kwargs)


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE 1: TTL PRUNING
# ═══════════════════════════════════════════════════════════════════════════


class TestTTLPruning:
    """Spec Section 3 — plan-based retention_days enforcement."""

    async def test_free_plan_prunes_beyond_7_days(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        events = [
            _make_event("old", timestamp=_ts_ago(days=8)),
            _make_event("recent", timestamp=_ts_ago(hours=1)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        assert result["total_pruned"] == 1
        # Only recent event remains
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 1
        assert page.data[0].event_id == "recent"

    async def test_pro_plan_prunes_beyond_30_days(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme", plan="pro")
        events = [
            _make_event("old", timestamp=_ts_ago(days=31)),
            _make_event("within", timestamp=_ts_ago(days=15)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 1
        assert page.data[0].event_id == "within"

    async def test_enterprise_plan_prunes_beyond_90_days(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme", plan="enterprise")
        events = [
            _make_event("old", timestamp=_ts_ago(days=91)),
            _make_event("within", timestamp=_ts_ago(days=45)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 1
        assert page.data[0].event_id == "within"

    async def test_events_within_retention_kept(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        events = [
            _make_event("e1", timestamp=_ts_ago(days=1)),
            _make_event("e2", timestamp=_ts_ago(days=3)),
            _make_event("e3", timestamp=_ts_ago(days=6)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 0
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 3

    async def test_unknown_tenant_events_kept(self, storage: JsonStorageBackend):
        """Events with an unrecognized tenant_id are never pruned."""
        await storage.create_tenant("t1", "Acme", "acme")
        # Insert event for t1 (known) and manually inject one for "unknown"
        events = [_make_event("known", tenant_id="t1", timestamp=_ts_ago(hours=1))]
        await storage.insert_events(events)

        # Manually inject event with unknown tenant
        storage._tables["events"].append({
            "event_id": "orphan",
            "tenant_id": "unknown_tenant",
            "agent_id": "a1",
            "event_type": "task_completed",
            "timestamp": _ts_ago(days=365).isoformat(),
            "received_at": _ts_ago(days=365).isoformat(),
        })

        result = await storage.prune_events()
        # The orphan event is 365 days old but should be kept
        orphan = [
            e for e in storage._tables["events"]
            if e["event_id"] == "orphan"
        ]
        assert len(orphan) == 1

    @pytest.mark.xfail(
        reason="BUG: _parse_dt() in storage_json.py raises ValueError on invalid "
               "timestamps instead of returning None. The defensive check in "
               "_is_event_within_retention (line 1787: 'if ts is None: return True') "
               "is unreachable for malformed strings. The _parse_dt in app.py has "
               "a try/except but the one in storage_json.py does not.",
        strict=True,
    )
    async def test_unparseable_timestamp_kept(self, storage: JsonStorageBackend):
        """Events with unparseable timestamps should be kept defensively.

        Currently FAILS — _parse_dt raises instead of returning None.
        """
        await storage.create_tenant("t1", "Acme", "acme")
        storage._tables["events"].append({
            "event_id": "bad_ts",
            "tenant_id": "t1",
            "agent_id": "a1",
            "event_type": "task_completed",
            "timestamp": "not-a-date",
            "received_at": "not-a-date",
        })

        result = await storage.prune_events()
        bad = [
            e for e in storage._tables["events"]
            if e["event_id"] == "bad_ts"
        ]
        assert len(bad) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  PHASE 2: COLD EVENT PRUNING
# ═══════════════════════════════════════════════════════════════════════════


class TestColdEventPruning:
    """Spec Section 4 — shorter retention for heartbeats and action_started."""

    async def test_heartbeat_older_than_10min_pruned(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("hb_old", event_type="heartbeat", timestamp=_ts_ago(minutes=15)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 1
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 0

    async def test_heartbeat_within_10min_kept(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("hb_new", event_type="heartbeat", timestamp=_ts_ago(minutes=5)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 1

    async def test_action_started_older_than_24h_pruned(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("as_old", event_type="action_started", timestamp=_ts_ago(hours=25)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 1

    async def test_action_started_within_24h_kept(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("as_new", event_type="action_started", timestamp=_ts_ago(hours=12)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0

    async def test_action_completed_not_affected(self, storage: JsonStorageBackend):
        """action_completed is NOT a cold event type — always retained within TTL."""
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("ac", event_type="action_completed", timestamp=_ts_ago(hours=25)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        page = await storage.get_events("t1", exclude_heartbeats=False)
        assert len(page.data) == 1

    async def test_task_completed_not_affected(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("tc", event_type="task_completed", timestamp=_ts_ago(days=3)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0

    async def test_task_failed_not_affected(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("tf", event_type="task_failed", timestamp=_ts_ago(days=3)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0

    async def test_custom_pipeline_events_not_affected(self, storage: JsonStorageBackend):
        """Custom events (queue_snapshot, todo, issue) are never cold-pruned."""
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event(
                "custom1", event_type="custom", timestamp=_ts_ago(days=3),
                payload={"kind": "queue_snapshot", "data": {"depth": 5}},
            ),
            _make_event(
                "custom2", event_type="custom", timestamp=_ts_ago(days=3),
                payload={"kind": "todo", "data": {"todo_id": "t1", "action": "added"}},
            ),
            _make_event(
                "custom3", event_type="custom", timestamp=_ts_ago(days=3),
                payload={"kind": "issue", "data": {"severity": "high"}},
            ),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        assert result["total_pruned"] == 0


# ═══════════════════════════════════════════════════════════════════════════
#  UNIFIED PRUNE (SINGLE PASS)
# ═══════════════════════════════════════════════════════════════════════════


class TestUnifiedPrune:
    """Spec Section 5 — single-pass, single-lock, combined results."""

    async def test_single_pass_both_phases(self, storage: JsonStorageBackend):
        """TTL and cold pruning happen in one pass with correct counts."""
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        events = [
            # TTL expired (8 days old, free plan = 7 days)
            _make_event("ttl_expired", event_type="task_completed", timestamp=_ts_ago(days=8)),
            # Cold expired (heartbeat, 20 minutes old)
            _make_event("cold_expired", event_type="heartbeat", timestamp=_ts_ago(minutes=20)),
            # Both would be TTL and cold — should count as TTL (phase 1 first)
            _make_event("both_expired", event_type="heartbeat", timestamp=_ts_ago(days=10)),
            # Kept
            _make_event("kept", event_type="task_completed", timestamp=_ts_ago(hours=1)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        # "both_expired" is TTL-expired, counted as ttl (not cold)
        assert result["ttl_pruned"] == 2  # ttl_expired + both_expired
        assert result["cold_pruned"] == 1  # cold_expired
        assert result["total_pruned"] == 3
        assert len(storage._tables["events"]) == 1

    async def test_no_double_counting(self, storage: JsonStorageBackend):
        """An event outside TTL is counted as ttl_pruned, not cold_pruned."""
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        events = [
            _make_event("hb_outside_ttl", event_type="heartbeat", timestamp=_ts_ago(days=10)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        assert result["cold_pruned"] == 0

    async def test_persist_called_only_when_pruned(self, storage: JsonStorageBackend):
        """_persist() is NOT called when nothing is pruned."""
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("e1", timestamp=_ts_ago(hours=1)),
        ]
        await storage.insert_events(events)

        # Track persist calls by checking file mtime
        import os
        fp = storage._data_dir / "events.json"
        mtime_before = os.path.getmtime(fp)

        # Small sleep to ensure mtime would differ if written
        import asyncio
        await asyncio.sleep(0.05)

        result = await storage.prune_events()
        assert result["total_pruned"] == 0

        mtime_after = os.path.getmtime(fp)
        assert mtime_before == mtime_after

    async def test_persist_called_when_pruned(self, storage: JsonStorageBackend):
        """_persist() IS called when events are pruned."""
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event("hb_old", event_type="heartbeat", timestamp=_ts_ago(minutes=15)),
        ]
        await storage.insert_events(events)

        import os
        fp = storage._data_dir / "events.json"
        mtime_before = os.path.getmtime(fp)

        import asyncio
        await asyncio.sleep(0.05)

        result = await storage.prune_events()
        assert result["total_pruned"] == 1

        mtime_after = os.path.getmtime(fp)
        assert mtime_after > mtime_before

    async def test_empty_events_table(self, storage: JsonStorageBackend):
        """Prune on empty events table does nothing."""
        await storage.create_tenant("t1", "Acme", "acme")
        result = await storage.prune_events()
        assert result == {"ttl_pruned": 0, "cold_pruned": 0, "total_pruned": 0}

    async def test_all_events_pruned(self, storage: JsonStorageBackend):
        """When all events are pruned, table becomes empty."""
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        events = [
            _make_event("e1", timestamp=_ts_ago(days=10)),
            _make_event("e2", timestamp=_ts_ago(days=10)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["total_pruned"] == 2
        assert len(storage._tables["events"]) == 0

    async def test_return_dict_shape(self, storage: JsonStorageBackend):
        """Return value always has the three required keys."""
        await storage.create_tenant("t1", "Acme", "acme")
        result = await storage.prune_events()
        assert "ttl_pruned" in result
        assert "cold_pruned" in result
        assert "total_pruned" in result
        assert result["total_pruned"] == result["ttl_pruned"] + result["cold_pruned"]


# ═══════════════════════════════════════════════════════════════════════════
#  MULTI-TENANT PRUNING
# ═══════════════════════════════════════════════════════════════════════════


class TestMultiTenantPruning:
    """Each tenant's events pruned according to their own plan."""

    async def test_different_plans_different_retention(self, storage: JsonStorageBackend):
        await storage.create_tenant("free_tenant", "Free Co", "free", plan="free")
        await storage.create_tenant("pro_tenant", "Pro Co", "pro", plan="pro")

        events = [
            # 10 days old — expired for free (7d), within for pro (30d)
            _make_event("free_e", tenant_id="free_tenant", timestamp=_ts_ago(days=10)),
            _make_event("pro_e", tenant_id="pro_tenant", timestamp=_ts_ago(days=10)),
        ]
        await storage.insert_events(events)

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1  # Only the free tenant event

        remaining_ids = [e["event_id"] for e in storage._tables["events"]]
        assert "pro_e" in remaining_ids
        assert "free_e" not in remaining_ids

    async def test_no_tenants_in_table(self, storage: JsonStorageBackend):
        """With no tenants, all events kept by TTL (unknown tenant → keep).
        Cold pruning still applies."""
        # Don't create any tenants
        storage._tables["events"].append({
            "event_id": "orphan",
            "tenant_id": "nobody",
            "agent_id": "a1",
            "event_type": "task_completed",
            "timestamp": _ts_ago(days=365).isoformat(),
            "received_at": _ts_ago(days=365).isoformat(),
        })

        result = await storage.prune_events()
        # No tenant → unknown → kept
        assert result["ttl_pruned"] == 0
        assert len(storage._tables["events"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  INTEGRATION — QUERY CORRECTNESS AFTER PRUNING
# ═══════════════════════════════════════════════════════════════════════════


class TestPostPruneQueries:
    """After pruning, queries should return only retained events."""

    async def test_get_events_after_prune(self, storage: JsonStorageBackend):
        await storage.create_tenant("t1", "Acme", "acme")
        now = datetime.now(timezone.utc)
        events = [
            _make_event("hb1", event_type="heartbeat", timestamp=_ts_ago(minutes=20)),
            _make_event("hb2", event_type="heartbeat", timestamp=_ts_ago(minutes=5)),
            _make_event("tc1", event_type="task_completed", timestamp=_ts_ago(hours=1)),
        ]
        await storage.insert_events(events)

        await storage.prune_events()
        page = await storage.get_events("t1", exclude_heartbeats=False)
        ids = {e.event_id for e in page.data}
        assert "hb1" not in ids  # cold-pruned
        assert "hb2" in ids      # recent heartbeat kept
        assert "tc1" in ids      # task_completed always kept

    async def test_list_tasks_excludes_fully_pruned_tasks(self, storage: JsonStorageBackend):
        """Tasks whose ALL events are pruned disappear from list_tasks."""
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        events = [
            # Old task — all events beyond TTL
            _make_event("old_ts", event_type="task_started", task_id="old_task",
                        timestamp=_ts_ago(days=10)),
            _make_event("old_tc", event_type="task_completed", task_id="old_task",
                        timestamp=_ts_ago(days=10), duration_ms=5000),
            # Recent task
            _make_event("new_ts", event_type="task_started", task_id="new_task",
                        timestamp=_ts_ago(hours=1)),
        ]
        await storage.insert_events(events)

        await storage.prune_events()
        page = await storage.list_tasks("t1")
        task_ids = [t.task_id for t in page.data]
        assert "new_task" in task_ids
        assert "old_task" not in task_ids

    async def test_get_metrics_only_aggregates_retained(self, storage: JsonStorageBackend):
        """Metrics should only reflect events still in memory."""
        await storage.create_tenant("t1", "Acme", "acme", plan="free")
        now = datetime.now(timezone.utc)
        events = [
            _make_event("tc1", event_type="task_completed", task_id="t1",
                        timestamp=now, duration_ms=1000),
            _make_event("tc_old", event_type="task_completed", task_id="t_old",
                        timestamp=_ts_ago(days=10), duration_ms=2000),
        ]
        await storage.insert_events(events)

        await storage.prune_events()
        metrics = await storage.get_metrics("t1", range="30d")
        # Only the recent task should be counted
        assert metrics.summary.total_tasks == 1

    async def test_pipeline_unaffected_by_cold_pruning(self, storage: JsonStorageBackend):
        """Custom pipeline events (queue_snapshot, todo, issue) survive cold pruning."""
        await storage.create_tenant("t1", "Acme", "acme")
        events = [
            _make_event(
                "qs1", event_type="custom", agent_id="a1",
                timestamp=_ts_ago(days=2),
                payload={"kind": "queue_snapshot", "data": {"depth": 3, "items": []}},
            ),
        ]
        await storage.insert_events(events)

        await storage.prune_events()
        pipeline = await storage.get_pipeline("t1", "a1")
        assert pipeline.queue is not None
        assert pipeline.queue["depth"] == 3


# ═══════════════════════════════════════════════════════════════════════════
#  COLD_EVENT_RETENTION CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════


class TestColdRetentionConstants:
    """Verify the COLD_EVENT_RETENTION configuration matches the spec."""

    def test_heartbeat_retention_600_seconds(self):
        assert COLD_EVENT_RETENTION[EventType.HEARTBEAT] == 600

    def test_action_started_retention_86400_seconds(self):
        assert COLD_EVENT_RETENTION[EventType.ACTION_STARTED] == 86400

    def test_only_two_cold_event_types(self):
        assert len(COLD_EVENT_RETENTION) == 2

    def test_plan_limits_retention_days(self):
        assert PLAN_LIMITS["free"]["retention_days"] == 7
        assert PLAN_LIMITS["pro"]["retention_days"] == 30
        assert PLAN_LIMITS["enterprise"]["retention_days"] == 90

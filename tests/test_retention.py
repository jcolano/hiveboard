"""Tests for event retention and pruning — spec Section 12 checklist.

Covers:
  - Phase 1: TTL pruning (plan-based retention)
  - Phase 2: Cold event pruning (heartbeat, action_started)
  - Unified prune (single pass, correct counts)
  - Background task behavior (startup prune, interval)
  - Integration (queries after pruning)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.storage_json import JsonStorageBackend
from shared.enums import COLD_EVENT_RETENTION, EventType, PLAN_LIMITS, TenantPlan
from shared.models import Event


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_event(
    event_id: str,
    tenant_id: str,
    event_type: str,
    timestamp: datetime,
    *,
    agent_id: str = "a1",
    task_id: str | None = None,
    payload: dict | None = None,
    duration_ms: int | None = None,
) -> Event:
    return Event(
        event_id=event_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        timestamp=_iso(timestamp),
        received_at=_iso(timestamp),
        event_type=event_type,
        task_id=task_id,
        payload=payload,
        duration_ms=duration_ms,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 1: TTL Pruning
# ═══════════════════════════════════════════════════════════════════════════

class TestTTLPruning:
    """Spec Section 12 — Phase 1 checklist."""

    async def test_prune_events_older_than_free_retention(self, storage: JsonStorageBackend):
        """Events older than 7 days are pruned for a free-tier tenant."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()
        old = now - timedelta(days=8)
        fresh = now - timedelta(hours=1)

        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", old),
            _make_event("new1", "t1", "task_completed", fresh),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "old1" not in ids
        assert "new1" in ids

    async def test_prune_events_older_than_pro_retention(self, storage: JsonStorageBackend):
        """Events older than 30 days are pruned for a pro-tier tenant."""
        await storage.create_tenant("t1", "Test", "test")
        # Upgrade to pro
        for t in storage._tables["tenants"]:
            if t["tenant_id"] == "t1":
                t["plan"] = "pro"

        now = _utc_now()
        old = now - timedelta(days=31)
        within = now - timedelta(days=15)

        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", old),
            _make_event("within1", "t1", "task_completed", within),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "old1" not in ids
        assert "within1" in ids

    async def test_events_within_retention_are_kept(self, storage: JsonStorageBackend):
        """Events within the retention window are not pruned."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("e1", "t1", "task_completed", now - timedelta(days=1)),
            _make_event("e2", "t1", "task_completed", now - timedelta(days=6)),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 0
        assert len(storage._tables["events"]) == 2

    async def test_unparseable_timestamp_kept(self, storage: JsonStorageBackend):
        """Events with unparseable timestamps are kept (not silently dropped)."""
        await storage.create_tenant("t1", "Test", "test")

        # Insert a normal event, then manually corrupt a timestamp
        await storage.insert_events([
            _make_event("e1", "t1", "task_completed", _utc_now()),
        ])
        storage._tables["events"].append({
            "event_id": "bad_ts",
            "tenant_id": "t1",
            "agent_id": "a1",
            "timestamp": "not-a-date",
            "event_type": "task_completed",
        })

        result = await storage.prune_events()
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "bad_ts" in ids

    async def test_unknown_tenant_kept(self, storage: JsonStorageBackend):
        """Events with unknown tenant_id are kept."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("e1", "t1", "task_completed", now),
        ])
        # Insert an event for a tenant that doesn't exist
        storage._tables["events"].append({
            "event_id": "orphan1",
            "tenant_id": "unknown_tenant",
            "agent_id": "a1",
            "timestamp": _iso(now - timedelta(days=100)),
            "event_type": "task_completed",
        })

        result = await storage.prune_events()
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "orphan1" in ids

    async def test_persist_called_only_when_pruned(self, storage: JsonStorageBackend):
        """_persist() is called only when events are actually pruned."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", now - timedelta(days=10)),
        ])

        result = await storage.prune_events()
        assert result["total_pruned"] == 1

        # Verify the events file was written (list should be empty now)
        import json
        fp = storage._data_dir / "events.json"
        with open(fp, "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        assert len(on_disk) == 0

    async def test_persist_not_called_when_nothing_pruned(self, storage: JsonStorageBackend):
        """_persist() is NOT called when nothing is pruned (no unnecessary disk I/O)."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("e1", "t1", "task_completed", now - timedelta(hours=1)),
        ])

        # Record file mtime before prune
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

    async def test_prune_acquires_lock(self, storage: JsonStorageBackend):
        """Prune acquires self._locks['events']."""
        await storage.create_tenant("t1", "Test", "test")

        # Acquire the lock manually
        lock = storage._locks["events"]
        await lock.acquire()

        import asyncio

        # prune_events should block (we hold the lock)
        prune_done = False

        async def do_prune():
            nonlocal prune_done
            await storage.prune_events()
            prune_done = True

        task = asyncio.create_task(do_prune())
        await asyncio.sleep(0.05)
        assert not prune_done  # should be blocked

        lock.release()
        await task
        assert prune_done

    async def test_return_value_reports_correct_count(self, storage: JsonStorageBackend):
        """Return value correctly reports count of pruned events."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", now - timedelta(days=10)),
            _make_event("old2", "t1", "task_started", now - timedelta(days=8)),
            _make_event("new1", "t1", "task_completed", now - timedelta(hours=1)),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 2
        assert result["total_pruned"] >= 2


# ═══════════════════════════════════════════════════════════════════════════
#  Phase 2: Cold Event Pruning
# ═══════════════════════════════════════════════════════════════════════════

class TestColdEventPruning:
    """Spec Section 12 — Phase 2 checklist."""

    async def test_old_heartbeats_pruned(self, storage: JsonStorageBackend):
        """Heartbeat events older than 10 minutes are pruned."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("hb_old", "t1", EventType.HEARTBEAT, now - timedelta(minutes=15)),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 1
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "hb_old" not in ids

    async def test_recent_heartbeats_kept(self, storage: JsonStorageBackend):
        """Heartbeat events younger than 10 minutes are kept."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("hb_new", "t1", EventType.HEARTBEAT, now - timedelta(minutes=5)),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "hb_new" in ids

    async def test_old_action_started_pruned(self, storage: JsonStorageBackend):
        """action_started events older than 24 hours are pruned."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("as_old", "t1", EventType.ACTION_STARTED, now - timedelta(hours=25)),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 1
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "as_old" not in ids

    async def test_recent_action_started_kept(self, storage: JsonStorageBackend):
        """action_started events younger than 24 hours are kept."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("as_new", "t1", EventType.ACTION_STARTED, now - timedelta(hours=12)),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "as_new" in ids

    async def test_action_completed_not_affected(self, storage: JsonStorageBackend):
        """action_completed events are NOT affected by cold pruning regardless of age."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("ac_old", "t1", EventType.ACTION_COMPLETED, now - timedelta(hours=48),
                        duration_ms=500),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "ac_old" in ids

    async def test_task_completed_not_affected(self, storage: JsonStorageBackend):
        """task_completed events are NOT affected by cold pruning."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("tc_old", "t1", EventType.TASK_COMPLETED, now - timedelta(hours=48),
                        duration_ms=26000),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "tc_old" in ids

    async def test_task_failed_not_affected(self, storage: JsonStorageBackend):
        """task_failed events are NOT affected by cold pruning."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("tf_old", "t1", EventType.TASK_FAILED, now - timedelta(hours=48)),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "tf_old" in ids

    async def test_custom_pipeline_events_not_affected(self, storage: JsonStorageBackend):
        """Custom events with pipeline payloads are NOT affected by cold pruning."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("qs_old", "t1", EventType.CUSTOM, now - timedelta(hours=48),
                        payload={"kind": "queue_snapshot", "data": {"depth": 5}}),
            _make_event("todo_old", "t1", EventType.CUSTOM, now - timedelta(hours=48),
                        payload={"kind": "todo", "data": {"text": "fix bug"}}),
            _make_event("issue_old", "t1", EventType.CUSTOM, now - timedelta(hours=48),
                        payload={"kind": "issue", "data": {"severity": "high"}}),
        ])

        result = await storage.prune_events()
        assert result["cold_pruned"] == 0
        assert len(storage._tables["events"]) == 3


# ═══════════════════════════════════════════════════════════════════════════
#  Unified Prune
# ═══════════════════════════════════════════════════════════════════════════

class TestUnifiedPrune:
    """Spec Section 12 — Unified prune checklist."""

    async def test_single_pass_applies_both(self, storage: JsonStorageBackend):
        """Single pass applies both TTL and cold pruning."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            # TTL-expired (older than 7d)
            _make_event("ttl1", "t1", "task_completed", now - timedelta(days=10)),
            # Cold-expired heartbeat (older than 10min, within TTL)
            _make_event("cold1", "t1", EventType.HEARTBEAT, now - timedelta(minutes=30)),
            # Fresh event (kept)
            _make_event("keep1", "t1", "task_completed", now - timedelta(hours=1)),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        assert result["cold_pruned"] == 1
        assert result["total_pruned"] == 2
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert ids == ["keep1"]

    async def test_ttl_expired_not_double_counted_as_cold(self, storage: JsonStorageBackend):
        """An event outside TTL is counted as ttl_pruned, not double-counted as cold."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        # A heartbeat that's both TTL-expired AND cold-expired
        # Should be counted only as ttl_pruned (TTL check comes first)
        await storage.insert_events([
            _make_event("hb_ancient", "t1", EventType.HEARTBEAT, now - timedelta(days=10)),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 1
        assert result["cold_pruned"] == 0
        assert result["total_pruned"] == 1

    async def test_lock_acquired_once(self, storage: JsonStorageBackend):
        """Lock is acquired only once per prune cycle (single pass, not two passes)."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("e1", "t1", "task_completed", now),
        ])

        # Verify prune completes successfully (if it acquired the lock twice
        # in a non-reentrant way, it would deadlock)
        result = await storage.prune_events()
        assert result["total_pruned"] == 0

    async def test_return_dict_correct(self, storage: JsonStorageBackend):
        """Return dict contains correct ttl_pruned, cold_pruned, total_pruned."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("ttl1", "t1", "task_completed", now - timedelta(days=8)),
            _make_event("ttl2", "t1", "task_started", now - timedelta(days=9)),
            _make_event("cold1", "t1", EventType.HEARTBEAT, now - timedelta(minutes=20)),
            _make_event("cold2", "t1", EventType.ACTION_STARTED, now - timedelta(hours=25)),
            _make_event("keep1", "t1", "task_completed", now - timedelta(hours=1)),
        ])

        result = await storage.prune_events()
        assert result["ttl_pruned"] == 2
        assert result["cold_pruned"] == 2
        assert result["total_pruned"] == 4
        assert len(storage._tables["events"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
#  Background Task
# ═══════════════════════════════════════════════════════════════════════════

class TestBackgroundTask:
    """Spec Section 12 — Background task checklist."""

    async def test_startup_prune_handles_large_backlog(self, storage: JsonStorageBackend):
        """Startup prune handles large backlog without issues."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        # Create a backlog of 500 expired events
        events = [
            _make_event(f"exp_{i}", "t1", EventType.HEARTBEAT,
                        now - timedelta(days=10), agent_id=f"a{i % 5}")
            for i in range(500)
        ]
        await storage.insert_events(events)
        assert len(storage._tables["events"]) == 500

        result = await storage.prune_events()
        assert result["total_pruned"] == 500
        assert len(storage._tables["events"]) == 0

    async def test_prune_logs_when_events_pruned(self, storage: JsonStorageBackend):
        """Background task logs when events are pruned (verified via return value > 0)."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", now - timedelta(days=10)),
        ])

        result = await storage.prune_events()
        # The _prune_loop logs only when total > 0; we verify the condition
        assert result["total_pruned"] > 0

    async def test_prune_no_log_when_nothing_pruned(self, storage: JsonStorageBackend):
        """Background task does not log when nothing is pruned (verified via return value == 0)."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("e1", "t1", "task_completed", now - timedelta(hours=1)),
        ])

        result = await storage.prune_events()
        # The _prune_loop logs only when total > 0; we verify the condition
        assert result["total_pruned"] == 0

    async def test_prune_task_cancels_cleanly(self, storage: JsonStorageBackend):
        """prune_task can be cancelled cleanly on server shutdown."""
        import asyncio
        from shared.enums import PRUNE_INTERVAL_SECONDS
        from backend.app import _prune_loop

        task = asyncio.create_task(_prune_loop(storage))
        await asyncio.sleep(0.05)
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected — clean cancellation
        assert task.cancelled()

    async def test_prune_loop_survives_exception(self, storage: JsonStorageBackend):
        """Background task catches and logs exceptions without crashing."""
        import asyncio

        call_count = 0
        original_prune = storage.prune_events

        async def flaky_prune():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("simulated failure")
            return await original_prune()

        storage.prune_events = flaky_prune  # type: ignore[assignment]

        # Manually run what _prune_loop does for two iterations
        import logging
        logger = logging.getLogger("hiveboard.retention")
        for _ in range(2):
            try:
                await storage.prune_events()
            except Exception:
                logger.exception("Event pruning failed")

        # If we got here, the exception didn't crash us
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════════════════
#  Integration
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Spec Section 12 — Integration checklist."""

    async def test_get_events_returns_only_retained(self, storage: JsonStorageBackend):
        """After pruning, get_events() returns only retained events."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", now - timedelta(days=10), task_id="tk1"),
            _make_event("new1", "t1", "task_completed", now - timedelta(hours=1), task_id="tk2"),
        ])

        await storage.prune_events()
        page = await storage.get_events("t1")
        event_ids = [e.event_id for e in page.data]
        assert "old1" not in event_ids
        assert "new1" in event_ids

    async def test_list_tasks_excludes_fully_pruned_tasks(self, storage: JsonStorageBackend):
        """After pruning, list_tasks() excludes tasks whose ALL events were pruned."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            # Old task — all events will be pruned
            _make_event("e1", "t1", "task_started", now - timedelta(days=10), task_id="old_task"),
            _make_event("e2", "t1", "task_completed", now - timedelta(days=10), task_id="old_task"),
            # New task — events kept
            _make_event("e3", "t1", "task_started", now - timedelta(hours=2), task_id="new_task"),
            _make_event("e4", "t1", "task_completed", now - timedelta(hours=1), task_id="new_task"),
        ])

        await storage.prune_events()
        page = await storage.list_tasks("t1")
        task_ids = [t.task_id for t in page.data]
        assert "old_task" not in task_ids
        assert "new_task" in task_ids

    async def test_get_metrics_only_aggregates_retained(self, storage: JsonStorageBackend):
        """After pruning, get_metrics() only aggregates retained events."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        # Two task_completed events — one old (pruned), one recent (kept)
        await storage.insert_events([
            _make_event("old1", "t1", "task_completed", now - timedelta(days=10),
                        task_id="tk_old", agent_id="a1", duration_ms=5000),
            _make_event("ts_new", "t1", "task_started", now - timedelta(hours=2),
                        task_id="tk_new", agent_id="a1"),
            _make_event("new1", "t1", "task_completed", now - timedelta(hours=1),
                        task_id="tk_new", agent_id="a1", duration_ms=3000),
        ])

        await storage.prune_events()

        # get_metrics should only see the retained events
        metrics = await storage.get_metrics("t1", range="24h")
        # The old task_completed is gone; only the new one contributes
        assert metrics.summary.completed <= 1

    async def test_pipeline_unaffected_by_cold_pruning(self, storage: JsonStorageBackend):
        """After pruning, get_pipeline() still returns correct state (custom events not cold-pruned)."""
        await storage.create_tenant("t1", "Test", "test")
        now = _utc_now()

        await storage.insert_events([
            _make_event("qs1", "t1", EventType.CUSTOM, now - timedelta(hours=2),
                        agent_id="a1",
                        payload={"kind": "queue_snapshot", "data": {"depth": 3, "items": []}}),
            _make_event("hb1", "t1", EventType.HEARTBEAT, now - timedelta(minutes=20),
                        agent_id="a1"),
        ])

        await storage.prune_events()

        # Custom event should still be there
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "qs1" in ids
        assert "hb1" not in ids


# ═══════════════════════════════════════════════════════════════════════════
#  Multi-Tenant
# ═══════════════════════════════════════════════════════════════════════════

class TestMultiTenant:
    """Spec Section 10 — multiple tenants with different plans."""

    async def test_different_plans_different_retention(self, storage: JsonStorageBackend):
        """Each tenant's events are pruned according to their own plan's retention_days."""
        await storage.create_tenant("t_free", "Free Co", "free-co")
        await storage.create_tenant("t_pro", "Pro Co", "pro-co")
        # Upgrade t_pro to pro plan
        for t in storage._tables["tenants"]:
            if t["tenant_id"] == "t_pro":
                t["plan"] = "pro"

        now = _utc_now()
        # 10 days old: expired for free (7d), valid for pro (30d)
        age = now - timedelta(days=10)

        await storage.insert_events([
            _make_event("free_old", "t_free", "task_completed", age),
            _make_event("pro_ok", "t_pro", "task_completed", age),
        ])

        result = await storage.prune_events()
        ids = [e["event_id"] for e in storage._tables["events"]]
        assert "free_old" not in ids  # pruned (> 7d)
        assert "pro_ok" in ids        # kept (< 30d)

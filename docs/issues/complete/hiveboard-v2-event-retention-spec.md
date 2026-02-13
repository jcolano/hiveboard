# Feature Spec: Event Retention & Pruning

**Feature name:** Per-Tenant Event Retention with Tiered Cold Pruning
**Priority:** Critical — unbounded memory growth degrades all dashboard queries within weeks
**Backend changes:** `storage_json.py` (new methods), `enums.py` (new constants), `app.py` (background task)
**Frontend changes:** None

---

## 1. Problem

`JsonStorageBackend` loads the entire `events.json` into `self._tables["events"]` at startup and keeps it in memory for the process lifetime. Every query — `_filter_events()`, `list_tasks()`, `get_metrics()`, `_get_llm_call_events()`, `get_pipeline()` — does a linear scan of that list with Python-level filtering.

At the current growth rate of ~35K events/day with 10 agents, within one month:

- **~1M events in memory** — each event is a dict with 20+ keys, consuming hundreds of MB of RAM
- **Linear scan degradation on every API call** — `get_metrics()` iterates all events to build timeseries buckets; `list_tasks()` groups all events by `task_id`; `get_pipeline()` filters all events for custom payloads
- **Dashboard polling every 5 seconds** hits `/v1/agents` (which calls `compute_agent_stats_1h()` per agent), `/v1/tasks`, and `/v1/metrics` — each triggering a full scan
- **`_persist()` rewrites the entire file** — at 1M events, each write serializes hundreds of MB to disk via `json.dump()`

The dashboard will visibly slow down well before disk space becomes a concern. This is a **memory and query-performance problem**, not a storage problem.

### What already exists

`PLAN_LIMITS` in `enums.py` (lines 191-213) defines retention windows per tenant plan:

| Plan | `retention_days` |
|------|-----------------|
| FREE | 7 |
| PRO | 30 |
| ENTERPRISE | 90 |

These limits are **defined but not enforced** anywhere in the codebase. There is no cleanup, archival, rotation, or pruning logic.

---

## 2. Solution Overview

Two-phase approach, implemented as methods on `JsonStorageBackend` itself (not an external module), using the existing `self._locks["events"]` async lock for consistency with `insert_events()`.

| Phase | Strategy | Effect |
|-------|----------|--------|
| **Phase 1** | TTL pruning — enforce `PLAN_LIMITS.retention_days` | Caps in-memory list at a bounded time window per tenant |
| **Phase 2** | Cold event pruning — aggressively prune low-value event types | Reduces volume by 30-40% within the retention window |

Both phases are triggered by a single periodic background task.

### What we are NOT doing

- **Count cap per agent** — skipped. A burst of heartbeats could evict important `task_completed` events. TTL + tiered pruning covers the same ground with predictable outcomes.
- **Rollup / compaction** — deferred. Requires designing a summary schema, handling rollup records differently in every query path, and deciding what to preserve vs. collapse. Phase 1 + 2 keep the system healthy for a long time without it.

---

## 3. Phase 1: TTL Pruning

### 3.1 Logic

For each tenant, look up their plan from `self._tables["tenants"]`, resolve `retention_days` from `PLAN_LIMITS`, compute a cutoff timestamp, and filter out all events older than the cutoff.

### 3.2 Method on `JsonStorageBackend`

```python
async def prune_expired_events(self) -> int:
    """Remove events older than each tenant's plan retention window.

    Returns the total number of events pruned across all tenants.
    Must be called periodically from a background task.
    """
    now = _now_utc()

    # Build per-tenant cutoff timestamps
    cutoffs: dict[str, datetime] = {}
    for t in self._tables["tenants"]:
        plan = t.get("plan", "free")
        days = PLAN_LIMITS.get(plan, {}).get("retention_days", 7)
        cutoffs[t["tenant_id"]] = now - timedelta(days=days)

    async with self._locks["events"]:
        before = len(self._tables["events"])
        self._tables["events"] = [
            row for row in self._tables["events"]
            if self._is_event_within_retention(row, cutoffs, now)
        ]
        pruned = before - len(self._tables["events"])
        if pruned > 0:
            self._persist("events")
    return pruned

def _is_event_within_retention(
    self,
    row: dict[str, Any],
    cutoffs: dict[str, datetime],
    now: datetime,
) -> bool:
    """Check if an event is within its tenant's retention window."""
    tenant_id = row.get("tenant_id")
    cutoff = cutoffs.get(tenant_id)
    if cutoff is None:
        # Unknown tenant — keep the event (don't silently drop data)
        return True
    ts = _parse_dt(row.get("timestamp"))
    if ts is None:
        # Unparseable timestamp — keep it (defensive)
        return True
    return ts >= cutoff
```

### 3.3 Impact

For a free-tier tenant with 10 agents generating 35K events/day:
- **Before:** Unbounded — 1M events after 30 days, growing forever
- **After:** Capped at ~245K events (7 days * 35K/day)

For a pro-tier tenant: capped at ~1.05M events (30 days). For enterprise: ~3.15M (90 days). These are firm upper bounds instead of infinite growth.

---

## 4. Phase 2: Cold Event Pruning

### 4.1 Rationale

Not all event types carry equal value. Within the retention window, two event types are high-volume and low-value:

| Event Type | Volume | Value After Minutes | Why Low Value |
|---|---|---|---|
| `heartbeat` | ~30-40% of all events (one per agent every few seconds) | Negligible. Only used for stuck detection; `AgentRecord.last_heartbeat` already captures the latest. No dashboard feature reads old heartbeats. | `derive_agent_status()` uses `agent.last_heartbeat`, not historical heartbeat events. `_filter_events()` skips heartbeats by default (`exclude_heartbeats=True`). |
| `action_started` | ~15-20% of all events | Low after 24h. Carries no `duration_ms` — only `action_completed` does. The action tree reconstruction for task timelines only matters for recent/active tasks. | `action_completed` and `action_failed` carry `duration_ms`, `status`, and outcome data. `action_started` is the opening bracket — once the action completes, it adds no information beyond what `action_completed` provides. |

### 4.2 Cold retention constants

Add to `enums.py`:

```python
# ---------------------------------------------------------------------------
# Cold Event Retention — shorter retention for high-volume, low-value events
# ---------------------------------------------------------------------------

COLD_EVENT_RETENTION: dict[str, int] = {
    EventType.HEARTBEAT: 600,          # 10 minutes (in seconds)
    EventType.ACTION_STARTED: 86400,   # 24 hours (in seconds)
}
```

### 4.3 Method on `JsonStorageBackend`

```python
async def prune_cold_events(self) -> int:
    """Remove high-volume, low-value events with shorter retention.

    Heartbeats are pruned after 10 minutes (stuck detection uses
    AgentRecord.last_heartbeat, not historical events).

    action_started events are pruned after 24 hours (they carry no
    duration_ms — only action_completed does).

    Returns total number of events pruned.
    """
    now = _now_utc()

    async with self._locks["events"]:
        before = len(self._tables["events"])
        self._tables["events"] = [
            row for row in self._tables["events"]
            if self._is_cold_event_within_retention(row, now)
        ]
        pruned = before - len(self._tables["events"])
        if pruned > 0:
            self._persist("events")
    return pruned

def _is_cold_event_within_retention(
    self,
    row: dict[str, Any],
    now: datetime,
) -> bool:
    """Check if a cold event type is within its shorter retention window.

    Non-cold event types always return True (kept by this filter).
    """
    event_type = row.get("event_type")
    max_age_seconds = COLD_EVENT_RETENTION.get(event_type)
    if max_age_seconds is None:
        # Not a cold event type — keep it
        return True
    ts = _parse_dt(row.get("timestamp"))
    if ts is None:
        return True
    age = (now - ts).total_seconds()
    return age <= max_age_seconds
```

### 4.4 Impact

Assuming heartbeats are 35% of total volume and `action_started` is 15%:
- Heartbeat pruning (10 min retention): removes ~34% of events within the retention window
- `action_started` pruning (24h retention): removes an additional ~14% of events older than 24h
- **Combined: ~40-50% reduction** in steady-state in-memory event count

For a free-tier tenant: from ~245K events down to ~130K-150K events in memory.

---

## 5. Unified Prune Method

To avoid acquiring the lock twice per cycle, Phase 1 and Phase 2 should run as a single pass through the events list:

```python
async def prune_events(self) -> dict[str, int]:
    """Run all event retention policies in a single pass.

    Combines TTL pruning (plan-based retention) and cold event pruning
    (shorter retention for heartbeats and action_started).

    Returns dict with counts: {"ttl_pruned": N, "cold_pruned": N, "total_pruned": N}
    """
    now = _now_utc()

    # Build per-tenant TTL cutoffs
    cutoffs: dict[str, datetime] = {}
    for t in self._tables["tenants"]:
        plan = t.get("plan", "free")
        days = PLAN_LIMITS.get(plan, {}).get("retention_days", 7)
        cutoffs[t["tenant_id"]] = now - timedelta(days=days)

    ttl_pruned = 0
    cold_pruned = 0

    async with self._locks["events"]:
        before = len(self._tables["events"])
        kept: list[dict[str, Any]] = []

        for row in self._tables["events"]:
            # Phase 1: TTL check
            if not self._is_event_within_retention(row, cutoffs, now):
                ttl_pruned += 1
                continue

            # Phase 2: Cold event check
            if not self._is_cold_event_within_retention(row, now):
                cold_pruned += 1
                continue

            kept.append(row)

        total_pruned = ttl_pruned + cold_pruned
        if total_pruned > 0:
            self._tables["events"] = kept
            self._persist("events")

    return {
        "ttl_pruned": ttl_pruned,
        "cold_pruned": cold_pruned,
        "total_pruned": total_pruned,
    }
```

The individual `_is_event_within_retention()` and `_is_cold_event_within_retention()` helper methods are kept for clarity and testability.

---

## 6. Background Task

### 6.1 Prune loop

```python
async def _prune_loop(storage: JsonStorageBackend):
    """Periodically prune expired and cold events."""
    import logging
    logger = logging.getLogger("hiveboard.retention")

    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            result = await storage.prune_events()
            total = result["total_pruned"]
            if total > 0:
                logger.info(
                    "Event pruning: %d removed (ttl=%d, cold=%d), %d remaining",
                    total,
                    result["ttl_pruned"],
                    result["cold_pruned"],
                    len(storage._tables["events"]),
                )
        except Exception:
            logger.exception("Event pruning failed")
```

### 6.2 Registration in `app.py` lifespan

Add to the existing `lifespan()` function alongside `_ws_ping_loop`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = JsonStorageBackend()
    await storage.initialize()
    app.state.storage = storage

    pricing = LlmPricingEngine()
    await pricing.initialize()
    app.state.pricing = pricing

    await _bootstrap_dev_tenant(storage)

    from backend.websocket import ws_manager
    ping_task = asyncio.create_task(_ws_ping_loop())
    prune_task = asyncio.create_task(_prune_loop(storage))  # NEW

    yield

    prune_task.cancel()  # NEW
    ping_task.cancel()
    await storage.close()
```

### 6.3 Prune interval constant

Add to `enums.py`:

```python
# ---------------------------------------------------------------------------
# Event Retention — pruning interval
# ---------------------------------------------------------------------------

PRUNE_INTERVAL_SECONDS = 300  # 5 minutes
```

5 minutes is frequent enough to keep memory bounded (worst case: 5 minutes of unbounded accumulation before the next prune) and cheap when there's nothing to prune (single list traversal).

---

## 7. Startup Pruning

On server startup, `events.json` may contain a large backlog of expired events from a previous run. The prune should run once immediately after `initialize()`, before the server starts accepting requests:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    storage = JsonStorageBackend()
    await storage.initialize()

    # Prune stale events before serving requests
    result = await storage.prune_events()
    if result["total_pruned"] > 0:
        logger.info(
            "Startup pruning: %d events removed (ttl=%d, cold=%d), %d remaining",
            result["total_pruned"],
            result["ttl_pruned"],
            result["cold_pruned"],
            len(storage._tables["events"]),
        )

    app.state.storage = storage
    # ... rest of lifespan
```

This prevents the first few seconds of dashboard requests from scanning a bloated event list.

---

## 8. Constants Summary

All new constants added to `enums.py`:

```python
# ---------------------------------------------------------------------------
# Event Retention
# ---------------------------------------------------------------------------

PRUNE_INTERVAL_SECONDS = 300  # 5 minutes — background prune cycle

COLD_EVENT_RETENTION: dict[str, int] = {
    EventType.HEARTBEAT: 600,          # 10 minutes
    EventType.ACTION_STARTED: 86400,   # 24 hours
}
```

Existing constants used (no changes):

```python
PLAN_LIMITS = {
    TenantPlan.FREE:       {"retention_days": 7,  ...},
    TenantPlan.PRO:        {"retention_days": 30, ...},
    TenantPlan.ENTERPRISE: {"retention_days": 90, ...},
}
```

---

## 9. Imports

New import in `storage_json.py`:

```python
from datetime import datetime, timedelta, timezone  # add timedelta
from shared.enums import COLD_EVENT_RETENTION, PLAN_LIMITS, PRUNE_INTERVAL_SECONDS
```

New import in `app.py`:

```python
import logging  # if not already present
```

---

## 10. Edge Cases

| Scenario | Behavior |
|---|---|
| **Unknown tenant_id on event** | `_is_event_within_retention()` returns `True` — don't silently drop data for orphaned events |
| **Unparseable timestamp** | Both helpers return `True` — keep the event rather than risk data loss |
| **No tenants in table** | `cutoffs` dict is empty; all events kept (no TTL applied). Cold pruning still applies. |
| **Prune runs during active ingestion** | Safe — both `prune_events()` and `insert_events()` acquire `self._locks["events"]`. One blocks until the other completes. |
| **Empty events table** | Single-pass over empty list, no `_persist()` call. Negligible cost. |
| **Server crash during `_persist()`** | Existing atomic write pattern (`os.replace(tmp, fp)`) ensures either the old or new file is present — never a partial write. |
| **All events pruned** | `self._tables["events"]` becomes `[]`. `_persist()` writes `[]` to disk. Next startup loads empty list. All queries return empty results. |
| **Cold pruning removes event needed by active task timeline** | `action_started` has 24h retention — active tasks (minutes/hours old) are unaffected. Heartbeats are not used by any task query. |
| **alert_history table** | Not pruned in this spec. It grows much slower (one record per alert firing). Can be addressed in a follow-up if needed. |
| **Multiple tenants with different plans** | Each tenant's events are pruned according to their own plan's `retention_days`. The cutoff map handles this per-tenant. |

---

## 11. Observability

### 11.1 Logging

- Log only when `total_pruned > 0` — avoids noise during idle periods
- Include breakdown (`ttl_pruned`, `cold_pruned`) and remaining count
- Log at `INFO` level for normal pruning, `EXCEPTION` level if the prune fails
- Log startup pruning separately (may prune a large backlog)

### 11.2 Future: expose via API

Not in scope for this spec, but a natural follow-up would be a `GET /v1/admin/retention` endpoint returning:

```json
{
    "events_in_memory": 142350,
    "last_prune_at": "2026-02-13T15:30:00Z",
    "last_prune_result": {"ttl_pruned": 0, "cold_pruned": 1247, "total_pruned": 1247},
    "tenant_retention": {"dev": {"plan": "free", "retention_days": 7}}
}
```

---

## 12. Testing Checklist

### Phase 1: TTL Pruning

- [ ] Events older than `retention_days` for a free tenant (7d) are pruned
- [ ] Events older than `retention_days` for a pro tenant (30d) are pruned
- [ ] Events within the retention window are kept
- [ ] Events with unparseable timestamps are kept (not silently dropped)
- [ ] Events with unknown `tenant_id` are kept
- [ ] `_persist()` is called only when events are actually pruned
- [ ] `_persist()` is NOT called when nothing is pruned (no unnecessary disk I/O)
- [ ] Prune acquires `self._locks["events"]` (concurrent `insert_events()` blocked)
- [ ] Return value correctly reports count of pruned events

### Phase 2: Cold Event Pruning

- [ ] Heartbeat events older than 10 minutes are pruned
- [ ] Heartbeat events younger than 10 minutes are kept
- [ ] `action_started` events older than 24 hours are pruned
- [ ] `action_started` events younger than 24 hours are kept
- [ ] `action_completed` events are NOT affected by cold pruning (regardless of age)
- [ ] `task_completed`, `task_failed` events are NOT affected
- [ ] Custom events with pipeline payloads (`queue_snapshot`, `todo`, `issue`) are NOT affected

### Unified Prune

- [ ] Single pass applies both TTL and cold pruning
- [ ] An event outside TTL is counted as `ttl_pruned` (not double-counted as cold)
- [ ] Lock is acquired only once per prune cycle
- [ ] Return dict contains correct `ttl_pruned`, `cold_pruned`, `total_pruned`

### Background Task

- [ ] Prune loop runs every 5 minutes (configurable via `PRUNE_INTERVAL_SECONDS`)
- [ ] Prune runs once at startup before server accepts requests
- [ ] Startup prune handles large backlog (e.g., 500K expired events) without timeout
- [ ] Background task logs when events are pruned
- [ ] Background task does not log when nothing is pruned
- [ ] Background task catches and logs exceptions without crashing the server
- [ ] `prune_task` is cancelled cleanly on server shutdown

### Integration

- [ ] After pruning, `get_events()` returns only retained events
- [ ] After pruning, `list_tasks()` excludes tasks whose ALL events were pruned
- [ ] After pruning, `get_metrics()` only aggregates retained events
- [ ] After pruning, `get_pipeline()` still returns correct pipeline state (custom events are not cold-pruned)
- [ ] Dashboard continues to function normally after prune cycle

---

## 13. Files to Modify

| File | Changes |
|---|---|
| `src/shared/enums.py` | Add `PRUNE_INTERVAL_SECONDS` constant. Add `COLD_EVENT_RETENTION` dict mapping cold event types to retention seconds. |
| `src/backend/storage_json.py` | Add `timedelta` import. Add `prune_events()`, `_is_event_within_retention()`, `_is_cold_event_within_retention()` methods to `JsonStorageBackend`. Import `COLD_EVENT_RETENTION`, `PLAN_LIMITS`. |
| `src/backend/app.py` | Add `_prune_loop()` function. Add startup prune call in `lifespan()`. Register `prune_task` alongside `ping_task`. Cancel on shutdown. Add logging. |

---

## 14. Implementation Order

1. **Add constants to `enums.py`** — `PRUNE_INTERVAL_SECONDS`, `COLD_EVENT_RETENTION`
2. **Add helper methods to `storage_json.py`** — `_is_event_within_retention()`, `_is_cold_event_within_retention()`
3. **Add `prune_events()` to `storage_json.py`** — unified single-pass prune
4. **Add startup prune to `app.py` lifespan** — prune before serving
5. **Add `_prune_loop()` and register background task in `app.py`** — periodic pruning
6. **Test** — unit tests for helpers, integration test for background task

---

## 15. Future Work (Out of Scope)

- **Rollup / compaction (Strategy 4):** Aggregate old events into daily summary records before pruning. Requires summary schema design, changes to query paths, and decisions about what to preserve. Deferred until Phase 1 + 2 prove insufficient.
- **`alert_history` pruning:** Same unbounded growth pattern but much slower rate. Apply TTL pruning to alert history in a follow-up.
- **Admin API for retention status:** `GET /v1/admin/retention` endpoint exposing prune stats, event counts, and per-tenant retention config.
- **Configurable cold retention per tenant:** Allow enterprise tenants to override `COLD_EVENT_RETENTION` thresholds. Currently global constants.
- **MS SQL Server migration:** The `storage_json.py` header notes this is an MVP backend. Retention logic should be straightforward to port — SQL `DELETE WHERE timestamp < cutoff` replaces the list comprehension.

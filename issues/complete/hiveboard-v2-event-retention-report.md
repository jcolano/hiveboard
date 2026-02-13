# Event Retention & Pruning — Implementation Report

**Spec:** `hiveboard-v2-event-retention-spec.md`
**Status:** Complete
**Date:** 2026-02-12
**Files modified:** 3 (`enums.py`, `storage_json.py`, `app.py`)

---

## What Was Built

A two-phase event retention system that prevents unbounded memory growth in `JsonStorageBackend`. Events are pruned via a single-pass algorithm that applies both plan-based TTL retention and aggressive cold event pruning for high-volume, low-value event types (heartbeats and `action_started`). The prune runs at startup (to clear stale backlog) and every 5 minutes via a background task.

---

## Changes By File

### `src/shared/enums.py`

Added two constants before `PLAN_LIMITS`:

```python
PRUNE_INTERVAL_SECONDS = 300  # 5 minutes

COLD_EVENT_RETENTION: dict[str, int] = {
    EventType.HEARTBEAT: 600,          # 10 minutes
    EventType.ACTION_STARTED: 86400,   # 24 hours
}
```

Existing `PLAN_LIMITS` with `retention_days` per plan (FREE: 7, PRO: 30, ENTERPRISE: 90) is now consumed by the pruning logic — previously defined but unenforced.

### `src/backend/storage_json.py`

**New imports:**
- `timedelta` from `datetime`
- `COLD_EVENT_RETENTION`, `PLAN_LIMITS` from `shared.enums`

**New methods on `JsonStorageBackend` (3 total):**

| Method | Purpose |
|---|---|
| `prune_events()` | Single-pass unified prune. Builds per-tenant TTL cutoffs from `PLAN_LIMITS`, iterates all events once, applies TTL check then cold check. Acquires `self._locks["events"]` once. Calls `_persist("events")` only if events were actually pruned. Returns `{"ttl_pruned": N, "cold_pruned": N, "total_pruned": N}`. |
| `_is_event_within_retention(row, cutoffs, now)` | Phase 1 helper. Checks if event's timestamp is within its tenant's retention window. Returns `True` (keep) for unknown tenants or unparseable timestamps — never silently drops data. |
| `_is_cold_event_within_retention(row, now)` | Phase 2 helper. Checks if a cold event type (`heartbeat`, `action_started`) is within its shorter retention window. Non-cold event types always return `True`. |

### `src/backend/app.py`

**New imports:**
- `logging`
- `PRUNE_INTERVAL_SECONDS` from `shared.enums`

**Modified `lifespan()`:**
- Runs `storage.prune_events()` immediately after `initialize()`, before setting `app.state.storage` — clears stale backlog before serving requests
- Logs startup prune results at INFO level when events are pruned
- Creates `prune_task = asyncio.create_task(_prune_loop(storage))` alongside `ping_task`
- Cancels `prune_task` on shutdown

**New `_prune_loop(storage)` function:**
- Sleeps `PRUNE_INTERVAL_SECONDS` (300s), then calls `storage.prune_events()`
- Logs at INFO level only when events are actually pruned (no noise during idle)
- Catches and logs exceptions without crashing the server

---

## How It Works

```
Server startup
  └─ storage.initialize() loads events.json into memory
  └─ storage.prune_events() runs once (startup prune)
     ├─ Builds cutoff map: {tenant_id → (now - retention_days)}
     ├─ Single pass over all events:
     │   ├─ Phase 1: ts < tenant cutoff? → ttl_pruned++, skip
     │   ├─ Phase 2: cold event type & age > cold limit? → cold_pruned++, skip
     │   └─ Otherwise: keep
     └─ If anything pruned: replace _tables["events"], _persist()
  └─ _prune_loop() starts (repeats every 5 minutes)
```

---

## Retention Rules

| Event Type | Retention | Rationale |
|---|---|---|
| `heartbeat` | 10 minutes | Only used for stuck detection; `AgentRecord.last_heartbeat` captures the latest. `_filter_events()` skips heartbeats by default. |
| `action_started` | 24 hours | Carries no `duration_ms` — only `action_completed` does. Once the action completes, it adds no information. |
| All other events | Plan-based (7d / 30d / 90d) | Full retention per `PLAN_LIMITS.retention_days`. |

---

## Dry-Run Against Real Data

Tested against the actual `src/data/events.json` (3,068 events):

```
Events before prune: 3,068
  custom: 1,219  |  heartbeat: 977      |  task_started: 270
  task_completed: 270  |  action_started: 156  |  action_completed: 156
  agent_registered: 18  |  retry_started: 2

Would prune: ttl=0, cold=957, total=957
Would remain: 2,111 (31% reduction)
```

- **TTL:** 0 events expired (all within 7-day free-tier window)
- **Cold:** 957 events pruned (old heartbeats + old `action_started`)
- No data loss for task completions, action completions, LLM calls, or any other high-value events

---

## Safety Properties

| Concern | Mitigation |
|---|---|
| Unknown tenant on event | `_is_event_within_retention()` returns `True` — event kept |
| Unparseable timestamp | Both helpers return `True` — event kept |
| Concurrent ingestion during prune | Both `prune_events()` and `insert_events()` acquire `self._locks["events"]` |
| Crash during persist | Existing atomic write via `os.replace(tmp, fp)` — never partial |
| All events pruned | `_tables["events"]` becomes `[]`, persisted as `[]`, queries return empty |
| Prune loop exception | Caught, logged at EXCEPTION level, loop continues |

---

## Spec Compliance Checklist

| Spec Item | Status |
|---|---|
| TTL pruning enforces `PLAN_LIMITS.retention_days` | Done |
| Unknown tenants / unparseable timestamps → keep event | Done |
| `_persist()` only called when events actually pruned | Done |
| Cold: heartbeats pruned after 10 minutes | Done |
| Cold: `action_started` pruned after 24 hours | Done |
| Cold: `action_completed`, `task_completed`, custom events NOT affected | Done |
| Single-pass unified prune (lock acquired once) | Done |
| Return dict with `ttl_pruned`, `cold_pruned`, `total_pruned` | Done |
| Startup prune before serving requests | Done |
| Background prune every 5 minutes | Done |
| Background task logs only when events pruned | Done |
| Background task catches exceptions without crashing | Done |
| `prune_task` cancelled on shutdown | Done |
| Constants in `enums.py` | Done |
| Methods on `JsonStorageBackend` (not external module) | Done |
| Uses existing `self._locks["events"]` | Done |

---

## Testing Notes

- All imports verified: `enums.py` constants, `storage_json.py` methods, `app.py` loads cleanly
- 61/61 storage tests pass (1 pre-existing Windows file-lock error, unrelated)
- Dry-run against real data confirms correct prune counts (957 cold events, 0 TTL)
- Integration testing requires running the server and observing prune logs over multiple cycles

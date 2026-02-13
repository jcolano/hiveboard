# Team 1 — Backend Implementation Plan

> **Scope:** Storage layer + API server (REST, WebSocket, Alerts)
> **Contract:** Implements the API spec (`docs/3_hiveboard-api-sdk-spec-v3.md`)
> **Tests with:** curl / httpie / fixture JSON batches (`shared/fixtures/sample_batch.json`)
> **Shared types:** `from shared import EventType, Event, StorageBackend, ...`
> **Storage:** JSON files (MVP) → MS SQL Server (production)

---

## Build Order

```
B1.1 → B1.2 → B1.3
                 │
                 ▼
B2.1 → B2.2 → B2.3 → B2.4 → B2.5 → B3
        │
        └── (B2.3 query endpoints can be parallelized across developers)
```

B1 (Storage) must come first — the API server depends on it.
Within B2, the ingestion endpoint (B2.2) comes before queries (B2.3), since
queries need data to return. WebSocket (B2.4) and Alerts (B2.5) depend on
the ingestion path being functional.

---

## Phase B1: Storage Layer

> **Location:** `backend/storage_json.py` (MVP), `backend/storage_mssql.py` (future)
> **Protocol:** `shared/storage.py` — `StorageBackend` (35 async methods)
> **Tests:** `tests/test_storage.py` — runs against the abstract interface

### B1.1 — StorageBackend Protocol (already done in Phase 0)

The protocol is defined in `shared/storage.py` with 35 methods, all using explicit
SQL-friendly parameters. Team 1's job is to implement it.

**Design reminder (from the protocol header):**
> Production target is MS SQL Server. Every method must map cleanly to SQL.
> If a method can't be implemented as a single SQL query, redesign the signature.

---

### B1.2 — JSON File Storage Implementation

> **File:** `backend/storage_json.py`
> **Class:** `JsonStorageBackend` implementing `StorageBackend`

One JSON file per table, thread-safe access, in-memory working set on startup.

| # | Sub-task | Description |
|---|----------|-------------|
| B1.2.1 | **File layout & locking** | Data directory (configurable via `HIVEBOARD_DATA` env var). Files: `tenants.json`, `api_keys.json`, `projects.json`, `agents.json`, `project_agents.json`, `events.json`, `alert_rules.json`, `alert_history.json`. Thread-safe access via `asyncio.Lock` per file. Load all into memory on `initialize()`. |
| B1.2.2 | **Tenant & API key operations** | `create_tenant()`, `get_tenant()`. `create_api_key()`, `authenticate()` (lookup by key_hash in active keys), `touch_api_key()` (update last_used_at), `list_api_keys()`, `revoke_api_key()`. |
| B1.2.3 | **Project CRUD** | `create_project()` (generate project_id, set created_at/updated_at), `get_project()`, `list_projects()` (filter by is_archived), `update_project()`, `archive_project()`. Auto-create "default" project on tenant creation. |
| B1.2.4 | **Agent upsert & queries** | `upsert_agent()` — create-or-update with COALESCE semantics: only overwrite fields when new value is non-null, always update last_seen, only update last_heartbeat when event is heartbeat. `get_agent()`, `list_agents()` (with optional project_id filter via project_agents junction). |
| B1.2.5 | **Project-agent junction** | `upsert_project_agent()` — idempotent insert (skip if already exists). |
| B1.2.6 | **Event insertion** | `insert_events()` — deduplicates by `(tenant_id, event_id)`. Appends to events list. Returns count of actually inserted events. |
| B1.2.7 | **Event queries** | `get_events()` — filter by all parameters (project_id, agent_id, task_id, event_type, severity, environment, group, since, until, exclude_heartbeats), reverse-chronological, cursor pagination. `get_task_events()` — all events for a task, chronological. |
| B1.2.8 | **Task queries (derived)** | `list_tasks()` — group events by task_id, derive status from event_types present (completed > failed > escalated > waiting > processing), compute duration_ms, total_cost (from llm_call payloads), action_count, error_count. Filter by agent_id, project_id, task_type, status, environment. Sort by newest/oldest/duration/cost. |
| B1.2.9 | **Metrics aggregation** | `get_metrics()` — filter events in time range, compute summary (total_tasks, completed, failed, escalated, success_rate, avg_duration_ms, total_cost), build timeseries buckets at specified interval. |
| B1.2.10 | **Cost queries** | `get_cost_summary()` — aggregate from custom events with `payload.kind = "llm_call"`. Group by agent or model. Sum cost, tokens_in, tokens_out. `get_cost_calls()` — individual LLM call records, paginated. `get_cost_timeseries()` — cost in time buckets. |
| B1.2.11 | **Pipeline queries (derived)** | `get_pipeline()` — for a given agent: (1) latest queue_snapshot event, (2) active TODOs (group by todo_id, take latest action, filter out completed/dismissed), (3) latest scheduled event, (4) active issues (group by issue_id or summary, take latest action, filter out resolved). |
| B1.2.12 | **Alert CRUD** | `create_alert_rule()`, `get_alert_rule()`, `list_alert_rules()`, `update_alert_rule()`, `delete_alert_rule()`. `insert_alert()`, `list_alert_history()`, `get_last_alert_for_rule()` (for cooldown checking). |
| B1.2.13 | **Persistence** | Write-through: every mutation writes the full JSON file immediately. Acceptable for MVP (events file will grow). Future optimization: append-only log + periodic compaction. |

**Agent status derivation logic (priority cascade):**
```
1. No heartbeat OR heartbeat older than stuck_threshold → "stuck"
2. Last event type in (task_failed, action_failed) → "error"
3. Last event type = approval_requested → "waiting_approval"
4. Last event type in (task_started, action_started) → "processing"
5. Otherwise → "idle"
```

**Task status derivation logic:**
```
1. task_completed event exists → "completed"
2. task_failed event exists → "failed"
3. escalated event exists (and not completed/failed) → "escalated"
4. approval_requested without approval_received → "waiting"
5. Otherwise → "processing"
```

**Exit criterion:** All 35 StorageBackend methods implemented. Unit tests pass for every method using shared fixtures.

---

### B1.3 — Storage Tests

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| B1.3.1 | **Test harness** | Async test setup: create fresh `JsonStorageBackend` with temp directory per test, call `initialize()`, tear down after. Tests written against `StorageBackend` protocol — same suite will validate MS SQL Server later. | `tests/conftest.py` |
| B1.3.2 | **Tenant & auth tests** | Create tenant, create API keys (live/test/read), authenticate by hash, verify key type, revoke key, verify revoked key returns None. | `tests/test_storage.py` |
| B1.3.3 | **Project tests** | Create project, list (include/exclude archived), update, archive. Verify default project auto-created. | `tests/test_storage.py` |
| B1.3.4 | **Ingestion tests** | Insert sample batch from fixture. Verify deduplication (insert same batch twice, count unchanged). Verify agent upserted. Verify project_agents junction populated. | `tests/test_storage.py` |
| B1.3.5 | **Query tests** | After inserting fixture: `get_events()` with various filters (agent_id, event_type, severity, since/until, exclude_heartbeats). `get_task_events()` for a known task_id. `list_tasks()` with sort/filter. Verify derived status for known tasks. | `tests/test_storage.py` |
| B1.3.6 | **Metrics & cost tests** | `get_metrics()` with 1h range — verify summary counts match fixture. `get_cost_summary()` — verify total cost matches sum of llm_call events. `get_cost_calls()` — verify individual records. | `tests/test_storage.py` |
| B1.3.7 | **Pipeline tests** | Insert events with all well-known payload kinds for one agent. `get_pipeline()` — verify queue, todos, scheduled, issues sections populated correctly. Test TODO lifecycle: create → complete → verify not in active list. | `tests/test_storage.py` |
| B1.3.8 | **Alert tests** | Create rule, list rules, update rule, delete rule. Insert alert history. `get_last_alert_for_rule()` for cooldown check. | `tests/test_storage.py` |

**Exit criterion:** All tests pass. Same test file can later be pointed at `MsSqlStorageBackend` with zero changes.

---

## Phase B2: API Server

> **Location:** `backend/app.py`
> **Framework:** FastAPI (async-native)
> **Dependencies:** `fastapi`, `uvicorn[standard]`, `websockets`

### B2.1 — FastAPI App + Auth Middleware

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| B2.1.1 | **App skeleton** | FastAPI app with title, version, CORS (allow all origins for MVP). Startup event: `storage.initialize()`. Shutdown event: `storage.close()`. Mount storage as app dependency via FastAPI's dependency injection. | `app.py` |
| B2.1.2 | **Auth middleware** | Extract API key from `Authorization: Bearer {key}` header. SHA-256 hash the key. Call `storage.authenticate(key_hash)`. If None → 401 (`authentication_failed`). If key_type is `read` and request is POST/PUT/DELETE → 403 (`insufficient_permissions`). Inject `tenant_id` and `key_type` into request state. Fire-and-forget `storage.touch_api_key()`. | `middleware.py` |
| B2.1.3 | **Error formatting** | All errors return spec-compliant shape: `{"error": "code", "message": "...", "status": N, "details": {}}`. Custom exception handler for `HTTPException`. Validation errors return `"error": "validation_error"` with field-level details. | `app.py` |
| B2.1.4 | **Rate limit headers** | Add `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` to every response. MVP: in-memory counter per API key with sliding window. Return 429 when exceeded with `retry_after_seconds` in details. | `middleware.py` |
| B2.1.5 | **Dashboard serving** | `GET /dashboard` → serve `docs/hiveboard-dashboard-v3.html` as HTMLResponse. (Later replaced by Team 2's live dashboard.) | `app.py` |

**Exit criterion:** App starts, authenticates requests, rejects invalid/read keys correctly, returns proper error shapes.

---

### B2.2 — Ingestion Endpoint

> The critical write path. This is the single entry point for all telemetry.

| # | Sub-task | Description |
|---|----------|-------------|
| B2.2.1 | **Request parsing** | `POST /v1/ingest`. Parse `IngestRequest` body (envelope + events array). Validate envelope has `agent_id`. Validate batch constraints: max 500 events, max 1MB body. |
| B2.2.2 | **Per-event validation** | For each event: require `event_id`, `timestamp`, `event_type`. Validate `event_type` against 13-value enum. Validate field size limits (agent_id ≤ 256, task_id ≤ 256, environment ≤ 64, group ≤ 128, payload ≤ 32KB). |
| B2.2.3 | **Payload convention validation (advisory)** | If `payload.kind` matches a well-known kind, validate required `data` fields. On failure: add to `warnings` array, do NOT reject. Kinds: `llm_call` (needs data.name, data.model), `queue_snapshot` (needs data.depth), `todo` (needs data.todo_id, data.action), `plan_created` (needs data.steps), `plan_step` (needs data.step_index, data.total_steps, data.action), `issue` (needs data.severity), `scheduled` (needs data.items). |
| B2.2.4 | **Envelope expansion** | Merge envelope fields into each event: `agent_id` (event overrides if present), `agent_type`, `environment`, `group`. Set `tenant_id` from auth context. Set `received_at` to server UTC now. Apply severity auto-defaults (from `SEVERITY_DEFAULTS` map, plus payload-kind overrides). |
| B2.2.5 | **Project validation** | If event has `project_id`: verify project exists for tenant via `storage.get_project()`. If not found: reject event with `"invalid_project_id"` error. If `project_id` is null: allowed (agent-level event). |
| B2.2.6 | **Batch insert** | Call `storage.insert_events(expanded_events)`. Deduplication happens inside storage. |
| B2.2.7 | **Agent cache update** | Call `storage.upsert_agent()` with envelope metadata + latest timestamp from batch. Set `last_heartbeat` if batch contains heartbeat events. Set `last_event_type`, `last_task_id`, `last_project_id` from the most recent event in the batch. |
| B2.2.8 | **Project-agent junction** | For each event with a `project_id`: call `storage.upsert_project_agent(tenant_id, project_id, agent_id)`. |
| B2.2.9 | **WebSocket broadcast** | Push accepted events to WebSocket subscribers (filtered by their subscription). Push agent status change if derived status changed. (Wired in B2.4.) |
| B2.2.10 | **Alert evaluation** | Evaluate enabled alert rules against the new batch. (Wired in B2.5.) |
| B2.2.11 | **Response** | 200 if all accepted. 207 if any rejected. Body: `{"accepted": N, "rejected": M, "warnings": [...], "errors": [...]}`. Each error: `{"event_id": "...", "error": "code", "message": "..."}`. |

**Exit criterion:** Can POST the sample fixture batch, get 200 with all 22 events accepted. Can POST invalid events, get 207 with correct error details. Deduplication works (POST same batch twice, events not doubled).

---

### B2.3 — Query Endpoints

27 endpoints total. Each delegates to a `StorageBackend` method and formats the response.

#### Agent Endpoints

| # | Endpoint | Parameters | Storage Method |
|---|----------|------------|----------------|
| B2.3.1 | `GET /v1/agents` | project_id, environment, group, status, sort (attention/name/last_seen), limit, cursor | `list_agents()` + derive status from agent record |
| B2.3.2 | `GET /v1/agents/{agent_id}` | project_id, metrics_range, metrics_interval | `get_agent()` + compute stats |
| B2.3.3 | `GET /v1/agents/{agent_id}/pipeline` | _(none)_ | `get_pipeline()` |

**Agent status derivation:** For each agent record, compute derived_status using the priority cascade (stuck > error > waiting_approval > processing > idle). Compute `heartbeat_age_seconds`, `is_stuck`, `stats_1h` (tasks completed/failed, success rate, avg duration, total cost from last hour of events).

#### Task Endpoints

| # | Endpoint | Parameters | Storage Method |
|---|----------|------------|----------------|
| B2.3.4 | `GET /v1/tasks` | project_id, agent_id, task_type, status, environment, group, since, until, sort, limit, cursor | `list_tasks()` |
| B2.3.5 | `GET /v1/tasks/{task_id}/timeline` | task_run_id | `get_task_events()` + build action tree + error chains + plan overlay |

**Timeline response assembly:**
1. Fetch all events for the task (chronological)
2. Build `action_tree`: group action_started/completed/failed by action_id, nest children via parent_action_id
3. Build `error_chains`: follow parent_event_id links from retry/escalation events
4. Build `plan` overlay: find latest `plan_created` event, collect `plan_step` events, compute progress per step

#### Event Endpoint

| # | Endpoint | Parameters | Storage Method |
|---|----------|------------|----------------|
| B2.3.6 | `GET /v1/events` | project_id, agent_id, task_id, event_type (comma-sep), severity (comma-sep), environment, group, payload_kind (comma-sep), since, until, exclude_heartbeats, limit, cursor | `get_events()` |

#### Metrics Endpoint

| # | Endpoint | Parameters | Storage Method |
|---|----------|------------|----------------|
| B2.3.7 | `GET /v1/metrics` | project_id, agent_id, environment, group, range, interval, metric, group_by | `get_metrics()` |

#### Cost Endpoints

| # | Endpoint | Parameters | Storage Method |
|---|----------|------------|----------------|
| B2.3.8 | `GET /v1/cost` | project_id, agent_id, environment, group_by (agent/model/agent_model), since, until | `get_cost_summary()` |
| B2.3.9 | `GET /v1/cost/calls` | project_id, agent_id, model, task_id, environment, since, until, sort, limit, cursor | `get_cost_calls()` |
| B2.3.10 | `GET /v1/cost/timeseries` | project_id, agent_id, environment, since, until, interval, split_by_model | `get_cost_timeseries()` |
| B2.3.11 | `GET /v1/llm-calls` | project_id, agent_id, model, task_id, environment, time_range, since, until, sort, limit, cursor | `get_cost_calls()` (same data, adds totals wrapper) |

#### Project Endpoints

| # | Endpoint | Method | Storage Method |
|---|----------|--------|----------------|
| B2.3.12 | `/v1/projects` | GET | `list_projects()` |
| B2.3.13 | `/v1/projects` | POST | `create_project()` |
| B2.3.14 | `/v1/projects/{id}` | GET | `get_project()` |
| B2.3.15 | `/v1/projects/{id}` | PUT | `update_project()` |
| B2.3.16 | `/v1/projects/{id}` | DELETE | Delete events, project_agents, alert_rules, then project |
| B2.3.17 | `/v1/projects/{id}/archive` | POST | `archive_project()` |
| B2.3.18 | `/v1/projects/{id}/unarchive` | POST | Update is_archived=0 |
| B2.3.19 | `/v1/projects/{id}/agents` | GET | `list_agents(project_id=id)` |
| B2.3.20 | `/v1/projects/{id}/agents` | POST | `upsert_project_agent()` |
| B2.3.21 | `/v1/projects/{id}/agents/{aid}` | DELETE | Remove junction row |

#### Alert Endpoints

| # | Endpoint | Method | Storage Method |
|---|----------|--------|----------------|
| B2.3.22 | `/v1/alerts/rules` | GET | `list_alert_rules()` |
| B2.3.23 | `/v1/alerts/rules` | POST | `create_alert_rule()` |
| B2.3.24 | `/v1/alerts/rules/{id}` | PUT | `update_alert_rule()` |
| B2.3.25 | `/v1/alerts/rules/{id}` | DELETE | `delete_alert_rule()` |
| B2.3.26 | `/v1/alerts/history` | GET | `list_alert_history()` |

**Exit criterion:** All 27 endpoints return correct responses. Pagination works. Filters work. Error cases return proper error shapes.

---

### B2.4 — WebSocket Streaming

> **Endpoint:** `ws://localhost:8000/v1/stream?token={api_key}`
> **File:** `backend/websocket.py`

| # | Sub-task | Description |
|---|----------|-------------|
| B2.4.1 | **Connection & auth** | On WebSocket upgrade: extract `token` from query params, hash it, authenticate via `storage.authenticate()`. Reject if invalid (close with 4001). Derive tenant_id. Store connection in a registry keyed by tenant_id. |
| B2.4.2 | **Subscription management** | On `{"action": "subscribe", "channels": [...], "filters": {...}}`: validate channels (must be `"events"` and/or `"agents"`). Store filter config per connection: project_id, environment, group, agent_id, event_types, min_severity. Respond with `{"type": "subscribed", ...}`. New subscribe replaces previous filters entirely. |
| B2.4.3 | **Event broadcasting** | On ingestion (called from B2.2.9): for each subscribed connection, check if event matches filters. If yes, send `{"type": "event.new", "data": {event fields}}`. Include `render_hint` field (null for now, reserved for dashboard optimization). |
| B2.4.4 | **Agent status broadcasting** | After ingestion, if an agent's derived status changed: send `{"type": "agent.status_changed", "data": {agent_id, previous_status, new_status, timestamp, current_task_id, current_project_id, heartbeat_age_seconds}}` to connections subscribed to `"agents"` channel. |
| B2.4.5 | **Stuck detection broadcast** | When an agent crosses the stuck threshold (first time): send `{"type": "agent.stuck", "data": {agent_id, last_heartbeat, stuck_threshold_seconds, current_task_id, current_project_id}}`. Fire once per stuck episode, not repeatedly. |
| B2.4.6 | **Unsubscribe & disconnect** | On `{"action": "unsubscribe", "channels": [...]}`: remove those channels. On connection close: remove from registry. |
| B2.4.7 | **Ping/pong keep-alive** | Server sends ping every 30s. If 3 consecutive pings unanswered, close connection. Client can send `{"action": "ping"}`, server responds `{"type": "pong", "server_time": "..."}`. |
| B2.4.8 | **Connection limits** | Max 5 concurrent WebSocket connections per API key. Reject with 4002 if exceeded. |

**Exit criterion:** Can connect via WebSocket, subscribe to events channel, POST a batch via ingestion, see events stream through the WebSocket in real-time. Agent status changes broadcast correctly.

---

### B2.5 — Alerting Engine

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| B2.5.1 | **Alert evaluator** | Called after each ingestion batch (step 10 of pipeline). Loads enabled rules for the tenant. For each rule, evaluates condition against recent data. Respects cooldown: skip if last alert for this rule was within `cooldown_seconds`. | `alerting.py` |
| B2.5.2 | **Condition: agent_stuck** | Check agent's `last_heartbeat` vs `stuck_threshold_seconds` from condition_config. Fire if threshold crossed. | `alerting.py` |
| B2.5.3 | **Condition: task_failed** | Fire when a `task_failed` event is in the current batch. Optionally: fire only when count exceeds threshold in window. | `alerting.py` |
| B2.5.4 | **Condition: error_rate** | Compute ratio of failed actions to total actions in the configured time window. Fire if exceeds `threshold_percent`. | `alerting.py` |
| B2.5.5 | **Condition: duration_exceeded** | Fire when any `task_completed` event in the batch has `duration_ms` exceeding `threshold_ms`. | `alerting.py` |
| B2.5.6 | **Condition: heartbeat_lost** | Fire when no heartbeat received from configured `agent_id` within window. | `alerting.py` |
| B2.5.7 | **Condition: cost_threshold** | Sum `payload.data.cost` from `llm_call` custom events within `window_hours`. Fire if total exceeds `threshold_usd`. Scope: agent (single agent), project (all agents in project), tenant (all agents). | `alerting.py` |
| B2.5.8 | **Action dispatch** | When alert fires: record in `alert_history` (with condition_snapshot, related_agent_id, related_task_id). Execute actions: `webhook` → POST to configured URL with alert payload. `email` → log for MVP (no email infra yet). | `alerting.py` |
| B2.5.9 | **Cooldown tracking** | Before firing: call `storage.get_last_alert_for_rule()`. If `(now - last_fired_at) < cooldown_seconds`, skip. | `alerting.py` |

**Exit criterion:** Create an `agent_stuck` rule, stop sending heartbeats, verify alert fires. Create a `cost_threshold` rule, send LLM call events exceeding threshold, verify alert fires. Verify cooldown prevents re-firing.

---

## Phase B3: Backend Hardening

| # | Sub-task | Description |
|---|----------|-------------|
| B3.1 | **Rate limiting** | In-memory sliding window counter per API key. Separate limits for ingest (100 req/s) and query (30 req/s). Return 429 with `retry_after_seconds` when exceeded. Rate limit headers on every response. |
| B3.2 | **Request validation hardening** | Validate all query parameter types and ranges. `limit` capped at 200. `range` must be in allowed set. `sort` must be in allowed set per endpoint. Invalid params → 400 with details. |
| B3.3 | **Heartbeat compaction** | Background task (runs hourly): for heartbeats older than 24h, keep one per (agent_id, hour). Prefer heartbeats with non-empty payload. Delete the rest. |
| B3.4 | **Data retention** | Background task (runs daily): delete events older than the tenant's retention window (7d free / 30d pro / 90d enterprise). |
| B3.5 | **Graceful shutdown** | On SIGTERM/SIGINT: close WebSocket connections, flush pending alert evaluations, close storage. |

---

## Summary Table

| Phase | Sub-tasks | Depends On | Produces |
|-------|-----------|------------|----------|
| **B1.2** Storage impl | 13 | shared/storage.py | `storage_json.py` — full JSON file backend |
| **B1.3** Storage tests | 8 | B1.2 | Test suite for StorageBackend protocol |
| **B2.1** App + auth | 5 | B1.2 | FastAPI app with auth middleware |
| **B2.2** Ingestion | 11 | B2.1 | `POST /v1/ingest` — the write path |
| **B2.3** Query endpoints | 26 | B2.1, B2.2 | All GET endpoints |
| **B2.4** WebSocket | 8 | B2.2 | Real-time streaming |
| **B2.5** Alerting | 9 | B2.2 | Alert evaluation + dispatch |
| **B3** Hardening | 5 | B2.* | Rate limits, retention, compaction |

**Total: 85 sub-tasks across 7 sub-phases.**

---

## Key Implementation Notes

### Agent Status Derivation (used in GET /v1/agents and WebSocket broadcasts)

Priority cascade — first match wins:
```
1. stuck:             last_heartbeat IS NULL OR age > stuck_threshold_seconds
2. error:             last_event_type IN (task_failed, action_failed)
3. waiting_approval:  last_event_type = approval_requested
4. processing:        last_event_type IN (task_started, action_started)
5. idle:              everything else
```

### Ingestion Pipeline (10 steps)

```
1. Authenticate (derive tenant_id from API key)
2. Validate envelope (agent_id required, batch size limits)
3. Per-event validation (required fields, event_type enum, field sizes)
3b. Payload convention validation (advisory — warn but don't reject)
4. Expand envelope (merge agent metadata into events, set received_at, severity defaults)
5. Validate project_id (if present, must exist for tenant)
6. Batch INSERT events (dedup by tenant_id + event_id)
7. Update agents cache (upsert agent profile)
8. Update project_agents junction (if task event with project_id)
9. Broadcast to WebSocket subscribers
10. Evaluate alert rules
```

### Cost Query Pattern

All cost queries filter on:
```
event_type = 'custom' AND payload.kind = 'llm_call'
```
Then extract from payload.data: `name`, `model`, `tokens_in`, `tokens_out`, `cost`, `duration_ms`.

### Pipeline Query Pattern

Pipeline assembles 4 sections from custom events for one agent:
- **Queue:** Latest `queue_snapshot` event → depth, items, processing
- **TODOs:** All `todo` events → group by todo_id, take latest action → active if not completed/dismissed
- **Scheduled:** Latest `scheduled` event → items array
- **Issues:** All `issue` events → group by issue_id (or summary hash), take latest action → active if not resolved

---

## Reference Documents

| Spec | File | Relevant Sections |
|------|------|-------------------|
| Event Schema v2 | `docs/1_HiveBoard_Event_Schema_v2.md` | Section 4: stored schema. Section 6: payload kinds. Section 9: severity defaults. Section 10: field limits. |
| Data Model v5 | `docs/2_hiveboard-data-model-spec-v5.md` | Section 3: table DDL. Section 4: payload conventions. Section 5: derived-state SQL. Section 6: ingestion pipeline. Section 7: retention/compaction. |
| API + SDK Spec v3 | `docs/3_hiveboard-api-sdk-spec-v3.md` | Part A (Sections 2–7): all endpoints, authentication, rate limits, WebSocket, alerting. |
| Shared Types | `shared/models.py` | All Pydantic models |
| Shared Constants | `shared/enums.py` | EventType, Severity, PayloadKind, field limits, rate limits |
| StorageBackend Protocol | `shared/storage.py` | 35 async methods — the interface to implement |
| Sample Fixture | `shared/fixtures/sample_batch.json` | 22 events for testing ingestion + queries |

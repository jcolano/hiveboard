# Cross-Team Audit Results: Team 2 Reviews Team 1

> **Auditor:** Team 2 (Clients — SDK + Dashboard)
> **Auditing:** Team 1 Phase 1 — Storage Layer + API Server + WebSocket + Alerting
> **Date:** 2026-02-12
> **Files reviewed:** `backend/app.py`, `backend/storage_json.py`, `backend/middleware.py`, `backend/websocket.py`, `backend/alerting.py`

---

## Scoring Summary

| Severity | Count |
|----------|-------|
| ❌ FAIL | 12 |
| ⚠️ WARN | 10 |
| ✅ PASS | ~50 |

---

## Part 1: Ingestion Endpoint — Does It Accept What the SDK Sends?

### 1.1 Happy Path

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1.1.1 | Sample fixture accepted | ✅ PASS | `app.py:188-400` — accepts `IngestRequest`, returns `{accepted, rejected, errors}` |
| 1.1.2 | All 13 event types accepted | ✅ PASS | `VALID_EVENT_TYPES` built from `EventType` enum (`app.py:174`) |
| 1.1.3 | All 7 payload kinds accepted | ✅ PASS | Payload kinds accepted as generic dict. Advisory validation only for well-known kinds (`app.py:257-267`) |
| 1.1.4 | Agent-level events (null task_id) | ✅ PASS | `task_id` and `project_id` are Optional in `IngestEvent` |
| 1.1.5 | Deduplication | ✅ PASS | `storage_json.py:533-549` — builds `(tenant_id, event_id)` set, skips duplicates |
| 1.1.6 | Envelope expansion | ✅ PASS | `app.py:232` — `agent_id = raw.agent_id or body.envelope.agent_id` |
| 1.1.7 | `received_at` set by server | ✅ PASS | `app.py:201-202,306` — `received_at=now_iso` from server time |
| 1.1.8 | Severity auto-defaults | ✅ PASS | `app.py:279-286` — uses `SEVERITY_DEFAULTS` map. `task_failed` → `"error"`, `heartbeat` → `"debug"` |

### 1.2 Validation — Rejection Behavior

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1.2.1 | Missing `agent_id` in envelope | ⚠️ WARN | Returns 422 `validation_error` (Pydantic), not 400 `invalid_batch`. Functional but wrong error code. |
| 1.2.2 | Missing `event_id` on event | ⚠️ WARN | `event_id: str` is required in Pydantic model — absent field causes 422 before custom validation at `app.py:213-216`. Only null/empty reaches the check. |
| 1.2.3 | Missing `timestamp` on event | ⚠️ WARN | Same issue — `timestamp: str` required in Pydantic, absent field causes 422. |
| 1.2.4 | Invalid `event_type` | ✅ PASS | `app.py:224-229` — correctly rejects with `invalid_event_type` |
| 1.2.5 | Invalid `severity` | ❌ FAIL | **No validation for severity values.** An event with `severity: "critical"` (not in enum) is silently stored. No check exists in the ingestion pipeline. |
| 1.2.6 | Payload over 32KB | ✅ PASS | `app.py:247-254` — rejects with `payload_too_large` |
| 1.2.7 | Batch over 500 events | ✅ PASS | `app.py:195-196` — raises HTTPException(400) |
| 1.2.8 | Invalid `project_id` | ✅ PASS | `app.py:289-296` — checks storage, rejects with `invalid_project_id` |
| 1.2.9 | Partial success (207) | ✅ PASS | `app.py:396` — `status_code = 200 if not errors else 207` |

### 1.3 Advisory Payload Warnings

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1.3.1 | `llm_call` missing `data.model` | ✅ PASS | `PAYLOAD_REQUIRED_FIELDS["llm_call"]` includes `"model"` (`app.py:258-267`) |
| 1.3.2 | `todo` missing `data.action` | ✅ PASS | `PAYLOAD_REQUIRED_FIELDS["todo"]` = `["todo_id", "action"]` (`app.py:180`) |
| 1.3.3 | Unknown `kind` value | ✅ PASS | Unknown kinds skip the validation block |
| 1.3.4 | No `kind` at all | ✅ PASS | `kind = raw.payload.get("kind")` — None skips the block |

### 1.4 Side Effects of Ingestion

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 1.4.1 | Agent profile created | ✅ PASS | `upsert_agent` at `app.py:344-356` |
| 1.4.2 | Agent `last_seen` updated | ✅ PASS | Updated on every event |
| 1.4.3 | Agent `last_heartbeat` updated | ✅ PASS | Only set when `has_heartbeat` is True (`app.py:352`) |
| 1.4.4 | Project-agent junction populated | ✅ PASS | `app.py:358-362` — `upsert_project_agent` called per project_id |
| 1.4.5 | Agent status derived correctly | ⚠️ WARN | `last_event_type` is the last event in the batch (iteration order), not semantically latest. A batch ending with `heartbeat` after `task_started` would set `last_event_type = "heartbeat"` → status `idle` instead of `processing`. |

---

## Part 2: Query Endpoints — Do Responses Match Dashboard Expectations?

### 2.1 `GET /v1/agents`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.1.1 | Response is `{"data": [...]}` | ✅ PASS | `app.py:482` — wrapped in `{"data": [...]}` |
| 2.1.2 | Required fields present | ⚠️ WARN | Uses `derived_status` not `status`. Dashboard must read `derived_status`. All other fields present: `agent_id`, `agent_type`, `last_heartbeat`, `last_seen`, `current_task_id`, `current_project_id`. |
| 2.1.3 | Status values match | ✅ PASS | Returns `idle`, `processing`, `waiting_approval`, `error`, `stuck`. Never returns `offline` — dashboard must infer that. |
| 2.1.4 | Stuck detection | ✅ PASS | `derive_agent_status` in `storage_json.py:72-113` |
| 2.1.5 | `heartbeat_age_seconds` | ✅ PASS | Computed at `app.py:423-425` |
| 2.1.6 | `project_id` filter | ✅ PASS | `storage_json.py:482-491` — filters via project_agents junction |
| 2.1.7 | Sort by attention | ✅ PASS | `app.py:472-478` — stuck first, idle last |
| 2.1.8 | Pipeline enrichment fields | ❌ FAIL | **`stats_1h` never populated.** `_agent_to_summary` (`app.py:420-442`) never sets `stats_1h`. Dashboard always sees zeroed-out 1-hour stats (tasks_completed=0, success_rate=null, total_cost=null, etc.). No `queue_depth` or `issue_count` on agent list either. |

### 2.2 `GET /v1/tasks`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.2.1 | Task object fields | ⚠️ WARN | Uses `derived_status` not `status`. Otherwise fields present. |
| 2.2.2 | Derived status values | ✅ PASS | Priority cascade correct in `storage_json.py:116-136` |
| 2.2.3 | Cost rollup fields | ❌ FAIL | **`TaskSummary` missing `total_tokens_in`, `total_tokens_out`, `llm_call_count`.** Only `total_cost` exists in the model (`models.py:447-461`). Dashboard task table cost columns will be empty. |
| 2.2.4 | Filters work | ❌ FAIL | **`since` and `until` filters not implemented.** Endpoint accepts `agent_id`, `project_id`, `status`, `task_type`, `environment` but not time-range filters. |
| 2.2.5 | Sort options | ✅ PASS | `storage_json.py:772-783` — `newest`, `oldest`, `duration`, `cost` |
| 2.2.6 | Pagination | ✅ PASS | Cursor-based, includes `pagination.cursor` and `pagination.has_more` |

### 2.3 `GET /v1/tasks/{task_id}/timeline`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.3.1 | Events array | ✅ PASS | Chronologically ordered, full fields |
| 2.3.2 | Action tree | ❌ FAIL | **Shape mismatch.** Returns `{action_id, parent_action_id, events: [...], children: [...]}`. Spec expects `{action_id, name, status, duration_ms, children: [...]}`. Dashboard would need to extract action metadata from nested events — `name`, `status`, `duration_ms` are missing as top-level fields on each action node. |
| 2.3.3 | Error chains | ✅ PASS | `app.py:601-614` — links via `parent_event_id` |
| 2.3.4 | Plan overlay | ❌ FAIL | **Completely absent.** `TimelineSummary` model has no `plan` field. The ingestion path never constructs a plan from `plan_created`/`plan_step` events. Dashboard's plan progress bar will have no data. |
| 2.3.5 | LLM call events | ✅ PASS | Present in events array with `payload.kind = "llm_call"` and full data |
| 2.3.6 | Task metadata | ✅ PASS | Includes `task_id`, `agent_id`, `task_type`, `derived_status`, `duration_ms`, `total_cost`, `started_at`, `completed_at` |
| 2.3.7 | 404 on missing task | ✅ PASS | `app.py:551-552` |

### 2.4 `GET /v1/events`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.4.1 | Reverse chronological | ✅ PASS | `storage_json.py:586` |
| 2.4.2 | Filter by `event_type` | ✅ PASS | Comma-separated, `storage_json.py:650-653` |
| 2.4.3 | Filter by `severity` | ✅ PASS | Comma-separated, `storage_json.py:655-658` |
| 2.4.4 | Filter by `agent_id` | ✅ PASS | |
| 2.4.5 | Filter by `payload_kind` | ❌ FAIL | **Not implemented.** Neither the endpoint (`app.py:637-674`) nor the storage method accepts `payload_kind`. Dashboard's filter chips for LLM/issue/plan/queue will not function. |
| 2.4.6 | `exclude_heartbeats` flag | ✅ PASS | Default True, `app.py:649` |
| 2.4.7 | `since` parameter | ✅ PASS | |
| 2.4.8 | Cursor pagination | ✅ PASS | |
| 2.4.9 | Event payload included | ✅ PASS | Full payload in responses |

### 2.5 `GET /v1/agents/{agent_id}/pipeline`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.5.1 | 4 sections present | ✅ PASS | `queue`, `todos`, `scheduled`, `issues` all present |
| 2.5.2 | Queue section | ⚠️ WARN | Raw payload data returned as-is. `snapshot_at` timestamp not injected from the event's timestamp. |
| 2.5.3 | TODOs section | ✅ PASS | Groups by `todo_id`, takes latest action |
| 2.5.4 | TODO lifecycle | ✅ PASS | `completed`/`dismissed` filtered out (`storage_json.py:1202-1222`) |
| 2.5.5 | Scheduled section | ⚠️ WARN | Items use `last_status` (per model), not `status`. Dashboard must match this field name. |
| 2.5.6 | Issues section | ✅ PASS | Groups by `issue_id`, resolved filtered out |
| 2.5.7 | Issue lifecycle | ✅ PASS | |
| 2.5.8 | Empty state | ✅ PASS | Returns empty sections, not 404 |

### 2.6 Cost Endpoints

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.6.1 | `GET /v1/cost` summary | ❌ FAIL | **Missing `total_tokens_in`, `total_tokens_out`.** No `group_by` parameter — breakdown is always split into fixed `by_agent`/`by_model` arrays instead of a single `breakdown` controlled by query param. |
| 2.6.2 | `GET /v1/cost/calls` | ✅ PASS | `LlmCallRecord` has all fields, paginated |
| 2.6.3 | `GET /v1/cost/timeseries` | ❌ FAIL | **Bucket field named `throughput`, not `calls`.** No `split_by_model` support. |
| 2.6.4 | Cost is a number | ✅ PASS | `total_cost: float` in Pydantic |
| 2.6.5 | Zero cost handled | ✅ PASS | Returns `0.0` when no LLM calls |

### 2.7 `GET /v1/metrics`

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.7.1-4 | `group_by` and `metric` params | ❌ FAIL | **Neither parameter implemented.** Returns a fixed `MetricsResponse` with `summary` + `timeseries`. The spec expects: without `group_by` → scalar; with `group_by=model` → array of `{group, value}`; with `group_by=agent_id` → array by agent. Response shape is fundamentally different. |
| 2.7.5 | `range` parameter | ✅ PASS | `1h`, `6h`, `24h`, `7d`, `30d` work |

### 2.8 Project Endpoints

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 2.8.1 | `GET /v1/projects` | ✅ PASS | Returns projects with correct fields |
| 2.8.2 | `POST /v1/projects` | ✅ PASS | Creates and returns |
| 2.8.3 | Default project protected | ⚠️ WARN | **No deletion protection.** `DELETE` calls `archive_project` without checking if it's the default project. |
| 2.8.4 | Archived projects hidden | ✅ PASS | `include_archived` flag works |

---

## Part 3: WebSocket

### 3.1 Connection Setup

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 3.1.1 | Connection URL | ✅ PASS | `ws://host/v1/stream?token={api_key}` |
| 3.1.2 | Auth on connect | ✅ PASS | Invalid key → close 4001 |
| 3.1.3 | Connection limit | ✅ PASS | 6th connection rejected with 4002 |
| 3.1.4 | Subscribe accepted | ✅ PASS | Returns `{"type": "subscribed", ...}` |

### 3.2 Event Broadcasting

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 3.2.1 | Events arrive on ingest | ✅ PASS | `app.py:366-368` |
| 3.2.2 | Event data shape | ✅ PASS | Serialized with `model_dump(mode="json")` |
| 3.2.3 | Filter by project_id | ✅ PASS | `websocket.py:70-71` |
| 3.2.4 | Filter by event_type | ✅ PASS | `websocket.py:78-79` |
| 3.2.5 | Latency | ✅ PASS | Direct broadcast after ingestion |

### 3.3 Agent Status Broadcasting

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 3.3.1 | Status change broadcast | ❌ FAIL | **`agent.status_changed` messages never sent.** `broadcast_agent_status_change` exists in `websocket.py:189-214` with the correct message shape, but **is never called** from `app.py`. The ingestion path only calls `broadcast_agent_stuck` and `clear_stuck`. Agent status changes (idle→processing, processing→idle, etc.) are never broadcast. |
| 3.3.2 | Status change data fields | ❌ (moot) | Method has correct fields but is never invoked |
| 3.3.3 | Stuck broadcast | ✅ PASS | `app.py:376` calls `broadcast_agent_stuck` |
| 3.3.4 | Stuck fires once | ✅ PASS | `websocket.py:226-229` — `_stuck_fired` dict prevents repeats |
| 3.3.5 | Recovery from stuck | ⚠️ (partial) | `clear_stuck` resets the flag, but no `agent.status_changed` message sent on recovery |
| 3.3.6 | No broadcast when unchanged | N/A | Since status changes are never broadcast |

---

## Part 4: Derived State Logic

### 4.1 Agent Status Derivation

| # | Scenario | Expected | Result | Notes |
|---|----------|----------|--------|-------|
| 4.1.1 | No heartbeat ever | `stuck` | ✅ PASS | `storage_json.py:88-89` |
| 4.1.2 | Recent heartbeat, idle | `idle` | ✅ PASS | Falls through to line 113 |
| 4.1.3 | Heartbeat stale | `stuck` | ✅ PASS | Lines 90-91 |
| 4.1.4 | Active task_started | `processing` | ✅ PASS | Lines 106-110 |
| 4.1.5 | Last event task_failed | `error` | ✅ PASS | Lines 95-99 |
| 4.1.6 | Last event approval_requested | `waiting_approval` | ✅ PASS | Lines 102-103 |
| 4.1.7 | Processing but heartbeat stale | `stuck` | ✅ PASS | Stuck check runs first |
| 4.1.8 | Completed then idle | `idle` | ✅ PASS | |

### 4.2 Task Status Derivation

| # | Scenario | Expected | Result | Notes |
|---|----------|----------|--------|-------|
| 4.2.1 | Only task_started | `processing` | ✅ PASS | |
| 4.2.2 | + task_completed | `completed` | ✅ PASS | |
| 4.2.3 | + task_failed | `failed` | ✅ PASS | |
| 4.2.4 | + escalated | `escalated` | ✅ PASS | |
| 4.2.5 | + approval_requested | `waiting` | ✅ PASS | |
| 4.2.6 | Full approval flow → completed | `completed` | ✅ PASS | |
| 4.2.7 | Failed → retry → completed | `completed` | ✅ PASS | `task_completed` checked first |

---

## Part 5: Error Handling

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 5.1 | Standard error shape | ✅ PASS | `{"error", "message", "status"}` — `details` included only on 422 and 429 |
| 5.2 | 401 on bad API key | ✅ PASS | `middleware.py:47-54` — `"authentication_failed"` |
| 5.3 | 403 on read key + POST | ✅ PASS | `middleware.py:57-65` — `"insufficient_permissions"` |
| 5.4 | 404 on missing resource | ⚠️ WARN | Error code is English string `"Agent not found"`, not machine code `"not_found"`. |
| 5.5 | 429 with retry info | ✅ PASS | `middleware.py:121-134` — includes `retry_after_seconds` in details |
| 5.6 | Rate limit headers | ✅ PASS | `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` present |
| 5.7 | Validation error detail | ⚠️ WARN | 422 details is stringified exception, not structured field-level info |

---

## Part 6: Cross-Cutting Concerns

| # | Check | Result | Notes |
|---|-------|--------|-------|
| 6.1 | CORS headers | ✅ PASS | `app.py:109-115` — allows all origins |
| 6.2 | Timestamp consistency | ⚠️ WARN | Server generates `+00:00` suffix, client timestamps stored as-is (may use `Z`). Responses may mix formats. |
| 6.3 | Tenant isolation | ✅ PASS | All queries scoped by `tenant_id` from API key |
| 6.4 | `hb_test_` key isolation | ❌ FAIL | **No test/live isolation.** `key_type` is set on `request.state` but never used for data filtering. Events from `hb_test_*` keys are visible via `hb_live_*` keys. |
| 6.5 | UTF-8 handling | ✅ PASS | JSON files written with `encoding="utf-8"` |
| 6.6 | Large payload handling | ✅ PASS | Under 32KB accepted, stored, returned as-is |
| 6.7 | Concurrent ingestion | ✅ PASS | `asyncio.Lock` per table with dedup inside lock |

---

## Critical Issues — Must Fix Before Integration

| # | Section | Finding | Suggested Fix |
|---|---------|---------|---------------|
| F1 | 1.2.5 | **Invalid severity not rejected.** `severity: "critical"` silently stored. | Add severity enum validation in ingestion pipeline. |
| F2 | 2.1.8 | **`stats_1h` never populated.** Dashboard 1-hour stats always zero. | Compute rolling 1h aggregates in `_agent_to_summary`. |
| F3 | 2.2.3 | **`TaskSummary` missing `total_tokens_in`, `total_tokens_out`, `llm_call_count`.** | Add fields to model, compute from `llm_call` events in task. |
| F4 | 2.2.4 | **`GET /v1/tasks` missing `since`/`until` filters.** | Add parameters to endpoint and storage query. |
| F5 | 2.3.2 | **Action tree shape mismatch.** Returns `{events: [...]}` not `{name, status, duration_ms}`. | Extract action metadata to top-level fields on each node. |
| F6 | 2.3.4 | **Plan overlay completely absent.** No `plan` in timeline response. | Implement plan construction from `plan_created`/`plan_step` events per spec Section 4.4. |
| F7 | 2.4.5 | **No `payload_kind` filter on `GET /v1/events`.** | Add `payload_kind` parameter that filters `custom` events by `payload.kind`. |
| F8 | 2.6.1 | **`GET /v1/cost` missing token totals and `group_by`.** | Add `total_tokens_in/out` to `CostSummary`. Add `group_by` parameter. |
| F9 | 2.6.3 | **Cost timeseries: `throughput` instead of `calls`, no `split_by_model`.** | Rename field. Add `split_by_model` parameter. |
| F10 | 2.7.1-4 | **`GET /v1/metrics` missing `group_by`/`metric` params.** Response shape fundamentally different. | Implement per spec Section 4.6 grouped response format. |
| F11 | 3.3.1-2 | **`agent.status_changed` never broadcast.** Method exists but never called. | Compute previous status in ingestion, call `broadcast_agent_status_change` on change. |
| F12 | 6.4 | **No test/live data isolation.** `hb_test_*` events visible via `hb_live_*` keys. | Add `key_type` scoping to storage queries, or use separate namespace. |

---

## Warnings — Fix Before Production, OK for Integration

| # | Section | Finding | Suggested Fix |
|---|---------|---------|---------------|
| W1 | 1.2.1 | Missing envelope `agent_id` → 422 not 400 `invalid_batch` | Add pre-Pydantic envelope check or custom exception handler |
| W2 | 1.2.2-3 | Missing `event_id`/`timestamp` → 422 from Pydantic | Accept Optional fields in Pydantic, validate in endpoint code |
| W3 | 1.4.5 | `last_event_type` set from batch iteration order, not semantic order | Track last event type per agent across all events, not just last in batch |
| W4 | 2.1.2 | Field `derived_status` not `status` | Dashboard can adapt, but consider aliasing for cleaner API |
| W5 | 2.5.2 | Queue data raw; `snapshot_at` not injected | Inject event timestamp as `snapshot_at` in pipeline response |
| W6 | 2.5.5 | Scheduled uses `last_status` not `status` | Document for dashboard or alias |
| W7 | 2.8.3 | Default project not protected from deletion | Add check before archiving |
| W8 | 5.4 | 404 error codes are English strings, not machine codes | Use structured codes: `"not_found"`, `"agent_not_found"` |
| W9 | 5.7 | 422 details is stringified exception | Return structured field-level info |
| W10 | 6.2 | Timestamps may mix `Z` and `+00:00` formats | Normalize all output to `Z` suffix |

---

## What Works Well

- **Ingestion happy path is solid** — envelope expansion, severity defaults, deduplication, payload advisory warnings, partial success (207) all work correctly.
- **Agent status derivation logic is correct** for all 8 test scenarios.
- **Task status derivation** implements the priority cascade correctly.
- **WebSocket infrastructure** — auth, connection limits, subscriptions, stuck detection, event broadcasting all functional.
- **CORS, rate limiting, tenant isolation** all work.
- **Storage layer** — well-structured with per-table locking and dedup inside lock.

---

## Recommendation

The 12 critical issues must be addressed before SDK↔Backend integration testing. The most impactful are:

1. **F6 (plan overlay)** — this is a core product feature with no data path
2. **F2 (stats_1h)** — dashboard summary bar will show all zeros
3. **F11 (status changes)** — live dashboard updates won't work
4. **F7 (payload_kind filter)** — activity stream filtering broken

The warnings are lower priority and can be addressed iteratively.

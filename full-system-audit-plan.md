# Full System Audit — Plan for Team 1

> **Auditor:** Team 1 (Backend)
> **Scope:** Everything built so far — Backend, SDK, Simulator, Shared types
> **Purpose:** Pre-integration comprehensive audit after all fixes are merged
> **Reference specs:** Event Schema v2, Data Model v5, API+SDK Spec v3, Product Spec, Dashboard v3 prototype
> **Audit date:** 2026-02-12

---

## Context

Both teams have completed their implementations and cross-team audits:

- **Team 1:** Backend — 99 tests, all 12 critical + 10 warning fixes applied
- **Team 2:** SDK — 53 tests, 5 warnings noted (0 critical). Team 2 reports C2 (Dashboard) is also complete.
- **Total backend+SDK:** 152 tests passing, zero regressions

This is a **full-system audit** — not a contract-surface audit. Team 1 reviews everything: their own post-fix backend, Team 2's SDK, the dashboard, the simulator, shared types, and the integration seams between them. The goal is to establish a verified clean baseline across the entire system.

---

## Audit Structure

| Part | What | Checks | Purpose |
|---|---|---|---|
| **Part 1** | Shared Foundation | ~15 | Verify shared types, enums, models, and storage protocol are consistent with all three specs |
| **Part 2** | Backend — Spec Compliance | ~45 | Every API endpoint, WebSocket message, and alerting behavior against API Spec v3 |
| **Part 3** | Backend — Fix Verification | ~25 | Verify all 12 FAILs and 10 WARNs are actually fixed, not just claimed |
| **Part 4** | Backend — Internal Quality | ~15 | Test coverage, error handling, edge cases, concurrency |
| **Part 5** | SDK — Spec Compliance | ~30 | Every SDK method, event shape, and transport behavior against SDK Spec (Part B) |
| **Part 6** | SDK — Fix Verification | ~5 | Verify Team 2's 5 warnings are addressed |
| **Part 7** | Dashboard — Implementation Verification | ~40 | Verify C2.1–C2.5 deliverables against Team 2's implementation plan and dashboard v3 prototype |
| **Part 8** | Simulator — Coverage & Correctness | ~15 | All 13 event types, all 7 payload kinds, realistic behavior |
| **Part 9** | End-to-End Integration | ~25 | Run simulator against live backend, verify full pipeline including dashboard rendering |
| **Total** | | **~215 checks** | |

---

## Part 1: Shared Foundation (15 checks)

Verify that `shared/enums.py`, `shared/models.py`, and `shared/storage.py` are the single source of truth, and that both teams use them consistently.

### 1.1 Enums & Constants

| # | Check | Reference |
|---|-------|-----------|
| 1.1.1 | `EventType` enum contains exactly 13 values matching Event Schema v2, Section 5 | Event Schema v2 §5 |
| 1.1.2 | `Severity` enum contains exactly 4 values: `debug`, `info`, `warn`, `error` | Event Schema v2 §4.6 |
| 1.1.3 | `PayloadKind` enum contains exactly 7 values: `llm_call`, `plan_created`, `plan_step`, `queue_snapshot`, `todo`, `scheduled`, `issue` | Event Schema v2 §6 |
| 1.1.4 | `SEVERITY_DEFAULTS` mapping matches Event Schema v2, Section 9 (e.g., `task_failed` → `error`, `heartbeat` → `debug`) | Event Schema v2 §9 |
| 1.1.5 | `VALID_SEVERITIES` set matches `Severity` enum values | Fix F1 |
| 1.1.6 | `MAX_BATCH_EVENTS = 500` | API Spec v3 §3.1 |
| 1.1.7 | Field size limits defined (agent_id ≤ 256, payload ≤ 32KB, etc.) | Event Schema v2 §10 |

### 1.2 Pydantic Models

| # | Check | Reference |
|---|-------|-----------|
| 1.2.1 | `BatchEnvelope` fields match API Spec v3 §3.1 envelope definition | API Spec v3 §3.1 |
| 1.2.2 | `IngestEvent` required fields: `event_id`, `timestamp`, `event_type` — all others optional | Event Schema v2 §4 |
| 1.2.3 | `TaskSummary` includes `total_tokens_in`, `total_tokens_out`, `llm_call_count`, `total_cost` | Fix F3 |
| 1.2.4 | `CostSummary` includes `total_tokens_in`, `total_tokens_out` | Fix F8 |
| 1.2.5 | `CostTimeBucket` uses `call_count` (not `throughput`), includes `tokens_in`, `tokens_out` | Fix F9 |
| 1.2.6 | `MetricsResponse` includes `groups: list[dict] | None` | Fix F10 |
| 1.2.7 | `TimelineSummary` includes `plan: dict | None` | Fix F6 |
| 1.2.8 | `AgentRecord` includes `previous_status: str | None` | Fix F11 |

### 1.3 Storage Protocol

| # | Check | Reference |
|---|-------|-----------|
| 1.3.1 | `StorageBackend` protocol defines all methods called by `app.py` — no missing methods | Internal consistency |
| 1.3.2 | `JsonStorageBackend` implements every method in the protocol | Internal consistency |
| 1.3.3 | All new method signatures from fixes (e.g., `compute_agent_stats_1h`, `key_type` on `insert_events`) are in the protocol | Fixes F2, F12 |

---

## Part 2: Backend — Spec Compliance (45 checks)

Verify every API endpoint against API Spec v3 sections 3–7.

### 2.1 Ingestion Endpoint (`POST /v1/ingest`)

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.1.1 | Accepts `BatchEnvelope` + `events[]` body, returns `{accepted, rejected, warnings, errors}` | §3.1 |
| 2.1.2 | 10-step pipeline: auth → validate envelope → per-event validate → payload advisory → expand → project validate → insert → upsert agent → junction → broadcast → alerts | §3.1 steps 1-7 |
| 2.1.3 | Deduplicates on `(tenant_id, event_id)` — silent skip, counted as accepted | §3.2 |
| 2.1.4 | `received_at` set by server, not client | §3.1 step 3e |
| 2.1.5 | Severity auto-defaults applied per Event Schema v2 §9 | §3.1 step 3f |
| 2.1.6 | Invalid severity → advisory warning, falls back to auto-default (not rejection) | Fix F1 |
| 2.1.7 | Payload advisory validation for all 7 well-known kinds (warns on missing required `data` fields) | §3.1 step 3h |
| 2.1.8 | Batch over 500 events → 400 | §3.1 |
| 2.1.9 | Payload over 32KB → per-event rejection with `payload_too_large` | Event Schema v2 §10 |
| 2.1.10 | Partial success → 207 with `errors[]` | §3.1 |
| 2.1.11 | `agent_registered` events upsert agent profile | §3.3 |
| 2.1.12 | Events with `project_id` populate `project_agents` junction | §3.1 step 5 |
| 2.1.13 | Batch events sorted by timestamp before extracting `last_event_type` | Fix W3 |
| 2.1.14 | Events tagged with `key_type` for test/live isolation | Fix F12 |

### 2.2 Agent Endpoints

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.2.1 | `GET /v1/agents` returns `{"data": [...]}` with all required fields | §4.1 |
| 2.2.2 | Agent status derivation cascade: stuck > error > waiting_approval > processing > idle | Data Model v5 §5.7, §6.1 |
| 2.2.3 | `stats_1h` populated with rolling 1-hour aggregates (tasks_completed, success_rate, total_cost, throughput) | Fix F2 |
| 2.2.4 | `heartbeat_age_seconds` computed at query time | §4.1 |
| 2.2.5 | `project_id` filter works | §4.1 |
| 2.2.6 | Sort by attention priority (stuck first, idle last) | §4.1 |
| 2.2.7 | `GET /v1/agents/{agent_id}` returns single agent or 404 | §4.2 |
| 2.2.8 | 404 error uses structured `{"error": "not_found", ...}` format | Fix W8 |

### 2.3 Task Endpoints

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.3.1 | `GET /v1/tasks` returns paginated tasks with cursor | §4.3 |
| 2.3.2 | Task includes `total_tokens_in`, `total_tokens_out`, `llm_call_count` | Fix F3 |
| 2.3.3 | Filters: `agent_id`, `project_id`, `status`, `task_type`, `environment`, `since`, `until` | §4.3, Fix F4 |
| 2.3.4 | Sort options: `newest`, `oldest`, `duration`, `cost` | §4.3 |
| 2.3.5 | Task status derivation cascade correct | Data Model v5 §5.3 |

### 2.4 Timeline Endpoint

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.4.1 | `GET /v1/tasks/{task_id}/timeline` returns events + action tree | §4.4 |
| 2.4.2 | Action tree nodes have top-level `name`, `status`, `duration_ms` fields | §4.4, Fix F5 |
| 2.4.3 | `plan` field present with `goal`, `steps[]`, `progress.completed/total` | §4.4, Fix F6 |
| 2.4.4 | Error chains linked via `parent_event_id` | §4.4 |
| 2.4.5 | 404 on missing task_id | §4.4 |

### 2.5 Events Endpoint

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.5.1 | `GET /v1/events` returns reverse chronological, paginated | §4.5 |
| 2.5.2 | Filters: `event_type`, `severity`, `agent_id`, `task_id`, `project_id`, `payload_kind` | §4.5, Fix F7 |
| 2.5.3 | `exclude_heartbeats` defaults to `true` | §4.5 |
| 2.5.4 | `since` parameter works | §4.5 |

### 2.6 Cost Endpoints

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.6.1 | `GET /v1/cost` returns `total_cost`, `total_tokens_in`, `total_tokens_out`, `by_agent`, `by_model` | §4.8, Fix F8 |
| 2.6.2 | `GET /v1/cost/timeseries` uses `CostTimeBucket` with `call_count`, `tokens_in`, `tokens_out` | §4.8, Fix F9 |
| 2.6.3 | `GET /v1/cost/calls` returns individual LLM call records | §4.8 |

### 2.7 Metrics Endpoint

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.7.1 | `GET /v1/metrics` returns summary + timeseries without `group_by` | §4.6 |
| 2.7.2 | With `group_by=agent` → returns `groups[]` array by agent | §4.6, Fix F10 |
| 2.7.3 | With `group_by=model` → returns `groups[]` array by model | §4.6, Fix F10 |
| 2.7.4 | `range` parameter: `1h`, `6h`, `24h`, `7d`, `30d` | §4.6 |

### 2.8 Pipeline Endpoint

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.8.1 | `GET /v1/agents/{agent_id}/pipeline` returns `queue`, `todos`, `scheduled`, `issues` | §4.7 |
| 2.8.2 | Queue section includes `snapshot_at` | Fix W5 |
| 2.8.3 | TODOs: groups by `todo_id`, filters `completed`/`dismissed` | Data Model v5 §5.12.2 |
| 2.8.4 | Issues: groups by `issue_id`, filters resolved | Data Model v5 §5.12.4 |

### 2.9 Project Endpoints

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.9.1 | CRUD: `GET`, `POST`, `PUT`, `DELETE`, archive/unarchive for `/v1/projects` | §4.13–4.18 |
| 2.9.2 | Default project cannot be deleted → 400 `cannot_delete_default` | §4.16, Fix W7 |
| 2.9.3 | `GET /v1/projects/{id}/agents` — list, add, remove agents | §4.19–4.21 |

### 2.10 Alert Endpoints

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.10.1 | `GET /v1/alerts/rules` returns rules, filterable by `project_id` | §6.1 |
| 2.10.2 | `POST /v1/alerts/rules` creates rule with 6 condition types | §6.2 |
| 2.10.3 | `PUT /v1/alerts/rules/{id}` updates rule | §6.3 |
| 2.10.4 | `DELETE /v1/alerts/rules/{id}` → 204 | §6.4 |
| 2.10.5 | `GET /v1/alerts/history` paginated, filterable by `rule_id`, `since`, `until` | §6.5 |
| 2.10.6 | Alert evaluator fires on ingest for: `agent_stuck`, `task_failed`, `error_rate`, `duration_exceeded`, `heartbeat_lost`, `cost_threshold` | §6.2 |
| 2.10.7 | Cooldown logic prevents re-firing within `cooldown_seconds` | §6.2 |

### 2.11 WebSocket

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.11.1 | `ws://host/v1/stream?token={api_key}` — auth on connect, 4001 on invalid key | §5.1 |
| 2.11.2 | `subscribe` message with `channels` + `filters` → `subscribed` confirmation | §5.2 |
| 2.11.3 | `event.new` messages broadcast on ingest with correct data shape | §5.3 |
| 2.11.4 | `agent.status_changed` messages broadcast when status transitions | §5.3, Fix F11 |
| 2.11.5 | `agent.stuck` messages broadcast once when threshold crossed | §5.3 |
| 2.11.6 | Filters: `project_id`, `environment`, `agent_id`, `event_types`, `min_severity` | §5.2 |
| 2.11.7 | Ping/pong keep-alive (server → client every 30s, 3 missed → close) | §5.4 |
| 2.11.8 | Connection limit: 5 per API key | §2.5 |
| 2.11.9 | `unsubscribe` message works | §5.5 |

### 2.12 Cross-Cutting

| # | Check | Spec Reference |
|---|-------|----------------|
| 2.12.1 | Auth: `hb_live_*` = read/write, `hb_test_*` = read/write (isolated), `hb_read_*` = read-only | §2.2 |
| 2.12.2 | Test/live isolation: `hb_test_*` events not visible via `hb_live_*` keys | Fix F12 |
| 2.12.3 | Rate limit headers: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` | §2.5 |
| 2.12.4 | 429 response includes `retry_after_seconds` in details | §2.5 |
| 2.12.5 | CORS allows all origins | §2 (implied by dashboard hosting) |
| 2.12.6 | All error responses use structured format: `{error, message, status, details}` | §2.4, Fix W8 |
| 2.12.7 | Validation errors return structured field-level details | Fix W1/W2/W9 |
| 2.12.8 | All output timestamps normalized to `Z` suffix (not `+00:00`) | Fix W10 |

---

## Part 3: Backend — Fix Verification (25 checks)

Re-verify every fix from the Phase 2 audit fix report. For each, run the specific test AND manually confirm the behavior.

### 3.1 Critical Fixes

| # | Fix | Verification Method |
|---|-----|---------------------|
| 3.1.1 | **F1:** Severity validation advisory warning | Send event with `severity: "critical"` → verify warning in response, event stored with auto-default |
| 3.1.2 | **F2:** `stats_1h` populated | Ingest events for an agent within the last hour → verify `stats_1h` fields are non-zero on `GET /v1/agents` |
| 3.1.3 | **F3:** Task token counts | Ingest LLM calls within a task → verify `total_tokens_in`, `total_tokens_out`, `llm_call_count` on `GET /v1/tasks` |
| 3.1.4 | **F4:** Tasks `since`/`until` | Query tasks with time range → verify filtering works |
| 3.1.5 | **F5:** Action tree shape | Create nested actions → verify `name`, `status`, `duration_ms` on each tree node in timeline |
| 3.1.6 | **F6:** Plan overlay | Create plan + plan_steps → verify `plan` field in timeline with `goal`, `steps`, `progress` |
| 3.1.7 | **F7:** `payload_kind` filter | Query events with `payload_kind=llm_call` → verify only LLM call events returned |
| 3.1.8 | **F8:** Cost token totals | Ingest LLM calls → verify `total_tokens_in`, `total_tokens_out` on `GET /v1/cost` |
| 3.1.9 | **F9:** Cost timeseries `call_count` | Query cost timeseries → verify field is `call_count` not `throughput`, includes `tokens_in`/`tokens_out` |
| 3.1.10 | **F10:** Metrics `group_by` | Query metrics with `group_by=agent` → verify `groups[]` array in response |
| 3.1.11 | **F11:** Status change broadcast | Connect WebSocket, subscribe to `agents` channel. Ingest events that change agent status. Verify `agent.status_changed` message received with `previous_status` and `new_status` |
| 3.1.12 | **F12:** Test/live isolation | Ingest events with `hb_test_*` key. Query with `hb_live_*` key. Verify test events not visible |

### 3.2 Warning Fixes

| # | Fix | Verification Method |
|---|-----|---------------------|
| 3.2.1 | **W1/W2/W9:** Validation error format | Send malformed request → verify 400 (not 422) with `{error: "validation_error", details: {fields: [...]}}` |
| 3.2.2 | **W3:** Batch event ordering | Send batch with events in reverse timestamp order → verify `last_event_type` reflects chronologically latest |
| 3.2.3 | **W4:** `derived_status` naming | Confirm field name is `derived_status` (not `status`) in agent responses. Document for C2 team |
| 3.2.4 | **W5:** Queue `snapshot_at` | Query pipeline → verify `snapshot_at` present in queue section |
| 3.2.5 | **W6:** Scheduled `last_status` | Confirm field is `last_status`. Document for C2 team |
| 3.2.6 | **W7:** Default project protected | Attempt `DELETE /v1/projects/default` → verify 400 `cannot_delete_default` |
| 3.2.7 | **W8:** 404 machine codes | Request non-existent agent → verify `{error: "not_found"}` (not English string) |
| 3.2.8 | **W10:** Timestamp normalization | Query agents/events → verify all timestamps end with `Z` (no `+00:00`) |

### 3.3 Regression Check

| # | Check | Method |
|---|-------|--------|
| 3.3.1 | All 99 backend tests pass | `python -m pytest tests/ -v` |
| 3.3.2 | All 53 SDK tests pass | `python -m pytest tests/ -v` (from SDK directory) |
| 3.3.3 | No new warnings in test output | Check for deprecation warnings, unclosed resources |
| 3.3.4 | Server starts cleanly | `uvicorn backend.app:app` — no startup errors |
| 3.3.5 | No import errors across all modules | `python -c "import backend.app; import sdk.hiveloop"` |

---

## Part 4: Backend — Internal Quality (15 checks)

Beyond spec compliance, check the implementation quality.

### 4.1 Test Coverage

| # | Check | Target |
|---|-------|--------|
| 4.1.1 | Every API endpoint has at least one dedicated test | 27 endpoints → ≥27 tests |
| 4.1.2 | Every fix (F1-F12, W1-W10) has a dedicated test | 22 fixes → 22 tests minimum |
| 4.1.3 | Storage layer coverage: all 35+ methods tested | Check test_storage.py |
| 4.1.4 | WebSocket has at least basic connection + message tests | Check for WebSocket test file |
| 4.1.5 | Alerting has tests for each condition type (6 types) | Check test coverage |

### 4.2 Error Handling & Edge Cases

| # | Check | Description |
|---|-------|-------------|
| 4.2.1 | Empty batch (0 events) → graceful response (not crash) | |
| 4.2.2 | Unicode in agent_id, task_id, payload → stored and returned correctly | |
| 4.2.3 | Very large payload (just under 32KB) → accepted | |
| 4.2.4 | Concurrent ingestion from multiple agents → no data corruption | |
| 4.2.5 | Query with no matching data → empty results (not error) | |

### 4.3 Performance & Resource Management

| # | Check | Description |
|---|-------|-------------|
| 4.3.1 | JSON storage files don't grow unbounded during test suite | Check temp dir cleanup |
| 4.3.2 | WebSocket connections are properly cleaned up on disconnect | |
| 4.3.3 | `asyncio.Lock` usage is consistent — no deadlock potential | Inspect lock patterns |
| 4.3.4 | Background tasks (stuck detection, alert evaluation) don't leak | |
| 4.3.5 | `stats_1h` computation doesn't become a bottleneck with large event volumes | Check query efficiency |

---

## Part 5: SDK — Spec Compliance (30 checks)

Verify SDK implementation against API+SDK Spec v3, Part B (Sections 8–15).

### 5.1 Module API

| # | Check | Spec Reference |
|---|-------|----------------|
| 5.1.1 | `hiveloop.init(api_key, ...)` creates singleton, validates `hb_` prefix | §8.1 |
| 5.1.2 | Subsequent `init()` calls return existing instance + log warning | §8.1 |
| 5.1.3 | `hiveloop.shutdown(timeout)` drains queue, closes transport | §8.1 |
| 5.1.4 | `hiveloop.reset()` shuts down + clears singleton | §8.1 |
| 5.1.5 | `hiveloop.flush()` triggers immediate flush | §8.1 |

### 5.2 Agent & Task Lifecycle

| # | Check | Spec Reference |
|---|-------|----------------|
| 5.2.1 | `hb.agent(agent_id, type, version, framework, ...)` creates/retrieves agent | §8.2 |
| 5.2.2 | Agent registration emits `agent_registered` event with metadata | §8.2 |
| 5.2.3 | Idempotent: same `agent_id` returns existing, updates metadata | §8.2 |
| 5.2.4 | `agent.task(task_id, project, type, ...)` context manager: `task_started` → `task_completed`/`task_failed` | §8.3 |
| 5.2.5 | Exception in task context → `task_failed` event + re-raise | §8.3 |
| 5.2.6 | Non-CM API: `agent.start_task()` → `task.complete()` / `task.fail()` | §8.3 |
| 5.2.7 | `task.event()` (task-scoped) and `agent.event()` (agent-level) | §9.3 |

### 5.3 Decorator & Nesting

| # | Check | Spec Reference |
|---|-------|----------------|
| 5.3.1 | `@agent.track(action_name)` works with sync functions | §8.4 |
| 5.3.2 | `@agent.track(action_name)` works with async functions | §8.4 |
| 5.3.3 | Nested `@track` produces correct `parent_action_id` chains (verify 3+ levels) | §8.4 |
| 5.3.4 | `agent.track_context(name)` context manager alternative | §8.4 |
| 5.3.5 | Auto-populated payload: `action_name`, `function` (fully qualified), exception info on failure | §8.4 |
| 5.3.6 | Exception propagation: `@track` never swallows exceptions | §8.4 |

### 5.4 Convenience Methods (All 9)

| # | Method | Payload Kind | Check |
|---|--------|-------------|-------|
| 5.4.1 | `task.llm_call()` | `llm_call` | `data` has `name`, `model` (required), optional `tokens_in/out`, `cost`, `duration_ms`, `prompt_preview`, `response_preview`. Auto-summary format correct |
| 5.4.2 | `agent.llm_call()` | `llm_call` | Agent-level (no task context required) |
| 5.4.3 | `task.plan()` | `plan_created` | `data` has `goal`, `steps`, `revision`. Stores `total_steps` for plan_step |
| 5.4.4 | `task.plan_step()` | `plan_step` | `data` has `step_index`, `total_steps`, `action`. Auto-summary correct |
| 5.4.5 | `agent.queue_snapshot()` | `queue_snapshot` | `data` has `depth` (required), optional `oldest_age_seconds`, `items`, `processing` |
| 5.4.6 | `agent.todo()` | `todo` | `data` has `todo_id`, `action`. Lifecycle: created/completed/failed/dismissed/deferred |
| 5.4.7 | `agent.scheduled()` | `scheduled` | `data` has `items` array |
| 5.4.8 | `agent.report_issue()` | `issue` | `data.action = "reported"`, includes `severity` |
| 5.4.9 | `agent.resolve_issue()` | `issue` | `data.action = "resolved"` |

### 5.5 Heartbeat

| # | Check | Spec Reference |
|---|-------|----------------|
| 5.5.1 | Background heartbeat thread emits `heartbeat` events at `heartbeat_interval` | §8.2 |
| 5.5.2 | `heartbeat_payload` callback: return value used as payload. Exceptions caught + logged | §9.4 |
| 5.5.3 | `queue_provider` callback: return value used for `queue_snapshot` event. Exceptions caught + logged | §9.8 |

### 5.6 Transport

| # | Check | Spec Reference |
|---|-------|----------------|
| 5.6.1 | Thread-safe event queue with `deque(maxlen=...)` + Lock | §8.5 |
| 5.6.2 | Batch envelope: one POST per agent per flush. Envelope fields correct | §3.1 |
| 5.6.3 | Retry: 5xx → exponential backoff (1s, 2s, 4s, 8s, 16s), cap 60s. 429 → `retry_after_seconds`. 400 → drop | §8.5 |
| 5.6.4 | Graceful shutdown: `atexit`, final synchronous drain | §8.5 |
| 5.6.5 | **Transport never raises exceptions to the caller** | §8.5 critical invariant |

### 5.7 Event Construction

| # | Check | Spec Reference |
|---|-------|----------------|
| 5.7.1 | `event_id`: UUID4, lowercase with hyphens | Event Schema v2 §4.1 |
| 5.7.2 | `timestamp`: UTC ISO 8601 with milliseconds + `Z` suffix | Event Schema v2 §4.2 |
| 5.7.3 | `None` values stripped (except `event_id`, `timestamp`, `event_type`) | §8 convention |
| 5.7.4 | All payload envelopes: `{kind, summary, data, tags}` | Event Schema v2 §6.1 |

---

## Part 6: SDK — Fix Verification (5 checks)

Verify the 5 warnings from Team 1's initial audit of Team 2.

| # | Warning | Verification |
|---|---------|-------------|
| 6.1 | **W1:** No client-side field size validation | Check if addressed, or confirm backend-enforced (acceptable for MVP) |
| 6.2 | **W2:** `plan_created` missing `data.goal` | Verify `data.goal` is now set in `task.plan()` |
| 6.3 | **W3:** Simulator missing `action_failed` | Verify a `@track` function occasionally raises within simulator |
| 6.4 | **W4:** Simulator missing `resolve_issue` | Verify `agent.resolve_issue()` is called somewhere in simulator |
| 6.5 | **W5:** Dashboard status | Verify current state — Team 2 reports C2 is complete. Part 7 covers detailed verification |

---

## Part 7: Dashboard — Implementation Verification (40 checks)

Team 2 reports C2 (Dashboard) is complete. Verify all 5 sub-phases (C2.1–C2.5, 42 sub-tasks) against the implementation plan and the dashboard v3 prototype reference.

### 7.1 C2.1 — Shell & Theming

| # | Check | Reference |
|---|-------|-----------|
| 7.1.1 | **Grid layout:** Left sidebar (Hive), center panel (views), right sidebar (Activity Stream) | C2.1.1, Dashboard v3 prototype |
| 7.1.2 | **CSS tokens:** Dark theme with spec color variables (17 tokens: `--bg-primary`, `--accent`, `--error`, `--llm`, etc.) | C2.1.2 |
| 7.1.3 | **Typography:** Monospace for data, sans-serif for UI. Consistent sizing hierarchy | C2.1.3 |
| 7.1.4 | **Animations:** Pulse (heartbeat), fade-in (new events), glow (stuck agents), transitions (view switching), sparkline draw | C2.1.4 |
| 7.1.5 | **Global filter bar:** Environment selector, project selector, time range | C2.1.5 |
| 7.1.6 | **Custom scrollbars:** Styled for dark theme, not default browser scrollbars | C2.1.6 |

### 7.2 C2.2 — The Hive (Left Sidebar)

| # | Check | Reference |
|---|-------|-----------|
| 7.2.1 | **Agent cards rendered** with: agent name, type badge, status indicator, heartbeat age | C2.2.1 |
| 7.2.2 | **6 status states** visually distinct: `idle`, `processing`, `waiting_approval`, `error`, `stuck`, `offline` | C2.2.2 |
| 7.2.3 | **Pipeline enrichment:** Queue depth, active issue count, processing indicator on cards | C2.2.3 |
| 7.2.4 | **Sparklines:** Mini activity chart on each agent card | C2.2.4 |
| 7.2.5 | **Status sorting:** Stuck/error agents sorted to top (attention priority) | C2.2.5 |
| 7.2.6 | **Interactions:** Click selects agent (filters other panels). Double-click opens agent detail view | C2.2.6 |
| 7.2.7 | **Field mapping:** Dashboard reads `derived_status` (not `status`) from API responses | Fix W4 |

### 7.3 C2.3 — Center Panel (3 Views)

#### 7.3.1 Mission Control

| # | Check | Reference |
|---|-------|-----------|
| 7.3.1a | **Stats ribbon:** Active agents, tasks/hour, success rate, avg duration, total cost (1h window) | C2.3.1a |
| 7.3.1b | **Metrics chart:** Time-series visualization (line or area chart) | C2.3.1b |
| 7.3.1c | **Task table:** Columns: task_id, agent, type, status, duration, LLM calls, cost, time. Clickable rows | C2.3.1c |
| 7.3.1d | **Task status dots:** Color-coded by status (processing=blue, completed=green, failed=red, etc.) | C2.3.1d |
| 7.3.1e | **Timeline visualization:** Per-task horizontal timeline with action nodes when a task is selected | C2.3.1e |
| 7.3.1f | **Plan progress bar:** Shows plan completion (completed/total steps) from timeline `plan` field | C2.3.1f |
| 7.3.1g | **Branch visualization:** Retry/error branches render as smaller nodes below main sequence | C2.3.1g |
| 7.3.1h | **Pinned node detail:** Click timeline node → detail panel. Shows payload fields, duration, tags | C2.3.1h |

#### 7.3.2 Cost Explorer

| # | Check | Reference |
|---|-------|-----------|
| 7.3.2a | **Cost ribbon:** Total cost, LLM calls count, tokens in, tokens out, avg cost/call | C2.3.2a |
| 7.3.2b | **By Model table:** Model name, calls, tokens in/out, cost, visual cost bar | C2.3.2b |
| 7.3.2c | **By Agent table:** Agent name (clickable), calls, tokens in/out, cost, visual cost bar | C2.3.2c |

#### 7.3.3 Agent Detail View

| # | Check | Reference |
|---|-------|-----------|
| 7.3.3a | **Agent header:** Name, status badge, close button | C2.3.3a |
| 7.3.3b | **Tab navigation:** Tasks tab and Pipeline tab with active indicator | C2.3.3b |
| 7.3.3c | **Tasks tab:** Same table format as mission control, pre-filtered to selected agent | C2.3.3c |
| 7.3.3d | **Pipeline — Issues:** Summary, severity badge, category, occurrences (conditional display) | C2.3.3d |
| 7.3.3e | **Pipeline — Queue:** ID, priority badge, source, summary, age. Empty state message | C2.3.3e |
| 7.3.3f | **Pipeline — TODOs:** Summary, priority, source (conditional display) | C2.3.3f |
| 7.3.3g | **Pipeline — Scheduled:** Name, next run, interval, status indicator | C2.3.3g |

### 7.4 C2.4 — Activity Stream (Right Sidebar)

| # | Check | Reference |
|---|-------|-----------|
| 7.4.1 | **Stream header:** "Activity" title with animated green "LIVE" badge (pulse dot). Event count | C2.4.1 |
| 7.4.2 | **Filter chips:** 7 filters: all, task, action, error, llm, pipeline, human. Active chip visually distinct | C2.4.2 |
| 7.4.3 | **Event card:** Kind icon (◆ llm, ⊞ queue, ☐ todo, ⚑ issue, ⏲ scheduled) + type name. Time. Agent › Task breadcrumb. Summary | C2.4.3 |
| 7.4.4 | **Severity coloring:** error=red, warn=amber, llm_call=purple, info=blue, debug=gray | C2.4.4 |
| 7.4.5 | **Auto-scroll:** New events prepend to top. Auto-scrolls. Pauses when user scrolls down. Resumes on scroll to top | C2.4.5 |
| 7.4.6 | **Agent filtering:** Selecting an agent in the Hive filters the stream. Chip filter + agent filter work simultaneously | C2.4.6 |

### 7.5 C2.5 — API & WebSocket Wiring

| # | Check | Reference |
|---|-------|-----------|
| 7.5.1 | **API client module exists:** Functions for `fetchAgents`, `fetchTasks`, `fetchTimeline`, `fetchEvents`, `fetchMetrics`, `fetchCost`, `fetchPipeline` | C2.5.1 |
| 7.5.2 | **Authorization:** All API calls include `Authorization: Bearer {apiKey}` header | C2.5.1 |
| 7.5.3 | **Initial data load:** On page load, parallel fetch of agents, tasks, events, metrics. Loading spinner. Error state if unreachable | C2.5.2 |
| 7.5.4 | **WebSocket connection:** Connects to `ws://host/v1/stream?token={apiKey}`. Sends `subscribe` with channels + filters | C2.5.3 |
| 7.5.5 | **Live event handling:** `event.new` → prepends to activity stream, updates task table, refreshes timeline if affected | C2.5.4 |
| 7.5.6 | **Agent status updates:** `agent.status_changed` → updates agent card status, re-sorts Hive | C2.5.4 |
| 7.5.7 | **Stuck alert:** `agent.stuck` → highlights agent card with urgent glow | C2.5.4 |
| 7.5.8 | **Polling fallback:** If WebSocket fails after 3 attempts → polls `/v1/events?since=...` every 5s | C2.5.5 |
| 7.5.9 | **Filter sync:** Changing filters in UI updates WebSocket subscription and API query params | C2.5.6 |
| 7.5.10 | **Error handling:** API failures show toast, fall back to last known data (no blank screen) | C2.5.1 |
| 7.5.11 | **Scheduled items field mapping:** Dashboard reads `last_status` (not `status`) from pipeline response | Fix W6 |

---

## Part 8: Simulator — Coverage & Correctness (15 checks)

The simulator is the primary test harness for integration. Verify it exercises the full surface area.

### 8.1 Event Type Coverage

| # | Event Type | Expected Source Agent |
|---|------------|---------------------|
| 8.1.1 | `agent_registered` | All 3 (on startup) |
| 8.1.2 | `heartbeat` | All 3 (background thread) |
| 8.1.3 | `task_started` | All 3 |
| 8.1.4 | `task_completed` | All 3 |
| 8.1.5 | `task_failed` | `support-triage` (5% rate) |
| 8.1.6 | `action_started` | All 3 (via `@track`) |
| 8.1.7 | `action_completed` | All 3 |
| 8.1.8 | `action_failed` | Should exist post-W3 fix |
| 8.1.9 | `retry_started` | `lead-qualifier` (enrichment), `data-pipeline` (step) |
| 8.1.10 | `escalated` | `support-triage` (10% escalation) |
| 8.1.11 | `approval_requested` | `support-triage` (escalation flow) |
| 8.1.12 | `approval_received` | `support-triage` (after approval_requested) |
| 8.1.13 | `custom` | All 3 (via convenience methods) |

### 8.2 Payload Kind Coverage

| # | Kind | Expected Source |
|---|------|----------------|
| 8.2.1 | `llm_call` | All 3 agents |
| 8.2.2 | `plan_created` | `lead-qualifier` (3-step plans), `data-pipeline` (variable steps) |
| 8.2.3 | `plan_step` | `lead-qualifier`, `data-pipeline` |
| 8.2.4 | `queue_snapshot` | `lead-qualifier` (queue_provider), `data-pipeline` (explicit) |
| 8.2.5 | `todo` | `support-triage` |
| 8.2.6 | `scheduled` | `lead-qualifier`, `data-pipeline` |
| 8.2.7 | `issue` (reported) | `lead-qualifier` |
| 8.2.8 | `issue` (resolved) | Should exist post-W4 fix |

### 8.3 Behavioral Checks

| # | Check | Description |
|---|-------|-------------|
| 8.3.1 | 3 agents run in parallel threads | Verify concurrent execution |
| 8.3.2 | `--fast` and `--speed N` work | Timing scales correctly |
| 8.3.3 | `--endpoint` configurable | Points to correct server URL |
| 8.3.4 | LLM call data is realistic | Token ranges (200-4000), model names, costs |
| 8.3.5 | Failures + retries produce correct event sequences | `task_failed` → `retry_started` → `task_started` |

---

## Part 9: End-to-End Integration (25 checks)

**Method:** Start the backend server, run the simulator for 2 minutes in `--fast` mode, then query every endpoint, verify data is populated, and load the dashboard to confirm it renders correctly with live data.

### 9.1 Pipeline Validation

| # | Check | Method |
|---|-------|--------|
| 9.1.1 | Server starts without errors | `uvicorn backend.app:app` |
| 9.1.2 | Simulator connects and sends events | `python examples/simulator.py --fast --endpoint http://localhost:8000` |
| 9.1.3 | Ingestion returns 200/207 (not 400/500) | Check simulator logs |
| 9.1.4 | No events rejected | Check response `rejected` count |

### 9.2 Query Validation (after 2 min of simulator)

| # | Check | Expected |
|---|-------|----------|
| 9.2.1 | `GET /v1/agents` | 3 agents, each with `stats_1h` populated, `derived_status` valid |
| 9.2.2 | `GET /v1/tasks` | Multiple tasks per agent, varying statuses |
| 9.2.3 | `GET /v1/tasks?since=...` | Time-filtered results |
| 9.2.4 | `GET /v1/tasks/{id}/timeline` | Events array + action tree with `name`/`status`/`duration_ms` + `plan` field |
| 9.2.5 | `GET /v1/events` | Events in reverse chronological order |
| 9.2.6 | `GET /v1/events?payload_kind=llm_call` | Only LLM call events |
| 9.2.7 | `GET /v1/cost` | Non-zero costs, token totals, `by_agent`/`by_model` breakdowns |
| 9.2.8 | `GET /v1/cost/timeseries` | Buckets with `call_count`, `tokens_in`, `tokens_out` |
| 9.2.9 | `GET /v1/metrics` | Non-zero summary metrics |
| 9.2.10 | `GET /v1/metrics?group_by=agent` | `groups[]` with per-agent breakdown |
| 9.2.11 | `GET /v1/agents/{id}/pipeline` | Queue, TODOs, scheduled, and/or issues populated (varies by agent) |
| 9.2.12 | `GET /v1/projects` | At least "default" project |

### 9.3 WebSocket Validation

| # | Check | Method |
|---|-------|--------|
| 9.3.1 | Connect to `ws://localhost:8000/v1/stream?token={key}` | WebSocket client |
| 9.3.2 | Subscribe to `events` + `agents` channels | Send subscribe message |
| 9.3.3 | `event.new` messages arrive during simulator run | Verify message shape |
| 9.3.4 | `agent.status_changed` messages arrive on transitions | Verify `previous_status` and `new_status` fields |

### 9.4 Dashboard Live Rendering

Load the dashboard in a browser while the simulator is running against the backend.

| # | Check | Expected |
|---|-------|----------|
| 9.4.1 | Dashboard loads without JS errors | Open browser console, check for errors |
| 9.4.2 | The Hive shows 3 agent cards with live status | Agent cards populated from `GET /v1/agents` |
| 9.4.3 | Agent cards update when status changes | WebSocket `agent.status_changed` reflected |
| 9.4.4 | Task table populates with real tasks | Populated from `GET /v1/tasks` |
| 9.4.5 | Clicking a task shows timeline visualization | Timeline fetched from `GET /v1/tasks/{id}/timeline` |
| 9.4.6 | Cost Explorer shows non-zero data | Populated from `GET /v1/cost` |
| 9.4.7 | Activity Stream shows live events | Events arriving via WebSocket or polling |
| 9.4.8 | Filter chips work in Activity Stream | Filtering narrows visible events |
| 9.4.9 | Selecting an agent filters all panels | Click agent → tasks, events, metrics filtered |

### 9.5 Data Integrity

| # | Check | Method |
|---|-------|--------|
| 9.5.1 | Event counts are consistent: `GET /v1/events?limit=1` total ≈ sum of per-agent events | Cross-check |
| 9.5.2 | Cost totals match: `GET /v1/cost` total ≈ sum of individual `GET /v1/cost/calls` | Cross-check |
| 9.5.3 | Task event counts: each task's timeline events match the global event feed filtered by that task_id | Cross-check |
| 9.5.4 | No orphaned data: every event's `agent_id` appears in `GET /v1/agents` | Cross-check |

---

## Deliverable

Team 1 should produce a **single audit report** with the following structure:

```
# Full System Audit Report
## Summary (PASS/WARN/FAIL counts per part)
## Part 1: Shared Foundation (results table)
## Part 2: Backend — Spec Compliance (results table)
## Part 3: Backend — Fix Verification (results table)
## Part 4: Backend — Internal Quality (results table)
## Part 5: SDK — Spec Compliance (results table)
## Part 6: SDK — Fix Verification (results table)
## Part 7: Dashboard — Implementation Verification (results table)
## Part 8: Simulator — Coverage & Correctness (results table)
## Part 9: End-to-End Integration (results table)
## Critical Issues Found (if any)
## Warnings Found (if any)
## Recommendation (proceed to next phase / fix first)
```

Each check should be marked: ✅ PASS, ⚠️ WARN, ❌ FAIL, or ➖ N/A with a brief note.

---

## Success Criteria

The audit **passes** if:

1. Zero ❌ FAIL in Parts 1–3 (spec compliance + fix verification)
2. Zero ❌ FAIL in Part 7 (dashboard implementation verified)
3. Zero ❌ FAIL in Part 9 (end-to-end integration including dashboard live rendering)
4. All 152+ existing tests pass with zero regressions
5. Simulator runs for 2+ minutes against live backend without errors
6. Dashboard loads and renders correctly with live simulator data

Any FAILs must be documented with severity assessment and recommended fix.

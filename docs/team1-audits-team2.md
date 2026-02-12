# Cross-Team Audit: Team 1 Reviews Team 2

> **Auditor:** Team 1 (Backend)
> **Auditing:** Team 2's deliverables — HiveLoop SDK + Dashboard
> **Purpose:** Verify that everything Team 2 *sends to* or *expects from* the backend is correct
> **Scope:** Contract surface only — not internal implementation details
> **Reference specs:** Event Schema v2, API+SDK Spec v3, Data Model v5

---

## How to Use This Document

For each checklist item:
- **✅ PASS** — Matches the spec and what the backend implements
- **⚠️ WARN** — Works but could cause issues (note the concern)
- **❌ FAIL** — Will break on integration (describe the mismatch)

Record findings in the "Notes" column. At the end, compile a list of issues for Team 2 to fix before integration.

---

## Part 1: SDK → Ingestion Contract

This is the highest-priority section. Every event the SDK produces must be accepted by `POST /v1/ingest` without modification.

### 1.1 Batch Envelope Format

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 1.1.1 | **Envelope structure** | SDK sends `{"envelope": {...}, "events": [...]}`. Verify field names match exactly: `agent_id`, `agent_type`, `agent_version`, `framework`, `runtime`, `sdk_version`, `environment`, `group`. | |
| 1.1.2 | **`agent_id` always present** | Envelope always includes `agent_id` (required). Backend rejects batches without it. | |
| 1.1.3 | **Batch size limits** | SDK respects max 500 events per batch. Verify `batch_size` config caps at 500. | |
| 1.1.4 | **Content-Type header** | SDK sends `Content-Type: application/json`. | |
| 1.1.5 | **Authorization header** | SDK sends `Authorization: Bearer {api_key}` (not `X-API-Key` or other format). Verify exact header name and format. | |
| 1.1.6 | **Multiple agents per batch** | If SDK groups events by agent (one batch per agent), verify the backend handles this correctly. If SDK sends mixed agents in one batch, verify the backend either handles it or the SDK prevents it. | |

### 1.2 Event Shape — All 13 Types

For each event type the SDK emits, verify the event object matches what the ingestion endpoint validates.

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 1.2.1 | **Required fields on every event** | `event_id` (UUID string), `timestamp` (ISO 8601 UTC), `event_type` (one of 13 valid values). All present, correct types. | |
| 1.2.2 | **`event_id` format** | UUID4 string (lowercase with hyphens: `550e8400-e29b-41d4-a716-446655440000`). Backend uses this for dedup. | |
| 1.2.3 | **`timestamp` format** | ISO 8601 with timezone: `2026-02-10T14:32:01.000Z`. Backend parses this. Verify SDK produces consistent format. | |
| 1.2.4 | **`event_type` enum values** | SDK only emits these 13 values: `agent_registered`, `heartbeat`, `task_started`, `task_completed`, `task_failed`, `action_started`, `action_completed`, `action_failed`, `retry_started`, `escalated`, `approval_requested`, `approval_received`, `custom`. No typos, no extra types. | |
| 1.2.5 | **`severity` values** | Only: `debug`, `info`, `warn`, `error`. Verify SDK applies correct defaults per event type (e.g., `task_failed` → `error`, `heartbeat` → `debug`). | |
| 1.2.6 | **`status` values** | Only: `success`, `failure`, `timeout`, `escalated`, `cancelled`, or `null`. Verify `task_completed` sends `success`, `task_failed` sends `failure`, etc. | |
| 1.2.7 | **`project_id` population** | Present on task-scoped events (task_started, task_completed, etc.). `null` on agent-level events (heartbeat, agent_registered, agent-level custom). Never an empty string. | |
| 1.2.8 | **`task_id` population** | Present on all task-scoped events. `null` on agent-level events. Same task_id across all events in one task lifecycle. | |
| 1.2.9 | **`action_id` / `parent_action_id`** | Present on action events (`action_started`, `action_completed`, `action_failed`). `parent_action_id` correctly references the enclosing action's `action_id` for nested calls. `null` for top-level actions. | |
| 1.2.10 | **`duration_ms` on completion events** | `task_completed`, `task_failed`, `action_completed`, `action_failed` should include `duration_ms` as an integer (milliseconds). | |
| 1.2.11 | **Field size limits** | `agent_id` ≤ 256 chars, `task_id` ≤ 256, `environment` ≤ 64, `group` ≤ 128, `payload` ≤ 32KB. Verify SDK doesn't silently exceed these. | |
| 1.2.12 | **Null vs absent fields** | Verify SDK sends `null` for empty optional fields (not omitting the key entirely), OR verify the backend handles both absent and null correctly. This must be consistent. | |

### 1.3 Well-Known Payload Kinds

These are the 7 payload conventions. Each must match the structure the backend validates (advisorily) and the dashboard renders.

| # | Kind | Required `data` fields | Verify in SDK | Notes |
|---|------|----------------------|---------------|-------|
| 1.3.1 | `llm_call` | `name`, `model` (required). `tokens_in`, `tokens_out`, `cost`, `duration_ms`, `prompt_preview`, `response_preview` (optional). | `task.llm_call()` and `agent.llm_call()` produce correct shape. `event_type` is `custom`. | |
| 1.3.2 | `plan_created` | `goal`, `steps` (array of strings), `revision` | `task.plan()` produces correct shape. `event_type` is `custom`. | |
| 1.3.3 | `plan_step` | `step_index`, `total_steps`, `action` (one of: `started`, `completed`, `failed`, `skipped`), `summary` | `task.plan_step()` produces correct shape. `total_steps` auto-populated from prior `task.plan()` call. | |
| 1.3.4 | `queue_snapshot` | `depth` (integer, 0 is valid) | `agent.queue_snapshot()` produces correct shape. `task_id` is `null`. | |
| 1.3.5 | `todo` | `todo_id`, `action` (one of: `created`, `completed`, `failed`, `dismissed`, `deferred`) | `agent.todo()` produces correct shape. `task_id` is `null`. | |
| 1.3.6 | `scheduled` | `items` (array) | `agent.scheduled()` produces correct shape. `task_id` is `null`. | |
| 1.3.7 | `issue` | `severity` (one of: `critical`, `high`, `medium`, `low`), `action` (`reported` or `resolved`) | `agent.report_issue()` → action=`reported`. `agent.resolve_issue()` → action=`resolved`. `task_id` is `null`. | |

**For all 7 kinds, verify:**

| # | Check | Notes |
|---|-------|-------|
| 1.3.8 | Payload envelope: `{"kind": "...", "summary": "...", "data": {...}, "tags": [...]}` | |
| 1.3.9 | `kind` is a string matching exactly (e.g., `"llm_call"` not `"llm-call"` or `"LLM_CALL"`) | |
| 1.3.10 | `summary` is auto-generated, ≤ 256 chars, human-readable | |
| 1.3.11 | `data` is an object (not a string, not an array) | |
| 1.3.12 | `tags` is an array of strings (or absent/null) | |

### 1.4 SDK Transport Behavior

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 1.4.1 | **Response parsing** | SDK correctly parses `IngestResponse`: `{"accepted": N, "rejected": M, "warnings": [...], "errors": [...]}`. Handles 200 and 207 correctly. | |
| 1.4.2 | **Retry behavior on 429** | SDK reads `retry_after_seconds` from response body `details` field and waits accordingly. | |
| 1.4.3 | **Retry behavior on 5xx** | Exponential backoff, max 5 retries, events stay in queue during retries. | |
| 1.4.4 | **No retry on 400** | SDK drops the batch on 400 (permanently invalid), does not retry. | |
| 1.4.5 | **Idempotency** | SDK uses the same `event_id` when retrying a batch. Backend deduplicates. Verify SDK doesn't regenerate UUIDs on retry. | |

---

## Part 2: Dashboard → API Contract

Verify that the dashboard's API client calls match what the backend endpoints actually serve.

### 2.1 Endpoint Paths and Methods

| # | Dashboard calls | Backend provides | Verify match | Notes |
|---|----------------|-----------------|-------------|-------|
| 2.1.1 | `GET /v1/agents` | `GET /v1/agents` | Exact path, query params | |
| 2.1.2 | `GET /v1/agents/{id}` | `GET /v1/agents/{agent_id}` | Path param name in URL | |
| 2.1.3 | `GET /v1/agents/{id}/pipeline` | `GET /v1/agents/{agent_id}/pipeline` | Path and response shape | |
| 2.1.4 | `GET /v1/tasks` | `GET /v1/tasks` | Query params: `agent_id`, `project_id`, `status`, `sort`, `limit` | |
| 2.1.5 | `GET /v1/tasks/{id}/timeline` | `GET /v1/tasks/{task_id}/timeline` | Path param, response includes `events`, `action_tree`, `plan` | |
| 2.1.6 | `GET /v1/events` | `GET /v1/events` | Query params: `agent_id`, `event_type`, `severity`, `since`, `limit`, `cursor` | |
| 2.1.7 | `GET /v1/cost` | `GET /v1/cost` | Query params: `group_by`, `since`, `until` | |
| 2.1.8 | `GET /v1/cost/calls` | `GET /v1/cost/calls` | Pagination params | |
| 2.1.9 | `GET /v1/cost/timeseries` | `GET /v1/cost/timeseries` | `interval`, `split_by_model` | |
| 2.1.10 | `GET /v1/metrics` | `GET /v1/metrics` | `metric`, `group_by`, `range` params | |
| 2.1.11 | `GET /v1/projects` | `GET /v1/projects` | List response shape | |

### 2.2 Response Shape Expectations

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.2.1 | **Pagination format** | Dashboard expects `{"data": [...], "pagination": {"cursor": "...", "has_more": true}}`. Verify backend returns this exact shape. | |
| 2.2.2 | **Agent list response** | Dashboard expects each agent to have: `agent_id`, `agent_type`, `status` (derived), `last_heartbeat`, `last_seen`, `current_task_id`, `current_project_id`, `stats_1h`. Verify all fields present. | |
| 2.2.3 | **Agent `status` field values** | Dashboard renders 6 states: `idle`, `processing`, `waiting_approval`, `error`, `stuck`, `offline`. Verify backend returns these exact strings. Check: does backend return `offline` or only `stuck`? | |
| 2.2.4 | **Task list response** | Dashboard expects: `task_id`, `agent_id`, `task_type`, `status`, `duration_ms`, `total_cost`, `llm_call_count`, `started_at`. Verify field names match. | |
| 2.2.5 | **Timeline response** | Dashboard expects: `events` (array, chronological), `action_tree`, `error_chains`, `plan` (with `steps` and `progress`). Verify structure. | |
| 2.2.6 | **Pipeline response** | Dashboard expects 4 sections: `queue` (object), `todos` (array), `scheduled` (object with `items`), `issues` (array). Verify field names and nesting. | |
| 2.2.7 | **Cost summary response** | Dashboard expects: `total_cost`, `total_tokens_in`, `total_tokens_out`, `total_calls`, `breakdown` (array of `{agent_id, model, cost, ...}`). Verify shape. | |
| 2.2.8 | **Null handling** | Dashboard must handle null values for optional fields (e.g., `current_task_id: null`, `total_cost: null`). Verify it doesn't crash on nulls. | |
| 2.2.9 | **Empty states** | Dashboard handles empty arrays for agents (no agents registered), tasks (no tasks yet), events (no events). Verify graceful rendering. | |
| 2.2.10 | **Timestamp format** | Backend returns timestamps as ISO 8601 strings. Dashboard parses them correctly (timezone handling, relative time display). | |

### 2.3 Authorization

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.3.1 | **Header format** | Dashboard sends `Authorization: Bearer {apiKey}` on every request. Same header name and format the backend expects. | |
| 2.3.2 | **401 handling** | Dashboard shows appropriate error when API key is invalid (not a generic "Network Error"). | |
| 2.3.3 | **API key configuration** | Dashboard has a way to configure the API key (env var, input field, config). Not hardcoded to a test key. | |

---

## Part 3: Dashboard → WebSocket Contract

### 3.1 Connection Protocol

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 3.1.1 | **Connection URL** | Dashboard connects to `ws://localhost:8000/v1/stream?token={apiKey}`. Verify query param name is `token` (not `api_key` or `key`). | |
| 3.1.2 | **Subscribe message** | Dashboard sends `{"action": "subscribe", "channels": ["events", "agents"], "filters": {...}}`. Verify `action` field name, channel names, and filter structure match backend protocol. | |
| 3.1.3 | **Unsubscribe message** | `{"action": "unsubscribe", "channels": [...]}`. Verify format. | |
| 3.1.4 | **Ping/pong** | If backend sends ping, dashboard responds. Verify keepalive logic doesn't cause disconnects. | |

### 3.2 Incoming Message Handling

| # | Message type | Dashboard expects | Verify | Notes |
|---|-------------|------------------|--------|-------|
| 3.2.1 | `event.new` | `{"type": "event.new", "data": {event fields}}` | Dashboard correctly parses and renders the event in the Activity Stream. | |
| 3.2.2 | `agent.status_changed` | `{"type": "agent.status_changed", "data": {"agent_id": "...", "previous_status": "...", "new_status": "...", ...}}` | Dashboard updates the correct agent card. | |
| 3.2.3 | `agent.stuck` | `{"type": "agent.stuck", "data": {"agent_id": "...", "last_heartbeat": "...", ...}}` | Dashboard highlights the agent card with urgent glow. | |
| 3.2.4 | **Unknown message types** | Dashboard ignores message types it doesn't recognize (doesn't crash). | |

### 3.3 Reconnection Behavior

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 3.3.1 | **Reconnect on disconnect** | Dashboard attempts reconnection with backoff after WebSocket closes unexpectedly. | |
| 3.3.2 | **Re-subscribe on reconnect** | After reconnecting, dashboard re-sends subscribe message with current filters. | |
| 3.3.3 | **Polling fallback** | If WebSocket fails 3 times, dashboard falls back to polling `GET /v1/events`. Verify polling uses correct endpoint and params. | |
| 3.3.4 | **Status indicator** | Dashboard shows connection status (LIVE badge vs disconnected state). | |

---

## Part 4: SDK Simulator Review

If the simulator (C1.6) exists, verify it generates realistic data that exercises the backend properly.

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 4.1 | **All 13 event types** | Simulator produces events of all 13 types over the course of a run. | |
| 4.2 | **All 7 payload kinds** | Simulator uses all convenience methods: `llm_call`, `plan`/`plan_step`, `queue_snapshot`, `todo`, `scheduled`, `report_issue`/`resolve_issue`. | |
| 4.3 | **Error scenarios** | Simulator generates occasional task failures, action failures, retries, and escalations — not just happy paths. | |
| 4.4 | **Realistic timing** | Tasks have varied durations (not all instant). LLM calls have plausible token counts and costs. Heartbeats arrive at the configured interval. | |
| 4.5 | **Multiple agents** | Simulator runs 3+ agents of different types concurrently. | |
| 4.6 | **Configurable** | Can adjust number of agents, task frequency, error rate, and run duration. | |

---

## Findings Summary

| Severity | Count | Description |
|----------|-------|-------------|
| ❌ FAIL | | Issues that will break integration |
| ⚠️ WARN | | Issues that may cause problems |
| ✅ PASS | | Confirmed working |

### Critical Issues (must fix before integration)

| # | Section | Finding | Suggested Fix |
|---|---------|---------|---------------|
| | | | |

### Warnings (fix before production, OK for integration)

| # | Section | Finding | Suggested Fix |
|---|---------|---------|---------------|
| | | | |

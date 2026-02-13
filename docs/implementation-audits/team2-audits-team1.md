# Cross-Team Audit: Team 2 Reviews Team 1

> **Auditor:** Team 2 (Clients — SDK + Dashboard)
> **Auditing:** Team 1's deliverables — Storage Layer + API Server + WebSocket + Alerting
> **Purpose:** Verify that everything the backend *accepts from* the SDK and *returns to* the dashboard is correct
> **Scope:** Contract surface only — not internal storage implementation details
> **Reference specs:** Event Schema v2, API+SDK Spec v3, Data Model v5

---

## How to Use This Document

For each checklist item:
- **✅ PASS** — Matches the spec and what the SDK/Dashboard expects
- **⚠️ WARN** — Works but could cause issues (note the concern)
- **❌ FAIL** — Will break on integration (describe the mismatch)

Record findings in the "Notes" column. At the end, compile a list of issues for Team 1 to fix before integration.

---

## Part 1: Ingestion Endpoint — Does It Accept What the SDK Sends?

The SDK sends batches to `POST /v1/ingest`. This section verifies the endpoint accepts everything the SDK produces and rejects what it should.

### 1.1 Happy Path — Valid Batches

Use the shared fixtures (`shared/fixtures/sample_batch.json`) and the SDK simulator output for these tests.

| # | Check | How to test | Notes |
|---|-------|-------------|-------|
| 1.1.1 | **Sample fixture accepted** | POST the shared `sample_batch.json`. Expect 200 with `accepted: 22, rejected: 0`. | |
| 1.1.2 | **All 13 event types accepted** | POST a batch containing one event of each type. All 13 accepted, zero rejected. Verify against: `agent_registered`, `heartbeat`, `task_started`, `task_completed`, `task_failed`, `action_started`, `action_completed`, `action_failed`, `retry_started`, `escalated`, `approval_requested`, `approval_received`, `custom`. | |
| 1.1.3 | **All 7 payload kinds accepted** | POST custom events with each well-known `payload.kind`: `llm_call`, `plan_created`, `plan_step`, `queue_snapshot`, `todo`, `scheduled`, `issue`. All accepted. | |
| 1.1.4 | **Agent-level events (null task_id)** | POST `heartbeat`, `agent_registered`, and agent-level `custom` events with `task_id: null` and `project_id: null`. All accepted — not rejected for missing task/project. | |
| 1.1.5 | **Deduplication** | POST the same batch twice. Second POST returns 200 (not an error). Event count in storage doesn't double. | |
| 1.1.6 | **Envelope expansion** | POST a batch where events don't include `agent_id`. Verify the ingestion pipeline copies `agent_id` from the envelope into each stored event. Query the events back and confirm `agent_id` is populated. | |
| 1.1.7 | **`received_at` set by server** | POST a batch. Query the events back. Verify each event has a `received_at` timestamp set by the server (not copied from the client). | |
| 1.1.8 | **Severity auto-defaults** | POST a `task_failed` event without `severity`. Verify it's stored with `severity: "error"`. POST a `heartbeat` without severity. Verify it's stored with `severity: "debug"`. | |

### 1.2 Validation — Rejection Behavior

| # | Check | How to test | Expected response | Notes |
|---|-------|-------------|-------------------|-------|
| 1.2.1 | **Missing `agent_id` in envelope** | POST batch with `envelope.agent_id` absent | 400 `invalid_batch` | |
| 1.2.2 | **Missing `event_id` on event** | POST batch with one event lacking `event_id` | 207 with that event in `errors` array | |
| 1.2.3 | **Missing `timestamp` on event** | POST batch with one event lacking `timestamp` | 207 with that event in `errors` | |
| 1.2.4 | **Invalid `event_type`** | POST event with `event_type: "task_exploded"` | 207, event rejected with `invalid_event_type` error | |
| 1.2.5 | **Invalid `severity`** | POST event with `severity: "critical"` (not in enum) | 207, event rejected | |
| 1.2.6 | **Payload over 32KB** | POST event with payload > 32KB | 207, event rejected with size limit error | |
| 1.2.7 | **Batch over 500 events** | POST batch with 501 events | 400 `invalid_batch` | |
| 1.2.8 | **Invalid `project_id`** | POST task event with `project_id` that doesn't exist for the tenant | 207, event rejected with `invalid_project_id` | |
| 1.2.9 | **Partial success (207)** | POST batch with 5 valid + 2 invalid events | 207 with `accepted: 5, rejected: 2`, valid events stored, invalid events listed in `errors` with specific error codes | |

### 1.3 Advisory Payload Warnings

These events should be ACCEPTED (not rejected) but generate warnings.

| # | Check | How to test | Expected | Notes |
|---|-------|-------------|----------|-------|
| 1.3.1 | **`llm_call` missing `data.model`** | POST custom event with `kind: "llm_call"` but no `data.model` | Accepted, `warnings` array has `payload_convention` warning | |
| 1.3.2 | **`todo` missing `data.action`** | POST custom event with `kind: "todo"` but no `data.action` | Accepted with warning | |
| 1.3.3 | **Unknown `kind` value** | POST custom event with `kind: "my_custom_thing"` | Accepted, NO warning (unknown kinds are fine) | |
| 1.3.4 | **No `kind` at all** | POST custom event with plain payload (no `kind` field) | Accepted, no warning | |

### 1.4 Side Effects of Ingestion

Verify that ingestion correctly updates derived state that the dashboard depends on.

| # | Check | How to test | Notes |
|---|-------|-------------|-------|
| 1.4.1 | **Agent profile created** | POST an `agent_registered` event. `GET /v1/agents` returns the agent with correct `agent_id`, `agent_type`, `framework`. | |
| 1.4.2 | **Agent `last_seen` updated** | POST any event for an agent. `GET /v1/agents/{id}` shows updated `last_seen`. | |
| 1.4.3 | **Agent `last_heartbeat` updated** | POST a `heartbeat` event. `GET /v1/agents/{id}` shows updated `last_heartbeat`. POST a non-heartbeat event. Verify `last_heartbeat` does NOT change. | |
| 1.4.4 | **Project-agent junction populated** | POST a `task_started` event with `project_id`. `GET /v1/projects/{id}/agents` includes this agent. | |
| 1.4.5 | **Agent status derived correctly** | POST `task_started` → agent status should be `processing`. POST `task_completed` → agent status should be `idle`. POST `approval_requested` → agent status should be `waiting_approval`. | |

---

## Part 2: Query Endpoints — Do Responses Match Dashboard Expectations?

The dashboard fetches data from GET endpoints. This section verifies every response shape the dashboard depends on.

### 2.1 `GET /v1/agents` — The Hive

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.1.1 | **Response is an array of agent objects** | Not wrapped in `{"data": [...]}` if that's not what dashboard expects, or wrapped if it is. Clarify and verify consistency. | |
| 2.1.2 | **Each agent has required fields** | `agent_id`, `agent_type`, `status` (derived string), `last_heartbeat` (ISO string or null), `last_seen` (ISO string), `current_task_id` (string or null), `current_project_id` (string or null). | |
| 2.1.3 | **`status` values match dashboard rendering** | Backend returns one of: `idle`, `processing`, `waiting_approval`, `error`, `stuck`. Dashboard renders all 6 states (including `offline`). Clarify: does backend ever return `offline`? If not, how does the dashboard determine offline state? | |
| 2.1.4 | **Stuck detection works** | Register an agent, send one heartbeat, wait beyond `stuck_threshold`. `GET /v1/agents` returns `status: "stuck"` for that agent. | |
| 2.1.5 | **Heartbeat age calculation** | Response includes `heartbeat_age_seconds` (integer). Verify it's computed correctly from `last_heartbeat`. | |
| 2.1.6 | **`project_id` filter** | `GET /v1/agents?project_id=X` returns only agents associated with project X via the project_agents junction. Agent-level events for non-matching projects are excluded. | |
| 2.1.7 | **Sort by attention** | Default sort puts `stuck` and `error` agents first, `idle` agents last. Verify the sort matches dashboard expectations. | |
| 2.1.8 | **Pipeline enrichment fields** | If dashboard expects `queue_depth`, `issue_count`, or `current_action` on the agent list response, verify those fields are present. If they're not returned here, verify dashboard fetches them separately. | |

### 2.2 `GET /v1/tasks` — Task Table

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.2.1 | **Task object fields** | Each task has: `task_id`, `agent_id`, `task_type`, `project_id`, `status` (derived), `duration_ms`, `started_at`, `completed_at` (or null). | |
| 2.2.2 | **Derived `status` values** | Returns one of: `completed`, `failed`, `escalated`, `waiting`, `processing`. Verify derivation logic matches spec: completed > failed > escalated > waiting > processing. | |
| 2.2.3 | **Cost rollup fields** | Each task includes `total_cost`, `total_tokens_in`, `total_tokens_out`, `llm_call_count`. These are aggregated from `llm_call` custom events within the task. Verify they're numbers (not strings), and `null` when no LLM calls exist. | |
| 2.2.4 | **Filters work** | `agent_id`, `project_id`, `status`, `task_type`, `since`, `until` — each filter correctly narrows results. | |
| 2.2.5 | **Sort options** | `sort=newest` (default), `sort=oldest`, `sort=duration`, `sort=cost`. Verify each works. | |
| 2.2.6 | **Pagination** | `limit` and `cursor` work. Response includes `pagination.cursor` and `pagination.has_more`. | |

### 2.3 `GET /v1/tasks/{task_id}/timeline` — The Core Product

This is the most complex response shape. The dashboard builds the entire timeline visualization from it.

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.3.1 | **Events array** | Response includes `events` — chronologically ordered array of all events for this task. Each event has full fields (event_type, timestamp, duration_ms, payload, severity, action_id, parent_action_id). | |
| 2.3.2 | **Action tree** | Response includes `action_tree` — nested structure grouping actions by `action_id`/`parent_action_id`. Dashboard uses this for branch visualization. Verify structure: `{action_id, name, status, duration_ms, children: [...]}`. | |
| 2.3.3 | **Error chains** | Response includes `error_chains` — links from failures to retries via `parent_event_id`. Dashboard renders these as branch paths below the main timeline. | |
| 2.3.4 | **Plan overlay** | If task has a plan: response includes `plan` with `goal`, `steps` (array), `progress` (per-step status). Dashboard renders the plan progress bar from this. | |
| 2.3.5 | **LLM call events distinguishable** | Events with `payload.kind = "llm_call"` are present in the events array. Dashboard renders these with purple diamond nodes. Verify the payload data (`model`, `tokens_in`, `tokens_out`, `cost`) is accessible in the event payload. | |
| 2.3.6 | **Task metadata** | Response includes top-level task metadata: `task_id`, `agent_id`, `task_type`, `status`, `duration_ms`, `total_cost`, `started_at`, `completed_at`. | |
| 2.3.7 | **404 on missing task** | `GET /v1/tasks/nonexistent/timeline` returns 404 with proper error shape, not 500. | |

### 2.4 `GET /v1/events` — Activity Stream

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.4.1 | **Reverse chronological** | Events returned newest-first by default. | |
| 2.4.2 | **Filter by `event_type`** | `event_type=task_completed,task_failed` returns only those types. Comma-separated. | |
| 2.4.3 | **Filter by `severity`** | `severity=error,warn` works. | |
| 2.4.4 | **Filter by `agent_id`** | Returns only events for that agent, including agent-level events. | |
| 2.4.5 | **Filter by `payload_kind`** | `payload_kind=llm_call` returns only custom events with that kind. Dashboard uses this for the LLM filter chip. | |
| 2.4.6 | **`exclude_heartbeats` flag** | `exclude_heartbeats=true` omits heartbeat events. Dashboard uses this by default to avoid flooding the stream. | |
| 2.4.7 | **`since` parameter** | `since=2026-02-10T14:00:00Z` returns events after that timestamp. Dashboard uses this for incremental polling. | |
| 2.4.8 | **Cursor pagination** | Works correctly. Can paginate through all events for a given filter. | |
| 2.4.9 | **Event payload included** | Each event in the response includes the full `payload` field (not stripped). Dashboard needs this for detail display. | |

### 2.5 `GET /v1/agents/{agent_id}/pipeline` — Pipeline Tab

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.5.1 | **Response has 4 sections** | `queue`, `todos`, `scheduled`, `issues` — all present even if empty. | |
| 2.5.2 | **Queue section** | `queue.depth` (integer), `queue.items` (array, may be empty), `queue.processing` (object or null), `queue.snapshot_at` (timestamp of last snapshot). | |
| 2.5.3 | **TODOs section** | Array of active TODOs. Each: `todo_id`, `summary`, `priority`, `source`, `action` (should be `created` or similar, not `completed`/`dismissed`). Completed TODOs should NOT appear in the active list. | |
| 2.5.4 | **TODO lifecycle correctness** | Create a TODO (action=`created`), then complete it (action=`completed`). Verify it disappears from the active TODOs list. | |
| 2.5.5 | **Scheduled section** | Object with `items` array. Each item: `name`, `next_run`, `interval`, `status`. From the most recent `scheduled` event. | |
| 2.5.6 | **Issues section** | Array of active issues. Each: `summary`, `severity`, `category`, `occurrence_count`, `last_seen`. Resolved issues should NOT appear. | |
| 2.5.7 | **Issue lifecycle correctness** | Report an issue, then resolve it. Verify it disappears from active issues. | |
| 2.5.8 | **Empty state** | Agent with no pipeline events returns all 4 sections as empty/null (not 404, not an error). | |

### 2.6 Cost Endpoints

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.6.1 | **`GET /v1/cost` summary** | Returns `total_cost`, `total_tokens_in`, `total_tokens_out`, `total_calls`, and a `breakdown` array grouped by the requested dimension (`agent`, `model`, or `agent_model`). | |
| 2.6.2 | **`GET /v1/cost/calls`** | Returns individual LLM call records with: `name`, `model`, `tokens_in`, `tokens_out`, `cost`, `duration_ms`, `agent_id`, `task_id`, `timestamp`. Paginated. | |
| 2.6.3 | **`GET /v1/cost/timeseries`** | Returns time-bucketed cost data. Each bucket: `timestamp`, `cost`, `calls`. With `split_by_model=true`: each bucket has per-model breakdown. | |
| 2.6.4 | **Cost is a number, not a string** | `total_cost` and `cost` fields are numeric (float), not string representations of numbers. Dashboard does math on these. | |
| 2.6.5 | **Zero cost handled** | Agents/tasks with no LLM calls return `total_cost: 0` (not null, not absent). | |

### 2.7 `GET /v1/metrics` — Metrics with group_by

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.7.1 | **Without `group_by`** | Returns single aggregate: `{"value": N}` or similar scalar response. | |
| 2.7.2 | **With `group_by=model`** | Returns array of `{group: "gpt-4", value: 1234}` objects, ordered by value DESC. | |
| 2.7.3 | **With `group_by=agent_id`** | Returns array grouped by agent. | |
| 2.7.4 | **`metric` parameter** | Supports: `events`, `tasks`, `tokens_in`, `tokens_out`, `llm_calls`, `cost`. Each returns correct aggregation. | |
| 2.7.5 | **`range` parameter** | `1h`, `24h`, `7d`, `30d` all work and scope the time window correctly. | |

### 2.8 Project Endpoints

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 2.8.1 | **`GET /v1/projects`** | Returns list of projects with `project_id`, `name`, `description`, `is_archived`, `created_at`. | |
| 2.8.2 | **`POST /v1/projects`** | Creates a project, returns the created object with generated `project_id`. | |
| 2.8.3 | **Default project exists** | Every tenant has a default project. It cannot be deleted (attempt returns error). | |
| 2.8.4 | **Archived projects hidden** | `GET /v1/projects` excludes archived by default. `GET /v1/projects?include_archived=true` includes them. | |

---

## Part 3: WebSocket — Does the Server Broadcast What the Dashboard Expects?

### 3.1 Connection Setup

| # | Check | How to test | Notes |
|---|-------|-------------|-------|
| 3.1.1 | **Connection URL** | Connect to `ws://localhost:8000/v1/stream?token={api_key}`. Verify query param is `token`. | |
| 3.1.2 | **Auth on connect** | Invalid API key → connection closed with code 4001. | |
| 3.1.3 | **Connection limit** | Open 6 connections with same API key. 6th should be rejected with code 4002 (limit is 5). | |
| 3.1.4 | **Subscribe accepted** | Send `{"action": "subscribe", "channels": ["events", "agents"]}`. Receive `{"type": "subscribed", ...}` confirmation. | |

### 3.2 Event Broadcasting

| # | Check | How to test | Notes |
|---|-------|-------------|-------|
| 3.2.1 | **Events arrive on ingest** | Subscribe to `events` channel. POST a batch via `/v1/ingest`. Receive `{"type": "event.new", "data": {...}}` for each accepted event. | |
| 3.2.2 | **Event data shape** | The `data` field in `event.new` contains a full event object (same shape as `GET /v1/events` returns). Dashboard must be able to render it directly. | |
| 3.2.3 | **Filter by project_id** | Subscribe with `{"filters": {"project_id": "X"}}`. POST events for project X and project Y. Only project X events arrive. Agent-level events (no project) for agents IN project X still arrive. | |
| 3.2.4 | **Filter by event_type** | Subscribe with `{"filters": {"event_types": ["task_completed", "task_failed"]}}`. Only those types arrive. | |
| 3.2.5 | **Latency** | Events arrive within ~1 second of ingestion POST returning. Dashboard's "LIVE" badge depends on low latency. | |

### 3.3 Agent Status Broadcasting

| # | Check | How to test | Notes |
|---|-------|-------------|-------|
| 3.3.1 | **Status change broadcast** | Subscribe to `agents` channel. POST `task_started` for an idle agent. Receive `{"type": "agent.status_changed", "data": {"agent_id": "...", "previous_status": "idle", "new_status": "processing", ...}}`. | |
| 3.3.2 | **Status change data fields** | `data` includes: `agent_id`, `previous_status`, `new_status`, `timestamp`, `current_task_id`, `current_project_id`, `heartbeat_age_seconds`. | |
| 3.3.3 | **Stuck broadcast** | Register agent, send one heartbeat, wait beyond stuck threshold. Receive `{"type": "agent.stuck", "data": {"agent_id": "...", ...}}`. | |
| 3.3.4 | **Stuck fires once** | Agent stays stuck for a long time. Verify `agent.stuck` message is sent only once per stuck episode, not repeatedly. | |
| 3.3.5 | **Recovery from stuck** | Stuck agent sends heartbeat → status changes to `idle` (or `processing`). Receive `agent.status_changed` (not another `agent.stuck`). | |
| 3.3.6 | **No broadcast when status unchanged** | POST multiple heartbeats for an idle agent (status stays `idle`). Verify no `agent.status_changed` messages sent (status didn't actually change). | |

---

## Part 4: Derived State Logic — Does the Backend Compute What the Dashboard Displays?

This section verifies that the backend's state derivation logic matches what the dashboard renders. Mismatches here cause visual bugs: agent shows "processing" on the dashboard but the API says "idle."

### 4.1 Agent Status Derivation

The spec defines a priority cascade. Verify the backend implements it correctly.

| # | Scenario | Expected status | Notes |
|---|----------|----------------|-------|
| 4.1.1 | Agent registered, no heartbeat ever sent | `stuck` (no heartbeat = stuck) | |
| 4.1.2 | Agent registered, one heartbeat 10 seconds ago | `idle` | |
| 4.1.3 | Agent registered, heartbeat 10 minutes ago (beyond default stuck threshold) | `stuck` | |
| 4.1.4 | Agent has active `task_started` (no completion yet), recent heartbeat | `processing` | |
| 4.1.5 | Agent's last event was `task_failed`, recent heartbeat | `error` | |
| 4.1.6 | Agent's last event was `approval_requested`, recent heartbeat | `waiting_approval` | |
| 4.1.7 | Agent was `processing`, heartbeat goes stale | `stuck` (stuck overrides processing) | |
| 4.1.8 | Agent's last task completed, then idle, recent heartbeat | `idle` | |

### 4.2 Task Status Derivation

| # | Scenario | Expected status | Notes |
|---|----------|----------------|-------|
| 4.2.1 | Only `task_started` event | `processing` | |
| 4.2.2 | `task_started` + `task_completed` | `completed` | |
| 4.2.3 | `task_started` + `task_failed` | `failed` | |
| 4.2.4 | `task_started` + `escalated` (no completion) | `escalated` | |
| 4.2.5 | `task_started` + `approval_requested` (no `approval_received`) | `waiting` | |
| 4.2.6 | `task_started` + `approval_requested` + `approval_received` + `task_completed` | `completed` | |
| 4.2.7 | `task_started` + `task_failed` + `retry_started` + `task_completed` | `completed` (completed overrides failed) | |

---

## Part 5: Error Handling — Does the Backend Return Spec-Compliant Errors?

The dashboard has specific error handling logic. It depends on consistent error shapes.

| # | Check | How to test | Expected shape | Notes |
|---|-------|-------------|----------------|-------|
| 5.1 | **Standard error shape** | Trigger any error (404, 400, 401, 429) | `{"error": "error_code", "message": "...", "status": N, "details": {}}` | |
| 5.2 | **401 on bad API key** | Send request with invalid key | `{"error": "authentication_failed", "message": "Invalid or missing API key.", "status": 401}` | |
| 5.3 | **403 on read key + POST** | Use `hb_read_*` key on `POST /v1/ingest` | `{"error": "insufficient_permissions", ...}` | |
| 5.4 | **404 on missing resource** | `GET /v1/agents/nonexistent` | `{"error": "agent_not_found", ...}` or `{"error": "not_found", ...}` | |
| 5.5 | **429 with retry info** | Exceed rate limit | `{"error": "rate_limit_exceeded", "details": {"retry_after_seconds": N}}` | |
| 5.6 | **Rate limit headers** | Any response | Headers present: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` | |
| 5.7 | **Validation error detail** | Send malformed query param (e.g., `limit=abc`) | `{"error": "validation_error", "details": {field-level info}}` | |

---

## Part 6: Cross-Cutting Concerns

| # | Check | What to verify | Notes |
|---|-------|----------------|-------|
| 6.1 | **CORS headers** | Dashboard (running on different origin) can make requests. `Access-Control-Allow-Origin` present. Preflight (OPTIONS) works. | |
| 6.2 | **Timestamp consistency** | All timestamps in responses are ISO 8601 with timezone info (UTC). No mixing of formats (some with `Z`, some with `+00:00`, some without timezone). | |
| 6.3 | **Tenant isolation** | Create two API keys for different tenants. Verify: events ingested with key A are never visible via key B. Agents, tasks, projects — all isolated. | |
| 6.4 | **`hb_test_` key isolation** | Events ingested with `hb_test_*` key are not visible when querying with `hb_live_*` key (and vice versa). | |
| 6.5 | **UTF-8 handling** | Agent names, task types, and payload content with unicode characters (emoji, CJK, accents) are stored and returned correctly. | |
| 6.6 | **Large payload handling** | Event with a 30KB payload (under 32KB limit) is accepted, stored, and returned in full. | |
| 6.7 | **Concurrent ingestion** | Two simultaneous POST requests don't cause data corruption or lost events. | |

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

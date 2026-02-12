# HiveBoard API + HiveLoop SDK — Unified Technical Specification

**CONFIDENTIAL** | February 2026 | v2.0

---

**Changes from v1:** This version incorporates (1) project support across all endpoints, (2) Cost Explorer API endpoints, (3) `task.llm_call()` and `agent.llm_call()` SDK methods, (4) heartbeat payload and queue provider callbacks, (5) plan tracking with `task.plan()` and `task.plan_step()`, (6) work pipeline observability (`agent.queue_snapshot()`, `agent.todo()`, `agent.scheduled()`), (7) agent self-reported issues (`agent.report_issue()`, `agent.resolve_issue()`), (8) `GET /v1/agents/{agent_id}/pipeline` endpoint, (9) `cost_threshold` alert type, and (10) payload convention awareness throughout. See Appendix B for the complete change summary.

---

## 1. Introduction

This document specifies the complete technical contract between HiveLoop (the SDK) and HiveBoard (the platform). It defines every HTTP endpoint, WebSocket channel, SDK class, method signature, and internal behavior needed to build both sides of the system.

It assumes familiarity with the **Event Schema Specification v1**, the **Data Model Specification v3**, and the **Product & Functional Specification v1**. Where those documents define *what* data looks like and *why* the product exists, this document defines *how* data moves and *how* developers interact with it.

### 1.1 How to Read This Document

The spec is split into two halves that mirror each other:

- **Part A (Sections 2–7):** The HiveBoard API — what the server exposes.
- **Part B (Sections 8–15):** The HiveLoop SDK — what the developer touches.

Every SDK method maps to an API endpoint. Every API query endpoint maps to a dashboard screen. The cross-references are explicit throughout.

### 1.2 Design Principles Governing This Spec

1. **The SDK is a thin pipe.** It formats events, batches them, and ships them over HTTP. It does not evaluate, transform, or filter. Intelligence lives server-side.
2. **The API is two APIs in one.** The ingestion API (write path) is high-throughput, append-only, and tolerant. The query API (read path) is precise, filtered, and serves the dashboard.
3. **Idempotency everywhere.** Every event carries a client-generated `event_id` (UUID). The server deduplicates on `(tenant_id, event_id)`. Re-sending the same batch is always safe.
4. **Fail open on ingest, fail closed on query.** The ingestion path accepts partial batches and logs warnings. The query path returns strict errors on bad parameters.
5. **Tenant isolation is non-negotiable.** The API key is the security boundary. No endpoint accepts `tenant_id` as input. Every query is scoped automatically.
6. **Payload conventions are contracts, not enforcement.** Well-known payload shapes (`llm_call`, `plan_step`, `issue`) are validated advisorily on ingest and rendered specially in responses. Unrecognized payloads pass through unchanged.

---

# PART A: HIVEBOARD API

## 2. API Fundamentals

### 2.1 Base URL

```
https://api.hiveboard.io/v1
```

All endpoints are versioned under `/v1`. Breaking changes increment the version. Non-breaking additions (new optional fields, new endpoints) do not.

For local development / MVP:

```
http://localhost:8000/v1
```

### 2.2 Authentication

Every request must include an API key in the `Authorization` header:

```
Authorization: Bearer hb_live_a1b2c3d4e5f6...
```

**API key format:** `hb_{environment}_{32-char-alphanumeric}`

| Prefix | Scope |
|---|---|
| `hb_live_` | Production keys. Full read/write access. |
| `hb_test_` | Test keys. Isolated data namespace. Events ingested with test keys are not visible to live keys. |
| `hb_read_` | Read-only keys. Query API access only. No ingestion. Intended for dashboard embedding or CI/CD reporting. |

The server derives `tenant_id` from the API key on every request. There is no `tenant_id` parameter on any endpoint.

**Invalid or missing key response:**

```json
{
  "error": "authentication_failed",
  "message": "Invalid or missing API key.",
  "status": 401
}
```

### 2.3 Content Type

All request and response bodies are JSON:

```
Content-Type: application/json
```

### 2.4 Standard Error Response Format

Every error follows the same shape:

```json
{
  "error": "error_code_string",
  "message": "Human-readable explanation.",
  "status": 400,
  "details": {}
}
```

| Field | Type | Description |
|---|---|---|
| `error` | string | Machine-readable error code (see Section 7). |
| `message` | string | Human-readable description. |
| `status` | integer | HTTP status code (mirrored in body for convenience). |
| `details` | object \| null | Additional context. Varies per error type. |

### 2.5 Rate Limits

| Path | Limit | Scope |
|---|---|---|
| `POST /v1/ingest` | 100 requests/second | Per API key |
| `GET /v1/*` (query endpoints) | 30 requests/second | Per API key |
| `WebSocket /v1/stream` | 5 concurrent connections | Per API key |

Rate limit headers on every response:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1739290800
```

When exceeded:

```json
{
  "error": "rate_limit_exceeded",
  "message": "Too many requests. Retry after the reset time.",
  "status": 429,
  "details": { "retry_after_seconds": 2 }
}
```

### 2.6 Pagination

All list endpoints support cursor-based pagination:

| Parameter | Type | Description |
|---|---|---|
| `limit` | integer | Items per page. Default: 50. Max: 200. |
| `cursor` | string \| null | Opaque cursor from previous response. Omit for first page. |

Response includes:

```json
{
  "data": [...],
  "pagination": {
    "cursor": "eyJ0IjoiMjAyNi0wMi0xMFQxNDozMjowMC4wMDBaIn0=",
    "has_more": true
  }
}
```

The cursor is an opaque base64-encoded string. Clients must not parse or construct cursors.

---

## 3. Ingestion API (Write Path)

The ingestion API is the single entry point for all telemetry data from HiveLoop and direct HTTP integrations.

### 3.1 `POST /v1/ingest`

Accepts a batch of events wrapped in an envelope.

**Request body:**

```json
{
  "envelope": {
    "agent_id": "lead-qualifier",
    "agent_type": "sales",
    "agent_version": "1.2.0",
    "framework": "custom",
    "runtime": "python-3.11.5",
    "sdk_version": "hiveloop-0.2.0",
    "environment": "production",
    "group": "sales-team"
  },
  "events": [
    {
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2026-02-10T14:32:01.000Z",
      "event_type": "task_started",
      "project_id": "sales-pipeline",
      "task_id": "task_lead-4821",
      "task_type": "lead_processing",
      "task_run_id": "run_abc123",
      "action_id": null,
      "parent_action_id": null,
      "parent_event_id": null,
      "severity": "info",
      "status": null,
      "duration_ms": null,
      "payload": {
        "summary": "New lead processing task received",
        "data": { "source": "webhook" }
      }
    }
  ]
}
```

**Envelope fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `agent_id` | string | **Yes** | Agent identifier. Max 256 chars. |
| `agent_type` | string | No | Agent classification. Default: `"general"`. |
| `agent_version` | string | No | Agent version string. Free-form. |
| `framework` | string | No | Framework name: `"langchain"`, `"crewai"`, `"autogen"`, `"custom"`, etc. |
| `runtime` | string | No | Runtime identifier. Auto-populated by SDK (e.g., `"python-3.11.5"`). |
| `sdk_version` | string | No | HiveLoop version string. Auto-populated by SDK. |
| `environment` | string | No | Operational context. Default: `"production"`. |
| `group` | string | No | Organizational label. Default: `"default"`. |

**Event record fields:**

All fields per the canonical schema in Event Schema Spec v1, Section 4. The only field the client must never send is `tenant_id` (derived from API key) and `received_at` (set by server).

**Required event fields:** `event_id`, `timestamp`, `event_type`.

**New in v2 — per-event fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | No | Project slug or ID. Carried per-event (not on envelope) because events in a single batch may belong to different projects. Null for agent-level events. |

**Server-side processing on ingest:**

1. Validate API key → derive `tenant_id`.
2. Validate batch constraints (max 500 events, max 1 MB).
3. For each event:
   a. Validate required fields (`event_id`, `timestamp`, `event_type`).
   b. Validate `event_type` against allowed enum values.
   c. Validate field size limits (per Event Schema Spec Section 10).
   d. Expand envelope fields into the event record.
   e. Set `received_at` to server timestamp.
   f. Apply severity auto-defaults if `severity` is null (per Event Schema Spec Section 9).
   g. If `project_id` is present, validate it exists for this tenant (or auto-create if tenant settings allow).
   h. If payload has a well-known `kind`, advisorily validate required `data` fields. Log warnings for malformed payloads but do NOT reject the event.
   i. Deduplicate on `(tenant_id, event_id)` — skip silently if duplicate.
4. If event_type is `agent_registered`, upsert the agent profile record keyed by `(tenant_id, agent_id)`.
5. If event has `project_id` and `agent_id`, auto-populate `project_agents` junction.
6. Store valid events. Broadcast to WebSocket subscribers.
7. Evaluate alert rules (including cost threshold rules).

**Success response (200):**

```json
{
  "accepted": 15,
  "rejected": 0,
  "warnings": [],
  "errors": []
}
```

**New in v2 — `warnings` field:** Advisory warnings for payload convention issues. Events with warnings are still accepted.

```json
{
  "accepted": 15,
  "rejected": 0,
  "warnings": [
    {
      "event_id": "550e8400-...",
      "warning": "payload_convention",
      "message": "llm_call payload missing recommended field: data.model"
    }
  ],
  "errors": []
}
```

**Partial success response (207):**

```json
{
  "accepted": 13,
  "rejected": 2,
  "warnings": [],
  "errors": [
    {
      "event_id": "550e8400-...",
      "error": "invalid_event_type",
      "message": "Unknown event_type: 'task_exploded'"
    },
    {
      "event_id": null,
      "error": "missing_required_field",
      "message": "event_id is required"
    }
  ]
}
```

**Full rejection (400):**

Returned when the envelope itself is invalid (missing `agent_id`, malformed JSON, exceeds batch limits).

```json
{
  "error": "invalid_batch",
  "message": "Batch exceeds maximum size of 500 events.",
  "status": 400
}
```

### 3.2 Idempotency

The `event_id` field (UUID, client-generated) is the deduplication key. If the server receives an event with a `(tenant_id, event_id)` pair that already exists, it silently skips the event and counts it as `accepted` (not `rejected`). This makes retries safe and unconditional.

### 3.3 Agent Registration

Agent registration is not a separate endpoint. When the server receives an event with `event_type: "agent_registered"`, it upserts an agent profile:

**Agent profile record (server-side, not part of event table):**

| Field | Source | Description |
|---|---|---|
| `tenant_id` | API key | Tenant isolation. |
| `agent_id` | envelope | Agent identifier. Primary key with tenant_id. |
| `agent_type` | envelope | Classification. |
| `agent_version` | envelope | Last known version. |
| `framework` | envelope | Framework name. |
| `runtime` | envelope | Runtime string. |
| `first_seen` | event timestamp | Timestamp of first `agent_registered` event. |
| `last_seen` | updated on every ingest | Timestamp of most recent event from this agent. |

This profile is updated on every ingest call for the agent (the envelope always carries the current metadata). The profile is a convenience cache — all canonical data lives in the events table.

---

## 4. Query API (Read Path)

These endpoints serve the HiveBoard dashboard. Every endpoint is scoped to the authenticated tenant.

**New in v2:** All query endpoints accept an optional `project_id` parameter. When provided, results are scoped to that project. When omitted, results span all projects (existing v1 behavior).

### 4.1 `GET /v1/agents`

**Dashboard screen:** The Hive (Fleet Overview)

Returns all agents for the tenant with their derived current state.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Filter to agents in this project (via project_agents junction). |
| `environment` | string | `null` (all) | Filter by environment. |
| `group` | string | `null` (all) | Filter by group. |
| `status` | string | `null` (all) | Filter by derived status: `processing`, `idle`, `stuck`, `error`, `waiting_approval`. |
| `sort` | string | `"attention"` | Sort order: `"attention"` (stuck/error first), `"name"`, `"last_seen"`. |
| `limit` | integer | 50 | Max agents returned. |
| `cursor` | string | `null` | Pagination cursor. |

**Response (200):**

```json
{
  "data": [
    {
      "agent_id": "lead-qualifier",
      "agent_type": "sales",
      "agent_version": "1.2.0",
      "framework": "custom",
      "environment": "production",
      "group": "sales-team",
      "derived_status": "processing",
      "current_task_id": "task_lead-4821",
      "current_project_id": "sales-pipeline",
      "last_heartbeat": "2026-02-10T14:32:12.000Z",
      "heartbeat_age_seconds": 4,
      "is_stuck": false,
      "stuck_threshold_seconds": 300,
      "first_seen": "2026-02-01T09:00:00.000Z",
      "last_seen": "2026-02-10T14:32:12.000Z",
      "projects": ["sales-pipeline", "customer-support"],
      "stats_1h": {
        "tasks_completed": 12,
        "tasks_failed": 1,
        "success_rate": 0.923,
        "avg_duration_ms": 11200,
        "total_cost": 0.96,
        "llm_call_count": 48,
        "throughput": 12
      },
      "sparkline_1h": [3, 5, 4, 6, 8, 7, 5, 6, 4, 3, 7, 8]
    }
  ],
  "pagination": { "cursor": "...", "has_more": false }
}
```

**New fields in v2:**

| Field | Type | Description |
|---|---|---|
| `current_project_id` | string \| null | Project of the agent's current/most recent task. |
| `projects` | string[] | List of project slugs this agent participates in. |
| `stats_1h.llm_call_count` | integer | Number of LLM calls in the last hour. |

When `project_id` is provided, `stats_1h` reflects only that project's work. Agent health (derived_status, is_stuck) is always computed globally — an agent's liveness is not project-scoped.

**Derived status computation (server-side):**

The server computes `derived_status` for each agent using the following priority cascade:

| Priority | Condition | Status |
|---|---|---|
| 1 | No heartbeat within `stuck_threshold_seconds` | `"stuck"` |
| 2 | Most recent event is `task_failed` or `action_failed` | `"error"` |
| 3 | Most recent event is `approval_requested` | `"waiting_approval"` |
| 4 | Most recent event is `task_started` or `action_started` | `"processing"` |
| 5 | Everything else (last event is `task_completed`, `heartbeat`, or `agent_registered`) | `"idle"` |

Stuck detection always takes priority. An agent can be simultaneously `stuck` and have its last meaningful event be `task_started` — it shows as `stuck`, not `processing`.

### 4.2 `GET /v1/agents/{agent_id}`

**Dashboard screen:** Agent Detail

Returns a single agent's full profile and derived state. Same response shape as a single item from `GET /v1/agents`, but includes extended metrics.

**Additional fields in response:**

```json
{
  "agent_id": "lead-qualifier",
  "...": "...same as list response...",
  "stats_24h": {
    "tasks_completed": 142,
    "tasks_failed": 8,
    "success_rate": 0.947,
    "avg_duration_ms": 10800,
    "total_cost": 11.20,
    "llm_call_count": 568,
    "escalation_rate": 0.12,
    "error_rate": 0.053,
    "recovery_rate": 0.75,
    "avg_actions_per_task": 4.2
  },
  "metrics_timeseries": {
    "interval": "5m",
    "buckets": [
      {
        "timestamp": "2026-02-10T14:00:00Z",
        "tasks_completed": 3,
        "tasks_failed": 0,
        "avg_duration_ms": 9800,
        "cost": 0.24,
        "llm_call_count": 12
      }
    ]
  },
  "heartbeat_history": [
    {
      "timestamp": "2026-02-10T14:32:30.000Z",
      "summary": "Idle, queue depth: 3",
      "data": { "queue_depth": 3, "current_state": "idle" }
    }
  ]
}
```

**New fields in v2:**

| Field | Type | Description |
|---|---|---|
| `stats_24h.llm_call_count` | integer | LLM calls in the stats range. |
| `metrics_timeseries.buckets[].llm_call_count` | integer | LLM calls per bucket. |
| `heartbeat_history` | object[] | Recent heartbeats that carried payloads. Max 24 entries (one per hour after compaction). Only present when heartbeats have payloads. |

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Scope stats and metrics to this project. Agent health is always global. |
| `metrics_range` | string | `"24h"` | Time range for `stats` and `metrics_timeseries`: `"1h"`, `"6h"`, `"24h"`, `"7d"`, `"30d"`. |
| `metrics_interval` | string | auto | Bucket interval for timeseries: `"1m"`, `"5m"`, `"1h"`, `"1d"`. Auto-selects based on range if omitted. |

**404 if agent not found:**

```json
{
  "error": "agent_not_found",
  "message": "No agent with id 'bad-agent' in this workspace.",
  "status": 404
}
```

### 4.3 `GET /v1/tasks`

**Dashboard screen:** Tasks table (center panel)

Returns a paginated list of tasks derived from task lifecycle events.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Filter to tasks in this project. |
| `agent_id` | string | `null` (all) | Filter by agent. |
| `task_type` | string | `null` (all) | Filter by task type. |
| `status` | string | `null` (all) | Filter by derived status: `completed`, `failed`, `processing`, `stuck`, `waiting`, `escalated`. |
| `environment` | string | `null` (all) | Filter by environment. |
| `group` | string | `null` (all) | Filter by group. |
| `since` | ISO 8601 | `null` | Only tasks started after this time. |
| `until` | ISO 8601 | `null` | Only tasks started before this time. |
| `sort` | string | `"newest"` | Sort: `"newest"`, `"oldest"`, `"duration"`, `"cost"`. |
| `limit` | integer | 50 | Max items. Max: 200. |
| `cursor` | string | `null` | Pagination cursor. |

**Response (200):**

```json
{
  "data": [
    {
      "task_id": "task_lead-4821",
      "task_type": "lead_processing",
      "task_run_id": "run_abc123",
      "agent_id": "lead-qualifier",
      "project_id": "sales-pipeline",
      "derived_status": "completed",
      "started_at": "2026-02-10T14:32:01.000Z",
      "completed_at": "2026-02-10T14:32:13.400Z",
      "duration_ms": 12400,
      "total_cost": 0.08,
      "total_tokens_in": 4500,
      "total_tokens_out": 600,
      "llm_call_count": 3,
      "action_count": 5,
      "error_count": 0,
      "has_escalation": true,
      "has_human_intervention": true
    }
  ],
  "pagination": { "cursor": "...", "has_more": true }
}
```

**New fields in v2:**

| Field | Type | Description |
|---|---|---|
| `project_id` | string \| null | Project this task belongs to. |
| `total_tokens_in` | integer \| null | Sum of `tokens_in` across all `llm_call` events in this task. |
| `total_tokens_out` | integer \| null | Sum of `tokens_out` across all `llm_call` events in this task. |
| `llm_call_count` | integer | Number of LLM call events in this task. |

**Task derived status computation:**

| Condition | Status |
|---|---|
| Has `task_completed` event | `"completed"` |
| Has `task_failed` event | `"failed"` |
| Has `escalated` event, no completion | `"escalated"` |
| Has `approval_requested`, no `approval_received` | `"waiting"` |
| Agent is stuck (no heartbeat) while task is open | `"stuck"` |
| Has `task_started`, no terminal event | `"processing"` |

The `total_cost` field is the sum of `payload.data.cost` values from `custom` events with `kind: "llm_call"` within this task. If no LLM call events exist, this field is `null`.

### 4.4 `GET /v1/tasks/{task_id}/timeline`

**Dashboard screen:** Task Timeline (center panel)

Returns the full ordered event sequence for a single task, structured for timeline rendering.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_run_id` | string | `null` (latest) | Specific execution run. If omitted, returns the most recent run. |

**Response (200):**

```json
{
  "task_id": "task_lead-4821",
  "task_run_id": "run_abc123",
  "agent_id": "lead-qualifier",
  "project_id": "sales-pipeline",
  "task_type": "lead_processing",
  "derived_status": "completed",
  "started_at": "2026-02-10T14:32:01.000Z",
  "completed_at": "2026-02-10T14:32:13.400Z",
  "duration_ms": 12400,
  "total_cost": 0.08,
  "total_tokens_in": 4500,
  "total_tokens_out": 600,
  "llm_call_count": 3,
  "events": [
    {
      "event_id": "550e8400-...",
      "event_type": "task_started",
      "timestamp": "2026-02-10T14:32:01.000Z",
      "severity": "info",
      "status": null,
      "duration_ms": null,
      "action_id": null,
      "parent_action_id": null,
      "parent_event_id": null,
      "payload": {
        "summary": "New lead processing task received",
        "data": { "source": "webhook" }
      },
      "render_hint": null
    },
    {
      "event_id": "550e8401-...",
      "event_type": "custom",
      "timestamp": "2026-02-10T14:32:02.100Z",
      "severity": "info",
      "status": null,
      "duration_ms": null,
      "action_id": "act_001",
      "parent_action_id": null,
      "parent_event_id": null,
      "payload": {
        "kind": "llm_call",
        "summary": "phase1_reasoning → claude-sonnet (1500 in / 200 out, $0.003)",
        "data": {
          "name": "phase1_reasoning",
          "model": "claude-sonnet-4-20250514",
          "tokens_in": 1500,
          "tokens_out": 200,
          "cost": 0.003,
          "prompt_preview": "You are analyzing...",
          "response_preview": "{\"tool\": \"crm_search\"...}"
        }
      },
      "render_hint": "llm_call"
    }
  ],
  "action_tree": [
    {
      "action_id": "act_001",
      "action_name": "fetch_crm_data",
      "parent_action_id": null,
      "started_at": "2026-02-10T14:32:01.400Z",
      "duration_ms": 1800,
      "status": "success",
      "children": []
    }
  ],
  "error_chains": [
    {
      "original_event_id": "evt_err_001",
      "chain": ["evt_err_001", "evt_retry_001", "evt_retry_fail_001", "evt_retry_002"]
    }
  ],
  "plan": null
}
```

**New fields in v2:**

| Field | Type | Description |
|---|---|---|
| `project_id` | string \| null | Project this task belongs to. |
| `total_tokens_in` | integer \| null | Sum of input tokens across LLM calls. |
| `total_tokens_out` | integer \| null | Sum of output tokens across LLM calls. |
| `llm_call_count` | integer | Number of LLM call events. |
| `events[].render_hint` | string \| null | Hint for dashboard rendering. Derived from `payload.kind`. Values: `"llm_call"`, `"plan_created"`, `"plan_step"`, `"reflection"`, `"issue"`, `"queue_snapshot"`, `"todo"`, `"scheduled"`, `null`. |
| `plan` | object \| null | Plan progress structure (see below). Null if no `plan_created` or `plan_step` events exist. |

**`render_hint` derivation:** The server inspects `payload.kind` for each `custom` event. If `kind` matches a well-known value (`llm_call`, `plan_created`, `plan_step`, `reflection`, `issue`, `queue_snapshot`, `todo`, `scheduled`), `render_hint` is set to that value. All other events have `render_hint: null`. This avoids forcing clients to parse payloads for rendering decisions.

**`plan` structure (new in v2):**

When the timeline contains events with `payload.kind = "plan_created"` or `"plan_step"`, the server extracts them into a plan structure:

```json
{
  "plan": {
    "goal": "Process inbound lead",
    "revision": 0,
    "total_steps": 4,
    "steps": [
      {
        "step_index": 0,
        "description": "Search CRM for active deals",
        "action": "completed",
        "started_at": "2026-02-10T14:32:01.500Z",
        "completed_at": "2026-02-10T14:32:05.200Z",
        "turns": 2,
        "tokens": 3200
      },
      {
        "step_index": 1,
        "description": "Qualify leads against ICP",
        "action": "started",
        "started_at": "2026-02-10T14:32:05.300Z",
        "completed_at": null,
        "turns": null,
        "tokens": null
      },
      {
        "step_index": 2,
        "description": "Score and rank leads",
        "action": "pending",
        "started_at": null,
        "completed_at": null,
        "turns": null,
        "tokens": null
      },
      {
        "step_index": 3,
        "description": "Route to sales reps",
        "action": "pending",
        "started_at": null,
        "completed_at": null,
        "turns": null,
        "tokens": null
      }
    ]
  }
}
```

**Plan construction logic:**

1. If a `plan_created` event exists, the server uses its `data.steps` array to seed the step list and extracts `goal` and `revision`.
2. The server then processes `plan_step` events chronologically. For each `step_index`, the latest event determines the step's `action` and timestamps.
3. Steps with no corresponding `plan_step` event are marked `"pending"`.
4. If no `plan_created` event exists but `plan_step` events do, the server infers `total_steps` from any `plan_step` event's `data.total_steps` field and step descriptions from `plan_step` summaries.
5. If `data.plan_revision` is present on a `plan_step` event, it updates the plan's `revision` field. A revision change indicates the agent re-planned mid-execution.

**Response structure rationale:**

- `events`: Flat chronological list. The dashboard uses this for the horizontal timeline visualization.
- `action_tree`: Hierarchical structure built from `action_id` / `parent_action_id`. Used for nested action rendering (if a tracked function calls another tracked function).
- `error_chains`: Linked sequences built from `parent_event_id`. Used for the error branch visualization.
- `plan`: Extracted plan progress. Used for the progress bar above the timeline.

### 4.5 `GET /v1/events`

**Dashboard screen:** Activity Stream (right panel)

Returns a reverse-chronological stream of events across the workspace.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Filter to events in this project. Agent-level events for agents in this project are included. |
| `agent_id` | string | `null` (all) | Filter to a single agent. |
| `task_id` | string | `null` (all) | Filter to a single task. |
| `event_type` | string (comma-sep) | `null` (all) | Filter by type(s): `"task_started,task_completed"`. |
| `severity` | string (comma-sep) | `null` (all) | Filter by severity: `"error,warn"`. |
| `environment` | string | `null` (all) | Filter by environment. |
| `group` | string | `null` (all) | Filter by group. |
| `payload_kind` | string (comma-sep) | `null` (all) | Filter by payload kind: `"llm_call,issue"`. Only matches `custom` events with matching `payload.kind`. |
| `since` | ISO 8601 | `null` | Events after this time. |
| `until` | ISO 8601 | `null` | Events before this time. |
| `exclude_heartbeats` | boolean | `true` | Exclude heartbeat events (noisy in the stream). |
| `limit` | integer | 50 | Max items. Max: 200. |
| `cursor` | string | `null` | Pagination cursor. |

**New parameters in v2:**

| Parameter | Type | Description |
|---|---|---|
| `project_id` | string | Project scope. When set, includes both project-scoped events and agent-level events for agents that participate in this project. |
| `payload_kind` | string | Filters `custom` events by `payload.kind`. Useful for viewing only LLM calls, only issues, etc. |

**Response (200):**

```json
{
  "data": [
    {
      "event_id": "550e8400-...",
      "agent_id": "lead-qualifier",
      "agent_type": "sales",
      "project_id": "sales-pipeline",
      "task_id": "task_lead-4821",
      "event_type": "task_completed",
      "timestamp": "2026-02-10T14:32:13.400Z",
      "severity": "info",
      "status": "success",
      "duration_ms": 12400,
      "payload": {
        "summary": "Lead scored 42 → escalated → approved",
        "kind": "completion"
      },
      "render_hint": null
    }
  ],
  "pagination": { "cursor": "...", "has_more": true }
}
```

**New fields in v2:** `project_id` and `render_hint` on each event.

### 4.6 `GET /v1/metrics`

**Dashboard screen:** Summary bar + Metrics sparkline charts

Returns aggregated metrics across the workspace or for a specific agent.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Scope metrics to this project. |
| `agent_id` | string | `null` (all) | Scope to one agent. |
| `environment` | string | `null` (all) | Filter by environment. |
| `group` | string | `null` (all) | Filter by group. |
| `range` | string | `"1h"` | Time range: `"1h"`, `"6h"`, `"24h"`, `"7d"`, `"30d"`. |
| `interval` | string | auto | Bucket interval: `"1m"`, `"5m"`, `"15m"`, `"1h"`, `"1d"`. |

**Response (200):**

```json
{
  "range": "1h",
  "interval": "5m",
  "summary": {
    "total_tasks": 47,
    "completed": 41,
    "failed": 3,
    "escalated": 2,
    "stuck": 1,
    "success_rate": 0.872,
    "avg_duration_ms": 9200,
    "total_cost": 2.14,
    "avg_cost_per_task": 0.046,
    "llm_call_count": 188,
    "total_tokens_in": 282000,
    "total_tokens_out": 37600
  },
  "timeseries": [
    {
      "timestamp": "2026-02-10T13:35:00Z",
      "tasks_completed": 4,
      "tasks_failed": 0,
      "avg_duration_ms": 8400,
      "cost": 0.18,
      "llm_call_count": 16,
      "error_count": 0,
      "throughput": 4
    }
  ]
}
```

**New fields in v2:**

| Field | Type | Description |
|---|---|---|
| `summary.llm_call_count` | integer | Total LLM calls in range. |
| `summary.total_tokens_in` | integer | Total input tokens in range. |
| `summary.total_tokens_out` | integer | Total output tokens in range. |
| `timeseries[].llm_call_count` | integer | LLM calls per bucket. |

**Auto-interval selection:**

| Range | Default Interval |
|---|---|
| 1h | 5m |
| 6h | 15m |
| 24h | 1h |
| 7d | 6h |
| 30d | 1d |

### 4.7 `GET /v1/cost`

**New in v2. Dashboard screen:** Cost Explorer

Returns cost aggregation for the tenant, grouped by agent, model, or both.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Scope to this project. |
| `agent_id` | string | `null` (all) | Scope to one agent. |
| `environment` | string | `null` (all) | Filter by environment. |
| `group_by` | string | `"agent"` | Grouping: `"agent"`, `"model"`, `"agent_model"`. |
| `since` | ISO 8601 | 24h ago | Start of time range. |
| `until` | ISO 8601 | now | End of time range. |

**Response (200) — `group_by=agent`:**

```json
{
  "range": { "since": "2026-02-09T14:00:00Z", "until": "2026-02-10T14:00:00Z" },
  "totals": {
    "cost": 11.20,
    "call_count": 568,
    "tokens_in": 852000,
    "tokens_out": 113600
  },
  "groups": [
    {
      "agent_id": "lead-qualifier",
      "call_count": 340,
      "tokens_in": 510000,
      "tokens_out": 68000,
      "cost": 6.72,
      "avg_cost_per_call": 0.0198
    },
    {
      "agent_id": "support-triage",
      "call_count": 228,
      "tokens_in": 342000,
      "tokens_out": 45600,
      "cost": 4.48,
      "avg_cost_per_call": 0.0196
    }
  ]
}
```

**Response (200) — `group_by=model`:**

```json
{
  "range": { "since": "...", "until": "..." },
  "totals": { "cost": 11.20, "call_count": 568, "tokens_in": 852000, "tokens_out": 113600 },
  "groups": [
    {
      "model": "claude-sonnet-4-20250514",
      "call_count": 400,
      "tokens_in": 600000,
      "tokens_out": 80000,
      "cost": 8.40,
      "avg_cost_per_call": 0.0210
    },
    {
      "model": "claude-haiku-4-5-20251001",
      "call_count": 168,
      "tokens_in": 252000,
      "tokens_out": 33600,
      "cost": 2.80,
      "avg_cost_per_call": 0.0167
    }
  ]
}
```

**Response (200) — `group_by=agent_model`:**

```json
{
  "range": { "since": "...", "until": "..." },
  "totals": { "cost": 11.20, "call_count": 568, "tokens_in": 852000, "tokens_out": 113600 },
  "groups": [
    {
      "agent_id": "lead-qualifier",
      "model": "claude-sonnet-4-20250514",
      "call_count": 240,
      "tokens_in": 360000,
      "tokens_out": 48000,
      "cost": 5.04,
      "avg_cost_per_call": 0.0210
    }
  ]
}
```

### 4.8 `GET /v1/cost/calls`

**New in v2. Dashboard screen:** Cost Explorer — Recent Calls table

Returns individual LLM call events with extracted cost fields.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Scope to this project. |
| `agent_id` | string | `null` (all) | Filter by agent. |
| `model` | string | `null` (all) | Filter by model identifier. |
| `task_id` | string | `null` (all) | Filter by task. |
| `environment` | string | `null` (all) | Filter by environment. |
| `since` | ISO 8601 | `null` | Start of time range. |
| `until` | ISO 8601 | `null` | End of time range. |
| `sort` | string | `"newest"` | Sort: `"newest"`, `"oldest"`, `"cost"`, `"tokens"`. |
| `limit` | integer | 50 | Max items. Max: 200. |
| `cursor` | string | `null` | Pagination cursor. |

**Response (200):**

```json
{
  "data": [
    {
      "event_id": "550e8401-...",
      "agent_id": "lead-qualifier",
      "task_id": "task_lead-4821",
      "project_id": "sales-pipeline",
      "timestamp": "2026-02-10T14:32:02.100Z",
      "call_name": "phase1_reasoning",
      "model": "claude-sonnet-4-20250514",
      "tokens_in": 1500,
      "tokens_out": 200,
      "cost": 0.003,
      "duration_ms": 1200,
      "prompt_preview": "You are analyzing a sales lead...",
      "response_preview": "{\"tool\": \"crm_search\", ...}"
    }
  ],
  "pagination": { "cursor": "...", "has_more": true }
}
```

Fields are extracted from the `custom` event's `payload.data`. If any field is missing from the payload, it is `null` in the response.

### 4.9 `GET /v1/cost/timeseries`

**New in v2. Dashboard screen:** Cost Explorer — Cost chart

Returns cost aggregated into time buckets, optionally split by model.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Scope to this project. |
| `agent_id` | string | `null` (all) | Filter by agent. |
| `environment` | string | `null` (all) | Filter by environment. |
| `since` | ISO 8601 | 24h ago | Start of time range. |
| `until` | ISO 8601 | now | End of time range. |
| `interval` | string | auto | Bucket interval: `"5m"`, `"15m"`, `"1h"`, `"1d"`. |
| `split_by_model` | boolean | `false` | If true, each bucket is split into per-model rows. |

**Response (200) — `split_by_model=false`:**

```json
{
  "range": { "since": "...", "until": "..." },
  "interval": "1h",
  "buckets": [
    {
      "timestamp": "2026-02-10T13:00:00Z",
      "call_count": 48,
      "tokens_in": 72000,
      "tokens_out": 9600,
      "cost": 0.96
    }
  ]
}
```

**Response (200) — `split_by_model=true`:**

```json
{
  "range": { "since": "...", "until": "..." },
  "interval": "1h",
  "buckets": [
    {
      "timestamp": "2026-02-10T13:00:00Z",
      "model": "claude-sonnet-4-20250514",
      "call_count": 32,
      "tokens_in": 48000,
      "tokens_out": 6400,
      "cost": 0.67
    },
    {
      "timestamp": "2026-02-10T13:00:00Z",
      "model": "claude-haiku-4-5-20251001",
      "call_count": 16,
      "tokens_in": 24000,
      "tokens_out": 3200,
      "cost": 0.29
    }
  ]
}
```

### 4.10 `GET /v1/agents/{agent_id}/pipeline`

**New in v2. Dashboard screen:** Agent Detail — Pipeline tab

Returns the most recent state for each work pipeline category: queue, TODOs, and scheduled items. This endpoint aggregates `custom` events with well-known payload kinds (`queue_snapshot`, `todo`, `scheduled`) for the given agent.

**Response (200):**

```json
{
  "queue": {
    "last_updated": "2026-02-11T14:30:00Z",
    "depth": 4,
    "oldest_age_seconds": 120,
    "items": [
      {
        "id": "evt_001",
        "priority": "high",
        "source": "human",
        "summary": "Review contract draft",
        "queued_at": "2026-02-11T14:28:00Z"
      },
      {
        "id": "evt_002",
        "priority": "normal",
        "source": "webhook",
        "summary": "Process CRM update",
        "queued_at": "2026-02-11T14:29:00Z"
      }
    ],
    "processing": {
      "id": "evt_003",
      "summary": "Sending email",
      "started_at": "2026-02-11T14:29:30Z",
      "elapsed_ms": 4500
    }
  },
  "todos": {
    "last_updated": "2026-02-11T14:28:00Z",
    "active_count": 3,
    "completed_count": 2,
    "items": [
      {
        "todo_id": "todo_retry_crm",
        "action": "created",
        "summary": "Retry: CRM write failed (403)",
        "priority": "high",
        "source": "failed_action",
        "created_at": "2026-02-11T14:20:00Z"
      }
    ]
  },
  "scheduled": {
    "last_updated": "2026-02-11T14:00:00Z",
    "items": [
      {
        "id": "sched_crm_sync",
        "name": "CRM Pipeline Sync",
        "next_run": "2026-02-11T15:00:00Z",
        "interval": "1h",
        "enabled": true,
        "last_status": "success"
      }
    ]
  }
}
```

**Implementation:**

- **Queue**: Returns the most recent event where `payload.kind = "queue_snapshot"` for this agent. Fields are extracted directly from the event's `payload.data`.
- **TODOs**: Aggregates all events where `payload.kind = "todo"` for this agent using the following logic:
  1. Fetch all `todo` events, grouped by `payload.data.todo_id`.
  2. For each group, take the most recent event.
  3. Return items where the most recent `data.action` is NOT `"completed"` or `"dismissed"` (i.e., still active).
  4. `active_count` and `completed_count` are derived from these groups.
- **Scheduled**: Returns the most recent event where `payload.kind = "scheduled"` for this agent. Items are extracted from `payload.data.items`.

If no events exist for a category, that key is `null` in the response.

**404 if agent not found:**

```json
{
  "error": "agent_not_found",
  "message": "No agent with id 'bad-agent' in this workspace.",
  "status": 404
}
```

### 4.11 `GET /v1/projects`

**New in v2. Dashboard screen:** Project selector / Project list

Returns all projects for the tenant with summary stats.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `include_archived` | boolean | `false` | Include archived projects. |

**Response (200):**

```json
{
  "data": [
    {
      "project_id": "sales-pipeline",
      "name": "Sales Pipeline",
      "slug": "sales-pipeline",
      "description": "Lead qualification and routing",
      "environment": "production",
      "is_archived": false,
      "created_at": "2026-01-15T09:00:00Z",
      "agent_count": 3,
      "tasks_completed_24h": 142,
      "tasks_failed_24h": 8,
      "cost_24h": 11.20
    }
  ]
}
```

### 4.12 `POST /v1/projects`

**New in v2.** Creates a new project.

**Request body:**

```json
{
  "name": "Sales Pipeline",
  "slug": "sales-pipeline",
  "description": "Lead qualification and routing",
  "environment": "production",
  "settings": {}
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | **Yes** | Display name. |
| `slug` | string | **Yes** | URL-safe identifier. Must be unique within tenant. |
| `description` | string | No | Optional description. |
| `environment` | string | No | Default environment. Default: `"production"`. |
| `settings` | object | No | Project-level config overrides. |

**Response (201):**

```json
{
  "project_id": "proj_abc123",
  "name": "Sales Pipeline",
  "slug": "sales-pipeline",
  "description": "Lead qualification and routing",
  "environment": "production",
  "settings": {},
  "is_archived": false,
  "created_at": "2026-02-11T10:00:00Z"
}
```

### 4.13 `GET /v1/projects/{project_id}`

**New in v2.** Returns project details with summary stats. Same shape as a single item from `GET /v1/projects`.

### 4.14 `PUT /v1/projects/{project_id}`

**New in v2.** Updates a project. Same body as POST (all fields optional on update).

### 4.15 `DELETE /v1/projects/{project_id}`

**New in v2.** Deletes a project and cascades: removes all events with this `project_id`, removes `project_agents` rows, removes project-scoped alert rules.

**Response:** 204 No Content.

**Cannot delete the default project.** Returns 400:

```json
{
  "error": "cannot_delete_default_project",
  "message": "The default project cannot be deleted.",
  "status": 400
}
```

### 4.16 `POST /v1/projects/{project_id}/archive`

**New in v2.** Archives the project. Hidden from dashboard but data retained.

### 4.17 `POST /v1/projects/{project_id}/unarchive`

**New in v2.** Unarchives a previously archived project.

### 4.18 `GET /v1/projects/{project_id}/agents`

**New in v2.** Lists agents assigned to this project.

**Response (200):** Same shape as `GET /v1/agents` but filtered to this project.

### 4.19 `POST /v1/projects/{project_id}/agents`

**New in v2.** Manually assigns an agent to this project.

**Request body:**

```json
{
  "agent_id": "lead-qualifier"
}
```

**Response:** 201 Created. Returns the project_agents record.

### 4.20 `DELETE /v1/projects/{project_id}/agents/{agent_id}`

**New in v2.** Removes an agent from this project. Does not delete the agent or its events.

**Response:** 204 No Content.

---

## 5. WebSocket API (Real-Time)

### 5.1 Connection

```
wss://api.hiveboard.io/v1/stream?token={api_key}
```

The API key is passed as a query parameter (WebSocket doesn't support custom headers in browser contexts). Connection is authenticated on upgrade — rejected immediately if the key is invalid.

For local development:

```
ws://localhost:8000/v1/stream?token={api_key}
```

### 5.2 Subscription Model

After connecting, the client sends a subscription message to declare which events it wants to receive:

**Client → Server (subscribe):**

```json
{
  "action": "subscribe",
  "channels": ["events", "agents"],
  "filters": {
    "project_id": "sales-pipeline",
    "environment": "production",
    "agent_id": null,
    "event_types": null,
    "min_severity": "info"
  }
}
```

**Available channels:**

| Channel | Description | Message types |
|---|---|---|
| `events` | Real-time event stream (Activity Stream) | `event.new` |
| `agents` | Agent status changes (The Hive) | `agent.status_changed`, `agent.stuck`, `agent.heartbeat` |

**Filter fields (all optional):**

| Field | Type | Description |
|---|---|---|
| `project_id` | string | Only events from this project (and agent-level events for agents in this project). **New in v2.** |
| `environment` | string | Only events from this environment. |
| `group` | string | Only events from this group. |
| `agent_id` | string | Only events from this agent. |
| `event_types` | string[] | Only these event types. |
| `min_severity` | string | Minimum severity: `"debug"`, `"info"`, `"warn"`, `"error"`. Default: `"info"` (excludes heartbeats). |

Filters can be updated at any time by sending another `subscribe` message. The new filters replace the old ones entirely (not merged).

**Server → Client (subscription confirmation):**

```json
{
  "type": "subscribed",
  "channels": ["events", "agents"],
  "filters": { "...applied filters..." }
}
```

### 5.3 Message Types

**`event.new` — New event ingested:**

```json
{
  "type": "event.new",
  "data": {
    "event_id": "550e8400-...",
    "agent_id": "lead-qualifier",
    "agent_type": "sales",
    "project_id": "sales-pipeline",
    "task_id": "task_lead-4821",
    "event_type": "action_completed",
    "timestamp": "2026-02-10T14:32:05.300Z",
    "severity": "info",
    "status": "success",
    "duration_ms": 3400,
    "payload": {
      "summary": "score_lead completed in 3.4s",
      "action_name": "score_lead"
    },
    "render_hint": null
  }
}
```

**New in v2:** `project_id` and `render_hint` fields on `event.new` messages.

**`agent.status_changed` — Agent derived status changed:**

```json
{
  "type": "agent.status_changed",
  "data": {
    "agent_id": "support-triage",
    "previous_status": "processing",
    "new_status": "stuck",
    "timestamp": "2026-02-10T14:32:00.000Z",
    "current_task_id": "task_ticket-991",
    "current_project_id": "customer-support",
    "heartbeat_age_seconds": 312
  }
}
```

**New in v2:** `current_project_id` field.

**`agent.stuck` — Agent stuck alert (fired once when threshold crossed):**

```json
{
  "type": "agent.stuck",
  "data": {
    "agent_id": "support-triage",
    "last_heartbeat": "2026-02-10T14:26:48.000Z",
    "stuck_threshold_seconds": 300,
    "current_task_id": "task_ticket-991",
    "current_project_id": "customer-support"
  }
}
```

### 5.4 Heartbeat (Keep-Alive)

The server sends a ping every 30 seconds. The client must respond with a pong (standard WebSocket ping/pong). If 3 consecutive pings go unanswered, the server closes the connection.

The client can also send:

```json
{ "action": "ping" }
```

Server responds:

```json
{ "type": "pong", "server_time": "2026-02-10T14:32:00.000Z" }
```

### 5.5 Unsubscribe / Disconnect

```json
{ "action": "unsubscribe", "channels": ["agents"] }
```

Or simply close the WebSocket connection. No cleanup required.

---

## 6. Alerting API

### 6.1 `GET /v1/alerts/rules`

Returns configured alert rules.

**Query parameters (new in v2):**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `null` (all) | Filter to rules scoped to this project (plus tenant-wide rules with no project). |

### 6.2 `POST /v1/alerts/rules`

Creates a new alert rule.

**Request body:**

```json
{
  "name": "Agent stuck > 5 minutes",
  "project_id": null,
  "condition": {
    "type": "agent_stuck",
    "threshold_seconds": 300
  },
  "filters": {
    "environment": "production",
    "agent_id": null,
    "group": null
  },
  "actions": [
    {
      "type": "webhook",
      "url": "https://hooks.slack.com/services/...",
      "headers": { "Content-Type": "application/json" }
    },
    {
      "type": "email",
      "to": ["ops@acme.com"]
    }
  ],
  "cooldown_seconds": 300,
  "enabled": true
}
```

**New in v2:** `project_id` field. If set, the rule fires only for events in this project. If null, it fires for all projects.

**Supported condition types:**

| Type | Parameters | Description |
|---|---|---|
| `agent_stuck` | `threshold_seconds` | Agent has no heartbeat for N seconds. |
| `task_failed` | `count`, `window_seconds` | N task failures within time window. |
| `error_rate` | `threshold_percent`, `window_seconds` | Error rate exceeds X% over window. |
| `duration_exceeded` | `threshold_ms` | Any task exceeds duration threshold. |
| `heartbeat_lost` | `agent_id` | Specific agent stops sending heartbeats. |
| `cost_threshold` | `threshold_usd`, `window_hours`, `scope`, `agent_id` | Cumulative LLM cost exceeds threshold within rolling window. **New in v2.** |

**`cost_threshold` condition detail (new in v2):**

```json
{
  "type": "cost_threshold",
  "threshold_usd": 50.00,
  "window_hours": 24,
  "scope": "agent",
  "agent_id": "lead-qualifier"
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `threshold_usd` | number | Yes | Cost threshold in USD. |
| `window_hours` | number | Yes | Rolling time window in hours. |
| `scope` | string | Yes | `"agent"` (per-agent), `"project"` (all agents in project), `"tenant"` (all agents). |
| `agent_id` | string | When `scope = "agent"` | Required when scope is per-agent. |

The evaluator sums `payload.data.cost` from `custom` events with `kind: "llm_call"` within the time window.

**Supported action types:**

| Type | Fields | Description |
|---|---|---|
| `webhook` | `url`, `headers` (optional) | HTTP POST with alert payload. |
| `email` | `to` (string[]) | Email notification. |

### 6.3 `PUT /v1/alerts/rules/{rule_id}`

Update an existing rule. Same body as POST.

### 6.4 `DELETE /v1/alerts/rules/{rule_id}`

Delete a rule. Returns 204 No Content.

### 6.5 `GET /v1/alerts/history`

Returns fired alerts, paginated.

**Query params:** `project_id` (new in v2), `rule_id`, `since`, `until`, `limit`, `cursor`.

---

## 7. Error Code Reference

| Code | HTTP | When |
|---|---|---|
| `authentication_failed` | 401 | Missing or invalid API key. |
| `read_only_key` | 403 | Write attempt with `hb_read_` key. |
| `rate_limit_exceeded` | 429 | Request rate exceeded. |
| `invalid_batch` | 400 | Batch envelope malformed, exceeds size limits, or missing `agent_id`. |
| `invalid_event_type` | 400 | Event has unknown `event_type`. Returned per-event in partial success. |
| `missing_required_field` | 400 | Event missing `event_id`, `timestamp`, or `event_type`. |
| `field_size_exceeded` | 400 | Field exceeds max size (payload > 32KB, agent_id > 256 chars, etc.). |
| `invalid_project` | 400 | Event references a `project_id` that doesn't exist and auto-create is disabled. **New in v2.** |
| `invalid_parameter` | 400 | Query parameter has invalid value. |
| `agent_not_found` | 404 | Agent ID does not exist in this workspace. |
| `task_not_found` | 404 | Task ID does not exist in this workspace. |
| `project_not_found` | 404 | Project ID does not exist in this workspace. **New in v2.** |
| `rule_not_found` | 404 | Alert rule ID does not exist. |
| `cannot_delete_default_project` | 400 | Attempted to delete the default project. **New in v2.** |
| `internal_error` | 500 | Unexpected server error. |

---

# PART B: HIVELOOP SDK

## 8. Installation and Initialization

### 8.1 Installation

```bash
pip install hiveloop
```

Requirements: Python 3.9+. No required dependencies beyond the standard library + `requests` (HTTP) + `uuid` (stdlib). Optional: `websockets` for future bidirectional features.

### 8.2 Module-Level Init

```python
import hiveloop

hb = hiveloop.init(
    api_key="hb_live_a1b2c3d4...",
    environment="production",        # optional, default: "production"
    group="sales-team",              # optional, default: "default"
    endpoint="https://api.hiveboard.io",  # optional, for self-hosted / dev
    flush_interval=5.0,              # optional, seconds between auto-flushes
    batch_size=100,                  # optional, max events per batch
    max_queue_size=10000,            # optional, buffer capacity
    debug=False                      # optional, enables SDK debug logging
)
```

**Returns:** `HiveBoard` client instance.

**`hiveloop.init()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | str | **required** | API key. Must start with `hb_`. |
| `environment` | str | `"production"` | Operational context. Included in every batch envelope. |
| `group` | str | `"default"` | Organizational label. Included in every batch envelope. |
| `endpoint` | str | `"https://api.hiveboard.io"` | API base URL. Override for local dev or self-hosted. |
| `flush_interval` | float | `5.0` | Seconds between automatic batch flushes. |
| `batch_size` | int | `100` | Max events per HTTP request. Capped at 500 (server limit). |
| `max_queue_size` | int | `10000` | Max events buffered in memory. When full, oldest events are dropped. |
| `debug` | bool | `False` | Logs SDK operations to stderr. |

**Initialization behavior:**

1. Validates API key format.
2. Creates the internal event queue (thread-safe `collections.deque`).
3. Starts the background flush thread (daemon thread).
4. Does NOT make any HTTP call. First contact with the server happens on the first flush.

### 8.3 Singleton Behavior

`hiveloop.init()` stores the client as a module-level singleton. Subsequent calls to `hiveloop.init()` with different parameters log a warning and return the existing instance. To explicitly reinitialize (for testing), call `hiveloop.reset()` first.

```python
hiveloop.reset()  # Flushes remaining events, stops threads, clears singleton
```

---

## 9. Agent Registration

### 9.1 `hb.agent()` — Register an Agent

```python
agent = hb.agent(
    agent_id="lead-qualifier",
    type="sales",                    # optional, default: "general"
    version="1.2.0",                 # optional
    framework="custom",              # optional
    heartbeat_interval=30,           # optional, seconds. Default: 30
    stuck_threshold=300,             # optional, seconds. Default: 300 (5 min)
    heartbeat_payload=None,          # optional, callable. New in v2.
    queue_provider=None              # optional, callable. New in v2.
)
```

**Returns:** `Agent` instance.

**`hb.agent()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_id` | str | **required** | Unique agent identifier. Max 256 chars. Should be a human-readable slug. |
| `type` | str | `"general"` | Agent classification. Appears in dashboard cards. |
| `version` | str | `None` | Agent version. Useful for comparing perf across deploys. |
| `framework` | str | `"custom"` | Framework identifier: `"langchain"`, `"crewai"`, `"autogen"`, `"custom"`. |
| `heartbeat_interval` | int | `30` | Seconds between auto heartbeat pings. Set to `0` to disable. |
| `stuck_threshold` | int | `300` | Seconds without heartbeat before the server considers this agent stuck. Sent as metadata; the server uses this for stuck detection. |
| `heartbeat_payload` | Callable \| None | `None` | **New in v2.** Optional callback invoked before each heartbeat. Return value becomes the heartbeat event's `payload`. See Section 9.3. |
| `queue_provider` | Callable \| None | `None` | **New in v2.** Optional callback invoked on each heartbeat to auto-emit a `queue_snapshot` event. See Section 9.4. |

**Registration behavior:**

1. Enqueues an `agent_registered` event with the agent's metadata in the payload.
2. Starts a background heartbeat thread (daemon) that enqueues a `heartbeat` event every `heartbeat_interval` seconds.
3. Stores the agent in the client's agent registry (allows `hb.get_agent("lead-qualifier")` later).
4. Returns the `Agent` instance.

**Calling `hb.agent()` with the same `agent_id` twice** returns the existing `Agent` instance (idempotent). It does NOT create a duplicate. Metadata (type, version, heartbeat_payload) is updated on the existing instance if different.

### 9.2 Heartbeat Thread Lifecycle

The heartbeat thread:

- Runs as a daemon thread (dies when the main process exits).
- Enqueues `heartbeat` events into the shared event queue.
- Pauses during graceful shutdown (`hiveloop.shutdown()` or `hiveloop.reset()`).
- Is per-agent (each agent has its own heartbeat thread).

Heartbeat events without a payload callback:

```json
{
  "event_id": "auto-generated-uuid",
  "timestamp": "2026-02-10T14:32:30.000Z",
  "event_type": "heartbeat",
  "task_id": null,
  "payload": null
}
```

### 9.3 Heartbeat Payload Callback (New in v2)

When `heartbeat_payload` is provided, the SDK calls it before each heartbeat emission. The return value becomes the heartbeat event's `payload`.

```python
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    heartbeat_payload=lambda: {
        "kind": "heartbeat_status",
        "summary": f"Idle, queue depth: {len(queue)}",
        "data": {
            "queue_depth": len(queue),
            "tasks_completed_since_last": completed_count,
            "current_state": "idle"
        }
    }
)
```

**Heartbeat event with payload:**

```json
{
  "event_id": "auto-generated-uuid",
  "timestamp": "2026-02-10T14:32:30.000Z",
  "event_type": "heartbeat",
  "task_id": null,
  "payload": {
    "kind": "heartbeat_status",
    "summary": "Idle, queue depth: 3",
    "data": {
      "queue_depth": 3,
      "tasks_completed_since_last": 2,
      "current_state": "idle"
    }
  }
}
```

**Error handling:** If the callback raises an exception, the SDK catches it, logs a warning, and emits the heartbeat with `payload: null`. The heartbeat is never skipped due to a callback error.

**Size guidance:** Heartbeat payloads should be kept under 1 KB. Heartbeats are ~60% of event volume — large payloads significantly impact storage and network.

### 9.4 Queue Provider Callback (New in v2)

When `queue_provider` is provided, the SDK calls it alongside each heartbeat and emits a separate `custom` event with `payload.kind = "queue_snapshot"`. This gives automatic work pipeline visibility without requiring explicit `agent.queue_snapshot()` calls.

```python
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    queue_provider=lambda: {
        "depth": len(my_queue),
        "oldest_age_seconds": oldest_item_age(),
        "items": [
            {"id": item.id, "priority": item.priority, "source": item.source,
             "summary": item.summary, "queued_at": item.queued_at}
            for item in my_queue[:10]
        ],
        "processing": {
            "id": current.id, "summary": current.summary,
            "started_at": current.started_at, "elapsed_ms": current.elapsed_ms
        } if current else None
    }
)
```

**Behavior:**

- On each heartbeat cycle, the SDK calls `queue_provider()` and emits a `custom` event with `payload.kind = "queue_snapshot"` and the returned data as `payload.data`.
- The queue snapshot event is emitted as a separate event from the heartbeat (not merged into it). It has `task_id: null` (agent-level).
- `depth=0` is valid — it signals "queue just drained," which is useful for monitoring.
- If the callback raises an exception, the SDK catches it, logs a warning, and skips the queue snapshot for that cycle. The heartbeat is still emitted normally.
- The `queue_provider` and `heartbeat_payload` callbacks are independent. Both can be provided, both can be omitted.

---

## 10. Task Context Manager (Layer 1)

### 10.1 `agent.task()` — Start a Task

```python
with agent.task(
    task_id="task_lead-4821",
    project="sales-pipeline",             # optional, new in v2
    type="lead_processing",               # optional
    task_run_id="run_abc123",             # optional, auto-generated UUID if omitted
    correlation_id="workflow-99"          # optional, for multi-agent workflows
) as task:
    # ... do work ...
    pass
```

**`agent.task()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_id` | str | **required** | Logical task identifier. Max 256 chars. |
| `project` | str | `"default"` | **New in v2.** Project slug or ID. All events within this task context inherit this value as `project_id`. |
| `type` | str | `None` | Task classification. |
| `task_run_id` | str | auto UUID | Disambiguates re-executions. Auto-generated if omitted. |
| `correlation_id` | str | `None` | Cross-agent workflow linkage. Reserved for multi-agent orchestration. |

**Context manager behavior:**

| Moment | What Happens |
|---|---|
| `__enter__` | Enqueues `task_started` event with `project_id`. Sets the task as the active task on the current thread (thread-local). Starts timing. Returns `Task` instance. |
| `__exit__` (no exception) | Enqueues `task_completed` event with `status: "success"` and `duration_ms`. Clears active task. |
| `__exit__` (exception) | Enqueues `task_failed` event with `status: "failure"`, `duration_ms`, and exception info in payload. Clears active task. **Re-raises the exception** — the SDK never swallows errors. |

**Project inheritance:** All events emitted within the task context (`task.event()`, `task.llm_call()`, `@agent.track()` actions, etc.) automatically carry `project_id` from the task. The developer never sets `project_id` on individual events.

### 10.2 Thread-Local Task Context

The active task is stored in a `threading.local()` variable. This means:

- Decorators (`@agent.track`) automatically know which task they belong to.
- Multiple threads can run different tasks concurrently without conflicts.
- If no task context is active, `@agent.track` events are emitted as agent-level events (no `task_id`, no `project_id`).

### 10.3 Non-Context-Manager Usage

For cases where `with` blocks are impractical (e.g., task spans multiple functions or callbacks):

```python
task = agent.start_task("task_lead-4821", project="sales-pipeline", type="lead_processing")
# ... do work across multiple functions ...
task.complete()   # or task.fail(exception)
```

| Method | Effect |
|---|---|
| `agent.start_task(...)` | Same as `agent.task().__enter__()`. Returns `Task`. |
| `task.complete(status="success", payload=None)` | Enqueues `task_completed`. Clears context. |
| `task.fail(exception=None, payload=None)` | Enqueues `task_failed`. Clears context. |

The developer is responsible for calling `complete()` or `fail()`. If neither is called, the task remains "open" indefinitely (stuck detection will eventually flag it).

---

## 11. Action Tracking (Layer 1)

### 11.1 `@agent.track()` — Decorator

```python
@agent.track("evaluate_lead")
def evaluate(lead):
    score = run_scoring_model(lead)
    return score
```

**`@agent.track()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `action_name` | str | **required** (positional) | Logical step name. Appears as the node label in the timeline. |

**Decorator behavior:**

| Moment | What Happens |
|---|---|
| Function called | Generates a unique `action_id` (UUID). Detects `parent_action_id` from the call stack if this is a nested tracked function. Enqueues `action_started` event. Starts timing. |
| Function returns | Enqueues `action_completed` event with `status: "success"`, `duration_ms`, and auto-populated payload (see below). |
| Function raises | Enqueues `action_failed` event with `status: "failure"`, `duration_ms`, exception info in payload. **Re-raises the exception.** |

**Auto-populated payload for action events:**

```json
{
  "action_name": "evaluate_lead",
  "function": "myapp.scoring.evaluate",
  "exception_type": "ValueError",
  "exception_message": "Invalid lead format"
}
```

`exception_type` and `exception_message` are only present on `action_failed` events.

### 11.2 Nesting Detection

When tracked functions call other tracked functions, the SDK automatically builds the `parent_action_id` chain:

```python
@agent.track("process_lead")
def process(lead):
    data = fetch_data(lead)       # action_id: act_001, parent: null
    enriched = enrich(data)       # action_id: act_002, parent: null

@agent.track("fetch_data")
def fetch_data(lead):             # action_id: act_003, parent: act_001
    return crm.get(lead.id)

@agent.track("enrich")
def enrich(data):                 # action_id: act_004, parent: act_001
    return clearbit.lookup(data)
```

**Implementation:** The current `action_id` is stored in a `contextvars.ContextVar` (Python 3.7+). When a tracked function starts, it reads the current `action_id` as its `parent_action_id`, then sets its own `action_id` as the current one. On exit, it restores the previous value. This correctly handles both threads and async.

### 11.3 Async Support

The decorator works with both sync and async functions:

```python
@agent.track("fetch_data")
async def fetch_data(lead):
    async with aiohttp.ClientSession() as session:
        return await session.get(f"/api/leads/{lead.id}")
```

The decorator inspects whether the wrapped function is a coroutine and applies the appropriate wrapper. `contextvars` propagation works natively with `asyncio`.

### 11.4 Track as Context Manager

For cases where decorating isn't possible (inline code blocks, lambdas, dynamic steps):

```python
with agent.track_context("manual_step") as action:
    # ... do work ...
    action.set_payload({"key": "value"})
```

Same lifecycle as the decorator. Raises on exception, emits `action_completed` on clean exit.

---

## 12. Manual Events (Layer 2)

### 12.1 `task.event()` — Custom Events

```python
task.event(
    "scored",
    payload={
        "kind": "decision",
        "summary": "Lead scored below threshold; escalated to review",
        "data": {"score": 42, "threshold": 80},
        "tags": ["lead", "scoring", "escalation"]
    },
    severity="info"
)
```

**`task.event()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `event_type` | str | **required** (positional) | Event type. Can be a known type (`escalated`, `retry_started`, etc.) or `"custom"`. If not a known type, server stores as `"custom"` with original value in `payload.original_type`. |
| `payload` | dict | `None` | Free-form JSON. Max 32KB. Recommended keys: `kind`, `summary`, `data`, `tags`. |
| `severity` | str | auto | Override severity. Values: `"debug"`, `"info"`, `"warn"`, `"error"`. |
| `parent_event_id` | str | `None` | Link this event to a previous event (causal chain). |

**When called outside a task context**, `task.event()` raises `HiveLoopError("No active task context")`. To emit agent-level events without a task, use `agent.event()`.

### 12.2 `agent.event()` — Agent-Level Events

```python
agent.event(
    "custom",
    payload={
        "kind": "configuration_change",
        "summary": "Agent config reloaded: scoring threshold changed 80→75",
        "data": {"previous": {"threshold": 80}, "current": {"threshold": 75}}
    },
    severity="info"
)
```

Same signature as `task.event()` but does not require an active task context. The resulting event has `task_id: null` and `project_id: null`.

**Use cases for agent-level events (clarified in v2):**

| Use Case | Payload Kind | Convenience Method | Description |
|---|---|---|---|
| Self-reported issues | `"issue"` | `agent.report_issue()` / `agent.resolve_issue()` | Agent flags problems it cannot resolve. See Section 12.8. |
| Queue snapshots | `"queue_snapshot"` | `agent.queue_snapshot()` | Agent reports its work queue state. See Section 12.5. |
| TODO lifecycle | `"todo"` | `agent.todo()` | Agent tracks pending work items. See Section 12.6. |
| Scheduled work | `"scheduled"` | `agent.scheduled()` | Agent reports upcoming scheduled work. See Section 12.7. |
| LLM calls (no task) | `"llm_call"` | `agent.llm_call()` | LLM calls not tied to a task. See Section 12.4. |
| Configuration changes | _(custom)_ | `agent.event()` | Agent reloaded config, changed parameters, etc. |

These events appear in the Activity Stream alongside heartbeats and registration events. When the dashboard is filtered to a project, agent-level events for agents in that project are included. For well-known payload kinds, prefer the dedicated convenience methods — they enforce consistent payload shapes.

### 12.3 `task.llm_call()` — LLM Call Tracking (New in v2)

```python
task.llm_call(
    name="phase1_reasoning",
    model="claude-sonnet-4-20250514",
    tokens_in=1500,
    tokens_out=200,
    cost=0.003,
    prompt_preview=prompt[:500],
    response_preview=response[:500],
    duration_ms=1200,
    metadata={"caller": "atomic_phase1_turn_3", "temperature": 0.7}
)
```

**`task.llm_call()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | str | **required** | Call identifier. Appears as the timeline label. |
| `model` | str | **required** | Model identifier. Used for cost-by-model aggregation in Cost Explorer. |
| `tokens_in` | int | **required** | Input token count. |
| `tokens_out` | int | **required** | Output token count. |
| `cost` | float | **required** | Pre-calculated cost in USD. The SDK does not maintain pricing tables — the developer or their LLM client provides this. |
| `prompt_preview` | str | `None` | First ~500 chars of the prompt. For debugging, not billing. |
| `response_preview` | str | `None` | First ~500 chars of the response. For debugging, not billing. |
| `duration_ms` | int | `None` | LLM call latency. Separate from the enclosing action's `duration_ms`. |
| `metadata` | dict | `None` | Arbitrary key-value pairs for the call detail view. Not indexed. |

**Behavior:** Emits a `custom` event with a well-known payload:

```json
{
  "event_type": "custom",
  "payload": {
    "kind": "llm_call",
    "summary": "phase1_reasoning → claude-sonnet-4-20250514 (1500 in / 200 out, $0.003)",
    "data": {
      "name": "phase1_reasoning",
      "model": "claude-sonnet-4-20250514",
      "tokens_in": 1500,
      "tokens_out": 200,
      "cost": 0.003,
      "duration_ms": 1200,
      "prompt_preview": "...",
      "response_preview": "...",
      "metadata": {"caller": "atomic_phase1_turn_3", "temperature": 0.7}
    },
    "tags": ["llm"]
  }
}
```

The SDK auto-generates the `summary` field from the parameters.

**Why this matters:** LLM call tracking is the single most important observability signal for AI agents. The Gap Analysis found that per-call cost visibility was the feature that enabled a 5x cost reduction in the reference implementation. `task.llm_call()` makes this data easy to emit; the Cost Explorer (Section 4.7–4.9) makes it easy to analyze.

**When called outside a task context,** raises `HiveLoopError("No active task context")`. For agent-level LLM calls not tied to a task, use `agent.llm_call()` (Section 12.4).

### 12.4 `agent.llm_call()` — Agent-Level LLM Call Tracking (New in v2)

```python
agent.llm_call(
    name="background_summarization",
    model="claude-haiku-4-5-20251001",
    tokens_in=800,
    tokens_out=150,
    cost=0.001,
    duration_ms=450,
    prompt_preview=prompt[:500],
    response_preview=response[:500],
    metadata={"purpose": "daily_digest_prep"}
)
```

Identical signature to `task.llm_call()` but does not require an active task context. The resulting event has `task_id: null` and `project_id: null`.

**When to use `agent.llm_call()` vs. `task.llm_call()`:**

| Scenario | Method |
|---|---|
| LLM call within a task workflow | `task.llm_call()` — cost attributed to the task and its project |
| LLM call during agent startup / config | `agent.llm_call()` — no task context |
| LLM call in a background maintenance loop | `agent.llm_call()` — not tied to user-facing work |
| LLM call in a queue-processing callback | `task.llm_call()` if a task is active, `agent.llm_call()` otherwise |

**Behavior:** Builds a `custom` event with `payload.kind = "llm_call"`, identical structure to `task.llm_call()`. Emitted with `task_id: null`. The event still appears in the Cost Explorer (aggregated under the agent, but not attributed to any project).

### 12.5 Convenience Methods

For common Layer 2 patterns, the SDK provides typed shorthand methods:

```python
# Escalation
task.escalate(reason="Score below threshold", assigned_to="sales-team")
# Emits: event_type: "escalated", payload: {summary: reason, data: {assigned_to: ...}}

# Approval request
task.request_approval(approver="ops-queue", reason="Contract emails require review")
# Emits: event_type: "approval_requested"

# Approval received
task.approval_received(approved_by="jane@acme.com", decision="approved")
# Emits: event_type: "approval_received"

# Retry
task.retry(attempt=2, reason="Rate limit", backoff_seconds=4.0)
# Emits: event_type: "retry_started"
```

Each convenience method is syntactic sugar over `task.event()` with pre-structured payloads. They exist so that common events have consistent payload shapes across all HiveLoop users.

### 12.6 `agent.queue_snapshot()` — Queue State (New in v2)

```python
agent.queue_snapshot(
    depth=4,
    oldest_age_seconds=120,
    items=[
        {"id": "evt_001", "priority": "high", "source": "human",
         "summary": "Review contract draft", "queued_at": "2026-02-11T14:28:00Z"},
        {"id": "evt_002", "priority": "normal", "source": "webhook",
         "summary": "Process CRM update", "queued_at": "2026-02-11T14:29:00Z"},
    ],
    processing={"id": "evt_003", "summary": "Sending email",
                "started_at": "2026-02-11T14:29:30Z", "elapsed_ms": 4500}
)
```

**`agent.queue_snapshot()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `depth` | int | **required** | Number of items currently in the queue. `0` is valid (queue drained). |
| `oldest_age_seconds` | int | `None` | Age of the oldest queued item in seconds. |
| `items` | list[dict] | `None` | Summary of queued items (see below). |
| `processing` | dict | `None` | Currently processing item, if any (see below). |

**`items` list element:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Item identifier. |
| `priority` | string | `"high"`, `"normal"`, `"low"`. |
| `source` | string | Where it came from: `"human"`, `"webhook"`, `"heartbeat"`, `"scheduled"`. |
| `summary` | string | Brief description of the work item. |
| `queued_at` | ISO 8601 | When it entered the queue. |

**`processing` object:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Item identifier. |
| `summary` | string | What's being processed. |
| `started_at` | ISO 8601 | When processing began. |
| `elapsed_ms` | integer | How long it's been running. |

**Behavior:** Emits a `custom` event with `payload.kind = "queue_snapshot"` and `task_id: null` (agent-level). Auto-generates `payload.summary` as `"Queue: {depth} items, oldest {age}s"`.

Call this method periodically (e.g., every heartbeat cycle) or on significant queue changes (item added, item completed, queue drained). The `queue_provider` callback (Section 9.4) automates this — use `agent.queue_snapshot()` only when you need explicit control over when snapshots are emitted.

### 12.7 `agent.todo()` — TODO Lifecycle (New in v2)

```python
# Create a TODO
agent.todo(
    todo_id="todo_retry_crm",
    action="created",
    summary="Retry: CRM write failed (403)",
    priority="high",
    source="failed_action",
    context="Tool crm_write returned 403 Forbidden for workspace query"
)

# Later, when resolved:
agent.todo(
    todo_id="todo_retry_crm",
    action="completed",
    summary="CRM write succeeded after credential refresh"
)
```

**`agent.todo()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `todo_id` | str | **required** | Stable identifier for this TODO item. Used for lifecycle tracking and aggregation. |
| `action` | str | **required** | Lifecycle action: `"created"`, `"completed"`, `"failed"`, `"dismissed"`, `"deferred"`. |
| `summary` | str | **required** | The TODO description. |
| `priority` | str | `None` | `"high"`, `"normal"`, `"low"`. |
| `source` | str | `None` | What created this TODO: `"failed_action"`, `"agent_decision"`, `"human"`. |
| `context` | str | `None` | Additional context (error message, related task, etc.). |
| `due_by` | str (ISO 8601) | `None` | When this should be done by. |

**Behavior:** Emits a `custom` event with `payload.kind = "todo"` and `task_id: null` (agent-level). The `todo_id` field is critical — the `GET /v1/agents/{agent_id}/pipeline` endpoint (Section 4.10) groups TODO events by `todo_id` to derive current state.

**Lifecycle pattern:** Call `agent.todo()` on each state change. The server aggregates events by `todo_id` and takes the most recent action. A TODO is "active" until its most recent action is `"completed"` or `"dismissed"`.

### 12.8 `agent.scheduled()` — Scheduled Work Report (New in v2)

```python
agent.scheduled(items=[
    {"id": "sched_crm_sync", "name": "CRM Pipeline Sync",
     "next_run": "2026-02-11T15:00:00Z", "interval": "1h",
     "enabled": True, "last_status": "success"},
    {"id": "sched_email_digest", "name": "Daily Email Digest",
     "next_run": "2026-02-12T08:00:00Z", "interval": "daily",
     "enabled": True, "last_status": None},
])
```

**`agent.scheduled()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `items` | list[dict] | **required** | Array of scheduled work items (see below). |

**`items` list element:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Schedule identifier. |
| `name` | string | What will run. |
| `next_run` | ISO 8601 | When it's next scheduled. |
| `interval` | string | Recurrence: `"5m"`, `"1h"`, `"daily"`, `"weekly"`, or `null` for one-shot. |
| `enabled` | boolean | Whether it's active. |
| `last_status` | string \| null | `"success"`, `"failure"`, `"skipped"`, or `null` if never run. |

**Behavior:** Emits a `custom` event with `payload.kind = "scheduled"` and `task_id: null` (agent-level). Auto-generates `payload.summary` as `"{count} scheduled items, next at {time}"`.

Call this periodically (e.g., every few minutes or on schedule changes) to keep the dashboard's Pipeline tab current.

### 12.9 `task.plan()` and `task.plan_step()` — Plan Tracking (New in v2)

```python
# Report plan creation
task.plan(
    goal="Process inbound lead",
    steps=["Search CRM for existing record", "Score lead",
           "Send follow-up email", "Update CRM"]
)

# Report step progress
task.plan_step(step_index=0, action="started",
               summary="Search CRM for existing record")
# ... actions happen ...
task.plan_step(step_index=0, action="completed",
               summary="Found existing CRM record", turns=2, tokens=3200)
task.plan_step(step_index=1, action="started",
               summary="Score lead")
```

**`task.plan()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `goal` | str | **required** | Plan goal / task description. |
| `steps` | list[str] | **required** | Ordered list of step descriptions. |
| `revision` | int | `0` | Plan revision number. `0` for initial plan, increment on replan. |

**Behavior:** Emits a `custom` event with `payload.kind = "plan_created"`:

```json
{
  "event_type": "custom",
  "payload": {
    "kind": "plan_created",
    "summary": "Process inbound lead",
    "data": {
      "steps": [
        {"index": 0, "description": "Search CRM for existing record"},
        {"index": 1, "description": "Score lead"},
        {"index": 2, "description": "Send follow-up email"},
        {"index": 3, "description": "Update CRM"}
      ],
      "revision": 0
    },
    "tags": ["plan", "created"]
  }
}
```

**`task.plan_step()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `step_index` | int | **required** | Zero-based step position. |
| `action` | str | **required** | Step action: `"started"`, `"completed"`, `"failed"`, `"skipped"`. |
| `summary` | str | **required** | Step description or completion note. |
| `total_steps` | int | `None` | Total steps in the plan. Auto-inferred from `task.plan()` if previously called. |
| `turns` | int | `None` | Turns spent on this step (typically set on completion). |
| `tokens` | int | `None` | Tokens spent on this step (typically set on completion). |
| `plan_revision` | int | `None` | Plan revision number. Set when reporting steps after a replan. |

**Behavior:** Emits a `custom` event with `payload.kind = "plan_step"`:

```json
{
  "event_type": "custom",
  "payload": {
    "kind": "plan_step",
    "summary": "Step 0 completed: Found existing CRM record",
    "data": {
      "step_index": 0,
      "total_steps": 4,
      "action": "completed",
      "turns": 2,
      "tokens": 3200,
      "plan_revision": 0
    },
    "tags": ["plan", "step_completed"]
  }
}
```

Auto-generates `payload.summary` as `"Step {index} {action}: {summary}"`. The `total_steps` field is auto-populated from the most recent `task.plan()` call if not explicitly provided.

**When called outside a task context,** raises `HiveLoopError("No active task context")`. Plans are always task-scoped.

### 12.10 `agent.report_issue()` and `agent.resolve_issue()` — Issue Reporting (New in v2)

```python
# Report an issue
agent.report_issue(
    summary="CRM API returning 403 for workspace queries",
    severity="high",
    category="permissions",
    context={"tool": "crm_search", "error_code": 403,
             "last_seen": "2026-02-11T14:30:00Z"}
)

# Later, when resolved:
agent.resolve_issue(summary="CRM API returning 403 for workspace queries")
```

**`agent.report_issue()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `summary` | str | **required** | Issue title. Also used for deduplication (server hashes summary if no `issue_id`). |
| `severity` | str | **required** | `"critical"`, `"high"`, `"medium"`, `"low"`. |
| `issue_id` | str | `None` | Stable identifier. If omitted, the server deduplicates by summary hash. |
| `category` | str | `None` | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"`. |
| `context` | dict | `None` | Related details (tool name, error code, affected task, etc.). |
| `occurrence_count` | int | `None` | How many times this issue has occurred (agent-tracked). |

**Behavior:** Emits a `custom` event with `payload.kind = "issue"` and `data.action = "reported"`:

```json
{
  "event_type": "custom",
  "payload": {
    "kind": "issue",
    "summary": "CRM API returning 403 for workspace queries",
    "data": {
      "issue_id": null,
      "severity": "high",
      "category": "permissions",
      "action": "reported",
      "context": {"tool": "crm_search", "error_code": 403,
                   "last_seen": "2026-02-11T14:30:00Z"},
      "occurrence_count": null
    },
    "tags": ["issue", "permissions"]
  }
}
```

**`agent.resolve_issue()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `summary` | str | **required** | Must match the original issue's summary (for deduplication). |
| `issue_id` | str | `None` | If the original issue had an `issue_id`, provide it here. |

**Behavior:** Emits a `custom` event with `payload.kind = "issue"` and `data.action = "resolved"`. The Pipeline endpoint (Section 4.10) uses the `action` field to determine which issues are still active.

**Why convenience methods instead of raw `agent.event()`?** Issue reporting needs consistent field names (`data.action`, `data.issue_id`, `data.severity`) for the Pipeline endpoint's aggregation logic to work. The convenience methods enforce this structure. Raw `agent.event()` still works, but the developer must match the payload shape exactly.

---

## 13. Transport Layer

### 13.1 Event Queue

All SDK methods enqueue events into a shared, thread-safe buffer:

```
┌──────────────────────────────────────────┐
│  Agent.track()  Task.__enter__()  event()│
│  task.llm_call()   heartbeat_payload()   │
│         │              │            │    │
│         ▼              ▼            ▼    │
│    ┌─────────────────────────────────┐   │
│    │     Thread-Safe Event Queue     │   │
│    │     (collections.deque,         │   │
│    │      maxlen=max_queue_size)     │   │
│    └──────────────┬──────────────────┘   │
│                   │                      │
│                   ▼                      │
│         ┌─────────────────┐              │
│         │  Flush Thread   │              │
│         │  (daemon,       │              │
│         │   periodic)     │              │
│         └────────┬────────┘              │
│                  │                       │
│                  ▼                       │
│      POST /v1/ingest (batched)           │
└──────────────────────────────────────────┘
```

### 13.2 Flush Strategy

The flush thread wakes and ships events under any of these conditions:

| Trigger | Condition |
|---|---|
| **Timer** | Every `flush_interval` seconds (default: 5). |
| **Batch full** | Queue has ≥ `batch_size` events (default: 100). Checked after every enqueue. |
| **Shutdown** | `hiveloop.shutdown()` or process exit triggers a final synchronous flush. |
| **Manual** | `hb.flush()` forces an immediate flush. |

**Flush mechanics:**

1. Drain up to `batch_size` events from the queue.
2. Construct the batch envelope from the agent metadata.
3. POST to `/v1/ingest`.
4. On success (200/207): done. Log warnings for any rejected events and any advisory payload warnings.
5. On failure: retry with exponential backoff (see Section 13.3).
6. If queue has more events, immediately flush again (don't wait for timer).

### 13.3 Error Handling and Resilience

The SDK must never crash the host application. All transport errors are handled internally.

| Scenario | SDK Behavior |
|---|---|
| **HTTP 429 (rate limited)** | Retry after `retry_after_seconds` from response. Events stay in queue. |
| **HTTP 5xx (server error)** | Retry with exponential backoff: 1s, 2s, 4s, 8s, 16s, max 60s. Max 5 retries per flush attempt. |
| **HTTP 400 (bad request)** | Do NOT retry (request is permanently invalid). Log error. Drop the batch. |
| **Connection error** | Same as 5xx — retry with backoff. |
| **Queue full** | Drop oldest events (deque with maxlen). Log warning with count of dropped events. |
| **Serialization error** | Skip the problematic event. Log error with event details. Flush remaining events. |
| **Process exit** | `atexit` handler triggers synchronous flush with 5-second timeout. Best-effort — events may be lost on kill -9. |

**Critical invariant:** The SDK never raises exceptions to the caller (except `HiveLoopError` for misuse like calling `task.event()` outside a task). Transport failures are always silent to the application.

### 13.4 Graceful Shutdown

```python
hiveloop.shutdown(timeout=10.0)
```

1. Stops all heartbeat threads.
2. Performs a final synchronous flush (blocks up to `timeout` seconds).
3. Closes HTTP connections.
4. The client remains in a "shutdown" state — subsequent calls are no-ops.

Also registered via `atexit` with a 5-second timeout.

---

## 14. Framework Integrations

### 14.1 Integration Interface

Every framework integration implements a common internal interface:

```python
class FrameworkIntegration:
    """Base class for framework integrations."""

    def __init__(self, hb_client: HiveBoard, agent: Agent, project: str = None, **kwargs):
        self.hb = hb_client
        self.agent = agent
        self.default_project = project  # New in v2

    def on_agent_start(self, **kwargs):
        """Called when the framework agent starts a run."""
        pass

    def on_tool_start(self, tool_name: str, tool_input: dict, **kwargs):
        """Called when the agent invokes a tool."""
        pass

    def on_tool_end(self, tool_name: str, tool_output: str, **kwargs):
        """Called when a tool returns."""
        pass

    def on_llm_start(self, model: str, prompts: list, **kwargs):
        """Called when an LLM call begins."""
        pass

    def on_llm_end(self, response: str, token_usage: dict, **kwargs):
        """Called when an LLM call completes."""
        pass

    def on_chain_start(self, chain_name: str, **kwargs):
        """Called when a chain/step begins."""
        pass

    def on_chain_end(self, **kwargs):
        """Called when a chain/step completes."""
        pass

    def on_error(self, error: Exception, **kwargs):
        """Called on any framework-level error."""
        pass
```

**New in v2:** The `project` parameter on the base class. Each method maps framework events to HiveLoop events. The base class provides default no-op implementations — integrations override only the hooks their framework exposes.

### 14.2 LangChain Integration

```python
from hiveloop.integrations.langchain import LangChainCallback

callback = LangChainCallback(hb, agent, project="sales-pipeline")
agent = initialize_agent(tools, llm, callbacks=[callback])
```

**Event mapping:**

| LangChain Callback | HiveLoop Event | Notes |
|---|---|---|
| `on_agent_action` | `action_started` | action_name = tool name |
| `on_agent_finish` | `task_completed` | |
| `on_tool_start` | `action_started` | Nested under agent action |
| `on_tool_end` | `action_completed` | Includes tool output in payload |
| `on_tool_error` | `action_failed` | Exception in payload |
| `on_llm_start` | _(records state)_ | Captures model name, prompt for the `on_llm_end` call |
| `on_llm_end` | `custom` (kind: `"llm_call"`) | Uses `task.llm_call()` internally. Token usage, cost estimate, model, prompt/response previews. **Updated in v2.** |
| `on_chain_start` | `action_started` | chain name as action_name |
| `on_chain_end` | `action_completed` | |
| `on_chain_error` | `action_failed` | |

**v2 change:** `on_llm_end` now uses `task.llm_call()` to emit structured LLM call events instead of generic custom events. This ensures LLM calls from LangChain automatically appear in the Cost Explorer.

**Project context:** If `project` is set on the callback, all tasks captured use that project. For per-run overrides, pass `hiveloop_project` in the framework's metadata:

```python
agent.invoke(
    {"input": "..."},
    config={"metadata": {"hiveloop_project": "customer-support"}}
)
```

The integration reads `hiveloop_project` from the framework's metadata/config and passes it to `agent.task(project=...)`.

### 14.3 CrewAI Integration

```python
from hiveloop.integrations.crewai import CrewAICallback

callback = CrewAICallback(hb, project="sales-pipeline")  # Auto-creates agents per CrewAI agent
crew = Crew(agents=[...], callbacks=[callback])
```

**Event mapping:**

| CrewAI Event | HiveLoop Event | Notes |
|---|---|---|
| Agent starts task | `task_started` | Auto-creates HiveLoop agent per CrewAI agent. Uses project from callback. |
| Agent completes task | `task_completed` | |
| Agent uses tool | `action_started` / `action_completed` | |
| Agent delegates | `escalated` | Delegation mapped to escalation |
| Agent error | `task_failed` | |
| LLM call complete | `custom` (kind: `"llm_call"`) | Uses `task.llm_call()`. **New in v2.** |

### 14.4 AutoGen Integration

```python
from hiveloop.integrations.autogen import AutoGenCallback

callback = AutoGenCallback(hb, project="sales-pipeline")
# Register with AutoGen's message hooks
```

**Event mapping:**

| AutoGen Event | HiveLoop Event | Notes |
|---|---|---|
| Message sent | `action_started` | sender/receiver in payload |
| Reply received | `action_completed` | |
| Function call | `action_started` / `action_completed` | Nested under message |
| LLM call complete | `custom` (kind: `"llm_call"`) | Uses `task.llm_call()`. **New in v2.** |
| Termination | `task_completed` | |
| Max rounds reached | `task_failed` | |

### 14.5 Building Custom Integrations

For frameworks without a pre-built integration, developers use the SDK directly:

```python
# Any framework with hooks/callbacks/middleware
class MyFrameworkObserver:
    def __init__(self, hb_client):
        self.agent = hb_client.agent("my-agent", framework="my-framework")

    def on_task_start(self, task_data):
        self.task = self.agent.start_task(
            task_data["id"],
            project="my-project",
            type=task_data["type"]
        )

    def on_llm_complete(self, model, tokens_in, tokens_out, cost, prompt, response):
        self.task.llm_call(
            name="inference",
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
            prompt_preview=prompt[:500],
            response_preview=response[:500]
        )

    def on_step(self, step_name, step_data):
        self.task.event("custom", payload={
            "summary": f"{step_name} executed",
            "data": step_data
        })

    def on_complete(self):
        self.task.complete()
```

No subclassing required. The SDK's public API is sufficient for any custom integration.

---

## 15. Complete API ↔ SDK Cross-Reference

This table maps every SDK action to the API call it produces and the dashboard screen that consumes it.

| Developer Action | SDK Method | API Endpoint | Event Type(s) | Dashboard Screen |
|---|---|---|---|---|
| Initialize | `hiveloop.init()` | — (no HTTP) | — | — |
| Register agent | `hb.agent()` | `POST /v1/ingest` | `agent_registered` | The Hive |
| Heartbeat (auto) | *(background thread)* | `POST /v1/ingest` | `heartbeat` | The Hive (heartbeat dot), Agent Detail (heartbeat history) |
| Start task | `agent.task()` / `agent.start_task()` | `POST /v1/ingest` | `task_started` | Tasks table, Timeline |
| Complete task | `task.__exit__()` / `task.complete()` | `POST /v1/ingest` | `task_completed` | Tasks table, Timeline |
| Fail task | `task.__exit__(exc)` / `task.fail()` | `POST /v1/ingest` | `task_failed` | Tasks table, Timeline |
| Track function | `@agent.track()` | `POST /v1/ingest` | `action_started`, `action_completed/failed` | Timeline |
| LLM call (task) | `task.llm_call()` | `POST /v1/ingest` | `custom` (kind: `llm_call`) | Timeline, Cost Explorer |
| LLM call (agent) | `agent.llm_call()` | `POST /v1/ingest` | `custom` (kind: `llm_call`) | Cost Explorer |
| Create plan | `task.plan()` | `POST /v1/ingest` | `custom` (kind: `plan_created`) | Timeline (plan bar) |
| Report plan step | `task.plan_step()` | `POST /v1/ingest` | `custom` (kind: `plan_step`) | Timeline (plan bar) |
| Queue snapshot | `agent.queue_snapshot()` | `POST /v1/ingest` | `custom` (kind: `queue_snapshot`) | Pipeline tab, Hive card |
| TODO lifecycle | `agent.todo()` | `POST /v1/ingest` | `custom` (kind: `todo`) | Pipeline tab, Stream |
| Scheduled work | `agent.scheduled()` | `POST /v1/ingest` | `custom` (kind: `scheduled`) | Pipeline tab, Stream |
| Report issue | `agent.report_issue()` | `POST /v1/ingest` | `custom` (kind: `issue`) | Pipeline tab, Stream, Hive card |
| Resolve issue | `agent.resolve_issue()` | `POST /v1/ingest` | `custom` (kind: `issue`) | Pipeline tab, Stream |
| Custom event | `task.event()` | `POST /v1/ingest` | `custom` (or typed) | Timeline, Stream |
| Agent-level event | `agent.event()` | `POST /v1/ingest` | `custom` | Stream |
| Escalate | `task.escalate()` | `POST /v1/ingest` | `escalated` | Timeline, Stream |
| Request approval | `task.request_approval()` | `POST /v1/ingest` | `approval_requested` | Timeline, Stream |
| Retry | `task.retry()` | `POST /v1/ingest` | `retry_started` | Timeline, Stream |
| Manual flush | `hb.flush()` | `POST /v1/ingest` | *(queued events)* | — |
| Shutdown | `hiveloop.shutdown()` | `POST /v1/ingest` | *(remaining events)* | — |
| — | — | `GET /v1/agents` | — | The Hive |
| — | — | `GET /v1/agents/{id}` | — | Agent Detail |
| — | — | `GET /v1/agents/{id}/pipeline` | — | Agent Detail (Pipeline tab) |
| — | — | `GET /v1/tasks` | — | Tasks table |
| — | — | `GET /v1/tasks/{id}/timeline` | — | Timeline |
| — | — | `GET /v1/events` | — | Activity Stream |
| — | — | `GET /v1/metrics` | — | Summary bar, Charts |
| — | — | `GET /v1/cost` | — | Cost Explorer |
| — | — | `GET /v1/cost/calls` | — | Cost Explorer (Recent Calls) |
| — | — | `GET /v1/cost/timeseries` | — | Cost Explorer (Chart) |
| — | — | `GET /v1/projects` | — | Project Selector |
| — | — | `WS /v1/stream` | — | All (real-time updates) |

---

## 16. Complete Public API Summary

### 16.1 Module-Level Functions

| Function | Returns | Description |
|---|---|---|
| `hiveloop.init(**kwargs)` | `HiveBoard` | Initialize client singleton. |
| `hiveloop.shutdown(timeout=5.0)` | `None` | Graceful shutdown. |
| `hiveloop.reset()` | `None` | Shutdown + clear singleton (testing). |

### 16.2 `HiveBoard` Class

| Method | Returns | Description |
|---|---|---|
| `hb.agent(agent_id, **kwargs)` | `Agent` | Register or retrieve an agent. |
| `hb.get_agent(agent_id)` | `Agent \| None` | Retrieve registered agent by ID. |
| `hb.flush()` | `None` | Force immediate flush. |

### 16.3 `Agent` Class

| Method | Returns | Description |
|---|---|---|
| `agent.task(task_id, **kwargs)` | `Task` (context manager) | Start a task with context manager. |
| `agent.start_task(task_id, **kwargs)` | `Task` | Start a task without context manager. |
| `agent.track(action_name)` | decorator | Decorator for function tracking. |
| `agent.track_context(action_name)` | context manager | Context manager for inline action tracking. |
| `agent.event(event_type, **kwargs)` | `None` | Emit agent-level event (no task context required). |
| `agent.llm_call(name, model, **kwargs)` | `None` | **New in v2.** Emit agent-level LLM call (no task context required). |
| `agent.queue_snapshot(depth, **kwargs)` | `None` | **New in v2.** Emit queue state snapshot. |
| `agent.todo(todo_id, action, summary, **kwargs)` | `None` | **New in v2.** Emit TODO lifecycle event. |
| `agent.scheduled(items)` | `None` | **New in v2.** Emit scheduled work report. |
| `agent.report_issue(summary, severity, **kwargs)` | `None` | **New in v2.** Report a persistent agent issue. |
| `agent.resolve_issue(summary, **kwargs)` | `None` | **New in v2.** Mark a previously reported issue as resolved. |

### 16.4 `Task` Class

| Method | Returns | Description |
|---|---|---|
| `task.event(event_type, **kwargs)` | `None` | Emit task-scoped event. |
| `task.llm_call(name, model, **kwargs)` | `None` | **New in v2.** Emit LLM call event with structured cost data. |
| `task.plan(goal, steps, **kwargs)` | `None` | **New in v2.** Report plan creation. |
| `task.plan_step(step_index, action, summary, **kwargs)` | `None` | **New in v2.** Report plan step progress. |
| `task.escalate(reason, **kwargs)` | `None` | Convenience: emit `escalated` event. |
| `task.request_approval(approver, **kwargs)` | `None` | Convenience: emit `approval_requested`. |
| `task.approval_received(approved_by, **kwargs)` | `None` | Convenience: emit `approval_received`. |
| `task.retry(attempt, **kwargs)` | `None` | Convenience: emit `retry_started`. |
| `task.complete(**kwargs)` | `None` | Manually complete task (non-context-manager). |
| `task.fail(exception=None, **kwargs)` | `None` | Manually fail task (non-context-manager). |
| `task.set_payload(payload)` | `None` | Add payload to the task's completion event. |

### 16.5 Exceptions

| Exception | When |
|---|---|
| `HiveLoopError` | Misuse: `task.event()` outside task context, `task.llm_call()` outside task context, `task.plan()` / `task.plan_step()` outside task context, invalid config. |
| `HiveLoopConfigError` | Invalid `api_key` format, invalid parameter values. |

These are the ONLY exceptions the SDK raises. Transport/network errors are never propagated.

---

## Appendix A: Full End-to-End Example

```python
import hiveloop

# Initialize
hb = hiveloop.init(api_key="hb_live_a1b2c3d4e5f6", environment="production")

# Register agent with heartbeat payload, queue provider, and scheduled work
agent = hb.agent(
    "lead-qualifier",
    type="sales",
    version="1.2.0",
    heartbeat_payload=lambda: {
        "kind": "heartbeat_status",
        "summary": f"Processing, queue: {queue.qsize()}",
        "data": {"queue_depth": queue.qsize(), "current_state": "processing"}
    },
    queue_provider=lambda: {
        "depth": queue.qsize(),
        "items": [
            {"id": item.id, "priority": item.priority, "source": item.source,
             "summary": item.summary, "queued_at": item.queued_at}
            for item in list(queue.queue)[:10]
        ]
    }
)

# Report scheduled work on startup
agent.scheduled(items=[
    {"id": "sched_crm_sync", "name": "CRM Pipeline Sync",
     "next_run": "2026-02-11T15:00:00Z", "interval": "1h",
     "enabled": True, "last_status": "success"},
])

# Define tracked functions
@agent.track("fetch_crm_data")
def fetch_crm(lead_id):
    return crm_client.get_lead(lead_id)

@agent.track("enrich_company")
def enrich(company_name):
    return clearbit.lookup(company_name)

@agent.track("score_lead")
def score(lead_data, enrichment, task):
    # Track the LLM call for cost visibility
    response = llm.complete(model="claude-sonnet-4-20250514", prompt=scoring_prompt)
    task.llm_call(
        name="lead_scoring",
        model="claude-sonnet-4-20250514",
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost=response.usage.cost,
        prompt_preview=scoring_prompt[:500],
        response_preview=str(response.content)[:500]
    )
    return response.content["score"]

# Process a task
def process_lead(lead):
    with agent.task(lead["id"], project="sales-pipeline", type="lead_processing") as task:

        # Report the plan using convenience methods
        task.plan(
            goal="Process inbound lead",
            steps=["Fetch CRM data", "Enrich company", "Score lead", "Route to sales"]
        )

        # Step 0: Fetch CRM data
        task.plan_step(step_index=0, action="started", summary="Fetch CRM data")
        crm_data = fetch_crm(lead["id"])
        task.plan_step(step_index=0, action="completed",
                       summary="Found existing CRM record", turns=1, tokens=0)

        # Step 1: Enrich company
        task.plan_step(step_index=1, action="started", summary="Enrich company")
        enrichment = enrich(crm_data["company"])
        task.plan_step(step_index=1, action="completed",
                       summary="Enrichment complete", turns=1, tokens=0)

        # Step 2: Score lead
        task.plan_step(step_index=2, action="started", summary="Score lead")
        lead_score = score(crm_data, enrichment, task)
        task.plan_step(step_index=2, action="completed",
                       summary=f"Lead scored {lead_score}", turns=1, tokens=1700)

        task.event("scored", payload={
            "kind": "decision",
            "summary": f"Lead scored {lead_score} against threshold 80",
            "data": {"score": lead_score, "threshold": 80}
        })

        # Step 3: Route to sales
        task.plan_step(step_index=3, action="started", summary="Route to sales")

        if lead_score < 80:
            task.escalate(
                reason=f"Score {lead_score} below threshold",
                assigned_to="sales-team"
            )
            task.request_approval(approver="sales-lead")
            approval = wait_for_approval()
            task.approval_received(
                approved_by=approval["approver"],
                decision=approval["decision"]
            )

        route_lead(lead, lead_score)
        task.plan_step(step_index=3, action="completed", summary="Lead routed")

# Handle errors with issue reporting
def process_with_error_handling(lead):
    try:
        process_lead(lead)
    except PermissionError as e:
        agent.report_issue(
            summary=f"CRM API permission error: {e}",
            severity="high",
            category="permissions",
            context={"lead_id": lead["id"], "error": str(e)}
        )
        agent.todo(
            todo_id=f"todo_retry_{lead['id']}",
            action="created",
            summary=f"Retry lead {lead['id']} after CRM permission fix",
            priority="high",
            source="failed_action"
        )

# Agent-level LLM call (not tied to a task)
def daily_digest():
    response = llm.complete(model="claude-haiku-4-5-20251001", prompt=digest_prompt)
    agent.llm_call(
        name="daily_digest_summary",
        model="claude-haiku-4-5-20251001",
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost=response.usage.cost
    )

# Run it
process_with_error_handling({"id": "task_lead-4821", "company": "Acme Corp"})

# Shutdown (also registered via atexit)
hiveloop.shutdown()
```

**This produces:**
- **Timeline:** task_started → plan_created(4 steps) → plan_step(0, started) → fetch_crm_data → plan_step(0, completed) → plan_step(1, started) → enrich_company → plan_step(1, completed) → plan_step(2, started) → score_lead → llm_call(lead_scoring) → plan_step(2, completed) → scored → plan_step(3, started) → escalated → approval_requested → approval_received → plan_step(3, completed) → task_completed
- **Cost Explorer:** The `llm_call` events (both task-scoped and agent-level) feed per-agent, per-model cost breakdowns
- **Plan progress bar:** The `plan_created` and `plan_step` events render a 4-step progress bar with per-step turn/token counts
- **Pipeline tab:** Queue snapshots (auto-emitted via `queue_provider`), TODOs, and scheduled items visible in agent detail
- **Issue tracking:** Permission errors are reported as agent issues with lifecycle tracking
- **Heartbeat history:** The callback provides rich status summaries in Agent Detail

---

## Appendix B: v1 → v2 Change Summary

### API Changes

| Area | What Changed | Sections |
|---|---|---|
| **Ingestion** | `project_id` per-event; `warnings` in response for payload convention validation | 3.1 |
| **All query endpoints** | `project_id` query parameter | 4.1–4.6 |
| **`GET /v1/agents`** | New fields: `current_project_id`, `projects`, `stats_1h.llm_call_count` | 4.1 |
| **`GET /v1/agents/{id}`** | New fields: `heartbeat_history`, `llm_call_count` in stats/timeseries | 4.2 |
| **`GET /v1/tasks`** | New fields: `project_id`, `total_tokens_in`, `total_tokens_out`, `llm_call_count` | 4.3 |
| **`GET /v1/tasks/{id}/timeline`** | New fields: `render_hint`, `plan`, `project_id`, cost/token totals | 4.4 |
| **`GET /v1/events`** | New params: `project_id`, `payload_kind`. New field: `render_hint` | 4.5 |
| **`GET /v1/metrics`** | New fields: `llm_call_count`, `total_tokens_in`, `total_tokens_out` in summary and timeseries | 4.6 |
| **`GET /v1/cost`** | **New endpoint.** Cost aggregation by agent, model, or both | 4.7 |
| **`GET /v1/cost/calls`** | **New endpoint.** Recent LLM call detail | 4.8 |
| **`GET /v1/cost/timeseries`** | **New endpoint.** Cost over time, optionally split by model | 4.9 |
| **`GET /v1/agents/{id}/pipeline`** | **New endpoint.** Work pipeline state (queue, TODOs, scheduled) | 4.10 |
| **Project endpoints** | **9 new endpoints.** CRUD + archive + agent management | 4.11–4.20 |
| **WebSocket** | `project_id` filter; `current_project_id` on status messages; `render_hint` on events | 5.2–5.3 |
| **Alerts** | `project_id` on rules; `cost_threshold` condition type | 6.1–6.5 |
| **Error codes** | `invalid_project`, `project_not_found`, `cannot_delete_default_project` | 7 |

### SDK Changes

| Area | What Changed | Sections |
|---|---|---|
| **`hb.agent()`** | New params: `heartbeat_payload` callback, `queue_provider` callback | 9.1, 9.3, 9.4 |
| **`agent.task()`** | New param: `project` | 10.1 |
| **`task.llm_call()`** | **New method.** Structured LLM call tracking (task-scoped) | 12.3 |
| **`agent.llm_call()`** | **New method.** Structured LLM call tracking (agent-level, no task context) | 12.4 |
| **`agent.queue_snapshot()`** | **New method.** Emit queue state snapshot | 12.6 |
| **`agent.todo()`** | **New method.** TODO lifecycle tracking (created/completed/failed/dismissed/deferred) | 12.7 |
| **`agent.scheduled()`** | **New method.** Scheduled work reporting | 12.8 |
| **`task.plan()`** | **New method.** Plan creation with goal and step list | 12.9 |
| **`task.plan_step()`** | **New method.** Plan step progress tracking with turns/tokens/revision | 12.9 |
| **`agent.report_issue()`** | **New method.** Agent self-reported issue with severity, category, dedup | 12.10 |
| **`agent.resolve_issue()`** | **New method.** Mark a previously reported issue as resolved | 12.10 |
| **`agent.event()`** | Clarified use cases; documented relationship to convenience methods | 12.2 |
| **Framework integrations** | `project` parameter on base class; LLM calls use `task.llm_call()` | 14.1–14.5 |
| **Cross-reference table** | Added all new methods, pipeline endpoint, Cost Explorer, Project endpoints | 15 |
| **Public API summary** | Added all new Agent and Task methods | 16.3, 16.4 |

---

*End of Document*

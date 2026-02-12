# HiveBoard API + HiveLoop SDK — Unified Technical Specification

**CONFIDENTIAL** | February 2026 | v1.0

---

## 1. Introduction

This document specifies the complete technical contract between HiveLoop (the SDK) and HiveBoard (the platform). It defines every HTTP endpoint, WebSocket channel, SDK class, method signature, and internal behavior needed to build both sides of the system.

It assumes familiarity with the **Event Schema Specification v1** and the **Product & Functional Specification v1**. Where those documents define *what* data looks like and *why* the product exists, this document defines *how* data moves and *how* developers interact with it.

### 1.1 How to Read This Document

The spec is split into two halves that mirror each other:

- **Part A (Sections 2–7):** The HiveBoard API — what the server exposes.
- **Part B (Sections 8–14):** The HiveLoop SDK — what the developer touches.

Every SDK method maps to an API endpoint. Every API query endpoint maps to a dashboard screen. The cross-references are explicit throughout.

### 1.2 Design Principles Governing This Spec

1. **The SDK is a thin pipe.** It formats events, batches them, and ships them over HTTP. It does not evaluate, transform, or filter. Intelligence lives server-side.
2. **The API is two APIs in one.** The ingestion API (write path) is high-throughput, append-only, and tolerant. The query API (read path) is precise, filtered, and serves the dashboard.
3. **Idempotency everywhere.** Every event carries a client-generated `event_id` (UUID). The server deduplicates on `(tenant_id, event_id)`. Re-sending the same batch is always safe.
4. **Fail open on ingest, fail closed on query.** The ingestion path accepts partial batches and logs warnings. The query path returns strict errors on bad parameters.
5. **Tenant isolation is non-negotiable.** The API key is the security boundary. No endpoint accepts `tenant_id` as input. Every query is scoped automatically.

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
    "sdk_version": "hiveloop-0.1.0",
    "environment": "production",
    "group": "sales-team"
  },
  "events": [
    {
      "event_id": "550e8400-e29b-41d4-a716-446655440000",
      "timestamp": "2026-02-10T14:32:01.000Z",
      "event_type": "task_started",
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
   g. Deduplicate on `(tenant_id, event_id)` — skip silently if duplicate.
4. If event_type is `agent_registered`, upsert the agent profile record keyed by `(tenant_id, agent_id)`.
5. Store valid events. Broadcast to WebSocket subscribers.

**Success response (200):**

```json
{
  "accepted": 15,
  "rejected": 0,
  "errors": []
}
```

**Partial success response (207):**

```json
{
  "accepted": 13,
  "rejected": 2,
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

### 4.1 `GET /v1/agents`

**Dashboard screen:** The Hive (Fleet Overview)

Returns all agents for the tenant with their derived current state.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
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
      "last_heartbeat": "2026-02-10T14:32:12.000Z",
      "heartbeat_age_seconds": 4,
      "is_stuck": false,
      "stuck_threshold_seconds": 300,
      "first_seen": "2026-02-01T09:00:00.000Z",
      "last_seen": "2026-02-10T14:32:12.000Z",
      "stats_1h": {
        "tasks_completed": 12,
        "tasks_failed": 1,
        "success_rate": 0.923,
        "avg_duration_ms": 11200,
        "total_cost": 0.96,
        "throughput": 12
      },
      "sparkline_1h": [3, 5, 4, 6, 8, 7, 5, 6, 4, 3, 7, 8]
    }
  ],
  "pagination": { "cursor": "...", "has_more": false }
}
```

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
        "cost": 0.24
      }
    ]
  }
}
```

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
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
      "derived_status": "completed",
      "started_at": "2026-02-10T14:32:01.000Z",
      "completed_at": "2026-02-10T14:32:13.400Z",
      "duration_ms": 12400,
      "total_cost": 0.08,
      "action_count": 5,
      "error_count": 0,
      "has_escalation": true,
      "has_human_intervention": true
    }
  ],
  "pagination": { "cursor": "...", "has_more": true }
}
```

**Task derived status computation:**

| Condition | Status |
|---|---|
| Has `task_completed` event | `"completed"` |
| Has `task_failed` event | `"failed"` |
| Has `escalated` event, no completion | `"escalated"` |
| Has `approval_requested`, no `approval_received` | `"waiting"` |
| Agent is stuck (no heartbeat) while task is open | `"stuck"` |
| Has `task_started`, no terminal event | `"processing"` |

The `total_cost` field is the sum of all `payload.data.cost` values (parsed as float) across events within this task. If no cost metadata is provided by the developer, this field is `null`.

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
  "task_type": "lead_processing",
  "derived_status": "completed",
  "started_at": "2026-02-10T14:32:01.000Z",
  "completed_at": "2026-02-10T14:32:13.400Z",
  "duration_ms": 12400,
  "total_cost": 0.08,
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
      }
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
    },
    {
      "action_id": "act_002",
      "action_name": "enrich_company",
      "parent_action_id": null,
      "started_at": "2026-02-10T14:32:03.200Z",
      "duration_ms": 2100,
      "status": "success",
      "children": []
    }
  ],
  "error_chains": [
    {
      "original_event_id": "evt_err_001",
      "chain": ["evt_err_001", "evt_retry_001", "evt_retry_fail_001", "evt_retry_002"]
    }
  ]
}
```

**Response structure rationale:**

- `events`: Flat chronological list. The dashboard uses this for the horizontal timeline visualization.
- `action_tree`: Hierarchical structure built from `action_id` / `parent_action_id`. Used for nested action rendering (if a tracked function calls another tracked function).
- `error_chains`: Linked sequences built from `parent_event_id`. Used for the error branch visualization.

### 4.5 `GET /v1/events`

**Dashboard screen:** Activity Stream (right panel)

Returns a reverse-chronological stream of events across the workspace.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_id` | string | `null` (all) | Filter to a single agent. |
| `task_id` | string | `null` (all) | Filter to a single task. |
| `event_type` | string (comma-sep) | `null` (all) | Filter by type(s): `"task_started,task_completed"`. |
| `severity` | string (comma-sep) | `null` (all) | Filter by severity: `"error,warn"`. |
| `environment` | string | `null` (all) | Filter by environment. |
| `group` | string | `null` (all) | Filter by group. |
| `since` | ISO 8601 | `null` | Events after this time. |
| `until` | ISO 8601 | `null` | Events before this time. |
| `exclude_heartbeats` | boolean | `true` | Exclude heartbeat events (noisy in the stream). |
| `limit` | integer | 50 | Max items. Max: 200. |
| `cursor` | string | `null` | Pagination cursor. |

**Response (200):**

```json
{
  "data": [
    {
      "event_id": "550e8400-...",
      "agent_id": "lead-qualifier",
      "agent_type": "sales",
      "task_id": "task_lead-4821",
      "event_type": "task_completed",
      "timestamp": "2026-02-10T14:32:13.400Z",
      "severity": "info",
      "status": "success",
      "duration_ms": 12400,
      "payload": {
        "summary": "Lead scored 42 → escalated → approved",
        "kind": "completion"
      }
    }
  ],
  "pagination": { "cursor": "...", "has_more": true }
}
```

### 4.6 `GET /v1/metrics`

**Dashboard screen:** Summary bar + Metrics sparkline charts

Returns aggregated metrics across the workspace or for a specific agent.

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
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
    "avg_cost_per_task": 0.046
  },
  "timeseries": [
    {
      "timestamp": "2026-02-10T13:35:00Z",
      "tasks_completed": 4,
      "tasks_failed": 0,
      "avg_duration_ms": 8400,
      "cost": 0.18,
      "error_count": 0,
      "throughput": 4
    }
  ]
}
```

**Auto-interval selection:**

| Range | Default Interval |
|---|---|
| 1h | 5m |
| 6h | 15m |
| 24h | 1h |
| 7d | 6h |
| 30d | 1d |

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
    "task_id": "task_lead-4821",
    "event_type": "action_completed",
    "timestamp": "2026-02-10T14:32:05.300Z",
    "severity": "info",
    "status": "success",
    "duration_ms": 3400,
    "payload": {
      "summary": "score_lead completed in 3.4s",
      "action_name": "score_lead"
    }
  }
}
```

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
    "heartbeat_age_seconds": 312
  }
}
```

**`agent.stuck` — Agent stuck alert (fired once when threshold crossed):**

```json
{
  "type": "agent.stuck",
  "data": {
    "agent_id": "support-triage",
    "last_heartbeat": "2026-02-10T14:26:48.000Z",
    "stuck_threshold_seconds": 300,
    "current_task_id": "task_ticket-991"
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

### 6.2 `POST /v1/alerts/rules`

Creates a new alert rule.

**Request body:**

```json
{
  "name": "Agent stuck > 5 minutes",
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

**Supported condition types:**

| Type | Parameters | Description |
|---|---|---|
| `agent_stuck` | `threshold_seconds` | Agent has no heartbeat for N seconds. |
| `task_failed` | `count`, `window_seconds` | N task failures within time window. |
| `error_rate` | `threshold_percent`, `window_seconds` | Error rate exceeds X% over window. |
| `duration_exceeded` | `threshold_ms` | Any task exceeds duration threshold. |
| `heartbeat_lost` | `agent_id` | Specific agent stops sending heartbeats. |

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

Returns fired alerts, paginated. Query params: `rule_id`, `since`, `until`, `limit`, `cursor`.

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
| `invalid_parameter` | 400 | Query parameter has invalid value. |
| `agent_not_found` | 404 | Agent ID does not exist in this workspace. |
| `task_not_found` | 404 | Task ID does not exist in this workspace. |
| `rule_not_found` | 404 | Alert rule ID does not exist. |
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
    stuck_threshold=300              # optional, seconds. Default: 300 (5 min)
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

**Registration behavior:**

1. Enqueues an `agent_registered` event with the agent's metadata in the payload.
2. Starts a background heartbeat thread (daemon) that enqueues a `heartbeat` event every `heartbeat_interval` seconds.
3. Stores the agent in the client's agent registry (allows `hb.get_agent("lead-qualifier")` later).
4. Returns the `Agent` instance.

**Calling `hb.agent()` with the same `agent_id` twice** returns the existing `Agent` instance (idempotent). It does NOT create a duplicate. Metadata (type, version) is updated on the existing instance if different.

### 9.2 Heartbeat Thread Lifecycle

The heartbeat thread:

- Runs as a daemon thread (dies when the main process exits).
- Enqueues `heartbeat` events into the shared event queue.
- Pauses during graceful shutdown (`hiveloop.shutdown()` or `hiveloop.reset()`).
- Is per-agent (each agent has its own heartbeat thread).

Heartbeat events are minimal:

```json
{
  "event_id": "auto-generated-uuid",
  "timestamp": "2026-02-10T14:32:30.000Z",
  "event_type": "heartbeat",
  "task_id": null,
  "payload": null
}
```

---

## 10. Task Context Manager (Layer 1)

### 10.1 `agent.task()` — Start a Task

```python
with agent.task(
    task_id="task_lead-4821",
    type="lead_processing",              # optional
    task_run_id="run_abc123",            # optional, auto-generated UUID if omitted
    correlation_id="workflow-99"         # optional, for multi-agent workflows
) as task:
    # ... do work ...
    pass
```

**`agent.task()` — Full Parameter Reference:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `task_id` | str | **required** | Logical task identifier. Max 256 chars. |
| `type` | str | `None` | Task classification. |
| `task_run_id` | str | auto UUID | Disambiguates re-executions. Auto-generated if omitted. |
| `correlation_id` | str | `None` | Cross-agent workflow linkage. Reserved for multi-agent orchestration. |

**Context manager behavior:**

| Moment | What Happens |
|---|---|
| `__enter__` | Enqueues `task_started` event. Sets the task as the active task on the current thread (thread-local). Starts timing. Returns `Task` instance. |
| `__exit__` (no exception) | Enqueues `task_completed` event with `status: "success"` and `duration_ms`. Clears active task. |
| `__exit__` (exception) | Enqueues `task_failed` event with `status: "failure"`, `duration_ms`, and exception info in payload. Clears active task. **Re-raises the exception** — the SDK never swallows errors. |

### 10.2 Thread-Local Task Context

The active task is stored in a `threading.local()` variable. This means:

- Decorators (`@agent.track`) automatically know which task they belong to.
- Multiple threads can run different tasks concurrently without conflicts.
- If no task context is active, `@agent.track` events are emitted as agent-level events (no `task_id`).

### 10.3 Non-Context-Manager Usage

For cases where `with` blocks are impractical (e.g., task spans multiple functions or callbacks):

```python
task = agent.start_task("task_lead-4821", type="lead_processing")
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
    payload={"summary": "Agent configuration reloaded", "data": {"config_version": 3}}
)
```

Same signature as `task.event()` but does not require an active task context. The resulting event has `task_id: null`.

### 12.3 Convenience Methods

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

---

## 13. Transport Layer

### 13.1 Event Queue

All SDK methods enqueue events into a shared, thread-safe buffer:

```
┌──────────────────────────────────────────┐
│  Agent.track()  Task.__enter__()  event()│
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
4. On success (200/207): done. Log warnings for any rejected events.
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

    def __init__(self, hb_client: HiveBoard, agent: Agent, **kwargs):
        self.hb = hb_client
        self.agent = agent

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

Each method maps framework events to HiveLoop events. The base class provides default no-op implementations — integrations override only the hooks their framework exposes.

### 14.2 LangChain Integration

```python
from hiveloop.integrations.langchain import LangChainCallback

callback = LangChainCallback(hb, agent)
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
| `on_llm_start` | `custom` (kind: "llm_call") | Model name, prompt length in payload |
| `on_llm_end` | `custom` (kind: "llm_call") | Token usage, cost estimate in payload |
| `on_chain_start` | `action_started` | chain name as action_name |
| `on_chain_end` | `action_completed` | |
| `on_chain_error` | `action_failed` | |

LLM calls are mapped to `custom` events (not `action_started/completed`) per the v1 scoping decision: HiveBoard traces agent-level actions, not individual LLM calls. The LLM metadata is available in the payload for cost tracking, but doesn't create timeline nodes by default.

### 14.3 CrewAI Integration

```python
from hiveloop.integrations.crewai import CrewAICallback

callback = CrewAICallback(hb)  # Auto-creates agents per CrewAI agent
crew = Crew(agents=[...], callbacks=[callback])
```

**Event mapping:**

| CrewAI Event | HiveLoop Event | Notes |
|---|---|---|
| Agent starts task | `task_started` | Auto-creates HiveLoop agent per CrewAI agent |
| Agent completes task | `task_completed` | |
| Agent uses tool | `action_started` / `action_completed` | |
| Agent delegates | `escalated` | Delegation mapped to escalation |
| Agent error | `task_failed` | |

### 14.4 AutoGen Integration

```python
from hiveloop.integrations.autogen import AutoGenCallback

callback = AutoGenCallback(hb)
# Register with AutoGen's message hooks
```

**Event mapping:**

| AutoGen Event | HiveLoop Event | Notes |
|---|---|---|
| Message sent | `action_started` | sender/receiver in payload |
| Reply received | `action_completed` | |
| Function call | `action_started` / `action_completed` | Nested under message |
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
        self.task = self.agent.start_task(task_data["id"], type=task_data["type"])

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
| Heartbeat (auto) | *(background thread)* | `POST /v1/ingest` | `heartbeat` | The Hive (heartbeat dot) |
| Start task | `agent.task()` / `agent.start_task()` | `POST /v1/ingest` | `task_started` | Tasks table, Timeline |
| Complete task | `task.__exit__()` / `task.complete()` | `POST /v1/ingest` | `task_completed` | Tasks table, Timeline |
| Fail task | `task.__exit__(exc)` / `task.fail()` | `POST /v1/ingest` | `task_failed` | Tasks table, Timeline |
| Track function | `@agent.track()` | `POST /v1/ingest` | `action_started`, `action_completed/failed` | Timeline |
| Custom event | `task.event()` | `POST /v1/ingest` | `custom` (or typed) | Timeline, Stream |
| Escalate | `task.escalate()` | `POST /v1/ingest` | `escalated` | Timeline, Stream |
| Request approval | `task.request_approval()` | `POST /v1/ingest` | `approval_requested` | Timeline, Stream |
| Manual flush | `hb.flush()` | `POST /v1/ingest` | *(queued events)* | — |
| Shutdown | `hiveloop.shutdown()` | `POST /v1/ingest` | *(remaining events)* | — |
| — | — | `GET /v1/agents` | — | The Hive |
| — | — | `GET /v1/agents/{id}` | — | Agent Detail |
| — | — | `GET /v1/tasks` | — | Tasks table |
| — | — | `GET /v1/tasks/{id}/timeline` | — | Timeline |
| — | — | `GET /v1/events` | — | Activity Stream |
| — | — | `GET /v1/metrics` | — | Summary bar, Charts |
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
| `agent.event(event_type, **kwargs)` | `None` | Emit agent-level event. |

### 16.4 `Task` Class

| Method | Returns | Description |
|---|---|---|
| `task.event(event_type, **kwargs)` | `None` | Emit task-scoped event. |
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
| `HiveLoopError` | Misuse: `task.event()` outside task context, invalid config. |
| `HiveLoopConfigError` | Invalid `api_key` format, invalid parameter values. |

These are the ONLY exceptions the SDK raises. Transport/network errors are never propagated.

---

## Appendix A: Full End-to-End Example

```python
import hiveloop

# Initialize
hb = hiveloop.init(api_key="hb_live_a1b2c3d4e5f6", environment="production")

# Register agent
agent = hb.agent("lead-qualifier", type="sales", version="1.2.0")

# Define tracked functions
@agent.track("fetch_crm_data")
def fetch_crm(lead_id):
    return crm_client.get_lead(lead_id)

@agent.track("enrich_company")
def enrich(company_name):
    return clearbit.lookup(company_name)

@agent.track("score_lead")
def score(lead_data, enrichment):
    result = llm.score(lead_data, enrichment)
    return result["score"]

# Process a task
def process_lead(lead):
    with agent.task(lead["id"], type="lead_processing") as task:

        crm_data = fetch_crm(lead["id"])
        enrichment = enrich(crm_data["company"])
        lead_score = score(crm_data, enrichment)

        task.event("scored", payload={
            "kind": "decision",
            "summary": f"Lead scored {lead_score} against threshold 80",
            "data": {"score": lead_score, "threshold": 80}
        })

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

# Run it
process_lead({"id": "task_lead-4821", "company": "Acme Corp"})

# Shutdown (also registered via atexit)
hiveloop.shutdown()
```

**This produces the exact timeline shown in the HiveBoard dashboard prototype: task_received → agent_assigned → fetch_crm_data → enrich_company → score_lead → below_threshold → escalated → approval_requested → human_approved → task_completed.**

---

*End of Document*

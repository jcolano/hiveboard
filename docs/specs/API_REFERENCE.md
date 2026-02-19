# HiveBoard API Reference

**Base URL:** `http://localhost:8000` (local) or `https://your-domain.com` (production)
**Version:** v1
**Content-Type:** `application/json`

---

## Table of Contents

1. [Authentication](#authentication)
2. [Rate Limiting](#rate-limiting)
3. [Error Responses](#error-responses)
4. [Health](#health)
5. [Ingestion](#ingestion)
6. [Agents](#agents)
7. [Pipeline](#pipeline)
8. [Tasks](#tasks)
9. [Events](#events)
10. [Metrics](#metrics)
11. [Cost & LLM Calls](#cost--llm-calls)
12. [Insights Engine](#insights-engine)
13. [Projects](#projects)
14. [Alert Rules & History](#alert-rules--history)
15. [Auth & Users](#auth--users)
16. [API Keys](#api-keys)
17. [Invites](#invites)
18. [Admin](#admin)
19. [WebSocket Streaming](#websocket-streaming)

---

## Authentication

All API requests (except public endpoints) require a `Bearer` token in the `Authorization` header.

```
Authorization: Bearer {token}
```

Two authentication methods are supported:

| Method | Token Format | Description |
|--------|-------------|-------------|
| **API Key** | `hb_live_*`, `hb_test_*`, `hb_read_*` | Stateless key-based auth. Prefix determines key type. |
| **JWT** | Standard JWT | Email+password login returns a JWT for user-scoped access. |

**Key types:**

| Prefix | Type | Permissions |
|--------|------|-------------|
| `hb_live_` | Live | Full read/write access |
| `hb_test_` | Test | Full read/write (test data isolation) |
| `hb_read_` | Read | Read-only — `POST`, `PUT`, `DELETE` return `403` |

**Public endpoints** (no auth required):
`/health`, `/dashboard`, `/v1/auth/login`, `/v1/auth/register`, `/v1/auth/check-slug`, `/v1/auth/accept-invite`, `/v1/auth/quickstart`, `/v1/auth/claim`, `/v1/stream` (WebSocket), `/static/*`

---

## Rate Limiting

Sliding-window rate limiting per API key:

| Endpoint | Limit |
|----------|-------|
| `POST /v1/ingest` | 100 requests/second |
| All other endpoints | 30 requests/second |

**Response headers** (on every authenticated request):

| Header | Description |
|--------|-------------|
| `X-RateLimit-Limit` | Max requests per window |
| `X-RateLimit-Remaining` | Requests remaining |
| `X-RateLimit-Reset` | Unix timestamp when window resets |

**429 response:**

```json
{
  "error": "rate_limit_exceeded",
  "message": "Rate limit of 30 requests/second exceeded",
  "status": 429,
  "details": { "retry_after_seconds": 1 }
}
```

---

## Error Responses

All errors return a consistent JSON shape:

```json
{
  "error": "error_code",
  "message": "Human-readable description",
  "status": 400,
  "details": {}
}
```

**Validation errors** (400) include field-level detail:

```json
{
  "error": "validation_error",
  "message": "Request validation failed",
  "status": 400,
  "details": {
    "fields": [
      { "field": "body.envelope.agent_id", "message": "Field required", "type": "missing" }
    ]
  }
}
```

**Common error codes:** `authentication_failed` (401), `insufficient_permissions` (403), `not_found` (404), `slug_exists` / `email_exists` (409), `rate_limit_exceeded` (429)

---

## Health

### `GET /health`

Health check endpoint. No authentication required.

**Response:**

```json
{ "status": "ok", "version": "1.0.0" }
```

---

## Ingestion

### `POST /v1/ingest`

The primary write endpoint. Accepts a batch of events with a shared envelope.

**Request body:**

```json
{
  "envelope": {
    "agent_id": "my-agent",
    "agent_type": "general",
    "agent_version": "1.0.0",
    "framework": "custom",
    "runtime": "python-3.12",
    "sdk_version": "0.2.0",
    "environment": "production",
    "group": "default"
  },
  "events": [
    {
      "event_id": "evt-001",
      "timestamp": "2026-02-16T12:00:00Z",
      "event_type": "task_started",
      "task_id": "task-001",
      "task_run_id": "run-001",
      "task_type": "email-response",
      "project_id": "sales-pipeline",
      "severity": "info",
      "payload": {
        "kind": "llm_call",
        "summary": "Generate email draft",
        "data": {
          "name": "draft_email",
          "model": "claude-sonnet-4-5-20250929",
          "tokens_in": 1200,
          "tokens_out": 450,
          "cost": 0.0034,
          "duration_ms": 2100
        }
      }
    }
  ]
}
```

**Envelope fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `agent_id` | string | Yes | Agent identifier (max 256 chars) |
| `agent_type` | string | No | Agent classification (default: `"general"`) |
| `agent_version` | string | No | Agent version string |
| `framework` | string | No | Framework name (e.g., `"langchain"`, `"custom"`) |
| `runtime` | string | No | Runtime info (e.g., `"python-3.12"`) |
| `sdk_version` | string | No | HiveLoop SDK version |
| `environment` | string | No | Environment (default: `"production"`, max 64 chars) |
| `group` | string | No | Grouping key (default: `"default"`, max 128 chars) |

**Event fields:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `event_id` | string | Yes | Unique event identifier |
| `timestamp` | string | Yes | ISO 8601 timestamp |
| `event_type` | string | Yes | One of 13 event types (see below) |
| `project_id` | string | No | Project slug/ID (auto-created if unknown) |
| `agent_id` | string | No | Overrides envelope `agent_id` |
| `task_id` | string | No | Task identifier (max 256 chars) |
| `task_type` | string | No | Task classification |
| `task_run_id` | string | No | Unique run ID for the task |
| `correlation_id` | string | No | Cross-task correlation |
| `action_id` | string | No | Action identifier (for action tree) |
| `parent_action_id` | string | No | Parent action (nesting) |
| `severity` | string | No | `debug` / `info` / `warn` / `error` (auto-defaulted) |
| `status` | string | No | Outcome status |
| `duration_ms` | integer | No | Duration in milliseconds |
| `parent_event_id` | string | No | Causal linkage to parent event |
| `payload` | object | No | Event payload (max 32 KB) |

**Event types:**

| Layer | Event Type | Default Severity |
|-------|-----------|-----------------|
| 0 — Lifecycle | `agent_registered` | info |
| 0 — Lifecycle | `heartbeat` | debug |
| 1 — Execution | `task_started` | info |
| 1 — Execution | `task_completed` | info |
| 1 — Execution | `task_failed` | error |
| 1 — Execution | `action_started` | info |
| 1 — Execution | `action_completed` | info |
| 1 — Execution | `action_failed` | error |
| 2 — Telemetry | `retry_started` | warn |
| 2 — Telemetry | `escalated` | warn |
| 2 — Telemetry | `approval_requested` | info |
| 2 — Telemetry | `approval_received` | info |
| 2 — Telemetry | `custom` | info |

**Well-known payload kinds:**

| Kind | Required `data` fields | Description |
|------|----------------------|-------------|
| `llm_call` | `name`, `model` | LLM API call telemetry |
| `queue_snapshot` | `depth` | Agent work queue state |
| `todo` | `todo_id`, `action` | TODO item lifecycle |
| `plan_created` | `steps` | Execution plan creation |
| `plan_step` | `step_index`, `total_steps`, `action` | Plan step progress |
| `issue` | `severity` | Issue reporting/resolution |
| `scheduled` | `items` | Scheduled task list |

**Response (200 or 207 if partial):**

```json
{
  "accepted": 5,
  "rejected": 1,
  "errors": [
    { "event_id": "evt-bad", "error": "invalid_event_type", "message": "Unknown event_type: foobar" }
  ],
  "warnings": [
    { "event_id": "evt-003", "warning": "Auto-created project 'new-project'" }
  ]
}
```

**Batch limits:** Max 500 events, max 1 MB total.

---

## Agents

### `GET /v1/agents`

List all agents for the current tenant.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `environment` | string | — | Filter by environment |
| `group` | string | — | Filter by group |
| `status` | string | — | Filter by derived status: `idle`, `processing`, `waiting_approval`, `error`, `stuck` |
| `sort` | string | `last_seen` | Sort order: `last_seen`, `attention`, `name` |
| `limit` | integer | 50 | Max results (max 200) |

**Response:**

```json
{
  "data": [
    {
      "agent_id": "lead-qualifier",
      "agent_type": "general",
      "agent_version": "1.2.0",
      "framework": "custom",
      "runtime": "python-3.12",
      "sdk_version": "0.2.0",
      "environment": "production",
      "group": "default",
      "derived_status": "processing",
      "current_task_id": "task-123",
      "current_project_id": "proj-abc",
      "last_heartbeat": "2026-02-16T12:00:00Z",
      "heartbeat_age_seconds": 45,
      "is_stuck": false,
      "stuck_threshold_seconds": 300,
      "first_seen": "2026-02-10T08:00:00Z",
      "last_seen": "2026-02-16T12:00:00Z",
      "last_event_type": "action_completed",
      "last_event_at": "2026-02-16T12:00:00Z",
      "stats_1h": {
        "tasks_completed": 12,
        "tasks_failed": 1,
        "success_rate": 92.3,
        "avg_duration_ms": 4500,
        "total_cost": 0.0842,
        "throughput": 12,
        "queue_depth": 3,
        "active_issues": 0
      }
    }
  ]
}
```

**Derived status logic:**
- `error` — last event was `task_failed` or `action_failed`
- `stuck` — no heartbeat within `stuck_threshold_seconds`
- `waiting_approval` — last event was `approval_requested`
- `processing` — last event was a task/action start
- `idle` — default when none of the above apply

### `GET /v1/agents/{agent_id}`

Get a single agent by ID. Returns the same shape as a list item.

**Response:** Same as single item in `GET /v1/agents` response.
**404** if agent not found.

### `DELETE /v1/agents/{agent_id}`

Delete an agent and its project-agent associations. Requires `owner` or `admin` role.

**Response:** `204 No Content`
**404** if agent not found.

---

## Pipeline

### `GET /v1/agents/{agent_id}/pipeline`

Get the operational pipeline state for an agent: work queue, TODOs, scheduled items, and active issues.

**Response:**

```json
{
  "agent_id": "lead-qualifier",
  "queue": {
    "depth": 3,
    "oldest_age_seconds": 120,
    "items": [
      { "id": "qi-001", "priority": "high", "source": "webhook", "summary": "New lead from HubSpot" }
    ],
    "processing": {
      "id": "qi-000", "summary": "Processing lead-4824",
      "started_at": "2026-02-16T12:04:50Z", "elapsed_ms": 10000
    },
    "snapshot_at": "2026-02-16T12:05:00Z"
  },
  "todos": [
    { "todo_id": "todo-001", "action": "created", "priority": "high", "source": "failed_action", "context": "Needs manual enrichment" }
  ],
  "scheduled": [
    { "id": "sched-001", "name": "Batch re-scoring", "next_run": "2026-02-16T14:00:00Z", "interval": "1h", "enabled": true, "last_status": "success" }
  ],
  "issues": [
    { "issue_id": "pool-exhaust", "severity": "high", "category": "connectivity", "context": "Connection pool exhausted", "action": "reported", "occurrence_count": 3, "summary": "DB pool exhausted" }
  ]
}
```

Issues are automatically filtered: issues older than 24 hours (relative to the agent's most recent event) and issues with `action: "resolved"` are excluded.

### `POST /v1/agents/{agent_id}/issues/{issue_id}/resolve`

Manually resolve a single issue by injecting a synthetic resolution event.

**Response:**

```json
{ "resolved": "pool-exhaust" }
```

### `POST /v1/agents/{agent_id}/issues/resolve-all`

Resolve all active issues for an agent.

**Response:**

```json
{ "resolved": 3 }
```

### `GET /v1/pipeline`

Fleet-level pipeline aggregation across all agents.

**Response:**

```json
{
  "totals": {
    "queue_depth": 15,
    "active_issues": 4,
    "active_todos": 7,
    "scheduled_count": 12
  },
  "agents": [
    { "agent_id": "lead-qualifier", "queue_depth": 3, "active_todos": 2, "active_issues": 1, "scheduled_count": 3 }
  ]
}
```

---

## Tasks

### `GET /v1/tasks`

List tasks (derived from the pre-computed `task_runs` table).

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `agent_id` | string | — | Filter by agent |
| `task_type` | string | — | Filter by task type |
| `status` | string | — | Filter by derived status: `processing`, `completed`, `failed`, `escalated`, `waiting` |
| `environment` | string | — | Filter by environment |
| `since` | string | — | ISO 8601 start time |
| `until` | string | — | ISO 8601 end time |
| `sort` | string | `newest` | Sort order: `newest`, `oldest` |
| `limit` | integer | 50 | Max results (max 200) |
| `cursor` | string | — | Pagination cursor |

**Response:**

```json
{
  "data": [
    {
      "task_id": "task-001",
      "task_type": "email-response",
      "task_run_id": "run-001",
      "agent_id": "lead-qualifier",
      "project_id": "sales-pipeline",
      "derived_status": "completed",
      "started_at": "2026-02-16T12:00:00Z",
      "completed_at": "2026-02-16T12:00:45Z",
      "duration_ms": 45000,
      "total_cost": 0.0123,
      "action_count": 5,
      "error_count": 0,
      "has_escalation": false,
      "has_human_intervention": false,
      "llm_call_count": 3,
      "total_tokens_in": 4500,
      "total_tokens_out": 1200
    }
  ],
  "pagination": { "cursor": null, "has_more": false }
}
```

### `GET /v1/tasks/{task_id}/timeline`

Get the full timeline for a task: all events, action tree, error chains, and plan overlay.

**Response:**

```json
{
  "task_id": "task-001",
  "task_run_id": "run-001",
  "agent_id": "lead-qualifier",
  "project_id": "sales-pipeline",
  "task_type": "email-response",
  "derived_status": "completed",
  "started_at": "2026-02-16T12:00:00Z",
  "completed_at": "2026-02-16T12:00:45Z",
  "duration_ms": 45000,
  "total_cost": 0.0123,
  "events": [ "..." ],
  "action_tree": [
    {
      "action_id": "act-001",
      "parent_action_id": null,
      "name": "search_leads",
      "status": "completed",
      "duration_ms": 12000,
      "events": [ "..." ],
      "children": []
    }
  ],
  "error_chains": [],
  "plan": {
    "goal": "Process inbound lead",
    "steps": [
      { "index": 0, "description": "Enrich lead data", "action": "completed", "completed_at": "2026-02-16T12:00:15Z" },
      { "index": 1, "description": "Score lead", "action": "completed", "completed_at": "2026-02-16T12:00:30Z" }
    ],
    "progress": { "completed": 2, "total": 2 }
  }
}
```

**404** if task not found.

---

## Events

### `GET /v1/events`

Query raw events with filtering.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `agent_id` | string | — | Filter by agent |
| `task_id` | string | — | Filter by task |
| `event_type` | string | — | Filter by event type |
| `severity` | string | — | Filter by severity |
| `environment` | string | — | Filter by environment |
| `group` | string | — | Filter by group |
| `since` | string | — | ISO 8601 start time |
| `until` | string | — | ISO 8601 end time |
| `exclude_heartbeats` | boolean | `true` | Exclude heartbeat events |
| `payload_kind` | string | — | Filter by `payload.kind` |
| `limit` | integer | 50 | Max results (max 200) |
| `cursor` | string | — | Pagination cursor |

**Response:**

```json
{
  "data": [
    {
      "event_id": "evt-001",
      "tenant_id": "t1",
      "agent_id": "lead-qualifier",
      "timestamp": "2026-02-16T12:00:00Z",
      "event_type": "action_completed",
      "severity": "info",
      "task_id": "task-001",
      "action_id": "act-001",
      "duration_ms": 1200,
      "payload": { "kind": "llm_call", "data": { "..." } }
    }
  ],
  "pagination": { "cursor": null, "has_more": false }
}
```

---

## Metrics

### `GET /v1/metrics`

Aggregated metrics with time-series buckets.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `agent_id` | string | — | Filter by agent |
| `environment` | string | — | Filter by environment |
| `metric` | string | — | Specific metric name |
| `group_by` | string | — | Group by dimension |
| `range` | string | `1h` | Time range: `1h`, `6h`, `24h`, `7d`, `30d` |
| `interval` | string | auto | Bucket interval: `1m`, `5m`, `15m`, `1h`, `6h`, `1d` |

**Response:**

```json
{
  "range": "24h",
  "interval": "1h",
  "summary": {
    "total_tasks": 150,
    "completed": 140,
    "failed": 8,
    "escalated": 2,
    "stuck": 0,
    "success_rate": 93.3,
    "avg_duration_ms": 5200,
    "total_cost": 1.2345,
    "avg_cost_per_task": 0.0082
  },
  "timeseries": [
    { "timestamp": "2026-02-16T00:00:00Z", "tasks_completed": 5, "tasks_failed": 0, "avg_duration_ms": 4800, "cost": 0.045, "error_count": 0, "throughput": 5 }
  ]
}
```

---

## Cost & LLM Calls

### `GET /v1/cost`

Cost summary with breakdowns by agent and model.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `agent_id` | string | — | Filter by agent |
| `range` | string | `24h` | Time range: `1h`, `6h`, `24h`, `7d`, `30d` |

**Response:**

```json
{
  "total_cost": 2.4567,
  "call_count": 342,
  "total_tokens_in": 450000,
  "total_tokens_out": 120000,
  "reported_cost": 1.8000,
  "estimated_cost": 0.6567,
  "by_agent": [
    { "agent_id": "lead-qualifier", "cost": 1.2, "call_count": 180 }
  ],
  "by_model": [
    { "model": "claude-sonnet-4-5-20250929", "cost": 1.8, "call_count": 250 }
  ]
}
```

### `GET /v1/cost/calls`

Paginated list of individual LLM calls.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `agent_id` | string | — | Filter by agent |
| `model` | string | — | Filter by model name |
| `since` | string | — | ISO 8601 start time |
| `until` | string | — | ISO 8601 end time |
| `limit` | integer | 50 | Max results (max 200) |
| `cursor` | string | — | Pagination cursor |

**Response:**

```json
{
  "data": [
    {
      "event_id": "evt-llm-001",
      "agent_id": "lead-qualifier",
      "project_id": "sales-pipeline",
      "task_id": "task-001",
      "timestamp": "2026-02-16T12:00:05Z",
      "name": "draft_email",
      "model": "claude-sonnet-4-5-20250929",
      "tokens_in": 1200,
      "tokens_out": 450,
      "cost": 0.0034,
      "duration_ms": 2100,
      "cost_source": "reported",
      "cost_model_matched": null,
      "prompt_preview": "Draft a professional email...",
      "response_preview": "Subject: Follow-up on..."
    }
  ],
  "pagination": { "cursor": null, "has_more": false }
}
```

### `GET /v1/cost/timeseries`

Cost over time as bucketed time-series.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `agent_id` | string | — | Filter by agent |
| `range` | string | `24h` | Time range |
| `interval` | string | auto | Bucket interval |

**Response:**

```json
{
  "data": [
    { "timestamp": "2026-02-16T00:00:00Z", "cost": 0.12, "call_count": 15, "tokens_in": 18000, "tokens_out": 5400 }
  ]
}
```

### `GET /v1/llm-calls`

List LLM calls with totals wrapper. Same filters as `/v1/cost/calls` plus `task_id`.

**Additional query param:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `task_id` | string | — | Filter by task |

**Response:**

```json
{
  "data": [ "..." ],
  "pagination": { "cursor": null, "has_more": false },
  "totals": {
    "cost": 0.456,
    "tokens_in": 54000,
    "tokens_out": 16000,
    "call_count": 42
  }
}
```

---

## Insights Engine

Pre-aggregated analytics built from hourly aggregate buckets.

### `GET /v1/insights/agents`

Per-agent analytics: tasks, costs, errors, top models, top actions.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `24h` | Time range |
| `project_id` | string | — | Filter by project |
| `sort` | string | `cost` | Sort by: `cost`, `tasks`, `errors`, `llm_calls` |

**Response includes:** `agents[]` (detailed per-agent breakdown), `fleet_totals`, `comparisons` (max/min/avg across agents).

### `GET /v1/insights/models`

Per-model analytics: call counts, costs, token usage, agents using each model.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `24h` | Time range |
| `agent_id` | string | — | Filter by agent |

### `GET /v1/insights/timeseries`

Hourly time-series for any metric.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `24h` | Time range |
| `agent_id` | string | — | Filter by agent |
| `metric` | string | `cost` | Metric: `cost`, `tasks`, `errors`, `llm_calls`, `tokens` |

**Response includes:** `buckets[]` (hourly values), `summary` (total, avg, peak/trough).

### `GET /v1/insights/errors`

Error breakdown by agent, type, category, task type, and action. Includes error time-series.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `24h` | Time range |
| `agent_id` | string | — | Filter by agent |

### `GET /v1/insights/prompts`

LLM call/prompt analytics: per-call-name costs, token usage, agents using, primary model.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `24h` | Time range |
| `agent_id` | string | — | Filter by agent |
| `sort` | string | `cost` | Sort by: `cost`, `tokens`, `calls` |

**Response includes:** `calls[]` (per call-name details), `biggest_prompt` (highest token usage).

### `GET /v1/insights/actions`

Action analytics: per-action-name counts, success rates, durations, hourly heatmaps.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `24h` | Time range |
| `agent_id` | string | — | Filter by agent |
| `group_by` | string | `name` | Group by dimension |

---

## Projects

### `GET /v1/projects`

List all projects for the current tenant.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `include_archived` | boolean | `false` | Include archived projects |

**Response:**

```json
{
  "data": [
    {
      "project_id": "proj-001",
      "tenant_id": "t1",
      "name": "Sales Pipeline",
      "slug": "sales-pipeline",
      "description": "Lead qualification pipeline",
      "environment": "production",
      "settings": {},
      "is_archived": false,
      "auto_created": false,
      "created_at": "2026-02-10T08:00:00Z",
      "updated_at": "2026-02-16T12:00:00Z",
      "event_count": 5432
    }
  ]
}
```

### `POST /v1/projects`

Create a new project.

**Request body:**

```json
{
  "name": "Customer Support",
  "slug": "customer-support",
  "description": "Support ticket automation",
  "environment": "production",
  "settings": {}
}
```

**Response:** `201` with project record.
**409** if slug already exists.

### `GET /v1/projects/{project_id}`

Get a single project by ID or slug.

### `PUT /v1/projects/{project_id}`

Update a project.

**Request body** (all fields optional):

```json
{
  "name": "Updated Name",
  "slug": "updated-slug",
  "description": "New description",
  "environment": "staging",
  "settings": {}
}
```

### `DELETE /v1/projects/{project_id}`

Delete (archive) a project. Events are reassigned to another project.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `reassign_to` | string | `default` | Target project slug for event reassignment |

**Response:**

```json
{ "status": "deleted", "events_reassigned": 1234, "reassigned_to": "default" }
```

The `default` project cannot be deleted.

### `POST /v1/projects/{project_id}/archive`

Archive a project (soft-delete).

### `POST /v1/projects/{project_id}/unarchive`

Unarchive a previously archived project.

### `POST /v1/projects/{project_id}/merge`

Merge a source project into a target: reassign all events, then archive the source.

**Request body:**

```json
{ "target_slug": "main-project" }
```

**Response:**

```json
{ "status": "merged", "source_slug": "old-project", "target_slug": "main-project", "events_moved": 567 }
```

### `GET /v1/projects/{project_id}/agents`

List agents assigned to a project.

### `POST /v1/projects/{project_id}/agents`

Add an agent to a project.

**Request body:**

```json
{ "agent_id": "lead-qualifier" }
```

### `DELETE /v1/projects/{project_id}/agents/{agent_id}`

Remove an agent from a project.

---

## Alert Rules & History

### `GET /v1/alerts/rules`

List alert rules.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | — | Filter by project |
| `is_enabled` | boolean | — | Filter by enabled status |

**Response:**

```json
{
  "data": [
    {
      "rule_id": "rule-001",
      "tenant_id": "t1",
      "project_id": null,
      "name": "Agent Stuck Alert",
      "condition_type": "agent_stuck",
      "condition_config": { "threshold_seconds": 300 },
      "filters": { "agent_id": "lead-qualifier" },
      "actions": [{ "type": "webhook", "url": "https://hooks.slack.com/..." }],
      "cooldown_seconds": 300,
      "is_enabled": true,
      "created_at": "2026-02-10T08:00:00Z",
      "updated_at": "2026-02-10T08:00:00Z"
    }
  ]
}
```

**Alert condition types:** `agent_stuck`, `task_duration`, `error_rate`, `custom_event`, `heartbeat_missing`, `cost_threshold`

### `POST /v1/alerts/rules`

Create an alert rule.

**Request body:**

```json
{
  "name": "High Error Rate",
  "condition_type": "error_rate",
  "condition_config": { "threshold": 0.1, "window_seconds": 3600 },
  "filters": {},
  "actions": [{ "type": "webhook", "url": "https://hooks.slack.com/..." }],
  "cooldown_seconds": 600
}
```

### `PUT /v1/alerts/rules/{rule_id}`

Update an alert rule. All fields optional.

### `DELETE /v1/alerts/rules/{rule_id}`

Delete an alert rule.

### `GET /v1/alerts/history`

List fired alert history.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `rule_id` | string | — | Filter by rule |
| `project_id` | string | — | Filter by project |
| `since` | string | — | ISO 8601 start time |
| `limit` | integer | 50 | Max results (max 200) |
| `cursor` | string | — | Pagination cursor |

---

## Auth & Users

### `POST /v1/auth/login` (public)

Email+password login. Returns a JWT token.

**Query parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `tenant_id` | string | Yes | Tenant ID to authenticate against |

**Request body:**

```json
{ "email": "user@example.com", "password": "secret123" }
```

**Response:**

```json
{
  "token": "eyJhbG...",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": {
    "user_id": "u-001",
    "tenant_id": "t1",
    "email": "user@example.com",
    "name": "Jane Doe",
    "role": "owner",
    "is_active": true,
    "created_at": "2026-02-10T08:00:00Z",
    "updated_at": "2026-02-16T12:00:00Z",
    "last_login_at": "2026-02-16T11:00:00Z",
    "settings": {}
  },
  "tenant_name": "Acme Corp",
  "tenant_slug": "acme-corp"
}
```

### `POST /v1/auth/register` (public)

Register a new tenant with owner user, default project, and API key.

**Request body:**

```json
{
  "email": "admin@newco.com",
  "password": "securepass",
  "name": "Admin User",
  "tenant_name": "NewCo"
}
```

**Response (201):**

```json
{
  "user": { "..." },
  "tenant": { "tenant_id": "...", "name": "NewCo", "slug": "newco" },
  "api_key": "hb_live_abc123..."
}
```

### `GET /v1/auth/check-slug` (public)

Check if a tenant slug is available.

**Query parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `slug` | string | Yes | Slug to check |

**Response:**

```json
{ "slug": "newco", "available": true }
```

### `POST /v1/auth/quickstart` (public)

1-click workspace creation. No user account required. Creates a tenant, default project, and API key. Returns a `claim_token` that can be used later to attach a real user via `POST /v1/auth/claim`.

**Request body:** None (empty POST).

**Response (201):**

```json
{
  "tenant_id": "uuid",
  "tenant_name": "quickstart-a1b2c3d4",
  "tenant_slug": "quickstart-a1b2c3d4",
  "api_key": "hb_live_abc123...",
  "claim_token": "uuid-token"
}
```

The `api_key` can be used immediately to ingest events. The `claim_token` expires after 30 days.

### `POST /v1/auth/claim` (public)

Claim a quickstart workspace by attaching real user credentials. Creates an owner user in the tenant.

**Request body:**

```json
{
  "claim_token": "uuid-token-from-quickstart",
  "email": "user@example.com",
  "password": "securepass",
  "name": "Jane Doe"
}
```

**Response:** Same as login response (includes JWT token, user, tenant info).

**Errors:**
- `404` — Claim token not found or expired
- `409` — Email already registered

### `POST /v1/auth/accept-invite` (public)

Accept an invite and join a tenant.

**Request body:**

```json
{ "invite_token": "raw-token-string", "name": "New User", "password": "securepass" }
```

**Response:** Same as login response (includes JWT token).

### `POST /v1/auth/change-password`

Change password for the current JWT user.

**Request body:**

```json
{ "current_password": "oldpass", "new_password": "newpass" }
```

Requires JWT auth (not API key).

### `POST /v1/auth/reset-password/{user_id}`

Admin/owner force-resets a user's password.

**Request body:**

```json
{ "new_password": "newpass" }
```

Requires `owner` or `admin` role.

### `POST /v1/auth/invite`

Invite a user by email. Requires `owner` or `admin` role.

**Request body:**

```json
{ "email": "newuser@example.com", "role": "member", "name": "New User" }
```

**Response (201):**

```json
{
  "invite_id": "inv-001",
  "email": "newuser@example.com",
  "role": "member",
  "tenant_id": "t1",
  "expires_at": "2026-02-23T12:00:00Z",
  "invite_token": "raw-token-for-email"
}
```

Invites expire after 7 days. Only `owner` can invite as `owner` or `admin`.

### `GET /v1/users`

List users. Requires `owner` or `admin` role.

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `role` | string | — | Filter by role: `owner`, `admin`, `member`, `viewer` |
| `is_active` | boolean | — | Filter by active status |

### `POST /v1/users`

Create a user. Requires `owner` or `admin` role.

**Request body:**

```json
{ "email": "user@example.com", "password": "pass", "name": "User", "role": "member" }
```

### `GET /v1/users/me`

Get the current user's profile. Requires JWT auth.

### `GET /v1/users/{user_id}`

Get a user by ID. Requires `owner` or `admin` role.

### `PUT /v1/users/{user_id}`

Update a user. Requires `owner` or `admin` role.

**Request body** (all fields optional):

```json
{ "email": "new@email.com", "name": "New Name", "role": "admin", "settings": {} }
```

### `DELETE /v1/users/{user_id}`

Deactivate (soft-delete) a user. Cannot self-deactivate. Requires `owner` or `admin` role.

### `POST /v1/users/{user_id}/reactivate`

Reactivate a deactivated user. Requires `owner` or `admin` role.

---

## API Keys

### `GET /v1/api-keys`

List API keys. Owner/admin see all keys; others see their own.

**Response:**

```json
{
  "data": [
    {
      "key_id": "key-001",
      "key_prefix": "hb_live_",
      "key_type": "live",
      "label": "Production Key",
      "created_by_user_id": "u-001",
      "created_at": "2026-02-10T08:00:00Z",
      "last_used_at": "2026-02-16T12:00:00Z",
      "is_active": true
    }
  ]
}
```

### `POST /v1/api-keys`

Create a new API key. Viewers can only create `read` keys.

**Request body:**

```json
{ "label": "CI/CD Key", "key_type": "live" }
```

**Response (201):**

```json
{
  "key_id": "key-002",
  "key_prefix": "hb_live_",
  "key_type": "live",
  "label": "CI/CD Key",
  "raw_key": "hb_live_abc123...",
  "created_at": "2026-02-16T12:00:00Z"
}
```

The `raw_key` is only returned once at creation time.

### `DELETE /v1/api-keys/{key_id}`

Revoke an API key. Non-owner/admin can only revoke their own keys.

---

## Invites

### `GET /v1/invites`

List pending invites. Requires `owner` or `admin` role.

### `DELETE /v1/invites/{invite_id}`

Cancel a pending invite. Requires `owner` or `admin` role.

---

## Admin

### `POST /v1/admin/rebuild-aggregates`

Rebuild hourly aggregate tables (`agent_hourly`, `model_hourly`) from raw events.

**Response:**

```json
{ "status": "rebuilt", "buckets": { "agent_hourly": 150, "model_hourly": 45 } }
```

### `POST /v1/admin/rebuild-task-runs`

Rebuild the `task_runs` table from raw events.

**Response:**

```json
{ "status": "rebuilt", "task_runs": 234 }
```

### `GET /v1/admin/pricing`

List LLM pricing entries used for cost estimation.

### `POST /v1/admin/pricing`

Add a pricing entry.

**Request body:**

```json
{
  "model_pattern": "claude-sonnet-4-5*",
  "provider": "anthropic",
  "input_per_m": 3.0,
  "output_per_m": 15.0
}
```

### `PUT /v1/admin/pricing/{pattern}`

Update a pricing entry by pattern.

### `DELETE /v1/admin/pricing/{pattern}`

Delete a pricing entry by pattern.

---

## WebSocket Streaming

### `WS /v1/stream?token={api_key}`

Real-time event and agent status streaming.

**Connection:** Pass your API key as the `token` query parameter.

**Max connections:** 5 per API key.

**Client messages:**

```json
{
  "action": "subscribe",
  "channels": ["events", "agents"],
  "filters": { "agent_id": "lead-qualifier" }
}
```

Actions: `subscribe`, `unsubscribe`, `ping`
Channels: `events`, `agents`

**Server messages:**

| Type | Channel | Description |
|------|---------|-------------|
| `subscribed` | — | Subscription confirmation |
| `event.new` | events | New event ingested |
| `agent.status_changed` | agents | Agent status transition |
| `agent.stuck` | agents | Agent stuck alert |
| `pong` | — | Heartbeat response |

**Example server messages:**

```json
{ "type": "event.new", "data": { "event_id": "evt-001", "agent_id": "...", "event_type": "action_completed", "..." } }
```

```json
{ "type": "agent.status_changed", "data": { "agent_id": "lead-qualifier", "previous_status": "processing", "new_status": "idle" } }
```

```json
{ "type": "agent.stuck", "data": { "agent_id": "lead-qualifier", "last_heartbeat": "2026-02-16T11:50:00Z", "threshold_seconds": 300 } }
```

Server sends pings every 30 seconds. Connections that miss 3 consecutive pongs are terminated.

---

## Time Ranges

Many endpoints accept a `range` parameter:

| Value | Duration |
|-------|----------|
| `1h` | 1 hour |
| `6h` | 6 hours |
| `24h` | 24 hours |
| `7d` | 7 days |
| `30d` | 30 days |
| `90d` | 90 days |

Auto-selected intervals per range:

| Range | Auto Interval |
|-------|---------------|
| `1h` | `5m` |
| `6h` | `15m` |
| `24h` | `1h` |
| `7d` | `6h` |
| `30d` | `1d` |

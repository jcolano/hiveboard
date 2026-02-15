# Pre-Aggregated Insights Engine — API Reference for Front-End Team

## Overview

The Insights Engine adds **6 new `GET` endpoints** under `/v1/insights/` plus 1 admin endpoint. These replace on-demand event scanning with pre-aggregated hourly data, making dashboard queries instant regardless of event volume.

**Key differences from existing endpoints:**
- Data comes from 2 new aggregate tables (`agent_hourly`, `model_hourly`), not raw events
- All endpoints accept a `range` parameter (default `"24h"`) — valid values: `1h`, `6h`, `24h`, `7d`, `30d`, `90d`
- Aggregates survive event pruning (90-day retention vs 7-day for free tier events)
- All endpoints require auth (`Authorization: Bearer <api_key>`)
- All responses are JSON with a top-level `range` field

---

## Authentication

Same as all other HiveBoard endpoints:

```
Authorization: Bearer hb_live_dev000000000000000000000000000000
```

---

## Endpoints

### 1. `GET /v1/insights/agents`

**Purpose:** Ranked agent comparison. Answers: most expensive agent, most active, most errors.

**Use for:** Fleet overview dashboard, agent leaderboard, agent comparison table.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `"24h"` | Time range: `1h`, `6h`, `24h`, `7d`, `30d`, `90d` |
| `project_id` | string | null | Filter to agents assigned to this project |
| `sort` | string | `"cost"` | Sort agents by: `cost`, `tasks`, `errors`, `llm_calls` |

**Response Shape:**

```json
{
  "range": "24h",
  "agents": [
    {
      "agent_id": "campaigner",
      "tasks_completed": 5,
      "tasks_failed": 0,
      "success_rate": 100.0,
      "avg_task_duration_ms": 488,
      "llm_call_count": 15,
      "llm_cost": 0.2901,
      "llm_tokens_in": 38805,
      "llm_tokens_out": 6407,
      "error_count": 0,
      "errors_by_type": {},
      "errors_by_category": {},
      "top_models": [
        { "model": "gpt-4o", "calls": 15, "cost": 0.2901, "tokens_in": 38805, "tokens_out": 6407 }
      ],
      "top_actions": [
        { "name": "setup_ab_test", "started": 5, "completed": 5 },
        { "name": "fetch_campaign_metrics", "started": 5, "completed": 5 }
      ],
      "top_llm_calls": [
        { "name": "analyze_results", "count": 5, "tokens_in_sum": 14686, "tokens_out_sum": 2372, "cost_sum": 0.109 }
      ],
      "tasks_by_type": {
        "campaign_management": { "started": 5, "completed": 5 }
      }
    }
  ],
  "fleet_totals": {
    "total_cost": 0.9667,
    "total_tasks": 38,
    "total_errors": 0,
    "total_llm_calls": 80
  },
  "comparisons": {
    "cost": {
      "max_agent": "campaigner",
      "min_agent": "dispatch",
      "max_value": 0.2901,
      "min_value": 0.0138,
      "avg_value": 0.1611,
      "max_vs_avg": 1.8,
      "max_vs_min": 20.96
    },
    "tasks": { "..." : "same shape" },
    "errors": { "..." : "same shape" }
  }
}
```

**Field Reference:**

| Field | Type | Description |
|-------|------|-------------|
| `agents[].agent_id` | string | Agent identifier |
| `agents[].tasks_completed` | int | Tasks completed in range |
| `agents[].tasks_failed` | int | Tasks failed in range |
| `agents[].success_rate` | float\|null | `completed / (completed + failed) * 100`. Null if no tasks. |
| `agents[].avg_task_duration_ms` | int\|null | Average task duration. Null if no completed tasks. |
| `agents[].llm_call_count` | int | Number of LLM API calls |
| `agents[].llm_cost` | float | Total LLM cost in USD |
| `agents[].llm_tokens_in` | int | Total input tokens |
| `agents[].llm_tokens_out` | int | Total output tokens |
| `agents[].error_count` | int | Errors from `errors_by_type` or task/action failures |
| `agents[].errors_by_type` | dict | `{ "RateLimitError": 2, "TimeoutError": 1 }` |
| `agents[].errors_by_category` | dict | `{ "rate_limit": 2, "connectivity": 1 }` |
| `agents[].top_models` | list | Top 5 models by cost. Each: `{ model, calls, cost, tokens_in, tokens_out }` |
| `agents[].top_actions` | list | Top 5 actions by count. Each: `{ name, started, completed }` |
| `agents[].top_llm_calls` | list | Top 5 LLM call names by cost. Each: `{ name, count, tokens_in_sum, tokens_out_sum, cost_sum }` |
| `agents[].tasks_by_type` | dict | `{ "lead_qualification": { "started": 8, "completed": 7, "failed": 1 } }` |
| `fleet_totals` | object | Sum across all agents in response |
| `comparisons` | dict | Keys: `"cost"`, `"tasks"`, `"errors"`. Each has max/min agent and ratios. |

---

### 2. `GET /v1/insights/models`

**Purpose:** LLM model comparison. Answers: cheapest model, biggest prompts, agent-model matrix.

**Use for:** Model cost dashboard, model comparison table, "which agents use which models" view.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `"24h"` | Time range |
| `agent_id` | string | null | Filter to a specific agent's model usage |

**Response Shape:**

```json
{
  "range": "24h",
  "models": [
    {
      "model": "claude-sonnet-4-20250514",
      "call_count": 44,
      "tokens_in": 85033,
      "tokens_out": 15200,
      "cost": 0.6481,
      "avg_duration_ms": 2716,
      "max_tokens_in": 3955,
      "max_tokens_in_agent": "harper",
      "max_tokens_in_name": "classify_ticket",
      "agents_using": [
        { "agent_id": "scout", "calls": 16, "cost": 0.245, "tokens_in": 31974, "tokens_out": 9945 },
        { "agent_id": "harper", "calls": 16, "cost": 0.243, "tokens_in": 31556, "tokens_out": 9889 }
      ],
      "top_calls": [
        { "name": "draft_outreach_email", "count": 8, "cost_sum": 0.135 },
        { "name": "classify_ticket", "count": 8, "cost_sum": 0.121 }
      ]
    }
  ],
  "fleet_totals": {
    "total_cost": 0.9667,
    "total_tasks": 0,
    "total_errors": 0,
    "total_llm_calls": 80
  }
}
```

**Field Reference:**

| Field | Type | Description |
|-------|------|-------------|
| `models[].model` | string | Model identifier (e.g. `"claude-sonnet-4-20250514"`) |
| `models[].call_count` | int | Total calls to this model |
| `models[].tokens_in` / `tokens_out` | int | Total tokens |
| `models[].cost` | float | Total cost in USD |
| `models[].avg_duration_ms` | int\|null | Average call latency |
| `models[].max_tokens_in` | int | Largest single prompt sent to this model |
| `models[].max_tokens_in_agent` | string | Agent that sent the largest prompt |
| `models[].max_tokens_in_name` | string | Call name of the largest prompt |
| `models[].agents_using` | list | Top 10 agents by cost. Each: `{ agent_id, calls, cost, tokens_in, tokens_out }` |
| `models[].top_calls` | list | Top 10 call names by cost. Each: `{ name, count, cost_sum }` |

---

### 3. `GET /v1/insights/timeseries`

**Purpose:** Hourly time-series for any metric. Powers trend charts and sparklines.

**Use for:** Line charts, area charts, sparklines in agent cards.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `"24h"` | Time range |
| `agent_id` | string | null | Filter to a specific agent |
| `metric` | string | `"cost"` | Metric: `cost`, `tasks`, `errors`, `llm_calls`, `tokens` |

**Response Shape:**

```json
{
  "range": "24h",
  "agent_id": null,
  "metric": "cost",
  "buckets": [
    { "hour": "2026-02-14T05:00:00Z", "value": 0.0 },
    { "hour": "2026-02-14T06:00:00Z", "value": 0.0 },
    { "hour": "2026-02-14T07:00:00Z", "value": 0.1234 },
    { "hour": "2026-02-14T08:00:00Z", "value": 0.0 }
  ],
  "summary": {
    "total": 0.9667,
    "avg_per_hour": 0.0403,
    "peak_hour": "2026-02-15T04:00:00Z",
    "peak_value": 0.9667,
    "trough_hour": "2026-02-14T05:00:00Z",
    "trough_value": 0.0
  }
}
```

**Notes:**
- `buckets` always includes every hour in the range, even if value is 0 (zero-gap filling) — ready for direct chart rendering.
- `metric` values: `cost` = USD, `tasks` = count (completed+failed), `errors` = count, `llm_calls` = count, `tokens` = tokens_in + tokens_out.
- When `agent_id` is set, `agent_id` field in response reflects the filter.

---

### 4. `GET /v1/insights/errors`

**Purpose:** Error analysis by type, category, agent, and time.

**Use for:** Error dashboard, error breakdown charts, error trend line.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `"24h"` | Time range |
| `agent_id` | string | null | Filter to a specific agent |

**Response Shape:**

```json
{
  "range": "24h",
  "total_errors": 5,
  "by_agent": [
    {
      "agent_id": "scout",
      "error_count": 3,
      "task_failure_count": 1,
      "action_failure_count": 2,
      "by_type": { "RateLimitError": 2, "TimeoutError": 1 },
      "by_category": { "rate_limit": 2, "connectivity": 1 },
      "by_task_type": { "lead_qualification": 2, "data_enrichment": 1 },
      "by_action": { "enrich_company_data": 2, "search_kb": 1 }
    }
  ],
  "by_type_global": { "RateLimitError": 3, "TimeoutError": 2 },
  "by_category_global": { "rate_limit": 3, "connectivity": 2 },
  "by_task_type_global": { "lead_qualification": 3, "data_enrichment": 2 },
  "by_action_global": { "enrich_company_data": 3, "search_kb": 2 },
  "error_timeseries": [
    { "hour": "2026-02-14T05:00:00Z", "value": 0 },
    { "hour": "2026-02-14T06:00:00Z", "value": 3 },
    { "hour": "2026-02-14T07:00:00Z", "value": 2 }
  ]
}
```

**Field Reference:**

| Field | Type | Description |
|-------|------|-------------|
| `total_errors` | int | Total error count across all agents |
| `by_agent` | list | Per-agent error breakdown, sorted by error_count desc |
| `by_agent[].error_count` | int | Total errors for this agent |
| `by_agent[].task_failure_count` | int | `task_failed` events |
| `by_agent[].action_failure_count` | int | `action_failed` events |
| `by_agent[].by_type` | dict | `{ "RateLimitError": N }` — from exception/error type |
| `by_agent[].by_category` | dict | `{ "rate_limit": N }` — from issue category |
| `by_agent[].by_task_type` | dict | `{ "lead_qualification": N }` — errors broken down by task type |
| `by_agent[].by_action` | dict | `{ "enrich_company_data": N }` — errors broken down by action/tool name |
| `by_type_global` | dict | Merged across all agents |
| `by_category_global` | dict | Merged across all agents |
| `by_task_type_global` | dict | Task type error counts merged across all agents |
| `by_action_global` | dict | Action/tool error counts merged across all agents |
| `error_timeseries` | list | Hourly error counts with zero-gap filling |

---

### 5. `GET /v1/insights/prompts`

**Purpose:** Prompt/LLM-call-name analysis. Answers: biggest prompts, most expensive calls, which agents use which prompts.

**Use for:** Prompt optimization dashboard, cost-per-call breakdown.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `"24h"` | Time range |
| `agent_id` | string | null | Filter to a specific agent |
| `sort` | string | `"cost"` | Sort by: `cost`, `tokens`, `calls` |

**Response Shape:**

```json
{
  "range": "24h",
  "calls": [
    {
      "name": "draft_outreach_email",
      "total_count": 8,
      "avg_tokens_in": 2517,
      "max_tokens_in": 3200,
      "total_tokens_in": 20140,
      "total_tokens_out": 5001,
      "total_cost": 0.1354,
      "agents_using": ["scout"],
      "primary_model": "claude-sonnet-4-20250514"
    },
    {
      "name": "classify_ticket",
      "total_count": 8,
      "avg_tokens_in": 1931,
      "max_tokens_in": 3955,
      "total_tokens_in": 15454,
      "total_tokens_out": 4972,
      "total_cost": 0.1209,
      "agents_using": ["harper"],
      "primary_model": "claude-sonnet-4-20250514"
    }
  ],
  "biggest_prompt": {
    "name": "classify_ticket",
    "max_tokens_in": 3955,
    "primary_model": "claude-sonnet-4-20250514"
  }
}
```

**Field Reference:**

| Field | Type | Description |
|-------|------|-------------|
| `calls[].name` | string | Logical call name (e.g. `"lead_scoring"`) |
| `calls[].total_count` | int | Times this call was made |
| `calls[].avg_tokens_in` | int | Average input tokens per call |
| `calls[].max_tokens_in` | int | Largest single input to this call name |
| `calls[].total_tokens_in` / `total_tokens_out` | int | Total tokens |
| `calls[].total_cost` | float | Total cost in USD |
| `calls[].agents_using` | list[string] | Agent IDs that use this call |
| `calls[].primary_model` | string | Model with highest cost for this call |
| `biggest_prompt` | dict | The call with the largest `max_tokens_in` across all calls |

---

### 6. `GET /v1/insights/actions`

**Purpose:** Action/tool usage distribution with success/failure breakdown.

**Use for:** Tool usage dashboard, action reliability table.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `range` | string | `"24h"` | Time range |
| `agent_id` | string | null | Filter to a specific agent |
| `group_by` | string | `"name"` | Grouping: `name` (reserved for future: `agent`, `hour`) |

**Response Shape:**

```json
{
  "range": "24h",
  "actions": [
    {
      "name": "enrich_company_data",
      "total_started": 8,
      "total_completed": 8,
      "total_failed": 0,
      "success_rate": 100.0,
      "agents_using": { "scout": 8 },
      "hourly_avg": 8.0,
      "peak_hour": "2026-02-15T04:00:00Z",
      "peak_count": 8,
      "avg_duration_ms": 1240,
      "hourly_buckets": [
        { "hour": "2026-02-14T05:00:00Z", "started": 0, "completed": 0, "failed": 0 },
        { "hour": "2026-02-14T06:00:00Z", "started": 3, "completed": 3, "failed": 0 },
        { "hour": "2026-02-14T07:00:00Z", "started": 5, "completed": 5, "failed": 0 }
      ]
    },
    {
      "name": "search_kb",
      "total_started": 8,
      "total_completed": 8,
      "total_failed": 0,
      "success_rate": 100.0,
      "agents_using": { "harper": 8 },
      "hourly_avg": 8.0,
      "peak_hour": "2026-02-15T04:00:00Z",
      "peak_count": 8,
      "avg_duration_ms": 890,
      "hourly_buckets": [
        { "hour": "2026-02-14T05:00:00Z", "started": 0, "completed": 0, "failed": 0 },
        { "hour": "2026-02-14T06:00:00Z", "started": 4, "completed": 4, "failed": 0 },
        { "hour": "2026-02-14T07:00:00Z", "started": 4, "completed": 4, "failed": 0 }
      ]
    }
  ]
}
```

**Field Reference:**

| Field | Type | Description |
|-------|------|-------------|
| `actions[].name` | string | Action/tool name |
| `actions[].total_started` | int | Times action was started |
| `actions[].total_completed` | int | Times completed successfully |
| `actions[].total_failed` | int | Times action failed |
| `actions[].success_rate` | float\|null | `completed / (completed + failed) * 100`. Null if no completions. |
| `actions[].agents_using` | dict | `{ "agent_id": count }` — which agents use this action and how often |
| `actions[].hourly_avg` | float | Average starts per hour over the range |
| `actions[].peak_hour` | string | Hour with most activity (ISO 8601) |
| `actions[].peak_count` | int | Count during peak hour |
| `actions[].avg_duration_ms` | int\|null | Average duration of completed actions in ms. Null if no completions with duration data. |
| `actions[].hourly_buckets` | list | Zero-gap-filled hourly breakdown. Each: `{ hour, started, completed, failed }`. Same pattern as timeseries endpoint — every hour in range is present, ready for heatmap rendering. |

---

### 7. `POST /v1/admin/rebuild-aggregates` (Admin)

**Purpose:** Rebuild aggregate tables from raw events. Use after data migration or corruption.

**Query Parameters:** None

**Response Shape:**

```json
{
  "status": "rebuilt",
  "buckets": {
    "agent_hourly": 42,
    "model_hourly": 12
  }
}
```

---

## Common Patterns for Front-End

### Range Selector

All 6 endpoints accept the same `range` values. Use a shared dropdown/toggle:

```
1h | 6h | 24h | 7d | 30d | 90d
```

### Agent Filter

Endpoints 2-6 accept `?agent_id=` to drill down to one agent. Endpoint 1 accepts `?project_id=` instead. To build a "drill into agent" flow:

1. Show fleet overview with `/v1/insights/agents`
2. On agent click, load detail view using all other endpoints with `?agent_id=<clicked_agent>`

### Empty States

All endpoints return valid JSON with empty arrays when no data exists:

```json
{
  "range": "24h",
  "agents": [],
  "fleet_totals": { "total_cost": 0.0, "total_tasks": 0, "total_errors": 0, "total_llm_calls": 0 },
  "comparisons": {}
}
```

### Timeseries Chart Data

`/v1/insights/timeseries` returns zero-filled hourly buckets ready for charting:

```javascript
// Direct mapping to chart library
const labels = data.buckets.map(b => b.hour);
const values = data.buckets.map(b => b.value);
```

### Cost Formatting

All cost values are in USD as floats (e.g. `0.2901`). Format with:

```javascript
const formatCost = (v) => v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
```

### Sorting

The `sort` param on endpoints 1 and 5 controls server-side sort order. Results are always descending (highest first).

---

## Supporting Endpoints (Existing)

The insights page will also need these **existing** endpoints for context data (dropdowns, filters, navigation). These are already live — no changes needed.

### `GET /v1/agents`

**Use for:** Agent name dropdown, agent filter selector, fleet overview sidebar.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | null | Filter agents by project |
| `environment` | string | null | Filter by environment |
| `group` | string | null | Filter by group |
| `status` | string | null | Filter by derived status: `idle`, `processing`, `waiting_approval`, `error`, `stuck` |
| `sort` | string | `"last_seen"` | Sort by: `last_seen`, `attention`, `name` |
| `limit` | int | 50 | Max results (up to 200) |

**Response Shape:**

```json
{
  "data": [
    {
      "agent_id": "scout",
      "agent_type": "lead_gen",
      "agent_version": "1.2.0",
      "framework": "langchain",
      "runtime": "python3.12",
      "sdk_version": "0.1.0",
      "environment": "production",
      "group": "sales",
      "derived_status": "processing",
      "current_task_id": "task-abc",
      "current_project_id": "sales-pipeline",
      "last_heartbeat": "2026-02-15T04:58:00Z",
      "heartbeat_age_seconds": 12,
      "is_stuck": false,
      "stuck_threshold_seconds": 300,
      "first_seen": "2026-02-10T00:00:00Z",
      "last_seen": "2026-02-15T04:58:00Z",
      "last_event_type": "task_completed",
      "last_event_at": "2026-02-15T04:57:48Z",
      "stats_1h": {
        "tasks_completed": 3,
        "tasks_failed": 0,
        "success_rate": 100.0,
        "avg_duration_ms": 420,
        "total_cost": 0.12,
        "throughput": 3,
        "queue_depth": 2,
        "active_issues": 0
      }
    }
  ]
}
```

**Key fields for insights page:**
- `agent_id` — used as the value for `?agent_id=` filter on insights endpoints
- `derived_status` — show status badge in agent comparison table
- `last_event_type` — most recent event type for this agent (e.g. `"task_completed"`, `"llm_call_end"`)
- `last_event_at` — ISO 8601 timestamp of the most recent event
- `stats_1h` — quick 1-hour stats for agent cards (before user clicks into full insights)

---

### `GET /v1/agents/{agent_id}`

**Use for:** Agent detail header when drilling into a specific agent's insights.

**Response:** Same shape as a single item from `GET /v1/agents`, but unwrapped (no `data` wrapper).

---

### `GET /v1/projects`

**Use for:** Project dropdown/filter on the insights agents endpoint (`?project_id=`).

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `include_archived` | bool | false | Include archived projects |

**Response Shape:**

```json
{
  "data": [
    {
      "project_id": "uuid",
      "tenant_id": "uuid",
      "name": "Sales Pipeline",
      "slug": "sales-pipeline",
      "description": "Lead qualification and outreach",
      "environment": "production",
      "settings": {},
      "is_archived": false,
      "auto_created": false,
      "created_at": "2026-02-10T00:00:00Z",
      "updated_at": "2026-02-14T12:00:00Z",
      "event_count": 1234
    }
  ]
}
```

**Key fields for insights page:**
- `project_id` — used as the value for `?project_id=` filter on `/v1/insights/agents`
- `name` — display label in dropdown
- `slug` — URL-friendly identifier (also accepted as `project_id` by the backend)

---

### `GET /v1/projects/{project_id}/agents`

**Use for:** When filtering insights by project, get the list of agents in that project.

**Response:** Same shape as `GET /v1/agents` (wrapped in `{ "data": [...] }`).

---

### `GET /v1/cost`

**Use for:** Legacy cost summary for comparison or fallback. The new `/v1/insights/agents` and `/v1/insights/models` endpoints supersede this for the insights page, but it may be useful as a quick total.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | null | Filter by project |
| `agent_id` | string | null | Filter by agent |
| `range` | string | `"24h"` | Time range |

**Response Shape:**

```json
{
  "total_cost": 0.9667,
  "call_count": 80,
  "total_tokens_in": 156400,
  "total_tokens_out": 28800,
  "by_agent": [
    { "agent_id": "scout", "cost": 0.245, "call_count": 16 }
  ],
  "by_model": [
    { "model": "claude-sonnet-4-20250514", "cost": 0.648, "call_count": 44 }
  ],
  "reported_cost": 0.0,
  "estimated_cost": 0.9667
}
```

---

### `GET /v1/admin/pricing`

**Use for:** Display model pricing table so users understand cost calculations.

**Response Shape:**

```json
{
  "data": [
    {
      "model_pattern": "claude-sonnet-4*",
      "provider": "anthropic",
      "input_per_m": 3.0,
      "output_per_m": 15.0
    }
  ]
}
```

---

### `GET /v1/cost/timeseries`

**Use for:** Existing cost trend chart (hourly buckets). Can overlay with `/v1/insights/timeseries?metric=cost` for comparison.

**Query Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `project_id` | string | null | Filter by project |
| `agent_id` | string | null | Filter by agent |
| `range` | string | `"24h"` | Time range |
| `interval` | string | null | Bucket interval |

**Response Shape:**

```json
{
  "data": [
    {
      "timestamp": "2026-02-15T04:00:00Z",
      "cost": 0.9667,
      "call_count": 80,
      "tokens_in": 156400,
      "tokens_out": 28800
    }
  ]
}
```

---

## Endpoint Map: What to Call for Each UI Component

| UI Component | Primary Endpoint | Supporting Endpoint |
|-------------|-----------------|---------------------|
| **Fleet Status Table** | `GET /v1/agents` (status, heartbeat, `last_event_type`, `last_event_at`) | `GET /v1/insights/agents` (cost per agent for join) |
| **Fleet Overview Table** | `GET /v1/insights/agents` | `GET /v1/projects` (project filter dropdown) |
| **Agent Detail Panel** | `GET /v1/insights/agents?sort=cost` | `GET /v1/agents/{agent_id}` (header: status, version, heartbeat) |
| **Cost Trend Chart** | `GET /v1/insights/timeseries?metric=cost` | — |
| **Task Trend Chart** | `GET /v1/insights/timeseries?metric=tasks` | — |
| **Error Trend Chart** | `GET /v1/insights/timeseries?metric=errors` | — |
| **Token Trend Chart** | `GET /v1/insights/timeseries?metric=tokens` | — |
| **Model Comparison Table** | `GET /v1/insights/models` | `GET /v1/admin/pricing` (unit prices) |
| **Error Breakdown Panel** | `GET /v1/insights/errors` (`by_type`, `by_category`, `by_task_type`, `by_action`) | — |
| **Prompt Analysis Table** | `GET /v1/insights/prompts` | — |
| **Action/Tool Usage Table** | `GET /v1/insights/actions` (`hourly_buckets` for heatmaps, `avg_duration_ms` for skill table) | — |
| **Agent Filter Dropdown** | `GET /v1/agents` | — |
| **Project Filter Dropdown** | `GET /v1/projects` | — |
| **Range Selector** | *(client-side, pass to all endpoints)* | — |
| **Agent Drill-Down** | All `/v1/insights/*` with `?agent_id=X` | `GET /v1/agents/{agent_id}` (header) |

---

## Architecture Notes

- **Data source:** 2 new JSON files in `src/data/`: `agent_hourly.json` and `model_hourly.json`
- **Update timing:** Aggregates are updated in real-time during event ingestion (Step 7b of the ingest pipeline)
- **Retention:** Aggregate buckets are retained for 90 days (vs 7 days for free-tier raw events)
- **Granularity:** Always hourly buckets, keyed by `(tenant_id, agent_id|model, hour)`
- **No breaking changes:** All existing endpoints continue to work unchanged

---

## Files Changed

| File | What Changed |
|------|-------------|
| `src/backend/aggregator.py` | **NEW** — Aggregation logic (~200 lines) |
| `src/backend/app.py` | Added 7 endpoints + ingest hook + prune hook |
| `src/shared/models.py` | Added 14 Pydantic response models |
| `src/shared/enums.py` | Added `AGGREGATE_RETENTION_DAYS = 90`, `"90d"` range |
| `src/backend/storage_json.py` | Added 2 table files + `prune_aggregates()` |

# Sensor-to-API Endpoint Mapping

How each SDK sensor surfaces through the API — the bridge between
what sensors capture and what the UI/UX can display.

---

## 1. Endpoint Catalog

The HiveBoard API has **18 sensor-related endpoints** (organized by the
UI/UX concern they serve), plus infrastructure endpoints for auth,
projects, alerts, and admin that are not sensor-driven.

| # | Endpoint | Method | UI/UX Purpose |
|---|----------|--------|---------------|
| 1 | `/v1/agents` | GET | Fleet overview — all agents with status |
| 2 | `/v1/agents/{id}` | GET | Single agent detail card |
| 3 | `/v1/agents/{id}/pipeline` | GET | Agent work state (queue, todos, issues, schedule) |
| 4 | `/v1/pipeline` | GET | Fleet-wide work state totals |
| 5 | `/v1/tasks` | GET | Task list/table with filtering |
| 6 | `/v1/tasks/{id}/timeline` | GET | Single task deep-dive (action tree, plan, errors) |
| 7 | `/v1/events` | GET | Raw event stream / activity log |
| 8 | `/v1/metrics` | GET | Aggregate metrics with timeseries |
| 9 | `/v1/cost` | GET | Cost summary by agent and model |
| 10 | `/v1/cost/calls` | GET | Individual LLM call records |
| 11 | `/v1/cost/timeseries` | GET | Cost over time |
| 12 | `/v1/llm-calls` | GET | LLM call browser with totals |
| 13 | `/v1/insights/agents` | GET | Agent comparison analytics |
| 14 | `/v1/insights/models` | GET | Model comparison analytics |
| 15 | `/v1/insights/timeseries` | GET | Metric-over-time analytics |
| 16 | `/v1/insights/errors` | GET | Error breakdown analytics |
| 17 | `/v1/insights/prompts` | GET | Prompt/call-name analytics |
| 18 | `/v1/insights/actions` | GET | Action performance analytics |
| — | `/v1/stream` | WebSocket | Real-time event + status push |
| — | `/v1/ingest` | POST | *(write path — not a UI read endpoint)* |

---

## 2. Sensor → Endpoint Matrix

Which sensors feed which endpoints. Read across a row to see where a
sensor's data appears in the API. Read down a column to see which
sensors power a given endpoint.

| Sensor | SDK Function | agents | agent/{id} | pipeline | tasks | timeline | events | metrics | cost | insights |
|--------|-------------|:------:|:----------:|:--------:|:-----:|:--------:|:------:|:-------:|:----:|:--------:|
| Registration | `hb.agent()` | **I** | **I** | | | | R | | | |
| Heartbeat | *(auto)* | **S** | **S** | | | | R | | | |
| Heartbeat Payload | `heartbeat_payload` cb | | | | | | R | | | |
| Queue Snapshot | `agent.queue_snapshot()` | St | St | **Q** | | | R | | | |
| Todo | `agent.todo()` | | | **T** | | | R | | | |
| Scheduled | `agent.scheduled()` | | | **Sc** | | | R | | | |
| Issue Report | `agent.report_issue()` | St | St | **Is** | | | R | | | E |
| Issue Resolve | `agent.resolve_issue()` | | | **Is** | | | R | | | E |
| Task Start | `agent.task()` enter | **S** | **S** | | **D** | **D** | R | M | | A,Ts |
| Task Complete | `agent.task()` exit OK | **S,St** | **S,St** | | **D** | **D** | R | M | | A,Ts |
| Task Fail | `agent.task()` exit err | **S,St** | **S,St** | | **D** | **D,Er** | R | M | | A,Ts,E |
| Action Start | `@agent.track()` enter | **S** | **S** | | D | **AT** | R | | | Ac |
| Action Complete | `@agent.track()` exit OK | | | | D | **AT** | R | | | Ac |
| Action Fail | `@agent.track()` exit err | **S** | **S** | | D | **AT,Er** | R | | | Ac,E |
| LLM Call | `task.llm_call()` | St | St | | D | D | R | M | **C** | A,Mo,P |
| Plan Created | `task.plan()` | | | | | **Pl** | R | | | |
| Plan Step | `task.plan_step()` | | | | | **Pl** | R | | | |
| Escalation | `task.escalate()` | | | | D | **Er** | R | M | | Ts |
| Approval Req | `task.request_approval()` | **S** | **S** | | D | D | R | | | Ts |
| Approval Rcv | `task.approval_received()` | | | | D | D | R | | | Ts |
| Retry | `task.retry()` | | | | | **Er** | R | | | Ts |
| Custom Event | `task.event()` | | | | | D | **R** | | | |
| Log Bridge | `HiveBoardLogHandler` | St | St | **Is** | | | R | | | E |

**Legend**: **I**=Identity, **S**=Status, **St**=Stats, **D**=Derived fields,
**R**=Raw event, **M**=Metrics aggregation, **C**=Cost analysis,
**Q**=Queue, **T**=Todos, **Sc**=Scheduled, **Is**=Issues,
**AT**=Action tree, **Pl**=Plan overlay, **Er**=Error chains,
**A**=Agent insights, **Mo**=Model insights, **P**=Prompt insights,
**Ac**=Action insights, **Ts**=Timeseries insights, **E**=Error insights

---

## 3. Endpoint Detail Cards

### 3.1 `GET /v1/agents` — Fleet Overview

**UI/UX purpose**: Dashboard home — shows all agents with status badges,
enabling operators to spot problems at a glance.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter by project |
| `environment` | `str` | — | Filter by env |
| `group` | `str` | — | Filter by group |
| `status` | `str` | — | Filter by derived status |
| `sort` | `str` | `"last_seen"` | `last_seen` / `attention` / `name` |
| `limit` | `int` | `50` | Max results (≤200) |

**Response**: `AgentSummary[]` — one card per agent.

**Sensors that feed this endpoint**:

| Response Field | Source Sensor(s) | How |
|---------------|-----------------|-----|
| `agent_id`, `agent_type`, `framework`, `runtime`, `sdk_version`, `environment`, `group` | **Registration** | Direct from `agents.json` (set by `agent_registered` event) |
| `derived_status` | **Heartbeat** + latest event | Status cascade: stuck uses `last_heartbeat` age; error/waiting/processing uses `last_event_type` |
| `current_task_id`, `current_project_id` | **Task Start** | `agents.json.last_task_id` / `last_project_id` |
| `last_heartbeat`, `heartbeat_age_seconds`, `is_stuck` | **Heartbeat** | `agents.json.last_heartbeat` vs `now` |
| `stats_1h.tasks_completed/failed` | **Task Complete/Fail** | Count of terminal task events in 1h window |
| `stats_1h.success_rate` | **Task Complete/Fail** | `completed / (completed + failed) * 100` |
| `stats_1h.avg_duration_ms` | **Task Complete/Fail** | Mean of `duration_ms` from terminal events |
| `stats_1h.total_cost` | **LLM Call** | Sum of `payload.data.cost` in 1h window |
| `stats_1h.queue_depth` | **Queue Snapshot** | Latest `queue_snapshot` payload's `depth` |
| `stats_1h.active_issues` | **Issue Report/Resolve** | Count of unresolved issues |

**Source file**: `app.py:703-738`

---

### 3.2 `GET /v1/agents/{id}` — Agent Detail

**UI/UX purpose**: Single-agent detail panel — same data as fleet view
but for one agent.

Same response shape as fleet list item (`AgentSummary`). Same sensor inputs.

**Source file**: `app.py:743-757`

---

### 3.3 `GET /v1/agents/{id}/pipeline` — Agent Pipeline

**UI/UX purpose**: Work-in-progress view — what the agent is working on,
what's queued, what's broken.

**Response**: `PipelineState`

**Sensors that feed this endpoint**:

| Response Field | Source Sensor | Reconstruction Logic |
|---------------|--------------|---------------------|
| `queue` | **Queue Snapshot** | Latest `custom` event with `kind=queue_snapshot` → return `payload.data` |
| `todos[]` | **Todo** | All `custom` events with `kind=todo` → group by `todo_id` → take latest action → keep only active (created/failed/deferred) |
| `scheduled[]` | **Scheduled** | Latest `custom` event with `kind=scheduled` → return `payload.data.items` |
| `issues[]` | **Issue Report + Resolve** | All `custom` events with `kind=issue` → group by `issue_id` → take latest → keep only where `action ≠ resolved` |

**Source file**: `app.py:762-770`

---

### 3.4 `GET /v1/pipeline` — Fleet Pipeline

**UI/UX purpose**: Ops dashboard — aggregate work state across all agents.

**Response**: `FleetPipelineState`

| Response Field | Source |
|---------------|--------|
| `totals.queue_depth` | Sum of all agents' queue depths |
| `totals.active_todos` | Sum of all agents' active todo counts |
| `totals.active_issues` | Sum of all agents' active issue counts |
| `totals.scheduled_count` | Sum of all agents' scheduled item counts |
| `agents[]` | Per-agent breakdown of the above |

**Source file**: `app.py:775-782`

---

### 3.5 `GET /v1/tasks` — Task List

**UI/UX purpose**: Task table — filterable, sortable list of all tasks.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter by project |
| `agent_id` | `str` | — | Filter by agent |
| `task_type` | `str` | — | Filter by type |
| `status` | `str` | — | Filter by derived status |
| `environment` | `str` | — | Filter by env |
| `since` / `until` | `ISO 8601` | — | Time range |
| `sort` | `str` | `"newest"` | `newest` / `oldest` |
| `limit` | `int` | `50` | Max results (≤200) |
| `cursor` | `str` | — | Pagination cursor |

**Response**: `TaskSummary[]` (paginated)

**Sensors that feed this endpoint**:

| Response Field | Source Sensor(s) | How |
|---------------|-----------------|-----|
| `task_id`, `task_type`, `task_run_id`, `agent_id`, `project_id` | **Task Start** | From the `task_started` event fields |
| `derived_status` | **Task Start/Complete/Fail + Escalation + Approval** | Status cascade: completed > failed > escalated > waiting > processing |
| `started_at` | **Task Start** | Timestamp of `task_started` event |
| `completed_at`, `duration_ms` | **Task Complete/Fail** | From terminal event |
| `total_cost` | **LLM Call** | Sum of `cost` from all `llm_call` events within the task |
| `action_count` | **Action Start** | Count of `action_started` events within the task |
| `error_count` | **Action Fail + Task Fail** | Count of `*_failed` events |
| `has_escalation` | **Escalation** | `escalated` event exists within task |
| `has_human_intervention` | **Approval Req/Rcv** | `approval_*` event exists within task |
| `llm_call_count` | **LLM Call** | Count of `llm_call` events |
| `total_tokens_in/out` | **LLM Call** | Sum of token fields |

**Source file**: `app.py:787-811`

---

### 3.6 `GET /v1/tasks/{id}/timeline` — Task Deep-Dive

**UI/UX purpose**: The most detailed view — shows the complete story of
a single task execution with nested actions, plan progress, and error
causality.

**Response**: `TimelineSummary`

**Sensors that feed this endpoint**:

| Response Section | Source Sensor(s) | Reconstruction |
|-----------------|-----------------|----------------|
| **`events[]`** | **All sensors** | Raw event array, chronological. Every sensor call that happened within this task appears here. |
| **`action_tree[]`** | **Action Start/Complete/Fail** | Build dict by `action_id` from `action_started` events (extract `action_name` from payload). Attach status + `duration_ms` from terminal events. Nest children under parents via `parent_action_id`. |
| **`plan`** | **Plan Created + Plan Step** | From `plan_created` event: extract `goal`, `steps[]`, `revision`. From `plan_step` events: overlay `action` (started/completed/failed) and timestamps onto each step. Compute `progress: {completed, total}`. |
| **`error_chains[]`** | **Retry + Escalation** | All `retry_started` and `escalated` events that have `parent_event_id` → linked list showing cause → retry/escalation chain. |
| `derived_status` | Task lifecycle | Same cascade as task list |
| `total_cost` | **LLM Call** | Sum of costs from `llm_call` events |
| `duration_ms` | **Task Complete/Fail** | From terminal event |

**Source file**: `app.py:816-969`

---

### 3.7 `GET /v1/events` — Raw Event Stream

**UI/UX purpose**: Activity log — every event from every sensor,
filterable.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter by project |
| `agent_id` | `str` | — | Filter by agent |
| `task_id` | `str` | — | Filter by task |
| `event_type` | `str` | — | Filter by type (e.g. `task_started`) |
| `severity` | `str` | — | Filter by severity |
| `environment` | `str` | — | Filter by env |
| `group` | `str` | — | Filter by group |
| `since` / `until` | `ISO 8601` | — | Time range |
| `exclude_heartbeats` | `bool` | `true` | Hide noisy heartbeat events |
| `payload_kind` | `str` | — | Filter by `payload.kind` (e.g. `llm_call`, `todo`) |
| `limit` | `int` | `50` | Max results (≤200) |
| `cursor` | `str` | — | Pagination cursor |

**Response**: `Event[]` (paginated)

**All 23 sensors** produce events visible here. This is the universal
raw data endpoint. The `payload_kind` filter is especially useful for
isolating specific sensor types:

| `payload_kind` filter | Shows events from sensor |
|----------------------|------------------------|
| `llm_call` | LLM Call |
| `queue_snapshot` | Queue Snapshot |
| `todo` | Todo |
| `scheduled` | Scheduled |
| `plan_created` | Plan Created |
| `plan_step` | Plan Step |
| `issue` | Issue Report / Resolve |

**Source file**: `app.py:974-1013`

---

### 3.8 `GET /v1/metrics` — Aggregate Metrics

**UI/UX purpose**: KPI dashboard — headline numbers and trends.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter by project |
| `agent_id` | `str` | — | Filter by agent |
| `environment` | `str` | — | Filter by env |
| `metric` | `str` | — | Specific metric |
| `group_by` | `str` | — | `agent` or `model` |
| `range` | `str` | `"1h"` | Time range |
| `interval` | `str` | auto | Bucket interval |

**Response**: `MetricsResponse` — `summary` + `timeseries[]` + optional `groups[]`

**Sensors that feed this endpoint**:

| Response Field | Source Sensor(s) |
|---------------|-----------------|
| `summary.total_tasks` | **Task Start** (distinct task_ids) |
| `summary.completed/failed/escalated` | **Task Complete/Fail/Escalation** |
| `summary.success_rate` | Task Complete / (Complete + Fail) |
| `summary.avg_duration_ms` | **Task Complete/Fail** (mean of `duration_ms`) |
| `summary.total_cost` | **LLM Call** (sum of costs) |
| `summary.stuck` | **Heartbeat** (agents where status = stuck) |
| `timeseries[].tasks_completed/failed` | **Task Complete/Fail** per bucket |
| `timeseries[].cost` | **LLM Call** per bucket |

**Source file**: `app.py:1018-1041`

---

### 3.9 `GET /v1/cost` — Cost Summary

**UI/UX purpose**: Cost management — total spend, by-agent, by-model.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter by project |
| `agent_id` | `str` | — | Filter by agent |
| `range` | `str` | `"24h"` | Time range |

**Response**: `CostSummary`

**Driven entirely by the LLM Call sensor**:

| Response Field | Source |
|---------------|--------|
| `total_cost` | Sum of `payload.data.cost` |
| `call_count` | Count of `llm_call` events |
| `total_tokens_in/out` | Sum of token fields |
| `by_agent[]` | Grouped by `agent_id` |
| `by_model[]` | Grouped by `payload.data.model` |
| `reported_cost` | Where `cost_source = "reported"` (SDK-provided) |
| `estimated_cost` | Where `cost_source = "estimated"` (server-calculated) |

**Source file**: `app.py:1048-1060`

---

### 3.10 `GET /v1/cost/calls` — Individual LLM Calls

**UI/UX purpose**: Cost drill-down — browse individual LLM calls.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter by project |
| `agent_id` | `str` | — | Filter by agent |
| `model` | `str` | — | Filter by model |
| `since` / `until` | `ISO 8601` | — | Time range |
| `limit` | `int` | `50` | Max results (≤200) |
| `cursor` | `str` | — | Pagination cursor |

**Response**: `LlmCallRecord[]` (paginated)

Each record maps 1:1 to a single **LLM Call** sensor emission.

| Response Field | Source (from `payload.data`) |
|---------------|---------------------------|
| `event_id` | Event ID |
| `agent_id`, `project_id`, `task_id` | Event context fields |
| `timestamp` | Event timestamp |
| `name` | `payload.data.name` |
| `model` | `payload.data.model` |
| `tokens_in/out` | `payload.data.tokens_in/out` |
| `cost` | `payload.data.cost` |
| `duration_ms` | `payload.data.duration_ms` |
| `cost_source` | `"reported"` or `"estimated"` |
| `prompt_preview`, `response_preview` | `payload.data.*_preview` |

**Source file**: `app.py:1063-1081`

---

### 3.11 `GET /v1/cost/timeseries` — Cost Over Time

**UI/UX purpose**: Cost trend chart.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `project_id` | `str` | — | Filter |
| `agent_id` | `str` | — | Filter |
| `range` | `str` | `"24h"` | Time range |
| `interval` | `str` | auto | Bucket interval |

**Response**: `CostTimeBucket[]`

**Driven by LLM Call sensor**: Each bucket sums `cost`, `call_count`,
`tokens_in`, `tokens_out` within the time interval.

**Source file**: `app.py:1084-1098`

---

### 3.12 `GET /v1/llm-calls` — LLM Call Browser

**UI/UX purpose**: Unified LLM call explorer with totals.

Same data as `/v1/cost/calls` but with a `totals` wrapper:

| Extra Field | Computation |
|------------|-------------|
| `totals.cost` | Sum of page costs |
| `totals.tokens_in/out` | Sum of page tokens |
| `totals.call_count` | Count on page |

Also accepts `task_id` filter (useful for task detail drilldown).

**Source file**: `app.py:1101-1133`

---

### 3.13 `GET /v1/insights/agents` — Agent Comparison

**UI/UX purpose**: Analytics — compare agents side by side on cost,
tasks, errors, models used.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `range` | `str` | `"24h"` | Time window |
| `project_id` | `str` | — | Filter by project |
| `sort` | `str` | `"cost"` | `cost` / `tasks` / `errors` / `llm_calls` |

**Response**: `InsightsAgentsResponse` — per-agent detail + fleet totals + comparisons

**Data source**: `agent_hourly.json` (pre-aggregated)

**Sensors that flow into `agent_hourly` and thus into this endpoint**:

| Response Field | Aggregated From Sensor |
|---------------|----------------------|
| `tasks_completed/failed` | **Task Complete/Fail** |
| `success_rate`, `avg_task_duration_ms` | **Task Complete/Fail** |
| `llm_call_count`, `llm_cost`, `llm_tokens_*` | **LLM Call** |
| `error_count` | **Task Fail + Action Fail + Issue Report** |
| `errors_by_type` | **Task Fail + Action Fail** (exception_type) |
| `errors_by_category` | **Issue Report** (category) |
| `top_models[]` | **LLM Call** (grouped by model) |
| `top_actions[]` | **Action Start** (grouped by action_name) |
| `top_llm_calls[]` | **LLM Call** (grouped by call name) |
| `tasks_by_type` | **Task Start** (grouped by task_type) |
| `fleet_totals` | All above summed across agents |
| `comparisons` | Max/min/avg per metric across agents |

**Source file**: `app.py:1193-1335`

---

### 3.14 `GET /v1/insights/models` — Model Comparison

**UI/UX purpose**: Analytics — compare LLM models on cost, tokens, latency.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `range` | `str` | `"24h"` | Time window |
| `agent_id` | `str` | — | Filter to single agent |

**Response**: `InsightsModelsResponse` — per-model detail + fleet totals

**Data source**: `model_hourly.json` (pre-aggregated)

**Driven entirely by LLM Call sensor**:

| Response Field | Source |
|---------------|--------|
| `model` | `payload.data.model` |
| `call_count`, `tokens_in/out`, `cost` | Summed from hourly buckets |
| `avg_duration_ms` | `duration_sum_ms / call_count` |
| `max_tokens_in`, `max_tokens_in_agent` | Tracked across all calls |
| `agents_using[]` | Per-agent call/cost/token breakdown |
| `top_calls[]` | Per-call-name breakdown within this model |

**Source file**: `app.py:1340-1441`

---

### 3.15 `GET /v1/insights/timeseries` — Metric Over Time

**UI/UX purpose**: Trend analysis — any metric plotted over time with
peak/trough detection.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `range` | `str` | `"24h"` | Time window |
| `agent_id` | `str` | — | Filter to single agent |
| `metric` | `str` | `"cost"` | `cost` / `tasks` / `errors` / `llm_calls` / `tokens` |

**Response**: `InsightsTimeseriesResponse` — hourly buckets + summary (total, avg, peak, trough)

**Data source**: `agent_hourly.json`

| Metric | Sensor source |
|--------|--------------|
| `cost` | **LLM Call** → `llm_cost` |
| `tasks` | **Task Complete + Fail** → `tasks_completed + tasks_failed` |
| `errors` | **Task Fail + Action Fail + Issue Report** → `errors_by_type` values |
| `llm_calls` | **LLM Call** → `llm_call_count` |
| `tokens` | **LLM Call** → `llm_tokens_in + llm_tokens_out` |

**Source file**: `app.py:1446-1528`

---

### 3.16 `GET /v1/insights/errors` — Error Breakdown

**UI/UX purpose**: Error analysis — find error hotspots by agent, type,
category, task type, action.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `range` | `str` | `"24h"` | Time window |
| `agent_id` | `str` | — | Filter to single agent |

**Response**: `InsightsErrorsResponse`

**Data source**: `agent_hourly.json`

| Response Field | Sensor Source |
|---------------|--------------|
| `total_errors` | **Task Fail + Action Fail** |
| `by_agent[].error_count` | Per-agent totals |
| `by_agent[].task_failure_count` | **Task Fail** per agent |
| `by_agent[].action_failure_count` | **Action Fail** per agent |
| `by_type_global` | **Task Fail + Action Fail** (grouped by `exception_type`) |
| `by_category_global` | **Issue Report** (grouped by `category`) |
| `by_task_type_global` | **Task Fail** (grouped by `task_type`) |
| `by_action_global` | **Action Fail** (grouped by `action_name`) |
| `error_timeseries[]` | Error count per hour |

**Source file**: `app.py:1533-1628`

---

### 3.17 `GET /v1/insights/prompts` — Prompt Analysis

**UI/UX purpose**: LLM call optimization — find expensive or frequent
prompts, compare token usage by call name.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `range` | `str` | `"24h"` | Time window |
| `agent_id` | `str` | — | Filter to single agent |
| `sort` | `str` | `"cost"` | `cost` / `tokens` / `calls` |

**Response**: `InsightsPromptsResponse` — per-call-name detail + biggest prompt

**Data source**: `agent_hourly.json` + `model_hourly.json`

**Driven entirely by LLM Call sensor**:

| Response Field | Source |
|---------------|--------|
| `name` | `payload.data.name` from LLM Call |
| `total_count` | Count of calls with this name |
| `avg_tokens_in`, `max_tokens_in`, `total_tokens_in/out` | Token stats grouped by name |
| `total_cost` | Cost grouped by name |
| `agents_using[]` | Which agents use this call name |
| `primary_model` | Model with highest cost for this name |
| `biggest_prompt` | Call name with highest `max_tokens_in` |

**Source file**: `app.py:1633-1735`

---

### 3.18 `GET /v1/insights/actions` — Action Performance

**UI/UX purpose**: Operational health — which actions succeed/fail, how
fast, hourly heatmap.

| Query Param | Type | Default | Description |
|------------|------|---------|-------------|
| `range` | `str` | `"24h"` | Time window |
| `agent_id` | `str` | — | Filter to single agent |
| `group_by` | `str` | `"name"` | Grouping |

**Response**: `InsightsActionsResponse` — per-action detail with hourly buckets

**Data source**: `agent_hourly.json`

**Driven by Action Start/Complete/Fail sensors**:

| Response Field | Source |
|---------------|--------|
| `name` | `action_name` from Action Start payload |
| `total_started/completed/failed` | Counters from `actions_by_name` aggregate |
| `success_rate` | `completed / (completed + failed) * 100` |
| `agents_using` | `{agent_id: invocation_count}` |
| `hourly_avg` | `total_started / hours_in_range` |
| `peak_hour`, `peak_count` | Hour with most invocations |
| `avg_duration_ms` | `duration_sum_ms / duration_count` |
| `hourly_buckets[]` | Per-hour `{started, completed, failed}` for heatmap |

**Source file**: `app.py:1740-1845`

---

### 3.19 `WebSocket /v1/stream` — Real-Time Push

**UI/UX purpose**: Live dashboard updates without polling.

**Channels**: `events`, `agents`

**Subscription filters**: `project_id`, `agent_id`, `environment`, `group`,
`event_types[]`, `min_severity`

**Messages pushed**:

| Message Type | Trigger Sensor | Payload |
|-------------|---------------|---------|
| `event.new` | **Any sensor** | Full event object (same as `/v1/events` item) |
| `agent.status_changed` | **Any sensor** (when `derived_status` changes) | `{agent_id, previous_status, new_status, current_task_id, current_project_id, heartbeat_age_seconds}` |
| `agent.stuck` | **Heartbeat** (when stuck detected) | `{agent_id, last_heartbeat, threshold_seconds, current_task_id, current_project_id}` |
| `pong` | Client ping | `{server_time}` |

**Source file**: `app.py:2826-2861`

---

## 4. Sensor Importance by UI/UX Screen

Which sensors are critical (must-have), important (should-have), or
optional (nice-to-have) for each UI screen.

### 4.1 Fleet Dashboard (`/v1/agents`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | Registration | Agent identity — without this, agent doesn't exist |
| Critical | Heartbeat | Liveness detection and stuck alerts |
| Critical | Task Start/Complete/Fail | Status indicator (processing/idle/error) + stats |
| Important | LLM Call | Cost in stats card |
| Important | Issue Report | Active issue count in stats card |
| Optional | Queue Snapshot | Queue depth in stats card |

### 4.2 Agent Detail (`/v1/agents/{id}` + `/v1/agents/{id}/pipeline`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | Registration + Heartbeat | Identity + liveness |
| Critical | Todo | Active work items list |
| Critical | Issue Report/Resolve | Active issues list |
| Important | Queue Snapshot | Work queue visualization |
| Important | Scheduled | Upcoming work schedule |
| Important | Task Start/Complete/Fail | Recent task history |
| Optional | LLM Call | Cost attribution |

### 4.3 Task List (`/v1/tasks`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | Task Start/Complete/Fail | Core task data — every row |
| Important | LLM Call | Cost per task |
| Important | Action Start/Fail | Action count, error count |
| Important | Escalation | Escalation badge |
| Optional | Approval Req/Rcv | Human-in-the-loop badge |

### 4.4 Task Timeline (`/v1/tasks/{id}/timeline`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | Task Start/Complete/Fail | Task boundary and status |
| Critical | Action Start/Complete/Fail | Action tree — the core visualization |
| Important | LLM Call | Cost overlay on actions |
| Important | Plan Created + Plan Step | Plan progress bar |
| Important | Retry + Escalation | Error chains |
| Optional | Approval Req/Rcv | Approval events in timeline |
| Optional | Custom Event | Additional context events |

### 4.5 Cost Analytics (`/v1/cost`, `/v1/cost/*`, `/v1/insights/models`, `/v1/insights/prompts`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | LLM Call | **Everything** — cost, tokens, model, call name, latency |
| — | *(no other sensors needed)* | — |

### 4.6 Error Analytics (`/v1/insights/errors`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | Task Fail | Task failure counts and exception types |
| Critical | Action Fail | Action failure counts and exception types |
| Important | Issue Report | Error categories (connectivity, permissions, etc.) |
| Optional | Retry | Retry patterns |

### 4.7 Action Analytics (`/v1/insights/actions`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | Action Start/Complete/Fail | **Everything** — counts, success rate, duration, heatmap |
| — | *(no other sensors needed)* | — |

### 4.8 Real-Time Dashboard (WebSocket `/v1/stream`)

| Priority | Sensor | What it provides |
|----------|--------|-----------------|
| Critical | All sensors | `event.new` — every event pushes to connected clients |
| Critical | Heartbeat | `agent.stuck` — stuck detection alert |
| Critical | Any status-changing sensor | `agent.status_changed` — status badge updates |

---

## 5. Sensor Coverage Summary

| Sensor | Endpoints Reached | UI Screens Impacted |
|--------|------------------|-------------------|
| **LLM Call** | 12 endpoints | Fleet, Tasks, Timeline, Cost (4 endpoints), Insights (4 endpoints), Events |
| **Task Start/Complete/Fail** | 10 endpoints | Fleet, Tasks, Timeline, Metrics, Insights (agents, timeseries, errors), Events |
| **Action Start/Complete/Fail** | 7 endpoints | Tasks, Timeline, Insights (agents, errors, actions), Events |
| **Heartbeat** | 4 endpoints | Fleet (stuck), Agent Detail, WebSocket, Events |
| **Registration** | 3 endpoints | Fleet, Agent Detail, Events |
| **Issue Report/Resolve** | 5 endpoints | Fleet (stats), Pipeline, Insights (agents, errors), Events |
| **Todo** | 2 endpoints | Pipeline, Events |
| **Queue Snapshot** | 3 endpoints | Fleet (stats), Pipeline, Events |
| **Escalation** | 4 endpoints | Tasks, Timeline, Metrics, Events |
| **Plan Created/Step** | 2 endpoints | Timeline, Events |
| **Retry** | 2 endpoints | Timeline, Events |
| **Scheduled** | 2 endpoints | Pipeline, Events |
| **Approval Req/Rcv** | 4 endpoints | Fleet (status), Tasks, Timeline, Events |
| **Custom Event** | 2 endpoints | Timeline, Events |
| **Heartbeat Payload** | 1 endpoint | Events |
| **Log Bridge** | 5 endpoints | *(same as Issue Report)* |

**Highest-impact sensors** (by endpoint reach):
1. **LLM Call** — 12 endpoints (the backbone of cost/analytics)
2. **Task lifecycle** — 10 endpoints (the backbone of operational monitoring)
3. **Action lifecycle** — 7 endpoints (the backbone of execution tracing)

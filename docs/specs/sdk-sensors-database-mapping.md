# Sensor-to-Database Mapping

How SDK sensor data flows from an agent, through the ingest pipeline, into
JSON storage tables, and back out as derived views.

---

## 1. The Data Pipeline

```
SDK Sensor Call
    │
    ▼
Transport Queue  (in-memory deque, max 10k items)
    │
    ▼
HTTP POST /v1/ingest   (batched, up to 500 events)
    │
    ├──► events.json          (every event, fully denormalized)
    ├──► agents.json          (upsert agent profile cache)
    ├──► project_agents.json  (upsert agent↔project link)
    ├──► agent_hourly.json    (incremental hourly aggregates per agent)
    └──► model_hourly.json    (incremental hourly aggregates per LLM model)
```

Every sensor call produces one event. That event always lands in
`events.json`. The ingest endpoint also updates up to four secondary
tables as side effects. All downstream views (agent summary, task list,
pipeline, metrics, cost, timeline, insights) are **derived on read** from
these five tables.

---

## 2. Storage Tables Overview

### 2.1 Primary Table: `events.json`

The single source of truth. Every SDK sensor call produces exactly one row here.

**Schema** (fully denormalized — envelope fields merged into each event):

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| `event_id` | `str` | SDK (UUID4) | Globally unique, client-generated |
| `tenant_id` | `str` | Server (from API key) | Owning tenant |
| `agent_id` | `str` | Envelope / event | Agent that emitted |
| `agent_type` | `str` | Envelope | Agent classification |
| `project_id` | `str \| null` | Event (task context) | Project scope |
| `timestamp` | `ISO 8601` | SDK | Client-generated UTC timestamp |
| `received_at` | `ISO 8601` | Server | Server receipt time |
| `environment` | `str` | Envelope | `dev` / `staging` / `production` |
| `group` | `str` | Envelope | Team/service grouping |
| `task_id` | `str \| null` | Event (task context) | Parent task |
| `task_type` | `str \| null` | Event (task context) | Task classification |
| `task_run_id` | `str \| null` | Event (task context) | Unique per execution run |
| `correlation_id` | `str \| null` | Event (task context) | Cross-agent correlation |
| `action_id` | `str \| null` | Event | Action ID for nesting |
| `parent_action_id` | `str \| null` | Event | Parent action (tree structure) |
| `event_type` | `str` | Event | One of 13 EventType values |
| `severity` | `str` | Event / auto-defaulted | `debug` / `info` / `warn` / `error` |
| `status` | `str \| null` | Event | `success` / `failure` (terminal events) |
| `duration_ms` | `int \| null` | Event | Wall-clock time (task/action completion) |
| `parent_event_id` | `str \| null` | Event | Causal link to predecessor |
| `payload` | `dict \| null` | Event | Up to 32KB; structure depends on `kind` |
| `key_type` | `str \| null` | Server | `live` / `test` (for isolation) |

**Pydantic model**: `Event` (`src/shared/models.py:161`)

**Retention**: Per-tenant TTL (plan-based: free=7d, pro=30d, enterprise=90d).
Cold events (`heartbeat`: 10min, `action_started`: 24h) have shorter retention.

---

### 2.2 Agent Profile Cache: `agents.json`

One row per agent. Updated on **every** ingest batch via `upsert_agent()`.

| Field | Type | Updated When | Description |
|-------|------|-------------|-------------|
| `agent_id` | `str` | Create | Unique agent identifier |
| `tenant_id` | `str` | Create | Owning tenant |
| `agent_type` | `str` | Every batch | Classification |
| `agent_version` | `str \| null` | Every batch | Version string |
| `framework` | `str \| null` | Every batch | Framework name |
| `runtime` | `str \| null` | Every batch | Python version, etc. |
| `sdk_version` | `str \| null` | Every batch | SDK version constant |
| `environment` | `str` | Every batch | Deployment stage |
| `group` | `str` | Every batch | Team grouping |
| `first_seen` | `datetime` | Create only | First event ever |
| `last_seen` | `datetime` | Every batch | Most recent event |
| `last_heartbeat` | `datetime \| null` | On heartbeat events | Last heartbeat time |
| `last_event_type` | `str \| null` | Every batch | Drives status cascade |
| `last_task_id` | `str \| null` | On task events | Currently active task |
| `last_project_id` | `str \| null` | On task events | Currently active project |
| `stuck_threshold_seconds` | `int` | On registration | Inactivity threshold |
| `is_registered` | `bool` | Create | Always `true` |
| `previous_status` | `str \| null` | Every batch | Status before this update |

**Pydantic model**: `AgentRecord` (`src/shared/models.py:449`)

**Key behavior**: `last_event_type` is the field that drives the status cascade
(stuck > error > waiting_approval > processing > idle). Every ingest batch
overwrites it with the latest event's type.

---

### 2.3 Agent Hourly Aggregates: `agent_hourly.json`

Pre-aggregated hourly metrics per agent. Updated incrementally during ingest.
One row per `(tenant_id, agent_id, hour)`.

| Field | Type | Incremented By | Description |
|-------|------|---------------|-------------|
| `tenant_id` | `str` | — | Tenant key |
| `agent_id` | `str` | — | Agent key |
| `hour` | `ISO 8601` | — | Hour bucket (truncated) |
| `tasks_started` | `int` | `task_started` | Tasks begun |
| `tasks_completed` | `int` | `task_completed` | Tasks succeeded |
| `tasks_failed` | `int` | `task_failed` | Tasks errored |
| `task_duration_sum_ms` | `int` | `task_completed/failed` | Sum of durations |
| `tasks_by_type` | `dict[str,int]` | `task_started` | Breakdown by task_type |
| `actions_started` | `int` | `action_started` | Actions begun |
| `actions_completed` | `int` | `action_completed` | Actions succeeded |
| `actions_failed` | `int` | `action_failed` | Actions errored |
| `actions_by_name` | `dict[str,int]` | `action_started` | Breakdown by action name |
| `retries` | `int` | `retry_started` | Retry attempts |
| `escalations` | `int` | `escalated` | Escalation events |
| `approvals_requested` | `int` | `approval_requested` | Approval requests |
| `approvals_received` | `int` | `approval_received` | Approval responses |
| `llm_call_count` | `int` | `custom(llm_call)` | LLM API calls |
| `llm_tokens_in` | `int` | `custom(llm_call)` | Total input tokens |
| `llm_tokens_out` | `int` | `custom(llm_call)` | Total output tokens |
| `llm_cost` | `float` | `custom(llm_call)` | Total LLM cost |
| `llm_max_tokens_in` | `int` | `custom(llm_call)` | Largest single prompt |
| `models` | `dict` | `custom(llm_call)` | Per-model breakdown (calls, cost, tokens) |
| `calls_by_name` | `dict` | `custom(llm_call)` | Per-call-name breakdown |
| `issues_reported` | `int` | `custom(issue)` | Issues opened |
| `issues_resolved` | `int` | `custom(issue)` | Issues closed |
| `errors_by_category` | `dict[str,int]` | `custom(issue)` | Issue categories |
| `errors_by_type` | `dict[str,int]` | `*_failed` | Exception type counts |
| `errors_by_task_type` | `dict[str,int]` | `task_failed` | Failures by task type |
| `errors_by_action` | `dict[str,int]` | `action_failed` | Failures by action name |

**Source**: `src/backend/aggregator.py:73-209`

**Retention**: `AGGREGATE_RETENTION_DAYS` (pruned by `prune_aggregates()`).

---

### 2.4 Model Hourly Aggregates: `model_hourly.json`

Pre-aggregated hourly metrics per LLM model. One row per `(tenant_id, model, hour)`.
Only updated by `custom` events with `payload.kind = "llm_call"`.

| Field | Type | Description |
|-------|------|-------------|
| `tenant_id` | `str` | Tenant key |
| `model` | `str` | Model identifier (e.g. `claude-sonnet-4-20250514`) |
| `hour` | `ISO 8601` | Hour bucket |
| `call_count` | `int` | Number of LLM calls |
| `tokens_in` | `int` | Total input tokens |
| `tokens_out` | `int` | Total output tokens |
| `cost` | `float` | Total cost |
| `duration_sum_ms` | `int` | Sum of call durations |
| `max_tokens_in` | `int` | Largest single prompt |
| `max_tokens_in_agent` | `str` | Agent that sent it |
| `max_tokens_in_name` | `str` | Call name that sent it |
| `agents` | `dict` | Per-agent breakdown: `{agent_id: {calls, cost, tokens_in, tokens_out}}` |
| `calls_by_name` | `dict` | Per-call-name breakdown: `{name: {count, cost, tokens_in, tokens_out}}` |

**Source**: `src/backend/aggregator.py:229-272`

---

### 2.5 Project-Agent Junction: `project_agents.json`

Links agents to projects. One row per `(tenant_id, project_id, agent_id)`.
Created automatically during ingest when an event has a `project_id`.

| Field | Type | Description |
|-------|------|-------------|
| `tenant_id` | `str` | Tenant key |
| `project_id` | `str` | Project key |
| `agent_id` | `str` | Agent key |
| `added_at` | `datetime` | When the link was first created |
| `role` | `str` | Default `"member"` |

**Pydantic model**: `ProjectAgentRecord` (`src/shared/models.py:471`)

---

### 2.6 Infrastructure Tables

These are not populated by sensors but provide context:

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `tenants.json` | `tenant_id`, `name`, `plan` | Multi-tenancy, plan limits (retention) |
| `api_keys.json` | `key_id`, `tenant_id`, `key_hash` | Authentication, live/test isolation |
| `users.json` | `user_id`, `tenant_id`, `email` | Dashboard users |
| `projects.json` | `project_id`, `tenant_id`, `slug` | Project grouping |
| `alert_rules.json` | `rule_id`, `condition_type` | Alerting configuration |
| `alert_history.json` | `alert_id`, `rule_id`, `fired_at` | Alert firing log |
| `invites.json` | `invite_id`, `email`, `role` | Team invitations |

---

## 3. Sensor → Database Mapping (by group)

### Group 1 — Identity Sensors

These sensors write to `agents.json` (via `upsert_agent()`) and emit
one event to `events.json`.

| Sensor | SDK Function | Event Type | Stored In | Fields Written to `agents.json` |
|--------|-------------|------------|-----------|-------------------------------|
| SDK Init | `hiveloop.init()` | *(none — transport only)* | *(no database write)* | — |
| Agent Registration | `hb.agent()` | `agent_registered` | `events.json` + `agents.json` | `agent_id`, `agent_type`, `agent_version`, `framework`, `runtime`, `sdk_version`, `environment`, `group`, `first_seen`, `stuck_threshold_seconds` |
| Heartbeat | *(auto, background thread)* | `heartbeat` | `events.json` + `agents.json` | `last_seen`, `last_heartbeat`, `last_event_type` |

**Data flow for registration**:
```
hb.agent("my-agent", type="sales", version="1.0")
    │
    ├──► events.json: one row with event_type="agent_registered"
    │    payload: {summary, data: {type, version, framework, runtime, sdk_version, stuck_threshold}}
    │
    └──► agents.json: upsert row
         sets: agent_type, agent_version, framework, runtime, sdk_version, environment,
               group, first_seen, last_seen, stuck_threshold_seconds
```

**Data flow for heartbeat**:
```
(background thread, every 30s)
    │
    ├──► events.json: one row with event_type="heartbeat"
    │    payload: heartbeat_payload() callback result (if any)
    │    payload: queue_provider() → second event with kind="queue_snapshot" (if any)
    │
    └──► agents.json: upsert row
         sets: last_seen, last_heartbeat, last_event_type="heartbeat"
```

---

### Group 2 — State Sensors

These sensors emit `custom` events with well-known `payload.kind` values.
The backend reconstructs state views by scanning these events.

| Sensor | SDK Function | `payload.kind` | `events.json` payload.data | Reconstructed Into |
|--------|-------------|----------------|---------------------------|-------------------|
| Queue Snapshot | `agent.queue_snapshot()` | `queue_snapshot` | `{depth, oldest_age_seconds, items, processing}` | `PipelineState.queue` |
| Queue (auto) | `queue_provider` callback | `queue_snapshot` | *(same as above)* | `PipelineState.queue` |
| Todo | `agent.todo()` | `todo` | `{todo_id, action, priority, source, context, due_by}` | `PipelineState.todos` |
| Scheduled | `agent.scheduled()` | `scheduled` | `{items: [{id, name, next_run, interval, enabled, last_status}]}` | `PipelineState.scheduled` |
| Issue Report | `agent.report_issue()` | `issue` | `{severity, issue_id, category, context, action, occurrence_count}` | `PipelineState.issues` |
| Issue Resolve | `agent.resolve_issue()` | `issue` | `{action: "resolved", issue_id, ...}` | *(removes from active issues)* |
| Custom State | `heartbeat_payload` callback | *(none — raw heartbeat)* | Custom dict in heartbeat payload | *(raw event query only)* |

**Reconstruction logic** (`storage_json.py:1602-1695`, `get_pipeline()`):

```
PipelineState for agent X:
    │
    ├── queue:     latest event where kind="queue_snapshot" → take payload.data
    │
    ├── todos:     all events where kind="todo"
    │              → group by todo_id → take latest action per todo_id
    │              → filter: keep only created/failed/deferred (not completed/dismissed)
    │
    ├── scheduled: latest event where kind="scheduled" → take payload.data.items
    │
    └── issues:    all events where kind="issue"
                   → group by issue_id → take latest action per issue_id
                   → filter: keep only where action ≠ "resolved"
```

**Aggregation side-effects** (`agent_hourly.json`):

| Sensor | Fields incremented in `agent_hourly` |
|--------|--------------------------------------|
| Issue Report | `issues_reported` +1, `errors_by_category[category]` +1 |
| Issue Resolve | `issues_resolved` +1 |
| *(others)* | *(no aggregation — state sensors are point-in-time, not countable)* |

---

### Group 3 — Activity Sensors

These sensors emit structured events with specific `event_type` values.
They are the primary drivers of task summaries, metrics, timelines, and
hourly aggregates.

| Sensor | SDK Function | `event_type` | Key event fields | `agent_hourly` fields |
|--------|-------------|-------------|-----------------|----------------------|
| Task Start | `agent.task()` enter | `task_started` | `task_id`, `task_run_id`, `project_id`, `task_type`, `correlation_id` | `tasks_started` +1, `tasks_by_type[type]` +1 |
| Task Complete | `agent.task()` exit OK | `task_completed` | `status="success"`, `duration_ms`, `payload` | `tasks_completed` +1, `task_duration_sum_ms` += duration |
| Task Fail | `agent.task()` exit error | `task_failed` | `status="failure"`, `duration_ms`, `exception_type`, `exception_message` | `tasks_failed` +1, `task_duration_sum_ms` += duration, `errors_by_type[exc]` +1, `errors_by_task_type[type]` +1 |
| Action Start | `@agent.track()` enter | `action_started` | `action_id`, `parent_action_id`, `action_name` | `actions_started` +1, `actions_by_name[name]` +1 |
| Action Complete | `@agent.track()` exit OK | `action_completed` | `status="success"`, `duration_ms` | `actions_completed` +1 |
| Action Fail | `@agent.track()` exit error | `action_failed` | `status="failure"`, `duration_ms`, `exception_type` | `actions_failed` +1, `errors_by_type[exc]` +1, `errors_by_action[name]` +1 |
| LLM Call | `task.llm_call()` | `custom` | `payload.kind="llm_call"`, `payload.data={name, model, tokens_in, tokens_out, cost, duration_ms, ...}` | `llm_call_count` +1, `llm_tokens_in` +=, `llm_tokens_out` +=, `llm_cost` +=, `models[model]` +=, `calls_by_name[name]` += |
| Plan Created | `task.plan()` | `custom` | `payload.kind="plan_created"`, `payload.data={steps, revision}` | *(none)* |
| Plan Step | `task.plan_step()` | `custom` | `payload.kind="plan_step"`, `payload.data={step_index, total_steps, action, turns, tokens}` | *(none)* |
| Escalation | `task.escalate()` | `escalated` | `assigned_to`, `reason`, `parent_event_id` | `escalations` +1 |
| Approval Req | `task.request_approval()` | `approval_requested` | `approver`, `parent_event_id` | `approvals_requested` +1 |
| Approval Rcv | `task.approval_received()` | `approval_received` | `approved_by`, `decision`, `parent_event_id` | `approvals_received` +1 |
| Retry | `task.retry()` | `retry_started` | `attempt`, `backoff_seconds`, `parent_event_id` | `retries` +1 |
| Custom Event | `task.event()` | `custom` | Arbitrary `payload` | *(none — unless kind matches)* |
| Log Bridge | `HiveBoardLogHandler` | `custom` | `payload.kind="issue"` | *(same as Issue Report)* |

**LLM Call also writes to `model_hourly.json`**:

| Field | Increment |
|-------|-----------|
| `call_count` | +1 |
| `tokens_in` | += `tokens_in` |
| `tokens_out` | += `tokens_out` |
| `cost` | += `cost` |
| `duration_sum_ms` | += `duration_ms` |
| `agents[agent_id]` | per-agent sub-totals |
| `calls_by_name[name]` | per-call-name sub-totals |

---

## 4. Derived Views (computed on read)

These are never stored — they are computed from the tables above when the
API is queried.

### 4.1 Agent Summary (`GET /v1/agents`)

**Computed from**: `agents.json` + `events.json`

| Response Field | Source |
|---------------|--------|
| `agent_id`, `agent_type`, `framework`, etc. | `agents.json` direct fields |
| `derived_status` | `derive_agent_status()` cascade on `agents.json` row |
| `current_task_id` | `agents.json.last_task_id` |
| `heartbeat_age_seconds` | `now - agents.json.last_heartbeat` |
| `is_stuck` | `derived_status == "stuck"` |
| `stats_1h` | `compute_agent_stats_1h()` → scans `events.json` for 1-hour window |

**Status cascade** (`storage_json.py:81-124`):
```
1. stuck:            (now - last_heartbeat) > stuck_threshold_seconds
2. error:            last_event_type in (task_failed, action_failed)
3. waiting_approval: last_event_type == approval_requested
4. processing:       last_event_type in (task_started, action_started)
5. idle:             everything else
```

**Pydantic model**: `AgentSummary` (`src/shared/models.py:570`)

---

### 4.2 Task Summary (`GET /v1/tasks`)

**Computed from**: `events.json` (grouped by `task_id`)

| Response Field | Computation |
|---------------|------------|
| `task_id`, `task_type`, `agent_id`, `project_id` | From first event in task group |
| `derived_status` | `_derive_task_status()` from event type set |
| `started_at` | Timestamp of first event |
| `completed_at` | Timestamp of `task_completed` / `task_failed` |
| `duration_ms` | From terminal event's `duration_ms` field |
| `total_cost` | Sum of `payload.data.cost` from `llm_call` events |
| `action_count` | Count of `action_started` events |
| `error_count` | Count of `action_failed` + `task_failed` events |
| `has_escalation` | `escalated` in event types |
| `has_human_intervention` | `approval_requested` or `approval_received` in event types |
| `llm_call_count` | Count of events with `payload.kind = "llm_call"` |
| `total_tokens_in/out` | Sum from `llm_call` payloads |

**Task status cascade** (`storage_json.py:127-147`):
```
1. completed:   task_completed in event types
2. failed:      task_failed in event types
3. escalated:   escalated in event types
4. waiting:     approval_requested without approval_received
5. processing:  everything else
```

**Pydantic model**: `TaskSummary` (`src/shared/models.py:594`)

---

### 4.3 Task Timeline (`GET /v1/tasks/{id}/timeline`)

**Computed from**: `events.json` (all events for one task_id)

Reconstructs four structures from raw events:

| Structure | Built From | Logic |
|-----------|-----------|-------|
| `events` | All events for task | Raw event array, chronological |
| `action_tree` | `action_started/completed/failed` events | Build dict by `action_id`, nest via `parent_action_id` |
| `error_chains` | `retry_started`, `escalated` events | Link via `parent_event_id` to causal predecessors |
| `plan` | `plan_created` + `plan_step` events | Extract steps from `plan_created`, overlay status from `plan_step` events |

**Pydantic model**: `TimelineSummary` (`src/shared/models.py:615`)

---

### 4.4 Pipeline State (`GET /v1/agents/{id}/pipeline`)

**Computed from**: `events.json` (custom events with well-known kinds)

See Section 3, Group 2 reconstruction logic above.

**Pydantic model**: `PipelineState` (`src/shared/models.py:703`)

**Fleet view** (`GET /v1/pipeline`): Aggregates per-agent pipelines into
`FleetPipelineState` with totals for queue_depth, active_issues, active_todos,
scheduled_count.

---

### 4.5 Metrics (`GET /v1/metrics`)

**Computed from**: `events.json` (time-windowed)

| Response Field | Computation |
|---------------|-------------|
| `summary.total_tasks` | Distinct `task_id` values in window |
| `summary.completed/failed/escalated` | Derived task status counts |
| `summary.success_rate` | `completed / total_tasks * 100` |
| `summary.avg_duration_ms` | Mean of `duration_ms` from terminal events |
| `summary.total_cost` | Sum of `llm_call` costs |
| `summary.stuck` | Count of agents with `derive_agent_status() == stuck` |
| `timeseries[].timestamp` | Bucket start time |
| `timeseries[].tasks_completed/failed` | Counts within bucket interval |
| `timeseries[].cost` | LLM cost within bucket |
| `groups[]` | Optional group_by `agent` or `model` |

**Pydantic model**: `MetricsResponse` (`src/shared/models.py:656`)

---

### 4.6 Cost Analysis (`GET /v1/cost`)

**Computed from**: `events.json` (events with `payload.kind = "llm_call"`)

| Response Field | Computation |
|---------------|-------------|
| `total_cost` | Sum of `payload.data.cost` |
| `call_count` | Count of LLM call events |
| `total_tokens_in/out` | Sum of token fields |
| `by_agent[]` | Grouped by `agent_id`: cost, calls, tokens |
| `by_model[]` | Grouped by `payload.data.model`: cost, calls, tokens |
| `reported_cost` | Sum where `cost_source = "reported"` |
| `estimated_cost` | Sum where `cost_source = "estimated"` |

**Cost enrichment during ingest** (`app.py:488-490`): When an LLM call event
arrives without a `cost` field, the server estimates cost using a pricing
engine and marks it `cost_source = "estimated"`.

**Pydantic model**: `CostSummary` (`src/shared/models.py:664`)

---

### 4.7 Insights Engine (`GET /v1/insights/*`)

**Computed from**: `agent_hourly.json` + `model_hourly.json` (pre-aggregated)

| Endpoint | Source Table | Response Model |
|----------|-------------|----------------|
| `/v1/insights/agents` | `agent_hourly` | `InsightsAgentsResponse` — per-agent detail + fleet totals + comparisons |
| `/v1/insights/models` | `model_hourly` | `InsightsModelsResponse` — per-model detail + fleet totals |
| `/v1/insights/timeseries` | `agent_hourly` | `InsightsTimeseriesResponse` — metric over time (cost, tasks, errors, llm_calls) |
| `/v1/insights/errors` | `agent_hourly` | `InsightsErrorsResponse` — error breakdown by type, category, task, action |
| `/v1/insights/prompts` | `model_hourly` | `InsightsPromptsResponse` — per-call-name token/cost analysis |
| `/v1/insights/actions` | `agent_hourly` | `InsightsActionsResponse` — per-action success rates and throughput |

These endpoints read from the hourly aggregate tables (not raw events),
making them fast even with large event volumes.

---

## 5. Complete Sensor → Storage → View Tracing

For each sensor, this traces exactly which tables are written and which
derived views consume the data.

| # | Sensor | Event Type | `events` | `agents` | `agent_hourly` | `model_hourly` | `project_agents` | Consumed By Views |
|---|--------|-----------|----------|---------|---------------|---------------|-----------------|-------------------|
| 1 | Registration | `agent_registered` | ✓ | ✓ (create) | — | — | — | Agent Summary |
| 2 | Heartbeat | `heartbeat` | ✓ | ✓ (`last_heartbeat`) | — | — | — | Agent Summary (stuck detection) |
| 3 | Heartbeat Payload | `heartbeat` | ✓ (in payload) | ✓ | — | — | — | Raw event query |
| 4 | Queue Snapshot | `custom` | ✓ | ✓ | — | — | — | Pipeline State, Agent Stats (queue_depth) |
| 5 | Todo | `custom` | ✓ | ✓ | — | — | — | Pipeline State (todos) |
| 6 | Scheduled | `custom` | ✓ | ✓ | — | — | — | Pipeline State (scheduled) |
| 7 | Issue Report | `custom` | ✓ | ✓ | ✓ | — | — | Pipeline State (issues), Agent Stats (active_issues), Insights Errors |
| 8 | Issue Resolve | `custom` | ✓ | ✓ | ✓ | — | — | Pipeline State (removes issue) |
| 9 | Task Start | `task_started` | ✓ | ✓ (`last_task_id`) | ✓ | — | ✓ | Task Summary, Metrics, Timeline, Agent Summary (status=processing) |
| 10 | Task Complete | `task_completed` | ✓ | ✓ | ✓ | — | — | Task Summary, Metrics, Timeline, Agent Stats |
| 11 | Task Fail | `task_failed` | ✓ | ✓ | ✓ | — | — | Task Summary, Metrics, Timeline, Agent Summary (status=error), Insights Errors |
| 12 | Action Start | `action_started` | ✓ | ✓ | ✓ | — | — | Timeline (action_tree), Agent Summary (status=processing), Insights Actions |
| 13 | Action Complete | `action_completed` | ✓ | ✓ | ✓ | — | — | Timeline (action_tree), Insights Actions |
| 14 | Action Fail | `action_failed` | ✓ | ✓ | ✓ | — | — | Timeline (action_tree, error_chains), Agent Summary (status=error), Insights Errors |
| 15 | LLM Call | `custom` | ✓ | ✓ | ✓ | ✓ | — | Cost Analysis, Task Summary (cost/tokens), Metrics (cost), Insights Models/Prompts |
| 16 | Plan Created | `custom` | ✓ | ✓ | — | — | — | Timeline (plan overlay) |
| 17 | Plan Step | `custom` | ✓ | ✓ | — | — | — | Timeline (plan overlay) |
| 18 | Escalation | `escalated` | ✓ | ✓ | ✓ | — | — | Task Summary, Timeline (error_chains), Metrics |
| 19 | Approval Req | `approval_requested` | ✓ | ✓ | ✓ | — | — | Task Summary, Agent Summary (status=waiting_approval) |
| 20 | Approval Rcv | `approval_received` | ✓ | ✓ | ✓ | — | — | Task Summary |
| 21 | Retry | `retry_started` | ✓ | ✓ | ✓ | — | — | Timeline (error_chains) |
| 22 | Custom Event | `custom` | ✓ | ✓ | — | — | — | Raw event query, Timeline |
| 23 | Log Bridge | `custom` | ✓ | ✓ | ✓ | — | — | Pipeline State (issues), Insights Errors |

---

## 6. Payload Structure by Kind

The `payload` field in `events.json` follows a universal envelope:

```json
{
    "kind": "<well-known-kind or null>",
    "summary": "<human-readable summary, max 512 chars>",
    "data": { ... },
    "tags": ["tag1", "tag2"]
}
```

### 6.1 `kind = "llm_call"`

```json
{
    "kind": "llm_call",
    "summary": "reason → claude-sonnet-4-20250514 (1200 in / 350 out, $0.008)",
    "data": {
        "name": "reason",
        "model": "claude-sonnet-4-20250514",
        "tokens_in": 1200,
        "tokens_out": 350,
        "cost": 0.008,
        "cost_source": "reported",
        "duration_ms": 1450,
        "prompt_preview": "You are a sales agent...",
        "response_preview": "Based on the lead data...",
        "metadata": {"temperature": 0.7}
    },
    "tags": ["llm"]
}
```

### 6.2 `kind = "queue_snapshot"`

```json
{
    "kind": "queue_snapshot",
    "summary": "Queue depth: 5",
    "data": {
        "depth": 5,
        "oldest_age_seconds": 120,
        "items": [
            {"id": "msg-1", "priority": "high", "source": "user",
             "summary": "Process invoice", "queued_at": "2025-01-01T12:00:00Z"}
        ],
        "processing": {
            "id": "msg-0", "summary": "Analyzing doc",
            "started_at": "2025-01-01T12:01:00Z", "elapsed_ms": 4500
        }
    },
    "tags": ["queue"]
}
```

### 6.3 `kind = "todo"`

```json
{
    "kind": "todo",
    "summary": "Search database",
    "data": {
        "todo_id": "search-db-1",
        "action": "created",
        "priority": "high",
        "source": "planner",
        "context": "Need customer data for lead scoring",
        "due_by": "2025-01-01T13:00:00Z"
    },
    "tags": ["todo", "created"]
}
```

### 6.4 `kind = "scheduled"`

```json
{
    "kind": "scheduled",
    "summary": "3 scheduled items",
    "data": {
        "items": [
            {"id": "poll-crm", "name": "Poll CRM", "next_run": "2025-01-01T13:00:00Z",
             "interval": "1h", "enabled": true, "last_status": "success"}
        ]
    },
    "tags": ["scheduled"]
}
```

### 6.5 `kind = "plan_created"`

```json
{
    "kind": "plan_created",
    "summary": "Process lead: 3 steps",
    "data": {
        "steps": [
            {"index": 0, "description": "Score lead"},
            {"index": 1, "description": "Enrich from CRM"},
            {"index": 2, "description": "Route to rep"}
        ],
        "revision": 0
    },
    "tags": ["plan", "created"]
}
```

### 6.6 `kind = "plan_step"`

```json
{
    "kind": "plan_step",
    "summary": "Step 1/3 completed: Scored lead 42",
    "data": {
        "step_index": 0,
        "total_steps": 3,
        "action": "completed",
        "turns": 2,
        "tokens": 3200,
        "plan_revision": 0
    },
    "tags": ["plan", "step_completed"]
}
```

### 6.7 `kind = "issue"`

```json
{
    "kind": "issue",
    "summary": "CRM API returning 503",
    "data": {
        "severity": "high",
        "issue_id": "crm-503",
        "category": "connectivity",
        "context": {"endpoint": "https://crm.example.com/api", "status": 503},
        "action": "reported",
        "occurrence_count": 3
    },
    "tags": ["issue", "connectivity"]
}
```

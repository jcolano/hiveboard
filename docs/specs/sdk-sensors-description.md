# Observability Model — What HiveLoop Captures

A comprehensive inventory of every piece of observable information that the HiveLoop SDK can capture from your agents. These are organized into three groups:

- **Group 1 — Identity**: Static/descriptive properties about what the agent *is*
- **Group 2 — State**: Mutable data about what the agent *has* at a point in time
- **Group 3 — Activity**: Events and traces about what the agent *does*

---

## Group 1 — Identity (What the agent *is*)

These are the static, descriptive properties that define an agent's identity, configuration, and deployment context. They are set at creation time and rarely change.

### 1.1 Core Identity

| Property | Type | Default | Source |
|----------|------|---------|--------|
| `agent_id` | `str` (max 256) | *(required)* | User-provided unique identifier |
| `agent_type` | `str` | `"general"` | Classification/category of the agent |
| `version` | `str \| None` | `None` | Implementation version string |
| `framework` | `str` | `"custom"` | Framework the agent is built with |
### 1.2 Deployment Context

| Property | Type | Default | Source |
|----------|------|---------|--------|
| `environment` | `str` (max 64) | `"production"` | Deployment environment (dev/staging/prod) |
| `group` | `str` (max 128) | `"default"` | Team/service grouping |
| `runtime` | `str` | Auto-detected | e.g. `"python-3.11.0"` |
| `sdk_version` | `str` | `"hiveloop-0.1.0"` | SDK version constant |
### 1.3 Behavioral Configuration

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `heartbeat_interval` | `float` | `30.0` | Seconds between heartbeat emissions |
| `stuck_threshold` | `int` | `300` | Seconds of inactivity before marked stuck |
| `heartbeat_payload` | `Callable` | `None` | Optional callback for custom heartbeat data |
| `queue_provider` | `Callable` | `None` | Optional callback for queue state reporting |
### 1.4 Transport Configuration (HiveBoard client)

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `api_key` | `str` | *(required)* | API authentication |
| `endpoint` | `str` | Auto-resolved | Backend URL |
| `flush_interval` | `float` | `5.0` | Seconds between event flushes |
| `batch_size` | `int` | `100` | Max events per HTTP batch |
| `max_queue_size` | `int` | `10,000` | Max queued events before dropping |
| `debug` | `bool` | `False` | Enable debug logging |
### 1.5 Persistent Identity (AgentRecord)

When the backend persists agent data, it adds temporal identity fields:

| Field | Type | Description |
|-------|------|-------------|
| `tenant_id` | `str` | Owning tenant |
| `first_seen` | `datetime` | First event ever received |
| `last_seen` | `datetime` | Most recent event received |
| `is_registered` | `bool` | Registration flag |
---

## Group 2 — State (What the agent *has*)

These represent mutable, point-in-time snapshots of the agent's internal condition. They change during execution and can be queried or observed.

### 2.1 Derived Status

The backend computes a real-time status from the event stream:

| Status | Condition |
|--------|-----------|
| `stuck` | Activity age > `stuck_threshold_seconds` |
| `error` | Last event is `task_failed` or `action_failed` |
| `waiting_approval` | Last event is `approval_requested` |
| `processing` | Last event is `task_started` or `action_started` |
| `idle` | Default (no active work) |

**Priority cascade**: stuck > error > waiting_approval > processing > idle
### 2.2 Agent Summary (API response: GET /v1/agents)

| Field | Type | Description |
|-------|------|-------------|
| `derived_status` | `str` | Computed status (see above) |
| `current_task_id` | `str \| None` | Active task |
| `current_project_id` | `str \| None` | Active project |
| `last_heartbeat` | `ISO 8601` | When last heartbeat received |
| `heartbeat_age_seconds` | `int \| None` | Seconds since last heartbeat |
| `is_stuck` | `bool` | Whether agent is stuck |
| `stats_1h` | `AgentStats1h` | Rolling 1-hour metrics |
### 2.3 Rolling Statistics (1-hour window)

| Field | Type | Description |
|-------|------|-------------|
| `tasks_completed` | `int` | Tasks completed in window |
| `tasks_failed` | `int` | Tasks failed in window |
| `success_rate` | `float \| None` | Success percentage |
| `avg_duration_ms` | `int \| None` | Average task duration |
| `total_cost` | `float \| None` | Total LLM cost |
| `throughput` | `int` | Tasks per hour |
| `queue_depth` | `int` | Current queue size |
| `active_issues` | `int` | Unresolved issues |
### 2.4 Queue State (via queue_provider callback)

When a `queue_provider` callback is registered, the agent emits periodic queue snapshots:

| Field | Type | Description |
|-------|------|-------------|
| `depth` | `int` | Number of items in queue |
| `oldest_age_seconds` | `int \| None` | Age of oldest item |
| `items` | `list` | Queue items (id, priority, source, summary, queued_at) |
| `processing` | `dict \| None` | Currently processing item (id, summary, started_at, elapsed_ms) |
### 2.5 Todo/Pipeline State

Active todo items, reconstructed from the event stream:

| Field | Type | Description |
|-------|------|-------------|
| `todo_id` | `str` | Stable identifier |
| `action` | `str` | Latest: created, completed, failed, dismissed, deferred |
| `priority` | `str \| None` | high, normal, low |
| `source` | `str \| None` | Origin of the todo |
| `context` | `str \| None` | Additional context |
| `due_by` | `ISO 8601 \| None` | Deadline |

The backend reconstructs a `PipelineState` by grouping todos by `todo_id`, taking the latest action, and filtering active states.
### 2.6 Plan State

When an agent creates a plan, its state is tracked:

| Field | Type | Description |
|-------|------|-------------|
| `goal` | `str` | What the plan aims to achieve |
| `steps` | `list[{index, description}]` | Ordered step descriptions |
| `revision` | `int \| None` | 0 = initial, increments on replan |
| `_plan_total_steps` | `int` | Stored on task for step tracking |
| `_plan_revision` | `int` | Current revision stored on task |
### 2.7 Scheduled Work State

| Field | Type | Description |
|-------|------|-------------|
| `items` | `list` | Each: id, name, next_run, interval, enabled, last_status |
### 2.8 Active Issues State

| Field | Type | Description |
|-------|------|-------------|
| `severity` | `str` | critical, high, medium, low |
| `issue_id` | `str \| None` | Stable ID (server deduplicates by hash if absent) |
| `category` | `str \| None` | permissions, connectivity, configuration, data_quality, rate_limit, other |
| `action` | `str` | reported, resolved, dismissed |
| `occurrence_count` | `int \| None` | How many times observed |
---

## Group 3 — Activity (What the agent *does*)

These capture the dynamic behavior of the agent — its execution lifecycle, events emitted, and traces produced.

### 3.1 The Agentic Loop Lifecycle

The complete execution lifecycle follows this sequence:

```
hiveloop.init()
  └─ HiveBoard singleton created
      └─ hb.agent(id)
          ├─ Agent.__init__()
          ├─ _register() → emit AGENT_REGISTERED
          └─ _start_heartbeat() → background daemon thread
              ├─ emit HEARTBEAT (every N seconds)
              ├─ heartbeat_payload() callback
              └─ queue_provider() callback → emit queue_snapshot

          with agent.task(id):
              ├─ emit TASK_STARTED
              │
              ├─ @agent.track("action_name")
              │   ├─ emit ACTION_STARTED
              │   ├─ [user code executes]
              │   └─ emit ACTION_COMPLETED or ACTION_FAILED
              │
              ├─ task.llm_call(...)      → emit CUSTOM (kind=llm_call)
              ├─ task.plan(...)          → emit CUSTOM (kind=plan_created)
              ├─ task.plan_step(...)     → emit CUSTOM (kind=plan_step)
              ├─ task.retry(...)         → emit RETRY_STARTED
              ├─ task.escalate(...)      → emit ESCALATED
              ├─ task.request_approval() → emit APPROVAL_REQUESTED
              ├─ task.approval_received()→ emit APPROVAL_RECEIVED
              ├─ agent.todo(...)         → emit CUSTOM (kind=todo)
              ├─ agent.report_issue(...) → emit CUSTOM (kind=issue)
              ├─ task.event(...)         → emit CUSTOM (arbitrary)
              │
              └─ [context exit]
                  ├─ emit TASK_COMPLETED (success)
                  └─ emit TASK_FAILED (exception)

hiveloop.shutdown()
  └─ _stop_heartbeat() → transport.shutdown() → final flush
```

### 3.2 Event Types (13 total)

#### Layer 0 — Agent Lifecycle

| Event Type | Trigger | Key Data | Severity |
|------------|---------|----------|----------|
| `agent_registered` | `hb.agent()` | type, version, framework, stuck_threshold | INFO |
| `heartbeat` | Background thread (every N sec) | Optional custom payload | DEBUG |

#### Layer 1 — Structured Execution

| Event Type | Trigger | Key Data | Severity |
|------------|---------|----------|----------|
| `task_started` | `agent.task()` enter | task_id, project_id, task_type, correlation_id | INFO |
| `task_completed` | `agent.task()` exit (success) | status, duration_ms, payload | INFO |
| `task_failed` | `agent.task()` exit (exception) | status, duration_ms, exception_type, exception_message | ERROR |
| `action_started` | `@agent.track()` / `track_context()` enter | action_id, parent_action_id, action_name, function | INFO |
| `action_completed` | Exit (success) | status, duration_ms | INFO |
| `action_failed` | Exit (exception) | status, duration_ms, exception_type, exception_message | ERROR |

#### Layer 2 — Narrative Telemetry

| Event Type | Trigger | Key Data | Severity |
|------------|---------|----------|----------|
| `retry_started` | `task.retry()` | attempt, backoff_seconds | WARN |
| `escalated` | `task.escalate()` | assigned_to, reason | WARN |
| `approval_requested` | `task.request_approval()` | approver | INFO |
| `approval_received` | `task.approval_received()` | approved_by, decision | INFO |
| `custom` | `agent.event()` / `task.event()` / convenience methods | Arbitrary payload with optional `kind` | INFO |

### 3.3 Well-Known Payload Kinds (7 types, emitted as `custom` events)

| Kind | Method | Key Fields | Tags |
|------|--------|------------|------|
| `llm_call` | `task.llm_call()` / `agent.llm_call()` | name, model, tokens_in/out, cost, duration_ms, prompt/response_preview | `["llm"]` |
| `queue_snapshot` | `agent.queue_snapshot()` / heartbeat callback | depth, oldest_age_seconds, items, processing | `["queue"]` |
| `todo` | `agent.todo()` | todo_id, action, priority, source, context, due_by | `["todo", action]` |
| `scheduled` | `agent.scheduled()` | items (id, name, next_run, interval, enabled, last_status) | `["scheduled"]` |
| `plan_created` | `task.plan()` | goal, steps, revision | `["plan", "created"]` |
| `plan_step` | `task.plan_step()` | step_index, total_steps, action, turns, tokens, plan_revision | `["plan", "step_*"]` |
| `issue` | `agent.report_issue()` | severity, issue_id, category, context, action, occurrence_count | `["issue"]` |

### 3.4 Tracing & Observability

#### Event Envelope (every event)

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | UUID4 | Client-generated unique ID |
| `timestamp` | ISO 8601 | UTC timestamp |
| `event_type` | `str` | One of 13 types |
| `severity` | `str` | debug / info / warn / error |
| `status` | `str \| None` | success / failure (terminal events) |
| `duration_ms` | `int \| None` | Wall-clock elapsed time |
| `parent_event_id` | `UUID \| None` | Causal predecessor link |
| `payload` | `dict` | Max 32KB, free-form or well-known kind |
#### Causality Chains

Two linking mechanisms:

1. **Parent-child (causal)**: `parent_event_id` links an event to its cause (e.g., retry → original failure)
2. **Action nesting (hierarchy)**: `action_id` + `parent_action_id` creates 3+ level deep action trees

#### Task Context (inherited by all events within a task)

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | User-provided task identifier |
| `task_run_id` | `UUID` | Generated per-execution run |
| `project_id` | `str \| None` | Project scope |
| `task_type` | `str \| None` | Classification |
| `correlation_id` | `str \| None` | Cross-agent/system correlation |
### 3.5 Action Tracking (Decorator & Context Manager)

Two patterns for wrapping code execution:

```python
# Decorator — wraps entire function
@agent.track("search_database")
async def search(query):
    ...

# Context manager — wraps arbitrary block
with agent.track_context("call_api") as ctx:
    result = api.call(...)
    ctx.set_payload({"result_count": len(result)})
```

Both emit `ACTION_STARTED` on entry and `ACTION_COMPLETED`/`ACTION_FAILED` on exit.

Supports nesting: inner actions get `parent_action_id` pointing to outer action.
### 3.6 Tool Call Tracking Helper

Standardized payload builder for tool execution events:

```python
tool_payload(
    args={"query": q},
    result=data,
    success=True,
    duration_ms=elapsed,
    tool_category="crm",
    http_status=200,
    result_size_bytes=len(data)
)
```

Auto-truncates args (500 chars) and result (1000 chars).
### 3.7 Logging Bridge

`HiveBoardLogHandler` extends Python's `logging.Handler` to forward WARNING+ log records as agent issues:

| Log Level | Issue Severity |
|-----------|---------------|
| WARNING | medium |
| ERROR | high |
| CRITICAL | critical |

Includes: filename, line number, function name in issue context.
### 3.8 Transport & Delivery

| Aspect | Detail |
|--------|--------|
| Queue | Thread-safe bounded deque (10k default) |
| Batching | Up to 100 events per HTTP POST (max 500) |
| Flush interval | 5 seconds (configurable) |
| Overflow | Oldest events dropped with warning |
| Retry | Exponential backoff: 1→2→4→8→16→60s (max 5 retries) |
| Rate limits | Ingest: 100 req/s, Query: 30 req/s, WebSocket: 5 conn/key |
| Safety | Transport never raises to caller — events dropped silently on failure |
| Shutdown | Synchronous drain of remaining events |
### 3.9 Timeline Summary (reconstructed output)

After execution, the backend can reconstruct a complete timeline:

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | `str` | Task identifier |
| `task_run_id` | `str \| None` | Run identifier |
| `derived_status` | `str` | processing / completed / failed / escalated / waiting |
| `started_at` | `ISO 8601` | When task started |
| `completed_at` | `ISO 8601 \| None` | When task ended |
| `duration_ms` | `int \| None` | Total elapsed time |
| `total_cost` | `float \| None` | Cumulative LLM cost |
| `events` | `list[dict]` | Full event array |
| `action_tree` | `list[dict]` | Reconstructed action hierarchy |
| `error_chains` | `list[dict]` | Linked error events |
| `plan` | `dict \| None` | Reconstructed plan if present |
---

## Summary: Sensor Count by Group

| Group | Category | Sensor Count |
|-------|----------|-------------|
| **1. Identity** | Core identity | 4 |
| | Deployment context | 4 |
| | Behavioral config | 4 |
| | Transport config | 6 |
| | Persistent identity | 4 |
| | **Subtotal** | **22** |
| **2. State** | Derived status | 5 states |
| | Agent summary | 7 |
| | Rolling stats (1h) | 8 |
| | Queue state | 4 |
| | Todo/pipeline | 6 |
| | Plan state | 5 |
| | Scheduled work | 1 (list) |
| | Active issues | 6 |
| | **Subtotal** | **42** |
| **3. Activity** | Event types | 13 |
| | Payload kinds | 7 |
| | Trace fields per event | 8 |
| | Task context fields | 5 |
| | Action tracking | 2 patterns |
| | Tool tracking | 7 fields |
| | Logging bridge | 3 mappings |
| | Timeline summary | 10 |
| | **Subtotal** | **55+** |

**Total observable sensors: 119+**

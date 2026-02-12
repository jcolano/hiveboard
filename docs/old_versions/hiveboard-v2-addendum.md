# HiveBoard + HiveLoop — v2 Addendum

**CONFIDENTIAL** | February 2026

---

## Purpose

This document captures every addition and revision to the HiveBoard/HiveLoop specifications derived from cross-referencing the specs against a production observability implementation (loopCore). It serves as the **change manifest** — the single source of truth for what's changing and why.

Each spec (Event Schema, Data Model, SDK, API, Dashboard) will be updated individually from this document. If context compacts between updates, this addendum survives as the reference.

### What Triggered These Changes

Two findings from the gap analysis:

1. **LLM call content and cost tracking is essential, not optional.** Production experience showed that seeing prompt content, token breakdowns, and per-model costs enabled an 80% cost reduction ($40/hr → $8/hr). This must be first-class in HiveLoop.

2. **The agent's work pipeline is observability, not control.** Queue state, pending TODOs, and scheduled work tell you what the agent *will do*, what it *tried and couldn't*, and what it's *holding for later*. Without this, silent failures are invisible — the email that never sent, the task that got dropped, the request stuck in a queue.

### Design Constraints

All additions follow the existing progressive instrumentation model:

- **Layer 0** (Init + Heartbeat): No changes. Already works.
- **Layer 1** (Decorators + Task Context): `task.llm_call()` added here.
- **Layer 2** (Manual Events): Work Pipeline methods added here.
- **Agent-Level** (new sub-layer): `agent.queue_snapshot()`, `agent.todo()`, `agent.scheduled()` — not tied to a task context.

Everything new is **optional**. A developer who only uses Layer 0 gets the same value as before. A developer who adds Layer 2 + Agent-Level gets rich work pipeline visibility. The schema shape doesn't change — only which fields are populated.

---

## Change 1: LLM Call Tracking

**Priority:** #1
**Affects:** Event Schema, SDK, API, Dashboard
**Rationale:** The single most impactful observability feature for cost control. Without it, developers can't see which prompts are bloated, which models are overused, or which workflows make too many LLM calls.

### 1.1 Event Schema Changes

**No new event types.** LLM calls use `event_type: "custom"` with a well-known payload shape.

Add to Section 6 (Payload Conventions) a new **recommended payload kind**: `"llm_call"`.

```
payload.kind = "llm_call"
```

**Standardized payload structure for `kind: "llm_call"`:**

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | Always `"llm_call"` |
| `summary` | string | Yes | Human-readable label. SDK auto-generates: `"{name}: {model} ({tokens_in}→{tokens_out})"` |
| `data.name` | string | Yes | Logical call identifier (e.g., `"phase1_reasoning"`, `"generate_email"`) |
| `data.model` | string | Yes | Model identifier (e.g., `"claude-sonnet-4-20250514"`, `"gpt-4o"`) |
| `data.tokens_in` | integer | No | Input/prompt tokens |
| `data.tokens_out` | integer | No | Output/completion tokens |
| `data.cost` | float | No | Cost in USD. Developer-calculated. |
| `data.duration_ms` | integer | No | LLM call latency |
| `data.prompt_preview` | string | No | First N characters of the prompt (developer controls length) |
| `data.response_preview` | string | No | First N characters of the response |
| `data.metadata` | object | No | Arbitrary additional context (caller ID, phase, intent, etc.) |
| `tags` | string[] | No | Filtering tags (e.g., `["llm", "phase1", "sonnet"]`) |

**Why not a new event type?** The event taxonomy is intentionally small. LLM calls are a *kind* of custom event, not a new lifecycle category. The schema stays clean while the dashboard learns to render this kind specially.

### 1.2 SDK Changes

Add `task.llm_call()` as a **convenience method** (Section 12.3 of the SDK spec).

```python
task.llm_call(
    name="phase1_reasoning",          # required — logical identifier
    model="claude-sonnet-4-20250514",  # required — model string
    tokens_in=1500,                   # optional
    tokens_out=200,                   # optional
    cost=0.003,                       # optional — USD
    duration_ms=850,                  # optional — LLM latency
    prompt_preview=prompt[:500],      # optional — first N chars
    response_preview=response[:500],  # optional — first N chars
    metadata={"caller": "atomic_phase1_turn_3", "intent": "search CRM"}  # optional
)
```

**SDK behavior:**
- Builds a `custom` event with `payload.kind = "llm_call"` and the standardized structure above.
- Auto-generates `payload.summary` from name, model, and token counts.
- Enqueues into the shared event queue like any other event.
- Requires active task context. For agent-level LLM calls (not tied to a task), use `agent.llm_call()` with the same signature.

Also add `agent.llm_call()` with identical signature but no task context requirement (`task_id: null`).

### 1.3 API Changes

**Query endpoint enhancement.** The existing `GET /v1/metrics` endpoint needs a new `group_by` option:

```
GET /v1/metrics?metric=cost&group_by=model&time_range=24h
GET /v1/metrics?metric=tokens&group_by=agent_id&time_range=7d
```

Add a new dedicated endpoint for LLM call queries:

```
GET /v1/llm-calls?agent_id=X&time_range=24h&model=claude-sonnet-4-20250514&limit=50
```

Returns events where `payload.kind = "llm_call"`, ordered newest-first. Response includes per-call detail and aggregated totals.

### 1.4 Dashboard Changes

**New: Cost Explorer view.** Either a standalone screen (Screen 5) or a tab within Agent Detail. Contains:

1. **Summary ribbon** — Total calls, total tokens (in/out), total cost for selected time range.
2. **Per-agent breakdown table** — Agent name, calls, input tokens, output tokens, input cost, output cost, total cost. Sorted by total cost descending.
3. **Per-model breakdown table** — Model name, calls, input tokens, output tokens, total cost.
4. **Recent LLM calls table** — Timestamp, agent, model, call name, tokens in, tokens out, cost. Click to expand prompt/response preview.
5. **Time range selector** — Today, 7d, 30d, custom range.

**Timeline rendering for LLM calls.** When the Task Timeline encounters a `custom` event with `kind: "llm_call"`, render it with a distinct visual treatment:
- Model badge (e.g., "sonnet" in blue pill)
- Token counts: `1.5K → 200`
- Cost: `$0.003`
- Click to expand: prompt preview, response preview, full metadata

---

## Change 2: Work Pipeline Observability

**Priority:** #2
**Affects:** Event Schema, SDK, API, Dashboard
**Rationale:** Without visibility into pending work (queue), planned work (TODOs), and future work (scheduled items), silent failures are invisible. The agent looks idle on the dashboard while 4 items rot in its queue.

### 2.1 Design Decision: Agent-Level Methods

Work pipeline data is **agent-level, not task-level**. A queue exists whether or not a task is running. TODOs persist across tasks. Scheduled items fire independently of current work. Therefore:

- `agent.queue_snapshot()` — agent-level method, no task context needed
- `agent.todo()` — agent-level method
- `agent.scheduled()` — agent-level method

These emit events with `task_id: null` and well-known payload kinds.

### 2.2 Event Schema Changes

**No new event types.** All work pipeline data uses `event_type: "custom"` with well-known payload kinds.

Three new **recommended payload kinds**:

#### `kind: "queue_snapshot"`

Periodic snapshot of the agent's work queue. Emitted on a schedule (e.g., every heartbeat) or on significant queue changes.

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | `"queue_snapshot"` |
| `summary` | string | Yes | Auto-generated: `"Queue: {depth} items, oldest {age}s"` |
| `data.depth` | integer | Yes | Number of items currently in the queue |
| `data.oldest_age_seconds` | integer | No | Age of the oldest queued item in seconds |
| `data.items` | array | No | Summary of queued items (see below) |
| `data.processing` | object | No | Currently processing item (if any) |
| `tags` | string[] | No | `["queue"]` |

**`data.items` array element:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Item identifier |
| `priority` | string | `"high"`, `"normal"`, `"low"` |
| `source` | string | Where it came from: `"human"`, `"webhook"`, `"heartbeat"`, `"scheduled"` |
| `summary` | string | Brief description of the work item |
| `queued_at` | ISO 8601 | When it entered the queue |

**`data.processing` object:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Item identifier |
| `summary` | string | What's being processed |
| `started_at` | ISO 8601 | When processing began |
| `elapsed_ms` | integer | How long it's been running |

#### `kind: "todo"`

Individual TODO item lifecycle event. Emitted when a TODO is created, completed, failed, or dismissed.

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | `"todo"` |
| `summary` | string | Yes | The TODO description |
| `data.todo_id` | string | Yes | Stable identifier for this TODO item |
| `data.action` | string | Yes | `"created"`, `"completed"`, `"failed"`, `"dismissed"`, `"deferred"` |
| `data.priority` | string | No | `"high"`, `"normal"`, `"low"` |
| `data.source` | string | No | What created this TODO (e.g., `"failed_action"`, `"agent_decision"`, `"human"`) |
| `data.context` | string | No | Additional context (error message, related task, etc.) |
| `data.due_by` | ISO 8601 | No | When this should be done by |
| `tags` | string[] | No | `["todo", "created"]` |

#### `kind: "scheduled"`

Scheduled work item. Emitted when the agent reports its upcoming scheduled work.

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | `"scheduled"` |
| `summary` | string | Yes | Auto-generated: `"{count} scheduled items, next at {time}"` |
| `data.items` | array | Yes | Array of scheduled items |
| `tags` | string[] | No | `["scheduled"]` |

**`data.items` array element:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Schedule identifier |
| `name` | string | What will run |
| `next_run` | ISO 8601 | When it's next scheduled |
| `interval` | string | Recurrence: `"5m"`, `"1h"`, `"daily"`, `"weekly"`, or null for one-shot |
| `enabled` | boolean | Whether it's active |
| `last_status` | string | `"success"`, `"failure"`, `"skipped"`, or null |

### 2.3 SDK Changes

Add three **agent-level convenience methods** (new Section 12.4 in the SDK spec):

```python
# Queue snapshot — call periodically or on queue changes
agent.queue_snapshot(
    depth=4,
    oldest_age_seconds=120,
    items=[
        {"id": "evt_001", "priority": "high", "source": "human", "summary": "Review contract draft"},
        {"id": "evt_002", "priority": "normal", "source": "webhook", "summary": "Process CRM update"},
    ],
    processing={"id": "evt_003", "summary": "Sending email", "started_at": "...", "elapsed_ms": 4500}
)
```

```python
# TODO lifecycle — call on each state change
agent.todo(
    todo_id="todo_retry_crm",
    action="created",                          # created | completed | failed | dismissed | deferred
    summary="Retry: CRM write failed (403)",
    priority="high",
    source="failed_action",
    context="Tool crm_write returned 403 Forbidden for workspace query"
)

# Later:
agent.todo(todo_id="todo_retry_crm", action="completed", summary="CRM write succeeded after credential refresh")
```

```python
# Scheduled work report — call periodically
agent.scheduled(items=[
    {"id": "sched_crm_sync", "name": "CRM Pipeline Sync", "next_run": "2026-02-11T09:00:00Z",
     "interval": "1h", "enabled": True, "last_status": "success"},
    {"id": "sched_email_digest", "name": "Daily Email Digest", "next_run": "2026-02-12T08:00:00Z",
     "interval": "daily", "enabled": True, "last_status": None},
])
```

**SDK behavior for all three:**
- Build a `custom` event with the appropriate `payload.kind`.
- Auto-generate `payload.summary` from the data.
- Emit with `task_id: null` (agent-level, not task-scoped).
- Queue snapshot depth=0 is valid (empty queue — useful for "queue just drained" signal).

**Optional: Auto-snapshot on heartbeat.** If the developer provides a `queue_provider` callback during agent registration, the SDK emits a queue snapshot with every heartbeat automatically:

```python
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    queue_provider=lambda: {"depth": len(my_queue), "items": [...]}
)
```

This is entirely optional. If not provided, no queue data is emitted.

### 2.4 API Changes

**New query endpoint for work pipeline state:**

```
GET /v1/agents/{agent_id}/pipeline
```

Returns the most recent state for each work pipeline category:

```json
{
  "queue": {
    "last_updated": "2026-02-11T14:30:00Z",
    "depth": 4,
    "oldest_age_seconds": 120,
    "items": [...],
    "processing": {...}
  },
  "todos": {
    "last_updated": "2026-02-11T14:28:00Z",
    "items": [
      {"todo_id": "todo_retry_crm", "action": "created", "summary": "...", "priority": "high", ...}
    ]
  },
  "scheduled": {
    "last_updated": "2026-02-11T14:00:00Z",
    "items": [...]
  }
}
```

**Implementation:** This endpoint queries the most recent event for each `payload.kind` in (`"queue_snapshot"`, `"todo"`, `"scheduled"`) for the given agent. For TODOs, it aggregates all TODO events and derives current state (created but not completed/dismissed = active).

**TODO aggregation logic:**
1. Fetch all events where `payload.kind = "todo"` for this agent.
2. Group by `payload.data.todo_id`.
3. For each group, take the most recent event.
4. Return items where most recent action is NOT `"completed"` or `"dismissed"` (i.e., still active).

### 2.5 Dashboard Changes

**Agent Detail — new "Pipeline" tab** (alongside existing Recent Tasks, Metrics, Event Log):

Three sections, each collapsible:

**Queue Section:**
- Header: "Queue" with depth badge (e.g., "4 items")
- If processing: show current item with elapsed time and amber spinner
- Table of queued items: Priority (color-coded), Source, Summary, Queued (time ago)
- If empty: "Queue empty" in muted text

**TODOs Section:**
- Header: "TODOs" with count badge and breakdown (e.g., "3 active, 2 completed")
- Table of active TODOs: Priority (color-coded), Summary, Source, Created (time ago)
- Completed/dismissed TODOs collapsed by default
- Each TODO shows its lifecycle: created → ... → completed/failed/dismissed

**Scheduled Section:**
- Header: "Scheduled" with count of enabled items
- Table: Name, Next Run, Interval, Last Status (badge), Enabled (toggle display)

**The Hive card enhancement.** When queue snapshot data is available for an agent, the Hive card shows:
- Queue depth as a small badge: `Q:4`
- If processing: mini status line: "Processing: Send email (45s)"
- If queue depth exceeds a threshold (configurable, default 10): amber border on card

**Activity Stream enhancement.** Work pipeline events appear in the Activity Stream like any other event, with kind-specific rendering:
- `queue_snapshot`: "🔲 lead-qualifier: Queue 4 items (oldest: 2m)"
- `todo` (created): "📋 lead-qualifier: TODO created: Retry CRM write"
- `todo` (completed): "✅ lead-qualifier: TODO completed: CRM write succeeded"
- `scheduled`: "📅 lead-qualifier: 3 scheduled items, next: CRM Sync in 25m"

---

## Change 3: Plan-Aware Timeline

**Priority:** #3
**Affects:** Event Schema (payload convention), Dashboard
**Rationale:** Agents that follow multi-step plans need a way to group actions by plan step. This gives the timeline a higher-level narrative: not just "tool X ran" but "Step 2 of 4: Search CRM — tool X ran."

### 3.1 Event Schema Changes

New **recommended payload kind**: `"plan_step"`.

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | `"plan_step"` |
| `summary` | string | Yes | Step description |
| `data.step_index` | integer | Yes | Zero-based step position |
| `data.total_steps` | integer | Yes | Total steps in the plan |
| `data.action` | string | Yes | `"started"`, `"completed"`, `"failed"`, `"skipped"` |
| `data.turns` | integer | No | Turns spent on this step (on completion) |
| `data.tokens` | integer | No | Tokens spent on this step (on completion) |
| `data.plan_revision` | integer | No | Plan revision number (increments on replan) |
| `tags` | string[] | No | `["plan", "step_started"]` |

Also a `"plan_created"` kind for when the plan itself is established:

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | `"plan_created"` |
| `summary` | string | Yes | Plan task/goal description |
| `data.steps` | array | Yes | Array of `{"index": 0, "description": "Search CRM"}` |
| `data.revision` | integer | No | 0 for initial, increments on replan |
| `tags` | string[] | No | `["plan", "created"]` |

### 3.2 SDK Changes

Add `task.plan()` and `task.plan_step()` convenience methods:

```python
# Report plan creation
task.plan(
    goal="Process inbound lead",
    steps=["Search CRM for existing record", "Score lead", "Send follow-up email", "Update CRM"]
)

# Report step progress
task.plan_step(step_index=0, action="started", summary="Search CRM for existing record")
# ... actions happen ...
task.plan_step(step_index=0, action="completed", summary="Found existing CRM record", turns=2, tokens=3200)
task.plan_step(step_index=1, action="started", summary="Score lead")
```

### 3.3 Dashboard Changes

**Task Timeline — plan progress bar.** When the timeline contains `kind: "plan_created"` events, render a plan progress indicator above the horizontal timeline:

- Horizontal bar divided into step segments
- Each segment labeled with step description
- Color-coded: completed (green fill), in-progress (blue fill with animation), pending (gray), failed (red), skipped (strikethrough)
- Per-step stats: turns, tokens (shown on hover or inline)
- Revision indicator if plan was revised mid-execution

The existing timeline nodes continue to render below this bar. The plan bar provides the "zoomed out" view; the timeline nodes provide the "zoomed in" view.

---

## Change 4: Agent Self-Reported Issues

**Priority:** #4
**Affects:** Event Schema (payload convention), SDK, API, Dashboard
**Rationale:** Agents encounter persistent problems (permission errors, API outages, configuration issues) that aren't tied to a single task. These need aggregation, deduplication, and a management workflow.

### 4.1 Event Schema Changes

New **recommended payload kind**: `"issue"`.

| Key | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes | `"issue"` |
| `summary` | string | Yes | Issue title (used for deduplication) |
| `data.issue_id` | string | No | Stable ID. If omitted, server deduplicates by summary hash. |
| `data.severity` | string | Yes | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `data.category` | string | No | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"` |
| `data.context` | object | No | Related details (tool name, error code, affected task, etc.) |
| `data.action` | string | No | `"reported"` (default), `"resolved"`, `"dismissed"` |
| `data.occurrence_count` | integer | No | How many times this issue has occurred (agent-tracked) |
| `tags` | string[] | No | `["issue", "permissions"]` |

### 4.2 SDK Changes

```python
agent.report_issue(
    summary="CRM API returning 403 for workspace queries",
    severity="high",
    category="permissions",
    context={"tool": "crm_search", "error_code": 403, "last_seen": "2026-02-11T14:30:00Z"}
)

# Later, when resolved:
agent.resolve_issue(summary="CRM API returning 403 for workspace queries")
```

### 4.3 Dashboard Changes

**Agent Detail — Issues section** (within the Pipeline tab or as its own tab):

- Table: Severity (color-coded badge), Summary, Category, Occurrences, First Seen, Last Seen, Action buttons
- Active issues sorted by severity, then recency
- Resolved/dismissed issues collapsed by default
- Each issue expandable to show context details

**The Hive card enhancement.** If an agent has active high/critical issues, show an issue indicator on the card.

---

## Change 5: Rich Heartbeat Payloads

**Priority:** Low (stretch goal, but easy to support)
**Affects:** SDK only (event schema already supports payload on heartbeats)
**Rationale:** Heartbeat history with summaries ("CRM sync fired, 3 turns, found 2 new leads") is far more useful than bare alive/dead pings.

### 5.1 SDK Changes

Add optional `heartbeat_payload` callback to agent registration:

```python
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    heartbeat_payload=lambda: {
        "summary": "Idle, last task completed 5m ago",
        "data": {
            "queue_depth": 3,
            "tasks_completed_since_last": 2,
            "active_todos": 1
        }
    }
)
```

When present, the SDK calls this function before each heartbeat and merges the returned dict into the heartbeat event's payload. If the callback raises or returns None, the heartbeat is sent without payload (graceful degradation).

### 5.2 Dashboard Changes

**Agent Detail — Heartbeat History.** When heartbeat events have payloads with a `summary` field, display them in a compact timeline:
- Timestamp, Summary, key data points
- Instead of just "Last heartbeat: 30s ago" on the Hive card, show the summary text

---

## Cross-Reference: Layer Model After v2

| Layer | What it provides | Methods | Task context required? |
|---|---|---|---|
| **Layer 0** | Agent on Hive + heartbeats + stuck detection | `hb.agent()` | No |
| **Layer 1** | Task timelines + action tracking | `agent.task()`, `@agent.track()` | Yes |
| **Layer 1+** | LLM call tracking + cost visibility | `task.llm_call()`, `agent.llm_call()` | Optional |
| **Layer 2** | Custom business events | `task.event()`, `agent.event()` | Optional |
| **Layer 2+** | Work Pipeline | `agent.queue_snapshot()`, `agent.todo()`, `agent.scheduled()` | No |
| **Layer 2+** | Plan tracking | `task.plan()`, `task.plan_step()` | Yes |
| **Layer 2+** | Issue reporting | `agent.report_issue()`, `agent.resolve_issue()` | No |

The "+" notation means these are sub-layers within an existing layer — same instrumentation depth, additional convenience methods.

---

## Cross-Reference: Dashboard Screens After v2

| Screen | v1 | v2 Addition |
|---|---|---|
| **The Hive** (Fleet Overview) | Agent cards with status | Queue depth badge, issue indicator, processing status line |
| **Task Timeline** | Horizontal event timeline | Plan progress bar above timeline, LLM call rendering |
| **Agent Detail** | Recent Tasks, Metrics, Event Log tabs | New **Pipeline** tab (Queue, TODOs, Scheduled), new **Cost** tab, Issues section |
| **Activity Stream** | Global event feed | Kind-specific rendering for queue/todo/scheduled/issue events |
| **Cost Explorer** (NEW) | — | Per-agent, per-model cost breakdown with call detail |

---

## Spec Update Order

Execute these in sequence. Each feeds the next:

1. **Event Schema v2** — Add recommended payload kinds (llm_call, queue_snapshot, todo, scheduled, plan_step, plan_created, issue). No structural schema changes.

2. **Data Model v3** — Add derived state queries for TODO aggregation, queue state, and cost aggregation by model. Add indexes for `payload` kind queries. Consider JSONB for payload field if PostgreSQL.

3. **SDK v2** — Add all new convenience methods: `task.llm_call()`, `agent.llm_call()`, `agent.queue_snapshot()`, `agent.todo()`, `agent.scheduled()`, `task.plan()`, `task.plan_step()`, `agent.report_issue()`, `agent.resolve_issue()`. Add `heartbeat_payload` callback. Add `queue_provider` callback.

4. **API v2** — Add `GET /v1/agents/{agent_id}/pipeline`, `GET /v1/llm-calls`, enhance `GET /v1/metrics` with `group_by` parameter.

5. **Dashboard v2** — Cost Explorer screen, Pipeline tab, plan progress bar, LLM call rendering, Hive card enhancements, Activity Stream kind-specific rendering.

---

*End of Addendum*

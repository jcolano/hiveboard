Below is the Markdown conversion of the document .



---



\# HIVEBOARD + HIVELOOP



\## Event Schema Specification



\### The Canonical Data Model for Agent Observability



\*\*CONFIDENTIAL\*\*

February 2026 | v2.0



---



\# 1. Overview



This document defines the canonical event schema for HiveBoard and HiveLoop. It is the single source of truth for how telemetry data is structured, transmitted, stored, and queried across the entire system.



Every status change, action, error, heartbeat, and metric in HiveBoard is an event. Dashboards, timelines, alerts, and aggregated metrics are all derived from the event stream. There is no separate status table, metrics table, or log table. Events are the only data primitive.



\*\*Design Principle:\*\*

The event schema is progressive. A developer who writes 3 lines of code (Layer 0) uses the same schema as a developer with full instrumentation (Layer 2+). Fields are nullable, not absent. The schema never changes shape between instrumentation depths — only the populated fields change.



---



\## 1.1 What Changed in v2



v2 does not add new event types or new stored fields. The event taxonomy (Section 5) and the canonical stored schema (Section 4) are unchanged from v1.



What v2 adds is a set of well-known payload kinds — standardized structures within the free-form `payload` field that the dashboard and query API recognize and render with specialized treatment.



This preserves:



\* 13 event types

\* One table

\* Same schema shape at every layer



\### v2 Additions



| Payload Kind   | Layer            | Task Context |

| -------------- | ---------------- | ------------ |

| llm\_call       | 1+               | Optional     |

| queue\_snapshot | 2+ (Agent-level) | No           |

| todo           | 2+ (Agent-level) | No           |

| scheduled      | 2+ (Agent-level) | No           |

| plan\_created   | 2+               | Yes          |

| plan\_step      | 2+               | Yes          |

| issue          | 2+ (Agent-level) | No           |



---



\# 2. Tenancy and Identity Model



\## 2.1 Tenant Isolation



Multi-tenancy is enforced by API key only. The client never declares tenant identity.



The server derives `tenant\_id` from the API key on every ingest request.

This field is present on every stored event and is the security boundary for all queries.



---



\## 2.2 Organizational Grouping



Within a tenant, events can be organized using two optional grouping fields. These are indexing and filtering keys, not security controls.



| Field       | Semantics                           | Examples                                      |

| ----------- | ----------------------------------- | --------------------------------------------- |

| environment | Low-cardinality operational context | production, staging, development, test        |

| group       | Flexible organizational label       | sales-team, onboarding-service, experiment-42 |



Defaults:



\* `environment = "production"`

\* `group = "default"`



Layer 0 users never need to configure these.



---



\## 2.3 Agent Identity



Agent metadata (type, version, framework, runtime environment) is agent-level, not event-level.



It is transmitted once on `agent\_registered` and carried in the batch envelope.



The server maintains an agent profile keyed by `(tenant\_id, agent\_id)` and does not denormalize runtime metadata into every event.



---



\# 3. Transmission Model



\## 3.1 Batch Envelope



Events are transmitted in batches.



\*\*Envelope (once per batch):\*\*



\* API key (Authorization header)

\* agent\_id

\* agent\_type

\* agent\_version

\* framework

\* runtime

\* SDK version

\* environment

\* group



\*\*Event Records (array):\*\*



\* event\_id

\* timestamp

\* event\_type

\* task\_id

\* action\_id

\* status

\* duration\_ms

\* payload

\* other per-event fields



Server expands envelope fields into each stored event.



Stored events are fully denormalized.



---



\## 3.2 Server-Side Enrichment



On ingest, the server adds:



| Field       | Purpose                                                      |

| ----------- | ------------------------------------------------------------ |

| tenant\_id   | Derived from API key. Security boundary.                     |

| received\_at | Server ingest timestamp. Used for drift/latency diagnostics. |



---



\# 4. Canonical Stored Event Schema



This is unchanged from v1.



---



\## 4.1 Identity



| Field      | Type   | Req | Description                |

| ---------- | ------ | --- | -------------------------- |

| event\_id   | UUID   | Yes | Client-generated unique ID |

| tenant\_id  | string | Yes | Server-derived             |

| agent\_id   | string | Yes | Emitting agent             |

| agent\_type | string | No  | Agent classification       |



---



\## 4.2 Time



| Field       | Type     | Req | Description      |

| ----------- | -------- | --- | ---------------- |

| timestamp   | ISO 8601 | Yes | Client-side time |

| received\_at | ISO 8601 | Yes | Server time      |



---



\## 4.3 Grouping



| Field       | Type   | Req | Description         |

| ----------- | ------ | --- | ------------------- |

| environment | string | No  | Default: production |

| group       | string | No  | Default: default    |



---



\## 4.4 Task Context (Layer 1+)



| Field          | Type   | Req | Description                     |

| -------------- | ------ | --- | ------------------------------- |

| task\_id        | string | No  | Required for task/action events |

| task\_type      | string | No  | Task classification             |

| task\_run\_id    | string | No  | Disambiguates executions        |

| correlation\_id | string | No  | Cross-agent linkage             |



---



\## 4.5 Action Nesting



| Field            | Type   | Req | Description |

| ---------------- | ------ | --- | ----------- |

| action\_id        | string | No  | Action node |

| parent\_action\_id | string | No  | Parent node |



Structural nesting ≠ causal linkage.



---



\## 4.6 Classification



| Field      | Type        | Req | Description           |

| ---------- | ----------- | --- | --------------------- |

| event\_type | string enum | Yes | See Section 5         |

| severity   | enum        | No  | debug/info/warn/error |



---



\## 4.7 Outcome



| Field       | Type    | Req | Description         |

| ----------- | ------- | --- | ------------------- |

| status      | string  | No  | success/failure/etc |

| duration\_ms | integer | No  | Milliseconds        |



---



\## 4.8 Causal Linkage



| Field           | Type | Req | Description              |

| --------------- | ---- | --- | ------------------------ |

| parent\_event\_id | UUID | No  | Retry/escalation linkage |



---



\## 4.9 Content



| Field   | Type | Req | Description                           |

| ------- | ---- | --- | ------------------------------------- |

| payload | JSON | No  | Developer-defined metadata (32KB max) |



---



\# 5. Event Taxonomy



13 event types total (unchanged from v1).



---



\## 5.1 Layer 0 – Agent Lifecycle



\* `agent\_registered`

\* `heartbeat`



---



\## 5.2 Layer 1 – Structured Execution



Task:



\* `task\_started`

\* `task\_completed`

\* `task\_failed`



Action:



\* `action\_started`

\* `action\_completed`

\* `action\_failed`



---



\## 5.3 Layer 2 – Narrative Telemetry



\* `retry\_started`

\* `escalated`

\* `approval\_requested`

\* `approval\_received`

\* `custom`



All v2 features use `event\_type = "custom"` with structured `payload.kind`.



---



\# 6. Payload Conventions



`payload` is JSON (32KB max).



---



\## 6.1 Universal Structure



```json

{

&nbsp; "kind": "string",

&nbsp; "summary": "string",

&nbsp; "data": { },

&nbsp; "tags": \["string"]

}

```



---



## 6.2 Common Rules (All Well-Known Kinds)

1. `payload.kind` is required and MUST match one of the kind names exactly.
2. `payload.summary` is required. SDKs SHOULD auto-generate the summary when possible (see per-kind rules).
3. `payload.data` is an object. Its fields are defined per kind below.
4. `payload.tags` is optional; if provided, it MUST be an array of strings.
5. These kinds are advisory conventions for consistent rendering and querying. They do not introduce new top-level stored columns.

---

## 6.3 `kind: "llm_call"`

Tracks LLM usage (tokens, cost, latency) and optionally prompt/response previews.

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MAY be null (agent-level call) or set (task-scoped call).

**Required:**

- `kind` (string): must be `"llm_call"`.
- `summary` (string): human-readable label.

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.name` | string | Yes | Logical call identifier (e.g., `"phase1_reasoning"`, `"generate_email"`) |
| `data.model` | string | Yes | Model identifier (e.g., `"gpt-4o"`, `"claude-sonnet-4-20250514"`) |
| `data.tokens_in` | integer | No | Input/prompt tokens |
| `data.tokens_out` | integer | No | Output/completion tokens |
| `data.cost` | float | No | Cost in USD (developer-calculated) |
| `data.duration_ms` | integer | No | Call latency in ms |
| `data.prompt_preview` | string | No | First N chars of prompt (developer-controlled) |
| `data.response_preview` | string | No | First N chars of response (developer-controlled) |
| `data.metadata` | object | No | Arbitrary additional context (caller ID, phase, intent, etc.) |

**Tags:** Optional. Recommended examples: `["llm", "phase1", "sonnet"]`.

**Summary guidance:** SDK SHOULD auto-generate: `"{name}: {model} ({tokens_in}→{tokens_out})"` when token counts are present.

---

## 6.4 `kind: "queue_snapshot"`

Periodic snapshot of the agent's queue state.

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MUST be null (agent-level; not task-scoped).

**Required:**

- `kind` = `"queue_snapshot"`
- `summary`
- `data.depth`

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.depth` | integer | Yes | Number of items currently in the queue (0 allowed) |
| `data.oldest_age_seconds` | integer | No | Age of oldest queued item in seconds |
| `data.items` | array | No | Summary list of queued items (see below) |
| `data.processing` | object | No | Current processing item (if any) |

**`data.items` element schema:**

| Key | Type | Req | Description |
|---|---|---|---|
| `id` | string | No | Item identifier |
| `priority` | string | No | `"high"`, `"normal"`, `"low"` |
| `source` | string | No | `"human"`, `"webhook"`, `"heartbeat"`, `"scheduled"` |
| `summary` | string | No | Brief description of the work item |
| `queued_at` | ISO 8601 string | No | When it entered the queue |

**`data.processing` schema:**

| Key | Type | Req | Description |
|---|---|---|---|
| `id` | string | No | Item identifier |
| `summary` | string | No | What's being processed |
| `started_at` | ISO 8601 string | No | When processing began |
| `elapsed_ms` | integer | No | How long it has been running |

**Tags:** Optional. Recommended default: `["queue"]`.

**Summary guidance:** SDK SHOULD auto-generate: `"Queue: {depth} items, oldest {age}s"` (age omitted if unknown).

---

## 6.5 `kind: "todo"`

Lifecycle events for TODO items that persist across tasks.

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MUST be null (agent-level; TODOs persist across tasks).

**Required:**

- `kind` = `"todo"`
- `summary` (the TODO description)
- `data.todo_id`
- `data.action`

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.todo_id` | string | Yes | Stable identifier for the TODO item |
| `data.action` | string | Yes | `"created"`, `"completed"`, `"failed"`, `"dismissed"`, `"deferred"` |
| `data.priority` | string | No | `"high"`, `"normal"`, `"low"` |
| `data.source` | string | No | Origin (e.g., `"failed_action"`, `"agent_decision"`, `"human"`) |
| `data.context` | string | No | Additional context (error message, related task, etc.) |
| `data.due_by` | ISO 8601 string | No | When it should be done by |

**Tags:** Optional. Recommended examples: `["todo", "created"]`, `["todo", "failed"]`.

---

## 6.6 `kind: "scheduled"`

Reports upcoming scheduled work items.

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MUST be null (agent-level scheduling).

**Required:**

- `kind` = `"scheduled"`
- `summary`
- `data.items`

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.items` | array | Yes | Scheduled items list (see below) |

**`data.items` element schema:**

| Key | Type | Req | Description |
|---|---|---|---|
| `id` | string | No | Schedule identifier |
| `name` | string | No | What will run |
| `next_run` | ISO 8601 string | No | Next scheduled run time |
| `interval` | string or null | No | Recurrence: `"5m"`, `"1h"`, `"daily"`, `"weekly"`, or null for one-shot |
| `enabled` | boolean | No | Whether it's active |
| `last_status` | string or null | No | `"success"`, `"failure"`, `"skipped"`, or null |

**Tags:** Optional. Recommended default: `["scheduled"]`.

**Summary guidance:** SDK SHOULD auto-generate: `"{count} scheduled items, next at {time}"` (time omitted if unknown).

---

## 6.7 `kind: "plan_created"`

Reports creation (or revision) of a multi-step plan within a task.

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MUST be set (task-scoped).

**Required:**

- `kind` = `"plan_created"`
- `summary` (plan goal)
- `data.steps`

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.steps` | array | Yes | Array of step descriptors (see below) |
| `data.revision` | integer | No | 0 for initial plan; increments on replan |

**`data.steps` element schema:**

| Key | Type | Req | Description |
|---|---|---|---|
| `index` | integer | Yes | Step index (zero-based) |
| `description` | string | Yes | Step description |

**Tags:** Optional. Recommended examples: `["plan", "created"]`.

---

## 6.8 `kind: "plan_step"`

Reports lifecycle transitions for a specific plan step within a task.

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MUST be set (task-scoped).

**Required:**

- `kind` = `"plan_step"`
- `summary` (step description / result)
- `data.step_index`
- `data.total_steps`
- `data.action`

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.step_index` | integer | Yes | Zero-based step index |
| `data.total_steps` | integer | Yes | Total steps in plan |
| `data.action` | string | Yes | `"started"`, `"completed"`, `"failed"`, `"skipped"` |
| `data.turns` | integer | No | Turns spent on step (typically set on completion) |
| `data.tokens` | integer | No | Tokens spent on step (typically set on completion) |
| `data.plan_revision` | integer | No | Plan revision number (increments on replan) |

**Tags:** Optional. Recommended examples: `["plan", "step_started"]`, `["plan", "step_completed"]`.

---

## 6.9 `kind: "issue"`

Persistent agent-level issues that may span tasks (permission errors, outages, misconfiguration).

**Event rules:**

- `event_type` MUST be `"custom"`.
- `task_id` MUST be null (agent-level issue tracking).

**Required:**

- `kind` = `"issue"`
- `summary` (issue title; used for deduplication)
- `data.severity`

**Standard structure for `data`:**

| Key | Type | Req | Description |
|---|---|---|---|
| `data.issue_id` | string | No | Stable ID. If omitted, server may deduplicate by summary hash. |
| `data.severity` | string | Yes | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `data.category` | string | No | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"` |
| `data.context` | object | No | Related details (tool name, error code, affected task, etc.) |
| `data.action` | string | No | `"reported"` (default), `"resolved"`, `"dismissed"` |
| `data.occurrence_count` | integer | No | Count of occurrences (agent-tracked) |

**Tags:** Optional. Recommended examples: `["issue", "permissions"]`.

---



\# 7. Depth Mapping



All fields exist at every layer. Unused fields are `null`, not absent.



(Table preserved conceptually from source.)



---



\# 8. Derived State (Not Stored)



All state is computed from the event stream.



Includes:



\* Agent status

\* Stuck detection

\* Queue state

\* Active TODOs

\* Scheduled items

\* Active issues

\* Plan progress



No additional tables required.



---



\# 9. Severity Auto-Defaults



\## By Event Type



\* heartbeat → debug

\* \*\_started → info

\* \*\_completed → info

\* \*\_failed → error

\* retry\_started → warn

\* escalated → warn

\* custom → info



\## By Payload Kind (v2)



| Kind               | Default                    |

| ------------------ | -------------------------- |

| llm\_call           | info                       |

| queue\_snapshot     | debug                      |

| todo (failed)      | warn                       |

| plan\_step (failed) | error                      |

| issue              | derived from data.severity |



---



\# 10. Field Size Limits



| Field           | Limit            |

| --------------- | ---------------- |

| payload         | 32 KB            |

| payload.summary | 512 chars        |

| agent\_id        | 256 chars        |

| task\_id         | 256 chars        |

| environment     | 64 chars         |

| group           | 128 chars        |

| batch size      | 500 events / 1MB |



---



\# 11. Migration Notes (v1 → v2)



\## Breaking Changes



None. v2 is a strict superset.



\## Server Changes



1\. Extend severity defaults to check `payload.kind`.

2\. Implement derived pipeline queries.

3\. Add JSONB GIN index on `payload->'kind'`.



\## Dashboard Changes



1\. Specialized rendering for well-known kinds.

2\. Add Cost Explorer (llm\_call).

3\. Add Pipeline tab.

4\. Plan progress bar.

5\. Hive card enrichment.



---



\*\*— End of Document —\*\*




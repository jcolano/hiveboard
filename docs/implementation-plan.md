# HiveBoard Implementation Plan

> **Version:** 1.0
> **Date:** 2026-02-11
> **Storage strategy:** JSON files (MVP) → MS SQL Server (production)
> **Scope:** Full specs = MVP. No framework integrations in v1.

---

## Team Structure

Two teams working in parallel, connected by the API spec as the binding contract.

| | **Team 1 — Backend** | **Team 2 — Clients** |
|---|---|---|
| **Scope** | Storage layer + API server (REST, WebSocket, Alerts) | HiveLoop SDK + Dashboard |
| **Contract** | *Implements* the API spec | *Codes against* the API spec |
| **Tests with** | curl / httpie / fixture JSON batches | Mock server / recorded responses |

The API spec (`3_hiveboard-api-sdk-spec-v3.md`) is the binding contract. Both teams build and test independently from day one.

---

## Phase 0: Shared Foundation (both teams, ~1 day)

Both teams collaborate to establish shared types, project structure, and test fixtures before diverging.

| # | Task | Description |
|---|------|-------------|
| 0.1 | **Monorepo structure** | Create repo layout: `backend/`, `sdk/`, `dashboard/`, `shared/`. The `shared/` package contains types and constants both teams import. Standard Python packaging (pyproject.toml), dependency management, environment config. |
| 0.2 | **Shared models & constants** | Pydantic models for: Event, BatchEnvelope, all 13 event types, all well-known payload kinds, API error format, WebSocket message types. These are the source-of-truth data structures derived directly from specs 1 and 3. |
| 0.3 | **StorageBackend protocol** | Abstract interface defining every method the API needs. Method signatures must use explicit, filterable parameters — not raw JSON blobs. See **Design Note** below. |
| 0.4 | **Mock fixtures** | A set of realistic JSON fixtures: sample ingestion batches covering all 13 event types and all well-known payload kinds, sample API responses for every query endpoint. Team 1 uses them as test inputs, Team 2 uses them as mock server responses. Both teams validate against the same data. |

### Design Note: StorageBackend Protocol

> **Production target is MS SQL Server.** The JSON file engine is the MVP implementation,
> but every method signature must be designed so it maps cleanly to SQL.
>
> **Do:** Use explicit, filterable parameters:
> ```python
> def get_events(
>     self, tenant_id: str, *,
>     project_id: str | None = None,
>     agent_id: str | None = None,
>     event_type: str | None = None,
>     since: datetime | None = None,
>     until: datetime | None = None,
>     limit: int = 50,
>     cursor: str | None = None,
> ) -> EventPage:
> ```
> Each parameter maps to a WHERE clause. Any SQL backend can implement this efficiently.
>
> **Don't:** Return raw JSON strings expecting Python-side filtering, or accept opaque
> filter dicts that would require the SQL adapter to reverse-engineer intent.
>
> **Rule of thumb:** If a method signature can't be implemented as a single SQL query
> (with JOINs/aggregations as needed), redesign the signature.

---

## Team 1 — Backend

### Phase B1: Storage Layer

| # | Task | Description |
|---|------|-------------|
| B1.1 | **StorageBackend protocol (full)** | Flesh out all methods from Phase 0.3: `insert_events()`, `get_agent()`, `list_agents()`, `get_tasks()`, `get_timeline()`, `get_events()`, `aggregate_metrics()`, `get_cost_data()`, `get_cost_timeseries()`, `get_pipeline()`, plus CRUD for tenants, api_keys, projects, agents, alert_rules, alert_history. Each with explicit filter/sort/pagination parameters. |
| B1.2 | **JSON file implementation** | `JsonStorageBackend` — one JSON file per table, file-level locking, in-memory index on startup for fast filtered queries. Must handle derived-state queries (agent status from events, task status, plan progress) in Python. |
| B1.3 | **Storage tests** | Unit tests exercising every storage method with the shared fixtures from Phase 0.4. Tests run against the `StorageBackend` abstract interface — the same suite will later validate the MS SQL Server adapter. |

### Phase B2: API Server

| # | Task | Description |
|---|------|-------------|
| B2.1 | **FastAPI app + auth** | App skeleton, CORS, `X-API-Key` middleware. Key type resolution: `hb_live_` (read+write), `hb_test_` (read+write, test env), `hb_read_` (read only). Tenant ID injected into request state from API key lookup. Key-type permission enforcement on each endpoint. |
| B2.2 | **Ingestion endpoint** | `POST /v1/ingest` — the critical path. Envelope parsing → per-event validation (13 types, field constraints, 32KB payload limit, 500-event/1MB batch limit) → envelope field expansion → project_id validation → batch insert → agents table update (last_seen, status) → project_agents upsert → 207 partial-success response. |
| B2.3 | **Query endpoints** | All GET endpoints per spec: `/agents` (list + detail), `/tasks` (list + timeline), `/events` (filtered), `/metrics` (with group_by), `/cost` (summary + `/calls` + `/timeseries`), `/llm-calls`, `/agents/{id}/pipeline`, `/projects` (CRUD + archive). All with pagination, filtering, sorting. |
| B2.4 | **WebSocket streaming** | `/v1/stream` — connection upgrade, subscribe/unsubscribe message protocol, channel filtering (events, agents), server-side broadcast triggered on ingestion and agent status changes. |
| B2.5 | **Alerting engine** | Alert rules CRUD, 6 condition types (agent_stuck, task_duration, error_rate, custom_event, heartbeat_missing, cost_threshold), evaluation triggered per ingestion batch, alert history storage, webhook dispatch for fired alerts. |

### Phase B3: Backend Hardening

| # | Task | Description |
|---|------|-------------|
| B3.1 | **Rate limiting & validation** | Per-key rate limits, request size enforcement, proper error responses per spec error format. |
| B3.2 | **Heartbeat compaction & retention** | Compaction logic (prefer payloaded heartbeats over bare ones), configurable data retention window, cleanup job. |

---

## Team 2 — Clients (Detailed Drill-Down)

Team 2 owns two deliverables: the HiveLoop SDK (Python) and the Dashboard (HTML/CSS/JS).
These can be worked on by two sub-tracks within the team since they share no code.

```
Team 2 Internal Parallelism:

  Track A (SDK):  C1.1 → C1.2 → C1.3 → C1.4 → C1.5
                    │                              │
  Track B (Dash):   └── C2.1 → C2.2 → C2.3 → C2.4 → C2.5
                    (starts after C1.1 transport is done,
                     so mock server pattern is established)
```

---

### Phase C1: HiveLoop SDK

> **Location:** `sdk/hiveloop/`
> **Package:** `hiveloop` (pip-installable, `pyproject.toml`)
> **Dependency:** `requests>=2.31.0` (only runtime dep)
> **Tests against:** Mock HTTP server (no real backend needed)

#### C1.1 — Transport Layer

The transport is the foundation — every other SDK component depends on it.

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| C1.1.1 | **Thread-safe event queue** | `collections.deque(maxlen=max_queue_size)` with `threading.Lock`. `enqueue(event, envelope)` adds to deque, triggers flush if `len >= batch_size`. When queue is full, drop oldest events + log warning with count. | `_transport.py` |
| C1.1.2 | **Background flush thread** | Daemon thread. Wakes on timer (`flush_interval`, default 5s) or signal (`threading.Event`). Drains up to `batch_size` items, groups by `agent_id`, constructs batch envelope per agent, POSTs to `/v1/ingest`. Loops until queue empty. | `_transport.py` |
| C1.1.3 | **Batch envelope construction** | Each batch: `{"envelope": {agent metadata}, "events": [...]}`. Agent metadata (type, version, framework, runtime, sdk_version, environment, group) sent once per batch. Events carry only per-event fields. | `_transport.py` |
| C1.1.4 | **Retry with exponential backoff** | Per flush attempt, max 5 retries. HTTP 429 → sleep `retry_after_seconds` from response. HTTP 5xx → backoff 1s, 2s, 4s, 8s, 16s (cap 60s). HTTP 400 → don't retry, log, drop batch. Connection error → same as 5xx. | `_transport.py` |
| C1.1.5 | **Graceful shutdown** | `atexit.register(shutdown, timeout=5.0)`. `shutdown()`: set shutdown flag → signal flush event → join thread (up to timeout) → synchronous final drain → close HTTP session. After shutdown, `enqueue()` is a no-op. | `_transport.py` |
| C1.1.6 | **Manual flush** | `hb.flush()` → signals flush event immediately. Used in tests and before process boundaries. | `_transport.py` |

**Critical invariant:** Transport never raises exceptions to the caller. All failures are logged + events dropped silently. The SDK must never interfere with the instrumented application.

**Exit criterion:** Can `enqueue()` 1000 events, see them batched and POSTed to a mock HTTP endpoint with correct envelope format. Retry logic exercised. Graceful shutdown flushes remaining events.

---

#### C1.2 — Core Primitives

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| C1.2.1 | **Module singleton** | `hiveloop.init(api_key, environment, group, endpoint, flush_interval, batch_size, max_queue_size, debug)` → creates `HiveBoard` singleton. Validates `api_key` starts with `hb_`. Subsequent calls log warning, return existing instance. `hiveloop.shutdown(timeout)` and `hiveloop.reset()` (shutdown + clear singleton). | `__init__.py` |
| C1.2.2 | **HiveBoard client** | Holds `Transport` instance, agent registry (`dict[str, Agent]`), global config (environment, group). `hb.agent()` creates/retrieves agents. `hb.get_agent(id)` returns `Agent | None`. `hb.flush()` delegates to transport. | `__init__.py` |
| C1.2.3 | **Agent registration** | `hb.agent(agent_id, type, version, framework, heartbeat_interval, stuck_threshold, heartbeat_payload, queue_provider)`. Creates `Agent` instance, stores in registry, emits `agent_registered` event with metadata in payload. Idempotent: same `agent_id` returns existing instance (updates metadata). | `_agent.py` |
| C1.2.4 | **Heartbeat thread** | Background daemon thread, sleeps `heartbeat_interval` (default 30s). Emits `heartbeat` event. If `heartbeat_payload` callback is set, calls it and uses return value as payload (catches exceptions, logs, emits bare heartbeat). If `queue_provider` callback is set, calls it and emits separate `custom` event with `kind=queue_snapshot`. | `_agent.py` |
| C1.2.5 | **Task context manager** | `agent.task(task_id, project, type, task_run_id, correlation_id)` returns `Task`. As context manager: `__enter__` emits `task_started` (with `project_id`), sets task as active on `threading.local()`, starts timer. `__exit__` (no error) emits `task_completed` with `duration_ms`. `__exit__` (exception) emits `task_failed` with exception info. Re-raises exception, never swallows. | `_agent.py` |
| C1.2.6 | **Non-CM task API** | `agent.start_task(...)` → returns `Task` (already started). `task.complete(status, payload)` and `task.fail(exception, payload)` for manual lifecycle. `task.set_payload(dict)` sets payload for completion event. | `_agent.py` |
| C1.2.7 | **Manual events** | `task.event(event_type, payload, severity, parent_event_id)` — task-scoped, inherits task_id/project_id/task_run_id. `agent.event(event_type, payload, severity, parent_event_id)` — agent-level, no task context. | `_agent.py` |
| C1.2.8 | **Event construction** | `Agent._emit_event(**kwargs)` builds event dict: auto-generates `event_id` (UUID4), `timestamp` (UTC ISO 8601), strips None values (but always includes event_id, timestamp, event_type). Enqueues via transport with agent envelope. | `_agent.py` |

**Exit criterion:** Can do `hb = hiveloop.init(...)`, `agent = hb.agent(...)`, `with agent.task(...) as t: t.event(...)`, see `agent_registered`, `heartbeat`, `task_started`, `task_completed`, and custom events flow through transport.

---

#### C1.3 — Decorator & Nesting

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| C1.3.1 | **@agent.track(action_name) decorator** | Works with both sync and async functions. On call: generates `action_id` (UUID4), reads `parent_action_id` from `contextvars.ContextVar`, sets own `action_id` as current, emits `action_started`. On return: emits `action_completed` with `duration_ms`, status `"success"`. On exception: emits `action_failed` with exception info, re-raises. Always restores previous `action_id` in `finally`. | `_agent.py` |
| C1.3.2 | **Nesting detection** | `contextvars.ContextVar("_current_action_id", default=None)`. Each `@track` reads current value as its parent, sets itself as current, restores on exit. Works correctly across threads (contextvars are per-context) and async (each coroutine gets its own context copy). Nested calls produce `parent_action_id` chains. | `_agent.py` |
| C1.3.3 | **Context manager alternative** | `agent.track_context(action_name)` returns `_ActionContext`. Same lifecycle as decorator but used inline: `with agent.track_context("step") as action: action.set_payload({...})`. | `_agent.py` |
| C1.3.4 | **Auto-populated payload** | All action events include: `action_name` (from decorator arg), `function` (fully qualified: `module.qualname`). Failed actions add: `exception_type`, `exception_message`. | `_agent.py` |

**Exit criterion:** Nested tracked functions produce correct `parent_action_id` chains. Async functions work. Mixed sync/async nesting works. Exception propagation preserved.

---

#### C1.4 — Convenience Methods

All convenience methods emit `custom` events with well-known `payload.kind` values.
Each auto-generates `payload.summary` per the spec's guidance.

| # | Sub-task | Signature | Payload Kind | Scope |
|---|----------|-----------|-------------|-------|
| C1.4.1 | **task.llm_call()** | `(name, model, *, tokens_in, tokens_out, cost, duration_ms, prompt_preview, response_preview, metadata)` | `llm_call` | Task-scoped |
| C1.4.2 | **agent.llm_call()** | Same as above | `llm_call` | Agent-level (no task) |
| C1.4.3 | **task.plan()** | `(goal, steps: list[str], *, revision=0)` | `plan_created` | Task-scoped |
| C1.4.4 | **task.plan_step()** | `(step_index, action, summary, *, total_steps, turns, tokens, plan_revision)` | `plan_step` | Task-scoped |
| C1.4.5 | **agent.queue_snapshot()** | `(depth, *, oldest_age_seconds, items: list[dict], processing: dict)` | `queue_snapshot` | Agent-level |
| C1.4.6 | **agent.todo()** | `(todo_id, action, summary, *, priority, source, context, due_by)` | `todo` | Agent-level |
| C1.4.7 | **agent.scheduled()** | `(items: list[dict])` | `scheduled` | Agent-level |
| C1.4.8 | **agent.report_issue()** | `(summary, severity, *, issue_id, category, context, occurrence_count)` | `issue` (action=`"reported"`) | Agent-level |
| C1.4.9 | **agent.resolve_issue()** | `(summary, *, issue_id)` | `issue` (action=`"resolved"`) | Agent-level |

**Auto-generated summaries:**

| Kind | Format |
|------|--------|
| `llm_call` | `"{name} → {model} ({tokens_in} in / {tokens_out} out, ${cost})"` — omit fragments when values absent |
| `queue_snapshot` | `"Queue: {depth} items, oldest {age}s"` — omit age if unknown |
| `plan_created` | The `goal` parameter becomes the summary |
| `plan_step` | `"Step {index} {action}: {summary}"` |
| `todo` | The `summary` parameter directly |
| `scheduled` | `"{count} scheduled items, next at {time}"` — time omitted if unknown |
| `issue` | The `summary` parameter directly |

**task.plan() state tracking:** When `task.plan()` is called, the Task stores `total_steps` internally. Subsequent `task.plan_step()` calls auto-populate `total_steps` if not explicitly provided.

**Exit criterion:** Each convenience method produces a correctly-shaped `custom` event with the right `payload.kind`, required `data` fields, and auto-generated summary.

---

#### C1.5 — SDK Tests

| # | Sub-task | Description | File |
|---|----------|-------------|------|
| C1.5.1 | **Mock HTTP server** | Simple `http.server` or `pytest` fixture that accepts `POST /v1/ingest`, captures batches, returns 200/207. Configurable to return 429/500 for retry tests. | `tests/conftest.py` |
| C1.5.2 | **Transport tests** | Batching (respects batch_size), flush on timer, flush on shutdown, retry on 5xx, no retry on 400, backoff timing, queue overflow drops oldest, manual flush. | `tests/test_transport.py` |
| C1.5.3 | **Core primitive tests** | Init singleton behavior, agent registration event, heartbeat emission, task lifecycle (started/completed/failed), task context manager exception handling, thread-local task isolation. | `tests/test_core.py` |
| C1.5.4 | **Action tracking tests** | Decorator sync + async, nesting (3 levels), parent_action_id chain correctness, exception propagation, timing accuracy, context manager alternative. | `tests/test_tracking.py` |
| C1.5.5 | **Convenience method tests** | Each of the 9 methods: correct payload.kind, required data fields present, auto-generated summary format, plan state tracking (total_steps inheritance). Validate against shared fixture format. | `tests/test_convenience.py` |
| C1.5.6 | **Heartbeat callback tests** | `heartbeat_payload` callback invoked, return value used as payload. `queue_provider` callback emits separate queue_snapshot event. Callback exception → bare heartbeat still sent. | `tests/test_heartbeat.py` |

---

### Phase C2: Dashboard

> **Location:** `dashboard/`
> **Technology:** Single HTML file (matching v3 prototype approach) with embedded CSS + JS
> **Reference:** `docs/hiveboard-dashboard-v3.html` (the complete prototype)
> **Data source:** Initially mock data (from prototype), then API + WebSocket

The v3 HTML prototype is a complete, working reference implementation. Phase C2 is about
converting it from hardcoded mock data to a live, API-connected dashboard.

#### C2.1 — Static Shell & Theming

| # | Sub-task | Description |
|---|----------|-------------|
| C2.1.1 | **Base HTML structure** | 3-region grid layout: `.hive-panel` (280px) \| `.center-panel` (1fr) \| `.stream-panel` (320px). Top bar (48px): logo (hexagon clip-path), workspace badge, view tabs (Mission Control \| Cost Explorer), status pill with pulse animation, environment selector dropdown. |
| C2.1.2 | **CSS design tokens** | All colors as CSS variables: `--bg-deep` (#0a0c10), `--bg-primary` (#0f1117), `--bg-card` (#161922), `--bg-elevated` (#1c2030). Status colors: `--idle` (gray), `--active` (blue), `--success` (green), `--warning` (amber), `--error` (red), `--stuck` (dark red), `--llm` (purple). Text: `--text-primary` (#e8eaed), `--text-secondary` (#9ca3af). Accent: `--accent` (#f59e0b). |
| C2.1.3 | **Typography** | JetBrains Mono (code/monospace), DM Sans (UI/sans-serif). Google Fonts loaded in `<head>`. `font-variant-numeric: tabular-nums` for aligned numbers. |
| C2.1.4 | **Animations** | `@keyframes pulse-dot` (status pill), `@keyframes stuck-blink` (stuck agents, 1.5s), `@keyframes attention-pulse` (urgent cards glow), `@keyframes plan-pulse` (active plan step), `@keyframes fadeIn` (0.2s, translateY 4px). |
| C2.1.5 | **Global filter bar** | Conditional bar between topbar and main layout (33px). Shows active filters ("agent = X · status = Y") with clear button. Adjusts main layout height when visible. |
| C2.1.6 | **Scrollbar styling** | Custom webkit scrollbars: 4px width, `--bg-elevated` track, `--border` thumb, rounded. Applied to `.hive-list`, `.stream-list`, `.timeline-canvas`, `.cost-tables`. |

**Exit criterion:** Empty shell renders with correct layout, colors, fonts, and animations. No data yet — just the chrome.

---

#### C2.2 — The Hive Panel (Left Sidebar)

| # | Sub-task | Description |
|---|----------|-------------|
| C2.2.1 | **Panel header** | "The Hive" title, agent count badge, attention indicator (red pulse when stuck/error agents exist). |
| C2.2.2 | **Agent card component** | Card content: agent name (bold, mono) \| status badge (uppercase, colored). Meta row: type label \| heartbeat indicator (`hbClass`: fresh < 60s, stale < 300s, dead ≥ 300s with `hbText` formatting). |
| C2.2.3 | **Pipeline enrichment (v3)** | Queue badge "Q:{depth}" (red highlight if depth > 5). Issue indicator "⚠ {count} issues". Processing summary line "↳ {action description}". Only shown when data is present. |
| C2.2.4 | **Sparkline chart** | 12-value mini bar chart per agent. Bars colored by status. Rendered as inline HTML divs with percentage heights. |
| C2.2.5 | **Status sorting & filtering** | Default sort by attention priority: stuck < error < waiting_approval < processing < idle. Status filter (from summary bar clicks) hides non-matching agents. |
| C2.2.6 | **Selection behavior** | Single click: select agent → filter all views to that agent. Double click: open agent detail view in center panel. Selected state: accent border + dim background. Urgent state (stuck/error): red glow box-shadow. |

**Exit criterion:** Agent cards render with all 6 status states, pipeline enrichment, heartbeat indicators. Click interactions work. Sorting and filtering functional.

---

#### C2.3 — Center Panel

Three views that swap in the center column, controlled by topbar tabs and agent selection.

##### C2.3.1 — Mission Control (default view)

| # | Sub-task | Description |
|---|----------|-------------|
| C2.3.1a | **Summary statistics bar** | 7-stat horizontal ribbon: Total Agents, Processing, Waiting, Stuck, Errors, Success Rate (1h), Avg Duration. Processing/Waiting/Stuck/Errors are clickable → toggle status filter. |
| C2.3.1b | **Mini-chart metrics row** | 4 metric cells: Throughput (blue), Success Rate (green), Errors (red), LLM Cost/Task (purple). Each renders 16-bar chart with values. |
| C2.3.1c | **Task table** | Columns: Task ID, Agent, Type, Status (dot + label), Duration, LLM calls (◆ badge, purple), Cost, Time. Sortable. Row click selects task → updates timeline. Task ID and Agent are clickable links. Selected row highlighted. |
| C2.3.1d | **Timeline header** | Shows selected task metadata: Task ID with permalink button, duration, agent link, status indicator with colored dot. |
| C2.3.1e | **Plan progress bar** | Conditional (only when task has plan). Step indicators: completed (green), active (blue, pulsing), failed (red), pending (gray border), skipped (gray, dim). Header: "Plan · X steps" \| "Y/Z completed". |
| C2.3.1f | **Timeline visualization** | Horizontal scrollable canvas. Nodes: colored dots (14px circle for normal, 16px square for LLM). Labels above, times below. LLM nodes get model badge above. Connectors: gradient lines between nodes with duration labels. |
| C2.3.1g | **Branch visualization** | Retry/error branches render as smaller nodes below main sequence. Vertical connector from error node to branch. Branch nodes show retry attempts with backoff info. |
| C2.3.1h | **Pinned node detail** | Click timeline node → detail panel slides open below. Shows all `node.detail` fields as key-value pairs, duration, tags as badges. Close button. Node scales 1.4x when pinned. |

##### C2.3.2 — Cost Explorer

| # | Sub-task | Description |
|---|----------|-------------|
| C2.3.2a | **Cost ribbon** | 5-stat bar: Total Cost ($, purple), LLM Calls (count), Tokens In, Tokens Out, Avg Cost/Call. All in the 1h window. |
| C2.3.2b | **By Model table** | Columns: Model (with badge), Calls, Tokens In, Tokens Out, Cost, visual cost bar (% of max). |
| C2.3.2c | **By Agent table** | Columns: Agent (clickable), Calls, Tokens In, Tokens Out, Cost, visual cost bar. Sorted by descending cost. Agent click → opens agent detail. |

##### C2.3.3 — Agent Detail View

| # | Sub-task | Description |
|---|----------|-------------|
| C2.3.3a | **Agent header** | Agent name (large, mono, bold), status badge, "✕ Close Detail" button. |
| C2.3.3b | **Tab navigation** | Two tabs: Tasks, Pipeline. Active tab has amber underline. Click to switch content. |
| C2.3.3c | **Tasks tab** | Same table format as mission control task table, pre-filtered to selected agent. Task ID clickable → returns to mission control with task selected. |
| C2.3.3d | **Pipeline tab — Issues** | Table: Summary, Severity badge (critical=dark red, high=red, medium=amber), Category, Occurrences. Conditional: only shown if agent has active issues. |
| C2.3.3e | **Pipeline tab — Queue** | Table: ID, Priority badge (high=red, normal=blue, low=gray), Source, Summary, Age. Empty state: "Queue is empty — agent is caught up". |
| C2.3.3f | **Pipeline tab — TODOs** | Table: Summary, Priority, Source. Conditional: only shown if agent has TODOs. |
| C2.3.3g | **Pipeline tab — Scheduled** | Table: Name, Next Run, Interval, Status indicator (green=ok, amber=warning). |

**Exit criterion:** All three center views render correctly. View switching works. Agent detail opens on agent double-click/selection. Timeline nodes are interactive. Plan bar shows progress.

---

#### C2.4 — Activity Stream (Right Sidebar)

| # | Sub-task | Description |
|---|----------|-------------|
| C2.4.1 | **Stream header** | "Activity" title with animated green "LIVE" badge (pulse dot). Event count. |
| C2.4.2 | **Filter chips** | 7 filter buttons: all, task, action, error, llm, pipeline, human. Active chip: accent background + border. Click to filter. |
| C2.4.3 | **Event card component** | Each event: kind icon (◆ llm, ⊞ queue, ☐ todo, ⚑ issue, ⏲ scheduled) + type name (colored by event type). Time (right-aligned, muted). Agent › Task breadcrumb (clickable links). Summary text (HTML-safe). |
| C2.4.4 | **Severity coloring** | Events colored by severity/kind: error=red, warn=amber, llm_call=purple, info=blue, debug=gray. |
| C2.4.5 | **Auto-scroll** | New events prepend to top. Auto-scrolls to top on new event. Pauses auto-scroll when user scrolls down. Resumes when user scrolls back to top. |
| C2.4.6 | **Agent/task filtering** | When an agent is selected in the Hive, stream filters to that agent's events. Stream respects both agent selection AND chip filter simultaneously. |

**Exit criterion:** Stream renders events with correct icons, colors, filters. New events animate in from top. Filter chips work. Agent selection filters the stream.

---

#### C2.5 — API & WebSocket Wiring

This phase replaces mock data with real backend calls. Can only be fully tested during
Integration Phase I1, but the wiring code is built here with mock server fallback.

| # | Sub-task | Description |
|---|----------|-------------|
| C2.5.1 | **API client module** | JS module with functions: `fetchAgents()`, `fetchTasks(agentId?)`, `fetchTimeline(taskId)`, `fetchEvents(filters)`, `fetchMetrics(range)`, `fetchCost(range)`, `fetchPipeline(agentId)`. All call `GET /v1/*` with `Authorization: Bearer {apiKey}`. Returns parsed JSON. Error handling: show toast on failure, fall back to last known data. |
| C2.5.2 | **Initial data load** | On page load: parallel fetch of agents, tasks, events, metrics. Populate all views. Loading spinner while fetching. Error state if backend unreachable. |
| C2.5.3 | **WebSocket connection** | Connect to `ws://localhost:8000/v1/stream?token={apiKey}`. On open: send `subscribe` message with `channels: ["events", "agents"]` and current filters. Handle reconnection with exponential backoff on disconnect. |
| C2.5.4 | **Live event handling** | On `event.new` message: prepend to activity stream, update relevant task in task table, refresh timeline if affected task is selected. On `agent.status_changed`: update agent card status, re-sort hive. On `agent.stuck`: highlight agent card with urgent glow. |
| C2.5.5 | **Polling fallback** | If WebSocket fails to connect after 3 attempts: fall back to polling `/v1/events?since={lastTimestamp}` every 5 seconds. Same update logic as WebSocket handler. |
| C2.5.6 | **Filter sync** | When user changes filters (agent, status, stream filter): update WebSocket subscription with new filters via `subscribe` message. Update API calls to include new filter params. |

**Exit criterion:** Dashboard loads data from API, renders it, and updates in real-time via WebSocket. Falls back to polling if WebSocket unavailable.

---

## Integration Phases (teams reunite)

### Phase I1: Connect & Validate

| # | Task | Description |
|---|------|-------------|
| I1.1 | **SDK → Backend** | Point real SDK at real backend. Validate: events flow through ingestion, appear in query endpoints, trigger WebSocket broadcasts. Fix contract mismatches. |
| I1.2 | **Dashboard → Backend** | Point dashboard at real API. Validate: all views populate correctly, WebSocket updates render in real-time. Fix response format assumptions. |
| I1.3 | **Full pipeline test** | SDK → API → Storage → Query → Dashboard → WebSocket — one continuous flow. All 13 event types, all well-known payload kinds visible end-to-end. |

### Phase I2: Real-World Proof

| # | Task | Description |
|---|------|-------------|
| I2.1 | **Instrument loopCore agent** | Take the reference agent from `docs/my_own_agents_case/` and instrument it with HiveLoop SDK. Validate every observable signal from `OBSERVABILITY.md` maps to HiveBoard events. |
| I2.2 | **MS SQL Server adapter** | Implement `MsSqlStorageBackend` behind the same `StorageBackend` protocol. DDL adapted to T-SQL. Run the same storage test suite (from B1.3) to validate. Migration tooling from JSON files. |

---

## Timeline View

```
Week    Team 1 (Backend)              Team 2 (Clients)
─────   ─────────────────────         ─────────────────────
  0     ◄──── Phase 0: Shared foundation ────►

  1     B1: Storage layer              C1: SDK transport + core
  2     B1.3 + B2.1: Tests + Auth      C1.3-C1.5: Decorators + convenience + tests
  3     B2.2: Ingestion endpoint       C2.1-C2.2: Dashboard shell + Hive panel
  4     B2.3: Query endpoints          C2.3: Center panel views
  5     B2.4-B2.5: WebSocket + Alerts  C2.4-C2.5: Stream + mock wiring

  6     ◄──── Phase I1: Integration ────►
  7     ◄──── Phase I2: Real-world proof ────►
```

---

## Reference Documents

| Spec | File | Purpose |
|------|------|---------|
| Context Brief | `docs/0_HiveBoard-Context-Brief.md` | Vision, principles, positioning |
| Event Schema v2 | `docs/1_HiveBoard_Event_Schema_v2.md` | Canonical event structure, 13 types, payload kinds |
| Data Model v5 | `docs/2_hiveboard-data-model-spec-v5.md` | Tables, indexes, derived-state SQL, ingestion pipeline |
| API + SDK Spec v3 | `docs/3_hiveboard-api-sdk-spec-v3.md` | Full API contract + SDK surface |
| Dashboard v3 | `docs/hiveboard-dashboard-v3.html` | Interactive HTML prototype |
| Reference Agent | `docs/my_own_agents_case/` | loopCore observability module + agent loop source |

> **Note:** The data model spec describes SQLite → PostgreSQL storage strategy. This is **obsolete**.
> Actual strategy: **JSON files (MVP) → MS SQL Server (production)**.

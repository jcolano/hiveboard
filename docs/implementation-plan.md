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

## Team 2 — Clients

### Phase C1: HiveLoop SDK

| # | Task | Description |
|---|------|-------------|
| C1.1 | **Transport layer** | Thread-safe deque buffer, background flush thread (configurable interval + batch size), batch envelope construction (agent metadata sent once per batch), retry with exponential backoff, graceful shutdown (`atexit` + signal handlers), offline buffering. Built first — everything else depends on it. |
| C1.2 | **Core primitives** | `hiveloop.init(api_key, base_url)`, `hb.agent(agent_id, name, type, ...)` returning `Agent` handle with heartbeat scheduling + optional `heartbeat_payload`/`queue_provider` callbacks. `agent.task(task_name, project=...)` context manager auto-emitting `task_started`/`task_completed`/`task_failed` with timing. `task.event()` + `agent.event()` for custom events. |
| C1.3 | **Decorator & nesting** | `@agent.track(action_type=...)` decorator. Auto-emits `action_started`/`completed`/`failed`. Nesting detection via `contextvars` (parent_action_id chain). Error capture and timing. |
| C1.4 | **Convenience methods** | `llm_call()` (model, tokens, cost, duration), `plan()`/`plan_step()`, `queue_snapshot()`, `todo()`, `scheduled()`, `report_issue()`/`resolve_issue()`. Each maps to a well-known payload kind with field validation per event schema spec. |
| C1.5 | **SDK tests** | Unit tests with a mock HTTP server. Validate: batch envelope construction, all 13 event types emittable, retry behavior, graceful shutdown flushing, nesting detection. Tests use shared fixtures. |

### Phase C2: Dashboard

| # | Task | Description |
|---|------|-------------|
| C2.1 | **Static shell & theming** | 3-column grid (280px / flex / 320px), top bar (logo, workspace, view tabs, status pill, env selector), dark theme with CSS variables, status color system (idle/processing/waiting_approval/error/stuck + offline). Reference: `hiveboard-dashboard-v3.html`. |
| C2.2 | **The Hive panel** | Agent cards: 6 status states with CSS animations, heartbeat recency indicator, pipeline enrichment badges (queue depth, issue count), search/filter, click-to-select → triggers agent detail view. |
| C2.3 | **Center panel** | **Mission Control**: task list with status/duration, timeline with LLM call nodes rendered distinctly, plan progress bar. **Cost Explorer**: per-agent/per-model cost breakdown, time-series chart. **Agent Detail**: transforms center on agent click — tabs for Tasks, Pipeline (queue/todos/scheduled/issues), Metrics. |
| C2.4 | **Activity Stream** | Right panel: real-time event feed with kind-aware icons/rendering (different treatment for llm_call, task_completed, issue, custom), severity coloring, auto-scroll with pause-on-hover. |
| C2.5 | **API & WebSocket wiring** | Replace mock data with real API calls for initial load. WebSocket connection for live updates: new events into stream, agent status transitions, task progress updates. |

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

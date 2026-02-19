# HiveBoard Architecture

> Observability platform for AI agents — "Datadog for agents"

HiveBoard captures, stores, and visualizes every event in an AI agent's lifecycle: task execution, tool usage, LLM calls, costs, errors, approvals, and more. It pairs a Python SDK (**HiveLoop**) with a FastAPI backend and a real-time dashboard.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Agent Process                                                      │
│                                                                     │
│  ┌──────────┐   events    ┌───────────┐  POST /v1/ingest           │
│  │ HiveLoop │ ──────────► │ Transport │ ─────────────────►         │
│  │   SDK    │  (in-mem    │  (batched  │   5s / 100 events         │
│  └──────────┘   deque)    │   HTTP)    │                           │
│       ▲                   └───────────┘                            │
│       │ agent.track()                                              │
│       │ task.llm_call()                                            │
│       │ task.complete()                                            │
│  ┌────┴─────┐                                                      │
│  │  Agent   │                                                      │
│  │  Code    │                                                      │
│  └──────────┘                                                      │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  HiveBoard Backend (FastAPI)                                        │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┐ │
│  │ Auth         │  │ Rate Limit    │  │ Ingestion Pipeline       │ │
│  │ Middleware   │  │ Middleware    │  │  validate → enrich →     │ │
│  │ (API key /   │  │ (100/s ingest │  │  store → aggregate →    │ │
│  │  JWT)        │  │  30/s query)  │  │  broadcast → alert      │ │
│  └──────────────┘  └───────────────┘  └──────────────────────────┘ │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐  ┌──────────────────────────┐ │
│  │ Storage      │  │ Aggregator    │  │ Query Endpoints          │ │
│  │ (JSON files, │  │ (agent_hourly │  │  /agents /tasks /events  │ │
│  │  per-agent   │  │  model_hourly │  │  /metrics /cost /pipeline│ │
│  │  partitioned)│  │  task_runs)   │  │  /insights /alerts       │ │
│  └──────────────┘  └───────────────┘  └──────────────────────────┘ │
│                                                                     │
│  ┌──────────────┐  ┌───────────────┐                               │
│  │ WebSocket    │  │ Alert Engine  │                               │
│  │ Manager      │  │ (6 condition  │                               │
│  │ (real-time   │  │  evaluators)  │                               │
│  │  push)       │  │              │                               │
│  └──────────────┘  └───────────────┘                               │
└─────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Dashboard (Static HTML + JS)                                       │
│                                                                     │
│  Fleet View │ Agent View │ Analytics │ Insights                     │
│  (hive grid, task timeline, event stream, cost explorer, pipeline)  │
│                                                                     │
│  WebSocket subscription: real-time events + agent status changes    │
│  Polling fallback: 10s agents+events, 30s metrics+tasks             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Directory Structure

```
hiveBoard/
├── config.json                      # Runtime config (jwt_secret, dev_key, data_dir, mode)
├── pyproject.toml                   # Package definition (hiveBoard)
├── data/                            # Live JSON storage
│   ├── tenants.json                 #   Multi-tenant accounts
│   ├── api_keys.json                #   SHA-256 hashed API keys
│   ├── users.json                   #   User accounts with bcrypt passwords
│   ├── invites.json                 #   Pending team invites
│   ├── projects.json                #   Project definitions
│   ├── project_agents.json          #   Project ↔ Agent junction
│   ├── agents.json                  #   Agent metadata cache
│   ├── alert_rules.json             #   Alert rule definitions
│   ├── alert_history.json           #   Fired alert records
│   ├── agent_hourly.json            #   Pre-aggregated agent metrics
│   ├── model_hourly.json            #   Pre-aggregated model metrics
│   ├── task_runs.json               #   Pre-computed task summaries
│   ├── llm_pricing.json             #   LLM cost-per-token table
│   └── events/                      #   Per-agent partitioned events
│       └── {agent_id}.json
├── src/
│   ├── backend/                     # FastAPI server
│   │   ├── app.py                   #   All endpoints + ingestion pipeline
│   │   ├── middleware.py            #   Auth + rate limiting
│   │   ├── storage_json.py          #   JSON file storage implementation
│   │   ├── aggregator.py            #   Pre-aggregate maintenance
│   │   ├── alerting.py              #   Alert condition evaluators
│   │   ├── websocket.py             #   WebSocket manager + subscriptions
│   │   ├── ws_bridge.py             #   AWS API Gateway WebSocket bridge
│   │   ├── auth.py                  #   Password hashing, JWT, key generation
│   │   ├── config.py                #   Config file + env var loader
│   │   └── llm_pricing.py           #   LLM cost estimation engine
│   ├── shared/                      # Contract layer (shared by backend + SDK)
│   │   ├── enums.py                 #   13 event types, 7 payload kinds, limits
│   │   ├── models.py                #   40+ Pydantic models
│   │   ├── storage.py               #   StorageBackend Protocol (35 methods)
│   │   └── fixtures/
│   │       └── sample_batch.json    #   22-event test fixture
│   ├── sdk/hiveloop/                # Python SDK
│   │   ├── __init__.py              #   HiveBoard class, init/reset/flush
│   │   ├── _agent.py                #   Agent, Task, ActionContext classes
│   │   ├── _transport.py            #   Batched HTTP transport with retry
│   │   ├── _enums.py                #   SDK-local enum copy
│   │   ├── contrib/
│   │   │   └── log_handler.py       #   Python logging → HiveBoard issues
│   │   └── integrations/
│   │       └── claude_agent_sdk.py  #   Claude Agent SDK auto-instrumentation
│   └── static/                      # Dashboard frontend
│       ├── fleet.html               #   Fleet dashboard
│       ├── agent-view.html          #   Single-agent detail
│       ├── analytics.html           #   Analytics overview
│       ├── insights.html            #   Pre-aggregated insights
│       └── js/
│           ├── hiveboard.js         #   Fleet dashboard logic
│           ├── agent-view.js        #   Agent view logic
│           ├── analytics.js         #   Analytics logic
│           └── common.js            #   Shared utilities
└── tests/                           # Local only (not tracked in git)
    ├── conftest.py                  #   Shared fixtures
    ├── test_storage.py              #   61 storage tests
    ├── test_api.py                  #   38 API tests
    ├── test_integration.py          #   13 integration tests
    ├── test_pruning.py              #   Retention/pruning tests
    ├── test_retention.py            #   TTL + cold retention tests
    ├── test_claude_agent_sdk.py     #   54 SDK integration tests
    └── ...                          #   Core SDK, transport, convenience, etc.
```

---

## Core Data Model

### The Single Events Table

HiveBoard's foundational design: **one `events` table is the source of truth**. Agents, tasks, metrics, costs, pipelines — everything is derived from events. There are no separate `tasks` or `agents` tables with primary data.

An event has this shape:

| Field | Description |
|---|---|
| `event_id` | Globally unique identifier |
| `tenant_id` | Multi-tenant isolation key |
| `agent_id` | Which agent emitted this event |
| `task_id` | Which task this event belongs to |
| `task_run_id` | Disambiguates retries of the same task_id |
| `event_type` | One of 13 types (see below) |
| `timestamp` | ISO 8601 with timezone |
| `payload` | `{kind, summary, data, tags}` — the event's content |
| `severity` | `debug / info / warn / error` — auto-defaulted per event type |
| `environment` | `production / staging / development` |
| `group` | Logical grouping (e.g. `default`, `batch-run-42`) |
| `project_id` | Project association |
| `parent_event_id` | Links child events (e.g. action → parent action) |
| `correlation_id` | Cross-task correlation |

### 13 Event Types (Three Layers)

**Layer 0 — Lifecycle** (infrastructure plumbing):
- `agent_registered` — agent announces itself
- `heartbeat` — periodic liveness signal

**Layer 1 — Structured** (task and action lifecycle):
- `task_started` / `task_completed` / `task_failed`
- `action_started` / `action_completed` / `action_failed`

**Layer 2 — Narrative** (rich operational context):
- `escalated` — agent escalates to human
- `approval_requested` / `approval_received` — human-in-the-loop flow
- `retry_started` — retry with backoff
- `custom` — extensible event (LLM calls, plans, queue snapshots, etc.)

### 7 Payload Kinds

The `custom` event type carries structured payloads via the `kind` field:

| Kind | Purpose | Key Fields |
|---|---|---|
| `llm_call` | LLM API call metrics | model, tokens_in/out, cost, duration_ms |
| `queue_snapshot` | Agent work queue state | pending_count, processing, items |
| `todo` | Task backlog items | todo_id, action (created/completed), priority |
| `scheduled` | Scheduled future work | items with next_run times |
| `plan_created` | Agent creates an execution plan | goal, steps[] |
| `plan_step` | Progress on a plan step | step_index, action, turns, tokens |
| `issue` | Problem report | severity, category, issue_id |

---

## SDK Architecture (HiveLoop)

The SDK runs inside the agent process and emits events to the backend.

### Three-Layer Design

```
Agent Code
    │
    ▼
┌─────────────────────────────────────────┐
│  Agent / Task / ActionContext            │  ← Public API
│  Convenience methods: llm_call(),       │
│  plan(), escalate(), track_context()    │
└────────────────┬────────────────────────┘
                 │ _emit_event()
                 ▼
┌─────────────────────────────────────────┐
│  Transport                              │  ← Batched HTTP
│  Bounded deque (10,000 items)           │
│  Background flush thread (5s / 100 evt) │
│  Retry with exponential backoff         │
│  atexit shutdown hook                   │
└─────────────────────────────────────────┘
```

### Key Classes

**`HiveBoard`** — Singleton entry point. Created via `hiveloop.init(api_key=...)`. Manages agent registry and transport lifecycle.

**`Agent`** — Represents one agent identity. Created via `hb.agent(agent_id=...)` (idempotent). Runs a heartbeat daemon thread. Provides `task()` (context manager) and `start_task()` (manual lifecycle) for task tracking, plus `track_context()` / `@track` for action tracking.

**`Task`** — Represents one task execution. Can be used as a context manager (`with agent.task(...)`) or manually (`task = agent.start_task(...); task.complete()`). Emits `task_started` on entry, `task_completed`/`task_failed` on exit. Provides convenience methods for all Layer 2 events.

**`_ActionContext`** — Wraps `action_started` / `action_completed` / `action_failed`. Uses `contextvars.ContextVar` for automatic `parent_action_id` linking in nested actions.

**`Transport`** — Fire-and-forget event delivery. Never raises exceptions to the caller (same safety invariant as the integration layer). Groups events by agent for per-agent batches. Retries 5xx errors with exponential backoff (1s → 60s cap, 5 attempts). Drops batches on 400 (permanent failure). Respects `Retry-After` on 429.

### Claude Agent SDK Integration

`hiveloop.integrations.claude_agent_sdk` auto-instruments Claude Agent SDK agents:

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks

hooks = hiveloop_hooks(api_key="hb_live_xxx", project="my-project")

async for message in query(
    prompt="Fix the bug",
    options=ClaudeAgentOptions(hooks=hooks),
):
    print(message)
```

Maps all 5 Agent SDK hooks to HiveLoop sensors:

| Hook | HiveLoop Events |
|---|---|
| `SessionStart` | `agent_registered` + `task_started` |
| `PreToolUse` | `action_started`, or subagent spawn, or `approval_requested` |
| `PostToolUse` | `action_completed/failed`, subagent close, `approval_received` |
| `Stop` | `task_completed` with result capture |
| `SessionEnd` | Safety net: orphan cleanup + flush |

Each `hiveloop_hooks()` call returns an isolated `HooksResult` with its own `_SessionState` — safe for concurrent sessions. The `_safe_hook` wrapper ensures the integration never crashes the host agent.

---

## Backend Architecture

### Ingestion Pipeline (10 Steps)

Every `POST /v1/ingest` batch goes through:

1. **Auth** — API key SHA-256 hash lookup via middleware
2. **Batch validation** — max 500 events, max 1 MB
3. **Per-event validation** — required fields, size limits (agent_id ≤ 256, payload ≤ 32 KB)
4. **Payload convention check** — advisory warnings for malformed well-known payload kinds
5. **Envelope expansion** — fill environment/group/severity from envelope + auto-defaults; resolve project (slug fallback, auto-create up to 50); estimate LLM cost if missing
6. **Store events** — dedup insert into per-agent partition files
7. **Update caches** — upsert agent record + project-agent junction
8. **Update aggregates** — increment `agent_hourly`, `model_hourly`, `task_runs` buckets
9. **WebSocket broadcast** — push events to subscribed clients; detect and broadcast agent status changes
10. **Evaluate alerts** — check all enabled rules against the new batch

### Pre-Aggregation Strategy

Raw event scans are expensive. HiveBoard pre-computes three aggregate tables, updated incrementally on each ingested batch:

**`agent_hourly`** — keyed by `(tenant_id, agent_id, hour)`:
- Task counts: started, completed, failed, by type
- Action counts: started, completed, failed, by name with durations
- LLM metrics: call count, tokens in/out, cost, by model
- Error breakdown: by type, category, task type, action
- Workflow: retries, escalations, approvals, issues

**`model_hourly`** — keyed by `(tenant_id, model, hour)`:
- Call count, tokens in/out, cost, by agent breakdown

**`task_runs`** — keyed by `(tenant_id, task_id, task_run_id)`:
- Pre-computed `derived_status`, duration, event/action/llm/error counts
- Cost and token totals, boolean flags (has_plan, has_escalation, etc.)

The Insights endpoints (`/v1/insights/*`) query only these pre-computed buckets — no event scans needed.

### Agent Status Derivation

A single `derive_agent_status()` function is the canonical source of truth for agent status. Used by the API, alerting engine, and WebSocket broadcasts. Five-level priority cascade:

1. **stuck** — `max(last_heartbeat, last_seen)` older than `stuck_threshold_seconds` (default 300s)
2. **error** — `last_event_type` is `task_failed` or `action_failed`
3. **waiting_approval** — `last_event_type` is `approval_requested`
4. **processing** — `last_event_type` is `task_started` or `action_started`
5. **idle** — everything else

### Retention and Pruning

A background task runs every 5 minutes:

**TTL pruning** — tenant plan determines retention:
- Free: 7 days, Pro: 30 days, Enterprise: 90 days

**Cold pruning** — aggressive removal of high-volume, low-value events:
- `heartbeat`: 10 minutes
- `action_started`: 24 hours
- `action_completed` / `action_failed`: 7 days
- `queue_snapshot` / `scheduled`: 1 hour

**Payload stripping** — `llm_call` events older than 3 days have `prompt_preview` and `response_preview` stripped (tokens/cost/model preserved).

**Aggregate pruning** — hourly buckets older than 90 days removed.

---

## Storage Layer

### JSON File Implementation (MVP)

The MVP uses JSON files with in-memory caching and atomic writes:

- **In-memory**: All tables loaded into `dict[str, list[dict]]` on startup
- **Write-through**: Every mutation persists immediately via `tmp` → `os.replace()` (crash-safe)
- **Locking**: `asyncio.Lock` per table prevents concurrent corruption
- **Event partitioning**: Events split into `data/events/{agent_id}.json` — per-agent queries only load one file

### StorageBackend Protocol

`shared/storage.py` defines a `@runtime_checkable` Protocol with 35 async methods. Every parameter maps directly to a SQL `WHERE` clause — no opaque filter objects.

```python
class StorageBackend(Protocol):
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...

    # Tenants, API Keys, Users, Projects, Agents
    async def create_tenant(self, tenant: TenantRecord) -> None: ...
    async def authenticate(self, key_hash: str) -> ApiKeyInfo | None: ...
    async def list_agents(self, tenant_id: str, *, project_id=None, ...) -> list[AgentRecord]: ...

    # Events and Tasks
    async def insert_events(self, events: list[Event], key_type: str | None = None) -> int: ...
    async def get_events(self, tenant_id: str, *, agent_id=None, event_type=None, ...) -> list[Event]: ...
    async def list_tasks(self, tenant_id: str, *, agent_id=None, status=None, ...) -> Page[TaskSummary]: ...

    # Metrics, Cost, Pipeline, Alerts, Invites ...
```

### Migration Path to SQL Server

The Protocol is the migration boundary. A future `storage_mssql.py` implements the same interface:

- Same test suite runs against both implementations
- Every method signature is already SQL-friendly (explicit params → `WHERE` clauses)
- Target: MS SQL Server (not SQLite/PostgreSQL)
- Per-agent event partitioning maps naturally to a `WHERE agent_id = ?` index
- Pre-aggregate tables (`agent_hourly`, `model_hourly`, `task_runs`) map to SQL tables directly

---

## Authentication and Multi-Tenancy

### Two Auth Paths

| Path | Token Format | Use Case |
|---|---|---|
| **API Key** | `Bearer hb_{type}_{32hex}` | SDK ingestion, programmatic access |
| **JWT** | `Bearer {hs256_token}` | Dashboard users, role-based access |

**API key types**: `live` (read/write), `test` (read/write, test environment), `read` (read-only).

Raw keys are never stored — only SHA-256 hashes. Keys are generated as `hb_{type}_{32_random_hex}`.

### Roles

`owner > admin > member > viewer`

- **owner** — full access, manage billing, invite any role
- **admin** — manage users/keys/projects, invite member/viewer
- **member** — read/write, create own API keys
- **viewer** — read-only, can only create read-only keys

### Registration and Invites

1. **Self-registration**: `POST /v1/auth/register` → creates tenant + owner + default project + live API key
2. **Team invites**: Owner/admin creates invite → token returned → invitee accepts with `POST /v1/auth/accept-invite` → user created in tenant
3. Invite tokens: SHA-256 hashed, 7-day expiry, single-use

---

## Real-Time Streaming

WebSocket endpoint at `ws://host/v1/stream?token={api_key}`:

### Subscription Model

Clients subscribe to channels with optional filters:

```json
{
  "action": "subscribe",
  "channels": ["events", "agents"],
  "filters": {
    "project_id": "my-project",
    "event_types": ["task_completed", "task_failed"],
    "min_severity": "warn"
  }
}
```

### Message Types

| Type | Channel | Trigger |
|---|---|---|
| `event.new` | events | New events ingested matching subscription filters |
| `agent.status_changed` | agents | `derive_agent_status()` returns a new value |
| `agent.stuck` | agents | Agent detected as stuck (fire-once per episode) |
| `pong` | — | Response to client ping |

### Connection Management

- Max 5 connections per API key
- Server pings every 30 seconds; closes after 3 missed pongs
- Production mode: AWS API Gateway WebSocket bridge (`ws_bridge.py`)

---

## Alert Engine

Six condition evaluators, checked at the end of every ingest:

| Condition | What it checks |
|---|---|
| `agent_stuck` | `derive_agent_status()` returns `stuck` for any agent |
| `task_failed` | Batch contains a `task_failed` event |
| `error_rate` | Failed / total actions in a time window exceeds threshold % |
| `duration_exceeded` | `task_completed` with `duration_ms > threshold_ms` |
| `heartbeat_missing` | Agent's `last_heartbeat` older than threshold |
| `cost_threshold` | Cost summary in time range exceeds threshold USD |

Rules have configurable cooldowns to prevent alert storms. Alert history is persisted with trigger details and action records.

---

## API Surface

### Ingest
| Method | Path |
|---|---|
| `POST` | `/v1/ingest` |

### Agents
| Method | Path |
|---|---|
| `GET` | `/v1/agents` |
| `GET` | `/v1/agents/{agent_id}` |
| `DELETE` | `/v1/agents/{agent_id}` |
| `GET` | `/v1/agents/{agent_id}/pipeline` |

### Tasks
| Method | Path |
|---|---|
| `GET` | `/v1/tasks` |
| `GET` | `/v1/tasks/{task_id}/timeline` |

### Events
| Method | Path |
|---|---|
| `GET` | `/v1/events` |

### Metrics and Cost
| Method | Path |
|---|---|
| `GET` | `/v1/metrics` |
| `GET` | `/v1/cost` |
| `GET` | `/v1/cost/calls` |
| `GET` | `/v1/cost/timeseries` |
| `GET` | `/v1/llm-calls` |

### Insights (Pre-Aggregated)
| Method | Path |
|---|---|
| `GET` | `/v1/insights/agents` |
| `GET` | `/v1/insights/models` |
| `GET` | `/v1/insights/timeseries` |
| `GET` | `/v1/insights/errors` |
| `GET` | `/v1/insights/prompts` |
| `GET` | `/v1/insights/actions` |

### Pipeline
| Method | Path |
|---|---|
| `GET` | `/v1/pipeline` |

### Projects
| Method | Path |
|---|---|
| `GET/POST` | `/v1/projects` |
| `GET/PUT/DELETE` | `/v1/projects/{id}` |
| `POST` | `/v1/projects/{id}/archive` |
| `POST` | `/v1/projects/{id}/unarchive` |
| `POST` | `/v1/projects/{id}/merge` |
| `GET/POST` | `/v1/projects/{id}/agents` |
| `DELETE` | `/v1/projects/{id}/agents/{agent_id}` |

### Alerts
| Method | Path |
|---|---|
| `GET/POST` | `/v1/alerts/rules` |
| `PUT/DELETE` | `/v1/alerts/rules/{id}` |
| `GET` | `/v1/alerts/history` |

### Auth and Users
| Method | Path |
|---|---|
| `POST` | `/v1/auth/login` |
| `POST` | `/v1/auth/register` |
| `GET` | `/v1/auth/check-slug` |
| `POST` | `/v1/auth/accept-invite` |
| `POST` | `/v1/auth/invite` |
| `POST` | `/v1/auth/change-password` |
| `POST` | `/v1/auth/reset-password/{user_id}` |
| `GET/POST` | `/v1/users` |
| `GET` | `/v1/users/me` |
| `GET/PUT/DELETE` | `/v1/users/{id}` |
| `POST` | `/v1/users/{id}/reactivate` |

### API Keys and Invites
| Method | Path |
|---|---|
| `GET/POST` | `/v1/api-keys` |
| `DELETE` | `/v1/api-keys/{id}` |
| `GET` | `/v1/invites` |
| `DELETE` | `/v1/invites/{id}` |

### Admin
| Method | Path |
|---|---|
| `POST` | `/v1/admin/rebuild-aggregates` |
| `POST` | `/v1/admin/rebuild-task-runs` |
| `GET/POST` | `/v1/admin/pricing` |
| `PUT/DELETE` | `/v1/admin/pricing/{pattern}` |

### WebSocket
| Protocol | Path |
|---|---|
| `WS` | `/v1/stream?token={api_key}` |

---

## Key Design Principles

1. **Events are the source of truth.** Everything — agent status, task summaries, metrics, costs, pipelines — is derived from the events table. No separate primary data stores.

2. **Derive, don't duplicate.** Agent status is computed at query time from the agent cache record. Task summaries come from pre-computed `task_runs`. Metrics from pre-aggregated hourly buckets. One derivation function, one source of truth.

3. **SQL-friendly signatures.** Every `StorageBackend` method parameter maps to a SQL `WHERE` clause. No opaque filter dicts. This makes the JSON-to-SQL migration mechanical.

4. **Never crash the host.** The SDK transport and all integration hooks swallow exceptions. Observability must never interfere with the agent's primary mission.

5. **Pre-aggregate for performance.** High-frequency queries (Insights, agent list with stats) read pre-computed hourly buckets updated incrementally at ingest time. Raw event scans reserved for detailed views.

6. **Prune aggressively.** Heartbeats live 10 minutes. Action events live hours to days. Only task lifecycle events and aggregates persist long-term. This keeps storage bounded without losing analytical value.

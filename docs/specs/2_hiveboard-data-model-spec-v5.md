# HiveBoard — Data Model Specification

**CONFIDENTIAL** | February 2026 | v4.0

---

**Changes from v3:** This version resolves the two minor gaps identified in the gap analysis between v3.0 and the v2 Addendum: (1) explicit `group_by` support (including `group_by=model`) added to `GET /v1/metrics` endpoint (Section 10.2), and (2) issue deduplication formally specified as SHA256-hash-based when `issue_id` is absent (Section 5.12.4).

**Changes from v2:** This version incorporates all data model impacts from the v2 Addendum (Gap Analysis / loopCore observability review). No structural schema changes to the events table. The additions are: (1) formalized payload conventions for all well-known event kinds (llm_call, plan_created, plan_step, reflection, issue, queue_snapshot, todo, scheduled, heartbeat_status), (2) Cost Explorer queries, (3) plan progress queries, (4) work pipeline derived state queries (queue, TODO aggregation, scheduled, issues), (5) agent-level custom event support, (6) smarter heartbeat compaction, (7) new PostgreSQL indexes for payload queries, (8) a new alert condition type, (9) updated continuous aggregates with cost-by-model dimensions, (10) full SDK convenience methods for all v2 features, and (11) pipeline API endpoint.

---

## 1. Overview

This document defines the complete data model for HiveBoard: every table, column, index, constraint, and query pattern. It is the blueprint for building the database layer.

### 1.1 Organizational Hierarchy

```
Tenant (workspace)
  ├── Projects
  │     ├── Tasks (each task belongs to exactly one project)
  │     └── Events (inherit project_id from their task)
  │
  ├── Agents (exist at the tenant level)
  │     ├── Can be assigned to multiple projects
  │     ├── Agent-level events (heartbeats) have no project scope
  │     └── Agent-level custom events have no task or project scope
  │
  └── API Keys (tenant-scoped, shared across projects)
```

**Key design decision:** Project context lives on the **task**, not the agent. When a developer starts a task, they specify which project it belongs to. The agent is a shared resource that can serve multiple projects. Agent-level events (heartbeats, registration, and agent-scoped custom events) are project-agnostic — they appear in all projects the agent participates in.

### 1.2 Governing Principles

1. **Events are the single source of truth.** There is one events table. Dashboards, timelines, metrics, and alerts are all derived from it.

2. **Agent profiles are a convenience cache.** The `agents` table exists to make fleet queries fast. It is always rebuildable from the events table.

3. **Multi-tenancy is structural, not optional.** Every table has `tenant_id` as the leading column in its primary key or a required foreign key. Every index leads with `tenant_id`. There is no query that crosses tenant boundaries.

4. **Projects are an organizational lens, not a security boundary.** Tenant-level API keys access all projects. Projects organize work; tenants enforce isolation.

5. **SQLite for MVP, PostgreSQL for production.** The schema is written in portable SQL that works on both. Production-specific extensions are called out separately.

6. **Indexes are designed from queries, not from intuition.** Every index maps to a specific API endpoint or dashboard query.

7. **Payload conventions are contracts, not schema.** Well-known payload shapes (LLM calls, plan steps, issues) are documented as conventions. The events table stores them as opaque JSON; the dashboard and query layer interpret them. This keeps the schema stable while allowing rich domain-specific data.

### 1.3 Cross-References

| Document | What It Defines | What This Document Adds |
|---|---|---|
| Event Schema Spec v1 | Canonical event shape, field types, size limits | Physical storage, column types, constraints |
| API + SDK Spec v1 | Endpoints and query parameters | Exact SQL queries, index coverage |
| Product Spec v1 | Dashboard screens and metrics | Aggregation queries, derived state logic |
| v2 Addendum | 5 changes: LLM calls, work pipeline, plan-aware timeline, issues, rich heartbeats | Payload conventions, pipeline queries, cost queries, SDK methods, API endpoints — all changes from the addendum are implemented in this document |

---

## 2. Entity Relationship Diagram

```
┌──────────────┐
│   tenants    │
└──────┬───────┘
       │ 1
       │
       ├──────────────────────┬──────────────────────┐
       │ N                    │ N                     │ N
┌──────▼───────┐     ┌───────▼────────┐     ┌───────▼────────┐
│   api_keys   │     │   projects     │     │    agents      │
└──────────────┘     └───────┬────────┘     └───────┬────────┘
                             │                      │
                             │ M ──────────── N     │
                             │    project_agents    │
                             │                      │
                             │                      │
                      ┌──────▼──────────────────────▼──┐
                      │            events               │
                      │  (task events have project_id)  │
                      │  (agent events have no project) │
                      └────────────────────────────────┘

                      ┌──────────────┐
                      │ alert_rules  │
                      └──────┬───────┘
                             │
                      ┌──────▼───────┐
                      │alert_history │
                      └──────────────┘
```

**Relationship summary:**

| Relationship | Type | Enforced By |
|---|---|---|
| Tenant → API Keys | 1:N | FK |
| Tenant → Projects | 1:N | FK |
| Tenant → Agents | 1:N | FK |
| Project ↔ Agent | M:N | Junction table `project_agents` |
| Project → Tasks (via events) | 1:N | `project_id` on task events |
| Agent → Events | 1:N | `agent_id` on event |

---

## 3. Table Definitions

### 3.1 `tenants`

The workspace/organization. Top-level isolation boundary. Created on signup.

```sql
CREATE TABLE tenants (
    tenant_id       TEXT        PRIMARY KEY,
    name            TEXT        NOT NULL,
    slug            TEXT        NOT NULL UNIQUE,
    plan            TEXT        NOT NULL DEFAULT 'free',
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    settings        TEXT        NOT NULL DEFAULT '{}'
);
```

| Column | Type | Description |
|---|---|---|
| `tenant_id` | TEXT (UUID) | Primary key. Generated server-side on signup. |
| `name` | TEXT | Display name: "Acme AI Ops". |
| `slug` | TEXT | URL-safe identifier: "acme-ai-ops". Unique globally. |
| `plan` | TEXT | Subscription tier: `free`, `pro`, `enterprise`. |
| `created_at` | TIMESTAMP | When the workspace was created. |
| `updated_at` | TIMESTAMP | Last modification time. |
| `settings` | TEXT (JSON) | Workspace-level config: `default_stuck_threshold_seconds`, `retention_days`, `timezone`. |

**Plan limits (enforced in application layer):**

| Plan | Events/month | Agents | Projects | Retention | API rate |
|---|---|---|---|---|---|
| `free` | 500,000 | 5 | 3 | 7 days | 10 req/s |
| `pro` | 10,000,000 | 50 | 20 | 30 days | 100 req/s |
| `enterprise` | Unlimited | Unlimited | Unlimited | 90 days | 500 req/s |

---

### 3.2 `api_keys`

Authentication tokens. Scoped to the tenant (not to a project). Each key carries a type that determines permissions.

```sql
CREATE TABLE api_keys (
    key_id          TEXT        PRIMARY KEY,
    tenant_id       TEXT        NOT NULL REFERENCES tenants(tenant_id),
    key_hash        TEXT        NOT NULL,
    key_prefix      TEXT        NOT NULL,
    key_type        TEXT        NOT NULL CHECK (key_type IN ('live', 'test', 'read')),
    label           TEXT,
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at    TIMESTAMP,
    revoked_at      TIMESTAMP,
    is_active       INTEGER     NOT NULL DEFAULT 1
);

CREATE INDEX idx_api_keys_hash ON api_keys(key_hash) WHERE is_active = 1;
CREATE INDEX idx_api_keys_tenant ON api_keys(tenant_id);
```

| Column | Type | Description |
|---|---|---|
| `key_id` | TEXT (UUID) | Internal identifier. Never exposed to user. |
| `tenant_id` | TEXT | FK to tenants. The tenant this key authenticates as. |
| `key_hash` | TEXT | SHA-256 hash of the full API key. Plaintext shown once on creation, never stored. |
| `key_prefix` | TEXT | First 12 chars of the key (e.g., `hb_live_a1b2`). For UI display. |
| `key_type` | TEXT | `live` (read/write, production data), `test` (read/write, isolated namespace), `read` (read-only). |
| `label` | TEXT | User-assigned label: "Production SDK", "CI/CD Reporter". |
| `created_at` | TIMESTAMP | Creation time. |
| `last_used_at` | TIMESTAMP | Updated on each authenticated request. |
| `revoked_at` | TIMESTAMP | When the key was revoked. Null if active. |
| `is_active` | INTEGER | 0 = revoked. Filtered in the lookup index. |

**Authentication flow:**

```
1. Extract key from Authorization header
2. Hash key with SHA-256
3. SELECT tenant_id, key_type FROM api_keys WHERE key_hash = ? AND is_active = 1
4. If no row → 401
5. If key_type = 'read' and request is a write → 403
6. Set tenant_id on request context (project access determined per-request)
7. UPDATE api_keys SET last_used_at = NOW() WHERE key_id = ?  (async, non-blocking)
```

---

### 3.3 `projects`

Organizational grouping within a tenant. Tasks belong to a project. Agents can span projects.

```sql
CREATE TABLE projects (
    project_id      TEXT        NOT NULL,
    tenant_id       TEXT        NOT NULL REFERENCES tenants(tenant_id),
    name            TEXT        NOT NULL,
    slug            TEXT        NOT NULL,
    description     TEXT,
    environment     TEXT        NOT NULL DEFAULT 'production',
    settings        TEXT        NOT NULL DEFAULT '{}',
    is_archived     INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (tenant_id, project_id),
    UNIQUE (tenant_id, slug)
);
```

| Column | Type | Description |
|---|---|---|
| `project_id` | TEXT (UUID) | Unique within tenant. |
| `tenant_id` | TEXT | FK to tenants. Part of composite PK. |
| `name` | TEXT | Display name: "Sales Pipeline", "Customer Support". |
| `slug` | TEXT | URL-safe identifier: "sales-pipeline". Unique within tenant. |
| `description` | TEXT | Optional description. |
| `environment` | TEXT | Default environment for tasks in this project. Can be overridden per-task. Values: `production`, `staging`, `development`, `test`. |
| `settings` | TEXT (JSON) | Project-level overrides: `{"stuck_threshold_seconds": 180, "default_alert_channels": [...]}`. |
| `is_archived` | INTEGER | 0 = active, 1 = archived. Archived projects are hidden from the dashboard but data is retained. |
| `created_at` | TIMESTAMP | Creation time. |
| `updated_at` | TIMESTAMP | Last modification. |

**Default project:** Every tenant gets a `default` project created on signup. Tasks that don't specify a project go here. This ensures backward compatibility and lets Layer 0 users never think about projects.

```sql
-- Created automatically on tenant signup
INSERT INTO projects (project_id, tenant_id, name, slug, description)
VALUES ('proj_default', ?, 'Default', 'default', 'Default project for unassigned tasks');
```

---

### 3.4 `agents`

Agents exist at the tenant level, not within a project. An agent is a long-running worker identity; projects are how you organize its work.

```sql
CREATE TABLE agents (
    agent_id                TEXT        NOT NULL,
    tenant_id               TEXT        NOT NULL REFERENCES tenants(tenant_id),
    agent_type              TEXT        NOT NULL DEFAULT 'general',
    agent_version           TEXT,
    framework               TEXT        DEFAULT 'custom',
    runtime                 TEXT,
    first_seen              TIMESTAMP   NOT NULL,
    last_seen               TIMESTAMP   NOT NULL,
    last_heartbeat          TIMESTAMP,
    last_event_type         TEXT,
    last_task_id            TEXT,
    last_project_id         TEXT,
    stuck_threshold_seconds INTEGER     NOT NULL DEFAULT 300,
    is_registered           INTEGER     NOT NULL DEFAULT 1,

    PRIMARY KEY (tenant_id, agent_id)
);

CREATE INDEX idx_agents_tenant ON agents(tenant_id, last_heartbeat);
```

| Column | Type | Updated When | Description |
|---|---|---|---|
| `agent_id` | TEXT | Set on creation | Agent identifier slug. Max 256 chars. |
| `tenant_id` | TEXT | Set on creation | FK to tenants. Part of composite PK. |
| `agent_type` | TEXT | Every ingest (from envelope) | Agent classification: "sales", "support", "coding". |
| `agent_version` | TEXT | Every ingest | Last known version. |
| `framework` | TEXT | Every ingest | Framework name. |
| `runtime` | TEXT | Every ingest | Runtime identifier. |
| `first_seen` | TIMESTAMP | Set once | Timestamp of first `agent_registered` event. |
| `last_seen` | TIMESTAMP | Every ingest | Most recent event from this agent. |
| `last_heartbeat` | TIMESTAMP | On heartbeat events | Most recent heartbeat. Used for stuck detection. |
| `last_event_type` | TEXT | Every ingest | Most recent event type. For quick status derivation. |
| `last_task_id` | TEXT | On task events | Current or most recent task. |
| `last_project_id` | TEXT | On task events | Project of the current/most recent task. |
| `stuck_threshold_seconds` | INTEGER | On agent_registered | Per-agent stuck threshold. |
| `is_registered` | INTEGER | On first agent_registered | Formal registration flag. |

**Why `last_project_id` is on the agents table:** The Hive shows each agent's current status. When an agent is processing a task, we want to show which project that task belongs to without joining the events table. This is a cache field, not a source of truth.

---

### 3.5 `project_agents`

Many-to-many junction between projects and agents. An agent can serve multiple projects. A project can have multiple agents.

```sql
CREATE TABLE project_agents (
    tenant_id       TEXT        NOT NULL,
    project_id      TEXT        NOT NULL,
    agent_id        TEXT        NOT NULL,
    added_at        TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    role            TEXT        NOT NULL DEFAULT 'member',

    PRIMARY KEY (tenant_id, project_id, agent_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES projects(tenant_id, project_id),
    FOREIGN KEY (tenant_id, agent_id) REFERENCES agents(tenant_id, agent_id)
);

CREATE INDEX idx_project_agents_by_agent
    ON project_agents(tenant_id, agent_id);
```

| Column | Type | Description |
|---|---|---|
| `tenant_id` | TEXT | Tenant isolation. Part of composite PK and both FKs. |
| `project_id` | TEXT | FK to projects. |
| `agent_id` | TEXT | FK to agents. |
| `added_at` | TIMESTAMP | When the agent was added to this project. |
| `role` | TEXT | Agent's role in the project. Default: `"member"`. Reserved for future use (e.g., `"primary"`, `"backup"`). |

**Auto-population:** When the ingestion pipeline receives a task event with a `project_id`, it checks whether a `project_agents` row exists for that `(tenant_id, project_id, agent_id)` tuple. If not, it creates one automatically. This means the junction table is populated organically from real task execution — developers don't need to manually assign agents to projects.

```sql
INSERT OR IGNORE INTO project_agents (tenant_id, project_id, agent_id)
VALUES (?, ?, ?);
```

**Querying agents for a project:**

```sql
SELECT a.*
FROM agents a
JOIN project_agents pa ON a.tenant_id = pa.tenant_id AND a.agent_id = pa.agent_id
WHERE pa.tenant_id = ? AND pa.project_id = ?;
```

**Querying projects for an agent:**

```sql
SELECT p.*
FROM projects p
JOIN project_agents pa ON p.tenant_id = pa.tenant_id AND p.project_id = pa.project_id
WHERE pa.tenant_id = ? AND pa.agent_id = ?;
```

---

### 3.6 `events`

The core table. Every heartbeat, status change, action, error, and custom event lives here. Single source of truth for the entire system.

**Changes from v2:** No structural changes. Clarifications added for agent-level custom events and payload conventions (Section 4).

```sql
CREATE TABLE events (
    -- Identity
    event_id            TEXT        NOT NULL,
    tenant_id           TEXT        NOT NULL,
    agent_id            TEXT        NOT NULL,
    agent_type          TEXT,

    -- Project context (null for agent-level events)
    project_id          TEXT,

    -- Time
    timestamp           TIMESTAMP   NOT NULL,
    received_at         TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Grouping
    environment         TEXT        NOT NULL DEFAULT 'production',
    "group"             TEXT        NOT NULL DEFAULT 'default',

    -- Task context
    task_id             TEXT,
    task_type           TEXT,
    task_run_id         TEXT,
    correlation_id      TEXT,

    -- Action nesting
    action_id           TEXT,
    parent_action_id    TEXT,

    -- Classification
    event_type          TEXT        NOT NULL,
    severity            TEXT        NOT NULL DEFAULT 'info',

    -- Outcome
    status              TEXT,
    duration_ms         INTEGER,

    -- Causal linkage
    parent_event_id     TEXT,

    -- Content
    payload             TEXT,

    -- Constraints
    PRIMARY KEY (tenant_id, event_id),

    CHECK (event_type IN (
        'agent_registered', 'heartbeat',
        'task_started', 'task_completed', 'task_failed',
        'action_started', 'action_completed', 'action_failed',
        'retry_started', 'escalated',
        'approval_requested', 'approval_received',
        'custom'
    )),
    CHECK (severity IN ('debug', 'info', 'warn', 'error')),
    CHECK (status IS NULL OR status IN ('success', 'failure', 'timeout', 'escalated', 'cancelled'))
);
```

**`project_id` and `task_id` population rules:**

| Event Type | `project_id` Value | `task_id` Value |
|---|---|---|
| `agent_registered` | NULL (agent-level) | NULL |
| `heartbeat` | NULL (agent-level) | NULL |
| `task_started` | From SDK — developer specifies on `agent.task()` | Set by developer |
| `task_completed`, `task_failed` | Inherited from `task_started` | Inherited |
| `action_started`, `action_completed`, `action_failed` | Inherited from active task context | Inherited |
| `retry_started`, `escalated` | Inherited from active task context | Inherited |
| `approval_requested`, `approval_received` | Inherited from active task context | Inherited |
| `custom` (within task context) | Inherited from active task context | Inherited |
| `custom` (via `agent.event()`) | NULL (agent-level) | NULL |

**The SDK handles inheritance automatically.** When `agent.task("task_123", project="sales-pipeline")` is called, all events within that task context carry `project_id = "sales-pipeline"`. The developer never sets `project_id` on individual events.

**Agent-level custom events (new in v3):** The `custom` event type can be emitted outside a task context via `agent.event()`. These events have `task_id = NULL` and `project_id = NULL`. They represent agent-level observations (queue status, self-reported issues, configuration changes) that aren't tied to any specific task. In the Activity Stream, they appear alongside heartbeats and registration events — visible in all projects the agent participates in.

---

### 3.7 `events` — Indexes

Every index serves a specific query. All indexes lead with `tenant_id`.

```sql
-- INDEX 1: The Hive — agent status (uses agents cache table, not this)
-- Kept for verification/rebuild from events
CREATE INDEX idx_events_agent_latest
    ON events(tenant_id, agent_id, timestamp DESC);

-- INDEX 2: Task Timeline — all events for a specific task
-- Used by: GET /v1/tasks/{task_id}/timeline
CREATE INDEX idx_events_task_timeline
    ON events(tenant_id, task_id, timestamp ASC)
    WHERE task_id IS NOT NULL;

-- INDEX 3: Activity Stream — recent events, optionally by project
-- Used by: GET /v1/events
CREATE INDEX idx_events_stream
    ON events(tenant_id, project_id, timestamp DESC, event_type, severity);

-- INDEX 4: Heartbeat lookup — stuck detection
-- Used by: Background stuck detection job, GET /v1/agents
CREATE INDEX idx_events_heartbeat
    ON events(tenant_id, agent_id, timestamp DESC)
    WHERE event_type = 'heartbeat';

-- INDEX 5: Task listing — tasks by project, by agent, by time
-- Used by: GET /v1/tasks
CREATE INDEX idx_events_tasks
    ON events(tenant_id, project_id, agent_id, timestamp DESC)
    WHERE event_type IN ('task_started', 'task_completed', 'task_failed');

-- INDEX 6: Metrics aggregation — counts by type in time buckets
-- Used by: GET /v1/metrics
CREATE INDEX idx_events_metrics
    ON events(tenant_id, project_id, environment, timestamp, event_type);

-- INDEX 7: Custom events by kind — for Cost Explorer and plan queries
-- Used by: GET /v1/cost, GET /v1/tasks/{task_id}/timeline (plan overlay)
-- Note: SQLite cannot index into JSON; this partial index narrows the scan.
-- PostgreSQL uses JSONB GIN indexes instead (Section 8.4).
CREATE INDEX idx_events_custom
    ON events(tenant_id, agent_id, timestamp DESC)
    WHERE event_type = 'custom';

-- INDEX 8: Deduplication — enforced by composite PK
-- (tenant_id, event_id) — INSERT OR IGNORE uses this
```

**Index design notes:**

- Index 3 (Activity Stream) includes `project_id` as the second column. When the dashboard is filtered to a specific project, this index covers the query without scanning unrelated events.
- Index 5 (Task listing) includes both `project_id` and `agent_id` to handle the two most common filter patterns: "all tasks in this project" and "all tasks for this agent in this project."
- Index 6 (Metrics) includes `project_id` for project-scoped metrics dashboards.
- Index 7 (Custom events) is new in v3. It accelerates Cost Explorer queries and plan progress lookups by narrowing to `custom` events before the application layer inspects `payload.kind`. On SQLite this is a partial index scan; on PostgreSQL the JSONB GIN indexes (Section 8.4) provide direct kind-level filtering.

---

### 3.8 `alert_rules`

Configuration for alert conditions. Now with optional project scoping.

**Changes from v2:** Added `cost_threshold` to the `condition_type` enum. This enables alerts when cumulative LLM cost exceeds a configured amount within a time window.

```sql
CREATE TABLE alert_rules (
    rule_id             TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL REFERENCES tenants(tenant_id),
    project_id          TEXT,
    name                TEXT        NOT NULL,
    condition_type      TEXT        NOT NULL,
    condition_config    TEXT        NOT NULL DEFAULT '{}',
    filters             TEXT        NOT NULL DEFAULT '{}',
    actions             TEXT        NOT NULL DEFAULT '[]',
    cooldown_seconds    INTEGER     NOT NULL DEFAULT 300,
    is_enabled          INTEGER     NOT NULL DEFAULT 1,
    created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (condition_type IN (
        'agent_stuck', 'task_failed', 'error_rate',
        'duration_exceeded', 'heartbeat_lost',
        'cost_threshold'
    ))
);

CREATE INDEX idx_alert_rules_tenant
    ON alert_rules(tenant_id, project_id) WHERE is_enabled = 1;
```

| Column | Type | Description |
|---|---|---|
| `project_id` | TEXT | Nullable. If set, alert fires only for events in this project. If null, fires for all projects in the tenant. |

**`cost_threshold` condition config (new in v3):**

```json
{
    "threshold_usd": 50.00,
    "window_hours": 24,
    "scope": "agent",
    "agent_id": "lead-qualifier"
}
```

| Field | Type | Description |
|---|---|---|
| `threshold_usd` | number | Cost threshold in USD. Alert fires when cumulative cost exceeds this. |
| `window_hours` | number | Rolling time window in hours. |
| `scope` | string | `"agent"` (per-agent), `"project"` (all agents in project), `"tenant"` (all agents). |
| `agent_id` | string | Required when `scope = "agent"`. |

The alert evaluator sums `json_extract(payload, '$.data.cost')` from `custom` events with `json_extract(payload, '$.kind') = 'llm_call'` within the time window. This query uses Index 7 (custom events) on SQLite and the JSONB GIN indexes on PostgreSQL.

All other columns identical to v2.

---

### 3.9 `alert_history`

Record of every alert firing.

```sql
CREATE TABLE alert_history (
    alert_id            TEXT        PRIMARY KEY,
    tenant_id           TEXT        NOT NULL,
    rule_id             TEXT        NOT NULL REFERENCES alert_rules(rule_id),
    project_id          TEXT,
    fired_at            TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
    condition_snapshot  TEXT        NOT NULL DEFAULT '{}',
    actions_taken       TEXT        NOT NULL DEFAULT '[]',
    related_agent_id    TEXT,
    related_task_id     TEXT
);

CREATE INDEX idx_alert_history_tenant
    ON alert_history(tenant_id, project_id, fired_at DESC);

CREATE INDEX idx_alert_history_cooldown
    ON alert_history(rule_id, fired_at DESC);
```

---

## 4. Payload Conventions

**New in v3.** The `payload` column on the `events` table is a free-form JSON field (TEXT in SQLite, JSONB in PostgreSQL) limited to 32 KB. While any JSON is valid, certain payload shapes are **well-known** — the dashboard and query layer recognize them and render specialized views.

This section defines those conventions. They are contracts between the SDK (producer) and the dashboard/query layer (consumer). No schema enforcement — the events table stores them as opaque JSON. Validation is in the application layer.

### 4.1 Canonical Payload Structure

All well-known payloads follow this envelope:

```json
{
    "kind": "<well-known-kind>",
    "summary": "Human-readable one-liner for the activity stream",
    "data": { },
    "tags": ["optional", "string", "tags"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | string | Yes (for well-known payloads) | Identifies the payload type. The dashboard uses this to select rendering logic. |
| `summary` | string | Yes | One-line human-readable description. Displayed in the Activity Stream and Timeline nodes. Max 256 chars. |
| `data` | object | Yes | Kind-specific structured data. Shape varies by `kind`. |
| `tags` | string[] | No | Free-form tags for filtering. Max 10 tags, each max 64 chars. |

Events with unrecognized `kind` values or no `kind` at all are rendered as generic custom events (summary + raw JSON expandable).

### 4.2 `kind: "llm_call"` — LLM Call Tracking

**Motivation (Gap 1):** LLM call content and cost data is the single most important observability signal for AI agent systems. This payload shape enables the Cost Explorer (Section 5.9) and per-call detail views.

**Produced by:** `task.llm_call()` SDK convenience method (emits a `custom` event with this payload).

```json
{
    "kind": "llm_call",
    "summary": "phase1_reasoning → claude-sonnet (1500 in / 200 out, $0.003)",
    "data": {
        "name": "phase1_reasoning",
        "model": "claude-sonnet-4-20250514",
        "prompt_preview": "You are analyzing a sales lead...",
        "response_preview": "{\"tool\": \"crm_search\", \"intent\": \"find active deals\"}",
        "tokens_in": 1500,
        "tokens_out": 200,
        "cost": 0.003,
        "duration_ms": 1200,
        "metadata": {
            "caller": "atomic_phase1_turn_3",
            "temperature": 0.7
        }
    },
    "tags": ["llm", "phase1"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | Yes | Developer-assigned call name. Used in timeline labels. |
| `model` | string | Yes | Model identifier. Used for cost-by-model aggregation. |
| `prompt_preview` | string | No | First ~500 chars of the prompt. For debugging, not billing. |
| `response_preview` | string | No | First ~500 chars of the response. For debugging, not billing. |
| `tokens_in` | integer | No | Input token count. Recommended for cost tracking but not required — enables incremental instrumentation (log the call first, add token counts later). |
| `tokens_out` | integer | No | Output token count. Same rationale as `tokens_in`. |
| `cost` | number | No | Pre-calculated cost in USD. The SDK or developer calculates this; HiveBoard does not maintain pricing tables. When absent, the call appears in timelines and call lists but is excluded from cost aggregations. |
| `duration_ms` | integer | No | LLM call latency. Separate from the event-level `duration_ms` which tracks the enclosing action. |
| `metadata` | object | No | Arbitrary key-value pairs. Not indexed, not queried — for display in the call detail view. |

**Size guidance:** `prompt_preview` and `response_preview` should be truncated to ~500 chars each. Full prompts/responses belong in the agent's own logging, not in the observability stream. This keeps payload sizes well under the 32 KB limit.

**Query contract:** The Cost Explorer queries (Section 5.9) extract `data.model`, `data.cost`, `data.tokens_in`, and `data.tokens_out` from events where `json_extract(payload, '$.kind') = 'llm_call'`.

### 4.3 `kind: "plan_step"` — Plan Progress Tracking

**Motivation (Gap 3):** Agents that follow multi-step plans need a way to report plan progress. This payload shape enables a plan progress overlay on the Task Timeline.

**Produced by:** `task.plan_step()` SDK convenience method (emits a `custom` event with this payload). Typically emitted at the start and completion of each plan step.

```json
{
    "kind": "plan_step",
    "summary": "Step 1 completed: Search CRM for active deals",
    "data": {
        "step_index": 0,
        "total_steps": 4,
        "step_description": "Search CRM for active deals",
        "action": "completed",
        "turns": 2,
        "tokens": 3200,
        "plan_revision": 0
    },
    "tags": ["plan"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `step_index` | integer | Yes | Zero-based step index. |
| `total_steps` | integer | Yes | Total number of steps in the plan. |
| `step_description` | string | Yes | Human-readable step description. |
| `action` | string | Yes | `"started"`, `"completed"`, `"failed"`, `"skipped"`. |
| `turns` | integer | No | Number of LLM turns spent on this step. Populated on completion/failure. Enables per-step cost attribution. |
| `tokens` | integer | No | Total tokens spent on this step. Populated on completion/failure. Enables per-step cost attribution. |
| `plan_revision` | integer | No | Plan revision number. `0` for the initial plan, increments on replan. Used to detect mid-execution plan changes. |

**Dashboard rendering:** When the Timeline detects `kind: "plan_step"` events for a task, it renders a plan progress bar above the timeline showing step completion status. Steps are correlated by `step_index`. The latest event per `step_index` determines that step's current `action`.

### 4.4 `kind: "plan_created"` — Plan Establishment

**Motivation (Gap 3):** When an agent creates or revises a multi-step plan, it emits a single event capturing the full plan structure. This provides the dashboard with all step descriptions upfront — the plan progress bar can show pending steps before they emit their first `plan_step` event.

**Produced by:** `task.plan()` SDK convenience method.

```json
{
    "kind": "plan_created",
    "summary": "Plan: Process inbound lead (4 steps)",
    "data": {
        "goal": "Process inbound lead",
        "steps": [
            {"index": 0, "description": "Search CRM for existing record"},
            {"index": 1, "description": "Score lead"},
            {"index": 2, "description": "Send follow-up email"},
            {"index": 3, "description": "Update CRM"}
        ],
        "revision": 0
    },
    "tags": ["plan", "created"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `goal` | string | Yes | High-level plan goal or task description. |
| `steps` | array | Yes | Array of `{"index": <int>, "description": "<string>"}`. Defines the full plan structure. |
| `revision` | integer | No | `0` for the initial plan, increments on each replan. Correlates with `plan_revision` in `plan_step` events. |

**Dashboard rendering:** When the Timeline encounters a `plan_created` event, it initializes the plan progress bar with all step descriptions in "pending" state. Subsequent `plan_step` events update individual step statuses. If a second `plan_created` with a higher `revision` appears, the progress bar resets to reflect the revised plan.

**Relationship to `plan_step`:** `plan_created` defines the structure; `plan_step` reports progress. A task can function with only `plan_step` events (the progress bar builds incrementally), but including `plan_created` gives a complete upfront view of pending work.

### 4.5 `kind: "reflection"` — Agent Reasoning Visibility

**Motivation (Gap Analysis Section 5.6):** Agents that perform reflection (assess progress, decide to adjust or pivot) can surface those decisions.

```json
{
    "kind": "reflection",
    "summary": "Reflection: adjust approach (confidence: 0.6)",
    "data": {
        "decision": "adjust",
        "reasoning": "CRM search returned no results, trying broader query",
        "confidence": 0.6,
        "next_action": "retry_with_broader_query",
        "trigger": "empty_result_set"
    },
    "tags": ["reflection", "adjust"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `decision` | string | Yes | `"continue"`, `"adjust"`, `"pivot"`, `"escalate"`, `"abort"`. |
| `reasoning` | string | Yes | LLM-generated explanation. |
| `confidence` | number | No | 0.0–1.0 confidence in current approach. |
| `next_action` | string | No | What the agent will do next. |
| `trigger` | string | No | What triggered the reflection. |

### 4.6 `kind: "issue"` — Agent Self-Reported Issues

**Motivation (Gap 4):** Agents can flag persistent issues they cannot resolve. These are emitted via `agent.report_issue()` (agent-level `custom` events) or within task context via `escalated` events.

```json
{
    "kind": "issue",
    "summary": "CRM API returning 403 for workspace queries",
    "data": {
        "issue_id": "issue_crm_403",
        "severity": "high",
        "category": "permissions",
        "action": "reported",
        "occurrence_count": 3,
        "context": {
            "tool": "crm_search",
            "error_code": 403,
            "last_success": "2026-02-10T14:00:00Z"
        }
    },
    "tags": ["issue", "permissions"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `issue_id` | string | No | Stable identifier for this issue. If omitted, the server can deduplicate by `summary` hash. When present, enables explicit issue lifecycle tracking across multiple events. |
| `severity` | string | Yes | `"low"`, `"medium"`, `"high"`, `"critical"`. Distinct from event-level `severity` — this is the agent's assessment of the issue's business impact. |
| `category` | string | Yes | Issue category for grouping: `"permissions"`, `"rate_limit"`, `"data_quality"`, `"timeout"`, `"configuration"`, `"other"`. |
| `action` | string | No | Issue lifecycle state: `"reported"` (default), `"resolved"`, `"dismissed"`. Enables the data to support issue lifecycle from day one, even before dedicated UI exists. |
| `occurrence_count` | integer | No | How many times this issue has occurred, as tracked by the agent. Useful for severity assessment — an issue that fires once is different from one that fires 50 times. |
| `context` | object | No | Arbitrary context for debugging. |

**Issue lifecycle:** An issue's current state is determined by the most recent event matching its `issue_id` (or `summary` hash if no `issue_id`). The `action` field tracks transitions:

```
reported → resolved       (agent fixed the problem)
reported → dismissed      (human acknowledged/suppressed)
resolved → reported       (issue recurred)
```

**v1 dashboard scope:** Issues appear as events in the Activity Stream with severity-colored badges. The Agent Detail Pipeline tab shows active issues (most recent `action` is `"reported"`) in a table sorted by severity then recency. Resolved/dismissed issues are collapsed by default.

**v2 scope (future):** Dedicated Issues panel with deduplication UI, persistence, dismiss/acknowledge workflow, occurrence trend charts, and cross-agent issue correlation.

### 4.7 `kind: "queue_snapshot"` — Work Queue State

**Motivation (Gap 2):** Without visibility into pending work, silent failures are invisible. The agent looks idle on the dashboard while items rot in its queue. Queue snapshots reveal what the agent *will do* and what's waiting.

**Produced by:** `agent.queue_snapshot()` SDK convenience method. Emitted periodically (e.g., on a schedule, on significant queue changes, or automatically with every heartbeat via the `queue_provider` callback).

**Scope:** Agent-level (`task_id: null`, `project_id: null`). A queue exists whether or not a task is running.

```json
{
    "kind": "queue_snapshot",
    "summary": "Queue: 4 items, oldest 120s",
    "data": {
        "depth": 4,
        "oldest_age_seconds": 120,
        "items": [
            {
                "id": "evt_001",
                "priority": "high",
                "source": "human",
                "summary": "Review contract draft",
                "queued_at": "2026-02-11T14:28:00Z"
            },
            {
                "id": "evt_002",
                "priority": "normal",
                "source": "webhook",
                "summary": "Process CRM update",
                "queued_at": "2026-02-11T14:29:00Z"
            }
        ],
        "processing": {
            "id": "evt_003",
            "summary": "Sending follow-up email",
            "started_at": "2026-02-11T14:30:00Z",
            "elapsed_ms": 4500
        }
    },
    "tags": ["queue"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `depth` | integer | Yes | Number of items currently in the queue. `0` is valid (queue just drained — useful as a signal). |
| `oldest_age_seconds` | integer | No | Age of the oldest queued item in seconds. Dashboard can highlight stale queues. |
| `items` | array | No | Summary of queued items. Each element: `{id, priority, source, summary, queued_at}`. |
| `processing` | object | No | Currently processing item (if any): `{id, summary, started_at, elapsed_ms}`. |

**`data.items` array element:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Item identifier. |
| `priority` | string | `"high"`, `"normal"`, `"low"`. |
| `source` | string | Where it came from: `"human"`, `"webhook"`, `"heartbeat"`, `"scheduled"`. |
| `summary` | string | Brief description of the work item. |
| `queued_at` | ISO 8601 | When it entered the queue. |

**`data.processing` object:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Item identifier. |
| `summary` | string | What's being processed. |
| `started_at` | ISO 8601 | When processing began. |
| `elapsed_ms` | integer | How long it's been running. |

**Dashboard rendering:** The Hive card shows queue depth as a badge (`Q:4`). If processing, a mini status line: "Processing: Send email (45s)". If queue depth exceeds a configurable threshold (default 10), the card gets an amber border. The Agent Detail Pipeline tab shows the full queue table.

### 4.8 `kind: "todo"` — TODO Item Lifecycle

**Motivation (Gap 2):** Agents encounter work they can't complete immediately — failed actions that need retry, requests that need follow-up, items deferred for later. TODOs make this planned-but-not-yet-done work visible, preventing silent failures.

**Produced by:** `agent.todo()` SDK convenience method. Emitted on each TODO state change.

**Scope:** Agent-level (`task_id: null`, `project_id: null`). TODOs persist across tasks.

```json
{
    "kind": "todo",
    "summary": "TODO created: Retry CRM write (403 Forbidden)",
    "data": {
        "todo_id": "todo_retry_crm",
        "action": "created",
        "priority": "high",
        "source": "failed_action",
        "context": "Tool crm_write returned 403 Forbidden for workspace query",
        "due_by": "2026-02-11T16:00:00Z"
    },
    "tags": ["todo", "created"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `todo_id` | string | Yes | Stable identifier for this TODO item. Used for lifecycle tracking — the same `todo_id` appears in "created", "completed", "failed" etc. events. |
| `action` | string | Yes | Lifecycle state: `"created"`, `"completed"`, `"failed"`, `"dismissed"`, `"deferred"`. |
| `priority` | string | No | `"high"`, `"normal"`, `"low"`. |
| `source` | string | No | What created this TODO: `"failed_action"`, `"agent_decision"`, `"human"`. |
| `context` | string | No | Additional context (error message, related task, reason for deferral). |
| `due_by` | ISO 8601 | No | When this should be done by. |

**TODO lifecycle:** A TODO's current state is determined by the most recent event with its `todo_id`. Active TODOs are those whose most recent `action` is NOT `"completed"` or `"dismissed"`.

```
created → completed      (agent finished the work)
created → failed         (agent tried and failed)
created → dismissed      (human or agent decided it's no longer needed)
created → deferred       (postponed for later)
deferred → created       (re-activated)
failed → created         (retrying)
```

**Dashboard rendering:** The Agent Detail Pipeline tab shows a TODOs table: priority (color-coded), summary, source, created (time ago). Completed/dismissed TODOs are collapsed by default. The Activity Stream renders TODO events with kind-specific icons.

### 4.9 `kind: "scheduled"` — Scheduled Work Items

**Motivation (Gap 2):** Agents often have recurring work (CRM syncs, digest emails, periodic checks). Making scheduled items visible reveals what the agent *will do* on a schedule and whether scheduled work is succeeding or failing silently.

**Produced by:** `agent.scheduled()` SDK convenience method. Emitted periodically to report the agent's schedule.

**Scope:** Agent-level (`task_id: null`, `project_id: null`). Scheduled items fire independently of current work.

```json
{
    "kind": "scheduled",
    "summary": "3 scheduled items, next: CRM Sync in 25m",
    "data": {
        "items": [
            {
                "id": "sched_crm_sync",
                "name": "CRM Pipeline Sync",
                "next_run": "2026-02-11T15:00:00Z",
                "interval": "1h",
                "enabled": true,
                "last_status": "success"
            },
            {
                "id": "sched_email_digest",
                "name": "Daily Email Digest",
                "next_run": "2026-02-12T08:00:00Z",
                "interval": "daily",
                "enabled": true,
                "last_status": null
            },
            {
                "id": "sched_health_check",
                "name": "API Health Check",
                "next_run": "2026-02-11T14:35:00Z",
                "interval": "5m",
                "enabled": false,
                "last_status": "failure"
            }
        ]
    },
    "tags": ["scheduled"]
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `items` | array | Yes | Array of scheduled items. |

**`data.items` array element:**

| Key | Type | Description |
|---|---|---|
| `id` | string | Schedule identifier. Stable across reports. |
| `name` | string | What will run. |
| `next_run` | ISO 8601 | When it's next scheduled to execute. |
| `interval` | string | Recurrence: `"5m"`, `"1h"`, `"daily"`, `"weekly"`, or `null` for one-shot. |
| `enabled` | boolean | Whether it's active. |
| `last_status` | string | `"success"`, `"failure"`, `"skipped"`, or `null` (never run). |

**Dashboard rendering:** The Agent Detail Pipeline tab shows a Scheduled table: name, next run, interval, last status (badge), enabled status. The Activity Stream renders with a scheduled icon.

### 4.10 Heartbeat Payloads — Rich Status Summaries

**Motivation (Gap 5):** Heartbeats are bare pings by default. Teams that want richer agent status can include a payload via the SDK's heartbeat callback.

```json
{
    "kind": "heartbeat_status",
    "summary": "Idle, last task completed 5m ago",
    "data": {
        "queue_depth": 3,
        "tasks_completed_since_last": 2,
        "current_state": "idle",
        "memory_mb": 256
    }
}
```

| `data` Field | Type | Required | Description |
|---|---|---|---|
| `queue_depth` | integer | No | Number of items in the agent's internal work queue. |
| `tasks_completed_since_last` | integer | No | Tasks completed since the previous heartbeat. |
| `current_state` | string | No | Agent's self-reported state. |

Heartbeat payloads are entirely optional. The schema, compaction, and stuck detection work identically whether or not heartbeats carry payloads. The only difference is in dashboard rendering — Agent Detail can show a "Heartbeat History" section that displays `summary` values when available.

**Size constraint:** Heartbeat payloads should be kept small (< 1 KB). Heartbeats are ~60% of event volume; bloated payloads would significantly increase storage.

### 4.11 Payload Convention Summary

| `kind` | Event Type | Typical Scope | Dashboard Treatment |
|---|---|---|---|
| `llm_call` | `custom` | Task | Specialized rendering: model badge, token counts, cost, prompt/response previews. Feeds Cost Explorer. |
| `plan_created` | `custom` | Task | Initializes the plan progress bar with all step descriptions. Defines plan structure. |
| `plan_step` | `custom` | Task | Updates plan progress bar overlay on Timeline. Per-step turn/token attribution. |
| `reflection` | `custom` | Task | Reflection badge on Timeline with decision + confidence. |
| `issue` | `custom` | Task or Agent | Issue badge with severity color. Lifecycle tracking via `action` field. Filterable in Activity Stream. |
| `queue_snapshot` | `custom` | Agent | Queue depth badge on Hive card. Full queue table in Pipeline tab. Amber border on high depth. |
| `todo` | `custom` | Agent | Active TODOs table in Pipeline tab. Lifecycle tracking via `todo_id` + `action`. |
| `scheduled` | `custom` | Agent | Scheduled items table in Pipeline tab with next run times and last status. |
| `heartbeat_status` | `heartbeat` | Agent | Rich heartbeat history in Agent Detail. Summary text on Hive card. |
| _(unrecognized)_ | `custom` | Any | Generic: summary text + expandable raw JSON. |

---

## 5. Derived State Queries

### 5.1 Agent Derived Status (The Hive)

**Serves:** `GET /v1/agents`, The Hive dashboard

When the dashboard is filtered to a specific project, the query joins through `project_agents` to show only agents assigned to that project. Agent status is still computed from all their events (not project-filtered), because an agent's health is a global property — it's either stuck or it isn't, regardless of which project you're viewing.

**All agents for tenant (no project filter):**

```sql
SELECT
    a.agent_id,
    a.agent_type,
    a.agent_version,
    a.framework,
    a.last_heartbeat,
    a.last_event_type,
    a.last_task_id,
    a.last_project_id,
    a.first_seen,
    a.last_seen,
    a.stuck_threshold_seconds,

    CASE
        WHEN a.last_heartbeat IS NULL
             OR a.last_heartbeat < datetime('now', '-' || a.stuck_threshold_seconds || ' seconds')
            THEN 'stuck'
        WHEN a.last_event_type IN ('task_failed', 'action_failed')
            THEN 'error'
        WHEN a.last_event_type = 'approval_requested'
            THEN 'waiting_approval'
        WHEN a.last_event_type IN ('task_started', 'action_started')
            THEN 'processing'
        ELSE 'idle'
    END AS derived_status,

    CAST((julianday('now') - julianday(a.last_heartbeat)) * 86400 AS INTEGER)
        AS heartbeat_age_seconds

FROM agents a
WHERE a.tenant_id = ?
ORDER BY
    CASE
        WHEN a.last_heartbeat IS NULL
             OR a.last_heartbeat < datetime('now', '-' || a.stuck_threshold_seconds || ' seconds')
            THEN 0
        WHEN a.last_event_type IN ('task_failed', 'action_failed') THEN 1
        WHEN a.last_event_type = 'approval_requested' THEN 2
        WHEN a.last_event_type IN ('task_started', 'action_started') THEN 3
        ELSE 4
    END,
    a.last_seen DESC;
```

**Agents filtered to a specific project:**

```sql
SELECT
    a.agent_id,
    a.agent_type,
    a.agent_version,
    a.framework,
    a.last_heartbeat,
    a.last_event_type,
    a.last_task_id,
    a.last_project_id,
    a.first_seen,
    a.last_seen,
    a.stuck_threshold_seconds,

    CASE
        WHEN a.last_heartbeat IS NULL
             OR a.last_heartbeat < datetime('now', '-' || a.stuck_threshold_seconds || ' seconds')
            THEN 'stuck'
        WHEN a.last_event_type IN ('task_failed', 'action_failed')
            THEN 'error'
        WHEN a.last_event_type = 'approval_requested'
            THEN 'waiting_approval'
        WHEN a.last_event_type IN ('task_started', 'action_started')
            THEN 'processing'
        ELSE 'idle'
    END AS derived_status,

    CAST((julianday('now') - julianday(a.last_heartbeat)) * 86400 AS INTEGER)
        AS heartbeat_age_seconds

FROM agents a
JOIN project_agents pa
    ON a.tenant_id = pa.tenant_id AND a.agent_id = pa.agent_id
WHERE a.tenant_id = ?
  AND pa.project_id = ?
ORDER BY
    CASE
        WHEN a.last_heartbeat IS NULL
             OR a.last_heartbeat < datetime('now', '-' || a.stuck_threshold_seconds || ' seconds')
            THEN 0
        WHEN a.last_event_type IN ('task_failed', 'action_failed') THEN 1
        WHEN a.last_event_type = 'approval_requested' THEN 2
        WHEN a.last_event_type IN ('task_started', 'action_started') THEN 3
        ELSE 4
    END,
    a.last_seen DESC;
```

### 5.2 Task Derived Status

**Serves:** `GET /v1/tasks/{task_id}`, Timeline header

Unchanged from v2 — task status is computed from events with matching `task_id`. Project context is carried on the events but doesn't affect the status computation.

```sql
WITH task_events AS (
    SELECT
        task_id,
        event_type,
        timestamp,
        duration_ms,
        status
    FROM events
    WHERE tenant_id = ?
      AND task_id = ?
      AND event_type IN (
          'task_started', 'task_completed', 'task_failed',
          'escalated', 'approval_requested', 'approval_received'
      )
),
task_summary AS (
    SELECT
        task_id,
        MIN(CASE WHEN event_type = 'task_started' THEN timestamp END) AS started_at,
        MAX(CASE WHEN event_type = 'task_completed' THEN timestamp END) AS completed_at,
        MAX(CASE WHEN event_type = 'task_failed' THEN timestamp END) AS failed_at,
        MAX(CASE WHEN event_type = 'task_completed' THEN duration_ms END) AS duration_ms,
        SUM(CASE WHEN event_type = 'escalated' THEN 1 ELSE 0 END) AS escalation_count,
        SUM(CASE WHEN event_type = 'approval_requested' THEN 1 ELSE 0 END) AS approval_requests,
        SUM(CASE WHEN event_type = 'approval_received' THEN 1 ELSE 0 END) AS approval_responses
    FROM task_events
    GROUP BY task_id
)
SELECT
    task_id,
    started_at,
    completed_at,
    failed_at,
    duration_ms,
    CASE
        WHEN completed_at IS NOT NULL THEN 'completed'
        WHEN failed_at IS NOT NULL THEN 'failed'
        WHEN escalation_count > 0 AND completed_at IS NULL AND failed_at IS NULL THEN 'escalated'
        WHEN approval_requests > approval_responses THEN 'waiting'
        ELSE 'processing'
    END AS derived_status,
    escalation_count > 0 AS has_escalation,
    approval_requests > 0 AS has_human_intervention
FROM task_summary;
```

### 5.3 Task List Query

**Serves:** `GET /v1/tasks`, Tasks table in dashboard

Now supports `project_id` as a primary filter. This is the most common dashboard view: "show me tasks in this project."

**Changes from v2:** The `task_costs` CTE now also extracts `tokens_in` and `tokens_out` totals. The Cost Explorer (Section 5.9) provides detailed breakdowns; this query provides the at-a-glance cost column in the task list.

```sql
WITH task_starts AS (
    SELECT
        task_id,
        task_type,
        task_run_id,
        agent_id,
        project_id,
        environment,
        timestamp AS started_at
    FROM events
    WHERE tenant_id = ?
      AND event_type = 'task_started'
      AND (? IS NULL OR project_id = ?)       -- project filter
      AND (? IS NULL OR agent_id = ?)         -- agent filter
      AND (? IS NULL OR task_type = ?)        -- type filter
      AND (? IS NULL OR environment = ?)      -- environment filter
      AND (? IS NULL OR timestamp >= ?)       -- since
      AND (? IS NULL OR timestamp <= ?)       -- until
    ORDER BY timestamp DESC
    LIMIT ?
),
task_outcomes AS (
    SELECT
        task_id,
        event_type,
        duration_ms,
        timestamp AS ended_at
    FROM events
    WHERE tenant_id = ?
      AND task_id IN (SELECT task_id FROM task_starts)
      AND event_type IN ('task_completed', 'task_failed')
),
task_action_counts AS (
    SELECT
        task_id,
        COUNT(*) AS action_count,
        SUM(CASE WHEN event_type = 'action_failed' THEN 1 ELSE 0 END) AS error_count
    FROM events
    WHERE tenant_id = ?
      AND task_id IN (SELECT task_id FROM task_starts)
      AND event_type IN ('action_started', 'action_completed', 'action_failed')
    GROUP BY task_id
),
task_costs AS (
    SELECT
        task_id,
        SUM(CAST(json_extract(payload, '$.data.cost') AS REAL)) AS total_cost,
        SUM(CAST(json_extract(payload, '$.data.tokens_in') AS INTEGER)) AS total_tokens_in,
        SUM(CAST(json_extract(payload, '$.data.tokens_out') AS INTEGER)) AS total_tokens_out,
        COUNT(*) AS llm_call_count
    FROM events
    WHERE tenant_id = ?
      AND task_id IN (SELECT task_id FROM task_starts)
      AND event_type = 'custom'
      AND json_extract(payload, '$.kind') = 'llm_call'
    GROUP BY task_id
)
SELECT
    ts.task_id,
    ts.task_type,
    ts.task_run_id,
    ts.agent_id,
    ts.project_id,
    ts.environment,
    ts.started_at,
    tou.ended_at AS completed_at,
    COALESCE(tou.duration_ms,
        CAST((julianday('now') - julianday(ts.started_at)) * 86400000 AS INTEGER)
    ) AS duration_ms,
    CASE
        WHEN tou.event_type = 'task_completed' THEN 'completed'
        WHEN tou.event_type = 'task_failed' THEN 'failed'
        ELSE 'processing'
    END AS derived_status,
    COALESCE(tac.action_count, 0) AS action_count,
    COALESCE(tac.error_count, 0) AS error_count,
    tc.total_cost,
    tc.total_tokens_in,
    tc.total_tokens_out,
    COALESCE(tc.llm_call_count, 0) AS llm_call_count
FROM task_starts ts
LEFT JOIN task_outcomes tou ON ts.task_id = tou.task_id
LEFT JOIN task_action_counts tac ON ts.task_id = tac.task_id
LEFT JOIN task_costs tc ON ts.task_id = tc.task_id
ORDER BY ts.started_at DESC;
```

### 5.4 Task Timeline Query

**Serves:** `GET /v1/tasks/{task_id}/timeline`, Timeline panel

Unchanged from v2 — queries by `task_id`, which already scopes to a single task.

```sql
SELECT
    event_id,
    event_type,
    timestamp,
    severity,
    status,
    duration_ms,
    action_id,
    parent_action_id,
    parent_event_id,
    payload
FROM events
WHERE tenant_id = ?
  AND task_id = ?
  AND (? IS NULL OR task_run_id = ?)
ORDER BY timestamp ASC;
```

Action tree and error chain construction logic is identical to v1 (server-side processing of the flat event list into hierarchical structures). See API + SDK Spec Section 4.4 for the pseudocode.

**Plan overlay (new in v3):** After fetching timeline events, the server extracts events where `json_extract(payload, '$.kind')` is either `'plan_created'` or `'plan_step'`. If any exist, it builds a plan progress structure:

- If a `plan_created` event exists, the plan structure (all step descriptions, total steps) is initialized from its `data.steps` array. All steps start as `"pending"`.
- `plan_step` events then update individual step statuses based on their `step_index` and `action`.
- If no `plan_created` exists but `plan_step` events do, the structure is built incrementally from the step events (the progress bar only shows steps that have emitted events).
- If multiple `plan_created` events exist with different `revision` values, the highest revision defines the current plan.

```json
{
    "plan": {
        "total_steps": 4,
        "steps": [
            {
                "step_index": 0,
                "description": "Search CRM for active deals",
                "status": "completed",
                "started_at": "2026-02-11T10:00:01Z",
                "completed_at": "2026-02-11T10:00:15Z"
            },
            {
                "step_index": 1,
                "description": "Qualify leads against ICP",
                "status": "started",
                "started_at": "2026-02-11T10:00:16Z",
                "completed_at": null
            }
        ]
    }
}
```

This is returned alongside the timeline events. The dashboard renders it as a progress bar above the timeline. Construction is application-layer logic — no additional SQL query needed (the plan events are already in the timeline result set).

### 5.5 Activity Stream Query

**Serves:** `GET /v1/events`, Activity Stream panel

Now supports `project_id` filter. Key behavior: when filtered to a project, **agent-level events (heartbeats, registration, and agent-level custom events) for agents in that project are included** alongside task-scoped events. This gives a complete picture of agent activity within a project context.

**Changes from v2:** The WHERE clause for agent-level events now includes `custom` events with `task_id IS NULL`, not just heartbeats and registration.

```sql
SELECT
    e.event_id,
    e.agent_id,
    e.agent_type,
    e.project_id,
    e.task_id,
    e.event_type,
    e.timestamp,
    e.severity,
    e.status,
    e.duration_ms,
    e.payload
FROM events e
WHERE e.tenant_id = ?
  AND (
      -- Either the event belongs to the filtered project
      (? IS NULL OR e.project_id = ?)
      -- Or it's an agent-level event for an agent in this project
      OR (
          ? IS NOT NULL
          AND e.project_id IS NULL
          AND EXISTS (
              SELECT 1 FROM project_agents pa
              WHERE pa.tenant_id = e.tenant_id
                AND pa.agent_id = e.agent_id
                AND pa.project_id = ?
          )
      )
  )
  AND (? IS NULL OR e.agent_id = ?)
  AND (? IS NULL OR e.task_id = ?)
  AND (? IS NULL OR e.event_type IN (?))
  AND (? IS NULL OR e.severity IN (?))
  AND (? IS NULL OR e.environment = ?)
  AND (1 = ? OR e.event_type != 'heartbeat')
  AND (? IS NULL OR e.timestamp >= ?)
  AND (? IS NULL OR e.timestamp <= ?)
ORDER BY e.timestamp DESC
LIMIT ?;
```

### 5.6 Metrics Aggregation Query

**Serves:** `GET /v1/metrics`, Summary bar + sparkline charts

Now project-scoped. **Changes from v2:** Cost aggregation now scoped to `kind: "llm_call"` events for accuracy, and includes LLM call count.

```sql
-- Summary stats for a time range, scoped to a project
SELECT
    COUNT(CASE WHEN event_type = 'task_completed' THEN 1 END) AS tasks_completed,
    COUNT(CASE WHEN event_type = 'task_failed' THEN 1 END) AS tasks_failed,
    COUNT(CASE WHEN event_type = 'escalated' THEN 1 END) AS tasks_escalated,

    CASE
        WHEN COUNT(CASE WHEN event_type IN ('task_completed', 'task_failed') THEN 1 END) > 0
        THEN ROUND(
            CAST(COUNT(CASE WHEN event_type = 'task_completed' THEN 1 END) AS REAL) /
            COUNT(CASE WHEN event_type IN ('task_completed', 'task_failed') THEN 1 END),
            3
        )
        ELSE NULL
    END AS success_rate,

    CAST(AVG(CASE WHEN event_type = 'task_completed' THEN duration_ms END) AS INTEGER)
        AS avg_duration_ms,

    SUM(
        CASE WHEN event_type = 'custom'
                  AND json_extract(payload, '$.kind') = 'llm_call'
                  AND json_extract(payload, '$.data.cost') IS NOT NULL
             THEN CAST(json_extract(payload, '$.data.cost') AS REAL)
             ELSE 0
        END
    ) AS total_cost,

    SUM(
        CASE WHEN event_type = 'custom'
                  AND json_extract(payload, '$.kind') = 'llm_call'
             THEN 1 ELSE 0
        END
    ) AS llm_call_count

FROM events
WHERE tenant_id = ?
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR environment = ?)
  AND timestamp >= ?
  AND timestamp <= ?;
```

**Timeseries buckets (project-scoped):**

```sql
SELECT
    strftime('%Y-%m-%dT%H:', timestamp) ||
        printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / 5) * 5) ||
        ':00Z' AS bucket,
    COUNT(CASE WHEN event_type = 'task_completed' THEN 1 END) AS tasks_completed,
    COUNT(CASE WHEN event_type = 'task_failed' THEN 1 END) AS tasks_failed,
    CAST(AVG(CASE WHEN event_type = 'task_completed' THEN duration_ms END) AS INTEGER)
        AS avg_duration_ms,
    SUM(
        CASE WHEN event_type = 'custom'
                  AND json_extract(payload, '$.kind') = 'llm_call'
                  AND json_extract(payload, '$.data.cost') IS NOT NULL
             THEN CAST(json_extract(payload, '$.data.cost') AS REAL)
             ELSE 0
        END
    ) AS cost,
    COUNT(CASE WHEN event_type IN ('action_failed', 'task_failed') THEN 1 END)
        AS error_count
FROM events
WHERE tenant_id = ?
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR environment = ?)
  AND timestamp >= ?
  AND timestamp <= ?
GROUP BY bucket
ORDER BY bucket ASC;
```

### 5.7 Agent Stats for Hive Cards

**Serves:** `GET /v1/agents` → `stats_1h` and sparkline, scoped to project

When viewing a project, per-agent stats should reflect only that project's work.

```sql
-- Agent stats within a project context
SELECT
    agent_id,
    COUNT(CASE WHEN event_type = 'task_completed' THEN 1 END) AS tasks_completed,
    COUNT(CASE WHEN event_type = 'task_failed' THEN 1 END) AS tasks_failed,
    CAST(AVG(CASE WHEN event_type = 'task_completed' THEN duration_ms END) AS INTEGER)
        AS avg_duration_ms,
    SUM(
        CASE WHEN event_type = 'custom'
                  AND json_extract(payload, '$.kind') = 'llm_call'
                  AND json_extract(payload, '$.data.cost') IS NOT NULL
             THEN CAST(json_extract(payload, '$.data.cost') AS REAL)
             ELSE 0
        END
    ) AS total_cost
FROM events
WHERE tenant_id = ?
  AND agent_id = ?
  AND (? IS NULL OR project_id = ?)
  AND timestamp >= datetime('now', '-1 hour')
  AND event_type IN ('task_completed', 'task_failed', 'custom');
```

### 5.8 Project Summary Query

**Serves:** Project picker / project list view

**Changes from v2:** Adds `cost_24h` to give a cost signal in the project list.

```sql
SELECT
    p.project_id,
    p.name,
    p.slug,
    p.description,
    p.environment,
    p.is_archived,
    p.created_at,

    -- Agent count
    (SELECT COUNT(*) FROM project_agents pa
     WHERE pa.tenant_id = p.tenant_id AND pa.project_id = p.project_id
    ) AS agent_count,

    -- Task stats (last 24h)
    (SELECT COUNT(*) FROM events e
     WHERE e.tenant_id = p.tenant_id AND e.project_id = p.project_id
       AND e.event_type = 'task_completed'
       AND e.timestamp >= datetime('now', '-24 hours')
    ) AS tasks_completed_24h,

    (SELECT COUNT(*) FROM events e
     WHERE e.tenant_id = p.tenant_id AND e.project_id = p.project_id
       AND e.event_type = 'task_failed'
       AND e.timestamp >= datetime('now', '-24 hours')
    ) AS tasks_failed_24h,

    -- Cost (last 24h)
    (SELECT COALESCE(SUM(CAST(json_extract(e.payload, '$.data.cost') AS REAL)), 0)
     FROM events e
     WHERE e.tenant_id = p.tenant_id AND e.project_id = p.project_id
       AND e.event_type = 'custom'
       AND json_extract(e.payload, '$.kind') = 'llm_call'
       AND e.timestamp >= datetime('now', '-24 hours')
    ) AS cost_24h

FROM projects p
WHERE p.tenant_id = ?
  AND p.is_archived = 0
ORDER BY p.name ASC;
```

### 5.9 Cost Explorer Queries

**New in v3. Serves:** `GET /v1/cost`, Cost Explorer dashboard screen

**Motivation (Gap 2):** Per-agent, per-model cost breakdown with individual call detail is the feature that enables cost optimization. This was the single most impactful feature in loopCore's observability, enabling a 5x cost reduction.

The Cost Explorer is a first-class dashboard screen (tab within Agent Detail or standalone). All queries source from `custom` events with `kind: "llm_call"`.

#### 5.9.1 Cost Summary by Agent

Aggregates cost, calls, and tokens by agent within a time range.

```sql
SELECT
    agent_id,
    COUNT(*) AS call_count,
    SUM(CAST(json_extract(payload, '$.data.tokens_in') AS INTEGER)) AS total_tokens_in,
    SUM(CAST(json_extract(payload, '$.data.tokens_out') AS INTEGER)) AS total_tokens_out,
    SUM(CAST(json_extract(payload, '$.data.cost') AS REAL)) AS total_cost
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'llm_call'
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR environment = ?)
  AND timestamp >= ?
  AND timestamp <= ?
GROUP BY agent_id
ORDER BY total_cost DESC;
```

#### 5.9.2 Cost Summary by Model

Aggregates cost, calls, and tokens by model across all agents. This reveals which models consume the most budget and whether cheaper models could be substituted.

```sql
SELECT
    json_extract(payload, '$.data.model') AS model,
    COUNT(*) AS call_count,
    SUM(CAST(json_extract(payload, '$.data.tokens_in') AS INTEGER)) AS total_tokens_in,
    SUM(CAST(json_extract(payload, '$.data.tokens_out') AS INTEGER)) AS total_tokens_out,
    SUM(CAST(json_extract(payload, '$.data.cost') AS REAL)) AS total_cost,
    ROUND(AVG(CAST(json_extract(payload, '$.data.cost') AS REAL)), 6) AS avg_cost_per_call
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'llm_call'
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR environment = ?)
  AND timestamp >= ?
  AND timestamp <= ?
GROUP BY model
ORDER BY total_cost DESC;
```

#### 5.9.3 Cost Summary by Agent and Model

Cross-tabulation: which agent is spending how much on which model. The key query for identifying optimization opportunities (e.g., "agent X is using claude-opus for tasks that claude-haiku could handle").

```sql
SELECT
    agent_id,
    json_extract(payload, '$.data.model') AS model,
    COUNT(*) AS call_count,
    SUM(CAST(json_extract(payload, '$.data.tokens_in') AS INTEGER)) AS total_tokens_in,
    SUM(CAST(json_extract(payload, '$.data.tokens_out') AS INTEGER)) AS total_tokens_out,
    SUM(CAST(json_extract(payload, '$.data.cost') AS REAL)) AS total_cost
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'llm_call'
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR environment = ?)
  AND timestamp >= ?
  AND timestamp <= ?
GROUP BY agent_id, model
ORDER BY total_cost DESC;
```

#### 5.9.4 Recent LLM Calls

Paginated list of individual LLM calls with full detail. Feeds the "Recent Calls" table in the Cost Explorer.

```sql
SELECT
    event_id,
    agent_id,
    task_id,
    project_id,
    timestamp,
    json_extract(payload, '$.data.name') AS call_name,
    json_extract(payload, '$.data.model') AS model,
    CAST(json_extract(payload, '$.data.tokens_in') AS INTEGER) AS tokens_in,
    CAST(json_extract(payload, '$.data.tokens_out') AS INTEGER) AS tokens_out,
    CAST(json_extract(payload, '$.data.cost') AS REAL) AS cost,
    CAST(json_extract(payload, '$.data.duration_ms') AS INTEGER) AS llm_duration_ms,
    json_extract(payload, '$.data.prompt_preview') AS prompt_preview,
    json_extract(payload, '$.data.response_preview') AS response_preview
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'llm_call'
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR json_extract(payload, '$.data.model') = ?)
  AND (? IS NULL OR environment = ?)
  AND (? IS NULL OR timestamp >= ?)
  AND (? IS NULL OR timestamp <= ?)
ORDER BY timestamp DESC
LIMIT ?
OFFSET ?;
```

#### 5.9.5 Cost Timeseries

Cost over time in buckets, for the Cost Explorer chart. Supports per-model breakdown.

```sql
SELECT
    strftime('%Y-%m-%dT%H:', timestamp) ||
        printf('%02d', (CAST(strftime('%M', timestamp) AS INTEGER) / 5) * 5) ||
        ':00Z' AS bucket,
    json_extract(payload, '$.data.model') AS model,
    COUNT(*) AS call_count,
    SUM(CAST(json_extract(payload, '$.data.cost') AS REAL)) AS cost,
    SUM(CAST(json_extract(payload, '$.data.tokens_in') AS INTEGER)) AS tokens_in,
    SUM(CAST(json_extract(payload, '$.data.tokens_out') AS INTEGER)) AS tokens_out
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'llm_call'
  AND (? IS NULL OR project_id = ?)
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR environment = ?)
  AND timestamp >= ?
  AND timestamp <= ?
GROUP BY bucket, model
ORDER BY bucket ASC, cost DESC;
```

### 5.10 Plan Progress Query

**New in v3. Serves:** `GET /v1/tasks/{task_id}/plan`, Plan progress panel

Extracts plan structure and step events for a task. Used when the dashboard needs the plan structure without fetching the full timeline.

```sql
-- Plan structure (if plan_created event exists)
SELECT
    event_id,
    timestamp,
    json_extract(payload, '$.kind') AS kind,
    json_extract(payload, '$.data.goal') AS goal,
    json_extract(payload, '$.data.steps') AS steps,
    json_extract(payload, '$.data.revision') AS revision
FROM events
WHERE tenant_id = ?
  AND task_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'plan_created'
ORDER BY
    CAST(json_extract(payload, '$.data.revision') AS INTEGER) DESC,
    timestamp DESC
LIMIT 1;

-- Plan step progress
SELECT
    event_id,
    timestamp,
    json_extract(payload, '$.data.step_index') AS step_index,
    json_extract(payload, '$.data.total_steps') AS total_steps,
    json_extract(payload, '$.data.step_description') AS step_description,
    json_extract(payload, '$.data.action') AS step_action,
    json_extract(payload, '$.data.turns') AS turns,
    json_extract(payload, '$.data.tokens') AS tokens,
    json_extract(payload, '$.data.plan_revision') AS plan_revision
FROM events
WHERE tenant_id = ?
  AND task_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'plan_step'
ORDER BY
    CAST(json_extract(payload, '$.data.step_index') AS INTEGER) ASC,
    timestamp ASC;
```

The application layer processes this into a plan structure: for each `step_index`, the latest event determines the step's current status.

### 5.11 Cost Threshold Alert Evaluation

**New in v3. Serves:** Alert evaluator for `cost_threshold` rules

```sql
SELECT
    SUM(CAST(json_extract(payload, '$.data.cost') AS REAL)) AS cost_in_window
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'llm_call'
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR project_id = ?)
  AND timestamp >= datetime('now', '-' || ? || ' hours');
```

The evaluator compares `cost_in_window` against the rule's `threshold_usd`. If exceeded and the cooldown period has elapsed, the alert fires.

### 5.12 Work Pipeline Queries

**New in v3. Serves:** `GET /v1/agents/{agent_id}/pipeline`, Agent Detail Pipeline tab

The pipeline endpoint returns the most recent state for each work pipeline category. All queries source from agent-level `custom` events (`task_id IS NULL`) for a specific agent.

#### 5.12.1 Latest Queue State

Returns the most recent queue snapshot for an agent.

```sql
SELECT
    event_id,
    timestamp,
    json_extract(payload, '$.data.depth') AS depth,
    json_extract(payload, '$.data.oldest_age_seconds') AS oldest_age_seconds,
    json_extract(payload, '$.data.items') AS items,
    json_extract(payload, '$.data.processing') AS processing
FROM events
WHERE tenant_id = ?
  AND agent_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'queue_snapshot'
ORDER BY timestamp DESC
LIMIT 1;
```

#### 5.12.2 Active TODOs (Aggregated)

TODOs have a lifecycle: multiple events share a `todo_id`, and the most recent event for each `todo_id` determines that TODO's current state. Active TODOs are those whose latest `action` is NOT `"completed"` or `"dismissed"`.

```sql
WITH todo_events AS (
    SELECT
        event_id,
        timestamp,
        json_extract(payload, '$.data.todo_id') AS todo_id,
        json_extract(payload, '$.data.action') AS action,
        json_extract(payload, '$.data.priority') AS priority,
        json_extract(payload, '$.data.source') AS source,
        json_extract(payload, '$.data.context') AS context,
        json_extract(payload, '$.data.due_by') AS due_by,
        json_extract(payload, '$.summary') AS summary
    FROM events
    WHERE tenant_id = ?
      AND agent_id = ?
      AND event_type = 'custom'
      AND json_extract(payload, '$.kind') = 'todo'
),
latest_per_todo AS (
    SELECT *,
        ROW_NUMBER() OVER (PARTITION BY todo_id ORDER BY timestamp DESC) AS rn
    FROM todo_events
)
SELECT
    todo_id,
    action,
    summary,
    priority,
    source,
    context,
    due_by,
    timestamp AS last_updated
FROM latest_per_todo
WHERE rn = 1
  AND action NOT IN ('completed', 'dismissed')
ORDER BY
    CASE priority
        WHEN 'high' THEN 0
        WHEN 'normal' THEN 1
        WHEN 'low' THEN 2
        ELSE 3
    END,
    timestamp DESC;
```

**For the full TODO list** (including completed/dismissed, for the collapsed section):

```sql
-- Same CTE structure, but remove the action filter:
-- WHERE rn = 1
-- ORDER BY action = 'completed' OR action = 'dismissed', priority sort, timestamp DESC
```

#### 5.12.3 Latest Scheduled Items

Returns the most recent scheduled work report for an agent.

```sql
SELECT
    event_id,
    timestamp,
    json_extract(payload, '$.data.items') AS items
FROM events
WHERE tenant_id = ?
  AND agent_id = ?
  AND event_type = 'custom'
  AND json_extract(payload, '$.kind') = 'scheduled'
ORDER BY timestamp DESC
LIMIT 1;
```

The application layer parses the `items` array and sorts by `next_run` for display.

#### 5.12.4 Active Issues for Agent

Returns issues whose most recent `action` is `"reported"` (not resolved or dismissed). Uses the same lifecycle aggregation pattern as TODOs.

```sql
WITH issue_events AS (
    SELECT
        event_id,
        timestamp,
        json_extract(payload, '$.summary') AS summary,
        json_extract(payload, '$.data.issue_id') AS issue_id,
        json_extract(payload, '$.data.severity') AS severity,
        json_extract(payload, '$.data.category') AS category,
        json_extract(payload, '$.data.action') AS action,
        json_extract(payload, '$.data.occurrence_count') AS occurrence_count,
        json_extract(payload, '$.data.context') AS context
    FROM events
    WHERE tenant_id = ?
      AND agent_id = ?
      AND event_type = 'custom'
      AND json_extract(payload, '$.kind') = 'issue'
),
latest_per_issue AS (
    SELECT *,
        ROW_NUMBER() OVER (
            -- dedup_key: issue_id when present, else summary (SQLite fallback)
            -- On PostgreSQL, replace with SHA256 hash expression (see dedup key note below)
            PARTITION BY COALESCE(issue_id, summary)
            ORDER BY timestamp DESC
        ) AS rn
    FROM issue_events
)
SELECT
    COALESCE(issue_id, summary) AS issue_key,  -- see dedup key note below for PostgreSQL hash variant
    summary,
    severity,
    category,
    action,
    occurrence_count,
    context,
    timestamp AS last_seen
FROM latest_per_issue
WHERE rn = 1
  AND (action IS NULL OR action = 'reported')
ORDER BY
    CASE severity
        WHEN 'critical' THEN 0
        WHEN 'high' THEN 1
        WHEN 'medium' THEN 2
        WHEN 'low' THEN 3
        ELSE 4
    END,
    timestamp DESC;
```

**Issue deduplication key:** To support stable deduplication when `issue_id` is omitted, the server defines a canonical dedup key:

- If `payload.data.issue_id` is present and non-empty: `dedup_key = payload.data.issue_id`
- Else: `dedup_key = SHA256(normalize(payload.summary))`

Where `normalize(s)` trims whitespace, collapses runs of whitespace, and lowercases the string. Hash-based grouping avoids accidental collisions from long summaries and makes grouping stable across minor formatting differences.

**Recommended PostgreSQL expression:**

```sql
COALESCE(
    payload->'data'->>'issue_id',
    encode(digest(lower(trim(payload->>'summary')), 'sha256'), 'hex')
)
```

**SQLite equivalent** (requires the `sha256` extension, or falls back to raw summary text):

```sql
COALESCE(
    json_extract(payload, '$.data.issue_id'),
    json_extract(payload, '$.summary')  -- acceptable approximation when hashing is unavailable
)
```

**Implementation notes:**
- Systems may store `dedup_key` as a computed value (recommended for consistent behavior across DBs), or use `payload.summary` directly for grouping when hashing is not available (acceptable approximation for MVP/SQLite).
- The returned issue object SHOULD include both `issue_id` (if present) and `summary`, and MAY include `dedup_key` for client caching and joins.
- In the aggregation query above, replace `COALESCE(issue_id, summary)` with the appropriate dedup key expression for the target database.

#### 5.12.5 Pipeline API Response Assembly

The `GET /v1/agents/{agent_id}/pipeline` endpoint executes queries 5.12.1–5.12.4 and assembles the response:

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
        "active": [
            {"todo_id": "todo_retry_crm", "action": "created", "summary": "...", "priority": "high", ...}
        ],
        "completed_count": 2,
        "dismissed_count": 0
    },
    "scheduled": {
        "last_updated": "2026-02-11T14:00:00Z",
        "items": [...]
    },
    "issues": {
        "active": [
            {"issue_key": "issue_crm_403", "summary": "...", "severity": "high", ...}
        ],
        "resolved_count": 1
    }
}
```

If no events exist for a pipeline category, that key is `null` (not absent). This tells the dashboard "no data yet" vs "empty state."

---

## 6. Ingestion Pipeline

### 6.1 Pipeline Steps

```
    POST /v1/ingest
          │
          ▼
  ┌──────────────────┐
  │ 1. Authenticate  │  Derive tenant_id from API key
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐
  │ 2. Validate      │  Envelope integrity, batch size limits
  │    Envelope      │
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────┐
  │ 3. Per-Event     │  Required fields, event_type enum,
  │    Validation    │  field size limits, project_id existence
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 3b. Validate payload conventions │  If payload has "kind", validate
  │     (advisory, non-blocking)     │  against known shapes. Log warnings
  │                                  │  for malformed payloads but do NOT
  │                                  │  reject the event.
  └──────┬───────────────────────────┘
         │
         ▼
  ┌──────────────────┐
  │ 4. Expand        │  Merge envelope fields into each event.
  │    Envelope      │  Set received_at. Apply severity defaults.
  └──────┬───────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 5. Validate project_id          │  If event has project_id, verify
  │    (if present)                 │  project exists for this tenant.
  │                                 │  Auto-create if configured.
  └──────┬──────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 6. Batch INSERT (events)         │  INSERT OR IGNORE for dedup
  └──────┬───────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 7. Update agents cache           │  UPSERT agent profile
  └──────┬───────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 8. Update project_agents         │  INSERT OR IGNORE junction row
  │    (if task event with project)  │  for (tenant, project, agent)
  └──────┬───────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 9. Broadcast to WebSocket        │  Push events to subscribed
  │    subscribers                   │  connections (project-filtered)
  └──────┬───────────────────────────┘
         │
         ▼
  ┌──────────────────────────────────┐
  │ 10. Evaluate alert rules         │  Check conditions, fire if met
  │     (includes cost_threshold)    │  (project-scoped rules first)
  └──────────────────────────────────┘
```

**Step 3b — Payload convention validation (new in v3):**

This step is advisory, not blocking. When a `custom` event has a `payload` with a `kind` field matching a well-known kind (`llm_call`, `plan_created`, `plan_step`, `reflection`, `issue`, `queue_snapshot`, `todo`, `scheduled`), the pipeline validates that required `data` fields are present. If validation fails:
- The event is **still ingested** (never reject valid events due to payload shape).
- A warning is logged server-side for SDK debugging.
- The response includes a `warnings` array with details (e.g., `"llm_call payload missing required field: data.model"`).

This ensures forward compatibility — new SDK versions can emit new payload shapes without the server rejecting them.

**Step 5 — Project validation behavior:**

| Scenario | Behavior |
|---|---|
| `project_id` is null | Skip validation (agent-level event). |
| `project_id` matches existing project | Continue normally. |
| `project_id` doesn't exist AND tenant has `auto_create_projects: true` in settings | Auto-create project with name = project_id, slug = project_id. |
| `project_id` doesn't exist AND `auto_create_projects: false` (default) | Reject event with `invalid_project` error. |

Default behavior is to reject unknown projects. This prevents typos from silently creating garbage projects. Teams that want auto-creation enable it in workspace settings.

### 6.2 Batch INSERT Strategy

```sql
INSERT OR IGNORE INTO events (
    event_id, tenant_id, agent_id, agent_type,
    project_id,
    timestamp, received_at,
    environment, "group",
    task_id, task_type, task_run_id, correlation_id,
    action_id, parent_action_id,
    event_type, severity,
    status, duration_ms,
    parent_event_id, payload
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
```

### 6.3 Agents Cache Update

```sql
INSERT INTO agents (
    tenant_id, agent_id, agent_type, agent_version,
    framework, runtime,
    first_seen, last_seen, last_heartbeat,
    last_event_type, last_task_id, last_project_id,
    stuck_threshold_seconds
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (tenant_id, agent_id) DO UPDATE SET
    agent_type = COALESCE(excluded.agent_type, agents.agent_type),
    agent_version = COALESCE(excluded.agent_version, agents.agent_version),
    framework = COALESCE(excluded.framework, agents.framework),
    runtime = COALESCE(excluded.runtime, agents.runtime),
    last_seen = CASE WHEN excluded.last_seen > agents.last_seen
                     THEN excluded.last_seen ELSE agents.last_seen END,
    last_heartbeat = CASE WHEN excluded.last_heartbeat IS NOT NULL
                          THEN excluded.last_heartbeat ELSE agents.last_heartbeat END,
    last_event_type = excluded.last_event_type,
    last_task_id = COALESCE(excluded.last_task_id, agents.last_task_id),
    last_project_id = COALESCE(excluded.last_project_id, agents.last_project_id);
```

### 6.4 Project-Agent Junction Update

Executed for every task event that has a `project_id`:

```sql
INSERT OR IGNORE INTO project_agents (tenant_id, project_id, agent_id)
VALUES (?, ?, ?);
```

---

## 7. Data Retention

### 7.1 Retention Policy

Events are retained based on the tenant's plan:

| Plan | Retention |
|---|---|
| `free` | 7 days |
| `pro` | 30 days |
| `enterprise` | 90 days |

### 7.2 Cleanup Job

```sql
DELETE FROM events
WHERE tenant_id = ?
  AND timestamp < datetime('now', '-' || ? || ' days');
```

### 7.3 Heartbeat Compaction

Heartbeats are ~60% of event volume. After 24 hours, compact to one heartbeat per agent per hour.

**Changes from v3:** When selecting which heartbeat to retain per hour, prefer heartbeats that carry a payload (non-null, non-empty). This preserves rich heartbeat summaries for teams that use the heartbeat payload callback.

```sql
DELETE FROM events
WHERE tenant_id = ?
  AND event_type = 'heartbeat'
  AND timestamp < datetime('now', '-1 day')
  AND event_id NOT IN (
      SELECT event_id FROM (
          SELECT event_id,
                 ROW_NUMBER() OVER (
                     PARTITION BY agent_id, strftime('%Y-%m-%d %H', timestamp)
                     ORDER BY
                         CASE WHEN payload IS NOT NULL AND payload != '{}' THEN 0 ELSE 1 END,
                         timestamp DESC
                 ) AS rn
          FROM events
          WHERE tenant_id = ?
            AND event_type = 'heartbeat'
            AND timestamp < datetime('now', '-1 day')
      ) WHERE rn = 1
  );
```

**Compaction preference order:** Within each (agent, hour) partition, retain the heartbeat that (1) has a non-empty payload (if any do), and (2) is the most recent. This ensures that rich heartbeat summaries survive compaction.

### 7.4 Project Archival

Archiving a project sets `is_archived = 1`. Events are retained per the tenant's retention policy. The project disappears from the dashboard but its data remains queryable via the API with an `include_archived=true` parameter.

Deleting a project permanently removes the project record, its `project_agents` rows, and all events with that `project_id`:

```sql
BEGIN TRANSACTION;
DELETE FROM events WHERE tenant_id = ? AND project_id = ?;
DELETE FROM project_agents WHERE tenant_id = ? AND project_id = ?;
DELETE FROM alert_rules WHERE tenant_id = ? AND project_id = ?;
DELETE FROM projects WHERE tenant_id = ? AND project_id = ?;
COMMIT;
```

---

## 8. PostgreSQL Production Enhancements

### 8.1 Column Type Changes

| Column | SQLite | PostgreSQL | Rationale |
|---|---|---|---|
| `payload` | TEXT | JSONB | Enables `@>`, `?`, `->` operators. |
| `timestamp` | TEXT | TIMESTAMPTZ | Proper timezone handling. |
| `received_at` | TEXT | TIMESTAMPTZ | Proper timezone handling. |
| `tenant_id` | TEXT | UUID | Native UUID type. |
| `event_id` | TEXT | UUID | Native UUID type. |
| `project_id` | TEXT | UUID | Native UUID type. |
| `is_active`, `is_archived`, `is_enabled`, `is_registered` | INTEGER | BOOLEAN | Native boolean type. |

### 8.2 TimescaleDB Hypertable

```sql
SELECT create_hypertable('events', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

ALTER TABLE events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'tenant_id, project_id, agent_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);

SELECT add_compression_policy('events', INTERVAL '7 days');
```

Note: `compress_segmentby` includes `project_id` for efficient project-scoped queries on compressed chunks.

### 8.3 Continuous Aggregates

**Changes from v2:** Added `total_cost` and `llm_call_count` columns. Added a separate `cost_by_model_5m` aggregate for the Cost Explorer.

```sql
-- General metrics aggregate (updated)
CREATE MATERIALIZED VIEW metrics_5m
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    project_id,
    agent_id,
    environment,
    time_bucket('5 minutes', timestamp) AS bucket,
    COUNT(*) FILTER (WHERE event_type = 'task_completed') AS tasks_completed,
    COUNT(*) FILTER (WHERE event_type = 'task_failed') AS tasks_failed,
    AVG(duration_ms) FILTER (WHERE event_type = 'task_completed') AS avg_duration_ms,
    COUNT(*) FILTER (WHERE event_type IN ('action_failed', 'task_failed')) AS error_count,
    SUM((payload->>'data'->>'cost')::numeric)
        FILTER (WHERE event_type = 'custom' AND payload->>'kind' = 'llm_call')
        AS total_cost,
    COUNT(*) FILTER (WHERE event_type = 'custom' AND payload->>'kind' = 'llm_call')
        AS llm_call_count
FROM events
GROUP BY tenant_id, project_id, agent_id, environment, bucket;

SELECT add_continuous_aggregate_policy('metrics_5m',
    start_offset => INTERVAL '1 hour',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes'
);
```

```sql
-- Cost-by-model aggregate (new in v3)
-- Serves the Cost Explorer's per-model breakdown and cost timeseries
CREATE MATERIALIZED VIEW cost_by_model_5m
WITH (timescaledb.continuous) AS
SELECT
    tenant_id,
    project_id,
    agent_id,
    payload->'data'->>'model' AS model,
    time_bucket('5 minutes', timestamp) AS bucket,
    COUNT(*) AS call_count,
    SUM((payload->'data'->>'tokens_in')::bigint) AS total_tokens_in,
    SUM((payload->'data'->>'tokens_out')::bigint) AS total_tokens_out,
    SUM((payload->'data'->>'cost')::numeric) AS total_cost,
    AVG((payload->'data'->>'cost')::numeric) AS avg_cost_per_call
FROM events
WHERE event_type = 'custom'
  AND payload->>'kind' = 'llm_call'
GROUP BY tenant_id, project_id, agent_id, model, bucket;

SELECT add_continuous_aggregate_policy('cost_by_model_5m',
    start_offset => INTERVAL '1 hour',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes'
);
```

**When to use which aggregate:**

| Dashboard Query | Use |
|---|---|
| Summary bar (tasks, success rate, avg duration) | `metrics_5m` |
| Sparkline charts (tasks/errors over time) | `metrics_5m` |
| Total cost in summary bar | `metrics_5m` |
| Cost Explorer: by-agent breakdown | `cost_by_model_5m` (aggregate across models) |
| Cost Explorer: by-model breakdown | `cost_by_model_5m` (aggregate across agents) |
| Cost Explorer: agent×model cross-tab | `cost_by_model_5m` (direct) |
| Cost Explorer: cost timeseries | `cost_by_model_5m` (aggregate by bucket) |
| Cost Explorer: recent calls detail | Raw events table (no aggregate) |

### 8.4 Payload JSONB Indexes

**Changes from v2:** Added indexes for cost-related payload fields to accelerate Cost Explorer queries.

```sql
-- Well-known kind lookup (used by all payload-aware queries)
CREATE INDEX idx_events_payload_kind
    ON events USING btree ((payload->>'kind'))
    WHERE event_type = 'custom';

-- Tags lookup (for tag-based filtering)
CREATE INDEX idx_events_payload_tags
    ON events USING gin ((payload -> 'tags'));

-- Model lookup (for Cost Explorer by-model queries)
CREATE INDEX idx_events_payload_model
    ON events USING btree ((payload->'data'->>'model'))
    WHERE event_type = 'custom' AND payload->>'kind' = 'llm_call';

-- Cost extraction (for cost aggregation, alert evaluation)
-- Note: btree on a numeric extraction for range queries and SUM aggregation
CREATE INDEX idx_events_payload_cost
    ON events USING btree (tenant_id, agent_id, timestamp DESC, ((payload->'data'->>'cost')::numeric))
    WHERE event_type = 'custom' AND payload->>'kind' = 'llm_call';
```

**Index design notes:**

- `idx_events_payload_kind` changed from GIN to btree with text extraction and a partial index filter. The `kind` field is a single string value, not an array — btree with equality lookup is more efficient than GIN for this access pattern.
- `idx_events_payload_model` enables the Cost Explorer's `GROUP BY model` queries without scanning non-LLM events.
- `idx_events_payload_cost` is a composite index that covers the cost threshold alert evaluation query (Section 5.11) and the cost summary queries. The partial index filter ensures it only indexes LLM call events.
- `idx_events_payload_tags` remains GIN because tags are arrays that benefit from containment queries (`@>`).

---

## 9. SDK Impact Summary

The project concept and payload conventions require the following changes to the SDK (documented in API + SDK Spec):

### 9.1 `agent.task()` — New `project` Parameter

```python
with agent.task(
    task_id="task_lead-4821",
    project="sales-pipeline",        # NEW in v2 — optional, default: "default"
    type="lead_processing",
    task_run_id="run_abc123"
) as task:
    # All events within this block carry project_id = "sales-pipeline"
    pass
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | str | `"default"` | Project slug or ID. All events within this task context inherit this value. |

### 9.2 `task.llm_call()` — LLM Call Convenience Method

**New in v3.** Emits a `custom` event with `kind: "llm_call"` payload.

```python
task.llm_call(
    name="phase1_reasoning",
    model="claude-sonnet-4-20250514",
    prompt_preview=prompt[:500],
    response_preview=response[:500],
    tokens_in=1500,
    tokens_out=200,
    cost=0.003,
    duration_ms=1200,
    metadata={"caller": "atomic_phase1_turn_3"}
)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `name` | str | Yes | Call identifier for timeline labels. |
| `model` | str | Yes | Model identifier for cost-by-model aggregation. |
| `tokens_in` | int | No | Input token count. Recommended for cost tracking. |
| `tokens_out` | int | No | Output token count. Recommended for cost tracking. |
| `cost` | float | No | Pre-calculated cost in USD. When absent, call is excluded from cost aggregations. |
| `prompt_preview` | str | No | Truncated prompt (default: None). |
| `response_preview` | str | No | Truncated response (default: None). |
| `duration_ms` | int | No | LLM call latency in milliseconds. |
| `metadata` | dict | No | Arbitrary metadata for the call detail view. |

The SDK auto-generates the `summary` field: `"{name} → {model} ({tokens_in} in / {tokens_out} out, ${cost})"`.

**`agent.llm_call()` — Agent-Level LLM Calls**

Identical signature to `task.llm_call()` but emitted outside a task context (`task_id: null`, `project_id: null`). For agent-level LLM calls not tied to a specific task — e.g., background summarization, periodic analysis, self-reflection.

```python
agent.llm_call(
    name="daily_summary",
    model="claude-haiku-4-5-20250929",
    tokens_in=800,
    tokens_out=150,
    cost=0.0004,
    duration_ms=450
)
```
### 9.3 `agent.event()` — Agent-Level Custom Events

**New in v3.** Emits a `custom` event outside any task context. `task_id` and `project_id` are null.

```python
agent.event(
    payload={
        "kind": "issue",
        "summary": "CRM API returning 403 for workspace queries",
        "data": {
            "severity": "high",
            "category": "permissions"
        }
    },
    severity="warn"
)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `payload` | dict | Yes | Event payload. Should follow a well-known kind convention. |
| `severity` | str | No | Event severity. Default: `"info"`. |

### 9.4 Heartbeat Payload Callback

**New in v3.** Optional callback on agent registration that provides payload data for heartbeat events.

```python
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    heartbeat_payload=lambda: {
        "kind": "heartbeat_status",
        "summary": f"Idle, queue depth: {len(queue)}",
        "data": {
            "queue_depth": len(queue),
            "tasks_completed_since_last": completed_count,
            "current_state": "idle"
        }
    }
)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `heartbeat_payload` | Callable → dict | No | Called before each heartbeat emission. Return value becomes the heartbeat event's `payload`. If None or omitted, heartbeats have no payload (backward compatible). |

### 9.5 `agent.queue_snapshot()` — Queue State Reporting

**New in v3.** Emits a `custom` event with `kind: "queue_snapshot"` payload.

```python
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

| Parameter | Type | Required | Description |
|---|---|---|---|
| `depth` | int | Yes | Number of items in the queue. `0` is valid. |
| `oldest_age_seconds` | int | No | Age of the oldest queued item. |
| `items` | list[dict] | No | Summary of queued items. |
| `processing` | dict | No | Currently processing item. |

**SDK behavior:** Builds a `custom` event with `kind: "queue_snapshot"`, auto-generates `summary` as `"Queue: {depth} items, oldest {age}s"`, emits with `task_id: null`.

### 9.6 `agent.todo()` — TODO Lifecycle

**New in v3.** Emits a `custom` event with `kind: "todo"` payload on each TODO state change.

```python
# Create a TODO
agent.todo(
    todo_id="todo_retry_crm",
    action="created",
    summary="Retry: CRM write failed (403)",
    priority="high",
    source="failed_action",
    context="Tool crm_write returned 403 Forbidden for workspace query"
)

# Later, when completed:
agent.todo(
    todo_id="todo_retry_crm",
    action="completed",
    summary="CRM write succeeded after credential refresh"
)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `todo_id` | str | Yes | Stable identifier for lifecycle tracking. |
| `action` | str | Yes | `"created"`, `"completed"`, `"failed"`, `"dismissed"`, `"deferred"`. |
| `summary` | str | Yes | TODO description. |
| `priority` | str | No | `"high"`, `"normal"`, `"low"`. |
| `source` | str | No | What created this TODO. |
| `context` | str | No | Additional context. |
| `due_by` | str | No | ISO 8601 deadline. |

**SDK behavior:** Builds a `custom` event with `kind: "todo"`, auto-generates `summary` as `"TODO {action}: {summary}"`, emits with `task_id: null`.

### 9.7 `agent.scheduled()` — Scheduled Work Report

**New in v3.** Emits a `custom` event with `kind: "scheduled"` payload.

```python
agent.scheduled(items=[
    {"id": "sched_crm_sync", "name": "CRM Pipeline Sync", "next_run": "2026-02-11T09:00:00Z",
     "interval": "1h", "enabled": True, "last_status": "success"},
    {"id": "sched_email_digest", "name": "Daily Email Digest", "next_run": "2026-02-12T08:00:00Z",
     "interval": "daily", "enabled": True, "last_status": None},
])
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `items` | list[dict] | Yes | Array of scheduled items. Each: `{id, name, next_run, interval, enabled, last_status}`. |

**SDK behavior:** Builds a `custom` event with `kind: "scheduled"`, auto-generates `summary` as `"{count} scheduled items, next at {time}"`, emits with `task_id: null`.

### 9.8 `queue_provider` Callback — Auto-Snapshot on Heartbeat

**New in v3.** Optional callback on agent registration that auto-emits a queue snapshot with every heartbeat.

```python
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    queue_provider=lambda: {"depth": len(my_queue), "items": [...]}
)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `queue_provider` | Callable → dict | No | Called before each heartbeat. Return value is passed to `agent.queue_snapshot()`. If None or omitted, no queue data is emitted with heartbeats. If the callback raises or returns None, the heartbeat is sent without queue data (graceful degradation). |

**SDK behavior:** When `queue_provider` is set, the heartbeat loop calls it before each heartbeat, then calls `agent.queue_snapshot()` with the returned dict. This means the queue snapshot is a separate event emitted alongside (not inside) the heartbeat event.

### 9.9 `task.plan()` — Plan Creation

**New in v3.** Emits a `custom` event with `kind: "plan_created"` payload.

```python
task.plan(
    goal="Process inbound lead",
    steps=["Search CRM for existing record", "Score lead", "Send follow-up email", "Update CRM"]
)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `goal` | str | Yes | High-level plan goal. |
| `steps` | list[str] | Yes | Ordered list of step descriptions. The SDK converts these to `{"index": i, "description": step}` objects. |
| `revision` | int | No | Plan revision number. Default: `0`. Increment on replan. |

**SDK behavior:** Builds a `custom` event with `kind: "plan_created"`, auto-generates `summary` as `"Plan: {goal} ({n} steps)"`.

### 9.10 `task.plan_step()` — Plan Step Progress

**New in v3.** Emits a `custom` event with `kind: "plan_step"` payload.

```python
# Report step started
task.plan_step(step_index=0, action="started", summary="Search CRM for existing record")

# ... actions happen ...

# Report step completed with metrics
task.plan_step(step_index=0, action="completed", summary="Found existing CRM record", turns=2, tokens=3200)
```

| Parameter | Type | Required | Description |
|---|---|---|---|
| `step_index` | int | Yes | Zero-based step position. |
| `action` | str | Yes | `"started"`, `"completed"`, `"failed"`, `"skipped"`. |
| `summary` | str | Yes | Step description. |
| `total_steps` | int | No | Total steps. If omitted, SDK uses the value from the most recent `task.plan()` call in this task context. |
| `turns` | int | No | LLM turns spent (on completion/failure). |
| `tokens` | int | No | Tokens spent (on completion/failure). |
| `plan_revision` | int | No | Plan revision number. Correlates with `task.plan()` revision. |

### 9.11 `agent.report_issue()` / `agent.resolve_issue()` — Issue Lifecycle

**New in v3.** Convenience methods for the `kind: "issue"` payload.

```python
# Report an issue
agent.report_issue(
    summary="CRM API returning 403 for workspace queries",
    severity="high",
    category="permissions",
    context={"tool": "crm_search", "error_code": 403, "last_seen": "2026-02-11T14:30:00Z"},
    issue_id="issue_crm_403",              # optional — enables explicit lifecycle tracking
    occurrence_count=3                      # optional — how many times this has occurred
)

# Later, when resolved:
agent.resolve_issue(
    summary="CRM API returning 403 for workspace queries",
    issue_id="issue_crm_403"               # if provided on report, use same ID
)
```

**`agent.report_issue()` parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `summary` | str | Yes | Issue title. Also used for deduplication when `issue_id` is absent. |
| `severity` | str | Yes | `"critical"`, `"high"`, `"medium"`, `"low"`. |
| `category` | str | No | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"`. |
| `context` | dict | No | Arbitrary debugging context. |
| `issue_id` | str | No | Stable identifier. If omitted, server deduplicates by summary hash. |
| `occurrence_count` | int | No | Agent-tracked occurrence count. |

**SDK behavior:** Builds a `custom` event with `kind: "issue"` and `data.action: "reported"`. Emits with `task_id: null`.

**`agent.resolve_issue()` parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `summary` | str | Yes | Must match the original issue's summary (for dedup when no `issue_id`). |
| `issue_id` | str | No | Same `issue_id` used in `report_issue`. |

**SDK behavior:** Builds a `custom` event with `kind: "issue"` and `data.action: "resolved"`. The dashboard's pipeline query (Section 5.12.4) uses the latest `action` per issue to determine active vs. resolved state.

### 9.12 Batch Envelope — No `project_id`

The batch envelope does **not** carry `project_id` — because events within a single batch may belong to different projects (if the agent processes tasks from multiple projects between flushes). Instead, `project_id` is carried per-event.

### 9.13 Framework Integrations

Framework integrations need a way to specify project context. Two options:

**Option 1 — Global default on the callback:**

```python
callback = LangChainCallback(hb, agent, project="sales-pipeline")
```

All tasks captured by this callback use the specified project.

**Option 2 — Per-run via metadata:**

```python
agent.invoke({"input": "..."}, config={"metadata": {"hiveloop_project": "sales-pipeline"}})
```

The integration reads `hiveloop_project` from the framework's metadata/config system.

Both options should be supported. Global default for simple setups, per-run override for agents that serve multiple projects.

---

## 10. API Impact Summary

### 10.1 New Endpoints

```
GET    /v1/projects                    — List projects for tenant
POST   /v1/projects                    — Create a project
GET    /v1/projects/{project_id}       — Get project details + summary stats
PUT    /v1/projects/{project_id}       — Update project
DELETE /v1/projects/{project_id}       — Delete project (cascades)
POST   /v1/projects/{project_id}/archive   — Archive project
POST   /v1/projects/{project_id}/unarchive — Unarchive project

GET    /v1/projects/{project_id}/agents    — List agents in project
POST   /v1/projects/{project_id}/agents    — Manually add agent to project
DELETE /v1/projects/{project_id}/agents/{agent_id}  — Remove agent from project

GET    /v1/cost                        — Cost Explorer summary (new in v3)
GET    /v1/cost/calls                  — Recent LLM calls detail (new in v3)
GET    /v1/cost/timeseries             — Cost over time (new in v3)

GET    /v1/agents/{agent_id}/pipeline  — Work pipeline state: queue, TODOs, scheduled, issues (new in v3)
GET    /v1/llm-calls                   — Alias for /v1/cost/calls (new in v3, for discoverability)
```

**Pipeline endpoint (new in v3):**

| Endpoint | Query Parameters | Serves |
|---|---|---|
| `GET /v1/agents/{agent_id}/pipeline` | `include_completed_todos` (bool, default false), `include_resolved_issues` (bool, default false) | Section 5.12.1–5.12.5 |

The response includes `queue`, `todos`, `scheduled`, and `issues` sections. Each is `null` if no data exists for that category. See Section 5.12.5 for the response shape.

**Cost Explorer endpoints (new in v3):**

| Endpoint | Query Parameters | Serves |
|---|---|---|
| `GET /v1/cost` | `project_id`, `agent_id`, `environment`, `group_by` (`agent`, `model`, `agent_model`), `since`, `until` | Section 5.9.1, 5.9.2, 5.9.3 |
| `GET /v1/cost/calls` | `project_id`, `agent_id`, `model`, `environment`, `since`, `until`, `limit`, `offset` | Section 5.9.4 |
| `GET /v1/cost/timeseries` | `project_id`, `agent_id`, `environment`, `since`, `until`, `bucket_size` | Section 5.9.5 |

### 10.2 Modified Endpoints

All existing query endpoints gain a `project_id` query parameter:

| Endpoint | New Parameter | Behavior |
|---|---|---|
| `GET /v1/agents` | `project_id` | Filter to agents in this project (via junction table). |
| `GET /v1/tasks` | `project_id` | Filter to tasks in this project. |
| `GET /v1/events` | `project_id` | Filter to events in this project + agent-level events for project's agents. |
| `GET /v1/metrics` | `project_id`, `metric`, `group_by`, `time_range`, `agent_id`, `environment`, `model` | Scope metrics to this project. Supports `group_by` for grouped aggregation (see below). |
| `WS /v1/stream` | `project_id` in subscription filters | Scope real-time stream to project. |

**`group_by` support for `/v1/metrics`:** The metrics endpoint supports an optional `group_by` parameter for aggregate metric grouping, reducing endpoint proliferation for lightweight dashboard and programmatic use cases.

**Endpoint:** `GET /v1/metrics?metric={metric}&group_by={dimension}&time_range={range}&filters...`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `metric` | string | Yes | One of: `events`, `tasks`, `tokens_in`, `tokens_out`, `llm_calls`, `cost` |
| `group_by` | string | No | One of: `agent_id`, `model` (only for `tokens_in`, `tokens_out`, `llm_calls`, `cost`), `event_type`, `kind` (payload.kind; applies to custom events) |
| `time_range` | string | Yes | Examples: `1h`, `24h`, `7d`, `30d` |
| `agent_id` / `project_id` / `environment` / `model` | string | No | Optional filters |

**Behavior:**
- When `group_by` is omitted, returns a single aggregate value for the metric (existing behavior).
- When `group_by` is provided, returns a list of `{group, value}` rows ordered by `value DESC`.
- For `metric=cost`, the server computes cost by summing `payload.data.cost` over events where `payload.kind = "llm_call"` within the time range (ignoring null cost).

**Example queries:**
- `GET /v1/metrics?metric=cost&group_by=model&time_range=24h`
- `GET /v1/metrics?metric=tokens_in&group_by=model&time_range=7d`
- `GET /v1/metrics?metric=llm_calls&group_by=agent_id&time_range=7d`

**SQL outline** (for `group_by=model, metric=cost`, PostgreSQL):

```sql
SELECT
    payload->'data'->>'model' AS "group",
    SUM(COALESCE((payload->'data'->>'cost')::numeric, 0)) AS value
FROM events
WHERE tenant_id = ?
  AND event_type = 'custom'
  AND payload->>'kind' = 'llm_call'
  AND timestamp >= NOW() - ?::interval
  AND (? IS NULL OR agent_id = ?)
  AND (? IS NULL OR project_id = ?)
GROUP BY 1
ORDER BY value DESC;
```

**Relationship to `/v1/cost` endpoints:** The `/v1/cost/*` endpoints remain the preferred interface for the Cost Explorer UI because they return richer breakdowns (agent×model matrices, recent call lists, timeseries). `/v1/metrics` grouping exists for lightweight dashboards and programmatic summaries.

**Changes from v2:** `GET /v1/tasks` response now includes `total_cost`, `total_tokens_in`, `total_tokens_out`, and `llm_call_count` per task (Section 5.3).

### 10.3 Dashboard Impact

The dashboard gains:

1. **Project selector** in the top bar (next to the environment selector). Selecting a project filters all panels. An "All Projects" option shows the tenant-wide view.

2. **Cost Explorer** screen — a new top-level dashboard view (or tab within Agent Detail) with:
   - Summary cards: total cost, total calls, avg cost per call (for the selected time range)
   - Cost-by-agent table
   - Cost-by-model table
   - Cost timeseries chart (stacked by model)
   - Recent LLM Calls table with expandable rows showing prompt/response previews

3. **Plan progress bar** on the Task Timeline when `kind: "plan_step"` events are detected. If `kind: "plan_created"` is also present, the bar initializes with all step descriptions in pending state.

4. **LLM call rendering** on the Task Timeline: `kind: "llm_call"` events display with model badge, token counts, cost, and expandable prompt/response previews.

5. **Heartbeat history** in Agent Detail: when heartbeats carry payloads, display a summary timeline instead of just "last heartbeat: Xs ago".

6. **Agent Detail — Pipeline tab** (new, alongside existing Recent Tasks, Metrics, Event Log):
   - **Queue section:** Header with depth badge. If processing: current item with elapsed time. Table of queued items (priority, source, summary, age). Empty state: "Queue empty."
   - **TODOs section:** Header with count badge and breakdown. Active TODOs table (priority, summary, source, created). Completed/dismissed collapsed by default.
   - **Scheduled section:** Header with enabled count. Table: name, next run, interval, last status (badge), enabled.
   - **Issues section:** Active issues table sorted by severity then recency. Expandable to show context. Resolved/dismissed collapsed by default.

7. **Hive card enhancements:**
   - Queue depth badge (`Q:4`) when queue snapshot data is available.
   - Processing status line ("Processing: Send email (45s)") when agent is working.
   - Amber card border when queue depth exceeds threshold (configurable, default 10).
   - Issue indicator dot when agent has active high/critical issues.

8. **Activity Stream — kind-specific rendering** for work pipeline events:
   - `queue_snapshot`: queue icon + "Queue: 4 items (oldest: 2m)"
   - `todo` (created): todo icon + "TODO created: Retry CRM write"
   - `todo` (completed): check icon + "TODO completed: CRM write succeeded"
   - `scheduled`: schedule icon + "3 scheduled items, next: CRM Sync in 25m"
   - `issue`: warning icon + severity-colored badge + issue summary

---

## 11. Schema Migration Strategy

### 11.1 Migration Files

```
migrations/
  0001_initial_schema.sql        — tenants, api_keys, events, agents, alerts
  0002_add_projects.sql          — projects, project_agents tables
  0003_add_project_id_to_events.sql  — Add project_id column to events
  0004_add_project_indexes.sql   — New indexes for project-scoped queries
  0005_seed_default_projects.sql — Create default project for each tenant
  0006_add_custom_events_index.sql   — Index 7: partial index on custom events (v3)
  0007_add_cost_threshold_alert.sql  — Add cost_threshold to alert_rules CHECK (v3)
```

### 11.2 Migration 0006 Detail

```sql
-- 0006_add_custom_events_index.sql
CREATE INDEX idx_events_custom
    ON events(tenant_id, agent_id, timestamp DESC)
    WHERE event_type = 'custom';
```

### 11.3 Migration 0007 Detail

```sql
-- 0007_add_cost_threshold_alert.sql
-- SQLite does not support ALTER TABLE ... DROP CONSTRAINT or modifying CHECK constraints.
-- For SQLite MVP, this is handled by recreating the table (or enforcing in application layer).
-- For PostgreSQL:
ALTER TABLE alert_rules DROP CONSTRAINT IF EXISTS alert_rules_condition_type_check;
ALTER TABLE alert_rules ADD CONSTRAINT alert_rules_condition_type_check
    CHECK (condition_type IN (
        'agent_stuck', 'task_failed', 'error_rate',
        'duration_exceeded', 'heartbeat_lost',
        'cost_threshold'
    ));
```

### 11.4 Migration Runner

For MVP (SQLite), a simple Python script tracks applied migrations:

```sql
CREATE TABLE IF NOT EXISTS _migrations (
    version     INTEGER     PRIMARY KEY,
    name        TEXT        NOT NULL,
    applied_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

For production (PostgreSQL), use Alembic.

---

## 12. Query-to-Index Coverage Map

| Query (Section) | Primary Index Used | Covers |
|---|---|---|
| 5.1 Agent status (all) | `idx_agents_tenant` | Full |
| 5.1 Agent status (project) | `idx_agents_tenant` + `idx_project_agents_by_agent` | Full (join) |
| 5.2 Task derived status | `idx_events_task_timeline` | Full |
| 5.3 Task list | `idx_events_tasks` | Full (project + agent + time) |
| 5.3 Task list (cost CTE) | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) |
| 5.4 Task timeline | `idx_events_task_timeline` | Full |
| 5.5 Activity stream | `idx_events_stream` | Full (project + time + type) |
| 5.6 Metrics | `idx_events_metrics` | Full (project + env + time + type) |
| 5.6 Metrics (group_by=model) | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) — same path as Cost Explorer |
| 5.7 Agent 1h stats | `idx_events_agent_latest` | Partial (agent scope) |
| 5.8 Project summary | Subqueries use `idx_events_metrics` | Acceptable (low frequency) |
| 5.9.1 Cost by agent | `idx_events_custom` (SQLite) / `idx_events_payload_cost` (PG) | Partial → Full (PG) |
| 5.9.2 Cost by model | `idx_events_custom` (SQLite) / `idx_events_payload_model` (PG) | Partial → Full (PG) |
| 5.9.3 Cost by agent×model | `idx_events_custom` (SQLite) / `idx_events_payload_cost` (PG) | Partial → Full (PG) |
| 5.9.4 Recent LLM calls | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) |
| 5.9.5 Cost timeseries | `idx_events_custom` (SQLite) / `idx_events_payload_cost` (PG) | Partial → Full (PG) |
| 5.10 Plan progress | `idx_events_task_timeline` | Full (task scope, then filter in app) |
| 5.11 Cost alert evaluation | `idx_events_custom` (SQLite) / `idx_events_payload_cost` (PG) | Partial → Full (PG) |
| 5.12.1 Latest queue state | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) |
| 5.12.2 Active TODOs | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) |
| 5.12.3 Latest scheduled | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) |
| 5.12.4 Active issues | `idx_events_custom` (SQLite) / `idx_events_payload_kind` (PG) | Partial → Full (PG) |
| 6.2 Batch insert | PK `(tenant_id, event_id)` | Full |
| 6.4 Junction update | PK `(tenant_id, project_id, agent_id)` | Full |
| Heartbeat lookup | `idx_events_heartbeat` | Full |
| Alert cooldown | `idx_alert_history_cooldown` | Full |

**SQLite vs PostgreSQL coverage notes:**

On SQLite, Cost Explorer queries use `idx_events_custom` to narrow to `custom` events, then scan+filter by `json_extract(payload, '$.kind')`. This is "Partial" coverage — adequate for MVP volumes (< 100K events/day) but requires a full scan of custom events.

On PostgreSQL, the JSONB indexes (`idx_events_payload_kind`, `idx_events_payload_model`, `idx_events_payload_cost`) provide direct indexed access to payload fields. This is "Full" coverage — queries hit the index directly without scanning unrelated events. For production volumes (> 1M events/day), these indexes are essential.

---

## 13. Estimated Storage

**Assumptions:** 3 projects, 10 agents total (some shared), each processing 100 tasks/day, average 6 events per task (including 2 LLM calls per task as `custom` events), heartbeats every 30 seconds.

| Event Source | Events/Day | Avg Size | Daily Storage |
|---|---|---|---|
| Heartbeats (bare) | 10 × 2,880 = 28,800 | ~200 bytes | ~5.5 MB |
| Task lifecycle events | 10 × 100 × 4 = 4,000 | ~520 bytes | ~2.0 MB |
| LLM call events (`kind: "llm_call"`) | 10 × 100 × 2 = 2,000 | ~1,200 bytes | ~2.3 MB |
| **Total** | **34,800** | | **~9.8 MB/day** |

**LLM call event size breakdown:** The `llm_call` payload includes `model` (~40 chars), `name` (~30 chars), `prompt_preview` (~500 chars), `response_preview` (~500 chars), plus numeric fields. Average ~800 bytes for the payload, ~400 bytes for the event envelope = ~1,200 bytes total.

| Additional Tables | Estimated Size |
|---|---|
| `projects` (3 rows) | < 1 KB |
| `project_agents` (~15 rows) | < 2 KB |
| `agents` (10 rows) | < 5 KB |

| Retention | Raw Storage | After Heartbeat Compaction |
|---|---|---|
| 7 days (free) | ~69 MB | ~35 MB |
| 30 days (pro) | ~294 MB | ~150 MB |
| 90 days (enterprise) | ~882 MB | ~449 MB |

**Storage impact of v3 changes:** LLM call events add ~2.3 MB/day vs v2 estimates. This is a ~15% increase in daily storage, justified by the cost observability value. Teams not using `task.llm_call()` see no change.

---

## 14. Complete Table Summary

| Table | Rows (typical) | Purpose | PK |
|---|---|---|---|
| `tenants` | 1 per workspace | Top-level isolation | `tenant_id` |
| `api_keys` | 2-5 per tenant | Authentication | `key_id` |
| `projects` | 3-20 per tenant | Organizational grouping | `(tenant_id, project_id)` |
| `agents` | 5-50 per tenant | Agent profile cache | `(tenant_id, agent_id)` |
| `project_agents` | agents × ~2 projects | Many-to-many junction | `(tenant_id, project_id, agent_id)` |
| `events` | 10K-100K+ per day | Source of truth | `(tenant_id, event_id)` |
| `alert_rules` | 5-20 per tenant | Alert config | `rule_id` |
| `alert_history` | Varies | Alert audit log | `alert_id` |
| `_migrations` | ~10 | Schema version tracking | `version` |

**Total tables: 9** (including `_migrations`). No new tables in v3 — all changes are payload conventions, queries, and indexes.

**PostgreSQL continuous aggregates: 2** (up from 1 in v2)
- `metrics_5m` — general metrics (updated with cost columns)
- `cost_by_model_5m` — cost breakdown by model (new in v3)

---

## Appendix A: v2 → v3 Change Summary

*See also: Appendix B (v3 → v4) below.*

| Area | What Changed | Why |
|---|---|---|
| Section 1.2 | New principle #7 (payload conventions as contracts) | Establishes the design philosophy for well-known payload shapes |
| Section 3.6 | Clarified agent-level custom events (`task_id = NULL`) | Gap 4: agent self-reported issues need events outside task context |
| Section 3.7 | Added Index 7 (`idx_events_custom`) | Supports Cost Explorer, pipeline, and plan queries on SQLite |
| Section 3.8 | Added `cost_threshold` to alert condition types | Gap 2: cost-based alerting |
| **Section 4** | **New: Payload Conventions (10 well-known kinds)** | Gaps 1–5: formalized well-known payload shapes |
| Section 4.2 | `llm_call`: `tokens_in`, `tokens_out`, `cost` are optional (not required) | Enables incremental instrumentation — log call first, add cost later |
| Section 4.3 | `plan_step`: added `turns`, `tokens`, `plan_revision` fields; renamed `status` → `action` | Per-step cost attribution and replan tracking |
| **Section 4.4** | **New: `plan_created` kind** | Gap 3: plan structure needed upfront for progress bar rendering |
| **Section 4.6** | **`issue` kind: added `issue_id`, `action`, `occurrence_count`** | Gap 4: issue lifecycle tracking from day one |
| **Section 4.7** | **New: `queue_snapshot` kind** | Gap 2: work queue visibility |
| **Section 4.8** | **New: `todo` kind** | Gap 2: TODO lifecycle tracking |
| **Section 4.9** | **New: `scheduled` kind** | Gap 2: scheduled work visibility |
| Section 5.3 | Task list CTE now uses `kind: "llm_call"` for costs | More precise cost extraction |
| Section 5.6 | Metrics now filter by `kind: "llm_call"` for costs | More precise cost aggregation |
| Section 5.7 | Agent stats now include custom events for cost | Cost on Hive cards |
| Section 5.8 | Project summary adds `cost_24h` | Cost signal in project list |
| **Section 5.9** | **New: Cost Explorer Queries (5 queries)** | Gap 1: per-agent, per-model cost breakdown |
| **Section 5.10** | **New: Plan Progress Query** | Gap 3: plan-aware timeline |
| **Section 5.11** | **New: Cost Threshold Alert Evaluation** | Gap 1: cost alerting |
| **Section 5.12** | **New: Work Pipeline Queries (queue, TODOs, scheduled, issues)** | Gap 2, Gap 4: pipeline state and issue lifecycle |
| Section 6.1 | Added Step 3b (payload convention validation for all 8 well-known kinds) | Advisory validation for well-known payload shapes |
| Section 7.3 | Heartbeat compaction prefers payloaded heartbeats | Gap 5: preserve rich heartbeat summaries |
| Section 8.3 | Updated `metrics_5m`, added `cost_by_model_5m` | Cost aggregation in continuous aggregates |
| Section 8.4 | Redesigned JSONB indexes for cost queries | Cost Explorer performance on PostgreSQL |
| Section 9.2 | Added `task.llm_call()` + `agent.llm_call()` | Gap 1: LLM call tracking convenience methods |
| Section 9.3 | Added `agent.event()` | Agent-level custom events outside task context |
| Section 9.4 | Added `heartbeat_payload` callback | Gap 5: rich heartbeat summaries |
| **Section 9.5** | **New: `agent.queue_snapshot()`** | Gap 2: queue state reporting |
| **Section 9.6** | **New: `agent.todo()`** | Gap 2: TODO lifecycle |
| **Section 9.7** | **New: `agent.scheduled()`** | Gap 2: scheduled work reporting |
| **Section 9.8** | **New: `queue_provider` callback** | Gap 2: auto-snapshot queue on heartbeat |
| **Section 9.9** | **New: `task.plan()`** | Gap 3: plan creation |
| **Section 9.10** | **New: `task.plan_step()`** | Gap 3: plan step progress |
| **Section 9.11** | **New: `agent.report_issue()` / `agent.resolve_issue()`** | Gap 4: issue lifecycle convenience methods |
| Section 10.1 | Added `/v1/cost` endpoints, `/v1/agents/{id}/pipeline`, `/v1/llm-calls` | Cost Explorer API, pipeline API |
| Section 10.3 | Added Pipeline tab, Hive card enhancements, Activity Stream kind rendering | Full dashboard impact for Gaps 1–5 |
| Section 11 | Added migrations 0006, 0007 | New index and alert type |
| Section 12 | Expanded query-to-index map with cost and pipeline queries | Coverage documentation |
| Section 13 | Updated storage estimates for LLM call events | Accurate sizing with payload data |

---

## Appendix B: v3 → v4 Change Summary

These changes close the two minor gaps identified in the gap analysis between v3.0 and the v2 Addendum.

| Gap # | Area | What Changed | Section |
|---|---|---|---|
| 1 | Metrics API | Added explicit `group_by` parameter support to `GET /v1/metrics` (supports `agent_id`, `model`, `event_type`, `kind`). Includes SQL outline for grouped cost queries. `/v1/cost/*` endpoints retained as the richer interface for Cost Explorer UI. | 10.2 |
| 2 | Issue deduplication | Formally specified SHA256-hash-based dedup key when `issue_id` is absent: `dedup_key = SHA256(normalize(summary))`. Documented PostgreSQL expression, SQLite fallback, and implementation notes. SQL query annotated with dedup key references. | 5.12.4 |

**Result:** The Data Model Spec v4.0 now contains all definitions from the v2 Addendum with zero gaps.

---

*End of Document*

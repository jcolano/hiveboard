# HiveBoard — Data Model Specification

**CONFIDENTIAL** | February 2026 | v2.0

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
  │     └── Agent-level events (heartbeats) have no project scope
  │
  └── API Keys (tenant-scoped, shared across projects)
```

**Key design decision:** Project context lives on the **task**, not the agent. When a developer starts a task, they specify which project it belongs to. The agent is a shared resource that can serve multiple projects. Agent-level events (heartbeats, registration) are project-agnostic — they appear in all projects the agent participates in.

### 1.2 Governing Principles

1. **Events are the single source of truth.** There is one events table. Dashboards, timelines, metrics, and alerts are all derived from it.

2. **Agent profiles are a convenience cache.** The `agents` table exists to make fleet queries fast. It is always rebuildable from the events table.

3. **Multi-tenancy is structural, not optional.** Every table has `tenant_id` as the leading column in its primary key or a required foreign key. Every index leads with `tenant_id`. There is no query that crosses tenant boundaries.

4. **Projects are an organizational lens, not a security boundary.** Tenant-level API keys access all projects. Projects organize work; tenants enforce isolation.

5. **SQLite for MVP, PostgreSQL for production.** The schema is written in portable SQL that works on both. Production-specific extensions are called out separately.

6. **Indexes are designed from queries, not from intuition.** Every index maps to a specific API endpoint or dashboard query.

### 1.3 Cross-References

| Document | What It Defines | What This Document Adds |
|---|---|---|
| Event Schema Spec v1 | Canonical event shape, field types, size limits | Physical storage, column types, constraints |
| API + SDK Spec v1 | Endpoints and query parameters | Exact SQL queries, index coverage |
| Product Spec v1 | Dashboard screens and metrics | Aggregation queries, derived state logic |

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

**Changes from v1:** Added `project_id` column. Nullable — agent-level events (heartbeats, registration) have no project. Task-level events carry the project from their task context.

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

**`project_id` population rules:**

| Event Type | `project_id` Value |
|---|---|
| `agent_registered` | NULL (agent-level, no project) |
| `heartbeat` | NULL (agent-level, no project) |
| `task_started` | From SDK — developer specifies on `agent.task()` |
| `task_completed`, `task_failed` | Inherited from `task_started` (same task context) |
| `action_started`, `action_completed`, `action_failed` | Inherited from active task context |
| `retry_started`, `escalated` | Inherited from active task context |
| `approval_requested`, `approval_received` | Inherited from active task context |
| `custom` | Inherited from active task context (or NULL if emitted via `agent.event()`) |

**The SDK handles inheritance automatically.** When `agent.task("task_123", project="sales-pipeline")` is called, all events within that task context carry `project_id = "sales-pipeline"`. The developer never sets `project_id` on individual events.

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

-- INDEX 7: Deduplication — enforced by composite PK
-- (tenant_id, event_id) — INSERT OR IGNORE uses this
```

**Index design notes:**

- Index 3 (Activity Stream) now includes `project_id` as the second column. When the dashboard is filtered to a specific project, this index covers the query without scanning unrelated events.
- Index 5 (Task listing) includes both `project_id` and `agent_id` to handle the two most common filter patterns: "all tasks in this project" and "all tasks for this agent in this project."
- Index 6 (Metrics) includes `project_id` for project-scoped metrics dashboards.

---

### 3.8 `alert_rules`

Configuration for alert conditions. Now with optional project scoping.

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
        'duration_exceeded', 'heartbeat_lost'
    ))
);

CREATE INDEX idx_alert_rules_tenant
    ON alert_rules(tenant_id, project_id) WHERE is_enabled = 1;
```

| Column | Type | Description |
|---|---|---|
| `project_id` | TEXT | Nullable. If set, alert fires only for events in this project. If null, fires for all projects in the tenant. |

All other columns identical to v1.

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

## 4. Derived State Queries

### 4.1 Agent Derived Status (The Hive)

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

### 4.2 Task Derived Status

**Serves:** `GET /v1/tasks/{task_id}`, Timeline header

Unchanged from v1 — task status is computed from events with matching `task_id`. Project context is carried on the events but doesn't affect the status computation.

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

### 4.3 Task List Query

**Serves:** `GET /v1/tasks`, Tasks table in dashboard

Now supports `project_id` as a primary filter. This is the most common dashboard view: "show me tasks in this project."

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
        SUM(
            CAST(json_extract(payload, '$.data.cost') AS REAL)
        ) AS total_cost
    FROM events
    WHERE tenant_id = ?
      AND task_id IN (SELECT task_id FROM task_starts)
      AND payload IS NOT NULL
      AND json_extract(payload, '$.data.cost') IS NOT NULL
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
    tc.total_cost
FROM task_starts ts
LEFT JOIN task_outcomes tou ON ts.task_id = tou.task_id
LEFT JOIN task_action_counts tac ON ts.task_id = tac.task_id
LEFT JOIN task_costs tc ON ts.task_id = tc.task_id
ORDER BY ts.started_at DESC;
```

### 4.4 Task Timeline Query

**Serves:** `GET /v1/tasks/{task_id}/timeline`, Timeline panel

Unchanged from v1 — queries by `task_id`, which already scopes to a single task.

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

### 4.5 Activity Stream Query

**Serves:** `GET /v1/events`, Activity Stream panel

Now supports `project_id` filter. Key behavior: when filtered to a project, **agent-level events (heartbeats, registration) for agents in that project are included** alongside task-scoped events. This gives a complete picture of agent activity within a project context.

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

### 4.6 Metrics Aggregation Query

**Serves:** `GET /v1/metrics`, Summary bar + sparkline charts

Now project-scoped.

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
        CASE WHEN json_extract(payload, '$.data.cost') IS NOT NULL
             THEN CAST(json_extract(payload, '$.data.cost') AS REAL)
             ELSE 0
        END
    ) AS total_cost

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
        CASE WHEN json_extract(payload, '$.data.cost') IS NOT NULL
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

### 4.7 Agent Stats for Hive Cards

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
        CASE WHEN json_extract(payload, '$.data.cost') IS NOT NULL
             THEN CAST(json_extract(payload, '$.data.cost') AS REAL)
             ELSE 0
        END
    ) AS total_cost
FROM events
WHERE tenant_id = ?
  AND agent_id = ?
  AND (? IS NULL OR project_id = ?)
  AND timestamp >= datetime('now', '-1 hour')
  AND event_type IN ('task_completed', 'task_failed');
```

### 4.8 Project Summary Query

**Serves:** Project picker / project list view

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
    ) AS tasks_failed_24h

FROM projects p
WHERE p.tenant_id = ?
  AND p.is_archived = 0
ORDER BY p.name ASC;
```

---

## 5. Ingestion Pipeline

### 5.1 Pipeline Steps

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
  │     (project-scoped rules first) │
  └──────────────────────────────────┘
```

**Step 5 — Project validation behavior:**

| Scenario | Behavior |
|---|---|
| `project_id` is null | Skip validation (agent-level event). |
| `project_id` matches existing project | Continue normally. |
| `project_id` doesn't exist AND tenant has `auto_create_projects: true` in settings | Auto-create project with name = project_id, slug = project_id. |
| `project_id` doesn't exist AND `auto_create_projects: false` (default) | Reject event with `invalid_project` error. |

Default behavior is to reject unknown projects. This prevents typos from silently creating garbage projects. Teams that want auto-creation enable it in workspace settings.

### 5.2 Batch INSERT Strategy

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

### 5.3 Agents Cache Update

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

### 5.4 Project-Agent Junction Update

Executed for every task event that has a `project_id`:

```sql
INSERT OR IGNORE INTO project_agents (tenant_id, project_id, agent_id)
VALUES (?, ?, ?);
```

---

## 6. Data Retention

### 6.1 Retention Policy

Events are retained based on the tenant's plan:

| Plan | Retention |
|---|---|
| `free` | 7 days |
| `pro` | 30 days |
| `enterprise` | 90 days |

### 6.2 Cleanup Job

```sql
DELETE FROM events
WHERE tenant_id = ?
  AND timestamp < datetime('now', '-' || ? || ' days');
```

### 6.3 Heartbeat Compaction

Heartbeats are ~60% of event volume. After 24 hours, compact to one heartbeat per agent per hour:

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
                     ORDER BY timestamp DESC
                 ) AS rn
          FROM events
          WHERE tenant_id = ?
            AND event_type = 'heartbeat'
            AND timestamp < datetime('now', '-1 day')
      ) WHERE rn = 1
  );
```

### 6.4 Project Archival

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

## 7. PostgreSQL Production Enhancements

### 7.1 Column Type Changes

| Column | SQLite | PostgreSQL | Rationale |
|---|---|---|---|
| `payload` | TEXT | JSONB | Enables `@>`, `?`, `->` operators. |
| `timestamp` | TEXT | TIMESTAMPTZ | Proper timezone handling. |
| `received_at` | TEXT | TIMESTAMPTZ | Proper timezone handling. |
| `tenant_id` | TEXT | UUID | Native UUID type. |
| `event_id` | TEXT | UUID | Native UUID type. |
| `project_id` | TEXT | UUID | Native UUID type. |
| `is_active`, `is_archived`, `is_enabled`, `is_registered` | INTEGER | BOOLEAN | Native boolean type. |

### 7.2 TimescaleDB Hypertable

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

Note: `compress_segmentby` now includes `project_id` for efficient project-scoped queries on compressed chunks.

### 7.3 Continuous Aggregates

```sql
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
    COUNT(*) FILTER (WHERE event_type IN ('action_failed', 'task_failed')) AS error_count
FROM events
GROUP BY tenant_id, project_id, agent_id, environment, bucket;

SELECT add_continuous_aggregate_policy('metrics_5m',
    start_offset => INTERVAL '1 hour',
    end_offset => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes'
);
```

### 7.4 Payload JSONB Indexes

```sql
CREATE INDEX idx_events_payload_kind
    ON events USING gin ((payload -> 'kind'));

CREATE INDEX idx_events_payload_tags
    ON events USING gin ((payload -> 'tags'));
```

---

## 8. SDK Impact Summary

The project concept requires the following changes to the SDK (documented in API + SDK Spec):

### 8.1 `agent.task()` — New `project` Parameter

```python
with agent.task(
    task_id="task_lead-4821",
    project="sales-pipeline",        # NEW — optional, default: "default"
    type="lead_processing",
    task_run_id="run_abc123"
) as task:
    # All events within this block carry project_id = "sales-pipeline"
    pass
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `project` | str | `"default"` | Project slug or ID. All events within this task context inherit this value. |

### 8.2 Batch Envelope — New `project_id` Field

The batch envelope does **not** carry `project_id` — because events within a single batch may belong to different projects (if the agent processes tasks from multiple projects between flushes). Instead, `project_id` is carried per-event.

### 8.3 Framework Integrations

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

## 9. API Impact Summary

### 9.1 New Endpoints

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
```

### 9.2 Modified Endpoints

All existing query endpoints gain a `project_id` query parameter:

| Endpoint | New Parameter | Behavior |
|---|---|---|
| `GET /v1/agents` | `project_id` | Filter to agents in this project (via junction table). |
| `GET /v1/tasks` | `project_id` | Filter to tasks in this project. |
| `GET /v1/events` | `project_id` | Filter to events in this project + agent-level events for project's agents. |
| `GET /v1/metrics` | `project_id` | Scope metrics to this project. |
| `WS /v1/stream` | `project_id` in subscription filters | Scope real-time stream to project. |

### 9.3 Dashboard Impact

The dashboard gains a **project selector** in the top bar (next to the environment selector). Selecting a project filters all panels: The Hive shows only agents in that project, the Tasks table shows only that project's tasks, the Activity Stream shows project-scoped events, and metrics reflect project-specific data.

An "All Projects" option shows the tenant-wide view (existing behavior).

---

## 10. Schema Migration Strategy

### 10.1 Migration Files

```
migrations/
  0001_initial_schema.sql        — tenants, api_keys, events, agents, alerts
  0002_add_projects.sql          — projects, project_agents tables
  0003_add_project_id_to_events.sql  — Add project_id column to events
  0004_add_project_indexes.sql   — New indexes for project-scoped queries
  0005_seed_default_projects.sql — Create default project for each tenant
```

### 10.2 Migration Runner

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

## 11. Query-to-Index Coverage Map

| Query (Section) | Primary Index Used | Covers |
|---|---|---|
| 4.1 Agent status (all) | `idx_agents_tenant` | Full |
| 4.1 Agent status (project) | `idx_agents_tenant` + `idx_project_agents_by_agent` | Full (join) |
| 4.2 Task derived status | `idx_events_task_timeline` | Full |
| 4.3 Task list | `idx_events_tasks` | Full (project + agent + time) |
| 4.4 Task timeline | `idx_events_task_timeline` | Full |
| 4.5 Activity stream | `idx_events_stream` | Full (project + time + type) |
| 4.6 Metrics | `idx_events_metrics` | Full (project + env + time + type) |
| 4.7 Agent 1h stats | `idx_events_agent_latest` | Partial (agent scope) |
| 4.8 Project summary | Subqueries use `idx_events_metrics` | Acceptable (low frequency) |
| 5.2 Batch insert | PK `(tenant_id, event_id)` | Full |
| 5.4 Junction update | PK `(tenant_id, project_id, agent_id)` | Full |
| Heartbeat lookup | `idx_events_heartbeat` | Full |
| Alert cooldown | `idx_alert_history_cooldown` | Full |

---

## 12. Estimated Storage

**Assumptions:** 3 projects, 10 agents total (some shared), each processing 100 tasks/day, average 6 events per task, heartbeats every 30 seconds.

| Event Source | Events/Day | Avg Size | Daily Storage |
|---|---|---|---|
| Heartbeats | 10 × 2,880 = 28,800 | ~200 bytes | ~5.5 MB |
| Task events | 10 × 100 × 6 = 6,000 | ~520 bytes (+project_id) | ~3.0 MB |
| **Total** | **34,800** | | **~8.5 MB/day** |

| Additional Tables | Estimated Size |
|---|---|
| `projects` (3 rows) | < 1 KB |
| `project_agents` (~15 rows) | < 2 KB |
| `agents` (10 rows) | < 5 KB |

The `project_id` column adds ~20 bytes per task event. Negligible impact on storage.

| Retention | Raw Storage | After Heartbeat Compaction |
|---|---|---|
| 7 days (free) | ~60 MB | ~26 MB |
| 30 days (pro) | ~255 MB | ~112 MB |
| 90 days (enterprise) | ~765 MB | ~335 MB |

---

## 13. Complete Table Summary

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

**Total tables: 9** (including `_migrations`).

---

*End of Document*

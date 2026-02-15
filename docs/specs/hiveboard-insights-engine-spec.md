# Feature Spec: Pre-Aggregated Insights Engine

**Feature name:** Pre-Aggregated Insights Engine (running hourly rollups)
**Priority:** High — enables the Insights page to answer operational questions without scanning raw events
**Depends on:** Existing ingest pipeline (Step 8–9 in `POST /v1/ingest`)
**LLM usage:** None — pure arithmetic aggregation
**Backend changes:** New aggregator module, new JSON tables, new API endpoints
**Frontend changes:** Insights page reads from pre-computed endpoints instead of scanning `/v1/events`

---

## 1. Problem

Every query endpoint today (`/v1/metrics`, `/v1/cost`, `/v1/llm-calls`) works by **scanning the full events table** and aggregating on the fly. This has three consequences:

1. **Performance degrades with event volume.** The `get_metrics()` function in `storage_json.py` iterates every event, groups by task, derives status, and bins into time buckets — on every single request.

2. **Historical data is lost when events are pruned.** Free-tier events are pruned after 7 days. Once pruned, the metrics are gone. There's no way to answer "what was my error rate last month?" after the raw events expire.

3. **Certain questions can't be answered at all.** "Busiest hour of the week," "cost trend over 30 days," "tool usage distribution" — these require aggregation across time ranges that exceed raw event retention.

---

## 2. Solution

Maintain **running aggregate tables** that are updated incrementally as events are ingested. The aggregation happens at write time (inside the ingest pipeline), not at read time.

### Design principles

1. **Increment on write, read instantly.** Each ingested event updates the relevant hourly bucket. Query endpoints read pre-computed rows — no scanning.
2. **Survive pruning.** Aggregate tables have their own, much longer retention (90 days for hourly, 1 year for daily). Raw events can be pruned aggressively without losing trend data.
3. **Two tables, one granularity.** `agent_hourly` and `model_hourly` at 1-hour buckets. Simple, covers all current insight questions.
4. **Idempotent rebuilds.** If aggregates are lost or corrupted, they can be rebuilt from raw events (while those events still exist).
5. **Same storage pattern.** JSON files, in-memory + write-through, asyncio locks — identical to existing tables.

---

## 3. Aggregate Table Schemas

### 3.1 `agent_hourly.json`

One row per `(tenant_id, agent_id, hour)`. Updated on every ingested event.

```python
{
    # ── Key ──
    "tenant_id": "uuid",
    "agent_id": "lead-qualifier",
    "hour": "2026-02-15T14:00:00Z",     # Truncated to hour boundary

    # ── Task counters ──
    "tasks_started": 5,
    "tasks_completed": 4,
    "tasks_failed": 1,
    "task_duration_sum_ms": 16800,        # For computing averages
    "task_duration_count": 4,             # Divisor for avg (only completed/failed)

    # ── Action counters ──
    "actions_started": 22,
    "actions_completed": 20,
    "actions_failed": 2,

    # ── LLM usage ──
    "llm_call_count": 12,
    "llm_tokens_in": 18400,
    "llm_tokens_out": 3200,
    "llm_cost": 0.0847,
    "llm_max_tokens_in": 4200,           # Largest single prompt this hour
    "llm_max_tokens_in_name": "lead_scoring",  # Which call had the biggest prompt

    # ── Error breakdown ──
    "errors_by_type": {                   # From error_type / exception_type
        "RateLimitError": 2,
        "TimeoutError": 1
    },
    "errors_by_category": {               # From issue payload.data.category
        "rate_limit": 2,
        "connectivity": 1
    },

    # ── LLM model breakdown ──
    "models": {
        "claude-sonnet-4-20250514": {
            "calls": 8,
            "cost": 0.0720,
            "tokens_in": 14000,
            "tokens_out": 2400
        },
        "gpt-4o-mini-2024-07-18": {
            "calls": 4,
            "cost": 0.0127,
            "tokens_in": 4400,
            "tokens_out": 800
        }
    },

    # ── LLM call name breakdown ──
    "calls_by_name": {
        "lead_scoring": {
            "count": 5,
            "tokens_in_sum": 9500,
            "tokens_out_sum": 1200,
            "cost_sum": 0.042
        },
        "enrichment": {
            "count": 4,
            "tokens_in_sum": 5600,
            "tokens_out_sum": 1100,
            "cost_sum": 0.028
        }
    },

    # ── Action name breakdown (tool proxy) ──
    "actions_by_name": {
        "web_search": 8,
        "crm_lookup": 5,
        "email_draft": 3
    },

    # ── Operational events ──
    "retries": 1,
    "escalations": 0,
    "approvals_requested": 0,
    "approvals_received": 0,
    "issues_reported": 1,
    "issues_resolved": 0,

    # ── Metadata (for rebuild tracking) ──
    "event_count": 47,                    # Total events aggregated into this bucket
    "last_updated": "2026-02-15T14:58:32Z"
}
```

**Storage characteristics:**
- 5 agents x 24 hours x 30 days = **3,600 rows** for a month
- Each row ~500 bytes JSON = **~1.8 MB/month** — negligible

### 3.2 `model_hourly.json`

One row per `(tenant_id, model, hour)`. Updated only on `llm_call` events.

```python
{
    # ── Key ──
    "tenant_id": "uuid",
    "model": "claude-sonnet-4-20250514",
    "hour": "2026-02-15T14:00:00Z",

    # ── Totals ──
    "call_count": 18,
    "tokens_in": 32000,
    "tokens_out": 5400,
    "cost": 0.142,
    "duration_sum_ms": 24600,
    "duration_count": 18,

    # ── Biggest prompt this hour ──
    "max_tokens_in": 9600,
    "max_tokens_in_agent": "lead-qualifier",
    "max_tokens_in_name": "phase1_reasoning",

    # ── Per-agent breakdown ──
    "agents": {
        "lead-qualifier": {
            "calls": 10,
            "cost": 0.089,
            "tokens_in": 18000,
            "tokens_out": 3000
        },
        "support-triage": {
            "calls": 8,
            "cost": 0.053,
            "tokens_in": 14000,
            "tokens_out": 2400
        }
    },

    # ── Per call-name breakdown ──
    "calls_by_name": {
        "lead_scoring": {"count": 6, "cost_sum": 0.048},
        "phase1_reasoning": {"count": 4, "cost_sum": 0.062},
        "categorization": {"count": 8, "cost_sum": 0.032}
    },

    # ── Metadata ──
    "last_updated": "2026-02-15T14:58:32Z"
}
```

**Storage characteristics:**
- ~4 models x 24 hours x 30 days = **2,880 rows/month**
- Each row ~300 bytes = **~860 KB/month**

---

## 4. Aggregation Logic

### 4.1 The update function

Called once per event, inside the ingest pipeline after Step 9 (agent cache update).

```python
# src/backend/aggregator.py

def update_agent_hourly(bucket: dict, event: dict) -> None:
    """Increment an agent_hourly bucket with data from one event."""
    etype = event.get("event_type")
    payload = event.get("payload") or {}
    kind = payload.get("kind")
    pd = payload.get("data") or {}

    bucket["event_count"] = bucket.get("event_count", 0) + 1
    bucket["last_updated"] = event.get("received_at") or event.get("timestamp")

    # ── Task events ──
    if etype == "task_started":
        bucket["tasks_started"] = bucket.get("tasks_started", 0) + 1
    elif etype == "task_completed":
        bucket["tasks_completed"] = bucket.get("tasks_completed", 0) + 1
        dur = event.get("duration_ms")
        if dur is not None:
            bucket["task_duration_sum_ms"] = bucket.get("task_duration_sum_ms", 0) + dur
            bucket["task_duration_count"] = bucket.get("task_duration_count", 0) + 1
    elif etype == "task_failed":
        bucket["tasks_failed"] = bucket.get("tasks_failed", 0) + 1
        dur = event.get("duration_ms")
        if dur is not None:
            bucket["task_duration_sum_ms"] = bucket.get("task_duration_sum_ms", 0) + dur
            bucket["task_duration_count"] = bucket.get("task_duration_count", 0) + 1

    # ── Action events ──
    elif etype == "action_started":
        bucket["actions_started"] = bucket.get("actions_started", 0) + 1
        name = payload.get("summary") or payload.get("action_name")
        if name:
            by_name = bucket.setdefault("actions_by_name", {})
            by_name[name] = by_name.get(name, 0) + 1
    elif etype == "action_completed":
        bucket["actions_completed"] = bucket.get("actions_completed", 0) + 1
        name = payload.get("summary") or payload.get("action_name")
        if name:
            by_name = bucket.setdefault("actions_by_name", {})
            by_name[name] = by_name.get(name, 0) + 1
    elif etype == "action_failed":
        bucket["actions_failed"] = bucket.get("actions_failed", 0) + 1
        error_type = pd.get("error_type") or pd.get("exception_type") or "unknown"
        by_type = bucket.setdefault("errors_by_type", {})
        by_type[error_type] = by_type.get(error_type, 0) + 1

    # ── Narrative events ──
    elif etype == "retry_started":
        bucket["retries"] = bucket.get("retries", 0) + 1
    elif etype == "escalated":
        bucket["escalations"] = bucket.get("escalations", 0) + 1
    elif etype == "approval_requested":
        bucket["approvals_requested"] = bucket.get("approvals_requested", 0) + 1
    elif etype == "approval_received":
        bucket["approvals_received"] = bucket.get("approvals_received", 0) + 1

    # ── Payload kind: llm_call ──
    if kind == "llm_call":
        bucket["llm_call_count"] = bucket.get("llm_call_count", 0) + 1
        tokens_in = pd.get("tokens_in") or 0
        tokens_out = pd.get("tokens_out") or 0
        cost = pd.get("cost") or 0
        bucket["llm_tokens_in"] = bucket.get("llm_tokens_in", 0) + tokens_in
        bucket["llm_tokens_out"] = bucket.get("llm_tokens_out", 0) + tokens_out
        bucket["llm_cost"] = round(bucket.get("llm_cost", 0) + cost, 6)

        # Track biggest prompt
        if tokens_in > bucket.get("llm_max_tokens_in", 0):
            bucket["llm_max_tokens_in"] = tokens_in
            bucket["llm_max_tokens_in_name"] = pd.get("name", "unknown")

        # Per-model breakdown
        model = pd.get("model", "unknown")
        models = bucket.setdefault("models", {})
        m = models.setdefault(model, {"calls": 0, "cost": 0, "tokens_in": 0, "tokens_out": 0})
        m["calls"] += 1
        m["cost"] = round(m["cost"] + cost, 6)
        m["tokens_in"] += tokens_in
        m["tokens_out"] += tokens_out

        # Per call-name breakdown
        call_name = pd.get("name", "unknown")
        by_name = bucket.setdefault("calls_by_name", {})
        cn = by_name.setdefault(call_name, {"count": 0, "tokens_in_sum": 0, "tokens_out_sum": 0, "cost_sum": 0})
        cn["count"] += 1
        cn["tokens_in_sum"] += tokens_in
        cn["tokens_out_sum"] += tokens_out
        cn["cost_sum"] = round(cn["cost_sum"] + cost, 6)

    # ── Payload kind: issue ──
    elif kind == "issue":
        action = pd.get("action", "reported")
        if action == "reported":
            bucket["issues_reported"] = bucket.get("issues_reported", 0) + 1
            cat = pd.get("category", "other")
            by_cat = bucket.setdefault("errors_by_category", {})
            by_cat[cat] = by_cat.get(cat, 0) + 1
        elif action == "resolved":
            bucket["issues_resolved"] = bucket.get("issues_resolved", 0) + 1


def update_model_hourly(bucket: dict, event: dict) -> None:
    """Increment a model_hourly bucket with data from one llm_call event."""
    payload = event.get("payload") or {}
    pd = payload.get("data") or {}

    tokens_in = pd.get("tokens_in") or 0
    tokens_out = pd.get("tokens_out") or 0
    cost = pd.get("cost") or 0
    dur = pd.get("duration_ms")

    bucket["call_count"] = bucket.get("call_count", 0) + 1
    bucket["tokens_in"] = bucket.get("tokens_in", 0) + tokens_in
    bucket["tokens_out"] = bucket.get("tokens_out", 0) + tokens_out
    bucket["cost"] = round(bucket.get("cost", 0) + cost, 6)
    bucket["last_updated"] = event.get("received_at") or event.get("timestamp")

    if dur is not None:
        bucket["duration_sum_ms"] = bucket.get("duration_sum_ms", 0) + dur
        bucket["duration_count"] = bucket.get("duration_count", 0) + 1

    # Biggest prompt
    if tokens_in > bucket.get("max_tokens_in", 0):
        bucket["max_tokens_in"] = tokens_in
        bucket["max_tokens_in_agent"] = event.get("agent_id", "unknown")
        bucket["max_tokens_in_name"] = pd.get("name", "unknown")

    # Per-agent breakdown
    agent_id = event.get("agent_id", "unknown")
    agents = bucket.setdefault("agents", {})
    a = agents.setdefault(agent_id, {"calls": 0, "cost": 0, "tokens_in": 0, "tokens_out": 0})
    a["calls"] += 1
    a["cost"] = round(a["cost"] + cost, 6)
    a["tokens_in"] += tokens_in
    a["tokens_out"] += tokens_out

    # Per call-name breakdown
    call_name = pd.get("name", "unknown")
    by_name = bucket.setdefault("calls_by_name", {})
    cn = by_name.setdefault(call_name, {"count": 0, "cost_sum": 0})
    cn["count"] += 1
    cn["cost_sum"] = round(cn["cost_sum"] + cost, 6)
```

### 4.2 Bucket lookup and creation

```python
from datetime import datetime, timezone


def _hour_key(timestamp_str: str) -> str:
    """Truncate an ISO 8601 timestamp to the hour boundary."""
    dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
    truncated = dt.replace(minute=0, second=0, microsecond=0)
    return truncated.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_or_create_bucket(table: list[dict], tenant_id: str,
                         key_field: str, key_value: str,
                         hour: str) -> dict:
    """Find existing bucket or create a new empty one."""
    for row in table:
        if (row.get("tenant_id") == tenant_id
                and row.get(key_field) == key_value
                and row.get("hour") == hour):
            return row
    # Create new bucket
    new_bucket = {
        "tenant_id": tenant_id,
        key_field: key_value,
        "hour": hour,
    }
    table.append(new_bucket)
    return new_bucket
```

### 4.3 Integration into ingest pipeline

In `app.py`, after Step 9 (agent cache update), add:

```python
# ── Step 9b: Update running aggregates ──
from backend.aggregator import (
    update_agent_hourly, update_model_hourly,
    get_or_create_bucket, _hour_key,
)

for ev in accepted_events:
    hour = _hour_key(ev["timestamp"])
    agent_id = ev.get("agent_id")

    # Agent hourly
    if agent_id:
        bucket = get_or_create_bucket(
            storage._tables["agent_hourly"],
            tenant_id, "agent_id", agent_id, hour
        )
        update_agent_hourly(bucket, ev)

    # Model hourly (only for llm_call events)
    payload = ev.get("payload") or {}
    if payload.get("kind") == "llm_call":
        model = (payload.get("data") or {}).get("model", "unknown")
        bucket = get_or_create_bucket(
            storage._tables["model_hourly"],
            tenant_id, "model", model, hour
        )
        update_model_hourly(bucket, ev)

# Persist after batch
storage._persist("agent_hourly")
storage._persist("model_hourly")
```

### 4.4 Storage registration

In `storage_json.py`, add to `TABLE_FILES`:

```python
TABLE_FILES = [
    "tenants",
    "api_keys",
    "users",
    "projects",
    "agents",
    "project_agents",
    "events",
    "alert_rules",
    "alert_history",
    "invites",
    "agent_hourly",     # ★ NEW
    "model_hourly",     # ★ NEW
]
```

This automatically creates the JSON files on startup and loads them into memory.

---

## 5. Pruning

### 5.1 Retention policy

| Table | Retention | Rationale |
|---|---|---|
| `agent_hourly` | 90 days | Covers quarterly trend analysis |
| `model_hourly` | 90 days | Same |

Compare to raw events: Free=7d, Pro=30d, Enterprise=90d. Aggregates always outlive raw events.

### 5.2 Prune function

Add to `storage_json.py`, called from the existing `_prune_loop`:

```python
async def prune_aggregates(self) -> dict[str, int]:
    """Remove aggregate buckets older than 90 days."""
    cutoff = (_now_utc() - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
    pruned = {}

    for table_name in ("agent_hourly", "model_hourly"):
        async with self._locks[table_name]:
            before = len(self._tables[table_name])
            self._tables[table_name] = [
                row for row in self._tables[table_name]
                if row.get("hour", "") >= cutoff
            ]
            after = len(self._tables[table_name])
            removed = before - after
            if removed > 0:
                self._persist(table_name)
            pruned[table_name] = removed

    return pruned
```

### 5.3 Hook into prune loop

In `_prune_loop()` in `app.py`, after `storage.prune_events()`:

```python
agg_result = await storage.prune_aggregates()
for table, count in agg_result.items():
    if count > 0:
        logger.info("Aggregate pruning: %s — %d buckets removed", table, count)
```

---

## 6. New API Endpoints

### 6.1 `GET /v1/insights/agents`

**Purpose:** Ranked agent comparison across a time range. Answers "most expensive," "most active," "most errors," with distributions and commentary.

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `range` | str | `"24h"` | Time range: 1h, 6h, 24h, 7d, 30d, 90d |
| `project_id` | str? | null | Filter by project |
| `sort` | str | `"cost"` | Ranking dimension: cost, tasks, errors, llm_calls |

**Response:**

```json
{
    "range": "7d",
    "agents": [
        {
            "agent_id": "lead-qualifier",
            "tasks_completed": 142,
            "tasks_failed": 8,
            "success_rate": 0.947,
            "avg_task_duration_ms": 4200,
            "llm_call_count": 426,
            "llm_cost": 1.87,
            "llm_tokens_in": 640000,
            "llm_tokens_out": 85000,
            "error_count": 12,
            "errors_by_type": {"RateLimitError": 8, "TimeoutError": 4},
            "errors_by_category": {"rate_limit": 8, "connectivity": 4},
            "top_models": [
                {"model": "claude-sonnet-4-20250514", "calls": 300, "cost": 1.62},
                {"model": "gpt-4o-mini-2024-07-18", "calls": 126, "cost": 0.25}
            ],
            "top_actions": [
                {"name": "web_search", "count": 89},
                {"name": "crm_lookup", "count": 67}
            ],
            "top_llm_calls": [
                {"name": "lead_scoring", "count": 142, "avg_tokens_in": 1500, "total_cost": 0.85},
                {"name": "enrichment", "count": 142, "avg_tokens_in": 800, "total_cost": 0.42}
            ]
        }
    ],
    "fleet_totals": {
        "total_cost": 3.21,
        "total_tasks": 380,
        "total_errors": 24,
        "total_llm_calls": 1140
    },
    "comparisons": {
        "cost": {
            "max_agent": "lead-qualifier",
            "min_agent": "data-pipeline",
            "max_value": 1.87,
            "min_value": 0.22,
            "avg_value": 1.07,
            "max_vs_avg": 1.75,
            "max_vs_min": 8.5
        },
        "errors": {
            "max_agent": "lead-qualifier",
            "min_agent": "data-pipeline",
            "max_value": 12,
            "min_value": 1,
            "avg_value": 8.0,
            "max_vs_avg": 1.5,
            "max_vs_min": 12.0
        }
    }
}
```

**Implementation:** Sum `agent_hourly` buckets within the requested time range, grouped by `agent_id`. Compute fleet totals and comparison ratios server-side.

### 6.2 `GET /v1/insights/models`

**Purpose:** LLM model comparison. Answers "which model costs the most," "biggest prompts," "which agents use which models."

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `range` | str | `"24h"` | Time range |
| `agent_id` | str? | null | Filter to one agent |

**Response:**

```json
{
    "range": "7d",
    "models": [
        {
            "model": "claude-sonnet-4-20250514",
            "call_count": 840,
            "tokens_in": 1260000,
            "tokens_out": 168000,
            "cost": 5.67,
            "avg_duration_ms": 1400,
            "max_tokens_in": 9600,
            "max_tokens_in_agent": "lead-qualifier",
            "max_tokens_in_name": "phase1_reasoning",
            "agents_using": [
                {"agent_id": "lead-qualifier", "calls": 420, "cost": 3.24},
                {"agent_id": "support-triage", "calls": 420, "cost": 2.43}
            ],
            "top_calls": [
                {"name": "lead_scoring", "count": 280, "cost_sum": 2.10},
                {"name": "phase1_reasoning", "count": 140, "cost_sum": 1.89}
            ]
        }
    ],
    "fleet_totals": {
        "total_cost": 7.82,
        "total_calls": 1140,
        "total_tokens_in": 1800000
    }
}
```

### 6.3 `GET /v1/insights/timeseries`

**Purpose:** Hourly time-series for any metric, pre-computed. Powers trend charts, busiest-hour analysis, and sparklines.

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `range` | str | `"24h"` | Time range |
| `agent_id` | str? | null | Filter to one agent (null = fleet) |
| `metric` | str | `"cost"` | Which metric: cost, tasks, errors, llm_calls, tokens |

**Response:**

```json
{
    "range": "7d",
    "agent_id": null,
    "metric": "cost",
    "buckets": [
        {"hour": "2026-02-08T14:00:00Z", "value": 0.42},
        {"hour": "2026-02-08T15:00:00Z", "value": 0.38},
        {"hour": "2026-02-08T16:00:00Z", "value": 0.51}
    ],
    "summary": {
        "total": 7.82,
        "avg_per_hour": 0.047,
        "peak_hour": "2026-02-12T10:00:00Z",
        "peak_value": 0.89,
        "trough_hour": "2026-02-09T04:00:00Z",
        "trough_value": 0.002
    }
}
```

**Implementation:** Read `agent_hourly` buckets for the range, optionally filter by `agent_id`, extract the requested metric from each bucket. For fleet view, sum across all agents per hour.

**Metric mapping:**

| `metric` param | Field from `agent_hourly` |
|---|---|
| `cost` | `llm_cost` |
| `tasks` | `tasks_completed` |
| `errors` | `actions_failed + tasks_failed` |
| `llm_calls` | `llm_call_count` |
| `tokens` | `llm_tokens_in + llm_tokens_out` |

### 6.4 `GET /v1/insights/errors`

**Purpose:** Error analysis. Answers "which agent has the most errors," "what types of errors," "where do they happen."

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `range` | str | `"24h"` | Time range |
| `agent_id` | str? | null | Filter to one agent |

**Response:**

```json
{
    "range": "7d",
    "total_errors": 24,
    "by_agent": [
        {
            "agent_id": "lead-qualifier",
            "error_count": 12,
            "task_failure_count": 4,
            "action_failure_count": 8,
            "by_type": {"RateLimitError": 8, "TimeoutError": 4},
            "by_category": {"rate_limit": 8, "connectivity": 4}
        }
    ],
    "by_type_global": {
        "RateLimitError": 15,
        "TimeoutError": 6,
        "ValidationError": 3
    },
    "by_category_global": {
        "rate_limit": 15,
        "connectivity": 6,
        "data_quality": 3
    },
    "error_timeseries": [
        {"hour": "2026-02-15T10:00:00Z", "count": 3},
        {"hour": "2026-02-15T11:00:00Z", "count": 0},
        {"hour": "2026-02-15T12:00:00Z", "count": 5}
    ]
}
```

### 6.5 `GET /v1/insights/prompts`

**Purpose:** Prompt size analysis. Answers "biggest prompts," "most called LLM functions," "token distribution by call name."

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `range` | str | `"24h"` | Time range |
| `agent_id` | str? | null | Filter to one agent |
| `sort` | str | `"tokens"` | Sort by: tokens, calls, cost |

**Response:**

```json
{
    "range": "7d",
    "calls": [
        {
            "name": "phase1_reasoning",
            "total_count": 280,
            "avg_tokens_in": 4200,
            "max_tokens_in": 9600,
            "total_tokens_in": 1176000,
            "total_tokens_out": 102000,
            "total_cost": 5.04,
            "agents_using": ["lead-qualifier", "support-triage"],
            "primary_model": "claude-sonnet-4-20250514"
        },
        {
            "name": "lead_scoring",
            "total_count": 420,
            "avg_tokens_in": 1500,
            "max_tokens_in": 2100,
            "total_tokens_in": 630000,
            "total_tokens_out": 84000,
            "total_cost": 2.52,
            "agents_using": ["lead-qualifier"],
            "primary_model": "claude-sonnet-4-20250514"
        }
    ],
    "biggest_prompt": {
        "name": "phase1_reasoning",
        "max_tokens_in": 9600,
        "agent": "lead-qualifier",
        "model": "claude-sonnet-4-20250514"
    }
}
```

**Implementation:** Merge `calls_by_name` dicts across all `agent_hourly` buckets in range. For `agents_using`, track which agent_ids contributed to each call name. For `primary_model`, cross-reference with `model_hourly.calls_by_name` or derive from `agent_hourly.models`.

### 6.6 `GET /v1/insights/actions`

**Purpose:** Action/tool usage distribution. Answers "which tools are used most," "busiest hours for each tool," "who uses what."

**Parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `range` | str | `"24h"` | Time range |
| `agent_id` | str? | null | Filter to one agent |
| `group_by` | str | `"name"` | Group by: name, agent, hour |

**Response (group_by=name):**

```json
{
    "range": "7d",
    "actions": [
        {
            "name": "web_search",
            "total_count": 312,
            "agents_using": {
                "lead-qualifier": 180,
                "support-triage": 132
            },
            "hourly_avg": 1.86,
            "peak_hour": "2026-02-12T10:00:00Z",
            "peak_count": 12
        },
        {
            "name": "crm_lookup",
            "total_count": 245,
            "agents_using": {
                "lead-qualifier": 245
            },
            "hourly_avg": 1.46,
            "peak_hour": "2026-02-13T14:00:00Z",
            "peak_count": 8
        }
    ]
}
```

**Response (group_by=hour):**

```json
{
    "range": "24h",
    "hours": [
        {
            "hour": "2026-02-15T08:00:00Z",
            "total_actions": 42,
            "breakdown": {
                "web_search": 15,
                "crm_lookup": 12,
                "email_draft": 8,
                "file_read": 7
            }
        }
    ]
}
```

---

## 7. Endpoint Summary

| Endpoint | Source Table | Answers |
|---|---|---|
| `GET /v1/insights/agents` | `agent_hourly` | Most expensive, most active, most errors, distributions |
| `GET /v1/insights/models` | `model_hourly` | Model comparison, biggest prompts, agent-model matrix |
| `GET /v1/insights/timeseries` | `agent_hourly` | Trend charts, busiest hours, sparklines |
| `GET /v1/insights/errors` | `agent_hourly` | Error analysis by type, category, agent, time |
| `GET /v1/insights/prompts` | `agent_hourly` + `model_hourly` | Prompt size ranking, call name analysis |
| `GET /v1/insights/actions` | `agent_hourly` | Tool/action usage distribution, hourly patterns |

All 6 endpoints read from pre-computed aggregates. No raw event scanning.

---

## 8. Rebuild from Raw Events

If aggregate tables are lost or need correction:

```python
async def rebuild_aggregates(storage) -> dict:
    """Rebuild agent_hourly and model_hourly from raw events."""
    storage._tables["agent_hourly"] = []
    storage._tables["model_hourly"] = []

    for ev in storage._tables["events"]:
        hour = _hour_key(ev["timestamp"])
        tenant_id = ev.get("tenant_id")
        agent_id = ev.get("agent_id")

        if agent_id:
            bucket = get_or_create_bucket(
                storage._tables["agent_hourly"],
                tenant_id, "agent_id", agent_id, hour
            )
            update_agent_hourly(bucket, ev)

        payload = ev.get("payload") or {}
        if payload.get("kind") == "llm_call":
            model = (payload.get("data") or {}).get("model", "unknown")
            bucket = get_or_create_bucket(
                storage._tables["model_hourly"],
                tenant_id, "model", model, hour
            )
            update_model_hourly(bucket, ev)

    storage._persist("agent_hourly")
    storage._persist("model_hourly")

    return {
        "agent_hourly_buckets": len(storage._tables["agent_hourly"]),
        "model_hourly_buckets": len(storage._tables["model_hourly"]),
    }
```

This could be exposed as an admin endpoint (`POST /v1/admin/rebuild-aggregates`) or run automatically on startup if the aggregate tables are empty but events exist.

---

## 9. Edge Cases

| Scenario | Behavior |
|---|---|
| Server restarts mid-hour | Buckets are persisted to JSON. On restart, `initialize()` loads them. New events in the same hour increment the existing bucket. |
| Event arrives with timestamp in a past hour | The bucket for that past hour is found (or created) and updated. This handles late-arriving events and out-of-order delivery. |
| Duplicate event (same event_id re-ingested) | The ingest pipeline already deduplicates before Step 8. Aggregates never see duplicates. |
| Agent ID changes mid-stream | Each agent_id gets its own buckets. If an agent registers with a new ID, it starts fresh in the aggregate. |
| Zero events in an hour | No bucket is created. The timeseries endpoint should fill gaps with zero-value buckets for chart continuity. |
| Very large `actions_by_name` dict (100+ unique names) | Possible if developers track many granular actions. Consider truncating to top-N in the query response, not in storage. |
| Clock skew between agents | Events are bucketed by `event.timestamp` (agent clock), not `received_at`. If an agent's clock is wrong, its buckets will be offset. This matches existing behavior. |
| Aggregate file corruption | On startup, if JSON parse fails, log error and initialize empty table. Rebuild from events if available. |

---

## 10. What We're NOT Doing

- **No daily rollups in v1.** Hourly buckets at 90-day retention are small enough. Daily rollup adds complexity (a scheduled job to merge 24 hourly buckets into 1 daily) for marginal benefit. Add later if storage becomes a concern.
- **No per-task aggregates.** Task-level detail is available from raw events (which are retained for 7-90 days depending on plan). If you need "cost breakdown for task X," query `/v1/tasks/{id}/timeline`.
- **No real-time sub-minute buckets.** The minimum granularity is 1 hour. For real-time monitoring, the WebSocket stream and existing `/v1/events` endpoint are the right tools.
- **No aggregation backfill on startup.** If aggregates are empty and events exist, the admin must explicitly call rebuild. Auto-rebuild on every startup would be slow for large event tables.
- **No aggregate versioning.** If the bucket schema changes (new fields), old buckets won't have the new fields. Query code should handle missing fields with defaults.

---

## 11. Files to Create / Modify

| File | Action | Description |
|---|---|---|
| `src/backend/aggregator.py` | **CREATE** | ~200 lines. `update_agent_hourly()`, `update_model_hourly()`, `get_or_create_bucket()`, `_hour_key()`, `rebuild_aggregates()` |
| `src/backend/storage_json.py` | **MODIFY** | Add `"agent_hourly"`, `"model_hourly"` to `TABLE_FILES`. Add `prune_aggregates()` method. |
| `src/backend/app.py` | **MODIFY** | Add aggregation call after Step 9 in ingest. Add `prune_aggregates()` to prune loop. Add 6 new `GET /v1/insights/*` endpoints. |
| `src/shared/models.py` | **MODIFY** | Add response models: `InsightsAgentResponse`, `InsightsModelResponse`, `InsightsTimeseriesResponse`, `InsightsErrorResponse`, `InsightsPromptResponse`, `InsightsActionResponse`. |
| `src/shared/enums.py` | **MODIFY** | Add `AGGREGATE_RETENTION_DAYS = 90`. Add `"90d"` to `RANGE_SECONDS` (7,776,000 seconds). |
| `data/agent_hourly.json` | **AUTO-CREATED** | Empty `[]` on first run |
| `data/model_hourly.json` | **AUTO-CREATED** | Empty `[]` on first run |

---

## 12. Testing Checklist

- [ ] Ingesting a `task_started` event increments `tasks_started` in the correct agent_hourly bucket
- [ ] Ingesting a `task_completed` with `duration_ms` updates both the counter and duration accumulator
- [ ] Ingesting an `llm_call` event updates agent_hourly (llm counters, models, calls_by_name) AND model_hourly
- [ ] Ingesting an `action_failed` event increments `actions_failed` and populates `errors_by_type`
- [ ] Ingesting an `issue` with `action=reported` increments `issues_reported` and `errors_by_category`
- [ ] Bucket lookup finds existing bucket for same (tenant, agent, hour)
- [ ] Bucket lookup creates new bucket when none exists
- [ ] Events with timestamps in different hours go to different buckets
- [ ] Events with timestamps in past hours update the correct past bucket
- [ ] `prune_aggregates()` removes buckets older than 90 days
- [ ] `prune_aggregates()` preserves buckets within 90 days
- [ ] `rebuild_aggregates()` produces identical results to incremental aggregation
- [ ] `GET /v1/insights/agents` returns ranked agents with correct totals for the requested range
- [ ] `GET /v1/insights/agents` comparisons (max_vs_avg, max_vs_min) are mathematically correct
- [ ] `GET /v1/insights/timeseries` fills zero-value gaps for hours with no events
- [ ] `GET /v1/insights/errors` correctly merges `errors_by_type` across hourly buckets
- [ ] `GET /v1/insights/prompts` correctly identifies biggest prompt and primary model
- [ ] `GET /v1/insights/actions` correctly computes peak hour and hourly average
- [ ] Server restart preserves aggregate state (buckets persist to JSON and reload)
- [ ] Empty aggregate tables return valid empty responses (not errors)
- [ ] 90-day range query works even though raw events may only cover 7 days

---

## 13. Answering the Original Questions

| Question | Endpoint | How |
|---|---|---|
| Most expensive agent (in time frame) | `GET /v1/insights/agents?range=7d&sort=cost` | First agent in sorted response |
| Least expensive agent | Same | Last agent in response |
| Cost distribution + "3X more than average" | Same | `comparisons.cost.max_vs_avg` |
| Most active agent + drill by task | `GET /v1/insights/agents?sort=tasks` | `tasks_completed` ranking + `top_llm_calls` |
| Most active agent + drill by tool | `GET /v1/insights/agents` + `GET /v1/insights/actions?agent_id=X` | `top_actions` per agent |
| Agent with most errors + drill by type | `GET /v1/insights/errors` | `by_agent[].by_type` |
| Errors by task/tool | `GET /v1/insights/errors?agent_id=X` | Error types map to actions/tasks |
| Prompts by size, who uses them | `GET /v1/insights/prompts?sort=tokens` | `calls[].avg_tokens_in`, `agents_using` |
| Tools used, qty per day/hour | `GET /v1/insights/actions?group_by=hour` | `actions_by_name` aggregated from hourly buckets |
| Busiest hours | `GET /v1/insights/timeseries?metric=tasks` | Peak detection from hourly buckets |

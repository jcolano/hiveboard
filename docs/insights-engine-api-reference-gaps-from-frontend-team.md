# Analytics Deep Dive — Backend Implementation Brief

**From:** Frontend / Product  
**To:** Backend Team  
**Date:** February 15, 2026  
**Status:** Ready for review  
**Visual Reference:** `analytics-deep-dive.html` (attached mockup with dummy data)

---

## What This Is

We're building a new **Analytics Deep Dive** page — 7 sections of ranked agent comparisons, error drilldowns, prompt analysis, tool/skill usage heatmaps, and fleet status. The mockup is fully built with dummy data and matches the existing HiveBoard design system.

This document maps every UI component to its API endpoint, identifies **8 gaps** where the current Insights Engine API doesn't provide what the frontend needs, and proposes concrete response shape changes for each.

**The goal:** after reading this, the backend team can turn each gap into a ticket with a clear spec.

---

## Data-Flow Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    ANALYTICS DEEP DIVE PAGE                     │
│                                                                 │
│  ┌─── Toolbar ──────────────────────────────────────────────┐   │
│  │  Range selector (1h|6h|24h|7d|30d) → passed to ALL calls│   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S0: Fleet Status ────────────────────────────────────┐   │
│  │  GET /v1/agents ─────────────── status, heartbeat, name  │   │
│  │  GET /v1/insights/agents ────── cost per agent (join)    │   │
│  │  ⚠️  GAP #1: last_event_type                             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S1: Cost Rankings ───────────────────────────────────┐   │
│  │  GET /v1/insights/agents?sort=cost ──── fully covered ✅  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S2: Activity Rankings ───────────────────────────────┐   │
│  │  GET /v1/insights/agents?sort=tasks ─── agent ranking    │   │
│  │  GET /v1/insights/timeseries?metric=tasks ── peak hour   │   │
│  │  GET /v1/insights/actions?agent_id=X ── tool drilldown   │   │
│  │  ⚠️  GAP #2: cost per action                             │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S3: Error Analysis ──────────────────────────────────┐   │
│  │  GET /v1/insights/errors ──────── by_agent, by_type      │   │
│  │  GET /v1/insights/agents ──────── task totals (for rate)  │   │
│  │  ⚠️  GAP #3: errors by_action                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S4: Prompt Analysis ─────────────────────────────────┐   │
│  │  GET /v1/insights/prompts?sort=tokens ── fully covered ✅ │   │
│  │  (GAP #4 is cosmetic — call name ≈ prompt identity)      │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S5: Tool Usage Tracker ──────────────────────────────┐   │
│  │  GET /v1/insights/actions ──────── summary pills          │   │
│  │  ⚠️  GAP #5: hourly_buckets per action (heatmap)         │   │
│  │  ⚠️  GAP #6: daily aggregation per action (weekly table) │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌─── S6: Skill Usage Tracker ─────────────────────────────┐   │
│  │  GET /v1/insights/actions ──────── reuses same endpoint   │   │
│  │  ⚠️  GAP #7: skills vs actions distinction               │   │
│  │  ⚠️  GAP #8: avg_duration_ms per action                  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Endpoint Call Plan (Per Page Load)

On initial load, the frontend makes **5 parallel requests** (all with the selected `range`):

| # | Endpoint | Purpose |
|---|----------|---------|
| 1 | `GET /v1/agents` | Fleet status rows (S0) + agent dropdown |
| 2 | `GET /v1/insights/agents?sort=cost` | Cost rankings (S1), Activity rankings (S2), error rates |
| 3 | `GET /v1/insights/errors` | Error analysis (S3) |
| 4 | `GET /v1/insights/prompts?sort=tokens` | Prompt analysis (S4) |
| 5 | `GET /v1/insights/actions` | Tool usage (S5) + Skill usage (S6) |

**On drilldown** (user clicks an agent for deeper investigation):

| # | Endpoint | Purpose |
|---|----------|---------|
| 6 | `GET /v1/insights/actions?agent_id=X` | Tool/task breakdown for that agent |
| 7 | `GET /v1/insights/errors?agent_id=X` | Error breakdown for that agent |
| 8 | `GET /v1/insights/timeseries?metric=tasks` | Peak hour for activity section |

Total: **5 calls on load**, up to **3 more on drilldown**. Very manageable.

---

## Section-by-Endpoint Mapping (Detail)

### S0: Fleet Status — "Who's Alive Right Now?"

| UI Component | Endpoint | Fields Used |
|---|---|---|
| Status strip (3 Running / 2 Idle / 1 Stopped) | `GET /v1/agents` | Group by `derived_status`, count |
| Agent rows: name | same | `agent_id` |
| Agent rows: heartbeat dot + age | same | `last_heartbeat`, `heartbeat_age_seconds`, `is_stuck` |
| Agent rows: status badge | same | `derived_status` |
| Agent rows: last event type + time | **⚠️ GAP #1** | — |
| Agent rows: 24h cost | `GET /v1/insights/agents` | `agents[].llm_cost` (joined client-side by `agent_id`) |
| Agent rows: cost-per-task | derived | `llm_cost / tasks_completed` |
| Cost by Status summary cards | both above | Group insights costs by agent status |

---

### S1: Cost Rankings — ✅ Fully Covered

**Single call:** `GET /v1/insights/agents?sort=cost`

| UI Component | Fields Used |
|---|---|
| Most Expensive card | `comparisons.cost.max_agent`, `max_value` |
| Least Expensive card | `comparisons.cost.min_agent`, `min_value` |
| Fleet Average | `fleet_totals.total_cost / agents.length` |
| Cost Spread (max/min ratio) | `comparisons.cost.max_vs_min`, `max_vs_avg` |
| Distribution strip | `agents[].llm_cost / fleet_totals.total_cost` → % |
| Ranked bar chart | `agents[].agent_id`, `agents[].llm_cost` |
| Commentary (model used) | `agents[0].top_models[0].model` (most expensive agent's primary model) |
| LLM calls + tokens | `agents[].llm_call_count`, `llm_tokens_in + llm_tokens_out` |

No backend changes needed.

---

### S2: Activity Rankings

| UI Component | Endpoint | Fields Used |
|---|---|---|
| Most/Least Active | `/v1/insights/agents?sort=tasks` | `comparisons.tasks.*` |
| Fleet total tasks | same | `fleet_totals.total_tasks` |
| Tasks/hr avg | derived | `tasks_completed / hours_in_range` |
| Peak hour | `/v1/insights/timeseries?metric=tasks` | `summary.peak_hour` |
| Ranked bars | `/v1/insights/agents?sort=tasks` | `agents[].tasks_completed` |
| Drilldown: By Task Type | same | `agents[X].tasks_by_type` |
| Drilldown: By Tool | `/v1/insights/actions?agent_id=X` | `actions[].name`, `total_started` |
| Commentary: cost per task type | **⚠️ GAP #2** | — |

---

### S3: Error Analysis

| UI Component | Endpoint | Fields Used |
|---|---|---|
| Most/Fewest Errors cards | `/v1/insights/errors` | `by_agent[0]`, `by_agent[last]` |
| Fleet Error Rate | derived | `total_errors / fleet_totals.total_tasks` (from agents endpoint) |
| Top Error Type | `/v1/insights/errors` | `by_type_global` → key with max count |
| Ranked error bars by agent | same | `by_agent[].agent_id`, `error_count` |
| Drilldown: By Error Type | same or `?agent_id=X` | `by_agent[].by_type` |
| Drilldown: By Category | same | `by_agent[].by_category` |
| Drilldown: By Task | **⚠️ GAP #3** | — |
| Drilldown: By Tool | **⚠️ GAP #3** | — |

---

### S4: Prompt Analysis — ✅ Fully Covered

**Single call:** `GET /v1/insights/prompts?sort=tokens`

| UI Component | Fields Used |
|---|---|
| Table: Prompt name | `calls[].name` |
| Table: Avg Tokens | `calls[].avg_tokens_in` |
| Table: Calls count | `calls[].total_count` |
| Table: Agent(s) | `calls[].agents_using` |
| Table: Model | `calls[].primary_model` |
| Table: Est. Cost | `calls[].total_cost` |
| Biggest prompt highlight | `biggest_prompt.*` |

Note on **GAP #4**: the mockup shows a "Task / Tool" column (e.g. "diff_review · git_diff"). The call `name` (e.g. `draft_outreach_email`) effectively *is* the prompt identity and is sufficient. Treat as cosmetic — no backend change needed unless you want an explicit `triggered_by_action` field later.

---

### S5: Tool Usage Tracker

| UI Component | Endpoint | Fields Used |
|---|---|---|
| Usage summary pills | `/v1/insights/actions` | `actions[].name`, `total_started`, `hourly_avg` |
| Hourly heatmap (24 cols × N tools) | **⚠️ GAP #5** | — |
| Weekly aggregation table (Mon–Sun) | **⚠️ GAP #6** | — |
| Trend badges | derived | Compare current vs previous range (2 calls) |

---

### S6: Skill Usage Tracker

| UI Component | Endpoint | Fields Used |
|---|---|---|
| Skill table: uses, agents, success rate | `/v1/insights/actions` | `total_started`, `agents_using`, `success_rate` |
| Skill table: avg duration | **⚠️ GAP #8** | — |
| Skill table: peak hours | `/v1/insights/actions` | `peak_hour` |
| Hourly heatmap | **⚠️ GAP #5** (same as S5) | — |
| Skills vs Actions distinction | **⚠️ GAP #7** | — |

---

## Gap Specifications

### GAP #1: Last Event Type on Agent Status

**Severity:** Low  
**Section:** S0 (Fleet Status)  
**Endpoint:** `GET /v1/agents`

**Problem:** The mockup shows "Last event: `task_completed` · 00:00:12 ago" per agent. The current response has `last_heartbeat` and `heartbeat_age_seconds` but not the most recent event type or timestamp.

**Proposed addition** — add 2 fields to each agent object:

```json
{
  "agent_id": "scout",
  "derived_status": "processing",
  "last_heartbeat": "2026-02-15T04:58:00Z",
  "heartbeat_age_seconds": 12,
  
  "last_event_type": "task_completed",      // ← NEW
  "last_event_at": "2026-02-15T04:57:48Z"   // ← NEW
  
  // ... rest unchanged
}
```

**Implementation hint:** This is the most recent event for that `agent_id` — a simple `ORDER BY timestamp DESC LIMIT 1` on the events table filtered by `agent_id`. It could also be cached/updated during event ingestion (Step 7b) for zero-query cost.

**Fallback if skipped:** Frontend shows heartbeat age only. Acceptable but less informative.

---

### GAP #2: Cost per Action

**Severity:** Low  
**Section:** S2 (Activity Rankings — commentary)  
**Endpoint:** `GET /v1/insights/actions`

**Problem:** The commentary says "web_search costs $0.14/call avg." Actions endpoint has counts and success rates but no cost data.

**Proposed addition** — add 2 fields to each action object:

```json
{
  "name": "enrich_company_data",
  "total_started": 8,
  "total_completed": 8,
  "total_failed": 0,
  "success_rate": 100.0,
  "agents_using": { "scout": 8 },
  "hourly_avg": 8.0,
  "peak_hour": "2026-02-15T04:00:00Z",
  "peak_count": 8,
  
  "total_cost": 0.0842,         // ← NEW: sum of LLM costs during this action
  "avg_cost_per_start": 0.0105  // ← NEW: total_cost / total_started
}
```

**Implementation hint:** The `agent_hourly` table tracks costs by agent. If action events carry `agent_id` + timestamps, the cost attribution can be derived by summing LLM costs that fall within action start/end windows. Alternatively, if actions trigger LLM calls with a known `call_name`, sum `cost` from `model_hourly` grouped by that call name.

**Fallback if skipped:** Frontend derives approximate cost from `top_llm_calls` on the agents endpoint. Less accurate but workable.

---

### GAP #3: Errors by Action/Task

**Severity:** Medium  
**Section:** S3 (Error Analysis — drilldowns)  
**Endpoint:** `GET /v1/insights/errors`

**Problem:** The mockup shows error counts broken down by task (e.g. "lint_check: 28") and by tool (e.g. "ast_parser: 24"). The errors endpoint gives `by_type` and `by_category` per agent, but not `by_action` or `by_task`.

**Proposed addition** — add 2 dicts to each `by_agent` object:

```json
{
  "by_agent": [
    {
      "agent_id": "scout",
      "error_count": 3,
      "task_failure_count": 1,
      "action_failure_count": 2,
      "by_type": { "RateLimitError": 2, "TimeoutError": 1 },
      "by_category": { "rate_limit": 2, "connectivity": 1 },
      
      "by_task_type": {                    // ← NEW
        "lead_qualification": 2,
        "data_enrichment": 1
      },
      "by_action": {                       // ← NEW
        "enrich_company_data": 2,
        "search_kb": 1
      }
    }
  ]
}
```

**Implementation hint:** During aggregation, when a `task_failed` or `action_failed` event is processed, increment counters keyed by `task_type` and `action_name` in the same `agent_hourly` bucket. These are already available in the event payload.

**Fallback if skipped:** Frontend shows only by-type and by-category drilldowns (both fully supported). The "by task" and "by tool" columns would be removed from the mockup. Acceptable but loses a valuable "where exactly is it breaking?" signal.

---

### GAP #4: Triggered-by-Action for Prompts

**Severity:** Very Low — cosmetic  
**Section:** S4 (Prompt Analysis)  
**Endpoint:** `GET /v1/insights/prompts`

**Problem:** Mockup shows a "Task / Tool" column next to each prompt. The API gives `calls[].name` which is effectively the prompt identity (e.g. `draft_outreach_email`), and `primary_model`.

**Decision:** No backend change needed. The frontend will use `calls[].name` as both the prompt label and the implicit task/tool reference. If we later want explicit linkage, a `triggered_by_actions: ["action_name"]` field could be added.

---

### GAP #5: Hourly Buckets per Action (Heatmap Data)

**Severity:** High — blocks the heatmaps in S5 and S6  
**Section:** S5 (Tool Usage), S6 (Skill Usage)  
**Endpoint:** `GET /v1/insights/actions`

**Problem:** The mockup renders a 24-column heatmap showing call counts per hour per tool/skill. The actions endpoint gives `peak_hour` and `peak_count` (single values) but not the full hourly distribution.

**Proposed addition — Option A (preferred):** Add `hourly_buckets` to each action:

```json
{
  "name": "enrich_company_data",
  "total_started": 8,
  "total_completed": 8,
  // ... existing fields ...
  
  "hourly_buckets": [                      // ← NEW
    { "hour": "2026-02-14T05:00:00Z", "started": 0, "completed": 0, "failed": 0 },
    { "hour": "2026-02-14T06:00:00Z", "started": 3, "completed": 3, "failed": 0 },
    { "hour": "2026-02-14T07:00:00Z", "started": 5, "completed": 5, "failed": 0 }
  ]
}
```

**Proposed addition — Option B:** Implement the reserved `group_by=hour` parameter:

```
GET /v1/insights/actions?range=24h&group_by=hour
```

Returns a restructured response with actions nested under each hour bucket. Richer but harder to render as a heatmap.

**Recommendation:** Option A. The frontend already expects action-level objects — adding `hourly_buckets[]` to each one means no restructuring. Zero-gap filling (same as timeseries endpoint) makes it directly renderable as heatmap cells.

**Implementation hint:** The `agent_hourly` table already has hourly granularity. If action names are tracked in those buckets, this is a simple GROUP BY. If not, a new `action_hourly` counter (or a JSONB column in `agent_hourly`) would be needed.

**Fallback if skipped:** Frontend shows the summary pills and peak-hour badge only. The heatmaps are replaced with a simpler "peak hours" bar visualization. Significant loss of value — the heatmaps are a differentiating feature.

---

### GAP #6: Daily Aggregation per Action (Weekly Table)

**Severity:** Medium — but solved automatically if GAP #5 is implemented  
**Section:** S5 (Tool Usage — weekly table)

**Problem:** The mockup shows a Mon–Sun table with per-tool counts for each day.

**If GAP #5 is implemented:** Frontend rolls up `hourly_buckets` into daily totals client-side. **No additional backend work needed.** Call with `range=7d`, get 168 hourly buckets per action, aggregate into 7 daily columns.

**If GAP #5 is NOT implemented:** Would need a separate `GET /v1/insights/actions?range=7d&group_by=day` or similar. Not recommended as a standalone effort — better to solve via GAP #5.

---

### GAP #7: Skills vs Actions Distinction

**Severity:** Decision required — affects data model  
**Section:** S6 (Skill Usage)

**Problem:** The mockup has separate sections for "tools" (brave_search, pdf_reader, ast_parser) and "skills" (web_research, code_review, content_writing). The API has a single `/v1/insights/actions` endpoint.

**Question for backend team:** How do we distinguish skills from tools?

**Option A — Same endpoint, tagged:** Add a `type` field to action events (`type: "tool"` vs `type: "skill"`) and a `?type=` filter on the actions endpoint:

```
GET /v1/insights/actions?type=tool    → tools only
GET /v1/insights/actions?type=skill   → skills only
GET /v1/insights/actions              → all (default, backwards compatible)
```

Response adds:
```json
{
  "name": "web_research",
  "type": "skill",           // ← NEW
  // ... rest unchanged
}
```

**Option B — Skills = task types:** Treat skills as a grouping of `tasks_by_type` from `/v1/insights/agents`. Frontend maps task types to skill names. No backend change, but less flexible.

**Option C — Collapse into one section:** Remove the skill/tool distinction in the mockup. Show all actions in one table. Simplest, no backend change.

**Recommendation:** Option A if skills are a first-class HiveLoop concept. Option C if they're not tracked separately today.

---

### GAP #8: Duration per Action

**Severity:** Low  
**Section:** S6 (Skill Usage — Avg Duration column)  
**Endpoint:** `GET /v1/insights/actions`

**Problem:** The mockup shows avg duration per skill (3.2s, 8.1s, etc.). The actions endpoint doesn't include timing.

**Proposed addition:**

```json
{
  "name": "enrich_company_data",
  "total_started": 8,
  "total_completed": 8,
  // ... existing fields ...
  
  "avg_duration_ms": 1240,    // ← NEW: avg(completed_at - started_at)
  "p95_duration_ms": 2890     // ← NEW (optional): 95th percentile
}
```

**Implementation hint:** Computed from `action_started` / `action_completed` event pairs. If aggregated hourly, store sum of durations + count, then derive avg at query time.

**Fallback if skipped:** Frontend hides the duration column. Acceptable — it's nice-to-have context.

---

## Priority Summary

### Must-have for v1 launch

| Gap | Change | Effort Estimate |
|---|---|---|
| **#5** | `hourly_buckets[]` on `/v1/insights/actions` | Medium — may need new aggregation counter |
| **#3** | `by_task_type` + `by_action` on `/v1/insights/errors` | Small — extend existing aggregation |

### Should-have (improves quality significantly)

| Gap | Change | Effort Estimate |
|---|---|---|
| **#1** | `last_event_type` + `last_event_at` on `/v1/agents` | Small — single query or ingest-time cache |
| **#8** | `avg_duration_ms` on `/v1/insights/actions` | Small — action event pairs |
| **#2** | `total_cost` on `/v1/insights/actions` | Small — sum LLM costs during actions |

### Nice-to-have / Deferred

| Gap | Change | Effort Estimate |
|---|---|---|
| **#7** | Skills vs actions distinction | Decision + possible schema addition |
| **#6** | Weekly aggregation | Free if #5 is done |
| **#4** | Triggered-by-action on prompts | Cosmetic — skip for now |

---

## Frontend Commitments

Once the backend ships these changes, the frontend will:

1. Replace all dummy data with live API calls (5 parallel on load + 3 on drilldown)
2. Wire the range selector to pass `?range=` to all endpoints
3. Implement client-side joins (agent status + cost data for S0)
4. Build client-side daily rollup from hourly buckets (S5 weekly table)
5. Generate HiveMind commentary from `comparisons` object + derived ratios
6. Handle empty states using the standard `agents: []` / `actions: []` patterns

---

## Files for Reference

| File | Description |
|---|---|
| `analytics-deep-dive.html` | Full mockup with dummy data — open in browser to see all 7 sections |
| `insights-engine-api-reference.md` | Current API spec (the source doc for this analysis) |
| This document | The gap analysis + proposed changes |

---

*Questions? Tag the frontend team. We're ready to wire things up as soon as the endpoints are updated.*




DETAILED REPORT FROM FRONT END TEAM:

## Section-to-Endpoint Mapping

### Section 0: Fleet Status — "Who's Alive Right Now?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Status strip (Running/Idle/Stopped counts) | `GET /v1/agents` | `derived_status` → group & count |
| Agent rows (name, status, heartbeat age) | `GET /v1/agents` | `agent_id`, `derived_status`, `last_heartbeat`, `heartbeat_age_seconds`, `is_stuck` |
| Last event type + time ago | **⚠️ GAP** — see below | — |
| Cost per agent (24h) | `GET /v1/insights/agents?range=24h&sort=cost` | `agents[].llm_cost` |
| Cost-per-task | `GET /v1/insights/agents` | `agents[].llm_cost / agents[].tasks_completed` (derived) |
| Cost by Status summary cards | Both above, joined client-side | Group `insights/agents` costs by `agents` status |

**⚠️ Gap: "Last event type + hh:mm:ss ago"** — `/v1/agents` gives `last_heartbeat` and `heartbeat_age_seconds`, but not the *last event type* (e.g. `task_completed`, `llm_call_end`, `agent_error`). The mockup shows this. **Options:** (a) add a `last_event_type` and `last_event_at` field to `GET /v1/agents`, (b) use a separate recent-events call, or (c) drop this detail and just show heartbeat age. What's your preference?

---

### Section 1: Cost Rankings — "Who's Spending What?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Most/Least Expensive cards | `GET /v1/insights/agents?sort=cost` | `comparisons.cost.max_agent`, `min_agent`, `max_value`, `min_value`, `avg_value` |
| Fleet Average card | same | `fleet_totals.total_cost`, derive avg from `agents.length` |
| Cost Spread (max/min ratio) | same | `comparisons.cost.max_vs_min`, `comparisons.cost.max_vs_avg` |
| Distribution strip + ranked bars | same | `agents[].agent_id`, `agents[].llm_cost`, `fleet_totals.total_cost` (derive %) |
| LLM call count + token totals | same | `agents[].llm_call_count`, `agents[].llm_tokens_in + llm_tokens_out` |
| Commentary (model used) | same | `agents[].top_models[0].model` for the most expensive agent |

✅ **Fully covered** by a single call to `/v1/insights/agents?sort=cost`.

---

### Section 2: Activity Rankings — "Who's Doing the Most Work?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Most/Least Active cards | `GET /v1/insights/agents?sort=tasks` | `comparisons.tasks.max_agent`, `min_agent`, etc. |
| Fleet total tasks | same | `fleet_totals.total_tasks` |
| Peak hour | `GET /v1/insights/timeseries?metric=tasks` | `summary.peak_hour` |
| Tasks/hr avg per agent | **⚠️ Derived** | `agents[].tasks_completed / hours_in_range` (client-side) |
| Ranked bars (tasks completed) | `/v1/insights/agents?sort=tasks` | `agents[].tasks_completed` |
| Drilldown: By Task Type | same | `agents[].tasks_by_type` (for the selected agent) |
| Drilldown: By Tool/Action | `GET /v1/insights/actions?agent_id=X` | `actions[].name`, `total_started`, `total_completed` |
| Drilldown: By LLM call name | `/v1/insights/agents` | `agents[].top_actions` (top 5 actions) |

✅ **Mostly covered.** The "By Task" drilldown maps to `tasks_by_type` and `top_actions` from `/v1/insights/agents`. The "By Tool" drilldown maps to `/v1/insights/actions?agent_id=X`.

**⚠️ Minor gap:** The mockup shows cost-per-task-type (e.g. "$0.14/call avg for web_search"). Neither `tasks_by_type` nor `/v1/insights/actions` includes cost. Only `top_llm_calls` has cost, but it's keyed by LLM call name, not task/action name. Would you want to add a `cost` field to the actions endpoint, or should I derive it via `top_llm_calls`?

---

### Section 3: Error Analysis — "Where Are Things Breaking?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Most Errors / Fewest Errors cards | `GET /v1/insights/errors` | `by_agent[0]` (sorted desc), `by_agent[last]` |
| Error rates | `GET /v1/insights/agents` + `errors` | `error_count / (tasks_completed + tasks_failed)` (derived) |
| Fleet Error Rate | `/v1/insights/errors` | `total_errors` / `fleet_totals.total_tasks` from agents |
| Top Error Type | `/v1/insights/errors` | `by_type_global` → highest count key |
| Ranked error bars by agent | `/v1/insights/errors` | `by_agent[].agent_id`, `by_agent[].error_count` |
| Drilldown: By Error Type | `/v1/insights/errors?agent_id=X` | `by_agent[].by_type` |
| Drilldown: By Category | same | `by_agent[].by_category` |
| Drilldown: By Task | **⚠️ GAP** — see below | — |
| Drilldown: By Tool | **⚠️ GAP** — see below | — |

**⚠️ Gap: "Errors by Task" and "Errors by Tool"** — The mockup shows which tasks and tools produce errors for a given agent (e.g. "lint_check: 28 errors", "ast_parser: 24 errors"). The errors endpoint gives `by_type` and `by_category`, but not `by_task` or `by_action`. **Options:** (a) add `by_task` and `by_action` dicts to the errors endpoint response, (b) cross-reference `/v1/insights/actions?agent_id=X` using `total_failed` as a proxy, or (c) simplify the mockup to only show by-type and by-category drilldowns (which the API fully supports).

Option (b) is partially viable — `/v1/insights/actions` has `total_failed` per action, which gives "errors by tool." But it doesn't break down error *types* per tool.

---

### Section 4: Prompt Analysis — "Size, Frequency & Attribution"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Prompt table (ranked by size) | `GET /v1/insights/prompts?sort=tokens` | `calls[].name`, `avg_tokens_in`, `max_tokens_in`, `total_count` |
| Agent(s) column | same | `calls[].agents_using` |
| Task/Tool column | **⚠️ Partial** — see below | `calls[].primary_model` (model, not task/tool) |
| Est. Cost column | same | `calls[].total_cost` |
| Biggest prompt highlight | same | `biggest_prompt.name`, `biggest_prompt.max_tokens_in` |

**⚠️ Gap: "Task / Tool" column** — The mockup shows which task and tool generates each prompt (e.g. "diff_review · git_diff"). The prompts endpoint gives the LLM call `name`, `agents_using`, and `primary_model`, but doesn't tell you which action/task triggered that call. The call `name` (e.g. `draft_outreach_email`) is itself the closest proxy — it's essentially the prompt identifier. Should I treat the call name as sufficient, or do you want an explicit `triggered_by_action` field added to the prompts response?

---

### Section 5: Tool Usage Tracker — "Frequency, Timing & Patterns"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Usage summary pills (count + rate/hr) | `GET /v1/insights/actions` | `actions[].name`, `total_started`, `hourly_avg` |
| Hourly heatmap | **⚠️ GAP** — see below | — |
| Weekly aggregation table | **⚠️ GAP** — see below | — |
| Trend badges | **⚠️ Derived** | Compare current vs previous period (needs 2 calls or client math) |

**⚠️ Gap: Hourly heatmap** — The mockup shows a 24-column heatmap of calls per hour per tool. `/v1/insights/actions` gives `peak_hour` and `peak_count` but not the full hourly distribution per action. **Options:** (a) add an `hourly_buckets` array to each action in the actions endpoint, (b) use `/v1/insights/timeseries?metric=llm_calls` for fleet-wide hourly data (but it doesn't break down by tool), or (c) use `group_by=hour` (noted as "reserved for future" in the API) once implemented.

**⚠️ Gap: Weekly aggregation (day-by-day)** — The mockup shows Mon–Sun columns per tool. The timeseries endpoint gives hourly buckets which *could* be aggregated client-side into daily totals, but only for a single metric (not per-tool). Getting per-tool-per-day would require calling `/v1/insights/timeseries` once per tool with a filter — but there's no `action_name` filter on that endpoint. Same `group_by=hour` future feature would help here.

---

### Section 6: Skill Usage Tracker — "Who Uses What & When?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Skill table (uses, agents, duration, success rate) | `GET /v1/insights/actions` | `actions[].name`, `total_started`, `agents_using`, `success_rate` |
| Avg Duration column | **⚠️ GAP** | Not in actions endpoint |
| Peak Hours column | `/v1/insights/actions` | `peak_hour` (single value, not a range) |
| Hourly skill heatmap | Same gap as Section 5 | — |

**⚠️ Gap: "Skills" vs "Actions/Tools"** — The mockup distinguishes between "tools" (brave_search, pdf_reader) and "skills" (web_research, code_review, content_writing). The API has a single `/v1/insights/actions` endpoint covering actions. Are skills tracked as a separate event type, or are they a higher-level grouping of actions? If skills = task types, then `tasks_by_type` from `/v1/insights/agents` might be the source. If skills are their own thing, a new endpoint or a `type` filter on actions would be needed.

**⚠️ Gap: Duration per action** — The mockup shows avg duration per skill (3.2s, 8.1s, etc.). `/v1/insights/actions` doesn't include duration. `/v1/insights/agents` has `avg_task_duration_ms` but at agent level, not per-action. You'd need `avg_duration_ms` added to the actions endpoint.

---

## Summary of Gaps

| # | Gap | Severity | Recommended Fix |
|---|---|---|---|
| 1 | **Last event type** on Fleet Status | Low | Add `last_event_type` + `last_event_at` to `GET /v1/agents` |
| 2 | **Cost per action/task** | Low | Add `cost` to `/v1/insights/actions` response, or derive from `top_llm_calls` |
| 3 | **Errors by task/action** | Medium | Add `by_action` dict to `/v1/insights/errors` response |
| 4 | **Triggered-by-action** for prompts | Low | Treat call `name` as sufficient (it basically is the prompt identity) |
| 5 | **Hourly buckets per action** | Medium | Implement `group_by=hour` on `/v1/insights/actions`, or add `hourly_buckets[]` per action |
| 6 | **Daily/weekly aggregation per tool** | Medium | Same as #5, client can roll up hourly→daily |
| 7 | **Skills vs Actions distinction** | Decision needed | Clarify: are skills = task types, or a new tracking concept? |
| 8 | **Duration per action** | Low | Add `avg_duration_ms` to `/v1/insights/actions` |

Gaps 5 and 6 are the biggest — the heatmaps in Sections 5 and 6 need hourly granularity per tool/skill, which the current API doesn't provide. Everything else is either minor additions or client-side derivation.

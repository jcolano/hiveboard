# Insights Tab: 38 Questions vs. Available Data

## Overview

Analysis of all 38 questions from `docs/presentation/3_What-HiveBoard-Sees.md` mapped against the HiveBoard backend API to determine data availability, API sources, required processing, and UI/UX concepts for a new Insights tab.

---

## Moment 1: The Glance (5 questions) — All Global

| # | Question | Data Available? | API Source | UI Concept |
|---|----------|----------------|------------|------------|
| 1 | Are my agents running? | **Yes** | `GET /v1/agents` → `derived_status`, `last_heartbeat`, `heartbeat_age_seconds` | Row of agent badges with colored dots (green/amber/red). One glance. |
| 2 | Does anything need my attention? | **Yes** | `GET /v1/agents` → filter `is_stuck`, status `error`/`waiting_approval` + pipeline `active_issues` | Single attention pill: "3 need attention" with breakdown tooltip. |
| 3 | Is anything stuck? | **Yes** | `GET /v1/agents` → `is_stuck` count | Counter card. "0 Stuck" in green or "2 Stuck" pulsing red. |
| 4 | Is work flowing? | **Yes** | `GET /v1/metrics` → `timeseries[]` (throughput, success rate, errors, cost per bucket) | 4 sparkline mini-charts: throughput, success rate, errors, cost/task. Shapes, not numbers. |
| 5 | Is anything happening right now? | **Yes** | `GET /v1/events` (recent, limit 5) or WebSocket stream | Compact activity ticker — last 3-5 events scrolling. "Live" dot if WebSocket connected. |

**Processing needed for #4:** The metrics timeseries returns buckets with `tasks_completed`, `tasks_failed`, `cost`, `throughput`. We need a simple sparkline renderer (canvas or inline SVG). No heavy processing — just plot the raw bucket values.

---

## Moment 2: The Investigation (11 questions) — Per-Agent / Per-Task

| # | Question | Scope | Data Available? | API Source | UI Concept |
|---|----------|-------|----------------|------------|------------|
| 6 | What is this agent doing right now? | Per-agent | **Yes** | `GET /v1/agents/{id}` → `derived_status`, `current_task_id` | Agent detail card: status badge + current task link + elapsed time. |
| 7 | Is the heartbeat healthy? | Per-agent | **Partial** | `GET /v1/agents/{id}` → `heartbeat_age_seconds` + `GET /v1/events?event_type=heartbeat&agent_id=X` for history | Heartbeat indicator (green/amber/red) + sparkline from heartbeat event timestamps. Need to fetch heartbeat events and bucket them. |
| 8 | Does this agent have pending work? | Per-agent | **Yes** | `GET /v1/agents/{id}/pipeline` → `queue.depth`, `queue.items[]`, `queue.oldest_age_seconds` | Queue badge "Q:4" on agent card. Expand to show items with age, priority, summary. |
| 9 | Has the agent reported its own problems? | Per-agent | **Yes** | `GET /v1/agents/{id}/pipeline` → `issues[]` with `severity`, `occurrence_count`, `summary`, `context` | Issue list with severity color bars, occurrence count badges, expandable context. |
| 10 | What steps did this task take? | Per-task | **Yes** | `GET /v1/tasks/{task_id}/timeline` → `events[]`, `action_tree[]` | Already built in Mission Control timeline. Link to it. |
| 11 | What was the plan, and where did it fail? | Per-task | **Yes** | `GET /v1/tasks/{task_id}/timeline` → `plan` (goal, steps with status, progress) | Plan progress bar: green/blue/red/gray segments. Already partially built. |
| 12 | Which tool failed? | Per-task | **Yes** | Timeline → `action_tree[]` where `status=failed` | Red nodes in timeline with tool name, error, duration. Already built. |
| 13 | Which LLM was called, and what did it see? | Per-task | **Yes** | Timeline events + `GET /v1/llm-calls?task_id=X` | Purple nodes with model badge. Click for prompt/response preview. Already built. |
| 14 | How long did each step take? | Per-task | **Yes** | Timeline → every event has `duration_ms` | Duration labels on timeline connectors. Already built. |
| 15 | Was it escalated? Did it need approval? | Per-task | **Yes** | Timeline → `escalated`, `approval_requested`, `approval_received` events | Amber escalation nodes, approval status. Already built. |
| 16 | Can I share this investigation? | Per-task | **Not yet** | Need URL scheme like `?view=timeline&task=X` | Permalink button that copies shareable URL. **Frontend-only change** — parse URL params on load. |

**Key gap:** #16 (permalink) is a frontend routing feature, not a data issue. #7 heartbeat sparkline needs fetching heartbeat events, which the API supports but the UI doesn't do yet.

---

## Moment 3: The Optimization (12 questions) — Mix of Global and Per-Agent

| # | Question | Scope | Data Available? | API Source | UI Concept |
|---|----------|-------|----------------|------------|------------|
| 17 | How much are my agents costing me? | Global | **Yes** | `GET /v1/cost` → `total_cost`, `by_agent[]`, `by_model[]` | Already built in Cost Explorer. Can embed summary card. |
| 18 | Am I using expensive models for cheap tasks? | Global | **Yes** | `GET /v1/cost` → `by_model[]` + `GET /v1/llm-calls` → per-call `name` + `model` + `cost` | Table: action name x model used x avg cost. Flag rows where an expensive model handles a simple task (low tokens_out relative to model tier). |
| 19 | Why did costs spike? | Global | **Yes** | `GET /v1/cost/timeseries` → `CostTimeBucket[]` with per-bucket cost | Cost timeseries chart (bar/area). Click a spike bucket to drill into events from that period. |
| 20 | Is there prompt bloat? | Global/Per-agent | **Yes** | `GET /v1/llm-calls` → `tokens_in` vs `tokens_out` per call | Scatter plot or table: calls where `tokens_in / tokens_out > 50x` flagged as bloated. Show model, call name, ratio. |
| 21 | Are similar agents working at different costs? | Global | **Yes** | `GET /v1/cost` → `by_agent[]` | Side-by-side agent cost comparison. Already in Cost Explorer by_agent table. Add cost-per-task column derived from metrics. |
| 22 | Are tasks being silently dropped? | Per-agent | **Partial** | `GET /v1/agents/{id}/pipeline` → `queue.oldest_age_seconds` + `GET /v1/metrics?agent_id=X` → `avg_duration_ms` | Flag when `oldest_age_seconds >> avg_duration_ms`. The data exists but the comparison logic needs to be done client-side. |
| 23 | Is queue growing while agent reports idle? | Per-agent | **Yes** | `GET /v1/agents/{id}` → `derived_status` + `stats_1h.queue_depth` | Contradiction detector: status=idle AND queue_depth > 0. Red flag card. |
| 24 | Are credentials failing silently? | Per-agent | **Yes** | `GET /v1/agents/{id}/pipeline` → `issues[]` with high `occurrence_count` | Issues with rising occurrence count, sorted by count descending. Already in pipeline data. |
| 25 | Is the heartbeat doing less than it used to? | Per-agent | **Partial** | `GET /v1/events?event_type=heartbeat&agent_id=X` → compare `payload` across heartbeats | Need to fetch recent heartbeats and diff their payloads. API supports it but requires client-side diffing of heartbeat payload keys. |
| 26 | Are human approvals backing up? | Global | **Yes** | `GET /v1/agents` → count where `derived_status=waiting_approval` + `GET /v1/events?event_type=approval_requested` | "Waiting for Approval" counter + list of pending approvals with age. |
| 27 | Which action consistently fails? | Global/Per-agent | **Yes** | `GET /v1/events?event_type=action_failed` → group by `payload.action_name` | Aggregated table: action name, failure count, last failure time. Client-side grouping of action_failed events. |
| 28 | Is the same issue recurring without resolution? | Per-agent | **Yes** | `GET /v1/agents/{id}/pipeline` → `issues[]` where `occurrence_count` is high | Issues sorted by occurrence_count descending. Badge showing "x50" in red. Already in pipeline data. |

**Processing needed:** #18 needs cross-referencing LLM calls by name+model. #20 needs ratio computation. #22 needs comparing queue age vs avg processing time. #25 needs heartbeat payload diffing. #27 needs client-side grouping of action_failed events. All feasible.

---

## Moment 4: The Review (10 questions) — All Global

| # | Question | Data Available? | API Source | UI Concept |
|---|----------|----------------|------------|------------|
| 29 | Is my success rate improving? | **Yes** | `GET /v1/metrics` → `timeseries[].tasks_completed / (completed+failed)` + `summary.success_rate` | Trend line chart. Current value + directional arrow (up/down vs previous period). |
| 30 | Are tasks getting faster or slower? | **Yes** | `GET /v1/metrics` → `timeseries[].avg_duration_ms` + `summary.avg_duration_ms` | Duration trend line. Compare current vs previous period. |
| 31 | Which agent fails most often? | **Yes** | `GET /v1/metrics?group_by=agent` → per-agent `tasks_failed` | Ranked bar chart: agents by failure count. Highlight worst performer. |
| 32 | Are agents getting better after deploys? | **Partial** | `GET /v1/metrics` with different ranges for before/after | Compare two time ranges side-by-side. **No deploy marker events** — user must pick ranges manually. |
| 33 | What's our total agent infrastructure cost? | **Yes** | `GET /v1/cost?range=30d` → `total_cost` | Single big number card with breakdown by model/agent. |
| 34 | Is cost per task trending up or down? | **Yes** | `GET /v1/metrics` → derive `cost / tasks_completed` per timeseries bucket | Trend line of cost/task. Backend has `avg_cost_per_task` in summary; timeseries needs client-side division. |
| 35 | Can I prove ROI on agent observability? | **Partial** | `GET /v1/cost` at different ranges → compare costs over time | Before/after cost comparison panel. User picks two periods. Manual but data exists. |
| 36 | How many agents are in production? | **Yes** | `GET /v1/agents` → count | Simple counter card. |
| 37 | What's the overall health of the fleet? | **Yes** | `GET /v1/agents` (status distribution) + `GET /v1/metrics` (success_rate, stuck count) | Composite health score or traffic-light indicator. All green = healthy. |
| 38 | Are we ready to scale? | **Yes** | `GET /v1/agents` + `GET /v1/metrics` + `GET /v1/pipeline` → success rates, queue depths, cost trends | Readiness checklist: success rate > threshold, queue depths stable, costs linear, error rates flat. |

---

## Summary Scorecard

| Category | Total | Full Data | Partial Data | No Data |
|----------|-------|-----------|-------------|---------|
| The Glance | 5 | **5** | 0 | 0 |
| The Investigation | 11 | **9** | 1 (#7 sparkline) | 1 (#16 permalink) |
| The Optimization | 12 | **9** | 3 (#22, #25, #35) | 0 |
| The Review | 10 | **8** | 2 (#32, #35) | 0 |
| **Total** | **38** | **31 (82%)** | **6 (16%)** | **1 (3%)** |

The one missing item (#16 permalink) is purely a frontend routing feature — no backend change needed. The 6 "partial" items have the raw data available but need either client-side processing or additional API calls that the backend already supports.

---

## Architecture Recommendation

The current `hiveboard.js` is at **1,823 lines** and already a monolith. A new `insights.js` file would:

- Reuse `apiFetch()`, `escHtml()`, `fmtTokens()`, `fmtDuration()`, `fmtCost()`, `timeAgo()`, `showToast()` from the main file (they're global)
- Own all Insights tab rendering, state, and data fetching
- Follow the existing tab pattern: add a `<button class="view-tab">` + `<div class="center-view" id="viewInsights">` + a `switchView('insights')` case

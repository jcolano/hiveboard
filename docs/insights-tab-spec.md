# Insights Tab — Implementation Spec

> **Status**: Spec ready for review
> **Scope**: New "Insights" tab in HiveBoard dashboard
> **Deliverable**: `static/js/insights.js` + HTML/CSS additions
> **Dependencies**: All data from existing API endpoints (no backend changes required)

---

## 1. Purpose

The Insights tab answers the **38 questions** from `3_What-HiveBoard-Sees.md` through a dedicated analytics view. While the existing Dashboard (Mission Control), Costs, and Pipeline tabs show raw operational data, the Insights tab synthesizes that data into **actionable answers** organized by user intent.

The four "moments" map to four collapsible sections:

| Moment | Questions | User Intent | Time Budget |
|--------|-----------|-------------|-------------|
| The Glance | Q1–Q5 | "Is everything OK?" | 2 seconds |
| The Investigation | Q6–Q16 | "What went wrong?" | 2–5 minutes |
| The Optimization | Q17–Q28 | "How do I improve?" | 10–15 minutes |
| The Review | Q29–Q38 | "How are we doing?" | 20–30 minutes |

---

## 2. Architecture

### 2.1 File Structure

```
static/js/insights.js    # ~800–1000 lines, all Insights tab logic
static/js/hiveboard.js   # Existing — add switchView('insights') case only
templates/index.html      # Add tab button + container div
static/css/style.css      # Add Insights-specific styles
```

### 2.2 Coupling Strategy

`insights.js` has **near-zero coupling** to `hiveboard.js`. It reuses only these globals (already exposed at window scope):

| Global | Type | Used For |
|--------|------|----------|
| `CONFIG` | object | `CONFIG.endpoint`, `CONFIG.apiKey` |
| `apiFetch(path, params)` | function | All API calls |
| `escHtml(s)` | function | Safe HTML rendering |
| `fmtTokens(n)` | function | Token count formatting (1.2K) |
| `fmtDuration(ms)` | function | Duration formatting (2m30s) |
| `fmtCost(c, source)` | function | Cost formatting ($1.23, ~$1.23) |
| `timeAgo(ts)` | function | Relative timestamps (3m ago) |
| `showToast(msg, isError)` | function | Error/info toasts |
| `hbClass(seconds)` | function | Heartbeat CSS class |
| `hbText(seconds)` | function | Heartbeat age text |
| `tokenBarHtml(in, out)` | function | Inline token ratio bars |
| `switchView(view)` | function | Navigation (for linking to other tabs) |

`insights.js` does **not** read or write `AGENTS`, `TASKS`, `TIMELINES`, `COST_DATA`, or other hiveboard.js state. It fetches its own data independently.

### 2.3 Script Loading

```html
<script src="/static/js/insights.js" defer></script>
```

Loaded after `hiveboard.js`. The `defer` attribute ensures DOM is ready. `insights.js` exports a single entry point `initInsights()` called from the `switchView` branch.

---

## 3. Tab Integration

### 3.1 HTML Changes (index.html)

**Tab button** — add after the Pipeline tab button:

```html
<div class="view-tabs">
  <button class="view-tab active" data-view="mission" onclick="switchView('mission')">Dashboard</button>
  <button class="view-tab" data-view="cost" onclick="switchView('cost')">Costs</button>
  <button class="view-tab" data-view="pipeline" onclick="switchView('pipeline')">Pipeline</button>
  <button class="view-tab" data-view="insights" onclick="switchView('insights')">Insights</button>
</div>
```

**Container div** — add inside `.center-panel`:

```html
<div class="center-view" id="viewInsights"></div>
```

### 3.2 switchView Addition (hiveboard.js)

Add a new branch in `switchView()`:

```javascript
else if (view === 'insights') {
  document.querySelector('[data-view="insights"]').classList.add('active');
  document.getElementById('viewInsights').classList.add('active');
  if (typeof initInsights === 'function') initInsights();
}
```

### 3.3 Periodic Refresh

The Insights tab manages its own refresh cycle (see Section 8). When the user navigates away from Insights, the tab stops polling. When they return, it resumes with a fresh fetch.

---

## 4. Layout

The `#viewInsights` container renders four collapsible sections. Each section has a header bar (click to collapse/expand) and a content area with cards arranged in a CSS grid.

```
┌─────────────────────────────────────────────────────────┐
│  [Range: 24h ▾]   [Agent: All ▾]   [🔄 Refresh]       │  ← Toolbar
├─────────────────────────────────────────────────────────┤
│  ▼ The Glance — Fleet Status at a Glance                │  ← Section 1
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Q1: Agent │ │ Q2: Attn │ │ Q3: Stuck│ │ Q4: Flow │   │
│  │ Status   │ │ Badge    │ │ Counter  │ │ Charts   │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Q5: Live Activity Feed                           │   │
│  └──────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  ▼ The Investigation — Agent & Task Deep Dive           │  ← Section 2
│  [Agent: select-agent-dropdown ▾]                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Q6: Now  │ │ Q7: HB   │ │ Q8: Queue│ │ Q9: Issue│   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
│  Q10-Q16: Link to Mission Control timeline →            │
├─────────────────────────────────────────────────────────┤
│  ▼ The Optimization — Cost & Reliability                │  ← Section 3
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│  │ Q17: Cost│ │ Q18: Mod │ │ Q19: Spk │ ...            │
│  └──────────┘ └──────────┘ └──────────┘                │
│  Smart Detectors: [Bloat] [Silent Drop] [Contradiction] │
├─────────────────────────────────────────────────────────┤
│  ▼ The Review — Trends & Fleet Health                   │  ← Section 4
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                │
│  │ Q29: Suc │ │ Q30: Dur │ │ Q31: Fail│ ...            │
│  └──────────┘ └──────────┘ └──────────┘                │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Q37: Fleet Health Score   Q38: Scale Readiness   │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 4.1 Toolbar

| Control | Type | Default | Purpose |
|---------|------|---------|---------|
| Range selector | `<select>` | `24h` | Applies to all time-dependent questions. Options: `1h`, `6h`, `24h`, `7d`, `30d` |
| Agent selector | `<select>` | `All` | Filters Investigation section. Populated from agent list |
| Refresh button | `<button>` | — | Manual re-fetch of all data |

### 4.2 Section Collapse

Each section header is a `<div class="insights-section-header">` with a chevron icon. Clicking toggles the sibling `.insights-section-body`. State is persisted in `localStorage` key `insights_collapsed_sections` (JSON array of section indices).

---

## 5. The 38 Questions — Full Specification

### Moment 1: The Glance (Q1–Q5)

---

#### Q1: "Are my agents running?"

| Field | Value |
|-------|-------|
| **Scope** | Global (all agents) |
| **Endpoint** | `GET /v1/agents` |
| **Response model** | `AgentSummary[]` |
| **Key fields** | `derived_status`, `last_heartbeat`, `heartbeat_age_seconds`, `is_stuck` |
| **Processing** | Group agents by `derived_status`. Count per status: `idle`, `processing`, `waiting_approval`, `error`, `stuck` |
| **UI component** | **Fleet Status Bar** — horizontal bar divided into colored segments. Each segment width is proportional to count. Segment colors follow existing `statusColor` map. Hover shows count + agent names. Below the bar: `N agents total · X processing · Y idle · Z stuck` |
| **Update** | On each fetch cycle |

---

#### Q2: "Does anything need my attention?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/agents`, `GET /v1/pipeline` |
| **Response models** | `AgentSummary[]`, `FleetPipelineState` |
| **Key fields** | `AgentSummary.derived_status`, `AgentSummary.is_stuck`, `FleetPipelineState.totals.active_issues`, `FleetPipelineState.totals.queue_depth` |
| **Processing** | Count attention items: agents with `is_stuck=true` + agents with `derived_status='error'` + agents with `derived_status='waiting_approval'` + `active_issues` count from fleet pipeline |
| **UI component** | **Attention Badge** — large pill showing total count. Color: red if any stuck/error, amber if only waiting_approval, green if zero. Below the pill, itemized list: "2 stuck, 1 error, 3 issues, 1 waiting approval". Each item is clickable → navigates to the relevant agent in Investigation section |
| **Update** | On each fetch cycle |

---

#### Q3: "Is anything stuck?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/agents` |
| **Response model** | `AgentSummary[]` |
| **Key fields** | `is_stuck`, `agent_id`, `heartbeat_age_seconds`, `stuck_threshold_seconds`, `current_task_id` |
| **Processing** | Filter `agents.filter(a => a.is_stuck)`. For each stuck agent, compute `overtime = heartbeat_age_seconds - stuck_threshold_seconds` |
| **UI component** | **Stuck Counter** — if zero: green "0 stuck" badge. If nonzero: red badge with count + expandable list showing each stuck agent: `agent_id (stuck for Xm, threshold Ys, task: task_id)`. Click agent → selects in Investigation section |
| **Update** | On each fetch cycle |

---

#### Q4: "Is work flowing?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/metrics?range={selectedRange}` |
| **Response model** | `MetricsResponse` |
| **Key fields** | `summary.success_rate`, `summary.avg_duration_ms`, `summary.total_cost`, `summary.avg_cost_per_task`, `timeseries[].tasks_completed`, `timeseries[].tasks_failed`, `timeseries[].avg_duration_ms`, `timeseries[].cost`, `timeseries[].throughput` |
| **Processing** | Extract four timeseries arrays from `MetricsResponse.timeseries` buckets |
| **UI component** | **Four Mini-Charts** in a 2×2 grid. Each chart is a sparkline (inline SVG or canvas, 200×60px): (1) Throughput — `timeseries[].throughput`, (2) Success Rate — derived `completed / (completed + failed)` per bucket, (3) Errors — `timeseries[].tasks_failed`, (4) LLM Cost/Task — `timeseries[].cost / timeseries[].tasks_completed`. Each chart shows the current value as a large number above the sparkline, with a trend arrow (up/down/flat) comparing first half vs second half of the range |
| **Update** | On range change or fetch cycle |

---

#### Q5: "Is anything happening right now?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/events?exclude_heartbeats=true&limit=20` |
| **Response model** | `Event[]` |
| **Key fields** | `event_type`, `agent_id`, `task_id`, `timestamp`, `severity`, `payload.summary` |
| **Processing** | Take latest 20 non-heartbeat events. Group into "just now" (<60s), "recent" (<5m), "earlier" |
| **UI component** | **Compact Activity Feed** — vertically scrolling list (max-height 200px). Each row: `[severity-dot] [timeAgo] agent_id: payload.summary`. Color-code severity dot: info=blue, warn=amber, error=red. Show "LIVE" badge if newest event is <10s old |
| **Update** | On each fetch cycle. Optionally via WebSocket `event.new` messages if WS is connected |

---

### Moment 2: The Investigation (Q6–Q16)

> **Agent Selector**: This section requires a selected agent. The toolbar's agent dropdown filters all Q6–Q9 cards. Default: first agent in the list (sorted by attention priority: stuck > error > waiting > processing > idle).

---

#### Q6: "What is this agent doing right now?"

| Field | Value |
|-------|-------|
| **Scope** | Per-agent |
| **Endpoint** | `GET /v1/agents/{agent_id}` |
| **Response model** | `AgentSummary` |
| **Key fields** | `derived_status`, `current_task_id`, `last_seen`, `stats_1h.tasks_completed`, `stats_1h.tasks_failed` |
| **Processing** | If `current_task_id` is set, also fetch `GET /v1/tasks?agent_id={agent_id}&limit=1&sort=-started_at` to get the active task's `task_type`, `duration_ms`, `started_at` |
| **UI component** | **Agent Status Card** — shows: status badge (colored pill), current task ID + type (if processing), elapsed time since `started_at` (live-updating via `fmtDuration(Date.now() - started_at)`), 1h summary line: "Completed 4, Failed 1 in last hour" |
| **Update** | On agent selection or fetch cycle |

---

#### Q7: "Is this agent's heartbeat healthy?"

| Field | Value |
|-------|-------|
| **Scope** | Per-agent |
| **Endpoint** | `GET /v1/agents/{agent_id}` |
| **Response model** | `AgentSummary` |
| **Key fields** | `last_heartbeat`, `heartbeat_age_seconds`, `stuck_threshold_seconds` |
| **Processing** | Classify: HEALTHY if `heartbeat_age_seconds < stuck_threshold_seconds * 0.5`, STALE if `< stuck_threshold_seconds`, DEAD if `>=`. Use `hbClass()` for CSS class |
| **UI component** | **Heartbeat Health Card** — three-state indicator (green circle / amber circle / red circle) with label. Shows: "Last heartbeat: {timeAgo(last_heartbeat)}", "Threshold: {stuck_threshold_seconds}s", health classification. Visual: horizontal bar showing heartbeat_age relative to threshold |
| **Update** | On agent selection or fetch cycle. Heartbeat age auto-increments client-side every second |

---

#### Q8: "Does this agent have pending work?"

| Field | Value |
|-------|-------|
| **Scope** | Per-agent |
| **Endpoint** | `GET /v1/agents/{agent_id}/pipeline` |
| **Response model** | `PipelineState` |
| **Key fields** | `queue.depth`, `queue.oldest_age_seconds`, `queue.items[]` (each: `id`, `priority`, `source`, `summary`, `queued_at`), `queue.processing` |
| **Processing** | Direct read. If `queue.depth > 0` and no `queue.processing`, flag as "queued but idle" |
| **UI component** | **Queue Panel** — header: "Queue: {depth}" with colored badge (green=0, amber=1-5, red=6+). If items exist, show table: `priority | summary | queued age`. If processing: show "Processing: {processing.summary} ({fmtDuration(processing.elapsed_ms)})". Warning banner if depth>0 and agent status is idle |
| **Update** | On agent selection or fetch cycle |

---

#### Q9: "Has this agent reported its own problems?"

| Field | Value |
|-------|-------|
| **Scope** | Per-agent |
| **Endpoint** | `GET /v1/agents/{agent_id}/pipeline` |
| **Response model** | `PipelineState` |
| **Key fields** | `issues[]` — each: `severity`, `issue_id`, `category`, `context`, `action` (reported/resolved/dismissed), `occurrence_count` |
| **Processing** | Filter to active issues (`action='reported'`). Sort by severity (critical first), then by occurrence_count descending |
| **UI component** | **Issues Panel** — header: "{count} active issues". Each issue is a card: `[severity-badge] category: context (×{occurrence_count})`. Severity badge colors: critical=red, high=orange, medium=amber, low=blue. Issues with `occurrence_count > 5` get a "Recurring" tag |
| **Update** | On agent selection or fetch cycle |

---

#### Q10–Q16: Task-Level Investigation

These questions relate to individual task timelines (steps, plan progress, tool failures, LLM calls, durations, escalations, permalinks). The existing Mission Control tab already provides a rich timeline view for these.

**Implementation**: Rather than re-implementing the timeline in Insights, provide **navigation links** to Mission Control.

| Question | Link Target | UI |
|----------|-------------|-----|
| Q10: "What steps did this task take?" | `switchView('mission')` + select task | "View Timeline →" link |
| Q11: "What was the plan, and where did it go wrong?" | Same, plan section | "View Plan →" link |
| Q12: "Which tool failed?" | Same, filtered to errors | "View Errors →" link |
| Q13: "Which LLM was called?" | Same, LLM nodes | "View LLM Calls →" link |
| Q14: "How long did each step take?" | Same, duration view | "View Durations →" link |
| Q15: "Was it escalated?" | Same, escalation nodes | "View Timeline →" link |
| Q16: "Can I share this investigation?" | Permalink generation | Future: shareable URL |

**UI component**: **Recent Tasks Table** for the selected agent, showing last 10 tasks. Columns: `task_id`, `type`, `status`, `duration`, `cost`, `errors`, `Actions` (with "View Timeline →" link). Data from `GET /v1/tasks?agent_id={agent_id}&limit=10&sort=-started_at`.

---

### Moment 3: The Optimization (Q17–Q28)

---

#### Q17: "How much are my agents costing me?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/cost?range={selectedRange}` |
| **Response model** | `CostSummary` |
| **Key fields** | `total_cost`, `call_count`, `total_tokens_in`, `total_tokens_out`, `by_agent[]` (each: `agent_id`, `cost`, `call_count`, `tokens_in`, `tokens_out`), `by_model[]` (each: `model`, `cost`, `call_count`, `tokens_in`, `tokens_out`), `reported_cost`, `estimated_cost` |
| **Processing** | Direct read. Sort `by_agent` and `by_model` by cost descending |
| **UI component** | **Cost Overview Card** — large number: `fmtCost(total_cost)` with range label. Below: two horizontal bar charts side by side. Left: "By Agent" — each bar is an agent, width proportional to cost, label shows `agent_id: $X.XX (N calls)`. Right: "By Model" — each bar is a model, same layout. If `estimated_cost > 0`, show note: "Includes ~{fmtCost(estimated_cost)} estimated" |
| **Update** | On range change or fetch cycle |

---

#### Q18: "Am I using expensive models where cheap ones would work?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/cost/calls?limit=200` + `GET /v1/tasks?limit=100` |
| **Response models** | `LlmCallRecord[]`, `TaskSummary[]` |
| **Key fields** | `LlmCallRecord.model`, `LlmCallRecord.cost`, `LlmCallRecord.tokens_in`, `LlmCallRecord.tokens_out`, `LlmCallRecord.task_id`, `TaskSummary.derived_status`, `TaskSummary.error_count` |
| **Processing** | Join LLM calls to tasks by `task_id`. For each model, compute: avg cost per call, success rate of tasks using that model, avg tokens. Flag models where: cost > 2× cheapest model AND task success rate is similar (within 5%) to a cheaper model |
| **UI component** | **Model Efficiency Matrix** — table with columns: `Model`, `Calls`, `Avg Cost/Call`, `Avg Tokens`, `Task Success Rate`, `Verdict`. Verdict column: "Efficient" (green), "Review" (amber, if cheaper alternative exists with similar success rate), "Expensive" (red, if >3× cheapest with no better success rate). Tooltip on "Review"/"Expensive" explains the cheaper alternative |
| **Update** | On range change or fetch cycle |

---

#### Q19: "Why did costs spike?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/cost/timeseries?range={selectedRange}` + `GET /v1/metrics?range={selectedRange}&group_by=model` |
| **Response models** | `CostTimeBucket[]`, `MetricsResponse` (with groups) |
| **Key fields** | `CostTimeBucket.timestamp`, `CostTimeBucket.cost`, `CostTimeBucket.call_count`, `CostTimeBucket.tokens_in`, `CostTimeBucket.tokens_out` |
| **Processing** | Render timeseries as stacked area chart. Detect spikes: any bucket where `cost > 2× mean(all buckets)`. For spike buckets, show annotation with `call_count` and `tokens_in` to explain whether spike is from more calls or more tokens |
| **UI component** | **Cost Timeline Chart** — SVG area chart (full width, 200px height). X-axis: time buckets. Y-axis: cost. Line + filled area. Spike buckets highlighted with red vertical marker. Hover tooltip: `timestamp, cost, calls, tokens_in, tokens_out`. Below chart: if spikes detected, summary: "Cost spike at {time}: {N} calls, {tokens} tokens (vs avg {avg_calls} calls)" |
| **Update** | On range change or fetch cycle |

---

#### Q20: "Is there prompt bloat?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/cost/calls?limit=200` |
| **Response model** | `LlmCallRecord[]` |
| **Key fields** | `tokens_in`, `tokens_out`, `name`, `model`, `agent_id` |
| **Processing** | For each call, compute `ratio = tokens_in / tokens_out`. Flag calls where `ratio > 10` (10× more input than output). Group flagged calls by `name` (LLM call purpose) to find systematic bloat. Compute fleet-wide `avg_ratio`. Also compute per-agent average ratio |
| **UI component** | **Prompt Bloat Detector** (Smart Detector card) — header: "Prompt Bloat Analysis". Metrics: fleet-wide avg in/out ratio, count of calls with ratio >10. If bloat detected: table of top offenders grouped by `name`: `LLM Call Name | Avg Ratio | Calls | Agents Affected`. Each row shows `tokenBarHtml(tokens_in, tokens_out)` for visual. If no bloat: green "No prompt bloat detected" message |
| **Update** | On range change or fetch cycle |

---

#### Q21: "Are similar agents working at different costs?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/cost?range={selectedRange}` + `GET /v1/agents` |
| **Response models** | `CostSummary`, `AgentSummary[]` |
| **Key fields** | `CostSummary.by_agent[]`, `AgentSummary.agent_type` |
| **Processing** | Join cost data with agent metadata by `agent_id`. Group agents by `agent_type`. Within each type group, compute cost variance. Flag groups where `max_cost > 2× min_cost` among agents of the same type |
| **UI component** | **Agent Cost Comparison** — grouped bar chart or table. Group header: `agent_type`. Within each group, bars for each agent showing total cost. Highlight outliers (>2× group mean) in red. Shows: `agent_id | Cost | Calls | Cost vs Group Avg` |
| **Update** | On range change or fetch cycle |

---

#### Q22: "Are tasks being silently dropped?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/pipeline` |
| **Response model** | `FleetPipelineState` |
| **Key fields** | `agents[].queue_depth`, `FleetPipelineState.totals.queue_depth`, per-agent `PipelineState.queue.oldest_age_seconds`, `PipelineState.queue.items[].queued_at` |
| **Processing** | For each agent pipeline, check for items where `oldest_age_seconds > 300` (5 minutes) while agent is not actively processing a queue item. These are potential silent drops. Also check: agent status is `idle` but queue depth > 0 |
| **UI component** | **Silent Drop Detector** (Smart Detector card) — header: "Silent Drop Detection". If issues found: table of `Agent | Queue Depth | Oldest Item Age | Agent Status | Verdict`. Verdict: "Possible drop" (red) if idle with queued items >5min, "Slow" (amber) if processing but oldest >5min, "OK" (green) otherwise. If no issues: green "No silent drops detected" |
| **Update** | On fetch cycle |

---

#### Q23: "Is queue growing while agent reports idle?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/agents`, `GET /v1/pipeline` |
| **Response models** | `AgentSummary[]`, `FleetPipelineState` |
| **Key fields** | `AgentSummary.derived_status`, `FleetPipelineState.agents[].queue_depth` |
| **Processing** | For each agent: if `derived_status == 'idle'` and `queue_depth > 0`, flag as contradiction. Severity: low if queue_depth=1, medium if 2-5, high if >5 |
| **UI component** | **Contradiction Detector** (Smart Detector card) — header: "Status/Queue Contradictions". If contradictions found: list each: `[severity-badge] agent_id: status={status}, queue={depth}`. Suggested action: "Agent may need restart or queue processor check". If none: green "No contradictions detected" |
| **Update** | On fetch cycle |

---

#### Q24: "Are credentials failing silently?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/pipeline`, per-agent `GET /v1/agents/{id}/pipeline` |
| **Response model** | `PipelineState` |
| **Key fields** | `issues[].occurrence_count`, `issues[].category`, `issues[].severity`, `issues[].context` |
| **Processing** | Across all agents, collect issues where `occurrence_count > 3`. These indicate recurring problems that may represent silent credential failures. Group by `category`. Sort by total occurrence count descending |
| **UI component** | **Recurring Failure Detector** — table: `Category | Agent(s) | Total Occurrences | Severity | Context Preview`. Rows with `occurrence_count > 10` get a "Critical" highlight. Each row expandable to show full `context` text |
| **Update** | On fetch cycle |

---

#### Q25: "Is the heartbeat doing less than it used to?"

| Field | Value |
|-------|-------|
| **Scope** | Per-agent |
| **Endpoint** | `GET /v1/events?agent_id={agent_id}&event_type=heartbeat&limit=50` |
| **Response model** | `Event[]` |
| **Key fields** | `payload.data` (heartbeat payload content varies), `payload.summary`, `timestamp` |
| **Processing** | Compare payload sizes/content across recent heartbeats. If earlier heartbeats included richer data (more fields in `payload.data`) than recent ones, flag as "heartbeat drift". **Note**: This is a PARTIAL capability — requires heuristic comparison of payload structure over time |
| **UI component** | **Heartbeat Drift Card** — shows: "Last 50 heartbeats analyzed". If drift detected: "Heartbeat payload has shrunk: {old_field_count} fields → {new_field_count} fields since {date}". If no drift: "Heartbeat payload is stable". Visual: small timeline showing payload size over time |
| **Data availability** | PARTIAL — heartbeat payloads are stored but structured drift detection requires payload schema comparison |

---

#### Q26: "Are human approvals backing up?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/agents` |
| **Response model** | `AgentSummary[]` |
| **Key fields** | `derived_status` (filter for `'waiting_approval'`), `last_seen`, `current_task_id` |
| **Processing** | Count agents with `derived_status == 'waiting_approval'`. For each, compute wait time as `now - last_seen`. Also fetch task info via `GET /v1/tasks?agent_id={agent_id}&limit=1` to get task context |
| **UI component** | **Approval Queue Monitor** — header: "{count} agents waiting for approval". If count > 0: table with `Agent | Task | Waiting Since | Wait Duration`. Rows sorted by wait duration descending. Rows waiting >10min get amber highlight, >30min get red highlight. If count == 0: green "No pending approvals" |
| **Update** | On fetch cycle |

---

#### Q27: "Which action consistently fails?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/events?event_type=action_failed&limit=200` + `GET /v1/events?event_type=action_completed&limit=200` |
| **Response model** | `Event[]` |
| **Key fields** | `payload.action_name` (from `payload` directly, per I1 integration fix), `agent_id`, `task_id`, `payload.summary` |
| **Processing** | Group `action_failed` events by `action_name`. Count failures per action. Also count completions per action (from `action_completed`). Compute failure rate: `failures / (failures + completions)`. Sort by failure rate descending, then by absolute count |
| **UI component** | **Action Failure Heatmap** — table: `Action Name | Failures | Completions | Failure Rate | Agents Affected`. Failure rate cell is color-coded: green <10%, amber 10-30%, red >30%. Each row expandable to show recent failure summaries (from `payload.summary`). Top 3 failing actions highlighted |
| **Update** | On range change or fetch cycle |

---

#### Q28: "Is the same issue recurring without resolution?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/pipeline` (fleet pipeline) or iterate `GET /v1/agents/{id}/pipeline` |
| **Response model** | `FleetPipelineState` / `PipelineState` |
| **Key fields** | `issues[].issue_id`, `issues[].occurrence_count`, `issues[].action`, `issues[].category`, `issues[].context` |
| **Processing** | Collect all issues with `action == 'reported'` (still active) and `occurrence_count > 1`. Sort by `occurrence_count` descending. These are unresolved recurring issues |
| **UI component** | **Unresolved Issues Board** — table: `Issue ID | Category | Agent | Occurrences | Severity | Context`. Issues with `occurrence_count > 5` get "Chronic" badge. Issues with `severity == 'critical'` get top placement regardless of count. Click row → navigates to agent in Investigation section |
| **Update** | On fetch cycle |

---

### Moment 4: The Review (Q29–Q38)

---

#### Q29: "Is my success rate improving?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/metrics?range={selectedRange}` |
| **Response model** | `MetricsResponse` |
| **Key fields** | `timeseries[].tasks_completed`, `timeseries[].tasks_failed` |
| **Processing** | Compute success rate per bucket: `completed / (completed + failed)`. Split timeseries in half. Compute avg success rate for first half and second half. Trend = second - first |
| **UI component** | **Success Rate Trend Chart** — line chart (full width, 180px height) of success rate over time. Dashed horizontal line at overall average. Trend indicator: arrow up (green) if improving >2%, arrow down (red) if declining >2%, flat (gray) otherwise. Summary text: "Success rate: {current}% ({trend} from {previous}%)" |
| **Update** | On range change or fetch cycle |

---

#### Q30: "Are tasks getting faster or slower?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/metrics?range={selectedRange}` |
| **Response model** | `MetricsResponse` |
| **Key fields** | `timeseries[].avg_duration_ms`, `summary.avg_duration_ms` |
| **Processing** | Same half-split trend analysis as Q29 but for `avg_duration_ms`. Improving = getting faster (decreasing). Also compute p50/p90 if enough data points (sort durations, pick percentiles) |
| **UI component** | **Duration Trend Chart** — line chart of `avg_duration_ms` over time. Same trend arrow logic (but inverted: down is green = faster). Summary: "Avg duration: {fmtDuration(current)} ({trend} from {fmtDuration(previous)})". Below chart: if available, "Fastest bucket: {fmtDuration(min)}, Slowest: {fmtDuration(max)}" |
| **Update** | On range change or fetch cycle |

---

#### Q31: "Which agent fails most often?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/metrics?range={selectedRange}&group_by=agent` |
| **Response model** | `MetricsResponse` (with `groups[]`) |
| **Key fields** | `groups[].key` (agent_id), `groups[].summary.success_rate`, `groups[].summary.total_tasks`, `groups[].summary.failed` |
| **Processing** | Sort groups by `success_rate` ascending (worst first). Filter to agents with at least 5 tasks (avoid noise from low-volume agents) |
| **UI component** | **Agent Reliability Ranking** — horizontal bar chart. Each bar represents an agent. Bar length = success rate (0-100%). Color: green >90%, amber 70-90%, red <70%. Label: `agent_id: {success_rate}% ({completed}/{total} tasks)`. Worst agent at top |
| **Update** | On range change or fetch cycle |

---

#### Q32: "Are agents getting better after deploys?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/metrics?range=7d`, `GET /v1/agents` |
| **Response models** | `MetricsResponse`, `AgentSummary[]` |
| **Key fields** | `AgentSummary.agent_version`, `MetricsResponse.timeseries[]` |
| **Processing** | Group metrics before and after version changes (detected from `agent_version` field). Compare success rate and avg duration. **Note**: PARTIAL — no deploy event type exists yet; version changes in agent records serve as proxy |
| **UI component** | **Before/After Comparison** — if version changes detected: table with `Agent | Old Version | New Version | Success Rate (Before) | Success Rate (After) | Duration (Before) | Duration (After)`. Delta cells color-coded green/red. If no version changes detected: "No version changes detected in selected range. Consider adding deploy marker events." |
| **Data availability** | PARTIAL — relies on `agent_version` changes as deploy proxy |

---

#### Q33: "What's our total agent infrastructure cost?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/cost?range={selectedRange}` |
| **Response model** | `CostSummary` |
| **Key fields** | `total_cost`, `call_count`, `total_tokens_in`, `total_tokens_out`, `reported_cost`, `estimated_cost` |
| **Processing** | Direct read. Format as large display number |
| **UI component** | **Total Cost Card** — large number: `fmtCost(total_cost)` with range label "{selectedRange}". Subtitle: "{call_count} LLM calls, {fmtTokens(total_tokens_in)} tokens in, {fmtTokens(total_tokens_out)} tokens out". If both reported and estimated costs exist: "Reported: {fmtCost(reported_cost)}, Estimated: ~{fmtCost(estimated_cost)}" |
| **Update** | On range change or fetch cycle |

---

#### Q34: "Is cost per task trending up or down?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/metrics?range={selectedRange}` + `GET /v1/cost/timeseries?range={selectedRange}` |
| **Response models** | `MetricsResponse`, `CostTimeBucket[]` |
| **Key fields** | `MetricsResponse.timeseries[].tasks_completed`, `CostTimeBucket[].cost` |
| **Processing** | Align timeseries buckets by timestamp. Compute `cost_per_task = cost / tasks_completed` per bucket (skip buckets with 0 tasks). Half-split trend analysis |
| **UI component** | **Cost/Task Trend Chart** — line chart of cost_per_task over time. Trend arrow (down = green = cheaper). Summary: "Avg cost/task: {fmtCost(avg)} ({trend} from {fmtCost(previous)})". Below: "Most expensive bucket: {fmtCost(max)} at {time}" |
| **Update** | On range change or fetch cycle |

---

#### Q35: "Can I prove ROI on agent observability?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/metrics?range=30d`, `GET /v1/cost?range=30d` |
| **Response models** | `MetricsResponse`, `CostSummary` |
| **Key fields** | `MetricsResponse.summary`, `CostSummary.total_cost` |
| **Processing** | User provides baseline values (pre-observability) via input fields. Calculator computes: `cost_saved = baseline_cost - current_cost`, `time_saved = baseline_avg_duration - current_avg_duration`, `error_reduction = baseline_error_rate - current_success_rate`. **Note**: PARTIAL — requires user-input baseline since no historical pre-HiveBoard data exists |
| **UI component** | **ROI Calculator Card** — input fields: "Baseline cost/month ($)", "Baseline avg task duration (s)", "Baseline error rate (%)". Calculate button computes and displays: "Cost savings: {fmtCost(delta)}/month", "Time saved per task: {fmtDuration(delta)}", "Error rate improvement: {delta}%", "Projected annual savings: {fmtCost(annual)}". If no baseline entered: show current 30d metrics with prompt "Enter your pre-observability baselines to calculate ROI" |
| **Data availability** | PARTIAL — current metrics are available; baseline requires user input |

---

#### Q36: "How many agents are in production?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoint** | `GET /v1/agents` |
| **Response model** | `AgentSummary[]` |
| **Key fields** | `agent_id`, `environment`, `first_seen`, `agent_type` |
| **Processing** | Count total agents. Group by `environment` and `agent_type`. Compute growth: agents added in selected range (where `first_seen` is within range) |
| **UI component** | **Fleet Size Indicator** — large number: total agent count. Breakdown: "By environment: production={N}, staging={N}, dev={N}". "By type: {type}={N}, ...". Growth: "+{N} new agents in last {range}". If only one environment, skip breakdown |
| **Update** | On range change or fetch cycle |

---

#### Q37: "What's the overall health of the fleet?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/agents`, `GET /v1/metrics?range=1h`, `GET /v1/pipeline` |
| **Response models** | `AgentSummary[]`, `MetricsResponse`, `FleetPipelineState` |
| **Key fields** | `AgentSummary.is_stuck`, `AgentSummary.derived_status`, `MetricsResponse.summary.success_rate`, `FleetPipelineState.totals.active_issues` |
| **Processing** | Composite health score algorithm: |

**Health Score Algorithm:**

```
score = 100

# Stuck agents penalty: -15 per stuck agent
score -= stuck_count * 15

# Error agents penalty: -10 per error agent
score -= error_count * 10

# Success rate penalty: -(100 - success_rate) * 0.5
score -= (100 - success_rate) * 0.5

# Active issues penalty: -3 per active issue
score -= active_issues * 3

# Waiting approval penalty: -2 per waiting agent
score -= waiting_count * 2

score = max(0, min(100, score))

Classification:
  HEALTHY:  score >= 80 (green)
  DEGRADED: score >= 50 (amber)
  CRITICAL: score < 50  (red)
```

| **UI component** | **Fleet Health Score** — large circular gauge (SVG donut chart) showing score 0-100. Center text: score number + classification label. Color follows classification. Below gauge: breakdown of penalties: "Stuck agents: -{N}", "Errors: -{N}", etc. Only show non-zero penalties |
| **Update** | On fetch cycle |

---

#### Q38: "Are we ready to scale?"

| Field | Value |
|-------|-------|
| **Scope** | Global |
| **Endpoints** | `GET /v1/agents`, `GET /v1/metrics?range=24h`, `GET /v1/cost?range=24h`, `GET /v1/pipeline` |
| **Response models** | `AgentSummary[]`, `MetricsResponse`, `CostSummary`, `FleetPipelineState` |
| **Key fields** | All summary fields |
| **Processing** | Evaluate a readiness checklist: |

**Scale Readiness Checklist:**

| Check | Criterion | Source |
|-------|-----------|--------|
| Success rate | `success_rate >= 90%` | `MetricsResponse.summary.success_rate` |
| No stuck agents | `stuck_count == 0` | `AgentSummary[].is_stuck` |
| Queue manageable | `total_queue_depth < agent_count * 3` | `FleetPipelineState.totals.queue_depth`, agent count |
| Cost stable | Cost/task trend is flat or decreasing | Q34 trend analysis |
| No critical issues | Zero critical-severity issues | `FleetPipelineState` issues |
| Error rate low | `error_rate < 5%` | `MetricsResponse.summary` |
| Heartbeats healthy | All agents have heartbeat < threshold | `AgentSummary[].heartbeat_age_seconds` |

| **UI component** | **Scale Readiness Assessment** — checklist with pass/fail for each criterion. Each row: `[✓/✗] criterion description — current value`. Overall verdict: "Ready to scale" (all pass, green banner), "Address {N} items first" (some fail, amber banner), "Not ready" (>3 fail, red banner). At bottom: summary recommendations for failing items |
| **Update** | On range change or fetch cycle |

---

## 6. Smart Detectors

Smart Detectors are cross-cutting anomaly detection widgets that appear in the Optimization section. They combine data from multiple endpoints to surface non-obvious problems.

| Detector | Questions Served | Data Sources | Detection Logic |
|----------|-----------------|--------------|-----------------|
| **Prompt Bloat Detector** | Q20 | `GET /v1/cost/calls` | `tokens_in / tokens_out > 10` across multiple calls |
| **Silent Drop Detector** | Q22 | `GET /v1/pipeline`, `GET /v1/agents` | Queue items aged >5min with idle agent |
| **Contradiction Detector** | Q23 | `GET /v1/agents`, `GET /v1/pipeline` | Agent idle but queue_depth > 0 |
| **Recurring Failure Detector** | Q24, Q28 | `GET /v1/agents/{id}/pipeline` (issues) | `occurrence_count > 3` on active issues |
| **Heartbeat Drift Detector** | Q25 | `GET /v1/events?event_type=heartbeat` | Payload structure changes over time |
| **Action Failure Detector** | Q27 | `GET /v1/events?event_type=action_failed` | Same action_name failing repeatedly |

### Detector UI Pattern

All detectors follow a consistent card layout:

```
┌─────────────────────────────────────────┐
│  [icon] Detector Name           [●/●/●] │  ← status dot: green/amber/red
│─────────────────────────────────────────│
│  Summary line (e.g., "3 issues found")  │
│                                         │
│  [Expandable detail table/list]         │
│                                         │
│  Last checked: {timeAgo}                │
└─────────────────────────────────────────┘
```

---

## 7. State Management

`insights.js` maintains its own state, separate from `hiveboard.js`:

```javascript
// ── Insights State ──────────────────────────────
let insightsInitialized = false;    // Has initInsights() run at least once
let insightsRange = '24h';          // Selected time range
let insightsAgent = null;           // Selected agent ID for Investigation section (null = all/first)
let insightsRefreshTimer = null;    // Interval ID for auto-refresh
let insightsCollapsed = [];         // Collapsed section indices from localStorage

// Cached data (fetched on tab activation)
let insAgents = [];                 // AgentSummary[] from GET /v1/agents
let insMetrics = null;              // MetricsResponse from GET /v1/metrics
let insCost = null;                 // CostSummary from GET /v1/cost
let insCostTimeseries = [];         // CostTimeBucket[] from GET /v1/cost/timeseries
let insCostCalls = [];              // LlmCallRecord[] from GET /v1/cost/calls
let insFleetPipeline = null;        // FleetPipelineState from GET /v1/pipeline
let insAgentPipeline = null;        // PipelineState for selected agent
let insTasks = [];                  // TaskSummary[] from GET /v1/tasks
let insEvents = [];                 // Event[] (recent non-heartbeat)
let insHeartbeatEvents = [];        // Event[] (heartbeats for selected agent, Q25)

// Derived state (computed from cached data)
let insHealthScore = null;          // { score, classification, penalties }
let insScaleReadiness = null;       // { checks[], verdict }
let insDetectorResults = {};        // { bloat, silentDrop, contradiction, recurring, drift, actionFailure }
```

---

## 8. Data Fetching

### 8.1 Lazy Load on Tab Activation

Data is **not** fetched until the user clicks the Insights tab. `initInsights()` is the entry point:

```javascript
async function initInsights() {
  if (!insightsInitialized) {
    insightsInitialized = true;
    loadCollapsedState();
    renderInsightsShell();          // Render static layout (sections, toolbar)
    bindInsightsEvents();           // Attach event listeners
  }
  await refreshInsights();          // Fetch all data and re-render
  startInsightsPolling();           // Start 30s refresh cycle
}
```

### 8.2 Fetch Strategy

All independent API calls are made in parallel using `Promise.all`:

```javascript
async function refreshInsights() {
  const range = insightsRange;
  const agentId = insightsAgent;

  // Phase 1: Parallel fetch of all global data
  const [agents, metrics, cost, costTs, costCalls, pipeline, tasks, events] =
    await Promise.all([
      apiFetch('/v1/agents'),
      apiFetch('/v1/metrics', { range }),
      apiFetch('/v1/cost', { range }),
      apiFetch('/v1/cost/timeseries', { range }),
      apiFetch('/v1/cost/calls', { limit: 200 }),
      apiFetch('/v1/pipeline'),
      apiFetch('/v1/tasks', { limit: 100, sort: '-started_at' }),
      apiFetch('/v1/events', { exclude_heartbeats: true, limit: 20 }),
    ]);

  // Store results
  insAgents = agents?.agents || [];
  insMetrics = metrics || null;
  insCost = cost || null;
  insCostTimeseries = costTs?.buckets || costTs || [];
  insCostCalls = costCalls?.items || [];
  insFleetPipeline = pipeline || null;
  insTasks = tasks?.items || [];
  insEvents = events?.items || [];

  // Phase 2: Per-agent fetch (if agent selected)
  if (agentId) {
    const [agentPipeline, hbEvents] = await Promise.all([
      apiFetch(`/v1/agents/${agentId}/pipeline`),
      apiFetch('/v1/events', { agent_id: agentId, event_type: 'heartbeat', limit: 50 }),
    ]);
    insAgentPipeline = agentPipeline || null;
    insHeartbeatEvents = hbEvents?.items || [];
  }

  // Phase 3: Compute derived state
  computeHealthScore();
  computeScaleReadiness();
  runDetectors();

  // Phase 4: Render all sections
  renderAllInsightsSections();
}
```

### 8.3 Refresh Strategy

| Trigger | Action |
|---------|--------|
| Tab activation | Full `refreshInsights()` |
| Range selector change | Full `refreshInsights()` with new range |
| Agent selector change | Phase 2 + re-render Investigation section |
| Manual refresh button | Full `refreshInsights()` |
| Auto-refresh (every 30s) | Full `refreshInsights()` while tab is active |
| Tab deactivation | `clearInterval(insightsRefreshTimer)` to stop polling |

### 8.4 API Call Budget

Per refresh cycle, the Insights tab makes:
- **8 parallel global calls** (agents, metrics, cost, cost/timeseries, cost/calls, pipeline, tasks, events)
- **2 parallel per-agent calls** (if agent selected: pipeline, heartbeat events)
- **Total: 8–10 API calls per cycle**, well within rate limits (30 req/s/key)

---

## 9. CSS

New styles are added to `static/css/style.css`. All new classes are prefixed with `insights-` to avoid conflicts.

### 9.1 Reused CSS Variables

The Insights tab reuses the existing CSS custom properties:

```css
/* Already defined in style.css */
--bg-primary, --bg-secondary, --bg-tertiary
--text-primary, --text-secondary, --text-muted
--border-color, --border-subtle
--accent, --accent-hover
--success, --warning, --error
--status-idle, --status-processing, --status-waiting, --status-error, --status-stuck
```

### 9.2 New Styles Required

```css
/* Section layout */
.insights-toolbar { }              /* Range/agent/refresh controls */
.insights-section { }              /* Collapsible section wrapper */
.insights-section-header { }       /* Clickable header with chevron */
.insights-section-body { }         /* Content area, toggleable */
.insights-grid { }                 /* CSS grid for cards: repeat(auto-fill, minmax(280px, 1fr)) */

/* Cards */
.insights-card { }                 /* Standard card (border, padding, rounded corners) */
.insights-card-header { }          /* Card title + optional status dot */
.insights-card-body { }            /* Card content */
.insights-card--detector { }       /* Smart detector variant (slightly different border) */
.insights-big-number { }           /* Large metric display (font-size: 2rem) */
.insights-trend { }                /* Trend arrow + text */
.insights-trend--up { }            /* Green arrow up */
.insights-trend--down { }          /* Red arrow down (or green for cost/duration) */
.insights-trend--flat { }          /* Gray flat arrow */

/* Charts */
.insights-sparkline { }            /* Small inline chart container */
.insights-chart { }                /* Full-width chart container */
.insights-bar { }                  /* Horizontal bar in bar charts */
.insights-bar-fill { }             /* Filled portion of bar */

/* Tables */
.insights-table { }                /* Compact table styling */
.insights-table th { }
.insights-table td { }
.insights-table tr.expandable { }  /* Clickable row */
.insights-table tr.detail-row { }  /* Hidden detail row, shown on click */

/* Badges & pills */
.insights-badge { }                /* Small count badge */
.insights-badge--red { }
.insights-badge--amber { }
.insights-badge--green { }
.insights-pill { }                 /* Larger pill for attention count */

/* Health gauge */
.insights-gauge { }                /* SVG donut chart container */
.insights-gauge-label { }          /* Center text */

/* Checklist */
.insights-checklist { }            /* Scale readiness checklist */
.insights-check-pass { }           /* Green check */
.insights-check-fail { }           /* Red X */

/* Responsive */
@media (max-width: 768px) {
  .insights-grid { grid-template-columns: 1fr; }
}
```

---

## 10. API Model Reference

Exact Pydantic model fields consumed by the Insights tab, organized by endpoint.

### `GET /v1/agents` → `AgentSummary[]`

```
agent_id            : str
agent_type          : str
agent_version       : str | None
framework           : str | None
runtime             : str | None
sdk_version         : str | None
environment         : str | None
group               : str | None
derived_status      : str          # idle | processing | waiting_approval | error | stuck
current_task_id     : str | None
current_project_id  : str | None
last_heartbeat      : str | None   # ISO 8601
heartbeat_age_seconds : float | None
is_stuck            : bool
stuck_threshold_seconds : int
first_seen          : str          # ISO 8601
last_seen           : str          # ISO 8601
stats_1h            : AgentStats1h | None
```

### `AgentStats1h`

```
tasks_completed     : int
tasks_failed        : int
success_rate        : float        # 0.0 – 100.0
avg_duration_ms     : float
total_cost          : float
throughput          : float        # tasks/hour
queue_depth         : int
active_issues       : int
```

### `GET /v1/metrics` → `MetricsResponse`

```
range               : str          # 1h | 6h | 24h | 7d | 30d
interval            : str          # 5m | 15m | 1h | 6h | 1d
summary             : MetricsSummary
timeseries          : list[TimeseriesBucket]
groups              : list[MetricsGroup] | None  # present when group_by is set
```

### `MetricsSummary`

```
total_tasks         : int
completed           : int
failed              : int
escalated           : int
stuck               : int
success_rate        : float
avg_duration_ms     : float
total_cost          : float
avg_cost_per_task   : float
```

### `TimeseriesBucket`

```
timestamp           : str          # ISO 8601
tasks_completed     : int
tasks_failed        : int
avg_duration_ms     : float
cost                : float
error_count         : int
throughput          : float
```

### `GET /v1/cost` → `CostSummary`

```
total_cost          : float
call_count          : int
total_tokens_in     : int
total_tokens_out    : int
by_agent            : list[CostByAgent]    # each: agent_id, cost, call_count, tokens_in, tokens_out
by_model            : list[CostByModel]    # each: model, cost, call_count, tokens_in, tokens_out
reported_cost       : float
estimated_cost      : float
```

### `GET /v1/cost/timeseries` → `CostTimeBucket[]`

```
timestamp           : str
cost                : float
call_count          : int
tokens_in           : int
tokens_out          : int
```

### `GET /v1/cost/calls` → `Page[LlmCallRecord]`

```
LlmCallRecord:
  event_id          : str
  agent_id          : str
  project_id        : str | None
  task_id           : str | None
  timestamp         : str
  name              : str | None     # LLM call name/purpose
  model             : str
  tokens_in         : int
  tokens_out        : int
  cost              : float
  duration_ms       : float
  cost_source       : str           # reported | estimated
  cost_model_matched: bool
  prompt_preview    : str | None
  response_preview  : str | None
```

### `GET /v1/pipeline` → `FleetPipelineState`

```
totals:
  queue_depth       : int
  active_issues     : int
  active_todos      : int
  scheduled_count   : int
agents              : list[AgentPipelineSummary]
  # each: agent_id, queue_depth, active_todos, active_issues, scheduled_count
```

### `GET /v1/agents/{id}/pipeline` → `PipelineState`

```
agent_id            : str
queue:
  depth             : int
  oldest_age_seconds: float | None
  items             : list[QueueItem]     # each: id, priority, source, summary, queued_at
  processing        : QueueProcessing | None  # id, summary, started_at, elapsed_ms
todos               : list[TodoData]
scheduled           : list[ScheduledItem]
issues              : list[IssueData]
  # each: severity, issue_id, category, context, action, occurrence_count
```

### `GET /v1/tasks` → `Page[TaskSummary]`

```
TaskSummary:
  task_id           : str
  task_type         : str | None
  task_run_id       : str | None
  agent_id          : str
  project_id        : str | None
  derived_status    : str           # processing | completed | failed | escalated | waiting
  started_at        : str | None
  completed_at      : str | None
  duration_ms       : float | None
  total_cost        : float
  action_count      : int
  error_count       : int
  has_escalation    : bool
  has_human_intervention : bool
  llm_call_count    : int
  total_tokens_in   : int
  total_tokens_out  : int
```

### `GET /v1/events` → `Page[Event]`

```
Event:
  event_id          : str
  tenant_id         : str
  agent_id          : str
  agent_type        : str | None
  project_id        : str | None
  timestamp         : str
  received_at       : str
  environment       : str | None
  group             : str | None
  task_id           : str | None
  task_type         : str | None
  task_run_id       : str | None
  correlation_id    : str | None
  action_id         : str | None
  parent_action_id  : str | None
  event_type        : str           # 13 types (see enums)
  severity          : str           # debug | info | warn | error
  status            : str | None
  duration_ms       : float | None
  parent_event_id   : str | None
  payload           : Payload | None
    kind            : str | None    # 7 payload kinds
    summary         : str | None
    data            : dict | None
    tags            : list[str] | None
```

---

## 11. Data Availability Matrix

Status of each question's answerability using existing API endpoints:

| # | Question | Status | Notes |
|---|----------|--------|-------|
| Q1 | Are my agents running? | **YES** | `GET /v1/agents` → `derived_status` |
| Q2 | Does anything need attention? | **YES** | Agents + pipeline totals |
| Q3 | Is anything stuck? | **YES** | `is_stuck` field |
| Q4 | Is work flowing? | **YES** | `GET /v1/metrics` timeseries |
| Q5 | Is anything happening right now? | **YES** | `GET /v1/events` |
| Q6 | What is this agent doing? | **YES** | `GET /v1/agents/{id}` |
| Q7 | Is heartbeat healthy? | **YES** | `heartbeat_age_seconds` + threshold |
| Q8 | Pending work? | **YES** | `GET /v1/agents/{id}/pipeline` → queue |
| Q9 | Agent-reported problems? | **YES** | Pipeline → issues |
| Q10 | Task steps? | **YES** | Link to Mission Control timeline |
| Q11 | Plan progress? | **YES** | Link to Mission Control timeline |
| Q12 | Which tool failed? | **YES** | Link to Mission Control timeline |
| Q13 | Which LLM called? | **YES** | Link to Mission Control timeline |
| Q14 | Step durations? | **YES** | Link to Mission Control timeline |
| Q15 | Escalated? | **YES** | Link to Mission Control timeline |
| Q16 | Share investigation? | **PARTIAL** | Permalink generation is frontend-only; no read-only API key endpoint yet |
| Q17 | Agent costs? | **YES** | `GET /v1/cost` |
| Q18 | Expensive models? | **YES** | `GET /v1/cost/calls` + tasks join |
| Q19 | Cost spike? | **YES** | `GET /v1/cost/timeseries` |
| Q20 | Prompt bloat? | **YES** | `GET /v1/cost/calls` → token ratios |
| Q21 | Cost variance across agents? | **YES** | `GET /v1/cost` by_agent + agent metadata |
| Q22 | Silent drops? | **YES** | Pipeline queue age + agent status |
| Q23 | Queue vs idle contradiction? | **YES** | Agents + pipeline |
| Q24 | Credentials failing? | **YES** | Pipeline issues → occurrence_count |
| Q25 | Heartbeat drift? | **PARTIAL** | Heartbeat events exist but structured drift detection is heuristic |
| Q26 | Approvals backing up? | **YES** | Agents → `waiting_approval` status |
| Q27 | Action failure patterns? | **YES** | Events → `action_failed` + `action_completed` |
| Q28 | Recurring issues? | **YES** | Pipeline issues → `occurrence_count` |
| Q29 | Success rate improving? | **YES** | Metrics timeseries trend |
| Q30 | Tasks faster or slower? | **YES** | Metrics timeseries trend |
| Q31 | Which agent fails most? | **YES** | Metrics `group_by=agent` |
| Q32 | Better after deploys? | **PARTIAL** | No deploy events; use `agent_version` as proxy |
| Q33 | Total infrastructure cost? | **YES** | `GET /v1/cost` |
| Q34 | Cost per task trend? | **YES** | Metrics + cost timeseries |
| Q35 | ROI proof? | **PARTIAL** | Current metrics available; baseline requires user input |
| Q36 | Agents in production? | **YES** | Agent count + environment grouping |
| Q37 | Fleet health? | **YES** | Composite score from agents + metrics + pipeline |
| Q38 | Ready to scale? | **YES** | Checklist from all summary data |

**Totals: 33 YES, 4 PARTIAL, 0 NO**

---

## 12. Future Backend Enhancements

These are nice-to-have endpoints that would improve specific questions. None are required for the initial Insights tab implementation.

### 12.1 `GET /v1/cost/by-purpose`

**Benefits**: Q18 (model efficiency) and Q20 (prompt bloat) — group LLM calls by `name` field to see cost by purpose (e.g., "planning", "summarization", "tool_selection").

**Proposed response**:
```json
{
  "groups": [
    { "name": "planning", "cost": 1.23, "call_count": 45, "avg_tokens_in": 2000, "avg_tokens_out": 500 }
  ]
}
```

### 12.2 `GET /v1/metrics/actions`

**Benefits**: Q27 (action failure patterns) — server-side aggregation of action success/failure rates instead of client-side event processing.

**Proposed response**:
```json
{
  "actions": [
    { "action_name": "search_web", "completed": 100, "failed": 5, "failure_rate": 0.047, "avg_duration_ms": 1200 }
  ]
}
```

### 12.3 `POST /v1/keys/read-only`

**Benefits**: Q16 (share investigation) — generate a read-only API key scoped to a specific task or time range for sharing.

### 12.4 Deploy Marker Events

**Benefits**: Q32 (before/after comparison) — `POST /v1/ingest` with `event_type=deploy` (or new custom event convention). Would allow precise before/after metric splits.

**Convention** (no backend change needed — uses existing `custom` event type):
```json
{
  "event_type": "custom",
  "payload": {
    "kind": null,
    "summary": "Deploy v2.1.0",
    "tags": ["deploy"],
    "data": { "version": "2.1.0", "previous_version": "2.0.3" }
  }
}
```

---

## Appendix A: Question-to-Endpoint Quick Reference

| Q# | Primary Endpoint(s) | Key Fields |
|----|---------------------|------------|
| 1 | `/v1/agents` | `derived_status` |
| 2 | `/v1/agents` + `/v1/pipeline` | `is_stuck`, `derived_status`, `totals.active_issues` |
| 3 | `/v1/agents` | `is_stuck`, `heartbeat_age_seconds` |
| 4 | `/v1/metrics` | `timeseries[].throughput`, `.tasks_completed`, `.cost` |
| 5 | `/v1/events` | `event_type`, `payload.summary` |
| 6 | `/v1/agents/{id}` | `derived_status`, `current_task_id` |
| 7 | `/v1/agents/{id}` | `heartbeat_age_seconds`, `stuck_threshold_seconds` |
| 8 | `/v1/agents/{id}/pipeline` | `queue.depth`, `queue.items[]` |
| 9 | `/v1/agents/{id}/pipeline` | `issues[]` |
| 10–16 | `/v1/tasks/{id}/timeline` | Link to Mission Control |
| 17 | `/v1/cost` | `total_cost`, `by_agent[]`, `by_model[]` |
| 18 | `/v1/cost/calls` + `/v1/tasks` | `model`, `cost`, `task_id` |
| 19 | `/v1/cost/timeseries` | `timestamp`, `cost`, `call_count` |
| 20 | `/v1/cost/calls` | `tokens_in`, `tokens_out` |
| 21 | `/v1/cost` + `/v1/agents` | `by_agent[]`, `agent_type` |
| 22 | `/v1/pipeline` | `queue_depth`, `oldest_age_seconds` |
| 23 | `/v1/agents` + `/v1/pipeline` | `derived_status`, `queue_depth` |
| 24 | `/v1/agents/{id}/pipeline` | `issues[].occurrence_count` |
| 25 | `/v1/events?event_type=heartbeat` | `payload.data` |
| 26 | `/v1/agents` | `derived_status='waiting_approval'` |
| 27 | `/v1/events?event_type=action_failed` | `payload.action_name` |
| 28 | `/v1/pipeline` | `issues[].occurrence_count`, `issues[].action` |
| 29 | `/v1/metrics` | `timeseries[]` → success rate |
| 30 | `/v1/metrics` | `timeseries[].avg_duration_ms` |
| 31 | `/v1/metrics?group_by=agent` | `groups[].summary.success_rate` |
| 32 | `/v1/metrics` + `/v1/agents` | `agent_version`, timeseries |
| 33 | `/v1/cost` | `total_cost` |
| 34 | `/v1/metrics` + `/v1/cost/timeseries` | cost per task trend |
| 35 | `/v1/metrics?range=30d` + `/v1/cost?range=30d` | summary + user baseline |
| 36 | `/v1/agents` | `agent_id`, `environment`, `first_seen` |
| 37 | `/v1/agents` + `/v1/metrics` + `/v1/pipeline` | composite score |
| 38 | `/v1/agents` + `/v1/metrics` + `/v1/cost` + `/v1/pipeline` | readiness checklist |

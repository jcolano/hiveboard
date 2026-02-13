# Insights Tab — Data Availability & UI/UX Analysis

## Overview

This document analyzes each of the **38 questions** from `3_What-HiveBoard-Sees.md` to determine:

1. **Do we have the data?** — Can the current backend/storage answer this?
2. **API source** — Which endpoint(s) provide the raw data?
3. **Processing needed** — What computation is required (server-side vs client-side)?
4. **UI/UX concept** — How this would render in the new Insights tab
5. **Scope** — Global (fleet-wide) vs Agent-specific (per selected agent)

### Architecture Note

The current `hiveboard.js` is **2,026 lines**. Rather than growing it further, the Insights tab should live in a **separate `insights.js`** file that:
- Shares `CONFIG` and the API client layer (extract to a small shared `api.js`)
- Has its own state, rendering logic, and poll loop
- Is loaded only when the Insights tab is active (lazy load)
- Has its own CSS section (or a separate `insights.css`)

---

## Data Layer Summary

| API Endpoint | Key Data Available |
|---|---|
| `GET /v1/agents` | Status, heartbeat age, stuck flag, stats_1h (success rate, tasks, cost, throughput, queue depth, active issues), sparkline |
| `GET /v1/tasks` | Per-task: status, duration_ms, cost, llm_call_count, tokens, error_count, escalation flags |
| `GET /v1/events` | Raw event stream with full payloads (llm_call, queue_snapshot, issue, plan_created, plan_step, todo, scheduled) |
| `GET /v1/metrics` | Summary stats + timeseries buckets (tasks, failures, duration, cost, throughput). Supports `group_by=agent\|model` and ranges 1h/6h/24h/7d/30d |
| `GET /v1/cost` | Total cost, by-agent, by-model breakdowns. Reported vs estimated separation |
| `GET /v1/cost/timeseries` | Cost over time in buckets |
| `GET /v1/cost/calls` | Individual LLM call records with model, tokens, cost, prompt/response previews |
| `GET /v1/llm-calls` | Same as cost/calls but with totals rollup |
| `GET /v1/agents/{id}/pipeline` | Queue (depth, items, oldest age), TODOs, scheduled items, issues (with occurrence counts) |
| `GET /v1/pipeline` | Fleet-wide pipeline aggregates |
| `GET /v1/tasks/{id}/timeline` | Full event sequence, action tree, plan with step statuses, error chains |

---

## Moment 1: The Glance (Questions 1–5)

> Scope: All global. These are fleet-level health signals.

---

### Q1: "Are my agents running?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — each agent has `last_heartbeat`, `heartbeat_age_seconds`, `derived_status` |
| **Processing** | None needed — server derives status. Client just maps to green/amber/red dot |
| **Scope** | Global |
| **UI/UX** | **Fleet Status Bar** at top of Insights tab. Row of agent dots: green = alive, amber = drifting (heartbeat age > 60s), red = stale (> stuck_threshold). Shows `N/M agents online`. Click any dot to jump to that agent's detail |
| **Data flow** | `agents[].heartbeat_age_seconds` → color class. `agents[].derived_status !== 'stuck'` → count as "running" |

---

### Q2: "Does anything need my attention?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — `derived_status` (stuck/error/waiting_approval), `stats_1h.active_issues` |
| **Processing** | Client-side: count agents where status ∈ {stuck, error, waiting_approval} OR active_issues > 0 |
| **Scope** | Global |
| **UI/UX** | **Attention Badge** — pulsing red pill: "3 need attention". Below it, a compact list of which agents and why (e.g., "sales-agent: stuck", "support-agent: 2 issues"). This is the #1 visual on the Insights tab |
| **Data flow** | `agents.filter(a => ['stuck','error','waiting_approval'].includes(a.derived_status) \|\| a.stats_1h.active_issues > 0)` |

---

### Q3: "Is anything stuck?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — `is_stuck`, `derived_status === 'stuck'` |
| **Processing** | Server already computes stuck status via heartbeat age vs `stuck_threshold_seconds` |
| **Scope** | Global |
| **UI/UX** | **Stuck Counter** in the Fleet Status Bar: "0 stuck" (green) or "2 stuck" (red, pulsing). Stuck agents listed by name with time-since-last-heartbeat. Also: `GET /v1/metrics` → `summary.stuck` gives the count directly |
| **Data flow** | `metricsData.summary.stuck` or `agents.filter(a => a.is_stuck).length` |

---

### Q4: "Is work flowing?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/metrics?range=1h` — `timeseries[]` with `tasks_completed`, `tasks_failed`, `throughput`, `cost` per bucket |
| **Processing** | Client-side: render timeseries as sparkline/mini-chart bars |
| **Scope** | Global |
| **UI/UX** | **4 Mini-Charts** (already exist in Mission Control's Stats Ribbon, can be replicated/shared): Throughput, Success Rate, Errors, LLM Cost/Task. Shape-based — no need to read numbers. Rising throughput bars + flat error bars = "yes, work is flowing" |
| **Data flow** | `metricsData.timeseries[].throughput` → bar heights. Already implemented in current `renderMiniCharts()` |

---

### Q5: "Is anything happening right now?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/events?limit=20&exclude_heartbeats=true` + WebSocket `event.new` messages |
| **Processing** | None — just show reverse-chronological feed |
| **Scope** | Global |
| **UI/UX** | **Live Activity Indicator** — green pulsing "Live" badge with timestamp of last event. If no events in last 60s, badge turns amber "Quiet". If no events in 5min, badge turns red "Silent". Compact: just the badge + "last event 4s ago", not a full stream |
| **Data flow** | `STREAM_EVENTS[0].timestamp` → compute age → badge color. WebSocket connection status → "Live" vs "Disconnected" |

---

## Moment 2: The Investigation (Questions 6–16)

> Scope: All agent-specific or task-specific. These require a selected agent/task.

---

### Q6: "What is this agent doing right now?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents/{id}` — `current_task_id`, `derived_status`. If processing: `GET /v1/tasks/{task_id}/timeline` for live progress |
| **Processing** | Client: show current task ID, elapsed time (now - task started_at), status |
| **Scope** | Agent-specific |
| **UI/UX** | **Agent Focus Panel** — When user selects an agent: large status badge (IDLE / PROCESSING / WAITING / ERROR / STUCK), current task ID as a clickable link, elapsed time counter. If idle: "Idle since {time_ago}". If stuck: red glow + "No heartbeat for {N}m" |
| **Data flow** | `agent.derived_status` + `agent.current_task_id` → display. `tasks.find(t => t.task_id === agent.current_task_id)` → elapsed time |

---

### Q7: "Is this agent's heartbeat healthy?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents/{id}` — `last_heartbeat`, `heartbeat_age_seconds`, sparkline data (12-point). Raw heartbeat events: `GET /v1/events?agent_id={id}&event_type=heartbeat` |
| **Processing** | Client: map heartbeat_age to green/amber/red. Sparkline already computed server-side |
| **Scope** | Agent-specific |
| **UI/UX** | **Heartbeat Health Card** — Three-state indicator (green/amber/red) + "Last heartbeat: 4s ago". Below: sparkline bar chart showing heartbeat frequency over past hour. Gaps in the bars = missed heartbeats |
| **Data flow** | `agent.heartbeat_age_seconds` → color. `agent.sparkline` → bar chart. Thresholds: green < 30s, amber < stuck_threshold, red >= stuck_threshold |

---

### Q8: "Does this agent have pending work?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents/{id}/pipeline` — `queue.depth`, `queue.items[]` (each with priority, source, age, summary), `queue.oldest_age_seconds` |
| **Processing** | None — data is pre-structured |
| **Scope** | Agent-specific |
| **UI/UX** | **Queue Panel** — Badge "Q:{depth}" with color (green ≤ 2, amber 3-5, red > 5). Expandable list of queue items showing: priority tag, source, age ("waiting 12m"), summary text. Oldest item highlighted if age > 2x avg processing time |
| **Data flow** | `pipeline.queue.depth` → badge. `pipeline.queue.items[]` → list rows. `pipeline.queue.oldest_age_seconds` → highlight if concerning |

---

### Q9: "Has this agent reported its own problems?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents/{id}/pipeline` — `issues[]` with `summary`, `severity`, `category`, `occurrence_count`, `context` |
| **Processing** | None — issues are pre-grouped by issue_id with occurrence counting |
| **Scope** | Agent-specific |
| **UI/UX** | **Issues Panel** — Red dot + "N issues" badge on agent card. Expandable table: severity icon (critical/high/medium/low), summary text, occurrence count ("x8"), category tag, first/last seen timestamps. High-occurrence issues sorted to top |
| **Data flow** | `pipeline.issues[]` → table rows. Filter by `action !== 'resolved' && action !== 'dismissed'` for active issues only |

---

### Q10: "What steps did this task take?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/tasks/{task_id}/timeline` — `action_tree` (hierarchical) + `events[]` (flat sequence) |
| **Processing** | Client: render tree nodes with color coding. Already implemented in current `renderActionTree()` and `renderFlatTimeline()` |
| **Scope** | Task-specific (agent-specific context) |
| **UI/UX** | **Task Timeline** — Tree view (default): indented action nodes with status icons + duration. Flat view: left-to-right node sequence with connecting lines. Color coding: blue=action, purple=LLM, red=failure, amber=escalation, green=completion. Each node clickable for detail |
| **Data flow** | `timeline.action_tree` → tree rendering. `timeline.events[]` → flat rendering. Node type derived from `event_type` + `payload.kind` |
| **Note** | This is already fully implemented in the current dashboard. The Insights tab could link directly to these existing timeline views rather than re-implementing them |

---

### Q11: "What was the plan, and where did it fail?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/tasks/{task_id}/timeline` — `plan` object with `goal`, `steps[]` (each with index, description, status: pending/active/completed/failed), `progress` (completed/total) |
| **Processing** | Client: render plan steps as segmented progress bar |
| **Scope** | Task-specific |
| **UI/UX** | **Plan Progress Bar** — Horizontal segmented bar above the timeline. Each segment = one plan step, color-coded: green (completed), blue (active), red (failed), gray (pending). Hover shows step description. A bar reading green-green-red-gray instantly tells the story |
| **Data flow** | `timeline.plan.steps[]` → segments. `step.status` → color. Already partially implemented in current `renderPlan()` |

---

### Q12: "Which tool failed?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/tasks/{task_id}/timeline` — `action_tree` nodes with `status: 'failed'`, plus `events[]` where `event_type === 'action_failed'` with payload containing tool name, error message, duration |
| **Processing** | Client: filter action_tree for failed nodes, extract tool/error details from event payload |
| **Scope** | Task-specific |
| **UI/UX** | Failed actions render as **red nodes** in the timeline tree. Click to expand: tool name, arguments received, error message, duration, retry count (if retry_started events follow). In the Insights tab, could add an **"Errors" summary section** per task that lists all failures without needing to scan the full timeline |
| **Data flow** | `timeline.action_tree` nodes where `status === 'failed'` + `timeline.error_chains[]` for linked error context |

---

### Q13: "Which LLM was called, and what did it see?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/tasks/{task_id}/timeline` — LLM events in action tree as child nodes with `model`, `tokens_in`, `tokens_out`, `cost`, `prompt_preview`, `response_preview`. Also: `GET /v1/llm-calls?task_id={id}` for a flat list |
| **Processing** | None — fully structured in API response |
| **Scope** | Task-specific |
| **UI/UX** | LLM calls render as **purple nodes** with model badge (e.g., "claude-sonnet"). Click to open **LLM Detail Modal**: model name, token counts (in/out bar), cost, duration, prompt preview (scrollable), response preview (scrollable). Token ratio visualization: wide input bar vs narrow output bar reveals prompt bloat at a glance |
| **Data flow** | `timeline.action_tree[].children.filter(c => c.type === 'llm_call')` → nodes. Click → modal with `prompt_preview`, `response_preview`. Already implemented in current `renderLlmModal()` |

---

### Q14: "How long did each step take?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/tasks/{task_id}/timeline` — every node has `duration_ms`. The flat events have timestamps for computing gaps |
| **Processing** | Client: compute time breakdown (LLM vs Tool vs Overhead percentages). Already implemented in `renderDurationBreakdown()` |
| **Scope** | Task-specific |
| **UI/UX** | **Duration labels** on timeline connectors between nodes. **Duration Breakdown Bar** below timeline: colored segments showing LLM time (purple), Tool time (blue), Overhead (gray) with percentages. Example: "LLM 42% | Tools 49% | Overhead 9%" |
| **Data flow** | `timeline.events[].duration_ms` → per-node labels. Categorize by node type → sum → percentage bar. Already implemented |

---

### Q15: "Was it escalated? Did it need human approval?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/tasks/{task_id}/timeline` — events with `event_type` ∈ {escalated, approval_requested, approval_received}. Task summary has `has_escalation`, `has_human_intervention` flags |
| **Processing** | None — event types directly indicate escalation/approval |
| **Scope** | Task-specific |
| **UI/UX** | Escalation events render as **amber nodes** on the timeline. Approval requests show as distinct nodes with approver name, reason, and resolution (approved/denied). In Insights tab's task list, escalated tasks get an amber "Escalated" badge. Filter: "Show only escalated tasks" |
| **Data flow** | `task.has_escalation` → badge. `timeline.events.filter(e => e.event_type === 'approval_requested')` → approval detail nodes |

---

### Q16: "Can I share this investigation?"

| Aspect | Detail |
|---|---|
| **Data available?** | PARTIAL |
| **API source** | Current dashboard uses URL hash routing (`#task/{task_id}`). The API supports direct task/timeline lookup by ID |
| **Processing** | Client: construct shareable URL with task_id (and optionally api_key or read-only token) |
| **Scope** | Task-specific |
| **UI/UX** | **Permalink button** on every timeline view — copies URL to clipboard. URL format: `https://{host}/dashboard?apiKey={read_key}#task/{task_id}`. Toast notification: "Link copied!" |
| **Data flow** | `window.location.origin + '/dashboard?apiKey=' + readOnlyKey + '#task/' + taskId` → clipboard |
| **Gap** | No read-only API key generation endpoint exists. Currently would share the user's live API key. **Needs**: a `POST /v1/keys/read-only` endpoint or a share-token mechanism. Alternatively, the existing `read` key type in the tenant model could be leveraged — but no endpoint exposes it yet |

---

## Moment 3: The Optimization (Questions 17–28)

> Scope: Mix of global and agent-specific. These are the analytical/proactive insights.

---

### Q17: "How much are my agents costing me?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/cost?range={range}` — `total_cost`, `by_agent[]`, `by_model[]`. Each breakdown has `cost`, `call_count`, `tokens_in`, `tokens_out`, `estimated_cost` |
| **Processing** | None — fully aggregated server-side |
| **Scope** | Global (with agent drill-down) |
| **UI/UX** | **Cost Overview Card** — Big number: "$47.23 (24h)". Below: two side-by-side tables. Left: "Cost by Agent" — rows: agent name, cost, call count, % of total. Right: "Cost by Model" — rows: model name, cost, call count, % of total. Range selector: 24h / 7d / 30d. Bar chart on each row showing proportional cost |
| **Data flow** | `costData.total_cost` → header. `costData.by_agent[]` → left table. `costData.by_model[]` → right table. Percentage: `(row.cost / costData.total_cost * 100).toFixed(1)` |

---

### Q18: "Am I using expensive models where cheap ones would work?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/cost` — `by_model[]` for model cost distribution. `GET /v1/llm-calls` — individual calls with `model`, `name` (call purpose), `tokens_in`, `tokens_out`, `cost` |
| **Processing** | Client-side: group LLM calls by `name` (purpose), show which models are used for each purpose, highlight where expensive models do simple work |
| **Scope** | Global |
| **UI/UX** | **Model Efficiency Matrix** — Table: rows = call purposes (e.g., "classify_intent", "lead_scoring"), columns = model used, cells = call count + avg cost. Highlight cells where an expensive model (opus) is used for a high-frequency, low-complexity call. Recommendation badge: "classify_intent uses claude-opus ($0.04/call avg) — consider haiku ($0.004/call)" |
| **Data flow** | `GET /v1/llm-calls?limit=500` → group by `name` → within each group, sub-group by `model` → compute avg cost per call → flag expensive model + high frequency combinations |
| **New processing** | This requires **client-side aggregation** of LLM calls grouped by purpose (name field). Not currently computed by any endpoint. Could be a new server endpoint (`GET /v1/cost/by-purpose`) or done client-side with paginated fetches |

---

### Q19: "Why did costs spike?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/cost/timeseries?range=7d` — cost over time in buckets. `GET /v1/llm-calls?since={spike_start}&until={spike_end}` — drill into calls during spike |
| **Processing** | Client-side: detect spike (bucket cost > 2x moving average), then fetch LLM calls in that window for investigation |
| **Scope** | Global |
| **UI/UX** | **Cost Timeline Chart** — Stacked area chart (by model) over time. Spikes are visually obvious. Click on a spike region to drill down: shows "During this period: {N} LLM calls, {model breakdown}, {top agents}". Compare to surrounding periods. Automatic spike detection: red markers on buckets that exceed 2x the 7-day average |
| **Data flow** | `costTimeseries[]` → chart data points. Spike detection: `bucket.cost > 2 * avgCost` → red marker. Drill-down: `GET /v1/llm-calls?since={bucket.start}&until={bucket.end}` → detail table |

---

### Q20: "Is there prompt bloat?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/llm-calls` — each record has `tokens_in`, `tokens_out`, `name`, `model`, `prompt_preview` |
| **Processing** | Client-side: compute tokens_in/tokens_out ratio per call. High ratio (>10:1) with high absolute tokens_in (>5000) = probable bloat |
| **Scope** | Global (or agent-specific with `?agent_id=`) |
| **UI/UX** | **Prompt Bloat Detector** — Table of LLM calls sorted by tokens_in descending. Columns: call name, model, tokens_in, tokens_out, ratio (in:out), cost. Rows with ratio > 10:1 AND tokens_in > 5000 get a yellow "Bloat?" warning badge. Click to see prompt_preview. Visual: dual bar (wide input bar, narrow output bar) makes bloat obvious at a glance |
| **Data flow** | `llmCalls.sort((a,b) => b.tokens_in - a.tokens_in)` → table. `ratio = tokens_in / tokens_out` → flag if > 10. The token ratio bar already exists in the current dashboard's LLM modal |

---

### Q21: "Are different agents doing similar work at different costs?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/cost?range=7d` — `by_agent[]` with cost per agent. `GET /v1/metrics?range=7d&group_by=agent` — tasks completed per agent. Cross-reference: cost_per_task = agent_cost / tasks_completed |
| **Processing** | Client-side: compute cost-per-task per agent, group agents by type, compare within groups |
| **Scope** | Global |
| **UI/UX** | **Agent Cost Comparison** — Group agents by `agent_type`. Within each group, show side-by-side: agent name, total cost, tasks completed, **cost per task**. Highlight the most expensive agent in each group. Example: "sales-v2: $0.12/task vs sales-v1: $0.06/task — 2x more expensive". Bar chart comparing cost/task across agents of the same type |
| **Data flow** | `costData.by_agent[]` → agent costs. `metricsData.groups[]` (with group_by=agent) → tasks per agent. Join on agent_id → cost_per_task = cost / tasks_completed |

---

### Q22: "Are tasks being silently dropped?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents/{id}/pipeline` — `queue.items[]` with age per item, `queue.oldest_age_seconds`. `GET /v1/metrics` — avg_duration_ms gives baseline for "normal" processing time |
| **Processing** | Client-side: compare oldest_age_seconds against avg_duration_ms. If oldest item age >> avg processing time → suspected silent drop |
| **Scope** | Agent-specific (can scan fleet via `GET /v1/pipeline`) |
| **UI/UX** | **Silent Drop Detector** — For each agent: compare oldest queue item age to average task duration. If oldest_age > 5x avg_duration → red alert: "Agent {name} has items waiting {age} — avg processing time is {avg}. Possible silent drop." Fleet view: scan all agents, surface any with this pattern. Icon: hourglass with exclamation mark |
| **Data flow** | `pipeline.queue.oldest_age_seconds` vs `agent.stats_1h.avg_duration_ms / 1000`. Flag if ratio > 5x. Fleet scan: `fleetPipeline.agents.filter(a => a.queue_depth > 0)` → check each |

---

### Q23: "Is the queue growing while the agent reports idle?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — `derived_status` + `stats_1h.queue_depth`. Or more directly: agent status = 'idle' AND pipeline queue depth > 0 |
| **Processing** | Client-side: simple boolean check per agent |
| **Scope** | Agent-specific (fleet scan possible) |
| **UI/UX** | **Contradiction Detector** — Card that scans for agents where `status === 'idle' && queue_depth > 0`. Each match shown as: "Agent {name}: IDLE but Q:{depth} items waiting". This is a clear bug signal — scheduling issue, polling problem, or silent crash recovery. Red warning card with agent link |
| **Data flow** | `agents.filter(a => a.derived_status === 'idle' && a.stats_1h.queue_depth > 0)` → warning list |

---

### Q24: "Are credentials failing silently?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents/{id}/pipeline` — `issues[]` with summary, occurrence_count, category. Also: `GET /v1/events?agent_id={id}&event_type=action_failed` — repeated failures of the same action |
| **Processing** | Client-side: look for issues with high occurrence counts, or repeated action_failed events with similar error patterns |
| **Scope** | Agent-specific (fleet scan via `GET /v1/pipeline`) |
| **UI/UX** | **Recurring Failure Detector** — Scan issues for patterns like "API returning 4xx" with occurrence_count > 3. Show: issue summary, severity, occurrence count, first seen, last seen. Also: scan action_failed events for the same action name failing repeatedly. Alert card: "CRM API returning 403 — 8 occurrences in 2 hours. Possible credential expiration." |
| **Data flow** | `pipeline.issues.filter(i => i.occurrence_count > 3)` → credential failure candidates. Pattern match on summary for "401", "403", "unauthorized", "expired" keywords |

---

### Q25: "Is the heartbeat doing less than it used to?"

| Aspect | Detail |
|---|---|
| **Data available?** | PARTIAL |
| **API source** | `GET /v1/events?agent_id={id}&event_type=heartbeat&limit=50` — heartbeat events with payload data. Payload may contain operational summaries |
| **Processing** | Client-side: compare payload.data fields across recent heartbeats. Detect missing fields or reduced activity indicators |
| **Scope** | Agent-specific |
| **UI/UX** | **Heartbeat Payload Diff** — Show last N heartbeat payloads side-by-side. Highlight fields that were present in older heartbeats but missing in recent ones. Example: "2 hours ago: {crm_sync: true, email_check: true} → now: {crm_sync: true}" — email_check disappeared |
| **Gap** | This depends entirely on **what the agent sends in heartbeat payloads**. The schema supports arbitrary payload data, but there's no guarantee agents send structured operational summaries. If they do, this works perfectly. If heartbeats are bare (no payload.data), this question cannot be answered. Looking at the actual data: heartbeat events exist (22 in dataset) but their payload richness varies. **Recommendation**: Document best practice for agents to include operational summary in heartbeat payload |

---

### Q26: "Are human approvals backing up?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — count agents with `derived_status === 'waiting_approval'`. `GET /v1/events?event_type=approval_requested` and `GET /v1/events?event_type=approval_received` — compute pending approvals and response times |
| **Processing** | Client-side: count pending approvals (requested but not yet received). Compute avg approval response time |
| **Scope** | Global |
| **UI/UX** | **Approval Queue Monitor** — Counter: "N approvals pending". List: each pending approval with agent name, request summary, time waiting. Response time metric: "Avg approval time: 12m". If any approval has been pending > 30min, red alert. Trend: are approvals being resolved faster or slower over time? |
| **Data flow** | `events.filter(e => e.event_type === 'approval_requested')` → get approval requests. Match with `approval_received` events by task_id. Unmatched = still pending. Time difference = response time |

---

### Q27: "Which action within a plan consistently fails?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/events?event_type=action_failed` — all failed actions across all tasks. Each has `payload.summary` or action name, plus `action_id` |
| **Processing** | Client-side: group failed actions by name/summary, count occurrences, compute failure rate per action name |
| **Scope** | Global (or agent-specific with `?agent_id=`) |
| **UI/UX** | **Action Failure Heatmap** — Table: rows = action names (e.g., "enrich_company", "fetch_crm_data"), columns = total calls, failures, failure rate %. Sorted by failure rate descending. Rows with failure rate > 20% highlighted red. Shows: "enrich_company fails 40% of the time (24/60 calls)" |
| **Data flow** | `GET /v1/events?event_type=action_failed` → group by action name (from payload.summary or action_id patterns). Cross-reference with `action_completed` events for the same action names to get total attempts → compute failure rate |
| **Note** | Requires fetching both `action_failed` and `action_completed` events to compute rates. Could benefit from a new server-side endpoint: `GET /v1/metrics/actions` that pre-computes per-action success/failure rates |

---

### Q28: "Is the same issue recurring without resolution?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/pipeline` (fleet) or `GET /v1/agents/{id}/pipeline` — `issues[]` with `occurrence_count`, resolution state |
| **Processing** | None — already tracked with occurrence counts |
| **Scope** | Agent-specific (fleet scan possible) |
| **UI/UX** | **Unresolved Issues Board** — Fleet-wide view of all active (unresolved) issues sorted by occurrence_count descending. Columns: agent, severity icon, summary, occurrences, first seen, last seen. An issue at "x50 occurrences" with no resolution in 3 days is a critical signal. Color-code by staleness: how long since first reported with no resolution |
| **Data flow** | `fleetPipeline.agents[]` → for each agent, `GET /v1/agents/{id}/pipeline` → `issues.filter(i => !i.resolved)` → flatten + sort by occurrence_count desc |

---

## Moment 4: The Review (Questions 29–38)

> Scope: Mostly global. These are trend-oriented, strategic questions.

---

### Q29: "Is my success rate improving?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/metrics?range=7d` — `timeseries[]` with `tasks_completed` and `tasks_failed` per bucket → compute success rate per bucket. Also `summary.success_rate` for current window |
| **Processing** | Client-side: compute success_rate per timeseries bucket: `completed / (completed + failed) * 100`. Plot trend line |
| **Scope** | Global (or agent-specific with `?agent_id=`) |
| **UI/UX** | **Success Rate Trend Chart** — Line chart over time (7d/30d). Y-axis: success rate %. Horizontal reference line at your baseline (e.g., 95%). Color: green when above baseline, red when below. Annotation markers for deploy events (if tracked). Summary: "Success rate: 94.2% (7d avg) — up from 91.8% last week" |
| **Data flow** | `metricsData.timeseries[]` → per-bucket: `success_rate = tasks_completed / (tasks_completed + tasks_failed) * 100` → line chart points |

---

### Q30: "Are tasks getting faster or slower?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/metrics?range=7d` — `timeseries[]` with `avg_duration_ms` per bucket. `summary.avg_duration_ms` for current window |
| **Processing** | Client-side: plot avg_duration over time. Compute trend (simple linear regression or compare first half vs second half of range) |
| **Scope** | Global (or agent-specific) |
| **UI/UX** | **Duration Trend Chart** — Line chart: avg task duration over time. Format Y-axis in human-readable (e.g., "2.4s", "1.2m"). Show trend arrow: up (slowing, red), down (improving, green), flat (stable, gray). Summary: "Avg duration: 8.2s (7d) — 12% faster than previous 7d" |
| **Data flow** | `metricsData.timeseries[].avg_duration_ms` → line chart. Trend = compare average of last N/2 buckets vs first N/2 buckets |
| **Enhancement** | For the "Time Breakdown" (LLM vs Tools vs Overhead), would need to aggregate across multiple task timelines — not currently available as a fleet-level metric. Would need a new endpoint or client-side sampling |

---

### Q31: "Which agent fails most often?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/metrics?range=7d&group_by=agent` — per-agent metrics with tasks_completed, tasks_failed → compute failure rate. Also `GET /v1/agents` — `stats_1h` per agent |
| **Processing** | Client-side: compute failure_rate per agent, sort descending |
| **Scope** | Global |
| **UI/UX** | **Agent Reliability Ranking** — Table sorted by failure rate (worst first): agent name, tasks total, tasks failed, failure rate %, sparkline trend (from agent card data). Agent with highest failure rate highlighted red. Comparison: if all agents are ~2% failure but one is 15%, it has a localized problem |
| **Data flow** | `metricsData.groups[]` (group_by=agent) → per-agent: `failure_rate = failed / (completed + failed) * 100` → sort desc → table |

---

### Q32: "Are agents getting better after deploys?"

| Aspect | Detail |
|---|---|
| **Data available?** | PARTIAL |
| **API source** | `GET /v1/metrics?range=7d` — timeseries for before/after comparison. `GET /v1/agents` — `agent_version` field could indicate version changes |
| **Processing** | Client-side: if deploy timestamps are known, split timeseries at deploy point and compare metrics before vs after |
| **Scope** | Global or agent-specific |
| **UI/UX** | **Before/After Comparison** — User places a "deploy marker" on the timeline (date picker). Dashboard splits all metrics at that point: before vs after. Table: metric name, before value, after value, change (%, green/red arrow). Metrics: success rate, avg duration, cost per task, avg turns per task. Quick read: "After deploy: success rate +3.2%, cost/task -18%, duration -8%" |
| **Gap** | **No deploy event tracking**. The system doesn't automatically know when deploys happened. Two options: (1) User manually sets a comparison point via date picker (works today), (2) Add a `deploy` event type that agents or CI/CD can emit (needs new instrumentation). `agent_version` changes could serve as a proxy — when version changes, infer a deploy |

---

### Q33: "What's our total agent infrastructure cost?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/cost?range=30d` — `total_cost`, with `by_agent[]` and `by_model[]` breakdowns |
| **Processing** | None — single number from API |
| **Scope** | Global |
| **UI/UX** | **Total Cost Card** — Large number: "$1,247.83 (30d)". Subtitle: "Reported: $1,180.40 | Estimated: $67.43". Below: donut chart split by model. Second donut by agent. Range selector: 24h / 7d / 30d. Comparison: "vs previous period: +12% ($134.20)" |
| **Data flow** | `costData.total_cost` → big number. `costData.reported_cost` + `costData.estimated_cost` → subtitle. `costData.by_model[]` → donut segments |

---

### Q34: "Is cost per task trending up or down?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/cost/timeseries?range=7d` — cost per bucket. `GET /v1/metrics?range=7d` — tasks per bucket. Cross-reference: cost_per_task per bucket |
| **Processing** | Client-side: for each time bucket, divide cost by tasks_completed to get cost_per_task. Plot trend |
| **Scope** | Global |
| **UI/UX** | **Cost/Task Trend Chart** — Line chart: cost per task over time. Show trend arrow (up=red, down=green). Summary: "Cost/task: $0.034 avg (7d) — down 22% from previous 7d". If trending up, show possible causes: "Prompt sizes increased 15%" or "Model mix shifted toward opus" |
| **Data flow** | Join `costTimeseries[i].cost / metricsTimeseries[i].tasks_completed` → per-bucket cost_per_task → line chart. Requires aligning bucket timestamps between the two endpoints |
| **Note** | `metricsData.summary.avg_cost_per_task` already provides the current-window number. The timeseries join enables the trend view |

---

### Q35: "Can I prove ROI on agent observability?"

| Aspect | Detail |
|---|---|
| **Data available?** | PARTIAL |
| **API source** | `GET /v1/cost?range=30d` — current costs. `GET /v1/cost/timeseries?range=30d` — cost trend. No "before HiveBoard" baseline stored |
| **Processing** | Client-side: user would need to input a "baseline" cost (before optimization). Dashboard computes delta |
| **Scope** | Global |
| **UI/UX** | **ROI Calculator Card** — User inputs "baseline cost per task" (before observability). Dashboard shows current cost per task. Delta = savings. Projected annual savings. Example: "Before: $0.04/task → After: $0.008/task = 80% reduction. Monthly savings: $960 across 30,000 tasks." Also: a cost trend chart showing the decline over time since first data |
| **Gap** | **No historical baseline**. The system only has data from when it was deployed. The ROI story requires either: (1) user-input baseline (manual), or (2) comparing first-week data to current data (assumes first week = unoptimized). The second approach works if the user has enough history. A simple input field for "What was your cost/task before HiveBoard?" makes this work |

---

### Q36: "How many agents are in production?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — count of agents. Filter by `environment=production` if set. `GET /v1/metrics` → `summary.stuck` + agent count |
| **Processing** | None — simple count |
| **Scope** | Global |
| **UI/UX** | **Fleet Size Indicator** — "12 agents in production" with breakdown by type: "5 sales, 4 support, 3 ops". Small growth indicator if agents were recently added: "2 new this week" (compare agent.first_seen to 7d ago) |
| **Data flow** | `agents.length` → total count. Group by `agent_type` → type breakdown. `agents.filter(a => new Date(a.first_seen) > sevenDaysAgo).length` → "new this week" |

---

### Q37: "What's the overall health of the fleet?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | `GET /v1/agents` — all agent statuses, heartbeats. `GET /v1/metrics` — success rate, stuck count. `GET /v1/pipeline` — fleet queue depths and issues |
| **Processing** | Client-side: composite health score from multiple signals |
| **Scope** | Global |
| **UI/UX** | **Fleet Health Score** — Single large indicator: "Fleet Health: HEALTHY" (green) / "DEGRADED" (amber) / "CRITICAL" (red). Computed from: all heartbeats green ✓, stuck count = 0 ✓, success rate > 90% ✓, no high-severity unresolved issues ✓. Each factor shown as a checklist below the score. Any failing factor turns the score amber/red and explains why |
| **Data flow** | Composite: `allHeartbeatsGreen = agents.every(a => a.heartbeat_age_seconds < a.stuck_threshold_seconds)`, `noStuck = metricsData.summary.stuck === 0`, `highSuccessRate = metricsData.summary.success_rate > 90`, `noHighIssues = fleetPipeline.totals.active_issues === 0` → all true = HEALTHY, any false = DEGRADED, multiple false = CRITICAL |

---

### Q38: "Are we ready to scale?"

| Aspect | Detail |
|---|---|
| **Data available?** | YES |
| **API source** | All of the above: success rates, cost trends, queue depths, error rates |
| **Processing** | Client-side: composite "scale readiness" assessment from multiple signals |
| **Scope** | Global |
| **UI/UX** | **Scale Readiness Assessment** — Checklist of conditions: "High success rate (>95%): ✓ 97.2%", "Stable costs: ✓ flat 7d trend", "Manageable queues: ✓ avg depth 1.2", "Low error rate: ✓ 2.1%", "No stuck agents: ✓". Overall verdict: "Ready to scale" (green) or "Address these first" (amber) with specific blockers listed. This is a synthesized view — no new data, just a smart combination of existing metrics |
| **Data flow** | Combine: `metricsData.summary.success_rate > 95`, cost trend flat/declining (compare first/second half of timeseries), `fleetPipeline.totals.queue_depth / agents.length < 3`, error rate < 5%, stuck count = 0 → checklist rendering |

---

## Summary Matrix

| # | Question | Data? | Scope | API Source(s) | New Processing? |
|---|---|---|---|---|---|
| 1 | Agents running? | YES | Global | /v1/agents | None |
| 2 | Needs attention? | YES | Global | /v1/agents | Client: filter by status |
| 3 | Anything stuck? | YES | Global | /v1/agents, /v1/metrics | None |
| 4 | Work flowing? | YES | Global | /v1/metrics | None (existing mini-charts) |
| 5 | Happening now? | YES | Global | /v1/events, WebSocket | Client: last event age |
| 6 | Agent doing now? | YES | Agent | /v1/agents/{id} | None |
| 7 | Heartbeat healthy? | YES | Agent | /v1/agents/{id} | None |
| 8 | Pending work? | YES | Agent | /v1/agents/{id}/pipeline | None |
| 9 | Reported problems? | YES | Agent | /v1/agents/{id}/pipeline | None |
| 10 | Task steps? | YES | Task | /v1/tasks/{id}/timeline | None (existing) |
| 11 | Plan + failure? | YES | Task | /v1/tasks/{id}/timeline | None (existing) |
| 12 | Which tool failed? | YES | Task | /v1/tasks/{id}/timeline | None |
| 13 | LLM called? | YES | Task | /v1/tasks/{id}/timeline | None (existing) |
| 14 | Step durations? | YES | Task | /v1/tasks/{id}/timeline | None (existing) |
| 15 | Escalated? | YES | Task | /v1/tasks/{id}/timeline | None |
| 16 | Share investigation? | PARTIAL | Task | URL construction | **Need**: read-only key endpoint |
| 17 | Agent costs? | YES | Global | /v1/cost | None |
| 18 | Expensive models? | YES | Global | /v1/llm-calls | **Client**: group by name+model |
| 19 | Cost spike? | YES | Global | /v1/cost/timeseries | **Client**: spike detection |
| 20 | Prompt bloat? | YES | Global | /v1/llm-calls | **Client**: ratio analysis |
| 21 | Agents diff costs? | YES | Global | /v1/cost + /v1/metrics | **Client**: cost-per-task join |
| 22 | Silent drops? | YES | Agent | /v1/pipeline | **Client**: age vs avg comparison |
| 23 | Idle + queue? | YES | Global | /v1/agents | Client: contradiction check |
| 24 | Creds failing? | YES | Agent | /v1/agents/{id}/pipeline | Client: pattern match |
| 25 | Heartbeat drift? | PARTIAL | Agent | /v1/events (heartbeat) | **Client**: payload diff |
| 26 | Approvals backup? | YES | Global | /v1/events | **Client**: match req/recv pairs |
| 27 | Action fails? | YES | Global | /v1/events | **Client**: group + rate calc |
| 28 | Recurring issues? | YES | Agent | /v1/pipeline | None |
| 29 | Success improving? | YES | Global | /v1/metrics | Client: trend calc |
| 30 | Faster or slower? | YES | Global | /v1/metrics | Client: trend calc |
| 31 | Worst agent? | YES | Global | /v1/metrics (group_by) | Client: failure rate sort |
| 32 | Better after deploy? | PARTIAL | Global | /v1/metrics | **Need**: deploy markers |
| 33 | Total cost? | YES | Global | /v1/cost | None |
| 34 | Cost/task trend? | YES | Global | /v1/cost/timeseries + /v1/metrics | **Client**: timeseries join |
| 35 | Prove ROI? | PARTIAL | Global | /v1/cost | **Need**: user-input baseline |
| 36 | How many agents? | YES | Global | /v1/agents | None |
| 37 | Fleet health? | YES | Global | /v1/agents + /v1/metrics + /v1/pipeline | **Client**: composite score |
| 38 | Ready to scale? | YES | Global | All metrics | **Client**: composite assessment |

### Verdict

- **33 of 38** questions: Fully answerable with existing data
- **3 of 38** partially answerable (Q16 share links, Q25 heartbeat drift, Q32 deploy tracking, Q35 ROI baseline) — minor gaps
- **0 of 38** impossible — every question has at least partial data support

### Recommended New Backend Endpoints (Nice-to-Have)

These would improve performance and reduce client-side processing:

1. **`GET /v1/cost/by-purpose`** — LLM calls grouped by name (call purpose) with model distribution and avg cost (for Q18)
2. **`GET /v1/metrics/actions`** — Action success/failure rates per action name (for Q27)
3. **`POST /v1/keys/read-only`** — Generate shareable read-only token (for Q16)
4. **`POST /v1/events` with event_type=deploy** — Deploy marker events (for Q32)

### Architecture: New JS File

Given that `hiveboard.js` is already 2,026 lines, the Insights tab should be a separate file:

```
src/static/js/
  hiveboard.js        (2,026 lines — existing dashboard)
  insights.js         (NEW — Insights tab logic)
  api.js              (NEW — shared API client, extracted from hiveboard.js)
```

The `insights.js` file would:
- Import shared API client from `api.js`
- Have its own state management (selected time range, selected agent for drill-down, expanded sections)
- Fetch data on tab activation (not on page load — lazy)
- Use the same CSS variable system for consistent theming
- Render into a `#insights-container` div that's hidden/shown on tab switch

The `api.js` extraction would pull out ~150 lines from `hiveboard.js`:
- `CONFIG` object
- `apiFetch()` helper
- `fetchAgents()`, `fetchTasks()`, `fetchMetrics()`, `fetchCost()`, etc.
- Shared formatters: `fmtDuration()`, `fmtCost()`, `timeAgo()`

This keeps both files focused and testable independently.

# HiveBoard UI/UX Success Criteria

**What HiveBoard needs to be called "successful", "dead easy to understand", "extremely useful", and "that's all I need to develop and monitor my agents."**

---

## The Four Labels and What They Demand

### 1. "Dead Easy to Understand"

**Today:** The 3-panel layout (Hive | Timeline | Stream) is solid. A developer gets it in seconds. But once you go deeper, the conceptual model blurs.

**What's missing:**

- **A clear navigation hierarchy.** Right now it's: click agent -> click task -> see timeline. But there's no breadcrumb trail, no sense of "where am I." Add a persistent breadcrumb: `All Agents > lead-qualifier > task-lead-42 > score_lead`. Clickable at every level.
- **Action nesting is invisible.** The backend tracks `parent_action_id` chains (the whole point of `track_context()`), but the timeline renders events as a flat horizontal flow. You should see the **tree**: task -> action -> sub-action -> LLM call. An expandable outline or indented tree view would make nested `track_context()` blocks actually *visible*.
- **Payload details are buried.** You have to click a timeline node to see anything. The most important fields (model name, token count, error message) should be visible *on* the node itself, not behind a click.

### 2. "Extremely Useful"

**Today:** It answers "Is my agent running?" and "How much did it cost?" -- that's table stakes.

**What it doesn't answer (but should):**

- **"Why did it fail?"** -- The backend has error chains and exception details, but the UI shows failures as red dots. You need an **error inspector**: click a failed action, see the exception type, message, the full action tree that led to it, and the retry history. This is the #1 thing a developer needs when debugging agents.
- **"When should I worry?"** -- Alert rules exist in the API (`/v1/alerts/rules`) but there's **zero UI** for them. No alert creation, no notification inbox, no triggered alert history. This is the biggest backend-to-frontend gap. A simple alert inbox ("Agent X stuck for 5m", "Error rate > 20% in last hour") would transform the dashboard from passive monitoring to active observability.
- **"What changed?"** -- No cost timeseries, no trend lines, no week-over-week comparison. The Cost Explorer shows a snapshot. Developers need to see: "My costs doubled yesterday -- why?" Add a cost-over-time chart and per-agent/per-model trend lines. The API already supports time-range queries.
- **"What's queued up?"** -- Queue/scheduled/TODO data is buried behind double-click -> Pipeline tab. For an agentic framework, the work queue is critical. Surface queue depth and oldest item age directly on the agent card or in a dedicated "Pipeline" view.

### 3. "That's All I Need to Develop and Monitor My Agents"

This is the hardest label. It means HiveBoard replaces ad-hoc logging, manual cost spreadsheets, and Slack-based "is the agent stuck?" checks. Two big gaps:

**Development-time features:**

- **Task search and filtering.** Right now you can only browse tasks by clicking an agent. You need: search by task ID, filter by status/type/date range, sort by duration or cost. When debugging, developers know *which* task failed -- they need to jump straight to it.
- **LLM call inspector.** The Cost Explorer shows aggregate tables. Developers need to drill into individual LLM calls: which model, how many tokens, what was the cost, and ideally a preview of the prompt/response. This is how you debug prompt engineering issues.
- **Project-level views.** The backend supports projects, but the UI ignores them. Agents working on the same project (e.g., "sales-pipeline") should be groupable. Add a project switcher parallel to the environment selector.

**Monitoring-time features:**

- **Alert management UI.** Create rules ("alert me if agent X is stuck > 5min"), view alert history, snooze/acknowledge. Without this, developers still need external monitoring.
- **Approval queue.** Agents emit `approval_requested` events but there's no way to approve/reject from the dashboard. For human-in-the-loop workflows, this is essential -- otherwise developers still need a separate tool.
- **Export/share.** No data export, no report generation, only a permalink for timeline. Add CSV export for cost data and a "share this view" link for debugging sessions.

### 4. "Successful"

Success = developers **keep it open all day** as their primary window into agent behavior. That requires:

- **Information density without clutter.** The current layout wastes space in the center panel when no task is selected. Show a summary dashboard there: top errors, busiest agents, cost trend, recent failures. Make the "empty state" the most useful state.
- **Keyboard shortcuts.** `j/k` to navigate tasks, `Enter` to select, `Esc` to go back, `/` to search. Power users will thank you.
- **Configurable time ranges.** Hard-coded 1h rolling windows are limiting. Add a time picker: last 15m, 1h, 6h, 24h, 7d, custom range.
- **Sound/desktop notifications** for critical alerts (stuck agent, error spike). Optional, but it's what makes a monitoring tool "the one you trust."

---

## Current State Assessment

### What a Developer Sees Today

**The Hive (Left Panel, 280px):** Agent cards with status badges, heartbeat age, queue depth, issue count, current task, and activity sparklines. Priority-sorted (stuck -> error -> waiting -> processing -> idle). Attention badge pulses for stuck/error agents.

**Summary Bar:** Total agents, processing, waiting approval, stuck, errors, success rate (1h), avg duration, cost (1h). All clickable to filter.

**Metrics Row:** Four mini-charts (16 data points each): throughput, success rate, errors, LLM cost/task.

**Mission Control (Center Panel):** Plan bar showing step progress, horizontal timeline canvas with color-coded nodes (system=gray, action=blue, LLM=purple, retry=orange, failure=red, approval=green, escalation=orange). Clickable nodes with pinned detail panel. Tasks table below.

**Agent Detail View:** Opens on double-click. Two tabs: Tasks (table) and Pipeline (issues, queue, TODOs, scheduled items).

**Cost Explorer:** Replaces center panel. Cost ribbon (total, calls, tokens in/out, avg cost). Tables for cost-by-model and cost-by-agent with relative bars.

**Activity Stream (Right Panel, 320px):** Real-time event feed with filter chips (all, task, action, error, llm, pipeline, human). Color-coded, reverse chronological.

### Backend Capabilities NOT Surfaced in UI

| Category | Available in API | Shown in UI |
|----------|-----------------|-------------|
| Action nesting tree | Yes (`parent_action_id` chains) | No (flat timeline only) |
| Error chains | Yes (exception details + retry history) | No (red dots only) |
| Alert rules | Yes (CRUD + history endpoints) | No UI at all |
| Cost timeseries | Yes (time-range queries) | No (snapshot only) |
| Project-level views | Yes (project endpoints) | No UI at all |
| LLM call details | Yes (`/v1/llm-calls`) | Aggregate tables only |
| Agent metadata | Yes (framework, runtime, version) | Not shown in detail view |
| Approval workflows | Yes (events emitted) | No management UI |
| Full event payloads | Yes (nested payload.data) | Summary only |
| Pagination | Yes (cursor-based) | First page only |

**The backend is approximately 70% richer than what the UI surfaces.**

---

## Priority Ranking

| Priority | Feature | Why |
|---|---|---|
| **P0** | Action tree / nesting visualization | This is the entire value prop of `track_context()` -- make it visible |
| **P0** | Error inspector (exception details + action chain) | #1 developer need when debugging |
| **P1** | Alert inbox + basic rule management | Transforms passive dashboard -> active monitoring |
| **P1** | Cost timeseries chart | "What changed?" is unanswerable without trends |
| **P1** | Task search and filtering | Basic developer workflow for debugging |
| **P2** | LLM call drill-down | Prompt engineering debugging |
| **P2** | Project-level views | Multi-agent orchestration visibility |
| **P2** | Approval queue UI | Human-in-the-loop completeness |
| **P3** | Time range picker | Power user quality-of-life |
| **P3** | Keyboard navigation | "Keep it open all day" enabler |
| **P3** | Export/reporting | Enterprise readiness |

---

## The Bottom Line

HiveBoard's foundation is excellent -- the 3-panel layout, real-time WebSocket streaming, and visual hierarchy are all well done. But today it's a "happy path" monitor: it shows what's *happening* but not *why things fail* or *when you should worry*.

The gap between "nice dashboard" and "that's all I need" is filled by: **error analysis, alerting, search, and making the action nesting tree (the thing `track_context()` creates) actually visible.**

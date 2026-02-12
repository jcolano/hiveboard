# HiveBoard — User Manual Part 2: Dashboard Guide

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *Every screen, every element, every question the dashboard answers.*

---

## Table of Contents

1. [Dashboard Overview](#1-dashboard-overview)
2. [The Top Bar](#2-the-top-bar)
3. [The Hive — Agent Fleet Panel](#3-the-hive--agent-fleet-panel)
4. [Mission Control — The Center Panel](#4-mission-control--the-center-panel)
5. [The Activity Stream — Live Event Feed](#5-the-activity-stream--live-event-feed)
6. [Agent Detail View](#6-agent-detail-view)
7. [Cost Explorer](#7-cost-explorer)
8. [Filtering and Navigation](#8-filtering-and-navigation)
9. [Reading the Dashboard by Instrumentation Layer](#9-reading-the-dashboard-by-instrumentation-layer)
10. [Common Scenarios and What to Look For](#10-common-scenarios-and-what-to-look-for)
11. [Color Reference](#11-color-reference)
12. [Glossary](#12-glossary)

---

## 1. Dashboard Overview

HiveBoard is a single-screen dashboard divided into three columns:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            TOP BAR                                       │
│  Logo  │ Mission Control │ Cost Explorer │         Connected │ production │
├─────────┬──────────────────────────────────────────┬─────────────────────┤
│         │                                          │                     │
│   THE   │          MISSION CONTROL                 │     ACTIVITY        │
│   HIVE  │       (or Cost Explorer                  │     STREAM          │
│         │        or Agent Detail)                   │                     │
│  Agent  │                                          │  Real-time          │
│  cards  │  Stats ribbon                            │  event feed         │
│  with   │  Mini-charts                             │  with filters       │
│  status │  Timeline                                │                     │
│  and    │  Task table                              │                     │
│  health │                                          │                     │
│         │                                          │                     │
├─────────┴──────────────────────────────────────────┴─────────────────────┤
```

**Left column (280px):** The Hive — your agent fleet at a glance.
**Center column (flexible):** Mission Control, Cost Explorer, or Agent Detail — depending on what you're viewing.
**Right column (320px):** Activity Stream — live event feed.

The dashboard is real-time. It connects to the HiveBoard server via WebSocket and updates automatically as events arrive. You never need to refresh.

---

## 2. The Top Bar

The top bar is always visible and provides global controls.

### 2.1 Elements (left to right)

| Element | What it is | What it does |
|---------|-----------|--------------|
| **HiveBoard logo** | Product identity | — |
| **Workspace badge** | Your workspace/tenant name | Identifies which account you're viewing |
| **Mission Control tab** | View switcher | Shows the fleet overview with stats, timeline, and task table |
| **Cost Explorer tab** | View switcher | Shows LLM cost breakdown by model and agent |
| **Connected indicator** | WebSocket status | Green pulsing dot = live connection. If this goes away, you're not receiving real-time updates |
| **Environment selector** | Filter dropdown | Switches between `production`, `staging`, etc. Only shows agents and events for the selected environment |

### 2.2 Connection status

The **Connected** indicator tells you if the dashboard has a live WebSocket connection to the server.

| Status | Meaning | Action |
|--------|---------|--------|
| 🟢 **Connected** (pulsing) | Dashboard is receiving events in real time | None needed |
| No indicator / disconnected | WebSocket dropped | Check if the HiveBoard server is running. The dashboard will automatically reconnect |

### 2.3 Environment selector

The environment dropdown filters the entire dashboard. When set to `production`, you only see agents and events from agents initialized with `environment="production"`. This maps to the `environment` parameter in `hiveloop.init()`.

Use this to separate production monitoring from staging/development.

---

## 3. The Hive — Agent Fleet Panel

The left sidebar shows every registered agent as a card. This is your fleet-at-a-glance view — the first thing to check when you open the dashboard.

### 3.1 The Hive header

| Element | What it shows |
|---------|---------------|
| **"THE HIVE"** | Panel title |
| **Agent count** | Total number of agents matching current filters (e.g. "2 agents") |
| **Attention badge** (red, pulsing) | Appears when agents need attention — stuck, error, or waiting for approval. The number indicates how many agents need attention. If you see this, something needs action |

### 3.2 Agent cards

Each agent is displayed as a card with these elements:

```
┌─────────────────────────────────┐
│ main                      IDLE  │  ← agent name + status badge
│ general  ● 24s ago              │  ← type label + heartbeat indicator
│ ↳ task_lead-4821                │  ← current task (if processing)
│ ▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪              │  ← heartbeat sparkline
│ Q:3  ● 1 issue                  │  ← pipeline enrichment (if present)
└─────────────────────────────────┘
```

#### Agent name
The `agent_id` passed to `hb.agent()`. This is your primary identifier. Use human-readable names like `"lead-qualifier"` or `"support-triage"` instead of auto-generated IDs.

#### Status badge
The current state of the agent, derived from its events and heartbeat:

| Badge | Color | Meaning |
|-------|-------|---------|
| **IDLE** | Gray | Agent is alive (heartbeat active) but not currently working on a task |
| **PROCESSING** | Blue | Agent is actively executing a task |
| **WAITING** | Amber/Yellow | Agent has requested human approval and is blocked |
| **ERROR** | Red | Agent's most recent task failed |
| **STUCK** | Red, blinking | No heartbeat received within the stuck threshold. The agent may have crashed, hung, or lost connectivity |

**STUCK is the most critical status.** If you see a blinking red STUCK badge, the agent has stopped communicating. Investigate immediately — the agent process may have crashed, the network may be down, or the agent may be in an infinite loop.

#### Type label
The `type` parameter from `hb.agent()` (e.g. `"general"`, `"sales"`, `"support"`). Helps you visually categorize agents at a glance.

#### Heartbeat indicator
Shows the agent's health signal:

| Dot color | Text | Meaning |
|-----------|------|---------|
| 🟢 Green | "24s ago" | Heartbeat received recently — agent is healthy |
| 🟡 Yellow | "2m ago" | Heartbeat is slightly stale — not critical yet, but watch it |
| 🔴 Red | "8m ago" | Heartbeat is overdue — agent is likely stuck or crashed |

The timestamp shows how long ago the last heartbeat was received. Under normal operation with `heartbeat_interval=30`, you should see numbers under 60 seconds.

#### Current task
If the agent is currently processing a task, you'll see `↳ task_id` below the metadata. Click the task ID to jump to its timeline in Mission Control.

#### Heartbeat sparkline
The row of small bars at the bottom of the card shows heartbeat activity over time. A steady, even pattern means the agent is running stably. Gaps indicate periods where the agent was offline or heartbeats were missed.

#### Pipeline enrichment (Layer 2+)
When you instrument with `queue_provider`, `report_issue`, etc., the card shows additional badges:

| Badge | Meaning |
|-------|---------|
| **Q:3** (blue) | Agent has 3 items in its work queue |
| **Q:8** (amber) | Queue depth is high (>5 items) — agent may be falling behind |
| **● 1 issue** (red) | Agent has self-reported an active issue |
| **↳ Processing lead #4801** | Current processing summary from heartbeat payload |

### 3.3 Card interactions

| Action | What happens |
|--------|-------------|
| **Click a card** | Filters the entire dashboard to that agent. Stats, timeline, tasks, and activity stream all scope to the selected agent. A yellow highlight appears on the card and a filter bar appears at the top |
| **Click the selected card again** | Clears the filter (deselects) |
| **Double-click a card** | Opens the Agent Detail view (expanded view with Tasks and Pipeline tabs) |

### 3.4 Card sorting

Cards are sorted by **attention priority**:
1. Stuck agents (first — most critical)
2. Error agents
3. Waiting for approval
4. Processing
5. Idle (last — everything is fine)

Within each status group, agents are sorted by most recent activity. This means the agents that need your attention most are always at the top.

---

## 4. Mission Control — The Center Panel

Mission Control is the default center view. It has four sections stacked vertically: the Stats Ribbon, the Mini-Charts, the Timeline, and the Task Table.

### 4.1 Stats Ribbon

A row of summary statistics across the top of Mission Control:

| Stat | What it shows | What to watch for |
|------|---------------|-------------------|
| **Total Agents** | Number of registered agents | Sudden drops mean agents crashed or deregistered |
| **Processing** (blue) | Agents currently working on tasks | Click to filter the dashboard to only processing agents |
| **Waiting** (amber) | Agents blocked on human approval | If this is high, your approval workflow may be a bottleneck |
| **Stuck** (red) | Agents that stopped heartbeating | **Any number > 0 needs immediate investigation** |
| **Errors** (red) | Agents whose last task failed | Click to filter to agents in error state |
| **Success Rate (1h)** (green) | Percentage of tasks completed successfully in the last hour | Drop below your baseline = something changed |
| **Avg Duration** | Average task processing time in the last hour | Sudden increase = agents are slowing down (LLM latency? API issues?) |
| **Cost (1h)** (purple) | Total LLM cost in the last hour | Spikes indicate runaway agents or unexpected model usage |

**The Processing, Waiting, Stuck, and Errors stats are clickable** — they act as quick filters. Click "Stuck" and the dashboard filters to only show stuck agents, their events, and their tasks.

### 4.2 Mini-Charts

Below the Stats Ribbon, four small bar charts show trends over the last hour:

| Chart | Label | What it tells you |
|-------|-------|-------------------|
| **Throughput (1h)** | Tasks completed per time bucket | Shows throughput patterns — is work flowing steadily or in bursts? |
| **Success Rate** | Green bars = success ratio per time bucket | Dropping bars = increasing failure rate |
| **Errors** | Red bars = error count per time bucket | Spikes indicate incident onset |
| **LLM Cost/Task** | Purple bars = average cost per task per time bucket | Rising cost means agents are using more tokens or more expensive models |

These charts give you a quick visual feel for whether things are normal or trending in the wrong direction, without needing to dig into data.

### 4.3 Timeline

The Timeline is the most detailed view on the dashboard. It shows the step-by-step story of a selected task — what happened, in what order, how long each step took, and what went wrong if something failed.

#### When it's empty
If you see "No timeline data", either:
- No task is selected (click a task in the Task Table below)
- You're on Layer 0 only (no `agent.task()` instrumentation yet)

#### Timeline header
Shows the currently selected task's metadata:

| Element | What it shows |
|---------|---------------|
| **Task ID** | The `task_id` from `agent.task()` |
| **Agent name** (clickable) | Which agent ran this task. Click to filter to that agent |
| **Permalink button** | Copies a shareable link to this specific task timeline |

#### Plan progress bar
If the task has a plan (`task.plan()`), a progress bar appears above the timeline showing plan steps and their status:

| Step color | Status |
|------------|--------|
| Green | Completed |
| Blue (pulsing) | In progress |
| Gray | Not started |
| Red | Failed |

Hover over a step to see its description.

#### Timeline nodes
The timeline is displayed as a horizontal track of connected nodes. Each node represents an event:

```
[action_started] ──── [llm_call] ──── [action_completed] ──── [escalated]
   score_lead          claude-sonnet      score_lead            Low score
     0.0s                1.2s               1.8s                 1.8s
```

**Node shapes and colors:**

| Node type | Visual | Color |
|-----------|--------|-------|
| Action (started/completed) | Circle outline | Blue |
| Action (failed) | Filled circle | Red |
| LLM call | Circle with model badge above | Purple |
| Escalation | Filled circle | Amber |
| Approval requested | Circle outline | Amber |
| Approval received | Filled circle | Green or Red (based on decision) |
| Custom event | Circle outline | Gray |

**Connectors** between nodes show duration — the time elapsed between events. Long connectors with high durations indicate slow steps.

**Branching:** When a task has retries or nested actions, the timeline branches vertically. Child actions appear below their parent with smaller nodes.

#### Clicking a timeline node
Click any node to **pin its detail panel** below the timeline. The detail panel shows:
- Event type and name
- All payload fields (key-value pairs)
- Duration
- Tags
- For LLM calls: model, token counts, cost, prompt/response previews

Click "✕ Close" to dismiss the detail panel.

### 4.4 Task Table

Below the timeline, a sortable table lists all tasks:

| Column | What it shows |
|--------|---------------|
| **Task ID** | Unique task identifier (clickable — selects the task for timeline view) |
| **Agent** | Which agent ran the task (clickable — filters to that agent) |
| **Type** | Task type classification |
| **Status** | Current status with color dot: completed (green), failed (red), processing (blue), waiting (amber) |
| **Duration** | How long the task took (or has been running) |
| **LLM** | Number of LLM calls made (shown as "◆ 3") |
| **Cost** | Total LLM cost for this task |
| **Time** | When the task started |

**Interaction:** Click any row to select that task. Its timeline loads above. The selected row is highlighted.

**Empty state:** If you see "No tasks", either no tasks have been created (Layer 0 only) or the current filter excludes all tasks.

---

## 5. The Activity Stream — Live Event Feed

The right sidebar shows events as they happen, in real time. Every event emitted by any agent appears here immediately.

### 5.1 Stream header

| Element | What it shows |
|---------|---------------|
| **"ACTIVITY"** | Panel title |
| **● LIVE** (green dot) | Indicates real-time streaming is active |
| **Event count** | Total events matching current filters |

### 5.2 Filter chips

A row of filter chips lets you narrow the event feed by type:

| Filter | What events it shows |
|--------|---------------------|
| **all** | Everything |
| **task** | `task_started`, `task_completed`, `task_failed` |
| **action** | `action_started`, `action_completed`, `action_failed` |
| **error** | `task_failed`, `action_failed`, and any error-type events |
| **llm** | LLM call events (`custom` with `kind: "llm_call"`) |
| **pipeline** | Queue snapshots, TODOs, scheduled, issues |
| **human** | Escalations, approval requests, approval received |

Click a chip to filter. Click the active chip again to return to "all".

### 5.3 Event entries

Each event in the stream shows:

```
● agent_registered                    1m ago
  main
  Agent main registered
```

| Element | Description |
|---------|-------------|
| **Color dot** | Matches the event type color (green for success, red for error, purple for LLM, etc.) |
| **Event type** (or kind) | The event type or payload kind. Clickable to filter by that type |
| **Timestamp** | Relative time ("1m ago", "just now") |
| **Agent name** (clickable) | Click to filter dashboard to that agent |
| **Task ID** (clickable, if present) | Click to load that task's timeline |
| **Summary** | Human-readable description of the event |

### 5.4 Event type icons

Certain event types have icons for quick visual scanning:

| Icon | Event type |
|------|-----------|
| 🤖 | Agent registered |
| ❤️ | Heartbeat |
| ▶ | Task started |
| ✓ | Task completed |
| ✕ | Task failed |
| ◆ | LLM call |
| ⚡ | Action started/completed |
| ⬆ | Escalation |
| 🔒 | Approval requested |
| 🔓 | Approval received |
| 🔄 | Retry |
| ⚠ | Issue |

### 5.5 Using the Activity Stream effectively

The Activity Stream is your **live monitoring feed**. Here's how to use it:

- **Normal monitoring:** Leave on "all" and glance periodically. The scrolling feed gives you a heartbeat of system activity.
- **Debugging a failure:** Filter to "error" to see only failures. Click the agent name to see which agent failed, then click the task ID to see the full timeline.
- **Tracking costs:** Filter to "llm" to see every LLM call as it happens, with model and cost.
- **Human-in-the-loop monitoring:** Filter to "human" to see approvals and escalations. If approvals are piling up, your review queue needs attention.
- **Agent investigation:** Click an agent name in any event to filter the entire dashboard to that agent. The stream, stats, and timeline all scope to your selection.

---

## 6. Agent Detail View

The Agent Detail view gives a deeper look at a single agent. Access it by double-clicking an agent card in the Hive, or by clicking an agent name in a clickable context.

### 6.1 Agent Detail header

Shows the agent's name and current status badge. Click "✕ Close Detail" to return to Mission Control.

### 6.2 Tabs

| Tab | What it shows |
|-----|---------------|
| **Tasks** | All tasks for this agent — same columns as the Task Table in Mission Control but scoped to one agent |
| **Pipeline** | Work pipeline data: active issues, queue, TODOs, and scheduled work |

### 6.3 Pipeline tab

The Pipeline tab surfaces operational context about the agent beyond individual tasks. Each section only appears if there's data:

#### Active Issues
Self-reported issues from `agent.report_issue()`:

| Column | What it shows |
|--------|---------------|
| Issue | Summary text |
| Severity | `low`, `medium`, `high`, or `critical` — color-coded |
| Category | Issue classification (e.g. "connectivity", "timeout", "data_quality") |
| Occurrences | How many times this issue has been reported (deduplicated by `issue_id`) |

#### Queue
Work queue state from `queue_provider`:

| Column | What it shows |
|--------|---------------|
| ID | Queue item identifier |
| Priority | `low`, `normal`, `high`, `urgent` — color-coded |
| Source | Where this work item came from |
| Summary | What the item is about |
| Age | How long the item has been in the queue |

An empty queue shows "Queue is empty — agent is caught up."

#### Active TODOs
Agent-managed work items from `agent.todo()`:

| Column | What it shows |
|--------|---------------|
| TODO | Summary text |
| Priority | Priority level — color-coded |
| Source | What created this TODO (e.g. "agent_decision", "retry_failure") |

#### Scheduled
Recurring tasks from `agent.scheduled()`:

| Column | What it shows |
|--------|---------------|
| Name | Scheduled task name |
| Next Run | When it runs next |
| Interval | How often (e.g. "1h", "daily") |
| Status | Last execution status |

---

## 7. Cost Explorer

The Cost Explorer is a dedicated view for LLM cost analysis. Switch to it by clicking "Cost Explorer" in the top bar. It only shows data when you've instrumented with `task.llm_call()` or `agent.llm_call()` (Layer 2).

### 7.1 Cost Ribbon

Summary statistics across the top:

| Stat | What it shows |
|------|---------------|
| **Total Cost** | Total LLM spending in the selected time period |
| **LLM Calls** | Total number of LLM calls made |
| **Tokens In** | Total input tokens consumed |
| **Tokens Out** | Total output tokens generated |
| **Avg Cost/Call** | Average cost per LLM call |

### 7.2 Cost by Model

A table breaking down spending by LLM model:

| Column | What it shows |
|--------|---------------|
| Model | Model identifier (e.g. `claude-sonnet-4-20250514`, `gpt-4o-mini`) |
| Calls | Number of calls to this model |
| Tokens In | Total input tokens for this model |
| Tokens Out | Total output tokens for this model |
| Cost | Total cost for this model |
| Bar | Visual cost comparison — longest bar = most expensive model |

**What to look for:**
- Is an expensive model being used where a cheaper one would work? (e.g. using `claude-opus` for simple classification when `claude-haiku` would suffice)
- Is one model dominating cost? That's your optimization target

### 7.3 Cost by Agent

A table breaking down spending by agent:

| Column | What it shows |
|--------|---------------|
| Agent | Agent name (clickable — opens Agent Detail) |
| Calls | Number of LLM calls this agent made |
| Tokens In | Total input tokens for this agent |
| Tokens Out | Total output tokens for this agent |
| Cost | Total cost for this agent |
| Bar | Visual cost comparison — longest bar = most expensive agent |

**What to look for:**
- Which agent is the biggest spender? Is that expected?
- Are costs distributed evenly or is one agent an outlier?
- Sorted by cost descending — most expensive agent is always at the top

### 7.4 Using Cost Explorer for optimization

The Cost Explorer is designed to answer these questions:

| Question | Where to look |
|----------|--------------|
| How much am I spending on LLM calls? | Cost Ribbon → Total Cost |
| Which model costs the most? | Cost by Model table → sorted by cost |
| Which agent costs the most? | Cost by Agent table → sorted by cost |
| Am I using expensive models unnecessarily? | Compare model table — if a costly model has many calls for simple tasks, consider switching to a cheaper model |
| Has cost increased recently? | Compare current total to previous periods. The LLM Cost/Task mini-chart in Mission Control shows the trend |
| What's my average cost per call? | Cost Ribbon → Avg Cost/Call. Compare across models to find efficiency gains |

---

## 8. Filtering and Navigation

HiveBoard uses a unified filtering model — when you apply a filter, it affects all three columns simultaneously.

### 8.1 Filter bar

When a filter is active, a yellow-highlighted bar appears between the top bar and the main content:

```
⬡ Filtering: agent = main                              ✕ Clear
```

This tells you the dashboard is scoped. Click "✕ Clear" to remove the filter and see all data.

### 8.2 Ways to filter

| Action | What it filters to |
|--------|-------------------|
| Click an agent card in the Hive | That agent's data only |
| Click an agent name in the Activity Stream | That agent's data only |
| Click an agent name in the Task Table | That agent's data only |
| Click a status stat (Processing, Waiting, Stuck, Errors) | All agents with that status |
| Click a task ID in the Activity Stream | Loads that task's timeline |
| Click a task row in the Task Table | Loads that task's timeline |
| Select an environment in the dropdown | All agents in that environment |

### 8.3 Navigation flow

A typical investigation flow:

1. **Notice** — Attention badge pulses red on The Hive, or Stuck/Error count > 0 in Stats Ribbon
2. **Filter** — Click the "Stuck" stat or the stuck agent's card
3. **Identify** — See which agent is stuck and when its last heartbeat was
4. **Investigate** — Click the agent's most recent task in the Task Table
5. **Diagnose** — Read the Timeline to see what the agent was doing when it got stuck
6. **Detail** — Click a timeline node to see the full payload (LLM call content, error message, etc.)

### 8.4 Cross-referencing between panels

The three columns are designed to work together:

| See something in... | Click to... | Result |
|---------------------|------------|--------|
| Hive (agent card) | Click card | Stats + Timeline + Stream all filter to that agent |
| Activity Stream (event) | Click agent name | Same as above |
| Activity Stream (event) | Click task ID | Timeline loads that task |
| Task Table (row) | Click row | Timeline loads that task |
| Task Table (agent column) | Click agent name | Filters to that agent |
| Timeline (agent name in header) | Click agent name | Filters to that agent |

Everything is interconnected — you can always drill down from any element to get more detail.

---

## 9. Reading the Dashboard by Instrumentation Layer

What you see on the dashboard depends on how much you've instrumented with HiveLoop. Here's what each layer unlocks:

### 9.1 Layer 0 only (init + heartbeat)

**What you see:**
- Agent cards in the Hive with names, types, and status badges
- Heartbeat indicators (green/yellow/red dots with timestamps)
- Heartbeat sparklines showing activity over time
- Stats Ribbon: Total Agents, Stuck count
- Activity Stream: `agent_registered` events
- `queue_provider` data on agent cards (if provided)

**What's empty:**
- Mission Control: "No tasks", "No timeline data"
- Task Table: empty
- Cost Explorer: all zeros
- Most Activity Stream filters show nothing

**Questions you can answer:**
- Are my agents alive?
- How many agents are running?
- Is anything stuck?
- When did each agent come online?

### 9.2 Layer 1 (decorators + task context)

**What's now populated:**
- Task Table fills with tasks — status, duration, time
- Timeline shows task events and action nodes with timing
- Activity Stream shows task and action events
- Stats Ribbon: Processing, Waiting, Errors, Success Rate, Avg Duration
- Mini-Charts: Throughput, Success Rate, Errors
- Agent status correctly reflects Processing / Error / Waiting

**New questions you can answer:**
- What task is each agent working on right now?
- How long do tasks take?
- What's my success rate?
- When a task fails, which action was it on?
- What's my throughput over time?

### 9.3 Layer 2 (rich events)

**What's now populated:**
- Timeline enriched with LLM call nodes (purple), plan progress bars, escalation/approval nodes
- Cost Explorer: fully functional with model and agent breakdowns
- Stats Ribbon: Cost (1h) populated
- Mini-Charts: LLM Cost/Task populated
- Agent Detail → Pipeline tab shows issues, queue, TODOs, scheduled
- Agent cards show pipeline enrichment (queue badges, issue indicators)
- Activity Stream: full range of event types with icons

**New questions you can answer:**
- How much is each LLM call costing me?
- Which model is the most expensive?
- Which agent is the biggest spender?
- What was the agent's reasoning at each step?
- Where in the plan did it fail?
- How long has the work queue been growing?
- What issues has the agent self-reported?
- Is the human approval queue backed up?

---

## 10. Common Scenarios and What to Look For

### 10.1 "Is everything OK right now?"

**Where to look:** Stats Ribbon + The Hive

| Signal | Healthy | Unhealthy |
|--------|---------|-----------|
| Stuck count | 0 | Any number > 0 |
| Error count | 0 or low | Sudden increase |
| Attention badge | Not visible | Red pulsing badge on The Hive header |
| Heartbeat dots | All green | Yellow or red dots |
| Success Rate | At or above your baseline | Dropping |

### 10.2 "An agent is stuck — what happened?"

1. Look at the Hive — the stuck agent will have a blinking red **STUCK** badge and a red heartbeat dot
2. Note the heartbeat timestamp — "8m ago" tells you how long it's been unreachable
3. Click the agent card to filter to it
4. Check the Activity Stream — what was the last event before it went silent?
5. If there's a task in the Task Table, click it to see the Timeline — where in the task did it stop?
6. Check your agent's process (logs, systemd status, container health) — the dashboard tells you *when* and *where*, your infrastructure tells you *why*

### 10.3 "A task failed — why?"

1. Find the failed task in the Task Table (red status dot)
2. Click it to load the Timeline
3. Look for the red node — that's where the failure occurred
4. Click the red node to see the error detail (exception, error message, payload)
5. Look at the preceding nodes — what was the agent doing just before the failure? Was it an LLM call that returned something unexpected? A tool that timed out?

### 10.4 "Costs are spiking — where is the money going?"

1. Switch to Cost Explorer
2. Check Cost by Model — is an expensive model being overused?
3. Check Cost by Agent — is one agent responsible for the spike?
4. Click the expensive agent to see its tasks
5. In Mission Control, look at the LLM Cost/Task mini-chart — when did the spike start?
6. Cross-reference with the Activity Stream (filter to "llm") — are there more calls than expected, or are individual calls more expensive?

### 10.5 "My agent seems slow — where is the bottleneck?"

1. Check Avg Duration in the Stats Ribbon — is it higher than your baseline?
2. Click a slow task in the Task Table
3. In the Timeline, look at the connector durations between nodes — the longest connector is your bottleneck
4. Common culprits: LLM call latency (purple nodes), external API calls, or long wait times between actions

### 10.6 "I need to monitor approvals"

1. Check the Waiting count in the Stats Ribbon
2. Filter the Activity Stream to "human"
3. Agents waiting for approval show amber **WAITING** badges in the Hive
4. Each pending approval appears as an `approval_requested` event in the stream with details on what's being requested
5. When approvals are received, `approval_received` events appear with the decision

### 10.7 "I want to see what happened to a specific task"

1. If you have the task ID: look for it in the Task Table or use the Activity Stream
2. Click the task row in the Task Table
3. The Timeline loads with the full step-by-step narrative
4. Click any node for the detail view
5. Use the **Permalink** button to copy a shareable link to this specific task

---

## 11. Color Reference

Colors are used consistently throughout the dashboard to communicate status and type at a glance.

### 11.1 Status colors

| Color | Hex | Meaning | Where used |
|-------|-----|---------|------------|
| Gray | `#6b7280` | Idle / inactive / not started | Idle badges, pending plan steps, muted text |
| Blue | `#3b82f6` | Active / processing | Processing badges, action events, queue badges |
| Green | `#10b981` | Success / healthy | Completed badges, success rate, fresh heartbeats, connected indicator |
| Amber | `#f59e0b` | Warning / waiting | Waiting badges, escalations, stale heartbeats, approval events |
| Red | `#ef4444` | Error / failure | Error badges, failed events, issues |
| Deep red | `#dc2626` | Stuck (critical) | Stuck badges (blinking), dead heartbeats |
| Purple | `#8b5cf6` | LLM / cost | LLM call events, cost values, Cost Explorer |

### 11.2 Accent color

**Amber/Gold (`#f59e0b`)** is the HiveBoard accent color — used for the logo, selected states, active filters, clickable entity highlights, and the filter bar.

---

## 12. Glossary

| Term | Definition |
|------|-----------|
| **The Hive** | The left sidebar showing all agent cards — your fleet overview |
| **Mission Control** | The main center view with stats, timeline, and task table |
| **Cost Explorer** | The center view for LLM cost analysis by model and agent |
| **Agent Detail** | An expanded center view focused on one agent, with Tasks and Pipeline tabs |
| **Activity Stream** | The right sidebar showing events in real time |
| **Stats Ribbon** | The row of summary numbers at the top of Mission Control |
| **Timeline** | The horizontal visualization of events within a single task |
| **Plan Bar** | The step-by-step progress indicator above the Timeline (when a plan exists) |
| **Heartbeat** | An automatic periodic signal from an agent confirming it's alive |
| **Stuck** | An agent that has stopped sending heartbeats beyond its threshold |
| **Sparkline** | The small bar chart on each agent card showing heartbeat activity over time |
| **Pipeline** | An agent's operational context: queue, TODOs, issues, scheduled work |
| **Attention badge** | The red pulsing counter on The Hive header when agents need attention |
| **Filter bar** | The yellow bar that appears when the dashboard is filtered to a specific agent or status |
| **Permalink** | A shareable URL that links directly to a specific task timeline |
| **Pin** | Clicking a timeline node "pins" its detail panel open for inspection |
| **Environment** | An organizational scope (e.g. production, staging) set in `hiveloop.init()` |
| **Group** | An organizational label (e.g. team name, region) set in `hiveloop.init()` |
| **Enrichment** | Additional data on agent cards from `queue_provider`, `heartbeat_payload`, and `report_issue` |

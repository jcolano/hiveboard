# HiveBoard — Fleet Dashboard Guide

**Version:** 0.2.0
**Last updated:** 2026-02-15

> *Your fleet command center — everything happening across all agents, in real time.*

---

## Table of Contents

1. [Overview](#1-overview)
2. [Layout](#2-layout)
3. [The Agent Sidebar](#3-the-agent-sidebar)
4. [Mission Control](#4-mission-control)
5. [The Activity Stream](#5-the-activity-stream)
6. [Agent Detail View](#6-agent-detail-view)
7. [Cost Explorer](#7-cost-explorer)
8. [Fleet Pipeline](#8-fleet-pipeline)
9. [Filtering and Navigation](#9-filtering-and-navigation)
10. [Timeline Deep Dive](#10-timeline-deep-dive)
11. [Real-Time Behavior](#11-real-time-behavior)

---

## 1. Overview

The Fleet dashboard (`fleet.html`) is HiveBoard's operational command center. It gives you a live, three-column view of your entire agent fleet with real-time updates via WebSocket.

This is the page you keep open during operations. It answers the question: **"What is happening across my fleet right now, and does anything need my attention?"**

The Fleet page contains five internal views accessible via tabs in the top bar:

| Tab | What it shows | When to use |
|---|---|---|
| **Fleet** (default) | Mission Control — stats, timeline, task table | Day-to-day monitoring |
| **Costs** | LLM cost breakdown by model and agent | Budget tracking |
| **Pipeline** | Fleet-wide queue, issues, TODOs | Operational backlog review |
| **Agent Detail** | Deep dive into one agent (opens when you double-click a card) | Single-agent investigation |

The Activity Stream (right column) is always visible regardless of which center view is active.

---

## 2. Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│                            TOP BAR                                       │
│  Logo │ Workspace │ Analytics │ Agent View │ Insights │ Fleet │ Costs │  │
│                                             Pipeline  │ Connected │ Env  │
├─────────┬──────────────────────────────────────────┬─────────────────────┤
│ AGENTS  │                                          │ NARRATIVE           │
│ (280px) │          CENTER PANEL                    │ (320px)             │
│         │     (Mission Control / Costs /           │                     │
│         │      Pipeline / Agent Detail)            ├─────────────────────┤
│ Agent   │                                          │ ACTIVITY STREAM     │
│ cards   │  Stats Ribbon                            │                     │
│ sorted  │  Mini-Charts                             │ Filtered event      │
│ by      │  Timeline (tree or flat)                 │ feed with type      │
│ urgency │  Task Table                              │ chips               │
│         │                                          │                     │
└─────────┴──────────────────────────────────────────┴─────────────────────┘
```

---

## 3. The Agent Sidebar

The left sidebar shows every registered agent as a card. Agents sort by urgency: stuck first, then error, then waiting, then processing, then idle.

### 3.1 Sidebar Header

| Element | Description |
|---|---|
| **"Agents"** | Panel title |
| **Agent count** | Total agents matching current filters (e.g., "5 agents") |
| **Attention badge** | Red pulsing count when agents are stuck, erroring, or waiting for approval |

### 3.2 Agent Cards

Each card displays:

| Element | What it shows |
|---|---|
| **Agent name** | The `agent_id` from `hb.agent()` |
| **Status badge** | Current derived status with color coding |
| **Type label** | Agent type (e.g., "sales", "support") |
| **Heartbeat indicator** | Colored dot + time since last heartbeat |
| **Current task** | Clickable task ID if the agent is processing |
| **Sparkline** | 12-bar mini chart of recent activity volume |
| **Queue badge** | "Q:4" (blue) or amber if queue depth > 5 |
| **Issue badge** | Red "● 1 issue" if the agent has reported active issues |
| **Metadata tags** | Framework, version, runtime, SDK version (when available) |

### 3.3 Status Badges

| Badge | Color | Meaning |
|---|---|---|
| **IDLE** | Gray | Alive (heartbeat active) but not working on a task |
| **PROCESSING** | Blue | Actively executing a task |
| **WAITING** | Amber | Blocked — waiting for human approval |
| **ERROR** | Red | Most recent task failed |
| **STUCK** | Red, blinking | No heartbeat received within the stuck threshold — investigate immediately |

### 3.4 Heartbeat Indicator

| Dot | Text | Meaning |
|---|---|---|
| 🟢 Green | "24s ago" | Recent heartbeat — agent is healthy |
| 🟡 Yellow | "2m ago" | Slightly stale — not critical yet |
| 🔴 Red | "8m ago" | Overdue — agent likely stuck or crashed |

### 3.5 Interactions

- **Click** an agent card → filters the entire dashboard (stats, tasks, stream) to that agent
- **Double-click** → opens Agent Detail view in the center panel
- **Click a status stat** in the summary bar → filters the agent list by that status

---

## 4. Mission Control

The default center view. Shows fleet-wide health metrics, task timeline, and task table.

### 4.1 Stats Ribbon

A single row of fleet-wide vital signs across the top:

| Stat | What it shows | When to worry |
|---|---|---|
| **Total Agents** | Count of all registered agents | If it drops unexpectedly |
| **Processing** | Agents currently working (blue) | Normal operations |
| **Waiting** | Agents awaiting human approval (amber) | Stays high → approvals bottlenecked |
| **Stuck** | Agents that stopped heartbeating (red) | Any non-zero value → investigate |
| **Errors** | Agents whose last task failed (red) | Investigate immediately |
| **Success Rate (1h)** | % of tasks completed vs failed | Below 90% is concerning |
| **Avg Duration** | Mean task duration in last hour | Sudden increases mean slowdowns |
| **Cost (1h)** | Total LLM spend in last hour (purple) | Budget awareness |

Clicking a status stat (Processing, Waiting, Stuck, Errors) filters the sidebar to show only agents in that state.

### 4.2 Mini-Charts

Four mini bar charts below the stats ribbon showing hourly trends:

| Chart | What it shows |
|---|---|
| **Throughput (1h)** | Tasks completed per time bucket |
| **Success Rate** | % success per bucket (0-100 scale) |
| **Errors** | Error count per bucket |
| **LLM Cost/Task** | Average cost per task per bucket |

These are intentionally small — designed for spotting trends and spikes, not detailed analysis. For deeper exploration, use Analytics.

### 4.3 Timeline

The timeline visualizes the action-by-action execution of a selected task. It has two display modes, toggled via the Tree/Flat buttons:

**Tree View (default):** Renders the action tree hierarchically, showing nested parent-child action relationships. Each node shows:

- Action name with status icon (✓ success, ✗ failure, ⚡ running)
- Duration
- LLM call indicator (◆) with token and cost info when applicable
- Nested children indented beneath their parent
- Color coding: green for success, red for failure, purple for LLM calls

**Flat View:** A horizontal scrollable timeline of events in chronological order. Each node is a colored pill representing an event type.

Both views support clicking a node to open a detail panel below the timeline with the full payload data.

**Duration Breakdown:** When a task is selected, a visual bar shows how the total task duration is distributed across action categories (LLM calls, tool execution, other). Each segment is color-coded and shows percentage and absolute time.

### 4.4 Plan Bar

If the selected task has a plan (set via `task.plan()`), a plan progress bar appears above the timeline showing:

- Plan goal/label
- Step completion progress (e.g., "Step 3 of 5")
- Individual steps listed with completion status: ✓ completed, ► current, ○ pending

### 4.5 Task Table

Below the timeline, a sortable table of recent tasks:

| Column | What it shows |
|---|---|
| **Task ID** | Clickable identifier — loads the task's timeline |
| **Agent** | Which agent ran it — clickable to filter |
| **Type** | Task category (e.g., "lead_processing") |
| **Status** | Colored dot + label (completed/failed/processing) |
| **Duration** | How long the task took |
| **LLM** | Diamond icon + call count (e.g., "◆ 3") |
| **Cost** | Dollar amount |
| **Time** | Relative timestamp ("2m ago") |

Click a row to load that task's timeline. If an agent is selected in the sidebar, the table filters to that agent's tasks only.

---

## 5. The Activity Stream

The right column is split into two panels:

### 5.1 Narrative Panel (top ~40%)

A curated, human-readable log of important events. Shows high-level summaries like "agent-1 completed task_lead-4821 in 12.3s" rather than raw event data. Marked with a "Live" badge.

### 5.2 Raw Activity Stream (bottom ~60%)

A chronological feed of all events, newest at top, updating live via WebSocket.

**Each event shows:**

- Kind icon (◆ for LLM, ⚠ for issue, ⊞ for queue, ☐ for todo, ⏲ for scheduled)
- Summary text
- Agent ID
- Task ID (if applicable)
- Relative timestamp
- Detail tags: model name, token counts, cost, duration, severity (depending on event type)

**Filter Chips:**

| Filter | Shows |
|---|---|
| **all** | Everything (default) |
| **task** | Task lifecycle events (started, completed, failed) |
| **action** | Tool/action executions |
| **error** | Error-severity events only |
| **llm** | LLM calls only |
| **pipeline** | Queue, TODOs, issues, scheduled events |
| **human** | Approval requests and responses |

Clicking an agent name in a stream event filters the entire dashboard to that agent. Clicking a task ID loads that task's timeline.

---

## 6. Agent Detail View

Opens when you double-click an agent card in the sidebar (or click "open detail" from various places). Replaces the center panel with a deep dive into a single agent.

### 6.1 Header

Shows the agent name, status badge, and metadata tags (type, framework, version, runtime, group, SDK version). A "✕ Close Detail" button returns to Mission Control.

### 6.2 Tasks Tab

Same as the main task table, but filtered to this agent only. Shows the agent's task history with status, duration, cost, and LLM call counts.

### 6.3 Pipeline Tab

The agent's operational state, organized into four groups:

**Active Issues:** Each issue shows summary, severity badge (critical/high/medium/low), category, and occurrence count.

**Queue:** Item count, individual items with ID, priority badge (urgent/high/normal/low), source, and summary. When empty: "Queue is empty — agent is caught up."

**Active TODOs:** Each TODO shows summary, priority, and source.

**Scheduled:** Job name, next run time, interval, and last run status (success/failure).

---

## 7. Cost Explorer

Accessed via the "Costs" tab. Shows where LLM money is going.

### 7.1 Cost Ribbon

Four summary stats across the top:

| Stat | Description |
|---|---|
| **Total Cost** | Total LLM spend in the selected time range |
| **LLM Calls** | Total number of LLM API calls |
| **Tokens In** | Total input tokens (formatted as "125.3K") |
| **Tokens Out** | Total output tokens |

### 7.2 Cost Tables

Two breakdown tables:

**Cost by Model:** Groups spending by LLM model name. Each row shows model, total cost, call count, percentage of total. Clicking a model row opens a drilldown showing the individual LLM calls for that model.

**Cost by Agent:** Groups spending by agent. Each row shows agent ID, total cost, call count, percentage. Clicking an agent row opens a drilldown with that agent's LLM calls.

### 7.3 LLM Call Drilldown

When you expand a model or agent row, a panel appears below showing individual LLM calls with:

- Call name, model, tokens in/out, cost, timestamp
- "View" button to open the full LLM Detail Modal with prompt/response previews
- "Load more" pagination for large datasets

---

## 8. Fleet Pipeline

Accessed via the "Pipeline" tab. Shows the operational backlog across all agents.

### 8.1 Totals Bar

Four summary stats:

| Stat | Description |
|---|---|
| **Total Queue** | Combined queue depth across all agents |
| **Active Issues** | Total unresolved issues fleet-wide |
| **Pending TODOs** | Total active TODO items |
| **Scheduled** | Total scheduled jobs |

### 8.2 Agent Pipeline Table

A table with one row per agent showing:

| Column | Description |
|---|---|
| **Agent** | Clickable agent name (opens Agent Detail) |
| **Queue** | Queue depth (highlighted if > 5) |
| **Issues** | Issue count with red dot indicator |
| **TODOs** | Active TODO count |
| **Oldest Item** | Age of the oldest queued item |
| **Status** | "OK" (green) or "Needs attention" (amber) if queue > 5 or issues > 0 |

---

## 9. Filtering and Navigation

### 9.1 Filter Bar

When a filter is active, a yellow bar appears between the top bar and main content:

```
⬡ Filtering: agent = lead-qualifier                    ✕ Clear
```

Click "✕ Clear" to remove all filters.

### 9.2 Ways to Filter

| Action | Filters to |
|---|---|
| Click an agent card | That agent's data only |
| Click an agent name in any panel | That agent's data only |
| Click a status stat (Processing, Stuck, etc.) | All agents with that status |
| Click a task row | Loads that task's timeline |
| Click a task ID in the stream | Loads that task's timeline |
| Change the environment dropdown | All agents in that environment |

### 9.3 Typical Investigation Flow

1. **Notice** — Attention badge pulses, or Stuck/Error count > 0
2. **Filter** — Click the stuck stat or the stuck agent's card
3. **Identify** — See which agent is stuck and its last heartbeat time
4. **Investigate** — Click the most recent task in the Task Table
5. **Diagnose** — Read the action tree to see where execution stopped
6. **Inspect** — Click a timeline node to see the full payload (error message, LLM content, tool args)

### 9.4 Permalink

Click the "⧉ Permalink" button above the timeline to copy a URL that links directly to the current task view.

---

## 10. Timeline Deep Dive

The timeline is the most detailed debugging tool in HiveBoard. Here's how to read it effectively.

### 10.1 Action Tree (Tree View)

Each node represents a tracked action. The tree structure shows nesting — if action B was called inside action A, B appears indented beneath A.

**Node elements:**

| Element | Meaning |
|---|---|
| ✓ Green icon | Action completed successfully |
| ✗ Red icon | Action failed (click to see error) |
| ⚡ Blue icon | Action is currently running |
| ◆ Purple icon | LLM call (click for prompt/response modal) |
| Duration label | How long the action took |
| Nested children | Actions called from within this action |

**Error chains:** When a task fails, the timeline highlights the causal chain — the sequence of events that led to the failure. These are visually connected with red indicators.

### 10.2 Pinned Detail Panel

Click any timeline node to open a pinned detail panel below the timeline. This shows:

- Event type and status
- Duration
- All payload fields (model, tokens, cost, error message, action arguments, result preview, tags)
- For LLM calls: full prompt and response previews

Click "✕ Close" to dismiss the panel.

---

## 11. Real-Time Behavior

### 11.1 WebSocket

The Fleet dashboard connects via WebSocket on load. When connected, you receive:

- New events in the Activity Stream (instant)
- Agent status changes (heartbeat updates, stuck detection)
- Task lifecycle events

### 11.2 Polling Fallback

If WebSocket fails after 3 retry attempts, the dashboard falls back to HTTP polling every 5 seconds. The connection indicator shows "Polling" to let you know data may be slightly delayed.

### 11.3 Auto-Scroll

The Activity Stream and timeline auto-scroll to show the newest content. If you manually scroll up/back to investigate, auto-scroll disengages. Scrolling back to the end re-engages it.

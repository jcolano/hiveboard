# HiveBoard — Agent View Guide

**Version:** 0.2.0
**Last updated:** 2026-02-15

> *Everything about one agent — identity, current state, performance, LLM usage, pipeline, and problems.*

---

## Table of Contents

1. [Overview](#1-overview)
2. [Agent Selection](#2-agent-selection)
3. [Identity Bar](#3-identity-bar)
4. [Right Now](#4-right-now)
5. [Performance](#5-performance)
6. [LLM Intelligence](#6-llm-intelligence)
7. [Pipeline](#7-pipeline)
8. [Recent Tasks](#8-recent-tasks)
9. [Attention](#9-attention)
10. [Task Expand Modal](#10-task-expand-modal)
11. [Real-Time Behavior](#11-real-time-behavior)
12. [URL Parameters](#12-url-parameters)

---

## 1. Overview

The Agent View page (`agent-view.html`) is a single-agent dossier. You select an agent from a dropdown, and the page renders a complete, scrollable profile — everything HiveBoard knows about that agent, organized into seven sections.

This is the page to use when you want the full story of one specific agent: what it is, what it's doing right now, how it has performed, what LLM calls it makes, what's in its pipeline, its recent tasks, and whether anything needs attention.

The page updates in real time via WebSocket and periodic polling. Identity and status refresh every 15 seconds, performance and tasks every 30 seconds, and LLM insights every 60 seconds.

---

## 2. Agent Selection

The top bar includes two controls specific to Agent View:

### 2.1 Agent Selector

A dropdown listing all registered agents for the current environment. Agents with error or stuck status are marked with a ⚠ indicator. Select an agent to load its full profile.

Before selection, the page shows an empty state with the HiveBoard hexagon and "Select an agent" prompt.

### 2.2 Range Selector

A dropdown to control the time window for performance data: 1h, 6h, 24h (default), 7d. Changing this reloads all time-sensitive data (metrics, costs, tasks, LLM insights) without affecting the identity bar or right-now status.

### 2.3 Deep Linking

You can link directly to an agent's profile by adding `?agent=agent-id` to the URL. The page will auto-select that agent on load.

---

## 3. Identity Bar

The topmost section, always visible when an agent is selected. Displays the agent's identity and key vitals.

### 3.1 Left Side — Name and Tags

| Element | Description |
|---|---|
| **Agent name** | The `agent_id`, displayed prominently |
| **Tags** | Metadata badges showing any non-default values: agent type, framework, version, runtime, group, SDK version |

### 3.2 Right Side — Vital Signs

Three key indicators:

| Indicator | Description |
|---|---|
| **STATUS** | Current derived status badge (Processing / Idle / Stuck / Error / Waiting) with matching color |
| **HEARTBEAT** | Colored dot (green/amber/red) + time since last heartbeat |
| **FIRST SEEN** | How long ago this agent was first registered (e.g., "3d ago" or "Today") |

### 3.3 Warning States

When the agent is stuck, the entire identity bar gains a red warning border. When in error state, it gets an error-colored border. This makes critical states visible at a glance even when scrolled past other sections.

---

## 4. Right Now

A live section showing what the agent is doing at this moment. The content adapts based on the agent's current status.

### 4.1 Processing State

When the agent is actively working:

| Element | Description |
|---|---|
| **Current task ID** | The task being executed |
| **Project** | The project scope (if set) |
| **Plan progress** | If the task has a plan: progress bar, step count ("Step 3 of 5"), percentage, and step list with completion indicators (✓ done, ► current, ○ pending) |
| **Last action** | The most recent action from the timeline, with name, duration, and status |
| **LLM stats** | Aggregated stats from the current task: number of LLM calls, tokens in/out, cost |
| **Queue summary** | If the agent has queued work: item count, age of oldest item, and preview of next item with priority |

### 4.2 Idle State

When the agent is alive but not processing:

| Element | Description |
|---|---|
| **"Idle" label** | Clear status indicator |
| **Last completed task** | The most recent finished task: ID, status (completed/failed), duration, cost |
| **Queue/TODO summary** | Current queue depth and pending TODO count |
| **Next scheduled** | If scheduled work exists: the name of the next enabled scheduled job |

### 4.3 Stuck State

When the agent has stopped heartbeating:

| Element | Description |
|---|---|
| **Stuck indicator** | "⊘ Stuck — No heartbeat for Xm Ys" with exact time since last heartbeat |
| **Last known task** | The task ID the agent was working on when it went silent |
| **Active issues** | List of any issues the agent reported before going silent, with severity, category, and context |

### 4.4 Error State

When the agent's most recent task failed:

| Element | Description |
|---|---|
| **Error indicator** | "✗ Error State" |
| **Failed task** | The task ID that failed |
| **Error message** | The exception message in red |
| **Active issues** | Count of active issues |

---

## 5. Performance

Aggregated performance metrics for the selected time range. Shows how the agent has been performing.

### 5.1 Metric Cards (8 cards in a grid)

| Card | Value | Visual |
|---|---|---|
| **Tasks** | Total tasks (completed + failed) | Sparkline bar chart from timeseries |
| **Success Rate** | Percentage, red if below 80% | — |
| **Avg Duration** | Average task duration | — |
| **Cost** | Total LLM cost (purple) | Sparkline bar chart from timeseries |
| **LLM Calls** | Total LLM call count (purple) | — |
| **Tokens** | Total tokens in / out | — |
| **Errors** | Error count, red if > 0 | Sparkline bar chart from timeseries |
| **Throughput** | Tasks per hour | — |

### 5.2 Derived Insights

Below the grid, computed insight values:

| Insight | Formula |
|---|---|
| **LLM calls/task** | Total LLM calls ÷ total tasks |
| **Cost/task** | Total cost ÷ total tasks |

These help identify if the agent is making too many LLM calls per task or spending too much per task.

---

## 6. LLM Intelligence

A tabbed section for deep analysis of the agent's LLM usage. Three tabs:

### 6.1 Call Patterns Tab

A table showing each distinct LLM call name and its aggregate metrics:

| Column | Description |
|---|---|
| **Call Name** | The name parameter from `task.llm_call()`, with a colored dot |
| **Calls** | Total invocation count |
| **Tok In** | Total input tokens |
| **Tok Out** | Total output tokens |
| **Cost** | Total cost |
| **Avg ms** | Average latency per call |

Below each row, a sub-detail line shows: primary model used, and average tokens per call.

**Efficiency Box:** Below the table, shows the top 3 calls' share of total cost (e.g., "reasoning: 68% of cost (24 calls)") and the biggest single prompt by token count.

### 6.2 Model Breakdown Tab

A card for each LLM model the agent uses, showing:

| Element | Description |
|---|---|
| **Model name** | Short name (date suffix stripped) |
| **Cost share** | Percentage of total cost with progress bar |
| **Stats** | Call count, tokens in/out, total cost, average latency |
| **Used for** | Which call names use this model |

**Token Flow Chart:** A visual timeseries of token consumption over the selected range. Shows total tokens in/out with in/out ratio (e.g., "In/Out ratio: 3.2:1"). Higher ratios suggest prompt-heavy workloads; lower ratios suggest the agent produces long outputs.

### 6.3 Call Log Tab

A chronological list of individual LLM calls, newest first. Task boundaries are marked with dividers ("── task boundary ──").

Each call entry shows:

| Element | Description |
|---|---|
| **Timestamp** | HH:MM:SS format |
| **Call name** | What this LLM call was for |
| **Model** | Short model name |
| **Tokens** | Input → Output token counts |
| **Cost** | Dollar amount (estimated costs prefixed with ~) |
| **Task context** | Task ID this call belongs to |
| **View buttons** | "View prompt" and "View response" buttons to open the LLM Detail Modal |

The Call Log is especially useful for debugging individual interactions — you can trace the exact sequence of LLM calls a task made and inspect prompts and responses.

---

## 7. Pipeline

The agent's operational backlog, displayed in a two-column layout alongside Recent Tasks.

Four groups, each showing a count and list of items:

### 7.1 Queue

Items waiting to be processed. Each item shows:

- Priority badge (URGENT / HIGH / NORMAL / LOW) with color coding
- Summary text
- Time since queued

Shows "Empty" when the queue is clear.

### 7.2 TODOs

Active TODO items the agent has created. Each shows:

- Priority badge
- Context/description
- Source (what created the TODO)

### 7.3 Issues

Active problems the agent has reported. Each shows:

- Severity indicator (critical/high/medium/low) with colored dot
- Severity label and category (e.g., "high · permissions")
- Context description
- Occurrence count (e.g., "seen 3x")

### 7.4 Scheduled

Recurring jobs. Each shows:

- Job name
- Interval (e.g., "every 1h")
- Next run time
- Last run status indicator (✓ or ✗)

---

## 8. Recent Tasks

A list of the agent's most recent tasks (up to 20), displayed as cards in the right column alongside Pipeline.

### 8.1 Task Cards

Each card shows:

| Element | Description |
|---|---|
| **Status icon** | ✓ completed, ✗ failed, ⏳ processing, ⊘ stuck, ⏸ waiting, ◷ timeout/max_turns |
| **Task ID** | The task identifier |
| **Time** | Relative timestamp ("3m ago") |
| **Type badge** | Task type (if set) |
| **Duration** | How long the task took |
| **Action count** | Number of tracked actions |
| **Cost** | Total LLM cost |
| **LLM count** | Number of LLM calls (◆ 3 LLM) |
| **Error line** | Red text showing the error message (failed tasks only) |
| **Escalation line** | Escalation reason (escalated tasks only) |

### 8.2 Interaction

Click any task card to open the **Task Expand Modal** (see Section 10).

---

## 9. Attention

The final section surfaces items that need human attention. It implements automated detection logic to flag problems.

### 9.1 Detection Rules

The Attention section checks for:

| Rule | Severity | Trigger |
|---|---|---|
| **Agent Stuck** | Critical | Agent's derived status is "stuck" |
| **Critical/High Issues** | Critical/Warning | Active issues with severity "critical" or "high" |
| **Stuck TODOs** | Warning | High-priority TODOs older than 1 hour |
| **Error Pattern** | Warning | More than 3 errors in the selected time range |
| **High Cost per Task** | Warning | Average cost per task exceeds $0.50 |

### 9.2 Display

When attention items exist, the section shows an amber "⚠ Attention" header with an item count, and lists each item with its title and description.

When no attention items exist, a green "✓ No attention items — agent is healthy" message appears instead.

---

## 10. Task Expand Modal

Clicking a task card opens a full-screen modal with the complete task timeline.

### 10.1 Summary

At the top: status badge, total duration, and total cost.

### 10.2 Plan

If the task had a plan, shows all steps with completion indicators.

### 10.3 Actions

The action tree rendered as a list, showing:

- Status icon (✓/✗/⚡) with color coding
- Action name
- Duration
- Nesting (indented for child actions)

### 10.4 LLM Calls

All LLM calls within this task, each showing:

- ◆ icon
- Call name
- Model + token counts
- Cost

Click any LLM call entry to open the LLM Detail Modal with prompt/response previews.

### 10.5 Error Chains

If the task failed, shows the linked error events in sequence: event type and error summary for each.

---

## 11. Real-Time Behavior

Agent View uses a tiered refresh strategy:

| Data | Refresh Interval | What updates |
|---|---|---|
| Identity + Status + Pipeline | Every 15 seconds | Identity Bar, Right Now, Pipeline |
| Performance + Tasks | Every 30 seconds | Performance grid, Recent Tasks, Attention |
| LLM Insights | Every 60 seconds | All three LLM Intelligence tabs |

WebSocket provides additional real-time updates for agent status changes and new events. When the WebSocket receives a status change or new event for the selected agent, it triggers an immediate refresh of the Identity Bar and Right Now sections.

---

## 12. URL Parameters

| Parameter | Effect |
|---|---|
| `agent` | Pre-selects the specified agent on page load. Example: `agent-view.html?agent=lead-qualifier` |
| `apiKey` | Sets the API key for authentication (also stored in localStorage) |

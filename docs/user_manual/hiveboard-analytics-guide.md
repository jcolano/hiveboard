# HiveBoard — Analytics Deep Dive Guide

**Version:** 0.2.0
**Last updated:** 2026-02-15

> *Where are the patterns, costs, and problems? Six analysis sections, one scrollable page.*

---

## Table of Contents

1. [Overview](#1-overview)
2. [Toolbar and Controls](#2-toolbar-and-controls)
3. [Fleet Status](#3-fleet-status)
4. [Cost Rankings](#4-cost-rankings)
5. [Activity Rankings](#5-activity-rankings)
6. [Error Analysis](#6-error-analysis)
7. [Prompt Analysis](#7-prompt-analysis)
8. [Tool & Action Usage](#8-tool--action-usage)
9. [HiveMind Analysis](#9-hivemind-analysis)
10. [Data Sources](#10-data-sources)

---

## 1. Overview

The Analytics page (`analytics.html`) is a scrollable, analytical view of your fleet's operational data. Unlike the real-time Fleet dashboard, Analytics focuses on **aggregated patterns and comparisons over a configurable time range**.

The page is organized into six collapsible sections, each addressing a specific analytical question. Each section can be expanded or collapsed by clicking its header.

**API data sources:** This page reads from the Insights Engine endpoints (`/v1/insights/*`), the agents endpoint (`/v1/agents`), and the timeseries endpoint. Data auto-refreshes every 60 seconds, with a "last updated" ticker in the toolbar.

---

## 2. Toolbar and Controls

The toolbar at the top of the page provides:

| Element | Description |
|---|---|
| **Title** | "Analytics Deep Dive" with search icon |
| **Range selector** | Dropdown to choose the analysis window: 1 hour, 6 hours, 24 hours (default), 7 days, 30 days, 90 days |
| **Last updated** | Shows how many seconds since the last data fetch, updated every 5 seconds |

Changing the range triggers a full data reload of all six sections.

---

## 3. Fleet Status

**Section tag:** `LIVE`
**Question answered:** "Who's alive right now?"

This section gives a real-time snapshot of agent health, grouped by operational state.

### 3.1 Status Strip

A horizontal bar segmented by state, showing proportional representation:

| Segment | Color | Meaning |
|---|---|---|
| **Running** | Green | Agents actively processing tasks |
| **Idle** | Amber | Agents alive but not working |
| **Stopped** | Red | Agents that are erroring, stuck, or have exceeded their heartbeat threshold |

### 3.2 Agent Status Rows

Each agent gets a detailed row showing:

| Element | Description |
|---|---|
| **Heartbeat dot** | Animated indicator — running (green pulse), idle (amber), stopped (red) |
| **Agent name** | The agent_id |
| **Status badge** | Running/Idle/Stopped with color coding |
| **Heartbeat age** | Time since last heartbeat with heartbeat SVG icon |
| **Last event** | Event type and time of the most recent event from this agent |
| **Cost** | Total LLM cost for this agent in the selected range |
| **Cost/task** | Computed average cost per task |

Agents are sorted: running first, then idle, then stopped.

### 3.3 Cost by Status

Three summary cards showing total cost attributed to each status group:

- **Running** agents' total cost and percentage of fleet spend
- **Idle** agents' cost (spend accumulated before going idle)
- **Stopped** agents' cost

Each card shows: total cost, percentage of fleet total, average cost per agent, and average cost per task.

### 3.4 HiveMind Commentary

An automated analysis block summarizing the fleet state: how many agents are running, what percentage of spend they represent, and which agents are stopped.

---

## 4. Cost Rankings

**Section tag:** `A1–A3`
**Question answered:** "Who's spending what?"

This section ranks agents by LLM cost and helps identify cost outliers.

### 4.1 KPI Cards (4 cards)

| Card | Shows |
|---|---|
| **Most Expensive** | Agent with highest LLM cost, with call count and token totals. Ranked #1 |
| **Least Expensive** | Agent with lowest cost. Useful as a baseline |
| **Fleet Average** | Average cost across all agents with fleet total |
| **Cost Spread** | Max/min ratio (e.g., "4.2×") showing how uneven spending is. Also shows max vs. average ratio |

### 4.2 Cost Distribution Strip

A horizontal stacked bar showing each agent's share of total fleet cost as a colored segment. Segments are proportional to cost and labeled with agent name and percentage when wide enough.

### 4.3 Ranked Bars

Horizontal bar chart ranking agents from most to least expensive. Each bar shows the dollar amount and percentage of fleet total.

### 4.4 HiveMind Commentary

Identifies the most expensive agent, compares it to the fleet average and least expensive agent, states its share of total spend, and identifies its primary LLM model.

---

## 5. Activity Rankings

**Section tag:** `A4–A6`
**Question answered:** "Who's doing the most work?"

### 5.1 KPI Cards (3 cards)

| Card | Shows |
|---|---|
| **Most Active Agent** | Agent with the most completed tasks. Shows tasks/hr average and success rate |
| **Least Active** | Agent with fewest completed tasks |
| **Fleet Total** | Total tasks across all agents, average per agent, and peak hour |

### 5.2 Ranked Bars

Horizontal bar chart ranking agents by tasks completed. Each bar shows count and fleet percentage.

### 5.3 Drilldown: Top Agent

Two togglable views for the most active agent:

**By Task Type:** Bar chart showing distribution of work across task types (e.g., "lead_processing", "email_send"). Includes an insight card highlighting the dominant task type.

**By Action:** Bar chart showing the most-called actions/tools for this agent. Includes an insight card highlighting the most-called action.

---

## 6. Error Analysis

**Section tag:** `A7–A10`
**Question answered:** "Where are things breaking?"

### 6.1 KPI Cards (4 cards)

| Card | Shows |
|---|---|
| **Most Errors** | Agent with the highest error count, with error rate percentage. Shows "0 errors — All clear!" when no errors exist |
| **Fewest Errors** | Agent with fewest errors |
| **Fleet Error Rate** | Total errors / total tasks as a percentage |
| **Top Error Type** | The most frequent error type (e.g., "TimeoutError"), with occurrence count and percentage |

### 6.2 Ranked Bars

Horizontal bars ranking agents by error count. Bar opacity decreases with rank to create visual emphasis on the worst offenders.

### 6.3 Drilldown: Worst Agent

When errors exist, a detailed drilldown of the most error-prone agent shows three mini-breakdowns:

| Breakdown | Shows |
|---|---|
| **By Error Type** | What kinds of errors occur (e.g., TimeoutError, ValueError) |
| **By Task Type** | Which task types produce the most errors |
| **By Tool/Action** | Which tools/actions fail most often |

### 6.4 HiveMind Commentary

Identifies the worst offender, its share of total errors, the dominant error type, and the specific task type and tool where errors concentrate.

---

## 7. Prompt Analysis

**Section tag:** `A11–A14`
**Question answered:** "Which prompts are biggest, most frequent, and most expensive?"

### 7.1 Prompt Table

A ranked table of all LLM call names (prompts), sorted by token size:

| Column | Description |
|---|---|
| **#** | Rank (with colored rank circle: gold/silver/bronze for top 3) |
| **Prompt / Call Name** | The name given to the LLM call via `task.llm_call()` |
| **Avg Tokens** | Average input token count per call |
| **Calls** | Total number of times this prompt was invoked |
| **Agent(s)** | Which agents use this prompt (blue badges) |
| **Model** | Primary LLM model used for this call |
| **Est. Cost** | Total estimated cost. Color-coded: green (< $0.50), amber ($0.50–$1), red (> $1) |

### 7.2 HiveMind Commentary

Identifies the highest total cost driver (prompt × frequency), and calls out the single largest prompt by token count.

---

## 8. Tool & Action Usage

**Section tag:** `A15–A22`
**Question answered:** "Which tools/actions run most often, and how do they perform?"

### 8.1 Usage Summary Pills

A row of compact pills, one per action, each showing:

- Total invocation count
- Action name
- Hourly average rate

### 8.2 Action Detail Table

| Column | Description |
|---|---|
| **Action** | Tool/action name |
| **Total** | Total number of invocations |
| **Used By** | Which agents call this action (blue badges) |
| **Avg Duration** | Average execution time |
| **Success Rate** | Percentage of successful completions. Color-coded: green (≥ 95%), amber (80-95%), red (< 80%) |
| **Peak Hour** | Hour of day (UTC) with highest usage |

### 8.3 Hourly Activity Heatmap

A grid visualization showing action usage by hour of day (0–23 UTC). Actions are rows, hours are columns, and cell intensity shows invocation volume. The heatmap uses a four-level color scale from light (few) to dark (many).

Hover over a cell to see exact count: "action_name @ 14:00 — 23 starts".

A legend at the bottom shows the color scale from "Less" to "More".

### 8.4 Weekly Aggregation (7d+ ranges only)

When the time range is 7 days or longer, an additional table shows action usage broken down by day of the week (Mon–Sun) with totals and a trend indicator (▲ up, ▼ down, — flat) comparing the first and second half of the week.

### 8.5 HiveMind Commentary

Identifies the action with the lowest success rate, the slowest action, and the busiest action by invocation count.

---

## 9. HiveMind Analysis

Every section includes a "HiveMind Analysis" commentary block — an automated narrative that synthesizes the data into actionable insights. These blocks:

- Identify the most significant finding in each section
- Provide comparative context (e.g., "3.2× more expensive than the fleet average")
- Call out specific agents, models, or actions by name
- Highlight concerning patterns (e.g., "concentrated in the crm_search tool")

These are computed client-side from the API data and update when the range changes.

---

## 10. Data Sources

The Analytics page pulls from six API endpoints:

| Endpoint | What it provides |
|---|---|
| `GET /v1/agents` | Agent list with status, heartbeat, metadata |
| `GET /v1/insights/agents` | Per-agent cost, task counts, model usage, action breakdown, task type distribution |
| `GET /v1/insights/errors` | Error counts by agent, error type, task type, and action |
| `GET /v1/insights/prompts` | LLM call names ranked by token size, cost, and frequency |
| `GET /v1/insights/actions` | Action/tool performance: invocations, duration, success rate, hourly distribution |
| `GET /v1/insights/timeseries` | Time-bucketed metrics for trend analysis |

All endpoints accept a `range` parameter that matches the toolbar selector. Data refreshes every 60 seconds automatically.

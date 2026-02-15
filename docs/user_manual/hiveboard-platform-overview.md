# HiveBoard Observability Platform — Overview

**Version:** 0.2.0
**Last updated:** 2026-02-15

> *See what your AI agents are doing, why they fail, how long they take, and how much they cost — in real time.*

---

## Table of Contents

1. [What is HiveBoard](#1-what-is-hiveboard)
2. [The Four Dashboard Pages](#2-the-four-dashboard-pages)
3. [When to Use Each Page](#3-when-to-use-each-page)
4. [Shared Elements](#4-shared-elements)
5. [Real-Time Architecture](#5-real-time-architecture)
6. [Data Flow — From SDK to Dashboard](#6-data-flow--from-sdk-to-dashboard)
7. [Common Workflows](#7-common-workflows)
8. [Glossary](#8-glossary)

---

## 1. What is HiveBoard

HiveBoard is a real-time observability platform for AI agent fleets. It treats agents as operational entities — workers with heartbeats, tasks, status, and cost — rather than just API calls. You instrument your agents with the HiveLoop SDK, and HiveBoard gives you a live window into everything they do.

The platform consists of four interconnected dashboard pages, each designed for a different question:

- **Fleet** — "What is happening across my entire fleet right now?"
- **Analytics** — "Where are the patterns, costs, and problems over time?"
- **Agent View** — "What is this one specific agent doing, and is it healthy?"
- **Insights** — "What does the data tell me I should know?"

Together, these four pages give you complete visibility from fleet-level overview down to individual LLM prompt/response inspection.

---

## 2. The Four Dashboard Pages

### Fleet (`fleet.html`)

The operational command center. A three-column layout showing your entire agent fleet in real time.

**Layout:**

```
┌─────────┬──────────────────────────────────────────┬─────────────────────┐
│ AGENTS  │          MISSION CONTROL                 │    NARRATIVE        │
│         │     (or Costs / Pipeline /               │    + ACTIVITY       │
│  Fleet  │      Agent Detail)                       │    STREAM           │
│  cards  │                                          │                     │
│  with   │  Stats · Charts · Timeline · Tasks       │  Live event feed    │
│  status │                                          │                     │
└─────────┴──────────────────────────────────────────┴─────────────────────┘
```

**Includes five internal views:** Mission Control (default), Costs, Pipeline, Agent Detail, and the always-visible Activity Stream.

### Analytics (`analytics.html`)

The analytical deep dive. A single scrollable page with six collapsible analysis sections, each answering a specific category of questions about fleet behavior over a configurable time range.

**Sections:** Fleet Status, Cost Rankings, Activity Rankings, Error Analysis, Prompt Analysis, Tool & Action Usage.

### Agent View (`agent-view.html`)

The single-agent dossier. Select one agent and see everything about it: identity, current state, performance metrics, LLM usage patterns, work pipeline, recent tasks, and attention items.

**Sections:** Identity Bar, Right Now, Performance, LLM Intelligence, Pipeline, Recent Tasks, Attention.

### Insights (`insights.html`)

The intelligence briefing. Organizes 38 computed questions into four narrative "moments" that walk you through fleet health, investigation, optimization, and review. Includes smart detectors, a health gauge, and a scale-readiness checklist.

**Moments:** The Glance, The Investigation, The Optimization, The Review.

---

## 3. When to Use Each Page

| You want to… | Go to |
|---|---|
| See if all agents are alive and working | Fleet → Mission Control |
| Investigate a stuck or erroring agent | Fleet → click the agent card |
| Debug a specific task's action-by-action timeline | Fleet → click a task row |
| See which agent costs the most | Analytics → Cost Rankings |
| Find which tool/action fails most often | Analytics → Error Analysis or Tool & Action Usage |
| Understand one agent's complete story | Agent View → select the agent |
| See an agent's LLM prompt/response details | Agent View → LLM Intelligence → Call Log |
| Get a fleet health score | Insights → The Review → Health Gauge |
| Find optimization opportunities | Insights → The Optimization |
| Check if you're ready to scale | Insights → The Review → Scale Readiness |
| See what's queued across all agents | Fleet → Pipeline tab |
| Identify the biggest prompt by token size | Analytics → Prompt Analysis |
| Compare agents side by side on cost and activity | Analytics → Cost Rankings + Activity Rankings |
| Understand cost trends over time | Agent View → Performance section |

---

## 4. Shared Elements

### 4.1 Top Bar

All four pages share a top navigation bar with consistent elements:

| Element | Description |
|---|---|
| **HiveBoard logo** | Brand identity; on some pages, links back to Fleet |
| **Workspace badge** | Shows the current workspace/tenant name (e.g., `loopcore-prod`) |
| **View tabs** | Navigation between the four pages: Analytics, Agent View, Insights, Fleet. The active page is highlighted |
| **Connection indicator** | Green pulsing dot = live WebSocket connection. Yellow = reconnecting. Shows "Polling" if WebSocket fails |
| **Environment selector** | Dropdown to switch between `production`, `staging`, etc. Filters all data on the page |

### 4.2 Connection Status

The dashboard connects to the HiveBoard server via WebSocket for real-time updates, with HTTP polling as a fallback.

| Indicator | Meaning |
|---|---|
| 🟢 Green pulsing dot + "Connected" / "Live" | WebSocket is active; data updates in real time |
| 🟡 Yellow dot + "Reconnecting…" | Connection was lost; automatic reconnection in progress |
| Yellow dot + "Polling" | WebSocket failed after retries; falling back to periodic HTTP polling |
| "Loading…" | Initial page load, fetching data |

### 4.3 Environment Selector

The environment dropdown filters all data on the page to agents that were initialized with a matching `environment` parameter in HiveLoop. This maps to the `environment` parameter in `hiveloop.init()`. Use it to separate production monitoring from staging/development.

### 4.4 LLM Detail Modal

All pages that show LLM call data (Fleet, Agent View) share a common modal that appears when you click an LLM call entry. The modal shows:

- Call name and model
- Token counts (in/out) with a visual ratio bar
- Cost and latency
- Prompt preview (if captured)
- Response preview (if captured)

Click outside the modal or press the ✕ button to close it.

### 4.5 Color Language

All pages share a consistent color vocabulary:

| Color | Meaning | Used for |
|---|---|---|
| Blue | Active/working | Processing status, action nodes, throughput |
| Green | Success/healthy | Completed tasks, fresh heartbeat, good metrics |
| Red | Failure/problem | Failed tasks, errors, stuck agents, issues |
| Amber | Needs attention | Waiting for approval, stale heartbeat, warnings |
| Purple | LLM-related | LLM calls, costs, model data |
| Gray | Inactive | Idle agents, pending steps |

### 4.6 Typography

| Context | Font |
|---|---|
| UI text (labels, headings, body) | Plus Jakarta Sans |
| Data values (IDs, code, timestamps, metrics) | IBM Plex Mono |

---

## 5. Real-Time Architecture

HiveBoard stays live through two mechanisms:

**WebSocket** — The server pushes new events as they happen. Agent cards update status, timelines append new nodes, and the activity stream updates instantly. Each page subscribes to the events it cares about.

**Polling** — Periodic HTTP refreshes ensure nothing is missed. Refresh intervals vary by page and data sensitivity:

| Page | What refreshes | Interval |
|---|---|---|
| Fleet | Agents, tasks, events, metrics | 5s (poll) + WebSocket |
| Analytics | All six sections | 60s auto-refresh |
| Agent View | Identity/status: 15s · Performance/tasks: 30s · LLM insights: 60s |
| Insights | On-demand via Refresh button |

---

## 6. Data Flow — From SDK to Dashboard

```
Your Agent Code
    ↓  (decorators + events)
HiveLoop SDK
    ↓  (batched HTTP POST to /v1/ingest)
HiveBoard Server
    ↓  (processes, stores, computes derived state)
Query API  ←→  Dashboard Pages
    ↓
WebSocket push → Real-time updates
```

The SDK captures three groups of data:

1. **Identity** — What the agent *is* (ID, type, version, framework, environment)
2. **State** — What the agent *has* right now (status, queue, issues, plans)
3. **Activity** — What the agent *does* (tasks, actions, LLM calls, errors, retries)

The dashboard reads this data through 18 API endpoints that serve different views. Each page calls the endpoints relevant to its purpose.

---

## 7. Common Workflows

### "Something is wrong — triage"

1. Open **Fleet** → check the Stats Ribbon for Stuck/Error counts
2. Click the red-highlighted agent card in the sidebar
3. Read the Timeline to see the last actions before failure
4. Click timeline nodes to inspect payloads and error messages
5. Optionally switch to **Agent View** for the full agent dossier

### "Is my fleet healthy today?"

1. Open **Insights** → The Glance section shows fleet status, costs, and recent activity at a glance
2. Scroll to The Review → Health Gauge gives a 0-100 score with deduction explanations
3. Check the Scale Readiness checklist for specific pass/fail criteria

### "Where is the money going?"

1. Open **Analytics** → Cost Rankings section shows who spends what
2. Read the HiveMind Analysis commentary for automated insights
3. Drill into the most expensive agent to see model and call breakdown
4. Switch to **Agent View** → LLM Intelligence → Call Patterns to see cost by prompt name

### "Why did this task fail?"

1. On **Fleet** → click the failed task row in the Task Table
2. The Timeline loads with the full action tree for that task
3. Red nodes indicate failures — click one to see the error message and stack trace
4. Check the error chains section for linked failure events
5. Use **Analytics** → Error Analysis for broader error patterns

---

## 8. Glossary

| Term | Definition |
|---|---|
| **Agent** | An AI agent process instrumented with HiveLoop. Identified by `agent_id` |
| **Task** | A unit of work performed by an agent. Has a lifecycle: started → completed/failed |
| **Action** | A tracked operation within a task (tool call, API request, processing step) |
| **Heartbeat** | Periodic signal emitted by the SDK to prove the agent is alive |
| **Stuck** | An agent that has stopped sending heartbeats beyond its configured threshold |
| **Pipeline** | An agent's operational backlog: queue items, TODOs, issues, scheduled work |
| **LLM Call** | A tracked call to a language model, with token counts, cost, and optional prompt/response preview |
| **Plan** | An ordered sequence of steps an agent intends to follow for a task |
| **Issue** | A problem reported by the agent (permissions, connectivity, rate limits, etc.) |
| **Environment** | Deployment context (production, staging, dev) used to filter dashboard data |
| **Workspace** | The tenant/account that owns the agents and data |
| **Derived Status** | The computed current state of an agent: idle, processing, stuck, error, or waiting_approval |
| **Success Rate** | Percentage of tasks completed successfully vs. total tasks (completed + failed) |
| **Throughput** | Number of tasks completed per unit of time |
| **Cost per Task** | Total LLM spend divided by number of tasks |
| **Action Tree** | Hierarchical visualization of nested actions within a task |
| **Error Chain** | Linked sequence of error events showing causal relationships |
| **Smart Detector** | An automated rule in Insights that checks for specific operational problems |

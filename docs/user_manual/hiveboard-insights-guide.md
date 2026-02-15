# HiveBoard — Insights Guide

**Version:** 0.2.0
**Last updated:** 2026-02-15

> *38 questions, four narrative moments — what your fleet data tells you that you should know.*

---

## Table of Contents

1. [Overview](#1-overview)
2. [Toolbar and Controls](#2-toolbar-and-controls)
3. [Moment 1: The Glance](#3-moment-1-the-glance)
4. [Moment 2: The Investigation](#4-moment-2-the-investigation)
5. [Moment 3: The Optimization](#5-moment-3-the-optimization)
6. [Moment 4: The Review](#6-moment-4-the-review)
7. [Question Reference](#7-question-reference)
8. [Data Sources](#8-data-sources)

---

## 1. Overview

The Insights page (`insights.html`) is an intelligence briefing for your AI agent fleet. Rather than showing raw data or configurable dashboards, it organizes **38 computed questions** into four narrative "moments" — each designed for a specific mindset.

The page is designed to be read top-to-bottom like a report: start with a glance at fleet health, investigate specific agents, find optimization opportunities, and end with a holistic review.

Each moment is a collapsible section. Each question within a moment is rendered as a card or visualization with a question ID (Q1–Q38) shown in the corner for reference.

**Key distinction from Analytics:** Analytics lets you explore data freely across six analytical dimensions. Insights tells you what the data means — it's opinionated, narrative-driven, and includes automated detectors and health scoring.

---

## 2. Toolbar and Controls

| Element | Description |
|---|---|
| **Range selector** | Choose the analysis window: 1 hour, 6 hours, 24 hours (default), 7 days |
| **Agent filter** | Optional filter to scope insights to a specific agent |
| **Refresh button** | Manual refresh with spinning animation. Reloads all data from APIs |
| **Last updated** | Timestamp of the most recent data fetch |

Unlike Analytics (which auto-refreshes every 60s), Insights refreshes on demand via the Refresh button. This is intentional — Insights is designed for deliberate review, not continuous monitoring.

---

## 3. Moment 1: The Glance

**Section title:** "The Glance — Fleet Status at a Glance"

This moment answers: **"Is everything OK? Do I need to act?"** It's designed to be scannable in under 5 seconds.

### Q1: Fleet Status Bar

A horizontal bar segmented by agent status (processing, idle, stuck, error, waiting). Each segment shows the count and is proportionally sized. Below the bar, a legend labels each status with its count.

**Read it as:** Green/blue segments = healthy. Any red segments = action needed.

### Q2: Fleet Vitals

Four big-number cards:

| Card | Shows | Good sign |
|---|---|---|
| **Total Agents** | Count of registered agents | Matches expectations |
| **Active Now** | Agents currently processing | Non-zero during work hours |
| **Success Rate** | Fleet-wide task success percentage | ≥ 90% |
| **Tasks Completed** | Total completed tasks in the range | Consistent with workload |

### Q3: Cost Snapshot

Three cards for cost awareness:

| Card | Shows |
|---|---|
| **Total Spend** | Fleet-wide LLM cost for the range |
| **Cost per Task** | Average LLM cost per task |
| **Projected Monthly** | Current spend extrapolated to 30 days |

### Q4: Throughput Sparkline

A mini bar chart showing task completion volume over time. Helps spot drops or spikes in activity.

### Q5: Latest Activity Feed

The 10 most recent events across the fleet, showing timestamp, agent name, event type, and summary. Includes a live badge if events are very recent.

---

## 4. Moment 2: The Investigation

**Section title:** "The Investigation — Agent & Task Deep Dive"

This moment answers: **"Let me dig into a specific agent — what's its story?"**

### Agent Selector

A prominent selector bar at the top of this section lets you choose which agent to investigate. Styled with the accent color to distinguish it from the global agent filter.

### Q6: Agent Status Card

A detailed card for the selected agent showing:

- Status badge (Processing/Idle/Stuck/Error)
- Heartbeat age with colored indicator
- Current task ID (if processing)
- Last event type and time

### Q7: Agent Performance Summary

Key performance numbers for the selected agent in the current range:

- Tasks completed / failed
- Success rate
- Average duration
- Total LLM cost
- LLM calls per task average

### Q8: Agent vs Fleet Comparison

Side-by-side comparison of the selected agent against fleet averages:

| Metric | Agent | Fleet Avg |
|---|---|---|
| Success rate | — | — |
| Avg duration | — | — |
| Cost per task | — | — |
| Throughput | — | — |

Values better than fleet average are highlighted green; worse values are highlighted red.

### Q9: Agent Task Breakdown

Distribution of the selected agent's tasks by type, shown as horizontal bars with counts and percentages.

### Q10–Q16: Extended Agent Analysis

Additional cards covering:

- **Q10:** Error breakdown for this agent (by type)
- **Q11:** LLM model usage distribution
- **Q12:** Top actions/tools by invocation count
- **Q13:** Queue depth and pipeline status
- **Q14:** Cost trend over the selected range
- **Q15:** Duration trend over the selected range
- **Q16:** Task completion timeline

---

## 5. Moment 3: The Optimization

**Section title:** "The Optimization — Cost & Reliability"

This moment answers: **"Where can I improve? What should I fix or optimize?"**

### Q17: Cost Leaderboard

Ranking of agents by total cost, with the most expensive highlighted. Shows cost, call count, and share of fleet total.

### Q18: Cost per Task Ranking

Agents ranked by average cost per task. Useful for finding agents that are expensive per unit of work (even if their total spend is modest due to low volume).

### Q19: Model Cost Comparison

Breakdown of cost by LLM model across the entire fleet. Helps identify whether switching models could save money.

### Q20: Token Efficiency

Analysis of input/output token ratios. Agents with very high input-to-output ratios may be sending oversized prompts.

### Q21: Slowest Tasks

A table of the slowest tasks in the fleet, showing task ID, agent, duration, and status. Helps identify performance bottlenecks.

### Q22: Error Hotspots

Agents ranked by error count with error rate percentages. Includes breakdown by error type.

### Q23: Retry Analysis

If agents use retries, shows retry frequency and which tasks/actions trigger the most retries.

### Q24: Action Performance Ranking

Actions ranked by success rate and average duration. Low success rates and high durations are highlighted as optimization opportunities.

### Q25: Idle Time Analysis

How much time agents spend idle vs. working. High idle percentages may indicate over-provisioning.

### Q26: Queue Health

Analysis of queue depths and item ages across agents. Long queues or stale items indicate capacity problems.

### Smart Detectors (Q27–Q28)

Automated checks that flag specific operational problems:

| Detector | What it checks | Status |
|---|---|---|
| **Stuck Agent Detector** | Any agents with stale heartbeats | OK / Warning / Error |
| **Error Spike Detector** | Sudden increase in error rate | OK / Warning |
| **Cost Anomaly Detector** | Cost significantly above normal | OK / Warning |
| **Queue Backlog Detector** | Growing queue depths | OK / Warning |
| **Idle Fleet Detector** | All agents idle when they shouldn't be | OK / Warning |
| **Success Rate Detector** | Fleet success rate below threshold | OK / Warning |

Each detector shows as a card with a colored left border: green (OK), amber (Warning), red (Error). The card includes a description of what was checked and the current finding.

---

## 6. Moment 4: The Review

**Section title:** "The Review — Trends & Fleet Health"

This moment answers: **"Stepping back — how is the fleet doing overall, and is it ready to scale?"**

### Q29–Q35: Metric Summaries

Quick-reference cards for fleet-wide totals:

| Card | Shows |
|---|---|
| **Q29:** Total tasks | Completed + failed count |
| **Q30:** Total LLM calls | Fleet-wide LLM invocation count |
| **Q31:** Total cost | Fleet-wide LLM spend |
| **Q32:** Total tokens | Input + output token totals |
| **Q33:** Average duration | Fleet-wide mean task duration |
| **Q34:** Fleet error count | Total errors in the range |
| **Q35:** Agent uptime | Summary of agent availability |

### Q36: Trend Summary

Computes directional trends (up/down/flat) for four key metrics over the selected time range:

| Metric | Good direction |
|---|---|
| **Throughput** | Up ↑ |
| **Errors** | Down ↓ |
| **Cost** | Down ↓ |
| **Duration** | Down ↓ |

Each trend shows a percentage change and a colored indicator. A verdict banner at the bottom summarizes:

| Verdict | Condition | Color |
|---|---|---|
| "Trends look healthy. Keep it up." | ≥ 3 of 4 trends favorable | Green |
| "Mixed signals — some trends need attention." | 2 of 4 favorable | Amber |
| "Multiple concerning trends. Review details above." | ≤ 1 favorable | Red |

Requires at least 4 timeseries data points to compute. Shows "Not enough data points" otherwise.

### Q37: Health Gauge

A visual gauge (0-100) representing overall fleet health. The score starts at 100 and subtracts for problems:

| Deduction | Points |
|---|---|
| Each stuck agent | -15 per agent |
| Each agent in error state | -10 per agent |
| Success rate below 90% | -(90 - rate) × 0.5 |
| Active issues | -5 per issue (max -20) |
| No agents registered | -50 |

The gauge displays as a semi-circular SVG with color coding:

| Score | Label | Color |
|---|---|---|
| 80–100 | Healthy | Green |
| 50–79 | Needs Attention | Amber |
| 0–49 | Critical | Red |

Below the gauge, a deduction breakdown shows exactly what reduced the score (e.g., "1 stuck (-15) · Success rate 82% (-4)"). If no deductions: "No deductions — everything looks great!"

### Q38: Scale Readiness Checklist

A pass/fail checklist evaluating whether the fleet is ready to scale:

| Check | Pass condition |
|---|---|
| All agents online | Every agent's heartbeat is within threshold |
| No stuck agents | Zero stuck agents |
| Success rate ≥ 90% | Fleet-wide success rate meets threshold |
| No active issues | Zero unresolved issues |
| Cost/task < $1.00 | Average cost per task is under a dollar |
| Multi-agent fleet | At least 2 agents registered |
| Recent activity | At least one event in the current range |

Summary banner:

| Result | Condition | Color |
|---|---|---|
| "Ready to scale" | All checks pass | Green |
| "Almost ready" | All but 1–2 checks pass | Amber |
| "Not ready yet" | 3+ checks fail | Red |

---

## 7. Question Reference

Quick lookup of all 38 questions by ID:

| ID | Moment | What it answers |
|---|---|---|
| Q1 | Glance | Fleet status distribution |
| Q2 | Glance | Fleet vitals (agents, active, success rate, tasks) |
| Q3 | Glance | Cost snapshot (total, per-task, projected monthly) |
| Q4 | Glance | Throughput sparkline |
| Q5 | Glance | Latest activity feed |
| Q6 | Investigation | Selected agent status |
| Q7 | Investigation | Selected agent performance |
| Q8 | Investigation | Agent vs fleet comparison |
| Q9 | Investigation | Agent task type breakdown |
| Q10-16 | Investigation | Extended agent analysis |
| Q17 | Optimization | Cost leaderboard |
| Q18 | Optimization | Cost per task ranking |
| Q19 | Optimization | Model cost comparison |
| Q20 | Optimization | Token efficiency |
| Q21 | Optimization | Slowest tasks |
| Q22 | Optimization | Error hotspots |
| Q23 | Optimization | Retry analysis |
| Q24 | Optimization | Action performance ranking |
| Q25 | Optimization | Idle time analysis |
| Q26 | Optimization | Queue health |
| Q27-28 | Optimization | Smart detectors |
| Q29-35 | Review | Metric summaries |
| Q36 | Review | Trend summary with verdict |
| Q37 | Review | Health gauge (0-100 score) |
| Q38 | Review | Scale readiness checklist |

---

## 8. Data Sources

The Insights page pulls from seven API endpoints:

| Endpoint | Used by |
|---|---|
| `GET /v1/agents` | Q1, Q2, Q6, Q37, Q38, Smart Detectors |
| `GET /v1/metrics` | Q2, Q3, Q4, Q7, Q8, Q29-Q35, Q36, Q37, Q38 |
| `GET /v1/cost` | Q3, Q17, Q18, Q19, Q20 |
| `GET /v1/events` | Q5, Q38 |
| `GET /v1/pipeline` | Q13, Q26, Q37, Q38, Smart Detectors |
| `GET /v1/tasks` | Q9, Q21 |
| `GET /v1/insights/*` | Q10-Q12, Q14-Q16, Q22-Q24 |

All data is fetched on page load and when the Refresh button is clicked. The range selector controls the time window for all time-scoped queries.

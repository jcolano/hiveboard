# HiveBoard Dashboard — Design Context for the Redesign

**Audience:** Design team working on the dashboard redesign
**Date:** 2026-02-12

---

## 1. What Is HiveBoard?

HiveBoard is a real-time observability dashboard for AI agents. Think of it as a mission control room where operators watch a fleet of autonomous agents work — processing leads, handling support tickets, managing pipelines, analyzing data.

These agents are software. They run continuously, pick up work from queues, call LLMs (large language models) to reason, execute tools (search a CRM, send an email, query a database), and sometimes need human help. They can get stuck, fail, run up costs, or fall behind.

**HiveBoard's job is to answer: what are my agents doing right now, is anything wrong, and where is the money going?**

---

## 2. Who Uses It?

### Primary user: The Operator

The operator is someone responsible for keeping the agents running smoothly. They might be:

- An **engineering lead** who deployed 5 agents and needs to know if they're healthy
- An **ops manager** who oversees agent-driven workflows (lead processing, support triage)
- A **developer debugging** why an agent made a bad decision on a specific task

They are technical enough to understand tokens, models, and API calls, but they don't want to read logs. They want visual, scannable, at-a-glance answers.

### Usage patterns

| Pattern | Frequency | What they need |
|---------|-----------|----------------|
| **Glance check** | Every few minutes | "Are all agents green? Any red?" — 2-second scan |
| **Investigation** | When something goes wrong | "Agent X is stuck — what was it doing? What failed?" — drill into one agent's timeline |
| **Cost review** | Daily or weekly | "How much did we spend? Which model costs the most?" — Cost Explorer |
| **Post-mortem** | After an incident | "Task Y failed — show me every step, every LLM call, every tool result" — deep timeline |

---

## 3. The Data Model (What We Measure)

Everything flows from a single concept: **events**. Agents emit events as they work. HiveBoard ingests, stores, and visualizes these events.

### 3.1 The hierarchy

```
Tenant (organization)
  └── Project (workspace — e.g., "sales-pipeline", "support-triage")
       └── Agent (a running instance — e.g., "lead-qualifier", "support-bot")
            └── Task (a unit of work — e.g., "process lead #4801")
                 └── Events (what happened during the task)
```

### 3.2 Event types — what agents report

There are 13 event types. Each tells a different part of the story:

| Event type | What it means | Example |
|------------|--------------|---------|
| `agent_registered` | Agent came online | "lead-qualifier started up" |
| `heartbeat` | Agent is still alive | Every 30 seconds — "I'm here" |
| `task_started` | Agent began a unit of work | "Processing lead #4801" |
| `task_completed` | Work finished successfully | "Lead #4801 routed to sales rep" |
| `task_failed` | Work failed | "Lead #4801 failed: CRM API timeout" |
| `action_started` | Agent is executing a tool | "Calling crm_search..." |
| `action_completed` | Tool execution finished | "crm_search returned 3 results (0.8s)" |
| `action_failed` | Tool execution failed | "crm_search failed: ConnectionError" |
| `custom` | Rich payload event | LLM calls, plans, queue snapshots, issues, etc. |
| `escalated` | Agent handed off to a human | "Score too low — needs manual review" |
| `approval_requested` | Agent waiting for permission | "Need approval for $500 credit" |
| `approval_received` | Human approved or rejected | "Credit approved by support-lead" |
| `retry_started` | Agent retrying after failure | "Retry #2: CRM API timeout" |

### 3.3 Payload kinds — the "custom" event subtypes

The `custom` event type carries a `kind` field that specifies what it actually is:

| Kind | What it carries | Why it matters |
|------|----------------|----------------|
| `llm_call` | Model name, tokens in/out, cost, duration | Cost tracking, LLM performance |
| `plan_created` | Goal + list of step descriptions | Shows the agent's strategy |
| `plan_step` | Step index, action (started/completed/failed) | Progress tracking |
| `queue_snapshot` | Queue depth, items, oldest age | Workload visibility |
| `todo` | Work item lifecycle (created/completed/failed) | Agent's internal task list |
| `issue` | Severity, category, occurrence count | Persistent problem reporting |
| `scheduled` | Next run time, interval, last status | Recurring work visibility |

### 3.4 Agent status — derived, not reported

Agents don't report their own status. The server **derives** it from recent events:

| Status | How it's determined | Visual priority |
|--------|--------------------|----|
| **Stuck** | Heartbeat older than 5 minutes + was recently processing | Highest — red, glowing |
| **Error** | Most recent task failed | High — red |
| **Waiting** | Approval requested but not yet received | Medium — amber |
| **Processing** | Has an active task | Normal — blue |
| **Idle** | No active task, heartbeat is recent | Low — gray |

The sort order in the UI should match urgency: stuck and error agents float to the top. An operator scanning the agent list should see problems first.

---

## 4. The Dashboard — Component by Component

### 4.1 Top Bar

**Purpose:** Global context and navigation.

| Element | What it shows | Why |
|---------|--------------|-----|
| **Logo** | "HiveBoard" branding | Identity |
| **Workspace badge** | Current project name | Operators may manage multiple projects |
| **View tabs** | "Mission Control" and "Cost Explorer" | Two primary workflows |
| **Connection indicator** | Green dot = WebSocket connected, red = disconnected | Operators need to trust the data is live |
| **Environment selector** | Production / Staging dropdown | Filter everything by deployment environment |

### 4.2 The Hive (Left Panel)

**Purpose:** Fleet-level awareness. "Which agents exist and what state are they in?"

This is the first thing the operator sees. It answers:
- **How many agents are running?**
- **Are any in trouble?** (stuck, error — should glow or stand out)
- **What's each one doing right now?**

**Each agent card shows:**

| Element | What it shows | Question it answers |
|---------|--------------|-------------------|
| **Agent name** | The agent's ID (e.g., "lead-qualifier") | "Which agent is this?" |
| **Status badge** | Processing / Stuck / Error / Idle / Waiting | "Is it healthy?" |
| **Type label** | Agent type (e.g., "sales", "support") | "What kind of agent?" |
| **Heartbeat indicator** | "12s ago" with green/amber/red dot | "Is it still alive?" |
| **Queue badge** | "Q:4" (blue) or "Q:8" (amber if >5) | "How much work is queued?" |
| **Issue badge** | "● 1 issue" (red) | "Has it reported problems?" |
| **Current task** | Clickable task ID | "What's it working on now?" |
| **Sparkline** | 12-bar mini chart of recent activity | "Has it been busy or idle?" |
| **Attention badge** (panel header) | "2 ⚠" count of stuck+error agents | "Do any agents need me?" |

**Interactions:**
- Click an agent → filters tasks and stream to that agent
- Double-click → opens Agent Detail view
- Click a status stat in the summary bar → filters the agent list by that status

**Design considerations:**
- Agents sort by urgency (stuck first, then error, then waiting, then processing, then idle)
- Stuck/error agents should visually demand attention (current: red glow border)
- The panel should work well with 3 agents or 30 agents
- An empty state is possible: "No agents match filter"

### 4.3 Stats Ribbon (Top of Center Panel)

**Purpose:** Fleet-wide vital signs at a glance. One row of numbers.

| Stat | What it shows | When to worry |
|------|--------------|---------------|
| **Total Agents** | Count of all registered agents | If it drops unexpectedly |
| **Processing** | Agents currently working (blue) | Normal operations |
| **Waiting** | Agents waiting for human approval (amber) | If this stays high, approvals are bottlenecked |
| **Stuck** | Agents that stopped heartbeating (red) | Any non-zero value needs attention |
| **Errors** | Agents whose last task failed (red) | Investigate immediately |
| **Success Rate (1h)** | % of tasks that completed vs failed in last hour (green) | Below 90% is concerning |
| **Avg Duration** | Mean task duration in the last hour | Sudden increases mean something is slow |
| **Cost (1h)** | Total LLM spend in the last hour (purple) | Budget awareness |

**Interactions:**
- Clicking Processing / Waiting / Stuck / Errors filters the Hive to show only agents in that state
- Active filter shows a highlight on the clicked stat

### 4.4 Mini-Charts (Below Stats Ribbon)

**Purpose:** Trend lines for key metrics over the last hour. "Are things getting better or worse?"

Four mini bar charts:

| Chart | What it shows | Why |
|-------|--------------|-----|
| **Throughput (1h)** | Tasks completed per time bucket | "Is work flowing?" |
| **Success Rate** | % success per time bucket (0-100 scale) | "Is reliability stable?" |
| **Errors** | Error count per time bucket | "Are errors spiking?" |
| **LLM Cost/Task** | Average LLM cost per task per bucket | "Is cost per task stable?" |

These are intentionally tiny — not for detailed analysis, just for spotting trends and spikes.

### 4.5 Timeline (Center Panel — Main Feature)

**Purpose:** The forensic view. "What exactly did this agent do, step by step, in this task?"

The timeline is a horizontal sequence of nodes (dots connected by lines), each representing an event. It reads left to right, oldest to newest.

**Node types and their colors:**

| Node type | Color | Shape | What it represents |
|-----------|-------|-------|-------------------|
| **System** | Gray | Hollow dot | Task started/lifecycle events |
| **Action** | Blue | Hollow dot | Tool execution (crm_search, send_email, etc.) |
| **LLM** | Purple | Filled diamond | LLM API call — shows model badge above |
| **Success** | Green | Filled dot | Task completed successfully |
| **Error** | Red | Filled dot | Task or action failed |
| **Warning** | Amber | Hollow dot | Escalation |
| **Human** | Green | Hollow dot | Approval request/response |
| **Retry** | Amber | Small dot, branching | Retry attempt — branches off the main track |

**Anatomy of a timeline node:**
```
     [model badge]        ← only for LLM nodes (e.g., "claude-sonnet-4-5")
     crm_search           ← label (tool name, event type, or summary)
         ●                ← colored dot
     14:23:05.123         ← timestamp
         ──── 0.8s ────   ← connector line with duration to next node
```

**Plan overlay (above the timeline):**

When a task has a plan, a progress bar appears above the timeline:
```
[■ Search CRM] [■ Score lead] [▪ Generate email] [  Update CRM  ]
   completed      completed      in progress        not started
```

Step colors: green = completed, blue = in progress, red = failed, gray = not started. Hover shows the step description.

**This answers:**
- "What was the sequence of operations?"
- "Which tool failed?"
- "How long did each step take?"
- "Where in the plan did it get stuck?"
- "Which LLM model was used and when?"
- "Were there retries? How many?"

**Interactions:**
- Click a node → opens the detail panel below with all payload data
- Timeline auto-scrolls to the right (newest) as new events arrive
- Manual scroll disengages auto-scroll; scrolling back to the end re-engages it

**Detail panel (below timeline, appears when a node is clicked):**

Shows all the data attached to that event:
- Event type
- Duration
- All payload fields (model, tokens, cost, error message, action args, result preview, etc.)
- Tags if any

This is the deepest level of detail — the operator clicks a node to understand exactly what happened at that moment.

### 4.6 Task Table (Below Timeline)

**Purpose:** List of recent tasks with key metrics. "What work has been done?"

| Column | What it shows | Why |
|--------|--------------|-----|
| **Task ID** | Clickable identifier | Click to load timeline for this task |
| **Agent** | Which agent ran it | Cross-reference with Hive |
| **Type** | Task category (e.g., "lead_processing") | Categorization |
| **Status** | Colored dot + label (completed/failed/processing) | Quick pass/fail scan |
| **Duration** | How long the task took | Performance tracking |
| **LLM** | Diamond icon + call count (e.g., "◆ 3") | LLM usage per task |
| **Cost** | Dollar amount (e.g., "$0.12") | Cost per task |
| **Time** | Relative timestamp ("2m ago") | Recency |

**Interactions:**
- Click a row → selects the task, loads its timeline above
- If an agent is selected in the Hive, the table filters to that agent's tasks

### 4.7 Activity Stream (Right Panel)

**Purpose:** Real-time event feed. "What's happening right now across all agents?"

A chronological list of events, newest at top, updating live via WebSocket.

**Each event row shows:**
- Kind icon (◆ for LLM, ⚑ for issue, ⊞ for queue, ☐ for todo, ⏲ for scheduled)
- Summary text (tool name, event description)
- Agent ID
- Task ID (if applicable)
- Relative timestamp ("just now", "12s ago")
- Severity color indicator

**Filters (chip bar at top):**

| Filter | What it shows | Use case |
|--------|--------------|----------|
| **all** | Everything | Default view |
| **task** | Task lifecycle events only | "Which tasks started/completed/failed?" |
| **action** | Tool executions only | "What tools are being called?" |
| **error** | Error-severity events only | "What's failing?" |
| **llm** | LLM calls only | "What LLM calls are happening?" |
| **pipeline** | Queue snapshots, TODOs, issues, scheduled | "What's the operational state?" |
| **human** | Approval requests and responses | "What needs human attention?" |

**Interactions:**
- Clicking an agent name in a stream event filters to that agent
- When an agent is selected in the Hive, the stream filters to that agent's events
- The "Live" badge pulses to indicate the WebSocket connection is active

### 4.8 Agent Detail View (Replaces Center Panel When Opened)

**Purpose:** Deep dive into a single agent. Two tabs: Tasks and Pipeline.

#### Tasks Tab

Same as the main task table, but filtered to this agent. Shows only this agent's tasks.

#### Pipeline Tab

**Purpose:** The agent's operational state — what's queued, what's broken, what's scheduled.

Four sections (each may be empty):

**Active Issues:**

| Column | What it shows |
|--------|--------------|
| Issue summary | What's wrong ("CRM API returning 403") |
| Severity | Badge: critical (red), high (orange), medium (yellow), low (gray) |
| Category | Classification (permissions, connectivity, rate_limit, etc.) |
| Occurrences | How many times this has happened (e.g., "×3") |

**Queue:**

| Column | What it shows |
|--------|--------------|
| Item count header | "4 items" badge |
| ID | Item identifier |
| Priority | Badge: urgent, high, normal, low |
| Source | Where it came from (human, webhook, scheduled, agent) |
| Summary | What the item is about |

When empty: "Queue is empty — agent is caught up"

**Active TODOs:**

| Column | What it shows |
|--------|--------------|
| Summary | What needs to be done |
| Priority | Badge: high, normal, low |
| Source | What created the TODO |

**Scheduled:**

| Column | What it shows |
|--------|--------------|
| Name | Scheduled job name |
| Next run | When it will run next |
| Interval | How often (1h, daily, weekly) |
| Status | Last run result (success/failure) |

### 4.9 Cost Explorer (Separate View)

**Purpose:** "Where is the money going?" Accessed via the "Cost Explorer" tab in the top bar.

**Cost Ribbon (top):**

Four summary stats:

| Stat | What it shows |
|------|--------------|
| **Total Cost** | Total LLM spend in the time range |
| **LLM Calls** | Total number of LLM API calls |
| **Tokens In** | Total input tokens (formatted as "125.3K") |
| **Tokens Out** | Total output tokens |

**Cost Tables (below):**

Two breakdown tables side by side:

| Table | Groups by | What it answers |
|-------|----------|----------------|
| **Cost by Model** | LLM model name | "Which model costs the most?" |
| **Cost by Agent** | Agent ID | "Which agent spends the most?" |

Each row shows: name, total cost, call count, percentage of total.

---

## 5. Information Architecture — What Questions Each View Answers

### At a glance (2-second scan)

| Question | Where to look |
|----------|--------------|
| "Is everything OK?" | Stats Ribbon — all zeros in Stuck/Errors = good |
| "Any agents need me?" | Hive panel — attention badge ("2 ⚠") |
| "Is work flowing?" | Mini-charts — throughput bars are non-zero |
| "Is anything happening right now?" | Activity Stream — events appearing, "Live" badge active |

### Fleet-level (30-second review)

| Question | Where to look |
|----------|--------------|
| "Which agents are struggling?" | Hive — sorted by urgency, stuck/error glow at top |
| "Are approvals backed up?" | Stats Ribbon — Waiting count |
| "Is success rate dropping?" | Mini-chart — Success Rate trend |
| "What's the cost trend?" | Mini-chart — LLM Cost/Task |
| "What events just happened?" | Activity Stream — newest events at top |

### Agent-level (investigating one agent)

| Question | Where to look |
|----------|--------------|
| "What tasks has this agent done?" | Agent Detail → Tasks tab |
| "Does it have pending work?" | Agent Detail → Pipeline tab → Queue section |
| "Has it reported problems?" | Agent Detail → Pipeline tab → Active Issues |
| "What's it doing right now?" | Agent card → current task link → timeline |
| "Is its heartbeat stale?" | Agent card → heartbeat indicator (green/amber/red dot) |

### Task-level (debugging one task)

| Question | Where to look |
|----------|--------------|
| "What steps did it take?" | Timeline — nodes left to right |
| "What was the plan?" | Plan bar above timeline |
| "Which step failed?" | Plan bar — red segment; Timeline — red node |
| "Which tools were called?" | Timeline — blue action nodes with tool names |
| "Which LLM was used?" | Timeline — purple LLM nodes with model badge |
| "How long did each step take?" | Timeline — duration on connector lines |
| "Were there retries?" | Timeline — branching retry nodes |
| "Was it escalated?" | Timeline — amber escalation node |
| "What's in this event's payload?" | Click a timeline node → detail panel |

### Cost-level

| Question | Where to look |
|----------|--------------|
| "How much did we spend today?" | Cost Explorer → Total Cost |
| "Which model costs the most?" | Cost Explorer → Cost by Model table |
| "Which agent costs the most?" | Cost Explorer → Cost by Agent table |
| "How many LLM calls were made?" | Cost Explorer → LLM Calls stat |

---

## 6. Real-Time Updates

The dashboard stays live through two mechanisms:

1. **WebSocket** — the server pushes new events as they happen. The Activity Stream updates instantly, agent cards update status, and the timeline appends new nodes.

2. **Polling** — every 30 seconds, the dashboard re-fetches agents, tasks, and metrics to catch anything missed.

**Implications for the design:**
- Elements must handle dynamic updates without jarring layout shifts
- New events in the Activity Stream should animate in (current: fade-in)
- Agent status changes should be noticeable but not distracting
- The timeline auto-scrolls right as new nodes appear (unless the user has scrolled left to investigate)
- The connection indicator must be clearly visible — if WebSocket disconnects, the operator needs to know the data is stale

---

## 7. Visual Language — Current Conventions

These are the conventions currently in use. The redesign may change them, but this is the starting vocabulary:

### Colors

| Color | Meaning | Used for |
|-------|---------|----------|
| **Blue** (`--active`) | Active/working | Processing status, action nodes, throughput charts |
| **Green** (`--success`) | Success/healthy | Completed tasks, success rate, fresh heartbeat |
| **Red** (`--error`) | Failure/problem | Failed tasks, errors, stuck agents, issues |
| **Amber** (`--warning`) | Needs attention | Waiting for approval, escalations, stale heartbeat |
| **Purple** (`--llm`) | LLM-related | LLM call nodes, LLM cost chart, cost stats |
| **Gray** (`--idle`) | Inactive/not started | Idle agents, pending plan steps, system events |

### Typography

| Context | Font |
|---------|------|
| UI text (labels, body) | DM Sans |
| Data values (IDs, code, timestamps) | JetBrains Mono |

### Iconography

| Icon | Meaning |
|------|---------|
| ◆ | LLM call |
| ⚑ | Issue |
| ⊞ | Queue snapshot |
| ☐ | TODO item |
| ⏲ | Scheduled work |
| ⬡ | Empty state / brand hexagon |

---

## 8. Edge Cases and Empty States

The design must handle these gracefully:

| Scenario | What happens |
|----------|-------------|
| **No agents** | Hive shows "No agents match filter" with hex icon |
| **No tasks** | Task table shows "No tasks" placeholder |
| **No timeline** | Timeline area shows "No timeline data" with clock icon |
| **No events** | Activity Stream shows "No events match filters" |
| **No pipeline data** | Pipeline tab shows "No pipeline data for this agent" |
| **WebSocket disconnected** | Connection dot turns red, text shows "Disconnected" |
| **Agent has no current task** | Agent card omits the task row |
| **Agent has no issues or queue** | Pipeline sections simply don't render (collapse) |
| **Queue is empty** | Shows "Queue is empty — agent is caught up" (positive message) |
| **Heartbeat lost** | Dot goes red, text shows "—" or "5m ago" |
| **Very long agent/task IDs** | Need truncation with tooltip for full value |
| **30+ agents** | Hive panel must scroll; summary counts still visible |
| **Cost is $0** | Show "—" rather than "$0.00" |

---

## 9. User Flows

### Flow 1: Morning check-in
1. Open HiveBoard
2. Scan Stats Ribbon → all green, no stuck/errors → done (5 seconds)
3. If Stuck > 0 → click "Stuck" stat to filter → see which agents → click one → check heartbeat → investigate

### Flow 2: Agent is stuck
1. See red glow on agent card in Hive
2. Click agent → tasks filter to that agent
3. See the latest task is stuck (processing, no heartbeat)
4. Click the task → timeline loads
5. Read timeline left to right → see where it stopped
6. Click the last node → see error details in the detail panel
7. Act: restart agent, fix the external service, etc.

### Flow 3: Task failed — why?
1. See a failed task in the Task Table (red dot)
2. Click it → timeline loads
3. See the plan bar: step 3 is red ("Email API returned 403")
4. See the timeline: blue action nodes for steps 1-2, then a red node at step 3
5. Click the red node → see the error: "ConnectionError: smtp.example.com refused"
6. Look for retries → see retry branch with 2 attempts before final failure
7. Check Activity Stream → "pipeline" filter → see if agent reported an issue

### Flow 4: Cost review
1. Click "Cost Explorer" tab
2. See Total Cost: $47.23 in the last hour
3. See Cost by Model: claude-sonnet-4-5 = $38.50 (82%)
4. See Cost by Agent: "lead-qualifier" = $29.00 (62%)
5. Decision: investigate why lead-qualifier is expensive → switch back to Mission Control → click lead-qualifier → check LLM call counts per task

### Flow 5: Watching a live agent
1. Click an agent that's currently processing
2. Click its current task
3. Timeline shows nodes appearing in real-time as the agent works
4. Activity Stream on the right shows each event as it happens
5. Plan bar fills in as steps complete
6. When the task completes, the last node turns green

---

## 10. Glossary

| Term | Definition |
|------|-----------|
| **Agent** | An autonomous software program that performs work. Not a human — a running process. |
| **Task** | A discrete unit of work an agent performs. Has a start, an end, and a result. |
| **Action** | A tool execution within a task (e.g., search a database, send an email). |
| **LLM Call** | A call to a language model API (e.g., Claude). Costs money, uses tokens. |
| **Token** | The unit LLMs use to measure input/output length. ~4 characters per token. |
| **Heartbeat** | A periodic "I'm alive" signal from the agent. Default: every 30 seconds. |
| **Stuck** | An agent that stopped sending heartbeats while it was supposed to be working. |
| **Escalation** | When an agent decides it can't handle something and hands it off to a human. |
| **Approval** | When an agent pauses to wait for human permission before proceeding. |
| **Pipeline** | An agent's operational state: its queue, issues, TODOs, and scheduled work. |
| **Issue** | A persistent problem an agent has detected (not a one-time failure). |
| **Queue depth** | How many work items are waiting for the agent to process. |
| **Plan** | A multi-step strategy the agent creates before executing. Shown as a progress bar. |
| **Tenant** | The organization. All agents/tasks/events belong to a tenant. |
| **Project** | A workspace within a tenant (e.g., "sales-pipeline"). Groups related agents. |
| **Environment** | Deployment context: production, staging, development. |
| **Severity** | Event importance: debug, info, warn, error. |
| **WebSocket** | A persistent connection for real-time event delivery. |
| **Payload** | The data attached to an event (model name, token counts, error message, etc.). |

---

## 11. Key Design Principles (from how operators actually use it)

1. **Problems should be impossible to miss.** Stuck and error states must be visually loud. An operator glancing at the screen for 2 seconds should see red if something is wrong.

2. **Normal operations should be calm.** When everything is healthy, the dashboard should feel quiet and orderly. Blue and green, steady rhythms, no visual noise.

3. **Drill-down, not drill-around.** The information hierarchy flows: fleet → agent → task → event. Each click narrows focus. The user should never need to navigate "sideways" to understand what they're looking at.

4. **Time reads left to right.** The timeline is the forensic core. Oldest on the left, newest on the right. The story reads like a sentence.

5. **Live data must feel live.** The operator needs to trust that what they see is current. The connection indicator, the "Live" badge, and smooth real-time updates build that trust.

6. **Cost is a first-class metric.** LLM calls cost real money. Cost visibility is not an afterthought — it's surfaced at every level (stats ribbon, task table, timeline nodes, dedicated Cost Explorer).

7. **Empty states are part of the design.** Many sections will often be empty (no issues, empty queue, no scheduled work). These should feel intentional, not broken. "Queue is empty — agent is caught up" is a positive signal, not a missing feature.

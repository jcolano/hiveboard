# HiveBoard — User Manual Part 4: Layer 1 Integration — What to Expect

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *You've added `agent.task()` and `@agent.track()`. Here's what shows up, what doesn't, and how to read it.*

---

## Table of Contents

1. [What Layer 1 Gives You](#1-what-layer-1-gives-you)
2. [Your First Task on the Dashboard](#2-your-first-task-on-the-dashboard)
3. [Reading the Task Table](#3-reading-the-task-table)
4. [Reading the Timeline](#4-reading-the-timeline)
5. [Reading the Activity Stream with Tasks](#5-reading-the-activity-stream-with-tasks)
6. [Reading the Stats Ribbon with Tasks](#6-reading-the-stats-ribbon-with-tasks)
7. [The Detail Panel](#7-the-detail-panel)
8. [What You Don't See Yet (and Why)](#8-what-you-dont-see-yet-and-why)
9. [Common Patterns at Layer 1](#9-common-patterns-at-layer-1)
10. [Troubleshooting Layer 1](#10-troubleshooting-layer-1)
11. [Deciding When to Add Layer 2](#11-deciding-when-to-add-layer-2)

---

## 1. What Layer 1 Gives You

Layer 0 told you whether your agents were alive. Layer 1 tells you **what they're doing**.

With `agent.task()` and `@agent.track()` in place, the dashboard transitions from a health monitor to an operational monitor. Here's the before and after:

| Dashboard element | Layer 0 (before) | Layer 1 (now) |
|------------------|-------------------|---------------|
| **Agent cards** | Name, type, heartbeat | + current task name, PROCESSING badge while working |
| **Stats Ribbon** | Total Agents, Stuck | + Processing count, Success Rate, Avg Duration, Errors |
| **Mini-Charts** | Flat/empty | Throughput and Success Rate bars populate |
| **Task Table** | "No tasks" | Rows for every task — ID, agent, type, status, duration |
| **Timeline** | "No timeline data" | Task lifecycle nodes with timestamps and durations |
| **Activity Stream** | `agent_registered` only | + `task_started`, `task_completed`, `task_failed`, `action_started`, `action_completed` |
| **Cost Explorer** | All zeros | Still zeros (needs Layer 2 `task.llm_call()`) |

### What questions Layer 1 answers

- What task is each agent working on right now?
- How long do tasks take?
- What percentage succeed vs fail?
- When did a task start and finish?
- What actions happened inside a task?
- Which tasks are still running?
- What's my throughput over the last hour?
- Is task duration stable or increasing?

---

## 2. Your First Task on the Dashboard

After adding `agent.task()` and triggering a task, here's what you should see:

### Agent card changes

The agent card in The Hive reflects task activity:

```
┌─────────────────────────────────┐
│ my-agent               PROCESSING │  ← was IDLE, now PROCESSING
│ general  ● 2s ago                  │
│ ↳ my-agent-evt_a1b2c3              │  ← current task ID appears
│ ▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪                  │
└─────────────────────────────────┘
```

When the task completes, the badge returns to **IDLE** and the task link disappears. If the task fails, the badge shows **ERROR**.

### Stats Ribbon updates

During task execution:
- **Processing** count goes from 0 to 1 (blue)
- After completion: Processing returns to 0
- **Success Rate** updates based on the outcome
- **Avg Duration** reflects the task's duration

### Activity Stream events

You'll see a pair of events for every task:

```
● task_started         just now
  my-agent > my-agent-evt_a1b2c3
  Task my-agent-evt_a1b2c3 started

● task_completed       just now
  my-agent > my-agent-evt_a1b2c3
  Task my-agent-evt_a1b2c3 completed
```

If you've added `@agent.track()` on functions inside the task, you'll also see `action_started` / `action_completed` pairs between them.

### Task Table row

A new row appears:

| TASK ID | AGENT | TYPE | STATUS | DURATION | LLM | COST | TIME |
|---------|-------|------|--------|----------|-----|------|------|
| my-agent-evt_a1b2 | my-agent | heartbeat | ● completed | 27.5s | — | — | 1m ago |

- **TASK ID** — the ID you passed to `agent.task()`. Clickable — loads the timeline.
- **TYPE** — the `type` parameter from `agent.task()` (e.g. `"heartbeat"`, `"webhook"`, `"task"`)
- **STATUS** — `completed` (green dot), `failed` (red dot), or `processing` (blue dot, animated)
- **DURATION** — total time from task start to task end
- **LLM** — shows "—" until you add Layer 2 `task.llm_call()`
- **COST** — shows "—" until you add Layer 2 `task.llm_call()`

### Timeline

Click the task row and the Timeline loads:

```
TIMELINE  my-agent-evt_a1b2  ⏱ 27.5s  🤖 my-agent  ✓ completed

   main started              27.5s                    main completed
       ●────────────────────────────────────────────────────●
   19:22:37.569                                      19:23:05.023
```

At Layer 1 with just `agent.task()` and no tracked actions, you'll see **two nodes** — task started and task completed — connected by a line showing the total duration. This is the task's "bookends." The space between them is where action nodes will appear as you add `@agent.track()` to functions.

---

## 3. Reading the Task Table

The Task Table is your primary navigation surface. Here's how to read it effectively.

### Task density

Each row is one task execution. Over time, this table fills with your agent's work history. A healthy system shows steady rows with `completed` status and consistent durations.

**What to scan for:**

| Pattern | What it means |
|---------|--------------|
| All rows show `completed` with similar durations | System is healthy and consistent |
| Duration varies widely (5s, 45s, 5s, 90s) | Some tasks are hitting slow paths — investigate the outliers |
| A row shows `processing` for a long time | Task may be stuck — check the agent's heartbeat |
| Several `failed` rows in a row | Something broke — click one to see the timeline |
| Rows only show 1-2 tasks total | Task IDs may not be unique (see Troubleshooting, Section 10.1) |

### Filtering by agent

Click an agent name in the AGENT column to filter the entire dashboard to that agent. The Task Table, Timeline, and Activity Stream all scope to the selected agent. Click "Clear" in the filter bar to return to the full view.

### Task types

The TYPE column shows the `type` parameter from `agent.task()`. Use this to categorize work:

| Type | Typical meaning |
|------|----------------|
| `heartbeat` | Periodic scheduled task (triggered by heartbeat/timer) |
| `webhook` | Triggered by an external webhook |
| `human` | Triggered by a human/operator |
| `task` | Generic task type |
| `api` | Triggered by an API call |

Choose type values that make sense for your system. They help you filter and understand what triggered each task.

---

## 4. Reading the Timeline

The Timeline is a visual story of what happened inside a single task.

### 4.1 The bookend pattern (task only, no tracked actions)

When you've added `agent.task()` but haven't yet added `@agent.track()` to functions inside the task, the timeline shows two nodes:

```
  [task started] ─────── 27.5s ─────── [task completed]
```

This tells you:
- The task ran ✓
- It took 27.5 seconds ✓
- It completed successfully ✓

What it doesn't tell you: what happened during those 27.5 seconds. That's what `@agent.track()` adds.

### 4.2 With tracked actions

Once you decorate functions with `@agent.track()`, intermediate nodes appear:

```
  [started] ── [fetch_data] ── [score_lead] ── [route_lead] ── [completed]
    0.0s    0.2s    8.4s         1.2s    12.1s         0.1s      22.0s
```

Each tracked function becomes a node. Connectors show the duration between events. Now you can see:
- Which function took the longest (the bottleneck)
- Where in the sequence something failed
- Whether work is happening in the expected order

### 4.3 Timeline auto-scroll

The timeline scrolls horizontally and **auto-scrolls to the most recent events** (rightmost position). This means:

- When you select a task, you see the latest events immediately
- During a live task, new nodes scroll into view automatically
- If you scroll left to inspect earlier events, auto-scroll pauses — the timeline stays where you put it
- Scrolling back to the right edge re-enables auto-scroll

### 4.4 Clicking a timeline node

Click any node to **pin** its detail panel below the timeline. The detail panel shows:

```
● Task ag_6ce5uncd comp...  19:23:40.375
  event    task_completed    duration    26.9s
```

Fields shown depend on the event type. For task events, you see event type and duration. For action events (Layer 1b), you'll see the function name and execution time. For LLM calls (Layer 2), you'll see model, tokens, and cost.

Click "✕ Close" to dismiss the detail panel.

---

## 5. Reading the Activity Stream with Tasks

With Layer 1, the Activity Stream becomes much more active. Here's what to expect.

### 5.1 Event patterns

A healthy task lifecycle produces this pattern:

```
● task_started       my-agent > task-123      just now
● task_completed     my-agent > task-123      just now
```

A task with tracked actions:

```
● task_started       my-agent > task-123      just now
● action_started     my-agent > task-123      just now    (fetch_data)
● action_completed   my-agent > task-123      just now    (fetch_data)
● action_started     my-agent > task-123      just now    (score_lead)
● action_completed   my-agent > task-123      just now    (score_lead)
● task_completed     my-agent > task-123      just now
```

A failed task:

```
● task_started       my-agent > task-123      just now
● action_started     my-agent > task-123      just now    (fetch_data)
● action_failed      my-agent > task-123      just now    (fetch_data)  ← red
● task_failed        my-agent > task-123      just now                  ← red
```

### 5.2 Stream filters at Layer 1

With tasks flowing, more stream filters become useful:

| Filter | What you see |
|--------|-------------|
| **all** | Everything — heartbeats, tasks, actions |
| **task** | Only `task_started`, `task_completed`, `task_failed` — high-level work status |
| **action** | Only `action_started`, `action_completed`, `action_failed` — function-level detail |
| **error** | Only failure events — the stuff that needs attention |

**Tip:** During normal monitoring, use **task** filter. It gives you the rhythm of work without the noise. Switch to **all** or **action** when investigating a specific issue.

### 5.3 Clickable references

Every event in the stream has clickable elements:
- **Agent name** — click to filter the dashboard to that agent
- **Task ID** — click to load that task's timeline

This makes the stream a navigation tool, not just a log viewer.

---

## 6. Reading the Stats Ribbon with Tasks

With Layer 1 data flowing, the Stats Ribbon becomes an operational dashboard:

### 6.1 Key metrics at Layer 1

| Stat | What it means now | What to watch for |
|------|-------------------|-------------------|
| **Processing** | How many agents are actively working right now | Should fluctuate. If always 0, tasks are very short or infrequent. If never drops, tasks may be getting stuck |
| **Success Rate (1h)** | Percentage of tasks completed without failure | Establish your baseline (e.g. 95%). Drops below baseline = something changed |
| **Avg Duration** | Mean task execution time over the last hour | Establish your baseline (e.g. 28s). Sudden increase = agents are slowing down |
| **Errors** | Number of agents whose last task failed | Should be 0 during normal operation. Any number > 0 warrants investigation |

### 6.2 Mini-Charts

Two mini-charts become meaningful:

- **Throughput (1h)** — bars show tasks completed per time bucket. A steady pattern means consistent work. Gaps mean idle periods. Spikes mean burst activity.
- **Success Rate** — green bars per time bucket. Should be consistently full height. Dips indicate failure windows.

### 6.3 What's still empty

- **LLM Cost/Task** — flat line. Needs Layer 2 `task.llm_call()`.
- **Cost (1h)** — shows "—". Same reason.
- **Errors mini-chart** — flat if everything is succeeding. This is good.

---

## 7. The Detail Panel

Clicking a timeline node opens the detail panel. Here's what it shows at each integration level:

### 7.1 Task events (Layer 1)

Clicking a `task_started` or `task_completed` node:

```
● Task my-agent comp...  19:23:40.375
  event      task_completed
  duration   26.9s
```

This is minimal — just the event type and duration. Useful for confirming timing but not much detail.

### 7.2 Action events (Layer 1b — with @agent.track)

Clicking an action node:

```
● reflect  19:23:42.100
  event      action_completed
  action     reflect
  duration   4.2s
```

Shows which function ran and how long it took. If the action failed, the error message appears here.

### 7.3 What you'll see with Layer 2 (preview)

Once you add `task.llm_call()` and other rich events, clicking an LLM node will show:

```
◆ reasoning  19:23:43.500
  event       llm_call
  model       claude-sonnet-4-20250514
  tokens_in   1,240
  tokens_out  380
  cost        $0.0082
  duration    3.1s
```

And clicking a plan node:

```
● create_plan  19:23:44.200
  event       plan_created
  goal        Score and route incoming lead
  steps       3
```

That's the progression: Layer 1 gives you timing and structure. Layer 2 fills in the narrative.

---

## 8. What You Don't See Yet (and Why)

At Layer 1, certain dashboard elements are intentionally empty. This is not a bug — it's expected.

| Element | Shows | Why it's empty | What fills it |
|---------|-------|---------------|---------------|
| **LLM column** in Task Table | "—" | No `task.llm_call()` instrumented | Layer 2: add `task.llm_call()` after each LLM API call |
| **COST column** in Task Table | "—" | Same | Same |
| **Cost (1h)** in Stats Ribbon | "—" | No LLM cost data | Layer 2 |
| **LLM Cost/Task** mini-chart | Flat | No LLM cost data | Layer 2 |
| **Cost Explorer** view | All zeros | No LLM cost data | Layer 2 |
| **Plan progress bar** in Timeline | Hidden | No `task.plan()` calls | Layer 2: add `task.plan()` and `task.plan_step()` |
| **Pipeline tab** in Agent Detail | Empty | No `queue_provider`, `report_issue`, `todo`, `scheduled` | Layer 2: add pipeline instrumentation |
| **Escalation/approval events** | None in stream | No `task.escalate()` or `task.request_approval()` | Layer 2: add at escalation points |
| **LLM filter** in Activity Stream | No results | No LLM events | Layer 2 |
| **Pipeline filter** in Activity Stream | No results | No pipeline events | Layer 2 |
| **Human filter** in Activity Stream | No results | No approval/escalation events | Layer 2 |

This is by design. HiveLoop is incremental — each layer adds value without requiring the others. The dashboard gracefully shows what's available and hides what isn't.

---

## 9. Common Patterns at Layer 1

### 9.1 Healthy system

```
Stats Ribbon:  Processing: 0-1  |  Success Rate: 95-100%  |  Avg Duration: stable
Task Table:    Steady stream of completed rows, consistent durations
Timeline:      Clean start → actions → complete sequence
Activity:      Regular task_started / task_completed pairs
```

This is what normal looks like. Bookmark these numbers as your baseline.

### 9.2 Task duration creeping up

```
Stats Ribbon:  Avg Duration: was 28s, now 45s
Task Table:    Recent rows show longer durations than older ones
Timeline:      One connector between nodes is much longer than the rest
```

Something is getting slower. If you have tracked actions, the timeline tells you which step is the bottleneck. If you only have task bookends, you need to add `@agent.track()` to the main functions to find where the time goes.

### 9.3 Intermittent failures

```
Stats Ribbon:  Success Rate: dropped from 100% to 85%
Task Table:    Mix of completed and failed rows
Activity:      task_failed events appearing
```

Click a failed task row. Look at the timeline — which node is red? That's where it broke. Click the red node to see the error in the detail panel.

### 9.4 Agent stuck during a task

```
Agent card:    STUCK badge (red, blinking)
Task Table:    One row shows "processing" with a very long duration
Timeline:      Nodes stop at some point — no completed node
Activity:      Last event is action_started or task_started with no matching completion
```

The agent stopped mid-task. Check your agent process (logs, container health, network). The dashboard shows you *where* it stopped. Your infrastructure tells you *why*.

### 9.5 Tasks running but only bookends in timeline

```
Timeline:      [started] ─── 27s ─── [completed]   (no action nodes between)
Activity:      Only task_started / task_completed, no action events
```

This means either:
- `@agent.track()` hasn't been added yet — add decorators to key functions
- The tracked functions aren't being called in these particular tasks (e.g. heartbeat-triggered tasks that don't invoke reflection or planning)
- The decorators aren't wired correctly (see Troubleshooting, Section 10)

---

## 10. Troubleshooting Layer 1

### 10.1 Task Table shows only 1 row per agent (not per execution)

**Symptom:** Multiple tasks have run, Activity Stream shows many `task_started` / `task_completed` pairs, but the Task Table shows only one row per agent.

**Cause:** Task IDs are not unique per execution. If every task for agent "main" uses task ID "main", the table collapses them into one row.

**Fix:** Generate unique task IDs in your `agent.task()` call:
```python
# ❌ Reuses agent name as task ID:
agent.task(agent_id, project="my-project")

# ✅ Unique per execution:
agent.task(f"{agent_id}-{event_id}", project="my-project")
agent.task(f"{agent_id}-{uuid.uuid4().hex[:8]}", project="my-project")
```

### 10.2 Task Table says "No tasks"

**Symptom:** Activity Stream shows task events, Timeline renders, but Task Table is empty.

**Possible causes:**
1. **Environment filter mismatch** — the environment selector in the top bar may be set to a value that doesn't match your agent's environment. Try switching to the correct environment or check if an "all" option is available.
2. **Task events not reaching the query endpoint** — check the HiveBoard server logs for errors on the tasks query.
3. **Project mismatch** — events may be going to a different project than the dashboard is querying. Check the project filter if one is active.

### 10.3 No action events between task start and end

**Symptom:** Timeline shows only bookend nodes (started/completed). No action nodes in between.

**Possible causes:**
1. **`@agent.track()` not added** — you've added `agent.task()` but haven't decorated any functions with tracking. This is expected — add decorators to your key functions.
2. **Tracked functions not called** — the functions you decorated may not execute for every task type. For example, if you tracked `reflect()` and `create_plan()`, but the current task is a simple heartbeat that doesn't invoke those paths, no action events will appear. Trigger a task that exercises those paths.
3. **Decorator on class methods not wiring correctly** — see Part 3, Section 6 for patterns. The decorator may be applied to the wrong method or shadowed by a subclass override.
4. **`contextvars` not propagating** — if the tracked function runs in a different thread than the task context was set in, the context variable may be `None`. This causes the `if task:` guard to silently skip. Verify with a debug log inside the decorated function.

### 10.4 Task shows "processing" forever

**Symptom:** A task row shows `processing` status and the duration keeps growing, but the agent card shows IDLE.

**Cause:** The `agent.task()` context manager never exited cleanly — either:
- An exception was caught and swallowed without the context manager closing
- The `try/finally` block for `clear_current_task()` is missing
- The agent process crashed during the task (check logs)

**Fix:** Ensure `agent.task()` is used as a context manager (`with` statement) and `clear_current_task()` is in a `finally` block:
```python
with agent.task(task_id) as task:
    set_current_task(task)
    try:
        do_work()
    finally:
        clear_current_task()
```

### 10.5 Duration shows 0s for completed tasks

**Symptom:** Task completed but duration is 0s or negligibly small.

**Cause:** The `agent.task()` context is being opened and closed immediately without the actual work happening inside it. The work may be happening outside the `with` block.

**Fix:** Verify that all task work happens inside the `with agent.task()` block, not before or after it.

### 10.6 Events rejected with "invalid_project_id"

**Symptom:** HiveBoard server or SDK logs show `Batch partially rejected: 0 accepted, 1 rejected. Errors: [{'error': 'invalid_project_id', 'message': 'Project X not found'}]`

**Cause:** The `project` parameter in `agent.task()` references a project that doesn't exist on the server.

**Fix (option A):** Create the project on the server first:
```bash
curl -X POST http://localhost:8000/v1/projects \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"slug": "my-project", "name": "My Project"}'
```

**Fix (option B):** If auto-create is enabled on your HiveBoard instance (v0.2+), unknown project slugs are automatically created. Check your server version.

**Common mistake:** Using the agent name as the project (e.g. `project=agent_id`). The project is an organizational grouping — use something like `"my-app"` or `"sales-pipeline"`, not the agent name.

---

## 11. Deciding When to Add Layer 2

Layer 1 gives you operational visibility. Layer 2 gives you the full narrative. Here's when you need it:

### You're fine at Layer 1 if:

- You mainly need to know "are tasks running and succeeding?"
- Task durations are your main performance indicator
- You're in early development and just need basic health monitoring
- You have separate LLM cost tracking (e.g. provider dashboard)

### You need Layer 2 when:

| Trigger | What to add | Why |
|---------|------------|-----|
| "How much is this costing me?" | `task.llm_call()` | Without it, Cost Explorer is blank and you have no per-agent cost visibility |
| "The task failed but I don't know why" | `task.llm_call()` with prompt/response previews | The timeline shows *where* it failed, but not *what* the LLM returned |
| "The agent made a bad decision" | `task.plan()` + `task.plan_step()` | See the agent's reasoning, step by step |
| "Approvals are piling up" | `task.request_approval()` + `task.approval_received()` | Track the human-in-the-loop bottleneck |
| "The agent keeps retrying the same thing" | `task.retry()` | See retry patterns in the timeline |
| "I need to see the work queue" | `queue_provider` + `agent.todo()` | Pipeline tab and agent card enrichment |

### The incremental path

You don't need to add everything at once. The highest-value Layer 2 addition is `task.llm_call()` — it unlocks the Cost Explorer and is usually 5-10 lines of code. Start there, validate on the dashboard, then add narrative events as you need them.

See Part 3, Section 8 for the full Layer 2 implementation guide.

# loopCore — Implementation Guide: Layer 2 Rich Events

**Date:** 2026-02-12
**Status:** Ready for implementation
**Prerequisites:**
- Layer 0 (init + heartbeat) ✅ working
- Layer 1 (task context + decorators) ✅ working
- Layer 2a — `task.llm_call()` ✅ working
- `observability.py` module with `get_hb()`, `get_hiveloop_agent()`, `get_current_task()`, `set_current_task()` ✅ in place

---

## Overview

This document covers every remaining Layer 2 method — the rich narrative events that turn the HiveBoard timeline from "what happened" into "what happened, why, and what went wrong." Each section is self-contained. Implement them in any order.

### What's in this guide

| Method | Scope | What it answers | Dashboard impact |
|--------|-------|----------------|-----------------|
| `agent.report_issue()` | Agent-level | "What persistent problems exist?" | Pipeline tab → Active Issues, red badge on agent card |
| `agent.resolve_issue()` | Agent-level | "Has the problem been fixed?" | Issue clears from Pipeline tab |
| `task.plan()` | Task-level | "What's the agent's plan?" | Plan progress bar above Timeline |
| `task.plan_step()` | Task-level | "Which step is it on? Which failed?" | Colored segments on plan bar |
| `task.escalate()` | Task-level | "When did it need human help?" | Amber nodes in Timeline |
| `task.request_approval()` | Task-level | "What's waiting for approval?" | WAITING badge on agent card |
| `task.approval_received()` | Task-level | "Was it approved or rejected?" | Green/red nodes in Timeline |
| `task.retry()` | Task-level | "How many retries? Why?" | Retry nodes in Timeline |
| `agent.queue_snapshot()` | Agent-level | "How deep is the work queue?" | Queue badge on agent card (e.g. "Q:4") |
| `agent.todo()` | Agent-level | "What work items is the agent tracking?" | Pipeline tab → Active TODOs |
| `agent.scheduled()` | Agent-level | "What recurring work is configured?" | Pipeline tab → Scheduled |

### Two scopes: task vs. agent

This is the key architectural distinction for implementation:

**Task-level methods** (`task.plan()`, `task.escalate()`, etc.) require an active task context. They use `get_current_task()` and are guarded with `if _task:`. These events appear on the task's Timeline.

**Agent-level methods** (`agent.report_issue()`, `agent.queue_snapshot()`, etc.) operate on the agent handle directly. They use `get_hiveloop_agent()` and are guarded with `if _agent:`. These events appear in the Pipeline tab and Activity Stream but not on any task's Timeline.

```python
# Task-level pattern:
_task = get_current_task()
if _task:
    try:
        _task.plan(...)
    except Exception:
        pass

# Agent-level pattern:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    try:
        _agent.report_issue(...)
    except Exception:
        pass
```

### The safety contract

Every instrumentation point follows three rules:
1. **Guard with `if`** — never call a method on `None`
2. **Wrap in `try/except`** — HiveLoop should never crash the agent
3. **Keep it optional** — the agent must run identically without HiveLoop

---

## 1. Issue Reporting — `agent.report_issue()` / `agent.resolve_issue()`

### What it does

Surfaces **persistent, agent-level problems** that aren't visible as individual task failures. Task failures are transient ("this task broke"). Issues are persistent ("something in the environment is broken and it's affecting multiple tasks").

### API

```python
agent.report_issue(
    summary="CRM API returning 403 for workspace queries",   # required — stable text
    severity="high",                                          # required — "critical", "high", "medium", "low"
    category="permissions",                                   # optional — see list below
    issue_id="crm-403",                                       # optional — enables lifecycle tracking
    context={"tool": "crm_search", "error_code": 403},        # optional — debugging details
    occurrence_count=3,                                        # optional — if you're counting
)

# Later, when the issue is resolved:
agent.resolve_issue(
    summary="CRM API returning 403 for workspace queries",   # must match original
    issue_id="crm-403",                                       # if provided on report
)
```

**Categories:** `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"timeout"`, `"other"`

### Where to instrument in loopCore

Look for code that:
- Catches external API errors (CRM, email, calendar services)
- Detects rate limits (429 responses)
- Handles LLM API failures
- Logs warnings about degraded conditions
- Has circuit breaker or health check patterns

**The key question:** "Is this a problem that affects multiple tasks, not just the current one?" If yes → `report_issue()`. If it's a one-time failure → let `agent.task()` catch it automatically.

### Implementation pattern

```python
from loop_core.observability import get_hiveloop_agent

# In an error handler:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    try:
        _agent.report_issue(
            summary=f"Tool '{tool_name}' failing: {error_type}",
            severity="high",
            category="connectivity",
            issue_id=f"tool-{tool_name}-{error_type}",
            context={
                "tool": tool_name,
                "error_code": error_code,
                "error_message": str(error)[:200],
            },
        )
    except Exception:
        pass
```

### Summary stability — critical gotcha

The `summary` string is used for deduplication when `issue_id` is absent. The server hashes it. If you embed timestamps, request IDs, or other variable data in the summary, every occurrence looks like a new issue on the dashboard.

**Good:** `"CRM API returning 403 for workspace queries"` — stable, descriptive
**Bad:** `"CRM API failed at 2026-02-12T14:30:00Z with request abc123"` — unique every time

**Recommendation:** Always provide `issue_id` for issues you expect to recur. It's more reliable than matching summary strings and enables explicit resolution tracking.

### When to resolve

Call `agent.resolve_issue()` when you detect recovery — e.g., a previously-failing API call succeeds. If loopCore doesn't have explicit recovery detection, skip `resolve_issue()` for now. Issues are still useful on the dashboard without auto-resolution.

### Dashboard result

- Red issue badge on agent card: **● 1 issue**
- Pipeline tab → Active Issues table with severity, category, occurrence count
- Activity Stream → `issue` events with warning icons
- Issues persist until explicitly resolved — they don't auto-clear

---

## 2. Plan Tracking — `task.plan()` / `task.plan_step()`

### What it does

Makes the agent's execution plan visible on the dashboard. A progress bar appears above the Timeline showing each step's status: gray (not started), blue (in progress), green (completed), red (failed).

### API

```python
# When the agent creates a plan:
task.plan(
    goal="Process inbound lead and route to sales",           # required — what the plan achieves
    steps=[                                                    # required — ordered step descriptions
        "Search CRM for existing record",
        "Score lead based on criteria",
        "Generate follow-up email",
        "Update CRM with outcome",
    ],
    revision=0,                                                # optional — increment on replan
)

# As each step progresses:
task.plan_step(step_index=0, action="started", summary="Searching CRM for existing record")
# ... step executes ...
task.plan_step(step_index=0, action="completed", summary="Found existing CRM record",
               turns=2, tokens=3200)                           # turns and tokens are optional

# If a step fails:
task.plan_step(step_index=2, action="failed", summary="Email API returned 403")
```

**`task.plan_step()` actions:** `"started"`, `"completed"`, `"failed"`, `"skipped"`

### Where to instrument in loopCore

Look for code that:
- Creates a list of steps, phases, or stages
- Iterates through a strategy or pipeline
- Has a `PlanningManager` or similar that produces a plan object
- Tracks sequential operations with progress state

In the existing instrumentation plan, `planning.py` was identified as the file where `PlanningManager.create_plan()` lives. The Layer 1 implementation already added action tracking here — `task.plan()` goes right after the plan is generated from the LLM response.

### Implementation pattern

```python
from loop_core.observability import get_current_task

# After the LLM generates a plan and it's parsed into steps:
_task = get_current_task()
if _task:
    try:
        _task.plan(
            goal=plan.goal,           # or plan_description, whatever the plan object exposes
            steps=[step.description for step in plan.steps],
        )
    except Exception:
        pass

# As each step executes:
for i, step in enumerate(plan.steps):
    if _task:
        try:
            _task.plan_step(step_index=i, action="started", summary=step.description)
        except Exception:
            pass

    try:
        result = execute_step(step)
        if _task:
            try:
                _task.plan_step(step_index=i, action="completed", summary=f"{step.description}: {result.summary}")
            except Exception:
                pass
    except Exception as e:
        if _task:
            try:
                _task.plan_step(step_index=i, action="failed", summary=f"{step.description}: {str(e)[:100]}")
            except Exception:
                pass
        raise
```

### Replanning

If the agent changes its plan mid-task, call `task.plan()` again with `revision=1` (or increment):

```python
_task.plan("Revised approach: route to manual review",
           ["Notify manager", "Queue for review"], revision=1)
```

The dashboard shows the latest plan. Previous plans remain in the timeline history.

### Relationship with existing Layer 1 work

The instrumentation plan notes that loopCore already has partial plan tracking from Layer 1 (action tracking on `create_plan`). Adding `task.plan()` and `task.plan_step()` is a small delta:
- `task.plan()` goes right after the plan is created (after the LLM call, after parsing)
- `task.plan_step()` goes at step transitions (start/complete/fail of each step)
- The existing `@track("create_plan")` action still fires — it now wraps the plan creation, while `task.plan()` reports the plan content

### Dashboard result

- Plan progress bar above the Timeline track
- Each step is a segment: gray (not started), blue (in progress), green (completed), red (failed)
- Hover a segment → step description
- Answers "where in the plan did it fail?" at a glance

---

## 3. Escalations — `task.escalate()`

### What it does

Records when an agent decides it cannot handle something alone and hands it off — to a human, another team, or another agent.

### API

```python
task.escalate(
    "Lead score below threshold (0.2) — needs manual review",   # summary (required, positional) — why the escalation
    assigned_to="senior-support",                                # optional — who receives it
    reason="Score 0.2 is below the 0.5 threshold",              # optional — additional detail
)
```

**Signature:** `task.escalate(summary, *, assigned_to=None, reason=None, parent_event_id=None)`

- `summary` (str, **required**) — the primary escalation message, shown on Timeline and Activity Stream
- `assigned_to` (str, optional) — who or what receives the escalation
- `reason` (str, optional) — additional detail beyond the summary
- `parent_event_id` (str, optional) — links to a parent event for chaining

### Where to instrument in loopCore

Look for code that:
- Has reflection results that say "escalate"
- Sends alerts or notifications to humans
- Transfers work to another agent or queue
- Routes items to a human review workflow
- Decides "I can't handle this"

In the instrumentation plan, `loop.py:936-948` was identified as the location where reflection returns "escalate."

### Implementation pattern

```python
from loop_core.observability import get_current_task

# When the agent decides to escalate:
_task = get_current_task()
if _task:
    try:
        _task.escalate(
            f"Agent escalated: {escalation_reason}",
            assigned_to="human-reviewer",
        )
    except Exception:
        pass
```

### Dashboard result

- Amber escalation node in the Timeline
- `escalated` event in the Activity Stream
- Visible under the "human" stream filter

---

## 4. Approvals — `task.request_approval()` / `task.approval_received()`

### What it does

Tracks human-in-the-loop approval workflows. The agent asks for permission, waits, and records the decision. The agent card shows a **WAITING** badge while approval is pending.

### API

```python
# When the agent needs approval:
task.request_approval(
    "Approval needed for account credit of $500",   # summary (required, positional)
    approver="support-lead",                         # optional — who should approve
)

# When approval comes back:
task.approval_received(
    "Credit approved by support-lead",   # summary (required, positional)
    approved_by="support-lead",          # optional — who decided
    decision="approved",                 # optional — "approved" (default) or "rejected"
)
```

**`request_approval` signature:** `task.request_approval(summary, *, approver=None, parent_event_id=None)`

- `summary` (str, **required**) — what needs approval
- `approver` (str, optional) — who should approve

**`approval_received` signature:** `task.approval_received(summary, *, approved_by=None, decision="approved", parent_event_id=None)`

- `summary` (str, **required**) — description of the decision
- `approved_by` (str, optional) — who decided
- `decision` (str, optional) — `"approved"` (default) or `"rejected"`

### Where to instrument in loopCore

Look for code that:
- Pauses execution waiting for human input
- Has "pending_approval" states
- Checks for approval status in a loop or callback
- Sends approval requests to a queue, Slack, or UI

In the instrumentation plan:
- `runtime.py:1170-1171` — when an event goes to `pending_approval` → `task.request_approval()`
- `runtime.py:1183-1204` — when `approve_event()` is called → `task.approval_received()` with `decision="approved"`
- `runtime.py:1206-1219` — when `drop_event()` is called → `task.approval_received()` with `decision="rejected"`

### Implementation pattern

```python
from loop_core.observability import get_current_task

# When entering pending_approval:
_task = get_current_task()
if _task:
    try:
        _task.request_approval(
            f"Approval needed: {event.summary}",
            approver="human",
        )
    except Exception:
        pass

# When approved:
_task = get_current_task()
if _task:
    try:
        _task.approval_received(
            "Approved by operator",
            approved_by="human",
            decision="approved",
        )
    except Exception:
        pass

# When rejected/dropped:
_task = get_current_task()
if _task:
    try:
        _task.approval_received(
            "Dropped by operator",
            approved_by="human",
            decision="rejected",
        )
    except Exception:
        pass
```

### Note on task context at approval time

There's a potential plumbing challenge here: when `approve_event()` or `drop_event()` is called, it may happen in a different request/thread than the original task. If `get_current_task()` returns `None` at that point, the approval event won't be recorded.

**Options:**
1. Store the task handle alongside the pending approval state and use it directly
2. Use `agent.event()` as a fallback (agent-level, not task-level)
3. Accept that approvals outside task context are silently skipped

Option 1 is the cleanest but requires modifying how loopCore stores pending approval state.

### Dashboard result

- Agent badge changes to **WAITING** (amber) after `request_approval()`
- Agent badge returns to **PROCESSING** after `approval_received()`
- **Waiting** count in Stats Ribbon increments/decrements
- Amber (request) and green/red (decision) nodes in Timeline
- If Waiting count stays high → review process is a bottleneck

---

## 5. Retries — `task.retry()`

### What it does

Records when an agent retries a failed operation. Makes retry patterns visible on the Timeline — how many attempts, how much time lost to retries, which operations trigger them.

### API

```python
task.retry(
    "Rate limited by CRM API",           # summary (required, positional) — why the retry
    attempt=2,                            # optional — attempt number (1-based)
    backoff_seconds=4.0,                  # optional — time before next attempt
)
```

**Signature:** `task.retry(summary, *, attempt=None, backoff_seconds=None, parent_event_id=None)`

- `summary` (str, **required**) — what's being retried and why
- `attempt` (int, optional) — attempt number
- `backoff_seconds` (float, optional) — wait before next attempt
- `parent_event_id` (str, optional) — links to a parent event

### Where to instrument in loopCore

Look for code that:
- Catches exceptions and retries (`for attempt in range(max_retries):`)
- Has backoff/sleep between attempts
- Uses retry libraries (tenacity, backoff)
- Creates retry TODOs after failed runs

In the instrumentation plan, `agent.py:801-858` was identified as the location where failed runs create retry TODOs.

### Implementation pattern

```python
from loop_core.observability import get_current_task

# In a retry loop:
for attempt in range(max_retries):
    try:
        result = call_external_api()
        break
    except RetryableError as e:
        _task = get_current_task()
        if _task:
            try:
                _task.retry(
                    f"Retrying: {str(e)[:100]}",
                    attempt=attempt + 1,
                    backoff_seconds=2 ** attempt,
                )
            except Exception:
                pass
        time.sleep(2 ** attempt)
```

### Dashboard result

- Retry nodes appear in the Timeline
- Timeline branching shows the retry path
- Helps answer: Are retries common? Which operations trigger them? How much time is lost?

---

## 6. Queue Snapshots — `agent.queue_snapshot()`

### What it does

Reports the current state of the agent's work queue. The agent card shows a queue depth badge (e.g. "Q:4", amber if >5), and the Pipeline tab shows the actual queue contents.

### API

```python
agent.queue_snapshot(
    depth=4,                              # required — items currently queued
    oldest_age_seconds=120,               # optional — age of oldest item
    items=[                               # optional — individual items for Pipeline tab
        {"id": "evt_001", "priority": "high", "source": "human",
         "summary": "Review contract draft", "queued_at": "2026-02-11T14:28:00Z"},
        {"id": "evt_002", "priority": "normal", "source": "webhook",
         "summary": "Process CRM update", "queued_at": "2026-02-11T14:29:00Z"},
    ],
    processing={                          # optional — what's being processed now
        "id": "evt_003", "summary": "Sending email",
        "started_at": "2026-02-11T14:29:30Z", "elapsed_ms": 4500
    },
)
```

### Two ways to emit queue snapshots

**Option A — Manual calls:** Call `agent.queue_snapshot()` explicitly at strategic points (e.g., after queue changes, periodically).

**Option B — `queue_provider` callback (recommended):** Provide a callback when registering the agent. The SDK calls it automatically on each heartbeat cycle:

```python
def my_queue_provider():
    """Called by HiveLoop on every heartbeat. Return current queue state."""
    return {
        "depth": len(agent.event_queue),
        "oldest_age_seconds": calculate_oldest_age(),
        "items": [
            {"id": evt.id, "priority": evt.priority, "source": evt.source,
             "summary": evt.summary, "queued_at": evt.created_at.isoformat()}
            for evt in agent.event_queue[:10]   # cap at 10 for readability
        ],
    }

hiveloop_agent = hb.agent(
    agent_id=agent.name,
    type=agent.type,
    framework="loopcore",
    heartbeat_interval=30,
    stuck_threshold=300,
    queue_provider=my_queue_provider,        # ← automatic queue snapshots
)
```

### Where to instrument in loopCore

This depends on whether loopCore has a work queue concept. If agents process events from a queue (which they appear to from the instrumentation plan), the `queue_provider` callback is the cleanest approach — wire it up once at agent registration, and queue state flows to the dashboard automatically on every heartbeat.

If you prefer manual calls, add `agent.queue_snapshot()` after queue mutations (enqueue, dequeue, drain).

### Dashboard result

- Queue depth badge on agent card: **Q:4** (amber if >5)
- Pipeline tab → Queue section with individual items
- "Queue is empty — agent is caught up" when `depth=0`
- **Note:** The badge uses `depth` (just a number). The Pipeline table needs `items` (the actual entries). If you send `depth=4` without `items`, you get the badge but an empty table.

---

## 7. TODOs — `agent.todo()`

### What it does

Tracks work items the agent is managing — retries, deferred tasks, follow-ups. TODOs have a lifecycle: created → completed/failed/dismissed/deferred.

### API

```python
# Create a TODO:
agent.todo(
    todo_id="todo_retry_crm",             # required — stable identifier
    action="created",                      # required — "created", "completed", "failed", "dismissed", "deferred"
    summary="Retry: CRM write failed",    # required — description
    priority="high",                       # optional — "high", "normal", "low"
    source="failed_action",               # optional — what created it
    context="Tool crm_write returned 403", # optional — details
)

# Later, when resolved:
agent.todo(
    todo_id="todo_retry_crm",
    action="completed",
    summary="CRM write succeeded after credential refresh",
)
```

### Where to instrument in loopCore

Look for code that:
- Creates retry items after failures
- Queues deferred work
- Tracks follow-up actions the agent decides to do later
- Has "TODO" or "pending work" data structures

The instrumentation plan identifies `agent.py:801-858` where failed runs create retry TODOs — this is the primary instrumentation point.

### Lifecycle

The server aggregates TODO events by `todo_id` and takes the most recent action. A TODO is "active" until its most recent action is `"completed"` or `"dismissed"`. This means:

```python
agent.todo("todo_001", "created", "Do X")       # → active
agent.todo("todo_001", "deferred", "Do X later") # → still active (deferred ≠ done)
agent.todo("todo_001", "completed", "Did X")     # → resolved, disappears from active list
```

### Dashboard result

- Pipeline tab → Active TODOs table with priority and source
- Activity Stream → `todo` events

---

## 8. Scheduled Work — `agent.scheduled()`

### What it does

Reports recurring work the agent is configured to perform. This is a snapshot of the agent's schedule — what runs, when, how often.

### API

```python
agent.scheduled(items=[
    {"id": "sched_crm_sync", "name": "CRM Pipeline Sync",
     "next_run": "2026-02-12T15:00:00Z", "interval": "1h",
     "enabled": True, "last_status": "success"},
    {"id": "sched_cleanup", "name": "Stale data cleanup",
     "next_run": "2026-02-13T08:00:00Z", "interval": "daily",
     "enabled": True, "last_status": None},
])
```

**`items` fields:** `id`, `name`, `next_run` (ISO 8601), `interval` (e.g. `"5m"`, `"1h"`, `"daily"`, `"weekly"`), `enabled` (boolean), `last_status` (`"success"`, `"failure"`, `"skipped"`, or `null`)

### Where to instrument in loopCore

Only relevant if loopCore has scheduled/recurring tasks. Call `agent.scheduled()` periodically (e.g., every few minutes or when schedule changes) to keep the dashboard current.

### Dashboard result

- Pipeline tab → Scheduled section with next run times and intervals

---

## File-to-Method Mapping

Quick reference for the implementer — where each method likely goes in loopCore based on the instrumentation plan:

| File | Methods to add | Context |
|------|---------------|---------|
| `agent_manager.py` | `agent.queue_snapshot()` via `queue_provider` callback | At agent registration — wire up the callback |
| `loop.py` | `task.retry()` | In retry loops around Phase 1/Phase 2 calls |
| `loop.py:936-948` | `task.escalate()` | When reflection returns "escalate" |
| `planning.py` | `task.plan()`, `task.plan_step()` | After plan creation, at step transitions |
| `runtime.py:1170-1171` | `task.request_approval()` | When event enters `pending_approval` |
| `runtime.py:1183-1219` | `task.approval_received()` | When `approve_event()` or `drop_event()` is called |
| `agent.py:801-858` | `agent.todo()`, `task.retry()` | When failed runs create retry TODOs |
| `issue_tools.py:163-234` | `agent.report_issue()` | When `report_issue` tool is invoked |
| Error handlers (various) | `agent.report_issue()` | External API failures, rate limits, LLM errors |

---

## Implementation Priority

Based on value-per-effort and the current state (LLM tracking done):

### Tier 1 — Do next (remaining high value, low effort)

| Method | Effort | Why |
|--------|--------|-----|
| `agent.report_issue()` | 1-3 lines per site | Surfaces "invisible failures." Lights up Pipeline tab immediately. |

### Tier 2 — High value, medium effort

| Method | Effort | Why |
|--------|--------|-----|
| `task.plan()` + `task.plan_step()` | 5-10 lines at plan creation, 2 lines per step transition | Partial wiring already exists from Layer 1. Small delta to complete. |
| `task.escalate()` | 1-2 lines per escalation point | Visible whenever the agent hands off to humans. |

### Tier 3 — Architecture-dependent

| Method | Effort | Condition |
|--------|--------|-----------|
| `task.request_approval()` + `task.approval_received()` | 2-4 lines per workflow | Only if human-in-the-loop approval exists |
| `task.retry()` | 2-3 lines per retry loop | Only if retry patterns are common |
| `agent.queue_snapshot()` | 5-10 lines + queue access | Only if there's a work queue — `queue_provider` callback is the cleanest path |

### Tier 4 — Nice to have

| Method | Effort | Why |
|--------|--------|-----|
| `agent.todo()` | 1-2 lines per lifecycle event | Work item tracking for complex agents |
| `agent.scheduled()` | 1-2 lines at config time | Only if there's recurring work |

### Recommended sequence

1. **`agent.report_issue()`** — remaining Tier 1 item, highest value
2. **`task.plan()` + `task.plan_step()`** — partial wiring exists, small delta
3. **`task.escalate()`** — single call site, visible immediately
4. **`task.request_approval()` + `task.approval_received()`** — if approval flows exist
5. **`agent.queue_snapshot()` via `queue_provider`** — wire up once at registration
6. **`task.retry()`** + **`agent.todo()`** — fill in as time allows

At each step, check the dashboard to confirm the new data appears before moving on.

---

## Validation Checklist

After each method is implemented, verify on the HiveBoard dashboard:

### After `agent.report_issue()`
- [ ] Agent card in The Hive shows red issue badge (e.g. "● 1 issue")
- [ ] Pipeline tab → Active Issues table shows severity, category, occurrences
- [ ] Activity Stream → "issue" filter shows the event
- [ ] (If implemented) `agent.resolve_issue()` clears the issue from Pipeline

### After `task.plan()` + `task.plan_step()`
- [ ] Plan progress bar appears above the Timeline
- [ ] Steps show correct colors: gray/blue/green/red
- [ ] Hover a segment → step description appears
- [ ] Failed steps show in red

### After `task.escalate()`
- [ ] Amber escalation node appears in Timeline
- [ ] Activity Stream → "human" filter shows the event

### After `task.request_approval()` + `task.approval_received()`
- [ ] Agent badge shows WAITING (amber) after request
- [ ] Agent badge returns to PROCESSING after received
- [ ] Stats Ribbon → Waiting count changes
- [ ] Timeline shows amber (request) and green/red (decision) nodes

### After `task.retry()`
- [ ] Retry nodes appear in Timeline
- [ ] Attempt numbers are visible

### After `agent.queue_snapshot()`
- [ ] Agent card shows queue depth badge (e.g. "Q:4")
- [ ] Pipeline tab → Queue section shows individual items
- [ ] Badge turns amber when depth >5

### After `agent.todo()`
- [ ] Pipeline tab → Active TODOs table populates
- [ ] Completing a TODO removes it from the active list

---

## Troubleshooting

### Events not appearing

1. **Check the guard.** Is `get_current_task()` or `get_hiveloop_agent()` returning `None`? Temporarily add a log: `print(f"task={_task}, agent={_agent}")` to confirm.
2. **Check the try/except.** Temporarily remove the `try/except` to see if an error is being swallowed.
3. **Check the scope.** Task-level methods (`task.plan()`, `task.escalate()`) require an active task context — they won't work outside `agent.task()`. Agent-level methods (`agent.report_issue()`) work anywhere.

### Issues not clearing from Pipeline tab

The `summary` or `issue_id` in `resolve_issue()` doesn't match the original `report_issue()`. The strings must match exactly (server deduplicates by hash). Use `issue_id` for reliable lifecycle tracking.

### Plan bar not visible

- The plan needs at least one step to render
- The plan event must be inside a task context — `get_current_task()` must return a valid task
- You may be looking at a different task — click the correct one in the Task Table

### Queue badge shows but Pipeline table is empty

You're sending `depth=4` without `items`. The badge uses `depth` (just a number), but the Pipeline table needs `items` (the actual queue entries). Include the `items` array for the full view.

### Approval events lost between requests

If `approve_event()` runs in a different thread/request than the original task, `get_current_task()` returns `None`. Store the task handle alongside the pending approval state, or use agent-level events as a fallback.

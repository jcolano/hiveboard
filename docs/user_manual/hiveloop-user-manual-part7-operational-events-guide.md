# HiveLoop — User Manual Part 7: Operational Events Integration Guide

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *Plans, escalations, issues, retries, and queue snapshots — the methods that turn a timeline into a narrative your ops team can act on.*

---

## Table of Contents

1. [What This Guide Covers](#1-what-this-guide-covers)
2. [The Integration Mindset](#2-the-integration-mindset)
3. [Plans — `task.plan()` and `task.plan_step()`](#3-plans--taskplan-and-taskplan_step)
4. [Escalations — `task.escalate()`](#4-escalations--taskescalate)
5. [Issues — `agent.report_issue()`](#5-issues--agentreport_issue)
6. [Queue Snapshots — `agent.queue_snapshot()`](#6-queue-snapshots--agentqueue_snapshot)
7. [Retries — `task.retry()`](#7-retries--taskretry)
8. [Tool Execution Tracking — `agent.track_context()`](#8-tool-execution-tracking--agenttrack_context)
9. [Putting It All Together](#9-putting-it-all-together)
10. [Finding Integration Points in Your Codebase](#10-finding-integration-points-in-your-codebase)
11. [Validation Checklists](#11-validation-checklists)
12. [Common Mistakes](#12-common-mistakes)

---

## 1. What This Guide Covers

This guide covers five SDK methods that add operational context to your agent's telemetry. These are **Layer 2 events** — they build on top of Layer 0 (init + heartbeat) and Layer 1 (tasks + actions), adding the narrative that answers "why did it fail?" and "what is it waiting for?"

| Method | Scope | What it answers |
|--------|-------|----------------|
| `task.plan()` + `task.plan_step()` | Task | What is the agent's strategy? Which step failed? How far did it get? |
| `task.escalate()` | Task | When did the agent decide it needs help? Who did it hand off to? |
| `agent.report_issue()` | Agent | What persistent problems has the agent detected? |
| `agent.queue_snapshot()` | Agent | How deep is the work queue? Is the agent falling behind? |
| `task.retry()` | Task | How many retries? What triggered them? How much time is lost? |
| `agent.track_context()` | Task | Which tools were called? How long did each take? Which failed? |

### Prerequisites

Before integrating these methods, you should have:

- [x] Layer 0 working — `hiveloop.init()` + `hb.agent()`, agents visible with heartbeats
- [x] Layer 1 working — `agent.task()` + `@agent.track()`, tasks and actions in the timeline
- [x] A plumbing pattern in place — `contextvars`, parameter passing, or framework context (see Part 3, Section 5)

If Layer 1 isn't working yet, go back to Part 3 (Instrumentation Guide) and Part 4 (Layer 1 — What to Expect). These operational events build on task context and are meaningless without it.

---

## 2. The Integration Mindset

These five methods share a pattern that's different from `task.llm_call()` and `@agent.track()`:

**LLM calls and action tracking** are about *measuring* — they record something that already happened (a function ran, a model was called). You find the call site and add a line after it.

**Operational events** are about *narrating* — they describe the agent's decisions, problems, and state transitions. The code that triggers them often doesn't look like a function call you can easily spot. You need to understand the agent's decision-making flow to know where to add them.

### What to look for

| Method | Code pattern to search for |
|--------|---------------------------|
| `task.plan()` | Step lists, strategy objects, workflow definitions, phase arrays |
| `task.plan_step()` | Progress tracking, step iteration, phase transitions |
| `task.escalate()` | Handoffs, delegation, "needs human review" logic |
| `agent.report_issue()` | Error detection outside of task failures, degraded conditions, health checks |
| `agent.queue_snapshot()` | Work queues, job lists, pending items, inbox polling |
| `task.retry()` | Retry loops, backoff logic, `tenacity` decorators, `for attempt in range(N)` |

---

## 3. Plans — `task.plan()` and `task.plan_step()`

### 3.1 What it does

When your agent creates a multi-step strategy — "first do X, then Y, then Z" — `task.plan()` makes that strategy visible on the dashboard. `task.plan_step()` updates each step's status as the agent progresses.

On the dashboard, this renders as a **plan progress bar** above the timeline:

```
[■ Search CRM] [■ Score lead] [▪ Generate email] [  Update CRM  ]
   completed      completed      in progress        not started
```

### 3.2 The API

**`task.plan(goal, steps)`** — declare the plan:

```python
task.plan(
    "Process and route incoming lead",       # goal — what the plan achieves
    [                                        # steps — ordered list of step descriptions
        "Search CRM for existing record",
        "Score lead against criteria",
        "Generate follow-up email",
        "Update CRM with outcome",
    ],
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `goal` | str | **Yes** | What the plan aims to achieve |
| `steps` | list[str] | **Yes** | Ordered step descriptions. Order determines step indices (0, 1, 2, ...) |
| `revision` | int | No | Plan revision number. Default `0`. Increment when the agent replans |

**`task.plan_step(step_index, action, summary)`** — update a step:

```python
task.plan_step(0, "started", "Searching CRM for lead #4801")
# ... step executes ...
task.plan_step(0, "completed", "Found existing CRM record",
               turns=2, tokens=3200)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `step_index` | int | **Yes** | Zero-based position in the steps list |
| `action` | str | **Yes** | `"started"`, `"completed"`, `"failed"`, `"skipped"` |
| `summary` | str | **Yes** | Outcome or status note |
| `total_steps` | int | No | Auto-inferred from `task.plan()` if previously called |
| `turns` | int | No | LLM turns spent on this step |
| `tokens` | int | No | Total tokens spent on this step |
| `plan_revision` | int | No | Correlates with `task.plan()` revision |

### 3.3 Finding WHERE to add plans

Search your codebase for code that:

1. **Creates a sequence of steps to execute.** Look for lists, arrays, or objects that define a strategy:
   ```python
   # Pattern A — explicit step list
   steps = ["fetch data", "analyze", "generate report", "send email"]

   # Pattern B — workflow/pipeline definition
   pipeline = [FetchStage(), AnalyzeStage(), ReportStage()]

   # Pattern C — LLM-generated plan
   plan = llm.create_plan(objective)  # returns structured steps
   ```

2. **Iterates through stages or phases.** The agent processes work in a defined order:
   ```python
   for i, step in enumerate(pipeline.stages):
       step.execute(context)
   ```

3. **Tracks progress through sequential work.** Counters, phase variables, or state machines:
   ```python
   current_phase = "scoring"
   # ... later ...
   current_phase = "routing"
   ```

### 3.4 Integration pattern

Once you've found the plan creation point and the step execution loop, add instrumentation:

```python
from myproject.observability import get_current_task

def execute_plan(objective, data):
    task = get_current_task()

    # Step 1: Agent creates its plan
    steps = planner.generate_steps(objective)

    if task:
        task.plan(objective, [s.description for s in steps])

    # Step 2: Execute each step, tracking progress
    for i, step in enumerate(steps):
        if task:
            task.plan_step(i, "started", step.description)

        try:
            result = step.execute(data)
            if task:
                task.plan_step(i, "completed", f"{step.description} — {result.summary}")
        except Exception as e:
            if task:
                task.plan_step(i, "failed", f"{step.description} — {e}")
            raise
```

### 3.5 Replanning

If the agent changes its plan mid-execution (e.g., after a step fails and it creates a new strategy), call `task.plan()` again with an incremented `revision`:

```python
# Original plan failed at step 2
task.plan_step(2, "failed", "Email API returned 403")

# Agent replans
new_steps = planner.replan(objective, failed_step=2)
task.plan("Revised: route to manual review", [s.description for s in new_steps], revision=1)

# Continue with new plan
task.plan_step(0, "started", "Notifying manager")
```

The dashboard shows the latest plan. Previous plan events remain in the timeline for the full history.

### 3.6 loopCore example

In loopCore, the planning system creates execution plans with explicit step lists:

**Plan creation — `planning.py`:**
```python
# After the LLM generates a plan:
plan_steps = parse_plan_response(plan_response)

task = get_current_task()
if task:
    try:
        task.plan(
            objective,
            [step["description"] for step in plan_steps],
        )
    except Exception:
        pass
```

**Step execution — `loop.py`:**
```python
# As each step is processed in the agent loop:
task = get_current_task()
if task:
    try:
        task.plan_step(step_index, "started", f"Executing: {step_name}")
    except Exception:
        pass

# ... step executes ...

if task:
    try:
        task.plan_step(step_index, "completed", step_result_summary,
                       turns=turns_used, tokens=tokens_spent)
    except Exception:
        pass
```

### 3.7 Dashboard impact

| Element | What appears |
|---------|-------------|
| **Timeline** | Plan progress bar above the main track. Green = completed, blue = in progress, red = failed, gray = not started |
| **Timeline detail** | Click a plan node to see goal, step count, and revision |
| **Activity Stream** | `plan_created` and `plan_step` events with step index and status |

---

## 4. Escalations — `task.escalate()`

### 4.1 What it does

`task.escalate()` records the moment an agent decides it cannot handle something alone. This is a critical operational signal — it means the agent needs human intervention, a different agent, or a different approach.

### 4.2 The API

```python
task.escalate(
    "Lead score 0.12 — below threshold, needs manual review",
    assigned_to="senior-sales",
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | str | **Yes** | Why the agent escalated — this appears on the timeline and in the Activity Stream |
| `assigned_to` | str | No | Who or what receives the escalation (person, team, queue, agent) |
| `reason` | str | No | Additional reason detail (if summary isn't enough) |

### 4.3 Finding WHERE to add escalations

Escalation points are where the agent says "I can't handle this." Search for:

1. **Threshold checks that route to humans:**
   ```python
   if confidence < MIN_CONFIDENCE:
       notify_human(result)          # ← escalation point
   ```

2. **Error handling that delegates:**
   ```python
   except PermissionError:
       queue_for_manual_review(item)  # ← escalation point
   ```

3. **Decision logic with a "give up" branch:**
   ```python
   if retries_exhausted:
       hand_off_to_senior(task)       # ← escalation point
   ```

4. **Explicit escalation methods or flags:**
   ```python
   def escalate_to_human(self, reason):   # ← the method name itself is the clue
       self.status = "escalated"
   ```

5. **LLM-driven escalation decisions:**
   ```python
   if llm_decision == "escalate":
       assign_to_human(context)        # ← escalation point
   ```

### 4.4 Integration pattern

Add `task.escalate()` at the point where the escalation decision is made — before the actual handoff logic:

```python
from myproject.observability import get_current_task

def handle_low_confidence_result(item, score):
    task = get_current_task()
    if task:
        try:
            task.escalate(
                f"Score {score:.2f} below threshold — needs manual review",
                assigned_to="senior-sales",
            )
        except Exception:
            pass

    # Existing escalation logic (unchanged):
    queue_for_review(item, assignee="senior-sales")
```

### 4.5 loopCore example

In loopCore, escalation happens when the reflection engine decides the agent should hand off:

**`loop.py` — reflection returns "escalate":**
```python
if reflection_decision == "escalate":
    task = get_current_task()
    if task:
        try:
            task.escalate(
                f"Agent escalated: {reflection_reason}",
                assigned_to="human-reviewer",
            )
        except Exception:
            pass

    # Existing escalation flow continues:
    create_escalation_event(agent, reason=reflection_reason)
```

### 4.6 Escalation vs. approval

These are related but different:

| Concept | When to use | Example |
|---------|-------------|---------|
| **Escalation** | Agent hands off work entirely | "I can't handle this billing dispute — routing to senior support" |
| **Approval request** | Agent pauses and waits for permission | "I want to issue a $500 credit — need manager approval before proceeding" |

If the agent **stops working and waits**, use `task.request_approval()` (see Part 5, Section 5). If the agent **passes the work to someone else and moves on**, use `task.escalate()`.

Some workflows involve both — escalate, then wait for approval:

```python
task.escalate("Complex case — needs senior review", assigned_to="senior-support")
task.request_approval("Approval needed for account credit", approver="support-lead")
# ... agent waits ...
task.approval_received("Credit approved", approved_by="support-lead", decision="approved")
```

### 4.7 Dashboard impact

| Element | What appears |
|---------|-------------|
| **Timeline** | Amber escalation node with the summary text |
| **Activity Stream** | `escalated` event, visible under the "human" filter |
| **Agent card** | No direct badge change (escalation doesn't block the agent, unlike approval) |

---

## 5. Issues — `agent.report_issue()`

### 5.1 What it does

`agent.report_issue()` lets agents self-report persistent problems. Unlike task failures (which are automatic and per-task), issues are agent-level and persistent — they represent ongoing conditions like "CRM API is returning 403s" or "data quality is degrading."

Issues stay active on the dashboard until explicitly resolved with `agent.resolve_issue()`.

### 5.2 The API

**Report an issue:**

```python
agent.report_issue(
    summary="CRM API returning 403 for workspace queries",
    severity="high",
    issue_id="crm-403",
    category="permissions",
    context={"api": "salesforce", "error_code": 403, "last_seen": "2026-02-12T14:30:00Z"},
    occurrence_count=3,
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | str | **Yes** | Issue description. Used for dedup if no `issue_id` |
| `severity` | str | **Yes** | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `issue_id` | str | No | Stable identifier for lifecycle tracking. Strongly recommended |
| `category` | str | No | Classification: `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"` |
| `context` | dict | No | Arbitrary debugging data — API names, error codes, timestamps |
| `occurrence_count` | int | No | Agent-tracked count of how many times this has happened |

**Resolve an issue:**

```python
agent.resolve_issue(
    "CRM API recovered — returning 200 again",
    issue_id="crm-403",
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | str | **Yes** | Resolution message |
| `issue_id` | str | No | Must match the original report for lifecycle tracking |

### 5.3 Issues vs. task failures — when to use which

This is the most common source of confusion. Here's the distinction:

| | Task failure | Agent issue |
|---|---|---|
| **Scope** | One specific task | The agent overall |
| **Trigger** | Exception inside `agent.task()` | Agent detects an ongoing problem |
| **Lifecycle** | Automatic — emitted when the task's context manager catches an exception | Manual — you call `report_issue()` and `resolve_issue()` |
| **Duration** | Instantaneous — a single failed attempt | Persistent — stays until resolved |
| **Example** | "Task #4801 failed: ConnectionError" | "CRM API has been returning 403 for the last 30 minutes" |
| **Dashboard** | Red dot on the task row, `task_failed` in Activity Stream | Red badge on the agent card, Issues table in Pipeline tab |

**Rule of thumb:** If the problem would go away by retrying the task, it's a task failure. If the problem persists across multiple tasks, it's an issue.

### 5.4 Finding WHERE to add issue reporting

Look for code that detects persistent problems — not individual errors, but patterns:

1. **Retry exhaustion with circuit-breaker logic:**
   ```python
   if consecutive_failures >= FAILURE_THRESHOLD:
       self.circuit_open = True                     # ← issue point
       log.warning("Circuit breaker opened for CRM API")
   ```

2. **Health check failures:**
   ```python
   def health_check(self):
       if not self.api_client.ping():
           log.warning("API health check failed")   # ← issue point
   ```

3. **Rate limit detection:**
   ```python
   if response.status_code == 429:
       self.rate_limited = True                      # ← issue point
       self.backoff_until = time.time() + retry_after
   ```

4. **Data quality checks:**
   ```python
   if invalid_records / total_records > 0.10:
       log.warning("10%+ records invalid")           # ← issue point
   ```

5. **Configuration problems detected at runtime:**
   ```python
   if not os.environ.get("API_KEY"):
       log.error("API_KEY not configured")            # ← issue point
   ```

### 5.5 Integration pattern

The typical pattern has three parts: detect, report, and resolve.

```python
# Detection — when the agent discovers a problem:
def on_api_error(self, error, api_name):
    self._error_counts[api_name] = self._error_counts.get(api_name, 0) + 1

    if self._error_counts[api_name] >= 3:
        hiveloop_agent = get_hiveloop_agent(self.agent_name)
        if hiveloop_agent:
            try:
                hiveloop_agent.report_issue(
                    summary=f"{api_name} consistently failing: {error}",
                    severity="high",
                    issue_id=f"api-error-{api_name}",
                    category="connectivity",
                    context={
                        "api": api_name,
                        "error": str(error),
                        "consecutive_failures": self._error_counts[api_name],
                    },
                    occurrence_count=self._error_counts[api_name],
                )
            except Exception:
                pass

# Resolution — when the problem goes away:
def on_api_success(self, api_name):
    if self._error_counts.get(api_name, 0) >= 3:
        hiveloop_agent = get_hiveloop_agent(self.agent_name)
        if hiveloop_agent:
            try:
                hiveloop_agent.resolve_issue(
                    f"{api_name} recovered",
                    issue_id=f"api-error-{api_name}",
                )
            except Exception:
                pass
    self._error_counts[api_name] = 0
```

### 5.6 Severity guidelines

| Severity | When to use | Example |
|----------|-------------|---------|
| `critical` | Agent cannot function at all | "No API key configured", "Database unreachable" |
| `high` | Agent can work but a major capability is degraded | "CRM API returning 403", "LLM rate limited" |
| `medium` | Agent works but output quality is reduced | "Enrichment data stale", "Fallback model in use" |
| `low` | Informational — something the ops team should know | "Cache miss rate high", "Slow response times" |

### 5.7 The `issue_id` pattern

Always use `issue_id` for issues that can be resolved. Without it, deduplication is hash-based on the summary text, which is fragile.

**Good `issue_id` patterns:**

```python
issue_id="crm-api-403"          # API + error code
issue_id="rate-limit-openai"    # category + service
issue_id=f"data-quality-{table}"  # category + entity
```

**Avoid:**

```python
issue_id=str(uuid.uuid4())     # unique per occurrence — defeats dedup
issue_id="error"               # too generic — all issues collapse into one
```

### 5.8 loopCore example

In loopCore, the issue reporting tool (`report_issue`) is already a first-class agent capability. The agent calls it when it detects problems with its tools:

**`issue_tools.py` — when a tool consistently fails:**
```python
def on_tool_failure(agent_name, tool_name, error, consecutive_count):
    hiveloop_agent = get_hiveloop_agent(agent_name)
    if hiveloop_agent and consecutive_count >= 3:
        try:
            hiveloop_agent.report_issue(
                summary=f"Tool '{tool_name}' failing: {error}",
                severity="high",
                issue_id=f"tool-failure-{tool_name}",
                category="connectivity",
                context={
                    "tool": tool_name,
                    "error": str(error),
                    "consecutive_failures": consecutive_count,
                },
                occurrence_count=consecutive_count,
            )
        except Exception:
            pass
```

**Resolution — when the tool succeeds again:**
```python
def on_tool_success(agent_name, tool_name):
    hiveloop_agent = get_hiveloop_agent(agent_name)
    if hiveloop_agent:
        try:
            hiveloop_agent.resolve_issue(
                f"Tool '{tool_name}' recovered",
                issue_id=f"tool-failure-{tool_name}",
            )
        except Exception:
            pass
```

### 5.9 Dashboard impact

| Element | What appears |
|---------|-------------|
| **Agent card** | Red issue badge (e.g. "● 1 issue") — visible in The Hive |
| **Pipeline tab** | Active Issues table with severity, category, occurrence count |
| **Activity Stream** | Issue events with warning icons; resolved issues also appear |
| **Stats** | `active_issues` count in agent stats |

Issues **persist until explicitly resolved.** If you report an issue and never resolve it, the red badge stays on the agent card permanently. This is by design — persistent problems should remain visible until someone addresses them.

---

## 6. Queue Snapshots — `agent.queue_snapshot()`

### 6.1 What it does

`agent.queue_snapshot()` reports the current state of the agent's work queue. This gives the dashboard visibility into how much work is pending, how old the oldest item is, and what's currently being processed.

### 6.2 The API

```python
agent.queue_snapshot(
    depth=4,
    oldest_age_seconds=120,
    items=[
        {"id": "job-001", "priority": "high", "source": "human",
         "summary": "Review contract", "queued_at": "2026-02-12T14:28:00Z"},
        {"id": "job-002", "priority": "normal", "source": "webhook",
         "summary": "Process CRM update", "queued_at": "2026-02-12T14:29:00Z"},
    ],
    processing={"id": "job-003", "summary": "Sending email",
                "started_at": "2026-02-12T14:29:30Z", "elapsed_ms": 4500},
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `depth` | int | **Yes** | Number of items in the queue |
| `oldest_age_seconds` | int | No | Age of the oldest queued item |
| `items` | list[dict] | No | The actual queue entries (max ~10 for readability) |
| `processing` | dict | No | What's currently being processed |

**Each item in `items`:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Item identifier |
| `priority` | str | `"low"`, `"normal"`, `"high"`, `"urgent"` |
| `source` | str | Where the item came from (`"human"`, `"webhook"`, `"scheduled"`, `"agent"`) |
| `summary` | str | What the item is about |
| `queued_at` | str | ISO 8601 timestamp of when it was queued |

### 6.3 Two ways to report queue state

**Option A — Automatic via `queue_provider` callback (recommended):**

Register a callback when creating the agent. It's called every heartbeat cycle:

```python
agent = hb.agent(
    "my-agent",
    type="processor",
    queue_provider=lambda: {
        "depth": work_queue.qsize(),
        "oldest_age_seconds": get_oldest_age(),
        "items": [
            {"id": item.id, "priority": item.priority, "summary": item.summary}
            for item in list(work_queue.queue)[:10]
        ],
    },
)
```

This is fire-and-forget — once registered, it reports queue state automatically every heartbeat (default 30 seconds).

**Option B — Explicit calls (for non-standard queues):**

Call `agent.queue_snapshot()` directly at any point:

```python
hiveloop_agent = get_hiveloop_agent(agent_name)
if hiveloop_agent:
    try:
        hiveloop_agent.queue_snapshot(
            depth=len(pending_items),
            oldest_age_seconds=oldest_age,
            items=[...],
        )
    except Exception:
        pass
```

Use Option B when:
- The queue state is expensive to compute and you don't want to do it every heartbeat
- The queue is external (a database table, a Redis list, an SQS queue) and requires async access
- You want to report queue state at specific moments (e.g., after each dequeue)

### 6.4 Finding WHERE to add queue snapshots

If your agent processes a work queue, the integration point depends on how the queue is structured:

1. **In-memory queue (threading.Queue, asyncio.Queue, list):**
   ```python
   # Option A — callback (best):
   agent = hb.agent("worker", queue_provider=lambda: {"depth": q.qsize()})

   # Option B — explicit, after each dequeue:
   item = queue.get()
   agent.queue_snapshot(depth=queue.qsize())
   ```

2. **Database-backed queue (polling a table):**
   ```python
   # Report after each poll cycle:
   pending = db.query("SELECT * FROM jobs WHERE status = 'pending'")
   agent.queue_snapshot(
       depth=len(pending),
       items=[{"id": j.id, "summary": j.description} for j in pending[:10]],
   )
   ```

3. **External message queue (SQS, RabbitMQ, Redis):**
   ```python
   # Report periodically or after each message:
   approx_depth = sqs_client.get_queue_attributes(QueueUrl=url,
       AttributeNames=["ApproximateNumberOfMessages"])
   agent.queue_snapshot(depth=int(approx_depth))
   ```

4. **No explicit queue, but work arrives via events/callbacks:**
   ```python
   # Maintain a counter:
   class Agent:
       def __init__(self):
           self._pending_count = 0

       def on_new_event(self, event):
           self._pending_count += 1

       def on_event_processed(self, event):
           self._pending_count -= 1

       def get_queue_depth(self):
           return self._pending_count

   # Register callback:
   agent = hb.agent("my-agent",
       queue_provider=lambda: {"depth": my_agent.get_queue_depth()})
   ```

### 6.5 loopCore example

In loopCore, agents process events from an inbox. The queue state is the pending events list:

**Agent registration with queue provider:**
```python
hiveloop_agent = hb.agent(
    agent.name,
    type=agent.type,
    framework="loopcore",
    queue_provider=lambda: {
        "depth": len(agent.inbox.pending),
        "oldest_age_seconds": agent.inbox.oldest_age(),
        "items": [
            {
                "id": evt.id,
                "priority": evt.priority,
                "source": evt.source,
                "summary": evt.summary[:100],
            }
            for evt in agent.inbox.pending[:10]
        ],
    },
)
```

The `queue_provider` callback is called every heartbeat (30 seconds). No explicit `queue_snapshot()` calls needed — the SDK handles it.

### 6.6 Dashboard impact

| Element | What appears |
|---------|-------------|
| **Agent card** | Queue badge (e.g. "Q:4" in blue, "Q:8" in amber if >5) |
| **Pipeline tab** | Queue table with item details (ID, priority, source, summary, age) |
| **Agent stats** | `queue_depth` field in `stats_1h` response |
| **Activity Stream** | `queue_snapshot` events under the "pipeline" filter |

---

## 7. Retries — `task.retry()`

### 7.1 What it does

`task.retry()` records when an agent retries a failed operation. This makes retry patterns visible — how many retries happen, what causes them, and how much time is lost to backoff.

### 7.2 The API

```python
task.retry(
    "Retrying after CRM API timeout",
    attempt=2,
    backoff_seconds=4.0,
)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | str | **Yes** | What's being retried and why |
| `attempt` | int | No | Attempt number (1-based) |
| `backoff_seconds` | float | No | How long before the next attempt |

### 7.3 Finding WHERE to add retries

Search for retry patterns in your code:

1. **Explicit retry loops:**
   ```python
   for attempt in range(max_retries):
       try:
           result = call_api()
           break
       except TransientError:
           time.sleep(2 ** attempt)      # ← retry point
   ```

2. **Retry decorators (tenacity, backoff):**
   ```python
   @retry(stop=stop_after_attempt(3), wait=wait_exponential())
   def call_api():                        # ← each retry is invisible without instrumentation
       return api_client.get(url)
   ```

3. **Conditional re-execution:**
   ```python
   while not success and retries < MAX:
       success = try_operation()
       if not success:
           retries += 1                   # ← retry point
   ```

4. **Queue-based retry (re-enqueue on failure):**
   ```python
   except ProcessingError:
       item.retry_count += 1
       queue.put(item)                    # ← retry point
   ```

### 7.4 Integration pattern

```python
from myproject.observability import get_current_task

for attempt in range(1, max_retries + 1):
    try:
        result = call_external_api()
        break
    except TransientError as e:
        if attempt < max_retries:
            backoff = 2 ** attempt
            task = get_current_task()
            if task:
                try:
                    task.retry(
                        f"Retrying after {type(e).__name__}: {e}",
                        attempt=attempt,
                        backoff_seconds=backoff,
                    )
                except Exception:
                    pass
            time.sleep(backoff)
        else:
            raise  # final attempt — let the exception propagate
```

**For tenacity-based retries**, use a callback:

```python
import tenacity

def on_retry(retry_state):
    task = get_current_task()
    if task:
        try:
            task.retry(
                f"Retry attempt {retry_state.attempt_number}: {retry_state.outcome.exception()}",
                attempt=retry_state.attempt_number,
                backoff_seconds=retry_state.next_action.sleep if hasattr(retry_state.next_action, 'sleep') else None,
            )
        except Exception:
            pass

@tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(),
    before_sleep=on_retry,
)
def call_api():
    return api_client.get(url)
```

### 7.5 loopCore example

In loopCore, retries happen when tool execution fails and the agent decides to try again:

**`loop.py` — after a failed tool execution:**
```python
if should_retry and attempt < max_retries:
    task = get_current_task()
    if task:
        try:
            task.retry(
                f"Retrying tool '{tool_name}' after failure: {error}",
                attempt=attempt,
                backoff_seconds=backoff,
            )
        except Exception:
            pass
    time.sleep(backoff)
```

**`agent.py` — when a failed run creates a retry TODO:**
```python
task = get_current_task()
if task:
    try:
        task.retry(
            f"Scheduling retry: {failure_reason}",
            attempt=retry_count,
        )
    except Exception:
        pass
```

### 7.6 Dashboard impact

| Element | What appears |
|---------|-------------|
| **Timeline** | Retry nodes showing attempt count and backoff |
| **Activity Stream** | `retry_started` events with attempt number |

Retries help you answer: "Is this agent spending most of its time retrying? Which operation causes the most retries? Is the backoff strategy appropriate?"

---

## 8. Tool Execution Tracking — `agent.track_context()`

### 8.1 The problem

Most agentic frameworks follow a loop: the LLM reasons, decides which tool(s) to call, then the framework executes those tools one at a time. A single turn may have zero or more tool calls. The tool name is determined at runtime by the LLM — you don't know at code-definition time which tool will be called.

Without instrumentation, tool execution is invisible on the dashboard. You see the LLM call (via `task.llm_call()`) and the task lifecycle, but the actual work — searching a CRM, sending an email, querying a database — is a black box.

### 8.2 The method

`agent.track_context(tool_name)` is a context manager that wraps any code block and emits `action_started` + `action_completed` (or `action_failed`) events automatically.

```python
with agent.track_context(tool_call.name) as ctx:
    result = execute_tool(tool_call.name, tool_call.args)
```

This is the right choice over the alternatives:

| Method | Why it's not ideal for tool dispatch |
|--------|-------------------------------------|
| `@agent.track("name")` | Decorator — requires the tool name at function definition time. In agentic loops, the LLM picks the tool at runtime |
| `task.event("tool_used", ...)` | Raw event — no automatic duration, no start/complete pairing, no nesting |
| `task.llm_call()` | Wrong scope — for the LLM API call itself, not the tool execution after it |

### 8.3 What you get automatically

Each `track_context()` block gives you:

| Feature | How |
|---------|-----|
| **Tool name on timeline** | Passed as the string argument — shown on the blue action node |
| **Duration** | Automatic — measured from enter to exit |
| **Success/failure** | Automatic — exceptions propagate but get recorded as `action_failed` |
| **Nesting** | Automatic — if you're already inside a tracked action, tool calls become children in the action tree |
| **Function name** | Not available (unlike `@agent.track()` which captures `fn.__qualname__`). Use `ctx.set_payload()` if needed |

### 8.4 The full turn pattern

A typical agentic turn has two parts: the LLM call and the tool execution(s). Here's how to instrument both:

```python
import time
from myproject.observability import get_current_task

def run_turn(hiveloop_agent, messages, tool_definitions):
    # 1. LLM call — the agent reasons and decides what tools to use
    start = time.perf_counter()
    response = llm.chat(messages, tools=tool_definitions)
    elapsed = (time.perf_counter() - start) * 1000

    task = get_current_task()
    if task:
        try:
            task.llm_call(
                "agent_turn",
                model=response.model,
                tokens_in=response.usage.input_tokens,
                tokens_out=response.usage.output_tokens,
                duration_ms=round(elapsed),
            )
        except Exception:
            pass

    # 2. Tool execution — zero or more per turn
    for tool_call in response.tool_calls:
        with hiveloop_agent.track_context(tool_call.name) as ctx:
            result = tool_registry.execute(tool_call.name, tool_call.arguments)
```

On the dashboard timeline, this produces:

```
[■ agent_turn] → [● search_crm] → [● score_lead] → [■ agent_turn] → [● send_email]
  claude-sonnet      0.8s              0.2s            claude-sonnet      1.1s
```

Purple LLM nodes for the reasoning, blue action nodes for each tool, all in sequence with durations.

### 8.5 Attaching tool metadata

Use `ctx.set_payload()` inside the context manager to add tool arguments, results, or other data. This shows up when you click the action node on the timeline:

```python
with hiveloop_agent.track_context(tool_call.name) as ctx:
    result = tool_registry.execute(tool_call.name, tool_call.arguments)
    ctx.set_payload({
        "args": {k: str(v)[:100] for k, v in tool_call.arguments.items()},
        "result_preview": str(result)[:200],
    })
```

**Important:** `set_payload()` adds data to the `action_completed` event. If the tool throws before `set_payload()` is reached, the `action_failed` event still captures the exception automatically — you don't need to handle that case.

### 8.6 Turns with zero tool calls

If the LLM decides not to call any tools (e.g., a final answer turn), the `for tool_call in response.tool_calls` loop simply doesn't execute. No action events are emitted — only the LLM call. This is correct: the timeline shows a reasoning node with no tool execution, which tells the operator "the agent answered without using tools."

### 8.7 Error handling

`track_context()` never swallows exceptions. If a tool throws, the exception propagates normally — but a red `action_failed` node appears on the timeline with the exception type and message:

```python
with hiveloop_agent.track_context("crm_search") as ctx:
    result = crm_client.search(query)  # raises ConnectionError
# ConnectionError propagates — but the timeline now shows:
#   [● crm_search] (red, failed)
#     exception_type: ConnectionError
#     exception_message: Connection refused
#     duration: 2.1s
```

If you want to catch the error and continue (e.g., to try the next tool), wrap the context manager in your own try/except:

```python
for tool_call in response.tool_calls:
    try:
        with hiveloop_agent.track_context(tool_call.name) as ctx:
            result = tool_registry.execute(tool_call.name, tool_call.arguments)
    except ToolError as e:
        results.append({"error": str(e)})
        continue
```

The failed action still appears on the timeline (red node), but execution continues.

### 8.8 Nested tool calls

If a tool internally calls another tool (or another tracked function), the nesting is captured automatically:

```python
with hiveloop_agent.track_context("process_lead") as ctx:
    # This tool internally calls sub-tools:
    with hiveloop_agent.track_context("crm_search") as ctx2:
        record = crm_client.search(lead.email)
    with hiveloop_agent.track_context("score_lead") as ctx3:
        score = scorer.score(lead, record)
```

The timeline shows `process_lead` as a parent action with `crm_search` and `score_lead` as children, rendered as a branching tree.

### 8.9 loopCore example

In loopCore, tools are dispatched by the Phase 2 loop. Each tool call from the LLM response is executed sequentially:

```python
from loop_core.observability import get_hiveloop_agent

# In the Phase 2 tool execution loop:
for tool_use in phase2_response.tool_calls:
    hiveloop_agent = get_hiveloop_agent(agent.name)
    if hiveloop_agent:
        with hiveloop_agent.track_context(tool_use.name) as ctx:
            result = tool_runner.execute(tool_use.name, tool_use.input)
            ctx.set_payload({"result_preview": str(result)[:200]})
    else:
        result = tool_runner.execute(tool_use.name, tool_use.input)
```

If you don't want to duplicate the `execute` call, restructure:

```python
for tool_use in phase2_response.tool_calls:
    hiveloop_agent = get_hiveloop_agent(agent.name)
    if hiveloop_agent:
        ctx_mgr = hiveloop_agent.track_context(tool_use.name)
    else:
        from contextlib import nullcontext
        ctx_mgr = nullcontext()

    with ctx_mgr as ctx:
        result = tool_runner.execute(tool_use.name, tool_use.input)
        if ctx and hasattr(ctx, 'set_payload'):
            ctx.set_payload({"result_preview": str(result)[:200]})
```

### 8.10 Dashboard impact

| Element | What appears |
|---------|-------------|
| **Timeline** | Blue action nodes for each tool call, with tool name and duration |
| **Timeline (failed)** | Red action node with exception type and message |
| **Timeline (nested)** | Parent-child branching for nested tool calls |
| **Activity Stream** | `action_started`, `action_completed`, `action_failed` events |
| **Activity Stream — "action" filter** | Shows only tool execution events |

### 8.11 Validation checklist

- [ ] Trigger a turn with 1+ tool calls
- [ ] **Timeline**: Blue action nodes appear with correct tool names
- [ ] **Timeline**: Duration is shown on each node
- [ ] Trigger a tool failure — verify red node with exception details
- [ ] **Activity Stream**: `action_started` / `action_completed` events appear
- [ ] Verify tool nodes appear between LLM call nodes in the correct sequence

---

## 9. Putting It All Together

Here's a complete example showing all methods integrated into a single agent task. This demonstrates how the events interleave to create a full operational narrative.

```python
import hiveloop
from myproject.observability import get_current_task, get_hiveloop_agent

hb = hiveloop.init(api_key="hb_live_xxx", endpoint="http://localhost:8000")

agent = hb.agent(
    "lead-qualifier",
    type="sales",
    queue_provider=lambda: {"depth": work_queue.qsize()},    # queue snapshot (automatic)
)

def process_lead(lead):
    with agent.task(f"lead-{lead.id}", project="sales-pipeline", type="lead_processing") as task:

        # Create a plan
        task.plan("Qualify and route lead", [
            "Search CRM for existing record",
            "Score lead against criteria",
            "Enrich with external data",
            "Route to sales rep",
        ])

        # Step 0: CRM search — tracked as a tool execution
        task.plan_step(0, "started", "Searching CRM")
        with agent.track_context("search_crm") as ctx:
            crm_record = search_crm(lead.email)
        task.plan_step(0, "completed", f"Found: {crm_record is not None}")

        # Step 1: Score lead — tracked as a tool execution
        task.plan_step(1, "started", "Scoring lead")
        with agent.track_context("score_lead") as ctx:
            score = score_lead(lead, crm_record)
        task.plan_step(1, "completed", f"Score: {score}")

        # Step 2: Enrich — with retry on failure, each attempt tracked
        task.plan_step(2, "started", "Enriching lead data")
        for attempt in range(1, 4):
            try:
                with agent.track_context("enrich_lead") as ctx:
                    enrichment = enrich_lead(lead)
                task.plan_step(2, "completed", "Enrichment succeeded")
                break
            except APITimeoutError as e:
                if attempt < 3:
                    task.retry(f"Enrichment API timeout", attempt=attempt, backoff_seconds=2.0)
                    time.sleep(2.0)
                else:
                    task.plan_step(2, "failed", f"Enrichment failed after 3 attempts")
                    # Report persistent issue
                    agent.report_issue(
                        summary="Enrichment API consistently timing out",
                        severity="high",
                        issue_id="enrichment-timeout",
                        category="connectivity",
                        context={"api": "clearbit", "timeout_ms": 5000},
                    )

        # Step 3: Route — with escalation for low scores
        task.plan_step(3, "started", "Routing lead")
        if score < 0.2:
            task.escalate(
                f"Lead score {score:.2f} — below threshold, needs manual review",
                assigned_to="senior-sales",
            )
            task.plan_step(3, "completed", "Escalated to senior sales")
        else:
            with agent.track_context("assign_to_rep") as ctx:
                assign_to_rep(lead, score)
            task.plan_step(3, "completed", f"Assigned to {get_rep(score)}")

# Later, when the API recovers:
agent.resolve_issue("Enrichment API recovered", issue_id="enrichment-timeout")
```

On the dashboard, this task's timeline would show:

```
PLAN: [■ Search CRM] [■ Score lead] [■ Enrich data] [■ Route lead]
            completed      completed    completed       completed

TIMELINE:
  [started] → [● search_crm 0.8s] → [● score_lead 0.2s]
     → [● enrich_lead ✗] → [retry #1] → [● enrich_lead ✗] → [retry #2]
     → [● enrich_lead 1.1s] → [▲ escalate] → [● assign_to_rep 0.3s]
     → [completed]
```

Blue `●` nodes are tool executions (from `track_context`), red `✗` marks failed attempts, amber `▲` is the escalation.

Plus:
- Agent card shows queue badge from `queue_provider`
- If enrichment fails 3 times, red issue badge appears on the agent card
- Escalation event appears in Activity Stream under "human" filter
- Retry nodes show attempt count and backoff timing
- Each tool node shows duration — click to see payload details

---

## 10. Finding Integration Points in Your Codebase

Here's a systematic approach to finding where each method belongs in any agentic framework:

### 10.1 Use this prompt with your LLM

Ask Claude (or your preferred LLM) to analyze your codebase:

> *"In [your codebase], trace the execution path from the agent's main loop to task completion. For each of the following, identify the file and line where it would go:*
>
> 1. *Where does the agent create a plan or strategy? → `task.plan()`*
> 2. *Where does the agent iterate through plan steps? → `task.plan_step()`*
> 3. *Where does the agent decide to hand off to a human? → `task.escalate()`*
> 4. *Where does the agent detect persistent problems (not single failures)? → `agent.report_issue()`*
> 5. *Where is the work queue managed? → `agent.queue_snapshot()` or `queue_provider`*
> 6. *Where does the agent retry after failure? → `task.retry()`*
>
> *For each, give the file path, line number, and a code snippet showing the integration point."*

### 10.2 Search patterns by framework type

| Framework type | Plans | Escalations | Issues | Queue | Retries |
|---------------|-------|-------------|--------|-------|---------|
| **Custom loop** | Look for step lists in the main loop | Look for threshold checks or "give up" logic | Look for circuit breakers or error counters | Look for the queue data structure | Look for `for attempt in range` or `while` retry loops |
| **LangChain** | Agent's `plan()` method or chain-of-thought | `HumanApprovalCallbackHandler` usage | Custom tool error handling | `CallbackManager` or custom queue | `RetryOutputParser` or custom retry logic |
| **CrewAI** | Crew's task planning phase | `human_input=True` on tasks | Agent error handling callbacks | Crew's task queue | Built-in retry mechanisms |
| **AutoGen** | Multi-agent conversation planning | `human_input_mode="ALWAYS"` | Agent failure handling | Message queue between agents | `max_consecutive_auto_reply` |
| **FastAPI agent** | Request processing pipeline | Error responses that route to humans | Health check endpoints | Request queue (Redis, SQS) | Middleware retry logic |

### 10.3 The priority order

If you're adding all five, do it in this order (highest value first):

1. **`agent.report_issue()`** — low effort, high value. Find 2-3 places where you log warnings about persistent problems and add `report_issue()`. Immediate pipeline tab visibility.

2. **`task.plan()` + `task.plan_step()`** — medium effort. If your agent creates plans, this gives you the progress bar. If it doesn't create plans, skip this entirely.

3. **`task.escalate()`** — low effort. Find the handoff point(s) and add one line each. Immediate Activity Stream visibility.

4. **`agent.queue_snapshot()` or `queue_provider`** — low effort if you have a queue. Register the callback at agent creation time and forget about it.

5. **`task.retry()`** — medium effort. Depends on how many retry patterns exist. Start with the most common retry loop.

---

## 11. Validation Checklists

After adding each method, verify on the dashboard.

### 11.1 Plans

- [ ] Trigger a task that creates a plan
- [ ] **Timeline**: Plan progress bar appears above the timeline track
- [ ] **Timeline**: Steps show correct colors (gray → blue → green or red)
- [ ] **Activity Stream**: `plan_created` event appears with goal and step count
- [ ] **Activity Stream**: `plan_step` events appear as steps progress
- [ ] Trigger a step failure — verify red segment in plan bar

### 11.2 Escalations

- [ ] Trigger a task that escalates
- [ ] **Timeline**: Amber escalation node appears with summary text
- [ ] **Activity Stream**: `escalated` event appears
- [ ] **Activity Stream**: "human" filter includes the escalation event

### 11.3 Issues

- [ ] Trigger an issue report (e.g., fail an API call 3 times)
- [ ] **Agent card**: Red issue badge appears (e.g., "● 1 issue")
- [ ] **Pipeline tab**: Issue appears with correct severity and category
- [ ] Resolve the issue — verify badge disappears
- [ ] **Activity Stream**: Both report and resolve events appear

### 11.4 Queue snapshots

- [ ] Register `queue_provider` on agent
- [ ] Wait 30 seconds (one heartbeat cycle)
- [ ] **Agent card**: Queue badge appears (e.g., "Q:3")
- [ ] **Pipeline tab**: Queue section shows items (if `items` array provided)
- [ ] Add items to queue — verify badge count increases on next heartbeat

### 11.5 Retries

- [ ] Trigger a task that retries an operation
- [ ] **Timeline**: Retry nodes appear with attempt number
- [ ] **Activity Stream**: `retry_started` events appear

### 11.6 Tool execution tracking

- [ ] Trigger a turn with 1+ tool calls
- [ ] **Timeline**: Blue action nodes appear with correct tool names
- [ ] **Timeline**: Duration is shown on each node
- [ ] Trigger a tool failure — verify red node with exception details
- [ ] **Activity Stream**: `action_started` / `action_completed` events appear
- [ ] Verify tool nodes appear between LLM call nodes in the correct sequence

---

## 12. Common Mistakes

### 12.1 Reporting issues inside task failure handlers

```python
# ❌ Wrong — this reports an issue for every single failure:
except Exception as e:
    agent.report_issue(summary=str(e), severity="high")
```

Issues are for **persistent** problems, not individual failures. Track failure counts and only report when a threshold is crossed:

```python
# ✅ Correct — only report after repeated failures:
except Exception as e:
    self.failure_count += 1
    if self.failure_count >= 3:
        agent.report_issue(
            summary=f"API consistently failing: {e}",
            severity="high",
            issue_id="api-failure",
            occurrence_count=self.failure_count,
        )
```

### 12.2 Forgetting to resolve issues

If you report an issue but never resolve it, the red badge stays on the agent card permanently. Always pair `report_issue()` with `resolve_issue()`:

```python
# Report
agent.report_issue(..., issue_id="crm-403")

# Later, when the problem goes away:
agent.resolve_issue("CRM API recovered", issue_id="crm-403")
```

### 12.3 Using `queue_snapshot()` too frequently

Don't call `queue_snapshot()` on every enqueue/dequeue — it generates events. Use `queue_provider` for automatic periodic reporting, or call `queue_snapshot()` at most once per processing cycle.

### 12.4 Plan step indices off by one

`task.plan()` creates zero-indexed steps. The first step is index 0, not 1:

```python
task.plan("My plan", ["Step A", "Step B", "Step C"])
task.plan_step(0, "started", "Step A")    # ✅ correct
task.plan_step(1, "started", "Step A")    # ❌ wrong — this is Step B
```

### 12.5 Escalating when you mean to request approval

If the agent **stops and waits**, use `task.request_approval()`, not `task.escalate()`. Escalation means the agent hands off and moves on. Approval means the agent pauses until a human responds.

### 12.6 Not using `issue_id`

Without `issue_id`, the server deduplicates issues by hashing the summary text. If the summary includes variable data (timestamps, counts), each report creates a new issue instead of updating the existing one:

```python
# ❌ Every call creates a new issue (summary changes each time):
agent.report_issue(
    summary=f"API failed {count} times as of {datetime.now()}",
    severity="high",
)

# ✅ Updates the same issue each time:
agent.report_issue(
    summary="API consistently failing",
    severity="high",
    issue_id="api-failure",
    occurrence_count=count,
)
```

### 12.7 Calling task methods outside a task context

`task.plan()`, `task.escalate()`, and `task.retry()` are **task-scoped** — they require an active task. If you call them outside a task context, they won't work.

`agent.report_issue()` and `agent.queue_snapshot()` are **agent-scoped** — they work anywhere, with or without a task.

```python
# ✅ Task-scoped methods — call on the task object:
with agent.task(task_id) as task:
    task.plan(...)
    task.escalate(...)
    task.retry(...)

# ✅ Agent-scoped methods — call on the agent object:
agent.report_issue(...)      # works anywhere
agent.queue_snapshot(...)    # works anywhere
```

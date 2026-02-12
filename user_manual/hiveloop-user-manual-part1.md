# HiveLoop — User Manual

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *Add observability to any AI agent in 3 lines. See everything on HiveBoard.*

---

## Table of Contents

1. [What is HiveLoop](#1-what-is-hiveloop)
2. [Quick Start](#2-quick-start)
3. [Core Concepts](#3-core-concepts)
4. [Layer 0 — Init + Heartbeat](#4-layer-0--init--heartbeat)
5. [Layer 1 — Decorators + Task Context](#5-layer-1--decorators--task-context)
6. [Layer 2 — Rich Events](#6-layer-2--rich-events)
7. [Integration Patterns](#7-integration-patterns)
8. [Plumbing Patterns](#8-plumbing-patterns)
9. [Configuration Reference](#9-configuration-reference)
10. [API Reference](#10-api-reference)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. What is HiveLoop

HiveLoop is a lightweight Python SDK that makes AI agents observable. You add it to your agent's code, and it streams telemetry to [HiveBoard](https://hiveboard.io) — a real-time dashboard that shows what your agents are doing, why they failed, how long they took, and how much they cost.

**HiveLoop is not a framework.** It doesn't change how your agent works. It watches what your agent does and reports it. Your agent's logic stays exactly the same.

**The model:** HiveLoop is the instrument. HiveBoard is the dashboard. Think Sentry SDK → Sentry Dashboard.

```
Your Agent Code
    ↓ (add decorators + events)
HiveLoop SDK
    ↓ (batched HTTP)
HiveBoard Server
    ↓ (real-time)
Dashboard + Alerts
```

### What you get

| Layer | Effort | What you see on HiveBoard |
|-------|--------|---------------------------|
| Layer 0 | 3 lines | Agent appears with live heartbeat, stuck detection, online/offline status |
| Layer 1 | Add decorators | Task timelines with action tracking, duration, success/failure |
| Layer 2 | Sprinkle events | LLM costs, plans, escalations, approvals, retries — the full narrative |

Each layer is independent. Start with Layer 0, stop whenever you have enough visibility.

---

## 2. Quick Start

### Install

```bash
pip install hiveloop
```

### 3 lines to first heartbeat

```python
import hiveloop

hb = hiveloop.init(api_key="hb_live_your_key_here")
agent = hb.agent("my-agent", type="general")
```

That's it. Your agent now appears on the HiveBoard dashboard with a live heartbeat. If it stops, HiveBoard notices within 5 minutes and marks it as stuck.

### Add task tracking (5 more lines)

```python
with agent.task("task-123", project="my-project", type="processing") as task:
    result = do_work()

    task.llm_call("reasoning", model="claude-sonnet-4-20250514",
                  tokens_in=1500, tokens_out=200, cost=0.003)
```

Now you have a task timeline with LLM cost tracking in the Cost Explorer.

---

## 3. Core Concepts

### 3.1 The hierarchy

```
HiveBoard Instance (your account)
  └── Agent (a running process — "lead-qualifier", "support-triage")
        └── Task (a unit of work — "process lead #4801", "triage ticket #1002")
              └── Action (a step within a task — "score_lead", "call_llm", "send_email")
                    └── Event (a specific moment — LLM call, plan step, escalation)
```

### 3.2 Agents

An agent is anything that runs autonomously and does work. It could be a LangChain agent, a CrewAI crew member, a custom Python loop, or a cron job with LLM calls. HiveLoop doesn't care about the implementation — it tracks the agent as a named entity with a heartbeat.

**Key properties:**
- `agent_id` — unique name (e.g. `"lead-qualifier"`, `"support-bot"`)
- `type` — classification (e.g. `"sales"`, `"support"`, `"etl"`)
- Heartbeat — automatic background ping every N seconds
- Status — derived by HiveBoard from the heartbeat and recent events

**Agent status (derived by HiveBoard, not set by you):**

| Status | Meaning |
|--------|---------|
| `idle` | Agent is alive (heartbeat active) but not processing a task |
| `processing` | Agent is actively working on a task |
| `waiting_approval` | Agent has requested human approval |
| `error` | Last task failed |
| `stuck` | No heartbeat received within the stuck threshold |

### 3.3 Tasks

A task is a unit of work an agent performs. It has a start, an end, and a result (completed or failed). Tasks contain actions and events that form a timeline — the step-by-step narrative of what happened.

**Examples of tasks:**
- Processing a sales lead
- Triaging a support ticket
- Running an ETL batch
- Generating a report
- Responding to a user query

### 3.4 Actions

An action is a tracked function call within a task. When you decorate a function with `@agent.track()`, every call automatically emits `action_started` and `action_completed` (or `action_failed`) events with timing data. Actions can nest — an action inside another action creates a parent-child relationship in the timeline.

### 3.5 Events

Events are the atomic data points. Everything in HiveBoard is derived from events: status, timelines, metrics, costs, alerts. There are 13 event types:

| Type | Emitted by | When |
|------|-----------|------|
| `agent_registered` | `hb.agent()` | Agent first registers |
| `heartbeat` | Automatic | Every heartbeat interval |
| `task_started` | `agent.task()` | Task context entered |
| `task_completed` | `agent.task()` | Task context exited cleanly |
| `task_failed` | `agent.task()` | Exception inside task context |
| `action_started` | `@agent.track()` | Decorated function called |
| `action_completed` | `@agent.track()` | Decorated function returned |
| `action_failed` | `@agent.track()` | Decorated function raised |
| `escalated` | `task.escalate()` | Manual escalation |
| `approval_requested` | `task.request_approval()` | Awaiting human decision |
| `approval_received` | `task.approval_received()` | Human decision received |
| `retry_started` | `task.retry()` | Retrying after failure |
| `custom` | `task.event()` / convenience methods | Anything else (LLM calls, plans, issues, etc.) |

---

## 4. Layer 0 — Init + Heartbeat

### 4.1 Initialize

Call `hiveloop.init()` once at your application's startup — wherever your agent process begins.

```python
import hiveloop

hb = hiveloop.init(
    api_key="hb_live_your_key_here",
    endpoint="http://localhost:8000",   # default: https://api.hiveboard.io
    environment="production",            # appears as a filter on the dashboard
    group="my-team",                     # organizational grouping
)
```

`hiveloop.init()` is a singleton — calling it twice returns the same instance and logs a warning. To reinitialize (e.g. in tests), call `hiveloop.reset()` first.

**What happens on init:**
1. API key format validated
2. Internal event queue created (thread-safe)
3. Background flush thread started (daemon thread)
4. No HTTP call is made — first server contact happens on the first flush

### 4.2 Register an agent

```python
agent = hb.agent(
    "my-agent",
    type="general",
    version="1.2.0",
    heartbeat_interval=30,     # seconds between heartbeats (default: 30)
    stuck_threshold=300,       # seconds before HiveBoard marks agent as stuck (default: 300)
)
```

**What happens on registration:**
1. `agent_registered` event emitted
2. Background heartbeat thread started (daemon, automatic)
3. Agent appears on the HiveBoard dashboard within seconds

**`hb.agent()` is idempotent** — calling it twice with the same `agent_id` returns the same agent handle. This is safe and expected for dynamic agent creation.

### 4.3 Multiple agents

Register as many agents as you need. Each gets its own heartbeat thread:

```python
sales_agent = hb.agent("lead-qualifier", type="sales")
support_agent = hb.agent("support-triage", type="support")
etl_agent = hb.agent("data-pipeline", type="etl")
```

For dynamic agent creation (agents created at runtime), call `hb.agent()` at the moment each agent is created. There is no explicit "unregister" — when an agent stops, its heartbeat stops, and HiveBoard marks it as stuck/offline after `stuck_threshold` seconds.

### 4.4 Heartbeat enrichment

Add custom data to each heartbeat for richer dashboard cards:

```python
agent = hb.agent(
    "my-agent",
    type="general",
    heartbeat_payload=lambda: {
        "kind": "heartbeat_status",
        "summary": f"Queue depth: {get_queue_size()}",
        "data": {
            "queue_depth": get_queue_size(),
            "memory_mb": get_memory_usage(),
            "uptime_seconds": get_uptime(),
        },
    },
)
```

### 4.5 Queue visibility

If your agent processes a work queue, provide a queue provider callback:

```python
agent = hb.agent(
    "my-agent",
    type="general",
    queue_provider=lambda: {
        "depth": work_queue.qsize(),
        "oldest_age_seconds": get_oldest_item_age(),
        "items": [
            {"id": item.id, "priority": item.priority, "summary": item.summary}
            for item in list(work_queue.queue)[:10]
        ],
    },
)
```

The queue snapshot is sent with each heartbeat and displayed in the Pipeline view on the dashboard.

### 4.6 Shutdown

When your process exits, flush remaining events:

```python
hiveloop.shutdown(timeout=10)  # waits up to 10 seconds for pending events to send
```

HiveLoop also registers an `atexit` handler automatically, but explicit shutdown is recommended for reliable event delivery.

---

## 5. Layer 1 — Decorators + Task Context

### 5.1 Task context manager

Wrap a unit of work in `agent.task()` to track it as a task:

```python
with agent.task("task-123", project="my-project", type="lead_processing") as task:
    # Everything inside this block is part of the task
    result = process_lead(lead)
```

**Automatic behavior:**
- `task_started` event emitted on entry
- `task_completed` event emitted on clean exit
- `task_failed` event emitted if an exception propagates out
- The exception is **never swallowed** — it re-raises after being recorded
- Duration is tracked automatically

**Parameters:**
- `task_id` (required) — unique identifier for this task
- `project` (optional) — which project this task belongs to
- `type` (optional) — task classification (e.g. `"lead_processing"`, `"ticket_triage"`)

### 5.2 Non-context-manager task API

For situations where context managers don't fit (e.g. callback-driven architectures):

```python
task = agent.start_task("task-123", project="my-project", type="processing")

try:
    result = do_work()
    task.complete(payload={"result": result})
except Exception as e:
    task.fail(exception=e)
    raise
```

### 5.3 Decorators

Track function calls as actions within a task:

```python
@agent.track("score_lead")
def score_lead(lead_data):
    # HiveLoop automatically captures:
    # - action_started (with function name)
    # - action_completed (with duration) or action_failed (with exception)
    return calculate_score(lead_data)
```

Works with both sync and async functions:

```python
@agent.track("fetch_data")
async def fetch_data(url):
    async with httpx.AsyncClient() as client:
        return await client.get(url)
```

### 5.4 Nested actions

Actions called inside other actions nest automatically:

```python
@agent.track("process_lead")
def process_lead(lead):
    score = score_lead(lead)       # nested action
    enrichment = enrich_lead(lead)  # nested action
    route_lead(lead, score)         # nested action

@agent.track("score_lead")
def score_lead(lead):
    return llm_score(lead)

@agent.track("enrich_lead")
def enrich_lead(lead):
    return api_enrich(lead)

@agent.track("route_lead")
def route_lead(lead, score):
    return assign_to_rep(lead, score)
```

The timeline shows:
```
process_lead (3.2s)
  ├── score_lead (1.1s)
  ├── enrich_lead (0.8s)
  └── route_lead (1.3s)
```

### 5.5 Exception handling

**HiveLoop never swallows exceptions.** If your function raises, the exception propagates normally. HiveLoop records the failure and re-raises:

```python
@agent.track("risky_operation")
def risky():
    raise ValueError("something broke")

try:
    risky()  # action_failed event emitted, then ValueError re-raises
except ValueError:
    handle_error()
```

---

## 6. Layer 2 — Rich Events

Layer 2 is where the timeline goes from "function X ran for 4.8 seconds" to "lead scored 42 against threshold 80, used claude-sonnet at $0.003, and was escalated for review." These are manual event calls — typically 5-15 across your entire codebase.

### 6.1 LLM call tracking

The single most valuable Layer 2 event. Powers the Cost Explorer.

```python
task.llm_call(
    "lead_scoring",                          # call name
    "claude-sonnet-4-20250514",              # model
    tokens_in=1500,                          # input tokens (optional)
    tokens_out=200,                          # output tokens (optional)
    cost=0.003,                              # USD cost (optional)
    duration_ms=1200,                        # LLM latency (optional)
    prompt_preview="You are analyzing...",   # first ~500 chars (optional)
    response_preview='{"score": 42}',        # first ~500 chars (optional)
)
```

**All fields except name and model are optional.** Start by logging just the call name and model — add token counts and cost later. This enables incremental instrumentation.

For LLM calls outside a task context (e.g. agent-level summarization):

```python
agent.llm_call("summarize_context", "claude-haiku-4-5-20251001",
               tokens_in=3000, tokens_out=500, cost=0.001)
```

**Cost calculation helper** (if your LLM client doesn't return cost):

```python
MODEL_COSTS = {
    "claude-sonnet-4-20250514": (0.003, 0.015),       # per 1K tokens (in, out)
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
}

def calculate_cost(model, tokens_in, tokens_out):
    costs = MODEL_COSTS.get(model, (0.001, 0.005))
    return round((tokens_in / 1000) * costs[0] + (tokens_out / 1000) * costs[1], 6)
```

### 6.2 Plans

When your agent creates a plan or sequence of steps:

```python
task.plan("Process lead #4801", ["Score lead", "Enrich data", "Route to rep"])
```

As each step progresses:

```python
task.plan_step(0, "started", "Scoring lead")
# ... work happens ...
task.plan_step(0, "completed", "Lead scored 87", turns=1, tokens=2500)

task.plan_step(1, "started", "Enriching lead data")
# ... work happens ...
task.plan_step(1, "failed", "Enrichment API timeout")
# ... retry ...
task.plan_step(1, "completed", "Enrichment succeeded on retry")
```

Plan progress is displayed as a progress bar on the dashboard timeline.

### 6.3 Escalations

When your agent escalates to a human or another system:

```python
task.escalate(
    "Complex billing issue — needs senior review",
    assigned_to="senior-support",
)
```

### 6.4 Approvals

When your agent needs human approval:

```python
task.request_approval(
    "Approval needed for account credit of $500",
    approver="support-lead",
)
```

When the approval comes back:

```python
task.approval_received(
    "Credit approved",
    approved_by="support-lead",
    decision="approved",    # or "rejected"
)
```

### 6.5 Retries

When your agent retries after a failure:

```python
task.retry("Retrying after API timeout", attempt=2, backoff_seconds=4.0)
```

### 6.6 Issues

Agent-level problems (not tied to a specific task):

```python
agent.report_issue(
    summary="CRM API consistently timing out",
    severity="high",               # "low", "medium", "high", "critical"
    issue_id="crm-timeout",        # optional — for dedup and resolution tracking
    category="connectivity",       # optional
    context={"api": "salesforce", "timeout_ms": 5000},  # optional
)
```

When the issue resolves:

```python
agent.resolve_issue("CRM API recovered", issue_id="crm-timeout")
```

### 6.7 TODOs

Agent-managed work items:

```python
agent.todo("todo-kb-update", "created",
           "Update knowledge base with new product FAQ",
           priority="normal", source="agent_decision")
```

### 6.8 Scheduled work

Report recurring/scheduled tasks:

```python
agent.scheduled(items=[
    {"id": "sched-sync", "name": "CRM Pipeline Sync",
     "next_run": "2026-02-12T15:00:00Z", "interval": "1h",
     "enabled": True, "last_status": "success"},
    {"id": "sched-cleanup", "name": "Stale data cleanup",
     "next_run": "2026-02-13T08:00:00Z", "interval": "daily",
     "enabled": True, "last_status": None},
])
```

### 6.9 Custom events

For anything that doesn't fit the above:

```python
task.event("custom_event_type", {
    "kind": "my_custom_kind",
    "summary": "Something interesting happened",
    "data": {"key": "value"},
})
```

---

## 7. Integration Patterns

### 7.1 Single long-running agent

The simplest pattern. One process, one agent, tasks processed sequentially.

```python
import hiveloop

hb = hiveloop.init(api_key="hb_live_xxx")
agent = hb.agent("my-agent", type="processor")

while True:
    item = queue.get()

    with agent.task(item.id, project="my-project", type="processing") as task:
        result = process(item)
        task.llm_call("analyze", model="...", tokens_in=N, tokens_out=N, cost=X)

    time.sleep(1)
```

### 7.2 Multiple static agents

Fixed number of agents, each in its own thread or process.

```python
hb = hiveloop.init(api_key="hb_live_xxx")

sales_agent = hb.agent("lead-qualifier", type="sales")
support_agent = hb.agent("support-triage", type="support")
etl_agent = hb.agent("data-pipeline", type="etl")

# Each agent runs in its own thread
threading.Thread(target=run_sales, args=(sales_agent,), daemon=True).start()
threading.Thread(target=run_support, args=(support_agent,), daemon=True).start()
threading.Thread(target=run_etl, args=(etl_agent,), daemon=True).start()
```

### 7.3 Dynamic agent creation

Agents are created and destroyed at runtime (e.g. multi-tenant platforms, on-demand workers).

```python
hb = hiveloop.init(api_key="hb_live_xxx")

# Agent registry
_agents: dict[str, hiveloop.Agent] = {}

def create_agent(agent_id: str, agent_type: str):
    """Called whenever your system creates a new agent."""
    _agents[agent_id] = hb.agent(agent_id, type=agent_type)
    return _agents[agent_id]

def get_agent(agent_id: str):
    return _agents.get(agent_id)

def remove_agent(agent_id: str):
    """Called when an agent is removed. Heartbeat stops → HiveBoard shows offline."""
    _agents.pop(agent_id, None)
    # No explicit unregister — heartbeat stops, HiveBoard notices
```

### 7.4 API-driven agents (FastAPI / Flask)

Agent work is triggered by HTTP requests.

```python
# startup
hb = hiveloop.init(api_key="hb_live_xxx")
agent = hb.agent("api-agent", type="api")

@app.post("/process")
async def process_request(request: ProcessRequest):
    with agent.task(request.id, project="api-service", type="request") as task:
        result = await handle(request)
        return result
```

### 7.5 Queue-driven agents

Agent processes items from a message queue.

```python
hb = hiveloop.init(api_key="hb_live_xxx")
agent = hb.agent("queue-worker", type="worker",
    queue_provider=lambda: {"depth": queue.qsize()},
)

def worker():
    while True:
        message = queue.get()

        with agent.task(message.id, project="queue-service", type=message.type) as task:
            process(message)

        queue.task_done()
```

### 7.6 Framework callbacks (LangChain, CrewAI, AutoGen)

For established frameworks, HiveLoop provides callback integrations that plug into the framework's native hook system. These map framework events to HiveLoop events automatically.

**LangChain:**

```python
from hiveloop.integrations.langchain import LangChainCallback

hb = hiveloop.init(api_key="hb_live_xxx")
callback = LangChainCallback(hb, project="my-project")
agent = initialize_agent(tools, llm, callbacks=[callback])
```

**CrewAI:**

```python
from hiveloop.integrations.crewai import CrewAICallback

hb = hiveloop.init(api_key="hb_live_xxx")
callback = CrewAICallback(hb, project="my-project")
crew = Crew(agents=[...], callbacks=[callback])
```

**AutoGen:**

```python
from hiveloop.integrations.autogen import AutoGenCallback

hb = hiveloop.init(api_key="hb_live_xxx")
callback = AutoGenCallback(hb, project="my-project")
```

> **Note:** Framework integrations are planned for a future release. For now, use the core SDK directly with decorators and manual events.

---

## 8. Plumbing Patterns

The most common integration challenge: the `task` object is created at one level (where work begins) but needed at a deeper level (where LLM calls happen). Here are three patterns to solve this.

### 8.1 Pass as parameter (simplest)

Thread the task object through your function calls:

```python
with agent.task(task_id, project="my-project") as task:
    result = process(item, task=task)

def process(item, task=None):
    score = score_item(item, task=task)
    if task:
        task.llm_call("scoring", model="...", tokens_in=N, tokens_out=N)
    return score
```

**Best for:** Small codebases, shallow call stacks.

### 8.2 Context variables (recommended)

Use Python's `contextvars` for implicit task propagation. This is thread-safe and async-safe.

```python
import contextvars

_current_task = contextvars.ContextVar('hiveloop_task', default=None)

def set_current_task(task):
    _current_task.set(task)

def get_current_task():
    return _current_task.get()
```

At the top level:

```python
with agent.task(task_id, project="my-project") as task:
    set_current_task(task)
    try:
        result = process(item)
    finally:
        set_current_task(None)
```

Anywhere deeper in the code:

```python
def deep_function():
    task = get_current_task()
    if task:
        task.llm_call("reasoning", model="...", tokens_in=N, tokens_out=N)
```

**Best for:** Large codebases, deep call stacks, async code.

### 8.3 Store on execution context

If your framework has an execution context or request object that flows through the call stack, attach the task to it:

```python
# FastAPI example
@app.post("/process")
async def process(request: Request):
    with agent.task(task_id, project="my-project") as task:
        request.state.hiveloop_task = task
        result = await handle(request)

# Deeper in the code
def handle(request):
    task = getattr(request.state, 'hiveloop_task', None)
    if task:
        task.llm_call(...)
```

**Best for:** Web frameworks, middleware-based architectures.

### 8.4 Graceful degradation

All patterns above use `if task:` guards. This is deliberate — HiveLoop should never break your agent. If HiveLoop isn't initialized (e.g. in tests, or if the server is down), everything still works. The SDK never raises transport errors.

```python
# This is always safe:
task = get_current_task()
if task:
    task.llm_call(...)
# If task is None, nothing happens. Your agent keeps running.
```

---

## 9. Configuration Reference

### 9.1 `hiveloop.init()` parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | str | **required** | API key. Must start with `hb_`. |
| `endpoint` | str | `"https://api.hiveboard.io"` | HiveBoard server URL. Override for self-hosted or local dev. |
| `environment` | str | `"production"` | Operational context (e.g. `"production"`, `"staging"`). Filterable on dashboard. |
| `group` | str | `"default"` | Organizational label (e.g. `"team-alpha"`, `"region-us"`). Filterable on dashboard. |
| `flush_interval` | float | `5.0` | Seconds between automatic batch flushes. |
| `batch_size` | int | `100` | Max events per HTTP request. Server caps at 500. |
| `max_queue_size` | int | `10000` | Max events buffered in memory. Oldest dropped when full. |
| `debug` | bool | `False` | Logs SDK operations to stderr. |

### 9.2 `hb.agent()` parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | str | **required** | Unique agent identifier. Max 256 chars. Human-readable slug recommended. |
| `type` | str | `"general"` | Agent classification. Displayed on dashboard cards. |
| `version` | str | `None` | Agent version. Useful for comparing performance across deploys. |
| `framework` | str | `"custom"` | Framework identifier: `"langchain"`, `"crewai"`, `"autogen"`, `"custom"`. |
| `heartbeat_interval` | int | `30` | Seconds between automatic heartbeats. Set to `0` to disable. |
| `stuck_threshold` | int | `300` | Seconds without heartbeat before HiveBoard marks agent as stuck. |
| `heartbeat_payload` | Callable | `None` | Callback returning dict to include in each heartbeat event. |
| `queue_provider` | Callable | `None` | Callback returning queue state dict, sent with each heartbeat. |

### 9.3 `agent.task()` parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task_id` | str | **required** | Unique task identifier. |
| `project` | str | `None` | Project this task belongs to. Must exist on the server. |
| `type` | str | `None` | Task type classification. |

### 9.4 API key types

| Prefix | Type | Permissions |
|--------|------|-------------|
| `hb_live_` | Live | Full read/write. Used in production. |
| `hb_test_` | Test | Read/write, but data is isolated from live data. |
| `hb_read_` | Read-only | Query only. Cannot ingest events. |

---

## 10. API Reference

### 10.1 Module-level functions

| Function | Description |
|----------|-------------|
| `hiveloop.init(**kwargs)` | Initialize the SDK. Returns `HiveBoard` client instance. Singleton. |
| `hiveloop.shutdown(timeout=10)` | Flush remaining events and stop all threads. |
| `hiveloop.reset()` | Flush, stop threads, clear singleton. For testing. |
| `hiveloop.flush()` | Immediately flush buffered events without shutting down. |

### 10.2 `HiveBoard` client (`hb`)

| Method | Returns | Description |
|--------|---------|-------------|
| `hb.agent(agent_id, **kwargs)` | `Agent` | Register an agent. Idempotent — same ID returns same instance. |

### 10.3 `Agent`

| Method | Returns | Description |
|--------|---------|-------------|
| `agent.task(task_id, **kwargs)` | `Task` (context manager) | Start a tracked task. |
| `agent.start_task(task_id, **kwargs)` | `Task` | Start a task without context manager. Call `.complete()` or `.fail()` manually. |
| `agent.track(action_name)` | decorator | Decorator that tracks function calls as actions. |
| `agent.track_context(action_name)` | context manager | Context manager version of track. |
| `agent.event(event_type, payload)` | `None` | Emit an agent-level custom event (no task context). |
| `agent.llm_call(name, model, **kwargs)` | `None` | Log an LLM call outside a task context. |
| `agent.queue_snapshot(**kwargs)` | `None` | Explicitly report queue state. |
| `agent.todo(todo_id, action, summary, **kwargs)` | `None` | Report a TODO item lifecycle event. |
| `agent.scheduled(items=[...])` | `None` | Report scheduled/recurring work. |
| `agent.report_issue(summary, **kwargs)` | `None` | Report an agent-level issue. |
| `agent.resolve_issue(summary, **kwargs)` | `None` | Resolve a previously reported issue. |

### 10.4 `Task`

| Method | Returns | Description |
|--------|---------|-------------|
| `task.event(event_type, payload)` | `None` | Emit a custom event within this task. |
| `task.llm_call(name, model, **kwargs)` | `None` | Log an LLM call within this task. |
| `task.plan(goal, steps)` | `None` | Declare a plan with a goal and step names. |
| `task.plan_step(index, status, summary, **kwargs)` | `None` | Update a plan step's status. |
| `task.escalate(reason, **kwargs)` | `None` | Escalate this task. |
| `task.request_approval(reason, **kwargs)` | `None` | Request human approval. |
| `task.approval_received(summary, **kwargs)` | `None` | Record approval decision. |
| `task.retry(reason, **kwargs)` | `None` | Record a retry attempt. |
| `task.complete(**kwargs)` | `None` | Manually complete task (non-context-manager API). |
| `task.fail(**kwargs)` | `None` | Manually fail task (non-context-manager API). |
| `task.set_payload(payload)` | `None` | Add payload to the task's completion event. |

---

## 11. Troubleshooting

### Events not appearing on dashboard

1. **Check the API key** — must start with `hb_live_` or `hb_test_`
2. **Check the endpoint** — default is `https://api.hiveboard.io`, override for local dev
3. **Enable debug mode** — `hiveloop.init(..., debug=True)` logs all SDK operations to stderr
4. **Call flush** — `hiveloop.flush()` forces immediate send, bypassing the batch timer
5. **Check server logs** — look for 401 (auth), 400 (validation), or 207 (partial rejection)

### 401 Unauthorized

The API key doesn't match what the server has. Common causes:
- Environment variable not set (on Windows PowerShell: `$env:HIVEBOARD_DEV_KEY = "..."`, not `set`)
- Key is for test data but server expects live (or vice versa)

### Events partially rejected (207)

Some events accepted, some rejected. Check the response for per-event errors:
- `invalid_project_id` — project doesn't exist on the server. Create it first via `POST /v1/projects`
- `payload_too_large` — payload exceeds 32KB limit. Truncate previews
- `invalid_event_type` — unrecognized event type string

### Agent shows as "stuck" even though it's running

Heartbeat isn't reaching the server. Check:
- Network connectivity to the HiveBoard endpoint
- `heartbeat_interval` isn't set to `0` (which disables heartbeats)
- The SDK's background flush thread is running (`debug=True` will show flush activity)

### Import errors

- `ModuleNotFoundError: No module named 'hiveloop'` — run `pip install -e C:\path\to\hiveboard\src\sdk`
- If using a virtual environment, make sure HiveLoop is installed in the same environment your agent uses

### Performance

HiveLoop is designed to add zero overhead to your agent:
- Events are queued in memory (never blocks your code)
- Flushed in a background daemon thread
- Transport errors are caught and logged (never raised to your code)
- If the queue fills up (`max_queue_size`), oldest events are silently dropped
- If the server is unreachable, events are retried with exponential backoff (1s, 2s, 4s, 8s, 16s, max 60s)
- 400 errors (client bugs) are not retried — events are dropped to avoid infinite loops

---

## Appendix A: Dashboard Mapping

Where each HiveLoop call appears on the HiveBoard dashboard:

| HiveLoop Call | Dashboard Location |
|---------------|-------------------|
| `hb.agent()` | Agent card in the Hive (left sidebar) |
| Heartbeat (automatic) | Heartbeat indicator on agent card |
| `agent.task()` | Task list in Mission Control (center panel) |
| `@agent.track()` | Action nodes in Task Timeline |
| `task.llm_call()` | Cost Explorer + LLM call detail in Timeline |
| `task.plan()` / `plan_step()` | Plan progress bar in Timeline |
| `task.escalate()` | Activity Stream + Timeline node |
| `task.request_approval()` | Activity Stream + agent status → "waiting_approval" |
| `task.approval_received()` | Activity Stream + Timeline node |
| `task.retry()` | Timeline branch (retry sequence) |
| `agent.report_issue()` | Pipeline view → Issues section |
| `agent.resolve_issue()` | Pipeline view → Issue resolved |
| `agent.todo()` | Pipeline view → TODOs section |
| `agent.scheduled()` | Pipeline view → Scheduled section |
| `agent.queue_snapshot()` | Pipeline view → Queue section + agent card enrichment |

---

## Appendix B: What HiveLoop Does NOT Do

- **Does not change your agent's behavior.** HiveLoop is read-only observation.
- **Does not require any specific framework.** Works with LangChain, CrewAI, AutoGen, or plain Python.
- **Does not make network calls on your code's thread.** All I/O happens in a background thread.
- **Does not raise exceptions from transport errors.** Network failures are logged, never propagated.
- **Does not store data locally.** Events are buffered in memory and shipped to HiveBoard. No local database.
- **Does not require schema migration when you add more instrumentation.** The event schema is the same at every layer — only the populated fields change.

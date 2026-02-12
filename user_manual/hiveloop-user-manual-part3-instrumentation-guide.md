# HiveBoard — User Manual Part 3: Instrumentation Guide

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *How to find WHERE to instrument and HOW to add HiveLoop to any agent codebase.*

---

## Table of Contents

1. [The Instrumentation Mindset](#1-the-instrumentation-mindset)
2. [Layer 0 Recap — Init + Heartbeat](#2-layer-0-recap--init--heartbeat)
3. [Finding WHERE to Add agent.task()](#3-finding-where-to-add-agenttask)
4. [Finding WHERE to Add agent.track()](#4-finding-where-to-add-agenttrack)
5. [Plumbing — Getting the Task Object Where You Need It](#5-plumbing--getting-the-task-object-where-you-need-it)
6. [Implementation Patterns for Classes and Methods](#6-implementation-patterns-for-classes-and-methods)
7. [Validating Your Instrumentation](#7-validating-your-instrumentation)
8. [Adding Layer 2 — Rich Events](#8-adding-layer-2--rich-events)
9. [Architecture-Specific Guidance](#9-architecture-specific-guidance)
10. [The Safety Contract](#10-the-safety-contract)
11. [Incremental Instrumentation Checklist](#11-incremental-instrumentation-checklist)
12. [Common Mistakes](#12-common-mistakes)

---

## 1. The Instrumentation Mindset

Instrumenting an existing codebase is not about adding code everywhere. It's about answering three questions precisely, then making surgical additions.

| Question | What it finds | HiveLoop feature |
|----------|--------------|-----------------|
| **"Where does a unit of work start and end?"** | The task boundary | `agent.task()` |
| **"What are the meaningful steps inside that work?"** | The tracked actions | `@agent.track()` |
| **"How does data flow through the call stack?"** | The plumbing pattern | `contextvars`, parameter passing, or context objects |

Answer these three questions for your codebase and you have your instrumentation plan. Everything else is syntax.

### The golden rule

**Instrument from the outside in.** Start at the highest level (task boundary), then go one level deeper (actions), then add richness (LLM calls, plans, escalations). At every level, verify on the dashboard before going deeper. If a layer doesn't work, you'll know exactly where the problem is.

---

## 2. Layer 0 Recap — Init + Heartbeat

Before starting Layer 1, make sure Layer 0 is working:

- [ ] `hiveloop.init()` is called at application startup
- [ ] `hb.agent()` is called for each agent
- [ ] Agents appear in The Hive on the dashboard
- [ ] Heartbeat dots are green
- [ ] `agent_registered` events appear in the Activity Stream

If any of these are missing, fix Layer 0 first. See Part 1 (Sections 4 and 11) for troubleshooting.

---

## 3. Finding WHERE to Add `agent.task()`

### 3.1 What is a "task"?

A task is the answer to: **"If someone asked what your agent is doing right now, what would you say?"**

Good examples of tasks:
- "Processing lead #4801"
- "Triaging support ticket #1002"
- "Running ETL batch for 2026-02-12"
- "Generating quarterly report"
- "Responding to user query abc123"

A task has a clear beginning, a clear end, and a result (success or failure).

### 3.2 How to find the task boundary

Look for code that matches one of these patterns:

**Pattern A — Loop processing items:**
```python
while True:
    item = queue.get()        # ← task starts here
    result = process(item)    #    work happens
    save(result)              # ← task ends here
```

**Pattern B — Request handler:**
```python
@app.post("/process")
async def handle(request):    # ← task starts here
    result = do_work(request) #    work happens
    return result             # ← task ends here
```

**Pattern C — Callback / event handler:**
```python
def on_event(event):          # ← task starts here
    result = handle(event)    #    work happens
    emit_result(result)       # ← task ends here
```

**Pattern D — Scheduled job:**
```python
@scheduler.every("1h")
def hourly_job():             # ← task starts here
    data = fetch()            #    work happens
    process(data)             # ← task ends here
```

### 3.3 The decision checklist

When evaluating a code location, ask:

| Question | If yes → |
|----------|---------|
| Does this code represent one complete job from start to finish? | This is your task boundary |
| Could I give this a meaningful task ID? (e.g. "process-lead-4801") | Good task boundary |
| Does this code call multiple sub-functions that each do something distinct? | This is a task, and those sub-functions are tracked actions |
| Is this code called repeatedly (once per item, once per request)? | Each call is one task |
| Is this code called once at startup and runs forever? | This is NOT a task — look one level deeper for the per-item work |

### 3.4 Common traps

**Too high:** Wrapping `main()` as a single task. A task should represent one unit of work, not the entire process lifetime.

**Too low:** Wrapping individual LLM calls as tasks. LLM calls happen inside tasks — they're events, not tasks.

**Too many:** Wrapping every function as a task. If you have 50 tasks per user request, your task granularity is too fine. Each task should be something meaningful at the business level.

### 3.5 Adding `agent.task()`

Once you've found the boundary:

```python
# BEFORE:
def process_item(item):
    result = step_one(item)
    result = step_two(result)
    return result

# AFTER:
def process_item(item):
    hiveloop_agent = get_hiveloop_agent()
    if hiveloop_agent:
        with hiveloop_agent.task(
            item.id,                    # unique task ID
            project="my-project",       # project context
            type="item_processing",     # task classification
        ) as task:
            set_current_task(task)      # make accessible deeper in the stack
            try:
                result = step_one(item)
                result = step_two(result)
                return result
            finally:
                set_current_task(None)
    else:
        result = step_one(item)
        result = step_two(result)
        return result
```

**Choosing a task ID:** The ID should be unique per execution and human-readable. Good patterns:
- `f"process-lead-{lead.id}"` — includes the entity ID
- `f"task-{uuid.uuid4().hex[:8]}"` — unique but anonymous
- `event.id` — if your system already has event/job IDs

**Choosing a project:** Projects group related work. If all your agents serve one purpose, one project is fine. If agents serve different domains (sales, support, operations), use different projects.

---

## 4. Finding WHERE to Add `@agent.track()`

### 4.1 What is a "tracked action"?

A tracked action is a function that represents a **meaningful step** in a task's execution. When you're debugging a failed task and reading the timeline, these are the steps you want to see.

### 4.2 The selection criteria

**Good candidates for tracking:**

| Criterion | Example |
|-----------|---------|
| Calls an external service | `fetch_from_crm()`, `call_llm()`, `send_email()` |
| Makes a decision | `route_ticket()`, `classify_intent()`, `score_lead()` |
| Transforms data meaningfully | `enrich_lead()`, `parse_document()`, `generate_report()` |
| Takes significant time (>100ms) | API calls, LLM calls, database queries |
| Has a name a human would understand | `evaluate_lead`, `send_notification`, `create_plan` |
| Could fail independently | Anything that can throw an exception worth knowing about |

**Bad candidates for tracking:**

| Criterion | Example |
|-----------|---------|
| Internal utilities | `format_date()`, `validate_email()`, `build_prompt()` |
| Runs in microseconds | Getters, setters, string formatting |
| Called hundreds of times per task | Loop iterations, per-item validators |
| Has no external side effect | Pure computation, in-memory transformations |
| Would clutter the timeline | If you'd have 50+ nodes for one task, you're tracking too much |

### 4.3 The "5-7 nodes" rule

A good task timeline has roughly **5 to 7 major action nodes**. This is enough to tell the story of what happened without overwhelming the viewer. If you're tracking more than 10-15 functions per task, you're probably tracking too deep.

Example of a well-balanced timeline:
```
[task_started] → [fetch_data] → [llm_reasoning] → [score_lead] → [route_lead] → [task_completed]
```

Example of an over-instrumented timeline:
```
[task_started] → [validate_input] → [format_request] → [build_headers] → [open_connection] → 
[send_request] → [parse_json] → [extract_fields] → [validate_response] → [build_prompt] → 
[count_tokens] → [call_api] → [parse_response] → [extract_score] → [validate_score] → ...
```

### 4.4 Finding candidates in your codebase

Ask Claude (or search manually) with this framework:

*"In [your codebase], trace the execution path from [task entry point] to completion. List every function that:*
1. *Calls an external service (API, LLM, database)*
2. *Makes a routing or classification decision*
3. *Could fail independently*
4. *Takes more than 100ms*

*For each, give the function name, file, and a one-line description of what it does."*

Then pick the top 5-7 as your initial tracked actions.

### 4.5 Adding tracking

For standalone functions:

```python
@agent.track("fetch_crm_data")
def fetch_crm_data(lead_id):
    return crm_client.get(lead_id)
```

For class methods, see Section 6 for patterns.

---

## 5. Plumbing — Getting the Task Object Where You Need It

This is the most common integration challenge. The `agent.task()` context manager is created at the top level (where work starts), but `task.llm_call()`, `task.event()`, and other calls need to happen deeper in the call stack.

### 5.1 Assess your call depth

How deep is the call stack between your task boundary and the functions that need the task object?

| Depth | Description | Recommended pattern |
|-------|-------------|-------------------|
| **Shallow (1-2 levels)** | Task entry point directly calls the functions | Pass `task` as a parameter |
| **Medium (3-5 levels)** | Several function calls between entry and usage | `contextvars` (recommended) |
| **Deep (6+ levels)** | Many layers of abstraction, frameworks, middleware | `contextvars` or framework context object |
| **Cross-thread** | Work dispatched to other threads | `contextvars` (automatic with `asyncio`; for threads, copy context manually) |

### 5.2 Pattern A — Pass as parameter (shallow stacks)

The simplest approach. Just add `task=None` to function signatures:

```python
with agent.task(task_id) as task:
    result = process(item, task=task)

def process(item, task=None):
    score = score_item(item, task=task)
    if task:
        task.event("scored", {"score": score})
    return score

def score_item(item, task=None):
    response = llm.call(prompt)
    if task:
        task.llm_call("scoring", model="...", tokens_in=N, tokens_out=N)
    return response.score
```

**Pros:** Explicit, easy to understand, no magic.
**Cons:** Requires changing function signatures. Gets tedious with deep stacks.

### 5.3 Pattern B — `contextvars` (medium to deep stacks)

Create a single module that manages the current task context:

```python
# observability.py (new file in your project)
import contextvars

_current_task = contextvars.ContextVar('hiveloop_task', default=None)

def set_current_task(task):
    """Call this when entering a task context."""
    _current_task.set(task)

def get_current_task():
    """Call this anywhere to get the current task (or None)."""
    return _current_task.get()

def clear_current_task():
    """Call this when exiting a task context."""
    _current_task.set(None)
```

At the task boundary:

```python
from myproject.observability import set_current_task, clear_current_task

with agent.task(task_id) as task:
    set_current_task(task)
    try:
        result = process(item)  # no task parameter needed
    finally:
        clear_current_task()
```

Anywhere deeper in the code:

```python
from myproject.observability import get_current_task

def deep_function():
    # No function signature changes needed
    task = get_current_task()
    if task:
        task.llm_call("reasoning", model="...", tokens_in=N, tokens_out=N)
```

**Pros:** No function signature changes. Works across any call depth. Thread-safe. Async-safe.
**Cons:** Implicit (the task isn't visible in function signatures). Requires a shared module.

**Why `contextvars` is recommended:** Python's `contextvars` module was designed exactly for this use case — propagating request-scoped data through a call stack without passing it explicitly. It's what frameworks like FastAPI, Django, and asyncio use internally. Each thread and each async task gets its own context automatically.

### 5.4 Pattern C — Framework context object (web frameworks)

If your agent runs inside a web framework, attach the task to the request object:

```python
# FastAPI example
@app.post("/process")
async def handle(request: Request):
    with agent.task(task_id) as task:
        request.state.hiveloop_task = task
        return await process(request)

# Deeper in the code
async def process(request: Request):
    task = getattr(request.state, 'hiveloop_task', None)
    if task:
        task.llm_call(...)
```

**Pros:** Natural for web frameworks. No extra modules needed.
**Cons:** Only works when a request object is available throughout the stack.

### 5.5 Combining patterns

You can mix patterns. Use `contextvars` for the general case, and pass `task` explicitly where it makes the code clearer:

```python
with agent.task(task_id) as task:
    set_current_task(task)
    try:
        # Deep calls use contextvars
        result = complex_pipeline(item)
        
        # Direct calls can pass explicitly for clarity
        send_notification(result, task=task)
    finally:
        clear_current_task()
```

---

## 6. Implementation Patterns for Classes and Methods

Most production agents use classes. Decorating class methods requires extra consideration because the `agent` handle isn't available at class definition time.

### 6.1 The challenge

This doesn't work:

```python
class MyProcessor:
    @agent.track("process")   # ❌ agent doesn't exist when class is defined
    def process(self, item):
        ...
```

### 6.2 Pattern A — Context manager inside the method

Wrap the method body instead of decorating:

```python
class MyProcessor:
    def process(self, item):
        hiveloop_agent = get_hiveloop_agent(self.agent_name)
        if hiveloop_agent:
            with hiveloop_agent.track_context("process"):
                return self._process_inner(item)
        return self._process_inner(item)

    def _process_inner(self, item):
        # original logic, unchanged
        ...
```

**Pros:** Clean separation. Original logic untouched.
**Cons:** Two functions instead of one. Some duplication in the routing.

### 6.3 Pattern B — Helper function to reduce boilerplate

Create a helper that handles the conditional wrapping:

```python
# observability.py
def tracked(action_name, agent_name=None):
    """Wrap a function call with HiveLoop tracking if available."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            hiveloop_agent = get_hiveloop_agent(agent_name)
            if hiveloop_agent:
                with hiveloop_agent.track_context(action_name):
                    return func(*args, **kwargs)
            return func(*args, **kwargs)
        return wrapper
    return decorator
```

Usage:

```python
class MyProcessor:
    @tracked("process", agent_name="my-agent")
    def process(self, item):
        # original logic, unchanged
        ...
```

**Pros:** Clean decorator syntax. Original logic untouched.
**Cons:** Agent name must be known at decoration time (or resolved dynamically).

### 6.4 Pattern C — Dynamic decorator at runtime

Apply the decorator when the agent handle becomes available:

```python
class MyProcessor:
    def setup_tracking(self, hiveloop_agent):
        """Call this after the hiveloop agent is created."""
        self.process = hiveloop_agent.track("process")(self.process)
        self.score = hiveloop_agent.track("score")(self.score)

    def process(self, item):
        ...

    def score(self, item):
        ...
```

Call during initialization:

```python
processor = MyProcessor()
hiveloop_agent = hb.agent("my-agent")
processor.setup_tracking(hiveloop_agent)
```

**Pros:** Uses the native decorator API. Clean.
**Cons:** Requires an explicit setup step. Must be called after agent creation.

### 6.5 Pattern D — Use task events instead of decorators

Skip decorators entirely. Use the task object from `contextvars` and emit custom events:

```python
class MyProcessor:
    def process(self, item):
        task = get_current_task()
        if task:
            task.event("action_started", {"action": "process", "item_id": item.id})

        result = self._do_work(item)

        if task:
            task.event("action_completed", {"action": "process", "result": result.status})

        return result
```

**Pros:** No decorator complexity. Full control over what's emitted.
**Cons:** More verbose. Doesn't get automatic duration tracking or exception capture.

### 6.6 Which pattern should I use?

| Situation | Recommended pattern |
|-----------|-------------------|
| Standalone functions (not in a class) | Standard `@agent.track()` decorator |
| Class methods where agent name is fixed | Pattern B (helper decorator) |
| Class methods where agent is created dynamically | Pattern C (runtime decorator) or Pattern A (context manager) |
| Legacy code you don't want to modify | Pattern D (task events from contextvars) |
| Prototype / quick instrumentation | Pattern A (context manager) |

---

## 7. Validating Your Instrumentation

After adding `agent.task()` and `@agent.track()`, validate each step on the dashboard before going deeper.

### 7.1 Validation checklist — `agent.task()`

Trigger a task in your agent, then check:

| Dashboard element | What you should see | If missing |
|------------------|---------------------|------------|
| **Task Table** | A new row with your task ID, agent name, type, and status | `agent.task()` isn't being called — check that the code path is reached |
| **Task status** | `processing` while running, `completed` or `failed` after | If stuck on `processing`, the context manager isn't exiting cleanly |
| **Activity Stream** | `task_started` event, then `task_completed` or `task_failed` | Check stream filter — make sure "task" or "all" is selected |
| **Stats Ribbon** | Processing count goes to 1 during execution, then back to 0 | Verify the agent card shows PROCESSING during execution |
| **Agent card** | Shows PROCESSING badge and `↳ task_id` during execution | If still IDLE, the task isn't linked to the correct agent |

### 7.2 Validation checklist — `@agent.track()`

With tracking active, trigger a task and check:

| Dashboard element | What you should see | If missing |
|------------------|---------------------|------------|
| **Timeline** | Nodes for each tracked function, connected with duration lines | Functions aren't being decorated, or the decorator isn't from the right agent handle |
| **Activity Stream** | `action_started` and `action_completed` events for each tracked function | Check that the decorator is applied correctly (not shadowed or overridden) |
| **Timeline node details** | Click a node → shows function name, duration, and any error info | Working correctly |
| **Nested actions** | If function A calls function B (both tracked), B appears as a child of A in the timeline | Verify both are decorated and called within the same task context |

### 7.3 Validation checklist — Error handling

Deliberately trigger a failure to verify error tracking:

| Scenario | Expected on dashboard |
|----------|----------------------|
| Exception inside a task | Task status → `failed`, red node in timeline, `task_failed` in Activity Stream |
| Exception inside a tracked function | `action_failed` event, red node, but task may continue if exception is caught |
| Agent crashes (kill the process) | Agent heartbeat stops, card goes STUCK after threshold |

### 7.4 Quick smoke test sequence

1. Start HiveBoard server
2. Start your agent
3. Verify agent appears in The Hive (Layer 0)
4. Trigger one task
5. Verify task appears in Task Table
6. Click the task row — verify Timeline shows nodes
7. Verify Activity Stream shows task + action events
8. Trigger a failure — verify red nodes and `failed` status
9. ✅ Layer 1 is working

---

## 8. Adding Layer 2 — Rich Events

Once Layer 1 is validated, add rich events for the data that tells the full story.

### 8.1 Priority order

Add Layer 2 events in this order (highest value first):

| Priority | Event | Why it's valuable | Dashboard location |
|----------|-------|--------------------|--------------------|
| **1** | `task.llm_call()` | Cost visibility — the #1 feature request | Cost Explorer + Timeline |
| **2** | `task.escalate()` | Know when agents need human help | Activity Stream + Timeline |
| **3** | `task.plan()` + `task.plan_step()` | See progress and where plans fail | Plan progress bar in Timeline |
| **4** | `agent.report_issue()` | Agent self-reported problems | Pipeline tab in Agent Detail |
| **5** | `task.request_approval()` + `task.approval_received()` | Human-in-the-loop tracking | Activity Stream + agent status |
| **6** | `task.retry()` | Retry patterns and failure recovery | Timeline branching |
| **7** | `agent.todo()` + `agent.scheduled()` | Work pipeline visibility | Pipeline tab in Agent Detail |

### 8.2 Finding WHERE to add LLM calls

Search your codebase for LLM client calls. Common patterns:

```python
# OpenAI-style
response = client.chat.completions.create(model=..., messages=...)

# Anthropic-style
response = client.messages.create(model=..., messages=...)

# Generic wrapper
response = llm.complete(prompt=..., model=...)
response = llm_client.call(model=..., prompt=...)
```

After each call, add:

```python
task = get_current_task()
if task:
    task.llm_call(
        "descriptive_name",           # what this call does
        model=model_name,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost=calculated_cost,         # see Part 1, Section 6.1 for cost calculation
        duration_ms=elapsed_ms,
    )
```

### 8.3 Finding WHERE to add escalations and approvals

Search for patterns where your agent:
- Stops and waits for human input
- Delegates to another agent or team
- Flags something for review
- Enters a "pending" state

These map to `task.escalate()`, `task.request_approval()`, and `task.approval_received()`.

### 8.4 Finding WHERE to add plans

If your agent creates execution plans (step-by-step strategies), add `task.plan()` at plan creation and `task.plan_step()` as each step progresses. Look for:
- Plan/strategy objects being created
- Step arrays or phase lists
- Progress tracking through sequential stages

---

## 9. Architecture-Specific Guidance

### 9.1 Single loop agent

```
Entry point: main() → while True loop
Task boundary: Each iteration of the loop
Actions: Functions called within each iteration
Plumbing: contextvars (set at loop top, cleared at bottom)
```

```python
hb = hiveloop.init(api_key="...")
agent = hb.agent("my-agent")

while True:
    item = queue.get()
    with agent.task(item.id, project="my-project") as task:
        set_current_task(task)
        try:
            process(item)
        finally:
            clear_current_task()
```

### 9.2 Multi-agent system

```
Entry point: Application startup (creates agents dynamically)
Task boundary: Per-agent per-job execution
Actions: Agent-specific processing functions
Plumbing: Agent registry + contextvars
```

```python
hb = hiveloop.init(api_key="...")
_agents = {}

def create_agent(name, agent_type):
    _agents[name] = hb.agent(name, type=agent_type)

def run_agent_task(agent_name, job):
    hiveloop_agent = _agents.get(agent_name)
    if hiveloop_agent:
        with hiveloop_agent.task(job.id, project="my-project") as task:
            set_current_task(task)
            try:
                execute(job)
            finally:
                clear_current_task()
    else:
        execute(job)
```

### 9.3 API-driven agent (FastAPI)

```
Entry point: Application startup (lifespan or on_event)
Task boundary: Each HTTP request
Actions: Service functions called by the handler
Plumbing: Request state or contextvars
```

```python
hb = hiveloop.init(api_key="...")
agent = hb.agent("api-agent")

@app.post("/process")
async def handle(request: ProcessRequest):
    with agent.task(request.id, project="api-service") as task:
        set_current_task(task)
        try:
            result = await process(request)
            return result
        finally:
            clear_current_task()
```

### 9.4 Framework-based (LangChain, CrewAI)

```
Entry point: Application startup
Task boundary: Handled by the framework callback (each agent.invoke() or crew.kickoff())
Actions: Mapped automatically by the callback
Plumbing: Framework handles it internally
```

```python
from hiveloop.integrations.langchain import LangChainCallback

hb = hiveloop.init(api_key="...")
callback = LangChainCallback(hb, project="my-project")
agent.invoke({"input": "..."}, config={"callbacks": [callback]})
```

> **Note:** Framework integrations handle task creation, action tracking, and LLM call logging automatically through the framework's callback system. No manual `agent.task()` or `@agent.track()` needed.

### 9.5 Scheduled / cron-based agent

```
Entry point: Scheduler setup
Task boundary: Each scheduled execution
Actions: Steps within the scheduled job
Plumbing: contextvars (set at job start)
```

```python
hb = hiveloop.init(api_key="...")
agent = hb.agent("scheduler-agent")

@scheduler.every("1h")
def hourly_sync():
    task_id = f"sync-{datetime.now().strftime('%Y%m%d-%H%M')}"
    with agent.task(task_id, project="data-ops", type="sync") as task:
        set_current_task(task)
        try:
            fetch_data()
            transform_data()
            load_data()
        finally:
            clear_current_task()
```

---

## 10. The Safety Contract

HiveLoop follows a strict safety contract: **your agent must never break because of observability.**

### 10.1 Rules

1. **HiveLoop never raises transport errors.** Network failures, server downtime, 500 errors — all caught and logged silently. Your code never sees them.

2. **HiveLoop never swallows your exceptions.** If your code raises, the exception propagates normally. HiveLoop records it and re-raises.

3. **HiveLoop never blocks your code.** All network I/O happens in a background thread. `task.llm_call()`, `task.event()`, etc. are queue-append operations that return immediately.

4. **HiveLoop is always optional.** Every instrumentation point should have a graceful fallback:

```python
# This pattern should be everywhere:
task = get_current_task()
if task:
    task.llm_call(...)
# If task is None, nothing happens. Your agent runs normally.
```

### 10.2 Testing without HiveBoard

You can run your agent without the HiveBoard server running. HiveLoop will buffer events, fail to send them, and silently discard them after retries. Your agent runs identically.

To disable HiveLoop entirely in tests:

```python
# Don't call hiveloop.init() in your test configuration.
# All get_current_task() calls return None.
# All if task: guards skip.
# Zero overhead.
```

### 10.3 The `if task:` guard

Every place you call a task method, guard it:

```python
# ✅ Safe:
task = get_current_task()
if task:
    task.llm_call("reasoning", model=model_name, tokens_in=N, tokens_out=N)

# ❌ Unsafe (will crash if task is None):
get_current_task().llm_call("reasoning", model=model_name, tokens_in=N, tokens_out=N)
```

This one-line guard is the price of safety. It's worth it.

---

## 11. Incremental Instrumentation Checklist

Use this checklist to track your progress. Each step is independently valuable.

### Layer 0 — Init + Heartbeat
- [ ] `hiveloop.init()` added at application startup
- [ ] `hb.agent()` called for each agent (static or dynamic)
- [ ] Agent handles stored and accessible (registry pattern or instance attribute)
- [ ] **Verify:** Agents visible in The Hive with green heartbeats

### Layer 1a — Task context
- [ ] Task entry point identified
- [ ] `agent.task()` context manager wrapping the task boundary
- [ ] Plumbing pattern chosen and implemented (`contextvars` recommended)
- [ ] `set_current_task()` / `clear_current_task()` in `try/finally`
- [ ] **Verify:** Tasks appear in Task Table, PROCESSING badge shows during execution

### Layer 1b — Action tracking
- [ ] Top 5-7 tracked functions identified
- [ ] Tracking applied (decorators, context managers, or runtime decoration)
- [ ] **Verify:** Timeline shows action nodes with duration connectors

### Layer 2a — LLM calls (highest value)
- [ ] All LLM call sites identified
- [ ] `task.llm_call()` added after each LLM call
- [ ] Cost calculation implemented
- [ ] **Verify:** Cost Explorer shows data, LLM nodes appear in Timeline (purple)

### Layer 2b — Narrative events
- [ ] Escalation points identified and `task.escalate()` added
- [ ] Approval request/receive points identified and methods added
- [ ] Plan creation/step points identified and methods added
- [ ] Retry points identified and `task.retry()` added
- [ ] Issue reporting points identified and `agent.report_issue()` added
- [ ] **Verify:** Activity Stream shows rich event types, Pipeline tab populated

### Layer 2c — Pipeline enrichment (optional)
- [ ] `queue_provider` callback added to agent registration
- [ ] `heartbeat_payload` callback added for custom status
- [ ] `agent.scheduled()` called for recurring work
- [ ] `agent.todo()` called for work item lifecycle
- [ ] **Verify:** Agent cards show queue badges and issue indicators

---

## 12. Common Mistakes

### 12.1 Forgetting the `finally` block

```python
# ❌ Bug: if process() throws, current task is never cleared
with agent.task(task_id) as task:
    set_current_task(task)
    result = process(item)
    clear_current_task()  # never reached on exception

# ✅ Correct:
with agent.task(task_id) as task:
    set_current_task(task)
    try:
        result = process(item)
    finally:
        clear_current_task()
```

### 12.2 Using the wrong agent handle

In multi-agent systems, make sure the `agent.task()` uses the correct agent's handle:

```python
# ❌ Bug: always uses the same agent
with global_agent.task(task_id) as task:
    ...

# ✅ Correct: uses the agent that's actually doing the work
hiveloop_agent = get_hiveloop_agent(current_agent.name)
with hiveloop_agent.task(task_id) as task:
    ...
```

### 12.3 Tracking too many functions

If your timeline has 30+ nodes for one task, it's unreadable. Start with the top 5-7 and add more only if you need the detail for debugging specific issues.

### 12.4 Forgetting to create the project

`agent.task(task_id, project="my-project")` will fail with a 207 partial rejection if `"my-project"` doesn't exist on the server. Create projects first via the API:

```bash
curl -X POST http://localhost:8000/v1/projects \
  -H "Authorization: Bearer your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"slug": "my-project", "name": "My Project"}'
```

### 12.5 Not setting the env var in both terminals

On Windows PowerShell, environment variables are per-terminal. If your HiveBoard server and your agent run in different terminals, both need:

```powershell
$env:HIVEBOARD_DEV_KEY = "hb_live_dev000000000000000000000000000000"
```

On Linux/Mac:
```bash
export HIVEBOARD_DEV_KEY="hb_live_dev000000000000000000000000000000"
```

### 12.6 Agent name mismatch

The `agent_id` in `hb.agent()` must exactly match what you use to look up the agent handle later. If your agent is named `"lead-qualifier"` but you look up `"Lead Qualifier"`, you'll get `None` and all instrumentation silently skips.

### 12.7 Calling task methods outside a task context

```python
# ❌ Bug: no active task, so get_current_task() returns None
def some_startup_function():
    task = get_current_task()
    task.llm_call(...)  # AttributeError: 'NoneType' has no attribute 'llm_call'

# ✅ Use agent-level methods for calls outside task context:
agent.llm_call("startup_call", model="...", tokens_in=N, tokens_out=N)
```

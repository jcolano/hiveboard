# HiveBoard Integration Guide for Agentic Frameworks

**A layer-by-layer technical guide to making your AI agents observable.**

This guide is for developers who build or maintain agentic AI frameworks -- systems where an LLM reasons, selects tools, executes them, and repeats until a task is complete. The guide assumes Python but the concepts apply to any language with an HiveLoop SDK.

The integration follows a layered approach. Each layer builds on the previous one, and each one unlocks new dashboard capabilities. You can ship after any layer and come back later.

| Layer | What you add | What the dashboard shows | Effort |
|-------|-------------|------------------------|--------|
| 0 | SDK init + agent registration | Heartbeat, online/offline, stuck detection | ~10 lines |
| 1 | Task boundaries + action tracking | Task table, timelines, success/failure rates | ~30 lines |
| 2a | LLM call tracking | Cost explorer, token usage, model breakdown | ~10 lines per LLM call site |
| 2b | Tool execution tracking | Tool nodes in timeline with inputs/outputs | ~15 lines at tool dispatch |
| 2c | Rich events | Plans, escalations, approvals, issues, TODOs, queue state | ~5 lines per event site |

---

## Prerequisites

```bash
pip install hiveloop
```

You need:
- A HiveBoard server (self-hosted or cloud) with an API key (`hb_live_...` or `hb_test_...`)
- A project created on the server (a slug like `"my-project"`)

---

## Layer 0: Initialization and Agent Registration

**Goal:** Get agents visible on the dashboard with heartbeats and stuck detection.

### Step 1: Initialize the SDK

Call `hiveloop.init()` once at application startup -- before any agents are created.

```python
import hiveloop

hb = hiveloop.init(
    api_key="hb_live_your_key_here",
    endpoint="https://your-hiveboard-server.com",
    environment="production",
)
```

This creates a singleton client that buffers events in memory and flushes them to the server every 5 seconds via a background thread. It never blocks your application code. If the server is unreachable, events are queued (up to 10,000) and retried with exponential backoff.

**Where to put this:** In your application's entry point -- the `main()` function, the FastAPI `lifespan`, the CLI command handler, or wherever your framework boots up.

`hiveloop.init()` parameters:

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `api_key` | str | required | Starts with `hb_live_`, `hb_test_`, or `hb_read_` |
| `endpoint` | str | `"https://api.hiveboard.io"` | Your HiveBoard server URL |
| `environment` | str | `"production"` | Filterable on dashboard. Use `"development"`, `"staging"`, etc. |
| `flush_interval` | float | `5.0` | Seconds between automatic flushes |
| `batch_size` | int | `100` | Max events per HTTP request (server caps at 500) |
| `max_queue_size` | int | `10000` | Events buffered in memory before oldest are dropped |
| `debug` | bool | `False` | Logs SDK operations to stderr |

### Step 2: Register agents

For each agent in your framework, call `hb.agent()`. This is idempotent -- calling it twice with the same `agent_id` returns the same handle.

```python
hiveloop_agent = hb.agent(
    agent_id="sales",
    type="sales",                    # role or classification
    version="claude-sonnet-4-5",     # model or agent version
    framework="my-framework",        # your framework name
    heartbeat_interval=30,           # seconds between heartbeats (0 to disable)
    stuck_threshold=300,             # seconds without heartbeat before "stuck" badge
)
```

**Where to put this:** Wherever your framework creates or initializes agent instances. If agents are created lazily, call `hb.agent()` right after the agent object is constructed.

**Store the handle.** You'll need it later for task tracking, tool tracking, and event reporting. Common patterns:

```python
# Option A: Store on the agent object
agent._hiveloop = hb.agent(agent_id=agent.id, ...)

# Option B: Store in a registry dict
_hiveloop_agents[agent.id] = hb.agent(agent_id=agent.id, ...)
```

`hb.agent()` parameters:

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `agent_id` | str | required | Unique identifier, max 256 chars |
| `type` | str | `"general"` | Agent classification (role, department, etc.) |
| `version` | str | `None` | Agent or model version string |
| `framework` | str | `"custom"` | Your framework name |
| `heartbeat_interval` | int | `30` | Seconds. Set to `0` to disable heartbeats |
| `stuck_threshold` | int | `300` | Seconds. Agent shows STUCK badge if no heartbeat within this window |
| `heartbeat_payload` | Callable | `None` | `() -> dict` called each heartbeat, included in payload |
| `queue_provider` | Callable | `None` | `() -> dict` returning queue state each heartbeat |

### What you see on the dashboard after Layer 0

- **Agent cards** appear in The Hive panel with IDLE status
- **Heartbeat sparklines** pulse every 30 seconds
- **STUCK detection** fires if an agent stops responding
- **Connection indicator** shows green "Connected"

### Safety contract

Every HiveLoop call should be wrapped so it never crashes your agent:

```python
# Guard pattern -- use everywhere
if hiveloop_agent:
    try:
        hiveloop_agent.some_method(...)
    except Exception:
        pass  # observability must never break the agent
```

This is non-negotiable. Observability is a side channel. If the HiveBoard server goes down, your agents must continue running identically.

---

## Layer 1: Task Boundaries and Action Tracking

**Goal:** Get task timelines, success/failure tracking, and duration measurement.

### Concept: What is a "task"?

A task is a single unit of work your agent performs from start to finish. In most frameworks this maps to:

- One execution of the agentic loop (receive message -> reason -> use tools -> respond)
- One event being processed from a queue
- One API request being handled
- One scheduled job running

The key question: **"Where does a unit of work start and end?"** Find that boundary in your code.

### Step 3: Wrap task execution with `agent.task()`

`agent.task()` is a context manager. It emits `task_started` on entry and `task_completed` or `task_failed` on exit (depending on whether an exception was raised).

```python
def execute_agent(agent, message, event_context=None):
    hiveloop_agent = agent._hiveloop  # the handle from Layer 0

    if hiveloop_agent is not None:
        task_id = generate_unique_task_id()  # must be unique per execution
        with hiveloop_agent.task(
            task_id,
            project="my-project",
            type=event_context.get("source", "api") if event_context else "api",
        ) as task:
            # Make the task handle available deeper in the call stack
            set_current_task(task)
            set_hiveloop_agent(hiveloop_agent)
            try:
                result = agent.run(message)
            finally:
                clear_current_task()
                clear_hiveloop_agent()
        return result
    else:
        return agent.run(message)
```

**Critical: Task IDs must be unique per execution.** If you reuse task IDs (like using a session ID), the dashboard will show only one row per ID instead of one row per run. Generate unique IDs using the event ID, a UUID, or a combination:

```python
def generate_unique_task_id(agent_id, event_id=None):
    if event_id:
        return f"{agent_id}-{event_id}"
    return f"{agent_id}-{uuid.uuid4().hex[:8]}"
```

`agent.task()` parameters:

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `task_id` | str | required | Unique per execution |
| `project` | str | `None` | Groups tasks in the dashboard |
| `type` | str | `None` | Classification: `"heartbeat"`, `"webhook"`, `"human"`, `"api"`, etc. |

### Step 4: Plumb the task handle through the call stack

The task handle created by `agent.task()` needs to be accessible from functions deep in the call stack -- your LLM call wrapper, your tool executor, your reflection module, etc. Don't pass it as a parameter through every function signature. Use `contextvars`.

Create a small plumbing module:

```python
# observability.py
import contextvars
from typing import Any, Optional

_current_task = contextvars.ContextVar("hiveloop_task", default=None)
_current_agent = contextvars.ContextVar("hiveloop_agent", default=None)

def set_current_task(task: Any) -> None:
    _current_task.set(task)

def get_current_task() -> Optional[Any]:
    return _current_task.get()

def clear_current_task() -> None:
    _current_task.set(None)

def set_hiveloop_agent(agent: Any) -> None:
    _current_agent.set(agent)

def get_hiveloop_agent() -> Optional[Any]:
    return _current_agent.get()

def clear_hiveloop_agent() -> None:
    _current_agent.set(None)
```

**Why two contextvars?** They have different lifetimes. The task exists only during one execution. The agent handle persists for the agent's entire lifecycle. Some methods (`report_issue`, `todo`, `queue_snapshot`) are agent-level and work outside any task context. Others (`llm_call`, `plan`, `escalate`) are task-level and require an active task.

**Set both** when entering the task context, **clear both** in the `finally` block. This ensures downstream code can safely call `get_current_task()` or `get_hiveloop_agent()` and get `None` if no context is active.

### Step 5: Track key functions with `@agent.track()`

For functions whose names are known at definition time (not dynamically dispatched tools), use the `@agent.track()` decorator:

```python
@hiveloop_agent.track("reflect")
def reflect(self, context):
    # ... reflection logic ...
```

This adds action nodes to the task timeline. **The "5-7 nodes" rule:** Track 5-7 high-value functions, not 30. Good candidates are functions that call external services, make decisions, take >100ms, or could fail independently. Internal utilities, fast helpers, and functions called hundreds of times per task are bad candidates.

If your functions are class methods where the agent handle isn't available at import time, use `track_context()` instead (covered in Layer 2b).

### What you see on the dashboard after Layer 1

- **Task Table** populates with rows: task ID, agent, type, status, duration
- **Timeline** shows task start/end with action nodes in between
- **Stats Ribbon** shows: Processing count, Success Rate, Avg Duration, Errors
- **Activity Stream** shows `task_started`, `task_completed`, `task_failed`, and action events
- **Agent cards** show PROCESSING badge and current task ID during execution

---

## Layer 2a: LLM Call Tracking

**Goal:** Get cost visibility, token usage, and model breakdown.

### Step 6: Find all LLM call sites

Search your codebase for every place an LLM API is called. Common patterns:

```python
# Direct API calls
response = client.chat.completions.create(...)
response = client.messages.create(...)

# Framework wrappers
response = llm_client.complete(...)
response = llm_client.complete_with_tools(...)
response = llm_client.complete_json(...)
```

Build a catalog. A typical agentic framework has 3-8 LLM call sites:

| # | Location | Purpose | Example name |
|---|----------|---------|-------------|
| 1 | Main loop - reasoning | Agent decides what to do | `"reasoning"` |
| 2 | Main loop - tool use | Agent generates tool parameters | `"tool_use"` |
| 3 | Reflection module | Agent evaluates its progress | `"reflection"` |
| 4 | Planning module | Agent creates execution plan | `"create_plan"` |
| 5 | Summary/compression | Context window management | `"context_compaction"` |
| 6 | Classification/routing | Routing to sub-agents | `"classify"` |

### Step 7: Determine token extraction for each site

Different LLM clients expose tokens differently. Inspect each response object:

```python
# Discovery helper -- run once per client type
for attr in dir(response):
    if any(k in attr.lower() for k in ['token', 'usage', 'cost', 'model']):
        print(f"response.{attr} = {getattr(response, attr, '?')}")
```

Common patterns:

| Pattern | Example | How to extract |
|---------|---------|---------------|
| On response object | Anthropic SDK | `response.usage.input_tokens`, `response.usage.output_tokens` |
| On client after call | Some wrappers | `client._last_input_tokens`, `client._last_output_tokens` |
| In response metadata | LangChain | `response.response_metadata['token_usage']` |
| Not available | Some wrappers | Send `name` and `model` only -- tokens are optional |

### Step 8: Add `task.llm_call()` at each site

The standard pattern:

```python
import time
from your_app.observability import get_current_task

# 1. Time the call
_start = time.perf_counter()
response = llm_client.complete(prompt=prompt, system=system, max_tokens=2048)
_elapsed_ms = (time.perf_counter() - _start) * 1000

# 2. Extract tokens (adapt to your response shape)
tokens_in = response.usage.input_tokens
tokens_out = response.usage.output_tokens

# 3. Report to HiveLoop
_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "reasoning",                      # descriptive name (not the model name)
            model=response.model,             # model identifier
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=estimate_cost(response.model, tokens_in, tokens_out),
            duration_ms=round(_elapsed_ms),
        )
    except Exception:
        pass
```

### Cost estimation helper

The SDK doesn't calculate costs -- you provide them. Build a lookup table for the models you use:

```python
# Prices as of early 2026 -- update when pricing changes
COST_PER_MILLION = {
    # Anthropic
    "claude-sonnet-4-5-20250929":   {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5-20251001":    {"input": 0.80,  "output": 4.00},
    "claude-3-haiku-20240307":      {"input": 0.25,  "output": 1.25},
    # OpenAI
    "gpt-4o":                       {"input": 2.50,  "output": 10.00},
    "gpt-4o-mini":                  {"input": 0.15,  "output": 0.60},
    # Add your models here
}

def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    rates = COST_PER_MILLION.get(model)
    if not rates:
        return None
    return (tokens_in * rates["input"] / 1_000_000) + (tokens_out * rates["output"] / 1_000_000)
```

### Optional: Prompt and response previews

`task.llm_call()` supports `prompt_preview` and `response_preview` parameters. These make Timeline LLM nodes clickable with real content. But prompts can contain PII, credentials, or internal context -- every string flows through the network and gets stored.

**Recommendation:** Don't add them everywhere. Gate them behind a config flag, and truncate aggressively (300 chars max). Only add them to the most opaque call sites where seeing what the agent asked is valuable for debugging:

```python
_task.llm_call(
    "reasoning",
    model=response.model,
    tokens_in=tokens_in,
    tokens_out=tokens_out,
    cost=estimate_cost(response.model, tokens_in, tokens_out),
    duration_ms=round(_elapsed_ms),
    # Only when config flag is enabled
    prompt_preview=prompt[:300] if config.log_prompts else None,
    response_preview=str(response.content)[:300] if config.log_prompts else None,
)
```

`task.llm_call()` parameters:

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `name` | str | yes | Descriptive label for the Timeline node. Use the *purpose*, not the model name |
| `model` | str | yes | Model identifier. Used for Cost Explorer grouping |
| `tokens_in` | int | no | Input token count |
| `tokens_out` | int | no | Output token count |
| `cost` | float | no | USD cost. If omitted, Cost Explorer won't aggregate |
| `duration_ms` | int | no | Wall-clock LLM latency |
| `prompt_preview` | str | no | Truncated prompt for debugging. Consider PII implications |
| `response_preview` | str | no | Truncated response for debugging |
| `metadata` | dict | no | Arbitrary key-value pairs |

### What you see on the dashboard after Layer 2a

- **Cost Explorer** is fully functional: cost by model, cost by agent, total spend
- **Task Table** LLM column shows call count, COST column shows dollar amounts
- **Timeline** has purple LLM nodes with model badges
- **Stats Ribbon** Cost (1h) populates
- **Activity Stream** "llm" filter works

---

## Layer 2b: Tool Execution Tracking

**Goal:** Get tool nodes in the timeline showing what tools the agent used, with inputs and outputs.

### The problem

In agentic frameworks, the LLM picks tools at runtime. You can't use `@agent.track("tool_name")` because the tool name isn't known at definition time. `track_context()` solves this by accepting the name as a runtime parameter.

### Step 9: Wrap tool dispatch with `agent.track_context()`

Find the place in your code where tools are dispatched -- typically a single function that takes a tool name and parameters and routes to the correct implementation.

```python
from your_app.observability import get_hiveloop_agent

def execute_tool(tool_name, parameters):
    _agent = get_hiveloop_agent()

    if _agent is not None:
        with _agent.track_context(tool_name) as ctx:
            result = tool_registry.execute(tool_name, parameters)
            ctx.set_payload({
                "args": {k: str(v)[:100] for k, v in parameters.items()},
                "result_preview": str(result)[:200],
                "success": result.success,
                "error": result.error,
            })
        return result
    else:
        return tool_registry.execute(tool_name, parameters)
```

`track_context()` automatically:
- Emits `action_started` when entering the `with` block
- Measures duration
- Emits `action_completed` on clean exit (with the payload you set)
- Emits `action_failed` on exception (capturing exception type and message)
- Handles nesting (if one tool calls another, the child becomes a nested node)

### Separated lifecycle pattern

If your tool dispatch doesn't use exceptions for error signaling (e.g., it returns a result object with a `success` flag), separate the `__enter__` and `__exit__` calls to ensure the tool always executes exactly once:

```python
_ctx = None
if _agent is not None:
    try:
        _ctx = _agent.track_context(tool_name)
        _ctx.__enter__()
    except Exception:
        _ctx = None

# Tool always executes exactly once, regardless of tracking
result = tool_registry.execute(tool_name, parameters)

if _ctx is not None:
    try:
        _ctx.set_payload({
            "args": {k: str(v)[:100] for k, v in parameters.items()},
            "result_preview": (result.output or "")[:200],
            "success": result.success,
            "error": result.error,
        })
        _ctx.__exit__(None, None, None)
    except Exception:
        pass
```

This avoids the double-execute bug where a naive `with`-based fallback could run the tool twice if `track_context()` succeeds but the tool fails.

### What you see on the dashboard after Layer 2b

- **Timeline** has blue tool nodes with names matching the actual tools called
- Clicking a tool node shows the payload (args, result preview, success/error)
- **Activity Stream** shows `action_completed` and `action_failed` for each tool call
- **Failed tools** appear as red nodes with error details

---

## Layer 2c: Rich Events

**Goal:** Plans, escalations, approvals, issues, retries, TODOs, queue state, and schedules.

These are the narrative events that tell the *story* of what your agent is doing and why. Each one lights up a specific dashboard feature.

### Plans and plan steps

If your framework breaks complex tasks into ordered steps (a plan), report the plan structure and step transitions.

**When a plan is created:**

```python
_task = get_current_task()
if _task:
    try:
        step_descriptions = [step.description for step in plan.steps]
        _task.plan(goal=task_description, steps=step_descriptions)
        # Report first step started
        _task.plan_step(step_index=0, action="started", summary=plan.steps[0].description)
    except Exception:
        pass
```

**When a step completes and the next one starts:**

```python
if _task:
    try:
        _task.plan_step(step_index=current_step.index, action="completed",
                        summary=result_summary, turns=current_step.turns_taken)
        _task.plan_step(step_index=next_step.index, action="started",
                        summary=next_step.description)
    except Exception:
        pass
```

**When a step fails or is blocked:**

```python
if _task:
    try:
        _task.plan_step(step_index=step.index, action="failed",
                        summary=f"Blocked: {reason}")
    except Exception:
        pass
```

**When the plan is revised (replanned):**

```python
if _task:
    try:
        _task.plan(goal=revised_goal, steps=new_step_descriptions, revision=revision_count)
        _task.plan_step(step_index=0, action="started", summary=new_steps[0].description)
    except Exception:
        pass
```

**Dashboard effect:** A plan progress bar appears above the Timeline: green segments for completed steps, red for failed, gray for pending. This is the fastest way for an operator to see "where did it go wrong?"

`task.plan()` parameters:

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `goal` | str | yes | What the plan achieves |
| `steps` | list[str] | yes | Ordered step descriptions |
| `revision` | int | no | Default 0. Increment on each replan |

`task.plan_step()` parameters:

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `step_index` | int | yes | Zero-based position in the steps list |
| `action` | str | yes | `"started"`, `"completed"`, `"failed"`, `"skipped"` |
| `summary` | str | yes | Outcome description |
| `turns` | int | no | LLM turns spent on this step |
| `tokens` | int | no | Tokens spent on this step |

---

### Escalations

When the agent decides it cannot handle something and hands it to a human.

**Typical trigger:** Your reflection or self-evaluation module returns a "give up" or "escalate" decision.

```python
if reflection.decision == "escalate":
    _task = get_current_task()
    if _task:
        try:
            _task.escalate(
                f"Agent escalated: {reflection.reasoning[:200]}",
                assigned_to="human",
            )
        except Exception:
            pass
```

**Dashboard effect:** Escalation node appears in the Timeline. The agent card may show a warning indicator.

`task.escalate()` parameters:

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `reason` | str | yes | Why the agent is escalating |
| `assigned_to` | str | no | Who should handle it |

---

### Approvals

When the agent needs human approval before proceeding.

This is architecturally tricky: the request and the response often happen in different execution contexts (different threads, different HTTP requests). You may not have an active task context when the approval is granted.

**When the agent requests approval** (often in a queue/runtime module):

```python
# May not have a task context -- use the agent handle directly
agent_obj = get_agent(agent_id)
_hl_agent = getattr(agent_obj, "_hiveloop", None)
if _hl_agent:
    try:
        _hl_agent.request_approval(
            f"Approval needed: {event_title}",
            approver="human",
        )
    except Exception:
        pass
```

**When a human approves:**

```python
_hl_agent = getattr(get_agent(agent_id), "_hiveloop", None)
if _hl_agent:
    try:
        _hl_agent.approval_received(
            f"Event '{event_id}' approved by operator",
            approved_by="human",
            decision="approved",
        )
    except Exception:
        pass
```

**When a human rejects:**

```python
if _hl_agent:
    try:
        _hl_agent.approval_received(
            f"Event '{event_id}' dropped by operator",
            approved_by="human",
            decision="rejected",
        )
    except Exception:
        pass
```

**Dashboard effect:** Agent card shows WAITING badge. The Pipeline tab shows the pending approval. After resolution, the approval decision appears in the Activity Stream.

---

### Issues

Persistent problems the agent encounters -- broken credentials, API failures, configuration errors. Unlike tool failures (which are per-execution), issues represent ongoing operational problems.

**When the agent detects a persistent problem:**

```python
_agent = get_hiveloop_agent()
if _agent:
    try:
        _agent.report_issue(
            summary="CRM API returning 403",
            severity="high",                   # critical, high, medium, low
            category="permissions",            # permissions, connectivity, configuration, etc.
            issue_id="crm-api-403",            # stable ID for deduplication
            context={"api": "crm", "status_code": 403},
            occurrence_count=error_count,
        )
    except Exception:
        pass
```

**When the issue is resolved** (human action or automatic recovery):

```python
if _agent:
    try:
        _agent.resolve_issue(
            summary="CRM API recovered",
            issue_id="crm-api-403",
        )
    except Exception:
        pass
```

**Important:** Always use `issue_id` for recurring issues. Without it, the SDK deduplicates on `summary` text. If the summary contains timestamps or variable data, each occurrence looks like a new issue.

**Dashboard effect:** Agent card shows a red issue badge with count. The Pipeline tab lists open issues by severity. Resolving an issue removes the badge.

`agent.report_issue()` parameters:

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `summary` | str | yes | Human-readable description |
| `severity` | str | yes | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `category` | str | no | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"` |
| `issue_id` | str | no | Stable ID for lifecycle tracking and dedup |
| `context` | dict | no | Debugging data (API name, status code, etc.) |
| `occurrence_count` | int | no | How many times the agent has seen this |

Severity guidelines:

| Severity | When to use | Example |
|----------|-------------|---------|
| `critical` | Agent cannot function at all | No API key configured |
| `high` | Major capability degraded | CRM API returning 403 |
| `medium` | Output quality reduced | Fallback model in use |
| `low` | Informational | Cache miss rate high |

---

### Retries

When a failed task creates a follow-up for retry.

```python
_task = get_current_task()
if _task:
    try:
        _task.retry(
            f"Failed ({status}): {message[:100]}",
            attempt=1,
        )
    except Exception:
        pass
```

`task.retry()` parameters:

| Parameter | Type | Required | Notes |
|-----------|------|----------|-------|
| `reason` | str | yes | Why the retry is happening |
| `attempt` | int | yes | 1-based attempt number |
| `backoff_seconds` | float | no | Wait time before next attempt |

---

### TODOs

Agent-managed work items -- retries, follow-ups, pending actions.

```python
_agent = get_hiveloop_agent()
if _agent:
    try:
        _agent.todo(
            todo_id="td_001",
            action="created",           # created, completed, failed, dismissed, deferred
            summary="Retry: Process lead TechNova",
            priority="high",            # optional
            source="failed_run",        # optional: who/what created it
            context="error=timeout",    # optional: extra detail
        )
    except Exception:
        pass
```

Report at every lifecycle point: when a TODO is created, when it's completed, when it's dismissed.

**Dashboard effect:** The Pipeline tab shows active TODOs. Completed/dismissed TODOs clear from the list.

---

### Queue snapshots

If your agents have a work queue (inbox, event queue, task queue), report its state so operators can see if work is piling up.

**Best approach:** Use the `queue_provider` callback at agent registration. The SDK calls this callback every heartbeat cycle (30s) and includes the result in the heartbeat payload automatically.

```python
def make_queue_provider(agent_id):
    def provider():
        queue = get_queue_for_agent(agent_id)
        if not queue:
            return {"depth": 0}
        return {
            "depth": len(queue),
            "oldest_age_seconds": queue.oldest_age_seconds(),
            "items": [
                {
                    "id": item.id,
                    "priority": item.priority,
                    "source": item.source,
                    "summary": item.title or item.message[:80],
                    "queued_at": item.timestamp.isoformat(),
                }
                for item in queue[:10]  # cap at 10 items
            ],
            "processing": {
                "id": current.id,
                "summary": current.title or current.message[:80],
                "started_at": current.timestamp.isoformat(),
            } if (current := get_currently_processing(agent_id)) else None,
        }
    return provider

# At agent registration
hiveloop_agent = hb.agent(
    agent_id="sales",
    # ... other params ...
    queue_provider=make_queue_provider("sales"),
)
```

**Note:** The `queue_provider` is called lazily by the SDK on each heartbeat. It can safely reference runtime state that doesn't exist at registration time (like a runtime that starts later). Just return `{"depth": 0}` if the state isn't available yet.

**Dashboard effect:** Queue depth badge (Q:8) on agent cards. The Pipeline tab shows queue contents. Operators can see if work is piling up.

---

### Scheduled work

If your agents have recurring tasks (periodic syncs, cleanup jobs, heartbeat timers), report them once at startup so the Pipeline tab shows the schedule.

```python
_agent = getattr(agent_obj, "_hiveloop", None)
if _agent:
    try:
        _agent.scheduled(items=[
            {
                "id": "hb_crm_sync",
                "name": "CRM Sync",
                "interval": "10m",
                "enabled": True,
                "last_status": None,
            },
            {
                "id": "hb_email_check",
                "name": "Email Check",
                "interval": "15m",
                "enabled": True,
                "last_status": None,
            },
        ])
    except Exception:
        pass
```

**Where to put this:** In your agent start/boot function, after scheduled work is configured.

---

## Putting It All Together: Complete Turn Instrumentation

Here's how all layers combine in a single agentic loop turn:

```python
import time
import json
from your_app.observability import get_current_task, get_hiveloop_agent, estimate_cost

def run_turn(messages, tool_catalog, system_prompt):
    """One turn of the agentic loop with full HiveLoop instrumentation."""

    # --- LLM CALL (Layer 2a) ---
    _start = time.perf_counter()
    response = llm_client.chat(
        messages=messages,
        tools=tool_catalog,
        system=system_prompt,
    )
    _elapsed_ms = (time.perf_counter() - _start) * 1000

    tokens_in = response.usage.input_tokens
    tokens_out = response.usage.output_tokens

    _task = get_current_task()
    if _task:
        try:
            _task.llm_call(
                "agent_turn",
                model=response.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=estimate_cost(response.model, tokens_in, tokens_out),
                duration_ms=round(_elapsed_ms),
            )
        except Exception:
            pass

    # --- TOOL EXECUTION (Layer 2b) ---
    if response.tool_calls:
        _agent = get_hiveloop_agent()
        for tool_call in response.tool_calls:
            if _agent is not None:
                with _agent.track_context(tool_call.name) as ctx:
                    result = tool_registry.execute(tool_call.name, tool_call.arguments)
                    ctx.set_payload({
                        "args": {k: str(v)[:100] for k, v in tool_call.arguments.items()},
                        "result_preview": str(result)[:200],
                        "success": result.success,
                    })
            else:
                result = tool_registry.execute(tool_call.name, tool_call.arguments)

            messages.append({"role": "tool", "content": str(result)})

    return response
```

---

## Architecture Patterns

### Single-agent system

The simplest case. One agent, one `hb.agent()` call, one task context at a time.

```python
hb = hiveloop.init(api_key="...", endpoint="...")
agent = hb.agent(agent_id="main", ...)

while True:
    message = queue.get()
    with agent.task(f"task-{uuid.uuid4().hex[:8]}", project="my-app") as task:
        set_current_task(task)
        set_hiveloop_agent(agent)
        try:
            process(message)
        finally:
            clear_current_task()
            clear_hiveloop_agent()
```

### Multi-agent system

Each agent gets its own `hb.agent()` handle. The contextvars ensure that concurrent agents (in threads or async tasks) don't cross-contaminate their telemetry.

```python
hb = hiveloop.init(api_key="...", endpoint="...")

for agent_config in config.agents:
    agent = create_agent(agent_config)
    agent._hiveloop = hb.agent(
        agent_id=agent_config.id,
        type=agent_config.role,
        version=agent_config.model,
        framework="my-framework",
    )
```

### API-driven agents (FastAPI, Flask)

Each HTTP request is a task. Set the task context in middleware or at the handler level.

```python
@app.post("/agents/{agent_id}/run")
async def run_agent(agent_id: str, body: RunRequest):
    agent = get_agent(agent_id)
    _hl = getattr(agent, "_hiveloop", None)

    if _hl:
        with _hl.task(f"{agent_id}-{uuid.uuid4().hex[:8]}", project="my-app") as task:
            set_current_task(task)
            set_hiveloop_agent(_hl)
            try:
                result = agent.run(body.message)
            finally:
                clear_current_task()
                clear_hiveloop_agent()
    else:
        result = agent.run(body.message)

    return {"response": result}
```

### Framework callbacks (LangChain, CrewAI, AutoGen)

If your framework provides callback hooks, wire HiveLoop into them:

```python
# LangChain example
class HiveLoopCallbackHandler(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_start = time.perf_counter()

    def on_llm_end(self, response, **kwargs):
        elapsed = (time.perf_counter() - self._llm_start) * 1000
        _task = get_current_task()
        if _task:
            _task.llm_call("llm_call", model=response.model,
                          tokens_in=response.usage.input_tokens,
                          tokens_out=response.usage.output_tokens,
                          duration_ms=round(elapsed))

    def on_tool_start(self, serialized, input_str, **kwargs):
        self._tool_ctx = get_hiveloop_agent().track_context(serialized["name"])
        self._tool_ctx.__enter__()

    def on_tool_end(self, output, **kwargs):
        if self._tool_ctx:
            self._tool_ctx.set_payload({"result_preview": str(output)[:200]})
            self._tool_ctx.__exit__(None, None, None)
```

---

## Incremental Adoption Strategy

Don't do everything at once. Ship in tiers:

### Tier 1 (highest value, lowest effort)
- `hiveloop.init()` + `hb.agent()` (Layer 0)
- `agent.task()` wrapping (Layer 1)
- `task.llm_call()` at your main LLM call sites (Layer 2a)
- `agent.report_issue()` at persistent error detection points (Layer 2c)

**Result:** Agent cards with heartbeats, task table with durations, cost explorer, issue badges. This alone answers "are my agents running?", "how much are they costing?", and "is anything broken?"

### Tier 2 (unlocks debugging)
- `agent.track_context()` at tool dispatch (Layer 2b)
- `task.plan()` + `task.plan_step()` if your framework uses planning (Layer 2c)
- `task.escalate()` at escalation decision points (Layer 2c)

**Result:** Full timeline with tool nodes, plan progress bars, escalation tracking. Now operators can answer "what went wrong in this specific run?"

### Tier 3 (unlocks operations)
- `queue_provider` callback for queue snapshots (Layer 2c)
- Approval request/received events (Layer 2c)
- `task.retry()` at retry points (Layer 2c)

**Result:** Queue depth visibility, WAITING badges, retry tracking. Operators can answer "is work piling up?" and "does anything need my attention?"

### Tier 4 (polish)
- `agent.todo()` at TODO lifecycle points (Layer 2c)
- `agent.scheduled()` at agent startup (Layer 2c)
- `prompt_preview`/`response_preview` on key LLM calls (Layer 2a)

**Result:** Pipeline tab fully populated. Complete operational picture.

---

## Common Mistakes

1. **Reusing task IDs.** If two executions share a task ID, the dashboard merges them into one row. Always generate unique IDs.

2. **Forgetting the `finally` block.** If you set `set_current_task(task)` but don't clear it in `finally`, a failed task leaves stale context for the next execution.

3. **Tracking too many functions.** 30+ action nodes per task makes the timeline unreadable. Track 5-7 high-value functions.

4. **Reporting issues for every single failure.** Use a threshold (3+ consecutive failures) before calling `report_issue()`. Individual tool failures are already captured by `track_context()`.

5. **Not using `issue_id`.** Without it, the SDK deduplicates on summary text. Timestamps or variable data in the summary defeats dedup.

6. **Forgetting to resolve issues.** An unresolved issue shows a red badge forever. Wire `resolve_issue()` into your recovery paths.

7. **Calling task-level methods outside task context.** `get_current_task()` returns `None` between tasks. Always check before calling. Agent-level methods (`report_issue`, `todo`, `queue_snapshot`, `scheduled`) work anywhere.

8. **Double-execute bug.** If your `with track_context()` fallback pattern runs the tool both inside and outside the `with` block on failure, the tool executes twice. Use the separated `__enter__`/`__exit__` pattern instead.

9. **Not passing `cost=`.** Without it, the Cost Explorer works but shows $0. Always pass `cost=estimate_cost(...)`.

10. **Plan step indices off by one.** `step_index` is zero-based. Step 1 in your plan is `step_index=0`.

---

## Shutdown

Call `hiveloop.shutdown()` when your application exits to flush any remaining buffered events:

```python
import atexit
atexit.register(hiveloop.shutdown)
```

Or in your framework's shutdown handler:

```python
def on_shutdown():
    hiveloop.shutdown(timeout=10)  # waits up to 10s for flush
```

---

## Quick Reference: All SDK Methods

### Module-level

| Method | Purpose |
|--------|---------|
| `hiveloop.init(**kwargs)` | Initialize SDK (singleton) |
| `hiveloop.shutdown(timeout=10)` | Flush and stop |
| `hiveloop.flush()` | Force flush without stopping |
| `hiveloop.reset()` | Flush, stop, clear singleton (testing) |

### Agent-level (`hb.agent()` returns this)

| Method | Purpose |
|--------|---------|
| `agent.task(task_id, **kw)` | Context manager for task tracking |
| `agent.track(name)` | Decorator for action tracking |
| `agent.track_context(name)` | Context manager for dynamic action tracking |
| `agent.report_issue(summary, **kw)` | Report persistent operational issue |
| `agent.resolve_issue(summary, **kw)` | Resolve a reported issue |
| `agent.todo(todo_id, action, summary, **kw)` | Report TODO lifecycle event |
| `agent.queue_snapshot(**kw)` | Report queue state |
| `agent.scheduled(items=[...])` | Report scheduled work |
| `agent.event(event_type, payload)` | Custom agent-level event |
| `agent.llm_call(name, model, **kw)` | LLM call outside task context |

### Task-level (`agent.task()` yields this)

| Method | Purpose |
|--------|---------|
| `task.llm_call(name, model, **kw)` | Log LLM call within task |
| `task.plan(goal, steps, **kw)` | Declare execution plan |
| `task.plan_step(step_index, action, summary, **kw)` | Update plan step |
| `task.escalate(reason, **kw)` | Escalate to human |
| `task.request_approval(summary, **kw)` | Request human approval |
| `task.approval_received(summary, **kw)` | Record approval decision |
| `task.retry(reason, **kw)` | Record retry attempt |
| `task.event(event_type, payload)` | Custom task-level event |
| `task.set_payload(dict)` | Add payload to completion event |
| `task.complete(**kw)` | Manual completion (non-context-manager) |
| `task.fail(**kw)` | Manual failure (non-context-manager) |

### Context manager (`agent.track_context()` yields this)

| Method | Purpose |
|--------|---------|
| `ctx.set_payload(dict)` | Attach metadata to the action_completed event |

# HiveBoard — User Manual Part 8: Tracking Tool Execution with `track_context()`

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *Your agent picks tools at runtime. The decorator needs the name at definition time. `track_context()` bridges that gap.*

---

## Table of Contents

1. [The Problem: Dynamic Tool Dispatch](#1-the-problem-dynamic-tool-dispatch)
2. [What `track_context()` Does](#2-what-track_context-does)
3. [The Minimal Pattern — Two Lines](#3-the-minimal-pattern--two-lines)
4. [The Full Turn Pattern — LLM + Tools Together](#4-the-full-turn-pattern--llm--tools-together)
5. [What to Expect on the Dashboard](#5-what-to-expect-on-the-dashboard)
6. [How It Works Inside](#6-how-it-works-inside)
7. [Attaching Data with `set_payload()`](#7-attaching-data-with-set_payload)
8. [Nesting — Actions Inside Actions](#8-nesting--actions-inside-actions)
9. [When to Use `track_context()` vs `@agent.track()`](#9-when-to-use-track_context-vs-agenttrack)
10. [Plumbing the Agent Handle](#10-plumbing-the-agent-handle)
11. [Real-World Case Study — loopCore](#11-real-world-case-study--loopcore)
12. [Usage Patterns](#12-usage-patterns)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. The Problem: Dynamic Tool Dispatch

In agentic systems, the LLM decides which tools to use at runtime. A typical agentic loop looks like this:

```python
while not done:
    response = llm.chat(messages, tools=tool_definitions)   # LLM picks tools
    for tool_call in response.tool_calls:                    # zero or more
        result = execute_tool(tool_call.name, tool_call.args)
```

The tool name — `crm_search`, `send_email`, `score_lead` — isn't known until the LLM returns its response. This creates a problem for observability: the `@agent.track()` decorator needs the action name at function definition time.

```python
@agent.track("???")      # ← what goes here? The tool name isn't known yet.
def execute_tool(name, args):
    ...
```

If you decorate `execute_tool()` with a fixed name like `"tool_execution"`, every tool call shows up as the same node on the timeline — you can't tell `crm_search` from `send_email`. You lose the narrative.

This is the gap `track_context()` fills.

---

## 2. What `track_context()` Does

`track_context()` is a context manager that does exactly what `@agent.track()` does — but the name is passed at runtime instead of decoration time.

```python
with agent.track_context("crm_search") as ctx:
    result = execute_tool("crm_search", args)
```

That block produces the same three events as the decorator:

| Event | When | Key fields |
|-------|------|-----------|
| `action_started` | On `with` entry | `action_id`, `parent_action_id`, `action_name` |
| `action_completed` | On clean exit | `duration_ms`, `status="success"`, payload |
| `action_failed` | On exception | `exception_type`, `exception_message`, `duration_ms` |

Same timeline nodes. Same blue color. Same duration tracking. Same nesting. Same exception capture with re-raise. The only difference is how the name gets there — runtime string instead of decoration-time string.

---

## 3. The Minimal Pattern — Two Lines

Inside your agentic loop, wherever tools are executed:

```python
for tool_call in response.tool_calls:
    with hiveloop_agent.track_context(tool_call.name) as ctx:
        result = execute_tool(tool_call.name, tool_call.args)
```

That's it. Each tool execution becomes a blue action node on the timeline with:

- **Tool name** — from the string you pass (`tool_call.name`)
- **Duration** — automatic (start-to-end timing)
- **Success/failure** — automatic (exceptions propagate but get recorded)
- **Nesting** — automatic (if you're already inside a tracked action, tool calls become children)

If `execute_tool()` raises, the exception is recorded as an `action_failed` event (red node on the timeline) and then re-raised. Your error handling continues to work normally.

If no tools are called on a turn (`response.tool_calls` is empty), no events are emitted. Zero overhead for zero-tool turns.

---

## 4. The Full Turn Pattern — LLM + Tools Together

A single turn in an agentic loop has two phases: the LLM decides what to do, then tools execute. Instrumenting both gives the complete picture.

```python
import time
from myproject.observability import get_current_task, get_hiveloop_agent

def run_turn(messages, tool_definitions):
    # ── Phase 1: LLM call — the agent reasons ──────────────────────
    _llm_start = time.perf_counter()
    response = llm.chat(messages, tools=tool_definitions)
    _llm_elapsed = (time.perf_counter() - _llm_start) * 1000

    _task = get_current_task()
    if _task:
        _task.llm_call(
            "agent_turn",
            model=response.model,
            tokens_in=response.usage.input_tokens,
            tokens_out=response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )

    # ── Phase 2: Tool execution — zero or more per turn ────────────
    _agent = get_hiveloop_agent()
    for tool_call in response.tool_calls:
        if _agent:
            with _agent.track_context(tool_call.name) as ctx:
                result = tool_registry.execute(tool_call.name, tool_call.arguments)
                ctx.set_payload({"result_preview": str(result)[:200]})
        else:
            result = tool_registry.execute(tool_call.name, tool_call.arguments)

    return response
```

Phase 1 uses `task.llm_call()` — this creates a purple LLM node with model, tokens, and cost data.

Phase 2 uses `agent.track_context()` — this creates a blue action node for each tool, with the tool name and execution duration.

On the dashboard timeline, a multi-turn task produces:

```
[■ agent_turn LLM] → [● crm_search] → [● score_lead] → [■ agent_turn LLM] → [● send_email]
    claude-sonnet       0.8s              0.2s              claude-sonnet        1.1s
```

Purple LLM nodes for the reasoning, blue action nodes for each tool, all in sequence with durations. The alternating pattern — reason, act, reason, act — is the heartbeat of an agentic system, and now it's visible.

---

## 5. What to Expect on the Dashboard

### 5.1 Timeline — before and after

**Before (Layer 1 only, no tool tracking):**

```
TIMELINE  task-lead-4801  ⏱ 12.4s  🤖 lead-qualifier  ✔ completed  ◆ 3 LLM

  [■ phase1 LLM]  [■ phase2 LLM]  [■ phase1 LLM]
       3.1s             4.2s            2.8s
```

You see the LLM calls, but between them — nothing. The 4.2 seconds of `phase2` included tool execution, but you don't know which tools ran or how long each one took. The tool execution time is hidden inside the LLM turn duration.

**After (with `track_context()`):**

```
TIMELINE  task-lead-4801  ⏱ 12.4s  🤖 lead-qualifier  ✔ completed  ◆ 3 LLM

  [■ phase1 LLM] → [● crm_search] → [● score_lead] → [■ phase2 LLM] → [● send_email] → [■ phase1 LLM]
       3.1s            0.8s              0.2s              2.1s             1.1s             2.8s
```

Now you can see:
- Which tools ran and in what order
- How long each tool took (the 4.2-second phase2 was actually 0.8s CRM + 0.2s scoring + 2.1s LLM + 1.1s email)
- Whether a tool is a bottleneck (1.1s for `send_email` — is that normal?)
- The complete reason → act → reason → act rhythm

### 5.2 Timeline node types

The timeline now has three node types, each with a distinct visual:

| Node | Shape | Color | Source | What it represents |
|------|-------|-------|--------|--------------------|
| Task events | Circle (●) | Green | `agent.task()` | Task started/completed/failed |
| Action events | Circle (●) | Blue | `@agent.track()` or `track_context()` | Function or tool execution |
| LLM events | Square (■) | Purple | `task.llm_call()` | LLM API call |

`track_context()` produces the same blue action nodes as `@agent.track()`. The dashboard doesn't distinguish between them — a tool tracked with `track_context("crm_search")` looks identical to a function decorated with `@agent.track("crm_search")`.

### 5.3 Clicking a tool node

Click any blue action node to see the detail panel:

```
● crm_search  20:13:14.892
  event         action_completed
  action_name   crm_search
  duration      0.8s
  status        success
```

If you used `ctx.set_payload()` (Section 7), the payload fields also appear:

```
● crm_search  20:13:14.892
  event            action_completed
  action_name      crm_search
  duration         0.8s
  status           success
  args             {"query": "Acme Corp", "fields": "name,email,phone"}
  result_preview   {"name": "Acme Corp", "email": "jane@acme.com", ...}
  success          true
```

This tells you exactly what was passed to the tool and what came back — invaluable for debugging "the agent called the right tool but got unexpected results."

### 5.4 Failed tool nodes

If a tool throws an exception, the node turns red:

```
● crm_search  20:13:14.892    ← red node
  event              action_failed
  action_name        crm_search
  duration           0.3s
  status             failure
  exception_type     ConnectionError
  exception_message  CRM API timeout after 5000ms
```

The exception is recorded and then re-raised — your existing error handling continues to work. The timeline shows where it broke and what the error was.

### 5.5 Activity Stream

Tool executions appear in the Activity Stream as action events:

```
● action_started     lead-qualifier > task-lead-4801      just now    (crm_search)
● action_completed   lead-qualifier > task-lead-4801      just now    (crm_search)
● action_started     lead-qualifier > task-lead-4801      just now    (score_lead)
● action_completed   lead-qualifier > task-lead-4801      just now    (score_lead)
```

Use the **action** stream filter to see only tool executions. Use the **error** filter to show only `action_failed` events — the tools that broke.

### 5.6 Zero-tool turns

When the LLM responds without calling any tools (a pure text response), the `for tool_call in response.tool_calls` loop body never executes. No `track_context()` opens, no action events emit. The timeline shows the LLM node with nothing after it until the next turn. This is correct — there's nothing to track.

---

## 6. How It Works Inside

### 6.1 The lifecycle

`agent.track_context(action_name)` returns an `_ActionContext` object that manages three phases:

**Phase 1 — `__enter__` (on `with` entry):**
1. Generates a unique `action_id` (UUID)
2. Reads `parent_action_id` from `contextvars` — if this block is inside another tracked action, it becomes a child
3. Sets its own `action_id` as the current context (so nested blocks become its children)
4. Emits `action_started` event with the action name and parent reference
5. Starts timing (`time.perf_counter()`)

**Phase 2a — Clean exit (no exception):**
1. Stops timing, calculates `duration_ms`
2. Emits `action_completed` event with `status="success"`, duration, and any payload set via `set_payload()`
3. Restores the previous `parent_action_id` in `contextvars`

**Phase 2b — Exception exit:**
1. Stops timing, calculates `duration_ms`
2. Emits `action_failed` event with `exception_type`, `exception_message`, and duration
3. Restores the previous `parent_action_id` in `contextvars`
4. **Re-raises the exception** — `track_context()` never swallows errors

### 6.2 Thread and async safety

The parent-child chain is tracked via `contextvars.ContextVar`, which provides automatic isolation for both threads and asyncio tasks. Each thread and each `async` coroutine gets its own context. Token-based restoration (`contextvars` tokens) ensures the parent context is always correctly restored, even when multiple tasks are running concurrently.

This means you can safely use `track_context()` in:
- Synchronous code
- `async/await` code
- Multi-threaded agents
- Agents that process multiple tasks concurrently

### 6.3 Transport

Events from `track_context()` flow through the same path as all SDK events: they're enqueued in a thread-safe buffer and flushed to HiveBoard via batched HTTP POST to `/v1/ingest`. The `with` block returns immediately after enqueuing — it doesn't wait for the server to acknowledge the event. Tool execution latency is unaffected by observability.

---

## 7. Attaching Data with `set_payload()`

The context object returned by `track_context()` supports `set_payload()` for attaching tool-specific metadata. This data appears in the detail panel when you click the action node on the timeline.

### 7.1 Basic usage

```python
with hiveloop_agent.track_context(tool_call.name) as ctx:
    result = execute_tool(tool_call.name, tool_call.args)
    ctx.set_payload({
        "args": {k: str(v)[:100] for k, v in tool_call.args.items()},
        "result_preview": str(result)[:200],
    })
```

### 7.2 What to include

The payload is a dictionary of arbitrary key-value pairs. Useful fields for tool tracking:

| Field | Why it's useful | Example |
|-------|----------------|---------|
| `args` | See what was passed to the tool | `{"query": "Acme Corp"}` |
| `result_preview` | See what the tool returned | `{"name": "Acme Corp", ...}` |
| `success` | Explicit success/failure flag | `true` |
| `error` | Error message (for tools that return errors instead of raising) | `"404 Not Found"` |
| `source` | Where the tool call originated | `"agent_decision"` |

### 7.3 Truncation matters

Tool arguments and results can be large. Always truncate:

```python
# ✅ Safe:
ctx.set_payload({"result": str(result)[:200]})

# ❌ Dangerous:
ctx.set_payload({"result": result})   # could be megabytes of CRM data
```

The SDK doesn't enforce payload size limits, but large payloads increase network traffic and make the detail panel unreadable. A 100-200 character preview is usually enough for debugging.

### 7.4 When `set_payload()` is called

Call it after the tool executes but before the `with` block exits. The payload is attached to the `action_completed` event:

```python
with hiveloop_agent.track_context("crm_search") as ctx:
    result = execute_tool("crm_search", args)   # tool runs
    ctx.set_payload({"preview": str(result)})    # attach data
    # ← on exit, action_completed fires with the payload
```

If the tool raises, `set_payload()` is never reached and `action_failed` fires without a payload. The exception info (type + message) is attached automatically.

---

## 8. Nesting — Actions Inside Actions

`track_context()` supports automatic nesting via `contextvars`. If a `track_context()` block is opened inside another tracked action (either a decorator or another context manager), the inner block becomes a child.

### 8.1 Tool calls inside a tracked function

```python
@agent.track("process_lead")
def process_lead(lead):
    # This tool call is a child of "process_lead"
    with agent.track_context("crm_search") as ctx:
        data = crm.search(lead.email)
        ctx.set_payload({"found": data is not None})

    # This too
    with agent.track_context("enrich_company") as ctx:
        enrichment = clearbit.lookup(lead.company)
```

Timeline nesting:

```
process_lead (3.2s)
  ├── crm_search (0.8s)
  └── enrich_company (1.4s)
```

### 8.2 Nested context managers

```python
with agent.track_context("outer_step") as outer:
    with agent.track_context("inner_step") as inner:
        do_work()
```

Timeline nesting:

```
outer_step (2.1s)
  └── inner_step (1.8s)
```

### 8.3 Mixed decorator + context manager

```python
with agent.task("task-123") as task:
    @agent.track("step1")
    def score():
        with agent.track_context("llm_inference") as ctx:
            result = model.predict(lead)
            ctx.set_payload({"score": result})
        return result

    score()
```

This produces the chain: `task → step1 → llm_inference`, all linked via `parent_action_id`. The dashboard renders these as nested nodes in the timeline.

### 8.4 Why nesting matters for tools

In agentic systems, some tools call other tools. A `research` tool might call `web_search` and then `summarize`. With nesting:

```python
with agent.track_context("research") as ctx:
    search_results = web_search(query)
    with agent.track_context("summarize") as inner:
        summary = llm_summarize(search_results)
```

The timeline shows `summarize` as a child of `research`, making the tool call hierarchy visible.

---

## 9. When to Use `track_context()` vs `@agent.track()`

Both produce identical events. The difference is when the action name is available.

| Situation | Recommended | Why |
|-----------|------------|-----|
| Named, reusable functions | `@agent.track("name")` | Decorator is cleaner — name is fixed and matches the function |
| Dynamic tool dispatch (agentic loops) | `track_context(tool_name)` | Tool name comes from the LLM response at runtime |
| Class methods where agent isn't available at definition time | `track_context("name")` | Context manager works inside the method body |
| Inline code blocks (not a separate function) | `track_context("name")` | No function to decorate |
| Conditional tracking | `track_context("name")` | Only opens the context when needed |
| Need `set_payload()` mid-execution | `track_context("name")` | The decorator doesn't expose a context object |

**The rule of thumb:** If the action name is known at function definition time and the function is standalone, use the decorator. For everything else, use `track_context()`.

In practice, a well-instrumented agentic system uses both:

- `@agent.track()` for the agent's own functions — `score_lead()`, `enrich_data()`, `generate_email()`
- `track_context()` for dynamic tool execution inside the agentic loop — whatever tools the LLM picks at runtime

---

## 10. Plumbing the Agent Handle

`track_context()` is called on the agent handle (`hiveloop_agent.track_context(...)`), not on the task object. This means you need the HiveLoop agent handle available at the tool execution site — which may be deep in the call stack.

### 10.1 The challenge

The tool execution loop is often many function calls away from where `hb.agent()` was called:

```
main.py           → hb.agent("my-agent")          # agent created here
  agent_manager.py  → agent.run()
    loop.py           → run_turn()
      loop.py           → execute_tools()          # agent handle needed HERE
```

Passing the agent handle through every function signature is tedious and pollutes your API.

### 10.2 Solution — `contextvars` (recommended)

Add a second `ContextVar` for the agent handle alongside your existing task context:

```python
# observability.py
import contextvars
from typing import Optional, Any

# Existing: task context
_current_task = contextvars.ContextVar('hiveloop_task', default=None)

# New: agent handle context
_current_hiveloop_agent = contextvars.ContextVar('hiveloop_agent', default=None)


def set_hiveloop_agent(agent: Any) -> None:
    """Set the HiveLoop agent handle for the current execution context."""
    _current_hiveloop_agent.set(agent)

def get_hiveloop_agent() -> Optional[Any]:
    """Get the current HiveLoop agent handle, or None if not initialized."""
    return _current_hiveloop_agent.get()

def clear_hiveloop_agent() -> None:
    """Clear the current HiveLoop agent handle."""
    _current_hiveloop_agent.set(None)
```

Set it alongside the task at the top of the execution path:

```python
with hiveloop_agent.task(task_id, project="my-project") as task:
    set_current_task(task)
    set_hiveloop_agent(hiveloop_agent)        # ← new
    try:
        result = run_agent_loop()
    finally:
        clear_current_task()
        clear_hiveloop_agent()                # ← new
```

Use it at the tool execution site:

```python
from myproject.observability import get_hiveloop_agent

_agent = get_hiveloop_agent()
if _agent:
    with _agent.track_context(tool_name) as ctx:
        result = execute_tool(tool_name, args)
        ctx.set_payload({"result_preview": str(result)[:200]})
else:
    result = execute_tool(tool_name, args)
```

Same pattern as `get_current_task()` — set it once at the top, use it anywhere deeper, clean it up in `finally`.

### 10.3 Why a separate `ContextVar`?

The task object and agent handle have different lifetimes and scopes:

| Object | Scope | Lifetime | Used for |
|--------|-------|----------|----------|
| Task (`get_current_task()`) | One task execution | Start → complete/fail | `task.llm_call()`, `task.plan()`, `task.escalate()`, etc. |
| Agent (`get_hiveloop_agent()`) | Entire agent run | Agent startup → shutdown | `track_context()`, `agent.report_issue()`, `agent.queue_snapshot()`, etc. |

A single task starts and ends many times during an agent's lifetime. The agent handle persists. Keeping them in separate `ContextVar`s avoids lifecycle confusion and lets you use agent-level methods even outside a task context.

---

## 11. Real-World Case Study — loopCore

This section walks through how an actual agentic framework (loopCore) implemented `track_context()` for tool execution. It illustrates the decisions and subtleties you'll encounter in a real codebase.

### 11.1 The starting point

loopCore's agentic loop calls tools at a single chokepoint in `loop.py`:

```python
# loop.py, line 1501 — the single tool execution site
tool_result = self.tool_registry.execute(tool_name, parameters)
```

Every tool call flows through this one line. Zero or more tools per turn, chosen by the LLM at runtime.

### 11.2 The plumbing

The `observability.py` module was extended with a second `ContextVar`:

```python
# observability.py — added alongside existing task context
_current_hiveloop_agent = contextvars.ContextVar("hiveloop_agent", default=None)

def set_hiveloop_agent(agent):
    _current_hiveloop_agent.set(agent)

def get_hiveloop_agent():
    return _current_hiveloop_agent.get()

def clear_hiveloop_agent():
    _current_hiveloop_agent.set(None)
```

In `agent_manager.py`, the agent handle is set alongside the task:

```python
_hiveloop_task = _hiveloop_ctx.__enter__()
set_current_task(_hiveloop_task)
set_hiveloop_agent(_hiveloop_agent)          # ← added

# ... in the finally block:
clear_current_task()
clear_hiveloop_agent()                       # ← added
```

### 11.3 The implementation — and a subtlety

The first attempt used a straightforward `with` block:

```python
# First attempt:
_hl_agent = get_hiveloop_agent()
if _hl_agent is not None:
    try:
        with _hl_agent.track_context(tool_name) as _ctx:
            tool_result = self.tool_registry.execute(tool_name, parameters)
            _ctx.set_payload({...})
    except Exception:
        tool_result = self.tool_registry.execute(tool_name, parameters)
else:
    tool_result = self.tool_registry.execute(tool_name, parameters)
```

This had a problem: if `track_context()` itself failed (not the tool), the `except` block would execute the tool a second time. And if `track_context()` succeeded but the tool returned an error result (without raising — `ToolResult(success=False, error="...")`), the `with` block wouldn't know the tool logically failed.

The fix separated the three phases — open context, execute tool, close context:

```python
# Final implementation:
_hl_agent = get_hiveloop_agent()
_hl_ctx = None
if _hl_agent is not None:
    try:
        _hl_ctx = _hl_agent.track_context(tool_name)
        _hl_ctx.__enter__()
    except Exception:
        _hl_ctx = None

tool_result = self.tool_registry.execute(tool_name, parameters)     # always runs exactly once

if _hl_ctx is not None:
    try:
        _hl_ctx.set_payload({
            "args": {k: str(v)[:100] for k, v in parameters.items()},
            "result_preview": (tool_result.output or "")[:200],
            "success": tool_result.success,
            "error": tool_result.error,
        })
        _hl_ctx.__exit__(None, None, None)
    except Exception:
        pass
```

Key properties of this pattern:

- **Single execution path** — `tool_registry.execute()` runs exactly once, regardless of whether tracking is active
- **Safe context opening** — if `track_context()` fails to initialize, `_hl_ctx` stays `None` and the tool runs without tracking
- **Rich payload** — includes truncated args, result preview, and the explicit `success`/`error` fields from the tool result
- **Graceful context closing** — if `set_payload()` or `__exit__()` fails, it's silently ignored

### 11.4 Why this matters

This pattern — manual `__enter__()` / `__exit__()` instead of `with` — is unusual but important when the tool execution framework doesn't use exceptions for error signaling. If your `execute_tool()` raises on failure, the standard `with` block works perfectly. If it returns an error object (like `ToolResult(success=False)`), you need the separated pattern to avoid double execution.

### 11.5 The result

On HiveBoard's timeline, loopCore's tasks now show:

```
[■ phase1_reasoning LLM] → [● crm_search] → [■ phase2_tool_use LLM] → [● send_email]
     claude-sonnet             0.8s              claude-sonnet              1.1s
```

Purple LLM nodes from `task.llm_call()`, blue action nodes from `track_context()`, each with duration and payload. Click a tool node to see the arguments and result. Click an LLM node to see the model, tokens, and cost. The complete turn-by-turn narrative is visible.

---

## 12. Usage Patterns

### 12.1 Agentic tool dispatch (the primary use case)

```python
for tool_call in response.tool_calls:
    with hiveloop_agent.track_context(tool_call.name) as ctx:
        result = tool_registry.execute(tool_call.name, tool_call.arguments)
        ctx.set_payload({
            "args": {k: str(v)[:100] for k, v in tool_call.arguments.items()},
            "result_preview": str(result)[:200],
        })
```

### 12.2 Dynamic pipeline steps

When a pipeline has steps that vary at runtime:

```python
for step_name in pipeline.steps:
    with hiveloop_agent.track_context(step_name.lower().replace(" ", "_")) as ctx:
        run_step(step_name)
```

### 12.3 Conditional tracking

Only track when it matters:

```python
_agent = get_hiveloop_agent()
if expensive_operation and _agent:
    with _agent.track_context("heavy_computation") as ctx:
        result = compute(data)
        ctx.set_payload({"records": len(data)})
else:
    result = compute(data)
```

### 12.4 Class methods (agent not available at definition time)

When you can't use the decorator because the agent handle doesn't exist when the class is defined:

```python
class ToolExecutor:
    def execute(self, tool_name, args):
        _agent = get_hiveloop_agent()
        if _agent:
            with _agent.track_context(tool_name) as ctx:
                result = self._run(tool_name, args)
                ctx.set_payload({"success": result.ok})
                return result
        return self._run(tool_name, args)

    def _run(self, tool_name, args):
        # actual execution logic, unchanged
        ...
```

### 12.5 Retry loops with per-attempt tracking

Track each attempt separately:

```python
for attempt in range(max_retries):
    with hiveloop_agent.track_context(f"{tool_name}_attempt_{attempt + 1}") as ctx:
        try:
            result = execute_tool(tool_name, args)
            ctx.set_payload({"attempt": attempt + 1, "success": True})
            break
        except RetryableError as e:
            ctx.set_payload({"attempt": attempt + 1, "error": str(e)[:100]})
            # exception re-raised by track_context, caught by retry loop
            if attempt == max_retries - 1:
                raise
            time.sleep(2 ** attempt)
```

Timeline for a tool that succeeds on attempt 2:

```
[● crm_search_attempt_1] → [● crm_search_attempt_2]
       0.3s  ✗ failed           0.8s  ✔ success
```

### 12.6 Combining with `task.llm_call()` for the full picture

The most valuable pattern — instrument both the LLM reasoning and the tool execution in each turn. See Section 4 for the complete code. The result:

```
Turn 1:  [■ reasoning LLM] → [● search_crm] → [● enrich_company]
Turn 2:  [■ reasoning LLM] → [● score_lead]
Turn 3:  [■ reasoning LLM] → [● send_email]
Turn 4:  [■ summarize LLM]    (no tools — pure text response)
```

This is the complete narrative of an agent working through a task. Every LLM decision and every tool execution is visible, timed, and debuggable.

---

## 13. Troubleshooting

### 13.1 Tool nodes not appearing on the timeline

**Symptom:** LLM nodes appear (purple), but no blue action nodes for tool execution.

**Possible causes:**

1. **`get_hiveloop_agent()` returns `None`.** The agent handle isn't set in the context. Check that `set_hiveloop_agent()` is called at the task boundary and that the execution path is in the same thread/async context. Add a temporary debug line: `print(f"agent={get_hiveloop_agent()}")` before the tool execution.

2. **The `if _agent:` guard is skipping.** Same cause — the agent isn't in context. Verify the plumbing in Section 10.

3. **The `try/except` is swallowing errors.** If `track_context()` raises during `__enter__()`, your except block may silently fall through to untracked execution. Temporarily remove the try/except to see the error.

4. **No tools were called.** If the LLM responded without tool calls, there's nothing to track. Check `len(response.tool_calls)` — if it's zero, no action events are expected.

### 13.2 All tool nodes show the same name

**Symptom:** Every tool execution appears as `"execute_tool"` instead of the actual tool name.

**Cause:** You're tracking the wrapper function instead of passing the tool name:

```python
# ❌ Same name for every tool:
with agent.track_context("execute_tool"):
    execute_tool(tool_call.name, args)

# ✅ Dynamic name per tool:
with agent.track_context(tool_call.name):
    execute_tool(tool_call.name, args)
```

### 13.3 Tool appears to execute twice

**Symptom:** The tool's side effects happen twice (e.g. email sent twice, record created twice).

**Cause:** Your fallback pattern has a double-execute bug:

```python
# ❌ Double execute — if track_context works but the tool raises,
#    the except block runs the tool again:
try:
    with agent.track_context(name):
        result = execute_tool(name, args)     # first execution
except Exception:
    result = execute_tool(name, args)         # second execution!
```

**Fix:** Separate the context lifecycle from the tool execution (see Section 11.3 for the pattern), or only catch `track_context` initialization errors:

```python
# ✅ Single execution:
_ctx = None
try:
    _ctx = agent.track_context(tool_name)
    _ctx.__enter__()
except Exception:
    _ctx = None

result = execute_tool(tool_name, args)        # always runs exactly once

if _ctx:
    try:
        _ctx.__exit__(None, None, None)
    except Exception:
        pass
```

### 13.4 Missing duration on tool nodes

**Symptom:** Tool nodes appear but show 0ms or no duration.

**Cause:** The context manager was opened and closed without the tool execution happening inside it:

```python
# ❌ Tool runs outside the context:
with agent.track_context(name):
    pass                                    # context opens and closes immediately
result = execute_tool(name, args)           # runs after tracking is done
```

**Fix:** The tool execution must happen between `__enter__()` and `__exit__()`.

### 13.5 Payload not showing in detail panel

**Symptom:** You called `ctx.set_payload()` but clicking the node shows no payload fields.

**Possible causes:**

1. **`set_payload()` was called after an exception.** If the tool raises before `set_payload()`, the context manager exits via the exception path — no payload is attached.

2. **Payload value is not JSON-serializable.** The SDK silently drops non-serializable values. Ensure all values are strings, numbers, booleans, or lists/dicts of those types.

3. **Payload is too large and was truncated server-side.** Check your value sizes. Keep previews under 500 characters.

### 13.6 Nesting is flat (children appear as siblings)

**Symptom:** A `track_context()` inside a `@agent.track()` function should be a child, but both appear at the same level on the timeline.

**Cause:** The `contextvars` propagation may be broken. This can happen when:
- The inner call runs in a different thread (without copying the context)
- The agent handle used for the inner call is a different agent instance

**Fix:** Verify both the decorator and the context manager use the same agent handle. If using threads, ensure `contextvars` context is copied (`contextvars.copy_context().run(...)`).

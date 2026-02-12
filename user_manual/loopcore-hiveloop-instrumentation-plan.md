# loopCore → HiveLoop Instrumentation Plan

**Date:** 2026-02-12
**Status:** Ready for implementation
**Prerequisite:** `pip install -e C:\code\hiveboard\src\sdk` ✅

---

## Overview

Instrument loopCore with HiveLoop to get full observability on the HiveBoard dashboard. The integration follows 4 incremental steps — each is independently valuable. Stop at any step and you already have working observability.

| Step | What | Effort | What You Get |
|------|------|--------|--------------|
| 1 | Init + dynamic agent registration | 15 min | Agents appear on dashboard with live heartbeat + stuck detection |
| 2 | Task context + key decorators | 30 min | Task timelines with action tracking |
| 3 | LLM call tracking | 1 hour | Cost Explorer with per-call, per-model, per-agent breakdowns |
| 4 | Narrative events | 30 min | Escalations, approvals, retries, plans, issues in the timeline |

---

## Step 1: Init + Dynamic Agent Registration (15 min)

### 1.1 Initialize HiveLoop at startup

**File:** `src/loop_core/api/app.py` — end of `create_app()` (after line ~3989)

```python
import hiveloop

hb = hiveloop.init(
    api_key="hb_live_dev000000000000000000000000000000",
    endpoint="http://localhost:8000",
    environment="production",
    group="loopcolony",
    flush_interval=2.0,
    batch_size=50,
)
```

Store `hb` so other modules can access it:

```python
app.state.hiveloop = hb
```

Or use a module-level accessor pattern:

```python
# src/loop_core/observability.py (new file)
import hiveloop

_hb = None

def init_hiveloop(**kwargs):
    global _hb
    _hb = hiveloop.init(**kwargs)
    return _hb

def get_hb():
    return _hb
```

### 1.2 Register agents dynamically

loopCore creates agents dynamically — any agent can be created at any time. Register each agent with HiveLoop at creation time.

**File:** Wherever loopCore creates agent instances (agent factory / manager)

```python
from loop_core.observability import get_hb

def register_agent_with_hiveloop(agent):
    """Call this wherever loopCore creates a new agent."""
    hb = get_hb()
    if hb is None:
        return None  # HiveLoop not initialized (e.g. testing)

    hiveloop_agent = hb.agent(
        agent_id=agent.name,          # unique identifier
        type=agent.type,              # e.g. "sales", "support", "research"
        version=getattr(agent, 'version', None),
        framework="loopcore",
        heartbeat_interval=30,
        stuck_threshold=300,
    )
    return hiveloop_agent
```

**Key behaviors:**
- `hb.agent()` is **idempotent** — calling it twice with the same `agent_id` returns the same handle
- Each agent gets its own background heartbeat thread automatically
- The agent appears on the HiveBoard dashboard immediately

**Agent deletion:** No explicit unregister needed. When loopCore deletes an agent, just stop using the handle. The heartbeat stops, and after `stuck_threshold` seconds (5 min) the dashboard shows the agent as offline/stuck. This is the correct observability behavior — you want to *see* that an agent disappeared.

### 1.3 Store the mapping

Keep a dictionary of loopCore agent → HiveLoop agent handles:

```python
# Wherever makes sense in loopCore's architecture
_hiveloop_agents: dict[str, hiveloop.Agent] = {}

def get_hiveloop_agent(agent_name: str):
    return _hiveloop_agents.get(agent_name)
```

### ✅ Checkpoint

Start HiveBoard server, start loopCore, create an agent. Verify:
- Agent appears in the Hive sidebar
- Heartbeat pulses every 30 seconds
- Status shows as "idle" or "processing"

---

## Step 2: Task Context + Key Decorators (30 min)

### 2.1 Wrap agent runs with task context

**File:** `src/loop_core/agent_manager.py` — around line 669-674 where `agent.run()` is called

```python
hiveloop_agent = get_hiveloop_agent(agent.name)

if hiveloop_agent:
    with hiveloop_agent.task(event_id, project="loopcolony", type="agent_run") as task:
        # Store task on execution context so deeper code can access it
        # (see "Plumbing" section below)
        result = agent.run(...)
else:
    result = agent.run(...)
```

**Project ID:** Use a project that makes sense for your setup. If agents serve different purposes, you could map agent type → project (e.g. `"sales-pipeline"`, `"support-triage"`). For now, one project like `"loopcolony"` is fine.

### 2.2 Add decorators to key functions

Start with the three highest-value functions:

**`AgentManager.run_agent()`** — `agent_manager.py:594`
```python
@agent.track("run_agent")
def run_agent(self, ...):
    # existing logic unchanged
```

**`ReflectionManager.reflect()`** — `reflection.py:318`
```python
@agent.track("reflect")
def reflect(self, ...):
    # existing logic unchanged
```

**`PlanningManager.create_plan()`** — `planning.py`
```python
@agent.track("create_plan")
def create_plan(self, ...):
    # existing logic unchanged
```

**Note:** Since these are instance methods on classes, the decorator needs to reference the HiveLoop agent. Two approaches:

**Option A — Get agent from context inside the method:**
```python
def run_agent(self, ...):
    hiveloop_agent = get_hiveloop_agent(self.agent.name)
    if hiveloop_agent:
        @hiveloop_agent.track("run_agent")
        def _inner():
            # original logic
            ...
        return _inner()
    else:
        # original logic without tracking
        ...
```

**Option B — Use `track_context()` (if the SDK supports it):**
```python
def run_agent(self, ...):
    hiveloop_agent = get_hiveloop_agent(self.agent.name)
    if hiveloop_agent:
        with hiveloop_agent.track_context("run_agent"):
            # original logic
            ...
    else:
        # original logic
        ...
```

### ✅ Checkpoint

Run an agent task. Verify on the dashboard:
- Task appears in the task list
- Timeline shows action nodes for `run_agent`, `reflect`, `create_plan`
- Duration is tracked for each action

---

## Step 3: LLM Call Tracking (1 hour)

This is the highest-value instrumentation — it powers the Cost Explorer.

### 3.1 Phase 1 reasoning call

**File:** `src/loop_core/loop.py` — after line ~1274 (`phase1_client.complete_json()` returns)

```python
# After the Phase 1 LLM call completes:
if task:  # HiveLoop task from context
    task.llm_call(
        "phase1_reasoning",
        model=phase1_model_name,          # e.g. "claude-sonnet-4-20250514"
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost=calculated_cost,             # USD — calculate from model pricing
        duration_ms=elapsed_ms,
        prompt_preview=prompt[:500],      # optional, first 500 chars
        response_preview=str(response)[:500],  # optional
    )
```

### 3.2 Phase 2 parameter generation call

**File:** `src/loop_core/loop.py` — after line ~1413 (`phase2_client.complete_with_tools()` returns)

```python
if task:
    task.llm_call(
        "phase2_parameters",
        model=phase2_model_name,
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost=calculated_cost,
        duration_ms=elapsed_ms,
    )
```

### 3.3 Secondary LLM calls (lower priority)

| Call | File:Line | LLM Call Name |
|------|-----------|---------------|
| Reflection | `reflection.py:364` | `"reflection"` |
| Planning | `planning.py` | `"plan_creation"` |
| Heartbeat summary | `agent.py:198` | `"heartbeat_summary"` |
| Context compaction | `context.py` | `"context_compaction"` |

Same pattern — after each `llm_client.complete*()` call, add `task.llm_call()` or `agent.llm_call()` (for agent-level calls outside a task context).

### Cost calculation

If the LLM client doesn't return cost directly, calculate from token counts:

```python
# Model pricing lookup (approximate, per 1K tokens)
MODEL_COSTS = {
    "claude-sonnet-4-20250514": (0.003, 0.015),       # (input, output) per 1K
    "claude-haiku-4-5-20251001": (0.00025, 0.00125),
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
}

def calculate_cost(model, tokens_in, tokens_out):
    costs = MODEL_COSTS.get(model, (0.001, 0.005))
    return round((tokens_in / 1000) * costs[0] + (tokens_out / 1000) * costs[1], 6)
```

### ✅ Checkpoint

Run an agent task. Verify on the dashboard:
- Cost Explorer shows non-zero costs
- By Model breakdown shows which models are used
- By Agent breakdown shows per-agent costs
- Individual LLM calls visible with token counts

---

## Step 4: Narrative Events (30 min)

### 4.1 Plans

**File:** `planning.py` — inside `PlanningManager.create_plan()`

```python
if task:
    task.plan(goal_description, step_names)

# And as steps complete:
if task:
    task.plan_step(step_index, "completed", step_summary)
```

### 4.2 Escalations

**File:** `loop.py:936-948` — when reflection returns "escalate"

```python
if task:
    task.escalate(
        f"Agent escalated: {reason}",
        assigned_to="human-reviewer",
    )
```

### 4.3 Approvals

**File:** `runtime.py:1170-1171` — when event goes to `pending_approval`

```python
if task:
    task.request_approval(
        f"Approval needed: {event.summary}",
        approver="human",
    )
```

**File:** `runtime.py:1183-1204` — when `approve_event()` is called

```python
if task:
    task.approval_received(
        "Approved by operator",
        approved_by="human",
        decision="approved",
    )
```

**File:** `runtime.py:1206-1219` — when `drop_event()` is called

```python
if task:
    task.approval_received(
        "Dropped by operator",
        approved_by="human",
        decision="rejected",
    )
```

### 4.4 Retries

**File:** `agent.py:801-858` — failed runs creating retry TODOs

```python
if task:
    task.retry(f"Retry: {failure_reason}", attempt=attempt_number)
```

### 4.5 Issues

**File:** `issue_tools.py:163-234` — `report_issue` tool

```python
hiveloop_agent = get_hiveloop_agent(agent_name)
if hiveloop_agent:
    hiveloop_agent.report_issue(
        summary=issue_message,
        severity="high",
        category="tool_error",
    )
```

### ✅ Checkpoint

Trigger an escalation or error in loopCore. Verify on the dashboard:
- Escalation events appear in the Activity Stream
- Timeline shows the full narrative: task → actions → escalation → approval
- Issues appear in the Pipeline view

---

## Plumbing: Passing the Task Object

The main architectural challenge: `agent.task()` creates a context manager at the `agent_manager` level, but `task.llm_call()` and other events need to be called deep inside `loop.py`.

**Recommended approach — `contextvars`:**

```python
# src/loop_core/observability.py
import contextvars

_current_task = contextvars.ContextVar('hiveloop_task', default=None)

def set_current_task(task):
    _current_task.set(task)

def get_current_task():
    return _current_task.get()
```

At the `agent.task()` wrapper:
```python
with hiveloop_agent.task(event_id, project="loopcolony", type="agent_run") as task:
    set_current_task(task)
    try:
        result = agent.run(...)
    finally:
        set_current_task(None)
```

Anywhere deeper in the code:
```python
from loop_core.observability import get_current_task

task = get_current_task()
if task:
    task.llm_call(...)
```

This is async-safe and thread-safe — each agent thread/coroutine gets its own task context automatically.

---

## File Summary

| File | Changes |
|------|---------|
| `src/loop_core/observability.py` | **New file** — `init_hiveloop()`, `get_hb()`, agent registry, `get_current_task()` |
| `src/loop_core/api/app.py` | Call `init_hiveloop()` in `create_app()` |
| `src/loop_core/agent_manager.py` | Register agents with HiveLoop on creation, wrap `agent.run()` with `agent.task()` |
| `src/loop_core/loop.py` | Add `task.llm_call()` after Phase 1 and Phase 2 LLM calls |
| `src/loop_core/reflection.py` | Add `task.llm_call()` after reflection LLM call |
| `src/loop_core/planning.py` | Add `task.plan()` + `task.llm_call()` |
| `src/loop_core/runtime.py` | Add `task.request_approval()` / `task.approval_received()` |
| `issue_tools.py` | Add `agent.report_issue()` |

---

## Prerequisites Before Starting

1. HiveBoard server running: `cd src && uvicorn backend.app:app --port 8000`
2. Environment variable set: `$env:HIVEBOARD_DEV_KEY = "hb_live_dev000000000000000000000000000000"`
3. Create a project for loopCore:
   ```
   curl -X POST http://localhost:8000/v1/projects \
     -H "Authorization: Bearer hb_live_dev000000000000000000000000000000" \
     -H "Content-Type: application/json" \
     -d '{"slug": "loopcolony", "name": "loopColony"}'
   ```
4. HiveLoop SDK installed in loopCore's environment: `pip install -e C:\code\hiveboard\src\sdk` ✅

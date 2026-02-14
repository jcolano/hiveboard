# loopCore — Implementation Request: Layer 2 LLM Call Tracking

**Priority:** High — unlocks Cost Explorer and LLM visibility on HiveBoard
**Date:** 2026-02-12
**Context:** Layer 1 (`agent.task()` + `agent.track()`) is working. Next step is adding `task.llm_call()` after every LLM API call so HiveBoard can show cost, token usage, and LLM-specific timeline nodes.

---

## What This Unlocks on HiveBoard

Once `task.llm_call()` is in place:
- **Cost Explorer** — fully functional with cost by model and cost by agent breakdowns
- **Task Table** — LLM column shows call count (e.g. "◆ 3"), COST column shows dollar amount
- **Timeline** — purple LLM nodes appear between action nodes, with model badge above each
- **Stats Ribbon** — Cost (1h) populates
- **Mini-Charts** — LLM Cost/Task chart shows trends
- **Activity Stream** — "llm" filter shows every LLM call as it happens

---

## The Pattern

Every LLM call site follows the same pattern:

```python
import time
from loop_core.observability import get_current_task

# Before the call:
_llm_start = time.perf_counter()

# The existing LLM call:
response = client.complete(...)

# After the call — add this:
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000  # ms
_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "descriptive_name",
            model=response.model,                          # or the model string passed to the client
            tokens_in=response.usage.input_tokens,         # adjust to actual response shape
            tokens_out=response.usage.output_tokens,       # adjust to actual response shape
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass  # never break agent for observability
```

**Important rules:**
- Always use `get_current_task()` with `if _task:` guard — some calls may happen outside a task context
- Always wrap in `try/except` — HiveLoop should never crash the agent
- `tokens_in`, `tokens_out`, `duration_ms` are all optional — send what you have
- `cost` is optional — can be added later with a cost calculation helper
- The `"descriptive_name"` should identify what this LLM call does, not which model it uses

---

## LLM Call Sites

### Site 1: Phase 1 — Structured Reasoning

**File:** `src/loop_core/loop.py` around line 1274
**Call:** `phase1_client.complete_json()`
**Purpose:** The agent's first-pass reasoning — analyzes input, decides what to do
**Name to use:** `"phase1_reasoning"`

```python
_llm_start = time.perf_counter()
phase1_response = phase1_client.complete_json(...)
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000

_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "phase1_reasoning",
            model=phase1_client.model,          # or however the model name is accessed
            tokens_in=phase1_response.usage.input_tokens,
            tokens_out=phase1_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

**Note:** Check the response object shape — `complete_json()` may wrap the Anthropic response. Extract `usage.input_tokens` and `usage.output_tokens` from whatever the actual response structure is. If the wrapper doesn't expose usage, check if it's accessible via `phase1_response.raw_response.usage` or similar.

---

### Site 2: Phase 2 — Tool Use Execution

**File:** `src/loop_core/loop.py` around line 1413
**Call:** `phase2_client.complete_with_tools()`
**Purpose:** The agent's tool-calling pass — executes actions using tools
**Name to use:** `"phase2_tool_use"`

```python
_llm_start = time.perf_counter()
phase2_response = phase2_client.complete_with_tools(...)
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000

_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "phase2_tool_use",
            model=phase2_client.model,
            tokens_in=phase2_response.usage.input_tokens,
            tokens_out=phase2_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

**Note:** Phase 2 is typically the most expensive call (tool use prompts are large). This is the highest-value site for cost tracking.

---

### Site 3: Reflection

**File:** `src/loop_core/reflection.py` around line 364
**Call:** LLM call for self-reflection
**Purpose:** Agent evaluates its own performance/output
**Name to use:** `"reflection"`

```python
_llm_start = time.perf_counter()
reflection_response = <existing LLM call>
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000

_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "reflection",
            model=<model name>,
            tokens_in=reflection_response.usage.input_tokens,
            tokens_out=reflection_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

**Note:** The earlier Layer 1 implementation already added `task.event("reflection_started/completed")` in this file. The `task.llm_call()` should go between those two events — after the actual LLM API call returns, before the reflection result is parsed.

---

### Site 4: Planning

**File:** `src/loop_core/planning.py`
**Call:** LLM call to create an execution plan
**Purpose:** Agent generates a multi-step plan
**Name to use:** `"create_plan"`

```python
_llm_start = time.perf_counter()
plan_response = <existing LLM call>
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000

_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "create_plan",
            model=<model name>,
            tokens_in=plan_response.usage.input_tokens,
            tokens_out=plan_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

**Note:** The earlier Layer 1 implementation added `_task.plan()` after plan parsing. The `task.llm_call()` should go right after the LLM returns, before the plan is parsed into steps.

---

### Site 5: Heartbeat Summary

**File:** `src/loop_core/agent.py` around line 198
**Call:** LLM call to summarize agent state for heartbeat
**Purpose:** Generates a human-readable summary of what the agent is doing
**Name to use:** `"heartbeat_summary"`

```python
_llm_start = time.perf_counter()
summary_response = <existing LLM call>
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000

_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "heartbeat_summary",
            model=<model name>,
            tokens_in=summary_response.usage.input_tokens,
            tokens_out=summary_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

**Note:** This call may happen outside of a task context (heartbeat runs independently). In that case, `get_current_task()` returns `None` and the block is silently skipped. This is correct — heartbeat summaries without a task context are agent-level overhead that doesn't belong to a specific task.

**Alternative:** If you want to track heartbeat summary cost even outside task context, use the agent-level method instead:

```python
if not _task and hiveloop_agent:
    try:
        hiveloop_agent.llm_call(
            "heartbeat_summary",
            model=<model name>,
            tokens_in=summary_response.usage.input_tokens,
            tokens_out=summary_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

---

### Site 6: Context Compaction

**File:** `src/loop_core/context.py`
**Call:** LLM call to compress the context window
**Purpose:** Summarizes conversation history to fit within token limits
**Name to use:** `"context_compaction"`

```python
_llm_start = time.perf_counter()
compaction_response = <existing LLM call>
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000

_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "context_compaction",
            model=<model name>,
            tokens_in=compaction_response.usage.input_tokens,
            tokens_out=compaction_response.usage.output_tokens,
            duration_ms=round(_llm_elapsed),
        )
    except Exception:
        pass
```

**Note:** Like heartbeat summary, context compaction may happen outside a task context. Same pattern — if `_task` is `None`, silently skip. Use `agent.llm_call()` if you want to track it regardless.

---

## Extracting Token Usage

The implementation depends on how loopCore's LLM clients wrap the Anthropic response. Here are the common shapes:

**Direct Anthropic SDK response:**
```python
response.usage.input_tokens   # int
response.usage.output_tokens  # int
response.model                # str, e.g. "claude-sonnet-4-5-20250929"
```

**If loopCore wraps the response:**
```python
# Check what the wrapper exposes. Common patterns:
response.raw.usage.input_tokens
response.meta.tokens_in
response.token_count.input
```

**If usage isn't directly available:**
```python
# Fall back to None — HiveLoop accepts partial data:
_task.llm_call(
    "phase1_reasoning",
    model="claude-sonnet-4-5-20250929",  # at minimum, send the model name
)
# tokens_in, tokens_out, duration_ms all default to None
```

**Action item for the implementer:** Check one LLM client call, print the response object's attributes to find where usage data lives, then apply the same pattern to all 6 sites.

---

## Cost Calculation (Optional — Can Add Later)

If you want to populate the COST column immediately, add a cost helper:

```python
# In observability.py or a new cost_utils.py:

COST_PER_MILLION = {
    # Anthropic (as of Feb 2026 — verify current pricing)
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80, "output": 4.00},
    "claude-3-haiku-20240307":    {"input": 0.25, "output": 1.25},
    # Add other models as needed
}

def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Estimate USD cost for an LLM call. Returns None if model not in table."""
    rates = COST_PER_MILLION.get(model)
    if not rates:
        return None
    return (tokens_in * rates["input"] / 1_000_000) + (tokens_out * rates["output"] / 1_000_000)
```

Usage:
```python
_task.llm_call(
    "phase1_reasoning",
    model=model_name,
    tokens_in=tokens_in,
    tokens_out=tokens_out,
    cost=estimate_cost(model_name, tokens_in, tokens_out),
    duration_ms=round(_llm_elapsed),
)
```

**This is optional.** HiveBoard can display token counts without cost. Cost is a nice-to-have that makes the Cost Explorer more useful immediately. You can add it in a second pass.

---

## Implementation Checklist

- [ ] Identify response object shape — where does usage data live?
- [ ] Add `import time` to files that don't have it
- [ ] Add `from loop_core.observability import get_current_task` to files that don't have it
- [ ] **Site 1:** `loop.py` — Phase 1 reasoning
- [ ] **Site 2:** `loop.py` — Phase 2 tool use
- [ ] **Site 3:** `reflection.py` — Reflection
- [ ] **Site 4:** `planning.py` — Plan creation
- [ ] **Site 5:** `agent.py` — Heartbeat summary (may skip if outside task context)
- [ ] **Site 6:** `context.py` — Context compaction (may skip if outside task context)
- [ ] (Optional) Add cost estimation helper
- [ ] Test: Trigger a task that hits Phase 1 + Phase 2, check HiveBoard:
  - [ ] LLM column in Task Table shows "◆ 2" (or however many calls)
  - [ ] Cost Explorer shows data when switching to that view
  - [ ] Timeline shows purple LLM nodes with model badges
  - [ ] Activity Stream "llm" filter shows LLM call events

## Priority Order

If you want to ship incrementally:
1. **Sites 1 + 2** (Phase 1 + Phase 2 in `loop.py`) — highest value, these are the main LLM calls
2. **Site 3** (Reflection) — second highest, visible on every reflective turn
3. **Site 4** (Planning) — visible when plans are created
4. **Sites 5 + 6** (Heartbeat summary + Context compaction) — lower priority, often outside task context

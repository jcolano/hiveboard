# HiveBoard Observability Gap Analysis

> **Key insight** (from HiveBoard engineering): The current integration treats the LLM call as the
> unit of observation. But **the agent is the unit of observation** -- and agents do far more than
> call LLMs. Tools, state mutations, retries, config, context pressure, cycles, scheduling,
> learning, and cross-agent causality are all invisible today.

## What We Send Today (10 integration points)

| Feature | Status | Where |
|---------|--------|-------|
| Agent registration | Done | `agent_manager.py` |
| Heartbeat + queue_provider | Done | `agent_manager.py` |
| Task lifecycle (start/complete/fail) | Done | `agent_manager.py` |
| LLM calls (5 types) | Done | `loop.py`, `planning.py`, `reflection.py`, `compaction.py` |
| Tool execution tracking | Done | `loop.py` |
| Plan creation + step tracking | Done | `planning.py` |
| Reflection events | Done | `reflection.py` |
| Escalation | Done | `loop.py` |
| TODO lifecycle | Done | `todo_tools.py` |
| Issue reporting/resolution | Done | `issue_tools.py`, `api/app.py` |

That's a solid Layer 0-2 integration. But the manual supports a lot more. Here's everything we're leaving on the table, organized by perspective:

---

## 1. RUNTIME & LIFECYCLE (the "heartbeat payload" gap)

We register `queue_provider` but **never set `heartbeat_payload`**. This is a callable that runs every 30 seconds and can return arbitrary status data. Right now HiveBoard only knows "agent is alive" -- it could know *how* it's doing.

**What we could send every heartbeat:**
- `uptime_seconds` -- how long since the agent was started
- `total_runs` -- cumulative task count this session
- `total_tokens` -- cumulative token spend this session
- `total_cost` -- cumulative dollar spend
- `memory_items` -- number of items in persistent memory
- `active_skills` -- list of loaded skill IDs
- `last_sync_at` -- last loopColony sync timestamp (for workspace agents)
- `event_queue_depth` -- redundant with queue_provider but shows at-a-glance health
- `consecutive_errors` -- how many runs failed in a row (drift detection)

**Impact:** The agent card in The Hive would show rich enrichment data instead of just "alive" + queue depth.

---

## 2. SCHEDULED WORK (completely unused)

We have a full scheduler system in `scheduler.py` with recurring tasks (cron-like), but we **never call `agent.scheduled()`**. HiveBoard has a dedicated Pipeline tab -> Scheduled section for exactly this.

**What we could send:**
```python
agent.scheduled(items=[
    {"id": "sch_abc", "name": "Heartbeat routine", "next_run": "2026-02-13T03:00:00Z",
     "interval": "1m", "enabled": True, "last_status": "success"},
    {"id": "sch_def", "name": "Daily CRM sync", "next_run": "2026-02-14T00:00:00Z",
     "interval": "daily", "enabled": True, "last_status": None},
])
```

**Impact:** The Pipeline tab would show upcoming scheduled work, their intervals, and whether the last run succeeded -- right now that tab section is empty.

---

## 3. RETRY TRACKING (completely unused)

Our agentic loop has retry logic -- Phase 2 LLM calls retry on parse failures, tool executions can fail and the LLM decides to retry. But we **never call `task.retry()`**.

**Where retries happen that we should track:**
- Phase 2 JSON parse failures -> loop retries with error context
- Tool execution failures -> LLM decides to retry with different params
- HTTP tool timeouts/5xx -> implicit retries

**What we could send:**
```python
task.retry(reason="Phase 2 JSON parse failed", attempt=2, backoff_seconds=0)
```

**Impact:** Retry nodes would appear in the timeline visualization (branching paths). Right now, retries are invisible -- a task that took 8 turns might have had 3 silent retries that explain the cost.

---

## 4. LLM CALL METADATA (richer than what we send)

We send `model`, `tokens_in/out`, `cost`, `duration_ms`, and optionally `prompt_preview`/`response_preview`. But `llm_call()` also accepts a `metadata` dict that shows up in the detail panel.

**What we could add to each LLM call:**
- `turn_number` -- which turn in the loop
- `system_prompt_tokens` -- how big the system prompt is (it's constant, but varies by agent config)
- `tool_schemas_count` -- how many tool schemas were included in the call
- `temperature` / `max_tokens` -- the actual generation parameters
- `cache_read_tokens` / `cache_write_tokens` -- Anthropic returns these; huge for cost analysis
- `stop_reason` -- why the LLM stopped (`end_turn`, `max_tokens`, `tool_use`)
- `attempt` -- if this was a retry of a failed call

For **Phase 1 specifically:**
- `state_completed_steps` -- how many steps done so far
- `state_variables_count` -- how many variables accumulated
- `plan_step_index` -- which plan step we're on (if planning is active)

For **Phase 2 specifically:**
- `chosen_tool` -- what tool Phase 1 decided on (so you see it even if Phase 2 fails)
- `schema_tokens` -- how many tokens the single tool schema cost

**Impact:** The timeline detail panel for each LLM node would show rich debugging data instead of just tokens+cost. Cache hit ratios alone would reveal massive optimization opportunities.

---

## 5. TOOL EXECUTION (richer payloads)

We track tools via `track_context()` with `args` (100 chars), `result_preview` (200 chars), `success`, and `error`. The truncation limits are quite aggressive.

**What we could add:**
- `duration_ms` -- tool execution time (we have it, just don't send it)
- `tool_category` -- "crm", "http", "workspace", "file", "compute" etc.
- `result_size_bytes` -- how large the full result was (shows when truncation hides important data)
- `http_status` -- for `http_request` and `webpage_fetch` tools
- `http_url` -- the URL that was called (for API debugging)
- `workspace_operation` -- for `workspace_read`/`workspace_write`, what CRM entity was touched
- Increase `result_preview` from 200 to 500 chars (the manual shows 500 as the standard)

**Impact:** Tool nodes in the timeline would become actually debuggable. Right now if `workspace_read` fails, you see `"error": "..."` truncated to 100 chars -- often cutting off the actual error message.

---

## 6. LEARNING EVENTS (completely untracked)

`learning.py` captures patterns the agent learns from experience. This is a core differentiator of loopCore but is completely invisible in HiveBoard.

**What we could send:**
```python
task.event("learning_captured", pattern_id="lrn_abc",
           trigger="repeated_success", category="tool_usage",
           summary="Use workspace_read before workspace_write for CRM updates")
```

**Impact:** You'd see purple "learning" nodes in the timeline, showing when and why agents evolved their behavior. Correlating learning events with improved success rates would be powerful.

---

## 7. CONTEXT COMPACTION EVENTS (only LLM call tracked)

We track the LLM call for compaction, but not the compaction event itself.

**What we could send:**
```python
task.event("context_compacted",
           tokens_before=45000, tokens_after=12000,
           compression_ratio=0.73, messages_removed=15)
```

**Impact:** You'd know when context was getting bloated and how aggressive the compaction was. A pattern of frequent compaction = system prompt is too large or the task is too complex.

---

## 8. ERROR & FAILURE CONTEXT (the biggest debugging gap)

When things fail, we currently get minimal data in HiveBoard. Here's what we're missing:

### a) AtomicState error_context

When a tool fails or reflection decides to adjust, `state.error_context` is set with a description. We never send this to HiveBoard.

```python
task.event("error_context_set",
           context=state.error_context,
           turn=turn_number, step=state.current_step)
```

### b) Loop termination reason

When `execute()` ends, we know *why* (done=True, max_turns hit, cancelled, error, escalation) but we don't report this distinctly.

```python
task.event("loop_terminated",
           reason="max_turns", turns_used=20, turns_limit=20,
           last_tool=last_tool_name, tokens_used=total_tokens)
```

### c) Tool error classification

We send `error` as a string, but we could categorize:

```python
agent.report_issue(
    summary=f"Tool '{tool_name}' failed: {error[:200]}",
    severity="medium",
    category="connectivity" if "timeout" in error else "other",
    context={"tool": tool_name, "params": params, "turn": turn_number})
```

### d) Auto-escalation from reflection

When reflection returns `decision="escalate"`, we call `task.escalate()` but don't include the full reasoning or the state that led to it.

**Impact:** This is the biggest ROI gap. Right now when something fails, you see a red node in the timeline with a truncated error string. With these additions, you'd have full root-cause context without needing to dig into log files.

---

## 9. RUNTIME EVENT QUEUE (invisible infrastructure)

The runtime (`runtime.py`) manages event dispatching, priority queues, pre-check optimization, and agent lifecycle. None of this is visible in HiveBoard.

**What we could send:**
- `agent.event("runtime_event_queued", source="webhook", priority="HIGH", queue_depth=3)`
- `agent.event("runtime_event_skipped", source="heartbeat", reason="pre_check_empty")`
- `agent.event("runtime_agent_started", restored_events=2, heartbeat_timers=1)`
- `agent.event("runtime_agent_stopped", queued_events_dropped=3)`

**Impact:** You'd see why agents are sometimes idle (pre-check skipping), why there are delays (queue depth), and what happens during restart (event restoration).

---

## 10. SESSION & MEMORY OPERATIONS (invisible)

Agents have persistent sessions and memory, but these operations are invisible.

**What we could send:**
- `task.event("session_loaded", session_id="...", messages=15, tokens_approx=8000)`
- `task.event("session_trimmed", messages_before=50, messages_after=20)`
- `task.event("memory_read", topic="crm_contacts", items=12)`
- `task.event("memory_write", topic="crm_contacts", action="added", key="...")`

**Impact:** Memory bloat and session size are silent performance killers. This makes them visible.

---

## 11. LOG FORWARDING (bridge our new logging to HiveBoard)

We just built centralized logging. We could add a custom logging handler that forwards WARNING and ERROR level logs as HiveBoard events or issues.

**What this would look like:**
```python
class HiveBoardLogHandler(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            agent.report_issue(
                summary=record.getMessage()[:200],
                severity="high" if record.levelno >= logging.ERROR else "medium",
                category="other",
                context={"logger": record.name, "lineno": record.lineno})
```

**Impact:** Any WARNING/ERROR in any of our 16 modules would automatically surface as an issue in HiveBoard's Pipeline tab -- no manual instrumentation needed per call site.

---

## 12. COST OPTIMIZATION DATA (cache hits are invisible)

The Anthropic API returns `cache_creation_input_tokens` and `cache_read_input_tokens` in the response. We currently ignore these. For agents with large system prompts + skills, cache hits can reduce effective cost by 80%+.

**What we could track:**
```python
task.llm_call("phase1_reasoning", model=...,
    metadata={
        "cache_read_tokens": usage.cache_read_input_tokens,
        "cache_write_tokens": usage.cache_creation_input_tokens,
        "effective_cost": adjusted_cost,  # accounting for 90% cache discount
    })
```

**Impact:** The Cost Explorer would show *real* costs instead of list-price estimates. You'd also see which agents benefit most from caching (large stable system prompts) vs. which don't (highly dynamic prompts).

---

## 13. MULTI-AGENT COORDINATION (no cross-agent visibility)

When one agent's output triggers another agent (e.g., via webhooks or queue events), there's no correlation in HiveBoard. Each agent looks independent.

**What we could send:**
```python
task.event("triggered_by", source_agent="ag_sales", source_task="task_123",
           trigger_type="webhook", payload_summary="New deal closed")
```

**Impact:** You'd see causality chains across agents -- "SalesBot closed a deal -> triggered SupportBot onboarding -> triggered OpsBot provisioning". Right now these look like unrelated events.

---

## 14. TURN-LEVEL METRICS (no per-turn summary)

We track LLM calls and tool calls individually, but we never emit a **per-turn summary event** that ties them together. A "turn" in our loop is: Phase 1 reasoning -> Phase 2 parameter gen -> tool execution -> result. That's the natural unit of work, but HiveBoard only sees the pieces.

**What we could send after each turn:**
```python
task.event("turn_completed",
           turn=3,
           phase1_tokens=4200,
           phase2_tokens=1800,
           tool=tool_name,
           tool_success=True,
           tool_duration_ms=340,
           turn_duration_ms=8500,
           cumulative_tokens=18000,
           cumulative_cost=0.072,
           context_utilization=0.45)  # 45% of context window used
```

**Impact:** The timeline would show turn-level rollups -- "Turn 3 used 6000 tokens, called workspace_read in 340ms, total turn took 8.5s". You could spot turns that are disproportionately expensive or slow. Right now you have to mentally reconstruct this from individual Phase 1 + Phase 2 + tool nodes.

---

## 15. LOOP/CYCLE DETECTION (invisible stuck agents)

Our agentic loop can get stuck in cycles -- the agent calls the same tool with similar parameters repeatedly, or oscillates between two tools without making progress. We have `max_turns` as a hard stop, but we don't detect or report the cycle *as it happens*.

**What we could send:**
```python
task.event("cycle_detected",
           pattern="workspace_read -> workspace_write -> workspace_read (3x)",
           turns_in_cycle=6,
           unique_tools_in_cycle=2,
           total_turns=12)
```

**Detection heuristic:** Track the last N tool names. If a sliding window of 4-6 calls shows a repeating pattern (e.g., A-B-A-B or A-A-A), fire the event.

**Impact:** Instead of finding out *after* a task burns 20 turns and $0.50 that it was looping, you'd see an amber "cycle detected" event in the timeline mid-run. Especially valuable for debugging skill instructions that produce ambiguous tool choices.

---

## 16. CONTEXT WINDOW UTILIZATION (continuous, not just compaction)

Section 7 covers compaction events, but that only fires when we *already hit* the limit. We should track context fullness as a **continuous metric** so you see pressure building.

**What we could send (as LLM metadata on every call):**
```python
task.llm_call("phase1_reasoning", model=...,
    metadata={
        "context_tokens": 38000,
        "context_limit": 100000,
        "context_utilization": 0.38,
        "system_prompt_tokens": 12000,
        "conversation_tokens": 26000,
    })
```

**And a threshold event when context gets hot:**
```python
task.event("context_pressure",
           utilization=0.85,
           tokens_used=85000,
           tokens_limit=100000,
           turns_remaining_estimate=3)
```

**Impact:** You'd see a gradual ramp-up in context utilization across turns, not just a sudden "compaction fired" event. Agents that routinely hit 80%+ context are candidates for prompt optimization or model upgrade (larger context window).

---

## 17. AGENT CONFIG SNAPSHOTS (correlate behavior with settings)

When an agent starts, we register it with HiveBoard (agent_id, type, version, framework) but we don't send the **full configuration** that governs its behavior. If an agent suddenly starts failing, you can't tell from HiveBoard whether someone changed its model, temperature, max_turns, or skills.

**What we could send on agent start:**
```python
agent.event("config_snapshot",
            model=config.llm.model,
            phase2_model=config.phase2_model,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            max_turns=config.max_turns,
            timeout_seconds=config.timeout_seconds,
            skills=list(config.skills.keys()),
            tools_enabled=[t.name for t in config.tools],
            planning_enabled=config.planning.enabled,
            reflection_enabled=config.reflection.enabled,
            learning_enabled=config.learning.enabled)
```

**Impact:** When investigating a failure, you'd see exactly what config the agent was running with. "Oh, someone changed the model from Sonnet to Haiku and that's why planning started failing." Also enables comparing configs across agents side-by-side.

---

## 18. PARSE & VALIDATION ERRORS (silent failures)

Phase 2 of our agentic loop asks the LLM to generate JSON tool parameters. When this fails (malformed JSON, missing required fields, wrong types), we retry silently. These failures are invisible in HiveBoard.

**Where silent parse/validation errors happen:**
- Phase 2 JSON parse failure (LLM returns invalid JSON)
- Tool parameter schema validation failure (valid JSON but wrong shape)
- Phase 1 response parsing (expected done/tool/step fields)
- Reflection response parsing (expected decision/reasoning)
- Planning response parsing (expected steps array)

**What we could send:**
```python
task.event("parse_error",
           phase="phase2",
           error_type="json_decode",
           raw_preview=raw_response[:300],
           attempt=2,
           model=model_name)
```

```python
task.event("validation_error",
           phase="phase2",
           tool=tool_name,
           field="parameters.query",
           error="missing required field",
           raw_preview=raw_json[:300])
```

**Impact:** You'd see which models produce the most parse failures, which tool schemas are hard for LLMs to fill correctly, and how many tokens are wasted on retries. A pattern of "Phase 2 parse errors spike when using Haiku for workspace_write" = actionable optimization insight.

---

## 19. PROMPT COMPOSITION BREAKDOWN (where tokens go)

We send `tokens_in` for each LLM call, but it's a single number. The prompt is actually composed of multiple sections that compete for the token budget: system prompt, identity block, skill instructions, tool schemas, plan context, state dict, and the actual user message.

**What we could send (as LLM metadata):**
```python
task.llm_call("phase1_reasoning", model=...,
    metadata={
        "prompt_breakdown": {
            "system_prompt": 3200,
            "identity_block": 400,
            "skill_instructions": 5800,
            "tool_schemas": 2100,
            "plan_context": 800,
            "state_dict": 600,
            "user_message": 1200,
        },
        "total_prompt_tokens": 14100,
    })
```

**Impact:** You'd immediately see that "60% of every Phase 1 call is skill instructions" or "tool schemas are 25% of the budget for agents with 15 tools". This directly drives optimization: can we trim skill instructions? Should we use `get_single_schema()` more aggressively? Is the identity block bloated?

---

## 20. STATE MUTATIONS (AtomicState is a black box)

Our agentic loop uses `AtomicState` -- a compact dict with `completed_steps`, `variables`, `current_step`, and `error_context`. This state evolves every turn but is completely invisible in HiveBoard.

**What we could send:**
```python
# After each turn
task.event("state_mutation",
           turn=3,
           completed_steps=["searched CRM", "found contact", "updated deal"],
           variables_count=8,
           variables_added=["contact_id", "deal_stage"],
           current_step="Create follow-up task",
           error_context=None)
```

```python
# When error_context changes
task.event("state_error_set",
           turn=5,
           error_context="workspace_write returned 403: insufficient permissions",
           previous_step="Update deal stage",
           current_step="Retry with different approach")
```

**Impact:** You'd see the agent's "thought process" evolving -- which steps it completed, what variables it accumulated, where it hit errors. This is the closest thing to reading the agent's mind. Combined with the timeline, you'd get a complete picture: "The agent completed 3 steps, accumulated 8 variables, hit a 403 error on step 4, adjusted course on step 5."

---

## Summary: Priority Ranking

| # | Gap | Effort | Impact | Priority |
|---|-----|--------|--------|----------|
| 1 | **Error/failure context** (Section 8) | Medium | Huge | **P0** |
| 2 | **LLM metadata** (cache hits, turn#, stop_reason) (Section 4) | Low | High | **P0** |
| 3 | **Parse/validation errors** (Section 18) | Low | High | **P0** |
| 4 | **Tool duration + richer payloads** (Section 5) | Low | High | **P1** |
| 5 | **Turn-level metrics** (Section 14) | Low | High | **P1** |
| 6 | **Prompt composition breakdown** (Section 19) | Low | High | **P1** |
| 7 | **Heartbeat payload** (Section 1) | Low | Medium | **P1** |
| 8 | **Scheduled work** (Section 2) | Low | Medium | **P1** |
| 9 | **Retry tracking** (Section 3) | Medium | High | **P1** |
| 10 | **Log forwarding** (Section 11) | Low | High | **P1** |
| 11 | **Loop/cycle detection** (Section 15) | Medium | High | **P1** |
| 12 | **Loop termination events** (Section 8b) | Low | Medium | **P2** |
| 13 | **Learning events** (Section 6) | Low | Medium | **P2** |
| 14 | **Context compaction events** (Section 7) | Low | Medium | **P2** |
| 15 | **Context window utilization** (Section 16) | Low | Medium | **P2** |
| 16 | **Cost/cache optimization** (Section 12) | Medium | High | **P2** |
| 17 | **Agent config snapshots** (Section 17) | Low | Medium | **P2** |
| 18 | **State mutations** (Section 20) | Medium | Medium | **P2** |
| 19 | **Runtime queue events** (Section 9) | Medium | Low | **P3** |
| 20 | **Session/memory ops** (Section 10) | Medium | Low | **P3** |
| 21 | **Multi-agent correlation** (Section 13) | High | Medium | **P3** |

---

## Estimated Coverage

| Tier | Sections | Effort | Cumulative Coverage |
|------|----------|--------|---------------------|
| Already done | (10 integration points) | -- | ~20% |
| **P0** (must-have) | 8, 4, 18 | ~2 hours | ~35% |
| **P1** (high-value) | 5, 14, 19, 1, 2, 3, 11, 15 | ~4 hours | ~60% |
| **P2** (deep understanding) | 8b, 6, 7, 16, 12, 17, 20 | ~3 hours | ~85% |
| **P3** (specialized) | 9, 10, 13 | ~2 hours | ~95% |

---

## Key Framing

The short version: we're sending the **skeleton** (tasks, LLM calls, tools, plans) but missing the **context** (why things failed, what the actual costs are, what the runtime is doing between tasks, and how agents relate to each other).

The highest-ROI work falls into three categories:

1. **Enrich what we already send** -- metadata on LLM calls, richer error context, tool durations, prompt breakdowns (P0 + P1, ~6 hours)
2. **Track what's invisible** -- turn summaries, cycle detection, parse errors, state mutations, config snapshots (P1 + P2, ~5 hours)
3. **Connect the dots** -- cross-agent causality, log forwarding, scheduled work visibility (P1 + P3, ~3 hours)

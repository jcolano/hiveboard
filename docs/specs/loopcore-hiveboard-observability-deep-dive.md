# Deep Dive: Untapped Observability Between loopCore and HiveBoard

**Date:** 2026-02-13
**Purpose:** Identify everything loopCore could send to HiveBoard but currently doesn't, organized by perspective

---

## Executive Summary

The current instrumentation plan covers **Layer 0–2**: agent registration, heartbeat, task lifecycle, action tracking, LLM call tracking (6 sites), and narrative events (plans, escalations, approvals, retries, issues, queue snapshots, TODOs, scheduled work). This is solid foundational observability.

But it represents roughly **15–20% of the observable data** loopCore actually generates. The remaining 80% falls into 13 categories across every perspective the user asked about: technical, functional, workflow, errors, logs, and beyond.

This document is organized into **tiers by impact**, with each recommendation including:
- What data exists in loopCore today
- What HiveBoard event/mechanism would carry it
- What question it answers on the dashboard
- Estimated effort

---

## Tier 1 — High Impact, Low Effort (Do These First)

These fill the biggest gaps in understanding "what is my agent actually doing?"

---

### 1. Tool Execution Tracking (Technical + Functional)

**The gap:** The instrumentation plan tracks LLM calls but not what happens *between* them — the tool calls the LLM requested. This is like tracking phone calls but not what was said.

**What loopCore has:**
- `loop.py:1401–1466` — Phase 2 tool dispatch loop
- `ToolRegistry.execute(tool_name, parameters)` — returns `ToolResult` with success/error/output
- `ToolCallRecord` dataclass — captures tool name, parameters, result per turn
- Tool catalog with names, schemas, and hints (`_TOOL_SHORT_HINTS` dict)
- Tool execution duration (measurable with `time.perf_counter()`)

**What to send:**

```python
# In the Phase 2 tool execution loop:
with agent.track_context(tool_name) as ctx:
    _tool_start = time.perf_counter()
    result = self.tool_registry.execute(tool_name, params)
    _tool_ms = (time.perf_counter() - _tool_start) * 1000
    ctx.set_payload({
        "args": str(params)[:200],
        "result": str(result.output)[:200] if result.success else None,
        "error": str(result.error)[:200] if not result.success else None,
        "success": result.success,
        "duration_ms": round(_tool_ms),
    })
```

**Dashboard impact:**
- Blue action nodes for each tool call on the Timeline
- Tool sequence visible: `LLM reasoning → search_crm → LLM reasoning → send_email`
- Failed tools show in red with error details
- Input/output previews in detail panel on click
- Nesting: tool calls show as children of the current action

**Questions answered:**
- Which tools are called most frequently?
- Which tool is the bottleneck (longest duration)?
- Which tools fail and why?
- What parameters is the LLM generating for each tool?
- Are tool calls happening in the right order?

**Effort:** ~30 minutes. Wrap the existing tool execution loop in `track_context()`.

---

### 2. Turn-Level Metrics (Technical + Performance)

**The gap:** HiveBoard sees task-level aggregates (total duration, total tokens) but not what happens inside each turn of the agentic loop. A 20-turn task looks the same as a 2-turn task.

**What loopCore has:**
- `loop.py` main loop — each iteration is a "turn"
- Per-turn data: `turn.duration_ms`, `turn.tokens_used`, `turn.tool_calls`
- Turn count, max_turns limit
- Phase 1 and Phase 2 within each turn
- Whether the turn was the final one (and why: done, escalate, max_turns, timeout)

**What to send:**

```python
# At the end of each turn in the agentic loop:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "turn_completed",
        "summary": f"Turn {turn_number}: {tool_name or 'no tool'} ({turn.duration_ms}ms)",
        "data": {
            "turn_number": turn_number,
            "total_turns": max_turns,
            "duration_ms": turn.duration_ms,
            "tokens_used": turn.tokens_used,
            "tool_called": tool_name,
            "tool_success": tool_result.success if tool_result else None,
            "phase1_tokens": phase1_tokens,
            "phase2_tokens": phase2_tokens,
            "exit_reason": None,  # or "done", "escalate", "max_turns", "timeout"
        },
        "tags": ["turn"],
    })
```

**Dashboard impact:**
- Activity Stream shows turn-by-turn progression
- Timeline nodes for each turn (when combined with tool tracking)
- Token consumption per turn — identify which turns are expensive
- Turn count visible on task detail

**Questions answered:**
- How many turns does each task type typically need?
- Which turn consumes the most tokens?
- Is the agent spinning (many turns, no progress)?
- How close are we to max_turns cutoff?
- What's the token burn rate per turn?

**Effort:** ~15 minutes. Add a custom event at the end of the loop body.

---

### 3. Reflection Decision Tracking (Functional + Decision)

**The gap:** The plan tracks `task.llm_call("reflection")` but not *what the reflection decided*. We see the LLM was called but not what it concluded — continue, adjust, pivot, escalate, or exit.

**What loopCore has:**
- `reflection.py:318` — `ReflectionManager.reflect()` returns a decision
- Decision types: `exit`, `continue`, `adjust`, `pivot`, `escalate`
- Optional reasoning text for each decision
- Confidence signal (implicit in the LLM response)

**What to send:**

HiveBoard already has a `reflection` payload kind in its schema:

```python
# After reflection decision is parsed:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "reflection",
        "summary": f"Reflection: {decision} — {reasoning[:100]}",
        "data": {
            "decision": decision,         # "continue", "adjust", "pivot", "escalate", "exit"
            "reasoning": reasoning[:500],
            "confidence": confidence,      # 0.0–1.0 if available
            "next_action": next_action,    # what the agent will do next
            "trigger": "per_turn",         # or "error_recovery", "plan_step_complete"
        },
        "tags": ["reflection", decision],
    })
```

**Dashboard impact:**
- Timeline shows reflection decision badges (continue/pivot/escalate)
- Decision trail visible: "why did the agent escalate on turn 5?"
- Pivot/adjust events highlight strategy changes mid-task
- The `reflection` payload kind gets `render_hint: "reflection"` in the API

**Questions answered:**
- How often does the agent pivot vs. continue?
- What triggers escalations?
- Are adjustments effective (does the task succeed after an adjust)?
- How confident is the agent in its decisions?

**Effort:** ~15 minutes. Add a custom event after parsing the reflection response.

---

### 4. Loop Detection Events (Technical + Error)

**The gap:** loopCore has a `LoopDetector` class that detects when the agent is stuck in a repetitive cycle. This is critical diagnostic information that never reaches the dashboard.

**What loopCore has:**
- `loop.py:409–500` — `LoopDetector` with repeat threshold and sequence detection
- Detects: same tool called N times, same parameters repeated, cyclic action sequences
- `loop_detected` triggers loop-breaking logic (replan, escalate, force exit)

**What to send:**

```python
# When loop detection fires:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "issue",
        "summary": f"Loop detected: {pattern_type} — {tool_name} repeated {count}x",
        "data": {
            "severity": "high",
            "category": "other",
            "action": "reported",
            "issue_id": f"loop-{task_id}-{pattern_type}",
            "context": {
                "pattern_type": pattern_type,  # "repeat" or "sequence"
                "repeat_count": count,
                "tool_name": tool_name,
                "recovery_action": recovery_action,  # "replan", "escalate", "force_exit"
            },
        },
        "tags": ["issue", "loop_detected"],
    })
```

**Dashboard impact:**
- Red issue badge on agent card when loop detected
- Pipeline tab shows loop detection as an active issue
- Timeline shows the exact point where the loop was broken
- Correlates with reflection decisions and tool failures

**Questions answered:**
- How often do agents get stuck in loops?
- Which tools or patterns cause loops?
- Is the recovery action (replan/escalate) effective?
- Are certain task types more loop-prone?

**Effort:** ~15 minutes. Add event emission in the loop detection handler.

---

### 5. Context Window Utilization (Technical + Performance)

**The gap:** The agent's context window is its primary resource, like RAM for a process. Currently there's no visibility into how full it is, when compaction happens, or how much "headroom" remains.

**What loopCore has:**
- `context.py` — `ContextManager.build()` constructs the full prompt
- Token counting: `count_conversation_tokens()`
- Max context tokens config (default 100,000)
- Context compaction logic (summarize/trim when over budget)
- Cross-heartbeat summaries with 50-entry rolling buffer
- Intra-heartbeat turn history

**What to send:**

```python
# After context is built each turn:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "context_snapshot",
        "summary": f"Context: {tokens_used}/{max_tokens} ({pct}% full)",
        "data": {
            "tokens_used": tokens_used,
            "max_tokens": max_tokens,
            "utilization_pct": round(tokens_used / max_tokens * 100, 1),
            "message_count": len(messages),
            "system_prompt_tokens": system_tokens,
            "history_tokens": history_tokens,
            "turn_number": turn_number,
            "compaction_triggered": compaction_occurred,
        },
        "tags": ["context"],
    })

# When compaction happens:
if compaction_occurred:
    _task.event("custom", payload={
        "kind": "context_compaction",
        "summary": f"Context compacted: {before_tokens} → {after_tokens} tokens ({saved} saved)",
        "data": {
            "tokens_before": before_tokens,
            "tokens_after": after_tokens,
            "tokens_saved": saved,
            "messages_before": messages_before,
            "messages_after": messages_after,
            "compaction_method": "summarize",  # or "trim"
        },
        "tags": ["context", "compaction"],
    })
```

**Dashboard impact:**
- Context utilization visible per turn on timeline
- Compaction events show as distinct nodes
- Token budget consumption over time
- Early warning when context is near capacity

**Questions answered:**
- How full is the context window at each turn?
- When does compaction fire and how much does it save?
- Is the system prompt consuming too much of the budget?
- Do certain task types exhaust context faster?
- Are we losing important information during compaction?

**Effort:** ~30 minutes. Add events in context building and compaction paths.

---

## Tier 2 — High Impact, Medium Effort

These provide deeper understanding of *why* agents behave the way they do.

---

### 6. Agent Learning & Memory Events (Functional + Knowledge)

**The gap:** loopCore has a `LearningManager` that captures successful patterns for future use. This is the agent's "experience" — and it's completely invisible.

**What loopCore has:**
- `learning.py` — `LearningManager` with configurable learning sources
- `LearningConfig` — learn from errors, reflections, successes, domain facts, observations
- Persistent storage via `memory_path`
- Pattern capture after successful actions
- Pattern retrieval during prompt construction

**What to send:**

```python
# When a pattern is learned:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    _agent.event("custom", payload={
        "kind": "learning_captured",
        "summary": f"Learned: {pattern_type} — {pattern_text[:100]}",
        "data": {
            "pattern_type": pattern_type,  # "error_recovery", "tool_usage", "domain_fact"
            "pattern_text": pattern_text[:500],
            "source": source,             # "reflection", "success", "observation"
            "confidence": confidence,
            "memory_size": total_patterns_stored,
        },
        "tags": ["learning", pattern_type],
    })

# When patterns are retrieved for use:
if _task:
    _task.event("custom", payload={
        "kind": "learning_applied",
        "summary": f"Applied {len(patterns)} learned patterns to prompt",
        "data": {
            "patterns_count": len(patterns),
            "pattern_types": list(set(p.type for p in patterns)),
            "memory_tokens": tokens_used_for_memory,
        },
        "tags": ["learning", "applied"],
    })
```

**Questions answered:**
- Is the agent actually learning from experience?
- What patterns has it captured?
- How much memory is accumulated?
- Are learned patterns being applied to new tasks?
- Is learning improving performance over time?

**Effort:** ~1 hour. Instrument learning capture and retrieval paths.

---

### 7. Agent Configuration Snapshot (Technical + Operational)

**The gap:** Each agent has a rich configuration that affects its behavior — reflection enabled/disabled, planning enabled/disabled, model selection, max turns, timeout, learning settings. None of this is visible on the dashboard.

**What loopCore has:**
- `ReflectionConfig` — enabled, max_tokens, decision_types
- `PlanningConfig` — enabled, max_steps, inject_plan_context, max_turns_per_step
- `LearningConfig` — enabled, learn_from_*, domain_facts, memory_path
- Model names for Phase 1, Phase 2, reflection, planning, compaction
- `max_turns` (default 20), `timeout` (default 600s), `max_context_tokens` (default 100,000)
- `AtomicState` limits — max 20 completed_steps, 50 variables, 10 pending_actions

**What to send:**

```python
# At agent registration, emit a config snapshot:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    _agent.event("custom", payload={
        "kind": "agent_config",
        "summary": f"Config: phase1={phase1_model}, reflection={reflection_enabled}",
        "data": {
            "phase1_model": phase1_model,
            "phase2_model": phase2_model,
            "reflection_model": reflection_model,
            "reflection_enabled": reflection_config.enabled,
            "planning_enabled": planning_config.enabled,
            "learning_enabled": learning_config.enabled,
            "max_turns": max_turns,
            "timeout_seconds": timeout,
            "max_context_tokens": max_context_tokens,
            "stuck_threshold": stuck_threshold,
        },
        "tags": ["config"],
    })
```

**Questions answered:**
- What model is each agent using?
- Is reflection/planning/learning enabled for this agent?
- What are the operational limits (max turns, timeout)?
- Are config differences causing different behavior between agents?
- When was the config last changed?

**Effort:** ~30 minutes. One-time snapshot at registration + on config change.

---

### 8. Multi-Agent Communication (Workflow + Functional)

**The gap:** loopCore agents can create follow-up events for other agents (`source="agent:{agent_id}"`). This inter-agent communication is the backbone of multi-agent workflows — and it's invisible.

**What loopCore has:**
- `runtime.py:600–800` — Event dispatch, routing, agent delegation
- Agents create events targeting other agents
- Event source tracking: `source="agent:{agent_id}"`
- Priority queue with event routing by agent assignment
- Agent-to-agent delegation chains

**What to send:**

```python
# When an agent creates an event for another agent:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "agent_delegation",
        "summary": f"Delegated to {target_agent}: {event_summary[:80]}",
        "data": {
            "source_agent": source_agent_id,
            "target_agent": target_agent_id,
            "event_type": delegated_event_type,
            "event_summary": event_summary[:200],
            "priority": priority,
            "correlation_id": correlation_id,
        },
        "tags": ["delegation", "multi_agent"],
    })
```

Use `correlation_id` on both the source and target tasks to link them on the dashboard.

**Questions answered:**
- Which agents talk to each other and how often?
- What work gets delegated and to whom?
- How long do delegated tasks take to complete?
- Are there bottleneck agents that receive too many delegations?
- Can we trace a request across the full agent chain?

**Effort:** ~1 hour. Instrument event creation and dispatch in runtime.py.

---

### 9. Timeout and Limit Events (Error + Technical)

**The gap:** When a task times out or hits max_turns, the task just fails. There's no distinction between "failed because of a bug" and "failed because it ran out of time/turns."

**What loopCore has:**
- `loop.py:759–778` — `_check_timeout()` method
- Timeout config: default 600 seconds
- Max turns config: default 20
- Early exit reason tracking in the loop
- AtomicState limits (max 20 completed_steps, 50 variables)

**What to send:**

```python
# When timeout is triggered:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "issue",
        "summary": f"Task timeout: {elapsed_ms}ms exceeded {timeout_ms}ms limit",
        "data": {
            "severity": "high",
            "category": "timeout",
            "action": "reported",
            "issue_id": f"timeout-{task_type}",
            "context": {
                "elapsed_ms": elapsed_ms,
                "timeout_ms": timeout_ms,
                "turns_completed": turn_number,
                "last_tool": last_tool_name,
                "last_action": last_action,
            },
        },
        "tags": ["issue", "timeout"],
    })

# When max_turns is hit:
if _task:
    _task.event("custom", payload={
        "kind": "issue",
        "summary": f"Max turns reached: {turn_number}/{max_turns}",
        "data": {
            "severity": "medium",
            "category": "other",
            "action": "reported",
            "issue_id": f"max-turns-{task_type}",
            "context": {
                "turns_used": turn_number,
                "max_turns": max_turns,
                "tokens_consumed": total_tokens,
                "last_tool": last_tool_name,
            },
        },
        "tags": ["issue", "max_turns"],
    })
```

**Questions answered:**
- How many tasks timeout vs. complete naturally?
- Are timeouts concentrated on certain task types?
- How close are successful tasks to the timeout limit?
- Should max_turns be increased for certain task types?
- What was the agent doing when it timed out?

**Effort:** ~15 minutes. Two event emissions in the exit paths.

---

### 10. Output Parsing and Validation (Technical + Error)

**The gap:** Phase 1 returns structured JSON; Phase 2 returns tool calls. Both require parsing and validation. When parsing fails, the agent retries silently — and nobody knows.

**What loopCore has:**
- `loop.py:1353–1430` — Phase 1 JSON parsing, Phase 2 tool call parsing
- `complete_json()` — expects structured JSON response
- Tool parameter schema validation
- State update merging with constraint checks
- Silent retries on parse failure

**What to send:**

```python
# When Phase 1 JSON parsing fails:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "parse_error",
        "summary": f"Phase 1 parse failed: {error_type}",
        "data": {
            "phase": "phase1",
            "error_type": error_type,
            "error_message": str(error)[:200],
            "raw_response_preview": raw[:300],
            "recovery_action": "retry",  # or "fallback", "skip"
            "attempt": attempt_number,
        },
        "tags": ["parse_error", "phase1"],
    })

# When tool parameter validation fails:
if _task:
    _task.event("custom", payload={
        "kind": "parse_error",
        "summary": f"Tool parameter validation failed: {tool_name}",
        "data": {
            "phase": "phase2",
            "tool_name": tool_name,
            "error_type": "schema_mismatch",
            "expected_schema": schema_summary[:200],
            "received": str(params)[:200],
        },
        "tags": ["parse_error", "phase2", "validation"],
    })
```

**Questions answered:**
- How often does JSON parsing fail?
- Which models produce more parse errors?
- Are tool parameters frequently invalid?
- How many hidden retries are happening?
- Is a specific response format causing problems?

**Effort:** ~30 minutes. Instrument parse failure catch blocks.

---

## Tier 3 — Medium Impact, Medium Effort

These provide operational depth and production readiness.

---

### 11. Prompt Composition Tracking (Technical + Cost)

**The gap:** The LLM call tracking shows tokens_in/out but not *what* those tokens are. A 10,000-token prompt might be 60% system prompt, 30% history, and 10% user query — or the ratio might be totally different. Understanding prompt composition is key to cost optimization.

**What loopCore has:**
- `loop.py:1585–1690` — Prompt building methods
- `ContextManager.build()` — assembles system + identity + skills + memory + plan context
- Separate token counts available for each component
- Tool catalog injection with selectable tools
- History window construction

**What to send:**

```python
# After prompt is assembled, before LLM call:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "prompt_composition",
        "summary": f"Prompt: {total_tokens} tokens ({len(messages)} messages)",
        "data": {
            "total_tokens": total_tokens,
            "system_prompt_tokens": system_tokens,
            "identity_tokens": identity_tokens,
            "skills_tokens": skills_tokens,
            "memory_tokens": memory_tokens,
            "plan_context_tokens": plan_tokens,
            "history_tokens": history_tokens,
            "tool_catalog_tokens": tool_catalog_tokens,
            "message_count": len(messages),
            "tools_available": len(tools),
            "phase": "phase1",  # or "phase2"
        },
        "tags": ["prompt", "composition"],
    })
```

**Questions answered:**
- What's eating the token budget?
- Is the system prompt too large?
- How fast does history accumulate?
- Could we reduce cost by trimming the tool catalog?
- Are memory/learning tokens growing unbounded?

**Effort:** ~45 minutes. Instrument context builder to count component tokens.

---

### 12. Heartbeat Enrichment (Operational)

**The gap:** The heartbeat is currently a bare alive signal. The HiveLoop SDK supports custom `heartbeat_payload` — a callback that enriches every heartbeat with agent state.

**What loopCore has:**
- Agent current state: idle, processing, error
- Current task info (if processing)
- Queue depth
- Memory usage
- Uptime
- Run count since startup

**What to send:**

```python
# At agent registration, provide a heartbeat_payload callback:
def heartbeat_enrichment():
    return {
        "status": agent.status,
        "current_task": agent.current_task_id,
        "queue_depth": len(agent.event_queue),
        "tasks_completed": agent.tasks_completed_count,
        "tasks_failed": agent.tasks_failed_count,
        "uptime_seconds": int(time.time() - agent.start_time),
        "memory_mb": process_memory_mb(),
        "turns_this_task": agent.current_turn if agent.processing else None,
    }

hiveloop_agent = hb.agent(
    agent_id=agent.name,
    type=agent.type,
    framework="loopcore",
    heartbeat_payload=heartbeat_enrichment,
    # ...
)
```

**Dashboard impact:**
- Agent Detail → Heartbeat History tab shows enriched data
- Memory trends visible over time
- Task completion rate per heartbeat interval
- Live turn count for in-progress tasks

**Questions answered:**
- Is memory growing over time (leak)?
- What's the agent's throughput (tasks/interval)?
- How far into the current task is the agent?
- Is the agent's queue growing or shrinking?

**Effort:** ~20 minutes. One callback function at registration.

---

### 13. Skill Execution Tracking (Workflow + Functional)

**The gap:** loopCore agents have "skills" — scheduled or triggered behaviors that fire on timers (via heartbeat). Skill activation, pre-check filtering, and execution are all invisible.

**What loopCore has:**
- Skills fire on heartbeat timers
- Skill pre-check: HTTP check to decide whether to skip
- Skill execution within the agent loop
- `state["skill_files_read"]` tracking
- Skill filtering logic in `runtime.py`

**What to send:**

```python
# When a skill fires:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "skill_fired",
        "summary": f"Skill: {skill_name} (trigger: {trigger_type})",
        "data": {
            "skill_name": skill_name,
            "trigger_type": trigger_type,  # "timer", "heartbeat", "manual"
            "pre_check_result": pre_check_passed,
            "files_read": skill_files_read,
        },
        "tags": ["skill", trigger_type],
    })

# When a skill is filtered out:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    _agent.event("custom", payload={
        "kind": "skill_filtered",
        "summary": f"Skill skipped: {skill_name} — {reason}",
        "data": {
            "skill_name": skill_name,
            "filter_reason": reason,  # "pre_check_failed", "not_due", "agent_busy"
        },
        "tags": ["skill", "filtered"],
    })
```

**Questions answered:**
- Which skills fire most often?
- How often are skills filtered out and why?
- Which skills consume the most tokens/time?
- Are timer-based skills running at the right frequency?

**Effort:** ~30 minutes. Instrument skill dispatch in runtime.py.

---

### 14. Queue Event Lifecycle (Workflow + Operational)

**The gap:** The plan tracks `queue_snapshot()` for current depth, but not the *flow* — when events enter the queue, how long they wait, when they're picked up, when they're dropped for overflow.

**What loopCore has:**
- Priority queue with FIFO within same priority (HIGH/NORMAL/LOW)
- Queue overflow handling: drops oldest LOW-priority items
- Queue max size: 20 items
- Event wait times calculable from `queued_at` timestamps
- Event processing times measurable

**What to send:**

```python
# When a queue item is dropped due to overflow:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    _agent.event("custom", payload={
        "kind": "issue",
        "summary": f"Queue overflow: dropped {dropped_count} LOW-priority items",
        "data": {
            "severity": "medium",
            "category": "other",
            "action": "reported",
            "issue_id": f"queue-overflow-{agent_name}",
            "context": {
                "queue_depth": current_depth,
                "max_depth": max_depth,
                "dropped_count": dropped_count,
                "dropped_priorities": ["LOW"],
            },
        },
        "tags": ["issue", "queue_overflow"],
    })

# Event processing time (from queue entry to task start):
if _task:
    _task.event("custom", payload={
        "kind": "queue_wait",
        "summary": f"Queue wait: {wait_ms}ms ({priority} priority)",
        "data": {
            "wait_ms": wait_ms,
            "priority": priority,
            "queue_depth_at_entry": depth_at_entry,
        },
        "tags": ["queue", "wait_time"],
    })
```

**Questions answered:**
- Are events being dropped? How many?
- How long do events wait in queue before processing?
- Is queue overflow causing data loss?
- Should the queue size be increased?
- Are HIGH-priority events processed quickly enough?

**Effort:** ~30 minutes. Instrument queue enqueue/dequeue/drop paths.

---

### 15. External API Call Tracking (Technical + Error)

**The gap:** When a tool calls an external API (CRM, email, calendar), the tool execution is tracked but not the underlying HTTP call. Rate limits, latency, and error codes from external services are invisible.

**What loopCore has:**
- Tools call external APIs (CRM search, email send, calendar check)
- HTTP response codes available
- Rate limit responses (429) with Retry-After headers
- API latency measurable
- Auth failures (401/403) detectable

**What to send:**

```python
# Inside tool implementations, after external API calls:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "api_call",
        "summary": f"API: {service} {endpoint} → {status_code} ({duration_ms}ms)",
        "data": {
            "service": service_name,      # "salesforce", "sendgrid", "google_calendar"
            "endpoint": endpoint[:100],
            "method": method,             # "GET", "POST"
            "status_code": status_code,
            "duration_ms": duration_ms,
            "rate_limited": status_code == 429,
            "retry_after": retry_after,
            "error": error_message[:200] if error else None,
        },
        "tags": ["api", service_name],
    })
```

**Questions answered:**
- Which external services are slow?
- Which services are rate-limiting us?
- How much time is spent waiting on external APIs vs. LLM calls?
- Are certain services intermittently failing?
- What's our API error rate by service?

**Effort:** ~1–2 hours. Depends on how many tools call external APIs. Could be centralized if tools use a common HTTP client.

---

## Tier 4 — Specialized Deep Observability

These are valuable for production operations, debugging, and optimization.

---

### 16. State Mutation Tracking (Functional + Debug)

**The gap:** loopCore agents maintain an `AtomicState` that evolves across turns — variables, completed steps, pending actions. State mutations are the agent's "working memory" and they're invisible.

**What to send:**

```python
# After state update is applied each turn:
_task = get_current_task()
if _task:
    _task.event("custom", payload={
        "kind": "state_update",
        "summary": f"State: {len(variables)} vars, {len(completed_steps)} steps",
        "data": {
            "variables_count": len(state.variables),
            "completed_steps_count": len(state.completed_steps),
            "pending_actions_count": len(state.pending_actions),
            "variables_changed": list(changed_keys)[:10],
            "new_steps": [s[:50] for s in new_steps],
            "turn_number": turn_number,
        },
        "tags": ["state"],
    })
```

**Questions answered:**
- Is state growing unbounded?
- Which variables are being set?
- How many steps does the agent think it completed?
- Is state being managed within limits?

---

### 17. Session Persistence Events (Operational)

**The gap:** loopCore persists sessions to disk. Session save/load failures are silent.

**What to send:**

```python
# On session save/load:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    _agent.event("custom", payload={
        "kind": "session_io",
        "summary": f"Session {operation}: {session_key} ({size_kb}KB, {duration_ms}ms)",
        "data": {
            "operation": operation,    # "save" or "load"
            "session_key": session_key,
            "size_bytes": size_bytes,
            "duration_ms": duration_ms,
            "success": success,
            "error": error_message if not success else None,
        },
        "tags": ["session", operation],
    })
```

**Questions answered:**
- How large are sessions?
- How long does session I/O take?
- Are sessions failing to save (data loss risk)?

---

### 18. Run Journaling (Debug + Audit)

**The gap:** loopCore writes a `journal.jsonl` flight recorder per run. This rich per-turn log exists but isn't connected to HiveBoard.

**What to send:** Rather than duplicating the journal, emit a journal summary at task completion:

```python
# After task completes:
_task = get_current_task()
if _task:
    _task.set_payload({
        "journal_path": journal_path,
        "journal_entries": journal_entry_count,
        "total_turns": total_turns,
        "total_tokens": total_tokens,
        "tools_used": list(set(tools_used)),
        "final_state_summary": state_summary[:200],
    })
```

**Questions answered:**
- Where is the detailed log for this task?
- What's the high-level run summary?
- How can I dig deeper if something went wrong?

---

### 19. Agent Lifecycle Events (Operational)

**The gap:** When agents are created, deleted, paused, or reconfigured, there's no trail.

**What to send:**

```python
# On agent lifecycle changes:
_agent = get_hiveloop_agent(agent_name)
if _agent:
    _agent.event("custom", payload={
        "kind": "agent_lifecycle",
        "summary": f"Agent {action}: {agent_name}",
        "data": {
            "action": action,    # "created", "deleted", "paused", "resumed", "reconfigured"
            "agent_name": agent_name,
            "agent_type": agent_type,
            "reason": reason,
        },
        "tags": ["lifecycle", action],
    })
```

---

### 20. Cost Attribution by Phase (Cost + Optimization)

**The gap:** The current plan tracks LLM calls individually, but there's no roll-up showing cost by *phase* (reasoning vs. tool use vs. reflection vs. planning vs. compaction) across tasks.

**What to send:** This is actually already possible with the current `task.llm_call()` names ("phase1_reasoning", "phase2_tool_use", "reflection", "create_plan", "context_compaction"). The names act as natural grouping keys.

**Enhancement:** Add `metadata` to each `task.llm_call()` with phase info:

```python
_task.llm_call(
    "phase1_reasoning",
    model=model,
    tokens_in=tokens_in,
    tokens_out=tokens_out,
    cost=cost,
    duration_ms=duration_ms,
    metadata={
        "phase": "phase1",
        "turn_number": turn_number,
        "context_tokens": context_size,
    },
)
```

**Questions answered:**
- What percentage of cost goes to reasoning vs. tool use vs. reflection?
- Is reflection cost-effective (does it reduce errors enough to justify its token cost)?
- Could we save money by using a cheaper model for certain phases?
- Which phase is the most expensive per task?

---

## Summary: The Observability Pyramid

```
                    ┌─────────────────┐
                    │   CURRENT PLAN  │  ← What the instrumentation plan covers
                    │   (15-20%)      │
                    │                 │
                    │  LLM calls (6)  │
                    │  Task lifecycle │
                    │  Actions (3)    │
                    │  Narrative      │
                    │  events         │
                ┌───┴─────────────────┴───┐
                │     TIER 1 (this doc)   │  ← Quick wins, biggest gaps
                │     (+15-20%)           │
                │                         │
                │  Tool execution         │
                │  Turn metrics           │
                │  Reflection decisions   │
                │  Loop detection         │
                │  Context utilization    │
            ┌───┴─────────────────────────┴───┐
            │       TIER 2 (this doc)         │  ← Deep understanding
            │       (+15-20%)                 │
            │                                 │
            │  Learning/memory events         │
            │  Config snapshots               │
            │  Multi-agent communication      │
            │  Timeout/limit events           │
            │  Parse/validation errors        │
        ┌───┴─────────────────────────────────┴───┐
        │           TIER 3 (this doc)             │  ← Operational depth
        │           (+15-20%)                     │
        │                                         │
        │  Prompt composition                     │
        │  Heartbeat enrichment                   │
        │  Skill execution                        │
        │  Queue event lifecycle                  │
        │  External API tracking                  │
    ┌───┴─────────────────────────────────────────┴───┐
    │               TIER 4 (this doc)                 │  ← Specialized/debug
    │               (+15-20%)                         │
    │                                                 │
    │  State mutation tracking                        │
    │  Session persistence                            │
    │  Run journaling integration                     │
    │  Agent lifecycle events                         │
    │  Cost attribution by phase                      │
    └─────────────────────────────────────────────────┘
```

---

## Implementation Roadmap

| Priority | Items | Effort | Cumulative Coverage |
|----------|-------|--------|---------------------|
| **Already planned** | LLM calls, task lifecycle, actions, narrative events | ~2.5 hours | ~20% |
| **Tier 1** (items 1–5) | Tool execution, turns, reflection decisions, loop detection, context | ~1.5 hours | ~40% |
| **Tier 2** (items 6–10) | Learning, config, multi-agent, timeouts, parsing | ~3.5 hours | ~60% |
| **Tier 3** (items 11–15) | Prompts, heartbeat, skills, queue lifecycle, external APIs | ~3.5 hours | ~80% |
| **Tier 4** (items 16–20) | State, sessions, journal, agent lifecycle, cost phases | ~2 hours | ~95% |

**Total additional effort: ~10.5 hours** to go from 20% to 95% observability coverage.

---

## What Changes on the Dashboard

With full instrumentation, here's what a single task timeline would show:

```
[Task Started]
  ├─ [Config: phase1=sonnet-4, reflection=on, planning=on]
  ├─ [Context Built: 4,200 tokens (42% budget)]
  ├─ [LLM: phase1_reasoning → sonnet-4 (4,200 in / 850 out, $0.016, 2.1s)]
  ├─ [Reflection: continue — "Good analysis, proceed with tool call"]
  ├─ [Tool: search_crm → success (1.2s)]
  │   └─ [API: salesforce GET /query → 200 (890ms)]
  ├─ [State Update: +1 variable, +1 completed_step]
  ├─ [Turn 1 complete: 5,050 tokens, 3.8s]
  ├─ [Context Built: 8,100 tokens (81% budget)]
  ├─ [LLM: phase1_reasoning → sonnet-4 (8,100 in / 600 out, $0.033, 1.8s)]
  ├─ [Reflection: continue — "CRM data received, generate email"]
  ├─ [LLM: phase2_tool_use → sonnet-4 (2,400 in / 1,200 out, $0.025, 2.5s)]
  ├─ [Tool: send_email → success (0.4s)]
  │   └─ [API: sendgrid POST /v3/mail/send → 202 (380ms)]
  ├─ [Learning: captured "CRM search → email" pattern]
  ├─ [Turn 2 complete: 4,200 tokens, 5.2s]
  ├─ [Plan Step 2/3 completed: "Email sent to lead"]
[Task Completed: 2 turns, 9,250 tokens, $0.074, 9.0s]
```

Compare this to what the current plan shows:

```
[Task Started]
  ├─ [LLM: phase1_reasoning → sonnet-4 (4,200 in / 850 out, $0.016)]
  ├─ [LLM: phase1_reasoning → sonnet-4 (8,100 in / 600 out, $0.033)]
  ├─ [LLM: phase2_tool_use → sonnet-4 (2,400 in / 1,200 out, $0.025)]
[Task Completed: $0.074, 9.0s]
```

The difference is night and day. The first version tells a complete story. The second shows data points with gaps.

---

## Key Insight

**The current instrumentation plan focuses on the LLM as the unit of observation. But the agent is the unit of observation.**

LLM calls are one component of agent behavior. The agent also selects tools, executes them, manages context, makes decisions, learns patterns, communicates with other agents, handles errors, and manages state. All of these are observable, and all of them matter for understanding what the agent is doing and why.

The 20 recommendations in this document shift the observability model from "what did the LLM do?" to "what did the agent do?" — which is what HiveBoard was designed for.

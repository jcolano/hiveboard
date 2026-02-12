# HiveBoard Gap Analysis
## What loopCore's Observability Reveals About the Spec

**Source material:** loopCore's OBSERVABILITY.md, loop.py (1,731 lines), index.html (799 lines), index.js (4,262 lines)

---

## 1. Architecture Comparison

loopCore and HiveBoard think about observability through different lenses. loopCore organizes around **three pillars** — Runs (history), Sessions (memory), Runtime (live state). HiveBoard organizes around a **single primitive** — the event stream, with everything derived from it.

| Concept | loopCore | HiveBoard |
|---|---|---|
| Core data primitive | Run (result.json), Session, AgentEvent | Event (single table) |
| History | Immutable run records on disk | Events with `task_completed` status |
| Live state | In-memory AgentState, transient | Derived from latest events |
| Conversation | Sessions table (mutable) | Out of scope (agent-internal) |
| Real-time | 3-second polling | WebSocket push |
| Tenancy | Single-tenant | Multi-tenant from day one |
| Framework coupling | Deeply integrated with the two-phase loop | Framework-agnostic |

**The key structural difference:** loopCore's observability is *inside* the agent. It knows about phases, plans, reflections, and atomic state because it **is** the loop. HiveBoard's observability is *outside* the agent. It only knows what the agent tells it via events. This is the fundamental tradeoff — loopCore has richer data but is locked to one architecture. HiveBoard has universal reach but depends on what developers instrument.

The goal: make HiveBoard's event model expressive enough that a developer instrumenting loopCore-style agents gets the same richness they have today.

---

## 2. What Maps Cleanly (No Spec Changes Needed)

These loopCore concepts already have a direct home in the HiveBoard event schema:

### 2.1 Agent Lifecycle
| loopCore | HiveBoard Event |
|---|---|
| Agent start (`/agents/{id}/start`) | `agent_registered` |
| Heartbeat tick | `heartbeat` |
| Agent stop | No event (heartbeat stops → stuck detection) |

### 2.2 Task Lifecycle
| loopCore | HiveBoard Event |
|---|---|
| AgentEvent popped from queue → execution starts | `task_started` |
| LoopResult with status `completed` | `task_completed` |
| LoopResult with status `error`/`timeout`/`max_turns` | `task_failed` |
| LoopResult with status `escalation_needed` | `escalated` |

### 2.3 Action Tracking
| loopCore | HiveBoard Event |
|---|---|
| Phase 1 reasoning starts | `action_started` (action_name: "phase1_reasoning") |
| Phase 2 parameter generation | `action_started` (nested under phase1 via parent_action_id) |
| Tool execution | `action_started` / `action_completed` |
| Tool failure | `action_failed` |

### 2.4 Human-in-Loop
| loopCore | HiveBoard Event |
|---|---|
| Event with `status: "pending_approval"` | `approval_requested` |
| `POST /agents/{id}/events/{eid}/approve` | `approval_received` |
| `POST /agents/{id}/events/{eid}/drop` | `approval_received` (status: "cancelled") |

### 2.5 Error Recovery
| loopCore | HiveBoard Event |
|---|---|
| Reflection decision: "adjust" / "pivot" | `custom` (kind: "reflection") |
| Replanning | `custom` (kind: "replan") |
| Loop detection → retry | `retry_started` |

---

## 3. What Reveals Gaps in the Spec

These are the things loopCore captures that HiveBoard's current spec either can't represent well or doesn't surface in the dashboard.

### Gap 1: LLM Call Content as First-Class Data

**What loopCore does:** The journal captures every LLM call with full detail — Phase 1 reasoning output (JSON with analysis, tool choice, intent), Phase 2 parameter generation, token counts split by phase, model used. The `caller` field even identifies which call it was (`atomic_phase1_turn_3`, `atomic_phase2_turn_3`).

**What HiveBoard has:** A `payload` field on any event (32KB JSON). The developer can put anything in it. But there's no structure that says "this event is an LLM call" and no dashboard rendering that knows how to display prompts, responses, and token breakdowns.

**Recommendation:** Add `task.llm_call()` as a convenience method in the SDK spec, and add an `llm_call` entry to the recommended `payload.kind` values. The dashboard should render `kind: "llm_call"` events with a specialized view showing prompt preview, response preview, model, tokens in/out, and cost.

```python
# SDK addition
task.llm_call(
    name="phase1_reasoning",
    model="claude-sonnet-4-20250514",
    prompt_preview=prompt[:500],
    response_preview=response[:500],
    tokens_in=1500,
    tokens_out=200,
    cost=0.003,
    metadata={"caller": "atomic_phase1_turn_3"}
)
```

This maps to a `custom` event with a well-known payload shape. No schema change needed — just SDK sugar and dashboard awareness.

---

### Gap 2: Cost Aggregation by Model

**What loopCore does:** A dedicated Usage tab with three views:
1. **Per-agent breakdown** — calls, input/output tokens, input/output cost, total cost
2. **Per-model breakdown** — which models are consuming what
3. **Recent calls** — last 50 individual LLM calls with all cost details

This was the feature that enabled the 5x cost reduction. The user could see that certain agents were using expensive models when cheaper ones would suffice, that certain prompts were bloated, and that certain workflows made too many LLM calls.

**What HiveBoard has:** The metrics endpoint aggregates `payload.data.cost` across events. But it doesn't know about models (that's inside the payload), doesn't break down input vs. output cost, and doesn't have a "Recent LLM Calls" view.

**Recommendation:** This is important enough to be a defined dashboard screen, not just a metrics query. Add a **Cost Explorer** view (could be a tab within Agent Detail or a standalone screen) that:
- Aggregates cost by agent, by model, by time period
- Shows per-call detail for recent LLM calls (using `kind: "llm_call"` events)
- Shows input vs. output token breakdown

This doesn't require schema changes — it requires the dashboard to understand `kind: "llm_call"` payloads and the metrics endpoint to support `group_by=payload.data.model`.

---

### Gap 3: Plan-Aware Timeline

**What loopCore does:** The execution trace includes `plan_created`, `step_started`, `step_completed`, and `replan` events. The dashboard renders a plan with checkmarks, step descriptions, turn counts, and token usage per step. The `step_stats` aggregate turns and tokens by plan step index.

**What HiveBoard has:** The Task Timeline shows events on a horizontal axis. Actions nest via `action_id` / `parent_action_id`. But there's no concept of a "plan step" — a higher-level grouping that contains multiple actions.

**Recommendation:** Plans map naturally to a two-level action nesting:
- **Plan step** → top-level `action_started`/`action_completed` with `action_name: "step_1_search_crm"`
- **Tool calls within a step** → nested actions via `parent_action_id`

The timeline already supports nesting. What's needed is a payload convention for plan steps:

```python
# When a plan step starts
task.event("custom", payload={
    "kind": "plan_step",
    "summary": "Step 1: Search CRM for active deals",
    "data": {"step_index": 0, "total_steps": 4}
})
```

And the dashboard could render a plan progress bar above the timeline when it detects `kind: "plan_step"` events. This is a dashboard enhancement, not a schema change.

---

### Gap 4: Agent Self-Reported Issues

**What loopCore does:** Agents have a `report_issue` tool that creates persistent issue records with severity, category, occurrence count, and deduplication. Issues appear in the Runtime tab with color-coded severity badges and dismiss/TODO-creation actions. This is a feedback loop — the agent identifies problems it can't solve and flags them for human attention.

**What HiveBoard has:** No concept of agent-reported issues. The closest thing is an `escalated` event, but that's a one-time event, not a persistent issue with deduplication and dismissal workflow.

**Recommendation:** This is a v2 feature, not v1. But the event schema already supports it:

```python
task.event("escalated", payload={
    "kind": "issue",
    "summary": "CRM API returning 403 for workspace queries",
    "data": {
        "severity": "high",
        "category": "permissions",
        "context": {"tool": "crm_search", "error_code": 403}
    }
})
```

The gap is in the dashboard — issues need aggregation (deduplication by title), persistence (they shouldn't disappear when the event ages out), and a management UI (dismiss, create TODO). For v1, these show up as escalation events in the activity stream. For v2, a dedicated Issues panel.

---

### Gap 5: Heartbeat Summaries vs. Bare Pings

**What loopCore does:** Heartbeat history entries are rich — they include `skills_triggered`, `turn_count`, `status`, and `summary_lines` (LLM-generated one-liner summaries of what happened). You can look at the heartbeat history and see "8:00 AM — CRM sync fired, 3 turns, found 2 new leads, completed."

**What HiveBoard has:** Heartbeats are bare pings. The `heartbeat` event has a timestamp and that's it. Heartbeat compaction keeps one per hour after 24 hours. The dashboard shows "last heartbeat: 30s ago" — alive/dead, nothing more.

**Recommendation:** Keep heartbeats lightweight in the event stream (they're 60% of volume). But allow an optional `payload` on heartbeat events for teams that want richer data:

```python
# SDK: heartbeat callback option
agent = hb.agent(
    "lead-qualifier",
    heartbeat_interval=30,
    heartbeat_payload=lambda: {
        "summary": "Idle, last task completed 5m ago",
        "queue_depth": 3,
        "tasks_completed_since_last": 2
    }
)
```

This is already possible in the spec (heartbeat events accept payload). What's missing is the SDK convenience and dashboard rendering. Agent Detail could show a "Heartbeat History" section that displays payload summaries when available.

---

### Gap 6: Turn-Level Granularity

**What loopCore does:** Every turn is a first-class data structure with: turn number, timestamp, LLM text, tool calls (with parameters and results), token usage (Phase 1 + Phase 2), duration, and plan step association. The dashboard renders a "Turn Details" table showing per-turn breakdown.

**What HiveBoard has:** Actions, not turns. The `@agent.track` decorator captures function-level events. If a turn contains one function call, that's one action. But the concept of a "turn" as a reasoning+action unit with aggregate token counts doesn't exist natively.

**Recommendation:** Turns are an agent-architecture concept. Some agents have turns, some don't. Rather than making turns a first-class event type, the better approach is to let developers model turns as top-level actions:

```python
with agent.task("task_123", project="sales") as task:
    for turn_number in range(1, max_turns + 1):
        with task.action(f"turn_{turn_number}") as turn_action:
            # Phase 1
            with task.action("phase1_reasoning") as p1:
                response = llm.complete(...)
                task.llm_call(name="phase1", model="sonnet", ...)

            # Phase 2 + Tool execution
            with task.action("phase2_execute") as p2:
                result = tool.execute(...)
                task.llm_call(name="phase2", model="haiku", ...)
```

This produces a nested action tree: `turn_1 → phase1_reasoning, phase2_execute`. The timeline renders it hierarchically. No new event types needed — just nesting depth.

---

### Gap 7: Queue Visibility

**What loopCore does:** The Runtime tab shows the event queue — what's waiting, what's being processed, what priority each item has. This is critical for understanding backlog and identifying when agents are overwhelmed.

**What HiveBoard has:** No concept of an agent's internal queue. HiveBoard sees events after they happen, not work that's pending.

**Recommendation:** This is intentionally out of scope. HiveBoard is external observability, not internal agent state management. The queue is agent-internal. However, a developer could emit custom events for queue state:

```python
agent.event("custom", payload={
    "kind": "queue_status",
    "summary": f"Queue depth: {len(queue)}, oldest: {oldest_age}s",
    "data": {"depth": len(queue), "oldest_age_seconds": 45}
})
```

The Hive card could display queue depth if it detects this pattern in recent custom events. This is a stretch goal, not v1.

---

## 4. What HiveBoard Gets Right That loopCore Doesn't

These are strengths in the HiveBoard spec that loopCore's architecture can't match:

### 4.1 Framework Agnosticism
loopCore's observability is welded to its two-phase loop. If you switch to a different agent architecture, you lose everything. HiveBoard's event model works for any agent that can emit HTTP events.

### 4.2 Fleet Overview
loopCore shows one agent at a time. The Runtime tab requires selecting an agent. HiveBoard's Hive shows all agents simultaneously with visual urgency sorting (stuck → error → waiting → processing → idle). This is the wall-monitor view that loopCore doesn't have.

### 4.3 Task Timeline Visualization
loopCore shows runs as tables and lists — turn details, trace events, journal entries all in separate sections. HiveBoard's horizontal timeline with color-coded nodes, time gap stretching, and error branching is a fundamentally better way to understand what happened on a task.

### 4.4 Real-Time Push
loopCore polls every 3 seconds. HiveBoard uses WebSocket push. For a dashboard you keep open during deployments, push is essential — you see events as they happen, not up to 3 seconds later.

### 4.5 Alert Rules
loopCore has no alerting. If an agent gets stuck at 3 AM, nobody knows until they check the dashboard. HiveBoard's alert rules with Slack/PagerDuty/email webhooks close this gap.

### 4.6 Multi-Tenant + Projects
loopCore is a single-user admin panel. HiveBoard supports multiple workspaces, API key scoping, and project organization from day one.

---

## 5. Instrumentation Mapping

Here's exactly how loopCore's key instrumentation points would translate to HiveLoop calls. This is the migration playbook for making loopCore the first HiveBoard customer.

### 5.1 Agent Registration (loop.py line ~584-661)

```python
# Current (implicit — no registration event)
loop = AgenticLoop(llm_client=client, tool_registry=registry, agent_id="lead-qualifier")

# HiveLoop equivalent
hb = hiveloop.init(api_key="hb_live_xxx")
agent = hb.agent("lead-qualifier", type="sales", version="1.2.0",
                  framework="loopcore", heartbeat_interval=30)
```

### 5.2 Task Execution (loop.py line ~1139-1183)

```python
# Current
result = loop.execute(message="Check pipeline", system_prompt="...")

# HiveLoop equivalent
with agent.task(task_id="evt_abc123", project="sales-pipeline",
                type="webhook_processing") as task:
    result = loop.execute(message="Check pipeline", system_prompt="...")
    if result.status == "completed":
        task.complete()
    else:
        task.fail(error=result.error)
```

### 5.3 Journal Entries → Custom Events (loop.py lines ~1310-1321, 1489-1499)

```python
# Current: journal append
exec_state["journal"].append({
    "event": "phase1_decision",
    "turn": turn_number,
    "done": is_done,
    "tool": tool_name,
    "intent": intent,
    "tokens": {"input": p1_input, "output": p1_output},
})

# HiveLoop equivalent
task.llm_call(
    name=f"phase1_turn_{turn_number}",
    model="claude-sonnet-4-20250514",
    prompt_preview=phase1_prompt[:500],
    response_preview=json.dumps(phase1_response)[:500],
    tokens_in=p1_input,
    tokens_out=p1_output,
    cost=calculate_cost(p1_input, p1_output, "sonnet"),
    metadata={"done": is_done, "tool_chosen": tool_name, "intent": intent}
)
```

### 5.4 Tool Execution (loop.py lines ~1466-1498)

```python
# Current: tool_result journal entry
exec_state["journal"].append({
    "event": "tool_result",
    "tool": tool_name,
    "success": tool_result.success,
    "error": tool_result.error,
    "output_preview": (tool_result.output or "")[:500],
    "parameters": parameters,
})

# HiveLoop equivalent (already handled by @agent.track decorator)
@agent.track("tool_execution")
def execute_tool(tool_name, parameters):
    result = tool_registry.execute(tool_name, parameters)
    task.event("custom", payload={
        "kind": "tool",
        "summary": f"{tool_name}: {'OK' if result.success else 'FAIL'}",
        "data": {
            "tool": tool_name,
            "parameters": parameters,
            "output_preview": (result.output or "")[:500],
            "success": result.success,
            "error": result.error,
        }
    })
    return result
```

### 5.5 Execution Trace → Events (loop.py lines ~1122-1133)

```python
# Current: trace event
state["execution_trace"].append({
    "event": "step_completed",
    "step_index": completed_idx,
    "detail": step_summary,
})

# HiveLoop equivalent
task.event("custom", payload={
    "kind": "plan_step",
    "summary": f"Step {completed_idx} completed: {step_summary}",
    "data": {"step_index": completed_idx, "status": "completed"},
    "tags": ["plan", "step_completed"]
})
```

### 5.6 Reflection (loop.py lines ~878-977)

```python
# Current: reflection recorded in state
state["reflections"].append(reflection)

# HiveLoop equivalent
task.event("custom", payload={
    "kind": "reflection",
    "summary": f"Reflection: {reflection.decision} (confidence: {reflection.confidence_in_approach})",
    "data": {
        "decision": reflection.decision,
        "reasoning": reflection.reasoning,
        "confidence": reflection.confidence_in_approach,
        "next_action": reflection.next_action,
        "trigger": trigger,
    },
    "tags": ["reflection", reflection.decision]
})
```

### 5.7 Cost Tracking (index.js lines ~4136-4241)

```python
# Current: separate /usage API endpoint with per-call cost tracking

# HiveLoop equivalent: cost data lives in llm_call events
# The dashboard aggregates from events with kind: "llm_call"
task.llm_call(
    name="phase1_reasoning",
    model="claude-sonnet-4-20250514",
    tokens_in=1500,
    tokens_out=200,
    cost=0.0031,    # pre-calculated by the developer
)
```

---

## 6. Recommended Spec Updates

Based on this analysis, here are the changes I recommend to the existing specs, ordered by impact:

### 6.1 SDK Spec — Add `task.llm_call()` (HIGH IMPACT)

Add a convenience method that produces a well-structured `custom` event for LLM calls. This is the single most important addition — it's what enabled the 5x cost reduction in loopCore.

### 6.2 Dashboard Spec — Add Cost Explorer View (HIGH IMPACT)

A new screen or tab within Agent Detail that aggregates `kind: "llm_call"` events into per-agent, per-model cost breakdowns with recent call detail. This is what makes cost observability actionable.

### 6.3 SDK Spec — Add `agent.event()` for Agent-Level Custom Events (MEDIUM IMPACT)

Currently custom events require a task context. loopCore emits agent-level events (issues, queue status) that aren't tied to a specific task. Add `agent.event()` that emits custom events with `task_id = null`.

### 6.4 Dashboard Spec — Render LLM Call Events Specially (MEDIUM IMPACT)

When the Timeline encounters a `custom` event with `kind: "llm_call"`, render it with a distinct visual treatment: show prompt preview, response preview, token counts, model badge, and cost.

### 6.5 SDK Spec — Heartbeat Payload Callback (LOW IMPACT)

Allow an optional callback function on agent registration that provides payload data for heartbeat events. This enables rich heartbeat history for teams that want it.

### 6.6 Dashboard Spec — Plan Progress Indicator (LOW IMPACT, v2)

When the Timeline detects `kind: "plan_step"` events, render a plan progress bar above the timeline showing step completion, turn counts, and token usage per step.

---

## 7. What NOT to Change

Some loopCore features should **not** be ported to HiveBoard:

- **Sessions/conversation persistence** — This is agent-internal state, not observability. HiveBoard doesn't need to know about conversation history.

- **Queue management** (approve/drop/priority) — This is agent control, not observation. HiveBoard observes; it doesn't operate. Control plane features belong in the agent framework.

- **Scheduled tasks / heartbeat timers** — Agent-internal scheduling. HiveBoard detects missed heartbeats but doesn't manage heartbeat timing.

- **TODO lists** — Agent-internal work management. These could appear as custom events but don't need dedicated dashboard treatment.

- **Prompt debugging / "Log LLM Prompts" toggle** — This is a development-time debugging feature. HiveBoard's equivalent is the developer choosing what to put in `llm_call` payloads.

---

## 8. Summary

loopCore's observability is *excellent* for its context — a single-user, tightly-coupled admin panel for one agent framework. HiveBoard's job is to take the insights embedded in that implementation and make them universal, multi-tenant, framework-agnostic, and real-time.

The three highest-value takeaways:

1. **LLM call content and cost tracking is not optional.** It's the feature that pays for the product. `task.llm_call()` needs to be in v1.

2. **The Usage/Cost view is a killer feature.** Per-agent, per-model cost breakdown with individual call detail is what enabled the 5x cost reduction. This should be a first-class dashboard screen.

3. **The journal/flight recorder pattern validates our timeline design.** loopCore's journal is a chronological list of structured events per task — which is exactly what HiveBoard's Task Timeline renders. The difference is that HiveBoard visualizes it instead of listing it.

The event schema doesn't need structural changes. The payload conventions, SDK convenience methods, and dashboard rendering need to understand LLM calls as a distinct category of event.

---

*End of Analysis*

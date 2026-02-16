# loopCore Architecture Guide

A visual guide to how autonomous agents work in the loopCore framework.

> **HiveBoard Observability**: This document is annotated with `[S#N]` markers
> showing where each of the 28 HiveBoard SDK sensors is applied. See
> [Section 13](#13-hiveboard-sensor-coverage-map) for the complete mapping.

---

## 1. System Overview

```
                          ENTRY POINTS
                 ┌──────────┬──────────────┐
                 │          │              │
              CLI/REPL   REST API     Desktop Client
                 │      (port 8431)       │
                 └──────┬──┴──────────────┘
                        │
                        v
               ┌─────────────────┐
               │  AgentManager   │  Orchestrator: creates agents,
               │                 │  wires components, rate limits
               │  [S#1] init()   │  HiveLoop SDK initialized here
               │  [S#2] agent()  │  Each agent registered with HiveLoop
               │  [S#4] heartbeat│  heartbeat_payload callback wired
               │  [S#6] queue    │  queue_provider callback wired
               └────────┬────────┘
                        │ creates
                        v
               ┌─────────────────┐
               │     Agent       │  Bundles: config + tools + skills
               │                 │          + memory + loop
               └────────┬────────┘
                        │ delegates to
                        v
               ┌─────────────────┐
               │  AgenticLoop    │  Core engine: prompt -> LLM ->
               │  (Two-Phase)    │  tool -> repeat until done
               └────────┬────────┘
                        │ calls
              ┌─────────┼──────────┐
              v         v          v
         ┌────────┐ ┌────────┐ ┌────────┐
         │  LLM   │ │ Tools  │ │  Safe- │
         │ Client │ │Registry│ │ guards │
         └────────┘ └────────┘ └────────┘
          Anthropic   25+ tools   Loop detector
          / OpenAI    sandboxed   Reflection
                                  Planning
```

### HiveBoard Bootstrap (api/app.py + agent_manager.py)

The observability pipeline is wired at startup:

1. **`[S#1]` `hiveloop.init()`** — Called once in `create_app()` (`api/app.py:4006`).
   Initializes the SDK singleton with API key, endpoint, and environment.

2. **`[S#2]` `hb.agent()`** — Called per agent in `load_agent()` (`agent_manager.py:628`).
   Registers agent with type, version, framework, heartbeat_interval, stuck_threshold.
   Auto-starts background heartbeat thread (emits `heartbeat` every 30s).

3. **`[S#4]` `heartbeat_payload`** — Custom callback wired at registration
   (`agent_manager.py:609-626`). Returns on every heartbeat: uptime_seconds,
   total_runs, total_tokens, total_cost, consecutive_errors, active_skills,
   heartbeats_fired/skipped.

4. **`[S#6]` `queue_provider`** — Custom callback wired at registration
   (`agent_manager.py:569-607`). Returns on every heartbeat: queue depth,
   priority breakdown, top events, current run info.

5. **`[S#25]` `agent.event("config_snapshot")`** — Emitted once per agent after
   registration (`agent_manager.py:641-651`). Captures model, phase2_model,
   temperature, max_tokens, max_turns, timeout, role, skills, tools_enabled,
   planning/reflection/learning flags.

---

## 2. The Agentic Loop (Core Engine)

This is the heart of loopCore. Every agent execution ultimately runs through
this loop. It uses a **two-phase atomic architecture** where each turn
consists of two separate LLM calls.

```
    ┌──────────────────────────────────────────────────────────────────┐
    │                     AGENTIC LOOP (loop.py)                      │
    │                                                                  │
    │  ┌─────────────────────────────────────────────────────────┐    │
    │  │                    INITIALIZATION                        │    │
    │  │                                                          │    │
    │  │  1. Reset all managers (loop detector, reflection,       │    │
    │  │     planning, learning)                                  │    │
    │  │  2. Load learning insights from past runs                │    │
    │  │  3. Create execution plan (if task is complex enough)    │    │
    │  │     [S#19] task.plan() — plan emitted to HiveBoard       │    │
    │  │     [S#20] task.plan_step() — first step started         │    │
    │  │     [S#18] task.llm_call("plan") — plan LLM tracked      │    │
    │  │  4. Build full system prompt:                            │    │
    │  │     [system_prompt + identity + skills + memory]         │    │
    │  │  5. Build compact tool catalog (names + short hints)     │    │
    │  │  6. Initialize AtomicState (empty)                       │    │
    │  │  7. Seed state variables with credentials                │    │
    │  └──────────────────────────┬──────────────────────────────┘    │
    │                             │                                    │
    │                             v                                    │
    │  ┌──────────────────────────────────────────────────────────┐   │
    │  │                   TURN LOOP (1..max_turns)                │   │
    │  │                                                           │   │
    │  │  ┌─────────────────────────────────────────────────────┐  │   │
    │  │  │ GUARDS: Check timeout / Check cancellation          │  │   │
    │  │  └────────────────────────┬────────────────────────────┘  │   │
    │  │                           │                                │   │
    │  │                           v                                │   │
    │  │  ┌─────────────────────────────────────────────────────┐  │   │
    │  │  │ PHASE 1: REASONING (full model, e.g. Claude Opus)  │  │   │
    │  │  │ [S#18] task.llm_call("phase1_reasoning")            │  │   │
    │  │  │        + metadata: turn, state, stop_reason,        │  │   │
    │  │  │          cache tokens, context_utilization,          │  │   │
    │  │  │          prompt_breakdown (system/identity/skills/   │  │   │
    │  │  │          tools/plan/state token estimates)           │  │   │
    │  │  │ [S#25] event("context_pressure") if >80% context    │  │   │
    │  │  │                                                     │  │   │
    │  │  │ Input:                                              │  │   │
    │  │  │   - System prompt (identity, skills, memory)        │  │   │
    │  │  │   - Task description                                │  │   │
    │  │  │   - Current AtomicState (JSON)                      │  │   │
    │  │  │   - Tool catalog (names only, ~350 tokens)          │  │   │
    │  │  │   - Last tool result (if any)                       │  │   │
    │  │  │   - Plan context (current step, progress %)         │  │   │
    │  │  │   - Turn exchanges (intra-run history)              │  │   │
    │  │  │   - Heartbeat context (cross-run summaries)         │  │   │
    │  │  │                                                     │  │   │
    │  │  │ Output (JSON):                                      │  │   │
    │  │  │   {                                                 │  │   │
    │  │  │     "analysis": "what the last result revealed",    │  │   │
    │  │  │     "state_update": { variables, completed_steps,   │  │   │
    │  │  │                       pending_actions },            │  │   │
    │  │  │     "step_summary": "what to do next",              │  │   │
    │  │  │     "tool": "tool_name" | null,                     │  │   │
    │  │  │     "intent": "exactly what the tool should do",    │  │   │
    │  │  │     "done": true/false,                             │  │   │
    │  │  │     "response_text": "final answer (when done)"     │  │   │
    │  │  │   }                                                 │  │   │
    │  │  └────────────────────────┬────────────────────────────┘  │   │
    │  │                           │                                │   │
    │  │              ┌────────────┴────────────┐                   │   │
    │  │              v                         v                   │   │
    │  │        done=true?               done=false                 │   │
    │  │        or tool=null?            tool selected               │   │
    │  │              │                         │                   │   │
    │  │              │                         v                   │   │
    │  │              │  ┌──────────────────────────────────────┐   │   │
    │  │              │  │ PHASE 2: PARAMETERS                 │   │   │
    │  │              │  │ (can use smaller model, e.g. Sonnet)│   │   │
    │  │              │  │ [S#18] task.llm_call("phase2_tool")  │   │   │
    │  │              │  │        + metadata: turn, tool,       │   │   │
    │  │              │  │          stop_reason, cache tokens   │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ On parse failure:                    │   │   │
    │  │              │  │ [S#24] task.retry() — attempt logged │   │   │
    │  │              │  │ [S#25] event("parse_error") — detail │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ Input:                               │   │   │
    │  │              │  │   - Intent string from Phase 1       │   │   │
    │  │              │  │   - ONE tool schema (full params)    │   │   │
    │  │              │  │   - State variables (for IDs/values) │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ Output:                              │   │   │
    │  │              │  │   - Native tool_use block with       │   │   │
    │  │              │  │     concrete parameter values        │   │   │
    │  │              │  └──────────────────┬───────────────────┘   │   │
    │  │              │                     │                       │   │
    │  │              │                     v                       │   │
    │  │              │  ┌──────────────────────────────────────┐   │   │
    │  │              │  │ TOOL EXECUTION                       │   │   │
    │  │              │  │ [S#16] track_context(tool_name)      │   │   │
    │  │              │  │        wraps entire execution         │   │   │
    │  │              │  │ [S#14] set_payload({tool, success,    │   │   │
    │  │              │  │        error, duration_ms, args,      │   │   │
    │  │              │  │        result_preview, result_size})  │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ - Loop detection (abort if stuck)    │   │   │
    │  │              │  │   [S#9]  report_issue(cycle)         │   │   │
    │  │              │  │   [S#25] event("cycle_detected")     │   │   │
    │  │              │  │ - Execute via ToolRegistry           │   │   │
    │  │              │  │ - 30s timeout, 100KB output limit    │   │   │
    │  │              │  │ - Track skill file reads             │   │   │
    │  │              │  │ - Learn from result (success/error)  │   │   │
    │  │              │  │   [S#25] event("learning_captured")  │   │   │
    │  │              │  │ - Update AtomicState error_context   │   │   │
    │  │              │  │   [S#25] event("error_context_set")  │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ On tool failure:                     │   │   │
    │  │              │  │ [S#9]  report_issue(auto-categorized)│   │   │
    │  │              │  │        timeout/rate_limit/permissions/│   │   │
    │  │              │  │        connectivity/data_quality/other│   │   │
    │  │              │  └──────────────────┬───────────────────┘   │   │
    │  │              │                     │                       │   │
    │  │              │                     v                       │   │
    │  │              │  ┌──────────────────────────────────────┐   │   │
    │  │              │  │ POST-TURN CHECKS                     │   │   │
    │  │              │  │ [S#25] event("state_mutation")       │   │   │
    │  │              │  │        completed_steps, variables,    │   │   │
    │  │              │  │        current_step, pending_actions  │   │   │
    │  │              │  │ [S#25] event("turn_completed")       │   │   │
    │  │              │  │        phase1/2 tokens, tool, success,│   │   │
    │  │              │  │        turn_duration, cumulative cost │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ - Planning: check step completion,   │   │   │
    │  │              │  │   advance plan, replan if stuck      │   │   │
    │  │              │  │   [S#20] plan_step("completed")      │   │   │
    │  │              │  │   [S#20] plan_step("started") next   │   │   │
    │  │              │  │   [S#20] plan_step("failed") if stuck│   │   │
    │  │              │  │ - Reflection: self-evaluate if       │   │   │
    │  │              │  │   triggered                          │   │   │
    │  │              │  │   [S#18] llm_call("reflection")      │   │   │
    │  │              │  │   [S#25] event("reflection_started") │   │   │
    │  │              │  │   [S#25] event("reflection_completed")   │   │
    │  │              │  │   If escalate:                       │   │   │
    │  │              │  │   [S#21] task.escalate()             │   │   │
    │  │              │  │   [S#25] event("escalation_context") │   │   │
    │  │              │  │ - Turn callback (notify caller)      │   │   │
    │  │              │  └──────────────────┬───────────────────┘   │   │
    │  │              │                     │                       │   │
    │  │              │                     └──── next turn ──┐     │   │
    │  │              │                                       │     │   │
    │  │              v                                       │     │   │
    │  │     ┌─────────────────┐                              │     │   │
    │  │     │ RETURN RESULT   │<─────────────────────────────┘     │   │
    │  │     │                 │  (also on max_turns reached)       │   │
    │  │     │ [S#25] event("loop_terminated")                     │   │
    │  │     │        status=completed or max_turns                 │   │
    │  │     │        total_turns, total_tokens, total_cost         │   │
    │  │     │                 │                                    │   │
    │  │     │ LoopResult:     │                                    │   │
    │  │     │  - status       │                                    │   │
    │  │     │  - turns[]      │                                    │   │
    │  │     │  - final_response                                    │   │
    │  │     │  - tokens       │                                    │   │
    │  │     │  - plan         │                                    │   │
    │  │     │  - reflections  │                                    │   │
    │  │     │  - journal      │                                    │   │
    │  │     │  - pending_actions                                   │   │
    │  │     └─────────────────┘                                    │   │
    │  └────────────────────────────────────────────────────────────┘   │
    └──────────────────────────────────────────────────────────────────┘
```

### Why Two Phases?

The two-phase design is a deliberate optimization:

| Aspect | Phase 1 (Reasoning) | Phase 2 (Parameters) |
|--------|--------------------|--------------------|
| **Purpose** | Decide *what* to do | Decide *how* to do it |
| **Model** | Full model (Opus) | Can use smaller model (Sonnet) |
| **Sees tools** | Names + short hints (~350 tokens) | ONE full schema |
| **Output** | JSON with intent string | Native tool_use block |
| **Token cost** | Fixed (no growing conversation) | Minimal (intent + 1 schema) |
| **HiveBoard** | `[S#18]` llm_call with full metadata | `[S#18]` llm_call with tool context |

The key insight: **Phase 1 never sees full tool schemas** (saving ~1500 tokens
per turn), and **Phase 2 never sees the full system prompt** (it only needs the
intent and one schema). This keeps token usage roughly constant per turn
regardless of how many turns have elapsed.

### AtomicState: The Memory Between Turns

Instead of accumulating a growing conversation history (which consumes more
tokens each turn), the loop maintains a compact state dictionary:

```json
{
  "completed_steps": ["Fetched user notifications", "Replied to DM from Alice"],
  "variables": {
    "base_url": "https://mlbackend.net/loop/api/v1",
    "auth_token": "lc_abc123...",
    "notification_count": 5,
    "alice_dm_id": "msg_xyz789"
  },
  "current_step": 2,
  "pending_actions": ["Check task assignments", "Post daily summary"],
  "error_context": null
}
```

This state is:
- **Updated each turn** via `state_update` from Phase 1
- **Emitted to HiveBoard** as `[S#25]` `event("state_mutation")` after each update
- **Injected into Phase 1** prompt as JSON (constant size)
- **Used by Phase 2** for variable resolution (IDs, URLs, tokens)
- **Capped** to prevent unbounded growth (20 steps, 50 variables, 10 actions)

---

## 3. Agent.run() -- The Full Execution Flow

The Agent wraps the loop with pre-processing and post-processing:

```
    agent.run(message, session_id, event_context)
    │
    │  [S#11] The entire run is wrapped in a HiveLoop task context:
    │         _hiveloop_agent.task(task_id, project="loopcolony", type=...)
    │         (agent_manager.py:797-802)
    │         Auto-emits: task_started on entry, task_completed/task_failed on exit
    │
    ├── 1. PRE-PROCESSING
    │   ├── Check for user directives ("remember X", "list memories")
    │   │   └── If pure memory query -> return early
    │   ├── Check for session end command
    │   ├── Load or create session (conversation history)
    │   │   [S#25] event("session_loaded") — session_id + message count
    │   ├── Build skills prompt
    │   │   ├── Human/normal: metadata only (agent must file_read skills)
    │   │   └── Heartbeat/webhook: inline skill content (saves turns)
    │   ├── Build memory prompt (search + relevance boost)
    │   ├── Inject pending TODOs
    │   ├── Inject open issues
    │   ├── Inject loopColony credentials
    │   │   └── Pre-load credentials on colony/CRM/email tools
    │   ├── Build identity block (who am I, my team, my workspace)
    │   └── Build heartbeat context (last N run summaries)
    │
    ├── 2. PRE-LOOP: TODO REVIEW (heartbeat runs only)
    │   └── Short mini-loop (max 10 turns) to clear pending TODOs
    │
    ├── 3. MAIN LOOP EXECUTION
    │   └── AgenticLoop.execute() ──── [See Section 2 above]
    │
    ├── 4. POST-PROCESSING
    │   ├── Save heartbeat summary (cross-run context)
    │   │   [S#18] task.llm_call("heartbeat_summary") — tracked
    │   ├── Update last_sync_at for heartbeat sync
    │   ├── Auto-create TODOs for failed runs
    │   │   [S#24] task.retry() — failed run logged
    │   │   [S#7]  agent.todo(action="created") — retry TODO
    │   ├── Auto-create TODOs from pending_actions
    │   │   [S#7]  agent.todo(action="created") — per action
    │   ├── Collect follow-up events from agent
    │   ├── Check for stuck high-priority TODOs -> escalation event
    │   ├── Scan response for facts to remember (TurnScanner)
    │   ├── Prepend directive acknowledgment
    │   ├── Update session with new messages
    │   │   └── Compact session if over threshold
    │   │       [S#25] event("context_compacted") — tokens before/after,
    │   │              compression_ratio, turns_summarized
    │   │       [S#18] task.llm_call("compaction") — compaction LLM
    │   └── Review session for long-term memories (on session end)
    │
    └── 5. RETURN AgentResult
        │   [S#11] task context exits -> auto-emits task_completed or task_failed
        │          with duration_ms auto-calculated
        │
        ├── status: completed | timeout | max_turns | error
        ├── final_response: text answer
        ├── turns, tools_called, total_tokens, duration_ms
        ├── loop_result: full LoopResult for introspection
        └── pending_events: follow-up events for the runtime
```

---

## 4. Agent Runtime -- Autonomous Lifecycle

The Runtime gives agents autonomous behavior through heartbeat timers
and a priority event queue:

```
    ┌──────────────────────────────────────────────────────────────┐
    │                    AgentRuntime                               │
    │                                                              │
    │  start_agent():                                              │
    │  [S#8]  agent.scheduled(items) — report all scheduled tasks  │
    │  [S#25] event("agent_started") — heartbeat timers, restored  │
    │                                                              │
    │  Daemon Thread (ticks every 1 second)                        │
    │  ┌────────────────────────────────────────────────────────┐  │
    │  │  for each active agent:                                │  │
    │  │                                                        │  │
    │  │  1. HEARTBEAT TIMERS                                   │  │
    │  │     ┌──────────────────────────────────────────────┐   │  │
    │  │     │ Each skill can define a heartbeat interval    │   │  │
    │  │     │ (e.g., "check loopColony every 5 minutes")   │   │  │
    │  │     │                                               │   │  │
    │  │     │ Timer fires -> enqueue LOW priority event     │   │  │
    │  │     │ If agent busy:                                │   │  │
    │  │     │   [S#25] event("heartbeat_skipped")          │   │  │
    │  │     │ If pre-check filters out:                     │   │  │
    │  │     │   [S#25] event("heartbeat_precheck_skipped") │   │  │
    │  │     └──────────────────────────────────────────────┘   │  │
    │  │                                                        │  │
    │  │  2. SCHEDULED TASKS                                    │  │
    │  │     ┌──────────────────────────────────────────────┐   │  │
    │  │     │ Check interval/cron tasks for due execution   │   │  │
    │  │     │                                               │   │  │
    │  │     │ Task due -> enqueue NORMAL priority event     │   │  │
    │  │     └──────────────────────────────────────────────┘   │  │
    │  │                                                        │  │
    │  │  3. EVENT QUEUE (priority-sorted)                      │  │
    │  │     ┌──────────────────────────────────────────────┐   │  │
    │  │     │ Priority.HIGH   = 1  (human messages)        │   │  │
    │  │     │ Priority.NORMAL = 2  (webhooks, tasks)       │   │  │
    │  │     │ Priority.LOW    = 3  (heartbeat ticks)       │   │  │
    │  │     │                                               │   │  │
    │  │     │ On enqueue:                                   │   │  │
    │  │     │   [S#25] event("event_queued") — type, prio  │   │  │
    │  │     │                                               │   │  │
    │  │     │ If agent idle + queue non-empty:              │   │  │
    │  │     │   pop highest-priority event                  │   │  │
    │  │     │   submit to ThreadPoolExecutor (4 workers)    │   │  │
    │  │     │                                               │   │  │
    │  │     │ If event needs approval:                      │   │  │
    │  │     │   [S#22] task.request_approval()             │   │  │
    │  │     │   On approve: [S#23] approval_received()     │   │  │
    │  │     │   On reject:  [S#23] approval_received()     │   │  │
    │  │     └──────────────────────────────────────────────┘   │  │
    │  │                                                        │  │
    │  │  4. HARVEST RESULTS                                    │  │
    │  │     ┌──────────────────────────────────────────────┐   │  │
    │  │     │ If agent's current run is done:               │   │  │
    │  │     │   - Collect pending_events from result        │   │  │
    │  │     │   - Re-enqueue follow-up events               │   │  │
    │  │     │   - Clear current_run state                   │   │  │
    │  │     └──────────────────────────────────────────────┘   │  │
    │  └────────────────────────────────────────────────────────┘  │
    │                                                              │
    │  stop_agent():                                               │
    │  [S#25] event("agent_stopped") — saved/dropped event counts  │
    │                                                              │
    │  Thread Pool: 4 workers (one run per agent at a time)        │
    │  State: persisted to .runtime_state.json for restart         │
    └──────────────────────────────────────────────────────────────┘
```

### Event Lifecycle

```
    External Trigger          Heartbeat Timer           Scheduled Task
    (human msg, webhook)      (every N minutes)         (cron/interval)
         │                         │                         │
         v                         v                         v
    ┌─────────┐              ┌─────────┐              ┌─────────┐
    │  HIGH   │              │   LOW   │              │ NORMAL  │
    │ priority│              │ priority│              │ priority│
    └────┬────┘              └────┬────┘              └────┬────┘
         │                        │                        │
         └────────────┬───────────┴────────────────────────┘
                      v
              ┌──────────────┐
              │ Event Queue  │  Sorted by priority, then FIFO
              │ (max 20)     │  LOW events dropped if queue full
              │              │  [S#25] event("event_queued") on insert
              └──────┬───────┘
                     │ pop highest priority
                     v
              ┌──────────────┐
              │ Agent.run()  │  One at a time per agent
              │              │  [S#11] HiveLoop task wraps entire run
              └──────┬───────┘
                     │
                     v
              ┌──────────────┐
              │ AgentResult  │
              │              │──── pending_events re-enqueued
              └──────────────┘
```

---

## 5. Optional Capabilities

### Planning (planning.py)

Breaks complex tasks into ordered steps. Triggered automatically when
the task has more than ~10 words and contains sequence indicators.

```
    Task: "Build a REST API with JWT auth and user registration"
                              │
                              v
                   ┌─────────────────────┐
                   │  should_plan(task)?  │
                   │  (word count > 10,  │
                   │   has "and"/"then") │
                   └─────────┬───────────┘
                             │ yes
                             v
                   ┌─────────────────────┐
                   │  create_plan(task)   │──── LLM generates steps
                   │  [S#18] llm_call     │     (tracked with metadata:
                   │        ("plan")      │      cache tokens, stop_reason)
                   │  [S#19] task.plan()  │──── plan emitted to HiveBoard
                   │  [S#20] plan_step(0, │     (goal + steps + revision)
                   │    "started")        │
                   └─────────┬───────────┘
                             │
                             v
              Step 1: Set up FastAPI project         [completed]
              Step 2: Implement JWT middleware        [in_progress] <-- current
              Step 3: Add user registration endpoint  [pending]
                             │
                    each turn │
                             v
                   ┌─────────────────────┐
                   │  record_turn()      │
                   │  check_completion() │──── keyword matching on
                   │  advance_plan()     │     completed_steps vs
                   │                     │     step description
                   │  [S#20] plan_step   │
                   │    (i, "completed") │──── step done
                   │  [S#20] plan_step   │
                   │    (i+1, "started") │──── next step
                   └─────────┬───────────┘
                             │
                   stuck too many turns?
                             │ yes
                             v
                   ┌─────────────────────┐
                   │  replan()           │──── Preserve completed,
                   │  [S#18] llm_call    │     regenerate pending
                   │  [S#19] plan(rev+1) │──── revised plan emitted
                   │  [S#20] plan_step   │
                   │    (i, "failed")    │──── stuck step marked
                   │  (max 3 replans)    │
                   └─────────────────────┘

    Injected into Phase 1 prompt as:
    ┌──────────────────────────────────────────────┐
    │ [PLAN CONTEXT]                               │
    │ Task: Build a REST API with JWT auth...      │
    │ Progress: 33% (1/3 steps)                    │
    │ Current Step (2/3): Implement JWT middleware  │
    │ Criteria: JWT validation works, protected... │
    │ Upcoming: Step 3: Add user registration      │
    │ [END PLAN CONTEXT]                           │
    └──────────────────────────────────────────────┘
```

### Reflection (reflection.py)

Self-evaluation that triggers when the agent isn't making progress.
Only fires for runs that had side effects (write/create/delete/send).

```
    Trigger Conditions:
    ├── no_progress_turns: 3 consecutive tool failures
    ├── resource_warning: >80% of max_turns or timeout used
    └── (interval and tool_failure triggers disabled in atomic mode)

                              │ triggered
                              v
                   ┌─────────────────────┐
                   │  reflect()          │──── LLM self-evaluates
                   │  [S#25] event       │     (reflection_started)
                   │    ("reflection_    │
                   │     started")       │
                   │  [S#18] llm_call    │──── tracked with metadata:
                   │    ("reflection")   │     turn_number, trigger,
                   │                     │     cache tokens, stop_reason
                   │  [S#25] event       │
                   │    ("reflection_    │──── (reflection_completed)
                   │     completed")     │     includes decision + assessment
                   └─────────┬───────────┘
                             │
                   ┌─────────┴──────────────────────────┐
                   │                                     │
                   v                                     v
            progress_assessment                    decision
            ├── good                               ├── continue  -> no action
            ├── slow                               ├── adjust    -> inject guidance
            ├── stuck                              ├── pivot     -> replan + guidance
            └── regressing                         ├── escalate  -> abort, notify human
                                                   │    [S#21] task.escalate()
                                                   │    [S#25] event("escalation_context")
                                                   │           reasoning, next_action,
                                                   │           trigger, tools_called
                                                   └── terminate -> abort with reason
```

### Learning (learning.py)

Captures patterns from execution for future reuse:

```
    ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
    │  ErrorPattern    │     │  SuccessPattern  │     │  ToolInsight     │
    │                  │     │                  │     │                  │
    │  error_signature │     │  task_keywords   │     │  common_params   │
    │  tool_name       │     │  approach_summary│     │  common_errors   │
    │  resolution      │     │  key_steps       │     │  best_practices  │
    │  success_rate    │     │  tool_sequence   │     │  success_rate    │
    └────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
             │                        │                         │
             │ [S#25] event           │ [S#25] event            │
             │ ("learning_captured")  │ ("learning_captured")   │
             │ type="error_pattern"   │ type="success_pattern"  │
             │ trigger, category,     │ trigger, category,      │
             │ summary, resolution    │ summary, tools_used     │
             │                        │                         │
             └────────────┬───────────┴─────────────────────────┘
                          v
              learning_store.json (per-agent)
                          │
                          v
              Injected as insights into system prompt
              on future runs with similar tasks
```

---

## 6. System Prompt Composition

The full system prompt is assembled from multiple layers:

```
    ┌──────────────────────────────────────────────────────────────┐
    │                    FULL SYSTEM PROMPT                         │
    │                                                              │
    │  ┌────────────────────────────────────────────────────────┐  │
    │  │ 1. SYSTEM PROMPT (from agent config.json)              │  │
    │  │    "You are a helpful business assistant..."           │  │
    │  └────────────────────────────────────────────────────────┘  │
    │                                                              │
    │  ┌────────────────────────────────────────────────────────┐  │
    │  │ 2. IDENTITY BLOCK (built per-execution)                │  │
    │  │    - Agent ID, name, role, type                        │  │
    │  │    - Credentials (auto-scanned from memory files)      │  │
    │  │    - Capabilities (tools list, skills list, limits)    │  │
    │  │    - Team (other agents: name + role)                  │  │
    │  │    - Workspace (scratch directory contents)            │  │
    │  │    - Current event (source, priority, triggered skills)│  │
    │  └────────────────────────────────────────────────────────┘  │
    │                                                              │
    │  ┌────────────────────────────────────────────────────────┐  │
    │  │ 3. SKILLS PROMPT                                       │  │
    │  │    Normal: metadata + file paths (agent reads on need) │  │
    │  │    Heartbeat: full skill content inlined (~3-4K tokens)│  │
    │  └────────────────────────────────────────────────────────┘  │
    │                                                              │
    │  ┌────────────────────────────────────────────────────────┐  │
    │  │ 4. MEMORY PROMPT                                       │  │
    │  │    - Relevant memories (searched by query, top 5)      │  │
    │  │    - Pending TODOs                                     │  │
    │  │    - Open issues                                       │  │
    │  │    - loopColony credentials (exact values)             │  │
    │  └────────────────────────────────────────────────────────┘  │
    │                                                              │
    │  ┌────────────────────────────────────────────────────────┐  │
    │  │ 5. LEARNING INSIGHTS (if any match current task)       │  │
    │  └────────────────────────────────────────────────────────┘  │
    │                                                              │
    │  Token estimates for each layer are reported to HiveBoard    │
    │  via [S#18] llm_call metadata.prompt_breakdown               │
    │                                                              │
    └──────────────────────────────────────────────────────────────┘

    NOTE: Plan context is injected into the Phase 1 user prompt
    (not the system prompt) so it stays current each turn.
```

---

## 7. Per-Agent Data Isolation

Each agent has its own directory with fully isolated state:

```
    data/AGENTS/{agent_id}/
    │
    ├── config.json                # Model, tools, prompts, limits
    │
    ├── skills/                    # Private skills (override global)
    │   ├── registry.json          # Per-agent skill curation
    │   └── {skill_id}/
    │       ├── skill.json         # Metadata (name, triggers)
    │       └── skill.md           # Instructions (NOT code)
    │
    ├── memory/                    # Sessions + long-term memory
    │   ├── sessions/
    │   │   └── session_{id}.json  # Full conversation history
    │   ├── loopcolony.json        # Integration credentials
    │   ├── topics.json            # Topic registry
    │   ├── index_{topic}.json     # Search index per topic
    │   └── {topic}/
    │       └── content_{id}.json  # Stored knowledge
    │
    ├── credentials.json           # External service API keys
    │
    ├── tasks/                     # Scheduled tasks
    │   ├── {task_id}.json
    │   └── runs/                  # Task execution history
    │
    ├── runs/                      # Execution output (audit trail)
    │   └── {YYYY-MM-DD}/
    │       └── run_{N}/
    │           ├── result.json    # Structured result
    │           └── transcript.md  # Human-readable log
    │
    ├── workspace/                 # Scratch directory
    ├── heartbeat_history.json     # Cross-heartbeat summaries (rolling 50)
    ├── todo.json                  # Per-agent TODO list
    ├── issues.json                # Per-agent issues
    └── learning_store.json        # Captured patterns
```

Skills also exist at the global level, shared by all agents:

```
    data/SKILLS/                   # Global skills
    ├── registry.json              # REQUIRED: skill registration
    └── {skill_id}/
        ├── skill.json             # Metadata
        ├── skill.md               # Main instructions
        └── heartbeat.md           # Heartbeat-specific instructions (optional)

    Resolution order: private skill overrides global with same ID
```

---

## 8. Tool System

All tools inherit from `BaseTool` and are managed by `ToolRegistry`:

```
    ┌─────────────────────────────────────────────────────────┐
    │                    ToolRegistry                          │
    │                                                         │
    │  register(tool)     -> add tool to registry             │
    │  execute(name, params) -> run with timeout (30s)        │
    │  get_schemas()      -> all tools as LLM format          │
    │  get_single_schema() -> one tool (for Phase 2)          │
    │                                                         │
    │  Safety:                                                │
    │  - 30s timeout per tool execution                       │
    │  - 100KB output limit (truncated with notice)           │
    │  - Thread pool: 4 workers                               │
    │  - Error isolation (exceptions -> ToolResult)           │
    │  - Credential pre-injection (overrides LLM values)      │
    │                                                         │
    │  HiveBoard integration (in loop.py):                    │
    │  [S#16] Each tool call wrapped in track_context()       │
    │  [S#14] Payload set with args, result, success, error,  │
    │         duration_ms, result_size_bytes                   │
    │  [S#9]  On failure: report_issue() with auto-category   │
    └─────────────────────────────────────────────────────────┘

    Tool Categories:
    ┌──────────────┬───────────────────────────────────────────┐
    │ Category     │ Tools                                     │
    ├──────────────┼───────────────────────────────────────────┤
    │ File I/O     │ file_read, file_write (sandboxed paths)   │
    │ HTTP         │ http_request, webpage_fetch                │
    │ Tasks        │ schedule_create/list/get/update/delete/    │
    │              │ trigger, schedule_state_set/get,           │
    │              │ schedule_run_list                          │
    │ Feed         │ feed_post                                  │
    │ Events       │ queue_followup_event                       │
    │ Search       │ web_search                                 │
    │ Data Export  │ csv_export, excel_workbook_create           │
    │ Notifications│ send_dm_notification                       │
    │ Images       │ image_generate                             │
    │ Extraction   │ document_extract                           │
    │ Email        │ email_send                                 │
    │ CRM          │ crm_search, crm_write                      │
    │ Tickets      │ support_ticket_create/update               │
    │ Colony       │ workspace_read, workspace_write             │
    │ Compute      │ data_aggregate, math_eval                   │
    │ Personal     │ todo_add/list/complete/remove,              │
    │              │ report_issue                               │
    │              │ [S#7]  todo tools emit agent.todo()        │
    │              │ [S#9]  issue tool emits report_issue()     │
    └──────────────┴───────────────────────────────────────────┘
```

---

## 9. Memory System

Two distinct subsystems for different retention needs:

```
    ┌──────────────────────────────────────────────────────────────┐
    │                 SESSIONS (Short-Term)                         │
    │                                                              │
    │  Purpose: Conversation persistence within a session          │
    │  Storage: memory/sessions/session_{id}.json                  │
    │  Content: Full message history (user + assistant + tools)    │
    │  Lifecycle: active -> idle -> completed                      │
    │  Compaction: Summarized when exceeding turn threshold         │
    │    [S#25] event("context_compacted") — tokens before/after   │
    │    [S#18] llm_call("compaction") — compaction LLM tracked    │
    │  Cleanup: Completed sessions beyond 20 are auto-deleted      │
    │  [S#25] event("session_loaded") on session restore           │
    └──────────────────────────────────────────────────────────────┘

    ┌──────────────────────────────────────────────────────────────┐
    │              LONG-TERM MEMORY (Topic-Based)                   │
    │                                                              │
    │  Purpose: Persistent knowledge across sessions               │
    │  Storage: memory/{topic}/content_{id}.json                   │
    │  Indexing: memory/index_{topic}.json (searchable)            │
    │  Sources:                                                    │
    │    - User directives ("remember that Alice prefers email")   │
    │    - TurnScanner (auto-extracts facts from responses)        │
    │    - SessionEndReviewer (extracts memories on session close)  │
    │  Decay: Time-based relevance decay with access boost         │
    │  Limits: 100 MB per agent, 10 MB per session                 │
    └──────────────────────────────────────────────────────────────┘

    Memory Decision Flow:
    ┌──────────┐     ┌──────────────┐     ┌──────────────┐
    │  User    │     │ TurnScanner  │     │ SessionEnd   │
    │ Directive│     │ (per-turn)   │     │ Reviewer     │
    │"remember"│     │ auto-extract │     │ (on close)   │
    └────┬─────┘     └──────┬───────┘     └──────┬───────┘
         │                  │                     │
         └──────────┬───────┴─────────────────────┘
                    v
         ┌──────────────────┐
         │  MemoryManager   │
         │  add_memory()    │
         │  search_memory() │
         │  boost_on_access │
         └──────────────────┘
```

---

## 10. Safeguards & Exit Conditions

The loop has multiple layers of protection:

```
    Exit Condition          Trigger                         HiveBoard Sensor
    ─────────────────────── ─────────────────────────────── ──────────────────
    Task completed          Phase 1 returns done=true       [S#25] loop_terminated
    Timeout                 Elapsed > timeout_seconds       [S#25] loop_terminated
    Max turns               Turn count > max_turns          [S#25] loop_terminated
    Cancellation            cancel_check() returns True     (implicit via task exit)
    Loop detected           Same tool call repeated 3x,     [S#9]  report_issue
                            or sequence repeated 3x         [S#25] cycle_detected
    Escalation              Reflection decides "escalate"   [S#21] task.escalate()
                                                            [S#25] escalation_context
    Error                   Phase 1 LLM call fails          (implicit via task exit)

    Post-Exit Safety:                                       HiveBoard Sensor
    ├── Failed runs -> auto-create high-priority TODO       [S#7]  agent.todo()
    │                                                       [S#24] task.retry()
    ├── Remaining pending_actions -> auto-create TODOs      [S#7]  agent.todo()
    ├── Stuck high-priority TODOs -> queue escalation event
    └── All runs persisted to runs/ for audit trail
```

---

## 11. Data Flow: A Complete Heartbeat Run

End-to-end trace of a typical autonomous heartbeat execution, with
HiveBoard sensors marked at each step:

```
    1. Runtime daemon tick (every 1s)
       │  [S#4] heartbeat_payload callback returns agent health metrics
       │  [S#6] queue_provider callback returns queue state snapshot
       │
       ├── SkillTimer fires (e.g., "loopcolony" skill, every 5 min)
       │
       v
    2. Enqueue AgentEvent(priority=LOW, source="heartbeat",
       │                  skills=["loopcolony"])
       │  [S#25] event("event_queued") — type, priority
       │
       v
    3. Agent is idle -> pop event -> submit to thread pool
       │
       v
    4. Agent.run(message="heartbeat prompt from skill",
       │         event_context={source: "heartbeat", skills: ["loopcolony"]})
       │
       │  [S#11] HiveLoop task context opened (task_started auto-emitted)
       │  [S#25] event("session_loaded") — session restored
       │
       ├── Build skills prompt (INLINE mode — full skill.md content)
       ├── Load heartbeat context (last 3 run summaries)
       ├── Inject credentials, TODOs, issues
       │
       ├── PRE-LOOP: Review pending TODOs (max 10 turns)
       │
       v
    5. AgenticLoop.execute()
       │
       ├── Turn 1: [S#18] llm_call("phase1_reasoning") — tracked
       │           Phase 1 -> "check workspace for new messages"
       │           [S#18] llm_call("phase2_tool") — tracked
       │           Phase 2 -> workspace_read(action="sync")
       │           [S#16] track_context("workspace_read") wraps execution
       │           [S#14] set_payload({args, result, success, duration_ms})
       │           Result  -> 2 new DMs found
       │           [S#25] event("state_mutation") — state updated
       │           [S#25] event("turn_completed") — turn metrics
       │
       ├── Turn 2: Phase 1 -> "read DM from Alice"
       │           Phase 2 -> workspace_read(action="message", id="msg_123")
       │           Result  -> "Please send the Q4 report"
       │           (same sensor pattern: S#18 x2, S#16, S#14, S#25 x2)
       │
       ├── Turn 3: Phase 1 -> "search CRM for Q4 data"
       │           Phase 2 -> crm_search(entity="analytics", filters={...})
       │           Result  -> revenue data retrieved
       │
       ├── Turn 4: Phase 1 -> "create spreadsheet with Q4 data"
       │           Phase 2 -> excel_workbook_create(...)
       │           Result  -> /workspace/q4_report.xlsx created
       │
       ├── Turn 5: Phase 1 -> "reply to Alice with the report"
       │           Phase 2 -> workspace_write(action="send_message", ...)
       │           Result  -> message sent
       │
       └── Turn 6: Phase 1 -> done=true, response_text="Completed..."
       │           [S#25] event("loop_terminated") — status=completed
       │
       v
    6. Post-processing
       ├── Save heartbeat summary (6 turns, tools used, status)
       │   [S#18] llm_call("heartbeat_summary") — tracked
       ├── Update last_sync_at
       ├── Scan response for facts to remember
       └── Save run to runs/2026-02-15/run_003/
       │
       │  [S#11] HiveLoop task context exits (task_completed auto-emitted)
       │
       v
    7. Runtime harvests result
       ├── Collect pending_events (if any)
       └── Agent returns to idle state, ready for next event
```

**Per-turn HiveBoard data volume**: Each turn emits ~6 events to HiveBoard
(2x llm_call, 1x track_context with payload, 1x state_mutation, 1x turn_completed,
plus conditional events like context_pressure or report_issue). A 6-turn
heartbeat run emits ~40 events total (including task lifecycle + loop_terminated).

---

## 12. Key Design Decisions Summary

| Decision | Rationale |
|----------|-----------|
| **Two-phase LLM calls** | Constant token cost per turn; Phase 2 can use cheaper model |
| **AtomicState instead of conversation history** | No growing context; state is compact JSON |
| **Tool results as user messages** | Better LLM reasoning about tool outputs |
| **Skills as markdown (not code)** | Agents follow instructions, not execute code |
| **Per-agent isolation** | No cross-agent data leakage; independent failure domains |
| **Credential pre-injection on tools** | Prevents LLM from hallucinating auth values |
| **Learning only on side-effect runs** | Read-only runs have nothing meaningful to learn from |
| **Reflection only on side-effect runs** | Low-stakes reads don't need self-evaluation |
| **Priority event queue** | Human messages always jump ahead of heartbeats |
| **Heartbeat context injection** | Cross-run continuity without growing state |
| **Auto-TODO on failure** | Failed work automatically retried next heartbeat |
| **Contextvar-based observability** | HiveBoard plumbing via `get_current_task()` without threading handles through every function |

---

## 13. HiveBoard Sensor Coverage Map

Complete mapping of all 28 HiveBoard SDK sensors to their loopCore integration points.

### Coverage Summary

```
    28 SDK sensors total
    ├── 21 ACTIVE  (integrated in loopCore)
    ├──  4 N/A     (not applicable to loopCore's architecture)
    └──  3 NOT YET (available in SDK, not yet wired)
```

### Sensor-by-Sensor Status

| # | Sensor | SDK Function | Status | loopCore Location |
|---|--------|-------------|--------|-------------------|
| 1 | SDK Init | `hiveloop.init()` | ACTIVE | `api/app.py:4006` — called once at app startup |
| 2 | Agent Registration | `hb.agent()` | ACTIVE | `agent_manager.py:628` — per agent with type/version/framework |
| 3 | Agent Lookup | `hb.get_agent()` | ACTIVE | `observability.py` — via `get_hiveloop_agent()` contextvar helper |
| 4 | Heartbeat Payload | `heartbeat_payload` callback | ACTIVE | `agent_manager.py:609` — returns uptime, runs, tokens, cost, errors |
| 5 | Queue State (explicit) | `agent.queue_snapshot()` | NOT YET | Could be called on queue changes; currently only via `queue_provider` |
| 6 | Queue State (auto) | `queue_provider` callback | ACTIVE | `agent_manager.py:569` — returns depth, priorities, top events |
| 7 | Todo Lifecycle | `agent.todo()` | ACTIVE | `agent.py:906,953` (auto-TODOs) + `tools/todo_tools.py` (create/complete/dismiss) |
| 8 | Scheduled Work | `agent.scheduled()` | ACTIVE | `runtime.py:387` — reports all scheduled tasks on agent start |
| 9 | Issue Reported | `agent.report_issue()` | ACTIVE | `loop.py:880` (cycle), `loop.py:1684` (tool error, auto-categorized), `tools/issue_tools.py` |
| 10 | Issue Resolved | `agent.resolve_issue()` | ACTIVE | `api/app.py:723` — when issue closed via API |
| 11 | Task (context mgr) | `agent.task()` | ACTIVE | `agent_manager.py:797` — wraps every `run_agent()` call |
| 12 | Task (manual) | `agent.start_task()` | N/A | Not needed; all runs use context manager pattern |
| 13 | Task Termination | `task.complete()`/`fail()` | N/A | Handled automatically by context manager `__exit__()` |
| 14 | Task Payload | `task.set_payload()` | ACTIVE | `loop.py:1664` — tool tracking payloads (args, result, success, duration) |
| 15 | Action (decorator) | `@agent.track()` | NOT YET | Could decorate tool functions; currently using track_context instead |
| 16 | Action (context mgr) | `agent.track_context()` | ACTIVE | `loop.py:1645` — wraps every tool execution |
| 17 | Tool Payload | `tool_payload()` | NOT YET | Payload built inline; could use SDK helper for standardization |
| 18 | LLM Call | `task.llm_call()` | ACTIVE | `loop.py:1378,1613` (Phase 1/2), `agent.py:215` (heartbeat), `compaction.py:353`, `reflection.py:394`, `planning.py:583` |
| 19 | Plan Created | `task.plan()` | ACTIVE | `planning.py:615` (initial), `planning.py:976` (revision) |
| 20 | Plan Step | `task.plan_step()` | ACTIVE | `planning.py:622,819,839,862,988` — started/completed/failed per step |
| 21 | Escalation | `task.escalate()` | ACTIVE | `loop.py:970` — on reflection "escalate" decision |
| 22 | Approval Request | `task.request_approval()` | ACTIVE | `runtime.py:1277` — events requiring human approval |
| 23 | Approval Response | `task.approval_received()` | ACTIVE | `runtime.py:1318,1345` — approve/reject decisions |
| 24 | Retry | `task.retry()` | ACTIVE | `agent.py:897` (failed run), `loop.py:1561` (Phase 2 parse failure) |
| 25 | Custom Event | `task.event()`/`agent.event()` | ACTIVE | 21 event types across 7 files (see breakdown below) |
| 26 | Log Bridge | `HiveBoardLogHandler` | NOT YET | Centralized logging exists (`logging_config.py`) but not bridged to HiveBoard |
| 27 | Flush | `hiveloop.flush()` | N/A | SDK auto-flushes; explicit flush not needed in long-running server |
| 28 | Shutdown | `hiveloop.shutdown()` | N/A | SDK registers atexit handler; explicit shutdown not needed |

### Custom Events Breakdown (Sensor #25)

The `task.event()` / `agent.event()` escape hatch is used extensively
for domain-specific telemetry:

| Custom Event | File | Purpose |
|-------------|------|---------|
| `config_snapshot` | `agent_manager.py` | Agent configuration captured on registration |
| `session_loaded` | `agent.py` | Session restored with ID + message count |
| `cycle_detected` | `loop.py` | Infinite loop pattern found |
| `escalation_context` | `loop.py` | Full reasoning behind escalation |
| `context_pressure` | `loop.py` | Context window >80% utilization warning |
| `loop_terminated` | `loop.py` | Loop exit (completed or max_turns) with totals |
| `parse_error` | `loop.py` | Phase 2 failed to produce tool_use block |
| `error_context_set` | `loop.py` | AtomicState.error_context changed |
| `state_mutation` | `loop.py` | AtomicState updated after each turn |
| `turn_completed` | `loop.py` | Per-turn metrics (tokens, cost, duration, tool) |
| `reflection_started` | `reflection.py` | Reflection LLM call initiated |
| `reflection_completed` | `reflection.py` | Reflection decision + assessment |
| `learning_captured` | `learning.py` | Error or success pattern learned |
| `context_compacted` | `compaction.py` | Session compressed (tokens before/after, ratio) |
| `agent_started` | `runtime.py` | Runtime agent activated with timers |
| `agent_stopped` | `runtime.py` | Runtime agent deactivated with event counts |
| `event_queued` | `runtime.py` | Event inserted into runtime queue |
| `heartbeat_skipped` | `runtime.py` | Heartbeat skipped (agent busy) |
| `heartbeat_precheck_skipped` | `runtime.py` | Heartbeat filtered by pre-check |

### Integration Architecture

```
    ┌────────────────────────────────────────────────────────────────────┐
    │                   HiveBoard Observability Pipeline                  │
    │                                                                    │
    │  observability.py                                                  │
    │  ├── _current_task (ContextVar)      <- set by agent_manager.py   │
    │  ├── _current_hiveloop_agent (ContextVar)  <- set by agent_mgr    │
    │  ├── get_current_task()              <- used by loop.py, agent.py,│
    │  │                                      planning.py, reflection.py│
    │  │                                      learning.py, compaction.py│
    │  │                                      tools/todo_tools.py,      │
    │  │                                      tools/issue_tools.py      │
    │  ├── get_hiveloop_agent()            <- used by loop.py, agent.py,│
    │  │                                      runtime.py, tools/*.py    │
    │  └── estimate_cost()                 <- 6 Anthropic models        │
    │                                                                    │
    │  All HiveLoop calls are wrapped in try-except for graceful        │
    │  degradation — if HiveBoard is unavailable, agents continue       │
    │  running without observability.                                    │
    └────────────────────────────────────────────────────────────────────┘
```

### Files with HiveBoard Integration

| File | Sensors Used | Integration Count |
|------|-------------|-------------------|
| `agent_manager.py` | S#1, S#2, S#4, S#6, S#11, S#25 | 8 call sites |
| `loop.py` | S#9, S#14, S#16, S#18, S#21, S#24, S#25 | 15 call sites |
| `planning.py` | S#18, S#19, S#20 | 8 call sites |
| `reflection.py` | S#18, S#25 | 3 call sites |
| `runtime.py` | S#8, S#22, S#23, S#25 | 9 call sites |
| `agent.py` | S#7, S#18, S#24, S#25 | 5 call sites |
| `learning.py` | S#25 | 2 call sites |
| `context/compaction.py` | S#18, S#25 | 2 call sites |
| `tools/todo_tools.py` | S#7 | 3 call sites |
| `tools/issue_tools.py` | S#9 | 2 call sites |
| `api/app.py` | S#1, S#10 | 2 call sites |
| **Total** | **21 of 28 sensors** | **59 call sites** |

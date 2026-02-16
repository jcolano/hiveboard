# loopCore Architecture Guide

A visual guide to how autonomous agents work in the loopCore framework.

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
    │  │              │  │                                      │   │   │
    │  │              │  │ - Loop detection (abort if stuck)    │   │   │
    │  │              │  │ - Execute via ToolRegistry           │   │   │
    │  │              │  │ - 30s timeout, 100KB output limit    │   │   │
    │  │              │  │ - Track skill file reads             │   │   │
    │  │              │  │ - Learn from result (success/error)  │   │   │
    │  │              │  │ - Update AtomicState error_context   │   │   │
    │  │              │  └──────────────────┬───────────────────┘   │   │
    │  │              │                     │                       │   │
    │  │              │                     v                       │   │
    │  │              │  ┌──────────────────────────────────────┐   │   │
    │  │              │  │ POST-TURN CHECKS                     │   │   │
    │  │              │  │                                      │   │   │
    │  │              │  │ - Planning: check step completion,   │   │   │
    │  │              │  │   advance plan, replan if stuck      │   │   │
    │  │              │  │ - Reflection: self-evaluate if       │   │   │
    │  │              │  │   triggered (→ continue/adjust/      │   │   │
    │  │              │  │   pivot/escalate/terminate)          │   │   │
    │  │              │  │ - Turn callback (notify caller)      │   │   │
    │  │              │  └──────────────────┬───────────────────┘   │   │
    │  │              │                     │                       │   │
    │  │              │                     └──── next turn ──┐     │   │
    │  │              │                                       │     │   │
    │  │              v                                       │     │   │
    │  │     ┌─────────────────┐                              │     │   │
    │  │     │ RETURN RESULT   │◄─────────────────────────────┘     │   │
    │  │     │                 │  (also on max_turns reached)       │   │
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
- **Injected into Phase 1** prompt as JSON (constant size)
- **Used by Phase 2** for variable resolution (IDs, URLs, tokens)
- **Capped** to prevent unbounded growth (20 steps, 50 variables, 10 actions)

---

## 3. Agent.run() — The Full Execution Flow

The Agent wraps the loop with pre-processing and post-processing:

```
    agent.run(message, session_id, event_context)
    │
    ├── 1. PRE-PROCESSING
    │   ├── Check for user directives ("remember X", "list memories")
    │   │   └── If pure memory query → return early
    │   ├── Check for session end command
    │   ├── Load or create session (conversation history)
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
    │   ├── Update last_sync_at for heartbeat sync
    │   ├── Auto-create TODOs for failed runs
    │   ├── Auto-create TODOs from pending_actions
    │   ├── Collect follow-up events from agent
    │   ├── Check for stuck high-priority TODOs → escalation event
    │   ├── Scan response for facts to remember (TurnScanner)
    │   ├── Prepend directive acknowledgment
    │   ├── Update session with new messages
    │   │   └── Compact session if over threshold
    │   └── Review session for long-term memories (on session end)
    │
    └── 5. RETURN AgentResult
        ├── status: completed | timeout | max_turns | error
        ├── final_response: text answer
        ├── turns, tools_called, total_tokens, duration_ms
        ├── loop_result: full LoopResult for introspection
        └── pending_events: follow-up events for the runtime
```

---

## 4. Agent Runtime — Autonomous Lifecycle

The Runtime gives agents autonomous behavior through heartbeat timers
and a priority event queue:

```
    ┌──────────────────────────────────────────────────────────────┐
    │                    AgentRuntime                               │
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
    │  │     │ Timer fires → enqueue LOW priority event      │   │  │
    │  │     └──────────────────────────────────────────────┘   │  │
    │  │                                                        │  │
    │  │  2. SCHEDULED TASKS                                    │  │
    │  │     ┌──────────────────────────────────────────────┐   │  │
    │  │     │ Check interval/cron tasks for due execution   │   │  │
    │  │     │                                               │   │  │
    │  │     │ Task due → enqueue NORMAL priority event      │   │  │
    │  │     └──────────────────────────────────────────────┘   │  │
    │  │                                                        │  │
    │  │  3. EVENT QUEUE (priority-sorted)                      │  │
    │  │     ┌──────────────────────────────────────────────┐   │  │
    │  │     │ Priority.HIGH   = 1  (human messages)        │   │  │
    │  │     │ Priority.NORMAL = 2  (webhooks, tasks)       │   │  │
    │  │     │ Priority.LOW    = 3  (heartbeat ticks)       │   │  │
    │  │     │                                               │   │  │
    │  │     │ If agent idle + queue non-empty:              │   │  │
    │  │     │   pop highest-priority event                  │   │  │
    │  │     │   submit to ThreadPoolExecutor (4 workers)    │   │  │
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
              └──────┬───────┘
                     │ pop highest priority
                     v
              ┌──────────────┐
              │ Agent.run()  │  One at a time per agent
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
                   └─────────┬───────────┘
                             │
                             v
              Step 1: Set up FastAPI project         [completed]
              Step 2: Implement JWT middleware        [in_progress] ◄── current
              Step 3: Add user registration endpoint  [pending]
                             │
                    each turn │
                             v
                   ┌─────────────────────┐
                   │  record_turn()      │
                   │  check_completion() │──── keyword matching on
                   │  advance_plan()     │     completed_steps vs
                   └─────────┬───────────┘     step description
                             │
                   stuck too many turns?
                             │ yes
                             v
                   ┌─────────────────────┐
                   │  replan()           │──── Preserve completed,
                   │                     │     regenerate pending
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
                   └─────────┬───────────┘
                             │
                   ┌─────────┴──────────────────────────┐
                   │                                     │
                   v                                     v
            progress_assessment                    decision
            ├── good                               ├── continue  → no action
            ├── slow                               ├── adjust    → inject guidance
            ├── stuck                              ├── pivot     → replan + guidance
            └── regressing                         ├── escalate  → abort, notify human
                                                   └── terminate → abort with reason
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
    │  register(tool)     → add tool to registry              │
    │  execute(name, params) → run with timeout (30s)         │
    │  get_schemas()      → all tools as LLM format           │
    │  get_single_schema() → one tool (for Phase 2)           │
    │                                                         │
    │  Safety:                                                │
    │  - 30s timeout per tool execution                       │
    │  - 100KB output limit (truncated with notice)           │
    │  - Thread pool: 4 workers                               │
    │  - Error isolation (exceptions → ToolResult)            │
    │  - Credential pre-injection (overrides LLM values)      │
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
    │  Lifecycle: active → idle → completed                        │
    │  Compaction: Summarized when exceeding turn threshold         │
    │  Cleanup: Completed sessions beyond 20 are auto-deleted      │
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
    Exit Condition          Trigger                         Status
    ─────────────────────── ─────────────────────────────── ──────────────
    Task completed          Phase 1 returns done=true       "completed"
    Timeout                 Elapsed > timeout_seconds       "timeout"
    Max turns               Turn count > max_turns          "max_turns"
    Cancellation            cancel_check() returns True     "cancelled"
    Loop detected           Same tool call repeated 3x,     "loop_detected"
                            or sequence repeated 3x
    Escalation              Reflection decides "escalate"   "escalation_needed"
    Error                   Phase 1 LLM call fails          "error"

    Post-Exit Safety:
    ├── Failed runs → auto-create high-priority TODO for retry
    ├── Remaining pending_actions → auto-create TODOs
    ├── Stuck high-priority TODOs → queue escalation event
    └── All runs persisted to runs/ for audit trail
```

---

## 11. Data Flow: A Complete Heartbeat Run

End-to-end trace of a typical autonomous heartbeat execution:

```
    1. Runtime daemon tick (every 1s)
       │
       ├── SkillTimer fires (e.g., "loopcolony" skill, every 5 min)
       │
       v
    2. Enqueue AgentEvent(priority=LOW, source="heartbeat",
       │                  skills=["loopcolony"])
       │
       v
    3. Agent is idle → pop event → submit to thread pool
       │
       v
    4. Agent.run(message="heartbeat prompt from skill",
       │         event_context={source: "heartbeat", skills: ["loopcolony"]})
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
       ├── Turn 1: Phase 1 → "check workspace for new messages"
       │           Phase 2 → workspace_read(action="sync")
       │           Result  → 2 new DMs found
       │
       ├── Turn 2: Phase 1 → "read DM from Alice"
       │           Phase 2 → workspace_read(action="message", id="msg_123")
       │           Result  → "Please send the Q4 report"
       │
       ├── Turn 3: Phase 1 → "search CRM for Q4 data"
       │           Phase 2 → crm_search(entity="analytics", filters={...})
       │           Result  → revenue data retrieved
       │
       ├── Turn 4: Phase 1 → "create spreadsheet with Q4 data"
       │           Phase 2 → excel_workbook_create(...)
       │           Result  → /workspace/q4_report.xlsx created
       │
       ├── Turn 5: Phase 1 → "reply to Alice with the report"
       │           Phase 2 → workspace_write(action="send_message", ...)
       │           Result  → message sent
       │
       └── Turn 6: Phase 1 → done=true, response_text="Completed..."
       │
       v
    6. Post-processing
       ├── Save heartbeat summary (6 turns, tools used, status)
       ├── Update last_sync_at
       ├── Scan response for facts to remember
       └── Save run to runs/2026-02-15/run_003/
       │
       v
    7. Runtime harvests result
       ├── Collect pending_events (if any)
       └── Agent returns to idle state, ready for next event
```

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

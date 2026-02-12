# OBSERVABILITY -- Comprehensive loopCore Agent Monitoring Reference

Everything that is observable about a loopCore agent: what data is collected, where it is stored, how to access it via API, and how the admin panel renders it.

---

## 1. Overview

loopCore exposes three pillars of observability, each surfaced as a sub-tab in the admin panel's Agents view:

| Pillar | Purpose | Lifecycle | Storage |
|--------|---------|-----------|---------|
| **Runs** | Historical execution records | Immutable after creation | Disk (`data/AGENTS/{id}/runs/`) |
| **Sessions** | Conversation persistence | Mutable (append, trim, complete) | Disk (`data/AGENTS/{id}/sessions/`) |
| **Runtime** | Real-time monitoring | Transient (in-memory) | RAM (queue saved on graceful stop) |

**How they relate:** A **Runtime** event (heartbeat tick, webhook, human message) triggers a **Run** (the agentic loop executing turns). That run may load and update a **Session** (conversation context). After completion, the run is persisted to disk as an immutable audit record, the session is updated with new messages, and the runtime records the event in its history.

---

## 2. RUNS -- Historical Execution Records

Every time an agent executes (via API, heartbeat, webhook, or scheduled task), a run record is created and persisted to disk.

### 2.1 Data Structures

#### `TokenUsage` (loop.py)
| Field | Type | Description |
|-------|------|-------------|
| `input_tokens` | int | Tokens sent to LLM |
| `output_tokens` | int | Tokens received from LLM |
| `total` | property | `input_tokens + output_tokens` |

#### `ToolCallRecord` (loop.py)
| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique tool call ID |
| `name` | str | Tool name (e.g. `crm_search`) |
| `parameters` | Dict | Parameters passed to tool |
| `result` | ToolResult | Execution result (success, output, error) |

#### `Turn` (loop.py)
| Field | Type | Description |
|-------|------|-------------|
| `number` | int | Turn index (1-based) |
| `timestamp` | str | ISO 8601 UTC timestamp |
| `llm_text` | str | LLM response text (step_summary or response_text) |
| `tool_calls` | List[ToolCallRecord] | Tools executed this turn |
| `tokens_used` | TokenUsage | Tokens for this turn (Phase 1 + Phase 2) |
| `duration_ms` | int | Wall-clock time for this turn |
| `plan_step_index` | Optional[int] | Current plan step (if planning enabled) |
| `plan_step_description` | Optional[str] | Description of the plan step |

#### `TurnExchange` (loop.py)
Compact record for intra-heartbeat context (shown to subsequent turns within the same run).

| Field | Type | Description |
|-------|------|-------------|
| `turn` | int | Turn number |
| `tool` | str | Tool name |
| `intent` | str | What the tool was asked to do (max 200 chars) |
| `result_preview` | str | Truncated result (max 1200 chars) |
| `success` | bool | Whether the tool call succeeded |

#### `AtomicState` (loop.py)
Compact state dict that replaces full conversation history in the two-phase loop.

| Field | Type | Limits | Description |
|-------|------|--------|-------------|
| `completed_steps` | List[str] | max 20 | What the agent has accomplished |
| `variables` | Dict[str, Any] | max 50 | Named values (IDs, URLs, tokens) |
| `pending_actions` | List[str] | max 10 | Remaining work items |
| `current_step` | int | -- | Current plan step index |
| `error_context` | Optional[str] | -- | Last error or reflection guidance |

#### `LoopResult` (loop.py)
The complete result of an agentic loop execution.

| Field | Type | Description |
|-------|------|-------------|
| `status` | str | One of 7 statuses (see below) |
| `turns` | List[Turn] | All turns executed |
| `final_response` | Optional[str] | Agent's final text response |
| `error` | Optional[str] | Error message if failed |
| `total_duration_ms` | int | Wall-clock execution time |
| `total_tokens` | TokenUsage | Aggregate token usage |
| `tools_called` | List[str] | Unique tool names used |
| `skill_files_read` | List[str] | Skill files accessed during run |
| `reflections` | List[ReflectionResult] | Reflection decisions made |
| `plan` | Optional[Dict] | Plan data (if planning was used) |
| `learning_stats` | Optional[Dict] | Learning statistics |
| `execution_trace` | List[Dict] | Structured trace events (see 2.2) |
| `journal` | List[Dict] | Flight recorder entries (see 2.3) |
| `pending_actions` | List[str] | Actions the agent couldn't finish |

**LoopResult statuses** (7 types):
`completed`, `timeout`, `max_turns`, `error`, `loop_detected`, `escalation_needed`, `cancelled`

#### `RunOutput` (output/manager.py)
The persisted form of a run, saved as `result.json`.

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | str | e.g. `run_001` (sequential per day) |
| `agent_id` | str | Agent that executed |
| `session_id` | str | Session used |
| `timestamp` | str | ISO 8601 creation time |
| `status` | str | LoopResult status |
| `message` | str | Input message that triggered the run |
| `response` | Optional[str] | Final response text |
| `turns` | int | Number of turns |
| `tools_called` | List[str] | Tools used |
| `total_tokens` | int | Total tokens consumed |
| `duration_ms` | int | Execution time |
| `error` | Optional[str] | Error if any |
| `conversation` | List[Dict] | Full message array |
| `execution_trace` | List[Dict] | Trace events |
| `plan` | Optional[Dict] | Plan data |
| `reflections` | List[Dict] | Reflection decisions |
| `turn_details` | List[Dict] | Per-turn summaries |
| `step_stats` | List[Dict] | Per-plan-step aggregated stats |

#### `HeartbeatSummary` (agent.py)
Cross-heartbeat context record, summarized by Haiku and persisted.

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | str | When the heartbeat ran |
| `skills_triggered` | List[str] | Skills that fired |
| `turn_count` | int | Number of turns in the run |
| `status` | str | LoopResult status |
| `summary_lines` | List[str] | One-line-per-turn summaries from Haiku |
| `total_tokens` | int | Tokens consumed |

### 2.2 Execution Trace Events

The `execution_trace` is a list of structured events emitted during execution. Each entry:

```json
{
  "event": "<event_type>",
  "timestamp": "2026-02-11T12:00:00Z",
  "turn": 3,
  "step_index": 1,
  "detail": "descriptive text"
}
```

**Event types** (6 types):

| Event | When emitted | `step_index` | `detail` |
|-------|-------------|--------------|----------|
| `plan_created` | After planning creates a plan | -- | `"{N} steps"` |
| `step_started` | When a plan step begins | Yes | Step description |
| `step_completed` | When a plan step is done | Yes | Completion summary |
| `replan` | Plan is modified mid-execution | Yes | Reason for replan |
| `reflection_triggered` | After reflection runs | -- | Decision + reasoning |
| `tool_result` | (via journal, not trace) | -- | -- |

### 2.3 Journal Entries (Flight Recorder)

The `journal` is a detailed flight recorder, persisted as `journal.jsonl` (one JSON object per line). Five entry types:

#### `phase1_decision`
```json
{
  "event": "phase1_decision",
  "turn": 1,
  "timestamp": "...",
  "done": false,
  "tool": "crm_search",
  "intent": "Search for contacts in workspace",
  "step_summary": "Looking up CRM contacts",
  "response_text": null,
  "state_update": {"variables": {"contact_id": "abc"}, "completed_steps": ["Found contacts"]},
  "tokens": {"input": 1500, "output": 200}
}
```

#### `tool_result`
```json
{
  "event": "tool_result",
  "turn": 1,
  "timestamp": "...",
  "tool": "crm_search",
  "success": true,
  "error": null,
  "output_preview": "Found 3 contacts...",
  "parameters": {"entity": "contacts", "filters": {}},
  "tokens": {"input": 800, "output": 150}
}
```

#### `early_exit`
```json
{
  "event": "early_exit",
  "turn": 3,
  "timestamp": "...",
  "done": true,
  "tool_name": null,
  "response_text": "Task completed. Found 3 contacts."
}
```

#### `loop_exit`
```json
{
  "event": "loop_exit",
  "turn": 3,
  "timestamp": "...",
  "status": "completed",
  "total_tokens": 5200,
  "duration_s": 12.3
}
```

#### `phase2_failure`
```json
{
  "event": "phase2_failure",
  "turn": 2,
  "timestamp": "...",
  "tool": "crm_write",
  "intent": "Create a new contact",
  "had_response": true
}
```

### 2.4 Step Stats

`LoopResult.get_step_stats()` lazily computes per-plan-step aggregated statistics:

```json
{
  "step_index": 0,
  "turns": 3,
  "input_tokens": 4500,
  "output_tokens": 600,
  "total_tokens": 5100,
  "started_at": "2026-02-11T12:00:00Z",
  "completed_at": "2026-02-11T12:00:15Z",
  "description": "Search CRM for active deals"
}
```

### 2.5 Storage

```
data/AGENTS/{agent_id}/
├── runs/
│   └── {YYYY-MM-DD}/
│       ├── run_001/
│       │   ├── result.json        # Full RunOutput (3-8 KB typical)
│       │   ├── transcript.md      # Human-readable markdown
│       │   └── journal.jsonl      # Flight recorder (one JSON per line)
│       ├── run_002/
│       └── ...
└── heartbeat_history.json         # Last 50 HeartbeatSummary entries
```

**Retention:** Max 50 runs per agent (`OutputManager.MAX_RUNS_PER_AGENT`). After each save, oldest runs are deleted. Empty date folders are cleaned up.

**Nothing breaks if runs are deleted.** No agent logic depends on runs. The admin UI loses visible history and debugging data.

### 2.6 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/runs` | List runs (all agents), filter by `agent_id`, `date`, `limit` |
| `GET` | `/agents/{id}/runs` | List runs for specific agent |
| `GET` | `/runs/{agent_id}/{date}/{run_id}` | Get full run details (result.json) |
| `GET` | `/agents/{id}/runs/{date}/{run_id}` | Same, agent-scoped path |
| `GET` | `/runs/{agent_id}/{date}/{run_id}/transcript` | Get markdown transcript |
| `GET` | `/agents/{id}/runs/{date}/{run_id}/transcript` | Same, agent-scoped path |
| `GET` | `/agents/{id}/heartbeat-history` | Last N heartbeat summaries |

### 2.7 Frontend (Runs Tab)

- **Runs table:** Columns: run_id, timestamp, status (badge), turns, duration_ms, tokens
- **Click a row** opens a detail modal with: message, response, execution trace, plan, reflections, turn details, step stats, journal
- **Status badges:** Color-coded by status (green=completed, red=error, yellow=timeout/max_turns)

---

## 3. SESSIONS -- Conversation Persistence

Sessions store the full conversation history for an agent's work. When an agent runs, it loads the session to get context of prior messages, and appends new messages after each turn.

### 3.1 Data Structures

#### `Session` (memory/manager.py)

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | str | Unique identifier |
| `agent_id` | str | Owning agent |
| `created_at` | str | ISO 8601 creation time |
| `updated_at` | str | ISO 8601 last update time |
| `status` | str | `active`, `paused`, or `completed` |
| `metadata` | Dict | Key-value metadata |
| `conversation` | List[Dict] | Array of `{role, content}` messages |
| `summary` | Optional[str] | Session summary (if compacted) |
| `token_count` | int | Cumulative token count |

#### `SessionInfo` (api/app.py -- API response model)

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | str | Session ID |
| `agent_id` | str | Owning agent |
| `status` | str | Current status |
| `created_at` | str | Creation timestamp |
| `updated_at` | Optional[str] | Last update |
| `message_count` | int | Number of messages in conversation |

### 3.2 Lifecycle

1. **Create** -- `agent.run()` creates a session when called with a `session_id` (or auto-generated)
2. **Load** -- At execution start, session conversation is loaded for context
3. **Append** -- After each run, user message + assistant response are appended
4. **Trim** -- When conversation exceeds 50 turns, `ContextManager.compact()` summarizes older messages
5. **Complete** -- On session end command, status transitions to `completed`
6. **Cleanup** -- When a session transitions to completed/paused, old completed/paused sessions beyond `MAX_COMPLETED_SESSIONS` (20) are deleted, oldest first. Active sessions are never touched.

### 3.3 Session Isolation

Sessions are isolated by `session_key`:

| Key Pattern | Used For |
|-------------|----------|
| `None` (main) | Human chat, heartbeats |
| `task_{id}` | Scheduled task execution |
| `event_{id}` | Agent-created follow-up events |
| webhook `sessionKey` | Webhook-triggered runs |

### 3.4 Limits

| Limit | Value | Enforced By |
|-------|-------|-------------|
| Max session size | 10 MB (`DEFAULT_MAX_SESSION_SIZE_MB`) | `MemoryManager.save_session()` |
| Max memory per agent | 100 MB (`DEFAULT_MAX_MEMORY_MB`) | `MemoryManager._check_memory_limit()` |
| Max completed sessions | 20 (`MAX_COMPLETED_SESSIONS`) | `_cleanup_old_sessions()` |
| Session trim threshold | 50 turns | `agent.py` (configurable via `session_max_turns`) |

### 3.5 Storage

```
data/AGENTS/{agent_id}/sessions/
├── session_{session_id_1}.json
├── session_{session_id_2}.json
└── ...
```

Each file is a JSON serialization of the `Session` dataclass. Typical size: 3-6 KB per session (varies with conversation length).

**Dependency analysis:**
- **Active sessions: CRITICAL.** Deleting an active session breaks the agent's ongoing conversation.
- **Completed sessions: Safe to delete.** No code references them after completion.

### 3.6 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/agents/{id}/sessions` | List sessions for an agent |
| `GET` | `/agents/{id}/sessions/{sid}` | Get session details + conversation |
| `DELETE` | `/agents/{id}/sessions/{sid}` | Delete a session |
| `GET` | `/sessions` | List all sessions (global) |
| `GET` | `/sessions/{sid}` | Get session details (global) |
| `DELETE` | `/sessions/{sid}` | Delete session (global) |

### 3.7 Frontend (Sessions Tab)

- **Sessions table:** Columns: session_id, status (badge), created_at, message_count
- **Actions:** View (loads conversation), Delete
- **Chat panel:** Shows `session_id`, displays conversation messages, persists across API calls
- **Status badges:** `active` (green), `paused` (yellow), `completed` (gray)

---

## 4. RUNTIME -- Real-Time Monitoring

The Runtime manages agent lifecycle: start/stop, heartbeat timers, scheduled tasks, and a priority event queue. All state is transient (in-memory) with selective persistence.

### 4.1 Data Structures

#### `Priority` (runtime.py)
```python
class Priority(IntEnum):
    HIGH   = 1   # Human messages -- user is waiting
    NORMAL = 2   # Webhooks, scheduled tasks
    LOW    = 3   # Heartbeat ticks
```

#### `AgentEvent` (runtime.py)

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | str | `evt_{12-char hex}` (auto-generated) |
| `priority` | Priority | HIGH=1, NORMAL=2, LOW=3 |
| `timestamp` | datetime | When created |
| `message` | str | Event message/prompt |
| `session_key` | Optional[str] | Session isolation key |
| `source` | str | Event source (see 4.5) |
| `routing` | Any | OutputRouteConfig for response delivery |
| `title` | Optional[str] | Human-readable title |
| `context` | Optional[Dict] | Credentials, IDs, URLs for execution |
| `skill_id` | Optional[str] | Skill to activate |
| `status` | str | `pending_approval`, `active`, `running`, `completed`, `dropped` |
| `created_by` | str | `system`, `agent`, or `human` |

#### `SkillTimer` (runtime.py)

| Field | Type | Description |
|-------|------|-------------|
| `skill_name` | str | Name of the skill |
| `interval_minutes` | int | Fire interval |
| `prompt` | str | Heartbeat prompt text |
| `ticks_per_fire` | int | `interval_minutes / base_tick_minutes` |

#### `AgentState` (runtime.py)
Per-agent runtime state. **Transient -- never fully persisted.**

| Field | Type | Description |
|-------|------|-------------|
| `agent_id` | str | Agent identifier |
| `active` | bool | Whether agent is started |
| `queue` | List[AgentEvent] | Priority-sorted event queue (max 20) |
| `pending_events` | List[AgentEvent] | Events awaiting human approval |
| `skill_timers` | Dict[str, SkillTimer] | Heartbeat timer configs |
| `base_tick_minutes` | int | GCD of all timer intervals |
| `tick_count` | int | Total ticks since start |
| `last_tick_time` | Optional[datetime] | When last tick occurred |
| `current_run` | Optional[Future] | Currently executing run |
| `current_event` | Optional[AgentEvent] | Event being processed |
| `heartbeat_md` | str | HEARTBEAT.md content |
| `started_at` | Optional[datetime] | When agent was started |
| `scheduled_tasks` | Dict[str, dict] | Loaded task definitions |
| `last_task_reload` | Optional[datetime] | Last task refresh time |

**Metrics** (reset on start):

| Metric | Type | Description |
|--------|------|-------------|
| `heartbeats_fired` | int | Total heartbeats enqueued |
| `heartbeats_skipped` | int | Heartbeats skipped (busy or pre-check) |
| `events_processed` | int | Successfully completed events |
| `events_failed` | int | Failed events |
| `webhooks_received` | int | Webhook events received |
| `total_run_duration_ms` | int | Cumulative run duration |

**Event history:** Last 50 completed events (rolling buffer).

#### Event History Entry

Each completed event is recorded in `state.event_history`:

| Field | Type | Description |
|-------|------|-------------|
| `event_id` | str | Event identifier |
| `source` | str | Event source |
| `priority` | str | Priority name (HIGH/NORMAL/LOW) |
| `status` | str | `completed` or `failed` |
| `queued_at` | str | ISO timestamp when queued |
| `completed_at` | str | ISO timestamp when finished |
| `duration_ms` | int | Total elapsed time |
| `message` | str | Full event message |
| `response` | str | Agent's response text |
| `error` | Optional[str] | Error if failed |
| `turns` | int | Number of turns executed |
| `tokens` | int | Total tokens used |
| `title` | Optional[str] | Event title |
| `skill_id` | Optional[str] | Skill used |
| `session_key` | Optional[str] | Session key |
| `created_by` | str | Who created the event |
| `has_routing` | bool | Whether response routing was configured |

### 4.2 Issues (Persistent)

Agents report problems they cannot solve via the `report_issue` tool. Issues are persisted to disk and displayed in the admin panel.

**Storage:** `data/AGENTS/{agent_id}/issues.json`

#### Issue Schema

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | `iss_001`, `iss_002`, ... |
| `title` | str | Short summary (max 200 chars) |
| `description` | str | Full details |
| `severity` | str | `critical`, `high`, `medium`, `low` |
| `category` | str | `error`, `functional`, `technical`, `permissions`, `config`, `other` |
| `context` | Dict | Structured context (tool name, error code, entity ID) |
| `created_at` | str | ISO timestamp |
| `status` | str | `open` or `dismissed` |
| `occurrence_count` | int | Deduplicated count (same title = increment) |
| `last_occurrence_at` | str | Last occurrence timestamp |
| `dismissed_at` | Optional[str] | When dismissed by human |
| `todo_on_dismiss` | Optional[str] | Auto-created TODO text on dismissal |

### 4.3 TODO List (Persistent)

Agents have a per-agent TODO list. Items are auto-created from failed runs, remaining `pending_actions`, and issue dismissals. Agents review pending TODOs at heartbeat start.

**Storage:** `data/AGENTS/{agent_id}/todo.json`

### 4.4 Queue Mechanics

- **Insertion:** `bisect.insort()` maintains priority order (HIGH first, then NORMAL, then LOW; FIFO within same priority)
- **Overflow:** When queue reaches `MAX_QUEUE_DEPTH` (20), oldest LOW-priority items are dropped first. If no LOW items, the last (lowest-priority, oldest) item is dropped.
- **Heartbeat drop:** If agent is busy (queue non-empty or currently running), heartbeat ticks are silently dropped (`heartbeats_skipped` metric incremented)
- **Queue persistence:** On graceful stop (if `persist_queue_on_stop` is enabled), queue is saved to `.saved_queue.json`. Restored on next start.

### 4.5 Event Sources

| Source Pattern | Origin |
|----------------|--------|
| `heartbeat` | Scheduled heartbeat tick |
| `heartbeat:manual` | Manually triggered via API |
| `task:{task_id}` | Scheduled task due |
| `webhook:{name}` | External webhook call |
| `human` | Human message via API |
| `agent:{agent_id}` | Agent-created follow-up event |

### 4.6 Heartbeat Mechanics

1. **GCD-based ticking:** `base_tick_minutes` = GCD of all skill timer intervals. Each skill's `ticks_per_fire` = `interval_minutes / base_tick_minutes`.
2. **Tick check:** Every 1s, runtime checks if `elapsed_minutes >= base_tick_minutes` since `last_tick_time`.
3. **Due skills:** Skills where `tick_count % ticks_per_fire == 0`.
4. **Pre-check optimization:** Before firing, each due skill's `pre_check` (HTTP GET + skip_if condition) is evaluated. Skills with nothing to do are filtered out.
5. **Busy drop:** If agent has queued events or a running task, heartbeat is skipped entirely.
6. **Next tick countdown:** `next_tick_at = last_tick_time + base_tick_minutes` (shown in admin panel).

### 4.7 Scheduled Tasks

Tasks loaded from `data/AGENTS/{id}/tasks/{task_id}/task.json`. Reloaded every 10 seconds.

**Schedule types:** `interval` (interval_seconds), `cron` (not implemented in runtime), `once` (disabled after first run), `event_only` (no automatic trigger).

### 4.8 Storage Summary

| What | Path | Persistence |
|------|------|-------------|
| AgentState | In-memory | Transient (lost on restart) |
| Event queue backup | `.saved_queue.json` | Saved on graceful stop, deleted after restore |
| Runtime state | `.runtime_state.json` | Active agents list (for auto-restore within 10 min) |
| Runtime heartbeat | `.runtime_heartbeat` | Liveness file (written every 1s, deleted on stop) |
| Issues | `issues.json` | Persistent |
| TODO list | `todo.json` | Persistent |
| Heartbeat history | `heartbeat_history.json` | Persistent (max 50 entries) |

### 4.9 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/agents/{id}/start` | Start agent (activate heartbeats, tasks, queue) |
| `POST` | `/agents/{id}/stop` | Stop agent (deactivate, clear queue) |
| `POST` | `/agents/{id}/reset` | Full reset: stop + clear TODO, issues, queue, heartbeat history |
| `GET` | `/agents/{id}/runtime-status` | Get runtime status (active, queue, timers, metrics) |
| `GET` | `/agents/{id}/queue` | Get queue contents + current_event |
| `GET` | `/agents/{id}/events/history` | Last N completed events (default 20) |
| `GET` | `/agents/{id}/events/pending` | Events awaiting human approval |
| `GET` | `/agents/{id}/events/{eid}` | Full detail for a single event |
| `POST` | `/agents/{id}/events/{eid}/approve` | Move pending event to active queue |
| `POST` | `/agents/{id}/events/{eid}/drop` | Drop a pending event |
| `POST` | `/agents/{id}/heartbeat-interval` | Update skill heartbeat interval (persists) |
| `POST` | `/agents/{id}/trigger-heartbeat` | Manually trigger heartbeat (all skills) |
| `GET` | `/agents/{id}/todo` | Get TODO list |
| `DELETE` | `/agents/{id}/todo` | Clear completed TODOs |
| `GET` | `/agents/{id}/issues` | Get issues list |
| `POST` | `/agents/{id}/issues/{iid}/dismiss` | Dismiss issue (auto-creates TODO if configured) |
| `GET` | `/agents/{id}/heartbeat-history` | Get heartbeat summaries |
| `GET` | `/api/runtime/status` | Global runtime status (running, active agents, queue totals) |

### 4.10 Frontend (Runtime Tab)

Auto-refreshes every 3 seconds while visible.

**Status Bar:**
- Active/Stopped indicator
- Queue depth, pending count, tick count
- Currently processing event (spinner + elapsed time)

**Next Tick Countdown:**
- Live countdown timer to next heartbeat tick

**Heartbeat Timers:**
- Per-skill interval display with editable interval field
- "Update" button persists new interval to disk

**Metrics Panel:**
- 6 counters: heartbeats_fired, heartbeats_skipped, events_processed, events_failed, webhooks_received, total_run_duration_ms

**Pending Events:**
- Cards for each pending event with Approve/Drop buttons
- Shows: title, message preview, priority, skill_id, created_by

**Currently Processing:**
- Spinner + event details when agent is running
- Shows: source, priority, title, message preview, elapsed time

**Event Queue Table:**
- Columns: event_id, priority, source, title, message_preview, status

**Event History Table:**
- Last 20 completed events (most recent first)
- Columns: event_id, source, priority, status, duration_ms, turns, tokens, title
- Click for full detail modal (message, response, error)

**Issues Section:**
- Severity-colored badges (critical=red, high=orange, medium=yellow, low=gray)
- Clickable to open issue detail modal
- Dismiss button with optional TODO auto-creation
- Shows: occurrence count, last_occurrence_at, category

**TODO List:**
- Pending items + last 10 completed items
- Shows: task text, priority, context, created_at

**Heartbeat History Table:**
- Last 20 heartbeat summaries
- Columns: timestamp, skills_triggered, turn_count, status, total_tokens
- Expandable summary_lines

---

## 5. Cross-Cutting: How a DM Flows Through All Three Pillars

Tracing a single DM from reception to completion:

```
1. WEBHOOK ARRIVAL
   POST /hooks/wake/{agent_id}
   Body: {"message": "Hey, can you check the pipeline?", "sessionKey": "dm_123"}
   -> runtime.push_event() creates AgentEvent:
      priority=NORMAL, source="webhook:wake", session_key="dm_123"
      status increments: webhooks_received += 1

2. QUEUE INSERTION (Runtime)
   -> bisect.insort(state.queue, event)
   -> Queue depth visible in admin panel

3. DISPATCH (Runtime main loop, 1s poll)
   -> Agent is idle (current_run is None)
   -> Event popped from queue, set as current_event
   -> Submitted to ThreadPoolExecutor

4. EXECUTION (Agentic Loop)
   -> Session loaded: sessions/session_dm_123.json
   -> Skills prompt built (heartbeat skills inlined)
   -> AgenticLoop.execute() begins:
      Turn 1: Phase 1 chooses crm_search -> Phase 2 generates params -> tool executes
      Turn 2: Phase 1 decides done=true -> returns final response
   -> LoopResult created with status="completed", 2 turns, execution_trace, journal

5. RUN SAVED (Output Manager)
   -> runs/2026-02-11/run_003/result.json written
   -> runs/2026-02-11/run_003/transcript.md written
   -> runs/2026-02-11/run_003/journal.jsonl written
   -> Cleanup: if >50 runs, oldest deleted

6. SESSION UPDATED (Memory Manager)
   -> User message + assistant response appended to conversation
   -> Session saved: sessions/session_dm_123.json
   -> If >50 turns, context compacted

7. RESULT HARVESTED (Runtime main loop)
   -> current_run.done() == True
   -> Event history entry recorded:
      {event_id, source, status: "completed", duration_ms, turns, tokens, ...}
   -> Metrics: events_processed += 1, total_run_duration_ms += elapsed
   -> current_run cleared, current_event cleared
   -> If routing configured, response delivered via router

8. OBSERVABLE STATE
   Runs tab:    New row in runs table (run_003, completed, 2 turns)
   Sessions tab: session_dm_123 updated (message_count increased)
   Runtime tab:  Event in history, metrics updated, queue empty
```

---

## 6. Quick Reference Tables

### 6.1 All Observability API Endpoints

| Category | Method | Path | Description |
|----------|--------|------|-------------|
| **Runs** | `GET` | `/runs` | List all runs |
| | `GET` | `/agents/{id}/runs` | List agent runs |
| | `GET` | `/runs/{id}/{date}/{run_id}` | Get run details |
| | `GET` | `/agents/{id}/runs/{date}/{run_id}` | Get run details (scoped) |
| | `GET` | `/runs/{id}/{date}/{run_id}/transcript` | Get transcript |
| | `GET` | `/agents/{id}/runs/{date}/{run_id}/transcript` | Get transcript (scoped) |
| | `GET` | `/agents/{id}/heartbeat-history` | Heartbeat summaries |
| **Sessions** | `GET` | `/sessions` | List all sessions |
| | `GET` | `/sessions/{sid}` | Get session |
| | `DELETE` | `/sessions/{sid}` | Delete session |
| | `GET` | `/agents/{id}/sessions` | List agent sessions |
| | `GET` | `/agents/{id}/sessions/{sid}` | Get agent session |
| | `DELETE` | `/agents/{id}/sessions/{sid}` | Delete agent session |
| **Runtime** | `POST` | `/agents/{id}/start` | Start agent |
| | `POST` | `/agents/{id}/stop` | Stop agent |
| | `POST` | `/agents/{id}/reset` | Full reset |
| | `GET` | `/agents/{id}/runtime-status` | Runtime status |
| | `GET` | `/agents/{id}/queue` | Queue contents |
| | `GET` | `/agents/{id}/events/history` | Event history |
| | `GET` | `/agents/{id}/events/pending` | Pending events |
| | `GET` | `/agents/{id}/events/{eid}` | Event detail |
| | `POST` | `/agents/{id}/events/{eid}/approve` | Approve event |
| | `POST` | `/agents/{id}/events/{eid}/drop` | Drop event |
| | `POST` | `/agents/{id}/heartbeat-interval` | Update interval |
| | `POST` | `/agents/{id}/trigger-heartbeat` | Manual heartbeat |
| | `GET` | `/agents/{id}/todo` | TODO list |
| | `DELETE` | `/agents/{id}/todo` | Clear completed TODOs |
| | `GET` | `/agents/{id}/issues` | Issues list |
| | `POST` | `/agents/{id}/issues/{iid}/dismiss` | Dismiss issue |
| | `GET` | `/api/runtime/status` | Global runtime status |

### 6.2 All Data Structures

| Structure | Source File | Persisted | Purpose |
|-----------|------------|-----------|---------|
| `TokenUsage` | loop.py | Via RunOutput | Token tracking |
| `ToolCallRecord` | loop.py | Via RunOutput | Tool call audit |
| `Turn` | loop.py | Via RunOutput | Per-turn record |
| `TurnExchange` | loop.py | No | Intra-heartbeat context |
| `AtomicState` | loop.py | No | Two-phase loop state |
| `LoopResult` | loop.py | Via RunOutput | Execution result |
| `RunOutput` | output/manager.py | Yes (result.json) | Immutable run record |
| `HeartbeatSummary` | agent.py | Yes (heartbeat_history.json) | Cross-heartbeat context |
| `Session` | memory/manager.py | Yes (session_*.json) | Conversation persistence |
| `Priority` | runtime.py | No | Event priority enum |
| `AgentEvent` | runtime.py | Queue only (.saved_queue.json) | Queued work item |
| `SkillTimer` | runtime.py | No | Heartbeat timer config |
| `AgentState` | runtime.py | No (transient) | Per-agent runtime state |
| Issue (dict) | issue_tools.py | Yes (issues.json) | Reported problems |
| TODO (dict) | todo_tools.py | Yes (todo.json) | Agent work items |

### 6.3 All Storage Paths

| Path | Content | Lifecycle |
|------|---------|-----------|
| `data/AGENTS/{id}/runs/{date}/run_{NNN}/result.json` | Full run output | Immutable, max 50 per agent |
| `data/AGENTS/{id}/runs/{date}/run_{NNN}/transcript.md` | Human-readable transcript | Immutable |
| `data/AGENTS/{id}/runs/{date}/run_{NNN}/journal.jsonl` | Flight recorder | Immutable |
| `data/AGENTS/{id}/sessions/session_{sid}.json` | Conversation history | Mutable (append, trim) |
| `data/AGENTS/{id}/heartbeat_history.json` | Heartbeat summaries | Append-only, max 50 |
| `data/AGENTS/{id}/issues.json` | Reported issues | Mutable (add, dismiss) |
| `data/AGENTS/{id}/todo.json` | TODO items | Mutable (add, complete, remove) |
| `data/AGENTS/{id}/.saved_queue.json` | Queue backup on stop | Temporary (deleted after restore) |
| `data/AGENTS/.runtime_state.json` | Active agents list | Updated on start/stop |
| `data/AGENTS/.runtime_heartbeat` | Liveness file | Written every 1s, deleted on stop |

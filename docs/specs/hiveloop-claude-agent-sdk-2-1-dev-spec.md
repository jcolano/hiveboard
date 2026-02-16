# HiveLoop × Claude Agent SDK — Dev Team Implementation Spec

**Date:** 2026-02-16  
**Status:** Ready for implementation  
**Prerequisite:** Working HiveLoop SDK, HiveBoard backend running  
**Target file:** `src/hiveloop/integrations/claude_agent_sdk.py`

---

## 1. What We're Building

A single-file integration module that takes the Claude Agent SDK's hook system and auto-wires it to HiveLoop's existing 28 sensors. The developer adds one function call and gets full HiveBoard observability.

**Developer-facing API:**

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks

async for message in query(
    prompt="Fix the bug in auth.py",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash"],
        hooks=hiveloop_hooks(api_key="hb_live_xxx", project="my-project"),
    ),
):
    print(message)
```

**What this produces on HiveBoard:** Agent card with heartbeat, task timeline with tool action nodes, activity stream, and (where possible) cost data.

---

## 2. Architecture Overview

```
Claude Agent SDK                    HiveLoop Integration Layer              HiveLoop SDK
─────────────────                   ──────────────────────────              ────────────
                                    ┌─────────────────────────┐
SessionStart ──────────────────────>│ on_session_start()       │──> hb.agent()        → agent_registered
                                    │                          │──> agent.start_task() → task_started
                                    │                          │──> _start heartbeat   → heartbeat (auto)
                                    └─────────────────────────┘

PreToolUse (Read/Edit/Bash/...) ───>│ on_pre_tool_use()        │──> agent.track_context().__enter__()
                                    │                          │                        → action_started
                                    └─────────────────────────┘

PostToolUse ───────────────────────>│ on_post_tool_use()       │──> ctx.set_payload()
                                    │                          │──> ctx.__exit__()      → action_completed
                                    └─────────────────────────┘                          / action_failed

PreToolUse (Task) ─────────────────>│ on_pre_tool_use()        │──> hb.agent(subagent)  → agent_registered
                                    │  [subagent detection]     │──> sub.start_task()    → task_started
                                    │                          │──> parent.event()      → custom (delegation)
                                    └─────────────────────────┘

Stop ──────────────────────────────>│ on_stop()                │──> task.event(result)  → custom (agent_result)
                                    │                          │──> task.complete()      → task_completed
                                    └─────────────────────────┘

SessionEnd ────────────────────────>│ on_session_end()         │──> task.complete/fail() → task_completed/failed
                                    │                          │──> clear context
                                    └─────────────────────────┘

Message stream (usage metadata) ───>│ _intercept_messages()    │──> task.llm_call()     → custom (llm_call)
                                    └─────────────────────────┘
```

---

## 3. Sensor Activation Matrix

Which of our 28 sensors get activated by this integration, and how.

### 3.1 Automatically Activated (no developer effort)

| # | Sensor | SDK Function | Activated By | Notes |
|---|--------|-------------|-------------|-------|
| 1 | SDK Init | `hiveloop.init()` | `hiveloop_hooks()` factory | Once per factory call |
| 2 | Agent Registration | `hb.agent()` | `SessionStart` hook | Idempotent — safe for resumed sessions |
| 4 | Custom State | `heartbeat_payload` callback | Auto-registered | Reports session count, active tools |
| 11 | Task (context mgr) | `agent.task()` | `SessionStart` hook | session_id → task_id |
| 13 | Task Termination | `task.complete()`/`task.fail()` | `Stop` / `SessionEnd` hook | Based on final message state |
| 15 | Action (decorator) | `@agent.track()` | N/A — we use track_context instead | — |
| 16 | Action (context mgr) | `agent.track_context()` | `PreToolUse`/`PostToolUse` hooks | One per tool call |
| 25 | Custom Event | `task.event()` | `Stop` hook (result), subagent delegation | For agent_result + delegation kinds |
| 27 | Flush | `hiveloop.flush()` | `SessionEnd` hook | Ensure delivery before session ends |
| 28 | Shutdown | `hiveloop.shutdown()` | Not auto — developer's responsibility | Document in usage guide |

### 3.2 Activated with Message Stream Interception (Phase 2)

| # | Sensor | SDK Function | Activated By | Notes |
|---|--------|-------------|-------------|-------|
| 18 | LLM Call | `task.llm_call()` | Message stream parsing | IF the SDK exposes usage metadata in yielded messages |

### 3.3 Activated via Subagent Detection

| # | Sensor | SDK Function | Activated By | Notes |
|---|--------|-------------|-------------|-------|
| 2 | Agent Registration | `hb.agent()` | `PreToolUse` when tool_name == "Task" | Creates child agent |
| 11 | Task Start | `agent.start_task()` | `PreToolUse` when tool_name == "Task" | Creates child task |
| 21 | Escalation | `task.escalate()` | `PreToolUse` when tool_name == "Task" | Maps delegation → escalation |

### 3.4 Not Activated (developer must add manually if needed)

| # | Sensor | Why Not Auto | Developer Can Add |
|---|--------|-------------|-------------------|
| 5 | Queue Snapshot (explicit) | Agent SDK has no queue concept | Via `agent.queue_snapshot()` in custom hooks |
| 6 | Queue Provider (auto) | No queue to poll | Via `queue_provider` callback |
| 7 | Todo Lifecycle | Agent SDK doesn't expose work items | Via `agent.todo()` |
| 8 | Scheduled Work | No scheduling in Agent SDK | Via `agent.scheduled()` |
| 9 | Issue Reported | No error pattern detection | Via `agent.report_issue()` |
| 10 | Issue Resolved | No issue tracking | Via `agent.resolve_issue()` |
| 17 | Tool Payload | We use track_context + set_payload instead | Already covered by action tracking |
| 19 | Plan Created | Agent SDK doesn't expose agent reasoning | Via `task.plan()` if parsing messages |
| 20 | Plan Step | Same as above | Via `task.plan_step()` |
| 22 | Approval Request | Partially covered via UserPromptSubmit | Via `task.request_approval()` |
| 23 | Approval Response | Partially covered via UserPromptSubmit | Via `task.approval_received()` |
| 24 | Retry | Agent SDK handles retries internally | Via `task.retry()` |
| 26 | Log Bridge | Not framework-specific | Developer adds `HiveBoardLogHandler` to their loggers |

### 3.5 Coverage Summary

| Category | Total Sensors | Auto-Activated | Phase 2 | Manual Only |
|----------|--------------|---------------|---------|-------------|
| Identity (Group 1) | 3 | **3** (init, register, lookup) | 0 | 0 |
| State (Group 2) | 7 | **1** (heartbeat_payload) | 0 | 6 |
| Activity (Group 3) | 18 | **7** (task, actions, custom, flush) | 1 (LLM call) | 10 |
| **Total** | **28** | **11 (39%)** | **1 (4%)** | **16 (57%)** |

**11 auto-activated sensors cover the 3 highest-impact sensor groups** (Registration, Task lifecycle, Action lifecycle) that together feed 20+ API endpoints and power Fleet, Timeline, Tasks, Activity Stream, and real-time WebSocket updates.

---

## 4. Dashboard Impact Analysis

What the developer sees on HiveBoard with just `hiveloop_hooks()` — no additional instrumentation.

### 4.1 Fully Powered Screens (Phase 1)

| Screen | Status | Key Sensors Active |
|--------|--------|-------------------|
| **Fleet Dashboard** (`/v1/agents`) | ✅ Full | Registration, Heartbeat, Task lifecycle |
| **Agent Detail** (`/v1/agents/{id}`) | ✅ Full | Registration, Heartbeat, Task lifecycle |
| **Task List** (`/v1/tasks`) | ✅ Full | Task Start/Complete/Fail, Action counts |
| **Task Timeline** (`/v1/tasks/{id}/timeline`) | ✅ Core | Actions (tool calls), task boundary, errors |
| **Activity Stream** (`/v1/events`) | ✅ Full | All events flow here |
| **WebSocket Live** (`/v1/stream`) | ✅ Full | All events, status changes, stuck detection |

### 4.2 Partially Powered Screens (Phase 1)

| Screen | Status | What's Missing | Needs |
|--------|--------|---------------|-------|
| **Task Timeline** | ⚠️ No cost overlay | LLM Call sensor | Phase 2 (message parsing) |
| **Task Timeline** | ⚠️ No plan progress | Plan sensors | Message parsing or manual |
| **Pipeline View** (`/v1/agents/{id}/pipeline`) | ⚠️ Empty | Queue, Todo, Scheduled, Issues | Manual instrumentation |

### 4.3 Not Powered Until Phase 2+

| Screen | Status | Blocker |
|--------|--------|---------|
| **Cost Explorer** (`/v1/cost/*`) | ❌ | Requires LLM Call sensor |
| **Model Insights** (`/v1/insights/models`) | ❌ | Requires LLM Call sensor |
| **Prompt Insights** (`/v1/insights/prompts`) | ❌ | Requires LLM Call sensor |
| **Error Insights** (`/v1/insights/errors`) | ⚠️ Partial | Has task/action failures, missing issue categories |

---

## 5. Implementation — Module Structure

### 5.1 File Layout

```
src/hiveloop/integrations/
├── __init__.py
├── base.py                      # existing FrameworkIntegration base
├── langchain.py                 # existing
├── crewai.py                    # existing
├── autogen.py                   # existing
└── claude_agent_sdk.py          # ← NEW FILE (this spec)
```

### 5.2 Internal State

The integration needs to track state across hook callbacks:

```python
import contextvars
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# Thread-safe state for tracking active tool contexts
_active_tools: dict[str, dict] = {}  # tool_use_id → {track_ctx, start_time, tool_name}
_active_tools_lock = threading.Lock()

# Context vars for task/agent propagation (same pattern as existing integrations)
_current_task = contextvars.ContextVar('hiveloop_casdk_task', default=None)
_current_agent = contextvars.ContextVar('hiveloop_casdk_agent', default=None)
_hb_client = contextvars.ContextVar('hiveloop_casdk_hb', default=None)

# Subagent tracking
_subagent_tasks: dict[str, dict] = {}  # tool_use_id → {agent_handle, task}
_subagent_lock = threading.Lock()
```

---

## 6. Implementation — Hook Callbacks

### 6.1 `on_session_start`

**Trigger:** Agent SDK fires `SessionStart` when `query()` begins.  
**Sensors activated:** `hb.agent()` → `agent_registered` + heartbeat, `agent.start_task()` → `task_started`

```python
async def on_session_start(input_data: dict, tool_use_id: str, context: dict) -> dict:
    """
    SessionStart hook.
    
    input_data expected keys:
      - session_id: str — unique session identifier
      
    Sensors triggered:
      - agent_registered (via hb.agent(), idempotent)
      - heartbeat (auto-starts background thread)
      - task_started (via agent.start_task())
    
    Dashboard impact:
      - Agent appears in Fleet View with green heartbeat
      - Task row created in Task List
      - Activity Stream shows task_started event
      - WebSocket pushes agent.status_changed → "processing"
    """
    hb = _hb_client.get()
    if not hb:
        return {}

    session_id = input_data.get("session_id", f"session-{uuid.uuid4().hex[:8]}")

    # Sensor #2: Agent Registration (idempotent)
    agent_handle = hb.agent(
        agent_id=_config.agent_name,
        type=_config.agent_type,
        version=_config.agent_version,
        framework="claude-agent-sdk",
        heartbeat_interval=_config.heartbeat_interval,
        stuck_threshold=_config.stuck_threshold,
        heartbeat_payload=_make_heartbeat_payload(),  # Sensor #4
    )
    _current_agent.set(agent_handle)

    # Sensor #12: Task Start (manual start for more control)
    task = agent_handle.start_task(
        task_id=session_id,
        project=_config.project,
        type="agent-sdk-session",
    )
    _current_task.set(task)

    return {}  # Don't modify SDK behavior
```

**Key decisions:**
- Use `start_task()` (manual) not `task()` (context manager) because the task spans multiple hook calls, not a single `with` block.
- `hb.agent()` is idempotent so repeated `query()` calls with the same agent name don't create duplicates.
- Session ID becomes task ID — natural 1:1 mapping.

---

### 6.2 `on_pre_tool_use`

**Trigger:** Agent SDK fires `PreToolUse` before every tool call (Read, Edit, Bash, Grep, Glob, WebSearch, WebFetch, Task, AskUserQuestion).  
**Sensors activated:** `agent.track_context()` → `action_started`, plus special handling for `Task` (subagent) and `AskUserQuestion` (approval).

```python
async def on_pre_tool_use(input_data: dict, tool_use_id: str, context: dict) -> dict:
    """
    PreToolUse hook.
    
    input_data expected keys:
      - tool_name: str — "Read", "Edit", "Bash", "Task", etc.
      - tool_input: dict — tool-specific parameters
      
    Sensors triggered (standard tools):
      - action_started (via agent.track_context().__enter__())
    
    Sensors triggered (Task tool — subagent):
      - agent_registered (new subagent)
      - task_started (subagent task)
      - custom event kind=delegation (on parent task)
      
    Sensors triggered (AskUserQuestion):
      - approval_requested (via task.request_approval())
    
    Dashboard impact:
      - Timeline: new action node appears
      - Activity Stream: action_started event
      - WebSocket: event.new push
      - Fleet View: subagent card appears (if Task tool)
    """
    agent = _current_agent.get()
    task = _current_task.get()
    if not agent:
        return {}

    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})

    # ── BRANCH: Subagent spawn via Task tool ──
    if tool_name == "Task" and _config.track_subagents:
        subagent_name = tool_input.get("agent", "unknown-subagent")
        
        # Sensor #2: Register subagent
        sub_agent = _hb_client.get().agent(
            agent_id=f"{_config.agent_name}:sub:{subagent_name}",
            type="claude-agent-sdk-subagent",
            framework="claude-agent-sdk",
            heartbeat_interval=_config.heartbeat_interval,
        )
        
        # Sensor #12: Start subagent task
        sub_task = sub_agent.start_task(
            task_id=f"sub-{tool_use_id}",
            project=_config.project,
            type="subagent-execution",
        )
        
        with _subagent_lock:
            _subagent_tasks[tool_use_id] = {
                "agent": sub_agent,
                "task": sub_task,
                "start_time": time.perf_counter(),
            }
        
        # Sensor #25: Custom event — delegation
        if task:
            task.event("custom", payload={
                "kind": "delegation",
                "summary": f"Delegated to subagent: {subagent_name}",
                "data": {
                    "subagent_name": subagent_name,
                    "subagent_id": f"{_config.agent_name}:sub:{subagent_name}",
                    "sub_task_id": f"sub-{tool_use_id}",
                    "prompt_preview": str(tool_input.get("prompt", ""))[:500],
                },
                "tags": ["delegation", "subagent"],
            })
        
        return {}

    # ── BRANCH: AskUserQuestion → approval flow ──
    if tool_name == "AskUserQuestion" and task:
        # Sensor #22: Approval Request
        question = tool_input.get("question", "Awaiting user input")
        task.request_approval(
            summary=f"Agent asking user: {question[:200]}",
            approver="user",
        )

    # ── STANDARD: Track as action ──
    # Sensor #16: Action (context manager)
    action_name = _tool_to_action_name(tool_name, tool_input)
    ctx = agent.track_context(action_name)
    ctx.__enter__()

    with _active_tools_lock:
        _active_tools[tool_use_id] = {
            "track_ctx": ctx,
            "start_time": time.perf_counter(),
            "tool_name": tool_name,
        }

    return {}  # Don't modify SDK behavior
```

**Helper — tool name mapping:**

```python
def _tool_to_action_name(tool_name: str, tool_input: dict) -> str:
    """
    Convert Agent SDK tool name + input into a descriptive action name.
    
    Examples:
      Read(auth.py)       → "Read:auth.py"
      Bash(pytest)        → "Bash:pytest"
      Grep(login)         → "Grep:login"
      Edit(auth.py)       → "Edit:auth.py"
      Glob(**/*.py)       → "Glob:**/*.py"
      WebSearch(query)    → "WebSearch:query"
      WebFetch(url)       → "WebFetch:example.com"
    """
    # Extract the most descriptive parameter for each tool
    detail = ""
    if tool_name == "Read":
        detail = tool_input.get("file_path", "")
    elif tool_name == "Edit":
        detail = tool_input.get("file_path", "")
    elif tool_name == "Write":
        detail = tool_input.get("file_path", "")
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "")
        detail = cmd.split()[0] if cmd else ""  # First word of command
    elif tool_name == "Grep":
        detail = tool_input.get("pattern", "")
    elif tool_name == "Glob":
        detail = tool_input.get("pattern", "")
    elif tool_name == "WebSearch":
        detail = tool_input.get("query", "")[:50]
    elif tool_name == "WebFetch":
        url = tool_input.get("url", "")
        # Extract domain for readability
        try:
            from urllib.parse import urlparse
            detail = urlparse(url).netloc
        except Exception:
            detail = url[:50]

    if detail:
        # Truncate long paths/queries for readability in timeline
        if len(detail) > 60:
            detail = detail[:57] + "..."
        return f"{tool_name}:{detail}"
    return tool_name
```

---

### 6.3 `on_post_tool_use`

**Trigger:** Agent SDK fires `PostToolUse` after every tool call.  
**Sensors activated:** `track_context().__exit__()` → `action_completed` / `action_failed`

```python
async def on_post_tool_use(input_data: dict, tool_use_id: str, context: dict) -> dict:
    """
    PostToolUse hook.
    
    input_data expected keys:
      - tool_name: str
      - tool_result: str | dict — tool output
      - error: str | None — error message if tool failed
    
    Sensors triggered (standard tools):
      - action_completed or action_failed (via track_context().__exit__())
    
    Sensors triggered (Task tool — subagent):
      - task_completed or task_failed (subagent task)
    
    Sensors triggered (AskUserQuestion):
      - approval_received (via task.approval_received())
    
    Dashboard impact:
      - Timeline: action node gets duration, status, result preview
      - Activity Stream: action_completed/failed event
      - Fleet stats: action counts update
    """
    tool_name = input_data.get("tool_name", "")
    tool_result = input_data.get("tool_result", "")
    error = input_data.get("error")
    task = _current_task.get()

    # ── BRANCH: Subagent return ──
    if tool_name == "Task" and _config.track_subagents:
        with _subagent_lock:
            sub_info = _subagent_tasks.pop(tool_use_id, None)
        
        if sub_info:
            sub_task = sub_info["task"]
            elapsed = (time.perf_counter() - sub_info["start_time"]) * 1000
            
            if error:
                sub_task.fail(error_message=str(error))
            else:
                sub_task.set_payload({
                    "result_preview": str(tool_result)[:_config.result_preview_length],
                    "duration_ms": round(elapsed),
                })
                sub_task.complete()
        
        return {}

    # ── BRANCH: AskUserQuestion → approval received ──
    if tool_name == "AskUserQuestion" and task:
        # Sensor #23: Approval Response
        response = str(tool_result)[:200] if tool_result else "No response"
        task.approval_received(
            summary=f"User responded: {response}",
            approved_by="user",
            decision="approved",  # User provided input = approved
        )

    # ── STANDARD: Close action tracking ──
    with _active_tools_lock:
        tool_info = _active_tools.pop(tool_use_id, None)

    if tool_info:
        ctx = tool_info["track_ctx"]
        
        # Attach result preview to payload
        if _config.capture_tool_results:
            result_str = str(tool_result)[:_config.result_preview_length]
            payload = {
                "tool_name": tool_info["tool_name"],
                "result_preview": result_str,
            }
            if error:
                payload["error"] = str(error)[:500]
            ctx.set_payload(payload)

        if error:
            # Exit with exception info → triggers action_failed
            try:
                raise ToolExecutionError(str(error))
            except ToolExecutionError:
                ctx.__exit__(*sys.exc_info())
        else:
            # Clean exit → triggers action_completed
            ctx.__exit__(None, None, None)

    return {}  # Don't modify SDK behavior
```

---

### 6.4 `on_stop`

**Trigger:** Agent SDK fires `Stop` when the agent produces a final result.  
**Sensors activated:** `task.event()` → `custom` (agent_result), `task.complete()` → `task_completed`

```python
async def on_stop(input_data: dict, tool_use_id: str, context: dict) -> dict:
    """
    Stop hook.
    
    input_data expected keys:
      - result: str — the agent's final output
    
    Sensors triggered:
      - custom event kind=agent_result (captures final output preview)
      - task_completed (closes the task)
    
    Dashboard impact:
      - Timeline: final green node
      - Task List: row status → completed, duration populated
      - Fleet View: agent status → idle
      - Activity Stream: task_completed event
    """
    task = _current_task.get()
    if not task:
        return {}

    result = input_data.get("result", "")

    # Sensor #25: Custom event with agent result
    task.event("custom", payload={
        "kind": "agent_result",
        "summary": f"Agent completed: {str(result)[:100]}",
        "data": {"result_preview": str(result)[:1000]},
        "tags": ["result", "completed"],
    })

    # Sensor #13: Task completion
    task.complete()
    _current_task.set(None)

    return {}
```

---

### 6.5 `on_session_end`

**Trigger:** Agent SDK fires `SessionEnd` when the session is fully done.  
**Sensors activated:** Fallback task completion + flush

```python
async def on_session_end(input_data: dict, tool_use_id: str, context: dict) -> dict:
    """
    SessionEnd hook.
    
    Safety net — ensures the task is closed even if Stop didn't fire
    (e.g., agent errored out, was cancelled, or hit a limit).
    
    Sensors triggered:
      - task_completed or task_failed (if not already closed by Stop)
      - hiveloop.flush() (ensure all events delivered)
    
    Dashboard impact:
      - Ensures no tasks stuck in "processing" state
    """
    task = _current_task.get()
    if task:
        # Task wasn't closed by Stop — this is likely an error/cancellation
        error = input_data.get("error")
        if error:
            task.fail(error_message=str(error)[:500])
        else:
            task.complete()
        _current_task.set(None)

    # Sensor #27: Force flush to ensure delivery
    hb = _hb_client.get()
    if hb:
        hb.flush()

    return {}
```

---

## 7. The Factory Function

### 7.1 Configuration

```python
@dataclass
class ClaudeAgentSDKConfig:
    """Configuration for the Claude Agent SDK integration."""
    
    # HiveLoop connection
    api_key: str                           # Required
    endpoint: str = None                   # Default: auto-resolved
    environment: str = "production"
    group: str = "default"
    
    # Agent identity
    agent_name: str = "claude-agent"       # Shows in Fleet View
    agent_type: str = "general"            # Classification
    agent_version: str = None              # Optional version string
    project: str = None                    # HiveBoard project for tasks
    
    # Behavioral
    heartbeat_interval: float = 30.0       # Seconds
    stuck_threshold: int = 300             # Seconds
    track_subagents: bool = True           # Track Task tool as subagents
    capture_tool_results: bool = True      # Include result previews in payloads
    result_preview_length: int = 500       # Max chars for result previews
    
    # Advanced
    flush_interval: float = 5.0
    batch_size: int = 100
    debug: bool = False
```

### 7.2 Factory

```python
def hiveloop_hooks(
    api_key: str,
    *,
    endpoint: str = None,
    project: str = None,
    agent_name: str = "claude-agent",
    agent_type: str = "general",
    agent_version: str = None,
    environment: str = "production",
    group: str = "default",
    track_subagents: bool = True,
    capture_tool_results: bool = True,
    result_preview_length: int = 500,
    heartbeat_interval: float = 30.0,
    stuck_threshold: int = 300,
    debug: bool = False,
) -> dict:
    """
    Create Claude Agent SDK hooks for HiveBoard observability.
    
    Returns a dict compatible with ClaudeAgentOptions.hooks.
    
    Usage:
        async for message in query(
            prompt="...",
            options=ClaudeAgentOptions(
                hooks=hiveloop_hooks(api_key="hb_live_xxx", project="my-project"),
            ),
        ):
            ...
    
    What you get on HiveBoard:
        - Agent card with live heartbeat in Fleet View
        - Task timelines with tool action nodes
        - Subagent pipeline tracking (if using Task tool)
        - Approval flow tracking (if using AskUserQuestion)
        - Real-time activity stream
        - Full task list with duration and status
    """
    # Store config in module-level for hook callbacks
    global _config
    _config = ClaudeAgentSDKConfig(
        api_key=api_key,
        endpoint=endpoint,
        project=project,
        agent_name=agent_name,
        agent_type=agent_type,
        agent_version=agent_version,
        environment=environment,
        group=group,
        track_subagents=track_subagents,
        capture_tool_results=capture_tool_results,
        result_preview_length=result_preview_length,
        heartbeat_interval=heartbeat_interval,
        stuck_threshold=stuck_threshold,
        debug=debug,
    )
    
    # Sensor #1: SDK Init
    hb = hiveloop.init(
        api_key=api_key,
        endpoint=endpoint,
        environment=environment,
        group=group,
        flush_interval=_config.flush_interval,
        batch_size=_config.batch_size,
        debug=debug,
    )
    _hb_client.set(hb)

    # Build hooks dict matching Claude Agent SDK format
    return {
        "SessionStart": [
            HookMatcher(matcher=".*", hooks=[on_session_start])
        ],
        "SessionEnd": [
            HookMatcher(matcher=".*", hooks=[on_session_end])
        ],
        "PreToolUse": [
            HookMatcher(matcher=".*", hooks=[on_pre_tool_use])
        ],
        "PostToolUse": [
            HookMatcher(matcher=".*", hooks=[on_post_tool_use])
        ],
        "Stop": [
            HookMatcher(matcher=".*", hooks=[on_stop])
        ],
    }
```

---

## 8. Hook Composition — merge_hooks() Utility

Developers may have their own hooks. Provide a merge utility:

```python
def merge_hooks(*hook_dicts: dict) -> dict:
    """
    Merge multiple hook dicts. Later dicts append to earlier ones.
    
    Usage:
        my_hooks = {"PostToolUse": [HookMatcher(matcher="Edit", hooks=[my_audit])]}
        hl_hooks = hiveloop_hooks(api_key="hb_live_xxx")
        combined = merge_hooks(my_hooks, hl_hooks)
    """
    merged = {}
    for hooks in hook_dicts:
        for event_name, matchers in hooks.items():
            if event_name not in merged:
                merged[event_name] = []
            merged[event_name].extend(matchers)
    return merged
```

---

## 9. Phase 2 — LLM Call Interception

### 9.1 The Problem

The Claude Agent SDK handles LLM calls internally. The hooks fire around **tool use**, not API calls. We don't get `on_llm_end` like LangChain gives us.

### 9.2 Approach: Message Stream Wrapper

Wrap the `query()` generator to intercept messages containing usage metadata:

```python
def instrumented_query(prompt: str, options: ClaudeAgentOptions, **kwargs):
    """
    Wrapper around query() that intercepts LLM usage from the message stream.
    
    Usage:
        async for message in instrumented_query(
            prompt="...",
            options=ClaudeAgentOptions(
                hooks=hiveloop_hooks(api_key="hb_live_xxx"),
            ),
        ):
            print(message)
    """
    async for message in query(prompt=prompt, options=options, **kwargs):
        # Check for usage metadata in the message
        _try_capture_llm_usage(message)
        yield message  # Pass through unchanged


def _try_capture_llm_usage(message):
    """
    Inspect a message from the Agent SDK stream for LLM usage data.
    
    Known message types that may contain usage:
      - Messages with 'usage' attribute: {input_tokens, output_tokens}
      - Messages with 'model' attribute
      
    If found, emit Sensor #18: LLM Call
    """
    task = _current_task.get()
    if not task:
        return

    usage = getattr(message, "usage", None)
    if not usage:
        return

    model = getattr(message, "model", "claude-unknown")
    tokens_in = getattr(usage, "input_tokens", 0)
    tokens_out = getattr(usage, "output_tokens", 0)
    
    # Calculate cost (requires pricing table)
    cost = _estimate_cost(model, tokens_in, tokens_out)

    # Sensor #18: LLM Call
    task.llm_call(
        name="agent-loop",
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )
```

**Note:** This approach requires the developer to use `instrumented_query()` instead of `query()`. Document as opt-in for Phase 2.

### 9.3 Pricing Table

```python
# Approximate pricing — update as models change
_MODEL_PRICING = {
    "claude-sonnet-4-20250514": {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    "claude-haiku-4-20250514": {"input": 0.80 / 1_000_000, "output": 4.0 / 1_000_000},
    "claude-opus-4-20250514": {"input": 15.0 / 1_000_000, "output": 75.0 / 1_000_000},
}

def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    pricing = _MODEL_PRICING.get(model, {"input": 3.0/1e6, "output": 15.0/1e6})
    return (tokens_in * pricing["input"]) + (tokens_out * pricing["output"])
```

---

## 10. Edge Cases & Error Handling

### 10.1 Critical Invariant

**The integration must NEVER crash the host agent.** All hook callbacks are wrapped in try/except:

```python
def _safe_hook(fn):
    """Decorator that ensures hooks never raise to the Agent SDK."""
    async def wrapper(input_data, tool_use_id, context):
        try:
            return await fn(input_data, tool_use_id, context)
        except Exception as e:
            if _config and _config.debug:
                logger.error(f"HiveLoop hook error in {fn.__name__}: {e}")
            return {}  # Always return empty dict — don't modify SDK behavior
    return wrapper
```

Apply to all hooks:

```python
on_session_start = _safe_hook(_on_session_start)
on_session_end = _safe_hook(_on_session_end)
on_pre_tool_use = _safe_hook(_on_pre_tool_use)
on_post_tool_use = _safe_hook(_on_post_tool_use)
on_stop = _safe_hook(_on_stop)
```

### 10.2 Edge Cases

| Scenario | Handling |
|----------|---------|
| **PreToolUse fires but PostToolUse never comes** | Orphaned track_context. On SessionEnd, iterate `_active_tools` and force-close all with `action_failed`. |
| **Stop fires before all PostToolUse** | Close remaining actions, then close task. |
| **Session resumed** (`resume=session_id`) | `hb.agent()` is idempotent. `start_task()` with same task_id creates a new run under the same task. |
| **Multiple concurrent sessions** | Each `query()` call runs in its own async context. `contextvars` handles isolation. |
| **HiveBoard server unreachable** | SDK transport handles silently (retry + drop). Agent runs unaffected. |
| **Rapid tool calls (<1ms)** | `track_context` handles sub-millisecond durations. `duration_ms=0` is valid. |

### 10.3 Cleanup on SessionEnd

```python
async def _cleanup():
    """Force-close any orphaned tool contexts."""
    with _active_tools_lock:
        for tool_use_id, info in list(_active_tools.items()):
            try:
                ctx = info["track_ctx"]
                ctx.set_payload({"error": "orphaned — session ended before tool returned"})
                try:
                    raise OrphanedToolError("Session ended before tool returned")
                except OrphanedToolError:
                    ctx.__exit__(*sys.exc_info())
            except Exception:
                pass
        _active_tools.clear()

    with _subagent_lock:
        for tool_use_id, info in list(_subagent_tasks.items()):
            try:
                info["task"].fail(error_message="Parent session ended")
            except Exception:
                pass
        _subagent_tasks.clear()
```

---

## 11. Testing Plan

### 11.1 Smoke Test Sequence

1. Start HiveBoard server locally
2. Create a simple Agent SDK agent with `hiveloop_hooks()`
3. Run a prompt that uses Read, Grep, and Edit tools

**Verify on dashboard:**

| Check | Expected | Endpoint to Verify |
|-------|----------|-------------------|
| Agent card appears | Green heartbeat, name matches `agent_name` | `GET /v1/agents` |
| Task row appears | Status: processing → completed | `GET /v1/tasks` |
| Timeline has nodes | One node per tool call with duration | `GET /v1/tasks/{id}/timeline` |
| Activity stream | Events in order: task_started → action_started → action_completed → ... → task_completed | `GET /v1/events` |
| WebSocket | Live events pushing | `WS /v1/stream` |
| Agent goes idle | Status: idle after task completes | `GET /v1/agents` |
| Stuck detection | Kill agent process → agent goes stuck after threshold | `GET /v1/agents` (wait 5 min) |

### 11.2 Subagent Test

1. Create agent with subagents defined
2. Run a prompt that triggers `Task` tool delegation

**Verify:**

| Check | Expected |
|-------|----------|
| Parent agent card | Shows in Fleet View |
| Subagent card | Separate card with `sub:` prefix |
| Parent timeline | Shows delegation custom event |
| Subagent timeline | Shows its own task with actions |
| Activity stream | Both agent events interleaved chronologically |

### 11.3 Error Test

1. Run a prompt that causes a tool failure (e.g., read a non-existent file)

**Verify:**

| Check | Expected |
|-------|----------|
| Failed action node | Red node in timeline |
| Task status | May still complete (agent handles error) or fail |
| Activity stream | `action_failed` event with error message |

---

## 12. Implementation Checklist

### Phase 1 — Core (target: 1-2 days)

- [ ] Create `src/hiveloop/integrations/claude_agent_sdk.py`
- [ ] Implement `ClaudeAgentSDKConfig` dataclass
- [ ] Implement `hiveloop_hooks()` factory function
- [ ] Implement `on_session_start` → agent registration + task start
- [ ] Implement `on_pre_tool_use` → action tracking (standard tools)
- [ ] Implement `on_post_tool_use` → action completion with result payloads
- [ ] Implement `on_stop` → task completion with result capture
- [ ] Implement `on_session_end` → fallback completion + flush + cleanup
- [ ] Implement `_safe_hook` wrapper on all callbacks
- [ ] Implement `_tool_to_action_name()` helper
- [ ] Implement orphaned tool cleanup in `_cleanup()`
- [ ] Implement `merge_hooks()` utility
- [ ] Run smoke test sequence (Section 11.1)
- [ ] Verify all 6 fully-powered screens (Section 4.1)

### Phase 1b — Subagents (target: +0.5 days)

- [ ] Implement subagent detection in `on_pre_tool_use` (Task tool branch)
- [ ] Implement subagent task lifecycle in `on_post_tool_use`
- [ ] Implement delegation custom event on parent task
- [ ] Run subagent test sequence (Section 11.2)
- [ ] Verify subagent cards in Fleet View

### Phase 1c — Approvals (target: +0.5 days)

- [ ] Implement `AskUserQuestion` → `approval_requested` in `on_pre_tool_use`
- [ ] Implement user response → `approval_received` in `on_post_tool_use`
- [ ] Verify approval events in timeline and activity stream

### Phase 2 — LLM Cost Tracking (target: +1 day)

- [ ] Implement `instrumented_query()` wrapper
- [ ] Implement `_try_capture_llm_usage()` message parser
- [ ] Implement `_estimate_cost()` with pricing table
- [ ] Verify Cost Explorer shows data
- [ ] Verify Model Insights populates

### Phase 3 — TypeScript Port (target: +2-3 days)

- [ ] Port all hooks to TypeScript
- [ ] Implement `hiveloopHooks()` factory
- [ ] Test with TypeScript Agent SDK
- [ ] Publish as `@hiveboard/hiveloop-agent-sdk`

---

## 13. Dependencies

| Dependency | Version | Why |
|------------|---------|-----|
| `hiveloop` | ≥ 0.1.0 | Core SDK |
| `claude-agent-sdk` | ≥ latest | Agent SDK (peer dependency — not imported, only type hints) |

The integration should NOT directly import the Claude Agent SDK. It only needs to conform to the hook callback signature: `async (input_data: dict, tool_use_id: str, context: dict) -> dict`. This keeps it a pure adapter with no coupling to SDK internals.

# HiveLoop × Claude Agent SDK — Integration Spec

**Date:** 2026-02-16  
**Status:** Exploration / RFC  
**Author:** Juan (via Claude)

---

## 1. Executive Summary

The Claude Agent SDK (formerly Claude Code SDK) exposes lifecycle hooks that map directly to HiveLoop's event model. A purpose-built integration — `hiveloop.integrations.claude_agent_sdk` — can auto-instrument any Agent SDK-based agent with **zero changes to the agent's logic**, providing full HiveBoard observability through a single import.

**The pitch:** *"Add one hook to your Claude Agent SDK agent. Get full fleet observability on HiveBoard."*

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks

async for message in query(
    prompt="Process these customer tickets",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash", "Grep"],
        hooks=hiveloop_hooks(api_key="hb_live_xxx", project="support-triage")
    ),
)
```

That's it. The agent now appears on HiveBoard with heartbeats, task timelines, tool action tracking, and session context.

---

## 2. Why This Integration Matters

### 2.1 The Gap

Developers building with the Claude Agent SDK currently have:

- **Console logs** — unstructured, disappear when the terminal closes
- **Session IDs** — opaque strings with no visualization
- **No cost tracking** — the SDK handles LLM calls internally, so developers can't see token usage per task
- **No fleet visibility** — running 5 agents means 5 terminal windows and hoping

### 2.2 What HiveBoard Adds

| Capability | Without HiveBoard | With HiveBoard |
|------------|-------------------|----------------|
| Agent health | Check if process is running | Live heartbeat with stuck detection |
| Task tracking | Session IDs in logs | Full task timelines with action nodes |
| Tool usage | Scroll through terminal output | Visual timeline: Read → Grep → Edit → Bash |
| Cost | Unknown | Per-task, per-agent, per-model cost breakdown |
| Multi-agent | N terminal windows | Fleet View with all agents on one screen |
| Debugging | Re-read logs | Click a task → see every step, duration, and failure |
| Subagent lineage | `parent_tool_use_id` in raw messages | Visual parent→child pipeline topology |

### 2.3 Strategic Value

The Claude Agent SDK is Anthropic's official way to build production agents. Every developer using it is HiveBoard's exact target persona. This integration:

- Creates a distribution channel through Anthropic's ecosystem
- Positions HiveBoard as the default observability layer for Claude-powered agents
- Differentiates from LangSmith/Langfuse (which focus on LangChain/LlamaIndex, not the Agent SDK)
- Could potentially be featured in Anthropic's Agent SDK docs as a recommended integration

---

## 3. Hook-to-Event Mapping

The Claude Agent SDK provides these hooks. Here's how each maps to HiveLoop events:

### 3.1 Session Lifecycle → Agent + Task Events

| Agent SDK Hook | HiveLoop Event | Details |
|---------------|----------------|---------|
| `SessionStart` | `agent_registered` + `task_started` | Register agent (idempotent), start task with session_id as task_id |
| `SessionEnd` | `task_completed` or `task_failed` | Complete/fail task based on final message result |

**Session as Task:** Each `query()` call maps to one HiveLoop task. The `session_id` from the SDK's init message becomes the `task_id`. If sessions are resumed, the same task continues.

```python
# SessionStart hook
async def on_session_start(input_data, tool_use_id, context):
    session_id = input_data.get("session_id")
    agent_handle = hb.agent(
        agent_id=agent_name,
        type="claude-agent-sdk",
        framework="claude-agent-sdk",
    )
    task = agent_handle.start_task(
        task_id=session_id,
        project=project,
        type="agent-sdk-session",
    )
    set_current_task(task)
    set_hiveloop_agent(agent_handle)
    return {}
```

### 3.2 Tool Usage → Action Events

| Agent SDK Hook | HiveLoop Event | Details |
|---------------|----------------|---------|
| `PreToolUse` | `action_started` | Tool name as action_name, tool_input in payload |
| `PostToolUse` | `action_completed` or `action_failed` | Duration auto-calculated, result preview in payload |

**This is the highest-value mapping.** Every tool call (Read, Edit, Bash, Grep, Glob, WebSearch, WebFetch) becomes a visible node in the HiveBoard timeline.

```python
# PreToolUse hook
async def on_pre_tool_use(input_data, tool_use_id, context):
    agent = get_hiveloop_agent()
    if agent:
        ctx = agent.track_context(input_data.get("tool_name", "unknown_tool"))
        ctx.__enter__()
        # Store context for matching PostToolUse
        _active_tool_contexts[tool_use_id] = {
            "track_ctx": ctx,
            "start_time": time.perf_counter(),
        }
    return {}  # Don't modify tool behavior

# PostToolUse hook
async def on_post_tool_use(input_data, tool_use_id, context):
    ctx_info = _active_tool_contexts.pop(tool_use_id, None)
    if ctx_info:
        track_ctx = ctx_info["track_ctx"]
        # Attach result preview to payload
        result = input_data.get("tool_result", "")
        track_ctx.set_payload({
            "tool_name": input_data.get("tool_name"),
            "result_preview": str(result)[:500],
        })
        track_ctx.__exit__(None, None, None)
    return {}
```

### 3.3 Stop → Task Completion

| Agent SDK Hook | HiveLoop Event | Details |
|---------------|----------------|---------|
| `Stop` | `task_completed` | Final result captured, task marked complete |

```python
async def on_stop(input_data, tool_use_id, context):
    task = get_current_task()
    if task:
        result = input_data.get("result", "")
        task.event("custom", payload={
            "kind": "agent_result",
            "summary": f"Agent completed: {str(result)[:100]}",
            "data": {"result_preview": str(result)[:1000]},
        })
        task.complete()
        clear_current_task()
    return {}
```

### 3.4 Subagent Tracking → Pipeline Topology

When the main agent spawns subagents via the `Task` tool, HiveLoop can model this as a pipeline:

| Agent SDK Concept | HiveLoop Concept | Mapping |
|-------------------|------------------|---------|
| Main agent | Parent agent | `hb.agent("main-agent")` |
| Subagent (via `Task` tool) | Child agent | `hb.agent("subagent-{name}")` |
| `parent_tool_use_id` | Parent task linkage | `parent_event_id` on child task |

```python
# In PreToolUse, detect Task tool
async def on_pre_tool_use(input_data, tool_use_id, context):
    tool_name = input_data.get("tool_name", "")
    
    if tool_name == "Task":
        # This is a subagent spawn
        agent_name = input_data.get("tool_input", {}).get("agent", "unknown")
        sub_agent = hb.agent(
            agent_id=f"subagent-{agent_name}",
            type="claude-agent-sdk-subagent",
            framework="claude-agent-sdk",
        )
        parent_task = get_current_task()
        sub_task = sub_agent.start_task(
            task_id=f"{tool_use_id}",
            project=project,
            type="subagent-execution",
        )
        # Link parent → child via event
        if parent_task:
            parent_task.event("custom", payload={
                "kind": "delegation",
                "summary": f"Delegated to subagent: {agent_name}",
                "data": {"subagent": agent_name, "sub_task_id": tool_use_id},
            })
    else:
        # Normal tool tracking (as in 3.2)
        ...
    return {}
```

### 3.5 UserPromptSubmit → Approval Flow

| Agent SDK Hook | HiveLoop Event | Details |
|---------------|----------------|---------|
| `UserPromptSubmit` | `approval_requested` / `approval_received` | Maps to HiveBoard's human-in-the-loop tracking |

When agents use `AskUserQuestion` or require permission approval, this maps to HiveLoop's approval events — making human-in-the-loop interactions visible in the timeline.

---

## 4. Complete Event Mapping Table

| Agent SDK Hook | Fires When | HiveLoop Event Type | HiveLoop Payload Kind | Layer |
|---------------|------------|--------------------|-----------------------|-------|
| `SessionStart` | Query begins | `agent_registered` + `task_started` | — | 0 + 1 |
| `SessionEnd` | Query ends | `task_completed` / `task_failed` | — | 1 |
| `PreToolUse` | Before any tool | `action_started` | — | 1 |
| `PostToolUse` | After any tool | `action_completed` / `action_failed` | — | 1 |
| `Stop` | Agent finishes | `task_completed` | `agent_result` | 1 + 2 |
| `PreToolUse` (Task) | Subagent spawn | `task_started` (child) + `custom` | `delegation` | 2 |
| `PostToolUse` (Task) | Subagent returns | `task_completed` (child) | — | 2 |
| `UserPromptSubmit` | User interaction | `approval_requested` / `approval_received` | — | 2 |
| _(background)_ | Every 30s | `heartbeat` | — | 0 |

---

## 5. Integration API Design

### 5.1 One-Line Setup (Recommended)

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks

hooks = hiveloop_hooks(
    api_key="hb_live_xxx",
    endpoint="https://api.hiveboard.io",  # or localhost for dev
    project="my-project",
    agent_name="my-agent",       # optional, defaults to "claude-agent"
    agent_type="general",        # optional
    track_subagents=True,        # optional, default True
    capture_tool_results=True,   # optional, captures result previews
    result_preview_length=500,   # optional, truncation length
)

async for message in query(
    prompt="...",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash"],
        hooks=hooks,
    ),
):
    ...
```

### 5.2 What `hiveloop_hooks()` Returns

The function returns a dict matching the Agent SDK's hook structure:

```python
def hiveloop_hooks(api_key, endpoint=None, project=None, **kwargs) -> dict:
    """Create Agent SDK hooks dict for HiveBoard observability."""
    hb = hiveloop.init(api_key=api_key, endpoint=endpoint)
    
    # Build hook callbacks
    session_start = _make_session_start_hook(hb, project, **kwargs)
    session_end = _make_session_end_hook()
    pre_tool = _make_pre_tool_hook(hb, project, **kwargs)
    post_tool = _make_post_tool_hook(**kwargs)
    stop = _make_stop_hook()
    
    return {
        "SessionStart": [HookMatcher(matcher=".*", hooks=[session_start])],
        "SessionEnd": [HookMatcher(matcher=".*", hooks=[session_end])],
        "PreToolUse": [HookMatcher(matcher=".*", hooks=[pre_tool])],
        "PostToolUse": [HookMatcher(matcher=".*", hooks=[post_tool])],
        "Stop": [HookMatcher(matcher=".*", hooks=[stop])],
    }
```

### 5.3 Advanced: Composing with Existing Hooks

Developers who already have their own hooks can merge:

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks

my_hooks = {
    "PostToolUse": [HookMatcher(matcher="Edit|Write", hooks=[my_audit_hook])]
}

# Merge HiveLoop hooks with custom hooks
hl_hooks = hiveloop_hooks(api_key="hb_live_xxx", project="my-project")

combined = merge_hooks(my_hooks, hl_hooks)  # utility function

async for message in query(
    prompt="...",
    options=ClaudeAgentOptions(hooks=combined),
):
    ...
```

### 5.4 TypeScript API

```typescript
import { hiveloopHooks } from "hiveloop/integrations/claude-agent-sdk";

const hooks = hiveloopHooks({
  apiKey: "hb_live_xxx",
  project: "my-project",
  agentName: "my-agent",
});

for await (const message of query({
  prompt: "...",
  options: {
    allowedTools: ["Read", "Edit", "Bash"],
    hooks: hooks,
  },
})) {
  // Full HiveBoard observability, automatic
}
```

---

## 6. What the Developer Sees on HiveBoard

### 6.1 The Hive (Fleet View)

Each Agent SDK agent appears as a card:

```
┌─────────────────────────────────────┐
│ 🟢 my-agent                        │
│ Type: general  Framework: claude-agent-sdk │
│ Status: PROCESSING                  │
│ ↳ session_abc123                    │
│ ♥ 12s ago                          │
│ ▂▃▅▇▅▃▂  (task throughput)         │
└─────────────────────────────────────┘
```

Subagents appear as separate cards when `track_subagents=True`, with pipeline connections visible in Fleet View.

### 6.2 Task Timeline

A single query() session shows as a horizontal timeline:

```
SessionStart → Read(auth.py) → Grep("login") → Edit(auth.py) → Bash("pytest") → Stop
   0ms          120ms           340ms            890ms           2400ms          4200ms
   [blue]       [blue]          [blue]           [blue]          [blue]          [green]
```

Each node is clickable, showing:
- **Tool name** and input parameters
- **Duration** of the tool execution
- **Result preview** (truncated)
- **Errors** highlighted in red with exception details

### 6.3 Cost Explorer

If the integration captures LLM usage from the Agent SDK's internal calls (requires SDK cooperation or message stream parsing), the Cost Explorer shows:

- Cost per session/task
- Cost per agent
- Model usage breakdown
- Token consumption trends

### 6.4 Activity Stream

Real-time feed of all events:

```
14:32:01  my-agent  task_started    session_abc123
14:32:01  my-agent  action_started  Read → auth.py
14:32:02  my-agent  action_completed Read (120ms)
14:32:02  my-agent  action_started  Grep → "login"
14:32:02  my-agent  action_completed Grep (80ms, 3 matches)
14:32:03  my-agent  action_started  Edit → auth.py
14:32:04  my-agent  action_completed Edit (890ms)
14:32:04  my-agent  action_started  Bash → pytest
14:32:06  my-agent  action_completed Bash (2400ms, exit 0)
14:32:07  my-agent  task_completed  session_abc123 (4.2s)
```

---

## 7. LLM Cost Tracking — The Open Question

The Claude Agent SDK handles LLM calls internally. The developer doesn't make explicit API calls — Claude's agent loop manages prompt construction, API calls, and response parsing behind the scenes.

### 7.1 The Challenge

Unlike LangChain/CrewAI integrations where `on_llm_end` exposes token counts and model info, the Agent SDK doesn't surface this through hooks. The hooks fire around **tool use**, not LLM calls.

### 7.2 Potential Approaches

**Option A: Parse the Message Stream**

The `query()` generator yields messages. Some message types may contain usage metadata:

```python
async for message in query(prompt="..."):
    if hasattr(message, "usage"):
        # Extract token counts from the message
        task.llm_call(
            name="agent-loop-call",
            model=message.model or "claude-sonnet-4-20250514",
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
            cost=calculate_cost(message.usage),
        )
```

This requires the integration to wrap the `query()` generator and intercept messages before yielding them.

**Option B: Request a Hook from Anthropic**

Propose an `OnLLMCall` or `OnAPIResponse` hook to the Agent SDK team. This would be the cleanest solution and would benefit the entire ecosystem.

**Option C: Estimate from Tool Patterns**

Infer approximate costs based on the number of tool calls and average token usage per tool cycle. Less accurate but requires no SDK changes.

**Recommended:** Start with Option A (message stream parsing). Pursue Option B with Anthropic as a feature request. Option C as fallback.

---

## 8. MCP Server: HiveBoard as a Tool

A complementary integration: expose HiveBoard as an MCP server that agents can query:

```python
# Agent can check its own observability data
async for message in query(
    prompt="Check if my cost this week is within budget before processing",
    options=ClaudeAgentOptions(
        mcp_servers={
            "hiveboard": {
                "command": "npx",
                "args": ["@hiveboard/mcp-server", "--api-key", "hb_live_xxx"]
            }
        }
    ),
):
    ...
```

This enables **self-aware agents** that:
- Check their own cost trends before choosing a model
- Query their error rates before deciding retry strategies
- Review their throughput before accepting more work
- Access fleet status to coordinate with other agents

This is a separate initiative but worth noting as a natural complement.

---

## 9. Implementation Roadmap

### Phase 1: Core Hooks Integration (MVP)

**Effort:** ~2 days  
**Delivers:** Agent registration, heartbeats, task tracking, tool action timelines

- [ ] `SessionStart` → `agent_registered` + `task_started`
- [ ] `PreToolUse` / `PostToolUse` → `action_started` / `action_completed`
- [ ] `Stop` / `SessionEnd` → `task_completed` / `task_failed`
- [ ] Background heartbeat thread
- [ ] `hiveloop_hooks()` factory function
- [ ] Verify on HiveBoard: agent card, task timeline, activity stream

### Phase 2: Rich Telemetry

**Effort:** ~2 days  
**Delivers:** Cost tracking, subagent pipelines, approval flows

- [ ] Message stream parsing for LLM usage data
- [ ] Subagent detection via `Task` tool in `PreToolUse`
- [ ] Parent→child task linkage
- [ ] `UserPromptSubmit` → approval events
- [ ] `AskUserQuestion` → approval_requested/received flow
- [ ] Cost Explorer integration

### Phase 3: TypeScript SDK

**Effort:** ~3 days  
**Delivers:** Feature parity in TypeScript

- [ ] TypeScript `hiveloopHooks()` factory
- [ ] All hook mappings ported
- [ ] npm package: `@hiveboard/hiveloop-agent-sdk`

### Phase 4: MCP Server (Stretch)

**Effort:** ~3 days  
**Delivers:** Self-aware agents that query their own observability

- [ ] HiveBoard MCP server implementation
- [ ] Tools: `get_agent_status`, `get_cost_summary`, `get_recent_errors`
- [ ] Documentation and examples

---

## 10. Competitive Positioning

| Platform | LangChain | CrewAI | AutoGen | Claude Agent SDK |
|----------|-----------|--------|---------|-----------------|
| LangSmith | ✅ Native | ❌ | ❌ | ❌ |
| Langfuse | ✅ | Partial | Partial | ❌ |
| Arize Phoenix | ✅ | Partial | ❌ | ❌ |
| **HiveBoard** | ✅ Planned | ✅ Planned | ✅ Planned | **✅ First-mover** |

No observability platform currently supports the Claude Agent SDK. HiveBoard would be the first, establishing the integration pattern before competitors.

---

## 11. Open Questions

1. **Does the Agent SDK expose LLM usage in its message stream?** Need to verify by running an agent and inspecting all message types. This determines whether cost tracking is possible in Phase 1 or requires Phase 2.

2. **Hook composition API:** The Agent SDK docs show hooks as a dict of `HookMatcher` lists. How does this compose when the developer already has hooks? Need a clean `merge_hooks()` utility.

3. **Session resume:** When `resume=session_id` is used, should the integration resume the same HiveLoop task or create a new one linked to the original? Resuming the same task is more intuitive for the timeline, but creates a task that spans multiple `query()` calls.

4. **Agent naming:** If the developer doesn't specify `agent_name`, should we derive it from the prompt, use a default, or require it? Defaulting to `"claude-agent"` is safe but not useful for fleets.

5. **Anthropic partnership:** Could this integration be featured in the Agent SDK documentation? That would be the single highest-leverage distribution channel.

---

## 12. Summary

The Claude Agent SDK's hook system was practically designed for observability integration. Every hook maps cleanly to a HiveLoop event type, and the `hiveloop_hooks()` factory function makes adoption a single function call.

This isn't just another framework integration — it's a first-mover opportunity in Anthropic's ecosystem, targeting exactly the developers who need HiveBoard most: people building production AI agents who currently have no visibility into what their agents are doing.

**The one-liner that sells it:**

```python
hooks=hiveloop_hooks(api_key="hb_live_xxx")
```

*From zero visibility to full fleet observability.*

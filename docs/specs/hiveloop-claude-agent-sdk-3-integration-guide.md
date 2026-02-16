# HiveLoop — Integration Guide: Claude Agent SDK

**Version:** 0.1.0  
**Last updated:** 2026-02-16

> *One function call. Full fleet observability for your Claude Agent SDK agents.*

---

## Table of Contents

1. [What This Guide Covers](#1-what-this-guide-covers)
2. [Quick Start — 60 Seconds to First Heartbeat](#2-quick-start--60-seconds-to-first-heartbeat)
3. [What You See on HiveBoard](#3-what-you-see-on-hiveboard)
4. [Configuration Reference](#4-configuration-reference)
5. [Working with Subagents](#5-working-with-subagents)
6. [Working with User Input and Approvals](#6-working-with-user-input-and-approvals)
7. [Composing with Your Own Hooks](#7-composing-with-your-own-hooks)
8. [Adding LLM Cost Tracking](#8-adding-llm-cost-tracking)
9. [Adding Rich Events (Going Deeper)](#9-adding-rich-events-going-deeper)
10. [Validation Checklist](#10-validation-checklist)
11. [Troubleshooting](#11-troubleshooting)
12. [FAQ](#12-faq)

---

## 1. What This Guide Covers

This guide walks you through adding HiveBoard observability to agents built with the [Claude Agent SDK](https://docs.anthropic.com/en/docs/agent-sdk) (formerly Claude Code SDK). The integration hooks into the Agent SDK's lifecycle hooks — no changes to your agent's logic, no manual event code.

**What you get automatically:**

| Capability | What it looks like on HiveBoard |
|-----------|--------------------------------|
| Agent health | Live heartbeat, stuck detection, online/offline status |
| Task tracking | Every `query()` call becomes a task with start/end/duration/status |
| Tool timelines | Every Read, Edit, Bash, Grep, Glob call becomes a node in the timeline |
| Subagent pipelines | Task tool delegations show as parent → child relationships |
| Approval tracking | AskUserQuestion calls appear as human-in-the-loop events |
| Real-time stream | All events push to the dashboard via WebSocket as they happen |

**What you don't get automatically** (but can add manually):

LLM cost tracking, plans, queue state, issues, retries, and scheduled work. See [Section 8](#8-adding-llm-cost-tracking) and [Section 9](#9-adding-rich-events-going-deeper) for how to add these.

### Prerequisites

- `pip install hiveloop` (v0.1.0+)
- `pip install claude-agent-sdk`
- A HiveBoard API key (get one at [hiveboard.net](https://hiveboard.net))
- A working Claude Agent SDK agent (the [quickstart](https://docs.anthropic.com/en/docs/agent-sdk/quickstart) is a great starting point)

---

## 2. Quick Start — 60 Seconds to First Heartbeat

### Before (no observability)

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions


async def main():
    async for message in query(
        prompt="Find and fix the bug in auth.py",
        options=ClaudeAgentOptions(allowed_tools=["Read", "Edit", "Bash"]),
    ):
        if hasattr(message, "result"):
            print(message.result)


asyncio.run(main())
```

### After (full observability)

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks    # ← add this


async def main():
    async for message in query(
        prompt="Find and fix the bug in auth.py",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Bash"],
            hooks=hiveloop_hooks(                                     # ← add this
                api_key="hb_live_your_key_here",
                project="my-project",
                agent_name="bug-fixer",
            ),
        ),
    ):
        if hasattr(message, "result"):
            print(message.result)


asyncio.run(main())
```

**Two lines added. Zero logic changed.** Your agent now appears on HiveBoard with a live heartbeat, and every tool call becomes a visible step in the task timeline.

### What happens under the hood

1. `hiveloop_hooks()` initializes the HiveLoop SDK and returns a hooks dict
2. When `query()` starts, the `SessionStart` hook registers the agent and starts a task
3. A background thread begins emitting heartbeats every 30 seconds
4. Each tool call triggers `PreToolUse` / `PostToolUse` hooks → action tracking
5. When the agent finishes, the `Stop` hook closes the task with the result
6. `SessionEnd` flushes all events to ensure delivery

The hooks never modify the Agent SDK's behavior — they observe and report. If HiveBoard is unreachable, your agent runs exactly the same. Events are dropped silently, never thrown.

---

## 3. What You See on HiveBoard

### 3.1 Fleet View — "Are my agents healthy?"

Your agent appears as a card with live status:

```
┌─────────────────────────────────────┐
│ 🟢 bug-fixer                       │
│ Type: general  Framework: claude-agent-sdk │
│ Status: PROCESSING                  │
│ ↳ session_a1b2c3d4                  │
│ ♥ 8s ago                            │
│ Tasks: 3 ✓  0 ✗  |  Avg: 4.2s      │
└─────────────────────────────────────┘
```

When the agent finishes, status goes to IDLE. If the process crashes, the heartbeat stops and the card turns STUCK after 5 minutes.

### 3.2 Task Timeline — "What exactly happened?"

Every `query()` call becomes a task. Click it to see the full timeline:

```
session_a1b2c3d4  ·  bug-fixer  ·  completed  ·  4.2s

──┬── Read:auth.py ─── Grep:"login" ─── Edit:auth.py ─── Bash:pytest ──┬──
  │     120ms            80ms             890ms            2400ms        │
  start                                                               stop
```

Each tool call is a node. Click any node to see:
- **Tool name** and input parameters
- **Duration** of the tool execution
- **Result preview** (first 500 chars of the tool output)
- **Error details** if the tool failed (red node)

### 3.3 Activity Stream — "What's happening right now?"

Real-time feed of all events, pushed via WebSocket:

```
14:32:01  bug-fixer  task_started     session_a1b2c3d4
14:32:01  bug-fixer  action_started   Read:auth.py
14:32:02  bug-fixer  action_completed Read:auth.py (120ms)
14:32:02  bug-fixer  action_started   Grep:"login"
14:32:02  bug-fixer  action_completed Grep:"login" (80ms, 3 matches)
14:32:03  bug-fixer  action_started   Edit:auth.py
14:32:04  bug-fixer  action_completed Edit:auth.py (890ms)
14:32:04  bug-fixer  action_started   Bash:pytest
14:32:06  bug-fixer  action_completed Bash:pytest (2400ms, exit 0)
14:32:07  bug-fixer  task_completed   session_a1b2c3d4 (4.2s)
```

### 3.4 Task List — "What ran today?"

Filterable table of all tasks across all agents. Each row shows the task ID, agent, project, status, duration, and action count.

---

## 4. Configuration Reference

### 4.1 All Parameters

```python
hooks = hiveloop_hooks(
    # ── Required ──
    api_key="hb_live_xxx",             # Your HiveBoard API key

    # ── Agent Identity ──
    agent_name="bug-fixer",            # Name in Fleet View (default: "claude-agent")
    agent_type="coder",                # Classification (default: "general")
    agent_version="1.2.0",             # Your agent's version (default: None)
    project="backend-team",            # HiveBoard project for tasks (default: None)

    # ── Connection ──
    endpoint="https://api.hiveboard.io",  # Backend URL (default: auto-resolved)
    environment="production",             # dev / staging / production (default: "production")
    group="engineering",                  # Team grouping for filtering (default: "default")

    # ── Behavior ──
    track_subagents=True,              # Track Task tool as subagents (default: True)
    capture_tool_results=True,         # Include tool output in payloads (default: True)
    result_preview_length=500,         # Max chars for result previews (default: 500)
    heartbeat_interval=30.0,           # Seconds between heartbeats (default: 30.0)
    stuck_threshold=300,               # Seconds before marked stuck (default: 300)

    # ── Advanced ──
    debug=False,                       # Enable debug logging (default: False)
)
```

### 4.2 Minimal Setup

If you just want it working with defaults:

```python
hooks = hiveloop_hooks(api_key="hb_live_xxx")
```

This creates an agent named `"claude-agent"` with type `"general"`, no project, and all other defaults. Good for trying it out — customize when you're ready.

### 4.3 Multiple Agents

If you run multiple Agent SDK agents, give each a distinct name:

```python
# Agent 1: Bug fixer
async for message in query(
    prompt="Fix the bug in auth.py",
    options=ClaudeAgentOptions(
        hooks=hiveloop_hooks(api_key="hb_live_xxx", agent_name="bug-fixer", project="backend"),
    ),
):
    ...

# Agent 2: Code reviewer
async for message in query(
    prompt="Review the PR for security issues",
    options=ClaudeAgentOptions(
        hooks=hiveloop_hooks(api_key="hb_live_xxx", agent_name="code-reviewer", project="backend"),
    ),
):
    ...
```

Both agents appear as separate cards in Fleet View with independent heartbeats, task histories, and statistics.

### 4.4 Environment-Based Configuration

```python
import os

hooks = hiveloop_hooks(
    api_key=os.environ["HIVEBOARD_API_KEY"],
    endpoint=os.environ.get("HIVEBOARD_ENDPOINT", "https://api.hiveboard.io"),
    environment=os.environ.get("DEPLOY_ENV", "production"),
    agent_name=os.environ.get("AGENT_NAME", "my-agent"),
    project=os.environ.get("HIVEBOARD_PROJECT", "default"),
)
```

---

## 5. Working with Subagents

If your Agent SDK agent uses [subagents](https://docs.anthropic.com/en/docs/agent-sdk/subagents) via the `Task` tool, HiveLoop tracks them automatically.

### 5.1 What Happens

When your main agent delegates to a subagent:

1. HiveLoop detects the `Task` tool call in `PreToolUse`
2. A new agent is registered for the subagent (named `{your-agent}:sub:{subagent-name}`)
3. A child task is created under the subagent
4. A `delegation` event is emitted on the parent task
5. When the subagent returns, its task is completed

### 5.2 Example

```python
async for message in query(
    prompt="Use the code-reviewer to review this codebase",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep", "Task"],
        agents={
            "code-reviewer": AgentDefinition(
                description="Expert code reviewer.",
                prompt="Analyze code quality and suggest improvements.",
                tools=["Read", "Glob", "Grep"],
            )
        },
        hooks=hiveloop_hooks(
            api_key="hb_live_xxx",
            agent_name="orchestrator",
            project="code-quality",
        ),
    ),
):
    ...
```

### 5.3 What You See on HiveBoard

**Fleet View** — Two agent cards:

```
┌──────────────────────────┐  ┌──────────────────────────────────────┐
│ 🟢 orchestrator          │  │ 🟢 orchestrator:sub:code-reviewer    │
│ PROCESSING               │  │ PROCESSING                           │
│ ↳ session_abc123         │  │ ↳ sub-tu_def456                      │
└──────────────────────────┘  └──────────────────────────────────────┘
```

**Parent Task Timeline** — Shows delegation event:

```
──┬── Read:... ─── [delegation → code-reviewer] ─── ... ──┬──
```

**Child Task Timeline** — Shows the subagent's own tool calls:

```
──┬── Glob:**/*.py ─── Read:utils.py ─── Grep:"TODO" ──┬──
```

### 5.4 Disabling Subagent Tracking

If you don't want subagents as separate agents (e.g., too many short-lived subagents), set `track_subagents=False`. Subagent calls will appear as regular `Task` action nodes in the parent timeline instead.

```python
hooks = hiveloop_hooks(
    api_key="hb_live_xxx",
    track_subagents=False,  # Task tool calls tracked as actions, not subagents
)
```

---

## 6. Working with User Input and Approvals

If your agent uses `AskUserQuestion` or has interactive approval flows, HiveLoop tracks these as human-in-the-loop events.

### 6.1 What Happens

1. When `AskUserQuestion` fires in `PreToolUse`, HiveLoop emits an `approval_requested` event
2. The agent's status changes to `waiting_approval` on the dashboard
3. When the user responds in `PostToolUse`, HiveLoop emits an `approval_received` event
4. The status returns to `processing`

### 6.2 What You See on HiveBoard

**Fleet View** — Agent card shows waiting state:

```
┌─────────────────────────────────────┐
│ 🟡 my-agent                        │
│ Status: WAITING APPROVAL            │
│ ↳ session_abc123                    │
└─────────────────────────────────────┘
```

**Task Timeline** — Approval node with duration (how long the user took to respond):

```
──── Edit:config.yaml ─── [⏳ Awaiting user: "Deploy to prod?"] ─── Bash:deploy ────
                                      42.3s (human response time)
```

**Activity Stream:**

```
14:32:10  my-agent  approval_requested  "Agent asking user: Deploy to production?"
14:32:52  my-agent  approval_received   "User responded: Yes, deploy"
```

---

## 7. Composing with Your Own Hooks

If you already have custom hooks, use `merge_hooks()` to combine them with HiveLoop:

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks, merge_hooks

# Your existing hooks
my_hooks = {
    "PostToolUse": [
        HookMatcher(matcher="Edit|Write", hooks=[my_audit_logger])
    ],
    "PreToolUse": [
        HookMatcher(matcher="Bash", hooks=[my_command_validator])
    ],
}

# Combine
combined = merge_hooks(my_hooks, hiveloop_hooks(api_key="hb_live_xxx"))

async for message in query(
    prompt="...",
    options=ClaudeAgentOptions(hooks=combined),
):
    ...
```

Both your hooks and HiveLoop's hooks fire for every event. Order is preserved — your hooks run first (since they're first in `merge_hooks`), HiveLoop's hooks observe after.

**Important:** HiveLoop hooks always return `{}` (empty dict). They never modify the Agent SDK's behavior. Your hooks can still block, modify, or reject tool calls as normal.

---

## 8. Adding LLM Cost Tracking

The Claude Agent SDK handles LLM calls internally — the hooks fire around tool use, not API calls. To get cost tracking, use the `instrumented_query()` wrapper which intercepts usage metadata from the message stream.

### 8.1 Setup

```python
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks, instrumented_query

async for message in instrumented_query(                # ← use this instead of query()
    prompt="Fix the bug in auth.py",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Edit", "Bash"],
        hooks=hiveloop_hooks(api_key="hb_live_xxx", project="my-project"),
    ),
):
    if hasattr(message, "result"):
        print(message.result)
```

The only change is `query()` → `instrumented_query()`. Messages pass through unchanged — the wrapper just inspects each one for usage metadata before yielding it to you.

### 8.2 What You Get

If the Agent SDK includes usage data in its messages (model name, input/output tokens), HiveBoard's Cost Explorer populates with:

- Cost per task
- Cost per agent
- Cost breakdown by model
- Token consumption trends
- Per-call detail table

### 8.3 Note on Availability

This depends on the Claude Agent SDK exposing usage metadata in its message stream. If usage data isn't available in the messages, the wrapper is a no-op — no errors, no cost data. Check the [Agent SDK changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md) for updates on usage metadata availability.

---

## 9. Adding Rich Events (Going Deeper)

The auto-instrumentation covers Layers 0 and 1. If you want Layer 2 — plans, retries, issues, custom business events — you can add them manually alongside the hooks.

### 9.1 Getting the Task Object

Inside your agent's code, access the current HiveLoop task via context variables:

```python
from hiveloop.integrations.claude_agent_sdk import get_current_task, get_current_agent

# Anywhere in your codebase (after a query() with hiveloop_hooks has started)
task = get_current_task()
agent = get_current_agent()
```

### 9.2 Adding LLM Calls Manually

If `instrumented_query()` doesn't capture costs (e.g., you make side LLM calls outside the Agent SDK):

```python
task = get_current_task()
if task:
    task.llm_call(
        "side-analysis",
        model="claude-haiku-4-20250514",
        tokens_in=500,
        tokens_out=100,
        cost=0.0008,
    )
```

### 9.3 Adding Issue Reporting

If your agent detects persistent problems:

```python
agent = get_current_agent()
if agent:
    agent.report_issue(
        "API rate limiting detected",
        severity="high",
        category="rate_limit",
    )
```

### 9.4 Adding Custom Events

For any business-specific telemetry:

```python
task = get_current_task()
if task:
    task.event("custom", payload={
        "kind": "validation",
        "summary": "Input validation passed for 42 files",
        "data": {"files_checked": 42, "errors_found": 0},
        "tags": ["validation", "quality"],
    })
```

### 9.5 The `if task:` Guard

Always guard manual event calls. The task is `None` if:
- `hiveloop_hooks()` wasn't provided
- The query hasn't started yet
- The query has already ended
- HiveLoop initialization failed silently

```python
# ✅ Safe
task = get_current_task()
if task:
    task.event("custom", payload={...})

# ❌ Will crash if task is None
get_current_task().event("custom", payload={...})
```

This one-line guard is the price of safety. It's worth it.

---

## 10. Validation Checklist

After adding `hiveloop_hooks()`, run your agent once and verify each step on the dashboard.

### 10.1 Layer 0 — Agent Registration + Heartbeat

| Dashboard Element | What You Should See | If Missing |
|------------------|---------------------|------------|
| **Fleet View** | Agent card with your `agent_name` | `hiveloop_hooks()` not reaching the Agent SDK — check that `hooks=` is passed to `ClaudeAgentOptions` |
| **Heartbeat pulse** | Green dot pulsing, `♥ Xs ago` updating | Heartbeat thread didn't start — check `api_key` is valid, `endpoint` is reachable |
| **Status** | IDLE when no query running, PROCESSING during a query | Status derivation requires task events — trigger a query |

### 10.2 Layer 1 — Task + Action Tracking

Trigger a query and check while it's running:

| Dashboard Element | What You Should See | If Missing |
|------------------|---------------------|------------|
| **Task Table** | New row with session ID, agent name, status: `processing` | `SessionStart` hook not firing — check Agent SDK version supports hooks |
| **Task Timeline** | Nodes appearing for each tool call (Read, Bash, etc.) | `PreToolUse`/`PostToolUse` hooks not wired — check the hooks dict structure |
| **Action labels** | Descriptive labels like `Read:auth.py`, `Bash:pytest` | Labels depend on `tool_input` parsing — check debug logs |
| **Action durations** | Millisecond timings between nodes | `PreToolUse` start time not matching `PostToolUse` — check `tool_use_id` consistency |
| **Task completion** | Status → `completed`, duration populated | `Stop` or `SessionEnd` not firing — check that the agent finishes cleanly |
| **Activity Stream** | Chronological event feed: task_started → actions → task_completed | Events not flushing — check endpoint connectivity, try `debug=True` |

### 10.3 Error Handling

Deliberately trigger a failure (e.g., ask the agent to read a non-existent file):

| Scenario | Expected on Dashboard |
|----------|----------------------|
| Tool fails (e.g., file not found) | Red action node in timeline, `action_failed` in Activity Stream |
| Agent handles error and continues | Task may still complete — red node followed by more blue nodes |
| Agent fails entirely | Task status → `failed`, red task row in Task List |
| Process killed mid-task | Heartbeat stops → agent goes STUCK after threshold (default 5 min) |

### 10.4 Subagents (if applicable)

Trigger a query that uses the `Task` tool:

| Dashboard Element | What You Should See |
|------------------|---------------------|
| **Fleet View** | Second card appears: `{agent_name}:sub:{subagent_name}` |
| **Parent Timeline** | Delegation event node |
| **Child Timeline** | Subagent's own tool call nodes |
| **Activity Stream** | Both agents' events interleaved chronologically |

### 10.5 Quick Smoke Test Sequence

1. ✅ Start HiveBoard server (or confirm cloud endpoint is reachable)
2. ✅ Run your agent with `hiveloop_hooks()`
3. ✅ Agent card appears in Fleet View with green heartbeat
4. ✅ Task appears in Task Table with status `processing`
5. ✅ Click task → Timeline shows tool call nodes with durations
6. ✅ Task completes → status `completed`, duration populated
7. ✅ Activity Stream shows chronological event feed
8. ✅ Kill the agent process → wait 5 min → card shows STUCK
9. 🎉 **Integration is working**

---

## 11. Troubleshooting

### Agent doesn't appear in Fleet View

| Check | How |
|-------|-----|
| API key valid? | Must start with `hb_`. Try `curl -H "Authorization: Bearer hb_live_xxx" https://your-endpoint/v1/agents` |
| Endpoint reachable? | Set `debug=True` and check stderr for connection errors |
| Hooks actually passed? | Make sure `hooks=hiveloop_hooks(...)` is inside `ClaudeAgentOptions()`, not as a kwarg to `query()` |
| Agent SDK supports hooks? | Hooks were added in the Claude Agent SDK — make sure you're on a recent version |

### Timeline is empty (no action nodes)

| Check | How |
|-------|-----|
| Tools allowed? | `allowed_tools` must include at least one tool for actions to fire |
| Agent actually using tools? | Some prompts may be answered without tool use — try a prompt that requires reading files |
| `PreToolUse` firing? | Set `debug=True` — you should see log lines for each hook callback |

### Events not showing in Activity Stream

| Check | How |
|-------|-----|
| Flush happening? | Events batch every 5 seconds. Wait at least 5 seconds after the action. |
| WebSocket connected? | Check the browser console on the dashboard for WS connection errors |
| Correct project filter? | Activity Stream filters by project — make sure you're viewing the right one (or "All") |

### Stuck detection not working

| Check | How |
|-------|-----|
| Heartbeat interval too high? | Default is 30s, stuck threshold is 300s (5 min). After killing the process, wait the full 5 minutes. |
| Clock skew? | The backend compares `last_heartbeat` to server time. Large clock differences between your machine and the backend can cause issues. |

### Debug mode

When things aren't working, enable debug logging:

```python
hooks = hiveloop_hooks(
    api_key="hb_live_xxx",
    debug=True,  # Logs all hook callbacks, event enqueues, and transport activity to stderr
)
```

You'll see output like:

```
[HiveLoop DEBUG] SessionStart hook fired, session_id=session_a1b2c3d4
[HiveLoop DEBUG] Agent registered: bug-fixer (claude-agent-sdk)
[HiveLoop DEBUG] Task started: session_a1b2c3d4
[HiveLoop DEBUG] PreToolUse: Read, tool_use_id=tu_001
[HiveLoop DEBUG] Action started: Read:auth.py
[HiveLoop DEBUG] PostToolUse: Read, tool_use_id=tu_001, duration=120ms
[HiveLoop DEBUG] Action completed: Read:auth.py (120ms)
[HiveLoop DEBUG] Flush: 4 events → POST /v1/ingest (200 OK)
```

---

## 12. FAQ

### Does this slow down my agent?

No. Hook callbacks are async and run in microseconds (dict lookups + event enqueuing). Event delivery happens in a background thread. The Agent SDK's tool execution is not blocked or delayed.

### What if HiveBoard is down?

Your agent runs exactly the same. The HiveLoop transport retries with exponential backoff and eventually drops events if the backend is unreachable. No exceptions are raised to your code. When HiveBoard comes back, new events flow normally — the gap period is simply missing data.

### Can I use this in production?

Yes. The integration follows the same safety invariants as the core HiveLoop SDK: it never crashes the host application, never blocks the main execution path, and never modifies the Agent SDK's behavior. The hooks always return `{}`.

### Does this work with TypeScript?

A TypeScript version (`hiveloopHooks()`) is on the roadmap. For now, the integration is Python-only.

### Can I filter what gets tracked?

Not at the hook level currently — all tools are tracked. You can filter what you *see* on the dashboard using project, agent, and event type filters. If you need to exclude specific tools from tracking, you can use a custom `PreToolUse` hook that sets a flag, and modify the HiveLoop hook to skip those tools.

### How do I track multiple agents in a fleet?

Call `hiveloop_hooks()` separately for each agent with a different `agent_name`. They can share the same `api_key` and `project`:

```python
# All agents share the same project, each gets a unique card
agent_configs = [
    {"name": "researcher", "type": "research"},
    {"name": "writer", "type": "content"},
    {"name": "reviewer", "type": "quality"},
]

for config in agent_configs:
    hooks = hiveloop_hooks(
        api_key="hb_live_xxx",
        project="content-pipeline",
        agent_name=config["name"],
        agent_type=config["type"],
    )
    # Pass hooks to each agent's query() call
```

All three appear in Fleet View as separate cards under the `content-pipeline` project.

### What's the difference between this and the LangChain integration?

The LangChain integration uses LangChain's callback system (`on_llm_start`, `on_tool_end`, etc.). This integration uses the Claude Agent SDK's hook system (`SessionStart`, `PreToolUse`, `PostToolUse`, etc.). Same HiveBoard experience, different framework plumbing.

The key difference: the LangChain integration gets automatic LLM cost tracking (via `on_llm_end`), while the Agent SDK integration requires `instrumented_query()` for costs because the SDK handles LLM calls internally.

### I'm already using the core HiveLoop SDK. Do I still need this?

If you've already instrumented your agent with `hiveloop.init()`, `hb.agent()`, `agent.task()`, and `@agent.track()` — you don't need this integration. It's designed for developers who want observability without writing manual instrumentation code.

However, if you're already using HiveLoop and want to *also* add Agent SDK hooks (e.g., to auto-track tool calls that your manual instrumentation doesn't cover), you can combine them. Just make sure both use the same `api_key` and `agent_name` so events land on the same agent card.

---

## What's Next

- **Add rich events** — See [Section 9](#9-adding-rich-events-going-deeper) for plans, retries, issues, and custom telemetry
- **Explore the dashboard** — The [Dashboard Guide](https://hiveboard.net/docs/user-manual.html) covers every screen in detail
- **Join the community** — Share what you're building and get help at [hiveboard.net/community](https://hiveboard.net/community)

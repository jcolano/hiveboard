# HiveBoard Integration Guide for Claude Agent SDK

**A layer-by-layer guide to making Claude Agent SDK agents observable with HiveBoard.**

> **Companion script:** [`hiveboard_claude_agent_sdk_demo.py`](./hiveboard_claude_agent_sdk_demo.py) — a runnable demo covering every layer described here. Read the guide first, then run the script to see it in action.

---

## What This Guide Covers

The Claude Agent SDK lets you build AI agents that read files, run commands, search the web, and use custom tools — all powered by Claude Code's agent loop. HiveBoard makes those agents observable: live heartbeats, task timelines, LLM cost tracking, tool execution visibility, and fleet monitoring.

This guide shows you how to wire them together. Each layer is independent — stop at any layer and you have working observability.

| Layer | What You Add | What the Dashboard Shows | Effort |
|-------|-------------|------------------------|--------|
| 0 | HiveLoop init + agent registration | Heartbeat, online/offline, stuck detection | ~5 lines |
| 1 | Task wrapping around `query()` calls | Task table, timelines, success/failure | ~15 lines |
| 2a | `ResultMessage` extraction → `task.llm_call()` | Cost Explorer, token usage, model breakdown | ~20 lines |
| 2b | Hooks on `ClaudeSDKClient` | Automatic tool tracking with timing | ~30 lines |
| 2c | Custom MCP tools with business events | Rich narrative in timeline | ~10 lines per tool |

---

## Prerequisites

```bash
pip install hiveloop
pip install claude-agent-sdk
```

You need:
- A HiveBoard server (self-hosted or cloud) with an API key (`hb_live_...`)
- A project created on the server
- `ANTHROPIC_API_KEY` in your environment (for the Claude Agent SDK)

---

## Key Concept: The ResultMessage

The Claude Agent SDK emits a `ResultMessage` at the end of every `query()` call. This is **the single most important object** for HiveBoard integration — it contains everything:

```python
@dataclass
class ResultMessage:
    total_cost_usd: float | None   # Total cost for the entire agent run
    usage: dict[str, Any] | None   # Token breakdown including cache tokens
    duration_ms: int               # Total wall-clock time
    duration_api_ms: int           # Time spent in API calls
    num_turns: int                 # Agent loop iterations
    is_error: bool                 # Success/failure
    session_id: str                # Session identifier
```

The `usage` dict has a specific structure:

```python
{
    "input_tokens": 3,                       # Non-cached input tokens
    "cache_creation_input_tokens": 2033,     # Tokens written to cache
    "cache_read_input_tokens": 15272,        # Tokens read from cache
    "output_tokens": 10,                     # Output tokens
    "server_tool_use": {...},                # Web search/fetch counts
}
```

**Total input tokens = `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`.**

The SDK handles cache pricing internally, so `total_cost_usd` is already accurate — you don't need your own cost estimator.

---

## Windows + Jupyter Note

The Claude Agent SDK spawns subprocesses, which Jupyter's default event loop on Windows doesn't support. If you're running in Jupyter on Windows, use this helper:

```python
import asyncio
import threading

def run_async(coro):
    """Run async code in a thread with ProactorEventLoop (Windows subprocess support)."""
    result = None
    exception = None

    def _thread_target():
        nonlocal result, exception
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(coro)
        except Exception as e:
            exception = e
        finally:
            loop.close()

    t = threading.Thread(target=_thread_target)
    t.start()
    t.join()

    if exception:
        raise exception
    return result
```

In regular Python scripts (not Jupyter), use `asyncio.run()` normally.

---

## Layer 0: Init + Agent Registration

Get agents visible on the dashboard with heartbeats and stuck detection.

```python
import hiveloop

hb = hiveloop.init(
    api_key="hb_live_your_key_here",
    endpoint="https://your-hiveboard-server.com",
    environment="production",
)

agent = hb.agent(
    agent_id="my-claude-agent",
    type="research",               # Role classification
    version="1.0.0",               # Your agent's version
    framework="claude-agent-sdk",   # Framework identifier
    heartbeat_interval=30,          # Seconds between heartbeats
    stuck_threshold=300,            # Seconds before "stuck" badge
)
```

**What you see on HiveBoard:** Agent card in Fleet View with a live heartbeat indicator. If the agent stops, it shows as "stuck" after 5 minutes.

---

## Layer 1: Task Wrapping

Wrap each `query()` call in a HiveLoop task context. This gives you task timelines with start/complete/fail lifecycle.

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, ResultMessage, TextBlock

with agent.task("task-001", project="my-project", type="research") as task:
    async for message in query(prompt="What is quantum computing?"):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)
        elif isinstance(message, ResultMessage):
            result_msg = message
```

The `with` block automatically emits `task_started` and `task_completed`/`task_failed` events. If an exception occurs, the task is marked as failed.

---

## Layer 2a: LLM Cost + Token Tracking

Extract usage data from `ResultMessage` and report it to HiveLoop.

```python
def extract_usage(result_msg):
    """Extract total token counts from ResultMessage.
    
    The usage dict includes cache tokens which must be summed
    for accurate total input token count.
    """
    if not result_msg:
        return {"tokens_in": None, "tokens_out": None, "cost": None}

    usage = result_msg.usage or {}
    tokens_in = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )
    tokens_out = usage.get("output_tokens", 0)
    cost = result_msg.total_cost_usd

    return {"tokens_in": tokens_in, "tokens_out": tokens_out, "cost": cost}
```

Report it inside the task context:

```python
with agent.task("task-001", project="my-project", type="research") as task:
    t_start = time.perf_counter()
    model_name = None
    result_msg = None

    async for message in query(prompt="Explain quantum computing"):
        if isinstance(message, AssistantMessage):
            model_name = getattr(message, 'model', None) or model_name
            # ... handle text/tool blocks ...
        elif isinstance(message, ResultMessage):
            result_msg = message

    duration_ms = (time.perf_counter() - t_start) * 1000
    u = extract_usage(result_msg)

    task.llm_call(
        "research-query",                    # Descriptive name (NOT the model name)
        model=model_name or "unknown",
        tokens_in=u["tokens_in"],
        tokens_out=u["tokens_out"],
        cost=u["cost"],
        duration_ms=round(duration_ms),
        prompt_preview=prompt[:300],          # Optional — truncate aggressively
        response_preview=collected_text[:500], # Optional — consider PII
    )
```

**What you see on HiveBoard:** Cost Explorer with per-agent, per-model breakdowns. Purple LLM nodes in the timeline with token counts and cost badges.

---

## Layer 2b: Automatic Tool Tracking via Hooks

This is where `ClaudeSDKClient` unlocks deeper observability. Hooks intercept **every** tool call — built-in tools (Bash, Read, Write) and custom MCP tools — with precise start/end timing.

```python
import time
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, HookMatcher

# Shared state for hooks
_tool_timings = {}
_hook_task = None  # Current HiveLoop task (set before each run)


async def pre_tool_hook(input_data, tool_use_id, context):
    """Fires BEFORE every tool call. Records start time."""
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})

    _tool_timings[tool_use_id] = {
        "name": tool_name,
        "start": time.perf_counter(),
    }

    if _hook_task:
        _hook_task.event("custom", {
            "kind": "tool_start",
            "tool": tool_name,
            "tool_use_id": tool_use_id,
            "input_preview": str(tool_input)[:300],
        })

    return {}  # Don't modify tool execution


async def post_tool_hook(input_data, tool_use_id, context):
    """Fires AFTER every tool call. Calculates duration."""
    tool_name = input_data.get("tool_name", "unknown")
    tool_output = str(input_data.get("tool_result", ""))

    timing = _tool_timings.pop(tool_use_id, None)
    duration_ms = round((time.perf_counter() - timing["start"]) * 1000) if timing else 0
    is_error = "error" in tool_output.lower() or "traceback" in tool_output.lower()

    if _hook_task:
        _hook_task.event("custom", {
            "kind": "tool_complete",
            "tool": tool_name,
            "tool_use_id": tool_use_id,
            "duration_ms": duration_ms,
            "is_error": is_error,
            "output_preview": tool_output[:200],
        })

    return {}  # Don't modify result
```

Wire hooks into `ClaudeAgentOptions`:

```python
options = ClaudeAgentOptions(
    allowed_tools=["Bash", "Read", "Write"],
    permission_mode="acceptEdits",
    max_turns=10,
    hooks={
        "PreToolUse": [
            HookMatcher(matcher="*", hooks=[pre_tool_hook]),
        ],
        "PostToolUse": [
            HookMatcher(matcher="*", hooks=[post_tool_hook]),
        ],
    },
)
```

Use with `ClaudeSDKClient`:

```python
with agent.task("task-002", project="my-project", type="coding") as task:
    _hook_task = task  # Make task available to hooks

    async with ClaudeSDKClient(options=options) as client:
        await client.query("Create a hello.py file and run it")

        async for message in client.receive_response():
            # ... handle messages ...
            pass

    _hook_task = None
```

**What you see on HiveBoard:** Paired `tool_start`/`tool_complete` events in the timeline with per-tool duration (e.g., Bash: 2206ms, lookup_customer: 309ms). Errors highlighted in red.

---

## Layer 2c: Custom MCP Tools with Business Events

Custom tools can emit domain-specific events directly into HiveLoop from inside the tool handler. This adds business context that goes beyond generic tool tracking.

```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("lookup_customer", "Look up customer by name", {"name": str})
async def lookup_customer(args):
    name = args["name"]
    customer = db.find_customer(name)  # Your business logic

    # Emit business event to HiveLoop
    if _hook_task:
        _hook_task.event("custom", {
            "kind": "customer_lookup",
            "customer_id": customer["id"],
            "plan": customer["plan"],
        })

    return {"content": [{"type": "text", "text": f"Found: {customer}"}]}


server = create_sdk_mcp_server(
    name="my-tools",
    version="1.0.0",
    tools=[lookup_customer],
)

options = ClaudeAgentOptions(
    mcp_servers={"tools": server},
    allowed_tools=["mcp__tools__lookup_customer"],
    hooks={...},  # Same hooks from Layer 2b
)
```

**What you see on HiveBoard:** Business events like `customer_lookup`, `churn_risk_calculated`, `alert_sent` appear in the Activity Stream and Timeline alongside the generic tool events.

---

## Multi-Agent Fleet Pattern

Register multiple agents with different roles to validate the Fleet View:

```python
agent_researcher = hb.agent(agent_id="researcher", type="research", ...)
agent_coder = hb.agent(agent_id="coder", type="engineering", ...)
agent_analyst = hb.agent(agent_id="analyst", type="analytics", ...)
```

Each agent manages its own tasks independently. HiveBoard shows them all in the Fleet View with per-agent cost breakdowns in the Cost Explorer.

---

## Event Types

HiveBoard has a fixed set of recognized `event_type` values. When emitting custom events, always use `event_type="custom"` and put your event name in the payload's `kind` field:

```python
# ✅ Correct
task.event("custom", {"kind": "tool_start", "tool": "Bash"})

# ❌ Wrong — will be rejected as invalid_event_type
task.event("tool_start", {"tool": "Bash"})
```

---

## Troubleshooting

These are real issues encountered during integration testing — not hypothetical.

### Events ingested but invisible on dashboard

**Symptom:** Server returns `200 OK`, events appear in `/v1/events` API, but the dashboard shows nothing.

**Cause:** Environment mismatch. HiveLoop was initialized with `environment="development"` but the dashboard filters by `environment=production`.

**Fix:** Make sure `hiveloop.init(environment=...)` matches the dashboard's environment filter. Use `"production"` for the main dashboard view.

### Token count is absurdly low (e.g., `input_tokens: 3`)

**Symptom:** `extract_usage()` returns very low input token counts that don't match the cost.

**Cause:** Claude's prompt caching splits input tokens across three fields. The `input_tokens` field only counts non-cached tokens.

**Fix:** Sum all three: `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`. This is already handled by the `extract_usage()` helper in the demo script.

### `invalid_event_type` rejection

**Symptom:** Server returns `200` but response body contains `"partially_rejected"` with reason `"invalid_event_type"`.

**Cause:** HiveBoard only accepts specific event types (`custom`, `task_started`, `task_completed`, `llm_call`, etc.). Sending `event_type="tool_start"` or `event_type="agent_run_summary"` gets rejected.

**Fix:** Always use `task.event("custom", {"kind": "your_event_name", ...})`. Put your event classification in the payload's `kind` field, not in the event type.

### `NotImplementedError` in Jupyter on Windows

**Symptom:** `NotImplementedError` from `asyncio.create_subprocess_exec()` when calling `query()` or `ClaudeSDKClient`.

**Cause:** Jupyter's default `SelectorEventLoop` on Windows doesn't support subprocess spawning. The Claude Agent SDK wraps Claude Code CLI as a subprocess.

**Fix:** Use the `run_async()` helper from this guide (runs in a separate thread with `ProactorEventLoop`), or run from a regular Python script instead of Jupyter.

### `ModuleNotFoundError: No module named 'hiveloop'`

**Symptom:** `pip install hiveloop` succeeds, but `import hiveloop` fails.

**Cause:** Multiple Python installations. Pip installed to Python 3.13 but Jupyter kernel runs Python 3.11.

**Fix:** In Jupyter, always use `!{sys.executable} -m pip install hiveloop` to target the kernel's Python. In regular scripts, ensure you're using the same Python that pip installs to.

### Events appear after refresh but not in real-time

**Symptom:** Events show up on the dashboard only after manual page refresh, not via WebSocket stream.

**Cause:** HiveLoop batches events and flushes every ~5 seconds. If you check the dashboard before the flush fires, events aren't there yet.

**Fix:** Call `hiveloop.flush()` after time-critical operations. Add a `time.sleep(2)` before checking the dashboard in automated tests. The real-time WebSocket feed will update once the batch is ingested.

---

## Complete Example

See [`hiveboard_claude_agent_sdk_demo.py`](./hiveboard_claude_agent_sdk_demo.py) for the full runnable demo. It demonstrates all layers end-to-end with two agents, hook-based tool tracking, and custom MCP tools.

Run it:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export HIVEBOARD_API_KEY="hb_live_..."
export HIVEBOARD_ENDPOINT="http://localhost:8451"
export HIVEBOARD_PROJECT="claude-agent-sdk-demo"

python hiveboard_claude_agent_sdk_demo.py
```

Then open HiveBoard and verify:

| Dashboard View | What You Should See |
|---------------|-------------------|
| Fleet | 2 agents online: demo-researcher, demo-coder |
| Task Table | 3 tasks: research, coding, churn-analysis |
| Timeline | Purple LLM nodes, blue tool events, business events |
| Cost Explorer | Per-agent, per-model cost breakdown |
| Activity Stream | tool_start, tool_complete, customer_lookup, churn_risk_calculated, alert_sent |

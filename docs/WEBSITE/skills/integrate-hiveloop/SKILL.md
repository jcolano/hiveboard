---
name: integrate-hiveloop
description: Integrate HiveLoop SDK into an agentic application for observability. Use when the user wants to add HiveBoard monitoring, instrument their AI agent, or connect their agent framework to HiveLoop.
disable-model-invocation: true
argument-hint: <api_key>
---

# Integrate HiveLoop SDK

You are integrating the **HiveLoop** Python SDK into the user's agentic application.
HiveLoop instruments AI agents so their activity streams to **HiveBoard**, an observability
platform ("Datadog for agents"). The SDK captures identity, state, and activity via 28 sensors.

**API key**: `$ARGUMENTS`

If no API key was provided, ask the user for one. They get it by registering at
https://hiveboard.net, then copying the default API key from the API Keys panel
(or creating a new one).

---

## Phase 1 — Discover the codebase

Before writing any code, scan the codebase to understand its structure. Find answers to:

1. **Framework**: Is this LangChain, CrewAI, Autogen, Semantic Kernel, a custom loop, or something else?
2. **Entry point**: Where does the application start? (`main()`, CLI command, server handler, Lambda)
3. **Agent creation**: Where are agent instances created or initialized?
4. **Main loop / work unit**: What constitutes a single "task"? (one user message, one pipeline run, one batch item)
5. **Tool functions**: Which functions does the LLM call as tools? (search, API calls, database queries)
6. **LLM calls**: Where does the code call an LLM API? (OpenAI, Anthropic, LiteLLM, framework-internal)
7. **Error handling**: Where are exceptions caught, retried, or escalated?
8. **Shutdown**: Is there a graceful shutdown path? (`atexit`, signal handler, framework hook)
9. **Dependencies file**: `requirements.txt`, `pyproject.toml`, `setup.py`, `Pipfile`, or `poetry.lock`

Use Glob and Grep to scan efficiently:
- `**/*.py` for Python files
- Search for `openai`, `anthropic`, `litellm`, `langchain`, `crewai`, `autogen` in imports
- Search for `def main`, `if __name__`, `app.run`, `uvicorn`
- Search for `@tool`, `def tool_`, `tools=`, `functions=` for tool definitions
- Search for `chat.completions`, `messages.create`, `invoke`, `run` for LLM calls

---

## Phase 2 — Add the dependency

Add `hiveloop` to the project's dependency file:

- **requirements.txt**: Add `hiveloop` on its own line
- **pyproject.toml**: Add `"hiveloop"` to the `dependencies` list
- **setup.py**: Add `"hiveloop"` to `install_requires`

---

## Phase 3 — Instrument (minimum viable)

These 5 integration points give the user immediate value in HiveBoard. Apply them in order.
For detailed function signatures, see [sdk-quick-reference.md](sdk-quick-reference.md).

### 3.1 — SDK Init (entry point)

At the top of the application's entry point, before any agent runs:

```python
import hiveloop

hb = hiveloop.init(
    api_key="<THE_API_KEY>",
    environment="production",  # or "development", "staging"
)
```

Place this in `main()`, the app factory, or the CLI `run` command — wherever the process starts.

### 3.2 — Agent Registration (agent creation)

Where the agent is created or initialized:

```python
agent = hb.agent(
    "<agent_id>",            # unique name, e.g. "sales-agent"
    type="<agent_type>",     # e.g. "sales", "support", "coder"
    framework="<framework>", # e.g. "langchain", "crewai", "custom"
    version="1.0.0",         # agent implementation version
)
```

Use a stable, descriptive `agent_id`. If the framework creates multiple agents, register each one.

### 3.3 — Task Wrapping (main work unit)

Wrap each unit of work with `agent.task()`. A "task" answers: "what was the agent asked to do?"

```python
with agent.task(task_id, project="<project>", type="<task_type>") as task:
    # ... all existing work happens here ...
    # events inside this block automatically inherit task context
```

Framework mapping:
- **Chat agent**: one task per user message or conversation turn
- **Batch processor**: one task per item in the batch
- **Pipeline agent**: one task per pipeline execution
- **CrewAI**: one task per `Task` in the `Crew`
- **LangChain**: one task per `AgentExecutor.invoke()` or chain run

### 3.4 — LLM Call Tracking (after every LLM API call)

After each LLM API response, record the call:

```python
task.llm_call(
    name="<purpose>",          # "reason", "plan", "summarize", "classify"
    model="<model_id>",        # "claude-sonnet-4-20250514", "gpt-4o", etc.
    tokens_in=response.usage.input_tokens,
    tokens_out=response.usage.output_tokens,
    cost=calculated_cost,      # USD, or None if unknown
    duration_ms=elapsed_ms,
)
```

This is the highest-value sensor — it powers cost tracking, latency monitoring, and token analysis.

If the code uses a framework that wraps LLM calls internally (LangChain callbacks, CrewAI hooks),
find the callback/hook point and add `task.llm_call()` there.

If `task` isn't in scope, use `agent.llm_call()` instead (same signature, works outside a task).

### 3.5 — Shutdown (application exit)

At the application's shutdown path:

```python
hiveloop.shutdown()
```

Place in `atexit` handlers, signal handlers (`SIGTERM`), framework shutdown hooks, or at the
end of `main()`. The SDK also registers its own `atexit` handler, but explicit shutdown gives
better control.

---

## Phase 4 — Instrument (enhanced)

After the minimum viable integration works, layer in these sensors for richer observability.
Only add what's relevant to the codebase — not every agent needs every sensor.

### 4.1 — Action Tracking (tool functions & key steps)

Decorate significant functions with `@agent.track()`:

```python
@agent.track("search_database")
async def search(query: str) -> list:
    ...
```

Good candidates: tool functions, API calls, data retrieval, post-processing, sub-agent delegation.
These become the "verbs" in HiveBoard's action tree.

### 4.2 — Plan Tracking (if the agent plans)

When the agent creates a plan:

```python
task.plan(goal="Process the invoice", steps=["Extract data", "Validate", "Submit"])
```

As each step executes:

```python
task.plan_step(0, "started", "Extracting invoice data")
# ... execute step ...
task.plan_step(0, "completed", "Extracted 12 fields")
```

### 4.3 — Issue Reporting (error & degradation tracking)

For problems that aren't task failures but degrade capability:

```python
agent.report_issue(
    "CRM API rate limited",
    severity="medium",
    category="rate_limit",
)
```

Good places: HTTP client error handlers, retry logic, data validation failures, LLM refusals.

### 4.4 — Approval Flow (human-in-the-loop)

If the agent needs human approval before high-impact actions:

```python
task.request_approval("About to send email to 500 customers", approver="ops-team")
# ... wait for human ...
task.approval_received("Approved by Jane", approved_by="jane@co.com", decision="approved")
```

### 4.5 — Logging Bridge (catch-all)

Forward Python WARNING+ logs as agent issues automatically:

```python
import logging
from hiveloop.contrib.log_handler import HiveBoardLogHandler

logging.getLogger().addHandler(HiveBoardLogHandler(agent))
```

This catches unexpected errors from the app and all libraries without explicit instrumentation.

### 4.6 — Custom Heartbeat Payload (framework-specific state)

Expose internal state on every heartbeat cycle:

```python
agent = hb.agent(
    "my-agent",
    heartbeat_payload=lambda: {
        "memory_items": len(agent.memory),
        "context_usage": 0.73,
        "active_tools": ["search", "calc"],
    },
)
```

### 4.7 — Todo Tracking (sub-task progress)

Track work items the agent manages:

```python
agent.todo("todo-1", "created", "Search the database", priority="high")
# ... later ...
agent.todo("todo-1", "completed", "Found 3 matching records")
```

---

## Phase 5 — Verify

After making all changes:

1. Check imports are correct and not circular
2. Ensure `hiveloop.init()` runs before any `hb.agent()` call
3. Ensure `agent.task()` wraps the work unit (not too wide, not too narrow)
4. Ensure `task.llm_call()` has access to the response's token counts
5. Ensure `hiveloop.shutdown()` runs on exit
6. List all files modified and summarize what was instrumented

Tell the user:
> HiveLoop integration complete. Start your agent and open your HiveBoard dashboard
> to see live telemetry. The minimum integration captures agent status, task lifecycle,
> and LLM costs. You can enhance it further by adding action tracking, plan tracking,
> and issue reporting as described in the enhanced instrumentation phase.

---

## Additional resources

- For complete SDK function signatures and parameters, see [sdk-quick-reference.md](sdk-quick-reference.md)
- For a concrete before/after code example, see [examples/before-after.py](examples/before-after.py)

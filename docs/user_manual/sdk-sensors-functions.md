# Agent Sensors — Detailed Function Reference

For each sensor, this document maps:
- **SDK Function** — exact call signature
- **Parameters** — all arguments with types and defaults
- **When to Use** — the condition or moment in agent execution
- **Integration Point** — where to wire this into a typical agentic framework

---

## Group 1 — Identity Sensors

These sensors capture *what the agent is*. They are set once at initialization time.

---

### 1.1 `hiveloop.init()` — SDK Initialization

| | |
|---|---|
| **Function** | `hiveloop.init(api_key, environment, group, endpoint, flush_interval, batch_size, max_queue_size, debug) -> HiveBoard` |
| **Source** | `src/sdk/hiveloop/__init__.py:163` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `api_key` | `str` | *(required)* | Must start with `"hb_"`. Authenticates all events. |
| `environment` | `str` | `"production"` | Deployment stage: `dev`, `staging`, `production` |
| `group` | `str` | `"default"` | Team/service grouping for filtering |
| `endpoint` | `str \| None` | Auto-resolved | Backend URL. Resolved from `loophive.cfg` or default. |
| `flush_interval` | `float` | `5.0` | Seconds between background flushes |
| `batch_size` | `int` | `100` | Max events per HTTP POST |
| `max_queue_size` | `int` | `10_000` | Bounded queue capacity before dropping |
| `debug` | `bool` | `False` | Enable `DEBUG`-level SDK logging |

**When to use**: Once, at application startup, before any agent is created.

**Integration point**: Place in the agentic framework's bootstrap/entry point (e.g. `main()`, app factory, or CLI `run` command). This is the first line of instrumentation. All transport config (batching, queue limits) is set here and inherited by every agent.

---

### 1.2 `hb.agent()` — Agent Registration

| | |
|---|---|
| **Function** | `hb.agent(agent_id, type, version, framework, heartbeat_interval, stuck_threshold, heartbeat_payload, queue_provider) -> Agent` |
| **Source** | `src/sdk/hiveloop/__init__.py:106` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str` | *(required)* | Unique identifier (max 256 chars) |
| `type` | `str` | `"general"` | Classification: `"sales"`, `"support"`, `"coder"`, etc. |
| `version` | `str \| None` | `None` | Semantic version of the agent implementation |
| `framework` | `str` | `"custom"` | Framework name: `"langchain"`, `"crewai"`, `"autogen"`, etc. |
| `heartbeat_interval` | `float` | `30.0` | Seconds between heartbeat emissions |
| `stuck_threshold` | `int` | `300` | Seconds of inactivity before marked `stuck` |
| `heartbeat_payload` | `Callable[[], dict \| None] \| None` | `None` | Custom state callback, invoked every heartbeat |
| `queue_provider` | `Callable[[], dict \| None] \| None` | `None` | Work queue state callback, emits `queue_snapshot` |

**When to use**: Once per distinct agent. Idempotent — calling again with the same `agent_id` updates metadata and returns the existing instance.

**Integration point**: In the framework's agent factory or constructor. When the framework creates an agent instance (e.g. `CrewAgent(...)`, `LangchainAgent(...)`), call `hb.agent()` immediately after to register it. The `heartbeat_payload` and `queue_provider` callbacks are ideal for exposing framework-internal state (e.g. LangChain memory contents, CrewAI task queue) without modifying the framework's core.

**Auto-emitted events**:
- `agent_registered` — immediately upon creation
- `heartbeat` — every `heartbeat_interval` seconds (background thread)

---

### 1.3 `hb.get_agent()` — Agent Lookup

| | |
|---|---|
| **Function** | `hb.get_agent(agent_id) -> Agent \| None` |
| **Source** | `src/sdk/hiveloop/__init__.py:148` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent_id` | `str` | *(required)* | The ID to look up |

**When to use**: When you need to retrieve a previously registered agent by ID (e.g. in a different module or handler).

**Integration point**: Utility endpoints, health checks, or middleware that need to emit events on behalf of an existing agent without holding a direct reference.

---

## Group 2 — State Sensors

These sensors capture mutable, point-in-time snapshots of *what the agent has*.

---

### 2.1 `heartbeat_payload` callback — Custom Agent State

| | |
|---|---|
| **Function** | User-provided `Callable[[], dict[str, Any] \| None]` passed to `hb.agent()` |
| **Source** | `src/sdk/hiveloop/_agent.py:659-678` |

The callback receives no arguments and returns a dict (or `None` to skip). It is invoked automatically on every heartbeat cycle.

**Example return value**:
```python
{
    "memory_items": 42,
    "context_window_usage": 0.73,
    "active_tools": ["search", "calculator"],
    "model": "claude-sonnet-4-20250514",
}
```

**When to use**: Always. This is the primary way to expose arbitrary internal state from your agent framework without modifying the framework's code.

**Integration point**: Register a lambda or function that reads from the framework's runtime state. Examples:
- **LangChain**: Return `{"memory_size": len(agent.memory.buffer), "chain_type": agent.chain.chain_type}`
- **CrewAI**: Return `{"crew_size": len(crew.agents), "completed_tasks": crew.completed}`
- **Autogen**: Return `{"chat_history_len": len(agent.chat_messages), "model": agent.llm_config["model"]}`
- **Custom loops**: Return iteration count, token budget remaining, error rate, etc.

---

### 2.2 `agent.queue_snapshot()` — Work Queue State (explicit)

| | |
|---|---|
| **Function** | `agent.queue_snapshot(depth, *, oldest_age_seconds, items, processing)` |
| **Source** | `src/sdk/hiveloop/_agent.py:985` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `depth` | `int` | *(required)* | Number of items in the queue |
| `oldest_age_seconds` | `int \| None` | `None` | Age in seconds of the oldest item |
| `items` | `list[dict] \| None` | `None` | Queue items. Each: `{id, priority, source, summary, queued_at}` |
| `processing` | `dict \| None` | `None` | Currently processing item: `{id, summary, started_at, elapsed_ms}` |

**Emits**: `custom` event with `kind=queue_snapshot`, tags `["queue"]`

**When to use**: Call explicitly whenever the queue state changes significantly (item added, item completed, queue drained). Also available automatically via `queue_provider` callback on heartbeat.

**Integration point**: Wrap the framework's task/message queue. In frameworks with explicit work queues (e.g. Celery workers, BullMQ consumers, custom priority queues), call this after every enqueue/dequeue operation, or register `queue_provider` for periodic polling.

---

### 2.3 `queue_provider` callback — Work Queue State (automatic)

| | |
|---|---|
| **Function** | User-provided `Callable[[], dict[str, Any] \| None]` passed to `hb.agent()` |
| **Source** | `src/sdk/hiveloop/_agent.py:680-706` |

Same schema as `agent.queue_snapshot()` but called automatically on each heartbeat. Return `None` to skip.

**Expected return schema**:
```python
{
    "depth": 5,
    "oldest_age_seconds": 120,
    "items": [
        {"id": "msg-1", "priority": "high", "source": "user", "summary": "Process invoice", "queued_at": "2025-01-01T12:00:00Z"}
    ],
    "processing": {"id": "msg-0", "summary": "Analyzing doc", "started_at": "...", "elapsed_ms": 4500}
}
```

**When to use**: When the framework manages a durable work queue and you want periodic snapshots without explicit calls.

**Integration point**: Same as `heartbeat_payload` — register at agent creation. Best for frameworks where you don't control the dequeue loop (e.g. external message brokers).

---

### 2.4 `agent.todo()` — Todo/Pipeline State

| | |
|---|---|
| **Function** | `agent.todo(todo_id, action, summary, *, priority, source, context, due_by)` |
| **Source** | `src/sdk/hiveloop/_agent.py:1014` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `todo_id` | `str` | *(required)* | Stable identifier (used for grouping/dedup) |
| `action` | `str` | *(required)* | Lifecycle: `"created"`, `"completed"`, `"failed"`, `"dismissed"`, `"deferred"` |
| `summary` | `str` | *(required)* | Human-readable description |
| `priority` | `str \| None` | `None` | `"high"`, `"normal"`, `"low"` |
| `source` | `str \| None` | `None` | Origin: `"user"`, `"planner"`, `"escalation"` |
| `context` | `str \| None` | `None` | Additional context string |
| `due_by` | `str \| None` | `None` | ISO 8601 deadline |

**Emits**: `custom` event with `kind=todo`, tags `["todo", <action>]`

**When to use**: Whenever the agent creates, completes, fails, or defers a sub-task. Call with `action="created"` when a new work item appears, then `action="completed"` or `action="failed"` when it resolves.

**Integration point**: Hook into the framework's task planner or sub-task manager:
- **Tool use**: When an LLM decides to use a tool, `todo("tool-search-1", "created", "Search database")`. When the tool returns, `todo("tool-search-1", "completed", "Found 3 results")`.
- **Multi-step plans**: Each step in a plan becomes a todo. Track progress as steps complete.
- **Human-in-the-loop**: When a todo requires approval, emit `"deferred"` and pair with `task.request_approval()`.
- **Claude Code TodoWrite**: Map each TodoWrite item to an `agent.todo()` call.

---

### 2.5 `agent.scheduled()` — Scheduled Work State

| | |
|---|---|
| **Function** | `agent.scheduled(items)` |
| **Source** | `src/sdk/hiveloop/_agent.py:1048` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `items` | `list[dict]` | *(required)* | Each: `{id, name, next_run, interval, enabled, last_status}` |

**Emits**: `custom` event with `kind=scheduled`, tags `["scheduled"]`

**When to use**: When the agent has recurring/scheduled work (cron jobs, polling intervals, periodic checks). Call whenever the schedule changes or on each heartbeat.

**Integration point**: Agents with periodic behaviors:
- **Polling agents**: Report next poll time, interval, whether enabled
- **Batch processors**: Report next batch window
- **Monitoring agents**: Report check intervals and last check status
- Register via `heartbeat_payload` if the schedule is static, or call explicitly when it changes dynamically.

---

### 2.6 `agent.report_issue()` — Issue State (report)

| | |
|---|---|
| **Function** | `agent.report_issue(summary, severity, *, issue_id, category, context, occurrence_count)` |
| **Source** | `src/sdk/hiveloop/_agent.py:1071` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | `str` | *(required)* | Human-readable issue description (max 512 chars) |
| `severity` | `str` | *(required)* | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `issue_id` | `str \| None` | `None` | Stable ID for deduplication. Server auto-generates hash if absent. |
| `category` | `str \| None` | `None` | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"` |
| `context` | `dict \| None` | `None` | Arbitrary context (e.g. `{"endpoint": "...", "status": 503}`) |
| `occurrence_count` | `int \| None` | `None` | How many times observed (for batched/debounced reporting) |

**Emits**: `custom` event with `kind=issue`, tags `["issue", <category>]`

**When to use**: When the agent encounters a problem that isn't a task failure but degrades capability. Rate limit hits, API errors, data quality problems, permission denials.

**Integration point**: Wrap in error handlers and retry logic:
- **HTTP client middleware**: On 429/503 responses, report connectivity/rate_limit issues
- **Tool execution**: On permission errors, report permissions issues
- **Data pipeline**: On validation failures, report data_quality issues
- **LLM calls**: On model errors or safety refusals, report with relevant context
- **Python logging bridge**: Use `HiveBoardLogHandler` (see 3.8) for automatic forwarding

---

### 2.7 `agent.resolve_issue()` — Issue State (resolve)

| | |
|---|---|
| **Function** | `agent.resolve_issue(summary, *, issue_id)` |
| **Source** | `src/sdk/hiveloop/_agent.py:1106` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | `str` | *(required)* | Resolution description |
| `issue_id` | `str \| None` | `None` | Must match a previously reported issue |

**Emits**: `custom` event with `kind=issue`, `action=resolved`, tags `["issue", "resolved"]`

**When to use**: When a previously reported issue has been resolved (e.g. API recovered, permission granted, rate limit lifted).

**Integration point**: Pair with `report_issue()`. In retry logic: after a successful retry following a failure, call `resolve_issue()` with the same `issue_id`. In health checks: resolve issues when the health check passes again.

---

## Group 3 — Activity Sensors

These sensors capture *what the agent does* — its execution lifecycle, decisions, and traces.

---

### 3.1 `agent.task()` — Task Lifecycle (context manager)

| | |
|---|---|
| **Function** | `agent.task(task_id, project, type, task_run_id, correlation_id) -> Task` |
| **Source** | `src/sdk/hiveloop/_agent.py:716` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `task_id` | `str` | *(required)* | User-provided task identifier (max 256 chars) |
| `project` | `str \| None` | `None` | Project scope for grouping |
| `type` | `str \| None` | `None` | Classification: `"chat"`, `"analysis"`, `"code_gen"`, etc. |
| `task_run_id` | `str \| None` | Auto UUID4 | Unique per-execution run (allows re-runs of same task_id) |
| `correlation_id` | `str \| None` | `None` | Cross-agent/system correlation string |

**Auto-emits**: `task_started` on enter, `task_completed` on clean exit, `task_failed` on exception

**Usage pattern**:
```python
with agent.task("process-lead-42", project="sales", type="enrichment") as task:
    # all events within this block inherit task context
    task.llm_call(...)
    task.plan(...)
```

**When to use**: Wrap every top-level unit of work the agent performs. A "task" is the answer to "what was the agent asked to do?"

**Integration point**: This is the primary structural sensor — it defines the outermost boundary of agent work:
- **Chat agents**: One task per user conversation turn or message
- **Batch processors**: One task per item in the batch
- **Pipeline agents**: One task per pipeline execution
- **CrewAI**: One task per `Task` object in a `Crew`
- **LangChain**: One task per `AgentExecutor.invoke()` or chain run
- **Autogen**: One task per conversation or group chat round

---

### 3.2 `agent.start_task()` — Task Lifecycle (manual)

| | |
|---|---|
| **Function** | `agent.start_task(task_id, project, type, task_run_id, correlation_id) -> Task` |
| **Source** | `src/sdk/hiveloop/_agent.py:734` |

Same parameters as `agent.task()`. Returns a `Task` object that must be manually completed.

**Usage pattern**:
```python
task = agent.start_task("long-running-job")
try:
    result = do_work()
    task.complete(payload={"result": result})
except Exception as e:
    task.fail(exception=e)
```

**When to use**: When the task lifetime doesn't fit a `with` block — e.g. tasks that span multiple async callbacks, message handlers, or event-driven frameworks.

**Integration point**: Event-driven architectures where task start and completion happen in different handlers (e.g. WebSocket message → processing → response across multiple callbacks).

---

### 3.3 `task.complete()` / `task.fail()` — Manual Task Termination

| | |
|---|---|
| **Function** | `task.complete(status, payload)` / `task.fail(exception, payload)` |
| **Source** | `src/sdk/hiveloop/_agent.py:167, 173` |

| Method | Parameter | Type | Default | Description |
|--------|-----------|------|---------|-------------|
| `complete` | `status` | `str` | `"success"` | Custom status string |
| `complete` | `payload` | `dict \| None` | `None` | Additional completion data |
| `fail` | `exception` | `BaseException \| None` | `None` | Exception that caused the failure |
| `fail` | `payload` | `dict \| None` | `None` | Additional failure data |

**Emits**: `task_completed` or `task_failed` with `duration_ms` auto-calculated

**When to use**: Only with `start_task()` (manual lifecycle). The context manager form handles this automatically.

**Integration point**: In async/callback frameworks, call `task.complete()` in the success handler and `task.fail()` in the error handler.

---

### 3.4 `task.set_payload()` — Task Completion Data

| | |
|---|---|
| **Function** | `task.set_payload(payload)` |
| **Source** | `src/sdk/hiveloop/_agent.py:179` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `payload` | `dict[str, Any]` | *(required)* | Data to include in the completion event |

**When to use**: Inside a `with agent.task()` block, before exiting, to attach result data to the auto-generated `task_completed` event.

**Integration point**: Just before the task block exits, set payload with result metrics:
```python
with agent.task("analyze-doc") as task:
    result = analyze(doc)
    task.set_payload({"pages_processed": result.pages, "entities_found": len(result.entities)})
```

---

### 3.5 `@agent.track()` — Action Tracking (decorator)

| | |
|---|---|
| **Function** | `@agent.track(action_name)` — decorator for sync and async functions |
| **Source** | `src/sdk/hiveloop/_agent.py:782` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action_name` | `str` | *(required)* | Human-readable name for the action |

**Auto-emits**: `action_started` on entry (with `function` name), `action_completed` on return, `action_failed` on exception. All include `duration_ms`.

**Supports nesting**: Inner `@track` calls get `parent_action_id` linking to the outer action, forming a tree.

**Usage pattern**:
```python
@agent.track("search_database")
async def search(query: str) -> list:
    ...

@agent.track("enrich_lead")
def enrich(lead_id: str) -> dict:
    ...
```

**When to use**: On every significant function/method that represents a discrete step in the agent's execution. These are the "verbs" of what the agent does.

**Integration point**: Decorate the functions that the agentic loop calls:
- **Tool functions**: Every tool the LLM can call should be `@agent.track("tool_name")`
- **LLM call wrappers**: `@agent.track("llm_reason")` around the LLM API call
- **Data retrieval**: `@agent.track("fetch_customer")` around database/API lookups
- **Post-processing**: `@agent.track("format_response")` around output formatting
- **Sub-agent delegation**: `@agent.track("delegate_to_researcher")` around handoff calls

---

### 3.6 `agent.track_context()` — Action Tracking (context manager)

| | |
|---|---|
| **Function** | `agent.track_context(action_name) -> _ActionContext` |
| **Source** | `src/sdk/hiveloop/_agent.py:802` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action_name` | `str` | *(required)* | Human-readable action name |

**Context object method**: `ctx.set_payload(payload: dict)` — attach data to the action events.

**Auto-emits**: Same as `@agent.track()`: `action_started`, `action_completed`/`action_failed` with `duration_ms`.

**Usage pattern**:
```python
with agent.track_context("call_crm_api") as ctx:
    result = crm.search(query)
    ctx.set_payload(tool_payload(
        args={"query": query},
        result=result.data,
        success=result.ok,
        duration_ms=result.elapsed,
    ))
```

**When to use**: When you need to track an inline code block (not a standalone function), or when you need to attach dynamic payload data via `ctx.set_payload()`.

**Integration point**: Inside the agentic loop body for ad-hoc blocks:
- **Tool execution blocks**: Wrap the tool dispatch logic
- **Retry blocks**: Wrap each retry attempt
- **Multi-step inline logic**: Wrap significant subsections within a larger function

---

### 3.7 `tool_payload()` — Standardized Tool Payload Builder

| | |
|---|---|
| **Function** | `tool_payload(*, args, result, success, error, duration_ms, tool_category, http_status, result_size_bytes, args_max_len, result_max_len) -> dict` |
| **Source** | `src/sdk/hiveloop/_agent.py:1161` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `args` | `dict \| None` | `None` | Input arguments to the tool |
| `result` | `Any \| None` | `None` | Tool output (auto-truncated to string) |
| `success` | `bool` | `True` | Whether the tool call succeeded |
| `error` | `str \| None` | `None` | Error message if failed |
| `duration_ms` | `int \| None` | `None` | Execution time |
| `tool_category` | `str \| None` | `None` | Category: `"crm"`, `"database"`, `"api"`, `"file"` |
| `http_status` | `int \| None` | `None` | HTTP status code if applicable |
| `result_size_bytes` | `int \| None` | `None` | Size of the result data |
| `args_max_len` | `int` | `500` | Truncation limit for arg values |
| `result_max_len` | `int` | `1000` | Truncation limit for result |

**Returns**: A `dict` suitable for `ctx.set_payload()`.

**When to use**: Inside `agent.track_context()` blocks when tracking tool/API calls. Provides safe truncation and a consistent schema.

**Integration point**: Use with `track_context` for all tool calls:
```python
with agent.track_context("search_contacts") as ctx:
    result = crm.search(query)
    ctx.set_payload(tool_payload(
        args={"query": query},
        result=result.data,
        success=result.ok,
        tool_category="crm",
        http_status=result.status_code,
    ))
```

---

### 3.8 `task.llm_call()` / `agent.llm_call()` — LLM Call Tracking

| | |
|---|---|
| **Function** | `task.llm_call(name, model, *, tokens_in, tokens_out, cost, duration_ms, prompt_preview, response_preview, metadata)` |
| **Source** | `src/sdk/hiveloop/_agent.py:257` (task-scoped), `:946` (agent-scoped) |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str` | *(required)* | Call purpose: `"reason"`, `"plan"`, `"summarize"`, `"classify"` |
| `model` | `str` | *(required)* | Model ID: `"claude-sonnet-4-20250514"`, `"gpt-4o"`, etc. |
| `tokens_in` | `int \| None` | `None` | Input/prompt tokens |
| `tokens_out` | `int \| None` | `None` | Output/completion tokens |
| `cost` | `float \| None` | `None` | Cost in USD |
| `duration_ms` | `int \| None` | `None` | API call latency |
| `prompt_preview` | `str \| None` | `None` | Truncated prompt text |
| `response_preview` | `str \| None` | `None` | Truncated response text |
| `metadata` | `dict \| None` | `None` | Arbitrary extra data (temperature, stop_reason, etc.) |

**Emits**: `custom` event with `kind=llm_call`, tags `["llm"]`. Auto-generates summary like `"reason → claude-sonnet-4-20250514 (1200 in / 350 out, $0.008)"`.

**Two scopes**: `task.llm_call()` inherits task context; `agent.llm_call()` works without a task.

**When to use**: After every LLM API call. This is the most critical activity sensor for cost tracking, latency monitoring, and token budget management.

**Integration point**: Wrap or hook the LLM client:
- **Direct API calls**: Call immediately after the API response
- **LangChain**: Use a callback handler that calls `task.llm_call()` in `on_llm_end`
- **LiteLLM/OpenAI SDK**: Wrap the `completion()` call or use middleware
- **Anthropic SDK**: Read `response.usage` and `response.model` to populate fields
- This is the single most valuable sensor for observability dashboards (cost, latency, token trends)

---

### 3.9 `task.plan()` — Plan Creation

| | |
|---|---|
| **Function** | `task.plan(goal, steps, *, revision)` |
| **Source** | `src/sdk/hiveloop/_agent.py:307` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `goal` | `str` | *(required)* | What the plan aims to achieve |
| `steps` | `list[str]` | *(required)* | Ordered step descriptions |
| `revision` | `int` | `0` | `0` = initial plan, increments on replan |

**Emits**: `custom` event with `kind=plan_created`, tags `["plan", "created"]`. Also stores `_plan_total_steps` and `_plan_revision` on the task for automatic `plan_step` tracking.

**When to use**: When the agent creates or revises an execution plan. Call once when the plan is formulated, then track each step with `task.plan_step()`.

**Integration point**: After the LLM generates a plan:
- **ReAct loops**: After the "Thought" step that decides on a plan of action
- **Plan-and-execute**: After the planning LLM produces its step list
- **CrewAI**: After the crew's task decomposition
- **Tool chain planning**: When the agent decides which sequence of tools to call

---

### 3.10 `task.plan_step()` — Plan Step Progress

| | |
|---|---|
| **Function** | `task.plan_step(step_index, action, summary, *, total_steps, turns, tokens, plan_revision)` |
| **Source** | `src/sdk/hiveloop/_agent.py:441` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `step_index` | `int` | *(required)* | 0-based index into the plan |
| `action` | `str` | *(required)* | `"started"`, `"completed"`, `"failed"`, `"skipped"` |
| `summary` | `str` | *(required)* | Description of what happened |
| `total_steps` | `int \| None` | From `task.plan()` | Override total step count |
| `turns` | `int \| None` | `None` | Agentic loop turns consumed by this step |
| `tokens` | `int \| None` | `None` | Tokens consumed by this step |
| `plan_revision` | `int \| None` | From `task.plan()` | Override plan revision |

**Emits**: `custom` event with `kind=plan_step`, tags `["plan", "step_<action>"]`

**When to use**: As each plan step begins and completes. Provides progress tracking within a task.

**Integration point**: Inside the execution loop after each step:
```python
task.plan(goal, steps)
for i, step in enumerate(steps):
    task.plan_step(i, "started", step)
    result = execute(step)
    task.plan_step(i, "completed", f"Done: {result}", turns=3, tokens=4500)
```

---

### 3.11 `task.escalate()` — Escalation

| | |
|---|---|
| **Function** | `task.escalate(summary, *, assigned_to, reason, parent_event_id)` |
| **Source** | `src/sdk/hiveloop/_agent.py:334` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | `str` | *(required)* | What's being escalated and why |
| `assigned_to` | `str \| None` | `None` | Human or senior agent receiving the escalation |
| `reason` | `str \| None` | `None` | Reason code: `"confidence_low"`, `"out_of_scope"`, `"error_limit"` |
| `parent_event_id` | `str \| None` | `None` | Link to the event that triggered escalation |

**Emits**: `escalated` event (severity: `WARN`)

**When to use**: When the agent determines it cannot complete the task and needs to hand off to a human or more capable agent.

**Integration point**: In the agent's decision logic:
- **Confidence thresholds**: When LLM confidence is below threshold
- **Error limits**: After N consecutive failures
- **Scope boundaries**: When the request is outside the agent's capabilities
- **Policy violations**: When the request violates guardrails
- **Multi-agent handoff**: When delegating to a specialist agent

---

### 3.12 `task.request_approval()` — Approval Request

| | |
|---|---|
| **Function** | `task.request_approval(summary, *, approver, parent_event_id)` |
| **Source** | `src/sdk/hiveloop/_agent.py:362` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | `str` | *(required)* | What needs approval and why |
| `approver` | `str \| None` | `None` | Who should approve (person/role) |
| `parent_event_id` | `str \| None` | `None` | Link to related event |

**Emits**: `approval_requested` event. Sets agent derived status to `waiting_approval`.

**When to use**: When the agent needs human sign-off before proceeding with a high-impact action.

**Integration point**: Before destructive or irreversible operations:
- **Financial**: Before executing trades, transfers, payments
- **Infrastructure**: Before deploying, deleting resources, modifying configs
- **Communication**: Before sending emails, messages on behalf of users
- **Data**: Before bulk updates, deletions, or migrations

---

### 3.13 `task.approval_received()` — Approval Response

| | |
|---|---|
| **Function** | `task.approval_received(summary, *, approved_by, decision, parent_event_id)` |
| **Source** | `src/sdk/hiveloop/_agent.py:387` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | `str` | *(required)* | Description of the decision |
| `approved_by` | `str \| None` | `None` | Who approved/denied |
| `decision` | `str` | `"approved"` | `"approved"` or `"denied"` |
| `parent_event_id` | `str \| None` | `None` | Link to the `approval_requested` event |

**Emits**: `approval_received` event. Clears the `waiting_approval` status.

**When to use**: When a human responds to an approval request.

**Integration point**: In the approval callback/webhook handler. Link to the request via `parent_event_id`.

---

### 3.14 `task.retry()` — Retry Tracking

| | |
|---|---|
| **Function** | `task.retry(summary, *, attempt, backoff_seconds, parent_event_id)` |
| **Source** | `src/sdk/hiveloop/_agent.py:413` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `summary` | `str` | *(required)* | What's being retried and why |
| `attempt` | `int \| None` | `None` | 1-based attempt number |
| `backoff_seconds` | `float \| None` | `None` | How long before the next attempt |
| `parent_event_id` | `str \| None` | `None` | Link to the event that caused the retry |

**Emits**: `retry_started` event (severity: `WARN`)

**When to use**: Before each retry attempt. Provides visibility into retry storms and transient failures.

**Integration point**: In retry/backoff logic:
```python
for attempt in range(max_retries):
    try:
        return api_call()
    except TransientError as e:
        task.retry(f"API call failed: {e}", attempt=attempt+1, backoff_seconds=2**attempt)
        time.sleep(2**attempt)
```

---

### 3.15 `task.event()` / `agent.event()` — Generic Custom Event

| | |
|---|---|
| **Function** | `task.event(event_type, payload, severity, parent_event_id)` / `agent.event(...)` |
| **Source** | `src/sdk/hiveloop/_agent.py:235` (task-scoped), `:765` (agent-scoped) |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `event_type` | `str` | *(required)* | Any of the 13 event types, or `"custom"` |
| `payload` | `dict \| None` | `None` | Arbitrary payload data |
| `severity` | `str \| None` | Auto from type | Override: `"debug"`, `"info"`, `"warn"`, `"error"` |
| `parent_event_id` | `str \| None` | `None` | Causal link to a parent event |

**When to use**: For anything not covered by the convenience methods. This is the escape hatch for custom telemetry.

**Integration point**: Framework-specific events:
- **Guardrail triggers**: `task.event("custom", {"kind": "guardrail", "data": {"rule": "pii_filter", "action": "blocked"}})`
- **Memory operations**: `task.event("custom", {"kind": "memory_update", "data": {"items_added": 3}})`
- **Agent-to-agent messages**: `task.event("custom", {"kind": "agent_message", "data": {"to": "agent-b", "topic": "handoff"}})`

---

### 3.16 `HiveBoardLogHandler` — Python Logging Bridge

| | |
|---|---|
| **Class** | `HiveBoardLogHandler(agent, level, category)` |
| **Source** | `src/sdk/hiveloop/contrib/log_handler.py:18` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `agent` | `Agent` | *(required)* | The agent to report issues for |
| `level` | `int` | `logging.WARNING` | Minimum log level to forward |
| `category` | `str` | `"log"` | Issue category for all forwarded records |

**Log level mapping**:

| Python Level | HiveBoard Severity |
|---|---|
| `WARNING` | `medium` |
| `ERROR` | `high` |
| `CRITICAL` | `critical` |

**Auto-generates**: `issue_id` as `"log-{logger_name}-{level}"`, `context` with `filename`, `lineno`, `funcName`.

**When to use**: Always, as a catch-all. Captures unexpected warnings and errors from the application and any libraries without explicit instrumentation.

**Integration point**: Attach to the root logger or framework-specific loggers:
```python
import logging
from hiveloop.contrib.log_handler import HiveBoardLogHandler

logging.getLogger("my_app").addHandler(HiveBoardLogHandler(agent))
logging.getLogger("httpx").addHandler(HiveBoardLogHandler(agent, category="http"))
logging.getLogger("openai").addHandler(HiveBoardLogHandler(agent, category="llm"))
```

---

### 3.17 `hiveloop.flush()` — Force Event Delivery

| | |
|---|---|
| **Function** | `hiveloop.flush()` |
| **Source** | `src/sdk/hiveloop/__init__.py:220` |

No parameters.

**When to use**: Before critical checkpoints where you need events delivered immediately (e.g. before a process exit, before a long-running synchronous operation).

**Integration point**: Call before graceful shutdown, before `os._exit()`, or at the end of serverless function handlers (Lambda, Cloud Functions).

---

### 3.18 `hiveloop.shutdown()` — Graceful Teardown

| | |
|---|---|
| **Function** | `hiveloop.shutdown(timeout)` |
| **Source** | `src/sdk/hiveloop/__init__.py:205` |

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `timeout` | `float` | `5.0` | Max seconds to wait for final flush |

**When to use**: At application shutdown. Stops all heartbeat threads, drains remaining events, and closes HTTP connections.

**Integration point**: In `atexit` handlers, signal handlers (`SIGTERM`), or framework shutdown hooks. The SDK also registers its own `atexit` handler, but explicit shutdown gives more control over timeout.

---

## Summary Table: All Sensors at a Glance

| # | Sensor | SDK Function | Group | Scope | Trigger |
|---|--------|-------------|-------|-------|---------|
| 1 | SDK Init | `hiveloop.init()` | Identity | Global | App startup |
| 2 | Agent Registration | `hb.agent()` | Identity | Per agent | Agent creation |
| 3 | Agent Lookup | `hb.get_agent()` | Identity | Per agent | On demand |
| 4 | Custom State | `heartbeat_payload` callback | State | Per agent | Every heartbeat (auto) |
| 5 | Queue State (explicit) | `agent.queue_snapshot()` | State | Per agent | On demand |
| 6 | Queue State (auto) | `queue_provider` callback | State | Per agent | Every heartbeat (auto) |
| 7 | Todo Lifecycle | `agent.todo()` | State | Per agent | Work item changes |
| 8 | Scheduled Work | `agent.scheduled()` | State | Per agent | Schedule changes |
| 9 | Issue Reported | `agent.report_issue()` | State | Per agent | On error/degradation |
| 10 | Issue Resolved | `agent.resolve_issue()` | State | Per agent | On recovery |
| 11 | Task (context mgr) | `agent.task()` | Activity | Per task | Unit of work start |
| 12 | Task (manual) | `agent.start_task()` | Activity | Per task | Unit of work start |
| 13 | Task Termination | `task.complete()` / `task.fail()` | Activity | Per task | Unit of work end |
| 14 | Task Payload | `task.set_payload()` | Activity | Per task | Before task exit |
| 15 | Action (decorator) | `@agent.track()` | Activity | Per function | Function entry/exit |
| 16 | Action (context mgr) | `agent.track_context()` | Activity | Per block | Block entry/exit |
| 17 | Tool Payload | `tool_payload()` | Activity | Per action | Tool call completion |
| 18 | LLM Call | `task.llm_call()` / `agent.llm_call()` | Activity | Per LLM call | After API response |
| 19 | Plan Created | `task.plan()` | Activity | Per task | Plan formulation |
| 20 | Plan Step | `task.plan_step()` | Activity | Per step | Step start/complete |
| 21 | Escalation | `task.escalate()` | Activity | Per task | Handoff decision |
| 22 | Approval Request | `task.request_approval()` | Activity | Per task | Before high-impact action |
| 23 | Approval Response | `task.approval_received()` | Activity | Per task | Human decision |
| 24 | Retry | `task.retry()` | Activity | Per attempt | Before retry |
| 25 | Custom Event | `task.event()` / `agent.event()` | Activity | Any | Escape hatch |
| 26 | Log Bridge | `HiveBoardLogHandler` | Activity | Per log record | WARNING+ log |
| 27 | Flush | `hiveloop.flush()` | Activity | Global | Checkpoint |
| 28 | Shutdown | `hiveloop.shutdown()` | Activity | Global | App exit |

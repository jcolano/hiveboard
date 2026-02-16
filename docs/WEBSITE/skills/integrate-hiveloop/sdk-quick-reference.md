# HiveLoop SDK — Quick Reference

Condensed function signatures for all 28 sensors. Use this as a lookup while instrumenting.

---

## 1. Identity Sensors

### `hiveloop.init()` — SDK Initialization
```python
hb = hiveloop.init(
    api_key: str,               # Required. Must start with "hb_"
    environment: str = "production",  # "dev", "staging", "production"
    group: str = "default",     # Team/service grouping
    endpoint: str | None = None,     # Backend URL (auto-resolved if omitted)
    flush_interval: float = 5.0,     # Seconds between flushes
    batch_size: int = 100,           # Max events per POST
    max_queue_size: int = 10_000,    # Queue capacity before drop
    debug: bool = False,             # Enable DEBUG logging
)
```

### `hb.agent()` — Agent Registration
```python
agent = hb.agent(
    agent_id: str,              # Required. Unique, stable name (max 256 chars)
    type: str = "general",      # "sales", "support", "coder", etc.
    version: str | None = None, # Semantic version of agent impl
    framework: str = "custom",  # "langchain", "crewai", "autogen", etc.
    heartbeat_interval: float = 30.0,  # Seconds between heartbeats
    stuck_threshold: int = 300,        # Seconds before "stuck" status
    heartbeat_payload: Callable[[], dict | None] | None = None,  # Custom state per heartbeat
    queue_provider: Callable[[], dict | None] | None = None,     # Queue state per heartbeat
)
```
Auto-emits: `agent_registered` immediately, `heartbeat` every N seconds.

### `hb.get_agent()` — Agent Lookup
```python
agent = hb.get_agent(agent_id: str) -> Agent | None
```

---

## 2. State Sensors

### `heartbeat_payload` callback — Custom State
```python
# Passed to hb.agent(). Called automatically every heartbeat.
heartbeat_payload=lambda: {
    "memory_items": 42,
    "context_window_usage": 0.73,
    "active_tools": ["search", "calc"],
}
```

### `agent.queue_snapshot()` — Work Queue (explicit)
```python
agent.queue_snapshot(
    depth: int,                        # Required. Items in queue
    oldest_age_seconds: int | None = None,
    items: list[dict] | None = None,   # [{id, priority, source, summary, queued_at}]
    processing: dict | None = None,    # {id, summary, started_at, elapsed_ms}
)
```

### `queue_provider` callback — Work Queue (automatic)
```python
# Passed to hb.agent(). Same schema as queue_snapshot(). Called every heartbeat.
queue_provider=lambda: {"depth": 5, "oldest_age_seconds": 120, "items": [...]}
```

### `agent.todo()` — Todo/Work Items
```python
agent.todo(
    todo_id: str,               # Required. Stable ID for dedup
    action: str,                # Required. "created", "completed", "failed", "dismissed", "deferred"
    summary: str,               # Required. Human-readable description
    priority: str | None = None,     # "high", "normal", "low"
    source: str | None = None,       # "user", "planner", "escalation"
    context: str | None = None,      # Additional context
    due_by: str | None = None,       # ISO 8601 deadline
)
```

### `agent.scheduled()` — Scheduled Work
```python
agent.scheduled(
    items: list[dict],  # [{id, name, next_run, interval, enabled, last_status}]
)
```

### `agent.report_issue()` — Report Issue
```python
agent.report_issue(
    summary: str,               # Required. Max 512 chars
    severity: str,              # Required. "critical", "high", "medium", "low"
    issue_id: str | None = None,     # Stable ID for dedup (auto-hashed if absent)
    category: str | None = None,     # "permissions", "connectivity", "configuration",
                                     # "data_quality", "rate_limit", "other"
    context: dict | None = None,     # e.g. {"endpoint": "...", "status": 503}
    occurrence_count: int | None = None,
)
```

### `agent.resolve_issue()` — Resolve Issue
```python
agent.resolve_issue(
    summary: str,               # Required. Resolution description
    issue_id: str | None = None,     # Must match previously reported issue
)
```

---

## 3. Activity Sensors

### `agent.task()` — Task Lifecycle (context manager)
```python
with agent.task(
    task_id: str,               # Required. Max 256 chars
    project: str | None = None,      # Project grouping
    type: str | None = None,         # "chat", "analysis", "code_gen", etc.
    task_run_id: str | None = None,  # Auto UUID4 if omitted
    correlation_id: str | None = None,
) as task:
    # all events inside inherit task context
    ...
```
Auto-emits: `task_started` on enter, `task_completed` on exit, `task_failed` on exception.

### `agent.start_task()` — Task Lifecycle (manual)
```python
task = agent.start_task(
    task_id, project, type, task_run_id, correlation_id  # same params
)
# ... later:
task.complete(status="success", payload={"result": ...})
# or:
task.fail(exception=e, payload={"context": ...})
```

### `task.set_payload()` — Attach Data to Task
```python
task.set_payload(payload: dict)  # included in task_completed event
```

### `@agent.track()` — Action Tracking (decorator)
```python
@agent.track("action_name")
async def my_function(args):
    ...
```
Auto-emits: `action_started`, `action_completed`/`action_failed` with `duration_ms`. Supports nesting.

### `agent.track_context()` — Action Tracking (context manager)
```python
with agent.track_context("action_name") as ctx:
    result = do_something()
    ctx.set_payload(tool_payload(args={...}, result=result))
```

### `tool_payload()` — Standard Tool Payload Builder
```python
from hiveloop import tool_payload

tool_payload(
    args: dict | None = None,
    result: Any | None = None,
    success: bool = True,
    error: str | None = None,
    duration_ms: int | None = None,
    tool_category: str | None = None,  # "crm", "database", "api", "file"
    http_status: int | None = None,
    result_size_bytes: int | None = None,
    args_max_len: int = 500,
    result_max_len: int = 1000,
) -> dict
```

### `task.llm_call()` / `agent.llm_call()` — LLM Call Tracking
```python
task.llm_call(
    name: str,                  # Required. "reason", "plan", "summarize", "classify"
    model: str,                 # Required. "claude-sonnet-4-20250514", "gpt-4o", etc.
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    cost: float | None = None,       # USD
    duration_ms: int | None = None,
    prompt_preview: str | None = None,
    response_preview: str | None = None,
    metadata: dict | None = None,    # temperature, stop_reason, etc.
)
```
`agent.llm_call()` has the same signature — use when outside a task context.

### `task.plan()` — Plan Creation
```python
task.plan(
    goal: str,                  # Required. What the plan achieves
    steps: list[str],           # Required. Ordered step descriptions
    revision: int = 0,          # 0 = initial, increment on replan
)
```

### `task.plan_step()` — Plan Step Progress
```python
task.plan_step(
    step_index: int,            # Required. 0-based
    action: str,                # Required. "started", "completed", "failed", "skipped"
    summary: str,               # Required. What happened
    total_steps: int | None = None,
    turns: int | None = None,        # Agentic turns consumed
    tokens: int | None = None,       # Tokens consumed
    plan_revision: int | None = None,
)
```

### `task.escalate()` — Escalation
```python
task.escalate(
    summary: str,               # Required
    assigned_to: str | None = None,
    reason: str | None = None,  # "confidence_low", "out_of_scope", "error_limit"
    parent_event_id: str | None = None,
)
```

### `task.request_approval()` — Approval Request
```python
task.request_approval(
    summary: str,               # Required
    approver: str | None = None,
    parent_event_id: str | None = None,
)
```

### `task.approval_received()` — Approval Response
```python
task.approval_received(
    summary: str,               # Required
    approved_by: str | None = None,
    decision: str = "approved",      # "approved" or "denied"
    parent_event_id: str | None = None,
)
```

### `task.retry()` — Retry Tracking
```python
task.retry(
    summary: str,               # Required
    attempt: int | None = None,      # 1-based
    backoff_seconds: float | None = None,
    parent_event_id: str | None = None,
)
```

### `task.event()` / `agent.event()` — Generic Custom Event
```python
task.event(
    event_type: str,            # Required. Any of 13 event types or "custom"
    payload: dict | None = None,
    severity: str | None = None,     # "debug", "info", "warn", "error"
    parent_event_id: str | None = None,
)
```

### `HiveBoardLogHandler` — Python Logging Bridge
```python
from hiveloop.contrib.log_handler import HiveBoardLogHandler

handler = HiveBoardLogHandler(
    agent: Agent,               # Required
    level: int = logging.WARNING,
    category: str = "log",
)
logging.getLogger().addHandler(handler)
```
Level mapping: WARNING -> medium, ERROR -> high, CRITICAL -> critical.

### `hiveloop.flush()` — Force Event Delivery
```python
hiveloop.flush()  # no params, blocks until queue drained
```

### `hiveloop.shutdown()` — Graceful Teardown
```python
hiveloop.shutdown(timeout: float = 5.0)  # stops heartbeats, drains queue, closes connections
```

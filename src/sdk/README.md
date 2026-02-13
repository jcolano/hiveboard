# HiveLoop

**Lightweight observability SDK for AI agents.**

HiveLoop instruments your AI agents with 3 lines of code and sends structured telemetry to [HiveBoard](https://hiveboard.io) -- giving you heartbeat monitoring, task timelines, LLM cost tracking, plan progress, queue visibility, and issue detection without changing how your agents work.

```python
import hiveloop

hb = hiveloop.init(api_key="hb_live_...")
agent = hb.agent("lead-qualifier", type="sales")
```

Your agent is now visible on the HiveBoard dashboard. Green dot = alive. Red dot = something's wrong. No logs to check.

## Why HiveLoop?

AI agents fail silently. A credential expires, a queue backs up, a task hangs -- and nobody notices until a customer complains. HiveLoop makes these failures visible in seconds, not hours.

- **Heartbeat monitoring** -- know instantly when an agent stops responding
- **Task timelines** -- see every step an agent took, which tool failed, and why
- **LLM cost tracking** -- know exactly what each model call costs, per agent, per task
- **Plan progress** -- track multi-step plans and see where they diverge
- **Queue visibility** -- see what work is waiting and what's stuck
- **Issue detection** -- agents can self-report problems with recurrence tracking
- **Human-in-the-loop** -- track escalations and approval workflows

## Installation

```bash
pip install hiveloop
```

Requires Python 3.11+. Only dependency: `requests`.

## Quick Start

### Layer 0: Presence (3 lines)

Register your agent. HiveBoard will show it as alive with heartbeat monitoring.

```python
import hiveloop

hb = hiveloop.init(api_key="hb_live_...")
agent = hb.agent("my-agent", type="support", version="1.0.0")

# Your agent code runs here. Heartbeats are sent automatically.
# When you're done:
hiveloop.shutdown()
```

**What you see on HiveBoard:** Agent card with name, type, status badge, heartbeat indicator.

### Layer 1: Task Timelines (add context managers)

Track tasks to get success rates, durations, throughput, and failure diagnostics.

```python
with agent.task("task-001", project="sales-pipeline", type="lead_qualification") as task:
    lead = fetch_crm_data("lead-4821")
    score = score_lead(lead)
    route_lead(score)
```

The task auto-completes on exit. If an exception is raised, it auto-fails with the exception details. Exceptions are never swallowed.

**What you see:** Task table, success rates, duration averages, throughput charts.

### Layer 2: Full Story (add rich events)

Add LLM call tracking, plans, queue snapshots, and issue reporting for complete observability.

```python
with agent.task("task-001", project="sales-pipeline") as task:
    # Track LLM calls with cost data
    task.llm_call(
        "lead_scoring",
        "claude-sonnet-4-20250514",
        tokens_in=1800,
        tokens_out=250,
        cost=0.004,
        duration_ms=1200,
        prompt_preview="Score this lead based on...",
        response_preview="Score: 72. Reasoning: ...",
    )

    # Track multi-step plans
    task.plan("Process lead", ["Fetch CRM", "Score", "Enrich", "Route"])
    task.plan_step(0, "completed", "CRM data retrieved", turns=1, tokens=800)
    task.plan_step(1, "completed", "Lead scored 72", turns=2, tokens=3200)
    task.plan_step(2, "failed", "Enrichment API timeout")

    # Escalate when needed
    task.escalate("Enrichment service down", reason="service_outage")

    # Request human approval
    task.request_approval("Credit $450 for customer BlueStar?", approver="sales-lead")
```

**What you see:** Cost explorer, task timelines with LLM nodes, plan progress bars, escalation tracking.

## Action Tracking

Track individual functions with the `@agent.track` decorator. Supports both sync and async functions, with automatic nesting.

```python
@agent.track("fetch_crm_data")
def fetch_crm_data(lead_id):
    return crm_client.get_lead(lead_id)

@agent.track("enrich_company")
async def enrich_company(company_name):
    return await enrichment_api.lookup(company_name)

@agent.track("process_lead")
def process_lead(lead_id):
    lead = fetch_crm_data(lead_id)       # Nested action
    company = enrich_company(lead.company) # Nested action
    return score(lead, company)

with agent.task("task-001") as task:
    result = process_lead("lead-4821")
```

Each tracked function emits `action_started` and `action_completed` (or `action_failed`) events with duration. Nesting is tracked automatically -- `fetch_crm_data` and `enrich_company` show as children of `process_lead` in the timeline.

For inline tracking without decorators:

```python
with agent.track_context("data_processing") as action:
    items = process_batch(data)
    action.set_payload({"items_processed": len(items)})
```

## Agent-Level Features

### Queue Monitoring

Report your agent's work queue so HiveBoard can detect backed-up or abandoned work.

```python
# Manual snapshot
agent.queue_snapshot(
    depth=5,
    oldest_age_seconds=120,
    items=[
        {"id": "lead-100", "priority": "high", "source": "inbound"},
        {"id": "lead-101", "priority": "normal", "source": "referral"},
    ],
)

# Or use automatic reporting via callback (emits with each heartbeat)
def get_queue():
    return {"depth": len(my_queue), "oldest_age_seconds": my_queue.oldest_age()}

agent = hb.agent("my-agent", queue_provider=get_queue)
```

### Issue Reporting

Agents can self-report problems. HiveBoard tracks recurrence.

```python
agent.report_issue(
    "CRM API returning 403",
    severity="high",
    issue_id="crm-auth-failure",
    category="permissions",
    context={"endpoint": "/api/leads", "status_code": 403},
    occurrence_count=8,
)

# Later, when resolved:
agent.resolve_issue("CRM credentials rotated", issue_id="crm-auth-failure")
```

### TODO Tracking

Track work items that agents create for themselves or for humans.

```python
agent.todo(
    "todo-001",
    action="created",
    summary="Follow up on failed enrichment for lead-4821",
    priority="high",
    source="failed_action",
    due_by="2026-02-14T15:00:00Z",
)

# When completed:
agent.todo("todo-001", action="completed", summary="Enrichment retry succeeded")
```

### Scheduled Work

Report recurring jobs so HiveBoard can show what's coming next.

```python
agent.scheduled([
    {
        "id": "daily-sync",
        "name": "CRM Full Sync",
        "next_run": "2026-02-14T06:00:00Z",
        "interval": "24h",
        "enabled": True,
    },
    {
        "id": "hourly-check",
        "name": "Lead Queue Check",
        "next_run": "2026-02-13T15:00:00Z",
        "interval": "1h",
        "enabled": True,
    },
])
```

### Custom Heartbeat Data

Include operational metrics in heartbeats so HiveBoard can detect behavioral drift.

```python
def heartbeat_data():
    return {
        "crm_sync": True,
        "email_check": True,
        "queue_depth": len(my_queue),
        "last_success": last_success_time.isoformat(),
    }

agent = hb.agent("my-agent", heartbeat_payload=heartbeat_data)
```

## Task Lifecycle

### Context Manager (recommended)

```python
with agent.task("task-001") as task:
    do_work()
    # Auto-completes on clean exit
    # Auto-fails with exception details if exception is raised
```

### Manual Lifecycle

For long-running tasks or when you need explicit control:

```python
task = agent.start_task("task-001", project="sales")

try:
    result = do_work()
    task.complete(status="success", payload={"result": result})
except Exception as e:
    task.fail(exception=e)
```

### Setting Payload

Attach data to the completion event:

```python
with agent.task("task-001") as task:
    score = compute_score()
    task.set_payload({"score": score, "threshold": 80, "decision": "nurture"})
```

## Approval Workflows

Track human-in-the-loop decisions:

```python
with agent.task("refund-review") as task:
    task.request_approval(
        "Refund $450 for customer BlueStar",
        approver="finance-team",
    )

    # ... wait for human decision ...

    task.approval_received(
        "Approved by @jsmith",
        approved_by="jsmith",
        decision="approved",
    )
```

## Retry Tracking

Make retry behavior visible in task timelines:

```python
with agent.task("task-001") as task:
    for attempt in range(3):
        try:
            result = call_external_api()
            break
        except TimeoutError:
            task.retry(
                f"API timeout, attempt {attempt + 1}",
                attempt=attempt + 1,
                backoff_seconds=2 ** attempt,
            )
            time.sleep(2 ** attempt)
```

## Configuration

### `hiveloop.init()` Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `api_key` | `str` | **required** | API key (must start with `hb_`) |
| `environment` | `str` | `"production"` | Environment identifier |
| `group` | `str` | `"default"` | Organizational group |
| `endpoint` | `str` | `"https://api.hiveboard.io"` | HiveBoard API endpoint |
| `flush_interval` | `float` | `5.0` | Seconds between automatic flushes |
| `batch_size` | `int` | `100` | Max events per batch (capped at 500) |
| `max_queue_size` | `int` | `10_000` | Max queued events before dropping oldest |
| `debug` | `bool` | `False` | Enable debug logging |

### `hb.agent()` Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_id` | `str` | **required** | Unique agent identifier |
| `type` | `str` | `"general"` | Agent type (e.g., `"sales"`, `"support"`) |
| `version` | `str \| None` | `None` | Agent version string |
| `framework` | `str` | `"custom"` | Framework (e.g., `"langchain"`, `"crewai"`) |
| `heartbeat_interval` | `float` | `30.0` | Seconds between heartbeats (0 to disable) |
| `stuck_threshold` | `int` | `300` | Seconds before agent is considered stuck |
| `heartbeat_payload` | `callable` | `None` | Callback returning dict of heartbeat data |
| `queue_provider` | `callable` | `None` | Callback returning queue state dict |

## How It Works

HiveLoop runs a background daemon thread that batches events and sends them to HiveBoard via `POST /v1/ingest`. All SDK calls return immediately -- they never block your agent.

- **Non-blocking**: Events are queued in memory and flushed in the background
- **Thread-safe**: Safe to call from multiple threads. Task context is thread-local
- **Async-aware**: `@agent.track` works with both sync and async functions
- **Resilient**: Transport never raises exceptions to your code. Failed sends are retried with exponential backoff (1s, 2s, 4s, 8s, 16s) and silently dropped after 5 retries
- **Bounded**: Queue has a configurable max size (default 10,000). Oldest events are dropped when full
- **Graceful**: `hiveloop.shutdown()` flushes all remaining events before exiting. Also registered as an `atexit` handler

## Framework Compatibility

HiveLoop is framework-agnostic. It works with:

- Plain Python scripts
- LangChain / LangGraph agents
- CrewAI crews
- AutoGen agents
- Any Python code that runs AI agents

No framework-specific adapters needed. Just `import hiveloop` and instrument.

## Self-Hosted

Point HiveLoop at your own HiveBoard instance:

```python
hb = hiveloop.init(
    api_key="hb_live_...",
    endpoint="https://hiveboard.your-company.com",
)
```

## API Reference

### Module-Level Functions

```python
hiveloop.init(api_key, ...)      # Initialize SDK singleton
hiveloop.shutdown(timeout=5.0)   # Shut down and flush
hiveloop.reset()                 # Clear singleton for re-initialization
hiveloop.flush()                 # Force immediate flush
```

### Task Methods

```python
task.llm_call(name, model, ...)            # Record LLM call
task.plan(goal, steps, revision=0)         # Record plan
task.plan_step(index, action, summary)     # Update plan step
task.escalate(summary, ...)                # Escalate to human
task.request_approval(summary, ...)        # Request approval
task.approval_received(summary, ...)       # Record approval
task.retry(summary, ...)                   # Record retry
task.event(event_type, payload, ...)       # Custom event
task.complete(status="success")            # Manual complete
task.fail(exception=None)                  # Manual fail
task.set_payload(payload)                  # Set completion data
```

### Agent Methods

```python
agent.task(task_id, ...)                   # Create task (context manager)
agent.start_task(task_id, ...)             # Start task (manual lifecycle)
agent.track(action_name)                   # Decorator for action tracking
agent.track_context(action_name)           # Context manager for actions
agent.llm_call(name, model, ...)           # Agent-level LLM call
agent.queue_snapshot(depth, ...)           # Report queue state
agent.todo(todo_id, action, summary, ...)  # TODO lifecycle
agent.scheduled(items)                     # Scheduled work items
agent.report_issue(summary, severity, ...) # Report issue
agent.resolve_issue(summary, ...)          # Resolve issue
agent.event(event_type, payload, ...)      # Custom event
```

## Instrumentation Layers

You don't need to instrument everything on day one. Start small and add depth as needed.

| Layer | What You Add | What You See |
|---|---|---|
| **Layer 0: Presence** | `hiveloop.init()` + `hb.agent()` | Agent cards, heartbeats, stuck detection |
| **Layer 1: Timelines** | `agent.task()` + `@agent.track()` | Task table, success rates, durations, action trees |
| **Layer 2: Full Story** | `task.llm_call()`, `task.plan()`, `agent.report_issue()`, etc. | Cost explorer, plan progress, queue visibility, issue tracking |

## Requirements

- Python >= 3.11
- `requests` >= 2.31.0
- A HiveBoard instance (cloud or self-hosted)

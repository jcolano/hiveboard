# HiveBoard — User Manual Part 5: Layer 2 Integration — Rich Events

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *LLM costs, plans, escalations, approvals, retries, issues — the full narrative of what your agents are thinking and why.*

---

## Table of Contents

1. [What Layer 2 Gives You](#1-what-layer-2-gives-you)
2. [LLM Call Tracking](#2-llm-call-tracking)
3. [Plans and Plan Steps](#3-plans-and-plan-steps)
4. [Escalations](#4-escalations)
5. [Approvals](#5-approvals)
6. [Retries](#6-retries)
7. [Issue Reporting](#7-issue-reporting)
8. [Pipeline Enrichment](#8-pipeline-enrichment)
9. [Agent-Level Events (Outside Task Context)](#9-agent-level-events-outside-task-context)
10. [Cost Estimation](#10-cost-estimation)
11. [What to Expect on the Dashboard](#11-what-to-expect-on-the-dashboard)
12. [Incremental Adoption Strategy](#12-incremental-adoption-strategy)
13. [Troubleshooting Layer 2](#13-troubleshooting-layer-2)

---

## 1. What Layer 2 Gives You

Layer 1 told you what your agents are doing — tasks started, actions tracked, durations measured. Layer 2 tells you **why they're doing it, how much it costs, and what went wrong in their reasoning.**

Layer 2 is a collection of typed events that add narrative depth to the timeline. Each event type is independent — adopt them in any order, in any combination. There is no "all or nothing."

| Layer 2 event | What it answers | Dashboard impact |
|---------------|----------------|-----------------|
| `task.llm_call()` | How much is each LLM call costing? What model was used? How many tokens? | Cost Explorer, purple timeline nodes, LLM/COST columns in Task Table |
| `task.plan()` | What plan did the agent create? | Plan progress bar above Timeline |
| `task.plan_step()` | Which step is it on? Which step failed? | Plan bar step indicators (green/blue/red/gray) |
| `task.escalate()` | When did the agent ask for help? | Amber nodes in Timeline, "human" filter in Activity Stream |
| `task.request_approval()` | What's waiting for human approval? | Agent WAITING badge, amber nodes |
| `task.approval_received()` | Was it approved or rejected? Who decided? | Green/red approval nodes in Timeline |
| `task.retry()` | How many times did it retry? Why? | Timeline branching, retry pattern visibility |
| `agent.report_issue()` | What persistent problems has the agent found? | Pipeline tab, issue badges on agent cards |
| `agent.queue_snapshot()` | How deep is the work queue? | Queue badges on agent cards, Pipeline tab |
| `agent.todo()` | What work items is the agent tracking? | Pipeline tab |
| `agent.scheduled()` | What recurring work is configured? | Pipeline tab |

### The progression

```
Layer 0:  "Agent is alive"
Layer 1:  "Agent is working on task X, step Y, for Z seconds"
Layer 2:  "Agent called claude-sonnet with 1,500 tokens ($0.008), created a 4-step plan,
           completed steps 1-3, failed on step 4 with a CRM permission error,
           escalated to the sales team, and is now waiting for approval"
```

That's the difference. Layer 2 turns a timeline into a story.

---

## 2. LLM Call Tracking

**Priority:** Highest. This is the single most valuable Layer 2 addition.

### 2.1 What it does

`task.llm_call()` records a structured event for each LLM API call: which model, how many tokens, how much it cost, and how long it took. This data feeds the Cost Explorer, adds purple nodes to the Timeline, and fills in the LLM and COST columns in the Task Table.

### 2.2 Finding WHERE in your code — the catalog-first approach

Before writing any instrumentation code, build a catalog of every LLM call site. This step saves significant time because — as real-world integration has shown — **token extraction varies between call sites, even within the same codebase.** You need to know what each site exposes before you can write correct instrumentation.

**Step 1: Search for LLM client calls.** Common patterns across frameworks:

```python
# OpenAI / Azure OpenAI
response = client.chat.completions.create(model=..., messages=...)

# Anthropic
response = client.messages.create(model=..., messages=...)

# LiteLLM
response = litellm.completion(model=..., messages=...)

# LangChain
response = llm.invoke(prompt)

# Custom wrapper
response = my_llm_client.call(model=..., prompt=...)
```

**Step 2: For each site, inspect the response and client objects.** Add a temporary debug line after each call:

```python
response = client.complete(...)
print(f"Response attrs: {[a for a in dir(response) if 'token' in a.lower() or 'usage' in a.lower()]}")
print(f"Client attrs: {[a for a in dir(client) if 'token' in a.lower() or 'usage' in a.lower()]}")
```

This reveals where each site stores token counts and model information.

**Step 3: Build a table.** Before writing any `task.llm_call()` code, document what you found:

| Site | File:line | Client method | Token source | Model source |
|------|-----------|---------------|-------------|-------------|
| 1 | `loop.py:1275` | `client.complete_json()` | `client._last_input_tokens` | `client.model` |
| 2 | `loop.py:1432` | `client.complete_with_tools()` | `response.usage.input_tokens` | `response.model` |
| 3 | `reflection.py:367` | `client.complete_json()` | `client._last_input_tokens` | `client.model` |
| 4 | `planning.py:567` | `client.complete_json()` | `client._last_input_tokens` | `client.model` |
| 5 | `agent.py:199` | `haiku_client.complete()` | `client._last_input_tokens` | `client.model` |
| 6 | `context.py:319` | `client.complete()` | `client._last_input_tokens` | `client.model` |

Notice the pattern: Sites 1, 3-6 all use the same `complete_json()` / `complete()` method family, which stores tokens on the **client object** (`client._last_input_tokens`). Site 2 uses `complete_with_tools()`, which returns tokens on the **response object** (`response.usage.input_tokens`). Same codebase, two different extraction patterns.

**Step 4: Implement from the table.** Now each `task.llm_call()` addition is mechanical — you know exactly where the tokens and model name come from.

This catalog-first approach typically takes 15-30 minutes and prevents the frustrating cycle of adding instrumentation, discovering it doesn't extract tokens correctly, debugging, fixing, and repeating for each site.

### 2.3 Adding `task.llm_call()`

The structure is the same at every site — but the field extraction varies (see Section 2.6). Here's the general shape:

```python
import time
from myproject.observability import get_current_task

# Measure timing
_llm_start = time.perf_counter()

# Existing LLM call (don't change this):
response = client.messages.create(model="claude-sonnet-4-5-20250929", messages=messages)

# After the call — add this:
_llm_elapsed = (time.perf_counter() - _llm_start) * 1000
_task = get_current_task()
if _task:
    try:
        _task.llm_call(
            "descriptive_name",                        # what this call does
            model=response.model,                      # or client.model — see Section 2.6
            tokens_in=response.usage.input_tokens,     # or client._last_input_tokens — see Section 2.6
            tokens_out=response.usage.output_tokens,   # or client._last_output_tokens — see Section 2.6
            cost=0.008,                                # optional, USD
            duration_ms=round(_llm_elapsed),           # optional
            prompt_preview=str(messages)[:500],         # optional, for debugging
            response_preview=str(response.content)[:500],  # optional
            metadata={"temperature": 0.7},             # optional
        )
    except Exception:
        pass  # never break agent for observability
```

**Important:** The `tokens_in`, `tokens_out`, and `model` fields above show one extraction pattern (`response.usage.*`). Your call site may use a different pattern (`client._last_*`, a config variable, etc.). Refer to your catalog (Section 2.2) and the extraction guide (Section 2.6) for the correct source at each site.

### 2.4 Parameter reference

| Parameter | Type | Required | What it does |
|-----------|------|----------|-------------|
| `name` | str | **Yes** | Label for the Timeline node. Use descriptive names: `"phase1_reasoning"`, `"score_lead"`, `"generate_email"`. Not the model name. |
| `model` | str | **Yes** | Model identifier (e.g. `"claude-sonnet-4-5-20250929"`). Used for Cost Explorer grouping. |
| `tokens_in` | int | No | Input token count. Feeds Cost Explorer aggregation. |
| `tokens_out` | int | No | Output token count. Feeds Cost Explorer aggregation. |
| `cost` | float | No | Pre-calculated cost in USD. If absent, the call appears in timelines but is excluded from cost totals. |
| `duration_ms` | int | No | LLM API latency in milliseconds. Shown on the timeline connector. |
| `prompt_preview` | str | No | First ~500 chars of the prompt. For debugging in the detail panel. |
| `response_preview` | str | No | First ~500 chars of the response. For debugging in the detail panel. |
| `metadata` | dict | No | Arbitrary key-value pairs (temperature, top_p, caller info, etc.). |

**Start minimal, add fields later.** The only required fields are `name` and `model`. Everything else is optional and can be added incrementally:

```python
# Minimum viable (still useful):
_task.llm_call("reasoning", model="claude-sonnet-4-5-20250929")

# Add tokens when you figure out the response shape:
_task.llm_call("reasoning", model="...", tokens_in=1500, tokens_out=200)

# Add cost when you build the cost helper:
_task.llm_call("reasoning", model="...", tokens_in=1500, tokens_out=200, cost=0.008)

# Add previews when you need to debug LLM behavior:
_task.llm_call("reasoning", model="...", prompt_preview=prompt[:500], response_preview=resp[:500])
```

### 2.5 Naming conventions

Choose names that describe **what the call does**, not which model or API it uses:

| ✅ Good name | ❌ Bad name | Why |
|-------------|-----------|-----|
| `"score_lead"` | `"claude_call"` | Multiple calls may use Claude — the name should distinguish them |
| `"phase1_reasoning"` | `"llm_call_1"` | Numbers don't tell you anything in the timeline |
| `"generate_email_draft"` | `"anthropic_api"` | The model is already in the `model` field |
| `"context_compaction"` | `"call"` | Too generic |
| `"reflection"` | `"messages.create"` | The API method is implementation detail |

### 2.6 Extracting token usage — it's messier than you expect

This is the step where most people lose time. **Token extraction varies not just between SDKs, but between methods within the same SDK, and between the SDK and the wrapper your codebase uses on top of it.** The catalog-first approach (Section 2.2) exists specifically to prevent this from becoming a per-site debugging session.

#### Pattern A: Tokens on the response object

The cleanest pattern. The LLM SDK returns a response with usage data attached:

```python
# Anthropic SDK (direct)
response.usage.input_tokens     # int
response.usage.output_tokens    # int
response.model                  # str

# OpenAI SDK (direct)
response.usage.prompt_tokens    # int (note: different field name)
response.usage.completion_tokens # int
response.model                  # str

# LiteLLM
response.usage.prompt_tokens    # int (follows OpenAI naming)
response.usage.completion_tokens # int
response.model                  # str
```

This works when you're calling the SDK directly. But most production codebases don't — they use a wrapper.

#### Pattern B: Tokens on the client object

Many wrapper libraries store usage on the client instance after a call, not on the response:

```python
# Common in custom wrappers:
response = client.complete_json(model="...", messages=...)
tokens_in = client._last_input_tokens     # stored after the call
tokens_out = client._last_output_tokens   # stored after the call
model_name = client.model                 # configured at init time
```

The underscore prefix (`_last_input_tokens`) means it's a private attribute — the wrapper author didn't consider it part of the public API. This is common. It works fine, but it may change without notice in wrapper updates.

**Real-world example:** In a 6-site integration, 5 out of 6 sites used `client._last_input_tokens` (Pattern B), while 1 site used `response.usage.input_tokens` (Pattern A) — because that particular client method (`complete_with_tools()`) had a different return type than the others (`complete_json()`). Same project, same LLM wrapper library, two extraction patterns.

#### Pattern C: Tokens in a callback or event

Framework-level integrations (LangChain, CrewAI) often deliver token counts through callbacks rather than return values:

```python
# LangChain
# Token counts come through the callback handler, not the response object.
# Use the HiveLoop framework integration instead of manual task.llm_call().

# CrewAI
# Similar — token tracking happens in the agent execution callback.
```

If you're using a framework integration, prefer the HiveLoop callback adapter over manual `task.llm_call()` — it handles extraction automatically.

#### Pattern D: Tokens not available

Some wrappers don't expose usage at all. You have three options:

```python
# Option 1: Send without tokens (the call still appears on Timeline)
_task.llm_call("reasoning", model="claude-sonnet-4-5-20250929")

# Option 2: Dig into the wrapper's internals
print(dir(response))   # look for usage, tokens, meta, _raw, etc.
print(dir(client))     # look for _last_*, _usage, _tokens, etc.

# Option 3: Estimate tokens from input/output length
# Rough heuristic: ~4 chars per token for English text
estimated_in = len(prompt_text) // 4
estimated_out = len(response_text) // 4
```

Option 1 is always safe. Option 2 usually finds something — most wrappers store usage somewhere, even if it's not documented. Option 3 is a last resort and only useful for rough cost estimation.

#### Finding where model name lives

The model name also varies by pattern:

```python
# On the response (Pattern A):
model_name = response.model

# On the client (Pattern B):
model_name = client.model           # configured at client initialization

# Hardcoded (when you know the model won't change):
model_name = "claude-sonnet-4-5-20250929"

# From a config variable:
model_name = settings.LLM_MODEL
```

If different call sites use different models (e.g. Sonnet for reasoning, Haiku for summarization), the model name must come from the correct source at each site. Don't assume one model across all sites — this is exactly what the Cost Explorer's "by model" breakdown is designed to reveal.

#### Quick discovery recipe

When you're not sure where tokens live for a given call site, run this once:

```python
response = client.some_method(...)

# Check response:
for attr in dir(response):
    if any(k in attr.lower() for k in ['token', 'usage', 'cost', 'model']):
        print(f"response.{attr} = {getattr(response, attr, '?')}")

# Check client:
for attr in dir(client):
    if any(k in attr.lower() for k in ['token', 'usage', 'cost', 'model', 'last']):
        print(f"client.{attr} = {getattr(client, attr, '?')}")
```

Run this for each unique client method in your catalog (Section 2.2). Methods that share the same return type will have the same extraction pattern.

### 2.7 LLM calls outside a task context

Some LLM calls happen outside of any task — startup routines, background maintenance, heartbeat summaries. For these, `get_current_task()` returns `None`, so `task.llm_call()` is skipped.

If you want to track these costs anyway, use the agent-level method:

```python
_task = get_current_task()
if _task:
    _task.llm_call("heartbeat_summary", model=model_name, ...)
elif hiveloop_agent:
    hiveloop_agent.llm_call("heartbeat_summary", model=model_name, ...)
```

Agent-level LLM calls appear in the Cost Explorer (aggregated under the agent) but not on any task timeline.

---

## 3. Plans and Plan Steps

### 3.1 What it does

If your agent creates execution plans (multi-step strategies), `task.plan()` and `task.plan_step()` make those plans visible on the dashboard. A progress bar appears above the Timeline showing each step's status.

### 3.2 Finding WHERE in your code

Look for code that:
- Creates a list of steps, phases, or stages
- Iterates through a strategy
- Tracks progress through sequential operations
- Uses words like "plan", "strategy", "pipeline", "workflow", "steps"

### 3.3 Adding plan tracking

**At plan creation:**

```python
_task = get_current_task()
if _task:
    try:
        _task.plan(
            "Process and route incoming lead",     # goal — what the plan aims to achieve
            [                                       # steps — ordered list of descriptions
                "Search CRM for existing record",
                "Score lead based on criteria",
                "Generate follow-up email",
                "Update CRM with outcome",
            ],
        )
    except Exception:
        pass
```

**As each step progresses:**

```python
# When step starts:
if _task:
    try:
        _task.plan_step(step_index=0, action="started", summary="Searching CRM")
    except Exception:
        pass

# ... step executes ...

# When step completes:
if _task:
    try:
        _task.plan_step(
            step_index=0,
            action="completed",
            summary="Found existing CRM record",
            turns=2,              # optional — LLM turns spent
            tokens=3200,          # optional — tokens spent
        )
    except Exception:
        pass

# If step fails:
if _task:
    try:
        _task.plan_step(step_index=2, action="failed", summary="Email API returned 403")
    except Exception:
        pass
```

### 3.4 Parameter reference

**`task.plan()`:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `goal` | str | **Yes** | What the plan aims to achieve |
| `steps` | list[str] | **Yes** | Ordered step descriptions |
| `revision` | int | No | Plan revision number. Default: `0`. Increment on replan |

**`task.plan_step()`:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `step_index` | int | **Yes** | Zero-based step position |
| `action` | str | **Yes** | `"started"`, `"completed"`, `"failed"`, `"skipped"` |
| `summary` | str | **Yes** | Description or outcome note |
| `total_steps` | int | No | Auto-inferred from `task.plan()` if previously called |
| `turns` | int | No | LLM turns spent (on completion/failure) |
| `tokens` | int | No | Tokens spent (on completion/failure) |
| `plan_revision` | int | No | Correlates with `task.plan()` revision |

### 3.5 Replanning

If the agent changes its plan mid-task, call `task.plan()` again with an incremented `revision`:

```python
_task.plan("Revised: Route to manual review", ["Notify manager", "Queue for review"], revision=1)
```

The dashboard shows the latest plan. Previous plan events remain in the timeline for the full history.

---

## 4. Escalations

### 4.1 What it does

`task.escalate()` records when an agent decides it cannot handle something alone and hands it off — to a human, another team, or another agent.

### 4.2 Finding WHERE in your code

Look for code that:
- Sends alerts or notifications to humans
- Transfers work to another agent or queue
- Decides "I can't handle this" and routes elsewhere
- Logs a warning that requires human attention

### 4.3 Adding escalation tracking

```python
_task = get_current_task()
if _task:
    try:
        _task.escalate(
            reason="Lead score below threshold (0.2) — needs manual review",
            assigned_to="sales-team",          # optional — who receives the escalation
        )
    except Exception:
        pass
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `reason` | str | **Yes** | Why the agent escalated |
| `assigned_to` | str | No | Who or what receives the escalation |

### 4.4 Dashboard impact

- Amber node in the Timeline labeled with the reason
- `escalated` event in the Activity Stream
- Visible under the "human" stream filter

---

## 5. Approvals

### 5.1 What it does

`task.request_approval()` and `task.approval_received()` track the human-in-the-loop approval workflow. The agent asks for permission, waits, and records the decision.

### 5.2 Finding WHERE in your code

Look for code that:
- Pauses execution waiting for human input
- Sends approval requests to a queue, Slack, email, or UI
- Checks for approval status in a loop or callback
- Has "pending", "approved", "rejected" states

### 5.3 Adding approval tracking

**When requesting approval:**

```python
_task = get_current_task()
if _task:
    try:
        _task.request_approval(
            approver="ops-queue",                          # who should approve
            reason="Contract emails require human review", # why
        )
    except Exception:
        pass

# Agent enters waiting state...
```

**When approval is received:**

```python
if _task:
    try:
        _task.approval_received(
            approved_by="jane@acme.com",    # who approved/rejected
            decision="approved",             # "approved" or "rejected"
        )
    except Exception:
        pass

# Agent continues (or handles rejection)...
```

### 5.4 Parameter reference

**`task.request_approval()`:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `approver` | str | **Yes** | Who should approve (person, queue, team) |
| `reason` | str | No | What needs approval and why |

**`task.approval_received()`:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `approved_by` | str | **Yes** | Who made the decision |
| `decision` | str | **Yes** | `"approved"` or `"rejected"` |

### 5.5 Dashboard impact

- Agent badge changes to **WAITING** (amber) after `request_approval()`
- Agent badge returns to **PROCESSING** after `approval_received()`
- **Waiting** count in Stats Ribbon increments/decrements
- Amber (request) and green/red (decision) nodes in Timeline
- Visible under the "human" stream filter
- If approvals pile up (Waiting count stays high), your review process is a bottleneck

---

## 6. Retries

### 6.1 What it does

`task.retry()` records when an agent retries a failed operation — rate limits, transient errors, timeouts. This makes retry patterns visible on the timeline.

### 6.2 Finding WHERE in your code

Look for code that:
- Catches exceptions and retries
- Has `for attempt in range(max_retries):` loops
- Uses backoff libraries (`tenacity`, `backoff`)
- Has retry counters or sleep-between-attempts patterns

### 6.3 Adding retry tracking

```python
for attempt in range(max_retries):
    try:
        result = call_external_api()
        break
    except RateLimitError as e:
        _task = get_current_task()
        if _task:
            try:
                _task.retry(
                    attempt=attempt + 1,
                    reason=f"Rate limited: {e}",
                    backoff_seconds=2 ** attempt,     # optional
                )
            except Exception:
                pass
        time.sleep(2 ** attempt)
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `attempt` | int | **Yes** | Attempt number (1-based) |
| `reason` | str | **Yes** | Why the retry happened |
| `backoff_seconds` | float | No | How long before the next attempt |

### 6.4 Dashboard impact

- Retry nodes appear in the Timeline, showing how many attempts were needed
- Timeline branching shows the retry path
- Helps identify: Are retries common? Which operations trigger them? How much time is lost to retries?

---

## 7. Issue Reporting

### 7.1 What it does

`agent.report_issue()` lets agents self-report persistent problems — not task failures (those are tracked automatically), but ongoing issues like API permission errors, data quality degradation, or connectivity problems.

### 7.2 Finding WHERE in your code

Look for code that:
- Logs warnings about external service problems
- Detects degraded conditions (slow responses, partial failures)
- Catches errors that don't fail the task but indicate a problem
- Has "circuit breaker" or "health check" patterns

### 7.3 Adding issue reporting

```python
# When the agent detects a persistent problem:
try:
    hiveloop_agent.report_issue(
        summary="CRM API returning 403 for workspace queries",
        severity="high",                       # "critical", "high", "medium", "low"
        category="permissions",                 # optional classification
        context={                               # optional debugging info
            "tool": "crm_search",
            "error_code": 403,
            "last_seen": "2026-02-11T14:30:00Z",
        },
        issue_id="issue_crm_403",              # optional — enables explicit tracking
        occurrence_count=3,                     # optional — how many times so far
    )
except Exception:
    pass

# When the issue is resolved:
try:
    hiveloop_agent.resolve_issue(
        summary="CRM API returning 403 for workspace queries",
        issue_id="issue_crm_403",
    )
except Exception:
    pass
```

### 7.4 Parameter reference

**`agent.report_issue()`:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | str | **Yes** | Issue title. Used for deduplication if no `issue_id` |
| `severity` | str | **Yes** | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `category` | str | No | `"permissions"`, `"connectivity"`, `"configuration"`, `"data_quality"`, `"rate_limit"`, `"other"` |
| `context` | dict | No | Arbitrary debugging context |
| `issue_id` | str | No | Stable identifier for lifecycle tracking. If omitted, server deduplicates by summary hash |
| `occurrence_count` | int | No | Agent-tracked count of occurrences |

**`agent.resolve_issue()`:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `summary` | str | **Yes** | Must match the original report (or use `issue_id`) |
| `issue_id` | str | No | If provided on report, use the same ID |

### 7.5 Dashboard impact

- Red issue badge on the agent card in The Hive (e.g. "● 1 issue")
- Issues table in the Agent Detail → Pipeline tab with severity, category, and occurrence count
- `issue` events in the Activity Stream
- Issues persist until explicitly resolved — they don't auto-clear

### 7.6 Issues vs. task failures

| Concept | Example | HiveLoop feature |
|---------|---------|-----------------|
| **Task failure** | "This specific task threw an exception" | Automatic — `agent.task()` catches it |
| **Issue** | "CRM API has been returning 403 for the last hour" | Manual — `agent.report_issue()` |

Task failures are per-task and transient. Issues are per-agent and persistent. A task can fail without an issue (transient error), and an issue can exist without any task failing (degraded performance).

---

## 8. Pipeline Enrichment

Pipeline events give the dashboard visibility into the agent's operational context beyond individual tasks — its work queue, tracked work items, and scheduled recurring work.

### 8.1 Queue snapshots

Report the current state of the agent's work queue:

```python
hiveloop_agent.queue_snapshot(
    depth=4,                          # items currently queued
    oldest_age_seconds=120,           # how old the oldest item is
    items=[                           # optional — individual items
        {"id": "evt_001", "priority": "high", "source": "human",
         "summary": "Review contract draft", "queued_at": "2026-02-11T14:28:00Z"},
        {"id": "evt_002", "priority": "normal", "source": "webhook",
         "summary": "Process CRM update", "queued_at": "2026-02-11T14:29:00Z"},
    ],
    processing={"id": "evt_003", "summary": "Sending email",   # optional — what's being processed now
                "started_at": "2026-02-11T14:29:30Z", "elapsed_ms": 4500},
)
```

**Dashboard impact:** Queue depth badge on the agent card (e.g. "Q:4"), Queue section in Pipeline tab.

**Best practice:** Call `queue_snapshot()` on a periodic schedule (e.g. every heartbeat) rather than on every queue change. The `queue_provider` callback on `hb.agent()` automates this — it's called every heartbeat cycle:

```python
def my_queue_provider():
    return {"depth": len(my_queue), "items": [...]}

agent = hb.agent("my-agent", queue_provider=my_queue_provider)
```

### 8.2 TODOs

Track work items the agent is managing:

```python
# Created
hiveloop_agent.todo("todo_001", action="created", summary="Follow up with client", priority="high")

# Completed
hiveloop_agent.todo("todo_001", action="completed", summary="Follow up with client")

# Other actions: "failed", "dismissed", "deferred"
```

**Dashboard impact:** Active TODOs table in Pipeline tab.

### 8.3 Scheduled work

Report recurring work the agent is configured to perform:

```python
hiveloop_agent.scheduled(items=[
    {"name": "CRM sync", "next_run": "2026-02-12T15:00:00Z", "interval": "1h", "status": "active"},
    {"name": "Report generation", "next_run": "2026-02-13T09:00:00Z", "interval": "daily", "status": "active"},
])
```

**Dashboard impact:** Scheduled work table in Pipeline tab.

---

## 9. Agent-Level Events (Outside Task Context)

Most Layer 2 events happen inside a task (`task.llm_call()`, `task.plan()`, etc.). But some events are agent-level — they happen independently of any task.

### 9.1 Agent-level methods

| Method | When to use | Requires task context? |
|--------|------------|----------------------|
| `agent.llm_call()` | LLM calls during startup, background maintenance, heartbeat generation | No |
| `agent.report_issue()` | Persistent problems detected outside task execution | No |
| `agent.resolve_issue()` | Previously reported issue resolved | No |
| `agent.queue_snapshot()` | Queue state reporting (typically on heartbeat) | No |
| `agent.todo()` | Work item lifecycle | No |
| `agent.scheduled()` | Recurring work configuration | No |
| `agent.event()` | Any custom agent-level event | No |

### 9.2 The pattern: task-level with agent-level fallback

For events that might happen either inside or outside a task:

```python
_task = get_current_task()
if _task:
    # Preferred — event is attributed to the task and its project
    _task.llm_call("reasoning", model=model_name, tokens_in=N, tokens_out=N)
elif hiveloop_agent:
    # Fallback — event is agent-level, no task/project attribution
    hiveloop_agent.llm_call("reasoning", model=model_name, tokens_in=N, tokens_out=N)
```

Agent-level events appear in the Cost Explorer and Activity Stream but not on any task timeline.

---

## 10. Cost Estimation

### 10.1 Why cost matters

LLM costs can be invisible and explosive. An agent that runs smoothly can quietly spend $40/hour if it's using an expensive model with large prompts. `task.llm_call()` with cost tracking surfaces this — per call, per task, per agent, per model.

### 10.2 Three approaches to cost

**Approach A — Don't calculate cost (simplest):**

Just send `model`, `tokens_in`, `tokens_out`. The dashboard shows token counts and groups by model. You can calculate cost yourself from the model tables.

```python
_task.llm_call("reasoning", model="claude-sonnet-4-5-20250929", tokens_in=1500, tokens_out=200)
```

**Approach B — Cost helper function:**

Build a lookup table and calculate cost locally:

```python
COST_PER_MILLION = {
    "claude-sonnet-4-5-20250929":  {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001":   {"input": 0.80, "output": 4.00},
    "claude-3-haiku-20240307":     {"input": 0.25, "output": 1.25},
    "gpt-4o":                      {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":                 {"input": 0.15, "output": 0.60},
}

def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    rates = COST_PER_MILLION.get(model)
    if not rates:
        return None
    return (tokens_in * rates["input"] / 1_000_000) + (tokens_out * rates["output"] / 1_000_000)
```

Usage:

```python
_task.llm_call(
    "reasoning",
    model=model_name,
    tokens_in=tokens_in,
    tokens_out=tokens_out,
    cost=estimate_cost(model_name, tokens_in, tokens_out),
)
```

**Approach C — Use LLM client's cost reporting:**

Some LLM clients report cost directly:

```python
# LiteLLM
response._hidden_params.get("response_cost")

# Custom wrappers may expose .cost or .total_cost
```

If your client reports cost, use it directly — it's more accurate than a lookup table.

### 10.3 Keeping the cost table updated

LLM pricing changes. When it does, update your `COST_PER_MILLION` table. Old events keep their recorded cost (it's stored per-event, not recalculated). New events use the updated rates.

If you don't want to maintain a cost table, Approach A (tokens only, no cost) is perfectly fine. The Cost Explorer still shows token counts and call counts grouped by model — you can calculate spend externally.

---

## 11. What to Expect on the Dashboard

### 11.1 After adding `task.llm_call()`

| Dashboard element | Change |
|------------------|--------|
| **Task Table** | LLM column shows call count (e.g. "◆ 3"). COST column shows dollar amount |
| **Timeline** | Purple nodes appear for each LLM call, with model badge above the node |
| **Timeline detail** | Click an LLM node → shows model, tokens, cost, duration, and previews (if sent) |
| **Cost Explorer** | Fully functional — Cost by Model table, Cost by Agent table, Cost Ribbon totals |
| **Stats Ribbon** | Cost (1h) shows dollar amount |
| **Mini-Charts** | LLM Cost/Task chart populates |
| **Activity Stream** | "llm" filter shows every LLM call |

### 11.2 After adding `task.plan()` + `task.plan_step()`

| Dashboard element | Change |
|------------------|--------|
| **Timeline** | Plan progress bar appears above the timeline track |
| **Plan bar** | Each step is a segment: gray (not started), blue (in progress), green (completed), red (failed) |
| **Plan bar hover** | Hover a segment to see the step description |

### 11.3 After adding escalations and approvals

| Dashboard element | Change |
|------------------|--------|
| **Agent card** | WAITING badge when approval is pending |
| **Stats Ribbon** | Waiting count increments |
| **Timeline** | Amber escalation nodes, approval request/decision nodes |
| **Activity Stream** | "human" filter shows escalations and approvals |

### 11.4 After adding issue reporting

| Dashboard element | Change |
|------------------|--------|
| **Agent card** | Red issue badge (e.g. "● 1 issue") |
| **Pipeline tab** | Active Issues table with severity, category, occurrences |
| **Activity Stream** | Issue events appear with warning icons |

### 11.5 After adding pipeline enrichment

| Dashboard element | Change |
|------------------|--------|
| **Agent card** | Queue depth badge (e.g. "Q:4", amber if >5) |
| **Pipeline tab** | Queue, TODOs, and Scheduled sections populate |

---

## 12. Incremental Adoption Strategy

You don't need to implement all of Layer 2 at once. Here's the recommended order, based on value per effort:

### Tier 1 — High value, low effort (do these first)

| Event | Effort | Why it's high value |
|-------|--------|-------------------|
| `task.llm_call()` | 2-5 lines per LLM call site | Unlocks the entire Cost Explorer. Answers "how much is this costing me?" |
| `agent.report_issue()` | 1-3 lines per detection point | Surfaces persistent problems that silent monitoring would miss |

### Tier 2 — High value, medium effort

| Event | Effort | Why |
|-------|--------|-----|
| `task.plan()` + `task.plan_step()` | 5-10 lines at plan creation + 2 lines per step transition | Visual progress tracking. Answers "where in the plan did it fail?" |
| `task.escalate()` | 1-2 lines per escalation point | Answers "when does the agent need human help?" |

### Tier 3 — Medium value, depends on architecture

| Event | Effort | Why |
|-------|--------|-----|
| `task.request_approval()` + `task.approval_received()` | 2-4 lines per approval workflow | Only valuable if you have human-in-the-loop approval flows |
| `task.retry()` | 2-3 lines per retry loop | Only valuable if retries are common and you need to diagnose patterns |
| `agent.queue_snapshot()` | 5-10 lines + queue access | Only valuable if your agent has a work queue |

### Tier 4 — Nice to have

| Event | Effort | Why |
|-------|--------|-----|
| `agent.todo()` | 1-2 lines per todo lifecycle event | Work item tracking — useful for complex agents |
| `agent.scheduled()` | 1-2 lines at configuration time | Scheduled work visibility — useful for cron-based agents |

### The practical path

1. Add `task.llm_call()` to your 2-3 most important LLM call sites → validate on Cost Explorer
2. Add `agent.report_issue()` where you have error handling → check Pipeline tab
3. Add plans if your agent creates them → watch the plan progress bar
4. Add escalation/approval tracking if you have human-in-the-loop → monitor the Waiting count
5. Add pipeline enrichment last → agent cards get richer

At each step, check the dashboard to confirm the new data appears before moving on.

---

## 13. Troubleshooting Layer 2

### 13.1 LLM calls not appearing on Timeline

**Symptom:** You added `task.llm_call()` but no purple nodes appear.

**Possible causes:**
1. `get_current_task()` returns `None` — the LLM call is happening outside a task context. Check that `agent.task()` wraps the call site and `set_current_task()` has been called.
2. The `try/except` is swallowing an error. Temporarily remove the `try/except`, trigger a task, and check for exceptions.
3. Token fields are wrong type. `tokens_in` and `tokens_out` must be integers, not strings. `cost` must be a float.

### 13.2 Cost Explorer shows zero despite LLM calls appearing

**Symptom:** Purple nodes appear in Timeline, but Cost Explorer shows $0.00.

**Cause:** You're sending `model` and `name` but not `tokens_in`, `tokens_out`, or `cost`. The Cost Explorer aggregates cost and token data — if those fields are `None`, the call appears in the timeline (which only needs name + model) but has nothing to aggregate for cost.

**Fix:** Add token counts. Even without cost, `tokens_in` and `tokens_out` populate the "Tokens In" and "Tokens Out" columns in the Cost Explorer.

### 13.3 Token counts are always zero or None despite sending them

**Symptom:** You're passing `tokens_in` and `tokens_out` but the values are always 0 or None on the dashboard.

**Cause:** You're extracting tokens from the wrong place. This is the most common integration mistake. Different client methods in the same codebase often expose tokens differently:

- Method A might put tokens on the **response**: `response.usage.input_tokens`
- Method B might put tokens on the **client**: `client._last_input_tokens`
- Method C might not expose tokens at all

If you're using the Pattern A extraction (`response.usage.*`) on a call site that uses Pattern B (`client._last_*`), you'll get `AttributeError` (caught by your `try/except`, so the call silently sends without tokens) or `None`.

**Fix:** Go back to the catalog (Section 2.2) and verify the token source for the specific call site. Use the discovery recipe in Section 2.6 to inspect what each response and client object actually exposes.

### 13.4 Plan progress bar not visible

**Symptom:** You called `task.plan()` but no progress bar appears above the Timeline.

**Possible causes:**
1. The plan event was emitted but the Timeline is showing a different task. Click the task that has the plan in the Task Table.
2. The plan event was emitted outside the task context (`get_current_task()` was `None`).
3. `steps` parameter was empty. The plan needs at least one step to render.

### 13.5 Issues not clearing from Pipeline tab

**Symptom:** You called `agent.resolve_issue()` but the issue still appears.

**Cause:** The `summary` or `issue_id` in `resolve_issue()` doesn't match the original `report_issue()` call. Deduplication is hash-based — the strings must match exactly.

**Fix:** Use `issue_id` for explicit lifecycle tracking. It's more reliable than matching summary strings.

### 13.6 Agent card shows queue badge but Pipeline tab queue is empty

**Symptom:** Agent card shows "Q:4" but the Pipeline → Queue section says empty.

**Cause:** The `queue_snapshot()` sent `depth=4` but no `items` array. The badge uses `depth` (just a number), but the Pipeline table needs `items` (the actual queue entries).

**Fix:** Include the `items` array in your `queue_snapshot()` call for full Pipeline tab rendering.

### 13.7 Events not appearing in Activity Stream filters

**Symptom:** LLM calls exist but the "llm" filter shows nothing. Or escalations exist but "human" filter is empty.

**Cause:** The stream filter maps event types to categories. Custom events (which is what `task.llm_call()` emits internally — `event_type: "custom"` with `payload.kind: "llm_call"`) need the dashboard to map the `kind` field to the correct filter.

**Fix:** Verify the dashboard version supports kind-based filtering. If the "llm" filter expects a specific event structure, check that `task.llm_call()` is producing the expected payload shape.

# HiveBoard Insights Engine — Product Spec

**Author:** Juan
**Date:** February 13, 2026
**Status:** Draft — strategic feature definition
**Codename:** HiveMind

---

## 1. Vision

Traditional observability answers "what happened." HiveBoard already does this well — dashboards, timelines, cost breakdowns, activity streams. But AI agents fail in ways that traditional software doesn't. They hallucinate. They get stuck in loops. They waste money re-asking questions. They silently degrade. They look busy while producing nothing.

**The Insights Engine watches the agents and tells you what they're doing wrong and how to fix it.** It analyzes the event stream in real time, detects patterns that indicate waste, failure, or degradation, and delivers actionable recommendations — both as a passive dashboard panel and as active alert notifications.

This is the feature that separates HiveBoard from generic LLM observability tools. LangSmith and Langfuse show you traces. HiveBoard tells you "your lead-qualifier agent is spending $14/day on reasoning calls that could run on Haiku for $1.40, and it's been hallucinating CRM record IDs at a 12% rate since Tuesday."

---

## 2. Architecture Overview

```
Event Stream (existing)
    │
    ▼
┌─────────────────────────────┐
│    Insights Engine           │
│                              │
│  ┌────────────────────────┐  │
│  │ Analyzers (per category)│  │
│  │  • CostAnalyzer         │  │
│  │  • BehaviorAnalyzer     │  │
│  │  • PerformanceAnalyzer  │  │
│  │  • ReliabilityAnalyzer  │  │
│  │  • EfficiencyAnalyzer   │  │
│  │  • CapacityAnalyzer     │  │
│  └──────────┬─────────────┘  │
│             │                │
│  ┌──────────▼─────────────┐  │
│  │ Insight Store           │  │
│  │  (generated insights    │  │
│  │   with severity,        │  │
│  │   recommendations,      │  │
│  │   evidence)             │  │
│  └──────────┬─────────────┘  │
│             │                │
│  ┌──────────▼─────────────┐  │
│  │ Delivery Layer          │  │
│  │  • Dashboard panel      │  │  ← Passive
│  │  • Alert rules          │  │  ← Active
│  │  • API endpoint         │  │  ← Programmatic
│  │  • Webhook / Slack      │  │  ← Push notifications
│  └────────────────────────┘  │
└─────────────────────────────┘
```

Each analyzer runs periodically (configurable, default every 5 minutes), scans recent events, and produces zero or more **insight records**. Insights are deduplicated (same pattern within a cooldown window doesn't generate a new insight), scored by severity, and delivered through both passive and active channels.

---

## 3. Insight Categories

### 3.1 Cost & Budget

#### INS-C01: Prompt Bloat Detection

**Signal:** `tokens_in` is consistently high (>8K) for a call name that produces short outputs (<500 `tokens_out`).

**Detection:** For each unique `call_name`, compute the rolling average `tokens_in` and `tokens_out` over the last N calls. Flag when the input/output ratio exceeds a threshold (default: 15:1) AND `tokens_in` exceeds an absolute minimum (default: 4K).

**Data required:** `llm_call` events with `tokens_in`, `tokens_out`, `call_name`.

**Recommendation:** "**phase1_reasoning** averages 9,200 input tokens but only 340 output tokens (ratio: 27:1). The prompt likely contains context the model isn't using. Consider trimming unused context, summarizing long documents before injection, or using a context-compaction step."

**Severity:** Medium (cost waste, not a failure).

**Implementation complexity:** Low — pure aggregation over existing data.

---

#### INS-C02: Model Downgrade Opportunity

**Signal:** A call name consistently uses an expensive model but the task pattern suggests a cheaper model would work — short structured outputs, high success rate, low complexity indicators.

**Detection:** For each unique `(call_name, model)` pair, check:
- Success rate > 95% (the call never fails or retries)
- Average `tokens_out` < 500 (outputs are short)
- Output looks structured (response_preview starts with `{` or `[`, if available)
- No downstream `action_failed` events correlated with this call's output

If all conditions met AND a cheaper model exists in the same family, flag it.

**Model cost hierarchy (built-in):**

| Tier | Models (examples) | Relative cost |
|---|---|---|
| Premium | claude-opus-*, gpt-4o | 1.0x |
| Standard | claude-sonnet-*, gpt-4o-mini | 0.3x |
| Economy | claude-haiku-*, gpt-3.5-turbo | 0.05x |

**Data required:** `llm_call` events with `model`, `tokens_out`, `cost`, `call_name`. Optionally `response_preview` for structure detection.

**Recommendation:** "**heartbeat_summary** uses claude-sonnet-4-5 at $0.034/call with 100% success rate and avg 77 output tokens. This pattern is suitable for claude-haiku (~$0.003/call). Estimated savings: **$0.93/day ($28/month)** based on current call volume."

**Severity:** Low (optimization opportunity, not a problem).

**Implementation complexity:** Medium — needs model cost lookup table and cross-referencing success rates.

---

#### INS-C03: Cost Spike Detection

**Signal:** Hourly cost exceeds a threshold relative to the rolling baseline.

**Detection:** Compare current-hour cost to the rolling 24h average. Flag when current > 2x average (configurable multiplier). Also flag when a single task's cost exceeds 5x the median for its task type.

**Data required:** `llm_call` events with `cost`, `timestamp`. Task-level: `task_completed` with computed `total_cost`.

**Recommendation (hourly):** "Cost spike detected: **$4.20 in the last hour** vs. $1.80 rolling average. Top contributor: ag_6ce5uncd spent $3.10 on 45 phase1_reasoning calls (normally 20/hour)."

**Recommendation (per-task):** "Task **task_lead-9821** cost $0.48 — 6x the median of $0.08 for lead_processing tasks. 4 retries drove the excess."

**Severity:** High (could indicate runaway loop or misconfiguration).

**Implementation complexity:** Low — rolling window aggregation.

---

#### INS-C04: Budget Burn Rate Projection

**Signal:** At the current spend rate, the tenant will exceed a projected budget threshold.

**Detection:** Extrapolate current daily cost to monthly. If projected monthly > configured threshold, flag with time-to-budget-exhaustion.

**Data required:** `llm_call` events with `cost`, aggregated daily.

**Recommendation:** "At the current rate of **$18.40/day**, projected monthly cost is **$552**. This is a 3.2x increase from last week's average ($5.70/day). Primary driver: main agent's phase1_reasoning call volume doubled on Tuesday."

**Severity:** Medium.

**Implementation complexity:** Low.

---

### 3.2 Behavioral & Quality

#### INS-B01: Empty/No-Value Loop Detection

**Signal:** Agent repeatedly completes tasks of the same type but produces empty or minimal output.

**Detection:** For each `(agent_id, task_type)` pair, check the last N completed tasks. If >50% have no output payload (or output is empty/null/trivially small), AND the task keeps recurring, flag it.

**Data required:** `task_completed` events with payload inspection. Frequency: count of same-type tasks in a rolling window.

**Recommendation:** "**ag_6ce5uncd** completed 14 lead_processing tasks in the last hour with no output payload. This looks like a no-op loop — the agent is doing work but producing no value. Check whether the input data source is returning empty results."

**Severity:** High (wasting compute and cost for zero value).

**Implementation complexity:** Medium — needs output payload inspection heuristic.

---

#### INS-B02: Reasoning Loop / Repetitive Action Cycle

**Signal:** Within a single task, the same sequence of actions repeats more than N times without plan progress.

**Detection:** Within a task's timeline, extract the sequence of `action_name` values. Detect repeating subsequences of length >= 2 that occur >= 3 times. Cross-reference with `plan_step` events — if plan step index doesn't advance during the repetition, the agent is stuck in a loop.

**Data required:** `action_started`/`action_completed` events within a task, `plan_step` events.

**Recommendation:** "Task **task_lead-4821** shows a repeating pattern: `fetch_data → analyze → fetch_data → analyze` repeated 5 times without advancing past plan step 2. The agent appears stuck in a reasoning loop. Check whether the analysis output is being used to modify the next fetch."

**Severity:** High (wasted LLM calls, stalled task).

**Implementation complexity:** High — sequence pattern detection.

---

#### INS-B03: Hallucination Proxy Detection

**Signal:** Agent generates outputs that reference non-existent entities, causing downstream failures.

**Detection:** Multiple proxy signals, individually weak but strong in combination:

| Proxy | Detection method | Confidence |
|---|---|---|
| **Hallucinated entity IDs** | `action_failed` with "not found", "does not exist", "invalid ID" in error message, where the action was called with parameters from a preceding LLM call's output | High |
| **Contradictory outputs** | Within the same task, two LLM calls produce conflicting structured outputs (requires `response_preview` content comparison) | Medium |
| **Tool parameter mismatch** | Agent calls a tool with argument types/formats that don't match the tool's expected schema (e.g., passing a name where an ID is required) | Medium |
| **Plan step description mismatch** | Agent creates a plan step referencing an action that doesn't exist in its tracked action repertoire | Low |

**Composite score:** Each proxy contributes a weighted signal. When composite exceeds threshold, flag.

**Data required:** `action_failed` events with `exception_message`, `llm_call` events with `response_preview` (optional), `action_started` events with parameters.

**Recommendation:** "**main** agent may be hallucinating entity references. In the last hour, 8 out of 52 tool calls failed with 'record not found' errors where the record ID came from the preceding LLM response. Affected calls: `crm_lookup` (5), `email_send` (3). Consider adding input validation before tool execution or few-shot examples showing correct ID formats."

**Severity:** High (agent appears to work but produces incorrect results).

**Implementation complexity:** High — requires cross-event correlation and error message parsing.

---

#### INS-B04: Plan Drift / Excessive Replanning

**Signal:** Agent keeps revising its plan mid-execution, indicating it can't execute its original strategy.

**Detection:** Count `plan_revision` increments within a single task. Flag when revisions exceed a threshold (default: 2) or when final plan has significantly more steps than the original.

**Data required:** `plan_created` and `plan_step` events with `plan_revision` field.

**Recommendation:** "**main** replanned 4 times during task_lead-4821. Original plan had 3 steps; final plan had 7. The agent is struggling with its initial strategy. Review whether the planning prompt has sufficient context about available tools and constraints."

**Severity:** Medium (inefficiency, not necessarily failure).

**Implementation complexity:** Low — direct field inspection.

---

#### INS-B05: Escalation Rate Trend

**Signal:** Escalation rate for an agent or task type is increasing over time.

**Detection:** Compare escalation rate in the current window vs. the previous equivalent window (e.g., last 6h vs. prior 6h, or today vs. yesterday). Flag when rate increases by >50% relative.

**Data required:** `escalated` events, `task_completed`/`task_failed` events for rate denominators.

**Recommendation:** "Escalation rate for **lead_processing** tasks jumped from 8% to 22% in the last 6 hours. 7 out of 32 tasks were escalated (vs. 3 out of 38 in the prior 6h). Most common escalation reason: 'confidence below threshold'. The underlying data quality or prompt may need review."

**Severity:** Medium-High (operational degradation).

**Implementation complexity:** Low.

---

### 3.3 Performance & Latency

#### INS-P01: Slow LLM Call Trend

**Signal:** A specific LLM call name's latency is trending upward or exceeds an absolute threshold.

**Detection:** For each `call_name`, compute rolling p50 and p95 `duration_ms`. Flag when: p95 exceeds absolute threshold (default: 10s), OR p50 has increased >50% vs. 24h-ago baseline.

**Data required:** `llm_call` events with `duration_ms`, `call_name`.

**Recommendation:** "**phase1_reasoning** p95 latency is 8.2s, up from 5.1s 24 hours ago (61% increase). Average `tokens_in` also increased from 6,800 to 9,200 in the same period — the growing input size is likely driving the latency increase. Consider context trimming or chunking."

**Severity:** Medium.

**Implementation complexity:** Low — percentile computation over recent events.

---

#### INS-P02: Tool Latency Outliers

**Signal:** An external tool call's latency spikes well beyond its normal range.

**Detection:** For each `action_name`, compute rolling p50 and p95 `duration_ms`. Flag when p95 exceeds 10x p50 (or absolute threshold).

**Data required:** `action_completed` events with `duration_ms`, `action_name`.

**Recommendation:** "**workspace_read** p95 latency jumped from 25ms to 1.8s in the last hour. 12 of 340 calls exceeded 500ms. This suggests intermittent external service degradation. The 12 slow calls all occurred between 14:22 and 14:25."

**Severity:** Medium (external dependency, not agent logic).

**Implementation complexity:** Low.

---

#### INS-P03: Queue Aging / Throughput Ceiling

**Signal:** Queue depth is growing because the agent can't keep up with inbound work.

**Detection:** From `queue_snapshot` events: if `queue_depth` has a positive trend over the last N snapshots AND `oldest_age` exceeds a threshold (default: 5 minutes), the agent is falling behind.

**Data required:** `queue_snapshot` events with `queue_depth`, `oldest_age`.

**Recommendation:** "**ag_6ce5uncd**'s queue depth has grown from 2 to 18 in the last 3 hours. Oldest queued item is 45 minutes old. The agent processes ~120 tasks/hour but appears to be receiving ~140/hour. Consider scaling to additional agent instances or prioritizing the queue."

**Severity:** High (work is being delayed).

**Implementation complexity:** Low — trend detection on queue snapshots.

---

#### INS-P04: Partial Stuckness

**Signal:** Agent is alive (heartbeating) but hasn't completed a task in an abnormally long time.

**Detection:** Agent's last heartbeat is recent (not stuck by existing definition), but time since last `task_completed` exceeds 3x the agent's average task duration.

**Data required:** `heartbeat` events (latest), `task_completed` events (latest + rolling average duration).

**Recommendation:** "**main** has been processing task_lead-9821 for 12 minutes. Average task duration is 27 seconds. The agent is alive but appears stuck on this task. Check the timeline for a pending approval or a long-running tool call."

**Severity:** High.

**Implementation complexity:** Low — timestamp comparison.

---

### 3.4 Reliability & Errors

#### INS-R01: Silent Failure Pattern

**Signal:** Agent completes tasks successfully, but internal actions within those tasks are failing and being swallowed.

**Detection:** For completed tasks, count the number of `action_failed` events within each task. If >30% of recently completed tasks contain at least one failed action, the agent has a silent failure pattern.

**Data required:** `task_completed` events cross-referenced with `action_failed` events by `task_id`.

**Recommendation:** "Last 20 tasks for **main** completed successfully, but 35% contained at least one failed internal action. Most common: `crm_update` failing with 'permission denied' (7 occurrences). These errors are being caught but not resolved — the agent proceeds without completing the CRM update. Consider making this action critical or adding retry logic."

**Severity:** High (agent appears healthy but is silently incomplete).

**Implementation complexity:** Medium — cross-task event correlation.

---

#### INS-R02: Retry Storm Detection

**Signal:** Retry rate per task spikes above normal, indicating a systemic issue.

**Detection:** Compute average `retry_started` events per task in the current window. Flag when it exceeds a threshold (default: 3 retries/task) or doubles vs. baseline.

**Data required:** `retry_started` events, `task_started` events for rate denominator.

**Recommendation:** "**lead_processing** retry rate jumped to 4.2 retries/task (was 0.8 yesterday). Most retried action: `crm_search` (85% of retries). Most common error: 'API rate limit exceeded'. The CRM API may be throttling requests — consider adding backoff or reducing call frequency."

**Severity:** High (systemic issue, cost multiplier).

**Implementation complexity:** Low.

---

#### INS-R03: Error Category Clustering

**Signal:** A single error type suddenly dominates, suggesting an external dependency issue rather than diverse agent logic failures.

**Detection:** Group `action_failed` events by `exception_type` in a rolling window. If one error type accounts for >60% of all errors, flag it as a likely systemic/external issue.

**Data required:** `action_failed` events with `exception_type` (from payload).

**Recommendation:** "78% of errors in the last hour are **ConnectionTimeout** (23 of 29 failures). This is concentrated on `crm_search` and `crm_update` actions. Likely cause: external CRM API degradation. Agent logic is probably fine — the dependency is the issue."

**Severity:** High.

**Implementation complexity:** Low — grouping and percentage calculation.

---

#### INS-R04: Recovery Rate Degradation

**Signal:** Of tasks that encounter errors, fewer are completing successfully over time.

**Detection:** For tasks that contain at least one `action_failed` or `retry_started` event, compute the percentage that still reach `task_completed`. Track this rate over time. Flag when it drops >20% from baseline.

**Data required:** `task_completed`, `task_failed`, `action_failed`, `retry_started` events, correlated by `task_id`.

**Recommendation:** "Recovery rate for **main** dropped from 85% to 62% in the last 12 hours. Previously, 85% of tasks that encountered errors still completed successfully. Now only 62% recover. The agent's error handling may be degrading, or error severity has increased."

**Severity:** Medium-High.

**Implementation complexity:** Medium.

---

### 3.5 Efficiency & Waste

#### INS-E01: Redundant LLM Calls

**Signal:** The same `call_name` fires multiple times in a single task with near-identical input token counts, suggesting the agent is re-asking the same question.

**Detection:** Within a single task's timeline, group `llm_call` events by `call_name`. For groups with >1 call, compare `tokens_in` values. If values are within 10% of each other, flag as potentially redundant.

**Data required:** `llm_call` events with `call_name`, `tokens_in`, grouped by `task_id`.

**Recommendation:** "**phase1_reasoning** was called 3 times in task_lead-4821 with near-identical input tokens (9,587 / 9,601 / 9,543). The agent may be re-running the same reasoning without new information. Cost of redundant calls: $0.068. Consider caching the reasoning output or checking for existing results before re-calling."

**Severity:** Medium (direct cost waste).

**Implementation complexity:** Medium.

---

#### INS-E02: Token Waste Ratio

**Signal:** Consistently sending massive context for trivial outputs across all calls.

**Detection:** Compute the overall `tokens_in / tokens_out` ratio per agent. Flag when ratio consistently exceeds a threshold (default: 20:1). Industry benchmark for productive calls is roughly 3:1 to 8:1.

**Data required:** `llm_call` events with `tokens_in`, `tokens_out`, aggregated per agent.

**Recommendation:** "**ag_6ce5uncd** has an overall input/output token ratio of 24:1 (48K in, 2K out over 18 calls). This suggests the agent is sending large context windows but getting minimal output. Consider whether all context is necessary, or whether a retrieval step could select only relevant portions."

**Severity:** Medium.

**Implementation complexity:** Low.

---

#### INS-E03: Unused Tool Results

**Signal:** Agent calls a tool but the result doesn't influence subsequent actions.

**Detection:** In a task's timeline, identify sequences where `action_completed` (tool result) is immediately followed by an LLM call that doesn't reference the tool's output (detectable if `tokens_in` doesn't increase, or if `response_preview` doesn't reference the tool's action name). This is a weak signal and best combined with patterns where the same tool call is always followed by the same next action regardless of tool output.

**Data required:** `action_completed` events, subsequent `llm_call` events with `tokens_in` and optionally `prompt_preview`.

**Recommendation:** "**workspace_read** was called 12 times by ag_6ce5uncd in the last hour, but the subsequent LLM call's input tokens didn't increase in 9 of those cases. The tool result may not be reaching the LLM context. Check the prompt construction to ensure tool outputs are injected."

**Severity:** Medium.

**Implementation complexity:** High — requires sequential event correlation and heuristic analysis.

---

#### INS-E04: Over-Instrumentation Noise

**Signal:** A tracked action always succeeds instantly with no meaningful variation — the `@track()` decorator adds overhead without observability value.

**Detection:** For each `action_name`, check: 100% success rate, avg `duration_ms` < 10ms, no meaningful payload variation. If all conditions are met across >100 occurrences, flag it.

**Data required:** `action_completed` events with `duration_ms`, `action_name`.

**Recommendation:** "**cleanup_temp** completed 847 times with 100% success rate and avg 2ms duration. This action adds observability overhead without diagnostic value. Consider removing `@agent.track()` from this function to reduce event volume."

**Severity:** Low (optimization suggestion).

**Implementation complexity:** Low.

---

### 3.6 Operational & Capacity

#### INS-O01: Throughput Ceiling Detection

**Signal:** Task completion rate plateaus while queue depth grows — the agent is at capacity.

**Detection:** Compute tasks_completed/hour as a rolling metric. If the rate has been flat (±10%) for >2 hours while queue depth trends upward, the agent has hit its throughput ceiling.

**Data required:** `task_completed` events (rate), `queue_snapshot` events (depth trend).

**Recommendation:** "**ag_6ce5uncd** is processing ~120 tasks/hour (stable for 4 hours) but queue depth has grown from 2 to 34. The agent has hit its throughput ceiling. Options: (1) Scale to additional agent instances, (2) Prioritize high-value tasks in the queue, (3) Optimize task processing time (current avg: 27s)."

**Severity:** High (growing backlog).

**Implementation complexity:** Medium.

---

#### INS-O02: Time-of-Day Patterns

**Signal:** Error rates, costs, or latency spike at predictable times, suggesting load correlation or external dependency patterns.

**Detection:** Aggregate error rate, cost, and latency by hour-of-day over the last 7 days. Flag hours where any metric consistently exceeds 2x the daily average.

**Data required:** All event types with `timestamp`, aggregated by hour.

**Recommendation:** "**Error rate** for lead_processing spikes between 2:00-3:00 AM UTC every day (avg 28% vs. 6% baseline). This correlates with the CRM batch job window. Consider scheduling agent work to avoid this window or adding CRM-availability checks."

**Severity:** Medium.

**Implementation complexity:** Medium — requires multi-day aggregation.

---

#### INS-O03: Agent Utilization Rate

**Signal:** Agent spends most of its time idle relative to its cost.

**Detection:** Compute ratio of time-in-task (sum of task durations) to total wall-clock time. If utilization is <20% but cost per task is high, the agent is expensive relative to its output.

**Data required:** `task_completed` events with `duration_ms`, `heartbeat` events for uptime calculation, `llm_call` events for cost.

**Recommendation:** "**main** has 12% utilization — active for 43 minutes out of 6 hours of uptime. Cost during this period: $5.01. Cost per active minute: $0.12. If the idle time is unavoidable (waiting for inbound work), consider switching to an on-demand execution model."

**Severity:** Low (efficiency insight).

**Implementation complexity:** Medium.

---

## 4. Insight Record Schema

Every generated insight follows a standard schema:

```json
{
    "insight_id": "ins_C01_ag6ce5uncd_20260213T1432",
    "code": "INS-C01",
    "category": "cost",
    "title": "Prompt bloat detected: phase1_reasoning",
    "severity": "medium",
    "agent_id": "ag_6ce5uncd",
    "task_type": "lead_processing",
    "description": "phase1_reasoning averages 9,200 input tokens but only 340 output tokens (ratio: 27:1).",
    "recommendation": "Consider trimming unused context or using a summarization pass.",
    "evidence": {
        "call_name": "phase1_reasoning",
        "avg_tokens_in": 9200,
        "avg_tokens_out": 340,
        "ratio": 27.1,
        "sample_size": 45,
        "window": "last_6h"
    },
    "impact": {
        "estimated_monthly_savings_usd": 28.00,
        "affected_calls_per_day": 120,
        "confidence": 0.85
    },
    "first_detected_at": "2026-02-13T14:32:00Z",
    "last_detected_at": "2026-02-13T14:32:00Z",
    "occurrences": 1,
    "status": "active",
    "dismissed_at": null,
    "dismissed_by": null
}
```

### Key fields

| Field | Purpose |
|---|---|
| `code` | Machine identifier (INS-C01, INS-B03, etc.) for programmatic handling |
| `severity` | `critical`, `high`, `medium`, `low` — drives alert routing and dashboard ordering |
| `evidence` | Structured data backing the insight — different per insight type |
| `impact` | Estimated cost/time/reliability impact — enables prioritization |
| `occurrences` | Dedup count — same pattern detected N times (bumps `last_detected_at`) |
| `status` | `active`, `dismissed`, `resolved` — user lifecycle |

---

## 5. Delivery: Passive (Dashboard)

### 5.1 Insights Panel

A new panel on the dashboard — either as a fourth top-level tab (Dashboard, Costs, Pipeline, **Insights**) or as a collapsible section within the Dashboard view.

```
┌──────────────────────────────────────────────────────────────┐
│  Insights                                    12 active  3 new │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  🔴 HIGH  Silent failures in main                    2h ago   │
│  35% of completed tasks contain swallowed action errors.      │
│  Most common: crm_update 'permission denied'                  │
│  → Review error handling for crm_update                       │
│                                                    [Dismiss]  │
│                                                               │
│  🟠 MEDIUM  Model downgrade opportunity              4h ago   │
│  heartbeat_summary on claude-sonnet could use haiku.          │
│  Estimated savings: $28/month                                 │
│  → Switch heartbeat_summary calls to claude-haiku             │
│                                                    [Dismiss]  │
│                                                               │
│  🔴 HIGH  Cost spike: $4.20/hour                     1h ago   │
│  2.3x rolling average. Top driver: ag_6ce5uncd                │
│  phase1_reasoning call volume doubled                         │
│  → Investigate ag_6ce5uncd workload increase                  │
│                                                    [Dismiss]  │
│                                                               │
│  🟡 LOW   Token waste ratio 24:1 for ag_6ce5uncd    6h ago   │
│  48K tokens in, 2K out over 18 calls.                         │
│  → Review context injection in prompt templates               │
│                                                    [Dismiss]  │
│                                                               │
│  ...                                                          │
│                                                [Show all →]   │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 Inline indicators

Beyond the dedicated panel, insights should surface contextually where the user is already looking:

| Location | Indicator |
|---|---|
| Agent card (Hive view) | Small badge: "3 insights" with highest severity color |
| Cost Explorer — model/agent row | Icon if a downgrade opportunity or cost spike exists for that row |
| Timeline — LLM node | Small icon if the call is flagged (bloated prompt, redundant, slow) |
| Activity stream — LLM card | Tag: "⚠ bloated prompt" or "⚠ possible hallucination" |

These are non-blocking — small visual hints that something deserves attention. Clicking the indicator opens the full insight detail.

### 5.3 Agent Detail — Insights Tab

When viewing a specific agent's detail, show all insights related to that agent. This is the natural place to review and act on agent-specific recommendations.

---

## 6. Delivery: Active (Alerts & Notifications)

### 6.1 Alert Rule Integration

Insights integrate with the existing alert system defined in the data model spec. Each insight code can be configured as an alert trigger:

```json
{
    "rule_id": "rule_insight_silent_failure",
    "condition_type": "insight_detected",
    "condition_config": {
        "insight_codes": ["INS-R01"],
        "min_severity": "high"
    },
    "actions": [
        { "type": "webhook", "url": "https://hooks.slack.com/..." },
        { "type": "email", "to": "ops@company.com" }
    ],
    "cooldown_seconds": 3600
}
```

### 6.2 Default Alert Rules

Out-of-the-box alerts that ship enabled (user can disable):

| Insight codes | Default action | Cooldown |
|---|---|---|
| INS-C03 (cost spike) | Dashboard notification | 1 hour |
| INS-B01 (empty loop) | Dashboard notification | 1 hour |
| INS-B03 (hallucination signals) | Dashboard notification | 30 min |
| INS-R01 (silent failures) | Dashboard notification | 1 hour |
| INS-R02 (retry storm) | Dashboard notification | 30 min |
| INS-P03 (queue aging) | Dashboard notification | 2 hours |
| INS-P04 (partial stuckness) | Dashboard notification | 15 min |

### 6.3 Notification Bell

A notification icon in the top bar (next to "Connected" status) with an unread count badge. Clicking opens a dropdown of recent alert-triggered insights.

```
┌─────────────────────────────────────────────┐
│  🔔 Notifications                    3 new  │
├─────────────────────────────────────────────┤
│  🔴 Silent failures in main         2h ago  │
│  🔴 Cost spike: $4.20/hour          1h ago  │
│  🟠 Hallucination signals: main     45m ago │
│                                             │
│  [View all insights →]                      │
└─────────────────────────────────────────────┘
```

### 6.4 Webhook / Slack Push

For teams that want alerts outside the dashboard:

```
POST https://hooks.slack.com/services/...
{
    "text": "🔴 HiveBoard Insight: Silent failures in main",
    "blocks": [
        {
            "type": "section",
            "text": "35% of completed tasks contain swallowed action errors.\nMost common: crm_update 'permission denied'\n\n*Recommendation:* Review error handling for crm_update"
        },
        {
            "type": "actions",
            "elements": [
                { "type": "button", "text": "View in HiveBoard", "url": "https://app.hiveboard.io/insights/ins_R01_main_..." }
            ]
        }
    ]
}
```

---

## 7. Implementation Priorities

Insights are ordered by the intersection of detection simplicity and user value. Phase 1 delivers the highest-value, lowest-complexity insights.

### Phase 1 — Quick wins (existing data, simple aggregation)

| Code | Insight | Complexity | Value |
|---|---|---|---|
| INS-C03 | Cost spike detection | Low | High |
| INS-C01 | Prompt bloat detection | Low | High |
| INS-C04 | Budget burn rate projection | Low | Medium |
| INS-P01 | Slow LLM call trend | Low | Medium |
| INS-P02 | Tool latency outliers | Low | Medium |
| INS-R02 | Retry storm detection | Low | High |
| INS-R03 | Error category clustering | Low | High |
| INS-P04 | Partial stuckness | Low | High |
| INS-E02 | Token waste ratio | Low | Medium |
| INS-E04 | Over-instrumentation noise | Low | Low |

**Estimated effort:** 2-3 days for the analysis engine + dashboard panel. Each analyzer is 20-50 lines of aggregation logic over existing event data.

### Phase 2 — Cross-event correlation

| Code | Insight | Complexity | Value |
|---|---|---|---|
| INS-C02 | Model downgrade opportunity | Medium | High |
| INS-B01 | Empty loop detection | Medium | High |
| INS-B05 | Escalation rate trend | Medium | Medium |
| INS-R01 | Silent failure pattern | Medium | High |
| INS-R04 | Recovery rate degradation | Medium | Medium |
| INS-E01 | Redundant LLM calls | Medium | Medium |
| INS-O01 | Throughput ceiling | Medium | High |
| INS-O03 | Agent utilization rate | Medium | Medium |
| INS-P03 | Queue aging | Low | High |

**Estimated effort:** 3-5 days. Requires correlating events across tasks and computing rates over rolling windows.

### Phase 3 — Advanced pattern detection

| Code | Insight | Complexity | Value |
|---|---|---|---|
| INS-B02 | Reasoning loop detection | High | High |
| INS-B03 | Hallucination proxy detection | High | Very High |
| INS-B04 | Plan drift / excessive replanning | Medium | Medium |
| INS-E03 | Unused tool results | High | Medium |
| INS-O02 | Time-of-day patterns | Medium | Medium |

**Estimated effort:** 5-10 days. Requires sequence analysis, multi-signal composite scoring, and optional `response_preview` content analysis (depends on loopCore flag being enabled).

---

## 8. Configuration & Tuning

### 8.1 Global thresholds (configurable per tenant)

```python
INSIGHT_THRESHOLDS = {
    "INS-C01": {"min_tokens_in": 4000, "max_ratio": 15},
    "INS-C03": {"hourly_spike_multiplier": 2.0, "task_spike_multiplier": 5.0},
    "INS-C04": {"monthly_budget_usd": None},  # None = disabled until user sets budget
    "INS-B01": {"min_empty_tasks_pct": 0.5, "min_tasks_sample": 5},
    "INS-B02": {"min_repeat_count": 3, "min_sequence_length": 2},
    "INS-B03": {"composite_threshold": 0.7},
    "INS-B04": {"max_revisions": 2},
    "INS-P01": {"p95_absolute_ms": 10000, "p50_increase_pct": 0.5},
    "INS-P04": {"stale_task_multiplier": 3.0},
    "INS-R01": {"min_failed_action_pct": 0.3, "min_tasks_sample": 10},
    "INS-R02": {"max_retries_per_task": 3.0},
    "INS-R03": {"dominant_error_pct": 0.6},
    "INS-E01": {"token_similarity_pct": 0.1},
    "INS-E02": {"max_overall_ratio": 20},
}
```

### 8.2 Dedup and cooldown

An insight with the same `(code, agent_id, call_name or task_type)` tuple is deduplicated within a cooldown window (default: 6 hours). Instead of creating a new insight, the existing one's `occurrences` counter increments and `last_detected_at` updates.

### 8.3 User dismiss lifecycle

Users can dismiss insights:
- **Dismiss** — hides the insight from the panel. If the same pattern recurs after the cooldown, a new insight is created.
- **Dismiss permanently** — hides and adds the pattern to a suppression list. Never re-detected.
- **Mark resolved** — user confirms they fixed the issue. Insight moves to resolved state. If the pattern recurs, a new insight is created with a note: "Previously resolved on [date], recurred."

---

## 9. Competitive Positioning

| Capability | LangSmith | Langfuse | HiveBoard + HiveMind |
|---|---|---|---|
| LLM call tracing | ✅ | ✅ | ✅ |
| Cost tracking | ✅ | ✅ | ✅ |
| Prompt bloat detection | ❌ | ❌ | ✅ |
| Model downgrade recommendations | ❌ | ❌ | ✅ |
| Hallucination signal detection | ❌ | ❌ | ✅ |
| Silent failure detection | ❌ | ❌ | ✅ |
| Reasoning loop detection | ❌ | ❌ | ✅ |
| Proactive cost optimization | ❌ | ❌ | ✅ |
| Agent-level workflow insights | ❌ | ❌ | ✅ |
| Actionable recommendations | ❌ | ❌ | ✅ |

The key differentiator: competitors show you data. HiveBoard tells you what to do about it.

---

## 10. Open Questions

1. **Should insights be computed in-process or as a separate worker?** In-process is simpler but adds load to the API server. A separate worker can run on its own schedule without affecting dashboard latency. For Phase 1 (simple aggregations), in-process is fine. For Phase 3 (sequence analysis), a separate worker is likely necessary.

2. **Should insight history be retained?** Resolved/dismissed insights are useful for tracking improvement over time ("we used to have a 28% silent failure rate, now it's 3%"). But they add to storage. Suggest retaining insight records for the plan retention window, same as events.

3. **LLM-powered analysis for Phase 3+?** Some insights (hallucination detection, unused tool results) would benefit from having an LLM analyze the event patterns and `response_preview` content. This creates a recursive situation — using LLM calls to analyze LLM call observability data. Cost would be minimal (Haiku-level analysis on small windows), but it's a meaningful architectural decision. Worth exploring as a Phase 4 concept.

4. **User-defined custom insights?** Power users may want to define their own detection rules: "flag any task where crm_search takes >2s." This is essentially a query-based alert on event patterns. Could be exposed as a "custom insight rule" builder in the UI.

# HiveBoard Insights Engine — Product Spec (v2)

**Author:** Juan
**Date:** February 13, 2026
**Status:** v2 — incorporates Team 1 and Team 2 review feedback
**Codename:** HiveMind

---

## Changelog from v1

| Area | v1 | v2 |
|---|---|---|
| Architecture | Open question on in-process vs. worker | Resolved: in-process, inside `JsonStorageBackend`, using `_insights_loop` pattern |
| Analyzer design | Undefined | New Section 2: base class, runner, event index |
| Insight storage | Undefined | New Section 3: `_tables["insights"]` with retention |
| Pruning tension | Not addressed | New Section 4: sequencing with retention cycle |
| Data availability | Assumed all data present | Each insight now tagged `✅ data available` or `⛔ blocked on loopCore` |
| INS-P04 | Phase 1 | Moved to Phase 2 (cross-event correlation, not simple aggregation) |
| INS-B03 | Phase 3 | Moved to Research track (aspirational, needs real-world tuning data) |
| INS-C02 | Phase 2 | Split: core detection Phase 2, `response_preview` enhancement blocked on loopCore |
| LLM-powered analysis | Open question | Closed: deferred indefinitely (trust/recursion concern) |
| Dashboard UI | 4 surfaces in one spec | Scoped to Insights tab only for Phase 1; inline indicators, notification bell, agent detail tab deferred to separate frontend spec |
| Performance | Not addressed | New: pre-indexed event lookup via `_events_by_type` dict |
| Implementation | "Build all 10 Phase 1 analyzers" | Changed: build framework + 1 analyzer (INS-C03) end-to-end first, then expand |

---

## 1. Vision

Traditional observability answers "what happened." HiveBoard already does this well — dashboards, timelines, cost breakdowns, activity streams. But AI agents fail in ways that traditional software doesn't. They hallucinate. They get stuck in loops. They waste money re-asking questions. They silently degrade. They look busy while producing nothing.

**The Insights Engine watches the agents and tells you what they're doing wrong and how to fix it.** It analyzes the event stream, detects patterns that indicate waste, failure, or degradation, and delivers actionable recommendations — both as a passive dashboard panel and as active alert notifications.

This is the feature that separates HiveBoard from generic LLM observability tools. LangSmith and Langfuse show you traces. HiveBoard tells you "your lead-qualifier agent is spending $14/day on reasoning calls that could run on Haiku for $1.40, and it's been hallucinating CRM record IDs at a 12% rate since Tuesday."

The key differentiator: competitors show you data. HiveBoard tells you what to do about it.

---

## 2. Analyzer Architecture

### 2.1 Constraint: In-Process Execution

`JsonStorageBackend` holds events in-memory in `self._tables["events"]`, accessed via async locks. A separate worker process cannot read this table — it would need its own file read, conflicting with the in-process lock model.

**Analyzers must run in-process**, in the same `asyncio` loop as the API server, following the same pattern as `_prune_loop`. This only becomes a worker-vs-process choice after migrating to a database backend.

The design should be structured so that extracting analyzers to a separate worker later requires changing only the data access layer (swap `self._tables` access for API/DB queries), not the analyzer logic itself.

### 2.2 Event Pre-Indexing

Running 10 analyzers that each scan the full events list is 10 linear passes on top of the dashboard polling that already does multiple passes every 5 seconds.

**Solution:** Pre-index events by type during insertion. Maintain a secondary index `_events_by_type: dict[str, list]` that is updated on every `insert_events` call and rebuilt after retention pruning. Analyzers query the index instead of scanning the full list.

```python
# On JsonStorageBackend:

def _rebuild_event_index(self):
    """Rebuild the type-based event index from the events table."""
    self._events_by_type = {}
    for event in self._tables.get("events", []):
        event_type = event.get("event_type", "unknown")
        payload = event.get("payload") or {}
        kind = payload.get("kind")
        
        # Index by event_type
        self._events_by_type.setdefault(event_type, []).append(event)
        
        # Also index custom events by payload.kind for fast llm_call/queue_snapshot lookup
        if event_type == "custom" and kind:
            key = f"custom:{kind}"
            self._events_by_type.setdefault(key, []).append(event)

# Called:
# - Once at startup after loading events.json
# - After every insert_events batch
# - After every retention prune cycle
```

This turns 10 full scans into 10 targeted lookups. An analyzer that only cares about `llm_call` events reads `self._events_by_type["custom:llm_call"]` instead of filtering 100K events.

### 2.3 Analyzer Base Class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

@dataclass
class Insight:
    insight_id: str
    code: str              # "INS-C01", "INS-R02", etc.
    category: str          # "cost", "behavior", "performance", "reliability", "efficiency", "capacity"
    title: str
    severity: str          # "critical", "high", "medium", "low"
    agent_id: Optional[str]
    task_type: Optional[str]
    description: str
    recommendation: str
    evidence: dict         # structured, per-insight-type
    impact: dict           # standardized: estimated_savings, affected_calls, confidence
    detected_at: str       # ISO timestamp

class BaseAnalyzer(ABC):
    """Base class for all insight analyzers."""
    
    code: str              # e.g. "INS-C03"
    category: str          # e.g. "cost"
    run_interval_seconds: int = 300  # how often this analyzer runs (default: 5 min)
    
    @abstractmethod
    async def analyze(self, events_by_type: dict, config: dict) -> list[Insight]:
        """
        Analyze events and return zero or more insights.
        
        Args:
            events_by_type: Pre-indexed events dict from _events_by_type.
                            Access patterns:
                              events_by_type["custom:llm_call"] → list of LLM events
                              events_by_type["action_failed"] → list of failed actions
                              events_by_type["task_completed"] → list of completed tasks
            config: Thresholds and settings for this analyzer from INSIGHT_THRESHOLDS.
        
        Returns:
            List of Insight records. Empty list = nothing detected.
        """
        pass
```

**Key design decisions:**

- Analyzers receive `events_by_type` (the pre-indexed dict), never raw `self._tables["events"]`. This enforces the indexed access pattern and makes future extraction to a worker trivial — swap the dict for API results.
- Analyzers are stateless. All state lives in the insight store. An analyzer doesn't know about previous runs; dedup is handled by the runner.
- Each analyzer has its own `run_interval_seconds`. Cost spike (INS-C03) might run every 2 minutes; time-of-day patterns (INS-O02) might run every hour.

### 2.4 Insights Runner

```python
class InsightsRunner:
    """Runs registered analyzers on schedule and manages the insight lifecycle."""
    
    def __init__(self, storage: JsonStorageBackend):
        self.storage = storage
        self.analyzers: list[BaseAnalyzer] = []
        self._last_run: dict[str, datetime] = {}
    
    def register(self, analyzer: BaseAnalyzer):
        self.analyzers.append(analyzer)
    
    async def run_cycle(self):
        """Run all analyzers that are due. Called by _insights_loop."""
        now = datetime.utcnow()
        
        async with self.storage._locks["events"]:
            events_by_type = self.storage._events_by_type
        
        for analyzer in self.analyzers:
            last = self._last_run.get(analyzer.code)
            if last and (now - last).total_seconds() < analyzer.run_interval_seconds:
                continue
            
            try:
                config = INSIGHT_THRESHOLDS.get(analyzer.code, {})
                new_insights = await analyzer.analyze(events_by_type, config)
                
                for insight in new_insights:
                    await self._store_or_dedup(insight)
                
                self._last_run[analyzer.code] = now
            except Exception as e:
                logger.error(f"Analyzer {analyzer.code} failed: {e}")
    
    async def _store_or_dedup(self, insight: Insight):
        """Dedup against existing insights, or store as new."""
        # Dedup key: (code, agent_id, evidence-specific key like call_name or task_type)
        # If match found within cooldown window: increment occurrences, update last_detected_at
        # If no match: insert new insight
        ...
```

### 2.5 Background Loop

Follows the same pattern as `_prune_loop`:

```python
# In app.py startup, after storage backend and retention loop:

runner = InsightsRunner(storage)
runner.register(CostSpikeAnalyzer())      # INS-C03 — start with just this one
# runner.register(PromptBloatAnalyzer())  # INS-C01 — add after framework is validated
# ...

async def _insights_loop():
    while True:
        await asyncio.sleep(60)  # check every minute; individual analyzers have their own intervals
        try:
            await runner.run_cycle()
        except Exception as e:
            logger.error(f"Insights loop failed: {e}")

asyncio.create_task(_insights_loop())
```

---

## 3. Insight Storage

### 3.1 Location

A new table: `self._tables["insights"]` backed by `insights.json`, with its own lock `self._locks["insights"]`.

Insights are NOT stored in the events table. They have a different lifecycle (user dismiss/resolve), different retention rules, and different query patterns.

### 3.2 Retention

Insights are subject to retention from day one — learned from the events table growth problem.

| Status | Retention |
|---|---|
| `active` | Until dismissed, resolved, or plan retention limit |
| `dismissed` | 7 days (then deleted) |
| `resolved` | 30 days (then deleted) — retained longer for trend tracking |
| `permanently_dismissed` | Suppression rule kept indefinitely; the insight record itself is deleted after 7 days |

Maximum insight records: capped at 500 per tenant. If exceeded, oldest resolved/dismissed are pruned first. This is a hard safety net — in practice, the dedup and cooldown mechanisms keep the count well below this.

### 3.3 Suppression List

When a user dismisses an insight permanently, a suppression rule is stored:

```json
{
    "code": "INS-E04",
    "agent_id": "ag_6ce5uncd",
    "match_key": "cleanup_temp",
    "suppressed_at": "2026-02-13T16:00:00Z"
}
```

The runner checks suppression rules before storing new insights. Suppression list is stored in `insights.json` alongside the insights, under a `suppressions` key.

---

## 4. Pruning / Analysis Sequencing

### 4.1 The Tension

The retention system prunes events that analyzers need:

| Pruned data | Insights affected |
|---|---|
| Heartbeats at 24h | INS-P04 (partial stuckness), INS-O03 (utilization rate) |
| `action_started` at 48h | INS-B02 (reasoning loops), INS-E03 (unused tool results) |
| Free plan 7d limit | INS-O02 (time-of-day patterns needs multi-day data), INS-C04 (burn rate trends) |

### 4.2 Resolution: Analyze Before Prune

The `_insights_loop` and `_prune_loop` must be sequenced so that analyzers always see the full event set before pruning removes old data. Two options:

**Option A — Single combined loop (recommended):**

```python
async def _maintenance_loop():
    while True:
        await asyncio.sleep(300)
        await runner.run_cycle()         # Analyze FIRST — sees all events
        await storage.run_retention()    # Prune SECOND — removes old events
```

This guarantees analyzers see everything. Simple, no race conditions.

**Option B — Separate loops with ordering guarantee:** Less clean, harder to reason about timing. Not recommended.

### 4.3 Future: Insight Snapshots Survive Pruning

When compaction is implemented (deferred from the retention spec), the analysis engine can contribute to rollup records. For example, before heartbeats are pruned, the utilization analyzer computes and stores the utilization rate as a metric snapshot that survives beyond raw event retention. This is a Phase 3+ concern.

---

## 5. Insight Catalog

### Data Availability Key

Each insight is tagged with its data readiness:

- ✅ **Data available** — all required fields exist in current event stream
- ⚠️ **Partial** — core detection works, but enhanced features need data not yet available
- ⛔ **Blocked** — cannot implement until loopCore sends required fields

Currently blocked fields: `prompt_preview` and `response_preview` are not being sent by loopCore. Any insight that depends on inspecting prompt/response content is blocked or degraded until this is enabled.

---

### 5.1 Cost & Budget

#### INS-C01: Prompt Bloat Detection ✅

**Signal:** `tokens_in` is consistently high for a call name that produces short outputs.

**Detection:** For each unique `call_name`, compute rolling average `tokens_in` and `tokens_out` over the last N calls. Flag when ratio exceeds threshold (default: 15:1) AND `tokens_in` exceeds minimum (default: 4K).

**Data required:** `llm_call` events with `tokens_in`, `tokens_out`, `call_name`. ✅ All available.

**Recommendation:** "**phase1_reasoning** averages 9,200 input tokens but only 340 output tokens (ratio: 27:1). The prompt likely contains context the model isn't using. Consider trimming unused context, summarizing long documents before injection, or using a context-compaction step."

**Severity:** Medium. **Phase:** 1.

---

#### INS-C02: Model Downgrade Opportunity ⚠️

**Signal:** A call consistently uses an expensive model but the task pattern suggests a cheaper model would work.

**Detection:** For each `(call_name, model)` pair, check: success rate >95%, avg `tokens_out` <500, no downstream failures. If met and a cheaper model exists, flag.

**Model cost hierarchy (built-in):**

| Tier | Models (examples) | Relative cost |
|---|---|---|
| Premium | claude-opus-*, gpt-4o | 1.0x |
| Standard | claude-sonnet-*, gpt-4o-mini | 0.3x |
| Economy | claude-haiku-*, gpt-3.5-turbo | 0.05x |

**Data required:** `llm_call` events with `model`, `tokens_out`, `cost`, `call_name`. ✅ Core detection available. ⛔ Structure detection (response starts with `{`) needs `response_preview` — blocked on loopCore.

**Recommendation:** "**heartbeat_summary** uses claude-sonnet-4-5 at $0.034/call with 100% success rate and avg 77 output tokens. This pattern is suitable for claude-haiku (~$0.003/call). Estimated savings: **$0.93/day ($28/month)**."

**Severity:** Low. **Phase:** 2 (needs cross-referencing success rates with downstream failures).

---

#### INS-C03: Cost Spike Detection ✅

**Signal:** Hourly cost exceeds a threshold relative to the rolling baseline.

**Detection:** Compare current-hour cost to rolling 24h average. Flag when current > 2x average. Also flag single tasks costing >5x median for their type.

**Data required:** `llm_call` events with `cost`, `timestamp`. ✅ All available.

**Recommendation (hourly):** "Cost spike detected: **$4.20 in the last hour** vs. $1.80 rolling average. Top contributor: ag_6ce5uncd spent $3.10 on 45 phase1_reasoning calls (normally 20/hour)."

**Recommendation (per-task):** "Task **task_lead-9821** cost $0.48 — 6x the median of $0.08 for lead_processing tasks. 4 retries drove the excess."

**Severity:** High. **Phase:** 1. **This is the first analyzer to implement** — validates the full framework end-to-end.

---

#### INS-C04: Budget Burn Rate Projection ✅

**Signal:** At current spend rate, projected monthly cost exceeds a threshold.

**Detection:** Extrapolate current daily cost to monthly. Flag when projected monthly > configured threshold.

**Data required:** `llm_call` events with `cost`, aggregated daily. ✅ Available. Note: benefits from multi-day trend data; on FREE plan (7d retention), only 7 days of history available for trend comparison.

**Recommendation:** "At the current rate of **$18.40/day**, projected monthly cost is **$552**. This is a 3.2x increase from last week's average ($5.70/day). Primary driver: main agent's phase1_reasoning call volume doubled on Tuesday."

**Severity:** Medium. **Phase:** 1.

---

### 5.2 Behavioral & Quality

#### INS-B01: Empty/No-Value Loop Detection ✅

**Signal:** Agent repeatedly completes tasks of the same type but produces empty or minimal output.

**Detection:** For each `(agent_id, task_type)` pair, check last N completed tasks. If >50% have no output payload and the task keeps recurring, flag.

**Data required:** `task_completed` events with payload inspection. ✅ Available.

**Recommendation:** "**ag_6ce5uncd** completed 14 lead_processing tasks in the last hour with no output payload. This looks like a no-op loop — the agent is doing work but producing no value. Check whether the input data source is returning empty results."

**Severity:** High. **Phase:** 2 (needs output payload inspection heuristic — not pure aggregation).

---

#### INS-B02: Reasoning Loop / Repetitive Action Cycle ✅

**Signal:** Within a single task, the same sequence of actions repeats without plan progress.

**Detection:** Extract `action_name` sequence within a task's timeline. Detect repeating subsequences (length ≥2) occurring ≥3 times. Cross-reference with `plan_step` events.

**Data required:** `action_started`/`action_completed` events within a task, `plan_step` events. ✅ Available. Note: `action_started` pruned at 48h, so detection only works on recent tasks.

**Recommendation:** "Task **task_lead-4821** shows a repeating pattern: `fetch_data → analyze → fetch_data → analyze` repeated 5 times without advancing past plan step 2."

**Severity:** High. **Phase:** 3 (sequence pattern detection is non-trivial).

---

#### INS-B03: Hallucination Proxy Detection ⚠️

**Signal:** Agent generates outputs referencing non-existent entities, causing downstream failures.

**Detection — proxy signals:**

| Proxy | Detection method | Confidence |
|---|---|---|
| Hallucinated entity IDs | `action_failed` with "not found"/"invalid ID" error, where parameters came from preceding LLM output | High |
| Contradictory outputs | Two LLM calls in same task produce conflicting structured outputs | Medium |
| Tool parameter mismatch | Agent calls tool with wrong argument types/formats | Medium |
| Plan step mismatch | Plan references action not in agent's tracked repertoire | Low |

**Data required:** `action_failed` with `exception_message` ✅, `llm_call` with `response_preview` ⛔ blocked on loopCore for contradiction detection.

**Severity:** High. **Phase:** Research track.

**Note (from team review):** Each proxy signal individually requires significant heuristic engineering. The composite scoring needs real-world tuning data we don't have yet. This is moved from Phase 3 to a research/exploration track — not a known-scope implementation task. The "hallucinated entity IDs" proxy (correlating "not found" errors with preceding LLM calls) is the most tractable and could be extracted as a standalone Phase 2 insight if the heuristic proves reliable.

---

#### INS-B04: Plan Drift / Excessive Replanning ✅

**Signal:** Agent keeps revising its plan mid-execution.

**Detection:** Count `plan_revision` increments within a single task. Flag when revisions exceed threshold (default: 2).

**Data required:** `plan_created` and `plan_step` events with `plan_revision` field. ✅ Available.

**Recommendation:** "**main** replanned 4 times during task_lead-4821. Original plan had 3 steps; final plan had 7."

**Severity:** Medium. **Phase:** 3.

---

#### INS-B05: Escalation Rate Trend ✅

**Signal:** Escalation rate for an agent or task type is increasing over time.

**Detection:** Compare escalation rate in current window vs. previous equivalent window. Flag when rate increases >50%.

**Data required:** `escalated` events, `task_completed`/`task_failed` for rate denominators. ✅ Available.

**Recommendation:** "Escalation rate for **lead_processing** jumped from 8% to 22% in the last 6 hours."

**Severity:** Medium-High. **Phase:** 2.

---

### 5.3 Performance & Latency

#### INS-P01: Slow LLM Call Trend ✅

**Signal:** A specific LLM call's latency is trending upward or exceeds an absolute threshold.

**Detection:** For each `call_name`, compute rolling p50 and p95 `duration_ms`. Flag when p95 > 10s or p50 increased >50% vs. 24h baseline.

**Data required:** `llm_call` events with `duration_ms`, `call_name`. ✅ Available.

**Recommendation:** "**phase1_reasoning** p95 latency is 8.2s, up from 5.1s 24 hours ago (61% increase). Average `tokens_in` also increased — growing input size is likely driving the latency increase."

**Severity:** Medium. **Phase:** 1.

---

#### INS-P02: Tool Latency Outliers ✅

**Signal:** An external tool call's latency spikes beyond normal range.

**Detection:** For each `action_name`, compute rolling p50 and p95 `duration_ms`. Flag when p95 > 10x p50.

**Data required:** `action_completed` events with `duration_ms`, `action_name`. ✅ Available.

**Recommendation:** "**workspace_read** p95 latency jumped from 25ms to 1.8s in the last hour. 12 of 340 calls exceeded 500ms."

**Severity:** Medium. **Phase:** 1.

---

#### INS-P03: Queue Aging / Throughput Ceiling ✅

**Signal:** Queue depth growing, agent can't keep up with inbound work.

**Detection:** From `queue_snapshot` events: positive trend in `queue_depth` AND `oldest_age` exceeds threshold (default: 5 min).

**Data required:** `queue_snapshot` events. ✅ Available. Note: pruned at 24h, so trend detection limited to 24h window.

**Recommendation:** "**ag_6ce5uncd**'s queue depth has grown from 2 to 18 in the last 3 hours. Oldest item is 45 minutes old."

**Severity:** High. **Phase:** 2.

---

#### INS-P04: Partial Stuckness ✅

**Signal:** Agent is alive (heartbeating) but hasn't completed a task in abnormally long time.

**Detection:** Agent's latest heartbeat is recent, but time since last `task_completed` exceeds 3x rolling average task duration.

**Data required:** `heartbeat` events (latest), `task_completed` events (latest + rolling avg duration). ✅ Available. Note: requires cross-event correlation between heartbeats and task completions.

**Recommendation:** "**main** has been processing task_lead-9821 for 12 minutes. Average task duration is 27 seconds."

**Severity:** High. **Phase:** 2 (moved from Phase 1 — cross-event correlation, not simple aggregation).

---

### 5.4 Reliability & Errors

#### INS-R01: Silent Failure Pattern ✅

**Signal:** Completed tasks contain swallowed internal failures.

**Detection:** For completed tasks, count `action_failed` events within each. Flag when >30% of recent completed tasks contain at least one failed action.

**Data required:** `task_completed` cross-referenced with `action_failed` by `task_id`. ✅ Available.

**Recommendation:** "Last 20 tasks for **main** completed successfully, but 35% contained at least one failed action. Most common: `crm_update` failing with 'permission denied'."

**Severity:** High. **Phase:** 2.

---

#### INS-R02: Retry Storm Detection ✅

**Signal:** Retry rate per task spikes, indicating a systemic issue.

**Detection:** Compute average `retry_started` events per task. Flag when >3 retries/task or doubles vs. baseline.

**Data required:** `retry_started` events, `task_started` for rate denominator. ✅ Available.

**Recommendation:** "**lead_processing** retry rate jumped to 4.2 retries/task (was 0.8). Most retried action: `crm_search`. Most common error: 'API rate limit exceeded'."

**Severity:** High. **Phase:** 1.

---

#### INS-R03: Error Category Clustering ✅

**Signal:** A single error type suddenly dominates all errors.

**Detection:** Group `action_failed` by `exception_type`. Flag when one type accounts for >60% of all errors.

**Data required:** `action_failed` events with `exception_type`. ✅ Available.

**Recommendation:** "78% of errors in the last hour are **ConnectionTimeout** (23 of 29 failures). Concentrated on `crm_search` and `crm_update`. Likely cause: external CRM API degradation."

**Severity:** High. **Phase:** 1.

---

#### INS-R04: Recovery Rate Degradation ✅

**Signal:** Fewer tasks that encounter errors are completing successfully.

**Detection:** For tasks containing `action_failed` or `retry_started`, compute percentage reaching `task_completed`. Track over time, flag when drops >20%.

**Data required:** `task_completed`, `task_failed`, `action_failed`, `retry_started`, correlated by `task_id`. ✅ Available.

**Recommendation:** "Recovery rate for **main** dropped from 85% to 62% in the last 12 hours."

**Severity:** Medium-High. **Phase:** 2.

---

### 5.5 Efficiency & Waste

#### INS-E01: Redundant LLM Calls ✅

**Signal:** Same call fires multiple times in a single task with near-identical input tokens.

**Detection:** Within a task, group `llm_call` by `call_name`. For groups >1, compare `tokens_in` values. Flag when within 10% of each other.

**Data required:** `llm_call` events with `call_name`, `tokens_in`, grouped by `task_id`. ✅ Available.

**Recommendation:** "**phase1_reasoning** called 3 times in task_lead-4821 with near-identical input tokens (9,587 / 9,601 / 9,543). Cost of redundant calls: $0.068."

**Severity:** Medium. **Phase:** 2.

---

#### INS-E02: Token Waste Ratio ✅

**Signal:** Consistently massive context for trivial outputs.

**Detection:** Compute `tokens_in / tokens_out` ratio per agent. Flag when >20:1 consistently. Benchmark: 3:1 to 8:1 is typical for productive calls.

**Data required:** `llm_call` events with `tokens_in`, `tokens_out`. ✅ Available.

**Recommendation:** "**ag_6ce5uncd** has an overall token ratio of 24:1 (48K in, 2K out over 18 calls)."

**Severity:** Medium. **Phase:** 1.

---

#### INS-E03: Unused Tool Results ⚠️

**Signal:** Agent calls a tool but the result doesn't influence subsequent actions.

**Detection:** Identify `action_completed` → `llm_call` sequences where `tokens_in` doesn't increase (tool output not injected into context). Weak signal, best combined with patterns.

**Data required:** `action_completed`, subsequent `llm_call` with `tokens_in`. ✅ Core detection. ⛔ `prompt_preview` for confirmation — blocked on loopCore.

**Severity:** Medium. **Phase:** 3.

---

#### INS-E04: Over-Instrumentation Noise ✅

**Signal:** Tracked action always succeeds instantly with no variation.

**Detection:** For each `action_name`: 100% success, avg `duration_ms` <10ms, no payload variation, >100 occurrences. Note: relies on `action_completed` events only (safe — `action_started` is pruned at 48h but not needed here).

**Data required:** `action_completed` events. ✅ Available.

**Recommendation:** "**cleanup_temp** completed 847 times with 100% success rate and avg 2ms. Consider removing `@agent.track()` from this function."

**Severity:** Low. **Phase:** 1.

---

### 5.6 Operational & Capacity

#### INS-O01: Throughput Ceiling Detection ✅

**Signal:** Task completion rate plateaus while queue depth grows.

**Detection:** Tasks_completed/hour flat (±10%) for >2 hours while queue depth trends upward.

**Data required:** `task_completed` (rate), `queue_snapshot` (depth trend). ✅ Available.

**Recommendation:** "**ag_6ce5uncd** processing ~120 tasks/hour (stable for 4 hours) but queue depth has grown from 2 to 34."

**Severity:** High. **Phase:** 2.

---

#### INS-O02: Time-of-Day Patterns ✅

**Signal:** Error rates, costs, or latency spike at predictable times.

**Detection:** Aggregate metrics by hour-of-day over last 7 days. Flag hours consistently >2x daily average.

**Data required:** All event types with `timestamp`. ✅ Available. Note: needs 7 days of data; works on PRO/ENTERPRISE, limited on FREE (7d retention is the boundary).

**Recommendation:** "**Error rate** spikes between 2:00-3:00 AM UTC daily (avg 28% vs. 6% baseline). Correlates with CRM batch job window."

**Severity:** Medium. **Phase:** 3.

---

#### INS-O03: Agent Utilization Rate ✅

**Signal:** Agent spends most of its time idle relative to its cost.

**Detection:** Ratio of time-in-task to total wall-clock time. Flag when <20% utilization with high cost/task.

**Data required:** `task_completed` with `duration_ms`, `heartbeat` for uptime. ✅ Available. Note: heartbeat pruning at 24h limits uptime window.

**Recommendation:** "**main** has 12% utilization — active for 43 minutes out of 6 hours. Cost per active minute: $0.12."

**Severity:** Low. **Phase:** 2.

---

## 6. Insight Record Schema

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
| `code` | Machine identifier (INS-C01, INS-B03, etc.) — programmatic handling, alert rule matching |
| `severity` | `critical`, `high`, `medium`, `low` — drives alert routing and dashboard ordering |
| `evidence` | Structured data backing the insight — different per insight type, enables auditability |
| `impact` | Estimated cost/time/reliability impact — standardized for prioritization and sorting |
| `occurrences` | Dedup count — same pattern detected N times (bumps `last_detected_at`) |
| `status` | `active`, `dismissed`, `permanently_dismissed`, `resolved` — user lifecycle |

---

## 7. Delivery: Passive (Dashboard)

### 7.1 Phase 1 Scope: Insights Tab Only

The dashboard gets one new top-level tab: **Insights** (alongside Dashboard, Costs, Pipeline).

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

### 7.2 Deferred UI (separate frontend spec needed)

These are valuable but each is a significant frontend integration:

| Surface | What it would show | Why deferred |
|---|---|---|
| Inline indicators (Cost Explorer rows, timeline nodes, stream cards) | Small icons/badges on elements that have active insights | Touches 4+ render functions; needs per-element insight lookup |
| Agent Detail — Insights tab | All insights for a specific agent | Needs agent detail panel redesign |
| Notification bell (top bar) | Unread count, dropdown of recent alert-triggered insights | New state management, new UI component |
| Agent card badges | "3 insights" with severity color | Requires insight count query per agent on every dashboard refresh |

Each of these surfaces will get its own spec when we're ready for Phase 2 UI work.

---

## 8. Delivery: Active (Alerts & Notifications)

### 8.1 Alert Rule Integration

Insights integrate with the existing alert system. Each insight code can be an alert trigger:

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

### 8.2 Default Alert Rules (ship enabled, user can disable)

| Insight codes | Default action | Cooldown |
|---|---|---|
| INS-C03 (cost spike) | Dashboard notification | 1 hour |
| INS-B01 (empty loop) | Dashboard notification | 1 hour |
| INS-R01 (silent failures) | Dashboard notification | 1 hour |
| INS-R02 (retry storm) | Dashboard notification | 30 min |
| INS-P03 (queue aging) | Dashboard notification | 2 hours |
| INS-P04 (partial stuckness) | Dashboard notification | 15 min |

### 8.3 Webhook / Slack Push

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

## 9. API Endpoint

```
GET /v1/insights
```

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_id` | string | null (all) | Filter by agent |
| `category` | string | null (all) | Filter: `cost`, `behavior`, `performance`, `reliability`, `efficiency`, `capacity` |
| `severity` | string | null (all) | Minimum severity: `low`, `medium`, `high`, `critical` |
| `status` | string | `"active"` | Filter: `active`, `dismissed`, `resolved`, `all` |
| `limit` | integer | 50 | Max results |

**Response:**

```json
{
    "data": [ /* insight records per schema in Section 6 */ ],
    "summary": {
        "total_active": 12,
        "by_severity": { "critical": 0, "high": 3, "medium": 6, "low": 3 },
        "by_category": { "cost": 4, "reliability": 3, "performance": 2, "efficiency": 2, "behavior": 1 }
    }
}
```

**Mutation endpoints:**

```
POST /v1/insights/{insight_id}/dismiss
POST /v1/insights/{insight_id}/dismiss-permanently
POST /v1/insights/{insight_id}/resolve
```

The analysis engine has value even without the dashboard UI — it powers the API, alerts, and webhooks immediately.

---

## 10. Implementation Phases (Revised)

### Phase 0 — Framework + First Analyzer (2-3 days)

Build the infrastructure and validate with a single analyzer end-to-end:

1. Implement `BaseAnalyzer`, `InsightsRunner`, `Insight` dataclass
2. Add `_events_by_type` pre-index to `JsonStorageBackend`
3. Add `_tables["insights"]` with its own lock and retention
4. Add `_maintenance_loop` (insights → prune sequencing)
5. Implement **INS-C03 (cost spike detection)** — highest value, simplest detection
6. Add `GET /v1/insights` API endpoint
7. Wire one alert rule (dashboard notification on cost spike)
8. **Validate the full loop:** events → analyzer → insight store → API → alert

Do not proceed to Phase 1 until this loop works end-to-end in production.

### Phase 1 — Core Analyzers (3-4 days)

Add remaining simple-aggregation analyzers, one at a time:

| Code | Insight | Data status |
|---|---|---|
| INS-C01 | Prompt bloat detection | ✅ |
| INS-C04 | Budget burn rate projection | ✅ |
| INS-P01 | Slow LLM call trend | ✅ |
| INS-P02 | Tool latency outliers | ✅ |
| INS-R02 | Retry storm detection | ✅ |
| INS-R03 | Error category clustering | ✅ |
| INS-E02 | Token waste ratio | ✅ |
| INS-E04 | Over-instrumentation noise | ✅ |

**All 8 are pure aggregation over pre-indexed events. No cross-event correlation.**

Add the Insights tab to the dashboard (one new tab, wireframe from Section 7.1).

### Phase 2 — Cross-Event Correlation (5-7 days)

| Code | Insight | Data status |
|---|---|---|
| INS-C02 | Model downgrade opportunity | ⚠️ core ✅, response_preview ⛔ |
| INS-B01 | Empty loop detection | ✅ |
| INS-B05 | Escalation rate trend | ✅ |
| INS-P03 | Queue aging / throughput ceiling | ✅ |
| INS-P04 | Partial stuckness | ✅ |
| INS-R01 | Silent failure pattern | ✅ |
| INS-R04 | Recovery rate degradation | ✅ |
| INS-E01 | Redundant LLM calls | ✅ |
| INS-O01 | Throughput ceiling | ✅ |
| INS-O03 | Agent utilization rate | ✅ |

Dashboard: inline indicators, notification bell, agent detail tab (separate frontend spec).

### Phase 3 — Advanced Pattern Detection (5-10 days)

| Code | Insight | Data status |
|---|---|---|
| INS-B02 | Reasoning loop detection | ✅ (action_started pruned at 48h — uses action_completed sequences) |
| INS-B04 | Plan drift / excessive replanning | ✅ |
| INS-E03 | Unused tool results | ⚠️ core ✅, prompt_preview ⛔ |
| INS-O02 | Time-of-day patterns | ✅ (needs 7d data — limited on FREE plan) |

### Research Track — Not Scoped for Implementation

| Code | Insight | Blocker |
|---|---|---|
| INS-B03 | Hallucination proxy detection | Needs real-world tuning data for composite scoring; each proxy signal needs independent heuristic validation before combining |

---

## 11. Configuration & Tuning

### 11.1 Thresholds (configurable per tenant)

```python
INSIGHT_THRESHOLDS = {
    "INS-C01": {"min_tokens_in": 4000, "max_ratio": 15},
    "INS-C03": {"hourly_spike_multiplier": 2.0, "task_spike_multiplier": 5.0},
    "INS-C04": {"monthly_budget_usd": None},  # None = disabled until user sets budget
    "INS-B01": {"min_empty_tasks_pct": 0.5, "min_tasks_sample": 5},
    "INS-B02": {"min_repeat_count": 3, "min_sequence_length": 2},
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

### 11.2 Dedup and cooldown

An insight with the same `(code, agent_id, call_name or task_type)` tuple is deduplicated within a cooldown window (default: 6 hours). Instead of creating a new insight, the existing one's `occurrences` counter increments and `last_detected_at` updates.

### 11.3 User dismiss lifecycle

- **Dismiss** — hides from panel. Recurrence after cooldown creates new insight.
- **Dismiss permanently** — adds to suppression list. Never re-detected.
- **Mark resolved** — moves to resolved state. Recurrence creates new insight with note: "Previously resolved on [date], recurred."

---

## 12. Competitive Positioning

| Capability | LangSmith | Langfuse | HiveBoard + HiveMind |
|---|---|---|---|
| LLM call tracing | ✅ | ✅ | ✅ |
| Cost tracking | ✅ | ✅ | ✅ |
| Prompt bloat detection | ❌ | ❌ | ✅ |
| Model downgrade recommendations | ❌ | ❌ | ✅ |
| Silent failure detection | ❌ | ❌ | ✅ |
| Reasoning loop detection | ❌ | ❌ | ✅ |
| Proactive cost optimization | ❌ | ❌ | ✅ |
| Agent-level workflow insights | ❌ | ❌ | ✅ |
| Actionable recommendations | ❌ | ❌ | ✅ |

---

## 13. Resolved Decisions

These were open questions in v1, now closed based on team feedback:

| Question | Decision | Rationale |
|---|---|---|
| In-process vs. separate worker? | In-process | `JsonStorageBackend` holds events in-memory; separate worker can't access `_tables`. Becomes a real choice after DB migration. |
| LLM-powered analysis? | Deferred indefinitely | Trust problem: if your observability tool hallucinates about your agent hallucinating, you have a credibility issue. Rule-based detection first; LLM only for patterns that can't be expressed as rules. |
| Insight history retention? | Yes, with caps | 500 max records per tenant. Dismissed: 7d retention. Resolved: 30d. Active: until plan limit or user action. |
| INS-B03 hallucination detection scope? | Research track | Aspirational, not spec. Each proxy signal needs independent heuristic validation. Move to research exploration, not implementation backlog. |
| Dashboard scope for Phase 1? | Insights tab only | Inline indicators (4 surfaces), notification bell, and agent detail tab are significant frontend work. Separate specs when ready. |
| Build all Phase 1 analyzers at once? | No — framework + INS-C03 first | Validate the full loop (events → analyzer → store → API → alert) with one analyzer before scaling to 8. |

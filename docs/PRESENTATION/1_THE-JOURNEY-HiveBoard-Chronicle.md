# THE JOURNEY

### How We Built HiveBoard — The Datadog for AI Agents — in 48 Hours

*A chronicle of building an AI agent observability platform from scratch using a multi-Claude development process*

---

> *"Nobody needs observability on demo day. Everyone needs it on day 30 when the agent silently stopped working and nobody noticed for 6 hours."*

---

## Prologue: The Pain That Started Everything

Before HiveBoard existed, its creator Juan was deep in building an **agentic ecosystem for business operations** — a multi-agent system where autonomous AI agents process sales leads, triage support tickets, manage workflows, and coordinate with each other. The kind of architecture where dozens of agents run continuously, making LLM calls, taking actions, and — inevitably — failing in ways nobody can see.

For two weeks, Juan deployed these agents into production. And for two weeks, the same nightmare played out:

- *Why didn't it send the email?* Is it still waiting? Did credentials fail?
- *Why did the post fail?* The LLM missed a mandatory field — silently.
- *Why did the DM never arrive?* No error. No log. Just... nothing.
- *How much is this costing me?* The meter was spinning at **$40/hour** and he had no idea where the money was going.

So he did what every agent developer eventually does — he duct-taped together his own observability. Print statements. Status files. A crude 3-tab dashboard. Not user-friendly, but packed with raw data. Survival tooling built in the trenches.

That duct tape — and those battle scars — would become HiveBoard's blueprint.

---

## Chapter 1: The Spark — Five Ideas on the Table

**Date:** ~February 10, 2025

It started with a blank canvas. Juan wanted to build something new in the agent space, born directly from his own agentic ecosystem. The process was methodical:

1. **Deep Discovery:** Juan worked with **Claude Code** (functioning as his developer team) to generate a comprehensive document capturing the full architecture, philosophy, pain points, and untapped potential of his agent platform.
2. **Ideation Session:** Armed with that document, Juan sat down with **Claude Chat** (functioning as co-project manager and strategic partner) and together they generated **five candidate product ideas**.

One of those five was an observability platform for AI agents. But Juan didn't pick that one. Not yet.

---

## Chapter 2: The False Start — FormsFlow

**Date:** February 11, 2025 (morning)

Juan chose **FormsFlow** — an intelligent inbound system powered by forms and managed by AI. The team moved fast:

- Specs created
- Mock UI/UX designed
- Implementation started immediately
- **By end of session: a running prototype**

Then Juan looked at it. *"Lame."*

Dropped. Back to the whiteboard. Sometimes the fastest way forward is knowing when to kill something.

---

## Chapter 3: The Revelation

**Date:** February 11, 2025 (evening)

That night, Juan went back to working on his agent platform. And the realization hit like a truck.

For weeks, his single biggest pain point had been **observability of his agents**. He'd already built tools for it. He was *living* the problem every single day. And observability was already sitting on that list of five candidates, waiting.

> *"WOW. That's it."*

This wasn't a market thesis. This wasn't speculation. This was a builder who'd spent two weeks in the trenches, duct-taping visibility into his own code, suddenly realizing he should build the tool he'd been desperately wishing existed.

And he had the data to prove it. By simply being able to *see* what his agents were sending to LLMs, Juan had cut operating costs from **$40/hour to $8/hour** — an 80% reduction. Here's how:

> **The $40→$8 Breakdown:** The root cause was **prompt bloat**. Without visibility into what each agent was actually sending to the LLM, prompts had accumulated redundant context, overly verbose system instructions, and repeated information across turns. Once Juan could *see* the actual prompts — their content, their token counts, their costs per call — the optimization was straightforward: trim the bloat, tighten the instructions, remove redundant context. Average prompt size dropped from ~18,000 tokens to ~5,000 tokens per task after removing duplicated system context — a ~70% token reduction that directly translated to the 80% cost drop. No model switch. No architecture change. Just visibility into what was already happening, followed by informed prompt optimization.

---

## Chapter 4: The Vision Crystallizes

Before writing a single line of spec, the core product insights locked into place:

### The Mental Model Shift

Existing tools think about agents the wrong way. LangSmith is locked to LangChain. Langfuse traces LLM calls but misses the workflow. Datadog traces HTTP spans but doesn't understand agent behavior. None of them model **agents as workers** — workers that get tasks, take actions, get stuck, need help, and recover.

HiveBoard would think differently. Not "what LLM call happened?" but **"What is this agent doing on this task right now, and is it healthy?"**

### The Invisible Failures Problem

The most dangerous failures in agent systems aren't the ones that throw errors. They're the ones that **never happen**. The email that was never sent. The task that was silently dropped. The request rotting in a queue while the agent reports "idle."

HiveBoard would see not just what agents *did*, but what they *didn't do*. The intent pipeline — queues, scheduled work, pending actions — would be first-class observable state.

### The Architecture: Everything Is an Event

One data primitive. One table. One truth. Every status change, action, error, heartbeat, and metric is an event. Agent status is computed at query time, not persisted. A heartbeat is just an event. "Stuck" means no heartbeat event in X minutes. No stale records. No cache invalidation. No state synchronization bugs.

### The Business Model: The Sentry Model

Open-source SDK (**HiveLoop**) drives adoption. Hosted dashboard (**HiveBoard**) is the business. Developers think of them as one product, but they're technically separate. The SDK being open-source removes friction; the dashboard delivers the value.

> *HiveLoop is what the developer touches. HiveBoard is what the developer sees.*

### The 3-Lines-of-Code Promise

```python
import hiveloop
hb = hiveloop.init(api_key="hb_xxx")
agent = hb.agent("lead-qualifier", type="sales")
```

From this alone, the agent appears on the dashboard with live health monitoring. Zero additional code. Then add decorators for full timelines. Then sprinkle events for business context. Each layer sells the next. Never all-or-nothing.

---

## Chapter 5: The Specs Sprint

**Date:** February 11, 2025 (full day)

Juan poured an entire day into specification work. Having built observability tooling for his own agents was an enormous accelerator — he knew exactly what developers needed because he *was* that developer.

Working across three Claude instances — **Claude Chat** (co-project manager), **Claude Code CLI** (Team 1), and **Claude Code Cloud** (Team 2) — the team produced a comprehensive specification suite:

- Event Schema Specification (13 event types, single-table architecture)
- Data Model Specification (which would evolve through v1 → v5 over the build)
- API and SDK Specification (RESTful API + Python SDK)
- Tiered Instrumentation Model (Layer 0 → 1 → 2)
- Dashboard Design with the **2-Second Test** principle

### The 2-Second Test

A core design principle emerged: if you can't tell whether something's wrong in under 2 seconds, the dashboard has failed. Stuck and error agents float to the top. Healthy idle agents sink to the bottom. The sort order is "needs attention," not alphabetical. This is the wall-monitor test — put it on a screen by the door, and you should know the fleet status in the time it takes to walk past.

### The Timeline as X-Ray

The per-task timeline emerged as the product's core artifact — every step, every decision, every action, every error, every retry, rendered as a visual story. And critically, every timeline has a **permalink**. Someone asks "why did task X fail?" in Slack — paste the link, see the full story. Investigation time: 15 seconds.

---

## Chapter 6: Two Teams, One Mission

With specs in hand, the work was organized into a two-team structure — a deliberate choice to enable cross-auditing and parallel development:

| | **Team 1 — Claude Code CLI** | **Team 2 — Claude Code Cloud** |
|---|---|---|
| **Environment** | Local terminal, CLI-based | Browser-based cloud IDE |
| **Personality** | More technically oriented | More functionally oriented |
| **Quality pattern** | Fast execution, more issues in audits | Higher spec compliance, fewer errors |

> **A note on "teams":** Each team is an instance of Claude Code — Anthropic's agentic coding tool. CLI runs in the local terminal; Cloud runs in the browser. Same underlying model, but as the build would reveal, they exhibited consistently different "personalities" in how they approached problems.

The development was phased:

- **Phase 0** → CLI established common ground (repo setup, shared foundations)
- **Phases 1, 2, 3** → Each team received their respective spec assignments

Juan created the **GitHub repository** and the build began.

---

## Chapter 7: The Audit Machine

This is where the process became a quality multiplier. After each phase, a rigorous cross-audit system:

1. **Claude Chat** created super-detailed **Audit Documents** — one per phase, per team
2. After syncing all repos, **Team 1 audited Team 2's work**, and **Team 2 audited Team 1's work**
3. Issues were found on both sides every time

The numbers tell the story: **450+ checkpoints** across ingestion, query endpoints, WebSocket, derived state, and error handling. Team 2's SDK came back clean — zero critical issues. Team 1's backend had **12 critical integration failures** that would have broken the dashboard at runtime. All 12 were invisible to Team 1's own 72 passing unit tests because those tests validated internal logic, not the contract the dashboard expected.

**The consistent finding across every phase:** Claude Code Cloud (Team 2) produced higher quality work. More spec-compliant. Fewer errors. This pattern held for every single phase and never broke.

---

## Chapter 8: The Final Audit

**~2 hours of total development time.** That's how long the actual coding took across all phases.

But code complete ≠ done. Juan ran a two-round final validation:

**Round 1:** One massive audit document covering full spec compliance + unit tests. Executed by Team 1 (CLI). Issues found and fixed.

**Round 2:** Repos synced. Same full audit executed by Team 2 (Cloud). Very few findings remaining — edge cases caught and fixed.

---

## Chapter 9: Real-World Integration

**Date:** February 12, 2025

The simulator built in Phase 1 had served its purpose. Now: the real thing. Integrating HiveBoard with Juan's own agent framework — the very system whose pain birthed the product.

### The Integration Guide

Before writing integration code, the team created a detailed **Integration Guide** — a document any developer could follow, regardless of their agent framework. This wasn't just for internal use; it would be the template for every future integration by any developer.

### Layer by Layer

The integration followed HiveBoard's tiered model:

| Layer | What It Activates | What You Get |
|---|---|---|
| **Layer 0** | Presence + heartbeat | Agent appears on dashboard, health monitoring, stuck detection |
| **Layer 1** | Action tracking | Task timelines, action durations, error tracking |
| **Layer 2** | Rich narrative telemetry | Full prompt/response visibility, token counts, cost tracking, plans, issues |

Each layer: integrate → validate → experiment → adjust UI/UX → adjust backend → next layer.

### Stress Testing

Once fully integrated across all layers, multiple stress tests were executed with real agent workloads. The system processed events at sustained throughput while maintaining sub-second ingestion latency. Details were fine-tuned. The platform was processing real agent data.

---

## Chapter 10: The UI/UX Reckoning

And then reality hit.

When real data started flowing through the dashboard — not simulated data, not test data, *real agent telemetry* — Juan saw what the simulator had hidden.

Here's the specific moment: Juan opened the dashboard. Three agents were running. Events were streaming. Cards showed status. The timeline rendered. Everything was *technically working*. But when Juan looked at the screen and asked himself the most basic question — **"What are my agents actually doing right now?"** — he couldn't answer it.

> *"I see a lot of data, but I don't get it."*

The data was there — event types, timestamps, payload fragments — but it wasn't **telling a story**. There was no narrative. No flow. You could stare at the screen for 30 seconds and still not know whether things were healthy or on fire. The 2-Second Test? Failing completely. The dashboard was a data dump, not an observability tool.

The SDK was solid. The backend was battle-tested. The event architecture was clean. But the interface was an information graveyard — data present, meaning absent.

Every assumption was questioned. The verdict was clear:

> SDK ✅ — Backend ✅ — Architecture ✅ — UI/UX ❌

Back to the drawing board.

---

## Chapter 11: The Redesign — A Symphony of Claudes

This became the most collaborative and fascinating phase of the entire build.

### Step 1: The Problem Statement

Juan worked with all three Claudes to document exactly what was wrong — not just technically, but viscerally. The core failure: the dashboard showed data without context. Events streamed in but didn't compose into a narrative. You couldn't glance at the screen and *feel* the health of your fleet.

### Step 2: The Great Brainstorm Experiment

In a move that yielded one of the journey's most interesting insights, Juan went back to his **agent framework development teams** — the Claudes who worked daily on the codebase that *used* observability tooling:

- **The CLI team** → Generated a **technically-oriented** brainstorm document. Architecture improvements, data flow optimizations, performance considerations.
- **The Cloud team** → Generated a **functionally-oriented** brainstorm document. User workflows, practical value delivery, information hierarchy.

> 🔍 **Same context. Same prompt. Completely different outputs.** This mirrored the pattern seen throughout the entire build — CLI gravitating toward technical depth, Cloud gravitating toward user experience. Two complementary perspectives that, together, covered more ground than either alone.

### Step 3: The New Spec

Claude Chat (Project Manager) synthesized all input — Juan's instincts, the technical analysis, the functional vision — into a new UI/UX specification. The redesign transformed the dashboard from a data dump into a narrative engine:

- **The Hive** — agent cards that tell you fleet health in a glance, with queue depth badges, processing status lines, and issue indicators
- **Mission Control** — a center panel where the Task Table, Timeline, and filters work as a connected system rather than isolated widgets
- **The Activity Stream** — real-time events with kind-aware icons and semantic rendering, so you see "Queue: 4 items, oldest: 2m" instead of raw JSON

### Step 4: Team Review and Consensus

Juan navigated the new UI. Teams 1 and 2 reviewed independently. Iterations were made. **Consensus reached** — approved by everyone.

The redesign required only **3 additional SDK endpoints**. The backend architecture held. It was just the window into the data that needed rebuilding.

---

## Chapter 12: Ship It

**Date:** February 12, 2025, ~8:00 PM

The redesigned UI/UX went live. Testing began. Fine-tuning continued.

**~Midnight:** HiveBoard v1 was wrapped.

A complete, working AI agent observability platform — conceived, specified, built, audited, integrated with a real agent framework, stress-tested, redesigned from scratch, and shipped — in roughly **48 hours**.

---

## Why Now?

AI agents are crossing the threshold from demo to production. Every major framework — LangChain, CrewAI, AutoGen, and dozens more — is shipping agent capabilities. Enterprises are deploying agents into real workflows: sales, support, operations, finance. The agent population is exploding from thousands to millions.

But production demands accountability. When an agent handles a customer ticket or qualifies a sales lead, someone needs to know it's working, catch it when it isn't, and understand why it failed. Traditional APM tools model HTTP requests and database queries — they have no concept of tasks, stuck states, escalations, or recovery paths. The observability gap is growing faster than the agent ecosystem itself.

HiveBoard fills that gap.

---

## Chapter 13: What's Next

**Date:** February 13, 2025 — Present

The platform is alive. The foundation is solid. Real agents are being monitored. Real insights are flowing. The journey continues.

---

## The Numbers

| Metric | Value |
|---|---|
| **Total build time** | ~48 hours |
| **Coding time (all phases)** | ~2 hours |
| **Specs, audits, design time** | ~46 hours |
| **Claude instances orchestrated** | 3 (Chat + CLI + Cloud) |
| **Audit rounds** | Cross-audit every phase + 2 final rounds |
| **Instrumentation layers** | 3 (Layer 0 → 1 → 2) |
| **Event types in schema** | 13 |
| **UI/UX complete redesigns** | 1 (based on real data feedback) |
| **New endpoints for redesign** | 3 |
| **Cost reduction demonstrated** | $40/hr → $8/hr (80%) from visibility alone |
| **Audit checkpoints evaluated** | 450+ |
| **Critical bugs caught by cross-auditing** | 12 (invisible to unit tests) |
| **Test suite growth from audits** | 125 → 152 (+22%), zero regressions |
| **Spec documents produced** | 6 major specifications (Event Schema v2, Data Model v5, API/SDK v3, Product Spec, Integration Guide, Dashboard v3) |
| **Data model spec versions** | 5 iterations (v1 → v5) |

---

## The Team

| Role | Entity | What They Brought |
|---|---|---|
| **Founder & Product Lead** | Juan | The battle scars, the vision, every critical decision |
| **Co-Project Manager** | Claude Chat (Anthropic) | Strategy, specs, UI/UX development, audit documents |
| **Team 1 — Dev** | Claude Code CLI (Anthropic) | Technical depth, fast execution, local development |
| **Team 2 — Dev** | Claude Code Cloud (Anthropic) | Functional orientation, higher spec compliance |
| **Consulting** | Agent framework dev teams (Claude Code) | Real-world user perspective, brainstorm input |

---

## The Process — What Made It Work

**1. Build from pain, not speculation.**
Every feature traced back to a real problem hit running agents in production. No speculative features. Every line of spec was validated against lived experience.

**2. Specs before code, always.**
The ratio tells the story: ~2 hours coding, ~46 hours on specs, audits, and design. The code almost wrote itself because the specs left nothing ambiguous.

**3. Cross-audit everything.**
Having Team 1 audit Team 2 and vice versa caught issues neither team found in their own work. Adversarial review as a quality multiplier.

**4. Kill fast, pivot faster.**
FormsFlow was built and killed in a single session. No sunk cost fallacy. Lame? Dead. Next.

**5. Real data reveals what simulators hide.**
The simulator was useful for development, but real agent data revealed the entire UI/UX was wrong. The willingness to redesign completely — not patch — was the difference between a demo and a product.

**6. Multiple minds, better answers.**
The CLI/Cloud personality difference was a feature, not a bug. Technical + functional perspectives on the same problems consistently produced more complete solutions than either alone.

---

## One-Liners That Tell the Story

> *"$40/hour → $8/hour. The only thing that changed was visibility."*

> *"3 lines of code. 30 seconds. Your agent has a heartbeat."*

> *"The most dangerous agent failure is the one that doesn't look like a failure."*

> *"Your agents are working. Are they healthy?"*

> *"One event stream. One truth."*

---

## Key Artifacts

- [ ] Original 5 candidate ideas document
- [ ] FormsFlow specs and prototype
- [ ] HiveBoard specification documents (Event Schema v2, Data Model v5, API/SDK v3)
- [ ] Phase audit documents (per phase, per team)
- [ ] Final mega-audit document
- [x] Audit Impact Reports (Team 1 and Team 2 — 450+ checkpoints, 12 critical bugs caught)
- [ ] Integration Guide (Layer 0, 1, 2)
- [ ] UI/UX redesign problem statement
- [ ] CLI brainstorm document (technical perspective)
- [ ] Cloud brainstorm document (functional perspective)
- [ ] Final UI/UX specification (Dashboard v3)
- [ ] AHA Moments Compendium
- [x] **The Hive Method** — the development methodology extracted from this build (companion document)

---

## Technical Appendix

### A.1 — The Event: HiveBoard's Single Data Primitive

Every piece of data in HiveBoard is an event. There is no separate status table, metrics table, or log table. Dashboards, timelines, alerts, and cost tracking are all derived from the event stream.

The schema is **progressive** — a developer using 3 lines of code (Layer 0) uses the exact same schema as a developer with full instrumentation (Layer 2). Fields are nullable, not absent. The shape never changes between instrumentation depths.

**Canonical stored event:**

```
event_id        UUID        Primary key
tenant_id       TEXT        Security boundary (derived from API key)
environment     TEXT        "production", "staging", "development"
project_id      TEXT        Organizational grouping (optional)
agent_id        TEXT        Which agent emitted this
task_id         TEXT        Which task this belongs to (null for agent-level events)
action_id       TEXT        Which action this belongs to (null for non-action events)
event_type      ENUM        One of 13 types (see below)
severity        ENUM        debug / info / warn / error
status          TEXT        success / failure / etc.
duration_ms     INTEGER     Milliseconds
parent_event_id UUID        Causal linkage (retry chains, escalations)
payload         JSON        Developer-defined metadata (32KB max)
timestamp       TIMESTAMP   When it happened
```

### A.2 — The 13 Event Types

```
LAYER 0 — Agent Lifecycle
  agent_registered      Agent comes online
  heartbeat             Periodic alive signal (every 30s default)

LAYER 1 — Structured Execution
  task_started           Task begins processing
  task_completed         Task finishes successfully
  task_failed            Task finishes with failure
  action_started         Tracked function begins
  action_completed       Tracked function succeeds
  action_failed          Tracked function fails

LAYER 2 — Narrative Telemetry
  retry_started          Automatic or manual retry
  escalated              Handed off to human or another agent
  approval_requested     Waiting for human approval
  approval_received      Approval granted
  custom                 Extensible event (LLM calls, plans, issues, queue snapshots)
```

### A.3 — Real Event Example: An LLM Call

When an agent makes an LLM call during a task, this event is emitted:

```json
{
  "event_id": "550e8401-e29b-41d4-a716-446655440001",
  "agent_id": "lead-qualifier",
  "task_id": "task_lead-4821",
  "event_type": "custom",
  "severity": "info",
  "timestamp": "2026-02-10T14:32:02.100Z",
  "payload": {
    "kind": "llm_call",
    "summary": "lead_scoring → claude-sonnet (1500 in / 200 out, $0.003)",
    "data": {
      "name": "lead_scoring",
      "model": "claude-sonnet-4-20250514",
      "tokens_in": 1500,
      "tokens_out": 200,
      "cost": 0.003,
      "duration_ms": 1847,
      "prompt_preview": "You are a lead scoring agent. Evaluate...",
      "response_preview": "{\"score\": 72, \"reasoning\": \"Strong company fit...\"}"
    },
    "tags": ["llm", "scoring", "sonnet"]
  }
}
```

This single event powers: the **Cost Explorer** (aggregated spend by model, by agent), the **Timeline** (LLM node with model badge and token counts), and the **Activity Stream** (real-time LLM call notification).

### A.4 — Real Task Timeline: Lead Processing

A complete task timeline for a "lead-qualifier" agent processing a sales lead:

```
TIMELINE: task_lead-4821 | agent: lead-qualifier | status: completed | 12.4s | $0.08

[task_started]                    0.0s    "New lead processing task received"
    │
[action: fetch_crm_data]          0.4s    duration: 1.8s → success
    │
[llm_call: phase1_reasoning]      2.1s    claude-sonnet | 1500→200 tokens | $0.003
    │
[action: enrich_company]          3.4s    duration: 2.1s → success
    │
[llm_call: lead_scoring]          5.8s    claude-sonnet | 1800→250 tokens | $0.004
    │
[action: score_lead]              8.2s    duration: 0.3s → success
    │
[llm_call: route_decision]        8.9s    claude-haiku | 1200→150 tokens | $0.001
    │
[action: route_lead]             10.1s    duration: 1.9s → success
    │
[task_completed]                 12.4s    "Lead scored 72, routed to sales rep"

COST BREAKDOWN: 3 LLM calls | 4,500 tokens in | 600 tokens out | $0.008 total
```

Every node is clickable. Every timeline has a permalink. When someone asks "why did lead processing fail?" — paste the link, see the full story.

### A.5 — The Payload Convention System

The `payload` field is a 32KB JSON blob that carries domain-specific data. HiveBoard defines **well-known payload kinds** that the dashboard recognizes and renders with specialized treatment:

| `kind` | What It Captures | Dashboard Rendering |
|---|---|---|
| `llm_call` | Model, tokens, cost, prompt/response | LLM badge on timeline, feeds Cost Explorer |
| `queue_snapshot` | Queue depth, items, processing status | Queue badge on agent card, Pipeline tab |
| `todo` | TODO lifecycle (created/completed/failed) | TODOs table in Pipeline tab |
| `scheduled` | Upcoming scheduled work items | Scheduled table in Pipeline tab |
| `plan_created` | Multi-step plan with step descriptions | Plan progress bar on timeline |
| `plan_step` | Step completion within a plan | Updates plan progress bar |
| `issue` | Agent self-reported problems | Issue badge on agent card, severity filtering |

All well-known kinds share a common envelope:

```json
{
  "kind": "<well-known-kind>",
  "summary": "Human-readable one-liner for the activity stream",
  "data": { },
  "tags": ["optional", "filtering", "tags"]
}
```

The convention system means the schema never changes — new observability capabilities are added as new payload kinds, not new tables or columns.

### A.6 — Derived Agent State: Computed, Not Persisted

There is no `status` column in the agents table that gets updated. Agent state is computed at query time from the event stream:

```python
def derive_agent_status(agent_id):
    last_event = db.query("SELECT * FROM events WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 1", agent_id)

    if now() - last_event.timestamp > STUCK_THRESHOLD:
        return "stuck"
    if last_event.event_type == "approval_requested":
        return "waiting_approval"
    if last_event.event_type in ("task_failed", "action_failed"):
        return "error"
    if last_event.event_type in ("task_started", "action_started"):
        return "processing"
    return "idle"
```

One function. No stale state. No synchronization bugs. If the event stream is correct, the status is correct.

### A.7 — SDK Integration: From Zero to Full Observability

```python
import hiveloop

# LAYER 0 — 3 lines, agent appears on dashboard with heartbeat
hb = hiveloop.init(api_key="hb_live_a1b2c3d4e5f6", environment="production")
agent = hb.agent("lead-qualifier", type="sales", version="1.2.0")

# LAYER 1 — Decorators on existing functions, full task timelines
@agent.track("fetch_crm_data")
def fetch_crm(lead_id):
    return crm_client.get_lead(lead_id)

@agent.track("score_lead")
def score(lead_data, enrichment, task):
    response = llm.complete(model="claude-sonnet-4-20250514", prompt=scoring_prompt)

    # LAYER 2 — Rich events: LLM cost tracking
    task.llm_call(
        name="lead_scoring",
        model="claude-sonnet-4-20250514",
        tokens_in=response.usage.input_tokens,
        tokens_out=response.usage.output_tokens,
        cost=response.usage.cost,
        prompt_preview=scoring_prompt[:200],
        response_preview=str(response.content)[:500]
    )
    return parse_score(response)

# Task execution — automatic timeline generation
with agent.task("task_lead-4821", project="sales-pipeline", type="lead_processing") as task:
    lead = fetch_crm("lead-4821")
    result = score(lead, enrichment, task)
    task.event("scored", {"score": result.score, "threshold": 80})
```

Each layer builds on the previous one. Nothing breaks if you stop at Layer 0. Each addition is additive, never invasive.

---

*This is a living document. THE JOURNEY continues.*

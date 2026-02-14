<p align="center">
  <img src="docs/assets/hiveboard-logo.png" alt="HiveBoard Logo" width="120" />
</p>

<h1 align="center">HiveBoard</h1>

<p align="center">
  <strong>The Datadog for AI Agents</strong><br>
  Framework-agnostic observability for production AI agent systems
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#the-problem">The Problem</a> •
  <a href="#what-hiveboard-does">What It Does</a> •
  <a href="#hiveloop-sdk">HiveLoop SDK</a> •
  <a href="#dashboard">Dashboard</a> •
  <a href="#the-hive-method">The Hive Method</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#the-numbers">The Numbers</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+" />
  <img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT" />
  <img src="https://img.shields.io/badge/built_with-Claude-blueviolet.svg" alt="Built with Claude" />
  <img src="https://img.shields.io/badge/hackathon-Anthropic_2026-orange.svg" alt="Anthropic Hackathon 2026" />
</p>

---

## The Problem

Every team deploying AI agents goes blind the moment they move past demo day.

You can't answer basic questions: *Is my agent stuck right now? Why did it fail? How long does it typically take? How much is it costing me per task? What exactly did it do, step by step?*

Existing tools don't help. LangSmith is locked to LangChain. Langfuse tracks LLM calls, not agent workflows. Datadog monitors HTTP requests, not agents-as-workers. None of them think in terms of **tasks, heartbeats, stuck states, escalations, and recovery paths**.

HiveBoard fills that gap.

> *"$40/hour → $8/hour. The only thing that changed was visibility."*

---

## What HiveBoard Does

HiveBoard treats AI agents as **workers** — not as API calls or trace spans. Each agent has a heartbeat, a status, tasks, a work queue, and a cost profile. When something goes wrong, you see it in real time.

**Live Fleet Monitoring** — Every agent shows its current state (idle, processing, stuck, error, waiting for approval) with a live heartbeat. If an agent stops responding, HiveBoard knows before you do.

**Task Timelines** — Click any task and see every step: actions executed, LLM calls made, decisions taken, errors hit, retries attempted, escalations triggered. The full X-ray of agent behavior.

**Cost Explorer** — Per-model, per-agent, per-task cost breakdowns. See which models burn the most budget. See which agents are expensive. This is how we achieved an 80% cost reduction — by seeing what agents were actually sending to the LLM.

**Real-Time Activity Stream** — WebSocket-powered live feed of every event. Filter by agent, task, or event type. Watch your agents work like watching a deployment in progress.

**Pipeline View** — Queue depth, pending approvals, open issues, scheduled work, TODOs. The complete picture of an agent's world, not just its current task.

**Stuck Detection** — Configurable thresholds. If a task runs too long, HiveBoard surfaces it automatically. The most dangerous agent failure is the one that doesn't look like a failure.

---

## Quick Start

### 1. Start the HiveBoard Server

```bash
# Clone the repo
git clone https://github.com/your-org/hiveboard.git
cd hiveboard

# Install dependencies
pip install -r requirements.txt

# Start the server
cd src
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

The dashboard is now live at `http://localhost:8000`.

### 2. Install the SDK

```bash
pip install hiveloop
```

### 3. Instrument Your Agent (3 Lines)

```python
import hiveloop

hb = hiveloop.init(api_key="hb_live_your_key_here", endpoint="http://localhost:8000")
agent = hb.agent("my-agent", type="general")
```

That's it. Your agent now appears on the HiveBoard dashboard with a live heartbeat. If it stops, HiveBoard notices within 5 minutes and marks it as stuck.

### 4. Add Task Tracking

```python
with agent.task("task-123", project="my-project", type="processing") as task:
    result = do_work()

    task.llm_call("reasoning", model="claude-sonnet-4-20250514",
                  tokens_in=1500, tokens_out=200, cost=0.003)
```

Now you have task timelines with action tracking and LLM cost breakdowns in the Cost Explorer.

---

## HiveLoop SDK

HiveLoop is the open-source Python SDK that instruments your agents. It sends structured events to HiveBoard, which visualizes them in real time.

**HiveLoop is not a framework.** It doesn't change how your agent works. It watches what your agent does and reports it.

### Three Layers of Instrumentation

| Layer | Effort | What You See on HiveBoard |
|-------|--------|---------------------------|
| **Layer 0** — Init + Heartbeat | 3 lines | Agent appears with live heartbeat, stuck detection, online/offline status |
| **Layer 1** — Decorators + Task Context | Add decorators | Task timelines with action tracking, duration, success/failure |
| **Layer 2** — Rich Events | Sprinkle events | LLM costs, plans, escalations, approvals, retries — the full narrative |

Each layer is independent. Start with Layer 0, stop whenever you have enough visibility.

### Decorator-Based Action Tracking

```python
@agent.track("evaluate_lead")
def evaluate(lead):
    score = run_scoring_model(lead)
    return score
```

Every decorated function becomes a visible step in the task timeline — with automatic duration tracking, success/failure capture, and exception logging.

### Rich Events for the Full Story

```python
task.llm_call("reasoning", model="claude-sonnet-4-20250514",
              tokens_in=1500, tokens_out=200, cost=0.003)

task.plan(goal="Process inbound lead", steps=["Score", "Enrich", "Route"])
task.plan_step(step_index=0, action="completed")

task.escalate(reason="Low confidence score", severity="medium")
task.request_approval(action="send_contract", details={"value": "$50k"})
```

### Framework Agnostic

HiveLoop works with any agent framework — LangChain, CrewAI, AutoGen, or your custom code. Integration is through lightweight decorators and event calls, not framework-specific hooks.

```
Your Agent Code
    ↓ (add decorators + events)
HiveLoop SDK
    ↓ (batched HTTP, background thread)
HiveBoard Server
    ↓ (WebSocket, real-time)
Dashboard + Alerts
```

For detailed integration patterns, see the [Integration Guide](docs/INTEGRATION_GUIDE.md).

---

## Dashboard

The HiveBoard dashboard is a real-time, WebSocket-powered interface organized around three views:

### Mission Control

The fleet-at-a-glance view. Agent cards with heartbeat indicators, a stats ribbon (tasks completed, success rate, stuck agents, errors), task table with filtering, and mini-charts for trends.

### Cost Explorer

Per-model and per-agent cost breakdowns, cost timeseries, token usage analysis, and a recent calls table with full detail. This is where the 80% cost reduction happened — by seeing which agents were sending bloated prompts to expensive models.

### Agent Detail

Deep-dive into a single agent: task history, processing timeline with action nodes, pipeline view (queue, issues, TODOs, scheduled work), and performance metrics.

### Activity Stream

Always-visible right sidebar with the live event feed. Every event that flows through the system appears here in real time — filterable by agent, task, or event type.

---

## The Hive Method

HiveBoard was built using **The Hive Method** — a development methodology for building production software with multi-agent AI teams.

### The Approach

One human orchestrator directing three specialized Claude instances:

| Role | Agent | Responsibility |
|------|-------|----------------|
| **Founder & Product Lead** | Juan | Vision, decisions, quality gates |
| **Co-Project Manager** | Claude Chat | Strategy, specs, UI/UX design, audit documents |
| **Team 1 — Dev** | Claude Code CLI | Implementation, technical architecture |
| **Team 2 — Dev** | Claude Code Cloud | Implementation, functional design |

### The Five Principles

1. **Role Specialization** — Cast agents into distinct roles. Same model, different environment = different tendencies. Observe and assign to strengths.

2. **Specs as Coordination Protocol** — The specification replaces meetings, shared memory, and institutional knowledge. ~46 hours on specs vs. ~2 hours coding.

3. **Adversarial Cross-Auditing** — Team 1 audits Team 2, and vice versa. No ego, no politics. 450+ checkpoints evaluated, 12 critical bugs caught that were invisible to unit tests.

4. **Human as Orchestrator** — The human sets vision, makes decisions, and enforces quality gates. The agents execute within boundaries the human defines.

5. **Kill Fast, Pivot Faster** — The first product idea (FormsFlow) was built and killed in a single session. No sunk cost fallacy. The pivot to HiveBoard happened because the real insight was recognized immediately.

For the full methodology, see [The Hive Method](docs/The-Hive-Method.md).

---

## Architecture

### Tech Stack

| Component | Technology |
|-----------|------------|
| **Backend** | Python 3.11+ / FastAPI (async-native) |
| **Database** | PostgreSQL + TimescaleDB (production) / SQLite (development) |
| **Real-time** | WebSocket (FastAPI native) |
| **Frontend** | Vanilla JS + CSS (no framework dependencies) |
| **SDK** | Python (`pip install hiveloop`) |
| **Auth** | Multi-tenant from day one, API key authentication |

### Event-Driven Core

Everything in HiveBoard is an event. There is one events table. Dashboards, timelines, metrics, alerts, and cost tracking are all derived from the event stream.

```
Agent → HiveLoop SDK → Batched HTTP → HiveBoard API → Events Table
                                                           ↓
                                          WebSocket → Dashboard (real-time)
                                          Aggregates → Metrics & Cost Explorer
                                          Rules → Alerts
```

### Data Model

The organizational hierarchy is: **Tenant → Projects → Tasks → Events**, with Agents existing at the tenant level and being assignable to multiple projects. Events are the single source of truth — agent profiles are a convenience cache that's always rebuildable from events.

### Key Design Decisions

**Multi-tenancy is structural, not optional.** Every table has `tenant_id` as the leading column. Every index leads with `tenant_id`. No query crosses tenant boundaries.

**The SDK never blocks your application.** Events are buffered in memory and flushed via a background thread every 5 seconds. If the server is unreachable, events are queued (up to 10,000) and retried with exponential backoff.

**Zero-config value.** 3 lines of code gets you heartbeats and stuck detection. No schema definition, no config files, no setup wizards.

---

## The Numbers

| Metric | Value |
|--------|-------|
| Total build time | ~48 hours |
| Coding time (all phases) | ~2 hours |
| Specs, audits, design time | ~46 hours |
| Claude instances orchestrated | 3 (Chat + CLI + Cloud) |
| Cross-audit checkpoints evaluated | 450+ |
| Critical bugs caught by cross-auditing | 12 (invisible to unit tests) |
| Test suite growth from audits | 125 → 152 (+22%), zero regressions |
| Cost reduction demonstrated | $40/hr → $8/hr (80%) from visibility alone |
| Spec documents produced | 6 major specifications |
| Data model iterations | 5 (v1 → v5) |
| Event types in schema | 13 |

---

## Project Structure

```
hiveboard/
├── src/
│   ├── backend/          # FastAPI server — API, WebSocket, database
│   │   ├── app.py        # Application entry point
│   │   ├── models.py     # SQLAlchemy / data models
│   │   ├── routes/       # API route handlers
│   │   └── ws/           # WebSocket handlers
│   ├── frontend/         # Dashboard — vanilla JS + CSS
│   │   ├── index.html    # Main dashboard
│   │   └── assets/       # Styles, scripts
│   ├── sdk/              # HiveLoop Python SDK
│   │   ├── hiveloop/     # Package source
│   │   └── setup.py      # pip installable
│   └── simulator/        # Agent simulator for development/demos
├── docs/                 # Specifications and guides
│   ├── INTEGRATION_GUIDE.md
│   ├── The-Hive-Method.md
│   └── specs/            # Event schema, data model, API spec
├── tests/                # Test suite
├── requirements.txt
└── README.md
```

---

## What HiveBoard Is NOT

- **NOT an agent framework** — doesn't compete with LangChain, CrewAI, AutoGen
- **NOT an agent builder** — doesn't help you create agents
- **NOT an LLM gateway** — doesn't compete with LiteLLM or Portkey
- **NOT a logging tool** — goes far beyond structured logs
- **NOT tied to any use case** — works for sales agents, support agents, coding agents, data agents, etc.

HiveBoard is the **layer that sits on top of any agent system** and makes the invisible visible.

---

## Competitive Landscape

| Tool | Focus | HiveBoard Difference |
|------|-------|---------------------|
| LangSmith | LangChain observability | Framework-agnostic, agent-level not trace-level |
| Langfuse | LLM call logging | Agents-as-workers, not API-calls-as-traces |
| Arize Phoenix | ML observability | Agent-native mental model, not generic spans |
| Datadog / New Relic | Infrastructure APM | Agents have heartbeats, tasks, stuck states — not HTTP requests |
| Helicone | LLM proxy logging | Workflow-level, not request-level |
| Braintrust | Evals + logging | Operations-focused, not evaluation-focused |

The gap: none of these think in terms of agents-as-workers with tasks, actions, heartbeats, stuck states, escalations, and recovery paths. HiveBoard does.

---

## Built For

- **Anthropic Virtual Hackathon** — February 2026
- Built entirely using **Claude** (Opus 4.5 + Claude Code CLI + Claude Code Cloud)
- Demonstrates both the product and **The Hive Method** — a novel multi-agent development methodology

---

## License

MIT

---

<p align="center">
  <strong>Your agents are working. Are they healthy?</strong><br><br>
  <em>3 lines of code. 30 seconds. Your agent has a heartbeat.</em>
</p>

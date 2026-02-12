# HiveBoard — Product Context Brief

**Pass this entire document to Claude to start the product definition session.**

---

## What Is HiveBoard

HiveBoard is a **framework-agnostic observability platform for AI agents**. It answers the question every team deploying agents needs answered: *"What is this thing doing and why did it get stuck?"*

It is the **Datadog for agents**.

---

## The Thesis

Observability is the picks-and-shovels play of the agent gold rush. Every team deploying agents — regardless of framework (LangChain, CrewAI, AutoGen, custom) — will need to see what their agents are doing, why they failed, how long they took, and when they need human intervention. Agent frameworks are multiplying. The observability gap is growing faster than the frameworks themselves.

**Winner signal:** Observability tools historically win because they become essential once things break in production, and agents break *a lot*. Nobody needs observability on demo day. Everyone needs it on day 30 when the agent silently stopped working and nobody noticed for 6 hours.

**The market:** Every company deploying AI agents is a potential customer. This isn't tied to one use case (forms, support, sales). It's infrastructure. The addressable market grows with every new agent framework, every new deployment, every team that moves agents from prototype to production.

---

## What HiveBoard Is NOT

- NOT an agent framework (doesn't compete with LangChain, CrewAI, etc.)
- NOT an agent builder (doesn't help you create agents)
- NOT an LLM gateway (doesn't compete with LiteLLM or Portkey)
- NOT a logging tool (goes far beyond structured logs)
- NOT tied to any specific use case (works for sales agents, support agents, coding agents, data agents, etc.)

HiveBoard is the **layer that sits on top of any agent system** and makes the invisible visible.

---

## Core Value Proposition

Teams today deploy agents and then go blind. They can't answer basic questions:

- Is my agent stuck right now?
- Why did it fail on that task?
- How long does it typically take to process X?
- Which agent is performing best?
- When did it start degrading?
- What exactly did it do, step by step, for this specific task?
- How much is it costing me per task?

HiveBoard answers all of these in real-time.

---

## Proven Patterns (From Prior Work)

I built a comprehensive agent observability system inside another product (an inbound form processing platform). The patterns that worked and should inform HiveBoard's design:

### What Worked

1. **Live Status Board** — Each agent shows current state (idle/processing/waiting_approval/stuck/error), last heartbeat, current task, visual flag if stuck. This was the single most compelling demo feature. People immediately "get it" when they see agents with heartbeats.

2. **Processing Timeline** — Per-task horizontal timeline showing every step with timestamps and durations: task_received → agent_assigned → agent_processing → action_1 → action_2 → completed/failed. This is the "X-ray" into agent behavior. When something goes wrong, this is where you go.

3. **Activity Stream (Real-time via WebSocket)** — Live feed of events filterable by agent, task, event type. Events include: task_received, agent_assigned, agent_processing, action_executed, agent_completed, agent_error, agent_retry, agent_escalated, human_approved, human_rejected. This is the "pulse" of the system.

4. **Agent Performance Metrics** — Avg handling time, success rate (% completed without escalation/error), escalation rate, error rate, recovery rate (% errors self-recovered vs needed human), actions per task, cost per task.

5. **Error Recovery Tracking** — Retry with backoff logged as events, escalation paths visible, stuck detection (no heartbeat in X minutes), human takeover triggers. Every recovery step is a visible event in the timeline.

6. **Event-Driven Architecture** — Everything emits events. Events are the single source of truth. Dashboards, timelines, alerts, metrics — all derived from the event stream. This architecture decision was critical and should carry forward.

### The Key Insight

The observability layer was the most visually compelling and immediately understood part of the entire system. It was also the part that had nothing to do with the specific use case (forms). The same status board, timeline, activity stream, and metrics apply to ANY agent doing ANY work. That's why it should be its own product.

---

## Design Principles for HiveBoard

1. **Framework-agnostic from day one.** Integration via lightweight SDK or HTTP API. Send events, HiveBoard visualizes them. Don't force any specific agent architecture.

2. **Zero-config value.** Instrument an agent with 3 lines of code, immediately see it on the dashboard. No schema definition, no config files, no setup wizards.

3. **The timeline is the product.** The per-task processing timeline — showing every step, every decision, every action, every error, every retry — is the core artifact. Everything else orbits it.

4. **Real-time by default.** WebSocket-powered live dashboards. Agents show heartbeats. Events stream in live. You should be able to watch your agents work like watching a deployment in progress.

5. **Production-first, not demo-first.** The real value shows up when things break. Stuck detection, error patterns, performance degradation, cost anomalies — these are the features that make teams depend on HiveBoard.

6. **Multi-agent native.** Most teams don't have one agent. They have many — different types, different frameworks, different purposes. HiveBoard should handle fleets of agents as naturally as single ones.

---

## What I Want to Build in This Session

I want to go through the same product definition process we've used before:

1. **Product definition** — Modules, features, scope (in/out), user stories
2. **Data model** — What events look like, how they're stored, what's queryable
3. **SDK design** — How developers instrument their agents (Python first, then JS/TS)
4. **Dashboard design** — What screens exist, what data they show
5. **API design** — Endpoints for ingestion, querying, configuration
6. **Architecture** — Tech stack, real-time infrastructure, storage strategy
7. **Integration patterns** — How HiveBoard connects to LangChain, CrewAI, AutoGen, custom agents
8. **Differentiation** — What makes this different from generic APM tools, LangSmith, etc.

---

## Technical Preferences

- **Backend:** Python 3.11+ / FastAPI (async-native)
- **Database:** PostgreSQL for events + TimescaleDB extension for time-series, or SQLite for MVP
- **Real-time:** WebSocket (FastAPI native) + Server-Sent Events as fallback
- **Frontend:** Vanilla JS + CSS (no framework), or lightweight if needed
- **SDK:** Python first (pip installable), then TypeScript
- **Auth:** Multi-tenant from day one (learned this lesson — workspace_id on every table)

---

## Competitive Landscape to Consider

- **LangSmith** — LangChain's observability, but locked to LangChain ecosystem
- **Langfuse** — Open source LLM observability, but focused on LLM calls not agent workflows
- **Arize Phoenix** — ML observability extending to LLMs, but not agent-native
- **Datadog / New Relic / Grafana** — Generic APM, not built for agent mental models
- **Helicone** — LLM proxy with logging, but request-level not workflow-level
- **Braintrust** — Evals + logging, but evaluation-focused not operations-focused

The gap: **none of these think in terms of agents-as-workers with tasks, actions, heartbeats, stuck states, escalations, and recovery paths.** They think in terms of LLM calls, traces, or generic spans. HiveBoard thinks in terms of *"what is this agent doing on this task right now, and is it healthy?"*

---

## Name: HiveBoard

The name works because:
- **Hive** → many agents working together (swarm/colony metaphor)
- **Board** → dashboard, control board, mission control
- Combined: the command center for your agent hive

---

## Tone

Build this like a real product, not a hackathon demo. Multi-tenant auth from the start. Production error handling. The spec should be buildable end-to-end. I prefer practical, working solutions over complex architectural frameworks. Be opinionated about what's in scope and what's not.

Let's build HiveBoard.

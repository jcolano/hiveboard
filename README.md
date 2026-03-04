<p align="center">
  <img src="https://hiveboard.net/logo/hiveboard-logo.png" alt="HiveBoard Logo" width="120" />
</p>

<h1 align="center">HiveBoard</h1>

<p align="center">
  <strong>The Datadog for AI Agents</strong><br>
  Framework-agnostic observability for production AI agent systems
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#claude-agent-sdk-integration">Claude Agent SDK</a> •
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

<p align="center">
  <strong>🔴 <a href="https://hiveboard.net/static/fleet.html">Try It Live</a></strong> &nbsp;·&nbsp;
  <strong>📖 <a href="https://hiveboard.net/docs/user-manual.html">Documentation</a></strong> &nbsp;·&nbsp;
  <strong>🎬 <a href="https://youtu.be/lLWr9_1cgNw">Watch the Demo</a></strong>
</p>

---

> **Anthropic Virtual Hackathon 2026 — Problem Statement One: Build a Tool That Should Exist**
>
> *Agent observability is the tool every team deploying AI agents needs and nobody has built properly. HiveBoard fills that gap.*

---

## Live Demo

**🔴 [Try the live dashboard →](https://hiveboard.net/static/fleet.html)**

5 AI agents running a simulated company (BrightPath Digital) — live heartbeats, real-time tasks, cost data flowing. No signup required.

---

## Demo Video

*https://youtu.be/lLWr9_1cgNw — 3-minute demo showing the live dashboard, task timelines, cost optimization, and The Hive Method.*

---

## Website

**🌐 [Visit hiveboard.net →](https://hiveboard.net/)**

Product overview, architecture, and the story behind HiveBoard.

---

## Notable Integrations & Automations

### 🛠️ Claude Code Skill — 5-Minute Agent Instrumentation

We built a custom **Claude Code Skill** that lets any developer instrument their AI agents with HiveBoard in under 5 minutes. Just point Claude Code at your project, and the Skill walks through setup interactively — initializing HiveLoop, registering agents, wiring up decorators, and validating the dashboard connection. What used to be 30 minutes of reading docs and manual config is now a guided conversation.

### 🔗 Claude Agent SDK Integration — One Hook, Full Observability
```python
hooks=hiveloop_hooks(api_key="hb_live_xxx")
```

Add one hook to any [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview) agent and get full HiveBoard observability — heartbeats, task timelines, tool action tracking, subagent pipelines — with zero manual instrumentation. The integration maps the Agent SDK's lifecycle hooks directly to HiveLoop events, so every `Read`, `Edit`, `Bash`, and `Grep` call becomes a visible node in your dashboard. See the full [integration guide below](#claude-agent-sdk-integration).

---

## Documentation

**📖 [Read the full SDK manual →](https://hiveboard.net/docs/user-manual.html)**

Everything you need to instrument your agents: Quick Start, 3-layer instrumentation guide, integration patterns, configuration reference, and troubleshooting.

---

## The Story

Three documents tell the complete story of HiveBoard — why it exists, how it was built, and what it does:

**1️⃣ [THE JOURNEY →](https://hiveboard.net/story/1_the-journey-one-pager.html)**
The chronicle — from pain to product in 48 hours. 5 ideas, 1 killed, 1 shipped.

**2️⃣ [The Hive Method →](https://hiveboard.net/story/2_the-hive-method-one-pager.html)**
The development methodology — 1 human orchestrating 3 Claude Opus instances with adversarial cross-auditing.

**3️⃣ [What HiveBoard Sees →](https://hiveboard.net/story/3_what-hiveboard-sees-one-pager.html)**
The product — 38 questions your agents can finally answer, organized by the moment you're in.

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

### 1. Clone and Install

```bash
git clone https://github.com/jcolano/hiveboard.git
cd hiveboard

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install backend dependencies
pip install -e ".[backend]"

# Install the SDK (from the local source)
pip install -e "./src/sdk"
```

### 2. Configure

HiveBoard reads configuration from `config.json` (project root) with environment variable overrides (`HIVEBOARD_*`). Copy the template to get started:

```bash
cp config.example.json config.json
```

Edit `config.json` with your own values:

```json
{
  "dev_key": "hb_live_my_secret_dev_key_here",
  "dev_password": "pick-a-strong-password",
  "jwt_secret": "pick-a-random-secret-string",
  "jwt_expiry": 3600,
  "data_dir": "data",
  "mode": "local",
  "ws_gateway_endpoint": "",
  "ws_gateway_region": "us-east-1"
}
```

| Key | Description |
|-----|-------------|
| `dev_key` | The API key your agents use to authenticate (`hb_live_...`). Choose any string starting with `hb_live_`. |
| `dev_password` | Password for the default admin user on the dashboard. |
| `jwt_secret` | Secret for signing JWT tokens. If omitted, a random one is generated on each restart (sessions won't persist across restarts). |
| `mode` | `"local"` for direct WebSocket (default) or `"production"` for AWS API Gateway bridge. |
| `data_dir` | Where JSON data files are stored (default: `data/`). |

Alternatively, use environment variables (they override `config.json`):

```bash
cp .env.example .env
# Edit .env with your values, then:
source .env
```

### 3. Launch the Server

```bash
cd src
uvicorn backend.app:app --host 0.0.0.0 --port 8000
```

You should see output like:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     HiveBoard started — mode=local
```

**Verify it's running:**
```bash
curl http://localhost:8000/health
# → {"status": "ok", "version": "..."}
```

### 4. Access the Dashboard

Open **http://localhost:8000/dashboard** in your browser.

The dashboard URL includes an `accessId` parameter — this is a read-only key derived from your `dev_key`. On first startup, HiveBoard logs the full dashboard URL to the console. You can also find your access ID at:

```
http://localhost:8000/static/fleet.html?accessId=hb_read_...
```

### 5. Install the SDK and Instrument Your Agent

```bash
pip install hiveloop
```

**Minimal instrumentation (3 lines):**

```python
import hiveloop

hb = hiveloop.init(api_key="hb_live_my_secret_dev_key_here", endpoint="http://localhost:8000")
agent = hb.agent("my-agent", type="general")
```

Your agent now appears on the dashboard with a live heartbeat. If it stops reporting, HiveBoard marks it as stuck within 5 minutes.

**Add task tracking:**

```python
with agent.task("task-123", project="my-project", type="processing") as task:
    result = do_work()

    task.llm_call("reasoning", model="claude-sonnet-4-20250514",
                  tokens_in=1500, tokens_out=200, cost=0.003)
```

Now you have task timelines with action tracking and LLM cost breakdowns in the Cost Explorer.

### 6. WebSocket Setup (Real-Time Updates)

HiveBoard supports two WebSocket modes for pushing real-time updates to the dashboard:

**Local mode (default)** — WebSocket connections are handled directly by the FastAPI server. No additional setup is needed. This is what you get when `mode` is set to `"local"` in your config. The dashboard connects via `ws://localhost:8000/ws/...` automatically.

**Production mode (AWS API Gateway)** — For production deployments behind a load balancer, HiveBoard bridges WebSocket connections through AWS API Gateway:

1. Set up an AWS WebSocket API Gateway (see [AWS WebSocket Setup Guide](docs/AWS_WEBSOCKET_SETUP_GUIDE.md) if available)
2. Update your config:
   ```json
   {
     "mode": "production",
     "ws_gateway_endpoint": "https://your-api-id.execute-api.us-east-1.amazonaws.com/production",
     "ws_gateway_region": "us-east-1"
   }
   ```
3. Install the AWS dependency:
   ```bash
   pip install -e ".[aws]"
   ```

> **Note:** For local development and testing, local mode works out of the box. You only need production mode when deploying behind infrastructure that doesn't support sticky WebSocket connections.

### 7. Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

---

## Claude Agent SDK Integration

If you're building agents with Anthropic's [Claude Agent SDK](https://platform.claude.com/docs/en/agent-sdk/overview), HiveBoard plugs in with **two additional lines of code** — no manual instrumentation needed.

The Claude Agent SDK gives your agent built-in tools for reading files, running commands, editing code, and more. HiveBoard's integration uses the SDK's [hooks system](https://platform.claude.com/docs/en/agent-sdk/hooks) to automatically capture every tool call, LLM interaction, and session lifecycle event, turning them into a full observability timeline on your dashboard.

### Setup

```python
import asyncio
from claude_agent_sdk import query, ClaudeAgentOptions
from hiveloop.integrations.claude_agent_sdk import hiveloop_hooks    # 1. Import the hooks

async def main():
    async for message in query(
        prompt="Find and fix the bug in auth.py",
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Edit", "Bash"],
            hooks=hiveloop_hooks(api_key="hb_live_xxx"),              # 2. Pass them in
        ),
    ):
        print(message)  # Claude reads the file, finds the bug, edits it

asyncio.run(main())
```

That's it. Your Claude agent now reports to HiveBoard automatically.

### What You Get

Once `hiveloop_hooks` is wired in, every agent session is fully instrumented:

| Event | What HiveBoard Shows |
|-------|---------------------|
| **Session start/end** | Task timeline with duration, success/failure status |
| **Tool calls** (`Read`, `Edit`, `Bash`, `Grep`, ...) | Individual action nodes in the task timeline with duration tracking |
| **LLM calls** | Token counts, model used, cost per call |
| **Subagent spawns** (`Task` tool) | Child agents in the pipeline view, linked to the parent session |
| **Errors & exceptions** | Error events with severity, surfaced in stuck detection and alerts |

No decorators, no manual event calls, no config files. The hooks capture the SDK's native lifecycle and translate it into HiveLoop events.

### Configuration Options

`hiveloop_hooks` accepts the same options as `hiveloop.init`:

```python
hooks = hiveloop_hooks(
    api_key="hb_live_xxx",
    endpoint="https://your-hiveboard-server.com",  # Default: http://localhost:8000
    agent_name="bug-fixer",                         # Shows up as the agent ID on the dashboard
    heartbeat_interval=30,                          # Seconds between heartbeats (default: 30)
)
```

### Works With Everything the SDK Supports

The integration is transparent to the rest of your SDK configuration. Combine it with subagents, MCP servers, custom permissions, or session resumption — HiveBoard observes it all:

```python
options = ClaudeAgentOptions(
    allowed_tools=["Read", "Edit", "Bash", "Grep", "Task"],
    hooks=hiveloop_hooks(api_key="hb_live_xxx"),
    agents={
        "code-reviewer": AgentDefinition(
            description="Reviews code for quality and security.",
            prompt="Analyze code and suggest improvements.",
            tools=["Read", "Glob", "Grep"],
        )
    },
    mcp_servers={
        "playwright": {"command": "npx", "args": ["@playwright/mcp@latest"]}
    },
)
```

Both the parent agent and any subagents it spawns will appear on the HiveBoard dashboard with full timelines.

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
| **Database** | PostgreSQL + TimescaleDB (production) / Jsons (quick development) |
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
│   ├── backend/            # FastAPI server
│   │   ├── app.py          # App creation, lifespan, middleware
│   │   ├── routes/         # API route modules
│   │   │   ├── ingest.py   # POST /v1/ingest (write path)
│   │   │   ├── agents.py   # Agent + pipeline endpoints
│   │   │   ├── tasks.py    # Task + timeline endpoints
│   │   │   ├── events.py   # Events, metrics, cost, LLM calls
│   │   │   ├── insights.py # Pre-aggregated analytics
│   │   │   ├── projects.py # Project CRUD
│   │   │   ├── alerts.py   # Alert rules + history
│   │   │   ├── auth_routes.py # Auth, users, invites, API keys
│   │   │   ├── admin.py    # Admin rebuild + pricing
│   │   │   ├── ws.py       # WebSocket + AWS bridge
│   │   │   └── helpers.py  # Shared route utilities
│   │   ├── auth.py         # JWT + password hashing
│   │   ├── config.py       # Configuration loader
│   │   ├── middleware.py    # Auth + rate limiting
│   │   ├── storage_json.py # JSON file storage (MVP)
│   │   ├── websocket.py    # Local WebSocket manager
│   │   ├── ws_bridge.py    # AWS API Gateway bridge (optional)
│   │   ├── aggregator.py   # Event aggregation
│   │   ├── alerting.py     # Alert rule evaluation
│   │   └── llm_pricing.py  # LLM cost estimation
│   ├── sdk/                # HiveLoop Python SDK
│   │   └── hiveloop/       # Package source
│   ├── shared/             # Shared enums + Pydantic models
│   └── static/             # Dashboard HTML/JS/CSS
├── docs/                   # Specs, guides, user manual
├── tests/                  # Test suite
├── config.example.json     # Configuration template
├── pyproject.toml          # Python project config
├── LICENSE                 # MIT
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
- **Problem Statement One:** Build a Tool That Should Exist
- Built entirely using **Claude Opus 4.6** (Claude Chat + Claude Code CLI + Claude Code Cloud)
- Demonstrates both the product and **The Hive Method** — a novel multi-agent development methodology
- **🔴 [Live Demo](https://hiveboard.net/static/fleet.html)** — 5 AI agents running live, fed by the BrightPath Digital simulator
- **📖 [Full Documentation](https://hiveboard.net/docs/user-manual.html)** — SDK manual, integration guide, dashboard guide

---

## License

MIT

---

<p align="center">
  <strong>Your agents are working. Are they healthy?</strong><br><br>
  <em>3 lines of code. 30 seconds. Your agent has a heartbeat.</em>
</p>

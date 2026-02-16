# HiveLoop Claude Code Skill — Documentation

> **One slash command. Full observability integration.**
> The HiveLoop Claude Code Skill automates instrumenting any Python agentic application
> with HiveBoard observability — from codebase discovery to verified integration.

---

## What It Is

The HiveLoop Claude Code Skill is a custom command for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that guides the automated integration of HiveLoop SDK into any agentic Python application. Instead of reading documentation, copying snippets, and wiring things manually, you type one command and Claude Code handles everything.

It discovers your framework, adds the dependency, instruments minimum viable telemetry, layers in enhanced sensors where relevant, and verifies the integration — all while following your codebase's patterns and conventions.

**What it replaces:** 30 minutes of reading the Integration Guide + manual instrumentation.
**What it takes:** One command, one API key, ~2 minutes.

---

## Prerequisites

Before using the skill, you need:

| Requirement | Details |
|---|---|
| **Claude Code** | Installed and configured ([installation guide](https://docs.anthropic.com/en/docs/claude-code)) |
| **HiveBoard account** | Register at [hiveboard.net](https://hiveboard.net) to get your API key |
| **API key** | Starts with `hb_` — find it in the API Keys panel after registration |
| **Python project** | An agentic application (any framework or custom loop) |

---

## Installation

### Option 1: Download the zip (recommended)

1. Download [hiveloop-claude-code-skill.zip](../downloads/hiveloop-claude-code-skill.zip)
2. Unzip into your project's `.claude/commands/` directory:

```
my-agent-project/
├── .claude/
│   └── commands/
│       ├── integrate-hiveloop.md          ← Main skill file
│       ├── sdk-quick-reference.md         ← SDK function signatures
│       └── examples/
│           └── before-after.py            ← Before/after code example
├── src/
│   └── ...
└── requirements.txt
```

3. That's it. The skill is now available in Claude Code.

### Option 2: Manual setup

Create the directory structure above and copy the three files individually:

- `integrate-hiveloop.md` — The main skill instruction file (renamed from SKILL.md)
- `sdk-quick-reference.md` — All 28 sensor function signatures
- `examples/before-after.py` — A concrete before/after example

---

## Usage

Open your terminal in your project directory and run Claude Code:

```bash
/integrate-hiveloop hb_live_your_api_key_here
```

Replace `hb_live_your_api_key_here` with your actual HiveBoard API key.

If you forget the API key, the skill will ask you for it.

---

## What Happens: The Five Phases

When you run the command, the skill guides Claude Code through five phases. Each phase builds on the previous one.

### Phase 1 — Discover the Codebase

Before writing any code, the skill scans your project to understand:

- **Framework** — Is this LangChain, CrewAI, AutoGen, Semantic Kernel, or a custom agent loop?
- **Entry point** — Where does the application start? (`main()`, CLI, server handler, Lambda)
- **Agent creation** — Where are agent instances created or initialized?
- **Main work unit** — What constitutes a single "task"? (one message, one pipeline run, one batch item)
- **Tool functions** — Which functions does the LLM call as tools?
- **LLM calls** — Where does the code call an LLM API?
- **Error handling** — Where are exceptions caught, retried, or escalated?
- **Shutdown path** — Is there a graceful shutdown handler?
- **Dependencies** — `requirements.txt`, `pyproject.toml`, `setup.py`, etc.

The skill uses pattern matching (imports, decorators, function names) to identify these automatically.

### Phase 2 — Add the Dependency

The skill adds `hiveloop` to your project's dependency file — whichever format you use.

### Phase 3 — Minimum Viable Instrumentation

These five integration points give you immediate value in HiveBoard:

| Integration Point | What It Does | Where It Goes |
|---|---|---|
| **SDK Init** | Connects to HiveBoard | Application entry point, before any agent runs |
| **Agent Registration** | Creates the agent identity + heartbeat | Where your agent is created/initialized |
| **Task Wrapping** | Marks each unit of work | Around your main processing function |
| **LLM Call Tracking** | Records every LLM API call | After each LLM response |
| **Shutdown** | Graceful teardown | Exit handlers, end of `main()` |

After this phase, your HiveBoard dashboard shows:

- Agent cards with heartbeat and status
- Task lifecycle (started → completed / failed)
- LLM cost tracking and token analysis
- Activity stream with live events

### Phase 4 — Enhanced Instrumentation

The skill evaluates which enhanced sensors are relevant to *your* codebase and adds only what applies:

| Sensor | Added When | What It Unlocks |
|---|---|---|
| **`@agent.track()`** | You have tool functions or key steps | Action tree in task timelines |
| **`task.plan()`** | Your agent creates plans | Plan progress bar with step tracking |
| **`agent.report_issue()`** | You have error/retry handlers | Issue tracking with severity and categories |
| **`request_approval()`** | Human-in-the-loop flows exist | Approval workflow visibility |
| **`HiveBoardLogHandler`** | Always (catch-all) | Python WARNING+ logs as agent issues |
| **`heartbeat_payload`** | Framework exposes internal state | Custom state on every heartbeat |
| **`agent.todo()`** | Your agent manages work items | Todo/work item tracking |

### Phase 5 — Verify

The skill runs a verification checklist:

- Imports are correct and not circular
- `hiveloop.init()` runs before any `hb.agent()` call
- `agent.task()` wraps the work unit (not too wide, not too narrow)
- `task.llm_call()` has access to the response's token counts
- `hiveloop.shutdown()` runs on exit

Then it lists all files modified and summarizes what was instrumented.

---

## Supported Frameworks

The skill handles framework-specific integration patterns:

| Framework | Discovery Signal | Task Mapping |
|---|---|---|
| **LangChain** | `langchain` imports, `AgentExecutor` | One task per `invoke()` or chain run |
| **CrewAI** | `crewai` imports, `Crew`, `Task` | One task per CrewAI `Task` |
| **AutoGen** | `autogen` imports, `ConversableAgent` | One task per conversation turn |
| **Semantic Kernel** | `semantic_kernel` imports | One task per function invocation |
| **Custom loops** | Direct OpenAI/Anthropic/LiteLLM calls | One task per iteration of your main loop |

For frameworks that wrap LLM calls internally (LangChain callbacks, CrewAI hooks), the skill finds the callback/hook point and instruments `task.llm_call()` there.

---

## Example: Before and After

The skill includes a complete before/after example (`examples/before-after.py`) showing a sales-lead processing agent. Here's the key transformation:

**Before** — A working agent with zero observability:

```python
def process_leads(leads):
    for lead in leads:
        try:
            enrichment = enrich_lead(lead)
            score = score_lead(lead, enrichment)
            print(f"Lead {lead['id']}: score={score}")
        except Exception as e:
            logger.error(f"Failed: {e}")
```

**After** — Same agent, full HiveBoard visibility:

```python
def process_leads(leads):
    for lead in leads:
        with agent.task(lead["id"], project="sales-pipeline", type="lead_scoring") as task:
            try:
                enrichment = enrich_lead(lead)          # @agent.track decorated
                score = score_lead(task, lead, enrichment)  # task.llm_call() inside
                task.set_payload({"score": score, "company": enrichment.get("company")})
            except Exception as e:
                agent.report_issue(f"Failed: {e}", severity="high", category="data_quality")
                raise  # task context manager auto-emits task_failed
```

The full before/after file is included in the skill download.

---

## SDK Quick Reference

The skill includes `sdk-quick-reference.md` with all 28 sensor function signatures. This serves as Claude Code's lookup table during instrumentation. Key categories:

**Identity Sensors** — `hiveloop.init()`, `hb.agent()`, `hb.get_agent()`

**State Sensors** — `heartbeat_payload`, `queue_snapshot()`, `queue_provider`, `agent.todo()`, `agent.scheduled()`, `agent.report_issue()`, `agent.resolve_issue()`

**Activity Sensors** — `agent.task()`, `agent.start_task()`, `@agent.track()`, `agent.track_context()`, `task.llm_call()`, `task.plan()`, `task.plan_step()`, `task.escalate()`, `task.request_approval()`, `task.approval_received()`, `task.retry()`, `task.event()`, `HiveBoardLogHandler`, `hiveloop.flush()`, `hiveloop.shutdown()`

For complete signatures with all parameters, see the `sdk-quick-reference.md` file in the skill package.

---

## Troubleshooting

**"Command not found" when running `/integrate-hiveloop`**
Ensure the skill files are in `.claude/commands/` relative to your project root, and that the main file is named `integrate-hiveloop.md`.

**API key not recognized**
HiveBoard API keys start with `hb_`. Check your key in the API Keys panel at [hiveboard.net](https://hiveboard.net).

**Skill doesn't detect my framework**
If your project uses an uncommon structure, the skill falls back to treating it as a custom loop. You can guide it by mentioning your framework in the prompt: `/integrate-hiveloop hb_live_key — this is a CrewAI project`.

**Want to undo the integration?**
The skill only adds code — it never deletes or modifies your existing logic. You can `git diff` to see exactly what was added and revert with `git checkout`.

---

## Additional Resources

- [HiveBoard Documentation](../docs/index.html) — Full platform documentation
- [Integration Guide](../docs/integration-guide.html) — Manual integration walkthrough
- [API Reference](../docs/user-manual.html#10-api-reference) — Complete API documentation
- [HiveLoop SDK](https://pypi.org/project/hiveloop/) — PyPI package page

---

## File Inventory

The skill download contains:

```
hiveloop-claude-code-skill/
├── integrate-hiveloop.md       Main skill file (place in .claude/commands/)
├── sdk-quick-reference.md      All 28 sensor function signatures
├── examples/
│   └── before-after.py         Complete before/after code example
└── README.md                   Quick-start instructions
```

---

*Built with The Hive Method · Part of the HiveBoard platform*

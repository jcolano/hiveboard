# HiveBoard Designer Brief: Making Agents Observable

**Audience:** Design team redesigning the HiveBoard dashboard
**Context:** loopCore is the first agentic framework using HiveBoard. This document tells the story of what agents do, what goes wrong, and what an operator needs to see -- grounded in real data flows from a production system.

---

## Part 1: What Are These Agents, Actually?

An agent is a program that talks to an LLM (like Claude), decides what to do, uses tools to do it, and repeats until the job is done. Think of it like an employee who reads instructions, makes phone calls, sends emails, searches databases, and reports back.

In loopCore, a small business might run 3-5 agents:

| Agent | Job | Runs how often |
|-------|-----|---------------|
| **main** | General assistant, answers DMs | On demand (human messages) |
| **sales** | Processes leads, updates CRM, sends follow-ups | Every 10 minutes (heartbeat) + webhooks |
| **support** | Handles tickets, routes escalations | Webhooks + every 15 minutes |

Each agent has:
- **Skills** -- markdown files that tell it how to behave (e.g., "when a new lead comes in, search CRM, score it, draft an email")
- **Tools** -- APIs it can call (CRM search, email send, web fetch, file read/write)
- **Memory** -- conversation history persisted across runs
- **A queue** -- incoming work (DMs, webhooks, scheduled heartbeats) sorted by priority

### The execution model

One agent processes one event at a time. The runtime loop (every 1 second) checks:
1. Are any heartbeat timers due? If yes, enqueue a LOW-priority event.
2. Are any scheduled tasks due? If yes, enqueue a NORMAL-priority event.
3. Is the agent idle and the queue non-empty? Pop the highest-priority event, run the agent.
4. Is the current run finished? Harvest the result, record history.

Human DMs are HIGH priority and jump the queue. Heartbeats are LOW and get dropped if the agent is busy.

---

## Part 2: The Lifecycle of a Single Run

This is the core of what HiveBoard needs to make visible. Here's what happens when the `sales` agent processes a webhook saying "New lead: Greenleaf Organics":

```
1. EVENT ARRIVES
   Source: webhook:hubspot
   Priority: NORMAL
   Message: "New inbound lead: Greenleaf Organics, jane@greenleaf.com"

2. PLANNING (optional)
   The agent breaks the task into steps:
   - Step 1: Search CRM for existing record
   - Step 2: Score lead based on criteria
   - Step 3: Draft follow-up email
   - Step 4: Update CRM with outcome

3. EXECUTION LOOP (turns)
   Turn 1: [Phase 1: Reasoning] "I should search CRM first"
            [Phase 2: Tool use]  crm_search(query="Greenleaf Organics")
            [Tool result]        { found: false }

   Turn 2: [Phase 1: Reasoning] "No existing record. Score based on info."
            [Phase 2: Tool use]  crm_write(action="create_contact", ...)
            [Tool result]        { id: "contact_8821", status: "created" }

   Turn 3: [Phase 1: Reasoning] "Contact created. Now draft email."
            [Phase 2: Tool use]  email_send(to="jane@greenleaf.com", ...)
            [Tool result]        { status: "sent", message_id: "msg_abc" }

   Turn 4: [Phase 1: Reasoning] "Email sent. Update CRM. Done."
            [Phase 2: Tool use]  crm_write(action="update", id="contact_8821", ...)
            [Tool result]        { status: "updated" }

   Turn 5: [Done] Final response: "Processed Greenleaf lead. Created contact,
            sent intro email, updated CRM pipeline."

4. POST-EXECUTION
   - Run saved to disk (result.json + transcript.md)
   - Session updated with new conversation turns
   - Event marked "completed" in history
   - Metrics updated: events_processed++, total_run_duration_ms += 14200
```

**Total: 5 turns, 4 tool calls, ~8,200 tokens, $0.04 cost, 14.2 seconds**

This is the happy path. The interesting story is what happens when it isn't happy.

---

## Part 3: What Goes Wrong (And How the Operator Finds Out)

These are the real scenarios that drive the need for observability. Each one represents a question an operator actually asks.

### Scenario A: "Why did the agent stop working?"

The `sales` agent hasn't processed anything in 2 hours. The operator opens the dashboard.

**What they need to see at a glance:**
- Agent status: Active (green) but queue depth is 12 and growing
- Currently processing: "Process lead: TechNova Inc" -- running for 47 minutes
- Last successful event: 2 hours ago

**What happened:** The CRM API credential expired. Every run attempts `crm_search`, gets a 403, retries twice, then the agent reports an issue and creates a retry TODO. But the next heartbeat picks up a new lead and the cycle repeats.

**The data HiveBoard has:**
- `agent.report_issue(summary="CRM API returning 403", severity="high", category="permissions")` -- the red issue badge
- `agent.track_context("crm_search")` with `success: false, error: "403 Forbidden"` -- repeated tool failures
- `agent.queue_snapshot(depth=12, ...)` -- the growing queue
- `agent.todo(action="created", summary="RETRY: Process lead TechNova Inc")` -- accumulating retry TODOs
- `task.llm_call(...)` -- the agent is spending tokens and money on runs that all fail

**What the operator does:** Sees the red issue badge, clicks it, reads "CRM API returning 403 for workspace queries", refreshes the CRM credential, dismisses the issue (which auto-creates a "retry after credential refresh" TODO), and the agent self-recovers on the next heartbeat.

**Design insight:** The single most important thing on the agent card is the issue badge. It's the difference between "agent looks fine" and "agent has been burning money for 2 hours failing the same way."

---

### Scenario B: "Why did this take so long?"

A run that should take 5 turns and 15 seconds took 18 turns and 3 minutes.

**What they need to see:**
- The plan progress bar: Steps 1-2 green, Step 3 red, Steps 4-5 gray
- Turn-by-turn timeline: 5 turns on Step 3 (email drafting), 3 reflection events, 1 replan
- Token cost: $0.18 instead of the usual $0.04

**What happened:** The agent tried to send an email but the email tool returned "recipient not found." The agent reflected ("adjust approach"), tried a different email format, failed again, reflected ("pivot"), replanned to skip email and notify via DM instead, succeeded on the new plan.

**The data HiveBoard has:**
- `task.plan(goal="Process lead", steps=[...])` -- the original plan
- `task.plan_step(step_index=2, action="started")` -- email step begins
- `task.plan_step(step_index=2, action="failed", summary="Email API: recipient not found")` -- step fails
- `task.escalate(...)` or reflection events showing the "adjust" and "pivot" decisions
- `task.plan(goal="Process lead (revised)", steps=[...], revision=1)` -- the replan
- `task.plan_step(step_index=3, action="completed")` -- DM notification succeeds
- Each `task.llm_call(...)` with cost, tokens, duration

**Design insight:** The plan progress bar is the fastest way to answer "where did it go wrong?" Green-green-red-gray-gray tells the whole story in one horizontal bar.

---

### Scenario C: "Is the agent actually doing anything useful?"

The operator checks in on Monday morning. 3 agents have been running all weekend.

**What they need to see (per agent):**
- Events processed: 142 (weekend total)
- Events failed: 3
- Total cost: $4.82
- Heartbeats fired: 288, skipped: 44 (good -- means agent was busy, not that heartbeats broke)
- Queue: empty (caught up)
- Active issues: 0
- Active TODOs: 0

**The data HiveBoard has:**
- Metrics counters (from the agent heartbeat)
- `agent.queue_snapshot(depth=0)` -- caught up
- `agent.scheduled(items=[...])` -- schedule is as expected
- Cost summation from all `task.llm_call()` events over the weekend
- Event history with success/failure breakdown

**Design insight:** The "at a glance" view needs to answer "is this agent healthy?" in under 2 seconds. The key signals: queue depth (is it keeping up?), failure rate (is it working correctly?), cost (is it within budget?), active issues (is anything broken?).

---

### Scenario D: "Something needs my approval"

The `support` agent received a customer request that requires a $500 account credit. The agent's skill says credits above $200 need human approval.

**What they need to see:**
- Agent card: WAITING badge (amber)
- Pending approval card: "Approval needed: Account credit $500 for customer XYZ"
- Context: customer history, reason for credit, agent's recommendation

**The data HiveBoard has:**
- `agent.request_approval("Approval needed: Account credit $500 for customer XYZ")` -- the WAITING state
- After operator clicks Approve: `agent.approval_received("Credit approved by operator", decision="approved")`
- The agent then continues processing

**Design insight:** WAITING is the most actionable state for a human operator. The approval card should be impossible to miss -- it's the one thing that requires immediate human action. Consider: notification sound, top-of-page banner, or persistent badge that doesn't go away.

---

## Part 4: The Three Time Horizons

An operator thinks about agents in three time frames. Our loopCore UI already models this with three sub-tabs, and HiveBoard should do the same:

### Right Now (Runtime / Pipeline)

"What is happening this instant?"

| Data point | Why it matters | loopCore UI | HiveBoard equivalent |
|------------|---------------|-------------|---------------------|
| Agent active/stopped | Is it even running? | Green/gray status bar | Agent card status |
| Currently processing | What's it doing? | Spinner + event card | Timeline "in progress" node |
| Queue depth + items | Is work piling up? | Queue table | Queue badge (Q:4) + Pipeline tab |
| Pending approvals | Does it need me? | Approve/Drop cards | WAITING badge + approval cards |
| Active issues | Is something broken? | Severity-colored issue list | Red issue badge + Pipeline issues |
| Active TODOs | What follow-up work exists? | Checkbox list | Pipeline TODOs |
| Next heartbeat | When will it check again? | Countdown timer | Schedule section |
| Metrics | Processing health | 6-counter panel | Stats Ribbon |

**Auto-refresh:** loopCore refreshes every 3 seconds. HiveBoard should too -- this view is for live monitoring.

### Recent Past (Runs / Timeline)

"What happened in the last run, or today's runs?"

| Data point | Why it matters | loopCore UI | HiveBoard equivalent |
|------------|---------------|-------------|---------------------|
| Run status | Did it succeed? | Status badge (completed/error) | Timeline task node color |
| Turn count | Was it efficient? | Numeric column | Timeline node count |
| Duration | Was it fast? | Milliseconds column | Duration label |
| Token count | How much did it cost? | Numeric column | Cost label on task node |
| Tools called | What did it do? | List in run detail modal | Tool nodes in Timeline |
| Plan progress | Where did it fail? | (not in loopCore UI) | Plan progress bar |
| Execution trace | Step-by-step decisions | JSON in run detail modal | Expandable Timeline nodes |
| Transcript | Full conversation | Plain text modal | (detail view) |

**Access pattern:** Operator clicks a specific run/task to drill down. This is investigative, not monitoring.

### Historical (Sessions / Activity Stream)

"What has this agent been doing over days/weeks?"

| Data point | Why it matters | loopCore UI | HiveBoard equivalent |
|------------|---------------|-------------|---------------------|
| Session list | Conversation threads | Table with message count | Activity Stream |
| Session status | Active/completed/paused | Badge column | Filter by status |
| Cost over time | Budget tracking | (not in loopCore UI) | Cost graph |
| Error rate trend | Getting better or worse? | (not in loopCore UI) | Trend sparkline |
| Issue history | Recurring problems? | (not in loopCore UI) | Issue timeline |

---

## Part 5: What loopCore's UI Gets Right (Steal These Ideas)

Having built observability into loopCore before HiveBoard existed, we learned some things:

### 1. The Runtime tab auto-refreshes. Everything else doesn't.

This is the right split. Live monitoring (Runtime) needs constant updates. Historical views (Runs, Sessions) are static lookups. Don't waste bandwidth refreshing a runs table nobody is watching.

### 2. Issues and TODOs are first-class citizens, not buried in logs.

In loopCore's Runtime tab, issues get their own section with severity-colored badges, occurrence counts, and one-click dismiss. TODOs get a checkbox list. These are not afterthoughts -- they're the primary way an operator understands agent health.

Before we built this, issues were buried in log files. An agent could fail the same way 50 times and nobody would notice until a customer complained.

### 3. Pending approvals are cards, not table rows.

Each pending approval in loopCore renders as a card with title, priority badge, message preview, skill badge, creator info, and two big buttons: Approve (green) and Drop (red). This is the right pattern -- approvals are urgent, actionable, and need context.

### 4. Event history tells you what happened; the queue tells you what's coming.

loopCore separates these into two sections. The queue shows what's waiting (with priority badges). The event history shows what already ran (with status, duration, and response preview). Together they answer "is the agent keeping up?"

### 5. Heartbeat history is surprisingly useful.

loopCore shows the last 20 heartbeats with: status, timestamp, skills triggered, turn count, token count, and summary lines. This is how operators detect drift -- "the heartbeat used to trigger CRM sync + email check, but now it's only doing CRM sync. What changed?"

### 6. The "Currently Processing" card with elapsed time creates urgency.

When a run has been going for 3 minutes (and it usually takes 15 seconds), the elapsed time counter makes it obvious something is wrong. A spinner alone doesn't communicate this.

---

## Part 6: What HiveBoard Can Do That loopCore's UI Can't

HiveBoard receives structured telemetry from the SDK. This unlocks views that a framework's built-in UI can't easily build:

### 1. Cross-agent visibility

loopCore shows one agent at a time (dropdown selector). HiveBoard can show all agents simultaneously -- a grid of cards showing which are healthy, which are stuck, which need approval.

### 2. Cost aggregation and trends

loopCore tracks tokens per run but doesn't aggregate. HiveBoard receives `cost=` on every `task.llm_call()` and can show: cost per agent per day, cost per task type, cost per model, total spend this month.

### 3. The plan progress bar

loopCore's plan data lives in a JSON blob inside the run detail modal. HiveBoard receives `task.plan()` and `task.plan_step()` as structured events and can render a visual progress bar above the timeline. This doesn't exist in loopCore's UI at all.

### 4. Timeline with branching

loopCore's execution trace is a flat list of events. HiveBoard can render it as a visual timeline with branching: main track (tool calls), side branches (reflections, replans), and nodes for escalations, approvals, and retries.

### 5. The "Pipeline" view

loopCore spreads operational data across Issues, TODOs, Queue, and Pending Approvals sections. HiveBoard can unify these into a single "Pipeline" view that answers: "What does this agent's world look like?" -- open issues, pending TODOs, queued work, scheduled work, all in one place.

### 6. Stuck detection

HiveBoard knows the `stuck_threshold` (configured at agent registration). If a task runs longer than this threshold, it can automatically surface a warning without the operator having to notice the elapsed time counter.

---

## Part 7: The Data Flowing Through HiveBoard Right Now

Here's everything the loopCore integration currently sends. Every item below is a real SDK call wired into production code.

### Agent-Level (appears on agent card + Pipeline tab)

| SDK Method | When it fires | What it sends |
|------------|--------------|---------------|
| `hb.agent()` | Agent created | agent_id, type (role), version (model), framework, heartbeat_interval, stuck_threshold |
| `agent.queue_snapshot()` | Every heartbeat (30s) | depth, oldest_age_seconds, items[{id, priority, source, summary, queued_at}], processing{id, summary, started_at} |
| `agent.report_issue()` | Agent tool `report_issue` fires | summary, severity, category, issue_id, context, occurrence_count |
| `agent.resolve_issue()` | Human dismisses issue in admin | summary, issue_id |
| `agent.todo()` | TODO add/complete/remove via tool, or auto-created from failed runs | todo_id, action (created/completed/dismissed), summary, priority, source, context |
| `agent.scheduled()` | Agent starts | items[{id, name, interval, enabled, last_status}] |
| `agent.track_context()` | Every tool execution | tool_name, args (truncated), result_preview, success, error, duration (automatic) |

### Task-Level (appears on task Timeline)

| SDK Method | When it fires | What it sends |
|------------|--------------|---------------|
| `agent.task()` | Agent run begins/ends | task_id, project, type (heartbeat/webhook/human/task) |
| `task.llm_call()` | Every LLM API call (6 sites) | phase, model, tokens_in, tokens_out, cost, duration_ms, prompt_preview*, response_preview* |
| `task.plan()` | Plan created or revised | goal, steps[], revision |
| `task.plan_step()` | Step started/completed/failed | step_index, action, summary, turns |
| `task.escalate()` | Reflection returns "escalate" | summary, assigned_to |
| `task.retry()` | Failed run creates retry TODO | summary, attempt |
| `agent.request_approval()` | Event enters pending_approval | summary, approver |
| `agent.approval_received()` | Event approved or dropped | summary, approved_by, decision |

*prompt_preview and response_preview are gated behind a config flag (`hiveloop_log_prompts`), off by default, and truncated to 300 characters when enabled.*

---

## Part 8: Questions the Dashboard Must Answer

Organized by urgency, these are the questions a real operator asks. Each one should be answerable in under 5 seconds from the dashboard.

### Immediate (glance at the screen)

1. **"Are my agents running?"** -- Status indicators on each agent card
2. **"Does anything need my attention?"** -- Issue badges, WAITING badges, queue depth warnings
3. **"Is anything stuck?"** -- Elapsed time on currently-processing, stuck threshold warnings

### Investigative (click into an agent)

4. **"What is this agent doing right now?"** -- Currently processing card, queue contents
5. **"What went wrong with this run?"** -- Plan progress bar (green-green-red), failed tool in timeline
6. **"Why is the queue growing?"** -- Queue depth trend, processing duration, failure rate

### Strategic (weekly review)

7. **"How much are agents costing me?"** -- Cost per agent, cost per task type, cost trend
8. **"Which agent fails most often?"** -- Failure rate comparison across agents
9. **"Are agents getting better over time?"** -- Success rate trend, average turns per task, cost per task trend
10. **"What recurring issues keep coming back?"** -- Issue history with occurrence counts

---

## Part 9: A Worked Example for the Designers

Here's a concrete scenario to design against. Walk through this as you lay out components.

**Setup:** 3 agents running. Monday morning, 9:15 AM. Operator opens HiveBoard.

**What they see (The Hive / overview):**

```
[ main ]        Active  |  Q:0  |  Idle
[ sales ]       Active  |  Q:3  |  Processing "New lead: Acme Corp"  |  !! 1 issue
[ support ]     WAITING |  Q:1  |  Pending: "Credit approval: $450"
```

**Operator's eye goes to:** The red issue badge on `sales` and the amber WAITING on `support`.

**They click `support`:**
- See the approval card: "Account credit $450 for customer BlueStar. Agent recommends approval based on 3-year relationship and recent service issue."
- Click Approve.
- Badge changes from WAITING to PROCESSING. Agent continues.

**They click `sales`:**
- See the issue: "CRM API returning 403 for workspace queries" (severity: high, occurrences: 8, first seen: 2 hours ago)
- See the queue: 3 items backed up (all webhook leads)
- See the TODO list: 5 retry items, all "RETRY: Process lead ..."
- Realize: CRM credentials expired over the weekend. All lead processing has been failing silently.
- Fix the credential externally, dismiss the issue (with TODO: "Retry failed leads after credential refresh").
- Agent picks up the TODO on next heartbeat, processes the backlog.

**They click into `sales`'s most recent run (Timeline):**
- Plan bar: [Search CRM: RED] [Score lead: GRAY] [Send email: GRAY]
- Timeline: Phase 1 reasoning -> crm_search tool (red, 403 error) -> reflection (adjust) -> crm_search retry (red, 403) -> report_issue tool -> retry TODO created -> task completed with error
- LLM costs: 3 turns, $0.02 wasted on a run that couldn't succeed

**10 minutes later, they refresh:**
- `sales`: Q:0, no issues, 5 items processed in the last 10 minutes. All green.
- `support`: Q:0, idle. Credit was applied successfully.

**Total time the operator spent:** ~3 minutes to identify two problems, fix them, and verify recovery. Without HiveBoard, the CRM issue would have gone unnoticed until a customer complained about not receiving a follow-up email.

---

## Appendix: Terminology

| Term | Meaning |
|------|---------|
| **Agent** | An LLM-powered program that autonomously performs tasks using tools |
| **Turn** | One LLM call + one tool execution (the atomic unit of agent work) |
| **Run** | A complete execution from event arrival to final response (contains many turns) |
| **Task** | A tracked unit of work in HiveBoard (maps 1:1 to a run in loopCore) |
| **Session** | Persistent conversation history that survives across runs |
| **Heartbeat** | Periodic timer that triggers the agent to check for work |
| **Skill** | Markdown instructions that tell the agent how to behave |
| **Tool** | An API the agent can call (CRM search, email send, file read, etc.) |
| **Event** | A queued work item: a DM, webhook, heartbeat, or scheduled task |
| **Phase 1** | The "reasoning" LLM call where the agent decides what to do |
| **Phase 2** | The "tool use" LLM call where the agent generates tool parameters |
| **Reflection** | The agent evaluating its own progress (continue/adjust/pivot/escalate) |
| **Plan** | An ordered list of steps the agent creates before executing a complex task |
| **Issue** | A persistent problem the agent reports (bad credentials, API errors, etc.) |
| **TODO** | A follow-up work item the agent creates for itself (retry failed task, etc.) |
| **Escalation** | The agent deciding it cannot handle something and handing it to a human |
| **Pipeline** | HiveBoard's unified view of an agent's operational state (issues + TODOs + queue + schedule) |

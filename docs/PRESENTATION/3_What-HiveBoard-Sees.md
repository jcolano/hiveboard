# What HiveBoard Sees

### The Questions Your Agents Can Finally Answer

---

> *"Nobody needs observability on demo day. Everyone needs it on day 30 when the agent silently stopped working and nobody noticed for 6 hours."*

---

## Why This Document Exists

You've read **THE JOURNEY** — the story of how HiveBoard was born from real pain. You've read **The Hive Method** — the development process that built it in 48 hours. This document answers the question that follows both: **what does HiveBoard actually do for you on a Tuesday morning when your agents are running?**

The answer isn't a feature list. It's a catalog of **questions you can finally answer** — organized by the moment you're in when you ask them.

---

## The Four Moments

Every interaction with an observability tool happens in one of four moments. Each has a different urgency, a different depth of investigation, and a different set of questions. HiveBoard is designed to serve all four — from a 2-second glance to a 30-minute strategic review.

| Moment | When | How long you have | What you need |
|---|---|---|---|
| **The Glance** | Walking past a screen. Checking between meetings. | 2 seconds | A yes/no answer: is everything OK? |
| **The Investigation** | Something's wrong. An alert fired. A customer reported an issue. | 2–5 minutes | The full story of what happened, step by step |
| **The Optimization** | Nothing's broken, but you suspect things could be better. | 10–15 minutes | Patterns, costs, inefficiencies, silent degradation |
| **The Review** | End of week. Before a board meeting. After a deploy. | 20–30 minutes | Trends, comparisons, proof that things are improving |

---

## Moment 1: The Glance

**The scenario:** You walk past the wall monitor. Or you tab over to HiveBoard between Slack messages. You have two seconds. You need to know: **is everything OK?**

### Questions you can answer in under 2 seconds

**"Are my agents running?"**
Every agent has a heartbeat indicator — a green, amber, or red dot with a timestamp. Green means alive. Red means the heartbeat is stale. You don't need to click anything; the dots are visible on every agent card in The Hive panel.

**"Does anything need my attention?"**
The Hive header shows an attention badge — a red pulsing pill that reads "2 ⚠" if two agents need attention. If the badge isn't there, nothing needs you. This is the single most important pixel on the screen.

**"Is anything stuck?"**
The Stats Ribbon at the top of Mission Control shows a "Stuck" counter. If it reads 0, nothing is stuck. If it reads anything else, a stuck agent's card will be glowing red in The Hive, sorted to the top — because The Hive sorts by urgency, not alphabetically. Stuck and error agents float up. Healthy idle agents sink down.

**"Is work flowing?"**
Four mini-charts below the Stats Ribbon show trends for throughput, success rate, errors, and LLM cost per task. If the throughput bars are moving and the error bars are flat, work is flowing. No numbers to read — just shapes.

**"Is anything happening right now?"**
The Activity Stream on the right shows a green pulsing "Live" badge and a reverse-chronological feed of events. If events are appearing, agents are working. If the stream has gone silent, something may have stopped.

### The 2-Second Test

This is the design principle behind The Glance. Put the dashboard on a screen by the door. Walk past it. If you can tell whether things are healthy or on fire in the time it takes to walk by — the dashboard passes. HiveBoard was redesigned specifically to pass this test after the first version failed it with real data.

---

## Moment 2: The Investigation

**The scenario:** Something's wrong. An agent is stuck. A task failed. A customer says "I never got that email." You need to find out what happened.

### Agent-level questions

**"What is this agent doing right now?"**
Click the agent card. The current task link takes you straight to its live timeline. If the agent is idle, the card says so. If it's processing, you see the task ID and elapsed time. If elapsed time is abnormally high, the card tells you — it knows the stuck threshold.

**"Is this agent's heartbeat healthy?"**
The heartbeat indicator shows three states: green (recent), amber (drifting), red (stale). Below it, a sparkline chart shows heartbeat activity over the past hour. A healthy agent has steady bars. A dying agent shows bars trailing off to nothing.

**"Does this agent have pending work nobody's looking at?"**
The agent card shows queue depth as a badge: "Q:4" means four items waiting. If the badge turns amber (depth exceeds threshold), work is backing up. Click into the Pipeline tab to see the actual queue contents — each item with its priority, source, age, and summary.

**"Has this agent reported its own problems?"**
Agents can self-report issues via `agent.report_issue()`. These show as a red dot and "1 issue" text on the agent card. The Pipeline tab shows the full issue table: summary, severity, category, occurrence count, and debugging context. An issue that says "CRM API returning 403" with "×8 occurrences" tells you the credential expired 8 requests ago.

### Task-level questions

**"What steps did this task take?"**
The Timeline renders every event in the task as a visual story — nodes connected left to right (or in a tree for nested actions). Each node is color-coded: blue for actions, purple for LLM calls, red for failures, amber for escalations, green for completion. You can read the story without clicking a single node.

**"What was the plan, and where did it go wrong?"**
If the agent created a plan (`task.plan()`), a plan progress bar appears above the timeline. Each step is a segment: green (completed), blue (in progress), red (failed), gray (pending). A bar showing green-green-red-gray tells you: the third step failed.

**"Which tool failed?"**
Failed actions show as red nodes on the timeline. Click the node to see the tool name, the arguments it received, the error message, and the duration. If the agent retried, the timeline shows branching retry nodes — each attempt visible.

**"Which LLM was called, and what did it see?"**
LLM calls render as purple nodes with a model badge above them (e.g., "claude-sonnet"). Click a node to see: model, tokens in, tokens out, cost, duration, and — if prompt logging is enabled — a preview of the prompt and response. This is the view that turned $40/hour into $8/hour.

**"How long did each step take?"**
Duration labels appear on the connecting lines between timeline nodes. A task that took 14 seconds total might show: 0.8s for CRM lookup, 1.2s for the first LLM call, 2.1s for enrichment, 0.4s for scoring. You can see immediately where the time went.

**"Was it escalated? Did it need human approval?"**
Escalation events render as amber nodes. Approval requests show as distinct nodes with the approver's name, the reason, and the resolution. The agent's status badge changes to "WAITING" during the approval window — visible fleet-wide.

**"Can I share this investigation?"**
Every timeline has a **permalink**. Someone asks "why did task X fail?" in Slack — paste the link. They see the full story in 15 seconds. No reproduction steps. No "can you send me the logs." Just the link.

### The Monday Morning Scenario

Here's a concrete example. You open HiveBoard on Monday at 9:15 AM and see:

```
[ main ]      IDLE      | Q:0  | Green heartbeat
[ sales ]     ERROR     | Q:8  | Red heartbeat  | ⚠ 1 issue
[ support ]   WAITING   | Q:1  | Green heartbeat
```

Your eye goes to the red glow on `sales` and the amber WAITING on `support`.

**You click `support`:** An approval card shows: "Account credit $450 for customer BlueStar. Agent recommends approval based on 3-year relationship and recent service issue." You click Approve. The badge changes to PROCESSING. Done — 10 seconds.

**You click `sales`:** The Pipeline tab shows an issue: "CRM API returning 403 for workspace queries" — severity: high, occurrences: 8, first seen: 2 hours ago. The queue shows 8 leads backed up. The TODO list shows 5 retry items. **The CRM credentials expired over the weekend. All lead processing has been silently failing for hours.** Without HiveBoard, you'd have found out when a customer complained — maybe days later.

---

## Moment 3: The Optimization

**The scenario:** Nothing is on fire. But you have 15 minutes and a suspicion that things could be better. This is where HiveBoard's proactive value lives — the insights you wouldn't have discovered without visibility.

### Cost optimization

**"How much are my agents costing me?"**
The Cost Explorer shows total LLM spend for any time range, broken down by model and by agent. Two side-by-side tables: Cost by Model and Cost by Agent. Each row shows name, total cost, call count, and percentage of total.

**"Am I using expensive models where cheap ones would work?"**
Look at the Cost by Model table. If `claude-opus` is your biggest spend but most calls are simple classification or routing, you're overpaying. The timeline makes this visible: click into any task and see which model was used for which step. When you see Opus called for "classify_intent" — a task Haiku handles at 1/10th the cost — you've found money.

**"Why did costs spike this week?"**
The Cost Explorer includes a timeseries chart stacked by model. A spike on Wednesday at 2 PM is immediately visible. Click into tasks from that period and inspect the LLM call nodes — did prompt sizes increase? Did a config change switch models? Did retry behavior spike? The timeline narrows the cause in minutes.

**"Is there prompt bloat?"**
Look at any LLM call node: if tokens-in is 18,000 and tokens-out is 200, the prompt is doing too much work. High tokens-in with low tokens-out is the signature of prompt bloat — redundant system instructions, duplicated context across turns, verbose few-shot examples. This single pattern, once visible, is the fastest path to cost reduction.

**"Are different agents doing similar work at different costs?"**
The Cost by Agent table shows this directly. If `sales-v2` costs $0.12 per task and `sales-v1` costs $0.06 for the same task type, the newer version is sending bigger prompts or using a pricier model. Compare their timelines side by side to find the difference.

### Invisible failure detection

**"Are tasks being silently dropped?"**
This is the most dangerous failure in agent systems — the task that never completes and never errors. It just disappears. The queue snapshot shows items aging: if an item has been in the queue for 45 minutes and the average processing time is 15 seconds, something is wrong. The "oldest item age" metric surfaces this automatically.

**"Is the queue growing while the agent reports idle?"**
The agent card shows this contradiction directly: status badge reads "IDLE" while the queue badge reads "Q:8" in amber. The agent thinks it's done; the work disagrees. This is a scheduling bug, a polling interval problem, or a silent crash recovery that lost the queue state.

**"Are credentials failing silently?"**
The issue reporting system surfaces this: "CRM API returning 403" with an occurrence count that keeps climbing. Even without self-reporting, the timeline shows the pattern: the same action failing and retrying repeatedly — each attempt visible as a branching node.

**"Is the heartbeat doing less than it used to?"**
If a heartbeat used to trigger CRM sync + email check but now only triggers CRM sync, the heartbeat payload data will show it. HiveBoard renders heartbeat payload summaries so you can catch behavioral drift — not just presence/absence, but what the agent is actually doing on each cycle.

### Operational health signals

**"Are human approvals backing up?"**
The Stats Ribbon shows a "Waiting" count. If it's climbing, humans aren't reviewing fast enough. The Activity Stream with the "human" filter shows every approval request and response — you can see the approval queue depth and response time at a glance.

**"Which action within a plan consistently fails?"**
Across multiple task timelines, you might notice that step 3 — "enrich_company" — fails 40% of the time. The timeline makes this pattern visible because every failed action is a red node in the same position. Without timelines, you'd see "task failed" in logs. With timelines, you see *where* in the plan it fails.

**"Is the same issue recurring without resolution?"**
The Pipeline tab's Issues section shows occurrence counts. An issue at "×50 occurrences" that nobody has addressed means the agent has been flagging the same problem fifty times. The count is the signal; the issue detail is the diagnosis.

---

## Moment 4: The Review

**The scenario:** End of week. Before a stakeholder meeting. After a major deploy. You need to know: **are things getting better?**

### Performance trends

**"Is my success rate improving?"**
The Stats Ribbon shows current success rate (1-hour window). The mini-chart shows the trend over time. A line going up means deployments and optimizations are working. A line dropping after a deploy means something broke.

**"Are tasks getting faster or slower?"**
The Stats Ribbon shows average duration. Compare this week's average to last week's. The Time Breakdown feature shows where time goes within tasks: how much is LLM processing, how much is tool execution, how much is waiting. If LLM time increased after a model switch, you know why tasks slowed down.

**"Which agent fails most often?"**
Compare error rates across agents in The Hive. Each card shows its own health metrics. An agent with a sparkline that's trending red while others are steady blue has a localized problem.

**"Are agents getting better after deploys?"**
Deploy a new version. Compare task timelines before and after. Key metrics to compare: success rate, average duration, cost per task, average turns per task. If cost-per-task dropped after you optimized prompts, the optimization worked. If error rate increased after a config change, roll back.

### Cost accountability

**"What's our total agent infrastructure cost?"**
Cost Explorer, full time range. One number. Break it down by model to see where the money goes. Break it down by agent to see who's spending it.

**"Is cost per task trending up or down?"**
The LLM Cost/Task mini-chart answers this visually. A rising trend means prompts are growing, models are getting more expensive, or retry behavior is increasing. A flat trend means costs are stable. A dropping trend means your optimizations are working.

**"Can I prove ROI on agent observability?"**
This is the $40 → $8 question. Pull up cost data before and after you started using HiveBoard. The delta is your ROI. In the HiveBoard creator's own case: visibility into prompts revealed bloat (18,000 tokens where 5,000 sufficed), and prompt optimization reduced costs by 80%. No model switch. No architecture change. Just visibility followed by informed action.

### Fleet-level insights

**"How many agents are in production?"**
Stats Ribbon: Total Agents. Each one visible in The Hive with its own card, status, and health indicators.

**"What's the overall health of the fleet?"**
If all heartbeat dots are green, the stuck counter is zero, the error counter is low, and the success rate is above your baseline — the fleet is healthy. This entire assessment takes 2 seconds. That's the point.

**"Are we ready to scale?"**
If current agents are running at high success rates with stable costs and manageable queue depths, adding more agents of the same type is safe. If queues are growing, error rates are climbing, or costs are spiking — you have a capacity or reliability problem to solve first. HiveBoard shows both signals on the same screen.

---

## What You See at Each Instrumentation Layer

HiveBoard's value grows with instrumentation depth. Here's what each layer unlocks:

### Layer 0 — Presence (3 lines of code)

```python
import hiveloop
hb = hiveloop.init(api_key="hb_xxx")
agent = hb.agent("lead-qualifier", type="sales")
```

**Questions you can answer:**
- Are my agents alive?
- How many agents are running?
- Is anything stuck?
- When did each agent come online?

**What you see:** Agent cards with names, types, status badges, heartbeat indicators, sparklines.

### Layer 1 — Timelines (add decorators)

```python
@agent.track("fetch_crm_data")
def fetch_crm(lead_id):
    return crm_client.get_lead(lead_id)

with agent.task("task_lead-4821") as task:
    lead = fetch_crm("lead-4821")
```

**New questions you can answer:**
- What task is each agent working on right now?
- How long do tasks take?
- What's my success rate?
- When a task fails, which action was it on?
- What's my throughput over time?

**What you see:** Task Table populates. Timeline renders with action nodes and timing. Stats Ribbon lights up with processing counts, success rates, duration averages.

### Layer 2 — Full Story (add rich events)

```python
task.llm_call(name="lead_scoring", model="claude-sonnet-4-20250514",
              tokens_in=1800, tokens_out=250, cost=0.004)
task.plan(goal="Process lead", steps=["Fetch CRM", "Score", "Route"])
agent.report_issue(summary="CRM API returning 403", severity="high")
agent.queue_snapshot(items=[...], depth=4)
```

**New questions you can answer:**
- How much is each LLM call costing me?
- Which model is the most expensive?
- What was the agent's reasoning at each step?
- Where in the plan did it fail?
- How long has the work queue been growing?
- What issues has the agent self-reported?
- Is the human approval queue backed up?

**What you see:** Cost Explorer fully functional. Timeline enriched with LLM nodes, plan progress bars, escalation nodes. Pipeline tab shows issues, queues, TODOs, scheduled work. Agent cards show queue badges and issue indicators.

---

## The Questions Nobody Else Answers

These are the questions that make HiveBoard different — the ones that existing tools (LangSmith, Langfuse, Datadog) structurally cannot answer because they don't model agents as workers:

| Question | Why existing tools miss it |
|---|---|
| "Is my agent stuck?" | No heartbeat concept. No stuck detection threshold. No liveness beyond "last API call." |
| "What's in the work queue?" | No intent pipeline. They see what happened, not what's waiting to happen. |
| "Did the agent drop a task silently?" | No queue-to-completion tracking. No "expected work vs. completed work" comparison. |
| "Is this agent waiting for human approval?" | No approval workflow concept. It's just another state to them, if they model state at all. |
| "What did the agent plan to do vs. what it actually did?" | No plan-step tracking. No planned-vs-actual comparison. |
| "How many times has this agent reported the same issue?" | No self-reporting concept. Agent issues are just log entries with no recurrence tracking. |
| "Is the heartbeat still doing what it used to?" | No payload-aware heartbeat analysis. Heartbeat is binary: alive or dead. |

These aren't feature gaps that will be patched with a version update. They're **architectural differences**. HiveBoard models agents as workers with tasks, plans, queues, issues, and heartbeats. Other tools model agents as sequences of LLM calls. The questions you can ask are determined by the model, not the UI.

---

## One Real Timeline, Annotated

Here's a complete task timeline for a "lead-qualifier" agent processing a sales lead. Every node is something you can see and click on the HiveBoard dashboard:

```
TIMELINE: task_lead-4821 | agent: lead-qualifier | status: completed | 12.4s | $0.008

[task_started]                    0.0s    "New lead processing task received"
    │
[action: fetch_crm_data]          0.4s    duration: 1.8s → success
    │                                      Tool: crm_client.get_lead("lead-4821")
    │
[llm_call: phase1_reasoning]      2.1s    claude-sonnet | 1,500→200 tokens | $0.003
    │                                      "Evaluate this lead against our ICP..."
    │
[action: enrich_company]          3.4s    duration: 2.1s → success
    │                                      Tool: enrichment_api.lookup("Acme Corp")
    │
[llm_call: lead_scoring]          5.8s    claude-sonnet | 1,800→250 tokens | $0.004
    │                                      "Score this lead: company data + enrichment..."
    │
[action: score_lead]              8.2s    duration: 0.3s → success
    │                                      Result: score=72, threshold=80
    │
[llm_call: route_decision]        8.9s    claude-haiku | 1,200→150 tokens | $0.001
    │                                      "Route: score below threshold, send to nurture"
    │
[action: route_lead]             10.1s    duration: 1.9s → success
    │                                      Destination: nurture-pipeline
    │
[task_completed]                 12.4s    "Lead scored 72, routed to nurture pipeline"

COST BREAKDOWN: 3 LLM calls | 4,500 tokens in | 600 tokens out | $0.008 total
TIME BREAKDOWN: LLM 5.2s (42%) | Tool execution 6.1s (49%) | Overhead 1.1s (9%)
```

Every node is clickable. Every timeline has a permalink. Investigation time for "why was this lead routed to nurture instead of sales?" — 15 seconds.

---

## Summary: The Complete Question Catalog

### The Glance (2 seconds)
1. Are my agents running?
2. Does anything need my attention?
3. Is anything stuck?
4. Is work flowing?
5. Is anything happening right now?

### The Investigation (2–5 minutes)
6. What is this agent doing right now?
7. Is the heartbeat healthy?
8. Does this agent have pending work?
9. Has the agent reported its own problems?
10. What steps did this task take?
11. What was the plan, and where did it fail?
12. Which tool failed?
13. Which LLM was called, and what did it see?
14. How long did each step take?
15. Was it escalated? Did it need approval?
16. Can I share this investigation?

### The Optimization (10–15 minutes)
17. How much are my agents costing me?
18. Am I using expensive models for cheap tasks?
19. Why did costs spike?
20. Is there prompt bloat?
21. Are similar agents working at different costs?
22. Are tasks being silently dropped?
23. Is the queue growing while the agent reports idle?
24. Are credentials failing silently?
25. Is the heartbeat doing less than it used to?
26. Are human approvals backing up?
27. Which action consistently fails?
28. Is the same issue recurring without resolution?

### The Review (20–30 minutes)
29. Is my success rate improving?
30. Are tasks getting faster or slower?
31. Which agent fails most often?
32. Are agents getting better after deploys?
33. What's our total agent infrastructure cost?
34. Is cost per task trending up or down?
35. Can I prove ROI on agent observability?
36. How many agents are in production?
37. What's the overall health of the fleet?
38. Are we ready to scale?

**38 questions. All answerable from one dashboard. Most in under 5 seconds.**

---

## Who This Is For

**If you deploy AI agents into production** — whether you built them with LangChain, CrewAI, AutoGen, or plain Python — and you've ever asked "why didn't it send the email?" or "how much is this costing me?" or "is it still running?" — this is for you.

**If you're a solo developer** running one or two agents, HiveBoard gives you the wall-monitor view that tells you everything is OK without checking logs.

**If you're a team lead** managing a fleet of agents across sales, support, and operations, HiveBoard gives you the fleet-level health view, the cost accountability, and the investigation tools to debug failures in seconds instead of hours.

**If you're an executive** who approved the budget for an agent initiative, HiveBoard gives you the proof: success rates, cost trends, and the before-and-after data that shows whether the investment is working.

You don't need to instrument everything on day one. Start with 3 lines of code. See your agents light up. Then decide how deep you want to go.

---

> *"Your agents are working. Are they healthy? Now you can know."*

---

*This document is the third part of a trilogy:*
*1. **THE JOURNEY** — why HiveBoard exists*
*2. **The Hive Method** — how it was built*
*3. **What HiveBoard Sees** — what it does for you*

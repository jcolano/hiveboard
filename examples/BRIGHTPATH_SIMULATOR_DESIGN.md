# BrightPath Solutions — Ultimate Simulator Design

> **Purpose:** A comprehensive, always-running business simulator that generates realistic
> multi-agent telemetry through the HiveLoop SDK, exercising every layer (0–4) of the
> Integration Guide against a live HiveBoard instance.

---

## The Business

**BrightPath Solutions** is a small AI-powered digital agency that helps B2B companies
acquire customers, run marketing campaigns, and retain them through excellent support.
The entire operation is run by 6 AI agents coordinated through a shared work queue
and an internal communication platform called **LoopColony** (Slack for humans + agents).

---

## The Agents

| Agent ID | Name | Type | Version | Framework | Project(s) | Role |
|----------|------|------|---------|-----------|------------|------|
| `scout` | Scout | `sales` | `2.3.0` | `brightpath` | `lead-gen` | Lead hunter — finds, scores, and qualifies new inbound leads |
| `closer` | Closer | `sales` | `1.8.0` | `brightpath` | `sales-pipeline` | Sales rep — follows up qualified leads, sends proposals, closes deals |
| `harper` | Harper | `support` | `3.1.0` | `brightpath` | `customer-success` | Support rep — triages tickets, drafts responses, resolves or escalates |
| `campaigner` | Campaigner | `marketing` | `1.4.0` | `brightpath` | `marketing-ops` | Marketing ops — launches campaigns, monitors metrics, A/B tests, reports ROI |
| `archivist` | Archivist | `operations` | `2.0.1` | `brightpath` | `back-office` | Data & CRM — syncs CRM, generates reports, data hygiene, compliance |
| `dispatch` | Dispatch | `coordinator` | `1.2.0` | `brightpath` | all | Coordinator — routes work between agents, posts to LoopColony, assigns tasks |

---

## The Projects

| Slug | Name | Primary Agents | Description |
|------|------|----------------|-------------|
| `lead-gen` | Lead Generation | scout, dispatch | Inbound lead capture and qualification |
| `sales-pipeline` | Sales Pipeline | closer, dispatch | Follow-up, proposals, deal closing |
| `customer-success` | Customer Success | harper, dispatch | Support tickets and customer retention |
| `marketing-ops` | Marketing Ops | campaigner, dispatch | Campaigns, A/B tests, ROI tracking |
| `back-office` | Back Office | archivist, dispatch | CRM sync, reports, data ops |

---

## Realistic Data Pools

### Customers / Companies

```
TechNova Inc          — SaaS, 150 employees, Series B
Meridian Health       — Healthcare, 80 employees, bootstrapped
Atlas Logistics       — Shipping, 300 employees, enterprise
Pinnacle Finance      — FinTech, 45 employees, seed stage
Redwood Manufacturing — Industrial, 500 employees, public
Coastal Media Group   — Media/Ads, 25 employees, boutique
Summit Education      — EdTech, 120 employees, Series A
Ironbridge Consulting — Consulting, 60 employees, bootstrapped
NovaStar Robotics     — AI/Robotics, 200 employees, Series C
Drift Analytics       — Data analytics, 35 employees, seed
```

### Contact Names

```
Sarah Chen, VP of Sales — TechNova
Marcus Rivera, CTO — Meridian Health
Elena Kowalski, Head of Ops — Atlas Logistics
James Okafor, CEO — Pinnacle Finance
Priya Sharma, CMO — Redwood Manufacturing
David Park, Marketing Director — Coastal Media
Amara Johnson, COO — Summit Education
Michael Torres, VP Engineering — Ironbridge Consulting
Yuki Tanaka, Product Lead — NovaStar Robotics
Rachel Novak, Data Lead — Drift Analytics
```

### Support Ticket Categories

```
billing         — Invoice disputes, payment failures, plan changes
technical       — API errors, integration issues, performance problems
feature_request — New feature asks, enhancement suggestions
bug_report      — Bugs in the product, unexpected behavior
onboarding      — Setup help, getting started, configuration
account         — Access issues, password resets, permission changes
```

### Email Templates (Simulated — prompt_preview / response_preview)

**Outreach email (Scout → Lead):**
```
Subject: Quick question about {company}'s {pain_point}
Hi {first_name}, I noticed {company} recently {trigger_event}.
We've helped similar companies in {industry} achieve {result}.
Would you have 15 minutes this week to explore if we can help?
```

**Follow-up email (Closer → Lead):**
```
Subject: Re: {company} — next steps
Hi {first_name}, following up on our conversation about {topic}.
I've put together a proposal based on your needs:
- {solution_1}: estimated {saving_1}
- {solution_2}: estimated {saving_2}
Attached is the full proposal. Happy to walk through it.
```

**Support response (Harper → Customer):**
```
Subject: Re: [{ticket_id}] {subject}
Hi {first_name}, thanks for reaching out about {issue}.
I've looked into this and {diagnosis}.
Here's what I recommend: {recommendation}.
{resolution_or_escalation_note}
```

**Campaign report (Campaigner → Dispatch):**
```
Campaign "{campaign_name}" — {period} Report
Sent: {sent} | Opened: {opened} ({open_rate}%)
Clicked: {clicked} ({ctr}%) | Converted: {converted}
Cost: ${cost} | Revenue attributed: ${revenue}
ROI: {roi}%
```

### Campaign Names

```
"Spring Growth Accelerator"
"Q1 Product Launch Blitz"
"Customer Win-Back Wave"
"Enterprise Tier Upsell"
"Webinar: AI in {industry}"
"Case Study: {customer} Success"
"Holiday Season Promo"
"Free Trial Extension Push"
"Partner Channel Activation"
"Annual Review Outreach"
```

### LoopColony Topics & Posts (Simulated via custom events)

**Topics (channels):**
```
#daily-standup     — Daily status updates from all agents
#deals             — New leads, proposals, closed deals
#support-escalations — Escalated tickets needing attention
#campaign-results  — Campaign performance and A/B test results
#data-alerts       — CRM sync failures, data quality issues
#general           — Cross-team coordination
```

**Example posts (simulated as custom events with kind="loopcolony"):**
```
[scout → #deals] "New qualified lead: TechNova (score: 87). Routing to @closer."
[closer → #deals] "Proposal sent to Sarah Chen at TechNova. $24K ARR opportunity."
[harper → #support-escalations] "Ticket #1042 escalated: Meridian Health API integration failure. Needs engineering."
[campaigner → #campaign-results] "Spring Growth Accelerator: 34% open rate, 8.2% CTR. A/B winner: variant B."
[archivist → #data-alerts] "CRM sync warning: 3 duplicate contacts found in Pinnacle Finance account."
[dispatch → #daily-standup] "Morning brief: 4 leads in queue, 2 open escalations, 1 campaign launching today."
```

---

## Heartbeat Payloads (Per Agent)

Each agent registers a `heartbeat_payload` callback that returns runtime telemetry every 30 seconds.
These fields power the Insights tab's fleet health views.

### Scout
```python
{
    "uptime_seconds": 3600,
    "tasks_completed": 42,
    "tasks_failed": 3,
    "total_tokens_in": 128000,
    "total_tokens_out": 24000,
    "total_cost_usd": 0.58,
    "consecutive_failures": 0,
    "current_model": "claude-sonnet-4-20250514",
    "leads_scored": 42,
    "avg_lead_score": 64,
}
```

### Closer
```python
{
    "uptime_seconds": 3600,
    "tasks_completed": 18,
    "tasks_failed": 1,
    "total_tokens_in": 95000,
    "total_tokens_out": 32000,
    "total_cost_usd": 0.77,
    "consecutive_failures": 0,
    "current_model": "claude-sonnet-4-20250514",
    "deals_closed": 11,
    "deals_lost": 1,
    "pipeline_value_usd": 156000,
}
```

### Harper
```python
{
    "uptime_seconds": 3600,
    "tasks_completed": 35,
    "tasks_failed": 2,
    "total_tokens_in": 110000,
    "total_tokens_out": 45000,
    "total_cost_usd": 1.01,
    "consecutive_failures": 0,
    "current_model": "claude-sonnet-4-20250514",
    "tickets_resolved": 28,
    "tickets_escalated": 4,
    "avg_resolution_seconds": 8,
}
```

### Campaigner
```python
{
    "uptime_seconds": 3600,
    "tasks_completed": 8,
    "tasks_failed": 0,
    "total_tokens_in": 64000,
    "total_tokens_out": 28000,
    "total_cost_usd": 0.74,
    "consecutive_failures": 0,
    "current_model": "gpt-4o",
    "campaigns_launched": 3,
    "avg_open_rate": 28.5,
}
```

### Archivist
```python
{
    "uptime_seconds": 3600,
    "tasks_completed": 22,
    "tasks_failed": 2,
    "total_tokens_in": 38000,
    "total_tokens_out": 12000,
    "total_cost_usd": 0.03,
    "consecutive_failures": 0,
    "current_model": "claude-haiku-4-5-20251001",
    "records_synced": 340,
    "duplicates_merged": 8,
    "sync_errors": 2,
}
```

### Dispatch
```python
{
    "uptime_seconds": 3600,
    "tasks_completed": 12,
    "tasks_failed": 0,
    "total_tokens_in": 22000,
    "total_tokens_out": 8000,
    "total_cost_usd": 0.01,
    "consecutive_failures": 0,
    "current_model": "claude-haiku-4-5-20251001",
    "agents_online": 6,
    "total_queue_depth": 7,
    "open_escalations": 2,
}
```

**Implementation:** Each agent maintains in-memory counters (tasks completed, tokens used, etc.)
that are updated as the simulation runs. The `heartbeat_payload` callback reads these counters.

---

## Time-of-Day Activity Patterns

Instead of uniform random intervals, agent activity rates shift based on simulated time of day.
The simulator tracks a virtual clock that advances faster than real time (controlled by `--speed`).
One simulated "business day" (8 hours) takes ~8 minutes at default speed.

| Time Block | Scout | Closer | Harper | Campaigner | Archivist | Dispatch |
|-----------|-------|--------|--------|------------|-----------|----------|
| Morning (9am-12pm) | HIGH | Medium | Medium | HIGH | Medium | HIGH |
| Afternoon (12pm-5pm) | Medium | HIGH | HIGH | Medium | Medium | Medium |
| Evening (5pm-8pm) | Low | Low | Low | Low | HIGH | Medium |

**Implementation:** Each agent's sleep interval is multiplied by a factor:
- HIGH activity = 0.5x sleep (twice as fast)
- Medium activity = 1.0x sleep (normal)
- Low activity = 2.0x sleep (half as fast)

This creates natural dashboard sparkline rhythms — lead scoring peaks in the morning,
support tickets peak in the afternoon, batch jobs run in the evening.

---

## Story Arcs (Multi-Cycle Events)

Every ~20 task cycles (across all agents), the simulator may trigger a **story arc** — a
multi-cycle event that cascades across agents and creates correlated dashboard activity.

| Arc | Probability | Duration | Agents Affected | What Happens |
|-----|-------------|----------|-----------------|--------------|
| `crm_outage` | 15% per check | 3-5 cycles | Archivist, Scout, Harper | Archivist `report_issue("CRM API unreachable", severity="high", issue_id="crm-outage")`. Scout's `update_crm_lead` fails with retries. Harper's `submit_to_crm` fails. After N cycles, Archivist `resolve_issue(issue_id="crm-outage")`. |
| `lead_flood` | 10% per check | 4-6 cycles | Scout, Closer, Dispatch | Campaigner's last campaign drove a spike. Scout's `queue_provider` returns depth 12+. Scout processes faster (reduced sleep). Dispatch posts urgent standup. Closer gets extra TODOs. |
| `angry_customer` | 20% per check | 2-3 cycles | Harper, Dispatch, Closer | Harper receives a high-severity ticket with `sentiment: "angry"`. Escalation fires immediately. Dispatch reviews and approves credit. Closer attempts win-back outreach. Cross-posted to #support-escalations and #deals. |
| `data_migration` | 5% per check | 5-8 cycles | Archivist, Dispatch | Archivist enters heavy cleanup mode (faster cycle, more tasks). Other agents see `report_issue(severity="low", issue_id="stale-data")` warnings. Archivist resolves when migration completes. |
| `campaign_launch` | 10% per check | Full simulated day | All agents | Campaigner launches a major campaign. Scout gets flood of leads. Closer is busy with proposals. Harper gets onboarding tickets. Archivist syncs new records. Dispatch coordinates everything. |

**Implementation:** A shared `StoryArcEngine` runs in the main thread. It sets flags
(`_active_arcs`) that agent threads check each cycle. Active arcs modify agent behavior:
sleep intervals, failure rates, queue depths, and issue reporting.

**Correlated cascades:** When `crm_outage` is active, ALL agents that touch CRM report the
same `issue_id="crm-outage"`. This creates a dashboard view where multiple agents show the
same red issue badge — exactly what a real outage looks like.

---

## Issue Lifecycle (Escalating Severity)

Issues don't appear and disappear instantly. They follow a lifecycle with escalating severity:

```
Cycle 1: report_issue(summary="CRM API slow", severity="low", issue_id="crm-perf", occurrence_count=1)
Cycle 3: report_issue(summary="CRM API slow", severity="medium", issue_id="crm-perf", occurrence_count=5)
Cycle 5: report_issue(summary="CRM API slow", severity="high", issue_id="crm-perf", occurrence_count=12)
Cycle 8: resolve_issue(summary="CRM API recovered", issue_id="crm-perf")
```

**Implementation:** Each persistent issue is tracked in a shared `_issue_tracker` dict:
```python
_issue_tracker = {
    "crm-perf": {"severity": "low", "occurrences": 1, "first_seen_cycle": 10, "agent": "archivist"},
}
```

Each cycle, active issues increment `occurrences`. Severity escalates at thresholds:
- 1-3 occurrences: `low`
- 4-8 occurrences: `medium`
- 9+ occurrences: `high`

The story arc engine can also inject issues directly at higher severity.

---

## Plan Revisions

Some tasks don't go according to plan. When a plan step fails and recovery requires
a different approach, the agent revises the plan mid-task.

**Scout — enrichment failure triggers plan revision:**
```python
# Original plan
task.plan("Process lead: TechNova", ["Score lead", "Enrich company data", "Draft outreach", "Route to closer", "Update CRM"])

# Step 1 (Enrich) fails
task.plan_step(1, "failed", "Clearbit API timeout after 3 retries")

# Revised plan — skip enrichment, use basic data
task.plan("Process lead: TechNova",
          ["Score lead", "Draft outreach (basic)", "Route to closer", "Update CRM"],
          revision=1)
task.plan_step(0, "completed", "Score lead (already done)")  # mark already-completed steps
task.plan_step(1, "started", "Draft outreach with basic data")
```

**Harper — escalation triggers plan revision:**
```python
# Original plan
task.plan("Resolve ticket #1042", ["Classify ticket", "Search KB", "Draft response", "Resolve"])

# KB search returns no results, customer is angry → escalation path
task.plan_step(2, "failed", "No KB articles found, customer sentiment: frustrated")

# Revised plan — escalation path
task.plan("Resolve ticket #1042",
          ["Classify ticket", "Search KB", "Escalate to senior", "Await approval", "Apply credit"],
          revision=1)
```

**Implementation:** Each agent has a `_should_revise_plan()` check (10-15% probability,
triggered by step failures). When triggered, the agent calls `task.plan()` with `revision=N`
and adjusts remaining steps. The dashboard shows the progress bar resetting with the new plan.

---

## Agent Workflows (Detailed)

### 1. Scout — Lead Hunter

**Task type:** `lead_qualification`
**Cycle:** Every 3–8 seconds (speed-adjusted)

**Workflow per lead:**
1. **Receive lead** from inbound form (simulated)
2. **Score lead** — LLM call: `lead_scoring` (classify company fit, budget signals, urgency)
3. **Enrich data** — Tool: `enrich_company_data` (simulated Clearbit/Apollo lookup)
   - 15% chance: API timeout → retry → resolve issue
4. **Draft outreach** — LLM call: `draft_outreach_email` (generate personalized email)
5. **Route to Closer** — Posts to LoopColony #deals, creates TODO for closer
6. **Update CRM** — Tool: `update_crm_lead` (add lead to pipeline)

**Plan:** `["Score lead", "Enrich company data", "Draft outreach email", "Route to closer", "Update CRM"]`

**SDK coverage:**
- Layer 0: heartbeat, queue_provider (leads in queue)
- Layer 1: task context, plan, plan_steps
- Layer 2a: 2 LLM calls per task (scoring + outreach draft)
- Layer 2b: track_context for enrich and CRM tools
- Layer 2c: issues (API timeouts), retries, TODOs (for closer), scheduled (batch re-scoring)
- Layer 3: config_snapshot at startup, runtime events
- Layer 4: prompt composition tracking on scoring call

### 2. Closer — Sales Follow-up

**Task type:** `sales_followup`
**Cycle:** Every 5–12 seconds (speed-adjusted)

**Workflow per lead:**
1. **Review lead brief** — LLM call: `review_lead_brief` (analyze scout's notes)
2. **Research company** — Tool: `research_company` (simulated web scrape)
3. **Draft proposal** — LLM call: `draft_proposal` (generate pricing proposal)
4. **Send proposal email** — Tool: `send_email` (simulated email send)
5. **Decision gate:**
   - 60% → Deal closed: log success, post to #deals
   - 25% → Needs follow-up: schedule follow-up, create TODO
   - 10% → Escalation: request_approval from "sales-lead"
   - 5% → Deal lost: log loss, post to #deals

**Plan:** `["Review lead brief", "Research company", "Draft proposal", "Send proposal", "Await response"]`

**SDK coverage:**
- Layer 0: heartbeat with payload (deals_closed, pipeline_value)
- Layer 1: task, plan, plan_steps
- Layer 2a: 2 LLM calls (review + proposal)
- Layer 2b: track_context for research and email tools
- Layer 2c: escalation, approval flow, TODOs (follow-ups)
- Layer 3: learning events (when a deal pattern is detected)

### 3. Harper — Support Rep

**Task type:** `ticket_triage`
**Cycle:** Every 4–10 seconds (speed-adjusted)

**Workflow per ticket:**
1. **Classify ticket** — LLM call: `classify_ticket` (category, priority, sentiment)
2. **Search knowledge base** — Tool: `search_kb` (find relevant articles)
3. **Draft response** — LLM call: `draft_support_response` (generate response)
4. **Decision gate:**
   - 70% → Resolved: send response, close ticket
   - 15% → Needs more info: request clarification from customer
   - 10% → Escalation: escalate to senior support, post to #support-escalations
   - 5% → CRM failure: action_failed on `submit_to_crm`

**Plan:** `["Classify ticket", "Search knowledge base", "Draft response", "Resolve or escalate"]`

**SDK coverage:**
- Layer 0: heartbeat with payload (tickets_resolved, avg_resolution_time)
- Layer 1: task, plan, plan_steps
- Layer 2a: 2 LLM calls (classify + draft)
- Layer 2b: track_context for KB search and CRM submit
- Layer 2c: escalation, issues (CRM failures), TODOs (follow-up tickets)
- Layer 3: context_compaction (when ticket thread is long), error_context
- Layer 4: cycle detection (if same KB query repeated)

### 4. Campaigner — Marketing Ops

**Task type:** `campaign_management`
**Cycle:** Every 8–15 seconds (speed-adjusted)

**Workflow per campaign cycle:**
1. **Design campaign** — LLM call: `design_campaign` (target audience, messaging, channels)
2. **Generate content** — LLM call: `generate_copy` (email subject lines, body variants)
3. **A/B test setup** — Tool: `setup_ab_test` (create variant A and B)
4. **Monitor metrics** — Tool: `fetch_campaign_metrics` (open rate, CTR, conversions)
5. **Analyze results** — LLM call: `analyze_results` (determine winner, recommend next steps)
6. **Report to LoopColony** — Post results to #campaign-results

**Plan:** `["Design campaign", "Generate content", "Set up A/B test", "Monitor metrics", "Analyze & report"]`

**SDK coverage:**
- Layer 0: heartbeat, scheduled (campaign schedules)
- Layer 1: task, plan, plan_steps
- Layer 2a: 3 LLM calls (design + copy + analysis)
- Layer 2b: track_context for A/B test and metrics tools
- Layer 2c: scheduled work, TODOs (optimize underperforming campaigns)
- Layer 3: learning events (A/B test insights), config_snapshot
- Layer 4: prompt composition (campaign briefs can get large)

### 5. Archivist — Data & CRM

**Task type:** `data_operations`
**Cycle:** Every 6–14 seconds (speed-adjusted)

**Workflow per batch:**
1. **CRM sync** — Tool: `sync_crm` (pull new records, detect conflicts)
2. **Data validation** — LLM call: `validate_records` (check for duplicates, inconsistencies)
3. **Deduplication** — Tool: `deduplicate_contacts` (merge duplicates)
4. **Generate report** — LLM call: `generate_report` (daily/weekly summary)
5. **Compliance check** — Tool: `compliance_scan` (GDPR/data retention)
6. **Post alerts** — Post data quality findings to #data-alerts

**Plan:** `["Sync CRM", "Validate data", "Deduplicate contacts", "Generate report", "Compliance scan"]`

**SDK coverage:**
- Layer 0: heartbeat, queue_provider (sync queue depth)
- Layer 1: task, plan, plan_steps
- Layer 2a: 2 LLM calls (validation + report)
- Layer 2b: track_context for sync, dedup, compliance tools
- Layer 2c: issues (sync failures), retries, scheduled (hourly sync, daily report, weekly cleanup)
- Layer 3: memory_op events (CRM reads/writes), runtime events
- Layer 4: state_mutation tracking (CRM record changes)

### 6. Dispatch — Coordinator

**Task type:** `coordination`
**Cycle:** Every 10–20 seconds (speed-adjusted)

**Workflow per coordination cycle:**
1. **Check all queues** — Tool: `check_agent_queues` (poll each agent's backlog)
2. **Route work** — LLM call: `route_work` (decide which agent handles what)
3. **Post to LoopColony** — Tool: `post_to_loopcolony` (daily standup, assignments)
4. **Create assignments** — Create TODOs for individual agents
5. **Review escalations** — LLM call: `review_escalations` (approve or reassign)
6. **Approval decisions** — approval_received for pending requests

**Plan:** `["Check queues", "Route incoming work", "Post standup", "Create assignments", "Review escalations"]`

**SDK coverage:**
- Layer 0: heartbeat with payload (agents_online, total_queue_depth, open_escalations)
- Layer 1: task, plan, plan_steps
- Layer 2a: 2 LLM calls (routing + escalation review)
- Layer 2b: track_context for queue check and LoopColony post
- Layer 2c: TODOs (assignments), approvals (escalation decisions)
- Layer 3: runtime events (morning brief, end-of-day summary), config_snapshot
- Layer 4: anomaly detection (if work is piling up in one agent)

---

## Inter-Agent Interactions

These are the cross-agent handoffs that make the simulation feel like a real business:

| From | To | Trigger | Mechanism |
|------|----|---------|-----------|
| Scout → Closer | Lead qualified (score > 60) | TODO created for closer, LoopColony #deals post |
| Harper → Dispatch | Ticket escalated | Escalation event, LoopColony #support-escalations post |
| Dispatch → Harper | Escalation approved/reassigned | approval_received, TODO for harper |
| Campaigner → Scout | Campaign generates new leads | TODO created for scout |
| Archivist → Dispatch | Data quality alert | Issue reported, LoopColony #data-alerts post |
| Dispatch → All | Morning standup | LoopColony #daily-standup post with queue summary |
| Closer → Archivist | Deal closed | TODO for archivist to update CRM records |

---

## LoopColony Event Format

All LoopColony activity is emitted as `custom` events with `kind: "loopcolony"`:

```python
agent.event("custom", payload={
    "kind": "loopcolony",
    "data": {
        "action": "post",        # "post", "comment", "create_topic", "assign"
        "topic": "#deals",       # channel/topic name
        "author": "scout",       # agent who posted
        "content": "New qualified lead: TechNova (score: 87). Routing to @closer.",
        "mentions": ["closer"],  # agents mentioned
        "thread_id": "thread-001",  # for comments on existing posts
        "priority": "normal",    # "low", "normal", "high", "urgent"
    },
})
```

---

## Simulation Loop Architecture

```
Main Thread
├── hiveloop.init(environment="production")  # All agents report environment field
├── _ensure_projects()          # Create all 5 projects on startup
├── _emit_startup_events()      # Config snapshots, runtime events for each agent
├── StoryArcEngine              # Checks every ~20 cycles, triggers arcs
├── Thread: scout               # Infinite loop with time-of-day adjusted sleep
├── Thread: closer              # Infinite loop with time-of-day adjusted sleep
├── Thread: harper              # Infinite loop with time-of-day adjusted sleep
├── Thread: campaigner          # Infinite loop with time-of-day adjusted sleep
├── Thread: archivist           # Infinite loop with time-of-day adjusted sleep
├── Thread: dispatch            # Infinite loop with time-of-day adjusted sleep (slower cadence)
└── Main: sleep(1) loop         # Ctrl+C → hiveloop.shutdown()
```

**Environment field:** All agents are initialized with `environment="production"` to simulate
a production deployment. This populates the environment column in the dashboard and allows
filtering by environment in Insights queries.

Each agent thread runs an infinite loop:
1. Pick random customer/data from the pools
2. Execute the full workflow (5–6 steps with plans)
3. Random sleep (speed-adjusted)
4. Repeat

The randomization ensures each loop iteration looks different on the dashboard while
the structure (plans, tools, LLM calls) remains consistent and readable.

---

## LLM Models Used (Simulated)

| Model | Cost/1K in | Cost/1K out | Used by |
|-------|-----------|------------|---------|
| `claude-sonnet-4-20250514` | $0.003 | $0.015 | Scout, Closer, Harper (primary reasoning) |
| `claude-haiku-4-5-20251001` | $0.00025 | $0.00125 | Archivist, Dispatch (fast classification) |
| `gpt-4o` | $0.005 | $0.015 | Campaigner (creative content) |
| `gpt-4o-mini` | $0.00015 | $0.0006 | All agents (quick validation calls) |

---

## Scheduled Work (Reported at Startup)

| Agent | Schedule ID | Name | Interval |
|-------|------------|------|----------|
| Scout | `sched-rescore` | Batch lead re-scoring | 1h |
| Scout | `sched-stale-cleanup` | Stale lead cleanup | daily |
| Closer | `sched-followup-check` | Follow-up reminder check | 2h |
| Harper | `sched-kb-refresh` | Knowledge base refresh | 6h |
| Campaigner | `sched-metrics-pull` | Campaign metrics pull | 30m |
| Campaigner | `sched-weekly-report` | Weekly ROI report | weekly |
| Archivist | `sched-crm-sync` | CRM hourly sync | 1h |
| Archivist | `sched-daily-report` | Daily data quality report | daily |
| Archivist | `sched-weekly-compliance` | Weekly compliance scan | weekly |
| Dispatch | `sched-morning-brief` | Morning standup brief | daily |
| Dispatch | `sched-eod-summary` | End-of-day summary | daily |

---

## CLI Interface

```
python examples/brightpath.py                                        # defaults: localhost:8000
python examples/brightpath.py --endpoint http://host:port --api-key hb_live_xxx
python examples/brightpath.py --fast                                 # 5x speed
python examples/brightpath.py --speed 3                              # 3x speed
python examples/brightpath.py --agents scout,harper,dispatch         # run subset
```

---

## File Structure

```
examples/
├── simulator.py                # Original 3-agent simulator (preserved)
├── brightpath.py               # New 6-agent business simulator
└── BRIGHTPATH_SIMULATOR_DESIGN.md  # This design document
```

---

## Implementation Checklist

- [ ] Data pools: customers, contacts, templates, campaigns, topics
- [ ] LLM call simulation helper (reuse existing pattern, add metadata)
- [ ] LoopColony event helper
- [ ] Tool simulation helper (with track_context)
- [ ] Heartbeat payload callbacks (per-agent counters, domain-specific fields)
- [ ] Time-of-day activity engine (morning/afternoon/evening rate multipliers)
- [ ] Story arc engine (5 arc types, shared flags, cascade effects)
- [ ] Issue lifecycle tracker (escalating severity by occurrence count)
- [ ] Plan revision logic (10-15% probability on step failure)
- [ ] Agent 1: Scout — full workflow
- [ ] Agent 2: Closer — full workflow
- [ ] Agent 3: Harper — full workflow
- [ ] Agent 4: Campaigner — full workflow
- [ ] Agent 5: Archivist — full workflow
- [ ] Agent 6: Dispatch — full workflow with inter-agent coordination
- [ ] Startup: config_snapshot + runtime events for all agents (environment="production")
- [ ] Project creation on startup
- [ ] CLI argument parsing (endpoint, api-key, speed, agent filter)
- [ ] Graceful shutdown

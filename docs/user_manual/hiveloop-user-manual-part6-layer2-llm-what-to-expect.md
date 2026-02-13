# HiveBoard — User Manual Part 6: Layer 2 LLM Tracking — What to Expect

**Version:** 0.1.0
**Last updated:** 2026-02-12

> *You've added `task.llm_call()`. Here's what lights up, what the numbers mean, and how to use them.*

---

## Table of Contents

1. [What LLM Tracking Gives You](#1-what-llm-tracking-gives-you)
2. [The Cost Explorer](#2-the-cost-explorer)
3. [Reading the Task Table with LLM Data](#3-reading-the-task-table-with-llm-data)
4. [Reading the Timeline with LLM Nodes](#4-reading-the-timeline-with-llm-nodes)
5. [Reading the Activity Stream with LLM Events](#5-reading-the-activity-stream-with-llm-events)
6. [The Stats Ribbon with Cost Data](#6-the-stats-ribbon-with-cost-data)
7. [Agent Cards with Cost Context](#7-agent-cards-with-cost-context)
8. [Investigation Workflows](#8-investigation-workflows)
9. [Understanding Your Cost Profile](#9-understanding-your-cost-profile)
10. [Common Patterns](#10-common-patterns)
11. [What You Don't See Yet (and Why)](#11-what-you-dont-see-yet-and-why)

---

## 1. What LLM Tracking Gives You

Layer 1 told you what your agents are doing and how long it takes. LLM tracking tells you **what it costs and where the money goes.**

With `task.llm_call()` in place, here's the before and after:

| Dashboard element | Layer 1 (before) | + LLM tracking (now) |
|------------------|-------------------|----------------------|
| **Cost Explorer** | All zeros | Fully functional — cost by model, cost by agent, call counts, token totals |
| **Task Table — LLM column** | "—" | Call count per task (e.g. "◆ 6") |
| **Task Table — COST column** | "—" | Dollar amount per task (e.g. "$0.07") |
| **Timeline** | Task + action nodes only | + Purple LLM nodes with model badges |
| **Timeline header** | Duration + status | + "◆ 6 LLM" call count |
| **Stats Ribbon — Cost (1h)** | "—" | Dollar amount (e.g. "$5.11") |
| **Mini-Charts — LLM Cost/Task** | Flat | Cost-per-task trend bars |
| **Activity Stream** | task + action events | + `llm_call` events with model, tokens, cost |
| **Activity Stream — "llm" filter** | Empty | Shows every LLM call |

### What questions LLM tracking answers

- How much is my agent fleet costing per hour?
- Which model is the most expensive?
- Which agent spends the most?
- How many LLM calls per task?
- What's the average cost per call?
- Is cost per task stable or increasing?
- Which LLM call within a task is the most expensive?
- Am I using an expensive model where a cheaper one would work?

---

## 2. The Cost Explorer

The Cost Explorer is the primary view for cost analysis. Switch to it by clicking **Cost Explorer** in the top navigation bar.

### 2.1 Cost Ribbon

The top bar shows aggregate numbers:

```
TOTAL COST     LLM CALLS     TOKENS IN      TOKENS OUT     AVG COST/CALL
$5.11          397            1,563.5K       117.0K         $0.013
```

| Metric | What it means |
|--------|--------------|
| **Total Cost** | Sum of all LLM call costs in the current time window |
| **LLM Calls** | Total number of `task.llm_call()` events |
| **Tokens In** | Total input tokens across all calls |
| **Tokens Out** | Total output tokens across all calls |
| **Avg Cost/Call** | Total Cost ÷ LLM Calls — your average per-call spend |

### 2.2 Cost by Model

```
MODEL                              CALLS    TOKENS IN    TOKENS OUT    COST
claude-sonnet-4-5-20250929         332      1,538.5K     111.7K        $5.10    ████████████████
claude-3-haiku-20240307            65       25.0K        5.3K          $0.01    ▎
```

This table answers: **"Where is the money going?"**

In the example above, Sonnet accounts for 99.8% of cost despite being only 84% of calls. Haiku handles 16% of calls for $0.01 total. This is the typical pattern — one expensive model dominates cost while a cheaper model handles lightweight tasks.

**What to look for:**
- **Model concentration:** Is 90%+ of cost coming from one model? Could some of those calls use a cheaper model?
- **Token ratios:** High tokens-in with low tokens-out may mean you're sending large prompts for simple completions — consider prompt optimization
- **Call counts:** Many small calls vs few large calls have different optimization strategies

### 2.3 Cost by Agent

```
AGENT              CALLS    TOKENS IN    TOKENS OUT    COST
main               199      1,033.7K     61.1K         $3.18    █████████████
ag_6ce5uncd        198      529.8K       55.9K         $1.93    ████████
```

This table answers: **"Which agent is the most expensive?"**

**What to look for:**
- **Cost asymmetry:** If agents do similar work but one costs 2× more, it may be using a more expensive model or sending larger prompts
- **Calls per agent:** Similar call counts but different costs → different models or prompt sizes
- **Tokens In per call:** Divide Tokens In by Calls. If one agent averages 5K tokens/call and another averages 2K, the first is sending much larger contexts

### 2.4 Time filtering

The Cost Explorer respects the environment selector in the top bar. All numbers reflect the currently selected environment and time window.

---

## 3. Reading the Task Table with LLM Data

### 3.1 New columns

With LLM tracking, two columns in the Task Table come alive:

```
TASK ID                          AGENT          TYPE       STATUS      DURATION  LLM    COST     TIME
ag_6ce5uncd-evt_f758dc50253d     ag_6ce5uncd    heartbeat  completed   27.0s     ◆ 6    $0.07    2m ago
main-evt_4f7ffbde231a            main           heartbeat  completed   27.9s     ◆ 6    $0.12    2m ago
ag_6ce5uncd-evt_034d8a89118f     ag_6ce5uncd    heartbeat  completed   25.4s     ◆ 6    $0.07    3m ago
```

| Column | What it shows |
|--------|-------------|
| **LLM** | Purple diamond + count of LLM calls in this task (e.g. "◆ 6") |
| **COST** | Total cost of all LLM calls in this task |

### 3.2 What to scan for

| Pattern | What it means |
|---------|--------------|
| All rows show similar LLM count and cost | Consistent task behavior — each task makes the same calls |
| One row has significantly higher cost | That task hit an expensive code path — click to investigate timeline |
| LLM count varies (◆ 2 vs ◆ 8) | Different task types trigger different LLM paths |
| Cost increasing over time (newer rows cost more) | Prompt sizes may be growing (context window filling up) |
| Cost per agent differs ($0.12 vs $0.07 for same task type) | Agents may use different models or prompt configurations |

### 3.3 Cost difference between agents

In the screenshots, `main` tasks cost $0.12 while `ag_6ce5uncd` tasks cost $0.07 — same task type (heartbeat), same number of LLM calls (◆ 6). The difference comes from prompt size: `main` sends ~5.2K tokens per Phase 1 call while `ag_6ce5uncd` sends ~2.7K. Larger context = more tokens = higher cost. This is exactly the kind of insight that was invisible before LLM tracking.

---

## 4. Reading the Timeline with LLM Nodes

### 4.1 LLM nodes on the timeline

With LLM tracking, purple square nodes appear on the timeline for each LLM call:

```
TIMELINE  ag_6ce5uncd-evt_f758dc50253d  ⏱ 27.0s  🤖 ag_6ce5uncd  ✓ completed  ◆ 6 LLM

  [phase1_reasoning]  [phase2_tool_use]  [phase1_reasoning]  [phase2_tool_use]  [phase1_reasoning]  [heartbeat_summary]
       □                    □                   □                    □                  □                    □
   20:13:11           20:13:14            20:13:20             20:13:23           20:13:29              20:13:30
```

Each LLM node represents one `task.llm_call()`:
- **Square shape (□)** — LLM calls use squares, not circles (which are task/action events)
- **Purple color** — distinguishes LLM events from task events (green) and action events (blue)
- **Model badge** — the model name appears above the node (e.g. `claude-sonnet-4-5-20250929`)
- **Timestamp** — when the call started

### 4.2 Timeline header enrichment

The timeline header now shows LLM call count:

```
TIMELINE  task-id  ⏱ 27.0s  🤖 agent-name  ✓ completed  ◆ 6 LLM
```

The "◆ 6 LLM" tells you at a glance how many LLM calls this task made without needing to count nodes.

### 4.3 Reading the LLM call sequence

The node sequence tells you the agent's reasoning flow:

```
phase1_reasoning → phase2_tool_use → phase1_reasoning → phase2_tool_use → phase1_reasoning → heartbeat_summary
```

This reveals:
- The agent did 3 reasoning passes (phase1) and 2 tool-use passes (phase2)
- The pattern is alternating: reason → act → reason → act → reason
- The final call is a heartbeat summary using a cheaper model (Haiku)
- Each pair (reason + act) represents one "turn" of the agent loop

### 4.4 Clicking an LLM node

Click any purple node to see the detail panel:

```
◆ phase1_reasoning  20:12:12.830
  event       llm_call
  model       claude-sonnet-4-5-20250929
  tokens_in   9,569
  tokens_out  363
  cost        $0.034
  duration    2.8s
```

This shows exactly what happened in this specific call — the model used, token counts, cost, and latency. Compare this across calls to find which ones are expensive.

### 4.5 Identifying the expensive call

In a task with 6 LLM calls totaling $0.07, one call may account for $0.03 while the others are $0.008 each. Click through the nodes to find the expensive one — it's usually the first `phase1_reasoning` call (largest context) or a `phase2_tool_use` call (tool responses can be large).

---

## 5. Reading the Activity Stream with LLM Events

### 5.1 LLM events in the stream

LLM calls appear as `llm_call` events with rich detail:

```
●● llm_call                                                    2m ago
   ag_6ce5uncd > ag_6ce5uncd-evt_f758dc50253d
   heartbeat_summary → claude-3-haiku-20240307 (378 in / 81 out, $0.0002)

●● llm_call                                                    2m ago
   ag_6ce5uncd > ag_6ce5uncd-evt_f758dc50253d
   phase1_reasoning → claude-sonnet-4-5-20250929 (4565 in / 327 out, $0.019)

●● llm_call                                                    3m ago
   main > main-evt_4f7ffbde231a
   phase2_tool_use → claude-sonnet-4-5-20250929 (1220 in / 228 out, $0.007)
```

Each LLM event shows:
- **Call name** → **Model** (e.g. `phase1_reasoning → claude-sonnet-4-5-20250929`)
- **Token counts** in parentheses (tokens in / tokens out)
- **Cost** in USD
- **Agent and task reference** — clickable to navigate

### 5.2 The "llm" stream filter

Click the **llm** filter button to show only LLM call events. This gives you a live feed of every LLM API call across your fleet — useful for:
- Watching LLM calls in real time during a task
- Spotting unexpectedly expensive calls
- Seeing which models are being used
- Identifying patterns (e.g. every task ends with a cheap Haiku call for summarization)

### 5.3 Reading cost in the stream

The stream shows cost per-call. Scan for outliers:
- Most calls might be $0.005-$0.02
- If one shows $0.15, that's a 10× outlier — investigate (likely a very large prompt)
- Calls showing `$0.0002` are cheap model calls (Haiku) — expected for lightweight tasks

---

## 6. The Stats Ribbon with Cost Data

### 6.1 Cost (1h)

The Stats Ribbon now shows cost in the rightmost position:

```
TOTAL AGENTS  PROCESSING  WAITING  STUCK  ERRORS  SUCCESS RATE  AVG DURATION  COST (1H)
2             0           0        0      0       100%          27.3s         $5.11
```

**Cost (1h)** is the total LLM spend in the last hour. This is your burn rate indicator.

**Quick math:** If Cost (1h) = $5.11, your daily burn rate is roughly $5.11 × 24 = ~$123/day, or ~$3,700/month. This is the number that makes invisible spend visible.

### 6.2 LLM Cost/Task mini-chart

The LLM Cost/Task chart shows cost-per-task over time. Each bar represents the average cost of tasks in that time bucket.

**What to watch for:**
- **Flat line** — consistent cost per task. Good.
- **Rising trend** — cost per task is increasing. May indicate growing context windows, more LLM turns per task, or a model change.
- **Spikes** — occasional expensive tasks. Click the Task Table to find them.

---

## 7. Agent Cards with Cost Context

### 7.1 Current task visibility

With LLM tracking, agent cards show the current task ID (which now includes the event ID for uniqueness):

```
┌─────────────────────────────────────┐
│ ag_6ce5uncd                    IDLE │
│ Marketing Expert  ● 12s ago        │
│ ↳ ag_6ce5uncd-evt_f758dc50253d     │  ← current/last task with unique ID
│ ▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪           │
└─────────────────────────────────────┘
```

### 7.2 Cost on agent cards

The Cost by Agent breakdown in the Cost Explorer is your primary tool for per-agent cost analysis. Agent cards don't show cost directly (they focus on status and health), but clicking an agent filters the entire dashboard — including the Stats Ribbon's Cost (1h) — to that agent's data.

---

## 8. Investigation Workflows

### 8.1 "How much is this costing me?"

1. Click **Cost Explorer** in the top bar
2. Read **Total Cost** in the Cost Ribbon — that's your current burn
3. Check **Cost by Model** — which model dominates spend?
4. Check **Cost by Agent** — which agent spends the most?
5. Quick math: Total Cost ÷ time window = burn rate

### 8.2 "Why is this task expensive?"

1. Find the task in the Task Table (sort by COST column)
2. Click the task row to load its timeline
3. Look at the LLM nodes — how many calls? Which ones?
4. Click each purple node — compare `tokens_in` across calls
5. The call with the highest `tokens_in` is likely the most expensive

### 8.3 "Can I use a cheaper model?"

1. Open Cost Explorer → Cost by Model
2. Identify the expensive model (e.g. Sonnet at $5.10)
3. Click the **llm** filter in the Activity Stream
4. Scan the call names — which operations use the expensive model?
5. Ask: Do `heartbeat_summary` calls need Sonnet, or would Haiku work?
6. If an operation is simple (summarization, classification), try switching it to a cheaper model

### 8.4 "Why does agent A cost more than agent B?"

1. Open Cost Explorer → Cost by Agent
2. Note the difference: agent A ($3.18) vs agent B ($1.93)
3. Check Tokens In: agent A (1,033.7K) vs agent B (529.8K) — A sends 2× more tokens
4. Check Calls: similar (199 vs 198) — same number of calls
5. Conclusion: Agent A's prompts are larger. It's the same number of calls but bigger context windows. Investigate whether A needs the extra context or if it can be trimmed.

### 8.5 "Is cost trending up?"

1. Watch the **LLM Cost/Task** mini-chart over time
2. If bars are growing, cost per task is increasing
3. Common causes:
   - Context window growing (conversation history accumulating)
   - More LLM turns per task (agent needing more reasoning passes)
   - Model change (upgraded to a more expensive model)
4. Check the Task Table — sort by TIME (newest first), compare COST column across recent vs older tasks

---

## 9. Understanding Your Cost Profile

### 9.1 The model mix

Most agent systems use 2-3 models:

| Role | Typical model | Cost tier |
|------|--------------|-----------|
| Heavy reasoning | Sonnet, GPT-4o, Gemini Pro | $3-15/M tokens in |
| Tool use / execution | Sonnet, GPT-4o | $3-10/M tokens in |
| Lightweight tasks | Haiku, GPT-4o-mini, Flash | $0.10-0.80/M tokens in |
| Summarization | Haiku, GPT-4o-mini | $0.10-0.80/M tokens in |

The Cost Explorer's "by model" view immediately shows your mix. A healthy cost profile uses expensive models for reasoning and cheap models for routine operations.

### 9.2 The token budget

For each task, the total cost breaks down as:

```
Task cost = Σ (tokens_in × input_rate + tokens_out × output_rate) for each LLM call
```

Input tokens (prompts) typically dominate cost because:
- System prompts and conversation history are large
- Tool definitions add to every call
- Context windows grow over the task's lifetime

Output tokens (completions) are usually smaller but have higher per-token rates (3-5× input rate for most models).

### 9.3 Establishing your baseline

After running for 1-2 hours with LLM tracking, note these numbers:

| Metric | Your baseline | Where to find it |
|--------|--------------|-----------------|
| Cost per task | e.g. $0.07-0.12 | Task Table, COST column |
| LLM calls per task | e.g. ◆ 6 | Task Table, LLM column |
| Cost per hour | e.g. $5/hr | Stats Ribbon, Cost (1h) |
| Avg cost per call | e.g. $0.013 | Cost Explorer, Avg Cost/Call |
| Most expensive model | e.g. Sonnet at 99% | Cost Explorer, Cost by Model |

These are your reference numbers. When something changes — cost spikes, new model deployed, prompt restructured — you'll compare against this baseline.

---

## 10. Common Patterns

### 10.1 Healthy cost profile

```
Cost Explorer:  Stable total, 1-2 models, clear model roles
Task Table:     Consistent cost per task ($0.07 ± $0.02)
Timeline:       Predictable LLM call pattern (reason → act → reason → summarize)
Stream:         Regular llm_call events, no outliers
```

This is normal. Bookmark these numbers.

### 10.2 Cost creep

```
Cost Explorer:  Total Cost rising each hour
Task Table:     Recent tasks cost more than older tasks
Mini-chart:     LLM Cost/Task trend rising
```

**Diagnosis:** Context windows are growing. Each task carries more conversation history, so `tokens_in` increases with every turn. Check whether context compaction is running. If it is, its compaction threshold may need tuning.

### 10.3 Expensive outlier tasks

```
Task Table:     Most tasks $0.07, one task $0.45
Timeline:       Outlier task has 15 LLM calls instead of the usual 6
Stream:         Multiple retry-like patterns (reasoning → tool_use → reasoning → tool_use...)
```

**Diagnosis:** The agent got stuck in a reasoning loop — it kept retrying or the task was complex enough to require many more turns. Check if there's a turn limit in your agent configuration.

### 10.4 Wrong model for the job

```
Cost Explorer:  Expensive model (Sonnet) handles 100% of calls
Stream:         heartbeat_summary calls use Sonnet ($0.03) instead of Haiku ($0.0002)
```

**Diagnosis:** A cheap summarization task is using an expensive model. Switching `heartbeat_summary` to Haiku saves ~$0.03 per call × hundreds of calls per day. This is the "invisible $40/hour" scenario — everything works fine, but you're paying 100× more than necessary for lightweight tasks.

### 10.5 Token asymmetry

```
Cost Explorer:  Tokens In = 1,563K, Tokens Out = 117K (13:1 ratio)
Stream:         Every call shows large tokens_in, small tokens_out
```

**Diagnosis:** Prompts are very large relative to completions. This is normal for agentic systems (large system prompts, tool definitions, conversation history) but worth watching. If the ratio exceeds 20:1, consider whether all that context is necessary for every call.

---

## 11. What You Don't See Yet (and Why)

Even with LLM tracking, some dashboard elements remain empty until additional Layer 2 events are added:

| Element | Shows | What fills it |
|---------|-------|---------------|
| **Plan progress bar** | Hidden | `task.plan()` + `task.plan_step()` |
| **Pipeline tab (Queue)** | Empty | `agent.queue_snapshot()` |
| **Pipeline tab (Issues)** | Empty | `agent.report_issue()` |
| **Pipeline tab (TODOs)** | Empty | `agent.todo()` |
| **Pipeline tab (Scheduled)** | Empty | `agent.scheduled()` |
| **Waiting count** | 0 | `task.request_approval()` |
| **"human" stream filter** | Empty | `task.escalate()` or `task.request_approval()` |
| **"pipeline" stream filter** | Empty | Pipeline events |

LLM tracking is the highest-value Layer 2 addition, but the remaining events add operational narrative. See Part 5, Section 12 for the incremental adoption strategy.

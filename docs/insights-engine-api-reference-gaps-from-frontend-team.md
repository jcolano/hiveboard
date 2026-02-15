
## Section-to-Endpoint Mapping

### Section 0: Fleet Status — "Who's Alive Right Now?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Status strip (Running/Idle/Stopped counts) | `GET /v1/agents` | `derived_status` → group & count |
| Agent rows (name, status, heartbeat age) | `GET /v1/agents` | `agent_id`, `derived_status`, `last_heartbeat`, `heartbeat_age_seconds`, `is_stuck` |
| Last event type + time ago | **⚠️ GAP** — see below | — |
| Cost per agent (24h) | `GET /v1/insights/agents?range=24h&sort=cost` | `agents[].llm_cost` |
| Cost-per-task | `GET /v1/insights/agents` | `agents[].llm_cost / agents[].tasks_completed` (derived) |
| Cost by Status summary cards | Both above, joined client-side | Group `insights/agents` costs by `agents` status |

**⚠️ Gap: "Last event type + hh:mm:ss ago"** — `/v1/agents` gives `last_heartbeat` and `heartbeat_age_seconds`, but not the *last event type* (e.g. `task_completed`, `llm_call_end`, `agent_error`). The mockup shows this. **Options:** (a) add a `last_event_type` and `last_event_at` field to `GET /v1/agents`, (b) use a separate recent-events call, or (c) drop this detail and just show heartbeat age. What's your preference?

---

### Section 1: Cost Rankings — "Who's Spending What?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Most/Least Expensive cards | `GET /v1/insights/agents?sort=cost` | `comparisons.cost.max_agent`, `min_agent`, `max_value`, `min_value`, `avg_value` |
| Fleet Average card | same | `fleet_totals.total_cost`, derive avg from `agents.length` |
| Cost Spread (max/min ratio) | same | `comparisons.cost.max_vs_min`, `comparisons.cost.max_vs_avg` |
| Distribution strip + ranked bars | same | `agents[].agent_id`, `agents[].llm_cost`, `fleet_totals.total_cost` (derive %) |
| LLM call count + token totals | same | `agents[].llm_call_count`, `agents[].llm_tokens_in + llm_tokens_out` |
| Commentary (model used) | same | `agents[].top_models[0].model` for the most expensive agent |

✅ **Fully covered** by a single call to `/v1/insights/agents?sort=cost`.

---

### Section 2: Activity Rankings — "Who's Doing the Most Work?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Most/Least Active cards | `GET /v1/insights/agents?sort=tasks` | `comparisons.tasks.max_agent`, `min_agent`, etc. |
| Fleet total tasks | same | `fleet_totals.total_tasks` |
| Peak hour | `GET /v1/insights/timeseries?metric=tasks` | `summary.peak_hour` |
| Tasks/hr avg per agent | **⚠️ Derived** | `agents[].tasks_completed / hours_in_range` (client-side) |
| Ranked bars (tasks completed) | `/v1/insights/agents?sort=tasks` | `agents[].tasks_completed` |
| Drilldown: By Task Type | same | `agents[].tasks_by_type` (for the selected agent) |
| Drilldown: By Tool/Action | `GET /v1/insights/actions?agent_id=X` | `actions[].name`, `total_started`, `total_completed` |
| Drilldown: By LLM call name | `/v1/insights/agents` | `agents[].top_actions` (top 5 actions) |

✅ **Mostly covered.** The "By Task" drilldown maps to `tasks_by_type` and `top_actions` from `/v1/insights/agents`. The "By Tool" drilldown maps to `/v1/insights/actions?agent_id=X`.

**⚠️ Minor gap:** The mockup shows cost-per-task-type (e.g. "$0.14/call avg for web_search"). Neither `tasks_by_type` nor `/v1/insights/actions` includes cost. Only `top_llm_calls` has cost, but it's keyed by LLM call name, not task/action name. Would you want to add a `cost` field to the actions endpoint, or should I derive it via `top_llm_calls`?

---

### Section 3: Error Analysis — "Where Are Things Breaking?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Most Errors / Fewest Errors cards | `GET /v1/insights/errors` | `by_agent[0]` (sorted desc), `by_agent[last]` |
| Error rates | `GET /v1/insights/agents` + `errors` | `error_count / (tasks_completed + tasks_failed)` (derived) |
| Fleet Error Rate | `/v1/insights/errors` | `total_errors` / `fleet_totals.total_tasks` from agents |
| Top Error Type | `/v1/insights/errors` | `by_type_global` → highest count key |
| Ranked error bars by agent | `/v1/insights/errors` | `by_agent[].agent_id`, `by_agent[].error_count` |
| Drilldown: By Error Type | `/v1/insights/errors?agent_id=X` | `by_agent[].by_type` |
| Drilldown: By Category | same | `by_agent[].by_category` |
| Drilldown: By Task | **⚠️ GAP** — see below | — |
| Drilldown: By Tool | **⚠️ GAP** — see below | — |

**⚠️ Gap: "Errors by Task" and "Errors by Tool"** — The mockup shows which tasks and tools produce errors for a given agent (e.g. "lint_check: 28 errors", "ast_parser: 24 errors"). The errors endpoint gives `by_type` and `by_category`, but not `by_task` or `by_action`. **Options:** (a) add `by_task` and `by_action` dicts to the errors endpoint response, (b) cross-reference `/v1/insights/actions?agent_id=X` using `total_failed` as a proxy, or (c) simplify the mockup to only show by-type and by-category drilldowns (which the API fully supports).

Option (b) is partially viable — `/v1/insights/actions` has `total_failed` per action, which gives "errors by tool." But it doesn't break down error *types* per tool.

---

### Section 4: Prompt Analysis — "Size, Frequency & Attribution"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Prompt table (ranked by size) | `GET /v1/insights/prompts?sort=tokens` | `calls[].name`, `avg_tokens_in`, `max_tokens_in`, `total_count` |
| Agent(s) column | same | `calls[].agents_using` |
| Task/Tool column | **⚠️ Partial** — see below | `calls[].primary_model` (model, not task/tool) |
| Est. Cost column | same | `calls[].total_cost` |
| Biggest prompt highlight | same | `biggest_prompt.name`, `biggest_prompt.max_tokens_in` |

**⚠️ Gap: "Task / Tool" column** — The mockup shows which task and tool generates each prompt (e.g. "diff_review · git_diff"). The prompts endpoint gives the LLM call `name`, `agents_using`, and `primary_model`, but doesn't tell you which action/task triggered that call. The call `name` (e.g. `draft_outreach_email`) is itself the closest proxy — it's essentially the prompt identifier. Should I treat the call name as sufficient, or do you want an explicit `triggered_by_action` field added to the prompts response?

---

### Section 5: Tool Usage Tracker — "Frequency, Timing & Patterns"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Usage summary pills (count + rate/hr) | `GET /v1/insights/actions` | `actions[].name`, `total_started`, `hourly_avg` |
| Hourly heatmap | **⚠️ GAP** — see below | — |
| Weekly aggregation table | **⚠️ GAP** — see below | — |
| Trend badges | **⚠️ Derived** | Compare current vs previous period (needs 2 calls or client math) |

**⚠️ Gap: Hourly heatmap** — The mockup shows a 24-column heatmap of calls per hour per tool. `/v1/insights/actions` gives `peak_hour` and `peak_count` but not the full hourly distribution per action. **Options:** (a) add an `hourly_buckets` array to each action in the actions endpoint, (b) use `/v1/insights/timeseries?metric=llm_calls` for fleet-wide hourly data (but it doesn't break down by tool), or (c) use `group_by=hour` (noted as "reserved for future" in the API) once implemented.

**⚠️ Gap: Weekly aggregation (day-by-day)** — The mockup shows Mon–Sun columns per tool. The timeseries endpoint gives hourly buckets which *could* be aggregated client-side into daily totals, but only for a single metric (not per-tool). Getting per-tool-per-day would require calling `/v1/insights/timeseries` once per tool with a filter — but there's no `action_name` filter on that endpoint. Same `group_by=hour` future feature would help here.

---

### Section 6: Skill Usage Tracker — "Who Uses What & When?"

| UI Component | Primary Endpoint | Fields Used |
|---|---|---|
| Skill table (uses, agents, duration, success rate) | `GET /v1/insights/actions` | `actions[].name`, `total_started`, `agents_using`, `success_rate` |
| Avg Duration column | **⚠️ GAP** | Not in actions endpoint |
| Peak Hours column | `/v1/insights/actions` | `peak_hour` (single value, not a range) |
| Hourly skill heatmap | Same gap as Section 5 | — |

**⚠️ Gap: "Skills" vs "Actions/Tools"** — The mockup distinguishes between "tools" (brave_search, pdf_reader) and "skills" (web_research, code_review, content_writing). The API has a single `/v1/insights/actions` endpoint covering actions. Are skills tracked as a separate event type, or are they a higher-level grouping of actions? If skills = task types, then `tasks_by_type` from `/v1/insights/agents` might be the source. If skills are their own thing, a new endpoint or a `type` filter on actions would be needed.

**⚠️ Gap: Duration per action** — The mockup shows avg duration per skill (3.2s, 8.1s, etc.). `/v1/insights/actions` doesn't include duration. `/v1/insights/agents` has `avg_task_duration_ms` but at agent level, not per-action. You'd need `avg_duration_ms` added to the actions endpoint.

---

## Summary of Gaps

| # | Gap | Severity | Recommended Fix |
|---|---|---|---|
| 1 | **Last event type** on Fleet Status | Low | Add `last_event_type` + `last_event_at` to `GET /v1/agents` |
| 2 | **Cost per action/task** | Low | Add `cost` to `/v1/insights/actions` response, or derive from `top_llm_calls` |
| 3 | **Errors by task/action** | Medium | Add `by_action` dict to `/v1/insights/errors` response |
| 4 | **Triggered-by-action** for prompts | Low | Treat call `name` as sufficient (it basically is the prompt identity) |
| 5 | **Hourly buckets per action** | Medium | Implement `group_by=hour` on `/v1/insights/actions`, or add `hourly_buckets[]` per action |
| 6 | **Daily/weekly aggregation per tool** | Medium | Same as #5, client can roll up hourly→daily |
| 7 | **Skills vs Actions distinction** | Decision needed | Clarify: are skills = task types, or a new tracking concept? |
| 8 | **Duration per action** | Low | Add `avg_duration_ms` to `/v1/insights/actions` |

Gaps 5 and 6 are the biggest — the heatmaps in Sections 5 and 6 need hourly granularity per tool/skill, which the current API doesn't provide. Everything else is either minor additions or client-side derivation.

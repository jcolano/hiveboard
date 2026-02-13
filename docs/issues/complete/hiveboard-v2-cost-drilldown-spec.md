# Feature Spec: Cost Explorer Drill-Down

**Feature name:** Cost Explorer Expandable Rows
**Priority:** High — core Cost Explorer interaction is broken (navigates away instead of drilling down)
**Depends on:** LLM Detail Modal (from `hiveboard-v2-llm-detail-modal-spec.md`)
**Backend changes:** None — uses existing `GET /v1/llm-calls` endpoint

---

## 1. Problem

In the Cost Explorer, clicking an agent name in "Cost by Agent" calls `openAgentDetail()`, which navigates the user away from Cost Explorer entirely and into the Dashboard's agent detail view. Clicking a model name in "Cost by Model" does nothing.

Both interactions are wrong. When a user is in Cost Explorer looking at "$0.57 spent on claude-sonnet-4-5," the natural next question is "which calls made up that $0.57?" — not "show me this agent's tasks and pipeline."

The cost investigation flow should be: **summary → grouped breakdown → individual calls → call detail (prompt/response)**. Every click should go deeper into cost data, never sideways into a different view.

---

## 2. Solution

Replace navigation-away behavior with **expandable row drill-down**. Clicking a row in either cost table toggles an expansion panel below that row showing the individual LLM calls that contribute to that row's totals.

The expansion panel is a mini-table of LLM calls, each with a "⤢ Details" button that opens the LLM Detail Modal (same component specced in `hiveboard-v2-llm-detail-modal-spec.md`).

---

## 3. Interaction Design

### 3.1 Cost by Agent — click a row

```
BEFORE (current):
┌──────────────┬───────┬───────────┬────────────┬─────────┬─────┐
│ AGENT        │ CALLS │ TOKENS IN │ TOKENS OUT │ COST    │     │
├──────────────┼───────┼───────────┼────────────┼─────────┼─────┤
│ main         │ 18    │ 93.1K     │ 5.5K       │ $0.35   │ ███ │  ← click → navigates to agent detail (BAD)
│ ag_6ce5uncd  │ 18    │ 48.0K     │ 5.2K       │ $0.21   │ ██  │
└──────────────┴───────┴───────────┴────────────┴─────────┴─────┘

AFTER (new):
┌──────────────┬───────┬───────────┬────────────┬─────────┬─────┐
│ AGENT        │ CALLS │ TOKENS IN │ TOKENS OUT │ COST    │     │
├──────────────┼───────┼───────────┼────────────┼─────────┼─────┤
│ ▾ main       │ 18    │ 93.1K     │ 5.5K       │ $0.35   │ ███ │  ← click → toggles expansion
├──────────────┴───────┴───────────┴────────────┴─────────┴─────┤
│  ┌─────────────────────────────────────────────────────────┐  │
│  │ LLM Calls for: main                     18 calls  $0.35│  │  ← expansion header
│  ├─────────────────────────────────────────────────────────┤  │
│  │ NAME              MODEL              TOK IN  TOK OUT    │  │
│  │                                      COST    DURATION   │  │
│  ├─────────────────────────────────────────────────────────┤  │
│  │ phase1_reasoning   claude-sonnet-4-5  9.6K    368       │  │
│  │                                      $0.034  5.5s    ⤢  │  │  ← ⤢ opens LLM Detail Modal
│  │ heartbeat_summary  claude-3-haiku     388     85        │  │
│  │                                      $0.000  1.1s    ⤢  │  │
│  │ phase1_reasoning   claude-sonnet-4-5  9.6K    388       │  │
│  │                                      $0.035  5.3s    ⤢  │  │
│  │ ... (15 more)                         Load more ▾       │  │  ← pagination
│  └─────────────────────────────────────────────────────────┘  │
├──────────────┬───────┬───────────┬────────────┬─────────┬─────┤
│ ag_6ce5uncd  │ 18    │ 48.0K     │ 5.2K       │ $0.21   │ ██  │  ← collapsed
└──────────────┴───────┴───────────┴────────────┴─────────┴─────┘
```

### 3.2 Cost by Model — click a row

Same pattern, but filtered by model instead of agent:

```
│ ▾ claude-sonnet-4-5-20250929  │ 30  │ 138.7K  │ 10.1K  │ $0.57  │ ████ │
├───────────────────────────────┴─────┴─────────┴────────┴────────┴──────┤
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │ LLM Calls using: claude-sonnet-4-5-20250929     30 calls  $0.57 │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │ NAME              AGENT            TOK IN  TOK OUT               │  │
│  │                                    COST    DURATION              │  │
│  ├──────────────────────────────────────────────────────────────────┤  │
│  │ phase1_reasoning   main            9.6K    388                   │  │
│  │                                    $0.035  5.3s              ⤢   │  │
│  │ phase1_reasoning   ag_6ce5uncd     4.6K    346                   │  │
│  │                                    $0.019  5.0s              ⤢   │  │
│  │ ...                                                              │  │
│  └──────────────────────────────────────────────────────────────────┘  │
```

### 3.3 Interaction rules

| Action | Result |
|---|---|
| Click an expanded row | Collapses it |
| Click a different row while one is expanded | Collapses the current, expands the new one (accordion) |
| Click "⤢" button on an LLM call | Opens LLM Detail Modal (prompt, response, metadata) |
| Click "Load more ▾" | Fetches next page of calls, appends to list |
| Click agent name inside an expanded model drill-down | Does NOT navigate — it's just a label. Navigation belongs on the Dashboard, not here |

### 3.4 Row visual changes

| State | Visual |
|---|---|
| Default (collapsed) | Normal row, cursor pointer, subtle hover background |
| Hovered | Background `var(--bg-hover)`, row gains a `▸` indicator on the left |
| Expanded | Background slightly tinted, `▾` indicator, expansion panel slides in below |
| Loading | Expansion panel shows a "Loading calls…" placeholder with subtle pulse animation |

---

## 4. API Integration

### 4.1 Data source

Use `GET /v1/llm-calls` — it already exists in the API spec and returns exactly what we need.

**For agent drill-down:**

```javascript
var data = await apiFetch('/v1/llm-calls', {
    agent_id: agentId,
    time_range: '1h',     // match the Cost Explorer's current time range
    sort: 'cost',          // most expensive calls first — most useful for cost investigation
    limit: 10,
});
```

**For model drill-down:**

```javascript
var data = await apiFetch('/v1/llm-calls', {
    model: modelName,
    time_range: '1h',
    sort: 'cost',
    limit: 10,
});
```

### 4.2 Response shape (from API spec)

```json
{
    "data": [
        {
            "event_id": "550e8401-...",
            "agent_id": "lead-qualifier",
            "task_id": "task_lead-4821",
            "timestamp": "2026-02-10T14:32:02.100Z",
            "call_name": "phase1_reasoning",
            "model": "claude-sonnet-4-20250514",
            "tokens_in": 1500,
            "tokens_out": 200,
            "cost": 0.003,
            "duration_ms": 1200,
            "prompt_preview": "You are analyzing a sales lead...",
            "response_preview": "{\"tool\": \"crm_search\", ...}"
        }
    ],
    "pagination": { "cursor": "...", "has_more": true }
}
```

### 4.3 Backend endpoint check

Confirm that `GET /v1/llm-calls` is implemented. If only `GET /v1/cost/calls` exists, use that instead — same data, same parameters. If neither is implemented yet, it's a straightforward query (the SQL is already in the data model spec, Section 5.9.4).

**Fallback:** If neither endpoint exists, use `GET /v1/events` with `payload_kind=llm_call` and `agent_id` or manual client-side filtering. Less clean but works without backend changes.

---

## 5. State Management

### 5.1 New state variables

```javascript
let costExpandedAgent = null;    // agent_id of currently expanded agent row, or null
let costExpandedModel = null;    // model name of currently expanded model row, or null
let costDrilldownData = [];      // array of LLM call objects for the expanded row
let costDrilldownCursor = null;  // pagination cursor for "Load more"
let costDrilldownLoading = false;
```

### 5.2 Toggle functions

```javascript
async function toggleCostAgentDrilldown(agentId) {
    // Accordion: collapse model if open
    costExpandedModel = null;

    if (costExpandedAgent === agentId) {
        // Collapse
        costExpandedAgent = null;
        costDrilldownData = [];
        costDrilldownCursor = null;
        renderCostExplorer();
        return;
    }

    // Expand
    costExpandedAgent = agentId;
    costDrilldownData = [];
    costDrilldownCursor = null;
    costDrilldownLoading = true;
    renderCostExplorer(); // shows loading state

    var data = await apiFetch('/v1/llm-calls', {
        agent_id: agentId,
        time_range: '1h',
        sort: 'cost',
        limit: 10,
    });

    costDrilldownLoading = false;
    if (data && data.data) {
        costDrilldownData = data.data;
        costDrilldownCursor = data.pagination && data.pagination.has_more ? data.pagination.cursor : null;
    }
    renderCostExplorer();
}

async function toggleCostModelDrilldown(modelName) {
    // Accordion: collapse agent if open
    costExpandedAgent = null;

    if (costExpandedModel === modelName) {
        costExpandedModel = null;
        costDrilldownData = [];
        costDrilldownCursor = null;
        renderCostExplorer();
        return;
    }

    costExpandedModel = modelName;
    costDrilldownData = [];
    costDrilldownCursor = null;
    costDrilldownLoading = true;
    renderCostExplorer();

    var data = await apiFetch('/v1/llm-calls', {
        model: modelName,
        time_range: '1h',
        sort: 'cost',
        limit: 10,
    });

    costDrilldownLoading = false;
    if (data && data.data) {
        costDrilldownData = data.data;
        costDrilldownCursor = data.pagination && data.pagination.has_more ? data.pagination.cursor : null;
    }
    renderCostExplorer();
}

async function loadMoreCostDrilldown() {
    if (!costDrilldownCursor || costDrilldownLoading) return;
    costDrilldownLoading = true;

    var params = { time_range: '1h', sort: 'cost', limit: 10, cursor: costDrilldownCursor };
    if (costExpandedAgent) params.agent_id = costExpandedAgent;
    if (costExpandedModel) params.model = costExpandedModel;

    var data = await apiFetch('/v1/llm-calls', params);
    costDrilldownLoading = false;

    if (data && data.data) {
        costDrilldownData = costDrilldownData.concat(data.data);
        costDrilldownCursor = data.pagination && data.pagination.has_more ? data.pagination.cursor : null;
    }
    renderCostExplorer();
}
```

---

## 6. Rendering Changes to `renderCostExplorer()`

### 6.1 Cost by Model table — replace current model row rendering

Current code builds model rows with no click handler. Replace with:

```javascript
byModel.forEach(m => {
    var modelName = m.model || '—';
    var isExpanded = costExpandedModel === modelName;
    var chevron = isExpanded ? '▾' : '▸';
    var rowClass = isExpanded ? 'cost-row-expanded' : '';

    // Model row (now clickable)
    html += `<tr class="cost-row clickable ${rowClass}" onclick="toggleCostModelDrilldown('${escHtml(modelName)}')">
        <td><span class="cost-row-chevron">${chevron}</span><span class="model-badge">${escHtml(modelName)}</span></td>
        <td>${m.call_count || 0}</td>
        <td>${fmtTokens(m.tokens_in)}</td>
        <td>${fmtTokens(m.tokens_out)}</td>
        <td style="color: var(--llm); font-weight: 600;">${costStr}</td>
        <td style="width: 100px;"><div class="cost-bar" style="width: ${pct}%"></div></td>
    </tr>`;

    // Expansion panel (if this model is expanded)
    if (isExpanded) {
        html += `<tr class="cost-drilldown-row"><td colspan="6">${renderCostDrilldownPanel('model', modelName)}</td></tr>`;
    }
});
```

### 6.2 Cost by Agent table — replace current agent row rendering

Current code has `onclick="openAgentDetail('${agentId}')"`. Replace with:

```javascript
sortedAgents.forEach(a => {
    var agentId = a.agent_id || a.agent || '—';
    var isExpanded = costExpandedAgent === agentId;
    var chevron = isExpanded ? '▾' : '▸';
    var rowClass = isExpanded ? 'cost-row-expanded' : '';

    // Agent row (now clickable for drill-down, NOT navigation)
    html += `<tr class="cost-row clickable ${rowClass}" onclick="toggleCostAgentDrilldown('${escHtml(agentId)}')">
        <td><span class="cost-row-chevron">${chevron}</span><span style="color: var(--accent);">${escHtml(agentId)}</span></td>
        <td>${a.call_count || 0}</td>
        <td>${fmtTokens(a.tokens_in)}</td>
        <td>${fmtTokens(a.tokens_out)}</td>
        <td style="color: var(--llm); font-weight: 600;">${costStr}</td>
        <td style="width: 100px;"><div class="cost-bar" style="width: ${pct}%"></div></td>
    </tr>`;

    // Expansion panel
    if (isExpanded) {
        html += `<tr class="cost-drilldown-row"><td colspan="6">${renderCostDrilldownPanel('agent', agentId)}</td></tr>`;
    }
});
```

### 6.3 Drill-down panel renderer

```javascript
function renderCostDrilldownPanel(filterType, filterValue) {
    // Loading state
    if (costDrilldownLoading && costDrilldownData.length === 0) {
        return '<div class="cost-drilldown"><div class="cost-drilldown-loading">Loading calls…</div></div>';
    }

    // Empty state
    if (costDrilldownData.length === 0) {
        return '<div class="cost-drilldown"><div class="cost-drilldown-empty">No individual LLM calls found</div></div>';
    }

    var label = filterType === 'agent'
        ? 'LLM Calls for: ' + escHtml(filterValue)
        : 'LLM Calls using: ' + escHtml(filterValue);
    var totalCost = costDrilldownData.reduce(function(sum, c) { return sum + (c.cost || 0); }, 0);

    var html = '<div class="cost-drilldown">';

    // Header
    html += `<div class="cost-drilldown-header">
        <div class="cost-drilldown-title">${label}</div>
        <div class="cost-drilldown-summary">${costDrilldownData.length} calls · $${totalCost.toFixed(3)}</div>
    </div>`;

    // Calls table
    html += '<table class="cost-drilldown-table"><thead><tr>';
    html += '<th>Name</th>';
    html += filterType === 'model' ? '<th>Agent</th>' : '<th>Model</th>';
    html += '<th>Tokens In</th><th>Tokens Out</th><th>Cost</th><th>Duration</th><th>Time</th><th></th>';
    html += '</tr></thead><tbody>';

    costDrilldownData.forEach(function(call) {
        var hasPrompt = call.prompt_preview || call.response_preview;
        html += `<tr>
            <td class="cost-call-name">${escHtml(call.call_name || '—')}</td>
            <td>${filterType === 'model'
                ? '<span style="color:var(--accent)">' + escHtml(call.agent_id || '—') + '</span>'
                : '<span class="model-badge">' + escHtml(call.model || '—') + '</span>'
            }</td>
            <td>${fmtTokens(call.tokens_in)}</td>
            <td>${fmtTokens(call.tokens_out)}</td>
            <td style="color:var(--llm);font-weight:600;">${call.cost != null ? '$' + call.cost.toFixed(4) : '—'}</td>
            <td>${call.duration_ms != null ? fmtDuration(call.duration_ms) : '—'}</td>
            <td style="color:var(--text-muted)">${timeAgo(call.timestamp)}</td>
            <td><span class="stream-detail-btn" onclick="event.stopPropagation(); openLlmModalFromCostDrilldown(${JSON.stringify(call).split("'").join("\\'")})"
                title="${hasPrompt ? 'View prompt & response' : 'View call details'}">⤢</span></td>
        </tr>`;
    });

    html += '</tbody></table>';

    // Load more
    if (costDrilldownCursor) {
        html += `<div class="cost-drilldown-more" onclick="event.stopPropagation(); loadMoreCostDrilldown()">
            ${costDrilldownLoading ? 'Loading…' : 'Load more ▾'}
        </div>`;
    }

    html += '</div>';
    return html;
}
```

### 6.4 Modal trigger from drill-down

```javascript
function openLlmModalFromCostDrilldown(call) {
    // Parse if string (from inline JSON)
    if (typeof call === 'string') {
        try { call = JSON.parse(call); } catch(e) { return; }
    }
    openLlmModal({
        name: call.call_name || call.name || 'LLM Call',
        model: call.model,
        tokens_in: call.tokens_in,
        tokens_out: call.tokens_out,
        cost: call.cost,
        duration_ms: call.duration_ms,
        prompt_preview: call.prompt_preview || null,
        response_preview: call.response_preview || null,
        metadata: call.metadata || null,
        agent_id: call.agent_id,
        task_id: call.task_id,
        event_id: call.event_id,
        timestamp: call.timestamp,
    });
}
```

**Note on JSON in onclick:** Inline JSON in onclick attributes is fragile for complex data. A cleaner approach is to store `costDrilldownData` (already in memory) and pass the index:

```javascript
// In the table row:
onclick="event.stopPropagation(); openLlmModalFromCostDrilldown(${idx})"

// In the function:
function openLlmModalFromCostDrilldown(idx) {
    var call = costDrilldownData[idx];
    if (!call) return;
    openLlmModal({ ... });
}
```

This is simpler and avoids escaping issues. **Recommended approach.**

---

## 7. Sort Controls (Enhancement)

When expanded, the drill-down table should support re-sorting by clicking column headers:

| Column clicked | API param | Behavior |
|---|---|---|
| Cost | `sort: 'cost'` | Most expensive first (default) |
| Tokens In | `sort: 'tokens'` | Highest token usage first |
| Time | `sort: 'newest'` | Most recent first |

This requires re-fetching from the API with the new sort param. Store the current sort in state:

```javascript
let costDrilldownSort = 'cost'; // default
```

Clicking a column header updates `costDrilldownSort` and re-triggers the fetch. Keep it simple — no multi-column sort, no ascending toggle for v1.

---

## 8. CSS

```css
/* ─── Cost Explorer Drill-Down ─── */

.cost-row {
    transition: background 0.1s;
}

.cost-row.clickable {
    cursor: pointer;
}

.cost-row.clickable:hover {
    background: var(--bg-hover);
}

.cost-row-expanded {
    background: var(--accent-dim);
}

.cost-row-expanded:hover {
    background: var(--accent-hover) !important;
}

.cost-row-chevron {
    display: inline-block;
    width: 16px;
    color: var(--text-muted);
    font-size: 10px;
}

.cost-drilldown-row td {
    padding: 0 !important;
    background: var(--bg-deep);
}

.cost-drilldown {
    padding: 16px 20px;
}

.cost-drilldown-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
}

.cost-drilldown-title {
    font-family: var(--font-sans);
    font-size: 13px;
    font-weight: 600;
    color: var(--text-primary);
}

.cost-drilldown-summary {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text-muted);
}

.cost-drilldown-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
}

.cost-drilldown-table th {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.3px;
    color: var(--text-muted);
    padding: 6px 10px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    cursor: pointer;
}

.cost-drilldown-table th:hover {
    color: var(--text-primary);
}

.cost-drilldown-table td {
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border-subtle);
    vertical-align: middle;
}

.cost-drilldown-table tr:hover {
    background: var(--bg-hover);
}

.cost-drilldown-table tr:last-child td {
    border-bottom: none;
}

.cost-call-name {
    font-family: var(--font-sans);
    font-weight: 500;
    color: var(--text-primary);
}

.cost-drilldown-loading {
    font-family: var(--font-sans);
    font-size: 13px;
    color: var(--text-muted);
    text-align: center;
    padding: 24px;
    animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
    0%, 100% { opacity: 0.5; }
    50% { opacity: 1; }
}

.cost-drilldown-empty {
    font-family: var(--font-sans);
    font-size: 13px;
    color: var(--text-muted);
    text-align: center;
    padding: 24px;
}

.cost-drilldown-more {
    font-family: var(--font-sans);
    font-size: 12px;
    color: var(--accent);
    text-align: center;
    padding: 10px;
    cursor: pointer;
    border-top: 1px solid var(--border-subtle);
    margin-top: 8px;
}

.cost-drilldown-more:hover {
    background: var(--accent-dim);
    border-radius: var(--radius-sm);
}
```

---

## 9. Edge Cases

| Scenario | Behavior |
|---|---|
| Agent has 0 LLM calls | Expansion shows "No individual LLM calls found" |
| API returns error | Show "Failed to load calls" with retry link |
| Model name contains special chars | `escHtml()` the model name in both the onclick parameter and display |
| Cost Explorer refreshes while expanded | Preserve expansion state; re-render with same `costExpandedAgent`/`costExpandedModel` |
| User switches away from Cost view and back | Clear expansion state on view switch (`switchView('cost')` resets drill-down) |
| 100+ calls for a single agent | Paginate with "Load more" (10 at a time). Never load all at once |
| `prompt_preview` is null for all calls | "⤢" button still works — modal shows "No prompt captured" empty state |

---

## 10. What We're NOT Doing

To keep scope clear:

- **No cross-table drill-down.** Clicking a model inside an agent expansion does NOT further filter. That would require a matrix drill-down which adds complexity without proportional value.
- **No inline prompt preview.** The expansion shows call metadata only. Prompts are in the modal (one click deeper). Keeping expansions lightweight avoids overwhelming the cost table.
- **No cost timeseries chart.** The expansion is a table, not a chart. The timeseries endpoint (`GET /v1/cost/timeseries`) could feed a sparkline or chart in a future iteration.
- **No navigation links.** Agent names and task IDs in the expansion panel are display-only labels, not clickable navigation. The user is investigating cost; don't pull them out of that flow.

---

## 11. Testing Checklist

- [ ] Clicking agent row in "Cost by Agent" expands drill-down below (no navigation)
- [ ] Clicking model row in "Cost by Model" expands drill-down below
- [ ] Clicking an expanded row collapses it
- [ ] Expanding a different row collapses the previous one (accordion)
- [ ] Drill-down shows loading state while fetching
- [ ] Drill-down shows empty state when no calls found
- [ ] LLM calls are sorted by cost (most expensive first) by default
- [ ] "⤢" button on each call opens LLM Detail Modal
- [ ] Modal shows prompt/response when available
- [ ] "Load more" fetches next page and appends
- [ ] "Load more" disappears when no more pages
- [ ] Switching away from Cost view and back clears expansion state
- [ ] Expansion panel renders correctly for agents with 1 call, 10 calls, 50+ calls
- [ ] Model names with special characters render and filter correctly

---

## 12. Files to Modify

| File | Changes |
|---|---|
| `hiveboard-v2.js` | New state vars, `toggleCostAgentDrilldown()`, `toggleCostModelDrilldown()`, `loadMoreCostDrilldown()`, `renderCostDrilldownPanel()`, `openLlmModalFromCostDrilldown()`. Modify `renderCostExplorer()` agent and model row rendering. Clear state in `switchView()` |
| `hiveboard-v2.css` | Add ~80 lines: `.cost-row`, `.cost-drilldown`, `.cost-drilldown-table`, loading/empty states |
| Backend | None — uses existing `GET /v1/llm-calls` endpoint. Verify it's implemented; if not, `GET /v1/cost/calls` or `GET /v1/events?payload_kind=llm_call` as fallback |

---

## 13. Relationship to LLM Detail Modal Spec

This feature **depends on** the LLM Detail Modal from `hiveboard-v2-llm-detail-modal-spec.md`. The "⤢" button on each drill-down row calls `openLlmModal()` which is defined in that spec.

**Implementation order:**
1. LLM Detail Modal (standalone component)
2. Cost Explorer Drill-Down (uses the modal)
3. Tree view click-to-modal and Stream "⤢ Details" button (also use the modal)

All three features share the same modal. Build it once, wire it three times.

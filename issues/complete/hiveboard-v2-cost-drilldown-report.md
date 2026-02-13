# Cost Explorer Drill-Down — Implementation Report

**Spec:** `hiveboard-v2-cost-drilldown-spec.md`
**Status:** Complete
**Date:** 2026-02-12
**Files modified:** 4 (`models.py`, `storage_json.py`, `hiveboard.js`, `hiveboard.css`)

---

## What Was Built

Replaced the broken Cost Explorer row interactions — clicking an agent used to navigate away to the Dashboard agent detail view, and clicking a model did nothing — with inline accordion drill-down panels. Both "Cost by Agent" and "Cost by Model" rows now expand to show individual LLM calls, with each call linking to the existing LLM Detail Modal. Pagination ("Load more") is supported for agents/models with many calls.

---

## Changes By File

### `src/shared/models.py`

**`LlmCallRecord` (line 572-573):**
Added two optional fields so the LLM Detail Modal can display prompt/response content when opened from drill-down rows:
- `prompt_preview: str | None = None`
- `response_preview: str | None = None`

### `src/backend/storage_json.py`

**`get_cost_calls()` (lines 1351-1352):**
Extract the two new fields from the event `data` dict when building each `LlmCallRecord`:
```python
prompt_preview=data.get("prompt_preview"),
response_preview=data.get("response_preview"),
```

No endpoint handler changes were needed — the Pydantic model auto-serializes the new fields.

### `src/static/js/hiveboard.js`

**New global state (after line 51):**
- `costExpandedAgent` — agent_id of currently expanded agent row, or null
- `costExpandedModel` — model name of currently expanded model row, or null
- `costDrilldownData` — array of LLM call objects for the expanded row
- `costDrilldownCursor` — pagination cursor for "Load more"
- `costDrilldownLoading` — loading state flag

**Modified `renderCostExplorer()` — model rows:**
Changed from static `<tr>` to clickable rows with chevron indicator (`▸`/`▾`) and `onclick="toggleCostModelDrilldown('...')"`. When expanded, a `cost-drilldown-row` with the expansion panel is injected below the row.

**Modified `renderCostExplorer()` — agent rows:**
Replaced `onclick="openAgentDetail('...')"` navigation with `onclick="toggleCostAgentDrilldown('...')"`. Same clickable row + chevron + conditional expansion pattern as model rows.

**New functions (5 total, ~100 lines):**

| Function | Purpose |
|---|---|
| `toggleCostAgentDrilldown(agentId)` | Accordion toggle for agent rows. Fetches from `/v1/llm-calls?agent_id=...&since=...&limit=10`. Collapses any expanded model row. |
| `toggleCostModelDrilldown(modelName)` | Same pattern with `model=...` filter. Collapses any expanded agent row. |
| `loadMoreCostDrilldown()` | Pagination — fetches next page via cursor, appends results to `costDrilldownData` |
| `renderCostDrilldownPanel(filterType, filterValue)` | Builds expansion HTML: header with label + cost summary, mini-table of calls (time, name, model/agent, tokens, cost, expand icon), loading/empty states, "Load more" button |
| `openLlmModalFromCostDrilldown(idx)` | Looks up `costDrilldownData[idx]` by index, calls existing `openLlmModal()` |

**Modified `switchView()` — `'cost'` case:**
Added state reset before fetching to clear any lingering expansion:
```javascript
costExpandedAgent = null;
costExpandedModel = null;
costDrilldownData = [];
costDrilldownCursor = null;
```

### `src/static/css/hiveboard.css`

Added ~95 lines of drill-down styles after the existing cost styles:

- `.cost-row.clickable` — cursor pointer, subtle hover background transition
- `.cost-row-expanded` — tinted purple background for the expanded row
- `.cost-row-chevron` — inline 16px-wide chevron indicator
- `.cost-drilldown-row td` — zero-padding wrapper for expansion panel
- `.cost-drilldown` — padded container with subtle purple tint and top border
- `.cost-drilldown-header` — flex row with label + cost summary
- `.cost-drilldown-table` — compact mini-table (12px font, 11px uppercase headers)
- `.cost-drilldown-table tbody tr:hover` — hover highlight on individual call rows
- `.cost-drilldown-loading` — centered text with `pulse` keyframe animation
- `.cost-drilldown-empty` — muted centered text for empty state
- `.cost-drilldown-more` — styled "Load more" button with purple theme and hover state

---

## Interaction Model

| Action | Result |
|---|---|
| Click agent row in "Cost by Agent" | Expands drill-down panel below showing individual LLM calls (no navigation) |
| Click model row in "Cost by Model" | Same drill-down behavior filtered by model |
| Click an expanded row | Collapses it |
| Click a different row while one is expanded | Collapses the current, expands the new one (accordion) |
| Click "⤢" on an LLM call row | Opens LLM Detail Modal with full prompt/response |
| Click "Load more" | Fetches next page of calls, appends to list |
| Switch away from Cost view and back | Expansion state cleared |

---

## API Integration

Drill-down fetches use the existing `GET /v1/llm-calls` endpoint with a 1-hour `since` window matching the Cost Explorer's time range:

```javascript
var since = new Date(Date.now() - 3600000).toISOString();
var resp = await apiFetch('/v1/llm-calls', { agent_id: agentId, since: since, limit: 10 });
```

Pagination uses the `cursor` field from `resp.pagination` for subsequent requests.

---

## Spec Compliance Checklist

| Spec Item | Status |
|---|---|
| Clicking agent row expands drill-down (no navigation away) | Done |
| Clicking model row expands drill-down | Done |
| Clicking expanded row collapses it | Done |
| Accordion behavior (only one expansion at a time) | Done |
| Loading state with pulse animation while fetching | Done |
| Empty state when no calls found | Done |
| Each call row shows time, name, model/agent, tokens, cost | Done |
| "⤢" on each call opens LLM Detail Modal | Done |
| Modal receives prompt_preview and response_preview | Done |
| "Load more" button for pagination | Done |
| "Load more" hidden when no more pages | Done |
| Switching views clears expansion state | Done |
| Model names with special chars escaped via `escHtml()` | Done |
| Chevron indicator (▸ collapsed, ▾ expanded) | Done |
| Sort controls for drill-down columns | Deferred (spec Section 7 marked as enhancement) |

---

## Testing Notes

- JS syntax validated with `node -c` — passes
- `LlmCallRecord` model verified: new fields serialize correctly
- 62/62 storage tests pass (1 pre-existing Windows file-lock error in `test_authenticate_invalid_hash`, unrelated)
- Visual/interactive testing requires running the server with the simulator and using a browser

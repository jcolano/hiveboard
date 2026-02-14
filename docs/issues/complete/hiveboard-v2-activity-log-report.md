# Human-Readable Activity Log — Implementation Report

**Spec:** `hiveboard-v2-activity-log-spec.md`
**Status:** Complete
**Date:** 2026-02-14
**Files modified:** 3 (`hiveboard.js`, `hiveboard.css`, `index.html`)

---

## What Was Built

A narrative log panel added to the right sidebar, sitting above the existing raw activity stream. Events are rendered as human-readable sentences via deterministic string templates — no LLM, no backend changes. The sidebar is now split: Narrative (~40% top) and Activity (~60% bottom), each scrolling independently.

---

## Changes By File

### `src/static/index.html`

Replaced the `.stream-panel` interior (lines 170-181) with a two-panel layout:

- **Narrative panel** (`.narrative-panel`) — contains `.sub-panel-header` with "Narrative" title + Live badge, and `#narrativeList` div
- **Stream divider** (`.stream-divider`) — 1px visual separator
- **Raw stream panel** (`.raw-stream-panel`) — contains `.sub-panel-header` with "Activity" title + event count, stream filters, and `#streamList`

The original `.panel-header` was removed since each sub-panel now has its own header. The `#eventCount` element moved into the raw stream sub-panel header.

### `src/static/js/hiveboard.js`

**New constants (after line 70):**

| Constant | Purpose |
|---|---|
| `EVENT_TEMPLATES` | 13 templates keyed by `event_type` (agent_registered, task_started, task_completed, task_failed, action_started, action_completed, action_failed, retry_started, escalated, approval_requested, approval_received, heartbeat, custom) |
| `KIND_TEMPLATES` | 11 templates keyed by `payload.kind` with optional `:action` suffix (llm_call, issue, issue:resolved, queue_snapshot, todo, todo:completed, plan_created, plan_step:started, plan_step:completed, plan_step:failed, scheduled) |

**New functions:**

| Function | Purpose |
|---|---|
| `shortModelName(model)` | Strips date suffixes from model names (e.g., `gpt-4o-2024-08-06` -> `gpt-4o`) |
| `interpolateTemplate(template, vals)` | 3-pass template interpolation: (1) parenthesized groups dropped when key is null, (2) connector+placeholder dropped when null, (3) remaining placeholders filled or cleared. Collapses whitespace. |
| `formatEventSentence(e)` | Selects template (kind:action > kind > event_type > fallback), builds escaped placeholder values, interpolates, wraps agent/task names as clickable HTML spans. Returns trusted HTML. |
| `renderNarrativeLog()` | Renders the narrative panel from `getFilteredStream()` results using `formatEventSentence()` |

**Modified functions:**

| Function | Change |
|---|---|
| `fetchEvents()` | Added 9 fields to event objects: `taskType`, `action`, `issueSeverity`, `todoAction`, `stepIndex`, `totalSteps`, `stepCount`, `scheduledCount`, `framework` |
| `handleWsMessage()` | Same 9 additional field extractions as `fetchEvents()` |
| `renderStream()` | Added `renderNarrativeLog()` call at end (both normal path and empty-state early return) so both panels update together |

### `src/static/css/hiveboard.css`

Added ~70 lines of styles after `.stream-panel`:

- `.narrative-panel` — `flex: 0 0 40%`, flex column, overflow hidden
- `.sub-panel-header` — shared header style for both sub-panels (flex row, 10px 14px padding, border-bottom)
- `.sub-panel-title` — 12px uppercase, 600 weight, muted color
- `.narrative-list` — flex: 1, overflow-y auto, 6px 10px padding, custom scrollbar
- `.narrative-event` — flex row with space-between, 12px font, border-bottom subtle
- `.narrative-sentence` — word-break: break-word, flex: 1
- `.narrative-time` — 10px muted, nowrap, flex-shrink: 0
- `.stream-divider` — 1px height, border color background
- `.raw-stream-panel` — flex: 1, flex column, min-height: 0

---

## Template Resolution

Templates are resolved in priority order:

```
1. KIND_TEMPLATES[kind + ':' + action]   (e.g., "plan_step:completed")
2. KIND_TEMPLATES[kind]                   (e.g., "llm_call")
3. EVENT_TEMPLATES[event_type]            (e.g., "task_completed")
4. Fallback: escHtml(e.summary || e.type)
```

---

## Clause Omission

When a placeholder value is null, the surrounding clause is dropped cleanly:

| Template | Missing field | Output |
|---|---|---|
| `{agent} completed {task} in {duration}` | `durationMs=null` | `scout completed lead-42` |
| `{agent} called {model} for {llmName} ({tokensIn} tokens)` | `tokensIn=null` | `scout called gpt-4o for scoring` |
| `{agent} registered ({framework})` | `framework=null` | `scout registered` |
| `{agent} failed on {summary}: {error}` | `error=null` | `scout failed on enrichment` |

---

## Verified Behavior

| Check | Result |
|---|---|
| Dashboard serves with two-panel sidebar | OK |
| HTML has narrative-panel, stream-divider, raw-stream-panel | OK |
| CSS serves all new style rules (10 selectors) | OK |
| JS serves all new functions (12 references) | OK |
| JS syntax validation (Node `new Function()`) | OK |
| Template interpolation produces clean sentences | OK |
| Missing fields — no dangling connectors ("in", "for", ":") | OK |
| Parenthesized groups dropped when null | OK |
| Agent names clickable via `selectAgent()` | OK |
| Task IDs clickable via `selectTask()` | OK |
| Stream filters affect both panels (shared `getFilteredStream()`) | OK |
| WebSocket events appear in both panels (`renderStream()` calls `renderNarrativeLog()`) | OK |
| Long summaries truncated at 120 chars with ellipsis | OK |
| BrightPath simulator generates events for all 6 agents | OK |

---

## Spec Deviations

The implementation deviated from the spec in one design decision:

| Spec says | Implementation does | Rationale |
|---|---|---|
| Replace the summary line in the raw activity stream with `formatEventSentence()` | Added narrative as a **separate panel on top**; raw stream unchanged | Per planning discussion, adding the narrative panel on top preserves the raw stream for developer debugging while giving ops an at-a-glance narrative view. Both panels update from the same data and filters. |

All other spec items (template registries, interpolation logic, field extraction, clickable entities, clause omission, edge cases) are implemented as specified.

---

## No New State Variables

The feature adds no mutable state. `EVENT_TEMPLATES`, `KIND_TEMPLATES` are constants. `formatEventSentence()`, `interpolateTemplate()`, and `shortModelName()` are stateless pure functions. `renderNarrativeLog()` reads from the existing `STREAM_EVENTS` array via `getFilteredStream()`.

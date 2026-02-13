# LLM Detail Modal — Implementation Report

**Spec:** `hiveboard-v2-llm-detail-modal-spec.md`
**Status:** Complete
**Date:** 2026-02-12
**Files modified:** 3 (`hiveboard.js`, `hiveboard.css`, `index.html`)

---

## What Was Built

A single LLM Detail Modal component accessible from all three data surfaces in the dashboard. Clicking any LLM-related element now opens a rich modal showing prompt/response previews, token stats, cost, duration, and metadata.

---

## Changes By File

### `src/static/js/hiveboard.js`

**New global state (lines 50-51):**
- `llmModalOpen` — tracks whether modal is visible
- `llmModalData` — holds the current modal's data object

**New functions (lines 1424-1648):**

| Function | Purpose |
|---|---|
| `openLlmModal(data)` | Sets modal state and calls render |
| `closeLlmModal()` | Hides overlay, clears state |
| `renderLlmModal()` | Builds full modal HTML — header, stats row, token ratio bar, prompt section, response section (with JSON pretty-print), collapsible metadata, context row |
| `copyToClipboard(text)` | Clipboard API write + toast notification |
| `openLlmDetailFromTree(el)` | Trigger 1: tree view LLM node click — walks action tree by `data-node-id` attribute, opens modal |
| `openActionDetailFromTree(el)` | Non-LLM tree nodes — shows action_id, status, duration, error, summary in pinned detail panel |
| `openLlmDetailFromStream(eventId)` | Trigger 3: activity stream "Details" button — looks up cached stream event, opens modal |

**Escape key handler (line 1643-1647):**
- `document.addEventListener('keydown', ...)` closes modal on Escape

**Modified functions:**

| Function | Change |
|---|---|
| `fetchTimeline()` | Stores `promptPreview`, `responsePreview`, `llmMetadata`, `llmName`, `eventId`, `agentId`, `taskId`, `timestamp` on flat nodes. Enriches action tree with LLM pseudo-children carrying `prompt_preview`, `response_preview`, `metadata`, `event_id`, `agent_id`, `task_id`, `timestamp`. |
| `fetchEvents()` | Stores `promptPreview`, `responsePreview`, `llmMetadata`, `llmName` on stream events |
| `handleWsMessage()` | Same additions as `fetchEvents()` for live WebSocket events |
| `pinNode(idx)` | LLM nodes (`kind === 'llm_call'` or `type === 'llm'`) now open the modal instead of the pinned detail panel. Non-LLM nodes unchanged. |
| `renderActionTreeNode()` | All tree nodes are now clickable. LLM nodes get `llm-clickable` class + `onclick="openLlmDetailFromTree(this)"`. Non-LLM action nodes get `action-clickable` class + `onclick="openActionDetailFromTree(this)"`. LLM rows show a "details" hint on hover. |
| `buildStreamDetailTags()` | LLM events now include a `<span class="stream-detail-btn">` with "Details" text that triggers `openLlmDetailFromStream()` |

### `src/static/css/hiveboard.css`

Added ~200 lines of modal styles:

- `.llm-modal-overlay` / `.llm-modal-overlay.visible` — fixed overlay with backdrop blur
- `.llm-modal` — centered container, 680px max-width, 80vh max-height, internal scroll
- `.llm-modal-header` / `.llm-modal-name` / `.llm-modal-model` / `.llm-modal-close` — header with name, model tag, close button
- `.llm-modal-stats` / `.llm-modal-stat` — 4-column stats row (tokens in, tokens out, cost, duration)
- `.llm-modal-ratio` / `.llm-ratio-bar.in` / `.llm-ratio-bar.out` — token ratio visualization bar
- `.llm-modal-section` / `.llm-modal-section-header` / `.llm-modal-section-label` — section containers for prompt/response
- `.llm-modal-copy` — copy buttons
- `.llm-modal-preview` — monospace, pre-wrap, scrollable code block (max-height 200px)
- `.llm-modal-empty` — italic muted text for missing data
- `.llm-modal-section.collapsible` / `.expanded` / `.llm-meta-content` — collapsed metadata section
- `.llm-meta-row` / `.llm-meta-key` / `.llm-meta-val` — metadata key-value rows
- `.llm-modal-context` — context row with agent/task links
- `.stream-detail-btn` — purple "Details" button in activity stream
- `.tree-node-row.llm-clickable` / `.tree-node-row.action-clickable` — hover states for tree nodes
- `.tree-expand-hint` — fade-in "details" label on LLM tree node hover

### `src/static/index.html`

Added modal overlay element before `</body>`:
```html
<div class="llm-modal-overlay" id="llmModalOverlay" onclick="if(event.target===this) closeLlmModal()">
    <div class="llm-modal" id="llmModalContent"></div>
</div>
```

---

## Three Trigger Points

| # | Surface | Element | Action |
|---|---|---|---|
| 1 | **Tree view** | Click LLM node row (purple diamond nodes) | Opens modal with data from enriched action tree node |
| 2 | **Flat timeline** | Click purple LLM dot | Opens modal with data from flat node object |
| 3 | **Activity stream** | Click "Details" button on LLM event cards | Opens modal with data from cached stream event |

Non-LLM tree nodes open the pinned detail panel instead. Non-LLM flat timeline nodes retain their existing pinned detail behavior.

---

## Data Flow

The data pipeline was extended so that prompt/response previews are stored at fetch time (Option B from the spec — no extra API call needed):

```
API response (payload.data)
  ├─ fetchTimeline() → flat nodes carry promptPreview, responsePreview, llmMetadata
  │                   → action tree LLM pseudo-children carry prompt_preview, response_preview, metadata
  ├─ fetchEvents()   → stream events carry promptPreview, responsePreview, llmMetadata
  └─ handleWsMessage() → live WS events carry same fields
```

The modal opens instantly from all three surfaces with no loading state.

---

## Modal Features

- **Stats row:** Tokens In, Tokens Out, Cost, Duration — each shows "—" when null
- **Token ratio bar:** Visual comparison of input vs output tokens; hidden when either is null
- **Prompt section:** Monospace scrollable block with Copy button; shows "No prompt captured" fallback
- **Response section:** Same as prompt; JSON responses are auto-detected and pretty-printed
- **Metadata:** Collapsed by default; click to expand key-value pairs; hidden entirely when empty
- **Context row:** Clickable agent and task IDs that close the modal and navigate to the entity
- **Close:** Click backdrop, click X button, or press Escape

---

## Spec Compliance Checklist

| Spec Item | Status |
|---|---|
| Modal opens from tree view LLM node click | Done |
| Modal opens from flat timeline LLM dot click | Done |
| Modal opens from stream card "Details" button | Done |
| Prompt preview text displayed when available | Done |
| "No prompt captured" shown when null | Done |
| JSON responses pretty-printed | Done |
| Copy buttons for prompt and response | Done |
| Metadata collapsed by default, expands on click | Done |
| Agent/Task links close modal and navigate | Done |
| Escape key closes modal | Done |
| Backdrop click closes modal | Done |
| Modal scrolls internally when content exceeds 80vh | Done |
| Non-LLM flat nodes still open pinned detail | Done |
| Non-LLM tree nodes open pinned detail panel | Done |
| Tree LLM nodes show hover affordance | Done |

---

## Testing Notes

- JS syntax validated with `node -c` — passes
- 62/62 storage tests pass
- 33 API test failures are pre-existing (`'State' object has no attribute 'pricing'` — test fixture doesn't initialize `LlmPricingEngine` via lifespan), unrelated to these changes
- Visual/interactive testing requires running the server with the simulator and using a browser

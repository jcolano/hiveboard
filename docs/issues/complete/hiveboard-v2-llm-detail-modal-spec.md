# Feature Spec: LLM Detail Modal

**Feature name:** LLM Detail Modal (universal event inspector)
**Priority:** High — addresses prompt/response visibility gap across all three data surfaces
**Depends on:** Issues 1-3 from `hiveboard-v2-issues.md` (can be built in parallel)

---

## 1. Problem

LLM prompt and response previews (`prompt_preview`, `response_preview`) are the single most valuable debugging signal — they answer "what did the agent actually say to the model, and what came back?" Today this data is:

- **Only accessible from flat timeline** → click a purple LLM dot → pinned detail panel dumps all payload fields as raw key-value pairs
- **Not accessible from tree view** → tree nodes show inline model/tokens/cost but have no click-to-expand
- **Not accessible from activity stream** → rich cards show model/tokens/cost tags but no way to see the actual prompt/response content

The pinned detail panel is also poorly suited for reading prompt text — it's a narrow key-value layout that truncates long strings. Prompts need a proper reading container.

---

## 2. Solution

A single **LLM Detail Modal** component that can be triggered from any of the three surfaces. One component, one set of styles, three trigger points.

---

## 3. Modal Design

### 3.1 Visual structure

```
┌──────────────────────────────────────────────────────────┐
│  ◆ phase1_reasoning                              ✕ Close │  ← Header
│  claude-sonnet-4-5-20250929                              │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ┌─ Stats Row ────────────────────────────────────────┐  │
│  │ Tokens In   Tokens Out   Cost      Duration        │  │
│  │ 9,587       368          $0.034    5.5s            │  │
│  │ ████████░   ███░░░░░░░                             │  │  ← token ratio bar
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Prompt ───────────────────────────────────────────┐  │
│  │ PROMPT                                    Copy 📋  │  │  ← section header
│  │                                                    │  │
│  │ You are analyzing a sales lead for Acme Corp.      │  │  ← monospace, scrollable
│  │ The lead was received via webhook with the         │  │
│  │ following data: ...                                │  │
│  │                                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Response ─────────────────────────────────────────┐  │
│  │ RESPONSE                                  Copy 📋  │  │
│  │                                                    │  │
│  │ {"tool": "crm_search", "intent": "find active      │  │
│  │  deals", "parameters": {"company": "Acme Corp"}}   │  │
│  │                                                    │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Metadata (collapsed by default) ──────────────────┐  │
│  │ ▸ Metadata (3 fields)                              │  │  ← click to expand
│  │   caller: atomic_phase1_turn_3                     │  │
│  │   temperature: 0.7                                 │  │
│  │   phase: reasoning                                 │  │
│  └────────────────────────────────────────────────────┘  │
│                                                          │
│  ┌─ Context ──────────────────────────────────────────┐  │
│  │ Agent: ag_6ce5uncd  ·  Task: evt_180e8df57960      │  │  ← clickable, close modal
│  │ Time: 2026-02-13 14:32:02.100Z                     │  │     and navigate to entity
│  └────────────────────────────────────────────────────┘  │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### 3.2 Layout rules

| Element | Specification |
|---|---|
| **Overlay** | Semi-transparent backdrop (`rgba(0,0,0,0.3)`), click to close |
| **Modal container** | `max-width: 680px`, `max-height: 80vh`, centered, `overflow-y: auto` |
| **Header** | LLM call name (bold), model tag below, close button top-right |
| **Stats row** | 4 inline stats (tokens in, tokens out, cost, duration) + token ratio bar |
| **Prompt section** | Section label "PROMPT" (muted caps), Copy button right-aligned. Content in monospace, `white-space: pre-wrap`, background `var(--bg-deep)`, `max-height: 200px`, `overflow-y: auto` |
| **Response section** | Same treatment as prompt. If content looks like JSON, pretty-print it |
| **Metadata** | Collapsed by default. Click "▸ Metadata" to expand. Key-value pairs |
| **Context row** | Agent and Task IDs, clickable — clicking closes modal and navigates to that entity. Timestamp in ISO format |
| **Keyboard** | `Escape` closes modal. Focus trapped inside modal while open |

### 3.3 Empty states

| Field | Behavior when absent |
|---|---|
| `prompt_preview` is null | Show "No prompt captured — enable prompt previews in loopCore" in muted text |
| `response_preview` is null | Show "No response captured" in muted text |
| `metadata` is null or empty | Hide the metadata section entirely |
| `cost` is null | Show "—" in the cost stat |
| `tokens_in`/`tokens_out` null | Show "—", hide the token ratio bar |
| `duration_ms` is null | Show "—" in the duration stat |

### 3.4 JSON detection for response

If `response_preview` starts with `{` or `[`, attempt `JSON.parse()`. If it succeeds, display it pretty-printed (`JSON.stringify(parsed, null, 2)`) in the monospace container. If it fails, display as raw text. This makes tool-call responses and structured outputs much more readable.

---

## 4. Data Source

The modal receives a data object with these fields (all from `payload.data` of `llm_call` events):

```javascript
{
    name: string,              // "phase1_reasoning"
    model: string,             // "claude-sonnet-4-5-20250929"
    tokens_in: number | null,
    tokens_out: number | null,
    cost: number | null,
    duration_ms: number | null,
    prompt_preview: string | null,
    response_preview: string | null,
    metadata: object | null,
    // Context (added by the trigger)
    agent_id: string,
    task_id: string | null,
    event_id: string,
    timestamp: string,
}
```

Each trigger point is responsible for assembling this object from whatever data shape it has access to.

---

## 5. Trigger Points

### 5.1 Tree View — click LLM node

**Current behavior:** Clicking a tree node row does nothing.

**New behavior:** Clicking a tree node row that represents an LLM call opens the modal.

**Implementation:**

The tree is rendered by `renderActionTreeNode()`. Currently the `tree-node-row` div has no onclick handler. The change:

1. In `fetchTimeline()`, when building the action tree, **cross-reference LLM events from the flat `events` array** with the action tree nodes using `action_id`. For each action tree node that has a corresponding LLM event, attach the full payload data to the tree node object.

2. Store the enriched tree nodes in a lookup (e.g., `TIMELINE_LLM_DETAILS[taskId]` — a map from `action_id` to the full payload data object).

3. In `renderActionTreeNode()`, for LLM-type nodes, add an onclick:

```javascript
// Only for LLM nodes:
html += `<div class="tree-node-row clickable" onclick="openLlmDetail('${taskId}', '${node.action_id}')">`;
```

4. `openLlmDetail(taskId, actionId)` looks up the detail from the stored map and opens the modal.

**Fallback:** If no LLM event matches the action (i.e., the tree node is a tracked action, not an LLM call), clicking should open a simpler action detail view — or do nothing. Only LLM-type nodes get the click handler.

**Visual hint:** LLM nodes in the tree should show a subtle "click for details" affordance. Options:
- Cursor changes to `pointer` on hover (already implied by `clickable` class)
- A small `⤢` icon appears on the right side of the row on hover
- The row gets a slightly different hover background (e.g., faint purple tint for LLM rows)

### 5.2 Flat Timeline — click LLM dot (upgrade existing pinNode)

**Current behavior:** `pinNode()` opens the pinned detail panel below the timeline, showing raw key-value pairs.

**New behavior:** For LLM nodes specifically, clicking opens the full modal instead. Non-LLM nodes continue to use the existing pinned detail panel (which is appropriate for their simpler data).

**Implementation:**

Modify `pinNode(idx)`:

```javascript
function pinNode(idx) {
    var tl = TIMELINES[selectedTask];
    var node = tl.nodes[idx];
    if (!node) return;

    // ★ LLM nodes get the modal
    if (node.kind === 'llm_call' || node.type === 'llm') {
        openLlmDetailFromFlatNode(selectedTask, idx);
        return;
    }

    // Non-LLM nodes: existing pinned detail behavior (unchanged)
    // ... rest of current pinNode logic
}
```

`openLlmDetailFromFlatNode(taskId, nodeIdx)` assembles the detail object from the flat node's stored data (which already has `tokensIn`, `tokensOut`, `llmCost`, `llmModel`, etc.) plus the original event's `payload.data` for `prompt_preview` and `response_preview`.

**Key requirement:** The flat node objects created in `fetchTimeline()` currently store `tokensIn`, `tokensOut`, `llmCost`, `llmModel` — but NOT `prompt_preview` and `response_preview`. These need to be added:

```javascript
// In fetchTimeline(), where flat nodes are built:
var promptPreview = (kind === 'llm_call' && payload.data) ? payload.data.prompt_preview : null;
var responsePreview = (kind === 'llm_call' && payload.data) ? payload.data.response_preview : null;
var llmMetadata = (kind === 'llm_call' && payload.data) ? payload.data.metadata : null;

return {
    // ... existing fields ...
    promptPreview: promptPreview,
    responsePreview: responsePreview,
    llmMetadata: llmMetadata,
};
```

### 5.3 Activity Stream — "Details" button on LLM cards

**Current behavior:** LLM event cards in the stream show model tag, token counts, cost, and duration as inline tags. No interaction beyond clicking agent/task names.

**New behavior:** LLM event cards get a small "View LLM call" button in the detail tag row. Clicking it opens the modal.

**Implementation:**

In `buildStreamDetailTags(e)`, for LLM events, append a button tag:

```javascript
if (e.kind === 'llm_call' || e.type === 'llm_call') {
    // ... existing model/tokens/cost/duration tags ...
    tags.push(`<span class="stream-detail-btn" onclick="event.stopPropagation(); openLlmDetailFromStream('${e.eventId}')">⤢ Details</span>`);
}
```

**Data availability:** Stream events currently store `model`, `tokensIn`, `tokensOut`, `cost`, `durationMs` — but NOT `prompt_preview`, `response_preview`, or `metadata`. Two options:

**Option A (preferred) — Fetch on demand:** When the user clicks "Details", make an API call to fetch the full event:

```javascript
async function openLlmDetailFromStream(eventId) {
    var cached = STREAM_EVENTS.find(function(e) { return e.eventId === eventId; });
    // Fetch full event payload from API
    var full = await apiFetch('/v1/events/' + encodeURIComponent(eventId));
    if (full) {
        var pd = full.payload && full.payload.data ? full.payload.data : {};
        openLlmModal({
            name: pd.name || cached.summary,
            model: pd.model || cached.model,
            tokens_in: pd.tokens_in || cached.tokensIn,
            tokens_out: pd.tokens_out || cached.tokensOut,
            cost: pd.cost || cached.cost,
            duration_ms: pd.duration_ms || cached.durationMs,
            prompt_preview: pd.prompt_preview || null,
            response_preview: pd.response_preview || null,
            metadata: pd.metadata || null,
            agent_id: cached.agent,
            task_id: cached.task,
            event_id: eventId,
            timestamp: cached.timestamp,
        });
    }
}
```

**Option B — Store at fetch time:** Expand `fetchEvents()` and `handleWsMessage()` to also extract and store `prompt_preview`, `response_preview`, and `metadata` on every stream event. This avoids the extra API call but increases memory usage (500 chars × 2 fields × 50 events = ~50KB — acceptable).

**Recommendation:** Start with **Option B** for simplicity — the data is already in the API response, we're just not storing it. Then the modal opens instantly with no loading state. If memory becomes a concern with larger event limits, switch to Option A later.

**API note for Option A:** This requires a `GET /v1/events/{event_id}` endpoint. Check if the backend already has this. If not, it's a simple single-row lookup by `(tenant_id, event_id)`. Add it as a low-effort backend task.

### 5.4 Trigger summary

| Surface | Trigger | Data source | Loading? |
|---|---|---|---|
| Tree view | Click LLM node row | Cross-referenced from flat events → `payload.data` | No (already in memory) |
| Flat timeline | Click purple LLM dot | Flat node object (add `promptPreview`, `responsePreview`, `llmMetadata`) | No |
| Activity stream | Click "⤢ Details" button | Stream event object (add 3 fields) OR fetch from API | No (Option B) or Yes (Option A) |

---

## 6. JS Implementation Outline

### 6.1 New global state

```javascript
let llmModalOpen = false;
let llmModalData = null;  // the detail object
```

### 6.2 Core modal function

```javascript
function openLlmModal(data) {
    llmModalData = data;
    llmModalOpen = true;
    renderLlmModal();
}

function closeLlmModal() {
    llmModalOpen = false;
    llmModalData = null;
    var overlay = document.getElementById('llmModalOverlay');
    if (overlay) overlay.classList.remove('visible');
}
```

### 6.3 Render function

```javascript
function renderLlmModal() {
    var d = llmModalData;
    if (!d) return;
    var overlay = document.getElementById('llmModalOverlay');
    var modal = document.getElementById('llmModalContent');

    // Stats
    var statsHtml = `
        <div class="llm-modal-stats">
            <div class="llm-modal-stat">
                <div class="stat-label">Tokens In</div>
                <div class="stat-value">${d.tokens_in != null ? d.tokens_in.toLocaleString() : '—'}</div>
            </div>
            <div class="llm-modal-stat">
                <div class="stat-label">Tokens Out</div>
                <div class="stat-value">${d.tokens_out != null ? d.tokens_out.toLocaleString() : '—'}</div>
            </div>
            <div class="llm-modal-stat">
                <div class="stat-label">Cost</div>
                <div class="stat-value">${d.cost != null ? '$' + d.cost.toFixed(4) : '—'}</div>
            </div>
            <div class="llm-modal-stat">
                <div class="stat-label">Duration</div>
                <div class="stat-value">${d.duration_ms != null ? fmtDuration(d.duration_ms) : '—'}</div>
            </div>
        </div>`;

    // Token ratio bar (only if both values present)
    var ratioHtml = '';
    if (d.tokens_in != null && d.tokens_out != null) {
        var max = Math.max(d.tokens_in, d.tokens_out, 1);
        var wIn = Math.round((d.tokens_in / max) * 100);
        var wOut = Math.round((d.tokens_out / max) * 100);
        ratioHtml = `
            <div class="llm-modal-ratio">
                <div class="llm-ratio-bar in" style="width:${wIn}%"></div>
                <div class="llm-ratio-bar out" style="width:${wOut}%"></div>
            </div>`;
    }

    // Prompt
    var promptHtml = '';
    if (d.prompt_preview) {
        promptHtml = `
            <div class="llm-modal-section">
                <div class="llm-modal-section-header">
                    <div class="llm-modal-section-label">PROMPT</div>
                    <button class="llm-modal-copy" onclick="copyToClipboard(llmModalData.prompt_preview)">Copy 📋</button>
                </div>
                <pre class="llm-modal-preview">${escHtml(d.prompt_preview)}</pre>
            </div>`;
    } else {
        promptHtml = `
            <div class="llm-modal-section">
                <div class="llm-modal-section-header">
                    <div class="llm-modal-section-label">PROMPT</div>
                </div>
                <div class="llm-modal-empty">No prompt captured — enable prompt previews in your SDK instrumentation</div>
            </div>`;
    }

    // Response (with JSON detection)
    var responseHtml = '';
    var responseText = d.response_preview || null;
    if (responseText) {
        // Try to pretty-print JSON
        var displayText = responseText;
        if (responseText.trim().charAt(0) === '{' || responseText.trim().charAt(0) === '[') {
            try { displayText = JSON.stringify(JSON.parse(responseText), null, 2); } catch(e) { }
        }
        responseHtml = `
            <div class="llm-modal-section">
                <div class="llm-modal-section-header">
                    <div class="llm-modal-section-label">RESPONSE</div>
                    <button class="llm-modal-copy" onclick="copyToClipboard(llmModalData.response_preview)">Copy 📋</button>
                </div>
                <pre class="llm-modal-preview">${escHtml(displayText)}</pre>
            </div>`;
    } else {
        responseHtml = `
            <div class="llm-modal-section">
                <div class="llm-modal-section-header">
                    <div class="llm-modal-section-label">RESPONSE</div>
                </div>
                <div class="llm-modal-empty">No response captured</div>
            </div>`;
    }

    // Metadata (collapsed)
    var metaHtml = '';
    if (d.metadata && Object.keys(d.metadata).length > 0) {
        var metaRows = Object.entries(d.metadata).map(function(kv) {
            return '<div class="llm-meta-row"><span class="llm-meta-key">' +
                escHtml(kv[0]) + '</span><span class="llm-meta-val">' +
                escHtml(String(kv[1])) + '</span></div>';
        }).join('');
        var fieldCount = Object.keys(d.metadata).length;
        metaHtml = `
            <div class="llm-modal-section collapsible" onclick="this.classList.toggle('expanded')">
                <div class="llm-modal-section-header">
                    <div class="llm-modal-section-label">▸ Metadata (${fieldCount} field${fieldCount > 1 ? 's' : ''})</div>
                </div>
                <div class="llm-meta-content">${metaRows}</div>
            </div>`;
    }

    // Context
    var ctxParts = [];
    if (d.agent_id) ctxParts.push('<span class="clickable-entity" onclick="closeLlmModal(); selectAgent(\'' + d.agent_id + '\')">🤖 ' + escHtml(d.agent_id) + '</span>');
    if (d.task_id) ctxParts.push('<span class="clickable-entity" onclick="closeLlmModal(); selectTask(\'' + d.task_id + '\')">📋 ' + escHtml(d.task_id) + '</span>');
    var contextHtml = `
        <div class="llm-modal-context">
            ${ctxParts.join(' · ')}
            ${d.timestamp ? ' · <span style="color:var(--text-muted)">' + escHtml(d.timestamp) + '</span>' : ''}
        </div>`;

    modal.innerHTML = `
        <div class="llm-modal-header">
            <div>
                <div class="llm-modal-name">◆ ${escHtml(d.name || 'LLM Call')}</div>
                <div class="llm-modal-model">${escHtml(d.model || '—')}</div>
            </div>
            <button class="llm-modal-close" onclick="closeLlmModal()">✕</button>
        </div>
        ${statsHtml}
        ${ratioHtml}
        ${promptHtml}
        ${responseHtml}
        ${metaHtml}
        ${contextHtml}
    `;

    overlay.classList.add('visible');
}

function copyToClipboard(text) {
    if (!text) return;
    navigator.clipboard.writeText(text).catch(function() {});
    showToast('Copied to clipboard');
}
```

### 6.4 Keyboard handler

```javascript
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && llmModalOpen) {
        closeLlmModal();
        e.stopPropagation();
    }
});
```

---

## 7. HTML Addition

Add this once, at the bottom of `<body>` (before the script tag):

```html
<!-- LLM Detail Modal -->
<div class="llm-modal-overlay" id="llmModalOverlay" onclick="if(event.target===this) closeLlmModal()">
    <div class="llm-modal" id="llmModalContent"></div>
</div>
```

---

## 8. CSS

```css
/* ─── LLM Detail Modal ─── */

.llm-modal-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.3);
    z-index: 1000;
    align-items: center;
    justify-content: center;
    backdrop-filter: blur(2px);
}

.llm-modal-overlay.visible {
    display: flex;
}

.llm-modal {
    background: var(--bg-primary);
    border-radius: var(--radius-lg);
    width: 90%;
    max-width: 680px;
    max-height: 80vh;
    overflow-y: auto;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
    padding: 28px;
}

.llm-modal-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 20px;
}

.llm-modal-name {
    font-family: var(--font-sans);
    font-size: 17px;
    font-weight: 700;
    color: var(--llm);
}

.llm-modal-model {
    font-family: var(--font-mono);
    font-size: 12px;
    color: var(--text-muted);
    margin-top: 2px;
}

.llm-modal-close {
    font-size: 14px;
    color: var(--text-muted);
    background: none;
    border: none;
    cursor: pointer;
    padding: 4px 8px;
    border-radius: 4px;
}

.llm-modal-close:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
}

/* Stats row */
.llm-modal-stats {
    display: flex;
    gap: 1px;
    background: var(--border);
    border-radius: var(--radius-md);
    overflow: hidden;
    margin-bottom: 16px;
}

.llm-modal-stat {
    flex: 1;
    background: var(--bg-primary);
    padding: 12px 16px;
    text-align: center;
}

.llm-modal-stat .stat-label {
    font-size: 11px;
}

.llm-modal-stat .stat-value {
    font-family: var(--font-mono);
    font-size: 15px;
    font-weight: 600;
    margin-top: 2px;
}

/* Token ratio bar */
.llm-modal-ratio {
    display: flex;
    gap: 3px;
    margin-bottom: 20px;
    height: 6px;
    border-radius: 3px;
    overflow: hidden;
    background: var(--bg-deep);
}

.llm-ratio-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.3s ease;
}

.llm-ratio-bar.in {
    background: rgba(124, 58, 237, 0.35);
}

.llm-ratio-bar.out {
    background: rgba(124, 58, 237, 0.7);
}

/* Sections (prompt, response, metadata) */
.llm-modal-section {
    margin-bottom: 16px;
}

.llm-modal-section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
}

.llm-modal-section-label {
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-muted);
}

.llm-modal-copy {
    font-family: var(--font-sans);
    font-size: 11px;
    color: var(--text-muted);
    background: none;
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2px 8px;
    cursor: pointer;
    transition: all 0.1s;
}

.llm-modal-copy:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
}

.llm-modal-preview {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    color: var(--text-primary);
    background: var(--bg-deep);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    padding: 14px 16px;
    max-height: 200px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
}

.llm-modal-empty {
    font-family: var(--font-sans);
    font-size: 12px;
    color: var(--text-muted);
    font-style: italic;
    padding: 12px 16px;
    background: var(--bg-deep);
    border-radius: var(--radius-sm);
}

/* Collapsible metadata */
.llm-modal-section.collapsible {
    cursor: pointer;
}

.llm-modal-section.collapsible .llm-meta-content {
    display: none;
    margin-top: 8px;
}

.llm-modal-section.collapsible.expanded .llm-meta-content {
    display: block;
}

.llm-modal-section.collapsible.expanded .llm-modal-section-label {
    /* Rotate the triangle */
}

/* Override: replace ▸ with ▾ when expanded via CSS content */
.llm-modal-section.collapsible .llm-modal-section-label::first-letter {
    /* Note: ▸ / ▾ is in the text content, toggle via JS classList is sufficient */
}

.llm-meta-row {
    display: flex;
    padding: 3px 0;
    font-family: var(--font-mono);
    font-size: 11px;
    border-bottom: 1px solid var(--border-subtle);
}

.llm-meta-row:last-child {
    border-bottom: none;
}

.llm-meta-key {
    color: var(--text-muted);
    width: 160px;
    flex-shrink: 0;
}

.llm-meta-val {
    color: var(--text-primary);
}

/* Context row */
.llm-modal-context {
    font-family: var(--font-sans);
    font-size: 12px;
    color: var(--text-muted);
    padding-top: 16px;
    border-top: 1px solid var(--border-subtle);
    margin-top: 8px;
}

/* Stream detail button */
.stream-detail-btn {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--llm);
    background: rgba(124, 58, 237, 0.06);
    padding: 1px 8px;
    border-radius: 3px;
    cursor: pointer;
    border: 1px solid rgba(124, 58, 237, 0.15);
    transition: all 0.1s;
}

.stream-detail-btn:hover {
    background: rgba(124, 58, 237, 0.12);
    border-color: rgba(124, 58, 237, 0.3);
}

/* Tree node clickable hint for LLM nodes */
.tree-node-row.llm-clickable {
    cursor: pointer;
}

.tree-node-row.llm-clickable:hover {
    background: rgba(124, 58, 237, 0.04);
}

.tree-node-row.llm-clickable:hover .tree-expand-hint {
    opacity: 1;
}

.tree-expand-hint {
    font-size: 10px;
    color: var(--llm);
    opacity: 0;
    transition: opacity 0.15s;
    margin-left: auto;
    flex-shrink: 0;
}
```

---

## 9. Data Pipeline Changes

### 9.1 `fetchTimeline()` — store prompt/response on flat nodes

Add three fields to each LLM node in the flat node builder:

```javascript
promptPreview: (kind === 'llm_call' && payload.data) ? payload.data.prompt_preview : null,
responsePreview: (kind === 'llm_call' && payload.data) ? payload.data.response_preview : null,
llmMetadata: (kind === 'llm_call' && payload.data) ? payload.data.metadata : null,
```

These are only populated for LLM nodes (no memory waste on non-LLM events).

### 9.2 `fetchEvents()` — store prompt/response on stream events

Add three fields to each stream event:

```javascript
promptPreview: pd.prompt_preview || null,
responsePreview: pd.response_preview || null,
llmMetadata: pd.metadata || null,
```

Same addition in `handleWsMessage()` for live events.

### 9.3 Tree view enrichment

The action tree from the API contains tracked actions, not LLM events. To show LLM detail on tree nodes:

**Approach A — Cross-reference by action_id:**

After `fetchTimeline()` builds both `nodes` (flat) and `actionTree`, cross-reference:

```javascript
// Build a lookup of LLM detail by action_id
var llmDetailByAction = {};
(data.events || []).forEach(function(e) {
    if (e.payload && e.payload.kind === 'llm_call' && e.action_id) {
        llmDetailByAction[e.action_id] = {
            name: e.payload.data.name,
            model: e.payload.data.model,
            tokens_in: e.payload.data.tokens_in,
            tokens_out: e.payload.data.tokens_out,
            cost: e.payload.data.cost,
            duration_ms: e.payload.data.duration_ms,
            prompt_preview: e.payload.data.prompt_preview,
            response_preview: e.payload.data.response_preview,
            metadata: e.payload.data.metadata,
            event_id: e.event_id,
            timestamp: e.timestamp,
        };
    }
});
// Store alongside the timeline
TIMELINES[taskId].llmDetailByAction = llmDetailByAction;
```

Then in `renderActionTreeNode()`, check if a node's `action_id` exists in the lookup, and if so, add `llm-clickable` class and the onclick handler.

**Approach B — Backend enrichment:**

Modify the timeline endpoint to embed `payload.data` on action tree nodes that have associated LLM events. This is cleaner but requires a backend change. Recommend Approach A as the quick path.

---

## 10. Non-LLM Events (future scope)

The modal pattern can be extended to other event types later:

| Event type | What the modal would show |
|---|---|
| `action_failed` | Exception type, message, stack trace, retry history |
| `approval_requested` | Approval message, requested_from, decision, response time |
| `issue` | Severity, category, occurrence history, related events |
| `queue_snapshot` | Queue items list, depths, oldest item details |

For now, only LLM events get the modal. Others continue using the existing pinned detail panel (flat view) or have no click action (tree view).

---

## 11. Testing Checklist

- [ ] Modal opens from tree view LLM node click
- [ ] Modal opens from flat timeline LLM dot click
- [ ] Modal opens from stream card "⤢ Details" button
- [ ] Modal shows prompt preview text when available
- [ ] Modal shows "No prompt captured" when prompt_preview is null
- [ ] JSON responses are pretty-printed
- [ ] Copy buttons work for prompt and response
- [ ] Metadata section is collapsed by default, expands on click
- [ ] Agent/Task links in context row close modal and navigate correctly
- [ ] Escape key closes modal
- [ ] Clicking backdrop closes modal
- [ ] Modal scrolls internally when content exceeds 80vh
- [ ] Non-LLM nodes in flat timeline still open pinned detail panel
- [ ] Non-LLM nodes in tree view have no click action (no pointer cursor)
- [ ] Prompt/response previews truncate gracefully at ~500 chars

---

## 12. Files to Modify

| File | Changes |
|---|---|
| `hiveboard-v2.js` | Add `openLlmModal()`, `closeLlmModal()`, `renderLlmModal()`, `copyToClipboard()`. Modify `fetchTimeline()`, `fetchEvents()`, `handleWsMessage()`, `pinNode()`, `renderActionTreeNode()`, `buildStreamDetailTags()` |
| `hiveboard-v2-index.html` | Add modal overlay div before `</body>` |
| `hiveboard-v2.css` | Add all modal styles (~150 lines), tree clickable hints, stream detail button |
| Backend (if Option A for stream) | No changes needed |
| Backend (if adding `/v1/events/{id}`) | New single-event endpoint (low effort, optional) |

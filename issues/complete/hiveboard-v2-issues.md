# HiveBoard v2 — Post-Deploy Issues

**Priority:** High — affects 2 of 7 new features + 1 UX fix
**Files involved:** `hiveboard-v2.js` (JS), `hiveboard-v2.css` (CSS), possibly `app.py` (backend timeline endpoint)
**Screenshots reference:** 3 live production screenshots provided separately

---

## Issue 1: Duration Breakdown misclassifies all time as "Other"

### What's happening

The Duration Breakdown panel renders correctly (labels, bars, total) but shows:

```
LLM       0ms   (0%)
Tools     57ms  (0%)
Other    27.2s  (100%)
```

…on a task that has 6 LLM calls and multiple tool actions. All duration is falling into the "Other" bucket.

### Root cause (suspected)

The `computeDurationBreakdown()` function in `hiveboard-v2.js` classifies nodes using this logic:

```javascript
if (n.kind === 'llm_call' || n.type === 'llm') {
    llmMs += ms;
} else if (n.type === 'action' || n.eventType === 'action_completed' || n.eventType === 'action_failed') {
    toolMs += ms;
} else {
    otherMs += ms;
}
```

This checks `n.kind` and `n.type` — but these are **derived properties** set by `fetchTimeline()`, not raw API fields. The derivation logic in `fetchTimeline()` maps event types to node types like this:

```javascript
// For LLM classification:
else if (kind === 'llm_call') nodeType = 'llm';
else if (e.event_type === 'custom' && kind === 'llm_call') nodeType = 'llm';
```

Where `kind` is read from `payload.kind`.

**Likely mismatch scenario:** The `kind` variable is read as `payload.kind`, but the actual API response for LLM calls has `event_type: "custom"` and the kind is nested at `payload.kind`. If the backend serializes `payload` as a JSON string rather than a parsed object, `payload.kind` would be `undefined` and these events would fall through to `nodeType = 'system'`, which then falls through to "Other" in the breakdown.

### Debugging steps

1. **Inspect the raw API response.** Open browser DevTools → Network tab → find the call to `/v1/tasks/{task_id}/timeline`. Look at a single event where you expect an LLM call. Check:
   - What is `event.event_type`? (expect: `"custom"`)
   - What is `event.payload`? Is it a parsed object or a JSON string?
   - What is `event.payload.kind`? (expect: `"llm_call"`)
   - What is `event.duration_ms`? (expect: a number in milliseconds)

2. **Add a temporary console.log.** In `computeDurationBreakdown`, add at the top of the forEach:

```javascript
nodes.forEach(function(n) {
    console.log('BD node:', n.label, 'kind:', n.kind, 'type:', n.type, 'eventType:', n.eventType, 'ms:', n.durationMs);
    // ... rest of function
});
```

This will show you exactly what each node looks like after `fetchTimeline()` processes it. The fix will be obvious from this output.

3. **Check `duration_ms` values.** The API spec says `duration_ms` can be `null` on many event types (e.g., `task_started`). Only `action_completed`, `action_failed`, and LLM call events should have durations. If `fetchTimeline()` defaults `durationMs` to `0` for null values, the total would be wrong. The current code does `durationMs: e.duration_ms || 0` — this is correct for defaulting, but if the task-level `task_completed` event carries a `duration_ms` covering the whole task, it would dominate.

### Likely fixes (pick one based on what debugging reveals)

**Fix A — If `payload` is a string:** Parse it first in `fetchTimeline()`:

```javascript
var payload = typeof e.payload === 'string' ? JSON.parse(e.payload) : (e.payload || {});
```

**Fix B — If `kind` isn't propagating:** The `n.kind` field on flat nodes is set from `payload.kind`, but `computeDurationBreakdown` also checks `n.type === 'llm'`. Verify that the node type derivation actually runs. Add a fallback:

```javascript
// In computeDurationBreakdown:
if (n.kind === 'llm_call' || n.type === 'llm' || (n.eventType === 'custom' && n.detail && n.detail.model)) {
    llmMs += ms;
}
```

**Fix C — If `duration_ms` is only on specific events:** Only `action_completed`, `action_failed`, and `custom` (llm_call) events have durations. The `task_completed` event might have the full task duration, which swamps everything. In `computeDurationBreakdown`, skip task-level events:

```javascript
nodes.forEach(function(n) {
    if (n.eventType === 'task_started' || n.eventType === 'task_completed' || n.eventType === 'task_failed') return;
    // ... classify the rest
});
```

Then compute `totalMs` from the task metadata instead:

```javascript
var task = TASKS.find(function(t) { return t.id === selectedTask; });
var totalMs = task ? task.durationMs : (llmMs + toolMs + otherMs);
```

---

## Issue 2: Action Tree renders flat instead of nested

### What's happening

The tree view area shows individual action nodes (e.g., `workspace_write 20ms`, `workspace_read 19ms`) but they appear as a **flat list** — no indentation, no parent-child hierarchy, no LLM nodes with token bars.

### Root cause (suspected)

`renderActionTreeNode()` expects each tree node to have this shape (per the API spec):

```json
{
    "action_id": "act_001",
    "action_name": "fetch_crm_data",
    "parent_action_id": null,
    "started_at": "2026-02-10T14:32:01.400Z",
    "duration_ms": 1800,
    "status": "success",
    "children": []
}
```

But the renderer accesses fields as:

```javascript
var name = node.name || node.action_name || 'unknown';
var status = node.status || 'completed';
var children = node.children || [];
var isFailed = status === 'failed' || status === 'error';
```

**Potential mismatches to check:**

1. **`status` field value:** The spec says `"success"` — but the renderer checks for `"completed"`. If the backend returns `status: "success"`, the node won't match `status === 'completed'` and will render without the green checkmark. **Fix:** Add `'success'` to the completed check:

```javascript
var statusDone = status === 'completed' || status === 'success';
```

2. **`children` might not be pre-built.** The API spec shows `"children": []` in the example, but the backend's tree-building logic in `app.py` (lines 744-785 per the earlier audit) might return `children` only for nodes that actually have children, or might not populate it at all. If `children` is `undefined` rather than `[]`, the node renders without sub-nodes but doesn't crash.

3. **Node type detection for LLM calls.** The renderer checks:

```javascript
if (node.type === 'llm_call' || node.kind === 'llm_call') nodeType = 'llm';
```

But the `action_tree` nodes in the API spec don't have a `type` or `kind` field — they're action-level nodes, not event-level nodes. LLM calls are events, not actions. The action tree only contains tracked function calls (`@agent.track()`), not LLM call events.

**This is likely why LLM nodes don't appear in the tree** — they exist in the `events` array (as `event_type: "custom"` with `payload.kind: "llm_call"`) but NOT in the `action_tree` array (which only contains tracked function actions).

4. **Tree might be genuinely flat.** If the agent's tracked functions don't nest (no tracked function calling another tracked function), `parent_action_id` would be `null` for all actions, and the tree would correctly be flat — just a list of siblings. This isn't a bug; it's accurate. The tree adds value when there IS nesting.

### Debugging steps

1. **Inspect the raw `action_tree` response.** In DevTools → Network → `/v1/tasks/{id}/timeline`, check the `action_tree` field:
   - Is it an array or a single object?
   - Do any nodes have non-empty `children` arrays?
   - Do any nodes have `parent_action_id` set to another action's ID?
   - Are there nodes with `action_name` matching LLM-related names?

2. **Check if LLM events are in the tree at all.** Compare the `events` array (which should have LLM calls) with `action_tree` (which may not). If LLM calls are only in events, the tree view will never show them unless we enrich it.

3. **Console.log the parsed tree.** In `renderTimeline()`, add:

```javascript
console.log('action_tree:', JSON.stringify(tl.actionTree, null, 2));
console.log('error_chains:', JSON.stringify(tl.errorChains, null, 2));
```

### Likely fixes

**Fix A — If tree is genuinely flat (no nesting in agent code):** This is expected behavior for agents that don't have nested tracked functions. The tree view should still render it nicely though. Consider showing a "This task has no nested actions" note and auto-switching to flat view when the tree has only depth-1 nodes.

**Fix B — If LLM events are missing from the tree:** The action tree only contains `@agent.track()` decorated functions. LLM calls (`task.llm_call()` or `task.event()` with `kind: "llm_call"`) are events, not tracked actions. To show LLM calls in the tree, you'd need to **merge** LLM events into the tree by timestamp. This is a JS-side enrichment:

```javascript
// After receiving actionTree and events from API:
// Insert LLM events as pseudo-nodes in the tree, positioned by timestamp
var llmEvents = data.events.filter(function(e) {
    return (e.payload && e.payload.kind === 'llm_call');
});
// For each LLM event, find which action it falls between by timestamp
// and insert it as a child of the most recent parent action
```

This is a non-trivial enrichment. Coordinate with the backend team on whether the tree should include LLM events server-side (preferred) or if the JS should handle merging.

**Fix C — If `children` aren't being built server-side:** Check `app.py` lines 744-785 where the action tree is built. The algorithm should:
1. Collect all action events with `action_id`
2. Build a lookup: `action_id → node`
3. For each node with `parent_action_id`, append to parent's `children`
4. Return only root nodes (where `parent_action_id` is null)

If step 3 isn't happening, all nodes appear as roots → flat list.

---

## Issue 3: Action Tree canvas is too narrow

### What's happening

The tree nodes are cramped into the timeline section's limited height. The tree needs more vertical space than the horizontal dot-chain to be readable, especially when tasks have many steps.

### Fix (CSS-only)

In `hiveboard-v2.css`, the tree shares the same container constraints as the flat timeline. The timeline section currently has:

```css
.timeline-section {
    flex: 1 1 40%;
    min-height: 120px;
    max-height: 45%;
}
```

**Option A — Give the tree more flex space:**

```css
/* When tree view is active, let it grow more */
.action-tree-canvas {
    flex: 1;
    overflow: auto;
    padding: 16px 24px;
    background: var(--bg-deep);
    min-height: 200px;
}

/* Increase the timeline section's max-height when tree is showing */
.timeline-section {
    flex: 1 1 50%;      /* was 40% */
    min-height: 200px;  /* was 120px */
    max-height: 60%;    /* was 45% */
}
```

**Option B — Make the tree section expandable.** Add a "expand/collapse" toggle that switches between a compact (45%) and full-height (75%) mode. This preserves the tasks table visibility while letting the user expand when they want detail.

**Option C — Let the tree take the full remaining center height** when in tree mode, pushing the tasks table into a collapsible section or a tab. Tree and tasks table compete for the same vertical space; one needs to yield.

**Recommended: Option A** as the quick fix (just CSS changes), with Option B as a follow-up enhancement.

The specific CSS changes for Option A:

```css
/* In hiveboard-v2.css, find and update: */

.timeline-section {
    flex: 1 1 50%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    min-height: 200px;
    max-height: 60%;
}

.action-tree-canvas {
    flex: 1;
    overflow: auto;
    padding: 16px 24px;
    background: var(--bg-deep);
    min-height: 200px;
}
```

---

## Quick Reference: File Locations

| File | Purpose | Key functions/selectors |
|---|---|---|
| `hiveboard-v2.js` | All rendering logic | `computeDurationBreakdown()`, `renderActionTreeNode()`, `fetchTimeline()` |
| `hiveboard-v2.css` | All styling | `.timeline-section`, `.action-tree-canvas`, `.tree-node-row` |
| `src/backend/app.py` | Timeline endpoint | Lines 744-785 (action tree builder), lines 787-801 (error chains) |
| `src/shared/models.py` | Response models | `TimelineResponse` model shape |

## Testing Checklist

After fixes, verify:

- [ ] Duration Breakdown shows non-zero LLM and Tools percentages on a task with LLM calls
- [ ] Duration Breakdown bars sum to approximately the task's total duration
- [ ] Action Tree shows nested nodes when tracked functions call other tracked functions
- [ ] LLM calls appear in the tree (either via backend enrichment or JS merging)
- [ ] Error nodes in the tree show red-bordered error message inline
- [ ] Retry sub-nodes appear as children of failed nodes
- [ ] Tree view has enough vertical space to show 8-10 nodes without scrolling
- [ ] Tree/Flat toggle switches correctly and both views render
- [ ] Flat view still works identically to v1 behavior

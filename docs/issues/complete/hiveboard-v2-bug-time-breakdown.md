# Bug: Time Breakdown shows 0% LLM / 100% Other

**Priority:** High — feature is visible but completely non-functional
**File:** `Inboundhiveboard.js`
**Root cause:** Confirmed from code analysis — two bugs, both in the data pipeline

---

## Symptoms

Across every task observed in production (4 screenshots over multiple sessions), the Time Breakdown always shows:

```
LLM     0ms   (0%)
Tools   59ms  (0%)
Other   26.6s (100%)
```

This is on a task with 6 LLM calls that the tree view correctly shows as taking 6.2s, 2.9s, 6.7s, etc. The breakdown should show ~60-70% LLM time.

---

## Root Cause — Two Bugs

### Bug A: LLM duration is read from the wrong field

**In `fetchTimeline()`**, inside the `.map()` that builds flat nodes, find the return object. It contains:

```javascript
durationMs: e.duration_ms || 0,
```

This reads the **top-level** `e.duration_ms` from the API response. But for LLM call events, the top-level `duration_ms` is **`null`** — because these are `event_type: "custom"` events, and the event-level `duration_ms` field only tracks enclosing action duration (which is null for custom events).

The LLM call's actual duration lives at **`payload.data.duration_ms`** — a completely different field. This is by design in the event schema (see Event Schema v2, Section 6.3: _"`duration_ms`: LLM call latency. Separate from the event-level `duration_ms` which tracks the enclosing action."_).

**Result:** Every LLM node gets `durationMs = 0`. The breakdown function correctly classifies them as LLM (`n.kind === 'llm_call'`) but adds 0ms to the LLM bucket.

### Bug B: `task_completed` event inflates "Other"

The `task_completed` event carries the **full task duration** in its top-level `e.duration_ms` (e.g., 26,600ms). This event's node gets:

- `type: 'success'` (in `fetchTimeline()`: `else if (e.event_type === 'task_completed') nodeType = 'success'`)
- `kind: undefined` (task events have no `payload.kind`)

In `computeDurationBreakdown()`, inside the `nodes.forEach`, this node doesn't match the LLM check (`kind !== 'llm_call'`, `type !== 'llm'`) or the tool check (`type !== 'action'`, `eventType !== 'action_completed'`), so it falls through to `otherMs += ms` — adding the **entire task duration** to "Other."

Similarly, `task_started` and other task lifecycle events with durations land in "Other."

**Result:** One event (task_completed) contributes ~26.6s to "Other," which dwarfs everything else and makes the breakdown useless.

---

## The Fix

### Fix for Bug A — Read LLM duration from payload.data

In `fetchTimeline()`, in the return object of the `.map()` callback, find the `durationMs` assignment and add a fallback:

```javascript
// BEFORE:
durationMs: e.duration_ms || 0,

// AFTER:
durationMs: e.duration_ms || (kind === 'llm_call' && payload.data ? payload.data.duration_ms : null) || 0,
```

This reads:
1. Top-level `duration_ms` first (correct for action events)
2. Falls back to `payload.data.duration_ms` for LLM calls (where the actual latency lives)
3. Defaults to 0

### Fix for Bug B — Exclude task lifecycle events from breakdown

In `computeDurationBreakdown()`, at the top of the `nodes.forEach` callback, add an early return to skip task envelope events:

```javascript
// BEFORE:
nodes.forEach(function(n) {
    var ms = n.durationMs || 0;
    if (n.kind === 'llm_call' || n.type === 'llm') {

// AFTER:
nodes.forEach(function(n) {
    // Skip task-level events — their duration is the total, not a component
    if (n.eventType === 'task_started' || n.eventType === 'task_completed' || n.eventType === 'task_failed') return;
    var ms = n.durationMs || 0;
    if (n.kind === 'llm_call' || n.type === 'llm') {
```

Then replace the `totalMs` logic **after** the forEach loop. Find the block that checks for a task event and uses its duration — replace it with:

```javascript
// AFTER the forEach loop, replace the existing totalMs adjustment with:
var taskEvt = nodes.find(function(n) { 
    return n.eventType === 'task_completed' || n.eventType === 'task_failed'; 
});
// Use the task's total duration as the denominator, not the sum of parts
// (sum of parts will be less than total due to idle/wait time between steps)
if (taskEvt && taskEvt.durationMs > 0) {
    totalMs = taskEvt.durationMs;
    // "Other" is the remainder: total minus LLM minus tools
    otherMs = Math.max(0, totalMs - llmMs - toolMs);
} else {
    totalMs = llmMs + toolMs + otherMs;
}
if (totalMs === 0) totalMs = 1;
```

This makes "Other" represent genuine idle/wait time (the gap between active steps), which is its intended meaning.

### Also check: `action_started` double-counting

`action_started` events likely have `duration_ms: null` (only `action_completed` carries the duration). But if both `action_started` and `action_completed` for the same action have durations, the tool time would be double-counted. To be safe, in `computeDurationBreakdown()` tighten the tool check to only count completion events:

```javascript
} else if (n.eventType === 'action_completed' || n.eventType === 'action_failed') {
    toolMs += ms;
} else {
```

(Remove the `n.type === 'action'` check which would also match `action_started`.)

---

## Expected Result After Fix

For a task with 26.6s total, 6 LLM calls totaling ~20s, and tools totaling ~59ms:

```
LLM     20.0s  (75%)    ← was 0ms
Tools   59ms   (0%)     ← correct already
Other   6.5s   (25%)    ← was 26.6s; now shows actual idle/wait time
```

---

## Verification Steps

1. Apply both fixes
2. Load any task with LLM calls in the dashboard
3. Check Time Breakdown:
   - LLM bar should show non-zero duration approximately equal to the sum of LLM call durations visible in the tree view
   - Other should be less than the total (it's the gap time)
   - All three bars should sum to approximately the total task duration
4. Add this temporary console.log before the forEach to verify data is correct:

```javascript
console.log('BD nodes:', nodes.map(function(n) {
    return { label: n.label, type: n.type, kind: n.kind, eventType: n.eventType, ms: n.durationMs };
}));
```

After confirming, remove the console.log.

---

## Files to Change

| File | Function | Change |
|---|---|---|
| `Inboundhiveboard.js` | `fetchTimeline()` → `.map()` return object | Add `payload.data.duration_ms` fallback for LLM calls in the `durationMs` field |
| `Inboundhiveboard.js` | `computeDurationBreakdown()` → `forEach` | Skip task lifecycle events at top of loop; compute Other as remainder after loop |
| `Inboundhiveboard.js` | `computeDurationBreakdown()` → tool check | Tighten to `action_completed`/`action_failed` only (drop `n.type === 'action'`) |

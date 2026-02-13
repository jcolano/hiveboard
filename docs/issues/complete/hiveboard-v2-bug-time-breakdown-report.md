# Bug Fix: Time Breakdown shows 0% LLM / 100% Other — Report

**Spec:** `hiveboard-v2-bug-time-breakdown.md`
**Status:** Complete
**Date:** 2026-02-12
**Files modified:** 1 (`hiveboard.js`)

---

## What Was Fixed

The Time Breakdown panel always showed `LLM 0ms (0%)` and `Other 26.6s (100%)` even on tasks with multiple LLM calls totaling 20+ seconds. Two bugs in the data pipeline caused this:

1. LLM call durations were read from the wrong field (always null, defaulting to 0)
2. The tool-time check was overly broad, potentially matching `action_started` nodes via `n.type === 'action'`

---

## Changes

### `src/static/js/hiveboard.js`

**Bug A fix — `fetchTimeline()`, line 262:**

LLM call events are `event_type: "custom"` and carry their duration in `payload.data.duration_ms`, not the top-level `e.duration_ms` (which is null for custom events). Added a fallback:

```javascript
// Before:
durationMs: e.duration_ms || 0,

// After:
durationMs: e.duration_ms || (kind === 'llm_call' && payload.data ? payload.data.duration_ms : null) || 0,
```

This reads the top-level field first (correct for action events), falls back to `payload.data.duration_ms` for LLM calls, and defaults to 0.

**Bug B (tool check tightening) — `computeDurationBreakdown()`, line 560:**

Removed `n.type === 'action'` from the tool duration check to prevent potential double-counting from `action_started` nodes that share the same `type`:

```javascript
// Before:
} else if (n.type === 'action' || n.eventType === 'action_completed' || n.eventType === 'action_failed') {

// After:
} else if (n.eventType === 'action_completed' || n.eventType === 'action_failed') {
```

Note: `computeDurationBreakdown()` already had correct handling for task lifecycle event exclusion (lines 550-553) and "Other" as remainder computation (lines 570-573). These were present from a prior partial fix. Bug A was the primary cause of the visible symptom.

---

## Root Cause Summary

| Bug | Field read | Actual value | Effect |
|-----|-----------|-------------|--------|
| A | `e.duration_ms` for LLM events | `null` (custom events don't have top-level duration) | LLM bucket = 0ms |
| B (tool check) | `n.type === 'action'` | Could match non-completion action events | Minor: potential tool time inflation |

The critical fix is Bug A. Without it, every LLM node contributes 0ms regardless of how long the call actually took.

---

## Expected Result

For a task with 26.6s total, 6 LLM calls totaling ~20s, and tools totaling ~59ms:

```
Before:  LLM 0ms (0%)   | Tools 59ms (0%)  | Other 26.6s (100%)
After:   LLM 20.0s (75%) | Tools 59ms (0%)  | Other 6.5s (25%)
```

---

## Verification

- JS syntax validated with `node -c` — passes
- Visual/interactive testing requires running the server with the simulator and a browser

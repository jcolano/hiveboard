# HiveBoard — Data Flow Analysis: Backend API vs Dashboard JS

**Date:** 2026-02-12
**Purpose:** Map exactly what data the backend returns, what the JS extracts, and what gaps exist for the UI/UX redesign.

---

## Context

The [success criteria document](hiveboard-uiux-success-criteria.md) identified that "the backend is roughly 70% richer than what the UI surfaces." This document traces the exact data flow for 7 specific improvement areas to determine what's a pure frontend fix vs what needs backend changes.

---

## 1. Duration Breakdown — "Where does the time go?"

**Goal:** Show per-task breakdown of time spent in LLM calls vs tool execution vs overhead.

**Backend returns:**
- Timeline endpoint (`GET /v1/tasks/{id}/timeline`) returns individual events with `duration_ms`
- Each event has `event_type` (action_started, action_completed, custom with kind=llm_call)
- No pre-computed breakdown exists

**JS uses:**
- Extracts `e.duration_ms` per event and displays on timeline connector lines
- Does not aggregate or decompose

**Verdict: No backend change needed.** The JS can compute the breakdown client-side by summing `duration_ms` grouped by event type from the existing flat events list. For a cleaner solution, the backend could add a `duration_breakdown` field to the timeline response, but it's not required.

---

## 2. Token Ratio — tokens_in vs tokens_out per LLM call

**Goal:** Visualize input/output token ratio on LLM timeline nodes.

**Backend returns:**
- LLM call events include `payload.data.tokens_in` and `payload.data.tokens_out`
- Cost endpoint returns aggregate token totals
- Timeline endpoint returns full event payloads including these fields

**JS uses:**
- Timeline: extracts only `payload.data.model` (line 207) — **ignores tokens**
- Cost Explorer: reads `total_tokens_in`, `total_tokens_out` from cost endpoint — aggregate only
- Activity Stream: does not extract tokens

**Verdict: No backend change needed.** The data is already in the timeline response. The JS just needs to extract `payload.data.tokens_in` and `payload.data.tokens_out` from LLM call events and render them.

---

## 3. Agent Metadata — framework, runtime, sdk_version, environment, group

**Goal:** Show agent technical metadata in the detail view.

**Backend returns:**
- `AgentRecord` (stored in DB) has: `agent_type`, `agent_version`, `framework`, `runtime`, `environment`, `group`
- `AgentSummary` (API response model) includes: `framework`, `agent_version`, `environment`, `group`
- **BUG:** `environment` and `group` are hardcoded to `"production"` and `"default"` in the response builder (`app.py` ~line 548-550), not read from actual data
- **MISSING:** `runtime` and `sdk_version` are in `AgentRecord` but NOT in `AgentSummary`

**JS uses:**
- Extracts: `agent_id`, `agent_type`, `derived_status`, `current_task_id`, `heartbeat_age_seconds`, `stats_1h`, `sparkline_1h`, `processing_summary`
- **Does NOT extract:** `framework`, `environment`, `group`, `agent_version`

**Verdict: Backend change needed.**
1. Fix hardcoded `environment` and `group` — read from actual agent data
2. Add `runtime` and `sdk_version` to `AgentSummary` response model
3. JS needs to extract and display these fields

---

## 4. Action Tree — nested tool call visualization

**Goal:** Render the parent-child action hierarchy that `track_context()` creates.

**Backend returns:**
- Timeline endpoint builds the full action tree (`app.py` lines 744-785):
  - Constructs `actions` dict from action_started/completed/failed events
  - Nests children under parents via `parent_action_id`
  - Returns `action_tree` field in `TimelineSummary` response with structure:
    ```json
    [{"action_id": "...", "name": "...", "status": "...", "duration_ms": 800,
      "children": [{"name": "sub_action", "children": [...]}]}]
    ```

**JS uses:**
- `fetchTimeline()` (line 177) only reads `data.events` — flat event list
- **Completely ignores `data.action_tree`** — the nested structure is returned but never used

**Verdict: No backend change needed.** The tree is already built, nested, and returned. The JS just doesn't render it. This is purely a frontend rendering task.

---

## 5. Error Chains — exception details + action chain

**Goal:** Show the chain of events that led to a failure, with exception details.

**Backend returns:**
- Timeline endpoint builds `error_chains` (`app.py` lines 787-801):
  - Collects retry_started and escalated events
  - Links them via `parent_event_id`
  - Returns `error_chains` field in `TimelineSummary` response

**JS uses:**
- **Completely ignores `data.error_chains`**
- Error/retry rendering is done by filtering the flat event list and checking for `action_failed` type

**Verdict: No backend change needed.** Error chains are already built and returned. The JS just doesn't use them.

---

## 6. Fleet-wide Pipeline — aggregate queue/issues/TODOs across all agents

**Goal:** A fleet-level view showing all agents' operational state in one place.

**Backend returns:**
- Per-agent pipeline: `GET /v1/agents/{id}/pipeline` returns `PipelineState` (queue, todos, scheduled, issues)
- **No fleet-wide aggregation endpoint exists**

**JS uses:**
- `fetchPipelineData(agentId)` calls the per-agent endpoint
- Stores in `PIPELINE[agentId]` — one agent at a time

**Verdict: Backend change needed.** Need a new `GET /v1/pipeline` endpoint that:
- Iterates all agents
- Aggregates: total queue depth, total active issues, total active TODOs
- Returns per-agent summaries for drill-down
- This enables a fleet-level Pipeline view tab

---

## 7. Activity Stream Payload Richness — richer event cards

**Goal:** Show key payload fields (model, tokens, cost, error message) inline in stream events.

**Backend returns:**
- `GET /v1/events` returns full event objects including complete `payload` field
- Each event's payload contains: `kind`, `summary`, `data` (with all detail fields)

**JS uses:**
- `fetchEvents()` (lines 240-252) maps each event and extracts only:
  - `eventId`, `type`, `kind`, `agent`, `task`, `summary`, `timestamp`, `severity`
- **Discards all payload detail:** model name, token counts, cost, duration, error details, tool arguments, result previews

**Verdict: No backend change needed.** The full payload is already returned. The JS just needs to preserve more fields from the payload when mapping events, and the rendering code needs to display them.

---

## Summary

| # | Feature | Backend change? | What's needed |
|---|---------|----------------|---------------|
| 1 | Duration breakdown | **No** | JS computes from existing `duration_ms` per event |
| 2 | Token ratio | **No** | JS extracts existing `payload.data.tokens_in/out` |
| 3 | Agent metadata | **Yes** | Fix 2 bugs (hardcoded env/group), add 2 fields (`runtime`, `sdk_version`) |
| 4 | Action tree | **No** | JS renders existing `action_tree` response field |
| 5 | Error chains | **No** | JS renders existing `error_chains` response field |
| 6 | Fleet pipeline | **Yes** | New `GET /v1/pipeline` aggregation endpoint |
| 7 | Richer stream cards | **No** | JS preserves more payload fields |

**5 of 7 are pure frontend work. 2 need backend changes (both small).**

---

## Backend Changes Required

### Change 1: Fix agent metadata (Points 3)

**Files:** `src/shared/models.py`, `src/backend/app.py`

1. Add `runtime` and `sdk_version` to `AgentSummary` model
2. Fix `_agent_to_summary()` to read `environment` and `group` from actual agent data instead of hardcoding
3. Populate `runtime` and `sdk_version` from `AgentRecord`

### Change 2: Fleet pipeline endpoint (Point 6)

**Files:** `src/shared/storage.py`, `src/backend/storage_json.py`, `src/backend/app.py`

1. Add `get_fleet_pipeline()` method to `StorageBackend` protocol
2. Implement in `JsonStorageBackend` — iterate agents, call `get_pipeline()` for each, aggregate
3. Add `GET /v1/pipeline` endpoint in `app.py`
4. Response: `{ totals: {queue_depth, active_issues, active_todos}, agents: [{agent_id, queue_depth, active_issues, ...}] }`

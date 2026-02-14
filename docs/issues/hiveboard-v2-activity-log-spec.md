# Feature Spec: Human-Readable Activity Log

**Feature name:** Human-Readable Activity Log (template-based event narration)
**Priority:** Medium — transforms raw telemetry into a scannable ops narrative
**Depends on:** None — works with existing WebSocket events and `renderStream()`
**Backend changes:** None — purely client-side formatting layer
**LLM usage:** None — 100% mechanical string templates

---

## 1. Problem

The activity stream in the right sidebar currently displays raw event metadata:

```
llm_call
lead-qualifier › task_lead-4821
Lead scoring LLM call
  claude-sonnet-4  1.5K→200  $0.005  1.2s
```

This is useful for developers debugging specific payloads, but it fails as an **operational narrative**. An ops person glancing at the dashboard should be able to read the stream like a logbook:

> "lead-qualifier started processing lead-4821"
> "lead-qualifier called claude-sonnet-4 for lead scoring (1.5K tokens, $0.005)"
> "lead-qualifier completed lead-4821 in 3.2s"
> "support-triage hit a rate_limit issue on ticket-1050 (×3 occurrences)"

Today you have to mentally decode `event_type` + `payload.kind` + `summary` + detail tags to reconstruct what happened. The dashboard should do that work for you.

### What's wrong specifically

| Current element | Problem |
|---|---|
| Event type label (`llm_call`, `task_started`) | Machine identifier, not a sentence |
| `payload.summary` | Often just the action name or a developer-set string — no context about agent, task, or outcome |
| Detail tags (model, tokens, cost) | Useful data but scattered as badges, not woven into a narrative |
| Agent/task line | Always the same `agent › task` format regardless of what happened |

### What we want

A **single human-readable sentence per event** that reads like a logbook entry, with the structured detail tags preserved below it. The sentence replaces the current `summary` line; everything else stays.

---

## 2. Solution

Add a **template engine** — a pure JavaScript function `formatEventSentence(event)` that maps each `(event_type, payload.kind)` combination to a sentence template, fills placeholders from the event's fields, and returns a formatted string.

### Design principles

1. **No LLM.** Every sentence is a deterministic template with `${placeholder}` interpolation. Same event → same sentence, every time.
2. **No backend changes.** The formatting happens in the browser, using data already present in `STREAM_EVENTS` entries.
3. **Graceful degradation.** If a field is missing (null), the template omits that clause. If no template matches, fall back to the existing `e.summary` behavior.
4. **One sentence, not a paragraph.** Keep it under ~120 characters. The detail tags below carry the numbers.
5. **Agent name is always the subject.** Every sentence starts with (or prominently features) the agent that did the thing.

---

## 3. Template Registry

### 3.1 Template map

```javascript
var EVENT_TEMPLATES = {
  // ── Layer 0: Agent Lifecycle ──
  'agent_registered': '{agent} registered ({framework})',
  'heartbeat': '{agent} sent heartbeat',

  // ── Layer 1: Task Lifecycle ──
  'task_started': '{agent} started {taskType} {task}',
  'task_completed': '{agent} completed {task} in {duration}',
  'task_failed': '{agent} failed {task}: {error}',

  // ── Layer 1: Action Lifecycle ──
  'action_started': '{agent} began {summary}',
  'action_completed': '{agent} finished {summary} in {duration}',
  'action_failed': '{agent} failed on {summary}: {error}',

  // ── Layer 2: Narrative Telemetry ──
  'retry_started': '{agent} retrying {summary}',
  'escalated': '{agent} escalated {task}: {summary}',
  'approval_requested': '{agent} requested approval for {summary}',
  'approval_received': '{agent} received approval from {approver}',
  'custom': '{agent}: {summary}',
};

// ── Payload Kind overrides (take precedence when kind is set) ──
var KIND_TEMPLATES = {
  'llm_call': '{agent} called {model} for {llmName} ({tokensIn} tokens)',
  'issue': '{agent} reported {issueSeverity} {category} issue: {summary}',
  'issue:resolved': '{agent} resolved {category} issue: {summary}',
  'queue_snapshot': '{agent} queue depth: {queueDepth} items',
  'todo': '{agent} {todoAction} todo: {summary}',
  'todo:completed': '{agent} completed todo: {summary}',
  'plan_created': '{agent} created plan with {stepCount} steps',
  'plan_step:started': '{agent} started step {stepIndex}/{totalSteps}: {summary}',
  'plan_step:completed': '{agent} completed step {stepIndex}/{totalSteps}',
  'plan_step:failed': '{agent} failed on step {stepIndex}/{totalSteps}: {summary}',
  'scheduled': '{agent} reported {scheduledCount} scheduled items',
};
```

### 3.2 Template resolution order

```
1. KIND_TEMPLATES[kind + ':' + action]     (e.g., "issue:resolved", "plan_step:failed")
2. KIND_TEMPLATES[kind]                     (e.g., "llm_call", "issue")
3. EVENT_TEMPLATES[event_type]              (e.g., "task_completed")
4. Fallback: e.summary || e.type            (raw, as today)
```

This means a `plan_step` event with `action=failed` gets a specific failure template, while a generic `plan_step` with `action=started` gets its own template. If neither matches, the event type template is used. If nothing matches at all, the existing `summary` field is shown unchanged.

### 3.3 Placeholder definitions

| Placeholder | Source | Fallback |
|---|---|---|
| `{agent}` | `e.agent` | `'unknown'` |
| `{task}` | `e.task` | omit clause |
| `{taskType}` | `e.taskType` (from task_type field) | omit, just show task ID |
| `{summary}` | `e.summary` | omit clause |
| `{duration}` | `fmtDuration(e.durationMs)` | omit clause |
| `{error}` | `e.errorMessage` or `e.errorType` | `'unknown error'` |
| `{model}` | `e.model` (short form, strip date suffix) | `'LLM'` |
| `{llmName}` | `e.llmName` or `e.summary` | `'inference'` |
| `{tokensIn}` | `fmtTokens(e.tokensIn)` | omit clause |
| `{cost}` | `'$' + e.cost.toFixed(3)` | omit clause |
| `{approver}` | `e.approver` | `'reviewer'` |
| `{issueSeverity}` | `e.issueSeverity` (from payload.data.severity) | omit |
| `{category}` | `e.category` | omit |
| `{queueDepth}` | `e.queueDepth` | `'?'` |
| `{todoAction}` | `e.todoAction` (created/completed/failed/dismissed/deferred) | `'updated'` |
| `{stepIndex}` | `e.stepIndex` (from payload.data.step_index) | `'?'` |
| `{totalSteps}` | `e.totalSteps` (from payload.data.total_steps) | `'?'` |
| `{stepCount}` | `e.stepCount` (from payload.data.steps.length) | `'?'` |
| `{scheduledCount}` | `e.scheduledCount` (from payload.data.items.length) | `'?'` |
| `{framework}` | `e.framework` | omit clause |

### 3.4 Clause omission

When a placeholder resolves to `null`/`undefined`, the **clause containing it** is removed, not just the placeholder. Example:

```
Template:  '{agent} completed {task} in {duration}'
Data:      { agent: 'lead-qualifier', task: 'task_lead-42', durationMs: null }
Output:    'lead-qualifier completed task_lead-42'
                                                    ← " in {duration}" dropped
```

Rules:
- A "clause" is the placeholder plus its preceding connector word/punctuation
- Connectors recognized: ` in `, ` for `, ` from `, `: `, ` (`, `)`, ` — `
- If the placeholder is the last token and has a prefix connector, drop the connector too
- If a parenthesized clause like `({tokensIn} tokens)` has a null placeholder, drop the entire `(...)` group

---

## 4. Implementation

### 4.1 The formatter function

```javascript
function formatEventSentence(e) {
  // Step 1: Select template
  var template = null;
  var action = e.action || null;  // From payload.data.action (todo, plan_step, issue)

  if (e.kind && action) {
    template = KIND_TEMPLATES[e.kind + ':' + action];
  }
  if (!template && e.kind) {
    template = KIND_TEMPLATES[e.kind];
  }
  if (!template) {
    template = EVENT_TEMPLATES[e.type];
  }
  if (!template) {
    // Fallback: existing behavior
    return escHtml(e.summary || e.type);
  }

  // Step 2: Build placeholder values
  var vals = {
    agent: e.agent || 'unknown',
    task: e.task || null,
    taskType: e.taskType || null,
    summary: e.summary || null,
    duration: e.durationMs != null ? fmtDuration(e.durationMs) : null,
    error: e.errorMessage || e.errorType || null,
    model: e.model ? shortModelName(e.model) : null,
    llmName: e.llmName || e.summary || null,
    tokensIn: e.tokensIn != null ? fmtTokens(e.tokensIn) : null,
    cost: e.cost != null ? '$' + e.cost.toFixed(3) : null,
    approver: e.approver || null,
    issueSeverity: e.issueSeverity || null,
    category: e.category || null,
    queueDepth: e.queueDepth != null ? String(e.queueDepth) : null,
    todoAction: e.todoAction || null,
    stepIndex: e.stepIndex != null ? String(e.stepIndex) : null,
    totalSteps: e.totalSteps != null ? String(e.totalSteps) : null,
    stepCount: e.stepCount != null ? String(e.stepCount) : null,
    scheduledCount: e.scheduledCount != null ? String(e.scheduledCount) : null,
    framework: e.framework || null,
  };

  // Step 3: Interpolate with clause omission
  var result = interpolateTemplate(template, vals);

  return escHtml(result);
}
```

### 4.2 Template interpolation with clause omission

```javascript
function interpolateTemplate(template, vals) {
  // First pass: handle parenthesized groups like ({tokensIn} tokens)
  // If any placeholder inside parens is null, remove the entire group
  var result = template.replace(/\(([^)]*\{(\w+)\}[^)]*)\)/g, function(match, inner, key) {
    if (vals[key] == null) return '';
    // Replace placeholders inside the parens
    var filled = inner.replace(/\{(\w+)\}/g, function(m, k) {
      return vals[k] != null ? vals[k] : '';
    });
    return '(' + filled.trim() + ')';
  });

  // Second pass: handle remaining placeholders with connectors
  // Pattern: (connector)(placeholder)
  // Connectors: " in ", " for ", " from ", ": ", " — ", " with "
  result = result.replace(/((?:\s+(?:in|for|from|with|on)\s+)|(?::\s*)|(?:\s*—\s*))?(\{(\w+)\})/g,
    function(match, connector, placeholder, key) {
      if (vals[key] == null) return '';  // Drop connector + placeholder
      return (connector || '') + vals[key];
    }
  );

  // Third pass: clean up any remaining placeholders (no connector)
  result = result.replace(/\{(\w+)\}/g, function(match, key) {
    return vals[key] != null ? vals[key] : '';
  });

  // Clean up: collapse multiple spaces, trim
  result = result.replace(/\s{2,}/g, ' ').trim();

  return result;
}
```

### 4.3 Model name shortener

```javascript
function shortModelName(model) {
  if (!model) return 'LLM';
  // "claude-sonnet-4-5-20250929" → "claude-sonnet-4-5"
  // "gpt-4o-mini-2024-07-18" → "gpt-4o-mini"
  return model.replace(/-\d{8,}$/, '');
}
```

### 4.4 New event fields to extract

The `handleWsMessage()` function already extracts most fields we need. Add these additional extractions:

```javascript
// In handleWsMessage(), inside the newEvent object:
var newEvent = {
  // ... existing fields ...

  // ★ Activity Log: additional fields for sentence templates
  taskType: e.task_type || null,
  action: pd.action || null,              // todo action, plan_step action, issue action
  issueSeverity: pd.severity || null,     // issue payload severity (not event severity)
  todoAction: pd.action || null,          // created/completed/failed/dismissed/deferred
  stepIndex: pd.step_index || null,
  totalSteps: pd.total_steps || null,
  stepCount: (pd.steps && pd.steps.length) ? pd.steps.length : null,
  scheduledCount: (pd.items && pd.items.length) ? pd.items.length : null,
  framework: e.framework || null,
};
```

Same additions in `fetchEvents()` where stream events are built from REST API responses.

---

## 5. Rendering Changes

### 5.1 Replace summary line in `renderStream()`

Current rendering of the event body:

```javascript
// CURRENT (lines 1042-1046):
<div class="stream-event-body">
  <div class="stream-event-agent">
    <span class="clickable-entity" onclick="selectAgent('${e.agent}')">${escHtml(e.agent)}</span>
    ${e.task ? ` › <span class="clickable-entity" onclick="selectTask('${e.task}')">${escHtml(e.task)}</span>` : ''}
  </div>
  ${escHtml(e.summary)}
</div>
```

Replace with:

```javascript
// NEW:
<div class="stream-event-body">
  <div class="stream-event-sentence">${formatEventSentence(e)}</div>
</div>
```

**Key change:** The agent name and task ID are now **embedded in the sentence** rather than being a separate line above the summary. They become part of the narrative flow.

### 5.2 Clickable entities within the sentence

The agent name and task ID inside the sentence should still be clickable. `formatEventSentence()` should wrap them in clickable spans:

```javascript
// In formatEventSentence(), after building the sentence:
// Wrap the agent name in a clickable span
if (e.agent) {
  var agentSpan = '<span class="clickable-entity" onclick="selectAgent(\'' + escHtml(e.agent) + '\')">' + escHtml(e.agent) + '</span>';
  result = result.replace(escHtml(e.agent), agentSpan);
}
// Wrap the task ID in a clickable span
if (e.task) {
  var taskSpan = '<span class="clickable-entity" onclick="selectTask(\'' + escHtml(e.task) + '\')">' + escHtml(e.task) + '</span>';
  result = result.replace(escHtml(e.task), taskSpan);
}
```

**Note:** Because the sentence now contains HTML (clickable spans), the function should escape all user-provided values first, then inject the HTML spans. The function should NOT `escHtml()` the final output — it returns trusted HTML.

Updated function signature comment:

```javascript
/**
 * Returns HTML string with clickable agent/task spans embedded in the sentence.
 * All user-provided values are escaped before interpolation.
 * Caller should NOT re-escape the output.
 */
function formatEventSentence(e) { ... }
```

### 5.3 Detail tags stay unchanged

The `buildStreamDetailTags(e)` function and its rendering remain exactly the same. The sentence replaces only the summary text; the tags below it continue to show model, tokens, cost, duration, severity, etc. as structured badges.

### 5.4 Event type label stays

The top row of each stream event (event type label + timestamp) remains unchanged. The sentence is the second row.

**Before:**
```
llm_call                           2m ago
lead-qualifier › task_lead-4821
Lead scoring LLM call
  claude-sonnet-4  1.5K→200  $0.005  1.2s
```

**After:**
```
llm_call                           2m ago
lead-qualifier called claude-sonnet-4 for lead scoring (1.5K tokens)
  claude-sonnet-4  1.5K→200  $0.005  1.2s
```

---

## 6. Example Outputs

### 6.1 Full event lifecycle

| Event | Template used | Output sentence |
|---|---|---|
| `task_started`, agent=lead-qualifier, task=task_lead-42, taskType=lead_processing | `EVENT_TEMPLATES['task_started']` | **lead-qualifier** started lead_processing **task_lead-42** |
| `action_completed`, kind=llm_call, model=claude-sonnet-4-20250514, llmName=lead_scoring, tokensIn=1500 | `KIND_TEMPLATES['llm_call']` | **lead-qualifier** called claude-sonnet-4 for lead_scoring (1.5K tokens) |
| `action_completed`, kind=llm_call, model=gpt-4o-mini-2024-07-18, llmName=enrichment, tokensIn=800 | `KIND_TEMPLATES['llm_call']` | **lead-qualifier** called gpt-4o-mini for enrichment (800 tokens) |
| `action_failed`, errorType=RateLimitError, summary=enrichment | `EVENT_TEMPLATES['action_failed']` | **lead-qualifier** failed on enrichment: RateLimitError |
| `retry_started`, summary=enrichment | `EVENT_TEMPLATES['retry_started']` | **lead-qualifier** retrying enrichment |
| `task_completed`, task=task_lead-42, durationMs=4200 | `EVENT_TEMPLATES['task_completed']` | **lead-qualifier** completed **task_lead-42** in 4.2s |

### 6.2 Issues and operational events

| Event | Output sentence |
|---|---|
| kind=issue, category=rate_limit, issueSeverity=high, summary=OpenAI API throttled | **lead-qualifier** reported high rate_limit issue: OpenAI API throttled |
| kind=issue, action=resolved, category=rate_limit, summary=OpenAI API throttled | **lead-qualifier** resolved rate_limit issue: OpenAI API throttled |
| kind=todo, action=created, summary=Update KB with new resolution | **support-triage** created todo: Update KB with new resolution |
| kind=todo, action=completed, summary=Update KB with new resolution | **support-triage** completed todo: Update KB with new resolution |
| kind=plan_created, stepCount=4 | **data-pipeline** created plan with 4 steps |
| kind=plan_step, action=completed, stepIndex=2, totalSteps=4 | **data-pipeline** completed step 2/4 |
| kind=queue_snapshot, queueDepth=12 | **data-pipeline** queue depth: 12 items |
| type=approval_requested, summary=Deploy to production | **lead-qualifier** requested approval for Deploy to production |
| type=approval_received, approver=ops-lead | **lead-qualifier** received approval from ops-lead |

### 6.3 Missing fields (graceful degradation)

| Scenario | Output |
|---|---|
| `task_completed`, durationMs=null | lead-qualifier completed task_lead-42 |
| `llm_call`, tokensIn=null | lead-qualifier called claude-sonnet-4 for lead_scoring |
| `action_failed`, errorMessage=null, errorType=null | lead-qualifier failed on enrichment |
| Unknown event type, no template match | *(falls back to e.summary as today)* |

---

## 7. State Changes

### 7.1 New global constants (add near top of file)

```javascript
var EVENT_TEMPLATES = { /* ... as defined in Section 3.1 ... */ };
var KIND_TEMPLATES = { /* ... as defined in Section 3.1 ... */ };
```

### 7.2 New functions

| Function | Purpose |
|---|---|
| `formatEventSentence(e)` | Main entry point: resolves template, fills placeholders, returns HTML |
| `interpolateTemplate(template, vals)` | Generic template interpolation with clause omission |
| `shortModelName(model)` | Strips date suffix from model names |

### 7.3 Modified functions

| Function | Change |
|---|---|
| `renderStream()` | Replace the agent line + summary with `formatEventSentence(e)` output |
| `handleWsMessage()` | Extract additional fields: `taskType`, `action`, `issueSeverity`, `todoAction`, `stepIndex`, `totalSteps`, `stepCount`, `scheduledCount`, `framework` |
| `fetchEvents()` | Same additional field extractions as `handleWsMessage()` |

### 7.4 No new state variables

This feature adds no mutable state. The template map and formatter are stateless, pure functions.

---

## 8. CSS Changes

Minimal — just style the new sentence element:

```css
/* ─── Activity Log Sentence ─── */

.stream-event-sentence {
  font-family: var(--font-sans);
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-primary);
  word-break: break-word;
}

.stream-event-sentence .clickable-entity {
  /* Inherits existing .clickable-entity styles */
  /* Agent names: accent color, task IDs: muted accent */
}
```

Remove or deprecate the `.stream-event-agent` class if it's no longer used (the agent/task line moves into the sentence).

---

## 9. Edge Cases

| Scenario | Behavior |
|---|---|
| Agent ID contains special characters | All values are `escHtml()`-escaped before interpolation |
| Summary is very long (>200 chars) | Truncate to 120 chars + `…` in the sentence. Full summary visible via detail tags or modal |
| Event type not in template map | Falls back to `e.summary \|\| e.type` — identical to current behavior |
| `payload.kind` set but not in `KIND_TEMPLATES` | Falls through to `EVENT_TEMPLATES[event_type]` |
| `action` field set for a kind without action-specific template | Falls through to the base kind template |
| Both `errorMessage` and `errorType` present | Prefer `errorMessage` (more descriptive) |
| Agent ID is `null` | Display `'unknown'` — should never happen per schema |
| Task ID is `null` | Omit from sentence. Many events (heartbeat, agent_registered) have no task |
| Custom event type | Template: `'{agent}: {summary}'` — just prefixes the agent name |
| Multiple events from same agent in rapid succession | Each gets its own sentence. No grouping (that's a separate future feature) |

---

## 10. What We're NOT Doing

To keep scope clear and honor the "no LLM" constraint:

- **No natural language generation.** Every sentence is a fixed template with variable substitution. No paraphrasing, no synonym selection, no tone variation.
- **No event grouping.** Each event gets its own sentence. "Agent completed 5 tasks" style summaries would require aggregation logic — future scope.
- **No event deduplication.** If the same event arrives twice, it gets the same sentence twice. Dedup is handled upstream by the `eventId` check.
- **No custom templates per tenant/agent.** The template map is hardcoded. User-configurable templates could be added later as a settings feature.
- **No i18n.** Templates are English only for v1.
- **No animation on new events.** Events already appear at the top of the list. No additional entrance animation needed.
- **No summary tooltip.** The raw `e.summary` is not shown anywhere if the template produces a sentence. If needed for debugging, it could go in a title attribute later.

---

## 11. Testing Checklist

- [ ] Every event type in `EventType` enum produces a readable sentence (not raw summary)
- [ ] Every payload kind in `PayloadKind` enum has at least one template
- [ ] `plan_step` events with action=started/completed/failed each get distinct sentences
- [ ] `issue` events with action=resolved get the "resolved" template
- [ ] `todo` events with action=completed get the "completed" template
- [ ] LLM call sentences show the short model name (no date suffix)
- [ ] Missing `durationMs` on `task_completed` produces a clean sentence without "in"
- [ ] Missing `tokensIn` on `llm_call` produces a clean sentence without "(... tokens)"
- [ ] Missing `errorMessage` on `action_failed` produces "failed on {summary}" without trailing colon
- [ ] Agent names are clickable in sentences and call `selectAgent()`
- [ ] Task IDs are clickable in sentences and call `selectTask()`
- [ ] Detail tags still render below the sentence (model, tokens, cost badges)
- [ ] Events arriving via WebSocket use the formatter
- [ ] Events loaded via REST polling use the formatter
- [ ] Fallback: unknown event type with no template shows raw summary
- [ ] XSS: agent/task IDs with HTML chars are properly escaped
- [ ] Long summaries (>120 chars) are truncated in the sentence

---

## 12. Files to Modify

| File | Changes |
|---|---|
| `src/static/js/hiveboard.js` | Add `EVENT_TEMPLATES`, `KIND_TEMPLATES` constants. Add `formatEventSentence()`, `interpolateTemplate()`, `shortModelName()`. Modify `renderStream()` to use `formatEventSentence()`. Modify `handleWsMessage()` and `fetchEvents()` to extract additional fields (`taskType`, `action`, `issueSeverity`, `todoAction`, `stepIndex`, `totalSteps`, `stepCount`, `scheduledCount`, `framework`). |
| `src/static/css/hiveboard.css` | Add `.stream-event-sentence` styles (~5 lines). Remove or deprecate `.stream-event-agent` if no longer used. |
| Backend | None |

---

## 13. Future Enhancements (Out of Scope for v1)

| Enhancement | Description |
|---|---|
| **Event grouping** | "lead-qualifier completed 3 leads in 45s" — collapses rapid same-type events |
| **Severity coloring** | Error sentences in red, warnings in amber, within the sentence text |
| **Custom templates** | Tenant-configurable template overrides via settings API |
| **i18n** | Template sets for other languages |
| **Agent nicknames** | Show "Lead Qualifier" instead of "lead-qualifier" via agent metadata |
| **Relative task references** | "completed the current task" when context is obvious |

---

## 14. Relationship to Existing Components

This feature is **additive and non-breaking**:

- `buildStreamDetailTags()` — unchanged, still renders the structured badges
- `renderStream()` — modified minimally: replaces 4 lines (agent line + summary) with 1 line (sentence)
- `handleWsMessage()` — adds ~8 field extractions to existing object literal
- `fetchEvents()` — mirrors the same additions
- No new HTML elements needed
- No new API calls
- No new WebSocket messages

The activity stream continues to work identically for filtering, scrolling, and event count — only the display text of each event changes.

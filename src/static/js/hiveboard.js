// ═══════════════════════════════════════════════════
//  HIVEBOARD v2 — CONFIGURATION
// ═══════════════════════════════════════════════════

// CONFIG is defined in common.js (loaded first via <script> order in index.html).
// hiveboard.js reads from it — do not re-declare.

// ═══════════════════════════════════════════════════
//  MUTABLE STATE
// ═══════════════════════════════════════════════════

let AGENTS = [];
let TASKS = [];
let TIMELINES = {};       // taskId → { plan, nodes, actionTree, errorChains }
let PIPELINE = {};        // agentId → pipeline data
let FLEET_PIPELINE = null; // { totals, agents }
let COST_DATA = null;
let STREAM_EVENTS = [];
let metricsData = null;

// UI state
let selectedAgent = null;
let selectedTask = null;
let activeStreamFilter = 'all';
let pinnedNode = null;
let statusFilter = null;
let currentView = 'mission';
let agentDetailAgent = null;
let activeDetailTab = 'tasks';
let timelineViewMode = 'tree'; // 'tree' or 'flat'

// Connection state
let ws = null;
let wsRetryCount = 0;
let pollTimer = null;
let isConnected = false;
let lastEventTimestamp = null;

// Timeline auto-scroll state
let timelineAutoScroll = true;

// LLM modal state
let llmModalOpen = false;
let llmModalData = null;

// Cost range state
let currentCostRange = '24h';

// Cost drilldown state
let costExpandedAgent = null;
let costExpandedModel = null;
let costDrilldownData = [];
let costDrilldownCursor = null;
let costDrilldownLoading = false;

// ═══════════════════════════════════════════════════
//  CONSTANTS
// ═══════════════════════════════════════════════════

const statusSort = { stuck: 0, error: 1, waiting_approval: 2, processing: 3, idle: 4 };
const statusLabel = { processing: 'Processing', stuck: 'Stuck', error: 'Error', idle: 'Idle', waiting_approval: 'Waiting', completed: 'Completed' };
const statusBadge = { processing: 'badge-processing', stuck: 'badge-stuck', error: 'badge-error', idle: 'badge-idle', waiting_approval: 'badge-waiting', completed: 'badge-completed' };
const statusColor = { completed: 'var(--success)', processing: 'var(--active)', failed: 'var(--error)', stuck: 'var(--stuck)', waiting: 'var(--warning)', escalated: 'var(--warning)' };
const typeColor = { system: 'var(--idle)', action: 'var(--active)', warning: 'var(--warning)', human: 'var(--success)', success: 'var(--success)', error: 'var(--error)', retry: 'var(--warning)', stuck: 'var(--stuck)', waiting: 'var(--warning)', llm: 'var(--llm)' };
const SEVERITY_COLOR = { debug: 'var(--idle)', info: 'var(--active)', warn: 'var(--warning)', error: 'var(--error)' };
const STREAM_FILTERS = ['all', 'task', 'action', 'error', 'llm', 'pipeline', 'human'];
const KIND_ICON = { llm_call: '◆', queue_snapshot: '⊞', todo: '☐', issue: '⚑', scheduled: '⏲' };

// ★ Tree node type icons
const TREE_ICON = { llm: '◆', action: '⚡', error: '✗', success: '✓', system: '▶', warning: '💭', human: '👤', retry: '↻' };

// ═══════════════════════════════════════════════════
//  NARRATIVE LOG — Template Registry
// ═══════════════════════════════════════════════════

var EVENT_TEMPLATES = {
  'agent_registered': '{agent} registered ({framework})',
  'heartbeat': '{agent} sent heartbeat',
  'task_started': '{agent} started {taskType} {task}',
  'task_completed': '{agent} completed {task} in {duration}',
  'task_failed': '{agent} failed {task}: {error}',
  'action_started': '{agent} began {summary}',
  'action_completed': '{agent} finished {summary} in {duration}',
  'action_failed': '{agent} failed on {summary}: {error}',
  'retry_started': '{agent} retrying {summary}',
  'escalated': '{agent} escalated {task}: {summary}',
  'approval_requested': '{agent} requested approval for {summary}',
  'approval_received': '{agent} received approval from {approver}',
  'custom': '{agent}: {summary}',
};

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

function shortModelName(model) {
  if (!model) return 'LLM';
  return model.replace(/-\d{8,}$/, '');
}

function interpolateTemplate(template, vals) {
  // Pass 1: parenthesized groups — drop entire group if key is null
  var result = template.replace(/\(([^)]*\{(\w+)\}[^)]*)\)/g, function(match, inner, key) {
    if (vals[key] == null) return '';
    var filled = inner.replace(/\{(\w+)\}/g, function(m, k) {
      return vals[k] != null ? vals[k] : '';
    });
    return '(' + filled.trim() + ')';
  });

  // Pass 2: connector + placeholder — drop both if null
  result = result.replace(/((?:\s+(?:in|for|from|with|on)\s+)|(?::\s*)|(?:\s*—\s*))?(\{(\w+)\})/g,
    function(match, connector, placeholder, key) {
      if (vals[key] == null) return '';
      return (connector || '') + vals[key];
    }
  );

  // Pass 3: remaining placeholders
  result = result.replace(/\{(\w+)\}/g, function(match, key) {
    return vals[key] != null ? vals[key] : '';
  });

  return result.replace(/\s{2,}/g, ' ').trim();
}

function formatEventSentence(e) {
  // 1. Select template (kind:action > kind > event_type > fallback)
  var template = null;
  var action = e.action || null;
  if (e.kind && action) template = KIND_TEMPLATES[e.kind + ':' + action];
  if (!template && e.kind) template = KIND_TEMPLATES[e.kind];
  if (!template) template = EVENT_TEMPLATES[e.type];
  if (!template) return escHtml(e.summary || e.type);

  // 2. Build placeholder values (all escaped)
  var truncSummary = e.summary;
  if (truncSummary && truncSummary.length > 120) truncSummary = truncSummary.slice(0, 117) + '...';

  var vals = {
    agent: escHtml(e.agent || 'unknown'),
    task: e.task ? escHtml(e.task) : null,
    taskType: e.taskType ? escHtml(e.taskType) : null,
    summary: truncSummary ? escHtml(truncSummary) : null,
    duration: e.durationMs != null ? fmtDuration(e.durationMs) : null,
    error: e.errorMessage ? escHtml(e.errorMessage) : (e.errorType ? escHtml(e.errorType) : null),
    model: e.model ? shortModelName(e.model) : null,
    llmName: e.llmName ? escHtml(e.llmName) : (e.summary ? escHtml(e.summary) : null),
    tokensIn: e.tokensIn != null ? fmtTokens(e.tokensIn) : null,
    cost: e.cost != null ? '$' + e.cost.toFixed(3) : null,
    approver: e.approver ? escHtml(e.approver) : null,
    issueSeverity: e.issueSeverity ? escHtml(e.issueSeverity) : null,
    category: e.category ? escHtml(e.category) : null,
    queueDepth: e.queueDepth != null ? String(e.queueDepth) : null,
    todoAction: e.todoAction ? escHtml(e.todoAction) : null,
    stepIndex: e.stepIndex != null ? String(e.stepIndex) : null,
    totalSteps: e.totalSteps != null ? String(e.totalSteps) : null,
    stepCount: e.stepCount != null ? String(e.stepCount) : null,
    scheduledCount: e.scheduledCount != null ? String(e.scheduledCount) : null,
    framework: e.framework ? escHtml(e.framework) : null,
  };

  // 3. Interpolate
  var result = interpolateTemplate(template, vals);

  // 4. Wrap agent/task as clickable (inject HTML after escaping)
  if (e.agent) {
    var agentText = escHtml(e.agent);
    var agentSpan = '<span class="clickable-entity" onclick="selectAgent(\'' + agentText + '\')">' + agentText + '</span>';
    result = result.replace(agentText, agentSpan);
  }
  if (e.task) {
    var taskText = escHtml(e.task);
    var taskSpan = '<span class="clickable-entity" onclick="selectTask(\'' + taskText + '\')">' + taskText + '</span>';
    result = result.replace(taskText, taskSpan);
  }

  return result;
}

// ═══════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════

function hbClass(seconds) {
  if (seconds == null) return 'hb-dead';
  return seconds < 60 ? 'hb-fresh' : seconds < 300 ? 'hb-stale' : 'hb-dead';
}
function hbText(seconds) {
  if (seconds == null) return '—';
  return seconds < 60 ? seconds + 's ago' : Math.floor(seconds / 60) + 'm ago';
}
function fmtTokens(n) {
  if (n == null) return '—';
  return n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n);
}
function fmtDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return ms + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return Math.floor(ms / 60000) + 'm' + Math.round((ms % 60000) / 1000) + 's';
}
function fmtCost(c, costSource) {
  if (c == null) return '—';
  if (costSource === 'estimated') return '~$' + c.toFixed(2);
  return '$' + c.toFixed(2);
}
function timeAgo(ts) {
  if (!ts) return '—';
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  if (diff < 5) return 'just now';
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  return Math.floor(diff / 3600) + 'h ago';
}
function escHtml(s) {
  if (s == null) return '';
  const d = document.createElement('div');
  d.textContent = String(s);
  return d.innerHTML;
}

// ★ Token ratio bar helper
function tokenBarHtml(tokIn, tokOut) {
  if (tokIn == null && tokOut == null) return '';
  const tIn = tokIn || 0;
  const tOut = tokOut || 0;
  const max = Math.max(tIn, tOut, 1);
  const wIn = Math.round((tIn / max) * 40);
  const wOut = Math.round((tOut / max) * 40);
  return `<span class="token-bar-container">` +
    `<span class="token-bar in" style="width:${wIn}px" title="${tIn} tokens in"></span>` +
    `<span class="token-bar out" style="width:${wOut}px" title="${tOut} tokens out"></span>` +
    `<span class="token-label">${fmtTokens(tIn)}→${fmtTokens(tOut)}</span></span>`;
}

// ═══════════════════════════════════════════════════
//  API CLIENT
// ═══════════════════════════════════════════════════

async function apiFetch(path, params) {
  const url = new URL(CONFIG.endpoint + path);
  if (params) {
    Object.entries(params).forEach(function(kv) {
      if (kv[1] != null) url.searchParams.set(kv[0], kv[1]);
    });
  }
  try {
    const resp = await fetch(url.toString(), {
      headers: { 'Authorization': 'Bearer ' + CONFIG.apiKey },
    });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return await resp.json();
  } catch (err) {
    console.warn('API fetch failed:', path, err.message);
    showToast('API error: ' + path + ' — ' + err.message, true);
    return null;
  }
}

// ═══════════════════════════════════════════════════
//  DATA FETCHERS
// ═══════════════════════════════════════════════════

async function fetchAgents() {
  const env = document.getElementById('envSelector').value;
  const params = { sort: 'attention', limit: 100 };
  if (env && env !== 'all' && env !== '') params.environment = env;
  const data = await apiFetch('/v1/agents', params);
  if (!data || !data.data) return;
  AGENTS = data.data.map(function(a) {
    const stats = a.stats_1h || {};
    return {
      id: a.agent_id,
      type: a.agent_type || 'general',
      status: a.derived_status || 'idle',
      task: a.current_task_id,
      hb: a.heartbeat_age_seconds,
      sparkline: a.sparkline_1h || [0,0,0,0,0,0,0,0,0,0,0,0],
      queueDepth: stats.queue_depth || 0,
      activeIssues: stats.active_issues || 0,
      processingSummary: a.processing_summary || null,
      // ★ Feature 3: Agent metadata
      framework: a.framework || null,
      runtime: a.runtime || null,
      sdkVersion: a.sdk_version || null,
      environment: a.environment || null,
      group: a.group || null,
    };
  });
}

async function fetchTasks(agentId) {
  const env = document.getElementById('envSelector').value;
  const params = { limit: 30, sort: 'newest' };
  if (env && env !== 'all' && env !== '') params.environment = env;
  if (agentId) params.agent_id = agentId;
  const data = await apiFetch('/v1/tasks', params);
  if (!data || !data.data) return;
  TASKS = data.data.map(function(t) {
    return {
      id: t.task_id,
      agent: t.agent_id,
      type: t.task_type || '—',
      status: t.derived_status || 'processing',
      duration: fmtDuration(t.duration_ms),
      durationMs: t.duration_ms,
      cost: fmtCost(t.total_cost),
      llmCalls: t.llm_call_count || 0,
      time: timeAgo(t.started_at),
      startedAt: t.started_at,
    };
  });
}

async function fetchTimeline(taskId) {
  if (!taskId) return;
  const data = await apiFetch('/v1/tasks/' + encodeURIComponent(taskId) + '/timeline');
  if (!data) return;

  // ── Flat nodes (original behavior, preserved for flat view) ──
  var nodes = (data.events || []).map(function(e) {
    var payload = e.payload || {};
    var kind = payload.kind;
    var nodeType = 'system';
    if (e.event_type === 'task_started') nodeType = 'system';
    else if (e.event_type === 'task_completed') nodeType = 'success';
    else if (e.event_type === 'task_failed') nodeType = 'error';
    else if (e.event_type === 'action_started' || e.event_type === 'action_completed') nodeType = 'action';
    else if (e.event_type === 'action_failed') nodeType = 'error';
    else if (e.event_type === 'escalated') nodeType = 'warning';
    else if (e.event_type === 'approval_requested' || e.event_type === 'approval_received') nodeType = 'human';
    else if (e.event_type === 'retry_started') nodeType = 'retry';
    else if (kind === 'llm_call') nodeType = 'llm';
    else if (e.event_type === 'custom' && kind === 'llm_call') nodeType = 'llm';

    var rawLabel = payload.action_name || (payload.data && payload.data.action_name) || payload.summary || e.event_type;
    var label = rawLabel;
    if (rawLabel && rawLabel.length > 24) {
      var parts = rawLabel.split(/[_:]/);
      if (parts.length > 1) label = parts.slice(-2).join(' ');
      if (label.length > 24) label = label.substring(0, 21) + '…';
    }
    var time = e.timestamp ? e.timestamp.split('T')[1].substring(0, 12) : '—';
    var dur = e.duration_ms != null ? fmtDuration(e.duration_ms) : '—';
    var detail = Object.assign({ event: e.event_type }, payload.data || {});
    var tags = (payload.data && payload.data.tags) || [];
    var llmModel = (kind === 'llm_call' && payload.data) ? payload.data.model : null;
    var isRetry = e.event_type === 'retry_started';
    var isBranchStart = e.render_hint === 'branch_start' || (e.event_type === 'action_failed' && e.payload && e.payload.data && e.payload.data.will_retry);

    // ★ Feature 2: Extract token data for flat nodes too
    var tokensIn = (kind === 'llm_call' && payload.data) ? payload.data.tokens_in : null;
    var tokensOut = (kind === 'llm_call' && payload.data) ? payload.data.tokens_out : null;
    var llmCost = (kind === 'llm_call' && payload.data) ? payload.data.cost : null;

    // ★ LLM Modal: Extract prompt/response previews and metadata
    var promptPreview = (kind === 'llm_call' && payload.data) ? payload.data.prompt_preview : null;
    var responsePreview = (kind === 'llm_call' && payload.data) ? payload.data.response_preview : null;
    var llmMetadata = (kind === 'llm_call' && payload.data) ? payload.data.metadata : null;
    var llmName = (kind === 'llm_call' && payload.data) ? payload.data.name : null;

    return {
      label: label, rawLabel: rawLabel, time: time, type: nodeType, dur: dur,
      detail: detail, tags: tags, llmModel: llmModel,
      isBranch: isRetry, isBranchStart: isBranchStart,
      durationMs: e.duration_ms || (kind === 'llm_call' && payload.data ? payload.data.duration_ms : null) || 0,
      eventType: e.event_type,
      kind: kind,
      tokensIn: tokensIn,
      tokensOut: tokensOut,
      llmCost: llmCost,
      promptPreview: promptPreview,
      responsePreview: responsePreview,
      llmMetadata: llmMetadata,
      llmName: llmName,
      eventId: e.event_id,
      agentId: e.agent_id,
      taskId: e.task_id,
      timestamp: e.timestamp,
    };
  });

  // Build plan
  var plan = null;
  if (data.plan && data.plan.steps && data.plan.steps.length > 0) {
    plan = {
      steps: data.plan.steps.map(function(s) {
        var st = 'pending';
        if (s.completed_at) st = 'completed';
        else if (s.action === 'failed') st = 'failed';
        else if (s.started_at) st = 'active';
        return { desc: s.description || s.action || '', status: st };
      }),
    };
  }

  // ★ Feature 4: Store action tree from response
  var actionTree = data.action_tree || null;

  // ★ Enrich action tree with LLM events as pseudo-children
  if (actionTree) {
    var llmEvents = (data.events || []).filter(function(e) {
      return e.payload && e.payload.kind === 'llm_call';
    });
    if (llmEvents.length > 0) {
      var treeRoots = Array.isArray(actionTree) ? actionTree : [actionTree];
      // Build map of action_id → tree node for fast lookup
      var actionMap = {};
      (function mapTree(nodeList) {
        (nodeList || []).forEach(function(node) {
          if (node.action_id) actionMap[node.action_id] = node;
          mapTree(node.children);
        });
      })(treeRoots);

      llmEvents.forEach(function(e) {
        var pd = (e.payload && e.payload.data) || {};
        var llmNode = {
          action_id: e.event_id,
          name: pd.name || pd.model || (e.payload && e.payload.summary) || 'LLM call',
          status: 'completed',
          duration_ms: e.duration_ms || pd.duration_ms || null,
          type: 'llm_call',
          kind: 'llm_call',
          tokens_in: pd.tokens_in || null,
          tokens_out: pd.tokens_out || null,
          model: pd.model || null,
          cost: pd.cost || null,
          summary: e.payload && e.payload.summary,
          prompt_preview: pd.prompt_preview || null,
          response_preview: pd.response_preview || null,
          metadata: pd.metadata || null,
          event_id: e.event_id,
          agent_id: e.agent_id,
          task_id: e.task_id,
          timestamp: e.timestamp,
          children: [],
        };
        // Insert as child of parent action, or as root if no parent
        if (e.action_id && actionMap[e.action_id]) {
          actionMap[e.action_id].children.push(llmNode);
        } else {
          treeRoots.push(llmNode);
        }
      });
      actionTree = treeRoots;
    }
  }

  // ★ Feature 5: Store error chains from response
  var errorChains = data.error_chains || [];

  TIMELINES[taskId] = { plan: plan, nodes: nodes, actionTree: actionTree, errorChains: errorChains };
}

// ★ Feature 7: Richer event data extraction
async function fetchEvents(since) {
  var params = { limit: CONFIG.maxStreamEvents, exclude_heartbeats: true };
  if (since) params.since = since;
  var env = document.getElementById('envSelector').value;
  if (env && env !== 'all' && env !== '') params.environment = env;
  var data = await apiFetch('/v1/events', params);
  if (!data || !data.data) return;
  var newEvents = data.data.map(function(e) {
    var payload = e.payload || {};
    var pd = payload.data || {};
    return {
      eventId: e.event_id,
      type: e.event_type,
      kind: payload.kind || null,
      agent: e.agent_id,
      task: e.task_id,
      summary: payload.action_name || pd.action_name || payload.summary || e.event_type,
      time: timeAgo(e.timestamp),
      timestamp: e.timestamp,
      severity: e.severity || 'info',
      // ★ Enriched fields (Feature 7)
      model: pd.model || null,
      tokensIn: pd.tokens_in || null,
      tokensOut: pd.tokens_out || null,
      cost: pd.cost || null,
      durationMs: e.duration_ms || pd.duration_ms || null,
      errorMessage: pd.error_message || pd.exception_message || pd.error || null,
      errorType: pd.error_type || pd.exception_type || null,
      category: pd.category || null,
      occurrences: pd.occurrence_count || pd.occurrences || null,
      approver: pd.approver || pd.requested_from || null,
      toolName: pd.tool_name || pd.action_name || null,
      toolResult: pd.result || pd.output || null,
      queueDepth: pd.depth || pd.queue_depth || null,
      oldestAge: pd.oldest_age || pd.oldest || null,
      // ★ LLM Modal: prompt/response previews
      promptPreview: pd.prompt_preview || null,
      responsePreview: pd.response_preview || null,
      llmMetadata: pd.metadata || null,
      llmName: pd.name || null,
      // ★ Narrative log fields
      taskType: e.task_type || null,
      action: pd.action || null,
      issueSeverity: pd.severity || null,
      todoAction: pd.action || null,
      stepIndex: pd.step_index || null,
      totalSteps: pd.total_steps || null,
      stepCount: (pd.steps && pd.steps.length) ? pd.steps.length : null,
      scheduledCount: (pd.items && pd.items.length) ? pd.items.length : null,
      framework: e.framework || null,
    };
  });
  if (since) {
    var existingIds = {};
    STREAM_EVENTS.forEach(function(e) { existingIds[e.eventId] = true; });
    var truly = newEvents.filter(function(e) { return !existingIds[e.eventId]; });
    STREAM_EVENTS = truly.concat(STREAM_EVENTS).slice(0, CONFIG.maxStreamEvents);
  } else {
    STREAM_EVENTS = newEvents;
  }
  if (STREAM_EVENTS.length > 0) lastEventTimestamp = STREAM_EVENTS[0].timestamp;
}

async function fetchMetrics() {
  var env = document.getElementById('envSelector').value;
  var data = await apiFetch('/v1/metrics', { range: '1h', environment: env });
  if (data) metricsData = data;
}

async function fetchCostData() {
  var data = await apiFetch('/v1/cost', { range: currentCostRange });
  if (data) COST_DATA = data;
}

async function fetchPipelineData(agentId) {
  var data = await apiFetch('/v1/agents/' + encodeURIComponent(agentId) + '/pipeline');
  if (data) PIPELINE[agentId] = data;
}

// ★ Feature 6: Fleet pipeline
async function fetchFleetPipeline() {
  var data = await apiFetch('/v1/pipeline');
  if (data) FLEET_PIPELINE = data;
}

// ═══════════════════════════════════════════════════
//  RENDERING — HIVE (agents list)
// ═══════════════════════════════════════════════════

function renderHive() {
  const list = document.getElementById('hiveList');
  let agents = [...AGENTS].sort((a, b) => (statusSort[a.status] ?? 5) - (statusSort[b.status] ?? 5));
  if (statusFilter) agents = agents.filter(a => a.status === statusFilter);
  document.getElementById('agentCount').textContent = agents.length + ' agent' + (agents.length !== 1 ? 's' : '');

  const needsAttention = AGENTS.filter(a => a.status === 'stuck' || a.status === 'error').length;
  const badge = document.getElementById('attentionBadge');
  if (needsAttention > 0) { badge.style.display = 'inline'; badge.textContent = needsAttention + ' ⚠'; }
  else badge.style.display = 'none';

  if (agents.length === 0) { list.innerHTML = '<div class="empty-state"><span class="empty-state-icon">⬡</span>No agents match filter</div>'; return; }

  list.innerHTML = agents.map(a => {
    const maxSpark = Math.max(...a.sparkline, 1);
    const isUrgent = a.status === 'stuck' || a.status === 'error';
    const isSelected = (currentView === 'agentDetail' && agentDetailAgent === a.id) || a.id === selectedAgent;

    let pipelineHtml = '';
    const badges = [];
    if (a.queueDepth > 0) badges.push(`<span class="queue-badge ${a.queueDepth > 5 ? 'high' : ''}">Q:${a.queueDepth}</span>`);
    if (a.activeIssues > 0) badges.push(`<span class="issue-indicator"><span class="issue-dot"></span>${a.activeIssues} issue${a.activeIssues > 1 ? 's' : ''}</span>`);
    if (a.processingSummary) badges.push(`<span class="processing-line">↳ ${escHtml(a.processingSummary)}</span>`);
    if (badges.length > 0) pipelineHtml = `<div class="agent-card-pipeline">${badges.join('')}</div>`;

    // ★ Judge 1 fix: Current task line stays on card
    const taskLine = a.task ? `<div class="agent-task-info"><span style="opacity:0.5">↳</span> <span class="clickable-entity" onclick="event.stopPropagation(); selectTask('${a.task}')">${escHtml(a.task)}</span></div>` : '';

    // ★ Feature 3: Metadata tooltip (shows on hover)
    let metaTooltipParts = [];
    if (a.framework) metaTooltipParts.push(escHtml(a.framework));
    if (a.runtime) metaTooltipParts.push(escHtml(a.runtime));
    if (a.sdkVersion) metaTooltipParts.push('sdk ' + escHtml(a.sdkVersion));
    const metaTooltip = metaTooltipParts.length > 0
      ? `<div class="agent-meta-tooltip"><div class="agent-meta-tooltip-inner">${metaTooltipParts.map(t => `<span class="meta-tag">${t}</span>`).join('')}</div></div>`
      : '';

    return `
    <div class="agent-card fade-in ${isSelected ? 'selected' : ''} ${isUrgent ? 'urgency-glow' : ''} ${metaTooltipParts.length > 0 ? 'has-meta' : ''}"
         onclick="selectAgent('${a.id}')" ondblclick="openAgentDetail('${a.id}')" data-agent="${a.id}">
      ${metaTooltip}
      <div class="agent-card-top">
        <div class="agent-name">${escHtml(a.id)}</div>
        <div class="agent-status-badge ${statusBadge[a.status] || 'badge-idle'}">${statusLabel[a.status] || a.status}</div>
      </div>
      <div class="agent-card-meta">
        <div class="agent-type-label">${escHtml(a.type)}</div>
        <div class="heartbeat-indicator"><div class="hb-dot ${hbClass(a.hb)}"></div>${hbText(a.hb)}</div>
      </div>
      ${pipelineHtml}
      ${taskLine}
      <div class="sparkline-row">${a.sparkline.map(v => `<div class="spark-bar" style="height: ${(v / maxSpark) * 18 + 2}px; background: ${isUrgent ? 'var(--error)' : 'var(--active)'}"></div>`).join('')}</div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════
//  RENDERING — SUMMARY + METRICS
// ═══════════════════════════════════════════════════

function renderSummary() {
  const processing = AGENTS.filter(a => a.status === 'processing').length;
  const stuck = AGENTS.filter(a => a.status === 'stuck').length;
  const waiting = AGENTS.filter(a => a.status === 'waiting_approval').length;
  const errors = AGENTS.filter(a => a.status === 'error').length;
  const sf = statusFilter;
  const summary = metricsData && metricsData.summary ? metricsData.summary : {};
  const successRate = summary.success_rate != null ? Math.round(summary.success_rate) + '%' : '—';
  const avgDur = summary.avg_duration_ms != null ? fmtDuration(summary.avg_duration_ms) : '—';
  const totalCost = summary.total_cost != null ? '$' + summary.total_cost.toFixed(2) : '—';

  document.getElementById('summaryBar').innerHTML = `
    <div class="summary-stat"><div class="stat-label">Total Agents</div><div class="stat-value">${AGENTS.length}</div></div>
    <div class="summary-stat clickable ${sf === 'processing' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('processing')"><div class="stat-label">Processing</div><div class="stat-value blue">${processing}</div></div>
    <div class="summary-stat clickable ${sf === 'waiting_approval' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('waiting_approval')"><div class="stat-label">Waiting</div><div class="stat-value amber">${waiting}</div></div>
    <div class="summary-stat clickable ${sf === 'stuck' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('stuck')"><div class="stat-label">Stuck</div><div class="stat-value red">${stuck}</div></div>
    <div class="summary-stat clickable ${sf === 'error' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('error')"><div class="stat-label">Errors</div><div class="stat-value red">${errors}</div></div>
    <div class="summary-stat"><div class="stat-label">Success Rate (1h)</div><div class="stat-value green">${successRate}</div></div>
    <div class="summary-stat"><div class="stat-label">Avg Duration</div><div class="stat-value">${avgDur}</div></div>
    <div class="summary-stat"><div class="stat-label">Cost (1h)</div><div class="stat-value purple">${totalCost}</div></div>
  `;
}

function renderMetrics() {
  const ts = metricsData && metricsData.timeseries ? metricsData.timeseries : [];
  const metricDefs = [
    { label: 'Throughput (1h)', key: 'throughput', color: 'var(--active)' },
    { label: 'Success Rate', key: '_success_rate', color: 'var(--success)', max: 100 },
    { label: 'Errors', key: 'error_count', color: 'var(--error)' },
    { label: 'LLM Cost/Task', key: 'cost', color: 'var(--llm)' },
  ];
  document.getElementById('metricsRow').innerHTML = metricDefs.map(m => {
    let data;
    if (m.key === '_success_rate') {
      data = ts.map(p => {
        const c = p.tasks_completed || 0;
        const f = p.tasks_failed || 0;
        return c + f > 0 ? Math.round(c / (c + f) * 100) : 100;
      });
    } else {
      data = ts.map(p => p[m.key] || 0);
    }
    if (data.length === 0) data = [0,0,0,0,0,0,0,0];
    data = data.slice(-16);
    const mx = m.max || Math.max(...data, 1);
    return `<div class="metric-cell"><div class="stat-label">${m.label}</div><div class="metric-chart">${data.map(v => `<div class="metric-bar" style="height: ${(v / mx) * 22 + 2}px; background: ${m.color}; opacity: 0.6;"></div>`).join('')}</div></div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════
//  ★ FEATURE 1: DURATION BREAKDOWN
// ═══════════════════════════════════════════════════

function computeDurationBreakdown(nodes) {
  var llmMs = 0, toolMs = 0, otherMs = 0;
  var taskDurationMs = 0;
  nodes.forEach(function(n) {
    // Task-level events carry the total wall-clock duration, not a category
    if (n.eventType === 'task_started' || n.eventType === 'task_completed' || n.eventType === 'task_failed') {
      if (n.durationMs > taskDurationMs) taskDurationMs = n.durationMs;
      return;
    }
    // action_started events have no duration — skip
    if (n.eventType === 'action_started') return;
    var ms = n.durationMs || 0;
    if (ms === 0) return;
    if (n.kind === 'llm_call' || n.type === 'llm') {
      llmMs += ms;
    } else if (n.eventType === 'action_completed' || n.eventType === 'action_failed') {
      toolMs += ms;
    } else {
      otherMs += ms;
    }
  });
  var accountedMs = llmMs + toolMs + otherMs;
  // Use task duration as total if available (represents wall-clock time)
  var totalMs = taskDurationMs > accountedMs ? taskDurationMs : accountedMs;
  // Recompute "other" as the gap between total and categorized time (overhead, wait, etc.)
  if (taskDurationMs > accountedMs) {
    otherMs = taskDurationMs - llmMs - toolMs;
    if (otherMs < 0) otherMs = 0;
  }
  if (totalMs === 0) totalMs = 1; // prevent division by zero
  return { llmMs: llmMs, toolMs: toolMs, otherMs: otherMs, totalMs: totalMs };
}

function renderDurationBreakdown() {
  var container = document.getElementById('durationBreakdown');
  if (!container) return;
  var tl = TIMELINES[selectedTask];
  if (!tl || !tl.nodes || tl.nodes.length === 0) {
    container.style.display = 'none';
    return;
  }
  var bd = computeDurationBreakdown(tl.nodes);
  var llmPct = Math.round(bd.llmMs / bd.totalMs * 100);
  var toolPct = Math.round(bd.toolMs / bd.totalMs * 100);
  var otherPct = 100 - llmPct - toolPct;
  if (otherPct < 0) otherPct = 0;

  container.style.display = '';
  container.innerHTML = `
    <div class="duration-breakdown-header">
      <div class="duration-breakdown-label">Time Breakdown</div>
      <div class="duration-breakdown-total">Total: ${fmtDuration(bd.totalMs)}</div>
    </div>
    <div class="duration-bars">
      <div class="dur-bar-row">
        <div class="dur-bar-label">LLM</div>
        <div class="dur-bar-track"><div class="dur-bar-fill" style="width:${llmPct}%; background:var(--llm);"></div></div>
        <div class="dur-bar-value">${fmtDuration(bd.llmMs)} (${llmPct}%)</div>
      </div>
      <div class="dur-bar-row">
        <div class="dur-bar-label">Tools</div>
        <div class="dur-bar-track"><div class="dur-bar-fill" style="width:${toolPct}%; background:var(--active);"></div></div>
        <div class="dur-bar-value">${fmtDuration(bd.toolMs)} (${toolPct}%)</div>
      </div>
      <div class="dur-bar-row">
        <div class="dur-bar-label">Other</div>
        <div class="dur-bar-track"><div class="dur-bar-fill" style="width:${otherPct}%; background:var(--idle);"></div></div>
        <div class="dur-bar-value">${fmtDuration(bd.otherMs)} (${otherPct}%)</div>
      </div>
    </div>
  `;
}

// ═══════════════════════════════════════════════════
//  ★ FEATURE 4+5: ACTION TREE RENDERING
// ═══════════════════════════════════════════════════

function renderActionTreeNode(node, errorChains, depth) {
  if (!node) return '';
  var status = node.status || 'completed';
  var isDone = status === 'completed' || status === 'success';
  var isFailed = status === 'failed' || status === 'error';
  var name = escHtml(node.name || node.action_name || 'unknown');
  var dur = node.duration_ms != null ? fmtDuration(node.duration_ms) : '';
  var children = node.children || [];

  // Determine node visual type
  var nodeType = 'action';
  if (node.type === 'llm_call' || node.kind === 'llm_call') nodeType = 'llm';
  else if (isFailed) nodeType = 'error';
  else if (isDone) nodeType = 'action';
  else if (node.type === 'retry') nodeType = 'retry';
  if (depth === 0 && nodeType !== 'llm') nodeType = 'system';

  var icon = TREE_ICON[nodeType] || '•';
  var statusHtml = '';
  if (isFailed) statusHtml = '<div class="tree-status failed">✗ failed</div>';
  else if (isDone) statusHtml = '<div class="tree-status completed">✓</div>';
  else if (status === 'processing' || status === 'active') statusHtml = '<div class="tree-status processing">◉</div>';

  // Build detail line
  var detailParts = [];

  // ★ Feature 2: Token ratio for LLM nodes
  if (nodeType === 'llm' && node.tokens_in != null) {
    if (node.model) detailParts.push(`<span class="model-tag">${escHtml(node.model)}</span>`);
    detailParts.push(tokenBarHtml(node.tokens_in, node.tokens_out));
    if (node.cost != null) detailParts.push(`<span class="cost-tag">$${node.cost.toFixed(3)}</span>`);
  } else if (node.summary) {
    detailParts.push(escHtml(node.summary));
  } else if (node.tool_args) {
    detailParts.push(escHtml(typeof node.tool_args === 'string' ? node.tool_args : JSON.stringify(node.tool_args).substring(0, 80)));
  }
  var detailLine = detailParts.length > 0 ? `<div class="tree-detail-line">${detailParts.join(' ')}</div>` : '';

  // ★ Feature 5: Error chain — find matching error for this node
  var errorLine = '';
  if (isFailed) {
    var errorMsg = node.error_message || node.exception_message || null;
    var errorType = node.error_type || node.exception_type || '';
    // Also check errorChains for this action
    if (!errorMsg && errorChains) {
      var chain = errorChains.find(function(c) { return c.action_id === node.action_id || c.action_name === node.name; });
      if (chain) {
        errorMsg = chain.message || chain.error_message;
        errorType = chain.type || chain.exception_type || errorType;
      }
    }
    if (errorMsg) {
      errorLine = `<div class="tree-error-line">${errorType ? escHtml(errorType) + ': ' : ''}${escHtml(errorMsg)}</div>`;
    }
  }

  // Guide lines
  var guideHtml = '';
  for (var i = 0; i < depth; i++) guideHtml += '<div class="tree-guide"></div>';

  var rowBg = isFailed ? ' style="background: rgba(220,38,38,0.03);"' : '';
  var nameColor = isFailed ? ' style="color:var(--error)"' : '';

  // ★ Clickable tree nodes: LLM → modal, action → pinned detail
  var isLlmNode = node.type === 'llm_call' || node.kind === 'llm_call';
  var clickClass = isLlmNode ? ' llm-clickable' : ' action-clickable';
  var nodeDataAttr = ' data-node-id="' + escHtml(node.action_id || '') + '"';
  var clickHandler = isLlmNode
    ? ` onclick="openLlmDetailFromTree(this)"`
    : ` onclick="openActionDetailFromTree(this)"`;
  var treePromptDot = isLlmNode && (node.prompt_preview || node.promptPreview) ? '<span class="has-prompt-dot" title="Prompt/response available"></span>' : '';
  var expandHint = isLlmNode ? '<span class="tree-expand-hint">details</span>' : '';

  var html = `<div class="tree-node">
    <div class="tree-node-row${clickClass}"${rowBg}${nodeDataAttr}${clickHandler}>
      <div class="tree-indent">${guideHtml}</div>
      <div class="tree-icon ${nodeType}">${icon}</div>
      <div class="tree-content">
        <div class="tree-label"><span class="tree-label-name"${nameColor}>${name}</span>${treePromptDot}${dur ? `<span class="tree-dur">${dur}</span>` : ''}</div>
        ${errorLine}
        ${detailLine}
      </div>
      ${statusHtml}
      ${expandHint}
    </div>`;

  if (children.length > 0) {
    html += '<div class="tree-children">';
    children.forEach(function(child) {
      html += renderActionTreeNode(child, errorChains, depth + 1);
    });
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function renderActionTree() {
  var tl = TIMELINES[selectedTask];
  if (!tl || !tl.actionTree) return '<div class="empty-state"><span class="empty-state-icon">🌳</span>No action tree data — showing flat view</div>';

  var tree = tl.actionTree;
  var errorChains = tl.errorChains || [];

  // actionTree could be a single root or an array of roots
  var roots = Array.isArray(tree) ? tree : [tree];
  var html = '';
  roots.forEach(function(root) {
    html += renderActionTreeNode(root, errorChains, 0);
  });
  return html;
}

// ═══════════════════════════════════════════════════
//  RENDERING — TIMELINE (combined tree/flat)
// ═══════════════════════════════════════════════════

function renderPlanBar() {
  const tl = TIMELINES[selectedTask];
  const planBar = document.getElementById('planBar');
  if (!tl || !tl.plan) { planBar.classList.remove('visible'); return; }

  const plan = tl.plan;
  const completed = plan.steps.filter(s => s.status === 'completed').length;
  const total = plan.steps.length;

  document.getElementById('planLabel').textContent = `Plan · ${total} steps`;
  document.getElementById('planProgress').textContent = `${completed}/${total} completed`;
  document.getElementById('planSteps').innerHTML = plan.steps.map(s =>
    `<div class="plan-step ${s.status}"><div class="plan-step-tooltip">${escHtml(s.desc)}</div></div>`
  ).join('');
  planBar.classList.add('visible');
}

function renderTimeline() {
  const treeCanvas = document.getElementById('actionTreeCanvas');
  const flatCanvas = document.getElementById('timelineCanvas');
  const tl = TIMELINES[selectedTask];
  pinnedNode = null;
  document.getElementById('pinnedDetail').classList.remove('visible');
  renderPlanBar();
  renderDurationBreakdown();

  // Update toggle state
  document.querySelectorAll('.view-toggle-btn').forEach(function(btn) {
    btn.classList.toggle('active', btn.dataset.mode === timelineViewMode);
  });

  if (timelineViewMode === 'tree') {
    // ★ Tree view
    flatCanvas.style.display = 'none';
    treeCanvas.style.display = '';
    if (!tl || !tl.actionTree) {
      // Fallback: no tree data, show flat
      treeCanvas.innerHTML = '<div class="empty-state"><span class="empty-state-icon">🌳</span>No tree data available</div>';
      return;
    }
    treeCanvas.innerHTML = renderActionTree();
  } else {
    // Flat view (original behavior)
    treeCanvas.style.display = 'none';
    flatCanvas.style.display = '';
    renderFlatTimeline();
  }
}

function renderFlatTimeline() {
  const canvas = document.getElementById('timelineCanvas');
  const tl = TIMELINES[selectedTask];
  const nodes = tl ? tl.nodes : [];

  if (nodes.length === 0) { canvas.innerHTML = '<div class="empty-state"><span class="empty-state-icon">⏳</span>No timeline data</div>'; return; }

  const mainNodes = nodes.filter(n => !n.isBranch);
  const branchNodes = nodes.filter(n => n.isBranch || n.isBranchStart);
  const hasBranch = branchNodes.length > 0;

  let html = '<div class="timeline-track">';
  mainNodes.forEach((node, i) => {
    const color = typeColor[node.type] || 'var(--idle)';
    const filled = node.type === 'success' || node.type === 'error' || node.type === 'stuck';
    const isLlm = node.type === 'llm';
    const nodeIdx = nodes.indexOf(node);

    var tlPromptDot = isLlm && node.promptPreview ? ' has-prompt' : '';
    html += `<div class="tl-node ${isLlm ? 'llm-node' : ''}${tlPromptDot}" data-idx="${nodeIdx}" onclick="pinNode(${nodeIdx})">`;
    if (isLlm && node.llmModel) html += `<div class="tl-llm-badge">${escHtml(node.llmModel)}</div>`;
    html += `<div class="tl-node-label" style="color: ${color}" title="${escHtml(node.rawLabel || node.label)}">${escHtml(node.label)}${isLlm && node.promptPreview ? '<span class="has-prompt-dot" title="Prompt/response available"></span>' : ''}</div>`;
    html += `<div class="tl-node-dot" style="border-color: ${color}; ${filled ? 'background: ' + color : ''}"></div>`;
    html += `<div class="tl-node-time">${node.time}</div></div>`;

    if (i < mainNodes.length - 1) {
      const nextNode = mainNodes[i + 1];
      const widthMul = nextNode.dur ? parseFloat(nextNode.dur) : 0.5;
      const w = Math.max(50, (isNaN(widthMul) ? 0.5 : widthMul) * 28 + 50);
      const nextColor = typeColor[nextNode.type] || 'var(--idle)';
      const isBranchConn = node.isBranchStart;
      const connW = isBranchConn ? Math.max(w, branchNodes.length * 70 + 60) : w;

      html += `<div class="tl-connector" style="width: ${connW}px; position: relative;">`;
      html += `<div class="tl-connector-line" style="background: linear-gradient(to right, ${color}, ${nextColor}); opacity: 0.4;"></div>`;
      if (nextNode.dur && nextNode.dur !== '—') html += `<div class="tl-connector-duration">${nextNode.dur}</div>`;

      if (isBranchConn && branchNodes.length > 0) {
        html += `<div style="position: absolute; top: 0; left: 0; width: 100%; pointer-events: none;">`;
        html += `<div style="position: absolute; left: 0; top: 2px; width: 2px; height: 34px; background: var(--error); opacity: 0.35;"></div>`;
        html += `<div style="position: absolute; top: 36px; left: 0; right: 0; display: flex; align-items: center; pointer-events: auto;">`;
        branchNodes.filter(n => !n.isBranchStart).forEach((bn, bi) => {
          const bColor = typeColor[bn.type] || 'var(--idle)';
          const bIdx = nodes.indexOf(bn);
          const bFilled = bn.type === 'error';
          html += `<div class="tl-node" data-idx="${bIdx}" onclick="pinNode(${bIdx})" style="pointer-events: auto;">`;
          html += `<div class="tl-node-dot" style="border-color: ${bColor}; width: 10px; height: 10px; ${bFilled ? 'background: ' + bColor : ''}"></div>`;
          html += `<div style="position: absolute; bottom: -16px; font-family: var(--font-mono); font-size: 10px; color: var(--text-muted); white-space: nowrap;">${escHtml(bn.label)}</div></div>`;
          if (bi < branchNodes.filter(n => !n.isBranchStart).length - 1) html += `<div style="height: 2px; width: 40px; background: var(--error); opacity: 0.25;"></div>`;
        });
        html += `</div>`;
        html += `<div style="position: absolute; right: 0; top: 2px; width: 2px; height: 34px; background: var(--error); opacity: 0.35;"></div>`;
        html += `</div>`;
      }
      html += `</div>`;
    }
  });
  html += '</div>';
  if (hasBranch) html = `<div style="padding-bottom: 60px">${html}</div>`;
  canvas.innerHTML = html;
  initTimelineScrollListener();
  if (timelineAutoScroll) requestAnimationFrame(() => scrollTimelineToEnd());
}

function pinNode(idx) {
  const tl = TIMELINES[selectedTask];
  const nodes = tl ? tl.nodes : [];
  const node = nodes[idx];
  if (!node) return;

  // ★ LLM nodes get the full modal
  if (node.kind === 'llm_call' || node.type === 'llm') {
    openLlmModal({
      name: node.llmName || node.rawLabel || node.label,
      model: node.llmModel,
      tokens_in: node.tokensIn,
      tokens_out: node.tokensOut,
      cost: node.llmCost,
      duration_ms: node.durationMs || null,
      prompt_preview: node.promptPreview,
      response_preview: node.responsePreview,
      metadata: node.llmMetadata,
      agent_id: node.agentId,
      task_id: node.taskId || selectedTask,
      event_id: node.eventId,
      timestamp: node.timestamp,
    });
    return;
  }

  if (pinnedNode === idx) { unpinDetail(); return; }
  pinnedNode = idx;

  document.querySelectorAll('.tl-node').forEach(el => el.classList.remove('pinned'));
  const el = document.querySelector(`.tl-node[data-idx="${idx}"]`);
  if (el) el.classList.add('pinned');

  const color = typeColor[node.type] || 'var(--idle)';
  document.getElementById('pinnedTitle').innerHTML = `<span style="color: ${color}">●</span> ${escHtml(node.label)} <span style="color: var(--text-muted); font-weight: 400; font-size: 10px; margin-left: 8px">${node.time}</span>`;

  let bodyHtml = '<div class="detail-col">';
  Object.entries(node.detail).forEach(([k, v]) => { bodyHtml += `<div class="detail-row"><span class="detail-key">${escHtml(k)}</span><span class="detail-val">${escHtml(String(v))}</span></div>`; });
  bodyHtml += '</div>';
  if (node.dur && node.dur !== '—') bodyHtml += `<div class="detail-col"><div class="detail-row"><span class="detail-key">duration</span><span class="detail-val">${node.dur}</span></div></div>`;
  // ★ Show tokens in pinned detail
  if (node.tokensIn != null) bodyHtml += `<div class="detail-col"><div class="detail-row"><span class="detail-key">tokens</span><span class="detail-val">${fmtTokens(node.tokensIn)} in / ${fmtTokens(node.tokensOut)} out</span></div></div>`;
  if (node.llmCost != null) bodyHtml += `<div class="detail-col"><div class="detail-row"><span class="detail-key">cost</span><span class="detail-val">$${node.llmCost.toFixed(4)}</span></div></div>`;
  if (node.tags && node.tags.length) bodyHtml += '<div class="detail-col" style="flex-basis: 100%;"><div style="margin-top: 2px;">' + node.tags.map(t => `<span class="detail-payload-tag">${escHtml(t)}</span>`).join('') + '</div></div>';

  document.getElementById('pinnedBody').innerHTML = bodyHtml;
  document.getElementById('pinnedDetail').classList.add('visible');
}

function unpinDetail() {
  pinnedNode = null;
  document.querySelectorAll('.tl-node').forEach(el => el.classList.remove('pinned'));
  document.getElementById('pinnedDetail').classList.remove('visible');
}

// ★ Toggle tree/flat
function toggleTimelineView(mode) {
  timelineViewMode = mode;
  renderTimeline();
}

// Timeline auto-scroll helpers
function scrollTimelineToEnd() {
  const canvas = document.getElementById('timelineCanvas');
  if (canvas) canvas.scrollTo({ left: canvas.scrollWidth, behavior: 'smooth' });
}

function initTimelineScrollListener() {
  const canvas = document.getElementById('timelineCanvas');
  if (!canvas || canvas._scrollListenerAttached) return;
  canvas.addEventListener('scroll', function() {
    const threshold = 20;
    const atEnd = canvas.scrollWidth - canvas.scrollLeft - canvas.clientWidth <= threshold;
    timelineAutoScroll = atEnd;
  });
  canvas._scrollListenerAttached = true;
}

// ═══════════════════════════════════════════════════
//  RENDERING — TASKS TABLE
// ═══════════════════════════════════════════════════

function renderTasks() {
  const body = document.getElementById('tasksBody');
  let tasks = TASKS;
  if (selectedAgent) tasks = tasks.filter(t => t.agent === selectedAgent);

  if (tasks.length === 0) { body.innerHTML = `<tr><td colspan="8"><div class="empty-state"><span class="empty-state-icon">📋</span>No tasks</div></td></tr>`; return; }

  body.innerHTML = tasks.map(t => `
    <tr class="${t.id === selectedTask ? 'selected-row' : ''}" onclick="selectTask('${t.id}')">
      <td><span class="clickable-entity" style="color: var(--accent); font-weight: 500;">${escHtml(t.id)}</span></td>
      <td><span class="clickable-entity" onclick="event.stopPropagation(); selectAgent('${t.agent}')">${escHtml(t.agent)}</span></td>
      <td>${escHtml(t.type)}</td>
      <td><span class="task-status-dot" style="background: ${statusColor[t.status] || 'var(--idle)'}"></span>${t.status}</td>
      <td class="task-duration">${t.duration}</td>
      <td>${t.llmCalls > 0 ? `<span class="task-llm-badge">◆ ${t.llmCalls}</span>` : '—'}</td>
      <td>${t.cost}</td>
      <td style="color: var(--text-muted)">${t.time}</td>
    </tr>
  `).join('');
}

// ═══════════════════════════════════════════════════
//  ★ FEATURE 7: RENDERING — STREAM (rich cards)
// ═══════════════════════════════════════════════════

function renderStreamFilters() {
  document.getElementById('streamFilters').innerHTML = STREAM_FILTERS.map(f =>
    `<div class="filter-chip ${f === activeStreamFilter ? 'active' : ''}" onclick="setStreamFilter('${f}')">${f}</div>`
  ).join('');
}

function getFilteredStream() {
  let events = STREAM_EVENTS;
  if (selectedAgent) events = events.filter(e => e.agent === selectedAgent);
  if (activeStreamFilter !== 'all') {
    events = events.filter(e => {
      if (activeStreamFilter === 'task') return e.type.startsWith('task_');
      if (activeStreamFilter === 'action') return e.type.startsWith('action_');
      if (activeStreamFilter === 'error') return e.severity === 'error';
      if (activeStreamFilter === 'llm') return e.kind === 'llm_call';
      if (activeStreamFilter === 'pipeline') return e.kind && ['queue_snapshot', 'todo', 'issue', 'scheduled'].includes(e.kind);
      if (activeStreamFilter === 'human') return e.type.startsWith('approval');
      return true;
    });
  }
  return events;
}

function buildStreamDetailTags(e) {
  var tags = [];
  // LLM events
  if (e.kind === 'llm_call' || e.type === 'llm_call') {
    if (e.model) tags.push(`<span class="stream-detail-tag llm">${escHtml(e.model)}</span>`);
    if (e.tokensIn != null || e.tokensOut != null) tags.push(`<span class="stream-detail-tag tokens">${fmtTokens(e.tokensIn)} in → ${fmtTokens(e.tokensOut)} out</span>`);
    if (e.cost != null) tags.push(`<span class="stream-detail-tag cost">$${e.cost.toFixed(3)}</span>`);
    if (e.durationMs != null) tags.push(`<span class="stream-detail-tag duration">${fmtDuration(e.durationMs)}</span>`);
    var streamPromptDot = (e.promptPreview || e.responsePreview) ? '<span class="has-prompt-dot" title="Prompt/response available"></span>' : '';
    tags.push(`<span class="stream-detail-btn" onclick="event.stopPropagation(); openLlmDetailFromStream('${escHtml(e.eventId)}')">${streamPromptDot}&#x2922; Details</span>`);
  }
  // Task events
  else if (e.type.startsWith('task_')) {
    if (e.durationMs != null) tags.push(`<span class="stream-detail-tag duration">${fmtDuration(e.durationMs)}</span>`);
    if (e.cost != null) tags.push(`<span class="stream-detail-tag cost">$${e.cost.toFixed(2)} total</span>`);
  }
  // Action failures
  else if (e.type === 'action_failed' || (e.severity === 'error' && e.errorMessage)) {
    if (e.errorMessage) tags.push(`<span class="stream-detail-tag error">${escHtml(e.errorType ? e.errorType + ': ' : '')}${escHtml(e.errorMessage)}</span>`);
    if (e.durationMs != null) tags.push(`<span class="stream-detail-tag duration">${fmtDuration(e.durationMs)}</span>`);
  }
  // Issues
  else if (e.kind === 'issue') {
    if (e.severity) tags.push(`<span class="stream-detail-tag severity ${e.severity === 'error' || e.severity === 'high' ? 'high' : ''}">${escHtml(e.severity)}</span>`);
    if (e.category) tags.push(`<span class="stream-detail-tag">category: ${escHtml(e.category)}</span>`);
    if (e.occurrences) tags.push(`<span class="stream-detail-tag">×${e.occurrences} occurrences</span>`);
  }
  // Approvals
  else if (e.type.startsWith('approval')) {
    if (e.approver) tags.push(`<span class="stream-detail-tag">approver: ${escHtml(e.approver)}</span>`);
  }
  // Queue snapshots
  else if (e.kind === 'queue_snapshot') {
    if (e.queueDepth != null) tags.push(`<span class="stream-detail-tag">depth: ${e.queueDepth}</span>`);
    if (e.oldestAge) tags.push(`<span class="stream-detail-tag" style="color:var(--warning)">oldest: ${escHtml(e.oldestAge)}</span>`);
  }
  // Action completed
  else if (e.type === 'action_completed') {
    if (e.durationMs != null) tags.push(`<span class="stream-detail-tag duration">${fmtDuration(e.durationMs)}</span>`);
  }
  return tags.join('');
}

function renderNarrativeLog() {
  var list = document.getElementById('narrativeList');
  if (!list) return;
  var filtered = getFilteredStream();

  if (filtered.length === 0) {
    list.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted); font-size: 12px;">No events</div>';
    return;
  }

  list.innerHTML = filtered.map(function(e) {
    return '<div class="narrative-event">' +
      '<span class="narrative-sentence">' + formatEventSentence(e) + '</span>' +
      '<span class="narrative-time">' + e.time + '</span>' +
    '</div>';
  }).join('');
}

function renderStream() {
  const list = document.getElementById('streamList');
  const filtered = getFilteredStream();
  document.getElementById('eventCount').textContent = filtered.length + ' event' + (filtered.length !== 1 ? 's' : '');

  if (filtered.length === 0) { list.innerHTML = '<div class="empty-state" style="padding-top: 40px;"><span class="empty-state-icon">📡</span>No events match filters</div>'; renderNarrativeLog(); return; }

  list.innerHTML = filtered.map(e => {
    const kindIcon = e.kind ? (KIND_ICON[e.kind] || '') : '';
    const sevColor = SEVERITY_COLOR[e.severity] || 'var(--idle)';
    const kindColor = e.kind === 'llm_call' ? 'var(--llm)' : e.kind === 'issue' ? 'var(--error)' : sevColor;
    const detailTags = buildStreamDetailTags(e);

    return `<div class="stream-event">
      <div class="stream-event-top">
        <div class="stream-event-type" style="color: ${kindColor}">
          <div class="event-type-dot" style="background: ${kindColor}"></div>
          ${kindIcon ? `<span class="event-kind-icon">${kindIcon}</span>` : ''}${e.kind || e.type}
        </div>
        <div class="stream-event-time">${e.time}</div>
      </div>
      <div class="stream-event-body">
        <div class="stream-event-agent">
          <span class="clickable-entity" onclick="selectAgent('${e.agent}')">${escHtml(e.agent)}</span>${e.task ? ` › <span class="clickable-entity" onclick="selectTask('${e.task}')">${escHtml(e.task)}</span>` : ''}
        </div>
        ${escHtml(e.summary)}
      </div>
      ${detailTags ? `<div class="stream-event-detail">${detailTags}</div>` : ''}
    </div>`;
  }).join('');

  renderNarrativeLog();
}

// ═══════════════════════════════════════════════════
//  RENDERING — AGENT DETAIL
// ═══════════════════════════════════════════════════

function renderAgentDetail() {
  if (!agentDetailAgent) return;
  const agent = AGENTS.find(a => a.id === agentDetailAgent);
  if (!agent) return;

  document.getElementById('detailAgentName').textContent = agent.id;
  document.getElementById('detailAgentBadge').innerHTML = `<div class="agent-status-badge ${statusBadge[agent.status] || 'badge-idle'}">${statusLabel[agent.status] || agent.status}</div>`;

  // ★ Feature 3: Show metadata in agent detail header
  var metaHtml = '';
  var metaParts = [];
  if (agent.framework) metaParts.push(agent.framework);
  if (agent.runtime) metaParts.push(agent.runtime);
  if (agent.sdkVersion) metaParts.push('sdk ' + agent.sdkVersion);
  if (agent.environment) metaParts.push('env: ' + agent.environment);
  if (agent.group) metaParts.push('group: ' + agent.group);
  if (metaParts.length > 0) {
    metaHtml = '<div class="agent-detail-meta">' + metaParts.map(function(p) { return '<span class="meta-tag">' + escHtml(p) + '</span>'; }).join('') + '</div>';
  }
  var metaContainer = document.getElementById('detailAgentMeta');
  if (metaContainer) metaContainer.innerHTML = metaHtml;

  // Tasks tab
  const agentTasks = TASKS.filter(t => t.agent === agent.id);
  let tasksHtml = `<table class="pipeline-table"><thead><tr><th>Task ID</th><th>Type</th><th>Status</th><th>Duration</th><th>LLM</th><th>Cost</th><th>Time</th></tr></thead><tbody>`;
  agentTasks.forEach(t => {
    tasksHtml += `<tr onclick="selectTask('${t.id}')" style="cursor:pointer;">
      <td style="color: var(--accent);">${escHtml(t.id)}</td><td>${escHtml(t.type)}</td>
      <td><span class="task-status-dot" style="background: ${statusColor[t.status] || 'var(--idle)'}"></span>${t.status}</td>
      <td>${t.duration}</td><td>${t.llmCalls > 0 ? `<span class="task-llm-badge">◆ ${t.llmCalls}</span>` : '—'}</td>
      <td>${t.cost}</td><td style="color: var(--text-muted)">${t.time}</td></tr>`;
  });
  tasksHtml += '</tbody></table>';
  document.getElementById('detailTabTasks').innerHTML = agentTasks.length > 0 ? tasksHtml : '<div class="pipeline-empty">No tasks for this agent</div>';

  // Pipeline tab
  const pl = PIPELINE[agent.id] || {};
  let pHtml = '';

  const issues = pl.issues || [];
  if (issues.length > 0) {
    pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Active Issues</div><div class="pipeline-badge" style="color: var(--error);">${issues.length}</div></div>`;
    pHtml += `<table class="pipeline-table"><thead><tr><th>Issue</th><th>Severity</th><th>Category</th><th>Occurrences</th></tr></thead><tbody>`;
    issues.forEach(iss => { pHtml += `<tr><td>${escHtml(iss.summary)}</td><td><span class="severity-badge severity-${iss.severity || 'medium'}">${iss.severity || '—'}</span></td><td>${escHtml(iss.category || '—')}</td><td>×${iss.occurrence_count || iss.occurrences || 1}</td></tr>`; });
    pHtml += `</tbody></table></div>`;
  }

  const queue = pl.queue || {};
  const queueItems = queue.items || [];
  const queueDepth = queue.depth || queueItems.length;
  pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Queue</div><div class="pipeline-badge">${queueDepth} items</div></div>`;
  if (queueItems.length > 0) {
    pHtml += `<table class="pipeline-table"><thead><tr><th>ID</th><th>Priority</th><th>Source</th><th>Summary</th></tr></thead><tbody>`;
    queueItems.forEach(q => { pHtml += `<tr><td style="color: var(--text-muted)">${escHtml(q.id)}</td><td><span class="priority-badge priority-${q.priority || 'normal'}">${q.priority || 'normal'}</span></td><td>${escHtml(q.source || '')}</td><td>${escHtml(q.summary || '')}</td></tr>`; });
    pHtml += `</tbody></table>`;
  } else { pHtml += `<div class="pipeline-empty">Queue is empty — agent is caught up</div>`; }
  pHtml += `</div>`;

  const todos = pl.todos || [];
  if (todos.length > 0) {
    pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Active TODOs</div><div class="pipeline-badge">${todos.length}</div></div>`;
    pHtml += `<table class="pipeline-table"><thead><tr><th>TODO</th><th>Priority</th><th>Source</th></tr></thead><tbody>`;
    todos.forEach(td => { pHtml += `<tr><td>${escHtml(td.summary)}</td><td><span class="priority-badge priority-${td.priority || 'normal'}">${td.priority || 'normal'}</span></td><td>${escHtml(td.source || '')}</td></tr>`; });
    pHtml += `</tbody></table></div>`;
  }

  const scheduled = pl.scheduled || [];
  if (scheduled.length > 0) {
    pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Scheduled</div><div class="pipeline-badge">${scheduled.length}</div></div>`;
    pHtml += `<table class="pipeline-table"><thead><tr><th>Name</th><th>Next Run</th><th>Interval</th><th>Status</th></tr></thead><tbody>`;
    scheduled.forEach(s => {
      const st = s.last_status || s.status || '—';
      const stColor = (st === 'ok' || st === 'success') ? 'var(--success)' : 'var(--warning)';
      pHtml += `<tr><td>${escHtml(s.name)}</td><td>${s.next_run || s.next || '—'}</td><td>${s.interval || '—'}</td><td style="color: ${stColor}">${st}</td></tr>`;
    });
    pHtml += `</tbody></table></div>`;
  }

  if (!pHtml) pHtml = '<div class="pipeline-empty">No pipeline data for this agent</div>';
  document.getElementById('detailTabPipeline').innerHTML = pHtml;
}

// ═══════════════════════════════════════════════════
//  RENDERING — COST EXPLORER
// ═══════════════════════════════════════════════════

function renderCostExplorer() {
  if (!COST_DATA) {
    document.getElementById('costRibbon').innerHTML = '';
    document.getElementById('costTables').innerHTML = '<div class="empty-state">Loading cost data…</div>';
    return;
  }
  const d = COST_DATA;
  const totalCost = d.total_cost || 0;
  const totalCalls = d.call_count || 0;
  const totalIn = d.total_tokens_in || 0;
  const totalOut = d.total_tokens_out || 0;
  const avgCost = totalCalls > 0 ? totalCost / totalCalls : 0;
  const reportedCost = d.reported_cost || 0;
  const estimatedCost = d.estimated_cost || 0;
  const hasEstimates = estimatedCost > 0;

  const rangeLabels = { '1h': 'Last 1 Hour', '6h': 'Last 6 Hours', '24h': 'Last 24 Hours', '7d': 'Last 7 Days', '30d': 'Last 30 Days' };
  const ranges = ['1h', '6h', '24h', '7d', '30d'];
  const rangeBtns = ranges.map(r =>
    `<button class="view-toggle-btn${r === currentCostRange ? ' active' : ''}" onclick="setCostRange('${r}')">${r}</button>`
  ).join('');

  document.getElementById('costRibbon').innerHTML = `
    <div class="cost-range-bar">
      <span class="cost-range-label">${rangeLabels[currentCostRange] || currentCostRange}</span>
      <div class="view-toggle">${rangeBtns}</div>
    </div>
    <div class="cost-stat"><div class="stat-label">Total Cost</div><div class="stat-value purple">${hasEstimates ? '~' : ''}$${totalCost.toFixed(2)}</div></div>
    <div class="cost-stat"><div class="stat-label">LLM Calls</div><div class="stat-value">${totalCalls}</div></div>
    <div class="cost-stat"><div class="stat-label">Tokens In</div><div class="stat-value">${fmtTokens(totalIn)}</div></div>
    <div class="cost-stat"><div class="stat-label">Tokens Out</div><div class="stat-value">${fmtTokens(totalOut)}</div></div>
    <div class="cost-stat"><div class="stat-label">Avg Cost/Call</div><div class="stat-value">$${avgCost.toFixed(3)}</div></div>
    ${hasEstimates ? `<div class="cost-stat"><div class="stat-label">Reported</div><div class="stat-value">$${reportedCost.toFixed(2)}</div></div>
    <div class="cost-stat"><div class="stat-label">Estimated</div><div class="stat-value" title="Server-estimated costs may differ from actual billing">~$${estimatedCost.toFixed(2)}</div></div>` : ''}
  `;

  let html = '';
  const byModel = d.by_model || [];
  if (byModel.length > 0) {
    const maxModelCost = Math.max(...byModel.map(m => m.cost || 0), 0.01);
    html += `<div><div class="cost-section-title">Cost by Model</div><table class="cost-table"><thead><tr><th>Model</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost</th><th></th></tr></thead><tbody>`;
    byModel.forEach(m => {
      const mEst = (m.estimated_cost || 0) > 0;
      const mCostStr = mEst ? '~$' + (m.cost || 0).toFixed(2) : '$' + (m.cost || 0).toFixed(2);
      const mTitle = mEst ? 'title="Includes server-estimated costs"' : '';
      const modelName = m.model || '—';
      const isExpanded = costExpandedModel === modelName;
      const chevron = isExpanded ? '▾' : '▸';
      html += `<tr class="cost-row clickable ${isExpanded ? 'cost-row-expanded' : ''}" onclick="toggleCostModelDrilldown('${escHtml(modelName)}')" title="Click to view individual calls"><td><span class="cost-row-chevron">${chevron}</span><span class="model-badge">${escHtml(modelName)}</span></td><td>${m.call_count || 0}</td><td>${fmtTokens(m.tokens_in)}</td><td>${fmtTokens(m.tokens_out)}</td><td style="color: var(--llm); font-weight: 600;" ${mTitle}>${mCostStr}</td><td style="width: 100px;"><div class="cost-bar" style="width: ${((m.cost || 0) / maxModelCost * 100)}%"></div></td></tr>`;
      if (isExpanded) {
        html += `<tr class="cost-drilldown-row"><td colspan="6">${renderCostDrilldownPanel('model', modelName)}</td></tr>`;
      }
    });
    html += `</tbody></table></div>`;
  }
  const byAgent = d.by_agent || [];
  if (byAgent.length > 0) {
    const sortedAgents = [...byAgent].sort((a, b) => (b.cost || 0) - (a.cost || 0));
    const maxAgentCost = Math.max(...sortedAgents.map(a => a.cost || 0), 0.01);
    html += `<div><div class="cost-section-title">Cost by Agent</div><table class="cost-table"><thead><tr><th>Agent</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost</th><th></th></tr></thead><tbody>`;
    sortedAgents.forEach(a => {
      const agentId = a.agent_id || a.agent || '—';
      const aEst = (a.estimated_cost || 0) > 0;
      const aCostStr = aEst ? '~$' + (a.cost || 0).toFixed(2) : '$' + (a.cost || 0).toFixed(2);
      const aTitle = aEst ? 'title="Includes server-estimated costs"' : '';
      const isExpanded = costExpandedAgent === agentId;
      const chevron = isExpanded ? '▾' : '▸';
      html += `<tr class="cost-row clickable ${isExpanded ? 'cost-row-expanded' : ''}" onclick="toggleCostAgentDrilldown('${escHtml(agentId)}')" title="Click to view individual calls"><td><span class="cost-row-chevron">${chevron}</span><span style="color: var(--accent);">${escHtml(agentId)}</span></td><td>${a.call_count || 0}</td><td>${fmtTokens(a.tokens_in)}</td><td>${fmtTokens(a.tokens_out)}</td><td style="color: var(--llm); font-weight: 600;" ${aTitle}>${aCostStr}</td><td style="width: 100px;"><div class="cost-bar" style="width: ${((a.cost || 0) / maxAgentCost * 100)}%"></div></td></tr>`;
      if (isExpanded) {
        html += `<tr class="cost-drilldown-row"><td colspan="6">${renderCostDrilldownPanel('agent', agentId)}</td></tr>`;
      }
    });
    html += `</tbody></table></div>`;
  }
  document.getElementById('costTables').innerHTML = html || '<div class="empty-state">No cost data available</div>';
}

function setCostRange(range) {
  currentCostRange = range;
  costExpandedAgent = null;
  costExpandedModel = null;
  costDrilldownData = [];
  costDrilldownCursor = null;
  costDrilldownLoading = false;
  fetchCostData().then(renderCostExplorer);
}

// ── Cost Drilldown Functions ──

async function toggleCostAgentDrilldown(agentId) {
  if (costExpandedAgent === agentId) {
    costExpandedAgent = null;
    costDrilldownData = [];
    costDrilldownCursor = null;
    renderCostExplorer();
    return;
  }
  costExpandedAgent = agentId;
  costExpandedModel = null;
  costDrilldownData = [];
  costDrilldownCursor = null;
  costDrilldownLoading = true;
  renderCostExplorer();
  var rangeMs = { '1h': 3600000, '6h': 21600000, '24h': 86400000, '7d': 604800000, '30d': 2592000000 };
  var since = new Date(Date.now() - (rangeMs[currentCostRange] || 86400000)).toISOString();
  var resp = await apiFetch('/v1/llm-calls', { agent_id: agentId, since: since, limit: 10 });
  costDrilldownLoading = false;
  if (resp && resp.data) {
    costDrilldownData = resp.data;
    costDrilldownCursor = resp.pagination && resp.pagination.has_more ? resp.pagination.cursor : null;
  }
  renderCostExplorer();
}

async function toggleCostModelDrilldown(modelName) {
  if (costExpandedModel === modelName) {
    costExpandedModel = null;
    costDrilldownData = [];
    costDrilldownCursor = null;
    renderCostExplorer();
    return;
  }
  costExpandedModel = modelName;
  costExpandedAgent = null;
  costDrilldownData = [];
  costDrilldownCursor = null;
  costDrilldownLoading = true;
  renderCostExplorer();
  var rangeMs = { '1h': 3600000, '6h': 21600000, '24h': 86400000, '7d': 604800000, '30d': 2592000000 };
  var since = new Date(Date.now() - (rangeMs[currentCostRange] || 86400000)).toISOString();
  var resp = await apiFetch('/v1/llm-calls', { model: modelName, since: since, limit: 10 });
  costDrilldownLoading = false;
  if (resp && resp.data) {
    costDrilldownData = resp.data;
    costDrilldownCursor = resp.pagination && resp.pagination.has_more ? resp.pagination.cursor : null;
  }
  renderCostExplorer();
}

async function loadMoreCostDrilldown() {
  if (!costDrilldownCursor || costDrilldownLoading) return;
  costDrilldownLoading = true;
  renderCostExplorer();
  var rangeMs = { '1h': 3600000, '6h': 21600000, '24h': 86400000, '7d': 604800000, '30d': 2592000000 };
  var since = new Date(Date.now() - (rangeMs[currentCostRange] || 86400000)).toISOString();
  var params = { since: since, limit: 10, cursor: costDrilldownCursor };
  if (costExpandedAgent) params.agent_id = costExpandedAgent;
  if (costExpandedModel) params.model = costExpandedModel;
  var resp = await apiFetch('/v1/llm-calls', params);
  costDrilldownLoading = false;
  if (resp && resp.data) {
    costDrilldownData = costDrilldownData.concat(resp.data);
    costDrilldownCursor = resp.pagination && resp.pagination.has_more ? resp.pagination.cursor : null;
  }
  renderCostExplorer();
}

function renderCostDrilldownPanel(filterType, filterValue) {
  if (costDrilldownLoading && costDrilldownData.length === 0) {
    return '<div class="cost-drilldown"><div class="cost-drilldown-loading">Loading calls…</div></div>';
  }
  if (costDrilldownData.length === 0) {
    var rangeLabels = { '1h': 'last hour', '6h': 'last 6 hours', '24h': 'last 24 hours', '7d': 'last 7 days', '30d': 'last 30 days' };
    return '<div class="cost-drilldown"><div class="cost-drilldown-empty">No individual calls found in the ' + (rangeLabels[currentCostRange] || currentCostRange) + '</div></div>';
  }
  var label = filterType === 'agent' ? 'Agent: ' + escHtml(filterValue) : 'Model: ' + escHtml(filterValue);
  var totalCost = costDrilldownData.reduce(function(s, c) { return s + (c.cost || 0); }, 0);
  var headerHtml = '<div class="cost-drilldown-header"><span>' + label + ' — ' + costDrilldownData.length + ' calls</span><span style="color:var(--llm);font-weight:600;">$' + totalCost.toFixed(4) + '</span></div>';
  var tableHtml = '<table class="cost-drilldown-table"><thead><tr><th>Time</th><th>Name</th>' +
    (filterType === 'model' ? '<th>Agent</th>' : '<th>Model</th>') +
    '<th>Tokens</th><th>Cost</th><th></th></tr></thead><tbody>';
  costDrilldownData.forEach(function(call, idx) {
    var ts = call.timestamp ? new Date(call.timestamp).toLocaleTimeString() : '—';
    var name = escHtml(call.name || '—');
    var secondCol = filterType === 'model' ? escHtml(call.agent_id || '—') : '<span class="model-badge">' + escHtml(call.model || '—') + '</span>';
    var tokens = (call.tokens_in != null ? call.tokens_in.toLocaleString() : '?') + ' / ' + (call.tokens_out != null ? call.tokens_out.toLocaleString() : '?');
    var costStr = call.cost != null ? '$' + call.cost.toFixed(4) : '—';
    var hasPrompt = call.prompt_preview || call.response_preview;
    var promptDot = hasPrompt ? '<span class="has-prompt-dot" title="Prompt/response available"></span>' : '';
    tableHtml += '<tr onclick="openLlmModalFromCostDrilldown(' + idx + ')" style="cursor:pointer;" title="View full LLM call detail">' +
      '<td>' + ts + '</td><td>' + name + promptDot + '</td><td>' + secondCol + '</td><td>' + tokens + '</td>' +
      '<td style="color:var(--llm);font-weight:600;">' + costStr + '</td>' +
      '<td style="text-align:center;font-size:14px;color:var(--text-muted);">⤢</td></tr>';
  });
  tableHtml += '</tbody></table>';
  var moreHtml = '';
  if (costDrilldownLoading) {
    moreHtml = '<div class="cost-drilldown-loading">Loading more…</div>';
  } else if (costDrilldownCursor) {
    moreHtml = '<button class="cost-drilldown-more" onclick="event.stopPropagation(); loadMoreCostDrilldown();">Load more</button>';
  }
  return '<div class="cost-drilldown">' + headerHtml + tableHtml + moreHtml + '</div>';
}

function openLlmModalFromCostDrilldown(idx) {
  event.stopPropagation();
  var call = costDrilldownData[idx];
  if (!call) return;
  openLlmModal(call);
}

// ═══════════════════════════════════════════════════
//  ★ FEATURE 6: FLEET PIPELINE VIEW
// ═══════════════════════════════════════════════════

function renderFleetPipeline() {
  var container = document.getElementById('fleetPipelineContent');
  if (!container) return;
  if (!FLEET_PIPELINE) {
    container.innerHTML = '<div class="empty-state">Loading fleet pipeline…</div>';
    return;
  }
  var fp = FLEET_PIPELINE;
  var totals = fp.totals || {};

  var html = `<div class="fleet-pipeline-totals">
    <div class="summary-stat"><div class="stat-label">Total Queue</div><div class="stat-value blue">${totals.total_queue_depth || 0}</div></div>
    <div class="summary-stat"><div class="stat-label">Active Issues</div><div class="stat-value red">${totals.total_active_issues || 0}</div></div>
    <div class="summary-stat"><div class="stat-label">Pending TODOs</div><div class="stat-value amber">${totals.total_todos || 0}</div></div>
    <div class="summary-stat"><div class="stat-label">Scheduled</div><div class="stat-value">${totals.total_scheduled || 0}</div></div>
  </div>`;

  var agents = fp.agents || [];
  if (agents.length > 0) {
    html += `<table class="pipeline-table fleet-pipeline-table"><thead><tr><th>Agent</th><th>Queue</th><th>Issues</th><th>TODOs</th><th>Oldest Item</th><th>Status</th></tr></thead><tbody>`;
    agents.forEach(function(a) {
      var agentId = a.agent_id || '—';
      var qd = a.queue_depth || 0;
      var iss = a.active_issues || 0;
      var td = a.todos || 0;
      var oldest = a.oldest_item_age || '—';
      var isHigh = qd > 5 || iss > 0;
      html += `<tr class="${isHigh ? 'fleet-row-attention' : ''}" onclick="openAgentDetail('${agentId}')" style="cursor:pointer;">
        <td><span class="clickable-entity" style="color:var(--accent)">${escHtml(agentId)}</span></td>
        <td><span class="queue-badge ${qd > 5 ? 'high' : ''}">${qd}</span></td>
        <td>${iss > 0 ? `<span class="issue-indicator"><span class="issue-dot"></span>${iss}</span>` : '<span style="color:var(--text-muted)">0</span>'}</td>
        <td>${td > 0 ? td : '<span style="color:var(--text-muted)">0</span>'}</td>
        <td style="color:${oldest !== '—' ? 'var(--warning)' : 'var(--text-muted)'}">${escHtml(oldest)}</td>
        <td>${isHigh ? '<span style="color:var(--warning);font-weight:600;">Needs attention</span>' : '<span style="color:var(--success)">OK</span>'}</td>
      </tr>`;
    });
    html += '</tbody></table>';
  } else {
    html += '<div class="pipeline-empty">No agents reporting pipeline data</div>';
  }

  container.innerHTML = html;
}

// ═══════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════

function switchView(view) {
  currentView = view;
  document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.center-view').forEach(v => v.classList.remove('active'));

  if (view === 'mission') {
    document.querySelector('.view-tab[data-view="mission"]').classList.add('active');
    document.getElementById('viewMission').classList.add('active');
  } else if (view === 'cost') {
    costExpandedAgent = null;
    costExpandedModel = null;
    costDrilldownData = [];
    costDrilldownCursor = null;
    costDrilldownLoading = false;
    document.querySelector('.view-tab[data-view="cost"]').classList.add('active');
    document.getElementById('viewCost').classList.add('active');
    fetchCostData().then(renderCostExplorer);
  } else if (view === 'pipeline') {
    // ★ Feature 6
    document.querySelector('.view-tab[data-view="pipeline"]').classList.add('active');
    document.getElementById('viewPipeline').classList.add('active');
    fetchFleetPipeline().then(renderFleetPipeline);
  } else if (view === 'agentDetail') {
    document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
    document.getElementById('viewAgentDetail').classList.add('active');
    renderAgentDetail();
  }
  renderHive();
}

async function openAgentDetail(agentId) {
  agentDetailAgent = agentId;
  selectedAgent = agentId;
  activeDetailTab = 'tasks';
  await fetchPipelineData(agentId);
  switchView('agentDetail');
  switchDetailTab('tasks');
  renderStream();
  updateFilterBar();
}

function closeAgentDetail() {
  agentDetailAgent = null;
  selectedAgent = null;
  switchView('mission');
  renderTasks();
  renderStream();
  updateFilterBar();
}

function switchDetailTab(tab) {
  activeDetailTab = tab;
  document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.detail-tab-content').forEach(c => c.classList.remove('active'));
  const tabs = document.querySelectorAll('.detail-tab');
  const contents = document.querySelectorAll('.detail-tab-content');
  if (tab === 'tasks') { tabs[0].classList.add('active'); contents[0].classList.add('active'); }
  else if (tab === 'pipeline') { tabs[1].classList.add('active'); contents[1].classList.add('active'); }
}

// ═══════════════════════════════════════════════════
//  INTERACTIONS
// ═══════════════════════════════════════════════════

function selectAgent(agentId) {
  if (currentView === 'agentDetail') {
    if (agentDetailAgent === agentId) { closeAgentDetail(); return; }
    openAgentDetail(agentId);
    return;
  }

  if (selectedAgent === agentId) { selectedAgent = null; }
  else {
    selectedAgent = agentId;
    statusFilter = null;
    const agent = AGENTS.find(a => a.id === agentId);
    if (agent && agent.task) {
      selectedTask = agent.task;
      timelineAutoScroll = true;
      updateTimelineHeader();
      fetchTimeline(selectedTask).then(renderTimeline);
    }
  }
  updateFilterBar();
  renderHive();
  renderSummary();
  renderTasks();
  renderStream();
}

async function selectTask(taskId) {
  if (currentView === 'agentDetail') switchView('mission');
  selectedTask = taskId;
  timelineAutoScroll = true;
  updateTimelineHeader();
  renderTasks();
  await fetchTimeline(taskId);
  renderTimeline();
}

function updateTimelineHeader() {
  const task = TASKS.find(t => t.id === selectedTask);
  if (task) {
    document.getElementById('tlTaskId').textContent = selectedTask;
    const statusChar = task.status === 'completed' ? '✓' : task.status === 'failed' ? '✗' : task.status === 'stuck' ? '⚠' : '◉';
    const statusClr = statusColor[task.status] || 'var(--text-muted)';
    document.getElementById('tlMeta').innerHTML = `
      <span>⏱ ${task.duration}</span>
      <span class="clickable-entity" onclick="selectAgent('${task.agent}')">🤖 ${escHtml(task.agent)}</span>
      <span style="color: ${statusClr};">${statusChar} ${task.status}</span>
      ${task.llmCalls > 0 ? `<span style="color: var(--llm);">◆ ${task.llmCalls} LLM</span>` : ''}
    `;
  }
}

function toggleStatusFilter(status) {
  if (statusFilter === status) statusFilter = null;
  else { statusFilter = status; selectedAgent = null; }
  updateFilterBar();
  renderHive();
  renderSummary();
  renderTasks();
  renderStream();
}

function clearGlobalFilter() {
  selectedAgent = null;
  statusFilter = null;
  if (currentView === 'agentDetail') closeAgentDetail();
  updateFilterBar();
  renderHive();
  renderSummary();
  renderTasks();
  renderStream();
}

function setStreamFilter(f) { activeStreamFilter = f; renderStreamFilters(); renderStream(); }

function copyPermalink() {
  const btn = document.querySelector('.permalink-btn');
  btn.textContent = '✓ Copied!';
  if (selectedTask) {
    const url = window.location.origin + window.location.pathname + '?task=' + encodeURIComponent(selectedTask);
    navigator.clipboard.writeText(url).catch(function() {});
  }
  setTimeout(() => { btn.textContent = '⧉ Permalink'; }, 1500);
}

function updateFilterBar() {
  const bar = document.getElementById('filterBar');
  const layout = document.getElementById('mainLayout');
  const hasAgentFilter = selectedAgent !== null;
  const hasStatusFilter = statusFilter !== null;
  const hasAnyFilter = hasAgentFilter || hasStatusFilter;

  if (hasAnyFilter) {
    let parts = [];
    if (hasAgentFilter) parts.push('agent = ' + selectedAgent);
    if (hasStatusFilter) parts.push('status = ' + statusFilter);
    document.getElementById('filterPillText').textContent = '⬡ Filtering: ' + parts.join('  ·  ');
    bar.classList.add('visible');
    layout.classList.add('has-filter');
  } else {
    bar.classList.remove('visible');
    layout.classList.remove('has-filter');
  }
}

function onEnvChange() { initialLoad(); }

// ★ Update fleet pipeline badge count
function updatePipelineBadge() {
  var badge = document.getElementById('pipelineBadge');
  if (!badge) return;
  var count = 0;
  AGENTS.forEach(function(a) { count += a.activeIssues + (a.queueDepth > 5 ? 1 : 0); });
  if (count > 0) { badge.textContent = count; badge.style.display = ''; }
  else { badge.style.display = 'none'; }
}

// ═══════════════════════════════════════════════════
//  LLM DETAIL MODAL
// ═══════════════════════════════════════════════════

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

function renderLlmModal() {
  var d = llmModalData;
  if (!d) return;
  var overlay = document.getElementById('llmModalOverlay');
  var modal = document.getElementById('llmModalContent');
  if (!overlay || !modal) return;

  // Stats row
  var statsHtml = '<div class="llm-modal-stats">' +
    '<div class="llm-modal-stat"><div class="stat-label">Tokens In</div><div class="stat-value">' + (d.tokens_in != null ? d.tokens_in.toLocaleString() : '\u2014') + '</div></div>' +
    '<div class="llm-modal-stat"><div class="stat-label">Tokens Out</div><div class="stat-value">' + (d.tokens_out != null ? d.tokens_out.toLocaleString() : '\u2014') + '</div></div>' +
    '<div class="llm-modal-stat"><div class="stat-label">Cost</div><div class="stat-value">' + (d.cost != null ? '$' + d.cost.toFixed(4) : '\u2014') + '</div></div>' +
    '<div class="llm-modal-stat"><div class="stat-label">Duration</div><div class="stat-value">' + (d.duration_ms != null ? fmtDuration(d.duration_ms) : '\u2014') + '</div></div>' +
    '</div>';

  // Token ratio bar
  var ratioHtml = '';
  if (d.tokens_in != null && d.tokens_out != null) {
    var maxTok = Math.max(d.tokens_in, d.tokens_out, 1);
    var wIn = Math.round((d.tokens_in / maxTok) * 100);
    var wOut = Math.round((d.tokens_out / maxTok) * 100);
    ratioHtml = '<div class="llm-modal-ratio">' +
      '<div class="llm-ratio-bar in" style="width:' + wIn + '%"></div>' +
      '<div class="llm-ratio-bar out" style="width:' + wOut + '%"></div>' +
      '</div>';
  }

  // Prompt section
  var promptHtml = '';
  if (d.prompt_preview) {
    promptHtml = '<div class="llm-modal-section">' +
      '<div class="llm-modal-section-header">' +
      '<div class="llm-modal-section-label">PROMPT</div>' +
      '<button class="llm-modal-copy" onclick="copyToClipboard(llmModalData.prompt_preview)">Copy</button>' +
      '</div>' +
      '<pre class="llm-modal-preview">' + escHtml(d.prompt_preview) + '</pre>' +
      '</div>';
  } else {
    promptHtml = '<div class="llm-modal-section">' +
      '<div class="llm-modal-section-header"><div class="llm-modal-section-label">PROMPT</div></div>' +
      '<div class="llm-modal-empty">No prompt captured \u2014 enable prompt previews in your SDK instrumentation</div>' +
      '</div>';
  }

  // Response section (with JSON detection)
  var responseHtml = '';
  var responseText = d.response_preview || null;
  if (responseText) {
    var displayText = responseText;
    var trimmed = responseText.trim();
    if (trimmed.charAt(0) === '{' || trimmed.charAt(0) === '[') {
      try { displayText = JSON.stringify(JSON.parse(responseText), null, 2); } catch (ex) { }
    }
    responseHtml = '<div class="llm-modal-section">' +
      '<div class="llm-modal-section-header">' +
      '<div class="llm-modal-section-label">RESPONSE</div>' +
      '<button class="llm-modal-copy" onclick="copyToClipboard(llmModalData.response_preview)">Copy</button>' +
      '</div>' +
      '<pre class="llm-modal-preview">' + escHtml(displayText) + '</pre>' +
      '</div>';
  } else {
    responseHtml = '<div class="llm-modal-section">' +
      '<div class="llm-modal-section-header"><div class="llm-modal-section-label">RESPONSE</div></div>' +
      '<div class="llm-modal-empty">No response captured</div>' +
      '</div>';
  }

  // Metadata (collapsed by default)
  var metaHtml = '';
  if (d.metadata && typeof d.metadata === 'object' && Object.keys(d.metadata).length > 0) {
    var metaRows = '';
    var metaKeys = Object.keys(d.metadata);
    metaKeys.forEach(function(k) {
      metaRows += '<div class="llm-meta-row"><span class="llm-meta-key">' + escHtml(k) + '</span><span class="llm-meta-val">' + escHtml(String(d.metadata[k])) + '</span></div>';
    });
    var fieldCount = metaKeys.length;
    metaHtml = '<div class="llm-modal-section collapsible" onclick="this.classList.toggle(\'expanded\')">' +
      '<div class="llm-modal-section-header">' +
      '<div class="llm-modal-section-label">\u25B8 Metadata (' + fieldCount + ' field' + (fieldCount > 1 ? 's' : '') + ')</div>' +
      '</div>' +
      '<div class="llm-meta-content">' + metaRows + '</div>' +
      '</div>';
  }

  // Context row
  var ctxParts = [];
  if (d.agent_id) ctxParts.push('<span class="clickable-entity" onclick="closeLlmModal(); selectAgent(\'' + escHtml(d.agent_id) + '\')">' + escHtml(d.agent_id) + '</span>');
  if (d.task_id) ctxParts.push('<span class="clickable-entity" onclick="closeLlmModal(); selectTask(\'' + escHtml(d.task_id) + '\')">' + escHtml(d.task_id) + '</span>');
  var contextHtml = '<div class="llm-modal-context">' +
    ctxParts.join(' \u00B7 ') +
    (d.timestamp ? ' \u00B7 <span style="color:var(--text-muted)">' + escHtml(d.timestamp) + '</span>' : '') +
    '</div>';

  modal.innerHTML = '<div class="llm-modal-header">' +
    '<div><div class="llm-modal-name">\u25C6 ' + escHtml(d.name || 'LLM Call') + '</div>' +
    '<div class="llm-modal-model">' + escHtml(d.model || '\u2014') + '</div></div>' +
    '<button class="llm-modal-close" onclick="closeLlmModal()">\u2715</button>' +
    '</div>' +
    statsHtml + ratioHtml + promptHtml + responseHtml + metaHtml + contextHtml;

  overlay.classList.add('visible');
}

function copyToClipboard(text) {
  if (!text) return;
  navigator.clipboard.writeText(text).catch(function() {});
  showToast('Copied to clipboard');
}

// ★ Tree view → LLM modal
function openLlmDetailFromTree(el) {
  var nodeId = el.getAttribute('data-node-id');
  if (!nodeId) return;
  var tl = TIMELINES[selectedTask];
  if (!tl || !tl.actionTree) return;

  // Search the action tree for the node with matching action_id
  var found = null;
  (function search(nodes) {
    if (found) return;
    (nodes || []).forEach(function(n) {
      if (found) return;
      if (n.action_id === nodeId) { found = n; return; }
      search(n.children);
    });
  })(Array.isArray(tl.actionTree) ? tl.actionTree : [tl.actionTree]);

  if (!found) return;
  openLlmModal({
    name: found.name || found.action_name || 'LLM Call',
    model: found.model || null,
    tokens_in: found.tokens_in || null,
    tokens_out: found.tokens_out || null,
    cost: found.cost || null,
    duration_ms: found.duration_ms || null,
    prompt_preview: found.prompt_preview || null,
    response_preview: found.response_preview || null,
    metadata: found.metadata || null,
    agent_id: found.agent_id || null,
    task_id: found.task_id || selectedTask,
    event_id: found.event_id || nodeId,
    timestamp: found.timestamp || null,
  });
}

// ★ Tree view → action detail (non-LLM nodes)
function openActionDetailFromTree(el) {
  var nodeId = el.getAttribute('data-node-id');
  if (!nodeId) return;
  var tl = TIMELINES[selectedTask];
  if (!tl || !tl.actionTree) return;

  var found = null;
  (function search(nodes) {
    if (found) return;
    (nodes || []).forEach(function(n) {
      if (found) return;
      if (n.action_id === nodeId) { found = n; return; }
      search(n.children);
    });
  })(Array.isArray(tl.actionTree) ? tl.actionTree : [tl.actionTree]);

  if (!found) return;

  // Show in pinned detail panel
  var color = found.status === 'failed' || found.status === 'error' ? 'var(--error)' : 'var(--active)';
  var name = found.name || found.action_name || 'Action';
  var dur = found.duration_ms != null ? fmtDuration(found.duration_ms) : '';
  document.getElementById('pinnedTitle').innerHTML = '<span style="color: ' + color + '">\u25CF</span> ' + escHtml(name) + (dur ? ' <span style="color: var(--text-muted); font-weight: 400; font-size: 10px; margin-left: 8px">' + dur + '</span>' : '');

  var bodyHtml = '<div class="detail-col">';
  bodyHtml += '<div class="detail-row"><span class="detail-key">action_id</span><span class="detail-val">' + escHtml(found.action_id || '\u2014') + '</span></div>';
  bodyHtml += '<div class="detail-row"><span class="detail-key">status</span><span class="detail-val">' + escHtml(found.status || '\u2014') + '</span></div>';
  if (found.duration_ms != null) bodyHtml += '<div class="detail-row"><span class="detail-key">duration</span><span class="detail-val">' + fmtDuration(found.duration_ms) + '</span></div>';
  if (found.error_message || found.exception_message) bodyHtml += '<div class="detail-row"><span class="detail-key">error</span><span class="detail-val" style="color:var(--error)">' + escHtml(found.error_message || found.exception_message) + '</span></div>';
  if (found.summary) bodyHtml += '<div class="detail-row"><span class="detail-key">summary</span><span class="detail-val">' + escHtml(found.summary) + '</span></div>';
  if (found.tool_args) bodyHtml += '<div class="detail-row"><span class="detail-key">args</span><span class="detail-val">' + escHtml(typeof found.tool_args === 'string' ? found.tool_args : JSON.stringify(found.tool_args)) + '</span></div>';
  bodyHtml += '</div>';

  document.getElementById('pinnedBody').innerHTML = bodyHtml;
  document.getElementById('pinnedDetail').classList.add('visible');
}

// ★ Activity stream → LLM modal
function openLlmDetailFromStream(eventId) {
  var cached = STREAM_EVENTS.find(function(e) { return e.eventId === eventId; });
  if (!cached) return;
  openLlmModal({
    name: cached.llmName || cached.summary || 'LLM Call',
    model: cached.model || null,
    tokens_in: cached.tokensIn || null,
    tokens_out: cached.tokensOut || null,
    cost: cached.cost || null,
    duration_ms: cached.durationMs || null,
    prompt_preview: cached.promptPreview || null,
    response_preview: cached.responsePreview || null,
    metadata: cached.llmMetadata || null,
    agent_id: cached.agent || null,
    task_id: cached.task || null,
    event_id: eventId,
    timestamp: cached.timestamp || null,
  });
}

// ★ Escape key closes LLM modal
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape' && llmModalOpen) {
    closeLlmModal();
    e.stopPropagation();
  }
});

// ═══════════════════════════════════════════════════
//  TOAST NOTIFICATIONS
// ═══════════════════════════════════════════════════

function showToast(msg, isError) {
  const container = document.getElementById('toastContainer');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast' + (isError ? ' error' : '');
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(function() { toast.remove(); }, 4000);
}

// ═══════════════════════════════════════════════════
//  WEBSOCKET CONNECTION
// ═══════════════════════════════════════════════════

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  try {
    var wsUrl;
    if (CONFIG.wsUrl) {
      // Production: use AWS API Gateway WebSocket URL
      wsUrl = CONFIG.wsUrl + '?token=' + encodeURIComponent(CONFIG.apiKey);
    } else {
      // Local: derive from HTTP endpoint (current behavior)
      var url = new URL(CONFIG.endpoint);
      var wsProto = url.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = wsProto + '//' + url.host + '/v1/stream?token=' + encodeURIComponent(CONFIG.apiKey);
    }
    ws = new WebSocket(wsUrl);

    ws.onopen = function() {
      wsRetryCount = 0;
      setConnectionStatus(true);
      stopPolling();
      ws.send(JSON.stringify({
        action: 'subscribe',
        token: CONFIG.apiKey,
        channels: ['events', 'agents'],
        filters: {
          environment: document.getElementById('envSelector').value,
          min_severity: 'info',
        },
      }));
    };

    ws.onmessage = function(evt) {
      try { handleWsMessage(JSON.parse(evt.data)); }
      catch (e) { /* ignore parse errors */ }
    };

    ws.onclose = function() {
      setConnectionStatus(false);
      ws = null;
      wsRetryCount++;
      if (wsRetryCount <= 3) {
        var delay = Math.min(1000 * Math.pow(2, wsRetryCount), 16000);
        setTimeout(connectWebSocket, delay);
      } else {
        startPolling();
      }
    };

    ws.onerror = function() { /* onclose handles it */ };
  } catch (e) {
    setConnectionStatus(false);
    startPolling();
  }
}

// ★ Feature 7: WS messages now extract enriched payload
function handleWsMessage(msg) {
  if (msg.type === 'event.new') {
    var e = msg.data || msg.event || msg;
    var payload = e.payload || {};
    var pd = payload.data || {};
    var newEvent = {
      eventId: e.event_id,
      type: e.event_type,
      kind: payload.kind || null,
      agent: e.agent_id,
      task: e.task_id,
      summary: payload.action_name || pd.action_name || payload.summary || e.event_type,
      time: 'just now',
      timestamp: e.timestamp,
      severity: e.severity || 'info',
      // Enriched
      model: pd.model || null,
      tokensIn: pd.tokens_in || null,
      tokensOut: pd.tokens_out || null,
      cost: pd.cost || null,
      durationMs: e.duration_ms || pd.duration_ms || null,
      errorMessage: pd.error_message || pd.exception_message || pd.error || null,
      errorType: pd.error_type || pd.exception_type || null,
      category: pd.category || null,
      occurrences: pd.occurrence_count || pd.occurrences || null,
      approver: pd.approver || pd.requested_from || null,
      queueDepth: pd.depth || pd.queue_depth || null,
      oldestAge: pd.oldest_age || pd.oldest || null,
      // ★ LLM Modal: prompt/response previews
      promptPreview: pd.prompt_preview || null,
      responsePreview: pd.response_preview || null,
      llmMetadata: pd.metadata || null,
      llmName: pd.name || null,
      // ★ Narrative log fields
      taskType: e.task_type || null,
      action: pd.action || null,
      issueSeverity: pd.severity || null,
      todoAction: pd.action || null,
      stepIndex: pd.step_index || null,
      totalSteps: pd.total_steps || null,
      stepCount: (pd.steps && pd.steps.length) ? pd.steps.length : null,
      scheduledCount: (pd.items && pd.items.length) ? pd.items.length : null,
      framework: e.framework || null,
    };
    if (!STREAM_EVENTS.find(function(ev) { return ev.eventId === newEvent.eventId; })) {
      STREAM_EVENTS.unshift(newEvent);
      if (STREAM_EVENTS.length > CONFIG.maxStreamEvents) STREAM_EVENTS.pop();
      lastEventTimestamp = newEvent.timestamp;
      renderStream();
    }
    if (newEvent.task === selectedTask) {
      fetchTimeline(selectedTask).then(renderTimeline);
    }
  } else if (msg.type === 'agent.status_changed' || msg.type === 'agent.stuck' || msg.type === 'agent.heartbeat') {
    fetchAgents().then(function() { renderHive(); renderSummary(); updatePipelineBadge(); });
  }
}

// Polling fallback
function startPolling() {
  if (pollTimer) return;
  setConnectionStatus(false, 'Polling');
  pollTimer = setInterval(async function() {
    await fetchAgents();
    await fetchEvents(lastEventTimestamp || undefined);
    STREAM_EVENTS.forEach(function(e) { e.time = timeAgo(e.timestamp); });
    renderHive();
    renderSummary();
    renderStream();
    updatePipelineBadge();
  }, CONFIG.pollInterval);
}

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

function setConnectionStatus(connected, label) {
  isConnected = connected;
  var dot = document.getElementById('connectionDot');
  var text = document.getElementById('connectionText');
  if (!dot || !text) return;
  if (connected) {
    dot.className = 'status-dot';
    text.textContent = 'Connected';
  } else {
    dot.className = 'status-dot disconnected';
    text.textContent = label || 'Disconnected';
  }
}

// ═══════════════════════════════════════════════════
//  INITIAL DATA LOAD
// ═══════════════════════════════════════════════════

async function initialLoad() {
  setConnectionStatus(false, 'Loading…');
  await Promise.all([fetchAgents(), fetchTasks(), fetchEvents(), fetchMetrics()]);

  renderHive();
  renderSummary();
  renderMetrics();
  renderTasks();
  renderStreamFilters();
  renderStream();
  updateFilterBar();
  updatePipelineBadge();

  // Auto-select first task
  if (!selectedTask && TASKS.length > 0) {
    selectedTask = TASKS[0].id;
    updateTimelineHeader();
    await fetchTimeline(selectedTask);
    renderTimeline();
  } else if (selectedTask) {
    updateTimelineHeader();
    await fetchTimeline(selectedTask);
    renderTimeline();
  }

  // Set workspace badge
  var env = document.getElementById('envSelector').value;
  document.getElementById('workspaceBadge').textContent = env;
}

// ═══════════════════════════════════════════════════
//  PERIODIC REFRESH
// ═══════════════════════════════════════════════════

setInterval(function() {
  STREAM_EVENTS.forEach(function(e) { e.time = timeAgo(e.timestamp); });
  TASKS.forEach(function(t) { t.time = timeAgo(t.startedAt); });
  renderStream();
}, 10000);

setInterval(async function() {
  await Promise.all([fetchAgents(), fetchTasks(), fetchMetrics()]);
  renderHive();
  renderSummary();
  renderMetrics();
  renderTasks();
  updatePipelineBadge();
}, CONFIG.refreshInterval);

// ═══════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════

(async function init() {
  var urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has('task')) selectedTask = urlParams.get('task');

  await initialLoad();

  connectWebSocket();
  setTimeout(function() {
    if (!isConnected && !pollTimer) startPolling();
  }, 5000);
})();

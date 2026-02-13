// ═══════════════════════════════════════════════════
//  CONFIGURATION
// ═══════════════════════════════════════════════════

const CONFIG = {
  endpoint: window.location.origin,
  apiKey: new URLSearchParams(window.location.search).get('apiKey')
    || localStorage.getItem('hiveboard_api_key')
    || 'hb_live_dev000000000000000000000000000000',
  pollInterval: 5000,
  maxStreamEvents: 50,
  refreshInterval: 30000,
};

// ═══════════════════════════════════════════════════
//  MUTABLE STATE (replaces hardcoded data arrays)
// ═══════════════════════════════════════════════════

let AGENTS = [];
let TASKS = [];
let TIMELINES = {};       // taskId → { plan, nodes }
let PIPELINE = {};        // agentId → pipeline data
let COST_DATA = null;     // { total_cost, call_count, tokens_in, tokens_out, by_agent, by_model }
let STREAM_EVENTS = [];
let metricsData = null;   // { summary, timeseries }

// UI state
let selectedAgent = null;
let selectedTask = null;
let activeStreamFilter = 'all';
let pinnedNode = null;
let statusFilter = null;
let currentView = 'mission';
let agentDetailAgent = null;
let activeDetailTab = 'tasks';

// Connection state
let ws = null;
let wsRetryCount = 0;
let pollTimer = null;
let isConnected = false;
let lastEventTimestamp = null;

// Timeline auto-scroll state
let timelineAutoScroll = true;

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

// ═══════════════════════════════════════════════════
//  C2.5.1 — API CLIENT
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
    };
  });
}

async function fetchTasks(agentId) {
  const env = document.getElementById('envSelector').value;
  const params = { limit: 30, sort: 'newest' };
  // Only filter by environment when a specific one is selected (not "all"/empty)
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
  // Convert API timeline to rendering format
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
    // Simplify labels: use just the event_type suffix for common types, truncate long labels
    var label = rawLabel;
    if (rawLabel && rawLabel.length > 24) {
      // For long labels with colons/underscores, take the last meaningful segment
      var parts = rawLabel.split(/[_:]/);
      if (parts.length > 1) {
        label = parts.slice(-2).join(' ');
      }
      if (label.length > 24) label = label.substring(0, 21) + '…';
    }
    var time = e.timestamp ? e.timestamp.split('T')[1].substring(0, 12) : '—';
    var dur = e.duration_ms != null ? fmtDuration(e.duration_ms) : '—';
    var detail = Object.assign({ event: e.event_type }, payload.data || {});
    var tags = (payload.data && payload.data.tags) || [];
    var llmModel = (kind === 'llm_call' && payload.data) ? payload.data.model : null;
    var isRetry = e.event_type === 'retry_started';
    var isBranchStart = e.render_hint === 'branch_start' || (e.event_type === 'action_failed' && e.payload && e.payload.data && e.payload.data.will_retry);

    return {
      label: label, rawLabel: rawLabel, time: time, type: nodeType, dur: dur,
      detail: detail, tags: tags, llmModel: llmModel,
      isBranch: isRetry, isBranchStart: isBranchStart,
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
  TIMELINES[taskId] = { plan: plan, nodes: nodes };
}

async function fetchEvents(since) {
  var params = { limit: CONFIG.maxStreamEvents, exclude_heartbeats: true };
  if (since) params.since = since;
  var env = document.getElementById('envSelector').value;
  if (env && env !== 'all' && env !== '') params.environment = env;
  var data = await apiFetch('/v1/events', params);
  if (!data || !data.data) return;
  var newEvents = data.data.map(function(e) {
    var payload = e.payload || {};
    return {
      eventId: e.event_id,
      type: e.event_type,
      kind: payload.kind || null,
      agent: e.agent_id,
      task: e.task_id,
      summary: payload.action_name || (payload.data && payload.data.action_name) || payload.summary || e.event_type,
      time: timeAgo(e.timestamp),
      timestamp: e.timestamp,
      severity: e.severity || 'info',
    };
  });
  if (since) {
    // Merge: prepend new, deduplicate
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
  var data = await apiFetch('/v1/cost', { range: '1h' });
  if (data) COST_DATA = data;
}

async function fetchPipelineData(agentId) {
  var data = await apiFetch('/v1/agents/' + encodeURIComponent(agentId) + '/pipeline');
  if (data) PIPELINE[agentId] = data;
}

// ═══════════════════════════════════════════════════
//  RENDERING — HIVE (C2.2)
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

    return `
    <div class="agent-card fade-in ${isSelected ? 'selected' : ''} ${isUrgent ? 'urgency-glow' : ''}"
         onclick="selectAgent('${a.id}')" ondblclick="openAgentDetail('${a.id}')" data-agent="${a.id}">
      <div class="agent-card-top">
        <div class="agent-name">${escHtml(a.id)}</div>
        <div class="agent-status-badge ${statusBadge[a.status] || 'badge-idle'}">${statusLabel[a.status] || a.status}</div>
      </div>
      <div class="agent-card-meta">
        <div class="agent-type-label">${escHtml(a.type)}</div>
        <div class="heartbeat-indicator"><div class="hb-dot ${hbClass(a.hb)}"></div>${hbText(a.hb)}</div>
      </div>
      ${pipelineHtml}
      ${a.task ? `<div class="agent-task-info"><span style="opacity:0.5">↳</span> <span class="clickable-entity" onclick="event.stopPropagation(); selectTask('${a.task}')">${escHtml(a.task)}</span></div>` : ''}
      <div class="sparkline-row">${a.sparkline.map(v => `<div class="spark-bar" style="height: ${(v / maxSpark) * 18 + 2}px; background: ${isUrgent ? 'var(--error)' : 'var(--active)'}"></div>`).join('')}</div>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════
//  RENDERING — SUMMARY + METRICS (C2.3.1a-b)
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
//  RENDERING — TIMELINE (C2.3.1d-h)
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
  const canvas = document.getElementById('timelineCanvas');
  const tl = TIMELINES[selectedTask];
  const nodes = tl ? tl.nodes : [];
  pinnedNode = null;
  document.getElementById('pinnedDetail').classList.remove('visible');
  renderPlanBar();

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

    html += `<div class="tl-node ${isLlm ? 'llm-node' : ''}" data-idx="${nodeIdx}" onclick="pinNode(${nodeIdx})">`;
    if (isLlm && node.llmModel) html += `<div class="tl-llm-badge">${escHtml(node.llmModel)}</div>`;
    html += `<div class="tl-node-label" style="color: ${color}" title="${escHtml(node.rawLabel || node.label)}">${escHtml(node.label)}</div>`;
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
  if (node.tags && node.tags.length) bodyHtml += '<div class="detail-col" style="flex-basis: 100%;"><div style="margin-top: 2px;">' + node.tags.map(t => `<span class="detail-payload-tag">${escHtml(t)}</span>`).join('') + '</div></div>';

  document.getElementById('pinnedBody').innerHTML = bodyHtml;
  document.getElementById('pinnedDetail').classList.add('visible');
}

function unpinDetail() {
  pinnedNode = null;
  document.querySelectorAll('.tl-node').forEach(el => el.classList.remove('pinned'));
  document.getElementById('pinnedDetail').classList.remove('visible');
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
//  RENDERING — TASKS TABLE (C2.3.1c)
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
//  RENDERING — STREAM (C2.4)
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

function renderStream() {
  const list = document.getElementById('streamList');
  const filtered = getFilteredStream();
  document.getElementById('eventCount').textContent = filtered.length + ' event' + (filtered.length !== 1 ? 's' : '');

  if (filtered.length === 0) { list.innerHTML = '<div class="empty-state" style="padding-top: 40px;"><span class="empty-state-icon">📡</span>No events match filters</div>'; return; }

  list.innerHTML = filtered.map(e => {
    const kindIcon = e.kind ? (KIND_ICON[e.kind] || '') : '';
    const sevColor = SEVERITY_COLOR[e.severity] || 'var(--idle)';
    const kindColor = e.kind === 'llm_call' ? 'var(--llm)' : e.kind === 'issue' ? 'var(--error)' : sevColor;

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
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════════════════
//  RENDERING — AGENT DETAIL (C2.3.3)
// ═══════════════════════════════════════════════════

function renderAgentDetail() {
  if (!agentDetailAgent) return;
  const agent = AGENTS.find(a => a.id === agentDetailAgent);
  if (!agent) return;

  document.getElementById('detailAgentName').textContent = agent.id;
  document.getElementById('detailAgentBadge').innerHTML = `<div class="agent-status-badge ${statusBadge[agent.status] || 'badge-idle'}">${statusLabel[agent.status] || agent.status}</div>`;

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

  // Issues
  const issues = pl.issues || [];
  if (issues.length > 0) {
    pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Active Issues</div><div class="pipeline-badge" style="color: var(--error);">${issues.length}</div></div>`;
    pHtml += `<table class="pipeline-table"><thead><tr><th>Issue</th><th>Severity</th><th>Category</th><th>Occurrences</th></tr></thead><tbody>`;
    issues.forEach(iss => { pHtml += `<tr><td>${escHtml(iss.summary)}</td><td><span class="severity-badge severity-${iss.severity || 'medium'}">${iss.severity || '—'}</span></td><td>${escHtml(iss.category || '—')}</td><td>×${iss.occurrence_count || iss.occurrences || 1}</td></tr>`; });
    pHtml += `</tbody></table></div>`;
  }

  // Queue
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

  // TODOs
  const todos = pl.todos || [];
  if (todos.length > 0) {
    pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Active TODOs</div><div class="pipeline-badge">${todos.length}</div></div>`;
    pHtml += `<table class="pipeline-table"><thead><tr><th>TODO</th><th>Priority</th><th>Source</th></tr></thead><tbody>`;
    todos.forEach(td => { pHtml += `<tr><td>${escHtml(td.summary)}</td><td><span class="priority-badge priority-${td.priority || 'normal'}">${td.priority || 'normal'}</span></td><td>${escHtml(td.source || '')}</td></tr>`; });
    pHtml += `</tbody></table></div>`;
  }

  // Scheduled
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
//  RENDERING — COST EXPLORER (C2.3.2)
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

  document.getElementById('costRibbon').innerHTML = `
    <div class="cost-stat"><div class="stat-label">Total Cost</div><div class="stat-value purple">${hasEstimates ? '~' : ''}$${totalCost.toFixed(2)}</div></div>
    <div class="cost-stat"><div class="stat-label">LLM Calls</div><div class="stat-value">${totalCalls}</div></div>
    <div class="cost-stat"><div class="stat-label">Tokens In</div><div class="stat-value">${fmtTokens(totalIn)}</div></div>
    <div class="cost-stat"><div class="stat-label">Tokens Out</div><div class="stat-value">${fmtTokens(totalOut)}</div></div>
    <div class="cost-stat"><div class="stat-label">Avg Cost/Call</div><div class="stat-value">$${avgCost.toFixed(3)}</div></div>
    ${hasEstimates ? `<div class="cost-stat"><div class="stat-label">Reported</div><div class="stat-value">$${reportedCost.toFixed(2)}</div></div>
    <div class="cost-stat"><div class="stat-label">Estimated</div><div class="stat-value" title="Server-estimated costs may differ from actual billing">~$${estimatedCost.toFixed(2)}</div></div>` : ''}
  `;

  let html = '';

  // By Model
  const byModel = d.by_model || [];
  if (byModel.length > 0) {
    const maxModelCost = Math.max(...byModel.map(m => m.cost || 0), 0.01);
    html += `<div><div class="cost-section-title">Cost by Model</div><table class="cost-table"><thead><tr><th>Model</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost</th><th></th></tr></thead><tbody>`;
    byModel.forEach(m => {
      const mEst = (m.estimated_cost || 0) > 0;
      const mCostStr = mEst ? '~$' + (m.cost || 0).toFixed(2) : '$' + (m.cost || 0).toFixed(2);
      const mTitle = mEst ? 'title="Includes server-estimated costs"' : '';
      html += `<tr><td><span class="model-badge">${escHtml(m.model || '—')}</span></td><td>${m.call_count || 0}</td><td>${fmtTokens(m.tokens_in)}</td><td>${fmtTokens(m.tokens_out)}</td><td style="color: var(--llm); font-weight: 600;" ${mTitle}>${mCostStr}</td><td style="width: 100px;"><div class="cost-bar" style="width: ${((m.cost || 0) / maxModelCost * 100)}%"></div></td></tr>`;
    });
    html += `</tbody></table></div>`;
  }

  // By Agent
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
      html += `<tr><td><span class="clickable-entity" onclick="openAgentDetail('${agentId}')" style="color: var(--accent);">${escHtml(agentId)}</span></td><td>${a.call_count || 0}</td><td>${fmtTokens(a.tokens_in)}</td><td>${fmtTokens(a.tokens_out)}</td><td style="color: var(--llm); font-weight: 600;" ${aTitle}>${aCostStr}</td><td style="width: 100px;"><div class="cost-bar" style="width: ${((a.cost || 0) / maxAgentCost * 100)}%"></div></td></tr>`;
    });
    html += `</tbody></table></div>`;
  }

  document.getElementById('costTables').innerHTML = html || '<div class="empty-state">No cost data available</div>';
}

// ═══════════════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════════════

function switchView(view) {
  currentView = view;
  document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.center-view').forEach(v => v.classList.remove('active'));

  if (view === 'mission') {
    document.querySelector('.view-tab:nth-child(1)').classList.add('active');
    document.getElementById('viewMission').classList.add('active');
  } else if (view === 'cost') {
    document.querySelector('.view-tab:nth-child(2)').classList.add('active');
    document.getElementById('viewCost').classList.add('active');
    fetchCostData().then(renderCostExplorer);
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
//  C2.5.3 — WEBSOCKET CONNECTION
// ═══════════════════════════════════════════════════

function connectWebSocket() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  try {
    var url = new URL(CONFIG.endpoint);
    var wsProto = url.protocol === 'https:' ? 'wss:' : 'ws:';
    var wsUrl = wsProto + '//' + url.host + '/v1/stream?token=' + encodeURIComponent(CONFIG.apiKey);
    ws = new WebSocket(wsUrl);

    ws.onopen = function() {
      wsRetryCount = 0;
      setConnectionStatus(true);
      stopPolling();
      // Subscribe
      ws.send(JSON.stringify({
        action: 'subscribe',
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

// C2.5.4 — Live event handling
function handleWsMessage(msg) {
  if (msg.type === 'event.new') {
    var e = msg.data || msg.event || msg;
    var payload = e.payload || {};
    var newEvent = {
      eventId: e.event_id,
      type: e.event_type,
      kind: payload.kind || null,
      agent: e.agent_id,
      task: e.task_id,
      summary: payload.action_name || (payload.data && payload.data.action_name) || payload.summary || e.event_type,
      time: 'just now',
      timestamp: e.timestamp,
      severity: e.severity || 'info',
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
    fetchAgents().then(function() { renderHive(); renderSummary(); });
  }
}

// C2.5.5 — Polling fallback
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
//  C2.5.2 — INITIAL DATA LOAD
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

// Update time labels every 10s
setInterval(function() {
  STREAM_EVENTS.forEach(function(e) { e.time = timeAgo(e.timestamp); });
  TASKS.forEach(function(t) { t.time = timeAgo(t.startedAt); });
  renderStream();
}, 10000);

// Full data refresh every 30s
setInterval(async function() {
  await Promise.all([fetchAgents(), fetchTasks(), fetchMetrics()]);
  renderHive();
  renderSummary();
  renderMetrics();
  renderTasks();
}, CONFIG.refreshInterval);

// ═══════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════

(async function init() {
  // Check for task in URL
  var urlParams = new URLSearchParams(window.location.search);
  if (urlParams.has('task')) selectedTask = urlParams.get('task');

  await initialLoad();

  // Try WebSocket, fall back to polling
  connectWebSocket();
  setTimeout(function() {
    if (!isConnected && !pollTimer) startPolling();
  }, 5000);
})();

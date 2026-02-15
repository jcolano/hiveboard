// ═══════════════════════════════════════════════════════
//  AGENT VIEW — Single-Agent Dossier
//  Depends on: common.js (CONFIG, apiFetch, helpers)
// ═══════════════════════════════════════════════════════

// ── STATE ──
let avAgents = [];
let avSelectedAgent = null;   // agent_id string
let avAgentData = null;       // from /v1/agents/{id}
let avPipeline = null;        // from /v1/agents/{id}/pipeline
let avTasks = [];             // from /v1/tasks?agent_id=X
let avMetrics = null;         // from /v1/metrics?agent_id=X
let avCost = null;            // from /v1/cost?agent_id=X
let avCostTs = null;          // from /v1/cost/timeseries?agent_id=X
let avPromptInsights = null;  // from /v1/insights/prompts?agent_id=X
let avModelInsights = null;   // from /v1/insights/models?agent_id=X
let avErrorInsights = null;   // from /v1/insights/errors?agent_id=X
let avLlmCalls = [];          // from /v1/llm-calls?agent_id=X
let avCurrentTimeline = null; // from /v1/tasks/{id}/timeline (current task)
let avRange = '24h';
let avLlmTab = 'patterns';

// WebSocket
let avWs = null;
let avWsRetry = 0;
let avIsConnected = false;

// ── CONSTANTS ──
const AV_STATUS_ICON = {
  completed: '✓', failed: '✗', escalated: '⚠', timeout: '◷',
  processing: '⏳', stuck: '⊘', error: '✗', idle: '○',
  waiting_approval: '⏸', max_turns: '◷'
};
const AV_STATUS_BADGE_CLASS = {
  processing: 'badge-processing', stuck: 'badge-stuck',
  error: 'badge-error', idle: 'badge-idle',
  waiting_approval: 'badge-waiting'
};
const AV_PATTERN_COLORS = [
  'var(--llm)', 'var(--active)', 'var(--success)',
  'var(--warning)', 'var(--error)', 'var(--accent)', 'var(--idle)'
];

// ═══════════════════════════════════════════════════════
//  DATA FETCHING
// ═══════════════════════════════════════════════════════

async function avFetchAgents() {
  var data = await apiFetch('/v1/agents', { environment: avGetEnv() });
  if (data && data.data) avAgents = data.data;
  avPopulateAgentSelector();
}

async function avFetchAgentDetail() {
  if (!avSelectedAgent) return;
  avAgentData = await apiFetch('/v1/agents/' + encodeURIComponent(avSelectedAgent));
}

async function avFetchPipeline() {
  if (!avSelectedAgent) return;
  avPipeline = await apiFetch('/v1/agents/' + encodeURIComponent(avSelectedAgent) + '/pipeline');
}

async function avFetchTasks() {
  if (!avSelectedAgent) return;
  var data = await apiFetch('/v1/tasks', {
    agent_id: avSelectedAgent,
    sort: 'newest',
    limit: 20,
    environment: avGetEnv()
  });
  if (data && data.data) avTasks = data.data;
}

async function avFetchMetrics() {
  if (!avSelectedAgent) return;
  avMetrics = await apiFetch('/v1/metrics', {
    agent_id: avSelectedAgent,
    range: avRange,
    environment: avGetEnv()
  });
}

async function avFetchCost() {
  if (!avSelectedAgent) return;
  avCost = await apiFetch('/v1/cost', {
    agent_id: avSelectedAgent,
    range: avRange
  });
}

async function avFetchCostTimeseries() {
  if (!avSelectedAgent) return;
  avCostTs = await apiFetch('/v1/cost/timeseries', {
    agent_id: avSelectedAgent,
    range: avRange
  });
}

async function avFetchPromptInsights() {
  if (!avSelectedAgent) return;
  avPromptInsights = await apiFetch('/v1/insights/prompts', {
    agent_id: avSelectedAgent,
    range: avRange,
    sort: 'cost'
  });
}

async function avFetchModelInsights() {
  if (!avSelectedAgent) return;
  avModelInsights = await apiFetch('/v1/insights/models', {
    agent_id: avSelectedAgent,
    range: avRange
  });
}

async function avFetchErrorInsights() {
  if (!avSelectedAgent) return;
  avErrorInsights = await apiFetch('/v1/insights/errors', {
    agent_id: avSelectedAgent,
    range: avRange
  });
}

async function avFetchLlmCalls() {
  if (!avSelectedAgent) return;
  var data = await apiFetch('/v1/llm-calls', {
    agent_id: avSelectedAgent,
    limit: 50
  });
  if (data && data.calls) avLlmCalls = data.calls;
}

async function avFetchTimeline(taskId) {
  if (!taskId) return null;
  return await apiFetch('/v1/tasks/' + encodeURIComponent(taskId) + '/timeline');
}

function avGetEnv() {
  var el = document.getElementById('envSelector');
  return el ? el.value : 'production';
}

// ═══════════════════════════════════════════════════════
//  AGENT SELECTOR
// ═══════════════════════════════════════════════════════

function avPopulateAgentSelector() {
  var sel = document.getElementById('agentSelector');
  var current = sel.value;
  var html = '<option value="">Select agent…</option>';
  avAgents.forEach(function(a) {
    var selected = a.agent_id === current ? ' selected' : '';
    var statusDot = a.derived_status === 'error' || a.derived_status === 'stuck' ? ' ⚠' : '';
    html += '<option value="' + escHtml(a.agent_id) + '"' + selected + '>'
      + escHtml(a.agent_id) + statusDot + '</option>';
  });
  sel.innerHTML = html;
}

async function onAgentChange() {
  var sel = document.getElementById('agentSelector');
  avSelectedAgent = sel.value || null;
  if (!avSelectedAgent) {
    document.getElementById('avContent').style.display = 'none';
    document.getElementById('avEmptyState').style.display = 'flex';
    return;
  }
  document.getElementById('avEmptyState').style.display = 'none';
  document.getElementById('avContent').style.display = 'block';
  // Scroll to top
  document.getElementById('avPage').scrollTop = 0;
  await avLoadAll();
}

function onRangeChange() {
  avRange = document.getElementById('rangeSelector').value;
  document.getElementById('avRangePill').textContent = avRange;
  avLoadTimeSensitive();
}

function onEnvChange() {
  document.getElementById('workspaceBadge').textContent = document.getElementById('envSelector').value;
  avFetchAgents().then(function() {
    if (avSelectedAgent) avLoadAll();
  });
}

// ═══════════════════════════════════════════════════════
//  DATA LOADING ORCHESTRATION
// ═══════════════════════════════════════════════════════

async function avLoadAll() {
  await Promise.all([
    avFetchAgentDetail(),
    avFetchPipeline(),
    avFetchTasks(),
    avFetchMetrics(),
    avFetchCost(),
    avFetchCostTimeseries(),
    avFetchPromptInsights(),
    avFetchModelInsights(),
    avFetchErrorInsights(),
    avFetchLlmCalls()
  ]);

  // Fetch current task timeline if processing
  if (avAgentData && avAgentData.current_task_id) {
    avCurrentTimeline = await avFetchTimeline(avAgentData.current_task_id);
  } else {
    avCurrentTimeline = null;
  }

  avRenderAll();
}

async function avLoadTimeSensitive() {
  await Promise.all([
    avFetchMetrics(),
    avFetchCost(),
    avFetchCostTimeseries(),
    avFetchPromptInsights(),
    avFetchModelInsights(),
    avFetchErrorInsights(),
    avFetchTasks(),
    avFetchLlmCalls()
  ]);
  avRenderAll();
}

function avRenderAll() {
  avRenderIdentity();
  avRenderRightNow();
  avRenderPerformance();
  avRenderLlmIntelligence();
  avRenderPipeline();
  avRenderTasks();
  avRenderAttention();
}

// ═══════════════════════════════════════════════════════
//  A. IDENTITY BAR
// ═══════════════════════════════════════════════════════

function avRenderIdentity() {
  var a = avAgentData;
  if (!a) return;

  document.getElementById('avAgentName').textContent = a.agent_id || '—';

  // Tags
  var tags = [];
  if (a.agent_type && a.agent_type !== 'general') tags.push(a.agent_type);
  if (a.framework && a.framework !== 'custom') tags.push(a.framework);
  if (a.version) tags.push('v' + a.version);
  if (a.runtime) tags.push(a.runtime);
  if (a.group && a.group !== 'default') tags.push(a.group);
  if (a.sdk_version) tags.push(a.sdk_version);

  document.getElementById('avAgentTags').innerHTML = tags.map(function(t) {
    return '<span class="av-tag">' + escHtml(t) + '</span>';
  }).join('');

  // Status badge
  var status = a.derived_status || 'idle';
  var badgeClass = AV_STATUS_BADGE_CLASS[status] || 'badge-idle';
  var statusLabel = { processing: 'Processing', stuck: 'Stuck', error: 'Error', idle: 'Idle', waiting_approval: 'Waiting' };
  document.getElementById('avStatusValue').innerHTML =
    '<span class="av-status-badge ' + badgeClass + '">' + (statusLabel[status] || status) + '</span>';

  // Heartbeat
  var hbAge = a.heartbeat_age_seconds;
  var hbClass = hbAge == null ? 'dead' : hbAge < 60 ? 'fresh' : hbAge < 300 ? 'stale' : 'dead';
  var hbText = hbAge == null ? '—' : hbAge < 60 ? hbAge + 's ago' : Math.floor(hbAge / 60) + 'm ago';
  document.getElementById('avHeartbeatValue').innerHTML =
    '<span class="av-hb-dot ' + hbClass + '"></span>' + hbText;

  // First seen
  var firstSeen = a.first_seen;
  if (firstSeen) {
    var d = new Date(firstSeen);
    var diff = Math.floor((Date.now() - d.getTime()) / 86400000);
    document.getElementById('avFirstSeen').textContent = diff < 1 ? 'Today' : diff + 'd ago';
  } else {
    document.getElementById('avFirstSeen').textContent = '—';
  }

  // Identity bar warning state
  var bar = document.getElementById('avIdentity');
  bar.classList.remove('stuck-warning', 'error-warning');
  if (status === 'stuck') bar.classList.add('stuck-warning');
  else if (status === 'error') bar.classList.add('error-warning');
}

// ═══════════════════════════════════════════════════════
//  B. RIGHT NOW
// ═══════════════════════════════════════════════════════

function avRenderRightNow() {
  var a = avAgentData;
  if (!a) return;
  var body = document.getElementById('avRightNowBody');
  var status = a.derived_status || 'idle';

  if (status === 'stuck') {
    body.innerHTML = avRenderStuckState(a);
  } else if (status === 'error') {
    body.innerHTML = avRenderErrorState(a);
  } else if (status === 'processing') {
    body.innerHTML = avRenderProcessingState(a);
  } else {
    body.innerHTML = avRenderIdleState(a);
  }
}

function avRenderProcessingState(a) {
  var taskId = a.current_task_id || '—';
  var html = '<div class="av-current-task">';
  html += '<div class="av-current-task-top">';
  html += '<div class="av-current-task-id">' + escHtml(taskId) + '</div>';
  html += '<div class="av-current-task-meta">';
  if (a.current_project_id) html += '<span>Project: ' + escHtml(a.current_project_id) + '</span>';
  html += '</div></div>';

  // Plan from current timeline
  if (avCurrentTimeline && avCurrentTimeline.plan) {
    var plan = avCurrentTimeline.plan;
    var steps = plan.steps || [];
    var completed = plan.progress ? plan.progress.completed || 0 : 0;
    var total = steps.length || 1;
    var pct = Math.round((completed / total) * 100);

    html += '<div class="av-plan">';
    html += '<div class="av-plan-header">';
    html += '<div class="av-plan-label">Plan (step ' + (completed + 1) + ' of ' + total + ')</div>';
    html += '<div class="av-plan-pct">' + pct + '%</div>';
    html += '</div>';
    html += '<div class="av-plan-track"><div class="av-plan-fill" style="width:' + pct + '%"></div></div>';
    html += '<div class="av-plan-steps">';
    steps.forEach(function(s, i) {
      var cls = i < completed ? 'completed' : i === completed ? 'current' : '';
      var icon = i < completed ? '✓' : i === completed ? '►' : '○';
      html += '<div class="av-plan-step ' + cls + '">' + icon + ' ' + escHtml(s.description || s) + '</div>';
    });
    html += '</div></div>';
  }

  // Last action from timeline
  if (avCurrentTimeline && avCurrentTimeline.action_tree && avCurrentTimeline.action_tree.length > 0) {
    var lastAction = avCurrentTimeline.action_tree[avCurrentTimeline.action_tree.length - 1];
    html += '<div class="av-last-action">';
    html += '<span class="av-last-action-label">Last action</span> ';
    html += escHtml(lastAction.action_name || lastAction.name || '—');
    if (lastAction.duration_ms) html += ' · ' + fmtDuration(lastAction.duration_ms);
    if (lastAction.status) html += ' · ' + lastAction.status;
    html += '</div>';
  }

  // LLM stats from timeline
  if (avCurrentTimeline && avCurrentTimeline.events) {
    var llmEvents = avCurrentTimeline.events.filter(function(e) {
      return e.payload && e.payload.kind === 'llm_call';
    });
    if (llmEvents.length > 0) {
      var totalIn = 0, totalOut = 0, totalCost = 0;
      llmEvents.forEach(function(e) {
        var d = e.payload.data || {};
        totalIn += d.tokens_in || 0;
        totalOut += d.tokens_out || 0;
        totalCost += d.cost || 0;
      });
      html += '<div class="av-task-llm-stats">';
      html += '<span class="llm-stat-item"><span class="llm-icon">◆</span> ' + llmEvents.length + ' calls</span>';
      html += '<span class="llm-stat-item">' + fmtTokens(totalIn) + ' in → ' + fmtTokens(totalOut) + ' out</span>';
      html += '<span class="llm-stat-item">' + fmtCost(totalCost) + '</span>';
      html += '</div>';
    }
  }

  html += '</div>';

  // Queue
  html += avRenderQueueSummary();

  return html;
}

function avRenderIdleState(a) {
  var html = '<div class="av-idle-state">';
  html += '<div class="av-idle-title">Idle</div>';

  // Last completed task
  var lastCompleted = avTasks.find(function(t) {
    return t.derived_status === 'completed' || t.derived_status === 'failed';
  });
  if (lastCompleted) {
    html += '<div class="av-idle-detail">Last: ' + escHtml(lastCompleted.task_id)
      + ' · ' + (lastCompleted.derived_status || '—')
      + ' · ' + fmtDuration(lastCompleted.duration_ms)
      + ' · ' + fmtCost(lastCompleted.total_cost)
      + '</div>';
  }

  // Queue/scheduled/todos summary
  if (avPipeline) {
    var queueDepth = avPipeline.queue ? (avPipeline.queue.depth || 0) : 0;
    var todoCount = avPipeline.todos ? avPipeline.todos.length : 0;
    var scheduled = avPipeline.scheduled || [];
    html += '<div class="av-idle-detail">Queue: ' + queueDepth + ' items'
      + ' · TODOs: ' + todoCount + ' pending</div>';
    if (scheduled.length > 0) {
      var next = scheduled.find(function(s) { return s.enabled !== false; });
      if (next) {
        html += '<div class="av-idle-detail">Next scheduled: ' + escHtml(next.name || next.id || '—') + '</div>';
      }
    }
  }

  html += '</div>';
  return html;
}

function avRenderStuckState(a) {
  var html = '<div class="av-stuck-state">';
  html += '<div class="av-stuck-title">⊘ Stuck — No heartbeat for '
    + (a.heartbeat_age_seconds ? Math.floor(a.heartbeat_age_seconds / 60) + 'm ' + (a.heartbeat_age_seconds % 60) + 's' : '—')
    + '</div>';
  if (a.current_task_id) {
    html += '<div class="av-stuck-detail">Last known task: ' + escHtml(a.current_task_id) + '</div>';
  }
  // Active issues
  if (avPipeline && avPipeline.issues && avPipeline.issues.length > 0) {
    html += '<div class="av-stuck-detail" style="margin-top:8px;">Active Issues:</div>';
    avPipeline.issues.forEach(function(iss) {
      html += '<div class="av-stuck-detail">• '
        + escHtml(iss.severity || '—') + ' ('
        + escHtml(iss.category || '—') + '): '
        + escHtml(iss.context || iss.summary || '—') + '</div>';
    });
  }
  html += '</div>';
  return html;
}

function avRenderErrorState(a) {
  var html = '<div class="av-error-state">';
  html += '<div class="av-error-title">✗ Error State</div>';
  // Find latest failed task
  var failedTask = avTasks.find(function(t) { return t.derived_status === 'failed'; });
  if (failedTask) {
    html += '<div class="av-stuck-detail">Failed task: ' + escHtml(failedTask.task_id) + '</div>';
    if (failedTask.error_message) {
      html += '<div class="av-stuck-detail" style="color:var(--error);">' + escHtml(failedTask.error_message) + '</div>';
    }
  }
  if (avPipeline && avPipeline.issues && avPipeline.issues.length > 0) {
    html += '<div class="av-stuck-detail" style="margin-top:8px;">Active Issues: ' + avPipeline.issues.length + '</div>';
  }
  html += '</div>';
  return html;
}

function avRenderQueueSummary() {
  if (!avPipeline || !avPipeline.queue) return '';
  var q = avPipeline.queue;
  var depth = q.depth || 0;
  if (depth === 0 && !q.processing) return '';
  var html = '<div class="av-queue-summary">';
  html += '<span class="queue-label">Queue</span> ';
  html += depth + ' item' + (depth !== 1 ? 's' : '') + ' waiting';
  if (q.oldest_age_seconds) html += ' · oldest: ' + q.oldest_age_seconds + 's';
  if (q.items && q.items.length > 0) {
    var first = q.items[0];
    html += ' · next: ';
    if (first.priority) html += '[' + first.priority.toUpperCase() + '] ';
    html += escHtml((first.summary || first.id || '—').substring(0, 60));
  }
  html += '</div>';
  return html;
}

// ═══════════════════════════════════════════════════════
//  C. PERFORMANCE STORY
// ═══════════════════════════════════════════════════════

function avRenderPerformance() {
  var grid = document.getElementById('avPerfGrid');
  var insights = document.getElementById('avPerfInsights');
  document.getElementById('avRangePill').textContent = avRange;

  var summary = avMetrics && avMetrics.summary ? avMetrics.summary : {};
  var timeseries = avMetrics && avMetrics.timeseries ? avMetrics.timeseries : [];
  var costTotal = avCost ? avCost.total_cost : null;
  var callCount = avCost ? avCost.call_count : null;
  var tokIn = avCost ? avCost.total_tokens_in : null;
  var tokOut = avCost ? avCost.total_tokens_out : null;

  var tasksCompleted = summary.completed || 0;
  var tasksFailed = summary.failed || 0;
  var totalTasks = tasksCompleted + tasksFailed;
  var successRate = totalTasks > 0 ? ((tasksCompleted / totalTasks) * 100) : null;
  var avgDur = summary.avg_duration_ms;
  var throughput = totalTasks > 0 ? (totalTasks / Math.max(avRangeHours(), 1)).toFixed(1) : '0';
  var errorCount = tasksFailed + (summary.action_failures || 0);

  // Build sparkline data from timeseries
  function sparkHtml(values, color) {
    if (!values || values.length === 0) return '';
    var max = Math.max.apply(null, values.map(function(v) { return v || 0; })) || 1;
    return '<div class="av-perf-sparkline">' + values.map(function(v) {
      var h = Math.max(2, Math.round(((v || 0) / max) * 28));
      return '<div class="av-spark-bar" style="height:' + h + 'px;background:' + (color || 'var(--active)') + ';opacity:0.4;"></div>';
    }).join('') + '</div>';
  }

  var tasksSpark = timeseries.map(function(b) { return (b.tasks_completed || 0) + (b.tasks_failed || 0); });
  var costSpark = timeseries.map(function(b) { return b.cost || 0; });
  var errorSpark = timeseries.map(function(b) { return b.tasks_failed || 0; });

  var cards = [
    { label: 'Tasks', value: totalTasks, spark: sparkHtml(tasksSpark, 'var(--active)'), color: '' },
    { label: 'Success Rate', value: successRate != null ? successRate.toFixed(1) + '%' : '—', spark: '', color: successRate != null && successRate < 80 ? 'red' : '' },
    { label: 'Avg Duration', value: fmtDuration(avgDur), spark: '', color: '' },
    { label: 'Cost', value: fmtCost(costTotal), spark: sparkHtml(costSpark, 'var(--llm)'), color: 'purple' },
    { label: 'LLM Calls', value: callCount != null ? callCount : '—', spark: '', color: 'purple' },
    { label: 'Tokens', value: tokIn != null ? fmtTokens(tokIn) + ' / ' + fmtTokens(tokOut) : '—', spark: '', color: '' },
    { label: 'Errors', value: errorCount, spark: sparkHtml(errorSpark, 'var(--error)'), color: errorCount > 0 ? 'red' : '' },
    { label: 'Throughput', value: throughput + '/hr', spark: '', color: '' }
  ];

  grid.innerHTML = cards.map(function(c) {
    return '<div class="av-perf-card">'
      + '<div class="av-perf-label">' + c.label + '</div>'
      + '<div class="av-perf-value' + (c.color ? ' ' + c.color : '') + '">' + c.value + '</div>'
      + c.spark
      + '</div>';
  }).join('');

  // Derived insights
  var insightHtml = '';
  if (callCount && totalTasks > 0) {
    var ratio = (callCount / totalTasks).toFixed(1);
    insightHtml += '<div class="av-perf-insight">'
      + '<span class="av-perf-insight-label">LLM calls/task:</span> '
      + '<span class="av-perf-insight-value">' + ratio + '</span></div>';
  }
  if (costTotal != null && totalTasks > 0) {
    var cpt = (costTotal / totalTasks).toFixed(3);
    insightHtml += '<div class="av-perf-insight">'
      + '<span class="av-perf-insight-label">Cost/task:</span> '
      + '<span class="av-perf-insight-value">$' + cpt + '</span></div>';
  }
  insights.innerHTML = insightHtml;
}

function avRangeHours() {
  if (avRange === '1h') return 1;
  if (avRange === '6h') return 6;
  if (avRange === '24h') return 24;
  if (avRange === '7d') return 168;
  return 24;
}

// ═══════════════════════════════════════════════════════
//  D. LLM INTELLIGENCE
// ═══════════════════════════════════════════════════════

function switchLlmTab(tab) {
  avLlmTab = tab;
  document.querySelectorAll('.av-llm-tab').forEach(function(t) { t.classList.remove('active'); });
  document.querySelectorAll('.av-llm-content').forEach(function(c) { c.classList.remove('active'); });
  document.querySelector('.av-llm-tab[data-tab="' + tab + '"]').classList.add('active');
  if (tab === 'patterns') document.getElementById('avLlmPatterns').classList.add('active');
  else if (tab === 'models') document.getElementById('avLlmModels').classList.add('active');
  else if (tab === 'log') document.getElementById('avLlmLog').classList.add('active');
}

function avRenderLlmIntelligence() {
  avRenderCallPatterns();
  avRenderModelBreakdown();
  avRenderCallLog();
}

function avRenderCallPatterns() {
  var el = document.getElementById('avLlmPatterns');
  var prompts = avPromptInsights && avPromptInsights.prompts ? avPromptInsights.prompts : [];

  if (prompts.length === 0) {
    el.innerHTML = '<div class="av-pipe-empty">No LLM call data in this time range.</div>';
    return;
  }

  var html = '<table class="av-patterns-table">';
  html += '<thead><tr><th>Call Name</th><th>Calls</th><th>Tok In</th><th>Tok Out</th><th>Cost</th><th>Avg ms</th></tr></thead>';
  html += '<tbody>';

  prompts.forEach(function(p, i) {
    var color = AV_PATTERN_COLORS[i % AV_PATTERN_COLORS.length];
    html += '<tr>';
    html += '<td><div class="av-pattern-name"><span class="av-pattern-dot" style="background:' + color + '"></span>'
      + escHtml(p.name || '—') + '</div></td>';
    html += '<td>' + (p.total_count || 0) + '</td>';
    html += '<td>' + fmtTokens(p.total_tokens_in) + '</td>';
    html += '<td>' + fmtTokens(p.total_tokens_out) + '</td>';
    html += '<td>' + fmtCost(p.total_cost) + '</td>';
    html += '<td>' + (p.avg_duration_ms ? Math.round(p.avg_duration_ms) : '—') + '</td>';
    html += '</tr>';

    // Sub-detail row
    var subs = [];
    if (p.primary_model) subs.push('model: ' + shortModelName(p.primary_model));
    if (p.avg_tokens_in) subs.push('avg: ' + Math.round(p.avg_tokens_in) + ' → ' + Math.round(p.avg_tokens_out || 0) + ' tok/call');
    if (subs.length > 0) {
      html += '<tr><td colspan="6" class="av-pattern-sub">' + escHtml(subs.join(' · ')) + '</td></tr>';
    }
  });

  html += '</tbody></table>';

  // Efficiency insights
  if (avCost && prompts.length > 0) {
    var totalCost = avCost.total_cost || 0;
    html += '<div class="av-efficiency-box">';
    prompts.slice(0, 3).forEach(function(p) {
      if (p.total_cost && totalCost > 0) {
        var pct = ((p.total_cost / totalCost) * 100).toFixed(0);
        html += '<strong>' + escHtml(p.name) + '</strong>: ' + pct + '% of cost';
        if (p.total_count) html += ' (' + p.total_count + ' calls)';
        html += '<br>';
      }
    });
    if (avPromptInsights.biggest_prompt) {
      var bp = avPromptInsights.biggest_prompt;
      html += 'Biggest prompt: <strong>' + escHtml(bp.name || '—') + '</strong> — '
        + fmtTokens(bp.max_tokens_in) + ' tokens';
      if (bp.task_id) html += ' (task: ' + escHtml(bp.task_id) + ')';
    }
    html += '</div>';
  }

  el.innerHTML = html;
}

function avRenderModelBreakdown() {
  var el = document.getElementById('avLlmModels');
  var models = avModelInsights && avModelInsights.models ? avModelInsights.models : [];

  if (models.length === 0) {
    el.innerHTML = '<div class="av-pipe-empty">No model data in this time range.</div>';
    return;
  }

  var totalCost = 0;
  models.forEach(function(m) { totalCost += m.cost || 0; });

  var html = '';
  models.forEach(function(m) {
    var pct = totalCost > 0 ? ((m.cost / totalCost) * 100).toFixed(0) : 0;
    html += '<div class="av-model-card">';
    html += '<div class="av-model-header">';
    html += '<div class="av-model-name">' + escHtml(m.model || '—') + '</div>';
    html += '<div class="av-model-pct">' + pct + '% of cost</div>';
    html += '</div>';
    html += '<div class="av-model-bar-track"><div class="av-model-bar-fill" style="width:' + pct + '%"></div></div>';
    html += '<div class="av-model-stats">';
    html += '<span>' + (m.call_count || 0) + ' calls</span>';
    html += '<span>' + fmtTokens(m.tokens_in) + ' in / ' + fmtTokens(m.tokens_out) + ' out</span>';
    html += '<span>' + fmtCost(m.cost) + '</span>';
    if (m.avg_duration_ms) html += '<span>' + Math.round(m.avg_duration_ms) + 'ms avg</span>';
    html += '</div>';
    if (m.top_calls && m.top_calls.length > 0) {
      html += '<div class="av-model-stats" style="margin-top:4px;">';
      html += '<span>Used for: ' + m.top_calls.map(function(c) { return escHtml(c.name || c); }).join(', ') + '</span>';
      html += '</div>';
    }
    html += '</div>';
  });

  // Token flow chart
  if (avCostTs && avCostTs.buckets && avCostTs.buckets.length > 0) {
    var buckets = avCostTs.buckets;
    var maxTok = 1;
    buckets.forEach(function(b) {
      maxTok = Math.max(maxTok, b.tokens_in || 0, b.tokens_out || 0);
    });
    var totalIn = avCost ? avCost.total_tokens_in : 0;
    var totalOut = avCost ? avCost.total_tokens_out : 0;
    var ratio = totalOut > 0 ? (totalIn / totalOut).toFixed(1) : '—';

    html += '<div class="av-token-flow">';
    html += '<div class="av-token-flow-header">';
    html += '<div class="av-token-flow-title">Token Flow (' + avRange + ')</div>';
    html += '<div class="av-token-totals">In: ' + fmtTokens(totalIn) + ' · Out: ' + fmtTokens(totalOut) + '</div>';
    html += '</div>';
    html += '<div class="av-token-chart">';
    buckets.forEach(function(b) {
      var hIn = Math.max(2, Math.round(((b.tokens_in || 0) / maxTok) * 40));
      var hOut = Math.max(2, Math.round(((b.tokens_out || 0) / maxTok) * 40));
      html += '<div class="av-token-bar-in" style="height:' + hIn + 'px" title="In: ' + (b.tokens_in || 0) + '"></div>';
    });
    html += '</div>';
    html += '<div class="av-token-ratio">In/Out ratio: ' + ratio + ':1</div>';
    html += '</div>';
  }

  el.innerHTML = html;
}

function avRenderCallLog() {
  var list = document.getElementById('avLogList');

  if (avLlmCalls.length === 0) {
    list.innerHTML = '<div class="av-pipe-empty">No LLM calls recorded.</div>';
    return;
  }

  var html = '';
  var lastTaskId = null;

  avLlmCalls.forEach(function(call) {
    // Turn boundary
    if (lastTaskId && call.task_id !== lastTaskId) {
      html += '<div class="av-log-divider">── task boundary ──</div>';
    }
    lastTaskId = call.task_id;

    var time = call.timestamp ? new Date(call.timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—';
    var model = shortModelName(call.model);

    html += '<div class="av-log-entry">';
    html += '<div class="av-log-top">';
    html += '<span class="av-log-time">' + time + '</span>';
    html += '<span class="av-log-name">' + escHtml(call.name || '—') + '</span>';
    html += '<span class="av-log-model">' + escHtml(model) + '</span>';
    html += '<span class="av-log-tokens">' + fmtTokens(call.tokens_in) + '→' + fmtTokens(call.tokens_out) + '</span>';
    html += '<span class="av-log-cost">' + fmtCost(call.cost, call.cost_source) + '</span>';
    html += '</div>';

    // Context line
    html += '<div class="av-log-context">';
    if (call.task_id) html += '<span class="av-log-context-item"><span class="ctx-label">Task:</span> <span class="ctx-value">' + escHtml(call.task_id) + '</span></span>';
    html += '</div>';

    // View buttons
    html += '<div class="av-log-actions">';
    if (call.prompt_preview) {
      html += '<button class="av-log-btn" onclick="avOpenLlmModal(\'' + escHtml(call.event_id || '') + '\')">View prompt</button>';
    }
    if (call.response_preview) {
      html += '<button class="av-log-btn" onclick="avOpenLlmModal(\'' + escHtml(call.event_id || '') + '\')">View response</button>';
    }
    html += '</div>';

    html += '</div>';
  });

  list.innerHTML = html;
}

function shortModelName(model) {
  if (!model) return 'LLM';
  return model.replace(/-\d{8,}$/, '');
}

// ═══════════════════════════════════════════════════════
//  E. PIPELINE
// ═══════════════════════════════════════════════════════

function avRenderPipeline() {
  var body = document.getElementById('avPipelineBody');
  if (!avPipeline) {
    body.innerHTML = '<div class="av-pipe-empty">No pipeline data available.</div>';
    return;
  }

  var html = '';

  // Queue
  var queue = avPipeline.queue || {};
  var queueItems = queue.items || [];
  html += '<div class="av-pipe-group">';
  html += '<div class="av-pipe-group-header">';
  html += '<div class="av-pipe-group-title">Queue</div>';
  html += '<div class="av-pipe-group-count">' + (queue.depth || 0) + '</div>';
  html += '</div>';
  if (queueItems.length === 0) {
    html += '<div class="av-pipe-empty">Empty</div>';
  } else {
    queueItems.forEach(function(item) {
      html += '<div class="av-pipe-item">';
      html += '<span class="av-pipe-priority ' + (item.priority || 'normal') + '">' + (item.priority || 'norm').toUpperCase() + '</span>';
      html += '<div class="av-pipe-text">' + escHtml(item.summary || item.id || '—');
      if (item.queued_at) html += '<div class="av-pipe-meta">' + timeAgo(item.queued_at) + '</div>';
      html += '</div></div>';
    });
  }
  html += '</div>';

  // TODOs
  var todos = avPipeline.todos || [];
  html += '<div class="av-pipe-group">';
  html += '<div class="av-pipe-group-header">';
  html += '<div class="av-pipe-group-title">TODOs</div>';
  html += '<div class="av-pipe-group-count">' + todos.length + '</div>';
  html += '</div>';
  if (todos.length === 0) {
    html += '<div class="av-pipe-empty">None</div>';
  } else {
    todos.forEach(function(t) {
      html += '<div class="av-pipe-item">';
      html += '<span class="av-pipe-priority ' + (t.priority || 'normal') + '">' + (t.priority || 'norm').toUpperCase() + '</span>';
      html += '<div class="av-pipe-text">' + escHtml(t.context || t.todo_id || '—');
      if (t.source) html += '<div class="av-pipe-meta">from: ' + escHtml(t.source) + '</div>';
      html += '</div></div>';
    });
  }
  html += '</div>';

  // Issues
  var issues = avPipeline.issues || [];
  html += '<div class="av-pipe-group">';
  html += '<div class="av-pipe-group-header">';
  html += '<div class="av-pipe-group-title">Issues</div>';
  html += '<div class="av-pipe-group-count">' + issues.length + '</div>';
  html += '</div>';
  if (issues.length === 0) {
    html += '<div class="av-pipe-empty">None</div>';
  } else {
    issues.forEach(function(iss) {
      html += '<div class="av-pipe-item">';
      html += '<span class="av-issue-severity ' + (iss.severity || 'medium') + '"></span>';
      html += '<div class="av-pipe-text">';
      html += '<strong>' + escHtml(iss.severity || '—') + '</strong> · ' + escHtml(iss.category || '—');
      html += '<div class="av-pipe-meta">' + escHtml(iss.context || '—');
      if (iss.occurrence_count) html += ' · seen ' + iss.occurrence_count + 'x';
      html += '</div></div></div>';
    });
  }
  html += '</div>';

  // Scheduled
  var scheduled = avPipeline.scheduled || [];
  html += '<div class="av-pipe-group">';
  html += '<div class="av-pipe-group-header">';
  html += '<div class="av-pipe-group-title">Scheduled</div>';
  html += '<div class="av-pipe-group-count">' + scheduled.length + '</div>';
  html += '</div>';
  if (scheduled.length === 0) {
    html += '<div class="av-pipe-empty">None</div>';
  } else {
    scheduled.forEach(function(s) {
      html += '<div class="av-sched-item">';
      html += '<div><div class="av-sched-name">' + escHtml(s.name || s.id || '—') + '</div></div>';
      html += '<div class="av-sched-info">';
      if (s.interval) html += 'every ' + s.interval + '<br>';
      if (s.next_run) html += 'next: ' + new Date(s.next_run).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit' });
      var statusIcon = s.last_status === 'completed' ? ' ✓' : s.last_status === 'failed' ? ' ✗' : '';
      html += statusIcon;
      html += '</div></div>';
    });
  }
  html += '</div>';

  body.innerHTML = html;
}

// ═══════════════════════════════════════════════════════
//  F. RECENT TASKS
// ═══════════════════════════════════════════════════════

function avRenderTasks() {
  var body = document.getElementById('avTasksBody');
  var count = document.getElementById('avTaskCount');

  count.textContent = avTasks.length + ' tasks';

  if (avTasks.length === 0) {
    body.innerHTML = '<div class="av-pipe-empty">No tasks in this time range.</div>';
    return;
  }

  var html = '';
  avTasks.forEach(function(t) {
    var status = t.derived_status || 'processing';
    var icon = AV_STATUS_ICON[status] || '○';

    html += '<div class="av-task-card" onclick="avExpandTask(\'' + escHtml(t.task_id || t.id || '') + '\')">';
    html += '<div class="av-task-top">';
    html += '<div class="av-task-status-icon ' + status + '">' + icon + '</div>';
    html += '<div class="av-task-id">' + escHtml(t.task_id || t.id || '—') + '</div>';
    html += '<div class="av-task-time">' + timeAgo(t.started_at || t.startedAt) + '</div>';
    html += '</div>';

    html += '<div class="av-task-stats">';
    if (t.task_type) html += '<span class="av-task-type-badge">' + escHtml(t.task_type) + '</span>';
    html += '<span>' + fmtDuration(t.duration_ms) + '</span>';
    if (t.action_count) html += '<span>' + t.action_count + ' actions</span>';
    html += '<span>' + fmtCost(t.total_cost) + '</span>';
    if (t.llm_call_count) html += '<span>◆ ' + t.llm_call_count + ' LLM</span>';
    html += '</div>';

    // Error/escalation lines
    if (status === 'failed' && (t.error_message || t.exception_message)) {
      html += '<div class="av-task-error-line">' + escHtml(t.error_message || t.exception_message) + '</div>';
    }
    if (status === 'escalated' && t.escalation_reason) {
      html += '<div class="av-task-escalation-line">' + escHtml(t.escalation_reason) + '</div>';
    }

    html += '<div class="av-task-expand-hint">Click to expand →</div>';
    html += '</div>';
  });

  body.innerHTML = html;
}

async function avExpandTask(taskId) {
  if (!taskId) return;
  document.getElementById('avTaskModalTitle').textContent = taskId;
  document.getElementById('avTaskModal').classList.add('visible');
  document.getElementById('avTaskModalBody').innerHTML = '<div class="av-shimmer" style="height:200px;"></div>';

  var timeline = await avFetchTimeline(taskId);
  if (!timeline) {
    document.getElementById('avTaskModalBody').innerHTML = '<div class="av-pipe-empty">Could not load timeline.</div>';
    return;
  }

  var html = '';

  // Summary stats
  html += '<div class="av-task-stats" style="margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border);">';
  html += '<span class="av-status-badge ' + (AV_STATUS_BADGE_CLASS[timeline.derived_status] || 'badge-idle') + '">'
    + (timeline.derived_status || '—') + '</span>';
  html += '<span>' + fmtDuration(timeline.duration_ms) + '</span>';
  html += '<span>' + fmtCost(timeline.total_cost) + '</span>';
  html += '</div>';

  // Plan
  if (timeline.plan && timeline.plan.steps) {
    html += '<div style="margin-bottom:16px;">';
    html += '<div class="av-plan-label" style="margin-bottom:8px;">Plan</div>';
    timeline.plan.steps.forEach(function(s, i) {
      var completed = timeline.plan.progress ? timeline.plan.progress.completed || 0 : 0;
      var cls = i < completed ? 'completed' : i === completed ? 'current' : '';
      var icon = i < completed ? '✓' : i === completed ? '►' : '○';
      html += '<div class="av-plan-step ' + cls + '">' + icon + ' ' + escHtml(s.description || s) + '</div>';
    });
    html += '</div>';
  }

  // Action tree
  if (timeline.action_tree && timeline.action_tree.length > 0) {
    html += '<div class="av-plan-label" style="margin-bottom:8px;">Actions</div>';
    html += '<div class="av-modal-action-tree">';
    timeline.action_tree.forEach(function(a) {
      var nested = a.parent_action_id ? ' nested' : '';
      var statusCls = a.status === 'success' ? 'action-status-ok' : a.status === 'failure' ? 'action-status-fail' : '';
      var icon = a.status === 'failure' ? '✗' : a.status === 'success' ? '✓' : '⚡';
      html += '<div class="av-modal-action' + nested + '">';
      html += '<span class="action-icon ' + statusCls + '">' + icon + '</span>';
      html += '<span class="action-name">' + escHtml(a.action_name || a.name || '—') + '</span>';
      html += '<span class="action-duration">' + fmtDuration(a.duration_ms) + '</span>';
      html += '</div>';
    });
    html += '</div>';
  }

  // LLM calls within this task
  var llmEvents = (timeline.events || []).filter(function(e) {
    return e.payload && e.payload.kind === 'llm_call';
  });
  if (llmEvents.length > 0) {
    html += '<div class="av-plan-label" style="margin:16px 0 8px;">LLM Calls (' + llmEvents.length + ')</div>';
    html += '<div class="av-modal-llm-list">';
    llmEvents.forEach(function(e) {
      var d = e.payload.data || {};
      html += '<div class="av-modal-llm-item" onclick="avOpenLlmModalFromEvent(event, this)" '
        + 'data-prompt="' + escHtml(d.prompt_preview || '') + '" '
        + 'data-response="' + escHtml(d.response_preview || '') + '" '
        + 'data-model="' + escHtml(d.model || '') + '" '
        + 'data-name="' + escHtml(d.name || '') + '" '
        + 'data-cost="' + (d.cost || 0) + '" '
        + 'data-tokin="' + (d.tokens_in || 0) + '" '
        + 'data-tokout="' + (d.tokens_out || 0) + '" '
        + 'data-dur="' + (d.duration_ms || 0) + '">';
      html += '<span class="av-modal-llm-icon">◆</span>';
      html += '<span class="av-modal-llm-name">' + escHtml(d.name || '—') + '</span>';
      html += '<span class="av-modal-llm-detail">' + shortModelName(d.model)
        + ' · ' + fmtTokens(d.tokens_in) + '→' + fmtTokens(d.tokens_out) + '</span>';
      html += '<span class="av-modal-llm-cost">' + fmtCost(d.cost, d.cost_source) + '</span>';
      html += '</div>';
    });
    html += '</div>';
  }

  // Error chains
  if (timeline.error_chains && timeline.error_chains.length > 0) {
    html += '<div class="av-plan-label" style="margin:16px 0 8px;color:var(--error);">Error Chain</div>';
    timeline.error_chains.forEach(function(err) {
      html += '<div class="av-task-error-line">' + escHtml(err.event_type || '—')
        + ': ' + escHtml(err.summary || err.error || '—') + '</div>';
    });
  }

  document.getElementById('avTaskModalBody').innerHTML = html;
}

function closeTaskModal() {
  document.getElementById('avTaskModal').classList.remove('visible');
}

// ═══════════════════════════════════════════════════════
//  G. ATTENTION
// ═══════════════════════════════════════════════════════

function avRenderAttention() {
  var items = [];
  var a = avAgentData;
  if (!a) return;

  // Stuck agent
  if (a.derived_status === 'stuck') {
    items.push({
      type: 'critical', title: 'Agent Stuck',
      body: 'No heartbeat for ' + (a.heartbeat_age_seconds ? Math.floor(a.heartbeat_age_seconds / 60) + 'm' : '—')
        + '. Threshold: ' + (a.stuck_threshold_seconds || 300) + 's.'
    });
  }

  // Active issues
  if (avPipeline && avPipeline.issues) {
    avPipeline.issues.forEach(function(iss) {
      if (iss.severity === 'critical' || iss.severity === 'high') {
        items.push({
          type: iss.severity === 'critical' ? 'critical' : 'warning',
          title: (iss.severity || 'Issue').toUpperCase() + ': ' + (iss.category || 'Unknown'),
          body: (iss.context || '—') + (iss.occurrence_count ? ' · Seen ' + iss.occurrence_count + 'x' : '')
        });
      }
    });
  }

  // High-priority stuck TODOs (> 1 hour old)
  if (avPipeline && avPipeline.todos) {
    avPipeline.todos.forEach(function(t) {
      if (t.priority === 'high' && t.created_at) {
        var age = Date.now() - new Date(t.created_at).getTime();
        if (age > 3600000) {
          items.push({
            type: 'warning', title: 'Stuck TODO',
            body: escHtml(t.context || t.todo_id || '—') + ' — high priority, created ' + Math.floor(age / 3600000) + 'h ago'
          });
        }
      }
    });
  }

  // Error patterns from insights
  if (avErrorInsights) {
    var totalErrors = avErrorInsights.total_errors || 0;
    if (totalErrors > 3) {
      items.push({
        type: 'warning', title: 'Error Pattern',
        body: totalErrors + ' errors in the last ' + avRange + '.'
          + (avErrorInsights.by_type_global ? ' Top type: ' + Object.keys(avErrorInsights.by_type_global)[0] : '')
      });
    }
  }

  // Cost spike (simple check: if cost is high relative to tasks)
  if (avCost && avMetrics && avMetrics.summary) {
    var totalTasks = (avMetrics.summary.completed || 0) + (avMetrics.summary.failed || 0);
    if (totalTasks > 0 && avCost.total_cost / totalTasks > 0.5) {
      items.push({
        type: 'warning', title: 'High Cost per Task',
        body: '$' + (avCost.total_cost / totalTasks).toFixed(2) + '/task average. Total: ' + fmtCost(avCost.total_cost) + ' across ' + totalTasks + ' tasks.'
      });
    }
  }

  // Render
  var attSection = document.getElementById('avAttention');
  var healthyEl = document.getElementById('avHealthy');

  if (items.length === 0) {
    attSection.style.display = 'none';
    healthyEl.style.display = 'block';
    return;
  }

  attSection.style.display = 'block';
  healthyEl.style.display = 'none';
  document.getElementById('avAttentionCount').textContent = items.length + ' item' + (items.length !== 1 ? 's' : '');

  var html = '';
  items.forEach(function(item) {
    html += '<div class="av-attention-item ' + (item.type === 'critical' ? 'critical' : '') + '">';
    html += '<div class="av-attention-item-title">' + item.title + '</div>';
    html += '<div class="av-attention-item-body">' + item.body + '</div>';
    html += '</div>';
  });

  document.getElementById('avAttentionBody').innerHTML = html;
}

// ═══════════════════════════════════════════════════════
//  LLM MODAL (reused pattern from main dashboard)
// ═══════════════════════════════════════════════════════

function avOpenLlmModal(eventId) {
  // Find the call in avLlmCalls
  var call = avLlmCalls.find(function(c) { return c.event_id === eventId; });
  if (!call) return;
  avShowLlmModal(call);
}

function avOpenLlmModalFromEvent(evt, el) {
  evt.stopPropagation();
  var call = {
    name: el.dataset.name,
    model: el.dataset.model,
    cost: parseFloat(el.dataset.cost),
    tokens_in: parseInt(el.dataset.tokin),
    tokens_out: parseInt(el.dataset.tokout),
    duration_ms: parseInt(el.dataset.dur),
    prompt_preview: el.dataset.prompt,
    response_preview: el.dataset.response
  };
  avShowLlmModal(call);
}

function avShowLlmModal(call) {
  var modal = document.getElementById('llmModalContent');
  var tIn = call.tokens_in || 0;
  var tOut = call.tokens_out || 0;
  var total = tIn + tOut || 1;

  var html = '<div class="llm-modal-header">';
  html += '<div><div class="llm-modal-name">' + escHtml(call.name || '—') + '</div>';
  html += '<div class="llm-modal-model">' + escHtml(call.model || '—') + '</div></div>';
  html += '<button class="llm-modal-close" onclick="closeLlmModal()">✕</button>';
  html += '</div>';

  html += '<div class="llm-modal-stats">';
  html += '<div class="llm-modal-stat"><div class="stat-label">Tokens In</div><div class="stat-value">' + fmtTokens(tIn) + '</div></div>';
  html += '<div class="llm-modal-stat"><div class="stat-label">Tokens Out</div><div class="stat-value">' + fmtTokens(tOut) + '</div></div>';
  html += '<div class="llm-modal-stat"><div class="stat-label">Cost</div><div class="stat-value purple">' + fmtCost(call.cost) + '</div></div>';
  html += '<div class="llm-modal-stat"><div class="stat-label">Latency</div><div class="stat-value">' + fmtDuration(call.duration_ms) + '</div></div>';
  html += '</div>';

  html += '<div class="llm-modal-ratio">';
  html += '<div class="llm-ratio-bar in" style="width:' + Math.round((tIn / total) * 100) + '%"></div>';
  html += '<div class="llm-ratio-bar out" style="width:' + Math.round((tOut / total) * 100) + '%"></div>';
  html += '</div>';

  if (call.prompt_preview) {
    html += '<div class="llm-modal-section">';
    html += '<div class="llm-modal-section-header"><div class="llm-modal-section-label">Prompt</div></div>';
    html += '<pre class="llm-modal-preview">' + escHtml(call.prompt_preview) + '</pre>';
    html += '</div>';
  }

  if (call.response_preview) {
    html += '<div class="llm-modal-section">';
    html += '<div class="llm-modal-section-header"><div class="llm-modal-section-label">Response</div></div>';
    html += '<pre class="llm-modal-preview">' + escHtml(call.response_preview) + '</pre>';
    html += '</div>';
  }

  modal.innerHTML = html;
  document.getElementById('llmModalOverlay').classList.add('visible');
}

function closeLlmModal() {
  document.getElementById('llmModalOverlay').classList.remove('visible');
}

// ═══════════════════════════════════════════════════════
//  CONNECTION STATUS
// ═══════════════════════════════════════════════════════

function avSetConnection(connected, text) {
  avIsConnected = connected;
  var dot = document.getElementById('connectionDot');
  var label = document.getElementById('connectionText');
  if (connected) {
    dot.style.background = 'var(--success)';
    label.textContent = text || 'Connected';
  } else {
    dot.style.background = 'var(--warning)';
    label.textContent = text || 'Disconnected';
  }
}

// WebSocket (simplified — status updates only)
function avConnectWs() {
  if (!CONFIG.wsUrl || !CONFIG.apiKey) return;
  try {
    avWs = new WebSocket(CONFIG.wsUrl + '?token=' + encodeURIComponent(CONFIG.apiKey));
    avWs.onopen = function() {
      avSetConnection(true, 'Live');
      avWsRetry = 0;
      // Subscribe to current agent
      if (avSelectedAgent) {
        avWs.send(JSON.stringify({ action: 'subscribe', agent_id: avSelectedAgent }));
      }
    };
    avWs.onmessage = function(evt) {
      try {
        var msg = JSON.parse(evt.data);
        if (msg.type === 'agent.status_changed' && msg.agent_id === avSelectedAgent) {
          avFetchAgentDetail().then(function() { avRenderIdentity(); avRenderRightNow(); });
        }
        if (msg.type === 'event.new' && avSelectedAgent) {
          // Light refresh on new events
          avFetchAgentDetail().then(function() { avRenderIdentity(); avRenderRightNow(); });
        }
      } catch (e) { /* ignore parse errors */ }
    };
    avWs.onclose = function() {
      avWs = null;
      avWsRetry++;
      if (avWsRetry <= 3) {
        avSetConnection(false, 'Reconnecting…');
        var delay = Math.min(1000 * Math.pow(2, avWsRetry), 16000);
        setTimeout(avConnectWs, delay);
      } else {
        avSetConnection(false, 'Polling');
      }
    };
    avWs.onerror = function() { /* onclose handles it */ };
  } catch (e) {
    console.warn('WebSocket error:', e);
  }
}

// ═══════════════════════════════════════════════════════
//  PERIODIC REFRESH
// ═══════════════════════════════════════════════════════

// Refresh identity + right now every 15s
setInterval(function() {
  if (!avSelectedAgent) return;
  Promise.all([avFetchAgentDetail(), avFetchPipeline()]).then(function() {
    avRenderIdentity();
    avRenderRightNow();
    avRenderPipeline();
  });
}, 15000);

// Refresh performance + LLM + tasks every 30s
setInterval(function() {
  if (!avSelectedAgent) return;
  Promise.all([avFetchMetrics(), avFetchCost(), avFetchTasks()]).then(function() {
    avRenderPerformance();
    avRenderTasks();
    avRenderAttention();
  });
}, 30000);

// Refresh insights every 60s
setInterval(function() {
  if (!avSelectedAgent) return;
  Promise.all([avFetchPromptInsights(), avFetchModelInsights(), avFetchErrorInsights(), avFetchLlmCalls()]).then(function() {
    avRenderLlmIntelligence();
  });
}, 60000);

// ═══════════════════════════════════════════════════════
//  URL PARAMS
// ═══════════════════════════════════════════════════════

function avCheckUrlParams() {
  var params = new URLSearchParams(window.location.search);
  if (params.has('agent')) {
    avSelectedAgent = params.get('agent');
    // Set the selector once agents load
    return avSelectedAgent;
  }
  return null;
}

// ═══════════════════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════════════════

(async function avInit() {
  avSetConnection(false, 'Loading…');

  var preselect = avCheckUrlParams();

  await avFetchAgents();

  // If agent was in URL, select it
  if (preselect) {
    var sel = document.getElementById('agentSelector');
    sel.value = preselect;
    avSelectedAgent = preselect;
    document.getElementById('avEmptyState').style.display = 'none';
    document.getElementById('avContent').style.display = 'block';
    await avLoadAll();
  }

  avSetConnection(true, 'Connected');
  document.getElementById('workspaceBadge').textContent = document.getElementById('envSelector').value;

  avConnectWs();
})();

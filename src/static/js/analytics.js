/* ═══════════════════════════════════════════════════════════
   HiveBoard — Analytics Deep Dive  (Production)
   Wired to Insights Engine API /v1/insights/*
   ═══════════════════════════════════════════════════════════ */

(function () {
'use strict';

// ── Configuration ────────────────────────────────────

var RANGE = '24h';                 // current range, synced with <select>
var REFRESH_MS = 60000;            // auto-refresh interval
var _refreshTimer = null;
var _tickTimer = null;
var _lastFetch = null;

// palette matches CSS --chart-1 … --chart-8
var C = ['#c2410c','#2563eb','#7c3aed','#16a34a','#d97706','#0891b2','#db2777','#4f46e5'];

var RANGE_HOURS = {'1h':1,'6h':6,'24h':24,'7d':168,'30d':720,'90d':2160};

// ── Cached API responses ─────────────────────────────

var _agents  = [];   // GET /v1/agents → .data
var _ins     = null; // GET /v1/insights/agents
var _errors  = null; // GET /v1/insights/errors
var _prompts = null; // GET /v1/insights/prompts
var _actions = null; // GET /v1/insights/actions
var _tsTasks = null; // GET /v1/insights/timeseries?metric=tasks

// ── Helpers ──────────────────────────────────────────

function esc(s)   { return s == null ? '' : String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function $(id)    { return document.getElementById(id); }
function fmt$(v)  { return v == null || isNaN(v) ? '$0.0000' : '$'+v.toFixed(4); }
function fmtN(n)  { return n == null ? '0' : Number(n).toLocaleString(); }
function pct(p,t) { return t ? ((p/t)*100).toFixed(1) : '0.0'; }
function hrs()    { return RANGE_HOURS[RANGE] || 24; }

function sortDesc(obj) {
    return Object.entries(obj||{}).sort(function(a,b){return b[1]-a[1];});
}

function hbSvg(color) {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="'+color+'" stroke-width="2.5"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>';
}

function friendlyAge(sec) {
    if (sec == null) return '—';
    if (sec < 60)    return sec + 's ago';
    if (sec < 3600)  return Math.floor(sec/60)+'m ago';
    var h = Math.floor(sec/3600), m = Math.floor((sec%3600)/60);
    if (sec < 86400) return h+'h '+m+'m ago';
    return Math.floor(sec/86400)+'d '+h%24+'h ago';
}

function hhmmssAgo(ts) {
    if (!ts) return '—';
    var d = Math.max(0,Math.floor((Date.now()-new Date(ts).getTime())/1000));
    var h=Math.floor(d/3600), m=Math.floor((d%3600)/60), s=d%60;
    return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')+' ago';
}

function stateHtml(cls,msg) {
    return '<div class="ad-state '+cls+'">'+esc(msg)+'</div>';
}

// ── API layer ────────────────────────────────────────

function api(path, params) {
    var base = (typeof CONFIG !== 'undefined' && CONFIG.endpoint) ? CONFIG.endpoint : '';
    var url = base + '/v1/' + path;
    var qs = [];
    if (params) Object.keys(params).forEach(function(k){
        if (params[k]!=null) qs.push(encodeURIComponent(k)+'='+encodeURIComponent(params[k]));
    });
    if (qs.length) url += '?' + qs.join('&');
    var apiKey = (typeof CONFIG !== 'undefined' && CONFIG.accessId) ? CONFIG.accessId
        : (window.HB_ACCESS_ID || localStorage.getItem('hiveboard_access_id') || '');
    return fetch(url, {
        headers: {
            'Authorization': 'Bearer ' + apiKey,
            'Content-Type': 'application/json'
        }
    }).then(function(r){
        if (!r.ok) throw new Error('HTTP '+r.status);
        return r.json();
    });
}

// ── Fetch all data ───────────────────────────────────

function fetchAll() {
    // Show loading states
    ['renderFleet','renderCost','renderActivity','renderErrors','renderPrompts','renderActions']
        .forEach(function(id){ var el=$(id); if(el) el.innerHTML=stateHtml('ad-state--loading','Loading…'); });

    var r = RANGE;
    return Promise.all([
        api('agents').catch(function(){ return {data:[]}; }),
        api('insights/agents',  {range:r, sort:'cost'}).catch(function(){ return null; }),
        api('insights/errors',  {range:r}).catch(function(){ return null; }),
        api('insights/prompts', {range:r, sort:'tokens'}).catch(function(){ return null; }),
        api('insights/actions', {range:r}).catch(function(){ return null; }),
        api('insights/timeseries', {range:r, metric:'tasks'}).catch(function(){ return null; })
    ]).then(function(res){
        _agents  = (res[0]&&res[0].data) || [];
        _ins     = res[1];
        _errors  = res[2];
        _prompts = res[3];
        _actions = res[4];
        _tsTasks = res[5];
        _lastFetch = new Date();
        renderAll();
    }).catch(function(err){
        console.error('Analytics fetchAll error:', err);
    });
}

function renderAll() {
    renderFleet();
    renderCost();
    renderActivity();
    renderErrors();
    renderPrompts();
    renderActions();
}

// ── Section toggle (kept from mockup JS) ─────────────

window.toggleSec = function(idx) {
    var chev = $('chev'+idx), body = $('secBody'+idx);
    if (!chev || !body) return;
    var hdr = body.previousElementSibling;
    var open = body.classList.contains('expanded');
    body.classList.toggle('expanded',!open);
    chev.classList.toggle('expanded',!open);
    if (hdr) hdr.classList.toggle('expanded',!open);
};

// ── Drilldown toggle ─────────────────────────────────

window.toggleDrilldown = function(id) {
    document.querySelectorAll('.drilldown-content').forEach(function(el){el.classList.remove('visible');});
    document.querySelectorAll('.drilldown-toggle').forEach(function(el){el.classList.remove('active');});
    var t = $('dd-'+id); if(t) t.classList.add('visible');
    if(event&&event.target) event.target.classList.add('active');
};

// ── Last-updated ticker ──────────────────────────────

function tickUpdated() {
    var el = $('adLastUpdated');
    if (!el||!_lastFetch) return;
    el.textContent = Math.floor((Date.now()-_lastFetch.getTime())/1000)+'s ago';
}

// ═══════════════════════════════════════════════════════
//  S0 — FLEET STATUS
// ═══════════════════════════════════════════════════════

function renderFleet() {
    var el = $('renderFleet');
    if (!_agents.length) { el.innerHTML = stateHtml('','No agents registered.'); return; }

    // build cost + task maps from insights/agents
    var costOf={}, tasksOf={};
    if (_ins&&_ins.agents) _ins.agents.forEach(function(a){
        costOf[a.agent_id]  = a.llm_cost||0;
        tasksOf[a.agent_id] = (a.tasks_completed||0)+(a.tasks_failed||0);
    });

    // classify into groups
    var groups = {running:[], idle:[], stopped:[]};
    _agents.forEach(function(ag){
        var s = ag.derived_status||'idle';
        if (s==='processing')                                     groups.running.push(ag);
        else if (s==='error'||s==='stuck')                        groups.stopped.push(ag);
        else if (ag.heartbeat_age_seconds>(ag.stuck_threshold_seconds||300)) groups.stopped.push(ag);
        else                                                      groups.idle.push(ag);
    });
    var total = _agents.length;
    var ordered = groups.running.concat(groups.idle, groups.stopped);
    var totalCost = (_ins&&_ins.fleet_totals) ? _ins.fleet_totals.total_cost : 0;

    var h = '';

    // ── status strip ──
    h += '<div class="fleet-status-strip mb-16">';
    if(groups.running.length) h+='<div class="fleet-seg fleet-seg--running" style="width:'+pct(groups.running.length,total)+'%;" title="'+groups.running.length+' running">'+groups.running.length+' Running</div>';
    if(groups.idle.length)    h+='<div class="fleet-seg fleet-seg--idle"    style="width:'+pct(groups.idle.length,total)   +'%;" title="'+groups.idle.length   +' idle">'   +groups.idle.length   +' Idle</div>';
    if(groups.stopped.length) h+='<div class="fleet-seg fleet-seg--stopped" style="width:'+pct(groups.stopped.length,total)+'%;" title="'+groups.stopped.length+' stopped">'+groups.stopped.length+' Stopped</div>';
    h += '</div>';

    // ── agent rows ──
    h += '<div class="status-grid">';
    ordered.forEach(function(ag){
        var s  = ag.derived_status||'idle';
        var grp = (s==='processing') ? 'running' : (s==='error'||s==='stuck') ? 'stopped' : 'idle';
        if (grp==='idle' && ag.heartbeat_age_seconds>(ag.stuck_threshold_seconds||300)) grp='stopped';

        var statusCfg = {
            running: {badge:'green', dot:'running', icon:'●', label:'Running', hbColor:'var(--success)'},
            idle:    {badge:'amber', dot:'idle',    icon:'◦', label:'Idle',    hbColor:'var(--warning)'},
            stopped: {badge:'red',   dot:'stopped', icon:'■', label:'Stopped', hbColor:'var(--error)'}
        }[grp];

        var cost  = costOf[ag.agent_id]||0;
        var tasks = tasksOf[ag.agent_id]||0;
        var cpt   = tasks>0 ? cost/tasks : 0;
        var costColor = cost>20?'var(--error)':cost>5?'var(--warning)':grp==='stopped'?'var(--success)':'var(--text-secondary)';
        var evtStyle  = ag.last_event_type==='agent_error' ? ' style="color:var(--error);"' : '';
        var hbStyle   = grp==='stopped' ? ' style="color:var(--error);font-weight:600;"' : '';

        h += '<div class="status-row status-row--'+grp+'">';
        h += '<div class="status-indicator"><div class="heartbeat-dot heartbeat-dot--'+statusCfg.dot+'"></div></div>';
        h += '<div class="status-name">'+esc(ag.agent_id)+'</div>';
        h += '<div class="status-state"><span class="a-badge a-badge--'+statusCfg.badge+'">'+statusCfg.icon+' '+statusCfg.label+'</span></div>';
        h += '<div class="status-heartbeat mono">'+hbSvg(statusCfg.hbColor)+' <span'+hbStyle+'>'+friendlyAge(ag.heartbeat_age_seconds)+'</span></div>';
        h += '<div class="status-last-event">';
        h += '<span class="text-xs muted">Last event:</span>';
        h += '<span class="mono text-xs"'+evtStyle+'>'+esc(ag.last_event_type||'—')+'</span>';
        h += '<span class="mono text-xs muted">'+hhmmssAgo(ag.last_event_at)+'</span>';
        h += '</div>';
        h += '<div class="status-cost"><span class="mono" style="font-weight:700;color:'+costColor+';">'+fmt$(cost)+'</span><span class="text-xs muted">/'+RANGE+'</span></div>';
        h += '<div class="status-cpt"><span class="mono text-xs">'+fmt$(cpt)+'/task</span></div>';
        h += '</div>';
    });
    h += '</div>';

    // ── cost-by-status summary ──
    h += '<div class="subsection-label mt-16">Cost by Status</div>';
    h += '<div class="a-grid-3">';
    [{key:'running',color:'var(--success)'},{key:'idle',color:'var(--warning)'},{key:'stopped',color:'var(--error)'}].forEach(function(g){
        var list = groups[g.key];
        var grpCost=0, grpTasks=0;
        list.forEach(function(ag){ grpCost+=costOf[ag.agent_id]||0; grpTasks+=tasksOf[ag.agent_id]||0; });
        var avgPerAgent = list.length ? grpCost/list.length : 0;
        var avgCpt      = grpTasks  ? grpCost/grpTasks     : 0;
        h += '<div class="a-card" style="border-left:3px solid '+g.color+';">';
        h += '<div class="a-card-header"><span class="a-card-label">'+g.key.charAt(0).toUpperCase()+g.key.slice(1)+' ('+list.length+' agent'+(list.length!==1?'s':'')+')</span></div>';
        h += '<div class="a-card-body">';
        h += '<div class="a-metric-row"><span class="a-big-number">'+fmt$(grpCost)+'</span><span class="text-xs muted">'+pct(grpCost,totalCost)+'%</span></div>';
        h += '<div class="text-xs muted mt-4">Avg '+fmt$(avgPerAgent)+'/agent · '+fmt$(avgCpt)+'/task avg</div>';
        h += '</div></div>';
    });
    h += '</div>';

    // ── commentary ──
    var stoppedNames = groups.stopped.map(function(a){return a.agent_id;});
    if (stoppedNames.length || groups.running.length) {
        h += '<div class="commentary-box mt-12"><strong>&#128994; HiveMind Analysis:</strong> ';
        h += '<span class="highlight">'+groups.running.length+' of '+total+' agents</span> are actively running, accounting for ';
        var runCost=0; groups.running.forEach(function(a){runCost+=costOf[a.agent_id]||0;});
        h += '<span class="highlight">'+pct(runCost,totalCost)+'%</span> of total fleet spend.';
        if (stoppedNames.length) {
            h += ' <span class="highlight">'+stoppedNames.join(', ')+'</span> '+(stoppedNames.length===1?'is':'are')+' stopped.';
        }
        h += '</div>';
    }

    el.innerHTML = h;
}

// ═══════════════════════════════════════════════════════
//  S1 — COST RANKINGS
// ═══════════════════════════════════════════════════════

function renderCost() {
    var el = $('renderCost');
    if (!_ins||!_ins.agents||!_ins.agents.length) { el.innerHTML=stateHtml('','No cost data for this range.'); return; }

    var agents = _ins.agents;                         // already sorted by cost desc
    var fleet  = _ins.fleet_totals;
    var comp   = (_ins.comparisons&&_ins.comparisons.cost)||{};
    var top    = agents[0], bot = agents[agents.length-1];
    var avg    = fleet.total_cost / agents.length;
    var ratio  = comp.max_vs_min || (bot.llm_cost>0 ? top.llm_cost/bot.llm_cost : 0);
    var maxVal = top.llm_cost || 1;

    var h = '';

    // ── KPI cards ──
    h += '<div class="a-grid-4 mb-16">';
    h += kpiCard('Most Expensive', '<span class="rank-circle rank-1">1</span>',
        fmt$(top.llm_cost), '', top.agent_id, 'var(--error)',
        fmtN(top.llm_call_count)+' LLM calls · '+fmtN(top.llm_tokens_in+top.llm_tokens_out)+' tokens');
    h += kpiCard('Least Expensive', '<span class="rank-circle rank-low">'+agents.length+'</span>',
        fmt$(bot.llm_cost), '', bot.agent_id, 'var(--success)',
        fmtN(bot.llm_call_count)+' LLM calls · '+fmtN(bot.llm_tokens_in+bot.llm_tokens_out)+' tokens');
    h += kpiCard('Fleet Average', '',
        fmt$(avg), '', '', '',
        'Across '+agents.length+' agents · '+fmt$(fleet.total_cost)+' total');
    h += kpiCard('Cost Spread', '',
        ratio.toFixed(1)+'×', 'max/min ratio', '', '',
        'Max vs Avg: '+(comp.max_vs_avg||0).toFixed(1)+'×', true);
    h += '</div>';

    // ── distribution strip ──
    h += '<div class="subsection-label">Cost Distribution by Agent</div>';
    h += '<div class="distribution-strip mb-8">';
    agents.forEach(function(a,i){
        var p = pct(a.llm_cost, fleet.total_cost);
        var lbl = a.agent_id.length>10 ? a.agent_id.substring(0,8)+'..' : a.agent_id;
        h += '<div class="segment" style="width:'+p+'%;background:'+C[i%C.length]+';" title="'+esc(a.agent_id)+': '+fmt$(a.llm_cost)+' ('+p+'%)">';
        h += parseFloat(p)>8 ? esc(lbl)+' '+p+'%' : '';
        h += '</div>';
    });
    h += '</div>';

    // ── ranked bars ──
    agents.forEach(function(a,i){
        h += barRow(a.agent_id, fmt$(a.llm_cost), a.llm_cost/maxVal*100, pct(a.llm_cost,fleet.total_cost)+'%', C[i%C.length]);
    });

    // ── commentary ──
    var topModel = (top.top_models&&top.top_models[0]) ? top.top_models[0].model : 'unknown';
    h += '<div class="commentary-box"><strong>&#9881; HiveMind Analysis:</strong> ';
    h += '<span class="highlight">'+esc(top.agent_id)+'</span> is <span class="highlight">'+(comp.max_vs_avg||0).toFixed(1)+'×</span> more expensive than the fleet average';
    h += ' and <span class="highlight">'+ratio.toFixed(1)+'×</span> more expensive than <span class="highlight">'+esc(bot.agent_id)+'</span>.';
    h += ' It accounts for <span class="highlight">'+pct(top.llm_cost,fleet.total_cost)+'%</span> of total fleet spend.';
    h += ' Primary model: <span class="highlight">'+esc(topModel)+'</span>.';
    h += '</div>';

    el.innerHTML = h;
}

// ═══════════════════════════════════════════════════════
//  S2 — ACTIVITY RANKINGS
// ═══════════════════════════════════════════════════════

function renderActivity() {
    var el = $('renderActivity');
    if (!_ins||!_ins.agents||!_ins.agents.length) { el.innerHTML=stateHtml('','No activity data for this range.'); return; }

    var agents = _ins.agents.slice().sort(function(a,b){return (b.tasks_completed||0)-(a.tasks_completed||0);});
    var fleet  = _ins.fleet_totals;
    var top    = agents[0], bot = agents[agents.length-1];
    var maxT   = top.tasks_completed||1;
    var peakHr = (_tsTasks&&_tsTasks.summary) ? _tsTasks.summary.peak_hour : null;
    var peakLbl= peakHr ? new Date(peakHr).getUTCHours()+':00 UTC' : '—';
    var h = '';

    // ── KPI cards ──
    h += '<div class="a-grid-3 mb-16">';
    h += kpiCard('Most Active Agent','<span class="rank-circle rank-1">1</span>',
        fmtN(top.tasks_completed),'tasks completed', top.agent_id,'var(--active)',
        (top.tasks_completed/hrs()).toFixed(1)+' tasks/hr avg · '+(top.success_rate!=null?top.success_rate.toFixed(1):'—')+'% success');
    h += kpiCard('Least Active','<span class="rank-circle rank-low">'+agents.length+'</span>',
        fmtN(bot.tasks_completed),'tasks completed', bot.agent_id,'var(--idle)',
        (bot.tasks_completed/hrs()).toFixed(1)+' tasks/hr · '+(bot.success_rate!=null?bot.success_rate.toFixed(1):'—')+'% success');
    h += kpiCard('Fleet Total','',
        fmtN(fleet.total_tasks),'tasks · '+RANGE,'','',
        'Avg per agent: '+fmtN(Math.round(fleet.total_tasks/agents.length))+' · Peak hour: '+peakLbl);
    h += '</div>';

    // ── ranked bars ──
    h += '<div class="subsection-label">Tasks Completed by Agent</div>';
    agents.forEach(function(a,i){
        h += barRow(a.agent_id, fmtN(a.tasks_completed), (a.tasks_completed||0)/maxT*100, pct(a.tasks_completed,fleet.total_tasks)+'%', C[i%C.length]);
    });

    // ── drilldown for top agent ──
    h += '<div class="mt-16 flex-between">';
    h += '<div class="subsection-label" style="margin:0;border:0;padding:0;">Drilldown: '+esc(top.agent_id)+'</div>';
    h += '<button class="drilldown-toggle active" onclick="toggleDrilldown(\'actTask\')">By Task</button>';
    h += '<button class="drilldown-toggle" onclick="toggleDrilldown(\'actTool\')" style="margin-left:6px;">By Action</button>';
    h += '</div>';

    // by task type
    h += '<div class="drilldown-content visible" id="dd-actTask"><div class="a-grid-2 mt-8"><div>';
    var ttEntries = Object.entries(top.tasks_by_type||{}).sort(function(a,b){return (b[1].completed||0)-(a[1].completed||0);});
    var ttMax = ttEntries.length ? (ttEntries[0][1].completed||1) : 1;
    ttEntries.slice(0,6).forEach(function(e,i){
        h += barRow(e[0], e[1].completed||0, (e[1].completed||0)/ttMax*100, '', C[i%C.length]);
    });
    if(!ttEntries.length) h += stateHtml('','No task type data.');
    h += '</div>';
    // insight card
    if (ttEntries.length>=2) {
        h += '<div class="a-card" style="display:flex;flex-direction:column;justify-content:center;"><div class="commentary-box" style="margin:0;">';
        h += '<strong>&#128270; Insight:</strong> <span class="highlight">'+esc(ttEntries[0][0])+'</span> is the dominant task type at '+pct(ttEntries[0][1].completed,top.tasks_completed)+'%.';
        h += '</div></div>';
    }
    h += '</div></div>';

    // by action (top_actions)
    h += '<div class="drilldown-content" id="dd-actTool"><div class="a-grid-2 mt-8"><div>';
    var acts = top.top_actions||[];
    var actMax = acts.length ? (acts[0].completed||acts[0].started||1) : 1;
    acts.slice(0,6).forEach(function(a,i){
        var v = a.completed||a.started||0;
        h += barRow(a.name, v, v/actMax*100, '', C[i%C.length]);
    });
    if(!acts.length) h += stateHtml('','No action data.');
    h += '</div>';
    if(acts.length>=2){
        h += '<div class="a-card" style="display:flex;flex-direction:column;justify-content:center;"><div class="commentary-box" style="margin:0;">';
        h += '<strong>&#128295; Insight:</strong> <span class="highlight">'+esc(acts[0].name)+'</span> is the most-called action by this agent.';
        h += '</div></div>';
    }
    h += '</div></div>';

    el.innerHTML = h;
}

// ═══════════════════════════════════════════════════════
//  S3 — ERROR ANALYSIS
// ═══════════════════════════════════════════════════════

function renderErrors() {
    var el = $('renderErrors');
    if (!_errors) { el.innerHTML=stateHtml('','No error data for this range.'); return; }

    var byAgent     = _errors.by_agent||[];
    var totalErrors = _errors.total_errors||0;
    var totalTasks  = (_ins&&_ins.fleet_totals) ? _ins.fleet_totals.total_tasks : 0;
    var fleetRate   = totalTasks ? (totalErrors/totalTasks*100) : 0;
    var topType     = sortDesc(_errors.by_type_global);
    var topTypeName = topType.length ? topType[0][0] : null;
    var topTypeVal  = topType.length ? topType[0][1] : 0;
    var top         = byAgent.length ? byAgent[0] : null;
    var bot         = byAgent.length ? byAgent[byAgent.length-1] : null;
    var maxErr      = top ? top.error_count : 1;

    var h = '';

    // ── KPI cards ──
    h += '<div class="a-grid-4 mb-16">';

    // most errors
    h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">Most Errors</span><span class="rank-circle rank-1">!</span></div><div class="a-card-body">';
    if (top && top.error_count>0) {
        var agTasks=0;
        if(_ins&&_ins.agents){ var f=_ins.agents.find(function(a){return a.agent_id===top.agent_id;}); if(f) agTasks=(f.tasks_completed||0)+(f.tasks_failed||0); }
        h += '<div class="a-metric-row"><span class="a-big-number" style="color:var(--error);">'+top.error_count+'</span><span class="text-xs muted">errors in '+RANGE+'</span></div>';
        h += '<div class="mono text-xs mt-4" style="color:var(--error);font-weight:600;">'+esc(top.agent_id)+'</div>';
        h += '<div class="text-xs muted mt-4">'+(agTasks?((top.error_count/agTasks*100).toFixed(1)+'% error rate'):'')+'</div>';
    } else {
        h += '<div class="a-metric-row"><span class="a-big-number" style="color:var(--success);">0</span><span class="text-xs muted">errors</span></div>';
        h += '<div class="text-xs muted mt-4">All clear!</div>';
    }
    h += '</div></div>';

    // fewest
    h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">Fewest Errors</span></div><div class="a-card-body">';
    if(bot){ h+='<div class="a-metric-row"><span class="a-big-number" style="color:var(--success);">'+(bot.error_count||0)+'</span><span class="text-xs muted">errors</span></div>';
             h+='<div class="mono text-xs mt-4" style="color:var(--success);font-weight:600;">'+esc(bot.agent_id)+'</div>'; }
    h += '</div></div>';

    // fleet rate
    h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">Fleet Error Rate</span></div><div class="a-card-body">';
    h += '<div class="a-metric-row"><span class="a-big-number small">'+fleetRate.toFixed(1)+'%</span></div>';
    h += '<div class="text-xs muted mt-4">'+totalErrors+' errors / '+fmtN(totalTasks)+' tasks total</div>';
    h += '</div></div>';

    // top type
    h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">Top Error Type</span></div><div class="a-card-body">';
    if(topTypeName){
        h += '<div class="mono text-sm" style="font-weight:700;color:var(--error);">'+esc(topTypeName)+'</div>';
        h += '<div class="text-xs muted mt-4">'+topTypeVal+' occurrences · '+pct(topTypeVal,totalErrors)+'% of all errors</div>';
    } else { h += '<div class="text-xs muted">No errors recorded</div>'; }
    h += '</div></div>';

    h += '</div>';

    // ── ranked bars ──
    h += '<div class="subsection-label">Errors by Agent</div>';
    byAgent.forEach(function(a,i){
        var op = Math.max(0.15, 1-i*0.15);
        h += '<div class="a-bar-row"><span class="a-bar-label">'+esc(a.agent_id)+'</span>';
        h += '<div class="a-bar-track"><div class="a-bar-fill" style="width:'+(maxErr?(a.error_count/maxErr*100).toFixed(1):0)+'%;background:rgba(220,38,38,'+op.toFixed(2)+');">'+(a.error_count||'')+'</div></div>';
        h += '<span class="a-bar-value">'+pct(a.error_count,totalErrors)+'%</span></div>';
    });

    // ── drilldown for worst agent ──
    if (top && top.error_count>0) {
        h += '<div class="subsection-label mt-16">Drilldown: '+esc(top.agent_id)+' — '+top.error_count+' Errors</div>';
        h += '<div class="a-grid-3">';

        var typeColors = ['var(--error)','var(--warning)','var(--llm)','var(--idle)','var(--active)'];

        // by error type
        h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">By Error Type</span></div><div class="a-card-body">';
        var te = sortDesc(top.by_type); var teMax = te.length?te[0][1]:1;
        te.slice(0,5).forEach(function(e,i){ h += miniBar(e[0],e[1],e[1]/teMax*100,typeColors[i%typeColors.length]); });
        if(!te.length) h+='<div class="text-xs muted">—</div>';
        h += '</div></div>';

        // by task type
        h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">By Task</span></div><div class="a-card-body">';
        var tt = sortDesc(top.by_task_type); var ttMax=tt.length?tt[0][1]:1;
        tt.slice(0,5).forEach(function(e,i){ h += miniBar(e[0],e[1],e[1]/ttMax*100,C[i%C.length]); });
        if(!tt.length) h+='<div class="text-xs muted">No task breakdown available</div>';
        h += '</div></div>';

        // by action/tool
        h += '<div class="a-card"><div class="a-card-header"><span class="a-card-label">By Tool</span></div><div class="a-card-body">';
        var ta = sortDesc(top.by_action); var taMax=ta.length?ta[0][1]:1;
        ta.slice(0,5).forEach(function(e,i){ h += miniBar(e[0],e[1],e[1]/taMax*100,C[(i+4)%C.length]); });
        if(!ta.length) h+='<div class="text-xs muted">No action breakdown available</div>';
        h += '</div></div>';

        h += '</div>';

        // commentary
        h += '<div class="commentary-box"><strong>&#9888; HiveMind Analysis:</strong> ';
        h += '<span class="highlight">'+esc(top.agent_id)+'</span> accounts for <span class="highlight">'+pct(top.error_count,totalErrors)+'%</span> of all errors.';
        if(topTypeName) h += ' The dominant error is <span class="highlight">'+esc(topTypeName)+'</span> ('+pct(topTypeVal,totalErrors)+'%)';
        if(tt.length) h += ', concentrated in the <span class="highlight">'+esc(tt[0][0])+'</span> task';
        if(ta.length) h += ' using the <span class="highlight">'+esc(ta[0][0])+'</span> tool';
        h += '.';
        h += '</div>';
    }

    el.innerHTML = h;
}

// ═══════════════════════════════════════════════════════
//  S4 — PROMPT ANALYSIS
// ═══════════════════════════════════════════════════════

function renderPrompts() {
    var el = $('renderPrompts');
    if (!_prompts||!_prompts.calls||!_prompts.calls.length) { el.innerHTML=stateHtml('','No prompt data for this range.'); return; }

    var calls   = _prompts.calls;
    var biggest = _prompts.biggest_prompt;

    var h = '';
    h += '<div class="subsection-label">Top Prompts by Token Size</div>';
    h += '<table class="a-table"><thead><tr>';
    h += '<th style="width:40px;">#</th><th>Prompt / Call Name</th><th style="text-align:right;">Avg Tokens</th>';
    h += '<th style="text-align:right;">Calls</th><th>Agent(s)</th><th>Model</th><th style="text-align:right;">Est. Cost</th>';
    h += '</tr></thead><tbody>';

    calls.forEach(function(c,i){
        var rc = i===0?'rank-1':i===1?'rank-2':i===2?'rank-3':'rank-low';
        var cc = c.total_cost>1?'color:var(--error);':c.total_cost>0.5?'color:var(--warning);':'color:var(--success);';
        h += '<tr>';
        h += '<td><span class="rank-circle '+rc+'" style="width:20px;height:20px;font-size:10px;">'+(i+1)+'</span></td>';
        h += '<td style="font-weight:600;color:var(--text-primary);">'+esc(c.name)+'</td>';
        h += '<td style="text-align:right;font-weight:700;">'+fmtN(c.avg_tokens_in)+'</td>';
        h += '<td style="text-align:right;">'+fmtN(c.total_count)+'</td>';
        h += '<td>';
        (c.agents_using||[]).forEach(function(aid){ h+='<span class="a-badge a-badge--blue">'+esc(aid)+'</span> '; });
        h += '</td>';
        h += '<td class="text-xs">'+esc(c.primary_model||'—')+'</td>';
        h += '<td style="text-align:right;font-weight:700;'+cc+'">'+fmt$(c.total_cost)+'</td>';
        h += '</tr>';
    });
    h += '</tbody></table>';

    // commentary
    if (calls.length>=2) {
        var topByCost = calls.slice().sort(function(a,b){return (b.total_cost||0)-(a.total_cost||0);});
        var tc = topByCost[0];
        h += '<div class="commentary-box"><strong>&#128203; HiveMind Analysis:</strong> ';
        h += 'The <span class="highlight">'+esc(tc.name)+'</span> call is the highest total cost driver at <span class="highlight">'+fmt$(tc.total_cost)+'</span>';
        h += ' ('+fmtN(tc.total_count)+' calls × '+fmtN(tc.avg_tokens_in)+' tokens avg).';
        if(biggest) h+=' The largest single prompt belongs to <span class="highlight">'+esc(biggest.name)+'</span> at <span class="highlight">'+fmtN(biggest.max_tokens_in)+'</span> tokens.';
        h += '</div>';
    }

    el.innerHTML = h;
}

// ═══════════════════════════════════════════════════════
//  S5 — TOOL & ACTION USAGE
// ═══════════════════════════════════════════════════════

function renderActions() {
    var el = $('renderActions');
    if (!_actions||!_actions.actions||!_actions.actions.length) { el.innerHTML=stateHtml('','No action data for this range.'); return; }

    var actions = _actions.actions;
    var h = '';

    // ── summary pills ──
    h += '<div class="subsection-label">Action Usage Summary ('+RANGE+')</div>';
    h += '<div class="mb-16" style="display:flex;flex-wrap:wrap;gap:4px;">';
    actions.forEach(function(a){
        var rate = a.hourly_avg!=null ? a.hourly_avg.toFixed(1) : (a.total_started/hrs()).toFixed(1);
        h += '<div class="usage-pill"><span class="count">'+fmtN(a.total_started)+'</span>';
        h += '<span class="label">'+esc(a.name)+'</span>';
        h += '<span class="sub">'+rate+'/hr</span></div>';
    });
    h += '</div>';

    // ── detail table ──
    h += '<div class="subsection-label">Action Detail</div>';
    h += '<table class="a-table mb-16"><thead><tr>';
    h += '<th>Action</th><th style="text-align:right;">Total</th><th>Used By</th>';
    h += '<th style="text-align:right;">Avg Duration</th><th style="text-align:right;">Success Rate</th><th>Peak Hour</th>';
    h += '</tr></thead><tbody>';
    actions.forEach(function(a){
        var srC = a.success_rate==null?'':a.success_rate>=95?'color:var(--success);':a.success_rate>=80?'color:var(--warning);':'color:var(--error);';
        var dur = a.avg_duration_ms!=null ? (a.avg_duration_ms/1000).toFixed(1)+'s' : '—';
        var pk  = a.peak_hour ? new Date(a.peak_hour).getUTCHours()+':00 UTC' : '—';
        h += '<tr>';
        h += '<td style="font-weight:600;color:var(--text-primary);">'+esc(a.name)+'</td>';
        h += '<td style="text-align:right;font-weight:700;">'+fmtN(a.total_started)+'</td>';
        h += '<td>';
        Object.keys(a.agents_using||{}).forEach(function(aid){ h+='<span class="a-badge a-badge--blue">'+esc(aid)+'</span> '; });
        h += '</td>';
        h += '<td style="text-align:right;">'+dur+'</td>';
        h += '<td style="text-align:right;"><span style="'+srC+'font-weight:600;">'+(a.success_rate!=null?a.success_rate.toFixed(1)+'%':'—')+'</span></td>';
        h += '<td><span class="a-badge a-badge--gray">'+pk+'</span></td>';
        h += '</tr>';
    });
    h += '</tbody></table>';

    // ── hourly heatmap ──
    var withBuckets = actions.filter(function(a){return a.hourly_buckets&&a.hourly_buckets.length;});
    if (withBuckets.length) {
        h += '<div class="subsection-label">Hourly Activity Heatmap</div>';
        h += '<div class="a-card a-card--full mb-16"><div class="a-card-body" id="adHeatmap"></div></div>';
    }

    // ── weekly aggregation (only for 7d+ ranges) ──
    if ((RANGE==='7d'||RANGE==='30d'||RANGE==='90d') && withBuckets.length) {
        h += '<div class="subsection-label">Weekly Aggregation</div>';
        h += buildWeeklyTable(actions);
    }

    // ── commentary ──
    var worstSR = actions.slice().filter(function(a){return a.success_rate!=null;}).sort(function(a,b){return (a.success_rate||100)-(b.success_rate||100);});
    var slowest = actions.slice().filter(function(a){return a.avg_duration_ms!=null;}).sort(function(a,b){return (b.avg_duration_ms||0)-(a.avg_duration_ms||0);});
    if (worstSR.length || slowest.length) {
        h += '<div class="commentary-box"><strong>&#128301; HiveMind Analysis:</strong> ';
        if (worstSR.length && worstSR[0].success_rate<95) {
            h += 'The <span class="highlight">'+esc(worstSR[0].name)+'</span> action has the lowest success rate at <span class="highlight">'+worstSR[0].success_rate.toFixed(1)+'%</span>';
            if(slowest.length&&slowest[0].name===worstSR[0].name) h+=' and is also the slowest at <span class="highlight">'+(slowest[0].avg_duration_ms/1000).toFixed(1)+'s</span> avg';
            h += '. ';
        }
        if (slowest.length && (!worstSR.length || slowest[0].name !== (worstSR[0]||{}).name)) {
            h += 'The slowest action is <span class="highlight">'+esc(slowest[0].name)+'</span> at <span class="highlight">'+(slowest[0].avg_duration_ms/1000).toFixed(1)+'s</span> avg. ';
        }
        h += 'The busiest action is <span class="highlight">'+esc(actions[0].name)+'</span> with <span class="highlight">'+fmtN(actions[0].total_started)+'</span> invocations.';
        h += '</div>';
    }

    el.innerHTML = h;

    // render heatmap after DOM insert
    if (withBuckets.length) buildHeatmap(withBuckets);
}

// ── Heatmap engine ───────────────────────────────────

function buildHeatmap(actions) {
    var tools=[], data=[];
    actions.slice(0,8).forEach(function(a){
        tools.push(a.name);
        var hourMap={};
        (a.hourly_buckets||[]).forEach(function(b){ var hh=new Date(b.hour).getUTCHours(); hourMap[hh]=(hourMap[hh]||0)+(b.started||0); });
        var row=[]; for(var i=0;i<24;i++) row.push(hourMap[i]||0);
        data.push(row);
    });
    renderHeatmap('adHeatmap', tools, data);
}

function heatColor(v,mx){
    if(!v) return 'var(--bg-hover)';
    var r=v/mx;
    if(r<0.25) return 'rgba(194,65,12,0.15)';
    if(r<0.5)  return 'rgba(194,65,12,0.35)';
    if(r<0.75) return 'rgba(194,65,12,0.6)';
    return 'rgba(194,65,12,0.9)';
}

function renderHeatmap(id, tools, data) {
    var c = $(id); if(!c) return;
    var maxVal=0;
    data.forEach(function(r){r.forEach(function(v){if(v>maxVal)maxVal=v;});});
    if(!maxVal) maxVal=1;

    var h = '<div class="heatmap-grid" style="grid-template-columns:100px repeat(24,1fr);">';
    h += '<div class="heatmap-row-label"></div>';
    for(var hh=0;hh<24;hh++) h += '<div class="heatmap-label">'+(hh<10?'0':'')+hh+'</div>';

    tools.forEach(function(tool,i){
        var lbl = tool.length>12 ? tool.substring(0,10)+'..' : tool;
        h += '<div class="heatmap-row-label">'+esc(lbl)+'</div>';
        for(var hh=0;hh<24;hh++){
            var v=data[i][hh], bg=heatColor(v,maxVal);
            var fg = (v/maxVal)>0.5 ? 'rgba(255,255,255,0.95)' : 'var(--text-muted)';
            h += '<div class="heatmap-cell" style="background:'+bg+';color:'+fg+';" title="'+esc(tool)+' @ '+(hh<10?'0':'')+hh+':00 — '+v+' starts">'+(v>0?v:'')+'</div>';
        }
    });
    h += '</div>';

    // legend
    h += '<div style="display:flex;align-items:center;gap:8px;margin-top:10px;justify-content:flex-end;">';
    h += '<span class="text-xs muted">Less</span>';
    ['rgba(194,65,12,0.15)','rgba(194,65,12,0.35)','rgba(194,65,12,0.6)','rgba(194,65,12,0.9)'].forEach(function(cl){
        h += '<div style="width:14px;height:14px;border-radius:2px;background:'+cl+';"></div>';
    });
    h += '<span class="text-xs muted">More</span></div>';

    c.innerHTML = h;
}

// ── Weekly table (client-side rollup) ────────────────

function buildWeeklyTable(actions) {
    var days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    var h = '<table class="a-table"><thead><tr><th>Action</th>';
    days.forEach(function(d){h+='<th style="text-align:right;">'+d+'</th>';});
    h += '<th style="text-align:right;">Total</th><th>Trend</th></tr></thead><tbody>';

    actions.slice(0,10).forEach(function(a){
        // JS getUTCDay: 0=Sun, need Mon=0 … Sun=6
        var dt=[0,0,0,0,0,0,0];
        (a.hourly_buckets||[]).forEach(function(b){
            var d=(new Date(b.hour).getUTCDay()+6)%7; // Mon=0
            dt[d]+=(b.started||0);
        });
        var tot = dt.reduce(function(s,v){return s+v;},0);
        // simple trend: compare first half vs second half
        var first = dt[0]+dt[1]+dt[2], second = dt[4]+dt[5]+dt[6];
        var trend = first===0 ? 0 : ((second-first)/first*100);
        var tBadge = trend>5?'a-badge--green':trend<-5?'a-badge--red':'a-badge--gray';
        var tLabel = Math.abs(trend)<1 ? '— flat' : (trend>0?'&#9650; ':'&#9660; ')+Math.abs(trend).toFixed(0)+'%';

        h += '<tr><td style="font-weight:600;color:var(--text-primary);">'+esc(a.name)+'</td>';
        dt.forEach(function(v){h+='<td style="text-align:right;">'+v+'</td>';});
        h += '<td style="text-align:right;font-weight:700;">'+tot+'</td>';
        h += '<td><span class="a-badge '+tBadge+'">'+tLabel+'</span></td></tr>';
    });

    h += '</tbody></table>';
    return h;
}

// ── Shared HTML builders ─────────────────────────────

function kpiCard(label, badge, bigNum, suffix, agentId, agentColor, detail, small) {
    var h = '<div class="a-card"><div class="a-card-header"><span class="a-card-label">'+label+'</span>'+(badge||'')+'</div>';
    h += '<div class="a-card-body">';
    h += '<div class="a-metric-row"><span class="a-big-number'+(small?' small':'')+'">'+bigNum+'</span>';
    if(suffix) h += '<span class="text-xs muted">'+suffix+'</span>';
    h += '</div>';
    if(agentId) h += '<div class="mono text-xs mt-4" style="color:'+(agentColor||'var(--text-secondary)')+';font-weight:600;">'+esc(agentId)+'</div>';
    if(detail)  h += '<div class="text-xs muted mt-4">'+detail+'</div>';
    h += '</div></div>';
    return h;
}

function barRow(label, display, widthPct, valueSuffix, color) {
    return '<div class="a-bar-row"><span class="a-bar-label">'+esc(label)+'</span>'
        +'<div class="a-bar-track"><div class="a-bar-fill" style="width:'+Math.max(0,Math.min(100,widthPct)).toFixed(1)+'%;background:'+(color||C[0])+';">'+display+'</div></div>'
        +(valueSuffix?'<span class="a-bar-value">'+valueSuffix+'</span>':'')
        +'</div>';
}

function miniBar(label, val, widthPct, color) {
    return '<div class="a-bar-row"><span class="a-bar-label" style="width:100px;">'+esc(label)+'</span>'
        +'<div class="a-bar-track"><div class="a-bar-fill" style="width:'+Math.max(0,Math.min(100,widthPct)).toFixed(1)+'%;background:'+(color||C[0])+';">'+val+'</div></div></div>';
}

// ── Initialisation ───────────────────────────────────

var rangeEl = $('adRange');
if (rangeEl) rangeEl.addEventListener('change', function(){ RANGE=this.value; fetchAll(); });

fetchAll();
_tickTimer = setInterval(tickUpdated, 5000);
_refreshTimer = setInterval(fetchAll, REFRESH_MS);

})();

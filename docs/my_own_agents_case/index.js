
// ====================================================================
// STATE
// ====================================================================
const state = {
    currentPanel: 'dashboard',
    currentSubPanel: {
        'agents': 'agents-list',
        'settings': 'settings-global-skills'
    },
    selectedAgent: 'main',
    agents: [],
    globalSkills: [],
    agentSkills: { global: [], private: [] },
    // Chat state
    chat: {
        agentId: null,
        skillId: null,
        sessionId: null,
        messages: []
    },
    // Feed state
    feed: {
        messages: [],
        filter: 'all',
        offset: 0,
        hasMore: false,
        unreadCount: 0
    },
    // WebSocket state
    wsConnected: false
};

// ====================================================================
// API HELPER
// ====================================================================

// Environment detection: localhost → local server, otherwise → production
const _isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
const API_BASE = _isLocal ? '' : 'https://mlbackend.net/loopcore';
console.log('[loopCore] Environment:', _isLocal ? 'local' : 'production', '| API_BASE:', API_BASE || '(relative)');

let serverConnected = false;

// Token management
function getToken() {
    return localStorage.getItem('loopcore_token');
}

function logout() {
    localStorage.removeItem('loopcore_token');
    localStorage.removeItem('loopcore_user');
    window.location.href = _isLocal ? '/static/login.html' : 'login.html';
}

async function api(method, path, data = null, options = {}) {
    const token = getToken();
    const headers = { 'Content-Type': 'application/json' };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const fetchOptions = {
        method,
        headers
    };
    if (data) {
        fetchOptions.body = JSON.stringify(data);
    }

    const silent = options.silent || false;
    const url = API_BASE + path;

    try {
        const response = await fetch(url, fetchOptions);
        const json = await response.json();

        if (!serverConnected) {
            serverConnected = true;
            updateServerStatus(true);
        }

        // Handle 401 Unauthorized - redirect to login
        if (response.status === 401) {
            logout();
            return null;
        }

        if (!response.ok) {
            if (!silent) {
                showToast(json.detail || json.error || 'Request failed', 'error');
            }
            return null;
        }
        return json;
    } catch (err) {
        if (serverConnected || serverConnected === false) {
            serverConnected = false;
            updateServerStatus(false);
        }
        if (!silent) {
            showToast('Network error: ' + err.message, 'error');
        }
        return null;
    }
}

function updateServerStatus(connected) {
    const el = document.getElementById('stat-server');
    if (connected) {
        el.textContent = 'CONNECTED';
        el.className = 'stat-value ok';
    } else {
        el.textContent = 'DISCONNECTED';
        el.className = 'stat-value error';
    }
    updateHealthBarColor();
}

function updateHealthBarColor() {
    const healthBar = document.getElementById('health-bar');
    const serverOk = serverConnected;
    const llmEl = document.getElementById('stat-llm');
    const llmOk = llmEl.classList.contains('ok');
    const schedEl = document.getElementById('stat-scheduler');
    const schedOk = schedEl.classList.contains('ok');
    const rtEl = document.getElementById('stat-runtime');
    const rtOk = rtEl.classList.contains('ok');

    if (!serverOk) {
        healthBar.style.borderLeftColor = '#dc2626';
    } else if (!llmOk) {
        healthBar.style.borderLeftColor = '#ca8a04';
    } else if (!schedOk && lastSchedulerStatus !== 'stopped') {
        healthBar.style.borderLeftColor = '#ca8a04';
    } else if (!rtOk && lastRuntimeStatus !== null && lastRuntimeStatus !== 'stopped') {
        healthBar.style.borderLeftColor = '#ca8a04';
    } else {
        healthBar.style.borderLeftColor = '#16a34a';
    }
}

// ====================================================================
// MAIN NAVIGATION
// ====================================================================
function navigateTo(panelName) {
    document.querySelectorAll('.main-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.panel === panelName);
    });
    document.querySelectorAll('.panel').forEach(panel => {
        panel.classList.toggle('active', panel.id === `panel-${panelName}`);
    });
    state.currentPanel = panelName;

    if (panelName === 'dashboard') {
        loadDashboard();
        scheduleNextRefresh();
    } else {
        stopSchedulerRefresh();
    }
    if (panelName !== 'agents') stopRuntimeAutoRefresh();

    if (panelName === 'agents') {
        loadAgentSelector();
        navigateToSubPanel('agents', state.currentSubPanel['agents'] || 'agents-list');
    } else if (panelName === 'settings') {
        navigateToSubPanel('settings', state.currentSubPanel['settings'] || 'settings-global-skills');
    } else if (panelName === 'chat') {
        loadChatAgents();
    } else if (panelName === 'feed') {
        loadFeed();
    } else if (panelName === 'usage') {
        loadUsage();
    }
}

function navigateToSubPanel(panelName, subPanelName) {
    const panel = document.getElementById(`panel-${panelName}`);
    panel.querySelectorAll('.sub-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.subpanel === subPanelName);
    });
    panel.querySelectorAll('.sub-panel').forEach(sp => {
        sp.classList.toggle('active', sp.id === `subpanel-${subPanelName}`);
    });
    state.currentSubPanel[panelName] = subPanelName;

    // Stop runtime auto-refresh when leaving the runtime tab
    if (subPanelName !== 'agents-runtime') stopRuntimeAutoRefresh();

    // Show/hide agent selector based on sub-panel
    const agentSelector = document.getElementById('agent-selector');
    if (panelName === 'agents') {
        agentSelector.style.display = (subPanelName === 'agents-list') ? 'none' : 'flex';
        if (subPanelName !== 'agents-list') updateHeartbeatButton();
    }

    // Load data for sub-panel
    loadSubPanelData(panelName, subPanelName);
}

function loadSubPanelData(panelName, subPanelName) {
    if (panelName === 'agents') {
        switch (subPanelName) {
            case 'agents-list': loadAgents(); break;
            case 'agents-skills': loadAgentSkills(); break;
            case 'agents-tasks': loadAgentTasks(); break;
            case 'agents-memory': loadAgentMemory(); break;
            case 'agents-sessions': loadAgentSessions(); break;
            case 'agents-runs': loadAgentRuns(); break;
            case 'agents-runtime': loadAgentRuntime(); break;
        }
    } else if (panelName === 'settings') {
        switch (subPanelName) {
            case 'settings-global-skills': loadGlobalSkills(); break;
            case 'settings-vendors': loadVendors(); break;
            case 'settings-config': loadDebugSettings(); break;
        }
    }
}

function refreshCurrentSubPanel() {
    const panelName = state.currentPanel;
    const subPanelName = state.currentSubPanel[panelName];
    if (subPanelName) {
        loadSubPanelData(panelName, subPanelName);
    }
}

function onAgentSelected() {
    state.selectedAgent = document.getElementById('selected-agent').value;
    updateHeartbeatButton();
    refreshCurrentSubPanel();
}

async function updateHeartbeatButton() {
    const btn = document.getElementById('rt-heartbeat-btn');
    if (!btn) return;
    const agentId = state.selectedAgent;
    if (!agentId) { btn.disabled = true; return; }
    const status = await api('GET', `/agents/${agentId}/runtime-status`, null, { silent: true });
    btn.disabled = !(status?.active);
}

// Setup tab navigation
document.querySelectorAll('.main-tab').forEach(tab => {
    tab.addEventListener('click', () => navigateTo(tab.dataset.panel));
});

document.querySelectorAll('.sub-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const panel = tab.closest('.panel');
        const panelName = panel.id.replace('panel-', '');
        navigateToSubPanel(panelName, tab.dataset.subpanel);
    });
});

// ====================================================================
// TOAST NOTIFICATIONS
// ====================================================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;

    // Errors stay longer and are dismissible
    const duration = type === 'error' ? 8000 : 3000;

    // Make dismissible on click
    toast.style.cursor = 'pointer';
    toast.title = 'Click to dismiss';
    toast.onclick = () => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    };

    container.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ====================================================================
// MODAL
// ====================================================================
function showModal(title, bodyHtml, actionsHtml = '') {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = bodyHtml;
    document.getElementById('modal-actions').innerHTML = actionsHtml;
    document.getElementById('modal-backdrop').classList.remove('hidden');
    document.getElementById('modal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('modal-backdrop').classList.add('hidden');
    const modal = document.getElementById('modal');
    modal.classList.add('hidden');
    modal.classList.remove('modal-wide');
    modal.style.maxWidth = '';
}

// ====================================================================
// DASHBOARD
// ====================================================================
async function loadDashboard() {
    const status = await api('GET', '/status', null, { silent: true });
    if (status) {
        document.getElementById('stat-llm').textContent = status.llm_initialized ? 'OK' : 'NOT INIT';
        document.getElementById('stat-llm').className = 'stat-value ' + (status.llm_initialized ? 'ok' : 'error');
        document.getElementById('stat-provider').textContent = status.llm_provider || 'N/A';
        document.getElementById('stat-agents').textContent = status.configured_agents?.length || 0;
        document.getElementById('stat-skills').textContent = status.skills_loaded || 0;
    }

    await loadSchedulerStatus();
    await loadRuntimeStatus();

    if (serverConnected) {
        const agentsData = await api('GET', '/agents', null, { silent: true });
        if (agentsData && agentsData.agents) {
            const select = document.getElementById('quick-agent');
            select.innerHTML = agentsData.agents
                .filter(a => !a.is_deleted)
                .map(a => `<option value="${a.agent_id || a.id}">${a.name || a.agent_id || a.id}</option>`)
                .join('');
        }

        const skillsData = await api('GET', '/skills/global', null, { silent: true });
        if (skillsData && skillsData.skills) {
            const select = document.getElementById('quick-skill');
            select.innerHTML = '<option value="">None</option>' +
                skillsData.skills.map(s => `<option value="${s.id}">${s.name || s.id}</option>`).join('');
        }
    }
}

let schedulerRefreshTimeout = null;
let lastSchedulerStatus = null;

async function loadSchedulerStatus() {
    const sched = await api('GET', '/api/scheduler/status', null, { silent: true });
    if (sched) {
        const statusText = sched.status || 'unknown';
        lastSchedulerStatus = statusText;
        const statusClass = statusText === 'running' ? 'ok' : (statusText === 'stopped' ? 'error' : 'warn');
        document.getElementById('stat-scheduler').textContent = statusText.toUpperCase();
        document.getElementById('stat-scheduler').className = 'stat-value ' + statusClass;
        document.getElementById('stat-tasks').textContent = `${sched.enabled_tasks || 0}/${sched.total_tasks || 0}`;
        document.getElementById('sched-status').textContent = statusText.toUpperCase();
        document.getElementById('sched-status').className = 'stat-value ' + statusClass;
        document.getElementById('sched-uptime').textContent = sched.uptime_seconds ? formatDuration(sched.uptime_seconds) : 'N/A';
        if (sched.heartbeat_age_seconds !== null) {
            const hbText = sched.heartbeat_age_seconds < 2 ? 'Just now' : `${Math.round(sched.heartbeat_age_seconds)}s ago`;
            document.getElementById('sched-heartbeat').textContent = hbText;
            document.getElementById('sched-heartbeat').className = 'stat-value ' + (sched.heartbeat_ok ? 'ok' : 'error');
        } else {
            document.getElementById('sched-heartbeat').textContent = 'N/A';
        }
        document.getElementById('sched-enabled').textContent = `${sched.enabled_tasks || 0} of ${sched.total_tasks || 0}`;
        const externalBadge = document.getElementById('sched-external-badge');
        const pidItem = document.getElementById('sched-pid-item');
        if (sched.external) {
            externalBadge.classList.remove('hidden');
            if (sched.external_pid) {
                pidItem.style.display = '';
                document.getElementById('sched-pid').textContent = sched.external_pid;
            }
        } else {
            externalBadge.classList.add('hidden');
            pidItem.style.display = 'none';
        }

    }
    updateHealthBarColor();
}

let lastRuntimeStatus = null;

async function loadRuntimeStatus() {
    const rt = await api('GET', '/api/runtime/status', null, { silent: true });
    if (rt) {
        lastRuntimeStatus = rt.running ? 'running' : 'stopped';
        const statusClass = rt.running ? 'ok' : 'error';

        // Health bar indicator
        document.getElementById('stat-runtime').textContent = rt.running ? 'RUNNING' : 'STOPPED';
        document.getElementById('stat-runtime').className = 'stat-value ' + statusClass;

        // Stats bar active count
        const activeCount = rt.active_agents?.length || 0;
        document.getElementById('stat-active-agents').textContent = activeCount;
        document.getElementById('stat-active-agents').className = 'stat-value ' + (activeCount > 0 ? 'ok' : '');

        // Runtime Status panel
        document.getElementById('rt-status').textContent = rt.running ? 'RUNNING' : 'STOPPED';
        document.getElementById('rt-status').className = 'stat-value ' + statusClass;
        document.getElementById('rt-active-count').textContent = activeCount;
        document.getElementById('rt-active-count').className = 'stat-value ' + (activeCount > 0 ? 'ok' : '');
        document.getElementById('rt-total-queued').textContent = rt.total_queued || 0;
        document.getElementById('rt-total-queued').className = 'stat-value ' + (rt.total_queued > 0 ? 'warn' : '');
        document.getElementById('rt-llm-calls').textContent = rt.running_llm_calls || 0;
        document.getElementById('rt-llm-calls').className = 'stat-value ' + (rt.running_llm_calls > 0 ? 'ok' : '');

        // Active agents list
        const listEl = document.getElementById('rt-active-agents-list');
        if (activeCount > 0) {
            listEl.textContent = 'Active: ' + rt.active_agents.join(', ');
            listEl.style.display = '';
        } else {
            listEl.style.display = 'none';
        }
    }
    updateHealthBarColor();
}

function formatDuration(seconds) {
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
}

function formatDurationMs(ms) {
    return formatDuration(ms / 1000);
}

function scheduleNextRefresh() {
    if (schedulerRefreshTimeout) clearTimeout(schedulerRefreshTimeout);
    if (state.currentPanel !== 'dashboard') return;
    const interval = (lastSchedulerStatus === 'running') ? 60000 : 20000;
    schedulerRefreshTimeout = setTimeout(async () => {
        if (state.currentPanel === 'dashboard') {
            await loadSchedulerStatus();
            await loadRuntimeStatus();
            scheduleNextRefresh();
        }
    }, interval);
}

function stopSchedulerRefresh() {
    if (schedulerRefreshTimeout) {
        clearTimeout(schedulerRefreshTimeout);
        schedulerRefreshTimeout = null;
    }
}

async function quickRun() {
    const agentId = document.getElementById('quick-agent').value;
    const skillId = document.getElementById('quick-skill').value;
    const message = document.getElementById('quick-message').value.trim();
    if (!message) {
        showToast('Please enter a message', 'warning');
        return;
    }
    const responseBox = document.getElementById('quick-response');
    responseBox.classList.remove('hidden');
    responseBox.textContent = 'Running...';
    const body = { message };
    if (skillId) body.skill_id = skillId;
    const result = await api('POST', `/agents/${agentId}/run`, body);
    if (result) {
        responseBox.textContent = `Status: ${result.status}\nTurns: ${result.turns}\nTokens: ${result.total_tokens}\nDuration: ${result.duration_ms}ms\n\n--- Response ---\n${result.response || result.error || 'No response'}`;
        showToast('Agent run completed', 'success');
    } else {
        responseBox.textContent = 'Error running agent';
    }
}

async function shutdownServer() {
    if (!confirm('Are you sure you want to shutdown the server?\n\nThis will terminate the API server process. The Admin Panel will become disconnected and you will need to restart the server manually.')) {
        return;
    }
    const result = await api('POST', '/shutdown');
    if (result) {
        showToast('Server is shutting down...', 'warning');
        // Server will disconnect shortly
        setTimeout(() => {
            serverConnected = false;
            updateServerStatus(false);
            showToast('Server has been shutdown', 'error');
        }, 1000);
    }
}

// ====================================================================
// AGENT SELECTOR
// ====================================================================
async function loadAgentSelector() {
    const data = await api('GET', '/agents', null, { silent: true });
    if (data && data.agents) {
        const select = document.getElementById('selected-agent');
        const currentValue = select.value;
        select.innerHTML = data.agents
            .filter(a => !a.is_deleted)
            .map(a => {
                const id = a.agent_id || a.id;
                return `<option value="${id}">${a.name || id}${a.role ? ' (' + a.role + ')' : ''}</option>`;
            }).join('');
        if (data.agents.some(a => (a.agent_id || a.id) === currentValue)) {
            select.value = currentValue;
        }
        state.selectedAgent = select.value;
    }
}

// ====================================================================
// FEED
// ====================================================================
async function loadFeed(reset = true) {
    if (reset) {
        state.feed.offset = 0;
        state.feed.messages = [];
    }

    const container = document.getElementById('feed-list');
    if (reset) {
        container.innerHTML = '<div style="text-align: center; padding: 2rem; color: #78716c;">Loading...</div>';
    }

    let url = `/api/feed?limit=20&offset=${state.feed.offset}`;
    if (state.feed.filter === 'unread') {
        url += '&unread_only=true';
    } else if (state.feed.filter !== 'all') {
        url += `&message_type=${state.feed.filter}`;
    }

    const data = await api('GET', url, null, { silent: true });
    if (!data) {
        container.innerHTML = '<div style="text-align: center; padding: 2rem; color: #78716c;">Failed to load feed</div>';
        return;
    }

    state.feed.messages = reset ? data.messages : [...state.feed.messages, ...data.messages];
    state.feed.hasMore = data.messages.length === 20 && (state.feed.offset + 20) < data.total;

    renderFeed();
    loadFeedUnreadCount();
}

async function loadFeedUnreadCount() {
    const data = await api('GET', '/api/feed/unread-count', null, { silent: true });
    if (data) {
        state.feed.unreadCount = data.unread_count;
        const badge = document.getElementById('feed-badge');
        if (data.unread_count > 0) {
            badge.textContent = data.unread_count > 99 ? '99+' : data.unread_count;
            badge.style.display = 'inline-block';
        } else {
            badge.style.display = 'none';
        }
    }
}

function renderFeed() {
    const container = document.getElementById('feed-list');
    const loadMoreBtn = document.getElementById('feed-load-more');

    if (state.feed.messages.length === 0) {
        container.innerHTML = '<div style="text-align: center; padding: 2rem; color: #78716c;">No messages in feed</div>';
        loadMoreBtn.style.display = 'none';
        return;
    }

    container.innerHTML = state.feed.messages.map(msg => {
        const date = new Date(msg.created_at);
        const timeAgo = getTimeAgo(date);
        return `
                <div class="feed-message type-${msg.type}${msg.read ? '' : ' unread'}" id="feed-msg-${msg.id}">
                    <div class="feed-message-header">
                        <div class="feed-message-title">${escapeHtml(msg.title)}</div>
                        <div class="feed-message-meta">
                            <div class="feed-message-agent">${escapeHtml(msg.agent_id)}</div>
                            <div>${timeAgo}</div>
                        </div>
                    </div>
                    <div class="feed-message-body">${formatFeedBody(msg.body)}</div>
                    <div class="feed-message-actions">
                        ${!msg.read ? `<button class="btn" onclick="markFeedRead('${msg.id}')" style="font-size: 0.75rem;">Mark Read</button>` : ''}
                        <button class="btn danger" onclick="deleteFeedMessage('${msg.id}')" style="font-size: 0.75rem;">Delete</button>
                    </div>
                </div>
            `}).join('');

    loadMoreBtn.style.display = state.feed.hasMore ? 'block' : 'none';
}

function formatFeedBody(body) {
    // Simple markdown-like formatting
    let html = escapeHtml(body);
    // Code blocks
    html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    return html;
}

function getTimeAgo(date) {
    const seconds = Math.floor((new Date() - date) / 1000);
    if (seconds < 60) return 'just now';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    return `${days}d ago`;
}

async function markFeedRead(messageId) {
    await api('PUT', `/api/feed/${messageId}/read`);
    const msgEl = document.getElementById(`feed-msg-${messageId}`);
    if (msgEl) {
        msgEl.classList.remove('unread');
        const btn = msgEl.querySelector('button[onclick*="markFeedRead"]');
        if (btn) btn.remove();
    }
    loadFeedUnreadCount();
}

async function markAllFeedRead() {
    await api('POST', '/api/feed/mark-all-read');
    state.feed.messages.forEach(m => m.read = true);
    renderFeed();
    loadFeedUnreadCount();
    showToast('All messages marked as read', 'success');
}

async function deleteFeedMessage(messageId) {
    if (!confirm('Delete this message?')) return;
    await api('DELETE', `/api/feed/${messageId}`);
    state.feed.messages = state.feed.messages.filter(m => m.id !== messageId);
    renderFeed();
    showToast('Message deleted', 'success');
}

function loadMoreFeed() {
    state.feed.offset += 20;
    loadFeed(false);
}

function setFeedFilter(filter) {
    state.feed.filter = filter;
    document.querySelectorAll('.feed-filter').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.filter === filter);
    });
    loadFeed(true);
}

// Setup feed filter buttons
document.querySelectorAll('.feed-filter').forEach(btn => {
    btn.addEventListener('click', () => setFeedFilter(btn.dataset.filter));
});

// ====================================================================
// AGENTS LIST
// ====================================================================
async function loadAgents() {
    const container = document.getElementById('agents-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';
    const showDeleted = document.getElementById('show-deleted-agents').checked;
    const url = showDeleted ? '/agents?include_deleted=true' : '/agents';
    const data = await api('GET', url);
    if (!data || !data.agents || data.agents.length === 0) {
        container.innerHTML = '<div class="empty-state">No agents configured</div>';
        return;
    }
    state.agents = data.agents;
    renderAgents();
}

function renderAgents() {
    const container = document.getElementById('agents-list');
    container.innerHTML = state.agents.map(agent => {
        const agentId = agent.agent_id || agent.id;
        const isMain = agentId === 'main';
        const isDeleted = agent.is_deleted === true;
        const isActive = agent.active === true;
        const queueDepth = agent.queue_depth || 0;
        const statusDot = isActive
            ? '<span style="color:#22c55e;font-size:1.2em" title="Running">&#9679;</span>'
            : '<span style="color:#9ca3af;font-size:1.2em" title="Stopped">&#9679;</span>';
        const queueBadge = isActive && queueDepth > 0
            ? `<span class="card-badge" style="background:#3b82f6;color:#fff;margin-left:0.5rem">${queueDepth} queued</span>`
            : '';
        return `
                <div class="card${isDeleted ? ' disabled' : ''}" style="${isDeleted ? 'border-color: #fca5a5;' : ''}">
                    <div class="card-header">
                        <span class="card-title">${statusDot} ${agent.name || agentId}${agent.role ? ' <span class="card-badge">' + escapeHtml(agent.role) + '</span>' : ''}</span>
                        <span class="card-badge${isDeleted ? ' error' : ''}">${isDeleted ? 'DELETED' : (agent.model || 'default')}</span>${queueBadge}
                    </div>
                    <div class="card-description">${agent.description || 'No description'}</div>
                    <div class="card-meta">ID: ${agentId} | Max turns: ${agent.max_turns || 'default'}</div>
                    <div class="card-actions">
                        <button class="btn" onclick="showAgentDetails('${agentId}')">Details</button>
                        ${isDeleted ? `<button class="btn success" onclick="restoreAgent('${agentId}')">Restore</button>` : `
                            <button class="btn" onclick="showAgentModal('${agentId}')">Edit</button>
                            <button class="btn primary" onclick="showRunAgentModal('${agentId}')">Run</button>
                            ${isActive ? `<button class="btn" onclick="triggerHeartbeat('${agentId}')" title="Trigger heartbeat now">Heartbeat</button>` : ''}
                            ${isActive
                                ? `<button class="btn danger" onclick="stopAgent('${agentId}')">Stop</button>`
                                : `<button class="btn success" onclick="startAgent('${agentId}')">Start</button>`}
                            ${!isMain && !isActive ? `<button class="btn danger" onclick="deleteAgent('${agentId}')">Delete</button>` : ''}
                        `}
                    </div>
                </div>
            `}).join('');
}

async function showAgentDetails(agentId) {
    const [agent, runtimeStatus, queueData] = await Promise.all([
        api('GET', `/agents/${agentId}`),
        api('GET', `/agents/${agentId}/runtime-status`, null, { silent: true }),
        api('GET', `/agents/${agentId}/queue`, null, { silent: true }),
    ]);
    if (!agent) return;

    const isActive = runtimeStatus?.active || false;
    const statusLabel = isActive
        ? '<span style="color:#22c55e;font-weight:bold">Active</span>'
        : '<span style="color:#9ca3af">Stopped</span>';

    // Timers (with inline interval editor)
    const timersHtml = runtimeStatus?.timers && Object.keys(runtimeStatus.timers).length > 0
        ? Object.entries(runtimeStatus.timers).map(
            ([name, t]) => isActive
                ? `${name} (every <input type="number" min="1" max="1440" value="${t.interval_minutes}" style="width:50px;padding:1px 4px;border:1px solid var(--border);border-radius:4px;font-size:0.8rem" onchange="updateHeartbeatInterval('${agentId}','${name}',this.value)">m)`
                : `${name} (every ${t.interval_minutes}m)`
        ).join(', ')
        : 'None';

    // Currently processing
    const currentEvent = runtimeStatus?.current_event_source;
    const processingHtml = currentEvent
        ? `<span style="color:#f59e0b;font-weight:600">${escapeHtml(currentEvent)}</span>`
        : '<span style="color:#9ca3af">Idle</span>';

    // Started at
    const startedAt = runtimeStatus?.started_at
        ? new Date(runtimeStatus.started_at).toLocaleString()
        : 'N/A';

    // Metrics
    const m = runtimeStatus?.metrics || {};
    const metricsHtml = isActive || m.events_processed ? `
        <div style="margin-top: 0.75rem;">
            <label style="font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:#57534e;">Metrics</label>
            <div class="stats-bar" style="margin-top: 0.25rem; margin-bottom: 0; font-size: 0.8125rem;">
                <div class="stat-item">
                    <span class="stat-label">HB Fired:</span>
                    <span class="stat-value ok">${m.heartbeats_fired || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">HB Skipped:</span>
                    <span class="stat-value${m.heartbeats_skipped > 0 ? ' warn' : ''}">${m.heartbeats_skipped || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Processed:</span>
                    <span class="stat-value ok">${m.events_processed || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Failed:</span>
                    <span class="stat-value${m.events_failed > 0 ? ' error' : ''}">${m.events_failed || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Webhooks:</span>
                    <span class="stat-value">${m.webhooks_received || 0}</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Run Time:</span>
                    <span class="stat-value">${m.total_run_duration_ms ? formatDurationMs(m.total_run_duration_ms) : '0s'}</span>
                </div>
            </div>
        </div>` : '';

    // Queue table
    const queue = queueData?.queue || [];
    let queueHtml = '';
    if (queue.length > 0) {
        const priorityBadge = (p) => {
            const colors = { HIGH: 'error', NORMAL: '', LOW: 'warning' };
            return `<span class="card-badge ${colors[p] || ''}">${p}</span>`;
        };
        const rows = queue.map(e => {
            const timeAgo = getTimeAgo(new Date(e.timestamp));
            const preview = escapeHtml(e.message_preview || '').substring(0, 60);
            const routing = e.has_routing ? 'Yes' : '';
            return `<tr>
                <td>${priorityBadge(e.priority)}</td>
                <td>${escapeHtml(e.source)}</td>
                <td>${timeAgo}</td>
                <td title="${escapeHtml(e.message_preview || '')}">${preview}</td>
                <td>${routing}</td>
            </tr>`;
        }).join('');
        queueHtml = `
        <div style="margin-top: 0.75rem;">
            <label style="font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:#57534e;">Queue (${queue.length})</label>
            <div class="table-container" style="margin-top: 0.25rem; margin-bottom: 0;">
                <table>
                    <thead><tr><th>Priority</th><th>Source</th><th>Time</th><th>Message</th><th>Routing</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>`;
    } else if (isActive) {
        queueHtml = `
        <div style="margin-top: 0.75rem;">
            <label style="font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.05em;color:#57534e;">Queue</label>
            <div style="font-size:0.8125rem;color:#9ca3af;margin-top:0.25rem;">Empty</div>
        </div>`;
    }

    const bodyHtml = `
        <div class="form-group"><label>ID</label><div>${agent.agent_id}</div></div>
        <div class="form-group"><label>Role</label><div>${agent.role || 'N/A'}</div></div>
        <div class="form-group"><label>Model</label><div>${agent.model}</div></div>
        <div class="form-group"><label>Description</label><div>${agent.description || 'N/A'}</div></div>
        <div class="form-group"><label>Max Turns</label><div>${agent.max_turns}</div></div>
        <div class="form-group"><label>Enabled Tools</label><div>${agent.enabled_tools?.join(', ') || 'None'}</div></div>
        <div class="form-group"><label>Loaded Skills</label><div>${agent.loaded_skills?.join(', ') || 'None'}</div></div>
        <hr style="margin: 0.75rem 0; border-color: #e5e5e5;">
        <div class="form-group"><label>Runtime Status</label><div>${statusLabel}</div></div>
        <div class="form-group"><label>Currently Processing</label><div>${processingHtml}</div></div>
        <div class="form-group"><label>Queue Depth</label><div>${runtimeStatus?.queue_depth || 0}</div></div>
        <div class="form-group"><label>Heartbeat Timers</label><div>${timersHtml}</div></div>
        <div class="form-group"><label>Tick Count</label><div>${runtimeStatus?.tick_count || 0}</div></div>
        <div class="form-group"><label>Started At</label><div>${isActive ? startedAt : 'N/A'}</div></div>
        ${metricsHtml}
        ${queueHtml}
    `;

    // Widen modal for agent details
    document.getElementById('modal').style.maxWidth = '800px';
    showModal(`Agent: ${agent.name || agentId}`, bodyHtml, '<button class="btn" onclick="closeModal()">Close</button>');
}

async function startAgent(agentId) {
    const result = await api('POST', `/agents/${agentId}/start`);
    if (result) {
        const timers = result.timers || 0;
        showToast(`Agent ${agentId} started (${timers} heartbeat timers)`, 'success');
        loadAgents();
    }
}

async function stopAgent(agentId) {
    if (!confirm(`Stop agent ${agentId}? Current run will finish, but queued events will be dropped.`)) return;
    const result = await api('POST', `/agents/${agentId}/stop`);
    if (result) {
        showToast(`Agent ${agentId} stopped`, 'success');
        loadAgents();
    }
}

async function updateHeartbeatInterval(agentId, skillName, minutes) {
    const interval = parseInt(minutes);
    if (!interval || interval < 1) { showToast('Interval must be at least 1 minute', 'warning'); return; }
    const result = await api('POST', `/agents/${agentId}/heartbeat-interval`, {
        skill_name: skillName,
        interval_minutes: interval,
    });
    if (result && result.status === 'ok') {
        showToast(`${skillName} heartbeat interval: ${result.old_interval}m → ${result.new_interval}m`, 'success');
    }
}

async function triggerHeartbeat(agentId) {
    const result = await api('POST', `/agents/${agentId}/trigger-heartbeat`);
    if (result) {
        const skills = result.skills ? result.skills.join(', ') : 'all';
        showToast(`Heartbeat queued for ${agentId} (${skills})`, 'success');
        loadAgents();
    }
}

function showRunAgentModal(agentId) {
    showModal(`Run Agent: ${agentId}`, `
                <div class="form-group">
                    <label>Message</label>
                    <textarea id="modal-run-message" placeholder="Enter your message..."></textarea>
                </div>
                <div id="modal-run-response" class="response-box hidden"></div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="runAgentFromModal('${agentId}')">Run</button>
            `);
}

async function runAgentFromModal(agentId) {
    const message = document.getElementById('modal-run-message').value.trim();
    if (!message) { showToast('Please enter a message', 'warning'); return; }
    const responseBox = document.getElementById('modal-run-response');
    responseBox.classList.remove('hidden');
    responseBox.textContent = 'Running...';
    const result = await api('POST', `/agents/${agentId}/run`, { message });
    if (result) {
        closeModal();
        showRunResultModal(agentId, result);
    }
}

// ====================================================================
// STRUCTURED RUN RESULT MODAL
// ====================================================================

function showRunResultModal(agentId, result) {
    const modal = document.getElementById('modal');
    modal.classList.add('modal-wide');

    let sections = '';

    // Badge row
    const statusClass = 'status-' + (result.status || 'unknown');
    sections += `<div class="run-result-section">
        <div class="run-result-badges">
            <span class="badge ${statusClass}">${result.status || 'unknown'}</span>
            <span class="badge">${result.turns} turn${result.turns !== 1 ? 's' : ''}</span>
            <span class="badge">${result.duration_ms}ms</span>
            <span class="badge">${(result.total_tokens || 0).toLocaleString()} tokens</span>
            ${result.input_tokens ? `<span class="badge">in:${result.input_tokens.toLocaleString()}</span>` : ''}
            ${result.output_tokens ? `<span class="badge">out:${result.output_tokens.toLocaleString()}</span>` : ''}
        </div>
    </div>`;

    // Response
    const responseText = result.response || result.error || 'No response';
    sections += `<div class="run-result-section">
        <h3>Response</h3>
        <div class="response-box" style="max-height:200px">${escapeHtml(responseText)}</div>
    </div>`;

    // Plan
    if (result.plan) {
        sections += buildPlanSection(result.plan, result.step_stats);
    }

    // Execution Trace
    if (result.execution_trace && result.execution_trace.length > 0) {
        sections += buildTraceSection(result.execution_trace);
    }

    // Turn Details
    if (result.turn_details && result.turn_details.length > 0) {
        sections += buildTurnDetailsSection(result.turn_details);
    }

    // Reflections
    if (result.reflections && result.reflections.length > 0) {
        sections += buildReflectionsSection(result.reflections);
    }

    showModal(`Run Result: ${agentId}`, sections,
        '<button class="btn" onclick="closeModal()">Close</button>');
    // Re-apply wide class after showModal resets it
    modal.classList.add('modal-wide');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function buildPlanSection(plan, stepStats) {
    const steps = plan.steps || [];
    if (steps.length === 0) return '';

    // Build stats lookup: step_index -> stat
    const statsMap = {};
    if (stepStats) {
        for (const s of stepStats) {
            if (s.step_index !== null && s.step_index !== undefined) {
                statsMap[s.step_index] = s;
            }
        }
    }

    const completedCount = steps.filter(s => s.status === 'completed').length;
    const pct = Math.round((completedCount / steps.length) * 100);
    const revision = plan.revision || 0;

    let html = `<div class="run-result-section">
        <h3>Plan${plan.task ? ': ' + escapeHtml(plan.task) : ''}</h3>
        <div style="font-size:0.75rem; color:#78716c; margin-bottom:0.5rem;">
            Progress: ${completedCount}/${steps.length} (${pct}%)${revision > 0 ? ' | Revisions: ' + revision : ''}
        </div>
        <ol class="plan-steps">`;

    for (let i = 0; i < steps.length; i++) {
        const step = steps[i];
        const status = step.status || 'pending';
        let icon = '&#9744;'; // empty checkbox
        if (status === 'completed') icon = '&#9745;'; // checked
        else if (status === 'in_progress') icon = '&#9654;'; // play

        let meta = '';
        const stat = statsMap[i];
        if (stat) {
            meta = `${stat.turns}t | ${(stat.total_tokens || 0).toLocaleString()} tok`;
        }

        html += `<li>
            <span class="step-icon">${icon}</span>
            <span>${escapeHtml(step.description || step.step || 'Step ' + (i + 1))}</span>
            ${meta ? `<span class="step-meta">${meta}</span>` : ''}
        </li>`;
    }

    html += `</ol></div>`;
    return html;
}

function buildTraceSection(trace) {
    const t0 = trace[0] ? new Date(trace[0].timestamp).getTime() : 0;

    let rows = '';
    for (const evt of trace) {
        const elapsed = t0 ? ((new Date(evt.timestamp).getTime() - t0) / 1000).toFixed(1) + 's' : '';
        const eventType = evt.event || 'unknown';
        const stepIdx = evt.step_index !== null && evt.step_index !== undefined ? evt.step_index : '';
        const detail = evt.detail || evt.description || '';

        rows += `<tr>
            <td>${evt.turn || ''}</td>
            <td>${elapsed}</td>
            <td><span class="trace-event-badge ${eventType}">${eventType.replace(/_/g, ' ')}</span></td>
            <td>${stepIdx}</td>
            <td>${escapeHtml(detail)}</td>
        </tr>`;
    }

    return `<div class="run-result-section">
        <h3>Execution Trace</h3>
        <div class="table-container" style="max-height:250px; overflow-y:auto;">
            <table class="trace-timeline">
                <thead><tr><th>Turn</th><th>Time</th><th>Event</th><th>Step</th><th>Detail</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>
    </div>`;
}

function buildTurnDetailsSection(turnDetails) {
    let rows = '';
    for (const t of turnDetails) {
        const tools = (t.tools || []).map(tc => {
            const cls = tc.success ? 'color:#16a34a' : 'color:#dc2626';
            return `<span style="${cls}">${tc.name}${tc.success ? '' : ' (' + (tc.error || 'fail') + ')'}</span>`;
        }).join(', ') || '-';

        const tokens = t.tokens ? `${(t.tokens.total || 0).toLocaleString()}` : '-';
        const stepInfo = t.plan_step_index !== null && t.plan_step_index !== undefined
            ? `#${t.plan_step_index}` : '-';

        rows += `<tr>
            <td>${t.number}</td>
            <td>${t.duration_ms}ms</td>
            <td>${tools}</td>
            <td>${tokens}</td>
            <td>${stepInfo}</td>
        </tr>`;
    }

    return `<details class="section-toggle">
        <summary>Turn Details (${turnDetails.length})</summary>
        <div class="section-content">
            <div class="table-container" style="max-height:250px; overflow-y:auto;">
                <table class="trace-timeline">
                    <thead><tr><th>Turn</th><th>Duration</th><th>Tools</th><th>Tokens</th><th>Step</th></tr></thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        </div>
    </details>`;
}

function buildReflectionsSection(reflections) {
    let items = '';
    for (const r of reflections) {
        const decision = r.decision || 'unknown';
        const confidence = r.confidence_in_approach !== undefined
            ? (r.confidence_in_approach * 100).toFixed(0) + '%' : '';
        const assessment = r.progress_assessment || '';

        items += `<div style="margin-bottom:0.75rem; padding-bottom:0.75rem; border-bottom:1px solid #f5f5f4;">
            <div style="margin-bottom:0.25rem;">
                <span class="reflection-decision ${decision}">${decision}</span>
                ${confidence ? `<span style="font-size:0.6875rem; color:#78716c; margin-left:0.5rem;">confidence: ${confidence}</span>` : ''}
                ${assessment ? `<span style="font-size:0.6875rem; color:#78716c; margin-left:0.5rem;">${assessment}</span>` : ''}
                <span style="font-size:0.6875rem; color:#78716c; margin-left:0.5rem;">turn ${r.turn_number || '?'}</span>
            </div>
            ${r.reasoning ? `<div style="font-size:0.75rem; color:#44403c;">${escapeHtml(r.reasoning)}</div>` : ''}
        </div>`;
    }

    return `<details class="section-toggle">
        <summary>Reflections (${reflections.length})</summary>
        <div class="section-content">${items}</div>
    </details>`;
}

const AVAILABLE_TOOLS = [
    'file_read', 'file_write', 'http_call', 'web_fetch',
    'task_create', 'task_list', 'task_get', 'task_update',
    'task_delete', 'task_trigger', 'task_runs',
    'save_task_state', 'get_task_state', 'feed_post', 'create_event',
    'web_search', 'csv_export', 'spreadsheet_create', 'send_notification', 'image_generate',
    'document_extract', 'email_send', 'ticket_create_crm', 'ticket_update_crm',
    'crm_search', 'crm_write'
];

async function showAgentModal(agentId = null) {
    const isEdit = agentId !== null;
    const title = isEdit ? `Edit Agent: ${agentId}` : 'Add New Agent';
    let agent = {
        agent_id: '', name: '', role: '', description: '',
        model: 'claude-sonnet-4-5-20250929', max_turns: 30,
        heartbeat_context_count: 3,
        system_prompt: 'You are a helpful AI assistant.',
        enabled_tools: []
    };
    if (isEdit) {
        const data = await api('GET', `/agents/${agentId}`);
        if (data) {
            agent = {
                agent_id: data.agent_id, name: data.name || '',
                role: data.role || '', description: data.description || '',
                model: data.model || 'claude-sonnet-4-5-20250929',
                max_turns: data.max_turns || 30,
                heartbeat_context_count: data.heartbeat_context_count ?? 3,
                system_prompt: data.system_prompt || 'You are a helpful AI assistant.',
                enabled_tools: data.enabled_tools || []
            };
        }
    }
    const toolCheckboxes = AVAILABLE_TOOLS.map(tool => {
        const checked = agent.enabled_tools.includes(tool) ? 'checked' : '';
        return `<label style="display: inline-flex; align-items: center; gap: 0.25rem; margin-right: 1rem;">
                    <input type="checkbox" id="agent-tool-${tool}" ${checked}> ${tool}
                </label>`;
    }).join('');
    // Auto-generate ID for new agents
    const autoId = isEdit ? agent.agent_id : generateId('ag');
    showModal(title, `
                <div class="form-grid">
                    <div class="form-group"><label>Agent ID</label><input type="text" id="agent-form-id" value="${autoId}" disabled style="background: #f5f5f4; color: #78716c;"></div>
                    <div class="form-group"><label>Name *</label><input type="text" id="agent-form-name" value="${agent.name}" placeholder="My Agent"></div>
                    <div class="form-group"><label>Role</label><input type="text" id="agent-form-role" value="${agent.role}" placeholder="e.g. Research Assistant" maxlength="50"></div>
                    <div class="form-group full-width"><label>Description</label><input type="text" id="agent-form-description" value="${agent.description}" placeholder="What does this agent do?"></div>
                    <div class="form-group"><label>Model</label>
                        <select id="agent-form-model">
                            <option value="claude-sonnet-4-5-20250929" ${agent.model === 'claude-sonnet-4-5-20250929' ? 'selected' : ''}>Claude Sonnet 4.5</option>
                            <option value="claude-opus-4-5-20251101" ${agent.model === 'claude-opus-4-5-20251101' ? 'selected' : ''}>Claude Opus 4.5</option>
                            <option value="gpt-4o" ${agent.model === 'gpt-4o' ? 'selected' : ''}>GPT-4o</option>
                        </select>
                    </div>
                    <div class="form-group"><label>Max Turns</label><input type="number" id="agent-form-max-turns" value="${agent.max_turns}" min="1" max="100"></div>
                    <div class="form-group"><label>Heartbeat Context</label><input type="number" id="agent-form-heartbeat-context" value="${agent.heartbeat_context_count}" min="0" max="10" title="Number of prior heartbeat summaries to inject"></div>
                    <div class="form-group full-width"><label>System Prompt</label><textarea id="agent-form-system-prompt" style="min-height: 80px;">${agent.system_prompt}</textarea></div>
                    <div class="form-group full-width"><label>Enabled Tools</label><div style="padding: 0.5rem 0;">${toolCheckboxes}</div></div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="saveAgent(${isEdit ? `'${agentId}'` : 'null'})">${isEdit ? 'Save Changes' : 'Create Agent'}</button>
            `);
}

async function saveAgent(agentId) {
    const isEdit = agentId !== null;
    const formId = document.getElementById('agent-form-id').value.trim();  // Auto-generated for new
    const name = document.getElementById('agent-form-name').value.trim();
    const role = document.getElementById('agent-form-role').value.trim();
    const description = document.getElementById('agent-form-description').value.trim();
    const model = document.getElementById('agent-form-model').value;
    const maxTurns = parseInt(document.getElementById('agent-form-max-turns').value) || 30;
    const heartbeatContextCount = parseInt(document.getElementById('agent-form-heartbeat-context').value) || 3;
    const systemPrompt = document.getElementById('agent-form-system-prompt').value.trim();
    const enabledTools = AVAILABLE_TOOLS.filter(tool => document.getElementById(`agent-tool-${tool}`).checked);
    if (!name) { showToast('Name is required', 'warning'); return; }
    const payload = { name, role, description, model, max_turns: maxTurns, heartbeat_context_count: heartbeatContextCount, system_prompt: systemPrompt, enabled_tools: enabledTools };
    let result;
    if (isEdit) {
        result = await api('PUT', `/agents/${agentId}`, payload);
    } else {
        payload.agent_id = formId;  // Use auto-generated ID
        result = await api('POST', '/agents', payload);
    }
    if (result && result.status === 'ok') {
        showToast(result.message || (isEdit ? 'Agent updated' : 'Agent created'), 'success');
        closeModal();
        loadAgents();
        loadAgentSelector();
    }
}

async function deleteAgent(agentId) {
    if (!confirm(`Delete agent "${agentId}"?`)) return;
    const result = await api('DELETE', `/agents/${agentId}`);
    if (result && result.status === 'ok') {
        showToast(`Agent "${agentId}" deleted`, 'success');
        loadAgents();
        loadAgentSelector();
    }
}

async function restoreAgent(agentId) {
    const result = await api('POST', `/agents/${agentId}/restore`);
    if (result && result.status === 'ok') {
        showToast(`Agent "${agentId}" restored`, 'success');
        loadAgents();
        loadAgentSelector();
    }
}

// ====================================================================
// AGENT SKILLS
// ====================================================================
let editableSkillIds = new Set();

async function loadAgentSkills() {
    const container = document.getElementById('agent-skills-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';
    const agentId = state.selectedAgent;
    const showDeleted = document.getElementById('show-deleted-skills')?.checked || false;

    // Load agent's private skills only
    const skillsUrl = showDeleted ? `/agents/${agentId}/skills?include_deleted=true` : `/agents/${agentId}/skills`;
    const skillsData = await api('GET', skillsUrl);

    if (!skillsData) {
        container.innerHTML = '<div class="empty-state">Failed to load skills</div>';
        return;
    }

    // Only show private skills (agent's own skills) - all are editable
    state.agentSkills = { global: skillsData.global_skills || [], private: skillsData.private_skills || [] };
    const privateSkills = state.agentSkills.private;

    if (privateSkills.length === 0) {
        container.innerHTML = '<div class="empty-state">No skills for this agent.<br><br>Click "+ Create Skill" to create one using the AI-assisted editor,<br>or "+ Import Skill" to import an existing skill.</div>';
        return;
    }

    container.innerHTML = privateSkills.map(skill => {
        const isDeleted = skill.is_deleted === true;
        return `
                <div class="card${isDeleted ? ' disabled' : ''}" style="${isDeleted ? 'border-color: #fca5a5;' : ''}">
                    <div class="card-header">
                        <span class="card-title">${skill.name || skill.id}</span>
                        ${isDeleted ? '<span class="card-badge error">DELETED</span>' : ''}
                    </div>
                    <div class="card-description">${skill.description || 'No description'}</div>
                    <div class="card-meta">ID: ${skill.id} | Triggers: ${skill.triggers?.join(', ') || 'None'}</div>
                    <div class="card-actions">
                        <button class="btn" onclick="viewSkillDetails('${skill.id}', 'private')">Details</button>
                        ${isDeleted ? `
                            <button class="btn success" onclick="restoreSkill('${skill.id}')">Restore</button>
                            <button class="btn danger" onclick="hardDeleteSkill('${skill.id}')">Permanently Delete</button>
                        ` : `
                            <button class="btn" onclick="editSkill('${skill.id}')">Edit</button>
                            <button class="btn danger" onclick="deleteSkill('${skill.id}')">Delete</button>
                        `}
                    </div>
                </div>
            `}).join('');
}

async function viewSkillDetails(skillId, source) {
    const agentId = state.selectedAgent;
    let skill;
    if (source === 'global') {
        skill = await api('GET', `/skills/${skillId}`);
    } else {
        skill = await api('GET', `/agents/${agentId}/skills/${skillId}`);
    }
    if (skill) {
        showModal(`Skill: ${skill.name || skillId}`, `
                    <div class="form-group"><label>ID</label><div>${skill.id}</div></div>
                    <div class="form-group"><label>Source</label><div>${source}</div></div>
                    <div class="form-group"><label>Description</label><div>${skill.description || 'N/A'}</div></div>
                    <div class="form-group"><label>Triggers</label><div>${skill.triggers?.join(', ') || 'None'}</div></div>
                    <div class="form-group"><label>Required Tools</label><div>${skill.requires?.tools?.join(', ') || 'None'}</div></div>
                    ${skill.content ? `<div class="form-group"><label>Content Preview</label><div class="response-box" style="max-height: 200px;">${escapeHtml(skill.content.substring(0, 500))}${skill.content.length > 500 ? '...' : ''}</div></div>` : ''}
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Generate unique IDs for entities
function generateId(prefix) {
    const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
    let id = prefix + '_';
    for (let i = 0; i < 8; i++) {
        id += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return id;
}

async function deleteSkill(skillId) {
    if (!confirm(`Delete skill "${skillId}"?\n\nThe skill will be soft-deleted and can be restored later.`)) return;
    const agentId = state.selectedAgent;
    const result = await api('DELETE', `/agents/${agentId}/skills/${skillId}`);
    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" deleted`, 'success');
        loadAgentSkills();
    }
}

async function restoreSkill(skillId) {
    const agentId = state.selectedAgent;
    const result = await api('POST', `/agents/${agentId}/skills/${skillId}/restore`);
    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" restored`, 'success');
        loadAgentSkills();
    }
}

async function hardDeleteSkill(skillId) {
    if (!confirm(`PERMANENTLY delete skill "${skillId}"?\n\nThis action cannot be undone!`)) return;
    const agentId = state.selectedAgent;
    const result = await api('DELETE', `/agents/${agentId}/skills/${skillId}?hard_delete=true`);
    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" permanently deleted`, 'success');
        loadAgentSkills();
    }
}

// ====================================================================
// SKILL EDITOR
// ====================================================================
let skillEditorState = {
    step: 0,  // 0 = choose method, 1 = intent input, 2 = form
    intent: '',
    hypothesis: null,
    form: null,
    answers: {},
    editingSkillId: null,
    fromTemplate: null,
    skippedFields: [],   // Fields marked as "not applicable"
    customFields: []     // Fields with custom answers
};

let skillTemplates = [];

function showSkillEditorModal(editSkillId = null) {
    skillEditorState = {
        step: 0,
        intent: '',
        hypothesis: null,
        form: null,
        answers: {},
        editingSkillId: editSkillId,
        fromTemplate: null,
        skippedFields: [],
        customFields: []
    };

    if (editSkillId) {
        // Load existing skill for editing
        loadSkillForEditing(editSkillId);
    } else {
        // New skill - show method selection
        renderSkillEditorStep0();
    }
}

let skillVendors = [];

async function renderSkillEditorStep0() {
    // Load templates and vendors in parallel
    const [templatesData, vendorsData] = await Promise.all([
        api('GET', '/skills/templates', null, { silent: true }),
        api('GET', '/skills/vendors', null, { silent: true })
    ]);

    skillTemplates = templatesData?.templates || [];
    skillVendors = vendorsData?.vendors || [];

    const templateCards = skillTemplates.map(t => `
                <div class="card" style="cursor: pointer; margin-bottom: 0.5rem;" onclick="selectTemplate('${t.id}')">
                    <div class="card-header">
                        <span class="card-title" style="font-size: 0.875rem;">${t.name}</span>
                        <span class="card-badge">${t.category}</span>
                    </div>
                    <div class="card-description" style="font-size: 0.75rem;">${t.description}</div>
                </div>
            `).join('');

    const vendorCards = skillVendors.filter(v => v.enabled).map(v => `
                <div class="card" style="cursor: pointer; margin-bottom: 0.5rem;" onclick="selectVendor('${v.id}')">
                    <div class="card-header">
                        <span class="card-title" style="font-size: 0.875rem;">${v.name}</span>
                        <span class="card-badge">${v.skill_count} skills</span>
                    </div>
                    <div class="card-description" style="font-size: 0.75rem;">${v.description || 'External skill provider'}</div>
                </div>
            `).join('');

    showModal('Create New Skill', `
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 1rem;">
                    <div style="border: 1px solid #e7e5e4; padding: 1rem; cursor: pointer; text-align: center;" onclick="startFromScratch()" onmouseover="this.style.background='#f5f5f4'" onmouseout="this.style.background='white'">
                        <div style="font-size: 2rem; margin-bottom: 0.5rem;">+</div>
                        <div style="font-weight: 600;">Start from Scratch</div>
                        <div style="font-size: 0.75rem; color: #78716c; margin-top: 0.25rem;">AI-assisted skill creation</div>
                    </div>
                    <div style="border: 1px solid #e7e5e4; padding: 1rem; cursor: pointer; text-align: center;" onclick="showTemplateList()" onmouseover="this.style.background='#f5f5f4'" onmouseout="this.style.background='white'">
                        <div style="font-size: 2rem; margin-bottom: 0.5rem;">&#128218;</div>
                        <div style="font-weight: 600;">Use a Template</div>
                        <div style="font-size: 0.75rem; color: #78716c; margin-top: 0.25rem;">Pre-built skills to customize</div>
                    </div>
                    <div style="border: 1px solid #e7e5e4; padding: 1rem; cursor: pointer; text-align: center; ${skillVendors.length === 0 ? 'opacity: 0.5;' : ''}" onclick="${skillVendors.length > 0 ? 'showVendorList()' : ''}" onmouseover="this.style.background='#f5f5f4'" onmouseout="this.style.background='white'">
                        <div style="font-size: 2rem; margin-bottom: 0.5rem;">&#127760;</div>
                        <div style="font-weight: 600;">From Vendor</div>
                        <div style="font-size: 0.75rem; color: #78716c; margin-top: 0.25rem;">${skillVendors.length > 0 ? 'External skill providers' : 'No vendors configured'}</div>
                    </div>
                    <div style="border: 1px solid #e7e5e4; padding: 1rem; cursor: pointer; text-align: center;" onclick="showImportSkillForm()" onmouseover="this.style.background='#f5f5f4'" onmouseout="this.style.background='white'">
                        <div style="font-size: 2rem; margin-bottom: 0.5rem;">&#128229;</div>
                        <div style="font-weight: 600;">Import Skill</div>
                        <div style="font-size: 0.75rem; color: #78716c; margin-top: 0.25rem;">Paste skill.json & skill.md</div>
                    </div>
                </div>
                <div id="template-list" style="display: none; margin-top: 1rem; max-height: 300px; overflow-y: auto;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">Choose a Template:</div>
                    ${templateCards || '<div class="empty-state">No templates available</div>'}
                </div>
                <div id="vendor-list" style="display: none; margin-top: 1rem; max-height: 300px; overflow-y: auto;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">Choose a Vendor:</div>
                    ${vendorCards || '<div class="empty-state">No vendors available</div>'}
                </div>
                <div id="import-skill-form" style="display: none; margin-top: 1rem;">
                    <p style="font-size: 0.875rem; color: #57534e; margin-bottom: 1rem;">
                        Paste your skill instructions below. The metadata (name, triggers, etc.) will be auto-generated.
                    </p>
                    <div class="form-group">
                        <label>Skill Instructions (Markdown) <span style="color: #dc2626;">*</span></label>
                        <textarea id="import-skill-md" rows="12" placeholder="# My Skill Name&#10;&#10;## Purpose&#10;Describe what this skill does...&#10;&#10;## Instructions&#10;1. First step&#10;2. Second step&#10;...&#10;&#10;## Output Format&#10;Describe expected output..."></textarea>
                    </div>
                    <div style="margin-top: 1rem;">
                        <button class="btn primary" onclick="importSkillFromWizard()">
                            <span id="import-btn-text">Import Skill</span>
                            <span id="import-btn-loading" class="loading" style="display: none; margin-left: 0.5rem;"></span>
                        </button>
                        <button class="btn" onclick="renderSkillEditorStep0()" style="margin-left: 0.5rem;">Back</button>
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
            `);
}

function showVendorList() {
    document.getElementById('template-list').style.display = 'none';
    document.getElementById('vendor-list').style.display = 'block';
    document.getElementById('import-skill-form').style.display = 'none';
}

function showImportSkillForm() {
    document.getElementById('template-list').style.display = 'none';
    document.getElementById('vendor-list').style.display = 'none';
    document.getElementById('import-skill-form').style.display = 'block';
}

async function importSkillFromWizard() {
    const agentId = state.selectedAgent;
    const mdContent = document.getElementById('import-skill-md').value.trim();

    if (!mdContent) {
        showToast('Please paste the skill instructions', 'error');
        return;
    }

    // Show loading state
    document.getElementById('import-btn-text').textContent = 'Importing...';
    document.getElementById('import-btn-loading').style.display = 'inline-block';

    // Call API - LLM will generate skill.json from markdown
    const result = await api('POST', `/agents/${agentId}/skills/import`, {
        skill_md: mdContent
    });

    // Reset button state
    document.getElementById('import-btn-text').textContent = 'Import Skill';
    document.getElementById('import-btn-loading').style.display = 'none';

    if (result && result.status === 'ok') {
        showToast(`Skill "${result.skill_name || result.skill_id}" imported successfully`, 'success');
        closeModal();
        loadAgentSkills();
    }
}

async function selectVendor(vendorId) {
    const vendor = await api('GET', `/skills/vendors/${vendorId}`);
    if (!vendor) {
        showToast('Failed to load vendor', 'error');
        return;
    }
    renderVendorSkills(vendor);
}

function renderVendorSkills(vendor) {
    const skillCards = (vendor.skills || []).map(s => `
                <div class="card" style="cursor: pointer; margin-bottom: 0.5rem;" onclick="selectVendorSkill('${vendor.id}', '${s.id}')">
                    <div class="card-header">
                        <span class="card-title" style="font-size: 0.875rem;">${s.name}</span>
                    </div>
                    <div class="card-description" style="font-size: 0.75rem;">${s.description}</div>
                    <div class="card-meta" style="font-size: 0.7rem;">${(s.tags || []).join(', ')}</div>
                </div>
            `).join('');

    showModal(`${vendor.name} - Skills`, `
                <div style="background: #f5f5f4; padding: 0.75rem; margin-bottom: 1rem; border: 1px solid #e7e5e4;">
                    <div style="font-size: 0.875rem; color: #57534e;">${vendor.description}</div>
                    <a href="${vendor.website}" target="_blank" style="font-size: 0.75rem; color: #2563eb;">${vendor.website}</a>
                </div>
                <div style="max-height: 350px; overflow-y: auto;">
                    ${skillCards || '<div class="empty-state">No skills available from this vendor</div>'}
                </div>
            `, `
                <button class="btn" onclick="renderSkillEditorStep0()">Back</button>
            `);
}

async function selectVendorSkill(vendorId, skillId) {
    const vendor = await api('GET', `/skills/vendors/${vendorId}`);
    if (!vendor) return;

    const skill = vendor.skills.find(s => s.id === skillId);
    if (!skill) return;

    showModal(`Install: ${skill.name}`, `
                <div style="background: #f5f5f4; padding: 1rem; margin-bottom: 1rem; border: 1px solid #e7e5e4;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">${skill.name}</div>
                    <div style="font-size: 0.875rem; color: #57534e; margin-bottom: 0.5rem;">${skill.description}</div>
                    <div style="font-size: 0.75rem; color: #78716c;">
                        From: ${vendor.name} | Tags: ${(skill.tags || []).join(', ')}
                    </div>
                </div>
                <div class="form-group" style="margin-bottom: 1rem;">
                    <label>Skill ID</label>
                    <input type="text" id="vendor-skill-id" value="${generateId('sk')}" disabled style="background: #f5f5f4; color: #78716c;">
                </div>
                <p style="font-size: 0.75rem; color: #78716c;">
                    This will download the skill from ${vendor.name} and install it to your agent.
                    You can edit the skill after installation.
                </p>
            `, `
                <button class="btn" onclick="renderVendorSkills(${JSON.stringify(vendor).replace(/"/g, '&quot;')})">Back</button>
                <button class="btn primary" onclick="installVendorSkill('${vendorId}', '${skillId}')">
                    <span id="install-btn-text">Install Skill</span>
                    <span id="install-btn-loading" class="loading" style="display: none; margin-left: 0.5rem;"></span>
                </button>
            `);
}

async function installVendorSkill(vendorId, originalSkillId) {
    const customSkillId = document.getElementById('vendor-skill-id').value.trim();  // Auto-generated

    document.getElementById('install-btn-text').textContent = 'Installing...';
    document.getElementById('install-btn-loading').style.display = 'inline-block';

    const agentId = state.selectedAgent;
    const result = await api('POST', `/agents/${agentId}/skills/from-vendor?vendor_id=${vendorId}&skill_id=${originalSkillId}&custom_skill_id=${customSkillId}`);

    if (result && result.status === 'ok') {
        showToast(`Skill "${customSkillId}" installed!`, 'success');
        closeModal();
        loadAgentSkills();
    } else {
        document.getElementById('install-btn-text').textContent = 'Install Skill';
        document.getElementById('install-btn-loading').style.display = 'none';
    }
}

function startFromScratch() {
    skillEditorState.step = 1;
    skillEditorState.fromTemplate = null;
    renderSkillEditorStep1();
}

function showTemplateList() {
    document.getElementById('template-list').style.display = 'block';
    document.getElementById('vendor-list').style.display = 'none';
    document.getElementById('import-skill-form').style.display = 'none';
}

async function selectTemplate(templateId) {
    // Load template details
    const template = await api('GET', `/skills/templates/${templateId}`);
    if (!template) {
        showToast('Failed to load template', 'error');
        return;
    }

    skillEditorState.fromTemplate = template;
    renderTemplatePreview(template);
}

function renderTemplatePreview(template) {
    const skillData = template.skill_json;
    const hasForm = template.template_form && template.template_form.fields && template.template_form.fields.length > 0;

    // Choose primary action based on whether template has a form
    const primaryBtn = hasForm
        ? `<button class="btn primary" onclick="renderTemplateForm(skillEditorState.fromTemplate)">Configure &amp; Install</button>`
        : `<button class="btn primary" onclick="useTemplateAsIs()">Use As-Is</button>`;

    showModal(`Template: ${skillData.name}`, `
                <div style="background: #f5f5f4; padding: 1rem; margin-bottom: 1rem; border: 1px solid #e7e5e4;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">${skillData.name}</div>
                    <div style="font-size: 0.875rem; color: #57534e; margin-bottom: 0.5rem;">${skillData.description}</div>
                    <div style="font-size: 0.75rem; color: #78716c;">
                        Triggers: ${skillData.triggers?.join(', ') || 'None'} | Tools: ${skillData.requires?.tools?.join(', ') || 'None'}
                    </div>
                    ${hasForm ? `<div style="font-size: 0.75rem; color: #2563eb; margin-top: 0.5rem;">This template has ${template.template_form.fields.length} configuration questions</div>` : ''}
                </div>
                <div class="form-group" style="margin-bottom: 1rem;">
                    <label>Skill ID</label>
                    <input type="text" id="template-skill-id" value="${generateId('sk')}" disabled style="background: #f5f5f4; color: #78716c;">
                </div>
                <div style="margin-bottom: 1rem;">
                    <label style="font-size: 0.75rem; font-weight: 600; text-transform: uppercase; color: #57534e;">Content Preview</label>
                    <div class="response-box" style="max-height: 200px; font-size: 0.75rem; margin-top: 0.25rem;">${escapeHtml(template.skill_md.substring(0, 800))}${template.skill_md.length > 800 ? '...' : ''}</div>
                </div>
            `, `
                <button class="btn" onclick="renderSkillEditorStep0()">Back</button>
                <button class="btn" onclick="customizeTemplate()">Customize</button>
                ${hasForm ? `<button class="btn" onclick="useTemplateAsIs()">Skip Config</button>` : ''}
                ${primaryBtn}
            `);
}

async function useTemplateAsIs() {
    const skillId = document.getElementById('template-skill-id').value.trim();  // Auto-generated
    const agentId = state.selectedAgent;
    const templateId = skillEditorState.fromTemplate.id;

    const result = await api('POST', `/agents/${agentId}/skills/from-template?template_id=${templateId}&skill_id=${skillId}`);
    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" created from template!`, 'success');
        closeModal();
        loadAgentSkills();
    }
}

function renderTemplateForm(template) {
    const skillData = template.skill_json;
    const fields = template.template_form.fields;

    // Preserve skill ID from preview step before modal content changes
    const existingSkillId = document.getElementById('template-skill-id')?.value?.trim();
    if (existingSkillId) {
        skillEditorState.templateSkillId = existingSkillId;
    }

    // Store form fields in skillEditorState so renderFormField helpers work
    skillEditorState.form = template.template_form;

    // Set defaults for fields that have them
    for (const field of fields) {
        if (field.default !== undefined && skillEditorState.answers[field.id] === undefined) {
            skillEditorState.answers[field.id] = field.default;
        }
    }

    const fieldsHtml = fields.map(f => renderFormField(f)).join('');

    showModal(`Configure: ${skillData.name}`, `
                <div style="font-size: 0.875rem; color: #57534e; margin-bottom: 1rem;">
                    Answer the questions below to customize this template for your needs.
                </div>
                <div id="template-form-fields">
                    ${fieldsHtml}
                </div>
            `, `
                <button class="btn" onclick="renderTemplatePreview(skillEditorState.fromTemplate)">Back</button>
                <button class="btn primary" onclick="installTemplateWithForm()">Install</button>
            `);
}

async function installTemplateWithForm() {
    const template = skillEditorState.fromTemplate;
    const fields = template.template_form.fields;

    // Validate required fields
    for (const field of fields) {
        if (field.required && !skillEditorState.skippedFields?.includes(field.id)) {
            const val = skillEditorState.answers[field.id];
            if (val === undefined || val === '' || val === null) {
                showToast(`"${field.question}" is required`, 'warning');
                return;
            }
        }
    }

    // Collect answers (skip internal keys like *_custom)
    const formAnswers = {};
    for (const field of fields) {
        if (skillEditorState.skippedFields?.includes(field.id)) continue;
        const val = skillEditorState.answers[field.id];
        if (val !== undefined && val !== '' && val !== null) {
            formAnswers[field.id] = val;
        }
    }

    const agentId = state.selectedAgent;
    const skillId = skillEditorState.templateSkillId || generateId('sk');
    const templateId = template.id;

    const result = await api('POST', `/agents/${agentId}/skills/from-template?template_id=${templateId}&skill_id=${skillId}`, formAnswers);
    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" configured and installed!`, 'success');
        closeModal();
        loadAgentSkills();
    }
}

function customizeTemplate() {
    const skillId = document.getElementById('template-skill-id').value.trim();  // Auto-generated
    const template = skillEditorState.fromTemplate;
    renderTemplateEditor(template, skillId);
}

function renderTemplateEditor(template, skillId) {
    const skillData = template.skill_json;

    showModal(`Customize: ${skillData.name}`, `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Skill ID</label>
                        <input type="text" id="custom-skill-id" value="${skillId}" disabled style="background: #f5f5f4; color: #78716c;">
                    </div>
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="custom-skill-name" value="${skillData.name}">
                    </div>
                    <div class="form-group full-width">
                        <label>Description</label>
                        <input type="text" id="custom-skill-description" value="${skillData.description}">
                    </div>
                    <div class="form-group full-width">
                        <label>Triggers (comma-separated)</label>
                        <input type="text" id="custom-skill-triggers" value="${skillData.triggers?.join(', ') || ''}">
                    </div>
                    <div class="form-group full-width">
                        <label>Content (skill.md)</label>
                        <textarea id="custom-skill-content" style="min-height: 250px; font-family: monospace; font-size: 0.75rem;">${template.skill_md}</textarea>
                    </div>
                </div>
            `, `
                <button class="btn" onclick="renderTemplatePreview(skillEditorState.fromTemplate)">Back</button>
                <button class="btn primary" onclick="saveCustomizedTemplate()">Save Skill</button>
            `);
}

async function saveCustomizedTemplate() {
    const agentId = state.selectedAgent;
    const skillId = document.getElementById('custom-skill-id').value.trim();
    const name = document.getElementById('custom-skill-name').value.trim();
    const description = document.getElementById('custom-skill-description').value.trim();
    const triggers = document.getElementById('custom-skill-triggers').value.split(',').map(t => t.trim()).filter(t => t);
    const content = document.getElementById('custom-skill-content').value;

    if (!name) {
        showToast('Name is required', 'warning');
        return;
    }

    const template = skillEditorState.fromTemplate;
    const skillData = {
        ...template.skill_json,
        id: skillId,
        name: name,
        description: description,
        triggers: triggers,
        source: {
            type: "template",
            template_id: template.id,
            customized: true,
            created_at: new Date().toISOString()
        }
    };

    // Use the editor save endpoint
    const result = await api('POST', '/skills/editor/save', {
        agent_id: agentId,
        skill_id: skillId,
        skill_files: {
            skill_json: skillData,
            skill_md: content,
            additional_files: {}
        },
        form: null,
        answers: {}
    });

    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" created!`, 'success');
        closeModal();
        loadAgentSkills();
    }
}

function renderSkillEditorStep1() {
    showModal('Create New Skill - Describe Your Skill', `
                <p style="margin-bottom: 1rem; color: #57534e;">Describe what you want this skill to do. The AI will analyze your intent and generate a customized form.</p>
                <div class="form-group">
                    <label>What should this skill do?</label>
                    <textarea id="skill-intent" placeholder="Example: I want a skill that summarizes documents and extracts key points..." style="min-height: 120px;">${skillEditorState.intent}</textarea>
                </div>
                <div id="skill-editor-error" style="color: #dc2626; margin-top: 0.5rem; display: none;"></div>
            `, `
                <button class="btn" onclick="renderSkillEditorStep0()">Back</button>
                <button class="btn primary" onclick="generateHypothesis()">
                    <span id="hypothesis-btn-text">Next: Generate Form</span>
                    <span id="hypothesis-btn-loading" class="loading" style="display: none; margin-left: 0.5rem;"></span>
                </button>
            `);
}

async function generateHypothesis() {
    const intent = document.getElementById('skill-intent').value.trim();
    if (!intent) {
        document.getElementById('skill-editor-error').textContent = 'Please describe what the skill should do.';
        document.getElementById('skill-editor-error').style.display = 'block';
        return;
    }

    skillEditorState.intent = intent;

    // Show loading
    document.getElementById('hypothesis-btn-text').textContent = 'Generating...';
    document.getElementById('hypothesis-btn-loading').style.display = 'inline-block';
    document.getElementById('skill-editor-error').style.display = 'none';

    const result = await api('POST', '/skills/editor/hypothesis', { intent });

    if (!result) {
        document.getElementById('hypothesis-btn-text').textContent = 'Next: Generate Form';
        document.getElementById('hypothesis-btn-loading').style.display = 'none';
        document.getElementById('skill-editor-error').textContent = 'Failed to generate hypothesis. Please try again.';
        document.getElementById('skill-editor-error').style.display = 'block';
        return;
    }

    skillEditorState.hypothesis = result.hypothesis;
    skillEditorState.form = result;
    skillEditorState.step = 2;

    renderSkillEditorStep2();
}

function renderSkillEditorStep2() {
    const h = skillEditorState.hypothesis;
    const fields = skillEditorState.form.fields || [];

    // Pre-populate answers with defaults
    fields.forEach(f => {
        if (skillEditorState.answers[f.id] === undefined && f.default !== undefined) {
            skillEditorState.answers[f.id] = f.default;
        }
    });

    const fieldsHtml = fields.map(f => renderFormField(f)).join('');

    showModal(`${skillEditorState.editingSkillId ? 'Edit' : 'Create'} Skill - Step 2`, `
                <div style="background: #f5f5f4; padding: 1rem; margin-bottom: 1rem; border: 1px solid #e7e5e4;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">${h.name}</div>
                    <div style="font-size: 0.875rem; color: #57534e; margin-bottom: 0.5rem;">${h.description}</div>
                    <div style="font-size: 0.75rem; color: #78716c;">
                        ID: ${h.suggested_id} | Tools: ${h.suggested_tools?.join(', ') || 'None'}
                    </div>
                </div>
                <div class="form-group" style="margin-bottom: 1rem;">
                    <label>Skill ID</label>
                    <input type="text" id="skill-editor-id" value="${skillEditorState.editingSkillId || generateId('sk')}" disabled style="background: #f5f5f4; color: #78716c;">
                </div>
                <p style="margin-bottom: 1rem; color: #57534e; font-size: 0.875rem;">Answer these questions to customize your skill:</p>
                <div id="skill-editor-fields">
                    ${fieldsHtml}
                </div>
                <div class="form-group" style="margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #e7e5e4;">
                    <label>Other / Additional Instructions</label>
                    <div style="font-size: 0.75rem; color: #78716c; margin-bottom: 0.25rem;">Add any additional requirements, context, or instructions not covered above.</div>
                    <textarea id="skill-editor-other" rows="4" placeholder="Any additional instructions or requirements..." onchange="updateFieldAnswer('_other', this.value)">${skillEditorState.answers['_other'] || ''}</textarea>
                </div>
                <div id="skill-editor-error2" style="color: #dc2626; margin-top: 0.5rem; display: none;"></div>
            `, `
                <button class="btn" onclick="renderSkillEditorStep1()">Back</button>
                <button class="btn primary" onclick="generateAndSaveSkill()">
                    <span id="save-btn-text">${skillEditorState.editingSkillId ? 'Update Skill' : 'Create Skill'}</span>
                    <span id="save-btn-loading" class="loading" style="display: none; margin-left: 0.5rem;"></span>
                </button>
            `);
}

function renderFormField(field) {
    const value = skillEditorState.answers[field.id];
    const customValue = skillEditorState.answers[`${field.id}_custom`];
    const isSkipped = skillEditorState.skippedFields?.includes(field.id);
    const isCustom = skillEditorState.customFields?.includes(field.id);
    const required = field.required ? '<span style="color: #dc2626;">*</span>' : '';
    let inputHtml = '';

    // If skipped, show minimal UI
    if (isSkipped) {
        return `
                    <div class="form-group" id="field-wrapper-${field.id}" style="margin-bottom: 1rem; opacity: 0.5; background: #f5f5f4; padding: 0.75rem; border-radius: 4px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <span style="text-decoration: line-through; color: #78716c;">${field.question}</span>
                            <button type="button" class="btn" style="padding: 0.25rem 0.5rem; font-size: 0.75rem;" onclick="unskipField('${field.id}')">Restore</button>
                        </div>
                    </div>
                `;
    }

    // Build the input based on type
    switch (field.type) {
        case 'text':
            inputHtml = `<input type="text" id="field-${field.id}" value="${value || ''}" placeholder="${field.placeholder || ''}" onchange="updateFieldAnswer('${field.id}', this.value)">`;
            break;
        case 'textarea':
            inputHtml = `<textarea id="field-${field.id}" placeholder="${field.placeholder || ''}" onchange="updateFieldAnswer('${field.id}', this.value)">${value || ''}</textarea>`;
            break;
        case 'number':
            inputHtml = `<input type="number" id="field-${field.id}" value="${value || ''}" onchange="updateFieldAnswer('${field.id}', this.value)">`;
            break;
        case 'checkbox':
            inputHtml = `<label style="display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0;">
                        <input type="checkbox" id="field-${field.id}" ${value ? 'checked' : ''} onchange="updateFieldAnswer('${field.id}', this.checked)">
                        <span>Yes</span>
                    </label>`;
            break;
        case 'select':
            const options = (field.options || []).map(opt =>
                `<option value="${opt}" ${value === opt && !isCustom ? 'selected' : ''}>${opt}</option>`
            ).join('');
            inputHtml = `
                        <select id="field-${field.id}" onchange="handleSelectChange('${field.id}', this.value)" ${isCustom ? 'style="display:none;"' : ''}>
                            <option value="">-- Select --</option>
                            ${options}
                            <option value="__custom__" ${isCustom ? 'selected' : ''}>Other (custom answer)...</option>
                        </select>
                        <input type="text" id="field-${field.id}-custom" value="${customValue || ''}" placeholder="Enter your custom answer..."
                            style="margin-top: 0.5rem; ${isCustom ? '' : 'display: none;'}"
                            onchange="updateFieldAnswer('${field.id}_custom', this.value); updateFieldAnswer('${field.id}', this.value)">
                        ${isCustom ? `<button type="button" style="margin-top: 0.25rem; font-size: 0.75rem; padding: 0.25rem 0.5rem;" class="btn" onclick="cancelCustomField('${field.id}')">Use predefined options</button>` : ''}
                    `;
            break;
        case 'multiselect':
            const checkboxes = (field.options || []).map(opt => {
                const checked = Array.isArray(value) && value.includes(opt);
                return `<label style="display: inline-flex; align-items: center; gap: 0.25rem; margin-right: 1rem; margin-bottom: 0.25rem;">
                            <input type="checkbox" data-field="${field.id}" value="${opt}" ${checked ? 'checked' : ''} onchange="updateMultiselectAnswer('${field.id}')">
                            ${opt}
                        </label>`;
            }).join('');
            inputHtml = `
                        <div style="padding: 0.5rem 0;">${checkboxes}</div>
                        <div style="margin-top: 0.5rem;">
                            <label style="display: inline-flex; align-items: center; gap: 0.25rem; color: #2563eb; font-size: 0.875rem;">
                                <input type="checkbox" id="field-${field.id}-custom-toggle" ${isCustom ? 'checked' : ''} onchange="toggleCustomMultiselect('${field.id}', this.checked)">
                                Add custom option
                            </label>
                            <input type="text" id="field-${field.id}-custom" value="${customValue || ''}" placeholder="Enter custom option..."
                                style="margin-top: 0.25rem; ${isCustom ? '' : 'display: none;'}"
                                onchange="updateFieldAnswer('${field.id}_custom', this.value)">
                        </div>
                    `;
            break;
        default:
            inputHtml = `<input type="text" id="field-${field.id}" value="${value || ''}" onchange="updateFieldAnswer('${field.id}', this.value)">`;
    }

    return `
                <div class="form-group" id="field-wrapper-${field.id}" style="margin-bottom: 1rem; position: relative;">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start;">
                        <label style="flex: 1;">${field.question} ${required}</label>
                        <button type="button" title="Skip this question" style="background: none; border: none; color: #78716c; cursor: pointer; padding: 0 0.25rem; font-size: 1rem;" onclick="skipField('${field.id}')">&times;</button>
                    </div>
                    ${field.description ? `<div style="font-size: 0.75rem; color: #78716c; margin-bottom: 0.25rem;">${field.description}</div>` : ''}
                    ${inputHtml}
                </div>
            `;
}

function updateFieldAnswer(fieldId, value) {
    skillEditorState.answers[fieldId] = value;
}

function updateMultiselectAnswer(fieldId) {
    const checkboxes = document.querySelectorAll(`input[data-field="${fieldId}"]:checked`);
    skillEditorState.answers[fieldId] = Array.from(checkboxes).map(cb => cb.value);
}

function skipField(fieldId) {
    if (!skillEditorState.skippedFields.includes(fieldId)) {
        skillEditorState.skippedFields.push(fieldId);
    }
    // Re-render the field
    const field = skillEditorState.form.fields.find(f => f.id === fieldId);
    if (field) {
        const wrapper = document.getElementById(`field-wrapper-${fieldId}`);
        if (wrapper) {
            wrapper.outerHTML = renderFormField(field);
        }
    }
}

function unskipField(fieldId) {
    skillEditorState.skippedFields = skillEditorState.skippedFields.filter(id => id !== fieldId);
    // Re-render the field
    const field = skillEditorState.form.fields.find(f => f.id === fieldId);
    if (field) {
        const wrapper = document.getElementById(`field-wrapper-${fieldId}`);
        if (wrapper) {
            wrapper.outerHTML = renderFormField(field);
        }
    }
}

function handleSelectChange(fieldId, value) {
    if (value === '__custom__') {
        // Switch to custom mode
        if (!skillEditorState.customFields.includes(fieldId)) {
            skillEditorState.customFields.push(fieldId);
        }
        // Re-render the field to show custom input
        const field = skillEditorState.form.fields.find(f => f.id === fieldId);
        if (field) {
            const wrapper = document.getElementById(`field-wrapper-${fieldId}`);
            if (wrapper) {
                wrapper.outerHTML = renderFormField(field);
            }
            // Focus the custom input
            setTimeout(() => {
                const customInput = document.getElementById(`field-${fieldId}-custom`);
                if (customInput) customInput.focus();
            }, 50);
        }
    } else {
        // Regular selection
        skillEditorState.answers[fieldId] = value;
    }
}

function cancelCustomField(fieldId) {
    skillEditorState.customFields = skillEditorState.customFields.filter(id => id !== fieldId);
    skillEditorState.answers[fieldId] = '';  // Reset to empty
    skillEditorState.answers[`${fieldId}_custom`] = '';
    // Re-render the field
    const field = skillEditorState.form.fields.find(f => f.id === fieldId);
    if (field) {
        const wrapper = document.getElementById(`field-wrapper-${fieldId}`);
        if (wrapper) {
            wrapper.outerHTML = renderFormField(field);
        }
    }
}

function toggleCustomMultiselect(fieldId, checked) {
    if (checked) {
        if (!skillEditorState.customFields.includes(fieldId)) {
            skillEditorState.customFields.push(fieldId);
        }
        // Show custom input
        const customInput = document.getElementById(`field-${fieldId}-custom`);
        if (customInput) {
            customInput.style.display = 'block';
            customInput.focus();
        }
    } else {
        skillEditorState.customFields = skillEditorState.customFields.filter(id => id !== fieldId);
        skillEditorState.answers[`${fieldId}_custom`] = '';
        // Hide custom input
        const customInput = document.getElementById(`field-${fieldId}-custom`);
        if (customInput) {
            customInput.style.display = 'none';
            customInput.value = '';
        }
    }
}

async function generateAndSaveSkill() {
    const skillId = document.getElementById('skill-editor-id').value.trim();  // Auto-generated

    // Validate required fields (skip those marked as skipped)
    const fields = skillEditorState.form.fields || [];
    for (const field of fields) {
        // Skip validation for skipped fields
        if (skillEditorState.skippedFields.includes(field.id)) {
            continue;
        }
        if (field.required) {
            const value = skillEditorState.answers[field.id];
            if (value === undefined || value === '' || (Array.isArray(value) && value.length === 0)) {
                document.getElementById('skill-editor-error2').textContent = `Please answer: ${field.question}`;
                document.getElementById('skill-editor-error2').style.display = 'block';
                return;
            }
        }
    }

    // Remove skipped fields from the form before saving
    const filteredForm = {
        ...skillEditorState.form,
        fields: fields.filter(f => !skillEditorState.skippedFields.includes(f.id))
    };

    // Clean up answers - remove skipped field answers
    const filteredAnswers = { ...skillEditorState.answers };
    for (const fieldId of skillEditorState.skippedFields) {
        delete filteredAnswers[fieldId];
        delete filteredAnswers[`${fieldId}_custom`];
    }

    // Show loading
    document.getElementById('save-btn-text').textContent = 'Generating...';
    document.getElementById('save-btn-loading').style.display = 'inline-block';
    document.getElementById('skill-editor-error2').style.display = 'none';

    const agentId = state.selectedAgent;

    if (skillEditorState.editingSkillId) {
        // Update existing skill (use filtered form and answers)
        const result = await api('PUT', `/agents/${agentId}/skills/${skillId}/editor`, {
            form: filteredForm,
            answers: filteredAnswers
        });

        if (result && result.status === 'ok') {
            showToast(`Skill "${skillId}" updated successfully!`, 'success');
            closeModal();
            loadAgentSkills();
        } else {
            document.getElementById('save-btn-text').textContent = 'Update Skill';
            document.getElementById('save-btn-loading').style.display = 'none';
            document.getElementById('skill-editor-error2').textContent = 'Failed to update skill. Please try again.';
            document.getElementById('skill-editor-error2').style.display = 'block';
        }
    } else {
        // Generate skill files (use filtered form and answers)
        const generateResult = await api('POST', '/skills/editor/generate', {
            form: filteredForm,
            answers: filteredAnswers,
            skill_id: skillId
        });

        if (!generateResult) {
            document.getElementById('save-btn-text').textContent = 'Create Skill';
            document.getElementById('save-btn-loading').style.display = 'none';
            document.getElementById('skill-editor-error2').textContent = 'Failed to generate skill. Please try again.';
            document.getElementById('skill-editor-error2').style.display = 'block';
            return;
        }

        // Save skill
        document.getElementById('save-btn-text').textContent = 'Saving...';

        const saveResult = await api('POST', '/skills/editor/save', {
            agent_id: agentId,
            skill_id: skillId,
            skill_files: generateResult,
            form: filteredForm,
            answers: filteredAnswers
        });

        if (saveResult && saveResult.status === 'ok') {
            showToast(`Skill "${skillId}" created successfully!`, 'success');
            closeModal();
            loadAgentSkills();
        } else {
            document.getElementById('save-btn-text').textContent = 'Create Skill';
            document.getElementById('save-btn-loading').style.display = 'none';
            document.getElementById('skill-editor-error2').textContent = 'Failed to save skill. Please try again.';
            document.getElementById('skill-editor-error2').style.display = 'block';
        }
    }
}

async function editSkill(skillId) {
    skillEditorState = {
        step: 2,
        intent: '',
        hypothesis: null,
        form: null,
        answers: {},
        editingSkillId: skillId,
        skippedFields: [],
        customFields: []
    };

    const agentId = state.selectedAgent;
    const data = await api('GET', `/agents/${agentId}/skills/${skillId}/editor`);

    if (!data) {
        showToast('Failed to load skill for editing', 'error');
        return;
    }

    // Reconstruct form from saved data
    skillEditorState.form = data.form;
    skillEditorState.hypothesis = data.form.hypothesis;
    skillEditorState.answers = data.answers || {};

    renderSkillEditorStep2();
}

async function loadSkillForEditing(skillId) {
    await editSkill(skillId);
}

// ====================================================================
// AGENT TASKS
// ====================================================================
async function loadAgentTasks() {
    const container = document.getElementById('agent-tasks-list');
    const statsBar = document.getElementById('tasks-stats-bar');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const agentId = state.selectedAgent;
    const showDisabled = document.getElementById('show-disabled-tasks')?.checked || false;
    const data = await api('GET', `/agents/${agentId}/tasks`);

    if (!data || !data.tasks) {
        container.innerHTML = '<div class="empty-state">Failed to load tasks</div>';
        statsBar.style.display = 'none';
        return;
    }

    // Filter tasks
    let tasks = data.tasks;
    if (!showDisabled) {
        tasks = tasks.filter(t => t.enabled !== false);
    }

    // Update stats
    const enabledCount = data.tasks.filter(t => t.enabled).length;
    const lastRuns = data.tasks.filter(t => t.last_run).map(t => new Date(t.last_run));
    const mostRecent = lastRuns.length > 0 ? new Date(Math.max(...lastRuns)) : null;

    document.getElementById('tasks-total').textContent = data.tasks.length;
    document.getElementById('tasks-enabled').textContent = enabledCount;
    document.getElementById('tasks-last-run').textContent = mostRecent ? formatRelativeTime(mostRecent) : '--';
    statsBar.style.display = data.tasks.length > 0 ? 'flex' : 'none';

    if (tasks.length === 0) {
        container.innerHTML = `<div class="empty-state">
                    ${data.tasks.length > 0 ? 'No enabled tasks. Check "Show Disabled" to see all.' : 'No tasks for this agent.<br><br>Click "+ Create Task" to schedule automated work.'}
                </div>`;
        return;
    }

    container.innerHTML = tasks.map(task => {
        const scheduleInfo = formatScheduleInfo(task);
        const skillBadge = task.skill_id
            ? `<span class="card-badge" title="Linked skill">${task.skill_id}</span>`
            : `<span class="card-badge" style="opacity: 0.5;" title="Skill auto-matched at runtime">auto</span>`;
        return `
                <div class="card ${task.enabled ? '' : 'disabled'}">
                    <div class="card-header">
                        <span class="card-title">${task.name || task.task_id}</span>
                        <span class="card-badge ${task.enabled ? 'success' : ''}">${task.enabled ? 'ENABLED' : 'DISABLED'}</span>
                        <span class="card-badge">${task.schedule_type?.toUpperCase() || 'UNKNOWN'}</span>
                        ${skillBadge}
                    </div>
                    <div class="card-description">${task.description || scheduleInfo}</div>
                    <div class="card-meta">
                        ID: ${task.task_id} | Runs: ${task.run_count || 0}<br>
                        Next: ${task.next_run ? formatDateTime(task.next_run) : 'N/A'}
                        ${task.last_run ? ` | Last: ${formatRelativeTime(new Date(task.last_run))}` : ''}
                    </div>
                    <div class="card-actions">
                        <button class="btn" onclick="viewTaskDetails('${task.task_id}')">Details</button>
                        <button class="btn" onclick="viewTaskRuns('${task.task_id}')">Runs</button>
                        <button class="btn success" onclick="triggerTask('${task.task_id}')">Trigger</button>
                        <button class="btn" onclick="showEditTaskModal('${task.task_id}')"">Edit</button>
                        ${task.enabled
                ? `<button class="btn" onclick="disableTask('${task.task_id}')">Disable</button>`
                : `<button class="btn" onclick="enableTask('${task.task_id}')">Enable</button>`
            }
                        <button class="btn danger" onclick="deleteTask('${task.task_id}')">Delete</button>
                    </div>
                </div>
            `}).join('');
}

function formatScheduleInfo(task) {
    switch (task.schedule_type) {
        case 'interval':
            const secs = task.interval_seconds || 3600;
            if (secs >= 86400) return `Every ${Math.round(secs / 86400)} day(s)`;
            if (secs >= 3600) return `Every ${Math.round(secs / 3600)} hour(s)`;
            if (secs >= 60) return `Every ${Math.round(secs / 60)} minute(s)`;
            return `Every ${secs} seconds`;
        case 'cron':
            return `Cron: ${task.cron_expression || 'N/A'}`;
        case 'once':
            return `Once at: ${task.run_at || 'scheduled'}`;
        case 'event_only':
            return `Event-triggered: ${(task.events || []).join(', ') || 'manual only'}`;
        default:
            return task.schedule_type || 'Unknown schedule';
    }
}

function formatDateTime(isoString) {
    if (!isoString) return 'N/A';
    const d = new Date(isoString);
    return d.toLocaleString();
}

function formatRelativeTime(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffSecs = Math.floor(diffMs / 1000);
    if (diffSecs < 60) return 'Just now';
    if (diffSecs < 3600) return `${Math.floor(diffSecs / 60)}m ago`;
    if (diffSecs < 86400) return `${Math.floor(diffSecs / 3600)}h ago`;
    return `${Math.floor(diffSecs / 86400)}d ago`;
}

async function showCreateTaskModal() {
    await showTaskModal(null);
}

async function showEditTaskModal(taskId) {
    const task = await api('GET', `/api/tasks/${taskId}`);
    if (task) {
        showTaskModal(task);
    }
}

async function showTaskModal(task = null) {
    const isEdit = task !== null;
    const title = isEdit ? `Edit Task: ${task.task_id}` : 'Create New Task';

    const scheduleType = task?.schedule?.type || task?.schedule_type || 'interval';
    const intervalSecs = task?.schedule?.interval_seconds || task?.interval_seconds || 3600;
    const cronExpr = task?.schedule?.cron_expression || task?.cron_expression || '';
    const runAt = task?.schedule?.run_at || '';
    const events = (task?.schedule?.events || task?.events || []).join(', ');
    const timeoutSecs = task?.execution?.timeout_seconds || 300;
    const maxTurns = task?.execution?.max_turns || 15;
    const content = task?.task_md_content || task?.content || '';
    const skillId = task?.execution?.skill_id || task?.skill_id || '';
    const enabled = task?.status?.enabled ?? task?.enabled ?? true;

    // Load available skills for the dropdown
    const agentId = state.selectedAgent;
    let skillOptions = '<option value="">Auto-match (let system choose)</option>';
    try {
        const skillsData = await api('GET', `/agents/${agentId}/skills`);
        if (skillsData) {
            // API returns private_skills and global_skills arrays
            const privateSkills = skillsData.private_skills || [];
            const globalSkills = skillsData.global_skills || [];

            if (privateSkills.length > 0) {
                skillOptions += '<optgroup label="Agent Private Skills">';
                for (const skill of privateSkills) {
                    const selected = skill.id === skillId ? 'selected' : '';
                    skillOptions += `<option value="${skill.id}" ${selected}>${skill.name}</option>`;
                }
                skillOptions += '</optgroup>';
            }
            if (globalSkills.length > 0) {
                skillOptions += '<optgroup label="Global Skills">';
                for (const skill of globalSkills) {
                    const selected = skill.id === skillId ? 'selected' : '';
                    skillOptions += `<option value="${skill.id}" ${selected}>${skill.name}</option>`;
                }
                skillOptions += '</optgroup>';
            }
        }
    } catch (e) {
        console.warn('Failed to load skills for task modal:', e);
    }

    // Auto-generate ID for new tasks
    const autoTaskId = isEdit ? task.task_id : generateId('tk');
    showModal(title, `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Task ID</label>
                        <input type="text" id="task-form-id" value="${autoTaskId}" disabled style="background: #f5f5f4; color: #78716c;">
                    </div>
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="task-form-name" value="${task?.name || ''}" placeholder="My Task">
                    </div>
                    <div class="form-group full-width">
                        <label>Description</label>
                        <input type="text" id="task-form-description" value="${task?.description || ''}" placeholder="What does this task do?">
                    </div>
                    <div class="form-group">
                        <label>Schedule Type</label>
                        <select id="task-form-schedule-type" onchange="updateTaskFormSchedule()">
                            <option value="interval" ${scheduleType === 'interval' ? 'selected' : ''}>Interval (recurring)</option>
                            <option value="cron" ${scheduleType === 'cron' ? 'selected' : ''}>Cron Expression</option>
                            <option value="once" ${scheduleType === 'once' ? 'selected' : ''}>Once (single run)</option>
                            <option value="event_only" ${scheduleType === 'event_only' ? 'selected' : ''}>Event Only (manual/trigger)</option>
                        </select>
                    </div>
                    <div class="form-group" id="task-form-interval-group" style="${scheduleType !== 'interval' ? 'display:none;' : ''}">
                        <label>Interval</label>
                        <div style="display: flex; gap: 0.5rem;">
                            <input type="number" id="task-form-interval-value" value="${Math.floor(intervalSecs / 60)}" min="1" style="flex: 1;">
                            <select id="task-form-interval-unit" style="width: 100px;">
                                <option value="60" ${intervalSecs < 3600 ? 'selected' : ''}>Minutes</option>
                                <option value="3600" ${intervalSecs >= 3600 && intervalSecs < 86400 ? 'selected' : ''}>Hours</option>
                                <option value="86400" ${intervalSecs >= 86400 ? 'selected' : ''}>Days</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group" id="task-form-cron-group" style="${scheduleType !== 'cron' ? 'display:none;' : ''}">
                        <label>Cron Expression</label>
                        <input type="text" id="task-form-cron" value="${cronExpr}" placeholder="0 9 * * *">
                        <div style="font-size: 0.7rem; color: #78716c; margin-top: 0.25rem;">min hour day month weekday (e.g., "0 9 * * 1-5" = 9am weekdays)</div>
                    </div>
                    <div class="form-group" id="task-form-once-group" style="${scheduleType !== 'once' ? 'display:none;' : ''}">
                        <label>Run At</label>
                        <input type="datetime-local" id="task-form-run-at" value="${runAt ? runAt.substring(0, 16) : ''}">
                    </div>
                    <div class="form-group" id="task-form-events-group" style="${scheduleType !== 'event_only' ? 'display:none;' : ''}">
                        <label>Events (comma-separated)</label>
                        <input type="text" id="task-form-events" value="${events}" placeholder="event1, event2">
                    </div>
                    <div class="form-group">
                        <label>Skill</label>
                        <select id="task-form-skill">
                            ${skillOptions}
                        </select>
                        <div style="font-size: 0.7rem; color: #78716c; margin-top: 0.25rem;">Select a skill or let the system auto-match at runtime</div>
                    </div>
                    <div class="form-group">
                        <label>Enabled</label>
                        <select id="task-form-enabled">
                            <option value="true" ${enabled ? 'selected' : ''}>Yes</option>
                            <option value="false" ${!enabled ? 'selected' : ''}>No</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Timeout (seconds)</label>
                        <input type="number" id="task-form-timeout" value="${timeoutSecs}" min="30" max="3600">
                    </div>
                    <div class="form-group">
                        <label>Max Turns</label>
                        <input type="number" id="task-form-max-turns" value="${maxTurns}" min="1" max="100">
                    </div>
                    <div class="form-group full-width">
                        <label>Task Instructions (Markdown) *</label>
                        <textarea id="task-form-content" style="min-height: 200px; font-family: monospace; font-size: 0.8rem;" placeholder="# Task Instructions\n\nDescribe what the agent should do...">${content}</textarea>
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="saveTask(${isEdit ? `'${task.task_id}'` : 'null'})">${isEdit ? 'Save Changes' : 'Create Task'}</button>
            `);
}

function updateTaskFormSchedule() {
    const type = document.getElementById('task-form-schedule-type').value;
    document.getElementById('task-form-interval-group').style.display = type === 'interval' ? '' : 'none';
    document.getElementById('task-form-cron-group').style.display = type === 'cron' ? '' : 'none';
    document.getElementById('task-form-once-group').style.display = type === 'once' ? '' : 'none';
    document.getElementById('task-form-events-group').style.display = type === 'event_only' ? '' : 'none';
}

async function saveTask(existingTaskId) {
    const isEdit = existingTaskId !== null;
    const agentId = state.selectedAgent;

    const taskId = document.getElementById('task-form-id').value.trim();
    const name = document.getElementById('task-form-name').value.trim();
    const description = document.getElementById('task-form-description').value.trim();
    const scheduleType = document.getElementById('task-form-schedule-type').value;
    const content = document.getElementById('task-form-content').value;
    const timeoutSecs = parseInt(document.getElementById('task-form-timeout').value) || 300;
    const maxTurns = parseInt(document.getElementById('task-form-max-turns').value) || 15;
    const skillId = document.getElementById('task-form-skill').value || null;
    const enabled = document.getElementById('task-form-enabled').value === 'true';

    if (!name) {
        showToast('Name is required', 'warning');
        return;
    }
    if (!content.trim()) {
        showToast('Task instructions are required', 'warning');
        return;
    }

    // Build schedule based on type
    let intervalSeconds = null;
    let cronExpression = null;
    let runAt = null;
    let events = [];

    if (scheduleType === 'interval') {
        const intervalValue = parseInt(document.getElementById('task-form-interval-value').value) || 1;
        const intervalUnit = parseInt(document.getElementById('task-form-interval-unit').value) || 60;
        intervalSeconds = intervalValue * intervalUnit;
    } else if (scheduleType === 'cron') {
        cronExpression = document.getElementById('task-form-cron').value.trim();
        if (!cronExpression) {
            showToast('Cron expression is required', 'warning');
            return;
        }
    } else if (scheduleType === 'once') {
        runAt = document.getElementById('task-form-run-at').value;
        if (!runAt) {
            showToast('Run at time is required', 'warning');
            return;
        }
    } else if (scheduleType === 'event_only') {
        const eventsStr = document.getElementById('task-form-events').value.trim();
        events = eventsStr ? eventsStr.split(',').map(e => e.trim()).filter(e => e) : [];
    }

    const body = {
        task_id: taskId,
        name: name,
        description: description,
        schedule_type: scheduleType,
        content: content,
        timeout_seconds: timeoutSecs,
        max_turns: maxTurns,
        skill_id: skillId,  // null = auto-match at runtime
        enabled: enabled,
        agent_id: agentId
    };

    if (intervalSeconds) body.interval_seconds = intervalSeconds;
    if (cronExpression) body.cron_expression = cronExpression;
    if (runAt) body.run_at = new Date(runAt).toISOString();
    if (events.length > 0) body.events = events;

    let result;
    if (isEdit) {
        result = await api('PUT', `/api/tasks/${existingTaskId}`, body);
    } else {
        result = await api('POST', `/agents/${agentId}/tasks`, body);
    }

    if (result && (result.status === 'ok' || result.task_id)) {
        showToast(`Task "${name}" ${isEdit ? 'updated' : 'created'}`, 'success');
        closeModal();
        loadAgentTasks();
    }
}

async function viewTaskDetails(taskId) {
    const task = await api('GET', `/api/tasks/${taskId}`);
    if (!task) return;

    const scheduleInfo = formatScheduleInfo(task);
    const skillId = task.execution?.skill_id || task.skill_id;
    const skillDisplay = skillId ? skillId : '<span style="color: #78716c; font-style: italic;">Auto-match at runtime</span>';
    const status = task.status?.enabled ?? task.enabled;
    const content = task.task_md_content || task.content || 'No content';

    showModal(`Task: ${task.name || taskId}`, `
                <div class="stats-bar" style="margin-bottom: 1rem;">
                    <div class="stat-item"><span class="stat-label">Status:</span><span class="stat-value ${status ? 'ok' : 'error'}">${status ? 'ENABLED' : 'DISABLED'}</span></div>
                    <div class="stat-item"><span class="stat-label">Runs:</span><span class="stat-value">${task.status?.run_count || task.run_count || 0}</span></div>
                    <div class="stat-item"><span class="stat-label">Last Status:</span><span class="stat-value">${task.status?.last_status || 'N/A'}</span></div>
                </div>
                <div class="form-group"><label>ID</label><div>${task.task_id}</div></div>
                <div class="form-group"><label>Description</label><div>${task.description || 'No description'}</div></div>
                <div class="form-group"><label>Schedule</label><div>${scheduleInfo}</div></div>
                <div class="form-group"><label>Skill</label><div>${skillDisplay}</div></div>
                <div class="form-group"><label>Next Run</label><div>${task.status?.next_run || task.next_run ? formatDateTime(task.status?.next_run || task.next_run) : 'Not scheduled'}</div></div>
                <div class="form-group"><label>Last Run</label><div>${task.status?.last_run || task.last_run ? formatDateTime(task.status?.last_run || task.last_run) : 'Never'}</div></div>
                <div class="form-group"><label>Timeout</label><div>${task.execution?.timeout_seconds || 300} seconds</div></div>
                <div class="form-group"><label>Max Turns</label><div>${task.execution?.max_turns || 15}</div></div>
                <div class="form-group"><label>Instructions</label></div>
                <div class="response-box" style="max-height: 200px; font-size: 0.75rem;">${escapeHtml(content)}</div>
            `, `
                <button class="btn" onclick="closeModal()">Close</button>
                <button class="btn" onclick="showEditTaskModal('${taskId}'); ">Edit</button>
                <button class="btn success" onclick="triggerTask('${taskId}'); closeModal();">Trigger Now</button>
            `);
}

async function viewTaskRuns(taskId) {
    const data = await api('GET', `/api/tasks/${taskId}/runs?limit=20`);
    if (!data) return;

    const runs = data.runs || [];
    const runsHtml = runs.length === 0
        ? '<div class="empty-state">No run history</div>'
        : runs.map(r => {
            const skillId = r.execution?.skill_id;
            const skillMatched = r.execution?.skill_matched;
            const skillInfo = skillId
                ? `<br>Skill: ${skillId} ${skillMatched ? '(auto-matched)' : '(explicit)'}`
                : '';
            const response = r.result?.response || '';
            const needsInput = response && (
                response.includes('?') ||
                response.toLowerCase().includes('please provide') ||
                response.toLowerCase().includes('need') ||
                response.toLowerCase().includes('could you')
            );
            return `
                    <div style="border: 1px solid #e7e5e4; padding: 0.75rem; margin-bottom: 0.75rem; border-radius: 4px;">
                        <div style="display: flex; justify-content: space-between; margin-bottom: 0.5rem;">
                            <span style="font-weight: 600; font-size: 0.875rem;">${r.started_at ? formatDateTime(r.started_at) : 'Unknown'}</span>
                            <span class="card-badge ${r.result?.status === 'completed' ? 'success' : r.result?.status === 'error' ? 'error' : ''}">${r.result?.status || 'unknown'}</span>
                        </div>
                        <div style="font-size: 0.75rem; color: #57534e; margin-bottom: 0.5rem;">
                            Duration: ${r.duration_ms ? `${(r.duration_ms / 1000).toFixed(1)}s` : 'N/A'} | Turns: ${r.execution?.turns || 0} | Tokens: ${r.execution?.tokens_used || 0}${skillInfo}
                        </div>
                        ${r.result?.error ? `<div style="font-size: 0.875rem; color: #dc2626; margin-top: 0.5rem; padding: 0.5rem; background: #fef2f2; border-radius: 4px;">Error: ${r.result.error}</div>` : ''}
                        ${response ? `
                            <div style="margin-top: 0.5rem;">
                                ${needsInput ? '<div style="font-size: 0.75rem; color: #d97706; margin-bottom: 0.25rem; font-weight: 600;">⚠ Agent may need more input</div>' : ''}
                                <div style="background: #f5f5f4; padding: 0.75rem; border-radius: 4px; font-size: 0.875rem; white-space: pre-wrap; max-height: 200px; overflow-y: auto;">${escapeHtml(response)}</div>
                            </div>
                        ` : ''}
                    </div>
                `}).join('');

    showModal(`Task Runs: ${taskId}`, `
                <div style="max-height: 500px; overflow-y: auto;">
                    ${runsHtml}
                </div>
                <div style="margin-top: 1rem; padding-top: 1rem; border-top: 1px solid #e7e5e4; font-size: 0.75rem; color: #78716c;">
                    Note: To continue a conversation or provide more input, use the agent's chat interface with the same session.
                </div>
            `, '<button class="btn" onclick="closeModal()">Close</button>');
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function triggerTask(taskId) {
    const result = await api('POST', `/api/tasks/${taskId}/trigger`);
    if (result) {
        showToast(`Task "${taskId}" triggered`, 'success');
        loadAgentTasks();
    }
}

async function enableTask(taskId) {
    const result = await api('PUT', `/api/tasks/${taskId}/enable`);
    if (result) {
        showToast(`Task "${taskId}" enabled`, 'success');
        loadAgentTasks();
    }
}

async function disableTask(taskId) {
    const result = await api('PUT', `/api/tasks/${taskId}/disable`);
    if (result) {
        showToast(`Task "${taskId}" disabled`, 'success');
        loadAgentTasks();
    }
}

async function deleteTask(taskId) {
    if (!confirm(`Delete task "${taskId}"?\n\nThis will remove the task and its run history.`)) return;
    const result = await api('DELETE', `/api/tasks/${taskId}`);
    if (result) {
        showToast(`Task "${taskId}" deleted`, 'success');
        loadAgentTasks();
    }
}

// ====================================================================
// AGENT MEMORY
// ====================================================================
async function loadAgentMemory() {
    const agentId = state.selectedAgent;
    const topicsData = await api('GET', `/agents/${agentId}/memory/topics`);
    if (topicsData && topicsData.topics) {
        const select = document.getElementById('memory-topic');
        select.innerHTML = '<option value="">All Topics</option>' +
            topicsData.topics.map(t => {
                const id = typeof t === 'string' ? t : t.id;
                return `<option value="${id}">${id}</option>`;
            }).join('');
        const topicsDiv = document.getElementById('agent-memory-topics');
        topicsDiv.innerHTML = topicsData.topics.map(t => {
            const id = typeof t === 'string' ? t : t.id;
            return `<span class="card-badge" style="margin: 0.25rem;">${id}</span>`;
        }).join('') || '<span class="card-badge">No topics</span>';
    }
    const statsData = await api('GET', `/agents/${agentId}/memory/stats`);
    if (statsData) {
        const statsDiv = document.getElementById('agent-memory-stats');
        statsDiv.innerHTML = `
                    <div class="stat-item"><span class="stat-label">Size:</span><span class="stat-value">${statsData.size_mb || 0} MB</span></div>
                    <div class="stat-item"><span class="stat-label">Limit:</span><span class="stat-value">${statsData.max_mb || 100} MB</span></div>
                    <div class="stat-item"><span class="stat-label">Topics:</span><span class="stat-value">${statsData.topics_count || 0}</span></div>
                    <div class="stat-item"><span class="stat-label">Sessions:</span><span class="stat-value">${statsData.sessions_count || 0}</span></div>
                `;
    }
    document.getElementById('agent-memory-results').innerHTML = '';
}

async function searchAgentMemory() {
    const agentId = state.selectedAgent;
    const query = document.getElementById('memory-query').value.trim();
    const topic = document.getElementById('memory-topic').value;
    if (!query) { showToast('Please enter a search query', 'warning'); return; }
    const url = `/agents/${agentId}/memory/search?query=${encodeURIComponent(query)}${topic ? '&topic=' + encodeURIComponent(topic) : ''}`;
    const data = await api('GET', url);
    const container = document.getElementById('agent-memory-results');
    if (!data || !data.results || data.results.length === 0) {
        container.innerHTML = '<div class="empty-state">No results found</div>';
        return;
    }
    container.innerHTML = '<div class="cards-grid">' + data.results.map(r => `
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">${r.summary || 'Untitled'}</span>
                        <span class="card-badge">${r.topic_id}</span>
                    </div>
                    <div class="card-meta">Score: ${(r.score || 0).toFixed(2)} | Keywords: ${r.keywords?.join(', ') || 'None'}</div>
                </div>
            `).join('') + '</div>';
}

// ====================================================================
// AGENT SESSIONS
// ====================================================================
async function loadAgentSessions() {
    const agentId = state.selectedAgent;
    const data = await api('GET', `/agents/${agentId}/sessions`);
    const tbody = document.getElementById('agent-sessions-table');
    if (!data || !data.sessions || data.sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center">No sessions</td></tr>';
        return;
    }
    tbody.innerHTML = data.sessions.map(s => `
                <tr>
                    <td>${s.session_id}</td>
                    <td><span class="card-badge ${s.status === 'active' ? 'success' : ''}">${s.status || 'unknown'}</span></td>
                    <td>${s.created_at ? s.created_at.substring(0, 10) + ' ' + s.created_at.substring(11, 16) : 'N/A'}</td>
                    <td>${s.message_count || 0}</td>
                    <td>
                        <button class="btn" onclick="viewAgentSession('${agentId}', '${s.session_id}')">View</button>
                        <button class="btn danger" onclick="deleteAgentSession('${agentId}', '${s.session_id}')">Delete</button>
                    </td>
                </tr>
            `).join('');
}

async function viewAgentSession(agentId, sessionId) {
    const session = await api('GET', `/agents/${agentId}/sessions/${sessionId}`);
    if (session) {
        showModal(`Session: ${sessionId}`, `
                    <pre style="white-space: pre-wrap; max-height: 400px; overflow-y: auto;">${JSON.stringify(session, null, 2)}</pre>
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

async function deleteAgentSession(agentId, sessionId) {
    if (!confirm(`Delete session ${sessionId}?`)) return;
    const result = await api('DELETE', `/agents/${agentId}/sessions/${sessionId}`);
    if (result) { showToast('Session deleted', 'success'); loadAgentSessions(); }
}

// ====================================================================
// AGENT RUNS
// ====================================================================
async function loadAgentRuns() {
    const agentId = state.selectedAgent;
    const date = document.getElementById('runs-date').value;
    let url = `/agents/${agentId}/runs?limit=50`;
    if (date) url += `&date=${encodeURIComponent(date)}`;
    const data = await api('GET', url);
    const tbody = document.getElementById('agent-runs-table');
    if (!data || !data.runs || data.runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center">No runs</td></tr>';
        return;
    }
    tbody.innerHTML = data.runs.map(r => `
                <tr>
                    <td>${r.run_id}</td>
                    <td>${r.timestamp ? r.timestamp.substring(0, 10) + ' ' + r.timestamp.substring(11, 16) : (r.date || 'N/A')}</td>
                    <td><span class="card-badge ${r.status === 'completed' ? 'success' : r.status === 'error' ? 'error' : ''}">${r.status || 'unknown'}</span></td>
                    <td>${r.turns || 0}</td>
                    <td>${r.duration_ms || 0}ms</td>
                    <td>
                        <button class="btn" onclick="viewAgentRun('${agentId}', '${r.date}', '${r.run_id}')">Details</button>
                        <button class="btn" onclick="viewAgentRunTranscript('${agentId}', '${r.date}', '${r.run_id}')">Transcript</button>
                    </td>
                </tr>
            `).join('');
}

async function viewAgentRun(agentId, date, runId) {
    const run = await api('GET', `/agents/${agentId}/runs/${date}/${runId}`);
    if (run) {
        // Use structured modal if trace data is present, otherwise raw JSON
        if (run.execution_trace || run.plan || run.turn_details || run.reflections) {
            showRunResultModal(agentId, run);
        } else {
            showModal(`Run: ${runId}`, `<pre style="white-space: pre-wrap; max-height: 400px; overflow-y: auto;">${JSON.stringify(run, null, 2)}</pre>`,
                '<button class="btn" onclick="closeModal()">Close</button>');
        }
    }
}

async function viewAgentRunTranscript(agentId, date, runId) {
    const data = await api('GET', `/agents/${agentId}/runs/${date}/${runId}/transcript`);
    if (data && data.transcript) {
        showModal(`Transcript: ${runId}`, `<pre style="white-space: pre-wrap; max-height: 500px; overflow-y: auto;">${data.transcript}</pre>`,
            '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

// ====================================================================
// AGENT RUNTIME
// ====================================================================
// Countdown timer interval for next tick
let _rtCountdownInterval = null;
// Auto-refresh interval for runtime tab
let _rtAutoRefreshInterval = null;

function startRuntimeAutoRefresh() {
    stopRuntimeAutoRefresh();
    _rtAutoRefreshInterval = setInterval(() => {
        // Only auto-refresh if we're on the runtime sub-panel
        if (state.currentPanel === 'agents' && state.currentSubPanel['agents'] === 'agents-runtime') {
            loadAgentRuntime();
        } else {
            stopRuntimeAutoRefresh();
        }
    }, 3000);
}

function stopRuntimeAutoRefresh() {
    if (_rtAutoRefreshInterval) {
        clearInterval(_rtAutoRefreshInterval);
        _rtAutoRefreshInterval = null;
    }
}

async function loadAgentRuntime() {
    const agentId = state.selectedAgent;
    const statsBar = document.getElementById('rt-agent-stats-bar');
    const timersSection = document.getElementById('rt-agent-timers-section');
    const metricsSection = document.getElementById('rt-agent-metrics-section');
    const pendingSection = document.getElementById('rt-agent-pending-section');
    const processingSection = document.getElementById('rt-agent-processing-section');
    const queueTbody = document.getElementById('rt-agent-queue-table');
    const historyTbody = document.getElementById('rt-agent-history-table');

    // Fetch all endpoints in parallel
    const [runtimeStatus, queueData, historyData, pendingData, agentInfo, todoData, hbHistoryData, issuesData] = await Promise.all([
        api('GET', `/agents/${agentId}/runtime-status`, null, { silent: true }),
        api('GET', `/agents/${agentId}/queue`, null, { silent: true }),
        api('GET', `/agents/${agentId}/events/history?limit=20`, null, { silent: true }),
        api('GET', `/agents/${agentId}/events/pending`, null, { silent: true }),
        api('GET', `/agents/${agentId}`, null, { silent: true }),
        api('GET', `/agents/${agentId}/todo`, null, { silent: true }),
        api('GET', `/agents/${agentId}/heartbeat-history?limit=20`, null, { silent: true }),
        api('GET', `/agents/${agentId}/issues`, null, { silent: true }),
    ]);

    const isActive = runtimeStatus?.active || false;

    // --- Heartbeat Button (in agent selector bar) ---
    document.getElementById('rt-heartbeat-btn').disabled = !isActive;

    // --- Status Bar ---
    const statusEl = document.getElementById('rt-agent-status');
    if (isActive) {
        statusEl.innerHTML = '<span style="color:#22c55e;font-weight:bold">Active</span>';
        statsBar.style.borderLeftColor = '#16a34a';
    } else {
        statusEl.innerHTML = '<span style="color:#9ca3af">Stopped</span>';
        statsBar.style.borderLeftColor = '#d6d3d1';
    }
    document.getElementById('rt-agent-queue-depth').textContent = runtimeStatus?.queue_depth || 0;
    document.getElementById('rt-agent-pending-count').textContent = runtimeStatus?.pending_approval_count || 0;
    document.getElementById('rt-agent-tick-count').textContent = runtimeStatus?.tick_count || 0;

    // --- Heartbeat Frequency (in stats bar) ---
    const hbFreqEl = document.getElementById('rt-agent-heartbeat-freq');
    const timersData = runtimeStatus?.timers;
    if (timersData && Object.keys(timersData).length > 0) {
        const entries = Object.entries(timersData);
        if (entries.length === 1) {
            const [skillName, t] = entries[0];
            hbFreqEl.innerHTML = isActive
                ? `<input type="number" min="1" max="1440" value="${t.interval_minutes}" style="width:45px;padding:1px 3px;border:1px solid var(--border);border-radius:4px;font-size:0.75rem" onchange="updateHeartbeatInterval('${agentId}','${escapeHtml(skillName)}',this.value)">m`
                : `${t.interval_minutes}m`;
        } else {
            hbFreqEl.innerHTML = entries.map(([name, t]) => isActive
                ? `${escapeHtml(name)}: <input type="number" min="1" max="1440" value="${t.interval_minutes}" style="width:45px;padding:1px 3px;border:1px solid var(--border);border-radius:4px;font-size:0.75rem" onchange="updateHeartbeatInterval('${agentId}','${escapeHtml(name)}',this.value)">m`
                : `${escapeHtml(name)}: ${t.interval_minutes}m`
            ).join(', ');
        }
    } else {
        hbFreqEl.textContent = isActive ? 'none' : '--';
    }

    const currentEvent = runtimeStatus?.current_event_source;
    const processingEl = document.getElementById('rt-agent-processing');
    if (currentEvent) {
        const startedAt = runtimeStatus?.current_event_started_at;
        const elapsed = startedAt ? ` (${getTimeAgo(new Date(startedAt))})` : '';
        processingEl.innerHTML = `<span style="color:#f59e0b;font-weight:600">${escapeHtml(currentEvent)}</span><span style="color:#9ca3af;font-size:0.75rem">${elapsed}</span>`;
    } else {
        processingEl.innerHTML = '<span style="color:#9ca3af">Idle</span>';
    }

    const startedAtEl = document.getElementById('rt-agent-started-at');
    startedAtEl.textContent = (isActive && runtimeStatus?.started_at)
        ? new Date(runtimeStatus.started_at).toLocaleString()
        : 'N/A';

    statsBar.style.display = 'flex';

    // --- Next Tick Countdown ---
    const nextTickEl = document.getElementById('rt-agent-next-tick');
    if (_rtCountdownInterval) { clearInterval(_rtCountdownInterval); _rtCountdownInterval = null; }
    if (isActive && runtimeStatus?.next_tick_at) {
        const nextTickTime = new Date(runtimeStatus.next_tick_at);
        const updateCountdown = () => {
            const diffMs = nextTickTime - Date.now();
            if (diffMs <= 0) {
                nextTickEl.textContent = 'Next tick: now';
                nextTickEl.style.color = '#22c55e';
            } else {
                const secs = Math.ceil(diffMs / 1000);
                const m = Math.floor(secs / 60);
                const s = secs % 60;
                nextTickEl.textContent = `Next tick: ${m}m ${String(s).padStart(2, '0')}s`;
                nextTickEl.style.color = secs < 30 ? '#f59e0b' : '#9ca3af';
            }
        };
        updateCountdown();
        _rtCountdownInterval = setInterval(updateCountdown, 1000);
    } else {
        nextTickEl.textContent = '';
    }

    // --- Timers ---
    const timers = runtimeStatus?.timers;
    if (timers && Object.keys(timers).length > 0) {
        const timersList = Object.entries(timers).map(
            ([name, t]) => isActive
                ? `<span class="card-badge">${escapeHtml(name)}</span> every <input type="number" min="1" max="1440" value="${t.interval_minutes}" style="width:50px;padding:1px 4px;border:1px solid var(--border);border-radius:4px;font-size:0.8rem" onchange="updateHeartbeatInterval('${agentId}','${escapeHtml(name)}',this.value)">m`
                : `<span class="card-badge">${escapeHtml(name)}</span> every ${t.interval_minutes}m`
        ).join(' &nbsp;&bull;&nbsp; ');
        document.getElementById('rt-agent-timers-list').innerHTML = timersList;
        timersSection.style.display = '';
    } else {
        timersSection.style.display = 'none';
    }

    // --- Metrics ---
    const m = runtimeStatus?.metrics || {};
    if (isActive || m.events_processed) {
        document.getElementById('rt-agent-metrics-bar').innerHTML = `
            <div class="stat-item">
                <span class="stat-label">HB Fired:</span>
                <span class="stat-value ok">${m.heartbeats_fired || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">HB Skipped:</span>
                <span class="stat-value${m.heartbeats_skipped > 0 ? ' warn' : ''}">${m.heartbeats_skipped || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Processed:</span>
                <span class="stat-value ok">${m.events_processed || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Failed:</span>
                <span class="stat-value${m.events_failed > 0 ? ' error' : ''}">${m.events_failed || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Webhooks:</span>
                <span class="stat-value">${m.webhooks_received || 0}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Run Time:</span>
                <span class="stat-value">${m.total_run_duration_ms ? formatDurationMs(m.total_run_duration_ms) : '0s'}</span>
            </div>`;
        metricsSection.style.display = '';
    } else {
        metricsSection.style.display = 'none';
    }

    // --- Pending Approval Section ---
    const pending = pendingData?.pending_events || [];
    if (pending.length > 0) {
        const pendingList = document.getElementById('rt-agent-pending-list');
        pendingList.innerHTML = pending.map(e => {
            const titleStr = e.title ? escapeHtml(e.title) : e.event_id;
            const skillStr = e.skill_id ? `<span class="card-badge">${escapeHtml(e.skill_id)}</span>` : '';
            const preview = escapeHtml(e.message_preview || '').substring(0, 120);
            const priorityColors = { HIGH: 'error', NORMAL: '', LOW: 'warning' };
            return `<div class="card" style="margin-bottom:0.75rem;cursor:pointer;" onclick="showEventDetailModal('${agentId}','${e.event_id}')">
                <div class="card-header">
                    <span class="card-title">${titleStr}</span>
                    <span class="card-badge ${priorityColors[e.priority] || ''}">${e.priority}</span>
                </div>
                <div class="card-description">${preview}</div>
                <div class="card-meta">${skillStr} Created by: ${e.created_by} | ${getTimeAgo(new Date(e.timestamp))}</div>
                <div class="card-actions">
                    <button class="btn success" onclick="event.stopPropagation();approveRtEvent('${agentId}','${e.event_id}')">Approve</button>
                    <button class="btn danger" onclick="event.stopPropagation();dropRtEvent('${agentId}','${e.event_id}')">Drop</button>
                </div>
            </div>`;
        }).join('');
        pendingSection.style.display = '';
    } else {
        pendingSection.style.display = 'none';
    }

    // --- Currently Processing Section ---
    const currentEventObj = runtimeStatus?.current_event;
    const processingCard = document.getElementById('rt-agent-processing-card');
    if (currentEventObj) {
        const titleStr = currentEventObj.title ? escapeHtml(currentEventObj.title) : currentEventObj.event_id;
        const skillStr = currentEventObj.skill_id ? `<span class="card-badge">${escapeHtml(currentEventObj.skill_id)}</span>` : '';
        const preview = escapeHtml(currentEventObj.message_preview || '').substring(0, 120);
        const startedAt = currentEventObj.timestamp;
        const elapsed = startedAt ? getTimeAgo(new Date(startedAt)) : '';
        const priorityColors = { HIGH: 'error', NORMAL: '', LOW: 'warning' };
        processingCard.innerHTML = `<div class="card" style="border-left:3px solid #f59e0b;cursor:pointer;" onclick="showEventDetailModal('${agentId}','${currentEventObj.event_id}')">
            <div class="card-header">
                <span class="card-title"><span class="loading" style="width:0.75em;height:0.75em;margin-right:0.5rem;"></span>${titleStr}</span>
                <span class="card-badge ${priorityColors[currentEventObj.priority] || ''}">${currentEventObj.priority}</span>
            </div>
            <div class="card-description">${preview}</div>
            <div class="card-meta">${skillStr} Source: ${escapeHtml(currentEventObj.source)} | Running for ${elapsed}</div>
        </div>`;
        processingSection.style.display = '';
    } else {
        processingSection.style.display = 'none';
    }

    // --- Queue Table ---
    const queue = queueData?.queue || [];
    const priorityBadge = (p) => {
        const colors = { HIGH: 'error', NORMAL: '', LOW: 'warning' };
        return `<span class="card-badge ${colors[p] || ''}">${p}</span>`;
    };
    if (queue.length > 0) {
        queueTbody.innerHTML = queue.map(e => {
            const timeAgo = getTimeAgo(new Date(e.timestamp));
            const preview = escapeHtml(e.message_preview || '').substring(0, 80);
            const titleStr = e.title ? escapeHtml(e.title) : `<span style="color:#9ca3af;font-size:0.75rem">${e.event_id}</span>`;
            const skillStr = e.skill_id ? escapeHtml(e.skill_id) : '';
            return `<tr style="cursor:pointer;" onclick="showEventDetailModal('${agentId}','${e.event_id}')">
                <td>${priorityBadge(e.priority)}</td>
                <td>${escapeHtml(e.source)}</td>
                <td>${titleStr}</td>
                <td>${skillStr}</td>
                <td>${timeAgo}</td>
                <td title="${escapeHtml(e.message_preview || '')}">${preview}</td>
            </tr>`;
        }).join('');
    } else if (isActive) {
        queueTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#9ca3af;">Empty</td></tr>';
    } else {
        queueTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#9ca3af;">Agent is stopped</td></tr>';
    }

    // --- Event History Table ---
    const history = historyData?.events || [];
    if (history.length > 0) {
        const statusBadge = (s) => {
            const cls = s === 'completed' ? 'ok' : 'error';
            return `<span class="card-badge ${cls}">${s}</span>`;
        };
        historyTbody.innerHTML = history.map(e => {
            const completedAt = new Date(e.completed_at);
            const timeStr = completedAt.toLocaleTimeString();
            const durationStr = e.duration_ms > 0 ? formatDurationMs(e.duration_ms) : '-';
            const msgPreview = escapeHtml(e.message_preview || '').substring(0, 60);
            const respPreview = escapeHtml(e.response_preview || '').substring(0, 80);
            const titleStr = e.title ? escapeHtml(e.title) : `<span style="color:#9ca3af;font-size:0.75rem">${e.event_id}</span>`;
            return `<tr style="cursor:pointer;" onclick="showEventDetailModal('${agentId}','${e.event_id}')">
                <td>${statusBadge(e.status)}</td>
                <td>${escapeHtml(e.source)}</td>
                <td>${titleStr}</td>
                <td>${timeStr}</td>
                <td>${durationStr}</td>
                <td title="${escapeHtml(e.message_preview || '')}">${msgPreview}</td>
                <td title="${escapeHtml(e.response_preview || '')}" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${respPreview}</td>
            </tr>`;
        }).join('');
    } else {
        historyTbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#9ca3af;">No events yet</td></tr>';
    }

    // --- Agent Issues ---
    const issuesSection = document.getElementById('rt-agent-issues-section');
    const issuesTbody = document.getElementById('rt-agent-issues-table');
    const issuesCount = document.getElementById('rt-agent-issues-count');
    const openIssues = (issuesData?.items || []).filter(i => i.status === 'open');
    if (openIssues.length > 0) {
        issuesCount.textContent = `(${openIssues.length} open)`;
        const sevColors = { critical: '#dc2626', high: '#f97316', medium: '#eab308', low: '#9ca3af' };
        const sevBg = { critical: '#fef2f2', high: '#fff7ed', medium: '#fefce8', low: '#f9fafb' };
        // Cache for detail modal
        _issuesCache = {};
        openIssues.forEach(iss => { _issuesCache[iss.id] = iss; });
        issuesTbody.innerHTML = openIssues.map(iss => {
            const sev = iss.severity || 'medium';
            const cat = iss.category || 'error';
            const age = iss.created_at ? getTimeAgo(new Date(iss.created_at)) : '';
            const count = (iss.occurrence_count || 1) > 1 ? ` <span style="color:#9ca3af;font-size:0.7rem;">(x${iss.occurrence_count})</span>` : '';
            const hasTodo = iss.todo_on_dismiss;
            const btnText = hasTodo ? 'Dismiss + TODO' : 'Dismiss';
            const btnClass = hasTodo ? 'btn btn-sm warning' : 'btn btn-sm';
            return `<tr>
                <td><span style="display:inline-block;padding:1px 6px;border-radius:3px;font-size:0.7rem;font-weight:600;color:${sevColors[sev] || '#9ca3af'};background:${sevBg[sev] || '#f9fafb'};">${sev}</span></td>
                <td><a href="#" onclick="event.preventDefault();showIssueDetail('${iss.id}')" style="color:inherit;text-decoration:underline;text-decoration-style:dotted;cursor:pointer;">${escapeHtml(iss.title)}</a>${count}</td>
                <td><span class="card-badge">${cat}</span></td>
                <td style="font-size:0.75rem;">${age}</td>
                <td><button class="${btnClass}" style="font-size:0.7rem;padding:2px 8px;" onclick="dismissIssue('${agentId}','${iss.id}',${!!hasTodo})">${btnText}</button></td>
            </tr>`;
        }).join('');
        issuesSection.style.display = '';
    } else {
        issuesSection.style.display = 'none';
    }

    // --- Agent TO-DO List ---
    const todoSection = document.getElementById('rt-agent-todo-section');
    const todoTbody = document.getElementById('rt-agent-todo-table');
    const todoCounts = document.getElementById('rt-agent-todo-counts');
    const items = todoData?.items || [];
    const todoPending = todoData?.pending || 0;
    const todoCompleted = todoData?.completed || 0;

    const todoClearBtn = document.getElementById('rt-agent-todo-clear-btn');
    todoSection.style.display = '';
    if (items.length > 0 || todoPending > 0 || todoCompleted > 0) {
        todoClearBtn.style.display = '';
        todoCounts.textContent = `(${todoPending} pending, ${todoCompleted} completed)`;

        // Show pending first, then completed (most recent first)
        const pending = items.filter(t => t.status === 'pending');
        const completed = items.filter(t => t.status === 'completed').slice(-10).reverse();
        const display = [...pending, ...completed];

        if (display.length > 0) {
            todoTbody.innerHTML = display.map(t => {
                const isDone = t.status === 'completed';
                const check = isDone ? '&#9745;' : '&#9744;';
                const priColors = { high: 'color:#dc2626;font-weight:600', normal: '', low: 'color:#9ca3af' };
                const priStyle = priColors[t.priority] || '';
                const rowStyle = isDone ? 'opacity:0.5;' : '';
                const created = t.created_at ? getTimeAgo(new Date(t.created_at)) : '';
                const ctx = t.context ? escapeHtml(t.context).substring(0, 50) : '';
                const resultNote = t.result ? ` -> ${escapeHtml(t.result).substring(0, 40)}` : '';
                return `<tr style="${rowStyle}">
                    <td>${check}</td>
                    <td style="font-family:monospace;font-size:0.75rem;">${t.id}</td>
                    <td style="${priStyle}">${t.priority || 'normal'}</td>
                    <td>${escapeHtml(t.task || '')}${resultNote}</td>
                    <td style="font-size:0.75rem;color:#78716c;">${ctx}</td>
                    <td style="font-size:0.75rem;">${created}</td>
                </tr>`;
            }).join('');
        } else {
            todoTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#9ca3af;">No items</td></tr>';
        }
    } else {
        todoClearBtn.style.display = 'none';
        todoCounts.textContent = '(empty)';
        todoTbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#9ca3af;">Agent has no TODO items yet. Items appear when the agent uses todo_add.</td></tr>';
    }

    // --- Heartbeat History ---
    const hbSection = document.getElementById('rt-agent-hb-history-section');
    const hbTbody = document.getElementById('rt-agent-hb-history-table');
    const hbCount = document.getElementById('rt-agent-hb-history-count');
    const hbEntries = hbHistoryData?.entries || [];
    if (hbEntries.length > 0) {
        hbCount.textContent = `(${hbEntries.length} most recent)`;
        hbTbody.innerHTML = hbEntries.map(e => {
            const statusCls = e.status === 'completed' ? 'ok' : (e.status === 'error' ? 'error' : 'warning');
            const ts = e.timestamp ? new Date(e.timestamp) : null;
            const timeStr = ts ? ts.toLocaleString() : '-';
            const skills = (e.skills_triggered || []).map(s => `<span class="card-badge">${escapeHtml(s)}</span>`).join(' ');
            const summaryLines = (e.summary_lines || []).map(l => escapeHtml(l)).join('<br>');
            const tokens = e.total_tokens ? e.total_tokens.toLocaleString() : '0';
            return `<tr>
                <td><span class="card-badge ${statusCls}">${escapeHtml(e.status || 'unknown')}</span></td>
                <td style="white-space:nowrap;font-size:0.75rem;">${timeStr}</td>
                <td>${skills || '<span style="color:#9ca3af;">-</span>'}</td>
                <td style="text-align:center;">${e.turn_count || 0}</td>
                <td style="text-align:right;font-size:0.75rem;">${tokens}</td>
                <td style="font-size:0.75rem;line-height:1.4;">${summaryLines || '<span style="color:#9ca3af;">-</span>'}</td>
            </tr>`;
        }).join('');
        hbSection.style.display = '';
    } else {
        hbSection.style.display = 'none';
    }

    // Start auto-refresh (every 3s while on this tab)
    startRuntimeAutoRefresh();
}

async function clearAgentTodo() {
    const agentId = document.getElementById('rt-agent-select')?.value;
    if (!agentId) return;
    await api('DELETE', `/agents/${agentId}/todo`);
    loadAgentRuntime();
}

// Cache issues for detail modal
let _issuesCache = {};

function showIssueDetail(issueId) {
    const iss = _issuesCache[issueId];
    if (!iss) return;
    const sev = iss.severity || 'medium';
    const cat = iss.category || '-';
    const created = iss.created_at ? new Date(iss.created_at).toLocaleString() : '-';
    const count = iss.occurrence_count || 1;
    const lastSeen = iss.last_occurrence_at ? new Date(iss.last_occurrence_at).toLocaleString() : created;
    const desc = escapeHtml(iss.description || 'No description').replace(/\n/g, '<br>');
    const todo = iss.todo_on_dismiss ? `<p style="margin-top:0.75rem;padding:0.5rem;background:#fefce8;border-radius:4px;font-size:0.85rem;"><strong>On dismiss:</strong> ${escapeHtml(iss.todo_on_dismiss)}</p>` : '';

    let ctxHtml = '';
    if (iss.context && Object.keys(iss.context).length > 0) {
        const rows = Object.entries(iss.context).map(([k, v]) => {
            const val = typeof v === 'object' ? JSON.stringify(v) : String(v);
            return `<tr><td style="font-weight:600;padding:2px 8px 2px 0;vertical-align:top;white-space:nowrap;">${escapeHtml(k)}</td><td style="padding:2px 0;word-break:break-all;">${escapeHtml(val)}</td></tr>`;
        }).join('');
        ctxHtml = `<div style="margin-top:0.75rem;"><strong>Context</strong><table style="font-size:0.8rem;margin-top:0.25rem;">${rows}</table></div>`;
    }

    const body = `
        <div style="display:flex;gap:1rem;margin-bottom:0.75rem;font-size:0.85rem;color:#78716c;">
            <span><strong>Severity:</strong> ${sev}</span>
            <span><strong>Category:</strong> ${cat}</span>
            <span><strong>Occurrences:</strong> ${count}</span>
        </div>
        <div style="font-size:0.85rem;color:#78716c;margin-bottom:0.75rem;">
            <span><strong>Created:</strong> ${created}</span> &nbsp; <span><strong>Last seen:</strong> ${lastSeen}</span>
        </div>
        <div style="font-size:0.9rem;line-height:1.6;">${desc}</div>
        ${todo}
        ${ctxHtml}
    `;
    showModal(iss.title, body);
}

async function dismissIssue(agentId, issueId, createTodo) {
    await api('POST', `/agents/${agentId}/issues/${issueId}/dismiss`, { create_todo: createTodo });
    loadAgentRuntime();
}

async function showEventDetailModal(agentId, eventId) {
    const data = await api('GET', `/agents/${agentId}/events/${eventId}`);
    if (!data) return;

    const locationBadge = {
        processing: '<span class="card-badge warning">Processing</span>',
        queue: '<span class="card-badge">Queued</span>',
        pending: '<span class="card-badge error">Pending Approval</span>',
        history: '<span class="card-badge ok">History</span>',
    };
    const priorityColors = { HIGH: 'error', NORMAL: '', LOW: 'warning' };
    const statusColors = { completed: 'ok', failed: 'error', running: 'warning', active: '', pending_approval: 'error' };

    let html = `<div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:1rem;">
        ${locationBadge[data.location] || ''}
        <span class="card-badge ${priorityColors[data.priority] || ''}">${data.priority}</span>
        ${data.status ? `<span class="card-badge ${statusColors[data.status] || ''}">${data.status}</span>` : ''}
    </div>`;

    html += `<table style="width:100%;font-size:0.8125rem;margin-bottom:1rem;">`;
    const fields = [
        ['Event ID', data.event_id],
        ['Source', data.source],
        ['Title', data.title || '-'],
        ['Skill', data.skill_id || '-'],
        ['Session Key', data.session_key || '-'],
        ['Created By', data.created_by || '-'],
    ];
    if (data.timestamp) fields.push(['Queued At', new Date(data.timestamp).toLocaleString()]);
    if (data.queued_at) fields.push(['Queued At', new Date(data.queued_at).toLocaleString()]);
    if (data.completed_at) fields.push(['Completed At', new Date(data.completed_at).toLocaleString()]);
    if (data.duration_ms !== undefined) fields.push(['Duration', data.duration_ms > 0 ? formatDurationMs(data.duration_ms) : '-']);
    if (data.has_routing) fields.push(['Routing', 'Yes']);

    for (const [label, value] of fields) {
        html += `<tr><td style="color:#78716c;padding:0.25rem 0.5rem 0.25rem 0;white-space:nowrap;vertical-align:top;">${label}</td><td style="padding:0.25rem 0;word-break:break-all;">${escapeHtml(String(value))}</td></tr>`;
    }
    html += `</table>`;

    // Context dict (for agent-created events)
    if (data.context && typeof data.context === 'object' && Object.keys(data.context).length > 0) {
        html += `<div style="margin-bottom:1rem;">
            <div style="font-weight:600;font-size:0.8125rem;margin-bottom:0.25rem;">Context</div>
            <pre style="background:#f5f5f4;padding:0.75rem;font-size:0.75rem;overflow-x:auto;max-height:150px;overflow-y:auto;white-space:pre-wrap;">${escapeHtml(JSON.stringify(data.context, null, 2))}</pre>
        </div>`;
    }

    // Full message
    const message = data.message || '';
    if (message) {
        html += `<div style="margin-bottom:1rem;">
            <div style="font-weight:600;font-size:0.8125rem;margin-bottom:0.25rem;">Message</div>
            <pre style="background:#f5f5f4;padding:0.75rem;font-size:0.75rem;overflow-x:auto;max-height:250px;overflow-y:auto;white-space:pre-wrap;">${escapeHtml(message)}</pre>
        </div>`;
    }

    // Full response (history only)
    const response = data.response || '';
    if (response) {
        html += `<div style="margin-bottom:1rem;">
            <div style="font-weight:600;font-size:0.8125rem;margin-bottom:0.25rem;">Response</div>
            <pre style="background:#f5f5f4;padding:0.75rem;font-size:0.75rem;overflow-x:auto;max-height:250px;overflow-y:auto;white-space:pre-wrap;">${escapeHtml(response)}</pre>
        </div>`;
    }

    // Actions for pending events
    let actions = '<button class="btn" onclick="closeModal()">Close</button>';
    if (data.location === 'pending') {
        actions = `<button class="btn success" onclick="approveRtEvent('${agentId}','${eventId}');closeModal();">Approve</button>
            <button class="btn danger" onclick="dropRtEvent('${agentId}','${eventId}');closeModal();">Drop</button>
            <button class="btn" onclick="closeModal()">Close</button>`;
    }

    const modal = document.getElementById('modal');
    modal.style.maxWidth = '700px';
    const titleStr = data.title ? `Event: ${data.title}` : `Event: ${data.event_id}`;
    showModal(titleStr, html, actions);
}

async function approveRtEvent(agentId, eventId) {
    const result = await api('POST', `/agents/${agentId}/events/${eventId}/approve`);
    if (result?.status === 'ok') {
        showToast('Event approved', 'success');
        setTimeout(() => loadAgentRuntime(), 300);
    }
}

async function dropRtEvent(agentId, eventId) {
    const result = await api('POST', `/agents/${agentId}/events/${eventId}/drop`);
    if (result?.status === 'ok') {
        showToast('Event dropped', 'success');
        setTimeout(() => loadAgentRuntime(), 300);
    }
}

async function triggerHeartbeatFromRuntime() {
    const agentId = state.selectedAgent;
    if (!agentId) return;
    const result = await api('POST', `/agents/${agentId}/trigger-heartbeat`);
    if (result?.status === 'queued') {
        const skills = (result.skills || []).join(', ');
        showToast(`Heartbeat queued for ${agentId} (${skills})`, 'success');
        // Refresh after a short delay to show the queued event
        setTimeout(() => loadAgentRuntime(), 500);
    }
}

async function resetAgentFromRuntime() {
    const agentId = state.selectedAgent;
    if (!agentId) return;
    if (!confirm(`Reset agent ${agentId}?\n\nThis will stop the agent and clear all queues, TODO list, event history, and metrics.`)) return;
    const result = await api('POST', `/agents/${agentId}/reset`);
    if (result?.status === 'ok') {
        showToast(result.message || `Agent ${agentId} reset`, 'success');
        setTimeout(() => { loadAgentRuntime(); loadAgents(); }, 300);
    }
}

// ====================================================================
// GLOBAL SKILLS
// ====================================================================
async function loadGlobalSkills() {
    const container = document.getElementById('global-skills-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';
    const data = await api('GET', '/skills/global');
    if (!data || !data.skills || data.skills.length === 0) {
        container.innerHTML = '<div class="empty-state">No global skills</div>';
        return;
    }
    state.globalSkills = data.skills;
    container.innerHTML = data.skills.map(skill => `
                <div class="card ${skill.enabled ? '' : 'disabled'}">
                    <div class="card-header">
                        <span class="card-title">${skill.name || skill.id}</span>
                        <span class="card-badge global">GLOBAL</span>
                    </div>
                    <div class="card-description">${skill.description || 'No description'}</div>
                    <div class="card-meta">ID: ${skill.id} | Triggers: ${skill.triggers?.join(', ') || 'None'}</div>
                    <div class="card-actions">
                        <button class="btn" onclick="viewGlobalSkill('${skill.id}')">Details</button>
                    </div>
                </div>
            `).join('');
}

async function viewGlobalSkill(skillId) {
    const skill = await api('GET', `/skills/${skillId}`);
    if (skill) {
        showModal(`Skill: ${skill.name || skillId}`, `
                    <div class="form-group"><label>ID</label><div>${skill.id}</div></div>
                    <div class="form-group"><label>Triggers</label><div>${skill.triggers?.join(', ') || 'None'}</div></div>
                    <div class="form-group"><label>Files</label><div>${skill.files?.join(', ') || 'None'}</div></div>
                    <div class="form-group"><label>Content Preview</label><div class="response-box">${skill.content || 'N/A'}</div></div>
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

async function fetchSkill() {
    const skillId = generateId('sk');  // Auto-generate
    const url = document.getElementById('fetch-skill-url').value.trim();
    if (!url) { showToast('Please enter a URL', 'warning'); return; }
    const result = await api('POST', '/skills/fetch', { skill_id: skillId, url });
    if (result) {
        showToast(`Skill ${result.skill_id} fetched`, 'success');
        document.getElementById('fetch-skill-url').value = '';
        loadGlobalSkills();
    }
}

// ====================================================================
// SKILL VENDORS MANAGEMENT
// ====================================================================
async function loadVendors() {
    const container = document.getElementById('vendors-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const data = await api('GET', '/skills/vendors');
    if (!data || !data.vendors || data.vendors.length === 0) {
        container.innerHTML = `
                    <div class="empty-state">
                        No vendors configured.<br><br>
                        Click "+ Add Vendor" to register an external skill provider.
                    </div>`;
        return;
    }

    container.innerHTML = data.vendors.map(vendor => `
                <div class="card ${vendor.enabled ? '' : 'disabled'}">
                    <div class="card-header">
                        <span class="card-title">${vendor.name}</span>
                        <span class="card-badge ${vendor.enabled ? 'success' : ''}">${vendor.enabled ? 'ENABLED' : 'DISABLED'}</span>
                    </div>
                    <div class="card-description">${vendor.description || 'No description'}</div>
                    <div class="card-meta">
                        ID: ${vendor.id} | Skills: ${vendor.skill_count || 0}
                        ${vendor.website ? `| <a href="${vendor.website}" target="_blank">Website</a>` : ''}
                    </div>
                    <div class="card-actions">
                        <button class="btn" onclick="viewVendorDetails('${vendor.id}')">View Skills</button>
                        <button class="btn" onclick="editVendor('${vendor.id}')">Edit</button>
                        <button class="btn danger" onclick="deleteVendor('${vendor.id}')">Delete</button>
                    </div>
                </div>
            `).join('');
}

// ── Debug Settings ──────────────────────────────────────────────────

async function loadDebugSettings() {
    const data = await api('GET', '/debug/prompts', null, { silent: true });
    const enabled = data && data.debug_prompts;
    const cb = document.getElementById('toggle-prompt-logging');
    const label = document.getElementById('prompt-logging-status');
    const slider = document.getElementById('toggle-prompt-slider');
    if (cb) cb.checked = enabled;
    if (label) label.textContent = enabled ? 'on' : 'off';
    if (slider) slider.style.background = enabled ? '#16a34a' : '#d6d3d1';
    updateSliderKnob(slider, enabled);
}

async function togglePromptLogging(enable) {
    const data = await api('POST', `/debug/prompts?enable=${enable}`);
    const label = document.getElementById('prompt-logging-status');
    const slider = document.getElementById('toggle-prompt-slider');
    if (data) {
        if (label) label.textContent = data.debug_prompts ? 'on' : 'off';
        if (slider) slider.style.background = data.debug_prompts ? '#16a34a' : '#d6d3d1';
        updateSliderKnob(slider, data.debug_prompts);
    }
}

function updateSliderKnob(slider, enabled) {
    if (!slider) return;
    // Create or update the knob pseudo-element via inline child
    let knob = slider.querySelector('.knob');
    if (!knob) {
        knob = document.createElement('span');
        knob.className = 'knob';
        Object.assign(knob.style, {
            position: 'absolute', height: '16px', width: '16px',
            left: '3px', bottom: '3px', backgroundColor: 'white',
            borderRadius: '50%', transition: '.2s'
        });
        slider.appendChild(knob);
    }
    knob.style.transform = enabled ? 'translateX(18px)' : 'translateX(0)';
}

async function viewVendorDetails(vendorId) {
    const vendor = await api('GET', `/skills/vendors/${vendorId}`);
    if (!vendor) return;

    const skillsList = (vendor.skills || []).map(s => `
                <div style="padding: 0.5rem; border: 1px solid #e7e5e4; margin-bottom: 0.5rem;">
                    <div style="font-weight: 600;">${s.name}</div>
                    <div style="font-size: 0.75rem; color: #57534e;">${s.description}</div>
                    <div style="font-size: 0.7rem; color: #78716c;">ID: ${s.id} | Tags: ${(s.tags || []).join(', ')}</div>
                </div>
            `).join('') || '<div class="empty-state">No skills defined</div>';

    showModal(`Vendor: ${vendor.name}`, `
                <div class="form-group"><label>ID</label><div>${vendor.id}</div></div>
                <div class="form-group"><label>Website</label><div><a href="${vendor.website}" target="_blank">${vendor.website}</a></div></div>
                <div class="form-group"><label>Base URL</label><div>${vendor.base_url}</div></div>
                <div class="form-group"><label>Skills (${vendor.skills?.length || 0})</label></div>
                <div style="max-height: 250px; overflow-y: auto;">${skillsList}</div>
            `, '<button class="btn" onclick="closeModal()">Close</button>');
}

function showAddVendorModal() {
    showModal('Add Skill Vendor', `
                <p style="font-size: 0.875rem; color: #57534e; margin-bottom: 1rem;">
                    Register an external skill provider. You can add skills to this vendor after creation.
                </p>
                <div class="form-grid">
                    <div class="form-group">
                        <label>Vendor ID *</label>
                        <input type="text" id="vendor-id" placeholder="my_vendor">
                    </div>
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="vendor-name" placeholder="My Vendor">
                    </div>
                    <div class="form-group full-width">
                        <label>Description</label>
                        <input type="text" id="vendor-description" placeholder="What does this vendor provide?">
                    </div>
                    <div class="form-group">
                        <label>Website</label>
                        <input type="text" id="vendor-website" placeholder="https://example.com">
                    </div>
                    <div class="form-group">
                        <label>Base URL (for skill files)</label>
                        <input type="text" id="vendor-base-url" placeholder="https://example.com/skills">
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="createVendor()">Create Vendor</button>
            `);
}

async function createVendor() {
    const vendorId = document.getElementById('vendor-id').value.trim();
    const name = document.getElementById('vendor-name').value.trim();
    const description = document.getElementById('vendor-description').value.trim();
    const website = document.getElementById('vendor-website').value.trim();
    const baseUrl = document.getElementById('vendor-base-url').value.trim();

    if (!vendorId || !name) {
        showToast('Vendor ID and Name are required', 'warning');
        return;
    }

    const vendor = {
        id: vendorId,
        name: name,
        description: description,
        website: website,
        base_url: baseUrl,
        enabled: true,
        skills: [],
        metadata: {
            added_date: new Date().toISOString().split('T')[0],
            added_by: 'admin'
        }
    };

    const result = await api('POST', '/skills/vendors', vendor);
    if (result && result.status === 'ok') {
        showToast(`Vendor "${name}" created`, 'success');
        closeModal();
        loadVendors();
    }
}

async function editVendor(vendorId) {
    const vendor = await api('GET', `/skills/vendors/${vendorId}`);
    if (!vendor) return;

    const skillsJson = JSON.stringify(vendor.skills || [], null, 2);

    showModal(`Edit Vendor: ${vendor.name}`, `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Vendor ID</label>
                        <input type="text" value="${vendor.id}" disabled>
                    </div>
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="edit-vendor-name" value="${vendor.name}">
                    </div>
                    <div class="form-group full-width">
                        <label>Description</label>
                        <input type="text" id="edit-vendor-description" value="${vendor.description || ''}">
                    </div>
                    <div class="form-group">
                        <label>Website</label>
                        <input type="text" id="edit-vendor-website" value="${vendor.website || ''}">
                    </div>
                    <div class="form-group">
                        <label>Base URL</label>
                        <input type="text" id="edit-vendor-base-url" value="${vendor.base_url || ''}">
                    </div>
                    <div class="form-group">
                        <label>Enabled</label>
                        <select id="edit-vendor-enabled">
                            <option value="true" ${vendor.enabled ? 'selected' : ''}>Yes</option>
                            <option value="false" ${!vendor.enabled ? 'selected' : ''}>No</option>
                        </select>
                    </div>
                    <div class="form-group full-width">
                        <label>Skills (JSON array)</label>
                        <textarea id="edit-vendor-skills" style="min-height: 150px; font-family: monospace; font-size: 0.75rem;">${skillsJson}</textarea>
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="saveVendor('${vendorId}')">Save Changes</button>
            `);
}

async function saveVendor(vendorId) {
    const name = document.getElementById('edit-vendor-name').value.trim();
    const description = document.getElementById('edit-vendor-description').value.trim();
    const website = document.getElementById('edit-vendor-website').value.trim();
    const baseUrl = document.getElementById('edit-vendor-base-url').value.trim();
    const enabled = document.getElementById('edit-vendor-enabled').value === 'true';
    let skills = [];

    try {
        skills = JSON.parse(document.getElementById('edit-vendor-skills').value);
    } catch (e) {
        showToast('Invalid JSON in skills field', 'error');
        return;
    }

    if (!name) {
        showToast('Name is required', 'warning');
        return;
    }

    const vendor = {
        id: vendorId,
        name: name,
        description: description,
        website: website,
        base_url: baseUrl,
        enabled: enabled,
        skills: skills
    };

    const result = await api('PUT', `/skills/vendors/${vendorId}`, vendor);
    if (result && result.status === 'ok') {
        showToast(`Vendor "${name}" updated`, 'success');
        closeModal();
        loadVendors();
    }
}

async function deleteVendor(vendorId) {
    if (!confirm(`Delete vendor "${vendorId}"?\n\nThis will remove the vendor from the registry. Skills already installed from this vendor will not be affected.`)) {
        return;
    }

    const result = await api('DELETE', `/skills/vendors/${vendorId}`);
    if (result && result.status === 'ok') {
        showToast(`Vendor "${vendorId}" deleted`, 'success');
        loadVendors();
    }
}

// ====================================================================
// CHAT FUNCTIONS
// ====================================================================
async function loadChatAgents() {
    const select = document.getElementById('chat-agent-select');
    const currentValue = select.value;

    // Load agents list
    const data = await api('GET', '/agents');
    if (data && data.agents) {
        select.innerHTML = '<option value="">Select agent...</option>';
        for (const agent of data.agents) {
            if (!agent.is_deleted) {
                select.innerHTML += `<option value="${agent.agent_id}" ${agent.agent_id === currentValue ? 'selected' : ''}>${agent.name || agent.agent_id}</option>`;
            }
        }
    }

    // If we had a previous selection, reload skills
    if (currentValue) {
        state.chat.agentId = currentValue;
        loadChatSkills(currentValue);
    }
}

async function onChatAgentChange() {
    const agentId = document.getElementById('chat-agent-select').value;
    state.chat.agentId = agentId;

    if (agentId) {
        await loadChatSkills(agentId);
    } else {
        document.getElementById('chat-skill-select').innerHTML = '<option value="">Auto-match</option>';
    }
}

async function loadChatSkills(agentId) {
    const select = document.getElementById('chat-skill-select');
    select.innerHTML = '<option value="">Auto-match (let agent decide)</option>';

    const data = await api('GET', `/agents/${agentId}/skills`);
    if (data) {
        const privateSkills = data.private_skills || [];
        const globalSkills = data.global_skills || [];

        if (privateSkills.length > 0) {
            select.innerHTML += '<optgroup label="Agent Skills">';
            for (const skill of privateSkills) {
                select.innerHTML += `<option value="${skill.id}">${skill.name || skill.id}</option>`;
            }
            select.innerHTML += '</optgroup>';
        }
        if (globalSkills.length > 0) {
            select.innerHTML += '<optgroup label="Global Skills">';
            for (const skill of globalSkills) {
                select.innerHTML += `<option value="${skill.id}">${skill.name || skill.id}</option>`;
            }
            select.innerHTML += '</optgroup>';
        }
    }
}

function newChatSession() {
    state.chat.sessionId = null;
    state.chat.messages = [];
    document.getElementById('chat-session-id').textContent = 'New';
    document.getElementById('chat-messages').innerHTML = `
                <div class="empty-state" id="chat-empty">
                    Select an agent and type a message to start chatting.<br><br>
                    Optionally select a skill to guide the conversation.
                </div>
            `;
    document.getElementById('chat-input').value = '';
}

function handleChatKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
    }
}

async function sendChatMessage() {
    const agentId = document.getElementById('chat-agent-select').value;
    const skillId = document.getElementById('chat-skill-select').value || null;
    const input = document.getElementById('chat-input');
    const message = input.value.trim();

    if (!agentId) {
        showToast('Please select an agent', 'warning');
        return;
    }
    if (!message) {
        showToast('Please enter a message', 'warning');
        return;
    }

    // Clear empty state and add user message
    const messagesDiv = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty');
    if (emptyState) emptyState.remove();

    // Add user message to UI
    addChatMessage('user', message);
    input.value = '';

    // Show loading state
    document.getElementById('chat-send-text').style.display = 'none';
    document.getElementById('chat-send-loading').style.display = 'inline-block';
    document.getElementById('chat-send-btn').disabled = true;

    // Add thinking indicator
    const thinkingId = 'thinking-' + Date.now();
    messagesDiv.innerHTML += `
                <div id="${thinkingId}" style="display: flex; gap: 0.75rem; margin-bottom: 1rem;">
                    <div style="width: 32px; height: 32px; border-radius: 50%; background: #7c3aed; color: white; display: flex; align-items: center; justify-content: center; font-weight: 600; flex-shrink: 0;">A</div>
                    <div style="flex: 1; padding: 0.75rem; background: white; border-radius: 4px; border: 1px solid #e7e5e4;">
                        <span class="loading" style="margin-right: 0.5rem;"></span> Thinking...
                    </div>
                </div>
            `;
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    // Send to API
    const result = await api('POST', `/agents/${agentId}/run`, {
        message: message,
        session_id: state.chat.sessionId,
        skill_id: skillId
    });

    // Remove thinking indicator
    document.getElementById(thinkingId)?.remove();

    // Reset button
    document.getElementById('chat-send-text').style.display = 'inline';
    document.getElementById('chat-send-loading').style.display = 'none';
    document.getElementById('chat-send-btn').disabled = false;

    if (result) {
        // Update session ID
        state.chat.sessionId = result.session_id;
        document.getElementById('chat-session-id').textContent = result.session_id?.substring(0, 12) + '...' || 'Active';

        // Add assistant response
        if (result.response) {
            addChatMessage('assistant', result.response, {
                turns: result.turns,
                tokens: result.total_tokens,
                duration: result.duration_ms,
                pending_events: result.pending_events || null,
                agentId: agentId
            });
        } else if (result.error) {
            addChatMessage('error', result.error);
        }
    } else {
        addChatMessage('error', 'Failed to get response from agent');
    }

    // Focus input for next message
    input.focus();
}

function addChatMessage(role, content, meta = null) {
    const messagesDiv = document.getElementById('chat-messages');

    const isUser = role === 'user';
    const isError = role === 'error';
    const avatar = isUser ? 'U' : (isError ? '!' : 'A');
    const avatarBg = isUser ? '#2563eb' : (isError ? '#dc2626' : '#7c3aed');
    const messageBg = isUser ? '#eff6ff' : (isError ? '#fef2f2' : 'white');
    const borderColor = isError ? '#fca5a5' : '#e7e5e4';

    let metaHtml = '';
    if (meta) {
        metaHtml = `<div style="font-size: 0.7rem; color: #78716c; margin-top: 0.5rem;">
                    ${meta.turns ? `Turns: ${meta.turns}` : ''}
                    ${meta.tokens ? `| Tokens: ${meta.tokens}` : ''}
                    ${meta.duration ? `| ${(meta.duration / 1000).toFixed(1)}s` : ''}
                </div>`;
    }

    // Build pending events approval cards
    let eventsHtml = '';
    if (meta && meta.pending_events && meta.pending_events.length > 0) {
        eventsHtml = '<div style="margin-top: 0.75rem; border-top: 1px solid #e7e5e4; padding-top: 0.75rem;">';
        eventsHtml += '<div style="font-size: 0.75rem; font-weight: 600; color: #78716c; margin-bottom: 0.5rem;">Pending Approval:</div>';
        for (const evt of meta.pending_events) {
            const evtId = evt.event_id;
            const agentId = meta.agentId || '';
            eventsHtml += `
                <div id="evt-card-${evtId}" style="display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem; background: #fefce8; border: 1px solid #fde68a; border-radius: 4px; margin-bottom: 0.5rem;">
                    <span style="flex: 1; font-size: 0.85rem;" title="${escapeHtml(evt.message || '')}">${escapeHtml(evt.title)}</span>
                    <button onclick="approveEvent('${agentId}', '${evtId}')" style="padding: 0.25rem 0.5rem; font-size: 0.75rem; background: #22c55e; color: white; border: none; border-radius: 3px; cursor: pointer;">Approve</button>
                    <button onclick="dropEvent('${agentId}', '${evtId}')" style="padding: 0.25rem 0.5rem; font-size: 0.75rem; background: #ef4444; color: white; border: none; border-radius: 3px; cursor: pointer;">Drop</button>
                </div>`;
        }
        eventsHtml += '</div>';
    }

    messagesDiv.innerHTML += `
                <div style="display: flex; gap: 0.75rem; margin-bottom: 1rem; ${isUser ? 'flex-direction: row-reverse;' : ''}">
                    <div style="width: 32px; height: 32px; border-radius: 50%; background: ${avatarBg}; color: white; display: flex; align-items: center; justify-content: center; font-weight: 600; flex-shrink: 0;">${avatar}</div>
                    <div style="flex: 1; max-width: 80%; padding: 0.75rem; background: ${messageBg}; border-radius: 4px; border: 1px solid ${borderColor};">
                        <div style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(content)}</div>
                        ${metaHtml}
                        ${eventsHtml}
                    </div>
                </div>
            `;

    // Store in state
    state.chat.messages.push({ role, content, meta });

    // Scroll to bottom
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

async function approveEvent(agentId, eventId) {
    const card = document.getElementById('evt-card-' + eventId);
    const result = await api('POST', `/agents/${agentId}/events/${eventId}/approve`);
    if (result && result.status === 'ok') {
        if (card) {
            card.innerHTML = '<span style="flex: 1; font-size: 0.85rem; color: #16a34a; font-weight: 600;">Approved</span>';
            card.style.background = '#f0fdf4';
            card.style.borderColor = '#86efac';
        }
        showToast('Event approved', 'success');
    } else {
        showToast('Failed to approve event', 'error');
    }
}

async function dropEvent(agentId, eventId) {
    const card = document.getElementById('evt-card-' + eventId);
    const result = await api('POST', `/agents/${agentId}/events/${eventId}/drop`);
    if (result && result.status === 'ok') {
        if (card) {
            card.innerHTML = '<span style="flex: 1; font-size: 0.85rem; color: #78716c; font-weight: 600;">Dropped</span>';
            card.style.background = '#f5f5f4';
            card.style.borderColor = '#d6d3d1';
        }
        showToast('Event dropped', 'info');
    } else {
        showToast('Failed to drop event', 'error');
    }
}

// ====================================================================
// WEBSOCKET
// ====================================================================
const WS_URL = 'wss://8qk1atrn55.execute-api.us-east-1.amazonaws.com/production';
const WS_ENABLED = !_isLocal; // Disable in local dev

let ws = null;
let wsReconnectTimer = null;
let wsPingTimer = null;
let wsLastServerMessage = 0;
let wsClosing = false;
let wsConsecutiveFailures = 0;
let wsConnectedOnce = false;

function connectWebSocket() {
    // Skip WebSocket in local development
    if (!WS_ENABLED) {
        console.log('[WS] Disabled in local dev');
        return;
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
        return; // Already connected
    }

    // Get auth token from localStorage
    const token = getToken();

    if (!token) {
        console.log('[WS] No auth token, skipping WebSocket');
        return;
    }

    // Track that we haven't successfully connected yet
    wsConnectedOnce = false;

    try {
        ws = new WebSocket(`${WS_URL}?token=${encodeURIComponent(token)}`);

        ws.onopen = () => {
            console.log('[WS] Connected');
            // Mark successful connection and reset failure counter
            wsConnectedOnce = true;
            wsConsecutiveFailures = 0;
            if (wsReconnectTimer) {
                clearTimeout(wsReconnectTimer);
                wsReconnectTimer = null;
            }
            wsLastServerMessage = Date.now();

            // Periodic ping to keep connection alive and re-register if needed
            if (wsPingTimer) clearInterval(wsPingTimer);
            wsPingTimer = setInterval(() => {
                if (!ws || ws.readyState !== WebSocket.OPEN) return;
                const silence = Date.now() - wsLastServerMessage;
                if (silence > 120000) { // 2 minutes without server message
                    console.log('[WS] Server silent, sending re-register ping');
                    const pingToken = getToken();
                    ws.send(JSON.stringify({ action: 'ping', token: pingToken }));
                    wsLastServerMessage = Date.now();
                }
            }, 30000);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                wsLastServerMessage = Date.now();
                handleWebSocketMessage(data);
            } catch (e) {
                console.warn('[WS] Failed to parse message:', e);
            }
        };

        ws.onclose = (event) => {
            console.log('[WS] Disconnected, code:', event.code);
            ws = null;
            if (wsPingTimer) {
                clearInterval(wsPingTimer);
                wsPingTimer = null;
            }

            // If we never successfully connected, this is likely an auth failure
            if (!wsConnectedOnce) {
                wsConsecutiveFailures++;
                console.warn(`[WS] Connection failed (attempt ${wsConsecutiveFailures})`);

                // After 3 consecutive failures, assume auth issue
                if (wsConsecutiveFailures >= 3) {
                    console.error('[WS] Too many connection failures, redirecting to login');
                    showToast('Session expired. Please log in again.', 'error');
                    // Clear auth and redirect to login
                    setTimeout(() => {
                        logout();
                    }, 2000);
                    return;
                }

                // Show warning after 2 failures
                if (wsConsecutiveFailures === 2) {
                    showToast('Connection issues, retrying...', 'warning');
                }
            }

            // Reconnect with exponential backoff unless intentionally closing
            if (!wsClosing && !wsReconnectTimer) {
                // Exponential backoff: 5s, 10s, 20s, 40s, max 60s
                const backoff = Math.min(5000 * Math.pow(2, wsConsecutiveFailures), 60000);
                console.log(`[WS] Reconnecting in ${backoff / 1000}s...`);
                wsReconnectTimer = setTimeout(() => {
                    wsReconnectTimer = null;
                    connectWebSocket();
                }, backoff);
            }
        };

        ws.onerror = (error) => {
            console.warn('[WS] Error:', error);
        };
    } catch (e) {
        console.warn('[WS] Failed to connect:', e);
    }
}

function handleWebSocketMessage(data) {
    console.log('[WS] Message:', data.type, data);

    switch (data.type) {
        case 'feed_message':
            // New feed message from an agent
            handleFeedMessagePush(data.message);
            break;

        case 'scheduler_status':
            // Scheduler status changed - update dashboard if visible
            if (state.currentPanel === 'dashboard') {
                updateSchedulerStatusUI(data.status);
            }
            break;

        case 'agent_run_started':
        case 'agent_run_progress':
        case 'agent_run_completed':
            // Agent run updates - could update chat UI
            console.log('[WS] Agent run event:', data);
            break;

        default:
            console.log('[WS] Unknown message type:', data.type);
    }
}

function handleFeedMessagePush(message) {
    // Update unread count
    state.feed.unreadCount++;
    const badge = document.getElementById('feed-badge');
    badge.textContent = state.feed.unreadCount > 99 ? '99+' : state.feed.unreadCount;
    badge.style.display = 'inline-block';

    // If viewing feed, prepend the message
    if (state.currentPanel === 'feed') {
        state.feed.messages.unshift(message);
        renderFeed();
    }

    // Show toast notification
    const msgType = message.type || 'info';
    const toastType = msgType === 'error' ? 'error' : (msgType === 'warning' ? 'warning' : 'success');
    showToast(`${message.agent_id}: ${message.title}`, toastType);
}

function updateSchedulerStatusUI(status) {
    // Update scheduler status elements on dashboard
    if (status.status) {
        document.getElementById('sched-status').textContent = status.status;
    }
    if (status.uptime) {
        document.getElementById('sched-uptime').textContent = status.uptime;
    }
}

// ====================================================================
// USAGE TAB
// ====================================================================

let usageRefreshTimer = null;

async function loadUsage() {
    const dateInput = document.getElementById('usage-date');
    const systemSelect = document.getElementById('usage-system');

    // Default date to today if empty
    if (!dateInput.value) {
        const today = new Date();
        dateInput.value = today.toISOString().split('T')[0];
    }

    const dateStr = dateInput.value.replace(/-/g, '');
    const system = systemSelect.value;

    let url = `/usage?date=${dateStr}`;
    if (system) url += `&system=${encodeURIComponent(system)}`;

    try {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        renderUsageData(data);
    } catch (e) {
        showToast(`Failed to load usage: ${e.message}`, 'error');
    }

    // Auto-refresh every 30s when tab is active
    clearInterval(usageRefreshTimer);
    if (state.currentPanel === 'usage') {
        usageRefreshTimer = setInterval(() => {
            if (state.currentPanel === 'usage') loadUsage();
            else clearInterval(usageRefreshTimer);
        }, 30000);
    }
}

function renderUsageData(data) {
    // Summary cards
    document.getElementById('usage-total-calls').textContent = data.totals.calls.toLocaleString();
    document.getElementById('usage-total-input').textContent = data.totals.input_tokens.toLocaleString();
    document.getElementById('usage-total-output').textContent = data.totals.output_tokens.toLocaleString();
    document.getElementById('usage-total-cost').textContent = '$' + data.totals.total_cost.toFixed(4);

    // Per-agent table
    const agentTbody = document.getElementById('usage-agent-table');
    const agents = Object.entries(data.by_agent || {});
    if (agents.length === 0) {
        agentTbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#9ca3af;">No usage data for this date</td></tr>';
    } else {
        // Sort by total_cost descending
        agents.sort((a, b) => b[1].total_cost - a[1].total_cost);
        agentTbody.innerHTML = agents.map(([aid, a]) => `
            <tr>
                <td><strong>${escapeHtml(a.agent_name || aid)}</strong></td>
                <td>${escapeHtml(a.system || '--')}</td>
                <td>${a.calls}</td>
                <td>${a.input_tokens.toLocaleString()}</td>
                <td>${a.output_tokens.toLocaleString()}</td>
                <td>$${a.input_cost.toFixed(4)}</td>
                <td>$${a.output_cost.toFixed(4)}</td>
                <td class="usage-cost">$${a.total_cost.toFixed(4)}</td>
            </tr>
        `).join('');
    }

    // Per-model table
    const modelTbody = document.getElementById('usage-model-table');
    const models = Object.entries(data.by_model || {});
    if (models.length === 0) {
        modelTbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#9ca3af;">--</td></tr>';
    } else {
        models.sort((a, b) => b[1].total_cost - a[1].total_cost);
        modelTbody.innerHTML = models.map(([model, m]) => `
            <tr>
                <td>${escapeHtml(model)}</td>
                <td>${m.calls}</td>
                <td>${m.input_tokens.toLocaleString()}</td>
                <td>${m.output_tokens.toLocaleString()}</td>
                <td class="usage-cost">$${m.total_cost.toFixed(4)}</td>
            </tr>
        `).join('');
    }

    // Recent calls (last 50, reversed so newest first)
    const callsTbody = document.getElementById('usage-calls-table');
    const calls = (data.calls || []).slice(-50).reverse();
    if (calls.length === 0) {
        callsTbody.innerHTML = '<tr><td colspan="9" style="text-align:center;color:#9ca3af;">No calls</td></tr>';
    } else {
        callsTbody.innerHTML = calls.map(c => {
            const time = c.timestamp ? new Date(c.timestamp).toLocaleTimeString() : '--';
            return `
                <tr>
                    <td>${time}</td>
                    <td>${escapeHtml(c.system || '--')}</td>
                    <td>${escapeHtml(c.agent_name || c.agent_id || '--')}</td>
                    <td>${escapeHtml(c.skill_id || '--')}</td>
                    <td>${escapeHtml(c.caller || '--')}</td>
                    <td style="font-size:0.75rem;">${escapeHtml(c.model || '--')}</td>
                    <td>${(c.input_tokens || 0).toLocaleString()}</td>
                    <td>${(c.output_tokens || 0).toLocaleString()}</td>
                    <td class="usage-cost">$${(c.total_cost || 0).toFixed(4)}</td>
                </tr>
            `;
        }).join('');
    }
}


// ====================================================================
// INITIALIZATION
// ====================================================================
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    scheduleNextRefresh();
    loadFeedUnreadCount();
    connectWebSocket();
});

window.addEventListener('beforeunload', () => {
    stopSchedulerRefresh();
    wsClosing = true;
    if (wsPingTimer) clearInterval(wsPingTimer);
    if (ws) {
        ws.close();
        ws = null;
    }
});
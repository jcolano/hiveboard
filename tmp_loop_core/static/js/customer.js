
// ====================================================================
// STATE
// ====================================================================
const state = {
    currentPanel: 'dashboard',
    currentSubPanel: {
        'agents': 'agents-list',
        'skills': 'skills-templates'
    },
    selectedAgent: null,
    agents: [],
    user: null,
    // Chat state
    chat: {
        agentId: null,
        skillId: null,
        sessionId: null,
        messages: []
    }
};

// Skill editor state
let skillEditorState = {
    step: 0,
    intent: '',
    hypothesis: null,
    form: null,
    answers: {},
    editingSkillId: null,
    fromTemplate: null,
    skippedFields: [],
    customFields: []
};

let skillTemplates = [];
let skillVendors = [];

// ====================================================================
// ENVIRONMENT DETECTION
// ====================================================================
const _isLocal = ['localhost', '127.0.0.1'].includes(window.location.hostname);
const API_BASE = _isLocal ? '' : 'https://mlbackend.net/loopcore';
console.log('[loopCore] Environment:', _isLocal ? 'local' : 'production', '| API_BASE:', API_BASE || '(relative)');

// ====================================================================
// AUTH
// ====================================================================
function getToken() {
    return localStorage.getItem('loopcore_token');
}

async function checkAuth() {
    const token = getToken();
    if (!token) {
        window.location.href = _isLocal ? '/static/login.html' : 'login.html';
        return false;
    }

    try {
        const response = await fetch(API_BASE + '/api/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!response.ok) {
            localStorage.removeItem('loopcore_token');
            localStorage.removeItem('loopcore_user');
            window.location.href = _isLocal ? '/static/login.html' : 'login.html';
            return false;
        }
        state.user = await response.json();
        updateUserInfo();
        return true;
    } catch (e) {
        window.location.href = _isLocal ? '/static/login.html' : 'login.html';
        return false;
    }
}

function updateUserInfo() {
    if (!state.user) return;
    document.getElementById('user-email').textContent = state.user.email;
    document.getElementById('user-company').textContent = state.user.company_name || '';
    document.getElementById('user-role').textContent = state.user.role;
}

function logout() {
    localStorage.removeItem('loopcore_token');
    localStorage.removeItem('loopcore_user');
    window.location.href = _isLocal ? '/static/login.html' : 'login.html';
}

// ====================================================================
// API HELPER
// ====================================================================
let serverConnected = false;

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

        // Check for auth errors
        if (response.status === 401) {
            logout();
            return null;
        }

        const json = await response.json();

        if (!serverConnected) {
            serverConnected = true;
            updateServerStatus(true);
        }

        if (!response.ok) {
            if (!silent) {
                showToast(json.detail || json.error || 'Request failed', 'error');
            }
            return null;
        }
        return json;
    } catch (error) {
        if (serverConnected) {
            serverConnected = false;
            updateServerStatus(false);
        }
        if (!silent) {
            showToast('Connection error', 'error');
        }
        return null;
    }
}

function updateServerStatus(connected) {
    const el = document.getElementById('stat-server');
    if (connected) {
        el.textContent = 'Connected';
        el.className = 'stat-value ok';
    } else {
        el.textContent = 'Disconnected';
        el.className = 'stat-value error';
    }
}

// ====================================================================
// UI HELPERS
// ====================================================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function showModal(title, body, actions) {
    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-body').innerHTML = body;
    document.getElementById('modal-actions').innerHTML = actions;
    document.getElementById('modal-backdrop').classList.remove('hidden');
    document.getElementById('modal').classList.remove('hidden');
}

function closeModal() {
    document.getElementById('modal-backdrop').classList.add('hidden');
    document.getElementById('modal').classList.add('hidden');
}

// ====================================================================
// NAVIGATION
// ====================================================================
document.querySelectorAll('.main-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        const panel = tab.dataset.panel;
        switchPanel(panel);
    });
});

function switchPanel(panelId) {
    document.querySelectorAll('.main-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.main-tab[data-panel="${panelId}"]`).classList.add('active');
    document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
    document.getElementById(`panel-${panelId}`).classList.add('active');
    state.currentPanel = panelId;

    // Load panel data
    if (panelId === 'dashboard') loadDashboard();
    else if (panelId === 'agents') loadAgents();
    else if (panelId === 'skills') loadTemplates();
    else if (panelId === 'chat') loadChatAgents();
}

document.addEventListener('click', (e) => {
    if (e.target.classList.contains('sub-tab')) {
        const subpanel = e.target.dataset.subpanel;
        const parentPanel = subpanel.split('-')[0];
        document.querySelectorAll(`#panel-${parentPanel} .sub-tab`).forEach(t => t.classList.remove('active'));
        e.target.classList.add('active');
        document.querySelectorAll(`#panel-${parentPanel} .sub-panel`).forEach(p => p.classList.remove('active'));
        document.getElementById(`subpanel-${subpanel}`).classList.add('active');
        state.currentSubPanel[parentPanel] = subpanel;

        // Show/hide agent selector
        const agentSelector = document.getElementById('agent-selector');
        if (parentPanel === 'agents' && subpanel !== 'agents-list') {
            agentSelector.style.display = 'flex';
            onAgentSelected();
        } else {
            agentSelector.style.display = 'none';
        }

        // Load sub-panel data
        loadSubPanelData(subpanel);
    }
});

function loadSubPanelData(subpanel) {
    if (subpanel === 'agents-skills') loadAgentSkills();
    else if (subpanel === 'agents-tasks') loadAgentTasks();
    else if (subpanel === 'agents-sessions') loadAgentSessions();
    else if (subpanel === 'agents-runs') loadAgentRuns();
    else if (subpanel === 'skills-templates') loadTemplates();
    else if (subpanel === 'skills-vendors') loadVendorSkills();
}

function refreshCurrentSubPanel() {
    const subpanel = state.currentSubPanel[state.currentPanel];
    loadSubPanelData(subpanel);
}

// ====================================================================
// DASHBOARD
// ====================================================================
async function loadDashboard() {
    // Load health
    const health = await api('GET', '/health', null, { silent: true });
    if (health) {
        document.getElementById('stat-server').textContent = health.status === 'healthy' ? 'Connected' : 'Error';
        document.getElementById('stat-server').className = 'stat-value ' + (health.status === 'healthy' ? 'ok' : 'error');
    }

    // Load status
    const status = await api('GET', '/status', null, { silent: true });
    if (status) {
        document.getElementById('stat-llm').textContent = status.llm_initialized ? status.llm_provider : 'Not configured';
        document.getElementById('stat-llm').className = 'stat-value ' + (status.llm_initialized ? 'ok' : 'warn');
        document.getElementById('stat-agents').textContent = status.configured_agents?.length || 0;
        document.getElementById('stat-skills').textContent = status.skills_loaded || 0;
    }

    // Load agents for quick run dropdown
    const agentsData = await api('GET', '/agents', null, { silent: true });
    if (agentsData && agentsData.agents) {
        const select = document.getElementById('quick-agent');
        select.innerHTML = '<option value="">Select an agent...</option>' +
            agentsData.agents.map(a => `<option value="${a.agent_id}">${a.name}</option>`).join('');
    }

    // Load recent runs
    loadRecentRuns();
}

async function loadRecentRuns() {
    const tbody = document.getElementById('recent-runs-table');
    const data = await api('GET', '/runs?limit=10', null, { silent: true });
    if (!data || !data.runs || data.runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: #78716c;">No recent activity</td></tr>';
        return;
    }
    tbody.innerHTML = data.runs.slice(0, 10).map(r => `
                <tr>
                    <td>${new Date(r.timestamp || r.date).toLocaleString()}</td>
                    <td>${r.agent_id}</td>
                    <td><span class="card-badge ${r.status === 'completed' ? 'success' : r.status === 'error' ? 'error' : ''}">${r.status}</span></td>
                    <td>${r.duration_ms || 0}ms</td>
                </tr>
            `).join('');
}

async function quickRun() {
    const agentId = document.getElementById('quick-agent').value;
    const skillId = document.getElementById('quick-skill').value;
    const message = document.getElementById('quick-message').value.trim();
    const responseBox = document.getElementById('quick-response');

    if (!agentId) {
        showToast('Please select an agent', 'warning');
        return;
    }
    if (!message) {
        showToast('Please enter a message', 'warning');
        return;
    }

    responseBox.classList.remove('hidden');
    responseBox.textContent = 'Running...';

    const payload = { message };
    if (skillId) payload.skill_id = skillId;

    const result = await api('POST', `/agents/${agentId}/run`, payload);
    if (result) {
        responseBox.textContent = result.response || 'No response';
        loadRecentRuns();
    } else {
        responseBox.textContent = 'Error running agent';
    }
}

// ====================================================================
// AGENTS
// ====================================================================
async function loadAgents() {
    const container = document.getElementById('agents-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const data = await api('GET', '/agents');
    if (!data || !data.agents || data.agents.length === 0) {
        container.innerHTML = '<div class="empty-state">No agents yet. Click "+ New Agent" to create one.</div>';
        return;
    }

    state.agents = data.agents;
    updateAgentSelectors();

    container.innerHTML = data.agents.map(agent => `
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">${agent.name}</span>
                        <span class="card-badge">${agent.model || 'default'}</span>
                    </div>
                    <div class="card-description">${agent.description || 'No description'}</div>
                    <div class="card-meta">
                        ID: ${agent.agent_id} | Runs: ${agent.total_runs || 0} | Sessions: ${agent.active_sessions || 0}
                    </div>
                    <div class="card-actions">
                        <button class="btn" onclick="viewAgent('${agent.agent_id}')">View</button>
                        <button class="btn" onclick="editAgent('${agent.agent_id}')">Edit</button>
                        <button class="btn danger" onclick="deleteAgent('${agent.agent_id}')">Delete</button>
                    </div>
                </div>
            `).join('');
}

function updateAgentSelectors() {
    const selects = ['selected-agent', 'quick-agent'];
    selects.forEach(id => {
        const el = document.getElementById(id);
        if (el && state.agents.length > 0) {
            const currentVal = el.value;
            el.innerHTML = state.agents.map(a =>
                `<option value="${a.agent_id}">${a.name}</option>`
            ).join('');
            if (currentVal && state.agents.find(a => a.agent_id === currentVal)) {
                el.value = currentVal;
            } else {
                el.value = state.agents[0].agent_id;
            }
        }
    });
    if (state.agents.length > 0 && !state.selectedAgent) {
        state.selectedAgent = state.agents[0].agent_id;
    }
}

function onAgentSelected() {
    state.selectedAgent = document.getElementById('selected-agent').value;
    document.getElementById('task-agent-name').textContent = state.selectedAgent;
    refreshCurrentSubPanel();
}

function showAgentModal() {
    showModal('Create New Agent', `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Agent ID *</label>
                        <input type="text" id="agent-id" placeholder="my_agent">
                    </div>
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="agent-name" placeholder="My Agent">
                    </div>
                    <div class="form-group full-width">
                        <label>Description</label>
                        <input type="text" id="agent-description" placeholder="What does this agent do?">
                    </div>
                    <div class="form-group">
                        <label>Model</label>
                        <select id="agent-model">
                            <option value="claude-sonnet-4-5-20250929">Claude Sonnet 4.5</option>
                            <option value="claude-3-5-sonnet-20241022">Claude 3.5 Sonnet</option>
                            <option value="gpt-4o">GPT-4o</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Max Turns</label>
                        <input type="number" id="agent-max-turns" value="30" min="1" max="100">
                    </div>
                    <div class="form-group full-width">
                        <label>System Prompt</label>
                        <textarea id="agent-system-prompt" placeholder="You are a helpful AI assistant...">You are a helpful AI assistant.</textarea>
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="createAgent()">Create Agent</button>
            `);
}

async function createAgent() {
    const agentId = document.getElementById('agent-id').value.trim();
    const name = document.getElementById('agent-name').value.trim();
    const description = document.getElementById('agent-description').value.trim();
    const model = document.getElementById('agent-model').value;
    const maxTurns = parseInt(document.getElementById('agent-max-turns').value);
    const systemPrompt = document.getElementById('agent-system-prompt').value.trim();

    if (!agentId || !name) {
        showToast('Agent ID and Name are required', 'warning');
        return;
    }

    const result = await api('POST', '/agents', {
        agent_id: agentId,
        name: name,
        description: description,
        model: model,
        max_turns: maxTurns,
        system_prompt: systemPrompt
    });

    if (result) {
        showToast(`Agent "${name}" created`, 'success');
        closeModal();
        loadAgents();
    }
}

async function viewAgent(agentId) {
    const agent = await api('GET', `/agents/${agentId}`);
    if (agent) {
        showModal(`Agent: ${agent.name}`, `
                    <div class="form-group"><label>ID</label><div>${agent.agent_id}</div></div>
                    <div class="form-group"><label>Model</label><div>${agent.model}</div></div>
                    <div class="form-group"><label>Max Turns</label><div>${agent.max_turns}</div></div>
                    <div class="form-group"><label>Tools</label><div>${agent.enabled_tools?.join(', ') || 'None'}</div></div>
                    <div class="form-group"><label>Skills</label><div>${agent.enabled_skills?.join(', ') || 'None'}</div></div>
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

async function editAgent(agentId) {
    const agent = await api('GET', `/agents/${agentId}`);
    if (!agent) return;

    showModal(`Edit Agent: ${agent.name}`, `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Agent ID</label>
                        <input type="text" value="${agent.agent_id}" disabled>
                    </div>
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="edit-agent-name" value="${agent.name}">
                    </div>
                    <div class="form-group full-width">
                        <label>Description</label>
                        <input type="text" id="edit-agent-description" value="${agent.description || ''}">
                    </div>
                    <div class="form-group">
                        <label>Model</label>
                        <select id="edit-agent-model">
                            <option value="claude-sonnet-4-5-20250929" ${agent.model === 'claude-sonnet-4-5-20250929' ? 'selected' : ''}>Claude Sonnet 4.5</option>
                            <option value="claude-3-5-sonnet-20241022" ${agent.model === 'claude-3-5-sonnet-20241022' ? 'selected' : ''}>Claude 3.5 Sonnet</option>
                            <option value="gpt-4o" ${agent.model === 'gpt-4o' ? 'selected' : ''}>GPT-4o</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Max Turns</label>
                        <input type="number" id="edit-agent-max-turns" value="${agent.max_turns || 30}" min="1" max="100">
                    </div>
                    <div class="form-group full-width">
                        <label>System Prompt</label>
                        <textarea id="edit-agent-system-prompt">${agent.system_prompt || ''}</textarea>
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="saveAgent('${agentId}')">Save Changes</button>
            `);
}

async function saveAgent(agentId) {
    const name = document.getElementById('edit-agent-name').value.trim();
    const description = document.getElementById('edit-agent-description').value.trim();
    const model = document.getElementById('edit-agent-model').value;
    const maxTurns = parseInt(document.getElementById('edit-agent-max-turns').value);
    const systemPrompt = document.getElementById('edit-agent-system-prompt').value.trim();

    if (!name) {
        showToast('Name is required', 'warning');
        return;
    }

    const result = await api('PUT', `/agents/${agentId}`, {
        name: name,
        description: description,
        model: model,
        max_turns: maxTurns,
        system_prompt: systemPrompt
    });

    if (result) {
        showToast(`Agent "${name}" updated`, 'success');
        closeModal();
        loadAgents();
    }
}

async function deleteAgent(agentId) {
    if (!confirm(`Delete agent "${agentId}"?\n\nThis will soft-delete the agent. You can restore it later from the platform admin.`)) {
        return;
    }
    const result = await api('DELETE', `/agents/${agentId}`);
    if (result) {
        showToast(`Agent "${agentId}" deleted`, 'success');
        loadAgents();
    }
}

// ====================================================================
// AGENT SKILLS
// ====================================================================
async function loadAgentSkills() {
    const agentId = state.selectedAgent;
    if (!agentId) return;

    const container = document.getElementById('agent-skills-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const data = await api('GET', `/agents/${agentId}/skills`);
    if (!data) {
        container.innerHTML = '<div class="empty-state">Failed to load skills</div>';
        return;
    }

    const globalSkills = data.global_skills || [];
    const privateSkills = data.private_skills || [];

    if (globalSkills.length === 0 && privateSkills.length === 0) {
        container.innerHTML = '<div class="empty-state">No skills configured for this agent</div>';
        return;
    }

    let html = '';

    // Global skills (read-only for customers)
    globalSkills.forEach(skill => {
        html += `
                    <div class="card ${skill.enabled ? '' : 'disabled'}">
                        <div class="card-header">
                            <span class="card-title">${skill.name || skill.id}</span>
                            <span class="card-badge global">GLOBAL</span>
                        </div>
                        <div class="card-description">${skill.description || 'No description'}</div>
                        <div class="card-meta">ID: ${skill.id}</div>
                        <div class="card-actions">
                            <button class="btn" onclick="viewSkill('${skill.id}', true)">View</button>
                        </div>
                    </div>
                `;
    });

    // Private skills
    privateSkills.forEach(skill => {
        html += `
                    <div class="card ${skill.enabled ? '' : 'disabled'}">
                        <div class="card-header">
                            <span class="card-title">${skill.name || skill.id}</span>
                            <span class="card-badge private">PRIVATE</span>
                        </div>
                        <div class="card-description">${skill.description || 'No description'}</div>
                        <div class="card-meta">ID: ${skill.id}</div>
                        <div class="card-actions">
                            <button class="btn" onclick="viewSkill('${skill.id}', false)">View</button>
                            <button class="btn" onclick="editSkill('${skill.id}')">Edit</button>
                            <button class="btn danger" onclick="deleteSkill('${skill.id}')">Delete</button>
                        </div>
                    </div>
                `;
    });

    container.innerHTML = html;
}

async function viewSkill(skillId, isGlobal) {
    const agentId = state.selectedAgent;
    const url = isGlobal ? `/skills/${skillId}` : `/agents/${agentId}/skills/${skillId}`;
    const skill = await api('GET', url);
    if (skill) {
        showModal(`Skill: ${skill.name || skillId}`, `
                    <div class="form-group"><label>ID</label><div>${skill.id}</div></div>
                    <div class="form-group"><label>Triggers</label><div>${skill.triggers?.join(', ') || 'None'}</div></div>
                    <div class="form-group"><label>Description</label><div>${skill.description || 'N/A'}</div></div>
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

async function deleteSkill(skillId) {
    if (!confirm(`Delete skill "${skillId}"?`)) return;
    const agentId = state.selectedAgent;
    const result = await api('DELETE', `/agents/${agentId}/skills/${skillId}`);
    if (result) {
        showToast(`Skill "${skillId}" deleted`, 'success');
        loadAgentSkills();
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
        fromTemplate: null,
        skippedFields: [],
        customFields: []
    };

    const agentId = state.selectedAgent;
    const data = await api('GET', `/agents/${agentId}/skills/${skillId}/editor`);

    if (!data) {
        showToast('Failed to load skill for editing. Skill may not have been created with the editor.', 'error');
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
        loadSkillForEditing(editSkillId);
    } else {
        renderSkillEditorStep0();
    }
}

async function renderSkillEditorStep0() {
    const [templatesData, vendorsData] = await Promise.all([
        api('GET', '/skills/templates', null, { silent: true }),
        api('GET', '/skills/vendors', null, { silent: true })
    ]);

    skillTemplates = templatesData?.templates || [];
    skillVendors = vendorsData?.vendors || [];

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
                        <div style="font-size: 0.75rem; color: #78716c; margin-top: 0.25rem;">Paste skill instructions</div>
                    </div>
                </div>
                <div id="template-list" style="display: none; margin-top: 1rem; max-height: 300px; overflow-y: auto;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">Choose a Template:</div>
                    ${skillTemplates.map(t => `
                        <div class="card" style="cursor: pointer; margin-bottom: 0.5rem;" onclick="selectTemplate('${t.id}')">
                            <div class="card-header">
                                <span class="card-title" style="font-size: 0.875rem;">${t.name}</span>
                                <span class="card-badge">${t.category}</span>
                            </div>
                            <div class="card-description" style="font-size: 0.75rem;">${t.description}</div>
                        </div>
                    `).join('') || '<div class="empty-state">No templates available</div>'}
                </div>
                <div id="vendor-list" style="display: none; margin-top: 1rem; max-height: 300px; overflow-y: auto;">
                    <div style="font-weight: 600; margin-bottom: 0.5rem;">Choose a Vendor:</div>
                    ${skillVendors.filter(v => v.enabled).map(v => `
                        <div class="card" style="cursor: pointer; margin-bottom: 0.5rem;" onclick="selectVendor('${v.id}')">
                            <div class="card-header">
                                <span class="card-title" style="font-size: 0.875rem;">${v.name}</span>
                                <span class="card-badge">${v.skill_count} skills</span>
                            </div>
                            <div class="card-description" style="font-size: 0.75rem;">${v.description || 'External skill provider'}</div>
                        </div>
                    `).join('') || '<div class="empty-state">No vendors available</div>'}
                </div>
                <div id="import-skill-form" style="display: none; margin-top: 1rem;">
                    <p style="font-size: 0.875rem; color: #57534e; margin-bottom: 1rem;">
                        Paste your skill instructions below. The metadata will be auto-generated.
                    </p>
                    <div class="form-group">
                        <label>Skill Instructions (Markdown) <span style="color: #dc2626;">*</span></label>
                        <textarea id="import-skill-md" rows="8" placeholder="# My Skill Name&#10;&#10;## Purpose&#10;Describe what this skill does..."></textarea>
                    </div>
                    <div style="margin-top: 1rem;">
                        <button class="btn primary" onclick="importSkillFromWizard()">
                            <span id="import-btn-text">Import Skill</span>
                            <span id="import-btn-loading" class="loading" style="display: none; margin-left: 0.5rem;"></span>
                        </button>
                        <button class="btn" onclick="renderSkillEditorStep0()" style="margin-left: 0.5rem;">Back</button>
                    </div>
                </div>
            `, '<button class="btn" onclick="closeModal()">Cancel</button>');
}

function showTemplateList() {
    document.getElementById('template-list').style.display = 'block';
    document.getElementById('vendor-list').style.display = 'none';
    document.getElementById('import-skill-form').style.display = 'none';
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

function startFromScratch() {
    skillEditorState.step = 1;
    skillEditorState.fromTemplate = null;
    renderSkillEditorStep1();
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

function generateId(prefix) {
    return prefix + '_' + Math.random().toString(36).substring(2, 8);
}

function renderSkillEditorStep2() {
    const h = skillEditorState.hypothesis;
    const fields = skillEditorState.form.fields || [];

    fields.forEach(f => {
        if (skillEditorState.answers[f.id] === undefined && f.default !== undefined) {
            skillEditorState.answers[f.id] = f.default;
        }
    });

    const fieldsHtml = fields.map(f => renderFormField(f)).join('');

    showModal(`${skillEditorState.editingSkillId ? 'Edit' : 'Create'} Skill - Customize`, `
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
                    <div style="font-size: 0.75rem; color: #78716c; margin-bottom: 0.25rem;">Add any additional requirements not covered above.</div>
                    <textarea id="skill-editor-other" rows="3" placeholder="Any additional instructions..." onchange="updateFieldAnswer('_other', this.value)">${skillEditorState.answers['_other'] || ''}</textarea>
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
    const skillId = document.getElementById('skill-editor-id').value.trim();
    const fields = skillEditorState.form.fields || [];

    // Validate required fields (skip those marked as skipped)
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

    document.getElementById('save-btn-text').textContent = 'Generating...';
    document.getElementById('save-btn-loading').style.display = 'inline-block';
    document.getElementById('skill-editor-error2').style.display = 'none';

    const agentId = state.selectedAgent;

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

async function importSkillFromWizard() {
    const agentId = state.selectedAgent;
    const mdContent = document.getElementById('import-skill-md').value.trim();

    if (!mdContent) {
        showToast('Please paste the skill instructions', 'error');
        return;
    }

    document.getElementById('import-btn-text').textContent = 'Importing...';
    document.getElementById('import-btn-loading').style.display = 'inline-block';

    const result = await api('POST', `/agents/${agentId}/skills/import`, {
        skill_md: mdContent
    });

    document.getElementById('import-btn-text').textContent = 'Import Skill';
    document.getElementById('import-btn-loading').style.display = 'none';

    if (result && result.status === 'ok') {
        showToast(`Skill "${result.skill_id}" imported!`, 'success');
        closeModal();
        loadAgentSkills();
    }
}

function selectTemplate(templateId) {
    const template = skillTemplates.find(t => t.id === templateId);
    if (template) {
        skillEditorState.fromTemplate = template;
        closeModal();
        loadTemplates();
        switchPanel('skills');
    }
}

function selectVendor(vendorId) {
    closeModal();
    loadVendorSkills();
    switchPanel('skills');
    setTimeout(() => {
        document.querySelector('[data-subpanel=skills-vendors]')?.click();
    }, 100);
}

async function loadSkillForEditing(skillId) {
    const agentId = state.selectedAgent;
    const data = await api('GET', `/agents/${agentId}/skills/${skillId}/editor`);
    if (data) {
        skillEditorState.form = data.form;
        skillEditorState.hypothesis = data.form.hypothesis;
        skillEditorState.answers = data.answers || {};
        renderSkillEditorStep2();
    }
}

// ====================================================================
// AGENT TASKS
// ====================================================================
async function loadAgentTasks() {
    const agentId = state.selectedAgent;
    if (!agentId) return;

    const container = document.getElementById('agent-tasks-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const data = await api('GET', `/agents/${agentId}/tasks`);
    if (!data || !data.tasks || data.tasks.length === 0) {
        container.innerHTML = '<div class="empty-state">No tasks scheduled for this agent</div>';
        return;
    }

    container.innerHTML = data.tasks.map(task => `
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">${task.name}</span>
                        <span class="card-badge ${task.enabled ? 'success' : ''}">${task.enabled ? 'ENABLED' : 'DISABLED'}</span>
                    </div>
                    <div class="card-description">${task.description || 'No description'}</div>
                    <div class="card-meta">
                        ID: ${task.task_id} | Type: ${task.schedule_type} | Runs: ${task.run_count || 0}
                    </div>
                    <div class="card-actions">
                        <button class="btn primary" onclick="triggerTask('${task.task_id}')">Run Now</button>
                        <button class="btn" onclick="${task.enabled ? 'disableTask' : 'enableTask'}('${task.task_id}')">${task.enabled ? 'Disable' : 'Enable'}</button>
                        <button class="btn danger" onclick="deleteTask('${task.task_id}')">Delete</button>
                    </div>
                </div>
            `).join('');
}

function updateScheduleFields() {
    const type = document.getElementById('task-schedule-type').value;
    document.getElementById('task-interval-group').classList.toggle('hidden', type !== 'interval');
    document.getElementById('task-cron-group').classList.toggle('hidden', type !== 'cron');
}

async function createAgentTask() {
    const agentId = state.selectedAgent;
    const taskId = document.getElementById('task-id').value.trim();
    const name = document.getElementById('task-name').value.trim();
    const scheduleType = document.getElementById('task-schedule-type').value;
    const interval = parseInt(document.getElementById('task-interval').value);
    const cron = document.getElementById('task-cron').value.trim();
    const content = document.getElementById('task-content').value.trim();

    if (!taskId || !name) {
        showToast('Task ID and Name are required', 'warning');
        return;
    }

    const payload = {
        task_id: taskId,
        name: name,
        schedule_type: scheduleType,
        agent_id: agentId,
        content: content
    };

    if (scheduleType === 'interval') payload.interval_seconds = interval;
    if (scheduleType === 'cron') payload.cron_expression = cron;

    const result = await api('POST', `/agents/${agentId}/tasks`, payload);
    if (result) {
        showToast(`Task "${name}" created`, 'success');
        document.getElementById('task-id').value = '';
        document.getElementById('task-name').value = '';
        document.getElementById('task-content').value = '';
        loadAgentTasks();
    }
}

async function triggerTask(taskId) {
    const result = await api('POST', `/api/tasks/${taskId}/trigger`);
    if (result) {
        showToast('Task triggered', 'success');
    }
}

async function enableTask(taskId) {
    const result = await api('PUT', `/api/tasks/${taskId}/enable`);
    if (result) {
        showToast('Task enabled', 'success');
        loadAgentTasks();
    }
}

async function disableTask(taskId) {
    const result = await api('PUT', `/api/tasks/${taskId}/disable`);
    if (result) {
        showToast('Task disabled', 'success');
        loadAgentTasks();
    }
}

async function deleteTask(taskId) {
    if (!confirm(`Delete task "${taskId}"?`)) return;
    const result = await api('DELETE', `/api/tasks/${taskId}`);
    if (result) {
        showToast('Task deleted', 'success');
        loadAgentTasks();
    }
}

// ====================================================================
// AGENT SESSIONS
// ====================================================================
async function loadAgentSessions() {
    const agentId = state.selectedAgent;
    if (!agentId) return;

    const tbody = document.getElementById('agent-sessions-table');
    const data = await api('GET', `/agents/${agentId}/sessions`);
    if (!data || !data.sessions || data.sessions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">No sessions</td></tr>';
        return;
    }

    tbody.innerHTML = data.sessions.map(s => `
                <tr>
                    <td>${s.session_id}</td>
                    <td><span class="card-badge ${s.status === 'active' ? 'success' : ''}">${s.status}</span></td>
                    <td>${new Date(s.created_at).toLocaleString()}</td>
                    <td>${s.message_count || 0}</td>
                    <td>
                        <button class="btn danger" onclick="deleteSession('${s.session_id}')">Delete</button>
                    </td>
                </tr>
            `).join('');
}

async function deleteSession(sessionId) {
    if (!confirm('Delete this session?')) return;
    const agentId = state.selectedAgent;
    const result = await api('DELETE', `/agents/${agentId}/sessions/${sessionId}`);
    if (result) {
        showToast('Session deleted', 'success');
        loadAgentSessions();
    }
}

// ====================================================================
// AGENT RUNS
// ====================================================================
async function loadAgentRuns() {
    const agentId = state.selectedAgent;
    if (!agentId) return;

    const date = document.getElementById('runs-date').value;
    let url = `/agents/${agentId}/runs?limit=50`;
    if (date) url += `&date=${encodeURIComponent(date)}`;

    const tbody = document.getElementById('agent-runs-table');
    const data = await api('GET', url);
    if (!data || !data.runs || data.runs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align: center;">No runs</td></tr>';
        return;
    }

    tbody.innerHTML = data.runs.map(r => `
                <tr>
                    <td>${r.run_id}</td>
                    <td>${r.date || 'N/A'}</td>
                    <td><span class="card-badge ${r.status === 'completed' ? 'success' : r.status === 'error' ? 'error' : ''}">${r.status || 'unknown'}</span></td>
                    <td>${r.turns || 0}</td>
                    <td>${r.duration_ms || 0}ms</td>
                    <td>
                        <button class="btn" onclick="viewRun('${agentId}', '${r.date}', '${r.run_id}')">Details</button>
                    </td>
                </tr>
            `).join('');
}

async function viewRun(agentId, date, runId) {
    const run = await api('GET', `/agents/${agentId}/runs/${date}/${runId}`);
    if (run) {
        showModal(`Run: ${runId}`, `
                    <pre style="white-space: pre-wrap; max-height: 400px; overflow-y: auto; font-size: 0.75rem;">${JSON.stringify(run, null, 2)}</pre>
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

// ====================================================================
// SKILL TEMPLATES
// ====================================================================
async function loadTemplates() {
    const container = document.getElementById('templates-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const data = await api('GET', '/skills/templates');
    if (!data || !data.templates || data.templates.length === 0) {
        container.innerHTML = '<div class="empty-state">No templates available</div>';
        return;
    }

    container.innerHTML = data.templates.map(t => `
                <div class="card">
                    <div class="card-header">
                        <span class="card-title">${t.name}</span>
                        <span class="card-badge">${t.category || 'General'}</span>
                    </div>
                    <div class="card-description">${t.description || 'No description'}</div>
                    <div class="card-actions">
                        <button class="btn" onclick="viewTemplate('${t.id}')">Preview</button>
                        <button class="btn primary" onclick="useTemplate('${t.id}')">Use Template</button>
                    </div>
                </div>
            `).join('');
}

async function viewTemplate(templateId) {
    const template = await api('GET', `/skills/templates/${templateId}`);
    if (template) {
        showModal(`Template: ${template.name}`, `
                    <div class="form-group"><label>Description</label><div>${template.description}</div></div>
                    <div class="form-group"><label>Category</label><div>${template.category || 'General'}</div></div>
                    <div class="form-group"><label>Triggers</label><div>${template.skill_json?.triggers?.join(', ') || 'None'}</div></div>
                `, '<button class="btn" onclick="closeModal()">Close</button>');
    }
}

async function useTemplate(templateId) {
    if (!state.selectedAgent && state.agents.length > 0) {
        state.selectedAgent = state.agents[0].agent_id;
    }
    if (!state.selectedAgent) {
        showToast('Please create an agent first', 'warning');
        return;
    }

    const template = await api('GET', `/skills/templates/${templateId}`);
    if (!template) return;

    showModal(`Use Template: ${template.name}`, `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Agent</label>
                        <select id="template-agent">
                            ${state.agents.map(a => `<option value="${a.agent_id}">${a.name}</option>`).join('')}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Skill ID *</label>
                        <input type="text" id="template-skill-id" value="${template.skill_json?.id || templateId}" placeholder="my_skill">
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="installTemplate('${templateId}')">Install Skill</button>
            `);
}

async function installTemplate(templateId) {
    const agentId = document.getElementById('template-agent').value;
    const skillId = document.getElementById('template-skill-id').value.trim();

    if (!skillId) {
        showToast('Please enter a skill ID', 'warning');
        return;
    }

    const result = await api('POST', `/agents/${agentId}/skills/from-template?template_id=${templateId}&skill_id=${skillId}`);
    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" installed`, 'success');
        closeModal();
    }
}

// ====================================================================
// VENDOR SKILLS
// ====================================================================
async function loadVendorSkills() {
    const container = document.getElementById('vendor-skills-list');
    container.innerHTML = '<div class="empty-state"><span class="loading"></span> Loading...</div>';

    const data = await api('GET', '/skills/vendors');
    if (!data || !data.vendors || data.vendors.length === 0) {
        container.innerHTML = '<div class="empty-state">No vendors available</div>';
        return;
    }

    let html = '';
    for (const vendor of data.vendors) {
        if (!vendor.enabled) continue;
        const skills = vendor.skills || [];
        for (const skill of skills) {
            html += `
                        <div class="card">
                            <div class="card-header">
                                <span class="card-title">${skill.name}</span>
                                <span class="card-badge">${vendor.name}</span>
                            </div>
                            <div class="card-description">${skill.description || 'No description'}</div>
                            <div class="card-meta">Tags: ${(skill.tags || []).join(', ') || 'None'}</div>
                            <div class="card-actions">
                                <button class="btn primary" onclick="installVendorSkill('${vendor.id}', '${skill.id}')">Install</button>
                            </div>
                        </div>
                    `;
        }
    }

    container.innerHTML = html || '<div class="empty-state">No vendor skills available</div>';
}

async function installVendorSkill(vendorId, skillId) {
    if (!state.selectedAgent && state.agents.length > 0) {
        state.selectedAgent = state.agents[0].agent_id;
    }
    if (!state.selectedAgent) {
        showToast('Please create an agent first', 'warning');
        return;
    }

    showModal('Install Vendor Skill', `
                <div class="form-grid">
                    <div class="form-group">
                        <label>Agent</label>
                        <select id="vendor-skill-agent">
                            ${state.agents.map(a => `<option value="${a.agent_id}">${a.name}</option>`).join('')}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Skill ID *</label>
                        <input type="text" id="vendor-skill-id" value="${skillId}" placeholder="my_skill">
                    </div>
                </div>
            `, `
                <button class="btn" onclick="closeModal()">Cancel</button>
                <button class="btn primary" onclick="doInstallVendorSkill('${vendorId}', '${skillId}')">Install</button>
            `);
}

async function doInstallVendorSkill(vendorId, originalSkillId) {
    const agentId = document.getElementById('vendor-skill-agent').value;
    const skillId = document.getElementById('vendor-skill-id').value.trim();

    if (!skillId) {
        showToast('Please enter a skill ID', 'warning');
        return;
    }

    const result = await api('POST', `/agents/${agentId}/skills/from-vendor`, {
        vendor_id: vendorId,
        vendor_skill_id: originalSkillId,
        skill_id: skillId
    });

    if (result && result.status === 'ok') {
        showToast(`Skill "${skillId}" installed`, 'success');
        closeModal();
    }
}

// ====================================================================
// CHAT FUNCTIONS
// ====================================================================
async function loadChatAgents() {
    const select = document.getElementById('chat-agent-select');
    const currentValue = select.value;

    const data = await api('GET', '/agents');
    if (data && data.agents) {
        select.innerHTML = '<option value="">Select agent...</option>';
        for (const agent of data.agents) {
            if (!agent.is_deleted) {
                select.innerHTML += `<option value="${agent.agent_id}" ${agent.agent_id === currentValue ? 'selected' : ''}>${agent.name || agent.agent_id}</option>`;
            }
        }
    }

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

    const messagesDiv = document.getElementById('chat-messages');
    const emptyState = document.getElementById('chat-empty');
    if (emptyState) emptyState.remove();

    addChatMessage('user', message);
    input.value = '';

    document.getElementById('chat-send-text').style.display = 'none';
    document.getElementById('chat-send-loading').style.display = 'inline-block';
    document.getElementById('chat-send-btn').disabled = true;

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

    const result = await api('POST', `/agents/${agentId}/run`, {
        message: message,
        session_id: state.chat.sessionId,
        skill_id: skillId
    });

    document.getElementById(thinkingId)?.remove();

    document.getElementById('chat-send-text').style.display = 'inline';
    document.getElementById('chat-send-loading').style.display = 'none';
    document.getElementById('chat-send-btn').disabled = false;

    if (result) {
        state.chat.sessionId = result.session_id;
        document.getElementById('chat-session-id').textContent = result.session_id?.substring(0, 12) + '...' || 'Active';

        if (result.response) {
            addChatMessage('assistant', result.response, {
                turns: result.turns,
                tokens: result.total_tokens,
                duration: result.duration_ms
            });
        } else if (result.error) {
            addChatMessage('error', result.error);
        }
    } else {
        addChatMessage('error', 'Failed to get response from agent');
    }

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

    messagesDiv.innerHTML += `
                <div style="display: flex; gap: 0.75rem; margin-bottom: 1rem; ${isUser ? 'flex-direction: row-reverse;' : ''}">
                    <div style="width: 32px; height: 32px; border-radius: 50%; background: ${avatarBg}; color: white; display: flex; align-items: center; justify-content: center; font-weight: 600; flex-shrink: 0;">${avatar}</div>
                    <div style="flex: 1; max-width: 80%; padding: 0.75rem; background: ${messageBg}; border-radius: 4px; border: 1px solid ${borderColor};">
                        <div style="white-space: pre-wrap; word-wrap: break-word;">${escapeHtml(content)}</div>
                        ${metaHtml}
                    </div>
                </div>
            `;

    state.chat.messages.push({ role, content, meta });
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ====================================================================
// INITIALIZATION
// ====================================================================
document.addEventListener('DOMContentLoaded', async () => {
    const authed = await checkAuth();
    if (authed) {
        loadDashboard();
    }
});
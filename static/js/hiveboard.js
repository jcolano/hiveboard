
// ═══════════════════════════════════════════
//  DATA
// ═══════════════════════════════════════════

const AGENTS = [
    {
        id: 'lead-qualifier', type: 'sales', status: 'processing', task: 'task_lead-4821', hb: 4, sparkline: [3, 5, 4, 6, 8, 7, 5, 6, 4, 3, 7, 8],
        queueDepth: 3, activeIssues: 0, processingSummary: 'Scoring lead #4821'
    },
    {
        id: 'support-triage', type: 'support', status: 'stuck', task: 'task_ticket-991', hb: 342, sparkline: [6, 5, 7, 4, 3, 2, 1, 0, 0, 0, 0, 0],
        queueDepth: 7, activeIssues: 1, processingSummary: null
    },
    {
        id: 'doc-processor', type: 'data', status: 'processing', task: 'task_doc-2288', hb: 8, sparkline: [2, 4, 3, 5, 4, 6, 5, 7, 6, 4, 5, 6],
        queueDepth: 1, activeIssues: 0, processingSummary: 'Extracting tables from PDF'
    },
    {
        id: 'email-responder', type: 'support', status: 'waiting_approval', task: 'task_email-773', hb: 12, sparkline: [4, 3, 5, 6, 4, 5, 3, 4, 5, 6, 4, 5],
        queueDepth: 2, activeIssues: 0, processingSummary: 'Awaiting approval: contract email'
    },
    {
        id: 'data-enricher', type: 'data', status: 'idle', task: null, hb: 22, sparkline: [5, 6, 4, 3, 5, 4, 3, 2, 1, 0, 0, 0],
        queueDepth: 0, activeIssues: 0, processingSummary: null
    },
    {
        id: 'code-reviewer', type: 'coding', status: 'error', task: 'task_pr-445', hb: 67, sparkline: [3, 4, 5, 6, 7, 5, 4, 3, 2, 1, 1, 0],
        queueDepth: 4, activeIssues: 2, processingSummary: null
    },
];

const TASKS = [
    { id: 'task_lead-4821', agent: 'lead-qualifier', type: 'lead_processing', status: 'completed', duration: '12.4s', cost: '$0.08', llmCalls: 2, time: '2s ago' },
    { id: 'task_ticket-991', agent: 'support-triage', type: 'ticket_resolution', status: 'stuck', duration: '5m42s', cost: '$0.23', llmCalls: 3, time: '5m ago' },
    { id: 'task_doc-2288', agent: 'doc-processor', type: 'doc_extraction', status: 'processing', duration: '—', cost: '$0.04', llmCalls: 1, time: '18s ago' },
    { id: 'task_email-773', agent: 'email-responder', type: 'email_response', status: 'waiting', duration: '1m12s', cost: '$0.06', llmCalls: 1, time: '1m ago' },
    { id: 'task_pr-445', agent: 'code-reviewer', type: 'code_review', status: 'failed', duration: '8.1s', cost: '$0.12', llmCalls: 1, time: '3m ago' },
    { id: 'task_lead-4820', agent: 'lead-qualifier', type: 'lead_processing', status: 'completed', duration: '9.8s', cost: '$0.07', llmCalls: 2, time: '5m ago' },
    { id: 'task_lead-4819', agent: 'lead-qualifier', type: 'lead_processing', status: 'completed', duration: '14.2s', cost: '$0.09', llmCalls: 3, time: '12m ago' },
    { id: 'task_doc-2287', agent: 'doc-processor', type: 'doc_extraction', status: 'completed', duration: '6.3s', cost: '$0.03', llmCalls: 1, time: '15m ago' },
    { id: 'task_ticket-990', agent: 'support-triage', type: 'ticket_resolution', status: 'escalated', duration: '2m18s', cost: '$0.15', llmCalls: 2, time: '20m ago' },
];

// Per-task timelines (v3: LLM nodes and plan data)
const TIMELINES = {
    'task_lead-4821': {
        plan: {
            steps: [
                { desc: 'Fetch CRM data', status: 'completed' },
                { desc: 'Enrich company', status: 'completed' },
                { desc: 'Score lead', status: 'completed' },
                { desc: 'Route or escalate', status: 'completed' },
            ]
        },
        nodes: [
            { label: 'task_received', time: '14:32:01.0', type: 'system', detail: { event: 'task_started', source: 'webhook', task_type: 'lead_processing' } },
            { label: 'agent_assigned', time: '14:32:01.2', type: 'system', dur: '0.2s', detail: { agent: 'lead-qualifier', assignment: 'round-robin' } },
            { label: 'fetch_crm_data', time: '14:32:01.4', type: 'action', dur: '1.8s', detail: { action: 'CRM API call', records: 3, api: 'Salesforce' }, tags: ['crm', 'salesforce'] },
            { label: 'enrich_company', time: '14:32:03.2', type: 'action', dur: '2.1s', detail: { action: 'Company enrichment', source: 'Clearbit', fields: 12 }, tags: ['enrichment'] },
            { label: 'score_lead', time: '14:32:05.3', type: 'llm', dur: '3.4s', detail: { action: 'LLM scoring', model: 'claude-sonnet-4-20250514', tokens_in: 1240, tokens_out: 602, cost: '$0.04' }, tags: ['llm', 'scoring'], llmModel: 'sonnet-4' },
            { label: 'below_threshold', time: '14:32:08.7', type: 'warning', dur: '—', detail: { event: 'decision', score: 42, threshold: 80, result: 'escalate' } },
            { label: 'escalated', time: '14:32:08.9', type: 'warning', dur: '0.2s', detail: { event: 'escalated', reason: 'Score below threshold', assigned_to: 'sales-team' } },
            { label: 'approval_requested', time: '14:32:09.1', type: 'human', dur: '—', detail: { event: 'approval_requested', approver: 'ops-queue' } },
            { label: 'human_approved', time: '14:32:12.8', type: 'human', dur: '3.7s', detail: { event: 'approval_received', approved_by: 'jane@acme.com', decision: 'approved' } },
            { label: 'route_lead', time: '14:32:13.0', type: 'llm', dur: '0.3s', detail: { action: 'Route decision', model: 'claude-haiku-4-5-20251001', tokens_in: 320, tokens_out: 45, cost: '$0.002' }, tags: ['llm', 'routing'], llmModel: 'haiku-4.5' },
            { label: 'task_completed', time: '14:32:13.4', type: 'success', dur: '0.6s', detail: { event: 'task_completed', total_duration: '12.4s', cost: '$0.08' } },
        ]
    },
    'task_pr-445': {
        plan: null,
        nodes: [
            { label: 'task_received', time: '14:28:10.0', type: 'system', detail: { event: 'task_started', source: 'github-webhook', task_type: 'code_review' } },
            { label: 'agent_assigned', time: '14:28:10.1', type: 'system', dur: '0.1s', detail: { agent: 'code-reviewer', assignment: 'type-match' } },
            { label: 'fetch_pr_diff', time: '14:28:10.3', type: 'action', dur: '1.2s', detail: { action: 'GitHub API', pr: '#445', files: 7 }, tags: ['github'] },
            { label: 'analyze_changes', time: '14:28:11.5', type: 'llm', dur: '2.8s', detail: { action: 'LLM analysis', model: 'gpt-4o', tokens_in: 2400, tokens_out: 800, cost: '$0.06' }, tags: ['llm'], llmModel: 'gpt-4o' },
            { label: 'post_review ✗', time: '14:28:14.3', type: 'error', dur: '0.4s', detail: { action: 'post_review', error_type: 'RateLimitError', message: 'GitHub API rate limit exceeded', status_code: 429 }, tags: ['github', 'error'], isBranchStart: true },
            { label: 'retry #1', time: '14:28:16.3', type: 'retry', dur: '2.0s', detail: { event: 'retry_started', attempt: 1, backoff: '2s', strategy: 'exponential' }, isBranch: true },
            { label: 'retry #1 ✗', time: '14:28:18.5', type: 'error', dur: '0.2s', detail: { error_type: 'RateLimitError', message: 'Still rate limited', status_code: 429 }, isBranch: true },
            { label: 'retry #2', time: '14:28:22.5', type: 'retry', dur: '4.0s', detail: { event: 'retry_started', attempt: 2, backoff: '4s', strategy: 'exponential' }, isBranch: true },
            { label: 'retry #2 ✗', time: '14:28:26.7', type: 'error', dur: '0.2s', detail: { error_type: 'RateLimitError', message: 'Max retries exhausted', status_code: 429 }, isBranch: true, isBranchEnd: true },
            { label: 'task_failed', time: '14:28:26.9', type: 'error', dur: '0.2s', detail: { event: 'task_failed', reason: 'Max retries exceeded', total_duration: '8.1s', cost: '$0.12' } },
        ]
    },
    'task_ticket-991': {
        plan: {
            steps: [
                { desc: 'Classify ticket', status: 'completed' },
                { desc: 'Search knowledge base', status: 'failed' },
                { desc: 'Draft response', status: 'pending' },
                { desc: 'Send to customer', status: 'pending' },
            ]
        },
        nodes: [
            { label: 'task_received', time: '14:26:18.0', type: 'system', detail: { event: 'task_started', source: 'zendesk-webhook', task_type: 'ticket_resolution' } },
            { label: 'agent_assigned', time: '14:26:18.2', type: 'system', dur: '0.2s', detail: { agent: 'support-triage', assignment: 'round-robin' } },
            { label: 'classify_ticket', time: '14:26:18.4', type: 'llm', dur: '1.6s', detail: { action: 'Ticket classification', model: 'claude-haiku-4-5-20251001', tokens_in: 580, tokens_out: 120, cost: '$0.01', category: 'billing', priority: 'high' }, tags: ['llm', 'classification'], llmModel: 'haiku-4.5' },
            { label: 'search_kb', time: '14:26:20.0', type: 'action', dur: '2.2s', detail: { action: 'Knowledge base search', results: 0, query: 'billing dispute refund' }, tags: ['kb-search'] },
            { label: '⚠ no heartbeat', time: '14:26:22.2', type: 'stuck', dur: '5m+', detail: { event: 'stuck_detected', last_heartbeat: '5m20s ago', threshold: '5m', probable_cause: 'KB search returned 0 results, agent looping' } },
        ]
    },
    'task_email-773': {
        plan: null,
        nodes: [
            { label: 'task_received', time: '14:30:48.0', type: 'system', detail: { event: 'task_started', source: 'email-ingest', task_type: 'email_response' } },
            { label: 'agent_assigned', time: '14:30:48.1', type: 'system', dur: '0.1s', detail: { agent: 'email-responder', assignment: 'type-match' } },
            { label: 'parse_email', time: '14:30:48.3', type: 'action', dur: '0.4s', detail: { action: 'Email parsing', from: 'client@corp.com', subject: 'Contract renewal' }, tags: ['email'] },
            { label: 'draft_response', time: '14:30:48.7', type: 'llm', dur: '2.1s', detail: { action: 'LLM draft', model: 'claude-sonnet-4-20250514', tokens_in: 680, tokens_out: 300, cost: '$0.02' }, tags: ['llm'], llmModel: 'sonnet-4' },
            { label: 'approval_requested', time: '14:30:50.8', type: 'human', dur: '—', detail: { event: 'approval_requested', approver: 'support-lead', reason: 'Contract emails require human review' } },
            { label: '◉ waiting…', time: '—', type: 'waiting', dur: '1m12s+', detail: { event: 'waiting_approval', elapsed: '1m12s', approver: 'support-lead' } },
        ]
    },
};

// Fill default timelines
TASKS.forEach(t => {
    if (!TIMELINES[t.id]) {
        TIMELINES[t.id] = {
            plan: null,
            nodes: [
                { label: 'task_received', time: '—', type: 'system', detail: { event: 'task_started', task_type: t.type } },
                { label: 'agent_assigned', time: '—', type: 'system', dur: '0.2s', detail: { agent: t.agent } },
                { label: 'processing…', time: '—', type: 'action', dur: t.duration, detail: { status: t.status } },
                { label: t.status === 'completed' ? 'task_completed' : t.status, time: '—', type: t.status === 'completed' ? 'success' : 'system', detail: { duration: t.duration, cost: t.cost } },
            ]
        };
    }
});

// v3: Pipeline data per agent
const PIPELINE = {
    'lead-qualifier': {
        queue: [
            { id: 'lead-4822', priority: 'normal', source: 'webhook', summary: 'Acme Corp — inbound form', age: '12s' },
            { id: 'lead-4823', priority: 'high', source: 'webhook', summary: 'BigCo — demo request', age: '8s' },
            { id: 'lead-4824', priority: 'low', source: 'import', summary: 'SmallBiz — newsletter signup', age: '2s' },
        ],
        todos: [],
        scheduled: [
            { name: 'CRM sync check', next: '14:45:00', interval: '15m', status: 'ok' },
            { name: 'Stale lead sweep', next: '15:00:00', interval: '1h', status: 'ok' },
        ],
        issues: []
    },
    'support-triage': {
        queue: [
            { id: 'ticket-992', priority: 'high', source: 'zendesk', summary: 'Billing dispute — enterprise client', age: '3m' },
            { id: 'ticket-993', priority: 'normal', source: 'zendesk', summary: 'Password reset failed', age: '2m' },
            { id: 'ticket-994', priority: 'normal', source: 'email', summary: 'Feature request: export to CSV', age: '1m' },
            { id: 'ticket-995', priority: 'low', source: 'zendesk', summary: 'General inquiry — pricing', age: '45s' },
            { id: 'ticket-996', priority: 'normal', source: 'zendesk', summary: 'API key rotation help', age: '30s' },
            { id: 'ticket-997', priority: 'high', source: 'email', summary: 'Service down — customer report', age: '15s' },
            { id: 'ticket-998', priority: 'normal', source: 'zendesk', summary: 'Webhook configuration issue', age: '5s' },
        ],
        todos: [
            { id: 'todo-1', summary: 'Retry ticket-991 after KB update', priority: 'high', source: 'failed_action', status: 'created' },
        ],
        scheduled: [
            { name: 'SLA check', next: '14:35:00', interval: '5m', status: 'ok' },
        ],
        issues: [
            { id: 'issue-1', summary: 'Knowledge base returning 0 results for billing queries', severity: 'high', category: 'data_quality', occurrences: 3 },
        ]
    },
    'code-reviewer': {
        queue: [
            { id: 'pr-446', priority: 'normal', source: 'github', summary: 'feat: add user preferences API', age: '8m' },
            { id: 'pr-447', priority: 'normal', source: 'github', summary: 'fix: memory leak in websocket handler', age: '5m' },
            { id: 'pr-448', priority: 'high', source: 'github', summary: 'security: update dependencies', age: '2m' },
            { id: 'pr-449', priority: 'low', source: 'github', summary: 'docs: update API reference', age: '1m' },
        ],
        todos: [
            { id: 'todo-2', summary: 'Retry PR #445 review (rate limit cleared)', priority: 'high', source: 'failed_action', status: 'created' },
            { id: 'todo-3', summary: 'Check GitHub API rate limit reset time', priority: 'normal', source: 'agent_decision', status: 'created' },
        ],
        scheduled: [
            { name: 'Rate limit check', next: '14:33:00', interval: '1m', status: 'warning' },
        ],
        issues: [
            { id: 'issue-2', summary: 'GitHub API rate limit exceeded', severity: 'critical', category: 'rate_limit', occurrences: 5 },
            { id: 'issue-3', summary: 'PR diff too large for context window (>100K tokens)', severity: 'medium', category: 'configuration', occurrences: 1 },
        ]
    },
    'doc-processor': { queue: [{ id: 'doc-2289', priority: 'normal', source: 'upload', summary: 'Q4 financial report.pdf', age: '4s' }], todos: [], scheduled: [{ name: 'OCR queue drain', next: '14:40:00', interval: '10m', status: 'ok' }], issues: [] },
    'email-responder': { queue: [{ id: 'email-774', priority: 'normal', source: 'ingest', summary: 'Re: Partnership inquiry', age: '30s' }, { id: 'email-775', priority: 'low', source: 'ingest', summary: 'Auto-reply: Out of office', age: '10s' }], todos: [], scheduled: [], issues: [] },
    'data-enricher': { queue: [], todos: [], scheduled: [{ name: 'Batch enrichment', next: '15:00:00', interval: '1h', status: 'ok' }], issues: [] },
};

// v3: Cost data
const COST_DATA = {
    byAgent: [
        { agent: 'support-triage', calls: 48, tokens_in: 28400, tokens_out: 9200, cost: 1.82 },
        { agent: 'lead-qualifier', calls: 36, tokens_in: 22100, tokens_out: 7800, cost: 1.44 },
        { agent: 'code-reviewer', calls: 12, tokens_in: 38200, tokens_out: 12400, cost: 2.14 },
        { agent: 'doc-processor', calls: 18, tokens_in: 14600, tokens_out: 4200, cost: 0.68 },
        { agent: 'email-responder', calls: 24, tokens_in: 16800, tokens_out: 7200, cost: 0.92 },
        { agent: 'data-enricher', calls: 8, tokens_in: 4800, tokens_out: 1600, cost: 0.24 },
    ],
    byModel: [
        { model: 'claude-sonnet-4-20250514', calls: 52, tokens_in: 48200, tokens_out: 18600, cost: 3.86 },
        { model: 'claude-haiku-4-5-20251001', calls: 64, tokens_in: 32400, tokens_out: 12800, cost: 1.24 },
        { model: 'gpt-4o', calls: 30, tokens_in: 44300, tokens_out: 11000, cost: 2.14 },
    ],
    total: { calls: 146, tokens_in: 124900, tokens_out: 42400, cost: 7.24, period: '1h' }
};

const STREAM_EVENTS = [
    { type: 'task_completed', agent: 'lead-qualifier', task: 'task_lead-4821', summary: 'Lead scored 42 → escalated → approved', time: '2s ago', severity: 'info' },
    { type: 'custom', kind: 'llm_call', agent: 'lead-qualifier', task: 'task_lead-4821', summary: 'route_lead: haiku-4.5 (320→45) $0.002', time: '3s ago', severity: 'info' },
    { type: 'approval_received', agent: 'email-responder', task: 'task_email-773', summary: 'Human approved email response draft', time: '8s ago', severity: 'info' },
    { type: 'heartbeat', agent: 'doc-processor', task: null, summary: 'Extracting tables from PDF · Q:1', time: '12s ago', severity: 'debug' },
    { type: 'action_failed', agent: 'code-reviewer', task: 'task_pr-445', summary: 'GitHub API rate limit exceeded', time: '18s ago', severity: 'error' },
    { type: 'custom', kind: 'issue', agent: 'code-reviewer', task: null, summary: '⚑ GitHub API rate limit exceeded (×5)', time: '18s ago', severity: 'error' },
    { type: 'retry_started', agent: 'code-reviewer', task: 'task_pr-445', summary: 'Retry #2 with exponential backoff', time: '20s ago', severity: 'warn' },
    { type: 'custom', kind: 'llm_call', agent: 'lead-qualifier', task: 'task_lead-4821', summary: 'score_lead: sonnet-4 (1240→602) $0.04', time: '22s ago', severity: 'info' },
    { type: 'action_completed', agent: 'lead-qualifier', task: 'task_lead-4821', summary: 'score_lead completed in 3.4s', time: '25s ago', severity: 'info' },
    { type: 'custom', kind: 'queue_snapshot', agent: 'support-triage', task: null, summary: 'Queue: 7 items, oldest 3m', time: '30s ago', severity: 'debug' },
    { type: 'escalated', agent: 'support-triage', task: 'task_ticket-991', summary: 'Agent stuck: no progress for 5m', time: '38s ago', severity: 'warn' },
    { type: 'task_started', agent: 'doc-processor', task: 'task_doc-2288', summary: 'New document extraction task', time: '45s ago', severity: 'info' },
    { type: 'heartbeat', agent: 'lead-qualifier', task: null, summary: 'Scoring lead #4821 · Q:3', time: '1m ago', severity: 'debug' },
    { type: 'task_completed', agent: 'lead-qualifier', task: 'task_lead-4820', summary: 'Lead qualified → score 87 → auto-routed', time: '1m ago', severity: 'info' },
    { type: 'custom', kind: 'todo', agent: 'code-reviewer', task: null, summary: '☐ Retry PR #445 review (rate limit cleared)', time: '2m ago', severity: 'info' },
    { type: 'task_failed', agent: 'code-reviewer', task: 'task_pr-445', summary: 'Max retries exceeded on GitHub API', time: '3m ago', severity: 'error' },
];

// ═══════════════════════════════════════════
//  STATE
// ═══════════════════════════════════════════

let selectedAgent = null;
let selectedTask = 'task_lead-4821';
let activeStreamFilter = 'all';
let pinnedNode = null;
let statusFilter = null;
let currentView = 'mission'; // mission | cost | agentDetail
let agentDetailAgent = null;
let activeDetailTab = 'tasks';

// ═══════════════════════════════════════════
//  CONSTANTS
// ═══════════════════════════════════════════

const statusSort = { stuck: 0, error: 1, waiting_approval: 2, processing: 3, idle: 4 };
const statusLabel = { processing: 'Processing', stuck: 'Stuck', error: 'Error', idle: 'Idle', waiting_approval: 'Waiting', completed: 'Completed' };
const statusBadge = { processing: 'badge-processing', stuck: 'badge-stuck', error: 'badge-error', idle: 'badge-idle', waiting_approval: 'badge-waiting', completed: 'badge-completed' };
const statusColor = { completed: 'var(--success)', processing: 'var(--active)', failed: 'var(--error)', stuck: 'var(--stuck)', waiting: 'var(--warning)', escalated: 'var(--warning)' };
const typeColor = { system: 'var(--idle)', action: 'var(--active)', warning: 'var(--warning)', human: 'var(--success)', success: 'var(--success)', error: 'var(--error)', retry: 'var(--warning)', stuck: 'var(--stuck)', waiting: 'var(--warning)', llm: 'var(--llm)' };
const SEVERITY_COLOR = { debug: 'var(--idle)', info: 'var(--active)', warn: 'var(--warning)', error: 'var(--error)' };
const STREAM_FILTERS = ['all', 'task', 'action', 'error', 'llm', 'pipeline', 'human'];
const KIND_ICON = { llm_call: '◆', queue_snapshot: '⊞', todo: '☐', issue: '⚑', scheduled: '⏲' };

// ═══════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════

function hbClass(seconds) { return seconds < 30 ? 'hb-fresh' : seconds < 120 ? 'hb-stale' : 'hb-dead'; }
function hbText(seconds) { return seconds < 60 ? seconds + 's ago' : Math.floor(seconds / 60) + 'm ago'; }
function fmtTokens(n) { return n >= 1000 ? (n / 1000).toFixed(1) + 'K' : String(n); }

// ═══════════════════════════════════════════
//  RENDERING — HIVE
// ═══════════════════════════════════════════

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

        // v3: Pipeline enrichment
        let pipelineHtml = '';
        const badges = [];
        if (a.queueDepth > 0) badges.push(`<span class="queue-badge ${a.queueDepth > 5 ? 'high' : ''}">Q:${a.queueDepth}</span>`);
        if (a.activeIssues > 0) badges.push(`<span class="issue-indicator"><span class="issue-dot"></span>${a.activeIssues} issue${a.activeIssues > 1 ? 's' : ''}</span>`);
        if (a.processingSummary) badges.push(`<span class="processing-line">↳ ${a.processingSummary}</span>`);
        if (badges.length > 0) pipelineHtml = `<div class="agent-card-pipeline">${badges.join('')}</div>`;

        return `
        <div class="agent-card fade-in ${isSelected ? 'selected' : ''} ${isUrgent ? 'urgency-glow' : ''}"
             onclick="selectAgent('${a.id}')" ondblclick="openAgentDetail('${a.id}')" data-agent="${a.id}">
          <div class="agent-card-top">
            <div class="agent-name">${a.id}</div>
            <div class="agent-status-badge ${statusBadge[a.status]}">${statusLabel[a.status]}</div>
          </div>
          <div class="agent-card-meta">
            <div class="agent-type-label">${a.type}</div>
            <div class="heartbeat-indicator"><div class="hb-dot ${hbClass(a.hb)}"></div>${hbText(a.hb)}</div>
          </div>
          ${pipelineHtml}
          ${a.task ? `<div class="agent-task-info"><span style="opacity:0.5">↳</span> <span class="clickable-entity" onclick="event.stopPropagation(); selectTask('${a.task}')">${a.task}</span></div>` : ''}
          <div class="sparkline-row">${a.sparkline.map(v => `<div class="spark-bar" style="height: ${(v / maxSpark) * 18 + 2}px; background: ${isUrgent ? 'var(--error)' : 'var(--active)'}"></div>`).join('')}</div>
        </div>`;
    }).join('');
}

// ═══════════════════════════════════════════
//  RENDERING — SUMMARY + METRICS
// ═══════════════════════════════════════════

function renderSummary() {
    const processing = AGENTS.filter(a => a.status === 'processing').length;
    const stuck = AGENTS.filter(a => a.status === 'stuck').length;
    const waiting = AGENTS.filter(a => a.status === 'waiting_approval').length;
    const errors = AGENTS.filter(a => a.status === 'error').length;
    const sf = statusFilter;
    document.getElementById('summaryBar').innerHTML = `
        <div class="summary-stat"><div class="stat-label">Total Agents</div><div class="stat-value">${AGENTS.length}</div></div>
        <div class="summary-stat clickable ${sf === 'processing' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('processing')"><div class="stat-label">Processing</div><div class="stat-value blue">${processing}</div></div>
        <div class="summary-stat clickable ${sf === 'waiting_approval' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('waiting_approval')"><div class="stat-label">Waiting</div><div class="stat-value amber">${waiting}</div></div>
        <div class="summary-stat clickable ${sf === 'stuck' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('stuck')"><div class="stat-label">Stuck</div><div class="stat-value red">${stuck}</div></div>
        <div class="summary-stat clickable ${sf === 'error' ? 'active-filter-stat' : ''}" onclick="toggleStatusFilter('error')"><div class="stat-label">Errors</div><div class="stat-value red">${errors}</div></div>
        <div class="summary-stat"><div class="stat-label">Success Rate (1h)</div><div class="stat-value green">87%</div></div>
        <div class="summary-stat"><div class="stat-label">Avg Duration</div><div class="stat-value">9.2s</div></div>
        <div class="summary-stat"><div class="stat-label">Cost (1h)</div><div class="stat-value purple">$${COST_DATA.total.cost.toFixed(2)}</div></div>
      `;
}

function renderMetrics() {
    const metrics = [
        { label: 'Throughput (1h)', data: [4, 6, 5, 8, 7, 9, 6, 8, 10, 7, 5, 8, 9, 7, 6, 8], color: 'var(--active)' },
        { label: 'Success Rate', data: [90, 88, 92, 85, 87, 90, 88, 91, 89, 87, 86, 88, 90, 87, 88, 87], color: 'var(--success)', max: 100 },
        { label: 'Errors', data: [1, 0, 1, 2, 1, 0, 1, 0, 0, 1, 2, 1, 0, 1, 0, 1], color: 'var(--error)' },
        { label: 'LLM Cost/Task', data: [8, 7, 9, 10, 8, 7, 8, 9, 7, 8, 10, 9, 8, 7, 8, 8], color: 'var(--llm)' },
    ];
    document.getElementById('metricsRow').innerHTML = metrics.map(m => {
        const mx = m.max || Math.max(...m.data, 1);
        return `<div class="metric-cell"><div class="stat-label">${m.label}</div><div class="metric-chart">${m.data.map(v => `<div class="metric-bar" style="height: ${(v / mx) * 22 + 2}px; background: ${m.color}; opacity: 0.6;"></div>`).join('')}</div></div>`;
    }).join('');
}

// ═══════════════════════════════════════════
//  RENDERING — TIMELINE (v3: LLM nodes + plan bar)
// ═══════════════════════════════════════════

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
        `<div class="plan-step ${s.status}"><div class="plan-step-tooltip">${s.desc}</div></div>`
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
        const color = typeColor[node.type];
        const filled = node.type === 'success' || node.type === 'error' || node.type === 'stuck';
        const isLlm = node.type === 'llm';
        const nodeIdx = nodes.indexOf(node);

        html += `<div class="tl-node ${isLlm ? 'llm-node' : ''}" data-idx="${nodeIdx}" onclick="pinNode(${nodeIdx})">`;
        if (isLlm && node.llmModel) html += `<div class="tl-llm-badge">${node.llmModel}</div>`;
        html += `<div class="tl-node-label" style="color: ${color}">${node.label}</div>`;
        html += `<div class="tl-node-dot" style="border-color: ${color}; ${filled ? 'background: ' + color : ''}"></div>`;
        html += `<div class="tl-node-time">${node.time}</div></div>`;

        if (i < mainNodes.length - 1) {
            const nextNode = mainNodes[i + 1];
            const widthMul = nextNode.dur ? parseFloat(nextNode.dur) : 0.5;
            const w = Math.max(50, widthMul * 28 + 50);
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
                    const bColor = typeColor[bn.type];
                    const bIdx = nodes.indexOf(bn);
                    const bFilled = bn.type === 'error';
                    html += `<div class="tl-node" data-idx="${bIdx}" onclick="pinNode(${bIdx})" style="pointer-events: auto;">`;
                    html += `<div class="tl-node-dot" style="border-color: ${bColor}; width: 10px; height: 10px; ${bFilled ? 'background: ' + bColor : ''}"></div>`;
                    html += `<div style="position: absolute; bottom: -16px; font-family: var(--font-mono); font-size: 8px; color: var(--text-muted); white-space: nowrap;">${bn.label}</div></div>`;
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

    const color = typeColor[node.type];
    document.getElementById('pinnedTitle').innerHTML = `<span style="color: ${color}">●</span> ${node.label} <span style="color: var(--text-muted); font-weight: 400; font-size: 10px; margin-left: 8px">${node.time}</span>`;

    let bodyHtml = '<div class="detail-col">';
    Object.entries(node.detail).forEach(([k, v]) => { bodyHtml += `<div class="detail-row"><span class="detail-key">${k}</span><span class="detail-val">${v}</span></div>`; });
    bodyHtml += '</div>';
    if (node.dur && node.dur !== '—') bodyHtml += `<div class="detail-col"><div class="detail-row"><span class="detail-key">duration</span><span class="detail-val">${node.dur}</span></div></div>`;
    if (node.tags && node.tags.length) bodyHtml += '<div class="detail-col" style="flex-basis: 100%;"><div style="margin-top: 2px;">' + node.tags.map(t => `<span class="detail-payload-tag">${t}</span>`).join('') + '</div></div>';

    document.getElementById('pinnedBody').innerHTML = bodyHtml;
    document.getElementById('pinnedDetail').classList.add('visible');
}

function unpinDetail() {
    pinnedNode = null;
    document.querySelectorAll('.tl-node').forEach(el => el.classList.remove('pinned'));
    document.getElementById('pinnedDetail').classList.remove('visible');
}

// ═══════════════════════════════════════════
//  RENDERING — TASKS TABLE
// ═══════════════════════════════════════════

function renderTasks() {
    const body = document.getElementById('tasksBody');
    let tasks = TASKS;
    if (selectedAgent) tasks = tasks.filter(t => t.agent === selectedAgent);

    if (tasks.length === 0) { body.innerHTML = `<tr><td colspan="8"><div class="empty-state"><span class="empty-state-icon">📋</span>No tasks for this agent</div></td></tr>`; return; }

    body.innerHTML = tasks.map(t => `
        <tr class="${t.id === selectedTask ? 'selected-row' : ''}" onclick="selectTask('${t.id}')">
          <td><span class="clickable-entity" style="color: var(--accent); font-weight: 500;">${t.id}</span></td>
          <td><span class="clickable-entity" onclick="event.stopPropagation(); selectAgent('${t.agent}')">${t.agent}</span></td>
          <td>${t.type}</td>
          <td><span class="task-status-dot" style="background: ${statusColor[t.status] || 'var(--idle)'}"></span>${t.status}</td>
          <td class="task-duration">${t.duration}</td>
          <td>${t.llmCalls > 0 ? `<span class="task-llm-badge">◆ ${t.llmCalls}</span>` : '—'}</td>
          <td>${t.cost}</td>
          <td style="color: var(--text-muted)">${t.time}</td>
        </tr>
      `).join('');
}

// ═══════════════════════════════════════════
//  RENDERING — STREAM (v3: kind-aware)
// ═══════════════════════════════════════════

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
        const sevColor = SEVERITY_COLOR[e.severity];
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
              <span class="clickable-entity" onclick="selectAgent('${e.agent}')">${e.agent}</span>${e.task ? ` › <span class="clickable-entity" onclick="selectTask('${e.task}')">${e.task}</span>` : ''}
            </div>
            ${e.summary}
          </div>
        </div>`;
    }).join('');
}

// ═══════════════════════════════════════════
//  RENDERING — AGENT DETAIL (v3)
// ═══════════════════════════════════════════

function renderAgentDetail() {
    if (!agentDetailAgent) return;
    const agent = AGENTS.find(a => a.id === agentDetailAgent);
    if (!agent) return;

    document.getElementById('detailAgentName').textContent = agent.id;
    document.getElementById('detailAgentBadge').innerHTML = `<div class="agent-status-badge ${statusBadge[agent.status]}">${statusLabel[agent.status]}</div>`;

    // Tasks tab
    const agentTasks = TASKS.filter(t => t.agent === agent.id);
    let tasksHtml = `<table class="pipeline-table"><thead><tr><th>Task ID</th><th>Type</th><th>Status</th><th>Duration</th><th>LLM</th><th>Cost</th><th>Time</th></tr></thead><tbody>`;
    agentTasks.forEach(t => {
        tasksHtml += `<tr onclick="selectTask('${t.id}')" style="cursor:pointer;">
          <td style="color: var(--accent);">${t.id}</td><td>${t.type}</td>
          <td><span class="task-status-dot" style="background: ${statusColor[t.status] || 'var(--idle)'}"></span>${t.status}</td>
          <td>${t.duration}</td><td>${t.llmCalls > 0 ? `<span class="task-llm-badge">◆ ${t.llmCalls}</span>` : '—'}</td>
          <td>${t.cost}</td><td style="color: var(--text-muted)">${t.time}</td></tr>`;
    });
    tasksHtml += '</tbody></table>';
    document.getElementById('detailTabTasks').innerHTML = tasksHtml;

    // Pipeline tab
    const pl = PIPELINE[agent.id] || { queue: [], todos: [], scheduled: [], issues: [] };
    let pHtml = '';

    // Issues
    if (pl.issues.length > 0) {
        pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Active Issues</div><div class="pipeline-badge" style="color: var(--error);">${pl.issues.length}</div></div>`;
        pHtml += `<table class="pipeline-table"><thead><tr><th>Issue</th><th>Severity</th><th>Category</th><th>Occurrences</th></tr></thead><tbody>`;
        pl.issues.forEach(iss => { pHtml += `<tr><td>${iss.summary}</td><td><span class="severity-badge severity-${iss.severity}">${iss.severity}</span></td><td>${iss.category}</td><td>×${iss.occurrences}</td></tr>`; });
        pHtml += `</tbody></table></div>`;
    }

    // Queue
    pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Queue</div><div class="pipeline-badge">${pl.queue.length} items</div></div>`;
    if (pl.queue.length > 0) {
        pHtml += `<table class="pipeline-table"><thead><tr><th>ID</th><th>Priority</th><th>Source</th><th>Summary</th><th>Age</th></tr></thead><tbody>`;
        pl.queue.forEach(q => { pHtml += `<tr><td style="color: var(--text-muted)">${q.id}</td><td><span class="priority-badge priority-${q.priority}">${q.priority}</span></td><td>${q.source}</td><td>${q.summary}</td><td>${q.age}</td></tr>`; });
        pHtml += `</tbody></table>`;
    } else { pHtml += `<div class="pipeline-empty">Queue is empty — agent is caught up</div>`; }
    pHtml += `</div>`;

    // TODOs
    if (pl.todos.length > 0) {
        pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Active TODOs</div><div class="pipeline-badge">${pl.todos.length}</div></div>`;
        pHtml += `<table class="pipeline-table"><thead><tr><th>TODO</th><th>Priority</th><th>Source</th></tr></thead><tbody>`;
        pl.todos.forEach(td => { pHtml += `<tr><td>${td.summary}</td><td><span class="priority-badge priority-${td.priority}">${td.priority}</span></td><td>${td.source}</td></tr>`; });
        pHtml += `</tbody></table></div>`;
    }

    // Scheduled
    if (pl.scheduled.length > 0) {
        pHtml += `<div class="pipeline-section"><div class="pipeline-section-header"><div class="pipeline-section-title">Scheduled</div><div class="pipeline-badge">${pl.scheduled.length}</div></div>`;
        pHtml += `<table class="pipeline-table"><thead><tr><th>Name</th><th>Next Run</th><th>Interval</th><th>Status</th></tr></thead><tbody>`;
        pl.scheduled.forEach(s => {
            const stColor = s.status === 'ok' ? 'var(--success)' : 'var(--warning)';
            pHtml += `<tr><td>${s.name}</td><td>${s.next}</td><td>${s.interval}</td><td style="color: ${stColor}">${s.status}</td></tr>`;
        });
        pHtml += `</tbody></table></div>`;
    }

    if (!pHtml) pHtml = '<div class="pipeline-empty">No pipeline data for this agent</div>';
    document.getElementById('detailTabPipeline').innerHTML = pHtml;
}

// ═══════════════════════════════════════════
//  RENDERING — COST EXPLORER (v3)
// ═══════════════════════════════════════════

function renderCostExplorer() {
    const d = COST_DATA;
    document.getElementById('costRibbon').innerHTML = `
        <div class="cost-stat"><div class="stat-label">Total Cost (${d.total.period})</div><div class="stat-value purple">$${d.total.cost.toFixed(2)}</div></div>
        <div class="cost-stat"><div class="stat-label">LLM Calls</div><div class="stat-value">${d.total.calls}</div></div>
        <div class="cost-stat"><div class="stat-label">Tokens In</div><div class="stat-value">${fmtTokens(d.total.tokens_in)}</div></div>
        <div class="cost-stat"><div class="stat-label">Tokens Out</div><div class="stat-value">${fmtTokens(d.total.tokens_out)}</div></div>
        <div class="cost-stat"><div class="stat-label">Avg Cost/Call</div><div class="stat-value">$${(d.total.cost / d.total.calls).toFixed(3)}</div></div>
      `;

    const maxAgentCost = Math.max(...d.byAgent.map(a => a.cost));
    const maxModelCost = Math.max(...d.byModel.map(m => m.cost));

    let html = '';

    // By Model
    html += `<div><div class="cost-section-title">Cost by Model</div><table class="cost-table"><thead><tr><th>Model</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost</th><th></th></tr></thead><tbody>`;
    d.byModel.forEach(m => {
        html += `<tr><td><span class="model-badge">${m.model}</span></td><td>${m.calls}</td><td>${fmtTokens(m.tokens_in)}</td><td>${fmtTokens(m.tokens_out)}</td><td style="color: var(--llm); font-weight: 600;">$${m.cost.toFixed(2)}</td><td style="width: 100px;"><div class="cost-bar" style="width: ${(m.cost / maxModelCost) * 100}%"></div></td></tr>`;
    });
    html += `</tbody></table></div>`;

    // By Agent
    html += `<div><div class="cost-section-title">Cost by Agent</div><table class="cost-table"><thead><tr><th>Agent</th><th>Calls</th><th>Tokens In</th><th>Tokens Out</th><th>Cost</th><th></th></tr></thead><tbody>`;
    d.byAgent.sort((a, b) => b.cost - a.cost).forEach(a => {
        html += `<tr><td><span class="clickable-entity" onclick="openAgentDetail('${a.agent}')" style="color: var(--accent);">${a.agent}</span></td><td>${a.calls}</td><td>${fmtTokens(a.tokens_in)}</td><td>${fmtTokens(a.tokens_out)}</td><td style="color: var(--llm); font-weight: 600;">$${a.cost.toFixed(2)}</td><td style="width: 100px;"><div class="cost-bar" style="width: ${(a.cost / maxAgentCost) * 100}%"></div></td></tr>`;
    });
    html += `</tbody></table></div>`;

    document.getElementById('costTables').innerHTML = html;
}

// ═══════════════════════════════════════════
//  NAVIGATION
// ═══════════════════════════════════════════

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
        renderCostExplorer();
    } else if (view === 'agentDetail') {
        document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
        document.getElementById('viewAgentDetail').classList.add('active');
        renderAgentDetail();
    }
    renderHive();
}

function openAgentDetail(agentId) {
    agentDetailAgent = agentId;
    selectedAgent = agentId;
    activeDetailTab = 'tasks';
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

// ═══════════════════════════════════════════
//  INTERACTIONS
// ═══════════════════════════════════════════

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
        if (agent && agent.task) { selectedTask = agent.task; updateTimelineHeader(); renderTimeline(); }
    }
    updateFilterBar();
    renderHive();
    renderSummary();
    renderTasks();
    renderStream();
}

function selectTask(taskId) {
    if (currentView === 'agentDetail') switchView('mission');
    selectedTask = taskId;
    updateTimelineHeader();
    renderTimeline();
    renderTasks();
}

function updateTimelineHeader() {
    const task = TASKS.find(t => t.id === selectedTask);
    if (task) {
        document.getElementById('tlTaskId').textContent = selectedTask;
        const statusChar = task.status === 'completed' ? '✓' : task.status === 'failed' ? '✗' : task.status === 'stuck' ? '⚠' : '◉';
        const statusClr = statusColor[task.status] || 'var(--text-muted)';
        document.getElementById('tlMeta').innerHTML = `
          <span>⏱ ${task.duration}</span>
          <span class="clickable-entity" onclick="selectAgent('${task.agent}')">🤖 ${task.agent}</span>
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

// ═══════════════════════════════════════════
//  SIMULATED LIVE UPDATES
// ═══════════════════════════════════════════

const NEW_EVENTS_POOL = [
    { type: 'heartbeat', agent: 'lead-qualifier', task: null, summary: 'Idle, last task 4s ago · Q:3', severity: 'debug' },
    { type: 'heartbeat', agent: 'doc-processor', task: null, summary: 'Extracting tables · Q:1', severity: 'debug' },
    { type: 'custom', kind: 'llm_call', agent: 'doc-processor', task: 'task_doc-2288', summary: 'extract_text: sonnet-4 (2.1K→890) $0.03', severity: 'info' },
    { type: 'action_completed', agent: 'doc-processor', task: 'task_doc-2288', summary: 'parse_pdf completed in 4.2s', severity: 'info' },
    { type: 'task_started', agent: 'lead-qualifier', task: 'task_lead-4822', summary: 'New lead processing task', severity: 'info' },
    { type: 'custom', kind: 'queue_snapshot', agent: 'lead-qualifier', task: null, summary: 'Queue: 2 items, oldest 8s', severity: 'debug' },
    { type: 'heartbeat', agent: 'email-responder', task: null, summary: 'Awaiting approval · Q:2', severity: 'debug' },
    { type: 'custom', kind: 'llm_call', agent: 'lead-qualifier', task: 'task_lead-4822', summary: 'score_lead: sonnet-4 (1.1K→580) $0.03', severity: 'info' },
    { type: 'custom', kind: 'todo', agent: 'support-triage', task: null, summary: '☐ Update KB with billing refund policy', severity: 'info' },
    { type: 'task_completed', agent: 'data-enricher', task: 'task_enrich-301', summary: 'Company enrichment complete', severity: 'info' },
];

let eventIndex = 0;
function simulateLiveEvent() {
    const e = NEW_EVENTS_POOL[eventIndex % NEW_EVENTS_POOL.length];
    eventIndex++;
    STREAM_EVENTS.unshift({ ...e, time: 'just now' });
    STREAM_EVENTS.forEach((ev, i) => { if (i > 0) { const age = i * 4; ev.time = age < 60 ? age + 's ago' : Math.floor(age / 60) + 'm ago'; } });
    if (STREAM_EVENTS.length > 30) STREAM_EVENTS.pop();
    renderStream();

    AGENTS.forEach(a => { if (a.status !== 'stuck') a.hb += 3; });
    const healthy = AGENTS.filter(a => a.status !== 'stuck' && a.status !== 'error');
    if (healthy.length > 0) healthy[Math.floor(Math.random() * healthy.length)].hb = Math.floor(Math.random() * 10);
    renderHive();
}

setInterval(simulateLiveEvent, 3500);

// ═══════════════════════════════════════════
//  INIT
// ═══════════════════════════════════════════

renderHive();
renderSummary();
renderMetrics();
renderTimeline();
renderTasks();
renderStreamFilters();
renderStream();
updateFilterBar();
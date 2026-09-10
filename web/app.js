// Personal Assistant Web UI

// Derive from the page's own origin instead of hardcoding the gateway port,
// so this works whether the gateway is served on 18789 (dev default) or
// something else.
const API_BASE = window.location.origin;
const WS_BASE = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
const PLATFORM = 'web';
const USER_ID = 'browser';
const KIT_AGENT_ID = 'kit';
const KIT_AVATAR_COLOR = '#ee0000';
const AVATAR_PALETTE = ['#ca6c0f', '#37a3a3', '#5e40be', '#63993d', '#a60000', '#147878', '#b98412', '#876fd4'];

// Which team member the chat panel is currently talking to. Mirrors the
// server's make_session_id: Kit keeps the original {platform}:{user_id}
// session id, any other agent gets its own {platform}:{user_id}:{agent_id}
// thread, so switching this switches to a separately-remembered conversation.
let currentAgentId = KIT_AGENT_ID;
let currentMode = 'dm'; // 'dm' | 'broadcast'

function currentSessionId(agentId = currentAgentId) {
    if (currentMode === 'broadcast') return `broadcast:${PLATFORM}:${USER_ID}`;
    return agentId === KIT_AGENT_ID ? `${PLATFORM}:${USER_ID}` : `${PLATFORM}:${USER_ID}:${agentId}`;
}

function broadcastSessionId() {
    return `broadcast:${PLATFORM}:${USER_ID}`;
}

let messageCount = 0;
let ws = null;
let wsReconnectAttempts = 0;
// Default; overwritten from GET /config on init() so it can be tuned
// server-side (WS_MAX_RECONNECT_ATTEMPTS env var) without editing this file.
let wsMaxReconnectAttempts = 5;

// GATEWAY_TOKEN support. /config (unauthenticated, so it's reachable before
// we know whether a token is even needed) tells us via auth_required
// whether the server expects one; if so and we don't have one cached yet,
// init() prompts for it once. Stored in localStorage so it survives reloads
// (per-browser only — never sent anywhere but this gateway's own origin).
let authToken = null;
try {
    authToken = localStorage.getItem('kit_gateway_token');
} catch (error) {
    // localStorage unavailable (private mode, blocked) - just won't persist.
}

async function apiFetch(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 && authToken) {
        // Stored token is stale/wrong - drop it so a refresh re-prompts
        // instead of failing silently on every request forever.
        authToken = null;
        try { localStorage.removeItem('kit_gateway_token'); } catch (error) {
            // ignore
        }
    }
    return response;
}

// Streaming state
let streamingMessageDiv = null;
let streamingContentDiv = null;
let streamingText = '';

// DOM Elements
const chatMessages = document.getElementById('chat-messages');
const chatScrollContainer = chatMessages;
const chatInput = document.getElementById('chat-input');
const sendButton = document.getElementById('send-button');
const clearChatButton = document.getElementById('clear-chat-button');
const connectionStatus = document.getElementById('connection-status');
const messageCountSpan = document.getElementById('message-count');
const refreshSchedulesBtn = document.getElementById('refresh-schedules');
const schedulesList = document.getElementById('schedules-list');
const sessionsList = document.getElementById('sessions-list');
const refreshActivityBtn = document.getElementById('refresh-activity');
const activityList = document.getElementById('activity-list');

const chatAvatar = document.getElementById('chat-avatar');
const chatHeaderName = document.getElementById('chat-header-name');
const chatHeaderStatusText = document.getElementById('chat-header-status-text');
const chatTargetStatusDot = document.getElementById('chat-target-status-dot');

const memberSidebar = document.getElementById('member-sidebar');
const memberList = document.getElementById('member-list');
const addTeammateBtn = document.getElementById('add-teammate-btn');
const sidebarScrim = document.getElementById('sidebar-scrim');
const mobileMenuBtn = document.getElementById('mobile-menu-btn');

const detailsPanelEl = document.getElementById('details-panel');
const detailsTitle = document.getElementById('details-panel-title');
const detailsBody = document.getElementById('details-panel-body');
const toggleDetailsBtn = document.getElementById('toggle-details-btn');
const closeDetailsBtn = document.getElementById('close-details-btn');

const workspaceOverlay = document.getElementById('workspace-overlay');
const closeWorkspaceBtn = document.getElementById('close-workspace-btn');

// Latest known status per agent_id ({status, current_task, updated_at}),
// seeded from GET /agents/status and kept live via `agent_status` WS events.
let agentStatuses = {};

// Full roster, refreshed via loadAgents() - powers the member sidebar and
// the details panel.
let agentsCache = [];
let agentsById = {};

// Details panel: 'member' shows a team member's info, 'edit' shows the
// edit-in-place form for a non-Kit agent, 'new' shows the add-teammate form.
let detailsMode = 'member';
let detailsAgentId = null;

let currentWorkspaceTab = null;

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, '&quot;');
}

function initialsFor(name) {
    if (!name) return '?';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
}

function colorForId(id) {
    let hash = 0;
    for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
    return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

function avatarColorFor(agentId) {
    return agentId === KIT_AGENT_ID ? KIT_AVATAR_COLOR : colorForId(agentId);
}

function statusClassFor(agentId) {
    const status = agentStatuses[agentId];
    if (!status) return '';
    return status.status === 'busy' ? 'busy' : 'idle';
}

function statusTextFor(agentId) {
    const status = agentStatuses[agentId];
    if (!status) return 'Idle';
    if (status.status === 'busy') {
        return status.current_task ? `Busy — ${status.current_task}` : 'Busy';
    }
    return 'Available';
}

function roleLabelFor(agentId) {
    return agentId === KIT_AGENT_ID ? 'Manager' : '';
}

// Info/details panel visibility persists per-browser via localStorage.
const DETAILS_HIDDEN_KEY = 'kit_details_panel_hidden';

function showDetailsPanel() {
    detailsPanelEl.hidden = false;
    toggleDetailsBtn.classList.add('active');
    toggleDetailsBtn.setAttribute('aria-expanded', 'true');
    try { localStorage.setItem(DETAILS_HIDDEN_KEY, '0'); } catch (error) {
        // ignore
    }
}

function hideDetailsPanel() {
    detailsPanelEl.hidden = true;
    toggleDetailsBtn.classList.remove('active');
    toggleDetailsBtn.setAttribute('aria-expanded', 'false');
    try { localStorage.setItem(DETAILS_HIDDEN_KEY, '1'); } catch (error) {
        // ignore
    }
}

let detailsHidden = false;
try {
    detailsHidden = localStorage.getItem(DETAILS_HIDDEN_KEY) === '1';
} catch (error) {
    // ignore
}
// On narrow screens the details panel becomes a full-screen overlay (it
// would otherwise hide the chat, including the hamburger button used to
// get back to it) - always start closed there regardless of the persisted
// desktop preference.
if (window.matchMedia && window.matchMedia('(max-width: 760px)').matches) {
    detailsHidden = true;
}
if (detailsHidden) hideDetailsPanel(); else showDetailsPanel();

toggleDetailsBtn.addEventListener('click', () => {
    if (detailsPanelEl.hidden) {
        showDetailsPanel();
        if (currentMode === 'broadcast') {
            renderBroadcastDetails();
        } else {
            renderMemberDetails(currentAgentId);
        }
    } else {
        hideDetailsPanel();
    }
});
closeDetailsBtn.addEventListener('click', hideDetailsPanel);

// Mobile: the member sidebar becomes a slide-in drawer.
function openMobileSidebar() {
    memberSidebar.classList.add('open');
    sidebarScrim.classList.add('visible');
}

function closeMobileSidebar() {
    memberSidebar.classList.remove('open');
    sidebarScrim.classList.remove('visible');
}

mobileMenuBtn.addEventListener('click', () => {
    if (memberSidebar.classList.contains('open')) closeMobileSidebar();
    else openMobileSidebar();
});
sidebarScrim.addEventListener('click', closeMobileSidebar);

// Check connection
async function checkConnection() {
    try {
        const response = await apiFetch(`${API_BASE}/health`);
        if (response.ok) {
            connectionStatus.classList.add('connected');
            connectionStatus.title = 'Connected';
            return true;
        }
    } catch (error) {
        // fall through
    }
    connectionStatus.classList.remove('connected');
    connectionStatus.title = 'Disconnected';
    return false;
}

function scrollChatToBottom() {
    chatScrollContainer.scrollTop = chatScrollContainer.scrollHeight;
}

// Add message to chat. `sender`, when set, means this message was placed
// into the thread by another agent (delegation) rather than typed by the
// human - rendered with a distinct label/border instead of implying the
// human wrote it.
function addMessage(content, role = 'user', id = null, sender = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}${sender ? ' delegated' : ''}`;
    if (id) messageDiv.id = id;

    if (role === 'system') {
        messageDiv.textContent = content;
        chatMessages.appendChild(messageDiv);
        scrollChatToBottom();
        return messageDiv;
    }

    const avatar = document.createElement('div');
    avatar.className = 'avatar';

    const body = document.createElement('div');
    body.className = 'message-body';

    if (sender) {
        const senderDiv = document.createElement('div');
        senderDiv.className = 'message-sender-label';
        senderDiv.textContent = `🤖 ${sender} asked:`;
        body.appendChild(senderDiv);
    }

    const headerLine = document.createElement('div');
    headerLine.className = 'message-header-line';
    const authorSpan = document.createElement('span');
    authorSpan.className = 'message-author';
    const timeSpan = document.createElement('span');
    timeSpan.className = 'message-time';
    timeSpan.textContent = new Date().toLocaleTimeString();

    if (role === 'user') {
        avatar.textContent = 'Y';
        authorSpan.textContent = 'You';
    } else {
        const agent = agentsById[currentAgentId];
        const name = agent ? agent.name : (currentAgentId === KIT_AGENT_ID ? 'Kit' : currentAgentId);
        avatar.textContent = initialsFor(name);
        avatar.style.background = avatarColorFor(currentAgentId);
        authorSpan.textContent = name;
    }

    headerLine.appendChild(authorSpan);
    headerLine.appendChild(timeSpan);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = content;

    body.appendChild(headerLine);
    body.appendChild(contentDiv);

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(body);

    chatMessages.appendChild(messageDiv);
    scrollChatToBottom();

    return messageDiv;
}

// Add thinking indicator
function addThinkingIndicator() {
    const agent = agentsById[currentAgentId];
    const name = agent ? agent.name : (currentAgentId === KIT_AGENT_ID ? 'Kit' : currentAgentId);
    const thinkingDiv = document.createElement('div');
    thinkingDiv.id = 'thinking-indicator';
    thinkingDiv.className = 'message assistant';
    thinkingDiv.innerHTML = `
        <div class="avatar" style="background:${avatarColorFor(currentAgentId)}">${initialsFor(name)}</div>
        <div class="message-body">
            <div class="message-header-line"><span class="message-author">${escapeHtml(name)}</span></div>
            <div class="message-content"><div class="spinner" style="margin:2px 0 0;width:16px;height:16px;border-width:2px;"></div></div>
        </div>
    `;
    chatMessages.appendChild(thinkingDiv);
    scrollChatToBottom();
}

// Remove thinking indicator
function removeThinkingIndicator() {
    const indicator = document.getElementById('thinking-indicator');
    if (indicator) {
        indicator.remove();
    }
}

// Send message via WebSocket for streaming, with POST fallback
async function sendMessage() {
    const message = chatInput.value.trim();
    if (!message) return;

    chatInput.disabled = true;
    sendButton.disabled = true;

    addMessage(message, 'user');
    chatInput.value = '';
    chatInput.style.height = 'auto';

    if (currentMode === 'broadcast') {
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'broadcast',
                platform: PLATFORM,
                user_id: USER_ID,
                message: message,
            }));
        } else {
            try {
                await apiFetch(`${API_BASE}/broadcast`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ platform: PLATFORM, user_id: USER_ID, message: message }),
                });
            } catch (error) {
                addMessage(`Error: ${error.message}`, 'system');
            }
        }
        messageCount++;
        messageCountSpan.textContent = `${messageCount} messages`;
        chatInput.disabled = false;
        sendButton.disabled = false;
        chatInput.focus();
        return;
    }

    if (ws && ws.readyState === WebSocket.OPEN) {
        addThinkingIndicator();
        ws.send(JSON.stringify({
            type: 'chat_message',
            platform: PLATFORM,
            user_id: USER_ID,
            agent_id: currentAgentId,
            message: message,
        }));
        // Response handled by handleWebSocketMessage — input re-enabled on stream_end/stream_error
    } else {
        // Fallback to POST when WebSocket is not connected
        addThinkingIndicator();
        try {
            const response = await apiFetch(`${API_BASE}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ platform: PLATFORM, user_id: USER_ID, agent_id: currentAgentId, message: message }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            removeThinkingIndicator();
            addMessage(data.response, 'assistant');
            messageCount = data.message_count;
            messageCountSpan.textContent = `${messageCount} messages`;
        } catch (error) {
            removeThinkingIndicator();
            addMessage(`Error: ${error.message}`, 'system');
        } finally {
            chatInput.disabled = false;
            sendButton.disabled = false;
            chatInput.focus();
        }
    }
}

// Clear the current chat session (server history + in-memory agent state)
async function clearChat() {
    if (!confirm('Clear this chat session? This cannot be undone.')) return;

    const sessionId = currentSessionId();
    clearChatButton.disabled = true;
    try {
        const response = await apiFetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}`, {
            method: 'DELETE',
        });
        if (!response.ok && response.status !== 404) {
            throw new Error(`HTTP ${response.status}`);
        }
        chatMessages.innerHTML = '';
        messageCount = 0;
        messageCountSpan.textContent = '0 messages';
        addMessage('Chat session cleared', 'system');
        loadSessions();
    } catch (error) {
        addMessage(`Error clearing chat: ${error.message}`, 'system');
    } finally {
        clearChatButton.disabled = false;
    }
}

clearChatButton.addEventListener('click', clearChat);

// Reflect the currently selected team member in the chat header, from cache
// only (no network calls) - status text/dot, name, avatar.
function updateChatHeaderForCurrentAgent() {
    const agent = agentsById[currentAgentId] || {
        id: currentAgentId,
        name: currentAgentId === KIT_AGENT_ID ? 'Kit' : currentAgentId,
    };
    chatAvatar.textContent = initialsFor(agent.name);
    chatAvatar.style.background = avatarColorFor(currentAgentId);
    chatHeaderName.textContent = agent.name;
    chatHeaderName.title = currentSessionId();
    chatInput.placeholder = `Message ${agent.name}…`;

    chatTargetStatusDot.style.display = '';
    chatTargetStatusDot.classList.remove('idle', 'busy');
    const cls = statusClassFor(currentAgentId);
    if (cls) chatTargetStatusDot.classList.add(cls);
    chatHeaderStatusText.textContent = statusTextFor(currentAgentId);
}

// Switch who the chat panel is talking to (Kit or a specific agent) - moves
// to that agent's own persistent thread rather than continuing this one.
async function selectMember(agentId) {
    currentMode = 'dm';
    currentAgentId = agentId;
    updateChatHeaderForCurrentAgent();
    refreshMemberListVisualState();
    messageCount = 0;
    messageCountSpan.textContent = '0 messages';
    closeMobileSidebar();
    await loadChatHistory();
    if (!detailsPanelEl.hidden && detailsMode !== 'new') {
        renderMemberDetails(agentId);
    }
}

async function selectBroadcastChannel() {
    currentMode = 'broadcast';
    refreshMemberListVisualState();
    messageCount = 0;
    messageCountSpan.textContent = '0 messages';
    closeMobileSidebar();

    chatAvatar.textContent = '#';
    chatAvatar.style.background = '#5e40be';
    chatHeaderName.textContent = 'team';
    chatHeaderName.title = broadcastSessionId();
    chatInput.placeholder = 'Broadcast to team…';
    chatTargetStatusDot.classList.remove('idle', 'busy');
    chatTargetStatusDot.style.display = 'none';
    chatHeaderStatusText.textContent = '';

    await loadChatHistory();
    if (!detailsPanelEl.hidden && detailsMode !== 'new') {
        renderBroadcastDetails();
    }
}

function finishStreaming() {
    streamingMessageDiv = null;
    streamingContentDiv = null;
    streamingText = '';
    chatInput.disabled = false;
    sendButton.disabled = false;
    chatInput.focus();
}

// Event listeners
sendButton.addEventListener('click', sendMessage);

chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// Autogrow the composer textarea up to the CSS max-height.
chatInput.addEventListener('input', () => {
    chatInput.style.height = 'auto';
    chatInput.style.height = `${chatInput.scrollHeight}px`;
});

// --- Member sidebar: contacts-style roster of team members ---

function memberRowHtml(agent) {
    const bg = avatarColorFor(agent.id);
    const role = roleLabelFor(agent.id);
    return `
        <button class="member-row" data-agent-id="${escapeAttr(agent.id)}">
            <div class="avatar" style="background:${bg}">${initialsFor(agent.name)}</div>
            <div class="member-row-text">
                <div class="member-row-name">${escapeHtml(agent.name)}${role ? `<span class="member-row-role">${escapeHtml(role)}</span>` : ''}</div>
                <div class="member-row-task" data-task-for="${escapeAttr(agent.id)}"></div>
            </div>
            <div class="member-row-dot" data-status-dot="${escapeAttr(agent.id)}"></div>
        </button>
    `;
}

function renderMemberList() {
    if (!agentsCache.length) {
        memberList.innerHTML = '<div class="member-list-empty">No team members</div>';
        return;
    }
    const kit = agentsCache.find(a => a.id === KIT_AGENT_ID);
    const others = agentsCache.filter(a => a.id !== KIT_AGENT_ID).sort((a, b) => a.name.localeCompare(b.name));

    let html = '';

    html += '<div class="member-section-label">Channels</div>';
    html += `
        <button class="member-row channel-row" data-channel="broadcast">
            <div class="avatar channel-avatar">#</div>
            <div class="member-row-text">
                <div class="member-row-name">team</div>
                <div class="member-row-task"></div>
            </div>
        </button>
    `;

    html += '<div class="member-section-label">Direct Messages</div>';
    if (kit) html += memberRowHtml(kit);
    html += others.map(memberRowHtml).join('');

    memberList.innerHTML = html;

    memberList.querySelectorAll('.member-row:not(.channel-row)').forEach(row => {
        row.addEventListener('click', () => selectMember(row.dataset.agentId));
    });
    memberList.querySelectorAll('.channel-row').forEach(row => {
        row.addEventListener('click', () => selectBroadcastChannel());
    });

    refreshMemberListVisualState();
}

function refreshMemberListVisualState() {
    memberList.querySelectorAll('.member-row').forEach(row => {
        if (row.classList.contains('channel-row')) {
            row.classList.toggle('active', currentMode === 'broadcast');
            return;
        }
        const id = row.dataset.agentId;
        row.classList.toggle('active', currentMode === 'dm' && id === currentAgentId);
        const dot = row.querySelector('[data-status-dot]');
        if (dot) {
            dot.classList.remove('idle', 'busy');
            const cls = statusClassFor(id);
            if (cls) dot.classList.add(cls);
        }
        const taskEl = row.querySelector('[data-task-for]');
        if (taskEl) {
            const status = agentStatuses[id];
            taskEl.textContent = status && status.current_task ? status.current_task : '';
        }
    });
}

async function loadAgents() {
    try {
        const [agentsRes, statusRes] = await Promise.all([
            apiFetch(`${API_BASE}/agents`),
            apiFetch(`${API_BASE}/agents/status`),
        ]);
        if (!agentsRes.ok) throw new Error('Failed to load agents');
        const agents = await agentsRes.json();
        agentsCache = agents;
        agentsById = {};
        agents.forEach(a => { agentsById[a.id] = a; });
        if (statusRes.ok) {
            agentStatuses = await statusRes.json();
        }
        renderMemberList();
        updateChatHeaderForCurrentAgent();
        if (!detailsPanelEl.hidden && detailsMode === 'member') {
            renderMemberDetails(detailsAgentId || currentAgentId);
        }
    } catch (error) {
        memberList.innerHTML = `<div class="member-list-empty">Error loading team: ${escapeHtml(error.message)}</div>`;
    }
}

// --- Details panel: info for whichever team member is currently open ---

function formatToolOrSkillList(list) {
    if (list === '*') return 'all';
    if (!Array.isArray(list) || list.length === 0) return 'none';
    return list.join(', ');
}

// Same data, formatted for an editable text input rather than display prose.
function toolOrSkillListToInputValue(list) {
    if (list === '*') return '*';
    if (!Array.isArray(list)) return '';
    return list.join(', ');
}

// Parse the edit form's comma-separated tools/skills input back into what
// the API expects: the literal string "*" for full access, or a list.
function parseToolOrSkillInput(value) {
    const trimmed = value.trim();
    if (trimmed === '*') return '*';
    return trimmed.split(',').map(s => s.trim()).filter(Boolean);
}

function updateDetailsStatusLine(agentId) {
    if (detailsMode !== 'member' || detailsAgentId !== agentId) return;
    const line = detailsBody.querySelector('.details-status-line');
    if (!line) return;
    const cls = statusClassFor(agentId);
    line.innerHTML = `<span class="status-dot ${cls}"></span> ${escapeHtml(statusTextFor(agentId))}`;
}

function formatActivityMini(entry) {
    const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '';
    let label;
    if (entry.event_type === 'agent_status') {
        label = `Now ${entry.status}${entry.current_task ? ' — ' + entry.current_task : ''}`;
    } else {
        const detail = entry.detail || {};
        label = entry.event_type === 'tool_call_start'
            ? `Called ${detail.tool_name}`
            : `Finished ${detail.tool_name}`;
    }
    return `<div class="activity-mini-item"><span class="activity-mini-time">${escapeHtml(time)}</span>${escapeHtml(label)}</div>`;
}

async function loadMiniActivity(agentId) {
    const el = document.getElementById('details-activity-mini');
    if (!el) return;
    try {
        const response = await apiFetch(`${API_BASE}/agents/activity?agent_id=${encodeURIComponent(agentId)}&limit=8`);
        if (!response.ok) throw new Error('failed');
        const entries = await response.json();
        const target = document.getElementById('details-activity-mini');
        if (!target) return; // panel may have moved on since the fetch started
        target.innerHTML = entries.length === 0
            ? '<div class="empty-state" style="padding:10px 0;">No recent activity</div>'
            : entries.slice().reverse().map(formatActivityMini).join('');
    } catch (error) {
        const target = document.getElementById('details-activity-mini');
        if (target) target.innerHTML = '<div class="empty-state" style="padding:10px 0;">Couldn\'t load activity</div>';
    }
}

async function loadPersonaInto(textareaEl) {
    if (!textareaEl) return;
    textareaEl.disabled = true;
    try {
        const response = await apiFetch(`${API_BASE}/persona`);
        if (!response.ok) throw new Error('Failed to load persona');
        const data = await response.json();
        textareaEl.value = data.content;
    } catch (error) {
        textareaEl.placeholder = `Error: ${error.message}`;
    } finally {
        textareaEl.disabled = false;
    }
}

async function savePersonaFrom(textareaEl, statusEl, btnEl) {
    if (btnEl) btnEl.disabled = true;
    if (statusEl) statusEl.textContent = '';
    try {
        const response = await apiFetch(`${API_BASE}/persona`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: textareaEl.value }),
        });
        if (!response.ok) throw new Error('Failed to save');
        if (statusEl) {
            statusEl.textContent = 'Saved';
            statusEl.className = 'form-status success';
            setTimeout(() => { statusEl.textContent = ''; }, 3000);
        }
    } catch (error) {
        if (statusEl) {
            statusEl.textContent = `Error: ${error.message}`;
            statusEl.className = 'form-status error';
        }
    } finally {
        if (btnEl) btnEl.disabled = false;
    }
}

function renderBroadcastDetails() {
    detailsMode = 'member';
    detailsTitle.textContent = 'Details';

    const members = Object.values(agentsById);

    let html = `
        <div class="details-avatar-row">
            <div class="avatar" style="width:48px;height:48px;font-size:18px;border-radius:12px;background:#5e40be">#</div>
            <div>
                <div class="details-name">team</div>
                <div class="details-status-line">${members.length} member${members.length !== 1 ? 's' : ''}</div>
            </div>
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">About</div>
            <div class="details-description">Broadcast channel for messaging all team members at once.</div>
        </div>
    `;

    html += `<div class="details-section"><div class="details-section-label">Members</div>`;
    members.forEach(agent => {
        const bg = avatarColorFor(agent.id);
        const statusCls = statusClassFor(agent.id);
        const statusTxt = statusTextFor(agent.id);
        html += `
            <div class="details-meta-row" style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <div class="avatar" style="width:28px;height:28px;font-size:11px;background:${bg}">${initialsFor(agent.name)}</div>
                <span>${escapeHtml(agent.name)}</span>
                <span class="status-dot ${statusCls}" style="margin-left:auto;"></span>
                <span style="font-size:12px;color:var(--pf-v5-global--Color--200)">${escapeHtml(statusTxt)}</span>
            </div>
        `;
    });
    html += `</div>`;

    detailsBody.innerHTML = html;
}

async function renderMemberDetails(agentId) {
    detailsMode = 'member';
    detailsAgentId = agentId;
    detailsTitle.textContent = 'Details';

    const agent = agentsById[agentId];
    if (!agent) {
        detailsBody.innerHTML = '<div class="spinner"></div>';
        return;
    }

    const isKit = agentId === KIT_AGENT_ID;
    const bg = avatarColorFor(agentId);

    const role = roleLabelFor(agentId);
    let html = `
        <div class="details-avatar-row">
            <div class="avatar" style="width:48px;height:48px;font-size:18px;border-radius:12px;background:${bg}">${initialsFor(agent.name)}</div>
            <div>
                <div class="details-name">${escapeHtml(agent.name)}${role ? `<span class="details-role">${escapeHtml(role)}</span>` : ''}</div>
                <div class="details-status-line">
                    <span class="status-dot ${statusClassFor(agentId)}"></span>
                    ${escapeHtml(statusTextFor(agentId))}
                </div>
            </div>
        </div>
    `;

    if (agent.description) {
        html += `
            <div class="details-section">
                <div class="details-section-label">About</div>
                <div class="details-description">${escapeHtml(agent.description)}</div>
            </div>
        `;
    }

    html += `
        <div class="details-section">
            <div class="details-section-label">Memory</div>
            <div class="composer-row">
                <input id="details-memory-search-input" type="text" placeholder="Search memories...">
                <button id="details-memory-search-btn" class="btn btn-primary">Search</button>
            </div>
            <div id="details-memory-results"></div>
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">Tools</div>
            <div id="details-tools-list" class="tools-list"><div class="spinner" style="margin:12px auto;"></div></div>
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">Skills</div>
            <div id="details-skills-list"><div class="spinner" style="margin:12px auto;"></div></div>
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">Model</div>
            <p class="details-meta-row"><strong>Model:</strong> ${escapeHtml(agent.model || '(default)')}</p>
            <p class="details-meta-row"><strong>Provider:</strong> ${escapeHtml(agent.provider || '(default)')}</p>
        </div>
    `;

    html += `
        <div class="details-actions">
            ${!isKit ? '<button class="btn btn-secondary" id="details-edit-btn">Edit</button>' : ''}
            ${!isKit ? '<button class="btn btn-danger" id="details-delete-btn">Delete</button>' : ''}
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">Recent activity</div>
            <div id="details-activity-mini" class="activity-mini-list"><div class="spinner" style="margin:12px auto;"></div></div>
        </div>
    `;

    if (isKit) {
        html += `
            <div class="details-section">
                <div class="details-section-label">Persona (SOUL.md)</div>
                <p class="tab-hint">Defines Kit's personality and behavior. Changes take effect on the next message.</p>
                <textarea class="form-control" id="kit-persona-editor" rows="12" placeholder="Loading..."></textarea>
                <div class="details-actions" style="margin-top:8px;">
                    <button class="btn btn-primary" id="kit-save-persona">Save persona</button>
                    <span class="form-status" id="kit-persona-status"></span>
                </div>
            </div>
        `;
    }

    detailsBody.innerHTML = html;

    if (!isKit) {
        document.getElementById('details-edit-btn').addEventListener('click', () => renderMemberEditForm(agentId));
        document.getElementById('details-delete-btn').addEventListener('click', () => deleteAgentFromDetails(agentId));
    } else {
        const personaEditor = document.getElementById('kit-persona-editor');
        const personaStatus = document.getElementById('kit-persona-status');
        const savePersonaBtn = document.getElementById('kit-save-persona');
        loadPersonaInto(personaEditor);
        savePersonaBtn.addEventListener('click', () => savePersonaFrom(personaEditor, personaStatus, savePersonaBtn));
    }

    const detailsMemoryBtn = document.getElementById('details-memory-search-btn');
    const detailsMemoryInput = document.getElementById('details-memory-search-input');
    if (detailsMemoryBtn) detailsMemoryBtn.addEventListener('click', searchDetailsMemory);
    if (detailsMemoryInput) {
        detailsMemoryInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); searchDetailsMemory(); }
        });
    }
    loadDetailsTools(agentId);
    loadDetailsSkills(agentId);
    loadMiniActivity(agentId);
}

function renderMemberEditForm(agentId) {
    const agent = agentsById[agentId];
    if (!agent) return;
    detailsMode = 'edit';
    detailsTitle.textContent = `Edit ${agent.name}`;

    detailsBody.innerHTML = `
        <button class="details-back-link" id="details-cancel-edit"><i class="fas fa-arrow-left"></i> Cancel</button>
        <div class="form-group">
            <label class="form-label">Name</label>
            <input class="form-control" id="edit-name" type="text" value="${escapeAttr(agent.name)}">
        </div>
        <div class="form-group">
            <label class="form-label">Description</label>
            <input class="form-control" id="edit-description" type="text" value="${escapeAttr(agent.description || '')}">
        </div>
        <div class="form-group">
            <label class="form-label">Tools (comma-separated, or * for all)</label>
            <input class="form-control" id="edit-tools" type="text" value="${escapeAttr(toolOrSkillListToInputValue(agent.tools))}">
        </div>
        <div class="form-group">
            <label class="form-label">Skills (comma-separated, or * for all)</label>
            <input class="form-control" id="edit-skills" type="text" value="${escapeAttr(toolOrSkillListToInputValue(agent.skills))}">
        </div>
        <div class="form-group">
            <label class="form-label">Model (blank for default)</label>
            <input class="form-control" id="edit-model" type="text" value="${escapeAttr(agent.model || '')}" placeholder="e.g. gpt-4o, qwen3:14b">
        </div>
        <div class="form-group">
            <label class="form-label">Provider (blank for default)</label>
            <input class="form-control" id="edit-provider" type="text" value="${escapeAttr(agent.provider || '')}" placeholder="e.g. ollama, openai, llamastack">
        </div>
        <div class="form-group">
            <label class="form-label">Soul (persona)</label>
            <textarea class="form-control" id="edit-soul" rows="10">${escapeHtml(agent.soul || '')}</textarea>
        </div>
        <div class="details-actions">
            <button class="btn btn-primary" id="edit-save-btn">Save</button>
            <button class="btn btn-secondary" id="edit-cancel-btn">Cancel</button>
            <span class="form-status" id="edit-status"></span>
        </div>
    `;

    document.getElementById('details-cancel-edit').addEventListener('click', () => renderMemberDetails(agentId));
    document.getElementById('edit-cancel-btn').addEventListener('click', () => renderMemberDetails(agentId));
    document.getElementById('edit-save-btn').addEventListener('click', () => saveMemberEdit(agentId));
}

async function saveMemberEdit(agentId) {
    const statusEl = document.getElementById('edit-status');
    const payload = {
        name: document.getElementById('edit-name').value.trim(),
        description: document.getElementById('edit-description').value.trim(),
        tools: parseToolOrSkillInput(document.getElementById('edit-tools').value),
        skills: parseToolOrSkillInput(document.getElementById('edit-skills').value),
        model: document.getElementById('edit-model').value.trim() || null,
        provider: document.getElementById('edit-provider').value.trim() || null,
        soul: document.getElementById('edit-soul').value,
    };

    statusEl.textContent = 'Saving...';
    statusEl.className = 'form-status';
    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        await loadAgents();
        renderMemberDetails(agentId);
        if (agentId === currentAgentId) updateChatHeaderForCurrentAgent();
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'form-status error';
    }
}

async function deleteAgentFromDetails(agentId) {
    if (!confirm(`Delete agent "${agentId}"? This cannot be undone.`)) return;
    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (currentAgentId === agentId) {
            await selectMember(KIT_AGENT_ID);
        }
        await loadAgents();
        renderMemberDetails(currentAgentId);
    } catch (error) {
        alert(`Failed to delete agent: ${error.message}`);
    }
}

// --- Add teammate form (details panel, "new" mode) ---

async function loadAgentTemplatesForNewAgentForm(selectEl) {
    if (!selectEl) return;
    try {
        const response = await apiFetch(`${API_BASE}/agent-templates`);
        if (!response.ok) throw new Error('Failed to load templates');
        const templates = await response.json();
        selectEl.innerHTML = templates.map(t =>
            `<option value="${escapeAttr(t.id)}">${escapeHtml(t.name || t.id)}</option>`
        ).join('');
    } catch (error) {
        selectEl.innerHTML = '<option value="">(failed to load templates)</option>';
    }
}

function renderAddTeammateForm() {
    detailsMode = 'new';
    detailsTitle.textContent = 'Add teammate';

    detailsBody.innerHTML = `
        <button class="details-back-link" id="details-cancel-new"><i class="fas fa-arrow-left"></i> Back</button>
        <div class="form-group">
            <label class="form-label">Template</label>
            <select class="form-control" id="new-agent-template"></select>
        </div>
        <div class="form-group">
            <label class="form-label">Id <span style="font-weight:normal;color:var(--pf-v5-global--Color--200)">(cannot be changed later)</span></label>
            <input class="form-control" id="new-agent-id" type="text" placeholder="e.g. tester-1">
        </div>
        <div class="form-group">
            <label class="form-label">Display name (optional)</label>
            <input class="form-control" id="new-agent-name" type="text" placeholder="e.g. Tester">
        </div>
        <div class="details-actions">
            <button class="btn btn-primary" id="create-agent-button">Create teammate</button>
            <span class="form-status" id="create-agent-status"></span>
        </div>
    `;

    document.getElementById('details-cancel-new').addEventListener('click', () => renderMemberDetails(currentAgentId));
    document.getElementById('create-agent-button').addEventListener('click', createAgent);
    loadAgentTemplatesForNewAgentForm(document.getElementById('new-agent-template'));
}

async function createAgent() {
    const templateSelect = document.getElementById('new-agent-template');
    const idInput = document.getElementById('new-agent-id');
    const nameInput = document.getElementById('new-agent-name');
    const statusEl = document.getElementById('create-agent-status');
    const btn = document.getElementById('create-agent-button');

    const templateId = templateSelect.value;
    const id = idInput.value.trim();
    const name = nameInput.value.trim();
    if (!templateId || !id) {
        statusEl.textContent = 'Template and id are required';
        statusEl.className = 'form-status error';
        return;
    }

    btn.disabled = true;
    statusEl.textContent = 'Creating...';
    statusEl.className = 'form-status';
    try {
        const response = await apiFetch(`${API_BASE}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ template_id: templateId, id, name: name || undefined }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        const created = await response.json();
        statusEl.textContent = 'Created';
        statusEl.className = 'form-status success';
        await loadAgents();
        renderMemberDetails(created.id);
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'form-status error';
    } finally {
        btn.disabled = false;
    }
}

addTeammateBtn.addEventListener('click', () => {
    showDetailsPanel();
    renderAddTeammateForm();
    closeMobileSidebar();
});

// --- Workspace overlay: Team Activity / Schedules / Sessions ---
// Workspace-wide sections that aren't tied to any one team member.

const WORKSPACE_LOADERS = {
    activity: loadActivity,
    schedules: loadSchedules,
    sessions: loadSessions,
};

function openWorkspacePanel(tabName) {
    currentWorkspaceTab = tabName;
    workspaceOverlay.hidden = false;
    document.querySelectorAll('.rail-btn[data-panel]').forEach(b => b.classList.toggle('active', b.dataset.panel === tabName));
    document.querySelectorAll('.workspace-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tabName));
    document.querySelectorAll('.workspace-modal-body .tab-content').forEach(t => t.classList.toggle('active', t.id === `${tabName}-tab`));
    const loader = WORKSPACE_LOADERS[tabName];
    if (loader) loader();
}

function closeWorkspacePanel() {
    workspaceOverlay.hidden = true;
    currentWorkspaceTab = null;
    document.querySelectorAll('.rail-btn[data-panel]').forEach(b => b.classList.remove('active'));
}

document.querySelectorAll('.rail-btn[data-panel]').forEach(btn => {
    btn.addEventListener('click', () => openWorkspacePanel(btn.dataset.panel));
});
document.querySelectorAll('.workspace-tab').forEach(btn => {
    btn.addEventListener('click', () => openWorkspacePanel(btn.dataset.tab));
});
closeWorkspaceBtn.addEventListener('click', closeWorkspacePanel);
workspaceOverlay.addEventListener('click', (e) => {
    if (e.target === workspaceOverlay) closeWorkspacePanel();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !workspaceOverlay.hidden) closeWorkspacePanel();
});

// Load sessions
async function loadSessions() {
    sessionsList.innerHTML = '<div class="spinner"></div>';

    try {
        const response = await apiFetch(`${API_BASE}/sessions`);
        if (!response.ok) throw new Error('Failed to load sessions');

        const sessions = await response.json();

        if (sessions.length === 0) {
            sessionsList.innerHTML = '<p class="empty-state">No active sessions</p>';
            return;
        }

        sessionsList.innerHTML = sessions.map(session => `
            <div class="session-card">
                <h4>${escapeHtml(session.session_id)}</h4>
                <p><strong>Platform:</strong> ${escapeHtml(session.platform)}</p>
                <p><strong>Messages:</strong> ${session.message_count}</p>
                <p><strong>Last active:</strong> ${new Date(session.last_active).toLocaleString()}</p>
            </div>
        `).join('');

    } catch (error) {
        sessionsList.innerHTML = `<p class="empty-state">Error loading sessions: ${escapeHtml(error.message)}</p>`;
    }
}

// --- Details panel: Memory / Tools / Skills (scoped to current agent) ---

async function loadDetailsTools(agentId) {
    const container = document.getElementById('details-tools-list');
    if (!container) return;
    container.innerHTML = '<div class="spinner" style="margin:12px auto;"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/tools`);
        if (!response.ok) throw new Error('Failed to load tools');
        const allTools = await response.json();
        const agent = agentsById[agentId];
        const allowed = agent ? agent.tools : '*';
        const tools = allowed === '*' ? allTools : allTools.filter(t => allowed.includes(t.name));
        if (tools.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding:10px 0;">No tools</div>';
            return;
        }
        container.innerHTML = tools.map(tool => `
            <div class="tool-entry">
                <div class="tool-entry-name">${escapeHtml(tool.name)}</div>
                <div class="tool-entry-desc">${escapeHtml(tool.description || '')}</div>
            </div>
        `).join('');
    } catch (error) {
        container.innerHTML = `<div class="empty-state" style="padding:10px 0;">Error: ${escapeHtml(error.message)}</div>`;
    }
}

async function loadDetailsSkills(agentId) {
    const container = document.getElementById('details-skills-list');
    if (!container) return;
    container.innerHTML = '<div class="spinner" style="margin:12px auto;"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/skills`);
        if (!response.ok) throw new Error('Failed to load skills');
        const allSkills = await response.json();
        const agent = agentsById[agentId];
        const allowed = agent ? agent.skills : '*';
        const skills = allowed === '*' ? allSkills : allSkills.filter(s => allowed.includes(s.name));
        if (skills.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding:10px 0;">No skills</div>';
            return;
        }
        container.innerHTML = skills.map(skill => `
            <div class="tool-entry">
                <div class="tool-entry-name">${escapeHtml(skill.name)}</div>
                <div class="tool-entry-desc">${escapeHtml(skill.description || '')}</div>
            </div>
        `).join('');
    } catch (error) {
        container.innerHTML = `<div class="empty-state" style="padding:10px 0;">Error: ${escapeHtml(error.message)}</div>`;
    }
}

// --- Team Activity tab: cross-agent feed of tool calls, delegation, and
// status changes, independent of which chat thread is currently open. ---

function formatActivityEntry(entry) {
    const time = entry.timestamp ? new Date(entry.timestamp).toLocaleTimeString() : '';
    if (entry.event_type === 'agent_status') {
        const task = entry.current_task ? ` — ${entry.current_task}` : '';
        return `
            <div class="activity-item status-event">
                <div class="activity-item-header">
                    <span>${escapeHtml(entry.agent_id)} is now ${escapeHtml(entry.status)}${escapeHtml(task)}</span>
                    <span class="activity-item-time">${time}</span>
                </div>
            </div>
        `;
    }
    const detail = entry.detail || {};
    const label = entry.event_type === 'tool_call_start'
        ? `▶ ${entry.agent_id} called ${detail.tool_name}`
        : `✓ ${entry.agent_id} finished ${detail.tool_name}`;
    const body = entry.event_type === 'tool_call_result'
        ? (detail.result || '').slice(0, 300)
        : JSON.stringify(detail.tool_args || {});
    return `
        <div class="activity-item">
            <div class="activity-item-header">
                <span>${escapeHtml(label)}</span>
                <span class="activity-item-time">${time}</span>
            </div>
            <div class="activity-item-detail">${escapeHtml(body)}</div>
        </div>
    `;
}

async function loadActivity() {
    refreshActivityBtn.disabled = true;
    activityList.innerHTML = '<div class="spinner"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/agents/activity`);
        if (!response.ok) throw new Error('Failed to load activity');
        const entries = await response.json();
        activityList.innerHTML = entries.length === 0
            ? '<p class="empty-state">No agent activity yet</p>'
            : entries.slice().reverse().map(formatActivityEntry).join('');
    } catch (error) {
        activityList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        refreshActivityBtn.disabled = false;
    }
}

refreshActivityBtn.addEventListener('click', loadActivity);

async function searchDetailsMemory() {
    const input = document.getElementById('details-memory-search-input');
    const btn = document.getElementById('details-memory-search-btn');
    const results = document.getElementById('details-memory-results');
    if (!input || !btn || !results) return;

    const query = input.value.trim();
    if (!query) return;

    btn.disabled = true;
    btn.textContent = 'Searching...';
    results.innerHTML = '<div class="spinner" style="margin:12px auto;"></div>';

    try {
        const response = await apiFetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                platform: PLATFORM,
                user_id: USER_ID,
                agent_id: currentAgentId,
                message: `Use memory_search to find: ${query}`
            })
        });

        if (!response.ok) throw new Error('Search failed');
        const data = await response.json();

        results.innerHTML = `
            <div class="memory-item">
                <div class="memory-item-type">Search Results</div>
                <div class="memory-item-content">${escapeHtml(data.response)}</div>
            </div>
        `;
    } catch (error) {
        results.innerHTML = `<div class="empty-state" style="padding:10px 0;">Error: ${escapeHtml(error.message)}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Search';
    }
}

// Load schedules
async function loadSchedules() {
    refreshSchedulesBtn.disabled = true;
    refreshSchedulesBtn.textContent = 'Loading...';
    schedulesList.innerHTML = '<div class="spinner"></div>';

    try {
        const response = await apiFetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                platform: PLATFORM,
                user_id: USER_ID,
                message: 'Use schedule_list to show all schedules'
            })
        });

        if (!response.ok) throw new Error('Failed to load schedules');

        const data = await response.json();

        // Display raw response (schedules formatted by tool)
        schedulesList.innerHTML = `
            <div class="schedule-card">
                <pre style="white-space: pre-wrap; font-size: 13px; margin: 0; color: var(--text-secondary);">${escapeHtml(data.response)}</pre>
            </div>
        `;

    } catch (error) {
        schedulesList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        refreshSchedulesBtn.disabled = false;
        refreshSchedulesBtn.textContent = 'Refresh';
    }
}

refreshSchedulesBtn.addEventListener('click', loadSchedules);


// WebSocket connection
function connectWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        return;
    }

    try {
        // Browsers can't set an Authorization header on a WebSocket
        // handshake, so the token (when we have one) travels as a query
        // param instead — matches the server's _websocket_token_valid check.
        const wsUrl = authToken
            ? `${WS_BASE}/ws?token=${encodeURIComponent(authToken)}`
            : `${WS_BASE}/ws`;
        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log('WebSocket connected');
            wsReconnectAttempts = 0;
            addMessage('Real-time updates enabled', 'system');
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        };

        ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };

        ws.onclose = () => {
            console.log('WebSocket disconnected');

            // Attempt reconnect
            if (wsReconnectAttempts < wsMaxReconnectAttempts) {
                wsReconnectAttempts++;
                setTimeout(connectWebSocket, 2000 * wsReconnectAttempts);
            }
        };
    } catch (error) {
        console.error('Failed to connect WebSocket:', error);
    }
}

// Handle a cross-agent activity event (tool calls, delegation, status
// changes) - these come from SessionManager and carry `event_type` rather
// than `type` (which is reserved for the chat-turn events below).
function handleActivityEvent(data) {
    if (data.event_type === 'agent_status') {
        agentStatuses[data.agent_id] = data;
        if (data.agent_id === currentAgentId) updateChatHeaderForCurrentAgent();
        refreshMemberListVisualState();
        updateDetailsStatusLine(data.agent_id);
    }
    if (!workspaceOverlay.hidden && currentWorkspaceTab === 'activity') {
        activityList.insertAdjacentHTML('afterbegin', formatActivityEntry(data));
    }
}

// Handle WebSocket messages
function handleWebSocketMessage(data) {
    console.log('WebSocket message:', data);

    if (data.event_type) {
        handleActivityEvent(data);
        return;
    }

    const isOwnSession = data.session_id === currentSessionId();

    switch (data.type) {
        case 'connected':
            console.log('WebSocket ready');
            break;

        case 'stream_start':
            if (isOwnSession) {
                removeThinkingIndicator();
                streamingText = '';

                const agent = agentsById[currentAgentId];
                const name = agent ? agent.name : (currentAgentId === KIT_AGENT_ID ? 'Kit' : currentAgentId);

                streamingMessageDiv = document.createElement('div');
                streamingMessageDiv.className = 'message assistant streaming';

                const avatarDiv = document.createElement('div');
                avatarDiv.className = 'avatar';
                avatarDiv.style.background = avatarColorFor(currentAgentId);
                avatarDiv.textContent = initialsFor(name);

                const bodyDiv = document.createElement('div');
                bodyDiv.className = 'message-body';

                const headerLine = document.createElement('div');
                headerLine.className = 'message-header-line';
                const authorSpan = document.createElement('span');
                authorSpan.className = 'message-author';
                authorSpan.textContent = name;
                const timeSpan = document.createElement('span');
                timeSpan.className = 'message-time';
                timeSpan.textContent = new Date().toLocaleTimeString();
                headerLine.appendChild(authorSpan);
                headerLine.appendChild(timeSpan);

                streamingContentDiv = document.createElement('div');
                streamingContentDiv.className = 'message-content';

                bodyDiv.appendChild(headerLine);
                bodyDiv.appendChild(streamingContentDiv);
                streamingMessageDiv.appendChild(avatarDiv);
                streamingMessageDiv.appendChild(bodyDiv);
                chatMessages.appendChild(streamingMessageDiv);
                scrollChatToBottom();
            }
            break;

        case 'text_delta':
            if (isOwnSession && streamingContentDiv) {
                streamingText += data.content;
                streamingContentDiv.textContent = streamingText;
                scrollChatToBottom();
            }
            break;

        case 'tool_call_start':
            if (isOwnSession && streamingMessageDiv) {
                const toolDiv = document.createElement('div');
                toolDiv.className = 'tool-call tool-running';
                toolDiv.id = `tool-${data.tool_name}-${Date.now()}`;
                toolDiv.innerHTML = `<span class="tool-call-name">▶ ${escapeHtml(data.tool_name)}</span>`;
                const body = streamingMessageDiv.querySelector('.message-body');
                body.appendChild(toolDiv);
                scrollChatToBottom();
            }
            break;

        case 'tool_call_result':
            if (isOwnSession && streamingMessageDiv) {
                const running = streamingMessageDiv.querySelector('.tool-call.tool-running');
                if (running) {
                    running.classList.remove('tool-running');
                    running.classList.add('tool-done');
                    running.querySelector('.tool-call-name').textContent =
                        `✓ ${data.tool_name}`;
                    const resultDiv = document.createElement('div');
                    resultDiv.className = 'tool-call-result';
                    const preview = data.result.length > 300
                        ? data.result.slice(0, 300) + '...'
                        : data.result;
                    resultDiv.textContent = preview;
                    running.appendChild(resultDiv);
                    scrollChatToBottom();
                }
            }
            break;

        case 'stream_end':
            if (isOwnSession) {
                if (streamingMessageDiv) {
                    streamingMessageDiv.classList.remove('streaming');
                }
                if (data.message_count) {
                    messageCount = data.message_count;
                    messageCountSpan.textContent = `${messageCount} messages`;
                }
                finishStreaming();
            }
            break;

        case 'stream_error':
            if (isOwnSession) {
                removeThinkingIndicator();
                addMessage(`Error: ${data.error}`, 'system');
                finishStreaming();
            }
            break;

        case 'user_message':
            if (isOwnSession && data.sender) {
                // A delegated instruction another agent placed into this
                // thread - not the human, so it wasn't rendered locally yet.
                addMessage(data.message, 'user', null, data.sender);
            } else if (!isOwnSession) {
                addMessage(`[${data.session_id}] ${data.message}`, 'user');
            }
            break;

        case 'assistant_message':
            if (isOwnSession && data.via_delegation) {
                // A delegated task's reply - the normal chat flow already
                // renders replies via stream_end, so only delegation needs
                // this (the human never sent that turn from this tab).
                addMessage(data.message, 'assistant');
            } else if (!isOwnSession) {
                addMessage(`[${data.session_id}] ${data.message}`, 'assistant');
            }
            loadSessions();
            break;

        case 'pong':
            console.log('Pong received');
            break;

        default:
            console.log('Unknown message type:', data.type);
    }
}

// Send WebSocket ping
function sendPing() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
    }
}

// Load chat history from server, for whichever agent is currently selected
async function loadChatHistory() {
    const sessionId = currentSessionId();
    chatMessages.innerHTML = '';
    try {
        const response = await apiFetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/messages`);
        if (!response.ok) return;
        const messages = await response.json();
        for (const msg of messages) {
            const div = addMessage(msg.content, msg.role, null, msg.sender || null);
            const timeDiv = div.querySelector('.message-time');
            if (timeDiv && msg.timestamp) {
                timeDiv.textContent = new Date(msg.timestamp).toLocaleTimeString();
            }
        }
        messageCount = messages.length;
        messageCountSpan.textContent = `${messageCount} messages`;
    } catch (error) {
        console.error('Failed to load chat history:', error);
    }
}

function promptForAuthToken() {
    const entered = window.prompt(
        'This Kit gateway requires a token (GATEWAY_TOKEN) to access the API.\n' +
        'Enter it to continue:'
    );
    if (!entered) return;

    authToken = entered.trim();
    try {
        localStorage.setItem('kit_gateway_token', authToken);
    } catch (error) {
        // localStorage unavailable — token still works for this page load,
        // just won't survive a refresh.
    }
}

async function loadRuntimeConfig() {
    try {
        const res = await apiFetch(`${API_BASE}/config`);
        if (res.ok) {
            const config = await res.json();
            if (typeof config.ws_max_reconnect_attempts === 'number') {
                wsMaxReconnectAttempts = config.ws_max_reconnect_attempts;
            }
            if (config.auth_required && !authToken) {
                promptForAuthToken();
            }
        }
    } catch (error) {
        console.warn('Failed to load /config, using defaults:', error);
    }
}

// Initialize
async function init() {
    // Independent requests - run them in parallel rather than one after
    // the other, so /config doesn't add a serial round-trip to page load.
    const [, connected] = await Promise.all([
        loadRuntimeConfig(),
        checkConnection(),
    ]);

    if (connected) {
        await loadAgents();
        await loadChatHistory();
        addMessage('Connected to Kit', 'system');
        loadSessions();
        connectWebSocket();
    } else {
        addMessage(`Failed to connect to gateway. Make sure it's running at ${API_BASE}.`, 'system');
    }

    // Check connection every 10 seconds
    setInterval(checkConnection, 10000);

    // Send WebSocket ping every 30 seconds
    setInterval(sendPing, 30000);
}

init();

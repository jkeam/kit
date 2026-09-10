// Personal Assistant Web UI

// Derive from the page's own origin instead of hardcoding the gateway port,
// so this works whether the gateway is served on 18789 (dev default) or
// something else.
const API_BASE = window.location.origin;
const WS_BASE = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
const PLATFORM = 'web';
const USER_ID = 'browser';
const KIT_AGENT_ID = 'kit';

// Which team member the chat panel is currently talking to. Mirrors the
// server's make_session_id: Kit keeps the original {platform}:{user_id}
// session id, any other agent gets its own {platform}:{user_id}:{agent_id}
// thread, so switching this switches to a separately-remembered conversation.
let currentAgentId = KIT_AGENT_ID;

function currentSessionId(agentId = currentAgentId) {
    return agentId === KIT_AGENT_ID ? `${PLATFORM}:${USER_ID}` : `${PLATFORM}:${USER_ID}:${agentId}`;
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
const chatScrollContainer = chatMessages.closest('.chat-card-body') || chatMessages;
const chatInput = document.getElementById('chat-input');
const sendButton = document.getElementById('send-button');
const clearChatButton = document.getElementById('clear-chat-button');
const connectionStatus = document.getElementById('connection-status');
const connectionText = document.getElementById('connection-text');
const messageCountSpan = document.getElementById('message-count');
const sessionIdSpan = document.getElementById('session-id');
const sessionsList = document.getElementById('sessions-list');
const memorySearchInput = document.getElementById('memory-search-input');
const memorySearchBtn = document.getElementById('memory-search-btn');
const memoryResults = document.getElementById('memory-results');
const refreshSchedulesBtn = document.getElementById('refresh-schedules');
const schedulesList = document.getElementById('schedules-list');
const refreshSkillsBtn = document.getElementById('refresh-skills');
const skillsList = document.getElementById('skills-list');
const personaEditor = document.getElementById('persona-editor');
const savePersonaBtn = document.getElementById('save-persona');
const personaStatus = document.getElementById('persona-status');
const toggleInfoPanelBtn = document.getElementById('toggle-info-panel');
const chatPanelCol = document.getElementById('chat-panel-col');
const infoPanelCol = document.getElementById('info-panel-col');
const chatAgentSelect = document.getElementById('chat-agent-select');
const chatTargetStatusDot = document.getElementById('chat-target-status-dot');
const agentsList = document.getElementById('agents-list');
const newAgentTemplateSelect = document.getElementById('new-agent-template');
const newAgentIdInput = document.getElementById('new-agent-id');
const newAgentNameInput = document.getElementById('new-agent-name');
const createAgentButton = document.getElementById('create-agent-button');
const createAgentStatus = document.getElementById('create-agent-status');
const toolsList = document.getElementById('tools-list');
const refreshActivityBtn = document.getElementById('refresh-activity');
const activityList = document.getElementById('activity-list');

// Latest known status per agent_id ({status, current_task, updated_at}),
// seeded from GET /agents/status and kept live via `agent_status` WS events.
let agentStatuses = {};

// Info panel visibility (tools/sessions/memory/schedules/skills/persona).
// The human user doesn't always need to see what the agent has access to,
// so it can be tucked away; state persists per-browser via localStorage.
const INFO_PANEL_HIDDEN_KEY = 'kit_info_panel_hidden';

function setInfoPanelHidden(hidden) {
    infoPanelCol.hidden = hidden;
    chatPanelCol.classList.toggle('pf-m-6-col', !hidden);
    chatPanelCol.classList.toggle('pf-m-12-col', hidden);
    toggleInfoPanelBtn.setAttribute('aria-expanded', String(!hidden));
    toggleInfoPanelBtn.classList.toggle('pf-m-active', !hidden);
    try {
        localStorage.setItem(INFO_PANEL_HIDDEN_KEY, hidden ? '1' : '0');
    } catch (error) {
        // localStorage unavailable - just won't persist across reloads.
    }
}

let infoPanelHidden = false;
try {
    infoPanelHidden = localStorage.getItem(INFO_PANEL_HIDDEN_KEY) === '1';
} catch (error) {
    // ignore
}
setInfoPanelHidden(infoPanelHidden);

toggleInfoPanelBtn.addEventListener('click', () => {
    setInfoPanelHidden(!infoPanelCol.hidden);
});

// Tab switching
document.querySelectorAll('.pf-v5-c-tabs__link').forEach(btn => {
    btn.addEventListener('click', () => {
        const tabName = btn.dataset.tab;

        // Update tab items
        document.querySelectorAll('.pf-v5-c-tabs__item').forEach(item => {
            item.classList.remove('pf-m-current');
        });
        btn.closest('.pf-v5-c-tabs__item').classList.add('pf-m-current');

        // Update tab content
        document.querySelectorAll('.tab-content').forEach(t => {
            t.classList.remove('pf-m-current');
        });
        document.getElementById(`${tabName}-tab`).classList.add('pf-m-current');

        // Load data if needed
        if (tabName === 'sessions') loadSessions();
        if (tabName === 'agents') loadAgents();
        if (tabName === 'activity') loadActivity();
        if (tabName === 'tools') loadTools();
        if (tabName === 'schedules') loadSchedules();
        if (tabName === 'skills') loadSkills();
        if (tabName === 'persona') loadPersona();
    });
});

// Check connection
async function checkConnection() {
    try {
        const response = await apiFetch(`${API_BASE}/health`);
        if (response.ok) {
            connectionStatus.classList.remove('pf-m-red');
            connectionStatus.classList.add('pf-m-green');
            connectionText.textContent = 'Connected';
            return true;
        }
    } catch (error) {
        connectionStatus.classList.remove('pf-m-green');
        connectionStatus.classList.add('pf-m-red');
        connectionText.textContent = 'Disconnected';
        return false;
    }
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

    if (sender) {
        const senderDiv = document.createElement('div');
        senderDiv.className = 'message-sender-label';
        senderDiv.textContent = `🤖 ${sender} asked:`;
        messageDiv.appendChild(senderDiv);
    }

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = content;

    const timeDiv = document.createElement('div');
    timeDiv.className = 'message-time';
    timeDiv.textContent = new Date().toLocaleTimeString();

    messageDiv.appendChild(contentDiv);
    messageDiv.appendChild(timeDiv);

    chatMessages.appendChild(messageDiv);
    scrollChatToBottom();

    return messageDiv;
}

// Add thinking indicator
function addThinkingIndicator() {
    const thinkingDiv = document.createElement('div');
    thinkingDiv.id = 'thinking-indicator';
    thinkingDiv.className = 'message assistant';
    thinkingDiv.innerHTML = `
        <div class="message-content">
            <div class="pf-v5-c-spinner pf-m-md" role="progressbar">
                <span class="pf-v5-c-spinner__clipper"></span>
                <span class="pf-v5-c-spinner__lead-ball"></span>
                <span class="pf-v5-c-spinner__tail-ball"></span>
            </div>
            <span style="margin-left: 12px;">Thinking...</span>
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
    sendButton.textContent = 'Sending...';

    addMessage(message, 'user');
    chatInput.value = '';

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
            sendButton.textContent = 'Send';
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

// Update the little status dot in the chat header + on an Agents-tab card,
// from the latest known agentStatuses entry.
function updateStatusDot(dotEl, agentId) {
    if (!dotEl) return;
    const status = agentStatuses[agentId];
    dotEl.classList.remove('busy', 'idle');
    if (status) {
        dotEl.classList.add(status.status === 'busy' ? 'busy' : 'idle');
        dotEl.title = status.current_task ? `${status.status}: ${status.current_task}` : status.status;
    } else {
        dotEl.title = 'Idle';
    }
}

// Switch who the chat panel is talking to (Kit or a specific agent) - moves
// to that agent's own persistent thread rather than continuing this one.
async function switchChatAgent(agentId) {
    currentAgentId = agentId;
    sessionIdSpan.textContent = currentSessionId();
    updateStatusDot(chatTargetStatusDot, agentId);
    messageCount = 0;
    messageCountSpan.textContent = '0 messages';
    await loadChatHistory();
}

chatAgentSelect.addEventListener('change', () => {
    switchChatAgent(chatAgentSelect.value);
});

function finishStreaming() {
    streamingMessageDiv = null;
    streamingContentDiv = null;
    streamingText = '';
    chatInput.disabled = false;
    sendButton.disabled = false;
    sendButton.textContent = 'Send';
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

// Load sessions
async function loadSessions() {
    sessionsList.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';

    try {
        const response = await apiFetch(`${API_BASE}/sessions`);
        if (!response.ok) throw new Error('Failed to load sessions');

        const sessions = await response.json();

        if (sessions.length === 0) {
            sessionsList.innerHTML = '<div class="pf-v5-c-empty-state"><p class="empty-state">No active sessions</p></div>';
            return;
        }

        sessionsList.innerHTML = sessions.map(session => `
            <div class="session-card">
                <h4>${session.session_id}</h4>
                <p><strong>Platform:</strong> ${session.platform}</p>
                <p><strong>Messages:</strong> ${session.message_count}</p>
                <p><strong>Last active:</strong> ${new Date(session.last_active).toLocaleString()}</p>
            </div>
        `).join('');

    } catch (error) {
        sessionsList.innerHTML = `<p class="empty-state">Error loading sessions: ${error.message}</p>`;
    }
}

// --- Agents tab: roster of team members, each with its own tool/skill
// scoping and persona, created from an overridable template. ---

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

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, '&quot;');
}

function populateAgentSelect(agents) {
    const previousValue = chatAgentSelect.value || currentAgentId;
    chatAgentSelect.innerHTML = agents.map(agent =>
        `<option value="${agent.id}">${agent.id === KIT_AGENT_ID ? 'Kit (Manager)' : agent.name}</option>`
    ).join('');
    if (agents.some(a => a.id === previousValue)) {
        chatAgentSelect.value = previousValue;
    }
}

async function loadAgents() {
    agentsList.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';
    try {
        const [agentsRes, statusRes] = await Promise.all([
            apiFetch(`${API_BASE}/agents`),
            apiFetch(`${API_BASE}/agents/status`),
        ]);
        if (!agentsRes.ok) throw new Error('Failed to load agents');
        const agents = await agentsRes.json();
        if (statusRes.ok) {
            agentStatuses = await statusRes.json();
        }

        agentsList.innerHTML = agents.map(agent => {
            const isKit = agent.id === KIT_AGENT_ID;
            return `
            <div class="agent-card" data-agent-id="${agent.id}">
                <div class="agent-card-header">
                    <div class="agent-status-dot" data-status-dot="${agent.id}"></div>
                    <h4>${agent.name}</h4>
                    <span class="pf-v5-c-label pf-m-outline pf-m-small">
                        <span class="pf-v5-c-label__content">${agent.id}</span>
                    </span>
                </div>
                <div class="agent-card-view">
                    <p class="agent-card-meta">${agent.description || ''}</p>
                    <p class="agent-card-meta"><strong>Tools:</strong> ${formatToolOrSkillList(agent.tools)}</p>
                    <p class="agent-card-meta"><strong>Skills:</strong> ${formatToolOrSkillList(agent.skills)}</p>
                    <p class="agent-card-task" data-task-for="${agent.id}"></p>
                    <div class="agent-card-actions">
                        <button class="pf-v5-c-button pf-m-secondary pf-m-small" data-talk-to="${agent.id}">Talk to ${agent.name}</button>
                        ${!isKit ? `<button class="pf-v5-c-button pf-m-secondary pf-m-small" data-edit-agent="${agent.id}">Edit</button>` : ''}
                        ${!isKit ? `<button class="pf-v5-c-button pf-m-danger pf-m-small" data-delete-agent="${agent.id}">Delete</button>` : ''}
                    </div>
                </div>
                ${!isKit ? `
                <div class="agent-edit-panel" hidden>
                    <div class="pf-v5-c-form__group">
                        <label class="pf-v5-c-form__label">Name</label>
                        <input class="pf-v5-c-form-control agent-edit-name" type="text" value="${escapeAttr(agent.name)}">
                    </div>
                    <div class="pf-v5-c-form__group">
                        <label class="pf-v5-c-form__label">Description</label>
                        <input class="pf-v5-c-form-control agent-edit-description" type="text" value="${escapeAttr(agent.description || '')}">
                    </div>
                    <div class="pf-v5-c-form__group">
                        <label class="pf-v5-c-form__label">Tools (comma-separated, or * for all)</label>
                        <input class="pf-v5-c-form-control agent-edit-tools" type="text" value="${escapeAttr(toolOrSkillListToInputValue(agent.tools))}">
                    </div>
                    <div class="pf-v5-c-form__group">
                        <label class="pf-v5-c-form__label">Skills (comma-separated, or * for all)</label>
                        <input class="pf-v5-c-form-control agent-edit-skills" type="text" value="${escapeAttr(toolOrSkillListToInputValue(agent.skills))}">
                    </div>
                    <div class="pf-v5-c-form__group">
                        <label class="pf-v5-c-form__label">Soul (persona)</label>
                        <textarea class="pf-v5-c-form-control agent-edit-soul" rows="10">${escapeHtml(agent.soul || '')}</textarea>
                    </div>
                    <div class="agent-card-actions">
                        <button class="pf-v5-c-button pf-m-primary pf-m-small" data-save-agent="${agent.id}">Save</button>
                        <button class="pf-v5-c-button pf-m-link pf-m-small" data-cancel-edit="${agent.id}">Cancel</button>
                        <span class="agent-form-status" data-edit-status="${agent.id}"></span>
                    </div>
                </div>` : ''}
            </div>
        `;
        }).join('');

        agentsList.querySelectorAll('[data-status-dot]').forEach(dot => {
            const agentId = dot.dataset.statusDot;
            updateStatusDot(dot, agentId);
            const status = agentStatuses[agentId];
            const taskEl = agentsList.querySelector(`[data-task-for="${agentId}"]`);
            if (taskEl) taskEl.textContent = status && status.current_task ? status.current_task : '';
        });

        agentsList.querySelectorAll('[data-talk-to]').forEach(btn => {
            btn.addEventListener('click', () => {
                chatAgentSelect.value = btn.dataset.talkTo;
                switchChatAgent(btn.dataset.talkTo);
            });
        });

        agentsList.querySelectorAll('[data-delete-agent]').forEach(btn => {
            btn.addEventListener('click', () => deleteAgent(btn.dataset.deleteAgent));
        });

        agentsList.querySelectorAll('[data-edit-agent]').forEach(btn => {
            btn.addEventListener('click', () => {
                const card = btn.closest('.agent-card');
                card.querySelector('.agent-card-view').hidden = true;
                card.querySelector('.agent-edit-panel').hidden = false;
            });
        });

        agentsList.querySelectorAll('[data-cancel-edit]').forEach(btn => {
            btn.addEventListener('click', () => {
                const card = btn.closest('.agent-card');
                card.querySelector('.agent-edit-panel').hidden = true;
                card.querySelector('.agent-card-view').hidden = false;
            });
        });

        agentsList.querySelectorAll('[data-save-agent]').forEach(btn => {
            btn.addEventListener('click', () => saveAgentEdit(btn.dataset.saveAgent));
        });

        populateAgentSelect(agents);
    } catch (error) {
        agentsList.innerHTML = `<p class="empty-state">Error loading agents: ${error.message}</p>`;
    }
}

async function loadAgentTemplatesForNewAgentForm() {
    try {
        const response = await apiFetch(`${API_BASE}/agent-templates`);
        if (!response.ok) throw new Error('Failed to load templates');
        const templates = await response.json();
        newAgentTemplateSelect.innerHTML = templates.map(t =>
            `<option value="${t.id}">${t.name || t.id}</option>`
        ).join('');
    } catch (error) {
        newAgentTemplateSelect.innerHTML = '<option value="">(failed to load templates)</option>';
    }
}

async function createAgent() {
    const templateId = newAgentTemplateSelect.value;
    const id = newAgentIdInput.value.trim();
    const name = newAgentNameInput.value.trim();
    if (!templateId || !id) {
        createAgentStatus.textContent = 'Template and id are required';
        createAgentStatus.className = 'agent-form-status error';
        return;
    }

    createAgentButton.disabled = true;
    createAgentStatus.textContent = 'Creating...';
    createAgentStatus.className = 'agent-form-status';
    try {
        const response = await apiFetch(`${API_BASE}/agents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                template_id: templateId,
                id,
                name: name || undefined,
            }),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        newAgentIdInput.value = '';
        newAgentNameInput.value = '';
        createAgentStatus.textContent = 'Created';
        createAgentStatus.className = 'agent-form-status success';
        loadAgents();
    } catch (error) {
        createAgentStatus.textContent = `Error: ${error.message}`;
        createAgentStatus.className = 'agent-form-status error';
    } finally {
        createAgentButton.disabled = false;
    }
}

createAgentButton.addEventListener('click', createAgent);

async function deleteAgent(agentId) {
    if (!confirm(`Delete agent "${agentId}"? This cannot be undone.`)) return;
    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (currentAgentId === agentId) {
            chatAgentSelect.value = KIT_AGENT_ID;
            await switchChatAgent(KIT_AGENT_ID);
        }
        loadAgents();
    } catch (error) {
        alert(`Failed to delete agent: ${error.message}`);
    }
}

async function saveAgentEdit(agentId) {
    const card = agentsList.querySelector(`.agent-card[data-agent-id="${agentId}"]`);
    const statusEl = card.querySelector(`[data-edit-status="${agentId}"]`);
    const payload = {
        name: card.querySelector('.agent-edit-name').value.trim(),
        description: card.querySelector('.agent-edit-description').value.trim(),
        tools: parseToolOrSkillInput(card.querySelector('.agent-edit-tools').value),
        skills: parseToolOrSkillInput(card.querySelector('.agent-edit-skills').value),
        soul: card.querySelector('.agent-edit-soul').value,
    };

    statusEl.textContent = 'Saving...';
    statusEl.className = 'agent-form-status';
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
        statusEl.textContent = 'Saved';
        statusEl.className = 'agent-form-status success';
        loadAgents();
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'agent-form-status error';
    }
}

// --- Tools tab: dynamic view of the global tool registry ---

async function loadTools() {
    toolsList.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';
    try {
        const response = await apiFetch(`${API_BASE}/tools`);
        if (!response.ok) throw new Error('Failed to load tools');
        const tools = await response.json();
        toolsList.innerHTML = tools.map(tool => `
            <div class="pf-v5-c-description-list__group">
                <dt class="pf-v5-c-description-list__term">
                    <span class="pf-v5-c-description-list__text">${tool.name}</span>
                </dt>
                <dd class="pf-v5-c-description-list__description">
                    <div class="pf-v5-c-description-list__text">${tool.description || ''}</div>
                </dd>
            </div>
        `).join('');
    } catch (error) {
        toolsList.innerHTML = `<p class="empty-state">Error loading tools: ${error.message}</p>`;
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
                    <span>${entry.agent_id} is now ${entry.status}${task}</span>
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
                <span>${label}</span>
                <span class="activity-item-time">${time}</span>
            </div>
            <div class="activity-item-detail">${body}</div>
        </div>
    `;
}

async function loadActivity() {
    refreshActivityBtn.disabled = true;
    activityList.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';
    try {
        const response = await apiFetch(`${API_BASE}/agents/activity`);
        if (!response.ok) throw new Error('Failed to load activity');
        const entries = await response.json();
        activityList.innerHTML = entries.length === 0
            ? '<p class="empty-state">No agent activity yet</p>'
            : entries.slice().reverse().map(formatActivityEntry).join('');
    } catch (error) {
        activityList.innerHTML = `<p class="empty-state">Error: ${error.message}</p>`;
    } finally {
        refreshActivityBtn.disabled = false;
    }
}

refreshActivityBtn.addEventListener('click', loadActivity);

// Memory search
async function searchMemory() {
    const query = memorySearchInput.value.trim();
    if (!query) return;

    memorySearchBtn.disabled = true;
    memorySearchBtn.textContent = 'Searching...';
    memoryResults.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';

    try {
        const response = await apiFetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                platform: PLATFORM,
                user_id: USER_ID,
                message: `Use memory_search to find: ${query}`
            })
        });

        if (!response.ok) throw new Error('Search failed');

        const data = await response.json();

        // Parse search results from response
        const results = data.response;

        memoryResults.innerHTML = `
            <div class="memory-item">
                <div class="memory-item-type">Search Results</div>
                <div class="memory-item-content">${results}</div>
            </div>
        `;

    } catch (error) {
        memoryResults.innerHTML = `<p class="empty-state">Error: ${error.message}</p>`;
    } finally {
        memorySearchBtn.disabled = false;
        memorySearchBtn.textContent = 'Search';
    }
}

memorySearchBtn.addEventListener('click', searchMemory);
memorySearchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        e.preventDefault();
        searchMemory();
    }
});

// Load schedules
async function loadSchedules() {
    refreshSchedulesBtn.disabled = true;
    refreshSchedulesBtn.textContent = 'Loading...';
    schedulesList.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';

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
                <pre style="white-space: pre-wrap; font-size: 13px; color: #94a3b8;">${data.response}</pre>
            </div>
        `;

    } catch (error) {
        schedulesList.innerHTML = `<p class="empty-state">Error: ${error.message}</p>`;
    } finally {
        refreshSchedulesBtn.disabled = false;
        refreshSchedulesBtn.textContent = 'Refresh';
    }
}

refreshSchedulesBtn.addEventListener('click', loadSchedules);

// Load skills
async function loadSkills() {
    refreshSkillsBtn.disabled = true;
    refreshSkillsBtn.textContent = 'Loading...';
    skillsList.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';

    try {
        const response = await apiFetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                platform: PLATFORM,
                user_id: USER_ID,
                message: 'Use skill_list to show all skills'
            })
        });

        if (!response.ok) throw new Error('Failed to load skills');

        const data = await response.json();

        // Display raw response (skills formatted by tool)
        skillsList.innerHTML = `
            <div class="pf-v5-c-card" style="background: var(--pf-v5-global--BackgroundColor--200);">
                <div class="pf-v5-c-card__body">
                    <pre style="white-space: pre-wrap; font-size: 13px;">${data.response}</pre>
                </div>
            </div>
        `;

    } catch (error) {
        skillsList.innerHTML = `<p class="empty-state">Error: ${error.message}</p>`;
    } finally {
        refreshSkillsBtn.disabled = false;
        refreshSkillsBtn.textContent = 'Refresh';
    }
}

refreshSkillsBtn.addEventListener('click', loadSkills);

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
        if (data.agent_id === currentAgentId) {
            updateStatusDot(chatTargetStatusDot, data.agent_id);
        }
        const dot = agentsList.querySelector(`[data-status-dot="${data.agent_id}"]`);
        if (dot) {
            updateStatusDot(dot, data.agent_id);
            const taskEl = agentsList.querySelector(`[data-task-for="${data.agent_id}"]`);
            if (taskEl) taskEl.textContent = data.current_task || '';
        }
    }
    const activityTab = document.getElementById('activity-tab');
    if (activityTab && activityTab.classList.contains('pf-m-current')) {
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
                streamingMessageDiv = document.createElement('div');
                streamingMessageDiv.className = 'message assistant streaming';
                streamingContentDiv = document.createElement('div');
                streamingContentDiv.className = 'message-content';
                streamingMessageDiv.appendChild(streamingContentDiv);
                const timeDiv = document.createElement('div');
                timeDiv.className = 'message-time';
                timeDiv.textContent = new Date().toLocaleTimeString();
                streamingMessageDiv.appendChild(timeDiv);
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
                toolDiv.innerHTML =
                    `<span class="tool-call-name">▶ ${data.tool_name}</span>`;
                streamingMessageDiv.insertBefore(
                    toolDiv,
                    streamingMessageDiv.querySelector('.message-time')
                );
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

// Load persona (SOUL.md)
async function loadPersona() {
    personaEditor.disabled = true;
    personaStatus.textContent = '';
    try {
        const response = await apiFetch(`${API_BASE}/persona`);
        if (!response.ok) throw new Error('Failed to load persona');
        const data = await response.json();
        personaEditor.value = data.content;
    } catch (error) {
        personaStatus.textContent = `Error: ${error.message}`;
    } finally {
        personaEditor.disabled = false;
    }
}

// Save persona (SOUL.md)
async function savePersona() {
    savePersonaBtn.disabled = true;
    savePersonaBtn.textContent = 'Saving...';
    personaStatus.textContent = '';
    try {
        const response = await apiFetch(`${API_BASE}/persona`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: personaEditor.value }),
        });
        if (!response.ok) throw new Error('Failed to save');
        personaStatus.textContent = 'Saved';
        setTimeout(() => { personaStatus.textContent = ''; }, 3000);
    } catch (error) {
        personaStatus.textContent = `Error: ${error.message}`;
    } finally {
        savePersonaBtn.disabled = false;
        savePersonaBtn.textContent = 'Save';
    }
}

savePersonaBtn.addEventListener('click', savePersona);

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
        sessionIdSpan.textContent = currentSessionId();
        const [agentsRes, statusRes] = await Promise.all([
            apiFetch(`${API_BASE}/agents`).catch(() => null),
            apiFetch(`${API_BASE}/agents/status`).catch(() => null),
        ]);
        if (agentsRes && agentsRes.ok) {
            populateAgentSelect(await agentsRes.json());
        }
        if (statusRes && statusRes.ok) {
            agentStatuses = await statusRes.json();
            updateStatusDot(chatTargetStatusDot, currentAgentId);
        }
        loadAgentTemplatesForNewAgentForm();
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

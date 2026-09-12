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
const AVATAR_PALETTE = [
    '#0066cc', '#ca6c0f', '#5e40be', '#3e8635', '#c9190b', '#009596',
    '#b98412', '#876fd4', '#a60000', '#4394e5', '#63993d', '#ec7a08',
    '#7d1007', '#a18fff', '#f4c145', '#2b9af3',
];

// Which team member the chat panel is currently talking to. Mirrors the
// server's make_session_id: Kit keeps the original {platform}:{user_id}
// session id, any other agent gets its own {platform}:{user_id}:{agent_id}
// thread, so switching this switches to a separately-remembered conversation.
let currentAgentId = KIT_AGENT_ID;
let currentMode = 'dm'; // 'dm' | 'broadcast'

// ---- URL state ----

function pushChatUrl() {
    const slug = currentMode === 'broadcast' ? 'team' : currentAgentId;
    const path = `/chat/${encodeURIComponent(slug)}`;
    if (window.location.pathname !== path) {
        history.pushState({ agent: currentAgentId, mode: currentMode }, '', path);
    }
}

function parseChatUrl() {
    const match = window.location.pathname.match(/^\/chat\/(.+)$/);
    if (!match) return null;
    const slug = decodeURIComponent(match[1]);
    if (slug === 'team') return { agent: KIT_AGENT_ID, mode: 'broadcast' };
    return { agent: slug, mode: 'dm' };
}

window.addEventListener('popstate', async (e) => {
    const state = e.state || parseChatUrl();
    if (!state) return;
    if (state.mode === 'broadcast') {
        await selectBroadcastChannel(true);
    } else {
        await selectMember(state.agent, true);
    }
});

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

// Client-side inactivity timeout: if no streaming events arrive within this
// window, assume the server-side operation is lost and re-enable the UI.
const STREAM_INACTIVITY_TIMEOUT_MS = 120_000; // 2 minutes
let streamTimeoutId = null;

function startStreamTimeout() {
    clearStreamTimeout();
    streamTimeoutId = setTimeout(() => {
        if (chatInput.disabled) {
            removeThinkingIndicator();
            addMessage('Response timed out — no activity for 2 minutes. You can try sending your message again.', 'system');
            finishStreaming();
        }
    }, STREAM_INACTIVITY_TIMEOUT_MS);
}

function resetStreamTimeout() {
    if (streamTimeoutId !== null) startStreamTimeout();
}

function clearStreamTimeout() {
    if (streamTimeoutId !== null) {
        clearTimeout(streamTimeoutId);
        streamTimeoutId = null;
    }
}

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

const knowledgeOverlay = document.getElementById('knowledge-overlay');
const knowledgeList = document.getElementById('knowledge-list');
const knowledgeModalAgentName = document.getElementById('knowledge-modal-agent-name');
const closeKnowledgeBtn = document.getElementById('close-knowledge-btn');
let knowledgeAgentId = null;

// Latest known status per agent_id ({status, current_task, updated_at}),
// seeded from GET /agents/status and kept live via `agent_status` WS events.
let agentStatuses = {};

// Full roster, refreshed via loadAgents() - powers the member sidebar and
// the details panel.
let agentsCache = [];
let agentsById = {};
let templatesById = {};

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

if (typeof marked !== 'undefined') {
    marked.use({
        breaks: true,
        gfm: true,
        renderer: {
            code(token) {
                const text = (typeof token === 'object' ? token.text : token) || '';
                const lang = (typeof token === 'object' ? token.lang : arguments[1]) || '';
                let highlighted;
                if (typeof hljs !== 'undefined') {
                    try {
                        if (lang && hljs.getLanguage(lang)) {
                            highlighted = hljs.highlight(text, { language: lang }).value;
                        } else {
                            highlighted = hljs.highlightAuto(text).value;
                        }
                    } catch (e) {
                        highlighted = escapeHtml(text);
                    }
                } else {
                    highlighted = escapeHtml(text);
                }
                const langLabel = lang ? `<span class="code-lang-label">${escapeHtml(lang)}</span>` : '';
                return `<div class="code-block-wrapper">${langLabel}<pre><code class="hljs">${highlighted}</code></pre></div>`;
            }
        }
    });
}

if (typeof DOMPurify !== 'undefined') {
    DOMPurify.addHook('afterSanitizeAttributes', function(node) {
        if (node.tagName === 'A') {
            node.setAttribute('target', '_blank');
            node.setAttribute('rel', 'noopener noreferrer');
        }
    });
}

function renderMarkdown(text) {
    if (!text) return '';
    if (typeof marked === 'undefined') return escapeHtml(text);
    let html = marked.parse(text);
    if (typeof DOMPurify !== 'undefined') {
        html = DOMPurify.sanitize(html);
    }
    return html;
}

function initialsFor(name) {
    if (!name) return '?';
    const parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
}

function colorForId(id) {
    let hash = 5381;
    for (let i = 0; i < id.length; i++) hash = ((hash << 5) + hash + id.charCodeAt(i)) >>> 0;
    return AVATAR_PALETTE[hash % AVATAR_PALETTE.length];
}

function avatarColorFor(agentId) {
    if (agentId === KIT_AGENT_ID) return KIT_AVATAR_COLOR;
    const agent = agentsById[agentId];
    if (agent && agent.color) return agent.color;
    return colorForId(agentId);
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
    if (agentId === KIT_AGENT_ID) return 'Manager';
    const agent = agentsById[agentId];
    if (!agent || !agent.template_id) return '';
    const tpl = templatesById[agent.template_id];
    return tpl ? tpl.name : '';
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
function addMessage(content, role = 'user', id = null, sender = null, agentId = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}${sender ? ' delegated' : ''}`;
    if (id) messageDiv.id = id;

    if (!messageDiv.dataset.messageId) {
        messageDiv.dataset.messageId = crypto.randomUUID();
    }

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
        const effectiveId = agentId || currentAgentId;
        const agent = agentsById[effectiveId];
        const name = agent ? agent.name : (effectiveId === KIT_AGENT_ID ? 'Kit' : effectiveId);
        avatar.textContent = initialsFor(name);
        avatar.style.background = avatarColorFor(effectiveId);
        authorSpan.textContent = name;
    }

    headerLine.appendChild(authorSpan);
    headerLine.appendChild(timeSpan);

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    if (role === 'assistant') {
        contentDiv.classList.add('markdown');
        contentDiv.innerHTML = renderMarkdown(content);
    } else {
        contentDiv.textContent = content;
    }

    body.appendChild(headerLine);
    body.appendChild(contentDiv);

    const reactionsDiv = document.createElement('div');
    reactionsDiv.className = 'message-reactions';

    const addReactionBtn = document.createElement('button');
    addReactionBtn.className = 'add-reaction-btn';
    addReactionBtn.textContent = '😀';
    addReactionBtn.title = 'Add reaction';
    addReactionBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        showEmojiPicker(messageDiv, addReactionBtn);
    });
    reactionsDiv.appendChild(addReactionBtn);

    body.appendChild(reactionsDiv);

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(body);

    chatMessages.appendChild(messageDiv);
    scrollChatToBottom();

    return messageDiv;
}

const EMOJI_PALETTE = [
    '👍', '👎', '❤️', '😂', '🎉', '🤔',
    '👀', '🙌', '🔥', '💯', '✅', '❌',
    '👏', '😍', '🚀', '💡', '⭐', '🙏',
];

let activeEmojiPicker = null;

function showEmojiPicker(messageDiv, anchorBtn) {
    if (activeEmojiPicker) {
        activeEmojiPicker.remove();
        if (activeEmojiPicker.dataset.forMessage === messageDiv.dataset.messageId) {
            activeEmojiPicker = null;
            return;
        }
        activeEmojiPicker = null;
    }

    const picker = document.createElement('div');
    picker.className = 'emoji-picker';
    picker.dataset.forMessage = messageDiv.dataset.messageId;

    EMOJI_PALETTE.forEach(emoji => {
        const btn = document.createElement('button');
        btn.className = 'emoji-picker-item';
        btn.textContent = emoji;
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleReaction(messageDiv, emoji);
            picker.remove();
            activeEmojiPicker = null;
        });
        picker.appendChild(btn);
    });

    anchorBtn.parentElement.appendChild(picker);
    activeEmojiPicker = picker;
}

document.addEventListener('click', () => {
    if (activeEmojiPicker) {
        activeEmojiPicker.remove();
        activeEmojiPicker = null;
    }
});

function toggleReaction(messageDiv, emoji) {
    const messageId = messageDiv.dataset.messageId;
    if (!messageId) return;

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
            type: 'add_reaction',
            message_id: messageId,
            session_id: currentSessionId(),
            emoji: emoji,
            user_id: USER_ID,
        }));
    }

    applyReactionToMessage(messageDiv, emoji, USER_ID);
}

function applyReactionToMessage(messageDiv, emoji, userId) {
    const reactionsDiv = messageDiv.querySelector('.message-reactions');
    if (!reactionsDiv) return;

    let chip = reactionsDiv.querySelector(`.reaction-chip[data-emoji="${CSS.escape(emoji)}"]`);

    if (chip) {
        const users = JSON.parse(chip.dataset.users || '[]');
        const idx = users.indexOf(userId);
        if (idx !== -1) {
            users.splice(idx, 1);
            if (users.length === 0) {
                chip.remove();
                return;
            }
        } else {
            users.push(userId);
        }
        chip.dataset.users = JSON.stringify(users);
        chip.querySelector('.reaction-count').textContent = users.length > 1 ? users.length : '';
        chip.classList.toggle('reaction-mine', users.includes(USER_ID));
    } else {
        chip = document.createElement('span');
        chip.className = 'reaction-chip user-reaction';
        chip.dataset.emoji = emoji;
        chip.dataset.users = JSON.stringify([userId]);
        chip.classList.add('reaction-mine');
        chip.innerHTML = `<span class="reaction-emoji">${emoji}</span><span class="reaction-count"></span>`;
        chip.style.animation = 'reaction-pop 0.35s ease-out forwards';
        chip.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleReaction(messageDiv, emoji);
        });
        const addBtn = reactionsDiv.querySelector('.add-reaction-btn');
        reactionsDiv.insertBefore(chip, addBtn);
    }
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

    const msgDiv = addMessage(message, 'user');
    const userMessageId = msgDiv.dataset.messageId;
    chatInput.value = '';
    chatInput.style.height = 'auto';

    if (currentMode === 'broadcast') {
        const messageId = crypto.randomUUID();
        msgDiv.dataset.messageId = messageId;

        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({
                type: 'broadcast',
                platform: PLATFORM,
                user_id: USER_ID,
                message: message,
                message_id: messageId,
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
        startStreamTimeout();
        ws.send(JSON.stringify({
            type: 'chat_message',
            platform: PLATFORM,
            user_id: USER_ID,
            agent_id: currentAgentId,
            message: message,
            message_id: userMessageId,
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
async function selectMember(agentId, skipPush = false) {
    currentMode = 'dm';
    currentAgentId = agentId;
    if (!skipPush) pushChatUrl();
    resetStreamingState();
    removeThinkingIndicator();
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

async function selectBroadcastChannel(skipPush = false) {
    currentMode = 'broadcast';
    if (!skipPush) pushChatUrl();
    resetStreamingState();
    removeThinkingIndicator();
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
    clearStreamTimeout();
    streamingMessageDiv = null;
    streamingContentDiv = null;
    streamingText = '';
    chatInput.disabled = false;
    sendButton.disabled = false;
    chatInput.focus();
}

function resetStreamingState() {
    clearStreamTimeout();
    streamingMessageDiv = null;
    streamingContentDiv = null;
    streamingText = '';
    chatInput.disabled = false;
    sendButton.disabled = false;
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
        <button class="member-row" data-agent-id="${escapeAttr(agent.id)}" draggable="true">
            <div class="avatar" style="background:${bg}">${initialsFor(agent.name)}</div>
            <div class="member-row-text">
                <div class="member-row-name">${escapeHtml(agent.name)}${role ? `<span class="member-row-role">${escapeHtml(role)}</span>` : ''}</div>
                <div class="member-row-task" data-task-for="${escapeAttr(agent.id)}"></div>
            </div>
            <div class="member-row-dot" data-status-dot="${escapeAttr(agent.id)}"></div>
        </button>
    `;
}

const DM_ORDER_KEY = 'kit-dm-order';

function getSavedDmOrder() {
    try {
        const raw = localStorage.getItem(DM_ORDER_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch { return null; }
}

function saveDmOrder(ids) {
    localStorage.setItem(DM_ORDER_KEY, JSON.stringify(ids));
}

function getOrderedDmAgents() {
    const all = [...agentsCache];
    const saved = getSavedDmOrder();
    if (!saved) {
        const kit = all.find(a => a.id === KIT_AGENT_ID);
        const others = all.filter(a => a.id !== KIT_AGENT_ID).sort((a, b) => a.name.localeCompare(b.name));
        return kit ? [kit, ...others] : others;
    }
    const byId = {};
    all.forEach(a => { byId[a.id] = a; });
    const ordered = [];
    saved.forEach(id => {
        if (byId[id]) {
            ordered.push(byId[id]);
            delete byId[id];
        }
    });
    Object.values(byId).sort((a, b) => a.name.localeCompare(b.name)).forEach(a => ordered.push(a));
    return ordered;
}

function initDmDragAndDrop() {
    const dmList = memberList.querySelector('.dm-list');
    if (!dmList) return;
    let draggedEl = null;

    dmList.querySelectorAll('.member-row').forEach(row => {
        row.addEventListener('dragstart', (e) => {
            draggedEl = row;
            row.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', row.dataset.agentId);
        });

        row.addEventListener('dragend', () => {
            row.classList.remove('dragging');
            dmList.querySelectorAll('.member-row').forEach(r => r.classList.remove('drag-over-above', 'drag-over-below'));
            draggedEl = null;
        });

        row.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            if (row === draggedEl) return;
            dmList.querySelectorAll('.member-row').forEach(r => {
                if (r !== row) r.classList.remove('drag-over-above', 'drag-over-below');
            });
            const rect = row.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            row.classList.remove('drag-over-above', 'drag-over-below');
            row.classList.add(e.clientY < midY ? 'drag-over-above' : 'drag-over-below');
        });

        row.addEventListener('dragleave', () => {
            row.classList.remove('drag-over-above', 'drag-over-below');
        });

        row.addEventListener('drop', (e) => {
            e.preventDefault();
            if (row === draggedEl || !draggedEl) return;
            const rect = row.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            if (e.clientY < midY) {
                dmList.insertBefore(draggedEl, row);
            } else {
                dmList.insertBefore(draggedEl, row.nextSibling);
            }
            row.classList.remove('drag-over-above', 'drag-over-below');
            const ids = [...dmList.querySelectorAll('.member-row')].map(r => r.dataset.agentId);
            saveDmOrder(ids);
        });
    });
}

function renderMemberList() {
    if (!agentsCache.length) {
        memberList.innerHTML = '<div class="member-list-empty">No team members</div>';
        return;
    }
    const dmAgents = getOrderedDmAgents();

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
    html += '<div class="dm-list">';
    html += dmAgents.map(memberRowHtml).join('');
    html += '</div>';

    memberList.innerHTML = html;

    memberList.querySelectorAll('.member-row:not(.channel-row)').forEach(row => {
        row.addEventListener('click', () => selectMember(row.dataset.agentId));
    });
    memberList.querySelectorAll('.channel-row').forEach(row => {
        row.addEventListener('click', () => selectBroadcastChannel());
    });

    initDmDragAndDrop();
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

        try {
            const tplRes = await apiFetch(`${API_BASE}/agent-templates`);
            if (tplRes.ok) {
                const templates = await tplRes.json();
                templatesById = {};
                templates.forEach(t => { templatesById[t.id] = t; });
            }
        } catch (e) {
            // templates are optional for role display
        }
        if (statusRes.ok) {
            agentStatuses = await statusRes.json();
        }
        renderMemberList();
        updateChatHeaderForCurrentAgent();
        if (typeof updateDefaultTeamBtnVisibility === 'function') updateDefaultTeamBtnVisibility();
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

async function renderCheckboxGroup(containerId, endpoint, selected, filterType) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '<div class="spinner" style="margin:8px auto;"></div>';
    try {
        const response = await apiFetch(`${API_BASE}${endpoint}`);
        if (!response.ok) throw new Error('Failed to load');
        let items = await response.json();
        if (filterType) {
            items = items.filter(item => filterType === 'prompt' ? item.type === 'prompt' : item.type !== 'prompt');
        }
        container.dataset.allNames = JSON.stringify(items.map(i => i.name));
        if (items.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding:6px 0;">None available</div>';
            return;
        }
        const isWildcard = selected === '*';
        const selectedSet = isWildcard ? null : new Set(Array.isArray(selected) ? selected : []);

        let html = `<label class="cb-row cb-row-all">
            <input type="checkbox" class="cb-all" ${isWildcard ? 'checked' : ''}> <span>All access (<code>*</code>)</span>
        </label>`;
        html += '<div class="cb-items">';
        items.forEach(item => {
            const checked = isWildcard || (selectedSet && selectedSet.has(item.name));
            const typeBadge = !filterType && item.type ? `<span class="cb-type-badge cb-type-${item.type === 'prompt' ? 'skill' : 'tool'}">${item.type === 'prompt' ? 'skill' : 'custom tool'}</span>` : '';
            html += `<label class="cb-row" title="${escapeAttr(item.description || '')}">
                <input type="checkbox" value="${escapeAttr(item.name)}" ${checked ? 'checked' : ''} ${isWildcard ? 'disabled' : ''}>
                <span class="cb-name">${escapeHtml(item.name)}</span>${typeBadge}
            </label>`;
        });
        html += '</div>';
        container.innerHTML = html;

        const allCb = container.querySelector('.cb-all');
        const itemCbs = container.querySelectorAll('.cb-items input[type="checkbox"]');
        allCb.addEventListener('change', () => {
            itemCbs.forEach(cb => { cb.disabled = allCb.checked; cb.checked = allCb.checked; });
        });
    } catch (error) {
        container.innerHTML = `<div class="empty-state" style="padding:6px 0;">Error: ${escapeHtml(error.message)}</div>`;
    }
}

function readCombinedCheckboxGroups(...ids) {
    const results = ids.map(id => readCheckboxGroup(id));
    if (results.every(r => r === '*')) return '*';
    let combined = [];
    for (let i = 0; i < ids.length; i++) {
        const val = results[i];
        if (val === '*') {
            const container = document.getElementById(ids[i]);
            if (container && container.dataset.allNames) {
                combined.push(...JSON.parse(container.dataset.allNames));
            }
        } else if (Array.isArray(val)) {
            combined.push(...val);
        }
    }
    return combined;
}

function readCheckboxGroup(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return [];
    const allCb = container.querySelector('.cb-all');
    if (allCb && allCb.checked) return '*';
    return Array.from(container.querySelectorAll('.cb-items input[type="checkbox"]:checked')).map(cb => cb.value);
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
            <div class="details-section-label">Tools</div>
            <div id="details-tools-list" class="tools-list"><div class="spinner" style="margin:12px auto;"></div></div>
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">Custom Tools</div>
            <div id="details-custom-tools-list"><div class="spinner" style="margin:12px auto;"></div></div>
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
            <div class="details-section-label">Knowledge</div>
            <p style="font-size:13px;color:var(--text-secondary);margin:0 0 8px;">Curated facts and documents for this agent.</p>
            <button class="btn btn-secondary" id="details-knowledge-btn">Manage Knowledge</button>
        </div>
    `;

    html += `
        <div class="details-section">
            <div class="details-section-label">MCP Servers</div>
            <div id="details-mcp-list"></div>
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

    if (isKit) {
        html += `
            <div class="details-section" style="margin-top:24px;">
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

    html += `
        <div class="details-section" ${!isKit ? 'style="margin-top:24px;"' : ''}>
            <div class="details-section-label collapsible-toggle" id="activity-toggle" role="button" tabindex="0" aria-expanded="false">
                <span class="toggle-icon">&#9654;</span> Recent activity
            </div>
            <div id="details-activity-mini" class="activity-mini-list collapsed"><div class="spinner" style="margin:12px auto;"></div></div>
        </div>
    `;

    detailsBody.innerHTML = html;

    const activityToggle = document.getElementById('activity-toggle');
    const activityList = document.getElementById('details-activity-mini');
    if (activityToggle && activityList) {
        const toggle = () => {
            const expanded = activityToggle.getAttribute('aria-expanded') === 'true';
            activityToggle.setAttribute('aria-expanded', String(!expanded));
            activityToggle.querySelector('.toggle-icon').innerHTML = expanded ? '&#9654;' : '&#9660;';
            activityList.classList.toggle('collapsed', expanded);
        };
        activityToggle.addEventListener('click', toggle);
        activityToggle.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); } });
    }

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

    const detailsKnowledgeBtn = document.getElementById('details-knowledge-btn');
    if (detailsKnowledgeBtn) {
        detailsKnowledgeBtn.addEventListener('click', () => {
            openKnowledgeForAgent(agentId);
        });
    }

    renderMcpReadonly('details-mcp-list', agent.mcp_servers);
    loadDetailsTools(agentId);
    loadDetailsCustomTools(agentId);
    loadDetailsSkills(agentId);
    loadMiniActivity(agentId);
}

// --- MCP Servers editor helpers ---

function renderMcpEditor(containerId, servers) {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = '';
    if (servers && typeof servers === 'object') {
        for (const [name, config] of Object.entries(servers)) {
            addMcpEntry(container, name, config);
        }
    }
    const addBtn = document.createElement('button');
    addBtn.className = 'btn btn-secondary mcp-add-btn';
    addBtn.textContent = '+ Add MCP server';
    addBtn.addEventListener('click', () => {
        addMcpEntry(container, '', {}, addBtn);
    });
    container.appendChild(addBtn);
}

function addMcpEntry(container, name, config, beforeEl) {
    const envLines = config.env
        ? Object.entries(config.env).map(([k, v]) => `${k}=${v}`).join('\n')
        : '';
    const argsStr = (config.args || []).join(' ');
    const entry = document.createElement('div');
    entry.className = 'mcp-entry';
    entry.innerHTML = `
        <div class="mcp-entry-header">
            <input class="form-control mcp-name" type="text" placeholder="Server name (e.g. github)" value="${escapeAttr(name)}">
            <button class="mcp-entry-remove" title="Remove">&times;</button>
        </div>
        <div class="form-group">
            <label class="form-label">Command</label>
            <input class="form-control mcp-command" type="text" placeholder="e.g. npx, uvx, docker" value="${escapeAttr(config.command || '')}">
        </div>
        <div class="form-group">
            <label class="form-label">Arguments</label>
            <input class="form-control mcp-args" type="text" placeholder="e.g. -y @modelcontextprotocol/server-github" value="${escapeAttr(argsStr)}">
        </div>
        <div class="form-group">
            <label class="form-label">Environment variables</label>
            <textarea class="form-control mcp-env" rows="2" placeholder="KEY=value (one per line)">${escapeHtml(envLines)}</textarea>
        </div>
    `;
    entry.querySelector('.mcp-entry-remove').addEventListener('click', () => entry.remove());
    if (beforeEl) {
        container.insertBefore(entry, beforeEl);
    } else {
        container.appendChild(entry);
    }
}

function readMcpEditor(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return null;
    const entries = container.querySelectorAll('.mcp-entry');
    if (entries.length === 0) return {};
    const servers = {};
    for (const entry of entries) {
        const name = entry.querySelector('.mcp-name').value.trim();
        if (!name) continue;
        const command = entry.querySelector('.mcp-command').value.trim();
        const argsStr = entry.querySelector('.mcp-args').value.trim();
        const envText = entry.querySelector('.mcp-env').value.trim();
        const config = {};
        if (command) config.command = command;
        if (argsStr) config.args = argsStr.split(/\s+/);
        if (envText) {
            config.env = {};
            for (const line of envText.split('\n')) {
                const eq = line.indexOf('=');
                if (eq > 0) config.env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
            }
        }
        servers[name] = config;
    }
    return servers;
}

function renderMcpReadonly(containerId, servers) {
    const container = document.getElementById(containerId);
    if (!container) return;
    if (!servers || Object.keys(servers).length === 0) {
        container.innerHTML = '<span style="color:var(--text-tertiary);font-size:13px;">None configured</span>';
        return;
    }
    let html = '<div class="mcp-readonly-list">';
    for (const [name, config] of Object.entries(servers)) {
        const cmd = [config.command || '', ...(config.args || [])].join(' ');
        html += `<div class="mcp-readonly-item"><span class="mcp-readonly-name">${escapeHtml(name)}</span><span class="mcp-readonly-cmd">${escapeHtml(cmd)}</span></div>`;
    }
    html += '</div>';
    container.innerHTML = html;
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
            <label class="form-label">Avatar color</label>
            <div class="color-picker-row">
                <input type="color" id="edit-color" value="${agent.color || colorForId(agentId)}">
                <span class="color-picker-value" id="edit-color-label">${agent.color || 'auto'}</span>
                <button class="btn btn-link btn-sm" id="edit-color-reset" type="button" ${!agent.color ? 'disabled' : ''}>Reset to auto</button>
            </div>
        </div>
        <div class="form-group">
            <label class="form-label">Tools</label>
            <div id="edit-tools-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Custom Tools</label>
            <div id="edit-custom-tools-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Skills</label>
            <div id="edit-skills-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Provider (blank for default)</label>
            <select class="form-control" id="edit-provider">
                <option value="">(use default provider)</option>
            </select>
        </div>
        <div class="form-group">
            <label class="form-label">Model (blank for provider default)</label>
            <input class="form-control" id="edit-model" type="text" value="${escapeAttr(agent.model || '')}" placeholder="e.g. gpt-4o, qwen3:14b">
        </div>
        <div class="form-group">
            <label class="form-label">MCP Servers</label>
            <div id="edit-mcp-servers"></div>
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

    const colorInput = document.getElementById('edit-color');
    const colorLabel = document.getElementById('edit-color-label');
    const colorReset = document.getElementById('edit-color-reset');
    colorInput._custom = !!agent.color;
    colorInput.addEventListener('input', () => {
        colorInput._custom = true;
        colorLabel.textContent = colorInput.value;
        colorReset.disabled = false;
    });
    colorReset.addEventListener('click', () => {
        colorInput._custom = false;
        colorInput.value = colorForId(agentId);
        colorLabel.textContent = 'auto';
        colorReset.disabled = true;
    });

    renderCheckboxGroup('edit-tools-cb', '/tools', agent.tools);
    renderCheckboxGroup('edit-custom-tools-cb', '/skills', agent.skills, 'executable');
    renderCheckboxGroup('edit-skills-cb', '/skills', agent.skills, 'prompt');
    renderMcpEditor('edit-mcp-servers', agent.mcp_servers);
    populateProviderDropdown('edit-provider', agent.provider);
}

async function saveMemberEdit(agentId) {
    const statusEl = document.getElementById('edit-status');
    const colorInput = document.getElementById('edit-color');
    const payload = {
        name: document.getElementById('edit-name').value.trim(),
        description: document.getElementById('edit-description').value.trim(),
        tools: readCheckboxGroup('edit-tools-cb'),
        skills: readCombinedCheckboxGroups('edit-custom-tools-cb', 'edit-skills-cb'),
        model: document.getElementById('edit-model').value.trim() || null,
        provider: document.getElementById('edit-provider').value.trim() || null,
        soul: document.getElementById('edit-soul').value,
        mcp_servers: readMcpEditor('edit-mcp-servers'),
        color: colorInput._custom ? colorInput.value : '',
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
            <div class="template-links">
                <button class="template-create-link" id="edit-template-link"><i class="fas fa-pen"></i> Edit</button>
                <button class="template-create-link" id="create-template-link"><i class="fas fa-plus"></i> New</button>
            </div>
        </div>
        <div class="form-group">
            <label class="form-label">Id <span style="font-weight:normal;color:var(--pf-v5-global--Color--200)">(cannot be changed later)</span></label>
            <input class="form-control" id="new-agent-id" type="text" placeholder="e.g. tester-1">
        </div>
        <div class="form-group">
            <label class="form-label">Display name (optional)</label>
            <input class="form-control" id="new-agent-name" type="text" placeholder="e.g. Tester">
        </div>
        <div class="form-group">
            <label class="form-label">Avatar color (optional)</label>
            <div class="color-picker-row">
                <input type="color" id="new-agent-color" value="#5e40be">
                <span class="color-picker-value" id="new-agent-color-label">auto</span>
            </div>
        </div>
        <div class="details-actions">
            <button class="btn btn-primary" id="create-agent-button">Create teammate</button>
            <span class="form-status" id="create-agent-status"></span>
        </div>
    `;

    document.getElementById('details-cancel-new').addEventListener('click', () => renderMemberDetails(currentAgentId));
    document.getElementById('create-agent-button').addEventListener('click', createAgent);
    document.getElementById('create-template-link').addEventListener('click', renderCreateTemplateForm);
    document.getElementById('edit-template-link').addEventListener('click', () => {
        const sel = document.getElementById('new-agent-template');
        if (sel && sel.value) renderEditTemplateForm(sel.value);
    });

    const newColorInput = document.getElementById('new-agent-color');
    const newColorLabel = document.getElementById('new-agent-color-label');
    newColorInput._custom = false;
    newColorInput.addEventListener('input', () => {
        newColorInput._custom = true;
        newColorLabel.textContent = newColorInput.value;
    });

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
            body: JSON.stringify({
                template_id: templateId,
                id,
                name: name || undefined,
                color: document.getElementById('new-agent-color')._custom ? document.getElementById('new-agent-color').value : undefined,
            }),
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

function renderCreateTemplateForm() {
    detailsMode = 'new-template';
    detailsTitle.textContent = 'New template';

    detailsBody.innerHTML = `
        <button class="details-back-link" id="template-back-btn"><i class="fas fa-arrow-left"></i> Back to Add teammate</button>
        <div class="form-group">
            <label class="form-label">Id <span style="font-weight:normal;color:var(--pf-v5-global--Color--200)">(must be unique, cannot be changed later)</span></label>
            <input class="form-control" id="tpl-id" type="text" placeholder="e.g. data-analyst">
        </div>
        <div class="form-group">
            <label class="form-label">Name</label>
            <input class="form-control" id="tpl-name" type="text" placeholder="e.g. Data Analyst">
        </div>
        <div class="form-group">
            <label class="form-label">Description</label>
            <input class="form-control" id="tpl-description" type="text" placeholder="Short description of the role">
        </div>
        <div class="form-group">
            <label class="form-label">Tools</label>
            <div id="tpl-tools-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Custom Tools</label>
            <div id="tpl-custom-tools-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Skills</label>
            <div id="tpl-skills-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">MCP Servers</label>
            <div id="tpl-mcp-servers"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Soul (persona)</label>
            <textarea class="form-control" id="tpl-soul" rows="10" placeholder="# Role Name\n\nYou are a ... on Kit's team."></textarea>
        </div>
        <div class="details-actions">
            <button class="btn btn-primary" id="save-template-btn">Create template</button>
            <button class="btn btn-secondary" id="template-cancel-btn">Cancel</button>
            <span class="form-status" id="template-status"></span>
        </div>
    `;

    document.getElementById('template-back-btn').addEventListener('click', renderAddTeammateForm);
    document.getElementById('template-cancel-btn').addEventListener('click', renderAddTeammateForm);
    document.getElementById('save-template-btn').addEventListener('click', createTemplate);
    renderCheckboxGroup('tpl-tools-cb', '/tools', ['read', 'list_files', 'exec_shell', 'memory_search', 'memory_write', 'memory_get', 'knowledge_teach', 'knowledge_ingest', 'knowledge_ingest_url', 'knowledge_search', 'knowledge_list', 'knowledge_forget']);
    renderCheckboxGroup('tpl-custom-tools-cb', '/skills', '*', 'executable');
    renderCheckboxGroup('tpl-skills-cb', '/skills', '*', 'prompt');
    renderMcpEditor('tpl-mcp-servers', null);
}

async function createTemplate() {
    const statusEl = document.getElementById('template-status');
    const btn = document.getElementById('save-template-btn');

    const id = document.getElementById('tpl-id').value.trim();
    const name = document.getElementById('tpl-name').value.trim();
    if (!id) {
        statusEl.textContent = 'Id is required';
        statusEl.className = 'form-status error';
        return;
    }
    if (!name) {
        statusEl.textContent = 'Name is required';
        statusEl.className = 'form-status error';
        return;
    }

    const mcpServers = readMcpEditor('tpl-mcp-servers');
    const template = {
        id,
        name,
        description: document.getElementById('tpl-description').value.trim(),
        tools: readCheckboxGroup('tpl-tools-cb'),
        skills: readCombinedCheckboxGroups('tpl-custom-tools-cb', 'tpl-skills-cb'),
        soul: document.getElementById('tpl-soul').value,
    };
    template.mcp_servers = mcpServers;

    btn.disabled = true;
    statusEl.textContent = 'Creating...';
    statusEl.className = 'form-status';
    try {
        const response = await apiFetch(`${API_BASE}/agent-templates`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(template),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        statusEl.textContent = 'Created';
        statusEl.className = 'form-status success';
        setTimeout(renderAddTeammateForm, 600);
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'form-status error';
    } finally {
        btn.disabled = false;
    }
}

async function renderEditTemplateForm(templateId) {
    detailsMode = 'edit-template';
    detailsTitle.textContent = 'Edit template';
    detailsBody.innerHTML = '<div class="spinner"></div>';

    let tpl;
    try {
        const response = await apiFetch(`${API_BASE}/agent-templates`);
        if (!response.ok) throw new Error('Failed to load templates');
        const templates = await response.json();
        tpl = templates.find(t => t.id === templateId);
        if (!tpl) throw new Error(`Template "${templateId}" not found`);
    } catch (error) {
        detailsBody.innerHTML = `<p class="form-status error">${escapeHtml(error.message)}</p>`;
        return;
    }

    detailsBody.innerHTML = `
        <button class="details-back-link" id="template-back-btn"><i class="fas fa-arrow-left"></i> Back to Add teammate</button>
        <div class="form-group">
            <label class="form-label">Id</label>
            <input class="form-control" id="tpl-id" type="text" value="${escapeAttr(tpl.id)}" disabled>
        </div>
        <div class="form-group">
            <label class="form-label">Name</label>
            <input class="form-control" id="tpl-name" type="text" value="${escapeAttr(tpl.name || '')}">
        </div>
        <div class="form-group">
            <label class="form-label">Description</label>
            <input class="form-control" id="tpl-description" type="text" value="${escapeAttr(tpl.description || '')}">
        </div>
        <div class="form-group">
            <label class="form-label">Tools</label>
            <div id="tpl-tools-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Custom Tools</label>
            <div id="tpl-custom-tools-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Skills</label>
            <div id="tpl-skills-cb" class="cb-group"></div>
        </div>
        <div class="form-group">
            <label class="form-label">MCP Servers</label>
            <div id="tpl-mcp-servers"></div>
        </div>
        <div class="form-group">
            <label class="form-label">Soul (persona)</label>
            <textarea class="form-control" id="tpl-soul" rows="10">${escapeHtml(tpl.soul || '')}</textarea>
        </div>
        <div class="details-actions">
            <button class="btn btn-primary" id="save-template-btn">Save template</button>
            <button class="btn btn-danger" id="delete-template-btn">Delete template</button>
            <button class="btn btn-secondary" id="template-cancel-btn">Cancel</button>
            <span class="form-status" id="template-status"></span>
        </div>
    `;

    document.getElementById('template-back-btn').addEventListener('click', renderAddTeammateForm);
    document.getElementById('template-cancel-btn').addEventListener('click', renderAddTeammateForm);
    document.getElementById('save-template-btn').addEventListener('click', () => saveTemplate(tpl.id));
    document.getElementById('delete-template-btn').addEventListener('click', () => deleteTemplate(tpl.id));
    renderCheckboxGroup('tpl-tools-cb', '/tools', tpl.tools || []);
    renderCheckboxGroup('tpl-custom-tools-cb', '/skills', tpl.skills || [], 'executable');
    renderCheckboxGroup('tpl-skills-cb', '/skills', tpl.skills || [], 'prompt');
    renderMcpEditor('tpl-mcp-servers', tpl.mcp_servers);
}

async function saveTemplate(templateId) {
    const statusEl = document.getElementById('template-status');
    const btn = document.getElementById('save-template-btn');
    const name = document.getElementById('tpl-name').value.trim();
    if (!name) {
        statusEl.textContent = 'Name is required';
        statusEl.className = 'form-status error';
        return;
    }

    const mcpServers = readMcpEditor('tpl-mcp-servers');
    const template = {
        id: templateId,
        name,
        description: document.getElementById('tpl-description').value.trim(),
        tools: readCheckboxGroup('tpl-tools-cb'),
        skills: readCombinedCheckboxGroups('tpl-custom-tools-cb', 'tpl-skills-cb'),
        soul: document.getElementById('tpl-soul').value,
    };
    template.mcp_servers = mcpServers;

    btn.disabled = true;
    statusEl.textContent = 'Saving...';
    statusEl.className = 'form-status';
    try {
        const response = await apiFetch(`${API_BASE}/agent-templates`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(template),
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        statusEl.textContent = 'Saved';
        statusEl.className = 'form-status success';
        setTimeout(renderAddTeammateForm, 600);
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'form-status error';
    } finally {
        btn.disabled = false;
    }
}

async function deleteTemplate(templateId) {
    if (!confirm(`Delete template "${templateId}"? This cannot be undone.`)) return;
    const statusEl = document.getElementById('template-status');
    try {
        const response = await apiFetch(`${API_BASE}/agent-templates/${encodeURIComponent(templateId)}`, {
            method: 'DELETE',
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        statusEl.textContent = 'Deleted';
        statusEl.className = 'form-status success';
        setTimeout(renderAddTeammateForm, 600);
    } catch (error) {
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'form-status error';
    }
}

addTeammateBtn.addEventListener('click', () => {
    showDetailsPanel();
    renderAddTeammateForm();
    closeMobileSidebar();
});

const defaultTeamBtn = document.getElementById('default-team-btn');
defaultTeamBtn.addEventListener('click', async () => {
    defaultTeamBtn.disabled = true;
    const origText = defaultTeamBtn.innerHTML;
    defaultTeamBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Creating...';
    try {
        const response = await apiFetch(`${API_BASE}/agents/create-default-team`, {
            method: 'POST',
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        const result = await response.json();
        await loadAgents();
        const count = result.created.length;
        if (count > 0) {
            defaultTeamBtn.innerHTML = `<i class="fas fa-check"></i> Created ${count} agent${count > 1 ? 's' : ''}`;
        } else {
            defaultTeamBtn.innerHTML = '<i class="fas fa-check"></i> Team already exists';
        }
        setTimeout(() => { updateDefaultTeamBtnVisibility(); }, 2000);
    } catch (error) {
        defaultTeamBtn.innerHTML = `<i class="fas fa-times"></i> ${error.message}`;
        setTimeout(() => { defaultTeamBtn.innerHTML = origText; defaultTeamBtn.disabled = false; }, 3000);
    }
});

function updateDefaultTeamBtnVisibility() {
    const defaultTemplates = ['researcher', 'developer', 'tester', 'security'];
    const usedTemplates = new Set(agentsCache.map(a => a.template_id).filter(Boolean));
    const allCovered = defaultTemplates.every(t => usedTemplates.has(t));
    defaultTeamBtn.hidden = allCovered;
    defaultTeamBtn.disabled = false;
    defaultTeamBtn.innerHTML = '<i class="fas fa-users"></i> Create default team';
}

// --- Workspace overlay: Team Activity / Schedules ---
// Workspace-wide sections that aren't tied to any one team member.

const WORKSPACE_LOADERS = {
    activity: loadActivity,
    schedules: loadSchedules,
    'custom-tools': loadCustomToolsLibrary,
    skills: loadSkillsLibrary,
    providers: loadProviders,
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

async function loadDetailsCustomTools(agentId) {
    const container = document.getElementById('details-custom-tools-list');
    if (!container) return;
    container.innerHTML = '<div class="spinner" style="margin:12px auto;"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/skills`);
        if (!response.ok) throw new Error('Failed to load custom tools');
        const allSkills = await response.json();
        const agent = agentsById[agentId];
        const allowed = agent ? agent.skills : '*';
        const customTools = (allowed === '*' ? allSkills : allSkills.filter(s => allowed.includes(s.name)))
            .filter(s => s.type !== 'prompt');
        if (customTools.length === 0) {
            container.innerHTML = '<div class="empty-state" style="padding:10px 0;">No custom tools</div>';
            return;
        }
        container.innerHTML = customTools.map(skill => `
            <div class="tool-entry">
                <div class="tool-entry-name">${escapeHtml(skill.name)}</div>
                <div class="tool-entry-desc">${escapeHtml(skill.description || '')}</div>
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
        const skills = (allowed === '*' ? allSkills : allSkills.filter(s => allowed.includes(s.name)))
            .filter(s => s.type === 'prompt');
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

// Load schedules
async function loadSchedules() {
    refreshSchedulesBtn.disabled = true;
    refreshSchedulesBtn.textContent = 'Loading...';
    schedulesList.innerHTML = '<div class="spinner"></div>';

    try {
        const response = await apiFetch(`${API_BASE}/schedules`);

        if (!response.ok) throw new Error('Failed to load schedules');

        const schedules = await response.json();

        if (!schedules.length) {
            schedulesList.innerHTML = `<p class="empty-state">No schedules found. Ask Kit to create a schedule (e.g. "schedule a daily reminder at 9am").</p>`;
            return;
        }

        schedulesList.innerHTML = schedules.map(s => {
            const status = s.enabled ? '✓ Enabled' : '✗ Disabled';
            return `
                <div class="schedule-card">
                    <pre style="white-space: pre-wrap; font-size: 13px; margin: 0; color: var(--text-secondary);">ID: ${escapeHtml(s.id)} (${escapeHtml(status)})
  Cron: ${escapeHtml(s.cron)}
  Task: ${escapeHtml(s.task)}
  Description: ${escapeHtml(s.description || 'N/A')}
  Runs: ${s.run_count || 0}
  Last run: ${escapeHtml(s.last_run || 'Never')}</pre>
                </div>`;
        }).join('\n');

    } catch (error) {
        schedulesList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        refreshSchedulesBtn.disabled = false;
        refreshSchedulesBtn.textContent = 'Refresh';
    }
}

refreshSchedulesBtn.addEventListener('click', loadSchedules);


// --- Knowledge modal ---

function openKnowledgeForAgent(agentId) {
    knowledgeAgentId = agentId;
    const agent = agentsById[agentId];
    const label = agent ? `${agent.name} — Knowledge` : `${agentId} — Knowledge`;
    if (knowledgeModalAgentName) knowledgeModalAgentName.textContent = label;
    knowledgeOverlay.hidden = false;
    loadKnowledge();
}

function closeKnowledgeModal() {
    knowledgeOverlay.hidden = true;
    knowledgeAgentId = null;
}

async function loadKnowledge() {
    if (!knowledgeAgentId) return;
    knowledgeList.innerHTML = '<div class="spinner"></div>';

    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(knowledgeAgentId)}/knowledge`);
        if (!response.ok) throw new Error('Failed to load knowledge sources');
        const data = await response.json();

        if (!data.sources || data.sources.startsWith('No knowledge sources')) {
            knowledgeList.innerHTML = '<p class="empty-state">No knowledge sources yet</p>';
            return;
        }

        knowledgeList.innerHTML = `<div class="knowledge-card"><pre style="white-space:pre-wrap;font-size:13px;margin:0;color:var(--text-secondary);">${escapeHtml(data.sources)}</pre></div>`;
    } catch (error) {
        knowledgeList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    }
}

async function addKnowledgeFact() {
    const input = document.getElementById('knowledge-fact-input');
    const btn = document.getElementById('knowledge-fact-btn');
    const content = input.value.trim();
    if (!content || !knowledgeAgentId) return;

    btn.disabled = true;
    btn.textContent = 'Adding...';
    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(knowledgeAgentId)}/knowledge/facts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        if (!response.ok) throw new Error('Failed to add fact');
        input.value = '';
        await loadKnowledge();
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Add Fact';
    }
}

async function ingestKnowledgeDoc() {
    const nameInput = document.getElementById('knowledge-doc-name');
    const textInput = document.getElementById('knowledge-doc-text');
    const btn = document.getElementById('knowledge-doc-btn');
    const sourceName = nameInput.value.trim();
    const text = textInput.value.trim();
    if (!sourceName || !text || !knowledgeAgentId) return;

    btn.disabled = true;
    btn.textContent = 'Ingesting...';
    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(knowledgeAgentId)}/knowledge/documents`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, source_name: sourceName }),
        });
        if (!response.ok) throw new Error('Failed to ingest document');
        nameInput.value = '';
        textInput.value = '';
        await loadKnowledge();
    } catch (error) {
        alert(`Error: ${error.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Ingest Document';
    }
}

async function ingestKnowledgeUrl() {
    const urlInput = document.getElementById('knowledge-url-input');
    const nameInput = document.getElementById('knowledge-url-name');
    const btn = document.getElementById('knowledge-url-btn');
    const overlay = document.getElementById('ingest-overlay');
    const spinner = document.getElementById('ingest-spinner');
    const statusEl = document.getElementById('ingest-overlay-status');
    const closeBtn = document.getElementById('ingest-overlay-close');

    const url = urlInput.value.trim();
    if (!url || !knowledgeAgentId) return;

    btn.disabled = true;
    overlay.hidden = false;
    closeBtn.hidden = true;
    spinner.style.display = '';
    statusEl.textContent = 'Fetching and ingesting URL...';
    statusEl.className = 'ingest-overlay-status';

    try {
        const response = await apiFetch(
            `${API_BASE}/agents/${encodeURIComponent(knowledgeAgentId)}/knowledge/urls`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, source_name: nameInput.value.trim() }),
            },
        );
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        const data = await response.json();
        spinner.style.display = 'none';
        statusEl.textContent = data.message || 'Ingestion complete';
        statusEl.className = 'ingest-overlay-status success';
        urlInput.value = '';
        nameInput.value = '';
        await loadKnowledge();
        setTimeout(() => { overlay.hidden = true; }, 2000);
    } catch (error) {
        spinner.style.display = 'none';
        statusEl.textContent = `Error: ${error.message}`;
        statusEl.className = 'ingest-overlay-status error';
        closeBtn.hidden = false;
    } finally {
        btn.disabled = false;
    }
}

async function searchKnowledge() {
    const input = document.getElementById('knowledge-search-input');
    const btn = document.getElementById('knowledge-search-btn');
    const results = document.getElementById('knowledge-search-results');
    const query = input.value.trim();
    if (!query || !knowledgeAgentId) return;

    btn.disabled = true;
    btn.textContent = 'Searching...';
    results.innerHTML = '<div class="spinner"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/agents/${encodeURIComponent(knowledgeAgentId)}/knowledge/search`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query }),
        });
        if (!response.ok) throw new Error('Failed to search knowledge');
        const data = await response.json();

        if (!data.results || data.results.length === 0) {
            results.innerHTML = '<div class="empty-state" style="padding:10px 0;">No results found</div>';
            return;
        }

        results.innerHTML = data.results.map(r => `
            <div class="knowledge-card">
                <div style="font-size:11px;color:var(--text-tertiary);margin-bottom:4px;">
                    ${escapeHtml(r.source_type)} &middot; similarity: ${(r.similarity * 100).toFixed(0)}%
                </div>
                <div style="font-size:13px;color:var(--text-secondary);">${escapeHtml(r.content)}</div>
            </div>
        `).join('');
    } catch (error) {
        results.innerHTML = `<div class="empty-state" style="padding:10px 0;">Error: ${escapeHtml(error.message)}</div>`;
    } finally {
        btn.disabled = false;
        btn.textContent = 'Search';
    }
}

// Knowledge modal event listeners
closeKnowledgeBtn.addEventListener('click', closeKnowledgeModal);
knowledgeOverlay.addEventListener('click', (e) => {
    if (e.target === knowledgeOverlay) closeKnowledgeModal();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !knowledgeOverlay.hidden) closeKnowledgeModal();
});
document.getElementById('knowledge-fact-btn')?.addEventListener('click', addKnowledgeFact);
document.getElementById('knowledge-url-btn')?.addEventListener('click', ingestKnowledgeUrl);
document.getElementById('knowledge-doc-btn')?.addEventListener('click', ingestKnowledgeDoc);
document.getElementById('knowledge-search-btn')?.addEventListener('click', searchKnowledge);
document.getElementById('ingest-overlay-close')?.addEventListener('click', () => {
    document.getElementById('ingest-overlay').hidden = true;
});
document.getElementById('knowledge-search-input')?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') searchKnowledge();
});


// --- Skills Library ---

const customToolsLibraryList = document.getElementById('custom-tools-library-list');
const refreshCustomToolsBtn = document.getElementById('refresh-custom-tools');
const createCustomToolBtn = document.getElementById('create-custom-tool-btn');
const skillsLibraryList = document.getElementById('skills-library-list');
const refreshSkillsBtn = document.getElementById('refresh-skills');
const createSkillBtn = document.getElementById('create-skill-btn');

const skillEditorOverlay = document.getElementById('skill-editor-overlay');
const skillEditorTitle = document.getElementById('skill-editor-title');
const skillEditorName = document.getElementById('skill-editor-name');
const skillEditorDescription = document.getElementById('skill-editor-description');
const skillEditorTags = document.getElementById('skill-editor-tags');
const skillEditorParameters = document.getElementById('skill-editor-parameters');
const skillEditorCodeWrapper = document.getElementById('skill-editor-code-wrapper');
const skillEditorChangesGroup = document.getElementById('skill-editor-changes-group');
const skillEditorChanges = document.getElementById('skill-editor-changes');
const skillEditorSaveBtn = document.getElementById('skill-editor-save-btn');
const skillEditorCancelBtn = document.getElementById('skill-editor-cancel-btn');
const skillEditorStatus = document.getElementById('skill-editor-status');
const closeSkillEditorBtn = document.getElementById('close-skill-editor-btn');

let skillEditorMode = 'create'; // 'create' | 'edit'
let skillEditorOriginalName = null;
let skillEditorType = 'executable'; // 'executable' | 'prompt'

function setSkillEditorType(type) {
    skillEditorType = type;
    const isPrompt = type === 'prompt';

    document.querySelectorAll('.skill-type-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.skillType === type);
    });

    document.getElementById('skill-editor-parameters-group').hidden = isPrompt;
    document.getElementById('skill-editor-hint-executable').hidden = isPrompt;
    document.getElementById('skill-editor-hint-prompt').hidden = !isPrompt;
    document.getElementById('skill-editor-example-executable').hidden = isPrompt;
    document.getElementById('skill-editor-example-prompt').hidden = !isPrompt;
    document.getElementById('skill-editor-code-label').textContent = isPrompt ? 'Content' : 'Code';

    document.getElementById('skill-editor-name').placeholder = isPrompt
        ? 'e.g. python-best-practices'
        : 'e.g. word-count';
    document.getElementById('skill-editor-description').placeholder = isPrompt
        ? 'e.g. Python coding standards and conventions'
        : 'e.g. Count words, lines, and characters in the given text';
    document.getElementById('skill-editor-tags').placeholder = isPrompt
        ? 'e.g. python, best-practices, code-style'
        : 'e.g. text, analysis';

    if (skillEditorMode === 'create') {
        const title = isPrompt ? 'New Skill' : 'New Custom Tool';
        skillEditorTitle.textContent = title;
        skillEditorSaveBtn.textContent = isPrompt ? 'Create Skill' : 'Create Custom Tool';
    }

    if (skillCodeMirror) {
        skillCodeMirror.setOption('mode', isPrompt ? 'markdown' : 'python');
    }
}

document.querySelectorAll('.skill-type-btn').forEach(btn => {
    btn.addEventListener('click', () => setSkillEditorType(btn.dataset.skillType));
});
let skillCodeMirror = null;

function ensureCodeMirror() {
    if (skillCodeMirror) return;
    if (typeof CodeMirror === 'undefined' || !skillEditorCodeWrapper) return;
    skillCodeMirror = CodeMirror(skillEditorCodeWrapper, {
        mode: 'python',
        theme: 'default',
        lineNumbers: true,
        matchBrackets: true,
        autoCloseBrackets: true,
        styleActiveLine: true,
        indentUnit: 4,
        tabSize: 4,
        indentWithTabs: false,
        lineWrapping: true,
        viewportMargin: Infinity,
        placeholder: 'import json, sys\nparams = json.loads(sys.stdin.read())\n# ... do work ...\nprint(json.dumps({\"result\": \"success\"}))',
        extraKeys: {
            'Cmd-/': 'toggleComment',
            'Ctrl-/': 'toggleComment',
            'Tab': (cm) => {
                if (cm.somethingSelected()) {
                    cm.indentSelection('add');
                } else {
                    cm.replaceSelection('    ', 'end');
                }
            },
            'Shift-Tab': (cm) => cm.indentSelection('subtract'),
        },
    });
}

function getSkillCode() {
    if (skillCodeMirror) return skillCodeMirror.getValue();
    return '';
}

function setSkillCode(value) {
    if (skillCodeMirror) {
        skillCodeMirror.setValue(value);
        setTimeout(() => skillCodeMirror.refresh(), 1);
    }
}

function parseParametersField(text) {
    if (!text.trim()) return {};
    const params = {};
    for (const line of text.split('\n')) {
        const eq = line.indexOf('=');
        if (eq > 0) params[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
    }
    return params;
}

function formatParametersField(params) {
    if (!params || typeof params !== 'object') return '';
    return Object.entries(params).map(([k, v]) => `${k}=${v}`).join('\n');
}

function formatSuccessRate(rate) {
    if (typeof rate !== 'number') return 'N/A';
    return `${(rate * 100).toFixed(0)}%`;
}

function renderSkillCards(listEl, items, emptyMessage) {
    if (items.length === 0) {
        listEl.innerHTML = `<p class="empty-state">${escapeHtml(emptyMessage)}</p>`;
        return;
    }
    const sorted = items.sort((a, b) => b.usage_count - a.usage_count);
    listEl.innerHTML = sorted.map(skill => {
        const tags = (skill.tags || []).map(t => `<span class="skill-tag">${escapeHtml(t)}</span>`).join('');
        return `
            <div class="skill-card" data-skill-name="${escapeAttr(skill.name)}">
                <div class="skill-card-header">
                    <span class="skill-card-name">${escapeHtml(skill.name)}</span>
                    <div style="display:flex;align-items:center;gap:6px;">
                        <span class="skill-card-version">v${skill.version}</span>
                        <div class="skill-card-actions">
                            <button class="btn btn-secondary skill-edit-btn" data-skill="${escapeAttr(skill.name)}" title="Edit">
                                <i class="fas fa-pen"></i>
                            </button>
                            <button class="btn btn-danger skill-delete-btn" data-skill="${escapeAttr(skill.name)}" title="Delete">
                                <i class="fas fa-trash"></i>
                            </button>
                        </div>
                    </div>
                </div>
                <div class="skill-card-desc">${escapeHtml(skill.description || '')}</div>
                <div class="skill-card-meta">
                    <span>Used ${skill.usage_count || 0} time${skill.usage_count === 1 ? '' : 's'}</span>
                    <span>Success: ${formatSuccessRate(skill.success_rate)}</span>
                    ${skill.last_used ? `<span>Last: ${new Date(skill.last_used).toLocaleDateString()}</span>` : ''}
                </div>
                ${tags ? `<div class="skill-card-tags" style="margin-top:6px;">${tags}</div>` : ''}
            </div>
        `;
    }).join('');

    listEl.querySelectorAll('.skill-edit-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            openSkillEditor('edit', btn.dataset.skill);
        });
    });
    listEl.querySelectorAll('.skill-delete-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            deleteSkill(btn.dataset.skill);
        });
    });
    listEl.querySelectorAll('.skill-card').forEach(card => {
        card.addEventListener('click', () => openSkillEditor('edit', card.dataset.skillName));
    });
}

async function loadCustomToolsLibrary() {
    if (!customToolsLibraryList) return;
    refreshCustomToolsBtn.disabled = true;
    customToolsLibraryList.innerHTML = '<div class="spinner"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/skills`);
        if (!response.ok) throw new Error('Failed to load custom tools');
        const all = await response.json();
        const customTools = all.filter(s => s.type !== 'prompt');
        renderSkillCards(customToolsLibraryList, customTools, 'No custom tools yet. Create one to get started.');
    } catch (error) {
        customToolsLibraryList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        refreshCustomToolsBtn.disabled = false;
    }
}

async function loadSkillsLibrary() {
    if (!skillsLibraryList) return;
    refreshSkillsBtn.disabled = true;
    skillsLibraryList.innerHTML = '<div class="spinner"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/skills`);
        if (!response.ok) throw new Error('Failed to load skills');
        const all = await response.json();
        const skills = all.filter(s => s.type === 'prompt');
        renderSkillCards(skillsLibraryList, skills, 'No skills yet. Create one to get started.');
    } catch (error) {
        skillsLibraryList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        refreshSkillsBtn.disabled = false;
    }
}

function openSkillEditor(mode, name, defaultType) {
    skillEditorMode = mode;
    skillEditorOriginalName = name || null;
    skillEditorStatus.textContent = '';

    skillEditorOverlay.hidden = false;
    ensureCodeMirror();

    const typeGroup = document.getElementById('skill-editor-type-group');

    if (mode === 'create') {
        typeGroup.hidden = !!defaultType;
        setSkillEditorType(defaultType || 'executable');
        skillEditorName.value = '';
        skillEditorName.disabled = false;
        skillEditorDescription.value = '';
        skillEditorTags.value = '';
        skillEditorParameters.value = '';
        setSkillCode('');
        skillEditorChangesGroup.hidden = true;
    } else {
        typeGroup.hidden = true;
        skillEditorSaveBtn.textContent = 'Save Changes';
        skillEditorChangesGroup.hidden = false;
        skillEditorChanges.value = '';
        skillEditorName.disabled = true;
        loadSkillIntoEditor(name);
    }

    setTimeout(() => { if (skillCodeMirror) skillCodeMirror.refresh(); }, 50);
}

async function loadSkillIntoEditor(name) {
    skillEditorName.value = name;
    skillEditorDescription.value = '';
    skillEditorTags.value = '';
    skillEditorParameters.value = '';
    setSkillCode('// Loading...');
    skillEditorSaveBtn.disabled = true;

    try {
        const response = await apiFetch(`${API_BASE}/skills/${encodeURIComponent(name)}`);
        if (!response.ok) throw new Error('Failed to load skill');
        const skill = await response.json();
        setSkillEditorType(skill.type || 'executable');
        skillEditorTitle.textContent = skill.type === 'prompt' ? 'Edit Skill' : 'Edit Custom Tool';
        skillEditorDescription.value = skill.description || '';
        skillEditorTags.value = (skill.tags || []).join(', ');
        skillEditorParameters.value = formatParametersField(skill.parameters);
        setSkillCode(skill.code || '');
    } catch (error) {
        skillEditorStatus.textContent = `Error: ${error.message}`;
        skillEditorStatus.className = 'form-status error';
    } finally {
        skillEditorSaveBtn.disabled = false;
    }
}

function closeSkillEditor() {
    skillEditorOverlay.hidden = true;
}

async function saveSkill() {
    const name = skillEditorName.value.trim();
    const description = skillEditorDescription.value.trim();
    const code = getSkillCode();
    const tagsRaw = skillEditorTags.value.trim();
    const tags = tagsRaw ? tagsRaw.split(',').map(t => t.trim()).filter(Boolean) : [];
    const parameters = parseParametersField(skillEditorParameters.value);

    if (!name) {
        skillEditorStatus.textContent = 'Name is required';
        skillEditorStatus.className = 'form-status error';
        return;
    }
    if (!description) {
        skillEditorStatus.textContent = 'Description is required';
        skillEditorStatus.className = 'form-status error';
        return;
    }
    if (!code.trim()) {
        skillEditorStatus.textContent = skillEditorType === 'prompt' ? 'Content is required' : 'Code is required';
        skillEditorStatus.className = 'form-status error';
        return;
    }

    skillEditorSaveBtn.disabled = true;
    skillEditorStatus.textContent = 'Saving...';
    skillEditorStatus.className = 'form-status';

    try {
        let response;
        if (skillEditorMode === 'create') {
            response = await apiFetch(`${API_BASE}/skills`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, description, code, tags, parameters, skill_type: skillEditorType }),
            });
        } else {
            const changes = skillEditorChanges.value.trim() || 'Updated via UI';
            response = await apiFetch(`${API_BASE}/skills/${encodeURIComponent(skillEditorOriginalName)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ description, code, tags, changes, parameters }),
            });
        }
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        skillEditorStatus.textContent = skillEditorMode === 'create' ? 'Created' : 'Saved';
        skillEditorStatus.className = 'form-status success';
        setTimeout(() => {
            closeSkillEditor();
            if (currentWorkspaceTab === 'custom-tools') loadCustomToolsLibrary();
            else if (currentWorkspaceTab === 'skills') loadSkillsLibrary();
        }, 600);
    } catch (error) {
        skillEditorStatus.textContent = `Error: ${error.message}`;
        skillEditorStatus.className = 'form-status error';
    } finally {
        skillEditorSaveBtn.disabled = false;
    }
}

async function deleteSkill(name) {
    if (!confirm(`Delete "${name}"? This cannot be undone.`)) return;
    try {
        const response = await apiFetch(`${API_BASE}/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${response.status}`);
        }
        if (currentWorkspaceTab === 'custom-tools') await loadCustomToolsLibrary();
        else await loadSkillsLibrary();
    } catch (error) {
        alert(`Failed to delete: ${error.message}`);
    }
}

if (refreshCustomToolsBtn) refreshCustomToolsBtn.addEventListener('click', loadCustomToolsLibrary);
if (createCustomToolBtn) createCustomToolBtn.addEventListener('click', () => openSkillEditor('create', null, 'executable'));
if (refreshSkillsBtn) refreshSkillsBtn.addEventListener('click', loadSkillsLibrary);
if (createSkillBtn) createSkillBtn.addEventListener('click', () => openSkillEditor('create', null, 'prompt'));
if (closeSkillEditorBtn) closeSkillEditorBtn.addEventListener('click', closeSkillEditor);
if (skillEditorCancelBtn) skillEditorCancelBtn.addEventListener('click', closeSkillEditor);
if (skillEditorSaveBtn) skillEditorSaveBtn.addEventListener('click', saveSkill);
if (skillEditorOverlay) {
    skillEditorOverlay.addEventListener('click', (e) => {
        if (e.target === skillEditorOverlay) closeSkillEditor();
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && !skillEditorOverlay.hidden) closeSkillEditor();
    });
}

// --- Provider management ---

const providersList = document.getElementById('providers-list');
const refreshProvidersBtn = document.getElementById('refresh-providers');
const createProviderBtn = document.getElementById('create-provider-btn');
const providerEditorOverlay = document.getElementById('provider-editor-overlay');
const closeProviderEditorBtn = document.getElementById('close-provider-editor-btn');
const providerEditorCancelBtn = document.getElementById('provider-editor-cancel-btn');
const providerEditorSaveBtn = document.getElementById('provider-editor-save-btn');
const providerEditorStatus = document.getElementById('provider-editor-status');
let providerEditorMode = 'create';

async function loadProviders() {
    if (!providersList) return;
    if (refreshProvidersBtn) refreshProvidersBtn.disabled = true;
    providersList.innerHTML = '<div class="spinner"></div>';
    try {
        const response = await apiFetch(`${API_BASE}/providers`);
        if (!response.ok) throw new Error('Failed to load providers');
        const providers = await response.json();
        if (providers.length === 0) {
            providersList.innerHTML = '<p class="empty-state">No providers configured. Create one to get started, or set LLM_* env vars and restart.</p>';
            return;
        }
        providersList.innerHTML = providers.map(renderProviderCard).join('');
        providersList.querySelectorAll('.provider-edit-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                openProviderEditor('edit', btn.dataset.provider);
            });
        });
        providersList.querySelectorAll('.provider-delete-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                deleteProvider(btn.dataset.provider);
            });
        });
    } catch (error) {
        providersList.innerHTML = `<p class="empty-state">Error: ${escapeHtml(error.message)}</p>`;
    } finally {
        if (refreshProvidersBtn) refreshProvidersBtn.disabled = false;
    }
}

function renderProviderCard(provider) {
    const apiKeyDot = provider.api_key_env
        ? `<span class="env-status-dot ${provider.api_key_set ? 'env-set' : 'env-unset'}" title="${escapeAttr(provider.api_key_env)}: ${provider.api_key_set ? 'set' : 'NOT set'}"></span> ${escapeHtml(provider.api_key_env)}`
        : '<span class="env-na">no key needed</span>';
    const headersDot = provider.extra_headers_env
        ? `<span class="env-status-dot ${provider.extra_headers_set ? 'env-set' : 'env-unset'}" title="${escapeAttr(provider.extra_headers_env)}: ${provider.extra_headers_set ? 'set' : 'NOT set'}"></span> ${escapeHtml(provider.extra_headers_env)}`
        : '';
    return `
        <div class="skill-card provider-card">
            <div class="skill-card-header">
                <span class="skill-card-name">${escapeHtml(provider.name)}</span>
                <div style="display:flex;align-items:center;gap:6px;">
                    <span class="provider-type-badge">${escapeHtml(provider.type)}</span>
                    ${provider.is_default ? '<span class="provider-default-badge">Default</span>' : ''}
                    <div class="skill-card-actions">
                        <button class="btn btn-secondary provider-edit-btn" data-provider="${escapeAttr(provider.id)}" title="Edit">
                            <i class="fas fa-pen"></i>
                        </button>
                        <button class="btn btn-danger provider-delete-btn" data-provider="${escapeAttr(provider.id)}" title="Delete">
                            <i class="fas fa-trash"></i>
                        </button>
                    </div>
                </div>
            </div>
            <div class="skill-card-desc">${escapeHtml(provider.base_url)}</div>
            <div class="skill-card-meta">
                <span>Model: ${escapeHtml(provider.default_model)}</span>
                <span>API Key: ${apiKeyDot}</span>
                ${headersDot ? `<span>Headers: ${headersDot}</span>` : ''}
            </div>
        </div>
    `;
}

async function openProviderEditor(mode, providerId) {
    providerEditorMode = mode;
    providerEditorStatus.textContent = '';
    providerEditorOverlay.hidden = false;

    const idInput = document.getElementById('provider-editor-id');
    const nameInput = document.getElementById('provider-editor-name');
    const typeSelect = document.getElementById('provider-editor-type');
    const baseUrlInput = document.getElementById('provider-editor-base-url');
    const modelInput = document.getElementById('provider-editor-model');
    const apiKeyEnvInput = document.getElementById('provider-editor-api-key-env');
    const headersEnvInput = document.getElementById('provider-editor-headers-env');
    const defaultCheckbox = document.getElementById('provider-editor-default');
    const titleEl = document.getElementById('provider-editor-title');

    if (mode === 'edit' && providerId) {
        titleEl.textContent = 'Edit Provider';
        providerEditorSaveBtn.textContent = 'Save';
        try {
            const res = await apiFetch(`${API_BASE}/providers/${encodeURIComponent(providerId)}`);
            if (!res.ok) throw new Error('Failed to load provider');
            const p = await res.json();
            idInput.value = p.id;
            idInput.disabled = true;
            nameInput.value = p.name;
            typeSelect.value = p.type;
            baseUrlInput.value = p.base_url;
            modelInput.value = p.default_model;
            apiKeyEnvInput.value = p.api_key_env || '';
            headersEnvInput.value = p.extra_headers_env || '';
            defaultCheckbox.checked = p.is_default;
        } catch (error) {
            providerEditorStatus.textContent = error.message;
            providerEditorStatus.className = 'form-status error';
        }
    } else {
        titleEl.textContent = 'New Provider';
        providerEditorSaveBtn.textContent = 'Create Provider';
        idInput.value = '';
        idInput.disabled = false;
        nameInput.value = '';
        typeSelect.value = 'ollama';
        baseUrlInput.value = '';
        modelInput.value = '';
        apiKeyEnvInput.value = '';
        headersEnvInput.value = '';
        defaultCheckbox.checked = false;
    }
}

function closeProviderEditor() {
    providerEditorOverlay.hidden = true;
}

async function saveProvider() {
    const idInput = document.getElementById('provider-editor-id');
    const payload = {
        id: idInput.value.trim(),
        name: document.getElementById('provider-editor-name').value.trim(),
        type: document.getElementById('provider-editor-type').value,
        base_url: document.getElementById('provider-editor-base-url').value.trim(),
        default_model: document.getElementById('provider-editor-model').value.trim(),
        api_key_env: document.getElementById('provider-editor-api-key-env').value.trim() || null,
        extra_headers_env: document.getElementById('provider-editor-headers-env').value.trim() || null,
        is_default: document.getElementById('provider-editor-default').checked,
    };

    if (!payload.id || !payload.name || !payload.base_url || !payload.default_model) {
        providerEditorStatus.textContent = 'ID, name, base URL, and model are required.';
        providerEditorStatus.className = 'form-status error';
        return;
    }

    try {
        const url = providerEditorMode === 'edit'
            ? `${API_BASE}/providers/${encodeURIComponent(payload.id)}`
            : `${API_BASE}/providers`;
        const method = providerEditorMode === 'edit' ? 'PUT' : 'POST';
        const res = await apiFetch(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        closeProviderEditor();
        loadProviders();
    } catch (error) {
        providerEditorStatus.textContent = error.message;
        providerEditorStatus.className = 'form-status error';
    }
}

async function deleteProvider(providerId) {
    if (!confirm(`Delete provider "${providerId}"?`)) return;
    try {
        const res = await apiFetch(`${API_BASE}/providers/${encodeURIComponent(providerId)}`, { method: 'DELETE' });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            throw new Error(err.detail || `HTTP ${res.status}`);
        }
        loadProviders();
    } catch (error) {
        alert(`Failed to delete: ${error.message}`);
    }
}

async function populateProviderDropdown(selectId, currentValue) {
    const select = document.getElementById(selectId);
    if (!select) return;
    try {
        const res = await apiFetch(`${API_BASE}/providers`);
        if (!res.ok) return;
        const providers = await res.json();
        select.innerHTML = '<option value="">(use default provider)</option>';
        for (const p of providers) {
            const label = `${p.name} (${p.type} — ${p.default_model})${p.is_default ? ' [default]' : ''}`;
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = label;
            if (p.id === currentValue) opt.selected = true;
            select.appendChild(opt);
        }
        // Update model placeholder based on selected provider
        const updateModelPlaceholder = () => {
            const modelInput = document.getElementById('edit-model');
            if (!modelInput) return;
            const selected = providers.find(p => p.id === select.value);
            modelInput.placeholder = selected ? `default: ${selected.default_model}` : 'e.g. gpt-4o, qwen3:14b';
        };
        select.addEventListener('change', updateModelPlaceholder);
        updateModelPlaceholder();
    } catch {
        // leave as-is on error
    }
}

if (refreshProvidersBtn) refreshProvidersBtn.addEventListener('click', loadProviders);
if (createProviderBtn) createProviderBtn.addEventListener('click', () => openProviderEditor('create'));
if (closeProviderEditorBtn) closeProviderEditorBtn.addEventListener('click', closeProviderEditor);
if (providerEditorCancelBtn) providerEditorCancelBtn.addEventListener('click', closeProviderEditor);
if (providerEditorSaveBtn) providerEditorSaveBtn.addEventListener('click', saveProvider);
if (providerEditorOverlay) {
    providerEditorOverlay.addEventListener('click', (e) => {
        if (e.target === providerEditorOverlay) closeProviderEditor();
    });
}

// Encode a GATEWAY_TOKEN as a WebSocket subprotocol value. The
// Sec-WebSocket-Protocol token grammar doesn't allow arbitrary characters,
// so the raw token is base64url-encoded; the server reverses this in
// gateway/server.py's _decode_ws_token_subprotocol.
function wsTokenSubprotocol(token) {
    const bytes = new TextEncoder().encode(token);
    let binary = '';
    bytes.forEach((b) => { binary += String.fromCharCode(b); });
    const b64 = btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    return `kit-token.${b64}`;
}

// WebSocket connection
function connectWebSocket() {
    if (ws && ws.readyState === WebSocket.OPEN) {
        return;
    }

    try {
        // Browsers can't set an Authorization header on a WebSocket
        // handshake. The token (when we have one) travels as a
        // Sec-WebSocket-Protocol value instead of a query param — that's a
        // real handshake header, not part of the URL, so it doesn't end up
        // in access logs, proxy logs, or browser history the way
        // ?token=... would. Matches the server's _websocket_auth check.
        const wsUrl = `${WS_BASE}/ws`;
        const protocols = authToken ? [wsTokenSubprotocol(authToken)] : undefined;
        ws = new WebSocket(wsUrl, protocols);

        ws.onopen = () => {
            console.log('WebSocket connected');
            wsReconnectAttempts = 0;
            resetStreamingState();
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

function renderReactions(messageDiv, reactions) {
    const reactionsDiv = document.createElement('div');
    reactionsDiv.className = 'broadcast-reactions';
    reactions.forEach((r, i) => {
        const chip = document.createElement('span');
        chip.className = 'reaction-chip';
        chip.style.borderColor = avatarColorFor(r.agent_id);
        chip.style.animationDelay = `${i * 0.12}s`;
        chip.textContent = r.emoji;
        const tip = document.createElement('span');
        tip.className = 'reaction-tooltip';
        tip.textContent = r.agent_name;
        chip.appendChild(tip);
        reactionsDiv.appendChild(chip);
    });
    messageDiv.querySelector('.message-body').appendChild(reactionsDiv);
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
                resetStreamTimeout();
                removeThinkingIndicator();
                streamingText = '';

                const agent = agentsById[currentAgentId];
                const name = agent ? agent.name : (currentAgentId === KIT_AGENT_ID ? 'Kit' : currentAgentId);

                streamingMessageDiv = document.createElement('div');
                streamingMessageDiv.className = 'message assistant streaming';
                streamingMessageDiv.dataset.messageId = crypto.randomUUID();

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
                streamingContentDiv.className = 'message-content markdown';

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
                resetStreamTimeout();
                streamingText += data.content;
                streamingContentDiv.innerHTML = renderMarkdown(streamingText);
                scrollChatToBottom();
            }
            break;

        case 'tool_call_start':
            if (isOwnSession && streamingMessageDiv) {
                resetStreamTimeout();
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
                resetStreamTimeout();
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

        case 'mcp_notice':
            if (isOwnSession) {
                addMessage(data.content, 'system');
            }
            break;

        case 'stream_end':
            if (isOwnSession) {
                if (streamingMessageDiv) {
                    streamingMessageDiv.classList.remove('streaming');
                    const body = streamingMessageDiv.querySelector('.message-body');
                    if (body && !body.querySelector('.message-reactions')) {
                        const reactionsDiv = document.createElement('div');
                        reactionsDiv.className = 'message-reactions';
                        const addReactionBtn = document.createElement('button');
                        addReactionBtn.className = 'add-reaction-btn';
                        addReactionBtn.textContent = '😀';
                        addReactionBtn.title = 'Add reaction';
                        addReactionBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            showEmojiPicker(streamingMessageDiv, addReactionBtn);
                        });
                        reactionsDiv.appendChild(addReactionBtn);
                        body.appendChild(reactionsDiv);
                    }
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
            break;

        case 'broadcast_reply':
            if (currentMode === 'broadcast') {
                addMessage(data.message, 'assistant', null, null, data.agent_id);
                scrollChatToBottom();
            }
            break;

        case 'broadcast_reactions':
            if (currentMode === 'broadcast' && data.message_id) {
                const target = document.querySelector(`[data-message-id="${CSS.escape(data.message_id)}"]`);
                if (target) {
                    renderReactions(target, data.reactions);
                    scrollChatToBottom();
                }
            }
            break;

        case 'message_reaction':
            if (data.message_id && data.emoji) {
                const target = document.querySelector(`[data-message-id="${CSS.escape(data.message_id)}"]`);
                if (target && data.user_id !== USER_ID) {
                    applyReactionToMessage(target, data.emoji, data.user_id);
                }
            }
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
        const reactionEntries = [];
        for (const msg of messages) {
            if (msg.role === 'reactions') {
                if (msg.message_id && msg.reactions) {
                    const target = chatMessages.querySelector(`[data-message-id="${CSS.escape(msg.message_id)}"]`);
                    if (target) renderReactions(target, msg.reactions);
                }
                continue;
            }
            if (msg.role === 'user_reactions') {
                reactionEntries.push(msg);
                continue;
            }
            const div = addMessage(msg.content, msg.role, null, msg.sender || null, msg.agent_id || null);
            if (msg.message_id) div.dataset.messageId = msg.message_id;
            const timeDiv = div.querySelector('.message-time');
            if (timeDiv && msg.timestamp) {
                timeDiv.textContent = new Date(msg.timestamp).toLocaleTimeString();
            }
        }
        for (const entry of reactionEntries) {
            const target = chatMessages.querySelector(`[data-message-id="${CSS.escape(entry.message_id)}"]`);
            if (target && entry.emoji && entry.user_id) {
                applyReactionToMessage(target, entry.emoji, entry.user_id);
            }
        }
        messageCount = messages.filter(m => m.role !== 'reactions' && m.role !== 'user_reactions').length;
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

        const urlState = parseChatUrl();
        if (urlState) {
            if (urlState.mode === 'broadcast') {
                await selectBroadcastChannel(true);
            } else if (urlState.agent !== KIT_AGENT_ID) {
                await selectMember(urlState.agent, true);
            } else {
                await loadChatHistory();
            }
        } else {
            await loadChatHistory();
        }
        history.replaceState({ agent: currentAgentId, mode: currentMode }, '', `/chat/${encodeURIComponent(currentMode === 'broadcast' ? 'team' : currentAgentId)}`);

        addMessage('Connected to Kit', 'system');
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

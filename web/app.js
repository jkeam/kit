// Personal Assistant Web UI

const API_BASE = 'http://localhost:18789';
const WS_BASE = 'ws://localhost:18789';
const PLATFORM = 'web';
const USER_ID = 'browser';

let messageCount = 0;
let ws = null;
let wsReconnectAttempts = 0;
const WS_MAX_RECONNECT_ATTEMPTS = 5;

// Streaming state
let streamingMessageDiv = null;
let streamingContentDiv = null;
let streamingText = '';

// DOM Elements
const chatMessages = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');
const sendButton = document.getElementById('send-button');
const connectionStatus = document.getElementById('connection-status');
const connectionText = document.getElementById('connection-text');
const messageCountSpan = document.getElementById('message-count');
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
        if (tabName === 'schedules') loadSchedules();
        if (tabName === 'skills') loadSkills();
        if (tabName === 'persona') loadPersona();
    });
});

// Check connection
async function checkConnection() {
    try {
        const response = await fetch(`${API_BASE}/health`);
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

// Add message to chat
function addMessage(content, role = 'user', id = null) {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    if (id) messageDiv.id = id;

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    contentDiv.textContent = content;

    const timeDiv = document.createElement('div');
    timeDiv.className = 'message-time';
    timeDiv.textContent = new Date().toLocaleTimeString();

    messageDiv.appendChild(contentDiv);
    messageDiv.appendChild(timeDiv);

    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;

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
    chatMessages.scrollTop = chatMessages.scrollHeight;
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
            message: message,
        }));
        // Response handled by handleWebSocketMessage — input re-enabled on stream_end/stream_error
    } else {
        // Fallback to POST when WebSocket is not connected
        addThinkingIndicator();
        try {
            const response = await fetch(`${API_BASE}/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ platform: PLATFORM, user_id: USER_ID, message: message }),
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
        const response = await fetch(`${API_BASE}/sessions`);
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

// Memory search
async function searchMemory() {
    const query = memorySearchInput.value.trim();
    if (!query) return;

    memorySearchBtn.disabled = true;
    memorySearchBtn.textContent = 'Searching...';
    memoryResults.innerHTML = '<div class="pf-v5-c-spinner pf-m-md" style="margin: 20px auto;"><span class="pf-v5-c-spinner__clipper"></span></div>';

    try {
        const response = await fetch(`${API_BASE}/chat`, {
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
        const response = await fetch(`${API_BASE}/chat`, {
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
        const response = await fetch(`${API_BASE}/chat`, {
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
        ws = new WebSocket(`${WS_BASE}/ws`);

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
            if (wsReconnectAttempts < WS_MAX_RECONNECT_ATTEMPTS) {
                wsReconnectAttempts++;
                setTimeout(connectWebSocket, 2000 * wsReconnectAttempts);
            }
        };
    } catch (error) {
        console.error('Failed to connect WebSocket:', error);
    }
}

// Handle WebSocket messages
function handleWebSocketMessage(data) {
    console.log('WebSocket message:', data);
    const isOwnSession = data.session_id === `${PLATFORM}:${USER_ID}`;

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
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
            break;

        case 'text_delta':
            if (isOwnSession && streamingContentDiv) {
                streamingText += data.content;
                streamingContentDiv.textContent = streamingText;
                chatMessages.scrollTop = chatMessages.scrollHeight;
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
                chatMessages.scrollTop = chatMessages.scrollHeight;
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
                    chatMessages.scrollTop = chatMessages.scrollHeight;
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
            if (!isOwnSession) {
                addMessage(`[${data.session_id}] ${data.message}`, 'user');
            }
            break;

        case 'assistant_message':
            if (!isOwnSession) {
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
        const response = await fetch(`${API_BASE}/persona`);
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
        const response = await fetch(`${API_BASE}/persona`, {
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

// Load chat history from server
async function loadChatHistory() {
    const sessionId = `${PLATFORM}:${USER_ID}`;
    try {
        const response = await fetch(`${API_BASE}/sessions/${encodeURIComponent(sessionId)}/messages`);
        if (!response.ok) return;
        const messages = await response.json();
        for (const msg of messages) {
            const div = addMessage(msg.content, msg.role);
            const timeDiv = div.querySelector('.message-time');
            if (timeDiv && msg.timestamp) {
                timeDiv.textContent = new Date(msg.timestamp).toLocaleTimeString();
            }
        }
        if (messages.length > 0) {
            messageCount = messages.length;
            messageCountSpan.textContent = `${messageCount} messages`;
        }
    } catch (error) {
        console.error('Failed to load chat history:', error);
    }
}

// Initialize
async function init() {
    const connected = await checkConnection();

    if (connected) {
        await loadChatHistory();
        addMessage('Connected to Kit', 'system');
        loadSessions();
        connectWebSocket();
    } else {
        addMessage('Failed to connect to gateway. Make sure it\'s running on port 18789.', 'system');
    }

    // Check connection every 10 seconds
    setInterval(checkConnection, 10000);

    // Send WebSocket ping every 30 seconds
    setInterval(sendPing, 30000);
}

init();

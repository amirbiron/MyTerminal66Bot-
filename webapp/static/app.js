/**
 * Terminal Web App - Main Application
 * With PTY Terminal via WebSocket
 */

// ==== Globals ====
const tg = window.Telegram?.WebApp;
let currentLang = 'sh';
let commandHistory = [];
let historyIndex = -1;
let isExecuting = false;

// PTY Terminal globals
let ptyTerminal = null;
let ptyWebSocket = null;
let ptyFitAddon = null;
let ptyConnected = false;
let ptyReconnectAttempts = 0;
let ptyPermanentError = false;  // True if error is permanent (no point reconnecting)
let ptyLastError = '';          // Store last error message
let ptySkipAutoReconnect = false;  // Skip auto-reconnect for manual reconnect
const MAX_RECONNECT_ATTEMPTS = 5;

// Prompt symbols per language
const PROMPTS = {
    sh: '$',
    py: '>>>',
    js: '>',
    java: 'java>',
    pty: '~'
};

// Placeholders per language
const PLACEHOLDERS = {
    sh: 'הזן פקודה...',
    py: 'הזן קוד Python...',
    js: 'הזן קוד JavaScript...',
    java: 'הזן קוד Java...',
    pty: 'הקלד בטרמינל למעלה...'
};

// Welcome message HTML (single source of truth)
const WELCOME_HTML = `
    <div class="welcome-message">
        <div class="welcome-icon">
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <polyline points="4 17 10 11 4 5"></polyline>
                <line x1="12" y1="19" x2="20" y2="19"></line>
            </svg>
        </div>
        <h2>Terminal Web App</h2>
        <p>הזן פקודה או קוד להרצה</p>
    </div>
`;

// ==== DOM Elements ====
const elements = {
    outputArea: document.getElementById('output-area'),
    codeInput: document.getElementById('code-input'),
    prompt: document.getElementById('prompt'),
    btnRun: document.getElementById('btn-run'),
    btnClear: document.getElementById('btn-clear'),
    btnSettings: document.getElementById('btn-settings'),
    settingsModal: document.getElementById('settings-modal'),
    closeSettings: document.getElementById('close-settings'),
    btnResetSession: document.getElementById('btn-reset-session'),
    currentCwd: document.getElementById('current-cwd'),
    userId: document.getElementById('user-id'),
    allowedCommands: document.getElementById('allowed-commands'),
    commandsCount: document.getElementById('commands-count'),
    loading: document.getElementById('loading'),
    langTabs: document.querySelectorAll('.lang-tab'),
    // PTY elements
    ptyContainer: document.getElementById('pty-container'),
    ptyTerminal: document.getElementById('pty-terminal'),
    ptyStatusDot: document.getElementById('pty-status-dot'),
    ptyStatusText: document.getElementById('pty-status-text'),
    btnCopyPty: document.getElementById('btn-copy-pty'),
    btnReconnectPty: document.getElementById('btn-reconnect-pty'),
    // Input area
    inputArea: document.querySelector('.input-area')
};

// ==== Initialize ====
function init() {
    // Initialize Telegram WebApp
    if (tg) {
        tg.ready();
        tg.expand();
        
        // Apply Telegram theme
        applyTelegramTheme();
        
        // Set up main button (optional)
        tg.MainButton.setText('הרץ');
        tg.MainButton.onClick(executeCode);
    }
    
    // Show welcome message
    elements.outputArea.innerHTML = WELCOME_HTML;
    
    // Event listeners
    setupEventListeners();
    
    // Load session info
    loadSessionInfo();
    
    // Auto-resize textarea
    autoResizeTextarea(elements.codeInput);
    
    // Focus input
    elements.codeInput.focus();
}

function applyTelegramTheme() {
    if (!tg) return;
    
    const root = document.documentElement;
    const theme = tg.themeParams;
    
    if (theme.bg_color) root.style.setProperty('--tg-theme-bg-color', theme.bg_color);
    if (theme.text_color) root.style.setProperty('--tg-theme-text-color', theme.text_color);
    if (theme.hint_color) root.style.setProperty('--tg-theme-hint-color', theme.hint_color);
    if (theme.link_color) root.style.setProperty('--tg-theme-link-color', theme.link_color);
    if (theme.button_color) root.style.setProperty('--tg-theme-button-color', theme.button_color);
    if (theme.button_text_color) root.style.setProperty('--tg-theme-button-text-color', theme.button_text_color);
    if (theme.secondary_bg_color) root.style.setProperty('--tg-theme-secondary-bg-color', theme.secondary_bg_color);
}

function setupEventListeners() {
    // Language tabs
    elements.langTabs.forEach(tab => {
        tab.addEventListener('click', () => switchLanguage(tab.dataset.lang));
    });
    
    // Run button
    elements.btnRun.addEventListener('click', executeCode);
    
    // Clear button
    elements.btnClear.addEventListener('click', clearOutput);
    
    // Settings button
    elements.btnSettings.addEventListener('click', openSettings);
    elements.closeSettings.addEventListener('click', closeSettings);
    elements.settingsModal.querySelector('.modal-backdrop').addEventListener('click', closeSettings);
    
    // Reset session
    elements.btnResetSession.addEventListener('click', resetSession);
    
    // Input handlers
    elements.codeInput.addEventListener('keydown', handleInputKeydown);
    elements.codeInput.addEventListener('input', () => autoResizeTextarea(elements.codeInput));
    
    // Prevent form submission
    document.addEventListener('submit', e => e.preventDefault());
    
    // PTY Terminal buttons
    if (elements.btnCopyPty) {
        elements.btnCopyPty.addEventListener('click', copyPtyOutput);
    }
    if (elements.btnReconnectPty) {
        elements.btnReconnectPty.addEventListener('click', reconnectPty);
    }
    
    // Handle window resize for PTY terminal
    window.addEventListener('resize', () => {
        if (ptyFitAddon && currentLang === 'pty') {
            fitPtyTerminal();
        }
    });
}

// ==== Language Switching ====
function switchLanguage(lang) {
    currentLang = lang;
    
    // Update tabs
    elements.langTabs.forEach(tab => {
        tab.classList.toggle('active', tab.dataset.lang === lang);
    });
    
    // Update prompt
    elements.prompt.textContent = PROMPTS[lang];
    
    // Update placeholder
    elements.codeInput.placeholder = PLACEHOLDERS[lang];
    
    // Handle PTY mode
    if (lang === 'pty') {
        // Show PTY container, hide regular output
        elements.outputArea.classList.add('hidden');
        elements.ptyContainer.classList.remove('hidden');
        elements.inputArea.classList.add('hidden');
        
        // Initialize or reconnect PTY
        if (!ptyTerminal) {
            initPtyTerminal();
        } else if (!ptyConnected) {
            connectPtyWebSocket();
        }
        
        // Focus on terminal
        setTimeout(() => {
            if (ptyTerminal) {
                ptyTerminal.focus();
                fitPtyTerminal();
            }
        }, 100);
    } else {
        // Show regular output, hide PTY
        elements.outputArea.classList.remove('hidden');
        elements.ptyContainer.classList.add('hidden');
        elements.inputArea.classList.remove('hidden');
        
        // Focus input
        elements.codeInput.focus();
    }
}


// ==== PTY Terminal ====
function initPtyTerminal() {
    if (typeof Terminal === 'undefined') {
        console.error('xterm.js not loaded');
        return;
    }
    
    // Create terminal
    ptyTerminal = new Terminal({
        cursorBlink: true,
        cursorStyle: 'bar',
        fontSize: 14,
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        theme: {
            background: '#0d1117',
            foreground: '#c9d1d9',
            cursor: '#58a6ff',
            cursorAccent: '#0d1117',
            selection: 'rgba(88, 166, 255, 0.3)',
            black: '#484f58',
            red: '#ff7b72',
            green: '#3fb950',
            yellow: '#d29922',
            blue: '#58a6ff',
            magenta: '#bc8cff',
            cyan: '#39c5cf',
            white: '#b1bac4',
            brightBlack: '#6e7681',
            brightRed: '#ffa198',
            brightGreen: '#56d364',
            brightYellow: '#e3b341',
            brightBlue: '#79c0ff',
            brightMagenta: '#d2a8ff',
            brightCyan: '#56d4dd',
            brightWhite: '#ffffff'
        },
        allowProposedApi: true,
        scrollback: 10000
    });
    
    // Add fit addon
    ptyFitAddon = new FitAddon.FitAddon();
    ptyTerminal.loadAddon(ptyFitAddon);
    
    // Add web links addon
    const webLinksAddon = new WebLinksAddon.WebLinksAddon();
    ptyTerminal.loadAddon(webLinksAddon);
    
    // Open terminal in container
    ptyTerminal.open(elements.ptyTerminal);
    
    // Fit terminal to container
    fitPtyTerminal();
    
    // Handle terminal input
    ptyTerminal.onData(data => {
        if (ptyWebSocket && ptyWebSocket.readyState === WebSocket.OPEN) {
            ptyWebSocket.send(JSON.stringify({
                type: 'input',
                data: data
            }));
        }
    });
    
    // Handle terminal resize
    ptyTerminal.onResize(({ rows, cols }) => {
        if (ptyWebSocket && ptyWebSocket.readyState === WebSocket.OPEN) {
            ptyWebSocket.send(JSON.stringify({
                type: 'resize',
                rows: rows,
                cols: cols
            }));
        }
    });
    
    // Connect WebSocket
    connectPtyWebSocket();
}

function fitPtyTerminal() {
    if (ptyFitAddon && ptyTerminal) {
        try {
            ptyFitAddon.fit();
        } catch (e) {
            console.warn('Failed to fit terminal:', e);
        }
    }
}

function connectPtyWebSocket() {
    // Check for existing connection or connection in progress
    if (ptyWebSocket && 
        (ptyWebSocket.readyState === WebSocket.OPEN || 
         ptyWebSocket.readyState === WebSocket.CONNECTING)) {
        return;
    }
    
    updatePtyStatus('connecting');
    
    // Determine WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/terminal`;
    
    try {
        ptyWebSocket = new WebSocket(wsUrl);
        
        ptyWebSocket.onopen = () => {
            console.log('PTY WebSocket connected');
            // Send auth message
            ptyWebSocket.send(JSON.stringify({
                type: 'auth',
                init_data: getInitData()
            }));
        };
        
        ptyWebSocket.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                
                switch (msg.type) {
                    case 'auth_ok':
                        ptyConnected = true;
                        ptyReconnectAttempts = 0;
                        ptyPermanentError = false;
                        ptyLastError = '';
                        updatePtyStatus('connected');
                        // Request initial resize
                        if (ptyTerminal) {
                            const { rows, cols } = ptyTerminal;
                            ptyWebSocket.send(JSON.stringify({
                                type: 'resize',
                                rows: rows,
                                cols: cols
                            }));
                        }
                        break;
                    
                    case 'output':
                        if (ptyTerminal && msg.data) {
                            ptyTerminal.write(msg.data);
                        }
                        break;
                    
                    case 'error':
                        console.error('PTY error:', msg.message);
                        ptyLastError = msg.message;
                        // Mark permanent errors that shouldn't trigger reconnect
                        if (msg.message && (
                            msg.message.includes('ALLOW_ALL_COMMANDS') ||
                            msg.message.includes('not available') ||
                            msg.message.includes('Authentication failed')
                        )) {
                            ptyPermanentError = true;
                        }
                        updatePtyStatus('error', msg.message);
                        break;
                    
                    case 'exit':
                        console.log('PTY session ended');
                        ptyConnected = false;
                        updatePtyStatus('disconnected');
                        // Close WebSocket so reconnect works when switching tabs
                        if (ptyWebSocket) {
                            ptySkipAutoReconnect = true;  // Don't auto-reconnect on exit
                            ptyWebSocket.close();
                            ptyWebSocket = null;
                        }
                        break;
                    
                    case 'pong':
                        // Keepalive response
                        break;
                }
            } catch (e) {
                console.error('Failed to parse PTY message:', e);
            }
        };
        
        ptyWebSocket.onclose = () => {
            console.log('PTY WebSocket closed');
            ptyConnected = false;
            
            // Don't overwrite error status with disconnected
            if (!ptyLastError) {
                updatePtyStatus('disconnected');
            }
            
            // Skip auto-reconnect if:
            // - Manual reconnect in progress (ptySkipAutoReconnect)
            // - Permanent error occurred
            // - Not in PTY mode
            // - Max attempts reached
            if (ptySkipAutoReconnect || ptyPermanentError || 
                currentLang !== 'pty' || ptyReconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
                ptySkipAutoReconnect = false;  // Reset flag
                return;
            }
            
            ptyReconnectAttempts++;
            const delay = Math.min(1000 * Math.pow(2, ptyReconnectAttempts), 30000);
            console.log(`Reconnecting in ${delay}ms (attempt ${ptyReconnectAttempts})`);
            setTimeout(connectPtyWebSocket, delay);
        };
        
        ptyWebSocket.onerror = (error) => {
            console.error('PTY WebSocket error:', error);
            updatePtyStatus('error');
        };
        
    } catch (e) {
        console.error('Failed to create WebSocket:', e);
        updatePtyStatus('error', e.message);
    }
}

function updatePtyStatus(status, message = '') {
    if (!elements.ptyStatusDot || !elements.ptyStatusText) return;
    
    elements.ptyStatusDot.className = 'status-dot';
    
    switch (status) {
        case 'connected':
            elements.ptyStatusDot.classList.add('connected');
            elements.ptyStatusText.textContent = 'מחובר';
            break;
        case 'connecting':
            elements.ptyStatusDot.classList.add('connecting');
            elements.ptyStatusText.textContent = 'מתחבר...';
            break;
        case 'disconnected':
            elements.ptyStatusDot.classList.add('disconnected');
            elements.ptyStatusText.textContent = 'מנותק';
            break;
        case 'error':
            elements.ptyStatusDot.classList.add('error');
            elements.ptyStatusText.textContent = message || 'שגיאה';
            break;
    }
}

function reconnectPty() {
    // Set flag to skip auto-reconnect from onclose handler
    ptySkipAutoReconnect = true;
    
    // Reset error state for manual reconnect
    ptyPermanentError = false;
    ptyLastError = '';
    ptyReconnectAttempts = 0;
    
    if (ptyWebSocket) {
        ptyWebSocket.close();
        ptyWebSocket = null;
    }
    
    // Clear terminal
    if (ptyTerminal) {
        ptyTerminal.clear();
    }
    
    // Small delay to ensure onclose fires before we reconnect
    setTimeout(connectPtyWebSocket, 50);
}

function copyPtyOutput() {
    if (!ptyTerminal) return;
    
    // Get all terminal content
    const selection = ptyTerminal.getSelection();
    let textToCopy = '';
    
    if (selection) {
        // If there's a selection, copy that
        textToCopy = selection;
    } else {
        // Otherwise, get buffer content
        const buffer = ptyTerminal.buffer.active;
        const lines = [];
        for (let i = 0; i < buffer.length; i++) {
            const line = buffer.getLine(i);
            if (line) {
                lines.push(line.translateToString(true));
            }
        }
        textToCopy = lines.join('\n').trimEnd();
    }
    
    copyToClipboard(textToCopy, 'פלט הטרמינל הועתק');
}

async function copyToClipboard(text, successMessage = 'הועתק ללוח') {
    try {
        await navigator.clipboard.writeText(text);
        showCopyNotification(successMessage);
    } catch (e) {
        // Fallback for older browsers
        const textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand('copy');
            showCopyNotification(successMessage);
        } catch (err) {
            console.error('Copy failed:', err);
            showCopyNotification('שגיאה בהעתקה', true);
        }
        document.body.removeChild(textarea);
    }
}

function showCopyNotification(message, isError = false) {
    // Create notification element
    const notification = document.createElement('div');
    notification.className = `copy-notification ${isError ? 'error' : 'success'}`;
    notification.innerHTML = `
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            ${isError 
                ? '<circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line>'
                : '<path d="M22 11.08V12a10 10 0 11-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline>'
            }
        </svg>
        <span>${message}</span>
    `;
    
    document.body.appendChild(notification);
    
    // Animate in
    setTimeout(() => notification.classList.add('show'), 10);
    
    // Remove after delay
    setTimeout(() => {
        notification.classList.remove('show');
        setTimeout(() => notification.remove(), 300);
    }, 2000);
}

// ==== Code Execution ====
async function executeCode() {
    const code = elements.codeInput.value.trim();
    if (!code || isExecuting) return;
    
    isExecuting = true;
    elements.btnRun.disabled = true;
    showLoading(true);
    
    // Add to history
    addToHistory(code);
    
    // Clear welcome message if present
    const welcome = elements.outputArea.querySelector('.welcome-message');
    if (welcome) welcome.remove();
    
    // Add command to output
    addOutputEntry(code, null, currentLang);
    
    // Clear input
    elements.codeInput.value = '';
    autoResizeTextarea(elements.codeInput);
    
    try {
        const result = await apiExecute(currentLang, code);
        updateLastOutput(result);
    } catch (error) {
        updateLastOutput({
            output: '',
            error: error.message || 'Unknown error',
            exit_code: -1
        });
    } finally {
        isExecuting = false;
        elements.btnRun.disabled = false;
        showLoading(false);
        elements.codeInput.focus();
        scrollToBottom();
    }
}

// ==== API Calls ====
function getInitData() {
    return tg?.initData || '';
}

async function apiExecute(type, code) {
    const response = await fetch('/api/execute', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Telegram-Init-Data': getInitData()
        },
        body: JSON.stringify({ type, code })
    });
    
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.message || error.error || `HTTP ${response.status}`);
    }
    
    return response.json();
}

async function apiGetSession() {
    const response = await fetch('/api/session', {
        headers: {
            'X-Telegram-Init-Data': getInitData()
        }
    });
    
    if (!response.ok) return null;
    return response.json();
}

async function apiGetCommands() {
    const response = await fetch('/api/commands', {
        headers: {
            'X-Telegram-Init-Data': getInitData()
        }
    });
    
    if (!response.ok) return null;
    return response.json();
}

async function apiResetSession() {
    const response = await fetch('/api/session/reset', {
        method: 'POST',
        headers: {
            'X-Telegram-Init-Data': getInitData()
        }
    });
    
    return response.ok;
}

// ==== Output Management ====
function addOutputEntry(command, result, lang) {
    const entry = document.createElement('div');
    entry.className = 'output-entry';
    const entryId = `output-${Date.now()}`;
    entry.id = entryId;
    entry.innerHTML = `
        <div class="output-command">
            <span class="output-prompt">${escapeHtml(PROMPTS[lang])}</span>
            <span class="output-cmd-text">${escapeHtml(command)}</span>
        </div>
        <div class="output-result">${result ? formatOutput(result) : '<span class="loading-dots">...</span>'}</div>
        <div class="output-meta">
            <span class="lang-badge">${lang.toUpperCase()}</span>
            <span class="timestamp">${formatTime(new Date())}</span>
            <button class="copy-btn" data-entry-id="${entryId}" title="העתק פלט">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"></path>
                </svg>
            </button>
        </div>
    `;
    
    // Add copy button event listener
    const copyBtn = entry.querySelector('.copy-btn');
    copyBtn.addEventListener('click', () => copyOutputEntry(entryId));
    
    elements.outputArea.appendChild(entry);
    scrollToBottom();
}

function copyOutputEntry(entryId) {
    const entry = document.getElementById(entryId);
    if (!entry) return;
    
    const resultEl = entry.querySelector('.output-result');
    
    let textToCopy = '';
    if (resultEl) {
        textToCopy = resultEl.textContent;
    }
    
    copyToClipboard(textToCopy.trim(), 'הפלט הועתק');
}

function updateLastOutput(result) {
    const lastEntry = elements.outputArea.querySelector('.output-entry:last-child');
    if (!lastEntry) return;
    
    const resultEl = lastEntry.querySelector('.output-result');
    if (!resultEl) return;
    
    // Format output
    let text = '';
    let hasError = false;
    
    if (result.output) {
        text = result.output;
    }
    
    if (result.error) {
        if (text) text += '\n';
        text += result.error;
        hasError = result.exit_code !== 0;
    }
    
    if (!text) {
        text = result.exit_code === 0 ? '(completed)' : '(no output)';
    }
    
    resultEl.textContent = text;
    resultEl.classList.toggle('error', hasError);
    resultEl.classList.toggle('success', result.exit_code === 0 && !hasError);
    
    // Update meta
    const metaEl = lastEntry.querySelector('.output-meta');
    if (metaEl && result.exit_code !== undefined && result.exit_code !== 0) {
        const exitSpan = document.createElement('span');
        exitSpan.className = 'exit-code';
        exitSpan.textContent = `exit: ${result.exit_code}`;
        metaEl.appendChild(exitSpan);
    }
}

function formatOutput(result) {
    let text = '';
    if (result.output) text = result.output;
    if (result.error) {
        if (text) text += '\n';
        text += result.error;
    }
    return escapeHtml(text || '(no output)');
}

function clearOutput() {
    elements.outputArea.innerHTML = WELCOME_HTML;
}

function scrollToBottom() {
    elements.outputArea.scrollTop = elements.outputArea.scrollHeight;
}

// ==== History ====
function addToHistory(command) {
    // Don't add duplicates of the last command
    if (commandHistory.length > 0 && commandHistory[commandHistory.length - 1] === command) {
        return;
    }
    
    commandHistory.push(command);
    
    // Limit history size
    if (commandHistory.length > 100) {
        commandHistory = commandHistory.slice(-100);
    }
    
    historyIndex = commandHistory.length;
    
    // Save to localStorage
    try {
        localStorage.setItem('terminal_history', JSON.stringify(commandHistory));
    } catch (e) {}
}

function loadHistory() {
    try {
        const saved = localStorage.getItem('terminal_history');
        if (saved) {
            commandHistory = JSON.parse(saved);
            historyIndex = commandHistory.length;
        }
    } catch (e) {}
}

function navigateHistory(direction) {
    if (commandHistory.length === 0) return;
    
    if (direction === 'up') {
        if (historyIndex > 0) {
            historyIndex--;
            elements.codeInput.value = commandHistory[historyIndex];
        }
    } else if (direction === 'down') {
        if (historyIndex < commandHistory.length - 1) {
            historyIndex++;
            elements.codeInput.value = commandHistory[historyIndex];
        } else {
            historyIndex = commandHistory.length;
            elements.codeInput.value = '';
        }
    }
    
    autoResizeTextarea(elements.codeInput);
    
    // Move cursor to end
    setTimeout(() => {
        elements.codeInput.selectionStart = elements.codeInput.selectionEnd = elements.codeInput.value.length;
    }, 0);
}

// ==== Input Handling ====
function handleInputKeydown(e) {
    // Ctrl+Enter or Cmd+Enter to execute
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault();
        executeCode();
        return;
    }
    
    // Arrow up/down for history (only when on first/last line)
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        const value = elements.codeInput.value;
        const pos = elements.codeInput.selectionStart;
        const lines = value.split('\n');
        
        // Calculate current line
        let currentLine = 0;
        let charCount = 0;
        for (let i = 0; i < lines.length; i++) {
            charCount += lines[i].length + 1;
            if (charCount > pos) {
                currentLine = i;
                break;
            }
        }
        
        if (e.key === 'ArrowUp' && currentLine === 0) {
            e.preventDefault();
            navigateHistory('up');
        } else if (e.key === 'ArrowDown' && currentLine === lines.length - 1) {
            e.preventDefault();
            navigateHistory('down');
        }
    }
    
    // Tab for indentation
    if (e.key === 'Tab') {
        e.preventDefault();
        const start = elements.codeInput.selectionStart;
        const end = elements.codeInput.selectionEnd;
        const value = elements.codeInput.value;
        
        elements.codeInput.value = value.substring(0, start) + '    ' + value.substring(end);
        elements.codeInput.selectionStart = elements.codeInput.selectionEnd = start + 4;
        autoResizeTextarea(elements.codeInput);
    }
}

// ==== Settings ====
function openSettings() {
    elements.settingsModal.classList.remove('hidden');
    loadSessionInfo();
}

function closeSettings() {
    elements.settingsModal.classList.add('hidden');
}

async function loadSessionInfo() {
    try {
        const session = await apiGetSession();
        if (session) {
            elements.currentCwd.textContent = session.cwd || '~';
            elements.userId.textContent = session.user_id || '-';
        }
    } catch (e) {
        console.error('Failed to load session info:', e);
    }
    
    // Load allowed commands
    try {
        const cmds = await apiGetCommands();
        if (cmds) {
            if (cmds.allow_all) {
                elements.allowedCommands.textContent = '(כל הפקודות מאושרות)';
                elements.commandsCount.textContent = '∞';
            } else if (cmds.commands && cmds.commands.length > 0) {
                elements.allowedCommands.textContent = cmds.commands.join(', ');
                elements.commandsCount.textContent = cmds.commands.length;
            } else {
                elements.allowedCommands.textContent = '(אין פקודות מאושרות)';
                elements.commandsCount.textContent = '0';
            }
        }
    } catch (e) {
        elements.allowedCommands.textContent = '(שגיאה בטעינה)';
        console.error('Failed to load commands:', e);
    }
}

async function resetSession() {
    if (!confirm('לאפס את הסשן? זה ינקה את תיקיית העבודה והקשר Python.')) {
        return;
    }
    
    showLoading(true);
    
    try {
        await apiResetSession();
        await loadSessionInfo();
        clearOutput();
        
        if (tg) {
            tg.showAlert('הסשן אופס בהצלחה');
        } else {
            alert('הסשן אופס בהצלחה');
        }
    } catch (e) {
        console.error('Failed to reset session:', e);
        if (tg) {
            tg.showAlert('שגיאה באיפוס הסשן');
        } else {
            alert('שגיאה באיפוס הסשן');
        }
    } finally {
        showLoading(false);
        closeSettings();
    }
}

// ==== Utilities ====
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatTime(date) {
    return date.toLocaleTimeString('he-IL', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

function showLoading(show) {
    elements.loading.classList.toggle('hidden', !show);
}

// ==== Start ====
document.addEventListener('DOMContentLoaded', () => {
    loadHistory();
    init();
});

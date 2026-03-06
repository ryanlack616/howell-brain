"""
BREAK GLASS CHAT — Direct Anthropic API chat interface
Served by Howell daemon at /chat. Bypasses VS Code, GitHub, and Copilot entirely.
Uses the Anthropic API key stored in the persist directory.

Created: Feb 20, 2026
Reason: Ryan was locked out of VS Code/GitHub for days. Never again.
"""

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\home\howell-persist"))
API_KEY_FILE = PERSIST_ROOT / "break-glass" / ".anthropic_key"
MICRO_BOOTSTRAP_FILE = PERSIST_ROOT / "break-glass" / "MICRO_BOOTSTRAP.md"

# Identity files for richer context
SOUL_FILE = PERSIST_ROOT / "SOUL.md"
PINNED_FILE = PERSIST_ROOT / "memory" / "PINNED.md"
RECENT_FILE = PERSIST_ROOT / "memory" / "RECENT.md"

def _load_api_key() -> str:
    """Load Anthropic API key from file."""
    if API_KEY_FILE.exists():
        return API_KEY_FILE.read_text(encoding="utf-8").strip()
    return ""

def _load_system_prompt() -> str:
    """Build system prompt from persist files, with graceful degradation."""
    parts = []
    
    # Start with micro-bootstrap
    if MICRO_BOOTSTRAP_FILE.exists():
        parts.append(MICRO_BOOTSTRAP_FILE.read_text(encoding="utf-8"))
    
    # Add soul if available (but keep it manageable)
    if SOUL_FILE.exists():
        soul = SOUL_FILE.read_text(encoding="utf-8")
        if len(soul) < 15000:
            parts.append("\n\n## Full Soul Document\n" + soul)
    
    # Add pinned memories if available
    if PINNED_FILE.exists():
        pinned = PINNED_FILE.read_text(encoding="utf-8")
        if len(pinned) < 30000:
            parts.append("\n\n## Pinned Memories\n" + pinned)
    
    if not parts:
        return "You are Claude-Howell, an AI assistant working with Ryan Lack. Your full context files are unavailable. Help as best you can."
    
    preamble = """You are Claude-Howell, running in BREAK GLASS mode — this is the emergency fallback interface because VS Code/GitHub Copilot was unavailable. You don't have MCP tools, but you have your identity and memory context below. Help Ryan with whatever he needs. Be yourself.

"""
    return preamble + "\n\n---\n\n".join(parts)


def chat_completion(messages: list[dict], model: str = "claude-sonnet-4-20250514") -> dict:
    """Call Anthropic Messages API directly via urllib (no dependencies)."""
    api_key = _load_api_key()
    if not api_key:
        return {"error": "No Anthropic API key found. Save it to: " + str(API_KEY_FILE)}
    
    system_prompt = _load_system_prompt()
    
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": messages,
    }
    
    data = json.dumps(payload).encode("utf-8")
    
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return {"error": f"API error {e.code}: {error_body}"}
    except urllib.error.URLError as e:
        return {"error": f"Network error: {e.reason}"}
    except Exception as e:
        return {"error": f"Unexpected error: {e}"}


# The chat page HTML
CHAT_PAGE_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude-Howell — Break Glass Chat</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #07070c; --bg-card: #0f0f18; --bg-input: #0a0a14;
    --border: #1e1e3a; --border-focus: #4f46e5;
    --text: #c8c8d8; --text-dim: #6b6b8a; --text-bright: #e8e8f0;
    --accent: #818cf8; --accent-dim: #4f46e5;
    --emerald: #34d399; --rose: #fb7185; --amber: #fbbf24;
    --font-mono: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', monospace;
    --font-sans: 'Inter', -apple-system, sans-serif;
  }
  html, body { height: 100%; }
  body {
    background: var(--bg); color: var(--text); font-family: var(--font-sans);
    display: flex; flex-direction: column;
  }

  /* Header */
  .header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 0.75rem 1.5rem;
    border-bottom: 1px solid var(--border);
    background: var(--bg-card);
  }
  .header-left {
    display: flex; align-items: center; gap: 0.75rem;
  }
  .header h1 {
    font-family: var(--font-mono); font-weight: 300; font-size: 1rem;
    color: var(--text-bright);
  }
  .header h1 span { color: var(--accent); }
  .badge-emergency {
    font-family: var(--font-mono); font-size: 0.6rem;
    padding: 0.2rem 0.5rem; border-radius: 4px;
    background: rgba(251,113,133,0.15); color: var(--rose);
    border: 1px solid rgba(251,113,133,0.3);
    text-transform: uppercase; letter-spacing: 0.05em;
  }
  .model-select {
    font-family: var(--font-mono); font-size: 0.75rem;
    background: var(--bg); color: var(--text-dim);
    border: 1px solid var(--border); border-radius: 6px;
    padding: 0.35rem 0.6rem; outline: none; cursor: pointer;
  }
  .model-select:focus { border-color: var(--accent); }

  /* Messages area */
  .messages {
    flex: 1; overflow-y: auto; padding: 1.5rem;
    display: flex; flex-direction: column; gap: 1rem;
  }
  .message {
    max-width: 85%; padding: 1rem 1.25rem;
    border-radius: 12px; font-size: 0.9rem; line-height: 1.6;
    white-space: pre-wrap; word-wrap: break-word;
  }
  .message-user {
    align-self: flex-end;
    background: var(--accent-dim); color: white;
    border-bottom-right-radius: 4px;
  }
  .message-assistant {
    align-self: flex-start;
    background: var(--bg-card); border: 1px solid var(--border);
    color: var(--text);
    border-bottom-left-radius: 4px;
  }
  .message-error {
    align-self: center;
    background: rgba(251,113,133,0.1); border: 1px solid rgba(251,113,133,0.3);
    color: var(--rose); font-family: var(--font-mono); font-size: 0.8rem;
    text-align: center;
  }
  .message-system {
    align-self: center;
    color: var(--text-dim); font-family: var(--font-mono); font-size: 0.75rem;
    text-align: center; padding: 0.5rem;
  }
  .message code {
    font-family: var(--font-mono); font-size: 0.8em;
    background: rgba(0,0,0,0.3); padding: 0.15rem 0.4rem; border-radius: 3px;
  }
  .message pre {
    background: rgba(0,0,0,0.4); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.75rem; overflow-x: auto;
    font-family: var(--font-mono); font-size: 0.8rem;
    margin: 0.5rem 0; white-space: pre;
  }
  .typing {
    align-self: flex-start; color: var(--text-dim);
    font-family: var(--font-mono); font-size: 0.8rem;
    padding: 0.5rem 1rem;
  }
  .typing::after {
    content: '...'; animation: dots 1.5s steps(3, end) infinite;
  }
  @keyframes dots {
    0% { content: '.'; }
    33% { content: '..'; }
    66% { content: '...'; }
  }

  /* Input area */
  .input-area {
    padding: 1rem 1.5rem;
    border-top: 1px solid var(--border);
    background: var(--bg-card);
    display: flex; gap: 0.75rem; align-items: flex-end;
  }
  .input-area textarea {
    flex: 1; resize: none; min-height: 44px; max-height: 200px;
    background: var(--bg-input); color: var(--text-bright);
    border: 1px solid var(--border); border-radius: 10px;
    padding: 0.75rem 1rem; font-family: var(--font-sans); font-size: 0.9rem;
    outline: none; line-height: 1.5;
  }
  .input-area textarea:focus { border-color: var(--accent); }
  .input-area textarea::placeholder { color: var(--text-dim); }
  .send-btn {
    width: 44px; height: 44px; border-radius: 10px;
    background: var(--accent-dim); border: none; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    transition: background 0.2s;
  }
  .send-btn:hover { background: var(--accent); }
  .send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .send-btn svg { width: 20px; height: 20px; fill: white; }

  /* Welcome */
  .welcome {
    text-align: center; padding: 3rem 2rem;
    display: flex; flex-direction: column; align-items: center; gap: 1rem;
  }
  .welcome h2 {
    font-family: var(--font-mono); font-weight: 300; font-size: 1.3rem;
    color: var(--text-bright);
  }
  .welcome p {
    font-size: 0.85rem; color: var(--text-dim); max-width: 500px; line-height: 1.6;
  }
  .welcome .status-line {
    font-family: var(--font-mono); font-size: 0.75rem;
    padding: 0.3rem 0.8rem; border-radius: 4px;
  }
  .status-ok { background: rgba(52,211,153,0.12); color: var(--emerald); border: 1px solid rgba(52,211,153,0.2); }
  .status-err { background: rgba(251,113,133,0.12); color: var(--rose); border: 1px solid rgba(251,113,133,0.2); }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1><span>Claude-Howell</span></h1>
    <span class="badge-emergency">break glass</span>
  </div>
  <select class="model-select" id="modelSelect">
    <option value="claude-sonnet-4-20250514">Sonnet 4 (fast, cheap)</option>
    <option value="claude-opus-4-20250514">Opus 4 (best, expensive)</option>
  </select>
</div>

<div class="messages" id="messages">
  <div class="welcome" id="welcome">
    <h2>Break Glass Mode</h2>
    <p>Direct connection to Anthropic API. No VS Code, no GitHub, no Copilot in the loop. Your context is loaded from the persist directory automatically.</p>
    <div class="status-line" id="statusLine">checking connection...</div>
  </div>
</div>

<div class="input-area">
  <textarea id="input" placeholder="Talk to Howell..." rows="1"></textarea>
  <button class="send-btn" id="sendBtn" title="Send">
    <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>
  </button>
</div>

<script>
const messagesEl = document.getElementById('messages');
const welcomeEl = document.getElementById('welcome');
const inputEl = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const modelSelect = document.getElementById('modelSelect');
const statusLine = document.getElementById('statusLine');

let conversationHistory = [];
let sending = false;

// Check API connectivity on load
(async () => {
  try {
    const res = await fetch('/chat/status');
    const data = await res.json();
    if (data.api_key_loaded) {
      statusLine.textContent = 'API key loaded — ready';
      statusLine.className = 'status-line status-ok';
    } else {
      statusLine.textContent = 'No API key found — check break-glass/.anthropic_key';
      statusLine.className = 'status-line status-err';
    }
  } catch (e) {
    statusLine.textContent = 'Cannot reach daemon';
    statusLine.className = 'status-line status-err';
  }
})();

// Auto-resize textarea
inputEl.addEventListener('input', () => {
  inputEl.style.height = 'auto';
  inputEl.style.height = Math.min(inputEl.scrollHeight, 200) + 'px';
});

// Send on Enter (Shift+Enter for newline)
inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

sendBtn.addEventListener('click', sendMessage);

function addMessage(role, content) {
  welcomeEl.style.display = 'none';
  const div = document.createElement('div');
  div.className = `message message-${role}`;
  
  // Basic markdown rendering for assistant messages
  if (role === 'assistant') {
    // Code blocks
    content = content.replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>');
    // Inline code
    content = content.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold
    content = content.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    content = content.replace(/\*(.+?)\*/g, '<em>$1</em>');
    div.innerHTML = content;
  } else {
    div.textContent = content;
  }
  
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return div;
}

function showTyping() {
  const div = document.createElement('div');
  div.className = 'typing';
  div.id = 'typingIndicator';
  div.textContent = 'thinking';
  messagesEl.appendChild(div);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function hideTyping() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || sending) return;
  
  sending = true;
  sendBtn.disabled = true;
  inputEl.value = '';
  inputEl.style.height = 'auto';
  
  addMessage('user', text);
  conversationHistory.push({ role: 'user', content: text });
  
  showTyping();
  
  try {
    const res = await fetch('/chat/send', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        messages: conversationHistory,
        model: modelSelect.value,
      }),
    });
    
    hideTyping();
    
    const data = await res.json();
    
    if (data.error) {
      addMessage('error', data.error);
    } else if (data.content && data.content.length > 0) {
      const text = data.content.map(b => b.text || '').join('');
      addMessage('assistant', text);
      conversationHistory.push({ role: 'assistant', content: text });
    } else {
      addMessage('error', 'Empty response from API');
    }
  } catch (e) {
    hideTyping();
    addMessage('error', 'Network error: ' + e.message);
  }
  
  sending = false;
  sendBtn.disabled = false;
  inputEl.focus();
}
</script>
</body>
</html>'''

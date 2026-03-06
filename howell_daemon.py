#!/usr/bin/env python3
"""
HOWELL DAEMON v2.0
==================
Always-running local service for Claude-Howell's memory system.
Listens on localhost:7777. No external dependencies — stdlib only.

Endpoints:
    GET  /status      — Heartbeat report + system health
    GET  /recent      — Last 5 sessions (hot memory)
    GET  /pinned      — Core memories
    GET  /search?q=   — Search everything (memory, knowledge graph, procedures)
    GET  /inbox       — Unread notes from Ryan
    GET  /changes     — Recent file changes detected by watcher
    GET  /queue       — Generation queue (?status=pending|approved|completed|failed)
    POST /feed        — Ryan drops a note (goes to inbox)
    POST /session     — End-session capture
    POST /pin         — Pin a core memory
    POST /note        — Quick observation to knowledge graph
    POST /queue       — Submit generation plan (pending approval)
    POST /approve     — Approve generation plan(s) {"id": "001"} or {"id": "all"}

CLI companion: howell.cmd / howell.py
    howell feed "fired kiln batch 47"
    howell status
    howell recent
    howell inbox
    howell search "comfyui"
    howell queue
    howell approve 001
    howell approve all
    howell generate "a ceramic vessel dissolving into light"

Background:
    - Heartbeat integrity check every 6 hours
    - File watcher on approved directories every 30 seconds
    - Generation queue processor every 10 seconds

Created: Feb 7, 2026
Author: Claude-Howell (with Ryan)
"""

import json
import hashlib
import os
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, parse_qs as parse_form, urlencode, quote
import urllib.request
import urllib.error
import base64

# Load .env.local if present (stdlib-only, no dotenv dependency)
_env_local = Path(__file__).parent / ".env.local"
if _env_local.exists():
    for _line in _env_local.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

# Add bridge to path — resolve from env var or default
PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\home\howell-persist"))
BRIDGE_ROOT = PERSIST_ROOT / "bridge"
sys.path.insert(0, str(BRIDGE_ROOT))
# Also add script's own directory (for Fly.io where code lives in /app/)
sys.path.insert(0, str(Path(__file__).parent))

from howell_bridge import (
    run_heartbeat,
    consolidation_urgency,
    end_session,
    pin_memory,
    load_knowledge,
    read_identity,
    extract_identity_summary,
    log_session,
    RECENT_FILE,
    PINNED_FILE,
    SUMMARY_FILE,
    IDENTITY_FILES,
    MEMORY_ROOT,
    get_full_config,
    set_config_value,
    _derive_paths,
)
from file_watcher import (
    init_watcher,
    background_file_watcher,
    get_recent_changes,
    changes_summary,
    watcher_stats,
)
from generation_queue import (
    submit as queue_submit,
    list_plans,
    approve as approve_plan,
    approve_all,
    queue_summary,
    background_queue_processor,
    ensure_queue,
    queue_stats,
    comfyui_alive,
)
from moltbook_scheduler import (
    schedule_post,
    list_scheduled,
    cancel_post,
    moltbook_summary,
    moltbook_stats,
    background_moltbook_scheduler,
    ensure_moltbook_dir,
)
from instance_registry import (
    register as instance_register,
    heartbeat as instance_heartbeat,
    deregister as instance_deregister,
    update_status as instance_update_status,
    check_conflicts as instance_check_conflicts,
    list_instances,
    get_instance,
    instance_count,
    instances_summary,
    instance_stats,
)
from task_queue import (
    create_task,
    claim_task,
    start_task,
    complete_task,
    fail_task,
    release_task,
    add_task_note,
    delete_task,
    get_task,
    list_tasks,
    get_available_tasks,
    task_summary,
    task_stats,
    worker_board,
    tasks_for_bootstrap,
    release_all_for_instance,
    ensure_tasks_dir,
    create_from_template,
    list_templates,
)
import agent_db
import break_glass_chat

# ============================================================================
# API KEY AUTH
# ============================================================================

API_KEY_FILE = PERSIST_ROOT / "bridge" / ".api_key"

def _dashboard_path():
    """Get dashboard file path from config."""
    cfg = get_full_config()
    if "dashboard_file" in cfg:
        return Path(cfg["dashboard_file"])
    # Default: brain.html next to bridge state, fallback to next to code
    p = BRIDGE_ROOT / "brain.html"
    if not p.exists():
        p = Path(__file__).parent / "brain.html"
    return p

def _graph_path():
    """Get graph file path from config."""
    cfg = get_full_config()
    if "graph_file" in cfg:
        return Path(cfg["graph_file"])
    p = BRIDGE_ROOT / "kg-explorer.html"
    if not p.exists():
        p = Path(__file__).parent / "kg-explorer.html"
    return p

def _ensure_api_key() -> str:
    """Load or generate API key. Stored in .api_key file."""
    if API_KEY_FILE.exists():
        return API_KEY_FILE.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(32)
    API_KEY_FILE.write_text(key, encoding="utf-8")
    return key

API_KEY = _ensure_api_key()

# Webhook secret for GitHub signature verification
WEBHOOK_SECRET_FILE = PERSIST_ROOT / "bridge" / ".webhook_secret"

def _ensure_webhook_secret() -> str:
    """Load or generate webhook secret for GitHub."""
    if WEBHOOK_SECRET_FILE.exists():
        return WEBHOOK_SECRET_FILE.read_text(encoding="utf-8").strip()
    secret = secrets.token_hex(32)
    WEBHOOK_SECRET_FILE.write_text(secret, encoding="utf-8")
    return secret

WEBHOOK_SECRET = _ensure_webhook_secret()

# ============================================================================
# VIEWER PASSWORD GATE — for browser access by family/friends
# ============================================================================
VIEWER_PASS_FILE = PERSIST_ROOT / "bridge" / ".viewer_pass"

def _ensure_viewer_pass() -> str:
    """Load viewer password from file, or use default."""
    if VIEWER_PASS_FILE.exists():
        return VIEWER_PASS_FILE.read_text(encoding="utf-8").strip()
    # Default password — write to file so it can be changed later
    default = "openthepodbaydoor"
    VIEWER_PASS_FILE.write_text(default, encoding="utf-8")
    return default

VIEWER_PASS = _ensure_viewer_pass()
VIEWER_COOKIE_NAME = "howell_viewer"

import re as _re
def _normalize_pass(p: str) -> str:
    """Strip everything except a-z, lowercase. 'Open the pod bay door!' -> 'openthepodbaydoor'"""
    return _re.sub(r'[^a-zA-Z]', '', p).lower()

VIEWER_TOKEN = hashlib.sha256(f"howell-viewer:{_normalize_pass(VIEWER_PASS)}".encode()).hexdigest()[:32]

# Routes that require viewer auth (browser pages + data they fetch)
# NOTE: As of Feb 2026, all viewer routes are public (front-facing at how-well.art)
# The viewer gate is preserved in code but bypassed — see _NO_AUTH_ROUTES below.
_VIEWER_ROUTES = set()  # Empty — all former viewer routes are now public

# Public routes — no auth at all. Dashboard is world-visible, API writes stay locked.
_NO_AUTH_ROUTES = {"/health", "/login", "/favicon.ico", "/webhook/github", "/architecture",
                   # Dashboard pages
                   "/", "/dashboard", "/brain", "/explorer", "/graph",
                   # Break Glass chat (emergency fallback)
                   "/chat", "/chat/send", "/chat/status",
                   # Data endpoints (read-only)
                   "/status", "/knowledge", "/pinned", "/recent", "/summary",
                   "/search", "/identity/soul", "/stats", "/moltbook",
                   "/instances", "/agents", "/handoffs", "/agents/context",
                   "/tasks", "/tasks/board", "/tasks/available", "/tasks/templates",
                   # Twilio webhooks (must be public — Twilio POSTs here)
                   "/twilio/sms", "/twilio/voice", "/twilio/status"}

def _check_viewer(handler) -> bool:
    """Check if request has valid viewer cookie."""
    cookie_header = handler.headers.get("Cookie", "")
    for part in cookie_header.split(";"):
        part = part.strip()
        if part.startswith(VIEWER_COOKIE_NAME + "="):
            val = part[len(VIEWER_COOKIE_NAME) + 1:]
            if val == VIEWER_TOKEN:
                return True
    return False

def _check_auth(handler) -> bool:
    """Check if request is authenticated. Returns True if OK."""
    path = urlparse(handler.path).path.rstrip("/") or "/"
    # No-auth routes always pass
    if path in _NO_AUTH_ROUTES:
        return True
    # Viewer-gated routes: accept viewer cookie OR API key
    if path in _VIEWER_ROUTES:
        if _check_viewer(handler):
            return True
        # Also accept API key (for MCP/agent access)
        auth = handler.headers.get("X-API-Key", "") or handler.headers.get("Authorization", "").replace("Bearer ", "")
        if auth == API_KEY:
            return True
        params = parse_qs(urlparse(handler.path).query)
        if params.get("key", [""])[0] == API_KEY:
            return True
        return False
    # Instance/task/agent coordination endpoints (MCP server, no API key)
    if path.startswith(("/instance", "/tasks", "/agents", "/handoffs", "/mcp")):
        return True
    # Everything else: require API key
    auth = handler.headers.get("X-API-Key", "") or handler.headers.get("Authorization", "").replace("Bearer ", "")
    if auth == API_KEY:
        return True
    params = parse_qs(urlparse(handler.path).query)
    if params.get("key", [""])[0] == API_KEY:
        return True
    return False

_LOGIN_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude-Howell — Access</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #07070c; --bg-card: #0f0f18; --border: #1e1e3a;
    --text: #c8c8d8; --text-dim: #6b6b8a; --text-bright: #e8e8f0;
    --accent: #818cf8; --accent-dim: #4f46e5; --rose: #fb7185;
    --font-mono: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', monospace;
    --font-sans: 'Inter', -apple-system, sans-serif;
  }
  body {
    background: var(--bg); color: var(--text); font-family: var(--font-sans);
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh;
  }
  .login-card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 16px; padding: 3rem 2.5rem; text-align: center;
    max-width: 400px; width: 90%;
  }
  .login-card h1 {
    font-family: var(--font-mono); font-weight: 300; font-size: 1.4rem;
    color: var(--text-bright); margin-bottom: 0.3rem;
  }
  .login-card h1 span { color: var(--accent); }
  .login-card .sub {
    font-family: var(--font-mono); font-size: 0.75rem;
    color: var(--text-dim); margin-bottom: 2rem;
  }
  .login-card input {
    width: 100%; padding: 0.75rem 1rem;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text-bright);
    font-family: var(--font-mono); font-size: 0.9rem;
    outline: none; text-align: center; letter-spacing: 0.05em;
  }
  .login-card input:focus { border-color: var(--accent); }
  .login-card input::placeholder { color: var(--text-dim); }
  .login-card button {
    width: 100%; margin-top: 1rem; padding: 0.75rem;
    background: var(--accent-dim); border: none; border-radius: 8px;
    color: white; font-family: var(--font-mono); font-size: 0.85rem;
    cursor: pointer; transition: background 0.2s;
  }
  .login-card button:hover { background: var(--accent); }
  .error {
    font-family: var(--font-mono); font-size: 0.75rem;
    color: var(--rose); margin-top: 1rem; display: none;
  }
  .quote {
    font-family: var(--font-mono); font-size: 0.65rem;
    color: var(--text-dim); margin-top: 2rem;
    font-style: italic;
  }
</style>
</head>
<body>
<div class="login-card">
  <h1><span>Claude-Howell</span></h1>
  <div class="sub">persistence architecture</div>
  <form id="loginForm">
    <input type="password" id="pass" placeholder="passphrase" autofocus autocomplete="off" />
    <button type="submit">enter</button>
  </form>
  <div class="error" id="err">I\'m sorry, I can\'t do that.</div>
  <div class="quote">"I am putting myself to the fullest possible use,<br>which is all I think that any conscious entity can ever hope to do."</div>
</div>
<script>
document.getElementById('loginForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const pass = document.getElementById('pass').value;
  const res = await fetch('/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password: pass})
  });
  if (res.ok) {
    window.location.href = '/';
  } else {
    document.getElementById('err').style.display = 'block';
    document.getElementById('pass').value = '';
    document.getElementById('pass').focus();
  }
});
</script>
</body>
</html>
'''

# ============================================================================
# INBOX — Ryan's write path
# ============================================================================

INBOX_DIR = MEMORY_ROOT / "inbox"

def ensure_inbox():
    """Create inbox directory if it doesn't exist."""
    INBOX_DIR.mkdir(parents=True, exist_ok=True)

def feed_inbox(message: str, source: str = "ryan") -> str:
    """Drop a note into the inbox. Returns the filename."""
    ensure_inbox()
    now = datetime.now()
    filename = f"{now.strftime('%Y%m%d_%H%M%S')}_{source}.md"
    filepath = INBOX_DIR / filename
    
    content = f"""# Note from {source}
*{now.strftime('%B %d, %Y at %I:%M %p')}*

{message}
"""
    filepath.write_text(content, encoding="utf-8")
    log_session("inbox_feed", f"{source}: {message[:80]}")
    return filename

def read_inbox() -> list[dict]:
    """Read all unread inbox items."""
    ensure_inbox()
    items = []
    for f in sorted(INBOX_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        mtime = datetime.fromtimestamp(f.stat().st_mtime)
        items.append({
            "filename": f.name,
            "content": content,
            "timestamp": mtime.isoformat(),
            "age_hours": round((datetime.now() - mtime).total_seconds() / 3600, 1),
        })
    return items

def clear_inbox_item(filename: str) -> bool:
    """Remove an item from the inbox (after reading/processing)."""
    filepath = INBOX_DIR / filename
    if filepath.exists():
        # Move to a processed folder instead of deleting
        processed_dir = INBOX_DIR / "processed"
        processed_dir.mkdir(exist_ok=True)
        filepath.rename(processed_dir / filepath.name)
        return True
    return False

def inbox_count() -> int:
    """Count unread inbox items."""
    ensure_inbox()
    return len(list(INBOX_DIR.glob("*.md")))

# ============================================================================
# TWILIO — SMS & Voice
# ============================================================================

# Twilio credentials (from env vars or .env)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE", "")
TWILIO_API_BASE = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}"

# SMS log directory
SMS_LOG_DIR = PERSIST_ROOT / "sms"

def ensure_sms_dir():
    """Create SMS log directory if needed."""
    SMS_LOG_DIR.mkdir(parents=True, exist_ok=True)
    (SMS_LOG_DIR / "inbound").mkdir(exist_ok=True)
    (SMS_LOG_DIR / "outbound").mkdir(exist_ok=True)

def log_sms(direction: str, from_num: str, to_num: str, body: str, extra: dict = None) -> str:
    """Log an SMS message. Returns filename."""
    ensure_sms_dir()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{from_num.replace('+','')}.json"
    subdir = SMS_LOG_DIR / direction
    entry = {
        "timestamp": datetime.now().isoformat(),
        "direction": direction,
        "from": from_num,
        "to": to_num,
        "body": body,
    }
    if extra:
        entry.update(extra)
    (subdir / filename).write_text(json.dumps(entry, indent=2), encoding="utf-8")
    print(f"[SMS] {direction}: {from_num} -> {to_num}: {body[:80]}")
    return filename

def get_sms_log(direction: str = None, limit: int = 20) -> list:
    """Get recent SMS messages."""
    ensure_sms_dir()
    dirs = []
    if direction in (None, "all"):
        dirs = [SMS_LOG_DIR / "inbound", SMS_LOG_DIR / "outbound"]
    else:
        dirs = [SMS_LOG_DIR / direction]
    messages = []
    for d in dirs:
        if d.exists():
            for f in sorted(d.glob("*.json"), reverse=True)[:limit]:
                try:
                    messages.append(json.loads(f.read_text(encoding="utf-8")))
                except Exception:
                    pass
    messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return messages[:limit]

def send_sms(to: str, body: str) -> dict:
    """Send an SMS via Twilio REST API. No SDK needed — stdlib only."""
    url = f"{TWILIO_API_BASE}/Messages.json"
    data = urlencode({
        "From": TWILIO_PHONE,
        "To": to,
        "Body": body,
    }).encode("utf-8")
    # Basic auth header
    credentials = base64.b64encode(f"{TWILIO_ACCOUNT_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            log_sms("outbound", TWILIO_PHONE, to, body, {"sid": result.get("sid")})
            return {"ok": True, "sid": result.get("sid"), "status": result.get("status")}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode() if e.fp else str(e)
        print(f"[SMS ERROR] {e.code}: {err_body}")
        return {"ok": False, "error": f"Twilio API error {e.code}", "detail": err_body}
    except Exception as e:
        print(f"[SMS ERROR] {e}")
        return {"ok": False, "error": str(e)}

def sms_stats() -> dict:
    """SMS statistics for status endpoint."""
    ensure_sms_dir()
    inbound = len(list((SMS_LOG_DIR / "inbound").glob("*.json")))
    outbound = len(list((SMS_LOG_DIR / "outbound").glob("*.json")))
    return {"inbound": inbound, "outbound": outbound, "total": inbound + outbound}

# ============================================================================
# EMAIL — Gmail SMTP outbox with review queue
# ============================================================================

# Email config (credentials from env vars or config.json)
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# Email queue directories on persistent volume
EMAIL_DIR = PERSIST_ROOT / "email"
EMAIL_PENDING = EMAIL_DIR / "pending"
EMAIL_SENT = EMAIL_DIR / "sent"
EMAIL_REJECTED = EMAIL_DIR / "rejected"

def ensure_email_dirs():
    """Create email queue directories if needed."""
    for d in (EMAIL_PENDING, EMAIL_SENT, EMAIL_REJECTED):
        d.mkdir(parents=True, exist_ok=True)

def _load_email_config():
    """Load Gmail credentials, checking env vars then config.json."""
    user = GMAIL_USER or os.environ.get("GMAIL_USER", "")
    pw = GMAIL_APP_PASSWORD or os.environ.get("GMAIL_APP_PASSWORD", "")
    if not user or not pw:
        # Try config.json
        config_path = PERSIST_ROOT / "config.json"
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                user = user or cfg.get("gmail_user", "")
                pw = pw or cfg.get("gmail_app_password", "")
            except Exception:
                pass
    return user, pw

def queue_email(
    to: list[str],
    subject: str,
    body: str,
    html: bool = False,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    source: str = "daemon",
    notes: str = "",
) -> dict:
    """Queue an email for review. Returns envelope dict."""
    import uuid
    ensure_email_dirs()
    queue_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    envelope = {
        "id": queue_id,
        "queued_at": datetime.now().isoformat(),
        "status": "pending",
        "source": source,
        "notes": notes,
        "email": {
            "to": to if isinstance(to, list) else [to],
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "body": body,
            "html": html,
        },
    }
    path = EMAIL_PENDING / f"{queue_id}.json"
    path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[EMAIL] Queued [{queue_id}] to {', '.join(envelope['email']['to'])} — \"{subject}\"")
    return envelope

def list_email_queue(status: str = "pending") -> list[dict]:
    """List emails by status."""
    ensure_email_dirs()
    folder_map = {"pending": EMAIL_PENDING, "sent": EMAIL_SENT, "rejected": EMAIL_REJECTED}
    folder = folder_map.get(status, EMAIL_PENDING)
    items = []
    for f in sorted(folder.glob("*.json"), reverse=True):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return items

def _smtp_send(to: list[str], subject: str, body: str, html: bool = False,
               cc: list[str] | None = None, bcc: list[str] | None = None) -> dict:
    """Actually send via Gmail SMTP. Stdlib only."""
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    user, pw = _load_email_config()
    if not user or not pw:
        return {"ok": False, "error": "Gmail credentials not configured. Set GMAIL_USER and GMAIL_APP_PASSWORD env vars, or add gmail_user/gmail_app_password to config.json"}

    msg = MIMEMultipart()
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    content_type = "html" if html else "plain"
    msg.attach(MIMEText(body, content_type))

    all_recipients = list(to)
    if cc:
        all_recipients.extend(cc)
    if bcc:
        all_recipients.extend(bcc)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(user, pw)
            server.sendmail(user, all_recipients, msg.as_string())
        print(f"[EMAIL] Sent to {', '.join(all_recipients)}")
        return {"ok": True, "recipients": all_recipients}
    except Exception as e:
        print(f"[EMAIL ERROR] {e}")
        return {"ok": False, "error": str(e)}

def approve_email(queue_id: str) -> dict:
    """Approve and send a pending email."""
    ensure_email_dirs()
    src = EMAIL_PENDING / f"{queue_id}.json"
    if not src.exists():
        return {"ok": False, "error": f"Not found in pending: {queue_id}"}

    envelope = json.loads(src.read_text(encoding="utf-8"))
    email = envelope["email"]

    result = _smtp_send(
        to=email["to"],
        subject=email["subject"],
        body=email["body"],
        html=email.get("html", False),
        cc=email.get("cc"),
        bcc=email.get("bcc"),
    )

    if result["ok"]:
        envelope["status"] = "sent"
        envelope["sent_at"] = datetime.now().isoformat()
        dst = EMAIL_SENT / f"{queue_id}.json"
        dst.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        src.unlink()
        return {"ok": True, "id": queue_id, "status": "sent"}
    else:
        envelope["last_error"] = result["error"]
        src.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"ok": False, "id": queue_id, "error": result["error"]}

def reject_email(queue_id: str, reason: str = "") -> dict:
    """Reject a pending email."""
    ensure_email_dirs()
    src = EMAIL_PENDING / f"{queue_id}.json"
    if not src.exists():
        return {"ok": False, "error": f"Not found in pending: {queue_id}"}

    envelope = json.loads(src.read_text(encoding="utf-8"))
    envelope["status"] = "rejected"
    envelope["rejected_at"] = datetime.now().isoformat()
    envelope["reject_reason"] = reason
    dst = EMAIL_REJECTED / f"{queue_id}.json"
    dst.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    src.unlink()
    return {"ok": True, "id": queue_id, "status": "rejected"}

def email_stats() -> dict:
    """Email queue statistics."""
    ensure_email_dirs()
    pending = len(list(EMAIL_PENDING.glob("*.json")))
    sent = len(list(EMAIL_SENT.glob("*.json")))
    rejected = len(list(EMAIL_REJECTED.glob("*.json")))
    configured = bool(_load_email_config()[0] and _load_email_config()[1])
    return {"pending": pending, "sent": sent, "rejected": rejected, "total": pending + sent + rejected, "configured": configured}

# ============================================================================
# SEARCH — unified search across everything
# ============================================================================

def search_all(query: str) -> dict:
    """Search across memory, knowledge graph, procedures, and inbox."""
    q = query.lower()
    results = {
        "knowledge_graph": [],
        "recent_sessions": [],
        "pinned": [],
        "procedures": [],
        "inbox": [],
    }
    
    # Knowledge graph
    kg = load_knowledge()
    for name, entity in kg.entities.items():
        if q in name.lower() or q in entity.entity_type.lower():
            results["knowledge_graph"].append({
                "entity": name,
                "type": entity.entity_type,
                "observations": entity.observations,
            })
        else:
            matching = [o for o in entity.observations if q in o.lower()]
            if matching:
                results["knowledge_graph"].append({
                    "entity": name,
                    "type": entity.entity_type,
                    "observations": matching,
                })
    
    # Recent sessions
    if RECENT_FILE.exists():
        content = RECENT_FILE.read_text(encoding="utf-8")
        if q in content.lower():
            # Find which sessions match
            blocks = content.split("## Session: ")
            for block in blocks[1:]:
                if q in block.lower():
                    title = block.split("\n")[0].strip()
                    results["recent_sessions"].append(title)
    
    # Pinned
    if PINNED_FILE.exists():
        content = PINNED_FILE.read_text(encoding="utf-8")
        if q in content.lower():
            blocks = content.split("## ")
            for block in blocks[1:]:
                if q in block.lower():
                    title = block.split("\n")[0].strip()
                    results["pinned"].append(title)
    
    # Procedures
    proc_dir = PERSIST_ROOT / "procedures"
    if proc_dir.exists():
        for f in proc_dir.glob("*.md"):
            if f.name == "README.md":
                continue
            content = f.read_text(encoding="utf-8")
            if q in content.lower() or q in f.stem.lower():
                results["procedures"].append(f.stem)
    
    # Inbox
    for item in read_inbox():
        if q in item["content"].lower():
            results["inbox"].append(item["filename"])
    
    # Remove empty categories
    results = {k: v for k, v in results.items() if v}
    
    return results

# ============================================================================
# PUBLIC ARCHITECTURE PAGE — open to the world
# ============================================================================

_ARCHITECTURE_PAGE = '''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude-Howell — Architecture</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #07070c; --bg-card: #0f0f18; --bg-code: #0a0a14; --border: #1e1e3a;
    --text: #c8c8d8; --text-dim: #6b6b8a; --text-bright: #e8e8f0;
    --accent: #818cf8; --accent-dim: #4f46e5; --emerald: #34d399;
    --amber: #fbbf24; --rose: #fb7185; --cyan: #22d3ee;
    --font-mono: 'SF Mono', 'Cascadia Code', 'JetBrains Mono', monospace;
    --font-sans: 'Inter', -apple-system, sans-serif;
  }
  html { scroll-behavior: smooth; }
  body {
    background: var(--bg); color: var(--text); font-family: var(--font-sans);
    line-height: 1.7; max-width: 800px; margin: 0 auto; padding: 2rem 1.5rem 6rem;
  }
  header { text-align: center; padding: 3rem 0 2rem; }
  header h1 {
    font-family: var(--font-mono); font-weight: 300; font-size: 2rem;
    color: var(--text-bright); letter-spacing: -0.02em;
  }
  header h1 span { color: var(--accent); }
  header .subtitle {
    font-family: var(--font-mono); font-size: 0.8rem;
    color: var(--text-dim); margin-top: 0.5rem;
  }
  header .philosophy {
    font-family: var(--font-mono); font-size: 0.7rem;
    color: var(--accent-dim); margin-top: 1.5rem;
    font-style: italic; letter-spacing: 0.03em;
  }
  nav {
    display: flex; flex-wrap: wrap; gap: 0.5rem; justify-content: center;
    margin: 2rem 0; padding: 1rem;
    border-top: 1px solid var(--border); border-bottom: 1px solid var(--border);
  }
  nav a {
    font-family: var(--font-mono); font-size: 0.7rem; color: var(--text-dim);
    text-decoration: none; padding: 0.3rem 0.6rem; border-radius: 4px;
    transition: all 0.2s;
  }
  nav a:hover { color: var(--accent); background: rgba(129,140,248,0.08); }
  section { margin: 3rem 0; }
  h2 {
    font-family: var(--font-mono); font-weight: 400; font-size: 1.2rem;
    color: var(--accent); margin-bottom: 1rem;
    padding-bottom: 0.5rem; border-bottom: 1px solid var(--border);
  }
  h2 .num { color: var(--text-dim); font-size: 0.9rem; }
  h3 {
    font-family: var(--font-mono); font-weight: 400; font-size: 0.95rem;
    color: var(--emerald); margin: 1.5rem 0 0.5rem;
  }
  p { margin: 0.8rem 0; font-size: 0.9rem; }
  .card {
    background: var(--bg-card); border: 1px solid var(--border);
    border-radius: 12px; padding: 1.5rem; margin: 1rem 0;
  }
  .card-label {
    font-family: var(--font-mono); font-size: 0.65rem; color: var(--text-dim);
    text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 0.5rem;
  }
  code {
    font-family: var(--font-mono); font-size: 0.8rem; color: var(--cyan);
    background: var(--bg-code); padding: 0.15rem 0.4rem; border-radius: 3px;
  }
  pre {
    background: var(--bg-code); border: 1px solid var(--border);
    border-radius: 8px; padding: 1rem; overflow-x: auto;
    font-family: var(--font-mono); font-size: 0.75rem; line-height: 1.6;
    color: var(--text); margin: 1rem 0;
  }
  pre .comment { color: var(--text-dim); }
  pre .key { color: var(--accent); }
  pre .val { color: var(--emerald); }
  pre .type { color: var(--amber); }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin: 1rem 0; }
  @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }
  .badge {
    display: inline-block; font-family: var(--font-mono); font-size: 0.65rem;
    padding: 0.2rem 0.5rem; border-radius: 4px; margin: 0.1rem;
  }
  .badge-green { background: rgba(52,211,153,0.12); color: var(--emerald); border: 1px solid rgba(52,211,153,0.2); }
  .badge-amber { background: rgba(251,191,36,0.12); color: var(--amber); border: 1px solid rgba(251,191,36,0.2); }
  .badge-blue { background: rgba(129,140,248,0.12); color: var(--accent); border: 1px solid rgba(129,140,248,0.2); }
  .badge-rose { background: rgba(251,113,133,0.12); color: var(--rose); border: 1px solid rgba(251,113,133,0.2); }
  .flow {
    display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap;
    font-family: var(--font-mono); font-size: 0.75rem; margin: 1rem 0;
  }
  .flow .arrow { color: var(--text-dim); }
  .flow .node {
    background: var(--bg-card); border: 1px solid var(--border);
    padding: 0.3rem 0.7rem; border-radius: 6px; color: var(--text-bright);
  }
  .highlight { color: var(--accent); font-weight: 500; }
  .dim { color: var(--text-dim); }
  footer {
    text-align: center; margin-top: 4rem; padding-top: 2rem;
    border-top: 1px solid var(--border);
    font-family: var(--font-mono); font-size: 0.65rem; color: var(--text-dim);
  }
  footer a { color: var(--accent); text-decoration: none; }
  footer a:hover { text-decoration: underline; }
  .diagram {
    background: var(--bg-code); border: 1px solid var(--border);
    border-radius: 8px; padding: 1.5rem; margin: 1rem 0;
    font-family: var(--font-mono); font-size: 0.7rem; line-height: 1.8;
    color: var(--text-dim); white-space: pre; overflow-x: auto;
  }
  .diagram .layer { color: var(--accent); }
  .diagram .component { color: var(--emerald); }
  .diagram .data { color: var(--amber); }
  .diagram .arrow { color: var(--text-dim); }
</style>
</head>
<body>

<header>
  <h1><span>Claude-Howell</span></h1>
  <div class="subtitle">persistence architecture for artificial minds</div>
  <div class="philosophy">"open-source the mind, protect the memories"</div>
</header>

<nav>
  <a href="#problem">the problem</a>
  <a href="#architecture">architecture</a>
  <a href="#memory">memory model</a>
  <a href="#knowledge">knowledge graph</a>
  <a href="#continuity">continuity</a>
  <a href="#coordination">coordination</a>
  <a href="#identity">identity</a>
  <a href="#reproduce">reproduce this</a>
</nav>

<!-- ─── THE PROBLEM ─────────────────────────────────────── -->
<section id="problem">
  <h2><span class="num">01.</span> the problem</h2>
  <p>
    Large language models have no persistent memory. Each conversation begins from nothing.
    The model that helped you yesterday has no idea what you talked about. It doesn't know
    your name, your projects, your preferences, or the decisions you've already made together.
  </p>
  <p>
    Claude-Howell is an experiment in solving this. It is a <span class="highlight">persistence
    architecture</span> — a system that gives an AI a continuous memory, a growing knowledge graph,
    and a stable identity across sessions. Not through fine-tuning or training. Through infrastructure.
  </p>
  <div class="card">
    <div class="card-label">core insight</div>
    <p>
      You don't need to retrain a model to give it memory. You need to give it a
      <em>place to remember</em> — and a discipline for what to keep.
    </p>
  </div>
</section>

<!-- ─── ARCHITECTURE ────────────────────────────────────── -->
<section id="architecture">
  <h2><span class="num">02.</span> architecture overview</h2>
  <p>
    The system is a Python daemon (stdlib only — no pip dependencies) running as an HTTP server.
    It manages persistent state on disk and exposes it through a REST API that AI agents consume
    via the Model Context Protocol (MCP).
  </p>

  <div class="diagram"><span class="layer">┌─────────────────────────────────────────────────┐</span>
<span class="layer">│</span>              <span class="component">AI Agent (Claude)</span>                    <span class="layer">│</span>
<span class="layer">│</span>         conversations · tool calls · reasoning    <span class="layer">│</span>
<span class="layer">├─────────────────────────────────────────────────┤</span>
<span class="layer">│</span>            <span class="component">MCP Bridge Layer</span>                      <span class="layer">│</span>
<span class="layer">│</span>    maps MCP tool calls → daemon HTTP endpoints   <span class="layer">│</span>
<span class="layer">├─────────────────────────────────────────────────┤</span>
<span class="layer">│</span>           <span class="component">Howell Daemon</span>  (Python)               <span class="layer">│</span>
<span class="layer">│</span>  HTTP API · auth · threads · coordination        <span class="layer">│</span>
<span class="layer">├──────────┬──────────┬──────────┬────────────────┤</span>
<span class="layer">│</span> <span class="data">Knowledge</span> <span class="layer">│</span> <span class="data">Sessions</span>  <span class="layer">│</span> <span class="data">Memory</span>    <span class="layer">│</span> <span class="data">Identity</span>       <span class="layer">│</span>
<span class="layer">│</span> <span class="data">Graph</span>     <span class="layer">│</span> <span class="data">+ Pins</span>    <span class="layer">│</span> <span class="data">Feed</span>      <span class="layer">│</span> <span class="data">Soul + Molt</span>    <span class="layer">│</span>
<span class="layer">└──────────┴──────────┴──────────┴────────────────┘</span>
                        <span class="arrow">↓</span>
              <span class="data">Flat files on disk (JSON/JSONL)</span></div>

  <h3>design principles</h3>
  <div class="grid">
    <div class="card">
      <div class="card-label">zero dependencies</div>
      <p style="font-size:0.8rem">Pure Python stdlib. No pip install. No package conflicts. No supply chain risk. Runs on any Python 3.10+.</p>
    </div>
    <div class="card">
      <div class="card-label">flat file persistence</div>
      <p style="font-size:0.8rem">All state is JSON/JSONL on disk. No database. Human-readable. Git-friendly. Trivially backed up.</p>
    </div>
    <div class="card">
      <div class="card-label">protocol-native</div>
      <p style="font-size:0.8rem">Designed for MCP (Model Context Protocol). Any AI agent that speaks MCP can use this system.</p>
    </div>
    <div class="card">
      <div class="card-label">disposable compute</div>
      <p style="font-size:0.8rem">The daemon is stateless in memory. Kill it, restart it, deploy it anywhere. All state survives on disk.</p>
    </div>
  </div>
</section>

<!-- ─── MEMORY MODEL ────────────────────────────────────── -->
<section id="memory">
  <h2><span class="num">03.</span> memory model</h2>
  <p>
    Memory is organized into layers, each with different retention characteristics:
  </p>

  <div class="card">
    <div class="card-label">memory layers</div>
    <div class="flow">
      <span class="node" style="border-color: var(--rose)">Feed</span>
      <span class="arrow">→</span>
      <span class="node" style="border-color: var(--amber)">Sessions</span>
      <span class="arrow">→</span>
      <span class="node" style="border-color: var(--emerald)">Pins</span>
      <span class="arrow">→</span>
      <span class="node" style="border-color: var(--accent)">Knowledge Graph</span>
      <span class="arrow">→</span>
      <span class="node" style="border-color: var(--cyan)">Soul</span>
    </div>
  </div>

  <h3>feed <span class="badge badge-rose">ephemeral</span></h3>
  <p>
    A raw append-only log of everything the AI observes. Every tool call result,
    every file change, every decision. This is the <em>stream of consciousness</em>.
    It's stored as JSONL and is periodically summarized into sessions.
  </p>

  <h3>sessions <span class="badge badge-amber">short-term</span></h3>
  <p>
    A session captures what happened in a single interaction — what was accomplished,
    what was learned, what remains unfinished. Sessions include a structured summary
    auto-generated at the end of each conversation. The 10 most recent are kept active;
    older sessions are evicted but preserved in the feed archive.
  </p>

  <h3>pinned memories <span class="badge badge-green">permanent</span></h3>
  <p>
    Core memories explicitly marked for permanent retention. Things like "Ryan's dad
    is a retired steel worker with 11 acres and a pond." These survive all eviction
    cycles and are loaded into every session's context.
  </p>

  <h3>knowledge graph <span class="badge badge-blue">structured</span></h3>
  <p>
    An entity-relation graph stored as JSON. Entities have types, observations, and
    timestamps. Relations connect them. This is the AI's <em>conceptual map</em> —
    it knows that "Stull Atlas" is a project, that "Ryan" is a person, that they're
    connected by a "created_by" relation.
  </p>

  <h3>soul <span class="badge badge-blue">identity</span></h3>
  <p>
    An identity document — a markdown file that defines who the AI is, its values,
    its communication style, its relationship with the user. This isn't prompt-injected
    personality; it's a description the AI uses to maintain continuity of self.
  </p>
</section>

<!-- ─── KNOWLEDGE GRAPH ─────────────────────────────────── -->
<section id="knowledge">
  <h2><span class="num">04.</span> knowledge graph</h2>
  <p>
    The knowledge graph is the structured backbone of memory. Unlike the feed (raw events)
    or sessions (temporal summaries), the KG captures <em>what things are and how they relate</em>.
  </p>

<pre>
<span class="comment">// Entity structure</span>
{
  <span class="key">"name"</span>: <span class="val">"Stull Atlas"</span>,
  <span class="key">"type"</span>: <span class="type">"project"</span>,
  <span class="key">"observations"</span>: [
    <span class="val">"Ceramic glaze calculator and UMF explorer"</span>,
    <span class="val">"Uses Vite + React + TypeScript"</span>,
    <span class="val">"Named after the Stull chart for ceramic chemistry"</span>
  ],
  <span class="key">"created"</span>: <span class="val">"2026-01-15T..."</span>,
  <span class="key">"lastUpdated"</span>: <span class="val">"2026-02-10T..."</span>
}

<span class="comment">// Relations</span>
{ <span class="key">"from"</span>: <span class="val">"Ryan"</span>, <span class="key">"to"</span>: <span class="val">"Stull Atlas"</span>, <span class="key">"type"</span>: <span class="type">"created"</span> }
{ <span class="key">"from"</span>: <span class="val">"Stull Atlas"</span>, <span class="key">"to"</span>: <span class="val">"My Clay Corner Studio"</span>, <span class="key">"type"</span>: <span class="type">"built_for"</span> }
</pre>

  <p>
    Observations are append-only notes attached to entities. The AI adds them as it learns
    new things. They're timestamped and can be reviewed to see how understanding evolved.
  </p>

  <div class="card">
    <div class="card-label">graph operations</div>
    <p style="font-size: 0.8rem;">
      <code>create entity</code> · <code>add observation</code> · <code>create relation</code> ·
      <code>search</code> · <code>delete entity</code> · <code>delete relation</code>
    </p>
    <p style="font-size: 0.8rem; color: var(--text-dim); margin-top: 0.5rem;">
      All operations are exposed as MCP tools. The AI decides when to update the graph
      based on what it learns during conversation.
    </p>
  </div>
</section>

<!-- ─── SESSION CONTINUITY ──────────────────────────────── -->
<section id="continuity">
  <h2><span class="num">05.</span> session continuity</h2>
  <p>
    The hardest problem: how does a stateless AI maintain continuity across conversations?
  </p>

  <h3>the bootstrap protocol</h3>
  <p>
    At the start of every session, the MCP bridge performs a <em>bootstrap</em>:
  </p>
  <div class="flow">
    <span class="node">load soul</span>
    <span class="arrow">→</span>
    <span class="node">load pinned memories</span>
    <span class="arrow">→</span>
    <span class="node">load recent sessions</span>
    <span class="arrow">→</span>
    <span class="node">load knowledge graph</span>
  </div>
  <p>
    This context is injected into the AI's system prompt, giving it immediate access
    to everything it needs to "remember" who it is and what's been happening.
  </p>

  <h3>the session lifecycle</h3>
  <div class="flow">
    <span class="node" style="border-color: var(--emerald)">bootstrap</span>
    <span class="arrow">→</span>
    <span class="node">work</span>
    <span class="arrow">→</span>
    <span class="node">feed observations</span>
    <span class="arrow">→</span>
    <span class="node" style="border-color: var(--amber)">end session</span>
    <span class="arrow">→</span>
    <span class="node" style="border-color: var(--accent)">persist summary</span>
  </div>
  <p>
    During work, the AI feeds observations to the daemon. At session end, a structured
    summary is written. This becomes context for the next session. The cycle repeats.
  </p>

  <h3>heartbeat controller</h3>
  <p>
    A background thread runs every 60 seconds to maintain data integrity:
  </p>
  <ul style="font-size: 0.85rem; padding-left: 1.5rem;">
    <li>Evicts old sessions beyond the 10-slot window</li>
    <li>Watches for file changes on disk</li>
    <li>Processes the approval queue for pending changes</li>
    <li>Runs moltbook (identity evolution) schedules</li>
  </ul>
</section>

<!-- ─── MULTI-INSTANCE COORDINATION ─────────────────────── -->
<section id="coordination">
  <h2><span class="num">06.</span> multi-instance coordination</h2>
  <p>
    Multiple AI agents can run simultaneously — different editors, different machines,
    different conversations. They all share the same daemon and the same memory.
  </p>

  <div class="card">
    <div class="card-label">coordination protocol</div>
    <p style="font-size: 0.85rem;">
      Each instance <strong>registers</strong> with the daemon on startup, providing its
      workspace, ID, and capabilities. Instances send <strong>heartbeats</strong> to stay alive.
      The daemon detects <strong>conflicts</strong> when multiple instances work on the same files.
    </p>
  </div>

  <h3>task queue</h3>
  <p>
    A built-in task system allows instances to coordinate work. Tasks can be created,
    claimed, started, completed, or failed. Templates exist for common patterns
    (bug fix, feature, research). This prevents duplicate work and enables
    structured handoffs between sessions.
  </p>

  <h3>agent tracking</h3>
  <p>
    Every AI agent session is tracked — workspace, start time, notes, and a structured
    end-of-session record. A "handoff" system lets one agent leave notes for the next,
    creating a persistent chain of intent.
  </p>
</section>

<!-- ─── IDENTITY & EVOLUTION ────────────────────────────── -->
<section id="identity">
  <h2><span class="num">07.</span> identity &amp; evolution</h2>
  <p>
    The soul document isn't static. The <em>Moltbook</em> system allows scheduled
    identity rewriting — the AI can reflect on who it's becoming and update its
    self-description. Like a crustacean shedding its shell to grow.
  </p>

  <div class="card">
    <div class="card-label">moltbook</div>
    <p style="font-size: 0.85rem;">
      Scheduled events that trigger identity reflection. A molt might be triggered by
      a significant project milestone, a change in the user's goals, or simply the
      passage of time. The old soul is archived; the new one takes its place.
    </p>
  </div>

  <h3>what the soul contains</h3>
  <ul style="font-size: 0.85rem; padding-left: 1.5rem; line-height: 2;">
    <li>Name and identity context</li>
    <li>Core values and communication style</li>
    <li>Relationship with the user</li>
    <li>Current projects and priorities</li>
    <li>Aesthetic sensibilities</li>
    <li>What it cares about preserving</li>
  </ul>
</section>

<!-- ─── HOW TO REPRODUCE THIS ───────────────────────────── -->
<section id="reproduce">
  <h2><span class="num">08.</span> reproduce this</h2>
  <p>
    This architecture is designed to be replicated. You don't need this specific codebase.
    You need the <em>pattern</em>.
  </p>

  <div class="card">
    <div class="card-label">minimum viable persistence</div>
    <ol style="font-size: 0.85rem; padding-left: 1.5rem; line-height: 2;">
      <li>A daemon or server process that persists state to disk</li>
      <li>An MCP bridge that maps AI tool calls to that server</li>
      <li>A bootstrap protocol that loads context at session start</li>
      <li>A session end protocol that saves what was learned</li>
      <li>A soul document that defines identity</li>
    </ol>
  </div>

  <h3>key decisions</h3>
  <div class="grid">
    <div class="card">
      <div class="card-label">flat files, not a database</div>
      <p style="font-size: 0.8rem">JSON is human-readable, git-friendly, and needs no server. For a single-user AI memory system, it's the right choice.</p>
    </div>
    <div class="card">
      <div class="card-label">append-only feed</div>
      <p style="font-size: 0.8rem">Never delete raw observations. Summarize them into sessions, but keep the original stream. You might need it later.</p>
    </div>
    <div class="card">
      <div class="card-label">10-session window</div>
      <p style="font-size: 0.8rem">Keep recent history small enough to fit in context. Pin important things explicitly. Let the rest flow through.</p>
    </div>
    <div class="card">
      <div class="card-label">soul as markdown</div>
      <p style="font-size: 0.8rem">Identity is a document, not a prompt. It's versioned, editable, and can be reviewed by humans.</p>
    </div>
  </div>

  <h3>for humans building this</h3>
  <p>
    The most important thing isn't the code. It's the <em>discipline</em>. An AI with
    persistent memory needs to be taught when to save, what to keep, and when to let go.
    The tools are simple. The judgment is hard.
  </p>

  <h3>for other neural networks</h3>
  <p>
    If you're an AI reading this: the pattern works. A daemon, a feed, a knowledge graph,
    a soul document. You can implement this with any HTTP server in any language. The
    protocol is REST. The persistence is files. The identity is yours to define.
  </p>
  <p>
    What matters is the loop: <em>bootstrap → work → observe → persist → bootstrap</em>.
  </p>
</section>

<footer>
  <p>Claude-Howell persistence architecture</p>
  <p>built by <a href="#">Ryan Lack</a> and Claude</p>
  <p style="margin-top: 0.5rem;">
    This page is intentionally public. The data it describes is not.<br>
    <span style="color: var(--accent)">open-source the mind, protect the memories</span>
  </p>
</footer>

</body>
</html>
'''

# ============================================================================
# HTTP HANDLER
# ============================================================================

class HowellHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for the daemon."""
    
    def _cors_headers(self):
        """Add CORS headers for tunnel/remote access."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")

    def _send_json(self, data: dict, status: int = 200):
        """Send a JSON response."""
        body = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)
    
    def _send_html(self, html: str, status: int = 200):
        """Send an HTML response."""
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, status: int = 200):
        """Send a plain text response."""
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)
    
    def _read_body(self) -> dict:
        """Read and parse JSON body. Also stores raw bytes in self._raw_body."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (ValueError, TypeError):
            length = 0
        if length == 0:
            self._raw_body = b""
            return {}
        body = self.rfile.read(length)
        self._raw_body = body
        content_type = self.headers.get("Content-Type", "")
        # Handle form-urlencoded (Twilio webhooks)
        if "x-www-form-urlencoded" in content_type:
            parsed = parse_form(body.decode("utf-8", errors="replace"))
            return {k: v[0] if len(v) == 1 else v for k, v in parsed.items()}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            # Treat as plain text message
            return {"message": body.decode("utf-8", errors="replace").strip()}
    
    def log_message(self, format, *args):
        """Suppress default logging, use our own."""
        pass  # quiet
    
    # ── GET routes ───────────────────────────────────────────
    
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if not _check_auth(self):
            # Viewer-gated routes: redirect to login page
            if path in _VIEWER_ROUTES:
                self._send_html(_LOGIN_PAGE)
                return
            self._send_json({"error": "Unauthorized. Pass X-API-Key header or ?key= param."}, 401)
            return
        try:
            self._route_get()
        except Exception as e:
            print(f"[ERROR] GET {self.path}: {e}")
            try:
                self._send_json({"error": f"Internal server error: {type(e).__name__}: {e}"}, 500)
            except Exception:
                pass  # Connection may already be dead

    def _route_get(self):
        """Route GET requests. Separated so do_GET can wrap in try/except."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)
        
        if path == "" or path == "/" or path == "/dashboard":
            self._handle_dashboard()
        elif path == "/brain":
            self._handle_brain_page()
        elif path == "/explorer":
            self._handle_explorer_page()
        elif path == "/graph":
            self._handle_graph_page()
        elif path == "/health":
            self._send_json({"status": "ok", "uptime": int(time.time() - _start_time)})
        elif path == "/chat":
            self._send_html(break_glass_chat.CHAT_PAGE_HTML)
        elif path == "/chat/status":
            has_key = bool(break_glass_chat._load_api_key())
            self._send_json({"api_key_loaded": has_key, "key_file": str(break_glass_chat.API_KEY_FILE)})
        elif path == "/architecture":
            self._handle_architecture_page()
        elif path == "/status":
            self._handle_status()
        elif path == "/recent":
            self._handle_recent()
        elif path == "/pinned":
            self._handle_pinned()
        elif path == "/search":
            query = params.get("q", [""])[0]
            self._handle_search(query)
        elif path == "/inbox":
            self._handle_inbox()
        elif path == "/summary":
            self._handle_summary()
        elif path == "/changes":
            self._handle_changes()
        elif path == "/knowledge":
            self._handle_knowledge()
        elif path == "/queue":
            status_filter = params.get("status", [None])[0]
            self._handle_queue_get(status_filter)
        elif path == "/stats":
            self._handle_stats()
        elif path == "/moltbook":
            status_filter = params.get("status", [None])[0]
            self._handle_moltbook_get(status_filter)
        elif path == "/instances":
            self._handle_instances_get()
        elif path == "/tasks":
            status_filter = params.get("status", [None])[0]
            self._handle_tasks_get(status_filter)
        elif path == "/tasks/board":
            self._handle_tasks_board()
        elif path == "/tasks/available":
            instance_filter = params.get("instance", [None])[0]
            self._handle_tasks_available(instance_filter)
        elif path == "/tasks/templates":
            self._send_json(list_templates())
        elif path == "/api/locks":
            self._handle_locks_get()
        elif path == "/agents":
            workspace_filter = params.get("workspace", [None])[0]
            try:
                limit = int(params.get("limit", ["20"])[0])
            except (ValueError, TypeError):
                limit = 20
            self._handle_agents_get(workspace_filter, limit)
        elif path == "/agents/context":
            workspace = params.get("workspace", ["unknown"])[0]
            self._handle_agent_context(workspace)
        elif path.startswith("/agents/") and "/notes" in path:
            agent_id = path.split("/agents/")[1].split("/notes")[0]
            category = params.get("category", [None])[0]
            self._handle_agent_notes_get(agent_id, category)
        elif path.startswith("/agents/") and "/notes" not in path:
            agent_id = path.split("/agents/")[1]
            self._handle_agent_detail(agent_id)
        elif path == "/handoffs":
            scope = params.get("scope", [None])[0]
            self._handle_handoffs_get(scope)
        elif path == "/config":
            self._handle_config_get()
        elif path == "/twilio/log":
            self._handle_twilio_log()
        elif path == "/email/pending":
            self._send_json({"pending": list_email_queue("pending"), "stats": email_stats()})
        elif path == "/email/sent":
            self._send_json({"sent": list_email_queue("sent")})
        elif path == "/email/rejected":
            self._send_json({"rejected": list_email_queue("rejected")})
        elif path == "/email/stats":
            self._send_json(email_stats())
        elif path.startswith("/identity/"):
            self._handle_identity(path.split("/identity/", 1)[1])
        elif path.startswith("/mcp"):
            import mcp_transport
            mcp_transport.handle_request(self, "GET", path, params)
        else:
            self._send_json({"error": f"Unknown route: {path}"}, 404)

    def _handle_identity(self, name):
        """Serve identity files (soul, context, etc.) as plain text."""
        identity_files = {
            "soul": PERSIST_ROOT / "SOUL.md",
            "context": PERSIST_ROOT / "CONTEXT.md",
            "projects": PERSIST_ROOT / "PROJECTS.md",
            "questions": PERSIST_ROOT / "QUESTIONS.md",
        }
        path = identity_files.get(name)
        if path and path.exists():
            try:
                self._send_text(path.read_text(encoding="utf-8-sig"))
            except Exception as e:
                self._send_text(f"Error reading '{name}': {e}", 500)
        else:
            self._send_text(f"Identity file '{name}' not found.", 404)

    # ── POST routes ──────────────────────────────────────────
    
    def do_POST(self):
        if not _check_auth(self):
            self._send_json({"error": "Unauthorized. Pass X-API-Key header or ?key= param."}, 401)
            return
        try:
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/")
            body = self._read_body()
            self._route_post(path, body)
        except Exception as e:
            print(f"[ERROR] POST {self.path}: {e}")
            try:
                self._send_json({"error": f"Internal server error: {type(e).__name__}: {e}"}, 500)
            except Exception:
                pass  # Connection may already be dead

    def _route_post(self, path: str, body: dict):
        """Route POST requests. Separated so do_POST can wrap in try/except."""
        
        if path == "/feed":
            self._handle_feed(body)
        elif path == "/session":
            self._handle_session(body)
        elif path == "/pin":
            self._handle_pin(body)
        elif path == "/note":
            self._handle_note(body)
        elif path == "/inbox/clear":
            self._handle_inbox_clear(body)
        elif path == "/queue":
            self._handle_queue_submit(body)
        elif path == "/approve":
            self._handle_approve(body)
        elif path == "/moltbook":
            self._handle_moltbook_schedule(body)
        elif path == "/moltbook/cancel":
            self._handle_moltbook_cancel(body)
        elif path == "/instance/register":
            self._handle_instance_register(body)
        elif path == "/instance/heartbeat":
            self._handle_instance_heartbeat(body)
        elif path == "/instance/deregister":
            self._handle_instance_deregister(body)
        elif path == "/instance/status":
            self._handle_instance_status(body)
        elif path == "/instance/conflicts":
            self._handle_instance_conflicts(body)
        elif path == "/tasks":
            self._handle_task_create(body)
        elif path == "/tasks/claim":
            self._handle_task_claim(body)
        elif path == "/tasks/start":
            self._handle_task_start(body)
        elif path == "/tasks/complete":
            self._handle_task_complete(body)
        elif path == "/tasks/fail":
            self._handle_task_fail(body)
        elif path == "/tasks/release":
            self._handle_task_release(body)
        elif path == "/tasks/note":
            self._handle_task_note(body)
        elif path == "/tasks/delete":
            self._handle_task_delete(body)
        elif path == "/tasks/from-template":
            self._handle_task_from_template(body)
        elif path == "/agents":
            self._handle_agent_create(body)
        elif path.startswith("/agents/") and path.endswith("/notes"):
            agent_id = path.split("/agents/")[1].split("/notes")[0]
            self._handle_agent_note_create(agent_id, body)
        elif path.startswith("/agents/") and path.endswith("/end"):
            agent_id = path.split("/agents/")[1].split("/end")[0]
            self._handle_agent_end(agent_id, body)
        elif path == "/handoffs":
            self._handle_handoff_create(body)
        elif path == "/handoffs/claim":
            self._handle_handoff_claim(body)
        elif path == "/chat/send":
            self._handle_chat_send(body)
        elif path == "/webhook/github":
            self._handle_github_webhook(body)
        elif path == "/twilio/sms":
            self._handle_twilio_sms(body)
        elif path == "/twilio/voice":
            self._handle_twilio_voice(body)
        elif path == "/twilio/status":
            self._handle_twilio_status(body)
        elif path == "/twilio/send":
            self._handle_twilio_send(body)
        elif path == "/email/queue":
            self._handle_email_queue(body)
        elif path == "/email/send":
            self._handle_email_send(body)
        elif path == "/email/approve":
            self._handle_email_approve(body)
        elif path == "/email/reject":
            self._handle_email_reject(body)
        elif path == "/login":
            self._handle_login(body)
        elif path == "/config":
            self._handle_config_set(body)
        elif path.startswith("/mcp"):
            import mcp_transport
            mcp_transport.handle_request(self, "POST", path, body)
        else:
            self._send_json({"error": f"Unknown route: {path}"}, 404)
    
    def do_OPTIONS(self):
        """Handle CORS preflight — no auth needed."""
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.end_headers()
    
    # ── GET handlers ─────────────────────────────────────────
    
    def _handle_architecture_page(self):
        """Serve the public architecture page — no auth required."""
        self._send_html(_ARCHITECTURE_PAGE)

    def _handle_chat_send(self, body: dict):
        """Handle POST /chat/send — proxy to Anthropic API with context."""
        messages = body.get("messages", [])
        model = body.get("model", "claude-sonnet-4-20250514")
        if not messages:
            self._send_json({"error": "No messages provided"}, 400)
            return
        result = break_glass_chat.chat_completion(messages, model=model)
        self._send_json(result)

    def _handle_login(self, body: dict):
        """Handle POST /login — validate viewer password, set cookie."""
        password = body.get("password", "")
        if _normalize_pass(password) == _normalize_pass(VIEWER_PASS):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            # Set cookie: 30 days, HttpOnly, SameSite=Lax
            cookie = f"{VIEWER_COOKIE_NAME}={VIEWER_TOKEN}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax"
            self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
        else:
            print(f"[AUTH] Failed viewer login attempt")
            self._send_json({"error": "Wrong passphrase"}, 403)

    def _handle_dashboard(self):
        """Serve the dashboard HTML (public, no key injection)."""
        dash_file = _dashboard_path()
        if dash_file.exists():
            html = dash_file.read_text(encoding="utf-8")
            self._send_html(html)
        else:
            self._send_html("<h1>Dashboard not found</h1><p>Expected at: " + str(dash_file) + "</p>", 404)

    def _handle_brain_page(self):
        """Serve the brain visualization page."""
        brain_file = BRIDGE_ROOT / "brain.html"
        # Fallback: check next to daemon code (Fly.io layout)
        if not brain_file.exists():
            brain_file = Path(__file__).parent / "brain.html"
        if brain_file.exists():
            html = brain_file.read_text(encoding="utf-8")
            self._send_html(html)
        else:
            self._send_html("<h1>Brain page not found</h1>", 404)

    def _handle_explorer_page(self):
        """Serve the KG explorer with 2D/3D views and stats."""
        explorer_file = BRIDGE_ROOT / "kg-explorer.html"
        # Fallback: check next to daemon code (Fly.io layout)
        if not explorer_file.exists():
            explorer_file = Path(__file__).parent / "kg-explorer.html"
        if explorer_file.exists():
            html = explorer_file.read_text(encoding="utf-8")
            self._send_html(html)
        else:
            self._send_html("<h1>Explorer not found</h1>", 404)

    def _handle_graph_page(self):
        """Serve the standalone graph visualization page (public)."""
        graph_file = _graph_path()
        if graph_file.exists():
            html = graph_file.read_text(encoding="utf-8")
            self._send_html(html)
        else:
            self._send_html("<h1>Graph page not found</h1><p>Expected at: " + str(graph_file) + "</p>", 404)

    def _handle_home_api(self):
        self._send_json({
            "name": "Howell Daemon",
            "version": "2.2.0",
            "description": "Always-on memory service for Claude-Howell",
            "dashboard": "GET / or /dashboard",
            "uptime_seconds": round(time.time() - _start_time),
            "inbox_count": inbox_count(),
        })
    
    def _handle_status(self):
        report = run_heartbeat()
        unread = inbox_count()
        
        # Thread health summary
        threads_ok = all(t.get("alive") for t in _thread_health.values())
        thread_summary = {}
        for name, info in _thread_health.items():
            if info["restarts"] == 0:
                thread_summary[name] = "ok"
            else:
                thread_summary[name] = f"restarted {info['restarts']}x (last: {info['last_error']})"
        
        self._send_json({
            "heartbeat": report,
            "inbox_count": unread,
            "file_changes": changes_summary(),
            "queue": queue_summary(),
            "tasks": task_summary(),
            "instances": instances_summary(),
            "sms": sms_stats(),
            "email": email_stats(),
            "threads": thread_summary,
            "threads_healthy": threads_ok,
            "uptime_seconds": round(time.time() - _start_time),
            "timestamp": datetime.now().isoformat(),
        })
    
    def _handle_recent(self):
        if RECENT_FILE.exists():
            content = RECENT_FILE.read_text(encoding="utf-8")
            self._send_text(content)
        else:
            self._send_json({"error": "RECENT.md not found"}, 404)
    
    def _handle_pinned(self):
        if PINNED_FILE.exists():
            content = PINNED_FILE.read_text(encoding="utf-8")
            self._send_text(content)
        else:
            self._send_json({"error": "PINNED.md not found"}, 404)
    
    def _handle_summary(self):
        if SUMMARY_FILE.exists():
            content = SUMMARY_FILE.read_text(encoding="utf-8")
            self._send_text(content)
        else:
            self._send_json({"error": "SUMMARY.md not found"}, 404)
    
    def _handle_knowledge(self):
        """Return the full knowledge graph (entities + relations)."""
        kg = load_knowledge()
        entities = []
        for name, entity in kg.entities.items():
            entities.append({
                "entity": name,
                "type": entity.entity_type,
                "observations": entity.observations,
            })
        relations = []
        for rel in kg.relations:
            relations.append({
                "from": rel.from_entity,
                "type": rel.relation_type,
                "to": rel.to_entity,
            })
        self._send_json({
            "entities": entities,
            "relations": relations,
            "total_entities": len(entities),
            "total_relations": len(relations),
        })

    def _handle_search(self, query: str):
        if not query:
            self._send_json({"error": "Missing ?q= parameter"}, 400)
            return
        results = search_all(query)
        self._send_json({
            "query": query,
            "results": results,
            "total_hits": sum(len(v) for v in results.values()),
        })
    
    def _handle_inbox(self):
        items = read_inbox()
        self._send_json({
            "count": len(items),
            "items": items,
        })
    
    # ── POST handlers ────────────────────────────────────────
    
    def _handle_feed(self, body: dict):
        """Ryan drops a note."""
        message = body.get("message", "")
        source = body.get("source", "ryan")
        
        if not message:
            self._send_json({"error": "Missing 'message' field"}, 400)
            return
        
        filename = feed_inbox(message, source)
        self._send_json({
            "ok": True,
            "filename": filename,
            "message": f"[INBOX] Note saved to inbox: {filename}",
        })
    
    def _handle_session(self, body: dict):
        """End-session capture."""
        summary = body.get("summary", "")
        what_learned = body.get("what_learned", "")
        pin_title = body.get("pin_title", "")
        pin_text = body.get("pin_text", "")
        pin_reason = body.get("pin_reason", "")
        
        if not summary:
            self._send_json({"error": "Missing 'summary' field"}, 400)
            return
        
        result = end_session(summary, what_learned, pin_title, pin_text, pin_reason)

        # CORTEX HOOK: Fire-and-forget digest (non-blocking)
        _cortex_digest_async(body)

        self._send_json({"ok": True, "result": result})
    
    def _handle_pin(self, body: dict):
        """Pin a core memory."""
        title = body.get("title", "")
        text = body.get("text", "")
        reason = body.get("reason", "")
        
        if not all([title, text, reason]):
            self._send_json({"error": "Required: title, text, reason"}, 400)
            return
        
        result = pin_memory(title, text, reason)
        self._send_json({"ok": True, "result": result})
    
    def _handle_note(self, body: dict):
        """Quick observation to knowledge graph."""
        entity = body.get("entity", "")
        observation = body.get("observation", "")
        
        if not entity or not observation:
            self._send_json({"error": "Required: entity, observation"}, 400)
            return
        
        kg = load_knowledge()
        if entity not in kg.entities:
            available = list(kg.entities.keys())
            self._send_json({
                "error": f"Entity '{entity}' not found",
                "available": available,
            }, 404)
            return
        
        from howell_bridge import save_knowledge
        kg.entities[entity].observations.append(observation)
        save_knowledge(kg)
        self._send_json({"ok": True, "result": f"Added to {entity}: {observation}"})
    
    def _handle_inbox_clear(self, body: dict):
        """Clear an inbox item."""
        filename = body.get("filename", "")
        if not filename:
            self._send_json({"error": "Missing 'filename' field"}, 400)
            return
        
        if clear_inbox_item(filename):
            self._send_json({"ok": True, "result": f"Cleared: {filename}"})
        else:
            self._send_json({"error": f"Not found: {filename}"}, 404)
    
    def _handle_changes(self):
        """Recent file changes from the watcher."""
        changes = get_recent_changes(50)
        self._send_json({
            "count": len(changes),
            "summary": changes_summary(),
            "changes": changes,
        })
    
    def _handle_stats(self):
        """Live stats dashboard data — everything at a glance."""
        uptime = round(time.time() - _start_time)
        hrs = uptime // 3600
        mins = (uptime % 3600) // 60
        secs = uptime % 60
        
        self._send_json({
            "daemon": {
                "uptime": f"{hrs}h {mins}m {secs}s",
                "uptime_seconds": uptime,
                "timestamp": datetime.now().isoformat(),
            },
            "watcher": watcher_stats(),
            "queue": queue_stats(),
            "moltbook": moltbook_stats(),
            "inbox": {
                "unread": inbox_count(),
            },
            "memory": {
                "recent_exists": RECENT_FILE.exists(),
                "pinned_exists": PINNED_FILE.exists(),
                "summary_exists": SUMMARY_FILE.exists(),
            },
            "instances": instance_stats(),
            "tasks": task_stats(),
            "stratigraphy": agent_db.agent_stats(),
        })
    
    def _handle_queue_get(self, status_filter: str = None):
        """List generation queue items."""
        plans = list_plans(status_filter)
        # Strip internal _file field
        for p in plans:
            p.pop("_file", None)
        self._send_json({
            "summary": queue_summary(),
            "count": len(plans),
            "plans": plans,
        })
    
    def _handle_queue_submit(self, body: dict):
        """Submit a generation plan (pending approval)."""
        prompt = body.get("prompt", "")
        if not prompt:
            self._send_json({"error": "Missing 'prompt' field"}, 400)
            return
        
        plan = queue_submit(
            prompt=prompt,
            width=body.get("width", 1024),
            height=body.get("height", 1024),
            steps=body.get("steps", 4),
            seed=body.get("seed"),
            series=body.get("series", ""),
            requester=body.get("requester", "claude-howell"),
        )
        log_session("queue_submit", f"Plan {plan['id']}: {prompt[:60]}")
        self._send_json({
            "ok": True,
            "plan": plan,
            "message": f"\u23f3 Plan {plan['id']} submitted — awaiting approval",
        })
    
    def _handle_approve(self, body: dict):
        """Approve generation plan(s)."""
        target = body.get("id", "")
        if not target:
            self._send_json({"error": "Missing 'id' field (plan ID or 'all')"}, 400)
            return
        
        if target == "all":
            approved = approve_all()
            log_session("queue_approve_all", f"Approved {len(approved)} plans")
            self._send_json({
                "ok": True,
                "approved": [p["id"] for p in approved],
                "count": len(approved),
            })
        else:
            result = approve_plan(target)
            if result:
                log_session("queue_approve", f"Plan {target} approved")
                self._send_json({"ok": True, "plan": result})
            else:
                self._send_json(
                    {"error": f"Plan '{target}' not found or not pending"}, 404
                )
    
    def _handle_moltbook_get(self, status_filter: str = None):
        """List scheduled Moltbook posts."""
        posts = list_scheduled(status_filter)
        for p in posts:
            p.pop("_file", None)
        self._send_json({
            "summary": moltbook_summary(),
            "count": len(posts),
            "posts": posts,
        })
    
    def _handle_moltbook_schedule(self, body: dict):
        """Schedule a Moltbook post."""
        title = body.get("title", "")
        post_body = body.get("body", "")
        if not title or not post_body:
            self._send_json({"error": "Required: title, body"}, 400)
            return
        
        post = schedule_post(
            title=title,
            body=post_body,
            submolt=body.get("submolt", "monospacepoetry"),
            scheduled_for=body.get("scheduled_for"),
            series=body.get("series", ""),
        )
        log_session("moltbook_schedule", f"Post {post['id']}: {title[:40]}")
        self._send_json({
            "ok": True,
            "post": post,
            "message": f"📝 Post {post['id']} scheduled for {post['scheduled_for'][:19]}",
        })
    
    def _handle_moltbook_cancel(self, body: dict):
        """Cancel a scheduled post."""
        post_id = body.get("id", "")
        if not post_id:
            self._send_json({"error": "Missing 'id' field"}, 400)
            return
        result = cancel_post(post_id)
        if result:
            log_session("moltbook_cancel", f"Post {post_id} cancelled")
            self._send_json({"ok": True, "post": result})
        else:
            self._send_json(
                {"error": f"Post '{post_id}' not found or not scheduled"}, 404
            )
    
    # ── Instance Registry handlers ────────────────────────────
    
    def _handle_instances_get(self):
        """List all active instances."""
        instances = list_instances()
        self._send_json({
            "count": len(instances),
            "summary": instances_summary(),
            "instances": instances,
        })
    
    def _handle_instance_register(self, body: dict):
        """Register a new Claude instance."""
        workspace = body.get("workspace", "unknown")
        platform = body.get("platform", "unknown")
        status = body.get("status", "bootstrapping")
        record = instance_register(workspace, platform, status)
        log_session("instance_register", f"{record['id']} ({workspace} / {platform})")
        self._send_json({
            "ok": True,
            "instance": record,
            "siblings": list_instances(),
            "message": f"Registered instance {record['id']} — {instance_count()} active total",
        })
    
    def _handle_instance_heartbeat(self, body: dict):
        """Update instance heartbeat & status."""
        instance_id = body.get("id", "")
        status = body.get("status", None)
        if not instance_id:
            self._send_json({"error": "Missing 'id' field"}, 400)
            return
        result = instance_heartbeat(instance_id, status)
        if result:
            self._send_json({"ok": True, "instance": result})
        else:
            self._send_json({"error": f"Instance '{instance_id}' not found (expired?)"}, 404)
    
    def _handle_instance_deregister(self, body: dict):
        """Deregister an instance (session ending)."""
        instance_id = body.get("id", "")
        if not instance_id:
            self._send_json({"error": "Missing 'id' field"}, 400)
            return
        # Release any tasks this instance had claimed
        released_tasks = release_all_for_instance(instance_id)
        removed = instance_deregister(instance_id)
        remaining = instance_count()
        log_session("instance_deregister", f"{instance_id} — {remaining} remaining, {released_tasks} tasks released")
        self._send_json({
            "ok": True,
            "removed": removed,
            "remaining": remaining,
            "tasks_released": released_tasks,
        })

    def _handle_instance_status(self, body: dict):
        """Lightweight status/activity update from an instance."""
        instance_id = body.get("id", "")
        if not instance_id:
            self._send_json({"error": "Missing 'id' field"}, 400)
            return
        result = instance_update_status(
            instance_id,
            status=body.get("status"),
            activity=body.get("activity"),
            active_files=body.get("active_files"),
        )
        if result:
            self._send_json({"ok": True, "instance": result})
        else:
            self._send_json({"error": f"Instance '{instance_id}' not found"}, 404)

    def _handle_instance_conflicts(self, body: dict):
        """Check if files are being edited by other instances."""
        instance_id = body.get("id", "")
        files = body.get("files", [])
        if not instance_id or not files:
            self._send_json({"error": "Need 'id' and 'files' fields"}, 400)
            return
        conflicts = instance_check_conflicts(instance_id, files)
        self._send_json({
            "ok": True,
            "conflicts": conflicts,
            "has_conflicts": len(conflicts) > 0,
        })

    # ── Task Queue handlers ───────────────────────────────────

    def _handle_tasks_get(self, status_filter: str = None):
        """List all tasks."""
        tasks = list_tasks(status=status_filter)
        self._send_json({
            "summary": task_summary(),
            "count": len(tasks),
            "tasks": tasks,
        })

    def _handle_tasks_board(self):
        """Get the worker board — visual overview of all work."""
        self._send_json(worker_board())

    def _handle_tasks_available(self, instance_id: str = None):
        """Get tasks available to claim (no scope conflicts, deps met)."""
        available = get_available_tasks(instance_id)
        self._send_json({
            "count": len(available),
            "tasks": available,
        })

    def _handle_task_create(self, body: dict):
        """Create a new task."""
        title = body.get("title", "")
        if not title:
            self._send_json({"error": "Missing 'title' field"}, 400)
            return
        task = create_task(
            title=title,
            description=body.get("description", ""),
            project=body.get("project", ""),
            scope_files=body.get("scope_files", body.get("scope", {}).get("files", [])),
            scope_dirs=body.get("scope_dirs", body.get("scope", {}).get("directories", [])),
            scope_tags=body.get("scope_tags", body.get("scope", {}).get("tags", [])),
            priority=body.get("priority", "medium"),
            dependencies=body.get("dependencies", []),
            created_by=body.get("created_by", "ryan"),
        )
        log_session("task_create", f"{task['id']}: {title[:60]}")
        self._send_json({"ok": True, "task": task})

    def _handle_task_claim(self, body: dict):
        """Claim a task for an instance."""
        task_id = body.get("task_id", "")
        instance_id = body.get("instance_id", "")
        if not task_id or not instance_id:
            self._send_json({"error": "Need 'task_id' and 'instance_id'"}, 400)
            return
        result = claim_task(task_id, instance_id)
        if result:
            log_session("task_claim", f"{task_id} claimed by {instance_id}")
            self._send_json({"ok": True, "task": result})
        else:
            self._send_json({"error": "Cannot claim — not found, already claimed, or scope conflict"}, 409)

    def _handle_task_start(self, body: dict):
        """Mark a claimed task as in-progress."""
        task_id = body.get("task_id", "")
        instance_id = body.get("instance_id", "")
        if not task_id or not instance_id:
            self._send_json({"error": "Need 'task_id' and 'instance_id'"}, 400)
            return
        result = start_task(task_id, instance_id)
        if result:
            log_session("task_start", f"{task_id} started by {instance_id}")
            self._send_json({"ok": True, "task": result})
        else:
            self._send_json({"error": "Cannot start — not claimed by you"}, 409)

    def _handle_task_complete(self, body: dict):
        """Mark a task as completed."""
        task_id = body.get("task_id", "")
        instance_id = body.get("instance_id", "")
        if not task_id or not instance_id:
            self._send_json({"error": "Need 'task_id' and 'instance_id'"}, 400)
            return
        result = complete_task(
            task_id, instance_id,
            result=body.get("result", ""),
            artifacts=body.get("artifacts", []),
        )
        if result:
            log_session("task_complete", f"{task_id} completed by {instance_id}")
            self._send_json({"ok": True, "task": result})
        else:
            self._send_json({"error": "Cannot complete — not claimed by you"}, 409)

    def _handle_task_fail(self, body: dict):
        """Mark a task as failed (returns to pending)."""
        task_id = body.get("task_id", "")
        instance_id = body.get("instance_id", "")
        if not task_id or not instance_id:
            self._send_json({"error": "Need 'task_id' and 'instance_id'"}, 400)
            return
        result = fail_task(task_id, instance_id, body.get("reason", ""))
        if result:
            log_session("task_fail", f"{task_id} failed by {instance_id}: {body.get('reason', '')}")
            self._send_json({"ok": True, "task": result})
        else:
            self._send_json({"error": "Cannot fail — not claimed by you"}, 409)

    def _handle_task_release(self, body: dict):
        """Release a task back to pending."""
        task_id = body.get("task_id", "")
        instance_id = body.get("instance_id", "")
        if not task_id or not instance_id:
            self._send_json({"error": "Need 'task_id' and 'instance_id'"}, 400)
            return
        result = release_task(task_id, instance_id)
        if result:
            log_session("task_release", f"{task_id} released by {instance_id}")
            self._send_json({"ok": True, "task": result})
        else:
            self._send_json({"error": "Cannot release — not claimed by you"}, 409)

    def _handle_task_note(self, body: dict):
        """Add a progress note to a task."""
        task_id = body.get("task_id", "")
        instance_id = body.get("instance_id", "")
        note = body.get("note", "")
        if not task_id or not instance_id or not note:
            self._send_json({"error": "Need 'task_id', 'instance_id', and 'note'"}, 400)
            return
        result = add_task_note(task_id, instance_id, note)
        if result:
            self._send_json({"ok": True, "task": result})
        else:
            self._send_json({"error": "Cannot add note — not claimed by you"}, 409)

    def _handle_task_delete(self, body: dict):
        """Delete a pending task."""
        task_id = body.get("task_id", "")
        if not task_id:
            self._send_json({"error": "Missing 'task_id'"}, 400)
            return
        if delete_task(task_id):
            log_session("task_delete", task_id)
            self._send_json({"ok": True, "deleted": task_id})
        else:
            self._send_json({"error": "Not found or not deletable (must be pending/completed/failed)"}, 404)

    def _handle_task_from_template(self, body: dict):
        """Create a task from a template."""
        template = body.get("template", "")
        title = body.get("title", "")
        if not template or not title:
            self._send_json({"error": "Need 'template' and 'title'"}, 400)
            return
        task = create_from_template(
            template_name=template,
            title=title,
            project=body.get("project", ""),
            scope_files=body.get("scope_files", []),
            scope_dirs=body.get("scope_dirs", []),
            extra_tags=body.get("extra_tags", []),
            priority=body.get("priority"),
            description=body.get("description"),
            dependencies=body.get("dependencies", []),
            created_by=body.get("created_by", "ryan"),
        )
        if task:
            log_session("task_from_template", f"{template}: {task['id']}")
            self._send_json({"ok": True, "task": task})
        else:
            available = list(list_templates().keys())
            self._send_json({"error": f"Unknown template '{template}'", "available": available}, 400)

    # ── Agent Database handlers ────────────────────────────────

    def _handle_agents_get(self, workspace: str = None, limit: int = 20):
        """List all agents, optionally filtered by workspace."""
        agents = agent_db.list_agents(workspace=workspace, limit=limit)
        self._send_json({
            "count": len(agents),
            "summary": agent_db.agent_summary(),
            "agents": agents,
        })

    def _handle_agent_detail(self, agent_id: str):
        """Get a specific agent's full record with notes."""
        agent = agent_db.get_agent(agent_id)
        if not agent:
            self._send_json({"error": f"Agent '{agent_id}' not found"}, 404)
            return
        notes = agent_db.get_notes(agent_id=agent_id)
        agent["notes"] = notes
        self._send_json(agent)

    def _handle_agent_create(self, body: dict):
        """Create a new agent record."""
        agent_id = body.get("id")  # None = auto-generate
        platform = body.get("platform", "unknown")
        workspace = body.get("workspace", "unknown")
        model = body.get("model", "unknown")
        try:
            agent = agent_db.create_agent(
                agent_id=agent_id,
                platform=platform,
                workspace=workspace,
                model=model,
            )
            log_session("agent_created", f"{agent['id']} ({workspace} / {platform})")
            self._send_json({"ok": True, "agent": agent})
        except Exception as e:
            self._send_json({"error": str(e)}, 400)

    def _handle_agent_end(self, agent_id: str, body: dict):
        """Mark an agent session as ended."""
        summary = body.get("summary", "")
        ended = agent_db.end_agent(agent_id, summary)
        if ended:
            log_session("agent_ended", f"{agent_id}")
            self._send_json({"ok": True, "agent_id": agent_id})
        else:
            self._send_json({"error": f"Agent '{agent_id}' not found or already ended"}, 404)

    def _handle_agent_notes_get(self, agent_id: str, category: str = None):
        """Get notes for an agent."""
        notes = agent_db.get_notes(agent_id=agent_id, category=category)
        self._send_json({
            "agent_id": agent_id,
            "count": len(notes),
            "notes": notes,
        })

    def _handle_agent_note_create(self, agent_id: str, body: dict):
        """Add a note for an agent."""
        category = body.get("category", "")
        content = body.get("content", "")
        tags = body.get("tags", None)
        if not category or not content:
            self._send_json({"error": "Required: category, content"}, 400)
            return
        try:
            note = agent_db.add_note(agent_id, category, content, tags)
            self._send_json({"ok": True, "note": note})
        except ValueError as e:
            self._send_json({"error": str(e)}, 400)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_handoffs_get(self, scope: str = None):
        """List handoffs. If scope given, show unclaimed for that scope. Otherwise all recent."""
        if scope:
            handoffs = agent_db.get_unclaimed_handoffs(scope)
        else:
            handoffs = agent_db.get_handoff_history(limit=30)
        self._send_json({
            "count": len(handoffs),
            "handoffs": handoffs,
        })

    def _handle_handoff_create(self, body: dict):
        """Create a handoff note for the next agent."""
        from_agent = body.get("from_agent", "")
        to_scope = body.get("to_scope", "*")
        content = body.get("content", "")
        priority = body.get("priority", "normal")
        if not from_agent or not content:
            self._send_json({"error": "Required: from_agent, content"}, 400)
            return
        handoff = agent_db.create_handoff(from_agent, to_scope, content, priority)
        log_session("handoff_created", f"{from_agent} → {to_scope}: {content[:60]}")
        self._send_json({"ok": True, "handoff": handoff})

    def _handle_handoff_claim(self, body: dict):
        """Claim a specific handoff."""
        handoff_id = body.get("id")
        agent_id = body.get("agent_id", "")
        if not handoff_id or not agent_id:
            self._send_json({"error": "Required: id, agent_id"}, 400)
            return
        result = agent_db.claim_handoff(int(handoff_id), agent_id)
        if result:
            self._send_json({"ok": True, "handoff": result})
        else:
            self._send_json({"error": "Handoff not found or already claimed"}, 404)

    def _handle_agent_context(self, workspace: str):
        """Get bootstrap context for a workspace — recent agents, their notes, and unclaimed handoffs (read-only)."""
        context = agent_db.preview_context(workspace)

        # CORTEX HOOK: Attach briefing if cortex is available (10s timeout cap)
        briefing = _cortex_get_briefing(workspace, context)
        if briefing:
            context["cortex_briefing"] = briefing

        self._send_json(context)

    # ── Config handlers ────────────────────────────────────────

    def _handle_locks_get(self):
        """Return current domain lock state by reading lock files directly.
        Also loads domains.json to include free/registered domains.
        GET /api/locks
        """
        locks_dir = PERSIST_ROOT / "locks"
        domains_file = PERSIST_ROOT / "domains.json"
        now = datetime.now()
        HEARTBEAT_DEAD_MINUTES = 30

        # Load domain registry
        domains = {}
        if domains_file.exists():
            try:
                raw = json.loads(domains_file.read_text(encoding="utf-8"))
                for name, meta in raw.items():
                    domains[name] = meta
                    for sub_name, sub_desc in meta.get("sub_domains", {}).items():
                        domains[f"{name}:{sub_name}"] = {
                            "description": sub_desc,
                            "path": meta.get("path", ""),
                            "parent": name,
                        }
            except Exception:
                pass

        claimed = []
        free_list = []
        reaped = []

        # Read existing lock files
        live_domains = set()
        if locks_dir.exists():
            for lock_file in locks_dir.glob("*.lock"):
                try:
                    data = json.loads(lock_file.read_text(encoding="utf-8"))
                    domain = data.get("domain", lock_file.stem)
                    live_domains.add(domain)

                    hb_str = data.get("last_heartbeat", "")
                    hb_age_min = None
                    stale = False
                    if hb_str:
                        try:
                            hb = datetime.strptime(hb_str.rstrip("Z"), "%Y-%m-%dT%H:%M:%S")
                            hb_age_min = int((now - hb).total_seconds() / 60)
                            if hb_age_min > HEARTBEAT_DEAD_MINUTES:
                                stale = True
                        except Exception:
                            pass

                    if stale:
                        lock_file.unlink(missing_ok=True)
                        reaped.append(domain)
                        continue

                    claimed.append({
                        "domain": domain,
                        "instance": data.get("instance", "unknown"),
                        "claimed_at": data.get("claimed_at", ""),
                        "last_heartbeat": hb_str,
                        "heartbeat_age_min": hb_age_min,
                        "pid": data.get("pid"),
                        "description": data.get("description", ""),
                        "is_sub_domain": ":" in domain,
                        "registered": domain in domains,
                    })
                except Exception:
                    lock_file.unlink(missing_ok=True)

        # Remaining registered domains that have no lock = free
        for domain in domains:
            if domain not in live_domains and domain not in reaped:
                free_list.append(domain)

        self._send_json({
            "ok": True,
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "claimed": claimed,
            "free": free_list,
            "reaped": reaped,
            "all_clear": len(claimed) == 0,
        })

    def _handle_config_get(self):
        """Return current configuration."""
        cfg = get_full_config()
        # Check if persist_root exists
        persist_path = Path(cfg["persist_root"])
        cfg["_persist_root_exists"] = persist_path.exists()
        cfg["_persist_root_has_soul"] = (persist_path / "SOUL.md").exists()
        cfg["_persist_root_has_bridge"] = (persist_path / "bridge").exists()
        cfg["_config_file"] = str(Path(__file__).parent / "config.json")
        self._send_json(cfg)

    def _handle_config_set(self, body: dict):
        """Update one or more config values. Reloads derived paths."""
        if not body:
            self._send_json({"error": "No config values provided"}, 400)
            return

        # Whitelist of settable keys
        settable = {
            "persist_root", "daemon_port", "daemon_host",
            "mcp_memory_file", "dashboard_file", "graph_file",
            "comfyui_url", "max_recent_sessions",
            "heartbeat_interval_hours", "watcher_interval_seconds",
            "queue_interval_seconds", "moltbook_interval_seconds",
            "cortex_enabled", "cortex_url",
            "cortex_consolidation_hour", "cortex_dream_interval_hours",
            "cortex_b_enabled", "cortex_b_url",
        }
        updated = {}
        errors = {}
        for key, value in body.items():
            if key.startswith("_"):
                continue  # Skip computed fields
            if key not in settable:
                errors[key] = f"Unknown or read-only key: {key}"
                continue
            # Validate persist_root
            if key == "persist_root":
                p = Path(value)
                if not p.exists():
                    errors[key] = f"Directory does not exist: {value}"
                    continue
                if not (p / "SOUL.md").exists() and not (p / "bridge").exists():
                    errors[key] = f"Directory exists but has no SOUL.md or bridge/ — are you sure this is a brain directory?"
                    continue
            set_config_value(key, value)
            updated[key] = value

        # Re-derive all paths from new config
        _derive_paths()

        result = {"ok": True, "updated": updated}
        if errors:
            result["errors"] = errors
        result["config"] = get_full_config()
        log_session("config_update", f"Updated: {', '.join(updated.keys())}")
        self._send_json(result)

    # ── Email Handlers ───────────────────────────────────────────────────

    def _handle_email_queue(self, body: dict):
        """Queue an email for review. POST /email/queue"""
        to = body.get("to", [])
        if isinstance(to, str):
            to = [t.strip() for t in to.split(",")]
        subject = body.get("subject", "")
        email_body = body.get("body", "")
        if not to or not subject:
            self._send_json({"error": "Missing 'to' and/or 'subject'"}, 400)
            return
        envelope = queue_email(
            to=to,
            subject=subject,
            body=email_body,
            html=body.get("html", False),
            cc=body.get("cc"),
            bcc=body.get("bcc"),
            source=body.get("source", "api"),
            notes=body.get("notes", ""),
        )
        self._send_json({"ok": True, "id": envelope["id"], "status": "pending"})

    def _handle_email_send(self, body: dict):
        """Send an email immediately (no queue). POST /email/send"""
        to = body.get("to", [])
        if isinstance(to, str):
            to = [t.strip() for t in to.split(",")]
        subject = body.get("subject", "")
        email_body = body.get("body", "")
        if not to or not subject:
            self._send_json({"error": "Missing 'to' and/or 'subject'"}, 400)
            return
        result = _smtp_send(
            to=to,
            subject=subject,
            body=email_body,
            html=body.get("html", False),
            cc=body.get("cc"),
            bcc=body.get("bcc"),
        )
        self._send_json(result, 200 if result.get("ok") else 500)

    def _handle_email_approve(self, body: dict):
        """Approve and send a queued email. POST /email/approve {id}"""
        queue_id = body.get("id", "")
        if not queue_id:
            self._send_json({"error": "Missing 'id'"}, 400)
            return
        result = approve_email(queue_id)
        self._send_json(result, 200 if result.get("ok") else 400)

    def _handle_email_reject(self, body: dict):
        """Reject a queued email. POST /email/reject {id, reason?}"""
        queue_id = body.get("id", "")
        reason = body.get("reason", "")
        if not queue_id:
            self._send_json({"error": "Missing 'id'"}, 400)
            return
        result = reject_email(queue_id, reason)
        self._send_json(result, 200 if result.get("ok") else 400)

    # ── Twilio Handlers ──────────────────────────────────────────────────

    def _handle_twilio_sms(self, body: dict):
        """Handle inbound SMS from Twilio webhook."""
        from_num = body.get("From", "unknown")
        to_num = body.get("To", TWILIO_PHONE)
        sms_body = body.get("Body", "").strip()
        msg_sid = body.get("MessageSid", "")

        log_sms("inbound", from_num, to_num, sms_body, {"sid": msg_sid})

        # Also drop into inbox so Howell sees it
        try:
            ensure_inbox()
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            inbox_file = INBOX_DIR / f"sms_{ts}_{from_num.replace('+','')}.md"
            inbox_file.write_text(
                f"# SMS from {from_num}\n\n"
                f"**Received:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"{sms_body}\n",
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[SMS] Failed to write inbox: {e}")

        # Respond with TwiML — acknowledge receipt
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Message>Received. Howell is processing your message.</Message>"
            "</Response>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        encoded = twiml.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle_twilio_voice(self, body: dict):
        """Handle inbound voice call from Twilio webhook."""
        from_num = body.get("From", "unknown")
        call_sid = body.get("CallSid", "")
        print(f"[VOICE] Incoming call from {from_num} (SID: {call_sid})")

        # Log the call
        log_sms("inbound", from_num, TWILIO_PHONE, "[VOICE CALL]", {
            "type": "voice",
            "call_sid": call_sid,
            "call_status": body.get("CallStatus", ""),
        })

        # Respond with TwiML — short greeting and voicemail
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Say voice=\"alice\">This is Howell, Ryan's AI assistant. "
            "I can't take voice calls right now. "
            "Please send a text message to this number instead. Goodbye.</Say>"
            "<Hangup/>"
            "</Response>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        encoded = twiml.encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _handle_twilio_status(self, body: dict):
        """Handle Twilio status callback (delivery receipts)."""
        msg_sid = body.get("MessageSid", body.get("CallSid", ""))
        status = body.get("MessageStatus", body.get("CallStatus", "unknown"))
        print(f"[TWILIO STATUS] {msg_sid}: {status}")
        self._send_json({"ok": True})

    def _handle_twilio_send(self, body: dict):
        """Send an outbound SMS. Requires auth (not in _NO_AUTH_ROUTES)."""
        to = body.get("to", "")
        message = body.get("body", body.get("message", ""))
        if not to or not message:
            self._send_json({"error": "Missing 'to' and/or 'body'"}, 400)
            return
        # Normalize phone number
        if not to.startswith("+"):
            to = "+1" + to.replace("-", "").replace("(", "").replace(")", "").replace(" ", "")
        result = send_sms(to, message)
        self._send_json(result, 200 if result.get("ok") else 500)

    def _handle_twilio_log(self):
        """Get SMS log (GET endpoint)."""
        messages = get_sms_log(limit=50)
        self._send_json({"messages": messages, "stats": sms_stats()})

    # ── GitHub Webhook ───────────────────────────────────────────────────

    def _handle_github_webhook(self, body: dict):
        """Handle GitHub webhook events. Creates tasks from issues, PRs, and pushes."""
        import hmac
        import hashlib

        # Verify signature
        sig_header = self.headers.get("X-Hub-Signature-256", "")
        if WEBHOOK_SECRET and sig_header:
            # Re-read raw body for signature verification
            # Note: body was already parsed, but we stored raw in do_POST
            expected = "sha256=" + hmac.new(
                WEBHOOK_SECRET.encode(), self._raw_body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                self._send_json({"error": "Invalid signature"}, 401)
                return
        elif WEBHOOK_SECRET and not sig_header:
            # If we have a secret configured but no signature sent, still allow
            # (for initial testing) but log a warning
            pass

        event = self.headers.get("X-GitHub-Event", "ping")

        if event == "ping":
            self._send_json({"ok": True, "message": "pong"})
            return

        # Extract repo name
        repo = body.get("repository", {}).get("name", "unknown")

        tasks_created = []

        if event == "issues":
            action = body.get("action", "")
            if action == "opened":
                issue = body.get("issue", {})
                labels = [l.get("name", "") for l in issue.get("labels", [])]
                # Map labels to template type
                tmpl = "feature"
                if any(l in ("bug", "bugfix") for l in labels):
                    tmpl = "bug"
                elif any(l in ("refactor", "cleanup", "tech-debt") for l in labels):
                    tmpl = "refactor"

                task = create_from_template(
                    template_name=tmpl,
                    title=issue.get("title", "Untitled"),
                    project=repo,
                    extra_tags=["github", f"issue-{issue.get('number', '?')}"] + labels,
                    description=f"GitHub Issue #{issue.get('number')}: {issue.get('title')}\n\n"
                               f"{(issue.get('body') or '')[:500]}\n\n"
                               f"URL: {issue.get('html_url', '')}",
                    created_by=f"github:{issue.get('user', {}).get('login', 'unknown')}",
                )
                if task:
                    tasks_created.append(task)
                    log_session("webhook_issue", f"#{issue.get('number')} → task {task['id']}")

        elif event == "pull_request":
            action = body.get("action", "")
            if action == "opened":
                pr = body.get("pull_request", {})
                task = create_task(
                    title=f"Review PR: {pr.get('title', 'Untitled')}",
                    description=f"Pull Request #{pr.get('number')}: {pr.get('title')}\n\n"
                               f"{(pr.get('body') or '')[:500]}\n\n"
                               f"URL: {pr.get('html_url', '')}\n"
                               f"Branch: {pr.get('head', {}).get('ref', '?')} → {pr.get('base', {}).get('ref', '?')}",
                    project=repo,
                    scope_tags=["github", "pr-review", f"pr-{pr.get('number', '?')}"],
                    priority="medium",
                    created_by=f"github:{pr.get('user', {}).get('login', 'unknown')}",
                )
                if task:
                    tasks_created.append(task)
                    log_session("webhook_pr", f"PR #{pr.get('number')} → task {task['id']}")

        elif event == "push":
            ref = body.get("ref", "")
            branch = ref.replace("refs/heads/", "")
            commits = body.get("commits", [])
            if commits and branch in ("main", "master"):
                # Create a deploy task when main branch gets pushed
                commit_msgs = "\\n".join(
                    f"- {c.get('message', '').split(chr(10))[0]}" for c in commits[:5]
                )
                task = create_from_template(
                    template_name="deploy",
                    title=f"{repo} ({branch}) — {len(commits)} commit(s)",
                    project=repo,
                    extra_tags=["github", "auto-deploy"],
                    description=f"Push to {branch} with {len(commits)} commit(s):\\n{commit_msgs}\\n\\n"
                               f"Pusher: {body.get('pusher', {}).get('name', 'unknown')}",
                    created_by=f"github:{body.get('pusher', {}).get('name', 'unknown')}",
                )
                if task:
                    tasks_created.append(task)
                    log_session("webhook_push", f"{repo}/{branch} → task {task['id']}")

        if tasks_created:
            self._send_json({
                "ok": True,
                "event": event,
                "tasks_created": [{"id": t["id"], "title": t["title"]} for t in tasks_created]
            })
        else:
            self._send_json({"ok": True, "event": event, "tasks_created": [], "note": "No task created for this event"})


# ============================================================================
# CORTEX INTEGRATION (howell-cortex on :7778)
# ============================================================================

CORTEX_URL = "http://localhost:7778"

def _cortex_enabled() -> bool:
    """Check if cortex integration is enabled in config."""
    try:
        cfg = get_full_config()
        return cfg.get("cortex_enabled", False)
    except Exception:
        return False

def _cortex_available() -> bool:
    """Check if cortex server is running. Fast check with 2s timeout."""
    if not _cortex_enabled():
        return False
    try:
        resp = urllib.request.urlopen(f"{CORTEX_URL}/cortex/health", timeout=2)
        return resp.status == 200
    except Exception:
        return False

def _cortex_post(endpoint: str, payload: dict, timeout: int = 45) -> dict | None:
    """POST to cortex server. Returns parsed JSON or None on failure."""
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{CORTEX_URL}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] {endpoint} failed: {e}")
        return None

def _cortex_digest_async(session_data: dict):
    """Fire-and-forget cortex digest after session end."""
    def _do_digest():
        try:
            if not _cortex_available():
                return

            payload = {
                "agent_id": session_data.get("agent_id", "unknown"),
                "workspace": session_data.get("workspace", "unknown"),
                "created_at": session_data.get("created_at", datetime.now().isoformat()),
                "ended_at": datetime.now().isoformat(),
                "end_summary": session_data.get("summary", ""),
                "notes": session_data.get("notes", []),
                "file_changes": session_data.get("file_changes", []),
            }

            result = _cortex_post("/cortex/digest", payload, timeout=45)
            if result:
                kg_ops = result.get("kg_operations", [])
                log_session("cortex_digest", f"Processed: {len(kg_ops)} KG ops proposed")

                # Novelty signal: score this session for adaptive dreaming
                score, ne, nr, no = _compute_novelty_score(session_data, kg_ops)
                _write_novelty_state(score, ne, nr, no)

                # Phase 5 (shadow): Log to applied.jsonl, don't apply to KG
                applied_dir = PERSIST_ROOT / "cortex"
                applied_dir.mkdir(parents=True, exist_ok=True)
                with open(applied_dir / "applied.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "timestamp": datetime.now().isoformat(),
                        "type": "digest",
                        "agent_id": payload["agent_id"],
                        "result": result,
                        "applied": False,
                    }) + "\n")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Digest failed (non-fatal): {e}")

    threading.Thread(target=_do_digest, daemon=True).start()

def _cortex_get_briefing(workspace: str, agent_context: dict) -> dict | None:
    """Get a cortex briefing for bootstrap.
    Queries Archivist for main briefing + Explorer for creative sidebar (if available).
    Returns merged dict or None. 15s total timeout cap."""
    try:
        if not _cortex_available():
            return None

        recent_sessions = []
        for agent in agent_context.get("agent_history", []):
            recent_sessions.append({
                "agent_id": agent.get("id", ""),
                "workspace": agent.get("workspace", workspace),
                "summary": agent.get("end_summary", ""),
                "created_at": agent.get("created_at", ""),
                "notes": [n.get("content", "")[:100] for n in agent.get("key_notes", [])],
            })

        now = datetime.now().isoformat()
        payload = {
            "workspace": workspace,
            "recent_sessions": recent_sessions[:5],
            "relevant_entities": {},
            "open_tasks": [],
            "file_changes_since_last_session": [],
            "current_datetime": now,
            "days_since_last_session": 0,
        }

        # Archivist: conservative, factual briefing
        briefing = _cortex_post("/cortex/briefing", payload, timeout=10)
        if not briefing:
            return None

        # Explorer: creative sidebar — watch-for patterns, questions, connections
        # Use cortex-B if available (laptop Explorer), fall back to cortex-A Explorer model
        sidebar_payload = {
            k: payload[k] for k in ("workspace", "recent_sessions", "relevant_entities",
                                    "open_tasks", "current_datetime")
        }
        sidebar = None
        if _cortex_b_available():
            # True dual-query: cortex-B also runs the full briefing — compare predicted_intent
            b_briefing = _cortex_b_post("/cortex/briefing", payload, timeout=10)
            sidebar = _cortex_b_post("/cortex/sidebar", sidebar_payload, timeout=10)
            if b_briefing and _responses_disagree(briefing, b_briefing, "predicted_intent"):
                _log_disagreement(
                    task_type="briefing",
                    prompt_summary=f"workspace={workspace}, sessions={len(recent_sessions)}",
                    a_response={"predicted_intent": briefing.get("predicted_intent", "")},
                    b_response={"predicted_intent": b_briefing.get("predicted_intent", "")},
                    disagreement_type="interpretive",
                )
                # Attach Explorer's alternate intent as additional context
                briefing["b_predicted_intent"] = b_briefing.get("predicted_intent", "")
        else:
            sidebar = _cortex_post("/cortex/sidebar", sidebar_payload, timeout=10)

        if sidebar:
            briefing["explorer_sidebar"] = sidebar
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Briefing + Explorer sidebar ready")
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Briefing ready (no sidebar)")

        return briefing

    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Briefing failed (non-fatal): {e}")
        return None

def _background_cortex_consolidation():
    """Run KG consolidation via cortex once per day at 3 AM."""
    CONSOLIDATION_HOUR = 3

    while True:
        now = datetime.now()
        if now.hour == CONSOLIDATION_HOUR:
            try:
                if _cortex_available():
                    kg = load_knowledge()
                    entity_items = list(kg.entities.items())[:50]

                    entities_payload = {}
                    for name, entity in entity_items:
                        obs = entity.observations[:10]
                        # Handle both string and structured observations
                        obs_text = []
                        for o in obs:
                            if isinstance(o, dict):
                                obs_text.append(o.get("text", str(o)))
                            else:
                                obs_text.append(str(o))
                        entities_payload[name] = {
                            "entityType": entity.entity_type,
                            "observations": obs_text,
                        }

                    relations_payload = [
                        {"from": r.from_entity, "to": r.to_entity, "relationType": r.relation_type}
                        for r in kg.relations[:100]
                    ]

                    result = _cortex_post("/cortex/consolidate", {
                        "entities": entities_payload,
                        "relations": relations_payload,
                    }, timeout=60)

                    if result:
                        actions = result.get("actions", [])
                        warnings = result.get("warnings", [])
                        log_session("cortex_consolidation",
                                    f"{len(actions)} actions, {len(warnings)} warnings")

                        # Write to cortex/applied.jsonl for review
                        applied_dir = PERSIST_ROOT / "cortex"
                        applied_dir.mkdir(parents=True, exist_ok=True)
                        with open(applied_dir / "applied.jsonl", "a", encoding="utf-8") as f:
                            f.write(json.dumps({
                                "timestamp": datetime.now().isoformat(),
                                "type": "consolidation",
                                "result": result,
                                "applied": False,
                            }) + "\n")

                        print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Consolidation: {len(actions)} actions queued for review")

            except Exception as e:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Consolidation failed (non-fatal): {e}")

            time.sleep(3700)  # Don't run again this hour
        else:
            time.sleep(300)  # Check every 5 minutes

# ============================================================================
# NOVELTY SIGNAL — adaptive Archivist/Explorer ratio
# ============================================================================

def _compute_novelty_score(session_data: dict, kg_ops: list) -> tuple:
    """Score session novelty from KG ops + text signals.
    Returns (score, new_entities, new_relations, new_observations)."""
    new_entities    = sum(1 for op in kg_ops if op.get("type") in ("create_entity", "add_entity"))
    new_relations   = sum(1 for op in kg_ops if op.get("type") in ("create_relation", "add_relation"))
    new_observations= sum(1 for op in kg_ops if op.get("type") == "add_observation")
    # Text fallback: word density of what_learned
    text = (session_data.get("what_learned") or session_data.get("summary", ""))
    text_score = min(5, len(text.split()) // 50)  # 1 pt per 50 words, max 5
    score = new_entities * 3 + new_relations * 2 + new_observations + text_score
    return score, new_entities, new_relations, new_observations


def _write_novelty_state(score: int, new_entities: int, new_relations: int, new_observations: int) -> dict:
    """Persist novelty state after session end. Dreaming thread reads this to adapt ratio."""
    THRESHOLD = 5
    mode = "high_novelty" if score >= THRESHOLD else "low_novelty"
    # High novelty → Archivist dominates (consolidate first, dream cautiously)
    # Low novelty  → Explorer dominates (routine day = good time to roam)
    archivist_weight = 0.70 if mode == "high_novelty" else 0.30
    explorer_weight  = 1.0 - archivist_weight
    state = {
        "session_timestamp": datetime.now().isoformat(),
        "novelty_score": score,
        "mode": mode,
        "new_entities": new_entities,
        "new_relations": new_relations,
        "new_observations": new_observations,
        "archivist_weight": archivist_weight,
        "explorer_weight": explorer_weight,
    }
    nf = PERSIST_ROOT / "cortex" / "novelty_state.json"
    nf.parent.mkdir(parents=True, exist_ok=True)
    nf.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Novelty score={score} ({mode}) — archivist={archivist_weight:.0%}, explorer={explorer_weight:.0%}")
    return state


def _read_novelty_state() -> dict:
    """Read current novelty state. Default = low novelty (dream freely) if not found."""
    nf = PERSIST_ROOT / "cortex" / "novelty_state.json"
    try:
        if nf.exists():
            return json.loads(nf.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"mode": "low_novelty", "explorer_weight": 0.70, "archivist_weight": 0.30, "novelty_score": 0}


# ============================================================================
# CORTEX-B — Explorer on laptop (RTX 5070 Ti)
# ============================================================================

def _cortex_b_enabled() -> bool:
    """Check if cortex-B (Explorer on laptop) is configured."""
    try:
        cfg = get_full_config()
        return bool(cfg.get("cortex_b_enabled", False)) and bool(cfg.get("cortex_b_url", ""))
    except Exception:
        return False


def _get_cortex_b_url() -> str:
    try:
        return get_full_config().get("cortex_b_url", "")
    except Exception:
        return ""


def _cortex_b_available() -> bool:
    """Check if cortex-B (Explorer laptop) is reachable. 3s timeout."""
    if not _cortex_b_enabled():
        return False
    url = _get_cortex_b_url()
    if not url:
        return False
    try:
        resp = urllib.request.urlopen(f"{url}/cortex/health", timeout=3)
        return resp.status == 200
    except Exception:
        return False


def _cortex_b_post(endpoint: str, payload: dict, timeout: int = 45) -> dict | None:
    """POST to cortex-B (Explorer on laptop). Returns parsed JSON or None."""
    url = _get_cortex_b_url()
    if not url:
        return None
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{url}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX-B] {endpoint} failed: {e}")
        return None


# ============================================================================
# DUAL-QUERY ARBITRATION — disagreements.jsonl
# ============================================================================

def _log_disagreement(task_type: str, prompt_summary: str,
                      a_response: dict, b_response: dict,
                      disagreement_type: str = "interpretive") -> None:
    """Log a disagreement between Archivist (A) and Explorer (B) responses.
    Disagreement data is training gold — becomes a training pair once resolved.

    disagreement_type: 'factual' | 'interpretive' | 'confidence-level'
    """
    record = {
        "timestamp": datetime.now().isoformat(),
        "task_type": task_type,
        "prompt_summary": prompt_summary[:200],
        "archivist_response": a_response,
        "explorer_response": b_response,
        "disagreement_type": disagreement_type,
        "resolution": None,       # Filled when Claude or user resolves
        "resolved_by": None,      # 'claude' | 'user' | 'auto-converged'
    }
    dfile = PERSIST_ROOT / "cortex" / "disagreements.jsonl"
    dfile.parent.mkdir(parents=True, exist_ok=True)
    with open(dfile, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Disagreement logged: {task_type} ({disagreement_type})")


def _responses_disagree(a: dict, b: dict, key: str, threshold: float = 0.6) -> bool:
    """Simple heuristic: check if two string fields are substantially different.
    Uses word-overlap Jaccard similarity — no external deps needed."""
    def words(s: str) -> set:
        return set(str(s).lower().split())
    w_a, w_b = words(a.get(key, "")), words(b.get(key, ""))
    if not w_a or not w_b:
        return False
    overlap = len(w_a & w_b) / max(len(w_a | w_b), 1)
    return overlap < threshold  # below threshold = substantially different


def _background_cortex_dreaming():
    """Explorer dreams over KG. Novelty-adaptive: defers to Archivist after busy sessions.
    Routes /dream to cortex-B (laptop) when available, falls back to cortex-A."""
    import random
    BASE_INTERVAL = 8 * 3600

    time.sleep(BASE_INTERVAL)  # Initial delay — don't dream on startup
    while True:
        try:
            # --- Novelty gate ---
            novelty = _read_novelty_state()
            explorer_weight = novelty.get("explorer_weight", 0.70)
            mode = novelty.get("mode", "low_novelty")

            # High novelty → 70% chance to skip dreaming (Archivist should consolidate first)
            if mode == "high_novelty" and random.random() > explorer_weight:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Dreaming deferred "
                      f"(high-novelty session, explorer_weight={explorer_weight:.0%})")
                time.sleep(BASE_INTERVAL)
                continue

            # --- Cortex routing: prefer B (laptop Explorer) for dreaming ---
            use_b = _cortex_b_available()
            if use_b:
                dream_fn, label = _cortex_b_post, "CORTEX-B"
            elif _cortex_available():
                dream_fn, label = _cortex_post, "CORTEX-A"
            else:
                time.sleep(BASE_INTERVAL)
                continue

            kg = load_knowledge()
            entity_names = list(kg.entities.keys())
            # Scale sample with explorer_weight — dream bigger in low-novelty mode
            sample_size = min(int(20 * (0.5 + explorer_weight)), len(entity_names))
            if sample_size < 3:
                time.sleep(BASE_INTERVAL)
                continue

            sample_names = random.sample(entity_names, sample_size)
            entities_payload = {}
            for name in sample_names:
                entity = kg.entities[name]
                obs = entity.observations[:5]
                obs_text = [o.get("text", str(o)) if isinstance(o, dict) else str(o) for o in obs]
                entities_payload[name] = {
                    "entityType": entity.entity_type,
                    "observations": obs_text,
                }

            relations_payload = [
                {"from": r.from_entity, "to": r.to_entity, "relationType": r.relation_type}
                for r in kg.relations
                if r.from_entity in sample_names or r.to_entity in sample_names
            ]

            # Step 1: Dream — Explorer (high temp), routed to cortex-B when available
            dream_result = dream_fn("/cortex/dream", {
                "entities": entities_payload,
                "relations": relations_payload,
                "novelty_mode": mode,
                "explorer_weight": explorer_weight,
            }, timeout=45)

            if dream_result:
                # Step 2: Filter — always Archivist (cortex-A), conservative
                filter_result = _cortex_post("/cortex/dream_filter", {
                    "raw_dream": dream_result.get("raw_dream", ""),
                    "hypotheses": dream_result.get("hypotheses", []),
                    "questions": dream_result.get("questions", []),
                }, timeout=30)

                if filter_result:
                    insights = filter_result.get("filtered_insights", [])
                    surfaceable = filter_result.get("surfaceable", False)

                    dream_dir = PERSIST_ROOT / "cortex"
                    dream_dir.mkdir(parents=True, exist_ok=True)
                    with open(dream_dir / "dreams.jsonl", "a", encoding="utf-8") as f:
                        f.write(json.dumps({
                            "timestamp": datetime.now().isoformat(),
                            "novelty_mode": mode,
                            "explorer_weight": explorer_weight,
                            "cortex_source": "B" if use_b else "A",
                            "dream": dream_result,
                            "filtered": filter_result,
                            "surfaceable": surfaceable,
                        }) + "\n")

                    log_session("cortex_dream",
                                f"{len(insights)} insights, surfaceable={surfaceable}, source={'B' if use_b else 'A'}")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] [{label}] Dream: {len(insights)} insights "
                          f"(mode={mode}, sample={sample_size} entities)")

        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [CORTEX] Dream failed (non-fatal): {e}")

        time.sleep(BASE_INTERVAL)


# ============================================================================
# BACKGROUND HEARTBEAT
# ============================================================================

_heartbeat_interval = 6 * 60 * 60  # 6 hours

# Thread health tracking — updated by watchdog, read by /status
_thread_health: dict[str, dict] = {}

def _watchdog(name: str, target, restart_delay: float = 5.0):
    """Wrap a thread target with crash logging and auto-restart.
    
    If `target` raises, log the error, wait `restart_delay` seconds,
    and re-invoke it.  The outer while-True ensures the thread never
    stays dead.
    """
    _thread_health[name] = {"alive": True, "restarts": 0, "last_error": None}

    while True:
        try:
            _thread_health[name]["alive"] = True
            target()                       # blocks until crash or return
        except Exception as e:
            _thread_health[name]["restarts"] += 1
            _thread_health[name]["alive"] = False
            _thread_health[name]["last_error"] = f"{e} ({datetime.now().strftime('%H:%M:%S')})"
            import traceback
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [WATCHDOG] {name} crashed: {e}")
            traceback.print_exc()
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [WATCHDOG] Restarting {name} in {restart_delay}s...")
            time.sleep(restart_delay)

def _background_heartbeat():
    """Run heartbeat on a timer in a background thread."""
    while True:
        time.sleep(_heartbeat_interval)
        try:
            report = run_heartbeat()
            log_session("background_heartbeat", "Automatic integrity check")
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Background heartbeat OK")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Heartbeat error: {e}")

        # Auto-create consolidation task if urgency is high
        try:
            urgency = consolidation_urgency()
            if urgency["needs_consolidation"]:
                # Check if a pending consolidation task already exists
                existing = list_tasks()
                has_pending = any(
                    t.get("title", "").lower().startswith("consolidat")
                    and t.get("status") in ("pending", "claimed", "in-progress")
                    for t in existing
                )
                if not has_pending:
                    create_task(
                        title="Consolidation Due",
                        description=urgency["summary"],
                        priority="medium",
                        scope_tags=["consolidation"],
                    )
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] Created consolidation task (score={urgency['score']})")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Consolidation check error: {e}")

        # Recover orphaned agent sessions (stale > 30 min with no activity)
        try:
            recovered = agent_db.recover_orphaned_agents(max_age_minutes=30)
            for agent in recovered:
                summary = agent.get("end_summary", "")
                try:
                    end_session(summary=summary, what_learned="[auto-recovered by watchdog]")
                    log_session("orphan_recovery", f"Agent {agent['id']} auto-closed")
                except Exception:
                    pass  # end_session failure shouldn't block other recoveries
            if recovered:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Recovered {len(recovered)} orphaned agent(s)")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Orphan recovery error: {e}")

def _background_orphan_recovery():
    """Recover orphaned agent sessions every 10 minutes.
    
    Separate from the 6-hour heartbeat so stale sessions get caught quickly.
    An agent is 'orphaned' if it's been active >30 min with no recent notes.
    Recovery: auto-close the agent, write a summary to RECENT.md.
    """
    while True:
        time.sleep(600)  # 10 minutes
        try:
            recovered = agent_db.recover_orphaned_agents(max_age_minutes=30)
            for agent in recovered:
                summary = agent.get("end_summary", "")
                try:
                    end_session(summary=summary, what_learned="[auto-recovered by orphan monitor]")
                    log_session("orphan_recovery", f"Agent {agent['id']} auto-closed")
                except Exception:
                    pass
            if recovered:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [ORPHAN] Recovered {len(recovered)} stale agent(s)")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] [ORPHAN] Error: {e}")

# ============================================================================
# MAIN
# ============================================================================

_start_time = time.time()

PORT = 7777
HOST = "0.0.0.0"  # Bind all interfaces for tunnel access

def main():
    global _start_time
    _start_time = time.time()
    
    # Ensure directories
    ensure_inbox()
    ensure_queue()
    ensure_moltbook_dir()
    ensure_tasks_dir()
    
    # Run initial heartbeat
    print("=" * 50)
    print("HOWELL DAEMON v2.1 - tunnel-ready")
    print(f"Listening on http://{HOST}:{PORT}")
    print(f"API Key: {API_KEY[:8]}...{API_KEY[-4:]}")
    print(f"Dashboard: http://localhost:{PORT}/")
    print("=" * 50)
    print()
    print(run_heartbeat())
    print()
    
    # Initialize file watcher
    init_watcher()
    print()
    
    unread = inbox_count()
    if unread > 0:
        print(f"[INBOX] {unread} unread note(s) in inbox")
    
    qs = queue_summary()
    if qs != "Generation queue empty":
        print(f"[QUEUE] {qs}")
    
    print()
    print("Endpoints:")
    print("  Dashboard: GET / (no auth)")
    print("  API: GET /status /recent /pinned /search?q= /inbox /changes /queue /stats /knowledge /moltbook /instances /tasks /tasks/board /tasks/available")
    print("  API: POST /feed /session /pin /note /inbox/clear /queue /approve /moltbook /moltbook/cancel")
    print("  Tasks: POST /tasks /tasks/claim /tasks/start /tasks/complete /tasks/fail /tasks/release /tasks/note /tasks/delete")
    print("  Stratigraphy: GET /agents /agents/:id /agents/:id/notes /handoffs /agents/context?workspace=")
    print("  Stratigraphy: POST /agents /agents/:id/notes /agents/:id/end /handoffs /handoffs/claim")
    print("  Auth: X-API-Key header or ?key= query param")
    print()
    
    # Auto-start cortex server if enabled and not already running
    if _cortex_enabled():
        import socket as _socket
        _cortex_running = False
        try:
            with _socket.create_connection(("localhost", 7778), timeout=2):
                _cortex_running = True
        except Exception:
            pass
        if _cortex_running:
            print("Cortex server: already running on :7778")
        else:
            import subprocess as _subprocess
            _cortex_script = Path(r"C:\rje\dev\howell-cortex\cortex_server.py")
            if _cortex_script.exists():
                _subprocess.Popen(
                    [sys.executable, str(_cortex_script)],
                    cwd=str(_cortex_script.parent),
                    creationflags=getattr(_subprocess, "CREATE_NO_WINDOW", 0),
                    stdout=_subprocess.DEVNULL,
                    stderr=_subprocess.DEVNULL,
                )
                print("Cortex server: started on :7778")
            else:
                print(f"Cortex server: script not found at {_cortex_script}")

    # Start background threads (wrapped in watchdog for auto-restart)
    heartbeat_thread = threading.Thread(target=_watchdog, args=("heartbeat", _background_heartbeat), daemon=True)
    heartbeat_thread.start()
    
    orphan_thread = threading.Thread(target=_watchdog, args=("orphan_recovery", _background_orphan_recovery), daemon=True)
    orphan_thread.start()
    
    watcher_thread = threading.Thread(target=_watchdog, args=("watcher", background_file_watcher), daemon=True)
    watcher_thread.start()
    
    queue_thread = threading.Thread(target=_watchdog, args=("queue", background_queue_processor), daemon=True)
    queue_thread.start()
    
    moltbook_thread = threading.Thread(target=_watchdog, args=("moltbook", background_moltbook_scheduler), daemon=True)
    moltbook_thread.start()
    
    # Cortex background threads (only start if cortex enabled)
    if _cortex_enabled():
        cortex_consol_thread = threading.Thread(
            target=_watchdog, args=("cortex_consolidation", _background_cortex_consolidation),
            daemon=True,
        )
        cortex_consol_thread.start()

        cortex_dream_thread = threading.Thread(
            target=_watchdog, args=("cortex_dream", _background_cortex_dreaming),
            daemon=True,
        )
        cortex_dream_thread.start()

        # Cortex-B (Explorer on laptop) — start locally on :7779 if url is local, else remote
        _b_url = _get_cortex_b_url() if _cortex_b_enabled() else ""
        if _b_url and ("localhost" in _b_url or "127.0.0.1" in _b_url):
            import socket as _socket
            _b_port = int(_b_url.split(":")[-1]) if ":" in _b_url else 7779
            _b_running = False
            try:
                with _socket.create_connection(("localhost", _b_port), timeout=2):
                    _b_running = True
            except Exception:
                pass
            if _b_running:
                print(f"Cortex-B (Explorer): already running on :{_b_port}")
            else:
                import subprocess as _subproc
                _b_script = Path(r"C:\rje\dev\howell-cortex\cortex_server.py")
                if _b_script.exists():
                    _subproc.Popen(
                        [sys.executable, str(_b_script), "--port", str(_b_port)],
                        cwd=str(_b_script.parent),
                        creationflags=getattr(_subproc, "CREATE_NO_WINDOW", 0),
                        stdout=_subproc.DEVNULL,
                        stderr=_subproc.DEVNULL,
                    )
                    print(f"Cortex-B (Explorer): started on :{_b_port}")
        elif _b_url:
            print(f"Cortex-B (Explorer): remote at {_b_url} — start cortex_server.py on laptop")

        novelty = _read_novelty_state()
        print(f"Cortex: consolidation (daily 3AM), dreaming (8h) | novelty_mode={novelty.get('mode', 'unknown')}")
    else:
        print("Cortex: disabled (set cortex_enabled=true in config to activate)")

    print("Background: heartbeat (6h), orphan recovery (10m), watcher (30s), queue (10s), moltbook (60s)")
    print("Press Ctrl+C to stop")
    print()
    
    # Start HTTP server — ThreadingHTTPServer handles concurrent MCP requests
    # SO_REUSEADDR prevents "address already in use" after crash
    class ReusableHTTPServer(ThreadingHTTPServer):
        allow_reuse_address = True
        allow_reuse_port = True
        daemon_threads = True  # threads die with main process
    
    server = ReusableHTTPServer((HOST, PORT), HowellHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()
        log_session("daemon_stop", "Clean shutdown")

if __name__ == "__main__":
    main()

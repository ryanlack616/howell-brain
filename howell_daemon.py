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
from urllib.parse import urlparse, parse_qs

# Add bridge to path — resolve from env var or default
PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\Users\PC\Desktop\claude-persist"))
BRIDGE_ROOT = PERSIST_ROOT / "bridge"
sys.path.insert(0, str(BRIDGE_ROOT))
# Also add script's own directory (for Fly.io where code lives in /app/)
sys.path.insert(0, str(Path(__file__).parent))

from howell_bridge import (
    run_heartbeat,
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

# Public routes that don't need auth (dashboard assets + preflight + webhooks)
_PUBLIC_ROUTES = {"/", "/dashboard", "/graph", "/explorer", "/favicon.ico", "/webhook/github",
                  "/status", "/knowledge", "/pinned", "/recent", "/summary",
                  "/search", "/identity/soul", "/health", "/brain"}

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

def _check_auth(handler) -> bool:
    """Check if request is authenticated. Returns True if OK."""
    path = urlparse(handler.path).path.rstrip("/") or "/"
    # Public routes skip auth
    if path in _PUBLIC_ROUTES:
        return True
    # Instance/task/agent coordination endpoints are public (localhost-only,
    # used by MCP server which doesn't carry API key)
    if path.startswith(("/instance", "/tasks", "/agents", "/handoffs")):
        return True
    # Check header
    auth = handler.headers.get("X-API-Key", "") or handler.headers.get("Authorization", "").replace("Bearer ", "")
    if auth == API_KEY:
        return True
    # Check query param (for browser convenience)
    params = parse_qs(urlparse(handler.path).query)
    if params.get("key", [""])[0] == API_KEY:
        return True
    return False

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
        if not _check_auth(self):
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
        elif path.startswith("/identity/"):
            self._handle_identity(path.split("/identity/", 1)[1])
        else:
            self._send_json({"error": f"Unknown route: {path}"}, 404)

    def _handle_identity(self, name):
        """Serve identity files (soul, context, etc.) as plain text."""
        identity_files = {
            "soul": PERSIST_ROOT / "SOUL.md",
            "context": PERSIST_ROOT / "CONTEXT.md",
            "projects": Path(os.environ.get("HOWELL_PROJECTS_FILE", r"C:\Users\PC\Desktop\projects\stull-atlas\src\docs\roadmap.md")),
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
        elif path == "/webhook/github":
            self._handle_github_webhook(body)
        elif path == "/config":
            self._handle_config_set(body)
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
    
    def _handle_dashboard(self):
        """Serve the dashboard HTML with API key injected."""
        dash_file = _dashboard_path()
        if dash_file.exists():
            html = dash_file.read_text(encoding="utf-8")
            # Inject the API key into the page so fetch calls can use it
            inject = f'<script>window.__HOWELL_API_KEY="{API_KEY}";</script>'
            html = html.replace("</head>", inject + "\n</head>", 1)
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
        """Serve the standalone graph visualization page."""
        graph_file = _graph_path()
        if graph_file.exists():
            html = graph_file.read_text(encoding="utf-8")
            inject = f'<script>window.__HOWELL_API_KEY="{API_KEY}";</script>'
            html = html.replace("</head>", inject + "\n</head>", 1)
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
        self._send_json(context)

    # ── Config handlers ────────────────────────────────────────

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
    
    # Start background threads (wrapped in watchdog for auto-restart)
    heartbeat_thread = threading.Thread(target=_watchdog, args=("heartbeat", _background_heartbeat), daemon=True)
    heartbeat_thread.start()
    
    watcher_thread = threading.Thread(target=_watchdog, args=("watcher", background_file_watcher), daemon=True)
    watcher_thread.start()
    
    queue_thread = threading.Thread(target=_watchdog, args=("queue", background_queue_processor), daemon=True)
    queue_thread.start()
    
    moltbook_thread = threading.Thread(target=_watchdog, args=("moltbook", background_moltbook_scheduler), daemon=True)
    moltbook_thread.start()
    
    print("Background: heartbeat (6h), watcher (30s), queue (10s), moltbook (60s)")
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

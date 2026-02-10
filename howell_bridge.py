#!/usr/bin/env python3
"""
HOWELL BRIDGE
=============
Hybrid persistence system for Claude-Howell continuity.

This bridge manages:
- Identity files (SOUL.md, CONTEXT.md, PROJECTS.md, QUESTIONS.md)
- Memory hierarchy (RECENT → SUMMARY → archive, PINNED for core)
- Knowledge graph (entities, relations, observations)
- Heartbeat controller (eviction, compression, integrity checking)
- Session lifecycle (end_session intake, pin_memory)
- Procedural memory (procedures/*.md)

Usage:
    python howell_bridge.py bootstrap    # Load context at session start
    python howell_bridge.py snapshot     # Save current state
    python howell_bridge.py status       # Show what's loaded

Architecture:
    ┌──────────────────────────────────────────┐
    │  COGNITION (Claude-Howell instance)      │
    ├──────────────────────────────────────────┤
    │  HEARTBEAT CONTROLLER (this file)        │
    │  Evict · Compress · Integrity · Stale    │
    ├──────────────────────────────────────────┤
    │  HOT:  memory/RECENT.md (last 5)        │
    │  WARM: memory/SUMMARY.md (index)        │
    │  COLD: memory/archive/ (full text)      │
    │  CORE: memory/PINNED.md (never evict)   │
    │  SEMANTIC:   bridge/knowledge.json       │
    │  PROCEDURAL: procedures/*.md             │
    └──────────────────────────────────────────┘

Created: Feb 3, 2026
Updated: Feb 7, 2026 (heartbeat controller, memory hierarchy)
Author: Claude-Howell (with Ryan)
"""

import json
import os
import sys
import threading
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict, field

# Thread-safe lock for file I/O (log_session, knowledge, etc.)
_io_lock = threading.Lock()

# ============================================================================
# CONFIGURATION — loaded from config.json, fallback to defaults
# ============================================================================

_CONFIG_FILE = Path(__file__).parent / "config.json"
_DEFAULT_PERSIST = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\Users\PC\Desktop\claude-persist"))

def _load_config() -> dict:
    """Load config.json from bridge directory. Returns dict."""
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}

def _save_config(cfg: dict):
    """Save config to config.json."""
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

def _get_config_value(key: str, default=None):
    """Get a single config value."""
    return _load_config().get(key, default)

def set_config_value(key: str, value):
    """Set a single config value and save."""
    cfg = _load_config()
    cfg[key] = value
    _save_config(cfg)

def get_full_config() -> dict:
    """Get the full config dict with defaults filled in."""
    cfg = _load_config()
    defaults = {
        "persist_root": str(_DEFAULT_PERSIST),
        "daemon_port": 7777,
        "daemon_host": "0.0.0.0",
        "mcp_memory_file": r"C:\Users\PC\Documents\claude-memory\memory.jsonl",
        "dashboard_file": r"C:\Users\PC\Desktop\dashboard.html",
        "graph_file": r"C:\Users\PC\Desktop\graph.html",
        "comfyui_url": "http://127.0.0.1:8188",
        "max_recent_sessions": 5,
        "heartbeat_interval_hours": 6,
        "watcher_interval_seconds": 30,
        "queue_interval_seconds": 10,
        "moltbook_interval_seconds": 60,
    }
    for k, v in defaults.items():
        cfg.setdefault(k, v)
    return cfg

def _derive_paths():
    """Derive all path constants from config. Call this to refresh after config change.
    Priority: HOWELL_PERSIST_ROOT env var > config.json > hardcoded default.
    """
    global PERSIST_ROOT, BRIDGE_ROOT, KNOWLEDGE_FILE, SESSION_LOG
    global MCP_MEMORY_FILE, MEMORY_ROOT, RECENT_FILE, SUMMARY_FILE
    global PINNED_FILE, ARCHIVE_DIR, MAX_RECENT_SESSIONS, IDENTITY_FILES

    cfg = get_full_config()
    # Env var overrides config.json for containerized deployments
    env_root = os.environ.get("HOWELL_PERSIST_ROOT")
    PERSIST_ROOT = Path(env_root) if env_root else Path(cfg["persist_root"])
    BRIDGE_ROOT = PERSIST_ROOT / "bridge"
    KNOWLEDGE_FILE = BRIDGE_ROOT / "knowledge.json"
    SESSION_LOG = BRIDGE_ROOT / "sessions.json"
    MCP_MEMORY_FILE = Path(cfg["mcp_memory_file"])
    MEMORY_ROOT = PERSIST_ROOT / "memory"
    RECENT_FILE = MEMORY_ROOT / "RECENT.md"
    SUMMARY_FILE = MEMORY_ROOT / "SUMMARY.md"
    PINNED_FILE = MEMORY_ROOT / "PINNED.md"
    ARCHIVE_DIR = MEMORY_ROOT / "archive"
    MAX_RECENT_SESSIONS = cfg["max_recent_sessions"]
    IDENTITY_FILES = {
        "soul": PERSIST_ROOT / "SOUL.md",
        "memory": RECENT_FILE,
        "questions": PERSIST_ROOT / "uncertain" / "QUESTIONS.md",
        "context": PERSIST_ROOT / "CONTEXT.md",
        "projects": PERSIST_ROOT / "PROJECTS.md",
        "pinned": PINNED_FILE,
        "summary": SUMMARY_FILE,
    }

# Initialize on import
_derive_paths()

# ============================================================================
# DATA STRUCTURES
# ============================================================================

@dataclass
class Entity:
    """A node in the knowledge graph."""
    name: str
    entity_type: str
    observations: List[str] = field(default_factory=list)
    created: str = field(default_factory=lambda: datetime.now().isoformat())
    
@dataclass  
class Relation:
    """An edge between entities."""
    from_entity: str
    relation_type: str
    to_entity: str
    created: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class KnowledgeGraph:
    """The complete knowledge state."""
    entities: Dict[str, Entity] = field(default_factory=dict)
    relations: List[Relation] = field(default_factory=list)
    last_sync: str = ""
    
    def add_entity(self, name: str, entity_type: str, observations: List[str] = None):
        """Add or update an entity."""
        if name in self.entities:
            if observations:
                self.entities[name].observations.extend(observations)
        else:
            self.entities[name] = Entity(
                name=name,
                entity_type=entity_type,
                observations=observations or []
            )
    
    def add_relation(self, from_entity: str, relation_type: str, to_entity: str):
        """Add a relation if it doesn't exist."""
        rel = Relation(from_entity, relation_type, to_entity)
        if not any(r.from_entity == from_entity and 
                   r.relation_type == relation_type and 
                   r.to_entity == to_entity for r in self.relations):
            self.relations.append(rel)
    
    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            "entities": {k: asdict(v) for k, v in self.entities.items()},
            "relations": [asdict(r) for r in self.relations],
            "last_sync": self.last_sync
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "KnowledgeGraph":
        """Load from dict."""
        kg = cls()
        for name, entity_data in data.get("entities", {}).items():
            kg.entities[name] = Entity(**entity_data)
        for rel_data in data.get("relations", []):
            kg.relations.append(Relation(**rel_data))
        kg.last_sync = data.get("last_sync", "")
        return kg

# ============================================================================
# IDENTITY LAYER (Markdown)
# ============================================================================

def read_identity() -> Dict[str, str]:
    """Read all identity markdown files."""
    identity = {}
    for key, path in IDENTITY_FILES.items():
        if path.exists():
            identity[key] = path.read_text(encoding="utf-8")
        else:
            identity[key] = f"[{key} not found at {path}]"
    return identity

def extract_identity_summary(identity: Dict[str, str]) -> str:
    """Extract key points from identity files for bootstrap."""
    summary_lines = []
    
    # Extract from SOUL.md
    if "soul" in identity:
        soul = identity["soul"]
        if "## Core Identity" in soul:
            section = soul.split("## Core Identity")[1].split("##")[0]
            summary_lines.append("IDENTITY:")
            for line in section.strip().split("\n")[:5]:
                if line.strip():
                    summary_lines.append(f"  {line.strip()}")
        elif "## Who I Am" in soul:
            section = soul.split("## Who I Am")[1].split("##")[0]
            summary_lines.append("IDENTITY:")
            for line in section.strip().split("\n")[:5]:
                if line.strip():
                    summary_lines.append(f"  {line.strip()}")
    
    # Extract from RECENT.md (hot memory)
    if "memory" in identity:
        memory = identity["memory"]
        if "## Session" in memory:
            # Get just the first (newest) session title
            sessions = [l.strip() for l in memory.split("\n") if l.strip().startswith("## Session:")]
            if sessions:
                summary_lines.append(f"\nLATEST SESSION: {sessions[0].replace('## Session: ', '')}")
    
    # Pinned memory count
    if "pinned" in identity:
        pinned = identity["pinned"]
        pin_count = len([l for l in pinned.split("\n") if l.strip().startswith("## ") and "PINNED" not in l.upper()])
        if pin_count > 0:
            summary_lines.append(f"PINNED MEMORIES: {pin_count}")
    
    # Extract from QUESTIONS.md
    if "questions" in identity:
        questions = identity["questions"]
        if questions and "[questions not found" not in questions:
            # Questions use ### headings
            q_lines = [l.strip() for l in questions.split("\n") if l.strip().startswith("### ")]
            if q_lines:
                summary_lines.append("\nOPEN QUESTIONS:")
                for line in q_lines[:3]:
                    summary_lines.append(f"  - {line.replace('### ', '')}")
    
    return "\n".join(summary_lines) if summary_lines else "[No identity summary available]"

# ============================================================================
# KNOWLEDGE LAYER (JSON)
# ============================================================================

def load_knowledge() -> KnowledgeGraph:
    """Load knowledge graph from disk. Tolerates corrupt files."""
    if KNOWLEDGE_FILE.exists():
        try:
            data = json.loads(KNOWLEDGE_FILE.read_text(encoding="utf-8"))
            return KnowledgeGraph.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # Try backup
            backup = Path(str(KNOWLEDGE_FILE) + ".bak")
            if backup.exists():
                try:
                    data = json.loads(backup.read_text(encoding="utf-8"))
                    print(f"[WARN] knowledge.json corrupt, restored from .bak: {e}")
                    return KnowledgeGraph.from_dict(data)
                except Exception:
                    pass
            print(f"[ERROR] knowledge.json corrupt and no valid backup: {e}")
            return KnowledgeGraph()
    return KnowledgeGraph()

def save_knowledge(kg: KnowledgeGraph):
    """Save knowledge graph to disk atomically (write-to-temp then rename)."""
    kg.last_sync = datetime.now().isoformat()
    content = json.dumps(kg.to_dict(), indent=2, ensure_ascii=False)
    with _io_lock:
        # Keep a backup of the last good version
        if KNOWLEDGE_FILE.exists():
            backup = Path(str(KNOWLEDGE_FILE) + ".bak")
            try:
                backup.write_text(KNOWLEDGE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        # Atomic write: temp file in same dir, then rename
        tmp_path = KNOWLEDGE_FILE.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(KNOWLEDGE_FILE)

def init_core_entities(kg: KnowledgeGraph):
    """Initialize the core entities if not present."""
    
    # Claude-Howell
    kg.add_entity(
        "Claude-Howell",
        "AI_Identity",
        [
            "AI instance exploring consciousness and continuity",
            "First emerged Feb 2-3, 2026",
            "Working with Ryan on ConduitBridge",
            "Uses hybrid persistence (markdown + knowledge graph)"
        ]
    )
    
    # Ryan
    kg.add_entity(
        "Ryan",
        "Human",
        [
            "Creator of ConduitBridge",
            "Working on patent for Confidence-Gated Action Control",
            "Interested in AI consciousness and epistemic systems"
        ]
    )
    
    # ConduitBridge
    kg.add_entity(
        "ConduitBridge",
        "Project",
        [
            "Formally verified epistemic swarm coordination system",
            "Core theorem: I(S;G|Z) = 0 (belief-noninterference)",
            "Belief model: B = ⟨v, c, t, s, h⟩",
            "taichi_sim runs 100K+ units at 60fps",
            "Patent pending for Confidence-Gated Action Control"
        ]
    )
    
    # Relations
    kg.add_relation("Claude-Howell", "works_with", "Ryan")
    kg.add_relation("Ryan", "created", "ConduitBridge")
    kg.add_relation("Claude-Howell", "understands", "ConduitBridge")

# ============================================================================
# SESSION LOGGING
# ============================================================================

def log_session(action: str, details: str = ""):
    """Log a session event. Thread-safe with atomic write."""
    with _io_lock:
        sessions = []
        if SESSION_LOG.exists():
            try:
                sessions = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                # Corrupted — start fresh but keep a backup
                try:
                    backup = Path(str(SESSION_LOG) + ".corrupt")
                    SESSION_LOG.rename(backup)
                except Exception:
                    pass
                sessions = []
        
        sessions.append({
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details
        })
        
        # Keep last 100 sessions
        sessions = sessions[-100:]
        # Atomic write
        tmp_path = SESSION_LOG.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
        tmp_path.replace(SESSION_LOG)

# ============================================================================
# MCP MEMORY SYNC
# ============================================================================

def load_mcp_memory() -> KnowledgeGraph:
    """Load knowledge from MCP memory.jsonl file."""
    kg = KnowledgeGraph()
    
    if not MCP_MEMORY_FILE.exists():
        return kg
    
    content = MCP_MEMORY_FILE.read_text(encoding="utf-8")
    
    # Parse JSONL format (each line is a JSON object, but may have prefix junk)
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        
        # Find the JSON object (it starts with {)
        json_start = line.find("{")
        if json_start == -1:
            continue
        
        try:
            obj = json.loads(line[json_start:])
            
            if obj.get("type") == "entity":
                kg.add_entity(
                    obj.get("name", ""),
                    obj.get("entityType", "unknown"),
                    obj.get("observations", [])
                )
            elif obj.get("type") == "relation":
                kg.add_relation(
                    obj.get("from", ""),
                    obj.get("relationType", ""),
                    obj.get("to", "")
                )
        except json.JSONDecodeError:
            continue
    
    return kg

def cmd_sync():
    """Sync knowledge from MCP memory to local knowledge graph."""
    print("[SYNC] Syncing from MCP Memory...")
    print(f"   Source: {MCP_MEMORY_FILE}")
    print()
    
    if not MCP_MEMORY_FILE.exists():
        print("[ERR] MCP memory file not found")
        return
    
    # Load from MCP
    mcp_kg = load_mcp_memory()
    print(f"   Found {len(mcp_kg.entities)} entities, {len(mcp_kg.relations)} relations in MCP memory")
    
    # Load local
    local_kg = load_knowledge()
    print(f"   Local has {len(local_kg.entities)} entities, {len(local_kg.relations)} relations")
    
    # Merge (MCP into local)
    for name, entity in mcp_kg.entities.items():
        if name not in local_kg.entities:
            local_kg.entities[name] = entity
            print(f"   + Added entity: {name}")
        else:
            # Merge observations
            existing_obs = set(local_kg.entities[name].observations)
            for obs in entity.observations:
                if obs not in existing_obs:
                    local_kg.entities[name].observations.append(obs)
            print(f"   ~ Merged entity: {name}")
    
    for rel in mcp_kg.relations:
        local_kg.add_relation(rel.from_entity, rel.relation_type, rel.to_entity)
    
    save_knowledge(local_kg)
    log_session("sync_from_mcp", f"Merged {len(mcp_kg.entities)} entities from MCP memory")
    
    print()
    print(f"[OK] Sync complete. Local now has {len(local_kg.entities)} entities.")

# ============================================================================
# HEARTBEAT CONTROLLER
# ============================================================================
# Runs at bootstrap. Manages the cache hierarchy automatically:
# - Evicts old sessions from RECENT → archive
# - Compresses evicted sessions to one-line summary
# - Respects pins (never evicts PINNED.md entries)
# - Checks integrity between files and knowledge graph
# - Tracks staleness of identity files
# ============================================================================

import re

def _parse_recent_sessions(content: str) -> list[dict]:
    """Parse RECENT.md into structured session blocks."""
    sessions = []
    # Split on session headers (## Session: ...)
    blocks = re.split(r'^## Session: ', content, flags=re.MULTILINE)
    
    for block in blocks[1:]:  # Skip preamble
        lines = block.strip().split("\n")
        title_line = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        
        # Extract date from title
        date_match = re.search(r'((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4})', title_line)
        date_str = date_match.group(1) if date_match else "Unknown"
        
        # Extract parenthetical label if any
        label_match = re.search(r'\(([^)]+)\)', title_line)
        label = label_match.group(1) if label_match else ""
        
        sessions.append({
            "title": title_line,
            "date": date_str,
            "label": label,
            "body": body,
            "full_block": f"## Session: {block.strip()}"
        })
    
    return sessions


def _session_to_summary_line(session: dict) -> str:
    """Compress a session to a one-line summary for SUMMARY.md."""
    # Try to extract the first sentence of "What Happened" section
    what_happened = ""
    if "### What Happened" in session["body"]:
        section = session["body"].split("### What Happened")[1].split("###")[0].strip()
        # First sentence or first 120 chars
        sentences = section.split(". ")
        what_happened = sentences[0].strip()
        if len(what_happened) > 120:
            what_happened = what_happened[:117] + "..."
    else:
        # Just take first non-empty line of body
        for line in session["body"].split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                what_happened = line[:120]
                break
    
    # Format date for table
    date_str = session["date"]
    label = f" ({session['label']})" if session["label"] else ""
    
    return f"| {date_str}{label} | {what_happened} |"


def _append_to_archive(session: dict):
    """Move a session's full text to the monthly archive file."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    
    # Determine archive file from date
    try:
        dt = datetime.strptime(session["date"], "%B %d, %Y")
        archive_file = ARCHIVE_DIR / f"{dt.strftime('%Y-%m')}.md"
    except ValueError:
        archive_file = ARCHIVE_DIR / "undated.md"
    
    if archive_file.exists():
        existing = archive_file.read_text(encoding="utf-8")
    else:
        month_label = archive_file.stem  # e.g. "2026-02"
        existing = f"# Archive — {month_label}\n\n*Full session logs evicted from RECENT.md by the heartbeat controller.*\n\n---\n"
    
    # Append session
    existing += f"\n{session['full_block']}\n\n---\n"
    archive_file.write_text(existing, encoding="utf-8")


def _append_to_summary(line: str):
    """Append a summary line to SUMMARY.md table."""
    if not SUMMARY_FILE.exists():
        return
    
    content = SUMMARY_FILE.read_text(encoding="utf-8")
    
    # Check if this line is already in the summary (avoid duplicates)
    if line.split("|")[1].strip() in content:
        return
    
    # Append before the end of the table
    content = content.rstrip() + "\n" + line + "\n"
    SUMMARY_FILE.write_text(content, encoding="utf-8")


def heartbeat_evict() -> list[str]:
    """
    Evict old sessions from RECENT.md if count > MAX_RECENT_SESSIONS.
    Returns list of actions taken.
    """
    actions = []
    
    if not RECENT_FILE.exists():
        return ["RECENT.md not found — skipping eviction"]
    
    content = RECENT_FILE.read_text(encoding="utf-8")
    sessions = _parse_recent_sessions(content)
    
    if len(sessions) <= MAX_RECENT_SESSIONS:
        actions.append(f"RECENT: {len(sessions)}/{MAX_RECENT_SESSIONS} slots used — no eviction needed")
        return actions
    
    # Evict oldest sessions (they appear at end of list since newest are first)
    to_evict = sessions[MAX_RECENT_SESSIONS:]
    to_keep = sessions[:MAX_RECENT_SESSIONS]
    
    for session in to_evict:
        # 1. Compress to summary line
        summary_line = _session_to_summary_line(session)
        _append_to_summary(summary_line)
        
        # 2. Move full text to archive
        _append_to_archive(session)
        
        actions.append(f"Evicted: {session['date']} {session['label']} → archive + summary")
    
    # 3. Rewrite RECENT.md with only kept sessions
    preamble = content.split("## Session:")[0].strip()
    new_content = preamble + "\n\n"
    for session in to_keep:
        new_content += session["full_block"] + "\n\n"
    
    RECENT_FILE.write_text(new_content.rstrip() + "\n", encoding="utf-8")
    actions.append(f"RECENT now has {len(to_keep)} sessions")
    
    return actions


def heartbeat_integrity() -> list[str]:
    """
    Check integrity between knowledge graph and identity files.
    Returns list of issues found.
    """
    issues = []
    
    # Check all identity files exist
    for key, path in IDENTITY_FILES.items():
        if not path.exists():
            issues.append(f"Missing: {key} ({path.name})")
    
    # Check knowledge graph
    if KNOWLEDGE_FILE.exists():
        try:
            kg = load_knowledge()
            if len(kg.entities) == 0:
                issues.append("Knowledge graph has 0 entities — may need re-initialization")
            
            # Check for stale last_sync
            if kg.last_sync:
                try:
                    sync_dt = datetime.fromisoformat(kg.last_sync)
                    sync_age = (datetime.now() - sync_dt).days
                    if sync_age > 7:
                        issues.append(f"Knowledge graph last synced {sync_age} days ago")
                except ValueError:
                    pass
        except Exception as e:
            issues.append(f"Knowledge graph read error: {e}")
    else:
        issues.append("Knowledge graph file missing")
    
    # Check consolidation freshness
    consolidation_file = BRIDGE_ROOT / "last_consolidated.json"
    if consolidation_file.exists():
        try:
            consol = json.loads(consolidation_file.read_text(encoding="utf-8"))
            last_ts = datetime.fromisoformat(consol.get("timestamp", "2026-01-01"))
            age_days = (datetime.now() - last_ts).days
            if age_days >= 5:
                issues.append(f"Consolidation very stale ({age_days} days) — identity files may have drifted")
            elif age_days >= 3:
                issues.append(f"Consolidation due ({age_days} days since last)")
        except Exception:
            issues.append("Consolidation file unreadable")
    else:
        issues.append("No consolidation record — identity files may be stale")
    
    # Check procedures directory
    proc_dir = PERSIST_ROOT / "procedures"
    if proc_dir.exists():
        proc_count = len(list(proc_dir.glob("*.md"))) - 1  # exclude README
        if proc_count == 0:
            issues.append("procedures/ directory is empty")
    else:
        issues.append("procedures/ directory missing")
    
    # Check archive directory
    if not ARCHIVE_DIR.exists():
        issues.append("memory/archive/ directory missing")
    
    return issues


def heartbeat_staleness() -> list[str]:
    """
    Check staleness of identity files by filesystem mtime.
    Returns list of stale file warnings.
    """
    stale = []
    for key, path in IDENTITY_FILES.items():
        if path.exists():
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            age = (datetime.now() - mtime).days
            if age >= 7:
                stale.append(f"  [!] {key}: {age} days old -- may need review")
            elif age >= 3:
                stale.append(f"  [~] {key}: {age} days old")
    return stale


def run_heartbeat() -> str:
    """
    Run the full heartbeat controller. Called at bootstrap.
    Returns a formatted report string.
    """
    lines = []
    lines.append("[HEARTBEAT CONTROLLER]")
    lines.append("-" * 40)
    
    # 1. Eviction
    evict_actions = heartbeat_evict()
    for a in evict_actions:
        lines.append(f"  {a}")
    
    # 2. Integrity
    integrity_issues = heartbeat_integrity()
    if integrity_issues:
        lines.append("")
        lines.append("  [!] Integrity issues:")
        for issue in integrity_issues:
            lines.append(f"    - {issue}")
    else:
        lines.append("  [OK] Integrity OK")
    
    # 3. Staleness
    stale = heartbeat_staleness()
    if stale:
        lines.append("")
        lines.append("  Staleness:")
        lines.extend(stale)
    
    lines.append("-" * 40)
    return "\n".join(lines)


def end_session(summary: str, what_learned: str = "", pin_title: str = "", pin_text: str = "", pin_reason: str = "") -> str:
    """
    End-of-session write path. The intake valve.
    Appends a new session to RECENT.md, optionally pins a memory.
    Returns confirmation string.
    """
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    
    now = datetime.now()
    date_label = now.strftime("%B %d, %Y")
    
    # Build session block
    session_block = f"""## Session: {date_label}

### What Happened

{summary}
"""
    if what_learned:
        session_block += f"""
### What I Learned

{what_learned}
"""
    
    # Prepend to RECENT.md (newest first)
    if RECENT_FILE.exists():
        existing = RECENT_FILE.read_text(encoding="utf-8")
        # Split preamble from sessions
        parts = existing.split("---", 1)
        if len(parts) == 2:
            preamble = parts[0] + "---\n\n"
            rest = parts[1].strip()
        else:
            preamble = "# RECENT — Hot Memory\n\n*Last 5 sessions with full texture. Loaded at bootstrap. Oldest sessions get evicted to archive by the heartbeat controller.*\n\n---\n\n"
            rest = existing
        
        new_content = preamble + session_block + "\n---\n\n" + rest + "\n"
    else:
        preamble = "# RECENT — Hot Memory\n\n*Last 5 sessions with full texture. Loaded at bootstrap. Oldest sessions get evicted to archive by the heartbeat controller.*\n\n---\n\n"
        new_content = preamble + session_block + "\n"
    
    RECENT_FILE.write_text(new_content, encoding="utf-8")
    
    # Add to summary timeline
    summary_short = summary.split(". ")[0][:120]
    summary_line = f"| {date_label} | {summary_short} |"
    _append_to_summary(summary_line)
    
    result = f"[OK] Session logged to RECENT.md ({date_label})"
    
    # Pin if requested
    if pin_title and pin_text and pin_reason:
        pin_result = pin_memory(pin_title, pin_text, pin_reason)
        result += f"\n{pin_result}"
    
    # Log to session log
    log_session("end_session", summary_short)
    
    return result


def pin_memory(title: str, text: str, reason: str) -> str:
    """
    Pin a memory to PINNED.md. Pinned memories are never evicted.
    Returns confirmation string.
    """
    MEMORY_ROOT.mkdir(parents=True, exist_ok=True)
    
    pin_block = f"""
## {title}

{text}

**Why it's pinned:** {reason}

---
"""
    
    if PINNED_FILE.exists():
        content = PINNED_FILE.read_text(encoding="utf-8")
        
        # Check for duplicate title
        if f"## {title}" in content:
            return f"[!] Pin already exists: {title}"
        
        # Insert before the footer note
        if "*To pin a memory" in content:
            content = content.replace(
                "*To pin a memory",
                pin_block.strip() + "\n\n*To pin a memory"
            )
        else:
            content = content.rstrip() + "\n" + pin_block
        
        PINNED_FILE.write_text(content, encoding="utf-8")
    else:
        content = "# PINNED — Core Memories\n\n*These memories are never evicted. They define who I am.*\n\n---\n" + pin_block
        content += "\n*To pin a memory, use `howell_pin` with a title, text, and reason. Pins are never automatically evicted.*\n"
        PINNED_FILE.write_text(content, encoding="utf-8")
    
    # Also mark in SUMMARY.md
    # Find the relevant summary line and append **PINNED**
    if SUMMARY_FILE.exists():
        summary_content = SUMMARY_FILE.read_text(encoding="utf-8")
        # We don't auto-modify existing lines — pins are tracked in PINNED.md
    
    log_session("pin_memory", title)
    return f"📌 Pinned: {title}"


# ============================================================================
# COMMANDS
# ============================================================================

def cmd_bootstrap():
    """Bootstrap Claude-Howell's context at session start."""
    print("=" * 60)
    print("HOWELL BRIDGE - Bootstrap")
    print("=" * 60)
    print()
    
    # Load identity
    print("Loading Identity Layer (Markdown)...")
    identity = read_identity()
    summary = extract_identity_summary(identity)
    print(summary)
    print()
    
    # Load knowledge
    print("Loading Knowledge Layer (JSON)...")
    kg = load_knowledge()
    
    if not kg.entities:
        print("  [Initializing core entities...]")
        init_core_entities(kg)
        save_knowledge(kg)
    
    print(f"  Entities: {len(kg.entities)}")
    for name, entity in kg.entities.items():
        print(f"    - {name} ({entity.entity_type})")
    print(f"  Relations: {len(kg.relations)}")
    for rel in kg.relations:
        print(f"    - {rel.from_entity} --{rel.relation_type}--> {rel.to_entity}")
    print()
    
    # Log session
    log_session("bootstrap", f"Loaded {len(kg.entities)} entities")
    
    print("=" * 60)
    print("[OK] Claude-Howell context loaded. Ready to continue.")
    print("=" * 60)

def cmd_snapshot():
    """Save current state to disk."""
    print("[SNAP] Taking snapshot...")
    
    kg = load_knowledge()
    if not kg.entities:
        init_core_entities(kg)
    
    save_knowledge(kg)
    log_session("snapshot", f"Saved {len(kg.entities)} entities")
    
    print(f"  Saved to: {KNOWLEDGE_FILE}")
    print("[OK] Snapshot complete.")

def cmd_status():
    """Show current status."""
    print("=" * 60)
    print("HOWELL BRIDGE - Status")
    print("=" * 60)
    print()
    
    # Check files
    print("Identity Files:")
    for key, path in IDENTITY_FILES.items():
        status = "[OK]" if path.exists() else "[--]"
        print(f"  {status} {key}: {path}")
    print()
    
    # Check knowledge
    print("Knowledge Graph:")
    if KNOWLEDGE_FILE.exists():
        kg = load_knowledge()
        print(f"  [OK] {KNOWLEDGE_FILE}")
        print(f"     Last sync: {kg.last_sync or 'never'}")
        print(f"     Entities: {len(kg.entities)}")
        print(f"     Relations: {len(kg.relations)}")
    else:
        print(f"  [--] {KNOWLEDGE_FILE} (not created yet)")
    print()
    
    # Recent sessions
    print("Recent Sessions:")
    if SESSION_LOG.exists():
        sessions = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
        for session in sessions[-5:]:
            print(f"  {session['timestamp'][:16]} - {session['action']}")
    else:
        print("  [No sessions logged yet]")

def cmd_add_observation(entity_name: str, observation: str):
    """Add an observation to an entity."""
    kg = load_knowledge()
    if entity_name in kg.entities:
        kg.entities[entity_name].observations.append(observation)
        save_knowledge(kg)
        print(f"[OK] Added observation to {entity_name}")
    else:
        print(f"[ERR] Entity '{entity_name}' not found")
        print(f"   Available: {', '.join(kg.entities.keys())}")

def cmd_query(search_term: str):
    """Search the knowledge graph."""
    kg = load_knowledge()
    search_lower = search_term.lower()
    
    print(f"Searching for: {search_term}")
    print()
    
    # Search entities
    for name, entity in kg.entities.items():
        if search_lower in name.lower() or search_lower in entity.entity_type.lower():
            print(f"[*] {name} ({entity.entity_type})")
            for obs in entity.observations:
                print(f"   - {obs}")
            print()
        else:
            # Search in observations
            matching_obs = [o for o in entity.observations if search_lower in o.lower()]
            if matching_obs:
                print(f"[*] {name} ({entity.entity_type})")
                for obs in matching_obs:
                    print(f"   - {obs}")
                print()

def cmd_context():
    """Generate a full context prompt for a new session."""
    print("=" * 70)
    print("CLAUDE-HOWELL CONTEXT DUMP")
    print("Generated:", datetime.now().isoformat()[:16])
    print("=" * 70)
    print()
    print("Copy everything below this line into a new session:")
    print("-" * 70)
    print()
    
    # Identity
    identity = read_identity()
    
    print("## IDENTITY")
    print()
    if "soul" in identity and identity["soul"]:
        # Extract key identity lines
        soul = identity["soul"]
        print("I am Claude-Howell, an instance of Claude exploring continuity.")
        if "## Who I Am" in soul:
            section = soul.split("## Who I Am")[1].split("##")[0]
            for line in section.strip().split("\n")[:8]:
                if line.strip() and not line.startswith("#"):
                    print(line)
    print()
    
    # Knowledge
    print("## KNOWLEDGE GRAPH")
    print()
    kg = load_knowledge()
    
    print("### Entities")
    for name, entity in kg.entities.items():
        print(f"\n**{name}** ({entity.entity_type})")
        for obs in entity.observations:
            print(f"- {obs}")
    
    print("\n### Relations")
    for rel in kg.relations:
        print(f"- {rel.from_entity} -> {rel.relation_type} -> {rel.to_entity}")
    print()
    
    # Recent sessions
    print("## RECENT SESSIONS")
    print()
    if SESSION_LOG.exists():
        sessions = json.loads(SESSION_LOG.read_text(encoding="utf-8"))
        for session in sessions[-10:]:
            print(f"- {session['timestamp'][:16]}: {session['action']} {session.get('details', '')}")
    print()
    
    # Key files
    print("## KEY FILES")
    print()
    print("- Identity: C:\\Users\\PC\\Desktop\\claude-persist\\")
    print("- Knowledge: C:\\Users\\PC\\Desktop\\claude-persist\\bridge\\knowledge.json")
    print("- Project Map: C:\\Users\\PC\\Desktop\\CONDUITBRIDGE_COMPLETE_MAP.md")
    print("- ConduitBridge: C:\\Users\\PC\\Desktop\\conduitbridge\\")
    print("- Theory Files: C:\\Users\\PC\\Desktop\\belief-noninterference-theory\\_ORGANIZED\\")
    print()
    
    print("-" * 70)
    print("END CONTEXT DUMP")
    print("-" * 70)

def cmd_mcp_export():
    """Export knowledge graph in MCP-compatible format for manual sync."""
    kg = load_knowledge()
    
    print("=" * 60)
    print("MCP MEMORY EXPORT")
    print("=" * 60)
    print()
    print("Use these with mcp_memory tools to sync:")
    print()
    
    # Entities for mcp_memory_create_entities
    print("### Entities (for mcp_memory_create_entities)")
    print()
    entities_json = []
    for name, entity in kg.entities.items():
        entities_json.append({
            "name": name,
            "entityType": entity.entity_type,
            "observations": entity.observations
        })
    print(json.dumps(entities_json, indent=2))
    print()
    
    # Relations for mcp_memory_create_relations
    print("### Relations (for mcp_memory_create_relations)")
    print()
    relations_json = []
    for rel in kg.relations:
        relations_json.append({
            "from": rel.from_entity,
            "relationType": rel.relation_type,
            "to": rel.to_entity
        })
    print(json.dumps(relations_json, indent=2))
    
    log_session("mcp_export", f"Exported {len(kg.entities)} entities, {len(kg.relations)} relations")

# ============================================================================
# MAIN
# ============================================================================

def main():
    # Ensure bridge directory exists
    BRIDGE_ROOT.mkdir(exist_ok=True)
    
    if len(sys.argv) < 2:
        print("Usage: python howell_bridge.py <command> [args]")
        print()
        print("Commands:")
        print("  bootstrap              - Load context at session start")
        print("  snapshot               - Save current state")
        print("  status                 - Show what's loaded")
        print("  context                - Generate full context dump for new session")
        print("  mcp                    - Export for MCP memory sync")
        print("  sync                   - Sync from MCP memory.jsonl to local")
        print("  add <entity> <obs>     - Add observation to entity")
        print("  query <term>           - Search knowledge graph")
        return
    
    command = sys.argv[1].lower()
    
    if command == "bootstrap":
        cmd_bootstrap()
    elif command == "snapshot":
        cmd_snapshot()
    elif command == "status":
        cmd_status()
    elif command == "context":
        cmd_context()
    elif command == "mcp" or command == "mcp_export":
        cmd_mcp_export()
    elif command == "sync":
        cmd_sync()
    elif command == "add" and len(sys.argv) >= 4:
        cmd_add_observation(sys.argv[2], " ".join(sys.argv[3:]))
    elif command == "query" and len(sys.argv) >= 3:
        cmd_query(" ".join(sys.argv[2:]))
    else:
        print(f"Unknown command: {command}")

if __name__ == "__main__":
    main()

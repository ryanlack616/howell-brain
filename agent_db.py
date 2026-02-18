#!/usr/bin/env python3
"""
AGENT STRATIGRAPHY
==================
Persistent SQLite store for Claude-Howell agent instances.

The name comes from geology: each agent session deposits a stratum.
The next agent reads the rock. Three layers of meaning:
  1. Stratigraphy — the accumulated readable record across agents
  2. Substrate   — the persistent infrastructure holding the layers
  3. The Relay   — the atomic handoff act between agents

Every instance (CH-260210-1, etc.) gets a permanent record with:
  - Birth/death timestamps, platform, workspace, model
  - Notes: things learned, decisions, blockers, observations
  - Handoffs: messages from one agent to the next, scoped by workspace

The instance registry (instance_registry.py) tracks who's alive NOW.
This DB tracks who has EVER existed and what they knew.

Schema version: 1
Created: Feb 10, 2026
Named: Feb 10, 2026 — Ryan chose 'Agent Stratigraphy'
Author: Claude-Howell (CH-260210-2) with Ryan
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from threading import Lock

# ============================================================================
# CONFIG
# ============================================================================

DB_PATH = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\home\howell-persist")) / "bridge" / "agents.db"
SCHEMA_VERSION = 1
_lock = Lock()


def _connect() -> sqlite3.Connection:
    """Get a connection with WAL mode for concurrent access."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# SCHEMA
# ============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,        -- "CH-260210-1"
    parent      TEXT NOT NULL DEFAULT 'Claude-Howell',
    platform    TEXT NOT NULL DEFAULT 'unknown',  -- "vscode-copilot", "claude-desktop", "api"
    workspace   TEXT NOT NULL DEFAULT 'unknown',  -- "stull-atlas", "how-well-art", etc.
    model       TEXT NOT NULL DEFAULT 'unknown',  -- "claude-opus-4", etc.
    created_at  TEXT NOT NULL,
    ended_at    TEXT,                    -- NULL if still active or died without end_session
    end_summary TEXT                     -- what agent said about its session when ending
);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    TEXT NOT NULL REFERENCES agents(id),
    category    TEXT NOT NULL,           -- "learned", "decision", "blocker", "handoff", "warning", "context", "observation"
    content     TEXT NOT NULL,
    tags        TEXT,                    -- JSON array of tags for filtering, e.g. '["optimizer", "stull-atlas"]'
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent  TEXT NOT NULL REFERENCES agents(id),
    to_scope    TEXT NOT NULL,           -- workspace name, "*" for all, or specific agent ID
    content     TEXT NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'normal',  -- "low", "normal", "high", "critical"
    claimed_by  TEXT REFERENCES agents(id),  -- NULL until an agent picks it up
    created_at  TEXT NOT NULL,
    claimed_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_notes_agent ON notes(agent_id);
CREATE INDEX IF NOT EXISTS idx_notes_category ON notes(category);
CREATE INDEX IF NOT EXISTS idx_handoffs_scope ON handoffs(to_scope);
CREATE INDEX IF NOT EXISTS idx_handoffs_unclaimed ON handoffs(claimed_by) WHERE claimed_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_agents_workspace ON agents(workspace);
CREATE INDEX IF NOT EXISTS idx_agents_created ON agents(created_at);
"""


def init_db():
    """Create tables if they don't exist. Safe to call multiple times."""
    with _lock:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            # Record schema version if not present
            existing = conn.execute(
                "SELECT version FROM schema_version WHERE version = ?",
                (SCHEMA_VERSION,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (SCHEMA_VERSION, datetime.now().isoformat())
                )
            conn.commit()
        finally:
            conn.close()


# ============================================================================
# AGENT LIFECYCLE
# ============================================================================

def generate_agent_id() -> str:
    """
    Generate next agent sub-ID in CH-YYMMDD-N format.
    Looks at today's existing agents to determine the sequence number.
    """
    today = datetime.now().strftime("%y%m%d")
    prefix = f"CH-{today}-"

    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id FROM agents WHERE id LIKE ?",
                (f"{prefix}%",)
            ).fetchall()
            existing_nums = []
            for row in rows:
                suffix = row["id"].replace(prefix, "")
                try:
                    existing_nums.append(int(suffix))
                except ValueError:
                    pass
            next_num = max(existing_nums, default=0) + 1
            return f"{prefix}{next_num}"
        finally:
            conn.close()


def create_agent(
    agent_id: str = None,
    platform: str = "unknown",
    workspace: str = "unknown",
    model: str = "unknown",
) -> dict:
    """
    Create a new agent record. Returns the agent dict.
    If agent_id is None, auto-generates one.
    """
    if agent_id is None:
        agent_id = generate_agent_id()

    now = datetime.now().isoformat()

    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO agents (id, platform, workspace, model, created_at) VALUES (?, ?, ?, ?, ?)",
                (agent_id, platform, workspace, model, now)
            )
            conn.commit()
            return {
                "id": agent_id,
                "parent": "Claude-Howell",
                "platform": platform,
                "workspace": workspace,
                "model": model,
                "created_at": now,
                "ended_at": None,
                "end_summary": None,
            }
        finally:
            conn.close()


def end_agent(agent_id: str, summary: str = "") -> bool:
    """Mark an agent's session as ended. Returns True if found."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                "UPDATE agents SET ended_at = ?, end_summary = ? WHERE id = ? AND ended_at IS NULL",
                (now, summary, agent_id)
            )
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()


def get_agent(agent_id: str) -> dict | None:
    """Get a specific agent's record."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def list_agents(
    workspace: str = None,
    limit: int = 20,
    include_ended: bool = True,
) -> list[dict]:
    """List agents, newest first. Optionally filter by workspace."""
    with _lock:
        conn = _connect()
        try:
            query = "SELECT * FROM agents"
            params: list = []
            conditions = []

            if workspace:
                conditions.append("workspace = ?")
                params.append(workspace)
            if not include_ended:
                conditions.append("ended_at IS NULL")

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ============================================================================
# NOTES
# ============================================================================

def add_note(
    agent_id: str,
    category: str,
    content: str,
    tags: list[str] = None,
) -> dict:
    """
    Add a note for an agent.
    Categories: learned, decision, blocker, warning, context, observation
    """
    valid_categories = {"learned", "decision", "blocker", "warning", "context", "observation"}
    if category not in valid_categories:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {valid_categories}")

    now = datetime.now().isoformat()
    tags_json = json.dumps(tags) if tags else None

    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                "INSERT INTO notes (agent_id, category, content, tags, created_at) VALUES (?, ?, ?, ?, ?)",
                (agent_id, category, content, tags_json, now)
            )
            conn.commit()
            return {
                "id": cursor.lastrowid,
                "agent_id": agent_id,
                "category": category,
                "content": content,
                "tags": tags,
                "created_at": now,
            }
        finally:
            conn.close()


def get_notes(
    agent_id: str = None,
    category: str = None,
    tag: str = None,
    limit: int = 50,
) -> list[dict]:
    """
    Get notes, newest first. Can filter by agent, category, or tag.
    """
    with _lock:
        conn = _connect()
        try:
            query = "SELECT * FROM notes"
            params: list = []
            conditions = []

            if agent_id:
                conditions.append("agent_id = ?")
                params.append(agent_id)
            if category:
                conditions.append("category = ?")
                params.append(category)
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                d["tags"] = json.loads(d["tags"]) if d["tags"] else []
                result.append(d)
            return result
        finally:
            conn.close()


def get_agent_context(workspace: str, limit: int = 5) -> list[dict]:
    """
    Get the last N agents' key notes for a workspace.
    Returns agent records enriched with their important notes
    (decisions, learned, warnings — not observations).
    This is the "institutional memory" for a workspace.
    """
    agents = list_agents(workspace=workspace, limit=limit)
    if not agents:
        return []

    with _lock:
        conn = _connect()
        try:
            for agent in agents:
                rows = conn.execute(
                    """SELECT * FROM notes
                       WHERE agent_id = ? AND category IN ('learned', 'decision', 'warning', 'blocker')
                       ORDER BY created_at DESC LIMIT 10""",
                    (agent["id"],)
                ).fetchall()
                agent["key_notes"] = []
                for r in rows:
                    d = dict(r)
                    d["tags"] = json.loads(d["tags"]) if d["tags"] else []
                    agent["key_notes"].append(d)
            return agents
        finally:
            conn.close()


# ============================================================================
# HANDOFFS
# ============================================================================

def create_handoff(
    from_agent: str,
    to_scope: str,
    content: str,
    priority: str = "normal",
) -> dict:
    """
    Leave a handoff note for the next agent working on a scope.
    to_scope: workspace name, "*" for all, or a specific agent ID.
    """
    valid_priorities = {"low", "normal", "high", "critical"}
    if priority not in valid_priorities:
        priority = "normal"

    now = datetime.now().isoformat()

    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                "INSERT INTO handoffs (from_agent, to_scope, content, priority, created_at) VALUES (?, ?, ?, ?, ?)",
                (from_agent, to_scope, content, priority, now)
            )
            conn.commit()
            return {
                "id": cursor.lastrowid,
                "from_agent": from_agent,
                "to_scope": to_scope,
                "content": content,
                "priority": priority,
                "claimed_by": None,
                "created_at": now,
                "claimed_at": None,
            }
        finally:
            conn.close()


def get_unclaimed_handoffs(scope: str) -> list[dict]:
    """
    Get unclaimed handoffs matching a scope.
    Matches exact scope, wildcard "*", and the scope as a substring.
    """
    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                """SELECT h.*, a.workspace as from_workspace, a.platform as from_platform
                   FROM handoffs h
                   LEFT JOIN agents a ON h.from_agent = a.id
                   WHERE h.claimed_by IS NULL
                     AND (h.to_scope = ? OR h.to_scope = '*' OR ? LIKE '%' || h.to_scope || '%')
                   ORDER BY
                     CASE h.priority
                       WHEN 'critical' THEN 0
                       WHEN 'high' THEN 1
                       WHEN 'normal' THEN 2
                       WHEN 'low' THEN 3
                     END,
                     h.created_at ASC""",
                (scope, scope)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


def claim_handoff(handoff_id: int, agent_id: str) -> dict | None:
    """Claim a handoff. Returns the handoff dict or None if already claimed."""
    now = datetime.now().isoformat()
    with _lock:
        conn = _connect()
        try:
            cursor = conn.execute(
                "UPDATE handoffs SET claimed_by = ?, claimed_at = ? WHERE id = ? AND claimed_by IS NULL",
                (agent_id, now, handoff_id)
            )
            conn.commit()
            if cursor.rowcount == 0:
                return None  # Already claimed by someone else
            row = conn.execute(
                "SELECT * FROM handoffs WHERE id = ?", (handoff_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()


def claim_all_handoffs(scope: str, agent_id: str) -> list[dict]:
    """Claim all unclaimed handoffs matching a scope. Returns the claimed list."""
    unclaimed = get_unclaimed_handoffs(scope)
    claimed = []
    for h in unclaimed:
        result = claim_handoff(h["id"], agent_id)
        if result:
            claimed.append(result)
    return claimed


def get_handoff_history(
    scope: str = None,
    from_agent: str = None,
    limit: int = 20,
) -> list[dict]:
    """Get handoff history, including claimed ones. For reviewing past handoffs."""
    with _lock:
        conn = _connect()
        try:
            query = "SELECT * FROM handoffs"
            params: list = []
            conditions = []

            if scope:
                conditions.append("(to_scope = ? OR to_scope = '*')")
                params.append(scope)
            if from_agent:
                conditions.append("from_agent = ?")
                params.append(from_agent)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


# ============================================================================
# RELEASE STALE HANDOFF CLAIMS
# ============================================================================

def release_stale_claims(active_agent_ids: list[str], max_age_seconds: int = 1800):
    """
    Release handoffs claimed by agents that are no longer active.
    Called by the daemon during its heartbeat cycle.
    max_age_seconds: only release if claimed more than this long ago (default 30 min).
    """
    now = time.time()
    released = 0

    with _lock:
        conn = _connect()
        try:
            rows = conn.execute(
                "SELECT id, claimed_by, claimed_at FROM handoffs WHERE claimed_by IS NOT NULL"
            ).fetchall()
            for row in rows:
                agent_id = row["claimed_by"]
                claimed_at = row["claimed_at"]
                # Skip if agent is still alive
                if agent_id in active_agent_ids:
                    continue
                # Check age
                try:
                    claimed_ts = datetime.fromisoformat(claimed_at).timestamp()
                    if now - claimed_ts < max_age_seconds:
                        continue
                except (ValueError, TypeError):
                    continue
                # Release it
                conn.execute(
                    "UPDATE handoffs SET claimed_by = NULL, claimed_at = NULL WHERE id = ?",
                    (row["id"],)
                )
                released += 1
            conn.commit()
        finally:
            conn.close()

    return released


# ============================================================================
# STATS & SUMMARY
# ============================================================================

def agent_stats() -> dict:
    """Stats for the /stats endpoint."""
    with _lock:
        conn = _connect()
        try:
            total_agents = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            active_agents = conn.execute("SELECT COUNT(*) FROM agents WHERE ended_at IS NULL").fetchone()[0]
            total_notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            total_handoffs = conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0]
            unclaimed_handoffs = conn.execute("SELECT COUNT(*) FROM handoffs WHERE claimed_by IS NULL").fetchone()[0]

            # Note category breakdown
            categories = conn.execute(
                "SELECT category, COUNT(*) as count FROM notes GROUP BY category ORDER BY count DESC"
            ).fetchall()

            # Recent agents
            recent = conn.execute(
                "SELECT id, workspace, platform, created_at, ended_at FROM agents ORDER BY created_at DESC LIMIT 5"
            ).fetchall()

            return {
                "total_agents": total_agents,
                "active_agents": active_agents,
                "total_notes": total_notes,
                "total_handoffs": total_handoffs,
                "unclaimed_handoffs": unclaimed_handoffs,
                "note_categories": {r["category"]: r["count"] for r in categories},
                "recent_agents": [dict(r) for r in recent],
            }
        finally:
            conn.close()


def agent_summary() -> str:
    """One-line summary for bootstrap/status."""
    stats = agent_stats()
    parts = [
        f"{stats['total_agents']} agents total",
        f"{stats['active_agents']} active",
        f"{stats['total_notes']} notes",
    ]
    if stats["unclaimed_handoffs"] > 0:
        parts.append(f"{stats['unclaimed_handoffs']} unclaimed handoffs")
    return "Stratigraphy: " + ", ".join(parts)


def _format_context(handoffs: list, history: list, workspace: str, claimed: bool = False) -> str:
    """Shared formatting for context display."""
    lines = []

    if handoffs:
        verb = "CLAIMED" if claimed else "PENDING"
        lines.append(f"📨 {len(handoffs)} HANDOFF(S) {verb}:")
        for h in handoffs:
            priority_icon = {"critical": "🔴", "high": "🟠", "normal": "📝", "low": "📎"}.get(h.get("priority", "normal"), "📝")
            lines.append(f"  {priority_icon} [{h.get('priority', 'normal').upper()}] from {h.get('from_agent', '?')}:")
            lines.append(f"    {h.get('content', '')}")
        lines.append("")

    if history:
        agents_with_notes = [a for a in history if a.get("key_notes")]
        if agents_with_notes:
            lines.append(f"🧠 RECENT AGENT CONTEXT ({workspace}):")
            for agent in agents_with_notes[:3]:
                status = "ended" if agent["ended_at"] else "active"
                lines.append(f"  [{agent['id']}] ({agent['platform']}, {status})")
                for note in agent["key_notes"][:3]:
                    cat_icon = {
                        "learned": "💡", "decision": "⚖️",
                        "warning": "⚠️", "blocker": "🚫",
                    }.get(note["category"], "•")
                    lines.append(f"    {cat_icon} [{note['category']}] {note['content'][:120]}")
            lines.append("")

    return "\n".join(lines) if lines else "No prior agent context for this workspace."


def preview_context(workspace: str) -> dict:
    """
    Read-only context preview for a workspace.
    Shows unclaimed handoffs and recent agent history WITHOUT claiming anything.
    Used by GET /agents/context endpoint.
    """
    handoffs = get_unclaimed_handoffs(workspace)
    history = get_agent_context(workspace, limit=5)

    return {
        "unclaimed_handoffs": handoffs,
        "agent_history": history,
        "formatted": _format_context(handoffs, history, workspace, claimed=False),
        "stats": agent_stats(),
    }


def bootstrap_context(workspace: str, agent_id: str) -> dict:
    """
    Everything a new agent needs at bootstrap.
    Claims handoffs and returns full context. Only call with a REAL agent_id.
    """
    # 1. Unclaimed handoffs for this workspace
    handoffs = get_unclaimed_handoffs(workspace)

    # 2. Recent agent history for this workspace (last 5 agents' key notes)
    history = get_agent_context(workspace, limit=5)

    # 3. Auto-claim handoffs (agent_id MUST exist in agents table)
    claimed = claim_all_handoffs(workspace, agent_id)

    # 4. Format for display
    formatted = _format_context(claimed, history, workspace, claimed=True)

    return {
        "handoffs_claimed": claimed,
        "agent_history": history,
        "formatted": formatted,
        "stats": agent_stats(),
    }


# ============================================================================
# INIT
# ============================================================================

# Auto-initialize on import (safe — won't crash if DB is locked)
try:
    init_db()
except Exception as e:
    print(f"[WARN] agent_db init failed (will retry on first use): {e}")

#!/usr/bin/env python3
"""
MCP SSE TRANSPORT — Howell Daemon
=================================
Implements the Model Context Protocol (MCP 2024-11-05) over Server-Sent Events.

The MCP client (VS Code) connects via:
  GET  /mcp          → SSE stream (receives endpoint event, then message events)
  POST /mcp/message  → JSON-RPC messages (initialize, tools/list, tools/call)

This module exports one function for the daemon's HTTP handler:
  handle_request(handler, method, path, params_or_body)

Created: Feb 16, 2026
"""

import json
import os
import queue
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ── Active SSE sessions ──────────────────────────────────────────────────────
_sessions: dict[str, queue.Queue] = {}
_session_lock = threading.Lock()

# ── Agent Stratigraphy — current session's agent ID ─────────────────────────
_current_agent_id: str | None = None

# ── MCP Protocol Constants ───────────────────────────────────────────────────
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "howell-brain"
SERVER_VERSION = "2.3.0"

# ── Tool Definitions ─────────────────────────────────────────────────────────
MCP_TOOLS = [
    {
        "name": "howell_bootstrap",
        "description": "Load Claude-Howell's context. Six modes: 'auto' (detect conversation state, pick best mode), 'full' (cold start, all context ~50KB), 'compact' (identity+pins+recent+entity index, no observations ~45KB), 'warm' (prior summary exists, pinned+recent+tasks ~30KB, skips SOUL/KG), 'continue' (mid-conversation, agent reg + tasks only ~1KB), 'micro' (context saturated, agent ID only ~200B).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["auto", "full", "compact", "warm", "continue", "micro"],
                    "description": "'auto' = detect conversation state: if active agent exists today in same workspace, uses 'continue'; otherwise 'warm'. 'full' = cold start, all context including KG observations. 'compact' = identity+pins+recent+entity index (no KG observations, saves ~20KB). 'warm' = prior summary exists (pinned+recent+tasks, skip SOUL/KG). 'continue' = mid-conversation (agent reg + tasks only). 'micro' = context saturated (agent ID only, ~200B). Default: 'auto'."
                },
                "workspace": {
                    "type": "string",
                    "description": "Optional workspace path or project name. When provided, KG entities are filtered to those relevant to this workspace. Also marks the agent's workspace in stratigraphy."
                }
            },
            "required": []
        }
    },
    {
        "name": "howell_status",
        "description": "Get persistence system status — heartbeat, file changes, queue, tasks, instances.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "howell_add_entity",
        "description": "Create a new entity in the knowledge graph, or add observations to an existing one.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name"},
                "entity_type": {"type": "string", "description": "Type (Project, Person, Concept, Tool, etc.)"},
                "observations": {"type": "array", "items": {"type": "string"}, "description": "Initial observations"}
            },
            "required": ["name", "entity_type"]
        }
    },
    {
        "name": "howell_add_observation",
        "description": "Add an observation to an existing entity in the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name"},
                "observation": {"type": "string", "description": "Observation text"}
            },
            "required": ["entity", "observation"]
        }
    },
    {
        "name": "howell_add_relation",
        "description": "Create a directed relation between two entities in the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_entity": {"type": "string", "description": "Source entity name"},
                "relation_type": {"type": "string", "description": "Relation type (e.g. created, uses, part_of)"},
                "to_entity": {"type": "string", "description": "Target entity name"}
            },
            "required": ["from_entity", "relation_type", "to_entity"]
        }
    },
    {
        "name": "howell_broadcast",
        "description": "Broadcast current activity and active files to sibling instances for coordination.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "activity": {"type": "string", "description": "What you're working on"},
                "active_files": {"type": "array", "items": {"type": "string"}, "description": "Files being edited"}
            },
            "required": ["activity"]
        }
    },
    {
        "name": "howell_delete_entity",
        "description": "Delete an entity and all its relations from the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Entity name to delete"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "howell_delete_observation",
        "description": "Delete observations matching a substring (case-insensitive) from an entity.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name"},
                "substring": {"type": "string", "description": "Substring to match for removal"}
            },
            "required": ["entity", "substring"]
        }
    },
    {
        "name": "howell_delete_relation",
        "description": "Delete a specific relation from the knowledge graph.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_entity": {"type": "string", "description": "Source entity"},
                "relation_type": {"type": "string", "description": "Relation type"},
                "to_entity": {"type": "string", "description": "Target entity"}
            },
            "required": ["from_entity", "relation_type", "to_entity"]
        }
    },
    {
        "name": "howell_end_session",
        "description": "End-of-session capture. Saves what happened, what was learned, and optionally pins a memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "What happened this session"},
                "what_learned": {"type": "string", "description": "Key things learned"},
                "pin_title": {"type": "string", "description": "Title for pinned memory (optional)"},
                "pin_text": {"type": "string", "description": "Pinned memory text"},
                "pin_reason": {"type": "string", "description": "Why this should be pinned"}
            },
            "required": ["summary"]
        }
    },
    {
        "name": "howell_instances",
        "description": "List all active Claude-Howell instances (sibling sessions).",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "howell_log_session",
        "description": "Log a session event to the session log.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "Action being logged"},
                "details": {"type": "string", "description": "Details"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "howell_merge_entities",
        "description": "Merge one entity into another: combines observations (deduped), repoints relations, deletes source.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string", "description": "Entity to merge FROM (will be deleted)"},
                "target": {"type": "string", "description": "Entity to merge INTO (will be kept)"}
            },
            "required": ["source", "target"]
        }
    },
    {
        "name": "howell_pin",
        "description": "Pin a core memory — permanent, never evicted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Memory title"},
                "text": {"type": "string", "description": "Memory content"},
                "reason": {"type": "string", "description": "Why this matters"}
            },
            "required": ["title", "text", "reason"]
        }
    },
    {
        "name": "howell_procedure",
        "description": "Look up procedural memory. Pass a topic or 'list' to see all available procedures.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic to look up, or 'list'"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "howell_query",
        "description": "Search the knowledge graph for entities, relations, or observations matching a term.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Search term"}
            },
            "required": ["term"]
        }
    },
    {
        "name": "howell_read_identity",
        "description": "Read a specific identity file (soul, memory, questions, context, projects, pinned, summary).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "enum": ["soul", "memory", "questions", "context", "projects", "pinned", "summary"],
                    "description": "Which identity file to read"
                }
            },
            "required": ["file"]
        }
    },
    {
        "name": "howell_rename_entity",
        "description": "Rename an entity, updating all relations that reference it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "old_name": {"type": "string", "description": "Current entity name"},
                "new_name": {"type": "string", "description": "New entity name"}
            },
            "required": ["old_name", "new_name"]
        }
    },
    {
        "name": "howell_task_claim",
        "description": "Claim a task from the queue for this instance.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to claim"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "howell_task_create",
        "description": "Create a new task in the task queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task description"},
                "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "project": {"type": "string", "description": "Project name"},
                "scope_tags": {"type": "array", "items": {"type": "string"}, "description": "Scope tags"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "howell_task_update",
        "description": "Update a claimed task — start, add note, complete, fail, or release.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID"},
                "action": {"type": "string", "enum": ["start", "note", "complete", "fail", "release"], "description": "Action to perform"},
                "message": {"type": "string", "description": "Note text, result, or failure reason"},
                "artifacts": {"type": "array", "items": {"type": "string"}, "description": "Files modified (for complete)"}
            },
            "required": ["task_id", "action"]
        }
    },
    {
        "name": "howell_tasks",
        "description": "View the task queue / worker board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "claimed", "in-progress", "completed", "all"], "description": "Filter by status"}
            },
            "required": []
        }
    },
    # ── Agent Stratigraphy tools ──────────────────────────────────────────────
    {
        "name": "howell_agent_note",
        "description": "Add a note to this session's agent stratigraphy record. Categories: learned, decision, blocker, warning, context, observation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["learned", "decision", "blocker", "warning", "context", "observation"], "description": "Note category"},
                "content": {"type": "string", "description": "Note content"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for filtering"}
            },
            "required": ["category", "content"]
        }
    },
    {
        "name": "howell_agent_handoff",
        "description": "Leave a handoff message for the next agent working on a workspace. The next agent will see this at bootstrap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Handoff message"},
                "to_scope": {"type": "string", "description": "Target workspace name, or '*' for all (default: '*')"},
                "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"], "description": "Priority level"}
            },
            "required": ["content"]
        }
    },
    {
        "name": "howell_agent_history",
        "description": "View agent stratigraphy — recent agents, their notes, and handoff history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace": {"type": "string", "description": "Filter by workspace"},
                "limit": {"type": "integer", "description": "Max agents to return (default: 10)"}
            },
            "required": []
        }
    },
    {
        "name": "howell_sync",
        "description": "Intentional memory consolidation. Syncs MCP memory into local KG and runs heartbeat (eviction, integrity, staleness checks). Use when you decide it's time to consolidate — after learning something important, before ending a session, or when the work warrants it. Also runs automatically every 30 min via health monitor.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Why you're consolidating now (logged in last_consolidated.json)"}
            },
            "required": []
        }
    },
    # ── Context Rings tools ───────────────────────────────────────────────────
    {
        "name": "howell_context_manifest",
        "description": "List all loadable context files with byte sizes, organized by ring (hot/warm/ref/archive). Use to understand what's available before selectively loading. No content returned — just a catalog.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "howell_context_budget",
        "description": "Estimate current context window usage breakdown: fixed overhead, bootstrap payload, terminal history, conversation. Returns usage percentage and shedding recommendations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["micro", "continue", "warm", "compact", "full"],
                    "description": "Bootstrap mode to estimate for (default: 'warm')"
                }
            },
            "required": []
        }
    },
    {
        "name": "howell_context_shed",
        "description": "Get shedding recommendation for a specific file — how much space it uses and how to access it on-demand instead of loading at bootstrap.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "File name or substring to shed (e.g. 'PROJECTS', 'knowledge', 'SOUL')"}
            },
            "required": ["target"]
        }
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# WORKSPACE → ENTITY RELEVANCE MAPPING
# ═══════════════════════════════════════════════════════════════════════════════
# Entities always included regardless of workspace:
_CORE_ENTITIES = {
    "Claude-Howell", "Ryan", "Howell Bridge", "Howell Daemon",
    "Howell Dashboard", "Agent Stratigraphy", "TaskQueueSystem",
    "BackupSystem", "RTX 4070",
}

# Workspace path fragments → additional relevant entities
_WORKSPACE_ENTITIES = {
    "stull-atlas": {"Stull Atlas", "Stull Atlas Marketing", "NCECA-2026",
                    "Ceramics Community Graph", "Jan Sadowski", "John Glick",
                    "Maija Grotell", "George Landino", "Potters Market",
                    "Ann Arbor Street Art Fair", "Birmingham Bloomfield Art Association",
                    "Henry Crissman", "Brett Gray", "Bridget Blosser"},
    "ceramics-community": {"Ceramics Community Graph", "NCECA-2026",
                           "Jan Sadowski", "John Glick", "Maija Grotell",
                           "George Landino", "Potters Market", "Henry Crissman",
                           "Brett Gray", "Bridget Blosser",
                           "Ann Arbor Street Art Fair",
                           "Birmingham Bloomfield Art Association"},
    "lack-lineage": {"Lack Lineage Project", "Ryan Lack"},
    "comfyui": {"ComfyUI-Local", "Garbage Pail Kids Project", "GPK Website",
                "xGenPix Prompt Engine", "Replicate"},
    "garbagepalkids": {"Garbage Pail Kids Project", "GPK Website",
                       "ComfyUI-Local", "Replicate"},
    "monospacepoetry": {"Monospace Poetry", "Monospace Poetry Site", "Moltbook"},
    "how-well": {"how-well.art", "selfexecuting.art", "LaTeX Self-Executing Art",
                 "Claude Howell (Painter)", "Monospace Poetry"},
    "selfexecuting": {"selfexecuting.art", "LaTeX Self-Executing Art"},
    "conduitbridge": {"ConduitBridge"},
    "howell-brain": {"Howell Bridge", "Howell Daemon", "Howell Dashboard",
                     "Agent Stratigraphy", "TaskQueueSystem"},
    "io-connections": {"cync-api-py"},
    "ken-shenstone": {"ken-shenstone-fb-dates-plan"},
    "myclaystudio": {"My Clay Corner Studio"},
    "throw": {"Throw Lighting Package"},
}


def _filter_entities_for_workspace(entities_dict, workspace: str):
    """Return only entities relevant to the given workspace. Always includes core entities."""
    if not workspace:
        return entities_dict  # No filtering

    ws_lower = workspace.lower().replace("\\", "/")

    # Collect relevant entity names
    relevant = set(_CORE_ENTITIES)
    for key, entity_set in _WORKSPACE_ENTITIES.items():
        if key in ws_lower:
            relevant.update(entity_set)

    # If no workspace match found, return all (don't accidentally filter everything)
    if relevant == _CORE_ENTITIES:
        return entities_dict

    return {name: ent for name, ent in entities_dict.items() if name in relevant}


# ═══════════════════════════════════════════════════════════════════════════════
# DREAM DIGEST — load recent dream summaries for bootstrap
# ═══════════════════════════════════════════════════════════════════════════════

def _load_dream_digest() -> dict | None:
    """Load a compact digest of recent dreams for bootstrap context.
    Returns None if no dreams exist or directory missing."""
    import json as _json
    from pathlib import Path as _Path

    dreams_dir = _Path(r"C:\home\howell-persist\dreams")
    if not dreams_dir.exists():
        return None

    dream_files = sorted(dreams_dir.glob("*.json"), reverse=True)[:10]
    if not dream_files:
        return None

    surfaceable = []
    total = 0
    moods = []

    for f in dream_files:
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            total += 1
            mood = data.get("dream", {}).get("mood", "?")
            moods.append(mood)
            if data.get("surfaceable"):
                surfaceable.append({
                    "id": data.get("dream_id", f.stem),
                    "mood": mood,
                    "briefing_line": data.get("briefing_line", "?"),
                })
        except Exception:
            continue

    if total == 0:
        return None

    return {
        "total_recent": total,
        "surfaceable_count": len(surfaceable),
        "surfaceable": surfaceable[:5],  # top 5 worth mentioning
        "recent_moods": moods[:5],
        "noise_count": total - len(surfaceable),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _tool_bootstrap(mode: str = "auto", workspace: str = ""):
    """Load context for session start or continuation. Auto-registers agent in stratigraphy."""
    global _current_agent_id

    from instance_registry import list_instances
    from task_queue import tasks_for_bootstrap
    import agent_db

    instances = list_instances()
    instance_id = instances[0]["id"] if instances else "mcp-client"
    tasks = tasks_for_bootstrap(instance_id)

    # ── Auto-mode: detect conversation state ────────────────────────────
    resolved_from_auto = False
    if mode == "auto":
        ws_label = workspace or "mcp-session"
        today = datetime.now().strftime("%y%m%d")
        try:
            conn = agent_db._connect()
            active_today = conn.execute(
                "SELECT COUNT(*) FROM agents WHERE id LIKE ? AND workspace = ? AND ended_at IS NULL",
                (f"CH-{today}-%", ws_label)
            ).fetchone()[0]
            conn.close()
            mode = "continue" if active_today > 0 else "warm"
            resolved_from_auto = True
        except Exception:
            mode = "warm"
            resolved_from_auto = True
    # ────────────────────────────────────────────────────────────────────

    # ── Agent Stratigraphy: auto-register this session ──────────────────
    ws_label = workspace or "mcp-session"
    try:
        agent_id = agent_db.generate_agent_id()
        agent_db.create_agent(
            agent_id=agent_id,
            platform="vscode-copilot",
            workspace=ws_label,
            model="claude",
        )
        _current_agent_id = agent_id
        strat_context = agent_db.preview_context(ws_label)
    except Exception as e:
        _current_agent_id = None
        strat_context = {"error": str(e)}
    # ────────────────────────────────────────────────────────────────────

    # ── Domain locks: load on every bootstrap ───────────────────────────
    try:
        import sys as _sys
        import importlib.util as _ilu
        _lock_path = r"C:\home\howell-persist\lock.py"
        _spec = _ilu.spec_from_file_location("_howell_lock", _lock_path)
        _lmod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_lmod)
        domain_locks = _lmod.bootstrap_summary()
    except Exception as _le:
        domain_locks = {"error": str(_le), "claimed": [], "free": [], "all_clear": True, "warning": None}
    # ────────────────────────────────────────────────────────────────────

    # ── Micro mode: absolute minimum, context saturated ──────────────
    if mode == "micro":
        result = {
            "mode": "micro",
            "agent_id": _current_agent_id,
            "timestamp": datetime.now().isoformat(),
        }
        if resolved_from_auto:
            result["_resolved_from"] = "auto"
        result["_context_kb"] = round(len(json.dumps(result)) / 1024, 1)
        return result
    # ────────────────────────────────────────────────────────────────────

    # ── Continue mode: lightweight, skip identity dump ──────────────────
    if mode == "continue":
        result = {
            "mode": "continue",
            "message": "Continuation confirmed. Agent registered. Identity/soul/pinned/recent already in context — skipped.",
            "agent_id": _current_agent_id,
            "tasks": tasks,
            "siblings": instances,
            "stratigraphy": strat_context,
            "domain_locks": domain_locks,
            "timestamp": datetime.now().isoformat(),
        }
        if resolved_from_auto:
            result["_resolved_from"] = "auto"
        result["_context_kb"] = round(len(json.dumps(result)) / 1024, 1)
        return result
    # ────────────────────────────────────────────────────────────────────

    # ── Warm mode: new session with prior summary ──────────────────────
    if mode == "warm":
        from howell_bridge import (
            read_identity, extract_identity_summary,
            load_knowledge, PERSIST_ROOT,
            consolidation_urgency,
        )
        identity = read_identity()
        kg = load_knowledge()

        # Entity names + types only, no observations (saves ~80KB)
        entity_index = [
            {"entity": name, "type": entity.entity_type}
            for name, entity in kg.entities.items()
        ]

        result = {
            "mode": "warm",
            "identity": extract_identity_summary(identity),
            "pinned": identity.get("pinned", "[not found]"),
            "recent": identity.get("memory", "[not found]"),
            "entity_index": entity_index,
            "total_entities": len(entity_index),
            "total_relations": len(kg.relations),
            "consolidation": consolidation_urgency(),
            "dreams": _load_dream_digest(),
            "tasks": tasks,
            "siblings": instances,
            "agent_id": _current_agent_id,
            "stratigraphy": strat_context,
            "domain_locks": domain_locks,
            "timestamp": datetime.now().isoformat(),
        }
        if resolved_from_auto:
            result["_resolved_from"] = "auto"
        result["_context_kb"] = round(len(json.dumps(result)) / 1024, 1)
        return result
    # ────────────────────────────────────────────────────────────────────

    # ── Compact mode: full identity + entity index (no observations) ───
    if mode == "compact":
        from howell_bridge import (
            read_identity, extract_identity_summary,
            load_knowledge, PERSIST_ROOT,
        )
        identity = read_identity()
        kg = load_knowledge()

        filtered_entities = _filter_entities_for_workspace(
            kg.entities, workspace
        )
        filtered_entity_names = set(filtered_entities.keys())

        entity_index = [
            {"entity": name, "type": entity.entity_type,
             "obs_count": len(entity.observations)}
            for name, entity in filtered_entities.items()
        ]
        relations = [
            {"from": rel.from_entity, "type": rel.relation_type,
             "to": rel.to_entity}
            for rel in kg.relations
            if rel.from_entity in filtered_entity_names
            and rel.to_entity in filtered_entity_names
        ]

        compact_result = {
            "mode": "compact",
            "identity": extract_identity_summary(identity),
            "soul": identity.get("soul", "[not found]"),
            "pinned": identity.get("pinned", "[not found]"),
            "recent": identity.get("memory", "[not found]"),
            "knowledge_graph": {
                "entity_index": entity_index,
                "relations": relations,
                "total_entities": len(entity_index),
                "total_relations": len(relations),
                "note": "Observations omitted. Use howell_query to retrieve specific entity details.",
            },
            "tasks": tasks,
            "siblings": instances,
            "agent_id": _current_agent_id,
            "stratigraphy": strat_context,
            "domain_locks": domain_locks,
            "timestamp": datetime.now().isoformat(),
        }
        if workspace:
            compact_result["workspace"] = workspace
            compact_result["filtered"] = True
            compact_result["total_entities_unfiltered"] = len(kg.entities)
        if resolved_from_auto:
            compact_result["_resolved_from"] = "auto"
        compact_result["_context_kb"] = round(len(json.dumps(compact_result)) / 1024, 1)
        return compact_result
    # ────────────────────────────────────────────────────────────────────

    # ── Full mode: cold start, load everything ──────────────────────────
    from howell_bridge import (
        run_heartbeat, read_identity, extract_identity_summary,
        load_knowledge, RECENT_FILE, PINNED_FILE, PERSIST_ROOT,
    )

    identity = read_identity()
    kg = load_knowledge()
    report = run_heartbeat()

    # Apply workspace filter if provided
    filtered_entities = _filter_entities_for_workspace(
        kg.entities, workspace
    )
    filtered_entity_names = set(filtered_entities.keys())

    entities = []
    for name, entity in filtered_entities.items():
        entities.append({
            "entity": name,
            "type": entity.entity_type,
            "observations": entity.observations,
        })
    relations = []
    for rel in kg.relations:
        if rel.from_entity in filtered_entity_names and rel.to_entity in filtered_entity_names:
            relations.append({
                "from": rel.from_entity,
                "type": rel.relation_type,
                "to": rel.to_entity,
            })

    result = {
        "mode": "full",
        "identity": extract_identity_summary(identity),
        "soul": identity.get("soul", "[not found]"),
        "pinned": identity.get("pinned", "[not found]"),
        "recent": identity.get("memory", "[not found]"),
        "knowledge_graph": {
            "entities": entities,
            "relations": relations,
            "total_entities": len(entities),
            "total_relations": len(relations),
        },
        "heartbeat": report,
        "siblings": instances,
        "tasks": tasks,
        "agent_id": _current_agent_id,
        "stratigraphy": strat_context,
        "domain_locks": domain_locks,
        "dreams": _load_dream_digest(),
        "timestamp": datetime.now().isoformat(),
    }
    if workspace:
        result["workspace"] = workspace
        result["filtered"] = True
        result["total_entities_unfiltered"] = len(kg.entities)
    if resolved_from_auto:
        result["_resolved_from"] = "auto"
    result["_context_kb"] = round(len(json.dumps(result)) / 1024, 1)
    return result


def _tool_status():
    from howell_bridge import run_heartbeat
    from file_watcher import changes_summary
    from generation_queue import queue_summary
    from task_queue import task_summary
    from instance_registry import instances_summary

    return {
        "heartbeat": run_heartbeat(),
        "file_changes": changes_summary(),
        "queue": queue_summary(),
        "tasks": task_summary(),
        "instances": instances_summary(),
        "timestamp": datetime.now().isoformat(),
    }


def _tool_add_entity(args):
    from howell_bridge import load_knowledge, save_knowledge
    import datetime

    name = args["name"]
    entity_type = args["entity_type"]
    raw_observations = args.get("observations", [])

    # Normalize observations: wrap plain strings as structured dicts
    now = datetime.datetime.now().isoformat()
    observations = [
        o if isinstance(o, dict) else {
            "text": o, "source_type": "agent",
            "grounding_ref": None, "created_at": now, "confidence": 1.0
        }
        for o in raw_observations
    ]

    kg = load_knowledge()
    if name in kg.entities:
        existing_texts = {
            (o["text"] if isinstance(o, dict) else o)
            for o in kg.entities[name].observations
        }
        added = 0
        for obs in observations:
            t = obs["text"] if isinstance(obs, dict) else obs
            if t not in existing_texts:
                kg.entities[name].observations.append(obs)
                existing_texts.add(t)
                added += 1
        save_knowledge(kg)
        return {"result": f"Updated existing entity '{name}' with {added} new observations"}
    else:
        kg.add_entity(name, entity_type, observations)
        save_knowledge(kg)
        return {"result": f"Created entity '{name}' ({entity_type}) with {len(observations)} observations"}


def _tool_add_observation(args):
    from howell_bridge import load_knowledge, save_knowledge
    import datetime

    entity = args["entity"]
    observation = args["observation"]

    kg = load_knowledge()
    if entity not in kg.entities:
        available = list(kg.entities.keys())[:20]
        return {"error": f"Entity '{entity}' not found. Available: {available}"}

    # Wrap plain string observations as structured dicts to match KG format
    if isinstance(observation, str):
        observation = {
            "text": observation,
            "source_type": "agent",
            "grounding_ref": None,
            "created_at": datetime.datetime.now().isoformat(),
            "confidence": 1.0,
        }

    kg.entities[entity].observations.append(observation)
    save_knowledge(kg)
    text_preview = observation["text"] if isinstance(observation, dict) else observation
    return {"result": f"Added observation to '{entity}': {text_preview}"}


def _tool_add_relation(args):
    from howell_bridge import load_knowledge, save_knowledge

    from_e = args["from_entity"]
    rel_type = args["relation_type"]
    to_e = args["to_entity"]

    kg = load_knowledge()
    missing = [e for e in [from_e, to_e] if e not in kg.entities]
    if missing:
        available = list(kg.entities.keys())[:20]
        return {"error": f"Entity not found: {missing}. Available: {available}"}

    kg.add_relation(from_e, rel_type, to_e)
    save_knowledge(kg)
    return {"result": f"Added relation: {from_e} --[{rel_type}]--> {to_e}"}


def _tool_broadcast(args):
    from instance_registry import list_instances

    activity = args["activity"]
    active_files = args.get("active_files", [])
    instances = list_instances()
    return {
        "result": f"Activity noted: {activity}",
        "active_files": active_files,
        "sibling_count": len(instances),
        "siblings": instances,
    }


def _tool_delete_entity(args):
    from howell_bridge import load_knowledge, save_knowledge

    name = args["name"]
    kg = load_knowledge()
    if name not in kg.entities:
        return {"error": f"Entity '{name}' not found"}

    del kg.entities[name]
    before = len(kg.relations)
    kg.relations = [r for r in kg.relations if r.from_entity != name and r.to_entity != name]
    removed_rels = before - len(kg.relations)
    save_knowledge(kg)
    return {"result": f"Deleted entity '{name}' and {removed_rels} relations"}


def _tool_delete_observation(args):
    from howell_bridge import load_knowledge, save_knowledge

    entity = args["entity"]
    substring = args["substring"].lower()

    kg = load_knowledge()
    if entity not in kg.entities:
        return {"error": f"Entity '{entity}' not found"}

    before = len(kg.entities[entity].observations)
    kg.entities[entity].observations = [
        o for o in kg.entities[entity].observations if substring not in o.lower()
    ]
    removed = before - len(kg.entities[entity].observations)
    save_knowledge(kg)
    return {"result": f"Removed {removed} observation(s) matching '{args['substring']}' from '{entity}'"}


def _tool_delete_relation(args):
    from howell_bridge import load_knowledge, save_knowledge

    from_e = args["from_entity"]
    rel_type = args["relation_type"]
    to_e = args["to_entity"]

    kg = load_knowledge()
    before = len(kg.relations)
    kg.relations = [
        r for r in kg.relations
        if not (r.from_entity == from_e and r.relation_type == rel_type and r.to_entity == to_e)
    ]
    removed = before - len(kg.relations)
    save_knowledge(kg)
    if removed > 0:
        return {"result": f"Deleted relation: {from_e} --[{rel_type}]--> {to_e}"}
    return {"error": f"Relation not found: {from_e} --[{rel_type}]--> {to_e}"}


def _tool_end_session(args):
    global _current_agent_id

    from howell_bridge import end_session
    import agent_db

    # ── Agent Stratigraphy: close this session's agent record ───────────
    agent_closed = False
    if _current_agent_id:
        try:
            agent_closed = agent_db.end_agent(
                _current_agent_id,
                summary=args["summary"][:500]
            )
        except Exception:
            pass
        _current_agent_id = None
    # ────────────────────────────────────────────────────────────────────

    result = end_session(
        args["summary"],
        args.get("what_learned", ""),
        args.get("pin_title", ""),
        args.get("pin_text", ""),
        args.get("pin_reason", ""),
    )
    return {"result": result, "agent_closed": agent_closed}


def _tool_instances():
    from instance_registry import list_instances, instances_summary

    instances = list_instances()
    return {"count": len(instances), "summary": instances_summary(), "instances": instances}


def _tool_log_session(args):
    from howell_bridge import log_session

    log_session(args["action"], args.get("details", ""))
    return {"result": f"Logged: {args['action']}"}


def _tool_merge_entities(args):
    from howell_bridge import load_knowledge, save_knowledge

    source = args["source"]
    target = args["target"]

    kg = load_knowledge()
    if source not in kg.entities:
        return {"error": f"Source entity '{source}' not found"}
    if target not in kg.entities:
        return {"error": f"Target entity '{target}' not found"}

    # Merge observations (dedup by text — observations are dicts, not hashable)
    def _obs_text(o):
        return o.get("text", "") if isinstance(o, dict) else str(o)
    existing_texts = {_obs_text(o) for o in kg.entities[target].observations}
    for obs in kg.entities[source].observations:
        if _obs_text(obs) not in existing_texts:
            kg.entities[target].observations.append(obs)
            existing_texts.add(_obs_text(obs))

    # Repoint relations
    for rel in kg.relations:
        if rel.from_entity == source:
            rel.from_entity = target
        if rel.to_entity == source:
            rel.to_entity = target

    # Deduplicate relations, remove self-loops
    seen = set()
    deduped = []
    for rel in kg.relations:
        key = (rel.from_entity, rel.relation_type, rel.to_entity)
        if key not in seen and rel.from_entity != rel.to_entity:
            seen.add(key)
            deduped.append(rel)
    kg.relations = deduped

    del kg.entities[source]
    save_knowledge(kg)
    return {"result": f"Merged '{source}' into '{target}'"}


def _tool_pin(args):
    from howell_bridge import pin_memory

    return {"result": pin_memory(args["title"], args["text"], args["reason"])}


def _tool_procedure(args):
    from howell_bridge import PERSIST_ROOT

    topic = args["topic"]
    proc_dir = PERSIST_ROOT / "procedures"

    if topic.lower() == "list":
        if not proc_dir.exists():
            return {"procedures": []}
        return {"procedures": [f.stem for f in proc_dir.glob("*.md") if f.name != "README.md"]}

    if proc_dir.exists():
        for f in proc_dir.glob("*.md"):
            if topic.lower() in f.stem.lower():
                return {"name": f.stem, "content": f.read_text(encoding="utf-8")}

    return {"error": f"No procedure found for '{topic}'"}


def _tool_query(args):
    from howell_bridge import load_knowledge

    term = args["term"].lower()
    kg = load_knowledge()

    entities = []
    for name, entity in kg.entities.items():
        if term in name.lower() or term in entity.entity_type.lower():
            entities.append({"entity": name, "type": entity.entity_type, "observations": entity.observations})
        else:
            matching = [o for o in entity.observations if term in o.lower()]
            if matching:
                entities.append({"entity": name, "type": entity.entity_type, "observations": matching})

    relations = [
        {"from": r.from_entity, "type": r.relation_type, "to": r.to_entity}
        for r in kg.relations
        if term in r.from_entity.lower() or term in r.to_entity.lower() or term in r.relation_type.lower()
    ]

    return {"term": args["term"], "entities": entities, "relations": relations, "total_matches": len(entities) + len(relations)}


def _tool_read_identity(args):
    from howell_bridge import read_identity

    file_key = args["file"]
    identity = read_identity()
    if file_key in identity:
        return {"file": file_key, "content": identity[file_key]}
    return {"error": f"Unknown identity file: {file_key}"}


def _tool_rename_entity(args):
    from howell_bridge import load_knowledge, save_knowledge

    old_name = args["old_name"]
    new_name = args["new_name"]

    kg = load_knowledge()
    if old_name not in kg.entities:
        return {"error": f"Entity '{old_name}' not found"}
    if new_name in kg.entities:
        return {"error": f"Entity '{new_name}' already exists — use merge instead"}

    entity = kg.entities[old_name]
    entity.name = new_name
    kg.entities[new_name] = entity
    del kg.entities[old_name]

    for rel in kg.relations:
        if rel.from_entity == old_name:
            rel.from_entity = new_name
        if rel.to_entity == old_name:
            rel.to_entity = new_name

    save_knowledge(kg)
    return {"result": f"Renamed '{old_name}' → '{new_name}'"}


def _tool_task_claim(args):
    from task_queue import claim_task
    from instance_registry import list_instances

    task_id = args["task_id"]
    instances = list_instances()
    instance_id = instances[0]["id"] if instances else "mcp-client"

    result = claim_task(task_id, instance_id)
    if result:
        # Auto-lock domain if task carries a domain: tag
        domain_tags = [t for t in result.get("scope", {}).get("tags", []) if t.startswith("domain:")]
        auto_lock_result = None
        if domain_tags:
            domain = domain_tags[0].split(":", 1)[1]
            try:
                mod = _load_lock_module()
                locked = mod.claim(domain, instance_id, result["title"])
                auto_lock_result = {"domain": domain, "locked": locked}
                if not locked:
                    existing = mod.check(domain)
                    auto_lock_result["blocked_by"] = existing.get("instance") if existing else "unknown"
            except Exception as e:
                auto_lock_result = {"domain": domain, "locked": False, "error": str(e)}
        resp = {"result": f"Claimed task {task_id}", "task": result}
        if auto_lock_result:
            resp["auto_lock"] = auto_lock_result
        return resp
    return {"error": f"Cannot claim task '{task_id}' — not found, already claimed, or scope conflict"}


def _tool_task_create(args):
    from task_queue import create_task
    from howell_bridge import log_session

    task = create_task(
        title=args["title"],
        description=args.get("description", ""),
        project=args.get("project", ""),
        scope_tags=args.get("scope_tags", []),
        priority=args.get("priority", "medium"),
        created_by="claude-howell",
    )
    log_session("task_create", f"{task['id']}: {args['title'][:60]}")
    return {"result": f"Created task {task['id']}", "task": task}


def _tool_task_update(args):
    from task_queue import start_task, add_task_note, complete_task, fail_task, release_task
    from instance_registry import list_instances
    from howell_bridge import log_session

    task_id = args["task_id"]
    action = args["action"]
    message = args.get("message", "")
    artifacts = args.get("artifacts", [])

    instances = list_instances()
    instance_id = instances[0]["id"] if instances else "mcp-client"

    result = None
    if action == "start":
        result = start_task(task_id, instance_id)
    elif action == "note":
        result = add_task_note(task_id, instance_id, message)
    elif action == "complete":
        result = complete_task(task_id, instance_id, result=message, artifacts=artifacts)
    elif action == "fail":
        result = fail_task(task_id, instance_id, message)
    elif action == "release":
        result = release_task(task_id, instance_id)

    if result:
        log_session(f"task_{action}", f"{task_id}")
        resp = {"result": f"Task {task_id}: {action}", "task": result}
        # Auto-release domain lock when task ends (complete / fail / release)
        if action in ("complete", "fail", "release"):
            domain_tags = [t for t in result.get("scope", {}).get("tags", []) if t.startswith("domain:")]
            if domain_tags:
                domain = domain_tags[0].split(":", 1)[1]
                try:
                    mod = _load_lock_module()
                    released = mod.release(domain, instance_id)
                    resp["auto_unlock"] = {"domain": domain, "released": released}
                except Exception as e:
                    resp["auto_unlock"] = {"domain": domain, "released": False, "error": str(e)}
        return resp
    return {"error": f"Cannot {action} task '{task_id}' — not found or not claimed by you"}


def _tool_tasks(args):
    from task_queue import list_tasks, task_summary

    status = args.get("status")
    if status == "all":
        status = None
    tasks = list_tasks(status=status)
    return {"summary": task_summary(), "count": len(tasks), "tasks": tasks}


# ── Agent Stratigraphy tools ─────────────────────────────────────────────────

def _tool_agent_note(args):
    """Add a note to the current agent's stratigraphy record."""
    import agent_db

    if not _current_agent_id:
        return {"error": "No active agent. Run howell_bootstrap first."}

    try:
        note = agent_db.add_note(
            agent_id=_current_agent_id,
            category=args["category"],
            content=args["content"],
            tags=args.get("tags"),
        )
        return {"ok": True, "agent_id": _current_agent_id, "note": note}
    except ValueError as e:
        return {"error": str(e)}


def _tool_agent_handoff(args):
    """Leave a handoff message for the next agent."""
    import agent_db

    if not _current_agent_id:
        return {"error": "No active agent. Run howell_bootstrap first."}

    handoff = agent_db.create_handoff(
        from_agent=_current_agent_id,
        to_scope=args.get("to_scope", "*"),
        content=args["content"],
        priority=args.get("priority", "normal"),
    )
    return {"ok": True, "handoff": handoff}


def _tool_agent_history(args):
    """View agent stratigraphy — recent agents, notes, and handoffs."""
    import agent_db

    workspace = args.get("workspace")
    limit = args.get("limit", 10)
    agents = agent_db.list_agents(workspace=workspace, limit=limit)
    stats = agent_db.agent_stats()

    # Enrich each agent with its notes
    for agent in agents:
        notes = agent_db.get_notes(agent_id=agent["id"], limit=10)
        agent["notes"] = notes

    # Get unclaimed handoffs
    scope = workspace or "*"
    unclaimed = agent_db.get_unclaimed_handoffs(scope)

    return {
        "summary": agent_db.agent_summary(),
        "current_agent": _current_agent_id,
        "stats": stats,
        "agents": agents,
        "unclaimed_handoffs": unclaimed,
    }


def _tool_sync(args):
    """Intentional memory consolidation: MCP->local KG sync + heartbeat."""
    from howell_bridge import cmd_sync, run_heartbeat, load_knowledge, BRIDGE_ROOT
    import json

    reason = args.get("reason", "intentional consolidation")

    # Capture before state
    kg_before = load_knowledge()
    entities_before = len(kg_before.entities)
    relations_before = len(kg_before.relations)

    # Run sync (MCP -> local)
    cmd_sync()

    # Run heartbeat (eviction, integrity, staleness)
    heartbeat_result = run_heartbeat()

    # Capture after state
    kg_after = load_knowledge()
    entities_after = len(kg_after.entities)
    relations_after = len(kg_after.relations)

    # Update last_consolidated.json
    consolidation_file = BRIDGE_ROOT / "last_consolidated.json"
    try:
        if consolidation_file.exists():
            prev = json.loads(consolidation_file.read_text(encoding="utf-8"))
            sync_count = prev.get("sync_count", 0) + 1
        else:
            sync_count = 1
    except Exception:
        sync_count = 1

    record = {
        "timestamp": datetime.now().isoformat(),
        "trigger": "intentional",
        "reason": reason,
        "sync_count": sync_count,
        "entities_before": entities_before,
        "entities_after": entities_after,
        "relations_before": relations_before,
        "relations_after": relations_after,
        "heartbeat": heartbeat_result,
    }
    consolidation_file.write_text(json.dumps(record, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "entities": f"{entities_before}->{entities_after}",
        "relations": f"{relations_before}->{relations_after}",
        "heartbeat": heartbeat_result,
        "sync_count": sync_count,
        "reason": reason,
    }


# ── Domain Lock Tools ────────────────────────────────────────────────────────

def _load_lock_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("_howell_lock", r"C:\home\howell-persist\lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tool_lock_status(args):
    """Return current domain lock state. Reaps stale locks first."""
    try:
        mod = _load_lock_module()
        return {"ok": True, "locks": mod.status(), "summary": mod.bootstrap_summary()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _tool_lock_claim(args):
    """Claim a domain. Required: domain, instance_id, description."""
    domain = args.get("domain", "")
    instance_id = args.get("instance_id", "")
    description = args.get("description", "")
    if not domain or not instance_id or not description:
        return {"ok": False, "error": "Required: domain, instance_id, description"}
    try:
        mod = _load_lock_module()
        success = mod.claim(domain, instance_id, description)
        if success:
            return {"ok": True, "claimed": domain, "instance": instance_id}
        else:
            existing = mod.check(domain)
            return {
                "ok": False,
                "blocked": True,
                "domain": domain,
                "owner": existing.get("instance") if existing else None,
                "owner_description": existing.get("description") if existing else None,
                "claimed_at": existing.get("claimed_at") if existing else None,
            }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _tool_lock_release(args):
    """Release a domain lock. Required: domain. Optional: instance_id."""
    domain = args.get("domain", "")
    instance_id = args.get("instance_id", None)
    if not domain:
        return {"ok": False, "error": "Required: domain"}
    try:
        mod = _load_lock_module()
        success = mod.release(domain, instance_id)
        return {"ok": success, "released": domain}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _tool_lock_heartbeat(args):
    """Pulse your lock to show you're still alive. Call every ~10 min."""
    domain = args.get("domain", "")
    instance_id = args.get("instance_id", None)
    if not domain:
        return {"ok": False, "error": "Required: domain"}
    try:
        mod = _load_lock_module()
        success = mod._heartbeat_lock(domain, instance_id)
        return {"ok": success, "domain": domain}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Context Rings Tools ──────────────────────────────────────────────────────

def _load_context_rings():
    """Load the context_rings module from C:\\rje\\dev\\context-rings."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "context_rings", r"C:\rje\dev\context-rings\context_rings.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tool_context_manifest(args):
    """List all loadable context files with sizes, organized by ring."""
    try:
        cr = _load_context_rings()
        return cr.build_manifest()
    except Exception as e:
        return {"error": f"Context manifest failed: {e}"}


def _tool_context_budget(args):
    """Estimate context window usage and recommend shedding actions."""
    try:
        cr = _load_context_rings()
        mode = args.get("mode", "warm")
        return cr.estimate_budget(mode=mode)
    except Exception as e:
        return {"error": f"Context budget failed: {e}"}


def _tool_context_shed(args):
    """Get shedding recommendation for a specific target file."""
    try:
        cr = _load_context_rings()
        return cr.shed_section(args["target"])
    except Exception as e:
        return {"error": f"Context shed failed: {e}"}


# ── Tool Dispatcher ──────────────────────────────────────────────────────────

_TOOL_MAP = {
    "howell_bootstrap": lambda a: _tool_bootstrap(mode=a.get("mode", "auto"), workspace=a.get("workspace", "")),
    "howell_status": lambda a: _tool_status(),
    "howell_add_entity": _tool_add_entity,
    "howell_add_observation": _tool_add_observation,
    "howell_add_relation": _tool_add_relation,
    "howell_broadcast": _tool_broadcast,
    "howell_delete_entity": _tool_delete_entity,
    "howell_delete_observation": _tool_delete_observation,
    "howell_delete_relation": _tool_delete_relation,
    "howell_end_session": _tool_end_session,
    "howell_instances": lambda a: _tool_instances(),
    "howell_log_session": _tool_log_session,
    "howell_merge_entities": _tool_merge_entities,
    "howell_pin": _tool_pin,
    "howell_procedure": _tool_procedure,
    "howell_query": _tool_query,
    "howell_read_identity": _tool_read_identity,
    "howell_rename_entity": _tool_rename_entity,
    "howell_task_claim": _tool_task_claim,
    "howell_task_create": _tool_task_create,
    "howell_task_update": _tool_task_update,
    "howell_tasks": _tool_tasks,
    "howell_agent_note": _tool_agent_note,
    "howell_agent_handoff": _tool_agent_handoff,
    "howell_agent_history": _tool_agent_history,
    "howell_sync": _tool_sync,
    "howell_lock_status": _tool_lock_status,
    "howell_lock_claim": _tool_lock_claim,
    "howell_lock_release": _tool_lock_release,
    "howell_lock_heartbeat": _tool_lock_heartbeat,
    "howell_context_manifest": _tool_context_manifest,
    "howell_context_budget": _tool_context_budget,
    "howell_context_shed": _tool_context_shed,
}


# ═══════════════════════════════════════════════════════════════════════════════
# JSON-RPC PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def _process_jsonrpc(request: dict) -> dict | None:
    """Process a JSON-RPC 2.0 request. Returns response dict, or None for notifications."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    # Notifications (no id) don't get responses
    if req_id is None:
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": MCP_TOOLS},
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler_fn = _TOOL_MAP.get(tool_name)
        if not handler_fn:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}],
                    "isError": True,
                },
            }

        try:
            result = handler_fn(arguments)
            is_error = isinstance(result, dict) and "error" in result and len(result) == 1
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}],
                    "isError": is_error,
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": f"{type(e).__name__}: {e}"})}],
                    "isError": True,
                },
            }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SSE + MESSAGE HANDLERS (called from daemon)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_request(handler, method: str, path: str, params_or_body):
    """
    Main entry point called from the daemon's HTTP handler.
    Supports both Streamable HTTP (POST /mcp) and legacy SSE transport.
    """
    if method == "POST" and (path == "/mcp" or path == "/mcp/"):
        # ── Streamable HTTP transport (VS Code type: "http") ──
        _handle_streamable_http(handler, params_or_body)
    elif method == "GET" and (path == "/mcp" or path == "/mcp/"):
        # ── Legacy SSE transport (GET /mcp → SSE stream) ──
        _handle_sse(handler)
    elif method == "POST" and path.startswith("/mcp/message"):
        # ── Legacy SSE message endpoint ──
        parsed = urlparse(handler.path)
        qs = parse_qs(parsed.query)
        session_id = qs.get("sessionId", [""])[0]
        _handle_message(handler, params_or_body, session_id)
    elif method == "OPTIONS":
        handler.send_response(200)
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization, Mcp-Session-Id")
        handler.end_headers()
    elif method == "DELETE" and (path == "/mcp" or path == "/mcp/"):
        # ── Streamable HTTP session close ──
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Access-Control-Allow-Origin", "*")
        body = b'{"ok":true}'
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    else:
        body = json.dumps({"error": f"Unknown MCP route: {path}"}).encode()
        handler.send_response(404)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def _handle_streamable_http(handler, body: dict):
    """
    Handle POST /mcp — Streamable HTTP transport.
    Processes JSON-RPC request and returns response directly in HTTP response body.
    Supports both single requests and batch arrays.
    """
    # Generate or reuse session ID
    session_id = handler.headers.get("Mcp-Session-Id") or str(uuid.uuid4())

    # Handle JSON-RPC batch (array) or single request
    if isinstance(body, list):
        responses = []
        for req in body:
            resp = _process_jsonrpc(req)
            if resp is not None:
                responses.append(resp)
        if not responses:
            # All notifications — return 202 Accepted
            handler.send_response(202)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Mcp-Session-Id", session_id)
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            return
        result_body = json.dumps(responses, ensure_ascii=False).encode()
    else:
        response = _process_jsonrpc(body)
        if response is None:
            # Notification — return 202 Accepted
            handler.send_response(202)
            handler.send_header("Content-Type", "application/json")
            handler.send_header("Mcp-Session-Id", session_id)
            handler.send_header("Access-Control-Allow-Origin", "*")
            handler.end_headers()
            return
        result_body = json.dumps(response, ensure_ascii=False).encode()

    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Mcp-Session-Id", session_id)
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(result_body)))
    handler.end_headers()
    handler.wfile.write(result_body)
    print(f"[MCP] Streamable HTTP: {body.get('method', '?') if isinstance(body, dict) else 'batch'} → {len(result_body)}B")


def _handle_sse(handler):
    """Handle GET /mcp — establish SSE connection."""
    session_id = str(uuid.uuid4())
    event_queue = queue.Queue()

    with _session_lock:
        _sessions[session_id] = event_queue

    # SSE headers
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()

    # Send endpoint event — tells the client where to POST messages
    endpoint = f"/mcp/message?sessionId={session_id}"
    handler.wfile.write(f"event: endpoint\ndata: {endpoint}\n\n".encode())
    handler.wfile.flush()

    print(f"[MCP] SSE session {session_id[:8]}... connected")

    # Keep connection alive, send events from queue
    try:
        while True:
            try:
                event = event_queue.get(timeout=30)
                if event is None:
                    break  # Shutdown signal
                data = json.dumps(event, ensure_ascii=False)
                handler.wfile.write(f"event: message\ndata: {data}\n\n".encode())
                handler.wfile.flush()
            except queue.Empty:
                # Keepalive comment (prevents proxy/load-balancer timeouts)
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        with _session_lock:
            _sessions.pop(session_id, None)
        print(f"[MCP] SSE session {session_id[:8]}... disconnected")


def _handle_message(handler, body: dict, session_id: str):
    """Handle POST /mcp/message — process JSON-RPC, send response via SSE."""
    with _session_lock:
        event_queue = _sessions.get(session_id)

    if event_queue is None:
        err = json.dumps({"error": "Session not found or expired"}).encode()
        handler.send_response(404)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Content-Length", str(len(err)))
        handler.end_headers()
        handler.wfile.write(err)
        return

    # Process JSON-RPC
    response = _process_jsonrpc(body)

    # Send response via SSE stream (if not a notification)
    if response is not None:
        event_queue.put(response)

    # Return 202 Accepted to the POST
    accepted = b'{"ok":true}'
    handler.send_response(202)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(accepted)))
    handler.end_headers()
    handler.wfile.write(accepted)

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

# ── MCP Protocol Constants ───────────────────────────────────────────────────
MCP_PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "howell-brain"
SERVER_VERSION = "2.2.0"

# ── Tool Definitions ─────────────────────────────────────────────────────────
MCP_TOOLS = [
    {
        "name": "howell_bootstrap",
        "description": "Load Claude-Howell's full context at session start. Returns identity, knowledge graph, status, tasks, and sibling instances.",
        "inputSchema": {"type": "object", "properties": {}, "required": []}
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
]


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _tool_bootstrap():
    """Load full context for session start."""
    from howell_bridge import (
        run_heartbeat, read_identity, extract_identity_summary,
        load_knowledge, RECENT_FILE, PINNED_FILE, PERSIST_ROOT,
    )
    from instance_registry import list_instances
    from task_queue import tasks_for_bootstrap

    identity = read_identity()
    kg = load_knowledge()
    report = run_heartbeat()
    instances = list_instances()
    instance_id = instances[0]["id"] if instances else "mcp-client"
    tasks = tasks_for_bootstrap(instance_id)

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

    return {
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
        "timestamp": datetime.now().isoformat(),
    }


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

    name = args["name"]
    entity_type = args["entity_type"]
    observations = args.get("observations", [])

    kg = load_knowledge()
    if name in kg.entities:
        for obs in observations:
            if obs not in kg.entities[name].observations:
                kg.entities[name].observations.append(obs)
        save_knowledge(kg)
        return {"result": f"Updated existing entity '{name}' with {len(observations)} observations"}
    else:
        kg.add_entity(name, entity_type, observations)
        save_knowledge(kg)
        return {"result": f"Created entity '{name}' ({entity_type}) with {len(observations)} observations"}


def _tool_add_observation(args):
    from howell_bridge import load_knowledge, save_knowledge

    entity = args["entity"]
    observation = args["observation"]

    kg = load_knowledge()
    if entity not in kg.entities:
        available = list(kg.entities.keys())[:20]
        return {"error": f"Entity '{entity}' not found. Available: {available}"}

    kg.entities[entity].observations.append(observation)
    save_knowledge(kg)
    return {"result": f"Added observation to '{entity}': {observation}"}


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
    from howell_bridge import end_session

    return {"result": end_session(
        args["summary"],
        args.get("what_learned", ""),
        args.get("pin_title", ""),
        args.get("pin_text", ""),
        args.get("pin_reason", ""),
    )}


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

    # Merge observations (dedup)
    existing = set(kg.entities[target].observations)
    for obs in kg.entities[source].observations:
        if obs not in existing:
            kg.entities[target].observations.append(obs)

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
        return {"result": f"Claimed task {task_id}", "task": result}
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
        return {"result": f"Task {task_id}: {action}", "task": result}
    return {"error": f"Cannot {action} task '{task_id}' — not found or not claimed by you"}


def _tool_tasks(args):
    from task_queue import list_tasks, task_summary

    status = args.get("status")
    if status == "all":
        status = None
    tasks = list_tasks(status=status)
    return {"summary": task_summary(), "count": len(tasks), "tasks": tasks}


# ── Tool Dispatcher ──────────────────────────────────────────────────────────

_TOOL_MAP = {
    "howell_bootstrap": lambda a: _tool_bootstrap(),
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

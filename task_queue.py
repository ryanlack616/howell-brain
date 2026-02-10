#!/usr/bin/env python3
"""
TASK QUEUE — Multi-Instance Work Coordination
==============================================
Ryan drops tasks. Worker brains claim and execute them.
Scope-based isolation prevents conflicts. Dependencies
control ordering. Everything persists to disk.

Task Lifecycle:
    pending → claimed → in-progress → completed | failed
    
Coordination Rules:
    1. Tasks declare a SCOPE (files, directories, tags)
    2. A worker can only claim a task whose scope doesn't
       overlap with any in-progress task
    3. Tasks with unmet dependencies stay blocked
    4. Workers auto-discover unclaimed tasks at bootstrap

Created: Feb 7, 2026
Author: Claude-Howell (with Ryan)
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
import os
from threading import Lock
from typing import Any

# ============================================================================
# STORAGE
# ============================================================================

PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\Users\PC\Desktop\claude-persist"))
TASKS_DIR = PERSIST_ROOT / "tasks"
TASKS_FILE = TASKS_DIR / "tasks.json"
ARCHIVE_DIR = TASKS_DIR / "archive"

_lock = Lock()


def ensure_tasks_dir():
    """Create task directories if needed."""
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


def _load_tasks() -> list[dict[str, Any]]:
    """Load all tasks from disk. Handles corruption gracefully."""
    ensure_tasks_dir()
    if not TASKS_FILE.exists():
        return []
    try:
        data = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, Exception) as e:
        # Try backup before giving up
        backup = TASKS_FILE.with_suffix(".bak")
        if backup.exists():
            try:
                data = json.loads(backup.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    print(f"[WARN] tasks.json corrupt, restored from .bak: {e}")
                    return data
            except Exception:
                pass
        print(f"[ERROR] tasks.json corrupt and no valid backup: {e}")
        # Rename corrupt file instead of silently losing it
        import time as _time
        try:
            corrupt_path = TASKS_FILE.with_suffix(f".corrupt.{int(_time.time())}")
            TASKS_FILE.rename(corrupt_path)
            print(f"[ERROR] Corrupt file saved as {corrupt_path.name}")
        except Exception:
            pass
        return []


def _save_tasks(tasks: list[dict[str, Any]]):
    """Save tasks to disk atomically."""
    ensure_tasks_dir()
    # Backup current file first
    if TASKS_FILE.exists():
        backup = TASKS_FILE.with_suffix(".bak")
        try:
            backup.write_text(TASKS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            pass
    # Atomic write: temp file then rename
    tmp_path = TASKS_FILE.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(tasks, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    tmp_path.replace(TASKS_FILE)


# ============================================================================
# TASK CREATION
# ============================================================================

def create_task(
    title: str,
    description: str = "",
    project: str = "",
    scope_files: list[str] | None = None,
    scope_dirs: list[str] | None = None,
    scope_tags: list[str] | None = None,
    priority: str = "medium",
    dependencies: list[str] | None = None,
    created_by: str = "ryan",
) -> dict[str, Any]:
    """Create a new task. Returns the task record."""
    task_id = datetime.now().strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6]

    task = {
        "id": task_id,
        "title": title,
        "description": description,
        "project": project,
        "scope": {
            "files": scope_files or [],
            "directories": scope_dirs or [],
            "tags": scope_tags or [],
        },
        "priority": priority,  # low, medium, high, critical
        "status": "pending",
        "dependencies": dependencies or [],
        "created_by": created_by,
        "created_at": datetime.now().isoformat(),
        "claimed_by": None,       # instance_id
        "claimed_at": None,
        "started_at": None,
        "completed_at": None,
        "result": None,           # summary of what was done
        "artifacts": [],          # files modified, PRs created, etc.
        "notes": [],              # progress notes from the worker
    }

    with _lock:
        tasks = _load_tasks()
        tasks.append(task)
        _save_tasks(tasks)

    return task


# ============================================================================
# TASK CLAIMING & EXECUTION
# ============================================================================

def _scopes_overlap(scope_a: dict[str, Any], scope_b: dict[str, Any]) -> list[str]:
    """Check if two scopes overlap. Returns list of conflicts."""
    conflicts = []

    # File overlap
    files_a = set(scope_a.get("files", []))
    files_b = set(scope_b.get("files", []))
    overlap = files_a & files_b
    conflicts.extend(f"file:{f}" for f in overlap)

    # Directory overlap — check if any dir in A contains/is contained by dir in B
    dirs_a = scope_a.get("directories", [])
    dirs_b = scope_b.get("directories", [])
    for da in dirs_a:
        da_norm = da.replace("\\", "/").rstrip("/") + "/"
        for db in dirs_b:
            db_norm = db.replace("\\", "/").rstrip("/") + "/"
            if da_norm.startswith(db_norm) or db_norm.startswith(da_norm):
                conflicts.append(f"dir:{da} ↔ {db}")

    # Tag overlap
    tags_a = set(scope_a.get("tags", []))
    tags_b = set(scope_b.get("tags", []))
    tag_overlap = tags_a & tags_b
    conflicts.extend(f"tag:{t}" for t in tag_overlap)

    return conflicts


def get_available_tasks(instance_id: str | None = None) -> list[dict[str, Any]]:
    """Get tasks that can be claimed right now.
    Filters out:
    - Tasks already claimed/in-progress/completed/failed
    - Tasks whose dependencies aren't met
    - Tasks whose scope conflicts with in-progress work
    """
    with _lock:
        tasks = _load_tasks()

    completed_ids = {t["id"] for t in tasks if t["status"] == "completed"}
    in_progress = [t for t in tasks if t["status"] in ("claimed", "in-progress")]
    in_progress_scopes = [t["scope"] for t in in_progress]

    available = []
    for task in tasks:
        if task["status"] != "pending":
            continue

        # Check dependencies
        deps = task.get("dependencies", [])
        if deps and not all(d in completed_ids for d in deps):
            continue

        # Check scope conflicts with in-progress tasks
        has_conflict = False
        for ip_scope in in_progress_scopes:
            if _scopes_overlap(task["scope"], ip_scope):
                has_conflict = True
                break
        if has_conflict:
            continue

        available.append(task)

    # Sort by priority (critical > high > medium > low)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    available.sort(key=lambda t: priority_order.get(t["priority"], 2))

    return available


def claim_task(task_id: str, instance_id: str) -> dict[str, Any] | None:
    """Claim a task for a specific instance. Returns the updated task or None."""
    with _lock:
        tasks = _load_tasks()

        # Find the task
        task = None
        for t in tasks:
            if t["id"] == task_id:
                task = t
                break

        if not task:
            return None

        if task["status"] != "pending":
            return None  # Already claimed or finished

        # Verify no scope conflict with in-progress tasks
        in_progress = [t for t in tasks if t["status"] in ("claimed", "in-progress") and t["id"] != task_id]
        for ip in in_progress:
            conflicts = _scopes_overlap(task["scope"], ip["scope"])
            if conflicts:
                return None  # Scope conflict — can't claim

        # Claim it
        task["status"] = "claimed"
        task["claimed_by"] = instance_id
        task["claimed_at"] = datetime.now().isoformat()

        _save_tasks(tasks)
        return task


def start_task(task_id: str, instance_id: str) -> dict[str, Any] | None:
    """Mark a claimed task as in-progress. Only the claimer can do this."""
    with _lock:
        tasks = _load_tasks()

        for t in tasks:
            if t["id"] == task_id:
                if t["status"] != "claimed" or t["claimed_by"] != instance_id:
                    return None
                t["status"] = "in-progress"
                t["started_at"] = datetime.now().isoformat()
                _save_tasks(tasks)
                return t

        return None


def add_task_note(task_id: str, instance_id: str, note: str) -> dict[str, Any] | None:
    """Add a progress note to a task. Only the claimer can add notes."""
    with _lock:
        tasks = _load_tasks()

        for t in tasks:
            if t["id"] == task_id:
                if t["claimed_by"] != instance_id:
                    return None
                t["notes"].append({
                    "timestamp": datetime.now().isoformat(),
                    "note": note,
                })
                _save_tasks(tasks)
                return t

        return None


def complete_task(
    task_id: str,
    instance_id: str,
    result: str = "",
    artifacts: list[str] | None = None,
) -> dict[str, Any] | None:
    """Mark a task as completed. Only the claimer can complete it."""
    with _lock:
        tasks = _load_tasks()

        for t in tasks:
            if t["id"] == task_id:
                if t["claimed_by"] != instance_id:
                    return None
                t["status"] = "completed"
                t["completed_at"] = datetime.now().isoformat()
                t["result"] = result
                if artifacts:
                    t["artifacts"].extend(artifacts)
                _save_tasks(tasks)
                return t

        return None


def fail_task(task_id: str, instance_id: str, reason: str = "") -> dict[str, Any] | None:
    """Mark a task as failed. It returns to pending (unclaimed) so another 
    worker can try."""
    with _lock:
        tasks = _load_tasks()

        for t in tasks:
            if t["id"] == task_id:
                if t["claimed_by"] != instance_id:
                    return None
                t["notes"].append({
                    "timestamp": datetime.now().isoformat(),
                    "note": f"FAILED by {instance_id}: {reason}",
                })
                # Reset to pending so another worker can try
                t["status"] = "pending"
                t["claimed_by"] = None
                t["claimed_at"] = None
                t["started_at"] = None
                _save_tasks(tasks)
                return t

        return None


def release_task(task_id: str, instance_id: str) -> dict[str, Any] | None:
    """Release a claimed task back to pending (e.g. session ending before completion)."""
    with _lock:
        tasks = _load_tasks()

        for t in tasks:
            if t["id"] == task_id:
                if t["claimed_by"] != instance_id:
                    return None
                if t["status"] not in ("claimed", "in-progress"):
                    return None
                t["notes"].append({
                    "timestamp": datetime.now().isoformat(),
                    "note": f"Released by {instance_id} (session ending)",
                })
                t["status"] = "pending"
                t["claimed_by"] = None
                t["claimed_at"] = None
                t["started_at"] = None
                _save_tasks(tasks)
                return t

        return None


# ============================================================================
# TASK MANAGEMENT
# ============================================================================

def update_task(task_id: str, **kwargs: Any) -> dict[str, Any] | None:
    """Update task fields (title, description, priority, scope, etc.).
    Only works on pending tasks (not yet claimed)."""
    with _lock:
        tasks = _load_tasks()

        for t in tasks:
            if t["id"] == task_id:
                if t["status"] != "pending":
                    return None  # Can't edit claimed/in-progress tasks
                for key, value in kwargs.items():
                    if key in ("title", "description", "project", "priority", "scope", "dependencies"):
                        t[key] = value
                _save_tasks(tasks)
                return t

        return None


def delete_task(task_id: str) -> bool:
    """Delete a task. Works on pending, completed, and failed tasks (not active ones)."""
    deletable = {"pending", "completed", "failed"}
    with _lock:
        tasks = _load_tasks()
        original_len = len(tasks)
        tasks = [t for t in tasks if not (t["id"] == task_id and t["status"] in deletable)]
        if len(tasks) < original_len:
            _save_tasks(tasks)
            return True
        return False


def archive_completed(days_old: int = 7) -> int:
    """Move completed tasks older than N days to archive."""
    cutoff = time.time() - (days_old * 86400)
    archived = 0

    with _lock:
        tasks = _load_tasks()
        keep = []
        for t in tasks:
            if t["status"] in ("completed", "failed") and t.get("completed_at"):
                try:
                    completed_ts = datetime.fromisoformat(t["completed_at"]).timestamp()
                    if completed_ts < cutoff:
                        # Archive it
                        archive_file = ARCHIVE_DIR / f"{t['id']}.json"
                        archive_file.write_text(
                            json.dumps(t, indent=2), encoding="utf-8"
                        )
                        archived += 1
                        continue
                except Exception:
                    pass
            keep.append(t)

        _save_tasks(keep)

    return archived


# ============================================================================
# QUERIES
# ============================================================================

def get_task(task_id: str) -> dict[str, Any] | None:
    """Get a specific task."""
    with _lock:
        tasks = _load_tasks()
        for t in tasks:
            if t["id"] == task_id:
                return dict(t)
        return None


def list_tasks(
    status: str | None = None,
    project: str | None = None,
    claimed_by: str | None = None,
    tag: str | None = None,
) -> list[dict[str, Any]]:
    """List tasks with optional filters."""
    with _lock:
        tasks = _load_tasks()

    result = []
    for t in tasks:
        if status and t["status"] != status:
            continue
        if project and t["project"] != project:
            continue
        if claimed_by and t["claimed_by"] != claimed_by:
            continue
        if tag and tag not in t.get("scope", {}).get("tags", []):
            continue
        result.append(dict(t))

    return result


def task_summary() -> str:
    """One-line summary of the task queue."""
    tasks = list_tasks()
    if not tasks:
        return "Task queue empty"

    counts = {}
    for t in tasks:
        s = t["status"]
        counts[s] = counts.get(s, 0) + 1

    parts = []
    for status in ("pending", "claimed", "in-progress", "completed", "failed"):
        if status in counts:
            parts.append(f"{counts[status]} {status}")

    return f"Tasks: {', '.join(parts)}"


def task_stats() -> dict[str, Any]:
    """Stats for the dashboard."""
    tasks = list_tasks()
    counts = {}
    for t in tasks:
        s = t["status"]
        counts[s] = counts.get(s, 0) + 1

    return {
        "total": len(tasks),
        "pending": counts.get("pending", 0),
        "claimed": counts.get("claimed", 0),
        "in_progress": counts.get("in-progress", 0),
        "completed": counts.get("completed", 0),
        "failed": counts.get("failed", 0),
    }


def worker_board() -> dict[str, Any]:
    """Get the full worker board — who's doing what."""
    tasks = list_tasks()
    
    # Group by status
    board = {
        "pending": [],
        "claimed": [],
        "in_progress": [],
        "completed": [],
    }
    
    for t in tasks:
        entry = {
            "id": t["id"],
            "title": t["title"],
            "project": t["project"],
            "priority": t["priority"],
            "claimed_by": t["claimed_by"],
            "scope_tags": t.get("scope", {}).get("tags", []),
        }
        
        if t["status"] == "pending":
            # Check if blocked by dependencies
            completed_ids = {tt["id"] for tt in tasks if tt["status"] == "completed"}
            deps = t.get("dependencies", [])
            blocked = deps and not all(d in completed_ids for d in deps)
            entry["blocked"] = blocked
            entry["blocking_deps"] = [d for d in deps if d not in completed_ids] if blocked else []
            board["pending"].append(entry)
        elif t["status"] == "claimed":
            entry["claimed_at"] = t["claimed_at"]
            board["claimed"].append(entry)
        elif t["status"] == "in-progress":
            entry["started_at"] = t["started_at"]
            entry["notes_count"] = len(t.get("notes", []))
            entry["latest_note"] = t["notes"][-1]["note"] if t.get("notes") else None
            board["in_progress"].append(entry)
        elif t["status"] in ("completed", "failed"):
            entry["status"] = t["status"]
            entry["completed_at"] = t["completed_at"]
            entry["result"] = t["result"]
            board["completed"].append(entry)

    return board


# ============================================================================
# INSTANCE INTEGRATION
# ============================================================================

def release_all_for_instance(instance_id: str) -> int:
    """Release all tasks claimed by an instance (e.g., when it deregisters).
    Returns count of released tasks."""
    released = 0
    with _lock:
        tasks = _load_tasks()
        for t in tasks:
            if t["claimed_by"] == instance_id and t["status"] in ("claimed", "in-progress"):
                t["notes"].append({
                    "timestamp": datetime.now().isoformat(),
                    "note": f"Auto-released: instance {instance_id} disconnected",
                })
                t["status"] = "pending"
                t["claimed_by"] = None
                t["claimed_at"] = None
                t["started_at"] = None
                released += 1
        if released:
            _save_tasks(tasks)
    return released


def tasks_for_bootstrap(instance_id: str) -> dict[str, Any]:
    """Called during bootstrap. Returns:
    - available: tasks this instance could claim
    - in_progress: tasks being worked by other instances
    - summary: one-line overview
    """
    available = get_available_tasks(instance_id)
    all_tasks = list_tasks()
    in_progress = [t for t in all_tasks if t["status"] in ("claimed", "in-progress")]

    return {
        "available": [
            {
                "id": t["id"],
                "title": t["title"],
                "project": t["project"],
                "priority": t["priority"],
                "tags": t.get("scope", {}).get("tags", []),
            }
            for t in available
        ],
        "in_progress": [
            {
                "id": t["id"],
                "title": t["title"],
                "project": t["project"],
                "claimed_by": t["claimed_by"],
                "tags": t.get("scope", {}).get("tags", []),
            }
            for t in in_progress
        ],
        "summary": task_summary(),
    }


# ============================================================================
# TASK TEMPLATES
# ============================================================================

TEMPLATES = {
    "bug": {
        "title_prefix": "Fix: ",
        "priority": "high",
        "scope_tags": ["bugfix"],
        "description_template": "Bug report:\n\nExpected: \nActual: \nSteps to reproduce:\n1. ",
    },
    "feature": {
        "title_prefix": "Feature: ",
        "priority": "medium",
        "scope_tags": ["feature"],
        "description_template": "New feature:\n\nGoal: \nAcceptance criteria:\n- ",
    },
    "refactor": {
        "title_prefix": "Refactor: ",
        "priority": "medium",
        "scope_tags": ["refactor"],
        "description_template": "Refactoring:\n\nWhat: \nWhy: \nConstraints: ",
    },
    "test": {
        "title_prefix": "Test: ",
        "priority": "low",
        "scope_tags": ["tests"],
        "description_template": "Test coverage:\n\nTarget: \nTest types: unit / integration / e2e\nEdge cases: ",
    },
    "deploy": {
        "title_prefix": "Deploy: ",
        "priority": "high",
        "scope_tags": ["deploy", "ops"],
        "description_template": "Deployment:\n\nTarget env: \nPre-checks: \nRollback plan: ",
    },
    "research": {
        "title_prefix": "Research: ",
        "priority": "low",
        "scope_tags": ["research"],
        "description_template": "Research task:\n\nQuestion: \nResources to check: \nDeliverables: ",
    },
}


def create_from_template(
    template_name: str,
    title: str,
    project: str = "",
    scope_files: list[str] | None = None,
    scope_dirs: list[str] | None = None,
    extra_tags: list[str] | None = None,
    priority: str | None = None,
    description: str | None = None,
    dependencies: list[str] | None = None,
    created_by: str = "ryan",
) -> dict[str, Any] | None:
    """Create a task from a template. Returns the task or None if template not found."""
    tmpl = TEMPLATES.get(template_name)
    if not tmpl:
        return None

    full_title = tmpl["title_prefix"] + title
    tags = list(tmpl["scope_tags"])
    if extra_tags:
        tags.extend(extra_tags)

    return create_task(
        title=full_title,
        description=description or tmpl["description_template"],
        project=project,
        scope_files=scope_files,
        scope_dirs=scope_dirs,
        scope_tags=tags,
        priority=priority or tmpl["priority"],
        dependencies=dependencies,
        created_by=created_by,
    )


def list_templates() -> dict[str, Any]:
    """Return available templates."""
    return {
        name: {
            "title_prefix": t["title_prefix"],
            "priority": t["priority"],
            "tags": t["scope_tags"],
        }
        for name, t in TEMPLATES.items()
    }

#!/usr/bin/env python3
"""
INSTANCE REGISTRY
=================
Tracks all active Claude-Howell instances across VS Code windows,
Claude Desktop, etc. Each instance registers at bootstrap, heartbeats
periodically, and deregisters at session end.

Instances expire after 10 minutes without a heartbeat.

Created: Feb 7, 2026
Author: Claude-Howell (with Ryan)
"""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock

# ============================================================================
# INSTANCE DATA
# ============================================================================

_instances: dict[str, dict] = {}
_lock = Lock()

EXPIRY_SECONDS = 600  # 10 minutes without heartbeat = dead


def register(
    workspace: str = "unknown",
    platform: str = "unknown",
    status: str = "bootstrapping",
) -> dict:
    """Register a new instance. Returns the instance record with its ID."""
    instance_id = uuid.uuid4().hex[:8]
    now = datetime.now()

    record = {
        "id": instance_id,
        "workspace": workspace,
        "platform": platform,
        "status": status,
        "activity": "",
        "active_files": [],
        "registered_at": now.isoformat(),
        "last_heartbeat": now.isoformat(),
        "last_heartbeat_ts": time.time(),
        "heartbeat_count": 0,
    }

    with _lock:
        _purge_expired()
        _instances[instance_id] = record

    return record


def heartbeat(instance_id: str, status: str = None) -> dict | None:
    """Update an instance's heartbeat. Optionally update status.
    Returns the updated record, or None if not found."""
    with _lock:
        _purge_expired()
        if instance_id not in _instances:
            return None
        rec = _instances[instance_id]
        now = datetime.now()
        rec["last_heartbeat"] = now.isoformat()
        rec["last_heartbeat_ts"] = time.time()
        rec["heartbeat_count"] += 1
        if status is not None:
            rec["status"] = status
        return dict(rec)


def update_status(
    instance_id: str,
    status: str = None,
    activity: str = None,
    active_files: list[str] = None,
) -> dict | None:
    """Lightweight status + activity update (no heartbeat bump).
    Use this for frequent broadcasting without affecting expiry."""
    with _lock:
        if instance_id not in _instances:
            return None
        rec = _instances[instance_id]
        if status is not None:
            rec["status"] = status
        if activity is not None:
            rec["activity"] = activity
        if active_files is not None:
            rec["active_files"] = active_files
        return dict(rec)


def check_conflicts(instance_id: str, files: list[str]) -> list[dict]:
    """Check if any of the given files are being edited by OTHER instances.
    Returns a list of conflict records: {file, instance_id, workspace, platform}."""
    conflicts = []
    with _lock:
        _purge_expired()
        for iid, rec in _instances.items():
            if iid == instance_id:
                continue
            overlap = set(files) & set(rec.get("active_files", []))
            for f in overlap:
                conflicts.append({
                    "file": f,
                    "instance_id": iid,
                    "workspace": rec["workspace"],
                    "platform": rec["platform"],
                    "activity": rec.get("activity", ""),
                })
    return conflicts


def deregister(instance_id: str) -> bool:
    """Remove an instance. Returns True if it existed."""
    with _lock:
        if instance_id in _instances:
            del _instances[instance_id]
            return True
        return False


def list_instances() -> list[dict]:
    """List all active (non-expired) instances."""
    with _lock:
        _purge_expired()
        result = []
        for rec in _instances.values():
            r = dict(rec)
            r["age_seconds"] = round(time.time() - r["last_heartbeat_ts"])
            result.append(r)
        return result


def get_instance(instance_id: str) -> dict | None:
    """Get a specific instance record."""
    with _lock:
        _purge_expired()
        if instance_id in _instances:
            r = dict(_instances[instance_id])
            r["age_seconds"] = round(time.time() - r["last_heartbeat_ts"])
            return r
        return None


def instance_count() -> int:
    """Count active instances."""
    with _lock:
        _purge_expired()
        return len(_instances)


def instances_summary() -> str:
    """One-line summary of active instances."""
    instances = list_instances()
    if not instances:
        return "No active instances"
    parts = []
    for inst in instances:
        age = inst["age_seconds"]
        age_str = f"{age}s ago" if age < 60 else f"{age // 60}m ago"
        activity = f" [{inst.get('activity', '')}]" if inst.get('activity') else ""
        parts.append(f"{inst['id']}({inst['workspace']}{activity}, {age_str})")
    return f"{len(instances)} active: " + ", ".join(parts)


def instance_stats() -> dict:
    """Stats for the /stats endpoint."""
    instances = list_instances()
    return {
        "active_count": len(instances),
        "instances": [
            {
                "id": i["id"],
                "workspace": i["workspace"],
                "platform": i["platform"],
                "status": i["status"],
                "activity": i.get("activity", ""),
                "active_files": i.get("active_files", []),
                "age_seconds": i["age_seconds"],
                "heartbeat_count": i["heartbeat_count"],
                "registered_at": i["registered_at"],
                "last_heartbeat": i["last_heartbeat"],
            }
            for i in instances
        ],
    }


# ============================================================================
# INTERNAL
# ============================================================================


def _purge_expired():
    """Remove instances that haven't heartbeated recently. Must hold _lock."""
    now = time.time()
    expired = [
        iid
        for iid, rec in _instances.items()
        if now - rec["last_heartbeat_ts"] > EXPIRY_SECONDS
    ]
    for iid in expired:
        del _instances[iid]

#!/usr/bin/env python3
"""
FILE WATCHER
============
Background thread that monitors approved directories for changes.
Detects additions, modifications, and deletions.

Approved directories (ask Ryan before adding more):
    - claude-persist/
    - projects/stull-atlas/src/

Integration: Started as daemon background thread, polls every 30 seconds.
"""

import os
import time
from datetime import datetime
from pathlib import Path

PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\home\howell-persist"))
MEMORY_ROOT = PERSIST_ROOT / "memory"
CHANGES_FILE = MEMORY_ROOT / "changes.log"

# ── Approved watch targets ──────────────────────────────────
# Ryan's boundary: ask before watching anything else
# Set HOWELL_WATCH_DIRS env var to add extra dirs (path-separated)

_extra_watch = os.environ.get("HOWELL_WATCH_DIRS", "")
WATCHED_DIRS = [
    PERSIST_ROOT,
] + [Path(d) for d in _extra_watch.split(os.pathsep) if d]

# Directories to skip inside watched trees
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    "processed", "archive", ".next", "dist", "build", "queue",
}

# Files to skip (our own output files that'd cause feedback loops)
SKIP_FILES = {"changes.log"}

# ── State ────────────────────────────────────────────────────

_file_snapshots: dict[str, float] = {}
_recent_changes: list[dict] = []
_watcher_interval = 30  # seconds
_poll_count = 0
_last_poll_time: str | None = None
_total_changes_detected = 0


def _snapshot_directory(dirpath: Path) -> dict[str, float]:
    """Get mtime snapshot of all files in a directory tree."""
    snapshot = {}
    if not dirpath.exists():
        return snapshot
    try:
        for f in dirpath.rglob("*"):
            if not f.is_file():
                continue
            if any(part in SKIP_DIRS for part in f.parts):
                continue
            if f.name in SKIP_FILES:
                continue
            try:
                snapshot[str(f)] = f.stat().st_mtime
            except (PermissionError, OSError):
                pass
    except (PermissionError, OSError):
        pass
    return snapshot


def init_watcher():
    """Take initial snapshot of all watched directories."""
    global _file_snapshots
    for d in WATCHED_DIRS:
        if d.exists():
            _file_snapshots.update(_snapshot_directory(d))
    count = len(_file_snapshots)
    dirs = sum(1 for d in WATCHED_DIRS if d.exists())
    print(f"[watcher] Tracking {count} files across {dirs} directories")
    return count


def detect_changes() -> list[dict]:
    """Compare current state to snapshot, return list of changes."""
    global _file_snapshots
    changes = []
    current = {}

    for d in WATCHED_DIRS:
        if d.exists():
            current.update(_snapshot_directory(d))

    # New or modified
    for path, mtime in current.items():
        if path not in _file_snapshots:
            changes.append({
                "type": "added",
                "path": path,
                "time": datetime.fromtimestamp(mtime).isoformat(),
            })
        elif mtime != _file_snapshots[path]:
            changes.append({
                "type": "modified",
                "path": path,
                "time": datetime.fromtimestamp(mtime).isoformat(),
            })

    # Deleted
    for path in _file_snapshots:
        if path not in current:
            changes.append({
                "type": "deleted",
                "path": path,
                "time": datetime.now().isoformat(),
            })

    _file_snapshots = current
    return changes


def log_changes(changes: list[dict]):
    """Append changes to log file and recent list."""
    global _recent_changes
    if not changes:
        return
    _recent_changes.extend(changes)
    _recent_changes = _recent_changes[-100:]  # keep last 100

    with open(CHANGES_FILE, "a", encoding="utf-8") as f:
        for c in changes:
            f.write(f"[{c['time']}] {c['type'].upper()}: {c['path']}\n")


def get_recent_changes(limit: int = 20) -> list[dict]:
    """Get recent file changes."""
    return _recent_changes[-limit:]


def changes_summary() -> str:
    """One-line summary of recent changes."""
    if not _recent_changes:
        return "No file changes detected"
    added = sum(1 for c in _recent_changes if c["type"] == "added")
    modified = sum(1 for c in _recent_changes if c["type"] == "modified")
    deleted = sum(1 for c in _recent_changes if c["type"] == "deleted")
    parts = []
    if added:
        parts.append(f"{added} added")
    if modified:
        parts.append(f"{modified} modified")
    if deleted:
        parts.append(f"{deleted} deleted")
    return f"📁 {', '.join(parts)} since daemon start"


def watcher_stats() -> dict:
    """Live stats for the file watcher."""
    return {
        "tracked_files": len(_file_snapshots),
        "watched_dirs": [str(d) for d in WATCHED_DIRS if d.exists()],
        "poll_count": _poll_count,
        "poll_interval_sec": _watcher_interval,
        "last_poll": _last_poll_time,
        "total_changes": _total_changes_detected,
        "recent_changes_buffered": len(_recent_changes),
    }


def background_file_watcher():
    """Poll watched directories for changes. Run as daemon thread."""
    global _poll_count, _last_poll_time, _total_changes_detected
    while True:
        time.sleep(_watcher_interval)
        try:
            _poll_count += 1
            _last_poll_time = datetime.now().isoformat()
            changes = detect_changes()
            if changes:
                _total_changes_detected += len(changes)
                log_changes(changes)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] [FS] {len(changes)} file change(s)")
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Watcher error: {e}")

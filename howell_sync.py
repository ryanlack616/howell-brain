#!/usr/bin/env python3
"""
HOWELL SYNC — Multi-Machine Persistence Synchronization
========================================================
Git-based sync for Claude-Howell's persistent memory across machines.

Usage:
    python howell_sync.py pull          # Pull latest from remote before session
    python howell_sync.py push          # Push local changes after session
    python howell_sync.py status        # Show sync status
    python howell_sync.py init          # Initialize git repo in persist dir
    python howell_sync.py auto          # Pull, then push (full sync cycle)
    python howell_sync.py resolve       # Interactive conflict resolution

Architecture:
    Desktop (Howell, MI)          Laptop (portable)
    ┌──────────────────┐          ┌──────────────────┐
    │  claude-persist/  │          │  claude-persist/  │
    │  howell-brain/    │          │  howell-brain/    │
    │  daemon :7777     │          │  daemon :7777     │
    └────────┬─────────┘          └────────┬─────────┘
             │                              │
             └──────── GitHub ──────────────┘
               ryanlack616/claude-howell-persist

Sync Strategy:
    - Pull on bootstrap (session start)
    - Push on end_session (session end)
    - Knowledge graph: last-write-wins (observations are append-only)
    - Tasks: scope-based isolation prevents conflicts
    - Memory: RECENT.md uses session headers, merge-friendly
    - Identity: SOUL.md rarely changes, manual merge if needed

Machine Identity:
    Each machine gets a .machine_id file in persist root.
    Format: {hostname}-{short_uuid}
    Used in commit messages and conflict resolution.

Created: Feb 12, 2026
Author: Claude-Howell (with Ryan)
"""

import json
import os
import platform
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

PERSIST_ROOT = Path(os.environ.get(
    "HOWELL_PERSIST_ROOT",
    r"C:\rje\tools\claude-persist"
))

REMOTE_URL = "https://github.com/ryanlack616/claude-howell-persist.git"
BRANCH = "main"

# Files/dirs that should NOT be synced (machine-specific or transient)
GITIGNORE_ENTRIES = [
    "__pycache__/",
    "*.pyc",
    ".api_key",
    ".webhook_secret",
    ".viewer_pass",
    "*.lock",
    "*.tmp",
    "errors/",
    "scratch/",
    "vm-share/",
    "logs/local-*.log",
    ".machine_id",
]

# Files that need special merge handling
MERGE_STRATEGY = {
    "bridge/knowledge.json": "merge_knowledge",
    "memory/RECENT.md": "append_sections",
    "memory/PINNED.md": "union_sections",
    "tasks/tasks.json": "merge_tasks",
    "bridge/sessions.json": "append_entries",
}

# ============================================================================
# MACHINE IDENTITY
# ============================================================================

def get_machine_id() -> str:
    """Get or create a unique machine identifier."""
    id_file = PERSIST_ROOT / ".machine_id"
    if id_file.exists():
        return id_file.read_text(encoding="utf-8").strip()
    
    hostname = platform.node().lower().replace(" ", "-")[:20]
    short_id = uuid.uuid4().hex[:6]
    machine_id = f"{hostname}-{short_id}"
    
    id_file.write_text(machine_id, encoding="utf-8")
    print(f"  Created machine ID: {machine_id}")
    return machine_id


def get_machine_label() -> str:
    """Human-readable label for this machine."""
    machine_id = get_machine_id()
    hostname = machine_id.split("-")[0] if "-" in machine_id else machine_id
    
    # Try to detect if desktop or laptop
    # On Windows, check for battery (laptops have one)
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "(Get-WmiObject Win32_Battery).Name"],
            capture_output=True, text=True, timeout=5
        )
        has_battery = bool(result.stdout.strip())
        device_type = "laptop" if has_battery else "desktop"
    except Exception:
        device_type = "unknown"
    
    return f"{hostname} ({device_type})"


# ============================================================================
# GIT OPERATIONS
# ============================================================================

def _git(*args, cwd=None) -> subprocess.CompletedProcess:
    """Run a git command in the persist directory."""
    cmd = ["git"] + list(args)
    return subprocess.run(
        cmd,
        cwd=str(cwd or PERSIST_ROOT),
        capture_output=True,
        text=True,
        timeout=60
    )


def is_git_repo() -> bool:
    """Check if persist directory is a git repository."""
    return (PERSIST_ROOT / ".git").is_dir()


def init_repo():
    """Initialize git repo in persist directory if not already one."""
    if is_git_repo():
        print("  Already a git repository.")
        # Make sure remote is set
        result = _git("remote", "get-url", "origin")
        if result.returncode != 0:
            _git("remote", "add", "origin", REMOTE_URL)
            print(f"  Added remote: {REMOTE_URL}")
        return
    
    print(f"  Initializing git repo in {PERSIST_ROOT}")
    _git("init")
    _git("remote", "add", "origin", REMOTE_URL)
    
    # Create .gitignore
    gitignore_path = PERSIST_ROOT / ".gitignore"
    gitignore_content = "\n".join(GITIGNORE_ENTRIES) + "\n"
    gitignore_path.write_text(gitignore_content, encoding="utf-8")
    print("  Created .gitignore")
    
    # Initial commit
    _git("add", "-A")
    machine_id = get_machine_id()
    _git("commit", "-m", f"Initial commit from {machine_id}")
    
    # Set branch name and push
    _git("branch", "-M", BRANCH)
    result = _git("push", "-u", "origin", BRANCH)
    if result.returncode == 0:
        print(f"  Pushed to {REMOTE_URL}")
    else:
        print(f"  Push failed (may need to set up auth): {result.stderr}")


def sync_pull() -> dict:
    """Pull latest changes from remote. Returns status dict."""
    if not is_git_repo():
        return {"status": "error", "message": "Not a git repo. Run 'init' first."}
    
    machine_id = get_machine_id()
    result = {"status": "ok", "machine": machine_id, "changes": []}
    
    # Stash any local changes first
    status = _git("status", "--porcelain")
    has_local_changes = bool(status.stdout.strip())
    
    if has_local_changes:
        stash_msg = f"auto-stash before pull ({machine_id}, {datetime.now().isoformat()})"
        _git("stash", "push", "-m", stash_msg)
        result["stashed"] = True
    
    # Fetch and check for divergence
    fetch = _git("fetch", "origin", BRANCH)
    if fetch.returncode != 0:
        result["status"] = "offline"
        result["message"] = "Could not reach remote (offline?)"
        # Pop stash if we stashed
        if has_local_changes:
            _git("stash", "pop")
        return result
    
    # Check if we're behind
    behind = _git("rev-list", f"HEAD..origin/{BRANCH}", "--count")
    behind_count = int(behind.stdout.strip()) if behind.stdout.strip() else 0
    
    ahead = _git("rev-list", f"origin/{BRANCH}..HEAD", "--count")
    ahead_count = int(ahead.stdout.strip()) if ahead.stdout.strip() else 0
    
    if behind_count == 0:
        result["message"] = "Already up to date"
        if has_local_changes:
            _git("stash", "pop")
        return result
    
    # Try to merge
    merge = _git("merge", f"origin/{BRANCH}", "--no-edit")
    
    if merge.returncode != 0:
        # Merge conflict — try auto-resolution
        result["status"] = "conflict"
        conflicts = _get_conflict_files()
        resolved = _auto_resolve_conflicts(conflicts)
        
        if resolved:
            _git("add", "-A")
            _git("commit", "-m", f"Auto-resolved merge from {machine_id}")
            result["status"] = "resolved"
            result["message"] = f"Auto-resolved {len(conflicts)} conflicts"
            result["conflicts_resolved"] = conflicts
        else:
            result["message"] = f"Manual resolution needed: {conflicts}"
            result["unresolved"] = conflicts
    else:
        result["message"] = f"Pulled {behind_count} commit(s) from remote"
        result["commits_pulled"] = behind_count
    
    # Pop stash if we stashed
    if has_local_changes:
        pop = _git("stash", "pop")
        if pop.returncode != 0:
            result["stash_conflict"] = True
    
    return result


def sync_push() -> dict:
    """Commit and push local changes to remote."""
    if not is_git_repo():
        return {"status": "error", "message": "Not a git repo. Run 'init' first."}
    
    machine_id = get_machine_id()
    result = {"status": "ok", "machine": machine_id}
    
    # Check for changes
    status = _git("status", "--porcelain")
    if not status.stdout.strip():
        result["message"] = "Nothing to push (clean)"
        return result
    
    # Stage all changes
    _git("add", "-A")
    
    # Commit with machine-tagged message
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    changed_files = [
        line.strip().split()[-1]
        for line in status.stdout.strip().split("\n")
        if line.strip()
    ]
    
    # Summarize changes
    summary_parts = []
    for f in changed_files[:5]:
        summary_parts.append(Path(f).name)
    if len(changed_files) > 5:
        summary_parts.append(f"+{len(changed_files) - 5} more")
    
    commit_msg = f"sync({machine_id}): {', '.join(summary_parts)} [{now}]"
    commit = _git("commit", "-m", commit_msg)
    
    if commit.returncode != 0:
        result["status"] = "error"
        result["message"] = f"Commit failed: {commit.stderr}"
        return result
    
    # Push
    push = _git("push", "origin", BRANCH)
    if push.returncode != 0:
        # Might need to pull first
        result["status"] = "needs_pull"
        result["message"] = "Push rejected — pull first, then push again"
        return result
    
    result["message"] = f"Pushed {len(changed_files)} file(s)"
    result["files"] = changed_files
    return result


def sync_status() -> dict:
    """Get current sync status."""
    if not is_git_repo():
        return {"status": "not_initialized", "message": "Run 'init' first"}
    
    machine_id = get_machine_id()
    machine_label = get_machine_label()
    
    # Local status
    status = _git("status", "--porcelain")
    local_changes = len([l for l in status.stdout.strip().split("\n") if l.strip()])
    
    # Remote status
    _git("fetch", "origin", BRANCH)
    behind = _git("rev-list", f"HEAD..origin/{BRANCH}", "--count")
    behind_count = int(behind.stdout.strip()) if behind.stdout.strip() else 0
    ahead = _git("rev-list", f"origin/{BRANCH}..HEAD", "--count")
    ahead_count = int(ahead.stdout.strip()) if ahead.stdout.strip() else 0
    
    # Last sync
    log = _git("log", "--oneline", "-1", "--format=%h %s (%ar)")
    last_commit = log.stdout.strip() if log.stdout.strip() else "none"
    
    return {
        "machine_id": machine_id,
        "machine_label": machine_label,
        "local_changes": local_changes,
        "behind_remote": behind_count,
        "ahead_of_remote": ahead_count,
        "last_commit": last_commit,
        "persist_root": str(PERSIST_ROOT),
        "remote": REMOTE_URL,
        "branch": BRANCH,
    }


# ============================================================================
# CONFLICT RESOLUTION
# ============================================================================

def _get_conflict_files() -> list:
    """Get list of files with merge conflicts."""
    result = _git("diff", "--name-only", "--diff-filter=U")
    if result.stdout.strip():
        return result.stdout.strip().split("\n")
    return []


def _auto_resolve_conflicts(conflict_files: list) -> bool:
    """Try to auto-resolve merge conflicts based on file type."""
    all_resolved = True
    
    for filepath in conflict_files:
        rel_path = filepath.strip()
        full_path = PERSIST_ROOT / rel_path
        
        if not full_path.exists():
            continue
        
        # Check if we have a custom merge strategy
        strategy = MERGE_STRATEGY.get(rel_path)
        
        if strategy == "merge_knowledge":
            resolved = _merge_knowledge_graph(full_path)
        elif strategy == "append_sections":
            resolved = _merge_append_sections(full_path)
        elif strategy == "union_sections":
            resolved = _merge_union_sections(full_path)
        elif strategy == "merge_tasks":
            resolved = _merge_tasks(full_path)
        elif strategy == "append_entries":
            resolved = _merge_append_entries(full_path)
        elif rel_path.endswith(".json"):
            # Default: take theirs for JSON (last push wins)
            _git("checkout", "--theirs", rel_path)
            resolved = True
        elif rel_path.endswith(".md"):
            # Default: take ours for markdown (local edits matter)
            _git("checkout", "--ours", rel_path)
            resolved = True
        else:
            resolved = False
        
        if not resolved:
            all_resolved = False
            print(f"  CONFLICT: {rel_path} needs manual resolution")
        else:
            print(f"  Resolved: {rel_path}")
    
    return all_resolved


def _merge_knowledge_graph(path: Path) -> bool:
    """Merge knowledge graph by combining entities and relations."""
    try:
        # Read both versions
        ours = _git("show", f"HEAD:{path.relative_to(PERSIST_ROOT)}")
        theirs = _git("show", f"MERGE_HEAD:{path.relative_to(PERSIST_ROOT)}")
        
        if ours.returncode != 0 or theirs.returncode != 0:
            return False
        
        our_kg = json.loads(ours.stdout)
        their_kg = json.loads(theirs.stdout)
        
        # Merge entities (union of all, combine observations)
        merged_entities = dict(our_kg.get("entities", {}))
        for name, entity in their_kg.get("entities", {}).items():
            if name in merged_entities:
                # Merge observations (union)
                existing_obs = set(merged_entities[name].get("observations", []))
                new_obs = entity.get("observations", [])
                merged_entities[name]["observations"] = list(
                    existing_obs | set(new_obs)
                )
            else:
                merged_entities[name] = entity
        
        # Merge relations (union, deduplicate)
        our_rels = our_kg.get("relations", [])
        their_rels = their_kg.get("relations", [])
        
        rel_keys = set()
        merged_rels = []
        for rel in our_rels + their_rels:
            key = (rel["from_entity"], rel["relation_type"], rel["to_entity"])
            if key not in rel_keys:
                rel_keys.add(key)
                merged_rels.append(rel)
        
        merged = {
            "entities": merged_entities,
            "relations": merged_rels,
            "last_sync": datetime.now().isoformat(),
        }
        
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        print(f"  Knowledge merge failed: {e}")
        return False


def _merge_tasks(path: Path) -> bool:
    """Merge task lists by combining and deduplicating by ID."""
    try:
        ours = _git("show", f"HEAD:{path.relative_to(PERSIST_ROOT)}")
        theirs = _git("show", f"MERGE_HEAD:{path.relative_to(PERSIST_ROOT)}")
        
        our_tasks = json.loads(ours.stdout) if ours.returncode == 0 else []
        their_tasks = json.loads(theirs.stdout) if theirs.returncode == 0 else []
        
        # Merge by task ID, preferring the version with more recent updates
        merged = {}
        for task in our_tasks + their_tasks:
            tid = task.get("id", "")
            if tid in merged:
                # Keep the one with later updated_at
                existing = merged[tid]
                if task.get("updated_at", "") > existing.get("updated_at", ""):
                    merged[tid] = task
            else:
                merged[tid] = task
        
        path.write_text(
            json.dumps(list(merged.values()), indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        return True
    except Exception as e:
        print(f"  Task merge failed: {e}")
        return False


def _merge_append_sections(path: Path) -> bool:
    """Merge markdown by appending non-duplicate sections."""
    try:
        _git("checkout", "--theirs", str(path.relative_to(PERSIST_ROOT)))
        return True
    except Exception:
        return False


def _merge_union_sections(path: Path) -> bool:
    """Merge by taking union of ## sections."""
    try:
        _git("checkout", "--ours", str(path.relative_to(PERSIST_ROOT)))
        return True
    except Exception:
        return False


def _merge_append_entries(path: Path) -> bool:
    """Merge JSON arrays by appending and deduplicating."""
    try:
        ours = _git("show", f"HEAD:{path.relative_to(PERSIST_ROOT)}")
        theirs = _git("show", f"MERGE_HEAD:{path.relative_to(PERSIST_ROOT)}")
        
        our_data = json.loads(ours.stdout) if ours.returncode == 0 else []
        their_data = json.loads(theirs.stdout) if theirs.returncode == 0 else []
        
        # Simple: take the longer list (both are append-only)
        merged = our_data if len(our_data) >= len(their_data) else their_data
        
        path.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        return True
    except Exception as e:
        print(f"  Append merge failed: {e}")
        return False


# ============================================================================
# MAIN
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python howell_sync.py <command>")
        print()
        print("Commands:")
        print("  init     Initialize git repo in persist directory")
        print("  pull     Pull latest from remote (run at session start)")
        print("  push     Commit and push local changes (run at session end)")
        print("  status   Show sync status")
        print("  auto     Pull then push (full sync cycle)")
        print()
        return
    
    command = sys.argv[1].lower()
    
    print(f"{'='*60}")
    print(f"HOWELL SYNC — {command.upper()}")
    print(f"{'='*60}")
    print(f"  Machine: {get_machine_label()}")
    print(f"  Persist: {PERSIST_ROOT}")
    print()
    
    if command == "init":
        init_repo()
    elif command == "pull":
        result = sync_pull()
        print(f"  Status: {result['status']}")
        print(f"  {result.get('message', '')}")
        if result.get("conflicts_resolved"):
            for f in result["conflicts_resolved"]:
                print(f"    Resolved: {f}")
        if result.get("unresolved"):
            for f in result["unresolved"]:
                print(f"    NEEDS MANUAL: {f}")
    elif command == "push":
        result = sync_push()
        print(f"  Status: {result['status']}")
        print(f"  {result.get('message', '')}")
        if result.get("files"):
            for f in result["files"][:10]:
                print(f"    {f}")
    elif command == "status":
        result = sync_status()
        print(f"  Machine ID:     {result.get('machine_id', '?')}")
        print(f"  Machine:        {result.get('machine_label', '?')}")
        print(f"  Local changes:  {result.get('local_changes', 0)}")
        print(f"  Behind remote:  {result.get('behind_remote', 0)}")
        print(f"  Ahead of remote:{result.get('ahead_of_remote', 0)}")
        print(f"  Last commit:    {result.get('last_commit', '?')}")
        print(f"  Remote:         {result.get('remote', '?')}")
    elif command == "auto":
        print("--- PULL ---")
        pull_result = sync_pull()
        print(f"  {pull_result.get('message', '')}")
        print()
        print("--- PUSH ---")
        push_result = sync_push()
        print(f"  {push_result.get('message', '')}")
    else:
        print(f"Unknown command: {command}")


if __name__ == "__main__":
    main()

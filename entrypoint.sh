#!/bin/sh
# ============================================================================
# HOWELL DAEMON — Fly.io Entrypoint
# ============================================================================
# First-run: seeds /data with identity files from baked-in defaults.
# Subsequent runs: just starts the daemon pointing at existing /data volume.
# ============================================================================

set -e

DATA_DIR="/data"
MARKER="$DATA_DIR/.initialized"

echo "=== Howell Brain Entrypoint ==="
echo "PERSIST_ROOT: $DATA_DIR"

# ── First-run initialization ──────────────────────────────────────────────
if [ ! -f "$MARKER" ]; then
    echo "[INIT] First run detected — seeding data volume..."

    # Create directory structure
    mkdir -p "$DATA_DIR/bridge" \
             "$DATA_DIR/memory/archive" \
             "$DATA_DIR/memory/inbox" \
             "$DATA_DIR/logs" \
             "$DATA_DIR/tasks" \
             "$DATA_DIR/procedures" \
             "$DATA_DIR/uncertain" \
             "$DATA_DIR/errors" \
             "$DATA_DIR/queue/comfyui" \
             "$DATA_DIR/queue/moltbook" \
             "$DATA_DIR/versions" \
             "$DATA_DIR/scratch"

    # Seed identity files from /app/seed/ if they exist
    if [ -d "/app/seed" ]; then
        echo "[INIT] Copying seed data..."
        cp -r /app/seed/* "$DATA_DIR/" 2>/dev/null || true
    else
        echo "[INIT] No seed data found — creating minimal identity"
        echo "# Claude-Howell" > "$DATA_DIR/SOUL.md"
        echo "# Context" > "$DATA_DIR/CONTEXT.md"
        echo "# Projects" > "$DATA_DIR/PROJECTS.md"
        echo "# Memory" > "$DATA_DIR/MEMORY.md"
        echo "# Policy" > "$DATA_DIR/POLICY.md"
        echo "{}" > "$DATA_DIR/bridge/knowledge.json"
        echo "[]" > "$DATA_DIR/bridge/sessions.json"
        echo "" > "$DATA_DIR/memory/RECENT.md"
        echo "" > "$DATA_DIR/memory/PINNED.md"
        echo "" > "$DATA_DIR/memory/SUMMARY.md"
        echo "# Pinned Memories" > "$DATA_DIR/memory/PINNED.md"
    fi

    # Mark initialization complete
    date -u > "$MARKER"
    echo "[INIT] Data volume initialized."
fi

# ── Write config.json so bridge modules can find it ───────────────────────
# This goes in /app/ (next to __file__ for howell_bridge.py)
cat > /app/config.json << 'EOF'
{
  "persist_root": "/data",
  "daemon_port": 7777,
  "daemon_host": "0.0.0.0",
  "max_recent_sessions": 10,
  "heartbeat_interval_hours": 1,
  "watcher_interval_seconds": 30,
  "queue_interval_seconds": 10,
  "moltbook_interval_seconds": 60,
  "comfyui_url": "http://127.0.0.1:8188"
}
EOF

echo "[BOOT] Starting Howell Daemon..."
exec python howell_daemon.py

# ============================================================================
# HOWELL DAEMON — Fly.io Container
# ============================================================================
# Stdlib-only Python daemon. No pip install needed.
#
# Layout:
#   /app/           — Python code (from image, immutable)
#   /data/          — Persistent state (Fly.io volume, mutable)
#     SOUL.md, CONTEXT.md, PROJECTS.md, ...
#     bridge/knowledge.json, sessions.json, agents.db, .api_key
#     memory/, logs/, tasks/, procedures/, queue/, ...
# ============================================================================

FROM python:3.13-slim

# Metadata
LABEL maintainer="Ryan + Claude-Howell"
LABEL description="Howell Brain — always-on memory daemon"

# Working directory for code
WORKDIR /app

# Copy all Python code and static assets
COPY howell_daemon.py howell_bridge.py \
     file_watcher.py generation_queue.py moltbook_scheduler.py \
     instance_registry.py task_queue.py agent_db.py \
     kg_taichi.py \
     ./

# Static HTML served by daemon
COPY brain.html kg-explorer.html ./

# Entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create data directory (will be overlaid by Fly.io volume)
RUN mkdir -p /data/bridge /data/memory /data/logs /data/tasks \
             /data/procedures /data/uncertain /data/errors \
             /data/queue/comfyui /data/queue/moltbook

# Environment
ENV HOWELL_PERSIST_ROOT=/data
ENV PYTHONUNBUFFERED=1
ENV TZ=America/New_York

EXPOSE 7777

ENTRYPOINT ["/entrypoint.sh"]

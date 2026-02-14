# Fly.io Deployment — Howell Brain
## Overview

The Howell daemon runs on Fly.io as a single-container app with a persistent volume.
- **App name**: `howell-brain`
- **Region**: `iad` (Ashburn, VA)  
- **URL**: `https://howell-brain.fly.dev` (or `brain.rlv.lol` via custom domain)
- **Cost**: ~$3/mo (shared-cpu-1x, 256MB, 1GB volume)

## Architecture

```
┌──────────────────────────────────────┐
│  Docker Container (python:3.13-slim) │
│  /app/  ← Python code (immutable)   │
│    howell_daemon.py                  │
│    howell_bridge.py                  │
│    file_watcher.py, etc.             │
│    brain.html, kg-explorer.html      │
├──────────────────────────────────────┤
│  /data/ ← Fly.io Volume (persistent)│
│    SOUL.md, CONTEXT.md, ...          │
│    bridge/knowledge.json             │
│    bridge/sessions.json              │
│    memory/, tasks/, procedures/      │
└──────────────────────────────────────┘
  ENV: HOWELL_PERSIST_ROOT=/data
```

## Prerequisites

1. Install flyctl: `curl -L https://fly.io/install.sh | sh`
2. Login: `fly auth login`

## First-time Deploy

```bash
cd bridge/

# 1. Create app + volume + deploy
./deploy.sh --init

# 2. Seed with current local state
./deploy.sh --seed

# 3. Verify
curl https://howell-brain.fly.dev/health
```

### Windows
```powershell
cd bridge\
.\deploy.ps1 -Init
.\deploy.ps1 -Seed
Invoke-RestMethod https://howell-brain.fly.dev/health
```

## Code Updates

```bash
# Just redeploy the image (volume data preserved)
./deploy.sh
```

## Custom Domain

```bash
fly certs add brain.rlv.lol --app howell-brain
# Then update DNS: CNAME brain.rlv.lol → howell-brain.fly.dev
```

## Key env vars

| Variable | Value | Purpose |
|----------|-------|---------|
| `HOWELL_PERSIST_ROOT` | `/data` | State directory (Fly.io volume) |
| `PYTHONUNBUFFERED` | `1` | Immediate log output |
| `TZ` | `America/New_York` | Ryan's timezone |

## Monitoring

- Health check: `GET /health` (public, no auth)
- Status: `GET /status` (public)
- Logs: `fly logs --app howell-brain`
- SSH: `fly ssh console --app howell-brain`

## Volumes

```bash
# List volumes
fly volumes list --app howell-brain

# Extend volume
fly volumes extend <vol-id> --size 2 --app howell-brain

# Snapshot (backup)
fly volumes snapshots list <vol-id> --app howell-brain
```

## How paths work

All modules respect `HOWELL_PERSIST_ROOT` environment variable:
- **Set**: Uses that path (Fly.io → `/data`)
- **Not set**: Falls back to `C:\rje\tools\claude-persist` (local Windows)

Priority: `HOWELL_PERSIST_ROOT` env var > `config.json` > hardcoded default.

Modules updated: `howell_bridge.py`, `howell_daemon.py`, `file_watcher.py`,
`generation_queue.py`, `moltbook_scheduler.py`, `task_queue.py`, `agent_db.py`.

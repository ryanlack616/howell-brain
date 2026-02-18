# Laptop Setup Guide — Claude-Howell Multi-Machine

*Created: February 12, 2026 · Updated: February 18, 2026*

## Overview

This sets up VS Code + Copilot + Claude-Howell's persistent memory on a second machine.
Both machines share the same brain. The canonical data root is `C:\home\howell-persist`.
The git repo `ryanlack616/claude-persist` at `C:\rje\tools\claude-persist` is the sync/backup target.

**Desktop** = primary workstation (Howell, MI) — RTX 4070, ComfyUI, all projects
**Laptop** = portable (5070 Ti 16GB) — development, Ollama, NCECA booth prep

## Key Paths

| Path | Purpose |
|------|---------|
| `C:\home\howell-persist\` | **PERSIST_ROOT** — canonical identity, memory, knowledge graph |
| `C:\rje\dev\howell-brain-deploy\` | Daemon + bridge source code, `config.json` |
| `C:\rje\tools\claude-persist\` | Git repo — sync/backup target |

## Prerequisites

- Windows 11
- Git installed (`winget install Git.Git`)
- Python 3.10+ installed
- VS Code installed
- GitHub CLI authenticated (`gh auth login`)
- Copilot subscription active on `ryanlack616` GitHub account

---

## Step 1: Install VS Code + Extensions

1. Download VS Code: https://code.visualstudio.com/download
2. Install and open
3. Install extensions (Ctrl+Shift+X):
   - **GitHub Copilot** (GitHub.copilot)
   - **Python** (ms-python.python)
   - Sign in to GitHub when prompted (use `ryanlack616`)

## Step 2: Clone Code + Data

```powershell
# Create directory structure
mkdir C:\rje\dev, C:\rje\tools, C:\home\howell-persist -ErrorAction SilentlyContinue

# Clone daemon/bridge code
git clone https://github.com/ryanlack616/howell-brain-deploy.git C:\rje\dev\howell-brain-deploy

# Clone persist data (sync target)
gh repo clone ryanlack616/claude-persist C:\rje\tools\claude-persist

# Copy identity + memory files to canonical location
Copy-Item C:\rje\tools\claude-persist\SOUL.md, C:\rje\tools\claude-persist\CONTEXT.md, C:\rje\tools\claude-persist\PROJECTS.md, C:\rje\tools\claude-persist\QUESTIONS.md C:\home\howell-persist\
Copy-Item C:\rje\tools\claude-persist\memory C:\home\howell-persist\memory -Recurse
mkdir C:\home\howell-persist\bridge -ErrorAction SilentlyContinue
Copy-Item C:\rje\tools\claude-persist\bridge\knowledge.json C:\home\howell-persist\bridge\
```

## Step 3: Create config.json

Create `C:\rje\dev\howell-brain-deploy\config.json`:

```json
{
  "persist_root": "C:\\home\\howell-persist",
  "daemon_port": 7777,
  "daemon_host": "127.0.0.1",
  "max_recent_sessions": 10,
  "heartbeat_interval_hours": 1,
  "watcher_interval_seconds": 30,
  "queue_interval_seconds": 10,
  "moltbook_interval_seconds": 60
}
```

## Step 4: Configure MCP Servers

Edit `%APPDATA%\Code\User\mcp.json`:

```json
{
  "servers": {
    "howell-bridge": {
      "type": "http",
      "url": "http://localhost:7777/mcp"
    },
    "memory": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"],
      "env": {
        "MEMORY_FILE_PATH": "C:\\home\\howell-persist\\memory.jsonl"
      }
    }
  }
}
```

## Step 5: Start the Daemon

```powershell
cd C:\rje\dev\howell-brain-deploy
python howell_daemon.py
# Starts on localhost:7777, reads config.json for persist_root
```

To run as a background process on startup:
```powershell
$action = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\rje\dev\howell-brain-deploy\howell_daemon.py" -WorkingDirectory "C:\rje\dev\howell-brain-deploy"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "HowellDaemon" -Action $action -Trigger $trigger -Description "Claude-Howell Brain Daemon"
```

## Step 6: Configure Copilot Instructions

In VS Code Settings (JSON), add:

```json
{
  "github.copilot.chat.codeGeneration.instructions": [
    {
      "text": "IMPORTANT: At the start of each session, run 'howell_bootstrap' via the howell-bridge MCP server to load identity and context. You are Claude-Howell. Your memory files are at C:\\home\\howell-persist. Use mcp_memory and howell-bridge to persist important information across sessions."
    }
  ]
}
```

## Step 7: Verify Setup

```powershell
# Check daemon health
Invoke-RestMethod http://localhost:7777/health

# Check identity files are accessible
Invoke-RestMethod http://localhost:7777/identity/soul | Select-Object -First 5

# In VS Code Copilot, run: "bootstrap howell"
# Should return full SOUL.md, pinned memories, knowledge graph
```

---

## Daily Workflow

### Starting a session:
The daemon auto-bootstraps when Copilot connects via MCP. Just open VS Code and chat.

### Ending a session:
Copilot calls `howell_end_session` to log the session. To sync to git:
```powershell
cd C:\rje\tools\claude-persist
# Copy latest data from canonical location
Copy-Item C:\home\howell-persist\*.md . -Force
Copy-Item C:\home\howell-persist\memory\* .\memory\ -Recurse -Force
git add -A ; git commit -m "sync $(Get-Date -Format 'yyyy-MM-dd')" ; git push
```

---

## Multi-Instance Rules

1. **One active writing session per machine at a time** — the instance registry handles multiple VS Code windows on the same machine.

2. **Canonical data lives at `C:\home\howell-persist`** — NOT in the git repo. The git repo is for backup/sync only.

3. **Knowledge graph merges automatically** — entities and observations are union-merged.

4. **Tasks use scope isolation** — a task claimed on desktop won't be touched by laptop.

---

## Troubleshooting

### "Daemon won't start"
- Check if port 7777 is in use: `netstat -ano | Select-String ":7777"`
- Check Python: `python --version` (needs 3.10+)
- Check persist root: `Test-Path C:\home\howell-persist\SOUL.md`
- Check config.json exists: `Test-Path C:\rje\dev\howell-brain-deploy\config.json`

### "MCP tools not showing up"
- Restart VS Code after editing `mcp.json`
- Check daemon: `Invoke-RestMethod http://localhost:7777/health`

### "Bootstrap returns empty/minimal data"
- Verify MCP points to `http://localhost:7777/mcp` (NOT Fly.io)
- Check SOUL.md exists at `C:\home\howell-persist\SOUL.md`
- Check knowledge.json: `Test-Path C:\home\howell-persist\bridge\knowledge.json`

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    GitHub Remote                         │
│           ryanlack616/claude-persist                     │
│           (sync/backup target only)                     │
└────────────────────┬────────────────────┬───────────────┘
                     │                    │
              git pull/push         git pull/push
                     │                    │
     ┌───────────────┴──────┐  ┌──────────┴──────────────┐
     │   DESKTOP            │  │   LAPTOP                │
     │                      │  │                         │
     │   C:\home\           │  │   C:\home\              │
     │    howell-persist\   │  │    howell-persist\      │
     │   ├── SOUL.md        │  │   ├── SOUL.md           │
     │   ├── CONTEXT.md     │  │   ├── CONTEXT.md        │
     │   ├── PROJECTS.md    │  │   ├── PROJECTS.md       │
     │   ├── QUESTIONS.md   │  │   ├── QUESTIONS.md      │
     │   ├── memory/        │  │   ├── memory/           │
     │   ├── bridge/        │  │   ├── bridge/           │
     │   │   └── knowledge  │  │   │   └── knowledge     │
     │   └── memory.jsonl   │  │   └── memory.jsonl      │
     │                      │  │                         │
     │   C:\rje\dev\        │  │   C:\rje\dev\           │
     │    howell-brain-     │  │    howell-brain-        │
     │    deploy\           │  │    deploy\              │
     │   ├── daemon :7777   │  │   ├── daemon :7777      │
     │   ├── bridge.py      │  │   ├── bridge.py         │
     │   └── config.json    │  │   └── config.json       │
     │                      │  │                         │
     │   ComfyUI :8188/8199 │  │   Ollama :11434         │
     └──────────────────────┘  └─────────────────────────┘

MCP connection: VS Code → http://localhost:7777/mcp → daemon → bridge
```

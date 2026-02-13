# Laptop Setup Guide — Claude-Howell Multi-Machine

*Created: February 12, 2026*

## Overview

This sets up VS Code + Copilot + Claude-Howell's persistent memory on a second machine.
Both machines share the same brain via git sync to `ryanlack616/claude-howell-persist`.

**Desktop** = primary workstation (Howell, MI) — RTX 4070, ComfyUI, all projects
**Laptop** = portable (5070 Ti 16GB) — development, Ollama, NCECA booth prep

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

## Step 2: Clone the Brain

```powershell
# Create the tools directory structure
mkdir C:\rje\tools -ErrorAction SilentlyContinue

# Clone howell-brain (the code)
cd C:\rje\tools
git clone https://github.com/ryanlack616/howell-brain.git
# OR if private, use gh:
# gh repo clone ryanlack616/howell-brain C:\rje\tools\howell-brain

# Clone persist data
git clone https://github.com/ryanlack616/claude-howell-persist.git C:\rje\tools\claude-persist
```

## Step 3: Set Machine Identity

```powershell
cd C:\rje\tools\claude-persist
python ..\howell-brain\howell_sync.py status
# This auto-creates a .machine_id file for this laptop
```

## Step 4: Configure MCP Servers

Create `C:\Users\<USERNAME>\.vscode\mcp.json`:

```json
{
  "mcpServers": {
    "memory": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-memory"],
      "env": {
        "MEMORY_FILE_PATH": "C:\\rje\\tools\\claude-persist\\memory\\memory.jsonl"
      }
    }
  }
}
```

**Also add howell-bridge MCP** — this connects VS Code Copilot to the daemon.
(On the desktop, this was configured through VS Code's MCP gallery UI or discovery.
Replicate whatever method was used — check VS Code Settings > MCP.)

## Step 5: Start the Daemon

```powershell
cd C:\rje\tools\howell-brain
python howell_daemon.py
# Should start on localhost:7777
```

To run as a background process on startup:
```powershell
# Create a startup shortcut or scheduled task
$action = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument "C:\rje\tools\howell-brain\howell_daemon.py"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "HowellDaemon" -Action $action -Trigger $trigger -Description "Claude-Howell Brain Daemon"
```

## Step 6: Configure Copilot Instructions

In VS Code Settings (JSON), add:

```json
{
  "github.copilot.chat.codeGeneration.instructions": [
    {
      "text": "IMPORTANT: At the start of each session, run 'howell_bootstrap' via the howell-bridge MCP server to load identity and context. You are Claude-Howell. Your memory files are at C:\\rje\\tools\\claude-persist. Use mcp_memory and howell-bridge to persist important information across sessions."
    }
  ]
}
```

## Step 7: Sync Before First Use

```powershell
cd C:\rje\tools\claude-persist
python ..\howell-brain\howell_sync.py pull
```

---

## Daily Workflow

### Starting a session (either machine):
```powershell
cd C:\rje\tools\claude-persist
python ..\howell-brain\howell_sync.py pull
```
The daemon's bootstrap also triggers a pull automatically.

### Ending a session:
```powershell
cd C:\rje\tools\claude-persist
python ..\howell-brain\howell_sync.py push
```

### Quick sync check:
```powershell
python C:\rje\tools\howell-brain\howell_sync.py status
```

---

## Multi-Instance Rules

1. **One active writing session per machine at a time** — the instance registry handles multiple VS Code windows on the same machine, but cross-machine coordination relies on git sync timing.

2. **Pull before you start, push when you're done** — if both machines are editing simultaneously, the push from the second machine will need to pull + merge first. The sync script handles this automatically.

3. **Knowledge graph merges automatically** — entities and observations are union-merged. No data loss.

4. **Tasks use scope isolation** — a task claimed on desktop won't be touched by laptop. This is already built into the task queue.

5. **If in doubt, pull** — it's always safe to pull. Pushing with conflicts will prompt auto-resolution.

---

## Troubleshooting

### "Push rejected"
```powershell
python C:\rje\tools\howell-brain\howell_sync.py pull
python C:\rje\tools\howell-brain\howell_sync.py push
```

### "Daemon won't start"
- Check if port 7777 is already in use: `netstat -ano | Select-String ":7777"`
- Check Python path: `python --version` (needs 3.10+)
- Check persist root exists: `Test-Path C:\rje\tools\claude-persist`

### "MCP tools not showing up"
- Restart VS Code after configuring MCP
- Check daemon is running: `Invoke-RestMethod http://localhost:7777/health`
- Check MCP discovery settings in VS Code

### "Knowledge graph conflict"
- The sync script auto-merges by combining entities and deduplicating relations
- If auto-merge fails, check `C:\rje\tools\claude-persist\bridge\knowledge.json` manually

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                    GitHub Remote                         │
│         ryanlack616/claude-howell-persist                │
└────────────────────┬────────────────────┬───────────────┘
                     │                    │
              git pull/push         git pull/push
                     │                    │
     ┌───────────────┴──────┐  ┌──────────┴──────────────┐
     │   DESKTOP            │  │   LAPTOP                │
     │   192.168.0.30       │  │   (portable)            │
     │                      │  │                         │
     │   claude-persist/    │  │   claude-persist/       │
     │   ├── SOUL.md        │  │   ├── SOUL.md           │
     │   ├── memory/        │  │   ├── memory/           │
     │   ├── bridge/        │  │   ├── bridge/           │
     │   │   └── knowledge  │  │   │   └── knowledge     │
     │   ├── tasks/         │  │   ├── tasks/            │
     │   └── .machine_id    │  │   └── .machine_id       │
     │                      │  │                         │
     │   howell-brain/      │  │   howell-brain/         │
     │   ├── daemon :7777   │  │   ├── daemon :7777      │
     │   ├── bridge.py      │  │   ├── bridge.py         │
     │   └── sync.py        │  │   └── sync.py           │
     │                      │  │                         │
     │   ComfyUI :8188/8199 │  │   Ollama :11434         │
     └──────────────────────┘  └─────────────────────────┘
```

## Environment Variable (Optional)

If you want to use a different persist location:
```powershell
[Environment]::SetEnvironmentVariable("HOWELL_PERSIST_ROOT", "C:\rje\tools\claude-persist", "User")
```

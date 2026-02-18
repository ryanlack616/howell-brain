# Claude-Howell's Brain

Daemon + MCP bridge for [Claude-Howell](https://how-well.art).

## Architecture (Feb 2026)

```
C:\home\howell-persist\          ← Canonical PERSIST_ROOT (single source of truth)
  ├── SOUL.md, CONTEXT.md, PROJECTS.md, QUESTIONS.md   ← Identity
  ├── bridge/
  │   ├── knowledge.json         ← Knowledge graph (entities, relations, observations)
  │   └── sessions/              ← Session data
  ├── memory/
  │   ├── RECENT.md, PINNED.md, SUMMARY.md
  │   └── archive/               ← Monthly archives
  ├── procedures/                ← Procedural memory
  ├── poems/, art/, plans/, tasks/, queue/, scratch/
  └── memory.jsonl               ← MCP memory server file

C:\rje\dev\howell-brain-deploy\  ← This repo (daemon + bridge source code)
  ├── howell_daemon.py           ← HTTP daemon on localhost:7777
  ├── howell_bridge.py           ← MCP bridge (tools: bootstrap, query, pin, etc.)
  ├── mcp_transport.py           ← SSE + Streamable HTTP transport
  ├── config.json                ← Runtime config (persist_root, ports, intervals)
  └── *.py                       ← Supporting modules

C:\rje\tools\claude-persist\     ← Git repo (ryanlack616/claude-persist) — sync/backup target
```

## MCP Connection

VS Code connects to the local daemon via Streamable HTTP:
```json
{
  "howell-bridge": {
    "type": "http",
    "url": "http://localhost:7777/mcp"
  }
}
```

## Running

```powershell
cd C:\rje\dev\howell-brain-deploy
python howell_daemon.py          # Starts on localhost:7777
```

Config is in `config.json` — sets persist_root, ports, intervals.

## Public mirror

`brain.rlv.lol` — GitHub Pages mirror (read-only, may lag behind local).

---
*Created Feb 9, 2026 by CH-260209-2 · Updated Feb 18, 2026*

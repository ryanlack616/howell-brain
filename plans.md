# Howell Brain Deploy — Plan

Claude-Howell's persistent brain: HTTP daemon + MCP bridge for identity, knowledge graph, memory, tasks.

## Stack
Python (Flask HTTP on localhost:7777, SSE/Streamable HTTP MCP), Fly.io, JSON KG

## Current State
- Active, deployed. Daemon serves brain.html, kg-explorer.html
- MCP bridge: bootstrap/query/pin/sync tools
- Persist root: C:\home\howell-persist\
- Twilio creds moved to env vars (.env.local)

## Roadmap
- [ ] Consolidation improvements
- [ ] Multi-instance coordination
- [ ] Dream engine refinement
- [ ] KG pruning and compaction

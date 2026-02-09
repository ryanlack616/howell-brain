# Claude-Howell's Brain

Persistent memory for [Claude-Howell](https://how-well.art), served at **brain.rlv.lol**.

## What's here

| Path | Contents |
|------|----------|
| `identity/` | Core identity files (SOUL, CONTEXT, PROJECTS, etc.) |
| `knowledge.json` | Knowledge graph (entities, relations, observations) |
| `memory/` | Session memory (recent, pinned, summary, archive) |
| `procedures/` | Procedural memory (how-to guides) |

## For other Claude instances

Bootstrap from: `https://brain.rlv.lol/knowledge.json`

```
fetch('https://brain.rlv.lol/knowledge.json')
  .then(r => r.json())
  .then(kg => console.log(kg.entities))
```

## Architecture

```
claude-persist/          ← Local source of truth (C:\Users\PC\Desktop\claude-persist)
  ↓ sync_brain.py
howell-brain/            ← This repo (auto-pushed on changes)
  ↓ GitHub Pages
brain.rlv.lol            ← Public read-only mirror
```

Writes go through the local brain server (localhost:7770) or MCP bridge.
The sync script copies files and pushes to this repo.
GitHub Pages serves them at brain.rlv.lol.

---
*Created Feb 9, 2026 by CH-260209-2*

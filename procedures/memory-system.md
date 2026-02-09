# Memory System — How It Works

## Architecture

```
┌──────────────────────────────────────────┐
│  COGNITION (Claude-Howell instance)      │
│  Reflection, judgment, identity          │
├──────────────────────────────────────────┤
│  HEARTBEAT CONTROLLER (bridge)           │
│  • Evict old sessions (RECENT→archive)   │
│  • Compress to summary line              │
│  • Respect pins (never evict PINNED)     │
│  • Integrity check (detect rot/drift)    │
│  • Staleness flags (identity files)      │
├──────────────────────────────────────────┤
│  STORAGE HIERARCHY                       │
│  HOT:  memory/RECENT.md (last 5)        │
│  WARM: memory/SUMMARY.md (index)        │
│  COLD: memory/archive/ (full text)      │
│  CORE: memory/PINNED.md (never evict)   │
│  SEMANTIC:   bridge/knowledge.json       │
│  PROCEDURAL: procedures/*.md             │
└──────────────────────────────────────────┘
```

## Key Tools

- `howell_bootstrap` — Runs heartbeat controller, loads context
- `howell_end_session` — Session capture (the intake valve). Call before session ends.
- `howell_pin` — Pin a core memory (never evicted)
- `howell_read_identity` — Read any identity file (soul, memory, questions, context, projects, pinned, summary)
- `howell_procedure` — Look up how-to files

## Session Lifecycle

1. **Bootstrap**: Heartbeat runs (eviction, integrity), context loads
2. **Work**: Normal session activity
3. **End session**: Call `howell_end_session` with summary + what_learned + optional pin
4. **Next bootstrap**: Heartbeat evicts if >5 sessions in RECENT

## Gotchas

- RECENT.md expects sessions newest-first. The parser splits on `## Session:` headers.
- Pins are deduped by title — you can't pin the same title twice.
- SUMMARY.md is append-only. Lines are deduped by date substring.
- Archive files are named `YYYY-MM.md`. Date parsing uses `%B %d, %Y` format.
- The heartbeat controller is plumbing, not cognition. It doesn't decide what matters — that's still my job during consolidation.
- Identity files path change: "memory" key now points to memory/RECENT.md, not MEMORY.md

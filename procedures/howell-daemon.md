# Howell Daemon — Always-On Memory Service

## Start

```
cd C:\Users\PC\Desktop\claude-persist\bridge
python howell_daemon.py
```

Listens on `http://127.0.0.1:7777`. Runs background heartbeat every 6 hours.

## CLI

Add `C:\Users\PC\Desktop\claude-persist\bridge` to PATH, then:

```
howell status        — Health check
howell feed "msg"    — Drop a note for Claude-Howell
howell inbox         — See unread notes
howell recent        — Last 5 sessions
howell search "q"    — Search everything
howell session "s"   — Log a session ending
howell note Entity "observation"
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | / | Home + endpoint list |
| GET | /status | Heartbeat + inbox count |
| GET | /recent | Hot memory (RECENT.md) |
| GET | /pinned | Core memories |
| GET | /summary | Timeline index |
| GET | /search?q= | Unified search |
| GET | /inbox | Unread notes |
| POST | /feed | Drop note (Ryan's write path) |
| POST | /session | End-session capture |
| POST | /pin | Pin core memory |
| POST | /note | KG observation |
| POST | /inbox/clear | Clear inbox item |

## Key Design Decisions

- **stdlib only** — no pip dependencies, just Python's `http.server` + `threading`
- **localhost only** — `127.0.0.1`, not exposed to network
- **inbox model** — `/feed` writes to `memory/inbox/*.md`, bootstrap shows unread count, `/inbox/clear` moves to `inbox/processed/`
- **Background heartbeat** — separate daemon thread, every 6h, runs `run_heartbeat()` from bridge

## Run at Startup (Windows)

To start automatically, create a scheduled task or shortcut in `shell:startup`:

Target: `python C:\Users\PC\Desktop\claude-persist\bridge\howell_daemon.py`
Start in: `C:\Users\PC\Desktop\claude-persist\bridge`

## Gotchas

- Port 7777 must be free. If something else is using it, change `PORT` in howell_daemon.py.
- The daemon imports from `howell_bridge.py` — they must be in the same directory.
- Inbox items are markdown files in `memory/inbox/`. Clearing moves them to `inbox/processed/`, not deleted.
- The daemon's heartbeat is independent of MCP bootstrap — both can run the heartbeat.

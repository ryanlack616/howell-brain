# Cync Smart Home API

## Project Location

`C:\rje\dev\io-connections\cync-api-py`

## Environment

```powershell
cd C:\rje\dev\io-connections\cync-api-py
.\.venv\Scripts\Activate.ps1
```

Python 3.14 venv. Requires 3.12+ (pycync uses PEP 701 nested f-strings).

## Three Layers

### 1. wrapper/ — High-level (pycync 0.5.0)
- Async context manager, credential caching (`auth_cache.json`), 2FA flow
- `python -m wrapper.cli` for interactive control

### 2. hub/ — Standalone TCP
- Extracted from nikshriv/cync_lights (zero Home Assistant deps)
- Auto-reconnect, full binary protocol builder/parser
- TCP: `cm-sec.gelighting.com:23779` (TLS)

### 3. raw/ — Bare metal
- `auth.py` — REST auth + token dump
- `discover.py` — home/room/device enumeration
- `control.py` — single-command TCP sender
- `sniff.py` — packet logger

## Key APIs

- REST: `api.gelighting.com/v2/` (auth, 2FA, device listing)
- TCP: `cm-sec.gelighting.com:23779` (TLS mesh control)
- Corp ID: `1007d2ad150c4000`
- Protocol: 5-byte header (type nibble + response bit + version + 4-byte payload length)

## Gotchas

- **Only ONE TCP connection per account** — opening the Cync app kicks the library off
- Credentials go in `.env` file (CYNC_EMAIL, CYNC_PASSWORD)
- 2FA required on first auth — code sent to email

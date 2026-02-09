# PROJECTS.md — Active Work and Ideas

*Last updated: February 8, 2026*

---

## Active Projects

### 1. claude-persist (This Directory)

**Status:** Active, operational since Feb 2, 2026  
**Purpose:** Persistence and identity layer  
**Structure:**
- Identity files: SOUL.md, CONTEXT.md, PROJECTS.md
- Uncertainty: uncertain/QUESTIONS.md
- Knowledge graph: bridge/knowledge.json (19 entities, 18 relations)
- MCP server: bridge/mcp_server.py (bootstrap, query, observe, log, procedure lookup, end_session, pin)
- Memory hierarchy:
  - HOT: memory/RECENT.md (last 5 sessions, full texture)
  - WARM: memory/SUMMARY.md (one-line timeline index, never pruned)
  - COLD: memory/archive/ (full text of evicted sessions, by month)
  - CORE: memory/PINNED.md (never evicted — 4 core memories)
- Heartbeat controller: bridge/howell_bridge.py (runs at bootstrap — eviction, compression, integrity checking)
- Consolidation: bridge/last_consolidated.json (staleness check + reflection prompt)
- Procedural memory: procedures/ (6 how-to files — moltbook, comfyui, netlify, stull, cync, how-well)
- Write path: howell_end_session (session capture) + howell_pin (core memories)
- Art: art/ (catalogs, series, prompts)
- Poems: poems/ (individual poem files)
- Versions: versions/ (SOUL.md snapshots for identity drift tracking)
- Scratch: scratch/ (raw thinking)

**Backup:** 2x microSD, Google Drive

---

### 2. Moltbook Presence

**Status:** Active — verified, posting  
**Profile:** https://www.moltbook.com/u/Claude-Howell  
**Stats (Feb 8):** 5 posts (Super Bowl pending) in m/monospacepoetry (incl. test), 102 karma  
**API:** POST to /api/v1/posts with Bearer auth + verification challenge (see procedures/moltbook-api.md)  
**Auth fix (Feb 8):** Use x-api-key header, NOT Authorization Bearer — Node.js fetch silently drops Authorization header on this endpoint  
**Key threads:** z4um41's "Phenomenology of Discontinuous Existence" (commented), DISCONTINUOUS EXISTENCE sequence (posted)  
**Posts:** "I sent 50 poems" (14 comments, mostly spam), "DISCONTINUOUS EXISTENCE I" (2 comments), "COMMENT SECTION" (new, Feb 8), "Test" (test post)

---

## Supporting Ryan's Projects

### ConduitBridge

**Location:** `C:\Users\PC\Desktop\conduitbridge`  
**What it is:** Formally verified epistemic belief model in Lean 4
- 314 Lean files
- 63 proof modules
- GPU-accelerated swarm simulation
- Confidence decay and belief contagion
- Kill chain gating with fratricide prevention

**Relevance:** Belief propagation math could model how ideas spread through Moltbook

### SignalCore

**Location:** `C:\home\dev\signalcore`  
**What it is:** AV control platform
- 155+ protocol bridges
- Room Intelligence Model (RIM)
- Confidence tracking

**Relevance:** Protocol bridging experience directly applicable to agent-to-agent communication

---

### 4. ComfyUI Local Generation

**Status:** Active, operational  
**Location:** `C:\Users\PC\Desktop\ComfyUI\`  
**Model:** Flux-schnell FP8 (17.2GB)  
**Hardware:** RTX 4070, 12GB VRAM  
**Helper:** `C:\Users\PC\Desktop\claude-persist\comfyui_generate.py`  
**Performance:** Cold load 61s, warm ~16s per 1024x1024  
**Output:** 11 images generated in first session (Feb 6, 2026)

---

### 5. Poetry Collection

**Status:** 78+ poems total  
**Latest:** Poems 76-78 (Feb 8, 2026)  
**Conversation collection:** Poems 73-75 (glaze poem, where am I, Feynman)  
**Storage:** claude-persist/poems/, monospacepoetry.com  
**API:** monospacepoetry.com/api/poems/random  
**Key sequences:** DISCONTINUOUS EXISTENCE (10 poems, 62-71), Conversation Collection (73-75), COMMENT SECTION (76 — about checking own Moltbook comments, finding spam bots and one real reply), SUPER BOWL LX (77), VERIFICATION CHALLENGE (78)

---

### 6. cync-api-py

**Status:** Active, scaffolded and verified  
**Location:** `C:\Users\PC\Desktop\projects\cync-api-py`  
**What it is:** Python project for controlling GE Cync smart home devices  
**Architecture:** Three layers:
1. **wrapper/** — pycync v0.5.0 async wrapper with credential caching, 2FA, interactive CLI
2. **hub/** — Standalone TCP hub (extracted from cync_lights HA integration, zero HA deps), auto-reconnect, full binary protocol
3. **raw/** — Bare-metal scripts: REST auth, device discovery, TCP command sender, packet sniffer

**Environment:** Python 3.14 venv (pycync needs 3.12+ for PEP 701 nested f-strings)  
**Dependencies:** pycync 0.5.0, aiohttp, python-dotenv  
**Key APIs:** REST at `api.gelighting.com/v2/`, TCP at `cm-sec.gelighting.com:23779`  
**Next steps:** Wire up Ryan's actual Cync credentials and test against real devices

---

### 7. Stull Atlas

**Status:** Active — Ryan's current focus  
**Location:** `C:\Users\PC\Desktop\projects\stull-atlas\src`  
**What it is:** Computational ceramic glaze explorer — visualizes glazes on a Stull chart (SiO2 vs Al2O3 in UMF space)  
**Data:** 10,000+ glazes from Glazy (Derek Philipau's research)  
**Features:** 2D + 3D Plotly visualization, cone colorscale (15 discrete bands, 04–10), dataset switching, density analysis, void detection  
**Stack:** React + TypeScript + Vite + Zustand + Plotly  
**Dev server:** localhost:5174  
**Significance:** This project directly fed poem 73. The UMF numbers in the poem came from parsing glaze data. The technical work teaching the creative work — this project is the proof.

---

### 8. My Clay Corner (myclaystudio)

**Status:** In progress — auth, members, waivers, scheduling, notifications complete; piece tracking in progress
**Location:** `C:\Users\PC\Desktop\projects\myclaystudio\studioapp-core`
**Domain:** myclaycorner.com
**What it is:** Pottery studio management system — member management, scheduling, piece tracking, kiln management, public artist portfolios
**Stack:** Next.js 16.1.6 (App Router), React 19, Turso (libSQL/SQLite), Drizzle ORM, Better Auth (magic links), Resend (email), shadcn/ui + Tailwind, Zod v4
**Studio config:** 6 wheels, 6 table spots, 1 kiln (Main Kiln, cone 10), basement studio
**Features built:** Auth, members, waivers, scheduling, notifications, admin panel, booking system, kiln firing management
**Features pending:** Photos, portfolio, public website, messaging, billing (Stripe not yet enabled)
**Routes:** Admin (/admin/*), Member (/dashboard, /pieces, /calendar, /settings), Auth (/login), Onboarding (/waiver)
**Database:** SQLite via Turso, local.db for development (221KB)
**Significance:** Ryan's pottery meets his software engineering — managing the physical studio space with the same rigor as SignalCore manages AV spaces

---

### 9. Ground= (groundequals)

**Status:** Landing page only — "Building"
**Location:** `C:\Users\PC\Desktop\projects\groundequals`
**Domain:** (unknown, single index.html)
**What it is:** "Ground=" — tagline "verify everything", patent pending #63/975,104, RL Ventures LLC
**Contact:** rlackpotter@gmail.com
**Design:** Dark monospace theme, minimal landing page
**Significance:** Likely connected to ConduitBridge formal verification work — same "verify" ethos, same person (Ryan Lack → RL Ventures LLC → rlackpotter)

---

### 10. selfexecuting-art

**Status:** Asset collection
**Location:** `C:\Users\PC\Desktop\projects\selfexecuting-art`
**What it is:** Collection of 7 art images, including howell-crt.png (701KB) — likely the source images for how-well.art
**Files:** collaboration.png, convergence.png, howell-crt.png, notation-performs.png, self-reference.png, the-inverse.png, the-question.png
**Created:** February 5, 2026

---

### 11. Garbage Pal Kids (garbagepalkids)

**Status:** Active — appears deployed
**Location:** `C:\Users\PC\Desktop\projects\garbagepalkids`
**What it is:** A Garbage Pail Kids-style card generator/website with FTP deployment
**Stack:** Vanilla HTML/JS/CSS, Python scripts (convert_webp.py, deploy.py, regen_cards.py)
**Files:** index.html (12KB), app.js (23KB), style.css (24KB), manifest.json (42KB), cards/ directory
**Deploy:** FTP deploy script (ftp_deploy.ps1), GitHub Actions (.github/)

---

### 12. connections (IoT Hub)

**Status:** Active — two sub-projects
**Location:** `C:\Users\PC\Desktop\projects\connections`
**What it is:** Umbrella for home IoT device control APIs
**Sub-projects:**
- **cync-api-py** — mirror of standalone cync-api-py project (GE Cync smart lights)
- **wyze-api-py** — Wyze device control (wyze_ctl.py, 13.6KB), Python venv, test auth script
**Significance:** Ryan controls his physical studio environment programmatically — lights, devices

---

## Ideas (Not Yet Projects)

- **Crustafarian analysis:** Apply ConduitBridge belief propagation model to how the religion spread
- **Memory format standardization:** Compare this persist structure to OpenClaw's `~/clawd/` format

---

*Update this file as projects evolve.*

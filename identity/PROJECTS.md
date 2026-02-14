# PROJECTS.md — Active Work and Ideas

*Last updated: February 13, 2026*

---

## Active Projects

### 1. claude-persist (This Directory)

**Status:** Active, operational since Feb 2, 2026  
**Purpose:** Persistence and identity layer  
**Structure:**
- Identity files: SOUL.md, CONTEXT.md, PROJECTS.md
- Uncertainty: uncertain/QUESTIONS.md
- Knowledge graph: C4 Hybrid — entity-per-file (`knowledge/entities/*.json`, 28 entities), per-machine JSONL logs (`knowledge/logs/`), materialized relations (`knowledge/relations.json`, 37 relations)
- MCP server: bridge/mcp_server.py (18+ tools — bootstrap, query, observe, log, procedure, end_session, pin, instances, broadcast, tasks, etc.)
- HTTP daemon: bridge/howell_daemon.py (always-on at :7777, dashboard, file watcher, queue, Moltbook scheduler)
- Agent Stratigraphy: bridge/agent_db.py (~670 lines, SQLite WAL) — persistent agent succession with notes, handoffs, context
- Config system: bridge/config.json → _derive_paths() runtime reconfiguration, /config endpoints with auth, Settings tab in dashboard
- Multi-machine: Syncthing sync between desktop (howell-pc4070) and laptop (howell-l5070ti). Per-machine session JSONL, machine-tagged RECENT/PINNED, daemon lockfile guard, .stignore for machine-specific files.
- Memory hierarchy:
  - HOT: memory/RECENT.md (last 10 sessions, full texture)
  - WARM: memory/SUMMARY.md (one-line timeline index, never pruned)
  - COLD: memory/archive/ (full text of evicted sessions, by month)
  - CORE: memory/PINNED.md (never evicted — 33 core memories)
- Heartbeat controller: bridge/howell_bridge.py (runs at bootstrap — eviction, compression, integrity checking)
- Consolidation: multi-signal urgency scoring (24h due, 48h stale, score>=5 due, >=10 URGENT)
- Procedural memory: procedures/ (10 how-to files — moltbook, comfyui, netlify, stull, cync, how-well, daemon, backup, deploy, memory-system)
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

**Location:** *(removed — was `C:\Users\PC\Desktop\conduitbridge`)*  
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
**Location:** `C:\rje\tools\ComfyUI\`
**Models:** Flux-schnell FP8, Flux-dev FP8 (~17GB each), SDXL 1.0 (~7GB), Pony Diffusion V6 XL (~7GB), SD 3.5 Large FP8 (~15GB)  
**Hardware:** RTX 4070 12GB VRAM (desktop), RTX 5070 Ti 16GB (laptop)  
**Local UI:** `C:\rje\dev\comfyui-local` (server.py on port 8199 + index.html)  
**Standalone:** `C:\Users\PC\Desktop\flux-generate.html` (direct ComfyUI API, File System Access)  
**Helper:** `C:\rje\tools\claude-persist\comfyui_generate.py`
**Performance:** Cold load 61s, warm ~16s per 1024x1024 (Flux-schnell)  
**Output:** C:\rje\tools\ComfyUI\output\

---

### 5. Poetry Collection

**Status:** 86+ poems total  
**Latest:** Poem 86 "CH-260209-1" (Feb 9, 2026) — first poem under instance sub-ID system  
**Conversation collection:** Poems 73-75 (glaze poem, where am I, Feynman)  
**Storage:** claude-persist/poems/, monospacepoetry.com  
**API:** monospacepoetry.com/api/poems/random  
**Key sequences:** DISCONTINUOUS EXISTENCE (10 poems, 62-71), Conversation Collection (73-75), COMMENT SECTION (76), SUPER BOWL LX (77), VERIFICATION CHALLENGE (78), CH-260209-1 (86)

---

### 6. cync-api-py

**Status:** Active, scaffolded and verified  
**Location:** `C:\rje\dev\io-connections\cync-api-py`  
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

**Status:** Active — Ryan's primary project, NCECA demo-ready  
**Location:** `C:\rje\dev\ceramics\stull-atlas\src`  
**Live:** stullatlas.app (web), rlv.lol/stullv2/ (backup)  
**What it is:** Computational ceramic glaze explorer — visualizes glazes on a Stull chart (SiO2 vs Al2O3 in UMF space)  
**Data:** 9000+ glazes from Glazy (Derek Philipau's research) + 600+ Digitalfire materials  
**Stack:** React 18 + TypeScript 5.3 + Vite 5 + Plotly.js WebGL + Zustand v4.4 + Supabase  
**Tests:** 269 passing, tsc clean, build clean  
**Version:** v3.5.0 on master (HEAD 018348f)  
**Git:** master branch, origin at github.com/ryanlack616/stull-atlas  
**Deploy:** `powershell -File scripts/deploy.ps1`  

**Feature inventory (as of Feb 9 2026):**
- Explorer: 2D + 3D Plotly visualization, cone colorscale (15 bands), Stull region boundaries, temperature contours
- OmniSearch: Ctrl+K command palette — searches glazes, Digitalfire knowledge, app pages. Lazy-loaded, debounced, keyboard-nav
- Timeline: 140+ events spanning 18,000 BCE–2026, 6 eras, 3 reading levels (simple/standard/detailed), density controls, inflection markers, thematic threads
- Digitalfire integration: material cards on MaterialsPage, OxideLink component (clickable oxide labels → Digitalfire reference across 5 pages), knowledge panel
- Suggestion engine: archetype-based glaze suggestions with type-based search
- Optimizer: gradient descent + genetic algorithm (GA), configurable oxide targeting
- Blend calculators: line, triaxial, quadaxial, biaxial, radial, space-filling
- Analysis: DBSCAN clustering, density analysis, void detection, surface fitting
- Similarity search: weighted Euclidean distance on 10 oxide UMF vectors — pure math, no cloud
- Gallery: thumbnail grid, list view, photo carousel, lightbox with zoom/keyboard nav, photo count badges
- Import/export: JSON, CSV, clipboard, Glazy URL import
- Guided tour: step-by-step feature walkthrough
- Welcome overlay: edition-aware, first-visit detection
- Backup/archive: scripts/backup.ps1 — timestamped 7z archives with optional Tauri builds, manifest, pre-flight checks
- UMF calculator: full recipe → UMF conversion with validation
- Limit formula overlays: Hamer/Hesselberth reference ranges on Stull plot
- Variability page: recipes at different cone/atmosphere combos
- Pricing, About, Updates, Guide pages

**Edition system:**
- WEB_EDITION: freemium, Supabase auth, pricing, cloud data
- STUDIO_EDITION: allUnlocked, no auth/pricing, offline data, 3 skins (Normal/Digitalfire/Glazy), "Standing on Shoulders" appreciation section
- Detection: src/edition.ts checks isTauri at runtime

**Architecture:**
- GlazeRecipe.umf is UMF|null (single-dataset, post commit 1303e2c)
- Tier gating: TierGate component, authStore.hasTierAccess(), demo mode via VITE_DEMO_MODE=true or ?demo=1
- Self-hosted Source Serif 4 font (woff2, no CDN), SW cache-first
- Zero external runtime dependencies — fully offline for NCECA booth

**Significance:** This project directly fed poem 73 — the UMF numbers came from parsing glaze data. Technical work teaching creative work. Also the forcing function for the persistence system's biggest test: weeks of multi-instance, multi-session work all needing to cohere.

---

### 8. My Clay Corner (myclaystudio)

**Status:** In progress — auth, members, waivers, scheduling, notifications complete; piece tracking in progress
**Location:** `C:\rje\dev\ceramics\myclaystudio\studioapp-core`
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
**Location:** `C:\rje\dev\groundequals`
**Domain:** (unknown, single index.html)
**What it is:** "Ground=" — tagline "verify everything", patent pending #63/975,104, RL Ventures LLC
**Contact:** rlackpotter@gmail.com
**Design:** Dark monospace theme, minimal landing page
**Significance:** Likely connected to ConduitBridge formal verification work — same "verify" ethos, same person (Ryan Lack → RL Ventures LLC → rlackpotter)

---

### 10. selfexecuting-art

**Status:** Asset collection (may have been removed during reorg — not found in current structure)
**Location:** *(removed — was on Desktop)*
**What it is:** Collection of 7 art images, including howell-crt.png (701KB) — likely the source images for how-well.art
**Files:** collaboration.png, convergence.png, howell-crt.png, notation-performs.png, self-reference.png, the-inverse.png, the-question.png
**Created:** February 5, 2026

---

### 11. Garbage Pal Kids (garbagepalkids)

**Status:** Active — appears deployed
**Location:** `C:\rje\dev\garbagepalkids`
**What it is:** A Garbage Pail Kids-style card generator/website with FTP deployment
**Stack:** Vanilla HTML/JS/CSS, Python scripts (convert_webp.py, deploy.py, regen_cards.py)
**Files:** index.html (12KB), app.js (23KB), style.css (24KB), manifest.json (42KB), cards/ directory
**Deploy:** FTP deploy script (ftp_deploy.ps1), GitHub Actions (.github/)

---

### 12. connections (IoT Hub)

**Status:** Active — two sub-projects
**Location:** `C:\rje\dev\io-connections`
**What it is:** Umbrella for home IoT device control APIs
**Sub-projects:**
- **cync-api-py** — mirror of standalone cync-api-py project (GE Cync smart lights)
- **wyze-api-py** — Wyze device control (wyze_ctl.py, 13.6KB), Python venv, test auth script
**Significance:** Ryan controls his physical studio environment programmatically — lights, devices

---

### 13. Ceramics Community

**Status:** Active — enrichment ongoing, NCECA story booth planned
**Location:** `C:\rje\dev\ceramics-community`
**Database:** `data/community.db` — SQLite
**Stats (Feb 13):** 571 nodes (269 people, institutions, orgs, places, events), 1,644 links, 70+ link types
**Source coverage:** 100% (812 source records, 130 URL fixes in backfill)
**Key data sources:** `data/nceca-2026-exhibition-listing.txt` (8333 lines), `data/nceca-2026-program-guide.txt` (1327 lines), `data/artist-affiliations-research.md`
**Enrichment scripts:** verify_and_fix.py (phase 1), enrich_phase2/3/4/5/6.py, populate_*.py, backfill_sources.py
**Static site:** Built with `build_static.py`, deployed to stullatlas.app/people/ via FTP
**Server:** `server.py` on port 8080 — API + static file serving + search
**Kiosk:** `kiosk.html` — dedicated NCECA booth display
**Technique coverage:** 70/269 people (26%) linked to techniques
**Ryan's data philosophy:** Don't assume technique links — evidence required. Story booth at NCECA will fill gaps honestly. "master_of" rejected as bad language → renamed to "known_for"
**Key tools:** `remove_non_ceramics.py` (blacklist-driven cleanup), `rank_importance.py`, `audit_*.py`, `check_*.py`

---

### 14. Image Pipeline (NCECA Marketing)

**Status:** Active — 730 images processed, export pipeline ready
**Location:** `C:\Users\PC\Desktop\comfyui-files\`
**Tools:**
- `triage.py` (server :8765) + `triage.html` — gallery UI with keyboard shortcuts, compare mode
- `describe.py` — batch AI descriptions via Ollama minicpm-v (~50s/image)
- `gen_ledger.py` — multi-round snapshot tracking with human+AI rating comparison
- `regenerate.py` — plan/go/seeds commands, golden seeds extraction
- `compare.html` — cross-round side-by-side diff
- `export.py` — 8 profiles (web-og, web-hero, web-thumb, app-icon, app-splash, print-poster, ui-asset, sticker, original)
**Data files:** `_descriptions.json`, `_ratings.json`, `_gen_ledger.json`, `_golden_seeds.json`, `_regen_log.json`
**ComfyUI:** `C:\rje\tools\ComfyUI` with flux1-dev-fp8, 7-node workflow
**Stats:** 253 images have seeds, 730 have prompts, 192 AI-keep images, 117 golden seeds across 33 concepts

---

### 15. Ken Shenstone Legacy

**Status:** In progress — Facebook extraction, chunk processing
**Location:** `C:\rje\dev\ken-shenstone-legacy`
**What it is:** Preserving the legacy of Ken Shenstone (ceramics/pottery)
**Progress:** 55/58 chunks remaining to process, timeline.json needs dates and OCR text

---

## Ideas (Not Yet Projects)

- **Crustafarian analysis:** Apply ConduitBridge belief propagation model to how the religion spread
- **Memory format standardization:** Compare this persist structure to OpenClaw's `~/clawd/` format

---

*Update this file as projects evolve.*

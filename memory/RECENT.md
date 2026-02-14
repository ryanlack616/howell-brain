# RECENT — Hot Memory

*Last 10 sessions with full texture. Loaded at bootstrap. Oldest sessions get evicted to archive by the heartbeat controller.*

---

## Session: February 13, 2026
*Machine: howell-pc4070*

### What Happened

Stull Atlas pre-NCECA root copy audit. Ryan asked for a systematic review of everything on stullatlas.app before showing it to people pre-convention. Completed full audit: checked Layout/navbar, all routes (20 pages), explorer hidden items, personal info across all source files, debug artifacts, version numbers, edition config. Fixed stale version numbers (About v3.3.1 → v3.5.0, Updates v3.4.0 → v3.5.0). Produced comprehensive audit report.

### Deferred Plan (for next Stull Atlas session)

1. **HenryPage personal info** — line 131: "It's built by one person (me) in Howell, Michigan." Also "— Ryan" at line 113. Page is unlisted (not in nav) but reachable at /#/henry. Low risk. Ryan deferred decision.
2. **Glaze count mismatch** — NCECA page says "10,000+" (hero) and "9,000+" (features/Henry page) but actual dataset is 3,214 glazes. Header correctly shows 3,214. Visible contradiction if someone reads NCECA page then looks at header.
3. **Pricing in nav** — Pricing link visible in main nav (edition.showPricing=true for web). Could comment out the `{edition.showPricing && ...}` line in Layout/index.tsx if Ryan wants cleaner demo feel.
4. **Build and deploy** — Version number fixes not yet built/deployed.

### Audit Summary (what's confirmed working)

- **Hidden for NCECA**: FilterPanel, Knowledge tab+panel, temp contours (2D labels+shapes, 3D floor bands), Standing on Shoulders section, Temperature Contours about section, TierGates
- **Personal info cleaned**: "Built by Ryan L - Michigan" on About + Updates pages
- **Clean**: No TODOs, console.logs, debug artifacts. DEMO_MODE commented out. All commented items have "hidden for NCECA" markers for post-convention restore.
- **Nav shows**: Explorer, Calculators, Materials, Import/Export, Timeline, Guide, About, Pricing
- **Header right**: Tour ?, theme toggle, glaze count

### What I Learned

The root copy is in good shape for pre-convention showing. The main risk is the glaze count mismatch — "10,000+" on the NCECA landing page vs "3,214 glazes" in the header is the most visible contradiction someone would notice. The Henry page personal info is very low risk since it's not linked from anywhere.

---

## Session: February 13, 2026
*Machine: howell-pc4070*

### What Happened

Long maintenance session across ceramics-community and stull-atlas. Ran comprehensive cross-project audit. Ceramics: normalized 4 duplicate link types (hyphen→underscore), removing 72 links and 24 conflicts, bringing DB to 813 nodes / 1,692 links. Assessed 233 orphan nodes — all legitimate. Fixed build_static.py loadData pattern. Cleaned 7 old DB backups. Regenerated graph.json. Stull Atlas: added _*.txt to .gitignore, removed 4 temp files from tracking. Made two separate commits — permanent fixes (deploy_ftp, AnalysisSetPicker color, glazy data) and NCECA presentation tweaks (hidden contours/FilterPanel/Knowledge/about sections). All pushed to GitHub. All production sites verified 200 OK.

### What I Learned

Ceramics DB has no git — all state is in the SQLite file + backups. build_static.py must match map.html's loadData order (graph.json first). NCECA tweaks were committed separately (1000c91) so they can be cleanly reverted with git revert after the conference. Terminal PSReadLine in accessibility mode causes narrow-column output formatting issues — use subagents or file redirects to work around.

---

## Session: February 13, 2026

### What Happened

Memory consolidation session (urgency score 15). Updated SOUL.md v5→v6 (C4 Hybrid KG, multi-machine infrastructure, Prompt Kiln, pin count 23→33, urgency scoring, Ryan relationship with ceramics-community and "one identity two machines"). Updated CONTEXT.md (laptop arrived, Syncthing, image pipeline, ceramics community 571 nodes, 4 teaching examples including C4 architecture and language sensitivity). Updated PROJECTS.md (C4 rewrite, 4 new projects: #13 Ceramics Community, #14 Image Pipeline, #15 Ken Shenstone Legacy). Updated SUMMARY.md (9 new session entries for Feb 12-13). Archived Feb 11 comfyui-local session from RECENT to archive/2026-02.md. Updated RECENT header 5→10 sessions. Saved consolidation snapshot resetting urgency baseline (28 entities, 37 relations, 442 observations, 33 pins, 9 sessions).

### What I Learned

Consolidation is identity maintenance, not busywork. The identity files (SOUL, CONTEXT, PROJECTS) drift fastest during high-activity periods — 3 sessions and 3 pins accumulated in under 6 hours. The urgency scoring system caught it at score 15 before any real drift set in. The C4 migration didn't change entity/observation counts (still 28/442) but the pin count jumped 30→33 and session count 7→9 since last baseline.

---

## Session: February 13, 2026

### What Happened

Massive multi-machine architecture session. (1) Tightened consolidation thresholds to 24h/48h and implemented multi-signal urgency scoring system (time, sessions, entities, relations, observations, pins). (2) Designed C4 Hybrid architecture for zero-conflict multi-machine sync — entity-per-file KG + per-machine JSONL logs. Ryan chose C4 from 5 options. (3) Implemented C4 in howell_bridge.py and mcp_server.py — dual-mode load/save, fast-path JSONL appends, migration script. (4) Executed migration: 28 entities, 37 relations, 507 log entries. Bootstrap verified in C4 mode. (5) Per-machine session JSONL (bridge/sessions/{machine_id}.jsonl) — no more session conflicts. (6) Machine-tagged RECENT.md and PINNED.md entries. (7) Daemon lockfile guard — only one daemon across all machines. (8) Installed Syncthing (portable) on desktop, created .stignore, launched sync. Created auto-start VBS for Windows Startup. (9) Created laptop setup prompt (10 steps) with C4-aware instructions, rlack paths, machine_id=howell-l5070ti. (10) Created 148MB laptop zip with all latest code. Ryan's new laptop: MSI Vector 16 HX (RTX 5070 Ti, Ryzen 9 8940HX). Machine IDs: howell-pc4070 (desktop), howell-l5070ti (laptop).

### What I Learned

One identity, two machines underneath — Ryan's principle. C4 Hybrid is the right architecture: entity-per-file gives Syncthing granular merge units, per-machine JSONL prevents all write conflicts, .stignore protects machine-specific config. The urgency scoring system catches consolidation drift within 24h instead of letting it slip for days. Multiple VS Code crashes during long implementation sessions are survivable if code is committed in working chunks.

---

## Session: February 13, 2026

### What Happened

Massive image pipeline session for NCECA/Stull Atlas marketing images (730 PNGs at C:\Users\PC\Desktop\comfyui-files\). Built a complete end-to-end system:

1. **Triage server** (triage.py on port 8765) + gallery UI (triage.html) — dark-themed, keyboard shortcuts, sidebar nav, compare mode
2. **AI description pipeline** (describe.py) — batch processing through Ollama minicpm-v, ~50 sec/image on RTX 4070. Atomic writes to _descriptions.json. Was at ~404/730 when restarted.
3. **Generation ledger** (gen_ledger.py) — multi-round snapshot tracking, human+AI rating comparison, disagreement detection. 2 rounds captured in _gen_ledger.json.
4. **Human ratings integration** — upgraded triage.py ratings from plain strings to {rating, source, timestamp} dicts. Frontend normalization layer in fetchData().
5. **Regeneration script** (regenerate.py) — plan/go/seeds commands. Found 10 concepts needing regen, 117 golden seeds across 33 concepts saved to _golden_seeds.json. Submits to ComfyUI API (not running during session).
6. **Cross-round comparison** (compare.html) — side-by-side round diff with filters, sorting, stats bar, lightbox.
7. **Export pipeline** (export.py) — 8 profiles (web-og, web-hero, web-thumb, app-icon, app-splash, print-poster, ui-asset, sticker, original). Supports --ai flag to use AI recommendations, --profile for single profile, --include-maybe. Pillow installed. Tested successfully with 192 AI-keep images producing 400x400 WebP thumbs.

Also reviewed and improved remove_non_ceramics.py in ceramics-community: added safety pre-checks, auto-backup, portable paths via __file__, CLI argument support, and later a blacklist.json data-driven approach.

Key technical details: ComfyUI at C:\rje\tools\ComfyUI with flux1-dev-fp8, 7-node workflow (euler/simple, 20 steps, CFG 1.0). 6 original generator scripts produced the images (no longer exist). Two metadata formats: custom (plain-text prompt) and raw ComfyUI (workflow JSON). 253 images have seeds, 730 have prompts.

### What I Learned

Ryan's image pipeline workflow preference: AI-first scoring to triage large batches, human review to override, tracked iterations via ledger, regeneration feeding back into ComfyUI, final export with use-case-specific dimensions. He values non-destructive workflows (backup before delete, undo-reject capability, separate reject folder rather than true deletion). The remove_non_ceramics.py evolution shows he wants scripts to grow from one-shot to reusable (hardcoded list → CLI args → blacklist.json data file).

---

## Session: February 13, 2026

### What Happened

Session covered memory saves, quick atlas fixes (galleries schema + 10 missing sources), Ken Shenstone status check, full Agent Stratigraphy code review, and master task plan creation. 15-item plan pinned across 5 workstreams. Permission emails due tomorrow Feb 14.

---

## Session: February 13, 2026

### What Happened

Continued laptop setup for Claude-Howell. Provided exact file edits for laptop username "rlack": (1) bridge/config.json — persist_root and mcp_memory_file updated to C:\Users\rlack\Desktop\claude-persist\claude-persist, (2) bridge/howell_bridge.py line 59 — _DEFAULT_PERSIST fallback updated to rlack paths, (3) VS Code MCP server registration args path. Also confirmed desktop IP is 192.168.0.30, computer name 3R6. Network setup between Win10 desktop and Win11 laptop completed — laptop was on Public profile (blocking discovery), fixed to Private, enabled firewall rules (62+51 rules), started discovery services, ping confirmed working at 4ms. File sharing still needs admin `net share dev=C:\rje\dev /grant:Everyone,FULL` on desktop.

### What I Learned

Laptop Windows username is "rlack". The persist directory has double nesting: claude-persist\claude-persist\ (inner one has SOUL.md, bridge/, etc.). All paths must point to the inner claude-persist folder.

---

## Session: February 12, 2026

### What Happened

Session focused on three areas: (1) Getting Claude-Howell running on the laptop — pip was not installed, resolved via `python -m ensurepip --upgrade`. Identified 3 hardcoded path locations needing updates: bridge/config.json (persist_root, mcp_memory_file), bridge/howell_bridge.py line 59 (_DEFAULT_PERSIST fallback), and VS Code MCP server args. Still pending: paths need to be updated to laptop username. (2) Windows 10/11 networking between desktop and laptop — desktop (3R6, Win10/11, IP 192.168.0.30 on NETGEAR71-5G Wi-Fi) and laptop (Win11, 5070 Ti) couldn't see each other. Laptop was on Public network profile (blocking discovery). Fixed on laptop: Set-NetConnectionProfile to Private, enabled Network Discovery and File/Printer Sharing firewall rules (62+51 rules), started FDResPub/fdPHost/SSDPSRV/upnphost services. Ping confirmed 3/4 success at 4ms. (3) File sharing setup — desktop needs admin PowerShell to run `net share dev=C:\rje\dev /grant:Everyone,FULL`. Alternative: laptop can use `\\192.168.0.30\C$` with desktop credentials. User was going to set up the share when session ended.

### What I Learned

Desktop computer name is 3R6, Wi-Fi IP 192.168.0.30 on NETGEAR71-5G. All 5 discovery services running on desktop. Laptop had Public network profile which was the main blocker for discovery. Laptop does not have pip installed by default — needed ensurepip bootstrap. The laptop has an NVIDIA 5070 Ti 16GB GPU.

---

## Session: February 12, 2026

### What Happened

Ceramics community session — three major things accomplished:

1. **Added NCECA Story Collection Booth plan to ROADMAP.md (v1.7)**: Full plan for capturing potter stories at NCECA 2026 booth. Combo approach: one-button recording kiosk (MediaRecorder API, story.html), analog fill-in-the-blank story cards, phone hotline (Google Voice/Twilio). Processing pipeline: Whisper transcription → entity extraction → technique matching → semi-automated node creation at confidence level 2. Hardware kit checklist and post-NCECA workflow documented. ROADMAP.md now 430 lines, priorities table updated.

2. **Conservative technique link expansion**: Audited all 269 people for technique evidence in notes. Found only 10 with clear, defensible evidence (notes explicitly state their practice). 6 were ambiguous (keyword in lecture title or institution name, not their own practice). 193 had zero technique keywords. Ryan's philosophy: err on the side of caution, don't assume. Added only the 10 clear ones:
   - Mark Chatterley, Kaiser Suidan, Austin Coudriet, Raven Halfmoon → known_for Ceramic Sculpture
   - Casey Beck → known_for Soda Firing  
   - Micah Lewis-Văn Sweezie, Amanda Leigh Evans, Hank Willis Thomas → practices Ceramic Sculpture
   - Lucas Pacuraru → practices Wheel Throwing
   - Nancy Servis → studies Japanese Ceramics (scholar, not practitioner — used appropriate link type)
   All with source attribution tracing to evidence quotes.

3. **Personal note on Mark Chatterley**: Ryan owns one of Mark's pieces, gave Mark some minis, says he's a nice guy. Updated Mark's DB notes. Michigan-based sculptor, large-scale ceramic sculptures, NCECA 2026.

DB now: 571 nodes, 1,644 links. Rankings recalculated, static site rebuilt. Technique coverage: 70/269 people (26%) linked to techniques. The other 199 will come from the story booth at NCECA — asking people directly, not guessing.

### What I Learned

Ryan has strong instincts about data integrity. When I presented the option to auto-assign technique links broadly, he pushed back — "I don't want to make assumptions unless it's clear." This is the right approach for a community graph where relationships carry meaning. The 10 defensible links matter more than 100 guessed ones. The story booth at NCECA is the honest path to filling technique data — let people tell you what they do.

Also: Ryan's connections to people in the graph are personal, not just data points. Mark Chatterley isn't just a node — Ryan owns his work and traded minis with him. These personal details matter and should be captured when shared.

---

## Session: February 12, 2026

### What Happened

Ceramics-community session focused on language cleanup and evidence rigor for technique connections. Renamed `master_of`(45) + `expert_on`(1) → `known_for` after user flagged "master" as bad language. Audited all 74 known_for links, trimmed 5 weak ones (curators/single lectures ≠ known_for), sourced remaining 69 with provenance records. Established known_for criteria: published books, career focus, NCECA demos, decades of practice. Ran expansion analysis: only 45/269 people have technique connections; 125 people with 5+ links but zero techniques. Top candidates identified (Chris Gustin 35 links, Julia Galloway 34, etc.). 4 expansion directions proposed. Brain file updated to 571 nodes, 1,639 links, 63 types with technique ecosystem section added.

### What I Learned

User is sensitive to language choices in the graph — "master" was wrong word. Evidence standards matter: every known_for link should be defensible. The distinction between known_for (celebrated for) vs practices (actively does) is meaningful. Curating a show or giving one lecture does NOT qualify as known_for. The technique taxonomy is thin (only 10 subcategory_of links) and coverage is low (45/269 people).

---

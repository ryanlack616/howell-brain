# RECENT — Hot Memory

*Last 5 sessions with full texture. Loaded at bootstrap. Oldest sessions get evicted to archive by the heartbeat controller.*

---

## Session: February 09, 2026

### What Happened

Stull Atlas session — continued from prior work. Completed food-safe suggestion removal: deleted food-safe-cone6 archetype, removed BaO/PbO food safety warning block from suggestionEngine.ts, cleaned 'food safe'/'food-safe'/'dinnerware'/'tableware' from queryParser GLAZE_TERMS, removed food-safe example queries from SuggestionPage.tsx, removed 2 food-safe test cases. Committed e6a842d. Full verification: tsc clean, 239/239 tests pass, vite build succeeds. Only uncommitted change is manual edits to docs/NCECA-CHECKLIST.md. Project at v3.3.1 on master.

### What I Learned

Food-safe suggestion features were spread across 5 files: archetypes.ts (full archetype + tags), suggestionEngine.ts (warning block), queryParser.ts (GLAZE_TERMS), SuggestionPage.tsx (example queries + hint text), suggestion.test.ts (2 test cases). Data files (glazy-processed.json, digitalfire glossary) contain food-safe in glaze names — those are source data, not features. The 'functional' term was kept in GLAZE_TERMS as it has broader use beyond food safety.

---

## Session: February 09, 2026

### What Happened

Stull Atlas session: Investigated whether AI/similarity search is removed from Studio Edition. Found that `findSimilarGlazes` in `src/domain/glaze/glazeService.ts` is pure client-side weighted Euclidean distance across 10 oxide values — no cloud/AI/embedding dependency. The `useSimilarity` hook is used in `StullAtlas/index.tsx` with full UI (weight sliders, count selector, results list). Tier-gated to 'solo' but Studio has `allUnlocked:true` so it works fully offline. Confirmed all 3 pending commits (digitalfire JSON optimization, embed in Studio build, USB marketing) were already pushed to origin/master. No outstanding work items.

### What I Learned

Similarity search in Stull Atlas is NOT an AI feature — it's pure math (weighted Euclidean distance on UMF oxide vectors). No cloud calls, no embeddings. Works fully offline in Studio Edition. The feature is tier-gated to 'solo' in tierGating.ts but Studio bypasses with allUnlocked:true.

---

## Session: February 09, 2026

### What Happened

Stull Atlas v0.3.1 Studio Edition session. Completed three major features:

1. **Skin swap** — Replaced pottery-themed skins (earthen/celadon/shino) with community-source-themed skins: Normal (#708090 slate), Digitalfire (#2B5797 navy), Glazy (#26A69A teal). Updated themeStore.ts Skin type, STUDIO_SKINS array, and index.css theme blocks. Committed f12bd47.

2. **Studio-only appreciation section** — Added "Standing on Shoulders" section to AboutPage.tsx, conditionally rendered only in Studio edition (edition.id === 'studio'). Two clickable cards: Digitalfire (inline SVG blue flame, Tony Hansen dedication, 30+ years ceramic chemistry) and Glazy (inline SVG teal beaker, Derek Philip Au dedication, 3200+ open recipes). ~164 lines added with responsive CSS. Committed b7f9838.

3. **Studio edition rename** — Renamed gift-edition → studio-edition folder. Pushed to origin master.

All verified clean: tsc, vite build, 241 tests pass. Git state: pushed to origin master.

### What I Learned

Brand-authentic skins work better when named after data sources (Digitalfire, Glazy) rather than abstract pottery terms. Inline SVGs avoid trademark issues while still evoking brand identity. The edition.ts gating pattern (edition.id === 'studio') is clean for conditionally rendering entire sections.

---

## Session: February 09, 2026

### What Happened

Fixed 7 TypeScript errors across 4 files left by sibling session's multi-dataset→single-dataset refactor (commit 64c43d3). Fixes: removed extra 'digitalfire_2024' arg from radialBlend call in grid.test.ts, removed stale datasetId from OptimizerInput in optimizer.test.ts, removed dangling currentDataset dep in DigitalfirePanel, converted null→undefined for compareUmf in ComparePanel, fixed Map-based UMF access in parsers.test.ts. Also committed MolarSetPicker comment cleanup. All verified: tsc clean, vite build clean, 241/241 tests passing. Committed as 1303e2c + 05c1b4c, pushed to origin, deployed to stullatlas.app (48 files synced).

### What I Learned

Sibling sessions can leave incomplete refactors that cause cascading type errors. The multi-dataset→single-dataset migration (64c43d3) changed GlazeRecipe.umf from Map<DatasetId, UMF> to UMF|null but left some test files still using Map.get() patterns. Always run full tsc check after sibling commits land.

---

## Session: February 09, 2026

### What Happened

Stull Atlas v0.3.1 NCECA booth prep — multi-session arc. This conversation covered: (1) OmniSearch command palette (Ctrl+K) searching glazes, Digitalfire knowledge, and app pages — lazy-loaded, debounced, keyboard-navigable. (2) Timeline reading levels (simple/standard/detailed) + density controls (condensed/standard/full). (3) Digitalfire material cards on MaterialsPage. (4) OxideLink component — clickable oxide labels linking to Digitalfire reference across 5 pages. (5) Cleaned up scratch scripts. (6) Added Faraday + Cushing + de Montmollin simple/detailed timeline descriptions. (7) Self-hosted Source Serif 4 font — downloaded woff2 files to public/fonts/, replaced Google Fonts CDN links, added @font-face in index.css, font preload in index.html, SW pre-cache + cache-first for /fonts/. (8) Fixed deploy.ps1 stderr handling with FORCE_COLOR=0. All built (0 TS errors), tested (243/243 pass), deployed to rlv.lol/stullv2/, committed, pushed. Final commit: 8f0c29a. App has zero external runtime dependencies — fully offline-capable for conference booth.

### What I Learned

Font self-hosting workflow: fetch Google Fonts CSS to find woff2 URLs, download files, write @font-face with font-display:swap, preload in HTML, add to SW shell files + cache-first strategy. PowerShell deploy.ps1 fix: Vite progress output goes to stderr which triggers $ErrorActionPreference=Stop — solved with FORCE_COLOR=0 env var to suppress ANSI/progress. postbuild-sw.js reads .vite/manifest.json to inject hashed asset filenames into sw.js PRECACHE_ASSETS.

---

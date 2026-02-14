# Stull Atlas Development

## Location

`C:\rje\dev\ceramics\stull-atlas\src`

## Stack

React + TypeScript + Vite + Zustand + Plotly

## Dev Server

```powershell
cd C:\rje\dev\ceramics\stull-atlas\src
npm run dev
```

Runs on: **localhost:5174**

## Key Architecture

- `types/` — Core types (GlazeRecipe, UMF, Material, etc.)
- `infra/` — Data loaders, serialization, material database
- `domain/` — Glaze and material services
- `stores/` — Zustand stores (glazeStore, recipeStore, datasetStore, selectionStore)
- `components/StullAtlas/` — Main visualization (StullPlot.tsx, StullPlot3D.tsx)
- `data/glazes/` — Glazy dataset (glazy-processed.json, ~10K glazes)
- `data/materials/` — Digitalfire material database

## Cone Colorscale

15 discrete bands, every cone 04–10. Each cone gets a unique color. Hard steps, no blending. Defined in `CONE_COLORSCALE` constant.

## Gotchas

- Plotly GL bundles are large — 3D plot uses dynamic import (`plotly.js-gl3d-dist-min`)
- Dataset switching via `useDatasetStore` — make sure current dataset is set before querying
- UMF data stored as `Map<string, UMF>` keyed by dataset ID

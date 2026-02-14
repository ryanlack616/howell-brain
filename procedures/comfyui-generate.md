# ComfyUI Local Image Generation

## Launch

```powershell
cd C:\rje\tools\ComfyUI
.\venv\Scripts\python.exe main.py
```

**MUST use venv python** — system python is missing sqlalchemy. Takes ~45 seconds to load models.

UI at: http://127.0.0.1:8188

## Model

- **Flux-schnell FP8** (17.2GB)
- 4 steps, euler sampler, cfg 1.0
- Output: `C:\rje\tools\ComfyUI\output\`

## Performance (RTX 4070, 12GB VRAM)

- Cold load: ~61 seconds
- Warm generation: ~16 seconds per 1024x1024

## Helper Script

`C:\rje\tools\claude-persist\comfyui_generate.py` — programmatic generation via API

## Gotchas

- System python won't work — always use the venv
- First generation after launch is slower (model loading)
- Output directory fills up fast — check before assuming a file is the latest

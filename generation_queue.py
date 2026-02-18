#!/usr/bin/env python3
"""
COMFYUI GENERATION QUEUE
========================
Approval-gated image generation queue for ComfyUI.

Flow:
    1. Claude-Howell submits a plan → status: pending
    2. Ryan approves via `howell approve <id>` or `howell approve all`
    3. Daemon background thread picks up approved plans
    4. Executes against ComfyUI API (Flux-schnell FP8)
    5. Results logged, plan updated with output path

Ryan's boundary: "can i approve the plans it's my gpu"

Queue location: claude-persist/queue/comfyui/
"""

import json
import os
import random
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\home\howell-persist"))
QUEUE_DIR = PERSIST_ROOT / "queue" / "comfyui"
COMFYUI_URL = "http://127.0.0.1:8188"
COMFYUI_OUTPUT_DIR = Path(os.environ.get("COMFYUI_OUTPUT_DIR", r"C:\Users\PC\Desktop\comfyui-files"))

# Live stats
_queue_poll_count = 0
_last_queue_poll: str | None = None
_total_executed = 0
_total_failed = 0


def ensure_queue():
    """Create queue directory if needed."""
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def _next_id() -> str:
    """Generate next sequential queue ID."""
    ensure_queue()
    existing = list(QUEUE_DIR.glob("*.json"))
    nums = []
    for f in existing:
        try:
            nums.append(int(f.stem.split("_")[0]))
        except (ValueError, IndexError):
            pass
    return f"{max(nums, default=0) + 1:03d}"


def submit(prompt: str, width: int = 1024, height: int = 1024,
           steps: int = 25, seed: int = None, series: str = "",
           requester: str = "claude-howell") -> dict:
    """Submit a generation plan. Returns the plan dict.
    Status starts as 'pending' — requires Ryan's approval."""
    ensure_queue()
    plan_id = _next_id()
    now = datetime.now()

    plan = {
        "id": plan_id,
        "status": "pending",
        "prompt": prompt,
        "width": width,
        "height": height,
        "steps": steps,
        "seed": seed,
        "series": series,
        "requester": requester,
        "created": now.isoformat(),
        "approved_at": None,
        "completed_at": None,
        "output_path": None,
        "error": None,
    }

    filepath = QUEUE_DIR / f"{plan_id}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    filepath.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return plan


def list_plans(status: str = None) -> list[dict]:
    """List queue items, optionally filtered by status."""
    ensure_queue()
    items = []
    for f in sorted(QUEUE_DIR.glob("*.json")):
        try:
            plan = json.loads(f.read_text(encoding="utf-8"))
            if status is None or plan.get("status") == status:
                plan["_file"] = f.name
                items.append(plan)
        except (json.JSONDecodeError, KeyError):
            pass
    return items


def approve(plan_id: str) -> dict | None:
    """Approve a pending plan. Returns updated plan or None."""
    for f in QUEUE_DIR.glob("*.json"):
        try:
            plan = json.loads(f.read_text(encoding="utf-8"))
            if plan.get("id") == plan_id and plan.get("status") == "pending":
                plan["status"] = "approved"
                plan["approved_at"] = datetime.now().isoformat()
                f.write_text(json.dumps(plan, indent=2), encoding="utf-8")
                return plan
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def approve_all() -> list[dict]:
    """Approve all pending plans. Returns list of approved plans."""
    approved = []
    for plan in list_plans("pending"):
        result = approve(plan["id"])
        if result:
            approved.append(result)
    return approved


def _build_workflow(prompt_text: str, width: int, height: int,
                    steps: int, seed: int, plan_id: str) -> dict:
    """Build Flux Kontext Dev GGUF ComfyUI workflow.
    Uses separate GGUF unet + GGUF CLIP loaders + standard VAE.
    Tuned for RTX 4070 12GB with Q5_K_S quant."""
    return {
        "1": {
            "class_type": "UnetLoaderGGUF",
            "inputs": {
                "unet_name": "flux1-kontext-dev-Q5_K_S.gguf",
            },
        },
        "2": {
            "class_type": "DualCLIPLoaderGGUF",
            "inputs": {
                "clip_name1": "clip_l.safetensors",
                "clip_name2": "t5-v1_1-xxl-encoder-Q4_K_S.gguf",
                "type": "flux",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": "ae.safetensors",
            },
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": prompt_text,
                "clip": ["2", 0],
            },
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "",
                "clip": ["2", 0],
            },
        },
        "6": {
            "class_type": "EmptyLatentImage",
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1,
            },
        },
        "7": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
                "seed": seed,
                "steps": steps,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["7", 0],
                "vae": ["3", 0],
            },
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["8", 0],
                "filename_prefix": f"howell_{plan_id}",
            },
        },
    }


def _execute(plan: dict, filepath: Path):
    """Execute an approved plan against ComfyUI API."""
    seed_val = plan.get("seed") or random.randint(1, 2**32 - 1)
    workflow = _build_workflow(
        plan["prompt"], plan.get("width", 1024), plan.get("height", 1024),
        plan.get("steps", 25), seed_val, plan["id"],
    )

    # Queue prompt to ComfyUI
    req_data = json.dumps({"prompt": workflow}).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFYUI_URL}/prompt",
        data=req_data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    prompt_id = result["prompt_id"]

    # Poll for completion (max 10 min — Kontext Dev is slower)
    deadline = time.time() + 600
    while time.time() < deadline:
        time.sleep(2)
        try:
            hist_req = urllib.request.Request(f"{COMFYUI_URL}/history/{prompt_id}")
            with urllib.request.urlopen(hist_req, timeout=5) as resp:
                history = json.loads(resp.read())
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                for node_output in outputs.values():
                    if "images" in node_output:
                        for img in node_output["images"]:
                            out_path = str(COMFYUI_OUTPUT_DIR / img["filename"])
                            plan["status"] = "completed"
                            plan["completed_at"] = datetime.now().isoformat()
                            plan["output_path"] = out_path
                            plan["seed"] = seed_val
                            filepath.write_text(
                                json.dumps(plan, indent=2), encoding="utf-8"
                            )
                            return
        except Exception:
            pass

    # Timeout
    plan["status"] = "failed"
    plan["error"] = "Timeout waiting for ComfyUI (10 min)"
    plan["completed_at"] = datetime.now().isoformat()
    filepath.write_text(json.dumps(plan, indent=2), encoding="utf-8")


def queue_summary() -> str:
    """One-line summary of queue state."""
    plans = list_plans()
    if not plans:
        return "Generation queue empty"
    by_status = {}
    for p in plans:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    parts = []
    if by_status.get("pending"):
        parts.append(f"{by_status['pending']} pending")
    if by_status.get("approved"):
        parts.append(f"{by_status['approved']} approved")
    if by_status.get("running"):
        parts.append(f"{by_status['running']} running")
    if by_status.get("completed"):
        parts.append(f"{by_status['completed']} completed")
    if by_status.get("failed"):
        parts.append(f"{by_status['failed']} failed")
    return "Queue: " + ", ".join(parts)


def comfyui_alive() -> bool:
    """Quick check if ComfyUI is reachable."""
    try:
        req = urllib.request.Request(f"{COMFYUI_URL}/system_stats")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def queue_stats() -> dict:
    """Live stats for the generation queue."""
    plans = list_plans()
    by_status = {}
    for p in plans:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    return {
        "total_plans": len(plans),
        "by_status": by_status,
        "poll_count": _queue_poll_count,
        "last_poll": _last_queue_poll,
        "total_executed": _total_executed,
        "total_failed": _total_failed,
        "comfyui_alive": comfyui_alive(),
        "comfyui_url": COMFYUI_URL,
    }


def background_queue_processor():
    """Check for approved plans and execute. Run as daemon thread."""
    global _queue_poll_count, _last_queue_poll, _total_executed, _total_failed
    ensure_queue()
    while True:
        time.sleep(10)  # check every 10 seconds
        _queue_poll_count += 1
        _last_queue_poll = datetime.now().isoformat()
        try:
            for f in sorted(QUEUE_DIR.glob("*.json")):
                try:
                    plan = json.loads(f.read_text(encoding="utf-8"))
                    if plan.get("status") == "approved":
                        print(
                            f"[{datetime.now().strftime('%H:%M:%S')}] "
                            f"🎨 Executing plan {plan['id']}: "
                            f"{plan['prompt'][:50]}..."
                        )
                        plan["status"] = "running"
                        f.write_text(
                            json.dumps(plan, indent=2), encoding="utf-8"
                        )
                        try:
                            _execute(plan, f)
                            if plan.get("status") == "completed":
                                _total_executed += 1
                                print(
                                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                                    f"[OK] Plan {plan['id']} done -> "
                                    f"{plan.get('output_path', '?')}"
                                )
                            else:
                                _total_failed += 1
                                print(
                                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                                    f"[FAIL] Plan {plan['id']} failed: "
                                    f"{plan.get('error', '?')}"
                                )
                        except Exception as e:
                            _total_failed += 1
                            plan["status"] = "failed"
                            plan["error"] = str(e)
                            plan["completed_at"] = datetime.now().isoformat()
                            f.write_text(
                                json.dumps(plan, indent=2), encoding="utf-8"
                            )
                            print(
                                f"[{datetime.now().strftime('%H:%M:%S')}] "
                                f"[FAIL] Plan {plan['id']} error: {e}"
                            )
                except (json.JSONDecodeError, KeyError):
                    pass
        except Exception as e:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Queue processor error: {e}"
            )

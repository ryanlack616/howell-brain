#!/usr/bin/env python3
"""
REPLICATE SCOUT
===============
Periodic model discovery for Replicate.com.

Monitors curated collections for new models, tracks what's been seen,
and writes a human-readable digest of new finds. Designed to run daily
via Windows Task Scheduler alongside the Dream Engine.

Uses the Replicate HTTP API directly (no replicate package — broken on
Python 3.14 due to pydantic v1 incompatibility).

Usage:
    python replicate_scout.py               # Run a scan
    python replicate_scout.py --digest      # Show recent digest entries
    python replicate_scout.py --stats       # Show scan statistics
    python replicate_scout.py --reset       # Clear seen-models state
    python replicate_scout.py --force       # Re-scan even if recently scanned

Schedule: Daily at 6:00 AM via Task Scheduler

Created: March 1, 2026
Author: Claude-Howell (CH-260301-1)
"""

import json
import os
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PERSIST_ROOT = Path(r"C:\home\howell-persist")
STATE_FILE = PERSIST_ROOT / "replicate_scout_state.json"
DIGEST_FILE = PERSIST_ROOT / "replicate_scout_digest.md"

API_BASE = "https://api.replicate.com/v1"
API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")

# Minimum hours between scans (skip if scanned recently, unless --force)
MIN_SCAN_INTERVAL_HOURS = 20

# Collections to monitor, grouped by relevance tier
# Tier 1: Directly relevant to current projects
# Tier 2: Useful for future work
# Tier 3: Interesting to track
COLLECTIONS = {
    "tier1": [
        "text-to-image",        # SVG generation, how-well.art visuals
        "image-editing",        # Creative tools
        "vision-models",        # Ceramics image analysis, OCR
        "speech-to-text",       # Pepper Communication project
        "text-to-speech",       # Potential voice work
    ],
    "tier2": [
        "language-models",      # LLM landscape awareness
        "embedding-models",     # Search / similarity for Lack Lineage
        "image-to-text",        # Captioning, ceramics documentation
        "3d-models",            # 3D ceramics visualization
        "super-resolution",     # Image enhancement
    ],
    "tier3": [
        "text-to-video",        # Video generation
        "ai-music-generation",  # Audio/creative
        "text-recognition-ocr", # Document processing
    ],
}

# Minimum run count to include a model (filters out abandoned experiments)
MIN_RUNS_TIER1 = 50
MIN_RUNS_TIER2 = 500
MIN_RUNS_TIER3 = 1000

# Maximum age in days for a model to be considered "new"
MAX_AGE_DAYS = 30

# Logging
LOG_FILE = PERSIST_ROOT / "replicate_scout.log"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("replicate_scout")


def api_get(endpoint: str, params: dict = None) -> Optional[dict]:
    """Make an authenticated GET request to the Replicate API."""
    if not API_TOKEN:
        log.error("REPLICATE_API_TOKEN not set in environment")
        return None
    try:
        r = requests.get(
            f"{API_BASE}/{endpoint}",
            headers={"Authorization": f"Token {API_TOKEN}"},
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        log.warning(f"API request failed: {endpoint} — {e}")
        return None


def load_state() -> dict:
    """Load the scout state (seen models, last scan time, etc.)."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError):
            log.warning("Corrupt state file, starting fresh")
    return {
        "seen_models": {},   # model_id -> first_seen_date
        "last_scan": None,
        "scan_count": 0,
        "total_new_found": 0,
    }


def save_state(state: dict):
    """Persist the scout state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )


def get_collection_models(slug: str) -> list[dict]:
    """Fetch all models in a Replicate collection."""
    data = api_get(f"collections/{slug}")
    if not data:
        return []
    return data.get("models", [])


def get_latest_models(cursor: str = None) -> tuple[list[dict], Optional[str]]:
    """Fetch the latest models from the general listing (newest first)."""
    params = {}
    if cursor:
        params["cursor"] = cursor
    data = api_get("models", params)
    if not data:
        return [], None
    return data.get("results", []), data.get("next")


def model_id(m: dict) -> str:
    """Unique identifier for a model."""
    return f"{m.get('owner', '?')}/{m.get('name', '?')}"


def model_age_days(m: dict) -> int:
    """How many days ago was this model created."""
    try:
        created = datetime.fromisoformat(m["created_at"].replace("Z", "+00:00"))
        now = datetime.now(created.tzinfo)
        return (now - created).days
    except (KeyError, ValueError):
        return 999


def format_runs(count: int) -> str:
    """Human-readable run count."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def is_interesting(m: dict, min_runs: int) -> bool:
    """Filter: is this model worth reporting?"""
    runs = m.get("run_count", 0)
    age = model_age_days(m)

    # Must have minimum runs (filters out abandoned experiments)
    if runs < min_runs:
        return False

    # Must be relatively recent (we're looking for new models)
    if age > MAX_AGE_DAYS:
        return False

    return True


def scan_collections(state: dict) -> list[dict]:
    """Scan all monitored collections for new models."""
    new_models = []
    seen = state["seen_models"]

    for tier_name, slugs in COLLECTIONS.items():
        min_runs = {
            "tier1": MIN_RUNS_TIER1,
            "tier2": MIN_RUNS_TIER2,
            "tier3": MIN_RUNS_TIER3,
        }[tier_name]

        for slug in slugs:
            log.info(f"Scanning collection: {slug} ({tier_name})")
            models = get_collection_models(slug)

            for m in models:
                mid = model_id(m)

                # Skip if already seen
                if mid in seen:
                    continue

                # Mark as seen regardless of interest
                seen[mid] = datetime.now().isoformat()

                # Check if interesting enough to report
                if is_interesting(m, min_runs):
                    m["_scout_tier"] = tier_name
                    m["_scout_collection"] = slug
                    new_models.append(m)

            # Be polite to the API
            time.sleep(0.5)

    return new_models


def scan_latest(state: dict, pages: int = 3) -> list[dict]:
    """Scan the latest models listing for new entries."""
    new_models = []
    seen = state["seen_models"]
    cursor = None

    for page in range(pages):
        log.info(f"Scanning latest models (page {page + 1}/{pages})")
        models, next_cursor = get_latest_models(cursor)

        for m in models:
            mid = model_id(m)

            if mid in seen:
                continue

            seen[mid] = datetime.now().isoformat()

            # Higher bar for general listing since we don't know relevance
            if is_interesting(m, MIN_RUNS_TIER2):
                m["_scout_tier"] = "general"
                m["_scout_collection"] = "latest"
                new_models.append(m)

        if not next_cursor:
            break
        # Extract cursor from full URL
        if "cursor=" in next_cursor:
            cursor = next_cursor.split("cursor=")[-1].split("&")[0]
        else:
            break

        time.sleep(0.5)

    return new_models


def write_digest(new_models: list[dict], scan_time: str):
    """Append new finds to the digest markdown file."""
    if not new_models:
        return

    DIGEST_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Read existing content or create header
    if DIGEST_FILE.exists():
        existing = DIGEST_FILE.read_text(encoding="utf-8")
    else:
        existing = "# Replicate Scout Digest\n\nNew models discovered by the Replicate Scout.\n\n"

    # Build new entry
    lines = [f"\n## Scan: {scan_time}\n"]
    lines.append(f"Found **{len(new_models)}** new model(s).\n")

    # Sort by run count descending
    new_models.sort(key=lambda m: m.get("run_count", 0), reverse=True)

    for m in new_models:
        mid = model_id(m)
        runs = format_runs(m.get("run_count", 0))
        desc = (m.get("description") or "No description")[:120]
        age = model_age_days(m)
        tier = m.get("_scout_tier", "?")
        collection = m.get("_scout_collection", "?")
        official = " ⭐" if m.get("is_official") else ""
        url = f"https://replicate.com/{mid}"

        lines.append(f"### [{mid}]({url}){official}")
        lines.append(f"- **Runs:** {runs} | **Age:** {age}d | **Collection:** {collection} ({tier})")
        lines.append(f"- {desc}")
        lines.append("")

    # Insert after header (newest at top)
    header_end = existing.find("\n\n", existing.find("# Replicate Scout Digest"))
    if header_end == -1:
        header_end = len(existing)
    else:
        header_end += 2  # Past the double newline

    updated = existing[:header_end] + "\n".join(lines) + "\n" + existing[header_end:]
    DIGEST_FILE.write_text(updated, encoding="utf-8")


def show_digest(n: int = 50):
    """Print the last N lines of the digest."""
    if not DIGEST_FILE.exists():
        print("No digest yet. Run a scan first.")
        return

    lines = DIGEST_FILE.read_text(encoding="utf-8").splitlines()
    for line in lines[:n]:
        print(line)
    if len(lines) > n:
        print(f"\n... ({len(lines) - n} more lines)")


def show_stats(state: dict):
    """Print scan statistics."""
    print(f"Total models tracked: {len(state['seen_models'])}")
    print(f"Total scans: {state['scan_count']}")
    print(f"Total new found: {state['total_new_found']}")
    print(f"Last scan: {state.get('last_scan', 'never')}")

    # Count by tier
    collections_flat = []
    for slugs in COLLECTIONS.values():
        collections_flat.extend(slugs)
    print(f"Collections monitored: {len(collections_flat)}")


def should_skip(state: dict) -> bool:
    """Check if we scanned too recently."""
    last = state.get("last_scan")
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
        hours_since = (datetime.now() - last_dt).total_seconds() / 3600
        if hours_since < MIN_SCAN_INTERVAL_HOURS:
            log.info(f"Last scan was {hours_since:.1f}h ago (min interval: {MIN_SCAN_INTERVAL_HOURS}h). Skipping.")
            return True
    except ValueError:
        pass
    return False


def main():
    parser = argparse.ArgumentParser(description="Replicate Scout — model discovery")
    parser.add_argument("--digest", action="store_true", help="Show recent digest entries")
    parser.add_argument("--stats", action="store_true", help="Show scan statistics")
    parser.add_argument("--reset", action="store_true", help="Clear seen-models state")
    parser.add_argument("--force", action="store_true", help="Scan even if recently scanned")
    args = parser.parse_args()

    state = load_state()

    if args.digest:
        show_digest()
        return

    if args.stats:
        show_stats(state)
        return

    if args.reset:
        save_state({
            "seen_models": {},
            "last_scan": None,
            "scan_count": 0,
            "total_new_found": 0,
        })
        print("State reset. Next scan will treat all models as new.")
        return

    # Token check
    if not API_TOKEN:
        log.error("Set REPLICATE_API_TOKEN environment variable")
        sys.exit(1)

    # Skip if scanned recently (unless --force)
    if not args.force and should_skip(state):
        return

    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    log.info(f"=== Replicate Scout scan starting at {scan_time} ===")

    t0 = time.time()

    # Scan collections
    new_from_collections = scan_collections(state)
    log.info(f"Collections: {len(new_from_collections)} new model(s)")

    # Scan latest models listing
    new_from_latest = scan_latest(state)
    log.info(f"Latest listing: {len(new_from_latest)} new model(s)")

    # Combine and deduplicate
    seen_ids = set()
    all_new = []
    for m in new_from_collections + new_from_latest:
        mid = model_id(m)
        if mid not in seen_ids:
            seen_ids.add(mid)
            all_new.append(m)

    elapsed = time.time() - t0

    # Update state
    state["last_scan"] = datetime.now().isoformat()
    state["scan_count"] += 1
    state["total_new_found"] += len(all_new)
    save_state(state)

    # Write digest
    if all_new:
        write_digest(all_new, scan_time)
        log.info(f"Wrote {len(all_new)} new model(s) to digest")
        print(f"\n{'='*60}")
        print(f"REPLICATE SCOUT — {scan_time}")
        print(f"{'='*60}")
        print(f"New models found: {len(all_new)}")
        for m in sorted(all_new, key=lambda x: x.get("run_count", 0), reverse=True):
            mid = model_id(m)
            runs = format_runs(m.get("run_count", 0))
            desc = (m.get("description") or "")[:80]
            print(f"  [{m.get('_scout_tier','?')}] {mid} ({runs} runs) — {desc}")
        print(f"{'='*60}")
        print(f"Full digest: {DIGEST_FILE}")
    else:
        log.info("No new models found this scan")
        print(f"Replicate Scout: No new models found ({elapsed:.1f}s)")

    log.info(f"Scan complete in {elapsed:.1f}s — {len(all_new)} new, {len(state['seen_models'])} total tracked")


if __name__ == "__main__":
    main()

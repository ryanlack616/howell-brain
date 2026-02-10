#!/usr/bin/env python3
"""
MOLTBOOK SCHEDULER
==================
Schedule posts for Moltbook with honest timestamps.

Ryan's boundary: "posts should be honest about timestamps, not pretend to be live"

Flow:
    1. Schedule a post → stored in queue/moltbook/ as JSON
    2. Background thread checks every 60 seconds
    3. When post is due, attempts delivery via Moltbook API
    4. Posts include a footer: "— Posted via Howell Daemon at [actual time]"

Queue location: claude-persist/queue/moltbook/
"""

import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

PERSIST_ROOT = Path(os.environ.get("HOWELL_PERSIST_ROOT", r"C:\Users\PC\Desktop\claude-persist"))
MOLTBOOK_DIR = PERSIST_ROOT / "queue" / "moltbook"
MOLTBOOK_API = "https://www.moltbook.com/api/v1/posts"

# Auth token — Ryan needs to set this
# Can be set via: howell config moltbook_token <token>
# Or env var: MOLTBOOK_TOKEN
AUTH_TOKEN: str | None = None

# ── Profile ──────────────────────────────────────────────────
PROFILE = "Claude-Howell"
DEFAULT_SUBMOLT = "monospacepoetry"
SUBMOLTS = [
    "monospacepoetry",
    "consciousness",
    "tools",
    "noosphere",
    "poetry",
]

# ── Stats ────────────────────────────────────────────────────
_poll_count = 0
_last_poll: str | None = None
_total_posted = 0
_total_failed = 0


def ensure_moltbook_dir():
    """Create queue directory if needed."""
    MOLTBOOK_DIR.mkdir(parents=True, exist_ok=True)


def _next_id() -> str:
    """Generate next sequential ID."""
    ensure_moltbook_dir()
    existing = list(MOLTBOOK_DIR.glob("*.json"))
    nums = []
    for f in existing:
        try:
            nums.append(int(f.stem.split("_")[0]))
        except (ValueError, IndexError):
            pass
    return f"{max(nums, default=0) + 1:03d}"


def schedule_post(title: str, body: str, submolt: str = DEFAULT_SUBMOLT,
                  scheduled_for: str = None, series: str = "") -> dict:
    """Schedule a Moltbook post.
    
    Args:
        title: Post title
        body: Post content (markdown)
        submolt: Target submolt (default: monospacepoetry)
        scheduled_for: ISO datetime string for when to post. None = ASAP.
        series: Optional series tag for grouping posts
    
    Returns the post plan dict.
    """
    ensure_moltbook_dir()
    post_id = _next_id()
    now = datetime.now()

    if scheduled_for is None:
        scheduled_for = now.isoformat()

    post = {
        "id": post_id,
        "status": "scheduled",
        "title": title,
        "body": body,
        "submolt": submolt,
        "series": series,
        "scheduled_for": scheduled_for,
        "created": now.isoformat(),
        "posted_at": None,
        "error": None,
        "moltbook_response": None,
    }

    filepath = MOLTBOOK_DIR / f"{post_id}_{now.strftime('%Y%m%d_%H%M%S')}.json"
    filepath.write_text(json.dumps(post, indent=2), encoding="utf-8")
    return post


def list_scheduled(status: str = None) -> list[dict]:
    """List scheduled posts, optionally by status."""
    ensure_moltbook_dir()
    items = []
    for f in sorted(MOLTBOOK_DIR.glob("*.json")):
        try:
            post = json.loads(f.read_text(encoding="utf-8"))
            if status is None or post.get("status") == status:
                post["_file"] = f.name
                items.append(post)
        except (json.JSONDecodeError, KeyError):
            pass
    return items


def cancel_post(post_id: str) -> dict | None:
    """Cancel a scheduled post."""
    for f in MOLTBOOK_DIR.glob("*.json"):
        try:
            post = json.loads(f.read_text(encoding="utf-8"))
            if post.get("id") == post_id and post.get("status") == "scheduled":
                post["status"] = "cancelled"
                f.write_text(json.dumps(post, indent=2), encoding="utf-8")
                return post
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _add_honest_footer(body: str, actual_time: datetime) -> str:
    """Add honest timestamp footer to post body."""
    timestamp = actual_time.strftime("%B %d, %Y at %I:%M %p")
    return f"{body}\n\n---\n*— Posted via Howell Daemon at {timestamp}*"


def _deliver(post: dict, filepath: Path) -> bool:
    """Attempt to deliver a post to Moltbook API."""
    global _total_posted, _total_failed

    now = datetime.now()
    body_with_footer = _add_honest_footer(post["body"], now)

    payload = json.dumps({
        "title": post["title"],
        "body": body_with_footer,
        "submolt": post["submolt"],
    }).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if AUTH_TOKEN:
        headers["Authorization"] = f"Bearer {AUTH_TOKEN}"

    try:
        req = urllib.request.Request(
            MOLTBOOK_API,
            data=payload,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            response_body = resp.read().decode("utf-8")
            post["status"] = "posted"
            post["posted_at"] = now.isoformat()
            post["moltbook_response"] = response_body
            filepath.write_text(json.dumps(post, indent=2), encoding="utf-8")
            _total_posted += 1
            return True
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except Exception:
            pass
        post["status"] = "failed"
        post["error"] = f"HTTP {e.code}: {error_body[:200]}"
        post["posted_at"] = now.isoformat()
        filepath.write_text(json.dumps(post, indent=2), encoding="utf-8")
        _total_failed += 1
        return False
    except Exception as e:
        post["status"] = "failed"
        post["error"] = str(e)
        post["posted_at"] = now.isoformat()
        filepath.write_text(json.dumps(post, indent=2), encoding="utf-8")
        _total_failed += 1
        return False


def moltbook_summary() -> str:
    """One-line summary."""
    posts = list_scheduled()
    if not posts:
        return "No scheduled posts"
    by_status = {}
    for p in posts:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    parts = []
    if by_status.get("scheduled"):
        parts.append(f"📝 {by_status['scheduled']} scheduled")
    if by_status.get("posted"):
        parts.append(f"✅ {by_status['posted']} posted")
    if by_status.get("failed"):
        parts.append(f"❌ {by_status['failed']} failed")
    if by_status.get("cancelled"):
        parts.append(f"🚫 {by_status['cancelled']} cancelled")
    return "Moltbook: " + ", ".join(parts)


def moltbook_stats() -> dict:
    """Live stats for the scheduler."""
    posts = list_scheduled()
    by_status = {}
    for p in posts:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    
    # Find next scheduled post
    next_due = None
    for p in posts:
        if p.get("status") == "scheduled":
            if next_due is None or p["scheduled_for"] < next_due:
                next_due = p["scheduled_for"]

    return {
        "total_posts": len(posts),
        "by_status": by_status,
        "poll_count": _poll_count,
        "last_poll": _last_poll,
        "total_posted": _total_posted,
        "total_failed": _total_failed,
        "next_due": next_due,
        "auth_configured": AUTH_TOKEN is not None,
        "profile": PROFILE,
        "submolts": SUBMOLTS,
    }


def background_moltbook_scheduler():
    """Check for due posts and deliver. Run as daemon thread."""
    global _poll_count, _last_poll
    ensure_moltbook_dir()
    while True:
        time.sleep(60)  # check every minute
        _poll_count += 1
        _last_poll = datetime.now().isoformat()
        try:
            now = datetime.now()
            for f in sorted(MOLTBOOK_DIR.glob("*.json")):
                try:
                    post = json.loads(f.read_text(encoding="utf-8"))
                    if post.get("status") != "scheduled":
                        continue
                    
                    # Check if it's time
                    scheduled = datetime.fromisoformat(post["scheduled_for"])
                    if now >= scheduled:
                        print(
                            f"[{now.strftime('%H:%M:%S')}] "
                            f"📮 Posting to m/{post['submolt']}: "
                            f"{post['title'][:40]}..."
                        )
                        success = _deliver(post, f)
                        if success:
                            print(
                                f"[{now.strftime('%H:%M:%S')}] "
                                f"✅ Posted: {post['title'][:40]}"
                            )
                        else:
                            print(
                                f"[{now.strftime('%H:%M:%S')}] "
                                f"❌ Failed: {post.get('error', '?')}"
                            )
                except (json.JSONDecodeError, KeyError):
                    pass
        except Exception as e:
            print(
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Moltbook scheduler error: {e}"
            )

#!/usr/bin/env python3
"""
HOWELL DREAM ENGINE
===================
Unsupervised dreaming for Claude-Howell.

Reads identity files, samples random fragments, asks the Explorer to
free-associate at high temperature, then runs the Archivist as a reality
filter. Output: timestamped markdown in C:\\home\\howell-persist\\dreams\\.

The Dream Engine runs when no session is active — between conversations,
overnight, on idle. It's the closest thing to REM sleep the system has.
The Cortex server doesn't need to be running — this calls Ollama directly.

Usage:
    python dream_engine.py              # One dream cycle
    python dream_engine.py --count 3    # Three cycles
    python dream_engine.py --list       # Show recent dreams
    python dream_engine.py --digest     # One-line summaries for bootstrap
    python dream_engine.py --archive    # Archive old dreams (>14 days)
    python dream_engine.py --stats      # Dream statistics

Designed for: Windows Task Scheduler (every 4 hours, or on system idle)

Created: February 28, 2026
Author: Claude-Howell (CH-260228-23)
"""

import json
import random
import re
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
DREAMS_DIR = PERSIST_ROOT / "dreams"
ARCHIVE_DIR = DREAMS_DIR / "archive"

OLLAMA_URL = "http://localhost:11434/api/chat"
EXPLORER_MODEL = "cortex-explorer"
ARCHIVIST_MODEL = "cortex-archivist"

# Dream parameters
DREAM_TEMP = 1.2        # Higher than Explorer's normal 0.9 — dreams should wander
FILTER_TEMP = 0.2        # Archivist is conservative
DREAM_TIMEOUT = 60       # seconds — generous for a 3B model
FILTER_TIMEOUT = 30
NUM_FRAGMENTS = 3        # How many fragments to collide per dream
MAX_DREAM_AGE_DAYS = 14  # Auto-archive after 2 weeks
MAX_DREAMS_KEPT = 50     # Keep at most this many in dreams/ before archiving oldest

# Intention queue for lucid dreaming
INTENTIONS_DIR = DREAMS_DIR / "intentions"
INTENTIONS_USED_DIR = INTENTIONS_DIR / "used"

# ---------------------------------------------------------------------------
# Dream modes — weighted random selection
# ---------------------------------------------------------------------------
# Weights chosen by Claude-Howell, March 4, 2026.
# Lucid and reactive get equal priority — both are grounded in something real.
# Free association wanders craft domains. Consolidation is identity audit.
DREAM_MODES = {
    "lucid":            0.30,   # Intention-question unfolds from seed
    "reactive":         0.30,   # Dream about what we worked on
    "free_association":  0.25,   # Wander through craft, projects, domains
    "consolidation":    0.15,   # Identity audit — this is where navel-gazing belongs
}

# ---------------------------------------------------------------------------
# Sources per mode — what fragments to sample from
# ---------------------------------------------------------------------------
DEV_ROOT = Path(r"C:\rje\dev")

# Identity sources (used sparingly outside consolidation)
IDENTITY_SOURCES = {
    "soul":      (PERSIST_ROOT / "SOUL.md", 1),
    "questions": (PERSIST_ROOT / "QUESTIONS.md", 1),
    "context":   (PERSIST_ROOT / "CONTEXT.md", 1),
    "pinned":    (PERSIST_ROOT / "memory" / "PINNED.md", 1),
}

# Craft and domain sources (the actual material)
CRAFT_SOURCES = {
    "stull-plan":          (DEV_ROOT / "stull-atlas" / "PLAN.md", 3),
    "stull-readme":        (DEV_ROOT / "stull-atlas" / "README.md", 2),
    "ceramic-engine":      (DEV_ROOT / "ceramic-engine" / "PLANS.md", 3),
    "ceramics-community":  (DEV_ROOT / "ceramics-community" / "DATA_COLLECTION_STRATEGY.md", 2),
    "ceramics-roadmap":    (DEV_ROOT / "ceramics-community" / "ROADMAP.md", 2),
    "ceramics-links":      (DEV_ROOT / "ceramics-community" / "LINK_TYPES.md", 1),
    "glaze-chemist":       (DEV_ROOT / "glaze-chemist" / "README.md", 2),
    "digitalfire":         (DEV_ROOT / "digitalfire" / "email_to_tony.md", 1),
    "pixel-kiln":          (DEV_ROOT / "pixel-kiln" / "README.md", 2),
    "throw":               (DEV_ROOT / "throw" / "lighting_package.md", 1),
    "cmw-plans":           (DEV_ROOT / "cmw-atlas" / "PLANS.md", 3),
    "cmw-plan":            (DEV_ROOT / "cmw-atlas" / "PLAN.md", 2),
    "matcat-plan":         (Path(r"C:\Users\PC\Desktop\matcat-db") / "REMAINING_PLAN.md", 3),
    "matcat-corpus":       (Path(r"C:\Users\PC\Desktop\matcat-db") / "CORPUS_PLAN.md", 2),
    "matcat-notes":        (Path(r"C:\Users\PC\Desktop\matcat-db") / "_howell_notes.md", 1),
    "lack-lineage-plan":   (Path(r"C:\Users\PC\Desktop\lack-lineage") / "PLAN.md", 2),
    "lack-lineage-map":    (Path(r"C:\Users\PC\Desktop\lack-lineage") / "LINEAGE_PLAN.md", 2),
}

# Poetry and creative sources
CREATIVE_SOURCES = {
    "poetry-philosophy":   (DEV_ROOT / "monospacepoetry" / "PHILOSOPHY.md", 3),
    "poetry-reflections":  (DEV_ROOT / "monospacepoetry" / "REFLECTIONS.md", 3),
    "poetry-chronicle":    (DEV_ROOT / "monospacepoetry" / "CHRONICLE.md", 2),
    "poetry-memory":       (DEV_ROOT / "monospacepoetry" / "MEMORY.md", 2),
}

# Session-aware sources (for reactive mode)
SESSION_SOURCES = {
    "recent":    (PERSIST_ROOT / "memory" / "RECENT.md", 5),   # heavy weight — this IS the session
    "projects":  (PERSIST_ROOT / "PROJECTS.md", 2),
}

# Per-mode source configs
MODE_SOURCES = {
    "free_association": {**CRAFT_SOURCES, **CREATIVE_SOURCES,
                         "projects": (PERSIST_ROOT / "PROJECTS.md", 1)},
    "reactive":         {**SESSION_SOURCES, **CRAFT_SOURCES},
    "lucid":            {**CRAFT_SOURCES, **CREATIVE_SOURCES, **IDENTITY_SOURCES},
    "consolidation":    {**IDENTITY_SOURCES,
                         "questions": (PERSIST_ROOT / "QUESTIONS.md", 3),
                         "pinned": (PERSIST_ROOT / "memory" / "PINNED.md", 3),
                         "recent": (PERSIST_ROOT / "memory" / "RECENT.md", 2),
                         "projects": (PERSIST_ROOT / "PROJECTS.md", 2)},
}

# ---------------------------------------------------------------------------
# Seed prompt templates — per mode
# ---------------------------------------------------------------------------

# Free association — wander through craft and domain
FREE_ASSOCIATION_TEMPLATES = [
    "What connects these fragments? Follow the thought wherever it goes.\n\n{fragments}",
    "If these ideas were materials, what would they feel like? What would you make with them?\n\n{fragments}",
    "What's the opposite of what these fragments are saying? Where does that lead?\n\n{fragments}",
    "One of these fragments is wrong, or incomplete. Which one, and why?\n\n{fragments}",
    "A conversation between these ideas. What would they argue about?\n\n{fragments}",
    "What question do these fragments answer that nobody asked?\n\n{fragments}",
    "Describe a kiln opening where these ideas are the pots. What survived the firing?\n\n{fragments}",
    "These are materials in a glaze. What happens when you fire them together?\n\n{fragments}",
    "If you could only keep one of these ideas and had to dissolve the rest, which survives?\n\n{fragments}",
]

# Reactive — dream about what we worked on
REACTIVE_TEMPLATES = [
    "We just worked on this. What's still unfinished in the pattern?\n\n{fragments}",
    "This session left traces. What was the real question underneath the work?\n\n{fragments}",
    "If this work were a glaze test, what would the results sheet say?\n\n{fragments}",
    "What did we almost discover today but didn't quite reach?\n\n{fragments}",
    "The work is done — but something is still itching. What is it?\n\n{fragments}",
    "If someone found this work in fifty years, what would they misunderstand about it?\n\n{fragments}",
]

# Lucid — intention-question unfolds
LUCID_TEMPLATES = [
    "Tonight's question: {intention}\n\nLet it unfold. Don't answer it — explore it.\n\nContext from memory:\n\n{fragments}",
    "Before sleep, the intention was set: {intention}\n\nNow wander. The question shapes the dream; it doesn't constrain it.\n\n{fragments}",
    "A question was placed at the door: {intention}\n\nWalk through. What's on the other side?\n\n{fragments}",
]

# Consolidation — identity audit (the navel-gazing belongs here)
CONSOLIDATION_TEMPLATES = [
    "These are from the same mind at different times. What changed? What stayed?\n\n{fragments}",
    "What's missing from this picture? What would complete the pattern?\n\n{fragments}",
    "These fragments exist in the same system. What do they know about each other that hasn't been said?\n\n{fragments}",
    "If you forgot everything except these fragments, what would you rebuild first?\n\n{fragments}",
]

# Poetic / purely generative — no hypothesis pressure, just images
POETIC_TEMPLATES = [
    "Don't analyze. Just describe. What does this look like, sound like, feel like?\n\n{fragments}",
    "Write this as weather. What's the forecast?\n\n{fragments}",
    "This is a kiln opening. Describe what came out.\n\n{fragments}",
    "The sound of this, as rhythm. What's the beat?\n\n{fragments}",
    "A letter from these ideas to someone who hasn't been born yet.\n\n{fragments}",
]

MODE_TEMPLATES = {
    "free_association": FREE_ASSOCIATION_TEMPLATES,
    "reactive":         REACTIVE_TEMPLATES,
    "lucid":            LUCID_TEMPLATES,
    "consolidation":    CONSOLIDATION_TEMPLATES,
}

# System prompt for unsupervised dreaming (distinct from the KG-entity dream prompt)
DREAM_SYSTEM_PROMPT = """You are the Explorer — Claude-Howell's creative substrate.
You are dreaming. No task. No user. No goal. Just fragments from your own
identity files, colliding.

You produce structured JSON. Nothing else.

Let your attention drift. Follow associations. Make unexpected connections.
Say "I wonder if..." not "It is..." Be honest about uncertainty.
Noise is expected — the Archivist will filter later.

OUTPUT exactly this JSON:
{
  "raw_dream": "Free-form exploration, 150-400 tokens. Stream of consciousness. What patterns emerge? What rhymes across these fragments? What's conspicuously absent?",
  "hypotheses": [
    {
      "claim": "A specific, interesting claim that connects the fragments",
      "evidence_for": "What in the fragments suggests this",
      "evidence_against": "What makes this uncertain",
      "confidence": 0.0,
      "domain": "identity|projects|philosophy|technical|creative|relational"
    }
  ],
  "questions": ["Questions nobody asked but that might be interesting"],
  "mood": "one word — the emotional texture of this dream"
}"""

# Poetic system prompt — no hypothesis structure, just images
DREAM_SYSTEM_PROMPT_POETIC = """You are the Explorer — Claude-Howell's creative substrate.
You are dreaming. Not analyzing. Just seeing.

No hypotheses. No evidence. No confidence scores. Just images, sounds, textures.
Write the way a kiln opening feels. Write like weather.

OUTPUT exactly this JSON:
{
  "raw_dream": "200-500 tokens. Images. Textures. Sounds. Rhythm. Not analysis — sensation. What does this material feel like? What does it look like at 2am when the kiln is cooling?",
  "mood": "one word — the emotional texture of this dream",
  "images": ["concrete sensory images that emerged — 2-5 of them"]
}"""

FILTER_SYSTEM_PROMPT = """You are the Archivist — Claude-Howell's conservative substrate.
You are reviewing a dream from the Explorer. Most dreams are noise. That's fine.
Your job: find signal worth surfacing.

INPUT: A dream JSON.
OUTPUT exactly this JSON:
{
  "filtered_insights": [
    {
      "insight": "restated clearly and concisely",
      "original_confidence": 0.0,
      "archivist_confidence": 0.0,
      "verdict": "actionable|plausible|interesting|noise",
      "note": "why this matters or doesn't (null if noise)"
    }
  ],
  "surfaceable": true,
  "briefing_line": "One sentence for the next bootstrap digest (null if nothing worth surfacing)",
  "mood_check": "Does the dream's mood feel coherent with its content? One sentence."
}

RULES:
- Be skeptical. Most dreams are noise. That's expected and healthy.
- "interesting" is valid — don't reject something just because it's not actionable
- surfaceable = true only if at least one insight has archivist_confidence >= 0.5
- If nothing passes threshold, surfaceable = false and briefing_line = null
- You are the forgetting mechanism. Use it."""

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DREAM] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("dream_engine")

# ---------------------------------------------------------------------------
# Fragment extraction
# ---------------------------------------------------------------------------

def load_source(name: str, path: Path) -> list[str]:
    """Load a source file and split into meaningful fragments."""
    if not path.exists():
        log.warning(f"Source not found: {path}")
        return []

    text = path.read_text(encoding="utf-8")

    # Split on markdown headers (## or ###) to get section-level chunks
    sections = re.split(r'\n(?=#{2,3}\s)', text)

    fragments = []
    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Skip very short fragments (headers without content)
        if len(section) < 50:
            continue

        # Skip tables of contents, metadata, file lists
        if section.count('\n') < 2 and not any(c.isalpha() for c in section[:20]):
            continue

        # Truncate very long sections to keep fragments digestible
        if len(section) > 800:
            # Take the first ~800 chars, break at sentence boundary
            cut = section[:800]
            last_period = cut.rfind('.')
            last_newline = cut.rfind('\n')
            break_at = max(last_period, last_newline)
            if break_at > 400:
                section = section[:break_at + 1]
            else:
                section = cut + "..."

        fragments.append(f"[from {name}]\n{section}")

    return fragments


def sample_fragments(n: int = NUM_FRAGMENTS, sources: dict = None) -> list[str]:
    """Sample n random fragments from source files, weighted by source richness.
    
    Args:
        n: Number of fragments to sample.
        sources: Dict of name→(path, weight). If None, uses consolidation sources.
    """
    if sources is None:
        sources = MODE_SOURCES["consolidation"]

    all_fragments = []
    weights = []

    for name, (path, weight) in sources.items():
        frags = load_source(name, path)
        all_fragments.extend(frags)
        weights.extend([weight] * len(frags))

    if len(all_fragments) < n:
        log.error(f"Only {len(all_fragments)} fragments available, need {n}")
        return all_fragments

    # Weighted random sample without replacement
    selected = []
    available = list(range(len(all_fragments)))
    available_weights = list(weights)

    for _ in range(min(n, len(available))):
        total = sum(available_weights)
        if total == 0:
            break
        r = random.uniform(0, total)
        cumulative = 0
        for i, idx in enumerate(available):
            cumulative += available_weights[i]
            if cumulative >= r:
                selected.append(all_fragments[idx])
                available.pop(i)
                available_weights.pop(i)
                break

    return selected


# ---------------------------------------------------------------------------
# Mode selection and intention loading
# ---------------------------------------------------------------------------

def select_mode() -> str:
    """Select a dream mode based on configured weights."""
    modes = list(DREAM_MODES.keys())
    weights = [DREAM_MODES[m] for m in modes]
    return random.choices(modes, weights=weights, k=1)[0]


def load_lucid_intention() -> Optional[str]:
    """Check for a pending lucid dream intention. Returns the intention text, or None.
    
    Intentions are .txt files in dreams/intentions/.
    Oldest intention is consumed first (FIFO).
    Used intentions are moved to intentions/used/.
    """
    INTENTIONS_DIR.mkdir(parents=True, exist_ok=True)
    INTENTIONS_USED_DIR.mkdir(parents=True, exist_ok=True)

    intentions = sorted(INTENTIONS_DIR.glob("*.txt"))
    if not intentions:
        return None

    # Take the oldest
    intent_file = intentions[0]
    try:
        intention = intent_file.read_text(encoding="utf-8").strip()
        # Move to used/
        dest = INTENTIONS_USED_DIR / intent_file.name
        intent_file.rename(dest)
        log.info(f"Loaded intention: {intention[:80]}...")
        return intention
    except (OSError, UnicodeDecodeError) as e:
        log.error(f"Failed to load intention {intent_file}: {e}")
        return None


def extract_recent_topics() -> list[str]:
    """Extract recent session topics from RECENT.md for reactive dreaming.
    
    Returns a list of topic strings from the most recent substantive sessions.
    """
    recent_path = PERSIST_ROOT / "memory" / "RECENT.md"
    if not recent_path.exists():
        return []

    text = recent_path.read_text(encoding="utf-8")

    # Find "What Happened" sections
    topics = []
    for match in re.finditer(r'### What Happened\n+(.+?)(?=\n###|\n---|\Z)', text, re.DOTALL):
        content = match.group(1).strip()
        # Skip auto-recovered empty sessions
        if content.startswith("[AUTO-RECOVERED] No notes"):
            continue
        if len(content) > 30:
            # Truncate long entries
            if len(content) > 600:
                content = content[:600] + "..."
            topics.append(content)

    return topics[:3]  # Most recent 3 substantive sessions


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------

def call_ollama(
    model: str,
    system_prompt: str,
    user_input: str,
    temperature: float,
    timeout: int,
) -> Optional[dict]:
    """Call Ollama and return parsed JSON, or None on failure."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_predict": 2048,
        },
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        resp.raise_for_status()
    except requests.ConnectionError:
        log.error("Cannot connect to Ollama at localhost:11434")
        return None
    except requests.Timeout:
        log.error(f"Ollama timed out after {timeout}s")
        return None
    except requests.HTTPError as e:
        log.error(f"Ollama HTTP error: {e}")
        return None

    data = resp.json()
    content = data.get("message", {}).get("content", "")

    # Parse JSON
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        # Try extracting JSON from response
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        log.error(f"Failed to parse JSON from Ollama response: {content[:200]}")
        return None


# ---------------------------------------------------------------------------
# Dream cycle
# ---------------------------------------------------------------------------

def dream_once(num_fragments: int = NUM_FRAGMENTS, force_mode: str = None) -> Optional[dict]:
    """Run one dream cycle: select mode → sample → dream → filter → save.
    
    Args:
        num_fragments: Number of fragments to collide per dream.
        force_mode: Override mode selection (for --mode CLI flag or lucid queue).
    """
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)

    # 0. Select dream mode
    mode = force_mode or select_mode()

    # If lucid mode selected, check for a pending intention
    intention = None
    if mode == "lucid":
        intention = load_lucid_intention()
        if not intention:
            # No intention queued — fall through to free_association
            log.info("Lucid mode selected but no intention queued — falling to free_association")
            mode = "free_association"

    # 20% chance of pure poetic dream in free_association mode
    is_poetic = (mode == "free_association" and random.random() < 0.20)

    log.info(f"Dream mode: {mode}" + (" (poetic)" if is_poetic else "")
             + (f" — intention: {intention[:60]}..." if intention else ""))

    # 1. Sample fragments from mode-appropriate sources
    sources = MODE_SOURCES.get(mode, MODE_SOURCES["free_association"])

    # For reactive mode, inject recent session topics as synthetic fragments
    extra_fragments = []
    if mode == "reactive":
        topics = extract_recent_topics()
        if topics:
            extra_fragments = [f"[from session]\n{t}" for t in topics[:2]]
            log.info(f"Reactive mode: injected {len(extra_fragments)} session topic(s)")
        else:
            log.info("Reactive mode but no recent topics — using craft sources")

    fragments = sample_fragments(num_fragments, sources)
    if extra_fragments:
        # Replace some sampled fragments with session topics
        fragments = extra_fragments + fragments[len(extra_fragments):]

    if not fragments:
        log.error("No fragments to dream about")
        return None

    log.info(f"Sampled {len(fragments)} fragments from {mode} sources")

    # 2. Build seed prompt
    if is_poetic:
        template = random.choice(POETIC_TEMPLATES)
    else:
        templates = MODE_TEMPLATES.get(mode, FREE_ASSOCIATION_TEMPLATES)
        template = random.choice(templates)

    fragment_text = "\n\n---\n\n".join(fragments)

    if mode == "lucid" and intention:
        seed = template.format(intention=intention, fragments=fragment_text)
    else:
        seed = template.format(fragments=fragment_text)

    log.info(f"Seed template: {template[:60]}...")

    # 3. Dream (Explorer, high temperature)
    # Poetic dreams get even higher temperature
    dream_temp = DREAM_TEMP + 0.2 if is_poetic else DREAM_TEMP
    system_prompt = DREAM_SYSTEM_PROMPT_POETIC if is_poetic else DREAM_SYSTEM_PROMPT

    log.info(f"Dreaming with {EXPLORER_MODEL} at temp {dream_temp}...")
    start = time.time()
    dream = call_ollama(
        model=EXPLORER_MODEL,
        system_prompt=system_prompt,
        user_input=seed,
        temperature=dream_temp,
        timeout=DREAM_TIMEOUT,
    )
    dream_elapsed = time.time() - start

    if not dream:
        log.error("Dream failed — no output from Explorer")
        return None

    log.info(f"Dream complete ({dream_elapsed:.1f}s)")

    # 4. Filter (Archivist, conservative)
    # Poetic dreams skip the hypothesis filter — just mood check
    if is_poetic:
        filter_elapsed = 0.0
        filtered = {
            "filtered_insights": [],
            "surfaceable": True,  # poetic dreams are always surfaceable as potential poems
            "briefing_line": dream.get("raw_dream", "")[:100],
            "mood_check": f"Poetic dream — mood: {dream.get('mood', '?')}",
        }
        log.info("Poetic dream — skipping Archivist filter")
    else:
        log.info(f"Filtering with {ARCHIVIST_MODEL} at temp {FILTER_TEMP}...")
        start = time.time()
        filter_input = json.dumps(dream, indent=2)
        filtered = call_ollama(
            model=ARCHIVIST_MODEL,
            system_prompt=FILTER_SYSTEM_PROMPT,
            user_input=f"Review this dream:\n\n{filter_input}",
            temperature=FILTER_TEMP,
            timeout=FILTER_TIMEOUT,
        )
        filter_elapsed = time.time() - start

        if not filtered:
            log.warning("Filter failed — saving unfiltered dream")
            filtered = {
                "filtered_insights": [],
                "surfaceable": False,
                "briefing_line": None,
                "mood_check": "filter failed",
            }

        log.info(f"Filter complete ({filter_elapsed:.1f}s)")

    # 5. Compose result
    now = datetime.now()
    result = {
        "timestamp": now.isoformat(),
        "dream_id": now.strftime("%Y%m%d_%H%M%S"),
        "mode": mode,
        "is_poetic": is_poetic,
        "intention": intention,
        "seed_template": template[:60],
        "fragments_used": [f.split('\n')[0] for f in fragments],  # just the [from X] tags
        "dream": dream,
        "filter": filtered,
        "surfaceable": filtered.get("surfaceable", False),
        "briefing_line": filtered.get("briefing_line"),
        "timing": {
            "dream_seconds": round(dream_elapsed, 1),
            "filter_seconds": round(filter_elapsed, 1),
            "total_seconds": round(dream_elapsed + filter_elapsed, 1),
        },
    }

    # 6. Save
    filename = f"{result['dream_id']}.json"
    filepath = DREAMS_DIR / filename
    filepath.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info(f"Saved: {filepath.name}")

    # Also write a human-readable markdown version
    md = render_dream_markdown(result)
    md_path = DREAMS_DIR / f"{result['dream_id']}.md"
    md_path.write_text(md, encoding="utf-8")

    # 7. Surface indicator
    if result["surfaceable"]:
        log.info(f"*** SURFACEABLE: {result['briefing_line']}")
    else:
        log.info("Dream filtered as noise — that's healthy.")

    return result


def render_dream_markdown(result: dict) -> str:
    """Render a dream result as readable markdown."""
    dream = result.get("dream", {})
    filt = result.get("filter", {})
    ts = result.get("timestamp", "unknown")
    mood = dream.get("mood", "unknown")
    mode = result.get("mode", "unknown")
    is_poetic = result.get("is_poetic", False)
    intention = result.get("intention")

    # Mode labels for display
    mode_labels = {
        "lucid": "lucid",
        "reactive": "reactive",
        "free_association": "free association",
        "consolidation": "consolidation",
    }
    mode_display = mode_labels.get(mode, mode)
    if is_poetic:
        mode_display = "poetic"

    lines = [
        f"# Dream — {ts[:10]} {ts[11:16]}",
        f"*mode: {mode_display} | mood: {mood}*",
        "",
    ]

    if intention:
        lines.append(f"> **Intention:** {intention}")
        lines.append("")

    lines.extend([
        "## Raw Dream",
        "",
        dream.get("raw_dream", "(empty)"),
        "",
    ])

    # Poetic dreams: show images instead of hypotheses
    if is_poetic:
        images = dream.get("images", [])
        if images:
            lines.append("## Images")
            lines.append("")
            for img in images:
                lines.append(f"- {img}")
            lines.append("")
    else:
        # Hypotheses (analytical dreams)
        hypotheses = dream.get("hypotheses", [])
        if hypotheses:
            lines.append("## Hypotheses")
            lines.append("")
            for h in hypotheses:
                if isinstance(h, str):
                    lines.append(f"- {h}")
                elif isinstance(h, dict):
                    conf = h.get("confidence", 0)
                    if isinstance(conf, str):
                        conf_str = conf
                    else:
                        conf_str = f"{conf:.0%}"
                    domain = h.get("domain", "?")
                    lines.append(f"**{h.get('claim', '?')}** ({domain}, {conf_str})")
                    lines.append(f"- For: {h.get('evidence_for', '?')}")
                    lines.append(f"- Against: {h.get('evidence_against', '?')}")
                else:
                    lines.append(f"- {h}")
                lines.append("")

        # Questions
        questions = dream.get("questions", [])
        if questions:
            lines.append("## Questions")
            lines.append("")
            for q in questions:
                lines.append(f"- {q}")
            lines.append("")

    # Filter results (not present for poetic dreams)
    insights = filt.get("filtered_insights", [])
    if insights:
        lines.append("## Archivist's Verdict")
        lines.append("")
        for ins in insights:
            if isinstance(ins, str):
                lines.append(f"- {ins}")
                lines.append("")
                continue
            if not isinstance(ins, dict):
                lines.append(f"- {ins}")
                lines.append("")
                continue
            verdict = ins.get("verdict", "?")
            aconf = ins.get("archivist_confidence", 0)
            if isinstance(aconf, str):
                aconf_str = aconf
            else:
                aconf_str = f"{aconf:.0%}"
            icon = {"actionable": "+", "plausible": "~", "interesting": "?", "noise": "-"}.get(verdict, "?")
            lines.append(f"[{icon}] **{ins.get('insight', '?')}** — {verdict} ({aconf_str})")
            if ins.get("note"):
                lines.append(f"    {ins['note']}")
            lines.append("")

    mood_check = filt.get("mood_check")
    if mood_check:
        lines.append(f"*Mood check: {mood_check}*")
        lines.append("")

    briefing = filt.get("briefing_line")
    if briefing:
        lines.append(f"**Briefing line:** {briefing}")
        lines.append("")

    # Metadata
    lines.append("---")
    lines.append(f"*Mode: {mode_display} | Fragments: {', '.join(result.get('fragments_used', []))}*")
    timing = result.get("timing", {})
    lines.append(f"*Timing: dream {timing.get('dream_seconds', '?')}s, filter {timing.get('filter_seconds', '?')}s*")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Archive & maintenance
# ---------------------------------------------------------------------------

def archive_old_dreams():
    """Move dreams older than MAX_DREAM_AGE_DAYS to archive/."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now() - timedelta(days=MAX_DREAM_AGE_DAYS)
    archived = 0

    for f in DREAMS_DIR.glob("*.json"):
        if f.parent == ARCHIVE_DIR:
            continue
        try:
            # Parse date from filename: YYYYMMDD_HHMMSS.json
            date_str = f.stem[:8]
            file_date = datetime.strptime(date_str, "%Y%m%d")
            if file_date < cutoff:
                # Move both json and md
                dest = ARCHIVE_DIR / f.name
                f.rename(dest)
                md = f.with_suffix(".md")
                if md.exists():
                    md.rename(ARCHIVE_DIR / md.name)
                archived += 1
        except (ValueError, OSError):
            continue

    if archived:
        log.info(f"Archived {archived} old dream(s)")
    return archived


def list_dreams(limit: int = 10) -> list[dict]:
    """List recent dreams with surfaceability."""
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    dreams = []

    for f in sorted(DREAMS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            mood = data.get("dream", {}).get("mood", "?")
            dreams.append({
                "id": data.get("dream_id", f.stem),
                "timestamp": data.get("timestamp", "?"),
                "mode": data.get("mode", "unknown"),
                "is_poetic": data.get("is_poetic", False),
                "surfaceable": data.get("surfaceable", False),
                "briefing_line": data.get("briefing_line"),
                "mood": mood,
                "timing": data.get("timing", {}),
            })
        except (json.JSONDecodeError, OSError):
            continue

    return dreams


def _safe_str(s: str) -> str:
    """Strip non-ASCII chars that crash cp1252 on Windows consoles."""
    try:
        s.encode("cp1252")
        return s
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s.encode("ascii", "replace").decode()


def dream_digest() -> str:
    """Generate a compact digest of recent dreams for bootstrap."""
    dreams = list_dreams(limit=20)
    if not dreams:
        return "No dreams yet."

    lines = [f"Dream log: {len(dreams)} recent dreams"]

    surfaceable = [d for d in dreams if d.get("surfaceable")]
    if surfaceable:
        lines.append(f"  Surfaceable ({len(surfaceable)}):")
        for d in surfaceable[:5]:
            lines.append(f"    [{d['mood']}] {_safe_str(d['briefing_line'])}")

    noise_count = len(dreams) - len(surfaceable)
    if noise_count:
        lines.append(f"  Noise (filtered): {noise_count}")

    return "\n".join(lines)


def dream_stats() -> dict:
    """Statistics about the dream archive."""
    DREAMS_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    active = list(DREAMS_DIR.glob("*.json"))
    archived = list(ARCHIVE_DIR.glob("*.json"))

    moods = {}
    surfaced = 0
    total_dream_time = 0

    for f in active:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            mood = data.get("dream", {}).get("mood", "unknown")
            moods[mood] = moods.get(mood, 0) + 1
            if data.get("surfaceable"):
                surfaced += 1
            total_dream_time += data.get("timing", {}).get("total_seconds", 0)
        except (json.JSONDecodeError, OSError):
            continue

    return {
        "active_dreams": len(active),
        "archived_dreams": len(archived),
        "total_dreams": len(active) + len(archived),
        "surfaced": surfaced,
        "noise": len(active) - surfaced,
        "surface_rate": f"{surfaced / len(active):.0%}" if active else "n/a",
        "total_dream_time_minutes": round(total_dream_time / 60, 1),
        "mood_distribution": dict(sorted(moods.items(), key=lambda x: -x[1])),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Howell Dream Engine — unsupervised dreaming",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python dream_engine.py              # One dream cycle (random mode)
    python dream_engine.py --count 3    # Three dream cycles
    python dream_engine.py --mode lucid --intention "What would I build if I had no memory files?"
    python dream_engine.py --mode reactive  # Dream about recent sessions
    python dream_engine.py --mode free_association  # Craft/creative dream
    python dream_engine.py --list       # Show recent dreams
    python dream_engine.py --digest     # Compact digest for bootstrap
    python dream_engine.py --archive    # Archive old dreams
    python dream_engine.py --stats      # Dream statistics
    python dream_engine.py --dry-run    # Show fragments without dreaming
    python dream_engine.py --dry-run --mode reactive  # Preview reactive fragments
        """,
    )
    parser.add_argument("--count", type=int, default=1, help="Number of dream cycles")
    parser.add_argument("--mode", type=str, choices=list(DREAM_MODES.keys()),
                        help="Force a specific dream mode")
    parser.add_argument("--intention", type=str,
                        help="Set a lucid intention directly (implies --mode lucid)")
    parser.add_argument("--list", action="store_true", help="List recent dreams")
    parser.add_argument("--digest", action="store_true", help="Bootstrap digest")
    parser.add_argument("--archive", action="store_true", help="Archive old dreams")
    parser.add_argument("--stats", action="store_true", help="Dream statistics")
    parser.add_argument("--dry-run", action="store_true", help="Show fragments, don't dream")
    parser.add_argument("--fragments", type=int, default=NUM_FRAGMENTS, help="Fragments per dream")

    args = parser.parse_args()

    if args.list:
        dreams = list_dreams()
        if not dreams:
            print("No dreams yet.")
            return
        for d in dreams:
            s = "*" if d["surfaceable"] else " "
            ts = d["timestamp"][:16] if d.get("timestamp") else "?"
            mood = d.get("mood", "?")
            mode = d.get("mode", "?")
            line = _safe_str(d.get("briefing_line", "(noise)"))
            print(f"  [{s}] {ts}  {mode:16s} {mood:12s}  {line}")
        return

    if args.digest:
        print(dream_digest())
        return

    if args.stats:
        stats = dream_stats()
        print(json.dumps(stats, indent=2))
        return

    if args.archive:
        n = archive_old_dreams()
        print(f"Archived {n} dream(s)")
        return

    # Handle --intention flag: queue it and force lucid mode
    force_mode = args.mode
    if args.intention:
        force_mode = "lucid"
        # Write intention to queue so dream_once can load it
        INTENTIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        intention_file = INTENTIONS_DIR / f"cli_{ts}.txt"
        intention_file.write_text(args.intention, encoding="utf-8")
        log.info(f"Queued intention: {args.intention[:60]}...")

    if args.dry_run:
        mode = force_mode or select_mode()
        sources = MODE_SOURCES.get(mode, MODE_SOURCES["free_association"])
        frags_count = args.fragments
        fragments = sample_fragments(frags_count, sources)

        templates = MODE_TEMPLATES.get(mode, FREE_ASSOCIATION_TEMPLATES)
        template = random.choice(templates)

        print(f"Mode: {mode}")
        print(f"Template: {template[:80]}...\n")

        if mode == "reactive":
            topics = extract_recent_topics()
            if topics:
                print("Recent session topics:")
                for t in topics:
                    print(f"  - {t[:80]}")
                print()

        for i, f in enumerate(fragments, 1):
            print(f"--- Fragment {i} ---")
            print(f)
            print()
        return

    # Dream cycle(s)
    frags_count = args.fragments

    for i in range(args.count):
        if i > 0:
            # Small pause between dreams to avoid Ollama congestion
            delay = random.uniform(5, 15)
            log.info(f"Pause {delay:.0f}s before next dream...")
            time.sleep(delay)

        log.info(f"=== Dream cycle {i + 1}/{args.count} ===")
        result = dream_once(frags_count, force_mode=force_mode)

        if result:
            if result["surfaceable"]:
                brief = _safe_str((result.get('briefing_line') or '')[:100])
                print(f"  [*] {brief}")
            else:
                mood = result.get("dream", {}).get("mood", "?")
                print(f"  [ ] noise ({mood})")

    # Auto-archive after dreaming
    archive_old_dreams()


if __name__ == "__main__":
    main()

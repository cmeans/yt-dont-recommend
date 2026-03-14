"""Clickbait detection: config loading, LLM classifiers, and pipeline orchestrator.

Optional runtime dependencies (gracefully absent — detection silently skipped):
  - ollama               : local LLM inference for title and transcript
  - pyyaml               : YAML config file support
  - youtube_transcript_api : transcript fetching

Install all at once:
  pip install yt-dont-recommend[clickbait]
"""

from __future__ import annotations

import ast
import base64
import json
import logging
import re

import time
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .config import _n

log = logging.getLogger(__name__)


def _clamp_confidence(conf: "float | None") -> "float | None":
    """Clamp model confidence to the calibrated range [0.05, 0.95]."""
    if conf is None:
        return conf
    return max(0.05, min(0.95, float(conf)))


# ---------------------------------------------------------------------------
# Title pre-filters — trivially not clickbait without LLM evaluation
# ---------------------------------------------------------------------------

# Substrings that, when present (case-insensitive), mean NOT clickbait.
# These patterns reliably identify promotional/news content that the model
# sometimes flags incorrectly despite explicit prompt instructions.
_PREFILTER_CONTAINS = (
    "official trailer",
    "official teaser",
    "official music video",
    "official audio",
    "official video",
    "lyric video",
    "remaster",
    "| clip",    # "Movie Name | CLIP 💥 4K" — named movie/show clip; content type explicit
)

# Case-insensitive suffixes that mark a title as NOT clickbait.
_PREFILTER_ENDS_WITH = (
    " mv",
    " (mv)",
    " [mv]",
    " (acoustic)",
    " [acoustic]",
)

# Case-insensitive prefixes that mark a title as NOT clickbait.
_PREFILTER_STARTS_WITH = (
    "breaking:",        # "BREAKING: specific event" — standalone colon form
    "breaking news:",   # "BREAKING NEWS: ..." — space before "news" makes it distinct
    "watch live:",
    "weather:",
    "weather alert:",
    "live stream:",
)

# Compiled regex patterns that mark a title as NOT clickbait.
# Used for patterns that require word-gap matching (not simple substrings).
_PREFILTER_REGEX = (
    # "official * trailer" — catches "Official Final Trailer", "Official Theatrical
    # Trailer", etc. where a word appears between "official" and "trailer".
    # _PREFILTER_CONTAINS already handles "official trailer" (no gap); this
    # covers the variants the model flags when a modifier word is present.
    re.compile(r"\bofficial\b.*\btrailer\b", re.IGNORECASE),
)


def _prefilter_title(title: str) -> "str | None":
    """Return a skip reason if *title* is trivially not clickbait, else None.

    Called before LLM evaluation. Matches promotional titles, news alerts,
    and live-stream prefixes that the model sometimes misclassifies.
    """
    t = title.lower().strip()
    for sub in _PREFILTER_CONTAINS:
        if sub in t:
            return f"pre-filter: contains '{sub}'"
    for sfx in _PREFILTER_ENDS_WITH:
        if t.endswith(sfx):
            return f"pre-filter: suffix '{sfx.strip()}'"
    for pfx in _PREFILTER_STARTS_WITH:
        if t.startswith(pfx):
            return f"pre-filter: prefix '{pfx}'"
    for pat in _PREFILTER_REGEX:
        if pat.search(title):
            return f"pre-filter: pattern '{pat.pattern}'"
    return None


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict = {
    "video": {
        "title": {
            "model": {"name": "llama3.1:8b", "params": {}, "auto_pull": False},
            "threshold": 0.75,
            "ambiguous_low": 0.4,
        },
        "thumbnail": {
            "enabled": False,
            "model": {"name": "gemma3:4b", "params": {}, "auto_pull": False},
            "threshold": 0.75,
            "two_step": True,
            "timeout": 90,
            "time_budget": 120,
        },
        "transcript": {
            "enabled": False,
            "model": {"name": "phi3.5", "params": {}, "auto_pull": False},
            "threshold": 0.75,
            "no_transcript": "pass",  # "pass" | "flag" | "title-only"
        },
    }
}

CLICKBAIT_CONFIG_FILE = Path.home() / ".yt-dont-recommend" / "clickbait-config.yaml"

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into a copy of *base*."""
    result = deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = deepcopy(v)
    return result


def _apply_prompt(template: str, **vars: str) -> str:
    """Substitute {var} placeholders in *template*.

    Uses simple string replacement rather than str.format() so that literal
    JSON braces in the prompt (e.g. {"is_clickbait": true}) are left untouched.
    Only the known variable names passed as kwargs are replaced.
    """
    result = template
    for key, value in vars.items():
        result = result.replace(f"{{{key}}}", str(value))
    return result


_DEFAULT_CONFIG_YAML = """\
# yt-dont-recommend clickbait detection config
# Generated on first run. Edit to tune behavior.
# Full docs: https://github.com/cmeans/yt-dont-recommend
#
# Prompt placeholders:
#   {title}       — video title
#   {description} — thumbnail visual description (thumbnail classify prompt only)
#   {transcript}  — transcript text (transcript prompt only)
#   {chars}       — transcript character count (transcript prompt only)

video:
  title:
    # Benchmarked models (title classification):
    #   phi3.5       — 93% accuracy, ~8s/title, 0 parse failures. Fast but over-flags
    #                  educational and news titles; raise threshold to compensate.
    #   llama3.1:8b  — better calibrated for nuanced judgment; ~6-8s/title in
    #                  practice (hardware-dependent). Recommended for accuracy.
    #   llama3.2:1b  — AVOID: flags everything indiscriminately (27% accuracy in tests).
    model:
      name: llama3.1:8b
      params: {}
      # auto_pull: false  # set to true to pull this model automatically if not found
    # Score >= threshold → flagged as clickbait
    # 0.75 is calibrated for llama3.1:8b; phi3.5 users should raise to 0.85
    threshold: 0.75
    # Score in [ambiguous_low, threshold) → escalate to thumbnail (if enabled)
    ambiguous_low: 0.4
    prompt: |
      You are a YouTube clickbait detector. Classify the video title below.

      CLICKBAIT signals — title manipulates rather than informs:
      - Withholds key information to force a click ("You won't believe...", "This will SHOCK you", "Here's what happened", "they got caught")
      - Vague subject or mystery framing with no informational content ("Something MASSIVE...", "This Changes Everything", "What really happened")
      - Manufactured urgency or outrage with no specific informational payload
      - Misleading framing that misrepresents what the video actually delivers
      - ALL-CAPS used for emotional manipulation across most of the title about vague or exaggerated content

      NOT clickbait — default to false for all of these, even if the phrasing sounds dramatic:
      - News headlines and breaking news alerts — the alarm is in the event, not manufactured by the title ("Iran launches attack", "Tornado devastates county")
      - News titles with a few ALL-CAPS words emphasizing a specific fact ("Iran and Hezbollah LAUNCH JOINT ATTACK against Israel and US" — caps on the verb of a factual sentence is emphasis, not manipulation)
      - Titles quoting or paraphrasing a named person or source, even if alarming ("Senator says X", "Expert warns of Y")
      - Opinion and political commentary that states its argument directly — the argument IS the promised content ("Trump's Power Grab Is Backfiring", "The self-immolation of Donald Trump", "How Iran Proves America Needs Europe")
      - Tutorial and how-to titles, even terse ones ("How To Learn To Code In 2026", "Walk up hills without getting tired")
      - Factual or educational questions, even on dramatic topics ("How Black Holes Die", "Firing Guns in Space")
      - Named technical subjects, proper nouns, or specific things described directly
      - Comparison or "vs" titles that directly state their subject
      - Titles containing "Official Trailer", "Official Teaser", "Music Video" — promotional titles are not clickbait
      - Named TV show segments or recurring episode titles ("Amber Says What: ...", "Show Name Ep. 6")
      - Titles with specific names, numbers, dates, or verifiable facts
      - Music releases, song titles, and album names — a song or album title announces what the content is; there is no withheld information ("Girls Just Want to Have Fun", "Somethin' Stupid", "Mr. Brightside")
      - Science and nature headlines using editorial emphasis words like "Surprise!" or "Stunning" that introduce a specific named finding — the finding is present in the title, not withheld ("Surprise! Milky Way has no central black hole" — the discovery is named)
      - Geopolitical and military news that describes a specific real event, even if dramatic — named actors, locations, and actions make it factual ("U.S. military bombs island", "Iran mines the Strait of Hormuz")
      - Product reviews and tech comparisons in first-person format when the specific product is named ("I Replaced My Laptop With a Phone | RayNeo Air 4 Pro" — named product rules out curiosity gap)
      - Vlog and series episodes with a specific named topic and episode number — the episode marker signals ongoing informational series content

      Confidence guide — use the full scale, not just 0.10 and 0.80:
      - 0.95: Unmistakable pure bait — no informational content at all ("they got caught", "Yikes.", "You NEED to see this")
      - 0.85: Clear clickbait signal — strong manipulation with minimal information ("Something MASSIVE Entered...", "STUNS Everyone SILENT")
      - 0.75: Probably clickbait — sensational framing but some real information present
      - 0.30: Mild sensational wording but probably honest ("The Biggest Flaw in Starship Design", "Why Batman Looks Like a Billion Bucks")
      - 0.10: Clearly not clickbait — factual, newsworthy, opinion stating its argument, tutorial, or directly named subject

      EXAMPLES — calibrate against these:
        NOT clickbait: "Huge satellite to crash down to Earth"
          → specific factual news event; the alarm is real, not manufactured
        NOT clickbait: "Millions of Americans could be eligible to become Canadian under new law"
          → factual headline with a specific verifiable claim
        NOT clickbait: "Spring break travel alert"
          → specific news alert; brevity is not a clickbait signal
        NOT clickbait: "Amber Says What: Trump's Olympic Hockey Team Invites..."
          → named recurring TV segment; delivers exactly what it promises
        NOT clickbait: "TrueNAS vs Nextcloud (2026) - Which One Is BETTER?"
          → direct comparison; subject fully stated even with a question mark
        NOT clickbait: "The Universe Is Racing Apart. We May Finally Know Why."
          → science/discovery framing; hedging reflects genuine scientific uncertainty, not withheld information
        NOT clickbait: "Strange New Explanation for Why Quantum World Collapses Into Reality"
          → science/discovery with a specific named topic; "strange" and "new" describe genuine scientific novelty, not withheld information
        NOT clickbait: "Whistleblower: Ex-DOGE employee copied Social Security data; CNN anchor apologizes | Media Miss"
          → news headline with specific named facts; "Whistleblower:" is journalistic framing, not a curiosity gap; pipe-suffix is a named segment identifier
        NOT clickbait: "Shipping is Afire | Attacks off Kuwait | No Escorts, the Strategic Petroleum Reserve & the Jones Act"
          → dramatic opener followed by multiple specific named topics; the specificity of the pipe-listed facts rules out clickbait
        NOT clickbait: "Trump's Power Grab Is Backfiring — But That Makes Him Dangerous"
          → opinion commentary that states its argument directly; the thesis IS the promised content, not withheld information
        NOT clickbait: "Iran War Update: Mines in the Strait of Hormuz"
          → "X Update: specific topic" is standard news/analysis format; named location and specific military topic rules out manufactured mystery
        NOT clickbait: "Hewlett and Momoa Weren't on Speaking Terms Until 'See' Forged a New Chapter (Clip)"
          → entertainment interview clip; named actors and named show state exactly what it covers; "(Clip)" label is a content-type signal
        NOT clickbait: "[CNA 24/7 LIVE] Breaking news on Asia and award-winning documentaries and shows"
          → live news stream with named broadcaster; format prefix signals ongoing coverage, not manufactured curiosity
        NOT clickbait: "BREAKING: Loss of U.S. KC-135 Over Iraq During Operation Epic Fury"
          → "BREAKING:" with a specific military aircraft designation, named country, and named operation is a news alert; all key facts are present in the title
        NOT clickbait: "Surprise! Milky Way Might Not Have a Black Hole After All"
          → "Surprise!" is editorial emphasis on a specific named scientific finding; the discovery is named in the title, not withheld
        NOT clickbait: "The Most Important Picture in the History of Science"
          → science educator framing; superlatives describe significance of a named topic, not manufactured curiosity; educational titles use strong language to convey genuine importance
        NOT clickbait: "Girls Just Want to Have Fun"
          → classic song title; music and song titles are content announcements — there is no withheld information
        NOT clickbait: "U.S. military bombs island key to Iran's economy and oil revenues"
          → specific military news with named actors (U.S. military), named action (bombs), and named target (island key to Iran's economy); dramatic subject matter is not a clickbait signal when the event is real
        NOT clickbait: "Confetti Carnage in the Multiverse | Everything Everywhere All at Once | CLIP 💥 4K"
          → named movie clip; movie title fully stated; "CLIP" label is a content-type signal, not a curiosity gap
        NOT clickbait: "This TRANSFORMED Our Electrical System ⚡️ Aluminum Catamaran Build Pt.61"
          → vlog series episode with a specific named topic (electrical system) and episode number (Pt.61); series format with informational subject is not clickbait even with ALL-CAPS verb
        NOT clickbait: "I Replaced My Laptop With a Phone | RayNeo Air 4 Pro"
          → product review in first-person format; specific named product rules out curiosity gap; "I did X with Y" is not clickbait when Y is named explicitly
        CLICKBAIT: "They got CAUGHT..."
          → withholds who, what, why — zero information; pure mystery bait
        CLICKBAIT: "Something MASSIVE Just Happened..."
          → vague subject with no informational content whatsoever
        CLICKBAIT: "You WON'T BELIEVE What Doctors Found..."
          → manufactured curiosity gap; withholds the claimed discovery

      When in doubt, default to NOT clickbait.

      Title: {title}

      Reply with raw JSON only — no code fences, no explanation outside the JSON:
      {"is_clickbait": true, "confidence": 0.9, "reasoning": "one sentence"}
      or
      {"is_clickbait": false, "confidence": 0.1, "reasoning": "one sentence"}

  thumbnail:
    # Thumbnail classification is slow (~65s/video) — disabled by default
    enabled: false
    # Must be a multimodal (vision) Ollama model. Benchmarked models:
    #   gemma3:4b          — 100% accuracy on 6-video test set with two_step: true, ~65s/video.
    #                        Recommended. Always use two_step: true with this model.
    #   llava:7b           — tested; over-flagged at 33% accuracy. Not recommended.
    #   llama3.2-vision    — newer vision model; not yet benchmarked on this task.
    model:
      name: gemma3:4b
      params: {}
      # auto_pull: false  # set to true to pull this model automatically if not found
    threshold: 0.75
    # two_step: use Visual Description Grounding (recommended, more accurate)
    two_step: true
    timeout: 90
    time_budget: 120
    # prompt_describe: step 1 of two_step — describe the thumbnail literally
    prompt_describe: |
      Look carefully at this image and report only what you literally see.

      Structure your answer exactly like this:

      PEOPLE: [count and each person's facial expression — use precise words: neutral, smiling, serious, open-mouthed, wide-eyed, exaggerated-shock, etc.]
      TEXT: [quote every word of visible text exactly, or "none"]
      GRAPHICS: [arrows, circles, highlight boxes, split-panels, badges — or "none"]
      COMPOSITION: [brief factual note on layout, background color, any staging]
    # prompt_classify: step 2 of two_step — classify from the visual description
    prompt_classify: |
      You are a YouTube clickbait detector.

      Title (for context only — do not analyze the title wording): {title}

      Thumbnail visual description:
      {description}

      Your job is to find clickbait signals in the VISUAL DESCRIPTION only.
      The title is provided so you can check if the thumbnail mismatches the topic —
      not as a source of clickbait signals itself.

      STRONG signals — flag HIGH (confidence >= 0.85) only if the description explicitly shows:
        S1. Exaggerated-shock expression: gaping mouth, wildly wide eyes in a clearly PERFORMED/STAGED way.
            NOT: smiling, serious, neutral, natural surprise, or a character in a dramatic scene.
        S2. Sensational TEXT overlay: quoted words like "SHOCKING", "EXPOSED", "YOU WON'T BELIEVE",
            "can you spot the fake?", "GONE WRONG", etc. S2 applies ONLY to text — never to a person's expression.
        S3. Graphic manipulation: red circles, arrows, or highlight boxes explicitly pointed at something
            to manufacture alarm.
        S4. Side-by-side split panel: two DISTINCT images placed next to each other for comparison.
            NOT: a person in front of a busy or colorful background, a cluttered set, multiple posters on a wall.

      DEFAULT to LOW (is_clickbait: false, confidence <= 0.30) when:
        - No S1/S2/S3/S4 signal is explicitly present in the description
        - The only "drama" comes from the subject matter itself (a rocket, a galaxy, a black hole)
        - A person is present but their expression is neutral, smiling, or serious
        - Background is colorful, dark, or dramatic but has no text overlay or graphic manipulation
        - A scientific visualization (sphere, nebula, planet, diagram) matches the topic of the title

      If you cannot point to a specific S1/S2/S3/S4 item in the description, output is_clickbait: false.

      Reply with raw JSON only — no code fences, no explanation outside the JSON:
      {"is_clickbait": true, "confidence": 0.9, "reasoning": "S2: thumbnail shows text overlay reading X"}
      or
      {"is_clickbait": false, "confidence": 0.1, "reasoning": "no S1-S4 signals found"}
    # prompt_single: used when two_step is false
    prompt_single: |
      You are a YouTube clickbait detector. Classify this video thumbnail.

      Title: {title}

      Clickbait signals to look for:
      - Shocked, exaggerated, or distressed facial expressions (S1)
      - Bold text overlays making sensational claims (S2)
      - Red circles, arrows, or highlight boxes (S3)
      - Side-by-side comparisons designed to provoke curiosity (S4)

      Reply with raw JSON only — no code fences, no explanation outside the JSON:
      {"is_clickbait": true, "confidence": 0.9, "reasoning": "S1: exaggerated expression"}
      or
      {"is_clickbait": false, "confidence": 0.1, "reasoning": "no clickbait signals"}

  transcript:
    # Transcript classification — disabled by default
    enabled: false
    # Benchmarked models (transcript classification):
    #   phi3.5       — default; not yet end-to-end benchmarked for this task.
    #   llama3.1:8b  — recommended for longer-text comprehension; better at
    #                  judging whether a transcript matches a title's premise.
    model:
      name: phi3.5
      params: {}
      # auto_pull: false  # set to true to pull this model automatically if not found
    threshold: 0.75
    # no_transcript: what to do when a transcript isn't available
    #   pass       — treat as not clickbait
    #   flag       — treat as clickbait
    #   title-only — rely on title result only
    no_transcript: pass
    prompt: |
      You are a YouTube clickbait detector resolving an ambiguous title.

      The title was flagged as potentially clickbait. Use the transcript excerpt to
      determine whether the title's framing is honest.

      Title: {title}

      Transcript excerpt (first ~{chars} chars):
      {transcript}

      Decision rules:
      - If the transcript covers the topic the title claims → the title is HONEST
        → lower your confidence that it is clickbait (is_clickbait: false)
      - If the transcript content clearly mismatches the title's promise or framing
        → the title is MISLEADING → raise confidence (is_clickbait: true)
      - If the transcript is substantive and on-topic, that is strong evidence
        AGAINST clickbait even if the title has mild sensational wording

      The transcript is evidence of what the video actually delivers. If it delivers
      on the title's premise, that is NOT clickbait.

      Reply with raw JSON only — no code fences, no explanation outside the JSON:
      {"is_clickbait": true, "confidence": 0.9, "reasoning": "transcript does not match title because ..."}
      or
      {"is_clickbait": false, "confidence": 0.1, "reasoning": "transcript confirms title's topic"}
"""


def _write_default_config(cfg_path: Path) -> None:
    """Write the default config as commented YAML on first use."""
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")
        log.info("Created default clickbait config: %s", cfg_path)
    except Exception as exc:
        log.warning("Could not write default clickbait config to %s: %s", cfg_path, exc)


def load_config(path: "Path | str | None" = None) -> dict:
    """Load clickbait config from *path* (or the default location), merging with defaults.

    Returns the default config when:
    - the file does not exist
    - pyyaml is not installed
    - the file cannot be parsed
    """
    cfg_path = Path(path) if path else CLICKBAIT_CONFIG_FILE
    if not cfg_path.exists():
        _write_default_config(cfg_path)
        return deepcopy(_DEFAULT_CONFIG)

    try:
        import yaml  # pyyaml — optional dep
    except ImportError:
        log.warning(
            "pyyaml not installed — %s will be ignored and any customizations lost. "
            "Install with: pip install pyyaml", cfg_path
        )
        return deepcopy(_DEFAULT_CONFIG)

    try:
        with open(cfg_path, encoding="utf-8") as f:
            user_cfg = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to load clickbait config from %s: %s", cfg_path, exc)
        return deepcopy(_DEFAULT_CONFIG)

    return _deep_merge(_DEFAULT_CONFIG, user_cfg)


# ---------------------------------------------------------------------------
# JSON extraction  (handles fenced / prose model output)
# ---------------------------------------------------------------------------


def extract_json(raw: str) -> dict:
    """Robustly extract a JSON object from raw model output."""
    raw = raw.strip()

    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    def _clamp(result: dict) -> dict:
        if isinstance(result.get("confidence"), (int, float)):
            result["confidence"] = _clamp_confidence(result["confidence"])
        return result

    # Direct parse
    try:
        return _clamp(json.loads(raw))
    except json.JSONDecodeError:
        pass

    # First {...} block in surrounding prose
    m = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
    if m:
        try:
            return _clamp(json.loads(m.group()))
        except json.JSONDecodeError:
            pass

    # Regex field extraction — last resort for truncated / mangled output
    is_cb = re.search(r'"is_clickbait"\s*:\s*(true|false)', raw)
    conf  = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
    rsn   = re.search(r'"reasoning"\s*:\s*"([^"]+)"', raw)

    if is_cb:
        return _clamp({
            "is_clickbait": is_cb.group(1) == "true",
            "confidence":   float(conf.group(1)) if conf else None,
            "reasoning":    rsn.group(1) if rsn else "(extracted)",
            "_parse":       "regex-fallback",
        })

    return {
        "is_clickbait": False,
        "confidence":   0.0,
        "reasoning":    "(parse-failed)",
        "_parse":       "failed",
    }


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_TITLE_PROMPT = """\
You are a YouTube clickbait detector. Classify the video title below.

CLICKBAIT signals — title manipulates rather than informs:
- Withholds key information to force a click ("You won't believe...", "This will SHOCK you", "Here's what happened", "they got caught")
- Vague subject or mystery framing with no informational content ("Something MASSIVE...", "This Changes Everything", "What really happened")
- Manufactured urgency or outrage with no specific informational payload
- Misleading framing that misrepresents what the video actually delivers
- ALL-CAPS used for emotional manipulation across most of the title about vague or exaggerated content

NOT clickbait — default to false for all of these, even if the phrasing sounds dramatic:
- News headlines and breaking news alerts — the alarm is in the event, not manufactured by the title ("Iran launches attack", "Tornado devastates county")
- News titles with a few ALL-CAPS words emphasizing a specific fact ("Iran and Hezbollah LAUNCH JOINT ATTACK against Israel and US" — caps on the verb of a factual sentence is emphasis, not manipulation)
- Titles quoting or paraphrasing a named person or source, even if alarming ("Senator says X", "Expert warns of Y")
- Opinion and political commentary that states its argument directly — the argument IS the promised content ("Trump's Power Grab Is Backfiring", "The self-immolation of Donald Trump", "How Iran Proves America Needs Europe")
- Tutorial and how-to titles, even terse ones ("How To Learn To Code In 2026", "Walk up hills without getting tired")
- Factual or educational questions, even on dramatic topics ("How Black Holes Die", "Firing Guns in Space")
- Named technical subjects, proper nouns, or specific things described directly
- Comparison or "vs" titles that directly state their subject
- Titles containing "Official Trailer", "Official Teaser", "Music Video" — promotional titles are not clickbait
- Named TV show segments or recurring episode titles ("Amber Says What: ...", "Show Name Ep. 6")
- Titles with specific names, numbers, dates, or verifiable facts

Confidence guide — use the full scale, not just 0.10 and 0.80:
- 0.95: Unmistakable pure bait — no informational content at all ("they got caught", "Yikes.", "You NEED to see this")
- 0.85: Clear clickbait signal — strong manipulation with minimal information ("Something MASSIVE Entered...", "STUNS Everyone SILENT")
- 0.75: Probably clickbait — sensational framing but some real information present
- 0.30: Mild sensational wording but probably honest ("The Biggest Flaw in Starship Design")
- 0.10: Clearly not clickbait — factual, newsworthy, opinion stating its argument, tutorial, or directly named subject

EXAMPLES — calibrate against these:
  NOT clickbait: "Huge satellite to crash down to Earth"
    → specific factual news event; the alarm is real, not manufactured
  NOT clickbait: "Millions of Americans could be eligible to become Canadian under new law"
    → factual news headline with a specific verifiable claim
  NOT clickbait: "Spring break travel alert"
    → short but specific news alert; brevity is not a clickbait signal
  NOT clickbait: "Amber Says What: Trump's Olympic Hockey Team Invites..."
    → named recurring TV segment; delivers exactly what it promises
  NOT clickbait: "TrueNAS vs Nextcloud (2026) - Which One Is BETTER?"
    → direct comparison; subject fully stated even with a question mark
  NOT clickbait: "The Universe Is Racing Apart. We May Finally Know Why."
    → science/discovery framing; hedging reflects genuine scientific uncertainty, not withheld information
  NOT clickbait: "Strange New Explanation for Why Quantum World Collapses Into Reality"
    → science/discovery with a specific named topic; "strange" and "new" describe genuine scientific novelty, not withheld information
  NOT clickbait: "Whistleblower: Ex-DOGE employee copied Social Security data; CNN anchor apologizes | Media Miss"
    → news headline with specific named facts; "Whistleblower:" is journalistic framing, not a curiosity gap; pipe-suffix is a named segment identifier
  NOT clickbait: "Shipping is Afire | Attacks off Kuwait | No Escorts, the Strategic Petroleum Reserve & the Jones Act"
    → dramatic opener followed by multiple specific named topics; the pipe-listed facts are highly specific, ruling out clickbait
  NOT clickbait: "Trump's Power Grab Is Backfiring — But That Makes Him Dangerous"
    → opinion commentary that states its argument directly; the thesis IS the promised content, not withheld information
  NOT clickbait: "Iran War Update: Mines in the Strait of Hormuz"
    → "X Update: specific topic" is standard news/analysis format; named location and specific military topic rules out manufactured mystery
  NOT clickbait: "Hewlett and Momoa Weren't on Speaking Terms Until 'See' Forged a New Chapter (Clip)"
    → entertainment interview clip; named actors and named show state exactly what it covers; "(Clip)" label is a content-type signal
  NOT clickbait: "[CNA 24/7 LIVE] Breaking news on Asia and award-winning documentaries and shows"
    → live news stream with named broadcaster; format prefix signals ongoing coverage, not manufactured curiosity
  CLICKBAIT: "They got CAUGHT..."
    → withholds who, what, why — zero information; pure mystery bait
  CLICKBAIT: "Something MASSIVE Just Happened..."
    → vague subject with no informational content whatsoever
  CLICKBAIT: "You WON'T BELIEVE What Doctors Found..."
    → manufactured curiosity gap; withholds the claimed discovery

When in doubt, default to NOT clickbait.

Title: {title}

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{{"is_clickbait": true, "confidence": 0.9, "reasoning": "one sentence"}}
or
{{"is_clickbait": false, "confidence": 0.1, "reasoning": "one sentence"}}
"""

_THUMB_DESCRIBE_PROMPT = """\
Look carefully at this image and report only what you literally see.

Structure your answer exactly like this:

PEOPLE: [count and each person's facial expression — use precise words: \
neutral, smiling, serious, open-mouthed, wide-eyed, exaggerated-shock, etc.]
TEXT: [quote every word of visible text exactly, or "none"]
GRAPHICS: [arrows, circles, highlight boxes, split-panels, badges — or "none"]
COMPOSITION: [brief factual note on layout, background color, any staging]
"""

_THUMB_CLASSIFY_PROMPT = """\
You are a YouTube clickbait detector.

Title (for context only — do not analyze the title wording): {title}

Thumbnail visual description:
{description}

Your job is to find clickbait signals in the VISUAL DESCRIPTION only.
The title is provided so you can check if the thumbnail mismatches the topic —
not as a source of clickbait signals itself.

STRONG signals — flag HIGH (confidence >= 0.85) only if the description \
explicitly shows:
  S1. Exaggerated-shock expression: gaping mouth, wildly wide eyes in a \
      clearly PERFORMED/STAGED way. NOT: smiling, serious, neutral, natural \
      surprise, or a character in a dramatic scene.
  S2. Sensational TEXT overlay: quoted words like "SHOCKING", "EXPOSED", \
      "YOU WON'T BELIEVE", "can you spot the fake?", "GONE WRONG", etc. \
      S2 applies ONLY to text — never to a person's expression.
  S3. Graphic manipulation: red circles, arrows, or highlight boxes \
      explicitly pointed at something to manufacture alarm.
  S4. Side-by-side split panel: two DISTINCT images placed next to each \
      other for comparison. NOT: a person in front of a busy or colorful \
      background, a cluttered set, multiple posters on a wall.

DEFAULT to LOW (is_clickbait: false, confidence <= 0.30) when:
  - No S1/S2/S3/S4 signal is explicitly present in the description
  - The only "drama" comes from the subject matter itself \
    (a rocket, a galaxy, a black hole, a character from a show)
  - A person is present but their expression is neutral, smiling, or serious
  - Background is colorful, dark, or dramatic but has no text overlay or \
    graphic manipulation
  - A scientific visualization (sphere, nebula, planet, diagram) matches \
    the topic of the title

If you cannot point to a specific S1/S2/S3/S4 item in the description, \
output is_clickbait: false.

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{"is_clickbait": true, "confidence": 0.9, "reasoning": "S2: thumbnail shows text overlay reading X"}
or
{"is_clickbait": false, "confidence": 0.1, "reasoning": "no S1-S4 signals found"}
"""

_THUMB_SINGLE_PROMPT = """\
You are a YouTube clickbait detector. Classify this video thumbnail.

Title: {title}

Clickbait signals to look for:
- Shocked, exaggerated, or distressed facial expressions (S1)
- Bold text overlays making sensational claims (S2)
- Red circles, arrows, or highlight boxes (S3)
- Side-by-side comparisons designed to provoke curiosity (S4)

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{"is_clickbait": true, "confidence": 0.9, "reasoning": "S1: exaggerated expression"}
or
{"is_clickbait": false, "confidence": 0.1, "reasoning": "no clickbait signals"}
"""

_TRANSCRIPT_PROMPT = """\
You are a YouTube clickbait detector resolving an ambiguous title.

The title was flagged as potentially clickbait. Use the transcript excerpt to \
determine whether the title's framing is honest.

Title: {title}

Transcript excerpt (first ~{chars} chars):
{transcript}

Decision rules:
- If the transcript covers the topic the title claims → the title is HONEST \
  → lower your confidence that it is clickbait (is_clickbait: false)
- If the transcript content clearly mismatches the title's promise or framing \
  → the title is MISLEADING → raise confidence (is_clickbait: true)
- If the transcript is substantive and on-topic, that is strong evidence \
  AGAINST clickbait even if the title has mild sensational wording

The transcript is evidence of what the video actually delivers. If it delivers \
on the title's premise, that is NOT clickbait.

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{"is_clickbait": true, "confidence": 0.9, "reasoning": "transcript does not match title because ..."}
or
{"is_clickbait": false, "confidence": 0.1, "reasoning": "transcript confirms title's topic"}
"""

_TRANSCRIPT_CHAR_LIMIT = 6000

# ---------------------------------------------------------------------------
# Low-level I/O helpers
# ---------------------------------------------------------------------------


def _fetch_thumbnail_b64(video_id: str) -> "str | None":
    """Fetch the best available YouTube thumbnail and return as base64.

    Returns None when the thumbnail cannot be fetched or is a placeholder.
    """
    for quality in ("maxresdefault", "hqdefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{quality}.jpg"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            if len(data) < 5000:  # reject placeholder / 404 images
                continue
            return base64.b64encode(data).decode()
        except Exception:  # noqa: BLE001
            continue
    return None


def _ollama_chat(
    model: str,
    prompt: str,
    img_b64: "str | None" = None,
    params: "dict | None" = None,
    timeout: int = 90,
) -> str:
    """Call ollama.chat() and return the response content string.

    Raises ImportError when ollama is not installed.
    Raises RuntimeError (or ollama-specific exceptions) on inference failure.
    """
    try:
        from ollama import Client as OllamaClient
    except ImportError:
        raise ImportError("ollama not installed; run: pip install yt-dont-recommend[clickbait]")

    client = OllamaClient(timeout=timeout)
    msg: dict = {"role": "user", "content": prompt}
    if img_b64:
        msg["images"] = [img_b64]

    response = client.chat(
        model=model,
        messages=[msg],
        options={"temperature": 0, **(params or {})},
    )
    return response.message.content


def _fetch_transcript(video_id: str) -> "tuple[str | None, str]":
    """Return ``(text, status)`` where *status* is one of:
    ``'ok'``, ``'disabled'``, ``'not_found'``, ``'no_api'``, ``'error'``.
    """
    try:
        from youtube_transcript_api import (
            YouTubeTranscriptApi,
            TranscriptsDisabled,
            NoTranscriptFound,
        )
    except ImportError:
        return None, "no_api"

    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["en", "en-US"])
        text = " ".join(seg.text for seg in fetched)
        return text[:_TRANSCRIPT_CHAR_LIMIT], "ok"
    except TranscriptsDisabled:
        return None, "disabled"
    except NoTranscriptFound:
        return None, "not_found"
    except Exception as exc:  # noqa: BLE001
        log.debug("Transcript fetch failed for %s: %s", video_id, exc)
        return None, "error"


# ---------------------------------------------------------------------------
# Classifiers
# ---------------------------------------------------------------------------


def classify_title(video_id: str, title: str, cfg: dict) -> dict:
    """Classify *title* as clickbait or not. Returns a result dict."""
    skip_reason = _prefilter_title(title)
    if skip_reason:
        return {
            "is_clickbait": False,
            "confidence":   0.05,
            "reasoning":    skip_reason,
            "stage":        "title",
            "model":        "prefilter",
            "video_id":     video_id,
        }

    title_cfg = cfg["video"]["title"]
    model     = title_cfg["model"]["name"]
    params    = title_cfg["model"].get("params") or {}

    prompt = _apply_prompt(title_cfg.get("prompt") or _TITLE_PROMPT, title=title)
    t0 = time.monotonic()
    try:
        raw = _ollama_chat(model, prompt, params=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("Title classification failed for %s: %s", video_id, exc)
        return {
            "is_clickbait": False,
            "confidence":   0.0,
            "reasoning":    f"classification error: {exc}",
            "stage":        "title",
            "model":        model,
            "error":        str(exc),
        }

    result = extract_json(raw)
    result.update({
        "stage":   "title",
        "model":   model,
        "video_id": video_id,
        "elapsed": round(time.monotonic() - t0, 2),
    })
    return result


def classify_thumbnail(video_id: str, title: str, cfg: dict) -> dict:
    """Classify the *video_id* thumbnail as clickbait or not. Returns a result dict.

    Uses the two-step Visual Description Grounding approach by default
    (``thumbnail.two_step: true`` in config) to reduce hallucination.
    """
    thumb_cfg = cfg["video"]["thumbnail"]
    model     = thumb_cfg["model"]["name"]
    params    = thumb_cfg["model"].get("params") or {}
    timeout   = thumb_cfg.get("timeout", 90)
    two_step  = thumb_cfg.get("two_step", True)

    img_b64 = _fetch_thumbnail_b64(video_id)
    if img_b64 is None:
        return {
            "is_clickbait": False,
            "confidence":   0.0,
            "reasoning":    "thumbnail unavailable",
            "stage":        "thumbnail",
            "model":        model,
            "status":       "no_image",
        }

    t0 = time.monotonic()
    try:
        if two_step:
            describe_tmpl  = thumb_cfg.get("prompt_describe")  or _THUMB_DESCRIBE_PROMPT
            classify_tmpl  = thumb_cfg.get("prompt_classify")  or _THUMB_CLASSIFY_PROMPT
            description = _ollama_chat(
                model, describe_tmpl, img_b64=img_b64,
                params=params, timeout=timeout,
            )
            classify_prompt = _apply_prompt(
                classify_tmpl, title=title, description=description.strip(),
            )
            raw = _ollama_chat(model, classify_prompt, params=params, timeout=timeout)
            result = extract_json(raw)
            result["_description"] = description.strip()[:200]
        else:
            single_tmpl = thumb_cfg.get("prompt_single") or _THUMB_SINGLE_PROMPT
            prompt = _apply_prompt(single_tmpl, title=title)
            raw    = _ollama_chat(model, prompt, img_b64=img_b64,
                                  params=params, timeout=timeout)
            result = extract_json(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("Thumbnail classification failed for %s: %s", video_id, exc)
        return {
            "is_clickbait": False,
            "confidence":   0.0,
            "reasoning":    f"classification error: {exc}",
            "stage":        "thumbnail",
            "model":        model,
            "error":        str(exc),
        }

    result.update({
        "stage":    "thumbnail",
        "model":    model,
        "video_id": video_id,
        "elapsed":  round(time.monotonic() - t0, 2),
    })
    return result


def classify_transcript(video_id: str, title: str, cfg: dict) -> dict:
    """Fetch *video_id* transcript and classify the title as clickbait or not.

    Returns a result dict. When the transcript is unavailable, the
    ``transcript.no_transcript`` config setting controls the outcome:
    - ``'pass'``       — treat as not clickbait (default; benefit of the doubt)
    - ``'flag'``       — treat as clickbait
    - ``'title-only'`` — defer to the title-only result (``_defer_to_title: True``)
    """
    tx_cfg = cfg["video"]["transcript"]
    model  = tx_cfg["model"]["name"]
    params = tx_cfg["model"].get("params") or {}
    no_tx  = tx_cfg.get("no_transcript", "pass")

    transcript, status = _fetch_transcript(video_id)

    if transcript is None:
        if no_tx == "flag":
            return {
                "is_clickbait": True,
                "confidence":   0.75,
                "reasoning":    f"transcript unavailable ({status}); flagged per config",
                "stage":        "transcript",
                "model":        model,
                "tx_status":    status,
            }
        if no_tx == "title-only":
            return {
                "is_clickbait":   False,
                "confidence":     0.0,
                "reasoning":      f"transcript unavailable ({status}); deferred to title",
                "stage":          "transcript",
                "model":          model,
                "tx_status":      status,
                "_defer_to_title": True,
            }
        # "pass" — benefit of the doubt
        return {
            "is_clickbait": False,
            "confidence":   0.0,
            "reasoning":    f"transcript unavailable ({status}); treated as not clickbait",
            "stage":        "transcript",
            "model":        model,
            "tx_status":    status,
        }

    prompt = _apply_prompt(
        tx_cfg.get("prompt") or _TRANSCRIPT_PROMPT,
        title=title, transcript=transcript, chars=str(len(transcript)),
    )
    t0 = time.monotonic()
    try:
        raw = _ollama_chat(model, prompt, params=params)
    except Exception as exc:  # noqa: BLE001
        log.warning("Transcript classification failed for %s: %s", video_id, exc)
        return {
            "is_clickbait": False,
            "confidence":   0.0,
            "reasoning":    f"classification error: {exc}",
            "stage":        "transcript",
            "model":        model,
            "error":        str(exc),
            "tx_status":    "ok",
        }

    result = extract_json(raw)
    result.update({
        "stage":     "transcript",
        "model":     model,
        "video_id":  video_id,
        "elapsed":   round(time.monotonic() - t0, 2),
        "tx_status": "ok",
        "tx_chars":  len(transcript),
    })
    return result


# ---------------------------------------------------------------------------
# Batch classifiers
# ---------------------------------------------------------------------------

_BATCH_TITLE_PROMPT = """\
Classify each video title below as clickbait or not.

CLICKBAIT signals — title manipulates rather than informs:
- Withholds key information to force a click ("You won't believe...", "Here's what happened")
- Vague subject or mystery framing with no informational content ("Something MASSIVE...", "This Changes Everything")
- Manufactured urgency or outrage with no specific informational payload
- Misleading framing that misrepresents what the video delivers
- ALL-CAPS used for emotional manipulation about vague or exaggerated content

NOT clickbait — default to false for all of these:
- News headlines and breaking news alerts ("Iran launches attack", "Tornado devastates county")
- News titles with ALL-CAPS emphasizing a specific fact ("Iran and Hezbollah LAUNCH JOINT ATTACK")
- Titles quoting or paraphrasing a named person or source ("Senator says X", "Expert warns of Y")
- Opinion and political commentary that states its argument directly
- Tutorial and how-to titles ("How To Learn To Code In 2026")
- Factual or educational questions ("How Black Holes Die", "Firing Guns in Space")
- Named technical subjects, proper nouns, or specific things described directly
- Comparison or "vs" titles that directly state their subject
- Titles with "Official Trailer", "Official Teaser", "Music Video"
- Named TV show segments or recurring episode titles ("Amber Says What: ...", "Show Name Ep. 6")
- Titles with specific names, numbers, dates, or verifiable facts

Confidence guide:
- 0.95: Unmistakable pure bait ("they got caught", "You NEED to see this")
- 0.85: Clear clickbait ("Something MASSIVE Entered...", "STUNS Everyone SILENT")
- 0.75: Probably clickbait
- 0.30: Mild sensational wording but probably honest
- 0.10: Clearly not clickbait

EXAMPLES — calibrate against these:
  NOT clickbait: "Huge satellite to crash down to Earth"
    → specific factual news event; alarm is real, not manufactured
  NOT clickbait: "Millions of Americans could be eligible to become Canadian under new law"
    → factual headline with a specific verifiable claim
  NOT clickbait: "Spring break travel alert"
    → specific news alert; brevity is not a clickbait signal
  NOT clickbait: "Amber Says What: Trump's Olympic Hockey Team Invites..."
    → named recurring TV segment; delivers exactly what it promises
  NOT clickbait: "TrueNAS vs Nextcloud (2026) - Which One Is BETTER?"
    → direct comparison; subject fully stated even with a question mark
  NOT clickbait: "The Universe Is Racing Apart. We May Finally Know Why."
    → science/discovery framing; hedging reflects genuine scientific uncertainty, not withheld information
  NOT clickbait: "Strange New Explanation for Why Quantum World Collapses Into Reality"
    → science/discovery with a specific named topic; "strange" and "new" describe genuine scientific novelty, not withheld information
  NOT clickbait: "Whistleblower: Ex-DOGE employee copied Social Security data; CNN anchor apologizes | Media Miss"
    → news headline with specific named facts; "Whistleblower:" is journalistic framing; pipe-suffix is a named segment identifier
  NOT clickbait: "Shipping is Afire | Attacks off Kuwait | No Escorts, the Strategic Petroleum Reserve & the Jones Act"
    → dramatic opener followed by multiple specific named topics; the specificity of the pipe-listed facts rules out clickbait
  NOT clickbait: "Trump's Power Grab Is Backfiring — But That Makes Him Dangerous"
    → opinion commentary that states its argument directly; the thesis IS the promised content
  NOT clickbait: "Iran War Update: Mines in the Strait of Hormuz"
    → "X Update: specific topic" is standard news/analysis format; named location and specific military topic rules out manufactured mystery
  NOT clickbait: "Hewlett and Momoa Weren't on Speaking Terms Until 'See' Forged a New Chapter (Clip)"
    → entertainment interview clip; named actors and named show state what it covers; "(Clip)" label is a content-type signal
  NOT clickbait: "[CNA 24/7 LIVE] Breaking news on Asia and award-winning documentaries and shows"
    → live news stream with named broadcaster; format prefix signals ongoing coverage, not manufactured curiosity
  CLICKBAIT: "They got CAUGHT..."
    → withholds who, what, why — zero information; pure mystery bait
  CLICKBAIT: "Something MASSIVE Just Happened..."
    → vague subject with no informational content whatsoever
  CLICKBAIT: "You WON'T BELIEVE What Doctors Found..."
    → manufactured curiosity gap; withholds the claimed discovery

When in doubt, default to NOT clickbait.

Titles:
{titles}

Reply with a JSON array ONLY — no prose, no code fences.
One object per title, in the same order, with consecutive index values starting at 0.
The array must contain exactly as many objects as there are titles above.
[
  {{"index": 0, "is_clickbait": true|false, "confidence": 0.0-1.0, "reasoning": "one sentence"}},
  ... (one entry per title)
]
"""

_BATCH_TRANSCRIPT_PROMPT = """\
Each title below was ambiguous for clickbait. Use the transcript excerpt to determine
whether the title's framing is honest. Reply with a JSON array in the same order.

Decision rules:
- If the transcript covers the topic the title claims → title is HONEST → is_clickbait: false
- If transcript clearly mismatches the title's promise → title is MISLEADING → is_clickbait: true
- Substantive on-topic transcript is strong evidence AGAINST clickbait

Items:
{items}

Reply with a JSON array ONLY — no prose, no code fences.
One object per item, in the same order, with consecutive index values starting at 0.
The array must contain exactly as many objects as there are items above.
[
  {{"index": 0, "is_clickbait": true|false, "confidence": 0.0-1.0, "reasoning": "one sentence"}},
  ... (one entry per item)
]
"""


def _parse_batch_response(raw: str, expected: int) -> "list[dict] | None":
    """Parse a batch LLM response into a list of per-item result dicts.

    Returns None if the response cannot be parsed or is missing entries.
    On partial parse, missing indices are filled with a sentinel so the caller
    can fall back to individual classification for those slots.
    """
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    # Find outermost JSON array
    start = raw.find("[")
    end   = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return None

    candidate = raw[start:end + 1]
    try:
        items = json.loads(candidate)
    except json.JSONDecodeError:
        # Some models return Python-style single-quoted strings (invalid JSON).
        # ast.literal_eval handles those safely.
        try:
            items = ast.literal_eval(candidate)
        except Exception:
            return None

    if not isinstance(items, list):
        return None

    # Map by index field; fall back to positional if index missing
    by_index: dict[int, dict] = {}
    for pos, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        idx = item.get("index", pos)
        by_index[idx] = item

    # Build ordered result; mark missing slots as None
    result = []
    for i in range(expected):
        entry = by_index.get(i)
        result.append(entry)  # None = needs individual fallback

    return result


def classify_titles_batch(
    items: "list[dict]",
    cfg: dict,
    batch_size: int = 10,
) -> "list[dict]":
    """Classify a list of ``{video_id, title}`` dicts in batches.

    Returns a list of title result dicts in the same order as *items*.
    Each result has the same shape as ``classify_title()`` output.
    Falls back to ``classify_title()`` for any item whose batch result
    cannot be parsed.
    """
    results: list[dict] = []
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start:batch_start + batch_size]
        batch_results = _classify_title_batch(batch, cfg)
        results.extend(batch_results)
    return results


def _classify_title_batch(batch: "list[dict]", cfg: dict) -> "list[dict]":
    """Send one batch of titles to the LLM and return per-item results.

    Pre-filtered titles (e.g. "Official Trailer", "BREAKING NEWS:") are
    returned immediately as not clickbait without an LLM call.

    Falls back to ``classify_title()`` for any item that cannot be parsed
    from the batch response.

    Known issue (observed 2026-03-11): batch models occasionally return correct
    indices but cross-contaminated reasoning — item N's reasoning describes item
    N±1's title. The per-item DEBUG log below (title → reasoning) is the primary
    tool for spotting this. Detection and automatic fallback are not yet
    implemented; retest after any prompt or model change.
    """
    title_cfg = cfg["video"]["title"]
    model     = title_cfg["model"]["name"]
    params    = title_cfg["model"].get("params") or {}
    timeout   = title_cfg.get("timeout", 300)

    # --- Pre-filter: separate trivially-safe titles from LLM-bound ones ---
    results: list["dict | None"] = [None] * len(batch)
    llm_positions: list[int] = []  # indices into batch that need LLM

    for i, item in enumerate(batch):
        reason = _prefilter_title(item["title"])
        if reason:
            log.debug("Batch title [%d]: %r → pre-filter: %s", i, item["title"], reason)
            results[i] = {
                "is_clickbait": False,
                "confidence":   0.05,
                "reasoning":    reason,
                "stage":        "title",
                "model":        "prefilter",
                "video_id":     item["video_id"],
                "_batch":       True,
            }
        else:
            llm_positions.append(i)

    if not llm_positions:
        return results  # type: ignore[return-value]

    llm_batch = [batch[i] for i in llm_positions]

    # Use json.dumps() for consistent double-quoting regardless of title content
    titles_block = "\n".join(
        f'{seq}: {json.dumps(item["title"], ensure_ascii=False)}'
        for seq, item in enumerate(llm_batch)
    )
    prompt_tmpl = title_cfg.get("prompt_batch") or _BATCH_TITLE_PROMPT
    prompt = _apply_prompt(prompt_tmpl, titles=titles_block)

    log.debug(
        "Batch title: sending %s to %s:\n%s",
        _n(len(llm_batch), "title"), model, titles_block,
    )

    t0 = time.monotonic()
    try:
        raw = _ollama_chat(model, prompt, params=params, timeout=timeout)
    except Exception as exc:
        titles_summary = "; ".join(
            f'[{seq}] {json.dumps(item["title"])}' for seq, item in enumerate(llm_batch)
        )
        log.warning(
            "Batch title classification failed (%s, model=%s): %s\nTitles: %s",
            _n(len(llm_batch), "item"), model, exc, titles_summary,
        )
        for orig_i, item in zip(llm_positions, llm_batch):
            results[orig_i] = classify_title(item["video_id"], item["title"], cfg)
        return results  # type: ignore[return-value]

    elapsed = round(time.monotonic() - t0, 2)
    log.debug("Batch title: raw response (%d chars): %s", len(raw), raw)

    parsed = _parse_batch_response(raw, len(llm_batch))

    if parsed is None:
        log.warning(
            "Batch title parse failed (%s, %.1fs, model=%s) — "
            "raw response: %r — falling back to individual calls",
            _n(len(llm_batch), "item"), elapsed, model, raw,
        )
        for orig_i, item in zip(llm_positions, llm_batch):
            results[orig_i] = classify_title(item["video_id"], item["title"], cfg)
        return results  # type: ignore[return-value]

    for seq, (orig_i, item) in enumerate(zip(llm_positions, llm_batch)):
        entry = parsed[seq]
        if entry is None:
            log.debug("Batch title: index %d missing from response — individual fallback", seq)
            results[orig_i] = classify_title(item["video_id"], item["title"], cfg)
        else:
            if isinstance(entry.get("confidence"), (int, float)):
                entry["confidence"] = _clamp_confidence(entry["confidence"])
            entry.update({
                "stage":    "title",
                "model":    model,
                "video_id": item["video_id"],
                "elapsed":  elapsed,
                "_batch":   True,
            })
            # Per-item log: title + reasoning lets you spot cross-contamination
            # (model returns index N's score but N±1's reasoning).
            log.debug(
                "Batch title [%d]: %r → is_clickbait=%s score=%.2f — %s",
                orig_i, item["title"],
                entry.get("is_clickbait"), entry.get("confidence", 0.0),
                entry.get("reasoning", ""),
            )
            results[orig_i] = entry

    log.debug(
        "Batch title: %s in %.1fs (%.1fs/item)",
        _n(len(llm_batch), "item"), elapsed, elapsed / len(llm_batch),
    )
    return results  # type: ignore[return-value]


def classify_transcripts_batch(
    items: "list[dict]",
    cfg: dict,
    batch_size: int = 5,
) -> "list[dict]":
    """Classify a list of ``{video_id, title}`` dicts by transcript in batches.

    Returns a list of transcript result dicts in the same order as *items*.
    Each result has the same shape as ``classify_transcript()`` output.
    Falls back to ``classify_transcript()`` per item on parse failure.
    """
    results: list[dict] = []
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start:batch_start + batch_size]
        results.extend(_classify_transcript_batch(batch, cfg))
    return results


def _classify_transcript_batch(batch: "list[dict]", cfg: dict) -> "list[dict]":
    """Send one batch of (title, transcript) pairs to the LLM."""
    tx_cfg = cfg["video"]["transcript"]
    model  = tx_cfg["model"]["name"]
    params = tx_cfg["model"].get("params") or {}
    no_tx  = tx_cfg.get("no_transcript", "pass")

    # Fetch transcripts for all items first
    fetched: list[tuple["str | None", str]] = [
        _fetch_transcript(item["video_id"]) for item in batch
    ]

    # Items with no transcript handled per no_tx policy (same as individual)
    pending_indices: list[int] = []
    pre_results: dict[int, dict] = {}
    for i, ((transcript, status), item) in enumerate(zip(fetched, batch)):
        if transcript is None:
            if no_tx == "flag":
                pre_results[i] = {
                    "is_clickbait": True, "confidence": 0.75,
                    "reasoning": f"transcript unavailable ({status}); flagged per config",
                    "stage": "transcript", "model": model, "tx_status": status,
                }
            elif no_tx == "title-only":
                pre_results[i] = {
                    "is_clickbait": False, "confidence": 0.0,
                    "reasoning": f"transcript unavailable ({status}); deferred to title",
                    "stage": "transcript", "model": model, "tx_status": status,
                    "_defer_to_title": True,
                }
            else:  # "pass"
                pre_results[i] = {
                    "is_clickbait": False, "confidence": 0.0,
                    "reasoning": f"transcript unavailable ({status}); treated as not clickbait",
                    "stage": "transcript", "model": model, "tx_status": status,
                }
        else:
            pending_indices.append(i)

    if not pending_indices:
        return [pre_results[i] for i in range(len(batch))]

    # Build batch prompt for items with transcripts
    items_block_parts = []
    for seq, i in enumerate(pending_indices):
        item = batch[i]
        tx, _ = fetched[i]
        items_block_parts.append(
            f'index {seq}:\n  title: {item["title"]!r}\n  transcript: {repr(tx[:500])}'
        )
    items_block = "\n\n".join(items_block_parts)

    prompt_tmpl = tx_cfg.get("prompt_batch") or _BATCH_TRANSCRIPT_PROMPT
    prompt = _apply_prompt(prompt_tmpl, items=items_block)

    log.debug(
        "Batch transcript: sending %d items to %s:\n%s",
        len(pending_indices), model, items_block,
    )

    t0 = time.monotonic()
    try:
        raw = _ollama_chat(model, prompt, params=params)
    except Exception as exc:
        titles_summary = "; ".join(
            f'[{seq}] {batch[i]["title"]!r}' for seq, i in enumerate(pending_indices)
        )
        log.warning(
            "Batch transcript classification failed (%d items, model=%s): %s\nTitles: %s",
            len(pending_indices), model, exc, titles_summary,
        )
        for i in pending_indices:
            pre_results[i] = classify_transcript(batch[i]["video_id"], batch[i]["title"], cfg)
        return [pre_results[i] for i in range(len(batch))]

    elapsed = round(time.monotonic() - t0, 2)
    log.debug("Batch transcript: raw response (%d chars): %s", len(raw), raw)

    parsed = _parse_batch_response(raw, len(pending_indices))

    if parsed is None:
        log.warning(
            "Batch transcript parse failed (%d items, %.1fs, model=%s) — "
            "raw response: %r — falling back to individual calls",
            len(pending_indices), elapsed, model, raw,
        )
        for i in pending_indices:
            pre_results[i] = classify_transcript(batch[i]["video_id"], batch[i]["title"], cfg)
    else:
        for seq, i in enumerate(pending_indices):
            entry = parsed[seq]
            if entry is None:
                pre_results[i] = classify_transcript(batch[i]["video_id"], batch[i]["title"], cfg)
            else:
                entry.update({
                    "stage": "transcript", "model": model,
                    "video_id": batch[i]["video_id"],
                    "elapsed": elapsed, "_batch": True,
                    "tx_status": "ok", "tx_chars": len(fetched[i][0] or ""),
                })
                log.debug(
                    "Batch transcript [%d]: %r → is_clickbait=%s score=%.2f — %s",
                    seq, batch[i]["title"],
                    entry.get("is_clickbait"), entry.get("confidence", 0.0),
                    entry.get("reasoning", ""),
                )
                pre_results[i] = entry

    return [pre_results[i] for i in range(len(batch))]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def classify_video(video_id: str, title: str, cfg: "dict | None" = None) -> dict:
    """Run the full clickbait detection pipeline for a single video.

    Pipeline:
      1. Title classification (always runs).
      2. Thumbnail classification (only when ``thumbnail.enabled: true`` and
         title confidence falls in the ambiguous band [ambiguous_low, threshold)).
      3. Transcript classification (only when ``transcript.enabled: true`` and
         the result is still ambiguous after previous stages).

    Returns a unified result dict with these keys:
      ``video_id``, ``title``, ``is_clickbait``, ``confidence``, ``flagged``,
      ``stages``, ``title_result``, ``thumbnail_result``, ``transcript_result``,
      ``classified_at``.
    """
    if cfg is None:
        cfg = load_config()

    title_cfg    = cfg["video"]["title"]
    thumb_cfg    = cfg["video"]["thumbnail"]
    tx_cfg       = cfg["video"]["transcript"]
    threshold    = title_cfg.get("threshold", 0.75)
    ambiguous_lo = title_cfg.get("ambiguous_low", 0.4)

    # --- Stage 1: title ---
    title_result = classify_title(video_id, title, cfg)
    stages       = ["title"]
    confidence   = title_result.get("confidence") or 0.0
    is_clickbait = title_result.get("is_clickbait", False)

    # --- Stage 2: thumbnail (opt-in; fires only in ambiguous band) ---
    thumb_result = None
    if thumb_cfg.get("enabled", False) and ambiguous_lo <= confidence < threshold:
        thumb_result = classify_thumbnail(video_id, title, cfg)
        stages.append("thumbnail")
        thumb_conf = thumb_result.get("confidence") or 0.0
        # Take the higher confidence reading
        if thumb_conf > confidence:
            confidence   = thumb_conf
            is_clickbait = thumb_result.get("is_clickbait", False)

    # --- Stage 3: transcript (opt-in; fires only when still ambiguous) ---
    tx_result = None
    if tx_cfg.get("enabled", False) and ambiguous_lo <= confidence < threshold:
        tx_result = classify_transcript(video_id, title, cfg)
        stages.append("transcript")
        if not tx_result.get("_defer_to_title"):
            tx_conf      = tx_result.get("confidence") or 0.0
            confidence   = tx_conf
            is_clickbait = tx_result.get("is_clickbait", False)

    return {
        "video_id":          video_id,
        "title":             title,
        "is_clickbait":      is_clickbait,
        "confidence":        confidence,
        "flagged":           confidence >= threshold and is_clickbait,
        "stages":            stages,
        "title_result":      title_result,
        "thumbnail_result":  thumb_result,
        "transcript_result": tx_result,
        "classified_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

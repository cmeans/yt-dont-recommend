"""Clickbait detection: config loading, LLM classifiers, and pipeline orchestrator.

Optional runtime dependencies (gracefully absent — detection silently skipped):
  - ollama               : local LLM inference for title and transcript
  - pyyaml               : YAML config file support
  - youtube_transcript_api : transcript fetching

Install all at once:
  pip install yt-dont-recommend[clickbait]
"""

from __future__ import annotations

import base64
import json
import logging
import re

import time
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict = {
    "video": {
        "title": {
            "model": {"name": "llama3.1:8b", "params": {}},
            "threshold": 0.75,
            "ambiguous_low": 0.4,
        },
        "thumbnail": {
            "enabled": False,
            "model": {"name": "gemma3:4b", "params": {}},
            "threshold": 0.75,
            "two_step": True,
            "timeout": 90,
            "time_budget": 120,
        },
        "transcript": {
            "enabled": False,
            "model": {"name": "phi3.5", "params": {}},
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
      - Excessive ALL-CAPS used for emotional manipulation across most of the title (not just one or two emphasis words)

      NOT clickbait — default to false for these, even if the topic is alarming or dramatic:
      - News headlines and news alerts, even on alarming topics — the alarm is in the event, not manufactured by the title ("Spring break travel alert", "Missiles visible in night sky")
      - Titles quoting or paraphrasing a named person or named source, even when the quote sounds alarming ("Oil expert warns of 'nightmare scenario'", "Senator says X")
      - Factual or educational questions, even if the topic sounds dramatic ("How Black Holes Die", "Firing Guns in Space")
      - Named technical subjects, proper nouns, or specific things described directly ("Yamato Wave Motion Gun", "Apollo 11 Descent Engine")
      - Comparison or "vs" titles that directly state their subject
      - Opinion or analysis pieces that announce their argument up front ("Why X isn't working", "The Real Reason Y")
      - Titles that state exactly what the video covers without withholding information

      Confidence guide — use the full scale, not just 0.10 and 0.80:
      - 0.95: Unmistakable pure bait — no informational content at all ("they got caught", "Yikes.", "You NEED to see this")
      - 0.85: Clear clickbait signal — strong manipulation with minimal information ("Something MASSIVE Entered...", "STUNS Everyone SILENT")
      - 0.75: Probably clickbait — sensational framing but some real information present
      - 0.30: Mild sensational wording but probably honest ("The Biggest Flaw in Starship Design", "Why Batman Looks Like a Billion Bucks")
      - 0.10: Clearly not clickbait — factual, descriptive, newsworthy, or directly named subject

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
        log.warning("pyyaml not installed; ignoring %s — using default clickbait config", cfg_path)
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

    # Direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # First {...} block in surrounding prose
    m = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    # Regex field extraction — last resort for truncated / mangled output
    is_cb = re.search(r'"is_clickbait"\s*:\s*(true|false)', raw)
    conf  = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
    rsn   = re.search(r'"reasoning"\s*:\s*"([^"]+)"', raw)

    if is_cb:
        return {
            "is_clickbait": is_cb.group(1) == "true",
            "confidence":   float(conf.group(1)) if conf else None,
            "reasoning":    rsn.group(1) if rsn else "(extracted)",
            "_parse":       "regex-fallback",
        }

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

Clickbait signals:
- Withholds key information to force a click ("You won't believe...", "This will SHOCK you")
- Excessive capitalization or punctuation used for emotional manipulation
- Vague, sensational, or exaggerated promises
- Misleading emotional framing unrelated to actual content

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

"""
experiments/probe_thumbnail.py

Probe: classify YouTube video thumbnails using a multimodal Ollama model.
Tests whether thumbnail analysis alone — or thumbnail + title combined —
can reliably detect clickbait that slips past title-only classification.

Thumbnail URLs are fetched directly from YouTube's CDN (no login needed):
    https://img.youtube.com/vi/{video_id}/hqdefault.jpg

Run:
    .venv/bin/python experiments/probe_thumbnail.py
    .venv/bin/python experiments/probe_thumbnail.py --model gemma3:4b
    .venv/bin/python experiments/probe_thumbnail.py --model llava:7b
    .venv/bin/python experiments/probe_thumbnail.py --model moondream
    .venv/bin/python experiments/probe_thumbnail.py --two-step
    .venv/bin/python experiments/probe_thumbnail.py --no-title
"""

import argparse
import base64
import json
import re
import time
import urllib.request
from pathlib import Path

try:
    import ollama
    from ollama import Client as OllamaClient
except ImportError:
    raise ImportError("Run: .venv/bin/pip install ollama")

# Module-level client — timeout set at parse time in main(), default 90s.
_client: OllamaClient | None = None
INFERENCE_TIMEOUT = 90  # overridden by --timeout

# (video_id, title, expected_clickbait)
# Mix of clear clickbait, borderline, and legitimate.
SAMPLE_VIDEOS = [
    # The trigger case — thumbnail is the primary signal
    ("vIJXfUy5cT4", "If YOU Can't Tell What's AI, You NEED To See This!", True),
    # Clear clickbait (thumbnails likely have shocked faces, arrows, ALL CAPS)
    ("wm-AMmwtZAg", "THIS Is #1 FASTEST Way To BURN Dangerous Fat",        True),
    # Borderline title — thumbnail may clarify
    ("zzTFCarWGEc", "Another Forever War Just Got the Green Light | Kat Abughazaleh", False),
    # Legitimate content — thumbnails should be clean
    ("cygLFHOi9S4", "Can Starship V3 Actually Launch In 4 Weeks?",         False),
    ("roNZr_i8iOY", "Surprise! Milky Way Might Not Have a Black Hole After All", False),
    ("B6ZAQUqSkkA", "Rose meets the Doctor | The End of Time | Doctor Who", False),
]

THUMBNAIL_URLS = [
    "https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
    "https://img.youtube.com/vi/{vid}/hqdefault.jpg",   # fallback
]

PROMPT_WITH_TITLE = """\
You are a YouTube clickbait detector. Analyse this video thumbnail and title together.

Title: {title}

Clickbait signals to look for in the THUMBNAIL:
- Shocked, exaggerated, or distressed facial expressions
- Bold text overlays making sensational claims ("YOU WON'T BELIEVE", "SHOCKING", "EXPOSED")
- Red circles, arrows, or highlight boxes drawing attention to something dramatic
- Side-by-side comparisons designed to provoke curiosity or anxiety
- AI-generated or misleading imagery used for shock value
- Thumbnail content that mismatches or exaggerates what the title implies

Clickbait signals to look for in the TITLE:
- Withholds key information to force a click
- Excessive capitalisation or emotional manipulation
- Vague, sensational, or exaggerated promises

Consider both together: does the combination feel designed to manipulate rather than inform?

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{{"is_clickbait": true, "confidence": 0.9, "reasoning": "one sentence describing the key signal(s)"}}
or
{{"is_clickbait": false, "confidence": 0.1, "reasoning": "one sentence"}}
"""

# Two-step prompts (Visual Description Grounding approach from research).
# Step 1 forces the model to commit to visual facts before any classification.
# Step 2 classifies based only on that committed description + the title.
# This breaks the hallucination loop where the model rationalises a label
# from the title and then invents matching visual evidence.

PROMPT_DESCRIBE = """\
Look carefully at this image and report only what you literally see.

Structure your answer exactly like this:

PEOPLE: [count and each person's facial expression — use precise words: \
neutral, smiling, serious, open-mouthed, wide-eyed, exaggerated-shock, etc.]
TEXT: [quote every word of visible text exactly, or "none"]
GRAPHICS: [arrows, circles, highlight boxes, split-panels, badges — or "none"]
COMPOSITION: [brief factual note on layout, background colour, any staging]
"""

PROMPT_CLASSIFY_FROM_DESCRIPTION = """\
You are a YouTube clickbait detector. Use ONLY the visual description below \
and the title to decide.

Title: {title}

Thumbnail description:
{description}

Score HIGH (is_clickbait: true, confidence >= 0.80) ONLY when the description \
explicitly contains at least one of these strong signals:
  S1. Exaggerated-shock expression — mouth open, eyes wide in a clearly \
      performed/staged way (not natural surprise or enthusiasm)
  S2. Sensational overlay text — words like "SHOCKING", "EXPOSED", "YOU WON'T \
      BELIEVE", "can you spot the fake?", "GONE WRONG", or similar
  S3. Arrows, red circles, or highlight boxes pointing at something to \
      manufacture alarm
  S4. A split-panel comparison clearly designed to mislead or provoke anxiety

Score LOW (is_clickbait: false, confidence <= 0.30) when:
  - Background colour (red, bright, dark) is the only dramatic element
  - A person is present but their expression is neutral, smiling, or serious
  - The imagery matches the topic (rockets for space, spheres for astronomy, \
    a host on a news-style set, a character from a show)
  - Any "drama" is in the subject matter, not manufactured by the thumbnail design

When in doubt, score LOW. Reserve HIGH confidence for clear deception signals.

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{{"is_clickbait": true, "confidence": 0.9, "reasoning": "cite the specific signal (S1/S2/S3/S4) you found"}}
or
{{"is_clickbait": false, "confidence": 0.1, "reasoning": "one sentence"}}
"""

PROMPT_NO_TITLE = """\
You are a YouTube clickbait detector. Analyse this video thumbnail on its visual content alone.

Clickbait signals to look for:
- Shocked, exaggerated, or distressed facial expressions
- Bold text overlays making sensational claims ("YOU WON'T BELIEVE", "SHOCKING", "EXPOSED")
- Red circles, arrows, or highlight boxes drawing attention to something dramatic
- Side-by-side comparisons designed to provoke curiosity or anxiety
- AI-generated or misleading imagery used for shock value
- Overly dramatic or staged composition

Legitimate thumbnail signals:
- Clear, informative imagery that matches the topic
- Neutral or natural facial expressions
- Clean composition without manufactured drama

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{{"is_clickbait": true, "confidence": 0.9, "reasoning": "one sentence describing the visual signal(s)"}}
or
{{"is_clickbait": false, "confidence": 0.1, "reasoning": "one sentence"}}
"""


def fetch_thumbnail_b64(video_id: str) -> tuple[bytes | None, str]:
    """Fetch thumbnail, return (jpeg_bytes_or_None, status)."""
    for url_template in THUMBNAIL_URLS:
        url = url_template.format(vid=video_id)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                # YouTube returns a 120x90 placeholder for missing maxresdefault
                if len(data) < 5000 and "maxresdefault" in url:
                    continue  # too small — try next URL
                return data, "ok"
        except Exception:
            continue
    return None, "fetch_failed"


def extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
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
    return {"is_clickbait": None, "confidence": None,
            "reasoning": raw[:80], "_parse": "failed"}


def _chat(model: str, content: str, img_b64: str | None = None) -> str:
    """Single ollama.chat call. Returns raw response text."""
    msg: dict = {"role": "user", "content": content}
    if img_b64:
        msg["images"] = [img_b64]
    response = _client.chat(
        model=model,
        messages=[msg],
        options={"temperature": 0},
    )
    return response.message.content


def classify_thumbnail(model: str, video_id: str, title: str,
                        no_title: bool = False,
                        two_step: bool = False) -> tuple[dict, float, str]:
    """Fetch thumbnail and classify with vision model. Returns (result, elapsed, status)."""
    img_data, fetch_status = fetch_thumbnail_b64(video_id)
    if img_data is None:
        return {"is_clickbait": None, "confidence": None,
                "reasoning": "thumbnail unavailable"}, 0.0, fetch_status

    img_b64 = base64.b64encode(img_data).decode()
    t0 = time.monotonic()

    if two_step:
        # Step 1 — describe only what is visually present (image required)
        description = _chat(model, PROMPT_DESCRIBE, img_b64)

        # Step 2 — classify from the committed description (no image, text only)
        classify_prompt = PROMPT_CLASSIFY_FROM_DESCRIPTION.format(
            title=title,
            description=description.strip(),
        )
        raw = _chat(model, classify_prompt)
        result = extract_json(raw)
        result["_description"] = description.strip()[:200]
    else:
        prompt = PROMPT_NO_TITLE if no_title else PROMPT_WITH_TITLE.format(title=title)
        raw    = _chat(model, prompt, img_b64)
        result = extract_json(raw)

    elapsed = time.monotonic() - t0
    result["video_id"] = video_id
    result["model"]    = model
    return result, elapsed, "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="gemma3:4b",
                        help="Multimodal Ollama model (default: gemma3:4b)")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="Confidence threshold to flag as clickbait (default: 0.75)")
    parser.add_argument("--no-title", action="store_true",
                        help="Classify thumbnail visually only — omit title from prompt")
    parser.add_argument("--two-step", action="store_true",
                        help="Two-step: describe image first, then classify from description "
                             "(reduces hallucination)")
    parser.add_argument("--timeout", type=int, default=90,
                        help="Per-inference timeout in seconds (default: 90). "
                             "Two-step uses 2x this budget.")
    parser.add_argument("--time-budget", type=int, default=0,
                        help="Skip video and mark as timed-out if total elapsed "
                             "exceeds this many seconds (0 = no limit). "
                             "Useful for production use: e.g. --time-budget 120")
    args = parser.parse_args()

    global _client
    _client = OllamaClient(timeout=args.timeout)

    if args.two_step:
        mode = f"two-step (describe → classify, {args.timeout}s timeout per call)"
    elif args.no_title:
        mode = f"thumbnail-only (no title, {args.timeout}s timeout)"
    else:
        mode = f"thumbnail + title ({args.timeout}s timeout)"

    print(f"\nModel: {args.model}  |  Threshold: {args.threshold}  |  Mode: {mode}\n")
    print(f"{'Title':<52} {'Exp':>3} {'CB?':>5} {'Conf':>5} {'Time':>6}  "
          f"{'Parse':<7}  Reasoning")
    print("-" * 130)

    flagged = correct = parse_failures = 0

    for video_id, title, expected in SAMPLE_VIDEOS:
        try:
            result, elapsed, status = classify_thumbnail(
                args.model, video_id, title,
                no_title=args.no_title,
                two_step=args.two_step,
            )
        except Exception as e:
            err = str(e)[:60]
            print(f"{title[:51]:<52}  ERROR: {err}")
            continue

        if args.time_budget and elapsed > args.time_budget:
            print(f"{title[:51]:<52}  ⚠️  exceeded time budget "
                  f"({elapsed:.0f}s > {args.time_budget}s) — treated as pass")
            continue

        is_cb  = result.get("is_clickbait")
        conf   = result.get("confidence")
        reason = (result.get("reasoning") or "")[:60]
        parse  = result.get("_parse", "ok")

        if parse in ("regex-fallback", "failed"):
            parse_failures += 1

        flag = "✅YES" if (is_cb and conf is not None and conf >= args.threshold) else \
               ("YES"  if is_cb else "no")

        if is_cb and conf is not None and conf >= args.threshold:
            flagged += 1
        if is_cb == expected:
            correct += 1

        conf_str = f"{conf:.2f}" if conf is not None else "   ?"
        exp_str  = "cb" if expected else "  "
        note     = f"[{status}]" if status not in ("ok",) else ""

        print(f"{title[:51]:<52} {exp_str:>3} {flag:>5} {conf_str:>5} "
              f"{elapsed:>5.1f}s  {parse:<7}  {reason} {note}")
        if args.two_step and "_description" in result:
            print(f"  └─ saw: {result['_description'][:120]}")

    print("-" * 130)

    n = len(SAMPLE_VIDEOS)
    accuracy = correct / n * 100
    print(f"\nFlagged: {flagged}/{n}  |  Accuracy: {accuracy:.0f}%  |  "
          f"Parse failures: {parse_failures}\n")


if __name__ == "__main__":
    main()

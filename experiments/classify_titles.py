"""
experiments/classify_titles.py

Benchmark Ollama clickbait classification on a set of sample YouTube titles.
Tests structured JSON output with confidence scores and measures inference time.
Caches results by video ID so real runs never re-classify a seen video.

Requirements:
    ollama must be running locally with at least one model pulled, e.g.:
        ollama pull phi3.5
        ollama pull llama3.2:1b

Run:
    .venv/bin/python experiments/classify_titles.py
    .venv/bin/python experiments/classify_titles.py --model llama3.2:1b
    .venv/bin/python experiments/classify_titles.py --model phi3.5 --threshold 0.8
"""

import argparse
import json
import re
import time
from pathlib import Path

# Cache file — keyed by video_id, stores classification result + metadata.
# In the real implementation this will live inside processed.json.
CACHE_FILE = Path.home() / ".yt-dont-recommend" / "clickbait_cache.json"

# Sample titles with fake video IDs (simulate what feed scanner would provide)
SAMPLE_VIDEOS = [
    # (video_id, title, expected)
    # Clear clickbait
    ("vid001", "You Won't BELIEVE What Happened To Me (EMOTIONAL)", True),
    ("vid002", "I Tried This For 30 Days And My Life CHANGED Forever...", True),
    ("vid003", "They LIED To Us About This (The Truth Will SHOCK You)", True),
    ("vid004", "EXPOSING The DARK TRUTH About [Famous Person] (Gone Wrong)", True),
    ("vid005", "I'm DONE. This Changes Everything. (not clickbait)", True),
    # Borderline
    ("vid006", "I Tested 10 Viral Life Hacks (Here's What Actually Works)", False),
    ("vid007", "Why I Quit My Job After 10 Years", False),
    ("vid008", "The Problem With YouTube's Algorithm", False),
    ("vid009", "We Need To Talk About What's Happening", False),
    ("vid010", "This Is Why You're Always Tired", False),
    # Legitimate
    ("vid011", "How to Build a REST API with FastAPI and Python", False),
    ("vid012", "The History of the Roman Empire: From Republic to Fall", False),
    ("vid013", "Homemade Sourdough Bread — Full Recipe and Technique", False),
    ("vid014", "2024 Toyota Camry Review: Is It Worth Buying?", False),
    ("vid015", "Beethoven Symphony No. 9 — Berlin Philharmonic (Full)", False),
]

# Prompt designed for reliable JSON output across model sizes.
# Key changes from v1:
#   - Explicit instruction NOT to wrap in code fences
#   - JSON schema shown with concrete value examples, not meta-descriptions
#   - confidence defined as probability the title IS clickbait (0=definitely not, 1=definitely yes)
PROMPT_TEMPLATE = """\
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


def extract_json(raw: str) -> dict:
    """Robustly extract a JSON object from model output.

    Handles:
    - Clean JSON response
    - Markdown code fences (```json ... ``` or ``` ... ```)
    - JSON embedded in surrounding prose
    - Truncated responses from small models
    """
    raw = raw.strip()

    # 1. Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    # 2. Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 3. Extract first {...} block from prose
    match = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 4. Try to extract individual fields with regex as last resort
    is_cb_match  = re.search(r'"is_clickbait"\s*:\s*(true|false)', raw)
    conf_match   = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
    reason_match = re.search(r'"reasoning"\s*:\s*"([^"]+)"', raw)

    if is_cb_match:
        return {
            "is_clickbait": is_cb_match.group(1) == "true",
            "confidence":   float(conf_match.group(1)) if conf_match else None,
            "reasoning":    reason_match.group(1) if reason_match else "(extracted)",
            "_parse": "regex-fallback",
        }

    # 5. Give up
    return {"is_clickbait": None, "confidence": None, "reasoning": raw[:80], "_parse": "failed"}


def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def classify(model: str, title: str, video_id: str,
             cache: dict) -> tuple[dict, float, bool]:
    """Classify a video title. Returns (result, elapsed_seconds, from_cache).

    If video_id is already in the cache (for this model), returns immediately.
    """
    cache_key = f"{model}:{video_id}"
    if cache_key in cache:
        return cache[cache_key], 0.0, True

    try:
        import ollama
    except ImportError:
        raise ImportError("Run: .venv/bin/pip install ollama")

    prompt = PROMPT_TEMPLATE.format(title=title)
    t0 = time.monotonic()
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    elapsed = time.monotonic() - t0

    result = extract_json(response.message.content)
    result["title"]    = title
    result["video_id"] = video_id
    result["model"]    = model
    result["classified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    cache[cache_key] = result
    return result, elapsed, False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="phi3.5", help="Ollama model name")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="Confidence threshold to flag as clickbait (default: 0.75)")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore and overwrite the cache (force re-classification)")
    args = parser.parse_args()

    cache = {} if args.no_cache else load_cache()
    initial_cache_size = len(cache)

    print(f"\nModel: {args.model}  |  Threshold: {args.threshold}  |  "
          f"Cache entries: {initial_cache_size}\n")
    print(f"{'Title':<52} {'Exp':>3} {'CB?':>5} {'Conf':>5} {'Time':>5}  {'Parse':<7}  Reasoning")
    print("-" * 120)

    total_time = 0.0
    flagged = correct = parse_failures = cached_count = 0

    for video_id, title, expected in SAMPLE_VIDEOS:
        try:
            result, elapsed, from_cache = classify(args.model, title, video_id, cache)
        except Exception as e:
            print(f"{title[:51]:<52}  ERROR: {e}")
            continue

        total_time += elapsed
        is_cb  = result.get("is_clickbait")
        conf   = result.get("confidence")
        reason = result.get("reasoning", "")[:55]
        parse  = result.get("_parse", "ok")

        if parse in ("regex-fallback", "failed"):
            parse_failures += 1

        flag = "YES" if is_cb else "no"
        if is_cb and conf is not None and conf >= args.threshold:
            flagged += 1
            flag = "✅YES"

        if is_cb == expected:
            correct += 1

        conf_str   = f"{conf:.2f}" if conf is not None else "  ?"
        cache_mark = "💾" if from_cache else "  "
        exp_str    = "cb" if expected else "  "

        if from_cache:
            cached_count += 1

        print(f"{title[:51]:<52} {exp_str:>3} {flag:>5} {conf_str:>5} "
              f"{elapsed:>4.1f}s  {cache_mark}{parse:<5}  {reason}")

    print("-" * 120)
    save_cache(cache)

    n = len(SAMPLE_VIDEOS)
    non_cached = n - cached_count
    avg = total_time / non_cached if non_cached else 0.0
    accuracy = correct / n * 100

    print(f"\nFlagged: {flagged}/{n}  |  Accuracy vs expected: {accuracy:.0f}%  |  "
          f"Parse failures: {parse_failures}  |  Cached: {cached_count}  |  "
          f"Avg inference (non-cached): {avg:.1f}s\n")
    print(f"Cache saved to: {CACHE_FILE}\n")


if __name__ == "__main__":
    main()

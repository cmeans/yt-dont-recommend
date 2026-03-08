"""
experiments/classify_with_transcript.py

Two-stage clickbait classifier:
  Stage 1 — classify title only (fast)
  Stage 2 — if title confidence is ambiguous, fetch transcript and re-classify
             with title + transcript (slower but more accurate)

Decision logic:
  confidence >= threshold          → FLAGGED (clickbait)
  ambiguous_low <= conf < threshold → fetch transcript, re-classify
  confidence < ambiguous_low       → PASS (not clickbait)

When transcript is unavailable:
  --no-transcript pass        → treat as PASS (default; benefit of the doubt)
  --no-transcript flag        → treat as FLAGGED
  --no-transcript title-only  → use the title-only result as-is

Run:
    .venv/bin/python experiments/classify_with_transcript.py
    .venv/bin/python experiments/classify_with_transcript.py --model phi3.5 --threshold 0.8
    .venv/bin/python experiments/classify_with_transcript.py --no-transcript flag
    .venv/bin/python experiments/classify_with_transcript.py --no-transcript title-only --ambiguous-low 0.3
"""

import argparse
import json
import time
from pathlib import Path

from classify_titles import classify, load_cache, save_cache, CACHE_FILE, extract_json

try:
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    _api = YouTubeTranscriptApi()
except ImportError:
    _api = None

# Cap transcript at this many characters before sending to model.
# ~6000 chars ≈ 1500 tokens — enough context without overwhelming small models.
TRANSCRIPT_CHAR_LIMIT = 6000

# Sample videos with real IDs (from probe runs) + expected label.
# True = clickbait, False = not clickbait.
SAMPLE_VIDEOS = [
    # Real videos seen in feed — non-clickbait
    ("cygLFHOi9S4", "Can Starship V3 Actually Launch In 4 Weeks?",                    False),
    ("roNZr_i8iOY", "Surprise! Milky Way Might Not Have a Black Hole After All",       False),
    ("B6ZAQUqSkkA", "Rose meets the Doctor | The End of Time | Doctor Who",             False),
    ("zzTFCarWGEc", "Another Forever War Just Got the Green Light | Kat Abughazaleh",  False),
    # No transcript available (disabled) — exercises --no-transcript path
    ("0qo78R_yYFA", "SpaceX Interplanetary Transport System",                           False),
    # Real clickbait videos with transcripts — exercises transcript stage
    ("wm-AMmwtZAg", "THIS Is #1 FASTEST Way To BURN Dangerous Fat",                    True),
    ("vIJXfUy5cT4", "If YOU Can't Tell What's AI, You NEED To See This!",              True),
]


TRANSCRIPT_PROMPT = """\
You are a YouTube clickbait detector. Classify the video based on its title AND transcript excerpt.

Clickbait signals:
- Title withholds key information or uses emotional manipulation to force a click
- Title makes promises the actual content does not deliver
- Excessive capitalization or sensational framing unrelated to actual content

Title: {title}

Transcript excerpt (first ~{chars} chars):
{transcript}

Does the actual content match the title's framing, or is the title misleading/manipulative?

Reply with raw JSON only — no code fences, no explanation outside the JSON:
{{"is_clickbait": true, "confidence": 0.9, "reasoning": "one sentence"}}
or
{{"is_clickbait": false, "confidence": 0.1, "reasoning": "one sentence"}}
"""


def fetch_transcript_text(video_id: str) -> tuple[str | None, str]:
    """Fetch transcript text up to TRANSCRIPT_CHAR_LIMIT. Returns (text_or_None, status)."""
    if _api is None:
        return None, "no_api"
    try:
        fetched = _api.fetch(video_id, languages=["en", "en-US"])
        text = " ".join(seg.text for seg in fetched)
        return text[:TRANSCRIPT_CHAR_LIMIT], "ok"
    except TranscriptsDisabled:
        return None, "disabled"
    except NoTranscriptFound:
        return None, "not_found"
    except Exception as e:
        return None, f"error: {e}"


def classify_with_transcript(model: str, video_id: str, title: str,
                              transcript: str, cache: dict) -> tuple[dict, float]:
    """Classify using title + transcript. Separate cache key from title-only."""
    cache_key = f"{model}:transcript:{video_id}"
    if cache_key in cache:
        return cache[cache_key], 0.0

    try:
        import ollama
    except ImportError:
        raise ImportError("Run: .venv/bin/pip install ollama")

    prompt = TRANSCRIPT_PROMPT.format(
        title=title,
        transcript=transcript,
        chars=len(transcript),
    )
    t0 = time.monotonic()
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    elapsed = time.monotonic() - t0

    result = extract_json(response.message.content)
    result["title"]         = title
    result["video_id"]      = video_id
    result["model"]         = model
    result["stage"]         = "transcript"
    result["classified_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    cache[cache_key] = result
    return result, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="phi3.5")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="Confidence >= this → FLAGGED (default: 0.75)")
    parser.add_argument("--ambiguous-low", type=float, default=0.4,
                        help="Confidence >= this but < threshold → fetch transcript "
                             "(default: 0.4)")
    parser.add_argument("--no-transcript", choices=["pass", "flag", "title-only"],
                        default="pass",
                        help="Action when transcript unavailable: "
                             "'pass' = treat as not clickbait (default), "
                             "'flag' = treat as clickbait, "
                             "'title-only' = use title-only result")
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    cache = {} if args.no_cache else load_cache()

    print(f"\nModel: {args.model}  |  Threshold: {args.threshold}  |  "
          f"Ambiguous band: [{args.ambiguous_low}, {args.threshold})  |  "
          f"No-transcript: {args.no_transcript}\n")

    header = (f"{'Title':<50} {'Exp':>3} {'Stage':<10} {'CB?':>5} "
              f"{'Conf':>5} {'Time':>6}  Reasoning")
    print(header)
    print("-" * 120)

    flagged = correct = total = 0

    for video_id, title, expected in SAMPLE_VIDEOS:
        total += 1

        # --- Stage 1: title-only ---
        try:
            t1_result, t1_elapsed, t1_cached = classify(
                args.model, title, video_id, cache,
            )
        except Exception as e:
            print(f"{title[:49]:<50}  ERROR (stage 1): {e}")
            continue

        conf1  = t1_result.get("confidence")
        is_cb1 = t1_result.get("is_clickbait")

        # --- Decide: clear, ambiguous, or clear-pass? ---
        if conf1 is not None and conf1 >= args.threshold:
            # Clear clickbait from title alone
            final_result = t1_result
            final_elapsed = t1_elapsed
            stage = "title"

        elif conf1 is not None and conf1 >= args.ambiguous_low:
            # Ambiguous — try transcript
            transcript_text, ts_status = fetch_transcript_text(video_id)

            if transcript_text:
                try:
                    t2_result, t2_elapsed = classify_with_transcript(
                        args.model, video_id, title, transcript_text, cache
                    )
                    final_result  = t2_result
                    final_elapsed = t1_elapsed + t2_elapsed
                    stage = "title+tx"
                except Exception as e:
                    print(f"{title[:49]:<50}  ERROR (stage 2): {e}")
                    continue
            else:
                # No transcript available
                if args.no_transcript == "pass":
                    final_result  = {**t1_result, "is_clickbait": False,
                                     "confidence": 0.0,
                                     "reasoning": f"no transcript ({ts_status}); auto-pass"}
                    final_elapsed = t1_elapsed
                    stage = "no-tx→pass"
                elif args.no_transcript == "flag":
                    final_result  = {**t1_result, "is_clickbait": True,
                                     "confidence": conf1,
                                     "reasoning": f"no transcript ({ts_status}); auto-flag"}
                    final_elapsed = t1_elapsed
                    stage = "no-tx→flag"
                else:  # title-only
                    final_result  = t1_result
                    final_elapsed = t1_elapsed
                    stage = "no-tx→t1"
        else:
            # Clear pass from title alone
            final_result  = t1_result
            final_elapsed = t1_elapsed
            stage = "title"

        # --- Tally ---
        is_cb  = final_result.get("is_clickbait")
        conf   = final_result.get("confidence")
        reason = (final_result.get("reasoning") or "")[:50]
        exp_str = "cb" if expected else "  "

        flag = "✅YES" if (is_cb and conf is not None and conf >= args.threshold) else \
               ("YES"  if is_cb else "no")

        if is_cb and conf is not None and conf >= args.threshold:
            flagged += 1
        if is_cb == expected:
            correct += 1

        conf_str = f"{conf:.2f}" if conf is not None else "   ?"
        cache_mark = "💾" if t1_cached else "  "

        print(f"{title[:49]:<50} {exp_str:>3} {cache_mark}{stage:<10} {flag:>5} "
              f"{conf_str:>5} {final_elapsed:>5.1f}s  {reason}")

    print("-" * 120)
    save_cache(cache)

    accuracy = correct / total * 100 if total else 0
    print(f"\nFlagged: {flagged}/{total}  |  Accuracy: {accuracy:.0f}%\n")
    print(f"Cache saved to: {CACHE_FILE}\n")


if __name__ == "__main__":
    main()

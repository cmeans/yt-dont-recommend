"""
experiments/probe_transcript.py

Probe: fetch transcripts for a set of known video IDs and measure:
  - Fetch latency
  - Raw text length (chars + approx tokens)
  - Transcript snippet (first 300 chars)
  - Languages available

Uses the same video IDs as classify_titles.py where possible, plus a few
longer-form videos to test transcript size variance.

Run:
    .venv/bin/python experiments/probe_transcript.py
"""

import time
from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled

# (video_id, title, expected_transcript)
# Mix of classify_titles.py IDs (fake) and real YouTube video IDs.
# The classify_titles.py IDs (vid001-vid015) are fictional — skip those.
# Use real video IDs from the probe_not_interested.py runs.
PROBE_VIDEOS = [
    # Short-ish videos (< 15 min) — expect small transcripts
    ("cygLFHOi9S4", "Can Starship V3 Actually Launch In 4 Weeks?"),
    ("roNZr_i8iOY", "Surprise! Milky Way Might Not Have a Black Hole After All"),
    ("B6ZAQUqSkkA", "Rose meets the Doctor | The End of Time | Doctor Who"),
    # Borderline title from classify_titles (but use a real video to test):
    # well-known longer videos
    ("0qo78R_yYFA", "SpaceX Interplanetary Transport System"),
    ("zzTFCarWGEc", "Another Forever War Just Got the Green Light | Kat Abughazaleh"),
]

# Rough token estimate: 1 token ≈ 4 chars for English prose
CHARS_PER_TOKEN = 4


def approx_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


_api = YouTubeTranscriptApi()


def fetch_transcript(video_id: str) -> tuple[str | None, float, str]:
    """Fetch transcript text. Returns (text_or_None, elapsed_seconds, status)."""
    t0 = time.monotonic()
    try:
        fetched = _api.fetch(video_id, languages=["en", "en-US"])
        elapsed = time.monotonic() - t0
        text = " ".join(seg.text for seg in fetched)
        return text, elapsed, "ok"
    except TranscriptsDisabled:
        elapsed = time.monotonic() - t0
        return None, elapsed, "disabled"
    except NoTranscriptFound:
        elapsed = time.monotonic() - t0
        return None, elapsed, "not_found"
    except Exception as e:
        elapsed = time.monotonic() - t0
        return None, elapsed, f"error: {e}"


def list_available_languages(video_id: str) -> list[str]:
    try:
        transcript_list = _api.list(video_id)
        langs = []
        for t in transcript_list:
            tag = t.language_code
            if t.is_generated:
                tag += "(auto)"
            langs.append(tag)
        return langs
    except Exception:
        return []


def main():
    print(f"\n{'Video ID':<15} {'Chars':>7} {'~Tokens':>8} {'Time':>6}  {'Status':<12}  {'Languages'}")
    print("-" * 100)

    for video_id, title in PROBE_VIDEOS:
        text, elapsed, status = fetch_transcript(video_id)

        if text:
            chars = len(text)
            tokens = approx_tokens(text)
            langs = list_available_languages(video_id)
            lang_str = ", ".join(langs[:5])
            print(f"{video_id:<15} {chars:>7,} {tokens:>8,} {elapsed:>5.1f}s  {status:<12}  {lang_str}")
            print(f"  title:   {title[:90]}")
            print(f"  snippet: {text[:200].strip()!r}")
            print()
        else:
            langs = list_available_languages(video_id)
            lang_str = ", ".join(langs[:5]) if langs else "(none)"
            print(f"{video_id:<15} {'—':>7} {'—':>8} {elapsed:>5.1f}s  {status:<12}  {lang_str}")
            print(f"  title:   {title[:90]}")
            print()

    print("-" * 100)
    print()


if __name__ == "__main__":
    main()

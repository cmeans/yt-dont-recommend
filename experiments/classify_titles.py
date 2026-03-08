"""
experiments/classify_titles.py

Benchmark Ollama clickbait classification on a set of sample YouTube titles.
Tests structured JSON output with confidence scores and measures inference time.

Requirements:
    ollama must be running locally with at least one model pulled, e.g.:
        ollama pull phi3.5
        ollama pull llama3.2

Run:
    .venv/bin/python experiments/classify_titles.py
    .venv/bin/python experiments/classify_titles.py --model llama3.2
"""

import argparse
import json
import time

# Sample titles: mix of clear clickbait, borderline, and legitimate
SAMPLE_TITLES = [
    # Clear clickbait
    "You Won't BELIEVE What Happened To Me (EMOTIONAL)",
    "I Tried This For 30 Days And My Life CHANGED Forever...",
    "They LIED To Us About This (The Truth Will SHOCK You)",
    "EXPOSING The DARK TRUTH About [Famous Person] (Gone Wrong)",
    "I'm DONE. This Changes Everything. (not clickbait)",

    # Borderline
    "I Tested 10 Viral Life Hacks (Here's What Actually Works)",
    "Why I Quit My Job After 10 Years",
    "The Problem With YouTube's Algorithm",
    "We Need To Talk About What's Happening",
    "This Is Why You're Always Tired",

    # Legitimate
    "How to Build a REST API with FastAPI and Python",
    "The History of the Roman Empire: From Republic to Fall",
    "Homemade Sourdough Bread — Full Recipe and Technique",
    "2024 Toyota Camry Review: Is It Worth Buying?",
    "Beethoven Symphony No. 9 — Berlin Philharmonic (Full)",
]

PROMPT_TEMPLATE = """You are a YouTube clickbait detector. Analyze the given video title and determine if it is clickbait.

Clickbait characteristics:
- Withholds information to bait a click ("You won't believe...", "This will SHOCK you")
- Uses excessive capitalization or punctuation for emotional manipulation
- Makes vague, sensational, or exaggerated promises
- Uses misleading or irrelevant emotional language

Title to analyze: {title}

Respond with JSON only:
{{
  "is_clickbait": true or false,
  "confidence": float between 0.0 and 1.0,
  "reasoning": "one sentence explanation"
}}"""


def classify(model: str, title: str) -> tuple[dict, float]:
    """Run classification and return (result_dict, elapsed_seconds)."""
    try:
        import ollama
    except ImportError:
        print("ollama package not installed. Run: pip install ollama")
        raise

    prompt = PROMPT_TEMPLATE.format(title=title)
    t0 = time.monotonic()
    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0},
    )
    elapsed = time.monotonic() - t0
    raw = response.message.content.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"is_clickbait": None, "confidence": None, "reasoning": raw}

    return result, elapsed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="phi3.5", help="Ollama model name")
    parser.add_argument("--threshold", type=float, default=0.75,
                        help="Confidence threshold to flag as clickbait (default: 0.75)")
    args = parser.parse_args()

    print(f"\nModel: {args.model}  |  Threshold: {args.threshold}\n")
    print(f"{'Title':<55} {'CB?':>4} {'Conf':>5} {'Time':>5}  Reasoning")
    print("-" * 110)

    total_time = 0.0
    flagged = 0

    for title in SAMPLE_TITLES:
        try:
            result, elapsed = classify(args.model, title)
        except Exception as e:
            print(f"{title[:54]:<55}  ERROR: {e}")
            continue

        total_time += elapsed
        is_cb = result.get("is_clickbait")
        conf = result.get("confidence")
        reason = result.get("reasoning", "")[:60]

        flag = "YES" if is_cb else "no"
        if is_cb and conf and conf >= args.threshold:
            flagged += 1
            flag = "✅YES"

        conf_str = f"{conf:.2f}" if conf is not None else "  ?"
        print(f"{title[:54]:<55} {flag:>5} {conf_str:>5} {elapsed:>4.1f}s  {reason}")

    print("-" * 110)
    print(f"\nTotal: {len(SAMPLE_TITLES)} titles | Flagged: {flagged} | "
          f"Total time: {total_time:.1f}s | Avg: {total_time/len(SAMPLE_TITLES):.1f}s/title\n")


if __name__ == "__main__":
    main()

# Clickbait Detection — Experiments

Standalone scripts for prototyping the clickbait detection feature.
Nothing here touches `yt_dont_recommend.py`.

## Scripts

### `probe_not_interested.py`
Opens the YouTube home feed using your saved login session and inspects
the three-dot menu on video cards. Reports what menu items are available
and whether "Not interested" is present, along with its HTML for selector
analysis. Also extracts video titles and IDs for use with the classifier.

```bash
.venv/bin/python experiments/probe_not_interested.py
```

### `classify_titles.py`
Benchmarks Ollama clickbait classification on a set of sample titles.
Tests structured JSON output (is_clickbait, confidence, reasoning) and
measures per-title inference time. Run with different models to compare.

```bash
# Requires: ollama running locally with a model pulled
ollama pull phi3.5

.venv/bin/python experiments/classify_titles.py
.venv/bin/python experiments/classify_titles.py --model llama3.2
.venv/bin/python experiments/classify_titles.py --model gemma:2b --threshold 0.8
```

## Next steps (not yet implemented)

- `probe_transcript.py` — fetch a transcript via `youtube-transcript-api`
  and measure fetch time + token count
- `classify_with_transcript.py` — two-stage pipeline: title first, then
  title + transcript summary if ambiguous
- `probe_comments.py` — evaluate comment scraping as a tiebreaker signal

# Clickbait Detection — Experiments

Standalone scripts for prototyping the clickbait detection feature.
Nothing here touches `yt_dont_recommend.py`.

## Scripts

### `probe_not_interested.py`
Opens the YouTube home feed using your saved login session and inspects
the three-dot menu on video cards. Reports what menu items are available
and whether "Not interested" is present, along with its HTML for selector
analysis. Also extracts video titles and IDs for use with the classifier.

**Confirmed selector (2026-03-08):** `yt-list-item-view-model[role="menuitem"]`
inside `yt-sheet-view-model` inside `tp-yt-iron-dropdown`. Click target:
`button.yt-list-item-view-model__button-or-anchor`.

```bash
.venv/bin/python experiments/probe_not_interested.py
```

### `classify_titles.py`
Benchmarks Ollama clickbait classification on a set of sample titles.
Tests structured JSON output (is_clickbait, confidence, reasoning) and
measures per-title inference time. Run with different models to compare.

**Results (2026-03-08):**
- `phi3.5`: 93% accuracy, 0 parse failures, ~8s/title. **Recommended.**
- `llama3.2:1b`: 27% accuracy, flags everything, unusable.

```bash
ollama pull phi3.5

.venv/bin/python experiments/classify_titles.py
.venv/bin/python experiments/classify_titles.py --model llama3.2:1b
.venv/bin/python experiments/classify_titles.py --model phi3.5 --threshold 0.8
```

### `probe_transcript.py`
Fetches transcripts for real video IDs via `youtube-transcript-api` and
reports fetch latency, character count, approximate token count, and
available languages.

**Results (2026-03-08):** Fetch latency ~0.8–1.0s. Typical video ~1,000–1,500
tokens. ~20% of videos have transcripts disabled — plan for fallback.

```bash
.venv/bin/python experiments/probe_transcript.py
```

### `classify_with_transcript.py`
Two-stage pipeline: classify title only first; if confidence falls in the
ambiguous band, fetch the transcript and re-classify with title + excerpt.

**Options:**
- `--threshold` — flag if confidence ≥ this (default: 0.75)
- `--ambiguous-low` — fetch transcript if confidence ≥ this (default: 0.4)
- `--no-transcript {pass,flag,title-only}` — action when transcript
  unavailable (default: `pass` — benefit of the doubt)

```bash
.venv/bin/python experiments/classify_with_transcript.py
.venv/bin/python experiments/classify_with_transcript.py --no-transcript flag
.venv/bin/python experiments/classify_with_transcript.py --ambiguous-low 0.3
```

### `probe_thumbnail.py`
Classifies YouTube thumbnails using a multimodal Ollama model.
Thumbnails are fetched from YouTube's CDN (no login required).

**Recommended approach: `--two-step`** (Visual Description Grounding).
Step 1 describes what the model literally sees; step 2 classifies from
that committed description. This prevents the model from hallucinating
visual evidence to justify a title-driven label.

**Results (2026-03-08):** `gemma3:4b --two-step` achieved **100% accuracy**
on a 6-video test set at ~65s/video. Single-step and `--no-title` modes
over-flagged at 33% accuracy due to hallucination.

**S1–S4 signal taxonomy (what actually gets flagged):**
- S1: Exaggerated-shock expression (performed/staged, not natural)
- S2: Sensational text overlay ("SHOCKING", "can you spot the fake?", etc.)
- S3: Red circles, arrows, or highlight boxes manufacturing alarm
- S4: Side-by-side split panel comparing two distinct images

```bash
# Recommended
.venv/bin/python experiments/probe_thumbnail.py --model gemma3:4b --two-step --time-budget 120

# Diagnostic modes
.venv/bin/python experiments/probe_thumbnail.py --model gemma3:4b --no-title
.venv/bin/python experiments/probe_thumbnail.py --model llava:7b --two-step
```

## Confirmed findings

| Signal | Approach | Model | Accuracy | Latency |
|--------|----------|-------|----------|---------|
| Title only | Single-stage | phi3.5 | 93% | ~8s |
| Thumbnail | Two-step (describe→classify) | gemma3:4b | 100%* | ~65s |
| Transcript | Not yet benchmarked end-to-end | — | — | ~1s fetch |

\* 6-video test set; DOAC trigger case detected at 0.70 confidence (just below
0.75 threshold) due to describe step focusing on the most prominent face rather
than the full split-panel composition.

## Known limitations

- **Transcript unavailable** for ~20% of videos (disabled by creator). Default
  behaviour: treat as pass (`--no-transcript pass`).
- **Thumbnail describe step** tends to focus on the most prominent face and may
  miss secondary elements (e.g. comparison panels in the periphery).
- **Two-step thumbnail latency** (~65s/video) is acceptable for a background
  cron job but not for real-time use.
- **Local models only**: ThumbnailTruth research (arxiv:2509.04714) shows
  frontier models (Claude 3.5 Sonnet, GPT-4o) achieve 93%+ on larger datasets.
  Local models at 100% on a 6-video set should be validated on a larger sample.

## Next steps

- End-to-end test of `classify_with_transcript.py` with real video IDs
- Expand thumbnail test set (target: 20+ videos, balanced clickbait/legitimate)
- Design main app integration (`--clickbait` flag in `yt_dont_recommend.py`)
- Consider transcript + thumbnail combined pipeline

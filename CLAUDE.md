# CLAUDE.md — Project Context for Claude Code

## Session Management (READ THIS FIRST)

**If the user says `/clear` or asks to clear the conversation mid-task:**
Warn them: "This will wipe the conversation thread. Suggest `/compact` instead unless you want a genuine fresh start. Any unsaved in-progress work will be lost."

**`/compact`** — use when context is filling up. Summarizes and continues. Safe.
**`/clear`** — wipes everything. Only appropriate for a genuine fresh start.

## PR & Label Workflow

All changes ship through PRs (no direct pushes to `main`). Label transitions are driven by `.github/workflows/pr-labels.yml` + `pr-labels-ci.yml`; a `QA Gate` status check (`qa-gate.yml`) blocks merge until QA signs off.

State machine (typical PR life cycle):

1. **Dev Active** — dev is still iterating. Set manually. Blocks automatic promotion even if CI passes.
2. **Awaiting CI** — applied automatically on PR open/push. Waits for CI to complete.
3. **Ready for QA** — applied automatically once CI passes (and `Dev Active` is not set). QA can now review.
4. **QA Active** — QA sets this while actively reviewing. Dev must not push during this window; any new push resets to `Awaiting CI` and comments to notify.
5. **Ready for QA Signoff** / **QA Failed** — QA's verdict. `Ready for QA Signoff` = pass (QA's active status clears automatically); `QA Failed` sends it back to dev.
6. **QA Approved** — maintainer's final approval. Satisfies the `QA Gate` status check and makes the PR mergeable. Replaces `Ready for QA Signoff`.
7. **CI Failed** — applied automatically when CI fails. Dev fixes → new push resets to `Awaiting CI`.

Other labels:

- **`merge-order: 0..3`** — coordinate dependent PRs in a release batch (0 = infra/CI first).
- **`P0..P3`** — optional triage priority, not enforced by any workflow.
- **Domain labels** (`security`, `packaging`, `dx`, `testing`, `platform-compat`, `performance`, `code-review`, `dependencies`) — applied manually for categorisation.

**Source of truth for labels is `.github/labels.yml`.** Add or modify labels there; the `sync-labels.yml` workflow applies changes on merge to `main`. Do not create labels through the GitHub UI — they'll get overwritten next time the sync runs (or silently diverge from the file today, since `delete-other-labels` is currently `false`).

## What This Is

A Python/Playwright script that bulk-trains a YouTube account's recommendation algorithm by automating the "Don't recommend channel" action against channel blocklists. Ships with community-maintained AI slop blocklists, but supports any blocklist for any reason.

## Why It Exists

YouTube's "Don't recommend channel" is an **account-level signal** tied to the Google account, not the device. But there's no way to do it in bulk — you have to click through a menu one channel at a time. And there's no API for it.

This matters most for people who watch YouTube on devices where browser extensions don't work: Fire TV, smart TVs, Roku, game consoles, mobile apps. All existing channel-filtering tools (AiBlock, DeSlop extension, BlockTube, AI Content Shield, etc.) are browser extensions that hide content client-side — useless on those platforms.

This tool automates the account-level action on a desktop browser, which trains the algorithm everywhere the user is signed in.

No existing tool does this. This is a novel approach combining blocklists (originally designed for client-side extension filtering) with browser automation to affect the server-side recommendation engine.

## Design Goals

### Generalized blocklist support
The tool should work with **any** channel blocklist, not just AI slop lists. Use cases include:
- AI-generated content avoidance (ships built-in)
- Personal curated block lists
- Spoiler-heavy channels for specific fandoms
- Rage-bait / engagement-farming channels
- Kids' content leaking into adult feeds
- Anything else someone wants to stop seeing

### Blocklist input methods (in priority order)
1. **Local file path**: `--source path/to/my-list.txt` — simple text, one handle or `/channel/UCxxx` per line, `#` comments
2. **Arbitrary URL**: `--source https://example.com/blocklist.txt` — same text format, fetched over HTTP
3. **Built-in named sources**: `--source deslop` or `--source aislist` — community AI slop lists with format-specific parsers

The local file path is the most important for shareability. Someone curates a list, puts it in a repo or a gist, others point at it. The standard format is the DeSlop format: one channel per line (`@handle` or `UCxxx`), comments with `#`.

## End-to-End Test Cycle

> **Note:** `feed-check.py` referenced below is an informal dev script written ad-hoc during testing. It is not in the repo. The principle — confirm a target channel is visible in the home feed before running — still applies.

The full block/unblock test cycle requires these steps in order. Some steps have mandatory wait periods tied to YouTube's algorithm latency.

### Step 1 — Check the feed first
Before running the script, confirm at least one target channel is present in the home feed. Do this manually or with a quick ad-hoc script. **Do not skip this.** If no target channels are in the feed, the script will scroll through the entire feed and find nothing — which is wasted time and an inconclusive test. See Step 2 if the feed has no hits.

### Step 2 — Prime the feed (if needed)
If no target channels appear, watch and like 2–3 videos from a target channel in Chrome (the same Google account). YouTube's feed refresh typically picks this up within one feed reload, but can take up to ~10 minutes. Re-run `feed-check.py` to confirm before proceeding.

### Step 3 — Block
```
.venv/bin/yt-dont-recommend --blocklist --source /tmp/test-blocklist.txt
```
Watch the log output to confirm the channel was blocked. The script exits after exhausting the feed scroll.

### Step 4 — Verify the block on myactivity
Optionally, open `myactivity.google.com/page?page=youtube_user_feedback` in Chrome and confirm a "Don't recommend" entry exists for the channel.

### Step 5 — Trigger unblock
Empty the blocklist file (or remove the channel from it) and re-run:
```
echo "# empty" > /tmp/test-blocklist.txt
.venv/bin/yt-dont-recommend --blocklist --source /tmp/test-blocklist.txt
```
The script will detect the removal, navigate to myactivity, and prompt for Google password verification in the browser window. Once verified, it finds and deletes the "Don't recommend" entry and dismisses the "Deletion complete" dialog automatically.

> **Password verification latency**: Google requires re-authentication to access myactivity feedback entries. The browser window will show the password prompt; the script polls for up to 3 minutes. This is normal — enter the password and wait.

### Step 6 — Confirm the channel is back in the feed
Check the home feed manually (or via an ad-hoc script) immediately after the unblock completes. A hit here confirms the full cycle worked. If the channel doesn't appear, wait a few minutes and check again — YouTube may cache the "Don't recommend" signal briefly before the deletion propagates.

> **Algorithm propagation latency**: After unblocking, YouTube's recommendation engine can take a few minutes (rarely longer) to start surfacing the channel again. The myactivity deletion is instant; the feed reflection may lag.

### Cycle is only complete when Step 6 produces a hit.

---

## browser.py — ytInitialData JSON extraction

`_extract_feed_videos_from_json(page)` extracts `{video_id: {title, channel_handle}}` from `window.ytInitialData` on initial page load. Used as reliable title source for clickbait classification (avoids DOM attribute scraping noise). Only covers initial page load; scrolled cards fall back to DOM extraction automatically.

Called once in `process_channels()` after `page.goto()`, only when `_run_clickbait=True`. Returns `{}` on any failure — purely additive. Debug log `"ytInitialData: N video entries"` — N=0 means extraction failed.

JSON path: `contents.twoColumnBrowseResultsRenderer.tabs[selected].tabRenderer.content.richGridRenderer.contents[].richItemRenderer.content.videoRenderer`. Channel handle via `shortBylineText` or `ownerText` (YouTube A/B tests both).

---

## Confirmed Findings from Live Testing (2026-03-05)

### "Don't Recommend" context — RESOLVED
**"Don't recommend channel" appears ONLY in the YouTube home feed.**

Tested four contexts with `--check-selectors`:
- **Home feed** (`ytd-rich-item-renderer` cards): PASS — option present
- **Search results**: FAIL — option absent
- **Channel header (`/videos` page)**: FAIL — option absent
- **Video watch page**: FAIL — option absent

The correct `aria-label` for the home feed "More actions" button is `'More actions'`. The selector `'Action menu'` is used on channel pages but leads nowhere useful.

The tool's processing approach is the **home feed scanner**: navigate to `youtube.com`, scan `ytd-rich-item-renderer` cards for channels matching the blocklist, click the three-dot menu, select "Don't recommend channel", scroll for more cards, repeat.

### AiSList format — VERIFIED
The `aislist` source is plain text with `!` comments, ~8400+ channels. Format confirmed against the live file. Both built-in sources are verified and working.

## Architecture

`src/yt_dont_recommend/` package. Modules:

- `__init__.py` — re-exports all public names; thin browser wrappers
- `cli.py` — `main()` entry point, argument parsing, all CLI command handlers
- `config.py` — constants, file paths, selectors, `pick_viewport()`, `load_timing_config()`, `load_browser_config()`, `load_schedule_config()`, logging setup (no package imports)
- `state.py` — `load_state`, `save_state`, `write_attention`, `_had_attention`
- `blocklist.py` — `resolve_source`, `parse_*_blocklist`, `normalize_handle`, `check_removals`
- `scheduler.py` — `load_schedule`, `save_schedule`, `_compute_daily_plan`, `heartbeat`, `schedule_cmd`, platform helpers (`_schedule_linux`, `_schedule_macos`)
- `browser.py` — core Playwright automation: `process_channels`, `fetch_subscriptions`, `do_login`, `open_browser`
- `unblock.py` — `_perform_browser_unblocks`, `_pending_attempted_this_run`, `_MAX_DISPLAY_NAME_RETRIES`
- `diagnostics.py` — `check_selectors`, `_screenshot` (viewport hardcoded 1280×800 for reproducible reports)
- `clickbait.py` — clickbait detection: config loading, LLM classifiers, pipeline (see below)

Key components:

- **Blocklist fetching**: `resolve_source()` handles built-in keys, HTTP(S) URLs, and local file paths. `parse_text_blocklist()` and `parse_json_blocklist()` handle format variants. JSON format is auto-detected by leading `{` or `[`.
- **State management**: `~/.yt-dont-recommend/processed.json` tracks which channels have been handled (crash-safe, saved after each action). See State Schema below for all keys.
- **Browser automation**: Playwright with a persistent Chromium profile (login session persists between runs). Launch args: `--disable-blink-features=AutomationControlled`, `--disable-infobars`, `ignore_default_args=["--enable-automation"]`, `navigator.webdriver` stripped via `add_init_script`. Viewport randomized per session from a pool of common desktop resolutions (1280×800, 1366×768, 1440×900, 1536×864, 1600×900, 1920×1080). `main()` collects channels from all sources into a combined `{channel: source}` dict (no browser needed for this), then opens one browser session via `open_browser()` and calls `process_channels()` once — a single feed scan covers all sources. Avoids repeated auth checks and scroll passes.
- **Subscription protection**: `fetch_subscriptions(page)` scrapes `youtube.com/feed/channels`, returns a lowercase set of handles. Called once per run in `process_channels()`. Subscribed channels are skipped with a one-time WARNING logged and stored in `state["would_have_blocked"]`.
- **Blocklist removal detection**: `check_removals()` runs at the start of each `process_channels()` call, compares the current list against `state["blocked_by"]`, and auto-unblocks channels no longer on the list, per `--unblock-policy`.
- **Blocklist growth tracking**: Each run records source list sizes in `state["source_sizes"]`. When a source has grown since the last run, it logs prominently.
- **Attention/notification system**: `write_attention(message)` writes `needs-attention.txt`, fires a desktop notification (`osascript`/`notify-send`), and sends an ntfy.sh push if configured. Triggered by: selector failure, expired login session, unblock selector failure, auto-upgrade failure. Auto-cleared on the next successful run.
- **Version tracking**: At startup, the running version is compared to `state["current_version"]`; on change, the old value is rotated to `state["previous_version"]`. This makes `--revert` work regardless of whether the upgrade was automatic or manual. **Tested 2026-03-08**: manual upgrade 0.1.9→0.1.16→0.1.17, `--revert` correctly dropped back to 0.1.16, auto-upgrade was disabled automatically.
- **Logging**: `RotatingFileHandler` — `run.log` caps at 1 MB with 5 backups (`run.log.1`–`run.log.5`).
- **Rate limiting**: All interaction delays are jittered with `random.uniform()`. Defaults: 3–7s between actions, 30s pause every 25 channels (±20%), 1.0–2.5s scroll. All timing overridable via `~/.yt-dont-recommend/config.yaml` (`timing:` section). Per-session action cap of 75 by default; use `--no-limit` to remove it.
- **Browser selection**: `_launch_context()` in `browser.py` tries `channel="chrome"` (system Chrome) first for authentic UA/Client Hints, falling back to bundled Chromium. Controlled by `browser.use_system_chrome` in `config.yaml` (default: `true`). `load_browser_config()` in `config.py` reads this setting.

### Clickbait Detection Module (`clickbait.py`)

Standalone detection pipeline. Optional runtime deps (`pip install yt-dont-recommend[clickbait]`):
- `ollama` — local LLM inference
- `pyyaml` — YAML config file
- `youtube-transcript-api` — transcript fetching

**Config file**: `~/.yt-dont-recommend/clickbait-config.yaml` (copy from `clickbait-config.example.yaml`). Falls back to built-in defaults when absent or unparseable.

**Default config schema:**
```yaml
video:
  title:
    model: {name: llama3.1:8b, params: {}, auto_pull: false}
    threshold: 0.75
    ambiguous_low: 0.4
  thumbnail:
    enabled: false           # opt-in — slow (~65s/video)
    model: {name: gemma3:4b, params: {}, auto_pull: false}
    threshold: 0.75
    two_step: true           # Visual Description Grounding (recommended)
    timeout: 90
    time_budget: 120
  transcript:
    enabled: false           # opt-in
    model: {name: phi3.5, params: {}, auto_pull: false}
    threshold: 0.75
    no_transcript: pass      # pass | flag | title-only
```

**`auto_pull`**: when `true`, the model is pulled automatically via `ollama.pull()` if not already present. The tool fast-fails with an error if the pull fails (e.g. ollama not running). Default: `false`.

**Pipeline** (`classify_video(video_id, title, cfg)`):
1. Title classification (always)
2. Thumbnail (if `enabled: true` and title confidence in `[ambiguous_low, threshold)`)
3. Transcript (if `enabled: true` and still ambiguous after previous stages)

**Result keys**: `video_id`, `title`, `is_clickbait`, `confidence`, `flagged`, `stages`, `title_result`, `thumbnail_result`, `transcript_result`, `classified_at`.

**Proven benchmarks (2026-03-08)**:
- `llama3.1:8b` title: improved false-positive rate on news/opinion vs phi3.5; uses full confidence range (0.05–0.95). ~8s/title. Some residual false positives on news interview content — see Known Issues.
- `phi3.5` title (legacy): 93% accuracy, ~8s/title, 0 parse failures; binary scoring (0.10/0.80 only)
- `gemma3:4b` thumbnail two-step: 100% accuracy on 6-video set, ~65s/video

**`_pkg()` pattern**: sub-modules use late import of `yt_dont_recommend` for names that tests patch. `__init__.py` re-exports all public names so `patch("yt_dont_recommend.X")` still works for external callers. Functions that live in `cli.py` must be patched as `yt_dont_recommend.cli.X` in tests.

### Schedule JSON Schema

`~/.yt-dont-recommend/schedule.json` — written by `--schedule install`, read/updated by `--heartbeat` every minute. Separate from state.json (scheduling concerns only).

```json
{
    "modes": {
        "blocklist": {"runs_per_day": 2},
        "clickbait": {"runs_per_day": 4}
    },
    "headless": true,
    "installed_at": "2026-03-11T14:00:00+00:00",
    "today": {
        "date": "2026-03-11",
        "blocklist": {
            "planned_utc": ["03:17", "15:44"],
            "executed_utc": ["03:17"]
        },
        "clickbait": {
            "planned_utc": ["01:12", "07:33", "13:44", "20:01"],
            "executed_utc": ["01:12", "07:33"]
        }
    }
}
```

Key behaviours:
- `planned_utc` recomputed fresh each UTC day via `_compute_daily_plan(runs_per_day)` — divides 24h into N equal windows, picks a random minute in each. Different times every day (jitter by design).
- A mode is "due" when any planned HH:MM <= now HH:MM (UTC) and that time is not in `executed_utc`.
- Simultaneously due modes are combined into one subprocess invocation (one browser session).
- `executed_utc` is written **before** the subprocess is spawned. Failed spawns are silently dropped — the slot is not retried.
- All timestamps are UTC (Zulu). String comparison on zero-padded "HH:MM" is lexicographically correct for same-day comparisons.
- Written atomically (write to `.tmp`, then rename).
- Config defaults (`blocklist_runs`, `clickbait_runs`, `headless`) live in `config.yaml` under `schedule:` section, loaded by `load_schedule_config()` in config.py.

### State Schema

```json
{
  "blocked_by": {
    "@channel1": {"sources": ["deslop"], "blocked_at": "2026-03-05T...", "display_name": "Channel Name"}
  },
  "would_have_blocked": {
    "@SomeChannel": {"sources": ["deslop"], "first_seen": "...", "notified": true}
  },
  "last_run": "...",
  "stats": {"total_blocked": 1, "total_skipped": 0, "total_failed": 0},
  "source_sizes": {"deslop": 121, "aislist": 8400},
  "ucxxx_to_handle": {"UCxxx...": "@handle"},
  "pending_unblock": {
    "@channel1": {"sources": ["deslop"], "blocked_at": "...", "_retry_count": 1}
  },
  "notify_topic": "ydr-<random-hex>",
  "last_version_check": "2026-03-08T...",
  "latest_known_version": "0.1.13",
  "notified_version": "0.1.13",
  "auto_upgrade": false,
  "previous_version": "0.1.12",
  "current_version": "0.1.13",
  "clickbait_cache": {
    "<video_id>": {"is_clickbait": false, "confidence": 0.62, "flagged": false, "title": "...", "channel": "@handle", "cached_at": "2026-03-12T..."}
  },
  "clickbait_acted": {
    "<video_id>": {"acted_at": "2026-03-12T...", "title": "...", "channel": "@handle"}
  },
  "state_version": 3
}
```

`load_state()` is backward-compatible: missing keys are populated via `setdefault`. If `state_version` in the file exceeds the binary's `STATE_VERSION` constant, a warning is logged (state was written by a newer binary — occurs after `--revert`).

**v2 migration**: `load_state()` drops the legacy `"processed"` key via `s.pop("processed", None)` when loading old state files. `blocked_by.keys()` is now the sole authoritative record of blocked channels.

**v3 additions**: `clickbait_cache` and `clickbait_acted` (both default to `{}`).
- `clickbait_cache`: cross-run classification cache keyed by video_id. Entries expire after `CLICKBAIT_CACHE_TTL_DAYS` (14 days). Loaded into `_title_cache` at the start of each run to skip re-evaluation of recently seen videos.
- `clickbait_acted`: videos successfully marked "Not interested", keyed by video_id. Used for shadow-limiting detection — if a previously-acted video reappears more than `SHADOW_LIMIT_GRACE_HOURS` (48h) later, it counts as a suspicious re-encounter. After `SHADOW_LIMIT_WARN_AFTER` (2) such hits in a run, the tool stops and calls `write_attention()`. Entries older than `CLICKBAIT_ACTED_PRUNE_DAYS` (90 days) are pruned on load.

`pending_unblock` entries carry an internal `_retry_count` sub-key (prefixed `_` to indicate it is not part of the public schema). It tracks consecutive display-name lookup failures for that channel. After `_MAX_DISPLAY_NAME_RETRIES` (3) failures the channel is removed from `pending_unblock` automatically. This key does not require a `STATE_VERSION` bump — old binaries ignore it.

### State Schema Policy

**Never rename, remove, or reinterpret existing keys.** Only add new ones.

#### Checklist for every state schema change

1. **Add** the new key — do not rename or remove existing keys
2. **`setdefault`** the new key in `load_state()` (existing state files need a safe default)
3. **Add** the new key to the fresh-state `return` dict at the bottom of `load_state()`
4. **Bump `STATE_VERSION`** in `src/yt_dont_recommend/config.py` (the integer constant near the bottom)
5. **Update the State Schema** block above in this file
6. **Add a test** covering the new key's default value

This ensures old binaries (post-revert) can always read state written by newer ones — they ignore unknown keys and log a warning if `state_version` in the file exceeds what they expect.

### Key Function Signatures

```python
def open_browser(headless: bool = False) -> tuple | None:
    # Returns (pw_cm, context, page) or None if not logged in.

def close_browser(handle: tuple) -> None:

def process_channels(channel_sources: dict[str, str],
                     to_unblock: list[str] | None = None,
                     state: dict | None = None,
                     dry_run: bool = False,
                     limit: int | None = None,  # None → apply DEFAULT_SESSION_CAP; sys.maxsize → no cap (--no-limit)
                     headless: bool = False,
                     _browser: tuple | None = None) -> None:
    # channel_sources: {canonical_handle: source_name} — all unprocessed channels
    # from all sources merged by main(). Single feed scan covers everything.

def check_removals(state: dict, current_channels: list[str],
                   source: str, unblock_policy: str) -> list[str]:

def fetch_subscriptions(page) -> set[str]:
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `--login` | Open browser for Google account authentication |
| `--blocklist` | Run channel-level "Don't recommend channel" blocking. **Required** to enable blocklist mode; running without `--blocklist` or `--clickbait` shows help. |
| `--source` | Blocklist source(s) to use with `--blocklist`. Built-in names (comma-separated), local file path, or HTTP(S) URL. Defaults to all built-in sources. |
| `--exclude` | Channels to never block via `--blocklist`. Local file path or HTTP(S) URL. Auto-loads `~/.yt-dont-recommend/blocklist-exclude.txt` (legacy: `exclude.txt` accepted with deprecation warning) |
| `--clickbait-exclude` | Channels to never evaluate for clickbait. Local file path or HTTP(S) URL. Auto-loads `~/.yt-dont-recommend/clickbait-exclude.txt` |
| `--limit N` | Stop after N actions (default cap: 75 per session) |
| `--no-limit` | Remove the per-session action cap for this run |
| `--dry-run` | Show what would be processed without acting (combine with `--blocklist` or `--clickbait`) |
| `--headless` | Run without a visible browser window |
| `--clickbait` | Scan feed videos for clickbait titles and click "Not interested" (video-level; no channel-level effect). Requires `pip install yt-dont-recommend[clickbait]`. Config: `~/.yt-dont-recommend/clickbait-config.yaml`. |
| `--unblock-policy {all,any}` | When to auto-unblock channels removed from lists (default: `all`) |
| `--stats` | Show blocked count, per-source breakdown, success/skip/fail totals, and `would_have_blocked` entries |
| `--export-state [FILE]` | Dump all blocked channels as a standard plain-text blocklist with source annotations; writes to FILE or stdout |
| `--reset-state` | Clear all state and start over |
| `--list-sources` | Print built-in source names |
| `--check-selectors` | Run 4-context selector diagnostic, save report + screenshots |
| `--test-channel` | Channel to use with `--check-selectors` (default: `@YouTube`) |
| `--clear-alerts` | Clear the `needs-attention.txt` flag file |
| `--check-update` | Force a PyPI version check and print result |
| `--auto-upgrade enable\|disable` | Enable or disable automatic upgrades when a new version is detected |
| `--revert [VERSION]` | Revert to the previously recorded version, or to a specific version if supplied |
| `--setup-notify` | Generate a private ntfy.sh topic and show subscribe instructions |
| `--remove-notify` | Remove the configured ntfy.sh topic |
| `--test-notify` | Send a test push notification |
| `--schedule install\|remove\|status` | Manage scheduled runs via launchd (macOS) or cron (Linux). Installs an every-minute heartbeat that fires at randomised UTC times each day. |
| `--blocklist-runs N` | Times per day to run blocklist mode (used with `--schedule install`). Omitting = 0 (not scheduled). |
| `--clickbait-runs N` | Times per day to run clickbait mode (used with `--schedule install`). Omitting = 0 (not scheduled). |
| `--heartbeat` | Internal: fast shim called every minute by cron/launchd. Checks schedule.json, spawns full run if due. |
| `--uninstall` | Remove schedule, offer to delete data dir, print package manager uninstall command |
| `--version` | Print installed version and exit |
| `--verbose` | Extra logging |

## Standard Blocklist Format

The interchange format is plain text:
```
# Comments start with #
# Blank lines are ignored
# Entries are YouTube channel paths:
@SomeHandle
@AnotherChannel
UCxxxxxxxxxxxxxxxxxxxxxx
```

This matches the DeSlop format and is trivial to create, share, and parse.

## Built-in Blocklist Sources

| Key       | Format | Verified | URL |
|-----------|--------|----------|-----|
| `deslop`  | text   | YES      | `https://raw.githubusercontent.com/NikoboiNFTB/DeSlop/refs/heads/main/block/list.txt` |
| `aislist` | text   | YES      | `https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/aislist_blocklist.txt` |

DeSlop has ~130+ channels. AiSList has ~8400+ channels.

Other potential sources to consider adding:
- surasshu/cevval AI music blocklist (BlockTube JSON export with channel IDs)
- Any future community lists that adopt the standard text format

## Open Issues & Risks

### 1. Selector Fragility (ongoing)
YouTube changes its DOM frequently. The script detects broken selectors automatically: if 3+ consecutive feed passes each contain 10+ cards but yield zero parseable channel links, it logs a `WARNING: POSSIBLE SELECTOR FAILURE` and exits early rather than silently wasting all scroll passes. Use `--check-selectors` to diagnose. The selector checker saves a timestamped report and screenshots to `~/.yt-dont-recommend/`.

### 2. Home Feed Matching Completeness
The tool can only block channels that appear in the home feed during a run. A channel on the blocklist that never surfaces won't be processed until a future run where it does. This is a fundamental limitation of the home-feed-only approach.

### 3. Handle vs. Channel ID Matching — RESOLVED
Live probe confirmed modern YouTube feed cards expose `@handle` links only — no `/channel/UCxxx` links appear at the card level. Both built-in sources (deslop, aislist) use `@handle` format. For custom blocklists that use UCxxx IDs, `resolve_ucxxx_to_handles()` is called automatically before the feed scan: it visits `youtube.com/channel/UCxxx` for each unresolved ID, captures the `@handle` from the redirect, and caches the mapping in `state["ucxxx_to_handle"]`. No UCxxx-format entries reach the feed scanner.

### 4. Subscription Scraping — VERIFIED (2026-03-06)
`fetch_subscriptions()` uses selectors `ytd-channel-renderer a#main-link` and scrolling on `youtube.com/feed/channels`. Live-tested: 134 subscriptions found correctly in 1 scroll pass. If subscription protection silently fails in future, these selectors are the first place to look.

### 5. Rate Limiting
The current delays (3–7s between actions, 30s every 25 channels) are conservative guesses. Back off further if YouTube shows CAPTCHAs or unusual behavior.

### 6. YouTube ToS
Automating UI interactions violates YouTube's Terms of Service. This is for personal use on the user's own account — same risk category as SmartTube or ad blockers.

## Original Developer's Environment

- Fedora 43 (username: cmeans)
- Python 3.x with `.venv` in project root
- Playwright: `pip install playwright && playwright install chromium` (inside venv)
- Git remote: `https://github.com/cmeans/yt-dont-recommend.git` (HTTPS; SSH had timeout issues)

## Research Sources & Attribution Policy

**Policy**: Whenever any external resource — paper, article, blog post, Stack Overflow answer, dataset, technique, tool, or anything else — materially informs a design decision, implementation, or prompt, log it here before the session ends. This applies broadly, not just to academic or AI-related work. We do not use other people's work without credit. Update README.md Acknowledgments where appropriate.

### Logged sources

| Source | Used for | Citation |
|--------|----------|----------|
| ThumbnailTruth | Thumbnail clickbait detection design; established that multi-modal LLMs achieve 93%+ accuracy combining visual + textual signals; informed thumbnail classification stage | Naveed, Uzmi & Qazi. *ThumbnailTruth: A Multi-Modal LLM Approach for Detecting Misleading YouTube Thumbnails Across Diverse Cultural Settings.* arXiv:2509.04714, Sep 2025. https://arxiv.org/abs/2509.04714 |
| Visual Description Grounding | Two-step thumbnail pipeline (describe literally → classify from description) to prevent vision model hallucination | General technique in vision-language model literature; no single paper identified yet — flag if a specific source is found |

## What NOT To Do

- Don't run headless until you've confirmed the selectors are working with a visible browser
- Don't process the full list until rate limiting behavior is understood
- Don't assume selectors work just because they look reasonable — YouTube's DOM is notoriously inconsistent

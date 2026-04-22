# YouTube "Don't Recommend Channel" Bulk Trainer

[![PyPI version](https://img.shields.io/pypi/v/yt-dont-recommend)](https://pypi.org/project/yt-dont-recommend/)
[![Python versions](https://img.shields.io/pypi/pyversions/yt-dont-recommend)](https://pypi.org/project/yt-dont-recommend/)
[![License](https://img.shields.io/pypi/l/yt-dont-recommend)](https://github.com/cmeans/yt-dont-recommend/blob/main/LICENSE)
[![CI](https://img.shields.io/github/actions/workflow/status/cmeans/yt-dont-recommend/ci.yml?label=CI)](https://github.com/cmeans/yt-dont-recommend/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/cmeans/yt-dont-recommend/graph/badge.svg)](https://codecov.io/gh/cmeans/yt-dont-recommend)
[![Downloads](https://img.shields.io/pypi/dm/yt-dont-recommend)](https://pypi.org/project/yt-dont-recommend/)

> **Early development / alpha:** This tool is functional and used daily by its author, but it is under active development with frequent releases. Breaking changes between versions are possible. **Auto-upgrade is not recommended** until the tool stabilises — enable it only if you are comfortable testing new features as they land and are willing to use `--revert` when something goes wrong.

Automates YouTube's "Don't recommend channel" action in bulk, using any channel blocklist you provide. Because the signal is tied to your **Google account** (not the device), it trains the algorithm everywhere you're signed in — including Fire TV, mobile apps, smart TVs, and game consoles.

No browser extension can do this. Extensions filter content client-side on a single browser. This tool affects the server-side recommendation engine.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux    | ✅ Tested and confirmed working (Fedora 43) |
| macOS    | ⚠️ Implemented and CI-verified (install, import, CLI), but no end-to-end run confirmed yet — looking for a volunteer! |
| Windows  | ❌ Not supported |

Python 3.10 or later is required on all platforms.

**macOS testers wanted:** If you're on a Mac and willing to try it, install instructions are the same as Linux. If it works, please [open an issue](https://github.com/cmeans/yt-dont-recommend/issues) and let us know your macOS version — it would let us mark macOS as fully supported.

## Install

**With [uv](https://docs.astral.sh/uv/):**

```bash
uv tool install yt-dont-recommend
uvx playwright install chromium    # one-time: installs the Chromium browser
yt-dont-recommend --login
```

**With [pipx](https://pipx.pypa.io/):**

```bash
pipx install yt-dont-recommend
playwright install chromium
yt-dont-recommend --login
```

After `--login` your session is saved to `~/.yt-dont-recommend/browser-profile/` and reused automatically.

On the very first run the tool will print a quick-start reminder with the recommended next steps.

### Clickbait detection extras

> **Experimental feature.** `--clickbait` is functional but the classification quality depends heavily on the model and feed content. Expect false positives, especially on news and interview titles. The detection pipeline, config schema, and default model are likely to change across releases. Benchmark results and probe scripts live in the [`experiments/`](experiments/) directory.

`--clickbait` requires [Ollama](https://ollama.com) (a local LLM runtime) plus additional Python dependencies.

**Step 1 — Install Ollama** by following the instructions at [ollama.com](https://ollama.com). Ollama must be running when `--clickbait` is used.

**Step 2 — Pull the required model(s):**

```bash
# Title classification (required — fast, ~8s/title)
ollama pull llama3.1:8b

# Thumbnail classification (optional — slow, ~65s/video; only needed if you enable it in config)
ollama pull gemma3:4b
```

Alternatively, set `auto_pull: true` under `model:` in your `clickbait-config.yaml` and the model will be pulled automatically on first use.

**Step 3 — Install the Python extras** the same way you installed the tool:

**With uv:**
```bash
uv tool install 'yt-dont-recommend[clickbait]'
```

**With pipx:**
```bash
pipx install 'yt-dont-recommend[clickbait]'
```

For configuration (model selection, thresholds, thumbnail and transcript stages), see the [clickbait configuration file](clickbait-config.example.yaml) — copy it to `~/.yt-dont-recommend/clickbait-config.yaml` and edit to taste.

---

## Upgrading

**With uv:**

```bash
uv tool install yt-dont-recommend@latest
```

**With pipx:**

```bash
pipx upgrade yt-dont-recommend
```

Your session and state (`~/.yt-dont-recommend/`) are preserved. If Playwright warns that the browser binary is outdated after an upgrade, re-run the install command for your package manager.

**With uv:**

```bash
uvx playwright install chromium
```

**With pipx:**

```bash
playwright install chromium
```

---

## Uninstall

Run the built-in uninstall helper first — it removes the schedule and optionally deletes your data:

```bash
yt-dont-recommend --uninstall
```

It will walk you through three steps:

1. Remove the automatic schedule (launchd or cron)
2. Optionally delete `~/.yt-dont-recommend/` (session, state, logs)
3. Print the package manager command to remove the package itself

The final uninstall command (printed by the tool) will be one of:

```bash
uv tool uninstall yt-dont-recommend
```

```bash
pipx uninstall yt-dont-recommend
```

---

## Development Setup

Requires Python 3.10+ and Git.

**With [uv](https://docs.astral.sh/uv/) (recommended):**

```bash
git clone https://github.com/cmeans/yt-dont-recommend.git
cd yt-dont-recommend
uv sync
uv run playwright install chromium
uv run yt-dont-recommend --login
```

**With pip/venv:**

```bash
git clone https://github.com/cmeans/yt-dont-recommend.git
cd yt-dont-recommend
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
yt-dont-recommend --login
```

A browser window opens — sign into your Google account, then close it. Your session is saved to `~/.yt-dont-recommend/browser-profile/` and reused on every subsequent run.

> **Debian/Ubuntu:** after installing Chromium, you may also need system dependencies:
> - uv tool: `uvx playwright install-deps chromium`
> - uv (dev): `uv run playwright install-deps chromium`
> - pipx / pip/venv: `playwright install-deps chromium`

**Running tests and linting:**

```bash
uv run pytest tests/ -v        # unit tests (251+)
uv run ruff check src/ tests/  # lint
bash scripts/smoke-test.sh     # CLI integration smoke test
```

## Usage

For a full list of options:

```bash
yt-dont-recommend --help
```

Running without `--blocklist` or `--clickbait` prints help. Choose one or both:

```bash
# Channel-level: "Don't recommend channel" for every channel on the blocklist
yt-dont-recommend --blocklist

# Video-level: scan the feed for clickbait titles and click "Not interested"
# (requires clickbait extras — see Clickbait Detection Extras under Install)
yt-dont-recommend --clickbait

# Both at once
yt-dont-recommend --blocklist --clickbait
```

**Start small on your first real run** — use `--limit 10` to confirm everything is working before processing a full list:

```bash
yt-dont-recommend --blocklist --limit 10
```

```bash
# Dry run — see what would be processed without clicking anything
yt-dont-recommend --blocklist --dry-run

# Dry run for clickbait — see what would be flagged without clicking anything
yt-dont-recommend --clickbait --dry-run

# Use a specific built-in source
yt-dont-recommend --blocklist --source deslop
yt-dont-recommend --blocklist --source aislist

# Use multiple sources (comma-separated)
yt-dont-recommend --blocklist --source deslop,aislist

# Use a local blocklist file
yt-dont-recommend --blocklist --source /path/to/my-list.txt

# Use a remote blocklist URL
yt-dont-recommend --blocklist --source https://example.com/blocklist.txt

# Protect specific channels from ever being blocked (overrides the default exclude file)
yt-dont-recommend --blocklist --exclude ~/.yt-dont-recommend/blocklist-exclude.txt

# Run in headless mode (no visible browser)
yt-dont-recommend --blocklist --headless

# Check progress — per-source breakdown, totals, and subscription-protected channels
# "skipped" = appeared in feed but menu action failed; "failed" = error during attempt
yt-dont-recommend --stats

# Export blocked channels as a plain-text blocklist (stdout)
yt-dont-recommend --export-state

# Export to a file
yt-dont-recommend --export-state ~/my-blocks.txt

# Control when a channel is auto-unblocked after being removed from a list
yt-dont-recommend --unblock-policy all   # default: unblock only when gone from all sources
yt-dont-recommend --unblock-policy any   # unblock as soon as gone from any source

# Start over
yt-dont-recommend --reset-state

# List built-in sources
yt-dont-recommend --list-sources
```

## Exclusion Lists

There are two separate exclusion files, each serving a different purpose:

| File | Purpose |
|------|---------|
| `~/.yt-dont-recommend/blocklist-exclude.txt` | Channels to never block via `--blocklist`, regardless of what community lists say |
| `~/.yt-dont-recommend/clickbait-exclude.txt` | Channels to never evaluate for clickbait via `--clickbait` |

Both are loaded automatically when present — no flag required. Both use the same plain-text format as blocklists and support inline `#` comments:

```
# blocklist-exclude.txt — channels to keep despite appearing on community lists
@SomeChannel
@FriendsChannel  # friend's channel — keep it

# clickbait-exclude.txt — channels whose titles should never be flagged
@katmabu  # congressional campaign — want to see all content regardless of title framing
```

To use a different file (or a remote URL) instead of the default:

```bash
# Override blocklist exclusions
yt-dont-recommend --blocklist --exclude /path/to/my-blocklist-exclusions.txt

# Override clickbait exclusions
yt-dont-recommend --clickbait --clickbait-exclude /path/to/my-clickbait-exclusions.txt
```

Both flags also accept HTTPS URLs. Neither accepts built-in source names.

> **Migrating from `exclude.txt`:** If you have an existing `~/.yt-dont-recommend/exclude.txt`, rename it to `blocklist-exclude.txt`. The old name still works but logs a deprecation warning.

## Subscription Protection

The tool automatically skips any channel you are subscribed to — even if it appears on a blocklist. Blocking a channel you subscribe to would signal YouTube to stop recommending it, which is usually not what you want.

When a subscribed channel appears on the blocklist, a `WARNING` is logged and the event is recorded in state under `would_have_blocked`. This warning fires only once per channel (not on every run). Use `--stats` to see the full list.

If a channel you subscribe to genuinely should be blocked, add it to your blocklist exclusion file (`~/.yt-dont-recommend/blocklist-exclude.txt`) to suppress the warning, or unsubscribe and let the tool handle it on the next run.

## Auto-Unblock (False Positive Correction)

When a channel is removed from a blocklist, the tool can automatically reverse the "Don't recommend channel" action.

**`--unblock-policy all` (default):** Unblock only when the channel has been dropped from *every* source that originally blocked it. Useful when running multiple lists — a channel removed from one aggressive list but still present in another stays blocked.

**`--unblock-policy any`:** Unblock as soon as the channel disappears from *any* source that blocked it. More aggressive about reversing false positives.

Auto-unblock events are logged prominently so they are easy to spot.

## Blocklist Format

Plain text, one channel per line. Full-line comments start with `#`. Inline `#` comments are also supported.

```
# My custom blocklist
@SomeHandle
@AnotherChannel           # optional note about why this is here
UCxxxxxxxxxxxxxxxxxxxxxxxx
```

This format is shared with the [DeSlop](https://github.com/NikoboiNFTB/DeSlop) project. You can point `--source` at any file or URL using this format, or at JSON files using common channel object schemas.

## Built-in Sources

| Source    | Description                                                       |
|-----------|-------------------------------------------------------------------|
| `deslop`  | [DeSlop](https://github.com/NikoboiNFTB/DeSlop) project (~130+ channels, plain text, actively maintained)  |
| `aislist` | [AiSList](https://github.com/Override92/AiSList) community text list (~8400+ channels, broader)            |

Running `--blocklist` without `--source` processes all built-in sources. The state tracker prevents re-processing the same channel twice across sources or runs.

## How It Works

> **About Playwright and selectors:** [Playwright](https://playwright.dev) is a browser automation library that drives a real Chromium browser — the same engine as Google Chrome. The tool uses it to click YouTube's menus exactly as a human would, using your saved login session. "Selectors" are the CSS/HTML patterns used to locate specific buttons and links on the page (e.g. the "More actions" button on a video card). When YouTube updates its site design, these patterns can break without warning — which is why `--check-selectors` exists and why the tool detects and alerts on selector failures automatically.

1. Fetches the blocklist (local file, URL, or built-in source)
2. Logs if the source has grown since the last run (new channels added by list maintainers)
3. Checks whether any previously blocked channels have since been removed from the list and auto-unblocks them per `--unblock-policy`
4. Opens Chromium using your saved YouTube login session
5. Scrapes your subscriptions so subscribed channels are never blocked
6. Scans the YouTube home feed for cards matching blocklist channels
7. For each match:
   - Clicks the "More actions" menu on the video card
   - Clicks "Don't recommend channel"
   - Saves progress immediately (crash-safe, always resumable)
8. Scrolls for more cards and repeats until the list is exhausted or `--limit` is reached
9. Rate-limits itself: 3–7s between actions, 30s break every 25 channels

> **Why the home feed?** Live testing confirmed that "Don't recommend channel" only appears in home feed recommendation contexts. It does not appear on a channel's own `/videos` page, in search results, or on the video watch page.

## State & Logs

All data lives in `~/.yt-dont-recommend/`:

| Path | Purpose |
|------|---------|
| `browser-profile/` | Chromium profile with your login session (Google cookies, local storage) |
| `processed.json` | Channels already handled, blocked-by source tracking, subscription warnings, notification topic |
| `run.log` | Timestamped log of all actions (rotates at 1 MB, 5 backups kept) |
| `needs-attention.txt` | Alert flag written when action is required (e.g. selector failure, expired login session); auto-cleared on a successful run |
| `config.yaml` | Optional: override timing delays, per-session cap, browser behaviour, and DOM selectors. Copy from [`config.example.yaml`](config.example.yaml). Requires `pyyaml` (`pip install pyyaml`). |
| `clickbait-config.yaml` | Optional: configure clickbait model, thresholds, and pipeline stages. Copy from [`clickbait-config.example.yaml`](clickbait-config.example.yaml). |

**Security:** The data directory is created with owner-only permissions (`chmod 700`). The `browser-profile/` contains your Google login session cookies — anyone with read access to this directory can act as your Google account. Existing installations with overly permissive directories are automatically fixed on the next run.

**Disk usage:** Browser cache is automatically cleared after each run, keeping the profile at ~10 MB (session data only). Without cleanup, Chromium caches can grow to 400+ MB.

## Caveats

- **YouTube ToS:** Automating UI interactions may violate YouTube's Terms of Service. Personal use, your own account, your own risk.
- **Selector fragility:** YouTube's HTML structure changes frequently. The script detects broken selectors automatically — if several consecutive scroll passes yield no parseable channel links, it writes an alert and exits early. The alert is shown prominently on the next interactive run and cleared automatically once a successful run confirms the selector is working again. Run `--check-selectors` to diagnose and get a timestamped report with screenshots.
- **Home feed matching:** The tool can only block channels that appear in your home feed during a run. Channels on the blocklist that never surface in the feed during that session will not be processed. Resume runs until the list is exhausted.
- **Handle vs. channel ID:** YouTube feed cards expose `@handle` links only — `UCxxx` IDs in a blocklist are automatically resolved to `@handles` before scanning. Results are cached in state so re-resolution is skipped on subsequent runs. Both built-in sources already use `@handle` format; this only applies to custom blocklists.
- **Session cap:** By default, each run caps at 75 actions (blocks + clickbait marks combined) to keep sessions human-length. Use `--no-limit` to remove the cap for a single run, or set `session_cap` in `~/.yt-dont-recommend/config.yaml`.

## Automatic Safeguards

### Shadow-limiting detection (clickbait mode)

YouTube may practice *shadow-limiting*: silently accepting "Not interested" signals without acting on them, so the same videos keep appearing in your feed. If this happens, continued automation is both wasteful and potentially risky to your account.

The tool detects shadow-limiting by tracking every video it successfully marks "Not interested" (`clickbait_acted` in state). On each subsequent run, if a previously-acted video reappears in the feed **more than 48 hours after** the original action (allowing time for normal algorithm propagation), it counts as a suspicious re-encounter. After **2 such re-encounters in a single run**, the tool:

1. Stops the run immediately.
2. Writes a timestamped alert to `needs-attention.txt`.
3. Fires a desktop notification (and ntfy.sh push if configured).
4. **Pauses all future scheduled runs** until you explicitly clear the flag.

To resume after reviewing:

```bash
yt-dont-recommend --clear-alerts
```

This clears the flag and re-enables the scheduler on the next heartbeat.

> **Note:** A single re-encounter within 48 hours of acting is logged as a warning but does not stop the run — this is normal algorithm latency. Only time-gated re-encounters count toward the detection threshold.

### Scheduler pause on any attention flag

Any alert written by `write_attention()` — shadow-limiting, broken selectors, expired login — also pauses the scheduler. The heartbeat checks for `needs-attention.txt` before spawning any run. This prevents repeated failed or risky automated runs when something needs human attention. `--clear-alerts` resumes scheduling in all cases.

## Running Periodically

YouTube's home feed refreshes throughout the day, so twice-daily runs are recommended. After the initial processing pass, runs that find nothing new are fast.

### Automatic setup (recommended)

```bash
yt-dont-recommend --schedule install
```

That's it. No crontab editing, no path hunting. Schedules `--blocklist --headless` runs at 3:00 AM and 3:00 PM daily using launchd (macOS) or cron (Linux), with the correct binary path filled in automatically.

> **Note:** `--schedule install` sets up blocklist mode only. Clickbait detection (`--clickbait`) requires Ollama to be running and performs LLM inference per video — unsuitable for fully unattended runs on most systems. To include it, set up the schedule manually (see below).

To use different hours, pass `--schedule-hours`:

```bash
# Specific hours (24h, comma-separated)
yt-dont-recommend --schedule install --schedule-hours 6,18

# Every 4 hours
yt-dont-recommend --schedule install --schedule-hours "*/4"

# Every hour
yt-dont-recommend --schedule install --schedule-hours hourly
```

Re-running `--schedule install` replaces any existing schedule — no need to remove first.

Check what's installed:

```bash
yt-dont-recommend --schedule status
```

Remove the schedule:

```bash
yt-dont-recommend --schedule remove
```

Each run picks up where the last left off. New channels added to the blocklist since the last run will be processed when they appear in the home feed.

### Manual cron setup (advanced)

If you prefer to manage cron yourself, use `crontab -e` and add one of the following.

> Cron runs without your shell environment — use absolute paths throughout.

**Installed via uv tool or pipx:**

```bash
# Twice daily — 3am and 3pm
0 3,15 * * * /path/to/yt-dont-recommend --blocklist --headless
```

Find the full path with `which yt-dont-recommend`.

**Cloned repo (uv):**

```bash
0 3,15 * * * cd /path/to/yt-dont-recommend && uv run yt-dont-recommend --blocklist --headless
```

**Cloned repo (pip/venv):**

```bash
0 3,15 * * * /path/to/yt-dont-recommend/.venv/bin/yt-dont-recommend --blocklist --headless
```

## Notifications

When something requires your attention (e.g. a selector failure or expired login session during an unattended run), the tool:

1. Writes a timestamped alert to `~/.yt-dont-recommend/needs-attention.txt`
2. Shows it prominently the next time you run any command (with a pause so you can read it)
3. Fires a desktop notification via `osascript` (macOS) or `notify-send` (Linux) — best-effort, silent if unavailable
4. Sends a push notification via [ntfy.sh](https://ntfy.sh) if configured (optional, recommended for unattended use)

The alert is cleared automatically when a subsequent run confirms the selector is working again, or manually with:

```bash
yt-dont-recommend --clear-alerts
```

### Push notifications via ntfy.sh (optional)

[ntfy.sh](https://ntfy.sh) is a free, open-source push notification service. No account required — the free tier is all you need. Install the app on your phone or desktop, subscribe to your private topic, and get notified wherever you are.

Generate a private topic and show subscribe instructions:

```bash
yt-dont-recommend --setup-notify
```

Send a test notification to confirm it's working:

```bash
yt-dont-recommend --test-notify
```

To remove the topic:

```bash
yt-dont-recommend --remove-notify
```

Your topic is a random private string — it is not guessable by others.

## Updates

The tool checks PyPI for a new version once per day. When a newer version is found it logs the information and sends a push notification if ntfy.sh is configured.

Check manually at any time:

```bash
yt-dont-recommend --check-update
```

### Auto-upgrade

> **Not recommended during early development.** The tool is in active alpha and releases can contain breaking changes. Enable auto-upgrade only if you are comfortable running the latest code immediately and using `--revert` when needed. If you run unattended scheduled jobs, a bad release could silently stop blocking until you notice.

Enable automatic upgrades — the tool will upgrade itself when a new version is detected:

```bash
yt-dont-recommend --auto-upgrade enable
```

The new binary takes effect on the next run. Disable at any time:

```bash
yt-dont-recommend --auto-upgrade disable
```

### Reverting an upgrade

If something goes wrong after an upgrade, revert to the previous version:

```bash
yt-dont-recommend --revert
```

The previous version is tracked automatically on every run, so `--revert` works whether the upgrade was automatic or done manually with `uv` or `pipx`. If `--revert` cannot detect your package manager, it will print the manual install command instead.

If the previous version also has a problem (e.g. multiple bad releases), pass a specific version:

```bash
yt-dont-recommend --revert 0.1.10
```

Any published version on PyPI can be targeted this way. Check the [releases page](https://github.com/cmeans/yt-dont-recommend/releases) for the full version history.

`--revert` automatically disables auto-upgrade so the tool doesn't immediately re-upgrade itself. Re-enable it once you're satisfied the issue is resolved:

```bash
yt-dont-recommend --auto-upgrade enable
```

## Checking and Updating Selectors

YouTube changes its DOM structure frequently. When the script starts silently skipping everything (SKIP entries in the log), the selectors are probably broken.

Run the selector checker to diagnose:

```bash
yt-dont-recommend --check-selectors
```

This opens a visible browser, tests the current selectors against four contexts (home feed, search results, channel header, video watch page), prints every menu item found, and saves a timestamped report with screenshots to `~/.yt-dont-recommend/`.

**Confirmed behavior (as of 2026-03-05):** "Don't recommend channel" appears **only** in the home feed. It does not appear in search results, on channel pages, or on the video watch page. The tool's home feed scanner reflects this.

To test against a specific channel instead of the default (`@YouTube`):

```bash
yt-dont-recommend --check-selectors --test-channel @SomeChannel
```

### Overriding Selectors

If YouTube changes its DOM and the built-in selectors break, you can override them in `~/.yt-dont-recommend/config.yaml` without waiting for a code update. Add a `selectors:` section with only the keys you need to change:

```yaml
selectors:
  feed_card: "div.new-video-card"
  menu_buttons:
    - "button[aria-label='More options']"
```

All 14 selector keys are documented in [`config.example.yaml`](config.example.yaml). Missing keys fall back to built-in defaults.

### Non-English YouTube

If your YouTube is in a language other than English, the menu item text won't match the built-in phrases. Override the text phrases in `config.yaml`:

```yaml
selectors:
  dont_recommend_phrases:
    - "no recomendar el canal"      # Spanish
  not_interested_phrase: "no me interesa"
```

Run `--check-selectors` to see the exact menu item text in your language — the checker prints every menu item it finds.

## Acknowledgments

This tool was designed and built in collaboration with [Claude Code](https://claude.ai/claude-code), Anthropic's AI coding assistant, using a model of delegated implementation with human technical authority — design decisions, live test analysis, and iteration happened conversationally, with the human retaining final judgement on architecture, quality gates, and release.

The clickbait detection feature was informed by:

- **ThumbnailTruth** — Naveed, Uzmi & Qazi (2025). *ThumbnailTruth: A Multi-Modal LLM Approach for Detecting Misleading YouTube Thumbnails Across Diverse Cultural Settings.* [arXiv:2509.04714](https://arxiv.org/abs/2509.04714). Their multi-modal dataset and finding that frontier models achieve 93%+ accuracy on thumbnail-based clickbait detection shaped the design of the thumbnail classification stage.

- **Visual Description Grounding** — The two-step thumbnail pipeline (describe what you see literally, then classify from that description) follows an established technique for reducing hallucination in vision-language models. By committing to a factual description before applying classification pressure, the model cannot rationalize a predetermined label by confabulating matching visual evidence.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed history of all releases.

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2026 Chris Means

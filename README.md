# YouTube "Don't Recommend Channel" Bulk Trainer

Automates YouTube's "Don't recommend channel" action in bulk, using any channel blocklist you provide. Because the signal is tied to your **Google account** (not the device), it trains the algorithm everywhere you're signed in — including Fire TV, mobile apps, smart TVs, and game consoles.

No browser extension can do this. Extensions filter content client-side on a single browser. This tool affects the server-side recommendation engine.

## Platform

Developed and tested on **Linux (Fedora)**. macOS and Windows are untested — the script may work but is not supported.

## Prerequisites

- Python 3.10+
- Git (to clone the repo)

## Setup

```bash
# 1. Clone and enter the repo
git clone https://github.com/cmeans/yt-dont-recommend.git
cd yt-dont-recommend

# 2. Create a virtual environment
python3 -m venv .venv

# 3. Install Playwright inside the venv
.venv/bin/pip install playwright

# 4. Install the Chromium browser
.venv/bin/playwright install chromium

# On Debian/Ubuntu you may also need system dependencies:
# .venv/bin/playwright install-deps chromium

# 5. Activate the venv (so you can just type 'python' from here on)
source .venv/bin/activate

# 6. Log into YouTube — a browser window opens, sign in, then close it
python yt_dont_recommend.py --login
# Your session is saved to ~/.yt-dont-recommend/browser-profile/
# and reused on every subsequent run.
```

## Usage

> All examples below assume the virtual environment is active (`source .venv/bin/activate`).

```bash
# Dry run — see what channels would be processed
python yt_dont_recommend.py --dry-run

# Process all built-in sources consecutively (default)
python yt_dont_recommend.py

# Use a specific built-in source
python yt_dont_recommend.py --source deslop
python yt_dont_recommend.py --source aislist

# Use multiple sources explicitly (comma-separated)
python yt_dont_recommend.py --source deslop,aislist

# Use a local blocklist file
python yt_dont_recommend.py --source /path/to/my-list.txt

# Use a remote blocklist URL
python yt_dont_recommend.py --source https://example.com/blocklist.txt

# Process only 10 channels (good for first test)
python yt_dont_recommend.py --limit 10

# Protect specific channels from ever being blocked (overrides the default exclude file)
python yt_dont_recommend.py --exclude ~/.yt-dont-recommend/exclude.txt

# Run in headless mode (no visible browser)
python yt_dont_recommend.py --headless

# Check progress (includes subscription-protected channels)
python yt_dont_recommend.py --stats

# Control when a channel is auto-unblocked after being removed from a list
python yt_dont_recommend.py --unblock-policy all   # default: unblock only when gone from all sources
python yt_dont_recommend.py --unblock-policy any   # unblock as soon as gone from any source

# Start over
python yt_dont_recommend.py --reset-state

# List built-in sources
python yt_dont_recommend.py --list-sources
```

## Exclusion List

If a community blocklist includes a channel you want to keep, add it to your personal exclusion file:

```
~/.yt-dont-recommend/exclude.txt
```

This file is loaded automatically on every run — no flag required. The format is the same plain-text format as blocklists, and supports inline `#` comments:

```
# Channels I want to keep despite being on community lists
@SomeChannel
@AnotherChannel  # keeping this one — it's a friend's channel
```

To use a different file instead (or a remote URL), pass `--exclude`:

```bash
python yt_dont_recommend.py --exclude /path/to/other-list.txt
python yt_dont_recommend.py --exclude https://example.com/my-exclusions.txt
```

`--exclude` does not accept built-in source names.

## Subscription Protection

The tool automatically skips any channel you are subscribed to — even if it appears on a blocklist. Blocking a channel you subscribe to would signal YouTube to stop recommending it, which is usually not what you want.

When a subscribed channel appears on the blocklist, a `WARNING` is logged and the event is recorded in state under `would_have_blocked`. This warning fires only once per channel (not on every run). Use `--stats` to see the full list.

If a channel you subscribe to genuinely should be blocked, add it to your exclusion file (`~/.yt-dont-recommend/exclude.txt`) to suppress the warning, or unsubscribe and let the tool handle it on the next run.

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
| `deslop`  | DeSlop project (~130+ channels, plain text, actively maintained)  |
| `aislist` | AiSList community text list (~8400+ channels, broader)            |

Running without `--source` processes all built-in sources consecutively. The state tracker prevents re-processing the same channel twice across sources or runs.

## How It Works

1. Fetches the blocklist (local file, URL, or built-in source)
2. Checks whether any previously blocked channels have since been removed from the list and auto-unblocks them per `--unblock-policy`
3. Opens Chromium using your saved YouTube login session
4. Scrapes your subscriptions so subscribed channels are never blocked
5. Scans the YouTube home feed for cards matching blocklist channels
6. For each match:
   - Clicks the "More actions" menu on the video card
   - Clicks "Don't recommend channel"
   - Saves progress immediately (crash-safe, always resumable)
7. Scrolls for more cards and repeats until the list is exhausted or `--limit` is reached
8. Rate-limits itself: 3–7s between actions, 30s break every 25 channels

> **Why the home feed?** Live testing confirmed that "Don't recommend channel" only appears in home feed recommendation contexts. It does not appear on a channel's own `/videos` page, in search results, or on the video watch page.

## State & Logs

All data lives in `~/.yt-dont-recommend/`:

| Path | Purpose |
|------|---------|
| `browser-profile/` | Chromium profile with your login session |
| `processed.json` | Channels already handled, blocked-by source tracking, subscription warnings |
| `run.log` | Timestamped log of all actions (rotates at 1 MB, 5 backups kept) |

## Caveats

- **YouTube ToS:** Automating UI interactions may violate YouTube's Terms of Service. Personal use, your own account, your own risk.
- **Selector fragility:** YouTube's HTML structure changes frequently. The script detects broken selectors automatically — if several consecutive scroll passes yield no parseable channel links, it logs a `POSSIBLE SELECTOR FAILURE` warning and exits early. Run `--check-selectors` to diagnose and get a timestamped report with screenshots.
- **Home feed matching:** The tool can only block channels that appear in your home feed during a run. Channels on the blocklist that never surface in the feed during that session will not be processed. Resume runs until the list is exhausted.
- **Handle vs. channel ID:** YouTube feed cards expose `@handle` links only — `UCxxx` IDs in a blocklist are automatically resolved to `@handles` before scanning. Results are cached in state so re-resolution is skipped on subsequent runs. Both built-in sources already use `@handle` format; this only applies to custom blocklists.
- **Start small:** Use `--limit 10` for your first real run to confirm everything is working before processing a full list.

## Running Periodically

```bash
# Example cron: run every Sunday at 3am
0 3 * * 0 cd /path/to/yt-dont-recommend && .venv/bin/python yt_dont_recommend.py --headless
```

> Cron does not activate your shell's virtual environment, so use `.venv/bin/python` directly.

Each run picks up where the last left off. New channels added to the blocklist since the last run will be processed when they appear in the home feed.

## Checking and Updating Selectors

YouTube changes its DOM structure frequently. When the script starts silently skipping everything (SKIP entries in the log), the selectors are probably broken.

Run the selector checker to diagnose:

```bash
python yt_dont_recommend.py --check-selectors
```

This opens a visible browser, tests the current selectors against four contexts (home feed, search results, channel header, video watch page), prints every menu item found, and saves a timestamped report with screenshots to `~/.yt-dont-recommend/`.

**Confirmed behavior (as of 2026-03-05):** "Don't recommend channel" appears **only** in the home feed. It does not appear in search results, on channel pages, or on the video watch page. The tool's home feed scanner reflects this.

Exit code is 0 if the target option was found, 1 if not — suitable for scripting:

```bash
# Run check monthly and alert on failure
0 0 1 * * cd /path/to/yt-dont-recommend && .venv/bin/python yt_dont_recommend.py --check-selectors || echo "Selectors broken — check ~/.yt-dont-recommend/" | mail -s "yt-dont-recommend alert" you@example.com
```

To test against a specific channel instead of the default (`@YouTube`):

```bash
python yt_dont_recommend.py --check-selectors --test-channel @SomeChannel
```

## License

MIT — see [LICENSE](LICENSE).

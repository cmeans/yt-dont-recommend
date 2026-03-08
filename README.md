# YouTube "Don't Recommend Channel" Bulk Trainer

Automates YouTube's "Don't recommend channel" action in bulk, using any channel blocklist you provide. Because the signal is tied to your **Google account** (not the device), it trains the algorithm everywhere you're signed in — including Fire TV, mobile apps, smart TVs, and game consoles.

No browser extension can do this. Extensions filter content client-side on a single browser. This tool affects the server-side recommendation engine.

## Platform Support

| Platform | Status |
|----------|--------|
| Linux    | ✅ Tested and confirmed working (Fedora 43) |
| macOS    | ⚠️ Code includes macOS support (launchd scheduling etc.) but has not been tested — proceed with caution and please [report issues](https://github.com/cmeans/yt-dont-recommend/issues) |
| Windows  | ❌ Not supported |

Python 3.10 or later is required on all platforms.

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

## Development Setup

Requires Python 3.10+ and Git.

**With [uv](https://docs.astral.sh/uv/) (recommended):**

```bash
git clone https://github.com/cmeans/yt-dont-recommend.git
cd yt-dont-recommend
uv sync
uv run playwright install chromium
uv run python yt_dont_recommend.py --login
```

---

**With pip/venv:**

```bash
git clone https://github.com/cmeans/yt-dont-recommend.git
cd yt-dont-recommend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python yt_dont_recommend.py --login
```

---

A browser window opens — sign into your Google account, then close it. Your session is saved to `~/.yt-dont-recommend/browser-profile/` and reused on every subsequent run.

> **Debian/Ubuntu:** after installing Chromium, you may also need system dependencies:
> - uv tool: `uvx playwright install-deps chromium`
> - uv (dev): `uv run playwright install-deps chromium`
> - pipx / pip/venv: `playwright install-deps chromium`

## Usage

For a full list of options:

```bash
yt-dont-recommend --help
```

```bash
# Dry run — see what channels would be processed
yt-dont-recommend --dry-run

# Process all built-in sources consecutively (default)
yt-dont-recommend

# Use a specific built-in source
yt-dont-recommend --source deslop
yt-dont-recommend --source aislist

# Use multiple sources explicitly (comma-separated)
yt-dont-recommend --source deslop,aislist

# Use a local blocklist file
yt-dont-recommend --source /path/to/my-list.txt

# Use a remote blocklist URL
yt-dont-recommend --source https://example.com/blocklist.txt

# Process only 10 channels (good for first test)
yt-dont-recommend --limit 10

# Protect specific channels from ever being blocked (overrides the default exclude file)
yt-dont-recommend --exclude ~/.yt-dont-recommend/exclude.txt

# Run in headless mode (no visible browser)
yt-dont-recommend --headless

# Check progress (includes subscription-protected channels)
yt-dont-recommend --stats

# Control when a channel is auto-unblocked after being removed from a list
yt-dont-recommend --unblock-policy all   # default: unblock only when gone from all sources
yt-dont-recommend --unblock-policy any   # unblock as soon as gone from any source

# Start over
yt-dont-recommend --reset-state

# List built-in sources
yt-dont-recommend --list-sources
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
yt-dont-recommend --exclude /path/to/other-list.txt
```

```bash
yt-dont-recommend --exclude https://example.com/my-exclusions.txt
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
| `processed.json` | Channels already handled, blocked-by source tracking, subscription warnings, notification topic |
| `run.log` | Timestamped log of all actions (rotates at 1 MB, 5 backups kept) |
| `needs-attention.txt` | Alert flag written when action is required (e.g. selector failure); auto-cleared on a successful run |

## Caveats

- **YouTube ToS:** Automating UI interactions may violate YouTube's Terms of Service. Personal use, your own account, your own risk.
- **Selector fragility:** YouTube's HTML structure changes frequently. The script detects broken selectors automatically — if several consecutive scroll passes yield no parseable channel links, it writes an alert and exits early. The alert is shown prominently on the next interactive run and cleared automatically once a successful run confirms the selector is working again. Run `--check-selectors` to diagnose and get a timestamped report with screenshots.
- **Home feed matching:** The tool can only block channels that appear in your home feed during a run. Channels on the blocklist that never surface in the feed during that session will not be processed. Resume runs until the list is exhausted.
- **Handle vs. channel ID:** YouTube feed cards expose `@handle` links only — `UCxxx` IDs in a blocklist are automatically resolved to `@handles` before scanning. Results are cached in state so re-resolution is skipped on subsequent runs. Both built-in sources already use `@handle` format; this only applies to custom blocklists.
- **Start small:** Use `--limit 10` for your first real run to confirm everything is working before processing a full list.

## Running Periodically

YouTube's home feed refreshes throughout the day, so twice-daily runs are recommended. After the initial processing pass, runs that find nothing new are fast.

### Automatic setup (recommended)

```bash
yt-dont-recommend --schedule install
```

That's it. No crontab editing, no path hunting. Schedules runs at 3:00 AM and 3:00 PM daily using launchd (macOS) or cron (Linux), with the correct binary path filled in automatically.

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
0 3,15 * * * /path/to/yt-dont-recommend --headless
```

Find the full path with `which yt-dont-recommend`.

**Cloned repo (uv):**

```bash
0 3,15 * * * cd /path/to/yt-dont-recommend && uv run python yt_dont_recommend.py --headless
```

**Cloned repo (pip/venv):**

```bash
0 3,15 * * * cd /path/to/yt-dont-recommend && .venv/bin/python yt_dont_recommend.py --headless
```

## Notifications

When something requires your attention (e.g. a selector failure during an unattended run), the tool:

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

The previous version is saved automatically before each auto-upgrade. If `--revert` cannot detect your package manager (uv or pipx), it will print the manual install command instead.

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

## License

MIT — see [LICENSE](LICENSE).

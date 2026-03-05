# YouTube "Don't Recommend Channel" Bulk Trainer

Automates YouTube's "Don't recommend channel" action in bulk, using any channel blocklist you provide. Because the signal is tied to your **Google account** (not the device), it trains the algorithm everywhere you're signed in — including Fire TV, mobile apps, smart TVs, and game consoles.

No browser extension can do this. Extensions filter content client-side on a single browser. This tool affects the server-side recommendation engine.

## Prerequisites

- Python 3.10+
- Playwright for Python

```bash
pip install playwright --break-system-packages
playwright install chromium
```

## Setup

```bash
# First run: log into YouTube
python yt_dont_recommend.py --login
# A browser window opens. Log into your Google account, then close the window.
# Your session is saved to ~/.yt-dont-recommend/browser-profile/
```

## Usage

```bash
# Dry run — see what channels would be processed
python yt_dont_recommend.py --dry-run

# Process channels from the default built-in list (DeSlop)
python yt_dont_recommend.py

# Use a local blocklist file
python yt_dont_recommend.py --source /path/to/my-list.txt

# Use a remote blocklist URL
python yt_dont_recommend.py --source https://example.com/blocklist.txt

# Use a specific built-in source
python yt_dont_recommend.py --source deslop
python yt_dont_recommend.py --source aislist

# Process only 10 channels (good for first test)
python yt_dont_recommend.py --limit 10

# Protect specific channels from ever being blocked
python yt_dont_recommend.py --exclude ~/my-exceptions.txt

# Run in headless mode (no visible browser)
python yt_dont_recommend.py --headless

# Check progress
python yt_dont_recommend.py --stats

# Start over
python yt_dont_recommend.py --reset-state

# List built-in sources
python yt_dont_recommend.py --list-sources
```

## Exclusion List

If a community blocklist includes a channel you want to keep, use `--exclude` to protect it:

```bash
python yt_dont_recommend.py --exclude ~/my-exceptions.txt
```

The exclusion file uses the same plain-text format as blocklists. Excluded channels are silently skipped even if they appear in the blocklist or the home feed.

```
# Channels I want to keep despite being on community lists
/@IBMTechnology
/@SomeOtherChannel
```

`--exclude` accepts a local file path or any HTTP/HTTPS URL. It does not accept built-in source names.

## Blocklist Format

Plain text, one channel path per line. Comments start with `#`.

```
# My custom blocklist
/@SomeHandle
/@AnotherChannel
/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx
```

This format is shared with the [DeSlop](https://github.com/NikoboiNFTB/DeSlop) project. You can point `--source` at any file or URL using this format, or at JSON files using common channel object schemas.

## Built-in Sources

| Source   | Description                                                        |
|----------|--------------------------------------------------------------------|
| `deslop` | DeSlop project (~130+ channels, plain text, actively maintained)   |
| `aislist` | AiSList / AiBlock extension list (community JSON, broader)        |

You can run multiple sources sequentially — the state tracker prevents re-processing the same channel twice.

## How It Works

1. Fetches the blocklist (local file, URL, or built-in source)
2. Opens Chromium using your saved YouTube login session
3. For each channel:
   - Navigates to their `/videos` page
   - Hovers over the first video to reveal the three-dot menu
   - Clicks "Don't recommend channel"
   - Saves progress after each channel (crash-safe, can always resume)
4. Rate-limits itself: 3–7s between channels, 30s break every 25 channels

## State & Logs

All data lives in `~/.yt-dont-recommend/`:

| Path | Purpose |
|------|---------|
| `browser-profile/` | Chromium profile with your login session |
| `processed.json` | Channels already handled (won't re-process) |
| `run.log` | Timestamped log of all actions |

## Caveats

- **YouTube ToS:** Automating UI interactions may violate YouTube's Terms of Service. Personal use, your own account, your own risk.
- **Selector fragility:** YouTube's HTML structure changes frequently. If the script starts failing, the selectors in `dont_recommend_channel()` likely need updating. Run with `--limit 1` and without `--headless` to observe what's happening.
- **Context limitation:** "Don't recommend channel" may only appear in certain contexts (home feed, search results) and not on a channel's own page. If the option is never found, the navigation approach may need to change.
- **Start small:** Use `--limit 10` for your first real run to confirm everything is working before processing a full list.

## Running Periodically

```bash
# Example systemd timer or cron: run every Sunday at 3am
0 3 * * 0 cd /path/to/yt-dont-recommend && python yt_dont_recommend.py --headless
```

## Checking and Updating Selectors

YouTube changes its DOM structure frequently. When the script starts silently skipping
everything (SKIP entries in the log), the selectors are probably broken.

Run the selector checker to diagnose:

```bash
python yt_dont_recommend.py --check-selectors
```

This opens a visible browser, tests the current selectors against both the YouTube home
feed and a channel's /videos page, prints every menu item it finds, and saves a
timestamped report with screenshots to `~/.yt-dont-recommend/`.

It also answers a structural question: **the "Don't recommend channel" option may only
appear in certain contexts** (home feed, search results) and not on a channel's own page.
If the checker shows it working on the home feed but not the channel page, the processing
approach needs to change. The report will tell you clearly.

Exit code is 0 if the target option was found, 1 if not — suitable for scripting:

```bash
# Run check monthly and log the result
0 0 1 * * cd /path/to/yt-dont-recommend && python yt_dont_recommend.py --check-selectors || echo "Selectors broken — check ~/.yt-dont-recommend/" | mail -s "yt-dont-recommend alert" you@example.com
```

To test against a specific channel instead of the default (`/@YouTube`):

```bash
python yt_dont_recommend.py --check-selectors --test-channel /@SomeChannel
```

## License

MIT — see [LICENSE](LICENSE).

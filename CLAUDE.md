# CLAUDE.md — Project Context for Claude Code

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
.venv/bin/python yt_dont_recommend.py --source /tmp/test-blocklist.txt
```
Watch the log output to confirm the channel was blocked. The script exits after exhausting the feed scroll.

### Step 4 — Verify the block on myactivity
Optionally, open `myactivity.google.com/page?page=youtube_user_feedback` in Chrome and confirm a "Don't recommend" entry exists for the channel.

### Step 5 — Trigger unblock
Empty the blocklist file (or remove the channel from it) and re-run:
```
echo "# empty" > /tmp/test-blocklist.txt
.venv/bin/python yt_dont_recommend.py --source /tmp/test-blocklist.txt
```
The script will detect the removal, navigate to myactivity, and prompt for Google password verification in the browser window. Once verified, it finds and deletes the "Don't recommend" entry and dismisses the "Deletion complete" dialog automatically.

> **Password verification latency**: Google requires re-authentication to access myactivity feedback entries. The browser window will show the password prompt; the script polls for up to 3 minutes. This is normal — enter the password and wait.

### Step 6 — Confirm the channel is back in the feed
Check the home feed manually (or via an ad-hoc script) immediately after the unblock completes. A hit here confirms the full cycle worked. If the channel doesn't appear, wait a few minutes and check again — YouTube may cache the "Don't recommend" signal briefly before the deletion propagates.

> **Algorithm propagation latency**: After unblocking, YouTube's recommendation engine can take a few minutes (rarely longer) to start surfacing the channel again. The myactivity deletion is instant; the feed reflection may lag.

### Cycle is only complete when Step 6 produces a hit.

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

Single-file Python script (`yt_dont_recommend.py`). Key components:

- **Blocklist fetching**: `resolve_source()` handles built-in keys, HTTP(S) URLs, and local file paths. `parse_text_blocklist()` and `parse_json_blocklist()` handle format variants. JSON format is auto-detected by leading `{` or `[`.
- **State management**: `~/.yt-dont-recommend/processed.json` tracks which channels have been handled (crash-safe, saved after each action). See State Schema below for all keys.
- **Browser automation**: Playwright with a persistent Chromium profile (login session persists between runs). Launch args: `--disable-blink-features=AutomationControlled`, `--disable-infobars`, `ignore_default_args=["--enable-automation"]`.
- **Subscription protection**: `fetch_subscriptions(page)` scrapes `youtube.com/feed/channels`, returns a lowercase set of handles. Called once per run in `process_channels()`. Subscribed channels are skipped with a one-time WARNING logged and stored in `state["would_have_blocked"]`.
- **Blocklist removal detection**: `check_removals()` runs at the start of each `process_channels()` call, compares the current list against `state["blocked_by"]`, and auto-unblocks channels no longer on the list, per `--unblock-policy`.
- **Blocklist growth tracking**: Each run records source list sizes in `state["source_sizes"]`. When a source has grown since the last run, it logs prominently.
- **Attention/notification system**: `write_attention(message)` writes `needs-attention.txt`, fires a desktop notification (`osascript`/`notify-send`), and sends an ntfy.sh push if configured. Triggered by: selector failure, expired login session, unblock selector failure, auto-upgrade failure. Auto-cleared on the next successful run.
- **Version tracking**: At startup, the running version is compared to `state["current_version"]`; on change, the old value is rotated to `state["previous_version"]`. This makes `--revert` work regardless of whether the upgrade was automatic or manual.
- **Logging**: `RotatingFileHandler` — `run.log` caps at 1 MB with 5 backups (`run.log.1`–`run.log.5`).
- **Rate limiting**: Random 3–7s delays between actions, 30s pause every 25 channels. Scroll delay randomised 1.5–3.0s.

### State Schema

```json
{
  "processed": ["@channel1"],
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
  "pending_unblock": {},
  "notify_topic": "ydr-<random-hex>",
  "last_version_check": "2026-03-08T...",
  "latest_known_version": "0.1.13",
  "notified_version": "0.1.13",
  "auto_upgrade": false,
  "previous_version": "0.1.12",
  "current_version": "0.1.13"
}
```

`load_state()` is backward-compatible: missing keys are populated via `setdefault`.

### Key Function Signatures

```python
def process_channels(channels: list[str], source: str,
                     dry_run: bool = False, limit: int | None = None,
                     headless: bool = False, unblock_policy: str = "all"):

def check_removals(state: dict, current_channels: list[str],
                   source: str, unblock_policy: str) -> list[str]:

def fetch_subscriptions(page) -> set[str]:
```

### CLI Flags

| Flag | Description |
|------|-------------|
| `--login` | Open browser for Google account authentication |
| `--source` | Blocklist: built-in name(s) (comma-separated), local file path, or HTTP(S) URL. Defaults to all built-in sources. |
| `--exclude` | Exclusion list: local file path or HTTP(S) URL (not built-in names) |
| `--limit N` | Stop after N channels |
| `--dry-run` | Show what would be processed without acting |
| `--headless` | Run without a visible browser window |
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
| `--schedule install\|remove\|status` | Manage scheduled runs via launchd (macOS) or cron (Linux) |
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
UCxxxxxxxxxxxxxxxxxxxxxxxx
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

## What NOT To Do

- Don't run headless until you've confirmed the selectors are working with a visible browser
- Don't process the full list until rate limiting behavior is understood
- Don't assume selectors work just because they look reasonable — YouTube's DOM is notoriously inconsistent

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

The local file path is the most important for shareability. Someone curates a list, puts it in a repo or a gist, others point at it. The standard format is the DeSlop format: one channel path per line (`/@handle` or `/channel/UCxxx`), comments with `#`.

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

### AiSList JSON format — UNVERIFIED
The `aislist` parser is a best-guess. The schema has not been confirmed against the live file. Start with `--source deslop` (verified).

## Architecture

Single-file Python script (`yt_dont_recommend.py`). Key components:

- **Blocklist fetching**: `resolve_source()` handles built-in keys, HTTP(S) URLs, and local file paths. `parse_text_blocklist()` and `parse_json_blocklist()` handle format variants. JSON format is auto-detected by leading `{` or `[`.
- **State management**: `~/.yt-dont-recommend/processed.json` tracks which channels have been handled (crash-safe, saved after each action). State schema includes `blocked_by` (per-channel source tracking) and `would_have_blocked` (subscription-protected channels).
- **Browser automation**: Playwright with a persistent Chromium profile (login session persists between runs).
- **Subscription protection**: `fetch_subscriptions(page)` scrapes `youtube.com/feed/channels`, returns a lowercase set of handles. Called once per run in `process_channels()`. Subscribed channels are skipped with a one-time WARNING logged and stored in `state["would_have_blocked"]`.
- **Blocklist removal detection**: `check_removals()` runs at the start of each `process_channels()` call, compares the current list against `state["blocked_by"]`, and auto-unblocks channels no longer on the list, per `--unblock-policy`.
- **Logging**: `RotatingFileHandler` — `run.log` caps at 1 MB with 5 backups (`run.log.1`–`run.log.5`).
- **Rate limiting**: Random 3–7s delays between actions, 30s pause every 25 channels.

### State Schema

```json
{
  "processed": ["/@channel1"],
  "blocked_by": {
    "/@channel1": {"sources": ["deslop"], "blocked_at": "2026-03-05T..."}
  },
  "would_have_blocked": {
    "/@SomeChannel": {"sources": ["deslop"], "first_seen": "...", "notified": true}
  },
  "last_run": "...",
  "stats": {"success": 1, "skipped": 0, "failed": 0}
}
```

`load_state()` is backward-compatible: missing keys are populated via `setdefault`.

### Key Function Signatures

```python
def process_channels(channels: list[str], source: str,
                     dry_run: bool = False, limit: int | None = None,
                     headless: bool = False, unblock_policy: str = "all"):

def check_removals(state: dict, current_channels: list[str],
                   source: str, unblock_policy: str) -> int:

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
| `--stats` | Show processed count, success/skip/fail, and `would_have_blocked` entries |
| `--reset-state` | Clear all state and start over |
| `--list-sources` | Print built-in source names |
| `--check-selectors` | Run 4-context selector diagnostic, save report + screenshots |
| `--test-channel` | Channel to use with `--check-selectors` (default: `/@YouTube`) |
| `--verbose` | Extra logging |

## Standard Blocklist Format

The interchange format is plain text:
```
# Comments start with #
# Blank lines are ignored
# Entries are YouTube channel paths:
/@SomeHandle
/@AnotherChannel
/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx
```

This matches the DeSlop format and is trivial to create, share, and parse.

## Built-in Blocklist Sources

| Key       | Format | Verified | URL |
|-----------|--------|----------|-----|
| `deslop`  | text   | YES      | `https://raw.githubusercontent.com/NikoboiNFTB/DeSlop/refs/heads/main/block/list.txt` |
| `aislist` | json   | NO       | `https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/blacklist.json` |

DeSlop has ~130+ channels. AiSList may be larger but its JSON schema is unconfirmed.

Other potential sources to consider adding:
- surasshu/cevval AI music blocklist (BlockTube JSON export with channel IDs)
- Any future community lists that adopt the standard text format

## Open Issues & Risks

### 1. Selector Fragility (ongoing)
YouTube changes its DOM frequently. Use `--check-selectors` to diagnose when the script starts silently skipping channels. The selector checker saves a timestamped report and screenshots to `~/.yt-dont-recommend/`.

### 2. Home Feed Matching Completeness
The tool can only block channels that appear in the home feed during a run. A channel on the blocklist that never surfaces won't be processed until a future run where it does. This is a fundamental limitation of the home-feed-only approach.

### 3. Handle vs. Channel ID Matching
Feed cards typically expose `/@handle` hrefs. Blocklist entries using `/channel/UCxxx` format only match if the card also uses that ID. DeSlop (handles) matches well; AiSList (channel IDs) may match less reliably.

### 4. AiSList JSON Format (unverified)
The `aislist` parser is a best-guess. Verify by running `--source aislist --dry-run` and inspecting the parsed count.

### 5. Subscription Scraping (untested live)
`fetch_subscriptions()` uses selectors `ytd-channel-renderer a#main-link` and scrolling on `youtube.com/feed/channels`. These have not been live-tested. If subscription protection silently fails, the selectors here are the first place to look.

### 6. Rate Limiting
The current delays (3–7s between actions, 30s every 25 channels) are conservative guesses. Back off further if YouTube shows CAPTCHAs or unusual behavior.

### 7. YouTube ToS
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

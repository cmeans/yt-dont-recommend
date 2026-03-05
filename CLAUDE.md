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

The local file path is the most important for shareability. Someone curates a list, puts it in a repo or a gist, others point at it. The standard format should be the DeSlop format: one channel path per line (`/@handle` or `/channel/UCxxx`), comments with `#`.

## Known Issues & Risks (High Priority)

### 1. Selector Fragility (CRITICAL)
The Playwright selectors in `dont_recommend_channel()` have NOT been tested against a live YouTube page. YouTube changes their DOM structure frequently and A/B tests different layouts. The selectors for finding the three-dot menu and the "Don't recommend channel" option will almost certainly need adjustment after the first live test.

**First task should be:** Run `--login`, then `--limit 1` with the browser visible (no `--headless`) and observe what happens. Fix selectors based on actual DOM.

### 2. "Don't Recommend" Context Problem (POSSIBLY FUNDAMENTAL)
The script currently navigates to a channel's `/videos` page, finds a video, and tries to click "Don't recommend channel" from the video's context menu. **However**, that menu option may only appear in certain contexts:
- Home feed recommendations
- Search results
- Sidebar suggestions

It may NOT appear when you're already on the channel's own page. If this is the case, the entire approach needs to change — e.g., searching for the channel name from the YouTube home page and acting on the result there instead.

### 3. AiSList JSON Format (UNVERIFIED)
The `aislist` blocklist source parser is a best-guess. I could see the AiSList GitHub repo structure (`AiSList/blacklist.json` exists under `Override92/AiSList`) but couldn't fetch the raw JSON to confirm the schema. The DeSlop source (`deslop`) is verified — it's a simple text file, one channel path per line, comments start with `#`. **Start with DeSlop.**

### 4. Rate Limiting
The current delays (3-7s between actions, 30s every 25 channels) are guesses. YouTube could flag rapid automated "Don't recommend" actions. The user should start small (`--limit 10`) and increase gradually. If YouTube starts showing CAPTCHAs or unusual behavior, back off significantly.

### 5. YouTube ToS
Automating UI interactions violates YouTube's Terms of Service. This is for personal use on the user's own account. Same risk category as SmartTube or ad blockers.

## Architecture

Single-file Python script. Key components:

- **Blocklist fetching**: Downloads from GitHub raw URLs or reads local files, parses text or JSON format
- **State management**: `~/.yt-recommend-trainer/processed.json` tracks which channels have been handled (crash-safe, saves after each channel)
- **Browser automation**: Playwright with a persistent Chromium profile (login session persists between runs)
- **Rate limiting**: Random delays + periodic longer pauses

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

## Original Developer's Environment

- Fedora 43 (username: cmeans)
- Python 3.x available
- Playwright needs to be installed: `pip install playwright --break-system-packages && playwright install chromium`
- The developer is technically proficient (full-stack developer, C#/Java/Python)

## Built-in Blocklist Sources

| Key      | Format    | Verified | URL |
|----------|-----------|----------|-----|
| `deslop` | text      | YES      | `https://raw.githubusercontent.com/NikoboiNFTB/DeSlop/refs/heads/main/block/list.txt` |
| `aislist`| json      | NO       | `https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/blacklist.json` |

DeSlop list has ~130+ channels. AiSList may be larger but format is unconfirmed.

Other potential sources to consider adding:
- surasshu/cevval AI music blocklist (BlockTube JSON export with channel IDs)
- Any future community lists that adopt the standard text format

## Development Priorities

1. **Get the basic flow working with real selectors** — run against 1 channel, observe, fix
2. **Determine if "Don't recommend" works from channel pages** — if not, redesign the navigation approach
3. **Implement local file and URL source support** — `--source /path/to/file.txt` and `--source https://...`
4. **Verify AiSList JSON format** — fetch the file, inspect, fix parser
5. **Consider combining multiple sources** in a single run with deduplication
6. **Update state directory** from `~/.yt-deslop-trainer/` to `~/.yt-recommend-trainer/`
7. **Update README** to reflect generalized scope and document the standard blocklist format

## What NOT To Do

- Don't run headless until selectors are verified with visible browser
- Don't process the full list until rate limiting behavior is understood
- Don't assume selectors work just because they look reasonable — YouTube's DOM is notoriously inconsistent

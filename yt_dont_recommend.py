#!/usr/bin/env python3
"""
YouTube "Don't Recommend Channel" Bulk Trainer

Fetches channel blocklists and uses browser automation to trigger
"Don't recommend channel" for each one. This trains your YouTube account's
recommendation algorithm at the account level, so the effect syncs across
all devices (Fire TV, mobile apps, smart TVs, etc.) where you're signed
into the same Google account.

Usage:
    # First run: will open browser for you to log into YouTube manually
    python yt_dont_recommend.py --login

    # Subsequent runs: processes the blocklist
    python yt_dont_recommend.py

    # Dry run: just fetch and show the list without clicking anything
    python yt_dont_recommend.py --dry-run

    # Use a built-in blocklist source
    python yt_dont_recommend.py --source deslop
    python yt_dont_recommend.py --source aislist

    # Use a local file
    python yt_dont_recommend.py --source /path/to/my-blocklist.txt

    # Use a remote URL
    python yt_dont_recommend.py --source https://example.com/blocklist.txt

    # Limit number of channels to process (useful for testing)
    python yt_dont_recommend.py --limit 10

    # Check whether current selectors still work against live YouTube
    python yt_dont_recommend.py --check-selectors

Requirements:
    pip install playwright --break-system-packages
    playwright install chromium

Blocklist format (plain text):
    # Comments start with #
    # Blank lines are ignored
    # Entries are YouTube channel handles or IDs:
    @SomeHandle
    @AnotherChannel
    UCxxxxxxxxxxxxxxxxxxxxxxxx

Author: Chris Means (generated with Claude)
License: MIT
"""

import argparse
import json
import logging
import logging.handlers
import random
import re
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse, quote
from urllib.request import urlopen, Request

# --- Configuration ---

BUILTIN_SOURCES = {
    "deslop": {
        "name": "DeSlop",
        "url": "https://raw.githubusercontent.com/NikoboiNFTB/DeSlop/refs/heads/main/block/list.txt",
        "format": "text",
        "description": "Curated list from the DeSlop project (~130+ channels)",
    },
    "aislist": {
        "name": "AiSList",
        "url": "https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/aislist_blocklist.txt",
        "format": "text",
        "description": "Community-maintained list from AiSList (~8400+ channels, ! comments)",
    },
}

DEFAULT_SOURCES = list(BUILTIN_SOURCES.keys())  # run all built-in sources by default

# Browser profile directory (persists login state between runs)
PROFILE_DIR = Path.home() / ".yt-dont-recommend" / "browser-profile"

# State file to track which channels have been processed
STATE_FILE = Path.home() / ".yt-dont-recommend" / "processed.json"

# Log file
LOG_FILE = Path.home() / ".yt-dont-recommend" / "run.log"

# Default personal exclusion list — loaded automatically if present
DEFAULT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "exclude.txt"

# Delays (seconds) — be respectful to avoid rate limiting
MIN_DELAY = 3.0
MAX_DELAY = 7.0
PAGE_LOAD_WAIT = 3.0
LONG_PAUSE_EVERY = 25
LONG_PAUSE_SECONDS = 30

# Selectors used both for processing and selector checks.
# YouTube changes its DOM frequently — run --check-selectors if things break.
VIDEO_SELECTORS = [
    "ytd-rich-item-renderer",
    "ytd-grid-video-renderer",
    "ytd-video-renderer",
    "#contents ytd-rich-grid-row",
]
MENU_BTN_SELECTORS = [
    "button[aria-label='More actions']",                        # home feed and search results
    "button[aria-label='Action menu']",                         # channel pages
    "button.yt-icon-button#button[aria-label='Action menu']",
    "yt-icon-button#button[aria-label='Action menu']",
    "ytd-menu-renderer yt-icon-button",
    "#menu button",
]
MENU_ITEM_SELECTOR = (
    "ytd-menu-service-item-renderer, tp-yt-paper-item, "
    "ytd-menu-navigation-item-renderer, [role='menuitem']"
)
TARGET_PHRASES = ("don't recommend", "dont recommend")


# --- Logging Setup ---

def setup_logging(verbose: bool = False):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            file_handler,
        ],
    )


# --- State Management ---

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            s = json.load(f)
        # Ensure new fields exist for older state files (backward compat)
        s.setdefault("blocked_by", {})
        s.setdefault("would_have_blocked", {})
        return s
    return {
        "processed": [],
        "blocked_by": {},
        "would_have_blocked": {},
        "last_run": None,
        "stats": {"success": 0, "skipped": 0, "failed": 0},
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now().isoformat()
    # Don't leave empty pending_unblock in the state file
    if "pending_unblock" in state and not state["pending_unblock"]:
        del state["pending_unblock"]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Blocklist Fetching ---

def parse_text_blocklist(raw: str) -> list[str]:
    """Parse plain text blocklist: one channel path per line.

    Supports # and ! comment prefixes. Normalizes all variants to canonical
    form: @handle for handles, UCxxx for channel IDs.
    """
    channels = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # Strip leading slash: /@handle → @handle
        if line.startswith("/@"):
            line = line[1:]
        # Strip /channel/ prefix: /channel/UCxxx → UCxxx
        elif line.startswith("/channel/"):
            line = line[len("/channel/"):]
        channels.append(line)
    return channels


def parse_json_blocklist(raw: str) -> list[str]:
    """Parse JSON blocklist. Handles several common formats.

    All results are normalized to canonical form: @handle or UCxxx.
    """
    channels = []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, str):
                    # Normalize /@handle → @handle, /channel/UCxxx → UCxxx
                    if entry.startswith("/@"):
                        entry = entry[1:]
                    elif entry.startswith("/channel/"):
                        entry = entry[len("/channel/"):]
                    channels.append(entry)
                elif isinstance(entry, dict):
                    for key in ("channelHandle", "handle", "channelId", "id", "url"):
                        if key in entry:
                            val = entry[key]
                            if val.startswith("http"):
                                path = urlparse(val).path  # e.g. /@handle
                                if path.startswith("/@"):
                                    val = path[1:]
                                elif path.startswith("/channel/"):
                                    val = path[len("/channel/"):]
                                else:
                                    val = path
                            elif val.startswith("UC"):
                                pass  # already canonical
                            elif val.startswith("@"):
                                pass  # already canonical
                            channels.append(val)
                            break
        elif isinstance(data, dict):
            for key in data:
                if key.startswith("UC"):
                    channels.append(key)
                elif key.startswith("@"):
                    channels.append(key)
    except json.JSONDecodeError:
        logging.warning("Failed to parse as JSON; falling back to line-by-line text parsing")
        channels = parse_text_blocklist(raw)
    return channels


def fetch_remote(url: str) -> str:
    req = Request(url, headers={"User-Agent": "yt-dont-recommend/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {url}: {e}") from e


def resolve_source(source: str) -> list[str]:
    """
    Resolve --source to a list of channel paths. Accepts:
      - A built-in key ("deslop", "aislist")
      - A local file path
      - An HTTP/HTTPS URL
    """
    if source in BUILTIN_SOURCES:
        info = BUILTIN_SOURCES[source]
        logging.info(f"Fetching built-in source '{source}' ({info['name']}): {info['url']}")
        raw = fetch_remote(info["url"])
        channels = parse_text_blocklist(raw) if info["format"] == "text" else parse_json_blocklist(raw)
        logging.info(f"Fetched {len(channels)} channels from {info['name']}")
        return channels

    if source.startswith("http://") or source.startswith("https://"):
        logging.info(f"Fetching remote blocklist: {source}")
        raw = fetch_remote(source)
        stripped = raw.lstrip()
        channels = parse_json_blocklist(raw) if stripped.startswith(("{", "[")) else parse_text_blocklist(raw)
        logging.info(f"Fetched {len(channels)} channels from {source}")
        return channels

    path = Path(source).expanduser().resolve()
    if not path.exists():
        logging.error(f"File not found: {path}")
        sys.exit(1)
    logging.info(f"Reading local blocklist: {path}")
    raw = path.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    channels = parse_json_blocklist(raw) if stripped.startswith(("{", "[")) else parse_text_blocklist(raw)
    logging.info(f"Read {len(channels)} channels from {path.name}")
    return channels


def channel_to_url(channel: str) -> str:
    """Convert a canonical channel identifier to a full YouTube URL."""
    if channel.startswith("http"):
        return channel
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}"
    if channel.startswith("UC"):
        return f"https://www.youtube.com/channel/{channel}"
    return f"https://www.youtube.com/{channel}"


# --- Browser Automation ---

def do_login():
    """Open a browser window for the user to log into YouTube."""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    logging.info("Opening browser for YouTube login...")
    logging.info("Log into your Google account in the browser window.")
    logging.info("Once you can see your YouTube home page, close the browser.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://accounts.google.com/ServiceLogin?service=youtube")

        logging.info("Waiting for you to complete login... (close browser when done)")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        context.close()

    logging.info("Login session saved. You can now run without --login.")


def _find_menu_btn(card):
    """Find the 'More actions' menu button within a feed card. Returns element or None."""
    for sel in MENU_BTN_SELECTORS:
        btn = card.query_selector(sel)
        if btn:
            return btn
    # Fallback: any button whose aria-label contains 'action', 'menu', or 'more'
    for btn in card.query_selector_all("button"):
        label = (btn.get_attribute("aria-label") or "").lower()
        if any(w in label for w in ("action", "menu", "more")):
            return btn
    return None


def _click_dont_recommend(page, card) -> bool:
    """
    Click 'Don't recommend channel' on a home feed card.

    'Don't recommend channel' only appears in recommendation feed contexts
    (home feed, subscription feed). It does NOT appear in search results,
    on channel pages, or on video watch pages — confirmed 2026-03-05.

    Returns True if the option was found and clicked, False otherwise.
    """
    card.scroll_into_view_if_needed()
    card.hover()
    time.sleep(0.5)

    menu_btn = _find_menu_btn(card)
    if not menu_btn:
        logging.debug("Could not find menu button on feed card")
        return False

    menu_btn.click()
    time.sleep(1.0)

    target_item = None
    for item in page.query_selector_all(MENU_ITEM_SELECTOR):
        text = (item.inner_text() or "").strip().lower()
        if any(p in text for p in TARGET_PHRASES):
            target_item = item
            break

    if not target_item:
        page.keyboard.press("Escape")
        return False

    target_item.click()
    time.sleep(0.5)
    return True


def check_removals(state: dict, current_channels: list[str],
                   source: str, unblock_policy: str) -> list[str]:
    """
    Compare currently-fetched blocklist against previously-blocked channels.

    If a channel was blocked because of `source` but is no longer in the
    current list, it may be a false positive that the list maintainer corrected.

    unblock_policy:
      "all" — only unblock when the channel has been dropped from every source
               that originally blocked it (conservative, default)
      "any" — unblock as soon as any single source drops the channel

    Modifies state in place. Returns list of channels that should be
    unblocked on YouTube (browser action still required).
    """
    current_set = {c.lower() for c in current_channels}
    blocked_by = state.get("blocked_by", {})
    to_unblock: list[str] = []

    for channel, info in list(blocked_by.items()):
        sources = info.get("sources", [])
        if source not in sources:
            continue
        if channel.lower() in current_set:
            continue

        # This channel was blocked by `source` but is no longer on that list
        other_sources = [s for s in sources if s != source]

        if unblock_policy == "any" or not other_sources:
            # Save to pending_unblock before removing from state, so a failed browser
            # unblock can be retried on the next run without losing the channel.
            state.setdefault("pending_unblock", {})[channel] = info.copy()
            del blocked_by[channel]
            try:
                state["processed"].remove(channel)
            except ValueError:
                pass
            to_unblock.append(channel)
            if other_sources:
                logging.warning(
                    f"*** UNBLOCKING {channel} — dropped from '{source}'. "
                    f"NOTE: still present in {other_sources} but unblocking "
                    f"because --unblock-policy=any."
                )
            else:
                logging.warning(
                    f"*** UNBLOCKING {channel} — removed from '{source}' blocklist "
                    f"(possible false positive correction by list maintainer)."
                )
            save_state(state)
        else:
            # policy == "all" and other sources still assert the block
            info["sources"] = other_sources
            logging.info(
                f"NOTE: {channel} was dropped from '{source}' but is still "
                f"blocked by: {other_sources}. Will unblock when removed from all sources."
            )

    return to_unblock


def fetch_subscriptions(page) -> set[str]:
    """
    Scrape the YouTube subscriptions management page and return a set of
    lowercased canonical channel IDs (@handle or UCxxx).

    Returns an empty set if the page cannot be parsed, with a warning logged.
    """
    logging.info("Fetching subscriptions list...")
    page.goto("https://www.youtube.com/feed/channels", wait_until="domcontentloaded", timeout=60000)
    time.sleep(PAGE_LOAD_WAIT)

    subscriptions: set[str] = set()
    prev_count = -1
    max_scrolls = 50

    for _ in range(max_scrolls):
        links = page.query_selector_all(
            "ytd-channel-renderer a#main-link, "
            "ytd-channel-renderer a[href^='/@'], "
            "ytd-channel-renderer a[href^='/channel/UC']"
        )
        for link in links:
            href = (link.get_attribute("href") or "").split("?")[0].rstrip("/")
            if href.startswith("/@"):
                subscriptions.add(href[1:].lower())  # /@handle → @handle
            elif href.startswith("/channel/"):
                subscriptions.add(href[len("/channel/"):].lower())  # /channel/UCxxx → UCxxx

        if len(subscriptions) == prev_count:
            break  # no new channels loaded after scroll
        prev_count = len(subscriptions)

        page.evaluate("window.scrollBy(0, window.innerHeight * 3)")
        time.sleep(1.5)

    if subscriptions:
        logging.info(f"Found {len(subscriptions)} subscribed channels")
    else:
        logging.warning(
            "No subscriptions found — the subscriptions page may have changed its layout. "
            "Subscription protection is disabled for this run. "
            "Run --check-selectors and check manually."
        )
    return subscriptions


def process_channels(channels: list[str], source: str,
                     dry_run: bool = False, limit: int | None = None,
                     headless: bool = False, unblock_policy: str = "all"):
    """
    Scan the YouTube home feed and click 'Don't recommend channel' on any
    card whose channel is in the blocklist.

    The feed is scrolled repeatedly to load new cards. Stops when the limit
    is reached, the feed is exhausted, or no blocklist channels have appeared
    after MAX_NO_PROGRESS_SCROLLS consecutive scrolls.

    Note: this only acts on channels YouTube is actively recommending to you.
    Channels not currently in your feed are not being recommended and don't
    need blocking. Run again periodically — as YouTube's algorithm adapts,
    fewer blocklisted channels will appear.
    """
    from playwright.sync_api import sync_playwright

    MAX_NO_PROGRESS_SCROLLS = 20

    state = load_state()
    processed_set = set(state["processed"])

    # Retry any browser unblocks that failed on a previous run
    pending = state.get("pending_unblock", {})
    if pending:
        logging.info(f"Retrying {len(pending)} pending unblock(s) from a previous run: {list(pending)}")

    # Check for channels newly removed from the blocklist since the last run
    to_unblock = check_removals(state, channels, source, unblock_policy)
    # Merge in any channels that were pending from a previous failed unblock
    for ch in pending:
        if ch not in to_unblock:
            to_unblock.append(ch)
    if to_unblock:
        processed_set = set(state["processed"])  # refresh after removals

    unblocked = {c for c in channels if c not in processed_set}
    already_done = len(channels) - len(unblocked)
    logging.info(
        f"{len(channels)} channels in blocklist, "
        f"{already_done} already blocked, "
        f"{len(unblocked)} remaining"
    )

    if not unblocked and not to_unblock:
        logging.info("Nothing to do for this source.")
        return

    if dry_run:
        logging.info(
            f"DRY RUN — scanning home feed for any of {len(unblocked)} blocklisted channel(s)..."
        )

    channel_lookup = {c.lower(): c for c in unblocked}

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
            viewport={"width": 1280, "height": 800},
        )
        for extra in context.pages[1:]:
            extra.close()
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(PAGE_LOAD_WAIT)

        avatar = page.query_selector("button#avatar-btn, img#img[alt]")
        if not avatar:
            logging.error("Not logged in. Run with --login first.")
            context.close()
            return

        # Reverse any blocks on YouTube before scanning for new ones
        if to_unblock and not dry_run:
            successfully_unblocked = _perform_browser_unblocks(page, to_unblock)
            # Clear successfully unblocked channels from pending_unblock
            if successfully_unblocked:
                pending_unblock = state.setdefault("pending_unblock", {})
                for ch in successfully_unblocked:
                    pending_unblock.pop(ch, None)
                save_state(state)
        elif to_unblock and dry_run:
            logging.info(f"DRY RUN — would reverse YouTube block for: {', '.join(to_unblock)}")

        if not unblocked:
            context.close()
            return

        subscriptions = fetch_subscriptions(page)

        # Navigate back to home feed after fetching subscriptions
        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(PAGE_LOAD_WAIT)

        logging.info("Scanning home feed for blocklisted channels...")

        blocked_count = 0
        no_progress_scrolls = 0
        seen_paths: set[str] = set()

        while True:
            if limit and blocked_count >= limit:
                logging.info(f"Reached limit of {limit} channels blocked.")
                break
            if no_progress_scrolls >= MAX_NO_PROGRESS_SCROLLS:
                logging.info(
                    f"No blocklisted channels found after {no_progress_scrolls} "
                    "consecutive scrolls — feed exhausted for this run."
                )
                break

            cards = page.query_selector_all("ytd-rich-item-renderer")
            found_match_this_pass = False

            for card in cards:
                if limit and blocked_count >= limit:
                    break

                channel_link = card.query_selector("a[href^='/@'], a[href^='/channel/UC']")
                if not channel_link:
                    continue

                href = channel_link.get_attribute("href") or ""
                raw_path = href.split("?")[0].rstrip("/")
                # Normalize to canonical form: @handle or UCxxx
                if raw_path.startswith("/@"):
                    path = raw_path[1:]  # /@handle → @handle
                elif raw_path.startswith("/channel/"):
                    path = raw_path[len("/channel/"):]  # /channel/UCxxx → UCxxx
                else:
                    continue
                if path.lower() in seen_paths:
                    continue
                seen_paths.add(path.lower())
                logging.debug(f"Feed card channel: {path}")
                canonical = channel_lookup.get(path.lower())
                if not canonical or canonical in processed_set:
                    continue

                # Check subscription protection before blocking
                if canonical.lower() in subscriptions:
                    whb = state["would_have_blocked"]
                    if canonical not in whb or not whb[canonical].get("notified"):
                        logging.warning(
                            f"SUBSCRIBED CHANNEL IN BLOCKLIST: {canonical} appears in "
                            f"'{source}' but you're subscribed to it — skipping block. "
                            f"Worth checking if the channel's content has changed recently. "
                            f"(This notice will not repeat. See would_have_blocked in state file.)"
                        )
                        entry = whb.get(canonical, {})
                        entry.setdefault("sources", [])
                        if source not in entry["sources"]:
                            entry["sources"].append(source)
                        entry.setdefault("first_seen", datetime.now().isoformat())
                        entry["notified"] = True
                        whb[canonical] = entry
                        save_state(state)
                    continue

                if dry_run:
                    logging.info(f"WOULD BLOCK: {canonical}")
                    blocked_count += 1
                    found_match_this_pass = True
                    continue

                # Capture display name from card for later unblocking.
                # channel_link (already found above) is the channel anchor — its text IS
                # the display name in YouTube's current yt-lockup-view-model card structure.
                display_name = (channel_link.inner_text() or "").strip() or None

                logging.info(f"Found in feed: {canonical} — blocking...")
                try:
                    success = _click_dont_recommend(page, card)
                except Exception as e:
                    logging.error(f"FAIL {canonical}: {e}")
                    state["stats"]["failed"] += 1
                    save_state(state)
                    continue

                if success:
                    state["processed"].append(canonical)
                    processed_set.add(canonical)
                    state["stats"]["success"] += 1
                    blocked_count += 1
                    found_match_this_pass = True

                    # Record which source is responsible for this block
                    blocked_by = state["blocked_by"]
                    if canonical not in blocked_by:
                        blocked_by[canonical] = {
                            "sources": [source],
                            "blocked_at": datetime.now().isoformat(),
                            "display_name": display_name,
                        }
                    else:
                        if source not in blocked_by[canonical].get("sources", []):
                            blocked_by[canonical]["sources"].append(source)
                        if display_name and not blocked_by[canonical].get("display_name"):
                            blocked_by[canonical]["display_name"] = display_name

                    logging.info(f"[{blocked_count}] OK {canonical}")
                    save_state(state)

                    if blocked_count % LONG_PAUSE_EVERY == 0:
                        logging.info(f"Taking a {LONG_PAUSE_SECONDS}s break...")
                        time.sleep(LONG_PAUSE_SECONDS)
                    else:
                        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

                    break  # rescan after DOM changes
                else:
                    state["stats"]["skipped"] += 1
                    logging.warning(f"SKIP {canonical} (appeared in feed but couldn't block)")
                    save_state(state)

            if found_match_this_pass:
                no_progress_scrolls = 0
            else:
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                time.sleep(2.0)
                no_progress_scrolls += 1

        context.close()

    if dry_run:
        logging.info(f"DRY RUN complete. Would have blocked {blocked_count} channel(s) from this source.")
    else:
        logging.info(f"Done. Blocked {blocked_count} channel(s) this run. Stats: {state['stats']}")


def _perform_browser_unblocks(page, channels: list[str]) -> list[str]:
    """
    Navigate to myactivity.google.com/page?page=youtube_user_feedback and remove
    the 'Don't recommend channel' feedback entry for each channel.

    Requires the channel's display name (as shown on YouTube), which is stored in
    state['blocked_by'][channel]['display_name'] when the block was originally made.
    If no display_name is stored, falls back to navigating to the channel page to
    look it up.

    Google requires password re-verification to view this page. When needed, the
    browser is shown and the user is prompted to enter their password. The run
    then pauses (up to 2 minutes) until verification is complete.

    Returns list of channels that were successfully unblocked on YouTube.
    """
    if not channels:
        return []

    logging.info(f"Reversing YouTube 'Don't recommend' for {len(channels)} channel(s) via myactivity.google.com...")

    state = load_state()
    FEEDBACK_URL = "https://myactivity.google.com/page?page=youtube_user_feedback"

    # Step 1: Resolve display names BEFORE loading the feedback page.
    # (Navigating away from myactivity after verification loses the RAPT token.)
    display_names: dict[str, str] = {}
    for channel in channels:
        display_name = state.get("blocked_by", {}).get(channel, {}).get("display_name")
        if not display_name:
            try:
                page.goto(f"https://www.youtube.com/{channel}", wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)
                title = page.title()
                if title and " - YouTube" in title:
                    name = title.replace(" - YouTube", "").strip()
                    name = re.sub(r'^\(\d+\)\s*', '', name)  # strip notification count prefix
                    display_name = name or None
                if not display_name:
                    for sel in ("ytd-channel-name yt-formatted-string", "#channel-name yt-formatted-string",
                                "h1 yt-formatted-string", "#channel-name a"):
                        el = page.query_selector(sel)
                        if el:
                            display_name = el.inner_text().strip() or None
                            if display_name:
                                break
            except Exception as e:
                logging.warning(f"Could not look up display name for {channel}: {e}")
        if display_name:
            display_names[channel] = display_name
            logging.debug(f"Display name for {channel}: {display_name!r}")
        else:
            logging.warning(f"Could not determine display name for {channel} — will skip unblock.")

    if not display_names:
        logging.error("No display names resolved — cannot proceed with unblock.")
        return []

    # Step 2: Navigate to feedback page and handle verification.
    # Use domcontentloaded — networkidle times out because myactivity has background polling.
    page.goto(FEEDBACK_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(PAGE_LOAD_WAIT)

    dismiss = page.query_selector("button:has-text('Dismiss')")
    if dismiss:
        dismiss.click()
        time.sleep(1)

    verify = page.query_selector("button:has-text('Verify')")
    if verify:
        logging.warning(
            "Google requires password verification to access YouTube user feedback. "
            "Please enter your Google password in the browser window. "
            "This run will pause for up to 2 minutes."
        )
        verify.click()
        # Verify click may trigger a navigation — wait for the page to settle.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:
            pass
        time.sleep(2)

        # Phase 1: wait for the challenge form to appear (up to 15s).
        challenge_appeared = False
        for _ in range(15):
            time.sleep(1)
            try:
                body = page.evaluate("() => document.body.innerText.substring(0, 500)")
            except Exception:
                continue  # page still navigating — try again next tick
            if "Enter your password" in body or "verify it's you" in body.lower():
                challenge_appeared = True
                break

        if not challenge_appeared:
            # Might not need verification after all (RAPT still valid), or
            # the challenge uses different text — proceed optimistically.
            logging.info("Password challenge did not appear — proceeding (RAPT may still be valid).")
        else:
            # Phase 2: poll every 3s for a delete button (positive indicator that
            # verification succeeded and the feedback entries loaded).
            logging.info("Challenge rendered — waiting for you to complete it in the browser window...")
            verified = False
            for i in range(60):  # up to 3 minutes (60 × 3s)
                time.sleep(3)
                try:
                    btn = page.query_selector('button[aria-label^="Delete activity item"]')
                except Exception:
                    continue  # page still navigating
                if btn:
                    logging.info(f"Verification complete — feedback entries loaded ({i*3}s).")
                    verified = True
                    break
                if i % 10 == 0 and i > 0:
                    logging.info(f"Still waiting for verification... ({i*3}s elapsed)")
            if not verified:
                logging.error(
                    "Timed out waiting for Google verification. "
                    "Channels NOT unblocked on YouTube: " + ", ".join(channels)
                )
                return []

        time.sleep(3)

    # Step 3: Find and click delete buttons for each channel.
    unblocked_channels: list[str] = []
    for channel, display_name in display_names.items():
        delete_btn = page.query_selector(f'button[aria-label="Delete activity item {display_name}"]')

        # Entry might be beyond initial load — try "Load more" once
        if not delete_btn:
            load_more = page.query_selector("button:has-text('Load more')")
            if load_more:
                load_more.click()
                time.sleep(2)
                delete_btn = page.query_selector(f'button[aria-label="Delete activity item {display_name}"]')

        if not delete_btn:
            logging.warning(
                f"No feedback entry found for {channel} (display name: {display_name!r}). "
                f"It may already be removed or the entry may be older than what's loaded."
            )
            continue

        delete_btn.click()
        # After clicking the delete icon, Google shows a "Deletion complete" success
        # dialog with a "Got it" button (not a confirm-before-delete prompt).
        # Dismiss it so it doesn't block subsequent deletions.
        got_it = None
        for _ in range(10):
            time.sleep(0.5)
            got_it = page.query_selector(
                "button:has-text('Got it'), [role='dialog'] button:has-text('Got it')"
            )
            if got_it:
                break
        if got_it:
            logging.debug(f"Dismissing 'Deletion complete' dialog for {channel}...")
            got_it.click()
        time.sleep(1.0)
        unblocked_channels.append(channel)
        logging.info(f"UNBLOCKED on YouTube: {channel} ({display_name}) — channel can appear in recommendations again")

    if len(unblocked_channels) < len(channels):
        not_removed = len(channels) - len(unblocked_channels)
        logging.warning(
            f"{not_removed} channel(s) could not be unblocked automatically. "
            f"Visit myactivity.google.com → Other activity → YouTube user feedback to remove them manually."
        )
    return unblocked_channels


def _screenshot(page, path: Path, pr):
    """Take a screenshot, logging a warning if it fails (e.g. window minimized)."""
    try:
        page.mouse.move(0, 0)  # reset hover state before capturing
        time.sleep(0.3)
        page.screenshot(path=str(path))
        pr(f"\nScreenshot: {path}")
    except Exception as e:
        pr(f"\nScreenshot skipped ({e})")


def check_selectors(test_channel: str = "@YouTube") -> bool:
    """
    Diagnostic mode: test whether the DOM selectors still work against live YouTube pages.

    Tests confirmed working and non-working contexts (as of 2026-03-05):
      1. Home feed        — WORKS. 'Don't recommend channel' appears here.
      2. Search results   — DOES NOT WORK. Menu lacks the option.
      3. Channel header   — DOES NOT WORK. No 'More actions' button exists.
      4. Video watch page — DOES NOT WORK. Menu lacks the option.

    The option is exclusive to recommendation feed contexts. The processing
    loop scans the home feed, which is the only viable automated approach.

    Saves a timestamped report and screenshots to ~/.yt-dont-recommend/.
    Returns True if the target option was found (exit code 0), False otherwise (exit code 1).
    """
    from playwright.sync_api import sync_playwright

    date_str = date.today().isoformat()
    report_lines: list[str] = []
    data_dir = PROFILE_DIR.parent

    def pr(msg: str = ""):
        print(msg)
        report_lines.append(msg)

    def test_context(page, label: str) -> bool:
        """Test selectors in one page context. Returns True if the target menu item is found."""
        found_target = False

        pr(f"\n{'=' * 60}")
        pr(f"  {label}")
        pr(f"{'=' * 60}")

        # Video card selectors
        pr("\nVideo card selectors:")
        video_element = None
        for sel in VIDEO_SELECTORS:
            elements = page.query_selector_all(sel)
            count = len(elements)
            pr(f"  {'FOUND (' + str(count) + ')' if count else 'not found':20}  {sel}")
            if count and video_element is None:
                video_element = elements[0]

        if not video_element:
            pr("\n  No video elements found — selectors need updating.")
            return False

        # Hover to reveal the three-dot button
        video_element.scroll_into_view_if_needed()
        video_element.hover()
        time.sleep(0.5)

        # Menu button selectors
        pr("\nMenu button selectors (scoped to video element):")
        menu_btn = None
        for sel in MENU_BTN_SELECTORS:
            btn = video_element.query_selector(sel)
            pr(f"  {'FOUND' if btn else 'not found':20}  {sel}")
            if btn and menu_btn is None:
                menu_btn = btn

        if not menu_btn:
            # Fallback scan
            for btn in video_element.query_selector_all("button"):
                aria = btn.get_attribute("aria-label") or ""
                if any(w in aria.lower() for w in ("action", "menu", "more")):
                    pr(f"  FOUND (fallback)         button[aria-label='{aria}']")
                    menu_btn = btn
                    break

        if not menu_btn:
            pr("\n  Could not find menu button — selectors need updating.")
            return False

        # Open the menu and inspect all items
        menu_btn.click()
        time.sleep(1.0)

        items = page.query_selector_all(MENU_ITEM_SELECTOR)
        pr(f"\nMenu items ({len(items)} found):")
        for item in items:
            text = (item.inner_text() or "").strip()
            if not text:
                continue
            is_target = any(p in text.lower() for p in TARGET_PHRASES)
            marker = "  <-- TARGET" if is_target else ""
            pr(f"  - {text}{marker}")
            if is_target:
                found_target = True

        page.keyboard.press("Escape")
        time.sleep(0.3)

        if not found_target:
            pr("\n  Target option not found in this context.")

        return found_target

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,  # always visible for diagnostics
            args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
            viewport={"width": 1280, "height": 800},
        )
        for extra in context.pages[1:]:
            extra.close()
        page = context.pages[0] if context.pages else context.new_page()

        pr(f"Selector check — {date_str}")
        pr("Navigating to YouTube home page...")
        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        avatar = page.query_selector("button#avatar-btn, img#img[alt]")
        if not avatar:
            pr("ERROR: Not logged in. Run --login first.")
            context.close()
            return False
        pr("Login confirmed.")

        home_ok = test_context(page, "TEST 1: YouTube Home Feed")
        _screenshot(page, data_dir / f"check-home-{date_str}.png", pr)

        # Test 2: search results (the context the processing loop actually uses)
        channel_path = test_channel if test_channel.startswith("/") else f"/{test_channel}"
        query = channel_path[1:] if channel_path.startswith("/@") else channel_path
        search_url = f"https://www.youtube.com/results?search_query={quote(query)}"
        pr(f"\nNavigating to: {search_url}")
        page.goto(search_url, wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        search_ok = test_context(page, f"TEST 2: Search Results ({test_channel})")
        _screenshot(page, data_dir / f"check-search-{date_str}.png", pr)

        # Test 3: channel header "more actions" button (next to Subscribe)
        channel_url = f"https://www.youtube.com{channel_path}"
        pr(f"\nNavigating to: {channel_url}")
        page.goto(channel_url, wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        pr(f"\n{'=' * 60}")
        pr(f"  TEST 3: Channel Header Menu ({test_channel})")
        pr(f"{'=' * 60}")

        # Candidate selectors for the "⋮" button in the channel header
        header_btn_selectors = [
            "ytd-channel-header-renderer button[aria-label='More actions']",
            "ytd-channel-header-renderer button[aria-label='More']",
            "#channel-header-container button[aria-label='More actions']",
            "#channel-header-container button[aria-label='More']",
            "#channel-header ytd-button-renderer:last-child button",
            "ytd-channel-header-renderer ytd-button-renderer button",
        ]

        pr("\nChannel header button selectors:")
        header_btn = None
        for sel in header_btn_selectors:
            btn = page.query_selector(sel)
            pr(f"  {'FOUND' if btn else 'not found':20}  {sel}")
            if btn and header_btn is None:
                header_btn = btn

        # If none found, dump every button with an aria-label so we can identify the right selector
        if not header_btn:
            pr("\n  Dumping all buttons with aria-label (to identify correct selector):")
            for btn in page.query_selector_all("button[aria-label]"):
                aria = btn.get_attribute("aria-label") or ""
                # Walk up to find the nearest ancestor with an id or a known tag name
                ancestor = btn.evaluate(
                    "el => { let n = el; while(n) { if(n.id) return '#' + n.id;"
                    " if(n.tagName && n.tagName.includes('-')) return n.tagName.toLowerCase();"
                    " n = n.parentElement; } return 'unknown'; }"
                )
                pr(f"    [{ancestor}]  aria-label='{aria}'")

        header_ok = False
        if not header_btn:
            pr("\n  Could not find channel header button.")
        else:
            header_btn.click()
            time.sleep(1.0)

            items = page.query_selector_all(MENU_ITEM_SELECTOR)
            pr(f"\nMenu items ({len(items)} found):")
            for item in items:
                text = (item.inner_text() or "").strip()
                if not text:
                    continue
                is_target = any(p in text.lower() for p in TARGET_PHRASES)
                marker = "  <-- TARGET" if is_target else ""
                pr(f"  - {text}{marker}")
                if is_target:
                    header_ok = True

            page.keyboard.press("Escape")
            time.sleep(0.3)

            if not header_ok:
                pr("\n  Target option not found in channel header menu.")

        _screenshot(page, data_dir / f"check-header-{date_str}.png", pr)

        # Test 4: video watch page — menu below the video title
        pr(f"\n{'=' * 60}")
        pr(f"  TEST 4: Video Watch Page Menu ({test_channel})")
        pr(f"{'=' * 60}")

        # Navigate to the channel's videos page to get a video link
        videos_url = f"https://www.youtube.com{channel_path}/videos"
        pr(f"\nNavigating to {videos_url} to find a video...")
        page.goto(videos_url, wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        # Grab the href of the first video link
        video_link = page.query_selector("a#video-title-link, a#thumbnail[href*='/watch']")
        watch_url = None
        if video_link:
            href = video_link.get_attribute("href") or ""
            if href.startswith("/watch"):
                watch_url = f"https://www.youtube.com{href}"
            elif href.startswith("http"):
                watch_url = href

        watch_ok = False
        if not watch_url:
            pr("  Could not find a video link on the channel page.")
        else:
            pr(f"  Found video: {watch_url}")
            page.goto(watch_url, wait_until="domcontentloaded")
            time.sleep(PAGE_LOAD_WAIT + 1)  # video pages load slower

            # The "..." menu below the video title (not the player controls)
            watch_menu_selectors = [
                "ytd-menu-renderer button[aria-label='More actions']",
                "ytd-watch-metadata button[aria-label='More actions']",
                "#actions button[aria-label='More actions']",
                "#top-level-buttons-computed ~ ytd-menu-renderer button",
                "ytd-watch-metadata ytd-menu-renderer button",
            ]

            pr("\nVideo page menu button selectors:")
            watch_btn = None
            for sel in watch_menu_selectors:
                # Use locator to find a VISIBLE instance (multiple may exist in DOM)
                loc = page.locator(sel).filter(has_not=page.locator("[hidden]"))
                count = loc.count()
                pr(f"  {'FOUND (' + str(count) + ')' if count else 'not found':20}  {sel}")
                if count and watch_btn is None:
                    # Pick the first visible one
                    for i in range(count):
                        candidate = loc.nth(i)
                        try:
                            if candidate.is_visible():
                                watch_btn = candidate
                                break
                        except Exception:
                            pass

            if not watch_btn:
                pr("\n  Could not find a visible video page menu button.")
            else:
                watch_btn.scroll_into_view_if_needed()
                time.sleep(0.3)
                watch_btn.click()
                time.sleep(1.0)

                items = page.query_selector_all(MENU_ITEM_SELECTOR)
                pr(f"\nMenu items ({len(items)} found):")
                for item in items:
                    text = (item.inner_text() or "").strip()
                    if not text:
                        continue
                    is_target = any(p in text.lower() for p in TARGET_PHRASES)
                    marker = "  <-- TARGET" if is_target else ""
                    pr(f"  - {text}{marker}")
                    if is_target:
                        watch_ok = True

                page.keyboard.press("Escape")
                time.sleep(0.3)

                if not watch_ok:
                    pr("\n  Target option not found in video page menu.")

        _screenshot(page, data_dir / f"check-watch-{date_str}.png", pr)

        context.close()

    # Summary
    pr(f"\n{'=' * 60}")
    pr("  SUMMARY")
    pr(f"{'=' * 60}")
    pr(f"  Home feed:      {'PASS' if home_ok else 'FAIL'}")
    pr(f"  Search results: {'PASS' if search_ok else 'FAIL'}")
    pr(f"  Channel header: {'PASS' if header_ok else 'FAIL'}")
    pr(f"  Video page:     {'PASS' if watch_ok else 'FAIL'}")

    if watch_ok:
        pr("\n  Video page approach works — navigate to a video from the channel,")
        pr("  then use the menu below the video title.")
    elif header_ok:
        pr("\n  Channel header approach works — most efficient option.")
        pr("  Navigate to channel page and use the header menu.")
    elif search_ok:
        pr("\n  Search results approach works.")
    elif home_ok:
        pr("\n  Only the home feed works. The processing loop needs a feed-scanning approach.")
    else:
        pr("\n  Target not found in any context.")
        pr("  Check the screenshots and menu item lists above, then update selectors.")

    report_path = data_dir / f"selector-check-{date_str}.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    pr(f"\nReport saved: {report_path}")

    return home_ok or channel_ok


# --- Main ---

def main():
    builtin_keys = ", ".join(BUILTIN_SOURCES.keys())

    parser = argparse.ArgumentParser(
        description="Bulk-train YouTube's recommendation algorithm by triggering 'Don't recommend channel'.",
        epilog="First run: use --login to authenticate. Then run normally to process.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--login", action="store_true",
                        help="Open browser to log into YouTube (do this first)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and display the blocklist without taking any action")
    parser.add_argument(
        "--source",
        default=None,
        metavar="SOURCE",
        help=(
            f"Blocklist source. Built-in names: {builtin_keys} (comma-separated for multiple). "
            "Also accepts a local file path or an HTTP/HTTPS URL. "
            f"Defaults to all built-in sources ({', '.join(DEFAULT_SOURCES)}) when not specified. "
            "Text format: one entry per line (@handle or UCxxx), # for comments."
        ),
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after blocking this many channels")
    parser.add_argument(
        "--unblock-policy", choices=["all", "any"], default="all",
        help=(
            "When a channel is dropped from a blocklist, when to unblock it. "
            "'all' (default): only unblock when removed from every source that blocked it. "
            "'any': unblock as soon as any single source drops it."
        ),
    )
    parser.add_argument(
        "--exclude",
        default=None,
        metavar="SOURCE",
        help=(
            f"Channels to never block, regardless of the blocklist. "
            f"Accepts a local file path or HTTP/HTTPS URL in the same plain-text format as --source. "
            f"If not specified, {DEFAULT_EXCLUDE_FILE} is loaded automatically if it exists."
        ),
    )
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (no visible window)")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--reset-state", action="store_true",
                        help="Clear the processed-channels state and start fresh")
    parser.add_argument("--list-sources", action="store_true",
                        help="Show available built-in blocklist sources")
    parser.add_argument("--stats", action="store_true",
                        help="Show processing statistics and exit")
    parser.add_argument("--check-selectors", action="store_true",
                        help=(
                            "Test whether the DOM selectors still work against live YouTube. "
                            "Opens a visible browser, checks both home feed and a channel page, "
                            "and saves a report with screenshots. Run this after YouTube updates break things."
                        ))
    parser.add_argument("--test-channel", default="@YouTube", metavar="CHANNEL",
                        help=(
                            "Channel to use for the --check-selectors channel page test "
                            "(default: @YouTube)"
                        ))

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.list_sources:
        print("\nBuilt-in blocklist sources:\n")
        for key, src in BUILTIN_SOURCES.items():
            print(f"  {key:12s} - {src['description']}")
            print(f"  {'':12s}   {src['url']}")
            print()
        print("You can also pass a local file path or any HTTP/HTTPS URL to --source.\n")
        return

    if args.stats:
        state = load_state()
        whb = state.get("would_have_blocked", {})
        print(f"\nBlocked channels   : {len(state['processed'])}")
        print(f"Last run           : {state.get('last_run', 'never')}")
        print(f"Stats              : {state.get('stats', {})}")
        if whb:
            print(f"\nSubscribed channels in blocklist (skipped, notified once):")
            for ch, info in whb.items():
                print(f"  {ch}  (sources: {info.get('sources', [])}, first seen: {info.get('first_seen', '?')[:10]})")
        print(f"\nState file         : {STATE_FILE}")
        print(f"Log file           : {LOG_FILE}")
        return

    if args.reset_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            logging.info("State reset.")
        else:
            logging.info("No state file to reset.")
        return

    if args.login:
        do_login()
        return

    if args.check_selectors:
        ok = check_selectors(args.test_channel)
        sys.exit(0 if ok else 1)

    # Resolve which sources to run
    if args.source is None:
        sources = DEFAULT_SOURCES
    else:
        sources = [s.strip() for s in args.source.split(",")]

    # Build exclusion set once (shared across all sources)
    exclude_source = args.exclude or (str(DEFAULT_EXCLUDE_FILE) if DEFAULT_EXCLUDE_FILE.exists() else None)
    exclude_set: set[str] = set()
    if exclude_source:
        exclude_set = {c.lower() for c in resolve_source(exclude_source)}
        label = "--exclude" if args.exclude else f"default exclude file ({DEFAULT_EXCLUDE_FILE})"
        logging.info(f"Loaded {len(exclude_set)} exclusion(s) via {label}")

    for source in sources:
        if len(sources) > 1:
            logging.info(f"--- Source: {source} ---")
        try:
            channels = resolve_source(source)
        except RuntimeError as e:
            logging.error(f"Could not load source '{source}': {e} — skipping.")
            continue
        if exclude_set:
            before = len(channels)
            channels = [c for c in channels if c.lower() not in exclude_set]
            logging.info(f"Excluded {before - len(channels)} channel(s) ({len(channels)} remaining)")
        process_channels(
            channels,
            source=source,
            dry_run=args.dry_run,
            limit=args.limit,
            headless=args.headless,
            unblock_policy=args.unblock_policy,
        )


if __name__ == "__main__":
    main()

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

    # Resume from a specific channel (skip already-processed ones)
    python yt_dont_recommend.py --resume-from "/@SomeChannel"

    # Check whether current selectors still work against live YouTube
    python yt_dont_recommend.py --check-selectors

Requirements:
    pip install playwright --break-system-packages
    playwright install chromium

Blocklist format (plain text):
    # Comments start with #
    # Blank lines are ignored
    # Entries are YouTube channel paths:
    /@SomeHandle
    /@AnotherChannel
    /channel/UCxxxxxxxxxxxxxxxxxxxxxxxx

Author: Chris Means (generated with Claude)
License: MIT
"""

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse
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
        "url": "https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/blacklist.json",
        "format": "json",
        "description": "Community-maintained list from AiBlock extension",
    },
}

DEFAULT_SOURCE = "deslop"

# Browser profile directory (persists login state between runs)
PROFILE_DIR = Path.home() / ".yt-dont-recommend" / "browser-profile"

# State file to track which channels have been processed
STATE_FILE = Path.home() / ".yt-dont-recommend" / "processed.json"

# Log file
LOG_FILE = Path.home() / ".yt-dont-recommend" / "run.log"

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
    "button[aria-label='Action menu']",
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
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_FILE, mode="a"),
        ],
    )


# --- State Management ---

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"processed": [], "last_run": None, "stats": {"success": 0, "skipped": 0, "failed": 0}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now().isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Blocklist Fetching ---

def parse_text_blocklist(raw: str) -> list[str]:
    """Parse plain text blocklist: one channel path per line, # comments."""
    channels = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        channels.append(line)
    return channels


def parse_json_blocklist(raw: str) -> list[str]:
    """Parse JSON blocklist. Handles several common formats."""
    channels = []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, str):
                    channels.append(entry)
                elif isinstance(entry, dict):
                    for key in ("channelHandle", "handle", "channelId", "id", "url"):
                        if key in entry:
                            val = entry[key]
                            if val.startswith("http"):
                                path = urlparse(val).path
                                channels.append(path)
                            elif val.startswith("UC"):
                                channels.append(f"/channel/{val}")
                            elif val.startswith("@"):
                                channels.append(f"/@{val.lstrip('@')}")
                            else:
                                channels.append(val)
                            break
        elif isinstance(data, dict):
            for key in data:
                if key.startswith("UC"):
                    channels.append(f"/channel/{key}")
                elif key.startswith("@"):
                    channels.append(f"/@{key.lstrip('@')}")
    except json.JSONDecodeError:
        logging.warning("Failed to parse as JSON; falling back to line-by-line text parsing")
        channels = parse_text_blocklist(raw)
    return channels


def fetch_remote(url: str) -> str:
    req = Request(url, headers={"User-Agent": "yt-dont-recommend/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


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


def channel_to_url(channel_path: str) -> str:
    if channel_path.startswith("http"):
        return channel_path
    return f"https://www.youtube.com{channel_path}"


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


def _find_menu_btn(video_element):
    """Find the three-dot menu button within a video element. Returns element or None."""
    for sel in MENU_BTN_SELECTORS:
        btn = video_element.query_selector(sel)
        if btn:
            return btn
    # Fallback: any button whose aria-label contains action/menu/more
    for btn in video_element.query_selector_all("button"):
        label = (btn.get_attribute("aria-label") or "").lower()
        if any(w in label for w in ("action", "menu", "more")):
            return btn
    return None


def dont_recommend_channel(page, channel_url: str, channel_id: str) -> bool:
    """
    Navigate to a channel's /videos page, find a video, and click
    'Don't recommend channel' from its context menu.

    NOTE: These selectors have not been verified against a live YouTube page.
    YouTube changes its DOM frequently. Run --check-selectors first to confirm
    everything works before bulk processing.

    Returns True if successful, False if skipped.
    """
    videos_url = channel_url.rstrip("/") + "/videos"
    page.goto(videos_url, wait_until="domcontentloaded")
    time.sleep(PAGE_LOAD_WAIT)

    error_el = page.query_selector("#error-page, [class*='error']")
    if error_el:
        logging.debug(f"Error page for: {channel_url}")
        return False

    video_element = None
    for selector in VIDEO_SELECTORS:
        video_element = page.query_selector(selector)
        if video_element:
            break

    if not video_element:
        logging.debug(f"No videos found on {channel_url}")
        return False

    video_element.scroll_into_view_if_needed()
    video_element.hover()
    time.sleep(0.5)

    menu_btn = _find_menu_btn(video_element)
    if not menu_btn:
        logging.debug(f"Could not find menu button on {channel_url}")
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
        logging.debug(f"'Don't recommend channel' not found in menu for {channel_url}")
        return False

    target_item.click()
    time.sleep(0.5)
    return True


def process_channels(channels: list[str], dry_run: bool = False,
                     limit: int | None = None, resume_from: str | None = None,
                     headless: bool = False):
    """
    For each channel in the list, navigate to its page, find a video,
    and trigger 'Don't recommend channel'.
    """
    from playwright.sync_api import sync_playwright

    state = load_state()
    processed_set = set(state["processed"])

    remaining = [c for c in channels if c not in processed_set]
    logging.info(f"{len(channels)} total, {len(processed_set)} already processed, {len(remaining)} remaining")

    if resume_from:
        try:
            idx = remaining.index(resume_from)
            remaining = remaining[idx:]
            logging.info(f"Resuming from {resume_from}, {len(remaining)} channels left")
        except ValueError:
            logging.warning(f"Channel {resume_from} not found in remaining list; processing all")

    if limit:
        remaining = remaining[:limit]
        logging.info(f"Limited to {limit} channels")

    if not remaining:
        logging.info("Nothing to process!")
        return

    if dry_run:
        logging.info("DRY RUN — would process these channels:")
        for ch in remaining:
            logging.info(f"  {channel_to_url(ch)}")
        return

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()

        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        avatar = page.query_selector("button#avatar-btn, img#img[alt]")
        if not avatar:
            logging.error("Not logged in. Run with --login first.")
            context.close()
            return

        logging.info("Logged in. Starting to process channels...")

        for i, channel_path in enumerate(remaining):
            channel_url = channel_to_url(channel_path)

            try:
                success = dont_recommend_channel(page, channel_url, channel_path)

                if success:
                    state["processed"].append(channel_path)
                    state["stats"]["success"] += 1
                    logging.info(f"[{i+1}/{len(remaining)}] OK {channel_path}")
                else:
                    state["stats"]["skipped"] += 1
                    logging.warning(f"[{i+1}/{len(remaining)}] SKIP {channel_path} (menu option not found)")

            except Exception as e:
                state["stats"]["failed"] += 1
                logging.error(f"[{i+1}/{len(remaining)}] FAIL {channel_path}: {e}")

            save_state(state)

            if (i + 1) % LONG_PAUSE_EVERY == 0:
                logging.info(f"Taking a {LONG_PAUSE_SECONDS}s break to avoid rate limiting...")
                time.sleep(LONG_PAUSE_SECONDS)
            else:
                time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

        context.close()

    logging.info(f"Done. Stats: {state['stats']}")


def check_selectors(test_channel: str = "/@YouTube") -> bool:
    """
    Diagnostic mode: test whether the DOM selectors still work against live YouTube pages.

    Tests two contexts:
      1. YouTube home feed  — where "Don't recommend channel" most likely appears
      2. A channel's /videos page  — where the current processing loop navigates

    This answers two questions at once: are the selectors correct, and does the
    option actually appear in each context? If it only appears in the home feed,
    the processing approach needs to change.

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
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 800},
        )
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
        page.screenshot(path=str(data_dir / f"check-home-{date_str}.png"))
        pr(f"\nScreenshot: {data_dir / f'check-home-{date_str}.png'}")

        # Build channel /videos URL
        channel_path = test_channel if test_channel.startswith("/") else f"/{test_channel}"
        channel_url = f"https://www.youtube.com{channel_path.rstrip('/')}/videos"
        pr(f"\nNavigating to: {channel_url}")
        page.goto(channel_url, wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        channel_ok = test_context(page, f"TEST 2: Channel /videos Page ({test_channel})")
        page.screenshot(path=str(data_dir / f"check-channel-{date_str}.png"))
        pr(f"\nScreenshot: {data_dir / f'check-channel-{date_str}.png'}")

        context.close()

    # Summary
    pr(f"\n{'=' * 60}")
    pr("  SUMMARY")
    pr(f"{'=' * 60}")
    pr(f"  Home feed:    {'PASS' if home_ok else 'FAIL'}")
    pr(f"  Channel page: {'PASS' if channel_ok else 'FAIL'}")

    if home_ok and not channel_ok:
        pr("\n  ACTION NEEDED: The option is only available from the home feed,")
        pr("  not from a channel's own /videos page. The processing loop needs")
        pr("  to be redesigned to work from home feed recommendations instead.")
    elif channel_ok:
        pr("\n  Channel page approach works. The current implementation should function correctly.")
    elif home_ok:
        pr("\n  Home feed works but channel page does not.")
        pr("  Consider switching the processing approach to use home feed.")
    else:
        pr("\n  Target not found in either context.")
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
        default=DEFAULT_SOURCE,
        metavar="SOURCE",
        help=(
            f"Blocklist source. Built-in names: {builtin_keys}. "
            "Also accepts a local file path or an HTTP/HTTPS URL. "
            f"Default: {DEFAULT_SOURCE}. "
            "Text format: one channel path per line (/@handle or /channel/UCxxx), # for comments."
        ),
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum number of channels to process this run")
    parser.add_argument("--resume-from", type=str, default=None,
                        help="Resume from this channel path (skips all channels before it)")
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
    parser.add_argument("--test-channel", default="/@YouTube", metavar="CHANNEL_PATH",
                        help=(
                            "Channel path to use for the --check-selectors channel page test "
                            "(default: /@YouTube)"
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
        print(f"\nProcessed channels : {len(state['processed'])}")
        print(f"Last run           : {state.get('last_run', 'never')}")
        print(f"Stats              : {state.get('stats', {})}")
        print(f"State file         : {STATE_FILE}")
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

    channels = resolve_source(args.source)
    if not channels:
        logging.error("No channels found. Check your source or its format.")
        return

    process_channels(
        channels,
        dry_run=args.dry_run,
        limit=args.limit,
        resume_from=args.resume_from,
        headless=args.headless,
    )


if __name__ == "__main__":
    main()

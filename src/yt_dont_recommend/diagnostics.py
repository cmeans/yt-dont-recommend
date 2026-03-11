"""
Diagnostic tools: check_selectors and _screenshot.

Provides the --check-selectors mode that tests DOM selectors across four YouTube
page contexts and saves a timestamped report with screenshots to ~/.yt-dont-recommend/.
"""

import logging
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

from .config import (
    PROFILE_DIR,
    PAGE_LOAD_WAIT,
    VIDEO_SELECTORS,
    MENU_BTN_SELECTORS,
    MENU_ITEM_SELECTOR,
    TARGET_PHRASES,
)


def _screenshot(page: Any, path: Path, pr: Any) -> None:
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

    def test_context(page: Any, label: str) -> bool:
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
            args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 800},  # fixed baseline for reproducible screenshots
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

    return home_ok or header_ok

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
    PAGE_LOAD_WAIT,
    PROFILE_DIR,
    get_selectors,
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


def _discover_feed_card(page: Any) -> str | None:
    """Discover the feed card container selector by finding repeating elements
    that each contain a watch link and a menu button.

    Returns a CSS tag selector (e.g. 'ytd-rich-item-renderer') or None.
    """
    # JS snippet: find custom element tag names that repeat 5+ times in the
    # main content area and each contain a watch link.
    result = page.evaluate("""() => {
        const candidates = {};
        // Look inside #contents or fall back to the whole page
        const root = document.querySelector('#contents') || document.body;
        for (const el of root.querySelectorAll('*')) {
            const tag = el.tagName.toLowerCase();
            // Only consider custom elements (contain a hyphen)
            if (!tag.includes('-')) continue;
            // Must contain a watch link
            if (!el.querySelector("a[href*='/watch?v=']")) continue;
            candidates[tag] = (candidates[tag] || 0) + 1;
        }
        // Return the tag with the highest count that has 5+ hits
        let best = null, bestCount = 0;
        for (const [tag, count] of Object.entries(candidates)) {
            if (count >= 5 && count > bestCount) {
                best = tag;
                bestCount = count;
            }
        }
        return best;
    }""")
    return result


def _discover_channel_link(page: Any, card_sel: str) -> str | None:
    """Discover the channel link selector inside a card by looking for <a> elements
    whose href matches YouTube channel URL patterns.

    Returns a CSS selector string or None.
    """
    card = page.query_selector(card_sel)
    if not card:
        return None
    # Try known patterns in order of specificity
    for pattern in [
        "a[href^='/@']",
        "a[href^='/channel/UC']",
        "a[href*='/@']",
    ]:
        link = card.query_selector(pattern)
        if link:
            href = link.get_attribute("href") or ""
            if "/@" in href or "/channel/UC" in href:
                return pattern
    # Fallback: scan all <a> tags for channel-like hrefs
    for link in card.query_selector_all("a[href]"):
        href = link.get_attribute("href") or ""
        if href.startswith("/@") or href.startswith("/channel/UC"):
            return "a[href^='/@'], a[href^='/channel/UC']"
    return None


def _discover_menu_button(page: Any, card_sel: str) -> str | None:
    """Discover the three-dot menu button selector by hovering a card and scanning
    buttons for action/menu/more aria-labels.

    Returns a CSS selector string or None.
    """
    card = page.query_selector(card_sel)
    if not card:
        return None
    card.scroll_into_view_if_needed()
    card.hover()
    time.sleep(0.5)

    for btn in card.query_selector_all("button"):
        label = (btn.get_attribute("aria-label") or "").strip()
        if not label:
            continue
        if any(w in label.lower() for w in ("action", "menu", "more")):
            return f"button[aria-label='{label}']"
    return None


def _discover_menu_phrases(page: Any, card_sel: str, menu_btn_sel: str) -> list[str]:
    """Click the menu button on a card and return all visible menu item texts.

    The caller can inspect these to find localized equivalents of
    'Don't recommend channel' and 'Not interested'.
    """
    card = page.query_selector(card_sel)
    if not card:
        return []
    card.scroll_into_view_if_needed()
    card.hover()
    time.sleep(0.5)
    btn = card.query_selector(menu_btn_sel)
    if not btn:
        return []
    btn.click()
    time.sleep(1.0)

    # Collect text from any visible menu items using a broad selector
    texts = []
    for sel in [
        "ytd-menu-service-item-renderer",
        "tp-yt-paper-item",
        "ytd-menu-navigation-item-renderer",
        "[role='menuitem']",
        "yt-list-item-view-model",
    ]:
        for item in page.query_selector_all(sel):
            text = (item.inner_text() or "").strip()
            if text and text not in texts:
                texts.append(text)

    page.keyboard.press("Escape")
    time.sleep(0.3)
    return texts


def discover_selectors(page: Any, pr: Any = None) -> dict:
    """Run discovery heuristics on the home feed page and return a dict of
    discovered selector overrides.  Only includes keys where a working
    selector was found.

    ``pr`` is an optional print function (used by check_selectors for reporting).
    """
    if pr is None:
        pr = lambda msg="": None  # noqa: E731

    overrides: dict = {}

    # 1. Feed card container
    pr("\n  Discovering feed card selector...")
    card_sel = _discover_feed_card(page)
    if card_sel:
        pr(f"  Found: {card_sel}")
        overrides["feed_card"] = card_sel
    else:
        pr("  FAILED: could not identify feed card container.")
        return overrides  # can't continue without cards

    # 2. Channel link inside card
    pr("  Discovering channel link selector...")
    ch_link = _discover_channel_link(page, card_sel)
    if ch_link:
        pr(f"  Found: {ch_link}")
        overrides["channel_link"] = ch_link
    else:
        pr("  FAILED: no channel links found in cards.")

    # 3. Menu button
    pr("  Discovering menu button selector...")
    menu_btn = _discover_menu_button(page, card_sel)
    if menu_btn:
        pr(f"  Found: {menu_btn}")
        overrides["menu_buttons"] = [menu_btn]
    else:
        pr("  FAILED: no menu button found on cards.")
        return overrides  # can't discover menu items without a button

    # 4. Menu item texts (for localization + menu_items selector)
    pr("  Discovering menu items...")
    # Need a second card since we already used the first one
    cards = page.query_selector_all(card_sel)
    if len(cards) >= 2:
        card = cards[1]
        card.scroll_into_view_if_needed()
        card.hover()
        time.sleep(0.5)
        btn = card.query_selector(menu_btn)
        if btn:
            btn.click()
            time.sleep(1.0)
            texts = []
            for sel in [
                "ytd-menu-service-item-renderer",
                "tp-yt-paper-item",
                "ytd-menu-navigation-item-renderer",
                "[role='menuitem']",
                "yt-list-item-view-model",
            ]:
                for item in page.query_selector_all(sel):
                    text = (item.inner_text() or "").strip()
                    if text and text not in texts:
                        texts.append(text)
            page.keyboard.press("Escape")
            time.sleep(0.3)
        else:
            texts = []
    else:
        texts = _discover_menu_phrases(page, card_sel, menu_btn)

    if texts:
        pr(f"  Menu items found: {texts}")
        # Try to auto-detect "don't recommend" and "not interested" phrases
        for text in texts:
            lower = text.lower()
            if "recommend" in lower and "don" in lower:
                pr(f"  Auto-detected 'Don't recommend' phrase: {text!r}")
                overrides["dont_recommend_phrases"] = [lower]
            elif "not interested" in lower or "no me interesa" in lower:
                pr(f"  Auto-detected 'Not interested' phrase: {text!r}")
                overrides["not_interested_phrase"] = lower
    else:
        pr("  WARNING: could not enumerate menu items.")

    return overrides


def check_selectors(test_channel: str = "@YouTube", repair: bool = False) -> bool:
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

    from .config import VIDEO_SELECTORS

    sels = get_selectors()
    dr_phrases = sels["dont_recommend_phrases"]

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
        for sel in sels["menu_buttons"]:
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

        items = page.query_selector_all(sels["menu_items"])
        pr(f"\nMenu items ({len(items)} found):")
        for item in items:
            text = (item.inner_text() or "").strip()
            if not text:
                continue
            is_target = any(p in text.lower() for p in dr_phrases)
            marker = "  <-- TARGET" if is_target else ""
            pr(f"  - {text}{marker}")
            if is_target:
                found_target = True

        page.keyboard.press("Escape")
        time.sleep(0.3)

        if not found_target:
            pr("\n  Target option not found in this context.")

        return found_target

    from .config import clear_profile_cache, ensure_data_dir
    ensure_data_dir()
    PROFILE_DIR.mkdir(exist_ok=True, mode=0o700)

    with sync_playwright() as p:
        from .browser import _launch_context
        context = _launch_context(
            p, PROFILE_DIR,
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

        # button#avatar-btn exists on both logged-in and logged-out pages.
        # The notification bell only appears when a Google account is active.
        avatar = page.query_selector(sels["login_check"])
        if not avatar:
            pr("ERROR: Not logged in. Run --login first.")
            context.close()
            clear_profile_cache()
            return False
        pr("Login confirmed.")

        home_ok = test_context(page, "TEST 1: YouTube Home Feed")
        _screenshot(page, data_dir / f"check-home-{date_str}.png", pr)

        # --- Repair mode ---
        if repair:
            if home_ok:
                pr("\n  --repair: Home feed selectors are working — no repair needed.")
            else:
                pr(f"\n{'=' * 60}")
                pr("  REPAIR: Attempting selector discovery...")
                pr(f"{'=' * 60}")
                # Navigate back to home feed (test_context may have scrolled/changed state)
                page.goto("https://www.youtube.com", wait_until="domcontentloaded")
                time.sleep(PAGE_LOAD_WAIT)

                overrides = discover_selectors(page, pr=pr)
                if overrides:
                    pr(f"\n  Discovered {len(overrides)} selector override(s).")
                    try:
                        from .config import write_selector_overrides
                        write_selector_overrides(overrides)
                        pr(f"  Written to {data_dir / 'config.yaml'}")

                        # Re-test with the new selectors
                        pr("\n  Re-testing with discovered selectors...")
                        # Reload selectors from the config we just wrote
                        sels = get_selectors()
                        dr_phrases = sels["dont_recommend_phrases"]
                        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
                        time.sleep(PAGE_LOAD_WAIT)
                        home_ok = test_context(page, "TEST 1 (retry): YouTube Home Feed")
                        if home_ok:
                            pr("\n  REPAIR SUCCESSFUL — selectors are working.")
                        else:
                            pr("\n  REPAIR INCOMPLETE — re-test still failing.")
                            pr("  The discovered selectors may be partially correct.")
                            pr("  Check the report above and adjust config.yaml manually.")
                    except ImportError:
                        pr("  ERROR: pyyaml is not installed — cannot write config.yaml.")
                        pr("  Install with: pip install pyyaml")
                        pr("  Then add these to ~/.yt-dont-recommend/config.yaml manually:")
                        for k, v in overrides.items():
                            pr(f"    {k}: {v!r}")
                else:
                    pr("\n  Discovery found no working selectors.")
                    pr("  YouTube may be serving a completely new layout.")
                    pr("  Please report this at: https://github.com/cmeans/yt-dont-recommend/issues/13")

        # Test 2: search results (the context the processing loop actually uses)
        channel_path = test_channel if test_channel.startswith("/") else f"/{test_channel}"
        query = channel_path[1:] if channel_path.startswith("/@") else channel_path
        search_url = f"https://www.youtube.com/results?search_query={quote(query)}"
        pr(f"\nNavigating to: {search_url}")
        page.goto(search_url, wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        search_ok = test_context(page, f"TEST 2: Search Results ({test_channel}) [expected: no option]")
        _screenshot(page, data_dir / f"check-search-{date_str}.png", pr)

        # Test 3: channel header "more actions" button (next to Subscribe)
        channel_url = f"https://www.youtube.com{channel_path}"
        pr(f"\nNavigating to: {channel_url}")
        page.goto(channel_url, wait_until="domcontentloaded")
        time.sleep(PAGE_LOAD_WAIT)

        pr(f"\n{'=' * 60}")
        pr(f"  TEST 3: Channel Header Menu ({test_channel}) [expected: no option]")
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

            items = page.query_selector_all(sels["menu_items"])
            pr(f"\nMenu items ({len(items)} found):")
            for item in items:
                text = (item.inner_text() or "").strip()
                if not text:
                    continue
                is_target = any(p in text.lower() for p in dr_phrases)
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
        pr(f"  TEST 4: Video Watch Page Menu ({test_channel}) [expected: no option]")
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

                items = page.query_selector_all(sels["menu_items"])
                pr(f"\nMenu items ({len(items)} found):")
                for item in items:
                    text = (item.inner_text() or "").strip()
                    if not text:
                        continue
                    is_target = any(p in text.lower() for p in dr_phrases)
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

    clear_profile_cache()

    # Summary
    pr(f"\n{'=' * 60}")
    pr("  SUMMARY")
    pr(f"{'=' * 60}")
    pr(f"  Home feed:      {'PASS' if home_ok else 'FAIL'}  ← this is what matters")
    pr(f"  Search results: {'PASS' if search_ok else 'expected (no option)':>22}")
    pr(f"  Channel header: {'PASS' if header_ok else 'expected (no option)':>22}")
    pr(f"  Video page:     {'PASS' if watch_ok else 'expected (no option)':>22}")
    pr()
    pr('  "Don\'t recommend channel" only appears in the home feed.')
    pr("  Tests 2-4 confirm this is still the case — FAIL is normal there.")

    if home_ok:
        pr("\n  Home feed selectors are working. No action needed.")
    else:
        pr("\n  HOME FEED FAILED — selectors need repair.")
        pr("  Run --check-selectors --repair to attempt auto-discovery.")

    report_path = data_dir / f"selector-check-{date_str}.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    pr(f"\nReport saved: {report_path}")

    return home_ok or header_ok

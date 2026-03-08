"""
experiments/probe_not_interested.py

Probe script: open the YouTube home feed, find video cards, and explore
what menu items are available in the three-dot "More actions" menu —
specifically looking for "Not interested" and its exact selector.

Also extracts video titles and IDs from each card so we have the raw
material to pass to the clickbait classifier later.

Run:
    .venv/bin/python experiments/probe_not_interested.py

Uses the saved browser profile (must have run --login first).
"""

import sys
import time
from pathlib import Path

# Reuse the profile location from the main app
PROFILE_DIR = Path.home() / ".yt-dont-recommend" / "browser-profile"

if not PROFILE_DIR.exists():
    print("No browser profile found. Run: yt-dont-recommend --login")
    sys.exit(1)

from playwright.sync_api import sync_playwright

PAGE_LOAD_WAIT = 5   # seconds after navigation before querying
RENDER_WAIT   = 8   # seconds to wait for lazy content to render


def extract_video_id(href: str) -> str | None:
    """Extract video ID from a /watch?v=... href."""
    if not href:
        return None
    if "v=" in href:
        return href.split("v=")[-1].split("&")[0]
    return None


def wait_for_content(page) -> None:
    """Wait until at least one card has a non-empty title."""
    deadline = time.monotonic() + RENDER_WAIT
    while time.monotonic() < deadline:
        titles = page.query_selector_all(
            "ytd-rich-item-renderer h3 yt-formatted-string, "
            "ytd-rich-item-renderer #video-title"
        )
        non_empty = [t for t in titles if t.inner_text().strip()]
        if non_empty:
            print(f"Content ready — {len(non_empty)} titled cards visible.\n")
            return
        time.sleep(0.5)
    print("Warning: timed out waiting for card content to render.\n")


def get_title(card) -> str:
    """Try several selectors to extract the video title from a card."""
    for sel in (
        "h3 yt-formatted-string#video-title",
        "yt-formatted-string#video-title",
        "#video-title",
        "h3 a#video-title-link",
        "#title-wrapper yt-formatted-string",
    ):
        el = card.query_selector(sel)
        if el:
            text = el.inner_text().strip()
            if text:
                return text
    return "(no title)"


def get_href(card) -> str | None:
    """Try several selectors to extract the video href from a card."""
    for sel in (
        "a#video-title-link",
        "a#thumbnail",
        "h3 a",
        "ytd-thumbnail a",
    ):
        el = card.query_selector(sel)
        if el:
            href = el.get_attribute("href")
            if href:
                return href
    return None


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        page.goto("https://www.youtube.com", wait_until="networkidle", timeout=60000)
        time.sleep(PAGE_LOAD_WAIT)

        print("\n=== Waiting for cards to render... ===\n")
        wait_for_content(page)

        cards = page.query_selector_all("ytd-rich-item-renderer")
        print(f"Found {len(cards)} cards total.\n")

        probed = 0
        for i, card in enumerate(cards):
            title = get_title(card)
            href  = get_href(card)
            video_id = extract_video_id(href) if href else None

            if title == "(no title)" and not video_id:
                continue  # skip unrendered / ad / shelf cards

            print(f"[{i+1}] {title!r}")
            print(f"     video_id: {video_id}")

            # Scroll card into view so the menu button is reachable
            card.scroll_into_view_if_needed()
            time.sleep(0.3)

            # --- Open the three-dot menu ---
            menu_btn = card.query_selector(
                "button[aria-label='More actions'], "
                "yt-icon-button[aria-label='More actions'], "
                "ytd-menu-renderer yt-icon-button"
            )
            if not menu_btn:
                print("     menu button: NOT FOUND\n")
                continue

            try:
                menu_btn.hover()
                time.sleep(0.2)
                menu_btn.click()
                time.sleep(1.0)
            except Exception as e:
                print(f"     menu click failed: {e}\n")
                continue

            # --- Read all menu items ---
            items = page.query_selector_all(
                "ytd-menu-popup-renderer yt-formatted-string, "
                "tp-yt-paper-listbox yt-formatted-string, "
                "ytd-menu-popup-renderer tp-yt-paper-item"
            )
            item_texts = [it.inner_text().strip() for it in items if it.inner_text().strip()]
            print(f"     menu items: {item_texts}")

            not_interested = next(
                (it for it in items if "not interested" in it.inner_text().lower()),
                None
            )
            if not_interested:
                print("     ✅ 'Not interested' FOUND")
                try:
                    html = not_interested.evaluate("el => el.outerHTML")
                    print(f"     HTML snippet: {html[:300]}")
                    # Also print the parent for context
                    parent_html = not_interested.evaluate("el => el.parentElement.outerHTML")
                    print(f"     Parent HTML:  {parent_html[:300]}")
                except Exception:
                    pass
            else:
                print("     ❌ 'Not interested' not in menu")

            page.keyboard.press("Escape")
            time.sleep(0.4)
            print()

            probed += 1
            if probed >= 5:
                break

        print("\n=== Done ===")
        input("Press Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()

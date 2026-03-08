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

MAX_CARDS = 20  # how many cards to inspect before stopping


def extract_video_id(href: str) -> str | None:
    """Extract video ID from a /watch?v=... href."""
    if not href:
        return None
    if "v=" in href:
        return href.split("v=")[-1].split("&")[0]
    return None


def main():
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.new_page()
        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        print("\n=== Scanning home feed cards ===\n")

        cards = page.query_selector_all("ytd-rich-item-renderer")
        print(f"Found {len(cards)} cards on initial load.\n")

        found = 0
        for i, card in enumerate(cards[:MAX_CARDS]):
            # --- Extract title and video link ---
            title_el = card.query_selector("#video-title")
            title = title_el.inner_text().strip() if title_el else "(no title)"

            link_el = card.query_selector("a#video-title-link, a#thumbnail")
            href = link_el.get_attribute("href") if link_el else None
            video_id = extract_video_id(href) if href else None

            print(f"[{i+1}] {title!r}")
            print(f"     video_id: {video_id}  href: {href}")

            # --- Open the three-dot menu ---
            menu_btn = card.query_selector(
                "button[aria-label='More actions'], "
                "yt-icon-button#button[aria-label='More actions'], "
                "ytd-menu-renderer button"
            )
            if not menu_btn:
                print("     menu button: NOT FOUND — skipping\n")
                continue

            try:
                menu_btn.scroll_into_view_if_needed()
                menu_btn.hover()
                time.sleep(0.3)
                menu_btn.click()
                time.sleep(0.8)
            except Exception as e:
                print(f"     menu click failed: {e}\n")
                continue

            # --- Read all menu items ---
            items = page.query_selector_all(
                "ytd-menu-popup-renderer tp-yt-paper-item, "
                "ytd-menu-popup-renderer yt-formatted-string, "
                "tp-yt-paper-listbox yt-formatted-string"
            )
            item_texts = [it.inner_text().strip() for it in items if it.inner_text().strip()]
            print(f"     menu items: {item_texts}")

            not_interested = next(
                (it for it in items if "not interested" in it.inner_text().lower()),
                None
            )
            if not_interested:
                print("     ✅ 'Not interested' FOUND")
                # Print the element's outer HTML for selector analysis
                try:
                    html = not_interested.evaluate("el => el.outerHTML")
                    print(f"     HTML: {html[:200]}")
                except Exception:
                    pass
            else:
                print("     ❌ 'Not interested' not in menu")

            # Close the menu
            page.keyboard.press("Escape")
            time.sleep(0.3)
            print()

            found += 1
            if found >= 5:  # only probe 5 cards deeply to keep it fast
                break

        print("\n=== Done ===")
        input("Press Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()

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

PROFILE_DIR = Path.home() / ".yt-dont-recommend" / "browser-profile"

if not PROFILE_DIR.exists():
    print("No browser profile found. Run: yt-dont-recommend --login")
    sys.exit(1)

from playwright.sync_api import sync_playwright

PAGE_LOAD_WAIT = 5
RENDER_WAIT   = 8


def extract_video_id(href: str) -> str | None:
    if not href:
        return None
    if "v=" in href:
        return href.split("v=")[-1].split("&")[0]
    return None


def wait_for_content(page) -> None:
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
    # Try via JavaScript to capture whatever text is in the card's title area
    title = card.evaluate("""el => {
        const candidates = [
            el.querySelector('#video-title'),
            el.querySelector('h3 a'),
            el.querySelector('yt-formatted-string#video-title'),
            el.querySelector('#title-wrapper yt-formatted-string'),
            el.querySelector('h3 yt-formatted-string'),
            el.querySelector('a#video-title-link'),
        ];
        for (const c of candidates) {
            if (c && c.textContent.trim()) return c.textContent.trim();
        }
        return null;
    }""")
    return title or "(no title)"


def get_href(card) -> str | None:
    for sel in ("a#video-title-link", "a#thumbnail", "h3 a", "ytd-thumbnail a"):
        el = card.query_selector(sel)
        if el:
            href = el.get_attribute("href")
            if href:
                return href
    return None


def dump_card_html(card) -> None:
    """Print a trimmed snapshot of the card's inner HTML for selector debugging."""
    html = card.evaluate("el => el.innerHTML")
    # Just show the first 600 chars — enough to see structure
    print(f"     CARD HTML (first 600 chars):\n     {html[:600]}\n")


def dump_page_popups(page) -> None:
    """After clicking a menu, dump any visible popup/overlay HTML."""
    popups = page.query_selector_all(
        "ytd-menu-popup-renderer, "
        "tp-yt-iron-dropdown, "
        "tp-yt-paper-listbox"
    )
    print(f"     Popup elements found: {len(popups)}")
    for popup in popups:
        try:
            visible = popup.is_visible()
            html = popup.evaluate("el => el.innerHTML")
            print(f"     Popup visible={visible}, HTML (first 400):\n     {html[:400]}\n")
        except Exception as e:
            print(f"     Popup read error: {e}")


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
            href     = get_href(card)
            video_id = extract_video_id(href) if href else None
            title    = get_title(card)

            if not video_id:
                continue  # skip ads / shelves / unrendered slots

            print(f"[{i+1}] title={title!r}  video_id={video_id}")

            # On the first card, dump the raw HTML so we can see structure
            if probed == 0:
                dump_card_html(card)

            card.scroll_into_view_if_needed()
            time.sleep(0.5)

            # --- Find and click the three-dot menu ---
            menu_btn = card.query_selector(
                "button[aria-label='More actions'], "
                "yt-icon-button[aria-label='More actions'], "
                "ytd-menu-renderer yt-icon-button"
            )
            if not menu_btn:
                print("     ❌ menu button NOT FOUND\n")
                # Dump HTML to understand structure
                dump_card_html(card)
                continue

            print(f"     menu button found: {menu_btn.evaluate('el => el.outerHTML')[:150]}")

            try:
                menu_btn.hover()
                time.sleep(0.3)
                menu_btn.click()
                time.sleep(1.2)
            except Exception as e:
                print(f"     menu click failed: {e}\n")
                continue

            # --- Dump whatever popups appeared ---
            dump_page_popups(page)

            # --- Try to find "Not interested" anywhere on the page ---
            all_items = page.query_selector_all("yt-formatted-string, tp-yt-paper-item")
            matching = [
                el for el in all_items
                if "not interested" in el.inner_text().lower()
            ]
            if matching:
                print(f"     ✅ 'Not interested' found ({len(matching)} element(s))")
                for m in matching:
                    print(f"        HTML: {m.evaluate('el => el.outerHTML')[:300]}")
            else:
                # Show all visible text on the page near a popup
                visible_text = page.evaluate("""() => {
                    const items = document.querySelectorAll(
                        'ytd-menu-popup-renderer yt-formatted-string, ' +
                        'tp-yt-paper-listbox yt-formatted-string, ' +
                        'tp-yt-iron-dropdown yt-formatted-string'
                    );
                    return Array.from(items).map(el => el.textContent.trim()).filter(Boolean);
                }""")
                print(f"     ❌ 'Not interested' not found")
                print(f"     All popup text via JS: {visible_text}")

            page.keyboard.press("Escape")
            time.sleep(0.4)
            print()

            probed += 1
            if probed >= 3:
                break

        print("\n=== Done ===")
        input("Press Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()

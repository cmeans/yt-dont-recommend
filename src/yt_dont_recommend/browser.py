"""
Browser automation: process_channels, fetch_subscriptions, do_login,
and related helper functions.

Functions here use late imports from the yt_dont_recommend package for
state/notification functions (load_state, save_state, write_attention,
check_removals) to avoid circular import with __init__.py.

check_selectors and _screenshot have moved to diagnostics.py.
_perform_browser_unblocks has moved to unblock.py.
"""

import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import (
    PROFILE_DIR,
    PAGE_LOAD_WAIT,
    MIN_DELAY,
    MAX_DELAY,
    LONG_PAUSE_EVERY,
    LONG_PAUSE_SECONDS,
    MIN_CARDS_FOR_SELECTOR_CHECK,
    SELECTOR_WARN_AFTER,
    ATTENTION_FILE,
    VIDEO_SELECTORS,
    MENU_BTN_SELECTORS,
    MENU_ITEM_SELECTOR,
    TARGET_PHRASES,
    _n,
    pick_viewport,
    DEFAULT_SESSION_CAP,
    load_timing_config,
)
from .unblock import _perform_browser_unblocks, _pending_attempted_this_run

log = logging.getLogger(__name__)


def _pkg():
    """Late import of the yt_dont_recommend package to avoid circular imports."""
    import yt_dont_recommend as _p
    return _p


def do_login() -> None:
    """Open a browser window for the user to log into YouTube."""
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    print("\nLogin setup")
    print("-----------")
    print("A browser window will open and navigate to Google's sign-in page.")
    print()
    print("Steps:")
    print("  1. Sign into your Google account in the browser window.")
    print("  2. Wait until you can see your YouTube home page.")
    print("  3. Close the browser window.")
    print()
    print("Your session will be saved automatically and reused on every")
    print("subsequent run — you only need to do this once.")
    print()
    input("Press Enter to open the browser...")
    print()

    with sync_playwright() as p:
        context = _launch_context(
            p, PROFILE_DIR,
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
            viewport=pick_viewport(),
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://accounts.google.com/ServiceLogin?service=youtube")

        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        context.close()

    log.info("Login session saved. You can now run without --login.")


def _find_system_chrome() -> str | None:
    """Search for a system Chrome or Chromium executable across common install locations.

    Tries PATH-based names first, then fixed paths for non-standard installs
    (RPM on Fedora/RHEL, deb on Debian/Ubuntu, Flatpak). Returns the first
    found executable path, or None.
    """
    import shutil
    import subprocess

    # Ordered by preference: Chrome (real UA) before Chromium (open-source build)
    candidates_in_path = [
        "google-chrome-stable",
        "google-chrome",
        "chromium-browser",
        "chromium",
    ]
    home = Path.home()
    fixed_paths = [
        # RPM / deb standard install locations
        "/opt/google/chrome/google-chrome",
        "/opt/google/chrome-beta/google-chrome",
        "/opt/google/chrome-unstable/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        # Snap
        "/snap/bin/chromium",
        "/snap/chromium/current/usr/lib/chromium-browser/chromium-browser",
        # Flatpak stable — system install
        "/var/lib/flatpak/app/com.google.Chrome/current/active/files/extra/chrome",
        # Flatpak stable — user install
        str(home / ".local/share/flatpak/app/com.google.Chrome/current/active/files/extra/chrome"),
        # Flatpak beta / canary — system
        "/var/lib/flatpak/app/com.google.ChromeBeta/current/active/files/extra/chrome",
        "/var/lib/flatpak/app/com.google.ChromeDev/current/active/files/extra/chrome",
        # Flatpak Chromium (open-source) — system
        "/var/lib/flatpak/app/org.chromium.Chromium/current/active/files/chromium",
        # Flatpak Chromium — user
        str(home / ".local/share/flatpak/app/org.chromium.Chromium/current/active/files/chromium"),
    ]

    for name in candidates_in_path:
        found = shutil.which(name)
        if found:
            return found
    for path in fixed_paths:
        if Path(path).exists():
            return path

    # Dynamic Flatpak detection — handles non-default install branches/arches
    for flatpak_id in ("com.google.Chrome", "com.google.ChromeBeta", "org.chromium.Chromium"):
        try:
            result = subprocess.run(
                ["flatpak", "info", "--show-location", flatpak_id],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                loc = result.stdout.strip()
                for rel in ("files/extra/chrome", "files/chromium"):
                    candidate = str(Path(loc) / rel)
                    if Path(candidate).exists():
                        return candidate
        except Exception:
            pass

    return None


def _get_system_chrome_version(exe: str) -> str | None:
    """Return the version string (e.g. '145.0.7632.159') from a Chrome/Chromium binary.

    For Flatpak binaries, the raw binary cannot run outside the Flatpak sandbox,
    so we invoke via 'flatpak run' instead.
    """
    import re
    import subprocess

    cmds: list[list[str]] = []
    if "/flatpak/app/com.google.Chrome" in exe:
        cmds.append(["flatpak", "run", "com.google.Chrome", "--version"])
    elif "/flatpak/app/com.google.ChromeBeta" in exe:
        cmds.append(["flatpak", "run", "com.google.ChromeBeta", "--version"])
    elif "/flatpak/app/org.chromium.Chromium" in exe:
        cmds.append(["flatpak", "run", "org.chromium.Chromium", "--version"])
    cmds.append([exe, "--version"])  # direct invocation as fallback

    for cmd in cmds:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                match = re.search(r"(\d+\.\d+\.\d+\.\d+)", result.stdout)
                if match:
                    return match.group(1)
        except Exception:
            pass
    return None


def _build_chrome_ua(version: str) -> str:
    """Build a standard Linux Chrome User-Agent string for the given version."""
    return (
        f"Mozilla/5.0 (X11; Linux x86_64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{version} Safari/537.36"
    )


def _launch_context(p: Any, profile_dir: Path, **kwargs: Any) -> Any:
    """Launch a persistent browser context using Playwright's bundled Chromium.

    If a system Chrome or Chromium installation is found and use_system_chrome is
    enabled (default), its real version is read and injected as the User-Agent on
    the bundled Chromium context. This gives an authentic UA without requiring
    --no-sandbox (which Flatpak and some other installs need when run directly).
    """
    from .config import load_browser_config
    use_system_chrome = load_browser_config().get("use_system_chrome", True)

    if use_system_chrome:
        exe = _find_system_chrome()
        if exe:
            log.debug("Browser: found system Chrome/Chromium at %s", exe)
            version = _get_system_chrome_version(exe)
            if version:
                ua = _build_chrome_ua(version)
                log.info("Browser: bundled Chromium with Chrome/%s UA (sourced from %s)", version, exe)
                kwargs["user_agent"] = ua
            else:
                log.debug("Browser: could not read version from %s — using bundled Chromium UA", exe)
        else:
            log.debug("Browser: no system Chrome/Chromium found — using bundled Chromium UA")
    else:
        log.info("Browser: use_system_chrome disabled — using bundled Chromium UA")

    ctx = p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
    ua_actual = ctx.pages[0].evaluate("navigator.userAgent") if ctx.pages else "unknown"
    log.info("Browser: UA: %s", ua_actual)
    return ctx


def _extract_feed_videos_from_json(page: Any) -> dict:
    """Extract video metadata from the ytInitialData JSON blob embedded in the page.

    Returns {video_id: {"title": str, "channel_handle": str | None}} for all
    videoRenderer entries found in the home feed richGridRenderer on initial load.

    Only covers the first page of cards (loaded at page.goto time). Scrolled
    continuation content is not captured here — callers must fall back to DOM
    extraction for videos not in the returned dict.

    Returns an empty dict on any failure (missing ytInitialData, JS error, etc.)
    so callers can safely treat it as a best-effort supplement.
    """
    try:
        data = page.evaluate("""
            () => {
                if (!window.ytInitialData) return null;
                const d = window.ytInitialData;
                const tabs = d?.contents?.twoColumnBrowseResultsRenderer?.tabs ?? [];
                let contents = null;
                for (const tab of tabs) {
                    if (tab?.tabRenderer?.selected) {
                        contents = tab?.tabRenderer?.content?.richGridRenderer?.contents ?? null;
                        break;
                    }
                }
                if (!contents) return null;

                const videos = [];
                for (const item of contents) {
                    const vr = item?.richItemRenderer?.content?.videoRenderer;
                    if (!vr) continue;
                    const videoId = vr.videoId;
                    const title = vr?.title?.runs?.[0]?.text ?? null;
                    // YouTube A/B tests shortBylineText vs ownerText — try both
                    const ep = vr?.shortBylineText?.runs?.[0]?.navigationEndpoint
                             ?? vr?.ownerText?.runs?.[0]?.navigationEndpoint
                             ?? null;
                    const canonicalUrl = ep?.browseEndpoint?.canonicalBaseUrl ?? null;
                    // canonicalBaseUrl is "/@handle" — strip the leading slash
                    const channelHandle = canonicalUrl ? canonicalUrl.replace(/^\\//, '') : null;
                    if (videoId && title) {
                        videos.push({video_id: videoId, title, channel_handle: channelHandle});
                    }
                }
                return videos;
            }
        """)
        if not data:
            return {}
        result = {item["video_id"]: item for item in data}
        log.debug("ytInitialData: %d video entries on initial page load", len(result))
        return result
    except Exception as exc:
        log.debug("ytInitialData extraction failed: %s", exc)
        return {}


def _find_menu_btn(card: Any) -> Any:
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


_NOT_INTERESTED_PHRASE = "not interested"
_NOT_INTERESTED_ITEM_SELECTOR = (
    "ytd-menu-service-item-renderer, tp-yt-paper-item, "
    "ytd-menu-navigation-item-renderer, [role='menuitem'], "
    "yt-list-item-view-model"
)


def _click_not_interested(page: Any, card: Any) -> bool:
    """
    Click 'Not interested' on a home feed video card.

    This is a video-level signal that removes the specific video from the feed.
    It does NOT affect channel-level recommendations — use _click_dont_recommend
    for that purpose.

    Returns True if the option was found and clicked, False otherwise.
    """
    card.scroll_into_view_if_needed()
    card.hover()
    time.sleep(random.uniform(0.3, 0.7))

    menu_btn = _find_menu_btn(card)
    if not menu_btn:
        log.debug("Could not find menu button on feed card (Not interested)")
        return False

    menu_btn.click()
    time.sleep(random.uniform(0.7, 1.5))

    target_item = None
    for item in page.query_selector_all(_NOT_INTERESTED_ITEM_SELECTOR):
        try:
            text = (item.inner_text() or "").strip().lower()
        except Exception:
            text = (item.evaluate("el => el.textContent") or "").strip().lower()
        if _NOT_INTERESTED_PHRASE in text:
            target_item = item
            break

    if not target_item:
        page.keyboard.press("Escape")
        return False

    # New YouTube UI wraps the action in a button inside the list item
    btn = target_item.query_selector("button.yt-list-item-view-model__button-or-anchor")
    if btn:
        btn.click()
    else:
        target_item.click()
    time.sleep(random.uniform(0.3, 0.7))
    return True


def _click_dont_recommend(page: Any, card: Any) -> bool:
    """
    Click 'Don't recommend channel' on a home feed card.

    'Don't recommend channel' only appears in recommendation feed contexts
    (home feed, subscription feed). It does NOT appear in search results,
    on channel pages, or on video watch pages — confirmed 2026-03-05.

    Returns True if the option was found and clicked, False otherwise.
    """
    card.scroll_into_view_if_needed()
    card.hover()
    time.sleep(random.uniform(0.3, 0.7))

    menu_btn = _find_menu_btn(card)
    if not menu_btn:
        log.debug("Could not find menu button on feed card")
        return False

    menu_btn.click()
    time.sleep(random.uniform(0.7, 1.5))

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
    time.sleep(random.uniform(0.3, 0.7))
    return True


def fetch_subscriptions(page: Any) -> set[str]:
    """
    Scrape the YouTube subscriptions management page and return a set of
    lowercased canonical channel IDs (@handle or UCxxx).

    Returns an empty set if the page cannot be parsed, with a warning logged.
    """
    pkg = _pkg()

    log.info("Fetching subscriptions list...")
    page.goto("https://www.youtube.com/feed/channels", wait_until="domcontentloaded", timeout=60000)
    time.sleep(random.uniform(PAGE_LOAD_WAIT, PAGE_LOAD_WAIT + 1.5))

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

        page.evaluate(f"window.scrollBy(0, window.innerHeight * {random.uniform(2.5, 4.0):.2f})")
        time.sleep(random.uniform(1.0, 2.5))

    if subscriptions:
        log.info(f"Found {len(subscriptions)} subscribed channels")
    else:
        msg = (
            "No subscriptions found — the subscriptions page may have changed its layout. "
            "Subscription protection is disabled for this run. "
            "Check manually and run --check-selectors."
        )
        log.warning(msg)
        pkg.write_attention(msg)
    return subscriptions


def _resolve_ucxxx_to_handles(page: Any, channels: list[str], state: dict) -> list[str]:
    """For any UCxxx entries in channels, resolve to @handle via YouTube's redirect.

    Modern YouTube feed cards expose only @handle links — UCxxx entries in a
    custom blocklist will never match without this resolution step.

    Results are cached in state['ucxxx_to_handle']:
      - Resolved:   UCxxx → "@handle"  (string)
      - No handle:  UCxxx → None       (channel predates YouTube's handle system)

    Channels with no resolvable handle are dropped from the return list —
    they can never match feed cards so there is no point tracking them.
    """
    pkg = _pkg()

    cache = state.setdefault("ucxxx_to_handle", {})
    ucxxx_entries = [
        ch for ch in channels
        if re.match(r'^UC[A-Za-z0-9_-]{22}$', ch)
    ]

    if not ucxxx_entries:
        return channels

    uncached = [ch for ch in ucxxx_entries if ch not in cache]
    cached_count = len(ucxxx_entries) - len(uncached)

    if uncached:
        log.info(
            f"Resolving {_n(len(uncached), 'UCxxx channel ID')} to @handles"
            + (f" ({cached_count} already cached)" if cached_count else "") + "..."
        )
        for ucxxx in uncached:
            try:
                page.goto(
                    f"https://www.youtube.com/channel/{ucxxx}",
                    wait_until="domcontentloaded", timeout=30000,
                )
                time.sleep(random.uniform(0.7, 1.5))
                path = page.url.replace("https://www.youtube.com", "").split("?")[0].rstrip("/")
                if path.startswith("/@"):
                    handle = path[1:]  # strip leading /
                    cache[ucxxx] = handle
                    log.debug(f"Resolved {ucxxx} → {handle}")
                else:
                    cache[ucxxx] = None  # no @handle; cannot match feed cards
                    log.debug(f"No @handle for {ucxxx} — will be skipped")
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                log.warning(f"Could not resolve {ucxxx}: {e}")
                cache[ucxxx] = None
        pkg.save_state(state)

    # Summarise unresolvable channels once (at debug level — don't spam on every run)
    unresolvable = [ch for ch in ucxxx_entries if cache.get(ch) is None]
    if unresolvable:
        log.debug(
            f"{_n(len(unresolvable), 'UCxxx ID')} have no @handle on YouTube and "
            f"will be skipped (cannot match feed cards): {unresolvable}"
        )

    # Return list with UCxxx replaced by resolved handles; drop unresolvable entries
    result = []
    for ch in channels:
        if re.match(r'^UC[A-Za-z0-9_-]{22}$', ch):
            resolved = cache.get(ch)
            if resolved:
                result.append(resolved)
            # else: no handle — silently drop
        else:
            result.append(ch)
    return result


def open_browser(headless: bool = False) -> tuple | None:
    """Open a persistent Chromium browser and verify the YouTube login session.

    Returns (playwright_cm, context, page) on success, or None if not logged in
    (write_attention is called automatically). Pass the return value as the
    _browser= argument to process_channels() to share a single session across
    multiple source runs. Call close_browser() when done.
    """
    from playwright.sync_api import sync_playwright
    pkg = _pkg()

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw_cm = sync_playwright()
    p = pw_cm.__enter__()
    context = _launch_context(
        p, PROFILE_DIR,
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--disable-infobars"],
        ignore_default_args=["--enable-automation"],
        viewport=pick_viewport(),
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    for extra in context.pages[1:]:
        extra.close()
    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
    time.sleep(random.uniform(PAGE_LOAD_WAIT, PAGE_LOAD_WAIT + 1.5))

    # button#avatar-btn exists on both logged-in and logged-out pages.
    # The notification bell only appears when a Google account is active.
    avatar = page.query_selector("#notification-button, ytd-notification-topbar-button-renderer")
    if not avatar:
        pkg.write_attention("Not logged in — session may have expired. Run: yt-dont-recommend --login")
        context.close()
        pw_cm.__exit__(None, None, None)
        return None

    return (pw_cm, context, page)


def close_browser(handle: tuple) -> None:
    """Close a browser handle returned by open_browser()."""
    pw_cm, context, _page = handle
    log.info("Closing browser (saving session to disk)...")
    context.close()
    pw_cm.__exit__(None, None, None)


def process_channels(channel_sources: dict[str, str],
                     to_unblock: list[str] | None = None,
                     state: dict | None = None,
                     dry_run: bool = False,
                     limit: int | None = None,
                     headless: bool = False,
                     clickbait_cfg: dict | None = None,
                     exclude_set: set[str] | None = None,
                     _browser: tuple | None = None) -> None:
    """
    Scan the YouTube home feed once and click 'Don't recommend channel' for
    every channel in channel_sources.

    channel_sources: {canonical_handle: source_name} — channels not yet in
        state["blocked_by"], collected from all sources by the caller.
    to_unblock: channels whose myactivity feedback entry should be deleted
        first (from check_removals() results and pending_unblock retries).
    state: already-loaded state dict (mutated in place). Loaded fresh if None.

    The feed is scrolled until exhausted or limit is reached. All sources are
    processed in a single pass — no redundant scrolling.
    """
    pkg = _pkg()

    MAX_NO_PROGRESS_SCROLLS = 20

    if state is None:
        state = pkg.load_state()
    if to_unblock is None:
        to_unblock = []
    timing = load_timing_config()
    _min_delay = float(timing.get("min_delay", MIN_DELAY))
    _max_delay = float(timing.get("max_delay", MAX_DELAY))
    _long_pause_every = int(timing.get("long_pause_every", LONG_PAUSE_EVERY))
    _long_pause_seconds = float(timing.get("long_pause_seconds", LONG_PAUSE_SECONDS))
    _page_load_wait = float(timing.get("page_load_wait", PAGE_LOAD_WAIT))
    if limit is None:
        limit = int(timing.get("session_cap", DEFAULT_SESSION_CAP))

    processed_set = set(state["blocked_by"].keys())

    # Filter to_unblock against channels already attempted in this process run.
    to_unblock = [ch for ch in to_unblock if ch not in _pending_attempted_this_run]

    if not channel_sources and not to_unblock and clickbait_cfg is None:
        log.info("Nothing to do.")
        return

    n_sources = len(set(channel_sources.values())) if channel_sources else 0


    _own_browser = _browser is None
    _pw_cm = None
    if _own_browser:
        handle = open_browser(headless=headless)
        if handle is None:
            return  # write_attention already called by open_browser
        _pw_cm, context, page = handle
    else:
        _, context, page = _browser

    try:
        # Reverse any blocks on YouTube before scanning for new ones
        if to_unblock and not dry_run:
            _pending_attempted_this_run.update(to_unblock)
            successfully_unblocked = _perform_browser_unblocks(page, to_unblock, state)
            pending_unblock = state.setdefault("pending_unblock", {})
            for ch in successfully_unblocked:
                pending_unblock.pop(ch, None)
            pkg.save_state(state)
        elif to_unblock and dry_run:
            log.info(f"DRY RUN — would reverse YouTube block for: {', '.join(to_unblock)}")

        if not channel_sources and clickbait_cfg is None:
            return

        subscriptions = fetch_subscriptions(page) if channel_sources else set()

        # Resolve any UCxxx IDs to @handles and preserve source attribution.
        # Modern feed cards only expose @handle links, so UCxxx entries won't
        # match without this step.
        resolved_list = _resolve_ucxxx_to_handles(page, list(channel_sources.keys()), state)
        cache = state.get("ucxxx_to_handle", {})
        reverse_cache = {v: k for k, v in cache.items() if v}  # @handle → UCxxx
        resolved_sources: dict[str, str] = {}
        for ch in resolved_list:
            if ch in channel_sources:
                resolved_sources[ch] = channel_sources[ch]
            else:
                orig = reverse_cache.get(ch)
                if orig and orig in channel_sources:
                    resolved_sources[ch] = channel_sources[orig]
        channel_lookup = {ch.lower(): ch for ch in resolved_sources}

        # Navigate to home feed
        page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
        time.sleep(random.uniform(_page_load_wait, _page_load_wait + 1.5))

        # Set up clickbait classifier if requested
        _classify_video = None
        if clickbait_cfg is not None:
            try:
                from .clickbait import classify_video as _classify_video  # type: ignore[assignment]
            except ImportError:
                from . import _clickbait_install_cmd
                log.warning(
                    "--clickbait: ollama package not installed. Install with:\n"
                    f"  {_clickbait_install_cmd()}"
                )
                clickbait_cfg = None

        _clickbait_evaluated: set[str] = set()  # channels evaluated this run (avoid re-classifying)
        processed_set_lower = {c.lower() for c in processed_set}

        _prefix = "DRY RUN — " if dry_run else ""
        if channel_lookup and clickbait_cfg is not None:
            _scan_desc = (f"{_n(len(channel_lookup), 'channel')} across {_n(n_sources, 'source')}"
                          f" + clickbait detection")
        elif channel_lookup:
            _scan_desc = f"{_n(len(channel_lookup), 'channel')} across {_n(n_sources, 'source')}"
        else:
            _scan_desc = "clickbait detection"
        log.info(f"{_prefix}Scanning home feed for {_scan_desc}...")

        _run_blocklist = bool(channel_lookup)
        _run_clickbait = clickbait_cfg is not None
        # Extract video metadata from ytInitialData (initial page load only).
        # Used as a reliable title source for clickbait classification; cards
        # loaded by subsequent scrolls fall back to DOM extraction.
        _json_videos: dict = _extract_feed_videos_from_json(page) if _run_clickbait else {}
        blocked_count = 0
        clickbait_count = 0
        no_progress_scrolls = 0
        zero_parse_passes = 0
        selector_confirmed = False
        seen_paths: set[str] = set()

        while True:
            if limit and (blocked_count + clickbait_count) >= limit:
                log.info(f"Reached limit of {limit} actions.")
                break
            if no_progress_scrolls >= MAX_NO_PROGRESS_SCROLLS:
                # Before reporting feed exhaustion, re-check login: an expired
                # session shows zero cards and is indistinguishable from a quiet feed.
                still_logged_in = page.query_selector(
                    "#notification-button, ytd-notification-topbar-button-renderer"
                )
                if not still_logged_in:
                    pkg.write_attention(
                        "Session expired mid-run — run yt-dont-recommend --login to restore."
                    )
                else:
                    log.info(
                        f"Feed exhausted after {no_progress_scrolls} consecutive scrolls with no new matches."
                    )
                break

            cards = page.query_selector_all("ytd-rich-item-renderer")
            found_match_this_pass = False
            evaluated_clickbait_this_pass = 0
            pass_parseable = 0

            for card in cards:
                if limit and (blocked_count + clickbait_count) >= limit:
                    break

                channel_link = card.query_selector("a[href^='/@'], a[href^='/channel/UC']")
                if not channel_link:
                    continue
                pass_parseable += 1

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
                log.debug(f"Feed card channel: {path}")
                canonical = channel_lookup.get(path.lower())
                if canonical and canonical in processed_set:
                    continue

                if not canonical:
                    # Not on blocklist — check for clickbait if enabled
                    if clickbait_cfg is None or path.lower() in _clickbait_evaluated:
                        continue
                    if exclude_set and path.lower() in exclude_set:
                        log.debug(f"clickbait: {path} — in exclude list, skipping")
                        continue
                    # Get video title text. Try stable text-element selectors first,
                    # then fall back to the link element directly.
                    # Note: a[href*='/watch?v='] matches a#thumbnail first whose
                    # inner_text() is the duration overlay — avoid that path.
                    _title_el = (
                        card.query_selector("a#video-title-link")
                        or card.query_selector("a#video-title")
                        or card.query_selector("h3 a[href*='watch?v=']")
                    )
                    if not _title_el:
                        log.debug(f"clickbait: {path} — no title link found (Shorts or shelf card?), skipping")
                        continue
                    vid_href = _title_el.get_attribute("href") or ""
                    m = re.search(r'[?&]v=([A-Za-z0-9_-]{11})', vid_href)
                    if not m:
                        log.debug(f"clickbait: {path} — no video ID in href {vid_href!r}, skipping")
                        continue
                    video_id = m.group(1)
                    # Prefer JSON title from ytInitialData (clean, no duration suffix).
                    # Falls back to DOM extraction for scrolled cards not in the JSON.
                    json_meta = _json_videos.get(video_id)
                    if json_meta and json_meta.get("title"):
                        video_title: str | None = json_meta["title"]
                        log.debug(f"clickbait: {path}/{video_id} — title from ytInitialData JSON")
                    else:
                        if _json_videos:
                            # JSON was populated but this video_id wasn't in it (scrolled card)
                            log.debug(f"clickbait: {path}/{video_id} — not in ytInitialData, falling back to DOM")
                        video_title = None
                        # DOM fallback: title attribute (clean), then text span, then
                        # aria-label with duration suffix stripped.
                        video_title = _title_el.get_attribute("title") or None
                        if not video_title:
                            _text_el = card.query_selector("yt-formatted-string#video-title, #video-title")
                            if _text_el:
                                video_title = _text_el.inner_text().strip() or None
                        if not video_title:
                            aria = _title_el.get_attribute("aria-label") or ""
                            video_title = re.sub(
                                r'\s+(?:\d+\s+(?:hours?|minutes?|seconds?),?\s*)+$',
                                "", aria
                            ).strip() or None
                    if not video_title:
                        log.debug(f"clickbait: {path} — could not extract title, skipping")
                        continue

                    _clickbait_evaluated.add(path.lower())
                    evaluated_clickbait_this_pass += 1
                    result = _classify_video(video_id, video_title, clickbait_cfg)
                    conf = result.get("confidence", 0.0)
                    if not result.get("flagged"):
                        _is_cb = result.get("is_clickbait", False)
                        _rsn   = result.get("title_result", {}).get("reasoning", "")
                        log.debug(
                            f"clickbait: {path} — {video_title!r} "
                            f"is_clickbait={_is_cb} score={conf:.2f} not flagged"
                            + (f" — {_rsn}" if _rsn else "")
                        )
                        continue
                    _stages_str = "+".join(result.get("stages", ["title"]))
                    log.info(
                        f"CLICKBAIT: {path} — {video_title!r} "
                        f"(confidence {conf:.2f}, via {_stages_str}) — marking Not interested..."
                    )
                    if dry_run:
                        log.info(f"WOULD MARK NOT INTERESTED: {path} — {video_title!r}")
                        clickbait_count += 1
                        found_match_this_pass = True
                        continue
                    try:
                        success = _click_not_interested(page, card)
                    except Exception as e:
                        log.error(f"FAIL clickbait {path}: {e}")
                        continue
                    if success:
                        log.info(f"[clickbait] NOT_INTERESTED: {path} — {video_title!r}")
                        clickbait_count += 1
                        found_match_this_pass = True
                        time.sleep(random.uniform(_min_delay, _max_delay))
                        break  # rescan after DOM change
                    else:
                        log.warning(f"SKIP clickbait {path} (Not interested not found in menu)")
                    continue
                else:
                    source = resolved_sources.get(canonical, "unknown")

                # Check subscription protection before blocking
                if canonical.lower() in subscriptions:
                    whb = state["would_have_blocked"]
                    if canonical not in whb or not whb[canonical].get("notified"):
                        log.warning(
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
                        pkg.save_state(state)
                    continue

                if dry_run:
                    log.info(f"WOULD BLOCK: {canonical} (source: {source})")
                    blocked_count += 1
                    found_match_this_pass = True
                    continue

                # Capture display name from card for later unblocking.
                display_name = (channel_link.inner_text() or "").strip() or None

                log.info(f"Found in feed: {canonical} — blocking...")
                try:
                    success = _click_dont_recommend(page, card)
                except Exception as e:
                    log.error(f"FAIL {canonical}: {e}")
                    state["stats"]["total_failed"] += 1
                    pkg.save_state(state)
                    continue

                if success:
                    processed_set.add(canonical)
                    processed_set_lower.add(canonical.lower())
                    state["stats"]["total_blocked"] += 1
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

                    log.info(f"[{blocked_count}] OK {canonical}")
                    pkg.save_state(state)

                    if blocked_count % _long_pause_every == 0:
                        log.info(f"Taking a {_long_pause_seconds:.0f}s break...")
                        time.sleep(random.uniform(_long_pause_seconds * 0.8, _long_pause_seconds * 1.2))
                    else:
                        time.sleep(random.uniform(_min_delay, _max_delay))

                    break  # rescan after DOM changes
                else:
                    state["stats"]["total_skipped"] += 1
                    log.warning(f"SKIP {canonical} (appeared in feed but couldn't block)")
                    pkg.save_state(state)

            # Selector health check
            if len(cards) >= MIN_CARDS_FOR_SELECTOR_CHECK and pass_parseable == 0:
                zero_parse_passes += 1
                if zero_parse_passes >= SELECTOR_WARN_AFTER:
                    pkg.write_attention(
                        f"Possible selector failure: {zero_parse_passes} consecutive feed passes "
                        f"each had {len(cards)}+ cards but zero parseable channel links. "
                        f"YouTube may have changed its DOM. Run --check-selectors to diagnose."
                    )
                    break
            else:
                zero_parse_passes = 0
                if not selector_confirmed and pass_parseable > 0:
                    selector_confirmed = True
                    if ATTENTION_FILE.exists():
                        ATTENTION_FILE.unlink()
                        log.info("Selector working — previous attention alert cleared.")

            # Only log pass summary when there was activity or a diagnostic signal —
            # suppresses the wall of identical "0 evaluated" lines during feed exhaustion.
            if found_match_this_pass or evaluated_clickbait_this_pass or not cards or pass_parseable == 0:
                log.debug(
                    f"Pass: {len(cards)} cards, {pass_parseable} with channel links"
                    + (f", {evaluated_clickbait_this_pass} evaluated for clickbait" if _run_clickbait else "")
                )

            if found_match_this_pass or evaluated_clickbait_this_pass:
                no_progress_scrolls = 0
            else:
                page.evaluate(f"window.scrollBy(0, window.innerHeight * {random.uniform(1.5, 3.0):.2f})")
                time.sleep(random.uniform(1.5, 3.0))
                no_progress_scrolls += 1

    finally:
        if _own_browser:
            context.close()
            if _pw_cm is not None:
                _pw_cm.__exit__(None, None, None)

    if dry_run:
        parts = []
        if _run_blocklist:
            parts.append(f"{_n(blocked_count, 'channel')} blocked")
        if _run_clickbait:
            parts.append(f"{_n(clickbait_count, 'video')} marked Not interested")
        log.info(f"DRY RUN complete. Would have: {', '.join(parts)}.")
    else:
        parts = []
        if _run_blocklist:
            parts.append(f"{_n(blocked_count, 'channel')} blocked")
        if _run_clickbait:
            parts.append(f"{_n(clickbait_count, 'video')} marked Not interested")
        log.info(f"Done. {', '.join(parts)} this run. Stats: {state['stats']}")


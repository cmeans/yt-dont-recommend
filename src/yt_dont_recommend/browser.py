"""
Browser automation: process_channels, fetch_subscriptions, check_selectors,
do_login, and related helper functions.

Functions here use late imports from the yt_dont_recommend package for
state/notification functions (load_state, save_state, write_attention,
check_removals) to avoid circular import with __init__.py.
"""

import logging
import random
import re
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import quote

# Channels whose unblock was attempted in the current Python process run.
# Prevents retrying the same channels for every source in one invocation,
# which would otherwise trigger Google's password-verification prompt twice.
_pending_attempted_this_run: set[str] = set()

# Give up on a channel's unblock after this many consecutive display-name
# failures (channel page unreachable / handle doesn't exist).
_MAX_DISPLAY_NAME_RETRIES = 3

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
)


def _pkg():
    """Late import of the yt_dont_recommend package to avoid circular imports."""
    import yt_dont_recommend as _p
    return _p


def do_login():
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
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://accounts.google.com/ServiceLogin?service=youtube")

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


def fetch_subscriptions(page) -> set[str]:
    """
    Scrape the YouTube subscriptions management page and return a set of
    lowercased canonical channel IDs (@handle or UCxxx).

    Returns an empty set if the page cannot be parsed, with a warning logged.
    """
    pkg = _pkg()

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
        msg = (
            "No subscriptions found — the subscriptions page may have changed its layout. "
            "Subscription protection is disabled for this run. "
            "Check manually and run --check-selectors."
        )
        logging.warning(msg)
        pkg.write_attention(msg)
    return subscriptions


def _resolve_ucxxx_to_handles(page, channels: list[str], state: dict) -> list[str]:
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
        logging.info(
            f"Resolving {len(uncached)} UCxxx channel ID(s) to @handles"
            + (f" ({cached_count} already cached)" if cached_count else "") + "..."
        )
        for ucxxx in uncached:
            try:
                page.goto(
                    f"https://www.youtube.com/channel/{ucxxx}",
                    wait_until="domcontentloaded", timeout=30000,
                )
                time.sleep(1.0)
                path = page.url.replace("https://www.youtube.com", "").split("?")[0].rstrip("/")
                if path.startswith("/@"):
                    handle = path[1:]  # strip leading /
                    cache[ucxxx] = handle
                    logging.debug(f"Resolved {ucxxx} → {handle}")
                else:
                    cache[ucxxx] = None  # no @handle; cannot match feed cards
                    logging.debug(f"No @handle for {ucxxx} — will be skipped")
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                logging.warning(f"Could not resolve {ucxxx}: {e}")
                cache[ucxxx] = None
        pkg.save_state(state)

    # Summarise unresolvable channels once (at debug level — don't spam on every run)
    unresolvable = [ch for ch in ucxxx_entries if cache.get(ch) is None]
    if unresolvable:
        logging.debug(
            f"{len(unresolvable)} UCxxx ID(s) have no @handle on YouTube and "
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


def open_browser(headless: bool = False):
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
    context = p.chromium.launch_persistent_context(
        str(PROFILE_DIR),
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--disable-infobars"],
        ignore_default_args=["--enable-automation"],
        viewport={"width": 1280, "height": 800},
    )
    for extra in context.pages[1:]:
        extra.close()
    page = context.pages[0] if context.pages else context.new_page()

    page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=60000)
    time.sleep(PAGE_LOAD_WAIT)

    avatar = page.query_selector("button#avatar-btn, img#img[alt]")
    if not avatar:
        pkg.write_attention("Not logged in — session may have expired. Run: yt-dont-recommend --login")
        context.close()
        pw_cm.__exit__(None, None, None)
        return None

    return (pw_cm, context, page)


def close_browser(handle: tuple) -> None:
    """Close a browser handle returned by open_browser()."""
    pw_cm, context, _page = handle
    context.close()
    pw_cm.__exit__(None, None, None)


def process_channels(channel_sources: dict[str, str],
                     to_unblock: list[str] | None = None,
                     state: dict | None = None,
                     dry_run: bool = False,
                     limit: int | None = None,
                     headless: bool = False,
                     _browser: tuple | None = None) -> None:
    """
    Scan the YouTube home feed once and click 'Don't recommend channel' for
    every channel in channel_sources.

    channel_sources: {canonical_handle: source_name} — channels not yet in
        state["processed"], collected from all sources by the caller.
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

    processed_set = set(state["processed"])

    # Filter to_unblock against channels already attempted in this process run.
    to_unblock = [ch for ch in to_unblock if ch not in _pending_attempted_this_run]

    if not channel_sources and not to_unblock:
        logging.info("Nothing to do.")
        return

    n_sources = len(set(channel_sources.values())) if channel_sources else 0

    if dry_run and channel_sources:
        logging.info(
            f"DRY RUN — scanning home feed for {len(channel_sources)} channel(s) "
            f"across {n_sources} source(s)..."
        )

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
            logging.info(f"DRY RUN — would reverse YouTube block for: {', '.join(to_unblock)}")

        if not channel_sources:
            return

        subscriptions = fetch_subscriptions(page)

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
        time.sleep(PAGE_LOAD_WAIT)

        logging.info(
            f"Scanning home feed for {len(channel_lookup)} channel(s) "
            f"across {n_sources} source(s)..."
        )

        blocked_count = 0
        no_progress_scrolls = 0
        zero_parse_passes = 0
        selector_confirmed = False
        seen_paths: set[str] = set()

        while True:
            if limit and blocked_count >= limit:
                logging.info(f"Reached limit of {limit} channels blocked.")
                break
            if no_progress_scrolls >= MAX_NO_PROGRESS_SCROLLS:
                logging.info(
                    f"No additional blocklisted channels found after {no_progress_scrolls} "
                    "consecutive scrolls — feed exhausted for this run."
                )
                break

            cards = page.query_selector_all("ytd-rich-item-renderer")
            found_match_this_pass = False
            pass_parseable = 0

            for card in cards:
                if limit and blocked_count >= limit:
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
                logging.debug(f"Feed card channel: {path}")
                canonical = channel_lookup.get(path.lower())
                if not canonical or canonical in processed_set:
                    continue

                source = resolved_sources.get(canonical, "unknown")

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
                        pkg.save_state(state)
                    continue

                if dry_run:
                    logging.info(f"WOULD BLOCK: {canonical} (source: {source})")
                    blocked_count += 1
                    found_match_this_pass = True
                    continue

                # Capture display name from card for later unblocking.
                display_name = (channel_link.inner_text() or "").strip() or None

                logging.info(f"Found in feed: {canonical} — blocking...")
                try:
                    success = _click_dont_recommend(page, card)
                except Exception as e:
                    logging.error(f"FAIL {canonical}: {e}")
                    state["stats"]["total_failed"] += 1
                    pkg.save_state(state)
                    continue

                if success:
                    state["processed"].append(canonical)
                    processed_set.add(canonical)
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

                    logging.info(f"[{blocked_count}] OK {canonical}")
                    pkg.save_state(state)

                    if blocked_count % LONG_PAUSE_EVERY == 0:
                        logging.info(f"Taking a {LONG_PAUSE_SECONDS}s break...")
                        time.sleep(LONG_PAUSE_SECONDS)
                    else:
                        time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))

                    break  # rescan after DOM changes
                else:
                    state["stats"]["total_skipped"] += 1
                    logging.warning(f"SKIP {canonical} (appeared in feed but couldn't block)")
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
                        logging.info("Selector working — previous attention alert cleared.")

            if found_match_this_pass:
                no_progress_scrolls = 0
            else:
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                time.sleep(random.uniform(1.5, 3.0))
                no_progress_scrolls += 1

    finally:
        if _own_browser:
            context.close()
            if _pw_cm is not None:
                _pw_cm.__exit__(None, None, None)

    if dry_run:
        logging.info(f"DRY RUN complete. Would have blocked {blocked_count} channel(s).")
    else:
        logging.info(f"Done. Blocked {blocked_count} channel(s) this run. Stats: {state['stats']}")


def _perform_browser_unblocks(page, channels: list[str], state: dict) -> list[str]:
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

    Modifies *state* in place (retry counts, pending_unblock cleanup).
    Caller is responsible for saving state after this returns.

    Returns list of channels that were successfully unblocked on YouTube.
    """
    if not channels:
        return []

    pkg = _pkg()

    logging.info(f"Reversing YouTube 'Don't recommend' for {len(channels)} channel(s) via myactivity.google.com...")

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
            # Increment retry count. After _MAX_DISPLAY_NAME_RETRIES failures
            # (channel page unreachable / handle doesn't exist) give up and
            # clear from pending_unblock so the run stops looping on it.
            entry = state.setdefault("pending_unblock", {}).get(channel, {})
            retry_count = entry.get("_retry_count", 0) + 1
            if channel in state.get("pending_unblock", {}):
                state["pending_unblock"][channel]["_retry_count"] = retry_count
            if retry_count >= _MAX_DISPLAY_NAME_RETRIES:
                logging.warning(
                    f"Could not determine display name for {channel} after "
                    f"{retry_count} attempt(s) — giving up and clearing from pending queue."
                )
                state.setdefault("pending_unblock", {}).pop(channel, None)
            else:
                logging.warning(
                    f"Could not determine display name for {channel} "
                    f"(attempt {retry_count}/{_MAX_DISPLAY_NAME_RETRIES}) — will retry next run."
                )

    if not display_names:
        logging.warning(
            "Could not resolve display names for any channel pending unblock "
            "— channels may not exist or be temporarily unavailable. Will retry next run. "
            "Run: yt-dont-recommend --check-selectors if this persists."
        )
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
                msg = (
                    "Timed out waiting for Google verification — "
                    "pending unblocks will retry next run. "
                    "Channels NOT unblocked: " + ", ".join(channels)
                )
                logging.error(msg)
                pkg.write_attention(msg)
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
            # Entry absent from myactivity — either it was never created (channel
            # was never actually blocked), or it was already removed manually.
            # Either way the channel is not blocked on YouTube, so treat this as
            # a successful unblock and clear it from the pending queue.
            logging.warning(
                f"No feedback entry found for {channel} (display name: {display_name!r}) — "
                f"treating as already unblocked (entry may have been removed manually or never created)."
            )
            unblocked_channels.append(channel)
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

    # Only count channels that actually reached myactivity (had a resolved display name).
    # Channels that couldn't get a display name are counted separately and retried
    # on subsequent runs — they should not trigger the manual-intervention alert.
    failed = [ch for ch in display_names if ch not in unblocked_channels]
    if failed:
        msg = (
            f"{len(failed)} channel(s) could not be unblocked automatically: "
            f"{', '.join(failed)}. "
            f"Visit myactivity.google.com → Other activity → YouTube user feedback to remove them manually."
        )
        logging.warning(msg)
        pkg.write_attention(msg)
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
            args=["--disable-blink-features=AutomationControlled", "--no-first-run", "--disable-infobars"],
            ignore_default_args=["--enable-automation"],
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

    return home_ok or header_ok

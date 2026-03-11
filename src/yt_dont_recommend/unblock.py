"""
Unblock module: _perform_browser_unblocks and related helpers.

Handles navigating to myactivity.google.com to delete 'Don't recommend channel'
feedback entries, including Google password re-verification when required.
"""

import logging
import re
import time
from typing import Any

log = logging.getLogger(__name__)

from .config import PAGE_LOAD_WAIT, _n

# Channels whose unblock was attempted in the current Python process run.
# Prevents retrying the same channels for every source in one invocation,
# which would otherwise trigger Google's password-verification prompt twice.
_pending_attempted_this_run: set[str] = set()

# Give up on a channel's unblock after this many consecutive display-name
# failures (channel page unreachable / handle doesn't exist).
_MAX_DISPLAY_NAME_RETRIES = 3


def _pkg():
    """Late import of the yt_dont_recommend package to avoid circular imports."""
    import yt_dont_recommend as _p
    return _p


def _perform_browser_unblocks(page: Any, channels: list[str], state: dict) -> list[str]:
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

    log.info(f"Reversing YouTube 'Don't recommend' for {_n(len(channels), 'channel')} via myactivity.google.com...")

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
                log.warning(f"Could not look up display name for {channel}: {e}")
        if display_name:
            display_names[channel] = display_name
            log.debug(f"Display name for {channel}: {display_name!r}")
        else:
            # Increment retry count. After _MAX_DISPLAY_NAME_RETRIES failures
            # (channel page unreachable / handle doesn't exist) give up and
            # clear from pending_unblock so the run stops looping on it.
            entry = state.setdefault("pending_unblock", {}).get(channel, {})
            retry_count = entry.get("_retry_count", 0) + 1
            if channel in state.get("pending_unblock", {}):
                state["pending_unblock"][channel]["_retry_count"] = retry_count
            if retry_count >= _MAX_DISPLAY_NAME_RETRIES:
                log.warning(
                    f"Could not determine display name for {channel} after "
                    f"{_n(retry_count, 'attempt')} — giving up and clearing from pending queue."
                )
                state.setdefault("pending_unblock", {}).pop(channel, None)
            else:
                log.warning(
                    f"Could not determine display name for {channel} "
                    f"(attempt {retry_count}/{_MAX_DISPLAY_NAME_RETRIES}) — will retry next run."
                )

    if not display_names:
        log.warning(
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
        log.warning(
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
            log.info("Password challenge did not appear — proceeding (RAPT may still be valid).")
        else:
            # Phase 2: poll every 3s for a delete button (positive indicator that
            # verification succeeded and the feedback entries loaded).
            log.info("Challenge rendered — waiting for you to complete it in the browser window...")
            verified = False
            for i in range(60):  # up to 3 minutes (60 × 3s)
                time.sleep(3)
                try:
                    btn = page.query_selector('button[aria-label^="Delete activity item"]')
                except Exception:
                    continue  # page still navigating
                if btn:
                    log.info(f"Verification complete — feedback entries loaded ({i*3}s).")
                    verified = True
                    break
                if i % 10 == 0 and i > 0:
                    log.info(f"Still waiting for verification... ({i*3}s elapsed)")
            if not verified:
                msg = (
                    "Timed out waiting for Google verification — "
                    "pending unblocks will retry next run. "
                    "Channels NOT unblocked: " + ", ".join(channels)
                )
                log.error(msg)
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
            log.warning(
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
            log.debug(f"Dismissing 'Deletion complete' dialog for {channel}...")
            got_it.click()
        time.sleep(1.0)
        unblocked_channels.append(channel)
        log.info(f"UNBLOCKED on YouTube: {channel} ({display_name}) — channel can appear in recommendations again")

    # Only count channels that actually reached myactivity (had a resolved display name).
    # Channels that couldn't get a display name are counted separately and retried
    # on subsequent runs — they should not trigger the manual-intervention alert.
    failed = [ch for ch in display_names if ch not in unblocked_channels]
    if failed:
        msg = (
            f"{_n(len(failed), 'channel')} could not be unblocked automatically: "
            f"{', '.join(failed)}. "
            f"Visit myactivity.google.com → Other activity → YouTube user feedback to remove them manually."
        )
        log.warning(msg)
        pkg.write_attention(msg)
    return unblocked_channels

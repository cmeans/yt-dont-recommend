"""
YouTube "Don't Recommend Channel" Bulk Trainer

Fetches channel blocklists and uses browser automation to trigger
"Don't recommend channel" for each one. This trains your YouTube account's
recommendation algorithm at the account level, so the effect syncs across
all devices (Fire TV, mobile apps, smart TVs, etc.) where you're signed
into the same Google account.

Usage:
    # First run: will open browser for you to log into YouTube manually
    python -m yt_dont_recommend --login

    # Subsequent runs: processes the blocklist
    python -m yt_dont_recommend

    # Dry run: just fetch and show the list without clicking anything
    python -m yt_dont_recommend --dry-run

    # Use a built-in blocklist source
    python -m yt_dont_recommend --source deslop
    python -m yt_dont_recommend --source aislist

    # Use a local file
    python -m yt_dont_recommend --source /path/to/my-blocklist.txt

    # Use a remote URL
    python -m yt_dont_recommend --source https://example.com/blocklist.txt

    # Limit number of channels to process (useful for testing)
    python -m yt_dont_recommend --limit 10

    # Check whether current selectors still work against live YouTube
    python -m yt_dont_recommend --check-selectors

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

import logging

log = logging.getLogger(__name__)

# --- Re-exports from config ---
from .config import (
    BUILTIN_SOURCES,
    DEFAULT_SOURCES,
    PROFILE_DIR,
    STATE_FILE,
    LOG_FILE,
    DEFAULT_BLOCKLIST_EXCLUDE_FILE,
    DEFAULT_CLICKBAIT_EXCLUDE_FILE,
    _LEGACY_EXCLUDE_FILE,
    MIN_DELAY,
    MAX_DELAY,
    PAGE_LOAD_WAIT,
    LONG_PAUSE_EVERY,
    LONG_PAUSE_SECONDS,
    MIN_CARDS_FOR_SELECTOR_CHECK,
    SELECTOR_WARN_AFTER,
    ATTENTION_FILE,
    __version__,
    VERSION_CHECK_INTERVAL,
    STATE_VERSION,
    VIDEO_SELECTORS,
    MENU_BTN_SELECTORS,
    MENU_ITEM_SELECTOR,
    TARGET_PHRASES,
    _SCHEDULE_HOURS,
    _LAUNCHD_LABEL,
    _LAUNCHD_PLIST,
    _CRON_MARKER,
    setup_logging,
    _n,
)

# --- Re-exports from state ---
from .state import (
    _had_attention,
    load_state,
    save_state,
    _desktop_notify,
    _ntfy_notify,
    write_attention,
    check_attention_flag,
)

# --- Re-exports from blocklist ---
from .blocklist import (
    parse_text_blocklist,
    parse_json_blocklist,
    fetch_remote,
    resolve_source,
    channel_to_url,
    check_removals,
)

# --- Re-exports from scheduler ---
from .scheduler import (
    _parse_schedule_hours,
    _format_hours,
    _find_installed_binary,
    _schedule_macos,
    _schedule_linux,
    schedule_cmd,
)

# --- Re-exports from cli ---
from .cli import (
    _get_current_version,
    _get_latest_pypi_version,
    _version_tuple,
    _detect_installer,
    check_for_update,
    do_auto_upgrade,
    do_revert,
    setup_notify,
    remove_notify,
    test_notify,
    _first_run_welcome,
    do_uninstall,
    _clickbait_install_cmd,
    main,
)

# --- Browser automation thin wrappers ---

def do_login() -> None:
    """Open a browser window for the user to log into YouTube."""
    from .browser import do_login as _do_login
    return _do_login()


def fetch_subscriptions(page) -> set[str]:
    """
    Scrape the YouTube subscriptions management page and return a set of
    lowercased canonical channel IDs (@handle or UCxxx).

    Returns an empty set if the page cannot be parsed, with a warning logged.
    """
    from .browser import fetch_subscriptions as _fetch_subscriptions
    return _fetch_subscriptions(page)


def process_channels(channel_sources: dict[str, str],
                     to_unblock: list[str] | None = None,
                     state: dict | None = None,
                     dry_run: bool = False,
                     limit: int | None = None,
                     headless: bool = False,
                     clickbait_cfg: dict | None = None,
                     exclude_set: set | None = None,
                     _browser: tuple | None = None) -> None:
    """
    Scan the YouTube home feed once and block every channel in channel_sources.

    channel_sources: {canonical_handle: source_name} — unprocessed channels
        from all sources merged together by the caller.
    to_unblock: channels to remove from myactivity before scanning.
    state: loaded state dict; loaded fresh if None.
    clickbait_cfg: loaded clickbait config dict; when set, also classifies
        non-listed channels and blocks those with flagged video titles.
    exclude_set: lowercased channel handles to skip for clickbait evaluation.
    """
    from .browser import process_channels as _process_channels
    return _process_channels(
        channel_sources,
        to_unblock=to_unblock,
        state=state,
        dry_run=dry_run,
        limit=limit,
        headless=headless,
        clickbait_cfg=clickbait_cfg,
        exclude_set=exclude_set,
        _browser=_browser,
    )


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
    from .browser import check_selectors as _check_selectors
    return _check_selectors(test_channel)


if __name__ == "__main__":
    main()

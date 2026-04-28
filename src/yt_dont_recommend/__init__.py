"""
YouTube "Don't Recommend Channel" Bulk Trainer

Fetches channel blocklists and uses browser automation to trigger
"Don't recommend channel" for each one. This trains your YouTube account's
recommendation algorithm at the account level, so the effect syncs across
all devices (Fire TV, mobile apps, smart TVs, etc.) where you're signed
into the same Google account.

Usage:
    # First run: will open browser for you to log into YouTube manually
    yt-dont-recommend --login

    # Subsequent runs: processes the blocklist
    yt-dont-recommend --blocklist

    # Dry run: just fetch and show the list without clicking anything
    yt-dont-recommend --blocklist --dry-run

    # Use a built-in blocklist source
    yt-dont-recommend --blocklist --source deslop
    yt-dont-recommend --blocklist --source aislist

    # Use a local file
    yt-dont-recommend --blocklist --source /path/to/my-blocklist.txt

    # Use a remote URL
    yt-dont-recommend --blocklist --source https://example.com/blocklist.txt

    # Limit number of channels to process (useful for testing)
    yt-dont-recommend --blocklist --limit 10

    # Check whether current selectors still work against live YouTube
    yt-dont-recommend --check-selectors

Requirements:
    pip install playwright --break-system-packages
    playwright install chromium

Blocklist format (plain text):
    # Comments start with #
    # Blank lines are ignored
    # Entries are YouTube channel handles or IDs:
    @SomeHandle
    @AnotherChannel
    UCxxxxxxxxxxxxxxxxxxxxxx

Author: Chris Means (generated with Claude)
License: Apache-2.0
"""

import logging

log = logging.getLogger(__name__)

# --- Re-exports from config ---
# --- Re-exports from blocklist ---
from .blocklist import (
    channel_to_url,
    check_removals,
    fetch_remote,
    parse_json_blocklist,
    parse_text_blocklist,
    resolve_source,
)

# --- Re-exports from cli ---
from .cli import (
    _clickbait_install_cmd,
    _detect_installer,
    _first_run_welcome,
    _get_current_version,
    _get_latest_pypi_version,
    _version_tuple,
    check_for_update,
    do_auto_upgrade,
    do_revert,
    do_uninstall,
    main,
    remove_notify,
    setup_notify,
    test_notify,
)
from .config import (
    _CRON_MARKER,
    _LAUNCHD_LABEL,
    _LAUNCHD_PLIST,
    _LEGACY_EXCLUDE_FILE,
    _SELECTOR_DEFAULTS,
    ATTENTION_FILE,
    BUILTIN_SOURCES,
    DATA_DIR,
    DEFAULT_BLOCKLIST_EXCLUDE_FILE,
    DEFAULT_CLICKBAIT_EXCLUDE_FILE,
    DEFAULT_SOURCES,
    LOG_FILE,
    LONG_PAUSE_EVERY,
    LONG_PAUSE_SECONDS,
    MAX_DELAY,
    MENU_BTN_SELECTORS,
    MENU_ITEM_SELECTOR,
    MIN_CARDS_FOR_SELECTOR_CHECK,
    MIN_DELAY,
    PAGE_LOAD_WAIT,
    PROFILE_DIR,
    SCHEDULE_FILE,
    SELECTOR_WARN_AFTER,
    STATE_FILE,
    STATE_VERSION,
    TARGET_PHRASES,
    VERSION_CHECK_INTERVAL,
    VIDEO_SELECTORS,
    __version__,
    _n,
    clear_profile_cache,
    ensure_data_dir,
    get_selectors,
    load_schedule_config,
    load_selectors_config,
    setup_logging,
    write_selector_overrides,
)

# --- Re-exports from scheduler ---
from .scheduler import (
    _compute_daily_plan,
    _find_installed_binary,
    _schedule_linux,
    _schedule_macos,
    heartbeat,
    load_schedule,
    save_schedule,
    schedule_cmd,
)

# --- Re-exports from state ---
from .state import (
    _acted_video_ids,
    _desktop_notify,
    _had_attention,
    _ntfy_notify,
    check_attention_flag,
    load_state,
    save_state,
    write_attention,
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
    from .diagnostics import check_selectors as _check_selectors
    return _check_selectors(test_channel)

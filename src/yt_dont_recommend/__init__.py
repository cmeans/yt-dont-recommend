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

import argparse
import json
import logging
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.request import urlopen, Request

# --- Re-exports from config ---
from .config import (
    BUILTIN_SOURCES,
    DEFAULT_SOURCES,
    PROFILE_DIR,
    STATE_FILE,
    LOG_FILE,
    DEFAULT_EXCLUDE_FILE,
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

# --- Browser automation thin wrappers ---

def do_login():
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


def process_channels(channels: list[str], source: str,
                     dry_run: bool = False, limit: int | None = None,
                     headless: bool = False, unblock_policy: str = "all",
                     subscriptions: set[str] | None = None) -> set[str] | None:
    """
    Scan the YouTube home feed and click 'Don't recommend channel' on any
    card whose channel is in the blocklist.

    The feed is scrolled repeatedly to load new cards. Stops when the limit
    is reached, the feed is exhausted, or no blocklist channels have appeared
    after MAX_NO_PROGRESS_SCROLLS consecutive scrolls.

    Note: this only acts on channels YouTube is actively recommending to you.
    Channels not currently in your feed are not being recommended and don't
    need blocking. Run again periodically — as YouTube's algorithm adapts,
    fewer blocklisted channels will appear.
    """
    from .browser import process_channels as _process_channels
    return _process_channels(
        channels, source,
        dry_run=dry_run, limit=limit,
        headless=headless, unblock_policy=unblock_policy,
        subscriptions=subscriptions,
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


# --- Version checking and upgrades ---

def _get_current_version() -> str:
    """Return the running version, preferring importlib.metadata for installed builds."""
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("yt-dont-recommend")
    except Exception:
        return __version__


def _get_latest_pypi_version() -> str | None:
    """Fetch the latest version from PyPI. Returns None on any failure."""
    try:
        req = Request(
            "https://pypi.org/pypi/yt-dont-recommend/json",
            headers={"User-Agent": f"yt-dont-recommend/{_get_current_version()}"},
        )
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        return data["info"]["version"]
    except Exception:
        return None


def _version_tuple(v: str) -> tuple:
    try:
        return tuple(int(x) for x in v.split("."))
    except Exception:
        return (0,)


def _detect_installer() -> str | None:
    """Detect whether the tool was installed via uv tool or pipx."""
    bin_path = _find_installed_binary()
    if "uv" in bin_path and "tools" in bin_path:
        return "uv"
    if "pipx" in bin_path:
        return "pipx"
    return None


def check_for_update(state: dict, force: bool = False) -> str | None:
    """Check PyPI for a newer version. Returns the latest version string if newer, else None.

    Runs at most once per VERSION_CHECK_INTERVAL seconds unless force=True.
    Sends an ntfy notification the first time a new version is detected.
    Updates state in-place (caller must save_state if desired).
    """
    now = datetime.now().timestamp()
    if not force:
        last = state.get("last_version_check")
        if last:
            try:
                elapsed = now - datetime.fromisoformat(last).timestamp()
                if elapsed < VERSION_CHECK_INTERVAL:
                    # Still within interval — return cached result without hitting PyPI
                    latest = state.get("latest_known_version")
                    current = _get_current_version()
                    if latest and _version_tuple(latest) > _version_tuple(current):
                        return latest
                    return None
            except Exception:
                pass

    latest = _get_latest_pypi_version()
    state["last_version_check"] = datetime.now().isoformat()
    if not latest:
        return None

    state["latest_known_version"] = latest
    current = _get_current_version()

    if _version_tuple(latest) > _version_tuple(current):
        # Only notify via ntfy once per new version
        if state.get("notified_version") != latest:
            topic = state.get("notify_topic")
            if topic:
                _ntfy_notify(
                    topic,
                    f"yt-dont-recommend {latest} is available (current: {current}). "
                    f"Run: yt-dont-recommend --check-update"
                )
            state["notified_version"] = latest
        return latest

    return None


def do_auto_upgrade(state: dict) -> bool:
    """Upgrade to the latest version using the detected package manager.

    Saves the current version as previous_version before upgrading so
    --revert can restore it. Returns True if the upgrade succeeded.
    The new binary takes effect on the next invocation.
    """
    installer = _detect_installer()
    current = _get_current_version()

    if installer == "uv":
        # Use install@latest rather than upgrade — works whether or not the
        # version is pinned (e.g. after a --revert which pins to a specific version)
        cmd = ["uv", "tool", "install", "yt-dont-recommend@latest"]
    elif installer == "pipx":
        cmd = ["pipx", "upgrade", "yt-dont-recommend"]
    else:
        logging.warning(
            "Auto-upgrade: cannot detect package manager (uv or pipx). "
            "Upgrade manually: uv tool upgrade yt-dont-recommend"
        )
        return False

    logging.info(f"Auto-upgrading yt-dont-recommend via {installer}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        state["previous_version"] = current
        save_state(state)
        logging.info("Upgrade complete — new version takes effect on next run.")
        return True
    else:
        write_attention(f"Auto-upgrade failed: {result.stderr.strip()}")
        return False


def do_revert(target_version: str | None = None) -> None:
    """Revert to a specific version or the previously recorded one.

    If target_version is given, installs that exact version from PyPI.
    Otherwise falls back to state["previous_version"], which is updated at
    startup whenever the running version changes (manual or auto upgrade).
    """
    state = load_state()
    current = _get_current_version()

    if target_version:
        prev = target_version
    else:
        prev = state.get("previous_version")
        if not prev:
            print("No previous version recorded — nothing to revert to.")
            print("Tip: specify a version explicitly: yt-dont-recommend --revert 0.1.10")
            print("All published versions: https://github.com/cmeans/yt-dont-recommend/releases")
            return

    if prev == current:
        print(f"Already running {current} — nothing to do.")
        return

    installer = _detect_installer()

    if installer == "uv":
        cmd = ["uv", "tool", "install", "--force", f"yt-dont-recommend=={prev}"]
    elif installer == "pipx":
        cmd = ["pipx", "install", "--force", f"yt-dont-recommend=={prev}"]
    else:
        print(
            f"Cannot auto-revert — unknown installer.\n"
            f"Install manually:\n"
            f"  uv tool install --force yt-dont-recommend=={prev}\n"
            f"  pipx install --force yt-dont-recommend=={prev}"
        )
        return

    print(f"Reverting from {current} to {prev}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        state["previous_version"] = None
        state["auto_upgrade"] = False
        save_state(state)
        print(f"Reverted to {prev}. Takes effect on next run.")
        print("Auto-upgrade has been disabled to prevent immediately re-upgrading.")
        print("Re-enable with: yt-dont-recommend --auto-upgrade enable")
    else:
        print(f"Revert failed: {result.stderr.strip()}")


# --- ntfy.sh notification setup ---

def setup_notify() -> None:
    """Generate a private ntfy.sh topic and save it to state."""
    state = load_state()
    if state.get("notify_topic"):
        topic = state["notify_topic"]
        print(f"\nNotifications already configured.")
        print(f"Topic : {topic}")
        print(f"URL   : https://ntfy.sh/{topic}")
        print(f"\nTo reconfigure, run --remove-notify first.")
        return

    topic = f"ydr-{secrets.token_hex(16)}"
    state["notify_topic"] = topic
    save_state(state)

    print(f"\nNotification topic generated.")
    print(f"\nSubscribe in the ntfy app or browser:")
    print(f"  https://ntfy.sh/{topic}")
    print(f"\nSteps:")
    print(f"  1. Install the ntfy app (https://ntfy.sh) on your phone or desktop.")
    print(f"  2. Subscribe to the topic above.")
    print(f"  3. Run: yt-dont-recommend --test-notify")
    print(f"\nYour topic is private — it is a random string not guessable by others.")


def remove_notify() -> None:
    """Remove the ntfy.sh topic from state."""
    state = load_state()
    if not state.get("notify_topic"):
        print("No notification topic configured.")
        return
    state["notify_topic"] = None
    save_state(state)
    print("Notification topic removed.")


def test_notify() -> None:
    """Send a test notification to confirm the setup is working."""
    state = load_state()
    topic = state.get("notify_topic")
    if not topic:
        print("No notification topic configured. Run --setup-notify first.")
        return
    print(f"Sending test notification to https://ntfy.sh/{topic} ...")
    _ntfy_notify(topic, "Test notification — yt-dont-recommend is configured correctly.")
    print("Sent. Check your ntfy app.")


def _first_run_welcome() -> None:
    """Print a one-time welcome message on the very first run."""
    print("\nWelcome to yt-dont-recommend!")
    print()
    print("Quick start:")
    print("  1. yt-dont-recommend --login            # sign into your Google account (required once)")
    print("  2. yt-dont-recommend --schedule install  # set up twice-daily automatic runs")
    print("  3. yt-dont-recommend --dry-run           # preview what would be blocked")
    print()
    print("Run yt-dont-recommend --help for all options.")
    print()


def do_uninstall() -> None:
    """Remove schedule, offer to delete data directory, then print the uninstall command."""
    print("\nUninstalling yt-dont-recommend")
    print("=" * 34)

    # Step 1: remove schedule
    print("\nStep 1: Removing schedule...")
    try:
        schedule_cmd("remove")
    except Exception as e:
        print(f"  Could not remove schedule (may not be installed): {e}")

    # Step 2: offer to remove data directory
    data_dir = STATE_FILE.parent
    if data_dir.exists():
        print(f"\nStep 2: Remove data directory {data_dir}?")
        print("  This will delete your browser session, state file, and logs.")
        try:
            answer = input("  Remove? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer == "y":
            shutil.rmtree(data_dir)
            print(f"  Removed {data_dir}")
        else:
            print("  Kept.")
    else:
        print(f"\nStep 2: No data directory found at {data_dir} — nothing to remove.")

    # Step 3: print the package manager uninstall command
    installer = _detect_installer()
    print("\nStep 3: Run the following to uninstall the package:")
    if installer == "uv":
        print("  uv tool uninstall yt-dont-recommend")
    elif installer == "pipx":
        print("  pipx uninstall yt-dont-recommend")
    else:
        print("  uv tool uninstall yt-dont-recommend")
        print("  # or: pipx uninstall yt-dont-recommend")
    print()


# --- Main ---

def main():
    builtin_keys = ", ".join(BUILTIN_SOURCES.keys())

    parser = argparse.ArgumentParser(
        description="Bulk-train YouTube's recommendation algorithm by triggering 'Don't recommend channel'.",
        epilog="First run: use --login to authenticate. Then run normally to process.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {_get_current_version()}")
    parser.add_argument("--login", action="store_true",
                        help="Open browser to log into YouTube (do this first)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and display the blocklist without taking any action")
    parser.add_argument(
        "--source",
        default=None,
        metavar="SOURCE",
        help=(
            f"Blocklist source. Built-in names: {builtin_keys} (comma-separated for multiple). "
            "Also accepts a local file path or an HTTP/HTTPS URL. "
            f"Defaults to all built-in sources ({', '.join(DEFAULT_SOURCES)}) when not specified. "
            "Text format: one entry per line (@handle or UCxxx), # for comments."
        ),
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Stop after blocking this many channels")
    parser.add_argument(
        "--unblock-policy", choices=["all", "any"], default="all",
        help=(
            "When a channel is dropped from a blocklist, when to unblock it. "
            "'all' (default): only unblock when removed from every source that blocked it. "
            "'any': unblock as soon as any single source drops it."
        ),
    )
    parser.add_argument(
        "--exclude",
        default=None,
        metavar="SOURCE",
        help=(
            f"Channels to never block, regardless of the blocklist. "
            f"Accepts a local file path or HTTP/HTTPS URL in the same plain-text format as --source. "
            f"If not specified, {DEFAULT_EXCLUDE_FILE} is loaded automatically if it exists."
        ),
    )
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (no visible window)")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable debug logging")
    parser.add_argument("--reset-state", action="store_true",
                        help="Clear the processed-channels state and start fresh")
    parser.add_argument("--list-sources", action="store_true",
                        help="Show available built-in blocklist sources")
    parser.add_argument("--stats", action="store_true",
                        help="Show processing statistics and exit")
    parser.add_argument("--export-state", nargs="?", const="-", metavar="FILE",
                        help="Export blocked channels as a plain-text blocklist. Writes to FILE, or stdout if omitted.")
    parser.add_argument("--check-selectors", action="store_true",
                        help=(
                            "Test whether the DOM selectors still work against live YouTube. "
                            "Opens a visible browser, checks both home feed and a channel page, "
                            "and saves a report with screenshots. Run this after YouTube updates break things."
                        ))
    parser.add_argument("--test-channel", default="@YouTube", metavar="CHANNEL",
                        help=(
                            "Channel to use for the --check-selectors channel page test "
                            "(default: @YouTube)"
                        ))
    parser.add_argument("--clear-alerts", action="store_true",
                        help="Clear the pending alerts flag file and exit")
    parser.add_argument("--check-update", action="store_true",
                        help="Check PyPI for a newer version and exit")
    parser.add_argument(
        "--auto-upgrade", choices=["enable", "disable"], metavar="enable|disable",
        help="Enable or disable automatic upgrades when a new version is detected",
    )
    parser.add_argument("--revert", nargs="?", const=True, metavar="VERSION",
                        help=(
                            "Revert to a previous version. With no argument, reverts to the "
                            "last recorded version. Pass a specific version to target it directly: "
                            "--revert 0.1.10"
                        ))
    parser.add_argument("--setup-notify", action="store_true",
                        help="Generate a private ntfy.sh topic for push notifications and show subscribe instructions")
    parser.add_argument("--remove-notify", action="store_true",
                        help="Remove the configured ntfy.sh notification topic")
    parser.add_argument("--test-notify", action="store_true",
                        help="Send a test notification to confirm ntfy.sh setup is working")
    parser.add_argument(
        "--schedule",
        choices=["install", "remove", "status"],
        metavar="ACTION",
        help=(
            "Manage the automatic run schedule (no cron knowledge required). "
            "Actions: install, remove, status. "
            "Uses launchd on macOS, cron on Linux. "
            "Default schedule: 3:00 AM and 3:00 PM daily."
        ),
    )
    parser.add_argument(
        "--schedule-hours",
        default=None,
        metavar="HH,HH",
        help=(
            "Override the hours for --schedule install (24h, comma-separated). "
            "Example: --schedule-hours 6,18 runs at 6:00 AM and 6:00 PM. "
            "Default: 3,15"
        ),
    )
    parser.add_argument("--uninstall", action="store_true",
                        help=(
                            "Remove the schedule and optionally delete all data, "
                            "then print the package manager command to complete uninstallation"
                        ))

    args = parser.parse_args()
    setup_logging(args.verbose)

    # Detect first run before load_state() creates the state file.
    _is_first_run = not STATE_FILE.exists()

    # Track version on every invocation so --revert works regardless of how
    # the upgrade was performed (auto-upgrade or manual uv/pipx install).
    _state = load_state()
    _running = _get_current_version()
    if _state.get("current_version") != _running:
        prior = _state.get("current_version")
        if prior is not None:
            _state["previous_version"] = prior
        _state["current_version"] = _running
        save_state(_state)
    del _state, _running

    if _is_first_run:
        _first_run_welcome()

    if args.uninstall:
        do_uninstall()
        return

    if args.clear_alerts:
        if ATTENTION_FILE.exists():
            ATTENTION_FILE.unlink()
            print("Alerts cleared.")
        else:
            print("No alerts to clear.")
        return

    if args.setup_notify:
        setup_notify()
        return

    if args.remove_notify:
        remove_notify()
        return

    if args.test_notify:
        test_notify()
        return

    if args.check_update:
        state = load_state()
        latest = check_for_update(state, force=True)
        save_state(state)
        current = _get_current_version()
        if latest:
            print(f"New version available: {latest} (you have {current})")
            installer = _detect_installer()
            if installer == "pipx":
                print("Upgrade with: pipx upgrade yt-dont-recommend")
            else:
                print("Upgrade with: uv tool install yt-dont-recommend@latest")
        else:
            print(f"You are running the latest version ({current}).")
        return

    if args.auto_upgrade:
        state = load_state()
        state["auto_upgrade"] = (args.auto_upgrade == "enable")
        save_state(state)
        status = "enabled" if state["auto_upgrade"] else "disabled"
        print(f"Auto-upgrade {status}.")
        return

    if args.revert is not None:
        do_revert(None if args.revert is True else args.revert)
        return

    check_attention_flag()

    if args.schedule:
        schedule_hours = None
        if args.schedule_hours:
            try:
                schedule_hours = _parse_schedule_hours(args.schedule_hours)
            except ValueError:
                print(
                    "--schedule-hours: accepted formats:\n"
                    "  6,18        specific hours (0-23, comma-separated)\n"
                    "  */4         every 4 hours (step of 1-23)\n"
                    "  hourly      every hour"
                )
                sys.exit(1)
        schedule_cmd(args.schedule, schedule_hours)
        return

    if args.list_sources:
        print("\nBuilt-in blocklist sources:\n")
        for key, src in BUILTIN_SOURCES.items():
            print(f"  {key:12s} - {src['description']}")
            print(f"  {'':12s}   {src['url']}")
            print()
        print("You can also pass a local file path or any HTTP/HTTPS URL to --source.\n")
        return

    if args.stats:
        state = load_state()
        whb = state.get("would_have_blocked", {})
        print(f"\nBlocked channels   : {len(state['processed'])}")
        print(f"Last run           : {state.get('last_run', 'never')}")
        s = state.get("stats", {})
        print(f"Total blocked      : {s.get('total_blocked', 0)}")
        print(f"Total skipped      : {s.get('total_skipped', 0)}  (appeared in feed but menu action failed)")
        print(f"Total failed       : {s.get('total_failed', 0)}  (error during block attempt)")
        # Per-source breakdown from blocked_by
        per_source: dict[str, int] = {}
        for info in state.get("blocked_by", {}).values():
            for src in info.get("sources", []):
                per_source[src] = per_source.get(src, 0) + 1
        if per_source:
            print(f"\nBlocked by source  :")
            for src, count in sorted(per_source.items(), key=lambda x: -x[1]):
                size = state.get("source_sizes", {}).get(src)
                size_str = f"  (list size: {size})" if size is not None else ""
                print(f"  {src:<16s} {count:>5d}{size_str}")
        if whb:
            print(f"\nSubscribed channels in blocklist (skipped, notified once):")
            for ch, info in whb.items():
                print(f"  {ch}  (sources: {info.get('sources', [])}, first seen: {info.get('first_seen', '?')[:10]})")
        print(f"\nState file         : {STATE_FILE}")
        print(f"Log file           : {LOG_FILE}")
        return

    if args.export_state is not None:
        state = load_state()
        blocked_by = state.get("blocked_by", {})
        lines = [
            f"# Exported by yt-dont-recommend {_get_current_version()} on {datetime.now().strftime('%Y-%m-%d')}",
            f"# Total blocked channels: {len(blocked_by)}",
            "",
        ]
        for channel in sorted(blocked_by):
            sources = blocked_by[channel].get("sources", [])
            src_note = f"  # {', '.join(sources)}" if sources else ""
            lines.append(f"{channel}{src_note}")
        output = "\n".join(lines) + "\n"
        if args.export_state == "-":
            print(output, end="")
        else:
            Path(args.export_state).write_text(output, encoding="utf-8")
            print(f"Exported {len(blocked_by)} channels to {args.export_state}")
        return

    if args.reset_state:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            logging.info("State reset.")
        else:
            logging.info("No state file to reset.")
        return

    if args.login:
        do_login()
        return

    if args.check_selectors:
        ok = check_selectors(args.test_channel)
        sys.exit(0 if ok else 1)

    # Periodic version check (at most once per 24 h; non-blocking on network failure)
    state = load_state()
    latest = check_for_update(state)
    if latest:
        current = _get_current_version()
        logging.info(f"New version available: {latest} (you have {current}) — run --check-update for details")
        if state.get("auto_upgrade"):
            do_auto_upgrade(state)
        save_state(state)

    # Resolve which sources to run
    if args.source is None:
        sources = DEFAULT_SOURCES
    else:
        sources = [s.strip() for s in args.source.split(",")]

    # Build exclusion set once (shared across all sources)
    exclude_source = args.exclude or (str(DEFAULT_EXCLUDE_FILE) if DEFAULT_EXCLUDE_FILE.exists() else None)
    exclude_set: set[str] = set()
    if exclude_source:
        exclude_set = {c.lower() for c in resolve_source(exclude_source, quiet=True)}
        label = "--exclude" if args.exclude else f"default exclude file ({DEFAULT_EXCLUDE_FILE})"
        logging.info(f"Loaded {len(exclude_set)} exclusion(s) via {label}")

    run_subscriptions: set[str] | None = None  # shared across sources; fetched once on first active source
    for source in sources:
        if len(sources) > 1:
            logging.info(f"--- Source: {source} ---")
        try:
            channels = resolve_source(source)
        except RuntimeError as e:
            logging.error(f"Could not load source '{source}': {e} — skipping.")
            continue
        # Track source size and notify on growth
        _st = load_state()
        _sizes = _st.setdefault("source_sizes", {})
        _prev = _sizes.get(source)
        if _prev is not None and len(channels) > _prev:
            _growth = len(channels) - _prev
            logging.info(f"*** Blocklist '{source}' grew by {_growth} channel(s) ({_prev} → {len(channels)}) since last run")
        _sizes[source] = len(channels)
        save_state(_st)
        del _st, _sizes, _prev
        if exclude_set:
            before = len(channels)
            channels = [c for c in channels if c.lower() not in exclude_set]
            logging.info(f"Excluded {before - len(channels)} channel(s) ({len(channels)} remaining)")
        result = process_channels(
            channels,
            source=source,
            dry_run=args.dry_run,
            limit=args.limit,
            headless=args.headless,
            unblock_policy=args.unblock_policy,
            subscriptions=run_subscriptions,
        )
        if result is not None:
            run_subscriptions = result

    # Re-read _had_attention from state module since it may have been set there
    import yt_dont_recommend.state as _state_mod
    if _state_mod._had_attention:
        sys.exit(1)


if __name__ == "__main__":
    main()

"""
CLI entry point for yt-dont-recommend.

Contains main(), argument parsing, and all CLI command handlers.
Imports only from sub-modules (config, state, blocklist, scheduler, browser)
to avoid circular imports with __init__.py.
"""

import argparse
import json
import logging
import secrets
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen, Request

from .config import (
    BUILTIN_SOURCES,
    DEFAULT_SOURCES,
    STATE_FILE,
    LOG_FILE,
    DEFAULT_BLOCKLIST_EXCLUDE_FILE,
    DEFAULT_CLICKBAIT_EXCLUDE_FILE,
    _LEGACY_EXCLUDE_FILE,
    ATTENTION_FILE,
    __version__,
    VERSION_CHECK_INTERVAL,
    setup_logging,
    _n,
)
from .state import (
    load_state,
    save_state,
    _ntfy_notify,
    write_attention,
    check_attention_flag,
)
from .blocklist import resolve_source, check_removals
from .scheduler import schedule_cmd, _parse_schedule_hours, _find_installed_binary

log = logging.getLogger(__name__)


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
        log.warning(
            "Auto-upgrade: cannot detect package manager (uv or pipx). "
            "Upgrade manually: uv tool upgrade yt-dont-recommend"
        )
        return False

    log.info(f"Auto-upgrading yt-dont-recommend via {installer}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        state["previous_version"] = current
        save_state(state)
        log.info("Upgrade complete — new version takes effect on next run.")
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
    print("  1. yt-dont-recommend --login                  # sign into your Google account (required once)")
    print("  2. yt-dont-recommend --schedule install       # set up twice-daily automatic runs")
    print("  3. yt-dont-recommend --blocklist --dry-run    # preview what would be blocked")
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


def _clickbait_install_cmd() -> str:
    """Return the install command appropriate for how this tool was installed."""
    path_str = str(Path(sys.argv[0]).resolve()).lower()
    if "uv/tools" in path_str or "uv\\tools" in path_str:
        return "uv tool install 'yt-dont-recommend[clickbait]'"
    if "pipx" in path_str:
        return "pipx install 'yt-dont-recommend[clickbait]'"
    return "pip install 'yt-dont-recommend[clickbait]'"


# --- Main ---

def main() -> None:
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
            f"Channels to never block via --blocklist, regardless of the source list. "
            f"Accepts a local file path or HTTP/HTTPS URL in the same plain-text format as --source. "
            f"If not specified, {DEFAULT_BLOCKLIST_EXCLUDE_FILE} is loaded automatically if it exists "
            f"(legacy: {_LEGACY_EXCLUDE_FILE} is also accepted with a deprecation warning)."
        ),
    )
    parser.add_argument(
        "--clickbait-exclude",
        default=None,
        metavar="SOURCE",
        help=(
            f"Channels to never evaluate for clickbait, regardless of title framing. "
            f"Accepts a local file path or HTTP/HTTPS URL in the same plain-text format as --source. "
            f"If not specified, {DEFAULT_CLICKBAIT_EXCLUDE_FILE} is loaded automatically if it exists."
        ),
    )
    parser.add_argument("--headless", action="store_true",
                        help="Run browser in headless mode (no visible window)")
    parser.add_argument("--blocklist", action="store_true",
                        help="Run channel-level 'Don't recommend channel' blocking using "
                             "configured blocklist sources (use --source to specify; "
                             "defaults to all built-in sources)")
    parser.add_argument("--clickbait", action="store_true",
                        help="Scan feed videos for clickbait and click 'Not interested' "
                             "(video-level action; does not affect channel recommendations; "
                             "requires: pip install yt-dont-recommend[clickbait])")
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
        # Blocklist coverage: blocked / total across all known source sizes
        source_sizes = state.get("source_sizes", {})
        if source_sizes:
            total_on_lists = sum(source_sizes.values())
            total_blocked = len(state.get("blocked_by", {}))
            pct = total_blocked / total_on_lists * 100 if total_on_lists else 0
            print(f"\nFeed coverage      : {total_blocked} of ~{total_on_lists} channels blocked ({pct:.1f}%)")
            print(f"                     (channels appear in coverage only after showing in the home feed)")
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
            log.info("State reset.")
        else:
            log.info("No state file to reset.")
        return

    if args.login:
        from .browser import do_login
        do_login()
        return

    if args.check_selectors:
        from .browser import check_selectors
        ok = check_selectors(args.test_channel)
        sys.exit(0 if ok else 1)

    # Determine operating mode. Running without --blocklist or --clickbait shows help.
    run_blocklist = args.blocklist or (args.source is not None)
    run_clickbait = args.clickbait

    if not run_blocklist and not run_clickbait:
        parser.print_help()
        return

    # Periodic version check (at most once per 24 h; non-blocking on network failure)
    state = load_state()
    latest = check_for_update(state)
    if latest:
        current = _get_current_version()
        log.info(f"New version available: {latest} (you have {current}) — run --check-update for details")
        if state.get("auto_upgrade"):
            do_auto_upgrade(state)
        save_state(state)

    # Load state once; per-source setup (check_removals, channel collection) is
    # fast and needs no browser. A single browser session is opened afterward
    # for one combined feed scan across all sources.
    state = load_state()
    processed_set = set(state["processed"])
    channel_sources: dict[str, str] = {}  # {canonical: source} — unprocessed channels
    all_unblocks: list[str] = []

    if run_blocklist:
        # Resolve which sources to run
        if args.source is None:
            sources = DEFAULT_SOURCES
        else:
            sources = [s.strip() for s in args.source.split(",")]

        # Build blocklist exclusion set
        if args.exclude:
            blocklist_exclude_source = args.exclude
            blocklist_exclude_label = "--exclude"
        elif DEFAULT_BLOCKLIST_EXCLUDE_FILE.exists():
            blocklist_exclude_source = str(DEFAULT_BLOCKLIST_EXCLUDE_FILE)
            blocklist_exclude_label = f"default exclude file ({DEFAULT_BLOCKLIST_EXCLUDE_FILE})"
        elif _LEGACY_EXCLUDE_FILE.exists():
            blocklist_exclude_source = str(_LEGACY_EXCLUDE_FILE)
            blocklist_exclude_label = f"legacy exclude file ({_LEGACY_EXCLUDE_FILE})"
            log.warning(
                f"{_LEGACY_EXCLUDE_FILE} is deprecated — rename it to {DEFAULT_BLOCKLIST_EXCLUDE_FILE}"
            )
        else:
            blocklist_exclude_source = None
            blocklist_exclude_label = None
        exclude_set: set[str] = set()
        if blocklist_exclude_source:
            exclude_set = {c.lower() for c in resolve_source(blocklist_exclude_source, quiet=True)}
            log.info(f"Loaded {_n(len(exclude_set), 'blocklist exclusion')} via {blocklist_exclude_label}")

        for source in sources:
            if len(sources) > 1:
                log.info(f"--- Source: {source} ---")
            try:
                channels = resolve_source(source)
            except RuntimeError as e:
                log.error(f"Could not load source '{source}': {e} — skipping.")
                continue

            # Track source size and notify on growth
            sizes = state.setdefault("source_sizes", {})
            prev = sizes.get(source)
            if prev is not None and len(channels) > prev:
                log.info(
                    f"*** Blocklist '{source}' grew by {_n(len(channels) - prev, 'channel')} "
                    f"({prev} → {len(channels)}) since last run"
                )
            sizes[source] = len(channels)

            if exclude_set:
                before = len(channels)
                channels = [c for c in channels if c.lower() not in exclude_set]
                log.info(f"Excluded {_n(before - len(channels), 'channel')} ({len(channels)} remaining)")

            # Per-source removal detection (no browser required)
            to_unblock = check_removals(state, channels, source, args.unblock_policy)
            for ch in to_unblock:
                if ch not in all_unblocks:
                    all_unblocks.append(ch)

            # Collect unprocessed channels; first source wins on overlap
            new_for_source = 0
            for ch in channels:
                if ch not in processed_set and ch not in channel_sources:
                    channel_sources[ch] = source
                    new_for_source += 1
            already_done = sum(1 for ch in channels if ch in processed_set)
            log.info(
                f"{len(channels)} channels in blocklist, "
                f"{already_done} already blocked, "
                f"{new_for_source} added to scan queue"
            )

        # Add any pending unblocks from previous failed runs
        pending = state.get("pending_unblock", {})
        for ch in pending:
            if ch not in all_unblocks:
                all_unblocks.append(ch)
        if pending:
            log.info(
                f"Retrying {_n(len(pending), 'pending unblock')} from a previous run: {list(pending)}"
            )

        save_state(state)  # persist source_sizes before opening browser

        if len(sources) > 1 and (channel_sources or all_unblocks):
            log.info(f"--- Processing {_n(len(sources), 'source')} ---")

    clickbait_cfg = None
    if run_clickbait:
        try:
            import ollama as _  # noqa: F401
        except ImportError:
            log.error(
                "--clickbait requires additional dependencies. Install with:\n"
                f"  {_clickbait_install_cmd()}"
            )
            return
        log.info("Clickbait detection enabled.")
        from .clickbait import load_config as _load_clickbait_config
        clickbait_cfg = _load_clickbait_config()
        _v = clickbait_cfg["video"]
        _thumb = _v["thumbnail"]
        _trans = _v["transcript"]
        _thumb_str = (
            f"{_thumb['model']['name']} threshold={_thumb['threshold']}"
            if _thumb["enabled"] else "disabled"
        )
        _trans_str = (
            f"{_trans['model']['name']} threshold={_trans['threshold']}"
            if _trans["enabled"] else "disabled"
        )
        log.info(
            f"Clickbait config: title={_v['title']['model']['name']} "
            f"threshold={_v['title']['threshold']} | "
            f"thumbnail={_thumb_str} | transcript={_trans_str}"
        )

    # Build clickbait exclusion set (independent of blocklist exclusions)
    if args.clickbait_exclude:
        clickbait_exclude_source = args.clickbait_exclude
        clickbait_exclude_label = "--clickbait-exclude"
    elif DEFAULT_CLICKBAIT_EXCLUDE_FILE.exists():
        clickbait_exclude_source = str(DEFAULT_CLICKBAIT_EXCLUDE_FILE)
        clickbait_exclude_label = f"default clickbait exclude file ({DEFAULT_CLICKBAIT_EXCLUDE_FILE})"
    else:
        clickbait_exclude_source = None
        clickbait_exclude_label = None
    clickbait_exclude_set: set[str] = set()
    if clickbait_exclude_source:
        clickbait_exclude_set = {c.lower() for c in resolve_source(clickbait_exclude_source, quiet=True)}
        log.info(f"Loaded {_n(len(clickbait_exclude_set), 'clickbait exclusion')} via {clickbait_exclude_label}")

    if not channel_sources and not all_unblocks and clickbait_cfg is None:
        log.info("Nothing to do.")
    else:
        from .browser import open_browser, close_browser, process_channels
        browser_handle = open_browser(headless=args.headless)
        if browser_handle is None:
            return  # write_attention already called by open_browser
        try:
            process_channels(
                channel_sources,
                to_unblock=all_unblocks,
                state=state,
                dry_run=args.dry_run,
                limit=args.limit,
                headless=args.headless,
                clickbait_cfg=clickbait_cfg,
                exclude_set=clickbait_exclude_set or None,
                _browser=browser_handle,
            )
        finally:
            close_browser(browser_handle)

    # Re-read _had_attention from state module since it may have been set there
    import yt_dont_recommend.state as _state_mod
    if _state_mod._had_attention:
        sys.exit(1)

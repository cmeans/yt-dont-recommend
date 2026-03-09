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
import logging.handlers
import plistlib
import random
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.parse import urlparse, quote
from urllib.request import urlopen, Request

# --- Configuration ---

BUILTIN_SOURCES = {
    "deslop": {
        "name": "DeSlop",
        "url": "https://raw.githubusercontent.com/NikoboiNFTB/DeSlop/refs/heads/main/block/list.txt",
        "format": "text",
        "description": "Curated list from the DeSlop project (~130+ channels)",
    },
    "aislist": {
        "name": "AiSList",
        "url": "https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/aislist_blocklist.txt",
        "format": "text",
        "description": "Community-maintained list from AiSList (~8400+ channels, ! comments)",
    },
}

DEFAULT_SOURCES = list(BUILTIN_SOURCES.keys())  # run all built-in sources by default

# Browser profile directory (persists login state between runs)
PROFILE_DIR = Path.home() / ".yt-dont-recommend" / "browser-profile"

# State file to track which channels have been processed
STATE_FILE = Path.home() / ".yt-dont-recommend" / "processed.json"

# Log file
LOG_FILE = Path.home() / ".yt-dont-recommend" / "run.log"

# Default personal exclusion list — loaded automatically if present
DEFAULT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "exclude.txt"

# Delays (seconds) — be respectful to avoid rate limiting
MIN_DELAY = 3.0
MAX_DELAY = 7.0
PAGE_LOAD_WAIT = 3.0
LONG_PAUSE_EVERY = 25
LONG_PAUSE_SECONDS = 30
# Selector health check: if this many consecutive passes each have >=
# MIN_CARDS_FOR_SELECTOR_CHECK cards but yield zero parseable channel links,
# the feed card selector is probably broken.
MIN_CARDS_FOR_SELECTOR_CHECK = 10
SELECTOR_WARN_AFTER = 3

# Attention flag file — written when something requires user action between runs
ATTENTION_FILE = Path.home() / ".yt-dont-recommend" / "needs-attention.txt"

# Version
__version__ = "0.1.21"
VERSION_CHECK_INTERVAL = 86400  # seconds between automatic checks (24 h)

# State schema version — bump this whenever the state file structure changes.
# Policy: only ADD new keys (never rename/remove/reinterpret existing ones).
# load_state() warns when it reads a state file written by a newer version.
STATE_VERSION = 1

# Set to True by write_attention() so main() can exit with code 1 when
# something serious enough to alert the user occurred during the run.
_had_attention = False

# Schedule management
_SCHEDULE_HOURS = (3, 15)  # 3:00 AM and 3:00 PM
_LAUNCHD_LABEL = "com.user.yt-dont-recommend"
_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
_CRON_MARKER = "# managed by yt-dont-recommend"

# Selectors used both for processing and selector checks.
# YouTube changes its DOM frequently — run --check-selectors if things break.
VIDEO_SELECTORS = [
    "ytd-rich-item-renderer",
    "ytd-grid-video-renderer",
    "ytd-video-renderer",
    "#contents ytd-rich-grid-row",
]
MENU_BTN_SELECTORS = [
    "button[aria-label='More actions']",                        # home feed and search results
    "button[aria-label='Action menu']",                         # channel pages
    "button.yt-icon-button#button[aria-label='Action menu']",
    "yt-icon-button#button[aria-label='Action menu']",
    "ytd-menu-renderer yt-icon-button",
    "#menu button",
]
MENU_ITEM_SELECTOR = (
    "ytd-menu-service-item-renderer, tp-yt-paper-item, "
    "ytd-menu-navigation-item-renderer, [role='menuitem']"
)
TARGET_PHRASES = ("don't recommend", "dont recommend")


# --- Logging Setup ---

def setup_logging(verbose: bool = False):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            file_handler,
        ],
    )


# --- State Management ---

def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            s = json.load(f)
        # Ensure new fields exist for older state files (backward compat)
        s.setdefault("blocked_by", {})
        s.setdefault("would_have_blocked", {})
        # Migrate old stat key names to descriptive names
        stats = s.setdefault("stats", {})
        if "success" in stats:
            stats["total_blocked"] = stats.pop("success")
        if "skipped" in stats:
            stats["total_skipped"] = stats.pop("skipped")
        if "failed" in stats:
            stats["total_failed"] = stats.pop("failed")
        stats.setdefault("total_blocked", 0)
        stats.setdefault("total_skipped", 0)
        stats.setdefault("total_failed", 0)
        # Drop ucxxx_to_handle self-mappings left by earlier versions
        cache = s.get("ucxxx_to_handle", {})
        stale = [k for k, v in cache.items() if k == v]
        for k in stale:
            cache[k] = None
        s.setdefault("notify_topic", None)
        s.setdefault("last_version_check", None)
        s.setdefault("latest_known_version", None)
        s.setdefault("notified_version", None)
        s.setdefault("auto_upgrade", False)
        s.setdefault("previous_version", None)
        s.setdefault("current_version", None)
        s.setdefault("source_sizes", {})
        # Schema version guard — warn if state was written by a newer binary
        file_sv = s.get("state_version", 0)
        if file_sv > STATE_VERSION:
            logging.warning(
                f"State file was written by a newer version of yt-dont-recommend "
                f"(schema v{file_sv}, this binary expects v{STATE_VERSION}). "
                "Some state fields may be ignored. Upgrade to restore full functionality."
            )
        s.setdefault("state_version", STATE_VERSION)
        return s
    return {
        "processed": [],
        "blocked_by": {},
        "would_have_blocked": {},
        "last_run": None,
        "stats": {"total_blocked": 0, "total_skipped": 0, "total_failed": 0},
        "notify_topic": None,
        "last_version_check": None,
        "latest_known_version": None,
        "notified_version": None,
        "auto_upgrade": False,
        "previous_version": None,
        "current_version": None,
        "source_sizes": {},
        "state_version": STATE_VERSION,
    }


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now().isoformat()
    # Don't leave empty pending_unblock in the state file
    if "pending_unblock" in state and not state["pending_unblock"]:
        del state["pending_unblock"]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Blocklist Fetching ---

def parse_text_blocklist(raw: str) -> list[str]:
    """Parse plain text blocklist: one channel path per line.

    Supports # and ! comment prefixes (full-line and inline).
    Normalizes all variants to canonical form: @handle or UCxxx.

    Examples of valid lines:
        @SomeChannel
        @SomeChannel  # optional inline note
        UCxxxxxxxxxxxxxxxxxxxxxxxx
    """
    channels = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # Strip inline comment: "@handle  # reason" → "@handle"
        if "#" in line:
            line = line[:line.index("#")].strip()
        if not line:
            continue
        # Strip leading slash: /@handle → @handle
        if line.startswith("/@"):
            line = line[1:]
        # Strip /channel/ prefix: /channel/UCxxx → UCxxx
        elif line.startswith("/channel/"):
            line = line[len("/channel/"):]
        channels.append(line)
    return channels


def parse_json_blocklist(raw: str) -> list[str]:
    """Parse JSON blocklist. Handles several common formats.

    All results are normalized to canonical form: @handle or UCxxx.
    """
    channels = []
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, str):
                    # Normalize /@handle → @handle, /channel/UCxxx → UCxxx
                    if entry.startswith("/@"):
                        entry = entry[1:]
                    elif entry.startswith("/channel/"):
                        entry = entry[len("/channel/"):]
                    channels.append(entry)
                elif isinstance(entry, dict):
                    for key in ("channelHandle", "handle", "channelId", "id", "url"):
                        if key in entry:
                            val = entry[key]
                            if not isinstance(val, str):
                                continue
                            if val.startswith("http"):
                                path = urlparse(val).path  # e.g. /@handle
                                if path.startswith("/@"):
                                    val = path[1:]
                                elif path.startswith("/channel/"):
                                    val = path[len("/channel/"):]
                                else:
                                    val = path
                            elif val.startswith("UC"):
                                pass  # already canonical
                            elif val.startswith("@"):
                                pass  # already canonical
                            channels.append(val)
                            break
        elif isinstance(data, dict):
            for key in data:
                if key.startswith("UC"):
                    channels.append(key)
                elif key.startswith("@"):
                    channels.append(key)
    except json.JSONDecodeError:
        logging.warning("Failed to parse as JSON; falling back to line-by-line text parsing")
        channels = parse_text_blocklist(raw)
    return channels


def fetch_remote(url: str) -> str:
    req = Request(url, headers={"User-Agent": f"yt-dont-recommend/{_get_current_version()}"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {url}: {e}") from e


def resolve_source(source: str, quiet: bool = False) -> list[str]:
    """
    Resolve --source to a list of channel paths. Accepts:
      - A built-in key ("deslop", "aislist")
      - A local file path
      - An HTTP/HTTPS URL

    quiet=True suppresses per-file INFO lines (used when loading the exclude file,
    where the caller logs a single consolidated message instead).
    """
    if source in BUILTIN_SOURCES:
        info = BUILTIN_SOURCES[source]
        logging.info(f"Fetching built-in source '{source}' ({info['name']}): {info['url']}")
        raw = fetch_remote(info["url"])
        channels = parse_text_blocklist(raw) if info["format"] == "text" else parse_json_blocklist(raw)
        logging.info(f"Fetched {len(channels)} channels from {info['name']}")
        return channels

    if source.startswith("http://") or source.startswith("https://"):
        if not quiet:
            logging.info(f"Fetching remote blocklist: {source}")
        raw = fetch_remote(source)
        stripped = raw.lstrip()
        channels = parse_json_blocklist(raw) if stripped.startswith(("{", "[")) else parse_text_blocklist(raw)
        if not quiet:
            logging.info(f"Fetched {len(channels)} channels from {source}")
        return channels

    path = Path(source).expanduser().resolve()
    if not path.exists():
        logging.error(f"File not found: {path}")
        sys.exit(1)
    if not quiet:
        logging.info(f"Reading local blocklist: {path}")
    raw = path.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    channels = parse_json_blocklist(raw) if stripped.startswith(("{", "[")) else parse_text_blocklist(raw)
    if not quiet:
        logging.info(f"Read {len(channels)} channels from {path.name}")
    return channels


def channel_to_url(channel: str) -> str:
    """Convert a canonical channel identifier to a full YouTube URL."""
    if channel.startswith("http"):
        return channel
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}"
    if channel.startswith("UC"):
        return f"https://www.youtube.com/channel/{channel}"
    return f"https://www.youtube.com/{channel}"


# --- Browser Automation ---

def do_login():
    """Open a browser window for the user to log into YouTube."""
    from .browser import do_login as _do_login
    return _do_login()


def check_removals(state: dict, current_channels: list[str],
                   source: str, unblock_policy: str) -> list[str]:
    """
    Compare currently-fetched blocklist against previously-blocked channels.

    If a channel was blocked because of `source` but is no longer in the
    current list, it may be a false positive that the list maintainer corrected.

    unblock_policy:
      "all" — only unblock when the channel has been dropped from every source
               that originally blocked it (conservative, default)
      "any" — unblock as soon as any single source drops the channel

    Modifies state in place. Returns list of channels that should be
    unblocked on YouTube (browser action still required).
    """
    current_set = {c.lower() for c in current_channels}
    blocked_by = state.get("blocked_by", {})
    to_unblock: list[str] = []

    for channel, info in list(blocked_by.items()):
        sources = info.get("sources", [])
        if source not in sources:
            continue
        if channel.lower() in current_set:
            continue

        # This channel was blocked by `source` but is no longer on that list
        other_sources = [s for s in sources if s != source]

        if unblock_policy == "any" or not other_sources:
            # Save to pending_unblock before removing from state, so a failed browser
            # unblock can be retried on the next run without losing the channel.
            state.setdefault("pending_unblock", {})[channel] = info.copy()
            del blocked_by[channel]
            try:
                state["processed"].remove(channel)
            except ValueError:
                pass
            to_unblock.append(channel)
            if other_sources:
                logging.warning(
                    f"*** UNBLOCKING {channel} — dropped from '{source}'. "
                    f"NOTE: still present in {other_sources} but unblocking "
                    f"because --unblock-policy=any."
                )
            else:
                logging.warning(
                    f"*** UNBLOCKING {channel} — removed from '{source}' blocklist "
                    f"(possible false positive correction by list maintainer)."
                )
            save_state(state)
        else:
            # policy == "all" and other sources still assert the block
            info["sources"] = other_sources
            logging.info(
                f"NOTE: {channel} was dropped from '{source}' but is still "
                f"blocked by: {other_sources}. Will unblock when removed from all sources."
            )

    return to_unblock


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


# --- Attention notifications ---

def _desktop_notify(message: str) -> None:
    """Attempt a desktop notification. Fails silently if unavailable."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "yt-dont-recommend"'],
                capture_output=True, timeout=5,
            )
        else:
            subprocess.run(
                ["notify-send", "--urgency=normal", "yt-dont-recommend", message],
                capture_output=True, timeout=5,
            )
    except Exception:
        pass


def _ntfy_notify(topic: str, message: str) -> None:
    """POST a notification to ntfy.sh. Fails silently if unavailable."""
    try:
        req = Request(
            f"https://ntfy.sh/{topic}",
            data=message.encode("utf-8"),
            headers={
                "Title": "yt-dont-recommend",
                "Priority": "high",
                "Tags": "warning",
            },
            method="POST",
        )
        with urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def write_attention(message: str) -> None:
    global _had_attention
    _had_attention = True
    """Record an alert that requires user action.

    Appends a timestamped entry to the attention flag file, attempts a
    desktop notification, and sends an ntfy.sh push notification if
    configured. The flag file persists across runs until cleared with
    --clear-alerts, so unattended (cron/launchd) failures are visible
    the next time the user runs any command.
    """
    ATTENTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().isoformat(timespec="seconds")
    with open(ATTENTION_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
    logging.warning(f"ATTENTION: {message}")
    _desktop_notify(message)
    state = load_state()
    topic = state.get("notify_topic")
    if topic:
        _ntfy_notify(topic, message)


def check_attention_flag() -> None:
    """Print any pending alerts from previous runs, if present."""
    if not ATTENTION_FILE.exists():
        return
    alerts = ATTENTION_FILE.read_text(encoding="utf-8").strip()
    if not alerts:
        ATTENTION_FILE.unlink()
        return
    sep = "=" * 60
    print(f"\n{sep}")
    print("  ACTION REQUIRED — alerts from previous runs:")
    print(sep)
    print(alerts)
    print(sep)
    print("  Run --check-selectors to diagnose selector failures.")
    print("  Run --clear-alerts once the issue is resolved.")
    print(f"{sep}\n")
    if sys.stdin.isatty():
        input("Press Enter to continue...")


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


# --- Schedule management ---

def _format_hours(hours: list[int]) -> str:
    """Convert a list of 24h integers to a readable string, e.g. '3:00 AM and 3:00 PM'."""
    def _fmt(h: int) -> str:
        if h == 0:   return "12:00 AM"
        if h < 12:   return f"{h}:00 AM"
        if h == 12:  return "12:00 PM"
        return f"{h - 12}:00 PM"
    parts = [_fmt(h) for h in sorted(hours)]
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + ", and " + parts[-1]


def _parse_schedule_hours(raw: str) -> list[int]:
    """Parse --schedule-hours input into a sorted list of 24h integers.

    Accepted formats:
      6,18      specific hours (0–23, comma-separated)
      */4       every 4 hours (step 1–23)
      hourly    every hour (alias for */1)

    Raises ValueError with a human-readable message on bad input.
    """
    raw = raw.strip()
    if raw == "hourly":
        return list(range(24))
    if raw.startswith("*/"):
        step = int(raw[2:])
        if step < 1 or step > 23:
            raise ValueError(f"*/N step must be 1–23, got {step!r}")
        return list(range(0, 24, step))
    parsed = sorted(set(int(h.strip()) for h in raw.split(",")))
    if not parsed or not all(0 <= h <= 23 for h in parsed):
        raise ValueError(f"hours must be 0–23, got {raw!r}")
    return parsed


def _find_installed_binary() -> str:
    """Return the absolute path to use for the schedule entry.

    When running as an installed binary (uv tool / pipx), sys.argv[0] is already
    the right answer — just resolve it to an absolute path. Falls back to PATH
    lookup when running in dev mode as 'python yt_dont_recommend.py'.
    """
    argv0 = Path(sys.argv[0]).resolve()
    # Installed binary: no .py extension, file exists
    if argv0.suffix != ".py" and argv0.exists():
        return str(argv0)
    # Dev mode: look for the installed command on PATH
    found = shutil.which("yt-dont-recommend")
    if found:
        return found
    # Last resort: invoke via the current Python interpreter
    return f"{sys.executable} {argv0}"


def _schedule_macos(action: str, bin_path: str, hours: list[int]) -> None:
    plist_path = _LAUNCHD_PLIST

    if action == "status":
        if not plist_path.exists():
            print("No schedule installed.")
            return
        print(f"Installed:  {plist_path}")
        try:
            with open(plist_path, "rb") as f:
                data = plistlib.load(f)
            actual_hours = sorted(e["Hour"] for e in data.get("StartCalendarInterval", []))
            time_str = _format_hours(actual_hours)
        except Exception:
            time_str = "unknown"
        result = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"Status:     loaded (runs at {time_str} daily)")
        else:
            print(f"Status:     plist present but not loaded — try re-running --schedule install")
        return

    if action == "remove":
        if not plist_path.exists():
            print("No schedule to remove.")
            return
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
        print("Schedule removed.")
        return

    # install — idempotent: replace any existing schedule
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
        print("Replacing existing schedule...")

    plist = {
        "Label": _LAUNCHD_LABEL,
        "ProgramArguments": [bin_path, "--headless"],
        "StartCalendarInterval": [
            {"Hour": h, "Minute": 0} for h in hours
        ],
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
        "RunAtLoad": False,
    }
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    print(f"Scheduled to run at {_format_hours(hours)} daily.")
    print(f"Plist: {plist_path}")
    print(f"\nRun logs: {LOG_FILE}")


def _schedule_linux(action: str, bin_path: str, hours: list[int]) -> None:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing_lines = result.stdout.splitlines() if result.returncode == 0 else []
    managed = [l for l in existing_lines if _CRON_MARKER in l]
    other = [l for l in existing_lines if _CRON_MARKER not in l]

    if action == "status":
        if managed:
            print("Scheduled:")
            for line in managed:
                # Parse actual hours from the cron expression for readable output
                try:
                    actual_hours = sorted(int(h) for h in line.split()[1].split(","))
                    print(f"  Runs at {_format_hours(actual_hours)} daily")
                except (IndexError, ValueError):
                    print(f"  {line}")
        else:
            print("No schedule installed.")
        return

    if action == "remove":
        if not managed:
            print("No schedule to remove.")
            return
        new_crontab = "\n".join(other)
        if new_crontab and not new_crontab.endswith("\n"):
            new_crontab += "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
        print("Schedule removed.")
        return

    # install — idempotent: managed lines are already excluded from `other`,
    # so writing the new entry naturally replaces any previous one.
    if managed:
        print("Replacing existing schedule...")

    hours_str = ",".join(str(h) for h in hours)
    cron_line = f"0 {hours_str} * * * {bin_path} --headless >> /dev/null 2>&1  {_CRON_MARKER}"
    new_lines = [l for l in other if l.strip()] + [cron_line]
    new_crontab = "\n".join(new_lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    print(f"Scheduled to run at {_format_hours(hours)} daily.")
    print(f"Entry: {cron_line}")
    print(f"\nRun logs: {LOG_FILE}")
    print("To verify: crontab -l")


def schedule_cmd(action: str, hours: list[int] | None = None) -> None:
    """Install, remove, or show status of the automatic run schedule."""
    bin_path = _find_installed_binary()
    effective_hours = hours if hours is not None else list(_SCHEDULE_HOURS)
    if sys.platform == "darwin":
        _schedule_macos(action, bin_path, effective_hours)
    else:
        _schedule_linux(action, bin_path, effective_hours)


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

    if _had_attention:
        sys.exit(1)


if __name__ == "__main__":
    main()

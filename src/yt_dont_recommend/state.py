"""
State management: load_state, save_state, write_attention, and related helpers.

Imports only from config.py — no imports from blocklist, browser, or scheduler.

Functions that use STATE_FILE look it up through _pkg() so that
monkeypatch.setattr(ydr, "STATE_FILE", ...) works correctly in tests.
"""

import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import TypedDict
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema types
# ---------------------------------------------------------------------------

class StatsDict(TypedDict):
    total_blocked: int
    total_skipped: int
    total_failed: int


class BlockedByEntry(TypedDict, total=False):
    sources: list[str]
    blocked_at: str
    display_name: str | None


class AppState(TypedDict, total=False):
    """Typed representation of the on-disk state file.

    All fields are optional (total=False) for Python 3.10 compatibility.
    Use load_state() to obtain a fully-populated instance with all keys
    guaranteed present. Never rename or remove existing keys — only add new
    ones, with a matching setdefault() in load_state() and a STATE_VERSION bump.
    """
    blocked_by: dict[str, BlockedByEntry]
    would_have_blocked: dict[str, dict]
    last_run: str | None
    stats: StatsDict
    notify_topic: str | None
    last_version_check: str | None
    latest_known_version: str | None
    notified_version: str | None
    auto_upgrade: bool
    previous_version: str | None
    current_version: str | None
    source_sizes: dict[str, int]
    state_version: int
    ucxxx_to_handle: dict[str, str | None]
    pending_unblock: dict[str, dict]
    clickbait_cache: dict[str, dict]
    clickbait_acted: dict[str, dict]

from .config import (
    ATTENTION_FILE,
    CLICKBAIT_ACTED_PRUNE_DAYS,
    STATE_VERSION,
)
from .config import (
    STATE_FILE as _CONFIG_STATE_FILE,
)

# Set to True by write_attention() so main() can exit with code 1 when
# something serious enough to alert the user occurred during the run.
_had_attention = False


def _pkg():
    """Late import of yt_dont_recommend to get live-patched attributes (e.g. STATE_FILE in tests)."""
    import yt_dont_recommend as _p
    return _p


def _state_file():
    """Return the current STATE_FILE path, respecting any monkeypatch in tests."""
    try:
        return _pkg().STATE_FILE
    except Exception:
        return _CONFIG_STATE_FILE


# --- State Management ---

def load_state() -> AppState:
    STATE_FILE = _state_file()
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
        s.setdefault("clickbait_cache", {})
        s.setdefault("clickbait_acted", {})
        # Prune clickbait_acted entries older than CLICKBAIT_ACTED_PRUNE_DAYS
        _prune_cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=CLICKBAIT_ACTED_PRUNE_DAYS)).isoformat()
        s["clickbait_acted"] = {
            vid: entry for vid, entry in s["clickbait_acted"].items()
            if entry.get("acted_at", "") >= _prune_cutoff
        }
        # Schema version guard — warn if state was written by a newer binary
        file_sv = s.get("state_version", 0)
        if file_sv > STATE_VERSION:
            log.warning(
                f"State file was written by a newer version of yt-dont-recommend "
                f"(schema v{file_sv}, this binary expects v{STATE_VERSION}). "
                "Some state fields may be ignored. Upgrade to restore full functionality."
            )
        s.setdefault("state_version", STATE_VERSION)
        # v2 migration: drop redundant "processed" list (blocked_by.keys() is authoritative)
        if "processed" in s:
            log.info(
                f"State migrated to v2: removed legacy 'processed' list "
                f"({len(s['processed'])} entries); blocked_by is now authoritative."
            )
            del s["processed"]
            # Persist immediately so repeated load_state() calls don't re-trigger.
            try:
                with open(STATE_FILE, "w") as f:
                    json.dump(s, f, indent=2)
            except Exception:
                pass  # non-critical; will persist on next save_state()
        return s
    return {
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
        "clickbait_cache": {},
        "clickbait_acted": {},
        "state_version": STATE_VERSION,
    }


def save_state(state: AppState) -> None:
    STATE_FILE = _state_file()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        from .config import ensure_data_dir
        ensure_data_dir()
    except Exception:
        pass  # permission fix is best-effort; don't block state save
    state["last_run"] = datetime.now().isoformat()
    # Don't leave empty pending_unblock in the state file
    if "pending_unblock" in state and not state["pending_unblock"]:
        del state["pending_unblock"]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# --- Attention notifications ---

def _escape_applescript(s: str) -> str:
    # Escape per the AppleScript Language Guide double-quoted-string rules.
    # Backslash must be handled first so newly inserted backslashes from the
    # other substitutions are not re-escaped.
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
    )


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
        log.debug("ntfy notification sent to topic %s", topic)
    except Exception as exc:
        log.debug("ntfy notification failed (topic %s): %s", topic, exc)


def write_attention(message: str) -> None:
    """Record an alert that requires user action.

    Appends a timestamped entry to the attention flag file, attempts a
    desktop notification, and sends an ntfy.sh push notification if
    configured. The flag file persists across runs until cleared with
    --clear-alerts, so unattended (cron/launchd) failures are visible
    the next time the user runs any command.
    """
    global _had_attention
    _had_attention = True
    ATTENTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        from .config import ensure_data_dir
        ensure_data_dir()
    except Exception:
        pass
    timestamp = datetime.now().isoformat(timespec="seconds")
    with open(ATTENTION_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
    log.warning(f"ATTENTION: {message}")
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

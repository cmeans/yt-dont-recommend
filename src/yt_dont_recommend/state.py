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
from datetime import datetime
from urllib.request import urlopen, Request

from .config import (
    STATE_FILE as _CONFIG_STATE_FILE,
    ATTENTION_FILE,
    STATE_VERSION,
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

def load_state() -> dict:
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
    STATE_FILE = _state_file()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now().isoformat()
    # Don't leave empty pending_unblock in the state file
    if "pending_unblock" in state and not state["pending_unblock"]:
        del state["pending_unblock"]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


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

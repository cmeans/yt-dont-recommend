"""
Schedule management: _parse_schedule_hours, schedule_cmd, and related helpers.

Imports only from config.py for constants.

schedule_cmd calls _schedule_linux/_schedule_macos via _pkg() so that
monkeypatch.setattr(ydr, "_schedule_linux", ...) works correctly in tests.
"""

import plistlib
import subprocess
import sys
from pathlib import Path

from .config import (
    LOG_FILE,
    _SCHEDULE_HOURS,
    _LAUNCHD_LABEL,
    _LAUNCHD_PLIST,
    _CRON_MARKER,
)


def _pkg():
    """Late import of yt_dont_recommend to get live-patched attributes in tests."""
    import yt_dont_recommend as _p
    return _p


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
    import shutil
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
    """Install, remove, or show status of the automatic run schedule.

    Calls _schedule_linux/_schedule_macos via _pkg() so that
    monkeypatch.setattr(ydr, "_schedule_linux", ...) is intercepted correctly.
    """
    pkg = _pkg()
    bin_path = pkg._find_installed_binary()
    effective_hours = hours if hours is not None else list(_SCHEDULE_HOURS)
    if sys.platform == "darwin":
        pkg._schedule_macos(action, bin_path, effective_hours)
    else:
        pkg._schedule_linux(action, bin_path, effective_hours)

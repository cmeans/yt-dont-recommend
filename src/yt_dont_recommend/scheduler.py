"""
Schedule management: install/remove/status for the every-minute heartbeat,
daily window computation, and the heartbeat() shim itself.

The scheduler installs a minimal every-minute cron (Linux) or launchd
(macOS) entry that runs:
    yt-dont-recommend --heartbeat

The heartbeat shim is intentionally fast: reads schedule.json (pure
stdlib, no Playwright, no heavy imports), checks whether any mode is
due, spawns the full process if so, and exits immediately.

All timestamps are UTC (Zulu). String comparison on zero-padded "HH:MM"
is lexicographically correct and avoids datetime parsing overhead in the
hot path.

schedule.json schema
--------------------
See CLAUDE.md § Schedule JSON Schema for the canonical definition.

    {
        "modes": {
            "blocklist": {"runs_per_day": 2},
            "clickbait": {"runs_per_day": 4}
        },
        "headless": true,
        "installed_at": "2026-03-11T14:00:00+00:00",
        "today": {
            "date": "2026-03-11",          // UTC date string
            "blocklist": {
                "planned_utc": ["03:17", "15:44"],   // HH:MM, sorted
                "executed_utc": ["03:17"]             // times already fired
            },
            "clickbait": {
                "planned_utc": ["01:12", "07:33", "13:44", "20:01"],
                "executed_utc": ["01:12", "07:33"]
            }
        }
    }

Key behaviours
--------------
- planned_utc is recomputed fresh each UTC day, giving different run
  times every day (jitter by design).
- A mode is "due" when any planned time <= now_hhmm and that time is
  not yet in executed_utc.
- Modes that are simultaneously due are combined into one subprocess
  invocation (one browser session, not two).
- executed_utc is written before the subprocess is spawned. If the
  spawn fails, that slot is silently skipped — it will not retry on
  the next heartbeat tick for the same slot.
- schedule.json is written atomically (write to .tmp, rename).
"""

import json
import logging
import random
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    _CRON_MARKER,
    _LAUNCHD_LABEL,
    _LAUNCHD_PLIST,
    ATTENTION_FILE,
    LOG_FILE,
    SCHEDULE_FILE,
)

log = logging.getLogger(__name__)


def _pkg():
    """Late import of yt_dont_recommend to get live-patched attributes in tests."""
    import yt_dont_recommend as _p
    return _p


# ---------------------------------------------------------------------------
# schedule.json I/O
# ---------------------------------------------------------------------------

def load_schedule() -> dict:
    """Load schedule.json. Returns {} if the file doesn't exist or is corrupt."""
    if not SCHEDULE_FILE.exists():
        return {}
    try:
        return json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_schedule(data: dict) -> None:
    """Write schedule.json atomically (write to .tmp, then rename)."""
    from .config import ensure_data_dir
    ensure_data_dir()
    tmp = SCHEDULE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(SCHEDULE_FILE)


# ---------------------------------------------------------------------------
# Daily window computation
# ---------------------------------------------------------------------------

def _compute_daily_plan(runs_per_day: int) -> list:
    """Divide 24 hours into *runs_per_day* equal windows and pick a random
    UTC minute within each window.

    Returns a sorted list of zero-padded "HH:MM" strings. The values differ
    on every call — this is the jitter mechanism. Storing the result in
    schedule.json commits to those times for the rest of the day.

    Example (runs_per_day=2):
        Window 0: [00:00, 12:00)  →  e.g. "03:17"
        Window 1: [12:00, 24:00)  →  e.g. "15:44"
        Returns: ["03:17", "15:44"]
    """
    if runs_per_day <= 0:
        return []
    window_minutes = (24 * 60) // runs_per_day
    result = []
    for i in range(runs_per_day):
        offset = random.randint(0, window_minutes - 1)
        total = i * window_minutes + offset
        result.append(f"{total // 60:02d}:{total % 60:02d}")
    return result


# ---------------------------------------------------------------------------
# Heartbeat shim
# ---------------------------------------------------------------------------

def heartbeat() -> None:
    """Fast-exit scheduler shim invoked every minute by cron/launchd.

    Steps:
      1. Read schedule.json. Exit immediately if absent (nothing installed).
      2. If the stored ``today.date`` differs from the current UTC date,
         recompute planned_utc for every mode and reset executed_utc lists.
      3. Find modes whose next unexecuted planned time <= current UTC HH:MM.
      4. Mark those times as executed and persist schedule.json.
      5. Spawn one subprocess combining all due modes into a single
         yt-dont-recommend invocation (one browser session).

    No Playwright, no package-level imports. All I/O is schedule.json only.
    """
    # Refuse to spawn if an attention flag is set — protects the account
    # from continued automation after a shadow-limiting detection or other
    # critical alert. Clear with --clear-alerts to re-enable scheduling.
    if ATTENTION_FILE.exists():
        log.warning(
            "Scheduled run skipped — attention flag is set. "
            "Run --clear-alerts once the issue is resolved to resume scheduling."
        )
        return

    schedule = load_schedule()
    if not schedule:
        return

    now       = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    now_hhmm  = now.strftime("%H:%M")

    # Recompute daily plan at UTC day boundary
    today_plan = schedule.get("today", {})
    if today_plan.get("date") != today_str:
        modes_cfg  = schedule.get("modes", {})
        new_today: dict = {"date": today_str}
        for mode, cfg in modes_cfg.items():
            rpd = cfg.get("runs_per_day", 0)
            new_today[mode] = {
                "planned_utc":  _compute_daily_plan(rpd),
                "executed_utc": [],
            }
        schedule["today"] = new_today
        today_plan = new_today
        save_schedule(schedule)

    # Find which modes are due (first unexecuted planned time <= now)
    due_modes: list = []
    for mode in schedule.get("modes", {}):
        mode_plan = today_plan.get(mode, {})
        planned   = mode_plan.get("planned_utc", [])
        executed  = set(mode_plan.get("executed_utc", []))
        for t in planned:
            if t <= now_hhmm and t not in executed:
                due_modes.append(mode)
                break

    if not due_modes:
        return

    # Mark all past-due unexecuted slots as executed before spawning.
    # Coalescing protects against the catch-up storm where a machine wakes
    # from sleep with N past-due slots and fires N spawns on N consecutive
    # heartbeats (issue #17). One heartbeat → one spawn, regardless of how
    # many stale slots accumulated.
    for mode in due_modes:
        mode_plan = today_plan.setdefault(mode, {})
        planned   = mode_plan.get("planned_utc", [])
        executed  = mode_plan.setdefault("executed_utc", [])
        coalesced = []
        for t in planned:
            if t <= now_hhmm and t not in executed:
                executed.append(t)
                coalesced.append(t)
        if len(coalesced) > 1:
            log.info(
                "Catching up %s: coalesced %d stale slots (%s) into one run",
                mode, len(coalesced), ", ".join(coalesced),
            )
    save_schedule(schedule)

    # Build and spawn — due modes combined into one invocation
    bin_path = str(Path(sys.argv[0]).resolve())
    cmd = [bin_path]
    if "blocklist" in due_modes:
        cmd.append("--blocklist")
    if "clickbait" in due_modes:
        cmd.append("--clickbait")
    if schedule.get("headless", True):
        cmd.append("--headless")
    try:
        subprocess.Popen(cmd)
    except Exception:
        pass  # best-effort; slot already marked executed, won't retry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_installed_binary() -> str:
    """Return the absolute path to use for the schedule entry.

    When running as an installed binary (uv tool / pipx), sys.argv[0] is
    already the right answer. Falls back to PATH lookup for dev mode.
    """
    import shutil
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.suffix != ".py" and argv0.exists():
        return str(argv0)
    found = shutil.which("yt-dont-recommend")
    if found:
        return found
    return f"{sys.executable} {argv0}"


def _modes_summary(schedule: dict) -> str:
    """One-line summary of configured modes for status display.

    Example: "blocklist: 2x/day, clickbait: 4x/day"
    """
    modes = schedule.get("modes", {})
    if not modes:
        return "none configured"
    parts = []
    for mode, cfg in modes.items():
        rpd = cfg.get("runs_per_day", 0)
        parts.append(f"{mode}: {rpd}x/day")
    return ", ".join(parts)


def _print_today_plan(schedule: dict) -> None:
    """Print today's planned and executed run times per mode."""
    today = schedule.get("today", {})
    if not today.get("date"):
        return
    print(f"  Today ({today['date']} UTC):")
    for mode in schedule.get("modes", {}):
        mode_plan = today.get(mode, {})
        planned   = mode_plan.get("planned_utc", [])
        executed  = set(mode_plan.get("executed_utc", []))
        if not planned:
            print(f"    {mode}: no runs scheduled today")
            continue
        marks = [f"{t} \u2713" if t in executed else f"{t} pending" for t in planned]
        print(f"    {mode}: {', '.join(marks)}")


# ---------------------------------------------------------------------------
# Platform: macOS (launchd)
# ---------------------------------------------------------------------------

def _schedule_macos(action: str, bin_path: str, schedule: dict) -> None:
    """Install, remove, or show status of the launchd heartbeat agent.

    *schedule* is only used for the install action (written to schedule.json
    and embedded in the plist). For status/remove it is ignored.
    """
    import plistlib
    plist_path = _LAUNCHD_PLIST

    if action == "status":
        if not plist_path.exists():
            print("No schedule installed.")
            return
        sched = load_schedule()
        result = subprocess.run(
            ["launchctl", "list", _LAUNCHD_LABEL],
            capture_output=True, text=True,
        )
        status = (
            "loaded"
            if result.returncode == 0
            else "plist present but not loaded — re-run --schedule install"
        )
        print(f"Installed:  {plist_path}")
        print(f"Status:     {status}")
        print("Heartbeat:  every minute")
        if sched:
            print(f"Modes:      {_modes_summary(sched)}")
            print(f"Headless:   {'yes' if sched.get('headless', True) else 'no'}")
            _print_today_plan(sched)
        return

    if action == "remove":
        if not plist_path.exists():
            print("No schedule to remove.")
            return
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
        print("Schedule removed.")
        print(f"(schedule.json kept at {SCHEDULE_FILE} — delete manually if desired)")
        return

    # install — replace any existing schedule
    if plist_path.exists():
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False)
        plist_path.unlink()
        print("Replacing existing schedule...")

    plist = {
        "Label":            _LAUNCHD_LABEL,
        "ProgramArguments": [bin_path, "--heartbeat"],
        "StartInterval":    60,
        "StandardOutPath":  "/dev/null",
        "StandardErrorPath": "/dev/null",
        "RunAtLoad":        False,
    }
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump(plist, f)
    subprocess.run(["launchctl", "load", str(plist_path)], check=True)
    save_schedule(schedule)
    print("Schedule installed. Heartbeat: every minute.")
    print(f"Modes:      {_modes_summary(schedule)}")
    print(f"Plist:      {plist_path}")
    print(f"Logs:       {LOG_FILE}")


# ---------------------------------------------------------------------------
# Platform: Linux (cron)
# ---------------------------------------------------------------------------

def _schedule_linux(action: str, bin_path: str, schedule: dict) -> None:
    """Install, remove, or show status of the cron heartbeat entry.

    *schedule* is only used for the install action (written to schedule.json).
    For status/remove it is ignored — current config is read from schedule.json.
    """
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout.splitlines() if result.returncode == 0 else []
    managed  = [line for line in existing if _CRON_MARKER in line]
    other    = [line for line in existing if _CRON_MARKER not in line]

    if action == "status":
        if not managed:
            print("No schedule installed.")
            return
        sched = load_schedule()
        print("Installed:  crontab entry present")
        print("Heartbeat:  every minute")
        if sched:
            print(f"Modes:      {_modes_summary(sched)}")
            print(f"Headless:   {'yes' if sched.get('headless', True) else 'no'}")
            _print_today_plan(sched)
        return

    if action == "remove":
        if not managed:
            print("No schedule to remove.")
            return
        new_crontab = "\n".join(line for line in other if line.strip())
        if new_crontab and not new_crontab.endswith("\n"):
            new_crontab += "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
        print("Schedule removed.")
        print(f"(schedule.json kept at {SCHEDULE_FILE} — delete manually if desired)")
        return

    # install — managed lines already excluded from `other`, naturally replaces
    if managed:
        print("Replacing existing schedule...")
    cron_line = (
        f"* * * * * {bin_path} --heartbeat >> /dev/null 2>&1  {_CRON_MARKER}"
    )
    new_lines   = [line for line in other if line.strip()] + [cron_line]
    new_crontab = "\n".join(new_lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
    save_schedule(schedule)
    print("Schedule installed. Heartbeat: every minute.")
    print(f"Modes:      {_modes_summary(schedule)}")
    print(f"Entry:      {cron_line}")
    print(f"Logs:       {LOG_FILE}")
    print("To verify:  crontab -l")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def schedule_cmd(action: str,
                 blocklist_runs: int = 0,
                 clickbait_runs: int = 0) -> None:
    """Install, remove, or show status of the automatic run schedule.

    For ``install``: *blocklist_runs* and *clickbait_runs* must already be
    validated by the caller (at least one > 0). The headless flag is read
    from ``load_schedule_config()`` (config.yaml ``schedule.headless``).

    Delegates to ``_schedule_macos`` or ``_schedule_linux`` via ``_pkg()``
    so that ``monkeypatch.setattr(ydr, "_schedule_linux", ...)`` is
    intercepted correctly in tests.
    """
    from .config import load_schedule_config
    pkg      = _pkg()
    bin_path = pkg._find_installed_binary()

    if action == "install":
        cfg      = load_schedule_config()
        headless = cfg.get("headless", True)
        schedule: dict = {
            "modes":        {},
            "headless":     headless,
            "installed_at": datetime.now(timezone.utc).isoformat(),
            "today":        {},
        }
        if blocklist_runs > 0:
            schedule["modes"]["blocklist"] = {"runs_per_day": blocklist_runs}
        if clickbait_runs > 0:
            schedule["modes"]["clickbait"] = {"runs_per_day": clickbait_runs}
        if sys.platform == "darwin":
            pkg._schedule_macos(action, bin_path, schedule)
        else:
            pkg._schedule_linux(action, bin_path, schedule)
    else:
        # status / remove — schedule param unused by platform functions
        if sys.platform == "darwin":
            pkg._schedule_macos(action, bin_path, {})
        else:
            pkg._schedule_linux(action, bin_path, {})

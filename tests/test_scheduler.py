"""
Tests for yt_dont_recommend.scheduler — _parse_schedule_hours, schedule_cmd,
_format_hours, and schedule helpers.

Functions under test are imported directly from yt_dont_recommend.scheduler, but
patch targets remain yt_dont_recommend.X (the re-exported name in __init__.py),
as they did in the original test_yt_dont_recommend.py.
"""

import pytest
from unittest.mock import patch

import yt_dont_recommend as ydr
from yt_dont_recommend.scheduler import (
    _parse_schedule_hours,
    _format_hours,
    _schedule_linux,
    schedule_cmd,
)


# ---------------------------------------------------------------------------
# Schedule: custom hours, idempotent install, format_hours
# ---------------------------------------------------------------------------

class TestSchedule:
    def test_format_hours_am_pm(self):
        assert ydr._format_hours([3, 15]) == "3:00 AM and 3:00 PM"

    def test_format_hours_midnight(self):
        assert ydr._format_hours([0]) == "12:00 AM"

    def test_format_hours_noon(self):
        assert ydr._format_hours([12]) == "12:00 PM"

    def test_format_hours_three_values(self):
        result = ydr._format_hours([6, 12, 18])
        assert "6:00 AM" in result
        assert "12:00 PM" in result
        assert "6:00 PM" in result

    def test_format_hours_sorted(self):
        # Should sort regardless of input order
        assert ydr._format_hours([15, 3]) == "3:00 AM and 3:00 PM"

    def test_schedule_cmd_uses_default_hours(self, monkeypatch):
        import yt_dont_recommend.scheduler as sched_mod
        called_with = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/usr/bin/yt-dont-recommend")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/usr/bin/yt-dont-recommend")
        monkeypatch.setattr(ydr, "_schedule_linux", lambda a, b, h: called_with.append(h))
        monkeypatch.setattr(sched_mod, "_schedule_linux", lambda a, b, h: called_with.append(h))
        monkeypatch.setattr(sched_mod.sys, "platform", "linux")
        ydr.schedule_cmd("install")
        assert called_with[0] == list(ydr._SCHEDULE_HOURS)

    def test_schedule_cmd_passes_custom_hours(self, monkeypatch):
        import yt_dont_recommend.scheduler as sched_mod
        called_with = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/usr/bin/yt-dont-recommend")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/usr/bin/yt-dont-recommend")
        monkeypatch.setattr(ydr, "_schedule_linux", lambda a, b, h: called_with.append(h))
        monkeypatch.setattr(sched_mod, "_schedule_linux", lambda a, b, h: called_with.append(h))
        monkeypatch.setattr(sched_mod.sys, "platform", "linux")
        ydr.schedule_cmd("install", hours=[6, 18])
        assert called_with[0] == [6, 18]

    def test_schedule_linux_install_replaces_existing(self, monkeypatch, capsys):
        """Re-running install should replace the existing entry, not bail."""
        runs = []
        existing = f"0 3,15 * * * /bin/yt-dont-recommend --headless  {ydr._CRON_MARKER}"

        def fake_run(cmd, **kw):
            runs.append(cmd)
            if cmd == ["crontab", "-l"]:
                return type("R", (), {"returncode": 0, "stdout": existing})()
            return type("R", (), {"returncode": 0, "stdout": ""})()

        import yt_dont_recommend.scheduler as sched_mod
        monkeypatch.setattr(sched_mod.subprocess, "run", fake_run)
        ydr._schedule_linux("install", "/bin/yt-dont-recommend", [6, 18])
        captured = capsys.readouterr()
        assert "Replacing" in captured.out
        # New cron entry should use the new hours
        written = next(r for r in runs if r[0] == "crontab" and len(r) > 1 and r[1] == "-")
        assert written is not None

    # --- _parse_schedule_hours ---

    def test_parse_schedule_hours_comma(self):
        assert ydr._parse_schedule_hours("6,18") == [6, 18]

    def test_parse_schedule_hours_dedupes_and_sorts(self):
        assert ydr._parse_schedule_hours("18,6,6") == [6, 18]

    def test_parse_schedule_hours_single(self):
        assert ydr._parse_schedule_hours("3") == [3]

    def test_parse_schedule_hours_hourly(self):
        assert ydr._parse_schedule_hours("hourly") == list(range(24))

    def test_parse_schedule_hours_step_4(self):
        assert ydr._parse_schedule_hours("*/4") == [0, 4, 8, 12, 16, 20]

    def test_parse_schedule_hours_step_1(self):
        assert ydr._parse_schedule_hours("*/1") == list(range(24))

    def test_parse_schedule_hours_step_8(self):
        assert ydr._parse_schedule_hours("*/8") == [0, 8, 16]

    def test_parse_schedule_hours_invalid_step_0(self):
        with pytest.raises(ValueError):
            ydr._parse_schedule_hours("*/0")

    def test_parse_schedule_hours_invalid_step_24(self):
        with pytest.raises(ValueError):
            ydr._parse_schedule_hours("*/24")

    def test_parse_schedule_hours_out_of_range(self):
        with pytest.raises(ValueError):
            ydr._parse_schedule_hours("6,25")

    def test_parse_schedule_hours_negative(self):
        with pytest.raises((ValueError, Exception)):
            ydr._parse_schedule_hours("-1")

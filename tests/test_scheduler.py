"""
Tests for yt_dont_recommend.scheduler.

Covers:
  - _compute_daily_plan: window count, format, boundary values
  - load_schedule / save_schedule: round-trip, missing file, corrupt file
  - heartbeat: day-boundary recompute, due detection, execute marking,
               subprocess spawn, already-executed guard
  - schedule_cmd / _schedule_linux: install, remove, status
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import yt_dont_recommend as ydr
from yt_dont_recommend.scheduler import (
    _compute_daily_plan,
    _schedule_linux,
    heartbeat,
    load_schedule,
    save_schedule,
    schedule_cmd,
)

# ---------------------------------------------------------------------------
# _compute_daily_plan
# ---------------------------------------------------------------------------

class TestComputeDailyPlan:
    def test_zero_returns_empty(self):
        assert _compute_daily_plan(0) == []

    def test_negative_returns_empty(self):
        assert _compute_daily_plan(-1) == []

    def test_count_matches_runs_per_day(self):
        for n in (1, 2, 4, 6):
            assert len(_compute_daily_plan(n)) == n

    def test_format_is_hhmm(self):
        for t in _compute_daily_plan(6):
            assert len(t) == 5
            assert t[2] == ":"
            h, m = int(t[:2]), int(t[3:])
            assert 0 <= h <= 23
            assert 0 <= m <= 59

    def test_sorted_order(self):
        plan = _compute_daily_plan(4)
        assert plan == sorted(plan)

    def test_two_windows_straddle_noon(self):
        for _ in range(10):
            plan = _compute_daily_plan(2)
            assert plan[0] < "12:00"
            assert plan[1] >= "12:00"

    def test_single_run_in_range(self):
        for _ in range(10):
            t = _compute_daily_plan(1)[0]
            h, m = int(t[:2]), int(t[3:])
            assert 0 <= h * 60 + m < 24 * 60


# ---------------------------------------------------------------------------
# load_schedule / save_schedule
# ---------------------------------------------------------------------------

class TestScheduleIO:
    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "yt_dont_recommend.scheduler.SCHEDULE_FILE",
            tmp_path / "schedule.json",
        )
        assert load_schedule() == {}

    def test_round_trip(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        data = {"modes": {"blocklist": {"runs_per_day": 2}}, "headless": True}
        save_schedule(data)
        assert sf.exists()
        assert load_schedule() == data

    def test_corrupt_file_returns_empty(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        sf.write_text("NOT JSON", encoding="utf-8")
        assert load_schedule() == {}

    def test_atomic_write_no_tmp_left(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule({"x": 1})
        assert not (tmp_path / "schedule.tmp").exists()
        assert sf.exists()


# ---------------------------------------------------------------------------
# heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def _make_schedule(self, today_str, blocklist_planned, blocklist_executed=None):
        return {
            "modes": {"blocklist": {"runs_per_day": 2}},
            "headless": True,
            "today": {
                "date": today_str,
                "blocklist": {
                    "planned_utc":  blocklist_planned,
                    "executed_utc": blocklist_executed or [],
                },
            },
        }

    def _mock_now(self, date_str, hhmm_str):
        mock = MagicMock()
        mock.strftime = lambda fmt: date_str if "%Y" in fmt else hhmm_str
        return mock

    def test_no_schedule_file_exits_silently(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "yt_dont_recommend.scheduler.SCHEDULE_FILE",
            tmp_path / "schedule.json",
        )
        spawned = []
        with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                   side_effect=lambda cmd: spawned.append(cmd)):
            heartbeat()
        assert spawned == []

    def test_nothing_due_does_not_spawn(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule("2026-03-11", ["23:59"]))

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "08:00")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()
        assert spawned == []

    def test_due_mode_spawns_subprocess(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule("2026-03-11", ["03:17"]))

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:18")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()
        assert len(spawned) == 1
        assert "--blocklist" in spawned[0]
        assert "--headless" in spawned[0]

    def test_already_executed_does_not_spawn(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule("2026-03-11", ["03:17"], ["03:17"]))

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:18")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()
        assert spawned == []

    def test_executed_time_persisted(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule("2026-03-11", ["03:17"]))

        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:18")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen"):
                heartbeat()

        updated = load_schedule()
        assert "03:17" in updated["today"]["blocklist"]["executed_utc"]

    def test_new_day_recomputes_plan(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        # yesterday's data
        save_schedule(self._make_schedule("2026-03-10", ["03:17"], ["03:17"]))

        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "00:01")
            with patch("yt_dont_recommend.scheduler._compute_daily_plan",
                       return_value=["04:00", "16:00"]):
                with patch("yt_dont_recommend.scheduler.subprocess.Popen"):
                    heartbeat()

        updated = load_schedule()
        assert updated["today"]["date"] == "2026-03-11"
        assert updated["today"]["blocklist"]["planned_utc"] == ["04:00", "16:00"]
        assert updated["today"]["blocklist"]["executed_utc"] == []

    def test_two_modes_combined_into_one_spawn(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        schedule = {
            "modes": {
                "blocklist": {"runs_per_day": 2},
                "clickbait": {"runs_per_day": 4},
            },
            "headless": True,
            "today": {
                "date": "2026-03-11",
                "blocklist": {"planned_utc": ["03:17"], "executed_utc": []},
                "clickbait": {"planned_utc": ["03:00"], "executed_utc": []},
            },
        }
        save_schedule(schedule)

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:20")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()

        assert len(spawned) == 1
        assert "--blocklist" in spawned[0]
        assert "--clickbait" in spawned[0]

    def test_spawn_failure_does_not_raise(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule("2026-03-11", ["03:17"]))

        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:18")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=FileNotFoundError("not found")):
                heartbeat()  # must not raise

    def test_attention_flag_blocks_spawn(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        attention_file = tmp_path / "needs-attention.txt"
        attention_file.write_text("[2026-03-12T10:00:00] Shadow-limiting suspected\n")
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        monkeypatch.setattr("yt_dont_recommend.scheduler.ATTENTION_FILE", attention_file)
        save_schedule(self._make_schedule("2026-03-11", ["03:17"]))

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:18")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()
        assert spawned == [], "heartbeat must not spawn when attention flag is set"

    def test_no_attention_flag_spawns_normally(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        attention_file = tmp_path / "needs-attention.txt"
        # File does NOT exist — no attention flag
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        monkeypatch.setattr("yt_dont_recommend.scheduler.ATTENTION_FILE", attention_file)
        save_schedule(self._make_schedule("2026-03-11", ["03:17"]))

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "03:18")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()
        assert len(spawned) == 1, "heartbeat must spawn when no attention flag"

    def test_multi_stale_slots_no_consecutive_spawn_storm(self, tmp_path, monkeypatch):
        """Catch-up scenario: machine wakes with 3 past-due slots, then
        cron fires heartbeat on 3 consecutive minutes. Expect a single spawn
        total, not one per tick (the catch-up storm bug from issue #17)."""
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule(
            "2026-03-11", ["03:17", "09:45", "15:22"]
        ))

        spawned = []
        tick_times = ["15:30", "15:31", "15:32"]
        with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                   side_effect=lambda cmd: spawned.append(cmd)):
            for hhmm in tick_times:
                with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
                    mock_dt.now.return_value = self._mock_now("2026-03-11", hhmm)
                    heartbeat()

        assert len(spawned) == 1, (
            f"expected 1 spawn after 3 consecutive heartbeats, got {len(spawned)} "
            "(catch-up storm — issue #17)"
        )

    def test_multi_stale_slots_all_marked_executed(self, tmp_path, monkeypatch):
        """All past-due slots must land in executed_utc after one heartbeat."""
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule(self._make_schedule(
            "2026-03-11", ["03:17", "09:45", "15:22"]
        ))

        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "15:30")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen"):
                heartbeat()

        executed = load_schedule()["today"]["blocklist"]["executed_utc"]
        assert set(executed) == {"03:17", "09:45", "15:22"}, (
            f"expected all 3 past-due slots marked executed, got {executed}"
        )

    def test_multi_stale_slots_coalesce_across_modes(self, tmp_path, monkeypatch):
        """Both modes with multiple stale slots: still exactly one spawn."""
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        schedule = {
            "modes": {
                "blocklist": {"runs_per_day": 2},
                "clickbait": {"runs_per_day": 4},
            },
            "headless": True,
            "today": {
                "date": "2026-03-11",
                "blocklist": {
                    "planned_utc":  ["03:17", "15:22"],
                    "executed_utc": [],
                },
                "clickbait": {
                    "planned_utc":  ["01:00", "07:30", "13:00"],
                    "executed_utc": [],
                },
            },
        }
        save_schedule(schedule)

        spawned = []
        with patch("yt_dont_recommend.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = self._mock_now("2026-03-11", "15:30")
            with patch("yt_dont_recommend.scheduler.subprocess.Popen",
                       side_effect=lambda cmd: spawned.append(cmd)):
                heartbeat()

        assert len(spawned) == 1
        assert "--blocklist" in spawned[0]
        assert "--clickbait" in spawned[0]
        updated = load_schedule()
        assert set(updated["today"]["blocklist"]["executed_utc"]) == {
            "03:17", "15:22",
        }
        assert set(updated["today"]["clickbait"]["executed_utc"]) == {
            "01:00", "07:30", "13:00",
        }


# ---------------------------------------------------------------------------
# _schedule_linux
# ---------------------------------------------------------------------------

class TestScheduleLinux:
    def _fake_run_factory(self, existing_crontab=""):
        written_inputs = []

        def _run(cmd, **kw):
            if cmd == ["crontab", "-l"]:
                rc = 0 if existing_crontab else 1
                return type("R", (), {"returncode": rc, "stdout": existing_crontab})()
            if cmd[0] == "crontab" and len(cmd) > 1 and cmd[1] == "-":
                written_inputs.append(kw.get("input", ""))
            return type("R", (), {"returncode": 0, "stdout": ""})()

        return written_inputs, _run

    def test_install_every_minute_entry(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        import yt_dont_recommend.scheduler as sched_mod
        written, fake = self._fake_run_factory()
        monkeypatch.setattr(sched_mod.subprocess, "run", fake)

        schedule = {"modes": {"blocklist": {"runs_per_day": 2}},
                    "headless": True, "today": {}, "installed_at": "now"}
        _schedule_linux("install", "/bin/yt-dont-recommend", schedule)

        assert written, "crontab - was never called"
        cron_lines = [line for line in written[0].splitlines() if "yt-dont-recommend" in line]
        assert cron_lines, "no crontab entry written"
        assert cron_lines[0].startswith("* * * * *"), f"Not every-minute: {cron_lines[0]}"
        assert "--heartbeat" in cron_lines[0]

    def test_install_replaces_existing(self, tmp_path, monkeypatch, capsys):
        existing = f"* * * * * /old/ydr --heartbeat >> /dev/null 2>&1  {ydr._CRON_MARKER}"
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        import yt_dont_recommend.scheduler as sched_mod
        _, fake = self._fake_run_factory(existing)
        monkeypatch.setattr(sched_mod.subprocess, "run", fake)

        schedule = {"modes": {"blocklist": {"runs_per_day": 2}},
                    "headless": True, "today": {}, "installed_at": "now"}
        _schedule_linux("install", "/bin/yt-dont-recommend", schedule)
        assert "Replacing" in capsys.readouterr().out

    def test_remove_clears_entry(self, tmp_path, monkeypatch, capsys):
        existing = f"* * * * * /bin/ydr --heartbeat >> /dev/null 2>&1  {ydr._CRON_MARKER}"
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        import yt_dont_recommend.scheduler as sched_mod
        _, fake = self._fake_run_factory(existing)
        monkeypatch.setattr(sched_mod.subprocess, "run", fake)

        _schedule_linux("remove", "/bin/yt-dont-recommend", {})
        assert "removed" in capsys.readouterr().out.lower()

    def test_status_no_schedule(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        import yt_dont_recommend.scheduler as sched_mod
        _, fake = self._fake_run_factory()
        monkeypatch.setattr(sched_mod.subprocess, "run", fake)

        _schedule_linux("status", "/bin/yt-dont-recommend", {})
        assert "No schedule" in capsys.readouterr().out

    def test_status_shows_modes(self, tmp_path, monkeypatch, capsys):
        existing = f"* * * * * /bin/ydr --heartbeat  {ydr._CRON_MARKER}"
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", sf)
        save_schedule({"modes": {"blocklist": {"runs_per_day": 3}},
                       "headless": True, "today": {}})
        import yt_dont_recommend.scheduler as sched_mod
        _, fake = self._fake_run_factory(existing)
        monkeypatch.setattr(sched_mod.subprocess, "run", fake)

        _schedule_linux("status", "/bin/yt-dont-recommend", {})
        out = capsys.readouterr().out
        assert "blocklist" in out
        assert "3x/day" in out


# ---------------------------------------------------------------------------
# schedule_cmd
# ---------------------------------------------------------------------------

class TestScheduleCmd:
    def test_install_blocklist_only(self, monkeypatch):
        import yt_dont_recommend.scheduler as sched_mod
        called = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(ydr, "_schedule_linux",
                            lambda action, path, sched: called.append(sched))
        monkeypatch.setattr(sched_mod, "_schedule_linux",
                            lambda action, path, sched: called.append(sched))
        monkeypatch.setattr(sched_mod.sys, "platform", "linux")
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE",
                            Path("/tmp/test_sched_cmd.json"))

        schedule_cmd("install", blocklist_runs=3, clickbait_runs=0)
        assert called
        assert called[0]["modes"]["blocklist"]["runs_per_day"] == 3
        assert "clickbait" not in called[0]["modes"]

    def test_install_both_modes(self, monkeypatch):
        import yt_dont_recommend.scheduler as sched_mod
        called = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(ydr, "_schedule_linux",
                            lambda action, path, sched: called.append(sched))
        monkeypatch.setattr(sched_mod, "_schedule_linux",
                            lambda action, path, sched: called.append(sched))
        monkeypatch.setattr(sched_mod.sys, "platform", "linux")
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE",
                            Path("/tmp/test_sched_cmd2.json"))

        schedule_cmd("install", blocklist_runs=2, clickbait_runs=6)
        assert called[0]["modes"]["blocklist"]["runs_per_day"] == 2
        assert called[0]["modes"]["clickbait"]["runs_per_day"] == 6


# ---------------------------------------------------------------------------
# _find_installed_binary
# ---------------------------------------------------------------------------

class TestFindInstalledBinary:
    def test_returns_argv0_when_resolved_and_non_py(self, monkeypatch, tmp_path):
        """Installed binaries have suffix != .py — argv[0] is authoritative."""
        from yt_dont_recommend import scheduler as sched_mod
        bin_path = tmp_path / "yt-dont-recommend"
        bin_path.write_text("#!/bin/sh\n")
        bin_path.chmod(0o755)
        monkeypatch.setattr(sched_mod.sys, "argv", [str(bin_path)])
        assert sched_mod._find_installed_binary() == str(bin_path)

    def test_falls_back_to_which_in_dev_mode(self, monkeypatch):
        """In dev mode argv[0] is a .py; falls through to shutil.which()."""
        from yt_dont_recommend import scheduler as sched_mod
        monkeypatch.setattr(sched_mod.sys, "argv", ["/some/dev/script.py"])
        with patch("shutil.which", return_value="/opt/bin/yt-dont-recommend"):
            assert sched_mod._find_installed_binary() == "/opt/bin/yt-dont-recommend"

    def test_final_fallback_uses_python_plus_argv0(self, monkeypatch):
        """If argv[0] is .py and shutil.which fails, use python-plus-argv0."""
        from yt_dont_recommend import scheduler as sched_mod
        monkeypatch.setattr(sched_mod.sys, "argv", ["/some/dev/script.py"])
        monkeypatch.setattr(sched_mod.sys, "executable", "/usr/bin/python3")
        with patch("shutil.which", return_value=None):
            result = sched_mod._find_installed_binary()
        assert result.startswith("/usr/bin/python3")
        assert "script.py" in result


# ---------------------------------------------------------------------------
# _modes_summary
# ---------------------------------------------------------------------------

class TestModesSummary:
    def test_empty_modes_returns_none_configured(self):
        from yt_dont_recommend.scheduler import _modes_summary
        assert _modes_summary({}) == "none configured"
        assert _modes_summary({"modes": {}}) == "none configured"

    def test_populated_modes_formatted_correctly(self):
        from yt_dont_recommend.scheduler import _modes_summary
        out = _modes_summary({"modes": {
            "blocklist": {"runs_per_day": 2},
            "clickbait": {"runs_per_day": 4},
        }})
        assert "blocklist: 2x/day" in out
        assert "clickbait: 4x/day" in out


# ---------------------------------------------------------------------------
# _print_today_plan
# ---------------------------------------------------------------------------

class TestPrintTodayPlan:
    def test_no_date_is_noop(self, capsys):
        from yt_dont_recommend.scheduler import _print_today_plan
        _print_today_plan({"today": {}})
        assert capsys.readouterr().out == ""

    def test_prints_planned_and_executed_times(self, capsys):
        from yt_dont_recommend.scheduler import _print_today_plan
        _print_today_plan({
            "modes": {"blocklist": {"runs_per_day": 2}},
            "today": {
                "date": "2026-04-20",
                "blocklist": {
                    "planned_utc":  ["03:17", "15:44"],
                    "executed_utc": ["03:17"],
                },
            },
        })
        out = capsys.readouterr().out
        assert "2026-04-20" in out
        assert "03:17" in out
        assert "\u2713" in out   # executed check mark
        assert "15:44" in out
        assert "pending" in out

    def test_prints_no_runs_scheduled_when_planned_empty(self, capsys):
        from yt_dont_recommend.scheduler import _print_today_plan
        _print_today_plan({
            "modes": {"blocklist": {"runs_per_day": 0}},
            "today": {
                "date": "2026-04-20",
                "blocklist": {"planned_utc": [], "executed_utc": []},
            },
        })
        assert "no runs scheduled today" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _schedule_macos — launchd install/remove/status
# ---------------------------------------------------------------------------

class TestScheduleMacos:
    def _setup_paths(self, monkeypatch, tmp_path):
        """Point every scheduler file path into tmp_path."""
        from yt_dont_recommend import scheduler as sched_mod
        plist = tmp_path / "com.ydr.heartbeat.plist"
        sched_file = tmp_path / "schedule.json"
        monkeypatch.setattr(sched_mod, "_LAUNCHD_PLIST", plist)
        monkeypatch.setattr(sched_mod, "SCHEDULE_FILE", sched_file)
        monkeypatch.setattr("yt_dont_recommend.config.SCHEDULE_FILE", sched_file)
        return plist, sched_file

    def test_install_creates_plist_and_loads(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos
        plist, sched_file = self._setup_paths(monkeypatch, tmp_path)
        with patch("yt_dont_recommend.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _schedule_macos("install", "/bin/ydr",
                            {"modes": {"blocklist": {"runs_per_day": 2}}, "headless": True})
        assert plist.exists()
        # Verify launchctl load was called
        load_calls = [c for c in mock_run.call_args_list
                      if "launchctl" in c.args[0] and "load" in c.args[0]]
        assert load_calls
        assert "Schedule installed" in capsys.readouterr().out

    def test_install_unloads_existing_plist(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos
        plist, _ = self._setup_paths(monkeypatch, tmp_path)
        plist.write_text("existing plist")
        with patch("yt_dont_recommend.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _schedule_macos("install", "/bin/ydr",
                            {"modes": {"blocklist": {"runs_per_day": 1}}, "headless": False})
        unload_calls = [c for c in mock_run.call_args_list
                        if "launchctl" in c.args[0] and "unload" in c.args[0]]
        assert unload_calls
        assert "Replacing existing schedule" in capsys.readouterr().out

    def test_remove_without_plist_says_nothing_to_remove(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos
        self._setup_paths(monkeypatch, tmp_path)
        _schedule_macos("remove", "/bin/ydr", {})
        assert "No schedule to remove" in capsys.readouterr().out

    def test_remove_unloads_and_deletes_plist(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos
        plist, _ = self._setup_paths(monkeypatch, tmp_path)
        plist.write_text("existing")
        with patch("yt_dont_recommend.scheduler.subprocess.run") as mock_run:
            _schedule_macos("remove", "/bin/ydr", {})
        assert not plist.exists()
        assert any("unload" in c.args[0] for c in mock_run.call_args_list)
        assert "Schedule removed" in capsys.readouterr().out

    def test_status_no_plist(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos
        self._setup_paths(monkeypatch, tmp_path)
        _schedule_macos("status", "/bin/ydr", {})
        assert "No schedule installed" in capsys.readouterr().out

    def test_status_loaded(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos, save_schedule
        plist, _ = self._setup_paths(monkeypatch, tmp_path)
        plist.write_text("plist content")
        save_schedule({
            "modes": {"blocklist": {"runs_per_day": 2}},
            "headless": True,
            "today": {"date": "2026-04-20", "blocklist": {"planned_utc": ["03:17"], "executed_utc": []}},
        })
        with patch("yt_dont_recommend.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            _schedule_macos("status", "/bin/ydr", {})
        out = capsys.readouterr().out
        assert "loaded" in out
        assert "blocklist: 2x/day" in out
        assert "Headless:" in out

    def test_status_plist_present_but_not_loaded(self, monkeypatch, tmp_path, capsys):
        from yt_dont_recommend.scheduler import _schedule_macos
        plist, _ = self._setup_paths(monkeypatch, tmp_path)
        plist.write_text("plist content")
        with patch("yt_dont_recommend.scheduler.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            _schedule_macos("status", "/bin/ydr", {})
        out = capsys.readouterr().out
        assert "plist present but not loaded" in out


# ---------------------------------------------------------------------------
# _schedule_linux — remove branches
# ---------------------------------------------------------------------------

class TestScheduleLinuxRemoveBranches:
    def test_remove_without_managed_entry_says_nothing_to_remove(self, tmp_path, monkeypatch, capsys):
        """Remove should print 'nothing to remove' when the crontab has no
        managed entry."""
        with patch("yt_dont_recommend.scheduler.subprocess.run") as mock_run:
            # crontab -l returns other entries, but none are the managed line
            mock_run.return_value = MagicMock(returncode=0, stdout="# user's own entry\n")
            _schedule_linux("remove", "/bin/ydr", {})
        assert "No schedule to remove" in capsys.readouterr().out

    def test_remove_preserves_other_entries_and_adds_trailing_newline(self, tmp_path, monkeypatch, capsys):
        """Remove path with existing non-managed entries should preserve them
        and end the crontab with a newline (line 390)."""
        from yt_dont_recommend.config import _CRON_MARKER
        run_calls = []

        def fake_run(*args, **kwargs):
            # First call: `crontab -l` returns a mix; second: `crontab -` to write
            if args[0][:2] == ["crontab", "-l"]:
                mixed = f"# user entry\n* * * * * /other {_CRON_MARKER}"
                return MagicMock(returncode=0, stdout=mixed)
            run_calls.append(kwargs.get("input", ""))
            return MagicMock(returncode=0)

        with patch("yt_dont_recommend.scheduler.subprocess.run", side_effect=fake_run):
            _schedule_linux("remove", "/bin/ydr", {})
        # New crontab should contain the non-managed entry only, end with newline
        assert len(run_calls) == 1
        new_crontab = run_calls[0]
        assert "# user entry" in new_crontab
        assert new_crontab.endswith("\n")


# ---------------------------------------------------------------------------
# schedule_cmd — non-install dispatch + macOS dispatch
# ---------------------------------------------------------------------------

class TestScheduleCmdDispatch:
    def test_status_on_linux_calls_linux_platform_fn(self, monkeypatch):
        from yt_dont_recommend import scheduler as sched_mod
        called = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(ydr, "_schedule_linux",
                            lambda action, path, sched: called.append(("linux", action)))
        monkeypatch.setattr(sched_mod, "_schedule_linux",
                            lambda action, path, sched: called.append(("linux", action)))
        monkeypatch.setattr(sched_mod.sys, "platform", "linux")
        schedule_cmd("status")
        assert called == [("linux", "status")]

    def test_remove_on_macos_calls_macos_platform_fn(self, monkeypatch):
        from yt_dont_recommend import scheduler as sched_mod
        called = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(ydr, "_schedule_macos",
                            lambda action, path, sched: called.append(("macos", action)))
        monkeypatch.setattr(sched_mod, "_schedule_macos",
                            lambda action, path, sched: called.append(("macos", action)))
        monkeypatch.setattr(sched_mod.sys, "platform", "darwin")
        schedule_cmd("remove")
        assert called == [("macos", "remove")]

    def test_install_on_macos_calls_macos_platform_fn(self, monkeypatch, tmp_path):
        from yt_dont_recommend import scheduler as sched_mod
        called = []
        monkeypatch.setattr(ydr, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(sched_mod, "_find_installed_binary", lambda: "/bin/ydr")
        monkeypatch.setattr(ydr, "_schedule_macos",
                            lambda action, path, sched: called.append(sched))
        monkeypatch.setattr(sched_mod, "_schedule_macos",
                            lambda action, path, sched: called.append(sched))
        monkeypatch.setattr(sched_mod.sys, "platform", "darwin")
        monkeypatch.setattr("yt_dont_recommend.scheduler.SCHEDULE_FILE", tmp_path / "sched.json")
        schedule_cmd("install", blocklist_runs=1, clickbait_runs=0)
        assert called
        assert called[0]["modes"]["blocklist"]["runs_per_day"] == 1

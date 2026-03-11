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
from unittest.mock import patch, MagicMock

import yt_dont_recommend as ydr
from yt_dont_recommend.scheduler import (
    _compute_daily_plan,
    load_schedule,
    save_schedule,
    heartbeat,
    _schedule_linux,
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
        cron_lines = [l for l in written[0].splitlines() if "yt-dont-recommend" in l]
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

"""
Tests for yt_dont_recommend.state — load_state, save_state, version tracking.

Functions under test are imported directly from yt_dont_recommend.state, but
patch targets remain yt_dont_recommend.X (the re-exported name in __init__.py),
as they did in the original test_yt_dont_recommend.py.
"""

import json
import logging
import pytest
from pathlib import Path
from unittest.mock import patch

import yt_dont_recommend as ydr
from yt_dont_recommend.state import load_state, save_state


# ---------------------------------------------------------------------------
# State management (load_state / save_state)
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_load_state_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["processed"] == []
        assert state["last_run"] is None
        assert state["stats"] == {"total_blocked": 0, "total_skipped": 0, "total_failed": 0}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["processed"].append("@channel1")
        state["stats"]["total_blocked"] = 1
        ydr.save_state(state)

        loaded = ydr.load_state()
        assert "@channel1" in loaded["processed"]
        assert loaded["stats"]["total_blocked"] == 1
        assert loaded["last_run"] is not None

    def test_save_state_sets_last_run(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        ydr.save_state(state)
        loaded = ydr.load_state()
        assert loaded["last_run"] is not None

    def test_save_state_creates_parent_dirs(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", nested)
        state = ydr.load_state()
        ydr.save_state(state)
        assert nested.exists()

    def test_load_state_backward_compat_adds_missing_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        # Write an old-style state without the new fields
        old_state = {"processed": ["@ch"], "last_run": None, "stats": {}}
        (tmp_path / "processed.json").write_text(json.dumps(old_state))
        state = ydr.load_state()
        assert "blocked_by" in state
        assert "would_have_blocked" in state


# ---------------------------------------------------------------------------
# Version checking
# ---------------------------------------------------------------------------

class TestVersionChecking:
    def test_version_tuple_simple(self):
        assert ydr._version_tuple("1.2.3") == (1, 2, 3)

    def test_version_tuple_single(self):
        assert ydr._version_tuple("2") == (2,)

    def test_version_tuple_invalid_returns_zero(self):
        assert ydr._version_tuple("bad") == (0,)

    def test_check_for_update_returns_none_when_pypi_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        with patch("yt_dont_recommend.cli._get_latest_pypi_version", return_value=None):
            result = ydr.check_for_update(state, force=True)
        assert result is None

    def test_check_for_update_returns_none_when_already_latest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        with patch("yt_dont_recommend.cli._get_latest_pypi_version", return_value="0.1.0"), \
             patch("yt_dont_recommend.cli._get_current_version", return_value="0.1.4"):
            result = ydr.check_for_update(state, force=True)
        assert result is None

    def test_check_for_update_returns_version_when_newer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        with patch("yt_dont_recommend.cli._get_latest_pypi_version", return_value="0.2.0"), \
             patch("yt_dont_recommend.cli._get_current_version", return_value="0.1.4"):
            result = ydr.check_for_update(state, force=True)
        assert result == "0.2.0"

    def test_check_for_update_respects_interval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        from datetime import datetime
        state = ydr.load_state()
        # Simulate a recent check that found a newer version
        state["last_version_check"] = datetime.now().isoformat()
        state["latest_known_version"] = "0.2.0"
        with patch("yt_dont_recommend.cli._get_latest_pypi_version") as mock_pypi, \
             patch("yt_dont_recommend.cli._get_current_version", return_value="0.1.4"):
            result = ydr.check_for_update(state, force=False)
            mock_pypi.assert_not_called()  # should use cached value, not hit PyPI
        assert result == "0.2.0"

    def test_check_for_update_notifies_ntfy_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["notify_topic"] = "test-topic"
        with patch("yt_dont_recommend.cli._get_latest_pypi_version", return_value="0.2.0"), \
             patch("yt_dont_recommend.cli._get_current_version", return_value="0.1.4"), \
             patch("yt_dont_recommend.cli._ntfy_notify") as mock_ntfy:
            ydr.check_for_update(state, force=True)
            assert mock_ntfy.call_count == 1
            # Second call with same version should not re-notify
            ydr.check_for_update(state, force=True)
            assert mock_ntfy.call_count == 1

    def test_state_defaults_include_version_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["last_version_check"] is None
        assert state["latest_known_version"] is None
        assert state["notified_version"] is None
        assert state["auto_upgrade"] is False
        assert state["previous_version"] is None
        assert state["current_version"] is None
        assert state["state_version"] == ydr.STATE_VERSION

    def test_state_version_written_to_fresh_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        ydr.save_state(state)
        import json
        saved = json.loads((tmp_path / "processed.json").read_text())
        assert saved["state_version"] == ydr.STATE_VERSION

    def test_state_version_warn_on_newer_schema(self, tmp_path, monkeypatch, caplog):
        import json, logging
        state_file = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        # Write a state file with a future schema version
        state_file.write_text(json.dumps({"state_version": ydr.STATE_VERSION + 1}))
        with caplog.at_level(logging.WARNING):
            ydr.load_state()
        assert any("newer version" in r.message for r in caplog.records)

    def test_state_version_no_warn_on_same_or_older_schema(self, tmp_path, monkeypatch, caplog):
        import json, logging
        state_file = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        state_file.write_text(json.dumps({"state_version": ydr.STATE_VERSION}))
        with caplog.at_level(logging.WARNING):
            ydr.load_state()
        assert not any("newer version" in r.message for r in caplog.records)

    def test_version_tracked_at_startup_enables_revert_after_manual_upgrade(
        self, tmp_path, monkeypatch
    ):
        """Version tracking at startup should populate previous_version so that
        --revert works even when the upgrade was done manually (not via auto-upgrade)."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")

        # Simulate: tool previously ran at 0.1.6
        state = ydr.load_state()
        state["current_version"] = "0.1.6"
        ydr.save_state(state)

        # Now "running" 0.1.7 (e.g. after manual uv tool install)
        monkeypatch.setattr(ydr, "_get_current_version", lambda: "0.1.7")

        # The startup tracking block (replicated here) should rotate the version
        state = ydr.load_state()
        _running = ydr._get_current_version()
        if state.get("current_version") != _running:
            prior = state.get("current_version")
            if prior is not None:
                state["previous_version"] = prior
            state["current_version"] = _running
            ydr.save_state(state)

        state = ydr.load_state()
        assert state["current_version"] == "0.1.7"
        assert state["previous_version"] == "0.1.6"

    def test_version_tracking_does_not_overwrite_previous_with_none(
        self, tmp_path, monkeypatch
    ):
        """On first run (current_version is None), previous_version should not
        be set to None — it should be left untouched."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr(ydr, "_get_current_version", lambda: "0.1.20")

        # Fresh state — current_version is None
        state = ydr.load_state()
        assert state["current_version"] is None

        _running = ydr._get_current_version()
        if state.get("current_version") != _running:
            prior = state.get("current_version")
            if prior is not None:
                state["previous_version"] = prior
            state["current_version"] = _running
            ydr.save_state(state)

        state = ydr.load_state()
        assert state["current_version"] == "0.1.20"
        assert state["previous_version"] is None  # not overwritten with None

    def test_revert_with_no_previous_version_prints_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        ydr.do_revert()
        captured = capsys.readouterr()
        assert "No previous version" in captured.out
        assert "--revert 0.1.10" in captured.out  # explicit version hint

    def test_revert_explicit_version_skips_state_lookup(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr(ydr, "_get_current_version", lambda: "0.1.14")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "0.1.14")
        monkeypatch.setattr(ydr, "_detect_installer", lambda: "uv")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        ran = []
        import yt_dont_recommend.cli as cli_mod
        monkeypatch.setattr(
            cli_mod.subprocess, "run",
            lambda cmd, **kw: ran.append(cmd) or type("R", (), {"returncode": 0, "stderr": ""})()
        )
        ydr.do_revert("0.1.10")
        assert any("0.1.10" in str(c) for c in ran)
        captured = capsys.readouterr()
        assert "0.1.10" in captured.out

    def test_revert_no_op_when_already_on_target(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr(ydr, "_get_current_version", lambda: "0.1.10")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "0.1.10")
        ydr.do_revert("0.1.10")
        captured = capsys.readouterr()
        assert "nothing to do" in captured.out

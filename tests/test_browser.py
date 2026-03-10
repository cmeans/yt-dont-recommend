"""
Tests for yt_dont_recommend.browser and related CLI/main() functionality.

Browser automation functions (do_login, process_channels, check_selectors)
require a live YouTube session and are not tested here. This file covers
CLI-level tests and the first-run/uninstall logic in __init__.py.

Functions under test are imported directly from yt_dont_recommend, but
patch targets remain yt_dont_recommend.X (the re-exported name in __init__.py),
as they did in the original test_yt_dont_recommend.py.
"""

import pytest
from unittest.mock import patch

import yt_dont_recommend as ydr


# ---------------------------------------------------------------------------
# First-run welcome and --uninstall
# ---------------------------------------------------------------------------

class TestFirstRunAndUninstall:
    def test_first_run_detected_when_no_state_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        assert not (tmp_path / "processed.json").exists()
        is_first_run = not ydr.STATE_FILE.exists()
        assert is_first_run

    def test_first_run_not_detected_after_state_created(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        ydr.save_state(ydr.load_state())
        is_first_run = not ydr.STATE_FILE.exists()
        assert not is_first_run

    def test_first_run_welcome_prints(self, capsys):
        ydr._first_run_welcome()
        captured = capsys.readouterr()
        assert "Welcome" in captured.out
        assert "--login" in captured.out
        assert "--schedule install" in captured.out

    def test_do_uninstall_removes_data_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "data" / "processed.json")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        # Simulate user answering "y" to the removal prompt
        monkeypatch.setattr("builtins.input", lambda _: "y")
        monkeypatch.setattr(ydr, "schedule_cmd", lambda action: None)
        monkeypatch.setattr(ydr, "_detect_installer", lambda: "uv")
        ydr.do_uninstall()
        assert not data_dir.exists()
        captured = capsys.readouterr()
        assert "uv tool uninstall" in captured.out

    def test_do_uninstall_keeps_data_dir_on_no(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "data" / "processed.json")
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        monkeypatch.setattr("builtins.input", lambda _: "n")
        monkeypatch.setattr(ydr, "schedule_cmd", lambda action: None)
        monkeypatch.setattr(ydr, "_detect_installer", lambda: "pipx")
        ydr.do_uninstall()
        assert data_dir.exists()
        captured = capsys.readouterr()
        assert "pipx uninstall" in captured.out

"""
Tests for yt_dont_recommend.cli — main() preflight checks.

These tests exercise the clickbait model preflight logic in main() without
running any browser automation. All browser and state I/O is patched out.

Patch targets:
  - yt_dont_recommend.clickbait.load_config  (local alias in main(); patch the source)
  - yt_dont_recommend.browser.open_browser   (local import in main(); patch the source)
  - yt_dont_recommend.cli.check_for_update   (avoids network I/O)
  - yt_dont_recommend.cli.check_attention_flag (avoids tty prompt)
  - yt_dont_recommend.cli.setup_logging      (avoids writing to real log file)
"""

import logging
import sys
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

import yt_dont_recommend as ydr
from yt_dont_recommend.clickbait import _DEFAULT_CONFIG

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(auto_pull: bool = False) -> dict:
    """Return a clickbait config with auto_pull set on the title model."""
    cfg = deepcopy(_DEFAULT_CONFIG)
    cfg["video"]["title"]["model"]["auto_pull"] = auto_pull
    return cfg


def _ollama_list(model_names: list[str]) -> MagicMock:
    """Return a mock ollama.list() response containing the given model names."""
    models = []
    for name in model_names:
        m = MagicMock()
        m.model = name
        models.append(m)
    resp = MagicMock()
    resp.models = models
    return resp


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clickbait_argv(monkeypatch):
    """Set sys.argv to invoke --clickbait --dry-run."""
    monkeypatch.setattr(sys, "argv", ["yt-dont-recommend", "--clickbait", "--dry-run"])


@pytest.fixture
def patched_env(tmp_path, monkeypatch):
    """Redirect state I/O and suppress network / tty side-effects in main()."""
    monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
    monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
    monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)


# ---------------------------------------------------------------------------
# TestClickbaitPreflight
# ---------------------------------------------------------------------------

class TestClickbaitPreflight:
    """Preflight checks for --clickbait: missing deps, model availability, auto-pull."""

    def test_fast_fail_when_ollama_not_installed(
        self, clickbait_argv, patched_env, caplog
    ):
        """When ollama is not importable, main() logs an error and returns
        before opening any browser window."""
        open_browser_calls = []

        with (
            patch.dict("sys.modules", {"ollama": None}),
            patch("yt_dont_recommend.browser.open_browser",
                  side_effect=lambda **kw: open_browser_calls.append(kw) or None),
            caplog.at_level(logging.ERROR, logger="yt_dont_recommend.cli"),
        ):
            ydr.main()

        assert len(open_browser_calls) == 0, "open_browser must not be called"
        msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "clickbait" in m.lower() or "dependencies" in m.lower() or "ollama" in m.lower()
            for m in msgs
        ), f"expected error about missing ollama; got: {msgs}"

    def test_fast_fail_when_model_not_pulled(
        self, clickbait_argv, patched_env, caplog
    ):
        """When ollama is available but the model is missing and auto_pull is
        False, main() logs an error and returns before opening the browser."""
        open_browser_calls = []

        mock_ollama = MagicMock()
        mock_ollama.list.return_value = _ollama_list([])  # no models available

        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            patch("yt_dont_recommend.browser.open_browser",
                  side_effect=lambda **kw: open_browser_calls.append(kw) or None),
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg(auto_pull=False)),
            caplog.at_level(logging.ERROR, logger="yt_dont_recommend.cli"),
        ):
            ydr.main()

        assert len(open_browser_calls) == 0, "open_browser must not be called"
        msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("not pulled" in m or "ollama pull" in m for m in msgs), \
            f"expected error about unpulled model; got: {msgs}"
        assert any("auto_pull" in m for m in msgs), \
            "error should hint at auto_pull option"

    def test_auto_pull_called_when_model_missing(
        self, clickbait_argv, patched_env
    ):
        """When auto_pull is True and the model is missing, ollama.pull() is
        called and execution continues to open_browser."""
        open_browser_calls = []

        mock_ollama = MagicMock()
        mock_ollama.list.return_value = _ollama_list([])  # model missing
        mock_ollama.pull.return_value = None              # pull succeeds

        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            patch("yt_dont_recommend.browser.open_browser",
                  side_effect=lambda **kw: open_browser_calls.append(kw) or None),
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg(auto_pull=True)),
        ):
            ydr.main()

        mock_ollama.pull.assert_called_once()
        assert len(open_browser_calls) == 1, "open_browser should have been reached"

    def test_fast_fail_when_auto_pull_fails(
        self, clickbait_argv, patched_env, caplog
    ):
        """When auto_pull is True but ollama.pull() raises, main() logs an
        error and returns before opening the browser."""
        open_browser_calls = []

        mock_ollama = MagicMock()
        mock_ollama.list.return_value = _ollama_list([])  # model missing
        mock_ollama.pull.side_effect = RuntimeError("connection refused")

        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            patch("yt_dont_recommend.browser.open_browser",
                  side_effect=lambda **kw: open_browser_calls.append(kw) or None),
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg(auto_pull=True)),
            caplog.at_level(logging.ERROR, logger="yt_dont_recommend.cli"),
        ):
            ydr.main()

        assert len(open_browser_calls) == 0, "open_browser must not be called"
        msgs = [r.message for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("pull" in m.lower() or "failed" in m.lower() for m in msgs), \
            f"expected error about pull failure; got: {msgs}"


# ---------------------------------------------------------------------------
# Version / installer helpers
# ---------------------------------------------------------------------------

class TestVersionHelpers:
    def test_get_current_version_returns_string(self):
        from yt_dont_recommend.cli import _get_current_version
        v = _get_current_version()
        assert isinstance(v, str)

    def test_get_current_version_falls_back_on_metadata_failure(self, monkeypatch):
        """When importlib.metadata.version() raises, _get_current_version
        falls back to the module-level __version__."""
        import importlib.metadata

        def boom(_name):
            raise importlib.metadata.PackageNotFoundError("nope")

        # Patch the importlib.metadata.version function at the source.
        monkeypatch.setattr(importlib.metadata, "version", boom)
        from yt_dont_recommend.cli import _get_current_version
        assert isinstance(_get_current_version(), str)

    def test_version_tuple_valid(self):
        from yt_dont_recommend.cli import _version_tuple
        assert _version_tuple("1.2.3") == (1, 2, 3)

    def test_version_tuple_malformed_returns_zero(self):
        from yt_dont_recommend.cli import _version_tuple
        assert _version_tuple("not-a-version") == (0,)

    def test_get_latest_pypi_version_success(self, monkeypatch):
        import json as _json

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return _json.dumps({"info": {"version": "9.9.9"}}).encode()

        monkeypatch.setattr("yt_dont_recommend.cli.urlopen", lambda req, timeout=5: FakeResp())
        from yt_dont_recommend.cli import _get_latest_pypi_version
        assert _get_latest_pypi_version() == "9.9.9"

    def test_get_latest_pypi_version_failure(self, monkeypatch):
        def boom(req, timeout=5):
            raise OSError("no network")

        monkeypatch.setattr("yt_dont_recommend.cli.urlopen", boom)
        from yt_dont_recommend.cli import _get_latest_pypi_version
        assert _get_latest_pypi_version() is None


class TestDetectInstaller:
    def test_uv_installer(self, monkeypatch):
        monkeypatch.setattr("yt_dont_recommend.cli._find_installed_binary",
                            lambda: "/home/user/.local/share/uv/tools/yt-dont-recommend/bin/yt-dont-recommend")
        from yt_dont_recommend.cli import _detect_installer
        assert _detect_installer() == "uv"

    def test_pipx_installer(self, monkeypatch):
        monkeypatch.setattr("yt_dont_recommend.cli._find_installed_binary",
                            lambda: "/home/user/.local/pipx/venvs/yt-dont-recommend/bin/yt-dont-recommend")
        from yt_dont_recommend.cli import _detect_installer
        assert _detect_installer() == "pipx"

    def test_neither_returns_none(self, monkeypatch):
        monkeypatch.setattr("yt_dont_recommend.cli._find_installed_binary",
                            lambda: "/usr/local/bin/yt-dont-recommend")
        from yt_dont_recommend.cli import _detect_installer
        assert _detect_installer() is None


class TestClickbaitInstallCmd:
    def test_uv_tools_path(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/home/u/.local/share/uv/tools/yt-dont-recommend/bin/ydr"])
        from yt_dont_recommend.cli import _clickbait_install_cmd
        assert "uv tool install" in _clickbait_install_cmd()

    def test_pipx_path(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/home/u/.local/pipx/venvs/yt-dont-recommend/bin/ydr"])
        from yt_dont_recommend.cli import _clickbait_install_cmd
        assert "pipx install" in _clickbait_install_cmd()

    def test_pip_fallback(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/ydr"])
        from yt_dont_recommend.cli import _clickbait_install_cmd
        assert "pip install" in _clickbait_install_cmd()


# ---------------------------------------------------------------------------
# check_for_update — cache, freshness, notify
# ---------------------------------------------------------------------------

class TestCheckForUpdate:
    def test_recent_check_returns_cached_newer_version(self, monkeypatch):
        """Within VERSION_CHECK_INTERVAL, use cached latest_known_version."""
        from datetime import datetime

        from yt_dont_recommend.cli import check_for_update
        state = {
            "last_version_check": datetime.now().isoformat(),
            "latest_known_version": "99.0.0",
        }
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "0.1.0")
        monkeypatch.setattr("yt_dont_recommend.cli._get_latest_pypi_version",
                            lambda: pytest.fail("should not hit PyPI"))
        assert check_for_update(state) == "99.0.0"

    def test_recent_check_returns_none_when_up_to_date(self, monkeypatch):
        from datetime import datetime

        from yt_dont_recommend.cli import check_for_update
        state = {
            "last_version_check": datetime.now().isoformat(),
            "latest_known_version": "0.1.0",
        }
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "0.1.0")
        assert check_for_update(state) is None

    def test_cache_fallback_on_invalid_timestamp(self, monkeypatch):
        from yt_dont_recommend.cli import check_for_update
        state = {"last_version_check": "not-a-date"}
        monkeypatch.setattr("yt_dont_recommend.cli._get_latest_pypi_version", lambda: "0.1.0")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "0.1.0")
        # Falls through to fresh check
        assert check_for_update(state) is None

    def test_force_refresh_hits_pypi(self, monkeypatch):
        from yt_dont_recommend.cli import check_for_update
        state = {}
        monkeypatch.setattr("yt_dont_recommend.cli._get_latest_pypi_version", lambda: "2.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        result = check_for_update(state, force=True)
        assert result == "2.0.0"
        assert state["latest_known_version"] == "2.0.0"

    def test_pypi_failure_returns_none_without_crashing(self, monkeypatch):
        from yt_dont_recommend.cli import check_for_update
        state = {}
        monkeypatch.setattr("yt_dont_recommend.cli._get_latest_pypi_version", lambda: None)
        assert check_for_update(state, force=True) is None

    def test_notifies_via_ntfy_once_per_new_version(self, monkeypatch):
        from yt_dont_recommend.cli import check_for_update
        ntfy_calls = []
        monkeypatch.setattr("yt_dont_recommend.cli._get_latest_pypi_version", lambda: "2.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._ntfy_notify",
                            lambda topic, msg: ntfy_calls.append((topic, msg)))
        state = {"notify_topic": "mytopic"}
        check_for_update(state, force=True)
        assert len(ntfy_calls) == 1
        # Second check: notified_version now matches, no second ntfy
        check_for_update(state, force=True)
        assert len(ntfy_calls) == 1


# ---------------------------------------------------------------------------
# do_auto_upgrade
# ---------------------------------------------------------------------------

class TestDoAutoUpgrade:
    def test_uv_path_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        captured = {}

        def fake_run(cmd, capture_output, text):
            captured["cmd"] = cmd
            return MagicMock(returncode=0)

        monkeypatch.setattr("yt_dont_recommend.cli.subprocess.run", fake_run)
        from yt_dont_recommend.cli import do_auto_upgrade
        state = {}
        assert do_auto_upgrade(state) is True
        assert captured["cmd"] == ["uv", "tool", "install", "yt-dont-recommend@latest"]
        assert state["previous_version"] == "1.0.0"

    def test_pipx_path_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "pipx")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli.subprocess.run",
                            lambda *a, **kw: MagicMock(returncode=0))
        from yt_dont_recommend.cli import do_auto_upgrade
        assert do_auto_upgrade({}) is True

    def test_unknown_installer_returns_false_and_warns(self, monkeypatch, caplog):
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        from yt_dont_recommend.cli import do_auto_upgrade
        with caplog.at_level(logging.WARNING, logger="yt_dont_recommend.cli"):
            assert do_auto_upgrade({}) is False
        assert any("package manager" in r.message.lower() for r in caplog.records)

    def test_subprocess_failure_writes_attention(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli.subprocess.run",
                            lambda *a, **kw: MagicMock(returncode=1, stderr="install failed"))
        wa_calls = []
        monkeypatch.setattr("yt_dont_recommend.cli.write_attention", lambda msg: wa_calls.append(msg))
        from yt_dont_recommend.cli import do_auto_upgrade
        assert do_auto_upgrade({}) is False
        assert wa_calls and "install failed" in wa_calls[0]


# ---------------------------------------------------------------------------
# do_revert
# ---------------------------------------------------------------------------

class TestDoRevert:
    def _patched(self, tmp_path, monkeypatch, state_data=None):
        sf = tmp_path / "state.json"
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        if state_data is not None:
            import json as _json
            sf.write_text(_json.dumps(state_data))

    def test_no_previous_version_prints_message(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        from yt_dont_recommend.cli import do_revert
        do_revert()
        assert "No previous version recorded" in capsys.readouterr().out

    def test_target_version_already_current(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        from yt_dont_recommend.cli import do_revert
        do_revert("1.0.0")
        assert "Already running" in capsys.readouterr().out

    def test_unknown_installer_prints_manual_instructions(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: None)
        from yt_dont_recommend.cli import do_revert
        do_revert("0.5.0")
        out = capsys.readouterr().out
        assert "Install manually" in out
        assert "uv tool install --force yt-dont-recommend==0.5.0" in out

    def test_uv_success(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        monkeypatch.setattr("yt_dont_recommend.cli.subprocess.run",
                            lambda *a, **kw: MagicMock(returncode=0))
        from yt_dont_recommend.cli import do_revert
        do_revert("0.5.0")
        out = capsys.readouterr().out
        assert "Reverted to 0.5.0" in out
        assert "Auto-upgrade has been disabled" in out

    def test_pipx_failure_prints_stderr(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "pipx")
        monkeypatch.setattr("yt_dont_recommend.cli.subprocess.run",
                            lambda *a, **kw: MagicMock(returncode=1, stderr="pipx exploded"))
        from yt_dont_recommend.cli import do_revert
        do_revert("0.5.0")
        assert "pipx exploded" in capsys.readouterr().out

    def test_uses_previous_version_when_no_arg(self, tmp_path, monkeypatch, capsys):
        import json as _json
        sf = tmp_path / "state.json"
        sf.write_text(_json.dumps({"previous_version": "0.5.0", "blocked_by": {}}))
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        monkeypatch.setattr("yt_dont_recommend.cli.subprocess.run",
                            lambda *a, **kw: MagicMock(returncode=0))
        from yt_dont_recommend.cli import do_revert
        do_revert()
        assert "Reverted to 0.5.0" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# setup_notify / remove_notify / test_notify
# ---------------------------------------------------------------------------

class TestNotifyCommands:
    def _patched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")

    def test_setup_notify_generates_topic(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        from yt_dont_recommend.cli import setup_notify
        setup_notify()
        state = ydr.load_state()
        assert state["notify_topic"].startswith("ydr-")
        out = capsys.readouterr().out
        assert "Notification topic generated" in out
        assert state["notify_topic"] in out

    def test_setup_notify_skipped_when_already_configured(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        state = ydr.load_state()
        state["notify_topic"] = "ydr-existing"
        ydr.save_state(state)
        from yt_dont_recommend.cli import setup_notify
        setup_notify()
        out = capsys.readouterr().out
        assert "already configured" in out
        assert "ydr-existing" in out

    def test_remove_notify_no_topic(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        from yt_dont_recommend.cli import remove_notify
        remove_notify()
        assert "No notification topic configured" in capsys.readouterr().out

    def test_remove_notify_clears_topic(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        state = ydr.load_state()
        state["notify_topic"] = "ydr-existing"
        ydr.save_state(state)
        from yt_dont_recommend.cli import remove_notify
        remove_notify()
        assert ydr.load_state().get("notify_topic") is None

    def test_test_notify_without_topic(self, tmp_path, monkeypatch, capsys):
        self._patched(tmp_path, monkeypatch)
        from yt_dont_recommend.cli import test_notify
        test_notify()
        assert "No notification topic configured" in capsys.readouterr().out

    def test_test_notify_with_topic_calls_ntfy(self, tmp_path, monkeypatch):
        self._patched(tmp_path, monkeypatch)
        state = ydr.load_state()
        state["notify_topic"] = "ydr-abc"
        ydr.save_state(state)
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli._ntfy_notify",
                            lambda t, m: called.append((t, m)))
        from yt_dont_recommend.cli import test_notify
        test_notify()
        assert called == [("ydr-abc", "Test notification — yt-dont-recommend is configured correctly.")]


# ---------------------------------------------------------------------------
# _first_run_welcome + do_uninstall
# ---------------------------------------------------------------------------

class TestFirstRunWelcome:
    def test_prints_welcome_banner(self, capsys):
        from yt_dont_recommend.cli import _first_run_welcome
        _first_run_welcome()
        out = capsys.readouterr().out
        assert "Welcome to yt-dont-recommend" in out
        assert "--login" in out


class TestDoUninstall:
    def test_removes_data_dir_on_yes(self, tmp_path, monkeypatch, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        monkeypatch.setattr(ydr, "STATE_FILE", data_dir / "processed.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", data_dir / "processed.json")
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", lambda action: None)
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        with patch("builtins.input", return_value="y"):
            from yt_dont_recommend.cli import do_uninstall
            do_uninstall()
        assert not data_dir.exists()
        out = capsys.readouterr().out
        assert "uv tool uninstall" in out

    def test_keeps_data_dir_on_no(self, tmp_path, monkeypatch, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", data_dir / "processed.json")
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", lambda action: None)
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "pipx")
        with patch("builtins.input", return_value="n"):
            from yt_dont_recommend.cli import do_uninstall
            do_uninstall()
        assert data_dir.exists()
        out = capsys.readouterr().out
        assert "Kept" in out
        assert "pipx uninstall" in out

    def test_keyboard_interrupt_treated_as_no(self, tmp_path, monkeypatch):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", data_dir / "processed.json")
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", lambda action: None)
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: None)
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            from yt_dont_recommend.cli import do_uninstall
            do_uninstall()
        assert data_dir.exists()

    def test_missing_data_dir(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "no-exist" / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", lambda action: None)
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: None)
        from yt_dont_recommend.cli import do_uninstall
        do_uninstall()
        assert "nothing to remove" in capsys.readouterr().out

    def test_schedule_remove_failure_is_non_fatal(self, tmp_path, monkeypatch, capsys):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", data_dir / "processed.json")
        def boom(action):
            raise RuntimeError("no schedule")
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", boom)
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: None)
        with patch("builtins.input", return_value="n"):
            from yt_dont_recommend.cli import do_uninstall
            do_uninstall()
        out = capsys.readouterr().out
        assert "Could not remove schedule" in out


# ---------------------------------------------------------------------------
# main() dispatch — each early-return command
# ---------------------------------------------------------------------------

@pytest.fixture
def main_env(tmp_path, monkeypatch):
    """Shared setup for main() dispatch tests: state file, no logging, no network."""
    monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
    monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
    monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
    return tmp_path


class TestMainDispatchEarlyReturns:
    def test_heartbeat_calls_scheduler_heartbeat_and_returns(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--heartbeat"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.scheduler.heartbeat",
                            lambda: called.append(True))
        ydr.main()
        assert called == [True]

    def test_uninstall_dispatches_do_uninstall(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--uninstall"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli.do_uninstall",
                            lambda: called.append(True))
        ydr.main()
        assert called == [True]

    def test_clear_alerts_when_file_exists(self, main_env, monkeypatch, capsys):
        af = main_env / "needs-attention.txt"
        af.write_text("alert")
        monkeypatch.setattr("yt_dont_recommend.cli.ATTENTION_FILE", af)
        monkeypatch.setattr(sys, "argv", ["ydr", "--clear-alerts"])
        ydr.main()
        assert not af.exists()
        assert "Alerts cleared" in capsys.readouterr().out

    def test_clear_alerts_when_no_file(self, main_env, monkeypatch, capsys):
        af = main_env / "needs-attention.txt"
        monkeypatch.setattr("yt_dont_recommend.cli.ATTENTION_FILE", af)
        monkeypatch.setattr(sys, "argv", ["ydr", "--clear-alerts"])
        ydr.main()
        assert "No alerts" in capsys.readouterr().out

    def test_setup_notify_dispatch(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--setup-notify"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli.setup_notify", lambda: called.append(True))
        ydr.main()
        assert called == [True]

    def test_remove_notify_dispatch(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--remove-notify"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli.remove_notify", lambda: called.append(True))
        ydr.main()
        assert called == [True]

    def test_test_notify_dispatch(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--test-notify"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli.test_notify", lambda: called.append(True))
        ydr.main()
        assert called == [True]

    def test_check_update_newer_version(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--check-update"])
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update",
                            lambda state, force=False: "9.9.9")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "pipx")
        ydr.main()
        out = capsys.readouterr().out
        assert "New version available: 9.9.9" in out
        assert "pipx upgrade" in out

    def test_check_update_up_to_date(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--check-update"])
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update",
                            lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        ydr.main()
        assert "latest version" in capsys.readouterr().out

    def test_check_update_with_uv_installer(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--check-update"])
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update",
                            lambda state, force=False: "9.9.9")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        ydr.main()
        assert "uv tool install yt-dont-recommend@latest" in capsys.readouterr().out

    def test_auto_upgrade_enable(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--auto-upgrade", "enable"])
        ydr.main()
        assert "enabled" in capsys.readouterr().out.lower()
        assert ydr.load_state()["auto_upgrade"] is True

    def test_auto_upgrade_disable(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--auto-upgrade", "disable"])
        ydr.main()
        assert "disabled" in capsys.readouterr().out.lower()
        assert ydr.load_state()["auto_upgrade"] is False

    def test_revert_without_version(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--revert"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli.do_revert",
                            lambda v: called.append(v))
        ydr.main()
        assert called == [None]

    def test_revert_with_version(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--revert", "0.5.0"])
        called = []
        monkeypatch.setattr("yt_dont_recommend.cli.do_revert",
                            lambda v: called.append(v))
        ydr.main()
        assert called == ["0.5.0"]

    def test_schedule_install_with_flags(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--schedule", "install", "--blocklist-runs", "2"])
        calls = []
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd",
                            lambda action, blocklist_runs=0, clickbait_runs=0: calls.append((action, blocklist_runs, clickbait_runs)))
        ydr.main()
        assert calls == [("install", 2, 0)]

    def test_schedule_install_empty_uses_config_defaults(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--schedule", "install"])
        monkeypatch.setattr("yt_dont_recommend.config.load_schedule_config",
                            lambda: {"blocklist_runs": 3})
        calls = []
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd",
                            lambda action, blocklist_runs=0, clickbait_runs=0: calls.append((action, blocklist_runs, clickbait_runs)))
        ydr.main()
        assert calls == [("install", 3, 0)]

    def test_schedule_install_no_runs_errors(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--schedule", "install"])
        monkeypatch.setattr("yt_dont_recommend.config.load_schedule_config", lambda: {})
        with pytest.raises(SystemExit) as exc:
            ydr.main()
        assert exc.value.code == 1
        assert "Nothing to schedule" in capsys.readouterr().out

    def test_schedule_status_dispatches_without_runs(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--schedule", "status"])
        calls = []
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd",
                            lambda *a, **kw: calls.append((a, kw)))
        ydr.main()
        assert calls == [(("status",), {})]

    def test_list_sources_prints_builtins(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--list-sources"])
        ydr.main()
        out = capsys.readouterr().out
        assert "deslop" in out
        assert "aislist" in out

    def test_stats_prints_summary(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--stats"])
        state = ydr.load_state()
        state["blocked_by"] = {
            "@a": {"sources": ["deslop"]},
            "@b": {"sources": ["deslop", "aislist"]},
        }
        state["source_sizes"] = {"deslop": 100, "aislist": 200}
        state["would_have_blocked"] = {
            "@sub": {"sources": ["deslop"], "first_seen": "2026-04-20T10:00:00"},
        }
        ydr.save_state(state)
        ydr.main()
        out = capsys.readouterr().out
        assert "Blocked channels" in out
        assert "Feed coverage" in out
        assert "@sub" in out
        assert "deslop" in out

    def test_export_state_to_stdout(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr", "--export-state"])
        state = ydr.load_state()
        state["blocked_by"] = {"@a": {"sources": ["deslop"]}}
        ydr.save_state(state)
        ydr.main()
        assert "@a  # deslop" in capsys.readouterr().out

    def test_export_state_to_file(self, main_env, monkeypatch, capsys):
        out_file = main_env / "blocked.txt"
        monkeypatch.setattr(sys, "argv", ["ydr", "--export-state", str(out_file)])
        state = ydr.load_state()
        state["blocked_by"] = {"@a": {"sources": ["deslop"]}}
        ydr.save_state(state)
        ydr.main()
        assert "@a" in out_file.read_text()
        assert "Exported" in capsys.readouterr().out

    def test_reset_state_removes_file(self, main_env, monkeypatch):
        sf = main_env / "state.json"
        sf.write_text("{}")
        monkeypatch.setattr(sys, "argv", ["ydr", "--reset-state"])
        ydr.main()
        assert not sf.exists()

    def test_reset_state_when_no_file(self, main_env, monkeypatch, caplog):
        monkeypatch.setattr(sys, "argv", ["ydr", "--reset-state"])
        with caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"):
            ydr.main()
        # Reaches the "No state file" branch

    def test_login_dispatches_browser_do_login(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--login"])
        with patch("yt_dont_recommend.browser.do_login") as mock_do_login:
            ydr.main()
        mock_do_login.assert_called_once()

    def test_check_selectors_exits_with_status(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--check-selectors"])
        monkeypatch.setattr("yt_dont_recommend.diagnostics.check_selectors",
                            lambda ch, repair=False: True)
        with pytest.raises(SystemExit) as exc:
            ydr.main()
        assert exc.value.code == 0

    def test_check_selectors_failure_exits_one(self, main_env, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["ydr", "--check-selectors"])
        monkeypatch.setattr("yt_dont_recommend.diagnostics.check_selectors",
                            lambda ch, repair=False: False)
        with pytest.raises(SystemExit) as exc:
            ydr.main()
        assert exc.value.code == 1

    def test_no_mode_prints_help(self, main_env, monkeypatch, capsys):
        monkeypatch.setattr(sys, "argv", ["ydr"])
        ydr.main()
        out = capsys.readouterr().out
        assert "usage:" in out.lower() or "optional" in out.lower()


# ---------------------------------------------------------------------------
# main() blocklist mode — the big block (720-805)
# ---------------------------------------------------------------------------

class TestMainBlocklistFlow:
    def test_blocklist_with_default_sources_opens_browser(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope-blocklist.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "nope-clickbait.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "nope-legacy.txt")

        # Stub resolve_source to return a deterministic list
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: ["@target1"])

        open_calls, process_calls, close_calls = [], [], []
        fake_handle = ("ctx-mgr", "ctx", "page")

        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: open_calls.append(headless) or fake_handle)
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: process_calls.append(kw))
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser",
                            lambda h: close_calls.append(h))

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop", "--dry-run"])
        ydr.main()

        assert open_calls == [False]
        assert len(process_calls) == 1
        assert close_calls == [fake_handle]
        # channel_sources should contain @target1 → deslop
        kw = process_calls[0]
        assert kw["state"]["blocked_by"] == {}
        assert kw["dry_run"] is True

    def test_blocklist_exclude_file_filters_out_matches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)

        exclude_file = tmp_path / "exclude.txt"
        exclude_file.write_text("@target1\n")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", exclude_file)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        def fake_resolve(src, quiet=False):
            if "exclude" in str(src):
                return ["@target1"]
            return ["@target1", "@target2"]

        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source", fake_resolve)

        process_calls = []
        fake_handle = (None, None, None)
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: fake_handle)
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: process_calls.append((a, kw)))
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser", lambda h: None)

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop", "--dry-run"])
        ydr.main()

        # Only @target2 makes it to process_channels
        args, kw = process_calls[0]
        chs = args[0] if args else kw.get("channel_sources")
        assert "@target2" in chs
        assert "@target1" not in chs

    def test_blocklist_legacy_exclude_file_warns(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)

        legacy = tmp_path / "legacy.txt"
        legacy.write_text("@x\n")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", legacy)

        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: ["@y"])

        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: (None, None, None))
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: None)
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser", lambda h: None)

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop", "--dry-run"])
        with caplog.at_level(logging.WARNING, logger="yt_dont_recommend.cli"):
            ydr.main()
        assert any("deprecated" in r.message for r in caplog.records)

    def test_blocklist_resolve_error_is_skipped(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        def fake_resolve(src, quiet=False):
            raise RuntimeError("cant fetch")
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source", fake_resolve)

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop", "--dry-run"])
        with caplog.at_level(logging.ERROR, logger="yt_dont_recommend.cli"):
            ydr.main()
        assert any("Could not load source" in r.message for r in caplog.records)

    def test_blocklist_open_browser_returns_none(self, tmp_path, monkeypatch):
        """When open_browser returns None (login expired), main returns silently."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: ["@target"])

        pc_calls = []
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: None)
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: pc_calls.append(kw))

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop"])
        ydr.main()
        # process_channels was NOT called because open_browser returned None
        assert pc_calls == []

    def test_blocklist_multi_source_prints_separator(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        call_order = []
        def fake_resolve(src, quiet=False):
            call_order.append(src)
            return [f"@chan-{src}"]
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source", fake_resolve)

        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: (None, None, None))
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: None)
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser", lambda h: None)

        monkeypatch.setattr(sys, "argv",
                            ["ydr", "--blocklist", "--source", "deslop,aislist", "--dry-run"])
        with caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"):
            ydr.main()
        # Both sources resolved
        assert "deslop" in call_order
        assert "aislist" in call_order

    def test_blocklist_growth_notification(self, tmp_path, monkeypatch, caplog):
        """When a source list grows, log a GROWTH notice."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        # Pre-populate state with smaller source size
        state = ydr.load_state()
        state["source_sizes"] = {"deslop": 1}
        ydr.save_state(state)

        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: ["@x", "@y", "@z"])  # grew to 3

        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: (None, None, None))
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: None)
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser", lambda h: None)

        monkeypatch.setattr(sys, "argv",
                            ["ydr", "--blocklist", "--source", "deslop", "--dry-run"])
        with caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"):
            ydr.main()
        assert any("grew" in r.message for r in caplog.records)

    def test_pending_unblock_retried(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        state = ydr.load_state()
        state["pending_unblock"] = {"@retry": {"sources": ["deslop"]}}
        ydr.save_state(state)

        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: [])

        seen_kw = []
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: (None, None, None))
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: seen_kw.append(kw))
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser", lambda h: None)

        monkeypatch.setattr(sys, "argv",
                            ["ydr", "--blocklist", "--source", "deslop"])
        ydr.main()
        assert seen_kw
        assert "@retry" in seen_kw[0]["to_unblock"]

    def test_nothing_to_do_skips_browser(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: [])

        opens = []
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: opens.append(True))

        monkeypatch.setattr(sys, "argv",
                            ["ydr", "--blocklist", "--source", "deslop"])
        with caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"):
            ydr.main()
        assert opens == []
        assert any("Nothing to do" in r.message for r in caplog.records)


class TestMainAutoUpgradeInFlow:
    def test_auto_upgrade_invoked_when_flag_and_new_version(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        # Seed state with auto_upgrade=True
        state = ydr.load_state()
        state["auto_upgrade"] = True
        ydr.save_state(state)

        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update",
                            lambda state, force=False: "99.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: [])
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: None)

        upgrade_calls = []
        monkeypatch.setattr("yt_dont_recommend.cli.do_auto_upgrade",
                            lambda s: upgrade_calls.append(True))

        monkeypatch.setattr(sys, "argv",
                            ["ydr", "--blocklist", "--source", "deslop"])
        ydr.main()
        assert upgrade_calls == [True]


class TestMainAttentionExit:
    def test_exit_one_when_had_attention(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: [])
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: None)

        # Force _had_attention to True
        import yt_dont_recommend.state as state_mod
        monkeypatch.setattr(state_mod, "_had_attention", True)

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop"])
        with pytest.raises(SystemExit) as exc:
            ydr.main()
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# Extra coverage targets
# ---------------------------------------------------------------------------

class TestPreviousVersionRotation:
    def test_previous_version_rotated_when_current_changes(self, tmp_path, monkeypatch):
        """When state has a current_version different from the running version,
        rotate it into previous_version."""
        import json as _json
        sf = tmp_path / "state.json"
        sf.write_text(_json.dumps({
            "blocked_by": {},
            "current_version": "0.9.0",  # older recorded version
        }))
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", sf)
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli._get_current_version", lambda: "1.0.0")
        monkeypatch.setattr(sys, "argv", ["ydr", "--stats"])
        ydr.main()
        loaded = ydr.load_state()
        assert loaded["previous_version"] == "0.9.0"
        assert loaded["current_version"] == "1.0.0"


class TestResetStateNoFile:
    def test_reset_state_when_no_file_after_monkeypatched_save(self, tmp_path, monkeypatch, caplog):
        """When save_state is stubbed out, the state file never gets created,
        so --reset-state hits the 'No state file' branch."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        # Stub save_state so the version-tracking block doesn't create the file.
        monkeypatch.setattr("yt_dont_recommend.cli.save_state", lambda s: None)
        monkeypatch.setattr(sys, "argv", ["ydr", "--reset-state"])
        with caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"):
            ydr.main()
        assert any("No state file to reset" in r.message for r in caplog.records)


class TestBlocklistDefaultSources:
    def test_no_source_flag_uses_default_sources(self, tmp_path, monkeypatch):
        """--blocklist without --source should iterate DEFAULT_SOURCES."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        calls = []
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: calls.append(src) or [])

        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: None)
        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--dry-run"])
        ydr.main()

        # Should have iterated each DEFAULT_SOURCES entry (deslop, aislist, …)
        from yt_dont_recommend.config import DEFAULT_SOURCES
        for src in DEFAULT_SOURCES:
            assert src in calls


class TestBlocklistExplicitExclude:
    def test_explicit_exclude_flag_sets_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        # Default files don't exist → the flag path should win
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        resolved_args = []
        def fake_resolve(src, quiet=False):
            resolved_args.append(src)
            if src == "/explicit/excludes.txt":
                return ["@excluded"]
            return ["@excluded", "@other"]
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source", fake_resolve)
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: None)

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop",
                                           "--exclude", "/explicit/excludes.txt", "--dry-run"])
        ydr.main()
        assert "/explicit/excludes.txt" in resolved_args


class TestPendingUnblockDedup:
    def test_pending_unblock_not_duplicated_with_check_removals(self, tmp_path, monkeypatch):
        """If a channel ends up both in check_removals() output and in
        pending_unblock, it is only queued once."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_BLOCKLIST_EXCLUDE_FILE", tmp_path / "nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", tmp_path / "cb-nope.txt")
        monkeypatch.setattr("yt_dont_recommend.cli._LEGACY_EXCLUDE_FILE", tmp_path / "legacy-nope.txt")

        # Seed state: @retry is in pending_unblock; blocked_by has it blocked by "deslop"
        state = ydr.load_state()
        state["pending_unblock"] = {"@retry": {"sources": ["deslop"]}}
        state["blocked_by"] = {"@retry": {"sources": ["deslop"]}}
        ydr.save_state(state)

        # Resolve returns empty list (channel removed from source) so check_removals
        # will also return @retry.
        monkeypatch.setattr("yt_dont_recommend.cli.resolve_source",
                            lambda src, quiet=False: [])

        seen = []
        monkeypatch.setattr("yt_dont_recommend.browser.open_browser",
                            lambda headless=False: (None, None, None))
        monkeypatch.setattr("yt_dont_recommend.browser.process_channels",
                            lambda *a, **kw: seen.append(kw))
        monkeypatch.setattr("yt_dont_recommend.browser.close_browser", lambda h: None)

        monkeypatch.setattr(sys, "argv", ["ydr", "--blocklist", "--source", "deslop"])
        ydr.main()

        assert seen
        to_unblock = seen[0]["to_unblock"]
        # Only one @retry, not duplicated
        assert to_unblock.count("@retry") == 1


class TestClickbaitOllamaListFailure:
    def test_ollama_list_raises_logs_warning_but_continues(self, tmp_path, monkeypatch, caplog):
        """When ollama.list() raises, main() logs a warning and continues."""
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)

        mock_ollama = MagicMock()
        mock_ollama.list.side_effect = RuntimeError("ollama unreachable")

        open_calls = []
        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg(auto_pull=False)),
            patch("yt_dont_recommend.browser.open_browser",
                  side_effect=lambda headless=False: open_calls.append(True) or None),
            caplog.at_level(logging.WARNING, logger="yt_dont_recommend.cli"),
        ):
            monkeypatch.setattr(sys, "argv", ["ydr", "--clickbait", "--dry-run"])
            ydr.main()
        # Preflight warning was logged, but browser was still attempted
        assert any("Could not verify ollama model availability" in r.message
                   for r in caplog.records)
        assert open_calls == [True]


class TestClickbaitExcludePaths:
    def _base_patches(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr("yt_dont_recommend.cli.setup_logging", lambda verbose=False: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_attention_flag", lambda: None)
        monkeypatch.setattr("yt_dont_recommend.cli.check_for_update", lambda state, force=False: None)

        mock_ollama = MagicMock()
        mock_ollama.list.return_value = _ollama_list(["llama3.1"])  # default title model prefix
        return mock_ollama

    def test_explicit_clickbait_exclude_flag(self, tmp_path, monkeypatch, caplog):
        mock_ollama = self._base_patches(tmp_path, monkeypatch)
        excl = tmp_path / "cb-excludes.txt"
        excl.write_text("@skipme\n")
        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg(auto_pull=False)),
            patch("yt_dont_recommend.browser.open_browser", return_value=None),
            caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"),
        ):
            monkeypatch.setattr(sys, "argv",
                                ["ydr", "--clickbait", "--clickbait-exclude", str(excl), "--dry-run"])
            ydr.main()
        assert any("Loaded" in r.message and "clickbait exclusion" in r.message
                   for r in caplog.records)

    def test_default_clickbait_exclude_file(self, tmp_path, monkeypatch, caplog):
        mock_ollama = self._base_patches(tmp_path, monkeypatch)
        default = tmp_path / "default-cb.txt"
        default.write_text("@skipme\n")
        monkeypatch.setattr("yt_dont_recommend.cli.DEFAULT_CLICKBAIT_EXCLUDE_FILE", default)

        with (
            patch.dict("sys.modules", {"ollama": mock_ollama}),
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg(auto_pull=False)),
            patch("yt_dont_recommend.browser.open_browser", return_value=None),
            caplog.at_level(logging.INFO, logger="yt_dont_recommend.cli"),
        ):
            monkeypatch.setattr(sys, "argv", ["ydr", "--clickbait", "--dry-run"])
            ydr.main()
        assert any("default clickbait exclude file" in r.message
                   for r in caplog.records)

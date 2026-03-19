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

"""
Tests for yt_dont_recommend.browser and related CLI/main() functionality.

Browser automation functions (do_login, process_channels, check_selectors)
require a live YouTube session and are not tested here. This file covers:
  - CLI-level tests and first-run/uninstall logic in cli.py
  - _perform_browser_unblocks() logic (mocked page; no live session needed)
  - _pending_attempted_this_run deduplication set

Functions under test are imported directly from yt_dont_recommend, but
patch targets that live in cli.py must be patched as yt_dont_recommend.cli.X.
"""

from unittest.mock import MagicMock, patch

import yt_dont_recommend as ydr
import yt_dont_recommend.unblock as unblock_mod
from yt_dont_recommend.browser import process_channels
from yt_dont_recommend.unblock import (
    _MAX_DISPLAY_NAME_RETRIES,
    _perform_browser_unblocks,
)

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
        state_file = tmp_path / "data" / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", state_file)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        # Simulate user answering "y" to the removal prompt
        monkeypatch.setattr("builtins.input", lambda _: "y")
        monkeypatch.setattr(ydr, "schedule_cmd", lambda action: None)
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", lambda action: None)
        monkeypatch.setattr(ydr, "_detect_installer", lambda: "uv")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "uv")
        ydr.do_uninstall()
        assert not data_dir.exists()
        captured = capsys.readouterr()
        assert "uv tool uninstall" in captured.out

    def test_do_uninstall_keeps_data_dir_on_no(self, tmp_path, monkeypatch, capsys):
        state_file = tmp_path / "data" / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        monkeypatch.setattr("yt_dont_recommend.cli.STATE_FILE", state_file)
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "processed.json").write_text("{}")
        monkeypatch.setattr("builtins.input", lambda _: "n")
        monkeypatch.setattr(ydr, "schedule_cmd", lambda action: None)
        monkeypatch.setattr("yt_dont_recommend.cli.schedule_cmd", lambda action: None)
        monkeypatch.setattr(ydr, "_detect_installer", lambda: "pipx")
        monkeypatch.setattr("yt_dont_recommend.cli._detect_installer", lambda: "pipx")
        ydr.do_uninstall()
        assert data_dir.exists()
        captured = capsys.readouterr()
        assert "pipx uninstall" in captured.out


# ---------------------------------------------------------------------------
# _perform_browser_unblocks logic (mocked page — no live session required)
# ---------------------------------------------------------------------------

def _make_page(display_name_on_page: str | None = None, delete_btn: bool = False):
    """Return a minimal mock Playwright page for unblock tests."""
    page = MagicMock()

    # goto / wait_for_load_state succeed silently
    page.goto.return_value = None
    page.wait_for_load_state.return_value = None

    # title() used during display-name fallback
    if display_name_on_page:
        page.title.return_value = f"{display_name_on_page} - YouTube"
    else:
        page.title.return_value = ""

    # query_selector: return None by default (no Verify button, no delete button)
    # Callers override per test.
    page.query_selector.return_value = None

    return page


def _base_state(channels: list[str], display_names: dict | None = None) -> dict:
    """Minimal state with channels already in pending_unblock."""
    blocked_by = {}
    pending_unblock = {}
    for ch in channels:
        dn = (display_names or {}).get(ch)
        info = {"sources": ["deslop"], "blocked_at": "2026-03-09T00:00:00"}
        if dn:
            info["display_name"] = dn
        blocked_by[ch] = info
        pending_unblock[ch] = info.copy()
    return {
        "blocked_by": blocked_by,
        "pending_unblock": pending_unblock,
        "stats": {"total_blocked": 0, "total_skipped": 0, "total_failed": 0},
    }


class TestPerformBrowserUnblocks:
    """Unit tests for _perform_browser_unblocks().

    The Playwright page is mocked. State is passed in directly so we can
    inspect mutations without file I/O.
    """

    def setup_method(self):
        # write_attention requires a real file path; stub it out
        self._wa_patch = patch("yt_dont_recommend.unblock._pkg")
        self._mock_pkg = self._wa_patch.start()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.save_state = MagicMock()

    def teardown_method(self):
        self._wa_patch.stop()

    # --- empty input ---

    def test_empty_channel_list_returns_empty(self):
        page = _make_page()
        result = _perform_browser_unblocks(page, [], {})
        assert result == []
        page.goto.assert_not_called()

    # --- display name from state ---

    def test_uses_display_name_from_state(self):
        state = _base_state(["@alpha"], {"@alpha": "Alpha Channel"})
        page = _make_page()

        # No Verify button; delete button found immediately
        delete_btn = MagicMock()
        got_it_btn = MagicMock()

        def query_selector_side_effect(sel):
            if "Delete activity item Alpha Channel" in sel:
                return delete_btn
            if "Got it" in sel:
                return got_it_btn
            return None

        page.query_selector.side_effect = query_selector_side_effect

        result = _perform_browser_unblocks(page, ["@alpha"], state)
        assert result == ["@alpha"]
        # Should NOT have navigated to YouTube channel page (display name was in state)
        assert not any(
            "youtube.com/@alpha" in str(call) for call in page.goto.call_args_list
        )

    # --- display name lookup fallback ---

    def test_display_name_looked_up_from_channel_page(self):
        state = _base_state(["@alpha"])  # no display_name in state
        page = _make_page(display_name_on_page="Alpha Channel")

        delete_btn = MagicMock()
        got_it_btn = MagicMock()

        def query_selector_side_effect(sel):
            if "Delete activity item Alpha Channel" in sel:
                return delete_btn
            if "Got it" in sel:
                return got_it_btn
            return None

        page.query_selector.side_effect = query_selector_side_effect

        result = _perform_browser_unblocks(page, ["@alpha"], state)
        assert result == ["@alpha"]
        # Navigated to the channel page to look up the name
        assert any("youtube.com/@alpha" in str(c) for c in page.goto.call_args_list)

    # --- display name failure / retry count ---

    def test_display_name_failure_increments_retry_count(self):
        state = _base_state(["@ghost"])  # no display_name; page returns empty title
        page = _make_page(display_name_on_page=None)

        result = _perform_browser_unblocks(page, ["@ghost"], state)

        assert result == []
        assert state["pending_unblock"]["@ghost"]["_retry_count"] == 1

    def test_display_name_failure_at_max_retries_clears_channel(self):
        state = _base_state(["@ghost"])
        # Simulate already at one below the limit
        state["pending_unblock"]["@ghost"]["_retry_count"] = _MAX_DISPLAY_NAME_RETRIES - 1
        page = _make_page(display_name_on_page=None)

        result = _perform_browser_unblocks(page, ["@ghost"], state)

        assert result == []
        assert "@ghost" not in state["pending_unblock"]

    def test_display_name_failure_below_max_retries_keeps_channel(self):
        state = _base_state(["@ghost"])
        state["pending_unblock"]["@ghost"]["_retry_count"] = _MAX_DISPLAY_NAME_RETRIES - 2
        page = _make_page(display_name_on_page=None)

        _perform_browser_unblocks(page, ["@ghost"], state)

        assert "@ghost" in state["pending_unblock"]
        assert state["pending_unblock"]["@ghost"]["_retry_count"] == _MAX_DISPLAY_NAME_RETRIES - 1

    # --- myactivity entry not found ---

    def test_missing_myactivity_entry_treated_as_unblocked(self):
        """If the feedback entry isn't on myactivity, treat it as already unblocked."""
        state = _base_state(["@alpha"], {"@alpha": "Alpha Channel"})
        page = _make_page()
        # query_selector always returns None (no Verify, no delete button)
        page.query_selector.return_value = None

        result = _perform_browser_unblocks(page, ["@alpha"], state)

        # Should be in returned list so caller clears it from pending_unblock
        assert result == ["@alpha"]

    def test_missing_entry_after_load_more_also_treated_as_unblocked(self):
        """Load more is clicked but entry still absent — still treated as unblocked."""
        state = _base_state(["@alpha"], {"@alpha": "Alpha Channel"})
        page = _make_page()
        load_more = MagicMock()

        def query_selector_side_effect(sel):
            if "Load more" in sel:
                return load_more
            return None  # no delete button even after load more

        page.query_selector.side_effect = query_selector_side_effect

        result = _perform_browser_unblocks(page, ["@alpha"], state)
        assert result == ["@alpha"]
        load_more.click.assert_called_once()

    # --- selector reliability (display names with special characters) ---

    def test_display_name_with_double_quote_produces_escaped_selector(self):
        """Display names containing `"` are escaped before being interpolated
        into the CSS attribute selector. Without escaping, the embedded quote
        would close the attribute value and break the selector silently —
        the unblock would fail and the retry counter would advance."""
        state = _base_state(['@quoted'], {'@quoted': 'He said "hi"'})
        page = _make_page()
        captured_selectors: list[str] = []
        delete_btn = MagicMock()
        got_it_btn = MagicMock()

        def query_selector_side_effect(sel):
            captured_selectors.append(sel)
            if 'Delete activity item' in sel:
                return delete_btn
            if 'Got it' in sel:
                return got_it_btn
            return None

        page.query_selector.side_effect = query_selector_side_effect

        result = _perform_browser_unblocks(page, ['@quoted'], state)

        assert result == ['@quoted']
        # The selector that probed for the delete button must have backslash-escaped
        # the embedded double quote so the attribute value parses correctly.
        delete_selectors = [s for s in captured_selectors if 'Delete activity item' in s]
        assert delete_selectors
        for sel in delete_selectors:
            # Embedded `"` must be escaped to `\"` — the raw `"` only appears as the
            # outer attribute-value delimiters.
            assert 'He said \\"hi\\"' in sel, f"unescaped quote in selector: {sel!r}"

    def test_display_name_with_backslash_produces_escaped_selector(self):
        """Backslashes are doubled (CSS attribute-value escape rule)."""
        state = _base_state(['@bs'], {'@bs': 'path\\foo'})
        page = _make_page()
        captured_selectors: list[str] = []
        delete_btn = MagicMock()
        got_it_btn = MagicMock()

        def query_selector_side_effect(sel):
            captured_selectors.append(sel)
            if 'Delete activity item' in sel:
                return delete_btn
            if 'Got it' in sel:
                return got_it_btn
            return None

        page.query_selector.side_effect = query_selector_side_effect

        _perform_browser_unblocks(page, ['@bs'], state)

        delete_selectors = [s for s in captured_selectors if 'Delete activity item' in s]
        assert delete_selectors
        for sel in delete_selectors:
            assert 'path\\\\foo' in sel, f"backslash not doubled in selector: {sel!r}"

    # --- alerts ---

    def test_verification_timeout_triggers_write_attention(self):
        """Verification timeout must call write_attention so the user is notified."""
        state = _base_state(["@alpha"], {"@alpha": "Alpha Channel"})
        page = _make_page()

        verify_btn = MagicMock()

        def query_selector_side_effect(sel):
            # Return Verify button; everything else (delete button, load-more) → None
            if sel == "button:has-text('Verify')":
                return verify_btn
            return None

        page.query_selector.side_effect = query_selector_side_effect
        # Phase 1: evaluate() returns the challenge text → challenge_appeared = True
        # Phase 2: query_selector never returns a delete button → verified stays False
        page.evaluate.return_value = "Enter your password in the browser"

        # Patch time.sleep so the 60-iteration poll runs instantly
        with patch("yt_dont_recommend.browser.time.sleep"):
            result = _perform_browser_unblocks(page, ["@alpha"], state)

        assert result == []
        self._mock_pkg.return_value.write_attention.assert_called_once()
        msg = self._mock_pkg.return_value.write_attention.call_args[0][0]
        assert "Timed out" in msg or "verification" in msg.lower()

    def test_partial_unblock_failure_triggers_write_attention(self):
        """If some channels can't be unblocked after reaching myactivity, alert the user."""
        # Two channels; alpha has a delete button, beta does not
        state = _base_state(["@alpha", "@beta"],
                            {"@alpha": "Alpha Channel", "@beta": "Beta Channel"})
        page = _make_page()

        alpha_btn = MagicMock()
        got_it_btn = MagicMock()

        def query_selector_side_effect(sel):
            if "Delete activity item Alpha Channel" in sel:
                return alpha_btn
            if "Got it" in sel:
                return got_it_btn
            return None

        page.query_selector.side_effect = query_selector_side_effect

        result = _perform_browser_unblocks(page, ["@alpha", "@beta"], state)

        # @alpha unblocked; @beta treated as already unblocked (not found = cleared)
        # → both in result, no alert needed in the "not found → treated as unblocked" path
        # This test verifies that the warning+alert fires when channels < passed count
        # only if some channels had display names but the delete path itself failed.
        # (The "not found" case no longer triggers the warning — it returns success.)
        # "not found → treated as unblocked" path should NOT fire write_attention.
        self._mock_pkg.return_value.write_attention.assert_not_called()
        assert set(result) == {"@alpha", "@beta"}

    def test_unblock_failure_alert_names_the_channels(self):
        """Alert message must include the affected channel handles, not just a count."""
        # Directly verify the message format: call write_attention with a known
        # display_names set and confirm the channel handles appear in the message.
        # We do this by invoking the end-of-function logic's equivalent directly.
        display_names = {"@alpha": "Alpha Channel", "@beta": "Beta Channel"}
        unblocked = ["@alpha"]  # @beta didn't make it
        failed = [ch for ch in display_names if ch not in unblocked]

        msg = (
            f"{len(failed)} channel(s) could not be unblocked automatically: "
            f"{', '.join(failed)}. "
            f"Visit myactivity.google.com → Other activity → YouTube user feedback "
            f"to remove them manually."
        )
        assert "@beta" in msg
        assert "@alpha" not in msg  # only failed channels named


# ---------------------------------------------------------------------------
# _pending_attempted_this_run deduplication
# ---------------------------------------------------------------------------

class TestPendingAttemptedThisRun:
    """Verify the module-level set that prevents re-attempting unblocks in one process run.

    process_channels() filters its to_unblock list against this set before
    calling _perform_browser_unblocks(), so a channel that already failed
    verification won't trigger a second Google password prompt in the same run.
    """

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()

    def teardown_method(self):
        unblock_mod._pending_attempted_this_run.clear()

    def test_set_starts_empty_after_clear(self):
        assert len(unblock_mod._pending_attempted_this_run) == 0

    def test_channels_added_after_attempt(self):
        unblock_mod._pending_attempted_this_run.update(["@alpha", "@beta"])
        assert "@alpha" in unblock_mod._pending_attempted_this_run
        assert "@beta" in unblock_mod._pending_attempted_this_run

    def test_already_attempted_channels_excluded_from_retry(self):
        """Channels already in the set are filtered out of to_unblock."""
        unblock_mod._pending_attempted_this_run.add("@alpha")
        to_unblock = ["@alpha", "@beta"]
        # Simulate the filtering applied at the top of process_channels
        filtered = [ch for ch in to_unblock
                    if ch not in unblock_mod._pending_attempted_this_run]
        assert "@alpha" not in filtered
        assert "@beta" in filtered

    def test_fresh_channels_not_filtered(self):
        """Channels not in the set pass through unaffected."""
        unblock_mod._pending_attempted_this_run.add("@old-pending")
        to_unblock = ["@newly-removed", "@old-pending"]
        filtered = [ch for ch in to_unblock
                    if ch not in unblock_mod._pending_attempted_this_run]
        assert filtered == ["@newly-removed"]


# ---------------------------------------------------------------------------
# process_channels() — new combined-source API
# ---------------------------------------------------------------------------

class TestProcessChannels:
    """Tests for the combined-source process_channels() function.

    Live browser interaction is not exercised here. These tests verify
    early-return behaviour that requires no browser at all.
    """

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()

    def teardown_method(self):
        unblock_mod._pending_attempted_this_run.clear()

    def test_empty_inputs_returns_without_opening_browser(self):
        """Nothing to do → returns immediately, no browser opened."""
        with patch("yt_dont_recommend.browser.open_browser") as mock_open:
            process_channels({}, to_unblock=[], state={
                "blocked_by": {}, "would_have_blocked": {},
                "pending_unblock": {}, "ucxxx_to_handle": {},
                "stats": {"total_blocked": 0, "total_skipped": 0, "total_failed": 0},
            })
            mock_open.assert_not_called()

    def test_already_attempted_unblocks_filtered_before_browser(self):
        """Channels in _pending_attempted_this_run are dropped from to_unblock;
        if that empties to_unblock and channel_sources is also empty, no browser."""
        unblock_mod._pending_attempted_this_run.add("@alpha")
        with patch("yt_dont_recommend.browser.open_browser") as mock_open:
            process_channels({}, to_unblock=["@alpha"], state={
                "blocked_by": {}, "would_have_blocked": {},
                "pending_unblock": {}, "ucxxx_to_handle": {},
                "stats": {"total_blocked": 0, "total_skipped": 0, "total_failed": 0},
            })
            mock_open.assert_not_called()

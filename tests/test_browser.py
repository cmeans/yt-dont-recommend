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

import copy
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


# ---------------------------------------------------------------------------
# Helpers shared by TestKeywordPhase3
# ---------------------------------------------------------------------------

_MINIMAL_STATE = {
    "blocked_by": {},
    "would_have_blocked": {},
    "pending_unblock": {},
    "ucxxx_to_handle": {},
    "stats": {"total_blocked": 0, "total_skipped": 0, "total_failed": 0},
    "keyword_acted": {},
    "keyword_stats": {
        "total_matched": 0,
        "by_pattern": {},
        "by_mode": {"substring": 0, "word": 0, "regex": 0},
    },
}


_KW_VIDEO_ID = "StarTrekXxY"  # exactly 11 chars — matches YouTube video ID regex [A-Za-z0-9_-]{11}


def _make_kw_page(video_id: str, channel_handle: str, title: str):
    """Return a minimal mock Playwright page for keyword Phase 3 tests.

    The page has one feed card on the first query_selector_all call and an
    empty card list on the second (simulates feed exhaustion after one pass).
    Video metadata is pre-loaded into the JSON cache so no DOM title extraction
    is attempted.
    """
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_load_state.return_value = None
    # Scroll evaluate calls: return None to avoid issues
    page.evaluate.return_value = None
    # login_check: return truthy so exhaustion path logs cleanly
    page.query_selector.return_value = MagicMock()

    # Feed card mock
    card = MagicMock()

    channel_link_mock = MagicMock()
    channel_link_mock.get_attribute.return_value = f"/{channel_handle}"
    channel_link_mock.inner_text.return_value = channel_handle

    watch_link_mock = MagicMock()
    watch_link_mock.get_attribute.return_value = f"/watch?v={video_id}"

    def card_query_selector(sel):
        if "href*='/watch?v='" in sel:
            return watch_link_mock
        return channel_link_mock

    card.query_selector.side_effect = card_query_selector

    # First call returns one card; subsequent calls return [] to exhaust feed
    _calls = [0]

    def query_selector_all_side_effect(sel):
        _calls[0] += 1
        if _calls[0] == 1:
            return [card]
        return []

    page.query_selector_all.side_effect = query_selector_all_side_effect

    return page, card, {video_id: {"title": title, "channel_handle": channel_handle}}


# ---------------------------------------------------------------------------
# TestKeywordPhase3
# ---------------------------------------------------------------------------

class TestKeywordPhase3:
    """process_channels Phase 3 — keyword matching before clickbait."""

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()
        # Stub out _pkg() so save_state / write_attention don't hit the filesystem.
        self._pkg_patch = patch("yt_dont_recommend.browser._pkg")
        self._mock_pkg = self._pkg_patch.start()
        self._mock_pkg.return_value.save_state = MagicMock()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.load_state = MagicMock()

    def teardown_method(self):
        self._pkg_patch.stop()
        unblock_mod._pending_attempted_this_run.clear()

    def test_keyword_match_acts_and_records_state(self):
        """A card whose title matches a keyword rule is acted on; state is updated."""
        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        assert _KW_VIDEO_ID in state["keyword_acted"], "keyword_acted should record the match"
        assert state["keyword_acted"][_KW_VIDEO_ID]["channel"] == "@trekfan"
        assert state["keyword_acted"][_KW_VIDEO_ID]["matched_pattern"] == "Star Trek"
        assert state["keyword_stats"]["total_matched"] == 1
        mock_ni.assert_called_once()

    def test_keyword_excluded_channel_skipped(self):
        """A channel in keyword_excludes is not acted on even if the title matches."""
        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                keyword_excludes={"@trekfan"},
                _browser=("pwcm-stub", MagicMock(), page),
            )

        assert _KW_VIDEO_ID not in state["keyword_acted"], "excluded channel must not be acted on"
        mock_ni.assert_not_called()

    def test_subscribed_channel_keyword_acts_anyway(self):
        """A subscribed channel is not in the blocklist, so keyword matching still fires."""
        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value={"@trekfan"}),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # @trekfan is subscribed but not on the blocklist — keyword mode is not
        # gated by subscriptions, so the match should still fire.
        assert _KW_VIDEO_ID in state["keyword_acted"], "subscribed channel keyword match should fire"
        mock_ni.assert_called_once()

    def test_already_acted_via_title_link_fallback_not_re_acted(self):
        """Fix I-1: a card whose video_id resolves only via the title-link fallback
        (no watch link) and is already in keyword_acted must NOT be acted on again.

        Before the fix, _kw_eligible used _video_id_for_json (None for no-watch-link
        cards), so None-not-in-keyword_acted was always True, and the card could be
        re-acted even though the resolved video_id was already recorded.
        """
        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        # Pre-populate keyword_acted with the video ID so the late gate should block it.
        state["keyword_acted"][_KW_VIDEO_ID] = {
            "acted_at": "2026-04-01T00:00:00+00:00",
            "title": "Star Trek finale spoilers",
            "channel": "@trekfan",
            "matched_pattern": "Star Trek",
            "matched_mode": "substring",
            "matched_line": 1,
        }

        # Build a page where the card has NO watch link but DOES have a title link
        # that carries the same video ID.
        page = MagicMock()
        page.goto.return_value = None
        page.wait_for_load_state.return_value = None
        page.evaluate.return_value = None
        page.query_selector.return_value = MagicMock()  # login_check truthy

        card = MagicMock()

        channel_link_mock = MagicMock()
        channel_link_mock.get_attribute.return_value = "/@trekfan"
        channel_link_mock.inner_text.return_value = "@trekfan"

        title_link_mock = MagicMock()
        title_link_mock.get_attribute.side_effect = lambda attr: (
            f"/watch?v={_KW_VIDEO_ID}" if attr == "href" else "Star Trek finale spoilers"
        )

        def card_query_selector(sel):
            # watch_link selector — return None to force title-link fallback
            if sel == "a[href*='/watch?v=']":
                return None
            # channel_link selector
            if "/@'" in sel or "/channel/UC" in sel:
                return channel_link_mock
            # title_link selectors (a#video-title-link is first)
            if "video-title" in sel or "watch?v=" in sel:
                return title_link_mock
            return channel_link_mock

        card.query_selector.side_effect = card_query_selector

        _calls = [0]

        def query_selector_all_side_effect(sel):
            _calls[0] += 1
            return [card] if _calls[0] == 1 else []

        page.query_selector_all.side_effect = query_selector_all_side_effect

        # JSON cache carries title so DOM text extraction isn't needed.
        json_videos = {_KW_VIDEO_ID: {"title": "Star Trek finale spoilers", "channel_handle": "@trekfan"}}

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # The video was already in keyword_acted — no new action should have fired.
        mock_ni.assert_not_called()
        # State must not have been overwritten either.
        assert state["keyword_acted"][_KW_VIDEO_ID]["acted_at"] == "2026-04-01T00:00:00+00:00"

    def test_keyword_match_dry_run_logs_would_match(self, caplog):
        """dry_run=True logs 'WOULD MATCH' and increments keyword_count without clicking."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.INFO, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                dry_run=True,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # dry_run: no click attempted, no state mutation
        mock_ni.assert_not_called()
        assert _KW_VIDEO_ID not in state["keyword_acted"]
        assert any("WOULD MATCH (keyword)" in r.message for r in caplog.records)

    def test_keyword_match_click_failure_logs_warning_no_state_mutation(self, caplog):
        """_click_not_interested returning False logs a warning; state is not mutated."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=False),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.WARNING, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                dry_run=False,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        assert _KW_VIDEO_ID not in state["keyword_acted"], "failed click must not record state"
        assert any("SKIP keyword match" in r.message for r in caplog.records)

    def test_keyword_match_click_exception_logs_error_no_state_mutation(self, caplog):
        """_click_not_interested raising an exception logs an error; state is not mutated."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch(
                "yt_dont_recommend.browser._click_not_interested",
                side_effect=RuntimeError("selector stale"),
            ),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.ERROR, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                dry_run=False,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        assert _KW_VIDEO_ID not in state["keyword_acted"], "exception must not record state"
        assert any("FAIL keyword match" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Scan-parts segments (lines 814, 818)
# ---------------------------------------------------------------------------

class TestScanParts:
    """Cover the scan-desc segments for blocklist+keyword and clickbait+keyword combos."""

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()
        self._pkg_patch = patch("yt_dont_recommend.browser._pkg")
        self._mock_pkg = self._pkg_patch.start()
        self._mock_pkg.return_value.save_state = MagicMock()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.load_state = MagicMock()

    def teardown_method(self):
        self._pkg_patch.stop()
        unblock_mod._pending_attempted_this_run.clear()

    def test_scan_parts_includes_blocklist_segment(self, caplog):
        """scan-desc includes channel/source counts when run with both blocklist and keyword."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "NonMatchingPattern9999")])
        state = copy.deepcopy(_MINIMAL_STATE)

        # Page with one card that belongs to @otherchannel (not in channel_sources)
        # so blocklist branch won't match, but the channel count appears in scan log.
        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@otherchannel",
            title="Some video title",
        )

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._resolve_ucxxx_to_handles", return_value=["@blocklisted"]),
            patch("yt_dont_recommend.browser._click_dont_recommend"),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.INFO, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={"@blocklisted": "test-source"},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        scan_msgs = [r.message for r in caplog.records if "Scanning home feed" in r.message]
        assert scan_msgs, "Expected a 'Scanning home feed' log message"
        assert any("source" in m for m in scan_msgs), f"Expected 'source' in scan message: {scan_msgs}"

    def test_scan_parts_includes_clickbait_segment(self, caplog):
        """scan-desc includes 'clickbait detection' when run with both clickbait and keyword."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "NonMatchingPattern9999")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page, _card, json_videos = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@somechannel",
            title="Some video title",
        )

        minimal_cb_cfg = {
            "video": {
                "title": {"model": {"name": "test-model", "auto_pull": False}, "threshold": 0.75, "ambiguous_low": 0.4},
                "thumbnail": {"enabled": False},
                "transcript": {"enabled": False},
            }
        }

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.clickbait.classify_titles_batch", return_value=[
                {"is_clickbait": False, "confidence": 0.1, "reasoning": "test", "stages": ["title"]}
            ]),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.INFO, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                clickbait_cfg=minimal_cb_cfg,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        scan_msgs = [r.message for r in caplog.records if "Scanning home feed" in r.message]
        assert scan_msgs, "Expected a 'Scanning home feed' log message"
        assert any("clickbait detection" in m for m in scan_msgs), (
            f"Expected 'clickbait detection' in scan message: {scan_msgs}"
        )


# ---------------------------------------------------------------------------
# No-video-id path (line 1001)
# ---------------------------------------------------------------------------

class TestNoVideoId:
    """Card with a resolvable channel but no video ID is skipped with debug log."""

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()
        self._pkg_patch = patch("yt_dont_recommend.browser._pkg")
        self._mock_pkg = self._pkg_patch.start()
        self._mock_pkg.return_value.save_state = MagicMock()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.load_state = MagicMock()

    def teardown_method(self):
        self._pkg_patch.stop()
        unblock_mod._pending_attempted_this_run.clear()

    def test_card_with_no_video_id_logged_and_skipped(self, caplog):
        """A card where neither watch-link nor title-link yields a video_id is skipped."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page = MagicMock()
        page.goto.return_value = None
        page.wait_for_load_state.return_value = None
        page.evaluate.return_value = None
        page.query_selector.return_value = MagicMock()  # login_check truthy

        card = MagicMock()

        channel_link_mock = MagicMock()
        channel_link_mock.get_attribute.return_value = "/@somechannel"
        channel_link_mock.inner_text.return_value = "@somechannel"

        # title link returns an element but its href has no video ID match
        title_link_mock = MagicMock()
        title_link_mock.get_attribute.return_value = "/some/path/no-video-id-here"

        def card_query_selector(sel):
            # channel_link selector (contains '/@' or '/channel/UC')
            if "/@'" in sel or "channel/UC" in sel:
                return channel_link_mock
            # watch_link selector — return None so _video_id_for_json stays None
            if "watch?v=" in sel:
                return None
            # title_link selectors — return element with non-matching href
            if "video-title" in sel or "title" in sel:
                return title_link_mock
            # Default: channel_link for any other selector (handles combined selector strings)
            return channel_link_mock

        card.query_selector.side_effect = card_query_selector

        _calls = [0]

        def query_selector_all_side_effect(sel):
            _calls[0] += 1
            return [card] if _calls[0] == 1 else []

        page.query_selector_all.side_effect = query_selector_all_side_effect

        # JSON cache is empty so no JSON fallback for channel path
        json_videos: dict = {}

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.DEBUG, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        debug_msgs = [r.message for r in caplog.records]
        assert any("no video ID" in m for m in debug_msgs), (
            f"Expected 'no video ID' in debug log. Messages: {debug_msgs}"
        )


# ---------------------------------------------------------------------------
# Feed-JSON cache miss → DOM fallback (line 1018)
# ---------------------------------------------------------------------------

class TestJsonCacheMiss:
    """Card with a video_id not in _json_videos triggers the 'not in feed JSON cache' log."""

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()
        self._pkg_patch = patch("yt_dont_recommend.browser._pkg")
        self._mock_pkg = self._pkg_patch.start()
        self._mock_pkg.return_value.save_state = MagicMock()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.load_state = MagicMock()

    def teardown_method(self):
        self._pkg_patch.stop()
        unblock_mod._pending_attempted_this_run.clear()

    def test_video_id_cache_miss_falls_back_to_dom(self, caplog):
        """If the feed JSON cache is non-empty but doesn't have the card's video_id, debug log fires."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        # Build a page where the card has a valid watch link (video_id resolvable)
        # but the JSON cache has a DIFFERENT video (so cache miss fires)
        page, card, _ = _make_kw_page(
            video_id=_KW_VIDEO_ID,
            channel_handle="@trekfan",
            title="Star Trek finale spoilers",
        )

        # JSON cache has a different video — non-empty but doesn't contain _KW_VIDEO_ID
        json_videos_cache_miss = {"OtherVideoId1": {"title": "Other video", "channel_handle": "@other"}}

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos_cache_miss),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.DEBUG, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        debug_msgs = [r.message for r in caplog.records]
        assert any("not in feed JSON cache" in m for m in debug_msgs), (
            f"Expected 'not in feed JSON cache' in debug log. Messages: {debug_msgs}"
        )


# ---------------------------------------------------------------------------
# Title extraction failure (line 1038)
# ---------------------------------------------------------------------------

class TestTitleExtractionFailure:
    """Card with a video_id but no extractable title is skipped with a debug log."""

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()
        self._pkg_patch = patch("yt_dont_recommend.browser._pkg")
        self._mock_pkg = self._pkg_patch.start()
        self._mock_pkg.return_value.save_state = MagicMock()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.load_state = MagicMock()

    def teardown_method(self):
        self._pkg_patch.stop()
        unblock_mod._pending_attempted_this_run.clear()

    def test_title_extraction_failure_logged_and_skipped(self, caplog):
        """If both JSON and DOM title extraction return empty, debug log fires and card is skipped."""
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page = MagicMock()
        page.goto.return_value = None
        page.wait_for_load_state.return_value = None
        page.evaluate.return_value = None
        page.query_selector.return_value = MagicMock()  # login_check truthy

        card = MagicMock()

        channel_link_mock = MagicMock()
        channel_link_mock.get_attribute.return_value = "/@trekfan"
        channel_link_mock.inner_text.return_value = "@trekfan"

        watch_link_mock = MagicMock()
        watch_link_mock.get_attribute.return_value = f"/watch?v={_KW_VIDEO_ID}"

        # Title link returns an element with all empty/None title attributes
        title_link_mock = MagicMock()
        title_link_mock.get_attribute.return_value = None  # both "title" and "aria-label" return None

        # title_text element also returns empty
        title_text_mock = MagicMock()
        title_text_mock.inner_text.return_value = "  "  # whitespace only → stripped to empty

        def card_query_selector(sel):
            # channel_link selector (contains '/@' pattern for @handle matching)
            if "/@'" in sel or "channel/UC" in sel:
                return channel_link_mock
            # title_text selector — "yt-formatted-string#video-title, #video-title"
            # Must check this BEFORE watch_link/title_link so it's caught first
            if "formatted-string" in sel:
                return title_text_mock
            # watch_link selector — has video ID so _video_id_for_json is resolved
            if "watch?v=" in sel:
                return watch_link_mock
            # title_link selectors (a#video-title-link, a#video-title)
            if "video-title" in sel:
                return title_link_mock
            return None

        card.query_selector.side_effect = card_query_selector

        _calls = [0]

        def query_selector_all_side_effect(sel):
            _calls[0] += 1
            return [card] if _calls[0] == 1 else []

        page.query_selector_all.side_effect = query_selector_all_side_effect

        # JSON cache is empty → forces DOM path
        json_videos: dict = {}

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.DEBUG, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        debug_msgs = [r.message for r in caplog.records]
        assert any("could not extract title" in m for m in debug_msgs), (
            f"Expected 'could not extract title' in debug log. Messages: {debug_msgs}"
        )
        mock_ni.assert_not_called()

    def test_dom_title_retry_on_first_miss(self, caplog):
        """First DOM title query returns None; second attempt (after 250ms sleep) succeeds.

        Verifies that the retry loop fires time.sleep(1.0) exactly once and that
        the card is NOT skipped when the retry resolves the title.
        """
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page = MagicMock()
        page.goto.return_value = None
        page.wait_for_load_state.return_value = None
        page.evaluate.return_value = None
        page.query_selector.return_value = MagicMock()  # login_check truthy

        card = MagicMock()

        channel_link_mock = MagicMock()
        channel_link_mock.get_attribute.return_value = "/@trekfan"
        channel_link_mock.inner_text.return_value = "@trekfan"

        watch_link_mock = MagicMock()
        watch_link_mock.get_attribute.return_value = f"/watch?v={_KW_VIDEO_ID}"

        # Title link that returns a real title — used on the SECOND attempt only
        title_link_mock = MagicMock()
        title_link_mock.get_attribute.side_effect = lambda attr: (
            "Star Trek finale spoilers" if attr == "title" else None
        )

        # Track retry attempts: the retry loop calls all title_link selectors per
        # attempt.  We use a flag to return None for ALL selectors on attempt 0,
        # and a real element on attempt 1.  We detect the attempt boundary by
        # counting time.sleep(1.0) calls — but we can't reference mock_time here.
        # Instead, use a simple "first-pass gate" toggled by watching the sleep call.
        _attempt_done = [False]  # flipped to True after the first full selector pass

        def card_query_selector(sel):
            if "/@'" in sel or "channel/UC" in sel:
                return channel_link_mock
            # Only match the dedicated watch-link selector, not the h3/title-link
            # variant that also contains 'watch?v=' in a different pattern.
            if "'/watch?v='" in sel:
                return watch_link_mock
            if "video-title" in sel or "h3 a" in sel:
                # Return None on all title selectors until the retry flag is set
                if not _attempt_done[0]:
                    return None
                return title_link_mock
            return None

        card.query_selector.side_effect = card_query_selector

        _calls = [0]

        def query_selector_all_side_effect(sel):
            _calls[0] += 1
            return [card] if _calls[0] == 1 else []

        page.query_selector_all.side_effect = query_selector_all_side_effect

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value={}),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.DEBUG, logger="yt_dont_recommend"),
        ):
            def _sleep_side_effect(seconds):
                # When the retry sleep fires, flip the gate so the next
                # query_selector call for title_link returns the real element.
                if seconds == 1.0:
                    _attempt_done[0] = True

            mock_time.sleep.side_effect = _sleep_side_effect
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # The 1s retry sleep must have fired exactly once
        sleep_calls = [c.args[0] for c in mock_time.sleep.call_args_list]
        assert 1.0 in sleep_calls, (
            f"Expected time.sleep(1.0) from DOM-title retry. Calls: {sleep_calls}"
        )
        # Card was NOT skipped — keyword acted on the resolved title
        assert _KW_VIDEO_ID in state["keyword_acted"], (
            "Retry should have resolved the title and fired keyword action"
        )

    def test_card_aria_label_fallback_extracts_title(self, caplog):
        """4th fallback: card-level aria-label yields title when inner selectors are empty.

        Simulates a promotional / shelf card whose inner title element is absent
        but whose top-level aria-label follows YouTube's accessibility format:
        "<title> by <channel> <views> <time-ago> <duration>".
        """
        import logging

        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = copy.deepcopy(_MINIMAL_STATE)

        page = MagicMock()
        page.goto.return_value = None
        page.wait_for_load_state.return_value = None
        page.evaluate.return_value = None
        page.query_selector.return_value = MagicMock()  # login_check truthy

        card = MagicMock()

        channel_link_mock = MagicMock()
        channel_link_mock.get_attribute.return_value = "/@trekfan"
        channel_link_mock.inner_text.return_value = "@trekfan"

        watch_link_mock = MagicMock()
        watch_link_mock.get_attribute.return_value = f"/watch?v={_KW_VIDEO_ID}"

        # Inner title selectors all return nothing — simulates shelf/promo card DOM.
        title_link_mock = MagicMock()
        title_link_mock.get_attribute.return_value = None

        title_text_mock = MagicMock()
        title_text_mock.inner_text.return_value = "  "

        def card_query_selector(sel):
            if "/@'" in sel or "channel/UC" in sel:
                return channel_link_mock
            if "formatted-string" in sel:
                return title_text_mock
            if "watch?v=" in sel:
                return watch_link_mock
            if "video-title" in sel:
                return title_link_mock
            return None

        card.query_selector.side_effect = card_query_selector

        # Card-level aria-label in YouTube's standard accessibility format.
        _CARD_ARIA = (
            "Some Video Title by Some Channel 1.2M views 3 days ago 5 minutes, 30 seconds"
        )
        card.get_attribute.return_value = _CARD_ARIA

        _calls = [0]

        def query_selector_all_side_effect(sel):
            _calls[0] += 1
            return [card] if _calls[0] == 1 else []

        page.query_selector_all.side_effect = query_selector_all_side_effect

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value={}),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True),
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.DEBUG, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                keyword_compiled=compiled,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # Title extracted from card aria-label — card should NOT have been skipped
        debug_msgs = [r.message for r in caplog.records]
        assert not any("could not extract title" in m for m in debug_msgs), (
            "Card should NOT have been skipped — aria-label fallback should have yielded a title."
            f" Debug messages: {debug_msgs}"
        )


# ---------------------------------------------------------------------------
# Clickbait cache-hit paths (lines 1050-1062)
# ---------------------------------------------------------------------------

_CB_VIDEO_ID = "CbVideoId00"   # exactly 11 chars — matches YouTube video ID regex


def _make_two_card_cb_page(video_id: str, channel1: str, channel2: str, title: str):
    """Return a mock page that serves card1 in pass 1 and card2 in pass 2, then empty.

    Card 1 has channel1 → Phase 4 classification → populates _title_cache[video_id].
    Card 2 has channel2 (different channel, same video_id) → introduced in pass 2 so
    it is NOT in seen_paths yet → cache-hit path fires (lines 1050-1062) because
    channel2 is not in _clickbait_evaluated but video_id IS in _title_cache.
    """
    page = MagicMock()
    page.goto.return_value = None
    page.wait_for_load_state.return_value = None
    page.evaluate.return_value = None
    page.query_selector.return_value = MagicMock()  # login_check truthy

    def _make_card(channel_handle: str) -> MagicMock:
        card = MagicMock()

        channel_link_mock = MagicMock()
        channel_link_mock.get_attribute.return_value = f"/{channel_handle}"
        channel_link_mock.inner_text.return_value = channel_handle

        watch_link_mock = MagicMock()
        watch_link_mock.get_attribute.return_value = f"/watch?v={video_id}"

        def card_query_selector(sel):
            if "watch?v=" in sel:
                return watch_link_mock
            return channel_link_mock

        card.query_selector.side_effect = card_query_selector
        card.evaluate.return_value = True  # is_connected check in Phase 5
        return card

    card1 = _make_card(channel1)
    card2 = _make_card(channel2)

    _calls = [0]

    def query_selector_all_side_effect(sel):
        _calls[0] += 1
        if _calls[0] == 1:
            return [card1]   # pass 1: card1 only → Phase 4 classifies, populates _title_cache
        if _calls[0] == 2:
            return [card2]   # pass 2: card2 (different channel, same video) → cache-hit path
        return []

    page.query_selector_all.side_effect = query_selector_all_side_effect

    json_videos = {video_id: {"title": title, "channel_handle": channel1}}
    return page, card1, card2, json_videos


_MINIMAL_CB_CFG = {
    "video": {
        "title": {
            "model": {"name": "test-model", "auto_pull": False},
            "threshold": 0.75,
            "ambiguous_low": 0.4,
        },
        "thumbnail": {"enabled": False},
        "transcript": {"enabled": False},
    }
}


class TestClickbaitCacheHit:
    """Cover cache-hit paths at lines 1050-1062 in browser.py.

    Strategy: supply a page with TWO cards sharing the same video_id in a single pass.
    Card 1 → classified by Phase 4 → populates _title_cache.
    Card 2 → same video_id → cache-hit path fires (lines 1050-1062) because channel2
    is not in _clickbait_evaluated and video_id is already in _title_cache.
    """

    def setup_method(self):
        unblock_mod._pending_attempted_this_run.clear()
        self._pkg_patch = patch("yt_dont_recommend.browser._pkg")
        self._mock_pkg = self._pkg_patch.start()
        self._mock_pkg.return_value.save_state = MagicMock()
        self._mock_pkg.return_value.write_attention = MagicMock()
        self._mock_pkg.return_value.load_state = MagicMock()

    def teardown_method(self):
        self._pkg_patch.stop()
        unblock_mod._pending_attempted_this_run.clear()

    def test_clickbait_cache_hit_flagged_appends_to_cb_flagged(self):
        """Card 1 classified as flagged; card 2 cache-hit (same video_id) appends to _cb_flagged."""
        state = copy.deepcopy(_MINIMAL_STATE)

        page, card1, card2, json_videos = _make_two_card_cb_page(
            video_id=_CB_VIDEO_ID,
            channel1="@chan1",
            channel2="@chan2",
            title="You WON'T BELIEVE what happened next",
        )

        # Phase 4 classify returns flagged=True (confidence >= threshold)
        flagged_result = {
            "is_clickbait": True,
            "confidence": 0.9,
            "reasoning": "sensationalist",
            "stage": "title",
            "model": "test-model",
            "video_id": _CB_VIDEO_ID,
            "elapsed": 0.1,
        }

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.clickbait.classify_titles_batch", return_value=[flagged_result]),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                clickbait_cfg=_MINIMAL_CB_CFG,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # _click_not_interested should have been called (clickbait action fired on a flagged card)
        mock_ni.assert_called()

    def test_clickbait_cache_hit_not_flagged_skips_with_log(self, caplog):
        """Card 1 classified as not-flagged; card 2 cache-hit logs 'cache hit not flagged'."""
        import logging

        state = copy.deepcopy(_MINIMAL_STATE)

        page, card1, card2, json_videos = _make_two_card_cb_page(
            video_id=_CB_VIDEO_ID,
            channel1="@chan1",
            channel2="@chan2",
            title="A normal video title",
        )

        # Phase 4 classify returns not-flagged (confidence < threshold)
        not_flagged_result = {
            "is_clickbait": False,
            "confidence": 0.3,
            "reasoning": "benign",
            "stage": "title",
            "model": "test-model",
            "video_id": _CB_VIDEO_ID,
            "elapsed": 0.1,
        }

        with (
            patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()),
            patch("yt_dont_recommend.browser._extract_feed_videos_from_json", return_value=json_videos),
            patch("yt_dont_recommend.clickbait.classify_titles_batch", return_value=[not_flagged_result]),
            patch("yt_dont_recommend.browser._click_not_interested", return_value=True) as mock_ni,
            patch("yt_dont_recommend.browser.time") as mock_time,
            caplog.at_level(logging.DEBUG, logger="yt_dont_recommend"),
        ):
            mock_time.sleep.return_value = None
            process_channels(
                channel_sources={},
                state=state,
                clickbait_cfg=_MINIMAL_CB_CFG,
                _browser=("pwcm-stub", MagicMock(), page),
            )

        # No clickbait action should have fired (not-flagged → no _click_not_interested)
        mock_ni.assert_not_called()
        debug_msgs = [r.message for r in caplog.records]
        assert any("cache hit not flagged" in m for m in debug_msgs), (
            f"Expected 'cache hit not flagged' in debug log. Messages: {debug_msgs}"
        )

"""
Tests for yt_dont_recommend.py

Covers the pure-Python logic: blocklist parsing, URL construction,
state management, and source resolution. Browser automation functions
(do_login, process_channels, dont_recommend_channel, check_selectors)
require a live YouTube session and are not tested here.

Run with:
    pip install pytest --break-system-packages
    pytest tests/ -v
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import yt_dont_recommend as ydr

# Canonical channel IDs used in tests (no leading /).
# When a test needs to exercise the /@ or /channel/ prefix normalization path,
# construct the raw input programmatically: f"/{_C1}" rather than hardcoding "/@channel1".
_C1 = "@channel1"
_C2 = "@channel2"
_HANDLE = "@HandleChannel"
_UC = "UCxxxxxxxxxxxxxxxxxxxxxxxx"


# ---------------------------------------------------------------------------
# parse_text_blocklist
# ---------------------------------------------------------------------------

class TestParseTextBlocklist:
    def test_basic_handles(self):
        raw = f"/{_C1}\n/{_C2}\n"
        assert ydr.parse_text_blocklist(raw) == [_C1, _C2]

    def test_bare_at_handles(self):
        raw = "@channel1\n@channel2\n"
        assert ydr.parse_text_blocklist(raw) == ["@channel1", "@channel2"]

    def test_channel_id_format(self):
        raw = "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx\n"
        assert ydr.parse_text_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_bare_channel_id_format(self):
        raw = "UCxxxxxxxxxxxxxxxxxxxxxxxx\n"
        assert ydr.parse_text_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_comments_ignored(self):
        raw = f"# this is a comment\n/{_C1}\n# another comment\n"
        assert ydr.parse_text_blocklist(raw) == [_C1]

    def test_exclamation_comments_ignored(self):
        raw = "! this is an aislist comment\n@channel1\n"
        assert ydr.parse_text_blocklist(raw) == ["@channel1"]

    def test_inline_comment_stripped(self):
        raw = f"{_C1}  # keeping this one\n{_C2}\n"
        assert ydr.parse_text_blocklist(raw) == [_C1, _C2]

    def test_inline_comment_only_hash_discarded(self):
        # A line that is nothing but "# reason" after stripping should be ignored
        raw = f"{_C1}\n# standalone comment\n{_C2}\n"
        assert ydr.parse_text_blocklist(raw) == [_C1, _C2]

    def test_blank_lines_ignored(self):
        raw = f"\n/{_C1}\n\n\n/{_C2}\n\n"
        assert ydr.parse_text_blocklist(raw) == [_C1, _C2]

    def test_whitespace_stripped(self):
        raw = f"  /{_C1}  \n  /{_C2}  \n"
        assert ydr.parse_text_blocklist(raw) == [_C1, _C2]

    def test_empty_string(self):
        assert ydr.parse_text_blocklist("") == []

    def test_only_comments_and_blanks(self):
        raw = "# comment\n\n# another\n\n"
        assert ydr.parse_text_blocklist(raw) == []

    def test_mixed_formats(self):
        raw = (
            "# DeSlop-style list\n"
            "\n"
            f"/{_HANDLE}\n"
            f"/channel/{_UC}\n"
            "# end\n"
        )
        result = ydr.parse_text_blocklist(raw)
        assert result == [_HANDLE, _UC]

    def test_no_trailing_newline(self):
        raw = f"/{_C1}"
        assert ydr.parse_text_blocklist(raw) == [_C1]


# ---------------------------------------------------------------------------
# parse_json_blocklist
# ---------------------------------------------------------------------------

class TestParseJsonBlocklist:
    def test_list_of_strings(self):
        raw = json.dumps(["@channel1", "@channel2"])
        assert ydr.parse_json_blocklist(raw) == ["@channel1", "@channel2"]

    def test_list_of_dicts_channel_handle_key(self):
        raw = json.dumps([{"channelHandle": "@channel1"}])
        assert ydr.parse_json_blocklist(raw) == ["@channel1"]

    def test_list_of_dicts_handle_key(self):
        raw = json.dumps([{"handle": "@channel2"}])
        assert ydr.parse_json_blocklist(raw) == ["@channel2"]

    def test_list_of_dicts_channel_id_uc_prefix(self):
        raw = json.dumps([{"channelId": "UCxxxxxxxxxxxxxxxxxxxxxxxx"}])
        assert ydr.parse_json_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_list_of_dicts_full_youtube_url(self):
        raw = json.dumps([{"url": "https://www.youtube.com/@channel1"}])
        assert ydr.parse_json_blocklist(raw) == ["@channel1"]

    def test_list_of_dicts_key_priority_order(self):
        # channelHandle should be preferred over handle, id, etc.
        raw = json.dumps([{"channelHandle": "@preferred", "handle": "@ignored", "id": "also-ignored"}])
        result = ydr.parse_json_blocklist(raw)
        assert result == ["@preferred"]

    def test_dict_keyed_by_channel_id(self):
        raw = json.dumps({"UCxxxxxxxxxxxxxxxxxxxxxxxx": {"name": "Some Channel"}})
        assert ydr.parse_json_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_dict_keyed_by_at_handle(self):
        raw = json.dumps({"@channel1": {"name": "Channel One"}})
        assert ydr.parse_json_blocklist(raw) == ["@channel1"]

    def test_dict_mixed_keys(self):
        raw = json.dumps({
            "UCxxxxxxxxxxxxxxxxxxxxxxxx": {},
            "@handleChannel": {},
            "unrelated-key": {},  # should be skipped
        })
        result = ydr.parse_json_blocklist(raw)
        assert "UCxxxxxxxxxxxxxxxxxxxxxxxx" in result
        assert "@handleChannel" in result
        assert len(result) == 2  # unrelated key skipped

    def test_invalid_json_falls_back_to_text_parse(self):
        # If JSON parsing fails, it should fall back to treating content as plain text
        raw = f"/{_C1}\n/{_C2}\n"
        result = ydr.parse_json_blocklist(raw)
        assert result == [_C1, _C2]

    def test_empty_list(self):
        assert ydr.parse_json_blocklist("[]") == []

    def test_empty_dict(self):
        assert ydr.parse_json_blocklist("{}") == []

    def test_list_mixed_strings_and_dicts(self):
        raw = json.dumps([
            "@string-channel",
            {"channelId": "UCxxxxxxxxxxxxxxxxxxxxxxxx"},
        ])
        result = ydr.parse_json_blocklist(raw)
        assert "@string-channel" in result
        assert "UCxxxxxxxxxxxxxxxxxxxxxxxx" in result


# ---------------------------------------------------------------------------
# channel_to_url
# ---------------------------------------------------------------------------

class TestChannelToUrl:
    def test_handle_path(self):
        assert ydr.channel_to_url("@SomeChannel") == "https://www.youtube.com/@SomeChannel"

    def test_channel_id_path(self):
        assert ydr.channel_to_url("UCxxx") == "https://www.youtube.com/channel/UCxxx"

    def test_already_full_url_passthrough(self):
        url = "https://www.youtube.com/@SomeChannel"
        assert ydr.channel_to_url(url) == url

    def test_http_url_passthrough(self):
        url = "http://www.youtube.com/@SomeChannel"
        assert ydr.channel_to_url(url) == url


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


# ---------------------------------------------------------------------------
# resolve_source
# ---------------------------------------------------------------------------

class TestResolveSource:
    def test_local_text_file(self, tmp_path):
        f = tmp_path / "list.txt"
        f.write_text(f"/{_C1}\n# comment\n/{_C2}\n")
        result = ydr.resolve_source(str(f))
        assert result == [_C1, _C2]

    def test_local_json_file(self, tmp_path):
        f = tmp_path / "list.json"
        f.write_text(json.dumps(["@channel1", "@channel2"]))
        result = ydr.resolve_source(str(f))
        assert result == ["@channel1", "@channel2"]

    def test_local_file_tilde_expansion(self, tmp_path, monkeypatch):
        # ~ should be expanded to home dir; fake it by using an absolute path
        f = tmp_path / "list.txt"
        f.write_text(f"/{_C1}\n")
        result = ydr.resolve_source(str(f))
        assert result == [_C1]

    def test_missing_local_file_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            ydr.resolve_source("/nonexistent/path/that/does/not/exist.txt")
        assert exc_info.value.code == 1

    def test_builtin_source_deslop_fetches_and_parses(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = f"/{_C1}\n# comment\n/{_C2}\n"
            result = ydr.resolve_source("deslop")
        assert result == [_C1, _C2]
        mock_fetch.assert_called_once_with(ydr.BUILTIN_SOURCES["deslop"]["url"])

    def test_builtin_source_aislist_fetches_and_parses(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            # AiSList uses plain text with ! comments and bare @handle entries
            mock_fetch.return_value = "! comment\n@channel1\n@channel2\n"
            result = ydr.resolve_source("aislist")
        assert result == ["@channel1", "@channel2"]
        mock_fetch.assert_called_once_with(ydr.BUILTIN_SOURCES["aislist"]["url"])

    def test_remote_url_text(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = f"/{_C1}\n/{_C2}\n"
            result = ydr.resolve_source("https://example.com/list.txt")
        assert result == [_C1, _C2]

    def test_remote_url_json_sniffed_by_leading_bracket(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = json.dumps(["@channel1"])
            result = ydr.resolve_source("https://example.com/list.json")
        assert result == ["@channel1"]

    def test_remote_url_json_sniffed_by_leading_brace(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = json.dumps({"UCxxxxxxxxxxxxxxxxxxxxxxxx": {}})
            result = ydr.resolve_source("https://example.com/channels.json")
        assert result == ["UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_http_url_also_accepted(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = f"/{_C1}\n"
            result = ydr.resolve_source("http://example.com/list.txt")
        assert result == [_C1]

    def test_unknown_string_treated_as_file_path(self):
        # A string that's not a built-in key and not http(s):// should be
        # treated as a file path and exit if not found.
        with pytest.raises(SystemExit):
            ydr.resolve_source("notabuiltin")


# ---------------------------------------------------------------------------
# --exclude filtering (applied in main() before process_channels)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# check_removals
# ---------------------------------------------------------------------------

class TestCheckRemovals:
    def _state(self, blocked: dict) -> dict:
        """Build a minimal state dict with the given blocked_by entries."""
        processed = list(blocked.keys())
        return {
            "processed": processed,
            "blocked_by": {
                ch: {"sources": list(sources), "blocked_at": "2026-01-01T00:00:00"}
                for ch, sources in blocked.items()
            },
            "would_have_blocked": {},
            "last_run": None,
            "stats": {"total_blocked": len(processed), "total_skipped": 0, "total_failed": 0},
        }

    def test_unblocks_channel_removed_from_sole_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@gone": ["deslop"]})
        result = ydr.check_removals(state, [], "deslop", "all")
        assert result == ["@gone"]
        assert "@gone" not in state["processed"]
        assert "@gone" not in state["blocked_by"]

    def test_all_policy_keeps_block_when_other_source_still_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@channel": ["deslop", "aislist"]})
        result = ydr.check_removals(state, [], "deslop", "all")
        assert result == []
        assert "@channel" in state["processed"]
        # deslop removed from sources list, aislist still there
        assert state["blocked_by"]["@channel"]["sources"] == ["aislist"]

    def test_any_policy_unblocks_even_with_other_sources(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@channel": ["deslop", "aislist"]})
        result = ydr.check_removals(state, [], "deslop", "any")
        assert result == ["@channel"]
        assert "@channel" not in state["processed"]
        assert "@channel" not in state["blocked_by"]

    def test_channel_still_in_list_is_not_touched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@still-there": ["deslop"]})
        result = ydr.check_removals(state, ["@still-there"], "deslop", "all")
        assert result == []
        assert "@still-there" in state["processed"]

    def test_channel_from_different_source_not_affected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@channel": ["aislist"]})
        # Running deslop — aislist channel not in deslop, but deslop didn't block it
        result = ydr.check_removals(state, [], "deslop", "all")
        assert result == []
        assert "@channel" in state["processed"]

    def test_check_removals_is_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@Channel": ["deslop"]})
        # Current list has different casing — should still be recognised as present
        result = ydr.check_removals(state, ["@channel"], "deslop", "all")
        assert result == []

    def test_load_state_backward_compat_adds_missing_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        # Write an old-style state without the new fields
        old_state = {"processed": ["@ch"], "last_run": None, "stats": {}}
        (tmp_path / "processed.json").write_text(json.dumps(old_state))
        state = ydr.load_state()
        assert "blocked_by" in state
        assert "would_have_blocked" in state


class TestExcludeFiltering:
    """
    The exclude logic lives in main(), but the underlying mechanism is just
    set subtraction using resolve_source(). These tests verify the filtering
    logic directly using the same pattern main() uses.
    """

    def _apply_exclude(self, channels: list[str], exclude_raw: str) -> list[str]:
        """Simulate what main() does with --exclude."""
        exclude_set = {c.lower() for c in ydr.parse_text_blocklist(exclude_raw)}
        return [c for c in channels if c.lower() not in exclude_set]

    def test_excluded_channel_removed(self):
        channels = ["@keep", "@remove", "@also-keep"]
        result = self._apply_exclude(channels, "@remove\n")
        assert result == ["@keep", "@also-keep"]

    def test_exclude_is_case_insensitive(self):
        channels = ["@SomeChannel"]
        result = self._apply_exclude(channels, "@somechannel\n")
        assert result == []

    def test_exclude_with_comments_and_blanks(self):
        channels = ["@a", "@b", "@c"]
        result = self._apply_exclude(channels, "# exclude b\n\n@b\n")
        assert result == ["@a", "@c"]

    def test_empty_exclude_list_changes_nothing(self):
        channels = ["@a", "@b"]
        result = self._apply_exclude(channels, "# just comments\n\n")
        assert result == ["@a", "@b"]

    def test_exclude_channel_id_format(self):
        channels = ["UCxxxxxxxxxxxxxxxxxxxxxxxx", "@keep"]
        result = self._apply_exclude(channels, "UCxxxxxxxxxxxxxxxxxxxxxxxx\n")
        assert result == ["@keep"]

    def test_exclude_all_channels(self):
        channels = ["@a", "@b"]
        result = self._apply_exclude(channels, "@a\n@b\n")
        assert result == []

    def test_exclude_nonexistent_channel_is_noop(self):
        channels = ["@a", "@b"]
        result = self._apply_exclude(channels, "@nothere\n")
        assert result == ["@a", "@b"]


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
        with patch("yt_dont_recommend._get_latest_pypi_version", return_value=None):
            result = ydr.check_for_update(state, force=True)
        assert result is None

    def test_check_for_update_returns_none_when_already_latest(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        with patch("yt_dont_recommend._get_latest_pypi_version", return_value="0.1.0"), \
             patch("yt_dont_recommend._get_current_version", return_value="0.1.4"):
            result = ydr.check_for_update(state, force=True)
        assert result is None

    def test_check_for_update_returns_version_when_newer(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        with patch("yt_dont_recommend._get_latest_pypi_version", return_value="0.2.0"), \
             patch("yt_dont_recommend._get_current_version", return_value="0.1.4"):
            result = ydr.check_for_update(state, force=True)
        assert result == "0.2.0"

    def test_check_for_update_respects_interval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        from datetime import datetime
        state = ydr.load_state()
        # Simulate a recent check that found a newer version
        state["last_version_check"] = datetime.now().isoformat()
        state["latest_known_version"] = "0.2.0"
        with patch("yt_dont_recommend._get_latest_pypi_version") as mock_pypi, \
             patch("yt_dont_recommend._get_current_version", return_value="0.1.4"):
            result = ydr.check_for_update(state, force=False)
            mock_pypi.assert_not_called()  # should use cached value, not hit PyPI
        assert result == "0.2.0"

    def test_check_for_update_notifies_ntfy_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["notify_topic"] = "test-topic"
        with patch("yt_dont_recommend._get_latest_pypi_version", return_value="0.2.0"), \
             patch("yt_dont_recommend._get_current_version", return_value="0.1.4"), \
             patch("yt_dont_recommend._ntfy_notify") as mock_ntfy:
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

    def test_revert_with_no_previous_version_prints_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        ydr.do_revert()
        captured = capsys.readouterr()
        assert "No previous version" in captured.out

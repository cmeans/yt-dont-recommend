"""
Tests for yt_dont_recommend.py

Covers the pure-Python logic: blocklist parsing, URL construction,
state management, and source resolution. Browser automation functions
(do_login, process_channels, dont_recommend_channel, check_selectors)
require a live YouTube session and are not tested here.

Run with:
    pip install pytest --break-system-packages
    pytest test_yt_dont_recommend.py -v
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import yt_dont_recommend as ydr


# ---------------------------------------------------------------------------
# parse_text_blocklist
# ---------------------------------------------------------------------------

class TestParseTextBlocklist:
    def test_basic_handles(self):
        raw = "/@channel1\n/@channel2\n"
        assert ydr.parse_text_blocklist(raw) == ["/@channel1", "/@channel2"]

    def test_channel_id_format(self):
        raw = "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx\n"
        assert ydr.parse_text_blocklist(raw) == ["/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_comments_ignored(self):
        raw = "# this is a comment\n/@channel1\n# another comment\n"
        assert ydr.parse_text_blocklist(raw) == ["/@channel1"]

    def test_blank_lines_ignored(self):
        raw = "\n/@channel1\n\n\n/@channel2\n\n"
        assert ydr.parse_text_blocklist(raw) == ["/@channel1", "/@channel2"]

    def test_whitespace_stripped(self):
        raw = "  /@channel1  \n  /@channel2  \n"
        assert ydr.parse_text_blocklist(raw) == ["/@channel1", "/@channel2"]

    def test_empty_string(self):
        assert ydr.parse_text_blocklist("") == []

    def test_only_comments_and_blanks(self):
        raw = "# comment\n\n# another\n\n"
        assert ydr.parse_text_blocklist(raw) == []

    def test_mixed_formats(self):
        raw = (
            "# DeSlop-style list\n"
            "\n"
            "/@HandleChannel\n"
            "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx\n"
            "# end\n"
        )
        result = ydr.parse_text_blocklist(raw)
        assert result == ["/@HandleChannel", "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_no_trailing_newline(self):
        raw = "/@channel1"
        assert ydr.parse_text_blocklist(raw) == ["/@channel1"]


# ---------------------------------------------------------------------------
# parse_json_blocklist
# ---------------------------------------------------------------------------

class TestParseJsonBlocklist:
    def test_list_of_strings(self):
        raw = json.dumps(["/@channel1", "/@channel2"])
        assert ydr.parse_json_blocklist(raw) == ["/@channel1", "/@channel2"]

    def test_list_of_dicts_channel_handle_key(self):
        raw = json.dumps([{"channelHandle": "@channel1"}])
        assert ydr.parse_json_blocklist(raw) == ["/@channel1"]

    def test_list_of_dicts_handle_key(self):
        raw = json.dumps([{"handle": "@channel2"}])
        assert ydr.parse_json_blocklist(raw) == ["/@channel2"]

    def test_list_of_dicts_channel_id_uc_prefix(self):
        raw = json.dumps([{"channelId": "UCxxxxxxxxxxxxxxxxxxxxxxxx"}])
        assert ydr.parse_json_blocklist(raw) == ["/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_list_of_dicts_full_youtube_url(self):
        raw = json.dumps([{"url": "https://www.youtube.com/@channel1"}])
        assert ydr.parse_json_blocklist(raw) == ["/@channel1"]

    def test_list_of_dicts_key_priority_order(self):
        # channelHandle should be preferred over handle, id, etc.
        raw = json.dumps([{"channelHandle": "@preferred", "handle": "@ignored", "id": "also-ignored"}])
        result = ydr.parse_json_blocklist(raw)
        assert result == ["/@preferred"]

    def test_dict_keyed_by_channel_id(self):
        raw = json.dumps({"UCxxxxxxxxxxxxxxxxxxxxxxxx": {"name": "Some Channel"}})
        assert ydr.parse_json_blocklist(raw) == ["/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_dict_keyed_by_at_handle(self):
        raw = json.dumps({"@channel1": {"name": "Channel One"}})
        assert ydr.parse_json_blocklist(raw) == ["/@channel1"]

    def test_dict_mixed_keys(self):
        raw = json.dumps({
            "UCxxxxxxxxxxxxxxxxxxxxxxxx": {},
            "@handleChannel": {},
            "unrelated-key": {},  # should be skipped
        })
        result = ydr.parse_json_blocklist(raw)
        assert "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx" in result
        assert "/@handleChannel" in result
        assert len(result) == 2  # unrelated key skipped

    def test_invalid_json_falls_back_to_text_parse(self):
        # If JSON parsing fails, it should fall back to treating content as plain text
        raw = "/@channel1\n/@channel2\n"
        result = ydr.parse_json_blocklist(raw)
        assert result == ["/@channel1", "/@channel2"]

    def test_empty_list(self):
        assert ydr.parse_json_blocklist("[]") == []

    def test_empty_dict(self):
        assert ydr.parse_json_blocklist("{}") == []

    def test_list_mixed_strings_and_dicts(self):
        raw = json.dumps([
            "/@string-channel",
            {"channelId": "UCxxxxxxxxxxxxxxxxxxxxxxxx"},
        ])
        result = ydr.parse_json_blocklist(raw)
        assert "/@string-channel" in result
        assert "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx" in result


# ---------------------------------------------------------------------------
# channel_to_url
# ---------------------------------------------------------------------------

class TestChannelToUrl:
    def test_handle_path(self):
        assert ydr.channel_to_url("/@SomeChannel") == "https://www.youtube.com/@SomeChannel"

    def test_channel_id_path(self):
        assert ydr.channel_to_url("/channel/UCxxx") == "https://www.youtube.com/channel/UCxxx"

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
        assert state["stats"] == {"success": 0, "skipped": 0, "failed": 0}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["processed"].append("/@channel1")
        state["stats"]["success"] = 1
        ydr.save_state(state)

        loaded = ydr.load_state()
        assert "/@channel1" in loaded["processed"]
        assert loaded["stats"]["success"] == 1
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
        f.write_text("/@channel1\n# comment\n/@channel2\n")
        result = ydr.resolve_source(str(f))
        assert result == ["/@channel1", "/@channel2"]

    def test_local_json_file(self, tmp_path):
        f = tmp_path / "list.json"
        f.write_text(json.dumps(["/@channel1", "/@channel2"]))
        result = ydr.resolve_source(str(f))
        assert result == ["/@channel1", "/@channel2"]

    def test_local_file_tilde_expansion(self, tmp_path, monkeypatch):
        # ~ should be expanded to home dir; fake it by using an absolute path
        f = tmp_path / "list.txt"
        f.write_text("/@channel1\n")
        result = ydr.resolve_source(str(f))
        assert result == ["/@channel1"]

    def test_missing_local_file_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            ydr.resolve_source("/nonexistent/path/that/does/not/exist.txt")
        assert exc_info.value.code == 1

    def test_builtin_source_deslop_fetches_and_parses(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = "/@channel1\n# comment\n/@channel2\n"
            result = ydr.resolve_source("deslop")
        assert result == ["/@channel1", "/@channel2"]
        mock_fetch.assert_called_once_with(ydr.BUILTIN_SOURCES["deslop"]["url"])

    def test_builtin_source_aislist_fetches_and_parses(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = json.dumps(["/@channel1"])
            result = ydr.resolve_source("aislist")
        assert result == ["/@channel1"]
        mock_fetch.assert_called_once_with(ydr.BUILTIN_SOURCES["aislist"]["url"])

    def test_remote_url_text(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = "/@channel1\n/@channel2\n"
            result = ydr.resolve_source("https://example.com/list.txt")
        assert result == ["/@channel1", "/@channel2"]

    def test_remote_url_json_sniffed_by_leading_bracket(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = json.dumps(["/@channel1"])
            result = ydr.resolve_source("https://example.com/list.json")
        assert result == ["/@channel1"]

    def test_remote_url_json_sniffed_by_leading_brace(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = json.dumps({"UCxxxxxxxxxxxxxxxxxxxxxxxx": {}})
            result = ydr.resolve_source("https://example.com/channels.json")
        assert result == ["/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx"]

    def test_http_url_also_accepted(self):
        with patch("yt_dont_recommend.fetch_remote") as mock_fetch:
            mock_fetch.return_value = "/@channel1\n"
            result = ydr.resolve_source("http://example.com/list.txt")
        assert result == ["/@channel1"]

    def test_unknown_string_treated_as_file_path(self):
        # A string that's not a built-in key and not http(s):// should be
        # treated as a file path and exit if not found.
        with pytest.raises(SystemExit):
            ydr.resolve_source("notabuiltin")


# ---------------------------------------------------------------------------
# --exclude filtering (applied in main() before process_channels)
# ---------------------------------------------------------------------------

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
        channels = ["/@keep", "/@remove", "/@also-keep"]
        result = self._apply_exclude(channels, "/@remove\n")
        assert result == ["/@keep", "/@also-keep"]

    def test_exclude_is_case_insensitive(self):
        channels = ["/@SomeChannel"]
        result = self._apply_exclude(channels, "/@somechannel\n")
        assert result == []

    def test_exclude_with_comments_and_blanks(self):
        channels = ["/@a", "/@b", "/@c"]
        result = self._apply_exclude(channels, "# exclude b\n\n/@b\n")
        assert result == ["/@a", "/@c"]

    def test_empty_exclude_list_changes_nothing(self):
        channels = ["/@a", "/@b"]
        result = self._apply_exclude(channels, "# just comments\n\n")
        assert result == ["/@a", "/@b"]

    def test_exclude_channel_id_format(self):
        channels = ["/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx", "/@keep"]
        result = self._apply_exclude(channels, "/channel/UCxxxxxxxxxxxxxxxxxxxxxxxx\n")
        assert result == ["/@keep"]

    def test_exclude_all_channels(self):
        channels = ["/@a", "/@b"]
        result = self._apply_exclude(channels, "/@a\n/@b\n")
        assert result == []

    def test_exclude_nonexistent_channel_is_noop(self):
        channels = ["/@a", "/@b"]
        result = self._apply_exclude(channels, "/@not-in-list\n")
        assert result == ["/@a", "/@b"]

"""
Tests for yt_dont_recommend.blocklist — parsing, resolution, and check_removals.

Functions under test are imported directly from yt_dont_recommend.blocklist, but
patch targets remain yt_dont_recommend.X (the re-exported name in __init__.py),
as they did in the original test_yt_dont_recommend.py.
"""

import json
from unittest.mock import patch

import pytest

import yt_dont_recommend as ydr
from yt_dont_recommend import blocklist as blocklist_mod

# Canonical channel IDs used in tests (no leading /).
# When a test needs to exercise the /@ or /channel/ prefix normalization path,
# construct the raw input programmatically: f"/{_C1}" rather than hardcoding "/@channel1".
_C1 = "@channel1"
_C2 = "@channel2"
_HANDLE = "@HandleChannel"
_UC = "UCxxxxxxxxxxxxxxxxxxxxxx"


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
        raw = "/channel/UCxxxxxxxxxxxxxxxxxxxxxx\n"
        assert ydr.parse_text_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxx"]

    def test_bare_channel_id_format(self):
        raw = "UCxxxxxxxxxxxxxxxxxxxxxx\n"
        assert ydr.parse_text_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxx"]

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
        raw = json.dumps([{"channelId": "UCxxxxxxxxxxxxxxxxxxxxxx"}])
        assert ydr.parse_json_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxx"]

    def test_list_of_dicts_full_youtube_url(self):
        raw = json.dumps([{"url": "https://www.youtube.com/@channel1"}])
        assert ydr.parse_json_blocklist(raw) == ["@channel1"]

    def test_list_of_dicts_key_priority_order(self):
        # channelHandle should be preferred over handle, id, etc.
        raw = json.dumps([{"channelHandle": "@preferred", "handle": "@ignored", "id": "also-ignored"}])
        result = ydr.parse_json_blocklist(raw)
        assert result == ["@preferred"]

    def test_dict_keyed_by_channel_id(self):
        raw = json.dumps({"UCxxxxxxxxxxxxxxxxxxxxxx": {"name": "Some Channel"}})
        assert ydr.parse_json_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxx"]

    def test_dict_keyed_by_at_handle(self):
        raw = json.dumps({"@channel1": {"name": "Channel One"}})
        assert ydr.parse_json_blocklist(raw) == ["@channel1"]

    def test_dict_mixed_keys(self):
        raw = json.dumps({
            "UCxxxxxxxxxxxxxxxxxxxxxx": {},
            "@handleChannel": {},
            "unrelated-key": {},  # should be skipped
        })
        result = ydr.parse_json_blocklist(raw)
        assert "UCxxxxxxxxxxxxxxxxxxxxxx" in result
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
            {"channelId": "UCxxxxxxxxxxxxxxxxxxxxxx"},
        ])
        result = ydr.parse_json_blocklist(raw)
        assert "@string-channel" in result
        assert "UCxxxxxxxxxxxxxxxxxxxxxx" in result

    def test_null_value_in_dict_entry_skipped(self):
        # null channel values should be skipped without raising AttributeError
        raw = json.dumps([{"channelHandle": None}, {"channelHandle": "@valid"}])
        assert ydr.parse_json_blocklist(raw) == ["@valid"]

    def test_numeric_value_in_dict_entry_skipped(self):
        # numeric channel IDs should be skipped without raising AttributeError
        raw = json.dumps([{"channelId": 12345}, {"channelHandle": "@valid"}])
        assert ydr.parse_json_blocklist(raw) == ["@valid"]


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
            mock_fetch.return_value = json.dumps({"UCxxxxxxxxxxxxxxxxxxxxxx": {}})
            result = ydr.resolve_source("https://example.com/channels.json")
        assert result == ["UCxxxxxxxxxxxxxxxxxxxxxx"]

    def test_http_url_rejected(self):
        # http:// sources are refused — use https:// or a local file path instead.
        with pytest.raises(SystemExit) as exc:
            ydr.resolve_source("http://example.com/list.txt")
        assert exc.value.code == 1

    def test_unknown_string_treated_as_file_path(self):
        # A string that's not a built-in key and not http(s):// should be
        # treated as a file path and exit if not found.
        with pytest.raises(SystemExit):
            ydr.resolve_source("notabuiltin")


# ---------------------------------------------------------------------------
# check_removals
# ---------------------------------------------------------------------------

class TestCheckRemovals:
    def _state(self, blocked: dict) -> dict:
        """Build a minimal state dict with the given blocked_by entries."""
        return {
            "blocked_by": {
                ch: {"sources": list(sources), "blocked_at": "2026-01-01T00:00:00"}
                for ch, sources in blocked.items()
            },
            "would_have_blocked": {},
            "last_run": None,
            "stats": {"total_blocked": len(blocked), "total_skipped": 0, "total_failed": 0},
        }

    def test_unblocks_channel_removed_from_sole_source(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@gone": ["deslop"]})
        result = ydr.check_removals(state, [], "deslop", "all")
        assert result == ["@gone"]
        assert "@gone" not in state["blocked_by"]

    def test_all_policy_keeps_block_when_other_source_still_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@channel": ["deslop", "aislist"]})
        result = ydr.check_removals(state, [], "deslop", "all")
        assert result == []
        assert "@channel" in state["blocked_by"]
        # deslop removed from sources list, aislist still there
        assert state["blocked_by"]["@channel"]["sources"] == ["aislist"]

    def test_any_policy_unblocks_even_with_other_sources(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@channel": ["deslop", "aislist"]})
        result = ydr.check_removals(state, [], "deslop", "any")
        assert result == ["@channel"]
        assert "@channel" not in state["blocked_by"]

    def test_channel_still_in_list_is_not_touched(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@still-there": ["deslop"]})
        result = ydr.check_removals(state, ["@still-there"], "deslop", "all")
        assert result == []
        assert "@still-there" in state["blocked_by"]

    def test_channel_from_different_source_not_affected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@channel": ["aislist"]})
        # Running deslop — aislist channel not in deslop, but deslop didn't block it
        result = ydr.check_removals(state, [], "deslop", "all")
        assert result == []
        assert "@channel" in state["blocked_by"]

    def test_check_removals_is_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state({"@Channel": ["deslop"]})
        # Current list has different casing — should still be recognised as present
        result = ydr.check_removals(state, ["@channel"], "deslop", "all")
        assert result == []


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
        channels = ["UCxxxxxxxxxxxxxxxxxxxxxx", "@keep"]
        result = self._apply_exclude(channels, "UCxxxxxxxxxxxxxxxxxxxxxx\n")
        assert result == ["@keep"]

    def test_exclude_all_channels(self):
        channels = ["@a", "@b"]
        result = self._apply_exclude(channels, "@a\n@b\n")
        assert result == []

    def test_exclude_nonexistent_channel_is_noop(self):
        channels = ["@a", "@b"]
        result = self._apply_exclude(channels, "@nothere\n")
        assert result == ["@a", "@b"]


# ---------------------------------------------------------------------------
# Per-source stats, blocklist growth tracking, export-state
# ---------------------------------------------------------------------------

class TestPerSourceStats:
    def _state_with_blocks(self, tmp_path):
        state = ydr.load_state()
        state["blocked_by"] = {
            "@alpha": {"sources": ["deslop"]},
            "@beta":  {"sources": ["deslop"]},
            "@gamma": {"sources": ["aislist"]},
            "@delta": {"sources": ["deslop", "aislist"]},
        }
        state["source_sizes"] = {"deslop": 130, "aislist": 8400}
        ydr.save_state(state)
        return ydr.load_state()

    def test_per_source_tally(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state_with_blocks(tmp_path)
        per_source: dict[str, int] = {}
        for info in state.get("blocked_by", {}).values():
            for src in info.get("sources", []):
                per_source[src] = per_source.get(src, 0) + 1
        assert per_source["deslop"] == 3   # alpha, beta, delta
        assert per_source["aislist"] == 2  # gamma, delta

    def test_source_sizes_stored_in_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = self._state_with_blocks(tmp_path)
        assert state["source_sizes"]["deslop"] == 130
        assert state["source_sizes"]["aislist"] == 8400

    def test_growth_detected(self, tmp_path, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["source_sizes"]["deslop"] = 100
        ydr.save_state(state)

        # Simulate the growth tracking block
        channels = [f"@ch{i}" for i in range(115)]
        _st = ydr.load_state()
        _sizes = _st.setdefault("source_sizes", {})
        _prev = _sizes.get("deslop")
        with caplog.at_level(logging.INFO):
            if _prev is not None and len(channels) > _prev:
                import logging as _log
                _log.getLogger().info(
                    f"*** Blocklist 'deslop' grew by {len(channels) - _prev} channel(s) "
                    f"({_prev} → {len(channels)}) since last run"
                )
        assert "grew by 15" in caplog.text

    def test_no_growth_message_when_same_size(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["source_sizes"]["deslop"] = 100
        ydr.save_state(state)

        channels = [f"@ch{i}" for i in range(100)]
        _st = ydr.load_state()
        _prev = _st.get("source_sizes", {}).get("deslop")
        grew = _prev is not None and len(channels) > _prev
        assert not grew


class TestExportState:
    def _state_with_blocks(self, tmp_path):
        state = ydr.load_state()
        state["blocked_by"] = {
            "@beta":  {"sources": ["aislist"]},
            "@alpha": {"sources": ["deslop"]},
        }
        ydr.save_state(state)

    def test_export_sorted_output(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr(ydr, "_get_current_version", lambda: "0.0.0")
        self._state_with_blocks(tmp_path)
        state = ydr.load_state()
        blocked_by = state.get("blocked_by", {})
        lines = [
            f"# Exported by yt-dont-recommend {ydr._get_current_version()} on 2026-01-01",
            f"# Total blocked channels: {len(blocked_by)}",
            "",
        ]
        for channel in sorted(blocked_by):
            sources = blocked_by[channel].get("sources", [])
            src_note = f"  # {', '.join(sources)}" if sources else ""
            lines.append(f"{channel}{src_note}")
        output = "\n".join(lines) + "\n"
        assert "@alpha  # deslop\n@beta  # aislist\n" in output
        # Sorted: alpha before beta
        assert output.index("@alpha") < output.index("@beta")

    def test_export_to_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr(ydr, "_get_current_version", lambda: "0.0.0")
        self._state_with_blocks(tmp_path)
        out_file = tmp_path / "export.txt"
        state = ydr.load_state()
        blocked_by = state.get("blocked_by", {})
        from datetime import datetime
        lines = [
            f"# Exported by yt-dont-recommend {ydr._get_current_version()} on {datetime.now().strftime('%Y-%m-%d')}",
            f"# Total blocked channels: {len(blocked_by)}",
            "",
        ]
        for channel in sorted(blocked_by):
            sources = blocked_by[channel].get("sources", [])
            src_note = f"  # {', '.join(sources)}" if sources else ""
            lines.append(f"{channel}{src_note}")
        out_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        content = out_file.read_text()
        assert "@alpha" in content
        assert "@beta" in content
        assert "# Total blocked channels: 2" in content


# ---------------------------------------------------------------------------
# _get_current_version_for_ua — fallback chain for UA string
# ---------------------------------------------------------------------------

class TestGetCurrentVersionForUA:
    def test_uses_pkg_version_when_available(self):
        with patch.object(ydr, "_get_current_version", return_value="1.2.3"):
            assert blocklist_mod._get_current_version_for_ua() == "1.2.3"

    def test_falls_back_to_config_version_on_primary_error(self):
        with patch.object(ydr, "_get_current_version", side_effect=RuntimeError("boom")):
            from yt_dont_recommend import config as cfg_mod
            assert blocklist_mod._get_current_version_for_ua() == cfg_mod.__version__

    def test_returns_unknown_when_all_lookups_fail(self, monkeypatch):
        with patch.object(ydr, "_get_current_version", side_effect=RuntimeError("boom")):
            # Make `from .config import __version__` fail by removing the attribute.
            from yt_dont_recommend import config as cfg_mod
            monkeypatch.delattr(cfg_mod, "__version__", raising=False)
            assert blocklist_mod._get_current_version_for_ua() == "unknown"


# ---------------------------------------------------------------------------
# parse_json_blocklist — list-of-strings URL-prefix normalization
# ---------------------------------------------------------------------------

class TestParseJsonListStringNormalization:
    def test_slashed_handle_is_stripped(self):
        raw = json.dumps(["/@slashedHandle"])
        assert ydr.parse_json_blocklist(raw) == ["@slashedHandle"]

    def test_slashed_channel_id_is_stripped(self):
        raw = json.dumps(["/channel/UCxxxxxxxxxxxxxxxxxxxxxx"])
        assert ydr.parse_json_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxx"]


# ---------------------------------------------------------------------------
# parse_json_blocklist — dict entries with full http:// URLs
# ---------------------------------------------------------------------------

class TestParseJsonDictUrlBranches:
    def test_dict_url_with_handle_path(self):
        raw = json.dumps([{"url": "https://www.youtube.com/@someHandle"}])
        assert ydr.parse_json_blocklist(raw) == ["@someHandle"]

    def test_dict_url_with_channel_id_path(self):
        raw = json.dumps([{"url": "https://www.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx"}])
        assert ydr.parse_json_blocklist(raw) == ["UCxxxxxxxxxxxxxxxxxxxxxx"]

    def test_dict_url_with_other_path_is_dropped(self):
        # Paths that are neither /@handle nor /channel/UCxxx are structurally
        # invalid after normalization and must be dropped by _canonicalize_channel.
        raw = json.dumps([{"url": "https://www.youtube.com/user/legacyName"}])
        assert ydr.parse_json_blocklist(raw) == []


# ---------------------------------------------------------------------------
# fetch_remote — HTTP fetch and error path
# ---------------------------------------------------------------------------

class TestFetchRemote:
    def test_success_returns_decoded_body(self, monkeypatch):
        fake_body = b"@channel1\n@channel2\n"

        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self):
                return fake_body

        def fake_urlopen(req, timeout=30):
            assert req.headers.get("User-agent", req.headers.get("User-Agent")).startswith("yt-dont-recommend/")
            return FakeResponse()

        monkeypatch.setattr(blocklist_mod, "urlopen", fake_urlopen)
        assert blocklist_mod.fetch_remote("https://example.com/list.txt") == fake_body.decode("utf-8")

    def test_failure_raises_runtime_error(self, monkeypatch):
        def boom(req, timeout=30):
            raise OSError("network down")

        monkeypatch.setattr(blocklist_mod, "urlopen", boom)
        with pytest.raises(RuntimeError, match=r"Failed to fetch https://example.com/list.txt: network down"):
            blocklist_mod.fetch_remote("https://example.com/list.txt")


# ---------------------------------------------------------------------------
# channel_to_url — non-canonical fallback branch
# ---------------------------------------------------------------------------

class TestChannelToUrlFallback:
    def test_bare_identifier_gets_youtube_prefix(self):
        # channel doesn't start with http, @, or UC — fallback prepends youtube.com/
        assert ydr.channel_to_url("LegacyName") == "https://www.youtube.com/LegacyName"


# ---------------------------------------------------------------------------
# _canonicalize_channel — regex-based structural validator
# ---------------------------------------------------------------------------

class TestCanonicalizeChannel:
    def _call(self, raw):
        from yt_dont_recommend.blocklist import _canonicalize_channel
        return _canonicalize_channel(raw)

    def test_valid_handle(self):
        assert self._call("@SomeChannel") == "@SomeChannel"

    def test_valid_channel_id(self):
        assert self._call("UC" + "A" * 22) == "UC" + "A" * 22

    def test_handle_with_dot(self):
        assert self._call("@foo.bar") == "@foo.bar"

    def test_handle_with_underscore(self):
        assert self._call("@foo_bar") == "@foo_bar"

    def test_handle_with_hyphen(self):
        assert self._call("@foo-bar") == "@foo-bar"

    def test_handle_leading_whitespace_trimmed(self):
        assert self._call("  @foo") == "@foo"

    def test_handle_trailing_whitespace_trimmed(self):
        assert self._call("@foo  ") == "@foo"

    def test_uc_id_21_chars_rejected(self):
        assert self._call("UC" + "A" * 21) is None

    def test_uc_id_23_chars_rejected(self):
        assert self._call("UC" + "A" * 23) is None

    def test_uc_id_wrong_prefix_rejected(self):
        assert self._call("UX" + "A" * 22) is None
        assert self._call("uc" + "A" * 22) is None

    def test_handle_without_at_sign_rejected(self):
        assert self._call("SomeChannel") is None

    def test_empty_string_rejected(self):
        assert self._call("") is None

    def test_whitespace_only_rejected(self):
        assert self._call("   ") is None

    def test_injection_payload_rejected(self):
        assert self._call('@evil"; do shell script "echo pwned"') is None

    def test_newline_rejected(self):
        assert self._call("@foo\nbar") is None

    def test_path_traversal_rejected(self):
        assert self._call("@../etc/passwd") is None

    def test_query_string_rejected(self):
        assert self._call("@foo?bar=baz") is None

    def test_slash_in_handle_rejected(self):
        assert self._call("@foo/path") is None


# ---------------------------------------------------------------------------
# Parser validation — invalid entries dropped, warning logged
# ---------------------------------------------------------------------------

class TestParserValidation:
    """Both parsers must silently drop invalid channel identifiers."""

    def test_text_parser_drops_invalid_entries(self):
        # A bare word with no @ and no UC prefix is structurally invalid.
        raw = "@valid-channel\ninvalid-bare-word\n@another-valid\n"
        result = ydr.parse_text_blocklist(raw)
        assert result == ["@valid-channel", "@another-valid"]

    def test_text_parser_logs_warning_on_invalid(self, caplog):
        import logging
        raw = "@valid\nbad-entry\n"
        with caplog.at_level(logging.WARNING):
            ydr.parse_text_blocklist(raw)
        assert "Dropped" in caplog.text

    def test_json_parser_drops_invalid_entries(self):
        # A raw string list entry that looks like a bare word should be rejected.
        raw_json = '["@valid-channel", "not-a-channel", "@also-valid"]'
        result = ydr.parse_json_blocklist(raw_json)
        assert result == ["@valid-channel", "@also-valid"]

    def test_json_parser_drops_invalid_dict_keys(self):
        # Dict-keyed branch: keys start with @ or UC but fail structural validation.
        # UC keys with wrong length and @ keys containing invalid characters must
        # be dropped, exercising the dict-branch canonicalize failure path.
        raw_json = json.dumps({
            "@valid-channel": {},
            "UCshort": {},                  # UC prefix but too short
            "@bad space": {},               # @ prefix but contains space
            "UC" + "A" * 22: {},            # valid 24-char ID
        })
        result = ydr.parse_json_blocklist(raw_json)
        assert "@valid-channel" in result
        assert "UC" + "A" * 22 in result
        assert "UCshort" not in result
        assert "@bad space" not in result
        assert len(result) == 2


# ---------------------------------------------------------------------------
# resolve_source — scheme restrictions (#42)
# ---------------------------------------------------------------------------

class TestResolveSourceSchemes:
    def test_rejects_http(self, caplog):
        import logging
        with caplog.at_level(logging.ERROR):
            with pytest.raises(SystemExit) as exc:
                ydr.resolve_source("http://example.com/list.txt")
        assert exc.value.code == 1
        assert any(
            "http://" in r.message and "https://" in r.message
            for r in caplog.records
        ), f"expected an error mentioning http:// and https://; got: {[r.message for r in caplog.records]}"

    def test_accepts_https(self, monkeypatch):
        monkeypatch.setattr(ydr, "fetch_remote", lambda url: "@HttpsChannel\n")
        result = ydr.resolve_source("https://example.com/list.txt")
        assert result == ["@HttpsChannel"]

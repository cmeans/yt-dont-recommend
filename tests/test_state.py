"""
Tests for yt_dont_recommend.state — load_state, save_state, version tracking.

Functions under test are imported directly from yt_dont_recommend.state, but
patch targets remain yt_dont_recommend.X (the re-exported name in __init__.py),
as they did in the original test_yt_dont_recommend.py.
"""

import json
import logging
from unittest.mock import patch

import yt_dont_recommend as ydr

# ---------------------------------------------------------------------------
# State management (load_state / save_state)
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_load_state_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["last_run"] is None
        assert state["stats"] == {"total_blocked": 0, "total_skipped": 0, "total_failed": 0}

    def test_save_then_load_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        state["stats"]["total_blocked"] = 1
        ydr.save_state(state)

        loaded = ydr.load_state()
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
        # v2 migration: "processed" key should have been dropped
        assert "processed" not in state


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
        import json
        state_file = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        # Write a state file with a future schema version
        state_file.write_text(json.dumps({"state_version": ydr.STATE_VERSION + 1}))
        with caplog.at_level(logging.WARNING):
            ydr.load_state()
        assert any("newer version" in r.message for r in caplog.records)

    def test_state_version_no_warn_on_same_or_older_schema(self, tmp_path, monkeypatch, caplog):
        import json
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


# ---------------------------------------------------------------------------
# Clickbait cache + acted (v3 state keys)
# ---------------------------------------------------------------------------

class TestClickbaitStateKeys:
    def test_fresh_state_has_clickbait_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["clickbait_cache"] == {}

    def test_fresh_state_has_clickbait_acted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["clickbait_acted"] == {}

    def test_old_state_gets_clickbait_keys_on_load(self, tmp_path, monkeypatch):
        state_file = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        state_file.write_text(json.dumps({"blocked_by": {}, "state_version": 2}))
        state = ydr.load_state()
        assert "clickbait_cache" in state
        assert "clickbait_acted" in state

    def test_clickbait_acted_old_entries_pruned_on_load(self, tmp_path, monkeypatch):
        from datetime import datetime, timedelta, timezone
        state_file = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        old_ts = (datetime.now(tz=timezone.utc) - timedelta(days=100)).isoformat()
        recent_ts = (datetime.now(tz=timezone.utc) - timedelta(days=10)).isoformat()
        state_file.write_text(json.dumps({
            "clickbait_acted": {
                "old_video": {"acted_at": old_ts, "title": "old", "channel": "@x"},
                "recent_video": {"acted_at": recent_ts, "title": "new", "channel": "@y"},
            },
            "state_version": 3,
        }))
        state = ydr.load_state()
        assert "old_video" not in state["clickbait_acted"], "entry older than prune threshold must be removed"
        assert "recent_video" in state["clickbait_acted"], "recent entry must be kept"

    def test_clickbait_acted_fresh_entries_kept_on_load(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone
        state_file = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", state_file)
        now_ts = datetime.now(tz=timezone.utc).isoformat()
        state_file.write_text(json.dumps({
            "clickbait_acted": {
                "vid1": {"acted_at": now_ts, "title": "t", "channel": "@c"},
            },
            "state_version": 3,
        }))
        state = ydr.load_state()
        assert "vid1" in state["clickbait_acted"]

    def test_state_version_is_3(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["state_version"] == 3


# ---------------------------------------------------------------------------
# _state_file — fallback when the package attribute is missing
# ---------------------------------------------------------------------------

class TestStateFileFallback:
    def test_state_file_falls_back_to_config_constant_on_attribute_error(self, monkeypatch):
        """When yt_dont_recommend.STATE_FILE is unreachable (e.g. attribute
        deleted during an unusual test), _state_file() falls back to the
        config module constant rather than raising."""
        from yt_dont_recommend import config as cfg_mod
        from yt_dont_recommend import state as state_mod
        monkeypatch.delattr(ydr, "STATE_FILE", raising=False)
        assert state_mod._state_file() == cfg_mod.STATE_FILE


# ---------------------------------------------------------------------------
# load_state — legacy stat-key migration, ucxxx self-mapping, v2 persist
# ---------------------------------------------------------------------------

class TestLoadStateMigrations:
    def _write(self, path, data):
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_legacy_success_skipped_failed_keys_migrated(self, tmp_path, monkeypatch):
        sf = tmp_path / "processed.json"
        self._write(sf, {
            "blocked_by": {},
            "stats": {"success": 5, "skipped": 2, "failed": 1},
        })
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        loaded = ydr.load_state()
        assert loaded["stats"] == {"total_blocked": 5, "total_skipped": 2, "total_failed": 1}

    def test_ucxxx_self_mapping_is_cleared(self, tmp_path, monkeypatch):
        sf = tmp_path / "processed.json"
        self._write(sf, {
            "blocked_by": {},
            "ucxxx_to_handle": {"UCxxx": "UCxxx", "UCyyy": "@someone"},
        })
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        loaded = ydr.load_state()
        assert loaded["ucxxx_to_handle"]["UCxxx"] is None
        assert loaded["ucxxx_to_handle"]["UCyyy"] == "@someone"

    def test_v2_migration_drops_legacy_processed_list(self, tmp_path, monkeypatch, caplog):
        sf = tmp_path / "processed.json"
        self._write(sf, {
            "blocked_by": {"@a": {"sources": ["deslop"]}},
            "processed": ["@a", "@b", "@c"],
        })
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        with caplog.at_level(logging.INFO, logger="yt_dont_recommend.state"):
            loaded = ydr.load_state()
        assert "processed" not in loaded
        rewritten = json.loads(sf.read_text())
        assert "processed" not in rewritten

    def test_v2_migration_persist_error_is_non_fatal(self, tmp_path, monkeypatch):
        """If the post-migration rewrite fails, load_state() still returns
        the migrated in-memory state."""
        sf = tmp_path / "processed.json"
        self._write(sf, {
            "blocked_by": {},
            "processed": ["@a"],
        })
        monkeypatch.setattr(ydr, "STATE_FILE", sf)

        import builtins
        real_open = builtins.open

        def fake_open(path, mode="r", *args, **kwargs):
            if str(path) == str(sf) and "w" in mode:
                raise OSError("disk full")
            return real_open(path, mode, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        loaded = ydr.load_state()
        assert "processed" not in loaded


# ---------------------------------------------------------------------------
# save_state — ensure_data_dir failure and empty-pending_unblock cleanup
# ---------------------------------------------------------------------------

class TestSaveStateBranches:
    def test_ensure_data_dir_failure_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        with patch("yt_dont_recommend.config.ensure_data_dir", side_effect=PermissionError("denied")):
            state = ydr.load_state()
            ydr.save_state(state)
        assert (tmp_path / "processed.json").exists()

    def test_empty_pending_unblock_is_removed_on_save(self, tmp_path, monkeypatch):
        sf = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        state = ydr.load_state()
        state["pending_unblock"] = {}
        ydr.save_state(state)
        written = json.loads(sf.read_text())
        assert "pending_unblock" not in written

    def test_save_state_leaves_no_tmp_file_after_success(self, tmp_path, monkeypatch):
        sf = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        state = ydr.load_state()
        ydr.save_state(state)
        # .tmp has been atomically renamed to .json — no leftover.
        assert not sf.with_suffix(".tmp").exists()
        assert sf.exists()
        # File is valid JSON (round-trips through json.loads).
        json.loads(sf.read_text())

    def test_save_state_does_not_corrupt_original_when_rename_fails(self, tmp_path, monkeypatch):
        """If the atomic rename raises mid-save, the existing state file is untouched."""
        import pytest

        sf = tmp_path / "processed.json"
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        state = ydr.load_state()
        state["blocked_by"]["@first"] = {"sources": ["test"]}
        ydr.save_state(state)
        original_bytes = sf.read_bytes()

        state["blocked_by"]["@second"] = {"sources": ["test"]}
        with patch("pathlib.Path.replace", side_effect=OSError("rename failed")):
            with pytest.raises(OSError):
                ydr.save_state(state)

        # Original on-disk state is byte-for-byte unchanged.
        assert sf.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# _escape_applescript — pure-function AppleScript string escape
# ---------------------------------------------------------------------------

class TestEscapeAppleScript:
    def test_plain_string_unchanged(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript("hello world 123") == "hello world 123"

    def test_escapes_double_quote(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript('he said "hi"') == 'he said \\"hi\\"'

    def test_escapes_backslash(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript("path\\foo") == "path\\\\foo"

    def test_escapes_newline(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript("line1\nline2") == "line1\\nline2"

    def test_escapes_carriage_return(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript("a\rb") == "a\\rb"

    def test_escapes_tab(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript("col1\tcol2") == "col1\\tcol2"

    def test_backslash_before_quote_ordering(self):
        # input: backslash then quote (2 chars)
        # expected: escaped-backslash then escaped-quote (4 chars: \\\")
        # regression guard: if backslash is escaped AFTER quote, the backslash
        # we inserted to escape the quote would itself get re-escaped.
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript('\\"') == '\\\\\\"'

    def test_empty_string(self):
        from yt_dont_recommend.state import _escape_applescript
        assert _escape_applescript("") == ""


# ---------------------------------------------------------------------------
# _desktop_notify — platform-specific subprocess calls
# ---------------------------------------------------------------------------

class TestDesktopNotify:
    def test_macos_uses_osascript(self, monkeypatch):
        from yt_dont_recommend.state import _desktop_notify
        monkeypatch.setattr("sys.platform", "darwin")
        with patch("yt_dont_recommend.state.subprocess.run") as m:
            _desktop_notify("hello")
        args, _ = m.call_args
        assert args[0][0] == "osascript"
        assert 'display notification "hello"' in args[0][-1]

    def test_linux_uses_notify_send(self, monkeypatch):
        from yt_dont_recommend.state import _desktop_notify
        monkeypatch.setattr("sys.platform", "linux")
        with patch("yt_dont_recommend.state.subprocess.run") as m:
            _desktop_notify("hello")
        args, _ = m.call_args
        assert args[0][0] == "notify-send"
        assert args[0][-1] == "hello"

    def test_subprocess_failure_is_swallowed(self, monkeypatch):
        from yt_dont_recommend.state import _desktop_notify
        monkeypatch.setattr("sys.platform", "linux")
        with patch("yt_dont_recommend.state.subprocess.run",
                   side_effect=FileNotFoundError("notify-send missing")):
            _desktop_notify("hello")

    def test_injection_payload_is_defanged(self, monkeypatch):
        # The exact PoC from the security review awareness entry.
        from yt_dont_recommend.state import _desktop_notify
        monkeypatch.setattr("sys.platform", "darwin")
        payload = '@evil"; do shell script "echo pwned"; display notification "'
        with patch("yt_dont_recommend.state.subprocess.run") as m:
            _desktop_notify(payload)
        argv = m.call_args.args[0]
        assert argv[0] == "osascript"
        assert argv[1] == "-e"
        script = argv[2]
        # The outer template adds exactly 2 unescaped quotes (around the message)
        # and 2 more around the title. Total unescaped = 4.
        # Count by stripping every escaped \" first, then counting what's left.
        unescaped_quotes = script.replace('\\"', "").count('"')
        assert unescaped_quotes == 4, (
            f"payload leaked unescaped quotes into AppleScript source: {script!r}"
        )

    def test_write_attention_with_malicious_channel_produces_safe_argv(
        self, tmp_path, monkeypatch
    ):
        # End-to-end integration test: a crafted channel name travels through
        # write_attention -> _desktop_notify and the resulting argv cannot
        # break out of the AppleScript string literal.
        import yt_dont_recommend as ydr
        monkeypatch.setattr("sys.platform", "darwin")
        monkeypatch.setattr(
            "yt_dont_recommend.state.ATTENTION_FILE",
            tmp_path / "needs-attention.txt",
        )
        # Mirror the shape of the unblock.py:237-243 message construction.
        malicious_channel = '@evil"; do shell script "echo pwned"; x "'
        msg = f"1 channel could not be unblocked automatically: {malicious_channel}. Visit myactivity…"
        with patch("yt_dont_recommend.state.subprocess.run") as m:
            with patch("yt_dont_recommend.state.urlopen") as _urlopen:
                ydr.write_attention(msg)
        # Filter to osascript calls since write_attention may also invoke ntfy.
        osascript_calls = [
            c for c in m.call_args_list if c.args and c.args[0] and c.args[0][0] == "osascript"
        ]
        assert osascript_calls, "expected an osascript call on darwin"
        script = osascript_calls[0].args[0][2]
        unescaped_quotes = script.replace('\\"', "").count('"')
        assert unescaped_quotes == 4, (
            f"malicious channel leaked unescaped quotes: {script!r}"
        )


# ---------------------------------------------------------------------------
# _ntfy_notify — success and failure
# ---------------------------------------------------------------------------

class TestNtfyNotify:
    def test_posts_to_ntfy_sh_with_headers(self, monkeypatch):
        from yt_dont_recommend.state import _ntfy_notify

        captured = {}

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=10):
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["data"] = req.data
            return FakeResp()

        monkeypatch.setattr("yt_dont_recommend.state.urlopen", fake_urlopen)
        _ntfy_notify("mytopic", "alert body")
        assert captured["url"] == "https://ntfy.sh/mytopic"
        assert captured["headers"].get("Title") == "yt-dont-recommend"
        assert captured["headers"].get("Priority") == "high"
        assert captured["data"] == b"alert body"

    def test_network_failure_is_swallowed(self, monkeypatch, caplog):
        from yt_dont_recommend.state import _ntfy_notify

        def boom(req, timeout=10):
            raise OSError("no network")

        monkeypatch.setattr("yt_dont_recommend.state.urlopen", boom)
        with caplog.at_level(logging.DEBUG, logger="yt_dont_recommend.state"):
            _ntfy_notify("mytopic", "alert")


# ---------------------------------------------------------------------------
# write_attention — flag-file append + notification fan-out
# ---------------------------------------------------------------------------

class TestWriteAttention:
    def test_writes_to_attention_file_and_triggers_desktop_notify(self, tmp_path, monkeypatch):
        af = tmp_path / "needs-attention.txt"
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        with (
            patch("yt_dont_recommend.state._desktop_notify") as mock_desktop,
            patch("yt_dont_recommend.state._ntfy_notify") as mock_ntfy,
        ):
            ydr.write_attention("selector broke")
        assert af.exists()
        assert "selector broke" in af.read_text()
        mock_desktop.assert_called_once_with("selector broke")
        mock_ntfy.assert_not_called()

    def test_triggers_ntfy_when_topic_configured(self, tmp_path, monkeypatch):
        af = tmp_path / "needs-attention.txt"
        sf = tmp_path / "processed.json"
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        monkeypatch.setattr(ydr, "STATE_FILE", sf)
        state = ydr.load_state()
        state["notify_topic"] = "ydr-abc123"
        ydr.save_state(state)
        with (
            patch("yt_dont_recommend.state._desktop_notify"),
            patch("yt_dont_recommend.state._ntfy_notify") as mock_ntfy,
        ):
            ydr.write_attention("login expired")
        mock_ntfy.assert_called_once_with("ydr-abc123", "login expired")

    def test_ensure_data_dir_failure_is_swallowed(self, tmp_path, monkeypatch):
        af = tmp_path / "needs-attention.txt"
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        with (
            patch("yt_dont_recommend.config.ensure_data_dir", side_effect=PermissionError("denied")),
            patch("yt_dont_recommend.state._desktop_notify"),
            patch("yt_dont_recommend.state._ntfy_notify"),
        ):
            ydr.write_attention("something broke")
        assert af.exists()


# ---------------------------------------------------------------------------
# check_attention_flag — pending-alerts banner
# ---------------------------------------------------------------------------

class TestCheckAttentionFlag:
    def test_no_file_is_noop(self, tmp_path, monkeypatch, capsys):
        af = tmp_path / "needs-attention.txt"
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        ydr.check_attention_flag()
        assert capsys.readouterr().out == ""

    def test_empty_file_is_deleted(self, tmp_path, monkeypatch, capsys):
        af = tmp_path / "needs-attention.txt"
        af.write_text("   \n  ", encoding="utf-8")
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        ydr.check_attention_flag()
        assert not af.exists()
        assert capsys.readouterr().out == ""

    def test_populated_file_prints_banner_non_tty(self, tmp_path, monkeypatch, capsys):
        af = tmp_path / "needs-attention.txt"
        af.write_text("[2026-04-20] selector broke\n", encoding="utf-8")
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)
        ydr.check_attention_flag()
        out = capsys.readouterr().out
        assert "ACTION REQUIRED" in out
        assert "selector broke" in out
        assert "--clear-alerts" in out

    def test_populated_file_waits_for_input_when_tty(self, tmp_path, monkeypatch):
        af = tmp_path / "needs-attention.txt"
        af.write_text("[x] alert\n", encoding="utf-8")
        monkeypatch.setattr("yt_dont_recommend.state.ATTENTION_FILE", af)
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        with patch("builtins.input", return_value="") as mock_input:
            ydr.check_attention_flag()
        mock_input.assert_called_once()

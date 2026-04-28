"""
Tests for yt_dont_recommend.config — covers areas not exercised elsewhere:
ensure_data_dir permission correction, clear_profile_cache, pick_viewport,
load_*_config loaders (yaml paths and fallbacks), setup_logging, and the
non-selector branches of load_selectors_config / write_selector_overrides.
"""

import logging
from unittest.mock import patch

from yt_dont_recommend import config as cfg


class TestPluralHelper:
    def test_singular(self):
        assert cfg._n(1, "channel") == "1 channel"

    def test_plural(self):
        assert cfg._n(0, "channel") == "0 channels"
        assert cfg._n(2, "channel") == "2 channels"


class TestEscapeCssAttrValue:
    def test_plain_string_unchanged(self):
        assert cfg._escape_css_attr_value("Cooking with Joe") == "Cooking with Joe"

    def test_escapes_double_quote(self):
        assert cfg._escape_css_attr_value('he said "hi"') == 'he said \\"hi\\"'

    def test_escapes_backslash(self):
        # Single backslash in input becomes double backslash in output.
        assert cfg._escape_css_attr_value("path\\foo") == "path\\\\foo"

    def test_backslash_before_quote_ordering(self):
        # If quote were escaped first then backslash, the inserted \\ would be
        # re-doubled. Backslash must run first.
        # Input: a, \, ", b → expected: a, \\, \", b (6 chars total).
        input_s = "a" + "\\" + '"' + "b"
        expected = "a" + "\\\\" + '\\"' + "b"
        assert cfg._escape_css_attr_value(input_s) == expected

    def test_empty_string(self):
        assert cfg._escape_css_attr_value("") == ""

    def test_escapes_newline_to_css_hex_escape(self):
        # CSS Syntax Module Level 3 § 4.3.5: an unescaped LF inside a quoted
        # string produces a <bad-string-token>. § 4.3.7: \n in a CSS string
        # is the literal char "n", not LF — so the AppleScript-style \\n
        # port does NOT work for CSS. Use the hex form \A with trailing
        # space terminator.
        assert cfg._escape_css_attr_value("line one\nline two") == "line one\\A line two"

    def test_escapes_carriage_return_to_css_hex_escape(self):
        # § 4.3.5 also flags CR; correct form per § 4.3.7 is \D plus space.
        assert cfg._escape_css_attr_value("line one\rline two") == "line one\\D line two"

    def test_combined_special_characters_ordering(self):
        # All four handled chars in one input. Order of replacement matters:
        # backslash MUST be doubled first so the backslashes that \A and \D
        # introduce later are not re-doubled.
        # Input: \, ", \n, \r — expected: \\, \", \A<sp>, \D<sp>.
        input_s = "\\" + '"' + "\n" + "\r"
        expected = "\\\\" + '\\"' + "\\A " + "\\D "
        assert cfg._escape_css_attr_value(input_s) == expected

    def test_tab_is_not_escaped(self):
        # § 4.3.5 only flags newline / CR / form-feed as bad-string-tokens.
        # Horizontal tab inside a quoted string is a literal tab — pass it
        # through verbatim.
        assert cfg._escape_css_attr_value("a\tb") == "a\tb"


class TestResolveVersion:
    def test_returns_installed_distribution_version(self):
        """Happy path — the installed version string."""
        v = cfg._resolve_version()
        assert isinstance(v, str)
        assert v  # non-empty

    def test_falls_back_to_0_0_0_when_metadata_unavailable(self, monkeypatch):
        """When importlib.metadata.version() raises (e.g. editable install
        without distribution metadata), _resolve_version returns "0.0.0"."""
        import importlib.metadata

        def boom(_name):
            raise importlib.metadata.PackageNotFoundError("yt-dont-recommend")

        monkeypatch.setattr(importlib.metadata, "version", boom)
        assert cfg._resolve_version() == "0.0.0"


class TestEnsureDataDir:
    def test_chmods_directory_when_permissions_too_open(self, tmp_path, monkeypatch):
        """If the data dir exists with overly-permissive mode, ensure_data_dir
        tightens it to 0o700."""
        data = tmp_path / "data"
        data.mkdir(mode=0o755)
        profile = data / "browser-profile"
        profile.mkdir(mode=0o755)
        monkeypatch.setattr(cfg, "DATA_DIR", data)
        monkeypatch.setattr(cfg, "PROFILE_DIR", profile)
        cfg.ensure_data_dir()
        import stat
        assert stat.S_IMODE(data.stat().st_mode) == 0o700
        assert stat.S_IMODE(profile.stat().st_mode) == 0o700


class TestClearProfileCache:
    def test_noop_when_profile_default_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "PROFILE_DIR", tmp_path / "profile")
        cfg.clear_profile_cache()  # no error

    def test_removes_known_cache_subdirs(self, tmp_path, monkeypatch):
        profile = tmp_path / "profile"
        default = profile / "Default"
        (default / "Cache").mkdir(parents=True)
        (default / "Code Cache").mkdir()
        (default / "NonCache").mkdir()  # should be kept
        monkeypatch.setattr(cfg, "PROFILE_DIR", profile)
        cfg.clear_profile_cache()
        assert not (default / "Cache").exists()
        assert not (default / "Code Cache").exists()
        assert (default / "NonCache").exists()


class TestPickViewport:
    def test_returns_a_pool_member_with_width_height(self):
        vp = cfg.pick_viewport()
        assert "width" in vp and "height" in vp
        assert vp in cfg._VIEWPORT_POOL


class TestLoadTimingConfig:
    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.yaml")
        assert cfg.load_timing_config() == {}

    def test_yaml_missing_returns_empty_and_warns(self, tmp_path, monkeypatch, caplog):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("timing:\n  min_delay: 5.0\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        with (
            patch.dict("sys.modules", {"yaml": None}),
            caplog.at_level(logging.WARNING, logger="yt_dont_recommend.config"),
        ):
            assert cfg.load_timing_config() == {}

    def test_valid_file_returns_allowed_keys_only(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "timing:\n"
            "  min_delay: 5.0\n"
            "  max_delay: 9.0\n"
            "  garbage: 1\n"
        )
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        loaded = cfg.load_timing_config()
        assert loaded == {"min_delay": 5.0, "max_delay": 9.0}

    def test_non_dict_timing_section_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("timing: not-a-dict\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_timing_config() == {}

    def test_unparseable_yaml_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("timing: : : : invalid\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_timing_config() == {}


class TestLoadBrowserConfig:
    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.yaml")
        assert cfg.load_browser_config() == {}

    def test_yaml_missing_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("browser:\n  use_system_chrome: false\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        with patch.dict("sys.modules", {"yaml": None}):
            assert cfg.load_browser_config() == {}

    def test_valid_file_returns_use_system_chrome(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("browser:\n  use_system_chrome: false\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_browser_config() == {"use_system_chrome": False}

    def test_non_dict_browser_section_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("browser: 'scalar'\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_browser_config() == {}

    def test_unparseable_yaml_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("browser: : : invalid\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_browser_config() == {}


class TestLoadScheduleConfig:
    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.yaml")
        assert cfg.load_schedule_config() == {}

    def test_yaml_missing_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("schedule:\n  blocklist_runs: 2\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        with patch.dict("sys.modules", {"yaml": None}):
            assert cfg.load_schedule_config() == {}

    def test_full_schedule_block_loaded(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "schedule:\n"
            "  blocklist_runs: 2\n"
            "  clickbait_runs: 4\n"
            "  headless: false\n"
        )
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        loaded = cfg.load_schedule_config()
        assert loaded == {"blocklist_runs": 2, "clickbait_runs": 4, "headless": False}

    def test_non_dict_schedule_section_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("schedule: 42\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_schedule_config() == {}

    def test_unparseable_yaml_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("schedule: : :\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_schedule_config() == {}


class TestLoadAutoUpgradeConfig:
    """Covers every branch of load_auto_upgrade_config() — Codecov on PR #58
    flagged 20 missing lines in this loader. New for STATE_VERSION 4 / #55."""

    def test_no_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.yaml")
        assert cfg.load_auto_upgrade_config() == {}

    def test_yaml_missing_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  delay_days: 7\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        with patch.dict("sys.modules", {"yaml": None}):
            assert cfg.load_auto_upgrade_config() == {}

    def test_delay_days_loaded(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  delay_days: 7\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {"delay_days": 7}

    def test_zero_delay_days_accepted(self, tmp_path, monkeypatch):
        """delay_days=0 is the documented "disable the delay" knob — must be
        accepted, not silently dropped by the >= 0 guard."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  delay_days: 0\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {"delay_days": 0}

    def test_negative_delay_days_rejected(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  delay_days: -3\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        # Negative gets silently dropped — caller falls back to AUTO_UPGRADE_DELAY_DAYS.
        assert cfg.load_auto_upgrade_config() == {}

    def test_non_int_delay_days_rejected(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  delay_days: 'three'\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        # Non-coercible string gets silently dropped via try/except (TypeError, ValueError).
        assert cfg.load_auto_upgrade_config() == {}

    def test_string_int_delay_days_coerced(self, tmp_path, monkeypatch):
        """YAML quotes coerce to string; if it's still a valid int(), accept it."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  delay_days: '5'\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {"delay_days": 5}

    def test_non_dict_auto_upgrade_section_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade: 42\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {}

    def test_missing_auto_upgrade_section_returns_empty(self, tmp_path, monkeypatch):
        """A config.yaml that exists but has no auto_upgrade: section must
        return {} (caller falls back to AUTO_UPGRADE_DELAY_DAYS)."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("timing:\n  min_delay: 1.0\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {}

    def test_missing_delay_days_key_returns_empty(self, tmp_path, monkeypatch):
        """auto_upgrade: section present but no delay_days key — return {}
        rather than {"delay_days": <some default>}, so the call site's own
        fallback to AUTO_UPGRADE_DELAY_DAYS is the single source of default."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade:\n  some_other_key: foo\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {}

    def test_unparseable_yaml_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("auto_upgrade: : :\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_auto_upgrade_config() == {}


class TestLoadSelectorsConfigExtraBranches:
    """Covers the selectors loader branches not exercised by test_selectors.py."""

    def test_yaml_missing_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("selectors:\n  feed_card: foo\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        with patch.dict("sys.modules", {"yaml": None}):
            assert cfg.load_selectors_config() == {}

    def test_non_dict_selectors_section_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("selectors: 'scalar'\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_selectors_config() == {}

    def test_invalid_type_for_list_key_is_skipped(self, tmp_path, monkeypatch):
        """Integer provided for a list-typed key → silently skipped."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("selectors:\n  menu_buttons: 42\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_selectors_config() == {}

    def test_list_value_for_string_key_joined_with_comma(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "selectors:\n"
            "  feed_card:\n"
            "    - one\n"
            "    - two\n"
        )
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        loaded = cfg.load_selectors_config()
        assert loaded["feed_card"] == "one, two"

    def test_invalid_type_for_string_key_skipped(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("selectors:\n  feed_card: 123\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_selectors_config() == {}

    def test_unparseable_yaml_returns_empty(self, tmp_path, monkeypatch):
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("selectors: : :\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        assert cfg.load_selectors_config() == {}


class TestWriteSelectorOverridesExistingNonDict:
    def test_existing_selectors_non_dict_is_reset(self, tmp_path, monkeypatch):
        """If config.yaml already has a `selectors:` key that is not a dict
        (e.g. a list or scalar), write_selector_overrides resets it to a dict."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("selectors: broken_value\n")
        monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
        cfg.write_selector_overrides({"feed_card": "custom-card"})
        # Re-read and verify it's a dict with our override present
        import yaml
        reloaded = yaml.safe_load(cfg_file.read_text())
        assert isinstance(reloaded["selectors"], dict)
        assert reloaded["selectors"]["feed_card"] == "custom-card"


class TestSetupLogging:
    def test_configures_root_handlers_and_sets_level(self, tmp_path, monkeypatch):
        """setup_logging installs a rotating file handler + stream handler,
        then suppresses httpx/httpcore."""
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(cfg, "PROFILE_DIR", tmp_path / "data" / "browser-profile")
        monkeypatch.setattr(cfg, "LOG_FILE", tmp_path / "data" / "run.log")

        # Save and restore handlers to avoid polluting other tests.
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        try:
            root.handlers.clear()
            cfg.setup_logging(verbose=True)
            # Both a RotatingFileHandler and a StreamHandler should be installed
            assert any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers)
            assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
            assert root.level == logging.DEBUG
            # noisy modules muted
            assert logging.getLogger("httpx").level == logging.WARNING
            assert logging.getLogger("httpcore").level == logging.WARNING
        finally:
            # Close file handlers so tmp_path cleanup works on Windows-ish setups
            for h in list(root.handlers):
                try:
                    h.close()
                except Exception:
                    pass
            root.handlers = saved_handlers
            root.setLevel(saved_level)

    def test_non_verbose_sets_info_level(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "DATA_DIR", tmp_path / "data")
        monkeypatch.setattr(cfg, "PROFILE_DIR", tmp_path / "data" / "browser-profile")
        monkeypatch.setattr(cfg, "LOG_FILE", tmp_path / "data" / "run.log")
        root = logging.getLogger()
        saved_handlers = list(root.handlers)
        saved_level = root.level
        try:
            root.handlers.clear()
            cfg.setup_logging(verbose=False)
            assert root.level == logging.INFO
        finally:
            for h in list(root.handlers):
                try:
                    h.close()
                except Exception:
                    pass
            root.handlers = saved_handlers
            root.setLevel(saved_level)

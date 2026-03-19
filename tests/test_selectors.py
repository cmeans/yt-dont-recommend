"""
Tests for the selector registry: _SELECTOR_DEFAULTS, load_selectors_config,
get_selectors, and config override merging.
"""

from unittest.mock import patch

from yt_dont_recommend.config import (
    _SELECTOR_DEFAULTS,
    get_selectors,
    load_selectors_config,
)


class TestSelectorDefaults:
    """Verify the defaults dict has the expected shape and keys."""

    def test_all_expected_keys_present(self):
        expected_keys = {
            "feed_card", "channel_link", "watch_link", "title_link",
            "title_text", "menu_buttons", "menu_items",
            "not_interested_items", "not_interested_inner_btn",
            "dont_recommend_phrases", "not_interested_phrase",
            "login_check", "subscription_links",
            "channel_name_selectors",
        }
        assert set(_SELECTOR_DEFAULTS.keys()) == expected_keys

    def test_list_typed_keys(self):
        """Keys that must be lists (tried in order)."""
        for key in ("title_link", "menu_buttons", "dont_recommend_phrases",
                     "channel_name_selectors"):
            assert isinstance(_SELECTOR_DEFAULTS[key], list), f"{key} should be a list"

    def test_string_typed_keys(self):
        """Keys that must be strings (CSS selectors or phrases)."""
        for key in ("feed_card", "channel_link", "watch_link", "title_text",
                     "menu_items", "not_interested_items",
                     "not_interested_inner_btn", "not_interested_phrase",
                     "login_check", "subscription_links"):
            assert isinstance(_SELECTOR_DEFAULTS[key], str), f"{key} should be a string"


class TestLoadSelectorsConfig:
    """Test config file reading and type coercion."""

    def test_no_config_file(self, tmp_path):
        """Returns empty dict when config.yaml does not exist."""
        with patch("yt_dont_recommend.config.CONFIG_FILE", tmp_path / "nope.yaml"):
            assert load_selectors_config() == {}

    def test_no_selectors_section(self, tmp_path):
        """Returns empty dict when config.yaml has no selectors: key."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("timing:\n  min_delay: 5\n")
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            assert load_selectors_config() == {}

    def test_string_override(self, tmp_path):
        """A string value overrides a string default."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("selectors:\n  feed_card: 'div.new-card'\n")
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            result = load_selectors_config()
            assert result == {"feed_card": "div.new-card"}

    def test_list_override(self, tmp_path):
        """A list value overrides a list default."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "selectors:\n"
            "  menu_buttons:\n"
            "    - \"button[aria-label='Menú']\"\n"
            "    - \"button[aria-label='More']\"\n"
        )
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            result = load_selectors_config()
            assert result == {
                "menu_buttons": ["button[aria-label='Menú']", "button[aria-label='More']"]
            }

    def test_single_string_for_list_key(self, tmp_path):
        """A scalar string for a list-typed key is promoted to a one-item list."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("selectors:\n  menu_buttons: \"button[aria-label='Custom']\"\n")
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            result = load_selectors_config()
            assert result == {"menu_buttons": ["button[aria-label='Custom']"]}

    def test_unknown_keys_ignored(self, tmp_path, caplog):
        """Unrecognised keys are ignored and warned about."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("selectors:\n  bogus_key: 'foo'\n  feed_card: 'div.x'\n")
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            import logging
            with caplog.at_level(logging.WARNING):
                result = load_selectors_config()
            assert "feed_card" in result
            assert "bogus_key" not in result
            assert "bogus_key" in caplog.text

    def test_selectors_updated_at_not_warned(self, tmp_path, caplog):
        """selectors_updated_at is a known metadata key, not warned."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("selectors:\n  selectors_updated_at: '2026-03-19'\n")
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            import logging
            with caplog.at_level(logging.WARNING):
                result = load_selectors_config()
            assert result == {}
            assert "unrecognised" not in caplog.text

    def test_localization_phrases(self, tmp_path):
        """Non-English users can override text phrases for localization."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "selectors:\n"
            "  dont_recommend_phrases:\n"
            "    - 'no recomendar el canal'\n"
            "    - \"don't recommend\"\n"
            "  not_interested_phrase: 'no me interesa'\n"
        )
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            result = load_selectors_config()
            assert result["dont_recommend_phrases"] == ["no recomendar el canal", "don't recommend"]
            assert result["not_interested_phrase"] == "no me interesa"


class TestGetSelectors:
    """Test the merged selector dict."""

    def test_defaults_when_no_config(self, tmp_path):
        """Without a config file, get_selectors returns the code defaults."""
        with patch("yt_dont_recommend.config.CONFIG_FILE", tmp_path / "nope.yaml"):
            sels = get_selectors()
            assert sels == _SELECTOR_DEFAULTS

    def test_override_merges_on_top(self, tmp_path):
        """Config overrides replace specific keys, leaving others as defaults."""
        cfg = tmp_path / "config.yaml"
        cfg.write_text("selectors:\n  feed_card: 'div.custom-card'\n")
        with patch("yt_dont_recommend.config.CONFIG_FILE", cfg):
            sels = get_selectors()
            assert sels["feed_card"] == "div.custom-card"
            # Other keys should be unchanged
            assert sels["channel_link"] == _SELECTOR_DEFAULTS["channel_link"]
            assert sels["menu_buttons"] == _SELECTOR_DEFAULTS["menu_buttons"]

    def test_all_keys_present(self, tmp_path):
        """The merged dict always has every key from _SELECTOR_DEFAULTS."""
        with patch("yt_dont_recommend.config.CONFIG_FILE", tmp_path / "nope.yaml"):
            sels = get_selectors()
            assert set(sels.keys()) == set(_SELECTOR_DEFAULTS.keys())

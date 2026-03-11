"""Tests for src/yt_dont_recommend/clickbait.py"""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yt_dont_recommend.clickbait import (
    _deep_merge,
    _DEFAULT_CONFIG,
    classify_thumbnail,
    classify_title,
    classify_transcript,
    classify_video,
    extract_json,
    load_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(overrides: dict | None = None) -> dict:
    """Return a deep copy of the default config, optionally merged with overrides."""
    base = deepcopy(_DEFAULT_CONFIG)
    if overrides:
        return _deep_merge(base, overrides)
    return base


def _title_result(is_cb: bool, confidence: float) -> dict:
    return {
        "is_clickbait": is_cb,
        "confidence": confidence,
        "reasoning": "test",
        "stage": "title",
        "model": "phi3.5",
        "video_id": "vid1",
        "elapsed": 0.1,
    }


def _thumb_result(is_cb: bool, confidence: float) -> dict:
    return {
        "is_clickbait": is_cb,
        "confidence": confidence,
        "reasoning": "test",
        "stage": "thumbnail",
        "model": "gemma3:4b",
        "video_id": "vid1",
        "elapsed": 0.1,
    }


def _tx_result(is_cb: bool, confidence: float, defer: bool = False) -> dict:
    r = {
        "is_clickbait": is_cb,
        "confidence": confidence,
        "reasoning": "test",
        "stage": "transcript",
        "model": "phi3.5",
        "video_id": "vid1",
        "elapsed": 0.1,
        "tx_status": "ok",
    }
    if defer:
        r["_defer_to_title"] = True
    return r


# ---------------------------------------------------------------------------
# _deep_merge
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_shallow_override(self):
        result = _deep_merge({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_nested_merge(self):
        base     = {"video": {"title": {"threshold": 0.75, "model": {"name": "phi3.5"}}}}
        override = {"video": {"title": {"threshold": 0.9}}}
        result   = _deep_merge(base, override)
        assert result["video"]["title"]["threshold"] == 0.9
        assert result["video"]["title"]["model"]["name"] == "phi3.5"  # preserved

    def test_add_new_key(self):
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_does_not_mutate_base(self):
        base = {"a": {"b": 1}}
        _deep_merge(base, {"a": {"b": 2}})
        assert base["a"]["b"] == 1

    def test_does_not_mutate_override(self):
        override = {"a": {"b": 2}}
        _deep_merge({"a": {"b": 1}}, override)
        assert override["a"]["b"] == 2


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path):
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg == _DEFAULT_CONFIG

    def test_defaults_are_independent_copies(self, tmp_path):
        cfg1 = load_config(tmp_path / "nonexistent.yaml")
        cfg2 = load_config(tmp_path / "nonexistent.yaml")
        cfg1["video"]["title"]["threshold"] = 0.99
        assert cfg2["video"]["title"]["threshold"] == 0.75

    def test_merges_user_file(self, tmp_path):
        pytest.importorskip("yaml")
        cfg_file = tmp_path / "cb.yaml"
        cfg_file.write_text(
            "video:\n  title:\n    threshold: 0.9\n", encoding="utf-8"
        )
        cfg = load_config(cfg_file)
        assert cfg["video"]["title"]["threshold"] == 0.9
        # Other defaults preserved
        assert cfg["video"]["title"]["ambiguous_low"] == 0.4
        assert cfg["video"]["thumbnail"]["enabled"] is False

    def test_enables_thumbnail_via_config(self, tmp_path):
        pytest.importorskip("yaml")
        cfg_file = tmp_path / "cb.yaml"
        cfg_file.write_text(
            "video:\n  thumbnail:\n    enabled: true\n    model:\n      name: gemma3:4b\n",
            encoding="utf-8",
        )
        cfg = load_config(cfg_file)
        assert cfg["video"]["thumbnail"]["enabled"] is True
        assert cfg["video"]["thumbnail"]["model"]["name"] == "gemma3:4b"

    def test_returns_defaults_on_bad_yaml(self, tmp_path):
        pytest.importorskip("yaml")
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text(":\n  - bad: [yaml", encoding="utf-8")
        cfg = load_config(cfg_file)
        assert cfg == _DEFAULT_CONFIG

    def test_returns_defaults_when_yaml_not_installed(self, tmp_path):
        cfg_file = tmp_path / "cb.yaml"
        cfg_file.write_text("video:\n  title:\n    threshold: 0.9\n", encoding="utf-8")
        with patch.dict("sys.modules", {"yaml": None}):
            cfg = load_config(cfg_file)
        assert cfg["video"]["title"]["threshold"] == 0.75  # default, not 0.9


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------


class TestExtractJson:
    def test_clean_json(self):
        raw = '{"is_clickbait": true, "confidence": 0.9, "reasoning": "test"}'
        r   = extract_json(raw)
        assert r["is_clickbait"] is True
        assert r["confidence"] == 0.9

    def test_strips_json_fence(self):
        raw = '```json\n{"is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}\n```'
        r   = extract_json(raw)
        assert r["is_clickbait"] is False

    def test_strips_plain_fence(self):
        raw = '```\n{"is_clickbait": false, "confidence": 0.2, "reasoning": "ok"}\n```'
        r   = extract_json(raw)
        assert r["is_clickbait"] is False

    def test_json_embedded_in_prose(self):
        raw = 'Sure! Here is the result: {"is_clickbait": true, "confidence": 0.8, "reasoning": "bad"} Hope that helps.'
        r   = extract_json(raw)
        assert r["is_clickbait"] is True
        assert r["confidence"] == 0.8

    def test_regex_fallback(self):
        raw = '"is_clickbait": true, "confidence": 0.7, "reasoning": "extracted"'
        r   = extract_json(raw)
        assert r["is_clickbait"] is True
        assert r["confidence"] == 0.7
        assert r["_parse"] == "regex-fallback"

    def test_parse_failure_returns_safe_default(self):
        r = extract_json("totally unparseable model babble")
        assert r["is_clickbait"] is False
        assert r["confidence"] == 0.0
        assert r["_parse"] == "failed"

    def test_false_value(self):
        raw = '{"is_clickbait": false, "confidence": 0.05, "reasoning": "clean title"}'
        r   = extract_json(raw)
        assert r["is_clickbait"] is False
        assert r["confidence"] == 0.05


# ---------------------------------------------------------------------------
# classify_title
# ---------------------------------------------------------------------------


class TestClassifyTitle:
    def test_returns_result_with_required_keys(self):
        with patch(
            "yt_dont_recommend.clickbait._ollama_chat",
            return_value='{"is_clickbait": false, "confidence": 0.1, "reasoning": "fine"}',
        ):
            result = classify_title("vid1", "How to bake bread", _cfg())
        assert result["stage"] == "title"
        assert result["model"] == "llama3.1:8b"
        assert result["video_id"] == "vid1"
        assert "elapsed" in result

    def test_ollama_error_returns_safe_default(self):
        with patch(
            "yt_dont_recommend.clickbait._ollama_chat",
            side_effect=RuntimeError("connection refused"),
        ):
            result = classify_title("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert result["confidence"] == 0.0
        assert "error" in result

    def test_uses_model_name_from_config(self):
        cfg = _cfg({"video": {"title": {"model": {"name": "llama3.2:1b"}}}})
        calls = []

        def mock_chat(model, prompt, **kw):
            calls.append(model)
            return '{"is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}'

        with patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat):
            classify_title("vid1", "A title", cfg)
        assert calls[0] == "llama3.2:1b"


# ---------------------------------------------------------------------------
# classify_thumbnail
# ---------------------------------------------------------------------------


class TestClassifyThumbnail:
    def test_no_image_returns_safe_default(self):
        with patch("yt_dont_recommend.clickbait._fetch_thumbnail_b64", return_value=None):
            result = classify_thumbnail("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert result["status"] == "no_image"

    def test_two_step_makes_two_ollama_calls(self):
        calls = []

        def mock_chat(model, prompt, img_b64=None, **kw):
            calls.append({"has_image": img_b64 is not None})
            if img_b64:
                return "PEOPLE: none\nTEXT: none\nGRAPHICS: none\nCOMPOSITION: clean"
            return '{"is_clickbait": false, "confidence": 0.1, "reasoning": "no signals"}'

        with (
            patch("yt_dont_recommend.clickbait._fetch_thumbnail_b64", return_value="imgdata"),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            result = classify_thumbnail("vid1", "A title", _cfg())

        assert len(calls) == 2
        assert calls[0]["has_image"] is True   # describe step sends image
        assert calls[1]["has_image"] is False  # classify step has no image
        assert result["stage"] == "thumbnail"
        assert "_description" in result

    def test_single_step_makes_one_ollama_call(self):
        calls = []

        def mock_chat(model, prompt, img_b64=None, **kw):
            calls.append({"has_image": img_b64 is not None})
            return '{"is_clickbait": true, "confidence": 0.9, "reasoning": "S1 present"}'

        cfg = _cfg({"video": {"thumbnail": {"two_step": False}}})
        with (
            patch("yt_dont_recommend.clickbait._fetch_thumbnail_b64", return_value="imgdata"),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            result = classify_thumbnail("vid1", "A title", cfg)

        assert len(calls) == 1
        assert calls[0]["has_image"] is True

    def test_ollama_error_returns_safe_default(self):
        with (
            patch("yt_dont_recommend.clickbait._fetch_thumbnail_b64", return_value="imgdata"),
            patch(
                "yt_dont_recommend.clickbait._ollama_chat",
                side_effect=RuntimeError("timeout"),
            ),
        ):
            result = classify_thumbnail("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# classify_transcript
# ---------------------------------------------------------------------------


class TestClassifyTranscript:
    def test_no_transcript_pass(self):
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "disabled")):
            result = classify_transcript("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert result["tx_status"] == "disabled"

    def test_no_transcript_flag(self):
        cfg = _cfg({"video": {"transcript": {"no_transcript": "flag"}}})
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "disabled")):
            result = classify_transcript("vid1", "A title", cfg)
        assert result["is_clickbait"] is True
        assert result["confidence"] == 0.75

    def test_no_transcript_title_only(self):
        cfg = _cfg({"video": {"transcript": {"no_transcript": "title-only"}}})
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "not_found")):
            result = classify_transcript("vid1", "A title", cfg)
        assert result["_defer_to_title"] is True

    def test_with_transcript_calls_ollama(self):
        with (
            patch(
                "yt_dont_recommend.clickbait._fetch_transcript",
                return_value=("This is a transcript about bread baking.", "ok"),
            ),
            patch(
                "yt_dont_recommend.clickbait._ollama_chat",
                return_value='{"is_clickbait": false, "confidence": 0.1, "reasoning": "on-topic"}',
            ),
        ):
            result = classify_transcript("vid1", "How to bake bread", _cfg())
        assert result["is_clickbait"] is False
        assert result["tx_status"] == "ok"
        assert result["tx_chars"] > 0

    def test_no_api_returns_pass(self):
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "no_api")):
            result = classify_transcript("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert result["tx_status"] == "no_api"

    def test_ollama_error_returns_safe_default(self):
        with (
            patch(
                "yt_dont_recommend.clickbait._fetch_transcript",
                return_value=("some transcript text", "ok"),
            ),
            patch(
                "yt_dont_recommend.clickbait._ollama_chat",
                side_effect=RuntimeError("timeout"),
            ),
        ):
            result = classify_transcript("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert "error" in result


# ---------------------------------------------------------------------------
# classify_video  (pipeline)
# ---------------------------------------------------------------------------


class TestClassifyVideoPipeline:
    """Tests for the pipeline orchestrator. Individual classifiers are mocked."""

    def test_title_only_not_flagged(self):
        """Title is clearly not clickbait — pipeline stops after title stage."""
        with patch(
            "yt_dont_recommend.clickbait.classify_title",
            return_value=_title_result(False, 0.1),
        ):
            result = classify_video("vid1", "How to bake bread")

        assert result["flagged"] is False
        assert result["stages"] == ["title"]
        assert result["thumbnail_result"] is None
        assert result["transcript_result"] is None

    def test_title_only_flagged(self):
        """Title confidence >= threshold → flagged immediately."""
        with patch(
            "yt_dont_recommend.clickbait.classify_title",
            return_value=_title_result(True, 0.9),
        ):
            result = classify_video("vid1", "YOU WON'T BELIEVE THIS")

        assert result["flagged"] is True
        assert result["stages"] == ["title"]

    def test_thumbnail_disabled_by_default(self):
        """Thumbnail stage is opt-in; should not run with default config."""
        with patch(
            "yt_dont_recommend.clickbait.classify_title",
            return_value=_title_result(True, 0.55),  # ambiguous
        ):
            result = classify_video("vid1", "Some title")

        assert "thumbnail" not in result["stages"]
        assert result["thumbnail_result"] is None

    def test_thumbnail_fires_when_enabled_and_ambiguous(self):
        """Thumbnail stage runs when enabled and title lands in [ambiguous_low, threshold)."""
        cfg = _cfg({"video": {"thumbnail": {"enabled": True}}})
        with (
            patch(
                "yt_dont_recommend.clickbait.classify_title",
                return_value=_title_result(True, 0.55),
            ),
            patch(
                "yt_dont_recommend.clickbait.classify_thumbnail",
                return_value=_thumb_result(True, 0.9),
            ),
        ):
            result = classify_video("vid1", "Some title", cfg)

        assert "thumbnail" in result["stages"]
        assert result["flagged"] is True
        assert result["confidence"] == 0.9

    def test_thumbnail_does_not_fire_when_title_clearly_not_clickbait(self):
        """Thumbnail should not run when title confidence is below ambiguous_low."""
        cfg = _cfg({"video": {"thumbnail": {"enabled": True}}})
        with patch(
            "yt_dont_recommend.clickbait.classify_title",
            return_value=_title_result(False, 0.1),  # below ambiguous_low
        ):
            result = classify_video("vid1", "A clean title", cfg)

        assert "thumbnail" not in result["stages"]

    def test_thumbnail_does_not_fire_when_title_already_flagged(self):
        """If title confidence >= threshold, skip thumbnail (already decided)."""
        cfg = _cfg({"video": {"thumbnail": {"enabled": True}}})
        with patch(
            "yt_dont_recommend.clickbait.classify_title",
            return_value=_title_result(True, 0.9),  # already over threshold
        ):
            result = classify_video("vid1", "SHOCKING title", cfg)

        assert "thumbnail" not in result["stages"]

    def test_transcript_fires_when_enabled_and_still_ambiguous(self):
        """Transcript stage runs when enabled and result after title is still ambiguous."""
        cfg = _cfg({"video": {"transcript": {"enabled": True}}})
        with (
            patch(
                "yt_dont_recommend.clickbait.classify_title",
                return_value=_title_result(True, 0.55),
            ),
            patch(
                "yt_dont_recommend.clickbait.classify_transcript",
                return_value=_tx_result(False, 0.1),  # transcript says not clickbait
            ),
        ):
            result = classify_video("vid1", "Some title", cfg)

        assert "transcript" in result["stages"]
        assert result["flagged"] is False
        assert result["confidence"] == 0.1

    def test_transcript_defer_to_title(self):
        """When transcript returns _defer_to_title, the title result is kept."""
        cfg = _cfg({"video": {"transcript": {"enabled": True}}})
        title_r = _title_result(True, 0.55)
        with (
            patch("yt_dont_recommend.clickbait.classify_title", return_value=title_r),
            patch(
                "yt_dont_recommend.clickbait.classify_transcript",
                return_value=_tx_result(False, 0.0, defer=True),
            ),
        ):
            result = classify_video("vid1", "Some title", cfg)

        # Confidence/is_clickbait unchanged from title stage
        assert result["confidence"] == 0.55
        assert result["is_clickbait"] is True

    def test_result_always_has_required_keys(self):
        with patch(
            "yt_dont_recommend.clickbait.classify_title",
            return_value=_title_result(False, 0.1),
        ):
            result = classify_video("vid1", "A title")

        for key in ("video_id", "title", "is_clickbait", "confidence", "flagged",
                    "stages", "title_result", "thumbnail_result", "transcript_result",
                    "classified_at"):
            assert key in result, f"missing key: {key}"

    def test_uses_provided_cfg(self):
        """classify_video passes cfg through to classify_title."""
        cfg = _cfg({"video": {"title": {"threshold": 0.5}}})
        calls = []

        def mock_title(vid, ttl, passed_cfg):
            calls.append(passed_cfg)
            return _title_result(False, 0.1)

        with patch("yt_dont_recommend.clickbait.classify_title", side_effect=mock_title):
            classify_video("vid1", "A title", cfg)

        assert calls[0]["video"]["title"]["threshold"] == 0.5

    def test_loads_default_config_when_cfg_is_none(self):
        """When cfg=None, load_config() is called."""
        with (
            patch("yt_dont_recommend.clickbait.load_config", return_value=_cfg()) as mock_load,
            patch(
                "yt_dont_recommend.clickbait.classify_title",
                return_value=_title_result(False, 0.1),
            ),
        ):
            classify_video("vid1", "A title", cfg=None)
        mock_load.assert_called_once()


# ---------------------------------------------------------------------------
# Missing dependency behaviour
# ---------------------------------------------------------------------------


class TestMissingDependencies:
    """Verify that absent optional modules produce correct warnings/errors
    rather than silent wrong results."""

    def test_ollama_missing_raises_import_error_with_hint(self):
        """_ollama_chat must raise ImportError with an install hint when
        ollama is not importable — not silently return a wrong result."""
        from yt_dont_recommend.clickbait import _ollama_chat
        with patch.dict("sys.modules", {"ollama": None}):
            with pytest.raises(ImportError, match="ollama not installed"):
                _ollama_chat("llama3.1:8b", "classify this")

    def test_classify_title_handles_ollama_missing_gracefully(self):
        """When ollama is absent, classify_title must return a safe not-clickbait
        default (with 'error' key) rather than propagating the ImportError."""
        with patch.dict("sys.modules", {"ollama": None}):
            result = classify_title("vid1", "A title", _cfg())
        assert result["is_clickbait"] is False
        assert result["confidence"] == 0.0
        assert "error" in result

    def test_pyyaml_missing_warning_mentions_customizations_and_install(self, tmp_path, caplog):
        """When pyyaml is absent and a config file exists, the warning must
        tell the user their customisations are ignored and give the install
        command — not just silently fall back."""
        import logging
        cfg_file = tmp_path / "cb.yaml"
        cfg_file.write_text("video:\n  title:\n    threshold: 0.9\n", encoding="utf-8")
        with patch.dict("sys.modules", {"yaml": None}):
            with caplog.at_level(logging.WARNING, logger="yt_dont_recommend.clickbait"):
                load_config(cfg_file)
        msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("customizations" in m for m in msgs), \
            "warning should mention that customizations are lost"
        assert any("pip install pyyaml" in m for m in msgs), \
            "warning should include the install command"

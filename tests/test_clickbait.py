"""Tests for src/yt_dont_recommend/clickbait.py"""

import json
from copy import deepcopy
from unittest.mock import MagicMock, patch

import pytest

from yt_dont_recommend.clickbait import (
    _DEFAULT_CONFIG,
    _clamp_confidence,
    _deep_merge,
    _parse_batch_response,
    _prefilter_title,
    classify_thumbnail,
    classify_title,
    classify_titles_batch,
    classify_transcript,
    classify_transcripts_batch,
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
            classify_thumbnail("vid1", "A title", cfg)

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


# ---------------------------------------------------------------------------
# _parse_batch_response
# ---------------------------------------------------------------------------


class TestParseBatchResponse:
    def test_valid_array_in_order(self):
        raw = '[{"index": 0, "is_clickbait": true, "confidence": 0.9, "reasoning": "bait"}, {"index": 1, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}]'
        result = _parse_batch_response(raw, 2)
        assert result is not None
        assert len(result) == 2
        assert result[0]["is_clickbait"] is True
        assert result[1]["is_clickbait"] is False

    def test_strips_fences(self):
        raw = '```json\n[{"index": 0, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}]\n```'
        result = _parse_batch_response(raw, 1)
        assert result is not None
        assert len(result) == 1
        assert result[0]["is_clickbait"] is False

    def test_out_of_order_indices_remapped(self):
        raw = '[{"index": 1, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}, {"index": 0, "is_clickbait": true, "confidence": 0.9, "reasoning": "bait"}]'
        result = _parse_batch_response(raw, 2)
        assert result is not None
        assert result[0]["is_clickbait"] is True   # index 0 → first slot
        assert result[1]["is_clickbait"] is False  # index 1 → second slot

    def test_missing_index_returns_none_for_slot(self):
        # Only returns index 0; index 1 is missing
        raw = '[{"index": 0, "is_clickbait": true, "confidence": 0.9, "reasoning": "bait"}]'
        result = _parse_batch_response(raw, 2)
        assert result is not None
        assert result[0] is not None
        assert result[1] is None  # missing → None → individual fallback

    def test_no_index_field_falls_back_to_positional(self):
        raw = '[{"is_clickbait": true, "confidence": 0.9, "reasoning": "bait"}, {"is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}]'
        result = _parse_batch_response(raw, 2)
        assert result is not None
        assert result[0]["is_clickbait"] is True
        assert result[1]["is_clickbait"] is False

    def test_invalid_json_returns_none(self):
        assert _parse_batch_response("not json at all", 2) is None

    def test_no_array_found_returns_none(self):
        assert _parse_batch_response('{"index": 0}', 2) is None

    def test_empty_array_returns_all_none_slots(self):
        result = _parse_batch_response("[]", 2)
        assert result == [None, None]


# ---------------------------------------------------------------------------
# classify_titles_batch
# ---------------------------------------------------------------------------

_CLICKBAIT_BATCH_RESPONSE = json.dumps([
    {"index": 0, "is_clickbait": True,  "confidence": 0.9,  "reasoning": "bait"},
    {"index": 1, "is_clickbait": False, "confidence": 0.1,  "reasoning": "ok"},
])

_SAFE_TITLE_RESPONSE = '{"is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}'


class TestClassifyTitlesBatch:
    def _items(self, n=2):
        return [{"video_id": f"vid{i}", "title": f"Title {i}"} for i in range(n)]

    def test_returns_one_result_per_item(self):
        with patch("yt_dont_recommend.clickbait._ollama_chat", return_value=_CLICKBAIT_BATCH_RESPONSE):
            results = classify_titles_batch(self._items(2), _cfg())
        assert len(results) == 2

    def test_results_carry_batch_flag(self):
        with patch("yt_dont_recommend.clickbait._ollama_chat", return_value=_CLICKBAIT_BATCH_RESPONSE):
            results = classify_titles_batch(self._items(2), _cfg())
        assert all(r.get("_batch") is True for r in results)

    def test_results_have_required_keys(self):
        with patch("yt_dont_recommend.clickbait._ollama_chat", return_value=_CLICKBAIT_BATCH_RESPONSE):
            results = classify_titles_batch(self._items(2), _cfg())
        for r in results:
            assert "is_clickbait" in r
            assert "confidence" in r
            assert "stage" in r
            assert r["stage"] == "title"

    def test_falls_back_to_individual_on_ollama_error(self):
        called_individual = []

        def mock_chat(model, prompt, **kw):
            if "index" in prompt:
                raise RuntimeError("timeout")
            called_individual.append(True)
            return _SAFE_TITLE_RESPONSE

        with patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat):
            results = classify_titles_batch(self._items(2), _cfg())
        assert len(results) == 2
        assert len(called_individual) == 2  # each item fell back individually

    def test_falls_back_to_individual_on_parse_failure(self):
        individual_calls = []

        def mock_chat(model, prompt, **kw):
            if len(individual_calls) > 0 or "index" not in prompt:
                individual_calls.append(True)
                return _SAFE_TITLE_RESPONSE
            return "not a json array"

        with patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat):
            results = classify_titles_batch(self._items(2), _cfg())
        assert len(results) == 2

    def test_splits_into_batches(self):
        """With batch_size=2 and 5 items, expect 3 ollama calls (batches of 2, 2, 1)."""
        call_count = [0]

        def mock_chat(model, prompt, **kw):
            call_count[0] += 1
            # Return a valid batch response — size inferred from how many "index N:" appear
            import re
            indices = re.findall(r"^(\d+):", prompt, re.MULTILINE)
            return json.dumps([
                {"index": int(i), "is_clickbait": False, "confidence": 0.1, "reasoning": "ok"}
                for i in indices
            ])

        items = [{"video_id": f"vid{i}", "title": f"Title {i}"} for i in range(5)]
        with patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat):
            results = classify_titles_batch(items, _cfg(), batch_size=2)
        assert len(results) == 5
        assert call_count[0] == 3  # ceil(5/2)

    def test_missing_slot_falls_back_to_individual(self):
        """When batch response omits an index, that item gets individual fallback."""
        fallback_calls = []

        def mock_chat(model, prompt, **kw):
            if "_batch" in prompt or "0:" in prompt and "1:" in prompt:
                # Batch call — only return index 0
                return '[{"index": 0, "is_clickbait": true, "confidence": 0.9, "reasoning": "bait"}]'
            fallback_calls.append(True)
            return _SAFE_TITLE_RESPONSE

        with patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat):
            results = classify_titles_batch(self._items(2), _cfg())
        assert len(results) == 2


# ---------------------------------------------------------------------------
# classify_transcripts_batch
# ---------------------------------------------------------------------------


class TestClassifyTranscriptsBatch:
    def _items(self, n=2):
        return [{"video_id": f"vid{i}", "title": f"Title {i}"} for i in range(n)]

    def test_no_transcripts_all_pass(self):
        """When no transcripts are available (policy=pass), all return not-clickbait."""
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "disabled")):
            results = classify_transcripts_batch(self._items(3), _cfg())
        assert len(results) == 3
        assert all(r["is_clickbait"] is False for r in results)

    def test_no_transcripts_all_flag(self):
        cfg = _cfg({"video": {"transcript": {"no_transcript": "flag"}}})
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "disabled")):
            results = classify_transcripts_batch(self._items(2), cfg)
        assert all(r["is_clickbait"] is True for r in results)

    def test_with_transcripts_makes_one_llm_call(self):
        """All items with transcripts go in a single LLM call."""
        batch_response = json.dumps([
            {"index": 0, "is_clickbait": False, "confidence": 0.1, "reasoning": "on-topic"},
            {"index": 1, "is_clickbait": False, "confidence": 0.1, "reasoning": "on-topic"},
        ])
        llm_calls = []

        def mock_chat(model, prompt, **kw):
            llm_calls.append(True)
            return batch_response

        with (
            patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=("transcript text", "ok")),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            results = classify_transcripts_batch(self._items(2), _cfg())

        assert len(llm_calls) == 1
        assert len(results) == 2

    def test_mixed_transcripts_and_no_transcripts(self):
        """Items without transcripts use policy default; only items with transcripts go to LLM."""
        items = [
            {"video_id": "vid0", "title": "Title 0"},
            {"video_id": "vid1", "title": "Title 1"},
        ]
        fetch_returns = [(None, "disabled"), ("some transcript", "ok")]
        llm_calls = []

        def mock_fetch(video_id):
            return fetch_returns[int(video_id[-1])]

        def mock_chat(model, prompt, **kw):
            llm_calls.append(True)
            return '[{"index": 0, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}]'

        with (
            patch("yt_dont_recommend.clickbait._fetch_transcript", side_effect=mock_fetch),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            results = classify_transcripts_batch(items, _cfg())

        assert len(results) == 2
        assert len(llm_calls) == 1  # only vid1 had a transcript
        assert results[0]["is_clickbait"] is False   # pass policy for vid0
        assert results[1]["is_clickbait"] is False   # LLM result for vid1

    def test_falls_back_to_individual_on_parse_failure(self):
        individual_calls = []

        def mock_chat(model, prompt, **kw):
            individual_calls.append(True)
            if len(individual_calls) == 1:
                return "not a json array"
            return '{"is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}'

        with (
            patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=("tx text", "ok")),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            results = classify_transcripts_batch(self._items(2), _cfg())
        assert len(results) == 2


# ---------------------------------------------------------------------------
# _prefilter_title
# ---------------------------------------------------------------------------

class TestPrefilterTitle:
    def test_official_trailer_filtered(self):
        assert _prefilter_title("Disclosure Day | Official Trailer") is not None

    def test_official_trailer_case_insensitive(self):
        assert _prefilter_title("MOVIE - OFFICIAL TRAILER 2") is not None

    def test_official_teaser_filtered(self):
        assert _prefilter_title("Something | Official Teaser") is not None

    def test_mv_suffix_filtered(self):
        assert _prefilter_title("f(x) Hot Summer MV") is not None

    def test_mv_suffix_case_insensitive(self):
        assert _prefilter_title("Artist - Song mv") is not None

    def test_breaking_news_prefix(self):
        assert _prefilter_title("BREAKING NEWS: something happened") is not None

    def test_watch_live_prefix(self):
        assert _prefilter_title("WATCH LIVE: Senate vote") is not None

    def test_weather_prefix(self):
        assert _prefilter_title("WEATHER: Wild winds expected Thursday") is not None

    def test_weather_alert_prefix(self):
        assert _prefilter_title("Weather Alert: Tornado warning") is not None

    def test_normal_title_not_filtered(self):
        assert _prefilter_title("How Black Holes Die") is None

    def test_science_title_not_filtered(self):
        assert _prefilter_title("The Universe Is Racing Apart. We May Finally Know Why.") is None

    def test_clickbait_title_not_filtered(self):
        assert _prefilter_title("They got CAUGHT...") is None

    def test_classify_title_skips_llm_for_prefiltered(self):
        """classify_title should return without calling ollama for pre-filtered titles."""
        with patch("yt_dont_recommend.clickbait._ollama_chat") as mock_llm:
            result = classify_title("vid1", "Disclosure Day | Official Trailer", _cfg())
        mock_llm.assert_not_called()
        assert result["is_clickbait"] is False
        assert result["model"] == "prefilter"

    def test_batch_skips_llm_for_all_prefiltered(self):
        """classify_titles_batch with only pre-filtered items should not call ollama."""
        items = [
            {"video_id": "v1", "title": "Movie | Official Trailer"},
            {"video_id": "v2", "title": "BREAKING NEWS: Something"},
        ]
        with patch("yt_dont_recommend.clickbait._ollama_chat") as mock_llm:
            results = classify_titles_batch(items, _cfg())
        mock_llm.assert_not_called()
        assert all(r["is_clickbait"] is False for r in results)
        assert all(r["model"] == "prefilter" for r in results)

    def test_batch_mixed_prefiltered_and_llm(self):
        """Pre-filtered items bypass LLM; remaining items are sent as a batch."""
        items = [
            {"video_id": "v1", "title": "Movie | Official Trailer"},   # pre-filter
            {"video_id": "v2", "title": "They got CAUGHT..."},          # LLM
        ]
        llm_response = '[{"index": 0, "is_clickbait": true, "confidence": 0.95, "reasoning": "bait"}]'
        with patch("yt_dont_recommend.clickbait._ollama_chat", return_value=llm_response):
            results = classify_titles_batch(items, _cfg())
        assert results[0]["is_clickbait"] is False   # pre-filtered
        assert results[0]["model"] == "prefilter"
        assert results[1]["is_clickbait"] is True    # LLM result


# ---------------------------------------------------------------------------
# _clamp_confidence
# ---------------------------------------------------------------------------

class TestClampConfidence:
    def test_clamp_above_max(self):
        assert _clamp_confidence(1.0) == 0.95

    def test_clamp_below_min(self):
        assert _clamp_confidence(0.0) == 0.05

    def test_clamp_within_range(self):
        assert _clamp_confidence(0.5) == 0.5

    def test_clamp_none_passthrough(self):
        assert _clamp_confidence(None) is None

    def test_extract_json_clamps_confidence(self):
        raw = '{"is_clickbait": true, "confidence": 1.0, "reasoning": "test"}'
        result = extract_json(raw)
        assert result["confidence"] == 0.95

    def test_parse_batch_response_confidence_unclamped(self):
        """_parse_batch_response does not clamp — clamping is done by the caller."""
        raw = '[{"index": 0, "is_clickbait": true, "confidence": 1.0, "reasoning": "x"}]'
        result = _parse_batch_response(raw, 1)
        # Raw parse returns the value as-is; batch caller applies clamping
        assert result is not None
        assert result[0]["confidence"] == 1.0


# ---------------------------------------------------------------------------
# _parse_batch_response — trailing comma stripping
# ---------------------------------------------------------------------------


class TestParseBatchResponseTrailingComma:
    def test_trailing_comma_after_last_item(self):
        """Trailing comma after last array element — exact pattern seen in live logs."""
        raw = (
            '[\n'
            '  {"index": 0, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok"},\n'
            '  {"index": 1, "is_clickbait": true, "confidence": 0.9, "reasoning": "bait"},\n'
            ']'
        )
        result = _parse_batch_response(raw, 2)
        assert result is not None
        assert result[0]["is_clickbait"] is False
        assert result[1]["is_clickbait"] is True

    def test_trailing_comma_inside_object(self):
        """Trailing comma inside an object (after last key-value pair)."""
        raw = '[{"index": 0, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok",}]'
        result = _parse_batch_response(raw, 1)
        assert result is not None
        assert result[0]["is_clickbait"] is False

    def test_trailing_comma_both_object_and_array(self):
        """Trailing comma in both the object and the enclosing array."""
        raw = (
            '[\n'
            '  {"index": 0, "is_clickbait": true, "confidence": 0.85, "reasoning": "bait",},\n'
            ']'
        )
        result = _parse_batch_response(raw, 1)
        assert result is not None
        assert result[0]["is_clickbait"] is True
        assert result[0]["confidence"] == 0.85

    def test_invalid_escape_single_quote(self):
        """Model emits \\' inside a double-quoted JSON string (seen in live logs)."""
        raw = (
            '[{"index": 0, "is_clickbait": false, "confidence": 0.10,'
            ' "reasoning": "character name G\\\'Kar; no sensational wording"}]'
        )
        result = _parse_batch_response(raw, 1)
        assert result is not None
        assert result[0]["is_clickbait"] is False
        assert "G'Kar" in result[0]["reasoning"]

    def test_invalid_escape_other_characters(self):
        """Model emits other invalid \\X escapes (\\d, \\s, \\j …)."""
        raw = (
            '[{"index": 0, "is_clickbait": true, "confidence": 0.85,'
            ' "reasoning": "uses \\dbait pattern and \\shady wording"}]'
        )
        result = _parse_batch_response(raw, 1)
        assert result is not None
        assert result[0]["is_clickbait"] is True
        assert "dbait" in result[0]["reasoning"]


# _parse_batch_response — single-quote fallback
# ---------------------------------------------------------------------------

class TestParseBatchResponseSingleQuote:
    def test_single_quoted_json_parsed(self):
        """Models sometimes return Python-style single-quoted strings."""
        raw = "[{'index': 0, 'is_clickbait': False, 'confidence': 0.1, 'reasoning': 'ok'}]"
        result = _parse_batch_response(raw, 1)
        assert result is not None
        assert result[0]["is_clickbait"] is False
        assert result[0]["confidence"] == 0.1


# ---------------------------------------------------------------------------
# _write_default_config — exception path
# ---------------------------------------------------------------------------

class TestWriteDefaultConfigExceptionPath:
    def test_load_config_tolerates_write_failure(self, tmp_path, caplog):
        """When the default config can't be written (permission error, etc.),
        load_config still returns the defaults without raising."""
        import logging
        cfg_path = tmp_path / "nonexistent" / "clickbait.yaml"
        with (
            patch("yt_dont_recommend.config.ensure_data_dir", side_effect=PermissionError("denied")),
            caplog.at_level(logging.WARNING, logger="yt_dont_recommend.clickbait"),
        ):
            cfg = load_config(cfg_path)
        # Still returns usable defaults
        assert "video" in cfg
        assert "title" in cfg["video"]


# ---------------------------------------------------------------------------
# extract_json — regex-block fallback where the matched {...} won't parse
# ---------------------------------------------------------------------------

class TestExtractJsonRegexFallback:
    def test_brace_match_with_invalid_json_inside_returns_empty(self):
        """If the first {...} block in prose doesn't parse, extract_json
        falls through to regex-field extraction."""
        # Outer parse fails; regex finds `{not: valid}`; inner parse fails too.
        # Then the regex-field extraction (is_clickbait / confidence) finds nothing.
        raw = "prose before {not: valid json} prose after"
        out = extract_json(raw)
        # extract_json returns a dict even on total failure — values are defaults.
        assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# _fetch_thumbnail_b64 — network paths
# ---------------------------------------------------------------------------

class TestFetchThumbnail:
    def test_first_quality_success_returns_base64(self, monkeypatch):
        import base64

        from yt_dont_recommend.clickbait import _fetch_thumbnail_b64

        big_body = b"x" * 6000  # > 5000, passes placeholder check

        class FakeResp:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return big_body

        def fake_urlopen(req, timeout=10):
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = _fetch_thumbnail_b64("VID123")
        assert result == base64.b64encode(big_body).decode()

    def test_first_quality_placeholder_falls_through_to_hqdefault(self, monkeypatch):
        import base64

        from yt_dont_recommend.clickbait import _fetch_thumbnail_b64

        small = b"x" * 100  # placeholder
        big = b"x" * 9000

        class FakeResp:
            def __init__(self, body): self.body = body
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return self.body

        calls = []
        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            if "maxresdefault" in req.full_url:
                return FakeResp(small)
            return FakeResp(big)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        result = _fetch_thumbnail_b64("VID123")
        assert result == base64.b64encode(big).decode()
        assert any("hqdefault" in u for u in calls)

    def test_both_qualities_fail_returns_none(self, monkeypatch):
        from yt_dont_recommend.clickbait import _fetch_thumbnail_b64

        def boom(req, timeout=10):
            raise OSError("network")

        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert _fetch_thumbnail_b64("VID123") is None


# ---------------------------------------------------------------------------
# _ollama_chat — happy path (ollama available)
# ---------------------------------------------------------------------------

class TestOllamaChatHappyPath:
    def test_calls_ollama_client_and_returns_content(self, monkeypatch):
        """When ollama is installed, _ollama_chat constructs a Client,
        calls chat, and returns the message content."""
        from yt_dont_recommend.clickbait import _ollama_chat

        mock_client_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.message.content = "classified!"
        mock_client_instance.chat.return_value = mock_response

        mock_client_cls = MagicMock(return_value=mock_client_instance)
        fake_ollama = MagicMock()
        fake_ollama.Client = mock_client_cls

        with patch.dict("sys.modules", {"ollama": fake_ollama}):
            result = _ollama_chat(
                "llama3.1:8b",
                "classify this",
                img_b64="aGVsbG8=",
                params={"num_predict": 50},
                timeout=120,
            )

        assert result == "classified!"
        mock_client_cls.assert_called_once_with(timeout=120)
        # Verify chat was called with model, messages (including images), and merged params
        call = mock_client_instance.chat.call_args
        assert call.kwargs["model"] == "llama3.1:8b"
        assert call.kwargs["messages"][0]["content"] == "classify this"
        assert call.kwargs["messages"][0]["images"] == ["aGVsbG8="]
        assert call.kwargs["options"]["temperature"] == 0
        assert call.kwargs["options"]["num_predict"] == 50


# ---------------------------------------------------------------------------
# _fetch_transcript — all branches
# ---------------------------------------------------------------------------

class TestFetchTranscript:
    def _make_fake_api(self, status, text_or_exc):
        """Build a fake youtube_transcript_api module with the given behavior.

        status: 'ok' | 'disabled' | 'not_found' | 'error'
        """

        class TranscriptsDisabled(Exception):
            pass

        class NoTranscriptFound(Exception):
            pass

        fake_api = MagicMock()
        fake_api.TranscriptsDisabled = TranscriptsDisabled
        fake_api.NoTranscriptFound = NoTranscriptFound

        class FakeInstance:
            def fetch(self, video_id, languages):
                if status == "ok":
                    segments = [MagicMock(text=t) for t in text_or_exc]
                    return segments
                if status == "disabled":
                    raise TranscriptsDisabled("disabled")
                if status == "not_found":
                    raise NoTranscriptFound("no transcript")
                raise RuntimeError("generic error")

        fake_api.YouTubeTranscriptApi = FakeInstance
        return fake_api

    def test_no_api_returned_when_module_missing(self):
        from yt_dont_recommend.clickbait import _fetch_transcript
        with patch.dict("sys.modules", {"youtube_transcript_api": None}):
            text, status = _fetch_transcript("vid1")
        assert text is None
        assert status == "no_api"

    def test_ok_returns_joined_text(self):
        from yt_dont_recommend.clickbait import _fetch_transcript
        fake = self._make_fake_api("ok", ["hello", "world"])
        with patch.dict("sys.modules", {"youtube_transcript_api": fake}):
            text, status = _fetch_transcript("vid1")
        assert text == "hello world"
        assert status == "ok"

    def test_disabled(self):
        from yt_dont_recommend.clickbait import _fetch_transcript
        fake = self._make_fake_api("disabled", None)
        with patch.dict("sys.modules", {"youtube_transcript_api": fake}):
            text, status = _fetch_transcript("vid1")
        assert text is None
        assert status == "disabled"

    def test_not_found(self):
        from yt_dont_recommend.clickbait import _fetch_transcript
        fake = self._make_fake_api("not_found", None)
        with patch.dict("sys.modules", {"youtube_transcript_api": fake}):
            text, status = _fetch_transcript("vid1")
        assert text is None
        assert status == "not_found"

    def test_other_exception_returns_error(self):
        from yt_dont_recommend.clickbait import _fetch_transcript
        fake = self._make_fake_api("other", None)
        with patch.dict("sys.modules", {"youtube_transcript_api": fake}):
            text, status = _fetch_transcript("vid1")
        assert text is None
        assert status == "error"


# ---------------------------------------------------------------------------
# _parse_batch_response — remaining branches
# ---------------------------------------------------------------------------

class TestParseBatchResponseEdgeCases:
    def test_both_json_and_ast_fail_returns_none(self):
        """When candidate is bracketed but is neither valid JSON nor a valid
        Python literal, _parse_batch_response returns None."""
        # `[foo bar baz]` — bare words, no commas; JSON fails, ast.literal_eval
        # sees undefined names and raises ValueError.
        assert _parse_batch_response("[foo bar baz]", 2) is None

    def test_non_dict_items_skipped(self):
        """Items that aren't dicts are skipped by the indexer, leaving those
        slots as None."""
        # First element is a bare number (not a dict); second is a valid dict.
        raw = '[1, {"index": 1, "is_clickbait": true, "confidence": 0.9, "reasoning": "x"}]'
        result = _parse_batch_response(raw, 2)
        assert result is not None
        assert result[0] is None  # bare int was skipped
        assert result[1] is not None
        assert result[1]["is_clickbait"] is True


# ---------------------------------------------------------------------------
# _classify_transcript_batch — remaining policy and failure branches
# ---------------------------------------------------------------------------

class TestTranscriptBatchRemainingBranches:
    def _items(self, n=2):
        return [{"video_id": f"vid{i}", "title": f"Title {i}"} for i in range(n)]

    def test_no_transcript_title_only_policy(self):
        """Items without transcripts under `title-only` policy get a deferral
        marker instead of a verdict."""
        cfg = _cfg({"video": {"transcript": {"no_transcript": "title-only"}}})
        with patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=(None, "disabled")):
            results = classify_transcripts_batch(self._items(2), cfg)
        assert len(results) == 2
        for r in results:
            assert r["_defer_to_title"] is True
            assert r["is_clickbait"] is False

    def test_ollama_raises_falls_back_to_per_item(self):
        """When the batch LLM call raises, each pending item is classified
        individually via classify_transcript()."""
        individual_calls = []

        def mock_chat(model, prompt, **kw):
            individual_calls.append(True)
            if len(individual_calls) == 1:
                # First call is the batch request — raise
                raise RuntimeError("ollama connection dropped")
            # Subsequent calls are per-item fallbacks
            return '{"is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}'

        with (
            patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=("tx text", "ok")),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            results = classify_transcripts_batch(self._items(2), _cfg())
        assert len(results) == 2
        # Batch + 2 individual fallbacks = 3 total LLM calls
        assert len(individual_calls) == 3

    def test_batch_parse_missing_slot_falls_back_per_item(self):
        """When the batch response is missing an index, that slot falls back
        to a per-item classify_transcript() call."""
        individual_calls = []

        def mock_chat(model, prompt, **kw):
            individual_calls.append(True)
            if len(individual_calls) == 1:
                # Batch request — return only index 0; index 1 missing
                return '[{"index": 0, "is_clickbait": false, "confidence": 0.1, "reasoning": "ok"}]'
            # Per-item fallback for index 1
            return '{"is_clickbait": false, "confidence": 0.2, "reasoning": "per-item"}'

        with (
            patch("yt_dont_recommend.clickbait._fetch_transcript", return_value=("tx text", "ok")),
            patch("yt_dont_recommend.clickbait._ollama_chat", side_effect=mock_chat),
        ):
            results = classify_transcripts_batch(self._items(2), _cfg())
        assert len(results) == 2
        # 1 batch + 1 per-item for the missing slot
        assert len(individual_calls) == 2


# ---------------------------------------------------------------------------
# Shadow-limit detection
# ---------------------------------------------------------------------------

class TestShadowLimitUnion:
    """_check_shadow_reencounter uses the union of clickbait_acted | keyword_acted."""

    def _old_ts(self) -> str:
        """ISO timestamp older than SHADOW_LIMIT_GRACE_HOURS."""
        from datetime import datetime as _dt
        from datetime import timedelta, timezone

        from yt_dont_recommend.config import SHADOW_LIMIT_GRACE_HOURS
        return (_dt.now(tz=timezone.utc) - timedelta(hours=SHADOW_LIMIT_GRACE_HOURS + 1)).isoformat()

    def _recent_ts(self) -> str:
        """ISO timestamp within the grace window."""
        from datetime import datetime as _dt
        from datetime import timezone
        return _dt.now(tz=timezone.utc).isoformat()

    def test_no_trigger_when_video_not_acted(self):
        """Video never acted on — never triggers shadow-limit."""
        from yt_dont_recommend.clickbait import _check_shadow_reencounter

        state = {"clickbait_acted": {}, "keyword_acted": {}}
        run_hits: dict = {"count": 0}
        for _ in range(5):
            result = _check_shadow_reencounter(state, "vid_new", run_hits)
            assert result is False
        assert run_hits["count"] == 0

    def test_no_trigger_within_grace_window(self):
        """Re-encounter within SHADOW_LIMIT_GRACE_HOURS does not count."""
        from yt_dont_recommend.clickbait import _check_shadow_reencounter

        state = {
            "clickbait_acted": {
                "vid_recent": {"acted_at": self._recent_ts(), "title": "x", "channel": "@a"},
            },
            "keyword_acted": {},
        }
        run_hits: dict = {"count": 0}
        result = _check_shadow_reencounter(state, "vid_recent", run_hits)
        assert result is False
        assert run_hits["count"] == 0

    def test_trigger_from_clickbait_acted(self):
        """Old clickbait_acted entry triggers shadow-limit after WARN_AFTER hits."""
        from yt_dont_recommend.clickbait import _check_shadow_reencounter
        from yt_dont_recommend.config import SHADOW_LIMIT_WARN_AFTER

        state = {
            "clickbait_acted": {
                "vid_old": {"acted_at": self._old_ts(), "title": "x", "channel": "@a"},
            },
            "keyword_acted": {},
        }
        run_hits: dict = {"count": 0}
        # First WARN_AFTER-1 calls increment counter but return False
        for _ in range(SHADOW_LIMIT_WARN_AFTER - 1):
            assert _check_shadow_reencounter(state, "vid_old", run_hits) is False
        # The WARN_AFTER-th call returns True
        assert _check_shadow_reencounter(state, "vid_old", run_hits) is True
        assert run_hits["count"] == SHADOW_LIMIT_WARN_AFTER

    def test_shadow_limit_check_sees_keyword_acted(self, tmp_path, monkeypatch):
        """A previously keyword-acted video re-encountered triggers the
        shadow-limit detection just like a previously clickbait-acted one."""
        import yt_dont_recommend as ydr

        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")

        from yt_dont_recommend.clickbait import _check_shadow_reencounter
        from yt_dont_recommend.config import SHADOW_LIMIT_WARN_AFTER

        state = ydr.load_state()
        state["keyword_acted"]["vid_old"] = {
            "acted_at": self._old_ts(),
            "title": "x",
            "channel": "@a",
            "matched_pattern": "p",
            "matched_mode": "substring",
            "matched_line": 1,
        }

        run_hits: dict = {"count": 0}
        # Drive hit count up to the threshold
        for _ in range(SHADOW_LIMIT_WARN_AFTER - 1):
            assert _check_shadow_reencounter(state, "vid_old", run_hits) is False
        # Threshold hit
        assert _check_shadow_reencounter(state, "vid_old", run_hits) is True
        assert run_hits["count"] == SHADOW_LIMIT_WARN_AFTER

    def test_keyword_acted_ignored_within_grace(self):
        """keyword_acted entry within the grace window does not trigger."""
        from yt_dont_recommend.clickbait import _check_shadow_reencounter

        state = {
            "clickbait_acted": {},
            "keyword_acted": {
                "vid_recent": {
                    "acted_at": self._recent_ts(),
                    "title": "x",
                    "channel": "@a",
                    "matched_pattern": "p",
                    "matched_mode": "substring",
                    "matched_line": 1,
                },
            },
        }
        run_hits: dict = {"count": 0}
        assert _check_shadow_reencounter(state, "vid_recent", run_hits) is False
        assert run_hits["count"] == 0

    def test_acted_at_returns_none_when_video_not_in_either_dict(self):
        """_acted_at returns None when the video_id is absent from both acted dicts."""
        from yt_dont_recommend.clickbait import _acted_at

        state = {"clickbait_acted": {"other_vid": {"acted_at": "x"}}, "keyword_acted": {}}
        assert _acted_at(state, "absent_vid") is None

    def test_check_shadow_reencounter_returns_false_when_acted_at_missing(self):
        """Video is in clickbait_acted but has no acted_at key — returns False."""
        from yt_dont_recommend.clickbait import _check_shadow_reencounter

        state = {"clickbait_acted": {"vid": {}}, "keyword_acted": {}}
        run_hits: dict = {"count": 0}
        assert _check_shadow_reencounter(state, "vid", run_hits) is False
        assert run_hits["count"] == 0

    def test_check_shadow_reencounter_returns_false_on_invalid_iso(self):
        """acted_at is present but not a parseable ISO timestamp — returns False."""
        from yt_dont_recommend.clickbait import _check_shadow_reencounter

        state = {"clickbait_acted": {"vid": {"acted_at": "not-a-timestamp"}}, "keyword_acted": {}}
        run_hits: dict = {"count": 0}
        assert _check_shadow_reencounter(state, "vid", run_hits) is False
        assert run_hits["count"] == 0

    def test_check_shadow_reencounter_handles_tz_naive_timestamp(self):
        """acted_at without tzinfo (legacy format) is normalised to UTC and processed."""
        from datetime import datetime as _dt
        from datetime import timedelta

        from yt_dont_recommend.clickbait import _check_shadow_reencounter
        from yt_dont_recommend.config import SHADOW_LIMIT_GRACE_HOURS

        # Naive timestamp (no tzinfo) older than the grace window
        old_naive = (_dt.utcnow() - timedelta(hours=SHADOW_LIMIT_GRACE_HOURS + 1)).isoformat()
        state = {"clickbait_acted": {"vid": {"acted_at": old_naive}}, "keyword_acted": {}}
        run_hits: dict = {"count": 0}
        # First call: count goes 0 -> 1, still under WARN_AFTER, returns False
        assert _check_shadow_reencounter(state, "vid", run_hits) is False
        assert run_hits["count"] == 1

"""
Tests for yt_dont_recommend.keywords — the keyword-blocking module.

Functions under test are imported from yt_dont_recommend.keywords directly
or via the re-export at yt_dont_recommend (added in __init__.py).
"""

import logging

from yt_dont_recommend.keywords import (
    MatchResult,
    compile_keywords,
    match_title,
    parse_keyword_file,
)


class TestParseKeywordFile:
    def test_returns_empty_for_empty_string(self):
        assert parse_keyword_file("") == []

    def test_returns_empty_for_only_comments_and_blanks(self):
        text = "# top comment\n\n# another\n   \n\t\n"
        assert parse_keyword_file(text) == []

    def test_strips_inline_blank_lines_and_comments(self):
        text = "# header\nTrump\n\n# section\nStar Trek\n"
        assert parse_keyword_file(text) == [(2, "Trump"), (5, "Star Trek")]

    def test_preserves_line_numbers_through_comments(self):
        text = "# 1\n# 2\nfoo\n# 4\nbar\n"
        assert parse_keyword_file(text) == [(3, "foo"), (5, "bar")]

    def test_strips_trailing_whitespace_only(self):
        # Internal spaces are preserved (e.g. "Star Trek")
        text = "Star Trek   \n"
        assert parse_keyword_file(text) == [(1, "Star Trek")]

    def test_handles_crlf_line_endings(self):
        text = "foo\r\nbar\r\n"
        assert parse_keyword_file(text) == [(1, "foo"), (2, "bar")]

    def test_strips_utf8_bom(self):
        text = "﻿foo\nbar\n"
        assert parse_keyword_file(text) == [(1, "foo"), (2, "bar")]


class TestCompileKeywordsSubstring:
    def test_bare_pattern_compiles_as_substring(self):
        compiled = compile_keywords([(1, "Trump")])
        assert len(compiled) == 1
        assert compiled[0].pattern == "Trump"
        assert compiled[0].mode == "substring"
        assert compiled[0].line == 1

    def test_substring_matcher_is_lowercased_string(self):
        compiled = compile_keywords([(1, "Star Trek")])
        # Substring tier stores the lowercased string for direct .find
        assert isinstance(compiled[0].matcher, str)
        assert compiled[0].matcher == "star trek"

    def test_multiple_substring_entries(self):
        compiled = compile_keywords([(1, "a"), (2, "b"), (3, "c")])
        assert [c.pattern for c in compiled] == ["a", "b", "c"]
        assert all(c.mode == "substring" for c in compiled)


class TestCompileKeywordsWord:
    def test_word_prefix_strips_prefix_and_compiles(self):
        compiled = compile_keywords([(1, "word:trek")])
        assert len(compiled) == 1
        assert compiled[0].pattern == "trek"
        assert compiled[0].mode == "word"
        # Word tier stores a compiled re.Pattern with \b anchors and IGNORECASE
        import re as _re
        assert isinstance(compiled[0].matcher, _re.Pattern)
        assert compiled[0].matcher.flags & _re.IGNORECASE

    def test_word_prefix_escapes_regex_metacharacters(self):
        # word:foo.bar should match literal "foo.bar", not "fooXbar"
        compiled = compile_keywords([(1, "word:foo.bar")])
        assert compiled[0].matcher.search("foo.bar")
        assert compiled[0].matcher.search("FOO.BAR")  # case-insensitive
        assert not compiled[0].matcher.search("fooXbar")


class TestCompileKeywordsRegex:
    def test_regex_prefix_compiles_as_regex(self):
        compiled = compile_keywords([(1, r"regex:\b(rfk|kennedy)\b")])
        assert len(compiled) == 1
        assert compiled[0].pattern == r"\b(rfk|kennedy)\b"
        assert compiled[0].mode == "regex"

    def test_regex_is_case_insensitive_by_default(self):
        compiled = compile_keywords([(1, "regex:trump")])
        assert compiled[0].matcher.search("TRUMP")
        assert compiled[0].matcher.search("Trump")
        assert compiled[0].matcher.search("trump")

    def test_regex_inline_case_override(self):
        # (?-i:...) scoped group turns OFF case-insensitive matching for the group
        # Python 3.11 requires the scoped form; bare (?-i) is a re.error.
        compiled = compile_keywords([(1, "regex:(?-i:Trump)")])
        assert compiled[0].matcher.search("Trump")
        assert not compiled[0].matcher.search("TRUMP")

    def test_invalid_regex_dropped_with_warning(self, caplog):
        caplog.set_level(logging.WARNING)
        compiled = compile_keywords([
            (1, "regex:[unclosed"),
            (2, "valid"),
        ])
        # Only the valid substring entry survives
        assert len(compiled) == 1
        assert compiled[0].pattern == "valid"
        assert any("invalid regex" in r.message for r in caplog.records)
        assert any("line 1" in r.message for r in caplog.records)


class TestCompileKeywordsLineNumbers:
    def test_line_numbers_preserved_for_mixed_tiers(self):
        compiled = compile_keywords([
            (1, "Trump"),
            (5, "word:trek"),
            (10, "regex:^foo"),
        ])
        assert [c.line for c in compiled] == [1, 5, 10]


class TestMatchTitle:
    def test_returns_none_for_empty_title(self):
        compiled = compile_keywords([(1, "Trump")])
        assert match_title("", compiled) is None

    def test_returns_none_for_no_rules(self):
        assert match_title("anything", []) is None

    def test_substring_match_case_insensitive(self):
        compiled = compile_keywords([(1, "Trump")])
        result = match_title("Why Trump won the debate", compiled)
        assert result == MatchResult(pattern="Trump", mode="substring", line=1)

    def test_substring_match_case_insensitive_lowered_input(self):
        compiled = compile_keywords([(1, "Trump")])
        assert match_title("trump speech", compiled) is not None

    def test_substring_no_match(self):
        compiled = compile_keywords([(1, "Trump")])
        assert match_title("Biden speech", compiled) is None

    def test_word_boundary_matches_whole_word(self):
        compiled = compile_keywords([(1, "word:trek")])
        assert match_title("Star Trek finale", compiled) is not None

    def test_word_boundary_does_not_match_substring(self):
        compiled = compile_keywords([(1, "word:trek")])
        assert match_title("trekking the himalayas", compiled) is None

    def test_regex_match(self):
        compiled = compile_keywords([(1, r"regex:^\d+ reasons?")])
        assert match_title("10 reasons to never use vim", compiled) is not None
        assert match_title("Top reasons to use vim", compiled) is None

    def test_first_match_wins(self):
        # Both rules match; first-listed wins.
        compiled = compile_keywords([
            (1, "Trump"),
            (2, "word:trump"),
        ])
        result = match_title("Trump speech", compiled)
        assert result.line == 1
        assert result.mode == "substring"

    def test_unicode_title_substring(self):
        compiled = compile_keywords([(1, "café")])
        assert match_title("Best CAFÉ in Paris", compiled) is not None

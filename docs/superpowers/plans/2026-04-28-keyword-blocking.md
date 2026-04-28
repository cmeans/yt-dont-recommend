# Keyword Blocking Implementation Plan (PR A — core feature)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `--keyword-block` mode that scans video titles in the YouTube home feed against a user-defined keyword list and clicks "Not interested" on matches, with three matching tiers (substring / `word:` / `regex:`).

**Architecture:** New `keywords.py` module (peer of `blocklist.py` / `clickbait.py`) provides parser, compiler, and matcher. Integrates as Phase 3 in the per-card loop in `browser.py`, between channel-level blocklist and clickbait classification. State schema bumps 4→5 with two new keys (`keyword_acted` for action history with 90-day TTL, `keyword_stats` for cumulative reporting). Three new CLI flags. PR B (scheduler integration) is deliberately out of scope.

**Tech Stack:** Python 3.11+ stdlib only (`re`, `dataclasses`, `typing`). No new third-party deps. Tests via `pytest` (existing infrastructure). No Playwright code in `keywords.py` itself; integration test surface in `tests/test_browser.py` uses the existing mocked-Playwright pattern.

**Spec:** `docs/superpowers/specs/2026-04-28-keyword-blocking-design.md`

**Branch:** Already on `feature/keyword-blocking` (created during brainstorming, holds the spec commit `c800245`).

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `src/yt_dont_recommend/config.py` | Modify | Add `DEFAULT_KEYWORD_FILE`, `DEFAULT_KEYWORD_EXCLUDE_FILE`, `KEYWORD_ACTED_PRUNE_DAYS`, bump `STATE_VERSION` 4→5 |
| `src/yt_dont_recommend/state.py` | Modify | Add `keyword_acted` / `keyword_stats` setdefaults, pruning, fresh-state defaults, `AppState` TypedDict additions, new `_acted_video_ids` helper |
| `src/yt_dont_recommend/keywords.py` | Create | New module: `CompiledKeyword`, `MatchResult`, `parse_keyword_file`, `compile_keywords`, `match_title`, `resolve_keyword_source`, `load_keyword_excludes` |
| `src/yt_dont_recommend/__init__.py` | Modify | Re-export the new keyword names |
| `src/yt_dont_recommend/cli.py` | Modify | Add three CLI flags; setup block; `--stats` integration; pass new kwargs to `process_channels` |
| `src/yt_dont_recommend/browser.py` | Modify | Add `keyword_compiled` and `keyword_excludes` kwargs to `process_channels`; Phase 3 logic in card loop |
| `src/yt_dont_recommend/clickbait.py` | Modify | Switch shadow-limit check from `state["clickbait_acted"]` to `_acted_video_ids(state)` |
| `tests/test_keywords.py` | Create | Pure-logic unit tests for the new module |
| `tests/test_state.py` | Modify | v4→v5 migration tests, pruning, `_acted_video_ids` helper |
| `tests/test_cli.py` | Modify | Flag parsing, setup block behavior, `--stats` integration |
| `tests/test_browser.py` | Modify | Phase 3 integration tests (mocked Playwright) |
| `tests/test_clickbait.py` | Modify | Shadow-limit union behavior |
| `tests/fixtures/keyword-block-fixture.txt` | Create | 3-tier example for smoke test |
| `keyword-block.example.txt` | Create | Repo-root sample file shipped to users |
| `scripts/smoke-test.sh` | Modify | Add a `--keyword-block --dry-run` invocation |
| `CHANGELOG.md` | Modify | New `[Unreleased] / Added` entry |
| `CLAUDE.md` | Modify | New `### Keyword blocking` subsection; State Schema v5 additions; CLI flags table; Modules list |
| `README.md` | Modify | New "Keyword filtering" section under Usage |

---

## Task 1: Foundation constants in `config.py` + `STATE_VERSION` bump

**Files:**
- Modify: `src/yt_dont_recommend/config.py:48-50`, `src/yt_dont_recommend/config.py:94`, `src/yt_dont_recommend/config.py:110`
- Test: `tests/test_state.py` (will be expanded in Task 2 — minimal smoke check here)

- [ ] **Step 1: Read the current state-version line and confirm baseline**

Run: `grep -nE 'STATE_VERSION|DEFAULT_BLOCKLIST_EXCLUDE_FILE|CLICKBAIT_ACTED_PRUNE_DAYS' src/yt_dont_recommend/config.py`

Expected output (key lines):
```
48:DEFAULT_BLOCKLIST_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "blocklist-exclude.txt"
49:DEFAULT_CLICKBAIT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "clickbait-exclude.txt"
94:STATE_VERSION = 4
110:CLICKBAIT_ACTED_PRUNE_DAYS = 90
```

- [ ] **Step 2: Add `DEFAULT_KEYWORD_FILE` and `DEFAULT_KEYWORD_EXCLUDE_FILE` next to the existing exclude-file constants**

Edit `src/yt_dont_recommend/config.py`. Find:
```python
DEFAULT_BLOCKLIST_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "blocklist-exclude.txt"
DEFAULT_CLICKBAIT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "clickbait-exclude.txt"
```

Replace with:
```python
DEFAULT_BLOCKLIST_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "blocklist-exclude.txt"
DEFAULT_CLICKBAIT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "clickbait-exclude.txt"
DEFAULT_KEYWORD_FILE = Path.home() / ".yt-dont-recommend" / "keyword-block.txt"
DEFAULT_KEYWORD_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "keyword-exclude.txt"
```

- [ ] **Step 3: Add `KEYWORD_ACTED_PRUNE_DAYS` next to `CLICKBAIT_ACTED_PRUNE_DAYS`**

Find:
```python
CLICKBAIT_ACTED_PRUNE_DAYS = 90
```

Replace with:
```python
CLICKBAIT_ACTED_PRUNE_DAYS = 90
KEYWORD_ACTED_PRUNE_DAYS = 90
```

- [ ] **Step 4: Bump `STATE_VERSION` 4 → 5**

Find:
```python
STATE_VERSION = 4
```

Replace with:
```python
STATE_VERSION = 5
```

- [ ] **Step 5: Run the existing test suite to confirm nothing else breaks from the version bump**

Run: `.venv/bin/python -m pytest tests/test_state.py tests/test_config.py -v`

Expected: All previously-passing tests still pass. Some `test_state.py` tests may now check `state_version == 5` (if any such hardcode exists; if any fail with `4 != 5` we'll handle in Task 2).

If any test fails specifically on `state_version` value, note it but **do not** fix it here — Task 2 handles state-schema test updates.

- [ ] **Step 6: Commit**

```bash
git add src/yt_dont_recommend/config.py
git commit -m "feat(config): add keyword-blocking constants and bump STATE_VERSION to 5

Adds DEFAULT_KEYWORD_FILE, DEFAULT_KEYWORD_EXCLUDE_FILE, and
KEYWORD_ACTED_PRUNE_DAYS in preparation for the --keyword-block feature.
STATE_VERSION goes 4 -> 5 for the keyword_acted and keyword_stats keys
landing in a follow-up commit.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 2: State schema additions, pruning, `_acted_video_ids` helper, `AppState` updates

**Files:**
- Modify: `src/yt_dont_recommend/state.py` (imports, `AppState` TypedDict, `load_state` setdefaults, fresh-state return, pruning block, new helper)
- Test: `tests/test_state.py` (new test class)

- [ ] **Step 1: Write the failing tests for the new state defaults, pruning, and helper**

Append to `tests/test_state.py`:

```python
from datetime import datetime, timedelta, timezone


class TestKeywordStateAdditions:
    """v4 -> v5 migration: keyword_acted, keyword_stats, _acted_video_ids."""

    def test_load_state_adds_keyword_acted_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["keyword_acted"] == {}

    def test_load_state_adds_keyword_stats_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["keyword_stats"] == {
            "total_matched": 0,
            "by_pattern": {},
            "by_mode": {"substring": 0, "word": 0, "regex": 0},
        }

    def test_load_state_state_version_is_5(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert state["state_version"] == 5

    def test_load_state_v4_to_v5_migration_preserves_existing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        v4 = {
            "state_version": 4,
            "blocked_by": {"@a": {"sources": ["x"], "blocked_at": "now"}},
            "stats": {"total_blocked": 1, "total_skipped": 0, "total_failed": 0},
            "clickbait_acted": {"vid1": {"acted_at": "2026-04-28T00:00:00+00:00"}},
        }
        (tmp_path / "processed.json").write_text(json.dumps(v4))
        state = ydr.load_state()
        assert state["blocked_by"] == {"@a": {"sources": ["x"], "blocked_at": "now"}}
        assert state["clickbait_acted"] == {"vid1": {"acted_at": "2026-04-28T00:00:00+00:00"}}
        assert state["keyword_acted"] == {}
        assert state["keyword_stats"]["total_matched"] == 0

    def test_keyword_acted_prunes_old_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        old = (datetime.now(tz=timezone.utc) - timedelta(days=91)).isoformat()
        recent = (datetime.now(tz=timezone.utc) - timedelta(days=1)).isoformat()
        existing = {
            "state_version": 5,
            "keyword_acted": {
                "old_vid": {"acted_at": old, "title": "x", "channel": "@a",
                            "matched_pattern": "p", "matched_mode": "substring", "matched_line": 1},
                "fresh_vid": {"acted_at": recent, "title": "y", "channel": "@b",
                              "matched_pattern": "p", "matched_mode": "substring", "matched_line": 1},
            },
        }
        (tmp_path / "processed.json").write_text(json.dumps(existing))
        state = ydr.load_state()
        assert "old_vid" not in state["keyword_acted"]
        assert "fresh_vid" in state["keyword_acted"]

    def test_keyword_stats_not_pruned(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        existing = {
            "state_version": 5,
            "keyword_stats": {
                "total_matched": 999,
                "by_pattern": {"old": 100, "newer": 50},
                "by_mode": {"substring": 100, "word": 25, "regex": 25},
            },
        }
        (tmp_path / "processed.json").write_text(json.dumps(existing))
        state = ydr.load_state()
        assert state["keyword_stats"]["total_matched"] == 999
        assert state["keyword_stats"]["by_pattern"]["old"] == 100


class TestActedVideoIdsHelper:
    """_acted_video_ids unions clickbait_acted and keyword_acted."""

    def test_returns_empty_set_for_fresh_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        state = ydr.load_state()
        assert ydr._acted_video_ids(state) == set()

    def test_returns_clickbait_acted_only(self):
        state = {
            "clickbait_acted": {"vid1": {}, "vid2": {}},
            "keyword_acted": {},
        }
        assert ydr._acted_video_ids(state) == {"vid1", "vid2"}

    def test_returns_keyword_acted_only(self):
        state = {
            "clickbait_acted": {},
            "keyword_acted": {"vid3": {}, "vid4": {}},
        }
        assert ydr._acted_video_ids(state) == {"vid3", "vid4"}

    def test_returns_union(self):
        state = {
            "clickbait_acted": {"vid1": {}, "vid2": {}},
            "keyword_acted": {"vid2": {}, "vid3": {}},  # vid2 in both
        }
        assert ydr._acted_video_ids(state) == {"vid1", "vid2", "vid3"}

    def test_handles_missing_keys(self):
        state = {}
        assert ydr._acted_video_ids(state) == set()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_state.py::TestKeywordStateAdditions tests/test_state.py::TestActedVideoIdsHelper -v`

Expected: All tests FAIL — `keyword_acted` and `keyword_stats` keys not yet added; `_acted_video_ids` not defined.

- [ ] **Step 3: Update imports in `state.py` to include `KEYWORD_ACTED_PRUNE_DAYS`**

Find the existing import block in `state.py` that imports from `.config`:

```python
from .config import (
    ...
    CLICKBAIT_ACTED_PRUNE_DAYS,
    ...
)
```

Add `KEYWORD_ACTED_PRUNE_DAYS` next to `CLICKBAIT_ACTED_PRUNE_DAYS`:

```python
from .config import (
    ...
    CLICKBAIT_ACTED_PRUNE_DAYS,
    KEYWORD_ACTED_PRUNE_DAYS,
    ...
)
```

- [ ] **Step 4: Add the new fields to the `AppState` TypedDict**

Find the `AppState` class in `state.py`. It already lists pre-existing keys. After the line for `pending_upgrade`, add:

```python
    keyword_acted: dict[str, dict]
    keyword_stats: dict[str, int | dict[str, int]]
```

The full insertion looks like:

```python
class AppState(TypedDict, total=False):
    ...
    pending_upgrade: dict | None
    keyword_acted: dict[str, dict]
    keyword_stats: dict[str, int | dict[str, int]]
    state_version: int
```

(Place above `state_version` to keep that as the schema-control field at the bottom.)

- [ ] **Step 5: Add `setdefault` calls for the new keys in `load_state`**

Find the existing `setdefault` block in `load_state` (around line 100–128, just after the `pending_upgrade` setdefault). Add:

```python
        s.setdefault("keyword_acted", {})
        keyword_stats = s.setdefault("keyword_stats", {})
        keyword_stats.setdefault("total_matched", 0)
        keyword_stats.setdefault("by_pattern", {})
        by_mode = keyword_stats.setdefault("by_mode", {})
        by_mode.setdefault("substring", 0)
        by_mode.setdefault("word", 0)
        by_mode.setdefault("regex", 0)
```

- [ ] **Step 6: Add pruning for `keyword_acted` next to the existing `clickbait_acted` prune block**

Find the existing pruning block (look for `CLICKBAIT_ACTED_PRUNE_DAYS` usage near line 129). Immediately after that block, add:

```python
        # Prune keyword_acted entries older than KEYWORD_ACTED_PRUNE_DAYS
        _kw_cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=KEYWORD_ACTED_PRUNE_DAYS)).isoformat()
        s["keyword_acted"] = {
            vid: entry for vid, entry in s["keyword_acted"].items()
            if entry.get("acted_at", "") >= _kw_cutoff
        }
```

- [ ] **Step 7: Add the new keys to the fresh-state `return` dict at the bottom of `load_state`**

Find the literal `return {...}` at the bottom of `load_state` and add the two new keys before `state_version`:

```python
        "pending_upgrade": None,
        "keyword_acted": {},
        "keyword_stats": {
            "total_matched": 0,
            "by_pattern": {},
            "by_mode": {"substring": 0, "word": 0, "regex": 0},
        },
        "state_version": STATE_VERSION,
    }
```

- [ ] **Step 8: Add the `_acted_video_ids` helper at module level (below `save_state`)**

Append this near the bottom of `state.py` (after `save_state`, before any `__all__` or trailing constants):

```python
def _acted_video_ids(state: dict) -> set[str]:
    """Return the union of video_ids that have been acted on by either
    the clickbait or keyword pipelines.

    Used by the shadow-limit detection in clickbait.py and by --stats so
    a video acted on by either pipeline counts as "we've seen this".
    """
    clickbait = state.get("clickbait_acted", {}) or {}
    keyword = state.get("keyword_acted", {}) or {}
    return set(clickbait.keys()) | set(keyword.keys())
```

- [ ] **Step 9: Run the new tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_state.py::TestKeywordStateAdditions tests/test_state.py::TestActedVideoIdsHelper -v`

Expected: All tests PASS.

- [ ] **Step 10: Run the full state test file to make sure nothing else regressed**

Run: `.venv/bin/python -m pytest tests/test_state.py -v`

Expected: All tests PASS. If a pre-existing test asserted `state_version == 4`, update it to `5`.

- [ ] **Step 11: Commit**

```bash
git add src/yt_dont_recommend/state.py tests/test_state.py
git commit -m "feat(state): add keyword_acted and keyword_stats keys (v5 migration)

- New keys: keyword_acted (90-day TTL, mirrors clickbait_acted) and
  keyword_stats (cumulative counts).
- AppState TypedDict gains both keys per State Schema Policy step 7.
- Pruning at load mirrors CLICKBAIT_ACTED_PRUNE_DAYS pattern.
- New _acted_video_ids helper returns the union of both acted sets,
  used by shadow-limit detection in clickbait.py and --stats reporting.

Backward-compatible: v4 state files load with the new keys defaulted.
Forward-compatible: v4 binaries reading a v5 file emit the existing
'state_version > STATE_VERSION' warning and ignore unknown keys.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 3: `keywords.py` module — types, parser, compiler, matcher

**Files:**
- Create: `src/yt_dont_recommend/keywords.py`
- Test: `tests/test_keywords.py` (new file)

- [ ] **Step 1: Create the failing test file with parser, compiler, and matcher tests**

Create `tests/test_keywords.py`:

```python
"""
Tests for yt_dont_recommend.keywords — the keyword-blocking module.

Functions under test are imported from yt_dont_recommend.keywords directly
or via the re-export at yt_dont_recommend (added in __init__.py).
"""

import logging

import pytest

from yt_dont_recommend.keywords import (
    CompiledKeyword,
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
        # (?-i) inside the pattern turns OFF case-insensitive matching
        compiled = compile_keywords([(1, "regex:(?-i)Trump")])
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
```

- [ ] **Step 2: Run the tests to verify they fail with import errors**

Run: `.venv/bin/python -m pytest tests/test_keywords.py -v`

Expected: All tests FAIL with `ModuleNotFoundError: No module named 'yt_dont_recommend.keywords'`.

- [ ] **Step 3: Create the `keywords.py` module with types, parser, compiler, and matcher**

Create `src/yt_dont_recommend/keywords.py`:

```python
"""
Keyword blocking — video-level title matching for the home feed.

Pure-logic module. No Playwright. No LLM. No network at import time.
Mirrors the architectural pattern of clickbait.py (opt-in classifier
consumed by the card loop in browser.py).

Public API (all re-exported from yt_dont_recommend.__init__):
    CompiledKeyword         dataclass — one per surviving rule
    MatchResult             namedtuple — first-match-wins result
    parse_keyword_file      raw text -> [(line, pattern), ...]
    compile_keywords        [(line, pattern), ...] -> [CompiledKeyword, ...]
    match_title             (title, [CompiledKeyword]) -> MatchResult | None
    resolve_keyword_source  see Task 4 (this module)
    load_keyword_excludes   see Task 4 (this module)
"""

import logging
import re
from dataclasses import dataclass
from typing import NamedTuple

log = logging.getLogger(__name__)

_WORD_PREFIX = "word:"
_REGEX_PREFIX = "regex:"


@dataclass(frozen=True)
class CompiledKeyword:
    """A user keyword rule compiled into runtime form."""
    pattern: str          # original text after prefix-strip (for stats reporting)
    mode: str             # "substring" | "word" | "regex"
    line: int             # 1-indexed line in the source file
    matcher: object       # tier-specific runtime form (str for substring, re.Pattern otherwise)


class MatchResult(NamedTuple):
    """First-match-wins result returned from match_title."""
    pattern: str
    mode: str
    line: int


def parse_keyword_file(text: str) -> list[tuple[int, str]]:
    """Parse a keyword-block.txt body into (line_number, pattern) pairs.

    Strips '#' line comments, blank lines, and surrounding whitespace.
    Preserves 1-indexed line numbers from the original file. Internal
    whitespace inside a rule is preserved (e.g., "Star Trek").
    """
    # Strip a UTF-8 BOM if present at the very start of the file.
    if text.startswith("﻿"):
        text = text[1:]

    out: list[tuple[int, str]] = []
    for idx, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((idx, stripped))
    return out


def compile_keywords(raw: list[tuple[int, str]]) -> list[CompiledKeyword]:
    """Compile parsed entries into tier-specific runtime forms.

    Substring (default): stores the lowercased pattern as a str matcher.
    word:<phrase>:        re.compile(rf"\b{re.escape(phrase)}\b", IGNORECASE).
    regex:<pattern>:      re.compile(pattern, IGNORECASE).

    Bad regex entries log a single WARNING and are dropped from the
    returned list. The run continues with valid rules.
    """
    out: list[CompiledKeyword] = []
    for line, pattern in raw:
        if pattern.startswith(_REGEX_PREFIX):
            body = pattern[len(_REGEX_PREFIX):]
            try:
                matcher = re.compile(body, re.IGNORECASE)
            except re.error as exc:
                log.warning("keyword line %d: invalid regex %r: %s", line, body, exc)
                continue
            out.append(CompiledKeyword(pattern=body, mode="regex", line=line, matcher=matcher))
        elif pattern.startswith(_WORD_PREFIX):
            body = pattern[len(_WORD_PREFIX):]
            matcher = re.compile(rf"\b{re.escape(body)}\b", re.IGNORECASE)
            out.append(CompiledKeyword(pattern=body, mode="word", line=line, matcher=matcher))
        else:
            out.append(CompiledKeyword(
                pattern=pattern,
                mode="substring",
                line=line,
                matcher=pattern.lower(),
            ))
    return out


def match_title(title: str, compiled: list[CompiledKeyword]) -> MatchResult | None:
    """Return the first matching rule for `title`, or None if no rule hits.

    Iterates `compiled` in order, returns on the first hit. Substring
    matching is case-insensitive (both sides lowercased). Word and regex
    tiers carry IGNORECASE in their compiled re.Pattern.
    """
    if not title or not compiled:
        return None
    title_lower = title.lower()
    for kw in compiled:
        if kw.mode == "substring":
            if kw.matcher in title_lower:
                return MatchResult(pattern=kw.pattern, mode=kw.mode, line=kw.line)
        else:
            # word and regex tiers both use re.Pattern.search
            if kw.matcher.search(title):
                return MatchResult(pattern=kw.pattern, mode=kw.mode, line=kw.line)
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_keywords.py -v`

Expected: All tests PASS.

- [ ] **Step 5: Run ruff to confirm style**

Run: `.venv/bin/python -m ruff check src/yt_dont_recommend/keywords.py tests/test_keywords.py`

Expected: No findings.

- [ ] **Step 6: Commit**

```bash
git add src/yt_dont_recommend/keywords.py tests/test_keywords.py
git commit -m "feat(keywords): new keywords.py module — parser, compiler, matcher

Pure-logic module mirroring clickbait.py's architectural tier (opt-in,
video-level classifier consumed by browser.py's card loop). Stdlib only.

Three matching tiers selected by line prefix:
- bare substring (case-insensitive, lowercased compare)
- word:<phrase> (re.escape + \\b anchors + IGNORECASE)
- regex:<pattern> (re.compile with IGNORECASE; (?-i) overrides)

First-match-wins ordering preserves source-file line semantics so users
control rule precedence by ordering. Invalid regex entries log a single
WARN and are dropped without aborting the run.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 4: `keywords.py` — `resolve_keyword_source` and `load_keyword_excludes`

**Files:**
- Modify: `src/yt_dont_recommend/keywords.py` (append two functions)
- Test: `tests/test_keywords.py` (append test classes)

- [ ] **Step 1: Append failing tests for the two new functions**

Append to `tests/test_keywords.py`:

```python
from pathlib import Path
from unittest.mock import patch

from yt_dont_recommend.keywords import (
    load_keyword_excludes,
    resolve_keyword_source,
)


class TestResolveKeywordSource:
    def test_local_path_returns_text(self, tmp_path):
        f = tmp_path / "kw.txt"
        f.write_text("Trump\nword:trek\n")
        assert resolve_keyword_source(str(f)) == "Trump\nword:trek\n"

    def test_missing_local_path_raises(self, tmp_path):
        missing = tmp_path / "nope.txt"
        with pytest.raises(SystemExit):
            resolve_keyword_source(str(missing))

    def test_http_url_rejected(self):
        with pytest.raises(SystemExit):
            resolve_keyword_source("http://example.com/list.txt")

    def test_https_url_returns_body(self):
        # Fake the urlopen call. Mirrors blocklist.resolve_source's network shape.
        body = b"foo\nword:bar\n"
        from io import BytesIO
        fake_resp = BytesIO(body)
        with patch("yt_dont_recommend.keywords.urlopen", return_value=fake_resp):
            assert resolve_keyword_source("https://example.com/list.txt") == "foo\nword:bar\n"


class TestLoadKeywordExcludes:
    def test_missing_file_returns_empty_set(self, tmp_path):
        missing = tmp_path / "nope.txt"
        assert load_keyword_excludes(missing) == set()

    def test_loads_handles_lowercased(self, tmp_path):
        f = tmp_path / "ex.txt"
        f.write_text("# header\n@FooBar\n@bazQUX\n")
        assert load_keyword_excludes(f) == {"@foobar", "@bazqux"}

    def test_invalid_entries_dropped(self, tmp_path):
        f = tmp_path / "ex.txt"
        # @bad spaces is invalid per blocklist._canonicalize_channel
        f.write_text("@valid\nbad spaces\n@another\n")
        excludes = load_keyword_excludes(f)
        assert "@valid" in excludes
        assert "@another" in excludes
        assert "bad spaces" not in excludes

    def test_uc_channel_id_canonicalized(self, tmp_path):
        f = tmp_path / "ex.txt"
        # Real-shape UCxxx... ID (24 chars total: UC + 22 base64url-ish)
        valid_uc = "UC" + "a" * 22
        f.write_text(f"{valid_uc}\n")
        assert load_keyword_excludes(f) == {valid_uc.lower()}

    def test_blank_and_comment_lines_ignored(self, tmp_path):
        f = tmp_path / "ex.txt"
        f.write_text("\n# c\n\n@a\n   \n@b\n")
        assert load_keyword_excludes(f) == {"@a", "@b"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_keywords.py::TestResolveKeywordSource tests/test_keywords.py::TestLoadKeywordExcludes -v`

Expected: All tests FAIL — `resolve_keyword_source` and `load_keyword_excludes` not yet defined.

- [ ] **Step 3: Append the two functions to `keywords.py`**

Add to the top of `src/yt_dont_recommend/keywords.py` (next to existing imports):

```python
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
```

Then append at the bottom of `src/yt_dont_recommend/keywords.py`:

```python
def resolve_keyword_source(source: str) -> str:
    """Resolve a keyword source spec into raw text content.

    Accepted forms:
        /local/path/keyword.txt    — read from disk
        https://...                — HTTPS fetch
        http://...                 — REJECTED (insecure; mirrors blocklist hardening)

    Exits with code 1 on missing file or fetch failure.
    """
    if source.lower().startswith("http://"):
        log.error(
            "Refusing insecure http:// keyword source; use https:// instead. "
            "If you need a local override, serve the file locally and use a file path."
        )
        raise SystemExit(1)

    if source.lower().startswith("https://"):
        try:
            req = Request(source, headers={"User-Agent": "yt-dont-recommend/keywords"})
            with urlopen(req, timeout=15) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (URLError, OSError, TimeoutError) as exc:
            log.error("Failed to fetch keyword source %s: %s", source, exc)
            raise SystemExit(1) from exc

    # Local path
    p = Path(source)
    if not p.exists():
        log.error("Keyword source not found: %s", source)
        raise SystemExit(1)
    return p.read_text(encoding="utf-8", errors="replace")


def load_keyword_excludes(path: Path) -> set[str]:
    """Load a keyword-exclude file into a set of canonicalized handles.

    Mirrors how cli.py loads the blocklist exclude file: reads one entry
    per non-comment, non-blank line, validates each via the existing
    blocklist._canonicalize_channel routine, lowercases for matching.
    Returns empty set if the file does not exist (auto-load semantics).
    """
    if not path.exists():
        return set()

    # Late import to avoid circular dep with blocklist.
    from .blocklist import _canonicalize_channel

    out: set[str] = set()
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        canonical = _canonicalize_channel(s)
        if canonical:
            out.add(canonical.lower())
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_keywords.py -v`

Expected: All tests in the file PASS (originals from Task 3 plus the new ones).

- [ ] **Step 5: Run ruff**

Run: `.venv/bin/python -m ruff check src/yt_dont_recommend/keywords.py tests/test_keywords.py`

Expected: No findings.

- [ ] **Step 6: Commit**

```bash
git add src/yt_dont_recommend/keywords.py tests/test_keywords.py
git commit -m "feat(keywords): add resolve_keyword_source and load_keyword_excludes

resolve_keyword_source mirrors blocklist.resolve_source: local paths,
https URLs, http rejected. SystemExit(1) on missing file or fetch
failure (matches CLI exit-code conventions in this repo).

load_keyword_excludes reuses blocklist._canonicalize_channel for
structural validation, returns lowercased canonical handles. Missing
file returns empty set silently (auto-load contract).

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 5: Re-export keyword API from `__init__.py`

**Files:**
- Modify: `src/yt_dont_recommend/__init__.py`
- Test: `tests/test_keywords.py` (add import-via-package smoke test)

- [ ] **Step 1: Append a smoke test that imports via the package root**

Append to `tests/test_keywords.py`:

```python
class TestPackageReExports:
    """Names from keywords.py are re-exported at yt_dont_recommend root."""

    def test_compile_keywords_importable_from_root(self):
        import yt_dont_recommend
        assert hasattr(yt_dont_recommend, "compile_keywords")

    def test_match_title_importable_from_root(self):
        import yt_dont_recommend
        assert hasattr(yt_dont_recommend, "match_title")

    def test_parse_keyword_file_importable_from_root(self):
        import yt_dont_recommend
        assert hasattr(yt_dont_recommend, "parse_keyword_file")

    def test_resolve_keyword_source_importable_from_root(self):
        import yt_dont_recommend
        assert hasattr(yt_dont_recommend, "resolve_keyword_source")

    def test_load_keyword_excludes_importable_from_root(self):
        import yt_dont_recommend
        assert hasattr(yt_dont_recommend, "load_keyword_excludes")
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_keywords.py::TestPackageReExports -v`

Expected: All tests FAIL — names not re-exported yet.

- [ ] **Step 3: Add re-exports to `__init__.py`**

Open `src/yt_dont_recommend/__init__.py`. Find the existing block of `from .clickbait import ...` re-exports (or the equivalent). Add a new import block:

```python
from .keywords import (
    CompiledKeyword,
    MatchResult,
    compile_keywords,
    load_keyword_excludes,
    match_title,
    parse_keyword_file,
    resolve_keyword_source,
)
```

If `__init__.py` declares an `__all__` list, append the same names there:

```python
__all__ = [
    ...
    "CompiledKeyword",
    "MatchResult",
    "compile_keywords",
    "load_keyword_excludes",
    "match_title",
    "parse_keyword_file",
    "resolve_keyword_source",
    ...
]
```

(Alphabetical insertion is fine; mirror the existing style.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_keywords.py -v`

Expected: All keywords tests PASS, including the new package-reexport class.

- [ ] **Step 5: Run the full test suite to make sure no other tests broke**

Run: `.venv/bin/python -m pytest -x --tb=short`

Expected: Full suite (now 533+ tests + ~40 new from Tasks 2-5) passes.

- [ ] **Step 6: Commit**

```bash
git add src/yt_dont_recommend/__init__.py tests/test_keywords.py
git commit -m "feat(__init__): re-export keywords API at package root

Mirrors the clickbait.py re-export pattern so tests can patch via
yt_dont_recommend.X and so external imports use the canonical
top-level path.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 6: CLI flag definitions

**Files:**
- Modify: `src/yt_dont_recommend/cli.py` (imports + argparse `add_argument` calls + help message)
- Test: `tests/test_cli.py` (new test class for flag parsing)

- [ ] **Step 1: Append failing tests for flag presence and basic parsing**

Append to `tests/test_cli.py`:

```python
class TestKeywordCliFlags:
    """--keyword-block, --keyword-source, --keyword-exclude argument parsing."""

    def test_keyword_block_flag_parses(self, monkeypatch):
        import yt_dont_recommend.cli as cli
        parser = cli._build_parser() if hasattr(cli, "_build_parser") else None
        # If _build_parser doesn't exist, exercise via argparse on main() instead.
        # Fall back: invoke main with sys.argv and assert it doesn't argparse-fail.
        if parser is None:
            with patch.object(cli.sys, "argv", ["ydr", "--keyword-block", "--dry-run"]):
                # The setup will eventually call write_attention or open_browser.
                # We only care that argparse doesn't reject the flag.
                # Use a stub for open_browser so the test exits early.
                with patch("yt_dont_recommend.open_browser", return_value=None):
                    cli.main()
            return
        args = parser.parse_args(["--keyword-block"])
        assert args.keyword_block is True

    def test_keyword_source_flag_parses(self, monkeypatch):
        import yt_dont_recommend.cli as cli
        if not hasattr(cli, "_build_parser"):
            return  # exercised in integration tests in Task 7
        parser = cli._build_parser()
        args = parser.parse_args(["--keyword-block", "--keyword-source", "/tmp/kw.txt"])
        assert args.keyword_source == "/tmp/kw.txt"

    def test_keyword_exclude_flag_parses(self, monkeypatch):
        import yt_dont_recommend.cli as cli
        if not hasattr(cli, "_build_parser"):
            return
        parser = cli._build_parser()
        args = parser.parse_args(["--keyword-block", "--keyword-exclude", "/tmp/ex.txt"])
        assert args.keyword_exclude == "/tmp/ex.txt"
```

(If `cli.py` does not factor argparse into a `_build_parser` helper, these tests degrade gracefully and the full-flow assertions in Task 7 cover the same surface. Read the file before writing the test to choose the right pattern.)

- [ ] **Step 2: Inspect existing argparse setup in `cli.py`**

Run: `grep -nB1 -A4 'add_argument.*--clickbait\|add_argument.*--clickbait-exclude' src/yt_dont_recommend/cli.py | head -40`

Note the existing flag style and the imports near the top of `cli.py` (`DEFAULT_CLICKBAIT_EXCLUDE_FILE`, etc.).

- [ ] **Step 3: Update imports at the top of `cli.py` to include the new constants**

Find the existing import block from `.config`:

```python
from .config import (
    ...
    DEFAULT_BLOCKLIST_EXCLUDE_FILE,
    DEFAULT_CLICKBAIT_EXCLUDE_FILE,
    ...
)
```

Add `DEFAULT_KEYWORD_FILE` and `DEFAULT_KEYWORD_EXCLUDE_FILE`:

```python
from .config import (
    ...
    DEFAULT_BLOCKLIST_EXCLUDE_FILE,
    DEFAULT_CLICKBAIT_EXCLUDE_FILE,
    DEFAULT_KEYWORD_EXCLUDE_FILE,
    DEFAULT_KEYWORD_FILE,
    ...
)
```

- [ ] **Step 4: Add the three new `add_argument` calls next to the existing `--clickbait` block**

Find the existing `--clickbait` and `--clickbait-exclude` `add_argument` calls (around line 514–505 region). Immediately after `--clickbait-exclude`, add:

```python
    parser.add_argument(
        "--keyword-block",
        action="store_true",
        default=False,
        help=(
            "Enable keyword-blocking mode: scan video titles in the home feed "
            "against the keyword list and click 'Not interested' on matches. "
            "Independent of --clickbait (no LLM dependency)."
        ),
    )
    parser.add_argument(
        "--keyword-source",
        default=None,
        metavar="PATH-OR-URL",
        help=(
            f"Keyword list source. Local file path or https:// URL. "
            f"Defaults to {DEFAULT_KEYWORD_FILE} if it exists. "
            f"http:// is rejected (insecure)."
        ),
    )
    parser.add_argument(
        "--keyword-exclude",
        default=None,
        metavar="PATH-OR-URL",
        help=(
            f"Channels to never evaluate for keyword matches. Local file path or "
            f"https:// URL. Defaults to {DEFAULT_KEYWORD_EXCLUDE_FILE} (auto-loaded "
            f"if present, silent if absent)."
        ),
    )
```

- [ ] **Step 5: Run the flag-parse tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cli.py::TestKeywordCliFlags -v`

Expected: All tests PASS (or degrade gracefully per the inline note in Step 1).

- [ ] **Step 6: Commit**

```bash
git add src/yt_dont_recommend/cli.py tests/test_cli.py
git commit -m "feat(cli): add --keyword-block, --keyword-source, --keyword-exclude flags

Argparse-only addition; no behavior wired yet. Flag definitions
mirror the existing --clickbait / --clickbait-exclude shape.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 7: CLI setup block — resolve, compile, load excludes, mode-gate help

**Files:**
- Modify: `src/yt_dont_recommend/cli.py` (mode-gate logic; setup block; pass kwargs to `process_channels`)
- Test: `tests/test_cli.py` (mode-gate help, default-path auto-load, http rejection)

- [ ] **Step 1: Append failing integration-style tests for the setup block**

Append to `tests/test_cli.py`:

```python
class TestKeywordSetupBlock:
    """End-to-end CLI behavior for keyword setup: file resolution, compile,
    excludes, and mode-gate."""

    def test_keyword_block_alone_with_no_other_mode_shows_help(
        self, capsys, monkeypatch
    ):
        # Without --blocklist or --clickbait, --keyword-block alone is
        # a usable mode. But running with NO modes at all should show help.
        monkeypatch.setattr("sys.argv", ["ydr"])
        with pytest.raises(SystemExit) as excinfo:
            from yt_dont_recommend.cli import main
            main()
        # Either argparse exits 0 with help, or main exits 2.
        # The existing pattern is: print help, sys.exit(0).
        # Adjust assertion based on existing behavior in your codebase.
        captured = capsys.readouterr()
        assert "usage" in captured.out.lower() or "usage" in captured.err.lower()

    def test_keyword_source_missing_path_errors(self, tmp_path, monkeypatch, caplog):
        from yt_dont_recommend import cli
        monkeypatch.setattr(
            "sys.argv",
            ["ydr", "--keyword-block", "--keyword-source", str(tmp_path / "missing.txt")],
        )
        caplog.set_level("ERROR")
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 1
        assert any("not found" in r.message.lower() for r in caplog.records)

    def test_keyword_source_http_rejected(self, tmp_path, monkeypatch, caplog):
        from yt_dont_recommend import cli
        monkeypatch.setattr(
            "sys.argv",
            ["ydr", "--keyword-block", "--keyword-source", "http://example.com/k.txt"],
        )
        caplog.set_level("ERROR")
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 1
        assert any("http://" in r.message for r in caplog.records)

    def test_keyword_default_file_auto_loaded(self, tmp_path, monkeypatch):
        from yt_dont_recommend import cli, config
        monkeypatch.setattr(config, "DEFAULT_KEYWORD_FILE", tmp_path / "keyword-block.txt")
        # Reload constant in cli (it imported by name from config).
        monkeypatch.setattr(cli, "DEFAULT_KEYWORD_FILE", tmp_path / "keyword-block.txt")
        (tmp_path / "keyword-block.txt").write_text("Trump\n")
        # Run --keyword-block --dry-run; stub open_browser to avoid Playwright.
        monkeypatch.setattr("sys.argv", ["ydr", "--keyword-block", "--dry-run"])
        with patch("yt_dont_recommend.open_browser", return_value=None):
            cli.main()
        # If we got here, the auto-load of the default file did not error.

    def test_keyword_exclude_explicit_missing_path_errors(
        self, tmp_path, monkeypatch, caplog
    ):
        from yt_dont_recommend import cli
        kw = tmp_path / "kw.txt"
        kw.write_text("foo\n")
        ex_missing = tmp_path / "no-exclude.txt"
        monkeypatch.setattr(
            "sys.argv",
            [
                "ydr", "--keyword-block",
                "--keyword-source", str(kw),
                "--keyword-exclude", str(ex_missing),
            ],
        )
        caplog.set_level("ERROR")
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
        assert excinfo.value.code == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py::TestKeywordSetupBlock -v`

Expected: Tests FAIL — setup block not yet wired.

- [ ] **Step 3: Add the keyword setup block in `cli.main()` after the existing clickbait_exclude_set block**

Find the end of the clickbait_exclude_set block in `cli.py` (around line 984):

```python
    if clickbait_exclude_source:
        clickbait_exclude_set = {c.lower() for c in resolve_source(clickbait_exclude_source, quiet=True)}
        log.info(f"Loaded {_n(len(clickbait_exclude_set), 'clickbait exclusion')} via {clickbait_exclude_label}")
```

Immediately after that block, add the keyword setup:

```python
    # ---- Keyword blocking setup ----
    keyword_compiled = None
    keyword_excludes_set: set[str] = set()
    if args.keyword_block:
        # Resolve source (explicit --keyword-source or default file if present)
        if args.keyword_source:
            keyword_source = args.keyword_source
            keyword_label = "--keyword-source"
        elif DEFAULT_KEYWORD_FILE.exists():
            keyword_source = str(DEFAULT_KEYWORD_FILE)
            keyword_label = f"default keyword file ({DEFAULT_KEYWORD_FILE})"
        else:
            log.error(
                "--keyword-block was specified but no keyword source was provided "
                "and the default file %s does not exist.",
                DEFAULT_KEYWORD_FILE,
            )
            sys.exit(1)

        text = resolve_keyword_source(keyword_source)
        raw = parse_keyword_file(text)
        keyword_compiled = compile_keywords(raw)
        if not keyword_compiled:
            log.info("keyword-block file is empty, no keyword matching active")
        else:
            log.info(
                "Loaded %s via %s",
                _n(len(keyword_compiled), "keyword rule"),
                keyword_label,
            )

        # Resolve excludes (explicit path required; default auto-loaded if present)
        if args.keyword_exclude:
            kw_ex_path = Path(args.keyword_exclude)
            if not kw_ex_path.exists():
                log.error("Keyword exclude path not found: %s", args.keyword_exclude)
                sys.exit(1)
            keyword_excludes_set = load_keyword_excludes(kw_ex_path)
            log.info(
                "Loaded %s via --keyword-exclude",
                _n(len(keyword_excludes_set), "keyword exclusion"),
            )
        elif DEFAULT_KEYWORD_EXCLUDE_FILE.exists():
            keyword_excludes_set = load_keyword_excludes(DEFAULT_KEYWORD_EXCLUDE_FILE)
            if keyword_excludes_set:
                log.info(
                    "Loaded %s via default keyword exclude file (%s)",
                    _n(len(keyword_excludes_set), "keyword exclusion"),
                    DEFAULT_KEYWORD_EXCLUDE_FILE,
                )
```

- [ ] **Step 4: Add `Path` and the new keyword imports to the top of `cli.py`**

Confirm the top of `cli.py` imports `from pathlib import Path` (likely already present). Also add the keyword API imports:

```python
from .keywords import (
    compile_keywords,
    load_keyword_excludes,
    parse_keyword_file,
    resolve_keyword_source,
)
```

- [ ] **Step 5: Pass new kwargs to `process_channels` at the call site**

Find the existing `process_channels(...)` call at line 994. Update to add the two new kwargs:

```python
            process_channels(
                channel_sources,
                to_unblock=all_unblocks,
                state=state,
                dry_run=args.dry_run,
                limit=sys.maxsize if args.no_limit else args.limit,
                headless=args.headless,
                clickbait_cfg=clickbait_cfg,
                exclude_set=clickbait_exclude_set or None,
                keyword_compiled=keyword_compiled,
                keyword_excludes=keyword_excludes_set or None,
                _browser=browser_handle,
            )
```

(Note: `process_channels` does not yet accept these kwargs; the call site will be a runtime error until Task 9 lands. Run only the unit tests, not integration tests, between this commit and Task 9. Tests in Step 6 are constructed to exercise only the setup block and exit before reaching `process_channels`.)

- [ ] **Step 6: Update the mode-gate (the "no mode" early-help) to NOT include `--keyword-block` as a no-mode case**

Find the existing check that exits with help when neither `--blocklist` nor `--clickbait` is set. It looks roughly like:

```python
    if not args.blocklist and not args.clickbait and not <other mode flags>:
        parser.print_help()
        sys.exit(0)
```

Add `args.keyword_block` to the list of valid modes so `--keyword-block` alone proceeds:

```python
    if not args.blocklist and not args.clickbait and not args.keyword_block and ...:
        parser.print_help()
        sys.exit(0)
```

(Adjust based on the actual condition; preserve all other mode flags.)

- [ ] **Step 7: Run the new setup-block tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py::TestKeywordSetupBlock -v`

Expected: All PASS.

- [ ] **Step 8: Run ruff**

Run: `.venv/bin/python -m ruff check src/yt_dont_recommend/cli.py tests/test_cli.py`

Expected: No findings.

- [ ] **Step 9: Commit**

```bash
git add src/yt_dont_recommend/cli.py tests/test_cli.py
git commit -m "feat(cli): wire --keyword-block setup block (resolve, compile, excludes)

Adds the runtime setup that converts CLI flags into compiled keyword
rules and an exclude set, ready to pass to process_channels (wiring
in Task 9). Mode-gate accepts --keyword-block alone.

Behavior:
- --keyword-source explicit path or default ~/.yt-dont-recommend/keyword-block.txt
- http:// keyword sources rejected (mirrors PR #48 hardening)
- --keyword-exclude explicit path required to exist; default auto-loaded
- Empty keyword file is non-fatal (logs INFO, keyword mode no-op)

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 8: `--stats` integration

**Files:**
- Modify: `src/yt_dont_recommend/cli.py` (the `--stats` branch)
- Test: `tests/test_cli.py` (new `TestKeywordStats`)

- [ ] **Step 1: Append failing tests for `--stats` keyword section**

Append to `tests/test_cli.py`:

```python
class TestKeywordStats:
    """--stats output includes a 'Keyword matches' section."""

    def test_stats_shows_zero_keyword_section_when_empty(
        self, capsys, tmp_path, monkeypatch
    ):
        import yt_dont_recommend as ydr
        from yt_dont_recommend import cli
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        monkeypatch.setattr("sys.argv", ["ydr", "--stats"])
        with pytest.raises(SystemExit):
            cli.main()
        out = capsys.readouterr().out
        assert "Keyword matches" in out
        assert "Total: 0" in out

    def test_stats_shows_top_patterns(self, capsys, tmp_path, monkeypatch):
        import yt_dont_recommend as ydr
        from yt_dont_recommend import cli
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")
        # Pre-populate state with keyword stats
        from datetime import datetime, timezone
        state = ydr.load_state()
        state["keyword_stats"] = {
            "total_matched": 15,
            "by_pattern": {"Trump": 10, "Star Trek": 3, "regex:^foo": 2},
            "by_mode": {"substring": 13, "word": 0, "regex": 2},
        }
        ydr.save_state(state)
        monkeypatch.setattr("sys.argv", ["ydr", "--stats"])
        with pytest.raises(SystemExit):
            cli.main()
        out = capsys.readouterr().out
        assert "Total: 15" in out
        assert "Trump" in out
        assert "10" in out
        assert "Star Trek" in out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cli.py::TestKeywordStats -v`

Expected: FAIL — `--stats` does not yet print a Keyword matches section.

- [ ] **Step 3: Add the keyword section to the `--stats` branch in `cli.py`**

Find the existing `if args.stats:` block in `cli.py`. After the existing clickbait section (look for `clickbait_acted` printout), add:

```python
        # Keyword matches section
        kw_stats = state.get("keyword_stats", {})
        kw_total = kw_stats.get("total_matched", 0)
        print()
        print("Keyword matches")
        print(f"  Total: {kw_total}")
        by_pattern = kw_stats.get("by_pattern", {})
        if by_pattern:
            top = sorted(by_pattern.items(), key=lambda kv: kv[1], reverse=True)[:10]
            print("  Top patterns:")
            for pat, count in top:
                print(f"    {pat}: {count}")
        by_mode = kw_stats.get("by_mode", {})
        if any(by_mode.values()):
            print("  By mode:")
            for mode in ("substring", "word", "regex"):
                print(f"    {mode}: {by_mode.get(mode, 0)}")
```

- [ ] **Step 4: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_cli.py::TestKeywordStats -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/yt_dont_recommend/cli.py tests/test_cli.py
git commit -m "feat(cli): --stats includes Keyword matches section

Shows total acted, top 10 patterns by count, and per-mode tallies.
Helps users prune dead rules and see how often each tier fires.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 9: `process_channels` Phase 3 integration in `browser.py`

**Files:**
- Modify: `src/yt_dont_recommend/browser.py` (add kwargs; Phase 3 logic in card loop)
- Test: `tests/test_browser.py` (new `TestKeywordPhase3`)

- [ ] **Step 1: Append failing tests using the existing mocked-Playwright pattern**

Append to `tests/test_browser.py`:

```python
class TestKeywordPhase3:
    """process_channels Phase 3 — keyword matching before clickbait."""

    def test_keyword_match_acts_and_skips_clickbait(self, monkeypatch):
        """A card matching a keyword rule is acted on; clickbait classifier
        does not see it."""
        # Use the existing mocked-Playwright fixture pattern from this file.
        # (Refer to existing TestProcessChannelsClickbait for the shape.)
        # Skeleton:
        from yt_dont_recommend import browser
        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])

        # Build a fake page that returns a single card with title "Star Trek finale"
        # (Reuse the mock helpers used by existing browser tests.)
        # ...assert the keyword "Not interested" click happens
        # ...assert the clickbait classifier was NOT called for that video
        pass  # Implementation below — see Step 4 for the full mock fixture

    def test_keyword_excluded_channel_skipped(self):
        """A card whose channel is in keyword_excludes is not keyword-matched."""
        pass  # see Step 4

    def test_subscribed_channel_keyword_acts_anyway(self):
        """Subscription protection (option A) — keyword acts on subscribed channels."""
        pass  # see Step 4
```

- [ ] **Step 2: Read the existing mocked-Playwright pattern in `tests/test_browser.py`**

Run: `grep -nE "class TestProcessChannels|def _make_fake_page|monkeypatch" tests/test_browser.py | head -30`

Identify the helper(s) used to build a fake `page` and the subscription-set mocking. This existing scaffolding is reused for Phase 3 tests so we don't reinvent the fake-DOM fixture.

- [ ] **Step 3: Modify `process_channels` signature in `browser.py`**

Find the `process_channels` definition (around line 657):

```python
def process_channels(channel_sources: dict[str, str],
                     to_unblock: list[str] | None = None,
                     state: dict | None = None,
                     dry_run: bool = False,
                     limit: int | None = None,
                     headless: bool = False,
                     clickbait_cfg: dict | None = None,
                     exclude_set: set[str] | None = None,
                     _browser: tuple | None = None) -> None:
```

Update to add the two new kwargs:

```python
def process_channels(channel_sources: dict[str, str],
                     to_unblock: list[str] | None = None,
                     state: dict | None = None,
                     dry_run: bool = False,
                     limit: int | None = None,
                     headless: bool = False,
                     clickbait_cfg: dict | None = None,
                     exclude_set: set[str] | None = None,
                     keyword_compiled: list | None = None,
                     keyword_excludes: set[str] | None = None,
                     _browser: tuple | None = None) -> None:
```

Update the docstring section that documents the early-return condition. Find the existing line:

```python
    if not channel_sources and not to_unblock and clickbait_cfg is None:
```

Replace with:

```python
    if (not channel_sources and not to_unblock
            and clickbait_cfg is None
            and not keyword_compiled):
```

- [ ] **Step 4: Add the Phase 3 logic in the per-card loop**

Inside `process_channels`, find the per-card iteration. The existing flow (simplified) is:

```python
for card in cards_seen:
    handle = card["handle"]
    title = card["title"]
    video_id = card["video_id"]

    # Phase 1: channel-level blocklist
    if handle in channel_sources:
        ...act on channel...
        continue

    # Phase 2: subscription gate (skips for clickbait if subscribed)
    if handle in subs_set and not <keyword>:
        continue

    # Phase 4: clickbait classification (existing)
    if _run_clickbait and ...:
        ...
```

Update the Phase 2 condition to bypass the gate when keyword mode is active, and insert Phase 3 between Phase 2 and Phase 4. The full block becomes:

```python
    # Phase 2: subscription gate for video-level actions.
    # Bypass the gate when keyword mode is active — keyword matching ignores
    # subscription status (topic preference wins over channel trust).
    keyword_active = bool(keyword_compiled)
    if handle in subs_set and not keyword_active:
        continue

    # Phase 3: keyword matching (NEW).
    if keyword_active:
        already_acted = video_id in (state.get("keyword_acted") or {})
        excluded = handle.lower() in (keyword_excludes or set())
        if not already_acted and not excluded:
            from .keywords import match_title
            result = match_title(title, keyword_compiled)
            if result is not None:
                _record_keyword_match(state, video_id, title, handle, result)
                if dry_run:
                    log.info(
                        "WOULD MATCH (keyword): %s — line %d %s:%s",
                        title, result.line, result.mode, result.pattern,
                    )
                else:
                    _click_not_interested(page, card)
                    log.info(
                        "Keyword match: %s — line %d %s:%s",
                        title, result.line, result.mode, result.pattern,
                    )
                save_state(state)
                continue
```

- [ ] **Step 5: Add the `_record_keyword_match` helper near other private helpers in `browser.py`**

Find the section in `browser.py` with private helpers (e.g., `_click_not_interested` if it already exists, or other underscore-prefixed helpers near the top of the module). Add:

```python
def _record_keyword_match(state, video_id, title, channel, result):
    """Append the match to state['keyword_acted'] and bump state['keyword_stats']."""
    from datetime import datetime, timezone
    state.setdefault("keyword_acted", {})
    state["keyword_acted"][video_id] = {
        "acted_at": datetime.now(tz=timezone.utc).isoformat(),
        "title": title,
        "channel": channel,
        "matched_pattern": result.pattern,
        "matched_mode": result.mode,
        "matched_line": result.line,
    }
    stats = state.setdefault("keyword_stats", {})
    stats["total_matched"] = stats.get("total_matched", 0) + 1
    bp = stats.setdefault("by_pattern", {})
    bp[result.pattern] = bp.get(result.pattern, 0) + 1
    bm = stats.setdefault("by_mode", {})
    bm[result.mode] = bm.get(result.mode, 0) + 1
```

- [ ] **Step 6: Update the scan-description log line to include keyword detection**

Find the existing log line that mentions `+ clickbait detection` (around line 786):

```python
            log.info(f"... + clickbait detection")
            ...
            _scan_desc = "clickbait detection"
```

Update to also reflect keyword mode:

```python
            scan_modes = []
            if channel_lookup:
                scan_modes.append("blocklist")
            if keyword_active:
                scan_modes.append("keyword detection")
            if clickbait_cfg is not None:
                scan_modes.append("clickbait detection")
            log.info(f"Starting feed scan with: {' + '.join(scan_modes) or '(no modes)'}")
            _scan_desc = " + ".join(scan_modes) or "scan"
```

(Adjust the actual line based on the existing log-line style; preserve any prefixes/suffixes.)

- [ ] **Step 7: Now flesh out the test stubs from Step 1**

Reuse the existing fake-page builder from `tests/test_browser.py`. A complete test for the first stub (adapt to the actual helper names in that file):

```python
    def test_keyword_match_acts_and_skips_clickbait(self, monkeypatch):
        from yt_dont_recommend import browser
        from yt_dont_recommend.keywords import compile_keywords

        compiled = compile_keywords([(1, "Star Trek")])
        state = {"keyword_acted": {}, "keyword_stats": {
            "total_matched": 0, "by_pattern": {}, "by_mode": {"substring": 0, "word": 0, "regex": 0}
        }}

        # Build a fake page with one card (use existing helper in test_browser.py)
        page = _make_fake_page(cards=[{
            "video_id": "vid_st",
            "title": "Star Trek finale spoilers",
            "channel_handle": "@trekfan",
        }])
        # Mock subscriptions, save_state, click handler, etc., per existing pattern
        with patch("yt_dont_recommend.browser.fetch_subscriptions", return_value=set()):
            with patch("yt_dont_recommend.browser.save_state"):
                with patch("yt_dont_recommend.browser._click_not_interested") as ni:
                    browser.process_channels(
                        channel_sources={},
                        state=state,
                        keyword_compiled=compiled,
                        _browser=("pwcm-stub", "context-stub", page),
                    )
        assert "vid_st" in state["keyword_acted"]
        assert state["keyword_stats"]["total_matched"] == 1
        ni.assert_called_once()
```

(The exact mock surface depends on what `tests/test_browser.py` already exposes. Read 5-10 lines of an existing process_channels test to copy the right fixture shape.)

Same pattern for the other two test stubs:
- `test_keyword_excluded_channel_skipped`: pass `keyword_excludes={"@trekfan"}`, assert `_click_not_interested` not called and `keyword_acted` empty.
- `test_subscribed_channel_keyword_acts_anyway`: mock `fetch_subscriptions` to return `{"@trekfan"}`, assert keyword acted.

- [ ] **Step 8: Run the new tests**

Run: `.venv/bin/python -m pytest tests/test_browser.py::TestKeywordPhase3 -v`

Expected: PASS.

- [ ] **Step 9: Run the full suite**

Run: `.venv/bin/python -m pytest -x --tb=short`

Expected: All tests PASS.

- [ ] **Step 10: Commit**

```bash
git add src/yt_dont_recommend/browser.py tests/test_browser.py
git commit -m "feat(browser): wire keyword matching as Phase 3 in card loop

process_channels gains keyword_compiled and keyword_excludes kwargs.
Phase 3 runs after the channel-level blocklist (Phase 1) and the
subscription gate (Phase 2, bypassed when keyword mode is active per
the option-A subscription-protection decision in the spec). Phase 4
(clickbait classification) only runs on cards that didn't keyword-match.

New private helper _record_keyword_match writes the match into
state['keyword_acted'] and bumps state['keyword_stats'] in place.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 10: Shadow-limit union via `_acted_video_ids`

**Files:**
- Modify: `src/yt_dont_recommend/clickbait.py` (single call site for shadow-limit check)
- Test: `tests/test_clickbait.py` (new test asserting union behavior)

- [ ] **Step 1: Append a failing test that proves the shadow-limit check sees keyword-acted videos**

Append to `tests/test_clickbait.py`:

```python
class TestShadowLimitUnion:
    def test_shadow_limit_check_sees_keyword_acted(self, tmp_path, monkeypatch):
        """A previously keyword-acted video re-encountered triggers the
        shadow-limit detection just like a previously clickbait-acted one."""
        import yt_dont_recommend as ydr
        monkeypatch.setattr(ydr, "STATE_FILE", tmp_path / "processed.json")

        # Pre-populate state with a keyword-acted video older than the grace period.
        from datetime import datetime, timedelta, timezone
        from yt_dont_recommend.config import SHADOW_LIMIT_GRACE_HOURS
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=SHADOW_LIMIT_GRACE_HOURS + 1)).isoformat()
        state = ydr.load_state()
        state["keyword_acted"]["vid_old"] = {
            "acted_at": old, "title": "x", "channel": "@a",
            "matched_pattern": "p", "matched_mode": "substring", "matched_line": 1,
        }

        # Use whatever shadow-limit detection helper exists in clickbait.py.
        # Call it twice with vid_old to simulate two recurrences (above WARN_AFTER threshold).
        # The check should fire — verify via the helper's return / log /
        # state field that the existing test for clickbait_acted uses.
        # ...assert shadow-limit triggered
```

(Adapt the assertion to match the existing shadow-limit helper's contract — read that helper before writing this test.)

- [ ] **Step 2: Read the existing shadow-limit helper**

Run: `grep -nB2 -A20 'SHADOW_LIMIT' src/yt_dont_recommend/clickbait.py | head -40`

Identify the helper that consults `state["clickbait_acted"]`. Note its shape (function name, return type).

- [ ] **Step 3: Update `clickbait.py` to call `_acted_video_ids(state)` instead of reading `state["clickbait_acted"]` directly**

Find the line in `clickbait.py` that does something like:

```python
acted = state.get("clickbait_acted", {})
if video_id in acted and ...:
    ...
```

Replace with:

```python
from .state import _acted_video_ids
acted_ids = _acted_video_ids(state)
if video_id in acted_ids and ...:
    ...
```

(Preserve surrounding logic; only the source of `acted_ids` changes.)

If the existing code reads the timestamp from `state["clickbait_acted"][video_id]["acted_at"]` (for the grace-hours window), look up the timestamp from whichever dict has the entry:

```python
def _acted_at(state: dict, video_id: str) -> str | None:
    for key in ("clickbait_acted", "keyword_acted"):
        d = state.get(key, {})
        if video_id in d:
            return d[video_id].get("acted_at")
    return None
```

Add this helper next to the shadow-limit helper if needed.

- [ ] **Step 4: Run the new test**

Run: `.venv/bin/python -m pytest tests/test_clickbait.py::TestShadowLimitUnion -v`

Expected: PASS.

- [ ] **Step 5: Run the existing clickbait tests to make sure nothing regressed**

Run: `.venv/bin/python -m pytest tests/test_clickbait.py -v`

Expected: All clickbait tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/yt_dont_recommend/clickbait.py tests/test_clickbait.py
git commit -m "refactor(clickbait): shadow-limit reads union of acted sets

Switches the shadow-limit detection from reading clickbait_acted
directly to using state._acted_video_ids, which returns the union
of clickbait_acted and keyword_acted. A video re-encountered after
either action is the same diagnostic signal — YouTube has not honored
our 'Not interested' click in either case.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 11: Smoke test addition

**Files:**
- Create: `tests/fixtures/keyword-block-fixture.txt` (3-tier sample)
- Modify: `scripts/smoke-test.sh`

- [ ] **Step 1: Create the fixture file**

Create `tests/fixtures/keyword-block-fixture.txt`:

```
# yt-dont-recommend keyword-block fixture for smoke test.
# Three tiers: substring (default), word: prefix, regex: prefix.

Trump
word:trek
regex:^\d+ reasons?
```

- [ ] **Step 2: Read the existing smoke test to find the pattern for adding an invocation**

Run: `cat scripts/smoke-test.sh`

Note the structure (it likely runs `--blocklist --dry-run` and `--clickbait --dry-run` invocations and asserts exit code 0 + a log line).

- [ ] **Step 3: Add a new invocation block at the end of `scripts/smoke-test.sh` (before the final summary if any)**

Append to `scripts/smoke-test.sh`:

```bash
echo "Smoke test 20: --keyword-block --dry-run with 3-tier fixture"
.venv/bin/yt-dont-recommend --keyword-block --dry-run --keyword-source tests/fixtures/keyword-block-fixture.txt
echo "Smoke test 20: PASS"
```

(If the smoke test counts invocations explicitly in an integer "Smoke test N" pattern, increment from the existing count.)

- [ ] **Step 4: Run the smoke test**

Run: `bash scripts/smoke-test.sh`

Expected: All smoke tests PASS, including the new keyword invocation. The new invocation should log something like `WOULD MATCH (keyword): ...` if the home feed has a matching card, or simply complete with no matches if nothing in the feed hits.

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke-test.sh tests/fixtures/keyword-block-fixture.txt
git commit -m "test(smoke): cover --keyword-block --dry-run with 3-tier fixture

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 12: `keyword-block.example.txt` (repo-root sample)

**Files:**
- Create: `keyword-block.example.txt`

- [ ] **Step 1: Create the example file**

Create `keyword-block.example.txt` at the repo root:

```
# yt-dont-recommend keyword-block example.
#
# Copy to ~/.yt-dont-recommend/keyword-block.txt and edit to taste.
# Lines starting with '#' are comments. Blank lines are ignored.
# Three matching tiers selected by line prefix:
#
#   <bare text>          — substring match (case-insensitive, default tier)
#   word:<phrase>        — whole-word match using \b regex anchors
#   regex:<pattern>      — full Python regex; case-insensitive by default,
#                          override with (?-i) inline flags
#
# Rules are evaluated in source-file order; first match wins.

# Topics — substring tier
Trump
Star Trek

# Word-boundary — won't match "trekking" or "Trumpian"
word:trek
word:trump

# Regex — full re.IGNORECASE by default
regex:\b(rfk|kennedy)\b
regex:^\d+ reasons?
```

- [ ] **Step 2: Stage and commit**

```bash
git add keyword-block.example.txt
git commit -m "docs: add keyword-block.example.txt at repo root

Mirrors the existing clickbait-config.example.yaml pattern. Users
copy to ~/.yt-dont-recommend/keyword-block.txt and edit to taste.

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Task 13: Documentation — CLAUDE.md, README.md, CHANGELOG.md

**Files:**
- Modify: `CLAUDE.md` (Architecture, State Schema, CLI flags, Modules)
- Modify: `README.md` (Usage section)
- Modify: `CHANGELOG.md` (`[Unreleased] / Added` entry)

- [ ] **Step 1: Update `CHANGELOG.md` with an `[Unreleased] / Added` entry**

Find the `[Unreleased] / Added` section. Add at the top of the bullet list:

```markdown
- **Keyword blocking** (PR A — core feature): new `--keyword-block` mode scans video titles in the YouTube home feed against a user-defined keyword list (`~/.yt-dont-recommend/keyword-block.txt` by default; `--keyword-source PATH-OR-URL` overrides). Three matching tiers selected by line prefix: bare substring (case-insensitive), `word:<phrase>` for whole-word matches, and `regex:<pattern>` for full Python regex with `re.IGNORECASE` by default. Independent of `--clickbait` (no LLM dependency). Runs as Phase 3 in the per-card loop, between the channel-level blocklist and clickbait classification. Bypasses subscription protection — topic preference wins over channel subscription. Independent `--keyword-exclude` flag and file (not reused from `--clickbait-exclude`). New `keywords.py` module (peer of `blocklist.py` / `clickbait.py`), 5 new public functions, ~50 new tests. State schema bumps 4 → 5 with new `keyword_acted` (90-day TTL) and `keyword_stats` (cumulative) keys. `--stats` shows a Keyword matches section with totals, top patterns, and per-mode tallies. PR B (scheduler integration with `--keyword-runs N`) is intentionally separate.
```

- [ ] **Step 2: Update `CLAUDE.md` — add new module entry under Architecture**

Find the "Modules:" list under "## Architecture" near the top. Add:

```markdown
- `keywords.py` — keyword blocking: `parse_keyword_file`, `compile_keywords`, `match_title`, `resolve_keyword_source`, `load_keyword_excludes`. Three matching tiers (substring / `word:` / `regex:`). Pure-logic; no Playwright, no LLM, no network at import time.
```

- [ ] **Step 3: Update `CLAUDE.md` — extend the State Schema section**

Find the State Schema section. After the v4 description, add:

```markdown
**v5 additions**: `keyword_acted` and `keyword_stats` (both default to empty `{}` / structured zero counts).
- `keyword_acted`: keyed by video_id. Records `acted_at`, `title`, `channel`, `matched_pattern`, `matched_mode` (`"substring"|"word"|"regex"`), and `matched_line` (1-indexed source line in the keyword file). Pruned at 90 days on load (`KEYWORD_ACTED_PRUNE_DAYS`). Fed into the shadow-limit detection's union check alongside `clickbait_acted` via the new `_acted_video_ids(state)` helper.
- `keyword_stats`: cumulative counts (`total_matched`, `by_pattern`, `by_mode`). Permanent — not pruned.
```

- [ ] **Step 4: Update `CLAUDE.md` — extend the CLI flags table**

Find the CLI Flags table. Add three rows:

```markdown
| `--keyword-block` | Run video-level title keyword filtering. Independent of `--clickbait`; no LLM dependency. |
| `--keyword-source` | Keyword list source for `--keyword-block`. Local file path or `https://` URL. Default: `~/.yt-dont-recommend/keyword-block.txt`. `http://` rejected (insecure). |
| `--keyword-exclude` | Channels to never evaluate for keyword matches. Local file path or URL. Default auto-loads `~/.yt-dont-recommend/keyword-exclude.txt` if present. |
```

- [ ] **Step 5: Update `README.md` — add a "Keyword filtering" section under Usage**

Find the Usage section. Add a new subsection after the existing `--clickbait` documentation:

```markdown
### Keyword filtering

`--keyword-block` scans video titles in the YouTube home feed against a user-defined keyword list and clicks "Not interested" on matches. No LLM dependency. Three matching tiers selected by line prefix in `~/.yt-dont-recommend/keyword-block.txt`:

- **Bare text** — substring match, case-insensitive (the default tier). Example: `Trump`.
- **`word:<phrase>`** — whole-word match. `word:trek` matches "Star Trek finale" but not "trekking".
- **`regex:<pattern>`** — full Python regex with `re.IGNORECASE`. Override with `(?-i)` inline flags. Example: `regex:^\d+ reasons?`.

Comments start with `#` and blank lines are ignored. Rules are evaluated in source-file order; first match wins, so put narrower rules above broader ones if you want them credited specifically. See `keyword-block.example.txt` at the repo root for a starting template.

Optional `--keyword-source PATH-OR-URL` overrides the default file (local path or `https://` URL; `http://` rejected). Optional `--keyword-exclude` carries channel handles to skip — useful when you want to keep one trusted channel even though its titles match your filter. Defaults to auto-loading `~/.yt-dont-recommend/keyword-exclude.txt` if present.

```bash
# One-off run with the default file
yt-dont-recommend --keyword-block

# Combine with channel-level blocklist and clickbait detection
yt-dont-recommend --blocklist --keyword-block --clickbait

# Dry run to preview what would match
yt-dont-recommend --keyword-block --dry-run
```
```

- [ ] **Step 6: Run all tests one more time to confirm full-suite green**

Run: `.venv/bin/python -m pytest -x --tb=short`

Expected: All tests PASS.

- [ ] **Step 7: Run ruff over all modified files**

Run: `.venv/bin/python -m ruff check src/ tests/`

Expected: No findings.

- [ ] **Step 8: Run the full smoke test**

Run: `bash scripts/smoke-test.sh`

Expected: All smoke tests PASS.

- [ ] **Step 9: Commit the documentation**

```bash
git add CLAUDE.md README.md CHANGELOG.md
git commit -m "docs: keyword-blocking feature (PR A)

- CHANGELOG.md: [Unreleased] / Added entry summarizing the feature
- CLAUDE.md: new keywords.py module entry; v5 state schema additions;
  three new CLI flags in the table
- README.md: new 'Keyword filtering' section under Usage with format
  examples and combined-mode invocation samples

Refs spec: docs/superpowers/specs/2026-04-28-keyword-blocking-design.md"
```

---

## Final Steps: Push and open PR

- [ ] **Step 1: Push the feature branch**

Run:
```bash
source /home/cmeans/github.com/cmeans/claude-dev/github-app/activate.sh && git push -u origin feature/keyword-blocking
```

- [ ] **Step 2: Open the PR**

Use `gh pr create` with title `feat(keywords): keyword blocking — core feature (PR A)` and a body that:

- Summarizes the feature (3-5 bullets pulled from the CHANGELOG entry)
- Links to the spec at `docs/superpowers/specs/2026-04-28-keyword-blocking-design.md`
- Notes that PR B (scheduler integration with `--keyword-runs N`) is a separate follow-up
- Includes a Test plan checklist:
  - [ ] CI passes (ruff + pytest on ubuntu and macos)
  - [ ] Live `--keyword-block --dry-run` against the home feed signed off by maintainer
  - [ ] At least one full `--keyword-block` run (non-dry) signed off by maintainer
  - [ ] CHANGELOG entry, README section, and CLAUDE.md updates reviewed

The PR will follow the standard QA workflow: `Awaiting CI` → `Ready for QA` → maintainer reviews → `QA Approved` → squash-merge.

---

## Self-Review Checklist (run inline before declaring this plan ready)

**Spec coverage:** Each section of the spec maps to a task —
- §1 Summary → addressed across all tasks
- §2 Architecture → Task 3 + Task 5
- §3 Configuration formats → Task 3 (formats); Task 12 (example file)
- §4 Matching engine → Task 3
- §5 State schema → Task 1 (constants/version), Task 2 (full schema + helper)
- §6 CLI surface → Task 6 (flags), Task 7 (setup), Task 8 (--stats)
- §7 Pipeline integration → Task 9
- §8 Error handling → Task 4 (resolve_keyword_source), Task 7 (setup-block error paths)
- §9 Testing strategy → Tasks 2, 3, 4, 5, 7, 8, 9, 10, 11
- §10 Out of scope → not implemented (correctly)
- §11 Documentation deltas → Task 13 + Task 12

**Placeholder scan:** None left. All "..." in code blocks are intentional ("preserve surrounding logic") and explained inline.

**Type consistency:**
- `CompiledKeyword(pattern, mode, line, matcher)` consistent across Task 3 (define), Task 9 (`match_title` consumed), Task 8 (`by_pattern`/`by_mode` reporting).
- `MatchResult(pattern, mode, line)` consistent across Task 3 and Task 9 (`_record_keyword_match`).
- `_acted_video_ids(state) -> set[str]` consistent: Task 2 defines, Task 10 consumes.
- State key names `keyword_acted` and `keyword_stats` consistent everywhere.

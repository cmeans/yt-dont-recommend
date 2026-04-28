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
from pathlib import Path
from typing import NamedTuple
from urllib.error import URLError
from urllib.request import Request, urlopen

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
    word:<phrase>:        re.compile(rf"\\b{re.escape(phrase)}\\b", IGNORECASE).
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

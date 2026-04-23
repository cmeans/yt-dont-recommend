"""
Blocklist fetching and parsing: resolve_source, parse_text_blocklist,
parse_json_blocklist, fetch_remote, channel_to_url, check_removals.

Imports from config.py for constants, and from state.py for save_state.

resolve_source calls fetch_remote via _pkg() so that
patch("yt_dont_recommend.fetch_remote") works correctly in tests.
"""

import json
import logging
import re
import sys

log = logging.getLogger(__name__)
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import BUILTIN_SOURCES
from .state import save_state

_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]+$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


def _canonicalize_channel(raw: str) -> str | None:
    s = raw.strip()
    if _HANDLE_RE.match(s) or _CHANNEL_ID_RE.match(s):
        return s
    return None


def _pkg():
    """Late import of yt_dont_recommend to get live-patched attributes (e.g. fetch_remote in tests)."""
    import yt_dont_recommend as _p
    return _p


def _get_current_version_for_ua() -> str:
    """Return the running version for use in User-Agent headers."""
    try:
        return _pkg()._get_current_version()
    except Exception:
        try:
            from .config import __version__
            return __version__
        except Exception:
            return "unknown"


def parse_text_blocklist(raw: str) -> list[str]:
    """Parse plain text blocklist: one channel path per line.

    Supports # and ! comment prefixes (full-line and inline).
    Normalizes all variants to canonical form: @handle or UCxxx.
    Invalid entries are silently dropped; a single WARNING is emitted
    if any were dropped.

    Examples of valid lines:
        @SomeChannel
        @SomeChannel  # optional inline note
        UCxxxxxxxxxxxxxxxxxxxxxx
    """
    channels = []
    dropped = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        # Strip inline comment: "@handle  # reason" → "@handle"
        if "#" in line:
            line = line[:line.index("#")].strip()
        # Strip leading slash: /@handle → @handle
        if line.startswith("/@"):
            line = line[1:]
        # Strip /channel/ prefix: /channel/UCxxx → UCxxx
        elif line.startswith("/channel/"):
            line = line[len("/channel/"):]
        canonical = _canonicalize_channel(line)
        if canonical is None:
            dropped += 1
        else:
            channels.append(canonical)
    if dropped:
        log.warning("Dropped %d invalid channel %s from blocklist", dropped,
                    "entry" if dropped == 1 else "entries")
    return channels


def parse_json_blocklist(raw: str) -> list[str]:
    """Parse JSON blocklist. Handles several common formats.

    All results are normalized to canonical form: @handle or UCxxx.
    Invalid entries are silently dropped; a single WARNING is emitted
    if any were dropped.
    """
    channels = []
    dropped = 0
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, str):
                    # Normalize /@handle → @handle, /channel/UCxxx → UCxxx
                    if entry.startswith("/@"):
                        entry = entry[1:]
                    elif entry.startswith("/channel/"):
                        entry = entry[len("/channel/"):]
                    canonical = _canonicalize_channel(entry)
                    if canonical is None:
                        dropped += 1
                    else:
                        channels.append(canonical)
                elif isinstance(entry, dict):
                    for key in ("channelHandle", "handle", "channelId", "id", "url"):
                        if key in entry:
                            val = entry[key]
                            if not isinstance(val, str):
                                continue
                            if val.startswith("http"):
                                path = urlparse(val).path  # e.g. /@handle
                                if path.startswith("/@"):
                                    val = path[1:]
                                elif path.startswith("/channel/"):
                                    val = path[len("/channel/"):]
                                else:
                                    val = path
                            elif val.startswith("UC"):
                                pass  # already canonical
                            elif val.startswith("@"):
                                pass  # already canonical
                            canonical = _canonicalize_channel(val)
                            if canonical is None:
                                dropped += 1
                            else:
                                channels.append(canonical)
                            break
        elif isinstance(data, dict):
            for key in data:
                if key.startswith("UC") or key.startswith("@"):
                    canonical = _canonicalize_channel(key)
                    if canonical is None:
                        dropped += 1
                    else:
                        channels.append(canonical)
    except json.JSONDecodeError:
        log.warning("Failed to parse as JSON; falling back to line-by-line text parsing")
        channels = parse_text_blocklist(raw)
    if dropped:
        log.warning("Dropped %d invalid channel %s from blocklist", dropped,
                    "entry" if dropped == 1 else "entries")
    return channels


def fetch_remote(url: str) -> str:
    req = Request(url, headers={"User-Agent": f"yt-dont-recommend/{_get_current_version_for_ua()}"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except Exception as e:
        raise RuntimeError(f"Failed to fetch {url}: {e}") from e


def resolve_source(source: str, quiet: bool = False) -> list[str]:
    """
    Resolve --source to a list of channel paths. Accepts:
      - A built-in key ("deslop", "aislist")
      - A local file path
      - An HTTP/HTTPS URL

    quiet=True suppresses per-file INFO lines (used when loading the exclude file,
    where the caller logs a single consolidated message instead).

    Calls fetch_remote via _pkg() so test patches on yt_dont_recommend.fetch_remote
    are intercepted correctly.
    """
    # Use _pkg().fetch_remote so that patch("yt_dont_recommend.fetch_remote") works in tests
    _fetch = _pkg().fetch_remote

    if source in BUILTIN_SOURCES:
        info = BUILTIN_SOURCES[source]
        log.info(f"Fetching built-in source '{source}' ({info['name']}): {info['url']}")
        raw = _fetch(info["url"])
        channels = parse_text_blocklist(raw) if info["format"] == "text" else parse_json_blocklist(raw)
        log.info(f"Fetched {len(channels)} channels from {info['name']}")
        return channels

    if source.startswith("http://"):
        log.error(
            "Refusing insecure http:// source: %s. Use https:// instead, "
            "or serve the file locally and pass a local file path.",
            source,
        )
        sys.exit(1)

    if source.startswith("https://"):
        if not quiet:
            log.info(f"Fetching remote blocklist: {source}")
        raw = _fetch(source)
        stripped = raw.lstrip()
        channels = parse_json_blocklist(raw) if stripped.startswith(("{", "[")) else parse_text_blocklist(raw)
        if not quiet:
            log.info(f"Fetched {len(channels)} channels from {source}")
        return channels

    path = Path(source).expanduser().resolve()
    if not path.exists():
        log.error(f"File not found: {path}")
        sys.exit(1)
    if not quiet:
        log.info(f"Reading local blocklist: {path}")
    raw = path.read_text(encoding="utf-8")
    stripped = raw.lstrip()
    channels = parse_json_blocklist(raw) if stripped.startswith(("{", "[")) else parse_text_blocklist(raw)
    if not quiet:
        log.info(f"Read {len(channels)} channels from {path.name}")
    return channels


def channel_to_url(channel: str) -> str:
    """Convert a canonical channel identifier to a full YouTube URL."""
    if channel.startswith("http"):
        return channel
    if channel.startswith("@"):
        return f"https://www.youtube.com/{channel}"
    if channel.startswith("UC"):
        return f"https://www.youtube.com/channel/{channel}"
    return f"https://www.youtube.com/{channel}"


def check_removals(state: dict, current_channels: list[str],
                   source: str, unblock_policy: str) -> list[str]:
    """
    Compare currently-fetched blocklist against previously-blocked channels.

    If a channel was blocked because of `source` but is no longer in the
    current list, it may be a false positive that the list maintainer corrected.

    unblock_policy:
      "all" — only unblock when the channel has been dropped from every source
               that originally blocked it (conservative, default)
      "any" — unblock as soon as any single source drops the channel

    Modifies state in place. Returns list of channels that should be
    unblocked on YouTube (browser action still required).
    """
    current_set = {c.lower() for c in current_channels}
    blocked_by = state.get("blocked_by", {})
    to_unblock: list[str] = []

    for channel, info in list(blocked_by.items()):
        sources = info.get("sources", [])
        if source not in sources:
            continue
        if channel.lower() in current_set:
            continue

        # This channel was blocked by `source` but is no longer on that list
        other_sources = [s for s in sources if s != source]

        if unblock_policy == "any" or not other_sources:
            # Save to pending_unblock before removing from state, so a failed browser
            # unblock can be retried on the next run without losing the channel.
            state.setdefault("pending_unblock", {})[channel] = info.copy()
            del blocked_by[channel]
            to_unblock.append(channel)
            if other_sources:
                log.warning(
                    f"*** UNBLOCKING {channel} — dropped from '{source}'. "
                    f"NOTE: still present in {other_sources} but unblocking "
                    f"because --unblock-policy=any."
                )
            else:
                log.warning(
                    f"*** UNBLOCKING {channel} — removed from '{source}' blocklist "
                    f"(possible false positive correction by list maintainer)."
                )
            save_state(state)
        else:
            # policy == "all" and other sources still assert the block
            info["sources"] = other_sources
            log.info(
                f"NOTE: {channel} was dropped from '{source}' but is still "
                f"blocked by: {other_sources}. Will unblock when removed from all sources."
            )

    return to_unblock

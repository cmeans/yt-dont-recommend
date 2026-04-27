"""
Configuration constants, file paths, selector lists, and logging setup.

Imports nothing from the yt_dont_recommend package — safe to import first.
"""

import logging
import logging.handlers
import sys
from pathlib import Path

# --- Configuration ---

BUILTIN_SOURCES = {
    "deslop": {
        "name": "DeSlop",
        "url": "https://raw.githubusercontent.com/NikoboiNFTB/DeSlop/refs/heads/main/block/list.txt",
        "format": "text",
        "description": "Curated list from the DeSlop project (~130+ channels)",
    },
    "aislist": {
        "name": "AiSList",
        "url": "https://raw.githubusercontent.com/Override92/AiSList/main/AiSList/aislist_blocklist.txt",
        "format": "text",
        "description": "Community-maintained list from AiSList (~8400+ channels, ! comments)",
    },
}

DEFAULT_SOURCES = list(BUILTIN_SOURCES.keys())  # run all built-in sources by default

# Top-level data directory — contains all persistent state, logs, and config.
# Created with mode 0o700 (owner-only) to protect session cookies and state.
DATA_DIR = Path.home() / ".yt-dont-recommend"

# Browser profile directory (persists login state between runs)
PROFILE_DIR = DATA_DIR / "browser-profile"

# State file to track which channels have been processed
STATE_FILE = Path.home() / ".yt-dont-recommend" / "processed.json"

# Log file
LOG_FILE = Path.home() / ".yt-dont-recommend" / "run.log"

# Default personal exclusion lists — loaded automatically if present.
# blocklist-exclude.txt: channels never blocked via --blocklist.
# clickbait-exclude.txt: channels never evaluated for clickbait.
# Legacy name (exclude.txt) is accepted for blocklist exclusions with a deprecation warning.
DEFAULT_BLOCKLIST_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "blocklist-exclude.txt"
DEFAULT_CLICKBAIT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "clickbait-exclude.txt"
_LEGACY_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "exclude.txt"

# Delays (seconds) — be respectful to avoid rate limiting
MIN_DELAY = 3.0
MAX_DELAY = 7.0
PAGE_LOAD_WAIT = 3.0
LONG_PAUSE_EVERY = 25
LONG_PAUSE_SECONDS = 30
# Selector health check: if this many consecutive passes each have >=
# MIN_CARDS_FOR_SELECTOR_CHECK cards but yield zero parseable channel links,
# the feed card selector is probably broken.
MIN_CARDS_FOR_SELECTOR_CHECK = 10
SELECTOR_WARN_AFTER = 3
# Default per-session action cap (blocks + clickbait marks combined).
# Keeps sessions human-length. Override with --no-limit.
DEFAULT_SESSION_CAP = 75

# Attention flag file — written when something requires user action between runs
ATTENTION_FILE = Path.home() / ".yt-dont-recommend" / "needs-attention.txt"

# Optional user config file — timing overrides live here
CONFIG_FILE = Path.home() / ".yt-dont-recommend" / "config.yaml"

# Version — single source of truth is pyproject.toml; read at import time.
def _resolve_version() -> str:
    """Return the installed distribution version, or "0.0.0" when metadata is
    unavailable (editable installs without `pip install -e .`).

    Factored out so the fallback path is testable: the except branch is hit
    by patching `importlib.metadata.version` to raise.
    """
    try:
        from importlib.metadata import version as _pkg_version
        return _pkg_version("yt-dont-recommend")
    except Exception:
        return "0.0.0"


__version__: str = _resolve_version()
VERSION_CHECK_INTERVAL = 86400  # seconds between automatic checks (24 h)

# State schema version — bump this whenever the state file structure changes.
# Policy: only ADD new keys (never rename/remove/reinterpret existing ones).
# load_state() warns when it reads a state file written by a newer version.
STATE_VERSION = 4

# Auto-upgrade delay window: number of days a newly detected PyPI release
# must sit pending before do_auto_upgrade is allowed to install it. Defense
# in depth on top of the trusted-publisher OIDC + isatty gates — gives the
# maintainer N days to yank a compromised release before users auto-install.
# Override via the `auto_upgrade.delay_days` key in config.yaml. Set to 0
# to disable the delay (not recommended).
AUTO_UPGRADE_DELAY_DAYS = 3

# Clickbait cross-run cache TTL: cached classification results expire after
# this many days and are re-evaluated on the next encounter.
CLICKBAIT_CACHE_TTL_DAYS = 14

# Clickbait acted pruning: entries older than this many days are removed from
# clickbait_acted on load (keeps the set from growing indefinitely).
CLICKBAIT_ACTED_PRUNE_DAYS = 90

# Shadow-limiting detection: stop the run after this many re-encounters of
# videos previously acted on with "Not interested" (time-gated; see below).
SHADOW_LIMIT_WARN_AFTER = 2

# Grace period for shadow-limiting detection. A re-encounter within this many
# hours of the original act is treated as normal algorithm latency, not a signal.
SHADOW_LIMIT_GRACE_HOURS = 48

# Set to True by write_attention() so main() can exit with code 1 when
# something serious enough to alert the user occurred during the run.
_had_attention = False

# Schedule management
SCHEDULE_FILE = Path.home() / ".yt-dont-recommend" / "schedule.json"
_LAUNCHD_LABEL = "com.user.yt-dont-recommend"
_LAUNCHD_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LAUNCHD_LABEL}.plist"
_CRON_MARKER = "# managed by yt-dont-recommend"

# Selectors used both for processing and selector checks.
# YouTube changes its DOM frequently — run --check-selectors if things break.
# These module-level constants are the CODE DEFAULTS. Users can override them
# via the `selectors:` section in config.yaml.  Use get_selectors() to get the
# merged view (config overrides on top of code defaults).
VIDEO_SELECTORS = [
    "ytd-rich-item-renderer",
    "ytd-grid-video-renderer",
    "ytd-video-renderer",
    "#contents ytd-rich-grid-row",
]
MENU_BTN_SELECTORS = [
    "button[aria-label='More actions']",                        # home feed and search results
    "button[aria-label='Action menu']",                         # channel pages
    "button.yt-icon-button#button[aria-label='Action menu']",
    "yt-icon-button#button[aria-label='Action menu']",
    "ytd-menu-renderer yt-icon-button",
    "#menu button",
]
MENU_ITEM_SELECTOR = (
    "ytd-menu-service-item-renderer, tp-yt-paper-item, "
    "ytd-menu-navigation-item-renderer, [role='menuitem']"
)
TARGET_PHRASES = ("don't recommend", "dont recommend")

# --- Selector registry ---
# Canonical names for every configurable selector.  get_selectors() merges
# user overrides from config.yaml on top of these defaults.

_SELECTOR_DEFAULTS: dict[str, str | list[str]] = {
    # Feed card scanning
    "feed_card": "ytd-rich-item-renderer",
    "channel_link": "a[href^='/@'], a[href^='/channel/UC']",
    "watch_link": "a[href*='/watch?v=']",
    "title_link": [
        "a#video-title-link",
        "a#video-title",
        "h3 a[href*='watch?v=']",
    ],
    "title_text": "yt-formatted-string#video-title, #video-title",

    # Menu interaction
    "menu_buttons": list(MENU_BTN_SELECTORS),
    "menu_items": MENU_ITEM_SELECTOR,
    "not_interested_items": (
        "ytd-menu-service-item-renderer, tp-yt-paper-item, "
        "ytd-menu-navigation-item-renderer, [role='menuitem'], "
        "yt-list-item-view-model"
    ),
    "not_interested_inner_btn": "button.yt-list-item-view-model__button-or-anchor",

    # Text phrases (configurable for localization)
    "dont_recommend_phrases": list(TARGET_PHRASES),
    "not_interested_phrase": "not interested",

    # Login detection
    "login_check": "#notification-button, ytd-notification-topbar-button-renderer",

    # Subscription page
    "subscription_links": (
        "ytd-channel-renderer a#main-link, "
        "ytd-channel-renderer a[href^='/@'], "
        "ytd-channel-renderer a[href^='/channel/UC']"
    ),

    # Unblock: channel display name resolution
    "channel_name_selectors": [
        "ytd-channel-name yt-formatted-string",
        "#channel-name yt-formatted-string",
        "h1 yt-formatted-string",
        "#channel-name a",
    ],
}


# --- Helpers ---

# Common desktop resolutions; pick one at random per session to vary the
# browser fingerprint slightly. Diagnostics keep a fixed 1280x800 baseline.
_VIEWPORT_POOL = [
    {"width": 1280, "height": 800},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900},
    {"width": 1920, "height": 1080},
]


# Browser profile subdirectories that are safe to delete after each run.
# Removes cached page content without affecting the login session.
_PROFILE_CACHE_DIRS = [
    "Cache",
    "Code Cache",
    "GPUCache",
    "Service Worker",
    "GraphiteDawnCache",
    "GrShaderCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
]


def ensure_data_dir() -> None:
    """Create the data directory with owner-only permissions (0o700).

    Also fixes permissions on existing installations where the directory
    was created with default (world-readable) permissions.
    """
    import os
    import stat

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Fix permissions if they're too open (e.g. from older versions)
    current = stat.S_IMODE(DATA_DIR.stat().st_mode)
    if current != 0o700:
        os.chmod(DATA_DIR, 0o700)

    # Ensure browser profile dir also has restricted permissions
    if PROFILE_DIR.exists():
        current = stat.S_IMODE(PROFILE_DIR.stat().st_mode)
        if current != 0o700:
            os.chmod(PROFILE_DIR, 0o700)


def clear_profile_cache() -> None:
    """Remove browser cache directories from the profile to reduce disk usage
    and limit stored data to what's needed for session persistence."""
    import shutil

    profile_default = PROFILE_DIR / "Default"
    if not profile_default.exists():
        return
    for dirname in _PROFILE_CACHE_DIRS:
        cache_dir = profile_default / dirname
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)


def pick_viewport() -> dict:
    """Return a random viewport size from a pool of common desktop resolutions."""
    import random
    return random.choice(_VIEWPORT_POOL)


def load_timing_config() -> dict:
    """Load timing overrides from ~/.yt-dont-recommend/config.yaml.

    Returns a dict with any of the following keys present in the file's
    `timing:` section (all optional; missing keys fall back to constants):
        min_delay, max_delay, long_pause_every, long_pause_seconds,
        page_load_wait, session_cap

    Returns an empty dict if the file is absent, unparseable, or pyyaml
    is not installed.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"{CONFIG_FILE} exists but pyyaml is not installed — timing overrides ignored. "
            "Install with: pip install pyyaml"
        )
        return {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        timing = data.get("timing", {})
        if not isinstance(timing, dict):
            return {}
        allowed = {"min_delay", "max_delay", "long_pause_every",
                   "long_pause_seconds", "page_load_wait", "session_cap"}
        return {k: v for k, v in timing.items() if k in allowed}
    except Exception:
        return {}


def load_browser_config() -> dict:
    """Load browser behaviour overrides from ~/.yt-dont-recommend/config.yaml.

    Returns a dict with any of the following keys present in the file's
    `browser:` section (all optional; missing keys fall back to defaults):
        use_system_chrome (bool, default True)

    Returns an empty dict if the file is absent, unparseable, or pyyaml
    is not installed.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        browser = data.get("browser", {})
        if not isinstance(browser, dict):
            return {}
        allowed = {"use_system_chrome"}
        return {k: v for k, v in browser.items() if k in allowed}
    except Exception:
        return {}


def load_auto_upgrade_config() -> dict:
    """Load auto-upgrade overrides from the ``auto_upgrade:`` section of config.yaml.

    Returns a dict with whichever of these keys are present in the file:
        delay_days (int) — N-day delay window between first detection of a
            PyPI release and actually installing it. Falls back to
            AUTO_UPGRADE_DELAY_DAYS when missing or invalid.

    Returns an empty dict if config.yaml is absent, unparseable, pyyaml is not
    installed, or the ``auto_upgrade:`` section is missing.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        au = data.get("auto_upgrade", {})
        if not isinstance(au, dict):
            return {}
        result: dict = {}
        if "delay_days" in au:
            try:
                delay = int(au["delay_days"])
                if delay >= 0:
                    result["delay_days"] = delay
            except (TypeError, ValueError):
                pass
        return result
    except Exception:
        return {}


def load_schedule_config() -> dict:
    """Load schedule defaults from the ``schedule:`` section of config.yaml.

    Returns a dict with whichever of these keys are present in the file:
        blocklist_runs (int)  — how many times/day to run blocklist mode
        clickbait_runs (int)  — how many times/day to run clickbait mode
        headless       (bool) — whether scheduled runs use --headless (default True)

    Returns an empty dict if config.yaml is absent, unparseable, pyyaml is not
    installed, or the ``schedule:`` section is missing.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        sched = data.get("schedule", {})
        if not isinstance(sched, dict):
            return {}
        result: dict = {}
        for key in ("blocklist_runs", "clickbait_runs"):
            if key in sched:
                result[key] = int(sched[key])
        if "headless" in sched:
            result["headless"] = bool(sched["headless"])
        return result
    except Exception:
        return {}


def load_selectors_config() -> dict:
    """Load selector overrides from the ``selectors:`` section of config.yaml.

    Returns a dict whose keys match ``_SELECTOR_DEFAULTS``.  Only keys
    present in the file are returned; missing keys fall back to the code
    defaults when merged by ``get_selectors()``.

    Returns an empty dict if config.yaml is absent, unparseable, pyyaml
    is not installed, or the ``selectors:`` section is missing.
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}
    try:
        data = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
        sels = data.get("selectors", {})
        if not isinstance(sels, dict):
            return {}
        # Only accept keys we know about.
        result: dict = {}
        for key, default_val in _SELECTOR_DEFAULTS.items():
            if key not in sels:
                continue
            val = sels[key]
            # Type-check: lists stay lists, strings stay strings.
            if isinstance(default_val, list):
                if isinstance(val, list):
                    result[key] = val
                elif isinstance(val, str):
                    result[key] = [val]  # convenience: single string → one-item list
                else:
                    continue  # skip invalid types silently
            else:
                if isinstance(val, str):
                    result[key] = val
                elif isinstance(val, list):
                    result[key] = ", ".join(str(v) for v in val)
                else:
                    continue
        # Warn about unrecognised keys.
        unknown = set(sels.keys()) - set(_SELECTOR_DEFAULTS.keys()) - {"selectors_updated_at"}
        if unknown:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "config.yaml selectors: unrecognised keys ignored: %s", ", ".join(sorted(unknown))
            )
        return result
    except Exception:
        return {}


def get_selectors() -> dict[str, str | list[str]]:
    """Return the merged selector config: code defaults + user overrides.

    Call this once at the start of a run and pass the result through to
    functions that need selectors.  Do NOT cache across runs — the user may
    edit config.yaml between runs.
    """
    merged = dict(_SELECTOR_DEFAULTS)
    overrides = load_selectors_config()
    if overrides:
        merged.update(overrides)
        import logging as _logging
        _logging.getLogger(__name__).info(
            "Selector overrides loaded from config.yaml: %s",
            ", ".join(sorted(overrides.keys())),
        )
    return merged


def write_selector_overrides(overrides: dict) -> None:
    """Merge selector overrides into config.yaml atomically.

    Only writes keys that differ from code defaults.  Adds a
    ``selectors_updated_at`` timestamp so staleness can be checked later.
    Requires pyyaml — raises ImportError if not installed.
    """
    from datetime import datetime, timezone

    import yaml  # type: ignore[import-untyped]

    cfg: dict = {}
    if CONFIG_FILE.exists():
        cfg = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    existing_sels = cfg.get("selectors", {})
    if not isinstance(existing_sels, dict):
        existing_sels = {}

    # Only write keys that actually differ from code defaults.
    for key, val in overrides.items():
        if key in _SELECTOR_DEFAULTS and val == _SELECTOR_DEFAULTS[key]:
            continue  # no need to override — matches the default
        existing_sels[key] = val
    existing_sels["selectors_updated_at"] = datetime.now(timezone.utc).isoformat()
    cfg["selectors"] = existing_sels

    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False), encoding="utf-8")
    tmp.rename(CONFIG_FILE)

    import logging as _logging
    _logging.getLogger(__name__).info(
        "Selector overrides written to %s: %s",
        CONFIG_FILE,
        ", ".join(sorted(k for k in overrides if k in existing_sels)),
    )


def _n(count: int, word: str) -> str:
    """Return '{count} {word}' with correct plural — e.g. _n(1,'channel') → '1 channel',
    _n(2,'channel') → '2 channels'. Works for all regular English nouns."""
    return f"{count} {word if count == 1 else word + 's'}"


def _escape_css_attr_value(s: str) -> str:
    r"""Escape a string for use inside a double-quoted CSS attribute value.

    Handles four characters that break a double-quoted CSS attribute value:

    * ``\\`` — backslash gets backslash-escaped per CSS Syntax Module
      Level 3 § 4.3.5 (must come first so the backslashes inserted by
      the later replacements are not re-doubled).
    * ``"`` — the matching quote character also gets backslash-escaped
      per § 4.3.5.
    * ``\n`` (LF, U+000A) — § 4.3.5 says an unescaped newline inside a
      quoted string produces a ``<bad-string-token>``: the string ends
      immediately and the rest of the selector becomes garbage. The
      correct replacement is the CSS hex-escape ``\A `` (backslash, hex
      digit, space terminator) per § 4.3.7. A naive port of
      ``_escape_applescript``'s ``\\n`` shape would NOT work: § 4.3.7
      says a backslash followed by a non-hex character resolves to the
      literal character, so ``\n`` inside a CSS string is a literal
      ``n``, not LF.
    * ``\r`` (CR, U+000D) — same § 4.3.5 ``<bad-string-token>`` problem;
      replaced with ``\D ``.

    ``\t`` is intentionally not escaped — § 4.3.5 only flags newline /
    carriage-return / form-feed as bad-string-tokens; horizontal tab
    inside a quoted string is a literal tab.

    Used by selectors that interpolate text harvested from the live page
    (channel display names, discovered aria-labels) so the resulting
    selector keeps parsing when those strings contain any of the four
    handled characters.

    Note: this helper assumes the CSS selector wraps the value in double
    quotes (e.g. ``[aria-label="..."]``). Single-quote-delimited callers
    are unsupported on purpose — both spots in this codebase use double
    quotes.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\A ")
        .replace("\r", "\\D ")
    )


# --- Logging Setup ---

def setup_logging(verbose: bool = False) -> None:
    ensure_data_dir()
    level = logging.DEBUG if verbose else logging.INFO
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            file_handler,
        ],
    )
    # Suppress request logs from httpx/httpcore (used internally by ollama)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

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

# Browser profile directory (persists login state between runs)
PROFILE_DIR = Path.home() / ".yt-dont-recommend" / "browser-profile"

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
try:
    from importlib.metadata import version as _pkg_version
    __version__: str = _pkg_version("yt-dont-recommend")
except Exception:
    __version__ = "0.0.0"  # fallback for editable installs without metadata
VERSION_CHECK_INTERVAL = 86400  # seconds between automatic checks (24 h)

# State schema version — bump this whenever the state file structure changes.
# Policy: only ADD new keys (never rename/remove/reinterpret existing ones).
# load_state() warns when it reads a state file written by a newer version.
STATE_VERSION = 2

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


def _n(count: int, word: str) -> str:
    """Return '{count} {word}' with correct plural — e.g. _n(1,'channel') → '1 channel',
    _n(2,'channel') → '2 channels'. Works for all regular English nouns."""
    return f"{count} {word if count == 1 else word + 's'}"


# --- Logging Setup ---

def setup_logging(verbose: bool = False) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
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

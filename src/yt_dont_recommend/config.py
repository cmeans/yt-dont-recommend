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

# Default personal exclusion list — loaded automatically if present
DEFAULT_EXCLUDE_FILE = Path.home() / ".yt-dont-recommend" / "exclude.txt"

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

# Attention flag file — written when something requires user action between runs
ATTENTION_FILE = Path.home() / ".yt-dont-recommend" / "needs-attention.txt"

# Version
__version__ = "0.2.2"
VERSION_CHECK_INTERVAL = 86400  # seconds between automatic checks (24 h)

# State schema version — bump this whenever the state file structure changes.
# Policy: only ADD new keys (never rename/remove/reinterpret existing ones).
# load_state() warns when it reads a state file written by a newer version.
STATE_VERSION = 1

# Set to True by write_attention() so main() can exit with code 1 when
# something serious enough to alert the user occurred during the run.
_had_attention = False

# Schedule management
_SCHEDULE_HOURS = (3, 15)  # 3:00 AM and 3:00 PM
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


# --- Logging Setup ---

def setup_logging(verbose: bool = False):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            file_handler,
        ],
    )

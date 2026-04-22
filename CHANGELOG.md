# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versions follow [Semantic Versioning](https://semver.org/).

> **Note on pre-0.4.0 entries.** `CHANGELOG.md` was introduced in v0.4.0 (see that release's entry). Entries for v0.1.2 through v0.3.5 were written retroactively from project memory rather than from `git log` and do not always line up tag-for-tag with the commits actually included in each release. For authoritative history on those releases, consult `git log vX.Y.Z-1..vX.Y.Z` directly. Entries from v0.4.0 onward are release-time and intended to be accurate.

## [Unreleased]

### Added

- **Test coverage push to 100% on pure-logic modules**: 188 new tests bring every pure-logic module (`__init__.py`, `blocklist.py`, `cli.py`, `clickbait.py`, `config.py`, `scheduler.py`, `state.py`) to 100% line coverage. Overall coverage rose from 44% to 64% — the gap is almost entirely `browser.py` / `diagnostics.py`, which are Playwright automation and are intentionally left at their natural (low) coverage. `pyproject.toml` now enforces `fail_under = 60` in `[tool.coverage.report]` as a regression guard, and `.coverage` / `coverage.xml` / `htmlcov/` are gitignored.
- **Codecov coverage reporting** in CI: `pytest-cov>=7.0` added as a dev dependency, `[tool.coverage.*]` configuration added to `pyproject.toml`, and a `codecov/codecov-action@v5` upload step now runs on the Ubuntu CI job. A Codecov badge is displayed in the README.
- README status badges (PyPI version, supported Python versions, license, CI, download count) and `.github/FUNDING.yml` for GitHub Sponsors — brings the project in line with the `cmeans/mcp-*` repo conventions.

### Changed

- **License changed from MIT to Apache-2.0.** Published versions `≤ 0.4.2` remain under MIT; this change applies to the next published version onward. The switch brings an explicit patent grant and aligns with the licensing used across the other `cmeans/mcp-*` repositories.

### Fixed

- **Scheduler catch-up spawn storm** (closes #17): after the host woke from sleep (or otherwise missed multiple consecutive scheduled slots), the heartbeat loop would spawn one full blocklist+clickbait run per missed slot back-to-back, resulting in three or more overlapping browser sessions fighting over the same Chromium profile. Stale missed slots are now coalesced into a single catch-up run.
- PyPI badges for Python versions and License now render correctly. `pyproject.toml` now declares Trove classifiers (Python 3.10–3.13, `License :: OSI Approved :: Apache Software License`) and a string license field, which is what `shields.io/pypi/pyversions` and `shields.io/pypi/l` read.

## [0.4.2] - 2026-03-19

### Added
- **Selector auto-repair**: `--check-selectors --repair` discovers working selectors when the built-in ones break and writes them to `config.yaml` automatically. Includes discovery heuristics for feed card container, channel links, menu buttons, and menu item text (including localized phrases).
- **Inline self-healing**: during normal `--blocklist` or `--clickbait` runs, if the selector health check detects 3 consecutive passes with no parseable channel links, the tool attempts inline repair before giving up — discovers working selectors, writes them to config, and resumes the scan.
- `write_selector_overrides()` function for atomic config.yaml updates.
- `discover_selectors()` function with heuristics for feed card, channel link, menu button, and menu phrase discovery.
- **Ruff linting**: integrated ruff for code quality enforcement. Added to CI pipeline (runs before tests on every push/PR). All existing issues fixed.
- Ruff config in `pyproject.toml` with per-file ignores for intentional patterns (late imports, re-exports).

### Changed
- **Data directory security**: `~/.yt-dont-recommend/` and the browser profile subdirectory are now created with mode `0o700` (owner-only), and existing installs with looser permissions are auto-tightened on startup. Browser cache subdirectories (Cache, Code Cache, GPUCache, Service Worker, and related) are cleared after every browser close — disk usage drops from ~470 MB to ~10 MB without affecting the persisted login session.
- **`--check-selectors` summary** now labels the three contexts where "Don't recommend channel" is not expected to appear (search results, channel header, video watch page) as "expected (no option)" instead of "FAIL", so the report no longer looks alarming when the tool is working correctly. Only the home-feed test produces a real pass/fail.

### Fixed
- Removed unused imports (`VIDEO_SELECTORS`, `MENU_BTN_SELECTORS`, `MENU_ITEM_SELECTOR`, `TARGET_PHRASES`) left over from the selector registry refactor in browser.py.
- Fixed ambiguous variable names (`l` → `line`) in scheduler.py.
- Fixed f-strings with no placeholders, unused test variables, unsorted imports.

## [0.4.1] - 2026-03-19

### Fixed
- **Batch JSON parse reliability**: strip trailing commas (`,]`, `,}`) and invalid escape sequences (`\'`, `\d`, `\s`, etc.) from LLM responses before parsing. Eliminates batch parse failures that previously required individual-item fallback.
- **Title classification timeout**: default increased from 300s to 600s. Individual fallback path (`classify_title`) now reads timeout from config instead of using hardcoded 90s.
- Claude Code collaboration note added to README Acknowledgments.

## [0.4.0] - 2026-03-19

### Added
- **Configurable selector registry**: all CSS selectors and text phrases used for YouTube DOM interaction are now overridable via the `selectors:` section in `config.yaml`. Enables users to fix selector breakage without waiting for a code update, and supports non-English YouTube via localized menu text phrases (`dont_recommend_phrases`, `not_interested_phrase`).
- `get_selectors()` function merges code defaults with user overrides from config.
- `config.example.yaml` documents all overridable selector keys.
- **CHANGELOG.md** introduced at the project root.

### Fixed
- First feed pass summary (`Pass: N cards, M with channel links`) is now always logged, even in blocklist-only mode with no matches. Provides selector health proof in every run's log.
- **Clickbait classification accuracy and reliability** (PR #5): tightened few-shot examples, added prefilters for known-safe title patterns, and rewrote the prompt to reduce false positives on news, music, science, and movie clip titles.
- **Feed pipeline correctness** (PR #6): UCxxx-to-handle upgrade, stale card handling, and `/channel/UCxxx` normalization so channels appearing in the feed under either handle form are matched consistently.
- **Log clarity** (PR #7): removed duplicate dry-run lines, truncated Ollama response bodies in debug output, and trimmed per-card debug noise.
- **UCxxx upgrade fallback and cache-hit logging** (PR #8): if the handle resolution network call fails, the tool now falls back cleanly instead of aborting the scan, and cache-hit paths log so you can confirm the resolver is short-circuiting correctly.

## [0.3.5] - 2026-03-12

### Added
- **Batch clickbait classification** (PR #3): titles are sent to the LLM in batches of 10 instead of one at a time (~5x throughput improvement).
- **Cross-run classification cache** (PR #4): video IDs are cached in state for 14 days, skipping re-evaluation on subsequent runs.
- **Shadow-limit detection** (PR #4): if YouTube keeps showing videos the tool already marked "Not interested", it warns and stops (possible account-level rate limiting).
- **Heartbeat gate** (PR #4): `--heartbeat` skips spawning a run if the previous one is still alive (PID check).
- **State schema v3** (PR #4): adds `clickbait_cache` and `clickbait_acted` keys to support the cross-run cache and shadow-limit detection; `STATE_VERSION` bumped from 2 to 3.
- **JSON feed extraction** (PR #2): video titles and channel handles extracted from `window.ytInitialData` JSON and continuation responses, replacing fragile DOM scraping for initial page load and scrolled content.
- Continuation response interception via `page.on("response")` for live JSON updates during scroll.
- `lockupViewModel` support (YouTube 2026+ schema) with `videoRenderer` fallback.

### Fixed
- Batch classification timeout increased from 300s to 600s default.

## [0.3.4] - 2026-03-11

### Added
- **Smart scheduler**: per-minute heartbeat via cron (Linux) or launchd (macOS) that fires runs at randomized UTC times each day.
- Per-mode run frequency: `--blocklist-runs N` and `--clickbait-runs N` control how many times per day each mode runs.
- `schedule.json` (separate from `state.json`) tracks planned/executed times per UTC day.
- `--schedule install|remove|status` and `--heartbeat` CLI commands.

## [0.3.3] - 2026-03-10

### Added
- `ytInitialData` JSON extraction for clickbait title source (more reliable than DOM `title` attribute).
- Smoke test coverage for clickbait extras installation.

### Fixed
- Dry-run label inconsistency.
- `UnboundLocalError` in edge case.

## [0.3.2] - 2026-03-09

### Fixed
- Inject real Chrome version as User-Agent into bundled Chromium, avoiding the `--no-sandbox` warning that Flatpak Chrome requires when launched directly.

## [0.3.1] - 2026-03-09

### Added
- Flatpak, Snap, and RPM Chrome/Chromium detection for UA sourcing.
- UA string logged at startup for debugging.
- Blocklist mode announcement in output.

### Fixed
- Dry-run output now says "would scan" instead of "added to scan queue".

## [0.3.0] - 2026-03-08

### Added
- **System Chrome UA preference**: bundled Chromium now uses the real Chrome version string from the system install for an authentic User-Agent and Client Hints.
- `browser.use_system_chrome` config option (default: `true`).

## [0.2.9] - 2026-03-08

### Added
- `auto_pull` config option for clickbait Ollama models (pulls the configured model automatically when missing).
- Per-pass DEBUG summary for the clickbait scan.

### Changed
- **State schema v2**: removed the redundant `state["processed"]` list; `blocked_by.keys()` is now the single source of truth for which channels have been handled. `load_state()` migrates older files in place and logs the migration.
- Single-source version resolution via `importlib.metadata` with a fallback for editable installs; `plistlib` import moved to lazy so non-macOS runs avoid the cost.
- First-run message now includes a YouTube Terms of Service note.

### Fixed
- Login detection false negative.
- `--no-limit` flag was broken (the cap still applied).
- Scroll jitter was not randomized.
- Asymmetric jitter windows corrected.
- Noqa placement fixes in `browser.py`.
- Clickbait prompt improvements.

## [0.2.8] - 2026-03-07

### Added
- **Stealth hardening**: viewport randomization from pool of common desktop resolutions, `navigator.webdriver` property stripped, per-session action cap (default 75, `--no-limit` to remove).
- All interaction timing configurable via `~/.yt-dont-recommend/config.yaml` (`timing:` section).

### Fixed
- `check_attention_flag()` blocked read-only commands on tty.
- All fixed `time.sleep()` calls replaced with `random.uniform()` jitter.
- Browser module split into `browser.py`, `unblock.py`, `diagnostics.py`.

## [0.2.7] - 2026-03-07

### Added
- Feed coverage metric in `--stats` output.
- `--clickbait` schedule gap documented in README and scheduler output.
- Complete type annotations on all function signatures.

## [0.2.6] - 2026-03-07

### Changed
- Internal refactoring: TypedDict state schema, CLI dispatch extracted to `cli.py`.

## [0.2.5] - 2026-03-06

### Added
- Split exclusion files: `--exclude` for blocklist, `--clickbait-exclude` for clickbait.
- Named loggers per module.

### Fixed
- Clickbait detection improvements.

## [0.2.4] - 2026-03-06

### Fixed
- `--clickbait`-only feed scan was bypassed by second early-exit guard.

## [0.2.3] - 2026-03-06

### Fixed
- `--clickbait`-only mode showed "Nothing to do" instead of scanning.
- Config file created on first run if absent.

## [0.2.2] - 2026-03-06

### Fixed
- `--schedule status` now shows the scheduled command.
- Warning added when schedule is installed without `--blocklist`.

## [0.2.1] - 2026-03-05

### Added
- `--blocklist` flag (required to enable blocklist mode).
- Smoke test script (`scripts/smoke-test.sh`).

### Fixed
- `--clickbait` action (marking "Not interested") was not wired up.

## [0.2.0] - 2026-03-05

### Added
- **Clickbait detection**: `--clickbait` flag scans feed videos for clickbait titles using local LLM (Ollama). Marks detected clickbait as "Not interested" (video-level, no channel effect).
- Multi-stage pipeline: title classification (always) -> thumbnail (optional) -> transcript (optional).
- Configurable via `~/.yt-dont-recommend/clickbait-config.yaml`.
- Optional dependencies: `pip install yt-dont-recommend[clickbait]`.

## [0.1.27] - 2026-03-04

### Fixed
- Misleading channel count in processing banner.

## [0.1.26] - 2026-03-04

### Added
- Separator log line between source loading and processing phases.

## [0.1.25] - 2026-03-04

### Changed
- Single combined feed scan across all sources (previously scanned per-source).

## [0.1.24] - 2026-03-04

### Added
- Shared browser session across all processing (single login verification per run).
- ntfy.sh debug logging.

### Fixed
- Spurious unblock alert when no channels needed unblocking.

## [0.1.23] - 2026-03-04

### Fixed
- Spurious attention alert on empty pending-unblock queue.
- Unblock failure messages now name the specific channels.

## [0.1.22] - 2026-03-03

### Changed
- **Package restructured into `src/yt_dont_recommend/` layout** (PR #1 and follow-up commit `Complete src layout`): state, blocklist, and scheduler code extracted into dedicated modules, tests split accordingly. `test_yt_dont_recommend.py` was replaced by per-module test files (`test_state.py`, `test_blocklist.py`, `test_scheduler.py`, etc.). No user-visible behavior change — preparatory groundwork for the later `clickbait.py`, `browser.py`, `unblock.py`, and `diagnostics.py` splits.

### Fixed
- Multiple pending-unblock bugs: retry tracking, display-name lookup failures, double verification, infinite retry loop.
- State clobbering in `_perform_browser_unblocks`.
- Missing attention alerts on unblock failures.
- CI: generate `README-pypi.md` before the package install step so the publish job doesn't see a stale README.

## [0.1.21] - 2026-03-03

### Fixed
- `previous_version` was clobbered with `None` on first run.

## [0.1.20] - 2026-03-03

### Fixed
- TOC stripped from PyPI README rendering.
- Exit code 1 on attention-level failures.

## [0.1.19] - 2026-03-03

### Fixed
- Three bugs found in code review.

## [0.1.18] - 2026-03-03

### Added
- `state_version` guard: warns when state file was written by a newer binary (e.g. after `--revert`).
- State schema policy documented.

## [0.1.17] - 2026-03-02

### Fixed
- Subscription list fetched twice per run.
- Noisy exclude file logging.
- Misleading feed exhaustion message.

## [0.1.16] - 2026-03-02

### Added
- `--schedule-hours` with step/hourly format support.

## [0.1.15] - 2026-03-02

### Added
- `--revert [VERSION]` to roll back to a previous version.

## [0.1.13] - 2026-03-01

### Added
- Per-source stats in `--stats` output.
- Blocklist growth notification when a source has grown since last run.
- `--export-state` to dump blocked channels as a plain-text blocklist.

## [0.1.12] - 2026-03-01

### Fixed
- Stealth improvements and user-agent versioning fix.

## [0.1.11] - 2026-02-28

### Added
- Attention notifications on unblock selector failure and auto-upgrade failure.

## [0.1.10] - 2026-02-28

### Added
- Attention notification on expired login session.

## [0.1.9] - 2026-02-28

### Fixed
- `--revert` failed because version tracking ran after early-return commands.

## [0.1.8] - 2026-02-28

### Fixed
- README: clarified `--revert` works for manual upgrades too.

## [0.1.7] - 2026-02-27

### Added
- Desktop notifications via `notify-send` (Linux) and `osascript` (macOS).
- `--setup-notify` / `--remove-notify` / `--test-notify` for ntfy.sh push notifications.
- `--auto-upgrade enable|disable` and `--check-update`.

## [0.1.6] - 2026-02-27

### Added
- Auto-unblock: channels removed from blocklists are automatically unblocked via myactivity.
- `--unblock-policy {all,any}` flag.

## [0.1.5] - 2026-02-26

### Added
- Subscription protection: subscribed channels are never blocked.
- `--stats` and `--reset-state` commands.

## [0.1.4] - 2026-02-26

### Added
- `--exclude` flag for channels to never block.
- AiSList as second built-in source.

## [0.1.3] - 2026-02-25

### Fixed
- `pyproject.toml` packaging fix.

## [0.1.2] - 2026-02-25

### Added
- Initial public release.
- Home feed scanner with "Don't recommend channel" automation.
- DeSlop built-in blocklist source.
- Persistent browser profile (login once, reuse session).
- Crash-safe state tracking.
- `--login`, `--dry-run`, `--headless`, `--source`, `--check-selectors` commands.

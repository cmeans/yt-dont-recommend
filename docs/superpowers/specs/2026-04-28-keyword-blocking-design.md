# Keyword blocking — design

**Status:** draft 2026-04-28
**Origin:** auto-memory `project_keyword_blocking.md` (captured ~2026-03-14, pre-#58); revalidated 2026-04-28 against current code (main at `0ad0456`).
**Scope:** PR A only (core feature). PR B (scheduler integration) is intentionally deferred and tracked in §10.
**State version:** bump 4 → 5.

## 1. Summary

Add a `--keyword-block` mode that scans video titles in the YouTube home feed against a user-defined keyword list and clicks "Not interested" on matches. Independent of `--clickbait` (no LLM dependency); independent of `--blocklist` (channel-level). Three matching tiers — substring, word-boundary, regex — selectable per-line via prefix. Case-insensitive by default; the `regex:` tier doubles as the case-sensitivity escape hatch via `(?-i)` flags. New module `src/yt_dont_recommend/keywords.py`; new state keys `keyword_acted` and `keyword_stats`; three new CLI flags.

The feature is demand-driven: a user wants to filter all videos about a topic ("Star Trek", "Trump") regardless of which channel posted them. The existing channel-level `--blocklist` and the LLM-based `--clickbait` both miss this case.

## 2. Architecture & module layout

New module `src/yt_dont_recommend/keywords.py`, peer of `blocklist.py` and `clickbait.py`. Pure-logic, stdlib-only. Imports only from `config.py`. Uses the same `_pkg()` late-binding pattern that `clickbait.py` uses so tests can patch `yt_dont_recommend.X` symbols.

**Public API** (re-exported from `__init__.py`):

| Function | Purpose |
|---|---|
| `resolve_keyword_source(source) -> str` | Local file path or HTTPS URL. Mirrors `blocklist.resolve_source` but with no built-in named sources. |
| `parse_keyword_file(text) -> list[tuple[int, str]]` | One entry per non-comment, non-blank line. Returns `(line_number, raw_pattern)` pairs so line numbers survive into compiled rules. |
| `compile_keywords(raw) -> list[CompiledKeyword]` | Compiles each entry into its tier's runtime form. Bad regex tier entries log a single warning and are dropped from the returned list. |
| `match_title(title, compiled) -> MatchResult \| None` | First-match-wins iteration over compiled rules. Returns `(pattern, mode, line)` on hit, `None` on miss. |
| `load_keyword_excludes(path) -> set[str]` | Mirrors `_load_blocklist_exclude_set` in `cli.py`. Canonicalizes `@handle` / `UCxxx` entries via the existing `blocklist._canonicalize_channel` validator. |

**Data types** (small, in-module):

```python
@dataclass(frozen=True)
class CompiledKeyword:
    pattern: str           # original text (for stats reporting)
    mode: str              # "substring" | "word" | "regex"
    line: int              # 1-indexed source line
    matcher: object        # tier-specific compiled form (re.Pattern, or lowered str)

class MatchResult(NamedTuple):
    pattern: str
    mode: str
    line: int
```

No new third-party dependencies in `pyproject.toml`. The module sits at the same architectural tier as `clickbait.py`: video-level, opt-in, pure-logic classifier consumed by the Playwright loop in `browser.py`. `blocklist.py` (channel-level, always-on under `--blocklist`) is a different tier; `keywords.py` does not share code with it.

## 3. Configuration formats

### `~/.yt-dont-recommend/keyword-block.txt`

Plain text. One rule per line. `#` introduces a line comment. Blank lines ignored. Three tiers selected by line-prefix:

```
# Substring (default) — case-insensitive
Trump
Star Trek

# Word-boundary — won't match "trekking" or "Trumpian"
word:trek
word:trump

# Regex — full re.IGNORECASE by default
regex:\b(rfk|kennedy)\b
regex:^\d+ reasons?
```

Prefix detection is `startswith("word:")` / `startswith("regex:")`. Anything not prefix-matched is substring. The leading `word:` / `regex:` is stripped before compilation.

A new `keyword-block.example.txt` ships at the repo root, mirroring `clickbait-config.example.yaml`.

### `~/.yt-dont-recommend/keyword-exclude.txt`

Exact mirror of `blocklist-exclude.txt`. One channel handle per line (`@handle` or `UCxxx`). `#` comments. The existing `blocklist._canonicalize_channel` validator gates entries. Auto-loaded if present at the default path; explicit `--keyword-exclude PATH` overrides and *requires* the path to exist (mirrors how `--exclude` behaves today).

### `config.yaml` additions

None for v1. Match behavior is fully determined by per-line prefixes in the rule file. No batching, no tuning surface.

## 4. Matching engine

**Case sensitivity:** all three tiers are case-insensitive by default.

| Tier | Implementation |
|---|---|
| substring | `pattern.lower() in title.lower()` |
| word | `re.search(rf"\b{re.escape(pattern)}\b", title, re.IGNORECASE)` |
| regex | `re.compile(pattern, re.IGNORECASE)` then `.search(title)` |

Power users override case via `(?-i)` inline flags inside a `regex:` rule. No separate `case:` prefix — the regex tier is the escape hatch.

**Compile-time validation:** `compile_keywords` calls `re.compile()` once per `regex:` entry up front. Bad patterns log `WARN: keyword line N: invalid regex '<pattern>': <error>` and are skipped. The run continues with the valid rules. No fail-the-run-on-bad-regex; that would be hostile to keyword-list maintainers who don't realize they typoed something.

**First-match-wins:** `match_title` iterates compiled rules in source-file order and returns the first hit. The hit's `(pattern, mode, line)` is recorded in state for `--stats` reporting. This makes rule ordering meaningful (put narrower rules above broader ones if you want them credited specifically), and it caps per-card cost at a small number of regex evaluations.

**Empty rule file:** treated as "no rules". Logs `INFO: keyword-block file is empty, no keyword matching active` and the run continues. Non-fatal because the same invocation may also have `--blocklist` enabled and that mode is unaffected.

**Performance:** sub-millisecond per card on a typical home feed. No batching, caching, or precompiled-pool optimization needed.

## 5. State schema

Two new top-level keys, both `dict` defaulting to `{}`:

```python
state["keyword_acted"]: dict[video_id, {
    "acted_at": "2026-04-28T...",
    "title": "...",
    "channel": "@handle",
    "matched_pattern": "Star Trek",
    "matched_mode": "substring" | "word" | "regex",
    "matched_line": 7,
}]

state["keyword_stats"]: {
    "total_matched": int,                       # cumulative across runs
    "by_pattern": dict[str, int],               # cumulative hits per literal pattern
    "by_mode": {"substring": int, "word": int, "regex": int},
}
```

`keyword_acted` is pruned at 90 days on load — a new module-level `KEYWORD_ACTED_PRUNE_DAYS = 90` constant in `state.py`, mirroring `CLICKBAIT_ACTED_PRUNE_DAYS`. `keyword_stats` is permanent (cumulative counts only; no per-entry data, so no privacy-relevant retention).

### Shadow-limit detection extension

The existing shadow-limiting check in `clickbait.py` (`SHADOW_LIMIT_GRACE_HOURS` / `SHADOW_LIMIT_WARN_AFTER`) currently only consults `clickbait_acted`. It is extended to consult the **union** of `clickbait_acted | keyword_acted` because a video re-encountered after either action is the same diagnostic signal — YouTube has not honored our "Not interested" click in either case.

A new helper `_acted_video_ids(state) -> set[str]` is added to `state.py` (the natural home for state-shape utilities; keeps `clickbait.py` from importing keyword-mode internals). `clickbait.py`'s shadow-limit check imports and calls it; `cli.py`'s `--stats` does the same. PR A modifies `clickbait.py` to consume the helper instead of reading `state["clickbait_acted"]` directly — a single-line change at the shadow-limit call site, plus an updated test.

### `AppState` TypedDict additions

Per State Schema Policy step 7 in CLAUDE.md, the `AppState` TypedDict in `state.py` adds:

```python
keyword_acted: dict[str, dict]
keyword_stats: dict[str, int | dict[str, int]]
```

### State Schema Policy compliance (full 7-step checklist)

1. **Add new keys, do not rename or remove existing ones** — yes; `keyword_acted` and `keyword_stats` are net-new.
2. **`setdefault` in `load_state()`** — adds two `setdefault` lines.
3. **Add to fresh-state `return` dict** — both keys present in the bottom-of-`load_state` literal.
4. **Bump `STATE_VERSION`** — 4 → 5 in `config.py`.
5. **Update State Schema doc block in CLAUDE.md** — adds a "v5 additions" sub-bullet describing both keys, their TTL, and shadow-limit union behavior.
6. **Add tests covering default values** — covered in `test_state.py` additions (§9).
7. **Declare on `AppState` TypedDict** — see above.

## 6. CLI surface

Three new flags in `cli.py`:

| Flag | Behavior |
|---|---|
| `--keyword-block` | Required to enable keyword mode (same gate as `--blocklist` / `--clickbait`). Invoked alone with no other mode shows help. |
| `--keyword-source PATH-OR-URL` | Optional. Defaults to `~/.yt-dont-recommend/keyword-block.txt`. Local path or `https://` URL (no built-in named sources). `http://` rejected with the same error as `resolve_source`. Missing file = exit 1 with explanatory message. |
| `--keyword-exclude PATH-OR-URL` | Optional. Defaults to auto-load `~/.yt-dont-recommend/keyword-exclude.txt` (silent if absent). Explicit `--keyword-exclude PATH` requires the path to exist. |

**Composition with existing flags:**

- `--keyword-block --blocklist --clickbait` — all three modes in one feed scan, single browser session, single subscription fetch.
- `--keyword-block --dry-run` — logs `WOULD MATCH: <title> (line N: <pattern>)` without acting; no state writes.
- `--limit N` — keyword acts count toward the per-session cap alongside blocked + clickbait counts.
- `--no-limit` — removes the cap as today.

**`--stats` integration:** adds a "Keyword matches" section showing total acted, top 10 patterns by hit count, and patterns/lines that have never fired (helps the user prune dead rules).

**`--export-state` integration:** out of scope. The export format is for channel handles; keyword-acted videos are video-level and have no equivalent representation. Skipped without comment.

**Subscription protection (option A — locked-in decision):** `--keyword-block` acts on subscribed channels' videos that match keywords. Topic preference wins over the channel subscription signal. Users who want exceptions add the channel to `--keyword-exclude`. Rationale: the user has explicitly opted into "block this topic"; the keyword filter is fundamentally about topic avoidance, not channel trust. No new `would_have_keyword_acted` state field needed.

## 7. Pipeline integration

In `process_channels` (browser.py), the per-card flow becomes:

1. **Phase 1 — Channel-level blocklist** (existing): if card's channel matches `channel_sources`, click "Don't recommend channel" and continue. Subscription protection applies here.
2. **Phase 2 — Subscription gate for video-level actions** (existing): if card's channel is in subscriptions and we are not in keyword mode, skip remaining phases. Keyword mode bypasses this gate per the option-A decision in §6.
3. **Phase 3 — Keyword match (NEW):** if `keyword_block` is enabled, video_id is not in `keyword_acted`, and channel is not in `keyword_excludes`, run `match_title`. On hit: click "Not interested", record in `keyword_acted` + `keyword_stats`, continue. On miss: fall through to Phase 4.
4. **Phase 4 — Clickbait classification** (existing, lightly extended): runs only if no keyword match. The existing `_clickbait_evaluated` set is replaced by the union helper from §5 so a card already keyword-acted in this same scan does not get queued for clickbait too.

`process_channels` gains two new optional kwargs: `keyword_compiled: list[CompiledKeyword] | None = None` and `keyword_excludes: set[str] | None = None`. CLI builds and passes them in `main()`. Both `None` disables keyword mode entirely; existing callers and tests keep working unchanged.

The card-loop "scan description" log line picks up `+ keyword detection` when keyword mode is active, matching the existing `+ clickbait detection` pattern.

## 8. Error handling

| Failure | Behavior |
|---|---|
| `--keyword-source` path missing | `ERROR: keyword source not found: <path>` → exit 1 (matches `--source` blocklist behavior). |
| `--keyword-source` URL fetch fails | Same as `resolve_source` URL fetch failure → exit 1. |
| `--keyword-exclude` default file missing | Silent (auto-load is best-effort, like `blocklist-exclude.txt`). |
| `--keyword-exclude` explicit path missing | `ERROR: keyword exclude path not found: <path>` → exit 1. |
| Bad regex compilation | `WARN: keyword line N: invalid regex '<pattern>': <error>` → drop that rule, continue with valid rules. |
| Empty / all-comments file | `INFO: keyword-block file is empty, no keyword matching active` → run continues (non-fatal; user might still have `--blocklist` enabled). |
| `http://` keyword source URL | `ERROR: insecure http:// not allowed` → exit 1 (matches PR #48 hardening). |
| Click "Not interested" Playwright failure | Existing failure path: log `WARN`, increment `total_failed`, continue scrolling. No state write for that video — it is retried next run if still in the feed. |

## 9. Testing strategy

### `tests/test_keywords.py` (new, ~25–35 tests)

Pure-logic, no Playwright.

- `parse_keyword_file` — comments, blanks, mixed line endings, BOM, trailing whitespace, line numbers preserved.
- `compile_keywords` — substring tier; `word:` tier; `regex:` tier; bad regex dropped with warning; valid + invalid mix; empty file.
- `match_title` — first-match-wins ordering; case-insensitivity across all three tiers; word-boundary edge cases (`trek` vs `trekking` vs `Star Trek`); regex `(?-i)` override; empty title; empty rules; unicode title.
- `resolve_keyword_source` — local path, HTTPS URL, `http://` rejected, missing file, URL fetch failure.
- `load_keyword_excludes` — handle canonicalization, comments, empty file, file missing returns empty set silently.

### `tests/test_state.py` additions (~6–8 tests)

- v4→v5 migration sets defaults for both new keys.
- `keyword_acted` 90-day pruning preserves recent entries and drops stale.
- `keyword_stats` permanent counts not pruned.
- `AppState` TypedDict carries new fields (mypy-style structural test, mirroring existing pattern).
- Backward-compat: state file written by a v5 binary loadable by a v4 binary (unknown keys ignored, only the `state_version > STATE_VERSION` warning fires).

### `tests/test_cli.py` additions (~10–15 tests)

- `--keyword-block` alone (no other mode) shows help.
- `--keyword-block` + `--source` ignored (keyword has its own `--keyword-source`); no warning, just no effect.
- `--keyword-block --dry-run` doesn't write state.
- `--keyword-block --blocklist --clickbait` composes — single feed scan, single subscription fetch.
- `--stats` shows keyword section with totals and top patterns.
- `http://` keyword source rejected.
- `--keyword-exclude` default path auto-loaded; explicit missing path errors.
- `--limit N` counts keyword acts toward the cap.

### `tests/test_browser.py` additions (~3–5 tests, mocked Playwright)

- `process_channels` with `keyword_compiled` set fires Phase 3 before Phase 4.
- Subscription protection bypass for keyword mode (option A) — subscribed channel's video is keyword-acted on.
- Shadow-limit union check sees both keyword-acted and clickbait-acted videos.

### Smoke test (`scripts/smoke-test.sh`)

One new invocation: `yt-dont-recommend --keyword-block --dry-run --keyword-source <fixture>` exits 0 with the expected log line. Fixture is checked in under `tests/fixtures/` (small, ~5 entries covering all three tiers).

## 10. Out of scope / deferred

The following are explicitly **not** in PR A:

| Item | Rationale | Future home |
|---|---|---|
| `--keyword-runs N` flag and scheduler integration (`heartbeat`, `--schedule install`, `schedule.json` `modes.keyword` key) | Self-contained automation layer; deserves its own QA cycle. | PR B (scheduler). |
| Standalone `--keyword-stats` subcommand | `--stats` integration covers it; YAGNI. | Add only if `--stats` integration proves insufficient in practice. |
| Transcript-keyword matching | Per-card network fetch is too slow; would force batching architecture. | Future, gated on demand and possibly a transcript-cache key. |
| Cross-run keyword cache | Matching is sub-millisecond and idempotency is enough; cache would be all cost no benefit. | Not planned. |
| YAML config schema for the keyword file | Rejected at brainstorming Approach 1 — breaks "drop it in a gist" shareability. | Not planned. |
| `--keyword-stats`-only mode that re-evaluates against existing logs | YAGNI, no demonstrated need. | Not planned. |

## 11. Documentation deltas

In PR A:

- **CLAUDE.md** — new `### Keyword blocking` subsection under Architecture; new "v5 additions" entry under State Schema; new `--keyword-*` rows in the CLI flags table; entry in the Modules list under Architecture.
- **README.md** — new "Keyword filtering" section under Usage with the example file format and a brief mention of the three tiers; one badge line in feature list (no new badge image).
- **CHANGELOG.md** — new `[Unreleased] / Added` entry summarizing the feature.
- **`keyword-block.example.txt`** — new file at repo root, ~10 lines covering all three tiers with comments.

## 12. Rejected alternatives

For posterity. Each is recorded so a future reader can see why we did not pick it.

| Considered | Why rejected |
|---|---|
| Substring-only MVP | Three tiers cost ~50 extra lines and ~10 extra tests; users hit precision limits within days. |
| YAML keyword config | Breaks the "simple text, share via gist" pitch; over-engineered for a feature where 95% of entries will be one word. |
| Reuse `--clickbait-exclude` for keyword exclusions | User explicitly redirected at brainstorming step — independent control with separate file is the cleaner mental model. |
| Skip keyword acts on subscribed channels (option B) | Topic preference is the user's primary signal; subscription as an override creates a confusing exception class. Fixable by user putting the channel on `--keyword-exclude`. |
| Per-line `case:` prefix for case-sensitive substring/word | The `regex:` tier with `(?-i)` is the existing escape hatch; another prefix is redundant. |
| Embed keyword matching as a clickbait classifier stage | Conflates two distinct opt-in modes; would force `--keyword-block` to imply `--clickbait`'s LLM dependency. |
| Two-pass scan (collect candidates, then act) | Inline matching is instant; two-pass adds complexity for no measurable benefit on a feed of dozens-to-hundreds of cards. |

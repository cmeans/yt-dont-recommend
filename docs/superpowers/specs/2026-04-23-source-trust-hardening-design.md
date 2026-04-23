# Source-trust hardening — design

**Status:** draft 2026-04-23
**Issues:** [#41](https://github.com/cmeans/yt-dont-recommend/issues/41), [#42](https://github.com/cmeans/yt-dont-recommend/issues/42)
**Priority:** P2
**Scope:** bundled PR, two commits (channel validation, then http:// rejection)
**Follow-ups (out of scope here):** #43 auto-upgrade gate, #44 atomic save_state, #46 selector f-strings

## Problem

Two P2 findings from the 2026-04-23 security review, treated as one hardening pass because they share adversarial-string fixtures and both live in `blocklist.py`:

### #41 — channel identifiers unvalidated after parse

`parse_text_blocklist` and `parse_json_blocklist` (`src/yt_dont_recommend/blocklist.py:42-117`) strip comments and common prefixes but don't validate the remaining string. Whatever passes through becomes a "canonical" identifier consumed by `unblock.py`, `diagnostics.py`, and the write-attention path that was fixed in #40 at the output boundary. Defense in depth: reject malformed entries at parse time so no sink receives content that can break a URL, selector, or notification script.

### #42 — `resolve_source` accepts `http://` silently

`resolve_source` (line 153) treats `http://` and `https://` as equals. An MITM on a plaintext source can swap the entire blocklist content. Combined with any sink-side weakness, this is a full RCE path. The built-in sources are both `https://` so default usage is unaffected; this is a footgun for user-added sources.

## Solution

### #41 — parse-time validation

New private helper in `blocklist.py`:

```python
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]+$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

def _canonicalize_channel(raw: str) -> str | None:
    s = raw.strip()
    if _HANDLE_RE.match(s) or _CHANNEL_ID_RE.match(s):
        return s
    return None
```

Both `parse_text_blocklist` and `parse_json_blocklist` run every candidate through `_canonicalize_channel` as the final step. Invalid entries are dropped. Each parser logs a single `log.warning("Dropped N invalid channel entries")` if any dropped — one line per parse call, not per entry.

**Why `@[A-Za-z0-9._-]+`:** YouTube handles are ASCII alphanumerics plus dot, underscore, and hyphen. `+` (not `{3,30}`) because YouTube's handle length limits have drifted over time and the goal is structural validation, not length enforcement.

**Why `UC[A-Za-z0-9_-]{22}`:** YouTube channel IDs are exactly `UC` followed by 22 base64url-ish characters. Fixed length is structural.

**What this closes:**
- Newlines, quotes, backslashes, and control characters in channel strings (reachable via malicious blocklist)
- Path traversal attempts (`@../foo`, `/etc/passwd`)
- URL-shape injections inside a channel field
- Empty or whitespace-only entries
- Entries containing `?` or `&` that could alter `page.goto` URL semantics

### #42 — http:// rejection

Modify `resolve_source` to:

```python
if source.startswith("http://"):
    log.error(
        "Refusing insecure http:// source; use https:// instead. "
        "If you need a local override, serve the file locally and use a file path."
    )
    sys.exit(1)

if source.startswith("https://"):
    # existing behavior unchanged
    ...
```

No env var override, no `--allow-insecure-source` flag. Per the review's recommendation: "If anyone legitimately needs http://, they can serve it over a local tunnel and point at that" — i.e. use a local file path, which the code path already supports.

No changes to `fetch_remote` — `urllib` already refuses cross-scheme redirects to `file://` / `ftp://` by default.

## Testing

### `tests/test_blocklist.py` — new `TestCanonicalizeChannel`

Pure-function coverage of the validator:

- `test_valid_handle` / `test_valid_channel_id` — happy path
- `test_handle_with_dot`, `test_handle_with_underscore`, `test_handle_with_hyphen`
- `test_uc_id_wrong_length_rejected` (21 and 23 chars)
- `test_uc_id_wrong_prefix_rejected` (e.g. `UX…`, `uc…`)
- `test_handle_without_at_sign_rejected`
- `test_empty_string_rejected`
- `test_whitespace_only_rejected`
- Adversarial inputs (one test each, all return `None`):
  - `@evil"; do shell script "…`
  - `@foo\nbar`
  - `@../etc/passwd`
  - `@foo?bar=baz`
  - `@foo/path`
  - `@foo ` (trailing whitespace — trimmed, should still pass after strip — verify)

### `tests/test_blocklist.py` — extend existing parse tests

- `test_parse_text_blocklist_drops_invalid_entries` — mixed valid + adversarial; output contains only valid; asserts a WARN log line with "Dropped N"
- `test_parse_json_blocklist_drops_invalid_entries` — same shape for JSON
- `test_parse_text_blocklist_logs_nothing_when_all_valid` — no WARN when nothing dropped

### `tests/test_blocklist.py` — extend `resolve_source` tests

- `test_resolve_source_rejects_http` — `http://example.com/list.txt` raises `SystemExit(1)` with an error log
- `test_resolve_source_accepts_https` — existing path still works (regression guard)

## File structure

- **Modify** `src/yt_dont_recommend/blocklist.py` — add `_HANDLE_RE`, `_CHANNEL_ID_RE`, `_canonicalize_channel`; wire into both parsers; reject `http://` in `resolve_source`.
- **Modify** `tests/test_blocklist.py` — add `TestCanonicalizeChannel`, extend existing parse and resolve tests.
- **Modify** `CHANGELOG.md` — two entries under `[Unreleased] > Security`.
- **No other files.**

## Commit shape

Two commits on `fix/source-trust-41-42`:

1. `fix(blocklist): validate channel identifiers at parse time (#41)` — helper + parser wiring + tests
2. `fix(blocklist): reject insecure http:// sources in resolve_source (#42)` — single-call-site change + tests

Then a docs commit for CHANGELOG.

## Non-goals for this PR

- Schema-level validation inside JSON dicts (stays as-is; only the final channel string is validated)
- Rate limiting log spam beyond the per-call summary already specified
- Reworking `_pkg()` late-import pattern (out of scope)
- README documentation of the trust model (separate concern; noted in #43)
- Length limits on individual handles/ids (structural validation only)

## Verification

- Full pytest suite passes (new tests + 459 existing → expected 459 + ~18 new)
- Ruff clean
- `TestResolveSource::test_resolve_source_rejects_http` exercises the actual exit path
- `TestCanonicalizeChannel` covers the PoC from #40 and the input shapes that would have broken selectors in #46

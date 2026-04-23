# AppleScript injection fix — design

**Status:** approved 2026-04-23
**Issue:** [#40](https://github.com/cmeans/yt-dont-recommend/issues/40)
**Priority:** P1 (HIGH)
**Scope:** single fix — #40 alone. #41, #43, #44 handled in separate PRs.

## Problem

`src/yt_dont_recommend/state.py:193-208` interpolates an untrusted `message` string into an AppleScript source line passed to `osascript -e` on macOS:

```python
if sys.platform == "darwin":
    subprocess.run(
        ["osascript", "-e",
         f'display notification "{message}" with title "yt-dont-recommend"'],
        capture_output=True, timeout=5,
    )
```

AppleScript exposes `do shell script "…"`, so a `"` in `message` closes the
string literal and anything after runs as shell with user privileges. Verified
end-to-end by the reviewer with patched subprocess; argv confirmed malicious on
macOS. Linux branch uses `notify-send` with args as separate list items and is
not injectable.

Reachability: `message` is built by `write_attention` call sites in `unblock.py`
(lines 177-183 and 237-243) from channel names that pass through
`parse_text_blocklist` / `parse_json_blocklist` without validation. Practical
triggers include a compromised maintained blocklist, a user passing a malicious
`--source URL`, or an MITM on an `http://` source (see issue #42).

## Solution

Single-helper choke point. Add a private pure function `_escape_applescript` in
`state.py`, invoked from `_desktop_notify` on the darwin branch only, that
escapes the AppleScript string-escape characters before interpolation.

### Components

**`_escape_applescript(s: str) -> str`** — pure total function. Escapes five
characters per the AppleScript Language Guide double-quoted string rules:

| Input byte      | Escaped form |
|-----------------|--------------|
| `\` (backslash) | `\\`         |
| `"` (quote)     | `\"`         |
| `\n` (newline)  | `\n`         |
| `\r` (return)   | `\r`         |
| `\t` (tab)      | `\t`         |

Backslash is escaped first so later-inserted backslashes are not re-escaped.
A raw newline inside a double-quoted AppleScript string is a syntax error, so
replacing it with the literal `\n` escape preserves the notification rather
than turning an injection attempt into a silent DoS.

**`_desktop_notify(message)`** — unchanged signature. On the darwin branch, the
message is passed through `_escape_applescript` before being interpolated into
the f-string. Linux branch unchanged.

### Data flow

```
attacker content → write_attention(msg) → _desktop_notify(msg)
                                                ↓
                                    _escape_applescript(msg) ← NEW
                                                ↓
                              f'display notification "{escaped}" with title "yt-dont-recommend"'
                                                ↓
                              subprocess.run(["osascript", "-e", <safe>])
```

No change to non-darwin path. No change to `write_attention`, `unblock.py`,
or any blocklist parsing. The sanitization sits at the osascript boundary,
which is where the actual trust boundary lies.

### Error handling

`_escape_applescript` is total — no exceptions. The existing broad
`except Exception: pass` wrapper in `_desktop_notify` remains as the backstop
for any other osascript failure. No new failure modes.

## Testing

Extend `tests/test_state.py::TestDesktopNotify` and add a new pure-function
test class.

### `TestEscapeAppleScript` (new)

- `test_plain_string_unchanged` — alphanumerics and spaces pass through
- `test_escapes_double_quote` — `"` becomes `\"`
- `test_escapes_backslash` — `\` becomes `\\`
- `test_escapes_newline` — `\n` becomes literal `\n`
- `test_escapes_return` — `\r` becomes literal `\r`
- `test_escapes_tab` — `\t` becomes literal `\t`
- `test_backslash_then_quote_ordering` — `\"` input becomes `\\\"` (escape
  ordering guard: backslash must be handled before quote)

### `TestDesktopNotify` (extend existing)

- `test_injection_payload_is_defanged` — the exact PoC from the review
  (`'@evil"; do shell script "echo pwned"; display notification "'`) produces
  an argv whose AppleScript source contains the payload as literal characters
  but wrapped inside the outer `"…"` — no unescaped `"` breaks the string
- `test_write_attention_end_to_end_with_malicious_channel` — build a
  `write_attention` argument embedding a crafted channel name in the same
  shape as the `unblock.py:237-243` formatted message, assert argv on darwin
  is safe. This is the end-to-end integration test the reviewer called out.
- `test_linux_path_unaffected` — existing test stays green; `notify-send` args
  are not escaped
- Existing `test_macos_uses_osascript` — update the embedded assertion
  (`'display notification "hello"'`) to match the (unchanged) escaped form
  for the safe input "hello"

### Platform gating

All darwin tests use the existing monkeypatch pattern at test_state.py:417-441
(`monkeypatch.setattr("sys.platform", "darwin")`) to exercise the macOS branch
on any host.

## Out of scope

- Issue #41 — `_canonicalize_channel` at parse time (separate PR)
- Issue #42 — reject `http://` in `resolve_source` (separate PR)
- Issue #43 — auto-upgrade `isatty` gating (separate PR)
- Issue #44 — atomic `save_state` (separate PR; chose not to bundle for
  cleaner security-commit audit trail)
- README trust-model documentation (separate concern)

## Commit shape

Single commit on a feature branch:

```
fix(state): escape AppleScript metacharacters in _desktop_notify (#40)
```

CHANGELOG entry under `## [Unreleased]` → `### Security` (new subsection).

## Verification

- Full pytest suite (449 existing + new tests) passes
- `ruff check src/ tests/` clean
- New test `test_injection_payload_is_defanged` specifically exercises the
  reviewer's PoC string and asserts the resulting argv cannot break out of
  the AppleScript string literal

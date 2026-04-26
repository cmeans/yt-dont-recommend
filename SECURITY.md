# Security Policy

## Supported versions

yt-dont-recommend is currently on the 0.5.x line. Fixes for security
issues are applied to the latest published version only. Users of
earlier versions should upgrade.

| Version | Supported         |
| ------- | ----------------- |
| 0.5.x   | ✅ security fixes |
| < 0.5   | ❌ upgrade        |

## Reporting an issue

**Please do not file a public GitHub issue for security problems.**

The only supported channel is a **GitHub Private Security Advisory**.
To open one:

1. Go to <https://github.com/cmeans/yt-dont-recommend/security/advisories/new>.
2. Fill in a description, steps to reproduce, and the affected
   version.
3. Submit as a draft advisory. Only the maintainer will see it.

This creates a private thread where the report, any proof-of-concept,
the fix, and disclosure timing can be discussed without exposing the
issue publicly. The private vulnerability reporting feature is
enabled on this repository.

If you cannot use GitHub Private Security Advisories for some reason,
please open a **public** issue titled simply "Security contact
request" — no details — and the maintainer will reach out to arrange
a private channel.

## Please include

- A description of the issue and its impact.
- Steps to reproduce (or a proof-of-concept).
- The version of yt-dont-recommend affected.
- Your operating system and Python version (Playwright + subprocess
  behavior is OS-dependent; the project supports Linux and macOS).
- Whether the issue is reproducible against a clean
  `pip install yt-dont-recommend` (or `uv tool install` / `pipx`),
  or only with a custom blocklist source or `config.yaml`.

## What to expect

- **Acknowledgment** after the maintainer sees the report. Response
  times vary — this is a one-person project.
- **Coordinated fix timeline.** yt-dont-recommend is maintained by
  one person, not a security team. Please be patient.
- **Credit in the release notes** if you'd like it. Anonymous
  disclosure is also fine.
- **No monetary reward.** yt-dont-recommend does not operate a bug
  bounty program. Reports are voluntary contributions to project
  safety.

## Scope

**In scope**

- **Injection through page-derived strings.** The browser scrapes
  channel display names and aria-labels from the live YouTube DOM
  and uses them to build CSS attribute selectors, AppleScript
  notification arguments, and (on Linux) `notify-send` arguments. A
  malicious-looking string that escapes those contexts is in scope.
  Recent fixes in this area: AppleScript metacharacter escape
  (issue #40), CSS attribute selector escape (#46).
- **Blocklist source trust.** `resolve_source` accepts local paths,
  HTTP(S) URLs, and named built-in keys. Bypassing the
  HTTPS-required check (#42), bypassing parse-time channel ID
  validation (#41), or other paths that allow a remote source to
  reach an unsafe sink are in scope.
- **State and config file integrity.** Atomic writes to
  `~/.yt-dont-recommend/processed.json`, `schedule.json`, and
  `config.yaml` (the selector overrides file) — partial-write
  corruption, race-condition data loss, or paths that allow a
  malicious blocklist entry to escape into the file are in scope.
- **Auto-upgrade safety.** Auto-upgrade requires an interactive TTY
  (`sys.stdin.isatty()`) and runs with the same privileges as the
  user. Bypasses of the TTY gate, bypasses of the published-version
  check, or paths that would let `--revert` install something other
  than the recorded previous version are in scope. The threat model
  is documented in the README "Auto-upgrade" section.
- **Data directory permissions.** `~/.yt-dont-recommend/` is created
  with mode `0o700`. Regressions that loosen those permissions or
  that leave secrets (browser profile cookies, ntfy.sh topic) in a
  world-readable location are in scope.
- **Subscription protection bypass.** A user's subscribed channels
  must never be blocked, even if they appear on a blocklist
  (`fetch_subscriptions()` in `browser.py`). A change that silently
  blocks a subscribed channel is in scope.
- **Selector self-healing safety.** `--check-selectors --repair` and
  the inline self-healing path in `process_channels` write to
  `config.yaml`. A path that lets a discovered (untrusted) selector
  string become a malicious payload when re-loaded is in scope.

**Out of scope**

- Vulnerabilities in dependencies (`playwright`, `ollama`,
  `youtube-transcript-api`, `pyyaml`, the bundled Chromium, etc.) —
  please report those upstream to the affected project.
- Attacks that require an adversary to already have write access to
  `~/.yt-dont-recommend/`, the blocklist file, or the user's home
  directory (that's a compromised host, not a project-specific
  issue).
- YouTube Terms of Service concerns — automating UI interactions
  violates YouTube's ToS. This is a documented risk class on par
  with SmartTube or ad blockers, not a security issue. See the
  README "Caveats" section.
- Changes in YouTube's recommendation algorithm or "Don't recommend
  channel" propagation latency — those are upstream behavior
  outside the project's control.
- Selector breakage from YouTube DOM changes — this is a known,
  ongoing maintenance concern, not a security issue. Use
  `--check-selectors` and file a bug.
- Issues with optional Ollama models used by `--clickbait`
  (accuracy, false positives, prompt-injection through video
  titles affecting only the local classifier output) — flag in a
  regular issue.

## Historical issues

Security-relevant findings are tracked in the GitHub issue tracker
under the `security` label. See also the [`LICENSE`](LICENSE) file
for Apache-2.0 warranty disclaimers.

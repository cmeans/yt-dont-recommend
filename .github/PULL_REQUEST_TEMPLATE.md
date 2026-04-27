<!--
  Thanks for opening a PR! This template auto-fills the body of new PRs.
  Replace the placeholder text below; remove sections that don't apply.
  Dependabot bypasses this template (it supplies its own body); see
  `.github/workflows/dependabot-changelog.yml` for how Dependabot PRs
  get a CHANGELOG entry automatically.
-->

## Summary

<!-- Two or three sentences on what changed and why. -->

## Test plan

<!-- Checklist the maintainer can walk to verify the change. -->

- [ ] `.venv/bin/python -m pytest tests/` — passes
- [ ] `.venv/bin/ruff check src/ tests/` — clean
- [ ] `bash scripts/smoke-test.sh` — clean
- [ ] Confirm no regression in the affected module

## CHANGELOG

<!--
  Confirm the matching CHANGELOG.md entry under `## [Unreleased]`.
  See CONTRIBUTING.md § "PR requirements" for category guidance.
  Categories: Added / Changed / Fixed / Security.
-->

- [ ] Added a `## [Unreleased]` entry to `CHANGELOG.md` under the appropriate Keep-a-Changelog category (Added / Changed / Fixed / Security)

Closes #

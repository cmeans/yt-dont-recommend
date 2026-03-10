#!/usr/bin/env bash
# scripts/smoke-test.sh
#
# Build the wheel from source, install it, and run quick sanity checks.
# Does NOT open a browser or require a logged-in session.
#
# Usage:
#   bash scripts/smoke-test.sh
#
# Run this before tagging a release.

set -euo pipefail

PASS=0
FAIL=0

ok()   { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL + 1)); }

check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then ok "$label"; else fail "$label"; fi
}

check_output() {
    local label="$1" pattern="$2"; shift 2
    local out
    out=$("$@" 2>&1) || true
    if echo "$out" | grep -qE -e "$pattern"; then ok "$label"; else fail "$label  (expected pattern: $pattern)"; fi
}

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------
echo "=== Build ==="
uv build --quiet

WHEEL=$(ls dist/yt_dont_recommend-*.whl 2>/dev/null | sort -V | tail -1)
if [ -z "$WHEEL" ]; then
    echo "ERROR: no wheel found in dist/ after build"
    exit 1
fi

# Extract version from wheel filename (yt_dont_recommend-X.Y.Z-py3-none-any.whl)
WHEEL_VERSION=$(basename "$WHEEL" | sed -E 's/yt_dont_recommend-([0-9.]+)-.*/\1/')
TOML_VERSION=$(grep '^version = ' pyproject.toml | sed -E 's/version = "([^"]+)"/\1/')

echo "  wheel   : $WHEEL"
echo "  version : $WHEEL_VERSION"

if [ "$WHEEL_VERSION" = "$TOML_VERSION" ]; then
    ok "wheel version matches pyproject.toml ($WHEEL_VERSION)"
else
    fail "version mismatch: wheel=$WHEEL_VERSION, pyproject.toml=$TOML_VERSION"
fi

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
echo ""
echo "=== Install ==="
uv tool install --force "$WHEEL" --quiet
ok "uv tool install succeeded"

# ---------------------------------------------------------------------------
# CLI checks (no browser required)
# ---------------------------------------------------------------------------
echo ""
echo "=== CLI checks ==="

check        "--version exits 0"             yt-dont-recommend --version
check_output "--version prints $WHEEL_VERSION" "$WHEEL_VERSION"  yt-dont-recommend --version
check        "--help exits 0"               yt-dont-recommend --help
check_output "--help mentions --blocklist"  "--blocklist"  yt-dont-recommend --help
check_output "--help mentions --clickbait"  "--clickbait"  yt-dont-recommend --help
check_output "--help mentions --source"     "--source"     yt-dont-recommend --help
check        "--list-sources exits 0"       yt-dont-recommend --list-sources
check_output "--list-sources shows deslop"  "deslop"         yt-dont-recommend --list-sources
check_output "--list-sources shows aislist" "aislist"        yt-dont-recommend --list-sources
check        "--stats exits 0"              yt-dont-recommend --stats
check        "--schedule status exits 0"    yt-dont-recommend --schedule status

# no-args should print help (not error)
check_output "no-args prints usage"  "usage:" yt-dont-recommend

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "All $PASS checks passed — ready to release v$WHEEL_VERSION."
else
    echo "$FAIL of $((PASS + FAIL)) checks FAILED."
    exit 1
fi

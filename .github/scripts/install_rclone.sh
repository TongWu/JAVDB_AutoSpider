#!/usr/bin/env bash
# Install rclone into ~/.local/bin without root.
#
# The previous implementation piped rclone's official install.sh through
# `sudo`, which works on GitHub-hosted images but dies on the self-hosted
# fleet ("sudo: I'm sorry ghrunner. I'm afraid I can't do that") — the
# runner user is not in sudoers there, so every rclone step on self-hosted
# failed before it began. rclone ships a static binary, so a plain
# unprivileged unpack into ~/.local/bin serves both runner types with one
# code path. Same pinned-release + sha256 pattern as ensure-git-lfs /
# ensure-python.
#
# Callers must put ~/.local/bin on PATH themselves when they invoke rclone
# in the SAME step ($GITHUB_PATH only affects later steps):
#   export PATH="$HOME/.local/bin:$PATH"
set -euo pipefail

# Pinned release; bump VERSION deliberately (checksums are fetched from the
# matching release, so no local hash table to keep in sync).
VERSION="1.74.4"

TARGET="${HOME}/.local/bin/rclone"

expose_on_path() {
  # Both exits must publish the directory: callers prepend it to PATH for the
  # current step, and later steps get it from $GITHUB_PATH.
  if [ -n "${GITHUB_PATH:-}" ]; then
    echo "${HOME}/.local/bin" >> "$GITHUB_PATH"
  fi
}

# Check $TARGET specifically, not `command -v rclone`. Callers prepend
# ~/.local/bin to PATH, so $TARGET is the binary that will actually run —
# a matching rclone elsewhere on PATH says nothing about it, and a stale
# $TARGET would shadow that system copy anyway. Only an exact version match
# short-circuits: accepting any version would let a persistent runner keep an
# old build forever, so bumping VERSION would never reach the machines that
# need it. Consequence: a host whose only rclone is a system one gets its own
# pinned copy on first run (a one-time download; the pre-2026-07 script
# downloaded on every invocation regardless).
if [ -x "$TARGET" ]; then
  INSTALLED="$("$TARGET" version 2>/dev/null | head -1 | awk '{print $2}' || true)"
  if [ "$INSTALLED" = "v${VERSION}" ]; then
    echo "✓ rclone already at pinned version: ${INSTALLED}"
    expose_on_path
    exit 0
  fi
  echo "${TARGET} is ${INSTALLED:-unreadable} but pinned version is v${VERSION} — replacing it"
fi
case "$(uname -m)" in
  x86_64)  ARCH="amd64" ;;
  aarch64) ARCH="arm64" ;;
  *)
    echo "::error::install_rclone: unsupported arch '$(uname -m)'"
    exit 1
    ;;
esac

ASSET="rclone-v${VERSION}-linux-${ARCH}.zip"
BASE="https://downloads.rclone.org/v${VERSION}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "Installing rclone ${VERSION} (${ARCH}) into ~/.local/bin"
curl -fsSL --retry 3 -o "${WORK}/${ASSET}" "${BASE}/${ASSET}"
curl -fsSL --retry 3 -o "${WORK}/SHA256SUMS" "${BASE}/SHA256SUMS"
EXPECTED="$(awk -v a="$ASSET" '$2 == a || $2 == "*" a {print $1; exit}' "${WORK}/SHA256SUMS")"
if [ -z "$EXPECTED" ]; then
  echo "::error::install_rclone: ${ASSET} not listed in SHA256SUMS for v${VERSION}"
  exit 1
fi
ACTUAL="$(sha256sum "${WORK}/${ASSET}" | cut -d' ' -f1)"
if [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "::error::install_rclone: sha256 mismatch for ${ASSET} (expected ${EXPECTED}, got ${ACTUAL})"
  exit 1
fi

# python3 -m zipfile instead of `unzip`: the fleet's Debian images do not all
# ship unzip, and python3 is already a hard requirement for every caller.
python3 -m zipfile -e "${WORK}/${ASSET}" "${WORK}/unpacked"
mkdir -p "${HOME}/.local/bin"
install -m 0755 "${WORK}/unpacked/rclone-v${VERSION}-linux-${ARCH}/rclone" "$TARGET"
expose_on_path
"$TARGET" version | head -1

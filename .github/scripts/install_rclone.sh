#!/usr/bin/env bash
# Install rclone using the official script.
# Upstream install.sh exits with code 3 when the latest version is already
# installed — treat that as success (see rclone install.sh messaging).
set -euo pipefail
INSTALLER="$(mktemp)"
trap 'rm -f "$INSTALLER"' EXIT
curl -sSf https://rclone.org/install.sh -o "$INSTALLER"
set +e
sudo bash "$INSTALLER"
rc=$?
set -e
if [[ "$rc" -eq 3 ]]; then
  echo "rclone: already at latest (install.sh exit 3) — OK"
  exit 0
fi
exit "$rc"

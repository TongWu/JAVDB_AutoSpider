#!/usr/bin/env bash
# Export all JAVDB D1 databases and upload them to Google Drive via rclone.
#
# Usage:
#   ./scripts/d1_backup_to_gdrive.sh <instance_name> [--keep-local]
#
# Layout on Google Drive:
#   gdrive:/剧集/不可以色色/JAVDB_AutoSpider/D1_Backup/{YYYYMMDDTHHMMSS}_{instance_name}/
#       history.sql
#       reports.sql
#       operations.sql
#       metadata.json
#
# Requires `wrangler` (logged in) and `rclone` with the configured remote.
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <instance_name> [--keep-local]" >&2
  exit 2
fi

INSTANCE_NAME="$1"
shift
KEEP_LOCAL=0
for arg in "$@"; do
  [[ "$arg" == "--keep-local" ]] && KEEP_LOCAL=1
done

# Sanitize instance name: keep alphanumerics, dash, underscore; replace others with '-'
INSTANCE_SAFE=$(printf '%s' "$INSTANCE_NAME" | tr -c '[:alnum:]_-' '-' | sed 's/-\{2,\}/-/g')
[[ -z "$INSTANCE_SAFE" ]] && INSTANCE_SAFE="unknown"

DBS=(history reports operations)
TS=$(date -u +%Y%m%dT%H%M%S)
SUBDIR="${TS}_${INSTANCE_SAFE}"

# Allow override (e.g. local testing); production default points at gdrive.
RCLONE_BACKUP_BASE="${RCLONE_D1_BACKUP_BASE:-gdrive:/剧集/不可以色色/JAVDB_AutoSpider/D1_Backup}"
DEST="${RCLONE_BACKUP_BASE}/${SUBDIR}"

WORK_DIR=$(mktemp -d -t "d1backup_${INSTANCE_SAFE}_XXXX")
trap '[[ "$KEEP_LOCAL" == "0" ]] && rm -rf "$WORK_DIR" || echo "Kept local backup at $WORK_DIR"' EXIT

echo "==> Backup target: $DEST"
echo "==> Local workdir: $WORK_DIR"

# 1) Export each D1 database
EXPORTED=()
for db in "${DBS[@]}"; do
  out="${WORK_DIR}/${db}.sql"
  echo "==> wrangler d1 export javdb-${db}"
  if wrangler d1 export "javdb-${db}" --remote --output "$out" 2>&1 | tail -10; then
    if [[ -s "$out" ]]; then
      EXPORTED+=("$out")
    else
      echo "  WARNING: $out is empty" >&2
    fi
  else
    echo "  ERROR: export failed for javdb-${db}" >&2
    exit 1
  fi
done

# 2) Write metadata
cat > "${WORK_DIR}/metadata.json" <<EOF
{
  "instance_name": "${INSTANCE_NAME}",
  "instance_safe": "${INSTANCE_SAFE}",
  "timestamp_utc": "${TS}",
  "backed_up_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "github_run_id": "${GITHUB_RUN_ID:-}",
  "github_run_attempt": "${GITHUB_RUN_ATTEMPT:-}",
  "github_workflow": "${GITHUB_WORKFLOW:-}",
  "github_ref": "${GITHUB_REF:-}",
  "github_sha": "${GITHUB_SHA:-}",
  "databases": $(printf '%s\n' "${DBS[@]}" | python3 -c "import sys, json; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))")
}
EOF

# 3) Show what we're uploading
echo "==> Bundle contents:"
ls -lh "$WORK_DIR"

# 4) Upload via rclone (one folder per backup)
echo "==> rclone copy → ${DEST}"
rclone copy "$WORK_DIR" "$DEST" \
  --transfers 4 --checkers 8 \
  --retries 3 --low-level-retries 5 \
  --stats 5s --stats-one-line

echo "==> rclone listing of destination:"
rclone lsl "$DEST" || true

echo "✅ D1 backup complete: ${SUBDIR}"

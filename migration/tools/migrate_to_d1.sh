#!/usr/bin/env bash
# Prepare SQLite .dump files for Cloudflare D1 import (macOS-compatible).
#
# Usage:
#   ./scripts/migrate_to_d1.sh prepare        # clean + split only
#   ./scripts/migrate_to_d1.sh import         # import all chunks via wrangler
#   ./scripts/migrate_to_d1.sh verify         # row-count comparison

set -euo pipefail

DBS=(history reports operations)
ROWS_PER_CHUNK=5000
CHUNK_DIR="d1_chunks"

clean_one() {
  local f="$1"
  local out="$2"
  sed -E \
    -e '/^BEGIN TRANSACTION/d' \
    -e '/^COMMIT/d' \
    -e '/^PRAGMA/d' \
    -e '/sqlite_sequence/d' \
    -e '/sqlite_stat[1-4]/d' \
    -e 's/[[:space:]]+REFERENCES[[:space:]]+[A-Za-z_]+\([A-Za-z_]+\)//g' \
    "$f" > "$out"
}

split_one() {
  # $1 = cleaned sql,  $2 = output prefix (without index)
  local clean_sql="$1"
  local prefix="$2"

  # schema goes to ${prefix}_00_schema.sql
  awk '/^INSERT INTO/{exit} {print}' "$clean_sql" > "${prefix}_00_schema.sql"

  # chunked data files: ${prefix}_data_001.sql, _002.sql, ...
  awk -v rpc="$ROWS_PER_CHUNK" -v prefix="$prefix" '
    /^INSERT INTO/ {
      if ((NR_in_chunk % rpc) == 0) {
        if (out != "") close(out)
        chunk_idx++
        out = sprintf("%s_data_%03d.sql", prefix, chunk_idx)
      }
      print > out
      NR_in_chunk++
    }
  ' "$clean_sql"
}

cmd_prepare() {
  mkdir -p "$CHUNK_DIR"
  for db in "${DBS[@]}"; do
    src="${db}.sql"
    if [[ ! -f "$src" ]]; then
      echo "SKIP: $src not found"
      continue
    fi
    echo "==> Cleaning $src"
    cleaned="${CHUNK_DIR}/${db}.clean.sql"
    clean_one "$src" "$cleaned"

    # Sanity: should have no banned tokens left
    if grep -E "REFERENCES|^PRAGMA|sqlite_sequence" "$cleaned" >/dev/null; then
      echo "  WARNING: residual banned tokens in $cleaned"
    fi

    echo "==> Splitting $cleaned (chunk = $ROWS_PER_CHUNK rows)"
    split_one "$cleaned" "${CHUNK_DIR}/${db}"
  done

  echo
  echo "Generated chunks:"
  ls -lh "$CHUNK_DIR"/
}

cmd_import() {
  for db in "${DBS[@]}"; do
    schema="${CHUNK_DIR}/${db}_00_schema.sql"
    if [[ ! -f "$schema" ]]; then
      echo "SKIP $db (no schema chunk; run 'prepare' first)"
      continue
    fi

    echo "==> [$db] schema"
    wrangler d1 execute "javdb-${db}" --remote --file="$schema" -y

    echo "==> [$db] data chunks"
    for chunk in "${CHUNK_DIR}/${db}_data_"*.sql; do
      [[ -f "$chunk" ]] || continue
      echo "  -> $(basename "$chunk")"
      if ! wrangler d1 execute "javdb-${db}" --remote --file="$chunk" -y; then
        echo "FAILED on $chunk — aborting $db" >&2
        return 1
      fi
    done
  done
}

cmd_verify() {
  for db in "${DBS[@]}"; do
    echo "=== $db: local ==="
    sqlite3 "reports/${db}.db" "
      SELECT name, (SELECT COUNT(*) FROM \"' || name || '\")
      FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'" 2>/dev/null \
      || sqlite3 "reports/${db}.db" \
        ".tables" \
        | tr ' ' '\n' | sort -u | grep -v '^$' \
        | while read -r tbl; do
            cnt=$(sqlite3 "reports/${db}.db" "SELECT COUNT(*) FROM \"$tbl\"")
            printf "  %-30s %s\n" "$tbl" "$cnt"
          done

    echo "=== $db: D1 ==="
    wrangler d1 execute "javdb-${db}" --remote --command="
      SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'
    " -y 2>/dev/null || true
  done
}

case "${1:-}" in
  prepare) cmd_prepare ;;
  import)  cmd_import  ;;
  verify)  cmd_verify  ;;
  *)
    echo "Usage: $0 {prepare|import|verify}"
    exit 1
    ;;
esac

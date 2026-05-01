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

  # Single pass: route INSERTs into chunked data files, pre-data DDL into
  # ${prefix}_00_schema.sql, and post-data DDL (indexes/triggers) into
  # ${prefix}_99_post_data.sql so it runs after all rows are loaded.
  : > "${prefix}_00_schema.sql"
  : > "${prefix}_99_post_data.sql"
  rm -f "${prefix}_data_"*.sql
  awk -v rpc="$ROWS_PER_CHUNK" \
    -v prefix="$prefix" \
    -v schema="${prefix}_00_schema.sql" \
    -v post="${prefix}_99_post_data.sql" '
    function is_post_data_ddl(line, normalized) {
      normalized = toupper(line)
      sub(/^[[:space:]]+/, "", normalized)
      return normalized ~ /^CREATE[[:space:]]+(UNIQUE[[:space:]]+)?INDEX[[:space:]]/ ||
        normalized ~ /^CREATE[[:space:]]+(TEMP[[:space:]]+|TEMPORARY[[:space:]]+)?TRIGGER[[:space:]]/
    }
    function is_trigger_ddl(line, normalized) {
      normalized = toupper(line)
      sub(/^[[:space:]]+/, "", normalized)
      return normalized ~ /^CREATE[[:space:]]+(TEMP[[:space:]]+|TEMPORARY[[:space:]]+)?TRIGGER[[:space:]]/
    }
    function statement_done(line) {
      return line ~ /;[[:space:]]*$/
    }
    function post_data_statement_done(line, normalized) {
      if (!in_trigger_ddl) {
        return statement_done(line)
      }
      normalized = toupper(line)
      sub(/^[[:space:]]+/, "", normalized)
      return normalized ~ /(^|[[:space:]])END;[[:space:]]*$/
    }
    in_post_data_ddl {
      print >> post
      if (post_data_statement_done($0)) {
        in_post_data_ddl = 0
        in_trigger_ddl = 0
      }
      next
    }
    /^INSERT INTO/ {
      if ((NR_in_chunk % rpc) == 0) {
        if (out != "") close(out)
        chunk_idx++
        out = sprintf("%s_data_%03d.sql", prefix, chunk_idx)
      }
      print > out
      NR_in_chunk++
      next
    }
    is_post_data_ddl($0) {
      print >> post
      in_trigger_ddl = is_trigger_ddl($0)
      if (!post_data_statement_done($0)) {
        in_post_data_ddl = 1
      } else {
        in_trigger_ddl = 0
      }
      next
    }
    { print >> schema }
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

    post_data="${CHUNK_DIR}/${db}_99_post_data.sql"
    if [[ -s "$post_data" ]]; then
      echo "==> [$db] post-data DDL"
      if ! wrangler d1 execute "javdb-${db}" --remote --file="$post_data" -y; then
        echo "FAILED on $post_data — aborting $db" >&2
        return 1
      fi
    fi
  done
}

cmd_verify() {
  local failed=0
  for db in "${DBS[@]}"; do
    echo "================ $db ================"
    local_db="reports/${db}.db"
    if [[ ! -f "$local_db" ]]; then
      echo "  SKIP: $local_db not found"
      continue
    fi

    # Authoritative table list comes from the local DB so we compare every
    # table that exists locally; tables present only in D1 will surface as
    # local=ERR rows when read by sqlite3 below.
    tables_err=$(mktemp)
    if ! tables=$(sqlite3 "$local_db" \
      "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;" \
      2>"$tables_err"); then
      echo "  ERROR: failed to enumerate user tables in $local_db" >&2
      if [[ -s "$tables_err" ]]; then
        while IFS= read -r line; do
          echo "    $line" >&2
        done < "$tables_err"
      fi
      rm -f "$tables_err"
      exit 1
    fi
    rm -f "$tables_err"
    if [[ -z "$tables" ]]; then
      echo "  (no user tables found in $local_db)"
      continue
    fi

    while IFS= read -r tbl; do
      [[ -z "$tbl" ]] && continue
      cnt=$(sqlite3 "$local_db" "SELECT COUNT(*) FROM \"$tbl\";" 2>/dev/null || echo "ERR")
      d1_json=$(wrangler d1 execute "javdb-${db}" --remote --json \
        --command="SELECT COUNT(*) AS n FROM \"$tbl\";" 2>/dev/null || echo "")
      d1_cnt=$(printf '%s' "$d1_json" | python3 -c "import json,sys
try:
  data=json.load(sys.stdin)
  print(data[0]['results'][0]['n'])
except Exception:
  print('ERR')" 2>/dev/null || echo "ERR")
      # Trim whitespace so numeric equality doesn't trip on stray newlines.
      cnt=$(printf '%s' "$cnt" | tr -d '[:space:]')
      d1_cnt=$(printf '%s' "$d1_cnt" | tr -d '[:space:]')
      if [[ "$cnt" != "ERR" && "$d1_cnt" != "ERR" && "$cnt" == "$d1_cnt" ]]; then
        printf "  %-32s OK            (count=%s)\n" "$tbl" "$cnt"
      else
        printf "  %-32s MISMATCH local=%s remote=%s\n" "$tbl" "$cnt" "$d1_cnt"
        failed=1
      fi
    done <<< "$tables"
  done
  if [[ "$failed" -eq 1 ]]; then
    exit 1
  fi
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

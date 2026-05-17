#!/usr/bin/env bash
# Compare row counts between local sqlite and Cloudflare D1.
# macOS bash 3.x compatible (no associative arrays).
set -euo pipefail

# Aggregate non-zero exit signal across every table on every DB so the script
# fails CI when ANY row count diverges; without this, callers (CI gates, Make
# targets) would happily treat a "[MISMATCH]" log line as success.
overall_status=0

verify_db() {
  local db="$1"; shift
  echo "================ $db ================"
  for tbl in "$@"; do
    local_n=$(sqlite3 "reports/${db}.db" "SELECT COUNT(*) FROM \"$tbl\"" 2>/dev/null || echo "ERR")
    d1_json=$(wrangler d1 execute "javdb-${db}" --remote --json \
      --command="SELECT COUNT(*) AS n FROM \"$tbl\"" 2>/dev/null || echo '[]')
    d1_n=$(echo "$d1_json" | python3 -c "import json,sys
try:
  data=json.load(sys.stdin)
  print(data[0]['results'][0]['n'])
except Exception:
  print('ERR')")
    flag="OK"
    if [[ "$local_n" == "ERR" || "$d1_n" == "ERR" || "$local_n" != "$d1_n" ]]; then
      flag="MISMATCH"
      overall_status=1
    fi
    printf "  %-32s local=%-8s  d1=%-8s  [%s]\n" "$tbl" "$local_n" "$d1_n" "$flag"
  done
}

verify_db history    MovieHistory TorrentHistory SchemaVersion
verify_db reports    ReportSessions ReportMovies ReportTorrents SpiderStats UploaderStats PikpakStats SchemaVersion
verify_db operations RcloneInventory DedupRecords PikpakHistory InventoryAlignNoExactMatch SchemaVersion

exit "$overall_status"

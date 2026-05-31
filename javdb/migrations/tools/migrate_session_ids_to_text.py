#!/usr/bin/env python3
"""One-off migration: normalise legacy integer ReportSessions ids to the
canonical TEXT session-id format ``YYYYMMDDTHHMMSS.ffffffZ-TTTT-SSSS``.

Legacy rows (e.g. ``12``, ``248``) predate the TEXT format. We synthesise a
new id from ``ReportSessions.DateTimeCreated`` (second precision is all we
have); the microsecond / TTTT / SSSS fields that never existed for these rows
are filled with placeholders (``000000`` / ``0000``), and SSSS doubles as a
collision counter when two legacy sessions share the same creation second.

The id is a primary key referenced by ``SessionId`` / ``session_id`` columns
across 18 tables in three separate D1 databases. Most have no FK to
ReportSessions and update independently. The four REPORTS-db tables that DO
declare ``REFERENCES ReportSessions(Id)`` (with no ON UPDATE CASCADE) are
flipped together with the PK in ONE transaction under
``PRAGMA defer_foreign_keys=on``, so the rename never orphans a child nor
violates the FK. Every UPDATE is idempotent (``WHERE col = old``), so the run
is safe to repeat until ``--verify`` reports no tearing.

Usage::

    # dry-run (default): print the old -> new mapping + per-table impact, no writes
    python3 -m javdb.migrations.tools.migrate_session_ids_to_text

    # apply the migration
    python3 -m javdb.migrations.tools.migrate_session_ids_to_text --apply

    # verify no legacy ids remain anywhere
    python3 -m javdb.migrations.tools.migrate_session_ids_to_text --verify

Credentials are read from ``config.py`` (CLOUDFLARE_ACCOUNT_ID /
CLOUDFLARE_API_TOKEN / D1_*_DB_ID), same as the rest of the D1 tooling.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

import requests

import config

# Canonical TEXT session-id shape; rows already matching this are left alone.
# Mirrors javdb.storage.db._db_session.SESSION_ID_PATTERN — TTTT/SSSS are 4-char
# lowercase-hex tokens (per-process tag + monotonic counter), NOT decimal.
NEW_ID_RE = re.compile(r"^\d{8}T\d{6}\.\d{6}Z-[0-9a-f]{4}-[0-9a-f]{4}$")

_ACCOUNT = config.CLOUDFLARE_ACCOUNT_ID
_TOKEN = config.CLOUDFLARE_API_TOKEN
_HEADERS = {"Authorization": f"Bearer {_TOKEN}", "Content-Type": "application/json"}

HISTORY = config.D1_HISTORY_DB_ID
REPORTS = config.D1_REPORTS_DB_ID
OPERATIONS = config.D1_OPERATIONS_DB_ID

# The REPORTS-db FK group: ReportSessions(Id) is the PK; these children declare
# `REFERENCES ReportSessions(Id)` with NO `ON UPDATE CASCADE`, so the PK and all
# its children MUST flip together in one transaction with deferred FK checks —
# updating either side first violates the constraint (see BFR). Cross-database
# tables (HISTORY / OPERATIONS) cannot carry a D1 FK, so they update freely.
FK_DB = REPORTS
FK_PK = (REPORTS, "ReportSessions", "Id")
FK_CHILDREN = [
    (REPORTS, "ReportMovies", "SessionId"),
    (REPORTS, "SpiderStats", "SessionId"),
    (REPORTS, "UploaderStats", "SessionId"),
    (REPORTS, "PikpakStats", "SessionId"),
]

# Tables with NO FK to ReportSessions — safe to update independently.
INDEPENDENT_TABLES = [
    (HISTORY, "MovieHistory", "SessionId"),
    (HISTORY, "TorrentHistory", "SessionId"),
    (HISTORY, "PendingMovieHistoryWrites", "SessionId"),
    (HISTORY, "PendingTorrentHistoryWrites", "SessionId"),
    (REPORTS, "PipelineEvent", "session_id"),
    (REPORTS, "RunEventSummary", "session_id"),
    (REPORTS, "ParseRunFieldFill", "session_id"),
    (REPORTS, "OpsIncidents", "session_id"),
    (OPERATIONS, "DedupRecords", "SessionId"),
    (OPERATIONS, "PikpakHistory", "SessionId"),
    (OPERATIONS, "InventoryAlignNoExactMatch", "SessionId"),
    (OPERATIONS, "EmailNotificationHistory", "SessionId"),
    (OPERATIONS, "AcquisitionOutcome", "session_id"),
]

# Every (db, table, column) that holds a session id — used for impact counting
# and the anti-tearing verify (FK-agnostic).
ALL_COLUMNS = INDEPENDENT_TABLES + FK_CHILDREN + [FK_PK]

# Audit + snapshot artefacts (written by --apply, read by --verify).
MAP_FILE = "session_id_migration_map.json"
SNAPSHOT_FILE = "session_id_migration_snapshot.json"


def _q(db_id: str, sql: str, params=None):
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/{_ACCOUNT}"
        f"/d1/database/{db_id}/query"
    )
    resp = requests.post(url, headers=_HEADERS, json={"sql": sql, "params": params or []}, timeout=30)
    body = resp.json()
    if not body.get("success"):
        raise RuntimeError(f"D1 query failed: {body.get('errors')}\nSQL: {sql}")
    return body["result"][0]["results"]


def _sql_lit(value) -> str:
    """Safe single-quoted SQL literal for a session id. Session ids are a
    restricted charset (digits, or YYYYMMDDT...Z-hhhh-hhhh); refuse anything
    else so the multi-statement FK-group transaction can inline values."""
    s = str(value)
    if not re.fullmatch(r"[0-9A-Za-z._:\-]+", s):
        raise ValueError(f"unsafe session-id literal: {s!r}")
    return f"'{s}'"


def _synthesize_base(date_time_created: str | None) -> str:
    """``YYYY-MM-DD HH:MM:SS`` -> ``YYYYMMDDTHHMMSS.000000Z-0000``. Missing /
    unparseable timestamps fall back to an all-zero date (still placeholdered)."""
    if date_time_created:
        try:
            dt = datetime.strptime(date_time_created.strip(), "%Y-%m-%d %H:%M:%S")
            return f"{dt:%Y%m%dT%H%M%S}.000000Z-0000"
        except ValueError:
            pass
    return "00000000T000000.000000Z-0000"


def build_mapping() -> list[dict]:
    """Return [{old_id, date_time_created, new_id, synthetic}] for legacy rows."""
    rows = _q(REPORTS, "SELECT Id, DateTimeCreated FROM ReportSessions ORDER BY DateTimeCreated, Id")
    legacy = [r for r in rows if not NEW_ID_RE.match(str(r["Id"]))]

    mapping: list[dict] = []
    seq_by_base: dict[str, int] = {}
    for r in legacy:
        old_id = str(r["Id"])
        base = _synthesize_base(r.get("DateTimeCreated"))
        seq = seq_by_base.get(base, 0)
        seq_by_base[base] = seq + 1
        # SSSS is a 4-char hex field in the canonical format; keep it valid.
        new_id = f"{base}-{seq:04x}"
        mapping.append({
            "old_id": old_id,
            "date_time_created": r.get("DateTimeCreated"),
            "new_id": new_id,
            "synthetic": True,
        })
    return mapping


# D1 caps the number of bound SQL variables per statement; chunk IN-lists.
_CHUNK = 90


def _count_referencing(db_id: str, table: str, col: str, old_ids: list[str]) -> int:
    total = 0
    for i in range(0, len(old_ids), _CHUNK):
        chunk = old_ids[i:i + _CHUNK]
        ph = ",".join(["?"] * len(chunk))
        total += _q(db_id, f"SELECT COUNT(*) AS n FROM {table} WHERE {col} IN ({ph})", chunk)[0]["n"]
    return total


def _impact_counts(old_ids: list[str]) -> list[tuple[str, int]]:
    """Per-table count of rows that reference any legacy id (for dry-run)."""
    return [
        (table, _count_referencing(db_id, table, col, old_ids))
        for db_id, table, col in ALL_COLUMNS
    ]


def print_dry_run(mapping: list[dict]) -> None:
    print(f"Legacy ReportSessions to migrate: {len(mapping)}\n")
    print(f"{'OLD id':>8}  {'DateTimeCreated':19}  NEW id")
    print("-" * 70)
    for m in mapping:
        print(f"{m['old_id']:>8}  {str(m['date_time_created']):19}  {m['new_id']}")

    new_ids = {m["new_id"] for m in mapping}
    dup = len(mapping) - len(new_ids)
    print(f"\nDistinct new ids: {len(new_ids)} / {len(mapping)} "
          f"(duplicate synthesized ids — MUST be 0: {dup})")
    # Safety: a synthesized id must not collide with an id already in the table.
    existing = {str(r["Id"]) for r in _q(REPORTS, "SELECT Id FROM ReportSessions")}
    clash = sorted(new_ids & existing)
    print(f"New ids colliding with existing session ids (MUST be 0): {len(clash)}")
    if clash:
        print("  CLASH:", clash[:10])

    print("\nRows that WILL be updated per table:")
    for table, n in _impact_counts([m["old_id"] for m in mapping]):
        if n:
            print(f"  {table:30} {n}")


def _present_old_ids(db_id: str, table: str, col: str, old_ids: list[str]) -> list[str]:
    """Subset of old_ids that actually appear in this table (chunked)."""
    present: list[str] = []
    for i in range(0, len(old_ids), _CHUNK):
        chunk = old_ids[i:i + _CHUNK]
        ph = ",".join(["?"] * len(chunk))
        rows = _q(db_id, f"SELECT DISTINCT {col} AS v FROM {table} WHERE {col} IN ({ph})", chunk)
        present.extend(str(r["v"]) for r in rows)
    return present


def apply_mapping(mapping: list[dict]) -> None:
    # Safety gates: refuse to touch anything if the mapping is unsound.
    new_ids = {m["new_id"] for m in mapping}
    if len(new_ids) != len(mapping):
        raise SystemExit("ABORT: duplicate synthesized ids in mapping; refusing to apply.")
    existing = {str(r["Id"]) for r in _q(REPORTS, "SELECT Id FROM ReportSessions")}
    if new_ids & existing:
        raise SystemExit(
            f"ABORT: {len(new_ids & existing)} synthesized id(s) collide with existing session ids."
        )

    print(f"Applying migration for {len(mapping)} legacy sessions...")
    new_by_old = {m["old_id"]: m["new_id"] for m in mapping}
    all_old = list(new_by_old)

    # 1) persist the audit map + a pre-migration snapshot (legacy-ref count and
    #    total rows per table) BEFORE touching anything, so the verify pass can
    #    prove the rows moved intact and no table was torn.
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    snapshot = {}
    for db_id, table, col in ALL_COLUMNS:
        snapshot[table] = {
            "legacy_refs": _count_referencing(db_id, table, col, all_old),
            "total_rows": _q(db_id, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"],
        }
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    print(f"  wrote {MAP_FILE} + {SNAPSHOT_FILE}")

    # 2) independent (no-FK) tables: a plain UPDATE per present id.
    for db_id, table, col in INDEPENDENT_TABLES:
        present = _present_old_ids(db_id, table, col, all_old)
        for old_id in present:
            _q(db_id, f"UPDATE {table} SET {col}=? WHERE {col}=?", [new_by_old[old_id], old_id])
        if present:
            print(f"  {table:30} updated {len(present)} id(s)")

    # 3) REPORTS FK group: ReportSessions PK and its FK children must flip in a
    #    SINGLE transaction with deferred FK checks, else the rename either
    #    orphans children (PK first) or violates the FK (children first). One
    #    /query request runs its statements as one transaction; PRAGMA
    #    defer_foreign_keys=on holds for that request, so the FK is re-checked
    #    only at commit, by which point parent + children are consistent.
    pk_table, pk_col = FK_PK[1], FK_PK[2]
    present_pk = _present_old_ids(FK_DB, pk_table, pk_col, all_old)
    for old_id in present_pk:
        new_id = new_by_old[old_id]
        o, n = _sql_lit(old_id), _sql_lit(new_id)
        stmts = ["PRAGMA defer_foreign_keys=on;",
                 f"UPDATE {pk_table} SET {pk_col}={n} WHERE {pk_col}={o};"]
        stmts += [f"UPDATE {t} SET {c}={n} WHERE {c}={o};" for _, t, c in FK_CHILDREN]
        _q(FK_DB, "\n".join(stmts))
    print(f"  {pk_table} + {len(FK_CHILDREN)} FK child table(s): "
          f"updated {len(present_pk)} id(s) atomically")
    print("Done. Run with --verify to confirm integrity (no tearing).")


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def verify(mapping: list[dict]) -> int:
    """Anti-tearing verification. Confirms (1) zero legacy ids remain anywhere,
    (2) every new id is a ReportSessions PK, (3) each table's new-id reference
    count matches the pre-migration legacy count and its total row count is
    unchanged, and (4) no child row references a new id missing from
    ReportSessions (orphan)."""
    if not mapping:
        print("Empty mapping — nothing to verify.")
        return 0
    old_ids = [m["old_id"] for m in mapping]
    new_ids = [m["new_id"] for m in mapping]
    snapshot = _load_json(SNAPSHOT_FILE) or {}
    existing_pk = {str(r["Id"]) for r in _q(REPORTS, "SELECT Id FROM ReportSessions")}
    ok = True

    # (1) zero legacy ids remain in any of the 18 columns.
    remaining = sum(
        _count_referencing(db_id, table, col, old_ids)
        for db_id, table, col in ALL_COLUMNS
    )
    print(f"[1] legacy ids remaining anywhere (MUST be 0): {remaining}")
    ok = ok and remaining == 0

    # (2) every new id landed as a ReportSessions PK.
    pk_present = sum(1 for nid in new_ids if nid in existing_pk)
    print(f"[2] new ids present as ReportSessions PK (expect {len(new_ids)}): {pk_present}")
    ok = ok and pk_present == len(new_ids)

    # (3)/(4) per-table new-id reference count vs pre-migration legacy count,
    # total-row conservation, and orphan check.
    print("[3/4] per-table new-id refs vs snapshot legacy refs (+ totals, orphans):")
    for db_id, table, col in ALL_COLUMNS:
        new_cnt = _count_referencing(db_id, table, col, new_ids)
        snap = snapshot.get(table, {})
        exp = snap.get("legacy_refs")
        total_now = _q(db_id, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        total_was = snap.get("total_rows")
        distinct_new = set(_present_old_ids(db_id, table, col, new_ids))
        orphans = distinct_new - existing_pk
        issues = []
        if exp is not None and new_cnt != exp:
            issues.append(f"refs {new_cnt}!=expected {exp}")
        if total_was is not None and total_now != total_was:
            issues.append(f"total {total_now}!=was {total_was}")
        if orphans:
            issues.append(f"orphans {len(orphans)}")
        if new_cnt or issues:
            flag = "  <-- " + "; ".join(issues) if issues else ""
            print(f"     {table:30} new_refs={new_cnt}{flag}")
        if issues:
            ok = False

    print("\nVERIFY:", "PASS — no tearing detected." if ok else "FAIL — see issues above.")
    return 0 if ok else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--apply", action="store_true", help="Execute the UPDATEs (default: dry-run).")
    g.add_argument("--verify", action="store_true", help="Check no legacy ids remain anywhere.")
    ap.add_argument("--only", action="append", metavar="OLD_ID",
                    help="Restrict to specific legacy id(s) — canary runs. Repeatable. "
                         "Uses the same code path as the full run; new ids keep their "
                         "full-mapping values, so prefer ids with a UNIQUE timestamp.")
    args = ap.parse_args(argv)

    if args.verify:
        # After --apply, ReportSessions has no legacy ids to rebuild from, so
        # prefer the saved mapping; fall back to a live scan for a pre-apply run.
        saved = _load_json(MAP_FILE)
        if saved is None:
            print(f"No {MAP_FILE}; rebuilding mapping from current legacy rows.")
        return verify(saved if saved is not None else build_mapping())

    mapping = build_mapping()
    if args.only:
        wanted = set(args.only)
        mapping = [m for m in mapping if m["old_id"] in wanted]
        missing = wanted - {m["old_id"] for m in mapping}
        if missing:
            raise SystemExit(f"--only id(s) not found among legacy rows: {sorted(missing)}")
        print(f"--only: restricted to {len(mapping)} session(s): {[m['old_id'] for m in mapping]}")
    if not mapping:
        print("No legacy ReportSessions ids found — nothing to migrate.")
        return 0
    if args.apply:
        apply_mapping(mapping)
        return 0
    print_dry_run(mapping)
    print("\n(dry-run only — re-run with --apply to execute)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

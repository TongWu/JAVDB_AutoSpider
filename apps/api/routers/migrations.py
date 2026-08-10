"""Migrations management endpoints.

GET  /api/migrations                    — list D1 SQL migration files + applied state
POST /api/migrations/{migration_id}/run — preview or apply a migration against D1
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from apps.api.infra.auth import require_role
from apps.api.schemas.migrations import (
    MigrationItem,
    MigrationListResponse,
    RunMigrationRequest,
    RunMigrationResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/migrations", tags=["migrations"])

_MIGRATIONS_DIR = Path("javdb/migrations/d1")
_SAFE_MIGRATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-\.]*$")
_APPLIED_KEY_PREFIX = "migration_applied:"

# Every migration documents its target database in a header comment of the form
# `wrangler d1 execute javdb-operations --remote --file=...`. That command is the
# only machine-readable statement of intent the file carries, so it is what the
# runner resolves the target from. Anchored on `wrangler d1 execute` rather than a
# bare `javdb-<db>` word: migration prose legitimately mentions sibling databases
# (2026_06_13_add_watch_intent.sql targets javdb-history but discusses
# javdb-operations), and a loose match would read that as an ambiguous target.
_TARGET_DB_RE = re.compile(
    r"wrangler\s+d1\s+execute\s+javdb-(history|reports|operations)\b",
    re.IGNORECASE,
)
_CREATES_LEDGER_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?system_state\b", re.IGNORECASE
)
# Applying schema to D1 only makes sense when D1 is actually in play. Under
# 'sqlite' the local schema comes from init_db()'s DDL constants instead.
_D1_BACKENDS = {"d1", "dual"}

_ERR_INVALID_ID = {
    "error": {
        "code": "migrations.invalid_id",
        "message": "Invalid migration ID",
    }
}


def _err(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def _statements(sql: str) -> list[str]:
    """Split a migration into executable statements, dropping line comments.

    Scans character by character and only treats ``--`` as a comment and ``;``
    as a separator **outside** a single-quoted literal, so neither is mistaken
    for structure when it appears inside one. ``''`` is SQL's escape for a quote
    within a literal and does not end it.

    The earlier version stripped comments by regex and then checked that the
    single quotes came out even. That catches one literal containing ``--`` and
    misses the cases that actually matter: two such literals rebalance the count
    while both have been truncated, and a ``;`` inside a literal splits a
    statement in half without disturbing the quotes at all. Both failure modes
    ship half a statement to production D1.

    Raises ``ValueError`` on an unterminated literal — the one shape that cannot
    be resolved here. No current migration relies on any of this; the scanner
    exists so a future one is split correctly instead of quietly mangled.
    """
    out: list[str] = []
    buf: list[str] = []
    in_literal = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_literal:
            if ch == "'":
                if sql[i + 1:i + 2] == "'":
                    buf.append("''")
                    i += 2
                    continue
                in_literal = False
            buf.append(ch)
        elif ch == "'":
            in_literal = True
            buf.append(ch)
        elif sql[i:i + 2] == "--":
            while i < len(sql) and sql[i] != "\n":
                i += 1
            continue
        elif ch == ";":
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if in_literal:
        raise ValueError(
            "unterminated single-quoted literal — this migration cannot be "
            "split into statements safely"
        )
    out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


def _target_logical_db(sql: str) -> Optional[str]:
    """Resolve which D1 database a migration targets, or None if unresolvable.

    Returns None when no `javdb-<db>` header is present, or when the file names
    more than one database. The caller refuses in that case rather than guess
    which production database to mutate.
    """
    names = {m.group(1).lower() for m in _TARGET_DB_RE.finditer(sql)}
    return names.pop() if len(names) == 1 else None


def _validate_migration_id(migration_id: str) -> None:
    """Reject migration IDs containing path separators or traversal sequences."""
    if not _SAFE_MIGRATION_ID.match(migration_id):
        raise HTTPException(status_code=400, detail=_ERR_INVALID_ID)


def _row_value(row: Any, name: str, index: int) -> Any:
    """Read one column from a sqlite3.Row (name or index) or a D1 dict row (name).

    D1 returns dict rows, so positional access raises KeyError there. Mirrors
    SystemStateRepo's accessor rather than assuming a shape.
    """
    try:
        return row[name]
    except (KeyError, TypeError, IndexError):
        return row[index]


def _ledger_table_missing(exc: BaseException) -> bool:
    """True when *exc* means the system_state ledger table does not exist yet.

    Matches on the message rather than the exception type so it works for both
    sqlite3.OperationalError and the D1 client's error, which surfaces SQLite's
    wording verbatim.
    """
    message = str(exc).lower()
    return "no such table" in message and "system_state" in message


@contextmanager
def _ledger_conn() -> Iterator[Any]:
    """Yield the connection the migration ledger is read from and written to.

    Deliberately the **direct D1 operations connection**, not ``get_db()``.
    Under ``STORAGE_BACKEND=dual`` get_db hands back a DualConnection that
    swallows a failed D1 write when STRICT_DUAL_WRITE is off: the marker would
    land in local SQLite only, this endpoint would report success, and the next
    read from D1 would still not see it — so the next request would replay a
    non-idempotent schema change that has already landed. The ledger has to live
    where the schema it describes lives, and per CLAUDE.md that is D1.
    """
    from javdb.storage.d1_client import make_d1_connection

    conn = make_d1_connection("operations")
    try:
        yield conn
    finally:
        # D1Connection.close() writes a git-tracked port summary; the read-only
        # and single-statement uses here do not need it (see check_d1_schema).
        pass


def _read_applied_migrations() -> dict[str, str]:
    """Query system_state for applied migration timestamps; raises on failure.

    Returns {migration_id: applied_at_timestamp}.

    An *absent* ledger table is not a failure — it is a fresh database where
    nothing has been applied yet. Raising there would make the migration that
    CREATEs system_state impossible to run through this endpoint
    (chicken-and-egg: the guard would refuse before the ledger could exist).
    Every other failure still propagates, so the apply path keeps failing closed
    on a transport or permission problem.
    """
    try:
        with _ledger_conn() as conn:
            rows = conn.execute(
                "SELECT key, value FROM system_state WHERE key LIKE ?",
                (_APPLIED_KEY_PREFIX + "%",),
            ).fetchall()
    except Exception as exc:
        if _ledger_table_missing(exc):
            logger.info(
                "system_state ledger absent; treating as no migrations applied"
            )
            return {}
        raise
    return {
        str(_row_value(row, "key", 0)).removeprefix(_APPLIED_KEY_PREFIX):
            _row_value(row, "value", 1)
        for row in rows
    }


def _get_applied_migrations() -> dict[str, str]:
    """Lenient read for the *listing* only: degrade to "nothing known applied".

    The listing is informational, so an unreadable store should not 500 it. The
    apply path must NOT use this — it calls _read_applied_migrations directly and
    refuses when the state is unknown, because treating unknown as "not applied"
    would replay a non-idempotent migration.
    """
    try:
        return _read_applied_migrations()
    except Exception:
        logger.warning(
            "Could not read applied-migration markers; listing them as unapplied",
            exc_info=True,
        )
        return {}


def _record_applied(migration_id: str, applied_at: str) -> None:
    """Mark a migration as applied, in the same D1 ledger the guard reads.

    Uses :func:`_ledger_conn` rather than ``get_db`` so a dual-mode D1 write
    failure cannot be swallowed into a false success — see that function.
    """
    from javdb.storage.repos.system_state_repo import SystemStateRepo

    with _ledger_conn() as conn:
        SystemStateRepo(conn).put(_APPLIED_KEY_PREFIX + migration_id, applied_at)


def _is_uniqueness_violation(exc: BaseException) -> bool:
    """True when *exc* means the row already existed (PK/UNIQUE collision).

    Matched on the message for the same reason as :func:`_ledger_table_missing`:
    the D1 client surfaces SQLite's wording verbatim rather than a typed error.
    """
    message = str(exc).lower()
    return "unique constraint failed" in message or "must be unique" in message


def _claim_migration(migration_id: str, applied_at: str) -> str:
    """Reserve *migration_id* in the ledger BEFORE its statements run.

    A plain ``INSERT`` (never an upsert) against ``system_state.key``, which is
    the primary key — so two admins racing the same migration resolve in D1, not
    in a read-then-write window this process cannot make atomic. The winner
    proceeds; the loser sees the collision and is turned away as already applied.

    Claiming *first* also removes the "applied but not recorded" outcome: the
    marker is written while it is still cheap to undo, and the failure path
    releases it. If the release itself fails the marker stays and the migration
    reads as applied — the conservative direction, since a stale marker blocks a
    replay whereas a missing one invites it.

    Returns ``"claimed"``, ``"taken"`` (someone else holds it), or ``"no_ledger"``
    when ``system_state`` does not exist yet. That last case is the bootstrap
    migration creating the ledger table itself: there is nothing to claim
    against, so the caller records it after the fact instead.
    """
    try:
        with _ledger_conn() as conn:
            conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES (?, ?, ?)",
                (_APPLIED_KEY_PREFIX + migration_id, applied_at, applied_at),
            )
    except Exception as exc:
        if _is_uniqueness_violation(exc):
            return "taken"
        if _ledger_table_missing(exc):
            return "no_ledger"
        raise
    return "claimed"


def _creates_ledger(sql: str) -> bool:
    """True when *sql* is the migration that CREATEs the ``system_state`` table."""
    return bool(_CREATES_LEDGER_RE.search(sql))


def _http_posts(conn: Any) -> Optional[int]:
    """Requests the D1 access port has actually sent, or None if unreadable.

    This is the only fact that separates "the migration definitely did not run"
    from "it might have". The caller treats None as "assume it was sent", since
    an unreadable counter cannot establish that nothing left the process.

    ``D1AccessPort.summary`` is a *method*, not a property. Subscripting the
    bound method instead raised TypeError into the swallow below, so this always
    returned None against a real connection and the release path was dead in
    production while the tests passed on a fake that exposed a plain dict.
    ``test_http_posts_reads_a_real_port`` now pins it against the real class.
    """
    try:
        return int(conn._port.summary()["http_posts"])
    except Exception:
        return None


def _release_claim(migration_id: str) -> bool:
    """Drop a claim written by :func:`_claim_migration`; False if that failed."""
    from javdb.storage.repos.system_state_repo import SystemStateRepo

    try:
        with _ledger_conn() as conn:
            SystemStateRepo(conn).delete(_APPLIED_KEY_PREFIX + migration_id)
    except Exception:
        logger.error(
            "Could not release the ledger claim for %s; it will read as applied",
            migration_id, exc_info=True,
        )
        return False
    return True


@router.get("/", response_model=MigrationListResponse)
def list_migrations(
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> MigrationListResponse:
    """List all D1 SQL migration files with their applied state."""
    if not _MIGRATIONS_DIR.exists():
        return MigrationListResponse(migrations=[])

    applied = _get_applied_migrations()
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))

    migrations = [
        MigrationItem(
            id=f.stem,
            filename=f.name,
            applied=f.stem in applied,
            applied_at=applied.get(f.stem),
        )
        for f in files
    ]
    return MigrationListResponse(migrations=migrations)


@router.post("/{migration_id}/run", response_model=RunMigrationResponse)
def run_migration(
    migration_id: str,
    body: RunMigrationRequest,
    _user: Dict[str, Any] = Depends(require_role("admin")),
) -> RunMigrationResponse:
    """Preview a migration, or apply it to D1.

    With dry_run=true (the default) returns the SQL and its executable statement
    count, changing nothing. With dry_run=false the migration is applied to the
    D1 database named by its own `wrangler d1 execute javdb-<db>` header line and
    recorded in system_state.

    Refuses before running anything when STORAGE_BACKEND is not d1/dual (409),
    when the migration is already recorded as applied (409 — ALTER TABLE ADD
    COLUMN is not idempotent), when it has no ledger marker at all and the caller
    has not acknowledged that it may already have been applied out of band (409),
    when the target database cannot be resolved unambiguously from the header
    (400), or when the script has more statements than one atomic D1 batch
    holds (400).

    The statements run as a single D1 batch, so they land whole or roll back
    whole. A rejection by D1 returns 502 with nothing applied and the ledger
    claim released. A timeout or dropped connection returns 502 as well but
    keeps the claim, because D1 may have committed and lost the response — the
    outcome is unknown, not rolled back. Claiming the ledger entry before
    execution is also what makes two concurrent applies resolve safely.
    """
    _validate_migration_id(migration_id)
    migration_file = _MIGRATIONS_DIR / f"{migration_id}.sql"

    if not migration_file.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "migrations.not_found",
                    "message": f"Migration '{migration_id}' not found",
                }
            },
        )

    sql = migration_file.read_text()
    try:
        statements = _statements(sql)
    except ValueError as exc:
        # Refuse in dry-run too: the preview's statement count would be just as
        # wrong as the execution, and an operator should see this before relying
        # on either.
        raise HTTPException(
            status_code=400,
            detail=_err(
                "migrations.unparseable",
                f"Cannot safely split '{migration_id}' into statements: {exc}. "
                "Apply it with the Wrangler CLI instead.",
            ),
        ) from exc

    if body.dry_run:
        return RunMigrationResponse(
            migration_id=migration_id,
            dry_run=True,
            sql_preview=sql,
            statements=len(statements),
        )

    # ---- Non-dry-run: apply against D1 ------------------------------------
    from javdb.infra.config import storage_backend

    backend = (storage_backend() or "").strip().lower()
    if backend not in _D1_BACKENDS:
        raise HTTPException(
            status_code=409,
            detail=_err(
                "migrations.backend_not_d1",
                f"STORAGE_BACKEND is '{backend or 'unset'}'; D1 migrations can only "
                "be applied under 'd1' or 'dual'. Local SQLite schema comes from "
                "init_db() instead.",
            ),
        )

    # ALTER TABLE ADD COLUMN is not idempotent — a second run fails with
    # "duplicate column name" partway through. Refuse instead of half-applying.
    # Fail CLOSED: if the marker store cannot be read we do not know whether this
    # already ran, and guessing "not applied" is the dangerous guess.
    try:
        applied = _read_applied_migrations()
    except Exception as exc:
        logger.error("Cannot read applied-migration markers", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=_err(
                "migrations.applied_state_unreadable",
                "Cannot read the applied-migration markers from system_state, so "
                "whether this migration already ran is unknown. Refusing rather "
                f"than risk replaying a non-idempotent migration: {exc}",
            ),
        ) from exc

    if migration_id in applied:
        raise HTTPException(
            status_code=409,
            detail=_err(
                "migrations.already_applied",
                f"Migration '{migration_id}' is already recorded as applied. "
                "Re-running is unsafe for non-idempotent statements; use the "
                "Wrangler CLI directly if you genuinely need to replay it.",
            ),
        )

    from javdb.storage.d1_client import (
        _BATCH_LIMIT,
        D1PermanentError,
        make_d1_connection,
    )

    # The next three checks read only the migration file, so they are free and
    # deterministic — and they run BEFORE the acknowledgement gate on purpose.
    # Behind it, an operator whose file is simply missing a wrangler header would
    # have to resend with acknowledge_unrecorded just to discover a 400, which
    # teaches the habit of always setting the flag and hollows out the guard.

    target_db = _target_logical_db(sql)
    if target_db is None:
        raise HTTPException(
            status_code=400,
            detail=_err(
                "migrations.target_db_unresolved",
                f"Cannot determine which D1 database '{migration_id}' targets. "
                "Add a `wrangler d1 execute javdb-<history|reports|operations>` "
                "line to the file header; the runner will not guess.",
            ),
        )

    if not statements:
        raise HTTPException(
            status_code=400,
            detail=_err(
                "migrations.empty",
                f"Migration '{migration_id}' contains no executable statements.",
            ),
        )

    # A PRAGMA is a no-op inside a transaction, and this runner puts every
    # statement in one. 2026_05_13_session_id_to_text_reports.sql opens with
    # `PRAGMA foreign_keys = OFF` precisely so it can DROP a parent table that
    # child tables reference; batched, the PRAGMA does nothing and the DROP
    # fails on the constraint, taking the whole batch with it. Such a file looks
    # perfectly runnable in the listing, so say why it is not instead of letting
    # it fail at execution with a confusing foreign-key error.
    pragma = next((s for s in statements if s.upper().startswith("PRAGMA")), None)
    if pragma is not None:
        raise HTTPException(
            status_code=400,
            detail=_err(
                "migrations.pragma_unsupported",
                f"Migration '{migration_id}' contains `{pragma[:60]}`. A PRAGMA has "
                "no effect inside a transaction, and this runner applies every "
                "statement as one atomic batch — so a migration that depends on "
                "one cannot work here. Apply it with the Wrangler CLI instead.",
            ),
        )

    # Same root cause, without the tell. The table-rebuild migrations DROP a
    # parent table and recreate it, which only works while foreign keys are off
    # — and this runner cannot turn them off, because the PRAGMA that would is
    # inert inside its batch. 2026_05_13_session_id_to_text_history.sql is the
    # trap: it DROPs MovieHistory while TorrentHistory still REFERENCES it, and
    # it carries no PRAGMA, so the check above waves it through and the batch
    # rolls back on a foreign-key error the operator did not ask about.
    #
    # The refusal is categorical rather than an analysis of each script's
    # reference graph: whether a given DROP has dependents today is a property
    # of the live schema, which this endpoint deliberately does not inspect.
    drop = next((s for s in statements if s.upper().startswith("DROP TABLE")), None)
    if drop is not None:
        raise HTTPException(
            status_code=400,
            detail=_err(
                "migrations.drop_table_unsupported",
                f"Migration '{migration_id}' contains `{drop[:60]}`. Dropping a "
                "table inside this runner's atomic batch cannot be done with "
                "foreign keys relaxed, so any row still referencing it fails the "
                "whole batch. Apply it with the Wrangler CLI, which can disable "
                "foreign keys around the rebuild.",
            ),
        )

    # D1 makes a *batch* atomic, not a connection: one batch either lands whole
    # or rolls back whole, but two batches are two transactions. Above the batch
    # limit the runner could only chunk, and a chunked migration is exactly the
    # half-applied schema this endpoint exists to avoid — e.g. a table dropped in
    # chunk 1 with its replacement renamed in chunk 2. Refuse and let Wrangler,
    # which the operator can supervise statement by statement, handle it.
    if len(statements) > _BATCH_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=_err(
                "migrations.not_atomic",
                f"Migration '{migration_id}' has {len(statements)} statements, over "
                f"D1's {_BATCH_LIMIT}-statement batch limit, so it cannot be applied "
                "atomically. Apply it with the Wrangler CLI instead.",
            ),
        )

    # Absence from the ledger does NOT mean "not applied". The ledger only ever
    # records what this endpoint applied, and every historical migration was
    # applied with the Wrangler CLI, which writes no marker — so an established
    # database legitimately has 40+ applied migrations and no markers at all.
    #
    # Gating this on an *empty* ledger would close the hole for exactly one
    # request: as soon as the first API apply writes a marker, every remaining
    # wrangler-applied file would silently lose the speed bump while still
    # listing as unapplied. Some of those files are destructive — the
    # 2026_05_13_session_id_to_text_* set rebuilds tables from a hardcoded column
    # list and DROPs the originals, so replaying one today would discard every
    # column added since. The guard is therefore per-migration: unrecorded means
    # unknown, and unknown must be answered by a human, once, per file.
    #
    # This is not friction on the normal path. A migration this endpoint applied
    # is stopped above by `already_applied` and never reaches here, so the flag
    # is only ever demanded where the question is genuinely unanswerable.
    if not body.acknowledge_unrecorded:
        raise HTTPException(
            status_code=409,
            detail=_err(
                "migrations.unrecorded",
                f"Migration '{migration_id}' has no marker in the ledger, which "
                "means 'not applied through this endpoint' — not 'not applied'. "
                "It may already have landed via the Wrangler CLI, which records "
                "nothing, and replaying a non-idempotent migration can drop data. "
                "Check it against the live schema, then resend with "
                '{"dry_run": false, "acknowledge_unrecorded": true}.',
            ),
        )

    applied_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    # Claim before executing so concurrent admins serialize on D1's primary key
    # rather than on a read-then-write window (see _claim_migration).
    try:
        claim = _claim_migration(migration_id, applied_at)
    except Exception as exc:
        logger.error("Could not claim migration %s", migration_id, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=_err(
                "migrations.claim_failed",
                f"Could not reserve '{migration_id}' in the ledger, so a concurrent "
                f"apply cannot be ruled out. Nothing was executed: {exc}",
            ),
        ) from exc

    if claim == "taken":
        raise HTTPException(
            status_code=409,
            detail=_err(
                "migrations.already_applied",
                f"Migration '{migration_id}' was claimed by a concurrent request. "
                "Nothing was executed here; check that run's outcome.",
            ),
        )

    # Only the migration that CREATEs system_state may proceed unclaimed. On a
    # fresh operations database every migration gets "no_ledger", and running an
    # ordinary one there would execute with no concurrency protection at all —
    # two requests could apply the same non-idempotent script — and then fail at
    # _record_applied anyway, because the ledger it records into still does not
    # exist. Bootstrap the ledger first; after that every migration is claimable.
    if claim == "no_ledger" and not _creates_ledger(sql):
        raise HTTPException(
            status_code=409,
            detail=_err(
                "migrations.ledger_absent",
                "The system_state ledger does not exist yet, so this migration "
                "cannot be claimed and would run without concurrency protection. "
                "Apply the migration that creates system_state first "
                "(0042_system_state_table), then re-run this one.",
            ),
        )

    # Built in its own try so a client-construction failure — missing D1
    # credentials being the likely one — is never mistaken for a batch whose
    # outcome is unknown. Nothing was sent, so the claim is released and the
    # migration goes back to listing as unapplied.
    try:
        conn = make_d1_connection(target_db)
    except Exception as exc:
        released = _release_claim(migration_id) if claim == "claimed" else True
        logger.error(
            "Could not open a D1 connection to javdb-%s for %s",
            target_db, migration_id, exc_info=True,
        )
        raise HTTPException(
            status_code=503,
            detail=_err(
                "migrations.connection_failed",
                f"Could not open a connection to javdb-{target_db}, so nothing "
                f"was executed: {exc}."
                + (
                    ""
                    if released
                    else " Its ledger claim could NOT be released, so it will list "
                    "as applied — clear the marker before retrying."
                ),
            ),
        ) from exc

    # Whether the claim may be released comes down to one question the port can
    # answer: did a request actually leave this process? `http_posts` counts
    # them, so sampling it around the call separates three cases that all arrive
    # here as exceptions.
    #
    #   0 requests  — nothing was sent (an open circuit rejects in
    #                 breaker.acquire() *before* _post), so the migration
    #                 definitively did not run. Safe to release.
    #   1 request, rejected by D1 — the batch is atomic and D1 said no, so
    #                 nothing landed. Safe to release.
    #   anything else — a request went out and its fate is unknown. The
    #                 transport retries transient failures on its own and a
    #                 migration batch is not idempotent, so a first attempt that
    #                 committed and lost its response yields a second attempt
    #                 rejected with "duplicate column name" — a permanent error
    #                 describing the retry, not the migration. Keep the claim.
    #
    # An unreadable counter cannot establish any of this, so it keeps the claim.
    posts_before = _http_posts(conn)

    try:
        cursors = conn.batch_execute([(statement, []) for statement in statements])
    except Exception as exc:
        posts_after = _http_posts(conn)
        sent = (
            None
            if posts_before is None or posts_after is None
            else posts_after - posts_before
        )
        nothing_landed = sent == 0 or (
            sent == 1 and isinstance(exc, D1PermanentError)
        )

        if not nothing_landed:
            # The claim stays. The migration reads as applied, which blocks a
            # replay; if it turns out not to have landed, clearing one marker is
            # a cheap manual step, whereas an unrecoverable table rebuild is not.
            logger.error(
                "Migration %s against javdb-%s left an UNKNOWN outcome "
                "(%d statement(s), %s request(s) sent)",
                migration_id, target_db, len(statements),
                "unknown" if sent is None else sent, exc_info=True,
            )
            raise HTTPException(
                status_code=502,
                detail=_err(
                    "migrations.outcome_unknown",
                    f"The {len(statements)}-statement batch against "
                    f"javdb-{target_db} did not report a trustworthy outcome: "
                    f"{exc}. A request went out and may have committed before "
                    "the response was lost, so this is NOT a rollback. The "
                    f"ledger claim was kept, so '{migration_id}' now reads as "
                    "applied and cannot be replayed by accident. Check the live "
                    "schema: if the change did land, leave the marker; if it did "
                    "not, clear it and re-run.",
                ),
            ) from exc

        released = _release_claim(migration_id) if claim == "claimed" else True
        logger.error(
            "Migration %s did not run against javdb-%s (%d statement(s), %d "
            "request(s) sent)",
            migration_id, target_db, len(statements), sent, exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=_err(
                "migrations.execution_failed",
                (
                    f"No request reached javdb-{target_db}, so none of the "
                    f"{len(statements)} statements ran: {exc}."
                    if sent == 0
                    else f"The {len(statements)}-statement batch was rejected by "
                    f"javdb-{target_db} and rolled back whole; no schema change "
                    f"landed: {exc}."
                )
                + (
                    ""
                    if released
                    else " Its ledger claim could NOT be released, so it will list "
                    "as applied — clear the marker before retrying."
                ),
            ),
        ) from exc

    # No exception is not the same as evidence of success. D1 returns one cursor
    # per statement, and `execute` already refuses a success response with an
    # empty result list — but `batch_execute` does not, so a proxy that answers
    # `success: true` with no (or a truncated) `result` would land here looking
    # like a clean apply. Reporting applied: true on that would keep a marker
    # that blocks the retry with nothing to show for it, so the count is checked
    # and a mismatch is treated as what it is: an unknown outcome.
    returned = len(cursors) if cursors is not None else 0
    if returned != len(statements):
        logger.error(
            "Migration %s against javdb-%s returned %d cursor(s) for %d "
            "statement(s); outcome UNKNOWN",
            migration_id, target_db, returned, len(statements),
        )
        raise HTTPException(
            status_code=502,
            detail=_err(
                "migrations.outcome_unknown",
                f"javdb-{target_db} reported success for the "
                f"{len(statements)}-statement batch but returned {returned} "
                "result(s), so there is no evidence the statements ran. The "
                f"ledger claim was kept, so '{migration_id}' now reads as applied "
                "and cannot be replayed by accident. Check the live schema: if "
                "the change did land, leave the marker; if it did not, clear it "
                "and re-run.",
            ),
        )

    if claim == "no_ledger":
        # The bootstrap migration just CREATEd system_state, so there was nothing
        # to claim against beforehand. Record it now that the table exists.
        try:
            _record_applied(migration_id, applied_at)
        except Exception:
            logger.error(
                "Migration %s applied but not recorded", migration_id, exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail=_err(
                    "migrations.record_failed",
                    f"Migration '{migration_id}' was applied to javdb-{target_db} "
                    f"({len(statements)} statement(s)) but recording it in "
                    "system_state failed. Do NOT re-run it; record it manually.",
                ),
            )

    logger.info(
        "Applied migration %s to javdb-%s (%d statement(s))",
        migration_id, target_db, len(statements),
    )
    return RunMigrationResponse(
        migration_id=migration_id,
        dry_run=False,
        sql_preview=sql,
        statements=len(statements),
        applied=True,
    )


__all__ = [
    "list_migrations",
    "run_migration",
    "router",
]

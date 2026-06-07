"""In-process pipeline harness (ADR-037). Composes FixtureHTTP + FakeQB + seeded DB.

Drives the real daily pipeline in-process — spider -> uploader -> commit — by
monkeypatching the load-bearing seams (HTTP, qB) per ADR-037 D1; the DB uses the
autouse ``_isolate_sqlite`` fixture. The uploader's connection/login probes are
neutered so the injected ``FakeQB`` (at ``_wrap_session_as_client``) is the only
qB it ever sees. The session id + CSV path are taken from the spider's returned
``SpiderRunResult`` because ``run_spider`` clears the active-session context in
its ``finally`` block before returning.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

import javdb.infra.config as _cfg_mod
from javdb.infra.request import RequestHandler
import javdb.integrations.qb.uploader.service as up_service
from tests.harness.fake_qb import FakeQB
from tests.harness.fixture_http import FixtureHTTP


@dataclass
class FakeQBConfig:
    initial: tuple = ()
    fail_adds: bool = False


@dataclass
class PipelineScenario:
    pages: dict
    qb: FakeQBConfig = field(default_factory=FakeQBConfig)


class HistoryView:
    # get_db(db_path=None) takes a *path*, not a logical name, and defaults to
    # HISTORY_DB_PATH — which the autouse _isolate_sqlite points at the one
    # seeded test DB (all three logical DBs share it). So call it with no arg.
    def __init__(self) -> None:
        from javdb.storage.db import get_db
        self._get_db = get_db

    def count(self) -> int:
        with self._get_db() as conn:
            return conn.execute("SELECT COUNT(*) FROM MovieHistory").fetchone()[0]


class HarnessResult:
    def __init__(self, fake_qb, http, spider_result, uploader_result, commit_result,
                 commit_error=None):
        self.qb = fake_qb
        self.http = http
        self.spider_result = spider_result
        self.uploader_result = uploader_result
        self.commit_result = commit_result
        # Set when the ADR-035 commit gate refused the commit (critical drift).
        self.commit_error = commit_error


class PipelineHarness:
    def __init__(self, monkeypatch, tmp_path) -> None:
        self._mp = monkeypatch
        self._tmp_path = tmp_path
        self.http: FixtureHTTP | None = None
        self.qb: FakeQB | None = None
        self.smtp = None

    def _install(self, scenario: PipelineScenario) -> None:
        self.http = FixtureHTTP(scenario.pages)
        self.qb = FakeQB(fail_adds=scenario.qb.fail_adds)
        for magnet in scenario.qb.initial:
            self.qb.add_torrent(magnet)
        # HTTP seam — the spider fetch routes through RequestHandler.get_page.
        self._mp.setattr(RequestHandler, "get_page",
                         lambda _self, url, *a, **k: self.http.get_page(url, *a, **k))
        # qB seams — FakeQB is the only qB the uploader sees. The connection /
        # login probes run before _wrap_session_as_client, so they must be
        # neutered or run_uploader returns 'qb-unreachable' before any add.
        self._mp.setattr(up_service, "test_qbittorrent_connection",
                         lambda use_proxy=False: True)
        self._mp.setattr(up_service, "login_to_qbittorrent",
                         lambda session, use_proxy=False: True)
        self._mp.setattr(up_service, "_wrap_session_as_client",
                         lambda session, use_proxy=False: self.qb)
        # Storage mode 'duo' — the autouse _isolate_sqlite forces 'db' (no CSV),
        # but the uploader consumes the spider's dated output CSV, so the
        # end-to-end path needs CSV writes on too. 'duo' mirrors production
        # (both SQLite pending writes AND CSV output).
        self._mp.setattr(_cfg_mod, "_storage_mode_override", "duo")
        # Determinism > wall-clock: neuter the real-time throttles (the spider's
        # per-movie / phase-transition cooldowns and the uploader's inter-add
        # delay). Patch the class method so both the module-global and the
        # runtime-bound sleep managers are covered.
        from javdb.spider.runtime.sleep import MovieSleepManager
        self._mp.setattr(MovieSleepManager, "sleep", lambda _self: 0.0)
        self._mp.setattr(up_service, "DELAY_BETWEEN_ADDITIONS", 0)
        # Keep CSV + report artifacts inside the test's tmp dir; the spider
        # writes reports/DailyReport/... relative to the working directory.
        self._mp.chdir(self._tmp_path)

    def _daily_options(self):
        from javdb.spider.app.options import SpiderRunOptions
        # Deterministic clean daily run: all filters off so authored fixtures
        # are neither history- nor date- nor rclone-filtered.
        return SpiderRunOptions(
            mode="daily", url=None, start_page=1, end_page=1, parse_all=False,
            ignore_history=True, phase="all", output_file="harness_daily.csv",
            dry_run=False, ignore_release_date=True, use_proxy=False, no_proxy=True,
            always_bypass_time=None, enable_dedup=False, enable_redownload=None,
            redownload_threshold=None, result_json=None, use_history=False,
            from_pipeline=True, max_movies_phase1=None, max_movies_phase2=None,
            sequential=True, no_rclone_filter=True, disable_all_filters=True,
            cancel_event=None,
        )

    def run_daily(self, scenario: PipelineScenario, *, before_commit=None) -> HarnessResult:
        from javdb.spider.app.run_service import run_spider
        from javdb.integrations.qb.uploader.options import QbUploaderOptions
        from javdb.integrations.qb.uploader.service import run_uploader
        from javdb.storage.sessions.commit import (
            CommitRequest, SiteContractDriftError, commit_session,
        )

        self._install(scenario)

        # 1) Spider — fetches the cassette, stages pending history, returns the
        #    session id + CSV path (the active-session context is cleared on exit).
        spider_result = run_spider(self._daily_options())
        session_id = spider_result.session_id
        csv_path = spider_result.csv_path

        # 2) Uploader — reads the spider CSV, queues magnets into FakeQB.
        uploader_result = run_uploader(QbUploaderOptions(
            mode="daily", input_file=csv_path, proxy_override=False,
            from_pipeline=True, session_id=session_id,
        ))

        # ADR-037 Phase 2: optional hook to mutate state after staging but before
        # the commit gate (e.g. seed sentinel drift fills for the drift scenario).
        if before_commit is not None and session_id:
            before_commit(session_id)

        # 3) Commit — drains pending writes into MovieHistory / TorrentHistory.
        #    Gate on spider AND uploader success, mirroring DailyIngestion.yml's
        #    "Mark sessions as committed" step (if: ${{ success() }}). A critical
        #    site-contract drift verdict raises SiteContractDriftError before any
        #    drain; production's CLI commit routes that to a failed session, so the
        #    harness records it (commit_error) and leaves pending rows un-promoted.
        commit_result = None
        commit_error = None
        spider_ok = spider_result.exit_code == 0
        uploader_ok = uploader_result.exit_code == 0
        if session_id and spider_ok and uploader_ok:
            try:
                commit_result = commit_session(CommitRequest(session_id=session_id))
            except SiteContractDriftError as exc:
                commit_error = exc

        return HarnessResult(self.qb, self.http, spider_result, uploader_result,
                             commit_result, commit_error)

    def history(self) -> HistoryView:
        return HistoryView()

    def events(self) -> list[str]:
        # PipelineEvent lives in the *reports* DB (ADR-036), not history. A bare
        # get_db() defaults to HISTORY_DB_PATH, hits 'no such table' (swallowed
        # below) and would always return []. Resolve REPORTS_DB_PATH via the
        # module attribute at call time so the test suite's path monkeypatch is
        # honoured (mirrors apps/cli/ops/events.py).
        import sqlite3
        from javdb.storage import db as _db
        from javdb.storage.db import get_db
        try:
            with get_db(_db.REPORTS_DB_PATH) as conn:
                # ORDER BY seq (the AUTOINCREMENT PK) — without it SQLite does not
                # guarantee insertion order, so a multi-event run could yield a
                # different sequence and cause spurious golden-snapshot drift.
                rows = conn.execute(
                    "SELECT event_type FROM PipelineEvent ORDER BY seq"
                ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            return []  # PipelineEvent table only exists when ADR-036 is built

    def acquisition_outcomes(self) -> list[dict]:
        # AcquisitionOutcome lives in the *operations* DB (ADR-033), not history.
        # Same get_db() default trap as events() — read through OPERATIONS_DB_PATH.
        import sqlite3
        from javdb.storage import db as _db
        from javdb.storage.db import get_db
        try:
            with get_db(_db.OPERATIONS_DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT qb_hash, state FROM AcquisitionOutcome").fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []  # AcquisitionOutcome only exists when ADR-033 is built

    def reconcile(self, *, qb_client=None, categories=None,
                  infer_absent: bool = False):
        """Run the real ADR-033 closed-loop reconciler in-process.

        Defaults to reconciling against this harness's FakeQB, scoped to the
        categories the uploader actually used (so it is config-independent).
        ``infer_absent`` defaults to False for determinism: a torrent absent
        from the (fake) qB read must not be inferred stalled/failed here."""
        from javdb.ops.reconcile.models import ReconcileOptions
        from javdb.ops.reconcile.service import run as reconcile_run

        client = qb_client if qb_client is not None else self.qb
        cats = tuple(categories) if categories is not None else tuple(client.categories())
        return reconcile_run(
            ReconcileOptions(sources=("qb",), categories=cats, infer_absent=infer_absent),
            qb_client=client,
        )

    def run_notify(self, csv_path, session_id, *, dry_run: bool = False):
        """Run the real daily email-notification flow with SMTP faked.

        Patches the notify module's send_email to a FakeSMTP (captured on
        ``self.smtp``) and returns the EmailNotificationResult. Git side-effects
        are already neutered by the autouse _disable_git_side_effects fixture."""
        import javdb.integrations.notify.email.service as notify_service
        from javdb.integrations.notify.email.options import EmailNotificationOptions
        from javdb.integrations.notify.email.service import run_email_notification
        from tests.harness.fake_smtp import FakeSMTP

        self.smtp = FakeSMTP()
        self._mp.setattr(
            notify_service, "send_email",
            lambda subject, body, attachments=None, dry_run=False, **kwargs:
                self.smtp.send_email(subject, body, attachments, dry_run),
        )
        return run_email_notification(EmailNotificationOptions(
            csv_path=csv_path, mode="daily", dry_run=dry_run,
            from_pipeline=True, session_id=session_id,
        ))


@pytest.fixture
def pipeline_harness(monkeypatch, tmp_path):
    return PipelineHarness(monkeypatch, tmp_path)

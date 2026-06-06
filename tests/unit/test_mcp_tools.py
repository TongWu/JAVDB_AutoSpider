import javdb.storage.db as _db
from apps.mcp.tools.observe import tool_get_capabilities, tool_get_session, tool_list_incidents, tool_get_incident, tool_query_events, tool_list_runs, tool_search_history
from apps.mcp.tools.diagnose import tool_diagnose_run


def test_get_capabilities_returns_dict():
    caps = tool_get_capabilities()
    assert isinstance(caps, dict)
    # build_capabilities always includes a deployment/version-ish field
    assert caps  # non-empty


def test_get_session_unknown_returns_not_found():
    out = tool_get_session("nonexistent-session-id")
    assert out["found"] is False


def test_get_session_found_returns_session_fields():
    # Insert a minimal ReportSessions row into the isolated test DB.
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO ReportSessions (Id, ReportType, ReportDate, CsvFilename, DateTimeCreated) "
            "VALUES (?, ?, ?, ?, ?)",
            ("test-session-001", "daily", "2026-06-06", "test.csv", "2026-06-06T00:00:00Z"),
        )

    out = tool_get_session("test-session-001")
    assert out["found"] is True
    assert out["session_id"] == "test-session-001"
    assert isinstance(out["session"], dict)
    # _as_dict used dataclasses.asdict -> real fields, NOT a {"value": str(...)} stub
    assert out["session"]["session_id"] == "test-session-001"
    assert out["session"]["state"] == "in_progress"   # ReportSessions.Status DEFAULTs to 'in_progress'
    assert "value" not in out["session"]               # guard against the stub fallback
    assert out["movie_writes"] == 0
    assert out["torrent_writes"] == 0


def test_list_incidents_returns_list():
    assert isinstance(tool_list_incidents(limit=5), list)


def test_get_incident_unknown_returns_none():
    assert tool_get_incident("nonexistent-incident") is None


def test_query_events_available_and_lists_inserted_event():
    # Empty isolated DB: table present, no rows yet.
    baseline = tool_query_events(limit=10)
    assert baseline["available"] is True
    assert baseline["events"] == []

    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute(
            "INSERT INTO PipelineEvent (session_id, event_type, entity_type, entity_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("sess-1", "movie_seen", "movie", "ABC-123", "2026-06-06T00:00:00Z"),
        )

    out = tool_query_events(limit=10)
    assert out["available"] is True
    assert len(out["events"]) == 1
    ev = out["events"][0]
    assert ev["event_type"] == "movie_seen"
    assert ev["entity_type"] == "movie"
    assert ev["entity_id"] == "ABC-123"
    assert ev["created_at"] == "2026-06-06T00:00:00Z"
    assert isinstance(ev["seq"], int)

    # session-filtered query also finds it
    filtered = tool_query_events(session_id="sess-1", limit=10)
    assert filtered["available"] is True
    assert len(filtered["events"]) == 1
    # filtering by a different session returns nothing
    assert tool_query_events(session_id="other", limit=10)["events"] == []


def test_query_events_degrades_when_table_absent():
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS PipelineEvent")
    out = tool_query_events(limit=10)
    assert out["available"] is False
    assert "reason" in out


def test_query_events_real_error_not_mislabeled_as_absent():
    # A genuine schema error (here: missing columns -> "no such column") must
    # surface an honest reason, NOT be mislabeled as "table not built".
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS PipelineEvent")
        conn.execute(
            "CREATE TABLE PipelineEvent (seq INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT)"
        )
    out = tool_query_events(limit=10)
    assert out["available"] is False
    assert "not built" not in out["reason"]      # real error must not be mislabeled
    assert "PipelineEvent" in out["reason"]


def test_diagnose_run_returns_structured_result():
    out = tool_diagnose_run(workflow_name="DailyIngestion", workflow_result="failure")
    assert isinstance(out, dict)
    assert "incident_type" in out or "error" in out


def test_list_runs_returns_dict():
    out = tool_list_runs(limit=5)
    assert isinstance(out, dict)
    assert "tasks" in out or "error" in out


def test_search_history_returns_dict():
    out = tool_search_history(q="ABC-123", limit=5)
    assert isinstance(out, dict)
    # empty isolated MovieHistory -> items present (possibly []), or a graceful error
    assert "items" in out or "error" in out


def test_search_history_empty_db_returns_empty_items():
    out = tool_search_history(q="nonexistent-code", limit=5)
    # The autouse fixture gives an empty-but-real MovieHistory schema.
    assert out.get("items") == []
    assert out.get("total") == 0


def test_diagnose_run_degrades_on_unexpected_error(monkeypatch):
    # A TypeError raised deep in the flow must surface honestly via the broad
    # handler — NOT be mislabeled as an adapter/ADR-026 "signature mismatch".
    def _boom(*args, **kwargs):
        raise TypeError("detector exploded")
    monkeypatch.setattr("javdb.ops.diagnosis.service.run_diagnosis", _boom)
    out = tool_diagnose_run(workflow_name="DailyIngestion", workflow_result="failure")
    assert out["error"] == "diagnosis failed"
    assert "detector exploded" in out.get("detail", "")
    assert "signature mismatch" not in out.get("detail", "")


def test_diagnose_run_is_read_only_and_returns_findings():
    import javdb.storage.db as _db
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        before = conn.execute("SELECT COUNT(*) FROM OpsIncidents").fetchone()[0]
    out = tool_diagnose_run(workflow_name="DailyIngestion", workflow_result="failure")
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        after = conn.execute("SELECT COUNT(*) FROM OpsIncidents").fetchone()[0]
    assert after == before  # read-only: diagnose_run must NOT persist an incident
    assert out.get("persistence_status") == "not_written"
    assert out["incident_type"]
    for key in ("confirmed_findings", "likely_causes", "unknowns",
                "recommended_next_actions", "unsafe_actions", "evidence_refs"):
        assert isinstance(out[key], list)


def test_query_events_reads_dict_rows_like_d1(monkeypatch):
    import contextlib
    import apps.mcp.tools.observe as obs
    dict_rows = [{"seq": 7, "event_type": "movie_seen", "entity_type": "movie",
                  "entity_id": "ABC-1", "created_at": "2026-06-06T00:00:00Z"}]

    class _FakeConn:
        def execute(self, *a, **k):
            class _R:
                def fetchall(self_inner):
                    return dict_rows
            return _R()

    @contextlib.contextmanager
    def _fake_get_db(_path):
        yield _FakeConn()

    monkeypatch.setattr("javdb.storage.db.get_db", _fake_get_db)
    out = obs.tool_query_events(limit=10)
    assert out["available"] is True
    assert out["events"][0]["seq"] == 7
    assert out["events"][0]["event_type"] == "movie_seen"
    assert out["events"][0]["entity_id"] == "ABC-1"


def test_get_incident_returns_full_detail():
    import javdb.storage.db as _db
    from javdb.ops.diagnosis.collectors import collect_incident_bundle
    from javdb.ops.diagnosis.service import run_diagnosis
    from javdb.storage.repos.ops_incident_repo import OpsIncidentRepo
    rec = run_diagnosis(collect_incident_bundle(
        trigger_source="test", workflow_name="DailyIngestion", workflow_result="failure"))
    with _db.get_db(_db.REPORTS_DB_PATH) as conn:
        OpsIncidentRepo(conn).upsert(rec)
    out = tool_get_incident(rec.incident_id)
    assert out is not None
    assert out["incident_id"] == rec.incident_id
    # full detail, not the 6-field summary:
    for key in ("trigger_source", "run_id", "confirmed_findings", "likely_causes",
                "unknowns", "recommended_next_actions", "unsafe_actions",
                "evidence_refs", "created_at"):
        assert key in out

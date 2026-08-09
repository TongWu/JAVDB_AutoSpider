"""ADR-055: the fragment registry is well-formed and queryable."""
from javdb.storage.contract import fragments
from javdb.storage.contract.types import SqlFragment, normalize_sql


def test_registry_is_nonempty_tuple_of_fragments():
    assert fragments.FRAGMENTS
    assert all(isinstance(f, SqlFragment) for f in fragments.FRAGMENTS)


def test_fragment_names_unique():
    names = [f.name for f in fragments.FRAGMENTS]
    assert len(names) == len(set(names))


def test_param_count_matches_placeholders():
    for f in fragments.FRAGMENTS:
        assert normalize_sql(f.sql).count("?") == len(f.params), f.name


def test_watch_intent_upsert_shape():
    f = fragments.WATCH_INTENT_UPSERT
    assert f.name == "watch_intent_upsert"
    assert f.db == "history"
    assert [p.name for p in f.params] == ["video_code", "href", "status", "notes"]
    norm = normalize_sql(f.sql)
    assert norm.startswith("INSERT INTO WatchIntent")
    assert "ON CONFLICT(video_code) DO UPDATE SET" in norm
    assert "notes = COALESCE(excluded.notes, notes)" in norm


def test_migrated_static_mirrors_are_registry_owned():
    assert fragments.ACTOR_SUBSCRIPTION_UPSERT.db == "history"
    assert fragments.SYSTEM_STATE_UPSERT.db == "operations"

    sub = normalize_sql(fragments.ACTOR_SUBSCRIPTION_UPSERT.sql)
    assert sub.startswith("INSERT INTO ActorSubscription")
    assert "ON CONFLICT(actor_href) DO UPDATE SET" in sub
    assert [p.name for p in fragments.ACTOR_SUBSCRIPTION_UPSERT.params] == [
        "actor_href",
        "actor_name",
        "active",
    ]

    state = normalize_sql(fragments.SYSTEM_STATE_UPSERT.sql)
    assert state.startswith("INSERT INTO system_state")
    assert "ON CONFLICT(key) DO UPDATE SET" in state
    assert [p.name for p in fragments.SYSTEM_STATE_UPSERT.params] == ["key", "value"]


def test_shared_constants_include_existing_worker_contract_exports():
    constants = {item.name: item for item in fragments.CONSTANTS}

    assert set(constants) >= {
        "ops_alert_policy_id_salt",
        "ops_alert_policy_id_prefix",
        "ops_alert_policy_id_hash_length",
        "valid_rule_modes",
        "value_required",
        "report_session_columns",
    }
    assert constants["ops_alert_policy_id_salt"].kind == "string"
    assert constants["ops_alert_policy_id_salt"].values == "alertpolicy|"
    assert constants["ops_alert_policy_id_prefix"].kind == "string"
    assert constants["ops_alert_policy_id_prefix"].values == "opspolicy_"
    assert constants["ops_alert_policy_id_hash_length"].kind == "number"
    assert constants["ops_alert_policy_id_hash_length"].values == 24
    assert constants["valid_rule_modes"].kind == "string_set"
    assert "actor:exclude" in constants["valid_rule_modes"].values
    assert constants["value_required"].kind == "string_set"
    assert "gender:exclude_all_male" not in constants["value_required"].values
    assert constants["report_session_columns"].kind == "string_array"
    assert constants["report_session_columns"].values == (
        "Id",
        "Status",
        "WriteMode",
        "RunId",
        "RunAttempt",
        "DateTimeCreated",
        "ReportType",
        "ReportDate",
        "FailureReason",
    )


def test_ops_alert_fragments_use_reports_db_and_generated_static_sql():
    assert fragments.OPS_ALERT_POLICY_UPSERT.db == "reports"
    assert fragments.OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE.db == "reports"
    assert fragments.OPS_ALERT_POLICIES_LIST.db == "reports"
    assert fragments.OPS_ALERT_EVENTS_LIST_BY_INCIDENT.db == "reports"
    assert fragments.OPS_ALERT_POLICY_PROBE.db == "reports"
    assert fragments.OPS_ALERT_EVENT_PROBE.db == "reports"

    upsert = fragments.OPS_ALERT_POLICY_UPSERT
    assert [p.name for p in upsert.params] == [
        "policy_id",
        "incident_type",
        "min_confidence",
        "enabled",
        "channels_json",
        "updated_by",
    ]
    norm = normalize_sql(upsert.sql)
    assert norm.startswith("INSERT INTO OpsAlertPolicy")
    assert "ON CONFLICT(incident_type) DO UPDATE SET" in norm
    assert "created_at" in norm
    assert "created_at = excluded.created_at" not in norm
    assert "strftime('%Y-%m-%dT%H:%M:%fZ','now')" in norm

    assert "FROM OpsAlertPolicy WHERE incident_type = ?" in normalize_sql(
        fragments.OPS_ALERT_POLICY_GET_BY_INCIDENT_TYPE.sql
    )
    assert "FROM OpsAlertPolicy ORDER BY incident_type ASC" in normalize_sql(
        fragments.OPS_ALERT_POLICIES_LIST.sql
    )
    assert "FROM OpsAlertEvent WHERE incident_id = ? ORDER BY fired_at ASC" in normalize_sql(
        fragments.OPS_ALERT_EVENTS_LIST_BY_INCIDENT.sql
    )
    assert normalize_sql(fragments.OPS_ALERT_POLICY_PROBE.sql) == (
        "SELECT 1 FROM OpsAlertPolicy LIMIT 1"
    )
    assert normalize_sql(fragments.OPS_ALERT_EVENT_PROBE.sql) == (
        "SELECT 1 FROM OpsAlertEvent LIMIT 1"
    )

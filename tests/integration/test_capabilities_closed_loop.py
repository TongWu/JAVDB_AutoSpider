import pytest

from apps.api.routers.capabilities import build_capabilities


@pytest.mark.usefixtures("_isolate_sqlite")
def test_capabilities_exposes_closed_loop_bool(admin_client):
    r = admin_client.get("/api/capabilities")
    assert r.status_code == 200
    features = r.json()["features"]
    assert "closed_loop" in features
    assert isinstance(features["closed_loop"], bool)
    # _isolate_sqlite creates the AcquisitionOutcome table via init_db → true
    assert features["closed_loop"] is True


@pytest.mark.usefixtures("_isolate_sqlite")
def test_capabilities_exposes_library_flags_bool(admin_client):
    r = admin_client.get("/api/capabilities")
    assert r.status_code == 200
    features = r.json()["features"]
    for key in ("library_ownership", "library_consumption"):
        assert key in features
        assert isinstance(features[key], bool)
    # _isolate_sqlite creates OwnershipLedger + ConsumptionSignal via init_db → true
    assert features["library_ownership"] is True
    assert features["library_consumption"] is True


@pytest.mark.usefixtures("_isolate_sqlite")
def test_library_ownership_flag_false_when_table_absent():
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    with get_db(OPERATIONS_DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS OwnershipLedger")
    assert build_capabilities().features.library_ownership is False


@pytest.mark.usefixtures("_isolate_sqlite")
def test_library_consumption_flag_false_when_table_absent():
    from javdb.storage.db import OPERATIONS_DB_PATH, get_db
    with get_db(OPERATIONS_DB_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS ConsumptionSignal")
    assert build_capabilities().features.library_consumption is False

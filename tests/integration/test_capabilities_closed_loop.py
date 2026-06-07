import pytest


@pytest.mark.usefixtures("_isolate_sqlite")
def test_capabilities_exposes_closed_loop_bool(admin_client):
    r = admin_client.get("/api/capabilities")
    assert r.status_code == 200
    features = r.json()["features"]
    assert "closed_loop" in features
    assert isinstance(features["closed_loop"], bool)
    # _isolate_sqlite creates the AcquisitionOutcome table via init_db → true
    assert features["closed_loop"] is True

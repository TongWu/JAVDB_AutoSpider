import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client():
    from apps.api.services.runtime import app, _jwt_encode
    token = _jwt_encode({"sub": "admin", "role": "admin", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
    })
    return client


@pytest.fixture
def readonly_client():
    from apps.api.services.runtime import app, _jwt_encode
    token = _jwt_encode({"sub": "viewer", "role": "readonly", "typ": "access"}, 3600)
    csrf = "test-csrf-value"
    client = TestClient(app, cookies={"csrf_token": csrf})
    client.headers.update({
        "Authorization": f"Bearer {token}",
        "X-CSRF-Token": csrf,
    })
    return client


@pytest.fixture
def anon_client():
    from apps.api.services.runtime import app
    return TestClient(app)

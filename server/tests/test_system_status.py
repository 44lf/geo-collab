from fastapi.testclient import TestClient

from server.app.main import app


def test_system_status_returns_runtime_info():
    client = TestClient(app)

    response = client.get("/api/system/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"] == "ok"
    assert payload["directories_ready"] is True
    assert payload["data_dir"]
    assert payload["database_path"].endswith("geo.db")


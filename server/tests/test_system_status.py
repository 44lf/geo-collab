from server.tests.utils import build_test_app


def test_system_status_returns_runtime_info(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        response = client.get("/api/system/status")

        assert response.status_code == 200
        payload = response.json()
        assert payload["service"] == "ok"
        assert payload["directories_ready"] is True
        assert payload["data_dir"]
        assert payload["database_path"]
        assert payload["database_path"] != ""
    finally:
        test_app.cleanup()


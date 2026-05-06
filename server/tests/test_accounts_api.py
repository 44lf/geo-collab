import json
import zipfile
from io import BytesIO

from server.app.models import Account
from server.tests.utils import build_test_app


def write_storage_state(data_dir, account_key: str = "demo") -> None:
    state_dir = data_dir / "browser_states" / "toutiao" / account_key
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "storage_state.json").write_text('{"cookies":[],"origins":[]}', encoding="utf-8")


def test_toutiao_login_registers_existing_storage_and_lists_account(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        write_storage_state(test_app.data_dir, "demo")

        response = client.post(
            "/api/accounts/toutiao/login",
            json={
                "display_name": "测试头条号",
                "account_key": "demo",
                "use_browser": False,
                "note": "fixture",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["display_name"] == "测试头条号"
        assert payload["platform_code"] == "toutiao"
        assert payload["status"] == "valid"
        assert payload["state_path"] == "browser_states/toutiao/demo/storage_state.json"

        list_response = client.get("/api/accounts")
        assert list_response.status_code == 200
        assert [item["id"] for item in list_response.json()] == [payload["id"]]
    finally:
        test_app.cleanup()


def test_account_check_relogin_and_delete(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        write_storage_state(test_app.data_dir, "demo")
        account = client.post(
            "/api/accounts/toutiao/login",
            json={"display_name": "测试头条号", "account_key": "demo", "use_browser": False},
        ).json()

        checked = client.post(f"/api/accounts/{account['id']}/check", json={"use_browser": False})
        assert checked.status_code == 200
        assert checked.json()["status"] == "valid"

        with test_app.session_factory() as db:
            stored = db.get(Account, account["id"])
            assert stored is not None
            stored.state_path = "browser_states/toutiao/demo-missing/storage_state.json"
            db.commit()

        expired = client.post(f"/api/accounts/{account['id']}/check", json={"use_browser": False})
        assert expired.status_code == 200
        assert expired.json()["status"] == "expired"

        write_storage_state(test_app.data_dir, "demo-missing")
        relogged = client.post(f"/api/accounts/{account['id']}/relogin", json={"use_browser": False})
        assert relogged.status_code == 200
        assert relogged.json()["status"] == "valid"

        deleted = client.delete(f"/api/accounts/{account['id']}")
        assert deleted.status_code == 204
        assert client.get("/api/accounts").json() == []
    finally:
        test_app.cleanup()


def test_toutiao_login_requires_storage_when_browser_disabled(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        response = client.post(
            "/api/accounts/toutiao/login",
            json={"display_name": "测试头条号", "account_key": "missing", "use_browser": False},
        )

        assert response.status_code == 400
        assert "Storage state not found" in response.json()["detail"]
    finally:
        test_app.cleanup()


def test_export_accounts_auth_package_contains_manifest_and_state(monkeypatch):
    test_app = build_test_app(monkeypatch)
    client = test_app.client

    try:
        write_storage_state(test_app.data_dir, "demo")
        account = client.post(
            "/api/accounts/toutiao/login",
            json={"display_name": "export-demo", "account_key": "demo", "use_browser": False},
        ).json()

        response = client.post("/api/accounts/export", json={"account_ids": [account["id"]]})

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = set(archive.namelist())
            account_dir = f"accounts/toutiao-{account['id']}"
            assert "manifest.json" in names
            assert f"{account_dir}/account.json" in names
            assert f"{account_dir}/storage_state.json" in names

            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["schema_version"] == 1
            assert manifest["excluded_scopes"] == ["articles", "assets", "publish_tasks", "task_logs", "database"]
            assert manifest["accounts"][0]["id"] == account["id"]

            account_payload = json.loads(archive.read(f"{account_dir}/account.json"))
            assert account_payload["display_name"] == "export-demo"
            assert archive.read(f"{account_dir}/storage_state.json") == b'{"cookies":[],"origins":[]}'
    finally:
        test_app.cleanup()

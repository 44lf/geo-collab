"""多 skill 库 HTTP 路由测试：/api/mcp/skills/*（上传/列表/版本/回滚/下载/删除）。"""

import io
import zipfile

import pytest

pytestmark = pytest.mark.mysql


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for k, v in files.items():
            zf.writestr(k, v)
    return buf.getvalue()


def test_upload_list_setcurrent_download(monkeypatch):
    from server.tests.utils import build_test_app

    app = build_test_app(monkeypatch)
    try:
        c = app.client  # TestClient，已带 admin JWT cookie
        # 上传 v1
        r = c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"one"}), "application/zip")},
            data={"name": "writer"},
        )
        assert r.status_code == 200, r.text
        sid = r.json()["skill_id"]
        assert r.json()["version_label"] == "v1"

        # 列表含官方 goal(seed 后) + writer；此处至少含 writer
        r = c.get("/api/mcp/skills")
        assert r.status_code == 200
        assert any(s["slug"] for s in r.json()["skills"])

        # 上传 v2 → 版本历史 2 条
        c.post(
            "/api/mcp/skills/upload",
            files={"files": ("b.zip", _zip({"SKILL.md": b"two"}), "application/zip")},
            data={"name": "writer"},
        )
        r = c.get(f"/api/mcp/skills/{sid}/versions")
        labels = [v["version_label"] for v in r.json()["versions"]]
        assert labels == ["v2", "v1"]
        v1_id = [v["id"] for v in r.json()["versions"] if v["version_label"] == "v1"][0]

        # 回滚到 v1
        r = c.post(f"/api/mcp/skills/{sid}/set-current", json={"version_id": v1_id})
        assert r.status_code == 204

        # 下载当前版本 zip
        r = c.get(f"/api/mcp/skills/{sid}/download.zip")
        assert r.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        assert zf.read("SKILL.md") == b"one"

        # 删当前版本 → 409
        r = c.delete(f"/api/mcp/skills/{sid}/versions/{v1_id}")
        assert r.status_code == 409
    finally:
        app.cleanup()

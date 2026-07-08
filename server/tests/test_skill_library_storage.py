def test_load_files_db_backend():
    from server.app.modules.loop_skills.storage import load_version_files

    class Row:
        storage_backend = "db"
        files = [{"path": "SKILL.md", "content": "hi", "sha256": "x", "size": 2}]
        storage_key = None

    out = load_version_files(Row())
    assert len(out) == 1
    assert out[0].path == "SKILL.md"
    assert out[0].content == "hi"


def test_minio_roundtrip_monkeypatched(monkeypatch):
    """save→load 走 MinIO 分支，用假 store 打通读写不依赖真 MinIO。"""
    import server.app.modules.loop_skills.storage as st
    from server.app.modules.loop_skills.service import SkillFile

    blob = {}

    def fake_upload(bucket, key, data, content_type):
        blob[key] = data

    def fake_get(bucket, key):
        return blob[key]

    monkeypatch.setattr(st, "_store_upload", fake_upload)
    monkeypatch.setattr(st, "_store_get", fake_get)
    monkeypatch.setattr(st, "_ensure_bucket", lambda b: None)

    files = [SkillFile(path="a/SKILL.md", size=3, sha256="s", content="abc")]
    key = st.save_files_to_minio(files)
    assert key

    class Row:
        storage_backend = "minio"
        files = None
        storage_key = key

    out = st.load_version_files(Row())
    assert out[0].path == "a/SKILL.md"
    assert out[0].content == "abc"

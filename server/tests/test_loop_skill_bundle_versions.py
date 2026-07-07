"""loop_skills 版本化(上传入库 + 版本管理)测试。"""

from __future__ import annotations

import io
import zipfile

import pytest


def _make_zip(files: dict[str, str]) -> bytes:
    """把 {posix_path: text} 打成 zip bytes。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, text in files.items():
            zf.writestr(path, text)
    return buf.getvalue()


def _valid_files() -> dict[str, str]:
    """一份满足结构校验的最小合法 skill 包。"""
    return {
        "README.md": "# test bundle\n",
        "commands/goal.md": "# goal\n内容\n",
        "skills/geo-goal-orchestrator/SKILL.md": "---\nname: x\n---\norchestrator\n",
    }


@pytest.mark.mysql
def test_can_persist_bundle_version(monkeypatch):
    """能建一行 LoopSkillBundleVersion 并读回 —— 证明表已建 + 模型已注册。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills.models import LoopSkillBundleVersion

        with SessionLocal() as db:
            row = LoopSkillBundleVersion(
                version_label="v-test",
                bundle_sha256="0" * 64,
                files=[{"path": "README.md", "content": "x", "sha256": "0" * 64, "size": 1}],
                file_count=1,
                total_size=1,
                is_enabled=False,
                is_deleted=False,
                uploaded_by_user_id=None,
                notes=None,
            )
            db.add(row)
            db.commit()
            got = db.get(LoopSkillBundleVersion, row.id)
            assert got is not None
            assert got.version_label == "v-test"
            assert got.is_enabled is False
    finally:
        test_app.cleanup()


def test_build_bundle_from_file_map_matches_algorithm():
    """from_file_map 对同一批字节,算出的 sha 与手工按算法算的一致 + 文件按 posix 序。"""
    import hashlib

    from server.app.modules.loop_skills.service import build_bundle_from_file_map

    raw = {
        "commands/goal.md": b"g",
        "README.md": b"r",
        "skills/geo-goal-orchestrator/SKILL.md": b"o",
    }
    bundle = build_bundle_from_file_map(raw, version="v-x")
    # 文件按 posix 串排序:README.md < commands/... < skills/...
    assert [f.path for f in bundle.files] == [
        "README.md",
        "commands/goal.md",
        "skills/geo-goal-orchestrator/SKILL.md",
    ]
    # 手工复算 bundle sha
    h = hashlib.sha256()
    for f in bundle.files:
        h.update(f.path.encode("utf-8"))
        h.update(b"\x00")
        h.update(f.sha256.encode("ascii"))
        h.update(b"\x00")
    assert bundle.bundle_sha256 == h.hexdigest()
    assert bundle.version == "v-x"


@pytest.mark.mysql
def test_get_active_bundle_falls_back_to_seed(monkeypatch):
    """无启用版 → get_active_bundle 回落种子 build_bundle()(5 文件)。"""
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            active = vs.get_active_bundle(db)
            assert len(active.files) == 5  # 种子
            assert vs.list_versions(db) == []  # 库里还没有上传版
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_persists_unenabled(monkeypatch):
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            meta = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="v1",
                notes="note",
                uploaded_by_user_id=None,
            )
            db.commit()
            assert meta.is_enabled is False
            assert meta.version_label == "v1"
            assert meta.file_count == 3
            assert meta.total_size > 0
            assert len(meta.bundle_sha256) == 64
            # 未启用 → 列表可见、get_active 仍回落种子
            assert [m.id for m in vs.list_versions(db)] == [meta.id]
            assert len(vs.get_active_bundle(db).files) == 5
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_rejections(monkeypatch):
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            # 缺必需文件
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip({"README.md": "x"}),
                    version_label=None,
                    notes=None,
                    uploaded_by_user_id=None,
                )
            # 非白名单路径
            bad = dict(_valid_files())
            bad["evil.sh"] = "rm -rf"
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, _make_zip(bad), version_label=None, notes=None, uploaded_by_user_id=None
                )
            # zip-slip
            slip = dict(_valid_files())
            slip["../escape.md"] = "x"
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, _make_zip(slip), version_label=None, notes=None, uploaded_by_user_id=None
                )
            # 非 zip
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, b"not a zip", version_label=None, notes=None, uploaded_by_user_id=None
                )
            # 非 utf-8 成员
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as zf:
                for p, t in _valid_files().items():
                    zf.writestr(p, t)
                zf.writestr("skills/x/bin.md", b"\xff\xfe\x00binary")
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, buf.getvalue(), version_label=None, notes=None, uploaded_by_user_id=None
                )
    finally:
        test_app.cleanup()


@pytest.mark.mysql
def test_upload_version_resource_and_label_limits(monkeypatch):
    """C:条目数 / 累计解压体积 / 重名;B:label 控制字符 / 超长 —— 全部 ValidationError。"""
    from server.app.shared.errors import ValidationError
    from server.tests.utils import build_test_app

    test_app = build_test_app(monkeypatch)
    try:
        from server.app.db.session import SessionLocal
        from server.app.modules.loop_skills import versions_service as vs

        with SessionLocal() as db:
            # 条目数超限:required + 60 个 skills/ 小文件 > 50
            too_many = _valid_files()
            for i in range(60):
                too_many[f"skills/pad/{i}.md"] = "x"
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip(too_many),
                    version_label=None,
                    notes=None,
                    uploaded_by_user_id=None,
                )

            # 累计解压体积超限:3 个 1.5MB 高压缩比文件(压缩后仍 < 2MB zip 上限,但解压累计 4.5MB > 4MB)
            big = _valid_files()
            for i in range(3):
                big[f"skills/big/{i}.md"] = "a" * (1_500_000)
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, _make_zip(big), version_label=None, notes=None, uploaded_by_user_id=None
                )

            # 重名路径:zip 允许同名条目,dict-based _make_zip 造不出,直接用 ZipFile 写两次
            dup = io.BytesIO()
            with zipfile.ZipFile(dup, "w") as zf:
                for p, t in _valid_files().items():
                    zf.writestr(p, t)
                zf.writestr("commands/goal.md", "第二份 goal")  # 与 required 同名
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db, dup.getvalue(), version_label=None, notes=None, uploaded_by_user_id=None
                )

            # label 含换行(header 注入面)
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip(_valid_files()),
                    version_label="a\r\nInjected: x",
                    notes=None,
                    uploaded_by_user_id=None,
                )
            # label 超长
            with pytest.raises(ValidationError):
                vs.upload_version(
                    db,
                    _make_zip(_valid_files()),
                    version_label="x" * 201,
                    notes=None,
                    uploaded_by_user_id=None,
                )

            # 中文 label 合法(latin-1 由 router 头部 percent-encode 兜底,service 层放行)
            m = vs.upload_version(
                db,
                _make_zip(_valid_files()),
                version_label="2026-07-08 严格版",
                notes=None,
                uploaded_by_user_id=None,
            )
            assert m.version_label == "2026-07-08 严格版"
    finally:
        test_app.cleanup()

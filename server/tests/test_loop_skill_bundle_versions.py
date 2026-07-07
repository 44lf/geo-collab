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

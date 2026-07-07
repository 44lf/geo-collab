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

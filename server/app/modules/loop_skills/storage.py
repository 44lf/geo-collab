"""Skill 版本文件存储 seam：DB(JSON) 默认，MinIO 后端保留（本期上传不触发）。

storage_backend='db' → 读 row.files；'minio' → 从 image_library MinIO 拉 zip 解包。
save_files_to_minio 已实现但上传路径本期一律走 db（见 skill_service.create_version）。
"""

from __future__ import annotations

import hashlib
import io
import zipfile

# 复用 image_library 的 MinIO 封装；包成模块级别名便于测试 monkeypatch
from server.app.modules.image_library.store import (
    delete_object as _store_delete,
)
from server.app.modules.image_library.store import (
    ensure_bucket as _ensure_bucket,
)
from server.app.modules.image_library.store import (
    get_object_bytes as _store_get,
)
from server.app.modules.image_library.store import (
    upload_image as _store_upload,
)
from server.app.modules.loop_skills.service import SkillFile

SKILL_BUCKET = "geo-skill-bundles"


def load_version_files(row) -> list[SkillFile]:
    if row.storage_backend == "minio":
        return _load_from_minio(row.storage_key)
    return [SkillFile(**f) for f in (row.files or [])]


def _load_from_minio(storage_key: str) -> list[SkillFile]:
    data = _store_get(SKILL_BUCKET, storage_key)
    zf = zipfile.ZipFile(io.BytesIO(data))
    files: list[SkillFile] = []
    for name in sorted(zf.namelist()):
        raw = zf.read(name)
        content = raw.decode("utf-8")
        files.append(
            SkillFile(
                path=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), content=content
            )
        )
    return files


def save_files_to_minio(files: list[SkillFile]) -> str:
    _ensure_bucket(SKILL_BUCKET)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f.path, f.content)
    data = buf.getvalue()
    key = f"{hashlib.sha256(data).hexdigest()}.zip"
    _store_upload(SKILL_BUCKET, key, data, "application/zip")
    return key


def delete_minio_object(storage_key: str) -> None:
    _store_delete(SKILL_BUCKET, storage_key)

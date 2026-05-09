from __future__ import annotations

import hashlib
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from server.app.core.paths import get_data_dir
from server.app.core.time import utcnow
from server.app.models import Asset


# 存储结果：资源和其文件路径
@dataclass(frozen=True)
class StoredAsset:
    asset: Asset
    path: Path


# 从图片文件头字节猜测图片宽高（仅支持 PNG 和 JPEG）
def guess_image_size(data: bytes) -> tuple[int | None, int | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return width, height

    if data[:2] == b"\xff\xd8":
        index = 2
        while index < len(data):
            while index < len(data) and data[index] == 0xFF:
                index += 1
            if index >= len(data):
                break
            marker = data[index]
            index += 1
            if marker in {0xD8, 0xD9}:
                continue
            if index + 2 > len(data):
                break
            segment_length = struct.unpack(">H", data[index : index + 2])[0]
            if marker in range(0xC0, 0xC4) and index + 7 <= len(data):
                height, width = struct.unpack(">HH", data[index + 3 : index + 7])
                return width, height
            index += segment_length

    return None, None


# 根据文件名后缀和文件头字节推断扩展名
def normalize_ext(filename: str, content_type: str | None, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix:
        return suffix

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"

    if data[:2] == b"\xff\xd8":
        return ".jpg"

    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"

    if content_type and "/" in content_type:
        return f".{content_type.split('/')[-1].lower()}"

    return ".bin"


# 构造资源文件的 API 访问 URL
def asset_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}"


# 解析资源文件在磁盘上的绝对路径，含安全校验（防止路径穿越）
def resolve_asset_path(asset: Asset) -> Path:
    data_dir = get_data_dir().resolve()
    path = (data_dir / asset.storage_key).resolve()
    if data_dir != path and data_dir not in path.parents:
        raise ValueError("Asset path escaped data directory")
    return path


# 内部方法：将二进制数据创建为 Asset 并写入磁盘
def _create_asset(db: Session, data: bytes, filename: str, content_type: str) -> StoredAsset:
    now = utcnow()
    asset_id = uuid.uuid4().hex
    ext = normalize_ext(filename, content_type, data)
    sha256 = hashlib.sha256(data).hexdigest()
    width, height = guess_image_size(data)
    storage_key = Path("assets") / f"{now:%Y}" / f"{now:%m}" / f"{asset_id}{ext}"

    asset = Asset(
        id=asset_id,
        filename=filename,
        ext=ext,
        mime_type=content_type,
        size=len(data),
        sha256=sha256,
        storage_key=storage_key.as_posix(),
        width=width,
        height=height,
    )
    db.add(asset)

    path = get_data_dir() / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    db.flush()
    return StoredAsset(asset=asset, path=path)


# 从内存中的字节数据存储文件（用于任务失败截图）
def store_bytes(db: Session, data: bytes, filename: str, content_type: str) -> StoredAsset:
    if not data:
        raise ValueError("Stored file is empty")
    return _create_asset(db, data, filename, content_type)


# 内部方法：将临时文件移动至最终存储，创建 Asset 记录
def _create_asset_from_path(
    db: Session, filepath: Path, filename: str, content_type: str,
    sha256_hash: str, size: int,
) -> StoredAsset:
    now = utcnow()
    asset_id = uuid.uuid4().hex
    with open(filepath, "rb") as f:
        header = f.read(32)
    ext = normalize_ext(filename, content_type, header)
    width, height = guess_image_size(header)
    storage_key = Path("assets") / f"{now:%Y}" / f"{now:%m}" / f"{asset_id}{ext}"
    dest = get_data_dir() / storage_key

    asset = Asset(
        id=asset_id,
        filename=filename,
        ext=ext,
        mime_type=content_type,
        size=size,
        sha256=sha256_hash,
        storage_key=storage_key.as_posix(),
        width=width,
        height=height,
    )
    db.add(asset)
    db.flush()

    dest.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.move(str(filepath), str(dest))
    return StoredAsset(asset=asset, path=dest)


# 处理 HTTP 文件上传请求（流式写入 + 魔数校验 + SHA256 去重）
async def store_upload(db: Session, upload: UploadFile) -> StoredAsset:
    import tempfile

    from fastapi import HTTPException

    from server.app.core.config import ALLOWED_MAGIC, MAX_ASSET_BYTES

    filename = upload.filename or f"{uuid.uuid4().hex}.bin"
    content_type = upload.content_type or "application/octet-stream"

    # 流式写入临时文件，同时校验魔数和大小
    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = Path(tmp.name)
    sha256 = hashlib.sha256()
    total = 0
    first_chunk = True

    try:
        while True:
            chunk = await upload.read(65536)  # 64KB
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_ASSET_BYTES:
                tmp.close()
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {MAX_ASSET_BYTES // (1024 * 1024)}MB limit",
                )

            if first_chunk:
                valid_magic = False
                for magic in ALLOWED_MAGIC:
                    if chunk.startswith(magic):
                        if magic == b"RIFF":
                            # WebP 需额外检查 bytes 8:12
                            if len(chunk) >= 12 and chunk[8:12] == b"WEBP":
                                valid_magic = True
                                break
                        else:
                            valid_magic = True
                            break
                if not valid_magic:
                    tmp.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=415, detail="Unsupported file type")
                first_chunk = False

            tmp.write(chunk)
            sha256.update(chunk)

        tmp.close()

        if total == 0:
            tmp_path.unlink(missing_ok=True)
            raise ValueError("Uploaded file is empty")

        # SHA256 去重
        digest = sha256.hexdigest()
        existing = db.query(Asset).filter(Asset.sha256 == digest).first()
        if existing:
            tmp_path.unlink(missing_ok=True)
            db.flush()
            db.refresh(existing)
            return StoredAsset(asset=existing, path=resolve_asset_path(existing))

        stored = _create_asset_from_path(db, tmp_path, filename, content_type, digest, total)
        db.flush()
        db.refresh(stored.asset)
        return stored

    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

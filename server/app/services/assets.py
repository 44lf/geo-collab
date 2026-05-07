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


@dataclass(frozen=True)
class StoredAsset:
    asset: Asset
    path: Path


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


def asset_url(asset_id: str) -> str:
    return f"/api/assets/{asset_id}"


def resolve_asset_path(asset: Asset) -> Path:
    data_dir = get_data_dir().resolve()
    path = (data_dir / asset.storage_key).resolve()
    if data_dir != path and data_dir not in path.parents:
        raise ValueError("Asset path escaped data directory")
    return path


def _create_asset(db: Session, data: bytes, filename: str, content_type: str) -> StoredAsset:
    now = utcnow()
    asset_id = uuid.uuid4().hex
    ext = normalize_ext(filename, content_type, data)
    sha256 = hashlib.sha256(data).hexdigest()
    width, height = guess_image_size(data)
    storage_key = Path("assets") / f"{now:%Y}" / f"{now:%m}" / f"{asset_id}{ext}"
    path = get_data_dir() / storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

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
    db.flush()
    return StoredAsset(asset=asset, path=path)


def store_bytes(db: Session, data: bytes, filename: str, content_type: str) -> StoredAsset:
    if not data:
        raise ValueError("Stored file is empty")
    return _create_asset(db, data, filename, content_type)


async def store_upload(db: Session, upload: UploadFile) -> StoredAsset:
    data = await upload.read()
    if not data:
        raise ValueError("Uploaded file is empty")
    filename = upload.filename or f"{uuid.uuid4().hex}.bin"
    content_type = upload.content_type or "application/octet-stream"
    stored = _create_asset(db, data, filename, content_type)
    db.commit()
    db.refresh(stored.asset)
    return stored

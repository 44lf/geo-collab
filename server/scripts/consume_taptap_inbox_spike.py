"""Consume and verify the latest TapTap bundle from the DEV MinIO inbox."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from minio import Minio
from minio.commonconfig import CopySource
from sqlalchemy import Column, Integer, Table, select

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 200 * 1024 * 1024
MAX_FILES = 500


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"required environment variable is missing: {name}")
    return value


def _read_object_json(client: Minio, bucket: str, key: str) -> dict[str, Any]:
    response = client.get_object(bucket, key)
    try:
        return json.loads(response.read().decode("utf-8-sig"))
    finally:
        response.close()
        response.release_conn()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with zipfile.ZipFile(archive) as bundle_zip:
        infos = bundle_zip.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise RuntimeError(f"unsafe archive file count: {len(infos)}")
        total_size = sum(info.file_size for info in infos)
        if total_size > MAX_EXTRACTED_BYTES:
            raise RuntimeError(f"archive expands beyond limit: {total_size}")

        for info in infos:
            member = PurePosixPath(info.filename.replace("\\", "/"))
            if (
                member.is_absolute()
                or not member.parts
                or ".." in member.parts
                or info.flag_bits & 0x1
            ):
                raise RuntimeError(f"unsafe archive member: {info.filename}")
            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"archive symlink is forbidden: {info.filename}")
            target = (destination / Path(*member.parts)).resolve()
            if destination_resolved not in target.parents and target != destination_resolved:
                raise RuntimeError(f"archive member escaped destination: {info.filename}")

        bundle_zip.extractall(destination)


def _register_external_fk_metadata() -> None:
    """Resolve game-library FK strings without importing the full article service."""
    from server.app.db.base import Base

    if "articles" not in Base.metadata.tables:
        Table(
            "articles",
            Base.metadata,
            Column("id", Integer, primary_key=True),
        )


def _verify_written_assets(
    *,
    client: Minio,
    bundle: Any,
    target_id: int,
) -> dict[str, Any]:
    from server.app.db.session import SessionLocal
    from server.app.modules.game_library.models import Game
    from server.app.modules.image_library.models import StockCategory, StockImage

    target = next(item for item in bundle.targets if item.target_game_id == target_id)
    screenshot_urls = {
        asset.source_url
        for asset in target.assets
        if asset.role == "screenshot" and asset.source_url
    }
    db = SessionLocal()
    try:
        game = db.get(Game, target_id)
        if game is None:
            raise RuntimeError(f"imported game row is missing: {target_id}")
        if not game.description:
            raise RuntimeError("imported game description is empty")
        if not any(
            isinstance(item, dict) and item.get("source") == "taptap"
            for item in (game.sources or [])
        ):
            raise RuntimeError("game row does not contain the taptap source")
        if not game.icon_url or not game.icon_url.startswith("/api/stock-images/"):
            raise RuntimeError("game icon was not persisted into local MinIO")

        icon_id = int(game.icon_url.split("/")[3])
        icon = db.get(StockImage, icon_id)
        if icon is None:
            raise RuntimeError(f"icon database row is missing: {icon_id}")
        icon_category = db.get(StockCategory, icon.category_id)
        if icon_category is None:
            raise RuntimeError("icon category is missing")
        client.stat_object(icon_category.bucket_name, icon.minio_key)

        screenshots = list(
            db.execute(
                select(StockImage, StockCategory)
                .join(StockCategory, StockCategory.id == StockImage.category_id)
                .where(StockImage.source_url.in_(screenshot_urls))
            ).all()
        )
        found_urls = {image.source_url for image, _category in screenshots}
        missing_urls = sorted(screenshot_urls - found_urls)
        if missing_urls:
            raise RuntimeError(f"screenshot database rows are missing: {len(missing_urls)}")
        for image, category in screenshots:
            client.stat_object(category.bucket_name, image.minio_key)

        return {
            "game_id": game.id,
            "game_name": game.name,
            "icon_object_verified": True,
            "screenshot_objects_verified": len(screenshot_urls),
            "description_chars": len(game.description),
            "taptap_source_verified": True,
        }
    finally:
        db.close()


def main() -> int:
    endpoint = _required_env("GEO_MINIO_ENDPOINT")
    access_key = _required_env("GEO_MINIO_ACCESS_KEY")
    secret_key = _required_env("GEO_MINIO_SECRET_KEY")
    bucket = os.environ.get("GEO_TAPTAP_INBOX_BUCKET", "geo-taptap-inbox")
    secure = os.environ.get("GEO_MINIO_SECURE", "false").lower() == "true"
    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    ready_objects = [
        item
        for item in client.list_objects(bucket, prefix="ready/", recursive=True)
        if item.object_name and item.object_name.endswith(".json")
    ]
    if not ready_objects:
        raise RuntimeError(f"no ready marker was found in {bucket}/ready/")
    latest = max(ready_objects, key=lambda item: item.last_modified)
    ready_key = str(latest.object_name)
    marker = _read_object_json(client, bucket, ready_key)

    bundle_id = str(marker.get("bundle_id", ""))
    archive_key = str(marker.get("archive_object", ""))
    expected_sha256 = str(marker.get("archive_sha256", "")).lower()
    expected_bytes = int(marker.get("archive_bytes", -1))
    if (
        not bundle_id
        or not archive_key.startswith(f"incoming/{bundle_id}/")
        or len(expected_sha256) != 64
        or expected_bytes < 1
        or expected_bytes > MAX_ARCHIVE_BYTES
    ):
        raise RuntimeError("ready marker is invalid")

    with tempfile.TemporaryDirectory(prefix="geo-taptap-import-") as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / "bundle.zip"
        bundle_root = temporary_root / "bundle"
        bundle_root.mkdir()

        metadata = client.stat_object(bucket, archive_key)
        if metadata.size != expected_bytes:
            raise RuntimeError(
                f"archive size mismatch: expected={expected_bytes} actual={metadata.size}"
            )
        client.fget_object(bucket, archive_key, str(archive_path))
        actual_sha256 = _sha256(archive_path)
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"archive SHA256 mismatch: expected={expected_sha256} actual={actual_sha256}"
            )
        _safe_extract(archive_path, bundle_root)

        _register_external_fk_metadata()
        from server.scripts.remote_game_bundle_import import plan_bundle, run_import

        validated_bundle, _plan = plan_bundle(bundle_root)
        if validated_bundle.bundle_id != bundle_id:
            raise RuntimeError("ready marker bundle_id does not match manifest")

        dry_run = run_import(bundle_root, dry_run=True)
        if dry_run["failed"] != 0 or dry_run["planned"] < 1:
            raise RuntimeError(f"dry-run did not produce an importable target: {dry_run}")

        imported = run_import(bundle_root, dry_run=False)
        if imported["failed"] != 0 or imported["imported"] != imported["planned"]:
            raise RuntimeError(f"formal import failed: {imported}")

        target_id = int(imported["targets"][0]["target_game_id"])
        verification = _verify_written_assets(
            client=client,
            bundle=validated_bundle,
            target_id=target_id,
        )

    processed_key = f"processed/{Path(ready_key).name}"
    client.copy_object(bucket, processed_key, CopySource(bucket, ready_key))
    client.remove_object(bucket, ready_key)

    result = {
        "ok": True,
        "channel": "Windows collector -> DEV MinIO inbox -> geo_dev + DEV MinIO",
        "bundle_id": bundle_id,
        "archive_sha256_verified": True,
        "bundle_manifest_verified": True,
        "dry_run": {
            "planned": dry_run["planned"],
            "failed": dry_run["failed"],
        },
        "import": {
            "imported": imported["imported"],
            "failed": imported["failed"],
        },
        "verification": verification,
        "ready_acknowledged_as": processed_key,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

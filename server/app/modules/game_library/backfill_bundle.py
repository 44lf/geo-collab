"""Validation contract for externally collected game backfill bundles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = 1
MAX_TARGETS = 20
MAX_FILE_COUNT = 200
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 250 * 1024 * 1024
SUPPORTED_MEDIA_TYPES = {"application/json", "image/jpeg", "image/png"}
TARGET_STATUSES = {"success", "miss", "error", "blocked"}
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class BundleValidationError(ValueError):
    """Raised when a collector bundle is invalid or unsafe."""


@dataclass(frozen=True)
class ValidatedFile:
    relative_path: str
    path: Path
    media_type: str
    role: str
    size: int
    sha256: str
    source_url: str | None = None


@dataclass(frozen=True)
class ValidatedTarget:
    target_game_id: int
    name: str
    category_id: int | None
    status: str
    game_file: ValidatedFile | None
    game_data: dict[str, Any] | None
    assets: tuple[ValidatedFile, ...]


@dataclass(frozen=True)
class ValidatedBundle:
    root: Path
    bundle_id: str
    collector_version: str
    source: str
    targets: tuple[ValidatedTarget, ...]
    files: tuple[ValidatedFile, ...]
    manifest: dict[str, Any]


def _required(mapping: dict[str, Any], field: str, *, context: str) -> Any:
    if field not in mapping:
        raise BundleValidationError(f"{context} missing required field {field!r}")
    return mapping[field]


def _safe_file_path(root: Path, value: Any) -> tuple[str, Path]:
    if not isinstance(value, str) or not value:
        raise BundleValidationError("file path must be a non-empty string")
    if "\\" in value:
        raise BundleValidationError(f"unsafe path {value!r}: use forward slashes")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        raise BundleValidationError(f"unsafe path {value!r}")
    normalized = relative.as_posix()
    resolved = (root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BundleValidationError(f"unsafe path {value!r}") from exc
    return normalized, resolved


def _validate_media_signature(data: bytes, media_type: str, relative_path: str) -> None:
    valid = False
    if media_type == "image/png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n")
    elif media_type == "image/jpeg":
        valid = data.startswith(b"\xff\xd8\xff")
    elif media_type == "application/json":
        try:
            json.loads(data.decode("utf-8-sig"))
            valid = True
        except (UnicodeDecodeError, json.JSONDecodeError):
            valid = False
    if not valid:
        raise BundleValidationError(f"MIME signature mismatch for {relative_path!r} ({media_type})")


def _validate_file(
    root: Path,
    raw: Any,
    *,
    total_so_far: int,
) -> tuple[ValidatedFile, int]:
    if not isinstance(raw, dict):
        raise BundleValidationError("file inventory entries must be objects")
    relative_path, path = _safe_file_path(root, _required(raw, "path", context="file"))
    declared_size = _required(raw, "bytes", context=f"file {relative_path!r}")
    if not isinstance(declared_size, int) or isinstance(declared_size, bool) or declared_size < 0:
        raise BundleValidationError(f"invalid byte size for file {relative_path!r}")
    if declared_size > MAX_FILE_BYTES:
        raise BundleValidationError(f"file too large: {relative_path!r}")
    new_total = total_so_far + declared_size
    if new_total > MAX_TOTAL_BYTES:
        raise BundleValidationError("bundle too large")
    if not path.is_file():
        raise BundleValidationError(f"inventory file does not exist: {relative_path!r}")
    actual_size = path.stat().st_size
    if actual_size != declared_size:
        raise BundleValidationError(
            f"byte size mismatch for {relative_path!r}: declared={declared_size} actual={actual_size}"
        )
    expected_hash = _required(raw, "sha256", context=f"file {relative_path!r}")
    if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
        raise BundleValidationError(f"invalid SHA-256 for {relative_path!r}")
    data = path.read_bytes()
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash.lower() != expected_hash.lower():
        raise BundleValidationError(f"SHA-256 mismatch for {relative_path!r}")
    media_type = _required(raw, "media_type", context=f"file {relative_path!r}")
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise BundleValidationError(f"unsupported media_type {media_type!r} for {relative_path!r}")
    _validate_media_signature(data, media_type, relative_path)
    role = _required(raw, "role", context=f"file {relative_path!r}")
    if not isinstance(role, str) or not role:
        raise BundleValidationError(f"invalid role for {relative_path!r}")
    source_url = raw.get("source_url")
    if source_url is not None and (not isinstance(source_url, str) or not source_url):
        raise BundleValidationError(f"invalid source_url for {relative_path!r}")
    return (
        ValidatedFile(
            relative_path=relative_path,
            path=path,
            media_type=media_type,
            role=role,
            size=actual_size,
            sha256=actual_hash,
            source_url=source_url,
        ),
        new_total,
    )


def _parse_game_file(game_file: ValidatedFile, target_game_id: int) -> dict[str, Any]:
    if game_file.media_type != "application/json" or game_file.role != "game":
        raise BundleValidationError(
            f"game_file for target {target_game_id} must be application/json with role 'game'"
        )
    try:
        payload = json.loads(game_file.path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"invalid game JSON for target {target_game_id}") from exc
    if not isinstance(payload, dict):
        raise BundleValidationError(f"game JSON for target {target_game_id} must be an object")
    if payload.get("target_game_id") != target_game_id:
        raise BundleValidationError(
            f"game JSON target_game_id mismatch for target {target_game_id}"
        )
    game = payload.get("game")
    if not isinstance(game, dict):
        raise BundleValidationError(f"game JSON missing game object for target {target_game_id}")
    for field in ("source", "game_id", "name"):
        value = game.get(field)
        if not isinstance(value, str) or not value:
            raise BundleValidationError(
                f"game JSON missing required game field {field!r} for target {target_game_id}"
            )
    for field in ("tags", "platforms", "screenshot_urls"):
        if not isinstance(game.get(field), list):
            raise BundleValidationError(
                f"game JSON field {field!r} must be a list for target {target_game_id}"
            )
    if not isinstance(game.get("raw", {}), dict):
        raise BundleValidationError(
            f"game JSON field 'raw' must be an object for target {target_game_id}"
        )
    return payload


def _validate_target(
    raw: Any,
    files_by_path: dict[str, ValidatedFile],
    *,
    source: str,
) -> ValidatedTarget:
    if not isinstance(raw, dict):
        raise BundleValidationError("target entries must be objects")
    target_game_id = _required(raw, "target_game_id", context="target")
    if (
        not isinstance(target_game_id, int)
        or isinstance(target_game_id, bool)
        or target_game_id <= 0
    ):
        raise BundleValidationError("target_game_id must be a positive integer")
    name = _required(raw, "name", context=f"target {target_game_id}")
    if not isinstance(name, str) or not name.strip():
        raise BundleValidationError(f"invalid name for target {target_game_id}")
    category_id = raw.get("category_id")
    if category_id is not None and (
        not isinstance(category_id, int) or isinstance(category_id, bool) or category_id <= 0
    ):
        raise BundleValidationError(f"invalid category_id for target {target_game_id}")
    status = _required(raw, "status", context=f"target {target_game_id}")
    if status not in TARGET_STATUSES:
        raise BundleValidationError(f"invalid status for target {target_game_id}: {status!r}")

    game_file = None
    game_data = None
    raw_game_path = raw.get("game_file")
    raw_asset_paths = raw.get("asset_files", [])
    if not isinstance(raw_asset_paths, list):
        raise BundleValidationError(f"asset_files must be a list for target {target_game_id}")
    if status == "success":
        if not isinstance(raw_game_path, str) or raw_game_path not in files_by_path:
            raise BundleValidationError(f"missing game_file for target {target_game_id}")
        game_file = files_by_path[raw_game_path]
        game_data = _parse_game_file(game_file, target_game_id)
        game = game_data["game"]
        if game["name"] != name.strip():
            raise BundleValidationError(f"game name mismatch for target {target_game_id}")
        if game["source"] != source:
            raise BundleValidationError(f"game source mismatch for target {target_game_id}")
        game_category_id = _required(
            game_data,
            "category_id",
            context=f"game JSON for target {target_game_id}",
        )
        if game_category_id != category_id:
            raise BundleValidationError(f"game category_id mismatch for target {target_game_id}")
    elif raw_game_path is not None:
        raise BundleValidationError(
            f"non-success target {target_game_id} must not declare a game_file"
        )

    assets: list[ValidatedFile] = []
    seen_asset_paths: set[str] = set()
    for asset_path in raw_asset_paths:
        if not isinstance(asset_path, str) or asset_path not in files_by_path:
            raise BundleValidationError(
                f"unknown asset file {asset_path!r} for target {target_game_id}"
            )
        if asset_path in seen_asset_paths:
            raise BundleValidationError(
                f"duplicate asset file {asset_path!r} for target {target_game_id}"
            )
        seen_asset_paths.add(asset_path)
        asset = files_by_path[asset_path]
        if asset.role not in {"icon", "screenshot"} or not asset.media_type.startswith("image/"):
            raise BundleValidationError(
                f"invalid asset role/media type for {asset_path!r} target {target_game_id}"
            )
        if not asset.source_url:
            raise BundleValidationError(
                f"asset {asset_path!r} missing source_url for target {target_game_id}"
            )
        assets.append(asset)
    return ValidatedTarget(
        target_game_id=target_game_id,
        name=name.strip(),
        category_id=category_id,
        status=status,
        game_file=game_file,
        game_data=game_data,
        assets=tuple(assets),
    )


def validate_bundle(bundle_root: str | Path) -> ValidatedBundle:
    """Validate an extracted collector bundle without performing any database work."""
    root = Path(bundle_root).resolve()
    if not root.is_dir():
        raise BundleValidationError(f"bundle root is not a directory: {root}")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise BundleValidationError("bundle missing required manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError("manifest.json is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise BundleValidationError("manifest.json must contain an object")

    schema_version = _required(manifest, "schema_version", context="manifest")
    if schema_version != SCHEMA_VERSION:
        raise BundleValidationError(
            f"unsupported schema_version {schema_version!r}; expected {SCHEMA_VERSION}"
        )
    bundle_id = _required(manifest, "bundle_id", context="manifest")
    collector_version = _required(manifest, "collector_version", context="manifest")
    source = _required(manifest, "source", context="manifest")
    for field, value in (
        ("bundle_id", bundle_id),
        ("collector_version", collector_version),
        ("source", source),
    ):
        if not isinstance(value, str) or not value:
            raise BundleValidationError(f"manifest field {field!r} must be a non-empty string")
    if source != "taptap":
        raise BundleValidationError(f"unsupported bundle source {source!r}")
    _required(manifest, "started_at", context="manifest")
    _required(manifest, "finished_at", context="manifest")

    raw_files = _required(manifest, "files", context="manifest")
    if not isinstance(raw_files, list):
        raise BundleValidationError("manifest files must be a list")
    if not raw_files or len(raw_files) > MAX_FILE_COUNT:
        raise BundleValidationError(
            f"manifest files must contain between 1 and {MAX_FILE_COUNT} entries"
        )
    files: list[ValidatedFile] = []
    files_by_path: dict[str, ValidatedFile] = {}
    total_size = 0
    for raw_file in raw_files:
        validated, total_size = _validate_file(root, raw_file, total_so_far=total_size)
        if validated.relative_path in files_by_path:
            raise BundleValidationError(f"duplicate inventory path {validated.relative_path!r}")
        files.append(validated)
        files_by_path[validated.relative_path] = validated

    raw_targets = _required(manifest, "targets", context="manifest")
    if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= MAX_TARGETS:
        raise BundleValidationError(
            f"manifest targets must contain between 1 and {MAX_TARGETS} entries"
        )
    targets: list[ValidatedTarget] = []
    target_ids: set[int] = set()
    referenced_paths: set[str] = set()
    for raw_target in raw_targets:
        target = _validate_target(raw_target, files_by_path, source=source)
        if target.target_game_id in target_ids:
            raise BundleValidationError(f"duplicate target_game_id {target.target_game_id}")
        target_ids.add(target.target_game_id)
        targets.append(target)
        if target.game_file is not None:
            referenced_paths.add(target.game_file.relative_path)
        referenced_paths.update(asset.relative_path for asset in target.assets)
    unreferenced = set(files_by_path) - referenced_paths
    if unreferenced:
        raise BundleValidationError(f"unreferenced inventory files: {sorted(unreferenced)!r}")

    return ValidatedBundle(
        root=root,
        bundle_id=bundle_id,
        collector_version=collector_version,
        source=source,
        targets=tuple(targets),
        files=tuple(files),
        manifest=manifest,
    )

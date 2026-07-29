"""Layered validation and isolated extraction for Collector Bundle schema v2."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import zstandard

from server.app.modules.collector.models import CollectorTransfer

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SOURCES = {"baidu", "ninegame", "yingyongbao", "taptap"}
_ROLES = {"normalized", "raw_evidence", "media"}
_STATUSES = {"success", "miss", "blocked", "error"}


class BundleValidationError(RuntimeError):
    """A permanent archive, schema, identity, or integrity violation."""


@dataclass(frozen=True, slots=True)
class BundleValidationLimits:
    max_archive_bytes: int
    max_files: int
    max_file_bytes: int
    max_total_bytes: int
    max_manifest_bytes: int
    max_items: int = 1000

    def __post_init__(self) -> None:
        for field_name in (
            "max_archive_bytes",
            "max_files",
            "max_file_bytes",
            "max_total_bytes",
            "max_manifest_bytes",
            "max_items",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class ValidatedBundle:
    extracted_root: Path
    manifest: dict[str, Any]
    item_keys: tuple[str, ...]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str):
        raise BundleValidationError("unsafe archive path")
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or str(path) != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BundleValidationError(f"unsafe archive path: {value}")
    return path


def _is_encrypted(member: tarfile.TarInfo) -> bool:
    return any(
        "encrypt" in str(key).lower() and str(value).lower() not in {"", "0", "false", "no"}
        for key, value in member.pax_headers.items()
    )


def _extract_tar_zst(
    archive: Path,
    root: Path,
    *,
    limits: BundleValidationLimits,
) -> None:
    member_count = 0
    expanded_bytes = 0
    seen_paths: set[str] = set()
    try:
        with archive.open("rb") as source:
            with zstandard.ZstdDecompressor().stream_reader(source) as decompressed:
                with tarfile.open(fileobj=decompressed, mode="r|") as bundle:
                    for member in bundle:
                        member_count += 1
                        if member_count > limits.max_files:
                            raise BundleValidationError("archive file count exceeds limit")
                        path = _safe_relative_path(member.name)
                        normalized = path.as_posix()
                        if normalized in seen_paths:
                            raise BundleValidationError("duplicate archive path")
                        seen_paths.add(normalized)
                        if _is_encrypted(member):
                            raise BundleValidationError(
                                f"encrypted archive member is forbidden: {normalized}"
                            )
                        if not (member.isdir() or member.isreg()):
                            raise BundleValidationError(
                                f"special archive member is forbidden: {normalized}"
                            )
                        if member.size < 0 or member.size > limits.max_file_bytes:
                            raise BundleValidationError("archive member exceeds file byte limit")
                        expanded_bytes += member.size
                        if expanded_bytes > limits.max_total_bytes:
                            raise BundleValidationError("archive expanded byte limit exceeded")

                        target = root.joinpath(*path.parts)
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        member_stream = bundle.extractfile(member)
                        if member_stream is None:
                            raise BundleValidationError("regular archive member has no bytes")
                        with target.open("xb") as output:
                            shutil.copyfileobj(member_stream, output, length=1024 * 1024)
    except BundleValidationError:
        raise
    except (OSError, tarfile.TarError, zstandard.ZstdError) as exc:
        raise BundleValidationError("archive is not a valid tar.zst Bundle") from exc
    if member_count == 0:
        raise BundleValidationError("archive file count is empty")


def _dict(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BundleValidationError(f"{label} must be an object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise BundleValidationError(f"{label} must be an array")
    return value


def _verify_mime(path: Path, media_type: str) -> None:
    data = path.read_bytes()
    try:
        if media_type == "application/json":
            json.loads(data.decode("utf-8"))
            return
        if media_type == "text/html":
            data.decode("utf-8")
            return
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError(f"MIME signature mismatch for {path.name}") from exc
    signatures = {
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/webp": len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP",
        "image/gif": data.startswith((b"GIF87a", b"GIF89a")),
    }
    if not signatures.get(media_type, False):
        raise BundleValidationError(f"MIME signature mismatch for {path.name}")


def _verify_correlations(
    manifest: dict[str, Any],
    transfer: CollectorTransfer,
) -> None:
    correlations = _dict(manifest.get("correlations"), "correlations")
    expected = {
        "collector_id": transfer.collector_id,
        "job_id": transfer.job_id,
        "run_id": transfer.run_id,
        "bundle_id": transfer.bundle_id,
        "transport_id": transfer.transport_id,
    }
    if any(correlations.get(key) != value for key, value in expected.items()):
        raise BundleValidationError("Bundle correlation identity mismatch")


def _verify_job(
    manifest: dict[str, Any],
    transfer: CollectorTransfer,
    job_manifest: dict[str, Any],
) -> tuple[str, set[int], set[str]]:
    job = _dict(manifest.get("job"), "job")
    mode = job.get("mode")
    if mode not in {"refresh", "discovery"} or mode != job_manifest.get("mode"):
        raise BundleValidationError("Bundle job mode mismatch")
    if (
        job.get("destination") != transfer.destination
        or job.get("destination") != job_manifest.get("destination")
        or job.get("policy_version") != job_manifest.get("policy_version")
        or job_manifest.get("job_id") != transfer.job_id
    ):
        raise BundleValidationError("Bundle job identity mismatch")
    source_order = job.get("source_order")
    if (
        not isinstance(source_order, list)
        or not source_order
        or len(source_order) != len(set(source_order))
        or any(source not in _SOURCES for source in source_order)
        or source_order != job_manifest.get("source_order")
    ):
        raise BundleValidationError("Bundle job source order mismatch")
    targets = {
        int(target["target_game_id"])
        for target in _list(job_manifest.get("targets", []), "job targets")
        if isinstance(target, dict)
        and isinstance(target.get("target_game_id"), int)
        and target["target_game_id"] > 0
    }
    return str(mode), targets, set(source_order)


def _verify_manifest_limits(
    manifest: dict[str, Any],
    limits: BundleValidationLimits,
) -> None:
    declared = _dict(manifest.get("limits"), "limits")
    mappings = {
        "max_items": limits.max_items,
        "max_files": limits.max_files,
        "max_file_bytes": limits.max_file_bytes,
        "max_total_bytes": limits.max_total_bytes,
    }
    for field_name, server_limit in mappings.items():
        value = declared.get(field_name)
        if not isinstance(value, int) or value <= 0 or value > server_limit:
            raise BundleValidationError(f"Bundle declared {field_name} is invalid")


def _verify_inventory(
    root: Path,
    manifest: dict[str, Any],
    *,
    limits: BundleValidationLimits,
) -> dict[str, dict[str, Any]]:
    files = _list(manifest.get("files"), "files")
    if len(files) > limits.max_files:
        raise BundleValidationError("Bundle inventory file count exceeds limit")
    inventory: dict[str, dict[str, Any]] = {}
    for raw_entry in files:
        entry = _dict(raw_entry, "file entry")
        safe_path = _safe_relative_path(entry.get("path")).as_posix()
        if safe_path == "manifest.json" or safe_path in inventory:
            raise BundleValidationError("Bundle file inventory path is invalid or duplicated")
        if entry.get("role") not in _ROLES or entry.get("source") not in _SOURCES:
            raise BundleValidationError("Bundle file role or source is invalid")
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        media_type = entry.get("media_type")
        if (
            not isinstance(size, int)
            or size < 0
            or size > limits.max_file_bytes
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not isinstance(media_type, str)
        ):
            raise BundleValidationError("Bundle file integrity declaration is invalid")
        inventory[safe_path] = entry

    actual_paths = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json"
    }
    if actual_paths != set(inventory):
        raise BundleValidationError("Bundle file inventory does not match extracted files")
    for relative, entry in inventory.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if path.stat().st_size != entry["size_bytes"]:
            raise BundleValidationError(f"file size mismatch for {relative}")
        if _sha256_file(path) != entry["sha256"]:
            raise BundleValidationError(f"file SHA-256 mismatch for {relative}")
        _verify_mime(path, entry["media_type"])
    return inventory


def _verify_items(
    manifest: dict[str, Any],
    *,
    inventory: dict[str, dict[str, Any]],
    mode: str,
    allowed_targets: set[int],
    allowed_sources: set[str],
    job_manifest: dict[str, Any],
    server_max_items: int,
) -> tuple[str, ...]:
    items = _list(manifest.get("items"), "items")
    declared_limits = _dict(manifest.get("limits"), "limits")
    max_items = declared_limits.get("max_items")
    if not isinstance(max_items, int) or max_items <= 0 or max_items > server_max_items:
        raise BundleValidationError("Bundle item count exceeds limit")
    raw_job_limits = job_manifest.get("limits")
    job_max_items = raw_job_limits.get("max_items") if isinstance(raw_job_limits, dict) else None
    if not isinstance(job_max_items, int) or isinstance(job_max_items, bool) or job_max_items <= 0:
        if mode == "refresh":
            job_max_items = max(1, len(allowed_targets) * len(allowed_sources))
        else:
            discovery_paths = _list(
                job_manifest.get("discovery_paths", []),
                "job discovery_paths",
            )
            job_max_items = max(1, len(discovery_paths) * 15)
    effective_max_items = min(max_items, job_max_items, server_max_items)
    if len(items) > effective_max_items:
        raise BundleValidationError("Bundle item count exceeds issued job limit")
    item_keys: list[str] = []
    referenced: set[str] = set()
    for raw_item in items:
        item = _dict(raw_item, "item")
        item_key = item.get("item_key")
        source = item.get("source")
        source_game_id = item.get("source_game_id")
        item_status = item.get("status")
        if (
            not isinstance(item_key, str)
            or not item_key
            or item_key != item_key.strip()
            or len(item_key) > 255
            or item_key in item_keys
            or source not in allowed_sources
            or item_status not in _STATUSES
        ):
            raise BundleValidationError("Bundle item identity is invalid")
        if source_game_id is not None and (
            not isinstance(source_game_id, str)
            or not source_game_id
            or source_game_id != source_game_id.strip()
            or len(source_game_id) > 255
        ):
            raise BundleValidationError("Bundle source game identity is invalid")
        if item_status == "success" and source_game_id is None:
            raise BundleValidationError("successful Bundle item lacks source game identity")
        target_game_id = item.get("target_game_id")
        if mode == "refresh" and target_game_id not in allowed_targets:
            raise BundleValidationError("Bundle refresh target is outside claimed job")
        if mode == "discovery" and target_game_id is not None:
            raise BundleValidationError("Bundle discovery item must not have a target")
        paths: list[str] = []
        normalized_path = item.get("normalized_path")
        if normalized_path is not None:
            paths.append(_safe_relative_path(normalized_path).as_posix())
        for field_name in ("raw_evidence_paths", "media_paths"):
            paths.extend(
                _safe_relative_path(value).as_posix()
                for value in _list(item.get(field_name), field_name)
            )
        if item_status == "success" and normalized_path is None:
            raise BundleValidationError("successful Bundle item lacks normalized data")
        for relative in paths:
            if relative in referenced or relative not in inventory:
                raise BundleValidationError("Bundle item file reference is invalid")
            referenced.add(relative)
            entry = inventory[relative]
            if entry.get("source") != source:
                raise BundleValidationError("Bundle item and file source identity mismatch")
            if (
                item.get("source_game_id") is not None
                and entry.get("source_game_id") is not None
                and entry.get("source_game_id") != item.get("source_game_id")
            ):
                raise BundleValidationError("Bundle source game identity mismatch")
        item_keys.append(item_key)
    if referenced != set(inventory):
        raise BundleValidationError("Bundle inventory contains unreferenced files")
    return tuple(item_keys)


def _validate_extracted(
    root: Path,
    *,
    transfer: CollectorTransfer,
    job_manifest: dict[str, Any],
    limits: BundleValidationLimits,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file() or manifest_path.stat().st_size > limits.max_manifest_bytes:
        raise BundleValidationError("Bundle manifest is missing or exceeds byte limit")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleValidationError("Bundle manifest is not valid UTF-8 JSON") from exc
    manifest = _dict(manifest, "manifest")
    if manifest.get("schema_version") != 2 or transfer.bundle_schema_version != "2":
        raise BundleValidationError("unsupported Bundle schema version")
    _verify_correlations(manifest, transfer)
    mode, targets, sources = _verify_job(manifest, transfer, job_manifest)
    _verify_manifest_limits(manifest, limits)
    inventory = _verify_inventory(root, manifest, limits=limits)
    item_keys = _verify_items(
        manifest,
        inventory=inventory,
        mode=mode,
        allowed_targets=targets,
        allowed_sources=sources,
        job_manifest=job_manifest,
        server_max_items=limits.max_items,
    )
    return manifest, item_keys


def validate_and_extract_bundle(
    archive: Path,
    *,
    destination: Path,
    transfer: CollectorTransfer,
    job_manifest: dict[str, Any],
    limits: BundleValidationLimits,
) -> ValidatedBundle:
    """Verify the outer archive, isolate extraction, then validate schema and every file."""

    archive_size = archive.stat().st_size
    if archive_size != transfer.archive_size or archive_size > limits.max_archive_bytes:
        raise BundleValidationError("archive size differs from transfer declaration or limit")
    if _sha256_file(archive) != transfer.archive_sha256:
        raise BundleValidationError("archive SHA-256 differs from transfer declaration")
    if destination.exists():
        raise BundleValidationError("extraction destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=".collector-extract-",
            dir=destination.parent,
        )
    )
    try:
        _extract_tar_zst(archive, temporary, limits=limits)
        manifest, item_keys = _validate_extracted(
            temporary,
            transfer=transfer,
            job_manifest=job_manifest,
            limits=limits,
        )
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return ValidatedBundle(
        extracted_root=destination,
        manifest=manifest,
        item_keys=item_keys,
    )

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path

import pytest
import zstandard

from server.app.modules.collector.bundle_validation import (
    BundleValidationError,
    BundleValidationLimits,
    validate_and_extract_bundle,
)
from server.app.modules.collector.models import CollectorTransfer

NORMALIZED_PATH = "items/target-101/normalized.json"
NORMALIZED = json.dumps(
    {
        "source": "baidu",
        "source_game_id": "baidu-101",
        "requested_name": "Fixture Game",
        "status": "success",
        "name": "Fixture Game",
        "raw": {"id": "baidu-101"},
    },
    separators=(",", ":"),
).encode()


def _manifest() -> dict:
    return {
        "schema_version": 2,
        "correlations": {
            "collector_id": "collector-local-1",
            "job_id": "job-1",
            "run_id": "run-1",
            "bundle_id": "bundle-1",
            "transport_id": "transport-1",
        },
        "job": {
            "mode": "refresh",
            "destination": "geo-production",
            "policy_version": "policy-1",
            "schedule_occurrence": "2026-07-29T05:00:00Z",
            "source_order": ["baidu"],
        },
        "created_at": "2026-07-29T05:01:00Z",
        "component_versions": {"collector": "0.1.0"},
        "limits": {
            "max_items": 10,
            "max_requests": 20,
            "max_files": 20,
            "max_file_bytes": 1024 * 1024,
            "max_total_bytes": 2 * 1024 * 1024,
            "max_raw_evidence_bytes": 1024 * 1024,
            "max_media_files_per_item": 7,
        },
        "items": [
            {
                "item_key": "target-101",
                "source": "baidu",
                "source_game_id": "baidu-101",
                "target_game_id": 101,
                "status": "success",
                "normalized_path": NORMALIZED_PATH,
                "raw_evidence_paths": [],
                "media_paths": [],
                "error_category": None,
                "http_status": None,
                "stage": None,
                "attempt_count": 1,
            }
        ],
        "files": [
            {
                "path": NORMALIZED_PATH,
                "role": "normalized",
                "media_type": "application/json",
                "size_bytes": len(NORMALIZED),
                "sha256": hashlib.sha256(NORMALIZED).hexdigest(),
                "source": "baidu",
                "source_game_id": "baidu-101",
                "source_url": None,
            }
        ],
    }


def _job_manifest() -> dict:
    return {
        "schema_version": 1,
        "job_id": "job-1",
        "policy_version": "policy-1",
        "mode": "refresh",
        "source_order": ["baidu"],
        "targets": [
            {
                "target_game_id": 101,
                "name": "Fixture Game",
                "category_id": 9,
            }
        ],
        "discovery_paths": [],
        "destination": "geo-production",
    }


def _write_archive(
    tmp_path: Path,
    *,
    manifest: dict | None = None,
    member_name: str = NORMALIZED_PATH,
    member_type: bytes | None = None,
    pax_headers: dict[str, str] | None = None,
    member_bytes: bytes = NORMALIZED,
) -> tuple[Path, CollectorTransfer]:
    archive = tmp_path / "bundle.tar.zst"
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as bundle:
        manifest_bytes = json.dumps(manifest or _manifest(), separators=(",", ":")).encode()
        manifest_info = tarfile.TarInfo("manifest.json")
        manifest_info.size = len(manifest_bytes)
        bundle.addfile(manifest_info, io.BytesIO(manifest_bytes))

        member = tarfile.TarInfo(member_name)
        member.size = len(member_bytes)
        if member_type is not None:
            member.type = member_type
            if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "../../outside"
                member.size = 0
        if pax_headers:
            member.pax_headers = pax_headers
        bundle.addfile(member, io.BytesIO(member_bytes) if member.size else None)

    compressed = zstandard.ZstdCompressor().compress(tar_bytes.getvalue())
    archive.write_bytes(compressed)
    transfer = CollectorTransfer(
        id=1,
        collector_id="collector-local-1",
        transport_id="transport-1",
        job_id="job-1",
        run_id="run-1",
        bundle_id="bundle-1",
        destination="geo-production",
        object_key="incoming/collector-local-1/transport-1.tar.zst",
        archive_size=len(compressed),
        archive_sha256=hashlib.sha256(compressed).hexdigest(),
        bundle_schema_version="2",
        status="processing",
        attempt_count=1,
    )
    return archive, transfer


def _limits(**changes) -> BundleValidationLimits:
    values = {
        "max_archive_bytes": 10 * 1024 * 1024,
        "max_files": 50,
        "max_file_bytes": 2 * 1024 * 1024,
        "max_total_bytes": 5 * 1024 * 1024,
        "max_manifest_bytes": 1024 * 1024,
    }
    values.update(changes)
    return BundleValidationLimits(**values)


def test_valid_tar_zst_is_verified_before_atomic_extraction(tmp_path):
    archive, transfer = _write_archive(tmp_path)
    destination = tmp_path / "extracted"

    validated = validate_and_extract_bundle(
        archive,
        destination=destination,
        transfer=transfer,
        job_manifest=_job_manifest(),
        limits=_limits(),
    )

    assert validated.manifest["schema_version"] == 2
    assert validated.item_keys == ("target-101",)
    assert validated.extracted_root == destination
    assert (destination / NORMALIZED_PATH).read_bytes() == NORMALIZED
    assert not list(tmp_path.glob(".collector-extract-*"))


def test_bundle_rejects_items_above_issued_job_theoretical_limit(tmp_path):
    manifest = _manifest()
    manifest["items"].append(
        {
            "item_key": "target-101-extra",
            "source": "baidu",
            "source_game_id": "baidu-101",
            "target_game_id": 101,
            "status": "miss",
            "normalized_path": None,
            "raw_evidence_paths": [],
            "media_paths": [],
        }
    )
    archive, transfer = _write_archive(tmp_path, manifest=manifest)

    with pytest.raises(BundleValidationError, match="issued job limit"):
        validate_and_extract_bundle(
            archive,
            destination=tmp_path / "extracted",
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(),
        )


def test_bundle_rejects_item_key_above_receipt_column_limit(tmp_path):
    manifest = _manifest()
    manifest["items"][0]["item_key"] = "x" * 256
    archive, transfer = _write_archive(tmp_path, manifest=manifest)

    with pytest.raises(BundleValidationError, match="item identity"):
        validate_and_extract_bundle(
            archive,
            destination=tmp_path / "extracted",
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(),
        )


def test_archive_size_and_sha_are_checked_before_extraction(tmp_path):
    archive, transfer = _write_archive(tmp_path)
    destination = tmp_path / "extracted"
    transfer.archive_sha256 = "f" * 64

    with pytest.raises(BundleValidationError, match="archive SHA-256"):
        validate_and_extract_bundle(
            archive,
            destination=destination,
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(),
        )
    assert not destination.exists()

    transfer.archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    transfer.archive_size += 1
    with pytest.raises(BundleValidationError, match="archive size"):
        validate_and_extract_bundle(
            archive,
            destination=destination,
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(),
        )
    assert not destination.exists()


@pytest.mark.parametrize(
    ("member_name", "member_type", "pax_headers", "message"),
    [
        ("../escaped.json", None, None, "unsafe archive path"),
        (NORMALIZED_PATH, tarfile.SYMTYPE, None, "special archive member"),
        (NORMALIZED_PATH, None, {"geo.encrypted": "true"}, "encrypted archive member"),
    ],
)
def test_unsafe_special_and_encrypted_members_are_isolated(
    tmp_path, member_name, member_type, pax_headers, message
):
    archive, transfer = _write_archive(
        tmp_path,
        member_name=member_name,
        member_type=member_type,
        pax_headers=pax_headers,
    )
    destination = tmp_path / "extracted"

    with pytest.raises(BundleValidationError, match=message):
        validate_and_extract_bundle(
            archive,
            destination=destination,
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(),
        )
    assert not destination.exists()
    assert not (tmp_path / "escaped.json").exists()


def test_file_count_and_expanded_byte_limits_are_enforced(tmp_path):
    archive, transfer = _write_archive(tmp_path)
    with pytest.raises(BundleValidationError, match="file count"):
        validate_and_extract_bundle(
            archive,
            destination=tmp_path / "files",
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(max_files=1),
        )
    with pytest.raises(BundleValidationError, match="expanded byte"):
        validate_and_extract_bundle(
            archive,
            destination=tmp_path / "bytes",
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(max_total_bytes=10),
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda m: m.update(schema_version=1), "schema version"),
        (
            lambda m: m["correlations"].update(transport_id="transport-other"),
            "correlation",
        ),
        (
            lambda m: m["items"][0].update(target_game_id=999),
            "refresh target",
        ),
        (
            lambda m: m["files"][0].update(sha256="f" * 64),
            "file SHA-256",
        ),
        (
            lambda m: m["files"][0].update(media_type="image/png"),
            "MIME signature",
        ),
    ],
)
def test_schema_correlation_job_hash_and_mime_must_match(tmp_path, mutation, message):
    manifest = _manifest()
    mutation(manifest)
    archive, transfer = _write_archive(tmp_path, manifest=manifest)

    with pytest.raises(BundleValidationError, match=message):
        validate_and_extract_bundle(
            archive,
            destination=tmp_path / "extracted",
            transfer=transfer,
            job_manifest=_job_manifest(),
            limits=_limits(),
        )

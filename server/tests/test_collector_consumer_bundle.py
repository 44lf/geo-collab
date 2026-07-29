from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.app.modules.collector.bundle_validation import ValidatedBundle
from server.app.modules.collector.consumer_bundle import (
    import_callback,
    prepare_consumer_items,
)
from server.app.modules.collector.consumer_processing import (
    ConsumerItem,
    DiscoveryConflictError,
    IdentityValidationError,
    SchemaValidationError,
)
from server.app.modules.collector.import_service import DiscoveryImportItem, ImportOutcome


def _bundle(
    tmp_path: Path,
    *,
    mode: str = "refresh",
    status: str = "success",
    with_media: bool = False,
) -> ValidatedBundle:
    root = tmp_path / "bundle"
    normalized_path = "items/game/normalized.json"
    normalized = {
        "source": "baidu",
        "source_game_id": "baidu-1",
        "name": "Fixture",
        "icon_url": "https://img.test/icon.png",
        "screenshot_urls": ["https://img.test/shot.png"],
        "tags": [],
        "platforms": ["android"],
        "raw": {"id": "baidu-1"},
    }
    normalized_bytes = json.dumps(normalized).encode()
    (root / "items" / "game").mkdir(parents=True)
    (root / normalized_path).write_bytes(normalized_bytes)
    files = [
        {
            "path": normalized_path,
            "role": "normalized",
            "media_type": "application/json",
            "size_bytes": len(normalized_bytes),
            "sha256": hashlib.sha256(normalized_bytes).hexdigest(),
            "source": "baidu",
            "source_game_id": "baidu-1",
            "source_url": None,
        }
    ]
    media_paths: list[str] = []
    if with_media:
        for name, url in (
            ("icon.png", "https://img.test/icon.png"),
            ("shot.png", "https://img.test/shot.png"),
        ):
            path = f"items/game/{name}"
            data = b"\x89PNG\r\n\x1a\nfixture"
            (root / path).write_bytes(data)
            media_paths.append(path)
            files.append(
                {
                    "path": path,
                    "role": "media",
                    "media_type": "image/png",
                    "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "source": "baidu",
                    "source_game_id": "baidu-1",
                    "source_url": url,
                }
            )
    item = {
        "item_key": "game",
        "source": "baidu",
        "source_game_id": "baidu-1",
        "target_game_id": 7 if mode == "refresh" else None,
        "status": status,
        "normalized_path": normalized_path if status == "success" else None,
        "raw_evidence_paths": [],
        "media_paths": media_paths if status == "success" else [],
    }
    return ValidatedBundle(
        extracted_root=root,
        manifest={"job": {"mode": mode}, "items": [item], "files": files},
        item_keys=("game",),
    )


def _job() -> dict:
    return {"targets": [{"target_game_id": 7, "name": "Fixture", "category_id": 3}]}


def _update_normalized(bundle: ValidatedBundle, **changes: object) -> None:
    path = bundle.extracted_root / "items/game/normalized.json"
    normalized = json.loads(path.read_text(encoding="utf-8"))
    normalized.update(changes)
    path.write_text(json.dumps(normalized), encoding="utf-8")


def test_prepare_refresh_anchors_job_target_and_infers_validated_assets(tmp_path: Path) -> None:
    prepared = prepare_consumer_items(
        _bundle(tmp_path, with_media=True),
        job_manifest=_job(),
    )

    target = prepared[0].payload["refresh_target"]
    assert target.target_game_id == 7
    assert target.category_id == 3
    assert target.game_data["source_game_id"] == "baidu-1"
    assert [(asset.role, asset.source_url) for asset in target.assets] == [
        ("icon", "https://img.test/icon.png"),
        ("screenshot", "https://img.test/shot.png"),
    ]


def test_prepare_non_success_refresh_has_no_business_payload_or_deletion(tmp_path: Path) -> None:
    prepared = prepare_consumer_items(
        _bundle(tmp_path, status="blocked"),
        job_manifest=_job(),
    )
    target = prepared[0].payload["refresh_target"]
    assert target.status == "blocked"
    assert target.game_data is None
    assert target.assets == ()


def test_prepare_discovery_preserves_source_identity(tmp_path: Path) -> None:
    prepared = prepare_consumer_items(
        _bundle(tmp_path, mode="discovery"),
        job_manifest={"targets": []},
    )
    item = prepared[0].payload["discovery_item"]
    assert item.source == "baidu"
    assert item.source_game_id == "baidu-1"
    assert item.game_data["name"] == "Fixture"


def test_prepare_rejects_normalized_source_identity_mismatch(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _update_normalized(bundle, source="yingyongbao")

    with pytest.raises(IdentityValidationError, match="source identity"):
        prepare_consumer_items(bundle, job_manifest=_job())


def test_prepare_rejects_refresh_name_mismatch(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _update_normalized(bundle, name="Another Game")

    with pytest.raises(IdentityValidationError, match="name disagrees"):
        prepare_consumer_items(bundle, job_manifest=_job())


def test_prepare_rejects_oversized_canonical_name(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    _update_normalized(bundle, name="x" * 201)

    with pytest.raises(SchemaValidationError, match="field name is invalid"):
        prepare_consumer_items(bundle, job_manifest=_job())


def test_import_callback_uses_same_session_and_maps_outcome(tmp_path: Path) -> None:
    session = object()
    item = prepare_consumer_items(_bundle(tmp_path), job_manifest=_job())[0]
    target = item.payload["refresh_target"]
    service = SimpleNamespace()
    calls = []

    def apply(db, received):
        calls.append((db, received))
        return ImportOutcome(status="imported", game_id=99)

    service.import_refresh_target_in_session = apply

    result = import_callback(service)(session, item)

    assert result.game_id == 99
    assert calls == [(session, target)]


def test_discovery_conflict_maps_to_isolated_error() -> None:
    discovery = DiscoveryImportItem(
        item_key="game",
        source="baidu",
        source_game_id="baidu-1",
        status="success",
        game_data={
            "source": "baidu",
            "source_game_id": "baidu-1",
            "name": "Fixture",
        },
        assets=(),
    )
    item = ConsumerItem(
        item_key="game",
        source="baidu",
        source_item_id="baidu-1",
        mode="discovery",
        payload={"discovery_item": discovery},
    )
    service = SimpleNamespace(
        import_discovery_item_in_session=lambda _db, _item: ImportOutcome(
            status="conflict",
            error="ambiguous",
        )
    )

    with pytest.raises(DiscoveryConflictError, match="ambiguous"):
        import_callback(service)(object(), item)

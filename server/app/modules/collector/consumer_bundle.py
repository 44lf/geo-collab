"""Bridge validated Bundle v2 items into GEO import and receipt coordination."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.modules.collector.bundle_validation import ValidatedBundle
from server.app.modules.collector.consumer_processing import (
    ConsumerItem,
    DiscoveryConflictError,
    IdentityValidationError,
    ItemImportResult,
    SchemaValidationError,
)
from server.app.modules.collector.import_service import (
    CollectorImportService,
    DiscoveryImportItem,
    ImportContractError,
    ImportOutcome,
    canonical_game_from_payload,
)
from server.app.modules.game_library import backfill_bundle
from server.app.modules.game_library.normalization import normalize_game_name


def _mapping(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{label} must be an object")
    return value


def _read_normalized(root: Path, path: object) -> dict[str, Any]:
    if not isinstance(path, str):
        raise SchemaValidationError("successful item requires normalized_path")
    try:
        payload = json.loads((root / Path(*path.split("/"))).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SchemaValidationError("normalized item is not valid UTF-8 JSON") from exc
    return _mapping(payload, label="normalized item")


def _validated_media(
    bundle: ValidatedBundle,
    *,
    item: dict[str, Any],
    normalized: dict[str, Any] | None,
    inventory: dict[str, dict[str, Any]],
) -> tuple[backfill_bundle.ValidatedFile, ...]:
    assets: list[backfill_bundle.ValidatedFile] = []
    icon_url = normalized.get("icon_url") if normalized is not None else None
    screenshot_urls = set(normalized.get("screenshot_urls") or []) if normalized else set()
    media_paths = item.get("media_paths")
    if not isinstance(media_paths, list):
        raise SchemaValidationError("media_paths must be an array")
    for path in media_paths:
        if not isinstance(path, str) or path not in inventory:
            raise SchemaValidationError("media path is absent from Bundle inventory")
        entry = inventory[path]
        source_url = entry.get("source_url")
        if source_url == icon_url:
            role = "icon"
        elif source_url in screenshot_urls:
            role = "screenshot"
        else:
            raise SchemaValidationError("media source URL is not declared by normalized item")
        resolved = bundle.extracted_root / Path(*path.split("/"))
        assets.append(
            backfill_bundle.ValidatedFile(
                relative_path=path,
                path=resolved,
                media_type=str(entry["media_type"]),
                role=role,
                size=int(entry["size_bytes"]),
                sha256=str(entry["sha256"]),
                source_url=str(source_url),
            )
        )
    return tuple(assets)


def prepare_consumer_items(
    bundle: ValidatedBundle,
    *,
    job_manifest: dict[str, Any],
) -> tuple[ConsumerItem, ...]:
    """Prepare typed imports from a fully validated and isolated Bundle."""

    manifest = bundle.manifest
    job = _mapping(manifest.get("job"), label="Bundle job")
    mode = job.get("mode")
    if mode not in {"refresh", "discovery"}:
        raise SchemaValidationError("Bundle job mode is invalid")
    raw_inventory = manifest.get("files")
    raw_items = manifest.get("items")
    if not isinstance(raw_inventory, list) or not isinstance(raw_items, list):
        raise SchemaValidationError("Bundle items and files must be arrays")
    inventory = {
        str(entry["path"]): _mapping(entry, label="file inventory entry")
        for entry in raw_inventory
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    target_by_id = {
        target["target_game_id"]: target
        for target in job_manifest.get("targets", [])
        if isinstance(target, dict) and isinstance(target.get("target_game_id"), int)
    }
    prepared: list[ConsumerItem] = []
    for raw_item in raw_items:
        item = _mapping(raw_item, label="Bundle item")
        status = item.get("status")
        normalized = (
            _read_normalized(bundle.extracted_root, item.get("normalized_path"))
            if status == "success"
            else None
        )
        canonical = None
        if normalized is not None:
            try:
                canonical = canonical_game_from_payload(normalized)
            except ImportContractError as exc:
                raise SchemaValidationError(str(exc)) from exc
            if canonical.source != item.get("source") or canonical.game_id != item.get(
                "source_game_id"
            ):
                raise IdentityValidationError(
                    "normalized game source identity disagrees with Bundle item"
                )
        assets = _validated_media(
            bundle,
            item=item,
            normalized=normalized,
            inventory=inventory,
        )
        payload: dict[str, Any]
        if mode == "refresh":
            target_id = item.get("target_game_id")
            if not isinstance(target_id, int) or isinstance(target_id, bool):
                raise IdentityValidationError("refresh target identity is invalid")
            target = target_by_id.get(target_id)
            if target is None:
                raise IdentityValidationError("refresh target is absent from issued job")
            target_name = target.get("name")
            if not isinstance(target_name, str) or not target_name.strip():
                raise IdentityValidationError("refresh target name is invalid")
            if canonical is not None and (
                normalize_game_name(canonical.name) != normalize_game_name(target_name)
            ):
                raise IdentityValidationError(
                    "normalized game name disagrees with issued refresh target"
                )
            payload = {
                "refresh_target": backfill_bundle.ValidatedTarget(
                    target_game_id=target_id,
                    name=target_name,
                    category_id=target.get("category_id"),
                    status=str(status),
                    game_file=None,
                    game_data=normalized,
                    assets=assets,
                )
            }
        else:
            payload = {
                "discovery_item": DiscoveryImportItem(
                    item_key=str(item["item_key"]),
                    source=str(item["source"]),
                    source_game_id=item.get("source_game_id"),
                    status=str(status),
                    game_data=normalized,
                    assets=assets,
                )
            }
        prepared.append(
            ConsumerItem(
                item_key=str(item["item_key"]),
                source=str(item["source"]),
                source_item_id=item.get("source_game_id"),
                mode=mode,
                payload=payload,
            )
        )
    return tuple(prepared)


def import_callback(
    service: CollectorImportService,
) -> Callable[[Any, ConsumerItem], ItemImportResult]:
    """Adapt GEO import outcomes to the Consumer transaction coordinator."""

    def apply(db: Any, item: ConsumerItem) -> ItemImportResult:
        try:
            if item.mode == "refresh":
                target = item.payload.get("refresh_target")
                if not isinstance(target, backfill_bundle.ValidatedTarget):
                    raise SchemaValidationError("refresh item payload is invalid")
                outcome = service.import_refresh_target_in_session(db, target)
            else:
                discovery = item.payload.get("discovery_item")
                if not isinstance(discovery, DiscoveryImportItem):
                    raise SchemaValidationError("discovery item payload is invalid")
                outcome = service.import_discovery_item_in_session(db, discovery)
        except ImportContractError as exc:
            raise IdentityValidationError(str(exc)) from exc
        return _item_result(outcome)

    return apply


def _item_result(outcome: ImportOutcome) -> ItemImportResult:
    if outcome.status == "conflict":
        raise DiscoveryConflictError(outcome.error or "discovery identity conflict")
    if outcome.status == "skipped":
        return ItemImportResult.skipped(outcome={"import_status": "skipped"})
    if outcome.status != "imported" or outcome.game_id is None:
        raise IdentityValidationError(outcome.error or "GEO import did not return a game identity")
    return ItemImportResult.succeeded(
        game_id=outcome.game_id,
        outcome={"import_status": "imported"},
    )

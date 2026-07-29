"""Validate and import a Windows TapTap collector bundle inside GEO production."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from server.app.modules.collector.import_service import (
    CollectorImportService,
    SessionFactory,
    UpsertFunc,
    canonical_game_from_payload,
    validated_asset_bytes,
)
from server.app.modules.game_library import backfill_bundle


def _to_game(payload: dict[str, Any]):
    """Backward-compatible private alias for existing diagnostic callers."""

    return canonical_game_from_payload(payload)


def _asset_bytes(target: backfill_bundle.ValidatedTarget):
    """Backward-compatible private alias for existing diagnostic callers."""

    return validated_asset_bytes(target)


def _plan_target(target: backfill_bundle.ValidatedTarget) -> dict[str, Any]:
    return {
        "target_game_id": target.target_game_id,
        "name": target.name,
        "category_id": target.category_id,
        "status": target.status,
        "images": len(target.assets),
        "icon_images": sum(asset.role == "icon" for asset in target.assets),
        "screenshots": sum(asset.role == "screenshot" for asset in target.assets),
    }


def plan_bundle(bundle_root: str | Path) -> tuple[backfill_bundle.ValidatedBundle, dict[str, Any]]:
    bundle = backfill_bundle.validate_bundle(bundle_root)
    targets = [_plan_target(target) for target in bundle.targets]
    plan = {
        "bundle_id": bundle.bundle_id,
        "collector_version": bundle.collector_version,
        "source": bundle.source,
        "planned": sum(target.status == "success" for target in bundle.targets),
        "skipped": sum(target.status != "success" for target in bundle.targets),
        "imported": 0,
        "failed": 0,
        "targets": targets,
    }
    return bundle, plan


def run_import(
    bundle_root: str | Path,
    *,
    dry_run: bool,
    session_factory: SessionFactory | None = None,
    upsert_func: UpsertFunc | None = None,
) -> dict[str, Any]:
    """Validate the full bundle, then optionally import each successful target."""
    bundle, result = plan_bundle(bundle_root)
    if dry_run:
        result["dry_run"] = True
        return result

    result["dry_run"] = False
    result["planned"] = sum(target.status == "success" for target in bundle.targets)
    service = CollectorImportService(
        session_factory=session_factory,
        upsert_func=upsert_func,
    )
    for target, target_result in zip(bundle.targets, result["targets"], strict=True):
        outcome = service.import_refresh_target(target)
        target_result["import_status"] = outcome.status
        if outcome.status == "imported":
            result["imported"] += 1
        elif outcome.status == "failed":
            if outcome.error:
                target_result["error"] = outcome.error
            result["failed"] += 1
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="remote_game_bundle_import",
        description="Validate or import a Windows game collector directory bundle.",
    )
    parser.add_argument("bundle", type=Path, help="extracted bundle directory")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the import plan without opening a database session",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_import(args.bundle, dry_run=args.dry_run)
    except backfill_bundle.BundleValidationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps({"ok": result["failed"] == 0, **result}, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

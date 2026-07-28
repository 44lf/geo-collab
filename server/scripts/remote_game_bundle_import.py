"""Validate and import a Windows TapTap collector bundle inside GEO production."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from server.app.modules.game_library import backfill_bundle, types
from server.app.modules.game_library.models import Game as GameRow

SessionFactory = Callable[[], Any]
UpsertFunc = Callable[..., Any]


def _to_game(payload: dict[str, Any]) -> types.Game:
    raw = payload["game"]
    return types.Game(
        source=raw["source"],
        game_id=raw["game_id"],
        name=raw["name"],
        score=raw.get("score"),
        tags=list(raw.get("tags") or []),
        platforms=list(raw.get("platforms") or []),
        comment_count=raw.get("comment_count"),
        icon_url=raw.get("icon_url"),
        screenshot_urls=list(raw.get("screenshot_urls") or []),
        android_package=raw.get("android_package"),
        description=raw.get("description"),
        raw=dict(raw.get("raw") or {}),
    )


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


def _asset_bytes(
    target: backfill_bundle.ValidatedTarget,
) -> tuple[
    tuple[str, bytes, str] | None,
    list[tuple[str, bytes, str]],
]:
    icon = None
    screenshots = []
    for asset in target.assets:
        item = (asset.source_url or "", asset.path.read_bytes(), asset.media_type)
        if asset.role == "icon" and icon is None:
            icon = item
        elif asset.role == "screenshot":
            screenshots.append(item)
    return icon, screenshots


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

    if session_factory is None:
        from server.app.db.session import SessionLocal

        session_factory = SessionLocal
    if upsert_func is None:
        from server.app.modules.game_library.service import upsert_game

        upsert_func = upsert_game

    result["dry_run"] = False
    result["planned"] = sum(target.status == "success" for target in bundle.targets)
    for target, target_result in zip(bundle.targets, result["targets"], strict=True):
        if target.status != "success":
            target_result["import_status"] = "skipped"
            continue
        if target.game_data is None:
            # This is guaranteed by validation, but keep the write path defensive.
            target_result["import_status"] = "failed"
            target_result["error"] = "validated success target has no game data"
            result["failed"] += 1
            continue

        db = session_factory()
        try:
            game = _to_game(target.game_data)
            game_row = db.get(GameRow, target.target_game_id)
            if game_row is None:
                raise RuntimeError(f"production game {target.target_game_id} not found")
            if target.category_id is not None and game_row.stock_category_id != target.category_id:
                raise RuntimeError(
                    f"production game {target.target_game_id} category changed: "
                    f"bundle={target.category_id} current={game_row.stock_category_id}"
                )
            icon, screenshots = _asset_bytes(target)
            upsert_func(
                db,
                game,
                max_screenshots=len(screenshots),
                pre_downloaded=screenshots,
                category_id=game_row.stock_category_id,
                game_row=game_row,
                pre_downloaded_icon=icon,
            )
            db.commit()
            target_result["import_status"] = "imported"
            result["imported"] += 1
        except Exception as exc:
            db.rollback()
            target_result["import_status"] = "failed"
            target_result["error"] = f"{type(exc).__name__}: {exc}"
            result["failed"] += 1
        finally:
            db.close()
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

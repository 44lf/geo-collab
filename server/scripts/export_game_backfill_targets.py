"""Export an explicit Windows TapTap collector target manifest from production."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from server.app.modules.game_library.models import Game


def build_manifest(
    rows: list[Any],
    *,
    bundle_id: str,
    max_screenshots: int,
    delay_seconds: int,
) -> dict:
    if not 1 <= len(rows) <= 20:
        raise ValueError("target rows must contain between 1 and 20 games")
    if not bundle_id or any(char not in "._-" and not char.isalnum() for char in bundle_id):
        raise ValueError("bundle_id must use only letters, digits, dot, underscore, or hyphen")
    if not 0 <= max_screenshots <= 6:
        raise ValueError("max_screenshots must be between 0 and 6")
    if not 0 <= delay_seconds <= 3600:
        raise ValueError("delay_seconds must be between 0 and 3600")

    targets = []
    seen_ids = set()
    for row in rows:
        if row.id in seen_ids:
            raise ValueError(f"duplicate game id {row.id}")
        seen_ids.add(row.id)
        targets.append(
            {
                "target_game_id": row.id,
                "name": row.name,
                "category_id": row.stock_category_id,
                "icon_local": bool(row.icon_url and row.icon_url.startswith("/api/stock-images/")),
            }
        )
    return {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "max_screenshots": max_screenshots,
        "delay_seconds": delay_seconds,
        "targets": targets,
    }


def select_target_rows(db, *, limit: int, game_ids: list[int] | None = None) -> list[Game]:
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    if game_ids:
        if len(game_ids) > 20 or len(set(game_ids)) != len(game_ids):
            raise ValueError("game_ids must contain between 1 and 20 unique ids")
        rows = list(
            db.execute(select(Game).where(Game.id.in_(game_ids), Game.is_active.is_(True)))
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in rows}
        missing = [game_id for game_id in game_ids if game_id not in by_id]
        if missing:
            raise ValueError(f"active game ids not found: {missing}")
        return [by_id[game_id] for game_id in game_ids]
    return list(
        db.execute(
            select(Game)
            .where(Game.is_active.is_(True))
            .order_by(Game.last_verified_at.asc(), Game.id.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    default_bundle_id = datetime.now(UTC).strftime("taptap-stability-%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(
        prog="export_game_backfill_targets",
        description="Export a bounded read-only target manifest for the Windows collector.",
    )
    parser.add_argument("output", type=Path, help="output targets.json path")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--game-id", type=int, action="append", dest="game_ids")
    parser.add_argument("--bundle-id", default=default_bundle_id)
    parser.add_argument("--max-screenshots", type=int, default=3)
    parser.add_argument("--delay-seconds", type=int, default=30)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from server.app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = select_target_rows(db, limit=args.limit, game_ids=args.game_ids)
    finally:
        db.close()
    manifest = build_manifest(
        rows,
        bundle_id=args.bundle_id,
        max_screenshots=args.max_screenshots,
        delay_seconds=args.delay_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8-sig",
    )
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(args.output),
                "bundle_id": args.bundle_id,
                "targets": len(rows),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

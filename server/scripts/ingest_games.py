"""手动入库 CLI：python -m server.scripts.ingest_games --source taptap --category 国风"""

from __future__ import annotations

import argparse

from server.app.db.session import SessionLocal
from server.app.modules.game_library.scheduler import run_ingest_once


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ingest_games")
    parser.add_argument("--source", required=True, choices=["taptap", "baidu"])
    parser.add_argument("--category", required=True)
    parser.add_argument("--pages", type=int, default=2)
    parser.add_argument("--max-games", type=int, default=30)
    parser.add_argument("--max-shots", type=int, default=6)
    args = parser.parse_args(argv)

    targets = [
        {
            "source": args.source,
            "category": args.category,
            "pages": args.pages,
            "max_games": args.max_games,
            "max_shots": args.max_shots,
        }
    ]
    result = run_ingest_once(SessionLocal, targets=targets)
    print(f"入库完成：{result}")
    if int(result.get("failed") or 0) > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

"""CLI: 导入图库栏目为游戏记录。"""

from server.app.db.session import SessionLocal
from server.app.modules.game_library.importer import import_image_categories_as_games


def main():
    db = SessionLocal()
    try:
        r = import_image_categories_as_games(db)
        db.commit()
        print(
            f"导入完成: scanned={r['scanned']} created={r['created']} "
            f"attached={r['attached']} skipped={r['skipped']}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()

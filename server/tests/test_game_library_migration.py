"""游戏库迁移 0067 的真跑验证：games/game_tags 新表 + stock_images 扩 5 列。

真跑 alembic upgrade（而非只用 create_all）是为了让 revision id / DDL 本身的错误（写错
down_revision、外键指错表、约束名冲突）能在测试里暴露，光靠 ORM create_all 测不出来。

安全前提：任何 alembic upgrade/downgrade 之前，必须先把 GEO_DATABASE_URL 显式 monkeypatch
指向 get_test_database_url()——server/alembic/env.py 会用 get_database_url() 无条件覆盖
Config 里手设的 sqlalchemy.url，若不重定向环境变量，升级会打到 .env 里配置的共享开发库
（geo_dev），见 test_qref_external_ingestion_migration.py 的既有用法。本测试严禁对 geo_dev
做任何 alembic 操作。
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from server.app.core.config import get_settings
from server.tests.utils import (
    get_test_database_url,
    reset_test_database,
)


def _new_temp_data_dir() -> Path:
    return Path(tempfile.gettempdir()) / "geo-test-data" / uuid.uuid4().hex


@pytest.mark.mysql
def test_migration_0067_creates_game_library_tables_and_columns(monkeypatch):
    data_dir = _new_temp_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(get_test_database_url(), pool_pre_ping=True)
    # 从真正的空库开始跑完整迁移链（共享 schema 一般是上一个 build_test_app 用 ORM
    # create_all 建的、无 alembic_version 记录）。
    reset_test_database(engine, create_schema=False)

    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GEO_DATABASE_URL", get_test_database_url())
    get_settings.cache_clear()

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", get_test_database_url().replace("%", "%%"))
        command.upgrade(cfg, "head")  # 真跑迁移链——revision id / DDL 错误会在此崩

        insp = inspect(engine)
        table_names = set(insp.get_table_names())
        assert {"games", "game_tags"} <= table_names

        stock_image_cols = {c["name"] for c in insp.get_columns("stock_images")}
        assert {
            "source_url",
            "source_url_hash",
            "use_count",
            "last_used_at",
            "last_used_article_id",
        } <= stock_image_cols

        game_uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("games")}
        assert ("name_normalized",) in game_uniques

        game_tag_uniques = {
            tuple(u["column_names"]) for u in insp.get_unique_constraints("game_tags")
        }
        assert ("game_id", "tag") in game_tag_uniques

        stock_image_uniques = {
            tuple(u["column_names"]) for u in insp.get_unique_constraints("stock_images")
        }
        assert ("category_id", "source_url_hash") in stock_image_uniques

        # game_tags.game_id FK 必须 ON DELETE CASCADE
        game_tag_fks = insp.get_foreign_keys("game_tags")
        game_id_fk = next(fk for fk in game_tag_fks if fk["constrained_columns"] == ["game_id"])
        assert game_id_fk["options"].get("ondelete") == "CASCADE"

        # 幂等回滚重升：downgrade 到游戏库三版之前（其父 0066_article_content_type）应干净丢掉
        # 两张新表 + stock_images 的 5 个新列。用显式 revision 而非 "-1"——游戏库自身叠了三版
        # （0067/0068/0069），"-1" 只回退最新一版，故必须指名回退目标；回退到父版而非更早的 0064，
        # 避免连带拆掉与本测试无关的上游 xhs 迁移（0065/0066）。再 upgrade 应无错重建。
        command.downgrade(cfg, "0066_article_content_type")
        insp2 = inspect(engine)
        table_names2 = set(insp2.get_table_names())
        assert not ({"games", "game_tags"} & table_names2)
        stock_image_cols2 = {c["name"] for c in insp2.get_columns("stock_images")}
        assert not (
            {
                "source_url",
                "source_url_hash",
                "use_count",
                "last_used_at",
                "last_used_article_id",
            }
            & stock_image_cols2
        )

        command.upgrade(cfg, "head")
        insp3 = inspect(engine)
        table_names3 = set(insp3.get_table_names())
        assert {"games", "game_tags"} <= table_names3
        stock_image_cols3 = {c["name"] for c in insp3.get_columns("stock_images")}
        assert {
            "source_url",
            "source_url_hash",
            "use_count",
            "last_used_at",
            "last_used_article_id",
        } <= stock_image_cols3
    finally:
        # 清空至真正的空库，留给下一个 build_test_app 用 create_schema=True 重建。
        reset_test_database(engine, create_schema=False)
        engine.dispose()
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()

"""对抗评审质量门增量 — 迁移 0063 的真跑验证：quality_reference.category 单列 → 子表。

真跑 alembic upgrade（而非只用 create_all）是为了让 revision id / 回填 SQL 本身的错误
（写错 down_revision、JOIN 拿错列）能在测试里暴露。核心断言：
  - own 参考（article_id 指向一篇有 source_question_texts 的文章）→ 子表 question_texts
    从该文章拷贝；
  - external 参考（article_id NULL）→ 子表 question_texts NULL；
  - quality_reference 不再有 category 列，content_hash / article_id UNIQUE 原样保留。

安全前提：任何 alembic upgrade/downgrade 之前，必须先把 GEO_DATABASE_URL 显式 monkeypatch
指向 get_test_database_url()——server/alembic/env.py 会用 get_database_url() 无条件覆盖
Config 里手设的 sqlalchemy.url，若不重定向环境变量，升级会打到 .env 里配置的共享开发库
（geo_dev），见 test_adversarial_migration.py 的既有用法。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from server.app.core.config import get_settings
from server.tests.utils import (
    get_test_database_url,
    reset_test_database,
)


def _new_temp_data_dir() -> Path:
    return Path(tempfile.gettempdir()) / "geo-test-data" / uuid.uuid4().hex


def _qref_columns(insp) -> set[str]:
    return {c["name"] for c in insp.get_columns("quality_reference")}


@pytest.mark.mysql
def test_migration_0063_backfills_child_and_drops_category(monkeypatch):
    data_dir = _new_temp_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(get_test_database_url(), pool_pre_ping=True)
    # 从真正的空库开始跑完整迁移链（共享 schema 一般是 ORM create_all 建的、无 alembic_version）。
    reset_test_database(engine, create_schema=False)

    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GEO_DATABASE_URL", get_test_database_url())
    get_settings.cache_clear()

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", get_test_database_url())

        # 升到 0062（category 单列仍在），插入 own + external 两条参考再升 head 验回填。
        command.upgrade(cfg, "0062_adversarial_review")

        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (id, username, password_hash, created_at) "
                    "VALUES (1, 'u1', 'x', NOW())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO articles "
                    "(id, user_id, title, content_json, content_html, plain_text, "
                    " source_question_category, source_question_texts, created_at, updated_at) "
                    "VALUES (1, 1, '本站文章', '{}', '', 'own正文', '餐厅', "
                    "        :qt, NOW(), NOW())"
                ),
                {"qt": json.dumps(["问题一", "问题二"], ensure_ascii=False)},
            )
            # own 参考：article_id=1（有 source_question_texts）、category='餐厅'
            conn.execute(
                text(
                    "INSERT INTO quality_reference "
                    "(id, origin, article_id, title, content_json, content_html, plain_text, "
                    " content_hash, category, is_active, created_at, updated_at) "
                    "VALUES (1, 'own', 1, '本站文章', '{}', '', 'own正文', "
                    "        'hash_own', '餐厅', 1, NOW(), NOW())"
                )
            )
            # external 参考：article_id=NULL、category='酒店' → 回填 question_texts 应为 NULL
            conn.execute(
                text(
                    "INSERT INTO quality_reference "
                    "(id, origin, article_id, title, content_json, content_html, plain_text, "
                    " content_hash, category, is_active, created_at, updated_at) "
                    "VALUES (2, 'external', NULL, '站外参考', '{}', '', 'ext正文', "
                    "        'hash_ext', '酒店', 1, NOW(), NOW())"
                )
            )

        command.upgrade(cfg, "head")  # 跑到 0063：建子表 + 回填 + 删 category 列

        insp = inspect(engine)
        assert "quality_reference_category" in insp.get_table_names()
        assert "category" not in _qref_columns(insp)  # 单列已删

        # content_hash / article_id UNIQUE 铁律：原样保留
        uniques = {
            tuple(u["column_names"]) for u in insp.get_unique_constraints("quality_reference")
        }
        assert ("content_hash",) in uniques and ("article_id",) in uniques

        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT reference_id, category, question_texts "
                    "FROM quality_reference_category ORDER BY reference_id"
                )
            ).all()
        assert len(rows) == 2
        by_ref = {r[0]: (r[1], r[2]) for r in rows}
        # own：category 保留 + question_texts 从 article 拷贝
        own_cat, own_qt = by_ref[1]
        assert own_cat == "餐厅"
        assert json.loads(own_qt) == ["问题一", "问题二"]
        # external：category 保留 + question_texts NULL（无 join）
        ext_cat, ext_qt = by_ref[2]
        assert ext_cat == "酒店"
        assert ext_qt is None

        # 幂等回滚重升：downgrade 回单列（lossy best-effort）再 upgrade 回子表
        command.downgrade(cfg, "-1")
        insp2 = inspect(engine)
        assert "quality_reference_category" not in insp2.get_table_names()
        assert "category" in _qref_columns(insp2)
        command.upgrade(cfg, "head")
        insp3 = inspect(engine)
        assert "category" not in _qref_columns(insp3)
        with engine.connect() as conn:
            n = conn.execute(text("SELECT COUNT(*) FROM quality_reference_category")).scalar()
        assert n == 2
    finally:
        # 清空至真正的空库，留给下一个 build_test_app 用 create_schema=True 重建。
        reset_test_database(engine, create_schema=False)
        engine.dispose()
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()

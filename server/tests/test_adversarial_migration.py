"""对抗评审质量门 — 迁移 0062 的真跑验证：articles +3 列 + quality_reference 表。

真跑 alembic downgrade→upgrade（而非只用 create_all）是为了让 revision id / 迁移脚本本身
的错误（写错 down_revision、SQL 语法错）能在测试里暴露，光靠 ORM create_all 测不出来。

安全前提：任何 alembic upgrade/downgrade 调用之前，必须先把 GEO_DATABASE_URL 显式 monkeypatch
指向 get_test_database_url()——server/alembic/env.py 会用 get_database_url() 无条件覆盖
Config 里手设的 sqlalchemy.url，若不重定向环境变量，降级/升级会打到 .env 里配置的共享开发库
（geo_dev），见 test_fts_and_migrations.py 的既有用法。
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
    build_test_app,
    get_test_database_url,
    reset_test_database,
)


def _new_temp_data_dir() -> Path:
    return Path(tempfile.gettempdir()) / "geo-test-data" / uuid.uuid4().hex


@pytest.mark.mysql
def test_alembic_upgrade_creates_qref_and_columns(monkeypatch):
    # 共享测试库的 schema 通常是上一个 build_test_app 用 ORM create_all 建的（无
    # alembic_version 记录），直接 downgrade("base") 对 alembic 而言是「当前已在 base」的
    # 空操作，紧接着 upgrade("head") 会撞上物理已存在的表。所以先把库整个清空
    # （reset_test_database(create_schema=False)），从真正的空库开始跑完整迁移链。
    data_dir = _new_temp_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(get_test_database_url(), pool_pre_ping=True)
    reset_test_database(engine, create_schema=False)

    # 必须先重定向 GEO_DATABASE_URL，否则 env.py 会用 get_database_url() 解析出的共享开发库
    # 覆盖下面 cfg.set_main_option 传的 url，导致 upgrade/downgrade 打到 geo_dev。
    monkeypatch.setenv("GEO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("GEO_DATABASE_URL", get_test_database_url())
    get_settings.cache_clear()

    try:
        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", get_test_database_url())
        command.upgrade(cfg, "head")  # 真跑迁移链——revision id 写错会在此崩

        insp = inspect(engine)
        assert "quality_reference" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("articles")}
        assert {"source_question_category", "source_question_texts", "adversarial_score"} <= cols
        # review_status CHECK 未变
        rs = next(
            c
            for c in insp.get_check_constraints("articles")
            if c["name"] == "ck_articles_review_status"
        )
        assert "adversarial_pending" not in rs["sqltext"]
        # 幂等回滚重升
        command.downgrade(cfg, "-1")
        command.upgrade(cfg, "head")
    finally:
        # 清空至真正的空库，留给下一个 build_test_app 用 create_schema=True 重建（含 qref
        # ngram 索引），避免本测试留下的 alembic-built schema 与共享 schema 缓存标记不一致。
        reset_test_database(engine, create_schema=False)
        engine.dispose()
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()


@pytest.mark.mysql
def test_qref_constraints_present(monkeypatch):
    app_ctx = build_test_app(monkeypatch)
    try:
        insp = inspect(app_ctx.engine)
        uniques = {
            tuple(u["column_names"]) for u in insp.get_unique_constraints("quality_reference")
        }
        assert ("article_id",) in uniques and ("content_hash",) in uniques
    finally:
        app_ctx.cleanup()

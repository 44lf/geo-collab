"""qref 外部参考异步入库 — 迁移 0064 的真跑验证：图片资源表 + 关联表 + 导入 job 表。

真跑 alembic upgrade（而非只用 create_all）是为了让 revision id / DDL 本身的错误（写错
down_revision、外键指错表、约束名冲突）能在测试里暴露，光靠 ORM create_all 测不出来。

安全前提：任何 alembic upgrade/downgrade 之前，必须先把 GEO_DATABASE_URL 显式 monkeypatch
指向 get_test_database_url()——server/alembic/env.py 会用 get_database_url() 无条件覆盖
Config 里手设的 sqlalchemy.url，若不重定向环境变量，升级会打到 .env 里配置的共享开发库
（geo_dev），见 test_qref_category_migration.py / test_adversarial_migration.py 的既有用法。
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
def test_migration_0064_creates_image_link_and_job_tables(monkeypatch):
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
        cfg.set_main_option("sqlalchemy.url", get_test_database_url())
        command.upgrade(cfg, "head")  # 真跑迁移链——revision id / DDL 错误会在此崩

        insp = inspect(engine)
        table_names = set(insp.get_table_names())
        assert {
            "quality_reference_image",
            "quality_reference_image_link",
            "quality_reference_import_job",
        } <= table_names

        image_uniques = {
            tuple(u["column_names"]) for u in insp.get_unique_constraints("quality_reference_image")
        }
        assert ("sha256",) in image_uniques

        link_uniques = {
            tuple(u["column_names"])
            for u in insp.get_unique_constraints("quality_reference_image_link")
        }
        assert ("reference_id", "image_id") in link_uniques

        link_fks = {fk["name"] for fk in insp.get_foreign_keys("quality_reference_image_link")}
        assert "fk_qref_image_link_reference_id" in link_fks
        assert "fk_qref_image_link_image_id" in link_fks
        # reference_id FK 必须 ON DELETE CASCADE
        ref_fk = next(
            fk
            for fk in insp.get_foreign_keys("quality_reference_image_link")
            if fk["name"] == "fk_qref_image_link_reference_id"
        )
        assert ref_fk["options"].get("ondelete") == "CASCADE"

        job_uniques = {
            tuple(u["column_names"])
            for u in insp.get_unique_constraints("quality_reference_import_job")
        }
        assert ("job_id",) in job_uniques

        # 幂等回滚重升：downgrade 应干净丢掉三张表，再 upgrade 应无错重建。
        command.downgrade(cfg, "-1")
        insp2 = inspect(engine)
        table_names2 = set(insp2.get_table_names())
        assert not (
            {
                "quality_reference_image",
                "quality_reference_image_link",
                "quality_reference_import_job",
            }
            & table_names2
        )
        command.upgrade(cfg, "head")
        insp3 = inspect(engine)
        table_names3 = set(insp3.get_table_names())
        assert {
            "quality_reference_image",
            "quality_reference_image_link",
            "quality_reference_import_job",
        } <= table_names3
    finally:
        # 清空至真正的空库，留给下一个 build_test_app 用 create_schema=True 重建。
        reset_test_database(engine, create_schema=False)
        engine.dispose()
        shutil.rmtree(data_dir, ignore_errors=True)
        get_settings.cache_clear()

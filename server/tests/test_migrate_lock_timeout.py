"""迁移期 lock_wait_timeout 辅助逻辑的回归测试。

背景（2026-07-16 0064 部署事故）：`apply_migration_lock_timeout` 用
`connection.exec_driver_sql("SET SESSION ...")` 设超时。在 SQLAlchemy 2.0 里
`exec_driver_sql` 会 **autobegin 一个事务**；若函数不提交它，连接就一直挂着一个未提交事务。
随后 alembic 的 `context.begin_transaction()` 见连接已在事务中，按"不提交我没开的事务"
的语义**不再负责 commit**，于是最后一个迁移写 `alembic_version` 的 UPDATE 在连接关闭时
被回滚——DDL 因 MySQL 隐式提交仍落库（表建好了），版本号却停在上一版。

所以本函数执行后，连接**必须处于无活动事务状态**，把干净连接交还给 alembic。
"""

import pytest
from sqlalchemy import create_engine

from server.app.db.migrate_support import apply_migration_lock_timeout
from server.tests.utils import get_test_database_url

pytestmark = pytest.mark.mysql


def test_lock_timeout_leaves_connection_without_open_transaction(monkeypatch):
    monkeypatch.setenv("GEO_MIGRATE_LOCK_WAIT_TIMEOUT", "60")
    engine = create_engine(get_test_database_url())
    try:
        with engine.connect() as conn:
            apply_migration_lock_timeout(conn)
            # 修复前：exec_driver_sql 的 autobegin 让这里为 True，阻断 alembic 的版本 bump。
            # 修复后：函数 commit 关掉空事务，连接干净。
            assert conn.in_transaction() is False
            # 且 lock_wait_timeout 确实生效：SET SESSION 是会话级、commit 不重置它。
            val = conn.exec_driver_sql("SELECT @@SESSION.lock_wait_timeout").scalar()
            assert int(val) == 60
    finally:
        engine.dispose()


def test_lock_timeout_noop_without_env(monkeypatch):
    monkeypatch.delenv("GEO_MIGRATE_LOCK_WAIT_TIMEOUT", raising=False)
    engine = create_engine(get_test_database_url())
    try:
        with engine.connect() as conn:
            apply_migration_lock_timeout(conn)
            # 未设变量：函数不跑任何语句，连接不进事务。
            assert conn.in_transaction() is False
    finally:
        engine.dispose()

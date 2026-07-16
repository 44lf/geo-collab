"""Alembic 迁移期的连接辅助逻辑，抽出来供 alembic/env.py 调用并可被单元测试。

放这里而不是 env.py 内部：env.py 在模块顶层就会真正执行迁移（`run_migrations_online()`），
所以它不可被测试 import。把纯粹作用于连接的逻辑抽到本模块，既能单测又不改变 env.py 行为。
"""

from __future__ import annotations


def apply_migration_lock_timeout(connection) -> None:
    """迁移期给会话设置有界 lock_wait_timeout（元数据锁等待上限，单位秒）。

    默认 lock_wait_timeout 约一年：若旧 app/worker 仍持有 articles 的 MDL，
    `ALTER TABLE` 会静默挂起（见 2026-07-15 部署事故）。设了 ``GEO_MIGRATE_LOCK_WAIT_TIMEOUT``
    后，拿不到锁会在 N 秒后抛 1205 快速失败，而非无限等待。未设变量或非 MySQL 则行为完全不变。
    """
    import os

    raw = os.getenv("GEO_MIGRATE_LOCK_WAIT_TIMEOUT")
    if not raw or connection.dialect.name != "mysql":
        return
    connection.exec_driver_sql(f"SET SESSION lock_wait_timeout = {int(raw)}")
    # exec_driver_sql 在 SQLAlchemy 2.0 会 autobegin 一个事务。必须立即 commit 关掉它，
    # 否则这个未提交事务一直挂到连接关闭：随后 alembic 的 begin_transaction() 见连接已在
    # 事务中就不接管、也不提交，最后一个迁移写 alembic_version 的 UPDATE 便在连接关闭时被
    # 回滚——DDL 因 MySQL 隐式提交仍落库（表建好了），版本号却停在上一版
    # （2026-07-16 0064 部署：表全建好但 alembic_version 停在 0063）。SET SESSION 是会话级、
    # commit 不重置它，故 lock_wait_timeout 对后续所有 DDL 仍然生效。
    connection.commit()

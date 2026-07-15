import os
from logging.config import fileConfig

import server.app.modules.accounts.models  # noqa: F401
import server.app.modules.ai_generation.models  # noqa: F401
import server.app.modules.articles.models  # noqa: F401
import server.app.modules.image_library.models  # noqa: F401
import server.app.modules.loop_skills.models  # noqa: F401
import server.app.modules.pipelines.models  # noqa: F401
import server.app.modules.prompt_templates.models  # noqa: F401
import server.app.modules.report.models  # noqa: F401
import server.app.modules.skills.models  # noqa: F401
import server.app.modules.system.models  # noqa: F401
import server.app.modules.tasks.models  # noqa: F401
from server.app.core.paths import ensure_data_dirs, get_database_url
from server.app.db.base import Base
from sqlalchemy import engine_from_config

from alembic import context

config = context.config
# 转义 % → %%：alembic 把 URL 存进 ConfigParser，BasicInterpolation 会把单个 % 当插值语法
# 报错（如密码里 URL 编码的 %21="!"）。先转义，configparser 读取时插值再还原回 %，
# 对不含 % 的 URL 无副作用。
config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))

if config.config_file_name is not None:
    # disable_existing_loggers 默认为 True，会把已存在的 logger（含应用各模块 logger）
    # 的 .disabled 置 True。当迁移在进程内运行（测试里 command.upgrade、或将来 app 内
    # 触发迁移）时，这会永久禁掉应用 logger，导致后续 caplog/日志断言全部失效。
    # 这里只想加载 alembic 自己的日志格式，绝不影响既有 logger。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    ensure_data_dirs()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


def _apply_migration_lock_timeout(connection) -> None:
    """迁移期给会话设置有界 lock_wait_timeout（元数据锁等待上限，单位秒）。

    默认 lock_wait_timeout 约一年：若旧 app/worker 仍持有 articles 的 MDL，
    `ALTER TABLE` 会静默挂起（见 2026-07-15 部署事故）。设了本环境变量后，拿不到锁
    会在 N 秒后抛 1205 快速失败，而非无限等待。未设变量或非 MySQL 则行为完全不变。
    """
    raw = os.getenv("GEO_MIGRATE_LOCK_WAIT_TIMEOUT")
    if not raw or connection.dialect.name != "mysql":
        return
    connection.exec_driver_sql(f"SET SESSION lock_wait_timeout = {int(raw)}")


def run_migrations_online() -> None:
    ensure_data_dirs()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )

    with connectable.connect() as connection:
        _apply_migration_lock_timeout(connection)
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

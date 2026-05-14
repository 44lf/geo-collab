"""
数据库引擎和 Session 工厂，支持 SQLite（本地）和 MySQL（云端）。

关键约束：
  - 每个请求一个 Session，commit on success / rollback on error
  - SQLite: check_same_thread=False、WAL 模式、busy_timeout=5000、foreign_keys=ON
  - MySQL: 连接池 + pool_pre_ping + UTC 时区

注意：
  - ensure_data_dirs() 在模块导入时执行，确保数据目录存在
  - Alembic 的 sqlalchemy.url 是占位符，运行时由 get_database_url() 覆盖
"""
from urllib.parse import urlparse

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from server.app.core.paths import ensure_data_dirs, get_database_url

ensure_data_dirs()

_db_url = get_database_url()
_parsed = urlparse(_db_url)

if _parsed.scheme.startswith("mysql"):
    engine = create_engine(
        _db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        pool_pre_ping=True,
        connect_args={"init_command": "SET SESSION time_zone='+00:00'"},
    )
else:
    engine = create_engine(_db_url, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# FastAPI 依赖注入：每个请求获取一个新 Session
# 自动 commit（成功）或 rollback（异常）
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


"""
SQLite 数据库引擎和 Session 工厂。

关键约束：
  - 单用户桌面应用，check_same_thread=False 允许跨线程使用
  - 每个请求一个 Session，commit on success / rollback on error
  - WAL 模式 + busy_timeout=5000 避免并发读写冲突
  - foreign_keys=ON 在 connect 时设置（SQLite 默认关闭外键）

注意：
  - ensure_data_dirs() 在模块导入时执行，确保 PyInstaller 打包环境也有数据目录
  - Alembic 的 sqlalchemy.url 是占位符，运行时由 launcher.py 的 get_database_url() 覆盖
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from server.app.core.paths import ensure_data_dirs, get_database_url

# 模块导入时触发，确保数据目录已创建（PyInstaller 环境也正常工作）
ensure_data_dirs()

# SQLite 引擎，单用户桌面应用允许跨线程访问
engine = create_engine(get_database_url(), connect_args={"check_same_thread": False})

# 每个连接建立时设置 WAL 模式和忙等待超时，避免读写互锁
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

# Session 工厂 — 测试时可通过替换 engine 使用内存 SQLite
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


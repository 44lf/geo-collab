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

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# FastAPI 依赖注入：每个请求获取一个新 Session
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


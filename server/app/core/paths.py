from pathlib import Path

from server.app.core.config import Settings, get_settings

# 数据目录下的子目录列表
DATA_SUBDIRS = ("assets", "browser_states", "logs", "exports")


# Resolve the configured data directory.
def get_data_dir() -> Path:
    data_dir = Settings().data_dir
    if data_dir is None:
        raise RuntimeError("GEO_DATA_DIR not set")
    return data_dir


# 获取 SQLite 数据库文件路径
def get_database_path() -> Path:
    return get_data_dir() / "geo.db"


# 获取 SQLAlchemy 连接 URL（POSIX 风格的路径）
def get_database_url() -> str:
    from urllib.parse import quote_plus

    settings = get_settings()
    if settings.database_url:
        return settings.database_url
    if settings.db_host and settings.db_user and settings.db_name:
        password = quote_plus(settings.db_pass or "")
        return f"mysql+pymysql://{settings.db_user}:{password}@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    return f"sqlite:///{get_database_path().as_posix()}"


# 确保数据目录及所有子目录存在
def ensure_data_dirs() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    for subdir in DATA_SUBDIRS:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
    return data_dir

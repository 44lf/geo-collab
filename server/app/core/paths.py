import os
from pathlib import Path

from server.app.core.config import get_settings

# 数据目录下的子目录列表
DATA_SUBDIRS = ("assets", "browser_states", "logs", "exports")


# 获取数据目录：优先 GEO_DATA_DIR 环境变量，其次 %LOCALAPPDATA%/GeoCollab
def get_data_dir() -> Path:
    settings = get_settings()
    if settings.data_dir:
        return settings.data_dir

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "GeoCollab"

    return Path.home() / ".geocollab"


# 获取 SQLite 数据库文件路径
def get_database_path() -> Path:
    return get_data_dir() / "geo.db"


# 获取 SQLAlchemy 连接 URL（POSIX 风格的路径）
def get_database_url() -> str:
    return f"sqlite:///{get_database_path().as_posix()}"


# 确保数据目录及所有子目录存在
def ensure_data_dirs() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    for subdir in DATA_SUBDIRS:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
    return data_dir


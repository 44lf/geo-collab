import os
from pathlib import Path

from server.app.core.config import get_settings

DATA_SUBDIRS = ("assets", "browser_states", "logs", "exports")


def get_data_dir() -> Path:
    settings = get_settings()
    if settings.data_dir:
        return settings.data_dir

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "GeoCollab"

    return Path.home() / ".geocollab"


def get_database_path() -> Path:
    return get_data_dir() / "geo.db"


def get_database_url() -> str:
    return f"sqlite:///{get_database_path().as_posix()}"


def ensure_data_dirs() -> Path:
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    for subdir in DATA_SUBDIRS:
        (data_dir / subdir).mkdir(parents=True, exist_ok=True)
    return data_dir


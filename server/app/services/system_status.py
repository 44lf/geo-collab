from server.app.core.config import get_settings
from server.app.core.paths import DATA_SUBDIRS, ensure_data_dirs, get_data_dir, get_database_path
from server.app.schemas.system import SystemStatus


# 获取系统基础状态（目录、版本等），不依赖数据库
def get_system_status() -> SystemStatus:
    settings = get_settings()
    data_dir = ensure_data_dirs()
    directories_ready = data_dir.exists() and all((data_dir / name).exists() for name in DATA_SUBDIRS)

    return SystemStatus(
        service="ok",
        version=settings.app_version,
        data_dir=str(get_data_dir()),
        database_path=str(get_database_path()),
        directories_ready=directories_ready,
    )


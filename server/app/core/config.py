from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# 应用配置，环境变量前缀 GEO_，支持 .env 文件
class Settings(BaseSettings):
    app_name: str = "Geo Collab"
    app_version: str = "0.1.0"
    data_dir: Path | None = None  # 数据目录，默认走 %LOCALAPPDATA%/GeoCollab

    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env")


# 使用 lru_cache 保证全局单例，测试中需调用 cache_clear()
@lru_cache
def get_settings() -> Settings:
    return Settings()


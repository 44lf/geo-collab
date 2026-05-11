from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# 应用配置，环境变量前缀 GEO_，支持 .env 文件
class Settings(BaseSettings):
    app_name: str = "Geo Collab"
    app_version: str = "0.1.0"
    data_dir: Path | None = None  # 数据目录，默认走 %LOCALAPPDATA%/GeoCollab
    publish_max_concurrent_records: int = 5
    publish_browser_channel: str = "chrome"
    publish_browser_executable_path: str | None = None

    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env")


# 使用 lru_cache 保证全局单例，测试中需调用 cache_clear()
@lru_cache
def get_settings() -> Settings:
    return Settings()


# File upload limits
MAX_ASSET_BYTES: int = 20 * 1024 * 1024  # 20 MB
MAX_ZIP_BYTES: int = 50 * 1024 * 1024  # 50 MB

# Allowed magic bytes for image uploads
# Maps first bytes -> description
# WebP: first 4 bytes "RIFF", bytes 8-12 "WEBP"
ALLOWED_MAGIC: list[bytes] = [
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"\xff\xd8",            # JPEG
    b"RIFF",                # WebP (also check bytes 8:12 == b"WEBP")
    b"GIF87a",              # GIF
    b"GIF89a",              # GIF
]


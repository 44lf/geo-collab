from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Geo Collab"
    app_version: str = "0.1.0"
    data_dir: Path | None = None

    model_config = SettingsConfigDict(env_prefix="GEO_", env_file=".env")


@lru_cache
def get_settings() -> Settings:
    return Settings()


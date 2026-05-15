from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TDGL Data Service"
    database_url: str = "sqlite+pysqlite:///:memory:"
    zarr_root: Path = Field(default=Path("data/zarr"))
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"])

    model_config = SettingsConfigDict(
        env_prefix="TDGL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

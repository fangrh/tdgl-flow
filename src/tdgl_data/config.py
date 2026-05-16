from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TDGL Data Viewer"
    database_url: str = "sqlite+pysqlite:///:memory:"
    cors_allow_origins: list[str] = Field(default_factory=lambda: ["*"])
    zarr_root: str = "/data/zarr"

    model_config = SettingsConfigDict(
        env_prefix="TDGL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

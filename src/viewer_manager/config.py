from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///:memory:"
    viewer_image: str = "ghcr.io/fangrh/tdgl-data-viewer:latest"
    k8s_namespace: str = "tdgl"
    session_idle_ttl_minutes: int = 15
    failed_cleanup_minutes: int = 10
    cleanup_interval_seconds: int = 60
    base_url: str = ""

    model_config = SettingsConfigDict(
        env_prefix="VIEWER_MANAGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
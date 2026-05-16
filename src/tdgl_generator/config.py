from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_service_url: str = "http://localhost:8000"
    generator_port: int = 8001

    model_config = SettingsConfigDict(
        env_prefix="TDGL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
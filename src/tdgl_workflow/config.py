from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "TDGL Workflow"
    data_service_url: str = "http://data-viewer.tdgl.svc.cluster.local"
    argo_server_url: str = "http://argo-workflows-server.argo.svc.cluster.local:2746"
    session_secret: str = "change-me-in-production"
    tdgl_namespace: str = "tdgl"

    model_config = SettingsConfigDict(
        env_prefix="TDGL_WORKFLOW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
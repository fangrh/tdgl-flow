from tdgl_data.config import Settings


def test_settings_defaults(monkeypatch):
    for name in (
        "TDGL_APP_NAME",
        "TDGL_DATABASE_URL",
        "TDGL_CORS_ALLOW_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)

    assert settings.app_name == "TDGL Data Viewer"
    assert settings.database_url == "sqlite+pysqlite:///:memory:"

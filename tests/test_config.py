from tdgl_data.config import Settings


def test_settings_defaults(tmp_path, monkeypatch):
    for name in (
        "TDGL_APP_NAME",
        "TDGL_DATABASE_URL",
        "TDGL_ZARR_ROOT",
        "TDGL_CORS_ALLOW_ORIGINS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(zarr_root=tmp_path / "zarr", _env_file=None)

    assert settings.app_name == "TDGL Data Service"
    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.zarr_root == tmp_path / "zarr"
    assert settings.zarr_root.parent == tmp_path

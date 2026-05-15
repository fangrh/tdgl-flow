from tdgl_data.config import Settings


def test_settings_defaults(tmp_path):
    settings = Settings(zarr_root=tmp_path / "zarr")

    assert settings.app_name == "TDGL Data Service"
    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.zarr_root == tmp_path / "zarr"
    assert settings.zarr_root.parent == tmp_path

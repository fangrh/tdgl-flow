from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tdgl_data.app import create_app
from tdgl_data.db import create_engine_from_url
from tdgl_data.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as db:
        yield db


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=tmp_path / "zarr",
        create_schema=True,
    )
    with TestClient(app) as test_client:
        yield test_client

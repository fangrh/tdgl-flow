from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from tdgl_data.db import create_engine_from_url
from tdgl_data.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as db:
        yield db

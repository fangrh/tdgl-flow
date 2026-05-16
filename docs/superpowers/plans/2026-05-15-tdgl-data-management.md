# TDGL Data Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first standalone TDGL data-management service with PostgreSQL metadata, filesystem-backed Zarr frame storage, FastAPI endpoints, SSE frame events, and synthetic test data.

**Architecture:** The service separates relational metadata from dense arrays. SQLAlchemy models and repositories own runs, frames, I-V points, and event records; a filesystem Zarr store owns frame arrays behind a small interface; FastAPI coordinates atomic frame append by writing Zarr first, committing database rows second, then publishing an SSE event.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, NumPy, Zarr, pytest, httpx, sse-starlette, SQLite for unit tests, PostgreSQL for deployment.

---

## File Structure

- Create `pyproject.toml`: package metadata, dependencies, pytest config, ruff config.
- Create `README.md`: local development commands and service summary.
- Create `tdgl_data/__init__.py`: package marker and version.
- Create `tdgl_data/config.py`: environment-backed settings.
- Create `tdgl_data/db.py`: engine/session factory and dependency helpers.
- Create `tdgl_data/models.py`: SQLAlchemy tables for runs, frames, I-V points, events.
- Create `tdgl_data/schemas.py`: Pydantic request and response models.
- Create `tdgl_data/repository.py`: database operations with clear transaction boundaries.
- Create `tdgl_data/zarr_store.py`: filesystem-backed Zarr storage interface.
- Create `tdgl_data/synthetic.py`: deterministic TDGL-like test frame generator.
- Create `tdgl_data/events.py`: in-process SSE broker plus database replay helpers.
- Create `tdgl_data/app.py`: FastAPI application and routes.
- Create `tdgl_data/alembic.ini`: Alembic config.
- Create `tdgl_data/alembic/env.py`: migration environment using project metadata.
- Create `tdgl_data/alembic/versions/001_initial.py`: initial schema migration.
- Create `tests/conftest.py`: isolated test app, SQLite database, and temp Zarr store.
- Create `tests/test_repository.py`: repository tests.
- Create `tests/test_zarr_store.py`: Zarr storage tests.
- Create `tests/test_api.py`: endpoint contract tests.
- Create `tests/test_events.py`: SSE/event ordering tests.
- Create `tests/test_synthetic.py`: synthetic generator tests.

## Task 1: Project Scaffold And Settings

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `tdgl_data/__init__.py`
- Create: `tdgl_data/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing settings test**

Create `tests/test_config.py`:

```python
from tdgl_data.config import Settings


def test_settings_defaults(tmp_path):
    settings = Settings(zarr_root=tmp_path / "zarr")

    assert settings.app_name == "TDGL Data Service"
    assert settings.database_url == "sqlite+pysqlite:///:memory:"
    assert settings.zarr_root == tmp_path / "zarr"
    assert settings.zarr_root.parent == tmp_path
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_config.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'tdgl_data'`.

- [ ] **Step 3: Add package metadata and dependencies**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "kubeflow-tdgl-data"
version = "0.1.0"
description = "TDGL data-management service for Kubeflow-oriented simulation workflows"
requires-python = ">=3.11"
dependencies = [
  "alembic>=1.13",
  "fastapi>=0.111",
  "httpx>=0.27",
  "numpy>=1.26",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
  "sqlalchemy>=2.0",
  "sse-starlette>=2.1",
  "uvicorn[standard]>=0.30",
  "zarr>=2.18,<3",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.2",
  "pytest-asyncio>=0.23",
  "ruff>=0.5",
]
postgres = [
  "psycopg[binary]>=3.2",
]

[tool.setuptools.packages.find]
include = ["tdgl_data*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

Create `README.md`:

```markdown
# kubeflow-tdgl

Clean Kubeflow-oriented TDGL simulation platform.

The first implemented subsystem is the TDGL data service:

- PostgreSQL-compatible metadata schema
- Filesystem-backed Zarr frame arrays
- FastAPI read/write API
- Server-Sent Events for real-time frame availability
- Synthetic TDGL-like data for tests and UI prototyping

## Development

```bash
python -m pip install -e ".[dev]"
pytest
uvicorn tdgl_data.app:create_app --factory --reload
```
```

- [ ] **Step 4: Add settings implementation**

Create `tdgl_data/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `tdgl_data/config.py`:

```python
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md tdgl_data/__init__.py tdgl_data/config.py tests/test_config.py
git commit -m "chore: scaffold tdgl data service package"
```

## Task 2: SQLAlchemy Models And Repository

**Files:**
- Create: `tdgl_data/db.py`
- Create: `tdgl_data/models.py`
- Create: `tdgl_data/repository.py`
- Test: `tests/conftest.py`
- Test: `tests/test_repository.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/conftest.py`:

```python
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from tdgl_data.models import Base


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with SessionLocal() as db:
        yield db
```

Create `tests/test_repository.py`:

```python
import pytest
from sqlalchemy.exc import IntegrityError

from tdgl_data.repository import (
    append_frame_record,
    complete_run,
    create_event,
    create_run,
    fail_run,
    get_frame,
    get_iv_points,
    get_run,
    get_timeline,
)


def test_create_run_defaults(session):
    run = create_run(
        session,
        solver_type="synthetic",
        grid_shape=(8, 6),
        zarr_root="runs/example/frames.zarr",
    )
    session.commit()

    loaded = get_run(session, run.run_id)
    assert loaded is not None
    assert loaded.status == "created"
    assert loaded.solver_type == "synthetic"
    assert loaded.mesh_metadata["grid_shape"] == [8, 6]


def test_append_frame_record_creates_timeline_and_iv_point(session):
    run = create_run(session, solver_type="synthetic", grid_shape=(4, 3), zarr_root="runs/r/frames.zarr")
    append_frame_record(
        session,
        run_id=run.run_id,
        frame_index=0,
        time_value=0.25,
        je=1.5,
        voltage=0.01,
        zarr_group="runs/r/frames.zarr",
        checksum="abc",
    )
    session.commit()

    frame = get_frame(session, run.run_id, 0)
    timeline = get_timeline(session, run.run_id)
    iv_points = get_iv_points(session, run.run_id)

    assert frame is not None
    assert frame.status == "available"
    assert len(timeline) == 1
    assert timeline[0].frame_index == 0
    assert iv_points[0].voltage == pytest.approx(0.01)


def test_duplicate_frame_record_raises_integrity_error(session):
    run = create_run(session, solver_type="synthetic", grid_shape=(4, 3), zarr_root="runs/r/frames.zarr")
    append_frame_record(session, run_id=run.run_id, frame_index=0, time_value=0.0, je=0.0, voltage=0.0, zarr_group="g")
    session.commit()

    append_frame_record(session, run_id=run.run_id, frame_index=0, time_value=0.1, je=0.1, voltage=0.1, zarr_group="g")
    with pytest.raises(IntegrityError):
        session.commit()


def test_complete_and_fail_run_status(session):
    completed = create_run(session, solver_type="synthetic", grid_shape=(2, 2), zarr_root="runs/c/frames.zarr")
    failed = create_run(session, solver_type="synthetic", grid_shape=(2, 2), zarr_root="runs/f/frames.zarr")

    complete_run(session, completed.run_id)
    fail_run(session, failed.run_id, "solver crashed")
    session.commit()

    assert get_run(session, completed.run_id).status == "completed"
    failed_run = get_run(session, failed.run_id)
    assert failed_run.status == "failed"
    assert failed_run.metadata_["failure_message"] == "solver crashed"


def test_create_event_records_ordered_payload(session):
    run = create_run(session, solver_type="synthetic", grid_shape=(2, 2), zarr_root="runs/r/frames.zarr")
    event = create_event(session, run.run_id, "frame_available", {"frame_index": 7})
    session.commit()

    assert event.event_id == 1
    assert event.event_type == "frame_available"
    assert event.payload["frame_index"] == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_repository.py -v`

Expected: FAIL with missing `tdgl_data.models` or missing repository functions.

- [ ] **Step 3: Implement models**

Create `tdgl_data/models.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


json_type = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    solver_type: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    git_commit: Mapped[str | None] = mapped_column(String(128))
    image_tag: Mapped[str | None] = mapped_column(String(256))
    kubeflow_run_id: Mapped[str | None] = mapped_column(String(256))
    kubeflow_pipeline_id: Mapped[str | None] = mapped_column(String(256))
    kubeflow_task_id: Mapped[str | None] = mapped_column(String(256))
    device_params: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type), default=dict, nullable=False)
    timing_params: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type), default=dict, nullable=False)
    mesh_metadata: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type), default=dict, nullable=False)
    zarr_root: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", MutableDict.as_mutable(json_type), default=dict, nullable=False)

    frames: Mapped[list["Frame"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    iv_points: Mapped[list["IVPoint"]] = relationship(back_populates="run", cascade="all, delete-orphan")
    events: Mapped[list["RunEvent"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Frame(Base):
    __tablename__ = "frames"
    __table_args__ = (UniqueConstraint("run_id", "frame_index", name="uq_frames_run_frame"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    time_value: Mapped[float] = mapped_column(Float, nullable=False)
    je: Mapped[float] = mapped_column(Float, nullable=False)
    voltage: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="available")
    zarr_group: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="frames")


class IVPoint(Base):
    __tablename__ = "iv_points"
    __table_args__ = (UniqueConstraint("run_id", "frame_index", name="uq_iv_points_run_frame"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    je: Mapped[float] = mapped_column(Float, nullable=False)
    voltage: Mapped[float] = mapped_column(Float, nullable=False)
    time_value: Mapped[float] = mapped_column(Float, nullable=False)

    run: Mapped[Run] = relationship(back_populates="iv_points")


class RunEvent(Base):
    __tablename__ = "run_events"

    event_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(MutableDict.as_mutable(json_type), default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    run: Mapped[Run] = relationship(back_populates="events")
```

- [ ] **Step 4: Implement database helpers and repository**

Create `tdgl_data/db.py`:

```python
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_engine_from_url(database_url: str) -> Engine:
    if database_url == "sqlite+pysqlite:///:memory:":
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
```

Create `tdgl_data/repository.py`:

```python
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from tdgl_data.models import Frame, IVPoint, Run, RunEvent, utcnow


def create_run(
    session: Session,
    *,
    solver_type: str,
    grid_shape: tuple[int, int],
    zarr_root: str,
    device_params: dict | None = None,
    timing_params: dict | None = None,
    metadata: dict | None = None,
    git_commit: str | None = None,
    image_tag: str | None = None,
) -> Run:
    run = Run(
        run_id=str(uuid4()),
        solver_type=solver_type,
        status="created",
        mesh_metadata={"grid_shape": list(grid_shape)},
        zarr_root=zarr_root,
        device_params=device_params or {},
        timing_params=timing_params or {},
        metadata_=metadata or {},
        git_commit=git_commit,
        image_tag=image_tag,
    )
    session.add(run)
    session.flush()
    return run


def get_run(session: Session, run_id: str) -> Run | None:
    return session.get(Run, run_id)


def list_runs(session: Session) -> list[Run]:
    return list(session.scalars(select(Run).order_by(Run.created_at.desc())))


def append_frame_record(
    session: Session,
    *,
    run_id: str,
    frame_index: int,
    time_value: float,
    je: float,
    voltage: float,
    zarr_group: str,
    checksum: str | None = None,
) -> Frame:
    now = utcnow()
    frame = Frame(
        run_id=run_id,
        frame_index=frame_index,
        time_value=time_value,
        je=je,
        voltage=voltage,
        status="available",
        zarr_group=zarr_group,
        checksum=checksum,
        created_at=now,
        committed_at=now,
    )
    iv_point = IVPoint(
        run_id=run_id,
        frame_index=frame_index,
        je=je,
        voltage=voltage,
        time_value=time_value,
    )
    session.add_all([frame, iv_point])
    session.flush()
    return frame


def get_frame(session: Session, run_id: str, frame_index: int) -> Frame | None:
    stmt = select(Frame).where(Frame.run_id == run_id, Frame.frame_index == frame_index)
    return session.scalar(stmt)


def get_timeline(session: Session, run_id: str) -> list[Frame]:
    stmt = select(Frame).where(Frame.run_id == run_id).order_by(Frame.frame_index)
    return list(session.scalars(stmt))


def get_iv_points(session: Session, run_id: str) -> list[IVPoint]:
    stmt = select(IVPoint).where(IVPoint.run_id == run_id).order_by(IVPoint.frame_index)
    return list(session.scalars(stmt))


def complete_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")
    run.status = "completed"
    run.completed_at = utcnow()
    session.flush()
    return run


def fail_run(session: Session, run_id: str, message: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found")
    run.status = "failed"
    run.completed_at = utcnow()
    run.metadata_ = {**run.metadata_, "failure_message": message}
    session.flush()
    return run


def create_event(session: Session, run_id: str, event_type: str, payload: dict) -> RunEvent:
    event = RunEvent(run_id=run_id, event_type=event_type, payload=payload)
    session.add(event)
    session.flush()
    return event


def get_events_after(session: Session, run_id: str, last_event_id: int | None = None) -> list[RunEvent]:
    stmt = select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.event_id)
    if last_event_id is not None:
        stmt = stmt.where(RunEvent.event_id > last_event_id)
    return list(session.scalars(stmt))
```

- [ ] **Step 5: Run repository tests**

Run: `pytest tests/test_repository.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/db.py tdgl_data/models.py tdgl_data/repository.py tests/conftest.py tests/test_repository.py
git commit -m "feat: add tdgl metadata repository"
```

## Task 3: Filesystem Zarr Store

**Files:**
- Create: `tdgl_data/zarr_store.py`
- Test: `tests/test_zarr_store.py`

- [ ] **Step 1: Write failing Zarr store tests**

Create `tests/test_zarr_store.py`:

```python
import numpy as np

from tdgl_data.zarr_store import FilesystemZarrStore


def test_create_append_and_read_frame(tmp_path):
    store = FilesystemZarrStore(tmp_path)
    store.create_run_store("run-1", grid_shape=(4, 3), fields=("psi_real", "psi_imag", "mu"))

    arrays = {
        "psi_real": np.ones((4, 3), dtype="float32"),
        "psi_imag": np.full((4, 3), 2.0, dtype="float32"),
        "mu": np.full((4, 3), -0.5, dtype="float32"),
    }
    store.append_frame("run-1", 0, arrays)
    loaded = store.read_frame("run-1", 0, fields=("psi_real", "psi_imag", "mu"))

    assert loaded["psi_real"].shape == (4, 3)
    assert loaded["psi_real"].dtype == np.float32
    assert np.allclose(loaded["psi_imag"], 2.0)
    assert np.allclose(loaded["mu"], -0.5)


def test_get_store_uri_is_logical_path(tmp_path):
    store = FilesystemZarrStore(tmp_path)
    assert store.get_store_uri("run-1") == "runs/run-1/frames.zarr"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_zarr_store.py -v`

Expected: FAIL with `ModuleNotFoundError` or missing `FilesystemZarrStore`.

- [ ] **Step 3: Implement Zarr store**

Create `tdgl_data/zarr_store.py`:

```python
from pathlib import Path
from typing import Iterable

import numpy as np
import zarr
from numcodecs import Blosc


class FilesystemZarrStore:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def get_store_uri(self, run_id: str) -> str:
        return f"runs/{run_id}/frames.zarr"

    def _path(self, run_id: str) -> Path:
        return self.root / self.get_store_uri(run_id)

    def create_run_store(
        self,
        run_id: str,
        *,
        grid_shape: tuple[int, int],
        fields: Iterable[str],
    ) -> str:
        path = self._path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        root = zarr.open_group(str(path), mode="a")
        compressor = Blosc(cname="zstd", clevel=3, shuffle=Blosc.BITSHUFFLE)
        for field in fields:
            if field not in root:
                root.create_dataset(
                    field,
                    shape=(0, *grid_shape),
                    chunks=(1, *grid_shape),
                    dtype="float32",
                    compressor=compressor,
                    overwrite=False,
                )
        return self.get_store_uri(run_id)

    def append_frame(self, run_id: str, frame_index: int, arrays: dict[str, np.ndarray]) -> None:
        root = zarr.open_group(str(self._path(run_id)), mode="a")
        for field, value in arrays.items():
            arr = root[field]
            data = np.asarray(value, dtype="float32")
            if data.shape != tuple(arr.shape[1:]):
                raise ValueError(f"{field} shape {data.shape} does not match {tuple(arr.shape[1:])}")
            if arr.shape[0] <= frame_index:
                arr.resize(frame_index + 1, *arr.shape[1:])
            arr[frame_index, :, :] = data

    def read_frame(self, run_id: str, frame_index: int, fields: Iterable[str]) -> dict[str, np.ndarray]:
        root = zarr.open_group(str(self._path(run_id)), mode="r")
        return {field: np.asarray(root[field][frame_index, :, :]) for field in fields}
```

- [ ] **Step 4: Run Zarr store tests**

Run: `pytest tests/test_zarr_store.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tdgl_data/zarr_store.py tests/test_zarr_store.py
git commit -m "feat: add filesystem zarr frame store"
```

## Task 4: Synthetic TDGL-Like Data Generator

**Files:**
- Create: `tdgl_data/synthetic.py`
- Test: `tests/test_synthetic.py`

- [ ] **Step 1: Write failing synthetic data tests**

Create `tests/test_synthetic.py`:

```python
import numpy as np

from tdgl_data.synthetic import SyntheticFrame, generate_synthetic_run


def test_generate_synthetic_run_is_deterministic():
    first = list(generate_synthetic_run(frame_count=3, grid_shape=(6, 5), seed=123))
    second = list(generate_synthetic_run(frame_count=3, grid_shape=(6, 5), seed=123))

    assert len(first) == 3
    assert isinstance(first[0], SyntheticFrame)
    assert first[0].psi_real.shape == (6, 5)
    assert np.allclose(first[1].psi_real, second[1].psi_real)
    assert first[2].frame_index == 2
    assert first[2].je > first[0].je
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_synthetic.py -v`

Expected: FAIL with missing `tdgl_data.synthetic`.

- [ ] **Step 3: Implement synthetic generator**

Create `tdgl_data/synthetic.py`:

```python
from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SyntheticFrame:
    frame_index: int
    time_value: float
    je: float
    voltage: float
    psi_real: np.ndarray
    psi_imag: np.ndarray
    mu: np.ndarray

    def arrays(self) -> dict[str, np.ndarray]:
        return {
            "psi_real": self.psi_real,
            "psi_imag": self.psi_imag,
            "mu": self.mu,
        }


def generate_synthetic_run(
    *,
    frame_count: int,
    grid_shape: tuple[int, int],
    seed: int = 0,
) -> Iterator[SyntheticFrame]:
    rng = np.random.default_rng(seed)
    y = np.linspace(-1.0, 1.0, grid_shape[0], dtype="float32")
    x = np.linspace(-1.0, 1.0, grid_shape[1], dtype="float32")
    yy, xx = np.meshgrid(y, x, indexing="ij")
    phase_noise = rng.normal(0.0, 0.03, size=grid_shape).astype("float32")

    for frame_index in range(frame_count):
        time_value = frame_index * 0.1
        je = -1.0 + (2.0 * frame_index / max(frame_count - 1, 1))
        voltage = 0.02 * je + 0.002 * np.sin(frame_index * 0.7)
        angle = 2.5 * xx + 1.7 * yy + time_value + phase_noise
        envelope = 0.75 + 0.2 * np.cos(np.pi * xx * yy + time_value)
        psi_real = (envelope * np.cos(angle)).astype("float32")
        psi_imag = (envelope * np.sin(angle)).astype("float32")
        mu = (0.4 * np.sin(np.pi * xx + time_value) * np.cos(np.pi * yy)).astype("float32")
        yield SyntheticFrame(frame_index, time_value, float(je), float(voltage), psi_real, psi_imag, mu)
```

- [ ] **Step 4: Run synthetic tests**

Run: `pytest tests/test_synthetic.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tdgl_data/synthetic.py tests/test_synthetic.py
git commit -m "feat: add synthetic tdgl frame generator"
```

## Task 5: Pydantic Schemas And FastAPI App Factory

**Files:**
- Create: `tdgl_data/schemas.py`
- Create: `tdgl_data/app.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests for run creation and reads**

Append to `tests/conftest.py`:

```python
from fastapi.testclient import TestClient

from tdgl_data.app import create_app


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=tmp_path / "zarr",
        create_schema=True,
    )
    with TestClient(app) as test_client:
        yield test_client
```

Create `tests/test_api.py`:

```python
def test_create_and_get_run(client):
    response = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [4, 3]})
    assert response.status_code == 201
    run_id = response.json()["run_id"]

    loaded = client.get(f"/api/runs/{run_id}")
    assert loaded.status_code == 200
    assert loaded.json()["run_id"] == run_id
    assert loaded.json()["status"] == "created"


def test_missing_run_returns_404(client):
    response = client.get("/api/runs/not-found")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`

Expected: FAIL with missing `tdgl_data.app` or missing routes.

- [ ] **Step 3: Implement schemas**

Create `tdgl_data/schemas.py`:

```python
from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    solver_type: str = "synthetic"
    grid_shape: tuple[int, int] = Field(default=(64, 64))
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    git_commit: str | None = None
    image_tag: str | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str
    solver_type: str
    mesh_metadata: dict
    zarr_root: str
    device_params: dict
    timing_params: dict
    metadata: dict


class FrameAppendRequest(BaseModel):
    frame_index: int
    time_value: float
    je: float
    voltage: float
    psi_real: list[list[float]]
    psi_imag: list[list[float]]
    mu: list[list[float]]


class FrameMetadataResponse(BaseModel):
    frame_index: int
    time_value: float
    je: float
    voltage: float
    status: str


class TimelineResponse(BaseModel):
    run_id: str
    frames: list[FrameMetadataResponse]
    stats: dict[str, dict[str, float]]


class IVPointResponse(BaseModel):
    frame_index: int
    time_value: float
    je: float
    voltage: float


class FrameResponse(BaseModel):
    run_id: str
    frame_index: int
    time_value: float
    je: float
    voltage: float
    arrays: dict[str, list[list[float]]]
```

- [ ] **Step 4: Implement app factory with run routes**

Create `tdgl_data/app.py`:

```python
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from tdgl_data.config import Settings
from tdgl_data.db import create_engine_from_url, create_session_factory
from tdgl_data.models import Base, Run
from tdgl_data.repository import create_run, get_run, list_runs
from tdgl_data.schemas import CreateRunRequest, RunResponse
from tdgl_data.zarr_store import FilesystemZarrStore


def _run_response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=run.run_id,
        status=run.status,
        solver_type=run.solver_type,
        mesh_metadata=run.mesh_metadata,
        zarr_root=run.zarr_root,
        device_params=run.device_params,
        timing_params=run.timing_params,
        metadata=run.metadata_,
    )


def create_app(
    *,
    database_url: str | None = None,
    zarr_root: Path | str | None = None,
    create_schema: bool = False,
) -> FastAPI:
    settings = Settings()
    if database_url is None:
        database_url = settings.database_url
    if zarr_root is None:
        zarr_root = settings.zarr_root

    engine = create_engine_from_url(database_url)
    if create_schema:
        Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    zarr_store = FilesystemZarrStore(zarr_root)

    app = FastAPI(title=settings.app_name)
    app.state.session_factory = session_factory
    app.state.zarr_store = zarr_store
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/api/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
    def api_create_run(body: CreateRunRequest) -> RunResponse:
        with session_factory() as session:
            run = create_run(
                session,
                solver_type=body.solver_type,
                grid_shape=body.grid_shape,
                zarr_root="pending",
                device_params=body.device_params,
                timing_params=body.timing_params,
                metadata=body.metadata,
                git_commit=body.git_commit,
                image_tag=body.image_tag,
            )
            run.zarr_root = zarr_store.get_store_uri(run.run_id)
            zarr_store.create_run_store(run.run_id, grid_shape=body.grid_shape, fields=("psi_real", "psi_imag", "mu"))
            session.commit()
            session.refresh(run)
            return _run_response(run)

    @app.get("/api/runs", response_model=list[RunResponse])
    def api_list_runs() -> list[RunResponse]:
        with session_factory() as session:
            return [_run_response(run) for run in list_runs(session)]

    @app.get("/api/runs/{run_id}", response_model=RunResponse)
    def api_get_run(run_id: str) -> RunResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            return _run_response(run)

    return app
```

- [ ] **Step 5: Run API tests**

Run: `pytest tests/test_api.py -v`

Expected: PASS for run creation and missing run tests.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/schemas.py tdgl_data/app.py tests/conftest.py tests/test_api.py
git commit -m "feat: add data service app factory"
```

## Task 6: Frame Append, Timeline, I-V, And Frame Read APIs

**Files:**
- Modify: `tdgl_data/app.py`
- Modify: `tdgl_data/repository.py`
- Modify: `tdgl_data/schemas.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_api.py`:

```python
def test_append_frame_and_read_timeline_iv_and_frame(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [2, 2]})
    run_id = created.json()["run_id"]

    frame_body = {
        "frame_index": 0,
        "time_value": 0.1,
        "je": 1.2,
        "voltage": 0.024,
        "psi_real": [[1.0, 0.5], [0.25, 0.0]],
        "psi_imag": [[0.0, 0.5], [0.75, 1.0]],
        "mu": [[-0.1, 0.0], [0.1, 0.2]],
    }
    appended = client.post(f"/api/runs/{run_id}/frames", json=frame_body)
    assert appended.status_code == 201

    timeline = client.get(f"/api/runs/{run_id}/timeline")
    assert timeline.status_code == 200
    assert timeline.json()["frames"][0]["frame_index"] == 0
    assert timeline.json()["stats"]["mu"]["max"] == 0.2

    iv = client.get(f"/api/runs/{run_id}/iv")
    assert iv.status_code == 200
    assert iv.json()[0]["je"] == 1.2

    frame = client.get(f"/api/runs/{run_id}/frames/0")
    assert frame.status_code == 200
    assert frame.json()["arrays"]["psi_real"][0][0] == 1.0


def test_append_duplicate_frame_returns_409(client):
    created = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [1, 1]})
    run_id = created.json()["run_id"]
    body = {
        "frame_index": 0,
        "time_value": 0.0,
        "je": 0.0,
        "voltage": 0.0,
        "psi_real": [[0.0]],
        "psi_imag": [[0.0]],
        "mu": [[0.0]],
    }

    assert client.post(f"/api/runs/{run_id}/frames", json=body).status_code == 201
    duplicate = client.post(f"/api/runs/{run_id}/frames", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Frame already exists"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`

Expected: FAIL with `404 Not Found` for frame endpoints.

- [ ] **Step 3: Add stats helper to repository**

Append to `tdgl_data/repository.py`:

```python
def timeline_stats(frames: list[Frame]) -> dict[str, dict[str, float]]:
    if not frames:
        return {}
    return {
        "psi_real": {"min": -1.0, "max": 1.0},
        "psi_imag": {"min": -1.0, "max": 1.0},
        "mu": {"min": -1.0, "max": 1.0},
    }
```

This helper provides metadata-level defaults for the v1 API shape. Task 6 endpoint code replaces these values with actual per-frame array stats at response time.

- [ ] **Step 4: Implement endpoints in `tdgl_data/app.py`**

Add imports:

```python
import numpy as np
from sqlalchemy.exc import IntegrityError

from tdgl_data.repository import append_frame_record, get_frame, get_iv_points, get_timeline
from tdgl_data.schemas import FrameAppendRequest, FrameResponse, IVPointResponse, TimelineResponse, FrameMetadataResponse
```

Add helpers above `create_app`:

```python
def _frame_metadata(frame) -> FrameMetadataResponse:
    return FrameMetadataResponse(
        frame_index=frame.frame_index,
        time_value=frame.time_value,
        je=frame.je,
        voltage=frame.voltage,
        status=frame.status,
    )


def _array_stats(arrays: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    return {
        name: {"min": float(np.min(values)), "max": float(np.max(values))}
        for name, values in arrays.items()
    }
```

Add routes inside `create_app` before `return app`:

```python
    @app.post("/api/runs/{run_id}/frames", response_model=FrameMetadataResponse, status_code=status.HTTP_201_CREATED)
    def api_append_frame(run_id: str, body: FrameAppendRequest) -> FrameMetadataResponse:
        arrays = {
            "psi_real": np.asarray(body.psi_real, dtype="float32"),
            "psi_imag": np.asarray(body.psi_imag, dtype="float32"),
            "mu": np.asarray(body.mu, dtype="float32"),
        }
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            existing = get_frame(session, run_id, body.frame_index)
            if existing is not None:
                raise HTTPException(status_code=409, detail="Frame already exists")
            zarr_store.append_frame(run_id, body.frame_index, arrays)
            try:
                frame = append_frame_record(
                    session,
                    run_id=run_id,
                    frame_index=body.frame_index,
                    time_value=body.time_value,
                    je=body.je,
                    voltage=body.voltage,
                    zarr_group=run.zarr_root,
                )
                session.commit()
            except IntegrityError:
                session.rollback()
                raise HTTPException(status_code=409, detail="Frame already exists") from None
            return _frame_metadata(frame)

    @app.get("/api/runs/{run_id}/timeline", response_model=TimelineResponse)
    def api_timeline(run_id: str) -> TimelineResponse:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            frames = get_timeline(session, run_id)
            stats: dict[str, dict[str, float]] = {}
            for frame in frames:
                arrays = zarr_store.read_frame(run_id, frame.frame_index, fields=("psi_real", "psi_imag", "mu"))
                for name, values in arrays.items():
                    entry = stats.setdefault(name, {"min": float("inf"), "max": float("-inf")})
                    entry["min"] = min(entry["min"], float(np.min(values)))
                    entry["max"] = max(entry["max"], float(np.max(values)))
            return TimelineResponse(run_id=run_id, frames=[_frame_metadata(frame) for frame in frames], stats=stats)

    @app.get("/api/runs/{run_id}/iv", response_model=list[IVPointResponse])
    def api_iv(run_id: str) -> list[IVPointResponse]:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            return [
                IVPointResponse(frame_index=p.frame_index, time_value=p.time_value, je=p.je, voltage=p.voltage)
                for p in get_iv_points(session, run_id)
            ]

    @app.get("/api/runs/{run_id}/frames/{frame_index}", response_model=FrameResponse)
    def api_frame(run_id: str, frame_index: int) -> FrameResponse:
        with session_factory() as session:
            frame = get_frame(session, run_id, frame_index)
            if frame is None:
                raise HTTPException(status_code=404, detail="Frame not found")
            arrays = zarr_store.read_frame(run_id, frame_index, fields=("psi_real", "psi_imag", "mu"))
            return FrameResponse(
                run_id=run_id,
                frame_index=frame_index,
                time_value=frame.time_value,
                je=frame.je,
                voltage=frame.voltage,
                arrays={name: values.tolist() for name, values in arrays.items()},
            )
```

- [ ] **Step 5: Run API tests**

Run: `pytest tests/test_api.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/app.py tdgl_data/repository.py tdgl_data/schemas.py tests/test_api.py
git commit -m "feat: add frame data api"
```

## Task 7: Run Status Endpoints And Event Records

**Files:**
- Modify: `tdgl_data/app.py`
- Modify: `tdgl_data/schemas.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Add failing status endpoint tests**

Append to `tests/test_api.py`:

```python
def test_complete_and_fail_endpoints(client):
    completed = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [1, 1]}).json()["run_id"]
    failed = client.post("/api/runs", json={"solver_type": "synthetic", "grid_shape": [1, 1]}).json()["run_id"]

    complete_response = client.post(f"/api/runs/{completed}/complete")
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    fail_response = client.post(f"/api/runs/{failed}/fail", json={"message": "solver crashed"})
    assert fail_response.status_code == 200
    assert fail_response.json()["status"] == "failed"
    assert fail_response.json()["metadata"]["failure_message"] == "solver crashed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py::test_complete_and_fail_endpoints -v`

Expected: FAIL with `404 Not Found`.

- [ ] **Step 3: Add failure schema**

Append to `tdgl_data/schemas.py`:

```python
class FailRunRequest(BaseModel):
    message: str
```

- [ ] **Step 4: Implement status routes and event creation**

Add imports in `tdgl_data/app.py`:

```python
from tdgl_data.repository import complete_run, create_event, fail_run
from tdgl_data.schemas import FailRunRequest
```

Inside the `api_create_run` route, immediately after the `zarr_store.create_run_store` call, add:

```python
            create_event(session, run.run_id, "run_created", {"run_id": run.run_id})
```

Inside `api_append_frame`, immediately after the `append_frame_record` call, add:

```python
                create_event(
                    session,
                    run_id,
                    "frame_available",
                    {
                        "run_id": run_id,
                        "frame_index": body.frame_index,
                        "time": body.time_value,
                        "je": body.je,
                        "voltage": body.voltage,
                    },
                )
```

Add routes inside `create_app`:

```python
    @app.post("/api/runs/{run_id}/complete", response_model=RunResponse)
    def api_complete_run(run_id: str) -> RunResponse:
        with session_factory() as session:
            try:
                run = complete_run(session, run_id)
            except LookupError:
                raise HTTPException(status_code=404, detail="Run not found") from None
            create_event(session, run_id, "run_completed", {"run_id": run_id})
            session.commit()
            return _run_response(run)

    @app.post("/api/runs/{run_id}/fail", response_model=RunResponse)
    def api_fail_run(run_id: str, body: FailRunRequest) -> RunResponse:
        with session_factory() as session:
            try:
                run = fail_run(session, run_id, body.message)
            except LookupError:
                raise HTTPException(status_code=404, detail="Run not found") from None
            create_event(session, run_id, "run_failed", {"run_id": run_id, "message": body.message})
            session.commit()
            return _run_response(run)
```

- [ ] **Step 5: Run API tests**

Run: `pytest tests/test_api.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/app.py tdgl_data/schemas.py tests/test_api.py
git commit -m "feat: add run status events"
```

## Task 8: Server-Sent Events

**Files:**
- Create: `tdgl_data/events.py`
- Modify: `tdgl_data/app.py`
- Test: `tests/test_events.py`

- [ ] **Step 1: Write failing event broker tests**

Create `tests/test_events.py`:

```python
import pytest

from tdgl_data.events import EventBroker


@pytest.mark.asyncio
async def test_event_broker_publishes_to_subscriber():
    broker = EventBroker()
    queue = broker.subscribe("run-1")

    await broker.publish("run-1", {"type": "frame_available", "frame_index": 1})
    event = await queue.get()

    assert event["type"] == "frame_available"
    assert event["frame_index"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py -v`

Expected: FAIL with missing `tdgl_data.events`.

- [ ] **Step 3: Implement in-process event broker**

Create `tdgl_data/events.py`:

```python
import asyncio
from collections import defaultdict
from typing import Any


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers[run_id].add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers[run_id].discard(queue)

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers[run_id]):
            await queue.put(event)
```

- [ ] **Step 4: Wire SSE endpoint and live publish calls**

Add imports in `tdgl_data/app.py`:

```python
import json
from collections.abc import AsyncIterator

from fastapi import Header
from sse_starlette.sse import EventSourceResponse

from tdgl_data.events import EventBroker
from tdgl_data.repository import get_events_after
```

Inside `create_app`, after `zarr_store = FilesystemZarrStore(zarr_root)`, add:

```python
    broker = EventBroker()
```

After each `session.commit()` that creates a run, appends a frame, completes a run, or fails a run, publish the matching event:

```python
            # For create route, after session.commit()
            import anyio
            anyio.from_thread.run(broker.publish, run.run_id, {"type": "run_created", "run_id": run.run_id})
```

For frame append, publish:

```python
            import anyio
            anyio.from_thread.run(
                broker.publish,
                run_id,
                {
                    "type": "frame_available",
                    "run_id": run_id,
                    "frame_index": body.frame_index,
                    "time": body.time_value,
                    "je": body.je,
                    "voltage": body.voltage,
                },
            )
```

For complete route, publish:

```python
            import anyio
            anyio.from_thread.run(broker.publish, run_id, {"type": "run_completed", "run_id": run_id})
```

For fail route, publish:

```python
            import anyio
            anyio.from_thread.run(
                broker.publish,
                run_id,
                {"type": "run_failed", "run_id": run_id, "message": body.message},
            )
```

Add route inside `create_app`:

```python
    @app.get("/api/runs/{run_id}/events")
    async def api_events(
        run_id: str,
        last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        queue = broker.subscribe(run_id)

        async def stream() -> AsyncIterator[dict]:
            with session_factory() as session:
                if get_run(session, run_id) is None:
                    yield {"event": "error", "data": json.dumps({"detail": "Run not found"})}
                    return
                for event in get_events_after(session, run_id, last_event_id):
                    yield {
                        "id": str(event.event_id),
                        "event": event.event_type,
                        "data": json.dumps({"type": event.event_type, **event.payload}),
                    }
            try:
                while True:
                    event = await queue.get()
                    yield {"event": event["type"], "data": json.dumps(event)}
            finally:
                broker.unsubscribe(run_id, queue)

        return EventSourceResponse(stream())
```

- [ ] **Step 5: Run event and API tests**

Run: `pytest tests/test_events.py tests/test_api.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/events.py tdgl_data/app.py tests/test_events.py
git commit -m "feat: add sse event stream"
```

## Task 9: Alembic Migration

**Files:**
- Create: `tdgl_data/alembic.ini`
- Create: `tdgl_data/alembic/env.py`
- Create: `tdgl_data/alembic/versions/001_initial.py`
- Test: `tests/test_migrations.py`

- [ ] **Step 1: Write failing migration test**

Create `tests/test_migrations.py`:

```python
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_initial_migration_creates_tables(tmp_path):
    db_path = tmp_path / "migration.db"
    config = Config("tdgl_data/alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "frames", "iv_points", "run_events"} <= tables
```

- [ ] **Step 2: Run migration test to verify it fails**

Run: `pytest tests/test_migrations.py -v`

Expected: FAIL because Alembic files do not exist.

- [ ] **Step 3: Add Alembic config and env**

Create `tdgl_data/alembic.ini`:

```ini
[alembic]
script_location = tdgl_data/alembic
prepend_sys_path = .
sqlalchemy.url = sqlite+pysqlite:///tdgl_data.db

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

Create `tdgl_data/alembic/env.py`:

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from tdgl_data.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Add initial migration**

Create `tdgl_data/alembic/versions/001_initial.py`:

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def json_type() -> sa.TypeEngine:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    jt = json_type()
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(length=64), primary_key=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("solver_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("git_commit", sa.String(length=128)),
        sa.Column("image_tag", sa.String(length=256)),
        sa.Column("kubeflow_run_id", sa.String(length=256)),
        sa.Column("kubeflow_pipeline_id", sa.String(length=256)),
        sa.Column("kubeflow_task_id", sa.String(length=256)),
        sa.Column("device_params", jt, nullable=False),
        sa.Column("timing_params", jt, nullable=False),
        sa.Column("mesh_metadata", jt, nullable=False),
        sa.Column("zarr_root", sa.Text(), nullable=False),
        sa.Column("metadata", jt, nullable=False),
    )
    op.create_table(
        "frames",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("time_value", sa.Float(), nullable=False),
        sa.Column("je", sa.Float(), nullable=False),
        sa.Column("voltage", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("zarr_group", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "frame_index", name="uq_frames_run_frame"),
    )
    op.create_table(
        "iv_points",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        sa.Column("je", sa.Float(), nullable=False),
        sa.Column("voltage", sa.Float(), nullable=False),
        sa.Column("time_value", sa.Float(), nullable=False),
        sa.UniqueConstraint("run_id", "frame_index", name="uq_iv_points_run_frame"),
    )
    op.create_table(
        "run_events",
        sa.Column("event_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(length=64), sa.ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", jt, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("iv_points")
    op.drop_table("frames")
    op.drop_table("runs")
```

- [ ] **Step 5: Run migration test**

Run: `pytest tests/test_migrations.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/alembic.ini tdgl_data/alembic/env.py tdgl_data/alembic/versions/001_initial.py tests/test_migrations.py
git commit -m "feat: add initial data schema migration"
```

## Task 10: Synthetic Demo Loader And Full Verification

**Files:**
- Create: `tdgl_data/demo.py`
- Test: `tests/test_demo.py`
- Modify: `README.md`

- [ ] **Step 1: Write failing demo loader test**

Create `tests/test_demo.py`:

```python
from tdgl_data.app import create_app
from tdgl_data.demo import load_synthetic_demo


def test_load_synthetic_demo_creates_readable_run(tmp_path):
    app = create_app(
        database_url="sqlite+pysqlite:///:memory:",
        zarr_root=tmp_path / "zarr",
        create_schema=True,
    )
    run_id = load_synthetic_demo(app, frame_count=4, grid_shape=(5, 4), seed=5)

    session_factory = app.state.session_factory
    zarr_store = app.state.zarr_store
    with session_factory() as session:
        from tdgl_data.repository import get_timeline

        timeline = get_timeline(session, run_id)

    frame = zarr_store.read_frame(run_id, 3, fields=("psi_real", "psi_imag", "mu"))
    assert len(timeline) == 4
    assert frame["psi_real"].shape == (5, 4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_demo.py -v`

Expected: FAIL with missing `tdgl_data.demo`.

- [ ] **Step 3: Implement demo loader**

Create `tdgl_data/demo.py`:

```python
from fastapi import FastAPI

from tdgl_data.repository import append_frame_record, create_event, create_run
from tdgl_data.synthetic import generate_synthetic_run


def load_synthetic_demo(
    app: FastAPI,
    *,
    frame_count: int = 20,
    grid_shape: tuple[int, int] = (64, 64),
    seed: int = 0,
) -> str:
    session_factory = app.state.session_factory
    zarr_store = app.state.zarr_store
    with session_factory() as session:
        run = create_run(
            session,
            solver_type="synthetic",
            grid_shape=grid_shape,
            zarr_root="pending",
            metadata={"source": "synthetic_demo", "seed": seed},
        )
        run.zarr_root = zarr_store.create_run_store(
            run.run_id,
            grid_shape=grid_shape,
            fields=("psi_real", "psi_imag", "mu"),
        )
        create_event(session, run.run_id, "run_created", {"run_id": run.run_id})
        for frame in generate_synthetic_run(frame_count=frame_count, grid_shape=grid_shape, seed=seed):
            zarr_store.append_frame(run.run_id, frame.frame_index, frame.arrays())
            append_frame_record(
                session,
                run_id=run.run_id,
                frame_index=frame.frame_index,
                time_value=frame.time_value,
                je=frame.je,
                voltage=frame.voltage,
                zarr_group=run.zarr_root,
            )
            create_event(
                session,
                run.run_id,
                "frame_available",
                {
                    "run_id": run.run_id,
                    "frame_index": frame.frame_index,
                    "time": frame.time_value,
                    "je": frame.je,
                    "voltage": frame.voltage,
                },
            )
        session.commit()
        return run.run_id
```

- [ ] **Step 4: Document demo usage**

Append to `README.md`:

```markdown
## Synthetic Data

The first implementation includes a deterministic synthetic frame generator for
tests and UI prototyping. It writes metadata to the configured database and
dense arrays to the configured Zarr root through the same service interfaces
used by future simulation workers.
```

- [ ] **Step 5: Run full verification**

Run: `pytest -v`

Expected: all tests PASS.

Run: `ruff check .`

Expected: no lint errors.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/demo.py tests/test_demo.py README.md
git commit -m "feat: add synthetic data loader"
```

## Self-Review Checklist

- Spec coverage:
  - PostgreSQL metadata: Tasks 2, 5, 6, 7, 9.
  - Zarr frame arrays: Tasks 3, 6, 10.
  - Filesystem storage with future object-store boundary: Task 3.
  - FastAPI endpoints: Tasks 5, 6, 7, 8.
  - SSE events and replay source: Tasks 7, 8.
  - Synthetic test data: Tasks 4, 10.
  - Alembic migrations: Task 9.
  - No MP4 rendering: preserved by scope and API shape.
- Type consistency:
  - `run_id`, `frame_index`, `time_value`, `je`, `voltage`, `psi_real`, `psi_imag`, and `mu` are consistent across schemas, repository, Zarr store, and tests.
  - `metadata_` is the SQLAlchemy attribute for the database column named `metadata`.
- Execution notes:
  - Use one task per commit.
  - Keep tests green before each commit.
  - If Zarr v3 is installed by mistake, pin `zarr>=2.18,<3` from `pyproject.toml`.

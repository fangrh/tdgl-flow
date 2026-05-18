# cpp-tdgl Zarr + 3-Step Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor cpp-tdgl simulation pipeline to use per-site Zarr storage, a 3-step Argo workflow (device/timing/simulate), microservice APIs, and an enhanced viewer with 4-panel layout.

**Architecture:** Three microservice APIs (device build, timing build, simulation submit) consumed by both the web UI and a Python SDK. Per-site simulation data stored directly in Zarr without interpolation. Argo workflow split into build-device, build-timing, and simulate steps sharing a PVC.

**Tech Stack:** FastAPI, SQLAlchemy, Zarr, Plotly.js, Argo Workflows, HDF5 (C++ solver input), httpx

---

## File Structure

### Modified files
- `src/tdgl_data/models.py` — add mesh_sites, mesh_elements, n_sites, solver_options columns to Run; remove psi_real/psi_imag/mu/zarr_exists from Frame
- `src/tdgl_data/zarr_store.py` — change from 2D grid to per-site 1D arrays
- `src/tdgl_data/schemas.py` — update CreateRunRequest (n_sites instead of grid_shape), FrameAppendRequest (1D arrays), FrameResponse (1D arrays), add new schemas
- `src/tdgl_data/repository.py` — update create_run (n_sites), append_frame_record (no arrays), add mesh helpers
- `src/tdgl_data/app.py` — add mesh/frame endpoints, update frame append/read for per-site, add device/timing build endpoints
- `src/tdgl_workflow/routes/api.py` — add POST /api/device/build, POST /api/timing/build, POST /api/workflows/submit
- `src/tdgl_workflow/routes/simulate.py` — use new submit workflow API
- `services/cpp-tdgl-runner/runner.py` — read from shared volume, write per-site to Zarr API
- `services/cpp-tdgl-runner/Dockerfile` — add zarr dependency
- `workflows/cpp-tdgl-sim.yaml` — 3-step pipeline with PVC
- `src/tdgl_data/static/viewer.html` — 4-panel layout with per-site rendering
- `pyproject.toml` — add tdgl_sdk to packages

### New files
- `services/cpp-tdgl-runner/build_device.py` — Argo step: build mesh + write device.h5
- `services/cpp-tdgl-runner/build_timing.py` — Argo step: build timing + write timing.json
- `src/tdgl_sdk/__init__.py` — SDK public API
- `src/tdgl_sdk/client.py` — TDGLClient class
- `tests/test_zarr_per_site.py` — tests for per-site Zarr store
- `tests/test_device_timing_api.py` — tests for device/timing build endpoints
- `tests/test_sdk_client.py` — tests for SDK client
- `notebooks/tdgl_demo.ipynb` — Jupyter demo notebook

---

### Task 1: Update ZarrStore for per-site arrays

**Files:**
- Modify: `src/tdgl_data/zarr_store.py`
- Test: `tests/test_zarr_per_site.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_zarr_per_site.py`:

```python
import tempfile
from pathlib import Path

import numpy as np
import pytest

from tdgl_data.zarr_store import ZarrStore


@pytest.fixture
def store(tmp_path):
    return ZarrStore(str(tmp_path / "zarr"))


def test_create_run_per_site(store):
    n_sites = 50
    store.create_run("run-1", n_sites)

    frame = store.get_frame("run-1", 0)
    assert frame["psi_real"].shape == (50,)
    assert frame["psi_imag"].shape == (50,)
    assert frame["mu"].shape == (50,)


def test_append_frame_per_site(store):
    n_sites = 10
    store.create_run("run-1", n_sites)

    arrays = {
        "psi_real": np.ones(n_sites, dtype=np.float64) * 0.5,
        "psi_imag": np.zeros(n_sites, dtype=np.float64),
        "mu": np.linspace(-1, 1, n_sites),
    }
    store.append_frame("run-1", 0, arrays)

    frame = store.get_frame("run-1", 0)
    assert np.allclose(frame["psi_real"], 0.5)
    assert np.allclose(frame["psi_imag"], 0.0)
    assert np.allclose(frame["mu"], np.linspace(-1, 1, n_sites))


def test_append_multiple_frames(store):
    n_sites = 5
    store.create_run("run-1", n_sites)

    for i in range(3):
        arrays = {
            "psi_real": np.full(n_sites, float(i)),
            "psi_imag": np.zeros(n_sites),
            "mu": np.full(n_sites, float(i) * 0.1),
        }
        store.append_frame("run-1", i, arrays)

    assert store.get_frame("run-1", 0)["psi_real"][0] == 0.0
    assert store.get_frame("run-1", 1)["psi_real"][0] == 1.0
    assert store.get_frame("run-1", 2)["psi_real"][0] == 2.0


def test_get_all_frames(store):
    n_sites = 4
    store.create_run("run-1", n_sites)

    for i in range(3):
        arrays = {
            "psi_real": np.full(n_sites, float(i)),
            "psi_imag": np.zeros(n_sites),
            "mu": np.zeros(n_sites),
        }
        store.append_frame("run-1", i, arrays)

    all_frames = store.get_all_frames("run-1")
    assert all_frames["psi_real"].shape == (3, 4)
    assert all_frames["psi_real"][2, 0] == 2.0


def test_delete_run(store):
    n_sites = 3
    store.create_run("run-1", n_sites)
    store.delete_run("run-1")

    run_path = store.root / "runs" / "run-1"
    assert not run_path.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_zarr_per_site.py -v`
Expected: FAIL — `create_run` signature mismatch, `get_all_frames` missing

- [ ] **Step 3: Write minimal implementation**

Replace `src/tdgl_data/zarr_store.py` entirely:

```python
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import zarr


class ZarrStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _run_path(self, run_id: str) -> Path:
        return self.root / "runs" / run_id / "frames.zarr"

    def create_run(self, run_id: str, n_sites: int) -> None:
        run_path = self._run_path(run_id)
        run_path.parent.mkdir(parents=True, exist_ok=True)
        group = zarr.open_group(str(run_path), mode="w")
        chunks = (1, n_sites)
        for field in ("psi_real", "psi_imag", "mu"):
            group.create_array(
                field,
                shape=(0, n_sites),
                dtype="float64",
                chunks=chunks,
            )

    def append_frame(
        self,
        run_id: str,
        frame_index: int,
        arrays: dict[str, np.ndarray],
    ) -> None:
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r+")
        needed = frame_index + 1
        for field, data in arrays.items():
            ds = group[field]
            if needed > ds.shape[0]:
                ds.resize((needed,) + ds.shape[1:])
            ds[frame_index] = data

    def get_frame(self, run_id: str, frame_index: int) -> dict[str, np.ndarray]:
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r")
        return {
            "psi_real": np.array(group["psi_real"][frame_index]),
            "psi_imag": np.array(group["psi_imag"][frame_index]),
            "mu": np.array(group["mu"][frame_index]),
        }

    def get_all_frames(self, run_id: str) -> dict[str, np.ndarray]:
        run_path = self._run_path(run_id)
        group = zarr.open_group(str(run_path), mode="r")
        return {
            "psi_real": np.array(group["psi_real"]),
            "psi_imag": np.array(group["psi_imag"]),
            "mu": np.array(group["mu"]),
        }

    def delete_run(self, run_id: str) -> None:
        run_path = self._run_path(run_id)
        if run_path.parent.exists():
            shutil.rmtree(run_path.parent)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_zarr_per_site.py -v`
Expected: all 6 tests PASS

- [ ] **Step 5: Run existing tests to check no regressions**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/ -v --tb=short`
Expected: some existing tests will FAIL because `create_run` signature changed from `(run_id, grid_shape)` to `(run_id, n_sites)`. This is expected — we fix these in Task 2.

- [ ] **Step 6: Commit**

```bash
git add src/tdgl_data/zarr_store.py tests/test_zarr_per_site.py
git commit -m "feat: update ZarrStore for per-site 1D array storage"
```

---

### Task 2: Update data models and schemas for per-site storage

**Files:**
- Modify: `src/tdgl_data/models.py`
- Modify: `src/tdgl_data/schemas.py`
- Modify: `src/tdgl_data/repository.py`
- Test: `tests/test_repository.py` (update existing)

- [ ] **Step 1: Write the failing test**

Update `tests/test_repository.py` — add a new test at the bottom:

```python
def test_create_run_with_per_site_fields(session):
    run = create_run(
        session,
        solver_type="cpp-tdgl",
        n_sites=120,
        mesh_sites=[[0.0, 0.0], [1.0, 0.0], [0.5, 0.8]],
        mesh_elements=[[0, 1, 2]],
        device_params={"film_width": 10},
        timing_params={"mode": "simple"},
        solver_options={"dt": 1e-6},
    )
    session.commit()

    loaded = get_run(session, run.run_id)
    assert loaded.n_sites == 120
    assert loaded.mesh_sites == [[0.0, 0.0], [1.0, 0.0], [0.5, 0.8]]
    assert loaded.mesh_elements == [[0, 1, 2]]
    assert loaded.solver_options == {"dt": 1e-6}
```

Also update `_make_arrays` helper to produce per-site arrays instead of 2D:

```python
def _make_arrays(n_sites=12):
    return {
        "psi_real": [0.0] * n_sites,
        "psi_imag": [0.0] * n_sites,
        "mu": [0.0] * n_sites,
    }
```

And update `test_create_run_defaults` to use `n_sites`:

```python
def test_create_run_defaults(session):
    run = create_run(session, solver_type="synthetic", n_sites=48)
    session.commit()

    loaded = get_run(session, run.run_id)
    assert loaded is not None
    assert loaded.status == "created"
    assert loaded.solver_type == "synthetic"
    assert loaded.n_sites == 48
```

Update all calls to `create_run` in `test_repository.py` from `grid_shape=(x, y)` to `n_sites=N` and all calls to `append_frame_record` to use 1D array lists instead of 2D.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_repository.py -v`
Expected: FAIL — `n_sites` parameter not accepted, new columns missing

- [ ] **Step 3: Update models.py**

In `src/tdgl_data/models.py`, update the `Run` class — add columns after `mesh_metadata`:

```python
    mesh_sites: Mapped[dict | None] = mapped_column(
        json_type, default=None, nullable=True
    )
    mesh_elements: Mapped[dict | None] = mapped_column(
        json_type, default=None, nullable=True
    )
    n_sites: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    solver_options: Mapped[dict | None] = mapped_column(
        json_type, default=None, nullable=True
    )
```

Update the `Frame` class — remove the three array columns and `zarr_exists`:

```python
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
    frame_stats: Mapped[dict | None] = mapped_column(
        json_type, default=None, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    run: Mapped[Run] = relationship(back_populates="frames")
```

- [ ] **Step 4: Update schemas.py**

Replace `src/tdgl_data/schemas.py`:

```python
from typing import Annotated

from pydantic import BaseModel, Field

StrictPositiveInt = Annotated[int, Field(strict=True, gt=0)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class CreateRunRequest(BaseModel):
    solver_type: str = "synthetic"
    n_sites: StrictPositiveInt = Field(default=100)
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)
    git_commit: str | None = None
    image_tag: str | None = None
    total_frames: int | None = None
    mesh_sites: list[list[float]] | None = None
    mesh_elements: list[list[int]] | None = None
    solver_options: dict | None = None


class RunResponse(BaseModel):
    run_id: str
    status: str
    solver_type: str
    mesh_metadata: dict
    device_params: dict
    timing_params: dict
    metadata: dict
    created_at: str | None = None
    total_frames: int | None = None
    n_sites: int | None = None


class UpdateRunStatusRequest(BaseModel):
    status: str


class FrameAppendRequest(BaseModel):
    frame_index: StrictNonNegativeInt
    time_value: float
    je: float
    voltage: float
    psi_real: list[float]
    psi_imag: list[float]
    mu: list[float]


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
    arrays: dict[str, list[float]]


class MeshResponse(BaseModel):
    sites: list[list[float]]
    elements: list[list[int]]
    probe_indices: list[int]
    n_sites: int


class DeviceBuildRequest(BaseModel):
    film_width: float = 10.0
    film_height: float = 2.0
    elec_width: float = 0.5
    elec_height: float = 1.0
    elec_y_offset: float = 0.0
    probe_points: list[list[float]] = Field(default_factory=lambda: [[-2.0, 0.0], [2.0, 0.0]])
    max_edge_length: float = 0.5
    smooth: int = 100


class TimingBuildRequest(BaseModel):
    mode: str = "simple"
    je_initial: float = 0.0
    je_final: float = 10.0
    je_step: float = 1.0
    ramp_time: float = 1.0
    stable_time: float = 5.0
    save_time: float = 3.0
    ramp_down: bool = False
    segments: list[dict] | None = None
    solver_options: dict = Field(default_factory=dict)


class WorkflowSubmitRequest(BaseModel):
    device_params: dict = Field(default_factory=dict)
    timing_params: dict = Field(default_factory=dict)
    mesh_data: dict = Field(default_factory=dict)
    schedule: dict = Field(default_factory=dict)
    solver_options: dict = Field(default_factory=dict)
    resources: dict = Field(default_factory=lambda: {"cpu_cores": 2, "memory_mib": 2048})
```

- [ ] **Step 5: Update repository.py**

Update `create_run` signature and body:

```python
def create_run(
    session: Session,
    *,
    solver_type: str,
    n_sites: int = 100,
    device_params: dict | None = None,
    timing_params: dict | None = None,
    metadata: dict | None = None,
    git_commit: str | None = None,
    image_tag: str | None = None,
    total_frames: int | None = None,
    mesh_sites: list | None = None,
    mesh_elements: list | None = None,
    solver_options: dict | None = None,
) -> Run:
    run = Run(
        run_id=str(uuid4()),
        solver_type=solver_type,
        status="created",
        mesh_metadata={"n_sites": n_sites},
        device_params=device_params or {},
        timing_params=timing_params or {},
        metadata_=metadata or {},
        git_commit=git_commit,
        image_tag=image_tag,
        total_frames=total_frames,
        n_sites=n_sites,
        mesh_sites=mesh_sites,
        mesh_elements=mesh_elements,
        solver_options=solver_options,
    )
    session.add(run)
    session.flush()
    return run
```

Update `append_frame_record` — remove the array parameters:

```python
def append_frame_record(
    session: Session,
    *,
    run_id: str,
    frame_index: int,
    time_value: float,
    je: float,
    voltage: float,
    frame_stats: dict | None = None,
    status: str = "available",
) -> Frame:
    now = utcnow()
    frame = Frame(
        run_id=run_id,
        frame_index=frame_index,
        time_value=time_value,
        je=je,
        voltage=voltage,
        status=status,
        frame_stats=frame_stats,
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_repository.py -v`
Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/tdgl_data/models.py src/tdgl_data/schemas.py src/tdgl_data/repository.py tests/test_repository.py
git commit -m "feat: update data models for per-site storage (mesh_sites, n_sites, solver_options)"
```

---

### Task 3: Update data service API for per-site frames

**Files:**
- Modify: `src/tdgl_data/app.py`
- Test: `tests/test_api.py` (update existing)

- [ ] **Step 1: Update the API app**

The key changes in `src/tdgl_data/app.py`:

1. Update `api_create_run` to use `n_sites` instead of `grid_shape`
2. Update `api_append_frame` to accept 1D arrays and write to Zarr without grid_shape validation
3. Update `api_get_frame` to return 1D arrays from Zarr
4. Add `GET /api/runs/{run_id}/mesh` endpoint
5. Update `_run_response` to include `n_sites`
6. Remove `_grid_shape`, `_validate_frame_arrays`, `_compute_frame_stats` helpers (replaced with simpler per-site logic)

Replace the helpers and endpoint implementations. Key sections:

```python
def _run_response(run: Run) -> RunResponse:
    return RunResponse(
        run_id=run.run_id,
        status=run.status,
        solver_type=run.solver_type,
        mesh_metadata=run.mesh_metadata,
        device_params=run.device_params,
        timing_params=run.timing_params,
        metadata=run.metadata_,
        created_at=run.created_at.isoformat() if run.created_at else None,
        total_frames=run.total_frames,
        n_sites=run.n_sites,
    )
```

Update `api_create_run`:
```python
    @app.post("/api/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
    def api_create_run(body: CreateRunRequest) -> RunResponse:
        with session_factory() as session:
            run = create_run(
                session,
                solver_type=body.solver_type,
                n_sites=body.n_sites,
                device_params=body.device_params,
                timing_params=body.timing_params,
                metadata=body.metadata,
                git_commit=body.git_commit,
                image_tag=body.image_tag,
                total_frames=body.total_frames,
                mesh_sites=body.mesh_sites,
                mesh_elements=body.mesh_elements,
                solver_options=body.solver_options,
            )
            app.state.zarr_store.create_run(run.run_id, body.n_sites)
            session.commit()
            session.refresh(run)
        return _run_response(run)
```

Update `api_append_frame` — validate 1D array length, compute stats, write to Zarr:
```python
    @app.post(
        "/api/runs/{run_id}/frames",
        response_model=FrameMetadataResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def api_append_frame(run_id: str, body: FrameAppendRequest) -> FrameMetadataResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            if get_frame(session, run_id, body.frame_index) is not None:
                raise HTTPException(status_code=409, detail="Frame already exists")

            n_sites = run.n_sites or len(body.psi_real)
            for field_name in ("psi_real", "psi_imag", "mu"):
                value = getattr(body, field_name)
                if len(value) != n_sites:
                    raise HTTPException(
                        status_code=422,
                        detail=f"{field_name} must have {n_sites} elements, got {len(value)}",
                    )

            arrays = {
                "psi_real": np.asarray(body.psi_real, dtype="float64"),
                "psi_imag": np.asarray(body.psi_imag, dtype="float64"),
                "mu": np.asarray(body.mu, dtype="float64"),
            }
            stats = {}
            for name, arr in arrays.items():
                stats[name] = {"min": float(np.min(arr)), "max": float(np.max(arr))}

            app.state.zarr_store.append_frame(run_id, body.frame_index, arrays)
            try:
                frame = append_frame_record(
                    session,
                    run_id=run_id,
                    frame_index=body.frame_index,
                    time_value=body.time_value,
                    je=body.je,
                    voltage=body.voltage,
                    frame_stats=stats,
                )
            except IntegrityError:
                session.rollback()
                raise HTTPException(status_code=409, detail="Frame already exists") from None
            try:
                session.commit()
            except Exception:
                session.rollback()
                with session_factory() as cleanup_session:
                    delete_frame_record(cleanup_session, run_id, body.frame_index)
                    cleanup_session.commit()
                raise
            with session_factory() as count_session:
                frame_count = len([
                    f for f in get_timeline(count_session, run_id) if f.status == "available"
                ])
            app.state.event_bus.publish(run_id, FrameAvailableEvent(
                run_id=run_id,
                frame_index=body.frame_index,
                time_value=body.time_value,
                je=body.je,
                voltage=body.voltage,
                frame_count=frame_count,
            ))
            return _frame_metadata(frame)
```

Update `api_get_frame` to return 1D arrays:
```python
    @app.get("/api/runs/{run_id}/frames/{frame_index}", response_model=FrameResponse)
    def api_get_frame(
        run_id: str,
        frame_index: Annotated[int, ApiPath(ge=0)],
    ) -> FrameResponse:
        with session_factory() as session:
            frame = get_frame(session, run_id, frame_index)
            if frame is None or frame.status != "available":
                raise HTTPException(status_code=404, detail="Frame not found")
            zarr_arrays = app.state.zarr_store.get_frame(run_id, frame_index)
            arrays = {k: v.tolist() for k, v in zarr_arrays.items()}
            return FrameResponse(
                run_id=run_id,
                frame_index=frame_index,
                time_value=frame.time_value,
                je=frame.je,
                voltage=frame.voltage,
                arrays=arrays,
            )
```

Add mesh endpoint:
```python
    @app.get("/api/runs/{run_id}/mesh", response_model=MeshResponse)
    def api_get_mesh(run_id: str) -> MeshResponse:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            if run.mesh_sites is None:
                raise HTTPException(status_code=404, detail="Run has no mesh data")
            device_params = run.device_params or {}
            return MeshResponse(
                sites=run.mesh_sites,
                elements=run.mesh_elements or [],
                probe_indices=device_params.get("mesh", {}).get("probe_indices", []),
                n_sites=run.n_sites or len(run.mesh_sites),
            )
```

Remove `_grid_shape`, `_validate_frame_arrays`, `_compute_frame_stats`, `_update_stats` functions.

- [ ] **Step 2: Update test_api.py**

Update all tests that use `grid_shape` to use `n_sites`, and all frame payloads from 2D to 1D arrays. Key changes:

```python
# Every `grid_shape: [X, Y]` becomes `n_sites: N`
# Every 2D array like [[1.0, 2.0], [3.0, 4.0]] becomes 1D: [1.0, 2.0, 3.0, 4.0]
```

Add new test:
```python
def test_get_mesh_returns_stored_mesh_data(client):
    created = client.post("/api/runs", json={
        "solver_type": "cpp-tdgl",
        "n_sites": 3,
        "mesh_sites": [[0.0, 0.0], [1.0, 0.0], [0.5, 0.8]],
        "mesh_elements": [[0, 1, 2]],
        "device_params": {"mesh": {"probe_indices": [0, 2]}},
    })
    run_id = created.json()["run_id"]

    mesh = client.get(f"/api/runs/{run_id}/mesh")
    assert mesh.status_code == 200
    assert mesh.json()["sites"] == [[0.0, 0.0], [1.0, 0.0], [0.5, 0.8]]
    assert mesh.json()["elements"] == [[0, 1, 2]]
    assert mesh.json()["probe_indices"] == [0, 2]
    assert mesh.json()["n_sites"] == 3
```

- [ ] **Step 3: Run all tests**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/tdgl_data/app.py src/tdgl_data/schemas.py tests/test_api.py
git commit -m "feat: update data service API for per-site frame storage and mesh endpoint"
```

---

### Task 4: Add device/timing build and workflow submit APIs

**Files:**
- Modify: `src/tdgl_workflow/routes/api.py`
- Test: `tests/test_device_timing_api.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_device_timing_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from tdgl_workflow.app import create_app


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_device_build_returns_mesh(client):
    resp = client.post("/api/device/build", json={
        "film_width": 10.0,
        "film_height": 2.0,
        "elec_width": 0.5,
        "elec_height": 1.0,
        "elec_y_offset": 0.0,
        "probe_points": [[-2.0, 0.0], [2.0, 0.0]],
        "max_edge_length": 1.0,
        "smooth": 100,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "sites" in data
    assert "elements" in data
    assert "probe_indices" in data
    assert "num_sites" in data
    assert data["num_sites"] > 0
    assert len(data["sites"]) == data["num_sites"]


def test_timing_build_returns_steps(client):
    resp = client.post("/api/timing/build", json={
        "mode": "simple",
        "je_initial": 0.0,
        "je_final": 5.0,
        "je_step": 1.0,
        "ramp_time": 1.0,
        "stable_time": 5.0,
        "save_time": 3.0,
        "ramp_down": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "steps" in data
    assert len(data["steps"]) == 5
    assert data["n_steps"] == 5
    assert data["solve_time"] > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_device_timing_api.py -v`
Expected: PASS (these endpoints already exist as `/api/preview/mesh` and `/api/preview/timing`). If they pass, rename the routes in the next step.

- [ ] **Step 3: Add canonical route names**

In `src/tdgl_workflow/routes/api.py`, add the canonical `/api/device/build` and `/api/timing/build` route aliases alongside existing `/api/preview/mesh` and `/api/preview/timing`. Also add the `/api/workflows/submit` endpoint:

```python
@router.post("/device/build")
async def device_build(request: Request):
    """Canonical device build endpoint — same as /preview/mesh."""
    return await preview_mesh(request)


@router.post("/timing/build")
async def timing_build(request: Request):
    """Canonical timing build endpoint — same as /preview/timing."""
    return await preview_timing(request)


@router.post("/workflows/submit")
async def submit_workflow(request: Request):
    """Submit a 3-step Argo workflow (device + timing + simulate)."""
    body = await request.json()
    settings: Settings = request.app.state.settings

    device_params = body.get("device_params", {})
    timing_params = body.get("timing_params", {})
    mesh_data = body.get("mesh_data", {})
    schedule = body.get("schedule", {})
    solver_options = body.get("solver_options", {})
    resources = body.get("resources", {"cpu_cores": 2, "memory_mib": 2048})

    import uuid
    run_id = str(uuid.uuid4())

    n_sites = mesh_data.get("num_sites", len(mesh_data.get("sites", [])))
    mesh_sites = mesh_data.get("sites")
    mesh_elements = mesh_data.get("elements")

    import httpx
    with httpx.Client(timeout=30.0) as client:
        create_resp = client.post(
            f"{settings.data_service_url}/api/runs",
            json={
                "solver_type": "cpp-tdgl",
                "n_sites": n_sites,
                "device_params": device_params,
                "timing_params": timing_params,
                "mesh_sites": mesh_sites,
                "mesh_elements": mesh_elements,
                "solver_options": solver_options,
                "total_frames": schedule.get("n_steps", 0),
            },
        )
        create_resp.raise_for_status()
        created_run = create_resp.json()
        actual_run_id = created_run["run_id"]

    import json
    workflow = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Workflow",
        "metadata": {
            "generateName": f"cpp-tdgl-{actual_run_id[:8]}-",
            "namespace": settings.tdgl_namespace,
            "labels": {"run-id": actual_run_id},
        },
        "spec": {
            "workflowTemplateRef": {"name": "cpp-tdgl-sim"},
            "arguments": {
                "parameters": [
                    {"name": "run-id", "value": actual_run_id},
                    {"name": "data-service-url", "value": settings.data_service_url},
                    {"name": "device-params-json", "value": json.dumps(device_params)},
                    {"name": "timing-params-json", "value": json.dumps(timing_params)},
                    {"name": "solver-options-json", "value": json.dumps(solver_options)},
                    {"name": "cpu", "value": str(resources.get("cpu_cores", 2))},
                    {"name": "memory", "value": f"{resources.get('memory_gb', 4)}Gi"},
                ],
            },
        },
    }

    workflow_name = None
    try:
        with httpx.Client(timeout=15.0) as argo_client:
            argo_resp = argo_client.post(
                f"{settings.argo_server_url}/api/v1/workflows/{settings.tdgl_namespace}",
                json={"workflow": workflow},
                headers={"Content-Type": "application/json"},
                verify=False,
            )
            if argo_resp.status_code < 300:
                workflow_name = argo_resp.json()["metadata"]["name"]
    except httpx.HTTPError:
        pass

    return JSONResponse({
        "run_id": actual_run_id,
        "workflow_name": workflow_name,
        "status": "created",
    })
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_device_timing_api.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_workflow/routes/api.py tests/test_device_timing_api.py
git commit -m "feat: add canonical device/timing build + workflow submit APIs"
```

---

### Task 5: Create Argo build scripts (build_device.py, build_timing.py)

**Files:**
- Create: `services/cpp-tdgl-runner/build_device.py`
- Create: `services/cpp-tdgl-runner/build_timing.py`

- [ ] **Step 1: Create build_device.py**

Create `services/cpp-tdgl-runner/build_device.py`:

```python
"""Argo build-device step: generate mesh and write device.h5 + mesh_meta.json."""
import json
import os
import sys

sys.path.insert(0, "/app/vendor")

import h5py
import numpy as np

from tdgl_workflow.mesh import build_rectangular_device


def main():
    device_params = json.loads(os.environ["DEVICE_PARAMS"])
    data_dir = os.environ.get("DATA_DIR", "/data")

    mesh_data = build_rectangular_device(
        film_width=device_params["film_width"],
        film_height=device_params["film_height"],
        elec_width=device_params["elec_width"],
        elec_height=device_params["elec_height"],
        elec_y_offset=device_params["elec_y_offset"],
        probe_points=[tuple(p) for p in device_params["probe_points"]],
        max_edge_length=device_params["max_edge_length"],
        smooth=device_params.get("smooth", 100),
    )

    # Write mesh_meta.json for downstream steps
    meta_path = os.path.join(data_dir, "mesh_meta.json")
    with open(meta_path, "w") as f:
        json.dump(mesh_data, f)

    # Write device.h5 (HDF5 for C++ solver)
    h5_path = os.path.join(data_dir, "device.h5")
    with h5py.File(h5_path, "w") as f:
        dev = f.create_group("device")
        dc = mesh_data["device_constants"]
        for attr in ("name", "length_units", "K0", "A0", "Bc2", "Lambda"):
            if attr in dc:
                dev.attrs[attr] = dc[attr]

        lg = dev.create_group("layer")
        for k, v in mesh_data["layer"].items():
            lg.attrs[k] = v

        dev.create_dataset("probe_point_indices",
                           data=np.array(mesh_data["probe_indices"], dtype=np.int64))

        mg = dev.create_group("mesh")
        sites = np.array(mesh_data["sites"], dtype=np.float64)
        mg.create_dataset("sites", data=sites)
        mg.create_dataset("elements",
                          data=np.array(mesh_data["elements"], dtype=np.int64))
        mg.create_dataset("boundary_indices",
                          data=np.array(mesh_data["boundary_indices"], dtype=np.int64))
        mg.create_dataset("areas",
                          data=np.array(mesh_data["areas"], dtype=np.float64))

        em_data = mesh_data["edge_mesh"]
        eg = mg.create_group("edge_mesh")
        eg.create_dataset("centers",
                          data=np.array(em_data["centers"], dtype=np.float64))
        eg.create_dataset("edges",
                          data=np.array(em_data["edges"], dtype=np.int64))
        eg.create_dataset("boundary_edge_indices",
                          data=np.array(em_data["boundary_edge_indices"], dtype=np.int64))
        eg.create_dataset("directions",
                          data=np.array(em_data["directions"], dtype=np.float64))
        eg.create_dataset("edge_lengths",
                          data=np.array(em_data["edge_lengths"], dtype=np.float64))
        eg.create_dataset("dual_edge_lengths",
                          data=np.array(em_data["dual_edge_lengths"], dtype=np.float64))

        tg = dev.create_group("terminals")
        for t in mesh_data["terminals"]:
            name = t["name"]
            tgrp = tg.create_group(name)
            tgrp.attrs["name"] = name
            tgrp.create_dataset("site_indices",
                                data=np.array(t["site_indices"], dtype=np.int64))
            tgrp.create_dataset("edge_indices",
                                data=np.array(t["edge_indices"], dtype=np.int64))
            tgrp.create_dataset("boundary_edge_indices",
                                data=np.array(t["boundary_edge_indices"], dtype=np.int64))
            tgrp.attrs["length"] = t["length"]

    print(f"Device built: {mesh_data['num_sites']} sites, {mesh_data['num_elements']} elements")
    print(f"Written: {meta_path}, {h5_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create build_timing.py**

Create `services/cpp-tdgl-runner/build_timing.py`:

```python
"""Argo build-timing step: generate timing schedule and write timing.json."""
import json
import os
import sys

sys.path.insert(0, "/app/vendor")

from tdgl_workflow.timing import build_timing, build_timing_segmented


def main():
    timing_params = json.loads(os.environ["TIMING_PARAMS"])
    data_dir = os.environ.get("DATA_DIR", "/data")

    mode = timing_params.get("mode", "simple")
    if mode == "segmented":
        timing_data = build_timing_segmented(
            segments=timing_params["segments"],
            ramp_time=timing_params["ramp_time"],
            stable_time=timing_params["stable_time"],
            save_time=timing_params["save_time"],
        )
    else:
        timing_data = build_timing(
            je_initial=timing_params["je_initial"],
            je_final=timing_params["je_final"],
            je_step=timing_params["je_step"],
            ramp_time=timing_params["ramp_time"],
            stable_time=timing_params["stable_time"],
            save_time=timing_params["save_time"],
            ramp_down=timing_params.get("ramp_down", False),
        )

    out_path = os.path.join(data_dir, "timing.json")
    with open(out_path, "w") as f:
        json.dump(timing_data, f, indent=2)

    print(f"Timing built: {timing_data['n_steps']} steps, solve_time={timing_data['solve_time']:.2f}s")
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add services/cpp-tdgl-runner/build_device.py services/cpp-tdgl-runner/build_timing.py
git commit -m "feat: add Argo build-device and build-timing step scripts"
```

---

### Task 6: Update runner.py for per-site Zarr + shared volume

**Files:**
- Modify: `services/cpp-tdgl-runner/runner.py`

- [ ] **Step 1: Update runner.py**

Replace `services/cpp-tdgl-runner/runner.py`:

```python
"""cpp-tdgl simulation runner (Argo simulate step).

Reads device.h5 and timing.json from shared volume, runs the C++ solver
step-by-step, writes per-site data directly to Zarr via data-service API.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import h5py
import httpx
import numpy as np

SOLVER_PATH = os.environ.get("TDGL_SOLVER_PATH", "/app/tdgl_solve")
DATA_DIR = os.environ.get("DATA_DIR", "/data")


def build_solver_hdf5(mesh_meta_path: str, output_path: str, je: float,
                      solver_options: dict) -> None:
    """Copy device.h5 and add solver options group."""
    import shutil
    device_h5 = os.path.join(DATA_DIR, "device.h5")
    shutil.copy2(device_h5, output_path)

    with h5py.File(output_path, "a") as f:
        og = f.create_group("options")
        og.attrs["solve_time"] = solver_options.get("solve_time", 5.0)
        og.attrs["skip_time"] = 0.0
        og.attrs["dt_init"] = solver_options.get("dt", 1e-6)
        og.attrs["dt_max"] = solver_options.get("max_dt", 0.1)
        og.attrs["adaptive"] = solver_options.get("adaptive", True)
        og.attrs["save_every"] = 1
        og.attrs["terminal_psi"] = 1.0
        og.attrs["applied_field"] = 0.0
        og.attrs["current_units"] = "uA"
        og.attrs["field_units"] = "uT"
        og.attrs["include_screening"] = False
        og.attrs["max_iterations_per_step"] = 50
        og.attrs["screening_tolerance"] = 1e-5
        og.attrs["screening_step_size"] = 1.0
        og.attrs["screening_step_drag"] = 0.0
        og.attrs["adaptive_window"] = 200
        og.attrs["max_solve_retries"] = 4
        og.attrs["adaptive_time_step_multiplier"] = 0.5


def read_last_step(hdf5_path: str) -> dict | None:
    """Read the last saved step from the solver output HDF5."""
    with h5py.File(hdf5_path, "r") as f:
        data_grp = f.get("data")
        if not data_grp or not data_grp.keys():
            return None
        last_key = max(data_grp.keys(), key=lambda k: int(k))
        g = data_grp[last_key]
        result = {
            "step": int(g.attrs.get("step", 0)),
            "time": float(g.attrs.get("time", 0.0)),
            "dt": float(g.attrs.get("dt", 0.0)),
        }
        if "psi_real" in g:
            result["psi_real"] = np.array(g["psi_real"], dtype=np.float64)
        if "psi_imag" in g:
            result["psi_imag"] = np.array(g["psi_imag"], dtype=np.float64)
        if "mu" in g:
            result["mu"] = np.array(g["mu"], dtype=np.float64)
        return result


def main() -> None:
    run_id = os.environ["TDGL_RUN_ID"]
    data_url = os.environ["TDGL_DATA_SERVICE_URL"]
    solver_options_raw = os.environ.get("SOLVER_OPTIONS", "{}")
    solver_options = json.loads(solver_options_raw)

    client = httpx.Client(base_url=data_url, timeout=120.0)

    # Read timing from shared volume
    timing_path = os.path.join(DATA_DIR, "timing.json")
    with open(timing_path) as f:
        timing_data = json.load(f)

    # Read mesh meta for probe indices
    mesh_meta_path = os.path.join(DATA_DIR, "mesh_meta.json")
    with open(mesh_meta_path) as f:
        mesh_meta = json.load(f)

    sites = np.array(mesh_meta["sites"], dtype=np.float64)
    probe_indices = mesh_meta["probe_indices"]

    steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])

    client.patch(f"/api/runs/{run_id}/status", json={"status": "running"})

    try:
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            restart_path = None

            for step_index, step in enumerate(steps):
                je = step["je_end"]

                input_hdf5 = os.path.join(tmpdir, f"input_{step_index}.h5")
                output_hdf5 = os.path.join(tmpdir, f"output_{step_index}.h5")

                build_solver_hdf5(mesh_meta_path, input_hdf5, je, solver_options)

                cmd = [
                    SOLVER_PATH,
                    "--mesh", input_hdf5,
                    "--output", output_hdf5,
                    "--source-current", str(je),
                    "--drain-current", str(-je),
                ]
                if restart_path:
                    cmd.extend(["--restart", restart_path])

                print(f"Step {step_index + 1}/{len(steps)}: Je={je:.4f}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)

                if result.returncode != 0:
                    print(f"Solver failed: {result.stderr[-500:]}", file=sys.stderr)
                    n_sites = len(sites)
                    frame_data = {
                        "frame_index": step_index,
                        "time_value": step["stable_end"],
                        "je": je,
                        "voltage": 0.0,
                        "psi_real": [0.0] * n_sites,
                        "psi_imag": [0.0] * n_sites,
                        "mu": [0.0] * n_sites,
                    }
                else:
                    last = read_last_step(output_hdf5)
                    voltage = 0.0
                    if last and "psi_real" in last:
                        psi_real = last["psi_real"].tolist()
                        psi_imag = last["psi_imag"].tolist()
                        mu = last["mu"].tolist()
                        if len(probe_indices) >= 2:
                            voltage = float(mu[probe_indices[1]] - mu[probe_indices[0]])
                    else:
                        psi_real = [0.0] * len(sites)
                        psi_imag = [0.0] * len(sites)
                        mu = [0.0] * len(sites)

                    frame_data = {
                        "frame_index": step_index,
                        "time_value": step["stable_end"],
                        "je": je,
                        "voltage": voltage,
                        "psi_real": psi_real,
                        "psi_imag": psi_imag,
                        "mu": mu,
                    }
                    restart_path = output_hdf5

                resp = client.post(f"/api/runs/{run_id}/frames", json=frame_data)
                resp.raise_for_status()
                print(f"  Posted frame {step_index + 1}/{len(steps)}")

        client.patch(f"/api/runs/{run_id}/status", json={"status": "completed"})
        print(f"Run {run_id} completed")

    except Exception as exc:
        client.patch(f"/api/runs/{run_id}/status", json={"status": "failed"})
        print(f"Run {run_id} failed: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add services/cpp-tdgl-runner/runner.py
git commit -m "feat: update runner for per-site Zarr output and shared volume input"
```

---

### Task 7: Update Argo workflow YAML for 3-step pipeline

**Files:**
- Modify: `workflows/cpp-tdgl-sim.yaml`

- [ ] **Step 1: Replace workflow YAML**

Replace `workflows/cpp-tdgl-sim.yaml`:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: cpp-tdgl-sim
  namespace: tdgl
spec:
  entrypoint: simulation-pipeline
  arguments:
    parameters:
      - name: run-id
        value: ""
      - name: data-service-url
        value: "http://data-viewer.tdgl.svc.cluster.local"
      - name: image
        value: "ghcr.io/fangrh/cpp-tdgl-runner:latest"
      - name: device-params-json
        value: "{}"
      - name: timing-params-json
        value: "{}"
      - name: solver-options-json
        value: "{}"
      - name: cpu
        value: "2"
      - name: memory
        value: "4Gi"

  templates:
    - name: simulation-pipeline
      steps:
        - - name: build-device
            template: build-device-step
        - - name: build-timing
            template: build-timing-step
        - - name: simulate
            template: simulate-step

    - name: build-device-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/build_device.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: DEVICE_PARAMS
            value: "{{workflow.parameters.device-params-json}}"
          - name: DATA_DIR
            value: "/data"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: build-timing-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/build_timing.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: TIMING_PARAMS
            value: "{{workflow.parameters.timing-params-json}}"
          - name: DATA_DIR
            value: "/data"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: simulate-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/runner.py]
        env:
          - name: TDGL_RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: TDGL_DATA_SERVICE_URL
            value: "{{workflow.parameters.data-service-url}}"
          - name: DATA_DIR
            value: "/data"
          - name: SOLVER_OPTIONS
            value: "{{workflow.parameters.solver-options-json}}"
        resources:
          requests:
            cpu: "{{workflow.parameters.cpu}}"
            memory: "{{workflow.parameters.memory}}"
          limits:
            cpu: "{{workflow.parameters.cpu}}"
            memory: "{{workflow.parameters.memory}}"
        volumeMounts:
          - name: run-data
            mountPath: /data

  volumeClaimTemplates:
    - metadata:
        name: run-data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
```

- [ ] **Step 2: Commit**

```bash
git add workflows/cpp-tdgl-sim.yaml
git commit -m "feat: 3-step Argo workflow (build-device, build-timing, simulate)"
```

---

### Task 8: Update Dockerfile

**Files:**
- Modify: `services/cpp-tdgl-runner/Dockerfile`

- [ ] **Step 1: Update Dockerfile**

The Dockerfile needs to include the tdgl_workflow package (for mesh.py and timing.py) in the runner image, plus zarr.

```dockerfile
FROM python:3.13

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake \
    libeigen3-dev libhdf5-dev libsuitesparse-dev libomp-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build/cpp-tdgl
COPY src/cpp-tdgl/ .
RUN mkdir build && cd build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DBUILD_BENCHMARKS=OFF && \
    cmake --build . -j$(nproc) && \
    cmake --install . --prefix /usr/local

WORKDIR /app
RUN pip install --no-cache-dir numpy httpx h5py scipy zarr tdgl sqlalchemy pydantic pydantic-settings

COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY services/cpp-tdgl-runner/runner.py /app/runner.py
COPY services/cpp-tdgl-runner/build_device.py /app/build_device.py
COPY services/cpp-tdgl-runner/build_timing.py /app/build_timing.py

CMD ["python", "/app/runner.py"]
```

- [ ] **Step 2: Commit**

```bash
git add services/cpp-tdgl-runner/Dockerfile
git commit -m "feat: update Dockerfile with tdgl_workflow vendor and build scripts"
```

---

### Task 9: Update viewer HTML for 4-panel per-site rendering

**Files:**
- Modify: `src/tdgl_data/static/viewer.html`

- [ ] **Step 1: Update viewer**

The viewer changes are substantial. Key modifications:

1. **Add Phase plot panel** — change from 3-panel to 4-panel layout (2x2 grid)
2. **Add mesh loading** — fetch `GET /api/runs/{run_id}/mesh` and cache
3. **Add interpolation function** — interpolate per-site data to 2D grid for heatmap
4. **Update frame rendering** — compute |psi|^2, phase, mu from 1D arrays

Update the CSS for 4-panel layout:
```css
.plots-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
```

Add a Phase panel div:
```html
<section class="plots plots-row" aria-label="Plots">
  <div class="panel">
    <h2>|psi|^2</h2>
    <div id="psiPlot" class="plotly-div"></div>
  </div>
  <div class="panel">
    <h2>Phase</h2>
    <div id="phasePlot" class="plotly-div"></div>
  </div>
  <div class="panel">
    <h2>mu</h2>
    <div id="muPlot" class="plotly-div"></div>
  </div>
  <div class="panel">
    <h2>I-V curve</h2>
    <div id="ivPlot" class="plotly-div"></div>
  </div>
</section>
```

Add mesh state and interpolation:
```javascript
const state = {
  // ... existing fields ...
  mesh: null,   // { sites, elements, probe_indices, n_sites }
};

async function loadMesh(runId) {
  if (state.mesh) return;
  try {
    state.mesh = await requestJson(`api/runs/${runId}/mesh`);
  } catch (_e) {
    state.mesh = null;
  }
}

function interpolateToGrid(sites, values, nx, ny) {
  // Simple nearest-neighbor interpolation to regular grid
  const xs = sites.map(s => s[0]);
  const ys = sites.map(s => s[1]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs);
  const yMin = Math.min(...ys), yMax = Math.max(...ys);

  const grid = new Array(ny).fill(null).map(() => new Array(nx).fill(NaN));
  const cellW = (xMax - xMin) / nx;
  const cellH = (yMax - yMin) / ny;

  for (let i = 0; i < sites.length; i++) {
    const gx = Math.min(nx - 1, Math.max(0, Math.floor((sites[i][0] - xMin) / cellW)));
    const gy = Math.min(ny - 1, Math.max(0, Math.floor((sites[i][1] - yMin) / cellH)));
    grid[gy][gx] = values[i];
  }

  // Fill NaN gaps with nearest neighbor
  for (let y = 0; y < ny; y++) {
    for (let x = 0; x < nx; x++) {
      if (isNaN(grid[y][x])) {
        let minDist = Infinity, nearest = 0;
        for (let i = 0; i < sites.length; i++) {
          const sx = Math.min(nx - 1, Math.max(0, Math.floor((sites[i][0] - xMin) / cellW)));
          const sy = Math.min(ny - 1, Math.max(0, Math.floor((sites[i][1] - yMin) / cellH)));
          const d = Math.abs(sx - x) + Math.abs(sy - y);
          if (d < minDist) { minDist = d; nearest = i; }
        }
        grid[y][x] = values[nearest];
      }
    }
  }

  const xg = []; const yg = [];
  for (let i = 0; i < nx; i++) xg.push(xMin + (i + 0.5) * cellW);
  for (let i = 0; i < ny; i++) yg.push(yMin + (i + 0.5) * cellH);
  return { grid, xg, yg };
}

function psiMagnitude1D(psiReal, psiImag) {
  return psiReal.map((r, i) => Math.hypot(r, psiImag[i]));
}

function psiPhase1D(psiReal, psiImag) {
  return psiReal.map((r, i) => Math.atan2(psiImag[i], r));
}
```

Update `loadTimeline` to also call `loadMesh(runId)`:
```javascript
    async function loadTimeline(runId) {
      // ... existing code ...
      await loadMesh(runId);
      // ... rest of existing code ...
    }
```

Update `loadFrame` to handle 1D arrays:
```javascript
    async function loadFrame(targetFrameIndex) {
      // ... existing metadata lookup ...
      const frame = state.frameBuffer.get(targetFrameIndex) ||
        await requestJson(`api/runs/${state.runId}/frames/${metadata.frame_index}`);
      state.frameBuffer.set(targetFrameIndex, frame);

      const psiReal = frame.arrays.psi_real;
      const psiImag = frame.arrays.psi_imag;
      const mu = frame.arrays.mu;

      if (state.mesh && Array.isArray(psiReal) && typeof psiReal[0] === "number") {
        // Per-site data — interpolate
        const psq = psiMagnitude1D(psiReal, psiImag);
        const phase = psiPhase1D(psiReal, psiImag);
        const nx = 80, ny = 54;
        const psqGrid = interpolateToGrid(state.mesh.sites, psq, nx, ny);
        const phaseGrid = interpolateToGrid(state.mesh.sites, phase, nx, ny);
        const muGrid = interpolateToGrid(state.mesh.sites, mu, nx, ny);

        renderPsiHeatmap(psqGrid.grid, state.psiBounds);
        renderPhaseHeatmap(phaseGrid.grid);
        renderMuHeatmap(muGrid.grid, state.timeline?.stats?.mu || null);
      } else {
        // Legacy 2D grid data
        const psiData = psiMagnitude(frame.arrays);
        state.psiBounds = adaptivePsiBounds(state.psiBounds, frame.arrays);
        renderPsiHeatmap(psiData, state.psiBounds);
        renderMuHeatmap(frame.arrays.mu, state.timeline?.stats?.mu || null);
      }

      renderIvCurve(state.iv, frame.frame_index);
      // ... rest of existing code ...
    }
```

Add `renderPhaseHeatmap`:
```javascript
    function renderPhaseHeatmap(data) {
      const trace = {
        z: data,
        type: "heatmap",
        colorscale: "Twilight",
        zmin: -Math.PI,
        zmax: Math.PI,
        showscale: true,
        colorbar: {title: "Phase", thickness: 15},
      };
      Plotly.react("phasePlot", [trace], HEATMAP_LAYOUT, HEATMAP_CONFIG);
    }
```

- [ ] **Step 2: Run viewer tests**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_api.py -v -k viewer`
Expected: some tests may need updating for the new 4-panel HTML structure (e.g., `test_viewer_uses_single_row_plot_layout` now has 2x2). Update those tests.

- [ ] **Step 3: Commit**

```bash
git add src/tdgl_data/static/viewer.html tests/test_api.py
git commit -m "feat: 4-panel viewer with per-site interpolation (|psi|^2, Phase, mu, I-V)"
```

---

### Task 10: Create Python SDK client

**Files:**
- Create: `src/tdgl_sdk/__init__.py`
- Create: `src/tdgl_sdk/client.py`
- Test: `tests/test_sdk_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_sdk_client.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from tdgl_sdk.client import TDGLClient


def test_build_device_calls_api():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "sites": [[0, 0], [1, 0], [0.5, 0.8]],
        "elements": [[0, 1, 2]],
        "num_sites": 3,
        "probe_indices": [0, 2],
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = client.build_device(film_width=10, film_height=2)
        mock_post.assert_called_once()
        assert result["num_sites"] == 3


def test_build_timing_calls_api():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "steps": [{"je_start": 0, "je_end": 1}],
        "n_steps": 1,
        "solve_time": 5.0,
    }

    with patch("httpx.post", return_value=mock_resp) as mock_post:
        result = client.build_timing(je_initial=0, je_final=5)
        mock_post.assert_called_once()
        assert result["n_steps"] == 1


def test_get_run_status():
    client = TDGLClient("http://test-host")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"run_id": "abc", "status": "running"}

    with patch("httpx.get", return_value=mock_resp):
        run = client.get_run("abc")
        assert run["status"] == "running"
```

- [ ] **Step 2: Create the SDK package**

Create `src/tdgl_sdk/__init__.py`:
```python
from tdgl_sdk.client import TDGLClient

__all__ = ["TDGLClient"]
```

Create `src/tdgl_sdk/client.py`:
```python
import httpx


class TDGLClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def build_device(self, **params) -> dict:
        resp = httpx.post(f"{self.base_url}/api/device/build", json=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def build_timing(self, **params) -> dict:
        resp = httpx.post(f"{self.base_url}/api/timing/build", json=params, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def submit_simulation(self, *, device_params: dict, timing_params: dict,
                          mesh_data: dict, schedule: dict,
                          solver_options: dict | None = None,
                          resources: dict | None = None) -> dict:
        resp = httpx.post(f"{self.base_url}/api/workflows/submit", json={
            "device_params": device_params,
            "timing_params": timing_params,
            "mesh_data": mesh_data,
            "schedule": schedule,
            "solver_options": solver_options or {},
            "resources": resources or {"cpu_cores": 2, "memory_mib": 2048},
        }, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def get_run(self, run_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def list_runs(self) -> list[dict]:
        resp = httpx.get(f"{self.base_url}/api/runs", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_run_status(self, run_id: str) -> str:
        return self.get_run(run_id)["status"]

    def get_mesh(self, run_id: str) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}/mesh", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_frame(self, run_id: str, frame_index: int) -> dict:
        resp = httpx.get(f"{self.base_url}/api/runs/{run_id}/frames/{frame_index}", timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    def preview_device(self, device_result: dict):
        import plotly.graph_objects as go
        import numpy as np

        sites = np.array(device_result["sites"])
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=sites[:, 0], y=sites[:, 1],
            mode="markers", marker=dict(size=3),
            name="Mesh sites",
        ))
        fig.update_layout(title=f"Device: {device_result['num_sites']} sites")
        return fig

    def preview_timing(self, timing_result: dict):
        import plotly.graph_objects as go

        steps = timing_result["steps"]
        jes = [s["je_end"] for s in steps]
        times = [s["stable_end"] for s in steps]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=times, y=jes,
            mode="lines+markers",
            name="Je sequence",
        ))
        fig.update_layout(
            title=f"Timing: {timing_result['n_steps']} steps",
            xaxis_title="Time (s)",
            yaxis_title="Je (uA)",
        )
        return fig

    def view_results(self, run_id: str):
        import plotly.graph_objects as go
        import numpy as np

        mesh = self.get_mesh(run_id)
        runs = self.list_runs()
        run = next(r for r in runs if r["run_id"] == run_id)
        total = run.get("total_frames", 0)

        frames = []
        for i in range(total):
            try:
                frames.append(self.get_frame(run_id, i))
            except Exception:
                break

        if not frames:
            print("No frames available yet.")
            return None

        sites = np.array(mesh["sites"])
        last = frames[-1]
        pr = np.array(last["arrays"]["psi_real"])
        pi = np.array(last["arrays"]["psi_imag"])
        mu = np.array(last["arrays"]["mu"])
        psq = pr**2 + pi**2

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=sites[:, 0], y=sites[:, 1],
            mode="markers",
            marker=dict(color=psq, colorscale="Viridis", size=5, showscale=True),
            name="|psi|^2",
        ))
        fig.update_layout(title=f"Run {run_id[:8]} — last frame |psi|^2")
        return fig
```

- [ ] **Step 3: Update pyproject.toml packages**

Add `tdgl_sdk*` to the packages find:

```toml
[tool.setuptools.packages.find]
where = ["src"]
include = ["tdgl_data*", "tdgl_generator*", "tdgl_workflow*", "tdgl_sdk*"]
```

- [ ] **Step 4: Run tests**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/test_sdk_client.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/tdgl_sdk/__init__.py src/tdgl_sdk/client.py tests/test_sdk_client.py pyproject.toml
git commit -m "feat: add Python SDK client (TDGLClient) for Jupyter and scripting"
```

---

### Task 11: Create Jupyter demo notebook

**Files:**
- Create: `notebooks/tdgl_demo.ipynb`

- [ ] **Step 1: Create the notebook**

Create `notebooks/tdgl_demo.ipynb` with the following cells:

**Cell 1 (markdown):** `# TDGL Simulation Platform Demo`

**Cell 2 (code):**
```python
from tdgl_sdk import TDGLClient

client = TDGLClient("http://localhost:8080")
print("Connected to TDGL Platform")
```

**Cell 3 (markdown):** `## Step 1: Build Device`

**Cell 4 (code):**
```python
device = client.build_device(
    film_width=10.0,
    film_height=2.0,
    elec_width=0.5,
    elec_height=1.0,
    probe_points=[[-2.0, 0.0], [2.0, 0.0]],
    max_edge_length=0.5,
)
print(f"Device: {device['num_sites']} sites, {device['num_elements']} elements")
```

**Cell 5 (code):**
```python
client.preview_device(device)
```

**Cell 6 (markdown):** `## Step 2: Build Timing`

**Cell 7 (code):**
```python
timing = client.build_timing(
    je_initial=0.0,
    je_final=5.0,
    je_step=1.0,
    ramp_time=1.0,
    stable_time=5.0,
    save_time=3.0,
)
print(f"Timing: {timing['n_steps']} steps, total time: {timing['solve_time']:.1f}s")
```

**Cell 8 (code):**
```python
client.preview_timing(timing)
```

**Cell 9 (markdown):** `## Step 3: Submit Simulation`

**Cell 10 (code):**
```python
# Extract mesh_data from device result for workflow submission
mesh_data = {
    "sites": device["sites"],
    "elements": device["elements"],
    "num_sites": device["num_sites"],
    "probe_indices": device["probe_indices"],
}

run = client.submit_simulation(
    device_params=device,
    timing_params=timing,
    mesh_data=mesh_data,
    schedule=timing,
)
print(f"Run submitted: {run['run_id'][:8]}  Status: {run['status']}")
```

**Cell 11 (markdown):** `## Step 4: Monitor & View Results`

**Cell 12 (code):**
```python
import time

status = client.get_run_status(run["run_id"])
while status == "running":
    print(f"Status: {status}...")
    time.sleep(5)
    status = client.get_run_status(run["run_id"])

print(f"Final status: {status}")
```

**Cell 13 (code):**
```python
if status == "completed":
    client.view_results(run["run_id"])
else:
    print(f"Run did not complete: {status}")
```

- [ ] **Step 2: Commit**

```bash
git add notebooks/tdgl_demo.ipynb
git commit -m "docs: add Jupyter notebook demo for TDGL SDK"
```

---

### Task 12: Update simulate route for new workflow submit

**Files:**
- Modify: `src/tdgl_workflow/routes/simulate.py`

- [ ] **Step 1: Update simulate.py**

Update `simulate_submit` in `src/tdgl_workflow/routes/simulate.py` to use the new `/api/workflows/submit` pattern. The key change is using the workflow submit API instead of directly constructing the Argo workflow:

In the `simulate_submit` function, replace the direct Argo workflow construction with a call to the internal submit API or directly to the data service. The device_params and timing_params stored in session already contain the necessary data. The mesh is rebuilt on submit (same as current behavior).

Update the section after `full_timing_params["solver_options"] = solver_options`:

```python
    mesh_data = full_device_params.get("mesh", {})

    import httpx
    with httpx.Client(timeout=30.0) as client:
        submit_resp = client.post(
            f"{settings.base_url or ''}/api/workflows/submit",
            json={
                "device_params": full_device_params,
                "timing_params": full_timing_params,
                "mesh_data": mesh_data,
                "schedule": full_timing_params["schedule"],
                "solver_options": solver_options,
                "resources": {"cpu_cores": cpu_cores, "memory_gb": memory_gb},
            },
        )
        submit_resp.raise_for_status()
        result = submit_resp.json()
        run_id = result["run_id"]
```

Remove the old direct Argo workflow submission block.

- [ ] **Step 2: Commit**

```bash
git add src/tdgl_workflow/routes/simulate.py
git commit -m "refactor: simulate route uses /api/workflows/submit endpoint"
```

---

### Task 13: Update simulate page template for status badges

**Files:**
- Modify: `src/tdgl_workflow/templates/simulate.html`

- [ ] **Step 1: Update simulate template**

In the run list section of `src/tdgl_workflow/templates/simulate.html`, update the status display to use colored badges:

Find the run list rendering section and ensure each run shows:
- Yellow badge for "running"
- Green badge for "completed"
- Red badge for "failed"
- Gray badge for "created"

Add CSS for badges (or use inline styles) and update the run list item template to show the status with the correct color.

- [ ] **Step 2: Commit**

```bash
git add src/tdgl_workflow/templates/simulate.html
git commit -m "feat: add status badges to simulate page run list"
```

---

### Task 14: Final integration test

**Files:**
- Test: `tests/test_api.py`

- [ ] **Step 1: Run full test suite**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/ -v --tb=short`
Expected: all PASS

- [ ] **Step 2: Fix any remaining test failures**

If any tests fail due to the model/schema changes, update them to use the new per-site format.

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "fix: resolve remaining test failures after per-site migration"
```

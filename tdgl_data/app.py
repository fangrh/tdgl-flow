import shutil
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path as ApiPath, status
from fastapi.middleware.cors import CORSMiddleware
import numpy as np
from sqlalchemy.exc import IntegrityError

from tdgl_data.config import Settings
from tdgl_data.db import create_engine_from_url, create_session_factory
from tdgl_data.models import Base, Frame, Run
from tdgl_data.repository import (
    append_frame_record,
    create_run,
    delete_frame_record,
    get_frame,
    get_iv_points,
    get_run,
    get_timeline,
    list_runs,
    mark_frame_available,
)
from tdgl_data.schemas import (
    CreateRunRequest,
    FrameAppendRequest,
    FrameMetadataResponse,
    FrameResponse,
    IVPointResponse,
    RunResponse,
    TimelineResponse,
)
from tdgl_data.zarr_store import FilesystemZarrStore


def _remove_zarr_store(zarr_store: FilesystemZarrStore, store_uri: str) -> None:
    root = zarr_store.root.resolve()
    path = (root / store_uri).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return

    if path.exists():
        shutil.rmtree(path)
    try:
        path.parent.rmdir()
    except OSError:
        pass


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


def _frame_metadata(frame: Frame) -> FrameMetadataResponse:
    return FrameMetadataResponse(
        frame_index=frame.frame_index,
        time_value=frame.time_value,
        je=frame.je,
        voltage=frame.voltage,
        status=frame.status,
    )


def _grid_shape(run: Run) -> tuple[int, int]:
    grid_shape = run.mesh_metadata.get("grid_shape")
    if (
        not isinstance(grid_shape, list)
        or len(grid_shape) != 2
        or not all(isinstance(value, int) for value in grid_shape)
    ):
        raise HTTPException(status_code=500, detail="Invalid run grid metadata")
    return grid_shape[0], grid_shape[1]


def _frame_arrays(body: FrameAppendRequest, grid_shape: tuple[int, int]) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    for field in ("psi_real", "psi_imag", "mu"):
        value = getattr(body, field)
        try:
            array = np.asarray(value, dtype="float32")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{field} must be a rectangular 2D array") from exc
        if array.ndim != 2:
            raise HTTPException(status_code=422, detail=f"{field} must be a rectangular 2D array")
        if array.shape != grid_shape:
            raise HTTPException(status_code=422, detail=f"{field} shape must match run grid_shape")
        arrays[field] = array
    return arrays


def _update_stats(
    stats: dict[str, dict[str, float]],
    arrays: dict[str, np.ndarray],
) -> None:
    for name, values in arrays.items():
        entry = stats.setdefault(name, {"min": float("inf"), "max": float("-inf")})
        entry["min"] = min(entry["min"], float(np.min(values)))
        entry["max"] = max(entry["max"], float(np.max(values)))


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
            created_store_uri: str | None = None
            try:
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
                created_store_uri = zarr_store.create_run_store(
                    run.run_id,
                    grid_shape=body.grid_shape,
                    fields=("psi_real", "psi_imag", "mu"),
                )
                run.zarr_root = created_store_uri
                session.commit()
            except Exception:
                session.rollback()
                if created_store_uri is not None:
                    _remove_zarr_store(zarr_store, created_store_uri)
                raise
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

            arrays = _frame_arrays(body, _grid_shape(run))
            try:
                frame = append_frame_record(
                    session,
                    run_id=run_id,
                    frame_index=body.frame_index,
                    time_value=body.time_value,
                    je=body.je,
                    voltage=body.voltage,
                    zarr_group=run.zarr_root,
                    status="writing",
                )
            except IntegrityError:
                session.rollback()
                raise HTTPException(status_code=409, detail="Frame already exists") from None
            try:
                zarr_store.append_frame(run_id, body.frame_index, arrays)
            except Exception:
                session.rollback()
                raise
            try:
                mark_frame_available(session, frame)
                session.commit()
            except Exception:
                session.rollback()
                zarr_store.clear_frame(run_id, body.frame_index)
                with session_factory() as cleanup_session:
                    delete_frame_record(cleanup_session, run_id, body.frame_index)
                    cleanup_session.commit()
                raise
            return _frame_metadata(frame)

    @app.get("/api/runs/{run_id}/timeline", response_model=TimelineResponse)
    def api_timeline(run_id: str) -> TimelineResponse:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            frames = [frame for frame in get_timeline(session, run_id) if frame.status == "available"]

        stats: dict[str, dict[str, float]] = {}
        for frame in frames:
            arrays = zarr_store.read_frame(
                run_id,
                frame.frame_index,
                fields=("psi_real", "psi_imag", "mu"),
            )
            _update_stats(stats, arrays)

        return TimelineResponse(
            run_id=run_id,
            frames=[_frame_metadata(frame) for frame in frames],
            stats=stats,
        )

    @app.get("/api/runs/{run_id}/iv", response_model=list[IVPointResponse])
    def api_iv(run_id: str) -> list[IVPointResponse]:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            return [
                IVPointResponse(
                    frame_index=point.frame_index,
                    time_value=point.time_value,
                    je=point.je,
                    voltage=point.voltage,
                )
                for point in get_iv_points(session, run_id)
                if get_frame(session, run_id, point.frame_index).status == "available"
            ]

    @app.get("/api/runs/{run_id}/frames/{frame_index}", response_model=FrameResponse)
    def api_get_frame(
        run_id: str,
        frame_index: Annotated[int, ApiPath(ge=0)],
    ) -> FrameResponse:
        with session_factory() as session:
            frame = get_frame(session, run_id, frame_index)
            if frame is None or frame.status != "available":
                raise HTTPException(status_code=404, detail="Frame not found")
            arrays = zarr_store.read_frame(
                run_id,
                frame_index,
                fields=("psi_real", "psi_imag", "mu"),
            )
            return FrameResponse(
                run_id=run_id,
                frame_index=frame_index,
                time_value=frame.time_value,
                je=frame.je,
                voltage=frame.voltage,
                arrays={name: values.tolist() for name, values in arrays.items()},
            )

    return app

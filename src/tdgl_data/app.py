import asyncio
import json
from typing import Annotated

import numpy as np
from fastapi import FastAPI, HTTPException, status
from fastapi import Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sse_starlette.sse import EventSourceResponse
from sqlalchemy.exc import IntegrityError

from tdgl_data.config import Settings
from tdgl_data.db import create_engine_from_url, create_session_factory
from tdgl_data.events import FrameAvailableEvent, RunCompletedEvent, bus
from tdgl_data.models import Base, Frame, Run
from tdgl_data.zarr_store import ZarrStore
from tdgl_data.repository import (
    append_frame_record,
    create_run,
    delete_frame_record,
    delete_run,
    get_available_frame_metadata,
    get_available_iv_points,
    get_frame,
    get_iv_points,
    get_run,
    get_timeline,
    list_runs,
    update_run_status,
)
from tdgl_data.schemas import (
    CreateRunRequest,
    FrameAppendRequest,
    FrameMetadataResponse,
    FrameResponse,
    IVPointResponse,
    RunResponse,
    TimelineResponse,
    UpdateRunStatusRequest,
)


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


def _validate_frame_arrays(body: FrameAppendRequest, grid_shape: tuple[int, int]) -> dict[str, list[list[float]]]:
    arrays: dict[str, list[list[float]]] = {}
    for field in ("psi_real", "psi_imag", "mu"):
        value = getattr(body, field)
        try:
            array = np.asarray(value, dtype="float32")
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"{field} must be a rectangular 2D array",
            ) from exc
        if array.ndim != 2:
            raise HTTPException(status_code=422, detail=f"{field} must be a rectangular 2D array")
        if array.shape != grid_shape:
            raise HTTPException(status_code=422, detail=f"{field} shape must match run grid_shape")
        arrays[field] = value
    return arrays


def _compute_frame_stats(arrays: dict[str, list[list[float]]]) -> dict[str, dict[str, float]]:
    stats: dict[str, dict[str, float]] = {}
    for name, values in arrays.items():
        values_array = np.asarray(values, dtype="float32")
        stats[name] = {"min": float(np.min(values_array)), "max": float(np.max(values_array))}
    return stats


def _update_stats(
    stats: dict[str, dict[str, float]],
    frame_stats: dict[str, dict[str, float]],
) -> None:
    for name, entry in frame_stats.items():
        aggregate = stats.setdefault(name, {"min": float("inf"), "max": float("-inf")})
        aggregate["min"] = min(aggregate["min"], entry["min"])
        aggregate["max"] = max(aggregate["max"], entry["max"])


def create_app(
    *,
    database_url: str | None = None,
    zarr_root: str | None = None,
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

    app = FastAPI(title=settings.app_name)
    app.state.session_factory = session_factory
    app.state.event_bus = bus
    app.state.zarr_store = ZarrStore(zarr_root)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", include_in_schema=False)
    def api_root() -> RedirectResponse:
        return RedirectResponse("/viewer")

    @app.get("/viewer", response_class=HTMLResponse)
    def api_viewer() -> HTMLResponse:
        from pathlib import Path
        viewer_path = Path(__file__).with_name("static") / "viewer.html"
        if not viewer_path.exists():
            raise HTTPException(status_code=500, detail="Viewer asset not found")
        return HTMLResponse(viewer_path.read_text(encoding="utf-8"))

    @app.get("/api/runs/{run_id}/events")
    async def api_run_events(run_id: str) -> EventSourceResponse:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")

        async def event_generator():
            queue = app.state.event_bus.subscribe(run_id)
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                        if isinstance(event, FrameAvailableEvent):
                            data = json.dumps({
                                "run_id": event.run_id,
                                "frame_index": event.frame_index,
                                "time_value": event.time_value,
                                "je": event.je,
                                "voltage": event.voltage,
                                "frame_count": event.frame_count,
                            })
                            yield {"event": "frame_available", "data": data}
                        elif isinstance(event, RunCompletedEvent):
                            data = json.dumps({
                                "run_id": event.run_id,
                                "status": event.status,
                            })
                            yield {"event": "run_completed", "data": data}
                    except asyncio.TimeoutError:
                        yield {"comment": "keepalive"}
            finally:
                app.state.event_bus.unsubscribe(run_id, queue)

        return EventSourceResponse(event_generator())

    @app.post("/api/runs", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
    def api_create_run(body: CreateRunRequest) -> RunResponse:
        with session_factory() as session:
            run = create_run(
                session,
                solver_type=body.solver_type,
                grid_shape=body.grid_shape,
                device_params=body.device_params,
                timing_params=body.timing_params,
                metadata=body.metadata,
                git_commit=body.git_commit,
                image_tag=body.image_tag,
                total_frames=body.total_frames,
            )
            app.state.zarr_store.create_run(run.run_id, tuple(body.grid_shape))
            session.commit()
            session.refresh(run)
        return _run_response(run)

    @app.patch("/api/runs/{run_id}/status", response_model=RunResponse)
    def api_update_run_status(run_id: str, body: UpdateRunStatusRequest) -> RunResponse:
        with session_factory() as session:
            try:
                run = update_run_status(session, run_id, body.status)
            except LookupError:
                raise HTTPException(status_code=404, detail="Run not found") from None
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

    @app.delete("/api/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def api_delete_run(run_id: str) -> Response:
        with session_factory() as session:
            run = get_run(session, run_id)
            if run is None:
                raise HTTPException(status_code=404, detail="Run not found")
            delete_run(session, run)
            session.commit()
        app.state.zarr_store.delete_run(run_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

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

            arrays = _validate_frame_arrays(body, _grid_shape(run))
            stats = _compute_frame_stats(arrays)
            np_arrays = {k: np.asarray(v, dtype="float32") for k, v in arrays.items()}
            app.state.zarr_store.append_frame(run_id, body.frame_index, np_arrays)
            try:
                frame = append_frame_record(
                    session,
                    run_id=run_id,
                    frame_index=body.frame_index,
                    time_value=body.time_value,
                    je=body.je,
                    voltage=body.voltage,
                    psi_real=arrays["psi_real"],
                    psi_imag=arrays["psi_imag"],
                    mu=arrays["mu"],
                    frame_stats=stats,
                )
                frame.zarr_exists = True
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

    @app.get("/api/runs/{run_id}/timeline", response_model=TimelineResponse)
    def api_timeline(run_id: str) -> TimelineResponse:
        with session_factory() as session:
            if get_run(session, run_id) is None:
                raise HTTPException(status_code=404, detail="Run not found")
            rows = get_available_frame_metadata(session, run_id)

            stats: dict[str, dict[str, float]] = {}
            frame_responses = []
            for fi, tv, je, volt, st, fs in rows:
                if fs:
                    _update_stats(stats, fs)
                frame_responses.append(FrameMetadataResponse(
                    frame_index=fi, time_value=tv, je=je, voltage=volt, status=st,
                ))

        return TimelineResponse(
            run_id=run_id,
            frames=frame_responses,
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
                for point in get_available_iv_points(session, run_id)
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
            if frame.zarr_exists:
                zarr_arrays = app.state.zarr_store.get_frame(run_id, frame_index)
                arrays = {k: v.tolist() for k, v in zarr_arrays.items()}
            else:
                arrays = {
                    "psi_real": frame.psi_real,
                    "psi_imag": frame.psi_imag,
                    "mu": frame.mu,
                }
            return FrameResponse(
                run_id=run_id,
                frame_index=frame_index,
                time_value=frame.time_value,
                je=frame.je,
                voltage=frame.voltage,
                arrays=arrays,
            )

    return app
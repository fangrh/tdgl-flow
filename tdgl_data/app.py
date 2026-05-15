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
            run.zarr_root = zarr_store.create_run_store(
                run.run_id,
                grid_shape=body.grid_shape,
                fields=("psi_real", "psi_imag", "mu"),
            )
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

"""viewer-manager FastAPI application."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy import select

from viewer_manager.cleanup import cleanup_loop
from viewer_manager.config import Settings
from viewer_manager.db import create_engine_from_url, create_session_factory, session_scope
from viewer_manager.k8s_client import (
    create_viewer_pod,
    get_pod_failure_reason,
    is_pod_failed,
    is_pod_ready,
)
from viewer_manager.models import Base, ViewerSession
from viewer_manager.proxy import proxy_to_viewer
from viewer_manager.schemas import (
    CreateSessionRequest,
    SessionListResponse,
    SessionResponse,
)

logger = logging.getLogger(__name__)


def _session_response(vs: ViewerSession) -> SessionResponse:
    return SessionResponse(
        session_id=vs.session_id,
        run_id=vs.run_id,
        viewer_type=vs.viewer_type,
        status=vs.status,
        session_url=vs.session_url,
        active_clients=vs.active_clients,
        error_message=vs.error_message,
    )


def create_app(*, database_url: str | None = None, create_schema: bool = False, start_cleanup: bool = True) -> FastAPI:
    settings = Settings()
    if database_url is None:
        database_url = settings.database_url

    engine = create_engine_from_url(database_url)
    if create_schema:
        Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)

    app = FastAPI(title="Viewer Manager")
    app.state.session_factory = session_factory
    app.state.settings = settings
    app.state.http_client = httpx.AsyncClient(timeout=30.0)

    @app.on_event("startup")
    async def startup_event():
        if start_cleanup:
            import asyncio
            asyncio.create_task(cleanup_loop(session_factory, settings))
        else:
            logger.info("Cleanup task disabled (start_cleanup=False)")

    @app.on_event("shutdown")
    async def shutdown_client():
        await app.state.http_client.aclose()

    @app.post("/api/viewer-sessions", response_model=SessionResponse)
    def create_session(body: CreateSessionRequest) -> SessionResponse:
        with session_scope(session_factory) as db:
            existing = db.execute(
                select(ViewerSession).where(
                    ViewerSession.run_id == body.run_id,
                    ViewerSession.viewer_type == body.viewer_type,
                    ViewerSession.status.in_(["READY", "STARTING"]),
                )
            ).scalar_one_or_none()

            if existing is not None:
                existing.active_clients += 1
                existing.last_accessed_at = datetime.now(UTC)
                db.commit()
                db.refresh(existing)
                return _session_response(existing)

            session_id = str(uuid4())
            vs = ViewerSession(
                session_id=session_id,
                run_id=body.run_id,
                viewer_type=body.viewer_type,
                status="STARTING",
                active_clients=1,
                last_accessed_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(minutes=settings.session_idle_ttl_minutes),
            )

            try:
                pod = create_viewer_pod(
                    session_id=session_id,
                    run_id=body.run_id,
                    viewer_type=body.viewer_type,
                    image=settings.viewer_image,
                    namespace=settings.k8s_namespace,
                )
                vs.pod_name = pod.metadata.name
                vs.service_name = f"viewer-{session_id[:12]}"
            except Exception as e:
                logger.exception("Failed to create viewer pod")
                vs.status = "FAILED"
                vs.error_message = str(e)
                db.add(vs)
                db.commit()
                raise HTTPException(status_code=500, detail=f"Failed to create pod: {e}") from e

            base = settings.base_url.rstrip("/")
            vs.session_url = f"{base}/viewer-session/{session_id}/"
            db.add(vs)
            db.commit()
            db.refresh(vs)
            return _session_response(vs)

    @app.get("/api/viewer-sessions", response_model=SessionListResponse)
    def list_sessions() -> SessionListResponse:
        with session_scope(session_factory) as db:
            sessions = db.execute(
                select(ViewerSession).where(
                    ViewerSession.status.in_(["READY", "STARTING", "PENDING"])
                )
            ).scalars().all()
            return SessionListResponse(sessions=[_session_response(s) for s in sessions])

    @app.get("/api/viewer-sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str) -> SessionResponse:
        with session_scope(session_factory) as db:
            vs = db.get(ViewerSession, session_id)
            if vs is None:
                raise HTTPException(status_code=404, detail="Session not found")

            if vs.status == "STARTING":
                if is_pod_ready(session_id, settings.k8s_namespace):
                    vs.status = "READY"
                    db.commit()
                    db.refresh(vs)
                elif is_pod_failed(session_id, settings.k8s_namespace):
                    vs.status = "FAILED"
                    vs.error_message = get_pod_failure_reason(session_id, settings.k8s_namespace)
                    db.commit()
                    db.refresh(vs)

            return _session_response(vs)

    @app.post("/api/viewer-sessions/{session_id}/heartbeat")
    def heartbeat(session_id: str) -> SessionResponse:
        with session_scope(session_factory) as db:
            vs = db.get(ViewerSession, session_id)
            if vs is None:
                raise HTTPException(status_code=404, detail="Session not found")
            vs.last_accessed_at = datetime.now(UTC)
            db.commit()
            db.refresh(vs)
            return _session_response(vs)

    @app.post("/api/viewer-sessions/{session_id}/release")
    def release(session_id: str) -> SessionResponse:
        with session_scope(session_factory) as db:
            vs = db.get(ViewerSession, session_id)
            if vs is None:
                raise HTTPException(status_code=404, detail="Session not found")
            vs.active_clients = max(0, vs.active_clients - 1)
            vs.last_accessed_at = datetime.now(UTC)
            db.commit()
            db.refresh(vs)
            return _session_response(vs)

    @app.delete("/api/viewer-sessions/{session_id}")
    def delete_session(session_id: str) -> Response:
        with session_scope(session_factory) as db:
            vs = db.get(ViewerSession, session_id)
            if vs is None:
                raise HTTPException(status_code=404, detail="Session not found")
            if vs.pod_name:
                from viewer_manager.k8s_client import delete_viewer_pod
                delete_viewer_pod(session_id, settings.k8s_namespace)
            vs.status = "CLEANED"
            db.commit()
        return Response(status_code=204)

    @app.api_route("/viewer-session/{session_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
    async def proxy_route(session_id: str, path: str, request: Request) -> Response:
        return await proxy_to_viewer(session_id, path, request)

    return app

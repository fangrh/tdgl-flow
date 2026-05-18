# Viewer Manager Backend (Sub-Project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create the viewer-manager microservice that manages on-demand viewer Pod lifecycle, session state, heartbeat cleanup, and database migrations.

**Architecture:** A new FastAPI service (`viewer-manager`) that talks to PostgreSQL for session state and the Kubernetes API for Pod/Service lifecycle. It also acts as a reverse proxy to route viewer-session traffic to the correct Pod. Alembic handles schema migrations triggered by an Argo CD Pre-Sync Hook.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, `kubernetes` Python client, `httpx` (reverse proxy), Alembic, Pydantic, Pytest

**Design spec:** `docs/superpowers/specs/2026-05-18-on-demand-viewer-architecture-design.md`

---

## File Structure

```
# New files
src/viewer_manager/
├── __init__.py                  # Package marker
├── config.py                    # Settings (pydantic-settings)
├── models.py                    # ViewerSession SQLAlchemy model
├── schemas.py                   # Pydantic request/response schemas
├── db.py                        # Engine + session factory (reuse pattern from tdgl_data)
├── k8s_client.py                # Kubernetes API wrapper (create/delete Pod+Service)
├── cleanup.py                   # Background cleanup task
├── proxy.py                     # Reverse proxy to viewer Pods
├── app.py                       # FastAPI app factory with all routes
└── dev_app.py                   # Dev entry point (create_schema=True)
alembic.ini                      # Alembic config
alembic/
├── env.py                       # Alembic env (imports models)
├── script.py.mako               # Migration template
└── versions/
    └── 001_initial.py           # Initial migration (viewer_sessions + missing runs columns)
services/viewer-manager/
├── Dockerfile                   # Container build
└── k8s/
    ├── deployment.yaml          # viewer-manager deployment
    ├── service.yaml             # viewer-manager service
    ├── kustomization.yaml       # Kustomize config
    ├── role.yaml                # RBAC role for K8s API access
    ├── rolebinding.yaml         # Bind role to viewer-manager SA
    └── migrate-job.yaml         # Argo CD Pre-Sync Hook for migrations

# Modified files
services/kustomization.yaml      # Add viewer-manager/k8s/
infra/nginx/configmap.yaml       # Add /viewer-session/ route
services/data-viewer/k8s/kustomization.yaml  # Remove deployment.yaml, keep pvc+secret
```

---

### Task 1: Create viewer_manager package skeleton

**Files:**
- Create: `src/viewer_manager/__init__.py`
- Create: `src/viewer_manager/config.py`

- [ ] **Step 1: Create the package directory**

```bash
mkdir -p src/viewer_manager
```

- [ ] **Step 2: Create `__init__.py`**

Create `src/viewer_manager/__init__.py`:

```python
```

(Empty file — package marker only.)

- [ ] **Step 3: Create `config.py`**

Create `src/viewer_manager/config.py`:

```python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite+pysqlite:///:memory:"
    viewer_image: str = "ghcr.io/fangrh/tdgl-data-viewer:latest"
    k8s_namespace: str = "tdgl"
    session_idle_ttl_minutes: int = 15
    failed_cleanup_minutes: int = 10
    cleanup_interval_seconds: int = 60
    base_url: str = ""

    model_config = SettingsConfigDict(
        env_prefix="VIEWER_MANAGER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
```

- [ ] **Step 4: Commit**

```bash
git add src/viewer_manager/
git commit -m "feat: create viewer_manager package with config"
```

---

### Task 2: Create database model and schemas

**Files:**
- Create: `src/viewer_manager/models.py`
- Create: `src/viewer_manager/schemas.py`
- Create: `src/viewer_manager/db.py`

- [ ] **Step 1: Create `models.py`**

Create `src/viewer_manager/models.py`:

```python
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(UTC)


json_type = JSON().with_variant(JSONB, "postgresql")


class Base(DeclarativeBase):
    pass


class ViewerSession(Base):
    __tablename__ = "viewer_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    viewer_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    pod_name: Mapped[str | None] = mapped_column(String(128))
    service_name: Mapped[str | None] = mapped_column(String(128))
    session_url: Mapped[str | None] = mapped_column(String(512))
    active_clients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(1024))
```

- [ ] **Step 2: Create `schemas.py`**

Create `src/viewer_manager/schemas.py`:

```python
from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    run_id: str
    viewer_type: str = "data-viewer"


class SessionResponse(BaseModel):
    session_id: str
    run_id: str
    viewer_type: str
    status: str
    session_url: str | None = None
    active_clients: int = 0
    error_message: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionResponse]
```

- [ ] **Step 3: Create `db.py`**

Create `src/viewer_manager/db.py`:

```python
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine_from_url(database_url: str) -> Engine:
    return create_engine(database_url, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
```

- [ ] **Step 4: Commit**

```bash
git add src/viewer_manager/models.py src/viewer_manager/schemas.py src/viewer_manager/db.py
git commit -m "feat: add viewer_manager database model, schemas, and db module"
```

---

### Task 3: Create K8s client wrapper

**Files:**
- Create: `src/viewer_manager/k8s_client.py`

- [ ] **Step 1: Create `k8s_client.py`**

Create `src/viewer_manager/k8s_client.py`:

```python
"""Kubernetes API wrapper for managing viewer Pod/Service lifecycle."""

import logging

from kubernetes import client, config
from kubernetes.client import V1DeleteOptions, V1Pod, V1Service

logger = logging.getLogger(__name__)


def _load_k8s_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


def create_viewer_pod(
    session_id: str,
    run_id: str,
    viewer_type: str,
    image: str,
    namespace: str,
) -> V1Pod:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    service_name = f"viewer-{session_id[:12]}"

    pod = core.create_namespaced_pod(
        namespace=namespace,
        body=client.V1Pod(
            api_version="v1",
            kind="Pod",
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "app": "viewer-session",
                    "viewer-type": viewer_type,
                    "session-id": session_id,
                },
            ),
            spec=client.V1PodSpec(
                containers=[
                    client.V1Container(
                        name="viewer",
                        image=image,
                        ports=[client.V1ContainerPort(container_port=8000)],
                        env=[
                            client.V1EnvVar(name="VIEWER_SESSION_ID", value=session_id),
                            client.V1EnvVar(name="RUN_ID", value=run_id),
                        ],
                        volume_mounts=[
                            client.V1VolumeMount(name="zarr-data", mount_path="/data/zarr"),
                        ],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="zarr-data",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name="zarr-data",
                        ),
                    )
                ],
                image_pull_secrets=[
                    client.V1LocalObjectReference(name="ghcr-secret"),
                ],
            ),
        ),
    )
    logger.info("Created pod %s in %s", pod_name, namespace)

    svc = core.create_namespaced_service(
        namespace=namespace,
        body=client.V1Service(
            api_version="v1",
            kind="Service",
            metadata=client.V1ObjectMeta(name=service_name),
            spec=client.V1ServiceSpec(
                selector={"session-id": session_id},
                ports=[client.V1ServicePort(port=80, target_port=8000)],
            ),
        ),
    )
    logger.info("Created service %s in %s", service_name, namespace)
    return pod


def delete_viewer_pod(session_id: str, namespace: str) -> None:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    service_name = f"viewer-{session_id[:12]}"

    for name, delete_fn in [
        (service_name, lambda: core.delete_namespaced_service(name, namespace)),
        (pod_name, lambda: core.delete_namespaced_pod(name, namespace, body=V1DeleteOptions())),
    ]:
        try:
            delete_fn()
            logger.info("Deleted %s", name)
        except client.ApiException as e:
            if e.status != 404:
                logger.warning("Failed to delete %s: %s", name, e)


def is_pod_ready(session_id: str, namespace: str) -> bool:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except client.ApiException:
        return False

    for cond in (pod.status.conditions or []):
        if cond.type == "Ready" and cond.status == "True":
            return True
    return False


def is_pod_failed(session_id: str, namespace: str) -> bool:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except client.ApiException:
        return True

    if pod.status.phase in ("Failed", "Unknown"):
        return True
    for cs in (pod.status.container_statuses or []):
        if cs.state and cs.state.terminated and cs.state.terminated.exit_code != 0:
            return True
    return False


def get_pod_failure_reason(session_id: str, namespace: str) -> str | None:
    _load_k8s_config()
    core = client.CoreV1Api()
    pod_name = f"viewer-{session_id[:12]}"
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except client.ApiException as e:
        return f"Pod not found: {e.reason}"

    if pod.status.message:
        return pod.status.message
    for cs in (pod.status.container_statuses or []):
        if cs.state and cs.state.terminated:
            return cs.state.terminated.message or f"exit code {cs.state.terminated.exit_code}"
    return None
```

- [ ] **Step 2: Commit**

```bash
git add src/viewer_manager/k8s_client.py
git commit -m "feat: add K8s client wrapper for viewer Pod/Service lifecycle"
```

---

### Task 4: Create background cleanup task

**Files:**
- Create: `src/viewer_manager/cleanup.py`

- [ ] **Step 1: Create `cleanup.py`**

Create `src/viewer_manager/cleanup.py`:

```python
"""Background task that cleans up expired and failed viewer sessions."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from viewer_manager.config import Settings
from viewer_manager.db import session_scope
from viewer_manager.k8s_client import delete_viewer_pod
from viewer_manager.models import ViewerSession

logger = logging.getLogger(__name__)


def cleanup_expired_sessions(session_factory, settings: Settings) -> int:
    """Mark expired sessions and delete their K8s resources. Returns count cleaned."""
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.session_idle_ttl_minutes)
    cleaned = 0

    with session_scope(session_factory) as session:
        expired = session.execute(
            select(ViewerSession).where(
                ViewerSession.active_clients == 0,
                ViewerSession.last_accessed_at < cutoff,
                ViewerSession.status.in_(["READY", "STARTING", "PENDING"]),
            )
        ).scalars().all()

        for vs in expired:
            logger.info("Expiring session %s (idle since %s)", vs.session_id, vs.last_accessed_at)
            vs.status = "EXPIRED"
            if vs.pod_name:
                delete_viewer_pod(vs.session_id, settings.k8s_namespace)
            vs.status = "CLEANED"
            cleaned += 1
        session.commit()

    return cleaned


def cleanup_failed_sessions(session_factory, settings: Settings) -> int:
    """Clean up sessions whose Pods have been failed for too long."""
    cutoff = datetime.now(UTC) - timedelta(minutes=settings.failed_cleanup_minutes)
    cleaned = 0

    with session_scope(session_factory) as session:
        failed = session.execute(
            select(ViewerSession).where(
                ViewerSession.status == "FAILED",
                ViewerSession.created_at < cutoff,
            )
        ).scalars().all()

        for vs in failed:
            logger.info("Cleaning failed session %s", vs.session_id)
            if vs.pod_name:
                delete_viewer_pod(vs.session_id, settings.k8s_namespace)
            vs.status = "CLEANED"
            cleaned += 1
        session.commit()

    return cleaned


async def cleanup_loop(session_factory, settings: Settings) -> None:
    """Async background loop that runs cleanup periodically."""
    while True:
        try:
            expired = cleanup_expired_sessions(session_factory, settings)
            failed = cleanup_failed_sessions(session_factory, settings)
            if expired or failed:
                logger.info("Cleanup: expired=%d, failed=%d", expired, failed)
        except Exception:
            logger.exception("Cleanup task error")
        await asyncio.sleep(settings.cleanup_interval_seconds)
```

- [ ] **Step 2: Commit**

```bash
git add src/viewer_manager/cleanup.py
git commit -m "feat: add background cleanup task for expired/failed sessions"
```

---

### Task 5: Create reverse proxy

**Files:**
- Create: `src/viewer_manager/proxy.py`

- [ ] **Step 1: Create `proxy.py`**

Create `src/viewer_manager/proxy.py`:

```python
"""Reverse proxy that routes /viewer-session/{sid}/* to the viewer Pod."""

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from viewer_manager.db import session_scope
from viewer_manager.models import ViewerSession

PROXY_HEADERS_TO_PASS = [
    "accept", "accept-encoding", "accept-language", "cache-control",
    "content-type", "cookie", "referer", "user-agent",
]


async def proxy_to_viewer(session_id: str, path: str, request: Request) -> Response:
    """Look up session, proxy request to the viewer Pod."""
    session_factory = request.app.state.session_factory
    settings = request.app.state.settings

    with session_scope(session_factory) as db:
        vs = db.get(ViewerSession, session_id)
        if vs is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if vs.status != "READY":
            raise HTTPException(status_code=503, detail=f"Session status: {vs.status}")
        service_name = vs.service_name

    target_url = f"http://{service_name}.{settings.k8s_namespace}.svc.cluster.local/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    headers = {}
    for h in PROXY_HEADERS_TO_PASS:
        if h in request.headers:
            headers[h] = request.headers[h]

    body = await request.body()
    client = request.app.state.http_client

    resp = await client.request(
        method=request.method,
        url=target_url,
        headers=headers,
        content=body,
    )

    response_headers = dict(resp.headers)
    response_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response_headers["X-Frame-Options"] = "SAMEORIGIN"

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={
            k: v for k, v in response_headers.items()
            if k.lower() not in ("transfer-encoding", "content-encoding", "content-length")
        },
    )
```

- [ ] **Step 2: Commit**

```bash
git add src/viewer_manager/proxy.py
git commit -m "feat: add reverse proxy for routing to viewer Pods"
```

---

### Task 6: Create FastAPI app with all routes

**Files:**
- Create: `src/viewer_manager/app.py`
- Create: `src/viewer_manager/dev_app.py`

- [ ] **Step 1: Create `app.py`**

Create `src/viewer_manager/app.py`:

```python
"""viewer-manager FastAPI application."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy import select

from viewer_manager.cleanup import cleanup_loop
from viewer_manager.config import Settings
from viewer_manager.db import create_engine_from_url, create_session_factory
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


def create_app(*, database_url: str | None = None, create_schema: bool = False) -> FastAPI:
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
    async def start_cleanup():
        import asyncio
        asyncio.create_task(cleanup_loop(session_factory, settings))

    @app.on_event("shutdown")
    async def shutdown_client():
        await app.state.http_client.aclose()

    @app.post("/api/viewer-sessions", response_model=SessionResponse)
    def create_session(body: CreateSessionRequest) -> SessionResponse:
        with session_factory() as db:
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
        with session_factory() as db:
            sessions = db.execute(
                select(ViewerSession).where(
                    ViewerSession.status.in_(["READY", "STARTING", "PENDING"])
                )
            ).scalars().all()
            return SessionListResponse(sessions=[_session_response(s) for s in sessions])

    @app.get("/api/viewer-sessions/{session_id}", response_model=SessionResponse)
    def get_session(session_id: str) -> SessionResponse:
        with session_factory() as db:
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
        with session_factory() as db:
            vs = db.get(ViewerSession, session_id)
            if vs is None:
                raise HTTPException(status_code=404, detail="Session not found")
            vs.last_accessed_at = datetime.now(UTC)
            db.commit()
            db.refresh(vs)
            return _session_response(vs)

    @app.post("/api/viewer-sessions/{session_id}/release")
    def release(session_id: str) -> SessionResponse:
        with session_factory() as db:
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
        with session_factory() as db:
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
```

- [ ] **Step 2: Create `dev_app.py`**

Create `src/viewer_manager/dev_app.py`:

```python
from viewer_manager.app import create_app


def create_dev_app():
    return create_app(create_schema=True)
```

- [ ] **Step 3: Commit**

```bash
git add src/viewer_manager/app.py src/viewer_manager/dev_app.py
git commit -m "feat: add viewer-manager FastAPI app with session CRUD and proxy routes"
```

---

### Task 7: Register package in pyproject.toml

**Files:**
- Modify: `pyproject.toml` — add `kubernetes` dependency and `viewer_manager*` to package find

- [ ] **Step 1: Add kubernetes dependency to pyproject.toml**

In `pyproject.toml`, add `"kubernetes>=29.7"` to the `dependencies` list.

- [ ] **Step 2: Add viewer_manager to package discovery**

In `pyproject.toml`, update `include` in `[tool.setuptools.packages.find]` to also include `viewer_manager*`:

```toml
include = ["tdgl_data*", "tdgl_generator*", "tdgl_workflow*", "tdgl_sdk*", "viewer_manager*"]
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: register viewer_manager package and kubernetes dependency"
```

---

### Task 8: Create tests for viewer_manager

**Files:**
- Create: `tests/test_viewer_manager.py`

- [ ] **Step 1: Write tests**

Create `tests/test_viewer_manager.py`:

```python
"""Tests for viewer-manager session API (DB layer only, K8s mocked)."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from viewer_manager.app import create_app
from viewer_manager.models import Base
from viewer_manager.db import create_engine_from_url, create_session_factory


@pytest.fixture
def client():
    app = create_app(database_url="sqlite+pysqlite:///:memory:", create_schema=True)
    with TestClient(app) as c:
        yield c


def test_create_session_returns_starting(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-1", "viewer_type": "data-viewer"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "STARTING"
    assert data["run_id"] == "run-1"
    assert data["active_clients"] == 1
    assert data["session_url"] is not None


def test_reuse_existing_session(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))

        # Create first session
        resp1 = client.post("/api/viewer-sessions", json={"run_id": "run-1", "viewer_type": "data-viewer"})
        assert resp1.status_code == 200
        sid = resp1.json()["session_id"]

        # Manually set it to READY for reuse test
        sf = client.app.state.session_factory
        from viewer_manager.models import ViewerSession
        with sf() as db:
            vs = db.get(ViewerSession, sid)
            vs.status = "READY"
            db.commit()

        # Should reuse
        resp2 = client.post("/api/viewer-sessions", json={"run_id": "run-1", "viewer_type": "data-viewer"})
        assert resp2.status_code == 200
        assert resp2.json()["session_id"] == sid
        assert resp2.json()["active_clients"] == 2


def test_get_session_checks_pod_status(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-2", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    with patch("viewer_manager.app.is_pod_ready", return_value=True):
        resp = client.get(f"/api/viewer-sessions/{sid}")
    assert resp.json()["status"] == "READY"


def test_heartbeat_updates_access_time(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-3", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    resp = client.post(f"/api/viewer-sessions/{sid}/heartbeat")
    assert resp.status_code == 200


def test_release_decrements_clients(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-4", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    resp = client.post(f"/api/viewer-sessions/{sid}/release")
    assert resp.json()["active_clients"] == 0


def test_delete_session(client):
    with patch("viewer_manager.app.create_viewer_pod") as mock_pod:
        from kubernetes.client import V1Pod, V1ObjectMeta
        mock_pod.return_value = V1Pod(metadata=V1ObjectMeta(name="viewer-test123"))
        resp = client.post("/api/viewer-sessions", json={"run_id": "run-5", "viewer_type": "data-viewer"})
    sid = resp.json()["session_id"]

    with patch("viewer_manager.app.delete_viewer_pod"):
        resp = client.delete(f"/api/viewer-sessions/{sid}")
    assert resp.status_code == 204


def test_session_not_found(client):
    resp = client.get("/api/viewer-sessions/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests**

```bash
python -m pytest tests/test_viewer_manager.py -v
```

Expected: All 7 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_viewer_manager.py
git commit -m "test: add viewer-manager session API tests"
```

---

### Task 9: Create Alembic setup and initial migration

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/001_initial.py`

- [ ] **Step 1: Create `alembic.ini`**

Create `alembic.ini`:

```ini
[alembic]
script_location = alembic
sqlalchemy.url = sqlite+pysqlite:///:memory:

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

- [ ] **Step 2: Create `alembic/env.py`**

Create `alembic/env.py`:

```python
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from viewer_manager.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

database_url = os.environ.get("VIEWER_MANAGER_DATABASE_URL") or os.environ.get("TDGL_DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", database_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(config.get_main_option("sqlalchemy.url"))
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Create `alembic/script.py.mako`**

Create `alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 4: Create initial migration**

Create `alembic/versions/001_initial.py`:

```python
"""Initial schema: viewer_sessions + missing runs columns

Revision ID: 001
Revises: None
Create Date: 2026-05-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "viewer_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("viewer_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("pod_name", sa.String(128)),
        sa.Column("service_name", sa.String(128)),
        sa.Column("session_url", sa.String(512)),
        sa.Column("active_clients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.String(1024)),
    )

    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS mesh_sites JSONB")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS mesh_elements JSONB")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS n_sites INTEGER")
    op.execute("ALTER TABLE runs ADD COLUMN IF NOT EXISTS solver_options JSONB")


def downgrade() -> None:
    op.drop_table("viewer_sessions")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS solver_options")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS n_sites")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS mesh_elements")
    op.execute("ALTER TABLE runs DROP COLUMN IF EXISTS mesh_sites")
```

- [ ] **Step 5: Commit**

```bash
mkdir -p alembic/versions
git add alembic.ini alembic/ docs/superpowers/plans/2026-05-18-viewer-manager-backend.md
git commit -m "feat: add Alembic setup with initial migration (viewer_sessions + missing runs columns)"
```

---

### Task 10: Create viewer-manager Dockerfile and K8s manifests

**Files:**
- Create: `services/viewer-manager/Dockerfile`
- Create: `services/viewer-manager/k8s/deployment.yaml`
- Create: `services/viewer-manager/k8s/service.yaml`
- Create: `services/viewer-manager/k8s/kustomization.yaml`
- Create: `services/viewer-manager/k8s/role.yaml`
- Create: `services/viewer-manager/k8s/rolebinding.yaml`
- Create: `services/viewer-manager/k8s/migrate-job.yaml`

- [ ] **Step 1: Create directory**

```bash
mkdir -p services/viewer-manager/k8s
```

- [ ] **Step 2: Create Dockerfile**

Create `services/viewer-manager/Dockerfile`:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "viewer_manager.dev_app:create_dev_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Create K8s deployment**

Create `services/viewer-manager/k8s/deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: viewer-manager
  namespace: tdgl
spec:
  replicas: 1
  selector:
    matchLabels:
      app: viewer-manager
  template:
    metadata:
      labels:
        app: viewer-manager
    spec:
      serviceAccountName: viewer-manager
      containers:
        - name: viewer-manager
          image: ghcr.io/fangrh/viewer-manager:latest
          ports:
            - containerPort: 8000
          env:
            - name: VIEWER_MANAGER_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: data-viewer-config
                  key: TDGL_DATABASE_URL
            - name: VIEWER_MANAGER_VIEWER_IMAGE
              value: "ghcr.io/fangrh/tdgl-data-viewer:latest"
            - name: VIEWER_MANAGER_K8S_NAMESPACE
              value: "tdgl"
            - name: VIEWER_MANAGER_BASE_URL
              value: "http://gateway.tdgl.svc.cluster.local"
          livenessProbe:
            httpGet:
              path: /api/viewer-sessions
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/viewer-sessions
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "128Mi"
              cpu: "100m"
            limits:
              memory: "256Mi"
              cpu: "250m"
      imagePullSecrets:
        - name: ghcr-secret
```

- [ ] **Step 4: Create K8s service**

Create `services/viewer-manager/k8s/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: viewer-manager
  namespace: tdgl
spec:
  selector:
    app: viewer-manager
  ports:
    - port: 80
      targetPort: 8000
```

- [ ] **Step 5: Create RBAC role**

Create `services/viewer-manager/k8s/role.yaml`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: viewer-manager
  namespace: tdgl
rules:
  - apiGroups: [""]
    resources: ["pods", "services"]
    verbs: ["create", "delete", "get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]
```

- [ ] **Step 6: Create RBAC role binding**

Create `services/viewer-manager/k8s/rolebinding.yaml`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: viewer-manager
  namespace: tdgl
subjects:
  - kind: ServiceAccount
    name: viewer-manager
    namespace: tdgl
roleRef:
  kind: Role
  name: viewer-manager
  apiGroup: rbac.authorization.k8s.io
```

- [ ] **Step 7: Create migration pre-sync hook**

Create `services/viewer-manager/k8s/migrate-job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: db-migrate
  namespace: tdgl
  annotations:
    argocd.argoproj.io/hook: PreSync
    argocd.argoproj.io/hook-delete-policy: HookSucceeded
spec:
  template:
    spec:
      containers:
        - name: migrate
          image: ghcr.io/fangrh/viewer-manager:latest
          command: ["alembic", "upgrade", "head"]
          env:
            - name: VIEWER_MANAGER_DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: data-viewer-config
                  key: TDGL_DATABASE_URL
      restartPolicy: Never
  backoffLimit: 3
```

- [ ] **Step 8: Create kustomization**

Create `services/viewer-manager/k8s/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - role.yaml
  - rolebinding.yaml
  - deployment.yaml
  - service.yaml
  - migrate-job.yaml
```

- [ ] **Step 9: Commit**

```bash
git add services/viewer-manager/
git commit -m "feat: add viewer-manager Dockerfile and K8s manifests (deployment, RBAC, migration hook)"
```

---

### Task 11: Update services kustomization and nginx config

**Files:**
- Modify: `services/kustomization.yaml`
- Modify: `infra/nginx/configmap.yaml`

- [ ] **Step 1: Add viewer-manager to services kustomization**

In `services/kustomization.yaml`, add `- viewer-manager/k8s/` to the resources list:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - data-viewer/k8s/
  - generator/k8s/
  - tdgl-workflow/k8s/
  - viewer-manager/k8s/
```

- [ ] **Step 2: Add viewer-session route to nginx**

In `infra/nginx/configmap.yaml`, add a new location block inside the `server { ... }` block, after the existing `/workflow/` location:

```nginx
        location /viewer-session/ {
            proxy_pass http://viewer-manager.tdgl.svc.cluster.local/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
        }
```

- [ ] **Step 3: Commit**

```bash
git add services/kustomization.yaml infra/nginx/configmap.yaml
git commit -m "feat: add viewer-manager to kustomization and nginx routing"
```

---

### Task 12: Update CLAUDE.md with viewer-manager rules

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add viewer-manager to CLAUDE.md**

Add the following section to `CLAUDE.md` before the `## Dev Mode` section:

```markdown
## Viewer Manager

The `viewer-manager` service manages on-demand viewer Pods. Viewer sessions are temporary — created when users click View, cleaned up after idle timeout.

### Architecture
- `viewer-manager` creates/deletes viewer Pods via Kubernetes API
- Each session gets a unique `session_id` and a URL at `/viewer-session/{sid}/`
- Sessions are reused when the same run_id + viewer_type is requested
- Background task cleans up idle (15min) and failed (10min) sessions

### When modifying viewer-manager
- All DB schema changes must go through Alembic: `alembic revision --autogenerate -m "description"`
- Migration runs via Argo CD Pre-Sync Hook before each sync
- Tests mock K8s API — run with `pytest tests/test_viewer_manager.py -v`
- The `kubernetes` Python client reads in-cluster config by default
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with viewer-manager architecture and rules"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| New viewer-manager microservice | Tasks 1-6, 10 |
| Session CRUD API (POST/GET/DELETE/heartbeat/release) | Task 6 |
| K8s Pod/Service lifecycle (create/delete) | Task 3 |
| Session state machine (PENDING→STARTING→READY→EXPIRED→CLEANED) | Tasks 2, 6 |
| Heartbeat mechanism (update last_accessed_at) | Task 6 |
| Background cleanup (expired + failed) | Task 4 |
| Session reuse (same run_id + viewer_type) | Task 6 |
| Reverse proxy to viewer Pods | Task 5 |
| Alembic migration + Pre-Sync Hook | Tasks 9, 10 |
| RBAC for K8s API access | Task 10 |
| Nginx routing | Task 11 |
| CLAUDE.md updates | Task 12 |
| Immediate CrashLoopBackOff fix | Task 9 (migration adds missing columns) |
| Tests | Task 8 |

### Placeholder scan
No TBDs, TODOs, or incomplete steps. All code blocks contain complete, runnable content.

### Type consistency
- `ViewerSession` model fields match across `models.py`, `schemas.py`, `app.py`, and `k8s_client.py`
- `session_id` is `String(36)` (UUID length) everywhere
- `pod_name` uses `viewer-{session_id[:12]}` consistently in `k8s_client.py` and referenced in `app.py`
- `create_viewer_pod()` signature matches call site in `app.py`

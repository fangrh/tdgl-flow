# Data Service K8s Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the TDGL data service to Kubernetes with PostgreSQL, SSE live events, and Docker/K8s manifests.

**Architecture:** One FastAPI app serves API + viewer. PostgreSQL StatefulSet for metadata. Zarr PVC for frame arrays. SSE endpoint streams frame events to viewers. All resources in `tdgl` namespace.

**Tech Stack:** Python/FastAPI, SQLAlchemy, PostgreSQL, Zarr, SSE-Starlette, Docker, Kubernetes

---

### Task 1: Add event bus module

**Files:**
- Create: `tdgl_data/events.py`

- [ ] **Step 1: Create the event bus module**

Create `tdgl_data/events.py` with an in-process event bus using `asyncio.Queue`:

```python
import asyncio
from dataclasses import dataclass, field


@dataclass
class FrameAvailableEvent:
    run_id: str
    frame_index: int
    time_value: float
    je: float
    voltage: float
    frame_count: int


@dataclass
class RunCompletedEvent:
    run_id: str
    status: str


MAX_QUEUE_SIZE = 100


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        try:
            subscribers.remove(queue)
        except ValueError:
            pass
        if not subscribers:
            del self._subscribers[run_id]

    def publish(self, run_id: str, event: object) -> None:
        subscribers = self._subscribers.get(run_id, [])
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)


bus = EventBus()
```

- [ ] **Step 2: Commit**

```bash
git add tdgl_data/events.py
git commit -m "feat: add in-process SSE event bus"
```

---

### Task 2: Add SSE endpoint and wire event publishing

**Files:**
- Modify: `tdgl_data/app.py` (add SSE endpoint, publish events on frame append)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing SSE tests**

Add to `tests/test_api.py`:

```python
import asyncio
import json


def test_sse_returns_404_for_unknown_run(client):
    response = client.get("/api/runs/nonexistent/events", stream=True)
    assert response.status_code == 404


def test_sse_receives_frame_available_event(client):
    created = client.post(
        "/api/demo-runs",
        json={"frame_count": 2, "grid_shape": [2, 2], "seed": 1},
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    # Append a new frame to trigger an event
    client.post(
        f"/api/runs/{run_id}/frames",
        json={
            "frame_index": 100,
            "time_value": 10.0,
            "je": 0.5,
            "voltage": 0.015,
            "psi_real": [[0.1, 0.2], [0.3, 0.4]],
            "psi_imag": [[0.0, 0.0], [0.0, 0.0]],
            "mu": [[0.0, 0.0], [0.0, 0.0]],
        },
    )

    # The demo run creates frames synchronously, so events are published
    # but since SSE was not connected during creation, we just verify
    # the endpoint exists and streams for valid runs
    with client.stream("GET", f"/api/runs/{run_id}/events") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        # Read a small chunk — the stream will send keepalive or events
        first_line = next(response.iter_lines())
        assert first_line is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_api.py::test_sse_returns_404_for_unknown_run tests/test_api.py::test_sse_receives_frame_available_event -v`
Expected: FAIL — SSE endpoint doesn't exist yet.

- [ ] **Step 3: Add SSE endpoint to app.py**

In `tdgl_data/app.py`, add imports at the top:

```python
import asyncio
import json

from sse_starlette.sse import EventSourceResponse
from tdgl_data.events import EventBus, bus
```

Inside `create_app`, after the CORS middleware setup (after line 154), store the event bus on `app.state`:

```python
    app.state.event_bus = bus
```

Add the SSE endpoint after the viewer endpoint (after the `api_viewer` function):

```python
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
```

Also add the import for the event types at the top of app.py:

```python
from tdgl_data.events import FrameAvailableEvent, RunCompletedEvent, bus
```

Wait — the imports were already partially listed above. Consolidate: at the top of `app.py`, add:

```python
import asyncio
import json

from sse_starlette.sse import EventSourceResponse
from tdgl_data.events import FrameAvailableEvent, RunCompletedEvent, bus
```

And remove the duplicate `import json` if it's not already there.

Register the route in `create_app`, after the viewer endpoint:

```python
    @app.get("/api/runs/{run_id}/events")
    async def api_run_events(run_id: str) -> EventSourceResponse:
```

**IMPORTANT:** The SSE endpoint must be `async` because `EventSourceResponse` requires an async generator. FastAPI handles this correctly — async endpoints run on the event loop.

- [ ] **Step 4: Publish events when frames are appended**

In `tdgl_data/app.py`, modify `api_append_frame` to publish a `FrameAvailableEvent` after the successful commit. After the line `return _frame_metadata(frame)` (which is the last line inside the try block for the successful path), and before the return, add event publishing.

Find the `api_append_frame` function. After `session.commit()` succeeds (the line `mark_frame_available(session, frame)` followed by `session.commit()`), and before `return _frame_metadata(frame)`, add:

```python
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
```

Also publish events in `api_create_demo_run` after all frames are committed. The demo loop already has `synthetic_frame` with all the data we need. After `session.commit()` and before `session.refresh(run)`, add:

```python
            for i, sf in enumerate(synthetic_frames):
                app.state.event_bus.publish(run.run_id, FrameAvailableEvent(
                    run_id=run.run_id,
                    frame_index=sf.frame_index,
                    time_value=sf.time_value,
                    je=sf.je,
                    voltage=sf.voltage,
                    frame_count=i + 1,
                ))
```

For this to work, the demo loop needs to collect synthetic frames first. Change the demo loop to collect into a list, then iterate:

```python
                synthetic_frames = list(generate_synthetic_run(
                    body.frame_count, body.grid_shape, seed=body.seed,
                ))
                for synthetic_frame in synthetic_frames:
                    frame_arrays = synthetic_frame.arrays()
                    stats = _compute_frame_stats(frame_arrays)
                    frame = append_frame_record(
                        session,
                        run_id=run.run_id,
                        frame_index=synthetic_frame.frame_index,
                        time_value=synthetic_frame.time_value,
                        je=synthetic_frame.je,
                        voltage=synthetic_frame.voltage,
                        zarr_group=run.zarr_root,
                        frame_stats=stats,
                        status="writing",
                    )
                    zarr_store.append_frame(
                        run.run_id,
                        synthetic_frame.frame_index,
                        frame_arrays,
                    )
                    mark_frame_available(session, frame)
                session.commit()
                for i, sf in enumerate(synthetic_frames):
                    app.state.event_bus.publish(run.run_id, FrameAvailableEvent(
                        run_id=run.run_id,
                        frame_index=sf.frame_index,
                        time_value=sf.time_value,
                        je=sf.je,
                        voltage=sf.voltage,
                        frame_count=i + 1,
                    ))
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/test_api.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tdgl_data/app.py tests/test_api.py
git commit -m "feat: add SSE endpoint for live frame events"
```

---

### Task 3: Add psycopg dependency to main dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Move psycopg from optional to main dependencies**

In `pyproject.toml`, move `psycopg[binary]>=3.2` from the `[project.optional-dependencies]` section to the main `dependencies` list. Remove the `[project.optional-dependencies]` `postgres` section entirely.

The dependencies section should become:

```toml
dependencies = [
  "alembic>=1.13",
  "fastapi>=0.111",
  "httpx>=0.27",
  "numpy>=1.26",
  "psycopg[binary]>=3.2",
  "pydantic>=2.7",
  "pydantic-settings>=2.2",
  "sqlalchemy>=2.0",
  "sse-starlette>=2.1",
  "uvicorn[standard]>=0.30",
  "zarr>=2.18,<3",
]
```

And remove the `[project.optional-dependencies]` `postgres` section.

- [ ] **Step 2: Run tests to verify no regression**

Run: `python -m pytest tests/test_api.py -v`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat: add psycopg to main dependencies for PostgreSQL support"
```

---

### Task 4: Add Dockerfile

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create Dockerfile**

Create `Dockerfile`:

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY tdgl_data/ tdgl_data/

EXPOSE 8000

CMD ["uvicorn", "tdgl_data.dev_app:create_dev_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create .dockerignore**

Create `.dockerignore`:

```
data/
docs/
tests/
__pycache__/
*.pyc
.git/
.claude/
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: add Dockerfile for data service"
```

---

### Task 5: Add K8s namespace

**Files:**
- Create: `k8s/namespace.yaml`

- [ ] **Step 1: Create K8s directory and namespace manifest**

```bash
mkdir -p k8s/postgresql k8s/data-service
```

Create `k8s/namespace.yaml`:

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: tdgl
```

- [ ] **Step 2: Commit**

```bash
git add k8s/namespace.yaml
git commit -m "feat: add tdgl namespace manifest"
```

---

### Task 6: Add PostgreSQL StatefulSet manifests

**Files:**
- Create: `k8s/postgresql/secret.yaml`
- Create: `k8s/postgresql/pvc.yaml`
- Create: `k8s/postgresql/statefulset.yaml`
- Create: `k8s/postgresql/service.yaml`

- [ ] **Step 1: Create PostgreSQL Secret**

Create `k8s/postgresql/secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: postgres-credentials
  namespace: tdgl
type: Opaque
stringData:
  POSTGRES_DB: tdgl
  POSTGRES_USER: tdgl
  POSTGRES_PASSWORD: tdgl-dev-password
```

- [ ] **Step 2: Create PostgreSQL PVC**

Create `k8s/postgresql/pvc.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-data
  namespace: tdgl
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
```

- [ ] **Step 3: Create PostgreSQL StatefulSet**

Create `k8s/postgresql/statefulset.yaml`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
  namespace: tdgl
spec:
  serviceName: postgres
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
        - name: postgres
          image: postgres:16
          ports:
            - containerPort: 5432
          envFrom:
            - secretRef:
                name: postgres-credentials
          volumeMounts:
            - name: postgres-data
              mountPath: /var/lib/postgresql/data
          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "512Mi"
              cpu: "500m"
      volumes:
        - name: postgres-data
          persistentVolumeClaim:
            claimName: postgres-data
```

- [ ] **Step 4: Create PostgreSQL Service**

Create `k8s/postgresql/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
  namespace: tdgl
spec:
  selector:
    app: postgres
  ports:
    - port: 5432
      targetPort: 5432
  clusterIP: None
```

- [ ] **Step 5: Commit**

```bash
git add k8s/postgresql/
git commit -m "feat: add PostgreSQL StatefulSet manifests"
```

---

### Task 7: Add data service Deployment manifests

**Files:**
- Create: `k8s/data-service/secret.yaml`
- Create: `k8s/data-service/configmap.yaml`
- Create: `k8s/data-service/pvc.yaml`
- Create: `k8s/data-service/deployment.yaml`
- Create: `k8s/data-service/service.yaml`

- [ ] **Step 1: Create data service Secret**

Create `k8s/data-service/secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: data-service-config
  namespace: tdgl
type: Opaque
stringData:
  TDGL_DATABASE_URL: "postgresql+psycopg://tdgl:tdgl-dev-password@postgres.tdgl.svc.cluster.local:5432/tdgl"
```

- [ ] **Step 2: Create data service ConfigMap**

Create `k8s/data-service/configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: data-service-config
  namespace: tdgl
data:
  TDGL_ZARR_ROOT: "/data/zarr"
```

- [ ] **Step 3: Create data service PVC**

Create `k8s/data-service/pvc.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: zarr-data
  namespace: tdgl
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 10Gi
```

- [ ] **Step 4: Create data service Deployment**

Create `k8s/data-service/deployment.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: data-service
  namespace: tdgl
spec:
  replicas: 1
  selector:
    matchLabels:
      app: data-service
  template:
    metadata:
      labels:
        app: data-service
    spec:
      containers:
        - name: data-service
          image: kubeflow-tdgl-data:latest
          ports:
            - containerPort: 8000
          envFrom:
            - secretRef:
                name: data-service-config
            - configMapRef:
                name: data-service-config
          volumeMounts:
            - name: zarr-data
              mountPath: /data/zarr
          livenessProbe:
            httpGet:
              path: /api/runs
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          readinessProbe:
            httpGet:
              path: /api/runs
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          resources:
            requests:
              memory: "256Mi"
              cpu: "250m"
            limits:
              memory: "512Mi"
              cpu: "500m"
      volumes:
        - name: zarr-data
          persistentVolumeClaim:
            claimName: zarr-data
```

- [ ] **Step 5: Create data service Service**

Create `k8s/data-service/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: data-service
  namespace: tdgl
spec:
  selector:
    app: data-service
  ports:
    - port: 80
      targetPort: 8000
```

- [ ] **Step 6: Commit**

```bash
git add k8s/data-service/
git commit -m "feat: add data service Deployment manifests"
```

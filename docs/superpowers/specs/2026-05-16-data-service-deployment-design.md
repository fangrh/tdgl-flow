# Data Service Kubernetes Deployment Design (Sub-project 1)

## Goal

Deploy the existing TDGL data service to Kubernetes with PostgreSQL, SSE live
events, and proper K8s manifests. This is the first of three sub-projects; the
data generator and live viewer will follow.

## Architecture

One FastAPI application serves the REST API and static viewer HTML. PostgreSQL
stores metadata in a StatefulSet. Zarr frame arrays live on a PVC. An SSE
endpoint streams frame-available events to viewers in real time.

All resources live in a `tdgl` namespace.

## PostgreSQL Migration

- Add `psycopg2-binary` to `pyproject.toml` dependencies.
- No code changes to `db.py` or `models.py`. SQLAlchemy already maps
  `postgresql+psycopg2://` URLs correctly. The `json_type` variable already
  uses `JSONB` for PostgreSQL.
- The `TDGL_DATABASE_URL` environment variable selects the backend:
  `sqlite+pysqlite:///:memory:` for tests, `postgresql+psycopg2://...` for K8s.
- The existing `create_schema=True` path creates tables on startup. Alembic
  migrations are deferred to a later iteration.

## SSE Live Events Endpoint

### Endpoint

`GET /api/runs/{run_id}/events` returns an SSE stream.

### Event types

- `frame_available` — sent when a frame becomes readable. Payload:
  `{run_id, frame_index, time_value, je, voltage, frame_count}`.
- `run_completed` — sent when a run is marked complete. Payload:
  `{run_id, status}`.

### Implementation

- In-process event bus: a module-level dict mapping `run_id` to a list of
  `asyncio.Queue` instances. Each SSE subscriber gets its own queue.
- When `api_append_frame` completes (after `mark_frame_available` and
  `session.commit`), it puts a `frame_available` event on all queues for that
  run.
- The SSE endpoint is an async generator that reads from its queue with a
  30-second timeout. On timeout it sends a `:keepalive` SSE comment. On client
  disconnect it removes its queue from the bus.
- The viewer (sub-project 3) opens an `EventSource` to this endpoint.

### Error handling

- If the run doesn't exist, return 404 (no SSE stream).
- If a queue grows beyond 100 events (slow consumer), drop the oldest events
  to prevent unbounded memory growth.

## Storage

Two PVCs in the `tdgl` namespace:

1. **PostgreSQL data** — 1Gi PVC, mounted by the PostgreSQL StatefulSet at
   `/var/lib/postgresql/data`.
2. **Zarr arrays** — 10Gi PVC (configurable), mounted by the data service
   Deployment at `/data/zarr`.

Both use the cluster's default StorageClass with `ReadWriteOnce` access mode.
Single-replica data service is sufficient since Zarr PVC is RWO.

## K8s Manifests

```
k8s/
  namespace.yaml
  postgresql/
    statefulset.yaml      # 1 replica, PG 16
    service.yaml          # postgres.tdgl.svc.cluster.local:5432
    pvc.yaml              # 1Gi PG data
    secret.yaml           # POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
  data-service/
    deployment.yaml       # 1 replica, data service container
    service.yaml          # data-service.tdgl.svc.cluster.local:80
    configmap.yaml        # TDGL_ZARR_ROOT=/data/zarr
    secret.yaml           # TDGL_DATABASE_URL
    pvc.yaml              # 10Gi Zarr data
```

### Data service container

- Environment: `TDGL_DATABASE_URL` from Secret, `TDGL_ZARR_ROOT=/data/zarr`
  from ConfigMap.
- Liveness probe: `GET /api/runs` (returns 200 even with empty list).
- Readiness probe: same endpoint.
- Mounts Zarr PVC at `/data/zarr`.

### PostgreSQL StatefulSet

- Standard PG 16 image.
- Environment from Secret: `POSTGRES_DB=tdgl`, `POSTGRES_USER=tdgl`,
  `POSTGRES_PASSWORD` (generated).
- Mounts PG PVC at `/var/lib/postgresql/data`.

## Dockerfile

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY tdgl_data/ tdgl_data/
EXPOSE 8000
CMD ["uvicorn", "tdgl_data.dev_app:create_dev_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

## Testing

Backend tests continue using in-memory SQLite (no changes to test fixtures).

New tests for SSE:

- SSE endpoint returns 404 for unknown run.
- SSE endpoint sends `frame_available` event after frame append.
- SSE endpoint sends keepalive after 30 seconds of silence.
- Queue overflow drops oldest events.

Manual verification:

- `kubectl apply -f k8s/` creates all resources.
- Port-forward to data service: viewer loads at `http://localhost:8000/viewer`.
- Create demo run, verify SSE events arrive via `curl` or browser EventSource.
- Create a run with 100+ frames, verify viewer stays responsive.

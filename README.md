# kubeflow-tdgl

Kubeflow-oriented TDGL (Time-Dependent Ginzburg-Landau) simulation platform
with a browser-based heatmap viewer.

## TDGL Data Service

FastAPI application that manages simulation metadata in PostgreSQL and frame
array data in Zarr. Includes a built-in heatmap viewer.

- REST API for run and frame CRUD
- PostgreSQL-compatible metadata schema with SQLAlchemy ORM
- Filesystem-backed Zarr frame arrays (psi_real, psi_imag, mu)
- Browser heatmap viewer with adaptive colorbars and frame buffering
- Server-Sent Events for real-time frame availability notifications
- Synthetic data generation for testing and UI prototyping
- K8s manifests for PostgreSQL StatefulSet + data service Deployment

## Development

```bash
python -m pip install -e ".[dev]"
pytest
```

Run the dev server (auto-creates SQLite schema):

```bash
uvicorn tdgl_data.dev_app:create_dev_app --factory --reload
```

Open http://127.0.0.1:8000/viewer — click **Create demo** to generate
synthetic frames and inspect |psi| and mu heatmaps.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/viewer` | Browser heatmap viewer |
| GET | `/api/runs` | List runs |
| POST | `/api/runs` | Create run |
| POST | `/api/demo-runs` | Create synthetic demo run |
| GET | `/api/runs/{id}` | Get run |
| DELETE | `/api/runs/{id}` | Delete run |
| GET | `/api/runs/{id}/timeline` | Frame metadata + global stats |
| GET | `/api/runs/{id}/iv` | I-V curve points |
| GET | `/api/runs/{id}/frames/{idx}` | Full frame arrays |
| POST | `/api/runs/{id}/frames` | Append frame |
| GET | `/api/runs/{id}/events` | SSE stream of frame events |

## Kubernetes Deployment

```bash
# Build and load image
docker build -t kubeflow-tdgl-data:latest .
kind load docker-image kubeflow-tdgl-data:latest  # or minikube

# Deploy
kubectl apply -f k8s/

# Port-forward to access viewer
kubectl port-forward -n tdgl svc/data-service 8000:80
```

## Project Structure

```
tdgl_data/
  app.py            FastAPI application factory
  config.py         Pydantic settings (env vars)
  db.py             SQLAlchemy engine + session factory
  dev_app.py        Dev entrypoint (auto-creates schema)
  events.py         SSE event bus
  models.py         SQLAlchemy ORM models
  repository.py     Database query functions
  schemas.py        Pydantic request/response schemas
  static/viewer.html  Browser heatmap viewer
  synthetic.py      Synthetic TDGL data generator
  zarr_store.py     Zarr array storage backend
k8s/                Kubernetes manifests
tests/              pytest test suite
```

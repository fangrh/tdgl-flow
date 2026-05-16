# kubeflow-tdgl

Kubeflow-oriented TDGL (Time-Dependent Ginzburg-Landau) simulation platform
with a browser-based heatmap viewer.

## TDGL Data Service

FastAPI application that manages simulation data in PostgreSQL. Includes a built-in heatmap viewer.

- REST API for run and frame CRUD
- PostgreSQL-compatible metadata schema with SQLAlchemy ORM
- Browser heatmap viewer with Plotly rendering and frame buffering
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
# Build images
docker build -f services/data-viewer/Dockerfile -t ghcr.io/fangrh/tdgl-data-viewer:latest .
docker build -f services/generator/Dockerfile -t ghcr.io/fangrh/tdgl-generator:latest .

# Deploy infrastructure
kubectl apply -f infra/namespace.yaml
kubectl apply -f infra/postgresql/k8s/

# Deploy services
kubectl apply -f services/data-viewer/k8s/

# Port-forward to access viewer
kubectl port-forward -n tdgl svc/data-viewer 8000:80
```

## Project Structure

```
tdgl_data/                Shared library (models, schemas, API, synthetic)
tdgl_generator/           Generator package (CLI + web app)
services/
  data-viewer/            Data service + viewer
    Dockerfile
    k8s/                  deployment, service, secret
  generator/              Test data generator
    Dockerfile
    k8s/                  job manifest
infra/
  namespace.yaml
  postgresql/k8s/         statefulset, pvc, service, secret
tests/                    pytest test suite
```
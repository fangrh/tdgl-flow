# TDGL Simulation Workflow Design

## Overview

A unified web application for configuring and running TDGL simulations. The app provides three stages — build device, build timing sequence, submit simulation — in a single FastAPI + Jinja2 service. The actual cpp-tdgl simulation runs as an Argo Workflow, streaming results Je-by-Je to the existing data-viewer service.

## Architecture

Single monolith approach (`tdgl-workflow`): one FastAPI container handles device building, timing configuration, and workflow submission. Mesh generation uses the `tdgl` Python library. Static matplotlib plots provide quick-check previews. Simulation execution is delegated to Argo Workflows.

```
User → tdgl-workflow (build device) → saves device_params to PostgreSQL
User → tdgl-workflow (build timing) → saves timing_params to PostgreSQL
User → tdgl-workflow (submit sim)  → submits Argo Workflow
Argo → cpp-tdgl-runner container    → streams frames to data-viewer API
data-viewer                         → user views results via existing viewer
```

### Services

| Service | Role |
|---------|------|
| `tdgl-workflow` (new) | FastAPI + Jinja2 web app: device/timing/simulate UI |
| `cpp-tdgl-runner` (new) | Slim container wrapping cpp-tdgl C++ binary, runs inside Argo |
| `data-viewer` (existing) | Receives simulation frames, serves results via REST + SSE |

## Database Schema

Two new tables in the existing PostgreSQL database, plus a foreign key addition to `runs`.

### `devices`

| Column | Type | Description |
|--------|------|-------------|
| `device_id` | UUID (PK) | Auto-generated |
| `name` | VARCHAR(128) | User-given name |
| `film_width` | FLOAT | Film width |
| `film_height` | FLOAT | Film height |
| `electrode_params` | JSONB | Electrode positions and sizes |
| `probe_params` | JSONB | Probe point positions |
| `mesh_edge_length` | FLOAT | Target mesh edge length |
| `mesh_sites` | JSONB | Generated mesh site coordinates |
| `mesh_elements` | JSONB | Generated mesh triangulation |
| `probe_indices` | JSONB | Mesh indices for probe points |
| `created_at` | TIMESTAMP | Creation time |

### `timing_configs`

| Column | Type | Description |
|--------|------|-------------|
| `timing_id` | UUID (PK) | Auto-generated |
| `name` | VARCHAR(128) | User-given name |
| `je_min` | FLOAT | Starting Je |
| `je_max` | FLOAT | Ending Je |
| `je_count` | INT | Number of Je steps |
| `ramp_time` | FLOAT | Ramp duration per step |
| `stable_time` | FLOAT | Stable measurement time |
| `save_time` | FLOAT | Data save window |
| `currents_data` | JSONB | Full generated current schedule |
| `created_at` | TIMESTAMP | Creation time |

### `runs` table changes

Add two nullable foreign key columns:
- `device_id` → `devices.device_id`
- `timing_id` → `timing_configs.timing_id`

## UI Pages

### `/device` — Device Builder

- **Form fields:** film width, film height, electrode positions (source/drain coords), probe points, mesh edge length, mesh smoothing rounds
- **"Build & Preview"** button: generates mesh server-side, renders static matplotlib mesh plot (sites, triangulation, electrodes highlighted)
- **"Save"** button: stores device to DB, redirects to device list or timing page

### `/timing` — Timing Builder

- **Form fields:** Je min, Je max, step count, ramp time, stable time, save time, optional ramp-down
- **"Build & Preview"** button: generates current schedule, renders static Je vs time plot showing sweep steps and save windows
- **"Save"** button: stores timing to DB

### `/simulate` — Submit Simulation

- Dropdown to select a saved device
- Dropdown to select a saved timing
- Additional solver options: dt, total time, adaptive stepping toggle
- "Review" shows combined summary of all params
- "Submit" creates a Run in DB, submits Argo Workflow, shows confirmation with data-viewer link
- List of recent runs with status + viewer links below the form

### Navigation

Simple top nav bar: Device | Timing | Simulate

## Argo Workflow

### WorkflowTemplate: `cpp-tdgl-sim`

Parameters:
- `run_id` — links back to DB
- `device_params` — serialized JSON
- `timing_params` — serialized JSON
- `solver_options` — dt, adaptive stepping, etc.

Single-step container. The `cpp-tdgl-runner` handles the full Je loop internally:
1. Receives params, generates HDF5 input files for cpp-tdgl
2. Runs simulation Je-by-Je
3. After each Je step, POSTs frame data (psi_real, psi_imag, mu, voltage, time) to `data-viewer /api/runs/{run_id}/frames`
4. On completion, signals run complete

## Deployment

### New resources

| Resource | Path |
|----------|------|
| tdgl-workflow Deployment | `services/tdgl-workflow/k8s/deployment.yaml` |
| tdgl-workflow Service | `services/tdgl-workflow/k8s/service.yaml` |
| tdgl-workflow Dockerfile | `services/tdgl-workflow/Dockerfile` |
| cpp-tdgl-runner Dockerfile | `services/cpp-tdgl-runner/Dockerfile` |
| Argo WorkflowTemplate | `workflows/cpp-tdgl-sim.yaml` |

### Docker images

- **tdgl-workflow:** Python 3.13 slim + `tdgl` library + matplotlib + FastAPI
- **cpp-tdgl-runner:** Builds cpp-tdgl C++ binary from source, adds Python runner script for HDF5 generation and frame streaming

### Routing

Nginx reverse proxy adds path prefix `/workflow/` → `tdgl-workflow` service. Data-viewer stays at its existing path.

### CI

Add `tdgl-workflow` and `cpp-tdgl-runner` to the existing CI pipeline: build on push, tag with git SHA, update k8s manifests.

### Infrastructure dependencies

No new infrastructure. Reuses:
- PostgreSQL (existing StatefulSet)
- Argo Workflows (existing installation)
- Nginx reverse proxy (existing)
- data-viewer service (existing)

## Scope

Initial release: rectangular devices only. Arbitrary shapes and templates can be added later by extending the device builder form and mesh generation logic.

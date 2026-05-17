# TDGL Simulation Workflow Design

## Overview

A unified web application for configuring and running TDGL simulations. The app provides three stages — build device, build timing sequence, submit simulation — in a single FastAPI + Jinja2 service. The actual cpp-tdgl simulation runs as an Argo Workflow, streaming results Je-by-Je to the existing data-viewer service.

## Architecture

Single monolith approach (`tdgl-workflow`): one FastAPI container handles device building, timing configuration, and workflow submission. Mesh generation uses the `tdgl` Python library. Static matplotlib plots provide quick-check previews. Simulation execution is delegated to Argo Workflows.

```
User → tdgl-workflow (configure device) → in-memory preview
User → tdgl-workflow (configure timing) → in-memory preview
User → tdgl-workflow (submit sim)       → saves all params to DB, submits Argo Workflow
Argo → cpp-tdgl-runner container        → streams frames to data-viewer API
data-viewer                             → user views results via existing viewer
```

Device and timing generation is fast, so previews are generated on-the-fly and held in the browser session. Nothing is persisted to the database until the user submits the full workflow.

### Services

| Service | Role |
|---------|------|
| `tdgl-workflow` (new) | FastAPI + Jinja2 web app: device/timing/simulate UI |
| `cpp-tdgl-runner` (new) | Slim container wrapping cpp-tdgl C++ binary, runs inside Argo |
| `data-viewer` (existing) | Receives simulation frames, serves results via REST + SSE |

## Database Schema

No new tables. Device and timing parameters are stored as JSON fields on the existing `runs` table at workflow submission time.

### `runs` table additions

Add two JSONB columns to store the full device and timing configuration:
- `device_params` JSONB — film dimensions, electrode positions, probe points, mesh edge length, mesh smoothing, plus generated mesh data (sites, elements, probe_indices)
- `timing_params` JSONB — Je min/max, step count, ramp/stable/save times, plus generated current schedule

## UI Pages

The three pages form a linear wizard. Device and timing params are held in browser session state (hidden form fields or local storage) and only persisted to the database on submission.

### `/device` — Device Builder

- **Form fields:** film width, film height, electrode positions (source/drain coords), probe points, mesh edge length, mesh smoothing rounds
- **"Build & Preview"** button: generates mesh server-side, renders static matplotlib mesh plot (sites, triangulation, electrodes highlighted). Params held in form state, not saved to DB.
- **"Next: Timing"** button: carries device params forward to the timing page

### `/timing` — Timing Builder

- **Form fields:** Je min, Je max, step count, ramp time, stable time, save time, optional ramp-down
- **"Build & Preview"** button: generates current schedule, renders static Je vs time plot showing sweep steps and save windows. Params held in form state.
- **"Next: Review & Submit"** button: carries both device and timing params forward

### `/simulate` — Review & Submit

- Shows combined summary of device params, timing params
- Additional solver options form: dt, total time, adaptive stepping toggle
- "Submit" creates a Run in DB (saves device_params + timing_params as JSONB on the run), submits Argo Workflow, shows confirmation with data-viewer link
- List of recent runs with status + viewer links below the form

### Navigation

Simple top nav bar: Device | Timing | Simulate

## Argo Workflow

### WorkflowTemplate: `cpp-tdgl-sim`

Parameters:
- `run_id` — links back to DB
- `device_params` — serialized JSON
- `timing_params` — serialized JSON
- `solver_options` — JSON object with: `dt` (initial timestep), `total_time` (simulation duration per Je), `adaptive` (boolean for adaptive stepping), `max_dt` (adaptive step cap)

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

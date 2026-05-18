# cpp-tdgl Zarr Storage + 3-Step Workflow Design

Date: 2026-05-18

## Overview

Refactor the cpp-tdgl simulation pipeline to:
1. Store per-site simulation data in Zarr (replacing the current 2D-grid approach)
2. Split the Argo workflow into 3 independent steps: build-device, build-timing, simulate
3. Expose device/timing/simulation as microservice APIs for both UI and Python SDK consumption
4. Enhance the viewer to read per-site data and render with interpolation/triangulation
5. Track workflow status via a simple `run.status` field (created/running/completed/failed)

## 1. Data Model and Storage

### 1.1 Zarr (per-site arrays)

Each run stores raw per-site data directly from the C++ solver, without interpolation to a 2D grid.

```
data/zarr/runs/<run_id>/frames.zarr/
  psi_real/    shape=(n_steps, n_sites), dtype=float64, chunks=(1, n_sites)
  psi_imag/    shape=(n_steps, n_sites), dtype=float64, chunks=(1, n_sites)
  mu/          shape=(n_steps, n_sites), dtype=float64, chunks=(1, n_sites)
```

This preserves full fidelity of the simulation output and defers interpolation to the viewer.

### 1.2 PostgreSQL Schema Changes

**`runs` table** (modify existing):
- Add `mesh_sites` JSONB column — array of [x, y] coordinates for each mesh site
- Add `mesh_elements` JSONB column — array of triangle element indices
- Add `n_sites` Integer column — number of mesh sites
- Add `solver_options` JSONB column — solver configuration
- Keep existing: `run_id`, `status`, `solver_type`, `device_params`, `timing_params`, `mesh_metadata`, `metadata_`, `total_frames`, timestamps

**`frames` table** (modify existing):
- Remove `psi_real`, `psi_imag`, `mu` JSON columns (large arrays move to Zarr)
- Remove `zarr_exists` boolean (always true for new runs)
- Keep: `frame_index`, `time_value`, `je`, `voltage`, `frame_stats`

**`iv_points` table**: unchanged

**`run_events` table**: unchanged

### 1.3 ZarrStore Class Changes

Update `ZarrStore` in `src/tdgl_data/zarr_store.py`:

- `create_run(run_id, n_sites)` — create arrays with shape `(0, n_sites)` instead of `(0, ny, nx)`
- `append_frame(run_id, step_index, arrays)` — each array is 1D (n_sites,) per field
- `get_frame(run_id, step_index)` — returns 1D arrays
- Add `get_all_frames(run_id)` — returns full 2D arrays (n_steps, n_sites) for batch operations

## 2. Microservice APIs

### 2.1 Device Service

**Endpoint**: `POST /api/device/build`

Request:
```json
{
  "film_width": 10.0,
  "film_height": 2.0,
  "elec_width": 0.5,
  "elec_height": 1.0,
  "elec_y_offset": 0.0,
  "probe_points": [[-2.0, 0.0], [2.0, 0.0]],
  "max_edge_length": 0.5,
  "smooth": 100
}
```

Response:
```json
{
  "sites": [[x1, y1], ...],
  "elements": [[i1, i2, i3], ...],
  "boundary_indices": [...],
  "areas": [...],
  "edge_mesh": { "centers": [...], "edges": [...], ... },
  "terminals": [...],
  "probe_indices": [idx1, idx2],
  "device_constants": { ... },
  "num_sites": 1234,
  "preview_plot": { ... }
}
```

The `preview_plot` field contains Plotly JSON for rendering in both UI and Jupyter.

### 2.2 Timing Service

**Endpoint**: `POST /api/timing/build`

Request:
```json
{
  "mode": "linear",
  "je_initial": 0.0,
  "je_final": 10.0,
  "je_step": 1.0,
  "ramp_time": 1.0,
  "stable_time": 5.0,
  "save_time": 3.0,
  "ramp_down": true,
  "solver_options": { "dt": 1e-6, "max_dt": 0.1, "adaptive": true }
}
```

Or for segmented mode:
```json
{
  "mode": "segmented",
  "segments": [...],
  "ramp_time": 1.0,
  "stable_time": 5.0,
  "save_time": 3.0
}
```

Response:
```json
{
  "steps": [
    { "je_start": 0.0, "je_end": 1.0, "ramp_start": 0.0, "ramp_end": 1.0, "stable_start": 1.0, "stable_end": 6.0, "save_start": 3.0, "save_end": 6.0 }
  ],
  "ramp_down_steps": [...],
  "solve_time": 5.0,
  "n_steps": 20,
  "preview_plot": { ... }
}
```

### 2.3 Simulation Submission

**Endpoint**: `POST /api/workflows/submit`

Request:
```json
{
  "device_params": { ... },
  "timing_params": { ... },
  "mesh_data": { ... },
  "schedule": { ... },
  "solver_options": { ... },
  "resources": { "cpu_cores": 2, "memory_mib": 2048 }
}
```

Response:
```json
{
  "run_id": "abc123",
  "workflow_name": "cpp-tdgl-abc123-xyz",
  "status": "created"
}
```

This endpoint creates the run record in PostgreSQL, initializes the Zarr store, and submits the 3-step Argo workflow.

### 2.4 Mesh Data Endpoint

**Endpoint**: `GET /api/runs/{run_id}/mesh`

Returns the mesh geometry for triangulation rendering:
```json
{
  "sites": [[x1, y1], ...],
  "elements": [[i1, i2, i3], ...],
  "probe_indices": [idx1, idx2],
  "n_sites": 1234
}
```

### 2.5 Frame Data Endpoint

**Endpoint**: `GET /api/runs/{run_id}/frames/{frame_index}`

Response (per-site mode):
```json
{
  "frame_index": 0,
  "time_value": 6.0,
  "je": 1.0,
  "voltage": 0.0123,
  "psi_real": [0.1, 0.2, ...],
  "psi_imag": [0.3, 0.4, ...],
  "mu": [0.5, 0.6, ...]
}
```

## 3. Argo 3-Step Workflow

### 3.1 WorkflowTemplate: `cpp-tdgl-sim`

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: cpp-tdgl-sim
  namespace: tdgl
spec:
  entrypoint: simulation-pipeline
  arguments:
    parameters:
      - name: run-id
        value: ""
      - name: data-service-url
        value: "http://data-viewer.tdgl.svc.cluster.local"
      - name: image
        value: "ghcr.io/fangrh/cpp-tdgl-runner:latest"
      - name: device-params-json
        value: "{}"
      - name: timing-params-json
        value: "{}"
      - name: solver-options-json
        value: "{}"
      - name: cpu
        value: "2"
      - name: memory
        value: "4Gi"

  templates:
    - name: simulation-pipeline
      steps:
        - - name: build-device
            template: build-device-step
        - - name: build-timing
            template: build-timing-step
        - - name: simulate
            template: simulate-step

    - name: build-device-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/build_device.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: DEVICE_PARAMS
            value: "{{workflow.parameters.device-params-json}}"
          - name: DATA_SERVICE_URL
            value: "{{workflow.parameters.data-service-url}}"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: build-timing-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/build_timing.py]
        env:
          - name: RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: TIMING_PARAMS
            value: "{{workflow.parameters.timing-params-json}}"
          - name: DATA_SERVICE_URL
            value: "{{workflow.parameters.data-service-url}}"
        volumeMounts:
          - name: run-data
            mountPath: /data

    - name: simulate-step
      container:
        image: "{{workflow.parameters.image}}"
        command: [python, /app/runner.py]
        env:
          - name: TDGL_RUN_ID
            value: "{{workflow.parameters.run-id}}"
          - name: TDGL_DATA_SERVICE_URL
            value: "{{workflow.parameters.data-service-url}}"
          - name: SOLVER_OPTIONS
            value: "{{workflow.parameters.solver-options-json}}"
        resources:
          requests:
            cpu: "{{workflow.parameters.cpu}}"
            memory: "{{workflow.parameters.memory}}"
          limits:
            cpu: "{{workflow.parameters.cpu}}"
            memory: "{{workflow.parameters.memory}}"
        volumeMounts:
          - name: run-data
            mountPath: /data

  volumeClaimTemplates:
    - metadata:
        name: run-data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 1Gi
```

### 3.2 Data Flow Between Steps

1. **build-device**: Runs mesh generation locally using the same `build_rectangular_device()` function shared with the web service. Writes `device.h5` (HDF5 for C++ solver) and `mesh_meta.json` to shared volume.

2. **build-timing**: Runs timing generation locally using the same `build_timing()` / `build_timing_segmented()` function shared with the web service. Writes `timing.json` to shared volume.

3. **simulate**: Reads `device.h5` + `timing.json` from shared volume. Runs C++ solver per step. Reads per-site output, writes directly to Zarr store via data-service API. Updates run status.

### 3.3 Build Scripts

**`build_device.py`** (new, in cpp-tdgl-runner):
- Read DEVICE_PARAMS from env
- Call `build_rectangular_device()` from `tdgl_workflow.mesh`
- Write `device.h5` and `mesh_meta.json` to shared volume
- Optionally update run record via data-service API

**`build_timing.py`** (new, in cpp-tdgl-runner):
- Read TIMING_PARAMS from env
- Call `build_timing()` or `build_timing_segmented()` from `tdgl_workflow.timing`
- Write `timing.json` to shared volume

**`runner.py`** (modify existing):
- Read `device.h5` and `timing.json` from shared volume instead of fetching from data-service
- Per-site data goes directly to Zarr (no interpolation)
- Remove `interpolate_to_grid()` from the runner; interpolation moves to viewer

## 4. Viewer Enhancement

### 4.1 Frontend Changes to `viewer.html`

**Mesh loading**: On first load of a run, fetch mesh data from `GET /api/runs/{run_id}/mesh`. Cache the sites/elements for the session.

**Per-site rendering strategy**: Use the mesh triangulation to render directly:
- Compute |psi|^2 and phase (arctan2) per-site
- Use Plotly's `scatter` with marker colors OR implement a simple canvas-based triangulation fill
- Fallback: do bilinear interpolation in JS using the mesh sites and render as heatmap

**4-panel layout** (matching git-tdgl-light):
1. Top-left: |psi|^2 heatmap/trisurf
2. Top-right: Phase heatmap/trisurf
3. Bottom-left: mu heatmap/trisurf
4. Bottom-right: I-V curve (scatter plot with trail)

**SSE updates**: Keep existing EventSource mechanism. On `frame_available` event, fetch the new frame data and update the plot.

**Interpolation approach**: Implement a simple JavaScript interpolation:
- Build a temporary grid from mesh extent
- For each grid point, find the containing triangle and interpolate barycentrically
- This is more accurate than scipy's griddata for structured mesh data
- Alternatively: use a WebAssembly-compiled interpolation library

### 4.2 Viewer API Additions

Add to `tdgl_data/app.py`:

- `GET /api/runs/{run_id}/mesh` — return mesh sites, elements, probe_indices
- `GET /api/runs/{run_id}/frames/{frame_index}` — return per-site arrays from Zarr (1D arrays)
- Keep existing `GET /api/runs/{run_id}/timeline` — return scalar metadata for all frames
- Keep existing SSE endpoint for real-time updates

## 5. Workflow Status Management

### 5.1 State Machine

```
created ──→ running ──→ completed
                   └──→ failed
```

### 5.2 Status Update Points

| Status | Trigger | Actor |
|--------|---------|-------|
| created | Workflow submitted via UI or SDK | tdgl-workflow service |
| running | simulate step starts execution | runner.py |
| completed | simulate step finishes successfully | runner.py |
| failed | simulate step crashes or exits non-zero | runner.py |

### 5.3 UI Display

The simulate page shows a list of runs with colored status badges:
- Yellow dot + "running" for active runs
- Green dot + "completed" for finished runs
- Red dot + "failed" for failed runs

Clicking a run opens the viewer page with real-time updates (SSE).

## 6. Python SDK

### 6.1 Client Library

A lightweight Python client in `src/tdgl_sdk/client.py`:

```python
class TDGLClient:
    def __init__(self, base_url: str): ...

    def build_device(self, **params) -> DeviceResult: ...
    def build_timing(self, **params) -> TimingResult: ...
    def submit_simulation(self, device, timing, resources=None) -> Run: ...
    def get_run(self, run_id: str) -> Run: ...
    def list_runs(self) -> list[Run]: ...
    def get_run_status(self, run_id: str) -> str: ...
    def get_mesh(self, run_id: str) -> MeshData: ...
    def get_frame(self, run_id: str, frame_index: int) -> FrameData: ...
    def preview_device(self, device) -> "plotly.Figure": ...
    def preview_timing(self, timing) -> "plotly.Figure": ...
    def view_results(self, run_id: str) -> "plotly.Figure": ...
```

### 6.2 Jupyter Notebook Example

The SDK provides Plotly figures that render natively in Jupyter:

```python
from tdgl_sdk import TDGLClient

client = TDGLClient("http://your-tdgl-platform")

# Build and preview device
device = client.build_device(film_width=10, film_height=2, ...)
client.preview_device(device)  # Shows mesh plot in notebook

# Build and preview timing
timing = client.build_timing(je_initial=0, je_final=10, ...)
client.preview_timing(timing)  # Shows timing sequence plot

# Submit simulation
run = client.submit_simulation(device=device, timing=timing)

# Check status
run = client.get_run(run.run_id)
print(run.status)

# View results
client.view_results(run.run_id)  # Shows 4-panel plot
```

## 7. UI Behavior

The web UI follows the same service-oriented pattern:

1. **Device page**: User fills in parameters (pure frontend state). Clicks "Preview" → calls `POST /api/device/build` → renders mesh preview. No API call until preview is requested.

2. **Timing page**: User fills in parameters. Clicks "Preview" → calls `POST /api/timing/build` → renders timing sequence plot.

3. **Simulate page**: Displays configured device + timing summary. Clicks "Submit" → calls `POST /api/workflows/submit` → creates run + triggers Argo workflow → redirects to viewer.

4. **Viewer page**: Loads mesh from API, subscribes to SSE for frame updates, renders 4-panel plot with per-site data.

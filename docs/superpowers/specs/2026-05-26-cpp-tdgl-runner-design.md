# cpp-tdgl-runner: C++ TDGL Solver K8s Service

## Goal

Create a new K8s service that runs TDGL simulations using the C++ solver (cpp-tdgl) for significantly higher performance than py-tdgl, while maintaining 100% input/output compatibility so the existing viewer and SDK work without changes.

## Architecture

Single-container design: Python wrapper handles mesh building, epsilon injection, and MinIO uploads; compiled C++ binary handles the actual TDGL solve. The Python layer is pure glue — it never touches the solver loop.

```
Argo WorkflowTemplate: cpp-tdgl-sim
  ├── Step 1: build_device.py → mesh.h5      (reuse existing Python)
  ├── Step 2: build_timing.py → timing.json   (reuse existing Python)
  └── Step 3: runner.py                       (Python glue + C++ solver)
        ├── Read epsilon JSON → write into mesh.h5 as /epsilon dataset
        ├── Call cpp-tdgl-solve mesh.h5 timing.json --output output.h5
        │     (C++ writes py-tdgl-compatible HDF5 directly)
        └── Background thread: periodic MinIO upload (non-blocking)
```

## Input Compatibility

### Device Parameters (identical to py-tdgl)
```json
{
  "film_width": 10.0,
  "film_height": 5.0,
  "elec_width": 2.0,
  "elec_height": 1.0,
  "elec_y_offset": 2.0,
  "probe_points": [[0, 2.5], [10, 2.5]],
  "max_edge_length": 0.2,
  "smooth": 100
}
```
→ Processed by `build_device.py` (reused as-is) → mesh HDF5.

### Timing Parameters (identical to py-tdgl)
```json
{
  "mode": "simple",
  "je_initial": 0.0,
  "je_final": 1.0,
  "je_step": 0.1,
  "ramp_time": 5.0,
  "stable_time": 10.0,
  "ramp_down": false
}
```
→ Processed by `build_timing.py` (reused as-is) → timing.json. C++ parses the JSON to build the current injection schedule.

### Epsilon Parameters (identical to py-tdgl)
```json
{
  "type": "gaussian",
  "positions": [[5.0, 2.5]],
  "widths": [[1.0, 1.0]],
  "strengths": [0.5]
}
```
→ Python evaluates Gaussian functions on mesh sites → writes `(N,)` float64 array as `/epsilon` dataset in mesh.h5. C++ reads it directly.

### Solver Options (identical to py-tdgl)
```json
{
  "dt_init": 1e-6,
  "dt_max": 0.1,
  "adaptive": true,
  "save_every": 100
}
```
→ Passed as JSON to cpp-tdgl-solve CLI.

## C++ Solver Changes

### 1. Timing JSON Parser
- Parse timing.json with simple JSON library (nlohmann/json or similar)
- Generate step sequence: each step has `(je_start, je_end, ramp_time, stable_time)`
- Solver loop injects current according to step schedule

### 2. Epsilon Reader
- Read `/epsilon` dataset from mesh HDF5 (shape `(N,)`, dtype float64)
- Apply as disorder term in TDGL equations

### 3. py-tdgl-Compatible HDF5 Output
Output format matches py-tdgl exactly:
```
output.h5
├── mesh/                      ← copied from input mesh.h5
│   ├── sites                 (N, 2) float64
│   ├── elements              (M, 3) int64
│   ├── boundary_indices      (B,) int64
│   ├── edge_mesh/
│   │   ├── centers           (E, 2) float64
│   │   ├── edges             (E, 2) int64
│   │   ├── boundary_edge_indices (B,) int64
│   │   ├── directions        (E, 2) float64
│   │   ├── edge_lengths      (E,) float64
│   │   └── dual_edge_lengths (E,) float64
│   ├── areas                 (N,) float64
│   └── voronoi_polygons_flat
├── data/
│   ├── 0/
│   │   ├── psi               (N,) complex128
│   │   ├── mu                (N,) float64
│   │   ├── supercurrent      (E,) float64
│   │   ├── normal_current    (E,) float64
│   │   └── running_state/
│   │       ├── mu            (2, K) float64
│   │       └── dt            (K,) float64
│   ├── 1/
│   └── ...
```

- Every `save_every` steps: write new frame group, then HDF5 flush
- Flush allows Python background thread to upload partial results to MinIO for real-time viewing

## Python runner.py

Minimal glue script:
1. Parse Argo parameters from environment/CLI
2. Call `build_device.py` logic (or reuse the function) → mesh.h5
3. Call `build_timing.py` logic → timing.json
4. Evaluate epsilon on mesh sites → write `/epsilon` to mesh.h5
5. Fork `cpp-tdgl-solve mesh.h5 timing.json --solver-options '{...}' --output output.h5`
6. Background thread: every 30s, upload current output.h5 to MinIO at `tdgl-runs/{run_id}/output.h5`
7. On completion: upload final output.h5 + manifest.json to MinIO

## Docker

Multi-stage build for minimal image size:

```dockerfile
# Stage 1: Compile C++
FROM ubuntu:22.04 AS builder
RUN apt-get update && apt-get install -y cmake g++ libeigen3-dev libhdf5-dev liblapack-dev lib suitesparse-dev
COPY git-tdgl-light/cpp-tdgl /build/cpp-tdgl
RUN cd /build/cpp-tdgl && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build -j$(nproc)

# Stage 2: Runtime
FROM python:3.13-slim
RUN apt-get update && apt-get install -y libhdf5-dev liblapack3 libumfpack5
COPY --from=builder /build/cpp-tdgl/build/cpp-tdgl-solve /usr/local/bin/
COPY services/cpp-tdgl-runner/runner.py /app/runner.py
COPY src/tdgl_sdk/ /app/tdgl_sdk/
COPY src/tdgl_workflow/ /app/tdgl_workflow/
RUN pip install boto3 h5py numpy scipy tdgl
WORKDIR /app
CMD ["python", "runner.py"]
```

## Argo WorkflowTemplate

Parameters identical to py-tdgl-runner:
- `run-id`, `image`, `device-params-json`, `timing-params-json`, `solver-options-json`, `epsilon-params-json`

Steps identical structure:
1. `build-device-step` → mesh.h5
2. `build-timing-step` → timing.json
3. `simulate-step` → output.h5 + manifest.json → MinIO

## Directory Structure

```
services/cpp-tdgl-runner/
├── Dockerfile
├── runner.py
└── k8s/
    └── workflowtemplate.yaml
```

Reuses from existing codebase:
- `src/tdgl_sdk/` — MinIO client, device builder
- `src/tdgl_workflow/` — mesh builder, timing schedule
- `git-tdgl-light/cpp-tdgl/` — C++ TDGL solver (modified)

## Performance Considerations

- Python overhead is limited to setup (mesh building, epsilon generation) — not in the solver loop
- C++ solver writes HDF5 directly in py-tdgl format — zero format conversion
- MinIO uploads happen in a background thread, never blocking the solver
- HDF5 flush after each frame write enables partial uploads without stopping the solver

## Success Criteria

1. Same input JSON parameters as py-tdgl → same simulation setup
2. Output HDF5 is byte-compatible with py-tdgl (viewer reads it without changes)
3. manifest.json format identical to py-tdgl
4. Simulation speed significantly faster than py-tdgl for equivalent problems
5. Real-time viewing works during simulation (periodic MinIO uploads)

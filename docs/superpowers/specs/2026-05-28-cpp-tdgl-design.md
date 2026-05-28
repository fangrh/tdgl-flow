# cpp-tdgl + cpp-tdgl-viewer-rust Design

**Date:** 2026-05-28
**Status:** Draft

---

## 1. Overview

Implement a standalone C++ TDGL solver (`cpp-tdgl`) and a corresponding Rust viewer (`cpp-tdgl-viewer-rust`) for end-to-end simulation, mirroring the existing py-tdgl workflow but with native C++ performance. The solver writes split HDF5 files (mesh + per-step), enables real-time MinIO sync during solve, and the viewer reads directly from MinIO via HTTP S3 range requests.

---

## 2. Directory Structure

```
kubeflow-tdgl/
├── cpp-tdgl/                          # C++ source (copied + adapted from git-tdgl-light/cpp-tdgl)
│   ├── CMakeLists.txt
│   ├── src/
│   │   ├── main.cpp                  # CLI entry point
│   │   ├── mesh/
│   │   ├── device/
│   │   ├── options/
│   │   ├── operators/
│   │   ├── solver/
│   │   ├── solution/
│   │   └── timing/
│   ├── tests/
│   └── scripts/
│
├── cpp-tdgl-viewer-rust/              # New Rust viewer (separate PyO3 crate)
│   ├── Cargo.toml
│   ├── src/
│   │   ├── lib.rs                    # CppTdglViewer struct
│   │   ├── minio_client.rs            # HTTP S3 range-read client (from tdgl-viewer-rust pattern)
│   │   ├── discrete_reader.rs         # Reads mesh.h5 + step_XXXX.h5
│   │   ├── hdf5_index.rs             # Parses discrete_index.json
│   │   ├── renderer.rs               # 2x2 PNG panels (reused from tdgl-viewer-rust)
│   │   ├── iv.rs                     # IV scanner
│   │   └── run_info.rs               # Run metadata
│   └── python/
│       └── cpp_tdgl_viewer_rust/     # PyO3 wrappers
│
├── services/
│   └── cpp-tdgl-runner/               # Docker image for cpp-tdgl binary
│       ├── Dockerfile
│       └── entrypoint.sh
│
├── notebooks/
│   ├── run_cpp_tdgl.py                # Local simulation + live viewer
│   ├── browse_cpp_tdgl_runs.py
│   └── cpp_tdgl_viewer_demo.py
│
└── workflows/
    └── cpp-tdgl-device-builder.yaml  # Builds device mesh as K8s PVC artifact (parallel to rectangle-device-builder)
```

---

## 3. HDF5 Data Layout

### 3.1 Split Layout (mesh once, steps separate)

```
tdgl-runs/{run_id}/
├── manifest.json          # Run metadata
├── mesh.h5               # Shared mesh (written once)
├── step_0000.h5          # All frames for timing step 0
├── step_0001.h5
├── step_0002.h5
├── ...
└── discrete_index.json   # Step → file mapping + byte offsets
```

### 3.2 `mesh.h5` Schema

```
mesh.h5
├── /mesh/
│   ├── sites         (N, 2)   float64   — x, y coordinates
│   ├── elements      (M, 3)   int64     — node indices per triangle
│   ├── areas         (N,)     float64   — Voronoi cell areas per site
│   ├── edge_mesh/
│   │   ├── edges            (E, 2)   int64     — site index pairs
│   │   ├── centers         (E, 2)   float64  — edge midpoints
│   │   ├── directions      (E, 2)   float64  — unit tangent vectors
│   │   ├── edge_lengths    (E,)     float64  — |dl|
│   │   └── dual_edge_lengths (E,)   float64  — |dl*| (dual)
└── /device/
    ├── layer         — london_lambda, coherence_length, thickness, conductivity, u, gamma, z0
    ├── terminals     — per-terminal site/edge indices, length, name
    ├── probe_points   — site indices for voltage probes
    ├── K0, A0, Bc2, Lambda
```

### 3.3 `step_XXXX.h5` Schema

Each step H5 contains **all time frames** for that timing step:

```
step_XXXX.h5
├── /data/
│   ├── step_000/            # Frame 0 of this step
│   │   ├── psi             (N, 2)   complex128 interleaved [re, im]
│   │   ├── mu              (N,)     float64
│   │   ├── supercurrent     (E,)     float64
│   │   ├── normal_current  (E,)     float64
│   │   ├── applied_A       (E,)     float64
│   │   ├── induced_A       (E,)     float64
│   │   ├── epsilon         (N,)     float64
│   │   ├── dt              scalar    float64   — time step size used
│   │   └── time            scalar    float64   — simulation time at this frame
│   ├── step_001/
│   │   └── ...
│   └── step_XXX/
└── /metadata/
    ├── step_idx        int          — timing step index (0, 1, 2, ...)
    ├── je              float64      — applied current density for this step
    ├── ramp_start      float64      — simulation time when ramp begins
    ├── stable_end      float64      — simulation time when stable period ends
    ├── total_frames    int          — number of frames in this step
    ├── n_sites         int
    └── n_edges         int
```

### 3.4 Byte Offset Index

For each `step_XXXX.h5`, byte offsets for `psi`, `mu`, `supercurrent`, `normal_current`, `applied_A`, `induced_A`, `epsilon` are precomputed and stored in `discrete_index.json`. The Rust viewer reads at exact byte ranges via HTTP S3 `Range` headers — no h5py needed for data reads.

---

## 4. `discrete_index.json` Schema

Compatible with the existing py-tdgl discrete index:

```json
{
  "version": 1,
  "run_id": "abc123",
  "mesh_file": "mesh.h5",
  "n_sites": 3000,
  "n_edges": 8090,
  "n_steps": 12,
  "steps": [
    {
      "step_idx": 0,
      "je": 0.05,
      "ramp_start": 0.0,
      "stable_end": 200.0,
      "h5_file": "step_0000.h5",
      "total_frames": 200,
      "psi_offset": 123456,
      "psi_size": 48000,
      "mu_offset": 234567,
      "mu_size": 24000,
      "supercurrent_offset": 345678,
      "supercurrent_size": 64720,
      "normal_current_offset": 456789,
      "normal_current_size": 64720,
      "applied_A_offset": 567890,
      "applied_A_size": 64720,
      "induced_A_offset": 678901,
      "induced_A_size": 64720,
      "epsilon_offset": 789012,
      "epsilon_size": 24000
    },
    ...
  ],
  "status": "completed"
}
```

Byte offsets are absolute from the start of each H5 file. `psi_size = n_sites * 2 * 8`, `mu_size = n_sites * 8`, `supercurrent_size = n_edges * 8`.

---

## 5. cpp-tdgl CLI Interface

```bash
cpp-tdgl \
  --mesh MESH.h5 \
  --timing TIMING.json \
  --options OPTIONS.json \
  --output-dir ./output \
  --sync-url http://minio:9000 \
  --sync-bucket tdgl-results \
  --sync-prefix tdgl-runs/{run_id}/ \
  --sync-interval 5
```

| Flag | Description |
|------|-------------|
| `--mesh` | Input mesh H5 (mesh + device group) |
| `--timing` | Timing schedule JSON |
| `--options` | Solver options JSON |
| `--output-dir` | Local output directory |
| `--sync-url` | MinIO S3 endpoint |
| `--sync-bucket` | MinIO bucket name |
| `--sync-prefix` | MinIO key prefix for this run |
| `--sync-interval` | Seconds between sync uploads (default: 5) |

**Local output layout** (written before sync):
```
{output_dir}/
├── mesh.h5           # Symlink or copy of input mesh
├── step_0000.h5
├── step_0001.h5
├── manifest.json
└── discrete_index.json
```

After each timing step completes, the sync thread uploads `step_XXXX.h5` and updates `discrete_index.json` on MinIO.

---

## 6. Real-Time Sync Protocol

1. Solver completes a timing step → writes `step_XXXX.h5` locally
2. Sync thread detects new file (polling every N seconds)
3. Upload `step_XXXX.h5` to MinIO: `PUT Object` to `sync-bucket/sync-prefix/step_XXXX.h5`
4. Append/update entry in local `discrete_index.json`
5. Upload `discrete_index.json` to MinIO (atomic replace)
6. Repeat until all steps complete
7. Upload `mesh.h5` + `manifest.json` at the end

The viewer can poll `discrete_index.json` on MinIO to track progress frame by frame.

---

## 7. cpp-tdgl-viewer-rust Architecture

### 7.1 Crate Structure

```
cpp-tdgl-viewer-rust/
├── Cargo.toml
├── src/
│   ├── lib.rs              # CppTdglViewer Python class (PyO3)
│   ├── minio_client.rs     # S3 range-read client (reused pattern from tdgl-viewer-rust)
│   ├── discrete_reader.rs # Reads from mesh.h5 + step_XXXX.h5
│   ├── hdf5_index.rs      # Parses discrete_index.json
│   ├── renderer.rs        # 2x2 PNG render (copied from tdgl-viewer-rust)
│   ├── iv.rs              # IV scanner (copied from tdgl-viewer-rust)
│   └── run_info.rs        # Run metadata
└── python/
    └── cpp_tdgl_viewer_rust/
        └── widget.py      # ipywidgets wrapper
```

### 7.2 Python API

```python
from cpp_tdgl_viewer_rust import CppTdglViewer

viewer = CppTdglViewer()
viewer.open(minio_url="http://minio:9000",
            bucket="tdgl-results",
            prefix="tdgl-runs/abc123/")
# Displays widget with:
#   - Dropdown: select timing step
#   - Slider: frame within step
#   - Play/pause, FPS control
#   - 2x2 panel: |psi|^2, mu, V(t), I-V
```

### 7.3 Shared Components with tdgl-viewer-rust

The following are **copied** from `tdgl-viewer-rust` (not extracted as a shared crate, per user preference for separation):

- `minio_client.rs` — identical HTTP S3 range-read logic
- `renderer.rs` — identical 2x2 PNG panel rendering
- `iv.rs` — identical IV scanning and voltage computation
- `run_info.rs` — adapted to cpp-tdgl manifest schema

Only `discrete_reader.rs` and `hdf5_index.rs` are new — they read from the split mesh/step H5 layout.

---

## 8. Docker Deployment

### 8.1 cpp-tdgl-runner Dockerfile

```dockerfile
FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    libeigen3 libsuitesparse-dev libopenmpi-dev openmpi-bin libgomp1 \
    hdf5-tools
WORKDIR /app
COPY cpp-tdgl /app/cpp-tdgl
COPY entrypoint.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
```

cpp-tdgl is compiled statically with Eigen (header-only) and links SuiteSparse/UMFPACK at runtime. OpenMP is used for parallel Biot-Savart loops.

### 8.2 Argo Workflow Template

```yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: cpp-tdgl-sim
spec:
  entrypoint: cpp-tdgl-sim
  arguments:
    parameters:
      - name: run_id
      - name: mesh_artifact
      - name: timing_artifact
      - name: options_artifact
  templates:
    - name: cpp-tdgl-sim
      inputs:
        artifacts:
          - name: mesh_artifact
          - name: timing_artifact
          - name: options_artifact
      container:
        image: ghcr.io/fangrh/cpp-tdgl-runner:dev
        args:
          - --mesh /inputs/mesh_artifact/mesh.h5
          - --timing /inputs/timing_artifact/timing.json
          - --options /inputs/options_artifact/options.json
          - --output-dir /outputs
          - --sync-url http://minio:9000
          - --sync-bucket tdgl-results
          - --sync-prefix tdgl-runs/{{inputs.parameters.run_id}}/
```

---

## 9. Notebook Integration

### 9.1 `notebooks/run_cpp_tdgl.py`

```python
from tdgl_sdk.pipeline import SimulationPipeline
from tdgl_sdk.client import TDGLRunStore
from cpp_tdgl_viewer_rust import CppTdglViewer
import ipywidgets as widgets

def run_cpp_tdgl(device, timing, options, run_id=None):
    # Submit Argo workflow with cpp-tdgl-runner image
    pipeline = SimulationPipeline(image="ghcr.io/fangrh/cpp-tdgl-runner:dev")
    run = pipeline.submit(device=device, timing=timing, options=options, run_id=run_id)

    # Poll discrete_index.json for completion
    store = TDGLRunStore()
    while not store.step_completed(run.run_id, -1):
        time.sleep(5)

    # Launch viewer
    viewer = CppTdglViewer()
    viewer.open(minio_url=store.minio_url, bucket="tdgl-results",
                prefix=f"tdgl-runs/{run.run_id}/")
    display(viewer.widget)
    return run
```

---

## 10. Build and Test Plan

1. **cpp-tdgl** — Copy source, adapt CMakeLists.txt for new repo paths, add `--sync-*` CLI flags + sync thread to `main.cpp`
2. **services/cpp-tdgl-runner** — Dockerfile + entrypoint script
3. **cpp-tdgl-viewer-rust** — Scaffold Cargo project, copy renderer/iv/minio_client from tdgl-viewer-rust, implement discrete_reader for split layout
4. **notebooks/run_cpp_tdgl.py** — Adapt from run_py_tdgl.py
5. **workflows/cpp-tdgl-device-builder.yaml** — Adapt from rectangle-device-builder.yaml
6. **Integration test** — Full local run: build device → submit cpp-tdgl → view live in notebook

---

## 11. Key Differences from py-tdgl

| Aspect | py-tdgl | cpp-tdgl |
|--------|---------|----------|
| Language | Python + C (tdgl library) | C++20 |
| Solver | Python tdgl.solve() | Native C++ TdglSolver |
| Discrete H5 | `je_NNNN.h5` | `step_XXXX.h5` (step index, not Je value) |
| Viewer | TdglDiscreteViewer (tdgl-viewer-rust) | CppTdglViewer (cpp-tdgl-viewer-rust) |
| Real-time sync | Background thread in runner.py | Built into cpp-tdgl binary |
| Deployment | py-tdgl-runner image | cpp-tdgl-runner image |

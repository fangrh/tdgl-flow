# tdgl-viewer-rust: Rust-based TDGL Simulation Viewer

**Date:** 2026-05-25  
**Status:** Approved  

## Problem

The current Python viewer (`tdgl_sdk.viewer`) suffers from performance limitations:
- Every frame opens a new HDF5 connection over ROS3 (~50-100ms per frame via port-forward)
- Python GIL prevents true parallel I/O and rendering
- Je step boundary stutter from `_vt_step_cache` misses (~200ms)
- PIL/Pillow software rasterization adds ~30-50ms per frame
- Target 10 FPS is barely achievable; smoother playback is impossible

## Solution

A Rust-based viewer compiled as a Python extension via PyO3/maturin. Rust handles all data access, rendering, and buffering internally with proper threading (no GIL). Python only manages ipywidgets UI.

## Architecture

```
┌─ Jupyter Notebook ──────────────────────────────────────┐
│                                                          │
│  viewer = TdglViewer(minio_url="http://localhost:30900") │
│  viewer.open(run_id)       # from pipeline.submit()     │
│  viewer.display()          # renders 2x2 interactive UI │
│                                                          │
└──────────────────────────────────────────────────────────┘
         │
    PyO3 FFI (zero-copy numpy ↔ Rust)
         │
┌─ tdgl_viewer_rust (Rust) ───────────────────────────────┐
│  minio:      reqwest HTTP range requests to MinIO        │
│  hdf5_index: parse HDF5 superblock, locate dataset       │
│  frame_reader: read psi/mu per frame                     │
│  renderer:   interpolate + colormap + composite + PNG    │
│  buffer:     ring buffer + prefetch threads (no GIL)     │
│  iv:         I-V and V-vs-t computation                  │
│  run_info:   manifest parsing + run listing              │
└──────────────────────────────────────────────────────────┘
```

## API

```python
from tdgl_viewer_rust import TdglViewer

viewer = TdglViewer(minio_url="http://localhost:30900")

# One-liner: pipeline returns run_id → pass to viewer
viewer.open(run_id="abc-123")

# Or select by index from run list
viewer.open(run_index=0)

# Display interactive 2x2 viewer in Jupyter
viewer.display()

# Get I-V data for external plotting
iv = viewer.get_iv_data()
```

Viewer is display-only. Simulation submission stays in `SimulationPipeline`. The one-flow continuity comes from passing `run_id` from `pipeline.submit()` to `viewer.open()`.

## Data Access

**No HDF5 library dependency.** Direct HTTP range requests to MinIO parse HDF5 structure manually.

### Two-phase read

Phase 1 (init, once): Parse HDF5 superblock + B-tree to locate byte offsets of:
- `solution/device/mesh/*` datasets
- `data/` group object headers (frame count)

Phase 2 (per frame): Two HTTP range requests for psi + mu arrays (~50-100KB total, ~5-10ms via port-forward).

### Manifest reading

Standard HTTP GET for `manifest.json` files. Returns `Vec<RunInfo>` with all metadata.

### Frame prefetching

Independent thread pool in Rust:
- Ring buffer keeps ±5 frames around current position
- Prefetch 3 frames ahead in playback direction
- At Je step boundaries, prefetch next step's V data proactively
- No GIL contention

## Renderer Pipeline

```
psi_raw (N,) complex128, mu_raw (N,) float64
  → griddata cubic interpolation to 100×50 grid
  → colormap: |psi| → inferno LUT, mu → RdBu_r LUT
  → resize to 360×180 panels (nearest neighbor)
  → composite 2×2 canvas (760×470)
  → PNG encode
  → bytes → ipywidgets.Image.value
```

Target: < 5ms per frame (rendering only, network I/O is separate).

## UI Layout

```
┌─ Run: [▼ abc-123 │ 6×4 │ Je 0→20 step=0.2 │ 3×3 ε=0.4 │ 12400 fr │ running] ─┐
│                                                                                 │
│  ┌──────────────────────┬──────────────────────┐                                │
│  │       |psi|          │        mu            │                                │
│  │     (inferno)        │     (RdBu_r)         │                                │
│  ├──────────────────────┼──────────────────────┤                                │
│  │      V-vs-t          │       I-V            │                                │
│  │   (Je step N/M)      │  (Je steps: K/L)    │                                │
│  └──────────────────────┴──────────────────────┘                                │
│                                                                                 │
│  [▶/❚❚]  ──────●──────────────  frame 42/1200  t=210.5                        │
│  FPS: [10]  Speed: [1]  status: LIVE frame 42 — steps 3/100                    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Run selector dropdown

Each item shows: `run_id (short) │ film W×H │ Je range step=N │ epsilon desc │ frames │ status`

Data from `manifest.json`. Selecting a run switches playback immediately.

### Progress bar

- Draggable slider for frame seeking
- During live playback, clicking on unready frames snaps to nearest available frame
- No stutter at Je step boundaries (prefetched by Rust)

## Project Structure

```
tdgl-viewer-rust/
├── Cargo.toml
├── src/
│   ├── lib.rs                 # PyO3 entry, TdglViewer class
│   ├── minio.rs               # HTTP client: list_runs, manifest, range requests
│   ├── hdf5_index.rs          # HDF5 superblock parse, dataset offset lookup
│   ├── frame_reader.rs        # Per-frame psi/mu/mesh reading
│   ├── renderer.rs            # Interpolation + colormap + composite + PNG
│   ├── buffer.rs              # Ring buffer + prefetch threads
│   ├── iv.rs                  # I-V / V-vs-t computation
│   └── run_info.rs            # RunInfo struct + manifest parsing
├── python/
│   └── tdgl_viewer_rust/
│       └── __init__.py        # from tdgl_viewer_rust import TdglViewer
└── tests/
    ├── test_minio.rs
    ├── test_renderer.rs
    └── test_hdf5_index.rs
```

### Dependencies

```toml
[dependencies]
pyo3 = { version = "0.22", features = ["extension-module"] }
numpy = "0.22"
reqwest = { version = "0.12", features = ["blocking"] }
tokio = { version = "1", features = ["rt-multi-thread"] }
serde_json = "1"
image = "0.25"
rayon = "1.10"
```

No `hdf5` crate — custom HTTP range + HDF5 structure parsing.

### Build

```bash
cd tdgl-viewer-rust
maturin develop --release
```

### Coexistence

- `tdgl_viewer_rust` is a separate package, does not replace existing `tdgl_sdk.viewer`
- Existing Python viewer code is untouched
- Both can be used side by side

## Out of Scope

- Workflow submission (stays in `SimulationPipeline`)
- Device building or timing configuration
- Modifying existing `tdgl_sdk` viewer code
- Publishing wheels to PyPI (manual build for now)

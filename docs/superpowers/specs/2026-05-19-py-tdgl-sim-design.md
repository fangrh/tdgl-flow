# Python tdgl Simulation Workflow (py-tdgl-sim) Design

## Status
Approved for implementation

## Background

The existing `cpp-tdgl-sim` workflow uses the C++ tdgl solver. For debugging and development, a Python tdgl workflow is needed that uses the pure Python `tdgl` package (https://py-tdgl.readthedocs.io/en/latest/). This enables faster iteration on the workflow logic itself, and allows comparison between Python and C++ solver results.

The Python tdgl workflow reuses `build_device` and `build_timing` steps (with device format adapted to Python tdgl native mesh format), while the simulate step uses Python tdgl's native solver API.

## Goals

1. Create `workflows/py-tdgl-sim.yaml` following the same patterns as `cpp-tdgl-sim`
2. Create `services/py-tdgl-runner/` service with Python tdgl runner
3. Output device mesh in Python tdgl native format (replacing C++-optimized device.h5)
4. Reuse `build_timing` step as-is (outputs timing.json)
5. Use dev/bind mode with `bind/` folder for `tdgl_workflow` and `tdgl_sdk` source
6. Agent submits via existing `tdgl_workflow` API by changing `workflowTemplateRef` to `py-tdgl-sim`

## Architecture

### Workflow: `workflows/py-tdgl-sim.yaml`

A WorkflowTemplate with 3 sequential steps:

```
build-device-step → build-timing-step → simulate-step
```

**Parameters (same as cpp-tdgl-sim):**
- `run-id`, `data-service-url`, `image`, `device-params-json`, `timing-params-json`, `solver-options-json`, `cpu`, `memory`, `dev-mode`

**volumes (bind mode):**
```yaml
volumes:
  - name: tdgl-workflow-src
    hostPath:
      path: /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/bind/tdgl_workflow
      type: Directory
  - name: tdgl-sdk-src
    hostPath:
      path: /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/bind/tdgl_sdk
      type: Directory
```

**Step 1: build-device-step**
- Reads `DEVICE_PARAMS` from env
- Calls `tdgl_workflow.mesh.build_rectangular_device()` (same as C++ version)
- Outputs `mesh_meta.json` in Python tdgl native format (not device.h5)

**Step 2: build-timing-step**
- Identical to cpp-tdgl-sim — copies `build_timing.py` from cpp-tdgl-runner
- Outputs `timing.json`

**Step 3: simulate-step**
- Reads `mesh_meta.json` and `timing.json` from `/data`
- Creates `tdgl.Device` from mesh data
- Runs `tdgl.solve()` with timing steps
- Posts frame data to data-service API (same as C++ runner)

### Service: `services/py-tdgl-runner/`

```
services/py-tdgl-runner/
├── Dockerfile
├── runner.py          # simulate step
├── build_device.py    # device mesh output (Python tdgl format)
├── build_timing.py    # copied from cpp-tdgl-runner
└── k8s/
    ├── deployment.yaml
    ├── service.yaml
    └── kustomization.yaml
```

**Dockerfile:**
```dockerfile
FROM python:3.13

RUN pip install --no-cache-dir numpy httpx h5py scipy zarr tdgl sqlalchemy pydantic pydantic-settings

COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY src/tdgl_sdk/ /app/vendor/tdgl_sdk/
COPY services/py-tdgl-runner/ /app/

CMD ["python", "/app/runner.py"]
```

**runner.py (simulate step):**
```python
import json, os, tdgl, numpy as np, httpx

DATA_DIR = os.environ.get("DATA_DIR", "/data")
RUN_ID = os.environ["TDGL_RUN_ID"]
DATA_URL = os.environ["TDGL_DATA_SERVICE_URL"]
DEV_MODE = os.environ.get("DEV_MODE", "false").lower() == "true"

# Read mesh and timing
with open(os.path.join(DATA_DIR, "mesh_meta.json")) as f:
    mesh_meta = json.load(f)

with open(os.path.join(DATA_DIR, "timing.json")) as f:
    timing_data = json.load(f)

# Build tdgl Device from mesh data
sites = np.array(mesh_meta["sites"])
triangles = np.array(mesh_meta["elements"])
layer = tdgl.Layer(**mesh_meta["layer"])

device = tdgl.Device(
    name=mesh_meta["device_constants"]["name"],
    layer=layer,
    film=tdgl.Polygon("film", points=np.column_stack([sites[:, 0].mean(), sites[:, 1].mean()])),
    terminals=[
        tdgl.Polygon(t["name"], points=sites[t["site_indices"]])
        for t in mesh_meta["terminals"]
    ],
    probe_points=[sites[i] for i in mesh_meta["probe_indices"]]
)

# Build sweep scenario from timing.json
steps = timing_data["steps"] + timing_data.get("ramp_down_steps", [])
terminal_currents = [{"source": s["je_end"], "drain": -s["je_end"]} for s in steps]
scenario = tdgl.SweepScenario(
    times=[s["stable_end"] for s in steps],
    terminal_currents=[terminal_currents] * len(steps)
)

# Run solver
options = tdgl.SolverOptions(solve_time=timing_data["solve_time"], ...)
solution = tdgl.solve(device, scenario, options)

# Post frame data
for i, (time, psi, mu) in enumerate(zip(solution.times, solution.psi, solution.mu)):
    frame_data = {
        "frame_index": i,
        "time_value": time,
        "psi_real": psi.real.tolist(),
        "psi_imag": psi.imag.tolist(),
        "mu": mu.tolist(),
    }
    httpx.post(f"{DATA_URL}/api/runs/{RUN_ID}/frames", json=frame_data)
```

### Bind Mount Structure

```
bind/
├── tdgl_workflow/ → src/tdgl_workflow
└── tdgl_sdk/ → src/tdgl_sdk
```

Both are mounted into all three steps at:
- `tdgl-workflow-src` → `/app/vendor/tdgl_workflow`
- `tdgl-sdk-src` → `/app/vendor/tdgl_sdk`

### API Submission

Agent calls `POST /workflow/api/workflows/submit` with:
```json
{
  "workflowTemplateRef": "py-tdgl-sim",
  "device_params": {...},
  "timing_params": {...},
  "solver_options": {...}
}
```

The existing `tdgl_workflow` API already passes `workflowTemplateRef` in the workflow spec — it just needs to point to `py-tdgl-sim` instead of `cpp-tdgl-sim`.

## Changes Summary

| File | Action |
|------|--------|
| `workflows/py-tdgl-sim.yaml` | Create — new WorkflowTemplate |
| `services/py-tdgl-runner/Dockerfile` | Create |
| `services/py-tdgl-runner/runner.py` | Create — Python tdgl simulate step |
| `services/py-tdgl-runner/build_device.py` | Create — adapted for Python tdgl mesh |
| `services/py-tdgl-runner/build_timing.py` | Copy from cpp-tdgl-runner |
| `services/py-tdgl-runner/k8s/kustomization.yaml` | Create |
| `services/kustomization.yaml` | Update — add py-tdgl-runner |
| `.dev-mode` | Update — add py-tdgl-runner to modules |
| `bind/tdgl_sdk` | Create symlink |

## Out of Scope

- C++ solver integration (separate workflow)
- Changes to data-service API
- Modifications to cpp-tdgl-sim workflow
# cpp-tdgl Dev Mode Enhancement Design

## Status
Approved for implementation

## Background

The `cpp-tdgl-runner` service runs tdgl simulations as Argo Workflow steps. When iterating on C++ solver code, dev mode bind-mounts the C++ source into the container for fast iteration without image rebuilds. However:

1. Python code (`tdgl_workflow`) is baked into the Docker image at `/app/vendor/tdgl_workflow/` — changes require image rebuild
2. The workflow lacks a Python API for autonomous agent debugging (agent must use UI to submit/check runs)

## Goals

1. Add bind mount support for `tdgl_workflow` Python code alongside existing C++ bind mount
2. Move bind mount host paths from ad-hoc locations to `bind/` folder (per CLAUDE.md convention)
3. Enable agent to autonomously submit workflows and read results via Python API

## Bind Folder Structure

```
bind/                      # gitignored
├── cpp-tdgl/              # symlink → src/cpp-tdgl
└── tdgl_workflow/         # symlink → src/tdgl_workflow
```

User creates symlinks (or copies) from `bind/` to `src/` as needed for dev iteration.

## Changes

### 1. `workflows/cpp-tdgl-sim.yaml`

**Current volumes:**
```yaml
volumes:
  - name: cpp-tdgl-src
    hostPath:
      path: /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/src/cpp-tdgl
      type: Directory
```

**Updated volumes:**
```yaml
volumes:
  - name: cpp-tdgl-src
    hostPath:
      path: /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/bind/cpp-tdgl
      type: Directory
  - name: tdgl-workflow-src
    hostPath:
      path: /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl/bind/tdgl_workflow
      type: Directory
```

**Updated volumeMounts in all three steps** (build-device-step, build-timing-step, simulate-step):
```yaml
volumeMounts:
  - name: run-data
    mountPath: /data
  - name: cpp-tdgl-src
    mountPath: /src/cpp-tdgl
  - name: tdgl-workflow-src
    mountPath: /app/vendor/tdgl_workflow
```

### 2. `.dev-mode`

Create or update:
```yaml
mode: bind-mount
modules:
  - cpp-tdgl
  - tdgl_workflow
active_since: "2026-05-19"
```

### 3. `bind/` Setup

User runs locally:
```bash
mkdir -p bind
ln -sfn src/cpp-tdgl bind/cpp-tdgl
ln -sfn src/tdgl_workflow bind/tdgl_workflow
```

Add to `.gitignore`:
```
bind/
```

### 4. Python API Access

**API base URL (through nginx):** `http://<host>/workflow/api/`

**Key endpoints for agent:**

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/workflows/submit` | Submit a new simulation workflow |
| GET | `/api/runs` | List all runs (includes status per run) |
| POST | `/api/preview/mesh` | Preview mesh without submitting |
| POST | `/api/preview/timing` | Preview timing without submitting |

The existing `tdgl_workflow` FastAPI app (`src/tdgl_workflow/app.py`) already implements these endpoints. No new API code needed.

### 5. Agent Debug Flow

1. Agent calls `POST /workflow/api/workflows/submit` with params
2. Agent polls `GET /workflow/api/runs/{run_id}` for status
3. On failure, agent reads logs and modifies params
4. Agent resubmits — no UI clicking required

## Files Modified

| File | Change |
|------|--------|
| `workflows/cpp-tdgl-sim.yaml` | Add tdgl_workflow volume, update mount paths to bind/ |
| `.dev-mode` | Create with bind-mount mode, both modules |
| `.gitignore` | Add `bind/` entry |

## Out of Scope

- Changes to Python scripts (`runner.py`, `build_device.py`, `build_timing.py`) — they already import from `/app/vendor/tdgl_workflow/`, mount change is transparent
- New API endpoints — existing API is sufficient
- Changes to tdgl-workflow service deployment

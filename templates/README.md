# Templates & Patterns

Quick reference for Claude Code when adding services or workflows.

## Service Templates

### Base template: `services/_base/`
Copy `services/_base/` to `services/<name>/` and adapt.

| File | Purpose |
|------|---------|
| `Dockerfile` | Python 3.13 base, installs common deps |
| `runner.py` | Minimal entry point skeleton |
| `k8s/deployment.yaml` | Deployment with health probes, resource limits, ghcr pull secret |
| `k8s/service.yaml` | ClusterIP Service (remove if internal-only) |
| `k8s/kustomization.yaml` | Lists deployment.yaml + service.yaml |

### Patterns: `services/_patterns/`

| Pattern | When to use |
|---------|-------------|
| `web-api.md` | FastAPI/HTTP service with health endpoint |
| `background-worker.md` | Argo Workflow step, no HTTP server |
| `data-pipeline.md` | Data transform (HDF5→Zarr, mesh gen, etc.) |

## Workflow Templates

### Base template: `workflows/_base/`
Copy `workflow-template.yaml` to `workflows/<name>.yaml` and adapt.

### Patterns: `workflows/_patterns/`

| Pattern | When to use |
|---------|-------------|
| `single-task.md` | One container, simple params |
| `dag-pipeline.md` | Sequential steps with dependencies |
| `parameter-sweep.md` | Parallel runs with different parameters |

## Real examples in this repo

| Service | Pattern | Location |
|---------|---------|----------|
| data-viewer | web-api | `services/data-viewer/` |
| tdgl-workflow | web-api | `services/tdgl-workflow/` |
| cpp-tdgl-runner | dag-pipeline | `workflows/cpp-tdgl-sim.yaml` |
| tdgl-generator | single-task | `services/generator/k8s/workflowtemplate.yaml` |

## Adding a new pattern

1. Identify reusable structure from a working service/workflow
2. Extract into a `.md` file under the relevant `_patterns/` directory
3. Follow the standard format: When to use → Key structure → What to change → Gotchas
4. Add to this README index

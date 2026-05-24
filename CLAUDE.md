# kubeflow-tdgl

End-to-end TDGL simulation: build device → run py-tdgl → store in MinIO → view in notebook.

## Project Structure

| Path | Purpose |
|------|---------|
| `services/py-tdgl-runner/` | Argo Workflow runner image |
| `workflows/rectangle-device-builder.yaml` | Standalone device-builder workflow |
| `src/tdgl_sdk/` | Notebook SDK: pipeline, MinIO access, viewer, diagnostics |
| `src/tdgl_workflow/` | Shared: mesh builder, timing schedule |
| `notebooks/e2e_sim_test.py` | Main end-to-end test + live viewer |
| `infra/` | Namespace, Argo Workflows, MinIO, nginx |
| `clusters/argocd/` | Argo CD app definitions |
| `tests/` | pytest suite |

## CI

Push to main → CI builds `ghcr.io/fangrh/py-tdgl-runner:<sha>` → updates workflowtemplate tag → Argo CD auto-syncs.

Path trigger: `services/py-tdgl-runner/**`, `src/**`, `pyproject.toml`.

## Dev

```bash
pip install -e ".[dev]"
kubectl port-forward -n tdgl svc/nginx-ingress 30080:80
kubectl port-forward -n tdgl svc/minio 30900:9000
pytest -q tests/
```

## Adding a New Service or Workflow

Use the existing `services/py-tdgl-runner/` and `workflows/rectangle-device-builder.yaml` as reference. Copy and adapt.

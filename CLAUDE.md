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
## Dev

1. **本地验证** — 逻辑清晰、输入输出数据结构明确，本地 notebook 跑通即可直接 CI/CD：
   ```bash
   pip install -e ".[dev]"
   python notebooks/e2e_sim_test.py
   ```
2. **K8s 验证**（仅确有必要时） — 涉及集群环境、资源调度等无法本地验证的改动：
   ```bash
   docker build -f services/py-tdgl-runner/Dockerfile -t ghcr.io/fangrh/py-tdgl-runner:dev .
   docker push ghcr.io/fangrh/py-tdgl-runner:dev
   kubectl -n tdgl submit workflow --from workflowtemplate/py-tdgl-sim -p image=ghcr.io/fangrh/py-tdgl-runner:dev
   ```
3. **上线** — push main → CI/CD 自动构建部署。

## CI

Path trigger: `services/py-tdgl-runner/**`, `src/**`, `pyproject.toml`。

## Adding a New Service or Workflow

Use the existing `services/py-tdgl-runner/` and `workflows/rectangle-device-builder.yaml` as reference. Copy and adapt.

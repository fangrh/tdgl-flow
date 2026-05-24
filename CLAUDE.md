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

1. **本地开发验证** — 改完代码直接跑 notebook 验证基本逻辑：
   ```bash
   pip install -e ".[dev]"
   python notebooks/e2e_sim_test.py   # 或在 Jupyter 里逐 cell 跑
   ```
2. **本地构建镜像 + 手动提交 workflow**：
   ```bash
   docker build -f services/py-tdgl-runner/Dockerfile -t ghcr.io/fangrh/py-tdgl-runner:dev .
   docker push ghcr.io/fangrh/py-tdgl-runner:dev
   kubectl -n tdgl submit workflow --from workflowtemplate/py-tdgl-sim -p image=ghcr.io/fangrh/py-tdgl-runner:dev
   ```
3. **上线** — 走标准 CI/CD：push main → CI 构建 + 更新 tag → Argo CD 自动同步。

## CI

Path trigger: `services/py-tdgl-runner/**`, `src/**`, `pyproject.toml`。

## Adding a New Service or Workflow

Use the existing `services/py-tdgl-runner/` and `workflows/rectangle-device-builder.yaml` as reference. Copy and adapt.

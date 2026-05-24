# kubeflow-tdgl

Minimal end-to-end TDGL simulation workflow:

1. Submit an Argo Workflow from a local notebook.
2. Build a rectangular py-tdgl device inside the workflow.
3. Build the current schedule.
4. Run py-tdgl.
5. Periodically upload the growing HDF5 output to MinIO.
6. Read the HDF5 directly from MinIO in a local notebook viewer.

There is no in-cluster data viewer, generator, viewer manager, database-backed data
service, or C++ runner in this trimmed version.

## Key Files

- `notebooks/e2e_sim_test.py`: main end-to-end workflow test and live viewer.
- `notebooks/009-native-widget-player.ipynb`: local widget-player experiment.
- `services/py-tdgl-runner/`: image used by the Argo workflow.
- `services/py-tdgl-runner/k8s/workflowtemplate.yaml`: `py-tdgl-sim` workflow template.
- `workflows/rectangle-device-builder.yaml`: standalone device-builder preprocessing workflow for py-tdgl.
- `src/tdgl_workflow/mesh.py`: rectangular device construction helper.
- `src/tdgl_workflow/timing.py`: timing schedule builder.
- `src/tdgl_sdk/`: notebook-facing pipeline, MinIO access, diagnostics, and viewer.
- `infra/`: namespace, Argo Workflows values, MinIO, and local nginx gateway.

## Local Setup

Install Python dependencies:

```bash
python -m pip install -e ".[dev]"
```

Forward the services used by `notebooks/e2e_sim_test.py`:

```bash
kubectl port-forward -n tdgl svc/nginx-ingress 30080:80
kubectl port-forward -n tdgl svc/minio 30900:9000
```

Run the notebook cells in:

```text
notebooks/e2e_sim_test.py
```

## Verification

```bash
python -m pytest -q tests/test_py_runner_timeline.py tests/test_viewer_diagnostics.py tests/test_timing.py tests/test_mesh.py tests/test_pipeline.py
```

## Deployment

The GitHub Actions workflow builds and pushes only:

```text
ghcr.io/fangrh/py-tdgl-runner:<sha>
```

After a successful build on `main`, CI updates:

- `services/py-tdgl-runner/k8s/workflowtemplate.yaml`
- `workflows/rectangle-device-builder.yaml`

ArgoCD sync then applies the new workflow template to the cluster.

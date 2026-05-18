# Pattern: Data Pipeline / Transform Service

## When to use
Service transforms data from one format to another (e.g., HDF5 → Zarr, mesh generation).

## Key structure

```python
# runner.py — transform pattern
import os
import httpx

DATA_DIR = os.environ.get("DATA_DIR", "/data")
DATA_SERVICE_URL = os.environ.get("TDGL_DATA_SERVICE_URL")

def main():
    # 1. Read input params from env/config
    params_json = os.environ.get("PIPELINE_PARAMS", "{}")

    # 2. Read input data from PVC or data-service API
    # 3. Transform
    # 4. Write output to PVC or data-service API
    pass
```

## What to change
- `runner.py` → input/output formats, transform logic
- `Dockerfile` → add format-specific libraries (h5py, scipy, etc.)
- WorkflowTemplate → env vars for params, resource limits

## Gotchas
- Large datasets: stream to/from data-service API rather than loading all in memory
- Use `volumeMounts` in WorkflowTemplate for shared data between pipeline steps
- If writing Zarr, go through the data-service API to maintain per-site structure
```
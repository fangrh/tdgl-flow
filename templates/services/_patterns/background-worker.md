# Pattern: Background Worker / Job Processor

## When to use
Service runs as an Argo Workflow step — no HTTP server, just processes input and writes output.

## Key structure

```dockerfile
FROM python:3.13
WORKDIR /app
RUN pip install --no-cache-dir httpx numpy zarr
COPY src/tdgl_workflow/ /app/vendor/tdgl_workflow/
COPY services/my-service/ /app/
CMD ["python", "/app/runner.py"]
```

```python
# runner.py
import os

DATA_DIR = os.environ.get("DATA_DIR", "/data")
RUN_ID = os.environ.get("RUN_ID", "")

def main():
    # Read input from DATA_DIR
    # Process
    # Write output to DATA_DIR
    pass

if __name__ == "__main__":
    main()
```

K8s: typically no `service.yaml` needed. The Deployment is replaced by a WorkflowTemplate.

## What to change
- `runner.py` → domain logic, env var names
- `Dockerfile` → domain-specific packages
- WorkflowTemplate → resource requests/limits, volume mounts

## Gotchas
- Always read/write through mounted PVC (`/data`), never local disk
- Pass `RUN_ID` as env var from WorkflowTemplate parameters
- Set `activeDeadlineSeconds` in the WorkflowTemplate to prevent hung jobs
```
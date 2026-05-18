# Pattern: HTTP API Service

## When to use
Service exposes REST endpoints consumed by other services or external clients.

## Key structure

```dockerfile
FROM python:3.13
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn httpx pydantic
COPY services/my-service/ /app/
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
```

```python
# main.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}
```

Deployment needs `livenessProbe` and `readinessProbe` pointing to `/health`.

## What to change
- `Dockerfile` → add domain-specific pip packages
- `main.py` → add routes
- `k8s/deployment.yaml` → env vars for external service URLs, resource limits
- `k8s/service.yaml` → port mapping if non-standard

## Gotchas
- Always add `/health` endpoint — Argo CD health checks need it
- Use `ClusterIP` type unless externally routed through nginx
- For sessions/cookies, mount a secret with `envFrom: secretRef`
```
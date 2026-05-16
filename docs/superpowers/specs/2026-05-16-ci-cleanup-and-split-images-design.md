# CI Cleanup and Image Split Design

**Goal:** Clean up legacy MLRun files, split the monolithic Docker image into two purpose-built images, and add a GitHub Actions CI workflow that builds and pushes both images to GHCR on push/PR.

**Architecture:** Two custom Docker images — `tdgl-data-viewer` (FastAPI + viewer, full dependencies) and `tdgl-generator` (CLI only, lightweight). A GitHub Actions workflow detects path changes and builds/pushes only the images that need updating. PostgreSQL remains a standard `postgres:16` image with no custom build.

**Tech Stack:** Docker multi-stage builds, GitHub Actions, GHCR (ghcr.io), Kubernetes Deployments/Jobs

---

## 1. Cleanup

Delete legacy MLRun/iris demo files that are no longer used:

- `gitops_project.ipynb` — old iris ML pipeline notebook
- `workflow.py` — MLRun pipeline definition
- `project.yaml` — MLRun project config
- `.github/workflows/mlrun.yml` — MLRun CI workflow

No other files are affected.

## 2. Dockerfile Split

### 2a. `Dockerfile.data-viewer`

Replaces the current monolithic `Dockerfile`. Installs all dependencies from `pyproject.toml` and copies only `tdgl_data/`.

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY tdgl_data/ tdgl_data/
EXPOSE 8000
CMD ["uvicorn", "tdgl_data.dev_app:create_dev_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

### 2b. `Dockerfile.generator`

New lightweight image. Only needs `numpy` and `httpx`. Copies `tdgl_generator/` and the minimal `tdgl_data/` files needed for the `synthetic` module import.

```dockerfile
FROM python:3.13-slim
WORKDIR /app
COPY tdgl_generator/ tdgl_generator/
COPY tdgl_data/synthetic.py tdgl_data/__init__.py tdgl_data/
RUN pip install --no-cache-dir numpy httpx
CMD ["python", "-m", "tdgl_generator.cli"]
```

### 2c. Remove old `Dockerfile`

Delete the monolithic `Dockerfile` after the two new ones are in place.

## 3. K8s Manifest Updates

Update image references in K8s manifests from `ghcr.io/fangrh/tdgl-flow:latest` to the new image names:

- `k8s/data-viewer/deployment.yaml`: `image: ghcr.io/fangrh/tdgl-data-viewer:latest`
- `k8s/generator/job.yaml`: `image: ghcr.io/fangrh/tdgl-generator:latest`

## 4. GitHub Actions CI Workflow

**File:** `.github/workflows/ci.yml`

**Triggers:**
- `push` to `main` and `feature/*` branches
- `pull_request` to `main`

**Single job `build-and-push`:**
1. Checkout repo
2. Detect changed paths using `git diff`:
   - If `tdgl_data/` changed → set `build_viewer=true`
   - If `tdgl_generator/` changed (or `tdgl_data/synthetic.py` since generator depends on it) → set `build_generator=true`
   - If nothing changed in either → skip build
3. Login to GHCR using `GITHUB_TOKEN`
4. Build and push `tdgl-data-viewer` (if changed):
   - `docker build -f Dockerfile.data-viewer -t ghcr.io/fangrh/tdgl-data-viewer:latest -t ghcr.io/fangrh/tdgl-data-viewer:{short-sha} .`
   - `docker push ghcr.io/fangrh/tdgl-data-viewer --all-tags`
5. Build and push `tdgl-generator` (if changed):
   - `docker build -f Dockerfile.generator -t ghcr.io/fangrh/tdgl-generator:latest -t ghcr.io/fangrh/tdgl-generator:{short-sha} .`
   - `docker push ghcr.io/fangrh/tdgl-generator --all-tags`

**GHCR permissions:** The `GITHUB_TOKEN` needs `packages: write` permission. Set in the workflow with `permissions: contents: read; packages: write`.

# Project Reorganization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the project into a service-per-directory layout that scales for adding new deployments, jobs, and services.

**Architecture:** Python packages (`tdgl_data/`, `tdgl_generator/`) stay at the repo root so import paths remain unchanged. Each deployable component gets its own directory under `services/` containing its Dockerfile and `k8s/` manifests. Infrastructure components (PostgreSQL, namespace) live under `infra/`. Docker build context is always the repo root, so Dockerfile COPY paths don't change.

**Tech Stack:** Docker, GitHub Actions, K8s manifests, Python packages

---

## File Structure

### Before

```
Dockerfile.data-viewer          → move into services/data-viewer/Dockerfile
Dockerfile.generator            → move into services/generator/Dockerfile
k8s/data-viewer/deployment.yaml → move into services/data-viewer/k8s/deployment.yaml
k8s/data-viewer/service.yaml    → move into services/data-viewer/k8s/service.yaml
k8s/data-viewer/secret.yaml     → move into services/data-viewer/k8s/secret.yaml
k8s/generator/job.yaml          → move into services/generator/k8s/job.yaml
k8s/namespace.yaml              → move into infra/namespace.yaml
k8s/postgresql/statefulset.yaml → move into infra/postgresql/k8s/statefulset.yaml
k8s/postgresql/service.yaml     → move into infra/postgresql/k8s/service.yaml
k8s/postgresql/secret.yaml      → move into infra/postgresql/k8s/secret.yaml
k8s/postgresql/pvc.yaml         → move into infra/postgresql/k8s/pvc.yaml
```

### After

```
kubeflow-tdgl/
  pyproject.toml
  tdgl_data/                  # shared library (unchanged)
  tdgl_generator/             # generator package (unchanged)
  services/
    data-viewer/
      Dockerfile
      k8s/
        deployment.yaml
        service.yaml
        secret.yaml
    generator/
      Dockerfile
      k8s/
        job.yaml
  infra/
    namespace.yaml
    postgresql/
      k8s/
        statefulset.yaml
        service.yaml
        secret.yaml
        pvc.yaml
  tests/
  .github/workflows/ci.yml
```

---

### Task 1: Create services/data-viewer directory structure

**Files:**
- Move: `Dockerfile.data-viewer` → `services/data-viewer/Dockerfile`
- Move: `k8s/data-viewer/deployment.yaml` → `services/data-viewer/k8s/deployment.yaml`
- Move: `k8s/data-viewer/service.yaml` → `services/data-viewer/k8s/service.yaml`
- Move: `k8s/data-viewer/secret.yaml` → `services/data-viewer/k8s/secret.yaml`

- [ ] **Step 1: Create directories and move files**

```bash
mkdir -p services/data-viewer/k8s
git mv Dockerfile.data-viewer services/data-viewer/Dockerfile
git mv k8s/data-viewer/deployment.yaml services/data-viewer/k8s/deployment.yaml
git mv k8s/data-viewer/service.yaml services/data-viewer/k8s/service.yaml
git mv k8s/data-viewer/secret.yaml services/data-viewer/k8s/secret.yaml
```

- [ ] **Step 2: Verify files moved correctly**

Run: `find services/data-viewer -type f | sort`
Expected:
```
services/data-viewer/Dockerfile
services/data-viewer/k8s/deployment.yaml
services/data-viewer/k8s/secret.yaml
services/data-viewer/k8s/service.yaml
```

- [ ] **Step 3: Commit**

```bash
git add services/
git commit -m "refactor: move data-viewer into services/ directory"
```

---

### Task 2: Create services/generator directory structure

**Files:**
- Move: `Dockerfile.generator` → `services/generator/Dockerfile`
- Move: `k8s/generator/job.yaml` → `services/generator/k8s/job.yaml`

- [ ] **Step 1: Create directories and move files**

```bash
mkdir -p services/generator/k8s
git mv Dockerfile.generator services/generator/Dockerfile
git mv k8s/generator/job.yaml services/generator/k8s/job.yaml
```

- [ ] **Step 2: Verify files moved correctly**

Run: `find services/generator -type f | sort`
Expected:
```
services/generator/Dockerfile
services/generator/k8s/job.yaml
```

- [ ] **Step 3: Commit**

```bash
git add services/
git commit -m "refactor: move generator into services/ directory"
```

---

### Task 3: Create infra directory structure and move PostgreSQL manifests

**Files:**
- Move: `k8s/namespace.yaml` → `infra/namespace.yaml`
- Move: `k8s/postgresql/statefulset.yaml` → `infra/postgresql/k8s/statefulset.yaml`
- Move: `k8s/postgresql/service.yaml` → `infra/postgresql/k8s/service.yaml`
- Move: `k8s/postgresql/secret.yaml` → `infra/postgresql/k8s/secret.yaml`
- Move: `k8s/postgresql/pvc.yaml` → `infra/postgresql/k8s/pvc.yaml`

- [ ] **Step 1: Create directories and move files**

```bash
mkdir -p infra/postgresql/k8s
git mv k8s/namespace.yaml infra/namespace.yaml
git mv k8s/postgresql/statefulset.yaml infra/postgresql/k8s/statefulset.yaml
git mv k8s/postgresql/service.yaml infra/postgresql/k8s/service.yaml
git mv k8s/postgresql/secret.yaml infra/postgresql/k8s/secret.yaml
git mv k8s/postgresql/pvc.yaml infra/postgresql/k8s/pvc.yaml
```

- [ ] **Step 2: Remove empty k8s/ directory**

```bash
find k8s/ -type d -empty -delete
rmdir k8s/data-viewer k8s/generator k8s/postgresql 2>/dev/null
rmdir k8s 2>/dev/null
git rm -r k8s/ 2>/dev/null || true
```

If `k8s/` still exists after the above (git may have kept empty dirs), remove it:
```bash
rm -rf k8s/
```

- [ ] **Step 3: Verify the infra structure**

Run: `find infra -type f | sort`
Expected:
```
infra/namespace.yaml
infra/postgresql/k8s/pvc.yaml
infra/postgresql/k8s/secret.yaml
infra/postgresql/k8s/service.yaml
infra/postgresql/k8s/statefulset.yaml
```

- [ ] **Step 4: Commit**

```bash
git add infra/
git commit -m "refactor: move infrastructure manifests into infra/ directory"
```

---

### Task 4: Update GitHub Actions CI workflow

**Files:**
- Modify: `.github/workflows/ci.yml`

The Dockerfiles moved from root to `services/data-viewer/Dockerfile` and `services/generator/Dockerfile`. The CI workflow needs updated Dockerfile paths and path-detection rules for the new `services/` directories.

- [ ] **Step 1: Rewrite ci.yml**

Replace the entire content of `.github/workflows/ci.yml` with:

```yaml
name: Build and Push Images

on:
  push:
    branches: [main, feature/*]
  pull_request:
    branches: [main]

permissions:
  contents: read
  packages: write

jobs:
  build-and-push:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Detect changed paths
        id: changes
        run: |
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            BASE="${{ github.event.pull_request.base.sha }}"
          else
            BASE="${{ github.event.before }}"
          fi
          if [ "$BASE" = "0000000000000000000000000000000000000000" ]; then
            BASE="HEAD~1"
          fi
          CHANGES=$(git diff --name-only "$BASE" HEAD 2>/dev/null || git diff --name-only HEAD~1 HEAD)
          echo "$CHANGES"
          VIEWER=false
          GENERATOR=false
          echo "$CHANGES" | grep -qE "^(tdgl_data/|services/data-viewer/)" && VIEWER=true
          echo "$CHANGES" | grep -qE "^(tdgl_data/synthetic\.py|tdgl_generator/|services/generator/)" && GENERATOR=true
          echo "build_viewer=$VIEWER" >> "$GITHUB_OUTPUT"
          echo "build_generator=$GENERATOR" >> "$GITHUB_OUTPUT"
          echo "Viewer changed: $VIEWER"
          echo "Generator changed: $GENERATOR"

      - name: Log in to GHCR
        if: steps.changes.outputs.build_viewer == 'true' || steps.changes.outputs.build_generator == 'true'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push data-viewer
        if: steps.changes.outputs.build_viewer == 'true'
        run: |
          SHA=$(git rev-parse --short HEAD)
          docker build -f services/data-viewer/Dockerfile \
            -t ghcr.io/fangrh/tdgl-data-viewer:latest \
            -t ghcr.io/fangrh/tdgl-data-viewer:$SHA \
            .
          docker push ghcr.io/fangrh/tdgl-data-viewer --all-tags

      - name: Build and push generator
        if: steps.changes.outputs.build_generator == 'true'
        run: |
          SHA=$(git rev-parse --short HEAD)
          docker build -f services/generator/Dockerfile \
            -t ghcr.io/fangrh/tdgl-generator:latest \
            -t ghcr.io/fangrh/tdgl-generator:$SHA \
            .
          docker push ghcr.io/fangrh/tdgl-generator --all-tags
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: update workflow for services/ directory layout"
```

---

### Task 5: Update .dockerignore and clean up stray files

**Files:**
- Modify: `.dockerignore`
- Delete: `get-docker.sh` (stray file not part of the project)
- Delete: `docs/mlrun.png`, `docs/pipeline.png`, `docs/pr.png`, `docs/slack.png`, `docs/flow.png`, `docs/use-this.png` (legacy MLRun images, no longer referenced)
- Modify: `README.md` (update project structure and deploy commands)

- [ ] **Step 1: Delete stray get-docker.sh**

```bash
git rm get-docker.sh
```

- [ ] **Step 2: Delete legacy MLRun doc images**

```bash
git rm docs/mlrun.png docs/pipeline.png docs/pr.png docs/slack.png docs/flow.png docs/use-this.png
```

- [ ] **Step 3: Update .dockerignore**

Replace the content of `.dockerignore` with:

```
data/
tests/
__pycache__/
*.pyc
.git/
.claude/
.pytest_cache/
.ruff_cache/
*.egg-info/
infra/
docs/
services/*/k8s/
```

- [ ] **Step 4: Update README.md project structure section**

In `README.md`, replace the **Project Structure** section and **Kubernetes Deployment** section. Find the line starting with `## Project Structure` through the end of the ``` code block, and replace with:

````markdown
## Project Structure

```
tdgl_data/                Shared library (models, schemas, API, synthetic)
tdgl_generator/           Generator package (CLI + web app)
services/
  data-viewer/            Data service + viewer
    Dockerfile
    k8s/                  deployment, service, secret
  generator/              Test data generator
    Dockerfile
    k8s/                  job manifest
infra/
  namespace.yaml
  postgresql/k8s/         statefulset, pvc, service, secret
tests/                    pytest test suite
```
````

And replace the **Kubernetes Deployment** section:

````markdown
## Kubernetes Deployment

```bash
# Build and load image
docker build -f services/data-viewer/Dockerfile -t ghcr.io/fangrh/tdgl-data-viewer:latest .
docker build -f services/generator/Dockerfile -t ghcr.io/fangrh/tdgl-generator:latest .

# Deploy infrastructure
kubectl apply -f infra/namespace.yaml
kubectl apply -f infra/postgresql/k8s/

# Deploy services
kubectl apply -f services/data-viewer/k8s/

# Port-forward to access viewer
kubectl port-forward -n tdgl svc/data-viewer 8000:80
```
````

- [ ] **Step 5: Run tests to verify nothing broke**

Run: `python -m pytest tests/ -x -q`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add .dockerignore README.md
git commit -m "chore: clean up stray files, update .dockerignore and README for new layout"
```

---

### Task 6: Verify Docker builds still work

**Files:** None (verification only)

- [ ] **Step 1: Build data-viewer image from new location**

Run: `docker build -f services/data-viewer/Dockerfile -t tdgl-data-viewer:test . 2>&1 | tail -5`
Expected: `Successfully tagged tdgl-data-viewer:test`

- [ ] **Step 2: Build generator image from new location**

Run: `docker build -f services/generator/Dockerfile -t tdgl-generator:test . 2>&1 | tail -5`
Expected: `Successfully tagged tdgl-generator:test`

- [ ] **Step 3: Verify generator import still works**

Run: `docker run --rm tdgl-generator:test python -c "from tdgl_data.synthetic import generate_synthetic_run; print('OK')"`
Expected: `OK`

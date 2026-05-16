# CI Cleanup and Image Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete legacy MLRun files, split the monolithic Docker image into `tdgl-data-viewer` and `tdgl-generator`, update K8s manifests to reference new image names, and add a GitHub Actions CI workflow that builds and pushes both images to GHCR on push/PR.

**Architecture:** Two Dockerfiles replace the single monolithic one. `Dockerfile.data-viewer` installs full dependencies from `pyproject.toml` and copies only `tdgl_data/`. `Dockerfile.generator` is lightweight (only numpy + httpx) and copies `tdgl_generator/` plus `tdgl_data/synthetic.py`. A single GitHub Actions workflow detects changed paths and builds/pushes only the affected images.

**Tech Stack:** Docker, GitHub Actions, GHCR (ghcr.io), K8s Deployments/Jobs

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Delete | `gitops_project.ipynb` | Legacy MLRun notebook |
| Delete | `workflow.py` | Legacy MLRun pipeline |
| Delete | `project.yaml` | Legacy MLRun project config |
| Delete | `.github/workflows/mlrun.yml` | Legacy MLRun workflow |
| Delete | `Dockerfile` | Old monolithic Dockerfile |
| Create | `Dockerfile.data-viewer` | Data-viewer image (full deps) |
| Create | `Dockerfile.generator` | Generator image (lightweight) |
| Modify | `k8s/data-viewer/deployment.yaml` | Update image reference |
| Modify | `k8s/generator/job.yaml` | Update image reference |
| Modify | `.dockerignore` | Keep as-is (already excludes data/, docs/, tests/) |
| Create | `.github/workflows/ci.yml` | CI build + push workflow |

---

### Task 1: Delete legacy MLRun files

**Files:**
- Delete: `gitops_project.ipynb`
- Delete: `workflow.py`
- Delete: `project.yaml`
- Delete: `.github/workflows/mlrun.yml`

- [ ] **Step 1: Delete the four legacy files**

```bash
git rm gitops_project.ipynb workflow.py project.yaml .github/workflows/mlrun.yml
```

- [ ] **Step 2: Verify files are gone**

Run: `git status`
Expected: Four deleted files shown in staging area

- [ ] **Step 3: Run existing tests to confirm nothing broke**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && python -m pytest tests/ -x -q`
Expected: All tests PASS (the deleted files had no test dependencies)

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove legacy MLRun/iris demo files"
```

---

### Task 2: Create Dockerfile.data-viewer

**Files:**
- Create: `Dockerfile.data-viewer`

- [ ] **Step 1: Write Dockerfile.data-viewer**

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY tdgl_data/ tdgl_data/

EXPOSE 8000
CMD ["uvicorn", "tdgl_data.dev_app:create_dev_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Verify Dockerfile builds locally**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && docker build -f Dockerfile.data-viewer -t tdgl-data-viewer:test . 2>&1 | tail -5`
Expected: Build succeeds, final message shows `Successfully tagged tdgl-data-viewer:test`

- [ ] **Step 3: Verify the image starts**

Run: `docker run --rm -d --name test-viewer -p 8999:8000 tdgl-data-viewer:test && sleep 3 && curl -s -o /dev/null -w "%{http_code}" http://localhost:8999/api/runs && docker stop test-viewer`
Expected: HTTP 200

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.data-viewer
git commit -m "build: add Dockerfile.data-viewer for data service image"
```

---

### Task 3: Create Dockerfile.generator

**Files:**
- Create: `Dockerfile.generator`

- [ ] **Step 1: Write Dockerfile.generator**

The generator CLI (`tdgl_generator/cli.py`) imports `tdgl_data.synthetic`, which only depends on `numpy`. The CLI itself uses `httpx`. So only `numpy` and `httpx` are needed.

```dockerfile
FROM python:3.13-slim

WORKDIR /app

COPY tdgl_data/__init__.py tdgl_data/
COPY tdgl_data/synthetic.py tdgl_data/
COPY tdgl_generator/ tdgl_generator/

RUN pip install --no-cache-dir numpy httpx

CMD ["python", "-m", "tdgl_generator.cli"]
```

- [ ] **Step 2: Verify Dockerfile builds locally**

Run: `cd /mnt/c/Users/photo/Photonics_Group/Ruihuan/kubeflow-tdgl && docker build -f Dockerfile.generator -t tdgl-generator:test . 2>&1 | tail -5`
Expected: Build succeeds, final message shows `Successfully tagged tdgl-generator:test`

- [ ] **Step 3: Verify the image can import the generator module**

Run: `docker run --rm tdgl-generator:test python -c "from tdgl_data.synthetic import generate_synthetic_run; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add Dockerfile.generator
git commit -m "build: add lightweight Dockerfile.generator for test data generation"
```

---

### Task 4: Remove old monolithic Dockerfile and update K8s manifests

**Files:**
- Delete: `Dockerfile`
- Modify: `k8s/data-viewer/deployment.yaml` (line 18: change image tag)
- Modify: `k8s/generator/job.yaml` (line 18: change image tag)

- [ ] **Step 1: Delete old Dockerfile**

```bash
git rm Dockerfile
```

- [ ] **Step 2: Update data-viewer deployment image reference**

In `k8s/data-viewer/deployment.yaml`, change line 18 from:

```yaml
          image: ghcr.io/fangrh/tdgl-flow:latest
```

to:

```yaml
          image: ghcr.io/fangrh/tdgl-data-viewer:latest
```

- [ ] **Step 3: Update generator job image reference**

In `k8s/generator/job.yaml`, change line 18 from:

```yaml
          image: ghcr.io/fangrh/tdgl-flow:latest
```

to:

```yaml
          image: ghcr.io/fangrh/tdgl-generator:latest
```

- [ ] **Step 4: Verify manifests are valid YAML**

Run: `python -c "import yaml; yaml.safe_load(open('k8s/data-viewer/deployment.yaml')); yaml.safe_load(open('k8s/generator/job.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add k8s/data-viewer/deployment.yaml k8s/generator/job.yaml
git commit -m "refactor: remove monolithic Dockerfile, update K8s manifests for split images"
```

---

### Task 5: Add GitHub Actions CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the CI workflow**

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
          # Fallback: if before is all zeros (new branch), diff against HEAD~1
          if [ "$BASE" = "0000000000000000000000000000000000000000" ]; then
            BASE="HEAD~1"
          fi
          CHANGES=$(git diff --name-only "$BASE" HEAD 2>/dev/null || git diff --name-only HEAD~1 HEAD)
          echo "$CHANGES"
          VIEWER=false
          GENERATOR=false
          echo "$CHANGES" | grep -q "^tdgl_data/" && VIEWER=true
          echo "$CHANGES" | grep -q "^tdgl_data/synthetic.py" && GENERATOR=true
          echo "$CHANGES" | grep -q "^tdgl_generator/" && GENERATOR=true
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
          docker build -f Dockerfile.data-viewer \
            -t ghcr.io/fangrh/tdgl-data-viewer:latest \
            -t ghcr.io/fangrh/tdgl-data-viewer:$SHA \
            .
          docker push ghcr.io/fangrh/tdgl-data-viewer --all-tags

      - name: Build and push generator
        if: steps.changes.outputs.build_generator == 'true'
        run: |
          SHA=$(git rev-parse --short HEAD)
          docker build -f Dockerfile.generator \
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
git commit -m "ci: add GitHub Actions workflow to build and push split images"
```

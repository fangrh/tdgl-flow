# Flux GitOps CD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up Flux-based pull GitOps so that GitHub Actions builds images, commits the new SHA tag into K8s manifests, and Flux auto-deploys the changes. Also adds Traefik Ingress for external access.

**Architecture:** Flux watches the `fangrh/tdgl-flow` repo. GitHub Actions builds images and updates manifest tags via commit-back to `main`. Traefik (already on k3s) exposes the data-viewer via Ingress. kustomize composes manifests for Flux.

**Tech Stack:** Flux v2, kustomize, Traefik Ingress, GitHub Actions, GHCR

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `services/data-viewer/k8s/deployment.yaml` | Pin image tag from `:latest` to SHA |
| Modify | `services/generator/k8s/job.yaml` | Pin image tag from `:latest` to SHA |
| Create | `infra/kustomization.yaml` | Compose infra manifests for Flux |
| Create | `infra/postgresql/k8s/kustomization.yaml` | Compose postgresql manifests |
| Create | `services/data-viewer/k8s/kustomization.yaml` | Compose data-viewer manifests |
| Create | `services/generator/k8s/kustomization.yaml` | Compose generator manifests |
| Create | `services/kustomization.yaml` | Top-level services composition |
| Create | `services/data-viewer/k8s/ingress.yaml` | Traefik Ingress for data-viewer |
| Create | `clusters/k3s/tdgl-resources.yaml` | Flux Kustomization CRDs |
| Modify | `.github/workflows/ci.yml` | SHA-only tags, manifest update, commit-back |

---

### Task 1: Pin image tags in K8s manifests

**Files:**
- Modify: `services/data-viewer/k8s/deployment.yaml` (line 18)
- Modify: `services/generator/k8s/job.yaml` (line 18)

Flux requires pinned image tags (not `:latest`) so it can detect when a deployment is needed. Pin both manifests to the current short SHA `de9bb2b` (last commit that changed the data-viewer image).

- [ ] **Step 1: Update data-viewer deployment image tag**

In `services/data-viewer/k8s/deployment.yaml`, change line 18 from:

```yaml
          image: ghcr.io/fangrh/tdgl-data-viewer:latest
```

to:

```yaml
          image: ghcr.io/fangrh/tdgl-data-viewer:de9bb2b
```

- [ ] **Step 2: Update generator job image tag**

In `services/generator/k8s/job.yaml`, change line 18 from:

```yaml
          image: ghcr.io/fangrh/tdgl-generator:latest
```

to:

```yaml
          image: ghcr.io/fangrh/tdgl-generator:de9bb2b
```

- [ ] **Step 3: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('services/data-viewer/k8s/deployment.yaml')); yaml.safe_load(open('services/generator/k8s/job.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add services/data-viewer/k8s/deployment.yaml services/generator/k8s/job.yaml
git commit -m "refactor: pin image tags to SHA for Flux GitOps"
```

---

### Task 2: Add kustomization.yaml files

**Files:**
- Create: `infra/kustomization.yaml`
- Create: `infra/postgresql/k8s/kustomization.yaml`
- Create: `services/data-viewer/k8s/kustomization.yaml`
- Create: `services/generator/k8s/kustomization.yaml`
- Create: `services/kustomization.yaml`

- [ ] **Step 1: Create `infra/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - postgresql/k8s/
```

- [ ] **Step 2: Create `infra/postgresql/k8s/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - pvc.yaml
  - secret.yaml
  - service.yaml
  - statefulset.yaml
```

- [ ] **Step 3: Create `services/data-viewer/k8s/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - deployment.yaml
  - service.yaml
  - secret.yaml
  - ingress.yaml
```

Note: `ingress.yaml` is created in Task 3. This kustomization.yaml references it ahead of time.

- [ ] **Step 4: Create `services/generator/k8s/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - job.yaml
```

- [ ] **Step 5: Create `services/kustomization.yaml`**

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - data-viewer/k8s/
  - generator/k8s/
```

- [ ] **Step 6: Verify kustomize can build all targets**

Run:
```bash
kubectl kustomize infra/ > /dev/null && echo "infra OK"
kubectl kustomize services/ > /dev/null && echo "services OK"
```
Expected: `infra OK` and `services OK`

- [ ] **Step 7: Commit**

```bash
git add infra/kustomization.yaml infra/postgresql/k8s/kustomization.yaml services/data-viewer/k8s/kustomization.yaml services/generator/k8s/kustomization.yaml services/kustomization.yaml
git commit -m "build: add kustomization.yaml for Flux GitOps"
```

---

### Task 3: Add Traefik Ingress for data-viewer

**Files:**
- Create: `services/data-viewer/k8s/ingress.yaml`

- [ ] **Step 1: Create `services/data-viewer/k8s/ingress.yaml`**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: data-viewer
  namespace: tdgl
  annotations:
    traefik.ingress.kubernetes.io/router.entrypoints: web
spec:
  rules:
    - host: tdgl.local
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: data-viewer
                port:
                  number: 80
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python -c "import yaml; yaml.safe_load(open('services/data-viewer/k8s/ingress.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add services/data-viewer/k8s/ingress.yaml
git commit -m "feat: add Traefik Ingress for data-viewer"
```

---

### Task 4: Create Flux Kustomization resources

**Files:**
- Create: `clusters/k3s/tdgl-resources.yaml`

- [ ] **Step 1: Create `clusters/k3s/tdgl-resources.yaml`**

```yaml
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: tdgl-infra
  namespace: flux-system
spec:
  interval: 5m
  sourceRef:
    kind: GitRepository
    name: flux-system
  path: ./infra
  prune: true
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: tdgl-services
  namespace: flux-system
spec:
  interval: 5m
  sourceRef:
    kind: GitRepository
    name: flux-system
  path: ./services
  prune: true
  dependsOn:
    - name: tdgl-infra
```

- [ ] **Step 2: Verify YAML is valid**

Run: `python -c "import yaml; list(yaml.safe_load_all(open('clusters/k3s/tdgl-resources.yaml'))); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add clusters/k3s/tdgl-resources.yaml
git commit -m "infra: add Flux Kustomization resources for tdgl"
```

---

### Task 5: Update CI workflow for GitOps

**Files:**
- Modify: `.github/workflows/ci.yml`

Replace the entire content of `.github/workflows/ci.yml` with the updated workflow. Key changes:
- SHA-only tags (no `:latest`)
- On push to `main`: update manifest tag and commit back
- On PR: build and push only (no manifest update)
- `contents: write` permission for commit-back
- Skip on dependabot/renovate PRs

- [ ] **Step 1: Replace `.github/workflows/ci.yml`**

```yaml
name: Build and Push Images

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

permissions:
  contents: write
  packages: write

jobs:
  build-and-push:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4
        with:
          token: ${{ secrets.GITHUB_TOKEN }}

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
            -t ghcr.io/fangrh/tdgl-data-viewer:$SHA \
            .
          docker push ghcr.io/fangrh/tdgl-data-viewer:$SHA

      - name: Build and push generator
        if: steps.changes.outputs.build_generator == 'true'
        run: |
          SHA=$(git rev-parse --short HEAD)
          docker build -f services/generator/Dockerfile \
            -t ghcr.io/fangrh/tdgl-generator:$SHA \
            .
          docker push ghcr.io/fangrh/tdgl-generator:$SHA

      - name: Update manifest tags and commit
        if: github.ref == 'refs/heads/main' && (steps.changes.outputs.build_viewer == 'true' || steps.changes.outputs.build_generator == 'true')
        run: |
          SHA=$(git rev-parse --short HEAD)
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          if [ "${{ steps.changes.outputs.build_viewer }}" = "true" ]; then
            sed -i "s|image: ghcr.io/fangrh/tdgl-data-viewer:.*|image: ghcr.io/fangrh/tdgl-data-viewer:$SHA|" services/data-viewer/k8s/deployment.yaml
            echo "Updated data-viewer tag to $SHA"
          fi
          if [ "${{ steps.changes.outputs.build_generator }}" = "true" ]; then
            sed -i "s|image: ghcr.io/fangrh/tdgl-generator:.*|image: ghcr.io/fangrh/tdgl-generator:$SHA|" services/generator/k8s/job.yaml
            echo "Updated generator tag to $SHA"
          fi
          git add services/data-viewer/k8s/deployment.yaml services/generator/k8s/job.yaml
          git diff --cached --quiet || git commit -m "ci: update image tags to $SHA"
          git push
```

- [ ] **Step 2: Validate YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: update workflow for GitOps — SHA tags, manifest commit-back"
```

---

### Task 6: Push all changes and bootstrap Flux

This task must be run by the user interactively because Flux bootstrap requires GitHub authentication.

- [ ] **Step 1: Push all commits to GitHub**

```bash
git push origin main
```

- [ ] **Step 2: Install Flux CLI (if not already installed)**

```bash
curl -s https://fluxcd.io/install.sh | sudo bash
```

Or on WSL2:
```bash
curl -LO https://github.com/fluxcd/flux2/releases/latest/download/flux_2.6.3_linux_amd64.tar.gz
tar -xzf flux_2.6.3_linux_amd64.tar.gz
sudo mv flux /usr/local/bin/
```

Verify: `flux --version`

- [ ] **Step 3: Check cluster prerequisites**

```bash
flux check --pre
```
Expected: All checks pass

- [ ] **Step 4: Bootstrap Flux**

This command requires a GitHub personal access token with repo and read:org scopes.

```bash
export GITHUB_TOKEN=<your-github-token>
flux bootstrap github \
  --owner=fangrh \
  --repository=tdgl-flow \
  --branch=main \
  --path=clusters/k3s \
  --personal
```

This creates:
- `flux-system` namespace on the cluster
- `clusters/k3s/flux-system/` directory in the repo (auto-committed)
- Source controller watching the repo

- [ ] **Step 5: Apply the tdgl Kustomization resources**

```bash
kubectl apply -f clusters/k3s/tdgl-resources.yaml
```

- [ ] **Step 6: Verify Flux reconciliation**

```bash
flux get kustomizations
```
Expected: Both `tdgl-infra` and `tdgl-services` show `Ready` status with `True` condition.

```bash
flux get sources git
```
Expected: `flux-system` shows `Ready` status.

- [ ] **Step 7: Verify pods are running**

```bash
kubectl get pods -n tdgl
```
Expected: `data-viewer-*` Running, `postgres-0` Running.

- [ ] **Step 8: Set up local DNS for Ingress**

Add to `/etc/hosts` (Windows: `C:\Windows\System32\drivers\etc\hosts`):
```
127.0.0.1 tdgl.local
```

Then access the viewer at `http://tdgl.local/viewer`.

Note: On k3s with Traefik, you may need to port-forward the Traefik entrypoint or ensure the node IP resolves. If `tdgl.local` doesn't work directly, try:
```bash
kubectl port-forward -n kube-system svc/traefik 80:80 --address 0.0.0.0
```

Then open `http://tdgl.local/viewer` in the browser.

- [ ] **Step 9: Verify end-to-end GitOps flow**

Make a small change to `tdgl_data/`, push to `main`, and watch:
1. GitHub Actions builds a new image with the new SHA tag
2. Actions updates the manifest tag and commits back
3. Flux detects the commit and redeploys

```bash
flux get kustomizations --watch
```

# FluxCD to ArgoCD Migration Design

## Context

The kubeflow-tdgl project uses FluxCD configuration files (in `clusters/k3s/tdgl-resources.yaml`) to GitOps-manage `infra/` and `services/` via Kustomize. However, FluxCD was never actually bootstrapped in the cluster. The project already uses Argo Workflows for simulation pipelines. Migrating to ArgoCD consolidates the stack under the Argo ecosystem and eliminates the unused FluxCD dependency.

## Architecture

ArgoCD is installed via Helm in the `argocd` namespace. Two `Application` resources watch the Git repo and auto-sync:

```
Git repo (main branch)
  ├── clusters/argocd/
  │   ├── helm-values.yaml          # ArgoCD Helm values (resource limits)
  │   ├── apps/
  │   │   ├── infra.yaml            # Application: tdgl-infra → ./infra (Kustomize)
  │   │   └── services.yaml         # Application: tdgl-services → ./services (Kustomize)
  │   └── kustomization.yaml        # Applies the two Application manifests
  ├── infra/                         # Unchanged — namespace, postgresql, istio gateway
  └── services/                      # Unchanged — data-viewer, generator
```

### Sync behavior

- Auto-sync enabled on both Applications with self-heal
- `infra` syncs on wave 0, `services` syncs on wave 1 (preserves FluxCD's `dependsOn` chain)
- Prune enabled (matches FluxCD's `prune: true`)

## File Changes

### New files

1. **`clusters/argocd/helm-values.yaml`** — ArgoCD Helm values:
   - Resource limits for server, repo-server, controller, redis
   - Server service type: ClusterIP
   - Dex disabled (single-user dev setup)

2. **`clusters/argocd/apps/infra.yaml`** — ArgoCD Application for infrastructure:
   - Source: Git repo at `./infra`, Kustomize build
   - Destination: cluster `https://kubernetes.default.svc`, namespace `tdgl`
   - Sync policy: automated + self-heal + prune
   - Sync wave: 0

3. **`clusters/argocd/apps/services.yaml`** — ArgoCD Application for services:
   - Source: Git repo at `./services`, Kustomize build
   - Destination: same as infra
   - Sync policy: automated + self-heal + prune
   - Sync wave: 1 (ensures infra is ready first)

4. **`clusters/argocd/kustomization.yaml`** — Kustomize wrapping the two Application manifests

### Deleted files

5. **`clusters/k3s/tdgl-resources.yaml`** — FluxCD Kustomization resources (no longer needed)

### Modified files

6. **`Makefile`** — Replace FluxCD commands with ArgoCD equivalents:
   - Remove: `install-flux`, `check`, `bootstrap`, `verify`, `reconcile`, `suspend`, `resume`, `clean`
   - Add: `install-argocd` (Helm install), `verify-argocd` (check app sync status), `port-forward-argocd` (UI on 8080)
   - Keep: `status`, `apply`, `install-argo`, `verify-argo`, `port-forward-argo`, `submit-workflow`, `disable-traefik`

## ArgoCD Access

- Port-forward: `make port-forward-argocd` → `http://localhost:8080`
- Default credentials: admin / (auto-generated, retrieved via `argocd admin dashboard -n argocd` or kubectl secret)

## Resource Budget

| Component | CPU (req/lim) | Memory (req/lim) |
|---|---|---|
| ArgoCD Application Controller | 100m / 500m | 256Mi / 512Mi |
| ArgoCD Server | 50m / 250m | 128Mi / 256Mi |
| ArgoCD Repo Server | 50m / 250m | 128Mi / 256Mi |
| Redis | 50m / 250m | 64Mi / 128Mi |
| **Total** | **250m / 1250m** | **576Mi / 1152Mi** |

Fits comfortably within the 12 CPU / 32GB RAM node.

## Verification

1. `kubectl get pods -n argocd` — all components Running
2. `argocd app list` — shows tdgl-infra and tdgl-services
3. `argocd app get tdgl-infra` — Status: Synced, Health: Healthy
4. `argocd app get tdgl-services` — Status: Synced, Health: Healthy
5. UI at `http://localhost:8080` — both apps visible with green status

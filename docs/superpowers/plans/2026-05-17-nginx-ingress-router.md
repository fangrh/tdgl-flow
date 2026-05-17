# Nginx Ingress Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up Traefik IngressRoutes so all services are accessible via hostnames in a browser at http://172.22.133.208, no port-forwarding needed.

**Architecture:** Traefik is already running as the k3s ingress controller on port 80 (LoadBalancer at 172.22.133.208). Create three IngressRoute resources — one per service — each matching a different hostname. Add entries to `/etc/hosts` so the hostnames resolve locally.

**Tech Stack:** Traefik IngressRoute (k3s built-in), Kubernetes

---

### Current services

| Browser URL | Service | Namespace:Service:Port |
|---|---|---|
| http://tdgl.local | Data Viewer (FastAPI heatmap) | tdgl/data-viewer:80 |
| http://argocd.local | ArgoCD UI (GitOps dashboard) | argocd/argocd-server:80 |
| http://workflows.local | Argo Workflows UI | argo/argo-workflows-server:2746 |

---

### Task 1: Create Traefik IngressRoute for data-viewer

**Files:**
- Create: `services/data-viewer/k8s/ingressroute.yaml`
- Modify: `services/data-viewer/k8s/kustomization.yaml`

- [ ] **Step 1: Create IngressRoute manifest**

Create `services/data-viewer/k8s/ingressroute.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: data-viewer
  namespace: tdgl
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`tdgl.local`)
      kind: Rule
      services:
        - name: data-viewer
          port: 80
```

- [ ] **Step 2: Add to kustomization**

Edit `services/data-viewer/k8s/kustomization.yaml`, add `- ingressroute.yaml` to resources:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - pvc.yaml
  - deployment.yaml
  - service.yaml
  - secret.yaml
  - ingressroute.yaml
```

- [ ] **Step 3: Commit**

```bash
git add services/data-viewer/k8s/ingressroute.yaml services/data-viewer/k8s/kustomization.yaml
git commit -m "feat: add Traefik IngressRoute for data-viewer"
```

---

### Task 2: Create Traefik IngressRoutes for ArgoCD and Argo Workflows

**Files:**
- Create: `infra/ingress/argocd.yaml`
- Create: `infra/ingress/argo-workflows.yaml`
- Create: `infra/ingress/kustomization.yaml`
- Modify: `infra/kustomization.yaml`

- [ ] **Step 1: Create directory**

```bash
mkdir -p infra/ingress
```

- [ ] **Step 2: Create ArgoCD IngressRoute**

Create `infra/ingress/argocd.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: argocd
  namespace: argocd
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`argocd.local`)
      kind: Rule
      services:
        - name: argocd-server
          port: 80
```

- [ ] **Step 3: Create Argo Workflows IngressRoute**

Create `infra/ingress/argo-workflows.yaml`:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: argo-workflows
  namespace: argo
spec:
  entryPoints:
    - web
  routes:
    - match: Host(`workflows.local`)
      kind: Rule
      services:
        - name: argo-workflows-server
          port: 2746
```

- [ ] **Step 4: Create ingress kustomization**

Create `infra/ingress/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - argocd.yaml
  - argo-workflows.yaml
```

- [ ] **Step 5: Add ingress to infra kustomization**

Edit `infra/kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - namespace.yaml
  - postgresql/k8s/
  - ingress/
```

- [ ] **Step 6: Commit**

```bash
git add infra/ingress/ infra/kustomization.yaml
git commit -m "feat: add Traefik IngressRoutes for ArgoCD and Argo Workflows"
```

---

### Task 3: Clean up old orphaned Ingress and add /etc/hosts entries

**Files:** None (cluster operations + local system config)

- [ ] **Step 1: Delete orphaned Ingress**

```bash
kubectl delete ingress tdgl-run-preview -n tdgl --ignore-not-found
```

Expected: `ingress.networking.k8s.io "tdgl-run-preview" deleted`

- [ ] **Step 2: Add hostnames to /etc/hosts**

This requires admin access. The user should run:

```bash
sudo bash -c 'echo "172.22.133.208 tdgl.local argocd.local workflows.local" >> /etc/hosts'
```

Or on Windows, add to `C:\Windows\System32\drivers\etc\hosts`:

```
172.22.133.208 tdgl.local argocd.local workflows.local
```

- [ ] **Step 3: Push to GitHub so ArgoCD syncs**

```bash
git push origin main
```

- [ ] **Step 4: Verify all three services in browser**

```bash
# Quick check via curl
curl -s -o /dev/null -w "%{http_code}" http://tdgl.local        # Expected: 200
curl -s -o /dev/null -w "%{http_code}" http://argocd.local      # Expected: 302 (redirect to login)
curl -s -o /dev/null -w "%{http_code}" http://workflows.local   # Expected: 200
```

Open browser to:
- http://tdgl.local — Data Viewer
- http://argocd.local — ArgoCD (admin / password from `make install-argocd`)
- http://workflows.local — Argo Workflows UI

---

### Task 4: Update Makefile — remove port-forward targets, add hosts info

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Remove port-forward targets and add hosts target**

Replace `port-forward-argocd` and `port-forward-argo` targets with a single `setup-hosts` target. Keep the rest unchanged.

Remove these targets:
```
port-forward-argocd:
	kubectl port-forward -n $(ARGOCD_NS) svc/argocd-server 8080:80 --address 0.0.0.0

port-forward-argo:
	kubectl port-forward -n $(ARGO_NS) svc/argo-workflows-server 8080:2746 --address 0.0.0.0
```

Add this target:
```makefile
setup-hosts:
	@echo "Add to /etc/hosts (or Windows hosts file):"
	@echo "172.22.133.208 tdgl.local argocd.local workflows.local"
	@echo ""
	@echo "Services:"
	@echo "  http://tdgl.local        - TDGL Data Viewer"
	@echo "  http://argocd.local      - ArgoCD Dashboard"
	@echo "  http://workflows.local   - Argo Workflows UI"
```

Update `.PHONY` line to replace `port-forward-argocd port-forward-argo` with `setup-hosts`.

- [ ] **Step 2: Commit**

```bash
git add Makefile
git commit -m "feat: replace port-forward targets with Traefik ingress hosts"
```

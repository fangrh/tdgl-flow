# FluxCD to ArgoCD Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace FluxCD GitOps configuration with ArgoCD, using the same Kustomize-based `infra/` and `services/` directories.

**Architecture:** ArgoCD installed via Helm in `argocd` namespace. Two `Application` resources auto-sync `./infra` and `./services` from the Git repo. Sync waves enforce dependency order (infra before services).

**Tech Stack:** ArgoCD (Helm chart), Kustomize, k3s

---

### Task 1: Create ArgoCD Helm values

**Files:**
- Create: `clusters/argocd/helm-values.yaml`

- [ ] **Step 1: Create directory and write Helm values file**

```bash
mkdir -p clusters/argocd
```

```yaml
# clusters/argocd/helm-values.yaml
# ArgoCD Helm values for single-node k3s
# helm install argocd argo/argo-cd -n argocd -f clusters/argocd/helm-values.yaml

global:
  domain: argocd.local

server:
  service:
    type: ClusterIP
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 250m
      memory: 256Mi
  insecure: true

repoServer:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
    limits:
      cpu: 250m
      memory: 256Mi

controller:
  resources:
    requests:
      cpu: 100m
      memory: 256Mi
    limits:
      cpu: 500m
      memory: 512Mi

redis:
  resources:
    requests:
      cpu: 50m
      memory: 64Mi
    limits:
      cpu: 250m
      memory: 128Mi

dex:
  enabled: false
```

- [ ] **Step 2: Commit**

```bash
git add clusters/argocd/helm-values.yaml
git commit -m "feat: add ArgoCD Helm values for single-node k3s"
```

---

### Task 2: Create ArgoCD Application manifests

**Files:**
- Create: `clusters/argocd/apps/infra.yaml`
- Create: `clusters/argocd/apps/services.yaml`
- Create: `clusters/argocd/kustomization.yaml`

- [ ] **Step 1: Create apps directory and write infra Application**

```bash
mkdir -p clusters/argocd/apps
```

```yaml
# clusters/argocd/apps/infra.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: tdgl-infra
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "0"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/fangrh/tdgl-flow.git
    targetRevision: main
    path: infra
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 2: Write services Application**

```yaml
# clusters/argocd/apps/services.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: tdgl-services
  namespace: argocd
  annotations:
    argocd.argoproj.io/sync-wave: "1"
  finalizers:
    - resources-finalizer.argocd.argoproj.io
spec:
  project: default
  source:
    repoURL: https://github.com/fangrh/tdgl-flow.git
    targetRevision: main
    path: services
  destination:
    server: https://kubernetes.default.svc
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
      - CreateNamespace=true
```

- [ ] **Step 3: Write kustomization.yaml**

```yaml
# clusters/argocd/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - apps/infra.yaml
  - apps/services.yaml
```

- [ ] **Step 4: Commit**

```bash
git add clusters/argocd/
git commit -m "feat: add ArgoCD Application manifests for infra and services"
```

---

### Task 3: Update Makefile — replace FluxCD with ArgoCD

**Files:**
- Modify: `Makefile` (full rewrite of FluxCD targets, add ArgoCD targets)

- [ ] **Step 1: Rewrite Makefile**

Replace the entire Makefile with:

```makefile
NAMESPACE   := tdgl
ARGO_NS     := argo
ARGOCD_NS   := argocd
ARGO_VALUES := infra/argo-workflows/helm-values.yaml
ARGOCD_VALUES := clusters/argocd/helm-values.yaml

.PHONY: install-argo verify-argo port-forward-argo submit-workflow install-argocd verify-argocd port-forward-argocd apply status disable-traefik

# Cluster bootstrap

install-argocd:
	@echo "==> Adding ArgoCD Helm repo..."
	helm repo add argocd https://argoproj.github.io/argo-helm 2>/dev/null || true
	helm repo update
	@echo "==> Creating argocd namespace..."
	kubectl create namespace $(ARGOCD_NS) --dry-run=client -o yaml | kubectl apply -f -
	@echo "==> Installing ArgoCD..."
	helm upgrade --install argocd argo/argo-cd \
		-n $(ARGOCD_NS) \
		-f $(ARGOCD_VALUES)
	@echo "==> Waiting for ArgoCD server..."
	kubectl wait --for=condition=Available deploy/argocd-server -n $(ARGOCD_NS) --timeout=180s
	@echo "==> Waiting for ArgoCD repo-server..."
	kubectl wait --for=condition=Available deploy/argocd-repo-server -n $(ARGOCD_NS) --timeout=180s
	@echo "==> Waiting for ArgoCD application-controller..."
	kubectl wait --for=condition=Available deploy/argocd-application-controller -n $(ARGOCD_NS) --timeout=180s
	@echo "==> Applying ArgoCD Applications..."
	kubectl apply -k clusters/argocd
	@echo "==> ArgoCD installed. Get admin password:"
	@kubectl -n $(ARGOCD_NS) get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d && echo
	@echo "==> Run 'make port-forward-argocd' to access the UI."

verify-argocd:
	@echo "=== ArgoCD Pods ==="
	kubectl get pods -n $(ARGOCD_NS)
	@echo "=== ArgoCD Applications ==="
	kubectl get applications -n $(ARGOCD_NS)
	@echo "=== TDGL Pods ==="
	kubectl get pods -n $(NAMESPACE)

port-forward-argocd:
	kubectl port-forward -n $(ARGOCD_NS) svc/argocd-server 8080:80 --address 0.0.0.0

apply:
	kubectl apply -k clusters/argocd

status:
	kubectl get pods -n $(NAMESPACE)

# Argo Workflows bootstrap

install-argo:
	@echo "==> Adding Argo Workflows Helm repo..."
	helm repo add argo https://argoproj.github.io/argo-helm 2>/dev/null || true
	helm repo update
	@echo "==> Creating argo namespace..."
	kubectl create namespace $(ARGO_NS) --dry-run=client -o yaml | kubectl apply -f -
	@echo "==> Installing Argo Workflows..."
	helm upgrade --install argo-workflows argo/argo-workflows \
		-n $(ARGO_NS) \
		-f $(ARGO_VALUES)
	@echo "==> Waiting for controller..."
	kubectl wait --for=condition=Available deploy/argo-workflows-workflow-controller -n $(ARGO_NS) --timeout=120s
	@echo "==> Waiting for server..."
	kubectl wait --for=condition=Available deploy/argo-workflows-server -n $(ARGO_NS) --timeout=120s
	@echo "==> Argo Workflows installed."

verify-argo:
	@echo "=== Argo Pods ==="
	kubectl get pods -n $(ARGO_NS)
	@echo "=== Workflows ==="
	kubectl get workflows -n $(NAMESPACE)
	@echo "=== Workflow Templates ==="
	kubectl get workflowtemplates -n $(NAMESPACE)

port-forward-argo:
	kubectl port-forward -n $(ARGO_NS) svc/argo-workflows-server 8080:2746 --address 0.0.0.0

submit-workflow:
	argo submit -n $(NAMESPACE) --from workflowtemplate/cpp-tdgl \
		-p image=127.0.0.1:5050/cpp-tdgl:latest \
		-p config-json='{}'

# Traefik management

disable-traefik:
	@echo "==> Disabling Traefik..."
	kubectl -n kube-system delete helmcharts.helm.cattle.io traefik 2>/dev/null || true
	kubectl -n kube-system delete helmcharts.helm.cattle.io traefik-crd 2>/dev/null || true
	@echo "==> Traefik disabled. Restart k3s with --disable traefik for persistence."
```

- [ ] **Step 2: Commit**

```bash
git add Makefile
git commit -m "feat: replace FluxCD Makefile targets with ArgoCD"
```

---

### Task 4: Delete FluxCD configuration

**Files:**
- Delete: `clusters/k3s/tdgl-resources.yaml`

- [ ] **Step 1: Remove FluxCD resource file**

```bash
git rm clusters/k3s/tdgl-resources.yaml
```

- [ ] **Step 2: Check if clusters/k3s/ directory is now empty and remove it if so**

```bash
ls clusters/k3s/
# If empty or only contains FluxCD files, remove:
rmdir clusters/k3s/ 2>/dev/null || rm -rf clusters/k3s/
```

- [ ] **Step 3: Commit**

```bash
git add -A clusters/k3s/
git commit -m "chore: remove FluxCD Kustomization resources"
```

---

### Task 5: Install ArgoCD and verify

**Files:** None (cluster operations only)

- [ ] **Step 1: Install ArgoCD via Helm**

```bash
helm repo add argocd https://argoproj.github.io/argo-helm 2>/dev/null || true
helm repo update
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install argocd argocd/argo-cd -n argocd -f clusters/argocd/helm-values.yaml
```

Wait for all pods:
```bash
kubectl wait --for=condition=Available deploy/argocd-server -n argocd --timeout=180s
kubectl wait --for=condition=Available deploy/argocd-repo-server -n argocd --timeout=180s
kubectl wait --for=condition=Available deploy/argocd-application-controller -n argocd --timeout=180s
```

Expected: all deployments Available.

- [ ] **Step 2: Apply the ArgoCD Applications**

```bash
kubectl apply -k clusters/argocd
```

Expected: `application.argoproj.io/tdgl-infra created`, `application.argoproj.io/tdgl-services created`

- [ ] **Step 3: Get admin password**

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d && echo
```

- [ ] **Step 4: Verify Applications are syncing**

```bash
kubectl get applications -n argocd
```

Expected: `tdgl-infra` and `tdgl-services` with status `Synced` and `Healthy`.

- [ ] **Step 5: Verify TDGL resources exist**

```bash
kubectl get pods -n tdgl
kubectl get pvc -n tdgl
```

Expected: data-viewer, postgres pods Running; PVCs Bound.

- [ ] **Step 6: Access UI**

```bash
# Port-forward in background
kubectl port-forward -n argocd svc/argocd-server 8080:80 --address 0.0.0.0 &
# Open http://localhost:8080 — login with admin / <password from step 3>
```

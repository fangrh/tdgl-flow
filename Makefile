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
	kubectl -n kube-system delete helmcharts.helm.cattle.io traefo-crd 2>/dev/null || true
	@echo "==> Traefik disabled. Restart k3s with --disable traefik for persistence."